# -*- coding: utf-8 -*-
"""Quantas ligacoes marcadas SIM acharam POI — por fonte, distancia e imagem."""
import collections

import base_comum as bc

SEM_ACENTO = ("translate(lower(coalesce(l.cidade,'')),"
              "'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn')")

# TRES CONSULTAS SIMPLES, e o cruzamento em Python.
#
# A versao anterior punha dois `exists` de imagem DENTRO da consulta principal,
# avaliados por linha sobre 94 mil vinculos. Estourou 10 minutos. E a terceira
# vez hoje que escrevo esse mesmo defeito — a captura e a fila do julgamento
# tiveram o gemeo dele, e a correcao foi sempre a mesma.

SQL_VINC = """
select lp.ligacao, lower(coalesce(p.fonte,'?')), lp.metros, p.id,
       coalesce(l.qualificacao,'')
  from radar_comercial.ligacao_poi lp
  join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
  join radar_comercial.pois p on p.id = lp.poi_id
 where lp.descartado_em is null and p.fundido_em is null
   and l.apta_cruzamento
   and upper(l.categoria) = 'RESIDENCIAL'
   and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
   and %s = 'canoas'
""" % SEM_ACENTO

SQL_SV = """select distinct poi_id from radar_comercial.poi_evidencia
             where tipo like 'sv_%' and (dados is not null
                                         or storage_path is not null)"""

SQL_FOTO = """select distinct poi_id from radar_comercial.images_urls
               where url like '%gps-cs-s%' and (dados is not null
                                                or storage_path is not null)"""


def faixa(m):
    if m is None:
        return "sem medida"
    if m <= 10:
        return "ate 10 m"
    if m <= 50:
        return "10-50 m"
    if m <= 100:
        return "50-100 m"
    return "100-200 m"


con = bc.conectar()
cur = con.cursor()
cur.execute(SQL_SV)
com_sv = {r[0] for r in cur.fetchall()}
cur.execute(SQL_FOTO)
com_foto = {r[0] for r in cur.fetchall()}
print("POIs com street view: %d · com foto do Google: %d"
      % (len(com_sv), len(com_foto)))

cur.execute(SQL_VINC)

por_fonte = collections.defaultdict(set)
por_faixa = collections.defaultdict(set)
por_qual = collections.defaultdict(set)
com_img, sem_img = set(), set()
img_por_lig = collections.defaultdict(bool)
todas = set()

for lig, fonte, metros, poi, qual in cur:
    tem_img = poi in com_sv or poi in com_foto
    todas.add(lig)
    por_fonte[fonte].add(lig)
    por_faixa[faixa(metros)].add(lig)
    por_qual[qual or "(vazio)"].add(lig)
    if tem_img:
        img_por_lig[lig] = True
    else:
        img_por_lig.setdefault(lig, False)

for lig, v in img_por_lig.items():
    (com_img if v else sem_img).add(lig)

print("LIGACOES MARCADAS SIM OU SIM_COM_ANALISE_HUMANA QUE ACHARAM AO MENOS 1 POI")
print("total: %d" % len(todas))
print()
print("por marcacao na tabela cadastral")
for k in sorted(por_qual, key=lambda x: -len(por_qual[x])):
    print("   %-24s %6d" % (k, len(por_qual[k])))
print()
print("por fonte do POI  (uma ligacao pode contar em mais de uma)")
for k in sorted(por_fonte, key=lambda x: -len(por_fonte[x])):
    print("   %-24s %6d   %4.1f%%" % (k, len(por_fonte[k]),
                                      100.0 * len(por_fonte[k]) / len(todas)))
print()
print("por faixa de distancia do POI mais proximo")
ordem = ["ate 10 m", "10-50 m", "50-100 m", "100-200 m", "sem medida"]
for k in ordem:
    if k in por_faixa:
        print("   %-24s %6d   %4.1f%%" % (k, len(por_faixa[k]),
                                          100.0 * len(por_faixa[k]) / len(todas)))
print()
print("por ter imagem de alguma fonte")
print("   %-24s %6d   %4.1f%%" % ("COM imagem", len(com_img),
                                  100.0 * len(com_img) / len(todas)))
print("   %-24s %6d   %4.1f%%" % ("SEM imagem", len(sem_img),
                                  100.0 * len(sem_img) / len(todas)))
print()
print("fonte x imagem")
print("   %-12s %8s %8s" % ("fonte", "com img", "sem img"))
for k in sorted(por_fonte, key=lambda x: -len(por_fonte[x])):
    c = len(por_fonte[k] & com_img)
    s = len(por_fonte[k] & sem_img)
    print("   %-12s %8d %8d" % (k, c, s))
con.close()
