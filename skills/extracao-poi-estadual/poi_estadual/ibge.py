# -*- coding: utf-8 -*-
"""Limites municipais: bootstrap do ZERO, alvo (UF menos exclusoes) e bbox derivado.

A v1.0.0 nao tinha esta etapa — dependia de `rs_keep.parquet` ja existir em disco e
de um parquet de divisoes territoriais que pode nao estar instalado. Aqui:

  1. parquet local (--malha-parquet / DIVISOES_PARQUET), se existir;
  2. senao, API IBGE: /localidades (codigo+nome) + /malhas v3 (geometria por municipio).

Malha IBGE e SIRGAS 2000 (EPSG:4674) — declarado explicitamente, nunca implicito.
O bbox da UF sai da geometria carregada; nao existe bbox fixo no codigo.

Fronteira: `simplificar_graus=0.0` por padrao. Simplificar polígono desloca a
fronteira e pode trocar o municipio de um ponto proximo ao limite — vira opcao,
nao default.
"""
import gzip
import json
import os
import urllib.request

import geopandas as gpd
import pandas as pd

SRID_IBGE = 4674          # SIRGAS 2000 — armazenamento
SRID_PONTOS = 4326        # WGS84 — lat/lon das fontes de POI
API_LOC = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
API_MALHA = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
             "?formato=application/vnd.geo+json&intrarregiao=municipio&qualidade={q}")
from .procedencia import UA           # noqa: E402 — versao unica, vinda de VERSION
META = ["COD_MUNICIPIO", "NOME_MUNICIPIO", "UF", "PLACA", "AREA_KM2",
        "TX_URBANIZACAO", "CLASSE_URB"]

UF_COD = {"RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
          "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27",
          "SE": "28", "BA": "29", "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
          "PR": "41", "SC": "42", "RS": "43", "MS": "50", "MT": "51", "GO": "52", "DF": "53"}


def _get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return json.loads(b.decode("utf-8"))


def _cod(serie):
    return serie.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(7)


def _da_api(uf, qualidade):
    """Baixa geometria + nomes do IBGE. Retorna GeoDataFrame em EPSG:4674."""
    loc = _get(API_LOC.format(uf=uf))
    nomes = {str(m["id"]): m["nome"] for m in loc}
    gj = _get(API_MALHA.format(uf=uf, q=qualidade))
    g = gpd.GeoDataFrame.from_features(gj["features"], crs=SRID_IBGE)
    g["COD_MUNICIPIO"] = _cod(g["codarea"])
    g["NOME_MUNICIPIO"] = g["COD_MUNICIPIO"].map(nomes)
    g["UF"] = uf
    for c in ("PLACA", "AREA_KM2", "TX_URBANIZACAO", "CLASSE_URB"):
        g[c] = None
    return g[META + ["geometry"]], {"origem": "api_ibge", "qualidade": qualidade,
                                    "municipios_localidades": len(nomes)}


def _do_parquet(path, uf):
    g = gpd.read_parquet(path)
    if g.crs is None:
        g = g.set_crs(SRID_IBGE)
    g["COD_MUNICIPIO"] = _cod(g["COD_MUNICIPIO"])
    g = g[g["UF"].astype(str).str.upper() == uf]
    for c in META:
        if c not in g.columns:
            g[c] = None
    return g[META + ["geometry"]], {"origem": "parquet_local", "arquivo": os.path.basename(path)}


def _so_municipios(g, uf):
    """Descarta feicao que nao e municipio.

    A malha do RS traz 499 feicoes = 497 municipios + Lagoa dos Patos e Lagoa Mirim.
    Regra generica (nao 'if RS'): COD_MUNICIPIO com 7 digitos e prefixo da UF.
    """
    pref = UF_COD[uf]
    ok = g["COD_MUNICIPIO"].str.fullmatch(r"\d{7}") & g["COD_MUNICIPIO"].str.startswith(pref)
    return g[ok].copy(), int((~ok).sum())


