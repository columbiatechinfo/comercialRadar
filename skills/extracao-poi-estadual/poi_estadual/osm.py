# -*- coding: utf-8 -*-
"""OSM via dump .pbf da macrorregiao (Geofabrik) lido com DuckDB ST_ReadOSM.

Dois scans separados (nunca uma query unica): (a) ways-POI com id/tags/refs;
(b) nos dos refs por SEMI JOIN. A query unica da v1 (`cmd_osm_ways` do rs_driver)
materializa ways x nos de uma vez e estoura memoria em UF grande.

Correcao v2 — POSICAO DO WAY. A v1 usava a MEDIA das coordenadas dos nos
(`AVG(lat), AVG(lon)`). Media nao e centroide e, em poligono concavo ou em L,
cai fora da propria geometria. Aqui a geometria e reconstruida e usa-se
`representative_point()` (equivalente a `ST_PointOnSurface`), com garantia de
ponto DENTRO do poligono; way aberto usa o ponto medio da linha.

v3.0.0 — RECALL. O predicado passou a ser configuravel (`--osm-predicado`) e `name`
deixou de ser obrigatorio (`--osm-sem-nome`). Medido em Canoas: o predicado classico
com `name` obrigatorio coletava 525 de 1.124 nos com cara de POI (47%).

v3.3.0 — RELATION. `kind='relation'` passou a ser lida: os ways membros sao resolvidos,
os aneis remontados com `polygonize` e a posicao sai de `representative_point()`. Sem
isso ficavam de fora justamente os grandes equipamentos (Base Aerea de Canoas,
shoppings, campi, hospitais, parques). Para nao duplicar, o membro ja coletado que
compartilha nome ou marca com a relation entra em `supressao.parquet` — SUPRESSAO POR
CONTENCAO E IDENTIDADE, nao por raio: um shopping tem 200 m de largura e distancia
nao resolveria. A supressao e aplicada no `raw`, contabilizada no funil e gravada em
`rejeitados`.
"""
import json
import os
import time

import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon

from .config import salvar_atomico
from .vendor import extrair_pois as ep
from .vendor import osm_pbf as op

CORE_BRUTO = ["id_fonte", "fonte", "nome", "lat", "lon", "categoria_orig", "endereco_raw",
              "bairro", "localidade_fonte", "cep", "telefone", "site", "email", "instagram",
              "marca", "status", "data_atualizacao", "osm_type", "osm_id", "osm_tags_json"]


# CORRIDA NA CONVERSAO DE COLUNA LIST — o teto existe por causa dela.
#
# `fetchdf()` de um resultado com coluna LIST corrompe a heap quando o DuckDB
# converte em paralelo. O processo morre com `double free or corruption (out)`
# e codigo 139, sem excecao Python: nao ha o que capturar, o interpretador ja
# foi abaixo junto.
#
# Medido em 31/08/2026, DuckDB 1.5.4 / pyarrow 25.0.0 / pandas 2.3.3, todos
# fixados no requirements.txt e instalados antes de qualquer uma das execucoes
# — o ambiente NAO mudou entre uma UF que passou e outra que abortou:
#
#   RS, 8 threads, teto de 2.6GB  -> aborta em `_ways` (3 execucoes, 3 aborts)
#   RS, 8 threads, teto de  32GB  -> aborta igual, em 5 segundos
#   RS, 1 thread,  teto de 2.6GB  -> nodes=105315 ways=169626 relations=2263
#   PE, 8 threads                 -> passa
#
# Os numeros da execucao com uma thread batem com os da rodada de duas fontes
# que tinha dado certo, entao o teto nao muda o dado — so a ordem de conversao.
#
# NAO E MEMORIA: com 32 GB de teto numa maquina de 123 GB o abort acontece em
# cinco segundos, cedo demais para derrame ou pressao. E nao e o arquivo: o PE
# leu um `.pbf` de 439.839.968 bytes identico ao que o PI abortou lendo. E a
# corrida, e quem a perde depende de onde caem os limites de chunk daquele
# arquivo — o que faz a falha parecer deterministica por UF.
#
# O teto vale so onde ha coluna LIST: `_ways` (refs) e `_relations` (members).
# `_nodes` traz id/lat/lon/tags, nenhuma lista, e e o scan mais pesado dos tres
# — continua em paralelo. Custo medido do teto: 51s -> 69s no OSM do RS.
THREADS_LIST = 1


