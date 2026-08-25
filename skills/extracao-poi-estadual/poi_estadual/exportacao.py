# -*- coding: utf-8 -*-
"""Entregaveis: padronizado (CSV e/ou GeoParquet), bruto POR FONTE e dicionario.

Bruto POR FONTE, nunca unificado: unir Overture + OSM + FSQ com `union_by_name`
soma os esquemas nativos e passa do limite de 1600 colunas do PostgreSQL. Cada
fonte sai no seu proprio arquivo, com as colunas nativas preservadas — e o OSM com
as tags em `osm_tags_json` (carregavel como JSONB).

O bruto e filtrado pelos `id_fonte` que sobreviveram ao clip: e o bruto DO
TERRITORIO, nao do bbox.
"""
import glob
import json
import os

import geopandas as gpd
import pandas as pd

from .normalizacao import (PADRAO, carregar_observacoes, carregar_padronizado,
                           carregar_rejeitados, carregar_vinculos)
from .osm import bruto as bruto_osm
from .vendor import tratar_pois as tp


def _keep_ids(cfg):
    p = os.path.join(cfg.dir_proc("territory"), "keep_ids.parquet")
    return set(pd.read_parquet(p)["id_fonte"].astype(str))


def _grava(cfg, df, base, geo=False):
    saidas = []
    if "csv" in cfg.formatos:
        p = cfg.arq_saida(base + ".csv")
        df.to_csv(p + ".tmp", index=False)
        os.replace(p + ".tmp", p)
        saidas.append(p)
    if "geoparquet" in cfg.formatos:
        p = cfg.arq_saida(base + ".parquet")
        if geo and {"lat", "lon"} <= set(df.columns):
            g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)
            g.to_parquet(p + ".tmp")
        else:
            df.to_parquet(p + ".tmp", index=False)
        os.replace(p + ".tmp", p)
        saidas.append(p)
    return saidas


def padronizado(cfg, man):
    df = carregar_padronizado(cfg)
    saidas = _grava(cfg, df, "poi_padronizado_%s" % cfg.rotulo.lower(), geo=True)
    dic = pd.DataFrame({"campo": PADRAO, "descricao": [tp._descr(c) for c in PADRAO]})
    p = cfg.arq_saida("poi_dicionario_%s.csv" % cfg.rotulo.lower())
    dic.to_csv(p + ".tmp", index=False)
    os.replace(p + ".tmp", p)
    saidas.append(p)
    return df, saidas


def _bruto_overture(cfg, man, keep):
    partes = sorted(glob.glob(os.path.join(
        cfg.dir_colecao("overture", *(man.colecao("overture") or ("x", "y")), "parts"), "ov_*.parquet")))
    frames = []
    for p in partes:
        d = pd.read_parquet(p)
        if len(d):
            frames.append(d[d["id_fonte"].astype(str).isin(keep)])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _bruto_fsq(cfg, man, keep):
    p = os.path.join(cfg.dir_colecao("fsq", *(man.colecao("fsq") or ("x", "y"))), "fsq.parquet")
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_parquet(p)
    return d[d["id_fonte"].astype(str).isin(keep)] if len(d) else d


def _bruto_ifood(cfg, man, keep):
    p = os.path.join(cfg.dir_colecao("ifood", *(man.colecao("ifood") or ("x", "y"))),
                     "ifood.parquet")
    if not os.path.exists(p):
        return pd.DataFrame()
    d = pd.read_parquet(p)
    return d[d["id_fonte"].astype(str).isin(keep)] if len(d) else d


def brutos(cfg, man):
    keep = _keep_ids(cfg)
    saidas = {}
    mapa = {}
    if "overture" in cfg.fontes:
        mapa["overture"] = _bruto_overture(cfg, man, keep)
    if "osm" in cfg.fontes:
        d = bruto_osm(cfg, man)
        mapa["osm"] = d[d["id_fonte"].astype(str).isin(keep)] if len(d) else d
    if "fsq" in cfg.fontes:
        mapa["fsq"] = _bruto_fsq(cfg, man, keep)
    if "ifood" in cfg.fontes:
        mapa["ifood"] = _bruto_ifood(cfg, man, keep)
    for fonte, d in mapa.items():
        base = "poi_bruto_%s_%s" % (fonte, cfg.rotulo.lower())
        p = cfg.arq_saida(base + ".parquet")
        d.to_parquet(p + ".tmp", index=False)
        os.replace(p + ".tmp", p)
        saidas[fonte] = {"arquivo": p, "linhas": len(d), "colunas": len(d.columns)}
    return saidas


