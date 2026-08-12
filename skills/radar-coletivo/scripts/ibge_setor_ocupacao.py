#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bloco SET — ocupação de domicílios por setor censitário (IBGE, Censo 2022).
================================================================================

POR QUE ESTE MÓDULO EXISTE
--------------------------
O CNEFE (arquivo de endereços) tem 34 colunas e **nenhuma delas é ocupação**.
O campo que parece candidato, ``COD_TIPO_ESPECI``, é tipologia construtiva:

    101 Casa · 102 Casa de vila/condomínio · 103 Apartamento · 104 Outros

O universo de domicílios particulares do CNEFE é **compatível** com o universo
agregado que inclui ocupados, vagos e de uso ocasional. Testado em Porto Alegre,
sobre o município inteiro:

    CNEFE  COD_ESPECIE=1 (domicílio particular)   = 686.846 linhas
    Setor  V0003 (DPPO + DPPV + DPPUO + DPIO)     = 686.846
    CNEFE  COD_ESPECIE=2 (domicílio coletivo)     =     833
    Setor  V0004 (DCCM + DCSM)                    =     833

Casamento exato NESTE teste. Isso mostra que ``COD_ESPECIE=1`` é um universo
que INCLUI vagos e de uso ocasional — não que se possa apontar QUAL registro
está vago. O IBGE declara expressamente que não divulga no CNEFE elementos que
caracterizem o domicílio segundo o estado de ocupação, por sigilo estatístico.
A igualdade agregada num município também não garante que a relação permaneça
exata em todo município, UF e vintage: é uma verificação, não um teorema.

A situação de ocupação existe, publicada, apenas no AGREGADO POR SETOR — e é de
lá que este módulo a traz, no grão do setor.

    V0007  Domicílios particulares ocupados
    V0008  Uso ocasional (DPPUO)
    V0009  Vagos (DPPV)

Em Porto Alegre: 100.997 vagos + 27.242 de uso ocasional = 128.239 (18,7%).

A REGRA QUE NÃO PODE SER QUEBRADA
---------------------------------
``SET_TX_SEM_OCUPACAO_HABITUAL`` é atributo do **setor**, jamais da unidade. Multiplicar a
taxa pelas unidades de uma coletiva para "estimar quantas estão vagas" produz um
número que parece dado e é palpite — exatamente o que a regra de origem existe
para barrar. O prefixo ``SET_`` é deliberado, e ``gate_set_nao_multiplicado()``
é executável.

Origem de todo campo: ``IBGE:AGREGADOS_SETOR_2022``. Nada fora do IBGE.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

FTP_AGREGADOS = (
    'https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/'
    'Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/'
)

# O IBGE versiona o básico por data no nome do arquivo. A lista é tentada em
# ordem; a primeira que responder vence. Falha explícita se nenhuma responder —
# arquivo silenciosamente ausente viraria coluna vazia sem ninguém perceber.
CANDIDATOS_BASICO = (
    'Agregados_por_setores_basico_BR_20260520.zip',
    'Agregados_por_setores_basico_BR_20250417.zip',
    'Agregados_por_setores_basico_BR.zip',
)

VARS_SET = {
    'v0003': 'SET_DOM_PARTICULARES',
    'v0007': 'SET_DOM_OCUPADOS',
    'v0008': 'SET_DOM_USO_OCASIONAL',
    'v0009': 'SET_DOM_VAGOS',
}

# Abaixo deste número de domicílios no setor a taxa é ruído amostral e não
# classifica. Setor com 3 domicílios e 1 vago não é "33% de vacância".
MIN_DOM_PARA_CLASSIFICAR = 20

FAIXAS_SEM_OCUPACAO = [
    (0.00, 0.10, 'BAIXA'),
    (0.10, 0.25, 'MEDIA'),
    (0.25, 0.40, 'ALTA'),
    (0.40, 1.01, 'MUITO_ALTA'),
]

ORIGEM_SET = 'IBGE:AGREGADOS_SETOR_2022:V0003/V0007/V0008/V0009'


class SetorError(RuntimeError):
    """Falha dura no bloco SET. Exceção real: imune a `python -O`."""


