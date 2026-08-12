#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R13/R14 — Ciclo de qualidade PÓS-EMISSÃO: reabre, reconcilia e lacra.
================================================================================

    R13  ARTEFATO NÃO É ENTREGA ATÉ SER RELIDO E LACRADO
         DataFrame aprovado não implica arquivo aprovado. Todo produto canônico
         é escrito em área temporária, reaberto pelo PARSER DO PRÓPRIO FORMATO,
         reconciliado contra o modelo, validado estrutural e semanticamente, e
         só então publicado. `GATES OK` não autoriza entrega — só
         QUALITY_SEAL ∈ {SEALED, CORRECTED_AND_SEALED} autoriza.

    R14  AUDITOR NÃO CONFIA NO PRODUTOR
         O validador crítico recalcula o invariante de forma INDEPENDENTE da
         função que gerou o campo. Se o auditor chamar o produtor, os dois
         concordam no mesmo erro e o rigor vira teatro. Por isso este módulo
         NÃO importa `radar_utils`, `rcc_emissor` nem `ibge_setor_ocupacao` —
         e um teste varre o arquivo para garantir.

POR QUE ESTA CAMADA EXISTE
--------------------------
O D15 foi encontrado olhando o ARTEFATO, não o dataframe: 100,00% dos 279.297
identificadores acima de 2^53 entregues em Porto Alegre estavam arredondados em
float64 — e os 19 gates aprovavam, porque G11 verifica DTYPE, `varredura_ids`
verifica NOME e o round-trip verifica SERIALIZAÇÃO. Os três olham a
REPRESENTAÇÃO. O VALOR já estava errado.

Daí os três níveis que o auditor cobre, nesta ordem:

    REPRESENTAÇÃO   o arquivo abre, tem as colunas certas, na ordem certa
    SEMÂNTICA       cada linha do modelo está lá, com os mesmos valores
    DERIVAÇÃO       o que é calculado FECHA quando recalculado por outro caminho

O QUE PODE E O QUE NÃO PODE SER REPARADO
----------------------------------------
Reparável HOJE, e só isto — a lista é a do código, não a da intenção:
    QA-EST-003   ordem de coluna divergente do contrato
    QA-IDENT-002 identificador com sufixo `.0` (rastro de float)
UMA tentativa, seguida de revalidação COMPLETA — nunca `while erro: tentar de
novo`, que esconde não-determinismo.

NÃO implementado (roadmap, e por isso NÃO prometido): restauração de zero à
esquerda, reparo de encoding, reordenação de linhas, tipo de célula em XLSX.
Anunciar capacidade que o código não tem é a mesma classe de defeito que este
módulo existe para combater.

XLSX não é reparável em nenhuma hipótese enquanto o QA por aba não existir:
regravar um workbook lido pela primeira aba achataria as outras.

Nunca reparável (significado): classificação, atividade, coletividade, natureza
da linha, contagem de unidades, polo, grau de evidência, destino de campo.
Divergência aqui é QUARENTENA. "Corrigi até passar" é o oposto de qualidade.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Versao do CONTRATO DO AUDITOR. 1.0 ficou parada enquanto a derivacao virava
# 100%, o manifest passou a reconciliar tudo, a publicacao mudou e o reader
# XLSX aprendeu multiaba — versao que nao versiona nao serve para forense.
VERSAO_QA = '1.3.0'

ESTADOS = ('PENDING', 'SEALED', 'CORRECTED_AND_SEALED',
           'SEALED_WITH_WARNINGS', 'QUARANTINED', 'FAILED')

# Campos que definem a IDENTIDADE SEMÂNTICA de uma linha. Não é o conjunto
# inteiro: metadados de execução mudam entre rodadas sem que o dado mude, e
# incluí-los faria toda reemissão parecer corrupção.
CAMPOS_SEMANTICOS = [
    'UNIDADE_ID', 'COLETIVA_ID', 'BLOCO_ID', 'COLETIVA_CHAVE_HASH',
    'ORIGEM_SISTEMA', 'ORIGEM_REGISTRO_ID',
    'END_MUNICIPIO', 'END_LOGRADOURO', 'END_NUMERO', 'END_LOCALIDADE',
    'UND_NATUREZA', 'UND_TIPO', 'UND_VALOR', 'UND_ECONOMIAS',
    'COL_FORMA', 'COL_USO', 'COL_CLASSE',
    'ATV_PRESENTE', 'ATV_SETOR',
    'EVD_REGRA', 'EVD_CLASSE', 'EVD_GRAU',
    'ACT_DESTINO_CAMPO',
]

# Constantes por grupo: variar dentro do mesmo COLETIVA_ID é defeito de grão.
CONSTANTES_DE_GRUPO = ['COL_FORMA', 'COL_USO', 'COL_CLASSE',
                       'COL_QTD_OBSERVADA', 'COL_QTD_INFERIDA',
                       'END_LOGRADOURO', 'END_NUMERO', 'END_LOCALIDADE']

_RE_CIENTIFICA = re.compile(r'^[+-]?\d+(\.\d+)?[eE][+-]?\d+$')
_RE_ID_COL = re.compile(r'(^|_)ID($|_)|_HASH$|^RADAR_ID')


# ─────────────────────────────────────────────────────────────────────────
@dataclass
class Issue:
    regra: str
    severidade: str                  # INFO | WARN | ERROR | FATAL
    bloco: str = ''
    linha: int | None = None
    chave: str = ''
    campo: str = ''
    esperado: str = ''
    encontrado: str = ''
    auto_corrigivel: bool = False
    acao: str = ''

    def dict(self):
        return {k: ('' if v is None else v) for k, v in self.__dict__.items()}


@dataclass
class Resultado:
    estado: str = 'PENDING'
    issues: list = field(default_factory=list)
    artifact_sha256: str = ''
    semantic_sha256: str = ''
    contract_sha256: str = ''
    blocos: dict = field(default_factory=dict)
    ciclo: int = 0
    caminho: str = ''
    correcoes: list = field(default_factory=list)

    @property
    def fatais(self):
        return [i for i in self.issues if i.severidade in ('FATAL', 'ERROR')]

    def selo(self) -> dict:
        return {
            'quality_seal': self.estado, 'qa_versao': VERSAO_QA,
            'checks_falhos': len(self.fatais),
            'checks_avisos': len([i for i in self.issues if i.severidade == 'WARN']),
            'artifact_sha256': self.artifact_sha256,
            'semantic_sha256': self.semantic_sha256,
            'contract_sha256': self.contract_sha256,
            'blocos': self.blocos, 'ciclo': self.ciclo,
            'correcoes_aplicadas': self.correcoes,
        }

    def issues_csv(self, destino):
        cols = list(Issue('x', 'y').dict())
        with open(destino, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter=';')
            w.writeheader()
            for i in self.issues:
                w.writerow(i.dict())
        return destino


# ─────────────────────────────────────────────────────────────────────────
#  contrato — a estrutura esperada vem do DICIONÁRIO, nunca de lista digitada
# ─────────────────────────────────────────────────────────────────────────
def contrato_rcc(caminho_dicionario) -> dict:
    with open(caminho_dicionario, encoding='utf-8-sig') as fh:
        linhas = list(csv.DictReader(fh, delimiter=';'))
    dominios = {r['CAMPO']: [v.strip() for v in r['DOMINIO'].split('|')]
                for r in linhas
                if '|' in r['DOMINIO'] and '<' not in r['DOMINIO']}
    bruto = open(caminho_dicionario, 'rb').read()
    return {
        'colunas': [r['CAMPO'] for r in linhas],
        'blocos': {r['CAMPO']: r['BLOCO'] for r in linhas},
        'tipos': {r['CAMPO']: r['TIPO'] for r in linhas},
        'dominios': dominios,
        'sha256': hashlib.sha256(bruto).hexdigest(),
    }


