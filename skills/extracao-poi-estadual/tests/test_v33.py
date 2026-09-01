# -*- coding: utf-8 -*-
"""v3.3.0 — identidade do cache e leitura de `relation` do OSM.

O teste de relation usa um `.pbf` REAL, gerado com `osmium` na hora: node + way
fechado + relation multipolygon. Sem isso a leitura de relation não seria
verificável, que era o motivo de ela ter ficado de fora até a v3.2.0.
"""
import os
import sys

import pandas as pd
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from poi_estadual.config import Config          # noqa: E402
from poi_estadual.manifest import Manifesto     # noqa: E402


# ------------------------------------------- identidade física == identidade lógica
def _cfg(tmp, **kw):
    return Config(uf=kw.pop("uf", "RR"), fontes=("osm",), base_dir=str(tmp), **kw).preparar()


def test_min_conf_muda_o_diretorio_de_normalize(tmp_path):
    """A falha da v3.2.0: o manifesto marcava `normalize` OBSOLETA e o worker via
    `n_00000.parquet` no mesmo caminho e fazia `continue`."""
    a, b = _cfg(tmp_path, min_conf=0.0), _cfg(tmp_path, min_conf=0.9)
    assert a.dir_proc("normalize") != b.dir_proc("normalize")
    assert a.dir_proc("territory") == b.dir_proc("territory"), "min_conf não afeta o clip"


def test_excluir_muda_territory_mas_nao_raw(tmp_path):
    a = _cfg(tmp_path)
    b = _cfg(tmp_path, excluir=("1400472",))
    assert a.dir_proc("raw") == b.dir_proc("raw"), "coleta e consolidação são da UF inteira"
    assert a.dir_proc("territory") != b.dir_proc("territory")
    assert a.dir_proc("normalize") != b.dir_proc("normalize")


def test_predicado_osm_muda_o_cache_da_fonte(tmp_path):
    a = _cfg(tmp_path)
    b = _cfg(tmp_path, osm_predicado="classico")
    c = _cfg(tmp_path, osm_sem_nome=False)
    assert len({a.sig_osm(), b.sig_osm(), c.sig_osm()}) == 3
    assert a.dir_fonte("osm", a.sig_osm()) != b.dir_fonte("osm", b.sig_osm())
    assert a.dir_proc("raw") != b.dir_proc("raw")


def test_uf_diferente_no_mesmo_workspace_nao_se_mistura(tmp_path):
    rs = _cfg(tmp_path, uf="RS")
    sc = _cfg(tmp_path, uf="SC")
    assert rs.sig_ibge() != sc.sig_ibge()
    assert rs.dir_fonte("ibge", rs.sig_ibge()) != sc.dir_fonte("ibge", sc.sig_ibge())
    for etapa in ("init", "raw", "territory", "normalize"):
        assert rs.dir_proc(etapa) != sc.dir_proc(etapa)
    # RS e SC são da mesma macrorregião: o `.pbf` bruto PODE ser compartilhado
    assert rs.dir_fonte("osm", "pbf") == sc.dir_fonte("osm", "pbf")
    assert rs.sig_osm() == sc.sig_osm(), "nodes/ways da região Sul servem às duas UFs"


def test_hash_do_caminho_e_o_hash_da_etapa(tmp_path):
    c = _cfg(tmp_path)
    for etapa in ("init", "raw", "territory", "normalize"):
        assert c.dir_proc(etapa).endswith(os.sep + c.hash_etapa(etapa))


# ------------------------------------------------------------- OSM relation
def _pbf_minimo(destino):
    osmium = pytest.importorskip("osmium")
    import osmium.osm.mutable as mut
    w = osmium.SimpleWriter(str(destino))
    quadra = [(-29.9000, -51.1800), (-29.9000, -51.1790),
              (-29.9010, -51.1790), (-29.9010, -51.1800)]
    for i, (la, lo) in enumerate(quadra, start=1):
        w.add_node(mut.Node(id=i, location=(lo, la), tags={}))
    w.add_node(mut.Node(id=99, location=(-51.1795, -29.9005),
                        tags={"amenity": "pharmacy", "name": "Farmacia Teste"}))
    # way com o MESMO nome da relation -> tem de ser suprimido como membro
    w.add_way(mut.Way(id=10, nodes=[1, 2, 3, 4, 1],
                      tags={"building": "retail", "name": "Base Aerea Teste"}))
    w.add_relation(mut.Relation(
        id=20, members=[("w", 10, "outer")],
        tags={"type": "multipolygon", "aeroway": "aerodrome", "name": "Base Aerea Teste"}))
    w.close()
    return str(destino)


def test_relation_e_lida_e_posicionada_dentro_do_poligono(tmp_path):
    pytest.importorskip("duckdb")
    from poi_estadual import osm
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    pbf = _pbf_minimo(tmp_path / "mini.osm.pbf")

    assert osm._nodes(cfg, man, pbf) == 1
    assert osm._ways(cfg, man, pbf) == 1
    assert osm._relations(cfg, man, pbf) == 1, "a relation multipolygon tem de entrar"

    rel = pd.read_parquet(os.path.join(osm._dir(cfg), "relations.parquet"))
    assert rel["nome"].iloc[0] == "Base Aerea Teste"
    assert rel["categoria_orig"].iloc[0] == "aerodrome"
    assert rel["id_fonte"].iloc[0] == "relation/20"
    assert rel["osm.geom_origem"].iloc[0] == "poligono"
    # ponto dentro do quadrado dos nós 1..4
    assert -29.9010 < float(rel["lat"].iloc[0]) < -29.9000
    assert -51.1800 < float(rel["lon"].iloc[0]) < -51.1790


def test_membro_com_o_mesmo_nome_da_relation_e_suprimido(tmp_path):
    pytest.importorskip("duckdb")
    from poi_estadual import osm
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    pbf = _pbf_minimo(tmp_path / "mini.osm.pbf")
    osm._nodes(cfg, man, pbf)
    osm._ways(cfg, man, pbf)
    osm._relations(cfg, man, pbf)

    sup = osm.suprimidos(cfg)
    assert list(sup["id_fonte"]) == ["way/10"], "o way homônimo é a mesma feição"
    assert "relation/20" in sup["motivo"].iloc[0]
    # a farmácia dentro do polígono NÃO é membro e não pode ser suprimida
    assert "node/99" not in set(sup["id_fonte"])


def test_relation_sem_nome_proprio_nao_suprime_membro(tmp_path):
    """Sem nome/marca na relation não há identidade para sustentar a supressão."""
    osmium = pytest.importorskip("osmium")
    pytest.importorskip("duckdb")
    import osmium.osm.mutable as mut
    from poi_estadual import osm
    cfg = _cfg(tmp_path)
    man = Manifesto(cfg)
    p = str(tmp_path / "sem_nome.osm.pbf")
    w = osmium.SimpleWriter(p)
    for i, (la, lo) in enumerate([(-29.90, -51.18), (-29.90, -51.179),
                                  (-29.901, -51.179), (-29.901, -51.18)], start=1):
        w.add_node(mut.Node(id=i, location=(lo, la), tags={}))
    w.add_way(mut.Way(id=10, nodes=[1, 2, 3, 4, 1],
                      tags={"building": "retail", "name": "Loja Real"}))
    w.add_relation(mut.Relation(id=20, members=[("w", 10, "outer")],
                                tags={"type": "multipolygon", "leisure": "park"}))
    w.close()
    osm._nodes(cfg, man, p)
    osm._ways(cfg, man, p)
    osm._relations(cfg, man, p)
    assert len(osm.suprimidos(cfg)) == 0