def carregar(cfg, man):
    """Malha da UF, cacheada em disco. Idempotente."""
    dest = os.path.join(cfg.dir_fonte("ibge", cfg.sig_ibge()), "malha.parquet")
    if os.path.exists(dest) and man.reutilizavel("init"):
        return gpd.read_parquet(dest)

    path = cfg.malha_parquet or os.environ.get("DIVISOES_PARQUET", "")
    if path and os.path.exists(path):
        g, meta = _do_parquet(path, cfg.uf)
    else:
        g, meta = _da_api(cfg.uf, cfg.malha_qualidade)

    bruto = len(g)
    g, descartadas = _so_municipios(g, cfg.uf)
    man.funil("init.malha", bruto, len(g), "feicao sem COD_MUNICIPIO valido da UF (ex.: lagoas)")
    meta["feicoes_brutas"] = bruto
    meta["feicoes_descartadas"] = descartadas
    meta["municipios"] = len(g)
    meta["srid"] = SRID_IBGE
    man.versao_fonte("ibge", **meta)

    tmp = dest + ".tmp"
    g.to_parquet(tmp)
    os.replace(tmp, dest)
    return g


def bbox_coleta(cfg, man):
    """BBOX IMUTAVEL da UF — da malha COMPLETA, ANTES de qualquer exclusao.

    DEFEITO CORRIGIDO (v3.1.0). Ate a v3.0.0 o bbox saia do ALVO (UF menos
    exclusoes) e alimentava a grade de tiles do Overture, o gate `raw.bbox` e o
    recorte do FSQ. Mas `excluir` NAO entra no hash de escopo de `fetch`/`raw` — a
    premissa e que a coleta e da UF inteira. Duas verdades em conflito:

        hash do fetch          -> nao depende de --excluir
        area efetivamente lida ->      depende de --excluir

    Com um municipio excluido na extremidade do estado, o bbox encolhe; depois uma
    execucao sem exclusao reaproveita esse `fetch` como valido, com cobertura menor.
    Pior: a grade de tiles muda de tamanho e o marcador `DONE` do tile `i` passa a
    apontar para outra area.

    Regra desta versao: **exclusao nunca toca o snapshot bruto da fonte**, so o
    recorte de consumo. Baixa a UF uma vez e produz qualquer combinacao de
    municipios depois, sem nova coleta."""
    dest = os.path.join(cfg.dir_fonte("ibge", cfg.sig_ibge()), "bbox_coleta.json")
    if os.path.exists(dest):
        with open(dest, encoding="utf-8") as fh:
            return tuple(float(x) for x in json.load(fh)["bbox"])
    g = carregar(cfg, man).to_crs(SRID_PONTOS)
    bb = tuple(float(x) for x in g.total_bounds)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"uf": cfg.uf, "bbox": list(bb), "municipios": int(len(g)),
                   "obs": "malha completa da UF; independe de --excluir"}, fh)
    os.replace(tmp, dest)
    return bb


def alvo(cfg, man):
    """Municipios da UF menos as exclusoes, em EPSG:4326 (para clip contra lat/lon).

    Retorna `(gdf_alvo, bbox_coleta, meta)`. O bbox devolvido e o da UF INTEIRA
    (ver `bbox_coleta`): quem recorta e o poligono do alvo, na etapa `territory`.
    """
    dest = os.path.join(cfg.dir_proc("init"), "alvo.parquet")
    if os.path.exists(dest) and man.reutilizavel("init"):
        g = gpd.read_parquet(dest)
        bb = bbox_coleta(cfg, man)
        assinaturas(cfg, man, bb)
        return g, bb, {"cache": True}

    g = carregar(cfg, man)
    faltando = sorted(set(cfg.excluir) - set(g["COD_MUNICIPIO"]))
    if faltando:
        raise ValueError("COD a excluir nao existe na UF %s: %s" % (cfg.uf, faltando))

    n0 = len(g)
    g = g[~g["COD_MUNICIPIO"].isin(cfg.excluir)].copy()
    man.funil("init.alvo", n0, len(g), "municipio excluido por --excluir")

    g = g.to_crs(SRID_PONTOS)
    if cfg.simplificar_graus > 0:
        g["geometry"] = g.geometry.simplify(cfg.simplificar_graus, preserve_topology=True)

    tmp = dest + ".tmp"
    g.to_parquet(tmp)
    os.replace(tmp, dest)
    bbox = bbox_coleta(cfg, man)
    return g, bbox, {"municipios_alvo": len(g), "excluidos": len(cfg.excluir),
                     "bbox_coleta": list(bbox),
                     "bbox_alvo": [float(x) for x in g.total_bounds]}