# ─────────────────────────────────────────────────────────────────────────────
# aquisição
# ─────────────────────────────────────────────────────────────────────────────

def baixar_basico(cache_dir: Path, timeout: int = 300) -> Path:
    """Baixa (uma vez) o agregado básico por setor do Brasil. Retorna o CSV."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_local = cache_dir / 'Agregados_por_setores_basico_BR.csv'
    # Cache validado pelo CONTEUDO, não pelo tamanho. O guarda `size > 1 MB`
    # aceitava um download interrompido de 2 MB e rejeitava uma fixture local
    # legitima de 2 KB — errava nas duas direções. Um arquivo so' e' cache
    # valido se tiver o cabecalho que o parser espera.
    if csv_local.exists() and _cabecalho_valido(csv_local):
        return csv_local

    # RADAR_OFFLINE fecha a porta da rede no ponto ÚNICO em que ela existe.
    # A suíte rodava o emissor em subprocesso: bloquear `urlopen` no processo
    # do pytest não alcançava o filho, e um teste que "passa" porque o FTP do
    # IBGE respondeu naquele dia não é um teste — é um monitor de rede.
    if os.environ.get('RADAR_OFFLINE', '').strip() not in ('', '0', 'false'):
        raise SetorError(
            f'RADAR_OFFLINE ativo e cache invalido em {csv_local} — a aquisicao '
            'pela rede esta proibida neste processo. Popule o cache antes.')

    ultimo_erro = None
    for nome in CANDIDATOS_BASICO:
        try:
            with urlopen(FTP_AGREGADOS + nome, timeout=timeout) as resp:
                bruto = resp.read()
            if len(bruto) < 1_000_000:          # HTML de erro, não zip
                raise SetorError(f'{nome}: resposta de {len(bruto)} bytes')
            with zipfile.ZipFile(io.BytesIO(bruto)) as z:
                interno = [n for n in z.namelist() if n.lower().endswith('.csv')]
                if not interno:
                    raise SetorError(f'{nome}: zip sem CSV')
                csv_local.write_bytes(z.read(interno[0]))
            return csv_local
        except Exception as e:                  # noqa: BLE001 — tenta o próximo
            ultimo_erro = e
    raise SetorError(
        'nenhum arquivo de agregados por setor pôde ser baixado. '
        f'Último erro: {ultimo_erro}. Sem ele o bloco SET não pode ser '
        'preenchido — e coluna vazia em silêncio é pior que falha.')


def _cabecalho_valido(caminho) -> bool:
    try:
        with open(caminho, encoding='latin-1') as fh:
            cab = fh.readline()
    except OSError:
        return False
    # PATCH LOCAL (execucao Corsan/RS) — o cabecalho real vem com os nomes
    # ENTRE ASPAS ("CD_SETOR";"CD_MUN";...), e o strip sozinho as preservava:
    # a validacao NUNCA aceitava o proprio arquivo que o downloader acabara de
    # gravar. Efeito: com RADAR_OFFLINE o cache era declarado invalido e o
    # bloco SET caia em quarentena; sem ele, cada municipio rebaixava 140 MB.
    # O guarda existe para validar o cache por CONTEUDO — precisa ler o
    # conteudo como ele e' escrito.
    cols = {c.strip().strip('"').strip() for c in cab.split(';')}
    return {'CD_SETOR', 'CD_MUN'} <= cols and set(VARS_SET) <= cols


def carregar_setores(csv_path: Path, cod_municipio: str | list | None = None) -> pd.DataFrame:
    """Lê o agregado básico e devolve o bloco SET já calculado, por setor.

    cod_municipio : código IBGE de 7 dígitos (str), lista deles, ou None p/ BR.
    """
    usecols = ['CD_SETOR', 'CD_MUN', 'NM_MUN', 'NM_BAIRRO'] + list(VARS_SET)
    df = pd.read_csv(csv_path, sep=';', encoding='latin-1', low_memory=False,
                     dtype={'CD_SETOR': str, 'CD_MUN': str})
    faltando = [c for c in usecols if c not in df.columns]
    if faltando:
        raise SetorError(f'agregado por setor sem as colunas {faltando} — '
                         'layout do IBGE mudou, o mapeamento precisa de revisão')
    df = df[usecols]
    if cod_municipio is not None:
        alvo = {str(cod_municipio)} if isinstance(cod_municipio, (str, int)) \
            else {str(m) for m in cod_municipio}
        df = df[df['CD_MUN'].astype(str).isin(alvo)]
    return montar_bloco_set(df)


def montar_bloco_set(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia, tipa e deriva taxa + classe. Set-based, sem apply por linha."""
    out = pd.DataFrame({
        'SET_COD': df['CD_SETOR'].astype(str),
        'SET_MUNICIPIO': df['NM_MUN'],
        'SET_BAIRRO': df['NM_BAIRRO'],
    })
    for src, dst in VARS_SET.items():
        out[dst] = pd.to_numeric(df[src], errors='coerce').fillna(0).astype('Int64')

    out['SET_DOM_SEM_OCUPACAO_HABITUAL'] = (out['SET_DOM_USO_OCASIONAL']
                                  + out['SET_DOM_VAGOS'])
    tot = out['SET_DOM_PARTICULARES'].astype('float64')
    out['SET_TX_SEM_OCUPACAO_HABITUAL'] = np.where(
        tot > 0, (out['SET_DOM_SEM_OCUPACAO_HABITUAL'].astype('float64') / tot).round(4), np.nan)

    classe = pd.Series('INSUFICIENTE', index=out.index, dtype=object)
    apto = out['SET_DOM_PARTICULARES'] >= MIN_DOM_PARA_CLASSIFICAR
    for lo, hi, rot in FAIXAS_SEM_OCUPACAO:
        classe = classe.mask(
            apto & (out['SET_TX_SEM_OCUPACAO_HABITUAL'] >= lo)
            & (out['SET_TX_SEM_OCUPACAO_HABITUAL'] < hi), rot)
    out['SET_CLASSE_SEM_OCUPACAO_HABITUAL'] = classe
    out['SET_ORIGEM_DADO'] = ORIGEM_SET
    return out


