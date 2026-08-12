#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fábrica de fixtures CNEFE — o que estava faltando para os testes valerem algo.
================================================================================

A auditoria de 2026-08 apontou o defeito de método: `tests/test_unit.py` fabricava
o estado

    NUM_ENDERECO = 10  E  PADRAO_ENDERECO = 'QUADRA_LOTE'

que `preparar_base()` NUNCA produz (Q/L só nasce onde NUM_ENDERECO == 0). O teste
passava, e o que ele afirmava proteger estava morto no fluxo canônico. Isso é pior
do que não ter teste: é teste emitindo confiança sobre um estado impossível.

A regra que este módulo impõe: **todo teste de comportamento parte do CSV bruto,
no layout oficial, e atravessa `preparar_base()`.** Se o estado não pode ser
escrito num CSV do IBGE, ele não pode ser objeto de teste de integração.

    from cnefe_fixture import csv_cnefe, base_preparada

    caminho = csv_cnefe([
        apto('RUA A', 100, '201'), apto('RUA A', 100, '303 SINDICO'),
    ])
    df, stats = base_preparada(caminho)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

COLS = ['COD_UNICO_ENDERECO', 'COD_UF', 'COD_MUNICIPIO', 'COD_SETOR', 'CEP',
        'NOM_TIPO_SEGLOGR', 'NOM_TITULO_SEGLOGR', 'NOM_SEGLOGR', 'NUM_ENDERECO',
        'DSC_LOCALIDADE', 'LATITUDE', 'LONGITUDE', 'NV_GEO_COORD', 'COD_ESPECIE',
        'COD_TIPO_ESPECIE', 'COD_INDICADOR_ESTAB_ENDERECO',
        'NOM_COMP_ELEM1', 'VAL_COMP_ELEM1', 'NOM_COMP_ELEM2', 'VAL_COMP_ELEM2',
        'NOM_COMP_ELEM3', 'VAL_COMP_ELEM3', 'NOM_COMP_ELEM4', 'VAL_COMP_ELEM4',
        'NOM_COMP_ELEM5', 'VAL_COMP_ELEM5', 'DSC_ESTABELECIMENTO']

MUN, SETOR, CEP = 4300604, '430060405000001', '92010000'
LAT, LON = -29.920, -51.180

_seq = [0]


def linha(logr='PRINCIPAL', num=100, tipo='RUA', titulo='', loc='CENTRO',
          esp=1, tesp=103, comps=(), estab='', ind='', nv=1,
          dlat=0.0, dlon=0.0, setor=SETOR, mun=MUN, cep=CEP):
    """Uma linha CNEFE. `comps` = [(NOM, VAL), ...] até 5 pares."""
    _seq[0] += 1
    r = {c: '' for c in COLS}
    r.update({
        'COD_UNICO_ENDERECO': str(10_000_000 + _seq[0]),
        'COD_UF': 43, 'COD_MUNICIPIO': mun, 'COD_SETOR': setor, 'CEP': cep,
        'NOM_TIPO_SEGLOGR': tipo, 'NOM_TITULO_SEGLOGR': titulo,
        'NOM_SEGLOGR': logr, 'NUM_ENDERECO': num, 'DSC_LOCALIDADE': loc,
        'LATITUDE': f'{LAT + dlat:.6f}', 'LONGITUDE': f'{LON + dlon:.6f}',
        'NV_GEO_COORD': nv, 'COD_ESPECIE': esp, 'COD_TIPO_ESPECIE': tesp,
        'COD_INDICADOR_ESTAB_ENDERECO': ind, 'DSC_ESTABELECIMENTO': estab,
    })
    for i, (nom, val) in enumerate(list(comps)[:5], 1):
        r[f'NOM_COMP_ELEM{i}'] = nom
        r[f'VAL_COMP_ELEM{i}'] = val
    return r


def apto(logr, num, valor, **kw):
    return linha(logr=logr, num=num, comps=[('APARTAMENTO', str(valor))], **kw)


def casa(logr, num, valor, **kw):
    return linha(logr=logr, num=num, comps=[('CASA', str(valor))], **kw)


def lote(logr, quadra, lt, **kw):
    """Quadra/Lote como o IBGE grava: SEM número predial (NUM_ENDERECO=0)."""
    return linha(logr=logr, num=0,
                 comps=[('QUADRA', str(quadra)), ('LOTE', str(lt))], **kw)


