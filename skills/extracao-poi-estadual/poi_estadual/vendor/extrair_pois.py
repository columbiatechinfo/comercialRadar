#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extrair_pois.py — Extração MULTIFONTE de pontos estratégicos (POIs) por bbox,
                  PRESERVANDO TODAS AS COLUNAS de cada fonte (zero perda).

Saída por linha = colunas HARMONIZADAS (comuns, p/ tratamento/dedup) +
TODAS as colunas NATIVAS da fonte, achatadas e com namespace:
  ov.*  (Overture)   |   fsq.*  (Foursquare)   |   osm.*  (OpenStreetMap)
Listas viram string ';'-separada (escalares) ou JSON (lista de objetos);
structs aninhados viram colunas pai.filho. Nada é descartado.

Fontes:
  overture : Overture Places (S3, anônimo, via CLI overturemaps)
  osm      : OpenStreetMap (Overpass) — amenity/shop/office/leisure/tourism c/ nome
  fsq      : Foursquare OS Places (Hugging Face hf://, com HF_TOKEN)

Uso:
  python extrair_pois.py --bbox W S E N --fonte overture --out raw_ov.parquet
  python extrair_pois.py --bbox W S E N --fonte all      --out raw_all.parquet
"""
import argparse, json, os, subprocess, tempfile, urllib.parse, urllib.request
import pandas as pd

COMUNS = ["fonte", "id_fonte", "nome", "lat", "lon", "categoria_orig", "endereco_raw",
          "bairro", "cep", "telefone", "site", "email", "instagram", "marca",
          "confianca", "status", "data_atualizacao",
          # v3.0.0 — harmonizadas no ADAPTADOR de cada fonte, não por alargamento da
          # projeção: `localidade_fonte` separa locality de bairro (no Overture o
          # locality é o município em ~96% das linhas) e `categoria_hier` leva a
          # taxonomia hierárquica até o normalize, onde a tradução acontece.
          "localidade_fonte", "categoria_hier"]


# ---------- helpers de extração de campo (p/ colunas comuns) ----------
def _g(v, *ks):
    for k in ks:
        if isinstance(v, dict):
            v = v.get(k)
        elif hasattr(v, "as_py"):
            v = v.as_py(); v = v.get(k) if isinstance(v, dict) else None
        else:
            return None
    return v


def _first(v, key=None):
    if hasattr(v, "__len__") and not isinstance(v, (str, bytes, dict)) and len(v):
        return _g(v[0], key) if key else v[0]
    return None


# ---------- achatamento genérico (preserva TODOS os campos) ----------
def _py(v):
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None                                  # geometria/binário: lat/lon já vão nas comuns
    if hasattr(v, "as_py"):
        try:
            return _py(v.as_py())
        except Exception:
            pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


def _is_list(v):
    return (hasattr(v, "__len__") and not isinstance(v, (str, bytes, dict))
            and not hasattr(v, "keys"))


def _deep(x):
    x = x.as_py() if hasattr(x, "as_py") else x
    if isinstance(x, dict):
        return {k: _deep(v) for k, v in x.items()}
    if _is_list(x):
        return [_deep(i) for i in x]
    return _py(x)


def flatten(obj, prefix, out):
    """Achata obj em out[] sob 'prefix' (sem perder nada)."""
    obj = obj.as_py() if hasattr(obj, "as_py") else obj
    key = prefix.rstrip(".")
    if isinstance(obj, dict):
        if not obj:
            out[key] = None
        for k, v in obj.items():
            flatten(v, f"{prefix}{k}.", out)
    elif _is_list(obj):
        lst = list(obj)
        if len(lst) == 0:
            out[key] = None
        elif not any(isinstance(x, dict) or _is_list(x) for x in lst):
            out[key] = ";".join("" if x is None else str(_py(x)) for x in lst)
        else:
            out[key] = json.dumps([_deep(x) for x in lst], ensure_ascii=False, default=str)
    else:
        out[key] = _py(obj)


def _native(row, cols, ns):
    nat = {}
    for c in cols:
        flatten(row[c], f"{ns}.{c}.", nat)
    return nat


# --------------------------------------------------------------- OVERTURE ----
def extrair_overture(W, S, E, N):
    import shapely
    tmp = tempfile.mktemp(suffix=".geoparquet")
    subprocess.run(["overturemaps", "download", f"--bbox={W},{S},{E},{N}",
                    "-f", "geoparquet", "--type=place", "-o", tmp],
                   check=True, capture_output=True)
    g = pd.read_parquet(tmp)
    geom = shapely.from_wkb(g["geometry"].values)
    cols = [c for c in g.columns if c != "geometry"]
    rows = []
    for i, r in g.reset_index(drop=True).iterrows():
        a0 = _first(r.get("addresses"))
        comum = dict(fonte="overture", id_fonte=r.get("id"),
                     nome=_g(r.get("names"), "primary"),
                     lat=float(shapely.get_y(geom[i])), lon=float(shapely.get_x(geom[i])),
                     categoria_orig=_g(r.get("categories"), "primary"),
                     endereco_raw=_g(a0, "freeform"), bairro=_g(a0, "locality"),
                     cep=_g(a0, "postcode"),
                     telefone=_first(r.get("phones")), site=_first(r.get("websites")),
                     email=_first(r.get("emails")), instagram=_first(r.get("socials")),
                     marca=_g(r.get("brand"), "names", "primary"),
                     confianca=float(r["confidence"]) if pd.notna(r.get("confidence")) else None,
                     status=r.get("operating_status"),
                     data_atualizacao=_py(r.get("update_time")))
        rows.append({**comum, **_native(r, cols, "ov")})
    os.remove(tmp)
    return pd.DataFrame(rows)


# -------------------------------------------------------------------- OSM ----
OSM_EP = ["https://overpass-api.de/api/interpreter",
          "https://overpass.kumi.systems/api/interpreter"]

def extrair_osm(W, S, E, N):
    q = (f'[out:json][timeout:90];('
         f'nwr["amenity"]["name"]({S},{W},{N},{E});'
         f'nwr["shop"]["name"]({S},{W},{N},{E});'
         f'nwr["office"]["name"]({S},{W},{N},{E});'
         f'nwr["leisure"]["name"]({S},{W},{N},{E});'
         f'nwr["tourism"]["name"]({S},{W},{N},{E}););out center tags;')
    data = None
    for ep in OSM_EP:
        try:
            req = urllib.request.Request(ep, data=urllib.parse.urlencode({"data": q}).encode(),
                                         headers={"User-Agent": "a2l-pontos-estrategicos/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode()); break
        except Exception as ex:
            print(f"   overpass {ep[:30]}… falhou ({type(ex).__name__})")
    if not data:
        return pd.DataFrame(columns=COMUNS)
    rows = []
    for el in data.get("elements", []):
        tg = el.get("tags", {})
        lat = el.get("lat") or _g(el.get("center"), "lat")
        lon = el.get("lon") or _g(el.get("center"), "lon")
        if lat is None or lon is None:
            continue
        cat = (tg.get("amenity") or tg.get("shop") or tg.get("office")
               or tg.get("leisure") or tg.get("tourism"))
        end = " ".join(x for x in [tg.get("addr:street"), tg.get("addr:housenumber")] if x) or None
        comum = dict(fonte="osm", id_fonte=f"{el.get('type')}/{el.get('id')}",
                     nome=tg.get("name"), lat=float(lat), lon=float(lon),
                     categoria_orig=cat, endereco_raw=end,
                     bairro=tg.get("addr:suburb") or tg.get("addr:neighbourhood"),
                     cep=tg.get("addr:postcode"),
                     telefone=tg.get("phone") or tg.get("contact:phone"),
                     site=tg.get("website") or tg.get("contact:website"),
                     email=tg.get("email") or tg.get("contact:email"),
                     instagram=tg.get("contact:instagram"), marca=tg.get("brand"),
                     confianca=None, status=None, data_atualizacao=el.get("timestamp"))
        nat = {}
        flatten({"type": el.get("type"), "id": el.get("id")}, "osm.", nat)
        flatten(tg, "osm.tags.", nat)                # TODAS as tags viram colunas
        rows.append({**comum, **nat})
    return pd.DataFrame(rows)


# -------------------------------------------------------------------- FSQ ----
def extrair_fsq(W, S, E, N, release="latest"):
    """Foursquare OS Places via Hugging Face (hf://) com HF_TOKEN. SELECT * (preserva tudo)."""
    import duckdb, os as _os
    token = (_os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")
             or _os.environ.get("HUGGINGFACE_HUB_TOKEN"))
    if not token:
        raise RuntimeError(
            "FSQ exige token gratuito do Hugging Face. Peça acesso em "
            "https://huggingface.co/datasets/foursquare/fsq-os-places e exporte "
            "HF_TOKEN=hf_xxx (ou use --hf-token). Overture e OSM funcionam sem token.")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token}');")
    base = "hf://datasets/foursquare/fsq-os-places/release"
    if release == "latest":
        rows = con.execute(
            f"SELECT DISTINCT regexp_extract(file,'dt=([0-9-]+)',1) AS dt "
            f"FROM glob('{base}/dt=*/places/parquet/*.parquet') "
            f"WHERE dt <> '' ORDER BY dt DESC LIMIT 1").fetchall()
        if not rows:
            raise RuntimeError("não listei releases FSQ no HF — verifique se seu acesso foi aprovado.")
        rel = rows[0][0]
    else:
        rel = release
    path = f"{base}/dt={rel}/places/parquet/*.parquet"
    print(f"   FSQ (HF) release dt={rel}")
    df = con.execute(f"""
        SELECT * EXCLUDE (geom)
        FROM read_parquet('{path}')
        WHERE longitude BETWEEN {W} AND {E} AND latitude BETWEEN {S} AND {N}
          AND name IS NOT NULL
    """).df()

    def full_label(lbls):
        if lbls is None or (hasattr(lbls, "__len__") and len(lbls) == 0):
            return None
        s = lbls[0] if hasattr(lbls, "__len__") and not isinstance(lbls, str) else lbls
        return str(s).strip()
    cols = list(df.columns)
    rows = []
    for _, r in df.iterrows():
        comum = dict(fonte="fsq", id_fonte=r.get("fsq_place_id"), nome=r.get("name"),
                     lat=r.get("latitude"), lon=r.get("longitude"),
                     categoria_orig=full_label(r.get("fsq_category_labels")),
                     endereco_raw=r.get("address"), bairro=r.get("locality"),
                     cep=r.get("postcode"),
                     telefone=r.get("tel"), site=r.get("website"), email=r.get("email"),
                     instagram=r.get("instagram"), marca=None, confianca=None,
                     status="fechado" if pd.notna(r.get("date_closed")) else None,
                     data_atualizacao=str(r.get("date_refreshed")) if pd.notna(r.get("date_refreshed")) else None)
        rows.append({**comum, **_native(r, cols, "fsq")})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--fonte", choices=["overture", "osm", "fsq", "all"], default="overture")
    ap.add_argument("--out", default="raw_pois.parquet")
    ap.add_argument("--fsq-release", default="latest")
    ap.add_argument("--hf-token", default=None, help="token HF p/ FSQ (ou env HF_TOKEN)")
    a = ap.parse_args()
    if a.hf_token:
        os.environ["HF_TOKEN"] = a.hf_token
    W, S, E, N = a.bbox
    fontes = ["overture", "osm", "fsq"] if a.fonte == "all" else [a.fonte]
    parts = []
    for f in fontes:
        print(f"[{f}] extraindo bbox=({W},{S},{E},{N})…")
        try:
            d = {"overture": extrair_overture, "osm": extrair_osm,
                 "fsq": lambda *x: extrair_fsq(*x, release=a.fsq_release)}[f](W, S, E, N)
            print(f"[{f}] {len(d)} POIs | {len(d.columns)} colunas")
            parts.append(d)
        except Exception as ex:
            print(f"[{f}] FALHOU: {type(ex).__name__}: {ex}")
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COMUNS)
    out.to_parquet(a.out, index=False)
    print(f"[ok] {len(out)} POIs | {len(out.columns)} colunas (união sem perda) -> {a.out}")


if __name__ == "__main__":
    main()
