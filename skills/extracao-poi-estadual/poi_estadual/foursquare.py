# -*- coding: utf-8 -*-
"""Foursquare OS Places (Hugging Face) restrito ao bbox da UF.

Os shards do FSQ sao clusterizados por geografia: no RS, 3 de 100 concentram o
estado. O caminho e (1) `dist` — GROUP BY filename para achar os densos;
(2) `copy` — materializar por faixa de latitude com `COPY ... TO parquet`;
(3) `build` — montar o esquema COMUNS de forma vetorizada.

Duas regras que a v1 documentava mas o `rs_driver` violava:
  - NAO usar `name IS NOT NULL` no scan remoto. Forca leitura da coluna texto
    (~18s/shard contra ~1s com stats pruning) e ainda descarta POI valido sem nome.
  - NAO usar `iterrows()` sobre o resultado. Em base estadual e o gargalo.

Correcao v2 de memoria: `build` grava uma parte por faixa e concatena com
`union_by_name` em DuckDB, em vez do `pd.concat` de todas as faixas em pandas.

────────────────────────────────────────────────────────────────────────────
PATCH LOCAL — 25/08/2026, comercialRadar. NAO E DA SKILL DE ORIGEM.

`categoria_orig` estourava com "boolean value of NA is ambiguous" e derrubava
a etapa `fetch` inteira. Ver o comentario em `_primeira_categoria`, logo
acima de `_build`.

Isto e uma skill VENDORADA: uma atualizacao dela sobrescreve este arquivo.
Ao atualizar, conferir se a correcao continua necessaria — e, se sim,
reaplica-la ou reportar a montante. `tests/test_fsq_categoria.py` reprova se
o defeito voltar, entao a suite avisa antes de uma producao de horas morrer.
────────────────────────────────────────────────────────────────────────────
"""
import glob
import json
import os
import time

import numpy as np
import pandas as pd

from .config import ler_json_cache, salvar_json_atomico

BASE_HF = "hf://datasets/foursquare/fsq-os-places/release"

MAPA = {
    "id_fonte": "fsq_place_id", "nome": "name", "lat": "latitude", "lon": "longitude",
    "endereco_raw": "address", "bairro": "locality", "cep": "postcode",
    "telefone": "tel", "site": "website", "email": "email", "instagram": "instagram",
}


def _dir(cfg, man, *partes):
    col, wk = man.colecao("fsq")
    return cfg.dir_colecao("fsq", col or "sem_colecao", wk or "sem_work", *partes)


def listar_releases(cfg, release=None):
    """(release_mais_recente, lista_de_shards) — consulta a fonte, sem cache.

    Ate a v3.3.0 `_release()` devolvia `files.json` do disco e o pipeline nunca mais
    perguntava qual era a ultima release. Podia rodar em outubro servindo agosto."""
    con = _con(cfg)
    try:
        if release:
            # PINNED LITERAL: le exatamente `dt=<release>`. Nunca "a ultima".
            files = [r[0] for r in con.execute(
                "SELECT file FROM glob('%s/dt=%s/places/parquet/*.parquet') ORDER BY file"
                % (BASE_HF, release)).fetchall()]
            if not files:
                raise RuntimeError("release FSQ %s nao encontrada na fonte" % release)
            return release, files
        rel = con.execute(
            "SELECT DISTINCT regexp_extract(file,'dt=([0-9-]+)',1) dt "
            "FROM glob('%s/dt=*/places/parquet/*.parquet') WHERE dt<>'' ORDER BY dt DESC LIMIT 1"
            % BASE_HF).fetchone()[0]
        files = [r[0] for r in con.execute(
            "SELECT file FROM glob('%s/dt=%s/places/parquet/*.parquet') ORDER BY file"
            % (BASE_HF, rel)).fetchall()]
    finally:
        con.close()
    return rel, files


def _con(cfg):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET threads=%d;" % cfg.threads)
    con.execute("SET memory_limit='%s';" % cfg.duckdb_memory)
    con.execute("SET temp_directory='%s';" % cfg.dir_fonte("fsq", "ddtmp"))
    con.execute("CREATE SECRET hf (TYPE huggingface, TOKEN '%s');" % os.environ["HF_TOKEN"])
    return con


