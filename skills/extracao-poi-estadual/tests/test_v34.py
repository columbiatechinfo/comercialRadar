# -*- coding: utf-8 -*-
"""v3.4.0 — procedência temporal: SNAPSHOT ≠ COLLECTION ≠ WORK.

O risco que esta versão fecha não é "dado velho reaproveitado por hash errado"
(v3.3.0), é o oposto: **dado velho perfeitamente cacheado e perfeitamente auditado
como se fosse o snapshot atual**.
"""
import json
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from poi_estadual import procedencia as prov          # noqa: E402
from poi_estadual.config import Config, ConfigInvalida  # noqa: E402


def _cfg(tmp, **kw):
    return Config(uf=kw.pop("uf", "RR"), fontes=kw.pop("fontes", ("osm",)),
                  base_dir=str(tmp), **kw).preparar()


BBOX = (-64.8253, -1.5806, -58.8869, 5.2718)


# ------------------------------------------------ as três identidades são distintas
def test_versao_da_fonte_muda_o_snapshot():
    a = prov.snapshot("fsq", "2026-08-01", digest="aaa", algoritmo="sha256")
    b = prov.snapshot("fsq", "2026-09-01", digest="bbb", algoritmo="sha256")
    assert a["snapshot_id"] != b["snapshot_id"]
    assert a["determinado"] and b["determinado"]


def test_pbf_diferente_com_o_mesmo_nome_e_snapshot_diferente():
    """`sul-latest.osm.pbf` de agosto e de setembro têm o mesmo nome."""
    ago = prov.snapshot("osm", "Fri, 01 Aug 2026 03:00:00 GMT", digest="1111",
                        algoritmo="md5", escopo="full")
    set_ = prov.snapshot("osm", "Tue, 01 Sep 2026 03:00:00 GMT", digest="2222",
                         algoritmo="md5", escopo="full")
    assert ago["snapshot_id"] != set_["snapshot_id"]


def test_bbox_muda_a_collection_mas_nao_o_snapshot():
    s = prov.snapshot("overture", "2026-08-19")
    c1 = prov.collection_id(s, "RS", [-57.6, -33.7, -49.6, -27.0])
    c2 = prov.collection_id(s, "SC", [-53.8, -29.4, -48.3, -25.9])
    assert c1 != c2
    assert prov.collection_id(s, "RS", [-57.6, -33.7, -49.6, -27.0]) == c1


def test_plano_de_execucao_nao_muda_a_identidade_do_dado():
    """strips 7 → 9 e tile 1.0 → 0.5 mudam o `work_id`. Nada mais."""
    s = prov.snapshot("fsq", "2026-08-01")
    c = prov.collection_id(s, "RS", [1, 2, 3, 4])
    w7, w9 = prov.work_id(c, 1.0, 7), prov.work_id(c, 1.0, 9)
    assert w7 != w9
    assert prov.collection_id(s, "RS", [1, 2, 3, 4]) == c, "collection é estável"
    assert s["snapshot_id"] == prov.snapshot(
        "fsq", source_version="2026-08-01")["snapshot_id"], "snapshot é estável"


def test_snapshot_indeterminado_e_declarado():
    s = prov.snapshot("overture", None)
    assert s["snapshot_id"] == "indeterminado" and not s["determinado"]


def test_pinned_recusa_snapshot_indeterminado(tmp_path):
    cfg = _cfg(tmp_path, fontes=("overture",), source_mode="pinned")
    with pytest.raises(RuntimeError, match="pinned"):
        prov.exigir_determinado(cfg, {"overture": prov.snapshot("overture", None)})
    # com a versão resolvida, passa
    prov.exigir_determinado(cfg, {"overture": prov.snapshot("overture", "2026-08-19")})


