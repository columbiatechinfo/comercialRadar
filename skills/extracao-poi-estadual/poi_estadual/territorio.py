# -*- coding: utf-8 -*-
"""Consolidacao do bruto (`raw`) e clip/atribuicao de municipio (`territory`).

Principio fetch-once: cada fonte foi buscada UMA vez no bbox da UF; aqui o recorte
e set-based (ponto-em-poligono em lote), nao um loop por municipio.

O sjoin roda em chunks porque um `sjoin` unico sobre a base estadual inteira
materializa o produto ponto x poligono de uma vez. Cada chunk e um artefato
retomavel.

Fronteira: por padrao os poligonos NAO sao simplificados (`--simplificar-graus 0`).
Simplificar move o limite municipal e pode trocar o municipio de um ponto proximo
a divisa; fica como opcao de performance, com o efeito declarado.
"""
import glob
import math
import os
import time

import geopandas as gpd
import pandas as pd

from .config import salvar_atomico
from .vendor import extrair_pois as ep

TERR = ["PLACA", "COD_MUNICIPIO", "NOME_MUNICIPIO", "UF", "AREA_KM2",
        "TX_URBANIZACAO", "CLASSE_URB"]

# v3.0.0 — descarte deixa de ser so um numero no funil: cada linha eliminada e
# gravada com motivo. Os 1.261 pontos descartados no recorte de Canoas e os 506 de
# Santa Maria existiam apenas como contagem, sem como revisar.
REJEITADOS = ["id_fonte", "fonte", "nome", "lat", "lon", "etapa", "motivo", "valor"]


def _rejeitados(d, etapa, motivo):
    if d is None or not len(d):
        return pd.DataFrame(columns=REJEITADOS)
    out = pd.DataFrame({c: (d[c] if c in d.columns else None)
                        for c in ("id_fonte", "fonte", "nome", "lat", "lon")})
    out["etapa"], out["motivo"], out["valor"] = etapa, motivo, None
    return out[REJEITADOS]


def _fontes_partes(cfg, man):
    p = []
    if "overture" in cfg.fontes:
        p += sorted(glob.glob(os.path.join(
            cfg.dir_colecao("overture", *(man.colecao("overture") or ("x", "y")), "parts"), "ov_*.parquet")))
    if "osm" in cfg.fontes:
        from .osm import _dir as _dir_osm
        p += [os.path.join(_dir_osm(cfg, man), n)
              for n in ("nodes.parquet", "ways.parquet", "relations.parquet")]
    if "fsq" in cfg.fontes:
        p += [os.path.join(cfg.dir_colecao("fsq", *(man.colecao("fsq") or ("x", "y"))), "fsq.parquet")]
    return [x for x in p if os.path.exists(x)]


# colunas nativas de onde `categoria_hier` pode ser derivada em cache antigo
_HIER_NATIVAS = ["ov.taxonomy.hierarchy", "ov.categories.hierarchy",
                 "ov.categories.alternate", "ov.basic_category"]


def _ler_comuns(path):
    """Projecao de coluna: le so o que precisa. Nunca materializar o OSM largo.

    Compatibilidade v2 -> v3.0.0: parquet de coleta antigo nao tem
    `localidade_fonte` nem `categoria_hier`. Em vez de exigir re-fetch, as duas sao
    DERIVADAS aqui — `locality` do Overture estava em `bairro`, e a hierarquia ja
    viaja nas colunas nativas `ov.*`. Le-se a lista de colunas do arquivo antes,
    para nao cair no caminho de ler o OSM inteiro."""
    try:
        import pyarrow.parquet as pq
        nomes = set(pq.ParquetFile(path).schema.names)
    except Exception:                                  # noqa: BLE001
        nomes = None
    if nomes is not None:
        cols = [c for c in ep.COMUNS if c in nomes]
        extras = [c for c in _HIER_NATIVAS if c in nomes]
        if not cols:
            return pd.DataFrame(columns=ep.COMUNS)
        try:
            d = pd.read_parquet(path, columns=cols + extras)
        except (ValueError, KeyError, OSError):
            d = None
    else:
        d = None
    if d is None:
        try:
            d = pd.read_parquet(path)
        except OSError:
            return pd.DataFrame(columns=ep.COMUNS)
    if not len(d):
        return pd.DataFrame(columns=ep.COMUNS)
    return _compat_v3(d)


