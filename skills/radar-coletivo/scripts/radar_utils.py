#!/usr/bin/env python3
"""
radar_utils.py — Funções utilitárias para o pipeline Radar Coletivo
=====================================================================
Todas testadas com Canoas/RS (176.899 registros CNEFE).
Sem dependência de PostgreSQL — 100% file-based.
"""

import re
import math
import unicodedata
import numpy as np
import pandas as pd

# ╔═══════════════════════════════════════════════════════════════╗
# ║  VERSÃO — FONTE ÚNICA                                        ║
# ╚═══════════════════════════════════════════════════════════════╝
# Existiam três: SKILL.md dizia 4.7.0, o manifest gravava 4.5.3 e o lock se
# anunciava 4.5.2. A versão existe para a forense separar entrega afetada de
# entrega sã — se ela não acompanha o código, a forense não funciona. Quem
# grava versão importa daqui; literal cravada em outro arquivo é defeito, e o
# teste D12 varre por isso.
VERSAO = '4.11.0'


# ╔═══════════════════════════════════════════════════════════════╗
# ║  NORMALIZAÇÃO DE LOGRADOURO                                  ║
# ╚═══════════════════════════════════════════════════════════════╝

def strip_accents(s: str) -> str:
    """Remove acentos de qualquer string."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def norm_logr(s: str) -> str:
    """Normaliza logradouro: remove acento, prefixo, expande abreviações.

    Testado em Canoas — resolve:
      'R. FLORENÇA' → 'FLORENCA'
      'AV. DR. SARMENTO LEITE' → 'DOUTOR SARMENTO LEITE'
      'RUA ARISTIDES GONÇALVES DA SIL' → 'ARISTIDES GONCALVES DA SIL'
    """
    s = strip_accents(str(s).upper().strip())
    # Remover prefixo de tipo de logradouro
    s = re.sub(
        r'^(RUA|R\.|AV\.|AV |AVENIDA|TRAV\.|TRAVESSA|BECO|'
        r'AL\.|ALAMEDA|PC\.|PRACA|EST\.|ESTRADA|ROD\.|RODOVIA|'
        r'LRG\.|LARGO|VL\.|VILA|LOT\.|LOTEAMENTO)\s+', '', s
    )
    # Expandir abreviações de títulos
    s = re.sub(r'^DR\.?\s+', 'DOUTOR ', s)
    s = re.sub(r'^PE\.?\s+', 'PADRE ', s)
    s = re.sub(r'^PROF\.?\s+', 'PROFESSOR ', s)
    s = re.sub(r'^ENG\.?\s+', 'ENGENHEIRO ', s)
    s = re.sub(r'^MAL\.?\s+', 'MARECHAL ', s)
    s = re.sub(r'^GEN\.?\s+', 'GENERAL ', s)
    s = re.sub(r'^PRES\.?\s+', 'PRESIDENTE ', s)
    s = re.sub(r'^GOV\.?\s+', 'GOVERNADOR ', s)
    s = re.sub(r'^DEP\.?\s+', 'DEPUTADO ', s)
    s = re.sub(r'^SEN\.?\s+', 'SENADOR ', s)
    s = re.sub(r'^VER\.?\s+', 'VEREADOR ', s)
    s = re.sub(r'^SGT\.?\s+', 'SARGENTO ', s)
    s = re.sub(r'^CAP\.?\s+', 'CAPITAO ', s)
    s = re.sub(r'^CEL\.?\s+', 'CORONEL ', s)
    s = re.sub(r'^TEN\.?\s+', 'TENENTE ', s)
    return re.sub(r'\s+', ' ', s).strip()


def logr_compat(a: str, b: str) -> tuple:
    """Verifica compatibilidade entre dois logradouros normalizados.

    Retorna: (match: bool, tipo: str)
      EXATO    → strings idênticas após normalização
      PARCIAL  → uma começa com a outra (truncamento)
      CONTEM   → substring significativa (≥6 chars)
      PALAVRAS → 2+ palavras em comum e ≥50% de sobreposição
      DIFERENTE→ sem match
      VAZIO    → um dos dois é vazio

    Testado em Canoas: removeu 12 falsos positivos de esquina
    (ruas diferentes com mesmo NRO dentro de 30m).
    """
    a, b = norm_logr(a), norm_logr(b)
    if not a or not b:
        return False, 'VAZIO'
    if a == b:
        return True, 'EXATO'
    if a.startswith(b) or b.startswith(a):
        return True, 'PARCIAL'
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    if len(shorter) >= 6 and shorter in longer:
        return True, 'CONTEM'
    wa, wb = set(a.split()), set(b.split())
    common = wa & wb
    if len(common) >= 2 and len(common) / max(len(wa), len(wb)) >= 0.5:
        return True, 'PALAVRAS'
    return False, 'DIFERENTE'


# ╔═══════════════════════════════════════════════════════════════╗
# ║  NORMALIZAÇÃO DE COMPLEMENTO (7 CAMADAS SEMÂNTICAS)          ║
# ╚═══════════════════════════════════════════════════════════════╝

LAYER_MAP = {
    # AGRUPAMENTO — empreendimento
    'PREDIO': 'AGRUPAMENTO', 'EDIFICIO': 'AGRUPAMENTO',
    'RESIDENCIAL': 'AGRUPAMENTO', 'QUADRA': 'AGRUPAMENTO',
    'GALERIA': 'AGRUPAMENTO', 'PAVILHAO': 'AGRUPAMENTO',
    'CONJUNTO': 'AGRUPAMENTO', 'GRUPO': 'AGRUPAMENTO', 'ALA': 'AGRUPAMENTO',
    # VIA_INTERNA
    'ALAMEDA INTERNA': 'VIA_INTERNA', 'RUA INTERNA': 'VIA_INTERNA',
    'AVENIDA INTERNA': 'VIA_INTERNA', 'TRAVESSA INTERNA': 'VIA_INTERNA',
    # BLOCO
    'BLOCO': 'BLOCO', 'TORRE': 'BLOCO', 'ENTRADA': 'BLOCO', 'PORTAO': 'BLOCO',
    # PAVIMENTO
    'ANDAR': 'PAVIMENTO', 'TERREO': 'PAVIMENTO', 'SUBSOLO': 'PAVIMENTO',
    'COBERTURA': 'PAVIMENTO', 'SOBRELOJA': 'PAVIMENTO', 'LAJE': 'PAVIMENTO',
    'PAVIMENTO': 'PAVIMENTO',
    # UNIDADE — economia individual
    'APARTAMENTO': 'UNIDADE', 'SALA': 'UNIDADE', 'LOJA': 'UNIDADE',
    'QUITINETE': 'UNIDADE', 'QUARTO': 'UNIDADE', 'SUITE': 'UNIDADE',
    'COMODO': 'UNIDADE', 'BOX': 'UNIDADE', 'BANCA': 'UNIDADE',
    'BARRACA': 'UNIDADE', 'CABINE': 'UNIDADE', 'MODULO': 'UNIDADE',
    'HABITACAO': 'UNIDADE', 'PECA': 'UNIDADE', 'PORTA': 'UNIDADE',
    'DEPOSITO': 'UNIDADE', 'ARMAZEM': 'UNIDADE', 'SEDE': 'UNIDADE',
    'GARAGEM': 'UNIDADE', 'CHALE': 'UNIDADE', 'HANGAR': 'UNIDADE',
    'BARRACAO': 'UNIDADE',
    # POSICAO
    'FUNDOS': 'POSICAO', 'FRENTE': 'POSICAO', 'LADO': 'POSICAO',
    'ANEXO': 'POSICAO', 'DEPENDENCIA': 'POSICAO', 'PORAO': 'POSICAO',
    'ESQ': 'POSICAO', 'DIR': 'POSICAO', 'MEIO': 'POSICAO',
    'LATERAL': 'POSICAO',
    # MORADIA
    'CASA': 'MORADIA', 'LOTE': 'MORADIA', 'SOBRADO': 'MORADIA',
    'SITIO': 'MORADIA', 'CHACARA': 'MORADIA', 'FAZENDA': 'MORADIA',
    'ESTANCIA': 'MORADIA', 'GRANJA': 'MORADIA', 'MANSAO': 'MORADIA',
    'CAIS': 'MORADIA',
}

CAMADAS = ['AGRUPAMENTO', 'VIA_INTERNA', 'BLOCO', 'PAVIMENTO',
           'UNIDADE', 'MORADIA', 'POSICAO']


def normalizar_complemento_cnefe(df: pd.DataFrame) -> pd.DataFrame:
    """Parseia os 5 pares NOM/VAL e reslota por camada semântica.

    Garante que BLOCO A + APTO 101 seja igual independente de estar
    no par 1/2 ou par 3/5. O primeiro match ganha.
    """
    kws_by_layer = {}
    for kw, lay in LAYER_MAP.items():
        kws_by_layer.setdefault(lay, []).append(kw)

    for cam in CAMADAS:
        df[f'{cam}_TIPO'] = None
        df[f'{cam}_VALOR'] = None

    for i in range(1, 6):
        nom_col = f'NOM_COMP_ELEM{i}'
        val_col = f'VAL_COMP_ELEM{i}'
        if nom_col not in df.columns:
            continue
        nom_upper = df[nom_col].fillna('').str.strip().str.upper()
        for cam in CAMADAS:
            mask = nom_upper.isin(kws_by_layer.get(cam, [])) & df[f'{cam}_TIPO'].isna()
            if mask.any():
                df.loc[mask, f'{cam}_TIPO'] = nom_upper[mask]
                df.loc[mask, f'{cam}_VALOR'] = (
                    df.loc[mask, val_col].fillna('').astype(str).str.strip()
                )

    def _build(row):
        parts = []
        for c in CAMADAS:
            t, v = row[f'{c}_TIPO'], row[f'{c}_VALOR']
            if pd.notna(t) and t:
                parts.append(f"{t} {v}".strip() if v else t)
        return ' → '.join(parts)

    df['COMPLEMENTO_NORM'] = df.apply(_build, axis=1)
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  PROMOÇÃO QUADRA/LOTE/CASA                                   ║
# ╚═══════════════════════════════════════════════════════════════╝

def promover_quadra_lote(row) -> tuple:
    """Retorna (NRO_OFICIAL, CHAVE_QUADRA_LOTE, PADRAO_ENDERECO).

    Padrão A: logradouro + número → NRO_OFICIAL = NUM_ENDERECO
    Padrão B: QUADRA [+LOTE] [+CASA] → NRO_OFICIAL = 0, chave Q48/C22 ou Q15/L8/C3

    CORREÇÃO v4.1 (validada em Santa Maria/RS):
    - CASA capturada SEM exigir LOTE (padrão Q+C é comum)
    - LOTE e CASA disputam a camada MORADIA (primeiro match vence);
      o perdedor é recuperado dos pares brutos NOM/VAL_COMP_ELEM
    """
    num = pd.to_numeric(row.get('NUM_ENDERECO'), errors='coerce')
    if pd.notna(num) and num > 0:
        return int(num), None, 'LOGRADOURO'

    quadra = str(row.get('AGRUPAMENTO_VALOR', '') or '').strip() \
             if row.get('AGRUPAMENTO_TIPO') == 'QUADRA' else ''

    # LOTE e CASA: ambos mapeiam para MORADIA — só um vence o slot.
    # Ler o vencedor da camada e recuperar o perdedor dos pares brutos.
    lote, casa = '', ''
    mor_tipo = row.get('MORADIA_TIPO')
    mor_val = str(row.get('MORADIA_VALOR', '') or '').strip()
    if mor_tipo == 'LOTE' and mor_val:
        lote = mor_val
    elif mor_tipo == 'CASA' and mor_val:
        casa = mor_val

    # Recuperar o perdedor do slot MORADIA nos pares brutos
    for i in range(1, 6):
        nom = str(row.get(f'NOM_COMP_ELEM{i}', '') or '').strip().upper()
        val = str(row.get(f'VAL_COMP_ELEM{i}', '') or '').strip()
        if not val or val.lower() == 'nan':
            continue
        if nom == 'LOTE' and not lote:
            lote = val
        elif nom == 'CASA' and not casa:
            casa = val

    if quadra or lote:
        parts = []
        if quadra: parts.append(f'Q{quadra}')
        if lote:   parts.append(f'L{lote}')
        if casa:   parts.append(f'C{casa}')
        return 0, '/'.join(parts), 'QUADRA_LOTE'

    return 0, None, 'SEM_NUMERO'


# ╔═══════════════════════════════════════════════════════════════╗
# ║  HARMONIZAÇÃO DE LOGRADOURO POR CEP                          ║
# ╚═══════════════════════════════════════════════════════════════╝

def harmonizar_logradouro(df: pd.DataFrame) -> pd.DataFrame:
    """Canonicaliza a GRAFIA do logradouro: moda por CEP+TIPO+NOME_NORMALIZADO.

    v4.3 (R7): a moda roda DENTRO do mesmo nome normalizado (sem acento, upper,
    trim) — harmoniza FLORENCA vs FLORENÇA vs florença para a forma mais
    frequente, mas NUNCA funde nomes de rua distintos. Motivo: em cidade de
    CEP ÚNICO (milhares de municípios pequenos têm 1 CEP para a cidade toda),
    a moda por CEP+TIPO puro colapsava TODOS os logradouros no nome modal —
    e, com a CHAVE usando HARM (N2), isso fundiria ruas diferentes no mesmo
    cluster. CEP aqui é só bucket de grafia, jamais decisão de identidade.
    Typo real de 1 letra (FLORENSA) fica para o cruzamento (norm_logr/
    logr_compat) — conservador por design.
    """
    norm = (df['NOM_SEGLOGR'].fillna('').astype(str)
            .map(strip_accents).str.upper().str.strip())
    df['_LOGR_NORM'] = norm
    grp_keys = ['CEP', 'NOM_TIPO_SEGLOGR', '_LOGR_NORM']

    cep_logr = (
        df.groupby(grp_keys)['NOM_SEGLOGR']
        .agg(lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else x.iloc[0])
        .rename('LOGR_MODA').reset_index()
    )
    df = df.merge(cep_logr, on=grp_keys, how='left')
    df['NOM_SEGLOGR_HARM'] = df['LOGR_MODA'].fillna(df['NOM_SEGLOGR'])

    # Título (PRESIDENTE vs PRES.) — mas por ABREVIAÇÃO, nunca por moda.
    #
    # A moda escolhia UM título e apagava o outro: `BARAO` e `BARONESA` do
    # mesmo nome de rua viravam o mesmo título, e com isso 'RUA BARAO DO
    # GRAVATAI' e 'RUA BARONESA DO GRAVATAI' — duas ruas de Porto Alegre —
    # colapsavam. Uniformizar por frequência destrói distinção real.
    #
    # A união agora exige PARENTESCO: um título só é absorvido por outro se
    # for PREFIXO dele (`PRES` ⊂ `PRESIDENTE`, abreviação) ou tiver a mesma
    # chave fonética. `BARAO` não é prefixo de `BARONESA`, `PAI` não é de
    # `SAO` — esses ficam distintos, que é o que são.
    if 'NOM_TITULO_SEGLOGR' in df.columns:
        _t0 = (df['NOM_TITULO_SEGLOGR'].fillna('').astype(str)
               .map(strip_accents).str.upper().str.strip())
        _gk = (df['CEP'].astype(str) + '|'
               + df['NOM_TIPO_SEGLOGR'].fillna('').astype(str) + '|' + norm)
        _pares = pd.DataFrame({'_g': _gk, '_t': _t0})
        _pares = _pares[_pares['_t'].ne('')].drop_duplicates()
        _canon_tit = {}
        for _g, _sub in _pares.groupby('_g', sort=False):
            _vals = sorted(set(_sub['_t']), key=len, reverse=True)
            for _v in _vals:
                _alvo = next((w for w in _vals
                              if w != _v and w.startswith(_v)), None)
                if _alvo is None:
                    _alvo = next((w for w in _vals if w != _v
                                  and fonetica_token(w) == fonetica_token(_v)
                                  and len(w) > len(_v)), None)
                _canon_tit[(_g, _v)] = _alvo or _v
        df['NOM_TITULO_HARM'] = [
            _canon_tit.get((g, t), t) for g, t in zip(_gk, _t0)]
        # OMISSÃO ≠ VIA DIFERENTE. Quando o município registra UM ÚNICO título
        # para aquele tipo+nome, a linha sem título é omissão do recenseador.
        # Levar o título para a identidade sem isto partiria 12 ruas de Porto
        # Alegre em duas ('RUA RAUL MOREIRA' × 'RUA DOUTOR RAUL MOREIRA').
        #
        # Com DOIS títulos distintos não se preenche NADA: aí são vias
        # diferentes de verdade — 'RUA BARAO DO GRAVATAI' e 'RUA BARONESA DO
        # GRAVATAI' existem as duas, e cada uma fica com o seu.
        _mun_h = (df['COD_MUNICIPIO'].astype(str)
                  if 'COD_MUNICIPIO' in df.columns
                  else pd.Series('0', index=df.index))
        _k = (_mun_h + '|'
              + df['NOM_TIPO_SEGLOGR'].fillna('').astype(str) + '|'
              + df['NOM_SEGLOGR_HARM'].fillna('').astype(str)
              .map(strip_accents).str.upper().str.strip())
        _t = df['NOM_TITULO_HARM'].fillna('').astype(str).str.strip()
        _nv = pd.DataFrame({'_k': _k, '_t': _t})
        _nv = _nv[_nv['_t'].ne('')].drop_duplicates()
        _cont = _nv.groupby('_k')['_t'].nunique()
        _unico = _nv[_nv['_k'].isin(_cont[_cont == 1].index)] \
            .drop_duplicates('_k').set_index('_k')['_t']
        _falta = _t.eq('')
        if _falta.any() and len(_unico):
            df.loc[_falta, 'NOM_TITULO_HARM'] = (
                _k[_falta].map(_unico).fillna(''))

    df.drop(columns=['LOGR_MODA', 'TITULO_MODA', '_LOGR_NORM'],
            inplace=True, errors='ignore')
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  SANIDADE GEOESPACIAL                                        ║
# ╚═══════════════════════════════════════════════════════════════╝

SANIDADE_RAIO_GROSSEIRO_KM = 300.0   # distância à mediana da partição (ajustável)
BBOX_BRASIL_LAT = (-34.1, 5.6)       # Chuí .. Monte Caburaí
BBOX_BRASIL_LON = (-74.1, -28.5)     # Serra do Divisor .. Trindade/M.Vaz


def sanidade_geo(df: pd.DataFrame, pct: float = 0.001,
                 particao: str = 'COD_MUNICIPIO') -> pd.DataFrame:
    """Sanidade geoespacial em 3 CAMADAS (v4.5, revisão externa P0).

    Um quantil não identifica erro — apenas define que haverá extremos; por
    construção ele recortava ~4·pct de registros LEGÍTIMOS de borda. Agora:

    CAMADA 1 — determinística (ERRO_GEOMETRIA, exclui do gate):
      coordenada nula; fora do bbox Brasil; (0,0); lat/lon possivelmente
      trocadas (ponto entra no bbox após swap).
    CAMADA 2 — erro grosseiro (ERRO_GEOMETRIA, exclui): haversine à MEDIANA
      da partição > SANIDADE_RAIO_GROSSEIRO_KM — typo de grau, hemisfério,
      município errado. Vale para TODAS as partições.
    CAMADA 3 — estatística (FLAG_OUTLIER_GEO=1, INFORMATIVA, não exclui):
      cauda percentil da partição (só onde n >= max(20, 1/pct)). Insumo de
      revisão/priorização, nunca gate.
    ROADMAP: camada territorial (ST_Covers na malha municipal + buffer).
    Registros ERRO_GEOMETRIA: marcados, NÃO removidos da base.
    """
    lat = pd.to_numeric(df['LATITUDE'], errors='coerce')
    lon = pd.to_numeric(df['LONGITUDE'], errors='coerce')
    tem = lat.notna() & lon.notna()

    # ── Camada 1: determinística
    dentro_br = lat.between(*BBOX_BRASIL_LAT) & lon.between(*BBOX_BRASIL_LON)
    zero = (lat == 0) & (lon == 0)
    swap = ((~dentro_br) & lon.between(*BBOX_BRASIL_LAT)
            & lat.between(*BBOX_BRASIL_LON))          # entraria no BR se trocasse
    erro = (~tem) | (~dentro_br) | zero | swap

    # ── Camada 2: grosseiro por mediana da partição (ou global)
    usar_part = particao and particao in df.columns
    base_med = tem & ~erro
    if base_med.any():
        if usar_part:
            med = (df.loc[base_med, [particao]]
                     .assign(_la=lat[base_med], _lo=lon[base_med])
                     .groupby(particao)[['_la', '_lo']].median())
            mlat = df[particao].map(med['_la']).astype(float)
            mlon = df[particao].map(med['_lo']).astype(float)
        else:
            mlat = pd.Series(lat[base_med].median(), index=df.index)
            mlon = pd.Series(lon[base_med].median(), index=df.index)
        rl, rm = np.radians(lat.astype(float)), np.radians(mlat)
        dphi = rl - rm
        dlmb = np.radians(lon.astype(float)) - np.radians(mlon)
        h = np.sin(dphi / 2) ** 2 + np.cos(rm) * np.cos(rl) * np.sin(dlmb / 2) ** 2
        dist_km = pd.Series(2 * 6371.0 * np.arcsin(np.sqrt(np.clip(h, 0, 1))),
                            index=df.index)
        erro = erro | (tem & dist_km.notna()
                       & (dist_km > SANIDADE_RAIO_GROSSEIRO_KM))

    df['SANIDADE_GEO'] = np.where(~erro, 'OK', 'ERRO_GEOMETRIA')

    # ── Camada 3: percentil vira FLAG informativa (nunca exclusão)
    df['FLAG_OUTLIER_GEO'] = 0
    okm = df['SANIDADE_GEO'] == 'OK'
    if okm.any():
        n_min = max(20, int(np.ceil(1.0 / max(pct, 1e-9))))
        if usar_part:
            v = (df.loc[okm, [particao]]
                   .assign(_la=lat[okm], _lo=lon[okm]))
            g = v.groupby(particao)
            n_part = df[particao].map(g.size()).fillna(0)
            lo_q = g[['_la', '_lo']].quantile(pct)
            hi_q = g[['_la', '_lo']].quantile(1 - pct)
            fora = (~lat.between(df[particao].map(lo_q['_la']),
                                 df[particao].map(hi_q['_la']))
                    | ~lon.between(df[particao].map(lo_q['_lo']),
                                   df[particao].map(hi_q['_lo'])))
            df.loc[okm & fora & (n_part >= n_min), 'FLAG_OUTLIER_GEO'] = 1
        else:
            cv_la, cv_lo = lat[okm], lon[okm]
            if len(cv_la) >= max(20, int(np.ceil(1.0 / max(pct, 1e-9)))):
                fora = (~lat.between(cv_la.quantile(pct), cv_la.quantile(1 - pct))
                        | ~lon.between(cv_lo.quantile(pct), cv_lo.quantile(1 - pct)))
                df.loc[okm & fora, 'FLAG_OUTLIER_GEO'] = 1
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  DETECÇÃO DE FRAUDE DE RECENSEADOR                           ║
# ╚═══════════════════════════════════════════════════════════════╝

# Termos genéricos que indicam preenchimento automatizado
ESTAB_GENERICOS = {
    'DOMICILIO PROVISORIO', 'CASA', 'RESIDENCIA', 'NI',
    'SEM DENOMINACAO', 'SEM IDENTIFICACAO', 'SEM NOME',
    'NAO INFORMADO', 'DESCONHECIDO', 'OUTROS', 'VAZIO',
}


def detectar_anomalia_coleta(df: pd.DataFrame,
                    seq_min: int = 15,
                    cobertura_min: float = 0.95) -> pd.DataFrame:
    """Detecção de fraude de recenseador — 5 sinais. 100% VETORIZADO.

    v4.1: loop por groupby substituído por agregações vetorizadas
    (o loop original estourava memória em bases >100K registros).
    Executa ANTES de dedupe e tratamento semântico.
    """
    df['FLAG_ANOMALIA_COLETA'] = 0
    df['ANOMALIA_MOTIVOS'] = ''

    # ── SINAL 1: mesma coordenada (round 5) com 3+ logradouros distintos
    # v4.5 (revisão externa): coordenadas NULAS ficam FORA do hash — antes
    # todos os sem-coordenada caíam no grupo 'nan|nan' e se flagravam
    # mutuamente de forma artificial.
    _lat_s1 = pd.to_numeric(df['LATITUDE'], errors='coerce')
    _lon_s1 = pd.to_numeric(df['LONGITUDE'], errors='coerce')
    _coord_ok = _lat_s1.notna() & _lon_s1.notna()
    coord_hash = (_lat_s1.round(5).astype(str) + '|' + _lon_s1.round(5).astype(str))
    coord_logr = (df[_coord_ok].groupby(coord_hash[_coord_ok])['NOM_SEGLOGR']
                  .transform('nunique').reindex(df.index))
    mask_s1 = _coord_ok & (coord_logr >= 3)
    df.loc[mask_s1, 'FLAG_ANOMALIA_COLETA'] = 1
    df.loc[mask_s1, 'ANOMALIA_MOTIVOS'] += 'COORD_MULTI_LOGR|'

    # ── SINAL 2: sequência perfeita — VETORIZADO
    if 'CHAVE' in df.columns and 'UNIDADE_VALOR' in df.columns:
        # Dois regimes de endereçamento, como em detectar_condominio_horizontal:
        # exigir NUM>0 excluía exatamente as linhas Q/L, e a calibração especial
        # de Q/L logo abaixo (nun>=30, cobertura>0.98) nunca chegava a ser
        # aplicada em execução canônica — regra anunciada, testada isolada,
        # ausente do fluxo real.
        _num = pd.to_numeric(df['NUM_ENDERECO'], errors='coerce').fillna(0)
        _eleg = (_num > 0) | (df.get('PADRAO_ENDERECO',
                                     pd.Series('', index=df.index)) == 'QUADRA_LOTE')
        sub = df[_eleg].copy()
        sub['_UN'] = num_unidade(sub['UNIDADE_VALOR'])
        sub = sub[sub['_UN'].notna()]
        if len(sub) > 0:
            agg = sub.groupby('CHAVE')['_UN'].agg(
                n='count', nun='nunique', mn='min', mx='max'
            )
            agg['esperadas'] = (agg['mx'] - agg['mn'] + 1).clip(lower=1)
            agg['cobertura'] = agg['nun'] / agg['esperadas']

            # Threshold padrão vs Q/L (mais permissivo)
            if 'PADRAO_ENDERECO' in df.columns:
                chaves_ql = set(
                    df.loc[df['PADRAO_ENDERECO'] == 'QUADRA_LOTE', 'CHAVE']
                )
            else:
                chaves_ql = set()

            # v4.5.1 (2ª revisão, bug objetivo): threshold sobre UNIDADES
            # DISTINTAS (nun), não linhas (n) — 8 unidades×2 registros = 16
            # linhas NÃO é "16 unidades em sequência".
            mask_padrao = (
                (agg['nun'] >= seq_min) & (agg['cobertura'] > cobertura_min) &
                (~agg.index.isin(chaves_ql))
            )
            mask_ql = (
                (agg['nun'] >= max(seq_min, 30)) &
                (agg['cobertura'] > max(cobertura_min, 0.98)) &
                (agg.index.isin(chaves_ql))
            )
            chaves_fraude = agg.index[mask_padrao | mask_ql]
            mask_s2 = df['CHAVE'].isin(chaves_fraude) & _eleg
            df.loc[mask_s2, 'FLAG_ANOMALIA_COLETA'] = 1
            df.loc[mask_s2, 'ANOMALIA_MOTIVOS'] += 'SEQ_PERFEITA|'

    # ── SINAL 3: estabelecimento repetido — VETORIZADO (merge, não loop)
    if 'DSC_ESTABELECIMENTO' in df.columns and 'CHAVE' in df.columns:
        estab_upper = df['DSC_ESTABELECIMENTO'].fillna('').str.upper().str.strip()
        mask_tem_estab = df['DSC_ESTABELECIMENTO'].notna() & (estab_upper != '')
        mask_generico = estab_upper.isin(ESTAB_GENERICOS)

        # Nomes específicos repetidos 5+ no mesmo endereço
        grp_espec = df[mask_tem_estab & ~mask_generico].groupby(
            ['CHAVE', 'DSC_ESTABELECIMENTO']
        )['COD_UNICO_ENDERECO'].transform('count') \
            if (mask_tem_estab & ~mask_generico).any() else pd.Series(dtype=int)
        if len(grp_espec) > 0:
            idx_s3 = grp_espec[grp_espec >= 5].index
            df.loc[idx_s3, 'FLAG_ANOMALIA_COLETA'] = 1
            df.loc[idx_s3, 'ANOMALIA_MOTIVOS'] += 'ESTAB_REPETIDO|'

        # Genéricos repetidos 10+ no mesmo endereço
        grp_gen = df[mask_generico].groupby(
            ['CHAVE', 'DSC_ESTABELECIMENTO']
        )['COD_UNICO_ENDERECO'].transform('count') \
            if mask_generico.any() else pd.Series(dtype=int)
        if len(grp_gen) > 0:
            idx_gen = grp_gen[grp_gen >= 10].index
            df.loc[idx_gen, 'FLAG_ANOMALIA_COLETA'] = 1
            df.loc[idx_gen, 'ANOMALIA_MOTIVOS'] += 'GENERICO_MASSA|'

    # ── SINAL 4: setor censitário 100% encontrado — potencializador
    if 'COD_SETOR' in df.columns and 'COD_INDICADOR_ESTAB_ENDERECO' in df.columns:
        ind = pd.to_numeric(df['COD_INDICADOR_ESTAB_ENDERECO'], errors='coerce')
        s = df.assign(_IND=ind).groupby('COD_SETOR').agg(
            total=('COD_SETOR', 'count'),
            pct_ok=('_IND', lambda x: (x == 1).mean())
        )
        perf = s[(s.pct_ok >= 0.99) & (s.total >= 100)].index
        mask_s4 = df['COD_SETOR'].isin(perf)
        df.loc[mask_s4, 'ANOMALIA_MOTIVOS'] += 'SETOR_SEM_RECUSA|'

    df['ANOMALIA_MOTIVOS'] = df['ANOMALIA_MOTIVOS'].str.rstrip('|')
    _decompor_sinais_anomalia(df)
    return df


def _decompor_sinais_anomalia(df: pd.DataFrame) -> None:
    """v4.5.1 — os sinais NÃO são todos independentes: S1/S2/S3 são
    primários; S4 é contexto de setor; S5 é reforço condicionado a S1/S3.
    A graduação futura (1 observação / 2 quarentena / 3 exclusão) deve usar
    N_SINAIS_PRIMARIOS — nunca o total cru — senão S1+S5 contaria como
    duas evidências independentes."""
    mot = df['ANOMALIA_MOTIVOS'].fillna('')
    df['N_SINAIS_PRIMARIOS'] = (
        mot.str.contains('COORD_MULTI_LOGR').astype(int)
        + mot.str.contains('SEQ_PERFEITA').astype(int)
        + mot.str.contains('ESTAB_REPETIDO|GENERICO_MASSA', regex=True).astype(int))
    df['FLAG_CONTEXTO_S4'] = mot.str.contains('SETOR_SEM_RECUSA').astype(int)
    df['FLAG_REFORCO_S5'] = mot.str.contains('NV_RUIM_COMPL_RICO').astype(int)
    # total cru mantido por compatibilidade (contém contexto+reforço)
    df['N_SINAIS_ANOMALIA'] = np.where(
        mot == '', 0, mot.str.count(r'\|') + 1)


def detectar_anomalia_pos_t1(df: pd.DataFrame) -> pd.DataFrame:
    """Sinal 5 — pós-T1: NV ruim + complemento rico.

    Só eleva FLAG se co-ocorrer com S1 ou S3.
    Áreas rurais/periferias têm NV alto legitimamente.
    """
    mask_nv_ruim = df['NV_GEO_COORD'].isin([4, 5, 6])
    mask_compl_rico = (df['BLOCO_TIPO'].notna() &
                       df['UNIDADE_TIPO'].notna() &
                       df['PAVIMENTO_TIPO'].notna())
    mask_s5 = mask_nv_ruim & mask_compl_rico

    df.loc[mask_s5, 'ANOMALIA_MOTIVOS'] = (
        df.loc[mask_s5, 'ANOMALIA_MOTIVOS'].fillna('').str.rstrip('|') +
        '|NV_RUIM_COMPL_RICO'
    ).str.lstrip('|')

    # Só eleva flag se S1 ou S3 já presentes
    mask_co = (
        mask_s5 &
        df['ANOMALIA_MOTIVOS'].str.contains(
            'COORD_MULTI_LOGR|ESTAB_REPETIDO|GENERICO_MASSA', na=False
        )
    )
    df.loc[mask_co, 'FLAG_ANOMALIA_COLETA'] = 1
    if 'ANOMALIA_MOTIVOS' in df.columns:      # recompor decomposição após S5
        _decompor_sinais_anomalia(df)
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  GAP ANALYSIS                                                ║
# ╚═══════════════════════════════════════════════════════════════╝

TERREO_ALIASES = {'TERREO', 'T0', 'T1', 'TER', 'T', '0', 'LOJA', 'G', 'SS'}
GAP_MAX_RATIO = 3.0


def normalizar_andar(valor) -> int:
    """TERREO e aliases → 0. Demais → int do primeiro grupo numérico."""
    v = str(valor).upper().strip()
    if v in TERREO_ALIASES or 'TERREO' in v:
        return 0
    m = re.search(r'\d+', v)
    return int(m.group()) if m else None


def num_unidade_ocupada(serie: pd.Series) -> pd.Series:
    """Prefixo numérico de QUALQUER forma de valor de unidade (R11).

    Contraparte permissiva de `num_unidade()`. As duas existem porque cumprem
    papéis OPOSTOS e confundi-las fabrica duplicata:

      num_unidade()          INCLUSÃO — o que pode ENTRAR na grade. Estrita:
                             só inteiro puro, senão "713E714" vira +inf.
      num_unidade_ocupada()  EXCLUSÃO — o que já OCUPA o lugar. Permissiva:
                             "303 SINDICO", "101 FUNDOS", "0203-A" ocupam o
                             303, o 101 e o 203.

    O defeito que criou esta função (achado em campo, COL-4314902-001003):
    `303 SINDICO` era rejeitado pelo parse estrito, saía da grade e voltava
    como lacuna SUSTENTADA — ordem de serviço para conferir um apartamento
    que está no arquivo. Em Porto Alegre eram 826 hipóteses, 461 delas no
    grau que vai direto a campo.

    Regra geral: o conjunto que CONSTRÓI e o conjunto que SUPRIME nunca podem
    ser o mesmo. Inclusão estrita, exclusão permissiva.
    """
    s = serie.fillna('').astype(str).str.strip()
    return pd.to_numeric(s.str.extract(r'^0*(\d{1,9})')[0], errors='coerce')


def _sem_ocupadas(hips, ocupadas):
    """Remove hipóteses cujo valor já está ocupado por alguma unidade real."""
    if not ocupadas:
        return hips
    fora = []
    for h in hips:
        v = h['VALOR']
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            fora.append(h); continue
        if n not in ocupadas:
            fora.append(h)
    return fora


def gap_linear(valores: list) -> list:
    """Gap em sequência numérica linear. CASA 1,2,5 → falta 3,4.
    Trava: se ausentes > GAP_MAX_RATIO × presentes → descarta.
    """
    if len(valores) < 2:
        return []
    seq = sorted(set(valores))
    ausentes = [v for v in range(seq[0], seq[-1] + 1) if v not in seq]
    if len(ausentes) > len(seq) * GAP_MAX_RATIO:
        return []
    return ausentes


def gap_alfa(valores: list) -> list:
    """Gap alfanumérico. BLOCO A,B,D → falta C."""
    chars = [v for v in valores if len(str(v)) == 1 and str(v).isalpha()]
    if len(chars) < 2:
        return []
    ords = sorted(ord(v.upper()) for v in chars)
    ausentes = [chr(v) for v in range(ords[0], ords[-1] + 1) if v not in ords]
    if len(ausentes) > len(chars) * 2:
        return []
    return ausentes


# ╔═══════════════════════════════════════════════════════════════╗
# ║  R9 — GRADUAÇÃO DA INFERÊNCIA (v4.7)                          ║
# ╚═══════════════════════════════════════════════════════════════╝
#
# Toda hipótese nasce classificada por UM critério único e verificável:
# por quantos lados a OBSERVAÇÃO cerca a hipótese.
#
#   SUSTENTADA    cercada dos dois lados      → vai a campo
#   PLAUSIVEL     cercada de um lado, perto   → amostra dirigida
#   ESPECULATIVA  extrapolação livre          → fica na base, não vai a campo
#
# Nenhuma hipótese é DESCARTADA. Descartar deixa um quadrante vazio na
# matriz de confusão: o campo só consegue devolver "fui e não existe"
# (falso positivo) e nunca "a régua matou e existia" (falso negativo) —
# e sem esse quadrante o limiar nunca calibra. Graduar preserva a medição.

GRAUS_INFERENCIA = ('SUSTENTADA', 'PLAUSIVEL', 'ESPECULATIVA')

DESTINO_POR_GRAU = {
    'SUSTENTADA':   'ENVIAR',
    'PLAUSIVEL':    'AMOSTRAR',
    'ESPECULATIVA': 'RETER',
}

# Andar ausente só é hipótese se a lacuna vertical for pequena perto do
# observado. 1 andar faltando entre dois presentes é sinal; 8 andares
# faltando é prédio mal recenseado — e aí a resposta certa é marcar o
# grupo como suspeito de coleta, não fabricar 200 apartamentos.
B_TETO_ANDARES_AUSENTES = 0.30

# Distância (em passos de unidade) até onde a extrapolação ainda merece
# amostra de campo. Acima disso fica registrada mas não sai para rota.
C_DIST_MAX_PLAUSIVEL = 2


def _hip(valor, classe, grau, evidencia, distancia=None):
    """Hipótese graduada — formato único de todas as regras de gap."""
    return {'VALOR': valor, 'EVD_CLASSE': classe, 'EVD_GRAU': grau,
            'EVD_DISTANCIA': distancia, 'EVD_DESCRICAO': evidencia}


def gap_grade(valores: list) -> list:
    """Gap em grade apto/andar, GRADUADO (v4.7). Retorna hipóteses, não ints.

    Três classes, ordenadas pela força da evidência:

      A_INTERIOR_FAIXA  interior ao intervalo observado DO PRÓPRIO andar.
                        andar 2 tem 201,202,204 → 203 é interpolação pura.

      B_ANDAR_AUSENTE   andar INTEIRO ausente, cercado por andares presentes
                        acima e abaixo. Era ponto cego: a versão anterior
                        montava a grade como produto cartesiano dos andares
                        OBSERVADOS, então um andar sem nenhum rastro era
                        invisível. Prédio não pula andar — é a hipótese mais
                        forte do conjunto. Guardas obrigatórias: unidades do
                        andar ausente = INTERSEÇÃO dos dois vizinhos (nunca a
                        união global) e teto de B_TETO_ANDARES_AUSENTES.

      C_EXTRAPOLADA     além do intervalo observado do andar. 101-109 não
                        implica 209: o andar 2 pode terminar no 208. Registra
                        a DISTÂNCIA para permitir recalibrar o corte depois
                        com um UPDATE, sem re-rodar a safra.
    """
    if len(valores) < 3:
        return []
    obs = set(valores)
    andares = sorted({v // 100 for v in obs})
    unidades = sorted({v % 100 for v in obs if v % 100 != 0})
    if not unidades:
        return []
    por_andar = {a: sorted({v % 100 for v in obs if v // 100 == a}) for a in andares}

    hips = []

    # — A e C: dentro dos andares observados —
    grade = [a * 100 + u for a in andares for u in unidades]
    ausentes = [v for v in grade if v not in obs]
    if len(ausentes) <= len(obs) * GAP_MAX_RATIO:
        for m in ausentes:
            a, u = m // 100, m % 100
            oa = por_andar[a]
            faixa = f'andar {a}: observado {oa[0]:02d}-{oa[-1]:02d}'
            if oa and oa[0] < u < oa[-1]:
                hips.append(_hip(m, 'A_INTERIOR_FAIXA', 'SUSTENTADA', faixa))
            else:
                dist = u - oa[-1] if u > oa[-1] else oa[0] - u
                grau = ('PLAUSIVEL' if dist <= C_DIST_MAX_PLAUSIVEL
                        else 'ESPECULATIVA')
                hips.append(_hip(m, 'C_EXTRAPOLADA', grau,
                                 f'{faixa}; hipótese {dist} passo(s) fora',
                                 int(dist)))

    # — B: andar intermediário sem nenhum rastro —
    aus_andares = [a for a in range(andares[0], andares[-1] + 1)
                   if a not in por_andar and a > 0]
    # teto relativo, com piso de 1: UM andar faltando cercado por dois
    # presentes é sempre admissível, mesmo num prédio de 2 andares.
    teto_b = max(1, int(B_TETO_ANDARES_AUSENTES * len(andares)))
    if aus_andares and len(aus_andares) <= teto_b:
        for a in aus_andares:
            if (a - 1) not in por_andar or (a + 1) not in por_andar:
                continue                       # sem os dois vizinhos, não cerca
            inter = sorted(set(por_andar[a - 1]) & set(por_andar[a + 1]))
            for u in inter:
                hips.append(_hip(
                    a * 100 + u, 'B_ANDAR_AUSENTE', 'SUSTENTADA',
                    f'andar {a} sem nenhum registro; andares {a-1} e {a+1} '
                    f'presentes e ambos com unidade {u:02d}'))
    return hips


PARES_POSICIONAIS = [
    ({'FRENTE', 'FRT'}, {'FUNDOS', 'FDS'}),
    ({'LADO DIREITO', 'DIR'}, {'LADO ESQUERDO', 'ESQ'}),
]


def gap_posicional(posicoes_por_unidade, tem_outro_descritor=None) -> list:
    """Polo posicional ausente, GRADUADO por existência de contraparte (v4.7).

    FRENTE/FUNDOS é par CONTRASTIVO: o rótulo carrega informação por
    oposição. Se algum irmão no endereço já pode exercer a oposição, a
    oposição está satisfeita e NÃO há lacuna. A versão anterior recebia
    apenas um `set` de tokens e era cega a isso — `{FRENTE}` num endereço
    com 7 casas numeradas e `{FRENTE}` num endereço de 1 unidade produziam
    a mesma hipótese.

    Três situações, medidas em POA sobre 17.277 endereços:

      SATURADO (38%)      toda unidade carrega o mesmo polo; ninguém pode ser
                          a contraparte → SUSTENTADA. Caso mais forte: uma
                          única unidade rotulada FUNDOS significa que a
                          construção da frente não virou endereço nenhum.

      ENUMERADO (14%)     o polo convive com irmãos de outro descritor
                          (CASA 1, CASA 2...). O rótulo é discriminante
                          dentro de uma enumeração, não metade de um par
                          → PLAUSIVEL.

      IMPLÍCITO (47%)     existe irmão SEM descritor algum: ele É a frente,
                          só não foi rotulada (ninguém escreve "frente" no
                          endereço principal). O par já está completo
                          → ESPECULATIVA.

    posicoes_por_unidade : lista, uma entrada por unidade do endereço, com o
                           conjunto de tokens posicionais daquela unidade.
    tem_outro_descritor  : lista booleana paralela — a unidade traz algum
                           outro descritor (UNIDADE/MORADIA numerada etc.).
                           None ⇒ trata todas como sem descritor.
    """
    unidades = [{str(p).upper() for p in s} for s in posicoes_por_unidade]
    n = len(unidades)
    if n == 0:
        return []
    outros = list(tem_outro_descritor) if tem_outro_descritor is not None else [False] * n

    hips = []
    for lado_a, lado_b in PARES_POSICIONAIS:
        tem_a = any(u & lado_a for u in unidades)
        tem_b = any(u & lado_b for u in unidades)
        if tem_a == tem_b:
            continue                                   # par completo ou ausente
        presente, ausente = (lado_a, lado_b) if tem_a else (lado_b, lado_a)
        rotulo = max(sorted(ausente), key=len)          # forma canônica
        pres_rot = max(sorted(presente), key=len)

        idx_contraparte = [i for i, u in enumerate(unidades) if not (u & presente)]
        if not idx_contraparte:
            hips.append(_hip(rotulo, 'D_POSICIONAL_SATURADO', 'SUSTENTADA',
                             f'{n} unidade(s), todas {pres_rot}; '
                             f'nenhum irmão pode ser {rotulo}'))
            continue
        sem_desc = [i for i in idx_contraparte if not outros[i]]
        if sem_desc:
            hips.append(_hip(rotulo, 'F_POSICIONAL_IMPLICITO', 'ESPECULATIVA',
                             f'{len(sem_desc)} irmão(s) sem descritor — '
                             f'{rotulo} implícita, par já completo'))
        else:
            hips.append(_hip(rotulo, 'E_POSICIONAL_ENUMERADO', 'PLAUSIVEL',
                             f'{len(idx_contraparte)} irmão(s) com outro '
                             f'descritor — {pres_rot} discrimina enumeração'))
    return hips


def gap_quadra_lote(registros: list) -> dict:
    """Gap em 3 níveis: entre quadras, lotes dentro de Q, casas dentro de L.

    registros: lista de dicts com keys 'quadra', 'lote', 'casa' (strings numéricas).
    """
    gaps = {}

    # Nível 1: entre quadras
    quadras = sorted(set(int(r['quadra']) for r in registros
                         if r.get('quadra', '').isdigit()))
    g = gap_linear(quadras)
    if g:
        gaps['QUADRA'] = g

    # Nível 2: lotes dentro de cada quadra
    from itertools import groupby as igroupby
    sorted_r = sorted(registros, key=lambda r: r.get('quadra', ''))
    for q, group in igroupby(sorted_r, key=lambda r: r.get('quadra', '')):
        lotes = sorted(int(r['lote']) for r in group
                        if r.get('lote', '').isdigit())
        g = gap_linear(lotes)
        if g:
            gaps[f'Q{q}_LOTES'] = g

    # Nível 3: casas dentro de cada lote
    for (q, l), group in igroupby(
        sorted(registros, key=lambda r: (r.get('quadra', ''), r.get('lote', ''))),
        key=lambda r: (r.get('quadra', ''), r.get('lote', ''))
    ):
        casas = sorted(int(r['casa']) for r in group
                        if r.get('casa', '').isdigit())
        g = gap_linear(casas)
        if g:
            gaps[f'Q{q}/L{l}_CASAS'] = g

    return gaps


def classificar_gap_confianca(gap_tipo: str, presentes: int,
                               ausentes: int) -> str:
    """Thresholds validados em Canoas.

    ALTA:  posicional, gap≤2 em sequência curta, alfa gap=1
    MEDIA: grade com padrão, gap 3-5 numérico, Q/L gap 1-2
    BAIXA: gap grande, 1 presente, numeração irregular
    """
    if gap_tipo == 'POSICIONAL':
        return 'ALTA'
    if gap_tipo == 'ALFANUMERICO' and ausentes == 1:
        return 'ALTA'
    if gap_tipo in ('SEQUENCIA', 'ALFANUMERICO') and ausentes <= 2 and presentes <= 5:
        return 'ALTA'
    if gap_tipo == 'GRADE_APTO':
        return 'MEDIA'
    if gap_tipo in ('SEQUENCIA', 'QUADRA_LOTE') and ausentes <= 5:
        return 'MEDIA'
    if presentes <= 1:
        return 'BAIXA'
    return 'BAIXA'


# ╔═══════════════════════════════════════════════════════════════╗
# ║  FACHADA ATIVA + USO MISTO                                   ║
# ╚═══════════════════════════════════════════════════════════════╝

TERREO_PARA_FACHADA = {'', 'TERREO', '0', 'T', 'LOJA', 'G', 'SOBRELOJA'}

SETORES_COMERCIAIS_TERREO = {
    'COMERCIO_VAREJO', 'ALIMENTACAO', 'BELEZA_ESTETICA',
    'SERVICO_PROF', 'SERVICO_AUTO', 'SAUDE', 'HOSPEDAGEM',
    'COMERCIO_SERVICO', 'MANUFATURA', 'INDUSTRIAL',
}

# COD_ESPECIE que indicam uso não-residencial
ESPECIE_NAO_RES = {3, 4, 5, 6, 8}  # agrop, ensino, saúde, outras, religioso


def verificar_fachada_ativa(grupo: pd.DataFrame) -> int:
    """Verifica se endereço tem comércio no térreo + residencial superior.

    Funciona em dois modos:
    1. Com SETOR_ATIVIDADE (pós-T2) — prioridade
    2. Com COD_ESPECIE puro (fallback quando T2 não classificou)
    """
    # Verificar comércio no térreo
    pav_vals = grupo['PAVIMENTO_VALOR'].fillna('').str.upper().str.strip()
    mask_terreo = pav_vals.isin(TERREO_PARA_FACHADA)

    # Modo 1: SETOR_ATIVIDADE disponível
    if 'SETOR_ATIVIDADE' in grupo.columns:
        tem_com_terreo = (
            mask_terreo & grupo['SETOR_ATIVIDADE'].isin(SETORES_COMERCIAIS_TERREO)
        ).any()
        tem_res_superior = (
            ~mask_terreo & (grupo['SETOR_ATIVIDADE'] == 'RESIDENCIAL')
        ).any()
    else:
        # Modo 2: fallback por COD_ESPECIE
        tem_com_terreo = (
            mask_terreo & grupo['COD_ESPECIE'].isin(ESPECIE_NAO_RES)
        ).any()
        tem_res_superior = (
            ~mask_terreo & (grupo['COD_ESPECIE'] == 1)
        ).any()

    return int(tem_com_terreo and tem_res_superior)


def verificar_uso_misto(grupo: pd.DataFrame) -> int:
    """Residencial E não-residencial no mesmo endereço."""
    especies = set(grupo['COD_ESPECIE'].dropna().astype(int))
    tem_res = bool(especies & {1, 2})
    tem_nres = bool(especies & ESPECIE_NAO_RES)
    return int(tem_res and tem_nres)


# ╔═══════════════════════════════════════════════════════════════╗
# ║  CONFIABILIDADE 3 EIXOS                                      ║
# ╚═══════════════════════════════════════════════════════════════╝

def calcular_confiabilidade(row, is_inferido: bool = False) -> dict:
    """Calcula confiabilidade em 3 eixos independentes.

    is_inferido=True → registro AUSENTE_INFERIDO (gap analysis):
      EIXO 1 forçado a 0, confiabilidade vem de CONF_GAP.
    """
    # EIXO 1 — COORDENADA (0-40)
    if is_inferido:
        e1 = 0
        e1_desc = 'INFERIDO (centróide do bloco)'
    else:
        nv = int(row.get('NV_GEO_COORD', 6) or 6)
        e1_map = {1: 40, 2: 35, 3: 20, 4: 10, 5: 5, 6: 0}
        e1 = e1_map.get(nv, 0)
        e1_desc = f'NV{nv}'

    # EIXO 2 — ENDEREÇO TEXTUAL (0-30)
    e2 = 0
    fatores2 = []
    if row.get('NOM_SEGLOGR_HARM'):
        e2 += 10; fatores2.append('LOGR_HARM')
    elif row.get('NOM_SEGLOGR'):
        e2 += 7; fatores2.append('LOGR_BRUTO')
    num = pd.to_numeric(row.get('NUM_ENDERECO'), errors='coerce')
    if pd.notna(num) and num > 0:
        e2 += 8; fatores2.append('NRO')
    # R7 (v4.3): CEP é entrada DIGITADA — não é decisório quando a coordenada
    # confirma. CEP 8 dígitos pontua; ausente/inválido com NV 1-2 + sanidade OK
    # é SUPRIDO pela coordenada (mesmos 7 pts) — nunca penaliza registro
    # confirmado em campo por causa de dígito de CEP.
    cep_dig = re.sub(r'\D', '', str(row.get('CEP', '') or ''))
    _nv = int(row.get('NV_GEO_COORD', 6) or 6)
    coord_confirma = (not is_inferido) and _nv in (1, 2) and row.get('SANIDADE_GEO') == 'OK'
    if len(cep_dig) == 8:
        e2 += 7; fatores2.append('CEP')
    elif coord_confirma:
        e2 += 7; fatores2.append('COORD_SUPRE_CEP')
    if row.get('COMPLEMENTO_NORM'):
        e2 += 5; fatores2.append('COMPL')

    # EIXO 3 — CONTEXTO (0-30)
    e3 = 0
    fatores3 = []
    if row.get('SANIDADE_GEO') == 'OK':
        e3 += 10; fatores3.append('GEO_OK')
    if row.get('NOM_SEGLOGR_HARM'):
        e3 += 8; fatores3.append('HARM_CEP')
    if row.get('FLAG_ANOMALIA_COLETA', 0) == 0:
        e3 += 7; fatores3.append('SEM_FRAUDE')
    if not is_inferido:
        e3 += 5; fatores3.append('REAL')

    total = e1 + e2 + e3
    if total > 75:   faixa = 'MUITO_ALTA'
    elif total > 55: faixa = 'ALTA'
    elif total > 35: faixa = 'MEDIA'
    else:            faixa = 'BAIXA'

    return {
        'RADAR_CONF_COORD': e1,
        'RADAR_CONF_COORD_DESC': e1_desc,
        'RADAR_CONF_ENDERECO': e2,
        'RADAR_CONF_ENDERECO_FATORES': '+'.join(fatores2),
        'RADAR_CONF_CONTEXTO': e3,
        'RADAR_CONF_CONTEXTO_FATORES': '+'.join(fatores3),
        'RADAR_CONF_LOCALIZACAO': total,
        'RADAR_CONF_FAIXA': faixa,
    }


# ╔═══════════════════════════════════════════════════════════════╗
# ║  POLOS COMERCIAIS (DBSCAN (sem sample_weight — ver nota))                         ║
# ╚═══════════════════════════════════════════════════════════════╝

# Setores que caracterizam atividade comercial para detecção de polos.
# DOMICILIO_COLETIVO e AGROPECUARIO excluídos — campus/presídio/fazenda
# não são polos comerciais (validado em Santa Maria: polo falso de 505
# estabelecimentos em um campus universitário).
SETORES_POLO = {
    'COMERCIO_SERVICO', 'COMERCIO_VAREJO', 'ALIMENTACAO', 'INDUSTRIAL',
    'MANUFATURA', 'SERVICO_AUTO', 'BELEZA_ESTETICA', 'SERVICO_PROF',
    'SAUDE', 'EDUCACAO', 'HOSPEDAGEM',
}


def _convex_hull_wkt(lons, lats):
    """Convex hull (monotone chain, SEM dependência externa) -> WKT POLYGON em
    lon/lat (EPSG:4326). Devolve os vértices que formam o contorno da área do
    polo, como anel fechado. Degenerado (colinear/poucos pontos) vira caixa mínima."""
    pts = sorted(set(zip([round(float(x), 6) for x in lons],
                         [round(float(y), 6) for y in lats])))
    def _box(ps):
        cx = sum(p[0] for p in ps) / len(ps)
        cy = sum(p[1] for p in ps) / len(ps)
        d = 5e-5  # ~5.5 m
        return [(cx - d, cy - d), (cx + d, cy - d), (cx + d, cy + d), (cx - d, cy + d)]
    if len(pts) < 3:
        hull = _box(pts)
    else:
        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        hull = lower[:-1] + upper[:-1]
        if len(hull) < 3:
            hull = _box(pts)
    ring = hull + [hull[0]]
    return 'POLYGON ((' + ', '.join(f'{x:.6f} {y:.6f}' for x, y in ring) + '))'


def detectar_polos_comerciais(df: pd.DataFrame,
                               eps_m: float = 100,
                               min_samples: int = 5,
                               peso_max: int = 500,
                               exigir_conf: bool = True) -> tuple:
    """DBSCAN por densidade de ENDEREÇOS sobre base AGRUPADA por endereço.

    Retorna (agg_enderecos, polos_caracterizados).

    Regras validadas em Santa Maria/RS (149.483 registros):
    - Apenas SETORES_POLO (sem DOMICILIO_COLETIVO/AGROPECUARIO)
    - 1 ponto por endereço com peso = qtd estabelecimentos (clip 500)
    - Classificação por porte: ZONA_CENTRAL ≥500 end | POLO_REGIONAL ≥50
      | POLO_LOCAL <50
    """
    from sklearn.cluster import DBSCAN

    nres = df[
        (df['SETOR_ATIVIDADE'].isin(SETORES_POLO)) &
        (df['SANIDADE_GEO'] == 'OK') &
        (df.get('NUMERO', df.get('NUM_ENDERECO', 0)) > 0)
    ].copy()
    if df.get('FLAG_ANOMALIA_COLETA') is not None:
        nres = nres[nres['FLAG_ANOMALIA_COLETA'] == 0]
    # v4.5.1: F7 é gate FAIL-CLOSED nos polos — coluna ausente ABORTA em vez
    # de silenciosamente ignorar o filtro (2ª revisão). Uso de biblioteca
    # sem F7 exige opt-out EXPLÍCITO (exigir_conf=False).
    if exigir_conf:
        _exigir('RADAR_CONF_FAIXA' in nres.columns,
                "R3: RADAR_CONF_FAIXA ausente na entrada dos polos — rode F7 "
                "antes ou passe exigir_conf=False (uso consciente de biblioteca)")
        nres = nres[nres['RADAR_CONF_FAIXA'].isin(['ALTA', 'MUITO_ALTA'])]
    elif 'RADAR_CONF_FAIXA' in nres.columns:
        nres = nres[nres['RADAR_CONF_FAIXA'].isin(['ALTA', 'MUITO_ALTA'])]

    if len(nres) == 0:
        return pd.DataFrame(), pd.DataFrame()

    agg = nres.groupby('CHAVE').agg(
        QTD_ESTAB_END=('COD_UNICO_ENDERECO', 'count'),
        LAT=('LATITUDE', 'mean'),
        LON=('LONGITUDE', 'mean'),
        SETOR_DOM=('SETOR_ATIVIDADE', lambda x: x.mode().iloc[0]),
    ).reset_index()

    coords_rad = np.radians(agg[['LAT', 'LON']].values)

    # v4.5 (revisão externa, P0): SEM sample_weight — no scikit-learn o peso
    # participa do critério de core point, então 1 endereço com 5 estab.
    # virava "polo" sozinho, violando a semântica de min_samples = mínimo de
    # ENDEREÇOS. Densidade é de endereços; a intensidade comercial
    # (QTD_ESTAB) caracteriza o cluster DEPOIS, não o forma. `peso_max`
    # permanece aceito por compatibilidade, sem efeito no clustering.
    db = DBSCAN(eps=eps_m / 6_371_000, min_samples=min_samples,
                metric='haversine', algorithm='ball_tree')
    agg['POLO_ID'] = db.fit_predict(coords_rad)

    polos = agg[agg['POLO_ID'] >= 0].groupby('POLO_ID').agg(
        QTD_END=('CHAVE', 'count'),
        QTD_ESTAB=('QTD_ESTAB_END', 'sum'),
        SETOR_DOMINANTE=('SETOR_DOM', lambda x: x.mode().iloc[0]),
        SETORES=('SETOR_DOM', lambda x: '+'.join(sorted(x.unique()))),
        CENTROIDE_LAT=('LAT', 'mean'),
        CENTROIDE_LON=('LON', 'mean'),
    ).sort_values('QTD_ESTAB', ascending=False).reset_index()

    polos['CLASSE_POLO'] = np.where(
        polos['QTD_END'] >= 500, 'ZONA_CENTRAL',
        np.where(polos['QTD_END'] >= 50, 'POLO_REGIONAL', 'POLO_LOCAL')
    )

    # Polígono da área de cada polo: convex hull dos endereços-membros (vértices em WKT)
    membros = agg[agg['POLO_ID'] >= 0]
    wkts = {pid: _convex_hull_wkt(g['LON'].values, g['LAT'].values)
            for pid, g in membros.groupby('POLO_ID')}
    polos['POLIGONO_WKT'] = polos['POLO_ID'].map(wkts)

    return agg, polos


# ╔═══════════════════════════════════════════════════════════════╗
# ║  SCORE DE CONSISTÊNCIA DE NUMERAÇÃO                          ║
# ╚═══════════════════════════════════════════════════════════════╝

def calcular_score_numeracao(df: pd.DataFrame,
                              logr_col: str = 'NOM_SEGLOGR_HARM') -> pd.DataFrame:
    """Score de Consistência de Numeração (0-100) por registro.

    Avalia 4 componentes independentes:
      N1 (0-30): Ordem crescente ao longo da rua (direção coord)
      N2 (0-25): Compatibilidade par/ímpar com vizinhos da face
      N3 (0-25): Distância para vizinhos (outlier de espaçamento)
      N4: DESATIVADO — `NUM_FACE` nao tem convencao par/impar publicada
            pelo IBGE. Total possivel: 80 pontos, nao 100.

    Retorna df com colunas:
      SCORE_NUM_N1, SCORE_NUM_N2, SCORE_NUM_N3, SCORE_NUM_N4,
      SCORE_NUMERACAO (0-100), SCORE_NUM_FAIXA (ALTA/MEDIA/BAIXA/INCONSISTENTE)

    Validado em Santa Maria/RS — identifica erros de recenseamento sem
    depender exclusivamente de NV_GEO_COORD.
    """
    from math import atan2, degrees

    df = df.copy()
    cols_out = ['SCORE_NUM_N1', 'SCORE_NUM_N2', 'SCORE_NUM_N3',
                'SCORE_NUM_N4', 'SCORE_NUMERACAO', 'SCORE_NUM_FAIXA']
    for c in cols_out:
        df[c] = 0 if c != 'SCORE_NUM_FAIXA' else 'INCONSISTENTE'

    # Só processar: NUM > 0, coord válida, logradouro harmonizado
    mask = (
        (df['NUM_ENDERECO'] > 0) &
        df['LATITUDE'].notna() & df['LONGITUDE'].notna() &
        df[logr_col].notna() & (df[logr_col] != '')
    )
    sub = df.loc[mask].copy()
    if len(sub) == 0:
        return df

    M_LAT = 110_900
    # A LATITUDE É A DO GRUPO, não uma constante do Rio Grande do Sul. O fator
    # anterior era `cos(-29,92°)` fixo, com o comentário "suficientemente
    # geral" — em Boa Vista (+2,8°) isso erra a escala longitudinal em ~13%,
    # e o DBSCAN ao lado já fazia certo com haversine. Motor que se declara
    # nacional não pode carregar a latitude de uma capital no código.
    #
    # E o agrupamento passa a ser por MUNICÍPIO + logradouro: 'RUA PRINCIPAL'
    # existe em centenas de municípios, e agrupar só pelo nome misturava
    # pontos de ruas que não têm nada a ver uma com a outra.
    _chaves = ([c for c in ('COD_MUNICIPIO',) if c in sub.columns] + [logr_col])
    for _k, grp in sub.groupby(_chaves if len(_chaves) > 1 else logr_col):
        if len(grp) < 2:
            # Rua com 1 registro: pontuação máxima em todos (sem base de comparação)
            df.loc[grp.index, 'SCORE_NUM_N1'] = 30
            df.loc[grp.index, 'SCORE_NUM_N2'] = 25
            df.loc[grp.index, 'SCORE_NUM_N3'] = 25
            df.loc[grp.index, 'SCORE_NUM_N4'] = 20
            df.loc[grp.index, 'SCORE_NUMERACAO'] = 100
            df.loc[grp.index, 'SCORE_NUM_FAIXA'] = 'ALTA'
            continue

        # Projetar na direção da rua (PCA simples: vetor médio lat/lon)
        _lat0 = float(np.nanmean(grp['LATITUDE'].values))
        m_lng = 111_320 * np.cos(np.radians(_lat0))
        xs = grp['LONGITUDE'].values * m_lng
        ys = grp['LATITUDE'].values * M_LAT
        nums = grp['NUM_ENDERECO'].values.astype(float)

        # Eixo principal da rua: correlação número × projeção espacial
        # Ordenar por número para comparação
        ord_idx = np.argsort(nums)
        xs_ord = xs[ord_idx]; ys_ord = ys[ord_idx]; nums_ord = nums[ord_idx]

        # ── N1: Ordem crescente (rank correlation de Spearman simplificado)
        # Projetar cada ponto no eixo (lon → distância ao longo da rua)
        dx = xs_ord[-1] - xs_ord[0]; dy = ys_ord[-1] - ys_ord[0]
        L = max(np.sqrt(dx**2 + dy**2), 1e-9)
        proj = (xs_ord - xs_ord[0]) * dx/L + (ys_ord - ys_ord[0]) * dy/L
        # Concordância: proj deve crescer junto com num
        diff_num = np.diff(nums_ord); diff_proj = np.diff(proj)
        if len(diff_num) > 0:
            concordante = np.sum((diff_num > 0) == (diff_proj > 0))
            n1 = round(30 * concordante / len(diff_num))
        else:
            n1 = 30
        # N1 individual = mesmo score para toda a rua (avaliação de rua)
        n1_scores = np.full(len(grp), n1)

        # ── N2: Compatibilidade par/ímpar
        # Calcular paridade dominante de cada face
        paridade = nums % 2  # 0=par, 1=ímpar
        dom = round(paridade.mean())  # 0=maioria par, 1=maioria ímpar
        concordancia_par = np.sum(paridade == dom) / len(paridade)
        n2_base = round(25 * concordancia_par)
        n2_scores = np.where(paridade == dom, n2_base, max(0, n2_base - 15))

        # ── N3: Distância para vizinhos (outlier de espaçamento)
        # Espaçamento numérico esperado: mediana dos intervalos
        intervalos = np.diff(sorted(nums))
        if len(intervalos) == 0:
            n3_scores = np.full(len(grp), 25)
        else:
            med_int = np.median(intervalos)
            if med_int == 0:
                n3_scores = np.full(len(grp), 25)
            else:
                # Para cada registro: qual o menor intervalo para vizinho
                nums_sorted = np.sort(nums)
                n3_scores = np.zeros(len(grp))
                for ki, n in enumerate(grp['NUM_ENDERECO'].values):
                    pos = np.searchsorted(nums_sorted, n)
                    dists = []
                    if pos > 0:
                        dists.append(abs(n - nums_sorted[pos-1]))
                    if pos < len(nums_sorted)-1:
                        dists.append(abs(n - nums_sorted[pos+1]))
                    if not dists:
                        n3_scores[ki] = 25; continue
                    d = min(dists)
                    # Outlier: intervalo > 4× a mediana
                    ratio = d / max(med_int, 1)
                    if ratio <= 2:
                        n3_scores[ki] = 25
                    elif ratio <= 4:
                        n3_scores[ki] = 15
                    elif ratio <= 8:
                        n3_scores[ki] = 8
                    else:
                        n3_scores[ki] = 0

        # ── N4: REMOVIDO — atribuía significado a um código que não o tem.
        # O código lia `NUM_FACE` como "1 = par, face 2 = ímpar (convenção
        # IBGE)". Essa convenção NÃO existe no dicionário do CNEFE, que define
        # `NUM_FACE` apenas como "número da face". Era semântica inventada
        # sobre um identificador, e um score que pune o lado "errado" de uma
        # rua a partir de uma convenção que ninguém publicou pune o dado certo.
        #
        # O eixo fica valendo 0 e o score passa a somar 80 pontos possíveis,
        # declarado abaixo. Reintroduzir exige fundamento documental do IBGE ou
        # validação empírica contra face geométrica de quadra — não palpite.
        n4_scores = np.zeros(len(grp), dtype=int)

        # Consolidar por registro da rua
        scores_total = n1_scores + n2_scores + n3_scores + n4_scores
        # teto real de 80 (N4 desativado); as faixas acompanham, senao a
        # remocao de um eixo rebaixaria toda a base sem nada ter piorado
        scores_total = np.clip(scores_total, 0, 80)

        faixas = np.where(scores_total >= 60, 'ALTA',
                 np.where(scores_total >= 40, 'MEDIA',
                 np.where(scores_total >= 20, 'BAIXA', 'INCONSISTENTE')))

        df.loc[grp.index, 'SCORE_NUM_N1'] = n1_scores.astype(int)
        df.loc[grp.index, 'SCORE_NUM_N2'] = n2_scores.astype(int)
        df.loc[grp.index, 'SCORE_NUM_N3'] = n3_scores.astype(int)
        df.loc[grp.index, 'SCORE_NUM_N4'] = n4_scores.astype(int)
        df.loc[grp.index, 'SCORE_NUMERACAO'] = scores_total.astype(int)
        df.loc[grp.index, 'SCORE_NUM_FAIXA'] = faixas

    # Registros que não passaram na mask: score 0, INCONSISTENTE
    # (NUM=0 ou sem coord — R1 já os excluirá de oportunidades)

    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  MODELO DE ENDEREÇO PROVÁVEL                                 ║
# ╚═══════════════════════════════════════════════════════════════╝

def construir_endereco_provavel(df: pd.DataFrame) -> pd.DataFrame:
    """Reconstrói ENDERECO_DECLARADO e ENDERECO_REAL por registro.

    ENDERECO_DECLARADO: endereço bruto como veio no CNEFE (raw)
    ENDERECO_REAL:      versão reconstruída normalizada com todas as
                        camadas semânticas em ordem canônica

    Exemplos de ENDERECO_REAL:
      "RUA JOAO XXIII, 123 — BL A / APT 201"
      "RUA CAMOBI, Q15/L8/C3"
      "AV. NOSSA SENHORA MEDIANEIRA, 450 — FRENTE"
      "RUA PINHEIRO MACHADO — S/N"

    Útil para: integração com cadastros comerciais, geocodificação,
    deduplicação cross-source, campo ENDERECO_DISPLAY no output.
    """
    df = df.copy()

    # ── ENDERECO_DECLARADO (raw bruto)
    def _declarado(row):
        parts = []
        tipo  = str(row.get('NOM_TIPO_SEGLOGR', '') or '').strip()
        titulo= str(row.get('NOM_TITULO_SEGLOGR', '') or '').strip()
        # Limpar "nan" literal que surge de valores nulos convertidos para string
        titulo = '' if titulo.lower() == 'nan' else titulo
        tipo   = '' if tipo.lower() == 'nan' else tipo
        nome   = str(row.get('NOM_SEGLOGR', '') or '').strip()
        nome   = '' if nome.lower() == 'nan' else nome
        logr_raw = ' '.join(filter(None, [tipo, titulo, nome])).strip()
        if logr_raw: parts.append(logr_raw)

        num = row.get('NUM_ENDERECO')
        num_int = pd.to_numeric(num, errors='coerce')
        if pd.notna(num_int) and int(num_int) > 0:
            parts.append(str(int(num_int)))
        else:
            parts.append('S/N')

        # Complementos brutos em ordem dos pares
        compls = []
        for i in range(1, 6):
            n = str(row.get(f'NOM_COMP_ELEM{i}', '') or '').strip()
            v = str(row.get(f'VAL_COMP_ELEM{i}', '') or '').strip()
            if n and n.upper() not in ('NAN', ''):
                compls.append(f'{n} {v}'.strip() if v and v.lower() != 'nan' else n)
        if compls:
            parts.append(' | '.join(compls))

        return ', '.join(parts[:2]) + (' — ' + parts[2] if len(parts) > 2 else '')

    # ── ENDERECO_REAL (reconstruído com camadas normalizadas)
    def _real(row):
        # Parte do logradouro (harmonizado)
        tipo  = str(row.get('NOM_TIPO_SEGLOGR', '') or '').strip()
        titulo= str(row.get('NOM_TITULO_HARM',
                             row.get('NOM_TITULO_SEGLOGR', '')) or '').strip()
        nome  = str(row.get('NOM_SEGLOGR_HARM',
                             row.get('NOM_SEGLOGR', '')) or '').strip()
        logr = ' '.join(filter(None, [tipo, titulo, nome])).strip()

        # Padrão B: Q/L/C
        chave_ql = str(row.get('CHAVE_QUADRA_LOTE', '') or '').strip()
        padrao   = str(row.get('PADRAO_ENDERECO', 'LOGRADOURO'))

        if padrao == 'QUADRA_LOTE' and chave_ql:
            base = f'{logr}, {chave_ql}' if logr else chave_ql
        else:
            num = row.get('NRO_OFICIAL', row.get('NUM_ENDERECO'))
            num_int = pd.to_numeric(num, errors='coerce')
            if pd.notna(num_int) and int(num_int) > 0:
                base = f'{logr}, {int(num_int)}'
            else:
                base = f'{logr} — S/N'

        # Complemento normalizado em ordem canônica
        compl_parts = []
        for cam, sep in [('AGRUPAMENTO', None), ('VIA_INTERNA', None),
                         ('BLOCO', 'BL'), ('PAVIMENTO', None),
                         ('UNIDADE', 'APT'), ('MORADIA', None), ('POSICAO', None)]:
            t = row.get(f'{cam}_TIPO')
            v = row.get(f'{cam}_VALOR')
            if not pd.isna(t) and t and str(t).upper() not in ('NAN', ''):
                v_str = '' if pd.isna(v) or str(v).lower() in ('nan', '') else str(v).strip()
                # Usar rótulo curto para blocos/aptos
                if cam == 'BLOCO' and v_str:
                    compl_parts.append(f'BL {v_str}')
                elif cam == 'UNIDADE' and v_str:
                    t_upper = str(t).upper()
                    prefix = 'APT' if 'APART' in t_upper or t_upper == 'APARTAMENTO' else str(t).title()
                    compl_parts.append(f'{prefix} {v_str}')
                elif v_str:
                    compl_parts.append(f'{str(t).title()} {v_str}')
                else:
                    compl_parts.append(str(t).title())

        compl_str = ' / '.join(compl_parts)
        if compl_str:
            return f'{base} — {compl_str}'
        return base

    df['ENDERECO_DECLARADO'] = df.apply(_declarado, axis=1)
    df['ENDERECO_REAL'] = df.apply(_real, axis=1)
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  DETECÇÃO DE CONDOMÍNIO HORIZONTAL                           ║
# ╚═══════════════════════════════════════════════════════════════╝

# Tipos horizontais novos (adicionados ao pipeline de classificação)
TIPOS_COND_HORIZ = {
    'COND_HORIZ_CASAS': 'Condomínio horizontal com sequência de casas (CASA 01, 02...)',
    'COND_HORIZ_LOTES': 'Condomínio horizontal com sequência de lotes (Q/L sequencial)',
    'COND_HORIZ_MISTO': 'Condomínio horizontal misto (casas + posições)',
}

# Threshold: mínimo de unidades sequenciais para classificar como condomínio
COND_HORIZ_MIN = 3


def detectar_condominio_horizontal(df: pd.DataFrame) -> pd.DataFrame:
    """Detecta condomínios horizontais na base CNEFE.

    Padrões reconhecidos:
      1. CASA 01, 02, 03... no mesmo endereço (MORADIA_TIPO=CASA sequencial)
      2. Q15/L08, Q15/L09, Q15/L10 (lotes sequenciais na mesma quadra)
      3. Misto: casas + fundos/frente no mesmo endereço

    Adiciona ao df:
      FLAG_COND_HORIZ      (0/1)
      TIPO_COND_HORIZ      (COND_HORIZ_CASAS / COND_HORIZ_LOTES / COND_HORIZ_MISTO)
      COND_HORIZ_SEQ_MIN   (menor número na sequência)
      COND_HORIZ_SEQ_MAX   (maior número na sequência)
      COND_HORIZ_QTD       (quantidade de unidades na sequência)
      COND_HORIZ_GAP       (unidades ausentes na sequência)
      COND_HORIZ_CONF      (ALTA/MEDIA/BAIXA)
    """
    df = df.copy()
    for col in ['FLAG_COND_HORIZ', 'TIPO_COND_HORIZ', 'COND_HORIZ_SEQ_MIN',
                'COND_HORIZ_SEQ_MAX', 'COND_HORIZ_QTD', 'COND_HORIZ_GAP',
                'COND_HORIZ_CONF']:
        df[col] = None

    # Elegibilidade em DOIS ramos, e é o ponto que estava quebrado.
    #
    # A versão anterior exigia NUM_ENDERECO > 0 para TUDO. Mas Quadra/Lote só
    # nasce onde NUM_ENDERECO == 0 (`radar_pipeline`: a promoção Q/L roda sob
    # a máscara `NUM_ENDERECO == 0`). As duas condições são incompatíveis, e o
    # padrão COND_HORIZ_LOTES era inalcançável pelo fluxo canônico — anunciado
    # na doutrina, testado com estado fabricado (`NUM=10 + PADRAO=QUADRA_LOTE`,
    # que o pipeline nunca produz) e morto na prática.
    #
    # Endereçamento por número e endereçamento por quadra/lote são regimes
    # distintos: o primeiro exige número, o segundo exige a chave Q/L. Exigir
    # número do segundo é exigir o que ele não tem por definição.
    _num = pd.to_numeric(df['NUM_ENDERECO'], errors='coerce').fillna(0)
    _geo_ok = df['NV_GEO_COORD'].isin([1, 2])
    _ql = (df.get('PADRAO_ENDERECO', pd.Series('', index=df.index)) == 'QUADRA_LOTE')
    mask_valid = ((_num > 0) | _ql) & _geo_ok
    sub = df[mask_valid].copy()
    if len(sub) == 0:
        return df

    # ── PADRÃO 1: CASA sequencial no mesmo endereço
    casas = sub[sub['MORADIA_TIPO'] == 'CASA'].copy()
    if len(casas) > 0:
        casas['_CASA_NUM'] = num_unidade(casas['MORADIA_VALOR'])
        casas_valid = casas[casas['_CASA_NUM'].notna()]

        ch_agg = casas_valid.groupby('CHAVE').agg(
            n=('_CASA_NUM', 'count'),
            nun=('_CASA_NUM', 'nunique'),
            mn=('_CASA_NUM', 'min'),
            mx=('_CASA_NUM', 'max'),
        )
        ch_agg['esperadas'] = (ch_agg['mx'] - ch_agg['mn'] + 1).clip(lower=1)
        ch_agg['gaps'] = ch_agg['esperadas'] - ch_agg['nun']
        ch_agg['cobertura'] = ch_agg['nun'] / ch_agg['esperadas']
        # Condomínio horizontal: ≥3 casas com sequência identificável
        mask_horiz = ch_agg['nun'] >= COND_HORIZ_MIN  # nunique: casas distintas

        for chave, row_agg in ch_agg[mask_horiz].iterrows():
            idx = sub[sub['CHAVE'] == chave].index
            conf = ('ALTA' if row_agg['cobertura'] >= 0.8 and row_agg['n'] >= 5
                    else 'MEDIA' if row_agg['n'] >= 3
                    else 'BAIXA')
            df.loc[idx, 'FLAG_COND_HORIZ'] = 1
            df.loc[idx, 'TIPO_COND_HORIZ'] = 'COND_HORIZ_CASAS'
            df.loc[idx, 'COND_HORIZ_SEQ_MIN'] = int(row_agg['mn'])
            df.loc[idx, 'COND_HORIZ_SEQ_MAX'] = int(row_agg['mx'])
            df.loc[idx, 'COND_HORIZ_QTD'] = int(row_agg['nun'])
            df.loc[idx, 'COND_HORIZ_GAP'] = int(row_agg['gaps'])
            df.loc[idx, 'COND_HORIZ_CONF'] = conf

    # ── PADRÃO 2: Lotes sequenciais na mesma quadra (Q/L padrão B)
    lotes = sub[
        (sub.get('PADRAO_ENDERECO', 'LOGRADOURO') == 'QUADRA_LOTE') &
        sub['CHAVE_QUADRA_LOTE'].notna() &
        sub['CHAVE_QUADRA_LOTE'].str.contains('/L', na=False)
    ].copy() if 'PADRAO_ENDERECO' in sub.columns else pd.DataFrame()

    if len(lotes) > 0:
        # Extrair quadra e lote do CHAVE_QUADRA_LOTE
        lotes['_Q'] = lotes['CHAVE_QUADRA_LOTE'].str.extract(r'Q(\w+)')[0]
        lotes['_L'] = pd.to_numeric(
            lotes['CHAVE_QUADRA_LOTE'].str.extract(r'L(\d+)')[0], errors='coerce'
        )
        lotes_valid = lotes[lotes['_L'].notna()]

        # Agrupar por (LOGRADOURO + QUADRA) para encontrar lotes sequenciais
        lotes_valid['_QL_KEY'] = (
            lotes_valid.get('NOM_SEGLOGR_HARM', lotes_valid.get('NOM_SEGLOGR','')) +
            '|Q' + lotes_valid['_Q'].fillna('')
        )
        ql_agg = lotes_valid.groupby('_QL_KEY').agg(
            n=('_L', 'count'),
            nun=('_L', 'nunique'),
            mn=('_L', 'min'),
            mx=('_L', 'max'),
            idx_list=('_L', lambda x: list(x.index)),
        )
        ql_agg['esperadas'] = (ql_agg['mx'] - ql_agg['mn'] + 1).clip(lower=1)
        ql_agg['gaps'] = ql_agg['esperadas'] - ql_agg['nun']

        # v4.3: gate por lotes ÚNICOS (nunique) — count duplicado não fabrica condomínio
        for ql_key, row_agg in ql_agg[ql_agg['nun'] >= COND_HORIZ_MIN].iterrows():
            idx = [i for i in row_agg['idx_list']]
            # Não sobrescrever se já foi marcado como CASAS (prioridade)
            mask_sem = df.loc[idx, 'FLAG_COND_HORIZ'].isna()
            idx_new = [i for i in idx if mask_sem.loc[i]]
            if not idx_new: continue
            conf = 'ALTA' if row_agg['nun'] >= 5 else 'MEDIA'
            df.loc[idx_new, 'FLAG_COND_HORIZ'] = 1
            df.loc[idx_new, 'TIPO_COND_HORIZ'] = 'COND_HORIZ_LOTES'
            df.loc[idx_new, 'COND_HORIZ_SEQ_MIN'] = int(row_agg['mn'])
            df.loc[idx_new, 'COND_HORIZ_SEQ_MAX'] = int(row_agg['mx'])
            df.loc[idx_new, 'COND_HORIZ_QTD'] = int(row_agg['nun'])
            df.loc[idx_new, 'COND_HORIZ_GAP'] = int(row_agg['gaps'])
            df.loc[idx_new, 'COND_HORIZ_CONF'] = conf

    # ── PADRÃO 3: Misto (casas + posições no mesmo endereço)
    mistos = sub[
        sub['MORADIA_TIPO'].isin(['CASA', 'SOBRADO']) &
        sub['POSICAO_TIPO'].notna()
    ]
    if len(mistos) > 0:
        misto_agg = mistos.groupby('CHAVE').size()
        for chave, n in misto_agg[misto_agg >= COND_HORIZ_MIN].items():
            idx = sub[sub['CHAVE'] == chave].index
            # Só marcar como MISTO se não classificado ainda
            mask_sem = df.loc[idx, 'FLAG_COND_HORIZ'].isna()
            idx_new = idx[mask_sem]
            if len(idx_new) == 0: continue
            df.loc[idx_new, 'FLAG_COND_HORIZ'] = 1
            df.loc[idx_new, 'TIPO_COND_HORIZ'] = 'COND_HORIZ_MISTO'
            df.loc[idx_new, 'COND_HORIZ_QTD'] = int(n)
            df.loc[idx_new, 'COND_HORIZ_CONF'] = 'MEDIA'

    # Garantir que FLAG_COND_HORIZ=0 onde não detectou
    df['FLAG_COND_HORIZ'] = (pd.to_numeric(df['FLAG_COND_HORIZ'], errors='coerce')
                             .fillna(0).astype(int))

    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  COLETIVAS SIMPLES + ECONOMIAS FALTANTES (gap)  — abas 5 e 6  ║
# ╚═══════════════════════════════════════════════════════════════╝

COMERCIAL_SET_SIMPLES = {
    'COMERCIO_VAREJO', 'COMERCIO_SERVICO', 'ALIMENTACAO', 'BELEZA_ESTETICA',
    'SERVICO_PROF', 'SERVICO_AUTO', 'SAUDE', 'HOSPEDAGEM', 'MANUFATURA',
    'INDUSTRIAL', 'EDUCACAO',
}
TERREO_AL_SIMPLES = {'TERREO', '0', 'T', 'TER', 'SS', 'G', 'LOJA'}

COLS_SIMPLES = ['ID_COLETIVA', 'RADAR_ID_ENDERECO',
                'LOGRADOURO', 'NUMERO', 'LOCALIDADE', 'CEP', 'COD_SETOR',
                'QTD_ECONOMIAS', 'TIPO_SIMPLES', 'MARCADORES',
                'QTD_RESIDENCIAL', 'QTD_COMERCIAL', 'QTD_OUTRO',
                'FLAG_USO_MISTO', 'FLAG_FACHADA_ATIVA',
                'CONFIANCA_COD', 'QUALIDADE_COORD', 'CENTROIDE_LAT', 'CENTROIDE_LON']

COLS_PAR_FALTANTE = ['ID_COLETIVA', 'RADAR_ID_ENDERECO',
                     'LOGRADOURO', 'NUMERO', 'LOCALIDADE', 'CEP', 'COD_SETOR',
                     'GAP_TIPO', 'PRESENTES', 'AUSENTE_INFERIDO', 'N_AUSENTES', 'CONF_GAP',
                     # R9 — graduação: viaja junto da hipótese desde a origem
                     'EVD_REGRA', 'EVD_CLASSE', 'EVD_GRAU', 'EVD_DISTANCIA',
                     'EVD_DESCRICAO', 'ACT_DESTINO_CAMPO',
                     'STATUS', 'RADAR_CONF_COORD', 'CENTROIDE_LAT', 'CENTROIDE_LON']


def num_unidade(serie):
    """Valor de unidade como INTEIRO ESTRITO — só dígitos.

    `pd.to_numeric` interpreta notação científica, e o complemento real do
    recenseamento traz `713E714` querendo dizer "apartamento 713 E 714" (a
    conjunção portuguesa). O Python lê 713x10^714 = infinito, e `int(inf)`
    levanta OverflowError — travou a base real de Porto Alegre em 5 registros.
    Casos como `101E102` são piores: viram 1e104, um número FINITO e absurdo
    que entra no gap analysis sem estourar nada.

    Regra: unidade é dígito puro. `713E714` não é um número — são duas
    unidades, e tratá-lo como uma seria inventar dado.
    """
    s = serie.fillna('').astype(str).str.strip()
    ok = s.str.fullmatch(r'\d{1,9}')
    return pd.to_numeric(s.where(ok), errors='coerce')


def _parse_chave_ql(s) -> dict:
    """'Q15/L8/C3' -> {'quadra':'15','lote':'8','casa':'3'}."""
    q = l = c = ''
    for tok in str(s).split('/'):
        tok = tok.strip()
        if tok[:1] == 'Q':   q = tok[1:]
        elif tok[:1] == 'L': l = tok[1:]
        elif tok[:1] == 'C': c = tok[1:]
    return {'quadra': q, 'lote': l, 'casa': c}


def construir_simples_e_faltantes(col, ql_records=None, universo=None):
    """Constrói as abas Coletivas_Simples e Coletivas_Par_Faltante num ÚNICO passe.

    col         : registros coletivos JÁ GATEADOS (FLAG_COL==1) com camadas de
                  complemento normalizadas (POSICAO/MORADIA/PAVIMENTO/UNIDADE/BLOCO/
                  AGRUPAMENTO _TIPO/_VALOR), SETOR_ATIVIDADE, CONFIANCA, COMPLEMENTO_NORM,
                  ID_COLETIVA, CHAVE, LOGRADOURO, NUMERO, DSC_LOCALIDADE, CEP, COD_SETOR,
                  LATITUDE, LONGITUDE, QUALIDADE_COORD.
    ql_records  : registros PADRAO_ENDERECO=='QUADRA_LOTE' já filtrados por
                  SANIDADE_GEO=='OK' e FLAG_ANOMALIA_COLETA==0 (gap Q/L). Opcional.

    Retorna (simples_df, par_faltante_df, flags_df):
      simples_df      — 1 linha por ENDEREÇO de coletiva simples (2 economias)
      par_faltante_df — 1 linha por economia AUSENTE_INFERIDO (EIXO1=0, coord nunca fabricada)
      flags_df        — ['ID_COLETIVA','FLAG_USO_MISTO','FLAG_FACHADA_ATIVA'] por ID
    """
    # Visão do ENDEREÇO INTEIRO por CHAVE (R12). `col` é o subconjunto
    # classificado como coletivo; a numeração tem de ser lida do grupo todo.
    # Só os DOIS CONJUNTOS por chave, nunca os dataframes. Materializar
    # `{chave: subframe}` para 279 mil endereços estourou 7 GB e o processo
    # morreu sem mensagem — o índice do groupby carrega o frame inteiro.
    # O que a grade precisa é numérico e cabe em dois dicionários de sets.
    _units_ch, _ocup_ch = {}, {}
    if universo is not None and 'CHAVE' in getattr(universo, 'columns', []):
        _u = pd.DataFrame({
            'CHAVE': universo['CHAVE'].values,
            '_n': num_unidade(universo['UNIDADE_VALOR']).values,
            '_o': num_unidade_ocupada(universo['UNIDADE_VALOR']).values,
        })
        _a = _u.dropna(subset=['_n'])
        if len(_a):
            _a = _a.assign(_n=_a['_n'].astype('int64')).drop_duplicates(['CHAVE', '_n'])
            _units_ch = _a.groupby('CHAVE')['_n'].agg(list).to_dict()
        _b = _u.dropna(subset=['_o'])
        if len(_b):
            _b = _b.assign(_o=_b['_o'].astype('int64')).drop_duplicates(['CHAVE', '_o'])
            _ocup_ch = _b.groupby('CHAVE')['_o'].agg(set).to_dict()
        del _u, _a, _b

    simples_rows, gap_rows, flag_rows = [], [], []

    for _chave, g in col.groupby('CHAVE', sort=False):
        pos_t = set(g['POSICAO_TIPO'].dropna().astype(str))
        pos_v = {str(v).upper() for v in g['POSICAO_VALOR'].dropna() if str(v).strip()}
        mor = list(g['MORADIA_TIPO'].dropna().astype(str))
        pav_t = set(g['PAVIMENTO_TIPO'].dropna().astype(str))
        pav_v = {str(v).upper() for v in g['PAVIMENTO_VALOR'].dropna() if str(v).strip()}
        uni = int(g['UNIDADE_VALOR'].fillna('').mask(lambda s: s.eq('')).dropna().nunique())
        nbloco = int(g['BLOCO_VALOR'].fillna('').mask(lambda s: s.eq('')).dropna().nunique())
        agrup = bool(g['AGRUPAMENTO_TIPO'].notna().any())
        n = len(g)
        n_res = int((g['SETOR_ATIVIDADE'] == 'RESIDENCIAL').sum())
        n_com = int(g['SETOR_ATIVIDADE'].isin(COMERCIAL_SET_SIMPLES).sum())
        compls = sorted({c for c in g['COMPLEMENTO_NORM'].dropna().astype(str) if c.strip()})
        idc = g['ID_COLETIVA'].iloc[0]
        xid = (g['RADAR_ID_ENDERECO'].iloc[0]
               if 'RADAR_ID_ENDERECO' in g.columns else None)
        logr = g['LOGRADOURO'].iloc[0]; numero = int(g['NUMERO'].iloc[0])
        loc = g['DSC_LOCALIDADE'].iloc[0]; cep = g['CEP'].iloc[0]; setor = g['COD_SETOR'].iloc[0]
        clat = float(g['LATITUDE'].mean()); clon = float(g['LONGITUDE'].mean())
        confc = (g['CONFIANCA'].mode().iloc[0] if len(g['CONFIANCA'].mode()) else g['CONFIANCA'].iloc[0])
        qcoord = g['QUALIDADE_COORD'].iloc[0]
        um = verificar_uso_misto(g); fa = verificar_fachada_ativa(g)
        flag_rows.append({'ID_COLETIVA': idc, 'FLAG_USO_MISTO': um, 'FLAG_FACHADA_ATIVA': fa})

        # — classificação SIMPLES —
        has_terreo = ('TERREO' in pav_t) or bool(pav_v & TERREO_AL_SIMPLES)
        has_terreo_split = has_terreo and (len(pav_t - {'TERREO'}) > 0 or len(pav_v - TERREO_AL_SIMPLES) > 0)
        if pos_t & {'FRENTE', 'FUNDOS'}:                ts = 'FRENTE_FUNDOS'
        elif pos_t & {'LADO'}:                          ts = 'LADO'
        elif pos_t & {'ANEXO', 'DEPENDENCIA', 'PORAO'}: ts = 'ANEXO'
        elif 'SOBRADO' in mor:                          ts = 'SOBRADO'
        elif has_terreo_split:                          ts = 'TERREO_SUPERIOR'
        elif 'CASA' in mor:                             ts = 'CASA_MULTIPLA'
        else:                                           ts = 'DUPLA_GENERICA'
        simple_marker = (bool(pos_t) or bool(mor) or has_terreo_split or
                         (n == 2 and uni == 0 and nbloco == 0 and not agrup))
        if (2 <= n <= 4) and (nbloco < 2) and not (agrup and uni >= 4) and (uni < 4) and simple_marker:
            simples_rows.append({
                'ID_COLETIVA': idc, 'RADAR_ID_ENDERECO': xid,
                'LOGRADOURO': logr, 'NUMERO': numero, 'LOCALIDADE': loc,
                'CEP': cep, 'COD_SETOR': setor, 'QTD_ECONOMIAS': n, 'TIPO_SIMPLES': ts,
                'MARCADORES': ' | '.join(compls[:6]) if compls else '(sem complemento)',
                'QTD_RESIDENCIAL': n_res, 'QTD_COMERCIAL': n_com, 'QTD_OUTRO': n - n_res - n_com,
                'FLAG_USO_MISTO': um, 'FLAG_FACHADA_ATIVA': fa, 'CONFIANCA_COD': confc,
                'QUALIDADE_COORD': qcoord, 'CENTROIDE_LAT': clat, 'CENTROIDE_LON': clon})

        # — GAP posicional (v4.7: por UNIDADE, não por conjunto do endereço) —
        # A contraparte só pode ser avaliada linha a linha: quem carrega o
        # polo, quem carrega outro descritor, quem não carrega nada.
        posset = pos_t | pos_v
        # curto-circuito: a esmagadora maioria dos endereços não tem token
        # posicional algum, e montar as listas para eles custaria o passe todo.
        hips_pos = []
        if posset:
            _pt = g['POSICAO_TIPO'].fillna('').astype(str).str.upper().str.strip()
            _pv = g['POSICAO_VALOR'].fillna('').astype(str).str.upper().str.strip()
            pos_por_un = [{x for x in (a, b) if x} for a, b in zip(_pt, _pv)]
            _od = None
            for c in ('UNIDADE_VALOR', 'MORADIA_VALOR', 'BLOCO_VALOR'):
                if c not in g.columns:
                    continue
                m = g[c].fillna('').astype(str).str.strip().ne('')
                _od = m if _od is None else (_od | m)
            outro_desc = ([False] * len(g)) if _od is None else _od.tolist()
            hips_pos = gap_posicional(pos_por_un, outro_desc)
        for h in hips_pos:
            gap_rows.append({
                'ID_COLETIVA': idc, 'RADAR_ID_ENDERECO': xid,   # herda o id do endereço-pai
                'LOGRADOURO': logr, 'NUMERO': numero, 'LOCALIDADE': loc,
                'CEP': cep, 'COD_SETOR': setor, 'GAP_TIPO': 'POSICIONAL',
                'PRESENTES': '/'.join(sorted(posset))[:80],
                'AUSENTE_INFERIDO': h['VALOR'], 'N_AUSENTES': 1,
                'CONF_GAP': classificar_gap_confianca('POSICIONAL', n, 1),
                'EVD_CLASSE': h['EVD_CLASSE'], 'EVD_GRAU': h['EVD_GRAU'],
                'EVD_DISTANCIA': h['EVD_DISTANCIA'], 'EVD_DESCRICAO': h['EVD_DESCRICAO'],
                'EVD_REGRA': 'gap_posicional',
                'ACT_DESTINO_CAMPO': DESTINO_POR_GRAU[h['EVD_GRAU']],
                'STATUS': 'AUSENTE_INFERIDO', 'RADAR_CONF_COORD': 0,
                'CENTROIDE_LAT': clat, 'CENTROIDE_LON': clon})

        # — GAP unidades: grade 3-díg COMPLETA -> gap_grade; senão sequência linear —
        # R12 — a grade de um endereço é definida pelo ENDEREÇO INTEIRO.
        #
        # `col` traz só as linhas com FLAG_COL==1, e essa flag NÃO é uniforme
        # dentro da mesma CHAVE: no caso real de RUA CORONEL VICENTE 529 eram
        # 7 de 18 linhas. O motor via 7 salas de um prédio com 18 e devolvia as
        # outras 11 como lacuna — hipóteses para unidades presentes no arquivo,
        # duas linhas abaixo na mesma tabela.
        #
        # Coletividade é atributo do endereço; a numeração também. Filtrar por
        # uma classificação POR REGISTRO antes de raciocinar sobre a numeração
        # do GRUPO é o mesmo erro de grão que matou Q/L e os polos.
        if _chave in _units_ch:
            units = sorted(_units_ch[_chave])
            ocupadas = _ocup_ch.get(_chave, set())
        else:                                   # sem universo: visão parcial
            units = sorted(set(num_unidade(g['UNIDADE_VALOR']).dropna().astype('int64')))
            # Conjunto de EXCLUSÃO, mais largo que o de inclusão: qualquer
            # unidade cujo prefixo numérico case já ocupa o lugar e cancela a
            # hipótese, mesmo sem servir para construir a grade ("303 SINDICO").
            ocupadas = set(num_unidade_ocupada(g['UNIDADE_VALOR']).dropna().astype('int64'))
        if len(units) >= 2:
            is_grade = (len(units) >= 3) and all(u >= 100 for u in units)
            if is_grade:
                hips_u, gtipo, regra = gap_grade(units), 'GRADE_APTO', 'gap_grade'
            else:
                # sequência linear é interpolação estrita entre os extremos
                # observados — cercada dos dois lados por construção.
                hips_u, gtipo, regra = ([
                    _hip(v, 'A_INTERIOR_SEQUENCIA', 'SUSTENTADA',
                         f'sequência observada {units[0]}-{units[-1]}')
                    for v in gap_linear(units)], 'SEQUENCIA', 'gap_linear')
            hips_u = _sem_ocupadas(hips_u, ocupadas)
            for h in hips_u:
                gap_rows.append({
                    'ID_COLETIVA': idc, 'RADAR_ID_ENDERECO': xid,   # herda o id do endereço-pai
                    'LOGRADOURO': logr, 'NUMERO': numero, 'LOCALIDADE': loc,
                    'CEP': cep, 'COD_SETOR': setor, 'GAP_TIPO': gtipo,
                    'PRESENTES': ','.join(map(str, units))[:80],
                    'AUSENTE_INFERIDO': str(h['VALOR']), 'N_AUSENTES': len(hips_u),
                    'CONF_GAP': classificar_gap_confianca(gtipo, len(units), len(hips_u)),
                    'EVD_CLASSE': h['EVD_CLASSE'], 'EVD_GRAU': h['EVD_GRAU'],
                    'EVD_DISTANCIA': h['EVD_DISTANCIA'], 'EVD_DESCRICAO': h['EVD_DESCRICAO'],
                    'EVD_REGRA': regra,
                    'ACT_DESTINO_CAMPO': DESTINO_POR_GRAU[h['EVD_GRAU']],
                    'STATUS': 'AUSENTE_INFERIDO', 'RADAR_CONF_COORD': 0,
                    'CENTROIDE_LAT': clat, 'CENTROIDE_LON': clon})

    # — GAP Quadra/Lote (loteamentos, Padrão B / NUM=0) —
    # v4.5 (R7): agrupamento por (COD_SETOR, LOGRADOURO) — o CEP havia
    # voltado a ser decisório aqui, violando a própria doutrina; o setor
    # censitário é identidade ESPACIAL oficial. CEP segue só descritivo.
    if ql_records is not None and len(ql_records):
        ql = ql_records.copy()
        ql['_QL'] = ql['CHAVE_QUADRA_LOTE'].fillna('').map(_parse_chave_ql)
        for (setorg, logrg), gq in ql.groupby(['COD_SETOR', 'LOGRADOURO'], sort=False):
            regs = [r for r in gq['_QL'].tolist() if r['quadra'] or r['lote']]
            if len(regs) < 3:
                continue
            clat = float(gq['LATITUDE'].mean()); clon = float(gq['LONGITUDE'].mean())
            for nivel, faltam in gap_quadra_lote(regs).items():
                for miss in faltam:
                    gap_rows.append({
                        'ID_COLETIVA': None, 'RADAR_ID_ENDERECO': None,  # lote inferido: sem pai único
                        'LOGRADOURO': logrg, 'NUMERO': 0,
                        'LOCALIDADE': gq['DSC_LOCALIDADE'].iloc[0],
                        'CEP': gq['CEP'].iloc[0],           # descritivo, não decisório (R7)
                        'COD_SETOR': setorg, 'GAP_TIPO': 'QUADRA_LOTE',
                        'PRESENTES': str(nivel)[:80], 'AUSENTE_INFERIDO': str(miss), 'N_AUSENTES': len(faltam),
                        'CONF_GAP': classificar_gap_confianca('QUADRA_LOTE', len(regs), len(faltam)),
                        # interpolação estrita dentro da faixa Q/L observada
                        'EVD_CLASSE': 'A_INTERIOR_QUADRA_LOTE', 'EVD_GRAU': 'SUSTENTADA',
                        'EVD_DISTANCIA': None, 'EVD_REGRA': 'gap_quadra_lote',
                        'EVD_DESCRICAO': f'{nivel} interpolado na faixa observada do logradouro',
                        'ACT_DESTINO_CAMPO': 'ENVIAR',
                        'STATUS': 'AUSENTE_INFERIDO', 'RADAR_CONF_COORD': 0,
                        'CENTROIDE_LAT': clat, 'CENTROIDE_LON': clon})

    def _id_fiel(rows, df):
        """R8/PDCA-01 — NENHUM identificador passa por float64.

        `pd.DataFrame(list_of_dicts)` infere float64 quando a coluna mistura
        int com None (aqui: gap QUADRA_LOTE, o "lote inferido sem pai único",
        que zera TANTO RADAR_ID_ENDERECO QUANTO ID_COLETIVA). Acima de 2^53 o
        valor é ARREDONDADO em silêncio (…996 -> …888) e o join com o
        endereço-pai morre.

        v4.5.3: reconstrói TODA coluna de identificador, não só a que
        estourou primeiro. A varredura R8 provou o ponto — o fix pontual
        do PDCA-01 cobria RADAR_ID_ENDERECO e deixava ID_COLETIVA em float
        no mesmo frame, pela mesma causa. Defeito de padrão pede fix de
        padrão.
        """
        for c in colunas_identificador(df):
            df[c] = pd.Series(id_seguro([r.get(c) for r in rows]), index=df.index)
        return df

    simples_df = (_id_fiel(simples_rows, pd.DataFrame(simples_rows))[COLS_SIMPLES].sort_values(
                     ['TIPO_SIMPLES', 'QTD_ECONOMIAS', 'QTD_COMERCIAL'], ascending=[True, False, False])
                  if simples_rows else pd.DataFrame(columns=COLS_SIMPLES))
    par_faltante_df = (_id_fiel(gap_rows, pd.DataFrame(gap_rows))[COLS_PAR_FALTANTE].sort_values(
                          ['CONF_GAP', 'GAP_TIPO', 'N_AUSENTES'], ascending=[True, True, False])
                       if gap_rows else pd.DataFrame(columns=COLS_PAR_FALTANTE))
    flags_df = (pd.DataFrame(flag_rows).drop_duplicates('ID_COLETIVA')
                if flag_rows else pd.DataFrame(columns=['ID_COLETIVA', 'FLAG_USO_MISTO', 'FLAG_FACHADA_ATIVA']))
    return simples_df, par_faltante_df, flags_df


# ════════════════════════════════════════════════════════════════════
#  DICIONÁRIO DE DADOS — conteúdo canônico da aba "Dicionario"
# ════════════════════════════════════════════════════════════════════
def construir_dicionario():
    """Conteúdo canônico da aba Dicionario da saída de potenciais.

    Retorna (intro_lines, df) onde df tem colunas
    [ABA, COLUNA, TIPO, DESCRICAO, VALORES] — uma linha por coluna de cada aba.
    """
    intro = [
        'Saída determinística de potenciais de individualização e oportunidade comercial, '
        'a partir da base de entrada processada. Identificação, safra, hash e estado de '
        'verificação da FONTE: consulte o manifest de linhagem (<saida>.manifest.json) — '
        'a origem nunca é afirmada aqui dentro.',
        'NATUREZA DO PRODUTO: motor de DESCOBERTA DE CANDIDATOS. Fato observado e hipótese '
        'inferida NUNCA se misturam (aba Hipoteses_Expansao é separada e requer confirmação); '
        'decisão operacional exige validação externa (cadastro comercial, vistoria).',
        'GATE universal (toda oportunidade): NUMERO>0, coordenada sã (sem ERRO_GEOMETRIA) e '
        'sem ANOMALIA DE COLETA (padrão atípico sinalizado pelo motor; fraude exige validação '
        'externa). Registros fora do gate não viram potencial.',
        'Confiabilidade é TRAVA: Nao_Residencial e polos exigem faixa de localização ALTA+. '
        'As faixas são score heurístico de completude de evidência — calibração contra verdade '
        'externa pendente.',
        'Coletiva vertical e subdivisão de lote (frente/fundos) = ALVO de individualização '
        '(1 ligação -> N economias). Condomínio horizontal: estrutura física NÃO prova a situação '
        'hidrométrica — SITUACAO_MEDICAO=DESCONHECIDA; cruzar com o cadastro comercial decide.',
        'Neutralidade de fonte (R5): rótulos, colunas e descrições no padrão radar_*, sem '
        'referência à origem dos dados. NÃO é anonimização de dados pessoais — endereço, '
        'coordenada e nome de estabelecimento permanecem. Linhagem técnica no manifest .json.',
        'Coordenadas em WGS84 (EPSG:4326). Use a coluna ABA para filtrar o dicionário por aba.',
    ]

    d_xid = ('Chave numérica determinística da REPRESENTAÇÃO CANÔNICA do endereço (N13): '
             'BLAKE2b-64 (63 bits) de MUN(7) | TIPO+NOME normalizados | N<numero> (ou QL '
             '<quadra/lote>) | LOCALIDADE normalizada — sem acento, caixa alta, só A-Z/0-9. '
             'Mesma REPRESENTAÇÃO gera o MESMO id em qualquer execução/safra; NÃO é identidade '
             'física permanente: mudança de nome de logradouro/localidade muda o id (entity '
             'resolution persistente = camada posterior). Municípios distintos nunca colidem; '
             'injetividade verificada pelo gate a cada run.')
    v_xid = ('inteiro de até 19 dígitos, gravado como TEXTO no Excel (>2^53 estouraria o '
             'float64 da célula); em banco, BIGINT. Vazio quando NUM=0 sem chave quadra/lote '
             '(sem identidade rastreável, R1).')
    d_xib = ('Chave numérica estável do grão BLOCO/TORRE: id64 da chave canônica do endereço '
             '+ " BL <bloco normalizado>" ("-" quando sem bloco).')

    d_logr = 'Nome do logradouro, harmonizado por moda dentro do CEP.'
    d_num = 'Número do endereço.'
    d_loc = 'Bairro/localidade do registro.'
    d_cep = 'CEP de 8 dígitos quando disponível.'
    d_setor = 'Código do setor censitário.'
    d_clat = 'Latitude do centróide do agrupamento (WGS84).'
    d_clon = 'Longitude do centróide do agrupamento (WGS84).'
    v_num = '>0 (número 0 é excluído pelo gate)'
    v_cep = '8 dígitos ou vazio'
    v_dash = '-'

    R = []  # (ABA, COLUNA, TIPO, DESCRICAO, VALORES)

    A = 'Coletivas'
    R += [
        (A, 'CLASSE', 'texto', 'Classe da coletiva OBSERVADA (hipóteses inferidas ficam na aba Hipoteses_Expansao).',
         'GRANDE = coletiva formal de porte (vertical/multibloco ou >=4 unidades, tipologia alta); '
         'PEQUENA = prédio/conjunto formal menor; SIMPLES = 2 economias compartilhando o lote/ligação.'),
        (A, 'ID_COLETIVA', 'texto', 'Identificador do agrupamento NESTE run (sequencial; muda entre execuções — para rastreamento use RADAR_ID_ENDERECO).', 'radar_*'),
        (A, 'RADAR_ID_ENDERECO', 'inteiro 19díg (texto no Excel)', d_xid, v_xid),
        (A, 'RADAR_ID_BLOCO', 'inteiro 19díg (texto no Excel)', d_xib, v_xid),
        (A, 'LOGRADOURO', 'texto', d_logr, v_dash),
        (A, 'NUMERO', 'inteiro', d_num, v_num),
        (A, 'LOCALIDADE', 'texto', d_loc, v_dash),
        (A, 'CEP', 'texto', d_cep, v_cep),
        (A, 'COD_SETOR', 'texto', d_setor, v_dash),
        (A, 'TIPO', 'texto',
         'Tipologia da coletiva: rótulo do classificador (GRANDE/PEQUENA) ou tipo de subdivisão (SIMPLES).',
         'GRANDE/PEQUENA: Condomínio vertical multiblocos, Edifício de apartamentos, Edifício com '
         'unidades, Condomínio com via interna, etc. SIMPLES: FRENTE_FUNDOS, SOBRADO, CASA_MULTIPLA, '
         'TERREO_SUPERIOR, LADO, ANEXO, DUPLA_GENERICA.'),
        (A, 'QTD_ECONOMIAS', 'inteiro',
         'Nº de economias/unidades OBSERVADAS no bloco/endereço — grandeza única e agregável '
         'em todas as classes desta aba (v4.5).', '>=1'),
        (A, 'DETALHE', 'texto',
         'Detalhamento: composição estrutural (Nbl/Nand/Nun/Ncasas) para GRANDE/PEQUENA; '
         'marcadores do complemento para SIMPLES.', v_dash),
        (A, 'FLAG_USO_MISTO', '0/1', 'Comércio e residência convivendo no mesmo endereço/lote.', '0 ou 1'),
        (A, 'FLAG_FACHADA_ATIVA', '0/1', 'Comércio no térreo com residência nos pavimentos superiores.', '0 ou 1'),
        (A, 'MULT_ESTAB_DECL', 'texto',
         'Multiplicidade de estabelecimentos DECLARADA pelo recenseador no endereço '
         '(anotação oficial da fonte, pior caso do bloco — não é inferência).',
         'Único; Múltiplo (até 10); Múltiplo (mais de 10); Múltiplo (qtd desconhecida); vazio'),
        (A, 'VALIDACAO_ESTAB_DECL', 'texto',
         'Veredito do confronto declarado×contado (estrutura decide, declaração valida): '
         'a contagem estrutural bate com a faixa declarada pelo recenseador?',
         'CONFIRMADO; DUVIDA_SUB_ENUMERACAO (campo viu mais que a base — verificar, nunca '
         'descartar); DUVIDA_SUPER_CONTAGEM (declaração curta/defasada); SEM_DECLARACAO'),
        (A, 'CONF_TIPOLOGIA', 'texto',
         'Confiança da CLASSIFICAÇÃO tipológica (dimensão separada da confiança de '
         'localização RADAR_CONF_* e da confiança de inferência das hipóteses).',
         'ALTA = tipologia confirmada + coordenada validada; MEDIA = evidência parcial ou coordenada '
         'estimada; BAIXA = agrupamento sem âncora estrutural confiável.'),
        (A, 'CENTROIDE_LAT', 'decimal', d_clat, v_dash),
        (A, 'CENTROIDE_LON', 'decimal', d_clon, v_dash),
    ]

    A = 'Hipoteses_Expansao'
    R += [
        (A, 'RADAR_ID_ENDERECO', 'inteiro 19díg (texto no Excel)',
         'Id do endereço-PAI observado que gerou a hipótese (rastreio da confirmação futura).', v_xid),
        (A, 'TIPO_HIPOTESE', 'texto', 'Tipo de lacuna que gerou a inferência.',
         'POSICIONAL (frente sem fundos); SEQUENCIA; GRADE_APTO; QUADRA_LOTE.'),
        (A, 'EVIDENCIA', 'texto', 'Unidades PRESENTES que sustentam a hipótese.', v_dash),
        (A, 'UNIDADE_INFERIDA', 'texto', 'Unidade AUSENTE inferida — HIPÓTESE, não fato: a lacuna '
         'pode ser praça, lote unido, área institucional ou numeração descontinuada.', v_dash),
        (A, 'N_AUSENTES', 'inteiro', 'Total de ausentes inferidos no mesmo cluster.', '>=1'),
        (A, 'CONF_INFERENCIA', 'texto', 'Confiança DA INFERÊNCIA (regra de gap), não do imóvel.',
         'ALTA / MEDIA / BAIXA'),
        (A, 'REGRA_ORIGEM', 'texto', 'Função de gap que gerou a linha (auditável).',
         'gap_posicional / gap_linear / gap_grade / gap_quadra_lote'),
        (A, 'REQUER_CONFIRMACAO', 'texto',
         'SEMPRE SIM — hipótese nunca entra em decisão operacional sem confirmação '
         '(cadastro comercial, vistoria ou campo). Nunca somar com abas observadas.', 'SIM'),
        (A, 'RADAR_CONF_COORD', 'inteiro', 'SEMPRE 0 — coordenada nunca é fabricada para inferido '
         '(EIXO1); o centróide exibido é do bloco-pai.', '0'),
    ]

    A = 'Condominios_Horizontais'
    R += [
        (A, 'ID_COLETIVA', 'texto', 'Identificador do condomínio horizontal NESTE run.', 'radar_*'),
        (A, 'RADAR_ID_ENDERECO', 'inteiro 19díg (texto no Excel)', d_xid, v_xid),
        (A, 'RADAR_ID_BLOCO', 'inteiro 19díg (texto no Excel)', d_xib, v_xid),
        (A, 'LOGRADOURO', 'texto', d_logr, v_dash),
        (A, 'NUMERO', 'inteiro', d_num, v_num),
        (A, 'LOCALIDADE', 'texto', d_loc, v_dash),
        (A, 'CEP', 'texto', d_cep, v_cep),
        (A, 'COD_SETOR', 'texto', d_setor, v_dash),
        (A, 'TIPO_COND', 'texto', 'Rótulo do classificador para o condomínio horizontal.',
         'Vila ou condomínio horizontal (casas); Condomínio horizontal com múltiplos blocos/vias; '
         'Múltiplas moradias no mesmo endereço (casa/lote/sobrado).'),
        (A, 'QTD_CASAS', 'inteiro', 'Nº de casas/unidades no condomínio.', v_dash),
        (A, 'SEQ_MIN', 'inteiro', 'Menor número na sequência de casas, quando detectada.', 'vazio se sem sequência'),
        (A, 'SEQ_MAX', 'inteiro', 'Maior número na sequência de casas, quando detectada.', 'vazio se sem sequência'),
        (A, 'GAP', 'inteiro', 'Casas ausentes na sequência do loteamento (lacuna).', '>=0 ou vazio'),
        (A, 'CONF', 'texto', 'Confiança da classificação.', 'ALTA / MEDIA / BAIXA'),
        (A, 'N_ECONOMIAS', 'inteiro', 'Total de economias no endereço.', v_dash),
        (A, 'CENTROIDE_LAT', 'decimal', d_clat, v_dash),
        (A, 'CENTROIDE_LON', 'decimal', d_clon, v_dash),
        (A, 'SITUACAO_MEDICAO', 'texto',
         'v4.5: a estrutura física NÃO prova a situação hidrométrica — o dado sabe que há casas '
         'agrupadas, não quantos hidrômetros existem.', 'DESCONHECIDA (estrutura nao prova hidrometro)'),
        (A, 'ACAO_RECOMENDADA', 'texto',
         'Onde nasce o valor: 6 casas/1 ligação = oportunidade enorme; 6 casas/6 ligações = '
         'individualizado.', 'CRUZAR_COM_CADASTRO_COMERCIAL'),
    ]

    A = 'Nao_Residencial'
    R += [
        (A, 'COD_UNICO_ENDERECO', 'texto', 'Identificador único do registro na fonte.', 'radar_*'),
        (A, 'RADAR_ID_ENDERECO', 'inteiro 19díg (texto no Excel)', d_xid, v_xid),
        (A, 'DSC_LOCALIDADE', 'texto', d_loc, v_dash),
        (A, 'LOGRADOURO', 'texto', d_logr, v_dash),
        (A, 'NUMERO', 'inteiro', d_num, v_num),
        (A, 'COMPLEMENTO_NORM', 'texto', 'Complemento normalizado (bloco -> pavimento -> unidade).', v_dash),
        (A, 'CEP', 'texto', d_cep, v_cep),
        (A, 'COD_SETOR', 'texto', d_setor, v_dash),
        (A, 'LATITUDE', 'decimal', 'Latitude do registro (WGS84).', v_dash),
        (A, 'LONGITUDE', 'decimal', 'Longitude do registro (WGS84).', v_dash),
        (A, 'NV_GEO_COORD', 'inteiro', 'Nível de qualidade da coordenada original.',
         '1-2 = validada (face/edificação); 3 = estimada; 4-6 = baixa qualidade.'),
        (A, 'QUALIDADE_COORD', 'texto', 'Rótulo da qualidade da coordenada.', 'VALIDADA / ESTIMADA / BAIXA'),
        (A, 'SETOR_ATIVIDADE', 'texto', 'Setor de atividade (espécie oficial + léxico curado de palavras-chave).',
         'RESIDENCIAL, COMERCIO_VAREJO, COMERCIO_SERVICO, ALIMENTACAO, SAUDE, EDUCACAO, BELEZA_ESTETICA, '
         'SERVICO_PROF, SERVICO_AUTO, HOSPEDAGEM, INDUSTRIAL, MANUFATURA, CONSTRUCAO, etc.; '
         'DESCONHECIDO quando sem match.'),
        (A, 'ATIVIDADE_DETALHE', 'texto', 'Atividade específica inferida (detalhe do setor).', v_dash),
        (A, 'DSC_ESTABELECIMENTO', 'texto', 'Descrição do estabelecimento (anonimizada).', v_dash),
        (A, 'DSC_ESPECIE', 'texto', 'Espécie do endereço.',
         'Domicílio particular; Domicílio coletivo; Estabelecimento (agropecuário/ensino/saúde/outros); '
         'Edificação em construção; Religioso.'),
        (A, 'FLAG_USO_MISTO', '0/1',
         'Residencial e não-residencial convivendo no MESMO endereço (chave logradouro+número).',
         '0 ou 1'),
        (A, 'DSC_MULT_ESTAB', 'texto',
         'Multiplicidade de estabelecimentos DECLARADA pelo recenseador neste registro '
         '(anotação oficial da fonte — percepção direta de estabelecimentos agrupados).',
         'Único; Múltiplo (até 10); Múltiplo (mais de 10); Múltiplo (qtd desconhecida); vazio'),
        (A, 'QTD_ESTAB_CONTADA', 'inteiro',
         'Nº de registros de estabelecimento (espécies 3/4/5/6/8) CONTADOS pela estrutura '
         'no mesmo endereço (CHAVE).', '>=0'),
        (A, 'VALIDACAO_ESTAB_DECL', 'texto',
         'Veredito do confronto declarado×contado (estrutura decide, declaração valida).',
         'CONFIRMADO; DUVIDA_SUB_ENUMERACAO (campo viu mais que a base — alvo de verificação); '
         'DUVIDA_SUPER_CONTAGEM; SEM_DECLARACAO'),
        (A, 'RADAR_CONF_LOCALIZACAO', 'inteiro 0-100', 'Confiabilidade de localização em 3 eixos.',
         'EIXO1 coordenada (0-40) + EIXO2 endereço (0-30) + EIXO3 contexto (0-30) = 0-100.'),
        (A, 'RADAR_CONF_FAIXA', 'texto', 'Faixa da confiabilidade de localização.',
         'MUITO_ALTA >75; ALTA >55; MEDIA >35; BAIXA <=35.'),
    ]

    A = 'Polos_Comerciais'
    R += [
        (A, 'POLO_ID', 'inteiro',
         'Identificador do polo (cluster DBSCAN por densidade de ENDEREÇOS, sem peso — '
         'min_samples = mínimo de endereços, literal).', '>=0'),
        (A, 'QTD_END', 'inteiro', 'Nº de endereços no polo.', v_dash),
        (A, 'QTD_ESTAB', 'inteiro', 'Nº de estabelecimentos no polo.', v_dash),
        (A, 'SETOR_DOMINANTE', 'texto', 'Setor predominante do polo.', v_dash),
        (A, 'SETORES', 'texto', 'Setores presentes no polo (concatenados).', v_dash),
        (A, 'CENTROIDE_LAT', 'decimal', 'Latitude do centróide do polo (WGS84).', v_dash),
        (A, 'CENTROIDE_LON', 'decimal', 'Longitude do centróide do polo (WGS84).', v_dash),
        (A, 'CLASSE_POLO', 'texto', 'Porte do polo comercial.',
         'ZONA_CENTRAL >=500 endereços; POLO_REGIONAL >=50; POLO_LOCAL <50.'),
        (A, 'POLIGONO_WKT', 'texto (WKT)',
         'Coordenadas que formam o polígono da área do polo (convex hull dos endereços-membros), '
         'em WKT POLYGON lon/lat. Carregável como geometria no QGIS.',
         'POLYGON ((lon lat, ...)) em EPSG:4326'),
    ]

    df = pd.DataFrame(R, columns=['ABA', 'COLUNA', 'TIPO', 'DESCRICAO', 'VALORES'])
    return intro, df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  ANOTAÇÕES DECISÓRIAS DA FONTE — indicadores oficiais (N16)  ║
# ╚═══════════════════════════════════════════════════════════════╝
# Anotação DIRETA do recenseador sobre multiplicidade agrupada no endereço —
# percepção de "quantos estabelecimentos/construções" sem inferência nossa.

IND_ESTAB_DESC = {          # COD_INDICADOR_ESTAB_ENDERECO
    1: 'Único', 2: 'Múltiplo (até 10)', 3: 'Múltiplo (mais de 10)',
    4: 'Múltiplo (qtd desconhecida)',
}
IND_CONST_DESC = {          # COD_INDICADOR_CONST_ENDERECO (espécie 7)
    1: 'Única', 2: 'Múltipla (até 10)', 3: 'Múltipla (mais de 10)',
    4: 'Múltipla (qtd desconhecida)',
}
FINALIDADE_CONST_DESC = {   # COD_INDICADOR_FINALIDADE_CONST (espécie 7)
    1: 'Residencial', 2: 'Não residencial', 3: 'Misto', 4: 'Indeterminado',
}

# Faixa de contagem esperada por código declarado (confronto declarado×contado)
_DECL_FAIXA_LO = {1: 1, 2: 2, 3: 11, 4: 2}
_DECL_FAIXA_HI = {1: 1, 2: 10, 3: np.inf, 4: np.inf}

# Espécies que são ESTABELECIMENTO no layout oficial (3 agro, 4 ensino,
# 5 saúde, 6 outras finalidades, 8 religioso)
_ESPECIES_ESTAB = (3, 4, 5, 6, 8)


def confrontar_declaracao_estab(df: pd.DataFrame) -> pd.DataFrame:
    """N16 — doutrina: ESTRUTURA DECIDE, DECLARAÇÃO VALIDA.

    A classificação estrutural (contagem/complementos/coordenada) permanece a
    autoridade; a anotação do recenseador entra como CHAVE DE VALIDAÇÃO OU
    DÚVIDA por endereço (CHAVE). Colunas geradas:
      DSC_MULT_ESTAB / DSC_MULT_CONST / DSC_FINALIDADE_CONST  declarações
        decodificadas (registro; NA quando a fonte não traz a coluna)
      QTD_ESTAB_CONTADA     nº de registros de estabelecimento no endereço
                            (espécies 3/4/5/6/8), contagem NOSSA
      VALIDACAO_ESTAB_DECL  veredito declarado×contado:
        CONFIRMADO             contagem dentro da faixa declarada
        DUVIDA_SUB_ENUMERACAO  declarado > contado — o campo VIU mais do que
                               a base registrou (alvo de verificação, nunca
                               descarte)
        DUVIDA_SUPER_CONTAGEM  contado > declarado — declaração curta ou
                               defasada
        SEM_DECLARACAO         indicador ausente/nulo
    NÃO altera classificação, gate nem score — auditoria e priorização.
    """
    for src, dst, mp in [
            ('COD_INDICADOR_ESTAB_ENDERECO', 'DSC_MULT_ESTAB', IND_ESTAB_DESC),
            ('COD_INDICADOR_CONST_ENDERECO', 'DSC_MULT_CONST', IND_CONST_DESC),
            ('COD_INDICADOR_FINALIDADE_CONST', 'DSC_FINALIDADE_CONST',
             FINALIDADE_CONST_DESC)]:
        df[dst] = (pd.to_numeric(df[src], errors='coerce').map(mp)
                   if src in df.columns else pd.NA)

    esp = pd.to_numeric(df['COD_ESPECIE'], errors='coerce')
    df['QTD_ESTAB_CONTADA'] = (
        df.assign(_e=esp.isin(_ESPECIES_ESTAB).astype(int))
          .groupby('CHAVE')['_e'].transform('sum').astype(int))

    if 'COD_INDICADOR_ESTAB_ENDERECO' not in df.columns:
        df['VALIDACAO_ESTAB_DECL'] = 'SEM_DECLARACAO'
        return df

    ind = pd.to_numeric(df['COD_INDICADOR_ESTAB_ENDERECO'], errors='coerce')
    decl = df.assign(_i=ind).groupby('CHAVE')['_i'].transform('max')
    lo = decl.map(_DECL_FAIXA_LO)
    hi = decl.map(_DECL_FAIXA_HI)
    n = df['QTD_ESTAB_CONTADA']
    df['VALIDACAO_ESTAB_DECL'] = np.select(
        [decl.isna(), (n >= lo) & (n <= hi), n < lo, n > hi],
        ['SEM_DECLARACAO', 'CONFIRMADO',
         'DUVIDA_SUB_ENUMERACAO', 'DUVIDA_SUPER_CONTAGEM'],
        default='SEM_DECLARACAO')
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  RADAR_ID — CHAVE NUMÉRICA ESTÁVEL DE RASTREAMENTO (N13)     ║
# ╚═══════════════════════════════════════════════════════════════╝

import hashlib


def canon_rastreio(s) -> str:
    """Caracteres PADRÃO da chave: sem acento, caixa alta, só [A-Z0-9],
    espaço único. É o pré-imagem determinística do id — variação de
    acentuação/caixa/pontuação NÃO muda o id."""
    s = strip_accents(str(s or '')).upper()
    s = re.sub(r'[^A-Z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


# ════════════════════════════════════════════════════════════════════════════
#  NUMERAL POR EXTENSO NA CANÔNICA  (CANON_VERSAO 2)
# ════════════════════════════════════════════════════════════════════════════
# Tabela PRÓPRIA. A skill `ferramenta-logradouro-padrao` chegou ao mesmo lugar
# por outro caminho; a metodologia é dela, os números abaixo são medidos NESTA
# base e a implementação é desta skill — importar o módulo de outra skill
# criaria uma dependência que o lacre não consegue versionar.
CANON_VERSAO = 3

_NUM_EXTENSO = {
    'ZERO': 0, 'UM': 1, 'UMA': 1, 'DOIS': 2, 'DUAS': 2, 'TRES': 3, 'QUATRO': 4,
    'CINCO': 5, 'SEIS': 6, 'SETE': 7, 'OITO': 8, 'NOVE': 9, 'DEZ': 10,
    'ONZE': 11, 'DOZE': 12, 'TREZE': 13, 'QUATORZE': 14, 'CATORZE': 14,
    'QUINZE': 15, 'DEZESSEIS': 16, 'DEZASSEIS': 16, 'DEZESSETE': 17,
    'DEZOITO': 18, 'DEZENOVE': 19, 'VINTE': 20, 'TRINTA': 30, 'QUARENTA': 40,
    'CINQUENTA': 50, 'CINCOENTA': 50, 'SESSENTA': 60, 'SETENTA': 70,
    'OITENTA': 80, 'NOVENTA': 90, 'CEM': 100, 'CENTO': 100,
    'DUZENTOS': 200, 'DUZENTAS': 200, 'TREZENTOS': 300, 'TREZENTAS': 300,
    'QUATROCENTOS': 400, 'QUATROCENTAS': 400, 'QUINHENTOS': 500,
    'QUINHENTAS': 500, 'SEISCENTOS': 600, 'SEISCENTAS': 600,
    'SETECENTOS': 700, 'SETECENTAS': 700, 'OITOCENTOS': 800, 'OITOCENTAS': 800,
    'NOVECENTOS': 900, 'NOVECENTAS': 900, 'MIL': 1000,
}
# 'MEIA' (=6 ao ditar telefone) fica DE FORA: em nome de via é 'meia-lua',
# 'meia-légua'. Ambiguidade que só o contexto resolve não entra em identidade.


def _classe_decimal(v: int) -> int:
    """Unidade/dezena/centena — para detectar numeral MAL FORMADO."""
    if v >= 1000:
        return 3
    if v >= 100:
        return 2
    if v >= 10:
        return 1
    return 0


def _numeral_extenso(tokens) -> int | None:
    """Numeral PT-BR por extenso → int, ou None se a janela não for numeral.

    A guarda de BOA-FORMAÇÃO é o que separa isto de uma soma ingênua:
    `atual += v` transforma 'SESSENTA SESSENTA' em 120, e em Porto Alegre isso
    fundiria a rua 'SESSENTA SESSENTA' com a rua '120' — duas ruas viram uma e
    ninguém percebe. Numeral bem formado NÃO repete a mesma classe decimal.
    """
    toks = [t for t in tokens if t and t != 'E']
    if not toks or any(t not in _NUM_EXTENSO for t in toks):
        return None
    total = atual = 0
    vistas = set()
    for t in toks:
        v = _NUM_EXTENSO[t]
        if v == 1000:
            if 3 in vistas:
                return None                       # 'MIL ... MIL'
            vistas.add(3)
            total += (atual or 1) * 1000
            atual = 0
            vistas -= {0, 1, 2}
            continue
        c = _classe_decimal(v)
        if c in vistas:
            return None                           # classe repetida: mal formado
        vistas.add(c)
        atual += v
    val = total + atual
    return val if val > 0 else None


def expandir_numerais(s: str) -> str:
    """Numeral por extenso → dígito no NOME DA VIA. Romano NÃO é convertido.

    Medido no CNEFE de Porto Alegre (6.876 nomes distintos): o extenso rende
    66 fusões, TODAS a mesma via escrita de dois jeitos ('25 DE JULHO' /
    'VINTE E CINCO DE JULHO'), fator de colisão 1,0096×. O romano rende UMA
    fusão legítima ('XV DE NOVEMBRO') e estraga nomes reais — 'ANNIBAL DI
    PRIMIO BECK' vira 'ANNIBAL 501 PRIMIO BECK' (D+I) e 'BEM TE VI' vira 'BEM
    TE 6'. Numa pré-imagem PUBLICADA isso é errado na cara de quem lê. O XV
    vira evidência graduada, não identidade.
    """
    toks = str(s or '').split()
    out, i, n = [], 0, len(toks)
    while i < n:
        j, win = i, []
        while j < n and (toks[j] in _NUM_EXTENSO or
                         (toks[j] == 'E' and win and j + 1 < n
                          and toks[j + 1] in _NUM_EXTENSO)):
            win.append(toks[j]); j += 1
        if win:
            v = _numeral_extenso(win)
            if v is not None:
                out.append(str(v)); i = j; continue
            # A SEQUÊNCIA INTEIRA sai como veio. Converter só o pedaço que por
            # acaso fecha ('SESSENTA SESSENTA' → 'SESSENTA 60') é pior que não
            # converter: inventa um número no meio de um nome que ninguém
            # entendeu. Não entendeu, não mexe.
            out.extend(win); i = j; continue
        out.append(toks[i]); i += 1
    return ' '.join(out)


# ── VOCABULÁRIO CANÔNICO DE TIPO DE VIA ────────────────────────────────────
# Serve para APONTAR, nunca para corrigir: o IBGE é a fonte, e um tipo que não
# está aqui significa que a lista está incompleta — não que o dado está errado.
# Base: tipos auditados contra o CNEFE em 7 UFs (PI, CE, MA, AM, RN, PE, RJ)
# pela metodologia da `ferramenta-logradouro-padrao`, MAIS os 10 que Porto
# Alegre revelou e que aquelas UFs não tinham. Cada base nova amplia a lista.
LOGR_TIPOS = {
    'AVENIDA', 'RUA', 'TRAVESSA', 'ALAMEDA', 'PRACA', 'RODOVIA', 'ESTRADA',
    'ACESSO', 'ACAMPAMENTO', 'NUCLEO', 'LOTEAMENTO', 'PROLONGAMENTO',
    'RECANTO', 'JARDIM', 'SERVIDAO', 'DESVIO', 'PASSEIO', 'PASSAGEM',
    'PASSARELA', 'CORREDOR', 'CORREGO', 'CONJUNTO', 'PARQUE', 'CONDOMINIO',
    'BECO', 'LARGO', 'VILA', 'VIELA', 'LADEIRA', 'ESCADARIA', 'VIADUTO',
    'MARGINAL', 'CONTORNO', 'DISTRITO', 'COLONIA', 'CHACARA', 'SITIO',
    'FAZENDA', 'GLEBA', 'QUADRA', 'CAMPO', 'PATIO', 'TERMINAL', 'ESTACAO',
    'PORTO', 'CAIS', 'MORRO', 'VEREDA', 'TRECHO', 'ANEL', 'ELEVADO', 'VIA',
    'POVOADO', 'LUGAREJO', 'COMUNIDADE', 'ASSENTAMENTO', 'AGLOMERADO',
    'VILAREJO', 'SEDE', 'ENTRONCAMENTO', 'CAMINHO', 'RUELA', 'ENTRADA',
    'SAIDA', 'PROPRIEDADE', 'SERRA', 'CHAPADA', 'LAGOA', 'ALTO', 'BAIXAO',
    'BAIXA', 'RESIDENCIAL', 'SETOR', 'RIO', 'IGARAPE', 'LAGO', 'RAMAL',
    'ALDEIA', 'AGROVILA', 'PRAIA', 'RIACHO', 'BREJO', 'ENGENHO', 'ESCADAO',
    'SUBIDA', 'BOULEVARD', 'ILHA', 'RETORNO', 'ROTULA', 'ESPLANADA',
    # ── revelados pelo CNEFE de Porto Alegre/RS (não apareciam nas 7 UFs) ──
    'AREA', 'BARRO', 'CONJUNTO HABITACIONAL', 'ESTANCIA', 'HABITACIONAL',
    'LIGACAO', 'PONTE', 'RUA DE PEDESTRE', 'RUA PRINCIPAL', 'TRILHA',
    'ESCADA', 'TRAVESSIA',
}
# Teto do fator de colisão de logradouro. Medido em POA sob a canônica v2:
# 1,0096× só por numeral, ~1,012× com o título fora. 1,05 dá folga de 4× a
# variação observada e ainda barra uma canonicalização que comece a fundir
# ruas de verdade — que é a única coisa que este número existe para impedir.
TETO_COLISAO_LOGR = 1.05


# ── CHAVE FONÉTICA PT-BR — EVIDÊNCIA, JAMAIS IDENTIDADE ────────────────────
# Medido no CNEFE de POA: 71 grupos, fator 1,0104×. Acerta 'AYRTON SENNA' ≡
# 'AIRTON SENA' e 'ARTHUR BOTTONA' ≡ 'ARTUR BOTONA' — e FUNDE 'CEFER I' com
# 'CEFER II', que são ruas diferentes. Por isso não encosta no id: a fonética
# entra graduada, como toda hipótese desta skill (R9), e quem decide é o campo.
_FON_DIGRAFOS = (('CH', 'X'), ('PH', 'F'), ('SH', 'X'), ('LH', 'L'),
                 ('NH', 'N'), ('QU', 'K'))


def fonetica_token(tok: str) -> str:
    t = strip_accents(str(tok or '')).upper()
    if not t.isalpha():
        return t                                   # número fica intacto
    for a, b in _FON_DIGRAFOS:
        t = t.replace(a, b)
    t = re.sub(r'G(?=[EI])', 'J', t)
    t = re.sub(r'C(?=[EI])', 'S', t)
    t = (t.replace('Q', 'K').replace('W', 'V').replace('Y', 'I')
          .replace('Z', 'S').replace('H', ''))
    return re.sub(r'(.)\1+', r'\1', t)              # letra dobrada → simples


def fonetica_frase(s) -> str:
    return ' '.join(fonetica_token(t) for t in str(s or '').split())


_ROMANO_RE = re.compile(r'^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$')
_RVAL = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}


_RE_ENUMERADOR = re.compile(r'^(?:\d+|[A-Z]|[A-Z]{1,2}\d+|\d+[A-Z])$')


def _enumerador(tok: str) -> bool:
    """Token cuja função é ENUMERAR — nunca é grafia alternativa do vizinho."""
    return bool(_RE_ENUMERADOR.match(str(tok or '').strip().upper()))


def romano_para_int(tok: str):
    """Romano → int. Usado SÓ na marcação; nunca na canônica (ver D19)."""
    t = str(tok or '').upper()
    if not t or not _ROMANO_RE.match(t):
        return None
    total = prev = 0
    for ch in reversed(t):
        v = _RVAL[ch]
        total += -v if v < prev else v
        prev = v
    return total or None


def chave_equivalencia(slot_logradouro: str) -> str:
    """Chave de CANDIDATURA a equivalência — fonética + romano resolvidos.

    É o canal onde entra tudo que NÃO pode entrar na identidade: a fonética
    (que confunde ordinal) e o romano (que estraga 'DI'/'VI' em nome real).
    Aqui isso é barato: gerar candidato é de graça, o que custa é confirmar —
    e quem confirma é a geometria e a numeração, não a string.
    """
    toks = []
    for t in canon_rastreio(slot_logradouro).split():
        r = romano_para_int(t) if len(t) >= 2 else None
        if r is not None:
            toks.append(str(r))            # XV e 15 são o mesmo VALOR
        elif _enumerador(t):
            # ENUMERADOR NÃO É FONETIZADO. `ACESSO S` e `ACESSO Z` viravam a
            # mesma chave (Z→S) e saíam marcados como SUSTENTADOS: são vias
            # irmãs de loteamento. Um token que existe para distinguir irmãos
            # não pode ser aproximado de nenhum outro.
            toks.append(t)
        else:
            toks.append(fonetica_token(t))
    return ' '.join(toks)


def _tipo_de_via(logradouro) -> str:
    """Primeiro token (ou os dois primeiros, p/ tipos compostos) do logradouro."""
    t = canon_rastreio(logradouro).split()
    if not t:
        return ''
    if len(t) >= 2 and ' '.join(t[:2]) in LOGR_TIPOS:
        return ' '.join(t[:2])
    return t[0]


def colisao_logradouro(formas, canonicas) -> dict:
    """Quantas grafias DISTINTAS a canonicalização funde numa só.

    Instrumento, não medição de ocasião. Toda vez que a canônica muda, alguém
    tem de responder "isso funde ruas de verdade?" — e sem número fixo a
    resposta volta como argumento. `fator` 1,0 = nenhuma fusão; quanto maior,
    mais grafias colapsam. Fundir grafia é o objetivo; fundir RUA é o defeito,
    e por isso os grupos fundidos saem listados.
    """
    import collections
    mapa = collections.defaultdict(set)
    for f, c in zip(formas, canonicas):
        f = str(f or '').strip()
        c = str(c or '').strip()
        if f and c:
            mapa[c].add(canon_rastreio(f))
    n_formas = len({x for s in mapa.values() for x in s})
    n_canon = len(mapa)
    fundidos = sorted((c, sorted(s)) for c, s in mapa.items() if len(s) > 1)
    return {
        'formas_distintas': int(n_formas),
        'canonicas_distintas': int(n_canon),
        'fator': round(n_formas / n_canon, 4) if n_canon else 1.0,
        'grupos_fundidos': int(len(fundidos)),
        'exemplos': [{'canonica': c, 'formas': f[:4]} for c, f in fundidos[:5]],
    }


def canon_logradouro(s, versao: int = CANON_VERSAO) -> str:
    """Canonicalização do SLOT DE LOGRADOURO — versionada.

    A expansão de numeral vale AQUI e só aqui: é no nome da via que a
    equivalência dígito×extenso está medida. Aplicá-la à localidade mudaria
    'TRES FIGUEIRAS' para '3 FIGUEIRAS' sem nenhuma evidência de que alguém
    escreve o bairro dos dois jeitos.
    """
    base = canon_rastreio(s)
    return expandir_numerais(base) if versao >= 2 else base


def id64_rastreio(canonico: str) -> int:
    """BLAKE2b (8 bytes) da chave canônica → int64 POSITIVO (63 bits).
    Estável entre execuções, processos, máquinas e versões de Python
    (nunca usar hash() nativo: é salteado por processo).

    COLISÃO: embutir o município garante que as CHAVES CANÔNICAS sejam
    distintas — NÃO que os hashes sejam. Em 63 bits, pela aproximação do
    aniversário, 40 milhões de chaves dão ~8,7e-5 (0,0087%) de probabilidade
    de ao menos uma colisão. Baixa, não nula: por isso N13 verifica
    injetividade e ABORTA (remédio: digest_size=12 e armazenamento em texto).
    Dizer "NUNCA colide" seria trocar um gate executável por uma promessa."""
    d = hashlib.blake2b(canonico.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(d, 'big') >> 1


def _serie_id_nullable(canonica, mask, lut, index):
    """Mapeia chave -> id SEM passar por float64 (R8).

    O `.map()` do pandas sobre uma Series com pd.NA devolve float64 e destrói
    silenciosamente qualquer inteiro acima de 2^53. A única forma segura e'
    mapear apenas as posicoes com valor e escrever num array Int64 nullable.
    """
    out = pd.Series(pd.NA, index=index, dtype='Int64')
    if mask.any():
        out.loc[mask] = pd.array(
            [lut[k] for k in canonica[mask]], dtype='Int64')
    return out


def gerar_id_rastreio(df: pd.DataFrame) -> pd.DataFrame:
    """Gera a chave numérica não-repetitiva de rastreamento futuro.

    RADAR_CHAVE_CANONICA = MUN(7)|LOGR_NORM|IDENT|LOC_NORM, onde IDENT:
      NUMERO>0            -> 'N<numero>'
      Padrão B (Q/L/C)    -> 'QL <chave canônica Q/L>'
      NUM=0 sem Q/L       -> chave NULA (R1: sem identidade rastreável)
    RADAR_ID_ENDERECO = id64(canônica)            [grão endereço/cluster]
    RADAR_ID_BLOCO    = id64(canônica + ' BL <bloco>')  [grão bloco/torre]

    Mesma REPRESENTAÇÃO canônica → mesmo id em qualquer execução ou safra;
    NÃO é identidade física permanente (rename de rua/localidade muda o id
    — entity resolution persistente é camada posterior). Municípios
    distintos nunca colidem (MUN embutido). Excel deve gravar como TEXTO
    (19 dígitos > 2^53 estoura float64); em banco, BIGINT.
    """
    def _col(nome):
        return (df[nome] if nome in df.columns
                else pd.Series([''] * len(df), index=df.index))

    mun = (pd.to_numeric(df['COD_MUNICIPIO'], errors='coerce')
           .fillna(0).astype('int64').astype(str).str.zfill(7))
    logr_src = (df['NOM_SEGLOGR_HARM'] if 'NOM_SEGLOGR_HARM' in df.columns
                else df['NOM_SEGLOGR'])
    # O TÍTULO ENTRA NA IDENTIDADE (v3). A CHAVE operacional já o levava desde
    # a v4.8; o identificador não. Duas entidades que o pipeline tratava como
    # distintas recebiam o MESMO id — 7 casos no CNEFE real de Porto Alegre,
    # entre eles 'RUA BARAO DO GRAVATAI' e 'RUA BARONESA DO GRAVATAI', que são
    # duas ruas da cidade. O gate N13 não podia pegar: as duas linhas viravam
    # a MESMA canônica ANTES do hash, então para ele não havia colisão.
    tit_src = (df['NOM_TITULO_HARM'] if 'NOM_TITULO_HARM' in df.columns
               else _col('NOM_TITULO_SEGLOGR'))
    _tipo_s = _col('NOM_TIPO_SEGLOGR').fillna('').astype(str)
    _nome_s = logr_src.fillna('').astype(str)
    _logr_v2 = _tipo_s + ' ' + _nome_s
    _logr_bruto = _tipo_s + ' ' + tit_src.fillna('').astype(str) + ' ' + _nome_s
    # v2 continua calculada: é o que liga esta safra à entrega anterior.
    # Trocar a canônica sem emitir a ponte quebraria em silêncio o
    # rastreamento safra a safra de 279 mil endereços.
    logr_v1 = _logr_v2.map(canon_logradouro)
    logr = _logr_bruto.map(canon_logradouro)
    loc = _col('DSC_LOCALIDADE').fillna('').astype(str).map(canon_rastreio)
    num = pd.to_numeric(df['NUM_ENDERECO'], errors='coerce').fillna(0).astype('int64')
    ql = _col('CHAVE_QUADRA_LOTE').fillna('').astype(str).map(canon_rastreio)

    ident = pd.Series(pd.NA, index=df.index, dtype=object)
    ident.loc[num > 0] = 'N' + num[num > 0].astype(str)
    m_ql = (num <= 0) & (ql != '')
    ident.loc[m_ql] = 'QL ' + ql[m_ql]

    canonica = pd.Series(pd.NA, index=df.index, dtype=object)
    mv = ident.notna()
    canonica[mv] = (mun[mv] + '|' + logr[mv] + '|' +
                    ident[mv].astype(str) + '|' + loc[mv])
    df['RADAR_CHAVE_CANONICA'] = canonica

    # hash só nos valores ÚNICOS (custo linear no nº de endereços distintos)
    uniq = pd.Series(canonica[mv].unique())
    lut = dict(zip(uniq, uniq.map(id64_rastreio)))
    # R8, 4ª instância do PDCA-01 — e a mais cara, porque é a LINHA QUE CRIA o
    # identificador. `canonica.map(lut)` sobre a Series INTEIRA (com pd.NA nas
    # linhas sem identidade) infere float64: todo id acima de 2^53 perde dígito
    # e o `.astype('Int64')` seguinte CARIMBA o valor corrompido como exato.
    # Medido no artefato de POA antes disto: 100,00% dos 279.297 hashes acima
    # de 2^53 eram exatamente representáveis em float64 (aleatórios dão 0,6%).
    # O `.map` roda SÓ no subconjunto sem NA, que permanece int64.
    df['RADAR_ID_ENDERECO'] = _serie_id_nullable(canonica, mv, lut, df.index)

    # PONTE v1→v2. Sem ela, mudar a canônica renumera a base inteira e a série
    # histórica morre calada. Com ela, quem tem a entrega anterior faz o JOIN.
    canon_v1 = pd.Series(pd.NA, index=df.index, dtype=object)
    canon_v1[mv] = (mun[mv] + '|' + logr_v1[mv] + '|' +
                    ident[mv].astype(str) + '|' + loc[mv])
    df['RADAR_CHAVE_CANONICA_V2'] = canon_v1
    uniq_1 = pd.Series(canon_v1[mv].unique())
    lut_1 = dict(zip(uniq_1, uniq_1.map(id64_rastreio)))
    df['RADAR_ID_ENDERECO_V2'] = _serie_id_nullable(canon_v1, mv, lut_1, df.index)

    blk = _col('BLOCO_VALOR').fillna('').astype(str).map(canon_rastreio)
    canon_blk = pd.Series(pd.NA, index=df.index, dtype=object)
    canon_blk[mv] = (canonica[mv].astype(str) + ' BL ' +
                     blk[mv].where(blk[mv] != '', '-'))
    uniq_b = pd.Series(canon_blk[mv].unique())
    lut_b = dict(zip(uniq_b, uniq_b.map(id64_rastreio)))
    df['RADAR_ID_BLOCO'] = _serie_id_nullable(canon_blk, mv, lut_b, df.index)
    return df


# ╔═══════════════════════════════════════════════════════════════╗
# ║  CONTRATO DE SCHEMA (M4) + GATE EXECUTÁVEL (M2) — v4.3       ║
# ╚═══════════════════════════════════════════════════════════════╝

# Colunas de acesso NÃO guardado no pipeline canônico: ausência = fonte errada.
COLUNAS_OBRIGATORIAS = (
    ['COD_UNICO_ENDERECO', 'COD_MUNICIPIO', 'COD_SETOR', 'CEP',
     'NOM_TIPO_SEGLOGR', 'NOM_TITULO_SEGLOGR', 'NOM_SEGLOGR',
     'NUM_ENDERECO', 'DSC_LOCALIDADE', 'LATITUDE', 'LONGITUDE',
     'NV_GEO_COORD', 'COD_ESPECIE', 'COD_TIPO_ESPECIE'] +
    [f'NOM_COMP_ELEM{i}' for i in range(1, 6)] +
    [f'VAL_COMP_ELEM{i}' for i in range(1, 6)]
)


class RadarQualityError(RuntimeError):
    """Violação de invariante de qualidade — o run DEVE falhar.

    v4.5 (revisão externa, P0): gates NUNCA usam `assert` — `python -O`
    remove asserts e o gate 'inviolável' deixaria de existir. Exceção real
    é imune a -O."""


def _exigir(condicao, msg: str) -> None:
    if not condicao:
        raise RadarQualityError(msg)


# ════════════════════════════════════════════════════════════════════
#  R8 — INTEGRIDADE DE IDENTIFICADORES  (inviolável, v4.5.3 / PDCA-01)
# ════════════════════════════════════════════════════════════════════
"""
R8 — IDENTIFICADOR DETERMINÍSTICO NUNCA TRANSITA POR REPRESENTAÇÃO INEXATA.

  Origem: PDCA-01. `pd.DataFrame(list_of_dicts)` misturando id de 19 dígitos
  com None infere float64 e ARREDONDA acima de 2^53 — sem exceção, sem
  warning, preservando unicidade e destruindo o JOIN. A classe de defeito é
  de REPRESENTAÇÃO, não daquela função: qualquer etapa que reintroduza o
  float reintroduz o bug.

  PROIBIDO em qualquer ponto do trajeto de um id:
    float32 / float64 / np.floating
    to_numeric() sem dtype seguro (coage p/ float na presença de nulo ou str)
    célula numérica de Excel (o formato não preserva 19 dígitos como número)
    round-trip por JSON number

  PERMITIDO:
    INTERNO  — Int64 nullable (o id é 63 bits positivos: cabe exato) ou str
    EXTERNO  — UTF-8 STRING, sempre. CSV, Excel, JSON, Parquet, Power BI,
               PostgreSQL e pandas do consumidor recriam o defeito com
               facilidade se receberem número; string não tem essa borda.
               (Em banco, BIGINT é aceitável quando a coluna é declarada
               explicitamente — a string é o default seguro do CONTRATO.)

  Verificação: `varredura_ids()` roda dentro de gate_pos_condicoes sobre
  TODOS os frames de saída, sem lista branca de colunas — o alvo é o padrão
  (RADAR_ID*, *_ID, ID_*), não um nome conhecido.
