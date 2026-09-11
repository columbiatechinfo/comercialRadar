# -*- coding: utf-8 -*-
"""carregar_bairro_estadual.py — o bairro das tres bases estaduais, do Parquet para o banco.

POR QUE ISTO EXISTE. A extracao estadual entrega o bairro de cada ponto no
`poi_padronizado_*.parquet` (coluna `bairro`: OSM addr:suburb, ou a leitura do
endereco), e ele nunca entrou no banco: `osm_data`, `overture_data` e
`foursquare_data` guardavam categoria e identificador, e nada de endereco.
Apontado pelo dono do produto em 11/09/2026.

O QUE CADA UMA TRAZ, medido em Canoas no mesmo dia:

    OSM          347 de  2.169 com bairro de verdade (Niteroi, Mathias Velho...)
    Foursquare 10.373 de 14.300 com o campo preenchido — 10.003 deles "Canoas":
                 o Foursquare poe a CIDADE no campo
    Overture       0 de 15.167

GUARDA-SE O QUE A FONTE DISSE, inclusive o "Canoas" do Foursquare. Quem sabe
que aquilo e o nome da cidade, e nao um bairro, e o resolvedor
(`resolver_logradouro.limpar_bairro`) — e e ele que decide consultar a
coordenada. Apagar aqui esconderia o que a fonte fez.

A LIGACAO E PELO `cluster_id`, que as tres tabelas guardam justamente para
voltar ao Parquet (ver `origem_estadual.py`).

    python carregar_bairro_estadual.py --uf RS            # ensaio
    python carregar_bairro_estadual.py --uf RS --aplicar
"""
import argparse
import glob
import time
from collections import Counter

import pandas as pd
from psycopg2.extras import execute_values

import base_comum as bc

PADRAO = "/app/dados_externos/estadual/%s/saida/poi_padronizado_*.parquet"
TABELAS = ("osm_data", "overture_data", "foursquare_data")


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--uf", default="RS")
    p.add_argument("--arquivo", default="")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    arq = a.arquivo or (sorted(glob.glob(PADRAO % a.uf.upper())) or [""])[-1]
    if not arq:
        _log("sem Parquet da extracao estadual para %s" % a.uf)
        return 1
    df = pd.read_parquet(arq, columns=["cluster_id", "fonte", "bairro"])
    df["bairro"] = df["bairro"].fillna("").astype(str).str.strip()
    df = df[df["bairro"] != ""]
    bairro_de = dict(zip(df["cluster_id"].astype(str), df["bairro"]))
    _log("%s: %d pontos com bairro no Parquet" % (arq, len(bairro_de)))

    con = bc.conectar()
    cur = con.cursor()
    total = 0
    for t in TABELAS:
        cur.execute("select poi_id, cluster_id from radar_comercial.%s" % t)
        pares = [(pid, bairro_de[str(cl)]) for pid, cl in cur.fetchall()
                 if str(cl) in bairro_de]
        top = Counter(b.upper() for _, b in pares).most_common(3)
        _log("   %-16s %6d com bairro · mais comuns: %s" % (t, len(pares), top))
        if not a.aplicar or not pares:
            continue
        execute_values(cur, """
            update radar_comercial.%s d set bairro = v.bairro
              from (values %%s) as v(poi_id, bairro)
             where d.poi_id = v.poi_id::bigint
               and d.bairro is distinct from v.bairro
        """ % t, pares, page_size=2000)
        con.commit()
        cur.execute("select count(bairro) from radar_comercial.%s" % t)
        n = int(cur.fetchone()[0] or 0)
        _log("   %-16s o banco tem %d com bairro (pedidos %d)" % (t, n, len(pares)))
        total += n
    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
