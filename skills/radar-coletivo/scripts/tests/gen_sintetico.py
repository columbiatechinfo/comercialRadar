#!/usr/bin/env python3
"""Gera base sintética determinística com casos de borda p/ smoke da v4.3."""
import csv, random
from pathlib import Path

random.seed(43)
OUT = Path(__file__).parent / 'base_sintetica.csv'

COLS = ['COD_UNICO_ENDERECO', 'COD_UF', 'COD_MUNICIPIO', 'COD_SETOR', 'CEP',
        'NOM_TIPO_SEGLOGR', 'NOM_TITULO_SEGLOGR', 'NOM_SEGLOGR', 'NUM_ENDERECO',
        'DSC_LOCALIDADE', 'LATITUDE', 'LONGITUDE', 'NV_GEO_COORD', 'COD_ESPECIE',
        'COD_TIPO_ESPECIE', 'COD_INDICADOR_ESTAB_ENDERECO',
        'NOM_COMP_ELEM1', 'VAL_COMP_ELEM1', 'NOM_COMP_ELEM2', 'VAL_COMP_ELEM2',
        'NOM_COMP_ELEM3', 'VAL_COMP_ELEM3', 'NOM_COMP_ELEM4', 'VAL_COMP_ELEM4',
        'NOM_COMP_ELEM5', 'VAL_COMP_ELEM5', 'DSC_ESTABELECIMENTO']

rows, _id = [], 0
def add(mun, setor, cep, tipo, nome, num, loc, lat, lon, nv, esp, tesp,
        ind='', c1=('', ''), c2=('', ''), c3=('', ''), estab='', uf=43, titulo=''):
    global _id
    _id += 1
    rows.append({'COD_UNICO_ENDERECO': str(10_000_000 + _id), 'COD_UF': uf,
                 'COD_MUNICIPIO': mun, 'COD_SETOR': setor, 'CEP': cep,
                 'NOM_TIPO_SEGLOGR': tipo, 'NOM_TITULO_SEGLOGR': titulo,
                 'NOM_SEGLOGR': nome, 'NUM_ENDERECO': num, 'DSC_LOCALIDADE': loc,
                 'LATITUDE': f'{lat:.6f}', 'LONGITUDE': f'{lon:.6f}',
                 'NV_GEO_COORD': nv, 'COD_ESPECIE': esp, 'COD_TIPO_ESPECIE': tesp,
                 'COD_INDICADOR_ESTAB_ENDERECO': ind,
                 'NOM_COMP_ELEM1': c1[0], 'VAL_COMP_ELEM1': c1[1],
                 'NOM_COMP_ELEM2': c2[0], 'VAL_COMP_ELEM2': c2[1],
                 'NOM_COMP_ELEM3': c3[0], 'VAL_COMP_ELEM3': c3[1],
                 'NOM_COMP_ELEM4': '', 'VAL_COMP_ELEM4': '',
                 'NOM_COMP_ELEM5': '', 'VAL_COMP_ELEM5': '',
                 'DSC_ESTABELECIMENTO': estab})

MA, SA, CEPA = 4300604, '430060405000001', '92010000'   # município A (sul)
MB, SB, CEPB = 3550308, '355030805000001', '01001000'   # município B (CEP com zero à esquerda)
LA, LO = -29.920, -51.180
LB, LOB = -23.550, -46.630

def j(k=0.0008):
    return random.uniform(-k, k)

# ── A1. Ruído urbano normal (casas espalhadas, ruas variadas)
for i in range(300):
    add(MA, SA, CEPA, 'RUA', f'RUA GERAL {i % 12}', str(10 + i), 'CENTRO',
        LA + j(0.02), LO + j(0.02), random.choice([1, 1, 2, 3]), 1, 101)

# ── A2. Harmonização + fusão de CHAVE: FLORENCA vs FLORENÇA no mesmo nº 50
for i in range(30):
    add(MA, SA, CEPA, 'RUA', 'FLORENCA', str(100 + i), 'CENTRO', LA + j(), LO + j(), 1, 1, 101)
add(MA, SA, CEPA, 'RUA', 'FLORENCA', '50', 'CENTRO', LA, LO, 1, 1, 101,
    c1=('CASA', '1'))
add(MA, SA, CEPA, 'RUA', 'FLORENÇA', '50', 'CENTRO', LA + 0.00002, LO, 1, 1, 101,
    c1=('CASA', '2'))

# ── A3. Edifício GRANDE: 2 blocos × grade 101-204, falta APTO 203 no bloco B
for bl in ['A', 'B']:
    for ap in [101, 102, 201, 202, 203, 204]:
        if bl == 'B' and ap == 203:
            continue
        add(MA, SA, CEPA, 'RUA', 'ERNESTO PEREIRA', '698', 'CENTRO',
            LA + 0.003, LO + 0.003, 1, 1, 103,
            c1=('BLOCO', bl), c2=('APARTAMENTO', str(ap)))

# ── A4. Frente sem fundos (gap POSICIONAL)
add(MA, SA, CEPA, 'AVENIDA', 'BRASIL', '100', 'CENTRO', LA + 0.004, LO, 1, 1, 101,
    c1=('FRENTE', ''))
add(MA, SA, CEPA, 'AVENIDA', 'BRASIL', '100', 'CENTRO', LA + 0.004, LO, 1, 1, 101)

# ── A5. DIR sem ESQ (valida N1 — ESQ/DIR na camada POSICAO)
add(MA, SA, CEPA, 'RUA', 'GARIBALDI', '300', 'CENTRO', LA + 0.005, LO, 1, 1, 101,
    c1=('DIR', ''))