def _con(cfg, threads=None):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET memory_limit='%s';" % cfg.duckdb_memory)
    con.execute("SET threads=%d;" % (threads or cfg.threads))
    con.execute("SET temp_directory='%s';" % cfg.dir_fonte("osm", "ddtmp"))
    return con


def _dir(cfg, man=None):
    """Derivados do OSM sob COLLECTION/WORK — portanto sob o SNAPSHOT.

    DEFEITO CORRIGIDO (v3.5.0): ate a v3.4.0 `nodes/ways/relations/supressao` viviam
    sob `sig_osm()` (regiao+predicado+sem_nome). Com `.pbf` novo, `_nodes()` achava o
    parquet antigo e devolvia — PBF de setembro, extracao de agosto."""
    if man is not None:
        col, wk = man.colecao("osm")
        if col:
            return cfg.dir_colecao("osm", col, wk or "sem_work")
    return cfg.dir_fonte("osm", cfg.sig_osm())


def _pbf(cfg, man):
    # o .pbf da macrorregiao independe de UF e de predicado: cache compartilhado
    destino = cfg.dir_fonte("osm", "pbf")
    snap = man.snapshot("osm")
    sid = snap.get("snapshot_id") or "indeterminado"
    # BLOB POR SNAPSHOT. `sul-latest.osm.pbf` e fonte movel: nome fixo, conteudo
    # variavel. Guardar o blob sob o snapshot e o que torna `pinned` restauravel —
    # sem isso, apagar o arquivo local e re-executar em `pinned` baixaria o ATUAL e
    # o marcaria com o snapshot antigo.
    cofre = cfg.dir_fonte("osm", "snapshots", sid)
    blob = os.path.join(cofre, "source.osm.pbf")
    if os.path.exists(blob):
        caminho = blob
    elif cfg.source_mode == "pinned":
        raise RuntimeError(
            "--source-mode pinned: o blob do snapshot OSM %s nao esta em %s. "
            "Baixar `-latest` agora traria outra versao do mundo. Rode com "
            "--source-mode latest para resolver e guardar um snapshot novo." % (sid, cofre))
    else:
        # DEFEITO CORRIGIDO (v3.6.0): antes o download caia em `baixar_pbf(force=False)`,
        # que devolvia o `-latest` JA EM CACHE (possivelmente de outro mes) e o promovia
        # para dentro do snapshot novo. Agora baixa direto para o cofre, com verificacao
        # de digest antes da promocao.
        from . import procedencia as prov
        caminho = prov.baixar_verificando(
            op.url_regiao(cfg.uf), blob,
            digest=snap.get("content_digest") if snap.get("digest_scope") == "full" else None,
            algoritmo=snap.get("digest_algorithm"))
    st = os.stat(caminho)
    man.versao_fonte("osm", assinatura=cfg.sig_osm(), arquivo=os.path.basename(caminho),
                     regiao=op.regiao_da_uf(cfg.uf), bytes=st.st_size,
                     snapshot_id=snap.get("snapshot_id"),
                     source_version=snap.get("source_version"),
                     content_digest=snap.get("content_digest"),
                     digest_algorithm=snap.get("digest_algorithm"),
                     mtime_local=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)))
    return caminho


def _comum(otype, oid, lat, lon, tg, ampliado=True):
    cat = op.categoria_de(tg, ampliado=ampliado)
    end = " ".join(v for v in [tg.get("addr:street"), tg.get("addr:housenumber")] if v) or None
    return dict(
        fonte="osm", id_fonte="%s/%s" % (otype, oid), nome=tg.get("name"),
        lat=float(lat), lon=float(lon), categoria_orig=cat, categoria_hier=None,
        endereco_raw=end,
        bairro=tg.get("addr:suburb") or tg.get("addr:neighbourhood"),
        localidade_fonte=tg.get("addr:city"),
        cep=tg.get("addr:postcode"),
        telefone=tg.get("phone") or tg.get("contact:phone"),
        site=tg.get("website") or tg.get("contact:website"),
        email=tg.get("email") or tg.get("contact:email"),
        instagram=tg.get("contact:instagram"), marca=tg.get("brand"),
        confianca=None, status=None, data_atualizacao=None)


def _montar(df, otype, ampliado=True):
    rows = []
    for r in df.itertuples(index=False):
        tg = dict(r.tags) if r.tags is not None else {}
        nat = {}
        op.flatten({"type": otype, "id": r.id}, "osm.", nat)
        op.flatten(tg, "osm.tags.", nat)
        rows.append({**_comum(otype, r.id, r.lat, r.lon, tg, ampliado), **nat})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=ep.COMUNS)


