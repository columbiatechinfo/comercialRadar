#!/usr/bin/env python3
"""Testes unitários dirigidos da v4.3 — falha = exit 1."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np
import pandas as pd
import radar_utils as xu

ok = 0

# ── 1. Sanidade POR PARTIÇÃO: cidade pequena não é recortada pelo bbox global
a = pd.DataFrame({'COD_MUNICIPIO': 4300604, 'LATITUDE': np.linspace(-29.95, -29.90, 790),
                  'LONGITUDE': np.linspace(-51.21, -51.15, 790)})
b = pd.DataFrame({'COD_MUNICIPIO': 3550308, 'LATITUDE': np.linspace(-23.556, -23.544, 10),
                  'LONGITUDE': np.linspace(-46.636, -46.624, 10)})
df = pd.concat([a, b], ignore_index=True)
# v4.5: percentil NÃO exclui mais (um quantil define extremos, não erros) —
# vira FLAG_OUTLIER_GEO informativa; ERRO_GEOMETRIA é determinístico+grosseiro.
p = xu.sanidade_geo(df.copy(), pct=0.01, particao='COD_MUNICIPIO')
assert (p['SANIDADE_GEO'] == 'OK').all(), 'coordenadas legítimas nunca viram ERRO'
assert p['FLAG_OUTLIER_GEO'].sum() > 0, 'caudas percentil devem virar FLAG informativa'
assert int(p.loc[p['COD_MUNICIPIO'] == 3550308, 'SANIDADE_GEO'].ne('OK').sum()) == 0
# determinístico: (0,0), fora do Brasil e lat/lon trocadas → ERRO
b2 = pd.concat([b, pd.DataFrame({
    'COD_MUNICIPIO': [3550308] * 3,
    'LATITUDE': [0.0, -23.55, -46.63], 'LONGITUDE': [0.0, 46.63, -23.55]})],
    ignore_index=True)
p2 = xu.sanidade_geo(pd.concat([a, b2], ignore_index=True), pct=0.01, particao='COD_MUNICIPIO')
errs = p2.tail(3)['SANIDADE_GEO'].tolist()
assert errs == ['ERRO_GEOMETRIA'] * 3, f'(0,0)/fora-BR/swap devem ser ERRO: {errs}'
ok += 1; print('[1] sanidade 3 camadas: legítimo nunca é ERRO; percentil vira flag; '
               '(0,0)/fora-BR/swap caem  OK')

# ── 2. R7: coordenada confirmada supre CEP inválido/curto
base = {'NV_GEO_COORD': 1, 'SANIDADE_GEO': 'OK', 'NOM_SEGLOGR_HARM': 'RUA X',
        'NUM_ENDERECO': 10, 'COMPLEMENTO_NORM': '', 'FLAG_ANOMALIA_COLETA': 0}
c_ok = xu.calcular_confiabilidade({**base, 'CEP': '01001000'})
c_bad = xu.calcular_confiabilidade({**base, 'CEP': '1001000'})     # zero perdido
assert c_ok['RADAR_CONF_ENDERECO'] == c_bad['RADAR_CONF_ENDERECO'], 'CEP inválido penalizou registro confirmado'
assert 'COORD_SUPRE_CEP' in c_bad['RADAR_CONF_ENDERECO_FATORES']
c_nv4 = xu.calcular_confiabilidade({**base, 'NV_GEO_COORD': 4, 'CEP': '1001000'})
assert c_nv4['RADAR_CONF_ENDERECO'] == c_bad['RADAR_CONF_ENDERECO'] - 7, 'NV4 não pode ganhar COORD_SUPRE_CEP'
ok += 1; print('[2] R7 COORD_SUPRE_CEP: supre com NV1+sanidade, nega com NV4  OK')

# ── 3. Gate negativo: cada violação aborta
def _frames():
    col = pd.DataFrame({'CLASSE': ['GRANDE'], 'NUMERO': [10], 'TIPO': ['Edifício de apartamentos'],
                        'CONF': ['ALTA']})
    vazio = pd.DataFrame({'NUMERO': pd.Series(dtype=float)})
    return col, vazio.copy(), vazio.copy(), pd.DataFrame()

col, chz, nres, po = _frames()
col.loc[0, 'CONF'] = 'BAIXA'
try:
    xu.gate_pos_condicoes(col, chz, nres, po); raise SystemExit('gate deixou GRANDE BAIXA passar')
except xu.RadarQualityError as e:
    assert 'R3' in str(e)
col, chz, nres, po = _frames()
col['FONTE_IBGE'] = 1
try:
    xu.gate_pos_condicoes(col, chz, nres, po); raise SystemExit('gate deixou coluna IBGE passar')
except xu.RadarQualityError as e:
    assert 'R5' in str(e)
col, chz, nres, po = _frames()
pf = pd.DataFrame({'RADAR_CONF_COORD': [5]})
try:
    xu.gate_pos_condicoes(col, chz, nres, po, par_faltante=pf); raise SystemExit('gate deixou EIXO1 fabricado passar')
except xu.RadarQualityError as e:
    assert 'EIXO1' in str(e)
col, chz, nres, po = _frames()
col.loc[0, 'NUMERO'] = 0
try:
    xu.gate_pos_condicoes(col, chz, nres, po); raise SystemExit('gate deixou NUM=0 OBSERVADO passar')
except xu.RadarQualityError as e:
    assert 'R1' in str(e)
col, chz, nres, po = _frames()
xu.gate_pos_condicoes(col, chz, nres, po)   # frame válido passa
ok += 1; print('[3] gate_pos_condicoes: aborta R3/R5/EIXO1/R1 e aprova frame válido  OK')

# ── 4. validar_schema aborta com coluna faltando
d = pd.DataFrame({c: [1] for c in xu.COLUNAS_OBRIGATORIAS})
xu.validar_schema(d)
try:
    xu.validar_schema(d.drop(columns=['NOM_COMP_ELEM3'])); raise SystemExit('schema deixou coluna faltante passar')
except xu.RadarQualityError as e:
    assert 'NOM_COMP_ELEM3' in str(e)
ok += 1; print('[4] validar_schema: aprova completo, aborta faltante  OK')

# ── 5. COND_HORIZ_LOTES por nunique: lote duplicado não fabrica condomínio
def _lotes(chaves_ql):
    n = len(chaves_ql)
    return pd.DataFrame({
        'NUM_ENDERECO': [10] * n, 'NV_GEO_COORD': [1] * n,
        'MORADIA_TIPO': [None] * n, 'MORADIA_VALOR': [None] * n,
        'POSICAO_TIPO': [None] * n,
        'CHAVE': ['RUA Q|10|C'] * n, 'CHAVE_QUADRA_LOTE': chaves_ql,
        'PADRAO_ENDERECO': ['QUADRA_LOTE'] * n,
        'NOM_SEGLOGR_HARM': ['RUA Q'] * n,
    })
dup = xu.detectar_condominio_horizontal(_lotes(['Q1/L5', 'Q1/L5', 'Q1/L5']))
assert int(dup['FLAG_COND_HORIZ'].sum()) == 0, 'lote repetido 3x não pode virar condomínio'
tri = xu.detectar_condominio_horizontal(_lotes(['Q1/L5', 'Q1/L6', 'Q1/L7']))
assert int(tri['FLAG_COND_HORIZ'].sum()) == 3 and (tri['TIPO_COND_HORIZ'] == 'COND_HORIZ_LOTES').all()
ok += 1; print('[5] COND_HORIZ_LOTES nunique: 3×mesmo lote → 0; 3 lotes → flag  OK')

# ── 6. S2 calibração Q/L embutida: 20 unidades perfeitas só flagram fora de Q/L
def _seq(padrao):
    n = 20
    return pd.DataFrame({
        'LATITUDE': [-29.9] * n, 'LONGITUDE': [-51.1] * n,
        'NOM_SEGLOGR': ['RUA S'] * n, 'NUM_ENDERECO': [99] * n,
        'CHAVE': ['RUA S|99|C'] * n, 'UNIDADE_VALOR': [str(i) for i in range(1, n + 1)],
        'PADRAO_ENDERECO': [padrao] * n,
        'DSC_ESTABELECIMENTO': [''] * n, 'COD_UNICO_ENDERECO': [str(i) for i in range(n)],
    })
f_norm = xu.detectar_anomalia_coleta(_seq('LOGRADOURO'))
f_ql = xu.detectar_anomalia_coleta(_seq('QUADRA_LOTE'))
assert int(f_norm['FLAG_ANOMALIA_COLETA'].sum()) == 20, 'S2 deveria flagrar sequência perfeita comum'
assert int(f_ql['FLAG_ANOMALIA_COLETA'].sum()) == 0, 'S2 Q/L exige seq_min=30 — 20 não flagra'
ok += 1; print('[6] S2 Q/L automático: 20 aptos flagra; 20 lotes Q/L não  OK')

# ── 7. Harmonização por nome normalizado: acento funde, ruas distintas nunca
h = xu.harmonizar_logradouro(pd.DataFrame({
    'CEP': ['99999000'] * 6,
    'NOM_TIPO_SEGLOGR': ['RUA'] * 6,
    'NOM_TITULO_SEGLOGR': [''] * 6,
    'NOM_SEGLOGR': ['FLORENCA', 'FLORENCA', 'FLORENÇA', 'GARIBALDI', 'GARIBALDI', 'SOLITARIA'],
}))
assert set(h.loc[h['NOM_SEGLOGR'].str.startswith('FLOREN'), 'NOM_SEGLOGR_HARM']) == {'FLORENCA'}
assert set(h['NOM_SEGLOGR_HARM']) == {'FLORENCA', 'GARIBALDI', 'SOLITARIA'}, 'CEP único não pode fundir ruas'
ok += 1; print('[7] harmonização: FLORENÇA→FLORENCA; GARIBALDI/SOLITARIA intactas em CEP único  OK')

# ── 8. RADAR_ID (N13): determinismo, caracteres padrão, município, Q/L, bloco, colisão
def _base_id(nome, mun=4300604, num=100, loc='CENTRO', ql=None, bloco=None):
    return pd.DataFrame({'COD_MUNICIPIO': [mun], 'NOM_TIPO_SEGLOGR': ['RUA'],
                         'NOM_SEGLOGR': [nome], 'NUM_ENDERECO': [num],
                         'DSC_LOCALIDADE': [loc], 'CHAVE_QUADRA_LOTE': [ql],
                         'BLOCO_VALOR': [bloco]})
i1 = xu.gerar_id_rastreio(_base_id('FLORENÇA'))['RADAR_ID_ENDERECO'].iloc[0]
i2 = xu.gerar_id_rastreio(_base_id('florenca.'))['RADAR_ID_ENDERECO'].iloc[0]
i3 = xu.gerar_id_rastreio(_base_id('FLORENÇA'))['RADAR_ID_ENDERECO'].iloc[0]
assert i1 == i2 == i3, 'acento/caixa/pontuação não podem mudar o id; reexecução idem'
i_sp = xu.gerar_id_rastreio(_base_id('FLORENÇA', mun=3550308))['RADAR_ID_ENDERECO'].iloc[0]
assert i_sp != i1, 'mesmo endereço em municípios distintos exige ids distintos'
r0 = xu.gerar_id_rastreio(_base_id('FLORENÇA', num=0))
assert pd.isna(r0['RADAR_ID_ENDERECO'].iloc[0]), 'NUM=0 sem Q/L → id nulo (R1)'
q1 = xu.gerar_id_rastreio(_base_id('CAMOBI', num=0, ql='Q15/L8'))['RADAR_ID_ENDERECO'].iloc[0]
q2 = xu.gerar_id_rastreio(_base_id('CAMOBI', num=0, ql='Q15/L9'))['RADAR_ID_ENDERECO'].iloc[0]
assert pd.notna(q1) and q1 != q2, 'Padrão B: id por chave Q/L, distinto por lote'
ba = xu.gerar_id_rastreio(_base_id('ERNESTO', bloco='A'))
bb = xu.gerar_id_rastreio(_base_id('ERNESTO', bloco='B'))
assert ba['RADAR_ID_ENDERECO'].iloc[0] == bb['RADAR_ID_ENDERECO'].iloc[0]
assert ba['RADAR_ID_BLOCO'].iloc[0] != bb['RADAR_ID_BLOCO'].iloc[0], 'bloco muda ID_BLOCO, não ID_ENDERECO'
assert 0 < int(i1) < 2**63, 'id fora do intervalo int64 positivo'
# colisão forjada → gate aborta
fake = pd.DataFrame({'FLAG_COL': [0, 0], 'FLAG_ANOMALIA_COLETA': [0, 0],
                     'SANIDADE_GEO': ['OK', 'OK'], 'NUMERO': [1, 2],
                     'RADAR_ID_ENDERECO': pd.array([777, 777], dtype='Int64'),
                     'RADAR_CHAVE_CANONICA': ['A|X', 'B|Y']})
colv, chzv, nresv, pov = _frames()
try:
    xu.gate_pos_condicoes(colv, chzv, nresv, pov, df_base=fake)
    raise SystemExit('gate deixou colisão de id passar')
except xu.RadarQualityError as e:
    assert 'N13' in str(e)
ok += 1; print('[8] RADAR_ID: determinístico, caracteres padrão, município/QL/bloco, colisão aborta  OK')

# ── 9. N16: confronto declarado×contado (estrutura decide, declaração valida)
def _decl(n_estab, ind):
    n = max(n_estab, 1)
    return pd.DataFrame({
        'CHAVE': ['4300604|RUA D|10|C'] * n,
        'COD_ESPECIE': [6] * n_estab + [1] * (n - n_estab),
        'COD_INDICADOR_ESTAB_ENDERECO': [ind] * n,
    })
casos = [
    (3, 2, 'CONFIRMADO'),               # declarado 2..10, contados 3
    (1, 3, 'DUVIDA_SUB_ENUMERACAO'),    # declarado >=11, contado 1
    (4, 1, 'DUVIDA_SUPER_CONTAGEM'),    # declarado exato 1, contados 4
    (2, None, 'SEM_DECLARACAO'),        # sem indicador
]
for n_e, ind, esperado in casos:
    r = xu.confrontar_declaracao_estab(_decl(n_e, ind))
    got = r['VALIDACAO_ESTAB_DECL'].iloc[0]
    assert got == esperado, f'contados={n_e} decl={ind}: esperado {esperado}, veio {got}'
    assert int(r['QTD_ESTAB_CONTADA'].iloc[0]) == n_e
# fonte sem a coluna do indicador → SEM_DECLARACAO, sem quebrar
r = xu.confrontar_declaracao_estab(pd.DataFrame({'CHAVE': ['k'], 'COD_ESPECIE': [6]}))
assert r['VALIDACAO_ESTAB_DECL'].iloc[0] == 'SEM_DECLARACAO'
ok += 1; print('[9] N16 declarado×contado: CONFIRMADO/SUB/SUPER/SEM corretos; fonte sem coluna OK')



# ── 10. DBSCAN v4.5: min_samples = ENDEREÇOS (peso não fabrica polo)
def _polo_df(n_end, estab_por_end):
    rows = []
    for i in range(n_end):
        for j in range(estab_por_end):
            rows.append({'CHAVE': f'4300604|AV C|{100+i}|C', 'COD_UNICO_ENDERECO': f'{i}-{j}',
                         'SETOR_ATIVIDADE': 'COMERCIO_VAREJO', 'SANIDADE_GEO': 'OK',
                         'NUMERO': 100 + i, 'FLAG_ANOMALIA_COLETA': 0,
                         'LATITUDE': -29.9 + i * 2e-4, 'LONGITUDE': -51.1})
    return pd.DataFrame(rows)
_, p1 = xu.detectar_polos_comerciais(_polo_df(1, 5), eps_m=100, min_samples=5, exigir_conf=False)
assert len(p1) == 0, '1 endereço com 5 estabelecimentos NÃO pode virar polo'
_, p5 = xu.detectar_polos_comerciais(_polo_df(5, 1), eps_m=100, min_samples=5, exigir_conf=False)
assert len(p5) == 1 and int(p5['QTD_END'].iloc[0]) == 5, '5 endereços próximos formam 1 polo'
ok += 1; print('[10] DBSCAN: 1 endereço/5 estab → 0 polos; 5 endereços → 1 polo  OK')

# ── 11. S1 v4.5: coordenadas NULAS não se agrupam em "nan|nan"
nul = pd.DataFrame({'LATITUDE': [None] * 6, 'LONGITUDE': [None] * 6,
                    'NOM_SEGLOGR': [f'RUA {i}' for i in range(6)],
                    'NUM_ENDERECO': [10] * 6})
r_nul = xu.detectar_anomalia_coleta(nul)
assert int(r_nul['FLAG_ANOMALIA_COLETA'].sum()) == 0, 'sem coordenada não pode virar S1'
ok += 1; print('[11] S1: 6 registros sem coordenada, 6 ruas → 0 anomalias (fim do nan|nan)  OK')

# ── 12. Gate imune a python -O (RadarQualityError, não assert)
import subprocess
_code = (
    "import sys; sys.path.insert(0, sys.argv[1])\n"
    "import pandas as pd, radar_utils as xu\n"
    "col = pd.DataFrame({'CLASSE': ['GRANDE'], 'NUMERO': [0], 'TIPO': ['x'], 'CONF': ['ALTA']})\n"
    "v = pd.DataFrame({'NUMERO': pd.Series(dtype=float)})\n"
    "try:\n"
    "    xu.gate_pos_condicoes(col, v.copy(), v.copy(), pd.DataFrame())\n"
    "    print('FURO')\n"
    "except xu.RadarQualityError:\n"
    "    print('BLOQUEOU')\n")
_out = subprocess.run([sys.executable, '-O', '-c', _code,
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')],
                      capture_output=True, text=True)
assert 'BLOQUEOU' in _out.stdout, f'gate falhou sob -O: {_out.stdout} {_out.stderr}'
ok += 1; print('[12] gate sob python -O: violação ainda aborta (RadarQualityError)  OK')

# ── 13. v4.5.1: S2 conta UNIDADES DISTINTAS; dup exata não fabrica anomalia
def _seq2(unidades, repeticoes=1, extra_dup_de=None):
    rows = []
    for u in unidades:
        for r in range(repeticoes):
            rows.append({'LATITUDE': -29.9, 'LONGITUDE': -51.1, 'NOM_SEGLOGR': 'RUA T',
                         'NUM_ENDERECO': 77, 'CHAVE': '430|RUA T|77|C',
                         'UNIDADE_VALOR': str(u), 'PADRAO_ENDERECO': 'LOGRADOURO',
                         'DSC_ESTABELECIMENTO': '', 'COD_UNICO_ENDERECO': f'{u}-{r}'})
    if extra_dup_de is not None:
        rows.append(dict(rows[0]))          # duplicata EXATA
    return pd.DataFrame(rows)
r8x2 = xu.detectar_anomalia_coleta(_seq2(range(101, 109), repeticoes=2))   # 8 un × 2 = 16 linhas
assert int(r8x2['FLAG_ANOMALIA_COLETA'].sum()) == 0, '8 unidades×2 registros não é 16 unidades'
d = _seq2(range(101, 115), extra_dup_de=0)                                  # 14 un + 1 dup = 15 linhas
d_dedup = d.drop_duplicates().reset_index(drop=True)                        # ordem canônica: dedup 1º
r14 = xu.detectar_anomalia_coleta(d_dedup)
assert int(r14['FLAG_ANOMALIA_COLETA'].sum()) == 0, 'dup exata não pode fabricar S2 (dedup precede)'
r15 = xu.detectar_anomalia_coleta(_seq2(range(101, 116)))                   # 15 unidades reais
assert int(r15['FLAG_ANOMALIA_COLETA'].sum()) == 15, '15 unidades distintas flagra'
ok += 1; print('[13] S2 nunique + dedup-primeiro: 8×2 não flagra; 14+dup não flagra; 15 flagra  OK')

# ── 14. v4.5.1: F7 fail-closed — coluna de confiança ausente ABORTA
colv, chzv, nresv, pov = _frames()
nres_sem_f7 = pd.DataFrame({'NUMERO': [10], 'SETOR_ATIVIDADE': ['COMERCIO_VAREJO']})
try:
    xu.gate_pos_condicoes(colv, chzv, nres_sem_f7, pov)
    raise SystemExit('gate deixou Nao_Residencial SEM RADAR_CONF_FAIXA passar')
except xu.RadarQualityError as e:
    assert 'RADAR_CONF_FAIXA ausente' in str(e)
try:
    xu.detectar_polos_comerciais(_polo_df(5, 1), eps_m=100, min_samples=5)   # default exigir_conf=True
    raise SystemExit('polos rodaram sem F7 no modo fail-closed')
except xu.RadarQualityError as e:
    assert 'RADAR_CONF_FAIXA ausente' in str(e)
ok += 1; print('[14] F7 fail-closed: gate e polos abortam sem a coluna de confiança  OK')

# ── 15. v4.5.2 (X1): NÚCLEO ÚNICO — determinismo, invariantes e funil de
#        radar_pipeline.preparar_base (o mesmo df que runner/Excel/mapa consomem)
import subprocess as _sp
_here = os.path.dirname(os.path.abspath(__file__))
_csv = os.path.join(_here, 'base_sintetica.csv')
if not os.path.exists(_csv):
    _sp.run([sys.executable, os.path.join(_here, 'gen_sintetico.py')], check=True)
import radar_pipeline as rp
df_a, st_a = rp.preparar_base(_csv)
df_b, st_b = rp.preparar_base(_csv)
# funil fecha por aritmética (W2/X5) e é determinístico entre execuções
assert st_a['entrada_raw'] - st_a['duplicados_exatos_removidos'] == st_a['apos_dedup']
assert st_a == st_b, f'determinismo: stats divergem: {st_a} != {st_b}'
# paridade semântica: mesmas linhas, mesmas decisões (o contrato dos writers)
_key = ['CHAVE', 'RADAR_ID_ENDERECO', 'FLAG_COL', 'FLAG_ANOMALIA_COLETA',
        'SANIDADE_GEO', 'RADAR_CONF_FAIXA']
pd.testing.assert_frame_equal(
    df_a[_key].sort_values(_key).reset_index(drop=True),
    df_b[_key].sort_values(_key).reset_index(drop=True))
# máscaras canônicas conferem com o stats publicado no manifest
m_u = rp.gate_universal(df_a)
m_c = rp.gate_conf(df_a)
assert int(m_u.sum()) == st_a['gate_universal_aprovado']
assert int(m_c.sum()) == st_a['gate_f7_alta_mais']
assert not (m_c & ~m_u).any(), 'gate_conf deve ser SUBCONJUNTO do universal'
# invariantes R1/R2/geo em TODO aprovado; FLAG_COL nunca sobrevive fora do gate
assert (df_a.loc[m_u, 'NUMERO'] > 0).all()
assert (df_a.loc[m_u, 'SANIDADE_GEO'] == 'OK').all()
# v4.11: a anomalia deixou de ser binaria. O aprovado pode carregar 1 ou 2
# sinais primarios (observacao/quarentena, marcados em CNF_ELEGIBILIDADE); o
# que NAO pode e' passar com 3+ sinais independentes.
assert (df_a.loc[m_u, 'N_SINAIS_PRIMARIOS'].fillna(0).astype(int)
        < rp.ANOMALIA_EXCLUI_A_PARTIR_DE).all()
assert df_a.loc[m_u, 'CNF_ELEGIBILIDADE'].str.startswith('ELEGIVEL').all()
assert not ((df_a['FLAG_COL'] == 1) & ~m_u).any(), 'coletiva fora do gate universal'
assert 'RADAR_CONF_FAIXA' in df_a.columns and 'RADAR_ID_ENDERECO' in df_a.columns
ok += 1; print('[15] núcleo único preparar_base: determinístico, funil fecha, '
               'gates conferem, invariantes R1/R2/geo nos aprovados  OK')


# ── 16. PDCA-01 (N13): FIDELIDADE do id herdado pela hipótese.
#        Regressão de um bug REAL: `pd.DataFrame(list_of_dicts)` misturando
#        id de 19 dígitos com None (gap QUADRA_LOTE = lote sem pai único)
#        inferia float64 e ARREDONDAVA o id acima de 2^53 (…996 -> …888).
#        A hipótese virava órfã: join com o endereço-pai devolvia ZERO linhas.
#        Injetividade NÃO detecta isso — só integridade referencial detecta.
_V = 4700095277622949996            # id real, > 2^53
_rows = [{'RADAR_ID_ENDERECO': np.int64(_V), 'X': 1},
         {'RADAR_ID_ENDERECO': None, 'X': 2}]          # lote Q/L sem pai
assert str(pd.DataFrame(_rows)['RADAR_ID_ENDERECO'].iloc[0]) != str(_V), \
    'pré-condição do teste: a inferência ingênua TEM de corromper'
_fix = pd.array([r['RADAR_ID_ENDERECO'] for r in _rows], dtype='Int64')
assert str(_fix[0]) == str(_V) and pd.isna(_fix[1]), 'Int64 explícito preserva id e NA'

# gate: hipótese com id que não existe na base ABORTA o export
_base = pd.DataFrame({
    'FLAG_COL': [1], 'FLAG_ANOMALIA_COLETA': [0], 'SANIDADE_GEO': ['OK'],
    'NUMERO': [100], 'RADAR_ID_ENDERECO': pd.array([_V], dtype='Int64'),
    'RADAR_CHAVE_CANONICA': ['4300604|AVENIDA BRASIL|N100|CENTRO']})
_col = pd.DataFrame({'CLASSE': ['GRANDE'], 'CONF_TIPOLOGIA': ['ALTA'], 'NUMERO': [100]})
_vazio = pd.DataFrame()
_hip_ok = pd.DataFrame({'RADAR_ID_ENDERECO': [str(_V), ''], 'NUMERO': [100, 0],
                        'TIPO_HIPOTESE': ['POSICIONAL', 'QUADRA_LOTE'],
                        'REQUER_CONFIRMACAO': ['SIM', 'SIM'], 'RADAR_CONF_COORD': [0, 0]})
xu.gate_pos_condicoes(coletivas=_col, cond_horiz=_vazio, nres=_vazio, polos=_vazio,
                      hipoteses=_hip_ok, df_base=_base, nomes_abas=())
_hip_bad = _hip_ok.copy()
_hip_bad.loc[0, 'RADAR_ID_ENDERECO'] = str(_V - 108)      # id arredondado
try:
    xu.gate_pos_condicoes(coletivas=_col, cond_horiz=_vazio, nres=_vazio, polos=_vazio,
                          hipoteses=_hip_bad, df_base=_base, nomes_abas=())
    raise SystemExit('gate aceitou hipótese com id órfão/corrompido')
except xu.RadarQualityError as e:
    assert 'sem endereço-pai' in str(e)
ok += 1; print('[16] PDCA-01: id de hipótese fiel (sem float64) e gate aborta id órfão  OK')


# ── 17. R8 — ID ROUND-TRIP INVARIANT: o id sobrevive à CADEIA INTEIRA de
#        serialização, não a uma função. DataFrame -> concat -> merge ->
#        Excel -> read_excel(dtype=str). Exigência: original == final.
#        Sentinelas escolhidas na borda exata do float64.
import tempfile as _tf
_SENT = [
    2**53 - 1,                 # último inteiro que o float64 representa
    2**53,                     # a borda
    2**53 + 1,                 # primeiro que ele NÃO representa
    9007199254740993,          # = 2^53+1, o caso clássico
    4700095277622949996,       # id real (AVENIDA BRASIL 100) — o do PDCA-01
    8403508116556714592,       # id real (RUA ERNESTO PEREIRA 698)
    (2**63) - 1,               # teto do int64 assinado
]
_orig = [str(v) for v in _SENT] + ['']            # inclui o NA/lote sem pai

# etapa 1 — construção a partir de list_of_dicts COM nulo (o gatilho real)
_linhas = [{'RADAR_ID_ENDERECO': v, 'ID_COLETIVA': i + 1, 'K': f'k{i}'}
           for i, v in enumerate(_SENT)] + \
          [{'RADAR_ID_ENDERECO': None, 'ID_COLETIVA': None, 'K': 'kNA'}]
_d = xu._pd.DataFrame(_linhas) if hasattr(xu, '_pd') else pd.DataFrame(_linhas)
for _c in xu.colunas_identificador(_d):
    _d[_c] = pd.Series(xu.id_seguro([r.get(_c) for r in _linhas]), index=_d.index)
assert all(str(_d['RADAR_ID_ENDERECO'].iloc[i]) == _orig[i] or _orig[i] == ''
           for i in range(len(_SENT))), 'etapa 1 (construção) perdeu dígito'

# etapa 2 — concat (upcast silencioso é o modo de falha aqui)
_d2 = pd.concat([_d.iloc[:3], _d.iloc[3:]], ignore_index=True)
assert _d2['RADAR_ID_ENDERECO'].dtype == 'Int64', f'concat degradou: {_d2.dtypes.to_dict()}'

# etapa 3 — merge (a chave de join não pode ser reconstruída em float)
_lado = pd.DataFrame({'K': [f'k{i}' for i in range(len(_SENT))],
                      'EXTRA': list(range(len(_SENT)))})
_d3 = _d2.merge(_lado, on='K', how='left')
assert _d3['RADAR_ID_ENDERECO'].dtype == 'Int64', 'merge degradou o dtype do id'

# etapa 4 — fronteira externa: contrato R8 = UTF-8 string
_d4 = _d3.copy()
for _c in xu.colunas_identificador(_d4):
    _d4[_c] = pd.Series(xu.id_seguro(_d4[_c].tolist(), saida='str'), index=_d4.index)
assert _d4['RADAR_ID_ENDERECO'].tolist()[:len(_SENT)] == _orig[:len(_SENT)]

# etapa 5 — Excel ida e volta (o formato NÃO preserva 19 dígitos como número)
with _tf.NamedTemporaryFile(suffix='.xlsx', delete=False) as _fh:
    _p = _fh.name
_d4.to_excel(_p, index=False)
_volta = pd.read_excel(_p, dtype={'RADAR_ID_ENDERECO': str, 'ID_COLETIVA': str})
_final = [('' if pd.isna(x) else str(x).strip())
          for x in _volta['RADAR_ID_ENDERECO'].tolist()]
assert _final == _orig, f'ROUND-TRIP QUEBROU:\n  orig  {_orig}\n  final {_final}'
os.unlink(_p)

# etapa 6 — a varredura R8 acusa quem burla a cadeia (float em coluna de id)
_sujo = _d3.copy()
_sujo['RADAR_ID_ENDERECO'] = _sujo['RADAR_ID_ENDERECO'].astype('float64')
_v = xu.varredura_ids({'frame_sujo': _sujo})
assert _v and 'float' in _v[0], 'varredura R8 deixou passar dtype float em id'
assert not xu.varredura_ids({'frame_ok': _d3}), 'varredura R8 tem falso positivo'

# etapa 7 — id_seguro RECUSA float acima de 2^53 (o dígito já se perdeu:
# converter depois só carimbaria o erro como verdade)
try:
    xu.id_seguro([float(2**60)])
    raise SystemExit('id_seguro aceitou float acima de 2^53')
except xu.RadarQualityError as e:
    assert '2^53' in str(e)
ok += 1; print('[17] R8 round-trip de id: construção→concat→merge→str→Excel→leitura '
               f'preserva {len(_SENT)} sentinelas + NA; varredura sem falso positivo  OK')

print(f'TOTAL FINAL: {ok} testes')
