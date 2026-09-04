# -*- coding: utf-8 -*-
"""auditar_coord2.py — a coordenada e do (rua, numero) que o endereco diz?

POR QUE O TESTE ANTERIOR NAO SERVIA. Medir a distancia ate a RUA nao pega o
defeito quando a rua passa perto: no POI 91794 o endereco diz "AVENIDA RIO
GRANDE DO SUL, 43" e a coordenada gravada e a do "BECO DEODORO DA FONSECA, 43".
A avenida passa a 100 m dali, entao "distancia ate a avenida" da um numero
pequeno e o caso passa como bom.

O TESTE CERTO E POR (RUA, NUMERO). Procura-se no CNEFE aquele numero NAQUELA
rua e mede-se a distancia. E ha um caso pior que "longe": o numero NAO EXISTIR
naquela rua e mesmo assim a coordenada estar carimbada `cnefe_numero_exato` —
isso e o geocodificador afirmando um casamento que nao aconteceu. A causa e o
indice: `fontes_para_poi.Cnefe` indexa so por CEP, e num CEP generico
(`92330-000` cobre Mathias Velho inteiro) o "numero 43" e o 43 de qualquer rua.
"""
from __future__ import annotations

import math
import re
import sys
from collections import defaultdict

import base_comum as bc

PERTO_M = 80.0            # o numero certo, na rua certa, deve estar aqui dentro

_ACENTO = str.maketrans("ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ", "AAAAAEEEEIIIIOOOOOUUUUC")
_TIPOS = {"AV", "AVE", "AVENIDA", "R", "RU", "RUA", "TV", "TRAV", "TRAVESSA",
          "EST", "ESTR", "ESTRADA", "ROD", "RODOVIA", "PC", "PCA", "PRACA",
          "BC", "BECO", "AL", "ALAMEDA", "LG", "LARGO", "VL", "VILA"}


def via_normal(txt: str) -> str:
    s = (txt or "").upper().translate(_ACENTO)
    s = s.split(",")[0].split(" - ")[0]
    p = re.sub(r"[^A-Z0-9 ]", " ", s).split()
    if p and p[0] in _TIPOS:
        p = p[1:]
    return " ".join(p)


_PORTA = re.compile(r"^[^,]+,\s*(\d{1,6})(?!\d)")


def numero_de(end: str):
    m = _PORTA.match(end or "")
    return int(m.group(1)) if m else None


