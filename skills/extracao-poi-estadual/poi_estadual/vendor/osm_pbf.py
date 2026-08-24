#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osm_pbf.py — OSM em escala ESTADUAL via dump .pbf (Geofabrik) processado LOCALMENTE
com DuckDB + extensão spatial (ST_ReadOSM). Evita o gargalo/ban do Overpass: baixa o
.pbf da macrorregião uma vez e extrai os POIs em minutos. Overpass fica só p/ ÁREA/CIDADE.

Mapeia a UF -> macrorregião da Geofabrik (o Brasil é dividido em 5 regiões, não por UF).
Extrai POI NÓS (amenity/shop/office/leisure/tourism + name) e CENTROIDE de WAYS POI
(resolvendo refs->nós só dos ways de interesse, via SEMI JOIN, p/ limitar memória),
achata TODAS as tags como osm.* (igual ao extrator Overpass) e clipa no polígono do estado.

Uso programático:
  import osm_pbf
  df = osm_pbf.extrair_uf("GO", poligono=gdf_uf)        # baixa (cacheia) + extrai + clipa
  df = osm_pbf.extrair_pbf("/path/uf.osm.pbf", poligono=gdf_uf)
"""
import os, sys, urllib.request, urllib.error
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extrair_pois import COMUNS, flatten

GEOFABRIK = "https://download.geofabrik.de/south-america/brazil/"
REGIAO_UF = {
    "norte":        {"AC", "AP", "AM", "PA", "RO", "RR", "TO"},
    "nordeste":     {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"},
    "centro-oeste": {"DF", "GO", "MT", "MS"},
    "sudeste":      {"ES", "MG", "RJ", "SP"},
    "sul":          {"PR", "RS", "SC"},
}
_UF2REG = {uf: reg for reg, ufs in REGIAO_UF.items() for uf in ufs}

# chaves OSM que caracterizam um POI (espelha o extrator Overpass)
POI_KEYS = ["amenity", "shop", "office", "leisure", "tourism"]

# v3.0.0 — predicado AMPLIADO. A auditoria de Canoas mediu que o predicado clássico
# coleta 47% dos nós com cara de POI: ficavam de fora as estações do Trensurb, o
# Terminal de Integração e plantas industriais. Chaves de valor ABERTO entram
# inteiras; chaves ruidosas (`railway` pega chave de manobra, `man_made` pega poste)
# entram só com a lista de valores que é POI de verdade.
POI_KEYS_ABERTAS = POI_KEYS + ["healthcare", "craft", "historic", "government", "military"]
POI_VALORES = {
    "railway": ["station", "halt", "tram_stop", "subway_entrance"],
    "public_transport": ["station"],
    "aeroway": ["aerodrome", "terminal", "hangar", "heliport"],
    "man_made": ["works", "water_works", "wastewater_plant", "water_tower",
                 "storage_tank", "pumping_station", "silo"],
    "building": ["commercial", "retail", "industrial", "warehouse", "works",
                 "hospital", "school", "supermarket", "office"],
}
CHAVES_CATEGORIA = POI_KEYS_ABERTAS + list(POI_VALORES)
CACHE_DIR = os.environ.get("OSM_PBF_DIR", os.path.expanduser("~/.cache/osm_pbf"))


def regiao_da_uf(uf):
    uf = str(uf).strip().upper()
    if uf not in _UF2REG:
        raise ValueError(f"UF inválida: {uf}")
    return _UF2REG[uf]


def url_regiao(uf):
    return f"{GEOFABRIK}{regiao_da_uf(uf)}-latest.osm.pbf"


def baixar_pbf(uf, dest_dir=CACHE_DIR, force=False):
    """Baixa o .pbf da macrorregião da UF (cacheado). Retorna o caminho local."""
    os.makedirs(dest_dir, exist_ok=True)
    reg = regiao_da_uf(uf)
    path = os.path.join(dest_dir, f"{reg}-latest.osm.pbf")
    if os.path.exists(path) and not force and os.path.getsize(path) > 0:
        return path
    url = url_regiao(uf)
    tmp = path + ".part"
    print(f"   baixando {url} -> {path}")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        tot = int(r.headers.get("Content-Length", 0)); got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk); got += len(chunk)
            if tot:
                print(f"\r   {got/1e6:,.0f}/{tot/1e6:,.0f} MB", end="", flush=True)
    print()
    os.replace(tmp, path)
    return path


def _con():
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    mem = os.environ.get("DUCKDB_MEMORY_LIMIT")
    if mem:
        con.execute(f"SET memory_limit='{mem}';")
    tmp = os.environ.get("DUCKDB_TEMP_DIR")
    if tmp:
        con.execute(f"SET temp_directory='{tmp}';")
    return con


def _pred(alias="tags", ampliado=False):
    """Predicado SQL de POI. `ampliado=False` reproduz o comportamento clássico."""
    if not ampliado:
        return " OR ".join(f"map_extract({alias},'{k}') <> []" for k in POI_KEYS)
    cond = [f"map_extract({alias},'{k}') <> []" for k in POI_KEYS_ABERTAS]
    for k, vals in POI_VALORES.items():
        lista = ",".join("'%s'" % v for v in vals)
        cond.append("coalesce(list_contains([%s], list_extract(map_extract(%s,'%s'),1)), false)"
                    % (lista, alias, k))
    return " OR ".join(cond)


def categoria_de(tags, ampliado=False):
    """Valor que representa a categoria do POI, na ordem de precedência das chaves."""
    chaves = CHAVES_CATEGORIA if ampliado else POI_KEYS
    return next((tags[k] for k in chaves if tags.get(k)), None)


def extrair_pbf(pbf_path, poligono=None, incluir_ways=True):
    """Lê POIs de um .pbf via DuckDB ST_ReadOSM. Retorna DataFrame (COMUNS + osm.* nativas)."""
    con = _con()
    has_name = "map_extract(tags,'name') <> []"
    sql_nodes = f"""
        SELECT id, lat, lon, tags
        FROM ST_ReadOSM('{pbf_path}')
        WHERE kind='node' AND {has_name} AND ({_pred('tags')})
    """
    df_nodes = con.execute(sql_nodes).fetchdf()
    df_nodes["otype"] = "node"

    if incluir_ways:
        sql_ways = f"""
            WITH src AS (SELECT kind, id, lat, lon, tags, refs FROM ST_ReadOSM('{pbf_path}')),
            pw AS (SELECT id, tags, refs FROM src
                   WHERE kind='way' AND {has_name} AND ({_pred('tags')})),
            need AS (SELECT DISTINCT UNNEST(refs) AS nid FROM pw),
            nc AS (SELECT s.id, s.lat, s.lon FROM src s SEMI JOIN need ON s.id = need.nid
                   WHERE s.kind='node')
            SELECT pw.id AS id, AVG(nc.lat) AS lat, AVG(nc.lon) AS lon, ANY_VALUE(pw.tags) AS tags
            FROM pw, UNNEST(pw.refs) AS u(nid)
            JOIN nc ON nc.id = u.nid
            GROUP BY pw.id
        """
        df_ways = con.execute(sql_ways).fetchdf()
        df_ways["otype"] = "way"
        df = pd.concat([df_nodes, df_ways], ignore_index=True)
    else:
        df = df_nodes
    con.close()
    if len(df) == 0:
        return pd.DataFrame(columns=COMUNS)

    rows = []
    for r in df.itertuples(index=False):
        tg = dict(r.tags) if r.tags is not None else {}
        if r.lat is None or r.lon is None:
            continue
        cat = categoria_de(tg, ampliado=True)
        end = " ".join(v for v in [tg.get("addr:street"), tg.get("addr:housenumber")] if v) or None
        comum = dict(fonte="osm", id_fonte=f"{r.otype}/{r.id}", nome=tg.get("name"),
                     lat=float(r.lat), lon=float(r.lon), categoria_orig=cat, categoria_hier=None,
                     endereco_raw=end,
                     bairro=tg.get("addr:suburb") or tg.get("addr:neighbourhood"),
                     localidade_fonte=tg.get("addr:city"),
                     cep=tg.get("addr:postcode"),
                     telefone=tg.get("phone") or tg.get("contact:phone"),
                     site=tg.get("website") or tg.get("contact:website"),
                     email=tg.get("email") or tg.get("contact:email"),
                     instagram=tg.get("contact:instagram"), marca=tg.get("brand"),
                     confianca=None, status=None, data_atualizacao=None)
        nat = {}
        flatten({"type": r.otype, "id": r.id}, "osm.", nat)
        flatten(tg, "osm.tags.", nat)
        rows.append({**comum, **nat})
    out = pd.DataFrame(rows)
    if poligono is not None and len(out):
        out = _clip(out, poligono)
    return out


def _clip(df, poligono):
    import geopandas as gpd
    poly = poligono.to_crs(4326)[["geometry"]] if poligono.crs else poligono.set_crs(4674).to_crs(4326)[["geometry"]]
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)
    keep = gpd.sjoin(pts, poly, predicate="within", how="inner").drop(columns=["index_right"])
    return pd.DataFrame(keep.drop(columns="geometry"))


def extrair_uf(uf, poligono=None, dest_dir=CACHE_DIR, cache_parquet=None, incluir_ways=True):
    """Baixa o .pbf da região da UF (cacheado) e extrai POIs (clipando no polígono se houver)."""
    if cache_parquet and os.path.exists(cache_parquet):
        return pd.read_parquet(cache_parquet)
    pbf = baixar_pbf(uf, dest_dir=dest_dir)
    df = extrair_pbf(pbf, poligono=poligono, incluir_ways=incluir_ways)
    if cache_parquet and len(df):
        df.to_parquet(cache_parquet)
    return df


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf"); ap.add_argument("--uf"); ap.add_argument("--out")
    ap.add_argument("--no-ways", action="store_true")
    a = ap.parse_args()
    if a.pbf:
        d = extrair_pbf(a.pbf, incluir_ways=not a.no_ways)
    else:
        d = extrair_uf(a.uf, incluir_ways=not a.no_ways)
    print(f"{len(d):,} POIs | {len(d.columns)} colunas")
    if a.out:
        d.to_parquet(a.out) if a.out.endswith(".parquet") else d.to_csv(a.out, index=False)
