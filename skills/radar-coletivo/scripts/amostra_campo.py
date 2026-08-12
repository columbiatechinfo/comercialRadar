#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Amostra estratificada de hipóteses para validação em campo.
================================================================================

POR QUE ESTE SCRIPT EXISTE
--------------------------
A graduação R9 decide o que vai a campo. Mas se o campo só visitar o que a
régua APROVOU, a matriz de confusão fica com um quadrante vazio para sempre:

                        campo confirma      campo nega
    régua aprovou   →   verdadeiro pos.     falso positivo
    régua reprovou  →   FALSO NEGATIVO      verdadeiro neg.
                        ↑ nunca medido se a hipótese reprovada não for visitada

Sem a linha de baixo, o limiar (`C_DIST_MAX_PLAUSIVEL`,
`B_TETO_ANDARES_AUSENTES`) nunca sai do valor que foi arbitrado. Este script
sorteia **deliberadamente também o que a régua reprovou**, estratificado, para
que o retorno de campo meça as duas linhas.

O sorteio é DETERMINÍSTICO (semente fixa): duas execuções sobre a mesma safra
produzem a mesma lista, e a lista é citável numa ordem de serviço.

    python amostra_campo.py --rcc POA_RCC.csv --out amostra.csv [--por-estrato 12]
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

SEMENTE = 20220101          # fixa por design: amostra tem de ser reproduzível

# Faixas de EVD_DISTANCIA. A de distância 1 é a mais informativa — é o caso
# "101-109 não implica 209, pode terminar no 208", onde a régua ou acerta ou
# erra por um fio. A de >10 é controle negativo: no andar de topo a cobertura
# tem menos unidades por projeto arquitetônico, e o campo deve condenar.
FAIXAS_DIST = [(1, 1, 'd1'), (2, 2, 'd2'), (3, 3, 'd3'),
               (4, 5, 'd4-5'), (6, 10, 'd6-10'), (11, 10**6, 'd>10')]

COLS_SAIDA = [
    'CASO', 'ESTRATO', 'EVD_GRAU', 'EVD_CLASSE', 'EVD_DISTANCIA',
    'ACT_DESTINO_CAMPO', 'UNIDADE_ID', 'COLETIVA_ID',
    'END_LOGRADOURO', 'END_NUMERO', 'END_COMPLEMENTO', 'END_LOCALIDADE',
    'UND_VALOR', 'COL_FORMA', 'COL_QTD_OBSERVADA',
    'SET_CLASSE_DESOCUPACAO', 'SET_TX_DESOCUPACAO',
    'EVD_DESCRICAO', 'EVD_PRESENTES',
    'LAT_REFERENCIA', 'LON_REFERENCIA', 'GEO_QUALIDADE_REFERENCIA',
    'STREET_VIEW',
    # colunas que o campo PREENCHE — a amostra já sai como formulário
    'CAMPO_EXISTE', 'CAMPO_OBSERVACAO',
]


def faixa_dist(v):
    if pd.isna(v):
        return 'n/a'
    v = int(v)
    for lo, hi, rot in FAIXAS_DIST:
        if lo <= v <= hi:
            return rot
    return 'n/a'


def referencia_geo(rcc: pd.DataFrame) -> pd.DataFrame:
    """Coordenada do endereço-pai, por COLETIVA_ID.

    A hipótese NUNCA tem coordenada própria (R2: não se fabrica GPS). Para ir a
    campo ela precisa de um ponto de referência, e o único honesto é o do
    endereço observado que a gerou — declarado como REFERÊNCIA, com a qualidade
    da coordenada junto, para que ninguém a leia como posição da unidade.
    """
    obs = rcc[(rcc['UND_NATUREZA'] == 'OBSERVADO')].copy()
    for c in ('GEO_LAT', 'GEO_LON'):
        obs[c] = pd.to_numeric(obs[c], errors='coerce')
    obs = obs[obs['GEO_LAT'].notna() & obs['GEO_LON'].notna()]
    # prioriza a melhor qualidade dentro do grupo
    ordem = {'VALIDADA': 0, 'ESTIMADA': 1, 'BAIXA': 2,
             'HERDADA_CENTROIDE': 3, 'AUSENTE': 4}
    obs['_q'] = obs['GEO_QUALIDADE'].map(ordem).fillna(9)
    obs = obs.sort_values(['COLETIVA_ID', '_q'], kind='stable')
    return (obs.groupby('COLETIVA_ID')
            .agg(LAT_REFERENCIA=('GEO_LAT', 'first'),
                 LON_REFERENCIA=('GEO_LON', 'first'),
                 GEO_QUALIDADE_REFERENCIA=('GEO_QUALIDADE', 'first'))
            .reset_index())