def _compat_v3(d):
    fonte = d["fonte"].iloc[0] if "fonte" in d.columns and len(d) else None
    if "localidade_fonte" not in d.columns:
        # no Overture, o `locality` era gravado em `bairro` ate a v2.0.0
        d["localidade_fonte"] = d["bairro"] if (fonte == "overture" and "bairro" in d.columns) \
            else None
        if fonte == "overture" and "bairro" in d.columns:
            d["bairro"] = None
    if "categoria_hier" not in d.columns:
        col = next((c for c in _HIER_NATIVAS if c in d.columns), None)
        d["categoria_hier"] = d[col] if col else None
    for c in ep.COMUNS:
        if c not in d.columns:
            d[c] = None
    return d[ep.COMUNS]


def consolidar(cfg, man, bbox):
    """Etapa `raw`: junta as fontes no esquema COMUNS e aplica gate de coordenada."""
    man.iniciar("raw")
    dest = os.path.join(cfg.dir_proc("raw"), "raw_narrow.parquet")
    if os.path.exists(dest) and man.reutilizavel("raw"):
        n = int(pd.read_parquet(dest, columns=["id_fonte"]).shape[0])
        print("RAW: reaproveitado (%d linhas)" % n)
        return n

    partes = _fontes_partes(cfg, man)
    if not partes:
        raise RuntimeError("nenhuma parte de fonte encontrada — rode a etapa fetch")
    frames = [d for d in (_ler_comuns(p) for p in partes) if len(d)]
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=ep.COMUNS)
    n0 = len(raw)

    raw["lat"] = pd.to_numeric(raw["lat"], errors="coerce")
    raw["lon"] = pd.to_numeric(raw["lon"], errors="coerce")
    rej = []

    n1 = len(raw)
    mask = raw["lat"].notna() & raw["lon"].notna()
    rej.append(_rejeitados(raw[~mask], "raw", "lat/lon nula ou nao numerica"))
    raw = raw[mask]
    man.funil("raw.coordenada", n1, len(raw), "lat/lon nula ou nao numerica")

    n2 = len(raw)
    mask = (raw["lat"] != 0) | (raw["lon"] != 0)
    rej.append(_rejeitados(raw[~mask], "raw", "coordenada (0,0)"))
    raw = raw[mask]
    man.funil("raw.zero", n2, len(raw), "coordenada (0,0)")

    # v3.3.0 — membro de relation ja representado pela propria relation. Supressao
    # por contencao + identidade; contabilizada, nunca silenciosa.
    if "osm" in cfg.fontes:
        from .osm import suprimidos
        sup = suprimidos(cfg, man)
        if len(sup):
            alvo_sup = set(sup["id_fonte"].astype(str))
            m = raw["id_fonte"].astype(str).isin(alvo_sup)
            rej.append(_rejeitados(raw[m], "raw", "membro de relation ja representado"))
            n_sup = len(raw)
            raw = raw[~m]
            man.funil("raw.osm_relation_membro", n_sup, len(raw),
                      "membro de relation ja representado pela relation")

    W, S, E, N = bbox
    n3 = len(raw)
    mask = (raw.lon >= W) & (raw.lon <= E) & (raw.lat >= S) & (raw.lat <= N)
    rej.append(_rejeitados(raw[~mask], "raw", "fora do bbox da UF alvo"))
    raw = raw[mask]
    man.funil("raw.bbox", n3, len(raw), "fora do bbox da UF alvo")

    salvar_atomico(pd.concat(rej, ignore_index=True),
                   os.path.join(cfg.dir_proc("raw"), "r_raw.parquet"))

    raw = raw.sort_values(["fonte", "id_fonte"], kind="stable").reset_index(drop=True)
    salvar_atomico(raw, dest)
    man.concluir("raw", entrada=n0, saida=len(raw), partes=len(partes))
    print("RAW: %d -> %d linhas (%d partes)" % (n0, len(raw), len(partes)))
    return len(raw)


