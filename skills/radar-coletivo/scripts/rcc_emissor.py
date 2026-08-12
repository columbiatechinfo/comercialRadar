#!/usr/bin/env python3
"""RCC — emissor canônico de coletividade a partir da base de endereços do IBGE.

Escopo: EXCLUSIVAMENTE o arquivo oficial de endereços e domicílios. Nenhuma
fonte externa é consultada, nada é presumido. Colunas que dependem do cadastro
da concessionária são EMITIDAS VAZIAS com o domínio declarado — o schema fica
completo e o consumidor sabe que a informação não existe, em vez de descobrir
que a coluna sumiu.

Grão: 1 linha = 1 UNIDADE por sistema de origem. As unidades OBSERVADAS são os
próprios registros do arquivo; as INFERIDAS vêm da análise de lacunas e carregam
coordenada nula por construção.

Consome `radar_pipeline.preparar_base` (núcleo único, X1). Nenhuma regra de
negócio é recalculada aqui — o emissor traduz, agrega e declara proveniência.

    python rcc_emissor.py --input <base.csv> --output <base_rcc.csv>
                          [--store rcc_store_coletivas.csv]
"""
import argparse
import csv
import hashlib
import json
import os
import sys

from pathlib import Path

import numpy as np
import pandas as pd

import ibge_setor_ocupacao as xs
import lexico_logradouro as lx
import qualidade_artefato as qa
import radar_pipeline as rp
import radar_utils as xu
from radar_utils import VERSAO

R_TERRA = 6_371_000.0

# ════════════════════════════════════════════════════════════════════════════
#  DE-PARA — tipologia da skill  ->  vocabulário reconciliado do RCC
#  A tabela é EXPLÍCITA e exaustiva: rótulo novo sem destino ABORTA o export,
#  em vez de cair silenciosamente em INDEFINIDA.
# ════════════════════════════════════════════════════════════════════════════
FORMA = {
    'COND_VERT_MULTIBLOCOS': 'VERTICAL_MULTIBLOCO',
    'COND_HORIZ_MULTIBLOCOS': 'HORIZONTAL_MULTIBLOCO',
    'COND_MULTIBLOCOS': 'VERTICAL_MULTIBLOCO',
    'COND_VIA_INT': 'HORIZONTAL',
    'EMPREEND_NOMEADO': 'VERTICAL',
    'VERT_APTO': 'VERTICAL',
    'VERT_COMPL': 'VERTICAL',
    'VERT_BLOCO': 'VERTICAL',
    'HORIZ_VILA_COND': 'HORIZONTAL',
    'HORIZ_MORADIAS': 'HORIZONTAL',
    'HORIZ_SUBDIV': 'SIMPLES',
    'MULT_ESTAB': 'COMERCIAL',
    'AGRUP_END': 'INDEFINIDA',
    'APTO_ISOLADO': 'VERTICAL',
    'AGRUP_TEXTO': 'INDEFINIDA',
    'DUPLA_END': 'SIMPLES',
    'NAO_COLETIVA': 'INDEFINIDA',
}

# tipo da unidade -> sigla de 3 letras do UNIDADE_ID (estável e legível)
SIGLA = {'APARTAMENTO': 'APT', 'CASA': 'CAS', 'SALA': 'SAL', 'LOJA': 'LOJ',
         'BOX': 'BOX', 'LOTE': 'LOT', 'SOBRADO': 'SOB', 'QUITINETE': 'QUI',
         'QUARTO': 'QRT', 'GALPAO': 'GAL', 'CONJUNTO': 'CJT'}

# piso de incerteza por nível de precisão da coordenada oficial, em metros
PISO_NV = {1: 5.0, 2: 8.0, 3: 15.0, 4: 40.0, 5: 60.0, 6: 250.0}

SETORES_ATIV = {  # setores que caracterizam ATIVIDADE ECONÔMICA
    'ALIMENTACAO', 'COMERCIO_VAREJO', 'COMERCIO_SERVICO', 'SERVICO_PROF',
    'SERVICO_AUTO', 'BELEZA_ESTETICA', 'SAUDE', 'EDUCACAO', 'HOSPEDAGEM',
    'LAZER', 'ASSOCIACAO_ONG', 'INDUSTRIAL', 'MANUFATURA', 'RELIGIOSO',
    'AGROPECUARIO', 'DIVERSOS',
    # v4.8: estabelecimento de espécie oficial cujo nome não casou com nenhuma
    # regra do léxico. A ATIVIDADE é fato — o recenseador registrou o
    # estabelecimento; o SETOR é que é desconhecido. Antes isso virava
    # COMERCIO_SERVICO e fabricava falso positivo comercial; agora fica
    # afirmado como atividade e em aberto como setor.
    'NAO_CLASSIFICADO'}

ESPECIE_ESTAB = {3, 4, 5, 6, 8}

# indicador oficial de multiplicidade -> CÓDIGO do RCC.
# Mapeado do CÓDIGO NUMÉRICO, não do rótulo de exibição: rótulo muda com a
# redação da skill, o código do layout não muda.
FAIXA_DECL = {1: 'UNICO', 2: 'MULTIPLO_ATE_10', 3: 'MULTIPLO_11_MAIS',
              4: 'MULTIPLO_QTD_DESCONHECIDA'}


class RccError(RuntimeError):
    """Violação de contrato do RCC — aborta sem emitir dado."""


def _exigir(cond, msg):
    if not cond:
        raise RccError(msg)


# ════════════════════════════════════════════════════════════════════════════
#  IDENTIFICADORES
# ════════════════════════════════════════════════════════════════════════════
def _slug(v, n=4):
    """Valor da unidade em forma ordenável: zero-padded quando numérico."""
    s = str(v or '').strip().upper()
    if not s:
        return '0000'
    return s.zfill(n) if s.isdigit() else ''.join(
        c for c in s if c.isalnum())[:6].ljust(4, '0')


def coletiva_ids(df, store_path):
    """COLETIVA_ID legível e ESTÁVEL entre execuções.

    O sequencial precisa de memória: sem store, a mesma coletiva ganharia número
    diferente a cada run e o id deixaria de servir para rastrear safra a safra.
    O store é append-only e chaveado pelo hash canônico — que é reproduzível sem
    ele, o que mantém a base auditável mesmo se o store se perder.
    """
    store = {}
    if store_path and os.path.exists(store_path):
        with open(store_path, encoding='utf-8') as fh:
            for r in csv.DictReader(fh, delimiter=';'):
                store[r['COLETIVA_CHAVE_HASH']] = r['COLETIVA_ID']

    pares = (df.loc[df['RADAR_ID_ENDERECO'].notna(),
                    ['COD_MUNICIPIO', 'RADAR_ID_ENDERECO']]
             .astype({'RADAR_ID_ENDERECO': str}).drop_duplicates()
             .sort_values(['COD_MUNICIPIO', 'RADAR_ID_ENDERECO']))
    # Sequência por município, vetorizada: iterrows sobre 179 mil endereços
    # custava 7 s. O que precisa ser sequencial é o CONTADOR, não o laço.
    ultimo = {}
    for v in store.values():
        m = v.split('-')[1]
        n = int(v.rsplit('-', 1)[1])
        if n > ultimo.get(m, 0):
            ultimo[m] = n

    pares = pares.copy()
    pares['_H'] = pares['RADAR_ID_ENDERECO'].astype(str)
    pares['_M'] = pares['COD_MUNICIPIO'].map(lambda m: f'{int(m):07d}')
    faltam = pares[~pares['_H'].isin(store.keys())]
    novos = []
    if len(faltam):
        base = faltam['_M'].map(lambda m: ultimo.get(m, 0))
        seq = faltam.groupby('_M').cumcount() + 1 + base
        cid = 'COL-' + faltam['_M'] + '-' + seq.map(lambda x: f'{x:06d}')
        store.update(dict(zip(faltam['_H'], cid)))
        novos = [{'COLETIVA_CHAVE_HASH': h, 'COLETIVA_ID': c}
                 for h, c in zip(faltam['_H'], cid)]

    if store_path and novos:
        existe = os.path.exists(store_path)
        with open(store_path, 'a', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=['COLETIVA_CHAVE_HASH', 'COLETIVA_ID'],
                               delimiter=';')
            if not existe:
                w.writeheader()
            w.writerows(novos)
    return store


# ════════════════════════════════════════════════════════════════════════════
#  GEOMETRIA
# ════════════════════════════════════════════════════════════════════════════
def _haversine_m(lat, lon, lat0, lon0):
    p1, p2 = np.radians(lat), np.radians(lat0)
    dp, dl = p2 - p1, np.radians(lon0) - np.radians(lon)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_TERRA * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def raio_do_grupo(df):
    """Raio máximo ao centróide por CHAVE, em metros.

    Um endereço é um lugar: grupo espalhado por centenas de metros denuncia que
    a chave uniu o que não coabita. Métrica em metros por haversine, nunca fator
    linear global.
    """
    m = (df['NUMERO'] > 0) & (df['SANIDADE_GEO'] == 'OK') & \
        df['LATITUDE'].notna() & df['LONGITUDE'].notna()
    out = pd.Series(np.nan, index=df.index)
    if not m.any():
        return out
    sub = df.loc[m]
    cen = sub.groupby('CHAVE')[['LATITUDE', 'LONGITUDE']].transform('mean')
    d = _haversine_m(sub['LATITUDE'].to_numpy(), sub['LONGITUDE'].to_numpy(),
                     cen['LATITUDE'].to_numpy(), cen['LONGITUDE'].to_numpy())
    dd = pd.Series(d, index=sub.index)
    out.loc[m] = dd.groupby(sub['CHAVE']).transform('max')
    return out