def amostrar(rcc: pd.DataFrame, por_estrato: int = 12,
             so_reprovadas: bool = False) -> pd.DataFrame:
    inf = rcc[rcc['UND_NATUREZA'] == 'INFERIDO'].copy()
    if not len(inf):
        return pd.DataFrame(columns=COLS_SAIDA)
    if so_reprovadas:
        inf = inf[inf['ACT_DESTINO_CAMPO'] != 'ENVIAR']

    inf['EVD_DISTANCIA'] = pd.to_numeric(inf['EVD_DISTANCIA'], errors='coerce')
    inf['_FX'] = inf['EVD_DISTANCIA'].map(faixa_dist)
    # o estrato precisa separar o que a régua tratou diferente; classe sozinha
    # esconderia a distância, e distância sozinha esconderia o mecanismo
    inf['ESTRATO'] = inf['EVD_CLASSE'].astype(str) + '|' + inf['_FX']

    # sorteio por estrato sem groupby.apply: a API mudou entre versões do
    # pandas e o sorteio precisa sobreviver a isso. Índice sorteado, depois
    # .loc — determinístico e estável.
    idx = []
    for _, sub in inf.groupby('ESTRATO', sort=True, observed=True):
        idx.extend(sub.sample(min(len(sub), por_estrato),
                              random_state=SEMENTE).index.tolist())
    amostra = inf.loc[idx].reset_index(drop=True)

    ref = referencia_geo(rcc)
    a = amostra.merge(ref, on='COLETIVA_ID', how='left')
    a['STREET_VIEW'] = np.where(
        a['LAT_REFERENCIA'].notna(),
        ('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint='
         + a['LAT_REFERENCIA'].astype(str) + ',' + a['LON_REFERENCIA'].astype(str)),
        '')
    a['CAMPO_EXISTE'] = ''            # SIM | NAO — preenchido em campo
    a['CAMPO_OBSERVACAO'] = ''
    a = a.sort_values(['EVD_GRAU', 'ESTRATO', 'END_LOGRADOURO', 'END_NUMERO'],
                      kind='stable').reset_index(drop=True)
    a.insert(0, 'CASO', range(1, len(a) + 1))
    for c in COLS_SAIDA:
        if c not in a.columns:
            a[c] = ''
    return a[COLS_SAIDA]


def resumo(a: pd.DataFrame) -> pd.DataFrame:
    return (a.groupby(['EVD_GRAU', 'ACT_DESTINO_CAMPO', 'ESTRATO'], observed=True)
            .size().rename('casos').reset_index()
            .sort_values(['EVD_GRAU', 'ESTRATO']))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--rcc', required=True, help='CSV do RCC emitido')
    p.add_argument('--out', required=True)
    p.add_argument('--por-estrato', type=int, default=12)
    p.add_argument('--so-reprovadas', action='store_true',
                   help='amostra apenas o que a regua NAO envia a campo '
                        '(fecha o quadrante de falso negativo)')
    p.add_argument('--xlsx', action='store_true', help='emite tambem .xlsx')
    a = p.parse_args()

    rcc = pd.read_csv(a.rcc, sep=';', dtype=str, encoding='utf-8-sig',
                      low_memory=False)
    am = amostrar(rcc, a.por_estrato, a.so_reprovadas)
    am.to_csv(a.out, sep=';', index=False, encoding='utf-8-sig')
    print(f'{len(am)} casos em {am["ESTRATO"].nunique()} estratos -> {a.out}')
    print(resumo(am).to_string(index=False))
    if a.xlsx:
        x = a.out.rsplit('.', 1)[0] + '.xlsx'
        with pd.ExcelWriter(x, engine='openpyxl') as w:
            am.to_excel(w, sheet_name='Amostra_Campo', index=False)
            resumo(am).to_excel(w, sheet_name='Estratos', index=False)
        print(f'-> {x}')


if __name__ == '__main__':
    main()
