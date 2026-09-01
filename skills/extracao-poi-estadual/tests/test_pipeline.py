# -*- coding: utf-8 -*-
"""Testes de base da skill extracao-poi-estadual (parametrizacao, retomada, entrega).

Os testes das correcoes de exatidao da v3.0.0 estao em `test_v3.py`.

Cobrem o que quebrou na v1.0.0:
  - parametrizacao (nada de UF/municipio fixo no codigo);
  - retomada segura (hash de escopo por etapa + gate de precedencia);
  - schema do entregavel;
  - escape do mapa;
  - amostra minima ponta a ponta (raw -> validate) com fixture sintetica.

A amostra usa a malha real de RR (15 municipios, o menor estado) quando ha rede;
sem rede, o teste e pulado. Nenhum teste baixa Overture/OSM/FSQ.
"""
import json
import os
import sys
import urllib.request

import pandas as pd
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from poi_estadual.config import Config, ConfigInvalida  # noqa: E402
from poi_estadual.manifest import EtapaBloqueada, Manifesto  # noqa: E402


def _tem_rede():
    try:
        urllib.request.urlopen("https://servicodados.ibge.gov.br/api/v1/localidades/estados/RR",
                               timeout=20)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------- config
def test_uf_invalida_recusada():
    with pytest.raises(ConfigInvalida):
        Config(uf="XX")


def test_cod_ibge_precisa_7_digitos():
    with pytest.raises(ConfigInvalida):
        Config(uf="RS", excluir=("431490",))


def test_fsq_exige_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ConfigInvalida):
        Config(uf="RS", fontes=("fsq",))


def test_nenhum_default_amarrado_ao_rs():
    """A v1 tinha RS_BBOX/POA no nucleo. Nenhum default pode carregar UF ou municipio."""
    c = Config(uf="GO", fontes=("osm",))
    blob = json.dumps(c.campos_hash())
    assert "4314902" not in blob and "-57.65" not in blob
    assert c.rotulo == "POI_GO"


def test_hash_por_etapa_isola_a_coleta():
    """Mudar min_conf/excluir NAO pode invalidar `fetch` — senao a coleta refaz a toa."""
    a = Config(uf="RS", fontes=("osm",), min_conf=0.5, excluir=("4314902",))
    b = Config(uf="RS", fontes=("osm",), min_conf=0.9, excluir=("4314902",))
    c = Config(uf="RS", fontes=("osm",), min_conf=0.5, excluir=())
    assert a.hash_etapa("fetch") == b.hash_etapa("fetch") == c.hash_etapa("fetch")
    assert a.hash_etapa("normalize") != b.hash_etapa("normalize")
    assert a.hash_etapa("territory") != c.hash_etapa("territory")


def test_hash_muda_com_uf_e_fontes():
    a = Config(uf="RS", fontes=("osm",))
    assert a.hash_etapa("fetch") != Config(uf="SC", fontes=("osm",)).hash_etapa("fetch")
    assert a.hash_etapa("fetch") != Config(uf="RS", fontes=("osm", "overture")).hash_etapa("fetch")


def test_parametro_de_performance_nao_invalida_dado():
    a = Config(uf="RS", fontes=("osm",), treat_batch=40000, threads=8)
    b = Config(uf="RS", fontes=("osm",), treat_batch=5000, threads=2)
    assert a.hashes() == b.hashes()


# ------------------------------------------------------------------- manifesto
def test_gate_de_precedencia_bloqueia_pulo(tmp_path):
    cfg = Config(uf="RR", fontes=("osm",), base_dir=str(tmp_path)).preparar()
    man = Manifesto(cfg)
    with pytest.raises(EtapaBloqueada):
        man.iniciar("export")          # dedup nunca rodou


def test_etapa_fica_obsoleta_quando_parametro_muda(tmp_path):
    c1 = Config(uf="RR", fontes=("osm",), min_conf=0.5, base_dir=str(tmp_path)).preparar()
    m1 = Manifesto(c1)
    m1.iniciar("init")
    m1.concluir("init", municipios_alvo=15)
    for e in ("fetch", "raw", "territory"):
        m1.d["etapas"][e] = {"status": "completed", "hash": c1.hash_etapa(e),
                             "fingerprint": m1.fingerprint(e), "em": None, "contagens": {}}
    m1.salvar()

    c2 = Config(uf="RR", fontes=("osm",), min_conf=0.9, base_dir=str(tmp_path)).preparar()
    m2 = Manifesto(c2)
    assert m2.reutilizavel("fetch"), "coleta deveria sobreviver a mudanca de min_conf"
    assert m2.reutilizavel("territory")
    m2.iniciar("normalize")            # permitido: territory continua valido
    assert m2.status("normalize") == "running"