# ════════════════════════════════════════════════════════════════════════════
#  MONTAGEM
# ════════════════════════════════════════════════════════════════════════════
def emitir(df, store_path=None, exec_id=None, fonte='IBGE', safra='',
           hash_entrada=''):
    d = df.copy()

    # ── proveniência do agrupamento ────────────────────────────────────────
    store = coletiva_ids(d, store_path)
    d['_HASH'] = d['RADAR_ID_ENDERECO'].astype('string')
    d['COLETIVA_ID'] = d['_HASH'].map(store)

    # ── tipologia reconciliada (DE-PARA exaustivo) ─────────────────────────
    desconhecidos = sorted(set(d['TIPO_COLETIVA'].dropna()) - set(FORMA))
    _exigir(not desconhecidos,
            f'DE-PARA incompleto: tipologia sem destino no RCC {desconhecidos}')

    # ── agregados por grupo (o que a listagem consome) ─────────────────────
    d['_EST'] = pd.to_numeric(d['COD_ESPECIE'], errors='coerce').isin(ESPECIE_ESTAB)
    d['_RES'] = pd.to_numeric(d['COD_ESPECIE'], errors='coerce').isin([1, 2])
    g = d.groupby('CHAVE')
    d['_N_RES'] = g['_RES'].transform('sum')
    d['_N_EST'] = g['_EST'].transform('sum')
    d['COL_FLAG_USO_MISTO'] = ((d['_N_RES'] > 0) & (d['_N_EST'] > 0)).astype(int)
    d['COL_USO'] = np.select(
        [(d['_N_RES'] > 0) & (d['_N_EST'] > 0), d['_N_EST'] > 0, d['_N_RES'] > 0],
        ['MISTO', 'COMERCIAL', 'RESIDENCIAL'], default='INDEFINIDO')

    # ── atividade econômica: só a partir de campos do layout oficial ───────
    nome = d['DSC_ESTABELECIMENTO'].fillna('').astype(str).str.strip()
    tem_nome = nome.ne('') & ~nome.str.upper().isin(xu.ESTAB_GENERICOS)
    setor_ativ = d['SETOR_ATIVIDADE'].isin(SETORES_ATIV)
    d['ATV_PRESENTE'] = np.select(
        [tem_nome | (d['_EST'] & setor_ativ), d['_RES'] & ~d['_EST']],
        ['SIM', 'NAO'], default='INDETERMINADO')
    d['ATV_EVIDENCIA_CAMPO'] = np.select(
        [tem_nome & d['_EST'], tem_nome, d['_EST']],
        ['MULTIPLOS_CAMPOS', 'DSC_ESTABELECIMENTO', 'COD_ESPECIE'],
        default='NENHUM')
    # grão: só resolve até a unidade quando o registro traz complemento
    tem_compl = d['COMPLEMENTO_NORM'].fillna('').astype(str).str.strip().ne('')
    d['ATV_GRAO_EVIDENCIA'] = np.where(d['ATV_PRESENTE'] == 'SIM',
                                       np.where(tem_compl, 'UNIDADE', 'ENDERECO'), '')
    nv = pd.to_numeric(d['NV_GEO_COORD'], errors='coerce')
    piso = nv.map(PISO_NV).fillna(PISO_NV[6])
    d['ATV_INCERTEZA_M'] = np.where(d['ATV_PRESENTE'] == 'SIM', piso, np.nan)
    ind = pd.to_numeric(d['COD_INDICADOR_ESTAB_ENDERECO'], errors='coerce')
    d['ATV_ROTA_TRATAMENTO'] = np.select(
        [ind == 1, ind.isin([2, 3, 4]), d['_EST'], d['_RES']],
        ['RECLASSIFICACAO_1_1', 'INDIVIDUALIZACAO_MULTI', 'VERIFICAR_CAMPO',
         'SEM_ROTA'], default='SEM_ROTA')
    d['ATV_QTD_ESTABELECIMENTOS'] = d['_N_EST'].astype(int)
    d['COL_QTD_UNID_ATIVIDADE'] = (
        d.assign(_a=(d['ATV_PRESENTE'] == 'SIM').astype(int))
        .groupby('CHAVE')['_a'].transform('sum'))

    # ── critério de unificação (4 valores reais) ───────────────────────────
    tem_ql = d['CHAVE_QUADRA_LOTE'].fillna('').astype(str).str.contains('/L')
    d['COL_CRITERIO_UNIFICACAO'] = np.select(
        [(d['NUMERO'] > 0) & tem_ql, d['NUMERO'] > 0, tem_ql],
        ['NUMERO_QUADRA_LOTE', 'NUMERO', 'QUADRA_LOTE'], default='PARCIAL')

    # ── veredito em 5 níveis ───────────────────────────────────────────────
    sem_coord = ~d['QUALIDADE_COORD'].isin(['VALIDADA'])
    d['COL_VEREDITO'] = np.select(
        [(d['CONFIANCA'] == 'ALTA') & ~sem_coord & (d['QTD_END'] >= 3),
         (d['CONFIANCA'] == 'ALTA'),
         (d['CONFIANCA'] == 'MEDIA'),
         (d['CONFIANCA'] == 'BAIXA')],
        ['CONCLUSIVO', 'FORTE', 'MODERADO', 'FRAGIL'], default='INSUFICIENTE')

    # ── geometria: par original/em uso. A skill NÃO move coordenada ────────
    d['GEO_RAIO_GRUPO_M'] = raio_do_grupo(d)
    d['GEO_ACURACIA_M'] = piso

    # ── quadra e lote em colunas próprias ──────────────────────────────────
    ql = d['CHAVE_QUADRA_LOTE'].fillna('').astype(str).map(xu._parse_chave_ql)
    d['END_QUADRA'] = [r['quadra'] for r in ql]
    d['END_LOTE'] = [r['lote'] for r in ql]

    # ── logradouro canônico: tipo + título + nome harmonizados ─────────────
    def _j(*ss):
        return ' '.join(x for x in ss if x).strip()
    tipo = d['NOM_TIPO_SEGLOGR'].fillna('').astype(str).str.strip()
    tit_h = (d['NOM_TITULO_HARM'] if 'NOM_TITULO_HARM' in d.columns
             else d['NOM_TITULO_SEGLOGR']).fillna('').astype(str).str.strip()
    tit_o = d['NOM_TITULO_SEGLOGR'].fillna('').astype(str).str.strip()
    nom_h = d['NOM_SEGLOGR_HARM'].fillna('').astype(str).str.strip()
    nom_o = d['NOM_SEGLOGR'].fillna('').astype(str).str.strip()
    # O CONTRATO deste campo diz "numeral expandido". Quando a canônica passou
    # a expandir e o campo tratado não, os dois passaram a discordar na mesma
    # linha — 10.147 casos em Porto Alegre, apanhados pelo auditor ANTES da
    # publicação. A grafia original fica inteira em END_LOGRADOURO_ORIG.
    d['END_LOGRADOURO'] = [
        xu.expandir_numerais(xu.strip_accents(_j(a, b, c)).upper())
        for a, b, c in zip(tipo, tit_h, nom_h)]
    d['END_LOGRADOURO'] = (pd.Series(d['END_LOGRADOURO'], index=d.index)
                           .str.replace(r'\s+', ' ', regex=True).str.strip())
    d['END_LOGRADOURO_ORIG'] = [_j(a, b, c) for a, b, c in zip(tipo, tit_o, nom_o)]
    # RESOLUÇÃO NO GRÃO DO GRUPO. O título é harmonizado por (CEP, tipo, nome);
    # quando o mesmo endereço aparece com CEPs diferentes, a moda não alcança e
    # 'AVENIDA DOUTOR X 363' e 'AVENIDA X 363' — que já compartilham o mesmo
    # COLETIVA_ID, porque a canônica não leva título — saem com END_LOGRADOURO
    # divergente DENTRO do grupo. Um id de endereço com dois nomes de rua não é
    # um endereço. A grafia de cada registro continua inteira em
    # END_LOGRADOURO_ORIG; aqui vale a do grupo, decidida por contagem e
    # desempatada por ordem alfabética (determinismo, não sorteio).
    if 'COLETIVA_ID' in d.columns:
        _cid = d['COLETIVA_ID'].fillna('').astype(str)
        _m = _cid.ne('')
        if _m.any():
            _cont = (pd.DataFrame({'_c': _cid[_m], '_l': d.loc[_m, 'END_LOGRADOURO']})
                     .value_counts().reset_index(name='_n')
                     .sort_values(['_c', '_n', '_l'], ascending=[True, False, True])
                     .drop_duplicates('_c').set_index('_c')['_l'])
            d.loc[_m, 'END_LOGRADOURO'] = _cid[_m].map(_cont).fillna(
                d.loc[_m, 'END_LOGRADOURO'])

    # ── tipo/valor da unidade ──────────────────────────────────────────────
    # `.replace('', np.nan)` sobre coluna de texto emite FutureWarning de
    # downcast no pandas 2.2.x (silencioso no 3.x): a doutrina afirmava suite
    # limpa sob -W error::FutureWarning e isso so' era verdade no runtime do
    # lock. `.mask(cond)` faz a mesma coisa sem depender da versao.
    und_tipo = d['UNIDADE_TIPO'].fillna('').mask(lambda s: s.eq(''))
    und_tipo = und_tipo.fillna(d['MORADIA_TIPO']).fillna('')
    und_val = d['UNIDADE_VALOR'].fillna('').mask(lambda s: s.eq(''))
    und_val = und_val.fillna(d['MORADIA_VALOR']).fillna('')
    d['UND_TIPO'] = und_tipo.replace('', 'OUTRO')
    d['UND_VALOR'] = und_val.astype(str)
    pos = d['POSICAO_TIPO'].fillna('').mask(lambda s: s.eq(''))
    d['UND_POSICAO'] = pos.fillna(d['PAVIMENTO_TIPO']).fillna('')

    # ── UNIDADE_ID composto e legível ──────────────────────────────────────
    bl = d['BLOCO_VALOR'].fillna('').astype(str).str.strip().str.upper()
    cid_s = d['COLETIVA_ID'].fillna('').astype(str)
    base_id = np.where(bl.ne('') & cid_s.ne(''), cid_s + '-B' + bl, cid_s)
    sig = d['UND_TIPO'].map(SIGLA).fillna('UND')
    # Registro SEM complemento não tem discriminante de unidade. Usar só o tipo
    # produziria UNIDADE_ID repetido — e o grão da tabela deixaria de ser
    # unidade. O discriminante passa a ser o próprio registro de origem: a
    # unidade existe (é uma economia observada), ela é que não tem nome.
    reg = d['COD_UNICO_ENDERECO'].astype(str)
    mun = d['COD_MUNICIPIO'].map(lambda m: f'{int(m):07d}')
    # Registro SEM identidade de endereço (número ausente) não tem COLETIVA_ID —
    # e mesmo assim é uma unidade observada, que não pode sumir da base. O id
    # nasce do município + registro, e o prefixo SEM- diz o que ele NÃO é.
    raiz = [b if b else f'SEM-{m}' for b, m in zip(base_id, mun)]
    uid = [f'{b}-{s}-{_slug(v)}' if str(v).strip() else f'{b}-REG-{r}'
           for b, s, v, r in zip(raiz, sig, d['UND_VALOR'], reg)]
    # Desambiguação: dois registros distintos podem declarar a MESMA unidade
    # (mesmo apto no mesmo bloco). Sem sufixo, o grão colapsaria e uma economia
    # desapareceria. O sufixo é o registro de origem — determinístico e rastreável.
    ser = pd.Series(uid, index=d.index)
    dupmask = ser.duplicated(keep=False)
    if dupmask.any():
        # O desambiguador NAO pode ser o COD_UNICO_ENDERECO: na base real de
        # Porto Alegre ele NAO e' unico (762.239 linhas, 757.512 valores) — o
        # "codigo unico de endereco" do arquivo oficial repete. Usar os ultimos
        # digitos era pior ainda: colidia em 218 mil casos.
        # Ordinal deterministico dentro do grupo duplicado, na ordem canonica.
        ordem = pd.DataFrame({'_u': ser, '_r': reg}).sort_values(
            ['_u', '_r'], kind='stable')
        k = ordem.groupby('_u', sort=False).cumcount() + 1
        suf = k.reindex(d.index).map(lambda x: f'-N{int(x):02d}')
        ser.loc[dupmask] = ser.loc[dupmask] + suf.loc[dupmask]
    d['UNIDADE_ID'] = ser
    d['BLOCO_ID'] = np.where(bl.ne('') & pd.Series(base_id, index=d.index).ne(''),
                             base_id, '')

    return d


