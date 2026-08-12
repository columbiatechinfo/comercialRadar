#!/usr/bin/env python3
"""Testes do emissor RCC. Falha = exit 1.

Metade dos casos é NEGATIVA: prova que o gate ABORTA. Gate que nunca foi visto
falhar não é gate — é comentário. O caminho feliz sozinho não distingue "passou
porque está certo" de "passou porque não olhou".
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
RAIZ = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)

import numpy as np                                          # noqa: E402
import pandas as pd                                         # noqa: E402

import rcc_emissor as R                                     # noqa: E402
import radar_pipeline as rp                                 # noqa: E402

ok = 0
CSV = os.path.join(HERE, 'base_sintetica.csv')
if not os.path.exists(CSV):
    subprocess.run([sys.executable, os.path.join(HERE, 'gen_sintetico.py')], check=True)

DIC = os.path.join(RAIZ, 'RCC_dicionario.csv')
if not os.path.exists(DIC):
    DIC = os.path.join(HERE, 'RCC_dicionario.csv')


def carregar_dicionario(path):
    import csv as _csv
    with open(path, encoding='utf-8-sig') as fh:
        d = list(_csv.DictReader(fh, delimiter=';'))
    cols = [r['CAMPO'] for r in d]
    dom = {r['CAMPO']: [v.strip() for v in r['DOMINIO'].split('|')]
           for r in d if '|' in r['DOMINIO'] and '<' not in r['DOMINIO']}
    obr = [r['CAMPO'] for r in d if r['OBRIGATORIO'] == 'sim']
    return cols, dom, obr


COLS, DOM, OBR = carregar_dicionario(DIC)


def emitir_tudo(csv_path=CSV, store=None, municipio=None):
    df, _ = rp.preparar_base(csv_path, municipio=municipio)
    d = R.emitir(df, store_path=store)
    out = R.montar_saida(d, 'RUN-teste', 'IBGE/RCC-1.0', '2022', 'abc', COLS)
    out['AUD_EXECUTADO_EM'] = '2026-01-01T00:00:00+00:00'
    out = R.pos_agregados(out, d)
    inf = R.linhas_inferidas(df, out, COLS)
    if len(inf):
        out = pd.concat([out, inf], ignore_index=True)
    for col, nat in (('COL_QTD_INFERIDA', 'INFERIDO'), ('COL_QTD_OBSERVADA', 'OBSERVADO')):
        v = (out.assign(_x=(out['UND_NATUREZA'] == nat).astype(int))
             .groupby('COLETIVA_ID')['_x'].transform('sum'))
        out[col] = v.values
    return out.sort_values(['END_MUNICIPIO', 'COLETIVA_ID', 'BLOCO_ID',
                            'UNIDADE_ID'], kind='stable'), df, d


def espera_abortar(fn, trecho):
    try:
        fn()
    except R.RccError as e:
        assert trecho in str(e), f'gate abortou por outro motivo: {e}'
        return
    raise SystemExit(f'GATE NAO ABORTOU (esperado conter {trecho!r})')


# ── 1. smoke: emite o schema completo, na ordem do dicionário ──────────────
out, df, d = emitir_tudo()
assert list(out.columns) == COLS, 'ordem/conjunto de colunas divergem do dicionário'
assert len(out) > 0
R.gates(out, DOM, OBR)
ok += 1; print(f'[1] smoke: {len(out)} linhas x {len(COLS)} colunas, gates OK')

# ── 2. GRÃO: UNIDADE_ID único por origem ──────────────────────────────────
dup = out.groupby(['UNIDADE_ID', 'ORIGEM_SISTEMA']).size()
assert (dup == 1).all(), f'UNIDADE_ID repetido: {dup[dup>1].index[:3].tolist()}'
ok += 1; print(f'[2] grão: {out["UNIDADE_ID"].nunique()} UNIDADE_ID únicos = {len(out)} linhas')

# ── 3. composicionalidade: unidade deriva do grupo ────────────────────────
cid = out[out['COLETIVA_ID'].astype(str).str.strip().ne('')]
assert cid.apply(lambda r: r['UNIDADE_ID'].startswith(r['COLETIVA_ID']), axis=1).all()
ok += 1; print('[3] composicionalidade: UNIDADE_ID sempre prefixado pelo COLETIVA_ID')

# ── 4. hipótese nunca se fantasia de fato ─────────────────────────────────
inf = out[out['UND_NATUREZA'] == 'INFERIDO']
assert len(inf) > 0, 'a base sintética tem lacunas: deveria gerar inferidas'
assert (pd.to_numeric(inf['CNF_COORD']) == 0).all()
assert (inf['ACT_REQUER_CONFIRMACAO'] == 'SIM').all()
assert inf['GEO_LAT'].isna().all() and inf['GEO_LON'].isna().all()
assert (inf['GEO_QUALIDADE'] == 'AUSENTE').all()
assert (inf['ATV_FLAG_GAP_TARIFARIO'].astype(int) == 0).all()
ok += 1; print(f'[4] hipótese: {len(inf)} inferidas sem coordenada, confirmação exigida')

# ── 5. coerência de grupo ─────────────────────────────────────────────────
for c in ('COL_FORMA', 'COL_CLASSE', 'COL_VEREDITO', 'COL_USO',
          'COL_CRITERIO_UNIFICACAO', 'COL_QTD_OBSERVADA', 'COL_QTD_INFERIDA'):
    n = cid.groupby('COLETIVA_ID')[c].nunique(dropna=False)
    assert (n <= 1).all(), f'{c} varia dentro do grupo: {n[n>1].index[:2].tolist()}'
ok += 1; print('[5] coerência: 7 campos de grupo constantes dentro do grupo')

# ── 6. a agregação FECHA contra a contagem de unidades ────────────────────
g = cid.groupby('COLETIVA_ID')
obs = g.apply(lambda s: (s['UND_NATUREZA'] == 'OBSERVADO').sum(), include_groups=False)
dec = g['COL_QTD_OBSERVADA'].first().astype(int)
assert (obs == dec).all(), 'soma de unidades != total declarado no grupo'
ok += 1; print(f'[6] agregação fecha em {len(obs)} grupos (observadas nunca somam inferidas)')

# ── 7. domínio fechado ────────────────────────────────────────────────────
for campo, vals in DOM.items():
    s = out[campo].dropna().astype(str)
    s = s[s.str.strip().ne('')]
    fora = sorted(set(s) - set(vals))
    assert not fora, f'{campo} fora do domínio: {fora[:4]}'
ok += 1; print(f'[7] domínio: {len(DOM)} campos fechados, zero valor fora')

# ── 8. R8: identificador nunca em float ───────────────────────────────────
for c in out.columns:
    if c.endswith('_ID') or c.endswith('_HASH'):
        assert not pd.api.types.is_float_dtype(out[c]), f'{c} em float'
h = out['COLETIVA_CHAVE_HASH'].dropna().astype(str)
h = h[h.str.strip().ne('')]
assert h.map(lambda x: x.isdigit()).all(), 'hash com formatação numérica'
ok += 1; print('[8] R8: nenhum identificador em ponto flutuante')

# ── 9. sem cadastro, nada de tarifa ──────────────────────────────────────
assert (out['ATV_FLAG_GAP_TARIFARIO'].astype(int) == 0).all()
assert (out['CAD_STATUS'] == 'SEM_CADASTRO').all()
for c in ('CAD_SITUACAO', 'CAD_PERFIL_TARIFARIO', 'CAD_QTD_LIGACOES',
          'CAD_ECONOMIAS', 'CAD_DELTA_ECONOMIAS', 'ORIGEM_LIGACAO', 'IMOVEL_ID'):
    v = out[c].fillna('').astype(str).str.strip()
    assert v.eq('').all(), f'{c} preenchido sem cadastro/resolucao de entidade'
ok += 1; print('[9] honestidade: 7 colunas vazias por dependência externa declarada')

# ── 10. atividade: nunca afirmada sem campo de origem ────────────────────
atv = out[out['ATV_PRESENTE'] == 'SIM']
assert len(atv) > 0
assert atv['ATV_EVIDENCIA_CAMPO'].ne('NENHUM').all()
assert atv['ATV_GRAO_EVIDENCIA'].isin(['UNIDADE', 'ENDERECO']).all()
assert atv['ATV_SETOR'].fillna('').ne('').all()
assert (pd.to_numeric(atv['ATV_INCERTEZA_M']) > 0).all()
nao = out[out['ATV_PRESENTE'] == 'NAO']
assert nao['ATV_NOME'].fillna('').eq('').all(), 'sem atividade mas com nome'
ok += 1; print(f'[10] atividade: {len(atv)} unidades, todas com campo de origem e grão')

# ── 11. store: id estável entre execuções, append-only ───────────────────
with tempfile.TemporaryDirectory() as tmp:
    st = os.path.join(tmp, 'store.csv')
    o1, _, _ = emitir_tudo(store=st)
    n1 = sum(1 for _ in open(st, encoding='utf-8'))
    o2, _, _ = emitir_tudo(store=st)
    n2 = sum(1 for _ in open(st, encoding='utf-8'))
    m = (o1[['COLETIVA_CHAVE_HASH', 'COLETIVA_ID']].dropna().drop_duplicates()
         .merge(o2[['COLETIVA_CHAVE_HASH', 'COLETIVA_ID']].dropna().drop_duplicates(),
                on='COLETIVA_CHAVE_HASH'))
    assert (m['COLETIVA_ID_x'] == m['COLETIVA_ID_y']).all(), 'COLETIVA_ID mudou entre runs'
    assert n1 == n2, f'store cresceu sem dado novo ({n1} -> {n2})'
ok += 1; print(f'[11] store: {len(m)} ids estáveis entre execuções, sem crescimento espúrio')

# ── 12. determinismo ─────────────────────────────────────────────────────
a, _, _ = emitir_tudo()
b, _, _ = emitir_tudo()
pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
ok += 1; print('[12] determinismo: duas execuções produzem o mesmo dataframe')

# ── 13. NEGATIVO: DE-PARA incompleto aborta ──────────────────────────────
def _depara_furado():
    d2 = d.copy()
    d2.loc[d2.index[0], 'TIPO_COLETIVA'] = 'TIPO_QUE_NAO_EXISTE'
    R.emitir(d2)
espera_abortar(_depara_furado, 'DE-PARA incompleto')
ok += 1; print('[13] NEG: tipologia sem destino no DE-PARA aborta o export')

# ── 14. NEGATIVO: valor fora do domínio aborta ───────────────────────────
def _dominio_furado():
    o = out.copy(); o.loc[o.index[0], 'COL_FORMA'] = 'PIRAMIDAL'
    R.gates(o, DOM, OBR)
espera_abortar(_dominio_furado, 'G10')
ok += 1; print('[14] NEG: valor fora do domínio fechado aborta')

# ── 15. NEGATIVO: grão quebrado aborta ───────────────────────────────────
def _grao_furado():
    o = out.copy()
    o.loc[o.index[1], 'UNIDADE_ID'] = o.loc[o.index[0], 'UNIDADE_ID']
    o.loc[o.index[1], 'COLETIVA_ID'] = o.loc[o.index[0], 'COLETIVA_ID']
    R.gates(o, DOM, OBR)
espera_abortar(_grao_furado, 'G12')
ok += 1; print('[15] NEG: UNIDADE_ID repetido aborta (o grão deixaria de ser unidade)')

# ── 16. NEGATIVO: campo de grupo incoerente aborta ───────────────────────
def _grupo_furado():
    o = out.copy()
    alvo = o[o['COLETIVA_ID'].astype(str).str.strip().ne('')]
    ids = alvo['COLETIVA_ID'].value_counts()
    multi = ids[ids > 1].index[0]
    i = o.index[o['COLETIVA_ID'] == multi][0]
    o.loc[i, 'COL_FORMA'] = 'LOTEAMENTO'
    R.gates(o, DOM, OBR)
espera_abortar(_grupo_furado, 'G13')
ok += 1; print('[16] NEG: COL_FORMA divergente dentro do grupo aborta')

# ── 17. NEGATIVO: hipótese com coordenada fabricada aborta ───────────────
def _coord_fabricada():
    o = out.copy()
    i = o.index[o['UND_NATUREZA'] == 'INFERIDO'][0]
    o.loc[i, 'GEO_LAT'] = -29.9
    R.gates(o, DOM, OBR)
espera_abortar(_coord_fabricada, 'G5')
ok += 1; print('[17] NEG: coordenada fabricada em unidade inferida aborta')

# ── 18. NEGATIVO: gap tarifário sem cadastro aborta ──────────────────────
def _gap_sem_tarifa():
    o = out.copy(); o.loc[o.index[0], 'ATV_FLAG_GAP_TARIFARIO'] = 1
    R.gates(o, DOM, OBR)
espera_abortar(_gap_sem_tarifa, 'G9')
ok += 1; print('[18] NEG: gap tarifário afirmado sem tarifa conhecida aborta')

# ── 19. NEGATIVO: atividade sem campo de origem aborta ───────────────────
def _atv_sem_fonte():
    o = out.copy()
    i = o.index[o['ATV_PRESENTE'] == 'SIM'][0]
    o.loc[i, 'ATV_EVIDENCIA_CAMPO'] = 'NENHUM'
    R.gates(o, DOM, OBR)
espera_abortar(_atv_sem_fonte, 'G7')
ok += 1; print('[19] NEG: atividade afirmada sem campo de origem aborta')

# ── 20. NEGATIVO: coluna obrigatória vazia sem isenção aborta ────────────
def _coluna_vazia():
    o = out.copy(); o['COL_FORMA'] = ''
    R.gates(o, DOM, OBR)
espera_abortar(_coluna_vazia, 'G14')
ok += 1; print('[20] NEG: coluna obrigatória inteiramente vazia aborta')

# ── 21. multi-município: prefixo evita colisão de id ─────────────────────
muns = out['END_MUNICIPIO'].nunique()
assert muns >= 2, 'a fixture tem 2 municípios'
pref = (out.loc[out['COLETIVA_ID'].astype(str).str.strip().ne('')]
        .assign(p=lambda x: x['COLETIVA_ID'].str.split('-').str[1]))
assert (pref['p'] == pref['END_MUNICIPIO'].str.zfill(7)).all(), \
    'COLETIVA_ID com prefixo de município errado'
assert pref.groupby('COLETIVA_ID')['END_MUNICIPIO'].nunique().eq(1).all()
ok += 1; print(f'[21] multi-município: {muns} municípios, zero colisão de COLETIVA_ID')

# ── 22. par original/em uso da coordenada ────────────────────────────────
obs = out[out['UND_NATUREZA'] == 'OBSERVADO']
comgeo = obs[obs['GEO_LAT'].notna()]
assert (comgeo['GEO_LAT'] == comgeo['GEO_LAT_ORIG']).all(), \
    'esta skill nao ajusta coordenada: em uso deve repetir a original'
assert (comgeo['GEO_AJUSTE_FONTE'] == 'SEM_AJUSTE').all()
assert (pd.to_numeric(comgeo['GEO_DESLOCAMENTO_M']) == 0).all()
assert (pd.to_numeric(comgeo['GEO_ACURACIA_M']) > 0).all()
ok += 1; print(f'[22] coordenada: par coerente em {len(comgeo)} linhas, deslocamento zero')

# ── 23. CLI ponta a ponta + manifest ─────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    saida = os.path.join(tmp, 'rcc.csv')
    # A fonte SET vem de um cache POPULADO LOCALMENTE. Antes, este teste
    # dependia de um `cache_ibge/` relativo ao diretório de invocação: passava
    # na máquina de quem já tinha baixado e ia à rede em todas as outras.
    import cnefe_fixture as _fx
    _cache = os.path.join(tmp, 'cache_ibge')
    _fx.cache_set_offline(_cache)
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
                        '--input', CSV, '--output', saida, '--dicionario', DIC,
                        '--store', os.path.join(tmp, 's.csv'), '--safra', '2022',
                        '--cache-ibge', _cache],
                       capture_output=True, text=True)
    assert r.returncode == 0, f'CLI falhou:\n{r.stdout}\n{r.stderr}'
    assert os.path.exists(saida) and os.path.exists(saida + '.manifest.json')
    import json
    man = json.load(open(saida + '.manifest.json', encoding='utf-8'))
    for k in ('rcc_versao', 'fonte', 'sha256_16', 'exec_id', 'linhas',
              'observadas', 'inferidas', 'funil_preparar_base'):
        assert k in man, f'manifest sem {k}'
    assert man['fonte'] == 'IBGE'
    assert man['linhas'] == man['observadas'] + man['inferidas'], 'funil nao fecha'
    lido = pd.read_csv(saida, sep=';', dtype=str)
    assert list(lido.columns) == COLS
ok += 1; print(f'[23] CLI: exit 0, manifest completo, funil fecha '
               f'({man["observadas"]}+{man["inferidas"]}={man["linhas"]})')

# ── 24. gates imunes a `python -O` ───────────────────────────────────────
prog = (f'import sys; sys.path.insert(0,{SCRIPTS!r}); sys.path.insert(0,{HERE!r});\n'
        'import pandas as pd, rcc_emissor as R\n'
        'from test_rcc import out, DOM\n')
r = subprocess.run([sys.executable, '-O', '-c',
                    f'import sys; sys.path.insert(0,{SCRIPTS!r})\n'
                    'import pandas as pd, rcc_emissor as R\n'
                    'o = pd.DataFrame({"UNIDADE_ID":[""],"COLETIVA_ID":[""],\n'
                    '  "UND_NATUREZA":["OBSERVADO"],"ATV_PRESENTE":["NAO"],\n'
                    '  "ATV_FLAG_GAP_TARIFARIO":[0],"END_NUMERO":[1]})\n'
                    'try:\n'
                    '    R.gates(o, {}, None)\n'
                    '    print("FALHOU: gate nao abortou sob -O")\n'
                    'except R.RccError as e:\n'
                    '    print("OK", str(e)[:20])\n'],
                   capture_output=True, text=True)
assert 'OK' in r.stdout, f'gate nao sobreviveu a -O: {r.stdout} {r.stderr}'
ok += 1; print('[24] gates sob python -O: continuam abortando (RccError, nao assert)')

# ══════════════════════════════════════════════════════════════════════════
#  R9 — GRADUAÇÃO DA INFERÊNCIA (v4.7)
#  Cada teste negativo prova que o gate ABORTA, não que o caso feliz passa.
# ══════════════════════════════════════════════════════════════════════════
import radar_utils as XU

# ── 25. graduação por quantos lados a observação cerca ────────────────────
h = {x['VALOR']: x for x in XU.gap_grade(list(range(101, 110)) + list(range(201, 209)))}
assert set(h) == {209}, f'esperava so o 209, veio {sorted(h)}'
assert h[209]['EVD_CLASSE'] == 'C_EXTRAPOLADA' and h[209]['EVD_DISTANCIA'] == 1
b = {x['VALOR']: x for x in XU.gap_grade([101, 102, 103, 301, 302, 303])}
assert set(b) == {201, 202, 203}, f'andar 2 nao inferido: {sorted(b)}'
assert all(v['EVD_GRAU'] == 'SUSTENTADA' for v in b.values())
i = {x['VALOR'] for x in XU.gap_grade([101, 102, 103, 301, 309])
     if x['EVD_CLASSE'] == 'B_ANDAR_AUSENTE'}
assert i == {201}, f'intersecao nao respeitada: {sorted(i)}'
assert XU.gap_grade([101, 102, 103, 1001, 1002, 1003]) == []
ok += 1; print('[25] grade: 209 extrapolado, andar ausente inferido, '
               'intersecao e teto vertical respeitados')

# ── 26. posicional: contraparte suprime, saturação sustenta ───────────────
so = XU.gap_posicional([{'FRENTE'}], [False])
assert so[0]['EVD_GRAU'] == 'SUSTENTADA' and so[0]['VALOR'] == 'FUNDOS'
en = XU.gap_posicional([{'FRENTE'}] + [set()] * 5, [True] * 6)
assert en[0]['EVD_GRAU'] == 'PLAUSIVEL', 'FRENTE em enumeracao nao pode ser forte'
im = XU.gap_posicional([{'FUNDOS'}, set()], [False, False])
assert im[0]['EVD_GRAU'] == 'ESPECULATIVA', 'frente implicita completa o par'
assert XU.gap_posicional([{'FRENTE'}, {'FUNDOS'}], [False, False]) == []
ok += 1; print('[26] posicional: saturado forte, enumerado medio, '
               'implicito fraco, par completo mudo')

# ── 27. NEG: hipótese sem grau aborta (G16) ───────────────────────────────
o = out.copy()
alvo = o.index[o['UND_NATUREZA'] == 'INFERIDO'][0]
o.loc[alvo, 'EVD_GRAU'] = ''
try:
    R.gates(o, DOM, OBR); raise SystemExit('FALHOU: G16 nao abortou')
except R.RccError as e:
    assert 'G16' in str(e), str(e)
ok += 1; print('[27] NEG: hipotese sem EVD_GRAU aborta — nada entra sem classificacao')

# ── 28. NEG: destino que não deriva do grau aborta (G17) ──────────────────
o = out.copy()
o.loc[alvo, 'ACT_DESTINO_CAMPO'] = (
    'ENVIAR' if o.loc[alvo, 'EVD_GRAU'] != 'SUSTENTADA' else 'RETER')
try:
    R.gates(o, DOM, OBR); raise SystemExit('FALHOU: G17 nao abortou')
except R.RccError as e:
    assert 'G17' in str(e), str(e)
ok += 1; print('[28] NEG: ESPECULATIVA marcada para campo aborta — '
               'o destino nao pode ser editado a mao')

# ── 29. NEG: distância fora da extrapolação aborta (G18) ──────────────────
o = out.copy()
obs = o.index[o['UND_NATUREZA'] == 'OBSERVADO'][0]
o['EVD_DISTANCIA'] = pd.to_numeric(o['EVD_DISTANCIA'], errors='coerce').astype('Int64')
o.loc[obs, 'EVD_DISTANCIA'] = 3
try:
    R.gates(o, DOM, OBR); raise SystemExit('FALHOU: G18 nao abortou')
except R.RccError as e:
    assert 'G18' in str(e), str(e)
ok += 1; print('[29] NEG: EVD_DISTANCIA fora de C_EXTRAPOLADA aborta — '
               'o limiar so pode mover o que e extrapolacao')

# ── 30. NEG: taxa do setor virando contagem por unidade aborta (G19) ──────
o = out.copy()
o['UND_QTD_VAGOS'] = 1
try:
    R.gates(o, DOM, OBR); raise SystemExit('FALHOU: G19 nao abortou')
except Exception as e:
    assert 'G19' in str(e), f'erro inesperado: {e}'
ok += 1; print('[30] NEG: contagem de vagos no grao da unidade aborta — '
               'taxa de setor nunca vira numero de imovel')

# ── 31. R8: id de 19 dígitos sobrevive ao caminho da hipótese ─────────────
sent = pd.Series([4700095277622949996, 8403508116556714592, pd.NA], dtype='Int64')
assert 'e+' in str(sent.map(lambda v: '' if pd.isna(v) else str(v)).iloc[0]), \
    'o controle negativo parou de reproduzir o defeito — revisar o teste'
texto = pd.Series(XU.id_seguro(sent, saida='str')).fillna('')
assert texto.iloc[0] == '4700095277622949996' and texto.iloc[2] == ''
inf_rcc = out[out['UND_NATUREZA'] == 'INFERIDO']
assert len(inf_rcc) > 0 and inf_rcc['COLETIVA_ID'].astype(str).str.strip().ne('').all(), \
    'hipotese orfa: o merge com o endereco-pai perdeu digito'
ok += 1; print('[31] R8: id_seguro preserva 19 digitos onde .map(str) perde; '
               'nenhuma hipotese orfa')

# ── 32. bloco SET: fechamento e origem 100% IBGE ──────────────────────────
import ibge_setor_ocupacao as XSET
demo = pd.DataFrame({'CD_SETOR': ['431490205000001', '431490205000002'],
                     'CD_MUN': ['4314902'] * 2, 'NM_MUN': ['PORTO ALEGRE'] * 2,
                     'NM_BAIRRO': ['A', 'B'],
                     'v0003': [100, 10], 'v0007': [70, 8],
                     'v0008': [10, 1], 'v0009': [20, 1]})
bl = XSET.montar_bloco_set(demo)
XSET.gate_set_nao_multiplicado(bl)
assert bl.loc[0, 'SET_TX_SEM_OCUPACAO_HABITUAL'] == 0.30
assert bl.loc[0, 'SET_CLASSE_SEM_OCUPACAO_HABITUAL'] == 'ALTA'
assert bl.loc[1, 'SET_CLASSE_SEM_OCUPACAO_HABITUAL'] == 'INSUFICIENTE', \
    'setor com 10 domicilios nao classifica: a taxa e ruido amostral'
assert bl['SET_ORIGEM_DADO'].str.startswith('IBGE:').all()
ruim = bl.copy(); ruim.loc[0, 'SET_DOM_VAGOS'] = 999
try:
    XSET.gate_set_nao_multiplicado(ruim); raise SystemExit('FALHOU: fechamento')
except XSET.SetorError as e:
    assert 'G19' in str(e)
assert XSET.chave_setor(pd.Series(['431490205002017P'])).iloc[0] == '431490205002017'
ok += 1; print('[32] SET: fechamento do setor, piso amostral, origem IBGE '
               'e truncamento do sufixo do CNEFE')

print(f'\nTOTAL: {ok} testes')