def test_parcial_nunca_vira_completo(tmp_path):
    cfg = Config(uf="RR", fontes=("osm",), base_dir=str(tmp_path)).preparar()
    man = Manifesto(cfg)
    man.iniciar("init")
    man.parcial("init", blocos_feitos=3, blocos_total=10)
    assert man.status("init") == "partial"
    assert not man.reutilizavel("init")
    with pytest.raises(EtapaBloqueada):
        man.iniciar("fetch")


def test_funil_fecha(tmp_path):
    cfg = Config(uf="RR", fontes=("osm",), base_dir=str(tmp_path)).preparar()
    man = Manifesto(cfg)
    man.funil("raw.bbox", 100, 90, "fora do bbox")
    assert man.funil_fecha() == []
    assert man.d["funil"][0]["descartados"] == 10


# ------------------------------------------------------------------------ mapa
def test_mapa_escapa_payload_hostil(tmp_path):
    """Nome/site vem de fonte aberta: `<img onerror>` nao pode chegar cru ao popup."""
    from poi_estadual import mapa
    hostil = '<img src=x onerror="alert(1)">'
    df = pd.DataFrame([{
        "nome": hostil, "fonte": "osm", "categoria_orig": "Mercado",
        "confianca_classe": "alta", "endereco_completo": 'Rua "A", 1',
        "telefone": "51999999999", "site": "javascript:alert(1)",
        "data_atualizacao": "2026-01-01", "lat": 2.82, "lon": -60.67,
        "NOME_MUNICIPIO": "Boa Vista"}])
    _, payload = mapa.construir(df)
    assert "esc(nome)" in mapa.TEMPLATE
    assert "safeUrl(site)" in mapa.TEMPLATE
    assert "'<span>'+nome+'</span>'" not in mapa.TEMPLATE
    import base64
    import gzip
    dados = json.loads(gzip.decompress(base64.b64decode(payload)).decode())
    nomes = [i[0] for c in dados["cidades"].values() for i in c["info"]]
    assert hostil in nomes            # o dado e preservado no payload...
    # ...e neutralizado na renderizacao: esc() cobre todo campo interpolado.
    for campo in ("esc(nome)", "esc(seg)", "esc(conf)", "esc(val)", "esc(site)", "esc(siteHref)"):
        assert campo in mapa.TEMPLATE


def test_safeurl_bloqueia_esquema_perigoso():
    """safeUrl so pode liberar http/https; qualquer outro esquema vira string vazia."""
    from poi_estadual import mapa
    corpo = mapa.TEMPLATE.split("function safeUrl(u){")[1].split("\nfunction ")[0]
    assert "/^https?:\\/\\//i.test(s)" in corpo
    assert "return ''" in corpo                       # esquema desconhecido -> vazio
    assert 'href="\'+esc(siteHref)+\'"' in mapa.TEMPLATE
    assert "'+site+'" not in mapa.TEMPLATE            # nunca cru