def gate(cfg):
    """Prova de acesso ao dataset ANTES de qualquer download pesado.

    O `HF_TOKEN` e conferido na config, mas o gate real (dataset gated -> HTTP 401)
    so aparecia no meio do `fetch`, depois de o OSM ja ter baixado ~421 MB. Uma
    leitura de 1 linha derruba isso em segundos."""
    con = _con(cfg)
    try:
        con.execute("SELECT 1 FROM glob('%s/dt=*/places/parquet/*.parquet') LIMIT 1" % BASE_HF)
    except Exception as e:                              # noqa: BLE001
        raise RuntimeError(
            "FSQ inacessivel (dataset gated ou token sem permissao): %s\n"
            "  aceite os termos em https://huggingface.co/datasets/foursquare/fsq-os-places "
            "ou rode sem `fsq` em --fontes" % str(e)[:200])
    finally:
        con.close()
    return True


def _release(cfg, man):
    fl = os.path.join(_dir(cfg, man), "files.json")
    em_cache = ler_json_cache(fl)
    if em_cache is not None:
        return em_cache
    snap = man.snapshot("fsq")
    fixada = snap.get("source_version")
    # DEFEITO CORRIGIDO (v3.5.0): o manifesto dizia agosto e o adapter consumia a
    # release corrente. Havendo `source_version` resolvida, ela MANDA.
    rel, files = listar_releases(cfg, release=fixada)
    meta = {"rel": rel, "files": files, "release_fixada": bool(fixada)}
    salvar_json_atomico(meta, fl)
    col, wk = man.colecao("fsq")
    man.versao_fonte("fsq", release=rel, shards_total=len(files),
                     collection_id=col, work_id=wk,
                     snapshot_id=man.snapshot("fsq").get("snapshot_id"))
    return meta


def _densos(cfg, man, bbox):
    dp = os.path.join(_dir(cfg, man), "dist.json")
    em_cache = ler_json_cache(dp)
    if em_cache is not None:
        return em_cache
    W, S, E, N = bbox
    files = _release(cfg, man)["files"]
    con = _con(cfg)
    lst = "[" + ",".join("'%s'" % f for f in files) + "]"
    t = time.time()
    r = con.execute(
        "SELECT filename fn, count(*) c FROM read_parquet(%s, filename=true) "
        "WHERE longitude BETWEEN %s AND %s AND latitude BETWEEN %s AND %s "
        "GROUP BY filename ORDER BY c DESC" % (lst, W, E, S, N)).fetchall()
    dense = {fn: int(c) for fn, c in r if c}
    salvar_json_atomico(dense, dp)
    man.versao_fonte("fsq", shards_densos=len(dense), pontos_bbox=sum(dense.values()),
                     dist_segundos=round(time.time() - t))
    print("  FSQ dist: %d shards densos de %d | %d pontos no bbox | %.0fs"
          % (len(dense), len(files), sum(dense.values()), time.time() - t))
    return dense


def _copy(cfg, man, bbox, dense):
    W, S, E, N = bbox
    raw = _dir(cfg, man, "raw")
    edges = np.linspace(S, N, cfg.fsq_strips + 1)
    con = _con(cfg)
    t0 = time.time()
    feitas = 0
    files = sorted(dense)
    total = len(files) * cfg.fsq_strips
    for fi, f in enumerate(files):
        for si in range(cfg.fsq_strips):
            outp = os.path.join(raw, "r_%03d_%02d.parquet" % (fi, si))
            if os.path.exists(outp):
                continue
            if cfg.budget_s and time.time() - t0 > cfg.budget_s:
                return feitas, total, False
            s0, s1 = float(edges[si]), float(edges[si + 1])
            con.execute(
                "COPY (SELECT * EXCLUDE (geom) FROM read_parquet('%s') "
                "WHERE longitude BETWEEN %s AND %s AND latitude BETWEEN %s AND %s) "
                "TO '%s' (FORMAT parquet)" % (f, W, E, s0, s1, outp + ".tmp"))
            os.replace(outp + ".tmp", outp)
            feitas += 1
    return feitas, total, True


