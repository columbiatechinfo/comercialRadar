#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GOLDEN — trava de regressão DISTRIBUTIVA sobre base REAL.
================================================================================

Por que existe
--------------
A suíte sintética prova INVARIANTES: o gate aborta, o id não vira float, a
hipótese não ganha coordenada. Ela não vê o que muda de escala — uma
canonicalização que passa a fundir 3% a mais de endereços, uma heurística que
sobe a faixa ALTA de 60% para 85%, um léxico que promove ruído. Nada disso
quebra invariante; tudo isso muda a entrega.

O que esta suíte NÃO era
------------------------
Canoas e Santa Maria estavam declaradas como "golden tests" no SKILL.md e,
duas linhas abaixo, como "precisam de RE-BASELINE antes de voltarem a travar
regressão". Um golden que não trava nada é um comentário com nome de teste. A
honestidade mínima é: ou existe baseline, ou não se chama golden.

Como funciona
-------------
`gravar` extrai MÉTRICAS de um RCC já emitido e as congela num JSON com o
`run_fingerprint` da execução que as produziu. `comparar` refaz a extração e
confronta com tolerância declarada POR MÉTRICA — contagem estrutural é exata,
distribuição tem banda.

    python golden.py gravar  POA_RCC.csv golden/poa.json
    python golden.py comparar POA_RCC.csv golden/poa.json

Tolerância
----------
Zero para o que é estrutural (linhas, colunas, endereços distintos): mudou,
alguém precisa explicar. Banda relativa para o que é distributivo: a
heurística pode andar, mas não pode andar em silêncio.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

# métrica -> tolerância relativa (0.0 = exato)
TOLERANCIA = {
    'linhas': 0.0,
    'colunas': 0.0,
    'observadas': 0.0,
    'enderecos_distintos': 0.0,
    'inferidas': 0.02,
    'grupos_coletivos': 0.02,
    'unidades_em_coletiva': 0.02,
    'com_atividade': 0.03,
    'equivalencias_marcadas': 0.10,
}
# distribuições: comparadas categoria a categoria, com a mesma banda
DISTRIBUICOES = {
    'por_grau': 0.05,
    'por_classe': 0.05,
    'destino_campo': 0.05,
    'elegibilidade': 0.03,
    'polo_classe': 0.05,
}


def _dist(s):
    s = s.fillna('').astype(str).str.strip()
    return {k: int(v) for k, v in s[s.ne('')].value_counts().items()}


def metricas(caminho) -> dict:
    """Métricas do ARTEFATO — lidas do arquivo, nunca do dataframe em memória."""
    cols = pd.read_csv(caminho, sep=';', nrows=0, encoding='utf-8-sig').columns
    uso = [c for c in ('UND_NATUREZA', 'COLETIVA_ID', 'COL_FORMA', 'EVD_GRAU',
                       'EVD_CLASSE', 'ACT_DESTINO_CAMPO', 'ATV_PRESENTE',
                       'COLETIVA_CHAVE_HASH', 'CNF_ELEGIBILIDADE',
                       'COL_POLO_CLASSE', 'END_LOGR_EQUIV_GRAU') if c in cols]
    d = pd.read_csv(caminho, sep=';', dtype=str, encoding='utf-8-sig', usecols=uso)
    nat = d.get('UND_NATUREZA', pd.Series(dtype=str)).fillna('')
    forma = d.get('COL_FORMA', pd.Series(dtype=str)).fillna('')
    cid = d.get('COLETIVA_ID', pd.Series(dtype=str)).fillna('')
    h = d.get('COLETIVA_CHAVE_HASH', pd.Series(dtype=str)).fillna('')
    eq = d.get('END_LOGR_EQUIV_GRAU', pd.Series(dtype=str)).fillna('')
    m = {
        'linhas': int(len(d)),
        'colunas': int(len(cols)),
        'observadas': int((nat == 'OBSERVADO').sum()),
        'inferidas': int((nat == 'INFERIDO').sum()),
        'enderecos_distintos': int(h[h.str.strip().ne('')].nunique()),
        'grupos_coletivos': int(cid[forma.ne('') & forma.ne('INDEFINIDA')].nunique()),
        'unidades_em_coletiva': int((forma.ne('') & forma.ne('INDEFINIDA')).sum()),
        'com_atividade': int((d.get('ATV_PRESENTE', pd.Series(dtype=str))
                              .fillna('') == 'SIM').sum()),
        'equivalencias_marcadas': int(eq.str.strip().ne('').sum()),
    }
    if 'EVD_GRAU' in d:
        m['por_grau'] = _dist(d.loc[nat == 'INFERIDO', 'EVD_GRAU'])
    if 'EVD_CLASSE' in d:
        m['por_classe'] = _dist(d.loc[nat == 'INFERIDO', 'EVD_CLASSE'])
    for chave, col in (('destino_campo', 'ACT_DESTINO_CAMPO'),
                       ('elegibilidade', 'CNF_ELEGIBILIDADE'),
                       ('polo_classe', 'COL_POLO_CLASSE')):
        if col in d:
            m[chave] = _dist(d[col])
    return m


def gravar(artefato, destino) -> dict:
    m = metricas(artefato)
    selo = artefato + '.seal.json'
    if os.path.exists(selo):
        with open(selo, encoding='utf-8') as fh:
            s = json.load(fh)
        # a baseline SÓ vale se souber de qual execução veio
        m['_run_fingerprint'] = s.get('run_fingerprint', '')
        m['_pipeline_version'] = s.get('pipeline_version', '')
        m['_artifact_sha256'] = s.get('artifact_sha256', '')
    os.makedirs(os.path.dirname(os.path.abspath(destino)) or '.', exist_ok=True)
    with open(destino, 'w', encoding='utf-8') as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return m


def comparar(artefato, baseline) -> list:
    with open(baseline, encoding='utf-8') as fh:
        base = json.load(fh)
    novo = metricas(artefato)
    falhas = []
    for k, tol in TOLERANCIA.items():
        if k not in base or k not in novo:
            continue
        a, b = float(base[k]), float(novo[k])
        limite = abs(a) * tol
        if abs(b - a) > limite:
            falhas.append(f'{k}: baseline {base[k]} -> agora {novo[k]} '
                          f'(tolerancia {tol:.0%})')
    for k, tol in DISTRIBUICOES.items():
        if k not in base or k not in novo:
            continue
        da, db = base[k] or {}, novo[k] or {}
        for cat in sorted(set(da) | set(db)):
            a, b = float(da.get(cat, 0)), float(db.get(cat, 0))
            if abs(b - a) > max(abs(a) * tol, 1.0):
                falhas.append(f'{k}[{cat}]: {da.get(cat, 0)} -> {db.get(cat, 0)} '
                              f'(tolerancia {tol:.0%})')
    return falhas


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('gravar', 'comparar'):
        raise SystemExit(__doc__)
    acao, art, ref = sys.argv[1:4]
    if acao == 'gravar':
        m = gravar(art, ref)
        print(f'baseline gravada em {ref}')
        for k, v in sorted(m.items()):
            print(f'  {k} = {v}')
    else:
        f = comparar(art, ref)
        if f:
            print(f'GOLDEN DIVERGIU ({len(f)}):')
            for x in f:
                print('  ' + x)
            raise SystemExit(1)
        print('GOLDEN OK — nenhuma metrica fora da tolerancia declarada')