def montar_saida(d, exec_id, fonte, safra, hash_entrada, colunas,
                 lex_ativos=None):
    """Projeta o dataframe tratado no schema canônico do RCC."""
    n = len(d)
    vazio = pd.Series([''] * n, index=d.index)
    _mapa_eq = equivalencias_logradouro(d, lex_ativos)
    _eq_grandes = _mapa_eq.pop('__grupos_acima_do_teto__', 0)
    # o corte viaja com o dataframe: quem publica precisa DECLARAR o que ficou
    # de fora, e o número não pode depender de recalcular a fase toda

    def _eq(dd, pos):
        if not _mapa_eq or 'RADAR_CHAVE_CANONICA' not in dd.columns:
            return vazio
        return (dd['RADAR_CHAVE_CANONICA'].astype(object)
                .map(lambda v: _mapa_eq.get(v, ('', '', ''))[pos]
                     if isinstance(_mapa_eq.get(v), tuple) else '')
                .fillna('').astype(str))
    M = {
        'IMOVEL_ID': vazio,                     # resolução de entidade: outra etapa
        'COLETIVA_ID': d['COLETIVA_ID'].fillna(''),
        'COLETIVA_CHAVE_HASH': d['RADAR_ID_ENDERECO'].astype('string').fillna(''),
        # A PRÉ-IMAGEM viaja com o dado. Sem ela o hash não é reproduzível por
        # terceiros: a canônica usa o nome HARMONIZADO e não leva o título, e
        # `END_LOGRADOURO` leva — 19% dos endereços de POA eram irreproduzíveis
        # e o auditor acusava 53.394 ids "que não derivam do endereço". Não
        # derivavam mesmo: faltava publicar de onde derivam.
        'COLETIVA_CHAVE_CANONICA': (d['RADAR_CHAVE_CANONICA'].astype('string')
                                    .fillna('') if 'RADAR_CHAVE_CANONICA'
                                    in d.columns else vazio),
        'END_LOGR_EQUIV_SUGERIDA': _eq(d, 0),
        'END_LOGR_EQUIV_GRAU': _eq(d, 1),
        'END_LOGR_EQUIV_EVIDENCIA': _eq(d, 2),
        'BLOCO_ID': d['BLOCO_ID'],
        'UNIDADE_ID': d['UNIDADE_ID'],
        'ORIGEM_SISTEMA': 'IBGE',
        'ORIGEM_REGISTRO_ID': d['COD_UNICO_ENDERECO'].astype(str),
        'ORIGEM_GRUPO_ID': d['RADAR_ID_BLOCO'].astype('string').fillna(''),
        'ORIGEM_LIGACAO': vazio,                # cadastro
        'EXEC_ID': exec_id,
        'END_MUNICIPIO': d['COD_MUNICIPIO'].astype(str),
        'END_LOGRADOURO': d['END_LOGRADOURO'],
        'END_LOGRADOURO_ORIG': d['END_LOGRADOURO_ORIG'],
        'END_NUMERO': d['NUMERO'],
        'END_NUMERO_ORIG': d['NUM_ENDERECO'].astype(str),
        'END_QUADRA': d['END_QUADRA'],
        'END_LOTE': d['END_LOTE'],
        'END_COMPLEMENTO': d['COMPLEMENTO_NORM'].fillna(''),
        'END_COMPLEMENTO_ORIG': d[[f'NOM_COMP_ELEM{i}' for i in range(1, 6)] +
                                  [f'VAL_COMP_ELEM{i}' for i in range(1, 6)]]
                                 .fillna('').astype(str)
                                 .apply(lambda r: ' '.join(x for x in r if x.strip()),
                                        axis=1),
        'END_LOCALIDADE': d['DSC_LOCALIDADE'].fillna(''),
        'END_CEP': d['CEP'].astype(str),
        'GEO_LAT_ORIG': d['LATITUDE'],
        'GEO_LON_ORIG': d['LONGITUDE'],
        'GEO_LAT': d['LATITUDE'],
        'GEO_LON': d['LONGITUDE'],
        'GEO_AJUSTE_FONTE': 'SEM_AJUSTE',       # esta skill não move coordenada
        'GEO_DESLOCAMENTO_M': 0.0,
        'GEO_ACURACIA_M': d['GEO_ACURACIA_M'],
        'GEO_NIVEL': d['NV_GEO_COORD'],
        'GEO_QUALIDADE': d['QUALIDADE_COORD'].fillna('AUSENTE'),
        'GEO_RAIO_GRUPO_M': d['GEO_RAIO_GRUPO_M'],
        'COL_FORMA': d['TIPO_COLETIVA'].map(FORMA).fillna('INDEFINIDA'),
        'COL_FORMA_ORIG': d['TIPO_COLETIVA'].fillna(''),
        'COL_USO': d['COL_USO'],
        'COL_CLASSE': vazio,                    # preenchido no pós-agregado
        'COL_CRITERIO_UNIFICACAO': d['COL_CRITERIO_UNIFICACAO'],
        'COL_VEREDITO': d['COL_VEREDITO'],
        'COL_QTD_OBSERVADA': d['QTD_END'],
        'COL_QTD_INFERIDA': 0,
        'COL_QTD_BLOCOS': d['N_BLOCOS'],
        'COL_QTD_UNID_ATIVIDADE': d['COL_QTD_UNID_ATIVIDADE'],
        'COL_FLAG_USO_MISTO': d['COL_FLAG_USO_MISTO'],
        'COL_POLO_CLASSE': vazio,               # preenchido pelo DBSCAN
        'UND_NATUREZA': 'OBSERVADO',
        'UND_BLOCO': d['BLOCO_VALOR'].fillna(''),
        'UND_TIPO': d['UND_TIPO'],
        'UND_VALOR': d['UND_VALOR'],
        'UND_POSICAO': d['UND_POSICAO'],
        'UND_ECONOMIAS': 1,
        'ATV_PRESENTE': d['ATV_PRESENTE'],
        'ATV_NOME': d['DSC_ESTABELECIMENTO'].fillna('').astype(str)
                     .map(lambda s: xu.strip_accents(s).upper().strip()),
        'ATV_NOME_ORIG': d['DSC_ESTABELECIMENTO'].fillna(''),
        'ATV_SETOR': d['SETOR_ATIVIDADE'].where(d['ATV_PRESENTE'] == 'SIM', ''),
        'ATV_DETALHE': d['ATIVIDADE_DETALHE'].fillna(''),
        'ATV_ESPECIE_COD': d['COD_ESPECIE'],
        'ATV_QTD_ESTABELECIMENTOS': d['ATV_QTD_ESTABELECIMENTOS'],
        'ATV_DECLARADA_FAIXA': pd.to_numeric(
            d['COD_INDICADOR_ESTAB_ENDERECO'], errors='coerce')
            .map(FAIXA_DECL).fillna('SEM_DECLARACAO'),
        'ATV_VALIDACAO_DECLARADA': d['VALIDACAO_ESTAB_DECL'].fillna('SEM_DECLARACAO'),
        'ATV_EVIDENCIA_CAMPO': d['ATV_EVIDENCIA_CAMPO'],
        'ATV_GRAO_EVIDENCIA': d['ATV_GRAO_EVIDENCIA'],
        'ATV_INCERTEZA_M': d['ATV_INCERTEZA_M'],
        'ATV_ROTA_TRATAMENTO': d['ATV_ROTA_TRATAMENTO'],
        'ATV_FLAG_FACHADA_ATIVA': vazio,        # preenchido no pós-agregado
        'ATV_FLAG_GAP_TARIFARIO': 0,            # exige cadastro: nunca 1 aqui
        'EVD_REGRA': d['TIPO_COLETIVA'].fillna('NAO_COLETIVA').map(
            lambda t: f'T3.classificar_coletiva[{t or "NAO_COLETIVA"}]'),
        # Evidencia nunca fica muda: registro individual TEM uma razao de nao
        # ser coletiva, e essa razao e' parte da auditoria.
        'EVD_DESCRICAO': d['EVIDENCIA'].fillna('').astype(str).where(
            d['EVIDENCIA'].fillna('').astype(str).str.strip().ne(''),
            'registro individual: sem marcador de coletividade no complemento'),
        'EVD_PRESENTES': vazio,
        # graduação só existe para hipótese; linha observada é fato, não grau
        'EVD_GRAU': vazio, 'EVD_CLASSE': vazio,
        'EVD_DISTANCIA': pd.array([pd.NA] * n, dtype='Int64'),
        # bloco SET — contexto territorial do IBGE, preenchido em enriquecer()
        'CNF_ELEGIBILIDADE': (d['CNF_ELEGIBILIDADE'].fillna('')
                              if 'CNF_ELEGIBILIDADE' in d.columns
                              else pd.Series('ELEGIVEL', index=d.index)),
        'SET_COD': (xs.chave_setor(d['COD_SETOR']) if 'COD_SETOR' in d.columns else vazio),
        'SET_DOM_PARTICULARES': d.get('SET_DOM_PARTICULARES', vazio),
        'SET_DOM_OCUPADOS': d.get('SET_DOM_OCUPADOS', vazio),
        'SET_DOM_USO_OCASIONAL': d.get('SET_DOM_USO_OCASIONAL', vazio),
        'SET_DOM_VAGOS': d.get('SET_DOM_VAGOS', vazio),
        'SET_DOM_SEM_OCUPACAO_HABITUAL': d.get('SET_DOM_SEM_OCUPACAO_HABITUAL', vazio),
        'SET_TX_SEM_OCUPACAO_HABITUAL': d.get('SET_TX_SEM_OCUPACAO_HABITUAL', vazio),
        'SET_CLASSE_SEM_OCUPACAO_HABITUAL': d.get('SET_CLASSE_SEM_OCUPACAO_HABITUAL', vazio),
        'CNF_COORD': d['RADAR_CONF_COORD'],
        'CNF_ENDERECO': d['RADAR_CONF_ENDERECO'],
        'CNF_CONTEXTO': d['RADAR_CONF_CONTEXTO'],
        'CNF_LOCALIZACAO': d['RADAR_CONF_LOCALIZACAO'],
        'CNF_FAIXA': d['RADAR_CONF_FAIXA'],
        'CNF_TIPOLOGIA': d['CONFIANCA'].where(d['CONFIANCA'] != 'NAO_COLETIVA', ''),
        'CNF_INFERENCIA': vazio,
        'CAD_STATUS': 'SEM_CADASTRO',
        'CAD_SITUACAO': vazio, 'CAD_PERFIL_TARIFARIO': vazio,
        'CAD_QTD_LIGACOES': vazio, 'CAD_ECONOMIAS': vazio,
        'CAD_DELTA_ECONOMIAS': vazio,
        'ACT_RECOMENDACAO': vazio,              # pós-agregado
        'ACT_REQUER_CONFIRMACAO': 'NAO',
        'ACT_PRIORIDADE': vazio,                # depende do delta -> cadastro
        'ACT_DESTINO_CAMPO': 'ENVIAR',          # observado é fato: pode ir a campo
        'AUD_FONTE': fonte, 'AUD_SAFRA': safra,
        'AUD_EXECUTADO_EM': '', 'AUD_HASH_ENTRADA': hash_entrada,  # preenchido no main
    }
    faltam = [c for c in colunas if c not in M]
    _exigir(not faltam, f'schema incompleto: {faltam}')
    _saida = pd.DataFrame({c: M[c] for c in colunas}, index=d.index)
    _saida.attrs['equiv_grupos_acima_do_teto'] = int(_eq_grandes)
    return _saida