# -------------------------------------------------------------- amostra e2e
@pytest.mark.skipif(not _tem_rede(), reason="sem rede para a malha IBGE")
def test_amostra_minima_ponta_a_ponta(tmp_path):
    """raw -> validate sobre fixture sintetica, com malha real de RR."""
    from poi_estadual import exportacao, ibge, mapa, normalizacao, territorio, validacao
    from poi_estadual.config import salvar_atomico
    from poi_estadual.vendor import extrair_pois as ep

    cfg = Config(uf="RR", fontes=("osm",), base_dir=str(tmp_path),
                 formatos=("csv", "geoparquet"), min_conf=0.5).preparar()
    man = Manifesto(cfg)
    # v3.6.0: `cache` não materializa sob snapshot indeterminado — o workspace precisa
    # ter a versão da fonte resolvida antes (aqui, fixada como numa execução anterior).
    from poi_estadual import procedencia as _prov
    _prov.salvar(cfg, "osm", _prov.snapshot("osm", "fixture-2026-08", digest="fix",
                                            algoritmo="md5", escopo="full", status="pinned"))
    alvo_gdf, bbox = ibge.executar(cfg, man)
    assert len(alvo_gdf) == 15, "RR tem 15 municipios"

    # fixture: 3 POIs dentro de Boa Vista, 1 fora do bbox, 1 sem coordenada
    bv = alvo_gdf[alvo_gdf["NOME_MUNICIPIO"] == "Boa Vista"].geometry.representative_point().iloc[0]
    lon0, lat0 = float(bv.x), float(bv.y)
    linhas = []
    for i in range(3):
        linhas.append({"fonte": "osm", "id_fonte": "node/%d" % i, "nome": "Mercado %d" % i,
                       "lat": lat0, "lon": lon0, "categoria_orig": "supermarket",
                       "endereco_raw": "Rua A, %d" % (i + 1)})
    linhas.append({"fonte": "osm", "id_fonte": "node/900", "nome": "Fora",
                   "lat": -23.5, "lon": -46.6, "categoria_orig": "supermarket"})
    linhas.append({"fonte": "osm", "id_fonte": "node/901", "nome": "SemCoord",
                   "lat": None, "lon": None, "categoria_orig": "supermarket"})
    df = pd.DataFrame(linhas)
    for c in ep.COMUNS:
        if c not in df.columns:
            df[c] = None
    # v3.5.0: o cache de fonte vive sob collection/work — logo, sob o snapshot
    from poi_estadual import osm as _osm
    salvar_atomico(df[ep.COMUNS], os.path.join(_osm._dir(cfg, man), "nodes.parquet"))
    man.d["etapas"]["fetch"] = {"status": "completed", "hash": cfg.hash_etapa("fetch"),
                                "fingerprint": man.fingerprint("fetch"),
                                "em": None, "contagens": {}}
    man.versao_fonte("osm", arquivo="fixture", regiao="teste")

    assert territorio.consolidar(cfg, man, bbox) == 3      # 5 - fora - sem coord
    assert territorio.clipar(cfg, man, alvo_gdf) == 3
    assert normalizacao.normalizar(cfg, man) == 3
    saida = exportacao.executar(cfg, man)
    assert set(normalizacao.PADRAO) <= set(saida.columns)

    mapa.executar(cfg, man, saida)
    rel = validacao.executar(cfg, man, alvo_gdf)
    assert rel["resultado"] == "APROVADO", [c for c in rel["verificacoes"]
                                            if c["resultado"] == "FALHA"]
    assert rel["falhas"] == 0
    assert os.path.exists(os.path.join(cfg.base_dir, "manifesto.json"))
    assert man.funil_fecha() == []

    # A auditoria dos DESCARTES continua fazendo parte da entrega. A dos
    # VINCULOS saiu junto com a fusao em 01/09/2026 — sem fundir, nao ha par
    # para auditar. Ela volta na etapa da area, onde a fusao passou a morar.
    assert os.path.exists(cfg.arq_saida("poi_rejeitados_%s.csv" % cfg.rotulo.lower()))
    assert not os.path.exists(
        cfg.arq_saida("poi_dedup_vinculos_%s.csv" % cfg.rotulo.lower())), \
        "o passo 1 nao funde mais, entao nao pode publicar tabela de vinculos"
    rejeitados = normalizacao.carregar_rejeitados(cfg)
    assert len(rejeitados) == 2, "o fora-do-bbox e o sem-coordenada tem linha propria"
    assert set(rejeitados["etapa"]) == {"raw"}

    # E o entregavel sai CRU: endereco como a fonte escreveu, categoria idem.
    assert "categoria_pt" not in saida.columns, "traducao de categoria saiu do passo 1"
    assert "logradouro" not in saida.columns, "parse de endereco saiu do passo 1"
    assert "cluster_id" in saida.columns, "a identidade da linha continua sendo entregue"
    assert {"precisao_coord_m", "coord_empilhada", "localidade_fonte",
            "flag_localidade_divergente", "cluster_id"} <= set(saida.columns)
    assert "n_registros_fundidos" not in saida.columns, \
        "sem fusao no passo 1, ninguem foi fundido em ninguem"

    # A procedencia continua; o elo observacao->entidade saiu com a fusao em
    # 01/09/2026. Sem fundir, cada observacao E a entidade — o elo seria uma
    # copia do entregavel.
    assert os.path.exists(cfg.arq_saida("poi_source_snapshot_%s.csv" % cfg.rotulo.lower()))
    assert not os.path.exists(cfg.arq_saida("poi_observacoes_%s.csv" % cfg.rotulo.lower()))