add(MA, SA, CEPA, 'RUA', 'GARIBALDI', '300', 'CENTRO', LA + 0.005, LO, 1, 1, 101)

# ── A6. Dupla TERREO/ANDAR (valida N7: DUPLA_END MEDIA)
add(MA, SA, CEPA, 'RUA', 'PAV SPLIT', '42', 'CENTRO', LA + 0.006, LO, 1, 1, 101,
    c1=('TERREO', ''))
add(MA, SA, CEPA, 'RUA', 'PAV SPLIT', '42', 'CENTRO', LA + 0.006, LO, 1, 1, 101,
    c1=('ANDAR', '1'))

# ── A7. Quadra/Lote (Padrão B, NUM vazio): Q10,11,13 → falta Q12
for q in [10, 11, 13]:
    for l in [1, 2, 3]:
        add(MA, SA, CEPA, 'RUA', 'CAMOBI', '', 'LOTEAMENTO SUL',
            LA + 0.010 + q * 1e-4, LO + 0.010 + l * 1e-4, 2, 1, 101,
            c1=('QUADRA', str(q)), c2=('LOTE', str(l)), c3=('CASA', str(l)))

# ── A8. Fraude S1: mesma coordenada, 4 logradouros distintos
for nome in ['RUA ALFA', 'RUA BETA', 'RUA GAMA', 'RUA DELTA']:
    for i in range(3):
        add(MA, SA, CEPA, 'RUA', nome, str(700 + i), 'CENTRO',
            -29.930000, -51.200000, 1, 1, 101)

# ── A9. Fraude S3: estabelecimento repetido 6× no mesmo endereço
for i in range(6):
    add(MA, SA, CEPA, 'RUA', 'DO COMERCIO', '500', 'CENTRO',
        LA + 0.007, LO + 0.001, 1, 6, 104, estab='MERCADO CENTRAL')

# ── A10. Fraude S2: sequência perfeita de 20 aptos
for u in range(1, 21):
    add(MA, SA, CEPA, 'AVENIDA', 'SEQ PERFEITA', '999', 'CENTRO',
        LA + 0.008, LO + 0.002, 1, 1, 103, c1=('APARTAMENTO', str(u)))

# ── A11. Vila horizontal: CASA 1..6, tipo 102 → aba Condominios_Horizontais
for c in range(1, 7):
    add(MA, SA, CEPA, 'RUA', 'DAS VILAS', '50', 'BAIRRO NOVO',
        LA + 0.009, LO + 0.003, 1, 1, 102, c1=('CASA', str(c)))

# ── A12. Léxico T2 (espécie 6 → keyword)
casos_t2 = [
    ('POSTO PETROBRAS', 950), ('AUTO POSTO SAO JOAO', 951),
    ('POSTO DE SAUDE CENTRAL', 952), ('PET SHOP AMIGO FIEL', 953),
    ('TAPETES PERSAS LTDA', 954), ('LAVANDERIA EXPRESS', 955),
    ('LAVA JATO DO ZE', 956), ('SUPERMERCADO BOM PRECO', 957),
    ('BARBEARIA DO CARLOS', 958), ('AGENCIA IBGE', 959),
]
for estab, num in casos_t2:
    add(MA, SA, CEPA, 'RUA', 'DOS NEGOCIOS', str(num), 'CENTRO',
        LA + 0.011, LO + 0.004 + (num - 950) * 1e-4, 1, 6, 104, estab=estab)

# ── A13. Uso misto no mesmo endereço
add(MA, SA, CEPA, 'RUA', 'MISTA', '800', 'CENTRO', LA + 0.012, LO, 1, 1, 101)
add(MA, SA, CEPA, 'RUA', 'MISTA', '800', 'CENTRO', LA + 0.012, LO, 1, 6, 104,
    c1=('LOJA', '1'), estab='PADARIA DO ZE')

# ── A14. MULT_ESTAB isolado (indicador oficial=2) — valida N4/R5 no TIPO
add(MA, SA, CEPA, 'RUA', 'SOLITARIA', '77', 'CENTRO', LA + 0.013, LO, 1, 6, 104,
    ind='2', estab='GALERIA POPULAR')

# ── A15. Polo comercial: 8 endereços comerciais em ~60 m
for i in range(8):
    add(MA, SA, CEPA, 'AVENIDA', 'CENTRAL', str(1000 + i * 2), 'CENTRO',
        LA + 0.020 + i * 5e-5, LO + 0.020, 1, 6, 104, estab=f'LOJA CENTRAL {i}')

# ── A16. Outlier geo grosseiro no município A
add(MA, SA, CEPA, 'RUA', 'PERDIDA', '1', 'ZONA RURAL', 0.0, 0.0, 5, 1, 101)

# ── B. Município B (SP): CEP com zero à esquerda + coordenada NV1 confirmando
for i in range(80):
    add(MB, SB, CEPB, 'RUA', 'PAULISTA SIM', str(20 + i), 'SE',
        LB + j(0.004), LOB + j(0.004), 1, 1, 101, uf=35)
# comercial B p/ ver CEP na aba Nao_Residencial
add(MB, SB, CEPB, 'RUA', 'PAULISTA SIM', '500', 'SE', LB, LOB, 1, 6, 104,
    uf=35, estab='ESCRITORIO PAULISTA')

# ── DUP. 5 linhas duplicadas integrais (valida N5)
rows.extend([dict(r) for r in rows[:5]])

with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=COLS, delimiter=';')
    w.writeheader()
    w.writerows(rows)
print(f'{OUT} — {len(rows)} linhas')