"""

# 2^53 — acima disso o float64 deixa de representar todo inteiro.
LIMITE_FLOAT_EXATO = 2 ** 53
_RE_COL_ID = re.compile(r'(^|_)ID($|_)|^RADAR_ID', re.I)


def colunas_identificador(df: pd.DataFrame) -> list:
    """Colunas que o framework trata como IDENTIFICADOR (R8), por PADRÃO
    de nome — nunca por lista branca, senão a próxima coluna nova escapa."""
    return [c for c in df.columns if _RE_COL_ID.search(str(c))]


def id_seguro(valores, saida: str = 'Int64'):
    """Converte uma sequência de ids para representação EXATA (R8).

    `saida='Int64'` p/ trânsito interno; `saida='str'` p/ fronteira externa.
    Aceita int, str, pd.NA, None e np.integer. RECUSA float não-inteiro e
    float acima de 2^53 — nesse ponto o dígito JÁ se perdeu e converter
    depois só carimbaria o erro como se fosse verdade.
    """
    limpo = []
    for v in valores:
        if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
            limpo.append(None); continue
        if isinstance(v, float) or isinstance(v, np.floating):
            f = float(v)
            _exigir(f.is_integer(),
                    f"R8: id em float não-inteiro ({f!r}) — dígito já perdido")
            _exigir(abs(f) < LIMITE_FLOAT_EXATO,
                    f"R8: id chegou como float acima de 2^53 ({f:.0f}) — "
                    "arredondamento já ocorreu a montante; corrigir na ORIGEM")
            limpo.append(int(f)); continue
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ('nan', '<na>', 'none'):
                limpo.append(None); continue
            _exigir(s.lstrip('-').isdigit(), f"R8: id não numérico ({s!r})")
            limpo.append(int(s)); continue
        limpo.append(int(v))
    if saida == 'str':
        return pd.array(['' if v is None else str(v) for v in limpo], dtype=object)
    return pd.array(limpo, dtype='Int64')


def blindar_ids(df: pd.DataFrame, saida: str = 'Int64') -> pd.DataFrame:
    """Aplica id_seguro a TODA coluna de identificador do frame (R8)."""
    for c in colunas_identificador(df):
        df[c] = pd.Series(id_seguro(df[c].tolist(), saida=saida), index=df.index)
    return df


def varredura_ids(frames: dict) -> list:
    """R8 — varre todo frame de saída e devolve as violações de TIPO.

    Escopo deliberadamente restrito ao que é DECIDÍVEL a partir do frame:
    dtype float numa coluna de identificador. É assim que o defeito nasce,
    e antes da serialização é onde ele ainda pode ser pego.

    O que esta função NÃO faz, por honestidade: julgar se um VALOR já foi
    arredondado. Um id corrompido é um inteiro perfeitamente válido — a
    informação perdida não está nele. A tentação é marcar como suspeito o
    id > 2^53 que sobrevive ao round-trip float (o corrompido sobrevive por
    construção), mas ~1 em 1.024 dos ids legítimos de 63 bits também
    sobrevive: viraria ruído de 0,1% num gate que deve ser inviolável.
    Verificação de VALOR tem dois caminhos exatos, ambos usados no gate:
    recomputar do RADAR_CHAVE_CANONICA (`verificar_id_recomputavel`) e a
    integridade referencial hipótese → endereço-pai.
    """
    viol = []
    for nome, df in frames.items():
        if df is None or not len(df):
            continue
        for c in colunas_identificador(df):
            if pd.api.types.is_float_dtype(df[c]):
                viol.append(f"{nome}.{c}: dtype {df[c].dtype} — R8 proíbe "
                            "float em identificador (perda >2^53 é silenciosa)")
    return viol


def verificar_id_recomputavel(df_base: pd.DataFrame, amostra: int = 0) -> list:
    """R8/N13 — verificação EXATA de valor: reconstrói o id a partir da
    chave canônica que está no próprio frame e compara. Nenhuma heurística:
    ou o hash bate, ou o id não é o que diz ser."""
    if not {'RADAR_ID_ENDERECO', 'RADAR_CHAVE_CANONICA'} <= set(df_base.columns):
        return []
    v = df_base.loc[df_base['RADAR_ID_ENDERECO'].notna(),
                    ['RADAR_ID_ENDERECO', 'RADAR_CHAVE_CANONICA']].drop_duplicates()
    if amostra and len(v) > amostra:
        v = v.iloc[:: max(1, len(v) // amostra)]
    ruins = []
    for _, r in v.iterrows():
        esperado = id64_rastreio(str(r['RADAR_CHAVE_CANONICA']))
        obtido = int(str(r['RADAR_ID_ENDERECO']).strip())
        if esperado != obtido:
            ruins.append(f"chave {r['RADAR_CHAVE_CANONICA']!r}: "
                         f"esperado {esperado}, gravado {obtido}")
        if len(ruins) >= 4:
            break
    return ruins


def validar_schema(df: pd.DataFrame) -> None:
    """M4 mínimo — contrato de schema. Arquivo/fonte incompatível ABORTA na
    ingestão em vez de virar 'qualidade 99,9%' silenciosa rio abaixo."""
    faltam = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    _exigir(not faltam,
            f"M4: colunas obrigatórias ausentes ({len(faltam)}): {faltam} — "
            "arquivo, separador ou encoding incompatível com a fonte esperada")


# R5 — termo proibido em QUALQUER artefato de saída (nome de aba, coluna, valor).
# v4.4: 'xfera' (marca legada pré-rebrand) também é proibido em output —
# resquício do rename incompleto abortaria o export em vez de vazar.
_RX_R5 = re.compile(r'(?i)cnefe|ibge|xfera')
# Colunas de TEXTO LIVRE vindas do recenseamento (DADO, não metadado): um
# estabelecimento pode se chamar 'AGENCIA IBGE' e existe rua/vila com esse
# nome — é conteúdo legítimo, não referência à fonte. O assert duro cobre os
# RÓTULOS GERADOS pelo pipeline (CLASSE/TIPO/CONF/OBS/descrições), nomes de
# coluna e nomes de aba.
_R5_ISENTAS = {'DSC_ESTABELECIMENTO', 'ATIVIDADE_DETALHE', 'LOGRADOURO',
               'DSC_LOCALIDADE', 'LOCALIDADE', 'MARCADORES', 'DETALHE',
               'PRESENTES', 'AUSENTE_INFERIDO'}


def _r5_scan(nome: str, dfx: pd.DataFrame) -> None:
    cols_join = '|'.join(map(str, dfx.columns))
    _exigir(not _RX_R5.search(cols_join), f"R5: nome de coluna proibido em {nome}")
    if not len(dfx):
        return
    for c in dfx.columns:
        if c in _R5_ISENTAS or dfx[c].dtype != object:
            continue
        s = dfx[c].dropna().astype(str)
        if not len(s):
            continue
        hit = s[s.str.contains(_RX_R5, regex=True)]
        _exigir(hit.empty, f"R5: termo proibido em {nome}.{c}: "
                           f"{hit.iloc[0][:80] if len(hit) else ''!r}")


def gate_pos_condicoes(coletivas: pd.DataFrame,
                       cond_horiz: pd.DataFrame,
                       nres: pd.DataFrame,
                       polos: pd.DataFrame,
                       par_faltante: pd.DataFrame = None,
                       df_base: pd.DataFrame = None,
                       dicionario: pd.DataFrame = None,
                       nomes_abas: tuple = (),
                       hipoteses: pd.DataFrame = None) -> None:
    """M2 — pós-condições executáveis, imunes a `python -O` (RadarQualityError).
    Roda ANTES de qualquer to_excel/export; violação → run falha sem emitir dado.

    R1  NUM>0 em toda linha OBSERVADA; hipóteses: exceção única QUADRA_LOTE.
    R2  Nada com FLAG_ANOMALIA_COLETA=1 ou SANIDADE!=OK virou coletiva.
    R3  GRANDE exige CONF_TIPOLOGIA ALTA; PEQUENA ALTA/MEDIA; Nao_Residencial
        exige RADAR_CONF_FAIXA ALTA/MUITO_ALTA (F7 agora É gate).
    H   Hipóteses NUNCA se misturam a observados: REQUER_CONFIRMACAO='SIM'
        e RADAR_CONF_COORD==0 em toda linha inferida (EIXO1).
    R5  Zero cnefe/ibge/marca legada em abas, colunas e rótulos gerados.
    """
    # ── R8: VARREDURA DE IDENTIFICADORES (transversal, sem lista branca).
    # Roda ANTES de tudo: um id corrompido invalida qualquer conclusão que as
    # demais pós-condições venham a tirar do frame.
    _viol_id = varredura_ids({
        'Coletivas': coletivas, 'Condominios_Horizontais': cond_horiz,
        'Nao_Residencial': nres, 'Polos_Comerciais': polos,
        'Hipoteses_Expansao': hipoteses, 'PAR_FALTANTE': par_faltante,
        'base': df_base})
    _exigir(not _viol_id, "R8: identificador em representação inexata — " +
            ' | '.join(_viol_id[:4]))
    if df_base is not None:
        _ruins = verificar_id_recomputavel(df_base)
        _exigir(not _ruins, "R8/N13: id não confere com o hash da própria "
                            "chave canônica — " + ' | '.join(_ruins[:3]))

    # ── R1 (Coletivas agora é 100% observada — hipóteses saíram da aba)
    _num = pd.to_numeric(coletivas['NUMERO'], errors='coerce').fillna(0)
    _exigir((_num > 0).all(), "R1: NUM<=0 em Coletivas (aba observada)")
    if len(cond_horiz):
        _exigir((pd.to_numeric(cond_horiz['NUMERO'], errors='coerce').fillna(0) > 0).all(),
                "R1: NUM<=0 em Condominios_Horizontais")
    if len(nres):
        _exigir((pd.to_numeric(nres['NUMERO'], errors='coerce').fillna(0) > 0).all(),
                "R1: NUM<=0 em Nao_Residencial")

    # ── R2 (invariantes na base: o gate zerou FLAG_COL fora dele)
    if df_base is not None:
        m = df_base['FLAG_COL'] == 1
        # PATCH LOCAL (execucao Corsan/RS) — a v4.11.0 trocou a politica de
        # anomalia de BINARIA para a ESCADA de sinais primarios em
        # `radar_pipeline.gate_universal` (1 observacao / 2 quarentena / 3
        # exclusao) e NAO atualizou esta pos-condicao, que continuava exigindo
        # `FLAG_ANOMALIA_COLETA == 0`. O auditor passou a cobrar uma politica
        # que o motor deixou de executar, e QUALQUER base com registro de 1-2
        # sinais abortava o export — inclusive a fixture sintetica da propria
        # skill. Aqui a pos-condicao passa a verificar a politica EXECUTADA:
        # nenhum registro EXCLUIDO pela escada pode ter FLAG_COL=1.
        _lim = 3
        try:
            import radar_pipeline as _rp
            _lim = int(_rp.ANOMALIA_EXCLUI_A_PARTIR_DE)
        except Exception:
            pass
        _sin = (df_base['N_SINAIS_PRIMARIOS']
                if 'N_SINAIS_PRIMARIOS' in df_base.columns
                else pd.Series(0, index=df_base.index)).fillna(0).astype(int)
        _exigir((_sin.loc[m] < _lim).all(),
                "R2: anomalia de coleta (>= %d sinais primarios) com FLAG_COL=1 "
                "(vazaria p/ oportunidade)" % _lim)
        _exigir((df_base.loc[m, 'SANIDADE_GEO'] == 'OK').all(),
                "R2: geometria insana com FLAG_COL=1")
        _exigir((df_base.loc[m, 'NUMERO'] > 0).all(), "R1: NUM=0 com FLAG_COL=1")
        # ── N13: injetividade da chave numérica (colisão de hash ABORTA)
        if {'RADAR_ID_ENDERECO', 'RADAR_CHAVE_CANONICA'} <= set(df_base.columns):
            v = df_base.loc[df_base['RADAR_ID_ENDERECO'].notna(),
                            ['RADAR_ID_ENDERECO', 'RADAR_CHAVE_CANONICA']]
            if len(v):
                ncol = v.groupby('RADAR_ID_ENDERECO')['RADAR_CHAVE_CANONICA'].nunique()
                _exigir((ncol <= 1).all(),
                        "N13: colisão de RADAR_ID_ENDERECO — subir digest_size e re-emitir")
            # ── N13/PDCA-01: INTEGRIDADE REFERENCIAL da hipótese.
            # Injetividade sozinha não detecta id CORROMPIDO (float64
            # arredonda acima de 2^53 e o id vira órfão, silenciosamente).
            # Toda hipótese com id não-nulo TEM de casar com o pai na base.
            # Comparação em TEXTO: to_numeric numa coluna já serializada
            # (str + '') devolveria float64 e rodaria o mesmo arredondamento
            # que a checagem existe para detectar.
            if hipoteses is not None and len(hipoteses) and \
                    'RADAR_ID_ENDERECO' in hipoteses.columns:
                pais = {str(x).strip() for x in v['RADAR_ID_ENDERECO']}
                filhos = [str(x).strip() for x in hipoteses['RADAR_ID_ENDERECO']]
                orfas = [x for x in filhos
                         if x and x.lower() not in ('nan', '<na>', 'none') and x not in pais]
                _exigir(not orfas,
                        "N13: RADAR_ID_ENDERECO de hipótese sem endereço-pai na base "
                        f"(id órfão/corrompido, ex.: {orfas[:3]})")
        # ── N14: a CHAVE OPERACIONAL não pode perder distinção no id ────────
        # N13 procura UM hash apontando para DUAS canônicas. A classe que
        # escapava é a oposta e mais perigosa: duas CHAVEs operacionais
        # distintas que viram a MESMA canônica ANTES do hash — aí, para N13,
        # não existe colisão nenhuma. Foi assim que 'RUA BARAO DO GRAVATAI' e
        # 'RUA BARONESA DO GRAVATAI' compartilharam identidade em Porto
        # Alegre. Um gate que só olha depois do hash não vê o que foi perdido
        # antes dele.
        if {'CHAVE', 'RADAR_ID_ENDERECO'} <= set(df_base.columns):
            v = df_base.loc[df_base['RADAR_ID_ENDERECO'].notna(),
                            ['CHAVE', 'RADAR_ID_ENDERECO']].drop_duplicates()
            if len(v):
                n = v.groupby('RADAR_ID_ENDERECO')['CHAVE'].nunique()
                maus = n[n > 1]
                # PATCH LOCAL (execucao Corsan/RS) — N14 nao distinguia FUSAO
                # DELIBERADA de PERDA DE CAMPO, e reprovava a propria decisao
                # da skill. A canonica v2 expande numeral por extenso de
                # proposito (v4.10, medido: 66 grupos em POA, todos a mesma
                # rua escrita de dois jeitos). Em Canoas isso faz
                # 'AVENIDA 17 DE ABRIL' e 'AVENIDA DEZESSETE DE ABRIL'
                # caberem no mesmo id — o comportamento pretendido — e o gate
                # abortava o municipio inteiro por causa dele.
                #
                # O teste que separa as duas classes: RECONSTRUIR a canonica a
                # partir dos PROPRIOS CAMPOS da CHAVE, aplicando so as
                # normalizacoes declaradas. Se todas as CHAVEs do grupo
                # colapsam na mesma string, a fusao veio da normalizacao
                # declarada e e legitima. Se NAO colapsam, algum campo esta
                # sendo perdido no caminho — que e a classe
                # BARAO/BARONESA DO GRAVATAI que o gate existe para pegar, e
                # essa continua abortando.
                # A segunda normalizacao declarada e o PREENCHIMENTO DE TITULO
                # OMITIDO: "quando o municipio registra um unico titulo para
                # aquele tipo+nome, a ausencia e omissao e e preenchida; com
                # dois, nao se preenche nada" (v4.11.0). Por isso o teste
                # reconstroi SEM o slot de titulo e cobra a regra do titulo a
                # parte — e assim BARAO x BARONESA DO GRAVATAI, que sao DOIS
                # titulos nao vazios, continua sendo perda e continua abortando.
                def _sem_titulo(chave: str):
                    p = str(chave).split('|')
                    if len(p) < 6:
                        return str(chave), ''
                    corpo = (p[0] + '|' + canon_logradouro(f'{p[1]} {p[3]}')
                             + '|N' + p[4] + '|' + canon_rastreio(p[5]))
                    return corpo, canon_rastreio(p[2])

                _legitimos, _perda = 0, []
                for _i in maus.index:
                    _chaves = sorted(v[v['RADAR_ID_ENDERECO'] == _i]
                                     ['CHAVE'].astype(str))
                    _partes = [_sem_titulo(c) for c in _chaves]
                    _corpos = {c for c, _ in _partes}
                    _titulos = {t for _, t in _partes if t}
                    if len(_corpos) == 1 and len(_titulos) <= 1:
                        _legitimos += 1
                    else:
                        _perda.append(' | '.join(_chaves[:2]))
                # A contagem e LINHAGEM: quem le a entrega precisa saber
                # quantos enderecos foram fundidos por normalizacao, e nao
                # descobrir num print que ninguem guardou.
                globals()['N14_FUSOES_POR_NORMALIZACAO'] = int(_legitimos)
                if _legitimos:
                    print(f"    N14: {_legitimos} id(s) com CHAVEs distintas "
                          f"fundidas por normalizacao declarada (numeral por "
                          f"extenso / titulo omitido) — legitimo, gravado no "
                          f"manifest")
                _exigir(not _perda,
                        'N14: %d identificador(es) recebendo CHAVEs '
                        'operacionais distintas que NAO se explicam pela '
                        'normalizacao declarada — a canonicalizacao esta '
                        'apagando distincao real. Ex.: %s'
                        % (len(_perda), ' // '.join(_perda[:2])))


    # ── R3 (tipologia nas coletivas + F7 como gate no não-residencial)
    _conf_col = 'CONF_TIPOLOGIA' if 'CONF_TIPOLOGIA' in coletivas.columns else 'CONF'
    g = coletivas.loc[coletivas['CLASSE'] == 'GRANDE', _conf_col]
    _exigir(g.isin(['ALTA', 'MUITO_ALTA']).all(), "R3: GRANDE sem tipologia ALTA")
    p = coletivas.loc[coletivas['CLASSE'] == 'PEQUENA', _conf_col]
    _exigir(p.isin(['MUITO_ALTA', 'ALTA', 'MEDIA']).all(), "R3: PEQUENA fora de ALTA/MEDIA")
    # v4.5.1: FAIL-CLOSED — a AUSÊNCIA da coluna de confiança é violação,
    # não passe livre (antes um frame sem RADAR_CONF_FAIXA passava calado).
    if len(nres):
        _exigir('RADAR_CONF_FAIXA' in nres.columns,
                "R3: RADAR_CONF_FAIXA ausente em Nao_Residencial (gate fail-closed)")
        _exigir(nres['RADAR_CONF_FAIXA'].isin(['ALTA', 'MUITO_ALTA']).all(),
                "R3: Nao_Residencial com faixa de localização abaixo de ALTA")

    # ── H / EIXO 1 em hipóteses inferidas
    hip = hipoteses if hipoteses is not None else par_faltante
    if hip is not None and len(hip):
        _exigir((pd.to_numeric(hip['RADAR_CONF_COORD'],
                               errors='coerce').fillna(-1) == 0).all(),
                "EIXO1 fabricado em hipótese inferida")
        if 'REQUER_CONFIRMACAO' in hip.columns:
            _exigir((hip['REQUER_CONFIRMACAO'] == 'SIM').all(),
                    "H: hipótese sem REQUER_CONFIRMACAO=SIM")
        _num_h = pd.to_numeric(hip['NUMERO'], errors='coerce').fillna(0)
        _tipo_h = hip['TIPO_HIPOTESE'] if 'TIPO_HIPOTESE' in hip.columns else hip.get('TIPO')
        _exigir((_num_h[_tipo_h != 'QUADRA_LOTE'] > 0).all(),
                "R1: NUM<=0 em hipótese não-Q/L")

    # ── R5 (abas, colunas e valores)
    for nome in nomes_abas:
        _exigir(not _RX_R5.search(str(nome)), f"R5: nome de aba proibido: {nome!r}")
    _r5_scan('Coletivas', coletivas)
    _r5_scan('Condominios_Horizontais', cond_horiz)
    _r5_scan('Nao_Residencial', nres)
    _r5_scan('Polos_Comerciais', polos)
    if hip is not None:
        _r5_scan('Hipoteses_Expansao', hip)
    if dicionario is not None:
        _r5_scan('Dicionario', dicionario)