# ════════════════════════════════════════════════════════════════════════════
#  PÓS-AGREGADOS (grão do grupo, escritos de volta na linha da unidade)
# ════════════════════════════════════════════════════════════════════════════
def pos_agregados(out, d, sem_polos=False):
    """Classe de porte, fachada ativa, polo e recomendação.

    Vivem no grupo, mas precisam estar NA LINHA: no grão UNIDADE, filtrar por
    'coletivas grandes' não pode exigir join com outra aba.
    """
    # Fachada ativa: térreo + não-residencial, pelo complemento normalizado.
    #
    # O `''` que estava nesta lista transformava AUSÊNCIA DE INFORMAÇÃO em
    # EVIDÊNCIA POSITIVA: estabelecimento sem posição declarada saía com
    # fachada ativa = 1. Na fixture eram 27 de 27. Ausência não é prova.
    #
    # A afirmação agora exige evidência explícita — posição térrea declarada,
    # ou tipo de unidade que só existe no térreo (LOJA, BOX). Sem isso a
    # resposta é 0, que aqui significa "não afirmado", não "negado": a
    # distinção entre NÃO e INDETERMINADO vive em ATV_PRESENTE, que já tem
    # os três estados.
    pos = out['UND_POSICAO'].fillna('').astype(str).str.upper().str.strip()
    terreo = (pos.isin(['TERREO', 'LOJA', 'SOBRELOJA'])
              | out['UND_TIPO'].isin(['LOJA', 'BOX']))
    out['ATV_FLAG_FACHADA_ATIVA'] = (
        (out['ATV_PRESENTE'] == 'SIM') & terreo).astype(int)

    # ── COERÊNCIA DE GRUPO ────────────────────────────────────────────────
    # COL_* descreve o GRUPO. A classificação da skill é por REGISTRO, então
    # sem isto o mesmo COLETIVA_ID sairia VERTICAL numa linha e SIMPLES na
    # outra — e qualquer filtro por forma devolveria meia coletiva.
    # PESOS DISTINTOS, sempre. COMERCIAL e LOTEAMENTO tinham ambos peso 2 e a
    # inversa {peso: rotulo} nao era injetiva: TODO COMERCIAL virava LOTEAMENTO.
    # Passou por todos os gates porque LOTEAMENTO e' valor valido do dominio —
    # corrupcao silenciosa de VALOR, que nenhum gate de forma pega. Achado na
    # base real de Porto Alegre (2.817 linhas), invisivel na fixture sintetica.
    FORCA_FORMA = {'VERTICAL_MULTIBLOCO': 7, 'HORIZONTAL_MULTIBLOCO': 6,
                   'VERTICAL': 5, 'HORIZONTAL': 4, 'LOTEAMENTO': 3,
                   'COMERCIAL': 2, 'SIMPLES': 1, 'INDEFINIDA': 0}
    FORCA_VER = {'CONCLUSIVO': 4, 'FORTE': 3, 'MODERADO': 2, 'FRAGIL': 1,
                 'INSUFICIENTE': 0}
    FORCA_CRIT = {'NUMERO_QUADRA_LOTE': 3, 'NUMERO': 2, 'QUADRA_LOTE': 1,
                  'PARCIAL': 0}
    com_id = out['COLETIVA_ID'].fillna('').astype(str).str.strip().ne('')
    gid = out['COLETIVA_ID'].where(com_id)
    for col, forca in (('COL_FORMA', FORCA_FORMA), ('COL_VEREDITO', FORCA_VER),
                       ('COL_CRITERIO_UNIFICACAO', FORCA_CRIT)):
        # a inversa so' existe se os pesos forem distintos
        _exigir(len(set(forca.values())) == len(forca),
                f'peso repetido em {col}: a inversa peso->rotulo trocaria valores')
        # a forma do GRUPO é a mais forte observada entre seus membros:
        # um prédio com um registro sem complemento não deixa de ser prédio.
        # Vetorizado: map + transform('max') são C. A versão com lambda por
        # grupo custava 27 s em 30 mil linhas — uma chamada Python por grupo.
        r = out[col].map(forca).fillna(-1)
        mx = r.groupby(gid).transform('max')
        inv = {v: k for k, v in forca.items()}
        novo = mx.map(inv)
        out[col] = np.where(com_id & novo.notna(), novo, out[col])
    # COL_USO, COL_QTD_BLOCOS e COL_FLAG_USO_MISTO já são calculados por CHAVE,
    # e CHAVE mapeia 1:1 em COLETIVA_ID — então já são constantes no grupo.
    # 'first' é só o cinto de segurança, e é C em vez de lambda.
    for col in ('COL_USO', 'COL_QTD_BLOCOS', 'COL_FLAG_USO_MISTO'):
        prim = out[col].groupby(gid).transform('first')
        out[col] = np.where(com_id & prim.notna(), prim, out[col])

    # ── PORTE: só depois da harmonização, e nunca sobre o balaio dos sem-grupo
    # COL_CLASSE deriva de COL_FORMA e do tamanho do grupo. Calculá-la ANTES da
    # harmonização fazia a classe variar dentro do grupo (1.063 grupos em Porto
    # Alegre). E agrupar por COLETIVA_ID vazio junta TODOS os registros sem
    # identidade de endereço num grupo só — em POA isso viraria uma "coletiva"
    # de dezenas de milhares de unidades.
    nun = out.groupby(gid)['UNIDADE_ID'].transform('size')
    nbl = pd.to_numeric(out['COL_QTD_BLOCOS'], errors='coerce').fillna(0)
    vert = out['COL_FORMA'].fillna('').astype(str).str.startswith('VERTICAL')
    classe = np.select(
        [(nbl >= 2) | (vert & (nun >= 4)) | (nun >= 6), nun >= 3],
        ['GRANDE', 'PEQUENA'], default='SIMPLES')
    out['COL_CLASSE'] = np.where(com_id, classe, 'SIMPLES')

    # ── polo comercial: DBSCAN por densidade de ENDEREÇOS ──────────────────
    # Reescrito após a auditoria de 2026-08. O que existia aqui estava morto de
    # três maneiras simultâneas, e as três caladas:
    #
    #   1. pré-agregava por CHAVE e passava o AGREGADO para uma função que
    #      espera a BASE e faz o próprio groupby — o agregado não tem NUMERO,
    #      então o filtro `NUMERO > 0` zerava tudo;
    #   2. `polos[0]` de `return agg, polos` selecionava os ENDEREÇOS, não os
    #      polos;
    #   3. lia `p['CHAVES']`, coluna que a função nunca produziu.
    #
    # Resultado em Porto Alegre: COL_POLO_CLASSE = FORA_DE_POLO em 812.653 de
    # 812.653 linhas, com a mesma base rendendo polos pela via direta. E tudo
    # embrulhado em `except Exception` — fail-open, que é o que torna um erro
    # destes indistinguível de um resultado.
    #
    # A ligação correta é em dois saltos: agg dá CHAVE -> POLO_ID, polos dá
    # POLO_ID -> CLASSE_POLO.
    out['COL_POLO_CLASSE'] = 'FORA_DE_POLO'
    if not sem_polos:
        agg, polos = xu.detectar_polos_comerciais(d)
        if len(polos) and len(agg):
            classe = dict(zip(polos['POLO_ID'], polos['CLASSE_POLO']))
            mapa = {ch: classe[pid]
                    for ch, pid in zip(agg['CHAVE'], agg['POLO_ID'])
                    if pid in classe}
            if mapa:
                out['COL_POLO_CLASSE'] = (d['CHAVE'].map(mapa)
                                          .fillna('FORA_DE_POLO').values)

    # recomendação: sem cadastro, a ação honesta é cruzar — nunca "individualizar"
    out['ACT_RECOMENDACAO'] = np.select(
        [out['UND_NATUREZA'] == 'INFERIDO',
         out['ATV_PRESENTE'] == 'SIM',
         out['COL_CLASSE'].isin(['GRANDE', 'PEQUENA'])],
        ['VISTORIA_CONFIRMATORIA', 'CRUZAR_COM_CADASTRO_COMERCIAL',
         'CRUZAR_COM_CADASTRO_COMERCIAL'], default='NENHUMA_ACAO')
    return out


# ════════════════════════════════════════════════════════════════════════════
#  HIPÓTESES — unidades INFERIDAS, na mesma tabela e jamais somadas
# ════════════════════════════════════════════════════════════════════════════
#  Colunas que a hipótese traz do motor de gaps e sobrescrevem o pai.
#  Nomeadas com prefixo para não colidirem no merge com as colunas do RCC.
_GAP_HERDA = {'AUSENTE_INFERIDO': '_G_VALOR', 'GAP_TIPO': '_G_TIPO',
              'PRESENTES': '_G_PRESENTES', 'CONF_GAP': '_G_CONF',
              'EVD_CLASSE': '_G_CLASSE', 'EVD_GRAU': '_G_GRAU',
              'EVD_DISTANCIA': '_G_DIST', 'EVD_DESCRICAO': '_G_DESC',
              'EVD_REGRA': '_G_REGRA', 'ACT_DESTINO_CAMPO': '_G_DESTINO'}


