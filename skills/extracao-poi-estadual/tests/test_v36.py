# -*- coding: utf-8 -*-
"""v3.6.0 — SNAPSHOT DECLARADO = BYTES CONSUMIDOS = CÓDIGO QUE PROCESSOU."""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from poi_estadual import procedencia as prov      # noqa: E402
from poi_estadual.config import Config            # noqa: E402
from poi_estadual.manifest import Manifesto       # noqa: E402

BBOX = (-64.8, -1.5, -58.8, 5.2)


def _cfg(tmp, **kw):
    return Config(uf="RR", fontes=kw.pop("fontes", ("osm",)), base_dir=str(tmp), **kw).preparar()


# ------------------- P0: bytes velhos nunca recebem identidade nova
def test_download_com_digest_divergente_nao_promove(tmp_path):
    """O caso reproduzido: `-latest` de AGOSTO em cache + snapshot de SETEMBRO."""
    destino = str(tmp_path / "source.osm.pbf")

    def _fake(url, parcial):                       # entrega bytes de agosto
        open(parcial, "wb").write(b"BYTES-DE-AGOSTO")

    import hashlib
    setembro = hashlib.md5(b"BYTES-DE-SETEMBRO").hexdigest()
    with pytest.raises(RuntimeError, match="digest do download nao confere"):
        prov.baixar_verificando("http://x/f.pbf", destino, digest=setembro,
                                algoritmo="md5", baixador=_fake)
    assert not os.path.exists(destino), "blob não verificado não pode existir"
    assert not os.path.exists(destino + ".part"), "o parcial é removido"


def test_download_com_digest_correto_promove(tmp_path):
    import hashlib
    destino = str(tmp_path / "source.osm.pbf")
    dados = b"BYTES-DE-SETEMBRO"

    def _fake(url, parcial):
        open(parcial, "wb").write(dados)

    prov.baixar_verificando("http://x/f.pbf", destino,
                            digest=hashlib.md5(dados).hexdigest(),
                            algoritmo="md5", baixador=_fake)
    assert open(destino, "rb").read() == dados


# -------------------------------- migração v3.5 → v3.6: legado é stale
def test_etapa_sem_fingerprint_e_stale(tmp_path):
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    man.d["etapas"]["fetch"] = {"status": "completed", "hash": cfg.hash_etapa("fetch"),
                                "em": None, "contagens": {}}      # manifesto v3.4/3.5
    assert not man.reutilizavel("fetch"), "legacy_without_fingerprint = STALE"
    assert man.obsoleta("fetch")


# ------------------------------------------- pinned não se autopina
def test_pinned_sem_snapshot_falha_sem_tocar_a_rede(tmp_path):
    cfg = _cfg(tmp_path, source_mode="pinned")
    man = Manifesto(cfg)
    assert prov.consulta_permitida(cfg, False) is False
    with pytest.raises(RuntimeError, match="exige snapshot ja resolvido"):
        prov.resolver_todas(cfg, man, BBOX)


def test_cache_sem_snapshot_falha(tmp_path):
    cfg = _cfg(tmp_path, source_mode="cache")
    with pytest.raises(RuntimeError, match="exige snapshot ja resolvido"):
        prov.resolver_todas(cfg, Manifesto(cfg), BBOX)


def test_observacao_sob_snapshot_indeterminado_e_proibida(tmp_path):
    import pandas as pd
    from poi_estadual.normalizacao import _identificar_observacoes
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    obs = pd.DataFrame({"fonte": ["osm"], "id_fonte": ["node/1"], "cluster_id": ["osm:node/1"]})
    with pytest.raises(RuntimeError, match="snapshot indeterminado"):
        _identificar_observacoes(obs, man)


# ------------------------------- versão do processador entra na linhagem
def test_versao_do_processador_invalida_a_etapa(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    snap = prov.snapshot("osm", "A", digest="1", algoritmo="md5")
    col, wk = prov.ids_da_fonte("osm", snap, cfg, BBOX)
    man.procedencia({"osm": {"snapshot": snap, "collection_id": col, "work_id": wk}})
    antes = man.fingerprint("normalize")
    monkeypatch.setitem(prov.PROCESSOR_VERSAO, "normalize", "normalize:v99")
    assert man.fingerprint("normalize") != antes, "algoritmo novo invalida artefato"


def test_adapter_no_work_id_das_tres_fontes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    for fonte in ("osm", "overture", "fsq"):
        snap = prov.snapshot(fonte, "v1")
        antes = prov.ids_da_fonte(fonte, snap, cfg, BBOX)[1]
        monkeypatch.setitem(prov.ADAPTER_VERSAO_FONTE, fonte, "%s_adapter:v999" % fonte)
        assert prov.ids_da_fonte(fonte, snap, cfg, BBOX)[1] != antes, fonte


def test_init_nao_depende_do_snapshot_de_poi(tmp_path):
    """`init` é IBGE. OSM mudar não pode torná-lo stale."""
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    ibge = prov.snapshot("ibge", "malha", digest="m", algoritmo="sha256")
    ci, wi = prov.ids_da_fonte("ibge", ibge, cfg, BBOX)
    a = prov.snapshot("osm", "A", digest="1", algoritmo="md5")
    ca, wa = prov.ids_da_fonte("osm", a, cfg, BBOX)
    man.procedencia({"ibge": {"snapshot": ibge, "collection_id": ci, "work_id": wi},
                     "osm": {"snapshot": a, "collection_id": ca, "work_id": wa}})
    fp_init, fp_fetch = man.fingerprint("init"), man.fingerprint("fetch")
    b = prov.snapshot("osm", "B", digest="2", algoritmo="md5")
    cb, wb = prov.ids_da_fonte("osm", b, cfg, BBOX)
    man.procedencia({"osm": {"snapshot": b, "collection_id": cb, "work_id": wb}})
    assert man.fingerprint("init") == fp_init, "init não consome OSM"
    assert man.fingerprint("fetch") != fp_fetch


# ---------------------------------------------- semântica dos campos
def test_release_do_ambiente_nao_e_verified_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERTURE_RELEASE", "2026-08-19.1")
    s = prov.resolver_overture(_cfg(tmp_path, fontes=("overture",)), None, True)
    assert s["source_version"] == "2026-08-19.1"
    assert s["resolution_status"] == "pinned", "informada != comprovadamente a mais nova"


def test_skill_versao_e_do_run_atual(tmp_path):
    cfg = _cfg(tmp_path)
    m1 = Manifesto(cfg)
    m1.d["skill_versao"] = "3.4.0"                 # workspace nascido numa versão antiga
    m1.salvar()
    m2 = Manifesto(cfg)
    assert m2.d["skill_versao"] != "3.4.0", "a versão do código é a desta execução"
    assert m2.d["workspace_criado_com"] == "3.4.0"
    assert m2.d["runs"][-1]["skill_versao"] == "3.4.0"