def tiles(bbox, graus):
    """Grade de tiles cobrindo o bbox. Determinista: mesma bbox -> mesma grade."""
    W, S, E, N = bbox
    out = []
    y = S
    while y < N:
        x = W
        y2 = min(y + graus, N)
        while x < E:
            x2 = min(x + graus, E)
            out.append([round(x, 6), round(y, 6), round(x2, 6), round(y2, 6)])
            x = x2
        y = y2
    return out


def _dir_ov(cfg, man):
    col, wk = man.colecao("overture")
    return cfg.dir_colecao("overture", col or "sem_colecao", wk or "sem_work")


def _guarda_grade(cfg, man, grade):
    """Grade nova != grade do disco => marcador `DONE` aponta para outra area.

    O marcador do Overture e por INDICE de tile. Se a grade muda de tamanho, o
    `DONE` do tile `i` passa a atestar um retangulo diferente do que foi baixado.
    Cache gerado antes da v3.1.0 (bbox derivado do alvo) cai exatamente nisso."""
    p = os.path.join(_dir_ov(cfg, man), "tiles.json")
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8") as fh:
        antiga = json.load(fh)
    if antiga == grade:
        return 0
    done = os.path.join(_dir_ov(cfg, man), "done")
    os.makedirs(done, exist_ok=True)
    marcadores = [os.path.join(done, f) for f in os.listdir(done) if f.endswith(".ok")]
    for m in marcadores:
        os.remove(m)
    print("INIT: grade de tiles mudou (%d -> %d) — %d marcador(es) DONE invalidado(s); "
          "o Overture sera recoletado nos tiles afetados" % (len(antiga), len(grade),
                                                             len(marcadores)))
    return len(marcadores)


def _sig_ov(cfg, grade):
    """A grade DEFINE a coleta do Overture. Como assinatura de diretorio, grade nova
    nao ve parte de grade velha — o residuo `ov_7_3.parquet` de um fatiamento antigo
    deixa de poder entrar no `glob` da coleta nova."""
    return cfg.sig("overture", json.dumps(grade, sort_keys=True), cfg.ov_tile_graus)


def _sig_fsq(cfg, bbox):
    return cfg.sig("fsq", [round(float(b), 6) for b in bbox], cfg.fsq_strips)


def assinaturas(cfg, man, bbox=None, grade=None):
    """Resolve SNAPSHOT/COLLECTION/WORK de cada fonte e persiste no manifesto.

    v3.4.0: a assinatura deixou de derivar so dos parametros de coleta. Agora o
    diretorio materializado carrega o `work_id`, que descende do `snapshot_id` —
    fonte nova, diretorio novo, coleta nova. Congelar a fonte deixa de ser possivel
    por construcao."""
    from . import procedencia as prov
    if bbox is None:
        bbox = bbox_coleta(cfg, man)
    return prov.resolver_todas(cfg, man, bbox, listar_fsq=_listar_fsq(cfg))


def _listar_fsq(cfg):
    if "fsq" not in cfg.fontes:
        return None
    from .foursquare import listar_releases
    return lambda: listar_releases(cfg)


def executar(cfg, man):
    man.iniciar("init")
    g, bbox, meta = alvo(cfg, man)
    grade = tiles(bbox, cfg.ov_tile_graus)
    assinaturas(cfg, man, bbox, grade)
    invalidados = _guarda_grade(cfg, man, grade) if "overture" in cfg.fontes else 0
    with open(os.path.join(_dir_ov(cfg, man), "tiles.json"), "w", encoding="utf-8") as fh:
        json.dump(grade, fh)
    man.concluir("init", municipios_alvo=len(g), tiles=len(grade),
                 marcadores_invalidados=invalidados)
    print("INIT: %s | municipios alvo=%d | excluidos=%d | bbox_coleta=%s | tiles=%d"
          % (cfg.uf, len(g), len(cfg.excluir), [round(b, 3) for b in bbox], len(grade)))
    return g, bbox