def test_modo_define_se_consulta_a_rede(tmp_path):
    assert prov.consulta_permitida(_cfg(tmp_path, source_mode="latest"), True) is True
    assert prov.consulta_permitida(_cfg(tmp_path, source_mode="cache"), False) is False
    # v3.6.0: `pinned` NUNCA consulta — pin criado sozinho não é pin
    assert prov.consulta_permitida(_cfg(tmp_path, source_mode="pinned"), True) is False
    assert prov.consulta_permitida(_cfg(tmp_path, source_mode="pinned"), False) is False


def test_source_mode_invalido_recusado(tmp_path):
    with pytest.raises(ConfigInvalida):
        _cfg(tmp_path, source_mode="sempre")
    with pytest.raises(ConfigInvalida):
        _cfg(tmp_path, refresh_fontes=("inexistente",))


# --------------------------------------------- o plano volta a bater com o hash
def test_plano_de_execucao_entra_no_hash_da_coleta(tmp_path):
    """v3.3.0: mudar o tile mudava o diretório da fonte e o hash de `fetch` dizia
    "reaproveitado" — apontando para um diretório novo e vazio."""
    a = _cfg(tmp_path)
    b = _cfg(tmp_path, ov_tile_graus=0.5)
    c = _cfg(tmp_path, fsq_strips=9)
    assert a.hash_etapa("fetch") != b.hash_etapa("fetch")
    assert a.hash_etapa("fetch") != c.hash_etapa("fetch")


def test_parametro_de_performance_continua_neutro(tmp_path):
    a = _cfg(tmp_path)
    b = _cfg(tmp_path, threads=2, clip_chunk=1000, duckdb_memory="1GB")
    assert a.hashes() == b.hashes()


# --------------------------------------------------------------- malha do IBGE
def test_malha_local_diferente_gera_snapshot_diferente(tmp_path):
    """v3.3.0: `--malha-parquet` não entrava em identidade nenhuma; trocar o arquivo
    mantinha `sig_ibge` e o hash de `init` iguais."""
    a = tmp_path / "a.parquet"
    b = tmp_path / "b.parquet"
    a.write_bytes(b"malha-A" * 100)
    b.write_bytes(b"malha-B-diferente" * 100)
    ca = _cfg(tmp_path / "wa", malha_parquet=str(a))
    cb = _cfg(tmp_path / "wb", malha_parquet=str(b))
    sa = prov.resolver_ibge(ca, None)
    sb = prov.resolver_ibge(cb, None)
    assert sa["content_digest"] != sb["content_digest"]
    assert sa["digest_algorithm"] == "sha256" and sa["digest_scope"] == "full"
    assert sa["snapshot_id"] != sb["snapshot_id"]


# ------------------------------------------------------- release do Overture/FSQ
def test_release_de_caminhos():
    caminhos = ["s3://overturemaps-us-west-2/release/2026-06-25.0/theme=places/x.parquet",
                "s3://overturemaps-us-west-2/release/2026-08-19.1/theme=places/y.parquet"]
    assert prov.release_de_caminhos(caminhos) == "2026-08-19.1"
    assert prov.release_de_caminhos([]) is None
    assert prov.release_de_caminhos(["s3://outro/caminho/x"]) is None


def test_fsq_release_nova_muda_o_snapshot(tmp_path):
    cfg = _cfg(tmp_path, source_mode="latest")
    ago = prov.resolver_fsq(cfg, None, True, lambda: ("2026-08-01", ["a.parquet", "b.parquet"]))
    out = prov.resolver_fsq(cfg, None, True, lambda: ("2026-10-01", ["a.parquet", "b.parquet"]))
    assert ago["source_version"] == "2026-08-01"
    assert ago["snapshot_id"] != out["snapshot_id"], "release nova = snapshot novo"


def test_fsq_mesma_release_com_shards_diferentes_muda_o_snapshot(tmp_path):
    cfg = _cfg(tmp_path, source_mode="latest")
    a = prov.resolver_fsq(cfg, None, True, lambda: ("2026-08-01", ["a.parquet"]))
    b = prov.resolver_fsq(cfg, None, True, lambda: ("2026-08-01", ["a.parquet", "c.parquet"]))
    assert a["snapshot_id"] != b["snapshot_id"]