# ─────────────────────────────────────────────────────────────────────────────
# junção com o CNEFE
# ─────────────────────────────────────────────────────────────────────────────

def chave_setor(serie: pd.Series) -> pd.Series:
    """CNEFE traz COD_SETOR com 16 caracteres (sufixo 'P'); o agregado, 15.

    Truncar é a única normalização necessária — verificado em POA: 2.552 dos
    2.641 setores casam, e o delta de domicílios fica em 270 sobre 664.657
    (0,04%). Nunca fazer o inverso (concatenar 'P' no agregado): o sufixo não
    é constante entre UFs.
    """
    return serie.fillna('').astype(str).str.strip().str[:15]


def enriquecer(df: pd.DataFrame, setores: pd.DataFrame,
               col_setor: str = 'COD_SETOR') -> pd.DataFrame:
    """Acopla o bloco SET ao dataframe do CNEFE por setor censitário.

    Não altera nenhuma coluna existente: só acrescenta as SET_*.
    """
    if col_setor not in df.columns:
        raise SetorError(f'coluna {col_setor} ausente — sem setor não há bloco SET')
    df = df.copy()
    df['_SET15'] = chave_setor(df[col_setor])
    cols = [c for c in setores.columns if c.startswith('SET_')]
    lado = setores[cols].rename(columns={'SET_COD': '_SET15'})

    # G19b — CARDINALIDADE. Um merge sem `validate` transforma duplicata do
    # lado direito em LINHA NOVA do lado esquerdo: dois setores iguais no
    # agregado duplicavam o endereço do CNEFE e o pipeline passava a contar
    # unidades que não existem. Testado: 2 linhas -> 4.
    #
    # O gate anterior chamava-se `gate_set_nao_multiplicado` e não verificava
    # multiplicação nenhuma — o nome dava uma garantia que o código não
    # entregava, que é a forma mais cara de falso rigor.
    dup = lado['_SET15'].duplicated().sum()
    if dup:
        raise SetorError(
            f'G19b: agregado por setor com {dup} chave(s) repetida(s) — o merge '
            'multiplicaria linhas do CNEFE e fabricaria unidades. '
            'Deduplique o agregado na ORIGEM; escolher uma linha aqui seria '
            'silenciar a divergência do IBGE.')

    n_antes = len(df)
    out = df.merge(lado, on='_SET15', how='left', validate='many_to_one')
    if len(out) != n_antes:
        raise SetorError(f'G19b: merge do bloco SET mudou a cardinalidade '
                         f'({n_antes} -> {len(out)})')
    return out.drop(columns=['_SET15'])