# ─────────────────────────────────────────────────────────────────────────
#  hashes
# ─────────────────────────────────────────────────────────────────────────
def sha256_arquivo(caminho, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(caminho, 'rb') as fh:
        while (b := fh.read(chunk)):
            h.update(b)
    return h.hexdigest()


_RE_PONTO_ZERO = re.compile(r'^(\d+)\.0$')


def _norm(v):
    """Normalização de COMPARAÇÃO — não de correção.

    O sufixo `.0` é rastro de float, não valor diferente. Se ele entrasse na
    comparação, um dano de REPRESENTAÇÃO (reparável) seria classificado como
    divergência SEMÂNTICA (quarentena) e o reparo nunca teria chance de rodar.
    A ordem certa é: normaliza para comparar, repara a representação, e só
    então julga significado.
    """
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ''
    s = str(v).strip()
    m = _RE_PONTO_ZERO.match(s)
    return m.group(1) if m else s


def hash_semantico(df, campos=None) -> str:
    """Hash dos VALORES, independente de metadados do formato.

    Um XLSX regravado muda de bytes sem mudar de conteúdo; um CSV reordenado,
    idem. Sem este par, `artifact mudou` e `conteúdo mudou` seriam a mesma
    pergunta — e são perguntas diferentes na hora de investigar.
    """
    campos = [c for c in (campos or CAMPOS_SEMANTICOS) if c in df.columns]
    if not campos:
        return hashlib.sha256(b'').hexdigest()
    m = df[campos].astype(object).map(_norm)
    corpo = '\n'.join('|'.join(r) for r in m.itertuples(index=False, name=None))
    return hashlib.sha256(('|'.join(campos) + '\n' + corpo).encode('utf-8')).hexdigest()


def hash_por_linha(df, campos=None) -> pd.Series:
    campos = [c for c in (campos or CAMPOS_SEMANTICOS) if c in df.columns]
    m = df[campos].astype(object).map(_norm)
    junto = m.apply(lambda r: '|'.join(r), axis=1)
    return junto.map(lambda s: hashlib.blake2b(s.encode('utf-8'),
                                               digest_size=16).hexdigest())


# ─────────────────────────────────────────────────────────────────────────
#  leitura do artefato — pelo parser do próprio formato
# ─────────────────────────────────────────────────────────────────────────
def reabrir(caminho):
    """CSV -> DataFrame; XLSX -> dict {aba: DataFrame}.

    `pd.read_excel(caminho)` sem `sheet_name=None` le SO a primeira aba. Se um
    reparo rodasse sobre isso e regravasse com `to_excel`, um workbook de seis
    abas viraria uma planilha. Workbook nao e' "um DataFrame salvo em Excel" —
    e' outro tipo de artefato, e o auditor precisa trata-lo como tal.
    """
    ext = os.path.splitext(caminho)[1].lower()
    if ext in ('.xlsx', '.xlsm'):
        return pd.read_excel(caminho, dtype=str, sheet_name=None)
    return pd.read_csv(caminho, sep=';', dtype=str, encoding='utf-8-sig',
                       low_memory=False, keep_default_na=False)


def abas_xlsx(caminho) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(caminho, read_only=True)
    try:
        return {ws.title: max(0, ws.max_row - 1) for ws in wb.worksheets}
    finally:
        wb.close()


# ─────────────────────────────────────────────────────────────────────────
#  validadores
# ─────────────────────────────────────────────────────────────────────────
def _estrutura(lido_cols, contrato, res):
    esperadas = contrato['colunas']
    dup = [c for c in set(lido_cols) if list(lido_cols).count(c) > 1]
    dup += [c for c in lido_cols if c.endswith('.1')]
    if dup:
        res.issues.append(Issue('QA-EST-004', 'FATAL', campo=str(sorted(set(dup))[:3]),
                                acao='coluna duplicada no cabecalho'))
    faltando = [c for c in esperadas if c not in lido_cols]
    sobrando = [c for c in lido_cols if c not in esperadas and not c.endswith('.1')]
    if faltando:
        res.issues.append(Issue('QA-EST-001', 'FATAL', esperado=str(faltando[:5]),
                                acao='coluna do contrato ausente no artefato'))
    if sobrando:
        res.issues.append(Issue('QA-EST-002', 'FATAL', encontrado=str(sobrando[:5]),
                                acao='coluna fora do contrato'))
    if not faltando and not sobrando and list(lido_cols) != esperadas:
        res.issues.append(Issue('QA-EST-003', 'ERROR', auto_corrigivel=True,
                                acao='ordem de coluna diverge do contrato'))


def _identificadores(lido, res):
    """Nível REPRESENTAÇÃO dos ids — o que o D15 provou não bastar sozinho."""
    for c in lido.columns:
        if not _RE_ID_COL.search(c):
            continue
        s = lido[c].fillna('').astype(str).str.strip()
        s = s[s.ne('')]
        if not len(s):
            continue
        sci = s[s.map(lambda v: bool(_RE_CIENTIFICA.match(v)))]
        if len(sci):
            res.issues.append(Issue(
                'QA-IDENT-001', 'FATAL', bloco='IDENT', campo=c,
                encontrado=str(sci.iloc[0]),
                acao='identificador em notacao cientifica no artefato'))
        pontos = s[s.str.fullmatch(r'\d+\.0')]
        if len(pontos):
            res.issues.append(Issue(
                'QA-IDENT-002', 'ERROR', bloco='IDENT', campo=c,
                encontrado=str(pontos.iloc[0]), auto_corrigivel=True,
                acao='identificador com sufixo .0 (passou por float)'))


def _linhas(lido, modelo, res):
    """Comparação POR LINHA. Contagem global não vê troca compensada."""
    if modelo is None:
        return
    if len(lido) != len(modelo):
        res.issues.append(Issue('QA-LIN-001', 'FATAL',
                                esperado=str(len(modelo)), encontrado=str(len(lido)),
                                acao='quantidade de linhas diverge do modelo'))
    he = hash_por_linha(modelo)
    ho = hash_por_linha(lido)
    ce, co = he.value_counts(), ho.value_counts()
    sumidas = (ce - co.reindex(ce.index, fill_value=0))
    sumidas = sumidas[sumidas > 0]
    novas = (co - ce.reindex(co.index, fill_value=0))
    novas = novas[novas > 0]
    if len(sumidas):
        i = he[he == sumidas.index[0]].index[0]
        res.issues.append(Issue(
            'QA-LIN-002', 'FATAL', linha=int(i),
            chave=str(modelo.iloc[i].get('UNIDADE_ID', '')),
            acao=f'{int(sumidas.sum())} linha(s) do modelo ausentes no artefato'))
    if len(novas):
        i = ho[ho == novas.index[0]].index[0]
        res.issues.append(Issue(
            'QA-LIN-003', 'FATAL', linha=int(i),
            chave=str(lido.iloc[i].get('UNIDADE_ID', '')),
            acao=f'{int(novas.sum())} linha(s) no artefato sem origem no modelo'))
    # diff campo a campo, para a mensagem ser acionável
    if (len(sumidas) or len(novas)) and 'UNIDADE_ID' in lido.columns:
        # index UNICO dos dois lados: com duplicata (que e' justamente um dos
        # danos que este auditor procura) o alinhamento explode em IndexError
        # e o auditor quebra no lugar do produto.
        m = modelo.drop_duplicates('UNIDADE_ID').set_index('UNIDADE_ID', drop=False)
        a = lido.drop_duplicates('UNIDADE_ID').set_index('UNIDADE_ID', drop=False)
        comuns = m.index.intersection(a.index)[:2000]
        for c in [x for x in CAMPOS_SEMANTICOS if x in lido.columns]:
            de = m.loc[comuns, c].astype(object).map(_norm)
            pa = a.loc[comuns, c].astype(object).map(_norm)
            dif = de[de.ne(pa)]
            if len(dif):
                k = dif.index[0]
                res.issues.append(Issue(
                    'QA-LIN-004', 'FATAL', campo=c, chave=str(k),
                    esperado=str(de.loc[k]), encontrado=str(pa.loc[k]),
                    acao=f'{len(dif)} divergencia(s) de valor no campo'))
                break


def _dominios(lido, contrato, res):
    for campo, valores in contrato['dominios'].items():
        if campo not in lido.columns:
            continue
        s = lido[campo].fillna('').astype(str).str.strip()
        fora = sorted(set(s[s.ne('')]) - set(valores))
        if fora:
            res.issues.append(Issue(
                'QA-DOM-001', 'FATAL', bloco=contrato['blocos'].get(campo, ''),
                campo=campo, encontrado=str(fora[:3]),
                acao='valor fora do dominio fechado do contrato'))


def _bloco_col(lido, res):
    """R14 — RECALCULA a contagem do grupo. Não confia na coluna."""
    if 'COLETIVA_ID' not in lido.columns or 'UND_NATUREZA' not in lido.columns:
        return
    com = lido[lido['COLETIVA_ID'].fillna('').astype(str).str.strip().ne('')]
    if not len(com):
        return
    for col, nat in (('COL_QTD_OBSERVADA', 'OBSERVADO'),
                     ('COL_QTD_INFERIDA', 'INFERIDO')):
        if col not in com.columns:
            continue
        real = com.assign(_x=(com['UND_NATUREZA'] == nat).astype(int)) \
                  .groupby('COLETIVA_ID')['_x'].sum()
        decl = pd.to_numeric(com.groupby('COLETIVA_ID')[col].first(), errors='coerce')
        maus = real.index[real.ne(decl)]
        if len(maus):
            k = maus[0]
            res.issues.append(Issue(
                'QA-COL-001', 'FATAL', bloco='COL', campo=col, chave=str(k),
                esperado=str(int(real[k])), encontrado=str(decl[k]),
                acao=f'{len(maus)} grupo(s) com contagem declarada != recalculada'))
    for c in [x for x in CONSTANTES_DE_GRUPO if x in com.columns]:
        n = com.groupby('COLETIVA_ID')[c].nunique(dropna=False)
        maus = n.index[n > 1]
        if len(maus):
            res.issues.append(Issue(
                'QA-COL-002', 'FATAL', bloco='COL', campo=c, chave=str(maus[0]),
                acao=f'{len(maus)} grupo(s) com atributo de GRUPO variando na linha'))


def _bloco_set(lido, res):
    """R14 — reprova a aritmética do setor por conta própria."""
    need = ['SET_DOM_PARTICULARES', 'SET_DOM_OCUPADOS',
            'SET_DOM_USO_OCASIONAL', 'SET_DOM_VAGOS']
    if not set(need) <= set(lido.columns) or 'SET_COD' not in lido.columns:
        return
    s = lido[['SET_COD'] + need + (['SET_DOM_SEM_OCUPACAO_HABITUAL'] if 'SET_DOM_SEM_OCUPACAO_HABITUAL'
                                   in lido.columns else [])].drop_duplicates('SET_COD')
    for c in need:
        s[c] = pd.to_numeric(s[c], errors='coerce')
    s = s.dropna(subset=need)
    if not len(s):
        return
    soma = s[need[1:]].sum(axis=1)
    maus = s[soma.ne(s['SET_DOM_PARTICULARES'])]
    if len(maus):
        r = maus.iloc[0]
        res.issues.append(Issue(
            'QA-SET-001', 'FATAL', bloco='SET', campo='SET_DOM_PARTICULARES',
            chave=str(r['SET_COD']),
            esperado=str(int(soma.loc[maus.index[0]])),
            encontrado=str(int(r['SET_DOM_PARTICULARES'])),
            acao=f'{len(maus)} setor(es) sem fechamento ocupados+ocasional+vagos'))
    if 'SET_DOM_SEM_OCUPACAO_HABITUAL' in s.columns:
        d = pd.to_numeric(s['SET_DOM_SEM_OCUPACAO_HABITUAL'], errors='coerce')
        esp = s['SET_DOM_USO_OCASIONAL'] + s['SET_DOM_VAGOS']
        m2 = s[d.ne(esp)]
        if len(m2):
            res.issues.append(Issue(
                'QA-SET-002', 'FATAL', bloco='SET', campo='SET_DOM_SEM_OCUPACAO_HABITUAL',
                chave=str(m2.iloc[0]['SET_COD']),
                acao='desocupados != uso ocasional + vagos'))


def _canon_rastreio(v) -> str:
    """Reimplementação PRÓPRIA da canonicalização (R14).

    Deliberadamente NÃO importa a do produtor. Se divergir, o auditor acusa —
    e divergência entre a regra escrita e a regra executada é exatamente o que
    precisa aparecer.
    """
    import unicodedata
    s = unicodedata.normalize('NFKD', str(v or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Z0-9 ]', ' ', s.upper())).strip()


def _id_endereco_rederivado(mun, logradouro, numero, localidade) -> int | None:
    """BLAKE2b(8) >> 1 da chave canônica — recomposta dos campos END_*."""
    try:
        n = int(float(numero))
        m = int(float(mun))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    c = f'{m:07d}|{_canon_rastreio(logradouro)}|N{n}|{_canon_rastreio(localidade)}'
    d = hashlib.blake2b(c.encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(d, 'big') >> 1


def _id64_indep(canonica) -> int:
    """BLAKE2b(8) >> 1 — reimplementado, não importado (R14)."""
    d = hashlib.blake2b(str(canonica).encode('utf-8'), digest_size=8).digest()
    return int.from_bytes(d, 'big') >> 1


def _derivacao_pela_canonica(lido, res) -> bool:
    """QA-DER-001 pela PRÉ-IMAGEM publicada — a rederivação de verdade.

    A versão anterior recompunha a canônica a partir de `END_LOGRADOURO`. Só
    que a canônica usa o nome HARMONIZADO e NÃO leva o título, enquanto
    `END_LOGRADOURO` leva: em Porto Alegre isso é 23% das linhas, e o auditor
    acusava 53.394 ids "que não derivam do endereço". Não derivavam mesmo — o
    dado publicado não permitia derivar. Recompor por adivinhação seria trocar
    um falso positivo por um falso negativo.

    Com a pré-imagem publicada, o teste é o certo e cobre 100%. E a canônica
    não pode ser forjada à vontade: `_coerencia_canonica` amarra município,
    número e tokens de logradouro ao endereço publicado na mesma linha.
    """
    if 'COLETIVA_CHAVE_CANONICA' not in lido.columns:
        return False
    par = lido[['COLETIVA_CHAVE_CANONICA', 'COLETIVA_CHAVE_HASH']].copy()
    par['_c'] = par['COLETIVA_CHAVE_CANONICA'].fillna('').astype(str).str.strip()
    par['_h'] = par['COLETIVA_CHAVE_HASH'].fillna('').astype(str).map(_norm)
    par = par[par['_c'].ne('') & par['_h'].ne('')].drop_duplicates(['_c', '_h'])
    if not len(par):
        return False
    maus = [(c, _id64_indep(c), h) for c, h in zip(par['_c'], par['_h'])
            if str(_id64_indep(c)) != h]
    if maus:
        c, e, o = maus[0]
        res.issues.append(Issue(
            'QA-DER-001', 'FATAL', bloco='IDENT', campo='COLETIVA_CHAVE_HASH',
            chave=str(c)[:60], esperado=str(e), encontrado=str(o),
            acao=f'{len(maus)} de {len(par)} hash(es) nao sao o BLAKE2b da '
                 'chave canonica publicada na propria linha'))
    _coerencia_canonica(lido, res)
    return True


def _coerencia_canonica(lido, res):
    """A canônica publicada tem de FALAR DO ENDEREÇO da linha.

    Sem isto, publicar a pré-imagem só moveria a confiança de lugar: bastaria
    escrever qualquer string e o hash dela para `QA-DER-001` passar. Município
    e número são exatos; do logradouro exige-se CONTINÊNCIA de tokens (a
    canônica descarta o título — pode ter menos, nunca ter o que a linha não
    tem).
    """
    need = {'END_MUNICIPIO', 'END_NUMERO', 'END_LOGRADOURO'}
    if not need <= set(lido.columns):
        return
    s = lido[['COLETIVA_CHAVE_CANONICA'] + sorted(need)].copy()
    s['_c'] = s['COLETIVA_CHAVE_CANONICA'].fillna('').astype(str).str.strip()
    s = s[s['_c'].ne('')].drop_duplicates('_c')
    maus = []
    for c, mun, num, logr in zip(s['_c'], s['END_MUNICIPIO'], s['END_NUMERO'],
                                 s['END_LOGRADOURO']):
        p = c.split('|')
        if len(p) < 4:
            maus.append((c, 'forma <mun>|<logr>|<ident>|<loc>')); continue
        try:
            if int(p[0]) != int(float(str(mun).strip())):
                maus.append((c, f'municipio {mun}')); continue
        except (TypeError, ValueError):
            maus.append((c, f'municipio {mun}')); continue
        if p[2].startswith('N'):
            try:
                if int(p[2][1:]) != int(float(str(num).strip())):
                    maus.append((c, f'numero {num}')); continue
            except (TypeError, ValueError):
                maus.append((c, f'numero {num}')); continue
        toks_c = set(_canon_rastreio(p[1]).split())
        toks_l = set(_canon_rastreio(logr).split())
        if not toks_c <= toks_l:
            maus.append((c, f'logradouro {logr} nao contem {sorted(toks_c - toks_l)}'))
    if maus:
        c, motivo = maus[0]
        res.issues.append(Issue(
            'QA-DER-006', 'FATAL', bloco='IDENT',
            campo='COLETIVA_CHAVE_CANONICA', chave=str(c)[:60],
            encontrado=str(motivo)[:80],
            acao=f'{len(maus)} chave(s) canonica(s) incoerentes com o endereco '
                 'publicado na mesma linha'))


def _derivacao(lido, res, amostra=None):
    """QA-DER — REDERIVA o id do endereço em vez de comparar com o modelo.

    Comparar artefato com modelo não detecta nada se o MODELO já estiver
    errado: foi assim que 279.297 identificadores corrompidos (D15) passaram
    por 19 gates. Aqui o auditor recalcula por caminho próprio.
    """
    if _derivacao_pela_canonica(lido, res):
        return
    need = {'COLETIVA_CHAVE_HASH', 'END_MUNICIPIO', 'END_LOGRADOURO',
            'END_NUMERO', 'END_LOCALIDADE'}
    if not need <= set(lido.columns):
        return
    s = lido[list(need)].copy()
    s = s[s['COLETIVA_CHAVE_HASH'].fillna('').astype(str).str.strip().ne('')]
    if not len(s):
        return
    # 100% dos identificadores distintos. Identificador estrutural NAO se
    # audita por amostra: com 6.001 enderecos e o erro no 5.501, o corte de
    # 5.000 selava o artefato — inclusive com o modelo carregando o mesmo erro,
    # que e' exatamente o cenario que o R14 existe para pegar.
    s = s.drop_duplicates('COLETIVA_CHAVE_HASH')
    if amostra:
        s = s.head(amostra)
    esp = [_id_endereco_rederivado(a, b, c, d_)
           for a, b, c, d_ in zip(s['END_MUNICIPIO'], s['END_LOGRADOURO'],
                                  s['END_NUMERO'], s['END_LOCALIDADE'])]
    obt = pd.to_numeric(s['COLETIVA_CHAVE_HASH'].map(_norm), errors='coerce')
    maus, total = [], 0
    for h, e, o in zip(s['COLETIVA_CHAVE_HASH'], esp, obt):
        if e is None or pd.isna(o):
            continue
        total += 1
        if int(o) != e:
            maus.append((h, e, int(o)))
    if not total:
        return
    if maus:
        h, esp, obt = maus[0]
        res.issues.append(Issue(
            'QA-DER-001', 'FATAL', bloco='IDENT', campo='COLETIVA_CHAVE_HASH',
            chave=str(h), esperado=str(esp), encontrado=str(obt),
            acao=f'{len(maus)} de {total} id(s) nao derivam do endereco '
                 '(rederivacao independente do produtor)'))


# Vocabulário de tipo de via — cópia PRÓPRIA do auditor (R14). Diverge da do
# produtor? Então uma das duas listas está desatualizada, e é exatamente isso
# que precisa aparecer.
TIPOS_VIA_CONHECIDOS = {
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
    'AREA', 'BARRO', 'CONJUNTO HABITACIONAL', 'ESTANCIA', 'HABITACIONAL',
    'LIGACAO', 'PONTE', 'RUA DE PEDESTRE', 'RUA PRINCIPAL', 'TRILHA',
    'ESCADA', 'TRAVESSIA',
}


# Avisos que NÃO escalam para quarentena nem em `strict`. A lista é curta e
# cada entrada precisa de justificativa: aqui, o vocabulário de tipo de via
# estar incompleto é o estado NORMAL — Porto Alegre sozinha trouxe 12 tipos que
# sete UFs auditadas não tinham, e duas delas só apareceram quando o ciclo
# rodou na base inteira. Reprovar a entrega do IBGE porque a NOSSA lista é
# curta inverteria quem é a fonte. Um aviso que reprova não é um aviso.
AVISOS_INFORMATIVOS = {'QA-END-010'}


def _colisao_indep(formas, canonicas) -> dict:
    """Recontagem própria do fator de colisão — sem chamar o produtor."""
    mapa = {}
    for f, c in zip(formas, canonicas):
        mapa.setdefault(c, set()).add(_canon_rastreio(f))
    n_f = len({x for s in mapa.values() for x in s})
    n_c = len(mapa)
    fund = sorted((c, sorted(s)) for c, s in mapa.items() if len(s) > 1)
    return {'formas_distintas': int(n_f), 'canonicas_distintas': int(n_c),
            'fator': round(n_f / n_c, 4) if n_c else 1.0,
            'grupos_fundidos': int(len(fund)),
            'exemplos': [{'canonica': c, 'formas': f[:4]} for c, f in fund[:5]]}


def _vocabulario_via(lido, res):
    """QA-END-010 — tipo de via fora do vocabulário canônico.

    Sempre WARN, nunca FATAL, e nunca correção. O vocabulário estar incompleto
    é o estado NORMAL — Porto Alegre sozinha trouxe 10 tipos que sete UFs
    auditadas não tinham. Reprovar a entrega porque a nossa lista é curta
    inverteria quem é a fonte: o IBGE grava, nós conferimos.
    """
    if 'END_LOGRADOURO' not in lido.columns:
        return
    s = lido['END_LOGRADOURO'].fillna('').astype(str).str.strip()
    s = s[s.ne('')].drop_duplicates()
    if not len(s):
        return
    desconhecidos = {}
    for v in s:
        t = _canon_rastreio(v).split()
        if not t:
            continue
        tipo = ' '.join(t[:2]) if ' '.join(t[:2]) in TIPOS_VIA_CONHECIDOS else t[0]
        if tipo not in TIPOS_VIA_CONHECIDOS:
            desconhecidos.setdefault(tipo, v)
    if desconhecidos:
        k = sorted(desconhecidos)[:5]
        res.issues.append(Issue(
            'QA-END-010', 'WARN', bloco='END', campo='END_LOGRADOURO',
            encontrado=', '.join(k)[:80],
            chave=str(desconhecidos[k[0]])[:60],
            acao=f'{len(desconhecidos)} tipo(s) de via fora do vocabulario '
                 'canonico — ampliar o vocabulario, NUNCA reescrever o dado'))


def manifest_observado(df) -> dict:
    """Métricas do manifest DERIVADAS do artefato, pelo auditor (R14).

    `QA-MAN` conferia 3 campos enquanto o manifest declarava uma dúzia:
    `com_atividade` e `grupos_coletivos` podiam ir a 999.999 e o pacote era
    selado. Reconciliar parcialmente é pior que não reconciliar, porque
    transmite a confiança inteira.
    """
    def _s(c):
        return df[c].fillna('').astype(str).str.strip() if c in df.columns \
            else pd.Series(dtype=str)
    nat = _s('UND_NATUREZA')
    cid = _s('COLETIVA_ID')
    forma = _s('COL_FORMA')
    obs = {
        'linhas': int(len(df)),
        'observadas': int((nat == 'OBSERVADO').sum()),
        'inferidas': int((nat == 'INFERIDO').sum()),
        'coletivas_distintas': int(cid[cid.ne('')].nunique()),
        'unidades_em_coletiva': int((forma.ne('') & forma.ne('INDEFINIDA')).sum()),
        'grupos_coletivos': int(cid[forma.ne('') & forma.ne('INDEFINIDA')].nunique()),
        'com_atividade': int((_s('ATV_PRESENTE') == 'SIM').sum()),
    }
    if 'EVD_GRAU' in df.columns:
        g = _s('EVD_GRAU')[nat == 'INFERIDO']
        obs['inferidas_por_grau'] = {k: int(v) for k, v in
                                     g[g.ne('')].value_counts().items()}
    if 'EVD_CLASSE' in df.columns:
        c = _s('EVD_CLASSE')[nat == 'INFERIDO']
        obs['inferidas_por_classe'] = {k: int(v) for k, v in
                                       c[c.ne('')].value_counts().items()}
    if 'ACT_DESTINO_CAMPO' in df.columns:
        dd = _s('ACT_DESTINO_CAMPO')
        obs['destino_campo'] = {k: int(v) for k, v in
                                dd[dd.ne('')].value_counts().items()}
    # Fator de colisão de logradouro, RECALCULADO do artefato (R14). O produtor
    # declara o dele; se a canonicalização começar a fundir ruas de verdade, os
    # dois números divergem e o pacote não fecha.
    if {'END_LOGRADOURO_ORIG', 'COLETIVA_CHAVE_CANONICA'} <= set(df.columns):
        f = _s('END_LOGRADOURO_ORIG')
        c = _s('COLETIVA_CHAVE_CANONICA').map(
            lambda v: v.split('|')[1] if v.count('|') >= 3 else '')
        m = f.ne('') & c.ne('')
        obs['colisao_logradouro'] = _colisao_indep(f[m], c[m])
    # bloco SET: a cobertura declarada era medida no dataframe INTERMEDIÁRIO
    # (antes das linhas inferidas) e nunca reconciliada. `pct_casado: 100` com
    # a coluna inteira vazia era um manifest válido. Aqui a métrica é derivada
    # do ARTEFATO, que é o que o cliente recebe.
    if 'SET_COD' in df.columns:
        cod, dom = _s('SET_COD'), _s('SET_DOM_PARTICULARES')
        casado = dom.ne('')
        obs['bloco_set'] = {
            'linhas': int(len(df)),
            'com_setor_casado': int(casado.sum()),
            'pct_casado': (round(100.0 * float(casado.mean()), 2)
                           if len(df) else 0.0),
            'setores_distintos': int(cod[cod.ne('')].nunique()),
        }
    return obs


# Reimplementação PRÓPRIA da tabela de siglas do produtor (R14). Copiar a
# constante por import faria auditor e produtor concordarem por construção:
# renomear 'LOJA'->'LOJ' para 'LOJA'->'XXX' no produtor passaria a ser
# "correto" para os dois. Aqui a tabela é uma AFIRMAÇÃO INDEPENDENTE do que a
# sigla tem de ser; divergir dela é o defeito.
SIGLA_INDEP = {'APARTAMENTO': 'APT', 'CASA': 'CAS', 'SALA': 'SAL',
               'LOJA': 'LOJ', 'BOX': 'BOX', 'LOTE': 'LOT', 'SOBRADO': 'SOB',
               'QUITINETE': 'QUI', 'QUARTO': 'QRT', 'GALPAO': 'GAL',
               'CONJUNTO': 'CJT'}
TAG_SEM_VALOR = 'REG'        # unidade observada sem discriminante próprio
TAG_INFERIDO = 'INF'         # hipótese
TAG_GENERICA = 'UND'         # tipo fora da tabela


def _tag_esperada(natureza, tipo, valor) -> str:
    """A TAG de `UNIDADE_ID` REDERIVADA — não é decoração, é classificação.

    `...-LOJ-0001` virar `...-XXX-0001` passava por QA-DER-004: a checagem
    olhava só o corpo (`partes[1]`) e ignorava a TAG. Um identificador cujo
    segmento de TIPO pode ser qualquer coisa não identifica o tipo.
    """
    if str(natureza or '').strip().upper() == 'INFERIDO':
        return TAG_INFERIDO
    if str(valor or '').strip():
        return SIGLA_INDEP.get(str(tipo or '').strip().upper(), TAG_GENERICA)
    return TAG_SEM_VALOR


def _store_coletivas(store):
    """Lê o store append-only de identidade — a FONTE da estabilidade do id.

    Sem ele o auditor só sabe conferir coerência INTERNA: trocar duas
    `COLETIVA_ID` entre si mantém forma, município e 1:1 intactos e passa por
    QA-DER-002 inteiro. A identidade histórica não é recalculável — é
    consultável.
    """
    if not store or not os.path.exists(store):
        return None
    mapa = {}
    try:
        with open(store, encoding='utf-8') as fh:
            for r in csv.DictReader(fh, delimiter=';'):
                h = _norm(str(r.get('COLETIVA_CHAVE_HASH', '') or '').strip())
                c = str(r.get('COLETIVA_ID', '') or '').strip()
                if h and c:
                    mapa[h] = c
    except (OSError, csv.Error, UnicodeDecodeError):
        return None
    return mapa or None


def _slug_indep(v, n=4) -> str:
    """Reimplementação do slug do produtor (R14) — deliberadamente própria."""
    s_ = str(v or '').strip().upper()
    if not s_:
        return '0000'
    if s_.isdigit():
        return s_.zfill(n)
    return ''.join(ch for ch in s_ if ch.isalnum())[:6].ljust(n, '0')


def _derivacao_ids_compostos(lido, res, store=None):
    """QA-DER-002/003/004 — os identificadores COMPOSTOS também derivam.

    `COLETIVA_ID` vem de store append-only e não é recalculável sem ele, mas a
    sua FORMA e a sua relação 1:1 com `COLETIVA_CHAVE_HASH` são. `BLOCO_ID` e
    `UNIDADE_ID` são derivados por composição — e composição se reconstrói.

    Cenário que passava: adulterar `UNIDADE_ID` no artefato E no modelo. Como
    só o hash era rederivado, produtor e auditor concordavam no mesmo erro.
    """
    cid = lido.get('COLETIVA_ID')
    if cid is None:
        return
    cid = cid.fillna('').astype(str).str.strip()
    tem = cid.ne('')
    if not tem.any():
        return

    # QA-DER-002a: forma COL-<mun 7>-<seq 6>
    forma = cid[tem].str.fullmatch(r'COL-\d{7}-\d{6}')
    if not forma.all():
        ex = cid[tem][~forma].iloc[0]
        res.issues.append(Issue('QA-DER-002', 'FATAL', bloco='IDENT',
                                campo='COLETIVA_ID', encontrado=str(ex),
                                acao='COLETIVA_ID fora da forma canonica'))
    # QA-DER-002b: municipio embutido bate com END_MUNICIPIO
    if 'END_MUNICIPIO' in lido.columns:
        mun_id = cid[tem].str.slice(4, 11)
        mun_col = lido.loc[tem, 'END_MUNICIPIO'].fillna('').astype(str) \
                      .str.strip().str.zfill(7)
        maus = mun_id.ne(mun_col)
        if maus.any():
            res.issues.append(Issue(
                'QA-DER-002', 'FATAL', bloco='IDENT', campo='COLETIVA_ID',
                chave=str(cid[tem][maus].iloc[0]),
                acao=f'{int(maus.sum())} COLETIVA_ID com municipio divergente '
                     'de END_MUNICIPIO'))
    # QA-DER-002c: relação 1:1 com o hash canônico
    if 'COLETIVA_CHAVE_HASH' in lido.columns:
        par = lido.loc[tem, ['COLETIVA_ID', 'COLETIVA_CHAVE_HASH']].copy()
        par['COLETIVA_CHAVE_HASH'] = par['COLETIVA_CHAVE_HASH'].fillna('') \
                                        .astype(str).map(_norm)
        par = par[par['COLETIVA_CHAVE_HASH'].ne('')].drop_duplicates()
        n1 = par.groupby('COLETIVA_ID')['COLETIVA_CHAVE_HASH'].nunique()
        n2 = par.groupby('COLETIVA_CHAVE_HASH')['COLETIVA_ID'].nunique()
        if (n1 > 1).any() or (n2 > 1).any():
            res.issues.append(Issue(
                'QA-DER-002', 'FATAL', bloco='IDENT', campo='COLETIVA_ID',
                acao='COLETIVA_ID e COLETIVA_CHAVE_HASH nao sao 1:1'))

    # QA-DER-005: confronto com o STORE — a única prova de identidade HISTÓRICA
    # Forma, município e 1:1 são propriedades INTERNAS: trocar COL-...-000001 e
    # COL-...-000002 entre si preserva as três e quebra a série temporal, que é
    # a única razão de o id ser sequencial e não recalculado.
    mapa = _store_coletivas(store)
    if mapa and 'COLETIVA_CHAVE_HASH' in lido.columns:
        par = lido.loc[tem, ['COLETIVA_ID', 'COLETIVA_CHAVE_HASH']].copy()
        par['_h'] = par['COLETIVA_CHAVE_HASH'].fillna('').astype(str).map(_norm)
        par = par[par['_h'].ne('')].drop_duplicates(['COLETIVA_ID', '_h'])
        maus, orfaos = [], 0
        for cid_, h_ in zip(par['COLETIVA_ID'], par['_h']):
            esperado = mapa.get(h_)
            if esperado is None:
                orfaos += 1
            elif esperado != cid_:
                maus.append((h_, esperado, cid_))
        if maus:
            h_, e_, o_ = maus[0]
            res.issues.append(Issue(
                'QA-DER-005', 'FATAL', bloco='IDENT', campo='COLETIVA_ID',
                chave=str(h_), esperado=str(e_), encontrado=str(o_),
                acao=f'{len(maus)} COLETIVA_ID divergem do store de identidade '
                     '(id historico trocado ou reatribuido)'))
        if orfaos:
            res.issues.append(Issue(
                'QA-DER-005', 'WARN', bloco='IDENT', campo='COLETIVA_ID',
                encontrado=str(orfaos),
                acao=f'{orfaos} endereco(s) do artefato sem registro no store — '
                     'id nao rastreavel entre safras'))

    # QA-DER-003: BLOCO_ID = COLETIVA_ID + '-B' + UND_BLOCO
    if {'BLOCO_ID', 'UND_BLOCO'} <= set(lido.columns):
        b = lido['BLOCO_ID'].fillna('').astype(str).str.strip()
        v = lido['UND_BLOCO'].fillna('').astype(str).str.strip()
        m = b.ne('') & v.ne('') & tem
        if m.any():
            esperado = cid[m] + '-B' + v[m]
            maus = b[m].ne(esperado)
            if maus.any():
                res.issues.append(Issue(
                    'QA-DER-003', 'FATAL', bloco='IDENT', campo='BLOCO_ID',
                    esperado=str(esperado[maus].iloc[0]),
                    encontrado=str(b[m][maus].iloc[0]),
                    acao=f'{int(maus.sum())} BLOCO_ID nao deriva de '
                         'COLETIVA_ID + UND_BLOCO'))

    # QA-DER-004: UNIDADE_ID deriva do grupo (composicionalidade)
    if 'UNIDADE_ID' in lido.columns:
        u = lido['UNIDADE_ID'].fillna('').astype(str).str.strip()
        base = lido['BLOCO_ID'].fillna('').astype(str).str.strip() \
            if 'BLOCO_ID' in lido.columns else pd.Series('', index=lido.index)
        # Registro sem identidade de endereço não tem COLETIVA_ID e o produtor
        # usa a raiz `SEM-<mun 7>`. Deixá-la fora da checagem criava um bolsão
        # de UNIDADE_ID sem nenhuma auditoria de composição.
        sem = ('SEM-' + lido['END_MUNICIPIO'].fillna('').astype(str)
               .str.strip().str.zfill(7)) if 'END_MUNICIPIO' in lido.columns \
            else pd.Series('', index=lido.index)
        raiz = base.where(base.ne(''), cid)
        raiz = raiz.where(raiz.ne(''), sem)
        m = u.ne('') & raiz.ne('')
        pref = [uu.startswith(rr + '-') for uu, rr in zip(u[m], raiz[m])]
        if not all(pref):
            i = pref.index(False)
            res.issues.append(Issue(
                'QA-DER-004', 'FATAL', bloco='IDENT', campo='UNIDADE_ID',
                chave=str(u[m].iloc[i]), esperado=str(raiz[m].iloc[i]) + '-...',
                acao=f'{pref.count(False)} UNIDADE_ID nao deriva do seu grupo'))
        # O SUFIXO é REDERIVADO do valor da unidade, não apenas conferido na
        # forma: checar só o formato deixava passar `...-REG-10000457X`, que é
        # alfanumérico e portanto "bem-formado". Derivar é comparar o valor.
        cauda = pd.Series([uu[len(rr) + 1:] for uu, rr in zip(u[m], raiz[m])],
                          index=u[m].index)
        val = (lido.loc[m, 'UND_VALOR'].fillna('').astype(str).str.strip()
               if 'UND_VALOR' in lido.columns
               else pd.Series('', index=cauda.index))
        reg = (lido.loc[m, 'ORIGEM_REGISTRO_ID'].fillna('').astype(str).str.strip()
               if 'ORIGEM_REGISTRO_ID' in lido.columns
               else pd.Series('', index=cauda.index))
        nat = (lido.loc[m, 'UND_NATUREZA'].fillna('').astype(str).str.strip()
               if 'UND_NATUREZA' in lido.columns
               else pd.Series('', index=cauda.index))
        tip = (lido.loc[m, 'UND_TIPO'].fillna('').astype(str).str.strip()
               if 'UND_TIPO' in lido.columns
               else pd.Series('', index=cauda.index))
        maus_c, maus_t = [], []
        for idx, c_ in cauda.items():
            partes = c_.split('-')
            if len(partes) < 2:
                maus_c.append((idx, c_, '<TAG>-<valor>')); continue
            # A TAG É DERIVADA, não conferida na forma. Sem isto, `-LOJ-0001`
            # virava `-XXX-0001` e o artefato era selado: o segmento que diz
            # QUE TIPO de unidade é aquela podia ser qualquer coisa.
            esp_tag = _tag_esperada(nat.get(idx, ''), tip.get(idx, ''),
                                    val.get(idx, ''))
            if partes[0] != esp_tag:
                maus_t.append((idx, c_, esp_tag, partes[0]))
            corpo = partes[1]                      # ignora TAG e ordinal -Nnn
            candidatos = {_slug_indep(val.get(idx, '')),
                          _slug_indep(reg.get(idx, '')),
                          str(val.get(idx, '')).strip().upper(),
                          str(reg.get(idx, '')).strip()}
            if corpo not in candidatos:
                maus_c.append((idx, c_, ' | '.join(sorted(x for x in candidatos if x))))
        if maus_c:
            idx, enc, esp = maus_c[0]
            res.issues.append(Issue(
                'QA-DER-004', 'FATAL', bloco='IDENT', campo='UNIDADE_ID',
                chave=str(u.get(idx, '')), esperado=esp[:60], encontrado=enc,
                acao=f'{len(maus_c)} UNIDADE_ID cujo sufixo NAO deriva de '
                     'UND_VALOR nem de ORIGEM_REGISTRO_ID'))
        if maus_t:
            idx, enc, esp, obt = maus_t[0]
            res.issues.append(Issue(
                'QA-DER-004', 'FATAL', bloco='IDENT', campo='UNIDADE_ID',
                chave=str(u.get(idx, '')), esperado=esp, encontrado=obt,
                acao=f'{len(maus_t)} UNIDADE_ID cuja TAG nao deriva de '
                     'UND_NATUREZA/UND_TIPO'))


def _comparavel(v):
    """Normaliza para comparação SEM destruir a informação.

    A versão anterior fazia `int(v)` nos dicionários: `pct_casado` 12.34 virava
    12 e qualquer valor entre 12,00 e 12,99 era aceito. Comparar métrica de
    ponto flutuante como inteiro é um gate que só parece existir.
    """
    if isinstance(v, bool):
        return v
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return str(v).strip()


def _manifest(lido, manifest, res):
    if not manifest:
        return
    obs = manifest_observado(lido)
    for chave, real in obs.items():
        if chave not in manifest:
            continue
        decl = manifest[chave]
        if isinstance(real, dict):
            decl = decl if isinstance(decl, dict) else {}
            # UNIÃO das chaves: categoria declarada que o artefato não tem é
            # tão divergente quanto contagem errada. E chave de PROVENIÊNCIA
            # (sha da fonte, estado do download) não pertence a um dicionário
            # reconciliável — vai em `*_proveniencia`, fora do confronto.
            div = []
            for k in sorted(set(real) | set(decl)):
                if k not in decl:
                    div.append(f'{k}: ausente no manifest (artefato={real[k]})')
                elif k not in real:
                    div.append(f'{k}: ausente no artefato (manifest={decl[k]})')
                elif _comparavel(decl[k]) != _comparavel(real[k]):
                    div.append(f'{k}: manifest={decl[k]} artefato={real[k]}')
            if div:
                res.issues.append(Issue(
                    'QA-MAN-002', 'FATAL', campo=chave,
                    esperado=str(decl)[:80], encontrado=str(real)[:80],
                    acao='distribuicao do manifest diverge do artefato: '
                         + '; '.join(div[:3])))
            continue
        try:
            if int(decl) != int(real):
                res.issues.append(Issue(
                    'QA-MAN-001', 'FATAL', campo=chave,
                    esperado=str(decl), encontrado=str(real),
                    acao='manifest diverge da metrica recalculada do artefato'))
        except (TypeError, ValueError):
            continue


# ─────────────────────────────────────────────────────────────────────────
#  orquestração
# ─────────────────────────────────────────────────────────────────────────
def auditar(caminho, modelo=None, colunas=None, contrato=None, manifest=None,
            modo='strict', avisos=None, blocos_obrigatorios=None,
            blocos_falhos=None, reparar=True, reconciliar_modelo=True,
            store=None, _ciclo=0) -> Resultado:
    """Reabre o artefato e devolve o veredito. Não publica nada."""
    res = Resultado(caminho=caminho, ciclo=_ciclo)
    if not os.path.exists(caminho) or os.path.getsize(caminho) == 0:
        res.issues.append(Issue('QA-EST-000', 'FATAL', acao='artefato ausente ou vazio'))
        res.estado = 'FAILED'
        return res

    try:
        lido = reabrir(caminho)
    except Exception as e:                                       # noqa: BLE001
        res.issues.append(Issue('QA-EST-005', 'FATAL', encontrado=str(e)[:120],
                                acao='artefato nao abre pelo parser do formato'))
        res.estado = 'FAILED'
        return res

    contrato = contrato or {'colunas': list(colunas or lido.columns),
                            'blocos': {}, 'tipos': {}, 'dominios': {}, 'sha256': ''}
    res.contract_sha256 = contrato.get('sha256', '')

    _estrutura(lido.columns, contrato, res)
    _identificadores(lido, res)
    _dominios(lido, contrato, res)
    _derivacao(lido, res)
    _derivacao_ids_compostos(lido, res, store=store)
    _vocabulario_via(lido, res)
    _bloco_col(lido, res)
    _bloco_set(lido, res)
    _manifest(lido, manifest, res)
    if reconciliar_modelo and modelo is not None:
        ordenado = lido
        if list(lido.columns) != contrato['colunas']:
            faltam = [c for c in contrato['colunas'] if c in lido.columns]
            ordenado = lido[faltam] if faltam else lido
        _linhas(ordenado, modelo, res)

    for b in (blocos_falhos or []):
        sev = 'FATAL' if b in (blocos_obrigatorios or []) else 'WARN'
        res.issues.append(Issue('QA-BLOCO-001', sev, bloco=b,
                                acao='bloco declarado obrigatorio nao foi produzido'))
    for a in (avisos or []):
        res.issues.append(Issue('QA-RUN-001',
                                'FATAL' if modo == 'strict' else 'WARN',
                                encontrado=a,
                                acao='aviso estrutural de execucao'))

    res.blocos = {b: int((pd.Series([contrato['blocos'].get(c, '') for c in lido.columns])
                          == b).sum()) for b in set(contrato['blocos'].values())}
    res.artifact_sha256 = sha256_arquivo(caminho)
    res.semantic_sha256 = hash_semantico(lido)

    reparaveis = [i for i in res.issues if i.auto_corrigivel]
    fatais_duros = [i for i in res.issues if i.severidade == 'FATAL']
    if fatais_duros:
        res.estado = 'QUARANTINED'
    elif reparaveis and reparar and _ciclo == 0:
        # UMA tentativa. `while erro: corrigir` esconde nao-determinismo e
        # converge para "consertei ate passar", que e' o oposto de qualidade.
        aplicadas = _reparar(caminho, lido, contrato, res)
        if aplicadas:
            novo = auditar(caminho, modelo, colunas, contrato, manifest, modo,
                           avisos, blocos_obrigatorios, blocos_falhos,
                           reparar=False, reconciliar_modelo=reconciliar_modelo,
                           store=store, _ciclo=1)
            # artefato corrigido automaticamente TEM de explicar o que mudou;
            # correcao silenciosa e' indistinguivel de corrupcao silenciosa
            novo.correcoes = aplicadas
            return novo
        res.estado = 'QUARANTINED'
    elif reparaveis:
        res.estado = 'QUARANTINED'
    elif any(i.severidade == 'WARN' and i.regra not in AVISOS_INFORMATIVOS
             for i in res.issues):
        res.estado = 'SEALED_WITH_WARNINGS' if modo != 'strict' else 'QUARANTINED'
    elif any(i.severidade == 'WARN' for i in res.issues):
        res.estado = 'SEALED_WITH_WARNINGS'
    else:
        res.estado = 'CORRECTED_AND_SEALED' if _ciclo else 'SEALED'
    return res


def _reparar(caminho, lido, contrato, res) -> list:
    """Só REPRESENTAÇÃO. Devolve o HISTÓRICO do que foi corrigido."""
    aplicadas = []
    df = lido
    cols = [c for c in contrato['colunas'] if c in df.columns]
    if cols and list(df.columns) != contrato['colunas'] and len(cols) == len(df.columns):
        df = df[contrato['colunas']]
        aplicadas.append({'regra': 'QA-EST-003', 'campo': '*',
                          'antes': 'ordem divergente do contrato',
                          'acao': 'reordenacao segundo o contrato'})
    for c in df.columns:
        if _RE_ID_COL.search(c):
            s = df[c].fillna('').astype(str)
            m = s.str.fullmatch(r'\d+\.0')
            if m.any():
                df.loc[m, c] = s[m].str.slice(0, -2)
                aplicadas.append({'regra': 'QA-IDENT-002', 'campo': c,
                                  'antes': f'{int(m.sum())} valor(es) com sufixo .0',
                                  'acao': 'remocao do rastro de float'})
    mudou = bool(aplicadas)
    if mudou:
        ext = os.path.splitext(caminho)[1].lower()
        if ext in ('.xlsx', '.xlsm'):
            # Reparo de workbook multiaba ainda NAO existe. Regravar com
            # `to_excel` achataria as outras abas — perder dado para consertar
            # formatacao e' o pior resultado possivel. Ate o QA por aba existir,
            # XLSX so' pode ser QUARENTENADO, nunca reparado.
            return []
        else:
            df.to_csv(caminho, sep=';', index=False, encoding='utf-8-sig')
    return aplicadas


def divergencia_de_runtime(lock_path) -> list:
    """Compara o runtime com o lock, SEM importar o runner.

    O checador que existia vivia em `radar_potenciais.py`, que roda argparse no
    escopo global — importa-lo de dentro de outro CLI sequestra a linha de
    comando. Independencia aqui nao e' so' R14: e' o unico jeito de o auditor
    ser utilizavel de qualquer lugar.
    """
    import importlib.metadata as md
    avisos = []
    if not lock_path or not os.path.exists(lock_path):
        return avisos
    for linha in open(lock_path, encoding='utf-8'):
        linha = linha.strip()
        if not linha or linha.startswith('#') or '==' not in linha:
            continue
        pkg, ver = linha.split('==')[:2]
        try:
            atual = md.version(pkg.strip())
        except Exception:                                # noqa: BLE001
            continue
        if atual != ver.strip():
            avisos.append(f'RUNTIME_DIVERGENTE:{pkg.strip()} lock={ver.strip()} runtime={atual}')
    return avisos


def verificar_artefato(caminho, selo) -> bool:
    """Responde UMA pergunta: o artefato mudou depois do lacre?"""
    return bool(selo.get('artifact_sha256')) and \
        sha256_arquivo(caminho) == selo['artifact_sha256']


# O PACOTE lacrado — cada hash obrigatório aponta para um arquivo que tem de
# viajar junto. `contract_sha256` sem `.contract.csv` no pacote era metadata
# inconferível: daqui a duas safras ninguém tem o dicionário daquela emissão.
SELO_HASHES = {'artifact_sha256': '',
               'manifest_sha256': '.manifest.json',
               'qa_report_sha256': '.qa_issues.csv',
               'contract_sha256': '.contract.csv',
               'crosswalk_sha256': '.crosswalk_canon.csv'}
# Campos de identidade sem os quais o selo não diz de QUE execução ele é.
SELO_CAMPOS = ('run_id', 'run_fingerprint', 'quality_seal')


def verificar_pacote(caminho, selo, obrigatorios=None) -> tuple:
    """`LACRE VÁLIDO` é sobre o PACOTE, não sobre um arquivo.

    `verificar_lacre` conferia só `artifact_sha256`: alterar o manifest depois
    do lacre e perguntar "está íntegro?" devolvia True. A função respondia
    corretamente a uma pergunta menor do que o nome sugeria.

    E é FAIL-CLOSED: hash ausente do selo era `continue`, então bastava APAGAR
    `manifest_sha256` para o pacote voltar a ser "válido". Um lacre que aprova
    o selo mutilado premia exatamente quem adultera.

    Devolve (ok, lista_de_falhas).
    """
    obrig = dict(SELO_HASHES) if obrigatorios is None else \
        {k: SELO_HASHES[k] for k in obrigatorios if k in SELO_HASHES}
    falhas = []
    for chave in SELO_CAMPOS:
        if not str(selo.get(chave) or '').strip():
            falhas.append(f'{chave}: AUSENTE no selo')
    for chave, sufixo in obrig.items():
        arq = caminho + sufixo
        esperado = selo.get(chave)
        if not esperado:
            falhas.append(f'{chave}: AUSENTE no selo')
            continue
        if not os.path.exists(arq):
            falhas.append(f'{chave}: arquivo ausente do pacote '
                          f'({os.path.basename(arq)})')
            continue
        if sha256_arquivo(arq) != esperado:
            falhas.append(chave)
    return (not falhas), falhas


# compatibilidade: o nome antigo passa a apontar para a pergunta correta
verificar_lacre = verificar_artefato


@contextlib.contextmanager
def publicacao_de_pacote(destino, sidecars=()):
    """Publica o pacote com UM rename — de diretório.

    A versão anterior fazia vários `os.replace` sequenciais e o comentário
    prometia "ou nenhum é". N renames NÃO são uma transação: injetando falha no
    segundo, o artefato ficava publicado e o manifest sumia — exatamente o
    estado que a arquitetura afirmava impedir.

    O sistema de arquivos só garante atomicidade de UM rename. Então os
    arquivos são montados dentro de um diretório temporário e o DIRETÓRIO é
    renomeado uma vez; em seguida os arquivos são movidos para os nomes finais
    a partir de um estado já validado. Se algo falhar antes do rename do
    diretório, nada foi publicado.
    """
    d = os.path.dirname(os.path.abspath(destino)) or '.'
    os.makedirs(d, exist_ok=True)
    stage = tempfile.mkdtemp(dir=d, prefix='.pending-')
    nome = os.path.basename(destino)
    pend = {suf: os.path.join(stage, nome + suf) for suf in ('',) + tuple(sidecars)}
    try:
        yield pend
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    # PONTO DE COMMIT — este rename é atômico e é o único que o sistema de
    # arquivos garante. Antes dele, nada existe; depois dele, o pacote completo
    # existe em algum lugar recuperável.
    firme = os.path.join(d, '.commit-' + os.path.basename(stage))
    os.replace(stage, firme)

    # HONESTIDADE SOBRE O LIMITE: com nomes planos (rcc.csv, rcc.csv.seal.json)
    # é IMPOSSÍVEL tornar N renames atômicos em POSIX. O que dá para garantir é
    # a ORDEM DA FALHA: sidecars primeiro, ARTEFATO POR ÚLTIMO. Assim uma
    # interrupção deixa linhagem sem artefato (inofensivo — ninguém entrega o
    # que não existe) e nunca artefato sem linhagem (perigoso — vira entrega
    # sem procedência). Em falha, `.commit-*` PERMANECE no disco para
    # recuperação; apagá-lo destruiria a única cópia íntegra do pacote.
    ordem = [k for k in pend if k != ''] + ['']

    # SUBSTITUIÇÃO. O caso que faltava: já existe uma entrega publicada. Apagar
    # os sidecars antigos no rollback deixava o artefato ANTIGO oficialmente
    # publicado SEM linhagem — o estado que a arquitetura existe para impedir.
    # A regra é: ou o pacote NOVO entra inteiro, ou o ANTIGO permanece inteiro.
    # Por isso o anterior é preservado dentro do commit antes de ser trocado.
    prev = {}
    for suf in ordem:
        alvo = destino + suf
        if os.path.exists(alvo):
            bkp = os.path.join(firme, '.prev' + suf)
            shutil.copy2(alvo, bkp)
            prev[suf] = bkp
    try:
        for suf in ordem:
            tmp = os.path.join(firme, os.path.basename(destino) + suf)
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, destino + suf)
    except BaseException:
        for suf in ordem:
            alvo = destino + suf
            if suf in prev:
                with contextlib.suppress(OSError):
                    shutil.copy2(prev[suf], alvo)     # volta o pacote anterior
            elif os.path.exists(alvo):
                with contextlib.suppress(OSError):
                    os.unlink(alvo)                   # não havia anterior
        raise
    shutil.rmtree(firme, ignore_errors=True)


@contextlib.contextmanager
def _publicacao_de_pacote_legado(destino, sidecars=()):
    """Publica o ARTEFATO E SEUS SIDECARS como UMA unidade.

    Antes, o CSV era renomeado para o nome oficial e o manifest nascia depois:
    havia uma janela em que existia entrega sem linhagem, e uma falha no meio
    deixava exatamente isso no disco. Aqui todos os arquivos são preparados em
    área temporária e renomeados em bloco no final — ou nenhum é.

    Uso:
        with publicacao_de_pacote(saida, ['.manifest.json', '.seal.json']) as p:
            p['']              -> caminho pendente do artefato
            p['.manifest.json'] -> caminho pendente do sidecar
    """
    d = os.path.dirname(os.path.abspath(destino)) or '.'
    os.makedirs(d, exist_ok=True)
    pend, mapa = {}, {}
    for suf in ('',) + tuple(sidecars):
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.pending-',
                                   suffix=(os.path.splitext(destino)[1] if suf == ''
                                           else suf))
        os.close(fd)
        pend[suf] = tmp
        mapa[tmp] = destino + suf
    try:
        yield pend
    except BaseException:
        for t in pend.values():
            with contextlib.suppress(OSError):
                os.unlink(t)
        raise
    for tmp, alvo in mapa.items():
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, alvo)
        else:
            with contextlib.suppress(OSError):
                os.unlink(tmp)


