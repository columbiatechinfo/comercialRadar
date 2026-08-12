#!/usr/bin/env python3
"""
Radar Coletivo — Gerador de Mapa HTML (Mapeamento Vertical)
=============================================================
Gera mapa Leaflet interativo com 3 abas, filtros checkbox,
popups com métricas e links Google Maps.

Uso:
  python cnefe_mapa.py --input 4211900_PALHOCA.csv --cidade "Palhoça/SC"
  python cnefe_mapa.py --input CNEFE_2022_SC.csv.gz --municipio 4211900
  python cnefe_mapa.py --input dados.csv --output meu_mapa.html

Dependências: pandas, numpy
Requer: cnefe_coletivas.py no mesmo diretório (funções de processamento).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Importa pipeline de processamento
try:
    from cnefe_coletivas import (
        load_cnefe, normalizar_complemento, classificar_uso,
        enriquecer, classificar_coletiva,
    )
    import radar_utils as xu
except ImportError:
    print("ERRO: cnefe_coletivas.py/radar_utils.py não encontrados no mesmo diretório.")
    print("Coloque os scripts na mesma pasta.")
    sys.exit(1)


def preparar_dados(df):
    """Converte DataFrame processado em payload JSON compacto."""
    locs = sorted(df['DSC_LOCALIDADE'].dropna().unique().tolist())
    loc_i = {v: i for i, v in enumerate(locs)}
    logrs = sorted(df['LOGRADOURO'].dropna().unique().tolist())
    logr_i = {v: i for i, v in enumerate(logrs)}
    TIPOS = sorted(df[df['FLAG_COL'] == 1]['TIPO_COLETIVA'].dropna().unique().tolist())
    tipo_i = {v: i for i, v in enumerate(TIPOS)}
    CONFS = ['ALTA', 'MEDIA', 'BAIXA']
    conf_i = {v: i for i, v in enumerate(CONFS)}
    SETORES = sorted(df[(df['SETOR_ATIVIDADE'] != 'RESIDENCIAL') &
                        (df['SETOR_ATIVIDADE'] != 'CONSTRUCAO')
                       ]['SETOR_ATIVIDADE'].dropna().unique().tolist())
    setor_i = {v: i for i, v in enumerate(SETORES)}

    # Coletivas: 1 linha por CHAVE+BLOCO
    dc = df[(df['FLAG_COL'] == 1) & (df['NUMERO'] != 0)].copy()
    dc['BK'] = dc['BLOCO_VALOR'].fillna('').replace('', np.nan).fillna('-')
    col = []
    for (ch, bk), sub in dc.groupby(['CHAVE', 'BK']):
        f = sub.iloc[0]
        lat, lon = sub['LATITUDE'].mean(), sub['LONGITUDE'].mean()
        if pd.isna(lat) or pd.isna(lon): continue
        total = int(dc[dc['CHAVE'] == ch].shape[0])
        nb = int(dc[dc['CHAVE'] == ch]['BK'].nunique())
        if nb == 1 and bk == '-': nb = 0
        col.append([
            round(lat, 6), round(lon, 6),
            int(f['ID_COLETIVA']) if pd.notna(f['ID_COLETIVA']) else 0,
            logr_i.get(str(f['LOGRADOURO']), -1), int(f['NUMERO']),
            loc_i.get(str(f['DSC_LOCALIDADE']), -1), str(bk)[:12],
            len(sub), total, nb,
            int(sub['PAVIMENTO_VALOR'].replace('', np.nan).dropna().nunique()),
            int(sub['UNIDADE_VALOR'].replace('', np.nan).dropna().nunique()),
            int(sub['MORADIA_VALOR'].replace('', np.nan).dropna().nunique()),
            int((sub['SETOR_ATIVIDADE'] == 'RESIDENCIAL').sum()),
            int(sub['SETOR_ATIVIDADE'].isin(['COMERCIO_VAREJO', 'COMERCIO_SERVICO', 'BELEZA_ESTETICA']).sum()),
            int((sub['SETOR_ATIVIDADE'] == 'INDUSTRIAL').sum()),
            int((sub['SETOR_ATIVIDADE'] == 'ALIMENTACAO').sum()),
            int(sub['SETOR_ATIVIDADE'].isin(['SERVICO_PROF', 'SERVICO_AUTO']).sum()),
            int((sub['SETOR_ATIVIDADE'] == 'VAGO').sum()),
            conf_i.get(str(f['CONFIANCA']), -1),
            tipo_i.get(str(f['TIPO_COLETIVA']), -1),
            str(f['QUALIDADE_COORD']),
        ])

    # Não-residencial — mesmo critério F7 do runner (faixa ALTA+); o
    # NUMERO>0 já vem garantido pelo gate universal aplicado no main
    dnr = df[(df['SETOR_ATIVIDADE'] != 'RESIDENCIAL') & (df['SETOR_ATIVIDADE'] != 'CONSTRUCAO')]
    if 'RADAR_CONF_FAIXA' in dnr.columns:
        dnr = dnr[dnr['RADAR_CONF_FAIXA'].isin(['ALTA', 'MUITO_ALTA'])]
    nres = []
    for _, r in dnr.iterrows():
        if pd.isna(r['LATITUDE']) or pd.isna(r['LONGITUDE']): continue
        nres.append([
            round(float(r['LATITUDE']), 6), round(float(r['LONGITUDE']), 6),
            logr_i.get(str(r['LOGRADOURO']), -1), int(r['NUMERO']),
            loc_i.get(str(r['DSC_LOCALIDADE']), -1),
            setor_i.get(str(r['SETOR_ATIVIDADE']), -1),
            str(r.get('ATIVIDADE_DETALHE', ''))[:40],
            str(r.get('DSC_ESTABELECIMENTO', ''))[:40],
            int(r.get('FLAG_COL', 0)), str(r.get('QUALIDADE_COORD', '')),
        ])

    # Endereços (agregados por coordenada)
    df['_lt'] = df['LATITUDE'].round(6)
    df['_ln'] = df['LONGITUDE'].round(6)
    ag = df.groupby(['_lt', '_ln']).agg(
        Q=('COD_UNICO_ENDERECO', 'count'), LG=('LOGRADOURO', 'first'),
        NM=('NUMERO', 'first'), LC=('DSC_LOCALIDADE', 'first'),
        NV=('NV_GEO_COORD', lambda x: int(x.mode().iloc[0]) if len(x.mode()) > 0 else int(x.iloc[0])),
        CL=('FLAG_COL', 'max'), CF=('CONFIANCA', 'first'),
        ES=('DSC_TIPO_ESPECIE', 'first'),
    ).reset_index()
    addr = []
    for _, r in ag.iterrows():
        if pd.isna(r['_lt']) or pd.isna(r['_ln']): continue
        addr.append([
            round(float(r['_lt']), 6), round(float(r['_ln']), 6),
            int(r['Q']), logr_i.get(str(r['LG']), -1), int(r['NM']),
            loc_i.get(str(r['LC']), -1), int(r['NV']), int(r['CL']),
            conf_i.get(str(r['CF']), -1), str(r.get('ES', '') or ''),
        ])

    return {'l': locs, 'g': logrs, 't': TIPOS, 's': SETORES,
            'c': col, 'n': nres, 'a': addr}


# ═══ Template HTML (lido do arquivo map_template.html se disponível,
#     senão usa a versão embedded) ═══
TEMPLATE_FILE = Path(__file__).parent / 'map_template.html'


def get_template():
    if TEMPLATE_FILE.exists():
        with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    print("ERRO: map_template.html não encontrado.")
    print(f"Esperado em: {TEMPLATE_FILE}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Radar Coletivo — Mapa HTML')
    parser.add_argument('--input', required=True, help='CSV do CNEFE')
    parser.add_argument('--municipio', type=int, default=None)
    parser.add_argument('--output', default=None, help='Caminho do HTML')
    parser.add_argument('--cidade', default='', help='Nome da cidade no header')
    args = parser.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        print(f"ERRO: {inp}"); sys.exit(1)

    out = args.output or str(inp.parent / f'RADAR_MAPA_{inp.stem.replace(".csv","")}.html')
    cidade = args.cidade or inp.stem.replace('.csv', '').replace('_', ' ')

    print(f"Radar Coletivo — Mapa")
    print('=' * 50)

    # v4.5.2: writer NÃO-CANÔNICO consumindo o núcleo único — dedup,
    # sanidade, anomalia, F7, classificação e gate vêm de radar_pipeline;
    # o mapa só desenha o que o gate aprova.
    import radar_pipeline as rp
    print('[1/2] Preparando base (núcleo único radar_pipeline)...')
    df, stats = rp.preparar_base(str(inp), municipio=args.municipio,
                                 log=lambda m: print('      ' + m))
    m_gate = rp.gate_universal(df)
    print(f'      gate universal: {int(m_gate.sum()):,} / '
          f"{stats['apos_dedup']:,} pós-dedup ({stats['entrada_raw']:,} raw)")
    df = df[m_gate].copy()

    print('[2/2] Gerando mapa...')
    payload = preparar_dados(df)
    dj = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)

    template = get_template()

    # Substituir contadores no template
    html = template.replace('__DATA_PLACEHOLDER__', dj)
    html = html.replace('17,040', f'{len(payload["c"]):,}')
    html = html.replace('11,289', f'{len(payload["n"]):,}')
    html = html.replace('86,882', f'{len(payload["a"]):,}')
    html = html.replace('120K', f'{len(df)//1000}K')
    html = html.replace('Palhoça/SC', cidade)

    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    import os
    sz = os.path.getsize(out) / 1024 / 1024
    print(f'\n✓ {out} ({sz:.1f} MB)')
    print(f'  Col: {len(payload["c"]):,} | NRes: {len(payload["n"]):,} | Addr: {len(payload["a"]):,}')


if __name__ == '__main__':
    main()