def test_modo_cache_nao_consulta_a_fonte(tmp_path):
    """`cache` não pode tocar a rede — nem para descobrir release."""
    cfg = _cfg(tmp_path, source_mode="cache")
    chamou = []

    def _listar():
        chamou.append(1)
        return "2026-08-01", ["a"]

    s = prov.resolver_fsq(cfg, None, prov.consulta_permitida(cfg, False), _listar)
    assert chamou == [] and not s["determinado"]


# ------------------------------------------------------------------ persistência
def test_snapshot_resolvido_persiste_e_historico_e_mantido(tmp_path):
    cfg = _cfg(tmp_path)
    s1 = prov.salvar(cfg, "fsq", prov.snapshot("fsq", "2026-08-01"))
    s2 = prov.salvar(cfg, "fsq", prov.snapshot("fsq", "2026-10-01"))
    assert prov.carregar(cfg, "fsq")["snapshot_id"] == s2["snapshot_id"]
    hist = os.listdir(cfg.dir_fonte("fsq", "snapshots"))
    assert "%s.json" % s1["snapshot_id"] in hist, "o snapshot anterior não é apagado"
    assert "%s.json" % s2["snapshot_id"] in hist


def test_snapshot_novo_gera_diretorio_de_colecao_novo(tmp_path):
    """É o mecanismo que impede a fonte de congelar: snapshot novo ⇒ collection nova
    ⇒ diretório novo ⇒ nenhum marcador `DONE` antigo é encontrado."""
    cfg = _cfg(tmp_path, fontes=("overture",))
    velho = prov.snapshot("overture", "2026-06-25.0")
    novo = prov.snapshot("overture", "2026-08-19.1")
    cv = prov.collection_id(velho, "RR", list(BBOX))
    cn = prov.collection_id(novo, "RR", list(BBOX))
    dv = cfg.dir_colecao("overture", cv, prov.work_id(cv, 1.0, 7))
    dn = cfg.dir_colecao("overture", cn, prov.work_id(cn, 1.0, 7))
    assert dv != dn
    open(os.path.join(os.makedirs(os.path.join(dv, "done"), exist_ok=True) or
                      os.path.join(dv, "done"), "0.ok"), "w").close()
    assert not os.path.exists(os.path.join(dn, "done", "0.ok"))


def test_workspace_e_run_sao_coisas_diferentes(tmp_path):
    from poi_estadual.manifest import Manifesto
    cfg = _cfg(tmp_path)
    m1 = Manifesto(cfg)
    ws, r1 = m1.d["workspace_id"], m1.d["run_id"]
    m1.salvar()
    import time as _t
    _t.sleep(1.05)
    m2 = Manifesto(cfg)
    assert isinstance(ws, str) and ws, "workspace_id não pode ser None"
    assert m2.d["workspace_id"] == ws, "o workspace é o mesmo diretório"
    assert m2.d["run_id"] != r1, "cada execução é uma execução"
    assert any(r["run_id"] == r1 for r in m2.d["runs"]), "a execução anterior fica no histórico"


def test_manifesto_registra_procedencia(tmp_path):
    from poi_estadual.manifest import Manifesto
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    snap = prov.snapshot("osm", "Fri, 01 Aug 2026 03:00:00 GMT", digest="abc",
                         algoritmo="md5", escopo="full")
    col = prov.collection_id(snap, "RR", list(BBOX))
    man.procedencia({"osm": {"snapshot": snap, "collection_id": col,
                             "work_id": prov.work_id(col, "osm_adapter:v5")}})
    assert man.snapshot("osm")["content_digest"] == "abc"
    assert man.colecao("osm")[0] == col
    assert man.retrieved_at("osm") == snap["retrieved_at"]
    with open(os.path.join(cfg.base_dir, "manifesto.json"), encoding="utf-8") as fh:
        assert json.load(fh)["procedencia"]["osm"]["snapshot"]["source_version"]
