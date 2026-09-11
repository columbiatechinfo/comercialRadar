# -*- coding: utf-8 -*-
"""Os POIs do iFood que casam por endereco e estao fora do teto."""
import csv
import io
import math

import base_comum as bc
import regra_vinculo as rv
from cruzar_ligacao import _via

SEM_ACENTO = ("translate(lower(coalesce(cidade,'')),"
              "'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn')")


def metros(la1, lo1, la2, lo2):
    if None in (la1, lo1, la2, lo2):
        return None
    dy = (la2 - la1) * 111320.0
    dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, dy)


con = bc.conectar()
cur = con.cursor()

cur.execute("select num_ligacao::text, coalesce(nom_logradouro,''), "
            "coalesce(nro,''), coalesce(nom_bairro,''), cod_latitude::float8, "
            "cod_longitude::float8 from resources_root.cadastro_corsan "
            "where " + SEM_ACENTO + " = 'canoas'")
porta = {}
for num, logr, nro, bairro, la, lo in cur:
    v, n = _via(logr), rv.numero_limpo(nro)
    if v and n:
        porta.setdefault((v, n), []).append((num, logr, nro, bairro, la, lo))

cur.execute("""
    select p.id, coalesce(p.nome,''), lr.logradouro, lr.numero,
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
      from radar_comercial.pois p
      join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
     where p.fonte = 'ifood' and p.fundido_em is null
       and lr.forca = 'prova'
       and not exists (select 1 from radar_comercial.ligacao_poi lp
                        where lp.poi_id = p.id and lp.descartado_em is null)""")

linhas = []
for pid, nome, logr, nro, pla, plo in cur:
    v, n = _via(logr or ""), rv.numero_limpo(nro or "")
    if not v or not n:
        continue
    for (numlig, l_logr, l_nro, bairro, la, lo) in porta.get((v, n), []):
        d = metros(pla, plo, la, lo)
        if d is None or d <= 50:
            continue
        linhas.append({
            "poi": pid, "nome_do_poi": nome,
            "endereco_do_poi": ("%s, %s" % (logr, nro)).strip(", "),
            "poi_lat": round(pla, 6), "poi_lng": round(plo, 6),
            "ligacao": numlig,
            "endereco_da_ligacao": ("%s, %s" % (l_logr, l_nro)).strip(", "),
            "bairro": bairro,
            "lig_lat": round(la, 6), "lig_lng": round(lo, 6),
            "metros": round(d)})
        break

linhas.sort(key=lambda x: x["metros"])
print("pares fora do teto: %d" % len(linhas))
with io.open("/saida/ifood102.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()) if linhas else [])
    w.writeheader()
    for r in linhas:
        w.writerow(r)
for r in linhas[:50]:
    print("%-30s | POI %-28s | LIG %-28s | %5d m | %s,%s"
          % (r["nome_do_poi"][:30], r["endereco_do_poi"][:28],
             r["endereco_da_ligacao"][:28], r["metros"],
             r["lig_lat"], r["lig_lng"]))
con.close()