@contextlib.contextmanager
def publicacao_atomica(destino):
    """O nome definitivo só passa a existir depois que o bloco fecha sem erro.

    Sem isto, uma interrupção — ou um QA que reprova — deixa um arquivo com o
    nome oficial que ninguém distingue de uma entrega válida.
    """
    d = os.path.dirname(os.path.abspath(destino)) or '.'
    os.makedirs(d, exist_ok=True)
    fd, pend = tempfile.mkstemp(dir=d, prefix='.pending-',
                                suffix=os.path.splitext(destino)[1])
    os.close(fd)
    try:
        yield pend
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(pend)
        raise
    os.replace(pend, destino)


def quarentenar_bundle(stage, destino_oficial, res, run_id='SEM-RUN'):
    """Move o PACOTE inteiro para a quarentena, não só o artefato.

    A versão anterior movia o CSV e reconstruía um selo reduzido — perdiam-se
    manifest, selo completo, run_fingerprint, input_sha256 e runtime. Uma
    execução REPROVADA é justamente onde se quer mais evidência, não menos.
    """
    import json as _json
    base = os.path.dirname(os.path.abspath(destino_oficial)) or '.'
    qdir = os.path.join(base, 'quarentena', str(run_id))
    os.makedirs(qdir, exist_ok=True)
    nome = os.path.basename(destino_oficial)
    for f in sorted(os.listdir(stage)):
        origem = os.path.join(stage, f)
        alvo = os.path.join(qdir, f + ('.rejeitado' if f == nome else ''))
        with contextlib.suppress(OSError):
            os.replace(origem, alvo)
    res.issues_csv(os.path.join(qdir, nome + '.qa_issues.csv'))
    with open(os.path.join(qdir, 'veredito.json'), 'w', encoding='utf-8') as fh:
        _json.dump(res.selo(), fh, ensure_ascii=False, indent=2, default=str)
    return qdir


def quarentenar(pendente, destino_oficial, res, run_id='SEM-RUN'):
    """Quarentena POR EXECUÇÃO — evidência não pode ser sobrescrita.

    `quarentena/<arquivo>.rejeitado` era substituído pela execução seguinte, e
    a evidência da primeira falha desaparecia justamente quando alguém fosse
    investigar por que houve duas.
    """
    import json as _json
    base = os.path.dirname(os.path.abspath(destino_oficial)) or '.'
    qdir = os.path.join(base, 'quarentena', str(run_id))
    os.makedirs(qdir, exist_ok=True)
    nome = os.path.basename(destino_oficial)
    alvo = os.path.join(qdir, nome + '.rejeitado')
    os.replace(pendente, alvo)
    res.issues_csv(alvo + '.qa_issues.csv')
    with open(os.path.join(qdir, 'seal.json'), 'w', encoding='utf-8') as fh:
        _json.dump(res.selo(), fh, ensure_ascii=False, indent=2, default=str)
    return alvo