def clipar(cfg, man, alvo):
    """Etapa `territory`: ponto-em-poligono em chunks + atributos IBGE."""
    man.iniciar("territory")
    kept = os.path.join(cfg.dir_proc("territory"), "kept.parquet")
    ids = os.path.join(cfg.dir_proc("territory"), "keep_ids.parquet")
    if os.path.exists(kept) and man.reutilizavel("territory"):
        n = int(pd.read_parquet(kept, columns=["id_fonte"]).shape[0])
        print("TERRITORY: reaproveitado (%d linhas)" % n)
        return n

    raw = pd.read_parquet(os.path.join(cfg.dir_proc("raw"), "raw_narrow.parquet"))
    poli = alvo[[c for c in TERR if c in alvo.columns] + ["geometry"]].to_crs(4326)
    partes_dir = cfg.dir_proc("territory", "kept_parts")
    nb = max(1, math.ceil(len(raw) / cfg.clip_chunk))
    t0 = time.time()
    feitos = 0
    for b in range(nb):
        outp = os.path.join(partes_dir, "k_%05d.parquet" % b)
        if os.path.exists(outp):
            continue
        if cfg.budget_s and time.time() - t0 > cfg.budget_s:
            man.parcial("territory", blocos_feitos=len(glob.glob(
                os.path.join(partes_dir, "k_*.parquet"))), blocos_total=nb)
            print("TERRITORY: parcial %d/%d blocos (budget) — reexecute" % (feitos, nb))
            return None
        ch = raw.iloc[b * cfg.clip_chunk:(b + 1) * cfg.clip_chunk]
        pts = gpd.GeoDataFrame(ch, geometry=gpd.points_from_xy(ch.lon, ch.lat), crs=4326)
        k = gpd.sjoin(pts, poli, predicate="within", how="inner")
        k = pd.DataFrame(k.drop(columns=[c for c in ("geometry", "index_right") if c in k.columns]))
        salvar_atomico(k, outp)
        feitos += 1

    partes = sorted(glob.glob(os.path.join(partes_dir, "k_*.parquet")))
    if len(partes) != nb:
        man.parcial("territory", blocos_feitos=len(partes), blocos_total=nb)
        raise RuntimeError("clip incompleto: %d/%d blocos" % (len(partes), nb))
    df = pd.concat([pd.read_parquet(p) for p in partes], ignore_index=True)
    df["COD_MUNICIPIO"] = df["COD_MUNICIPIO"].astype(str).str.replace(r"\.0$", "", regex=True)
    fora = raw[~raw["id_fonte"].astype(str).isin(set(df["id_fonte"].astype(str)))]
    salvar_atomico(_rejeitados(fora, "territory", "ponto fora dos municipios alvo"),
                   os.path.join(cfg.dir_proc("territory"), "r_territory.parquet"))
    salvar_atomico(df, kept)
    salvar_atomico(df[["id_fonte"]].drop_duplicates(), ids)
    man.funil("territory.clip", len(raw), len(df), "ponto fora dos municipios alvo")
    man.concluir("territory", entrada=len(raw), saida=len(df),
                 municipios=int(df["COD_MUNICIPIO"].nunique()), blocos=nb)
    print("TERRITORY: %d -> %d pontos em %d municipios"
          % (len(raw), len(df), df["COD_MUNICIPIO"].nunique()))
    return len(df)