def _achatar(v):
    if v is None:
        return None
    if isinstance(v, (list, np.ndarray)):
        return ";".join("" if x is None else str(x) for x in v) if len(v) else None
    return v


# PATCH LOCAL DA SKILL — 25/08/2026. Ver o comentário no topo do arquivo.
#
#     ERRO na etapa 'fetch': boolean value of NA is ambiguous
#
# O ramo `else (a or None)` fazia `bool(a)` quando `a` não era lista. Com um
# `pd.NA` na coluna — que não é `None`, não é lista e não é string — o pandas
# RECUSA avaliá-lo como booleano, e a etapa inteira morre.
#
# Aconteceu no RS e no PI, no mesmo ponto e depois de o download ter dado
# certo: `FSQ dist: 3 shards densos | 550.514 pontos no bbox | 25s` e então o
# estouro. O defeito só apareceu agora porque antes o `fsq` morria no 403 do
# Hugging Face, longe daqui.
#
# `pd.isna` ANTES de qualquer teste de verdade, e só depois o resto. A ordem
# é o conserto: qualquer coisa que force um booleano sobre NA repete o erro.
def _primeira_categoria(a):
    if isinstance(a, (list, np.ndarray)):
        return str(a[0]).strip() if len(a) else None
    if a is None:
        return None
    try:
        if pd.isna(a):          # pd.NA, NaN, NaT
            return None
    except (TypeError, ValueError):
        pass                    # tipo sobre o qual isna não decide: segue
    return a or None


def _build(cfg, man):
    dest = os.path.join(_dir(cfg, man), "fsq.parquet")
    if os.path.exists(dest):
        return int(pd.read_parquet(dest, columns=["id_fonte"]).shape[0])
    partes = sorted(glob.glob(os.path.join(_dir(cfg, man, "raw"), "r_*.parquet")))
    if not partes:
        pd.DataFrame().to_parquet(dest)
        return 0
    con = _con(cfg)
    lst = "[" + ",".join("'%s'" % p for p in partes) + "]"
    raw = con.execute(
        "SELECT * FROM read_parquet(%s, union_by_name=true)" % lst).fetchdf()
    out = pd.DataFrame(index=raw.index)
    out["fonte"] = "fsq"
    for destino, origem in MAPA.items():
        out[destino] = raw[origem] if origem in raw.columns else None
    lbl = raw["fsq_category_labels"] if "fsq_category_labels" in raw.columns else None

    out["categoria_orig"] = lbl.map(_primeira_categoria) if lbl is not None else None
    out["marca"] = None
    out["confianca"] = None
    dc = raw["date_closed"] if "date_closed" in raw.columns else None
    out["status"] = np.where(dc.notna(), "fechado", None) if dc is not None else None
    dr = raw["date_refreshed"] if "date_refreshed" in raw.columns else None
    out["data_atualizacao"] = dr.astype(str) if dr is not None else None
    for c in raw.columns:
        col = raw[c]
        if col.dtype == object:
            amostra = col.dropna().head(1).tolist()
            if amostra and isinstance(amostra[0], (list, np.ndarray)):
                col = col.map(_achatar)
        out["fsq.%s" % c] = col
    tmp = dest + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, dest)
    man.funil("fetch.fsq", len(raw), len(out), "nenhum (build e 1:1)")
    return len(out)


def executar(cfg, man, bbox):
    dense = _densos(cfg, man, bbox)
    if not dense:
        print("FSQ: nenhum shard denso no bbox")
        return {"pontos": 0, "completo": True}
    feitas, total, ok = _copy(cfg, man, bbox, dense)
    if not ok:
        print("FSQ: copy parcial (+%d faixas, budget) — reexecute" % feitas)
        return {"faixas": feitas, "faixas_total": total, "completo": False}
    n = _build(cfg, man)
    print("FSQ: %d pontos | %d shards densos | %d faixas" % (n, len(dense), total))
    return {"pontos": n, "shards_densos": len(dense), "completo": True}