def _amp(cfg):
    return getattr(cfg, "osm_predicado", "ampliado") == "ampliado"


def _where(cfg):
    """Predicado da coleta OSM: chaves de POI e, opcionalmente, exigência de `name`.

    `name` obrigatório descartava 213 nós só em Canoas. Uma `amenity=pharmacy` sem
    nome continua sendo uma farmácia que ocupa imóvel e consome água — a existência
    e a posição valem sem o nome. O que a coleta traz sem nome sai marcado em
    `sem_nome` e é tratado com regra própria no dedup."""
    amp = getattr(cfg, "osm_predicado", "ampliado") == "ampliado"
    cond = "(%s)" % op._pred("tags", ampliado=amp)
    if not getattr(cfg, "osm_sem_nome", True):
        cond = "map_extract(tags,'name')<>[] AND " + cond
    return cond


def _ponto_representativo(coords):
    """Ponto garantidamente sobre a feicao. Fecha o anel quando o way e fechado."""
    if len(coords) == 1:
        return coords[0]
    fechado = len(coords) >= 4 and coords[0] == coords[-1]
    try:
        if fechado:
            p = Polygon([(lo, la) for la, lo in coords])
            if not p.is_valid:
                p = p.buffer(0)
            if p.is_empty or p.area == 0:
                raise ValueError("degenerado")
            g = p.representative_point()
        else:
            g = LineString([(lo, la) for la, lo in coords]).interpolate(0.5, normalized=True)
        if isinstance(g, Point) and not g.is_empty:
            return (g.y, g.x)
    except (ValueError, TypeError, ZeroDivisionError):
        pass
    return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))


def _nodes(cfg, man, pbf):
    dest = os.path.join(_dir(cfg, man), "nodes.parquet")
    if os.path.exists(dest):
        return int(pd.read_parquet(dest, columns=["id_fonte"]).shape[0])
    con = _con(cfg)
    q = ("SELECT id, lat, lon, tags FROM ST_ReadOSM('%s') "
         "WHERE kind='node' AND %s" % (pbf, _where(cfg)))
    df = con.execute(q).fetchdf()
    salvar_atomico(_montar(df, "node", _amp(cfg)), dest)
    return len(df)


def _ways(cfg, man, pbf):
    dest = os.path.join(_dir(cfg, man), "ways.parquet")
    if os.path.exists(dest):
        return int(pd.read_parquet(dest, columns=["id_fonte"]).shape[0])
    bruto = os.path.join(_dir(cfg, man), "_ways_raw.pkl")
    con = _con(cfg, THREADS_LIST)  # refs e LIST
    if not os.path.exists(bruto):
        q = ("SELECT id, tags, refs FROM ST_ReadOSM('%s') "
             "WHERE kind='way' AND %s" % (pbf, _where(cfg)))
        raw = con.execute(q).fetchdf()
        raw["tags"] = raw["tags"].map(lambda d: dict(d) if d is not None else {})
        raw["refs"] = raw["refs"].map(lambda a: [int(x) for x in a] if a is not None else [])
        raw.to_pickle(bruto + ".tmp")
        os.replace(bruto + ".tmp", bruto)
    raw = pd.read_pickle(bruto)
    if not len(raw):
        salvar_atomico(pd.DataFrame(columns=ep.COMUNS), dest)
        return 0
    need = np.unique(np.concatenate(
        [np.asarray(r, dtype=np.int64) for r in raw["refs"].values if len(r)]))
    con.register("need_df", pd.DataFrame({"nid": need}))
    con.execute("CREATE TEMP TABLE need AS SELECT nid FROM need_df")
    nodes = con.execute(
        "SELECT s.id AS id, s.lat AS lat, s.lon AS lon FROM ST_ReadOSM('%s') s "
        "SEMI JOIN need ON s.id=need.nid WHERE s.kind='node'" % pbf).fetchdf()
    coord = dict(zip(nodes["id"].to_numpy(),
                     zip(nodes["lat"].to_numpy(), nodes["lon"].to_numpy())))
    rows = []
    sem_geom = 0
    for r in raw.itertuples(index=False):
        cs = [coord[n] for n in r.refs if n in coord]
        if not cs:
            sem_geom += 1
            continue
        la, lo = _ponto_representativo(cs)
        rows.append((int(r.id), float(la), float(lo), r.tags))
    man.funil("fetch.osm_ways", len(raw), len(rows), "way sem no resolvido no .pbf")
    wdf = pd.DataFrame(rows, columns=["id", "lat", "lon", "tags"])
    salvar_atomico(_montar(wdf, "way", _amp(cfg)), dest)
    return len(rows)