def linhas_inferidas(d, out, colunas):
    """Hipóteses como linhas do RCC — vetorizado, e GRADUADAS (v4.7).

    Nenhuma hipótese é descartada: o que muda é EVD_GRAU e, por consequência,
    ACT_DESTINO_CAMPO. A linha ESPECULATIVA fica na base, disponível para
    consulta e amostragem, e não sai para rota. Isso preserva o quadrante de
    falso negativo da matriz de confusão — sem ele o limiar nunca calibra.
    """
    col = d[d['FLAG_COL'] == 1].copy()
    if not len(col):
        return pd.DataFrame(columns=colunas)
    qlrec = d[(d['PADRAO_ENDERECO'] == 'QUADRA_LOTE') &
              (d['SANIDADE_GEO'] == 'OK') & (d['FLAG_ANOMALIA_COLETA'] == 0)].copy()
    _, gaps, _ = xu.construir_simples_e_faltantes(col, ql_records=qlrec, universo=d)
    if not len(gaps):
        return pd.DataFrame(columns=colunas)

    # Chave do pai em TEXTO (R8). NUNCA `.map(str)` aqui: em coluna Int64 com
    # NA presente o pandas materializa via float64 antes de aplicar a função, e
    # 4700095277622949996 sai como '4.70009527762295e+18' — o merge morre em
    # silêncio e TODA hipótese vira órfã. É o PDCA-01 pela terceira vez, agora
    # por um caminho novo: a conversão implícita, não a construção do frame.
    # id_seguro é a única porta de saída para texto.
    g = gaps.copy()
    g['_H'] = pd.Series(xu.id_seguro(g['RADAR_ID_ENDERECO'], saida='str'),
                        index=g.index).fillna('')
    ref = out.drop_duplicates('COLETIVA_CHAVE_HASH').copy()
    ref['COLETIVA_CHAVE_HASH'] = ref['COLETIVA_CHAVE_HASH'].astype(str).str.strip()
    g = g[g['_H'].isin(set(ref['COLETIVA_CHAVE_HASH']))]
    if not len(g):
        return pd.DataFrame(columns=colunas)

    ren = {k: v for k, v in _GAP_HERDA.items() if k in g.columns}
    g = g[['_H'] + list(ren)].rename(columns=ren)
    m = g.merge(ref, left_on='_H', right_on='COLETIVA_CHAVE_HASH', how='left')

    n = len(m)
    valor = m['_G_VALOR'].astype(str)
    # PATCH LOCAL (execucao Corsan/RS) — a RAIZ podia sair VAZIA.
    # O endereco Quadra/Lote com NUM=0 e a excecao unica de R1: ele gera
    # hipotese e NAO tem COLETIVA_ID. O caminho das observadas ja trata isso
    # com a raiz `SEM-<mun 7>` (ver `identidade`), e este aqui concatenava o
    # COLETIVA_ID vazio direto: o identificador nascia como `-INF-0002-N01`,
    # comecando por hifen e sem dizer nem de que municipio e'. O auditor
    # QA-DER-004 pegou (576 linhas em Santa Maria, 107 em Capao da Canoa) e
    # quarentenou o municipio inteiro — corretamente.
    # A raiz passa a ser a MESMA convencao das observadas.
    _cid = m['COLETIVA_ID'].fillna('').astype(str).str.strip()
    _mun7 = (m['END_MUNICIPIO'].fillna('').astype(str).str.strip().str.zfill(7)
             if 'END_MUNICIPIO' in m.columns
             else pd.Series('', index=m.index))
    raiz_inf = _cid.where(_cid.ne(''), 'SEM-' + _mun7)
    # o ordinal acompanha a RAIZ: agrupar por um COLETIVA_ID vazio poria todas
    # as hipoteses sem pai no mesmo balde por acidente, nao por regra
    ordm = m.groupby([raiz_inf, m['_G_VALOR']]).cumcount() + 1
    m['UNIDADE_ID'] = (raiz_inf + '-INF-'
                       + valor.map(_slug) + '-N' + ordm.astype(str).str.zfill(2))
    m['BLOCO_ID'] = ''
    m['ORIGEM_REGISTRO_ID'] = '(inferido)'
    # Campos de GRÃO DE UNIDADE herdados do pai têm de ser zerados. O pai vem
    # de drop_duplicates, então a hipótese saía vestindo o complemento de um
    # irmão SORTEADO: a linha do 303 vinha com 'APARTAMENTO 601' e quem lê o
    # complemento via um apartamento que existe. Achado em campo.
    # Vazio aqui é "não se aplica": a identidade da hipótese está em UND_VALOR,
    # e *_ORIG é campo de ORIGEM — hipótese não tem origem para preservar.
    for _c in ('END_COMPLEMENTO', 'END_COMPLEMENTO_ORIG'):
        if _c in m.columns:
            m[_c] = ''
    m['UND_NATUREZA'] = 'INFERIDO'
    m['UND_VALOR'] = valor
    m['UND_BLOCO'] = ''
    m['UND_TIPO'] = 'OUTRO'
    m['UND_POSICAO'] = ''
    m['UND_ECONOMIAS'] = 1
    # coordenada NUNCA é fabricada
    for c in ('GEO_LAT_ORIG', 'GEO_LON_ORIG', 'GEO_LAT', 'GEO_LON',
              'GEO_ACURACIA_M', 'GEO_RAIO_GRUPO_M'):
        m[c] = np.nan
    m['GEO_NIVEL'] = ''
    m['GEO_QUALIDADE'] = 'AUSENTE'
    m['GEO_DESLOCAMENTO_M'] = 0.0
    m['CNF_COORD'] = 0
    m['CNF_INFERENCIA'] = m.get('_G_CONF', pd.Series([''] * n)).fillna('')
    m['CNF_FAIXA'] = 'MEDIA'
    m['CNF_TIPOLOGIA'] = ''
    m['ATV_PRESENTE'] = 'INDETERMINADO'
    for c in ('ATV_NOME', 'ATV_NOME_ORIG', 'ATV_SETOR', 'ATV_DETALHE',
              'ATV_GRAO_EVIDENCIA'):
        m[c] = ''
    m['ATV_EVIDENCIA_CAMPO'] = 'NENHUM'
    m['ATV_INCERTEZA_M'] = np.nan
    m['ATV_FLAG_FACHADA_ATIVA'] = 0
    m['ATV_FLAG_GAP_TARIFARIO'] = 0

    # — graduação (R9) — vem do motor, não é recalculada aqui
    m['EVD_REGRA'] = m['_G_REGRA'].fillna('')
    m['EVD_DESCRICAO'] = m['_G_DESC'].fillna('')
    m['EVD_PRESENTES'] = m['_G_PRESENTES'].fillna('').astype(str)
    m['EVD_CLASSE'] = m['_G_CLASSE'].fillna('')
    m['EVD_GRAU'] = m['_G_GRAU'].fillna('')
    m['EVD_DISTANCIA'] = pd.array(
        pd.to_numeric(m['_G_DIST'], errors='coerce'), dtype='Int64')
    m['ACT_RECOMENDACAO'] = 'VISTORIA_CONFIRMATORIA'
    m['ACT_REQUER_CONFIRMACAO'] = 'SIM'
    m['ACT_DESTINO_CAMPO'] = m['_G_DESTINO'].fillna('RETER')

    faltando = [c for c in colunas if c not in m.columns]
    for c in faltando:
        m[c] = ''
    return m[colunas].reset_index(drop=True)


# ════════════════════════════════════════════════════════════════════════════
#  GATES — pós-condições executáveis, imunes a `python -O`
# ════════════════════════════════════════════════════════════════════════════
def aplicar_tipos(out, tipos):
    """O TIPO declarado no dicionário vira coerção, não convenção.

    Sem isto, uma coluna inteira que passa por transform com NaN volta float e
    sai escrita como '0.0' — que reprova no domínio '0 | 1' e, pior, chega ao
    consumidor como decimal onde ele espera flag. Foi o que aconteceu na base
    real de Porto Alegre.
    """
    for c, t in (tipos or {}).items():
        if c not in out.columns:
            continue
        if t == 'inteiro':
            out[c] = pd.array(pd.to_numeric(out[c], errors='coerce'),
                              dtype='Int64')
        elif t.startswith('decimal'):
            casas = 6 if 'LAT' in c or 'LON' in c else 1
            out[c] = pd.to_numeric(out[c], errors='coerce').round(casas)
    return out


# Réguas da marcação de equivalência de logradouro. São as MESMAS da fusão por
# proximidade (R7: a geometria arbitra, nunca o texto) — usar duas réguas para
# a mesma pergunta seria admitir que uma delas é arbitrária.
EQUIV_RAIO_SUSTENTADA_M = 50.0
EQUIV_RAIO_PLAUSIVEL_M = 300.0
# Acima disto a chave fonetica deixou de discriminar: nao e' abreviacao, e'
# nome generico. O corte e' DECLARADO no manifest, nunca silencioso.
TETO_GRUPO_EQUIV = 40


def pares_provados_de_logradouro(d, log=print):
    """Pares que a BASE prova serem o mesmo endereço, para o léxico aprender.

    Blocking por (município, localidade, NÚMERO EXATO): o número é a âncora
    mais barata e mais discriminante — sem ele o par vira comparação de string
    entre ruas que só compartilham o bairro.
    """
    need = {'RADAR_CHAVE_CANONICA', 'COD_MUNICIPIO', 'DSC_LOCALIDADE',
            'NUMERO', 'LATITUDE', 'LONGITUDE'}
    if not need <= set(d.columns):
        return []
    b = d.loc[d['RADAR_CHAVE_CANONICA'].notna(), sorted(need)].copy()
    if not len(b):
        return []
    b['_slot'] = b['RADAR_CHAVE_CANONICA'].astype(str).map(
        lambda v: v.split('|')[1] if v.count('|') >= 3 else '')
    b['_lat'] = pd.to_numeric(b['LATITUDE'], errors='coerce')
    b['_lon'] = pd.to_numeric(b['LONGITUDE'], errors='coerce')
    ag = (b.groupby(['COD_MUNICIPIO', 'DSC_LOCALIDADE', 'NUMERO', '_slot'])
          [['_lat', '_lon']].mean().reset_index())
    pares, pulados = [], 0
    for (mun, loc, num), g in ag.groupby(['COD_MUNICIPIO', 'DSC_LOCALIDADE',
                                          'NUMERO'], sort=False):
        if len(g) < 2:
            continue
        if len(g) > lx.TETO_BLOCO:
            pulados += 1        # DECLARADO, nunca silencioso
            continue
        reg = g.to_dict('records')
        for i in range(len(reg)):
            for j in range(i + 1, len(reg)):
                a, c = reg[i], reg[j]
                if pd.isna(a['_lat']) or pd.isna(c['_lat']):
                    continue
                if _haversine_m(a['_lat'], a['_lon'],
                                c['_lat'], c['_lon']) > lx.RAIO_PROVA_M:
                    continue
                par = lx.par_de_substituicao(a['_slot'].split(),
                                             c['_slot'].split())
                if par:
                    chave = f'{mun}|{loc}|{num}'
                    pares.append((par[0], par[1], chave, xu.fonetica_token))
    if pulados:
        log(f'  lexico: {pulados} bloco(s) acima de {lx.TETO_BLOCO} enderecos '
            'no mesmo numero nao foram pareados (custo quadratico)')
    return pares


def equivalencias_logradouro(d, lex_ativos=None):
    """MARCA logradouros que provavelmente são o mesmo — sem fundir nada.

    Modelo idêntico ao das hipóteses de unidade (R9): a evidência gera o
    candidato, a confirmação independente atribui o grau, nada é aplicado
    sozinho e nada é descartado. Aqui o candidato vem da fonética/romano e a
    confirmação vem de GEOMETRIA + NUMERAÇÃO — que são fato do IBGE, não
    opinião sobre a string.

    Devolve dict canonica -> (sugerida, grau, evidencia).
    """
    need = {'RADAR_CHAVE_CANONICA', 'COD_MUNICIPIO', 'DSC_LOCALIDADE',
            'NUMERO', 'LATITUDE', 'LONGITUDE'}
    if not need <= set(d.columns):
        return {}
    b = d.loc[d['RADAR_CHAVE_CANONICA'].notna(), sorted(need)].copy()
    if not len(b):
        return {}
    b['_slot'] = b['RADAR_CHAVE_CANONICA'].astype(str).map(
        lambda v: v.split('|')[1] if v.count('|') >= 3 else '')
    b['_lat'] = pd.to_numeric(b['LATITUDE'], errors='coerce')
    b['_lon'] = pd.to_numeric(b['LONGITUDE'], errors='coerce')
    ag = b.groupby('RADAR_CHAVE_CANONICA').agg(
        slot=('_slot', 'first'), mun=('COD_MUNICIPIO', 'first'),
        loc=('DSC_LOCALIDADE', 'first'), lat=('_lat', 'mean'),
        lon=('_lon', 'mean')).reset_index()
    nums = b.groupby('RADAR_CHAVE_CANONICA')['NUMERO'].agg(set).to_dict()
    # O LÉXICO entra AQUI e só aqui: ele amplia o conjunto de CANDIDATOS
    # (`CONS`≡`CONSELHEIRO`, que a fonética não pega), e a confirmação continua
    # sendo geometria + numeração. Nenhum token aprendido chega ao identificador.
    ag['_k'] = (ag['mun'].astype(str) + '|' + ag['loc'].astype(str) + '|'
                + ag['slot'].map(lambda s: xu.chave_equivalencia(
                    lx.aplicar(s, lex_ativos or {}))))

    saida = {}
    grandes = 0
    for _k, g in ag.groupby('_k', sort=False):
        if len(g) < 2:
            continue
        # TETO DECLARADO. Uma chave fonética compartilhada por dezenas de
        # endereços do mesmo bairro não é abreviação — é nome genérico
        # ('SEM DENOMINACAO', 'PROJETADA'), e parear isso é O(k²) para
        # produzir ruído. O corte é contado e vai para o manifest: cobertura
        # que se limita em silêncio lê como cobertura completa.
        if len(g) > TETO_GRUPO_EQUIV:
            grandes += 1
            continue
        # `.iloc` dentro de laço duplo custa mais que o cálculo: em Porto
        # Alegre são 279 mil grupos, e o emissor passou de 27 min sem chegar
        # na publicação. Materializar registros uma vez resolve.
        reg = g.to_dict('records')
        for i, ri in enumerate(reg):
            melhor = None
            for j, rj in enumerate(reg):
                # SLOT IGUAL NÃO É SUGESTÃO. Dois endereços da MESMA rua em
                # números diferentes caem na mesma chave — e sugerir a rua para
                # ela mesma marcou 278.724 linhas de Porto Alegre, 34% da base.
                # Marcação que dispara em um terço do arquivo não é marcação,
                # é papel de parede.
                if i == j or rj['slot'] == ri['slot']:
                    continue
                dist = float('inf')
                if pd.notna(ri['lat']) and pd.notna(rj['lat']):
                    dist = float(_haversine_m(ri['lat'], ri['lon'],
                                              rj['lat'], rj['lon']))
                comum = nums.get(ri['RADAR_CHAVE_CANONICA'], set()) & \
                    nums.get(rj['RADAR_CHAVE_CANONICA'], set())
                # SUSTENTADA exige as DUAS provas: a geometria diz que é o
                # mesmo lugar E a numeração diz que é a mesma régua. Só a
                # fonética nunca sustenta — foi ela que fundiu CEFER I e II.
                if dist <= EQUIV_RAIO_SUSTENTADA_M and comum:
                    grau, ev = 'SUSTENTADA', (
                        f'mesma coordenada ({dist:.0f} m) e {len(comum)} '
                        'numero(s) em comum')
                elif dist <= EQUIV_RAIO_PLAUSIVEL_M:
                    grau, ev = 'PLAUSIVEL', f'grafias vizinhas ({dist:.0f} m)'
                else:
                    grau, ev = 'ESPECULATIVA', 'so a grafia aproxima'
                ordem = xu.GRAUS_INFERENCIA.index(grau)
                if melhor is None or ordem < melhor[0]:
                    melhor = (ordem, rj['slot'], grau, ev)
            if melhor:
                saida[ri['RADAR_CHAVE_CANONICA']] = (melhor[1], melhor[2],
                                                     melhor[3])
    saida['__grupos_acima_do_teto__'] = grandes
    return saida


