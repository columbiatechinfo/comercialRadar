# -*- coding: utf-8 -*-
"""
limites.py — Resolve BBOX + POLÍGONO de um município ou de uma UF a partir dos
limites oficiais IBGE, para delimitar e clipar a extração.

Fonte primária (offline): parquet de divisões territoriais — o MESMO da skill
`roteirizacao-divisao-territorial` (5.572 municípios, EPSG:4674, com PLACA,
COD_MUNICIPIO, NOME_MUNICIPIO, UF, AREA_KM2, TX_URBANIZACAO, CLASSE_URB).
Aponte o caminho via env DIVISOES_PARQUET ou argumento `parquet`.
Fallback (online, por nome): Nominatim/OSM — bbox + polígono.

API:
    municipio(cod=..., nome=..., uf=..., parquet=...) -> (bbox, poligono_gdf, meta)
    uf(sigla, parquet=...)                             -> GeoDataFrame de municípios
"""
import os, re, unicodedata
import geopandas as gpd

DEFAULT_PARQUET = os.environ.get(
    "DIVISOES_PARQUET",
    "/mnt/skills/user/roteirizacao-divisao-territorial/assets/divisoes_territoriais_BR.parquet")

META_COLS = ["PLACA", "COD_MUNICIPIO", "NOME_MUNICIPIO", "UF",
             "AREA_KM2", "TX_URBANIZACAO", "CLASSE_URB"]


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def carregar(parquet=None):
    p = parquet or DEFAULT_PARQUET
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"Base de limites não encontrada: {p}. Defina DIVISOES_PARQUET, passe --parquet, "
            f"ou use o modo --bbox (sem clip).")
    g = gpd.read_parquet(p)
    if g.crs is None:
        g = g.set_crs(4674)
    return g.to_crs(4326)


def _cod_str(serie):
    return serie.astype(str).str.replace(r"\.0$", "", regex=True)


def municipio(cod=None, nome=None, uf=None, parquet=None):
    """Resolve um município. Retorna (bbox=(W,S,E,N), poligono_gdf[geometry], meta:dict)."""
    g = carregar(parquet)
    if cod is not None:
        sel = g[_cod_str(g["COD_MUNICIPIO"]) == str(cod)]
    elif nome is not None:
        alvo = _norm(nome)
        m = g["NOME_MUNICIPIO"].map(_norm) == alvo
        if not m.any():                                   # tenta 'contém'
            m = g["NOME_MUNICIPIO"].map(_norm).str.contains(re.escape(alvo), na=False)
        if uf:
            m &= g["UF"].str.upper() == uf.upper()
        sel = g[m]
    else:
        raise ValueError("Informe cod (COD_MUNICIPIO) ou nome (+uf).")
    if len(sel) == 0:
        raise ValueError("Município não encontrado.")
    if len(sel) > 1:
        ops = sel.apply(lambda r: f"{r['NOME_MUNICIPIO']}/{r['UF']} (cod {r['COD_MUNICIPIO']})", axis=1).tolist()
        raise ValueError("Ambíguo — especifique a UF ou o código. Candidatos: " + "; ".join(ops[:8]))
    row = sel.iloc[0]
    W, S, E, N = sel.total_bounds
    meta = {c: row[c] for c in META_COLS if c in sel.columns}
    return (float(W), float(S), float(E), float(N)), sel[["geometry"]].copy(), meta


def uf(sigla, parquet=None):
    """Retorna o GeoDataFrame de todos os municípios da UF (geometria + metadados)."""
    g = carregar(parquet)
    sel = g[g["UF"].str.upper() == sigla.upper()].copy()
    if len(sel) == 0:
        raise ValueError(f"UF '{sigla}' sem municípios na base.")
    return sel.reset_index(drop=True)


def municipio_nominatim(nome, uf=None):
    """Fallback online: bbox + polígono via Nominatim (sem a base IBGE)."""
    import urllib.request, json
    q = f"{nome}, {uf}, Brazil" if uf else f"{nome}, Brazil"
    url = ("https://nominatim.openstreetmap.org/search?q=" + urllib.parse.quote(q) +
           "&format=json&limit=1&polygon_geojson=1")
    req = urllib.request.Request(url, headers={"User-Agent": "a2l-extracao-poi/1.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=60).read())[0]
    S, N, W, E = [float(x) for x in d["boundingbox"]]
    poly = gpd.GeoDataFrame.from_features(
        [{"type": "Feature", "properties": {}, "geometry": d["geojson"]}], crs=4326)
    meta = {"NOME_MUNICIPIO": nome, "UF": (uf or "")}
    return (W, S, E, N), poly[["geometry"]], meta