def cobertura(df: pd.DataFrame) -> dict:
    """Diagnóstico da junção — para o manifest, não para decisão."""
    tem = df['SET_DOM_PARTICULARES'].notna() if 'SET_DOM_PARTICULARES' in df else pd.Series(False, index=df.index)
    return {'linhas': int(len(df)),
            'com_setor_casado': int(tem.sum()),
            'pct_casado': round(100.0 * tem.mean(), 2) if len(df) else 0.0}


# ─────────────────────────────────────────────────────────────────────────────
# gate
# ─────────────────────────────────────────────────────────────────────────────

def gate_set_nao_multiplicado(df: pd.DataFrame) -> None:
    """G19 — proíbe derivar contagem de vagas por unidade a partir da taxa.

    A taxa qualifica o setor; ela não sabe qual apartamento está vazio.
    Qualquer coluna cujo nome sugira contagem de desocupados FORA do prefixo
    SET_ (ou seja, no grão da unidade/coletiva) aborta a emissão.
    """
    suspeitas = [
        c for c in df.columns
        if not c.startswith('SET_')
        and any(t in c.upper() for t in ('VAGO', 'VAGA', 'DESOCUP', 'OCIOS'))
    ]
    if suspeitas:
        raise SetorError(
            'G19 — colunas de desocupação fora do bloco SET: '
            f'{suspeitas}. A taxa do setor NUNCA vira contagem por unidade; '
            'multiplicá-la produz número que parece dado e é palpite.')

    # o par tem de fechar: ocupados + uso ocasional + vagos = particulares
    partes = ['SET_DOM_OCUPADOS', 'SET_DOM_USO_OCASIONAL', 'SET_DOM_VAGOS']
    if {'SET_COD', 'SET_DOM_PARTICULARES', *partes} <= set(df.columns):
        s = df[['SET_COD', 'SET_DOM_PARTICULARES', *partes]].drop_duplicates('SET_COD')
        for c in ['SET_DOM_PARTICULARES', *partes]:
            s[c] = pd.to_numeric(s[c], errors='coerce')
        s = s.dropna()                       # setor sem casamento não é violação
        if len(s):
            soma = s[partes].sum(axis=1)
            maus = s[soma != s['SET_DOM_PARTICULARES']]
            if len(maus):
                ex = maus.iloc[0]
                raise SetorError(
                    f'G19 — fechamento do setor quebrado em {len(maus)} setor(es); '
                    f'ex. {ex.SET_COD}: {int(ex.SET_DOM_OCUPADOS)}+'
                    f'{int(ex.SET_DOM_USO_OCASIONAL)}+{int(ex.SET_DOM_VAGOS)} '
                    f'!= {int(ex.SET_DOM_PARTICULARES)}')


if __name__ == '__main__':                                  # pragma: no cover
    import argparse
    p = argparse.ArgumentParser(description='Bloco SET — ocupação por setor (IBGE)')
    p.add_argument('--municipio', help='código IBGE 7 dígitos')
    p.add_argument('--cache', default='./cache_ibge')
    p.add_argument('--out', default='setores_ocupacao.csv')
    a = p.parse_args()
    csv = baixar_basico(Path(a.cache))
    s = carregar_setores(csv, a.municipio)
    gate_set_nao_multiplicado(s)
    s.to_csv(a.out, index=False, sep=';', encoding='utf-8-sig')
    tot = int(s['SET_DOM_PARTICULARES'].sum())
    des = int(s['SET_DOM_SEM_OCUPACAO_HABITUAL'].sum())
    print(f'{len(s)} setores · {tot} domicílios particulares · '
          f'{des} desocupados ({100*des/tot:.1f}%) → {a.out}')
