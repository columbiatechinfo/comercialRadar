# -*- coding: utf-8 -*-
"""v3.5.0 — a identidade do dado passa a governar o ESTADO DA ETAPA e os BYTES.

Cada teste aqui é um cenário que a v3.4.0 não cobria e no qual ela falhava.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from poi_estadual import procedencia as prov      # noqa: E402
from poi_estadual.config import Config            # noqa: E402
from poi_estadual.manifest import Manifesto       # noqa: E402

BBOX = (-64.8253, -1.5806, -58.8869, 5.2718)


def _cfg(tmp, **kw):
    return Config(uf=kw.pop("uf", "RR"), fontes=kw.pop("fontes", ("osm",)),
                  base_dir=str(tmp), **kw).preparar()


def _proc(man, cfg, fonte, snap):
    col, wk = prov.ids_da_fonte(fonte, snap, cfg, BBOX)
    man.procedencia({fonte: {"snapshot": snap, "collection_id": col, "work_id": wk}})
    return col, wk


# ------------------------------------------------- P0: snapshot novo invalida fetch
def test_snapshot_novo_deixa_fetch_stale(tmp_path):
    """v3.4.0: `snapshot_changed=True`, `collection_changed=True`,
    `fetch_reusable_after=True`. O sistema sabia que o mundo mudou e reaproveitava."""
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    ago = prov.snapshot("osm", "01 Aug 2026", digest="md5a", algoritmo="md5")
    _proc(man, cfg, "osm", ago)
    man.iniciar("init"); man.concluir("init")
    man.iniciar("fetch"); man.concluir("fetch", osm_nodes=10)
    assert man.reutilizavel("fetch")

    setembro = prov.snapshot("osm", "01 Sep 2026", digest="md5b", algoritmo="md5")
    _proc(man, cfg, "osm", setembro)
    assert not man.reutilizavel("fetch"), "snapshot novo tem de tornar o fetch stale"
    assert man.obsoleta("fetch")


def test_mesma_linhagem_continua_reutilizavel(tmp_path):
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    snap = prov.snapshot("osm", "01 Aug 2026", digest="md5a", algoritmo="md5")
    _proc(man, cfg, "osm", snap)
    man.iniciar("init"); man.concluir("init")
    man.iniciar("fetch"); man.concluir("fetch")
    _proc(man, cfg, "osm", dict(snap))          # mesma identidade, outra leitura
    assert man.reutilizavel("fetch")


def test_fingerprint_propaga_para_a_cadeia(tmp_path):
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    _proc(man, cfg, "osm", prov.snapshot("osm", "A", digest="1", algoritmo="md5"))
    for e in ("init", "fetch", "raw", "territory", "normalize"):
        man.iniciar(e)
        man.concluir(e)
    _proc(man, cfg, "osm", prov.snapshot("osm", "B", digest="2", algoritmo="md5"))
    assert not any(man.reutilizavel(e) for e in ("fetch", "raw", "territory", "normalize"))


# --------------------------------------------------------- P0: pinned é literal
def test_pinned_fsq_le_a_release_fixada_e_nunca_a_ultima(tmp_path):
    """v3.4.0: manifesto dizia 2026-08-01 e o adapter consumia 2026-10-01."""
    from poi_estadual import foursquare
    chamadas = []

    def _fake(cfg, release=None):
        chamadas.append(release)
        return (release or "2026-10-01"), ["x.parquet"]

    cfg = _cfg(tmp_path, fontes=("osm",), source_mode="pinned")
    man = Manifesto(cfg)
    man.procedencia({"fsq": {"snapshot": prov.snapshot("fsq", "2026-08-01", digest="s",
                                                       algoritmo="sha256"),
                             "collection_id": "c", "work_id": "w"}})
    foursquare.listar_releases = _fake                     # injeta o dublê
    meta = foursquare._release(cfg, man)
    assert chamadas == ["2026-08-01"], "a release fixada tem de ir no pedido"
    assert meta["rel"] == "2026-08-01" and meta["release_fixada"]


def test_pinned_overture_exige_release_no_comando(tmp_path):
    from poi_estadual import overture
    cfg = _cfg(tmp_path, fontes=("overture",), source_mode="pinned")
    cmd = overture._cmd(cfg, "2026-08-19.1", (-1, -1, 1, 1), "/tmp/x.parquet")
    assert "--release" in cmd and "2026-08-19.1" in cmd
    with pytest.raises(RuntimeError, match="pinned"):
        overture._cmd(cfg, None, (-1, -1, 1, 1), "/tmp/x.parquet")


def test_release_indeterminada_fora_do_pinned_nao_bloqueia(tmp_path):
    from poi_estadual import overture
    cfg = _cfg(tmp_path, fontes=("overture",), source_mode="cache")
    cmd = overture._cmd(cfg, None, (-1, -1, 1, 1), "/tmp/x.parquet")
    assert "--release" not in cmd


def test_pinned_osm_sem_blob_falha(tmp_path):
    """`-latest` é fonte móvel: sem o blob do snapshot, `pinned` não pode baixar."""
    from poi_estadual import osm
    cfg = _cfg(tmp_path, source_mode="pinned")
    man = Manifesto(cfg)
    _proc(man, cfg, "osm", prov.snapshot("osm", "01 Aug 2026", digest="md5a",
                                         algoritmo="md5"))
    with pytest.raises(RuntimeError, match="blob do snapshot"):
        osm._pbf(cfg, man)


def test_pinned_osm_com_blob_usa_o_blob(tmp_path):
    from poi_estadual import osm
    cfg = _cfg(tmp_path, source_mode="pinned")
    man = Manifesto(cfg)
    snap = prov.snapshot("osm", "01 Aug 2026", digest="md5a", algoritmo="md5")
    _proc(man, cfg, "osm", snap)
    cofre = cfg.dir_fonte("osm", "snapshots", snap["snapshot_id"])
    blob = os.path.join(cofre, "source.osm.pbf")
    open(blob, "wb").write(b"conteudo-do-pbf-de-agosto")
    assert osm._pbf(cfg, man) == blob


# ------------------------------------------- P0: derivados OSM seguem o snapshot
def test_pbf_novo_gera_derivados_novos(tmp_path):
    """v3.4.0: `nodes/ways/relations` viviam sob `sig_osm()` e sobreviviam à troca
    do PBF — extração de agosto servida com PBF de setembro."""
    from poi_estadual import osm
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    _proc(man, cfg, "osm", prov.snapshot("osm", "A", digest="1", algoritmo="md5"))
    dir_a = osm._dir(cfg, man)
    _proc(man, cfg, "osm", prov.snapshot("osm", "B", digest="2", algoritmo="md5"))
    dir_b = osm._dir(cfg, man)
    assert dir_a != dir_b
    open(os.path.join(dir_a, "nodes.parquet"), "w").close()
    assert not os.path.exists(os.path.join(dir_b, "nodes.parquet"))


# ------------------------------------ cache preserva a identidade já resolvida
def test_cache_nao_reescreve_a_identidade_com_outro_algoritmo(tmp_path):
    """Snapshot oficial (md5 da Geofabrik) não pode virar outro id só porque a
    execução seguinte, sem rede, calculou um sha256 local do mesmo arquivo."""
    cfg = _cfg(tmp_path, source_mode="cache")
    man = Manifesto(cfg)
    oficial = prov.salvar(cfg, "osm", prov.snapshot(
        "osm", "Fri, 01 Aug 2026 03:00:00 GMT", digest="abc", algoritmo="md5",
        escopo="full", status="verified_latest"))
    prov.salvar(cfg, "ibge", prov.snapshot("ibge", "x", digest="y", algoritmo="sha256"))
    out = prov.resolver_todas(cfg, man, BBOX)
    assert out["osm"]["snapshot"]["snapshot_id"] == oficial["snapshot_id"]
    assert out["osm"]["snapshot"]["resolution_status"] == "cached"


# -------------------------------------------- IDs por fonte, sem acoplamento
def test_ids_sao_especificos_por_adapter(tmp_path):
    cfg = _cfg(tmp_path, fontes=("osm",))
    outro = _cfg(tmp_path / "b", fontes=("osm",), osm_sem_nome=False)
    sfsq = prov.snapshot("fsq", "2026-08-01")
    assert (prov.ids_da_fonte("fsq", sfsq, cfg, BBOX)[0]
            == prov.ids_da_fonte("fsq", sfsq, outro, BBOX)[0]), \
        "osm_sem_nome não pode mexer na collection do FSQ"

    strips = _cfg(tmp_path / "c", fontes=("osm",), fsq_strips=9)
    sov = prov.snapshot("overture", "2026-08-19.1")
    assert (prov.ids_da_fonte("overture", sov, cfg, BBOX)[1]
            == prov.ids_da_fonte("overture", sov, strips, BBOX)[1]), \
        "fsq_strips não pode mexer no work do Overture"

    tile = _cfg(tmp_path / "d", fontes=("osm",), ov_tile_graus=0.5)
    assert (prov.ids_da_fonte("overture", sov, cfg, BBOX)[1]
            != prov.ids_da_fonte("overture", sov, tile, BBOX)[1])
    assert (prov.ids_da_fonte("fsq", sfsq, cfg, BBOX)[1]
            != prov.ids_da_fonte("fsq", sfsq, strips, BBOX)[1])


# ------------------------------------------------------- semântica dos campos
def test_digest_declara_algoritmo_e_escopo():
    s = prov.snapshot("osm", "v", digest="deadbeef", algoritmo="sha256",
                      escopo="first_64mb")
    assert (s["content_digest"], s["digest_algorithm"], s["digest_scope"]) == \
        ("deadbeef", "sha256", "first_64mb")
    assert "content_sha256" not in s, "o nome mentia sobre algoritmo e escopo"


def test_is_latest_so_com_verified_latest(tmp_path):
    cfg = _cfg(tmp_path, source_mode="latest")
    man = Manifesto(cfg)
    # `latest` cuja consulta falhou e caiu no snapshot anterior
    prov.salvar(cfg, "osm", prov.snapshot("osm", "antigo", digest="a", algoritmo="md5",
                                          status="verified_latest"))
    caiu = dict(prov.carregar(cfg, "osm"), resolution_status="network_failed_fallback")
    _proc(man, cfg, "osm", caiu)
    from poi_estadual.exportacao import _procedencia
    linha = _procedencia(cfg, man).set_index("fonte").loc["osm"]
    assert linha["resolution_status"] == "network_failed_fallback"
    assert not linha["is_latest_at_collection"], \
        "modo latest com fallback não é latest verificado"


def test_workspace_id_nunca_e_none(tmp_path):
    man = Manifesto(_cfg(tmp_path))
    ws = man.d["workspace_id"]
    assert isinstance(ws, str) and len(ws) > 0 and ws.startswith("RR_")