def estabelecimento(logr, num, nome, esp=6, **kw):
    return linha(logr=logr, num=num, esp=esp, tesp='', estab=nome, ind=1, **kw)


def csv_cnefe(linhas, destino=None, sep=';'):
    """Escreve o CSV no layout oficial. Devolve o caminho."""
    import csv as _csv
    destino = Path(destino or tempfile.mkstemp(suffix='.csv')[1])
    with open(destino, 'w', newline='', encoding='utf-8') as fh:
        w = _csv.DictWriter(fh, fieldnames=COLS, delimiter=sep)
        w.writeheader()
        w.writerows(linhas)
    return str(destino)


def base_preparada(caminho, **kw):
    """O caminho canônico, sempre. Nenhum teste monta dataframe à mão."""
    import radar_pipeline as rp
    return rp.preparar_base(caminho, **kw)


def grupo(df, logr='PRINCIPAL', num=100):
    """Subconjunto de um endereço, para inspeção nos testes."""
    return df[(df['NOM_SEGLOGR'].astype(str).str.upper().str.contains(logr.upper()))
              & (df['NUM_ENDERECO'] == num)]


def hipoteses(df):
    """Roda o motor de lacunas sobre a base preparada e devolve o frame."""
    import radar_utils as xu
    col = df[df['FLAG_COL'] == 1].copy()
    ql = df[(df['PADRAO_ENDERECO'] == 'QUADRA_LOTE')
            & (df['SANIDADE_GEO'] == 'OK')
            & (df['FLAG_ANOMALIA_COLETA'] == 0)].copy()
    _, gaps, _ = xu.construir_simples_e_faltantes(col, ql_records=ql)
    return gaps


# ── bloco SET offline ─────────────────────────────────────────────────────
# A suite nao pode depender de rede: em `--qa-mode strict` o bloco SET e'
# obrigatorio, e sem internet o download falhava, o gate corretamente
# quarentenava e o TESTE quebrava por motivo errado. O gate estava certo; o
# teste e' que precisava de fonte local determinista.
SETORES_FIXTURE = [
    # CD_SETOR, CD_MUN, NM_MUN, NM_BAIRRO, v0003, v0007, v0008, v0009
    ('430060405000001', '4300604', 'MUNICIPIO A', 'CENTRO', 120, 90, 10, 20),
    ('355030805000001', '3550308', 'MUNICIPIO B', 'SE', 300, 240, 20, 40),
]


def csv_agregado_setor(destino=None):
    """Escreve um agregado por setor no layout do IBGE, sem rede."""
    import csv as _csv
    import tempfile as _tf
    destino = destino or _tf.mkstemp(suffix='.csv')[1]
    cols = ['CD_SETOR', 'SITUACAO', 'CD_SIT', 'CD_TIPO', 'AREA_KM2', 'CD_REGIAO',
            'NM_REGIAO', 'CD_UF', 'NM_UF', 'CD_MUN', 'NM_MUN', 'CD_DIST',
            'NM_DIST', 'CD_SUBDIST', 'NM_SUBDIST', 'CD_BAIRRO', 'NM_BAIRRO',
            'v0001', 'v0002', 'v0003', 'v0004', 'v0005', 'v0006', 'v0007',
            'v0008', 'v0009']
    with open(destino, 'w', newline='', encoding='latin-1') as fh:
        w = _csv.DictWriter(fh, fieldnames=cols, delimiter=';')
        w.writeheader()
        for setor, mun, nm, bairro, v3, v7, v8, v9 in SETORES_FIXTURE:
            r = {c: '' for c in cols}
            r.update({'CD_SETOR': setor, 'CD_MUN': mun, 'NM_MUN': nm,
                      'NM_BAIRRO': bairro, 'v0003': v3, 'v0007': v7,
                      'v0008': v8, 'v0009': v9, 'v0002': v3, 'v0004': 0})
            w.writerow(r)
    return destino


def cache_set_offline(diretorio):
    """Popula o cache do bloco SET para que `baixar_basico` nao va a rede."""
    os.makedirs(diretorio, exist_ok=True)
    alvo = os.path.join(diretorio, 'Agregados_por_setores_basico_BR.csv')
    csv_agregado_setor(alvo)
    return alvo