def _colisao_declarada(out):
    """O fator de colisão que o PRODUTOR afirma — o auditor recalcula o dele."""
    if not {'END_LOGRADOURO_ORIG', 'COLETIVA_CHAVE_CANONICA'} <= set(out.columns):
        return {}
    f = out['END_LOGRADOURO_ORIG'].fillna('').astype(str).str.strip()
    c = (out['COLETIVA_CHAVE_CANONICA'].fillna('').astype(str)
         .map(lambda v: v.split('|')[1] if v.count('|') >= 3 else ''))
    m = f.ne('') & c.ne('')
    return xu.colisao_logradouro(f[m], c[m])


def crosswalk_canonica(d):
    """PONTE v1→v2 da identidade de endereço — 1 linha por endereço distinto.

    Mudar a canônica sem publicar a ponte renumera 279.564 endereços de Porto
    Alegre e mata a série histórica em silêncio: quem tem a entrega anterior
    não descobre sozinho que `COL-4314902-000123` de ontem é outro id hoje.
    A ponte viaja DENTRO do pacote lacrado, com hash próprio no selo — é parte
    da entrega, não um anexo que alguém manda por e-mail depois.
    """
    need = {'RADAR_CHAVE_CANONICA', 'RADAR_CHAVE_CANONICA_V2',
            'RADAR_ID_ENDERECO', 'RADAR_ID_ENDERECO_V2'}
    if not need <= set(d.columns):
        return pd.DataFrame(columns=sorted(need))
    cw = d.loc[d['RADAR_ID_ENDERECO'].notna(), sorted(need)].drop_duplicates()
    cw = cw.rename(columns={
        'RADAR_CHAVE_CANONICA': 'COLETIVA_CHAVE_CANONICA',
        'RADAR_CHAVE_CANONICA_V2': 'COLETIVA_CHAVE_CANONICA_ANTERIOR',
        'RADAR_ID_ENDERECO': 'COLETIVA_CHAVE_HASH',
        'RADAR_ID_ENDERECO_V2': 'COLETIVA_CHAVE_HASH_ANTERIOR'})
    for c in ('COLETIVA_CHAVE_HASH', 'COLETIVA_CHAVE_HASH_ANTERIOR'):
        cw[c] = xu.id_seguro(cw[c], saida='str')
    cw['CANON_VERSAO_DE'] = xu.CANON_VERSAO - 1
    cw['CANON_VERSAO_PARA'] = xu.CANON_VERSAO
    cw['MUDOU'] = np.where(
        cw['COLETIVA_CHAVE_HASH'].ne(cw['COLETIVA_CHAVE_HASH_ANTERIOR']), 'SIM', 'NAO')
    return cw[['COLETIVA_CHAVE_HASH_ANTERIOR', 'COLETIVA_CHAVE_CANONICA_ANTERIOR',
               'COLETIVA_CHAVE_HASH', 'COLETIVA_CHAVE_CANONICA',
               'CANON_VERSAO_DE', 'CANON_VERSAO_PARA', 'MUDOU']].sort_values(
        'COLETIVA_CHAVE_CANONICA_ANTERIOR', kind='stable')


def _bloco_set_metricas(out):
    """Cobertura do bloco SET medida no ARTEFATO FINAL, não no intermediário.

    `xs.cobertura(d)` roda antes de as hipóteses entrarem: declarava 100% de
    casamento sobre um universo que não é o entregue. E como não havia nada no
    auditor que recalculasse isto, o número era uma afirmação sem contraparte.
    Escrito aqui, no produtor, com expressão própria — a reconciliação só vale
    se os dois lados forem calculados separadamente.
    """
    n = len(out)
    if 'SET_COD' not in out.columns:
        return {}
    dom = out.get('SET_DOM_PARTICULARES')
    if dom is None:
        casado = pd.Series(False, index=out.index)
    else:
        casado = dom.notna()
        if dom.dtype == object:      # bloco ausente: coluna de strings vazias
            casado = casado & dom.astype(str).str.strip().ne('')
    cod = out['SET_COD'].fillna('').astype(str).str.strip()
    return {'linhas': int(n),
            'com_setor_casado': int(casado.sum()),
            'pct_casado': round(100.0 * float(casado.mean()), 2) if n else 0.0,
            'setores_distintos': int(cod[cod.ne('')].nunique())}


def gates(out, dominios, obrigatorias=None, tipos=None):
    _exigir(out['UNIDADE_ID'].fillna('').astype(str).str.strip().ne('').all(),
            'G1: UNIDADE_ID vazio — toda unidade precisa de identidade')
    dv = out[out['COLETIVA_ID'].fillna('').astype(str).str.strip().ne('')]
    if len(dv):                     # base sem nenhum agrupamento e' caso valido
        _exigir(bool(dv.apply(lambda r: str(r['UNIDADE_ID']).startswith(
            str(r['COLETIVA_ID'])), axis=1).all()),
            'G2: UNIDADE_ID que nao deriva do COLETIVA_ID')

    inf = out['UND_NATUREZA'] == 'INFERIDO'
    if inf.any():
        _exigir((pd.to_numeric(out.loc[inf, 'CNF_COORD'], errors='coerce') == 0).all(),
                'G3: unidade INFERIDA com CNF_COORD != 0')
        _exigir((out.loc[inf, 'ACT_REQUER_CONFIRMACAO'] == 'SIM').all(),
                'G4: unidade INFERIDA sem REQUER_CONFIRMACAO=SIM')
        _exigir(out.loc[inf, 'GEO_LAT'].isna().all(),
                'G5: coordenada fabricada em unidade INFERIDA')

    # G6 — redefinido. A versao anterior neutralizava a condicao com uma
    # disjuncao sempre verdadeira:
    # nunca podia reprovar, e ainda assim entrava na contagem de gates
    # anunciada na doutrina. Gate decorativo e' pior que gate ausente, porque
    # induz confianca. O invariante REAL nao e' "todo observado tem numero"
    # (NUM=0 e' legitimo, vira NAO_COLETIVA); e' que registro SEM numero nao
    # pode ter identidade de agrupamento — a unidade existe, a coletiva nao.
    # Exceção necessária, encontrada quando o gate passou a de fato rodar:
    # endereçamento por QUADRA/LOTE tem identidade sem número predial — é o
    # outro regime, não ausência de identidade. Exigir número dele seria o
    # mesmo erro que matou a detecção de condomínio horizontal.
    obs = ~inf
    _num = pd.to_numeric(out.loc[obs, 'END_NUMERO'], errors='coerce').fillna(0)
    _cid = out.loc[obs, 'COLETIVA_ID'].fillna('').astype(str).str.strip()
    _ql = (out.loc[obs, 'END_QUADRA'].fillna('').astype(str).str.strip().ne('')
           | out.loc[obs, 'END_LOTE'].fillna('').astype(str).str.strip().ne(''))
    _maus = out.loc[obs].index[(_num <= 0) & _cid.ne('') & ~_ql]
    _exigir(len(_maus) == 0,
            f'G6: {len(_maus)} registro(s) sem numero e sem quadra/lote '
            'receberam COLETIVA_ID — sem um dos dois nao ha identidade de '
            'endereco (R1)')

    atv = out['ATV_PRESENTE'] == 'SIM'
    if atv.any():
        _exigir(out.loc[atv, 'ATV_EVIDENCIA_CAMPO'].ne('NENHUM').all(),
                'G7: atividade afirmada sem campo de origem nomeado')
        _exigir(out.loc[atv, 'ATV_GRAO_EVIDENCIA'].isin(['UNIDADE', 'ENDERECO']).all(),
                'G8: atividade sem grao de evidencia declarado')
    _exigir((pd.to_numeric(out['ATV_FLAG_GAP_TARIFARIO'],
                           errors='coerce').fillna(0) == 0).all(),
            'G9: gap tarifario afirmado sem cadastro — esta skill nao tem tarifa')

    # domínio fechado: valor fora da lista aborta
    for campo, vals in dominios.items():
        if campo not in out.columns:
            continue
        s = out[campo].dropna().astype(str)
        s = s[s.ne('')]
        fora = sorted(set(s) - set(vals))
        _exigir(not fora, f'G10: {campo} com valor fora do dominio {fora[:4]}')

    # G12: UNIDADE_ID identifica o GRÃO — repetido dentro da mesma origem
    # significa que duas economias distintas colapsaram numa linha só.
    dup = (out.groupby(['UNIDADE_ID', 'ORIGEM_SISTEMA']).size())
    dup = dup[dup > 1]
    _exigir(dup.empty,
            f'G12: UNIDADE_ID repetido na mesma origem ({len(dup)} casos, '
            f'ex.: {list(dup.index[:3])}) — o grao deixou de ser unidade')

    # G13: campo de GRUPO constante dentro do grupo
    gid = out[out['COLETIVA_ID'].ne('')]
    for col in ('COL_FORMA', 'COL_CLASSE', 'COL_VEREDITO', 'COL_USO',
                'COL_CRITERIO_UNIFICACAO', 'COL_QTD_OBSERVADA',
                'COL_QTD_INFERIDA'):
        n = gid.groupby('COLETIVA_ID')[col].nunique(dropna=False)
        maus = n[n > 1]
        _exigir(maus.empty,
                f'G13: {col} varia dentro do grupo ({len(maus)} grupos, '
                f'ex.: {list(maus.index[:2])}) — campo de grupo nao pode '
                'depender da linha')

    # G14: coluna OBRIGATORIA inteiramente vazia e' defeito.
    # A lista vem da coluna OBRIGATORIO do dicionario — o contrato ja declarado —
    # e NAO de uma lista de isencoes mantida a mao. A primeira versao deste gate
    # usava lista de isencao tunada na fixture sintetica e reprovava bases
    # legitimas (sem blocos, sem coordenada): gate calibrado no caso feliz
    # reprova a realidade.
    for c in obrigatorias or []:
        if c not in out.columns:
            continue
        _exigir(out[c].fillna('').astype(str).str.strip().ne('').any(),
                f'G14: coluna OBRIGATORIA {c} saiu inteiramente vazia')

    # G15: TIPO declarado x tipo emitido. Inteiro escrito como '1.0' passa
    # despercebido no CSV e quebra o consumidor que espera flag.
    for c, t in (tipos or {}).items():
        if c not in out.columns or t != 'inteiro':
            continue
        s = out[c].dropna().astype(str)
        s = s[s.str.strip().ne('')]
        maus = s[~s.str.fullmatch(r'-?\d+')]
        # a mensagem so' e' montada se houver violacao: f-string com .iloc[0]
        # em serie vazia estoura ANTES de o gate decidir — o instrumento
        # quebrando no lugar do produto, de novo.
        if not maus.empty:
            _exigir(False, f'G15: {c} declarado inteiro mas emitido como '
                           f'{maus.iloc[0]!r}')

    # id em texto: float em coluna de identificador aborta (R8)
    for c in out.columns:
        if c.endswith('_ID') or c.endswith('_HASH'):
            _exigir(not pd.api.types.is_float_dtype(out[c]),
                    f'G11: {c} em float — identificador nunca em ponto flutuante')

    # ── R9: graduação da inferência ────────────────────────────────────────
    inf = out['UND_NATUREZA'] == 'INFERIDO'

    # G16: hipótese sem grau entraria na base por porta dos fundos, sem
    # classificação e sem destino — e ninguém saberia se pode ir a campo.
    if inf.any():
        sem = out.loc[inf, 'EVD_GRAU'].fillna('').astype(str).str.strip().eq('')
        if sem.any():
            ex = out.loc[inf][sem].iloc[0]
            _exigir(False, f'G16: {int(sem.sum())} linha(s) INFERIDO sem '
                           f'EVD_GRAU; ex. UNIDADE_ID={ex["UNIDADE_ID"]}')

    # G17: AMOSTRAR e RETER nunca entram no export de campo, e NENHUM grau
    # entra em contagem. A exclusão de agregados vale para os três igualmente —
    # graduar decide o que vai a campo, não o que conta.
    dest = out['ACT_DESTINO_CAMPO'].fillna('').astype(str)
    _exigir(dest.ne('').all(), 'G17: ACT_DESTINO_CAMPO vazio')
    _exigir(dest.isin({'ENVIAR', 'AMOSTRAR', 'RETER'}).all(),
            f'G17: destino fora do domínio: {sorted(set(dest) - {"ENVIAR", "AMOSTRAR", "RETER"})}')
    if inf.any():
        esperado = out.loc[inf, 'EVD_GRAU'].map(xu.DESTINO_POR_GRAU)
        div = esperado.fillna('') != dest[inf]
        if div.any():
            ex = out.loc[inf][div].iloc[0]
            _exigir(False, f'G17: destino não deriva do grau em {int(div.sum())} '
                           f'linha(s); ex. grau={ex["EVD_GRAU"]} destino={ex["ACT_DESTINO_CAMPO"]}')

    # G18: EVD_DISTANCIA preenchida se e somente se a classe for extrapolação.
    # É o campo que permite recalibrar o corte com um UPDATE; se ele vazar para
    # classes interpoladas, o UPDATE passa a mover hipóteses que não deveria.
    if 'EVD_DISTANCIA' in out.columns:
        temd = out['EVD_DISTANCIA'].notna()
        ehc = out['EVD_CLASSE'].fillna('').astype(str).eq('C_EXTRAPOLADA')
        _exigir((temd == ehc).all(),
                f'G18: EVD_DISTANCIA e C_EXTRAPOLADA divergem em '
                f'{int((temd != ehc).sum())} linha(s) — a distância só existe '
                'em extrapolação')

    # G19: a taxa do setor nunca vira contagem por unidade
    xs.gate_set_nao_multiplicado(out)

    # G20: a canonicalização pode fundir GRAFIA; não pode fundir RUA.
    # Este número existe porque a decisão "largar o título é seguro?" voltou
    # como argumento uma vez — e argumento não é gate. Agora é medido, fica no
    # manifest, e o auditor recalcula por conta própria.
    if {'END_LOGRADOURO_ORIG', 'COLETIVA_CHAVE_CANONICA'} <= set(out.columns):
        _f = out['END_LOGRADOURO_ORIG'].fillna('').astype(str).str.strip()
        _c = (out['COLETIVA_CHAVE_CANONICA'].fillna('').astype(str)
              .map(lambda v: v.split('|')[1] if v.count('|') >= 3 else ''))
        _m = _f.ne('') & _c.ne('')
        col = xu.colisao_logradouro(_f[_m], _c[_m])
        _exigir(col['fator'] <= xu.TETO_COLISAO_LOGR,
                f"G20: fator de colisao de logradouro {col['fator']}x acima do "
                f"teto {xu.TETO_COLISAO_LOGR}x — a canonicalizacao esta "
                f"fundindo grafias demais. Exemplos: {col['exemplos'][:3]}")