def _rings(coords_por_way):
    """Remonta os aneis do multipolygon e devolve um ponto garantidamente sobre a
    feicao. `polygonize` ordena os ways sozinho; sem anel fechado, cai no fecho
    convexo dos membros (registrado como aproximacao)."""
    from shapely.ops import polygonize, unary_union
    linhas = [LineString([(lo, la) for la, lo in cs]) for cs in coords_por_way if len(cs) >= 2]
    if not linhas:
        pts = [c for cs in coords_por_way for c in cs]
        if not pts:
            return None, None
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)), "media"
    polis = list(polygonize(unary_union(linhas)))
    if polis:
        maior = max(polis, key=lambda g: g.area)
        p = maior.representative_point()
        return (p.y, p.x), "poligono"
    env = unary_union(linhas).convex_hull
    p = env.representative_point() if env.geom_type == "Polygon" else env.centroid
    return (p.y, p.x), "fecho_convexo"


def _relations(cfg, man, pbf):
    """Relations-POI: resolve members -> ways -> nos, remonta e posiciona."""
    dest = os.path.join(_dir(cfg, man), "relations.parquet")
    sup = os.path.join(_dir(cfg, man), "supressao.parquet")
    if os.path.exists(dest):
        return int(pd.read_parquet(dest, columns=["id_fonte"]).shape[0])
    con = _con(cfg, THREADS_LIST)  # members e LIST
    # `ref_types` e ENUM: `fetchdf()` devolve o CODIGO (int8), nao o rotulo. Sem o
    # cast, todo membro deixaria de ser reconhecido como way e a relation nao teria
    # geometria — falha silenciosa, com contagem zero e nenhum erro.
    rel = con.execute(
        "SELECT id, tags, refs, CAST(ref_types AS VARCHAR[]) AS ref_types "
        "FROM ST_ReadOSM('%s') WHERE kind='relation' AND %s" % (pbf, _where(cfg))).fetchdf()
    if not len(rel):
        salvar_atomico(pd.DataFrame(columns=ep.COMUNS), dest)
        salvar_atomico(pd.DataFrame(columns=["id_fonte", "motivo"]), sup)
        return 0
    rel["tags"] = rel["tags"].map(lambda d: dict(d) if d is not None else {})
    membros = []
    for r in rel.itertuples(index=False):
        tipos = list(r.ref_types) if r.ref_types is not None else []
        ids = [int(x) for x in (r.refs if r.refs is not None else [])]
        membros.append([(t, i) for t, i in zip(tipos, ids)])
    wid = sorted({i for ms in membros for t, i in ms if str(t) == "way"})
    nid_diretos = sorted({i for ms in membros for t, i in ms if str(t) == "node"})

    wrefs = {}
    if wid:
        con.register("need_w", pd.DataFrame({"wid": np.array(wid, dtype=np.int64)}))
        con.execute("CREATE TEMP TABLE nw AS SELECT wid FROM need_w")
        w = con.execute("SELECT s.id AS id, s.refs AS refs FROM ST_ReadOSM('%s') s "
                        "SEMI JOIN nw ON s.id=nw.wid WHERE s.kind='way'" % pbf).fetchdf()
        wrefs = {int(a): [int(x) for x in (b if b is not None else [])]
                 for a, b in zip(w["id"], w["refs"])}
    precisa = sorted(set(nid_diretos) | {n for v in wrefs.values() for n in v})
    coord = {}
    if precisa:
        con.register("need_n", pd.DataFrame({"nid": np.array(precisa, dtype=np.int64)}))
        con.execute("CREATE TEMP TABLE nn AS SELECT nid FROM need_n")
        nd = con.execute("SELECT s.id AS id, s.lat AS lat, s.lon AS lon FROM ST_ReadOSM('%s') s "
                         "SEMI JOIN nn ON s.id=nn.nid WHERE s.kind='node'" % pbf).fetchdf()
        coord = dict(zip(nd["id"].to_numpy(), zip(nd["lat"].to_numpy(), nd["lon"].to_numpy())))

    linhas, origem, sem_geom, usados = [], [], 0, []
    for r, ms in zip(rel.itertuples(index=False), membros):
        por_way = [[coord[n] for n in wrefs.get(i, []) if n in coord]
                   for t, i in ms if str(t) == "way"]
        soltos = [coord[i] for t, i in ms if str(t) == "node" and i in coord]
        if soltos:
            por_way.append(soltos)
        pos, como = _rings([c for c in por_way if c])
        if pos is None:
            sem_geom += 1
            continue
        linhas.append((int(r.id), float(pos[0]), float(pos[1]), r.tags))
        origem.append(como)
        usados.append((int(r.id), r.tags, ms))

    man.funil("fetch.osm_relations", len(rel), len(linhas), "relation sem geometria resolvida")
    rdf = pd.DataFrame(linhas, columns=["id", "lat", "lon", "tags"])
    out = _montar(rdf, "relation", _amp(cfg))
    if len(out):
        out["osm.geom_origem"] = origem
    salvar_atomico(out, dest)
    salvar_atomico(_supressao(cfg, man, usados), sup)
    return len(out)


