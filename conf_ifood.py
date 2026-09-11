# -*- coding: utf-8 -*-
"""Os numeros que o iFood publica existem na base — de QUAL cidade?"""
import base_comum as bc
import regra_vinculo as rv
from cruzar_ligacao import _via

SEM_ACENTO = ("translate(lower(coalesce(cidade,'')),"
              "'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn')")

SQL_POIS = """
select distinct p.id, lr.logradouro, lr.numero
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
 where p.fonte = 'ifood' and p.fundido_em is null
   and lp.descartado_motivo like 'a fonte publica outro numero%'
"""

con = bc.conectar()
cur = con.cursor()
cur.execute(SQL_POIS)
alvos = [(r[0], r[1], r[2]) for r in cur.fetchall()]
print("POIs do iFood que cairam por numero divergente: %d" % len(alvos))

for rotulo, filtro in (("SO CANOAS", " where " + SEM_ACENTO + " = 'canoas'"),
                       ("TODAS AS CIDADES", "")):
    cur.execute("select coalesce(nom_logradouro,''), coalesce(nro,'') "
                "from resources_root.cadastro_corsan" + filtro)
    base = set()
    for logr, nro in cur:
        v, n = _via(logr), rv.numero_limpo(nro)
        if v and n:
            base.add((v, n))
    tem = nao = 0
    for pid, logr, nro in alvos:
        v, n = _via(logr or ""), rv.numero_limpo(nro or "")
        if v and n and (v, n) in base:
            tem += 1
        else:
            nao += 1
    print("%-18s  enderecos na base: %7d  |  existe: %3d  |  nao existe: %3d"
          % (rotulo, len(base), tem, nao))
con.close()