def metros(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


so_fila = "--fila" in sys.argv
con = bc.conectar()
cur = con.cursor()
filtro = ("and exists (select 1 from radar_comercial.poi_veredito v "
          "where v.poi_id = p.id)" if so_fila else "")
cur.execute("""
    select p.id, coalesce(p.nome,''), coalesce(p.endereco,''),
           coalesce(p.coord_fonte,''),
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
      from radar_comercial.pois p
     where p.pt_geo is not null and p.cidade ilike 'canoas' %s
""" % filtro)
pois = cur.fetchall()
print("POIs a auditar: %d" % len(pois))

cur.execute("""
    select nom_seglogr, num_endereco, latitude::float8, longitude::float8
      from resources_root.ibge_cnefe
     where cod_municipio = '4304606'
       and latitude is not null and longitude is not null
""")
por_via_num = defaultdict(list)
todos = []
for via, num, la, lo in cur:
    v = via_normal(via)
    d = re.sub(r"\D", "", str(num or ""))
    if d:
        por_via_num[(v, int(d))].append((la, lo))
    todos.append((v, la, lo))
print("CNEFE de Canoas: %d enderecos" % len(todos))
con.close()


# UMA GRADE, E NÃO UMA VARREDURA. Procurar a rua mais próxima comparando com
# os 176 mil endereços do município custa 176 mil contas POR POI. A grade de
# 0,001 grau (~100 m) reduz a busca às nove células vizinhas, e o resultado é o
# mesmo para a pergunta que se faz aqui — "o ponto caiu em que rua".
GRADE = defaultdict(list)
for _v, _a, _b in todos:
    GRADE[(round(_a, 3), round(_b, 3))].append((_v, _a, _b))


def rua_do_ponto(la, lo):
    """Em que rua o ponto gravado de fato esta — o flagrante do caso errado."""
    melhor, dm = None, 1e9
    ca, cb = round(la, 3), round(lo, 3)
    for da in (-0.001, 0, 0.001):
        for db in (-0.001, 0, 0.001):
            for v, a, b in GRADE.get((round(ca + da, 3), round(cb + db, 3)), ()):
                d = metros(la, lo, a, b)
                if d < dm:
                    melhor, dm = v, d
    return melhor, dm


def mesma_via(a: str, b: str) -> bool:
    """Duas grafias da mesma rua.

    O CNEFE escreve "ORLANDO GOETHE" e a Receita "ORLANDO GHOETE"; um traz
    "SANTOS DIAS" e o outro "SANTOS DIAS DA SILVA". Tratar isso como rua
    diferente encheria a lista de suspeitos com erro de digitacao, e um alarme
    que grita por qualquer coisa deixa de ser lido. A regra e a de conter: uma
    grafia dentro da outra e a mesma via.
    """
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    # Mesma primeira palavra E mesmo comprimento aproximado cobre a troca de
    # letras ("GOETHE"/"GHOETE") sem casar ruas de nomes realmente diferentes.
    pa, pb = a.split(), b.split()
    if len(pa) == len(pb) and pa[0][:4] == pb[0][:4]:
        iguais = sum(1 for x, y in zip(pa, pb) if sorted(x) == sorted(y))
        return iguais == len(pa)
    return False


# As vias que o CNEFE do municipio conhece. E o que separa "o ponto caiu noutra
# rua" de "eu nao sei escrever o nome desta rua do mesmo jeito que o IBGE".
VIAS_CONHECIDAS = {v for v, _a, _b in todos}

aplicar = "--aplicar" in sys.argv
placar = defaultdict(int)
marcar = []          # (poi_id, suspeita, motivo)

for pid, nome, end, fonte, la, lo in pois:
    via, num = via_normal(end), numero_de(end)
    real, dreal = rua_do_ponto(la, lo)

    if not via or num is None:
        # SEM RUA OU SEM NÚMERO NÃO HÁ O QUE CONFERIR, e isso não é o mesmo que
        # estar errado. Fica NULL: ninguém afirma nada sobre esta coordenada.
        placar["nao_da_para_conferir"] += 1
        continue

    pts = por_via_num.get((via, num))
    if pts:
        d = min(metros(la, lo, a, b) for a, b in pts)
        if d <= PERTO_M:
            placar["ok"] += 1
            marcar.append((pid, False, None))
        else:
            placar["longe_do_numero_certo"] += 1
            marcar.append((pid, True,
                           "o %s desta rua fica a %.0f m daqui; o ponto caiu em %s"
                           % (num, d, real or "via desconhecida")))
        continue

    # O número não existe naquela rua no CNEFE. Aí o que decide é a rua em que
    # o ponto REALMENTE caiu: se for a mesma via, o ponto está no lugar certo e
    # o que falta é só o número no cadastro do IBGE — isso não é defeito nosso.
    if mesma_via(via, real or ""):
        placar["numero_ausente_mas_rua_certa"] += 1
        marcar.append((pid, False, None))
    elif via not in VIAS_CONHECIDAS:
        # A RUA DO ENDEREÇO NÃO EXISTE NO CNEFE COM ESSA GRAFIA, e aí não há
        # como acusar. "NASCENTE DO SOL" do cadastro é a "NASCER DO SOL" do
        # IBGE; "ORLANDO GHOETE" é "ORLANDO GOETHE". Chamar isso de rua errada
        # encheria a lista de suspeitos com erro de digitação, e alarme que
        # grita por qualquer coisa deixa de ser lido. Fica sem veredito: NULL.
        placar["rua_do_endereco_nao_esta_no_cnefe"] += 1
    else:
        placar["rua_errada"] += 1
        marcar.append((pid, True,
                       "endereco diz %s %s; o ponto caiu na %s (a %.0f m)"
                       % (via, num, real or "via desconhecida", dreal)))

print()
for k in sorted(placar, key=lambda x: -placar[x]):
    print("   %-32s %5d" % (k, placar[k]))

suspeitos = [m for m in marcar if m[1]]
print("\n%d suspeitos. Os primeiros:" % len(suspeitos))
nomes = {p[0]: p[1] for p in pois}
for pid, _s, motivo in suspeitos[:20]:
    print("   %8d %-30s %s" % (pid, nomes.get(pid, "")[:30], motivo))

if aplicar and marcar:
    con = bc.conectar()
    with con.cursor() as k:
        from psycopg2.extras import execute_values
        # PAGE_SIZE MAIOR QUE O LOTE, de propósito. `execute_values` divide em
        # páginas de 100 e o `rowcount` que sobra é o da ÚLTIMA — dizer "37
        # marcadas" quando foram 137 é o mesmo tipo de mentira que a auditoria
        # existe para achar. Numa página só, o número é o número.
        execute_values(k, """
            update radar_comercial.pois p
               set coord_suspeita = d.suspeita,
                   coord_motivo = d.motivo,
                   coord_conferida_em = now()
              from (values %s) as d(id, suspeita, motivo)
             where p.id = d.id
        """, [(a, b, c) for a, b, c in marcar],
            template="(%s, %s::boolean, %s)", page_size=len(marcar) + 1)
        print("\nlinhas marcadas: %d (esperado %d)" % (k.rowcount, len(marcar)))
    con.commit()
    con.close()
else:
    print("\n(ensaio: nada gravado. Use --aplicar)")