def auditoria(cfg, man):
    """v3.0.0 — os dois artefatos que fechavam a pendencia de rastreabilidade.

    `poi_dedup_vinculos`: par a par, o que foi fundido, por qual evidencia e o que
    foi RECUSADO (veto de telefone, contexto, diametro). Sem ele, uma fusao indevida
    e invisivel — sobra so o nome do sobrevivente.
    `poi_rejeitados`: cada linha descartada em raw/territory/normalize, com motivo."""
    vin = carregar_vinculos(cfg)
    rej = carregar_rejeitados(cfg)
    obs = carregar_observacoes(cfg)
    saidas = []
    saidas += _grava(cfg, vin, "poi_dedup_vinculos_%s" % cfg.rotulo.lower())
    saidas += _grava(cfg, rej, "poi_rejeitados_%s" % cfg.rotulo.lower())
    saidas += _grava(cfg, obs, "poi_observacoes_%s" % cfg.rotulo.lower())
    saidas += _grava(cfg, _procedencia(cfg, man), "poi_source_snapshot_%s" % cfg.rotulo.lower())
    return vin, rej, obs, saidas


def _procedencia(cfg, man):
    """Tabela formal de procedencia: uma linha por fonte, com a VERSAO DA FONTE
    separada de como a coleta foi pedida e materializada.

    `is_latest_at_collection` responde "no momento da coleta, era a mais nova que a
    fonte oferecia?" — e nao muda com o tempo, ao contrario de um `is_latest`."""
    lic = {"overture": "CDLA-Permissive-2.0", "osm": "ODbL — (c) OpenStreetMap contributors",
           "fsq": "Apache-2.0 (manter NOTICE)", "ibge": "uso publico",
           "ifood": "dado publico do marketplace (sem login); uso conforme os "
                    "termos do iFood"}
    linhas = []
    proc = man.procedencia() or {}
    versoes = man.d.get("fontes_versao") or {}
    for fonte in sorted(set(proc) | set(versoes)):
        snap = (proc.get(fonte) or {}).get("snapshot") or {}
        meta = versoes.get(fonte) or {}
        col, wk = man.colecao(fonte)
        linhas.append({
            "snapshot_id": snap.get("snapshot_id", "indeterminado"),
            "fonte": fonte,
            "source_version": snap.get("source_version"),
            "source_determinado": bool(snap.get("determinado")),
            "source_url": snap.get("source_url"),
            "etag": snap.get("etag"),
            "last_modified": snap.get("last_modified"),
            "content_length": snap.get("content_length"),
            "content_digest": snap.get("content_digest"),
            "digest_algorithm": snap.get("digest_algorithm"),
            "digest_scope": snap.get("digest_scope"),
            "resolution_status": snap.get("resolution_status"),
            "retrieved_at": man.retrieved_at(fonte),
            "collection_id": col,
            "work_id": wk,
            "source_mode": getattr(cfg, "source_mode", "cache"),
            # nasce no RESOLVEDOR: `latest` com consulta falhando e fallback NAO e
            # "latest verificado".
            "is_latest_at_collection": snap.get("resolution_status") == "verified_latest",
            "uf": cfg.uf,
            "licenca": lic.get(fonte, ""),
            "adapter_name": "poi_estadual.%s" % fonte,
            "adapter_versao": man.d.get("skill_versao"),
            "workspace_criado_com": man.d.get("workspace_criado_com"),
            # `row_count_raw` misturava shard, tile e municipio com REGISTRO. Tres
            # coisas diferentes, tres campos.
            "partition_count": meta.get("shards_total") or meta.get("tiles"),
            "source_object_count": meta.get("municipios"),
            "raw_record_count": meta.get("pontos") or meta.get("nodes"),
            "workspace_id": man.d.get("workspace_id"),
            "run_id": man.d.get("run_id"),
            "detalhe": json.dumps(meta, ensure_ascii=False, default=str)})
    return pd.DataFrame(linhas, columns=[
        "snapshot_id", "fonte", "source_version", "source_determinado", "source_url",
        "etag", "last_modified", "content_length", "content_digest", "digest_algorithm",
        "digest_scope", "resolution_status", "retrieved_at",
        "collection_id", "work_id", "source_mode", "is_latest_at_collection", "uf",
        "licenca", "adapter_name", "adapter_versao", "partition_count",
        "source_object_count", "raw_record_count",
        "workspace_criado_com", "workspace_id", "run_id", "detalhe"])


def executar(cfg, man):
    man.iniciar("export")
    df, saidas = padronizado(cfg, man)
    br = brutos(cfg, man)
    vin, rej, obs, saidas_aud = auditoria(cfg, man)
    saidas += saidas_aud
    fundidos = int(vin["aceito"].sum()) if len(vin) else 0
    man.concluir("export", padronizado=len(df), colunas=len(df.columns),
                 vinculos=len(vin), fusoes_aceitas=fundidos, rejeitados=len(rej),
                 observacoes=len(obs),
                 **{"bruto_%s" % k: v["linhas"] for k, v in br.items()})
    print("EXPORT: padronizado=%d x %d col | observacoes=%d | fusoes=%d | rejeitados=%d | bruto: %s"
          % (len(df), len(df.columns), len(obs), fundidos, len(rej),
             ", ".join("%s=%d" % (k, v["linhas"]) for k, v in br.items()) or "-"))
    for p in saidas:
        print("  -> %s" % p)
    for v in br.values():
        print("  -> %s (%d col)" % (v["arquivo"], v["colunas"]))
    return df