def _runtime_efetivo() -> dict:
    """Evidencia do runtime dentro do pacote — o modo strict depende dela."""
    import importlib.metadata as _md
    import platform as _pl
    out = {'python': _pl.python_version()}
    for p_ in ('pandas', 'numpy', 'scikit-learn', 'openpyxl'):
        try:
            out[p_] = _md.version(p_)
        except Exception:                                    # noqa: BLE001
            out[p_] = 'ausente'
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--store', default=None,
                    help='store append-only de COLETIVA_ID (estabilidade entre safras)')
    ap.add_argument('--lexico', default=None,
                    help='JSON do lexico auto-incremental de logradouro. '
                         'Aprende equivalencia de token de par PROVADO na base '
                         '(mesmo numero + 50 m + 1 token 1<->1) e so alimenta a '
                         'MARCACAO graduada — o identificador nunca o enxerga.')
    ap.add_argument('--municipio', default=None)
    ap.add_argument('--safra', default='NAO_DECLARADA',
                    help='safra do arquivo de entrada; ausente vira NAO_DECLARADA '
                         '(vazio significaria "nao se aplica", e sempre se aplica)')
    ap.add_argument('--qa-mode', choices=['strict', 'exploratory'],
                    default='strict',
                    help='strict: runtime divergente ou bloco obrigatorio '
                         'ausente NAO sela. exploratory: sela com aviso.')
    ap.add_argument('--sem-polos', action='store_true',
                    help='nao calcula polos comerciais (DBSCAN); sem esta flag '
                         'uma falha nos polos ABORTA em vez de emitir vazio')
    ap.add_argument('--sem-setor', action='store_true',
                    help='nao acopla o bloco SET (ocupacao por setor censitario)')
    ap.add_argument('--cache-ibge', default='./cache_ibge',
                    help='cache local dos agregados por setor do IBGE')
    ap.add_argument('--dicionario', default=None,
                    help='CSV do dicionario canonico; define a ordem e os dominios '
                         'das colunas. Default: RCC_dicionario.csv na raiz da skill')
    a = ap.parse_args()

    print('RCC — emissor canonico (fonte: base de enderecos do IBGE)')
    print('=' * 66)
    df, stats = rp.preparar_base(a.input, municipio=a.municipio)

    # ordem das colunas vem do DICIONARIO — contrato, nao convencao do codigo
    if not a.dicionario:
        a.dicionario = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'RCC_dicionario.csv')
    if os.path.exists(a.dicionario):
        with open(a.dicionario, encoding='utf-8-sig') as fh:
            dic = list(csv.DictReader(fh, delimiter=';'))
        colunas = [r['CAMPO'] for r in dic]
        # domínio FECHADO é lista de valores; '<' marca especificação de FORMA
        # (ex.: '<BLOCO_ID|COLETIVA_ID>-<TIPO 3>'), que não é enumeração.
        dominios = {r['CAMPO']: [v.strip() for v in r['DOMINIO'].split('|')]
                    for r in dic
                    if '|' in r['DOMINIO'] and '<' not in r['DOMINIO']}
        obrigatorias = [r['CAMPO'] for r in dic if r['OBRIGATORIO'] == 'sim']
        tipos = {r['CAMPO']: r['TIPO'] for r in dic}
    else:
        raise SystemExit('--dicionario e obrigatorio: a ordem e os dominios das '
                         'colunas sao contrato, nao convencao do codigo')

    # Runtime divergente do lock hoje so' virava WARNING no manifest e o
    # arquivo saia igual. Em --qa-mode strict isso passa a impedir o lacre:
    # reprodutibilidade declarada que nao e' imposta e' reprodutibilidade
    # imaginada.
    # A FONTE SET é resolvida ANTES do fingerprint. Lê-la depois significava
    # que, com o cache vazio, o arquivo efetivamente adquirido NAQUELA execução
    # ficava de fora — e duas fontes distintas davam o mesmo fingerprint.
    # `csv_set` é o caminho REAL devolvido pela aquisição, nunca um presumido.
    csv_set = None
    if not a.sem_setor:
        try:
            csv_set = xs.baixar_basico(Path(a.cache_ibge))
        except xs.SetorError as e:
            print(f'  fonte SET indisponivel: {e}')

    # O LÉXICO muda a marcação, logo muda o artefato — entra no fingerprint
    # pela mesma razão que a fonte SET entrou: duas execuções com léxicos
    # diferentes não são equivalentes, e afirmar que são é o defeito.
    _lex = lx.carregar(a.lexico)

    _lock = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'requirements.lock.txt')
    _runtime_avisos = qa.divergencia_de_runtime(_lock)

    h_full = hashlib.sha256(open(a.input, 'rb').read()).hexdigest()
    h = h_full[:16]
    # RUN_ID identifica ESTA execucao. O anterior era blake2b(input, 3 bytes):
    # 24 bits e deterministico da entrada — rodar duas vezes dava o mesmo id, o
    # que anulava a quarentena por RUN_ID (a segunda falha sobrescrevia a
    # evidencia da primeira). Timestamp UTC + uuid4 sao unicos por construcao.
    from datetime import datetime as _dt, timezone as _tz
    import uuid as _uuid
    exec_id = ('RUN-' + _dt.now(_tz.utc).strftime('%Y%m%dT%H%M%S.%f')[:-3] + 'Z-'
               + _uuid.uuid4().hex[:12])
    # Duas identidades distintas: RUN_ID diz QUAL execucao; RUN_FINGERPRINT diz
    # se DUAS execucoes sao equivalentes (mesma entrada, mesmo contrato, mesma
    # versao). O exec_id de 24 bits nao servia para nenhuma das duas coisas.
    # RUN_FINGERPRINT diz se DUAS execucoes sao equivalentes. Faltavam nele
    # coisas capazes de mudar o resultado: --municipio (dois municipios do mesmo
    # arquivo davam o mesmo fingerprint), o STORE (que participa da estabilidade
    # dos ids), a fonte SET e o runtime efetivo.
    def _sha_arq(p_):
        try:
            return qa.sha256_arquivo(p_) if p_ and os.path.exists(p_) else ''
        except OSError:
            return ''

    _params = json.dumps({
        'municipio': a.municipio, 'safra': a.safra, 'qa_mode': a.qa_mode,
        'sem_setor': bool(a.sem_setor), 'sem_polos': bool(a.sem_polos),
    }, sort_keys=True, ensure_ascii=False)
    _lock_sha = _sha_arq(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'requirements.lock.txt'))
    _qa_sha = _sha_arq(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'qualidade_artefato.py'))
    # A FONTE SET efetivamente usada e o RUNTIME EFETIVO entram no
    # fingerprint. Sem eles, duas execucoes com taxas de desocupacao de 20% e
    # 50% produziam artefatos diferentes e o MESMO fingerprint — o sistema
    # declarava equivalentes duas execucoes semanticamente distintas. E o
    # `lock_sha` diz qual ambiente DEVERIA existir, nao qual existia: em
    # exploratory os dois divergem por design.
    _set_sha = _sha_arq(csv_set) if csv_set else ''
    _runtime_sha = hashlib.sha256(json.dumps(
        _runtime_efetivo(), sort_keys=True).encode()).hexdigest()
    run_fp = hashlib.sha256('|'.join([
        h_full, VERSAO, qa.VERSAO_QA, _params,
        qa.contrato_rcc(a.dicionario)['sha256'],
        _sha_arq(a.store), _qa_sha, _lock_sha, _set_sha, _runtime_sha,
        _sha_arq(a.lexico),
    ]).encode()).hexdigest()

    from datetime import datetime, timezone
    agora = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    d = emitir(df, store_path=a.store, safra=a.safra, hash_entrada=h)

    # APRENDE antes de MARCAR: o que esta execução provar já vale para ela
    # própria. O léxico é gravado depois do lacre — artefato reprovado não
    # deixa aprendizado para trás.
    if a.lexico:
        from datetime import date as _date
        _hoje_ord = _date(*[int(x) for x in agora[:10].split('-')]).toordinal()
        _lex = lx.aprender(pares_provados_de_logradouro(d), _lex, _hoje_ord)
        print(f'  lexico: {lx.resumo(_lex)}')

    # ── bloco SET — enriquecimento com a fonte JÁ resolvida acima ──────────
    cob = {'estado': 'NAO_SOLICITADO'}
    if not a.sem_setor:
        try:
            if csv_set is None:
                raise xs.SetorError('fonte SET nao pode ser adquirida')
            muns = sorted(d['COD_MUNICIPIO'].astype(str).unique())
            setores = xs.carregar_setores(csv_set, muns)
            xs.gate_set_nao_multiplicado(setores)
            d = xs.enriquecer(d, setores)
            cob = xs.cobertura(d); cob['estado'] = 'OK'
            cob['setores_carregados'] = int(len(setores))
            cob['fonte_sha256'] = _set_sha
            print(f"  bloco SET: {cob['pct_casado']}% das linhas com setor casado")
        except xs.SetorError as e:
            cob = {'estado': 'FALHOU', 'erro': str(e)[:200]}
            print(f'  bloco SET indisponivel: {e}')

    out = montar_saida(d, exec_id, f'IBGE/RCC-{VERSAO}', a.safra, h, colunas,
                       lex_ativos=lx.ativos(_lex))
    out['AUD_EXECUTADO_EM'] = agora
    out = pos_agregados(out, d, sem_polos=a.sem_polos)

    inferidas = linhas_inferidas(df, out, colunas)
    if len(inferidas):
        out = pd.concat([out, inferidas], ignore_index=True)
    # o total de inferidas do grupo volta para TODAS as linhas do grupo
    ninf = (out.assign(_i=(out['UND_NATUREZA'] == 'INFERIDO').astype(int))
            .groupby('COLETIVA_ID')['_i'].transform('sum'))
    out['COL_QTD_INFERIDA'] = ninf.values
    nobs = (out.assign(_o=(out['UND_NATUREZA'] == 'OBSERVADO').astype(int))
            .groupby('COLETIVA_ID')['_o'].transform('sum'))
    out['COL_QTD_OBSERVADA'] = nobs.values

    _eq_grandes = int(out.attrs.get('equiv_grupos_acima_do_teto', 0))
    cw = crosswalk_canonica(d)
    out = aplicar_tipos(out, tipos)
    out = out.sort_values(['END_MUNICIPIO', 'COLETIVA_ID', 'BLOCO_ID',
                           'UNIDADE_ID', 'ORIGEM_SISTEMA'], kind='stable')
    gates(out, dominios, obrigatorias, tipos)

    # ── R13: PACOTE pendente -> QA (com manifest) -> lacre -> publicacao ───
    # `GATES OK` aprovou o DATAFRAME. O ARQUIVO ainda nao foi olhado, e o
    # manifest tem de entrar NO ciclo: reconciliar depois de publicar seria
    # conferir o troco depois de sair da loja.
    man = {'rcc_versao': '1.0', 'fonte': 'IBGE', 'entrada': os.path.basename(a.input),
           'input_sha256': h_full, 'sha256_16': h, 'exec_id': exec_id,
           'run_fingerprint': run_fp, 'pipeline_version': VERSAO,
           'safra': a.safra,
           'linhas': int(len(out)),
           'observadas': int((out['UND_NATUREZA'] == 'OBSERVADO').sum()),
           'inferidas': int((out['UND_NATUREZA'] == 'INFERIDO').sum()),
           'coletivas_distintas': int(
               out.loc[out['COLETIVA_ID'].ne(''), 'COLETIVA_ID'].nunique()),
           'unidades_em_coletiva': int((out['COL_FORMA'] != 'INDEFINIDA').sum()),
           'grupos_coletivos': int(out.loc[out['COL_FORMA'] != 'INDEFINIDA',
                                           'COLETIVA_ID'].nunique()),
           'com_atividade': int((out['ATV_PRESENTE'] == 'SIM').sum()),
           'inferidas_por_grau': {k: int(v) for k, v in
               out.loc[out['UND_NATUREZA'] == 'INFERIDO', 'EVD_GRAU']
               .value_counts().items()},
           'inferidas_por_classe': {k: int(v) for k, v in
               out.loc[out['UND_NATUREZA'] == 'INFERIDO', 'EVD_CLASSE']
               .value_counts().items()},
           'destino_campo': {k: int(v) for k, v in
               out['ACT_DESTINO_CAMPO'].value_counts().items()},
           # RECONCILIÁVEL vs PROVENIÊNCIA. A cobertura vinha medida no
           # dataframe INTERMEDIÁRIO (antes das hipóteses) e misturada com
           # sha da fonte e estado do download — um dicionário meio auditável
           # não é auditável. Aqui as métricas são recalculadas sobre `out`,
           # que é o arquivo entregue, e a proveniência sai do confronto.
           'canon_versao': int(xu.CANON_VERSAO),
           'canon_ids_alterados_vs_v1': int((cw['MUDOU'] == 'SIM').sum())
                                        if len(cw) else 0,
           'colisao_logradouro': _colisao_declarada(out),
           'equiv_grupos_acima_do_teto': int(_eq_grandes),
           'lexico_proveniencia': (lx.resumo(_lex) if a.lexico else
                      {'versao': lx.VERSAO_LEXICO, 'ativos': 0,
                       'candidatos': 0, 'arquivados': 0, 'quarentena': 0,
                       'blacklist': 0}),
           'bloco_set': _bloco_set_metricas(out),
           'bloco_set_proveniencia': {
               k: v for k, v in cob.items()
               if k in ('estado', 'erro', 'fonte_sha256', 'setores_carregados')},
           # ponteiro, nao conteudo: embutir o selo aqui obrigaria a regravar o
           # manifest depois de o hash dele entrar no selo, e a cadeia quebra.
           'seal_file': os.path.basename(a.output) + '.seal.json',
           'runtime': _runtime_efetivo(),
           'funil_preparar_base': stats,
           'colunas_vazias_por_dependerem_do_cadastro':
               [c for c in colunas if c.startswith('CAD_') or c == 'ORIGEM_LIGACAO'],
           'aviso': 'IMOVEL_ID vazio: resolucao de entidade e etapa separada.'}

    with qa.publicacao_de_pacote(
            a.output, ['.manifest.json', '.seal.json', '.qa_issues.csv',
                       '.contract.csv', '.crosswalk_canon.csv']) as pk:
        out.to_csv(pk[''], sep=';', index=False, encoding='utf-8-sig')
        cw.to_csv(pk['.crosswalk_canon.csv'], sep=';', index=False,
                  encoding='utf-8-sig')
        with open(pk['.manifest.json'], 'w', encoding='utf-8') as fh:
            json.dump(man, fh, ensure_ascii=False, indent=2, default=str)
        # O CONTRATO viaja com o pacote. `contract_sha256` só é PROVA se o
        # arquivo que ele lacra estiver junto — senão é metadata que ninguém
        # consegue conferir daqui a dois anos.
        import shutil as _sh
        _sh.copy2(a.dicionario, pk['.contract.csv'])

        _res = qa.auditar(
            pk[''], modelo=out, colunas=colunas,
            contrato=qa.contrato_rcc(a.dicionario), manifest=man,
            modo=a.qa_mode, avisos=_runtime_avisos, store=a.store,
            blocos_obrigatorios=(['SET'] if a.qa_mode == 'strict'
                                 and not a.sem_setor else []),
            blocos_falhos=(['SET'] if cob.get('estado') == 'FALHOU' else []))
        _res.issues_csv(pk['.qa_issues.csv'])

        # CADEIA UNIDIRECIONAL: artefato -> manifest -> qa -> SELO.
        # O selo e' o TERMINAL. Antes, o manifest era REGRAVADO com o selo
        # embutido DEPOIS de o manifest_sha256 ser calculado: o selo lacrava o
        # manifest A e o cliente recebia o manifest B. `artifact` e `qa_report`
        # conferiam; `manifest`, nao. Nada entra no selo e e' reescrito depois.
        selo = _res.selo()
        selo.update({'run_id': exec_id, 'run_fingerprint': run_fp,
                     'input_sha256': h_full, 'pipeline_version': VERSAO,
                     'runtime': _runtime_efetivo(),
                     'fonte_set_sha256': _set_sha,
                     'runtime_sha256': _runtime_sha,
                     'distributable': _res.estado in ('SEALED',
                                                      'CORRECTED_AND_SEALED'),
                     'manifest_sha256': qa.sha256_arquivo(pk['.manifest.json']),
                     'contract_file': os.path.basename(a.output) + '.contract.csv',
                     # o hash é do ARQUIVO PUBLICADO, não do original em disco:
                     # é aquele exemplar que o cliente vai conferir
                     'contract_sha256': qa.sha256_arquivo(pk['.contract.csv']),
                     'canon_versao': int(xu.CANON_VERSAO),
                     'crosswalk_sha256': qa.sha256_arquivo(
                         pk['.crosswalk_canon.csv']),
                     'qa_report_sha256': qa.sha256_arquivo(pk['.qa_issues.csv'])})
        with open(pk['.seal.json'], 'w', encoding='utf-8') as fh:
            json.dump(selo, fh, ensure_ascii=False, indent=2, default=str)

        if _res.estado not in ('SEALED', 'CORRECTED_AND_SEALED',
                               'SEALED_WITH_WARNINGS'):
            alvo = qa.quarentenar_bundle(os.path.dirname(pk['']), a.output,
                                         _res, run_id=exec_id)
            print(f'\n  QUALITY_SEAL ...... {_res.estado}')
            for i in _res.fatais[:6]:
                print(f'    {i.regra} {i.campo or i.bloco}: {i.acao}')
            print(f'  quarentena: {alvo}')
            raise SystemExit(3)
    # O léxico só é gravado DEPOIS do lacre: execução que foi para a
    # quarentena não deixa aprendizado atrás de si. Aprender com um artefato
    # reprovado é contaminar a próxima safra com o defeito desta.
    if a.lexico:
        lx.salvar(a.lexico, _lex)

    pendente_qa = selo

    print(f"  linhas ............ {man['linhas']:,}")
    print(f"  observadas ........ {man['observadas']:,}")
    print(f"  inferidas ......... {man['inferidas']:,}")
    print(f"  coletivas ......... {man['grupos_coletivos']:,} grupos / "
          f"{man['unidades_em_coletiva']:,} unidades")
    print(f"  enderecos ......... {man['coletivas_distintas']:,} distintos")
    print(f"  com atividade ..... {man['com_atividade']:,}")
    print(f"  colunas ........... {len(colunas)}")
    print(f'  GATES ............. OK')
    print(f"  QUALITY_SEAL ...... {pendente_qa.get('quality_seal')}")
    print(f"  artifact_sha256 ... {pendente_qa.get('artifact_sha256','')[:16]}...")
    print(f'\n  {a.output}\n  {a.output}.manifest.json')


if __name__ == '__main__':
    main()