def _supressao(cfg, man, usados):
    """Membro ja coletado que compartilha NOME ou MARCA com a relation vira duplicata.

    Contencao + identidade, nao raio: o way `building=retail` com o nome do shopping
    dentro da relation do shopping e o mesmo equipamento; um POI de loja la dentro,
    com nome proprio, NAO e."""
    from .vendor.tratar_pois import _name_core
    ja = {}
    for nome_arq, tipo in (("nodes.parquet", "node"), ("ways.parquet", "way")):
        p = os.path.join(_dir(cfg, man), nome_arq)
        if not os.path.exists(p):
            continue
        cols = ["id_fonte", "nome"] + (["marca"] if "marca" in
                                        pd.read_parquet(p).columns[:0].append(
                                            pd.Index(["marca"])) else [])
        d = pd.read_parquet(p)
        for idf, nm, mc in zip(d["id_fonte"].astype(str), d["nome"],
                               d["marca"] if "marca" in d.columns else d["nome"]):
            # a regra e "nome OU marca"; o membro tem de ser comparado pelos dois
            ja[idf] = {x for x in (_name_core(nm), _name_core(mc)) if x}
    linhas = []
    for rid, tags, ms in usados:
        alvos = {x for x in (_name_core(tags.get("name")),
                             _name_core(tags.get("brand"))) if x}
        if not alvos:
            continue
        for t, i in ms:
            chave = "%s/%s" % (t, i)
            if alvos & (ja.get(chave) or set()):
                linhas.append((chave, "membro da relation/%d com mesmo nome ou marca" % rid))
    return pd.DataFrame(linhas, columns=["id_fonte", "motivo"]) if linhas \
        else pd.DataFrame(columns=["id_fonte", "motivo"])


def suprimidos(cfg, man=None):
    p = os.path.join(_dir(cfg, man), "supressao.parquet")
    return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame(
        columns=["id_fonte", "motivo"])


def bruto(cfg, man=None):
    """Bruto OSM: tags 1000+ consolidadas em `osm_tags_json`.

    Em colunas, as tags do OSM estouram o limite de 1600 colunas do PostgreSQL.
    Como JSON, carrega direto em JSONB.
    """
    frames = []
    for nome, otype in (("nodes.parquet", "node"), ("ways.parquet", "way"),
                        ("relations.parquet", "relation")):
        p = os.path.join(_dir(cfg, man), nome)
        if not os.path.exists(p):
            continue
        d = pd.read_parquet(p)
        if not len(d):
            continue
        tagcols = [c for c in d.columns if c.startswith("osm.tags.")]
        # 0 colunas de tag -> to_dict('records') devolve [], nao N dicts vazios.
        if tagcols:
            tags = d[tagcols].rename(columns=lambda c: c[len("osm.tags."):])
            tags_json = [json.dumps({k: v for k, v in rec.items() if pd.notna(v)},
                                    ensure_ascii=False)
                         for rec in tags.to_dict("records")]
        else:
            tags_json = ["{}"] * len(d)
        d = d[[c for c in CORE_BRUTO if c in d.columns]].copy()
        d["osm_type"] = otype
        d["osm_id"] = d["id_fonte"].astype(str).str.split("/").str[-1]
        d["osm_tags_json"] = tags_json
        frames.append(d.reindex(columns=CORE_BRUTO))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=CORE_BRUTO)


def executar(cfg, man):
    pbf = _pbf(cfg, man)
    t = time.time()
    n = _nodes(cfg, man, pbf)
    w = _ways(cfg, man, pbf)
    r = _relations(cfg, man, pbf)
    s_ = len(suprimidos(cfg, man))
    print("OSM: nodes=%d | ways=%d | relations=%d | membros suprimidos=%d | %.0fs"
          % (n, w, r, s_, time.time() - t))
    return {"nodes": n, "ways": w, "relations": r, "suprimidos": s_, "completo": True}
