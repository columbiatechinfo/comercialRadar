# -*- coding: utf-8 -*-
"""Testes da v3.0.0 — cada caso aqui e uma falha MEDIDA na auditoria de
Canoas-RS e Santa Maria-RS sobre a v2.0.0, reproduzida como regressao.

Os nomes usados sao os dos casos reais entregues como um unico ponto.
"""
import os
import sys

import pandas as pd
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "poi_estadual", "vendor"))

from poi_estadual.config import Config, ConfigInvalida        # noqa: E402
from poi_estadual.vendor import dedup_v3 as dv                # noqa: E402
from poi_estadual.vendor import osm_pbf as op                 # noqa: E402
from poi_estadual.vendor import tratar_pois as tp             # noqa: E402
from poi_estadual.vendor.categorias_pt import traduzir        # noqa: E402

M = 1.0 / 111320.0          # ~1 m em grau de latitude
LAT0, LON0 = -29.9007193, -51.1804521   # base com 7 casas: coordenada realista


def poi(idf, nome, dm, seg="Serviços", cat="Serviço", tel=None, site=None,
        marca=None, conf=0.9, fonte="overture", dlon_m=0.0):
    return dict(id_fonte=idf, fonte=fonte, fontes=fonte, nome=nome, segmento=seg,
                categoria_pt=cat, categoria_orig=cat, telefone=tel, site=site, marca=marca,
                lat=round(LAT0 + dm * M, 7), lon=round(LON0 + dlon_m * M, 7),
                confianca=conf, confianca_classe=tp.classe_conf(conf))


def dedup(linhas, **kw):
    return dv.dedup_evidencia(pd.DataFrame(linhas), **kw)


# ------------------------------------------------ §1 fusao que apaga POI real
def test_token_de_contexto_nao_sustenta_fusao():
    """`Cheirin Bao Canoas Shopping` + `CVC Canoas Shopping Center`: o que sobra do
    nome depois de tirar o generico e o nome DO SHOPPING, compartilhado por todos."""
    linhas = [poi("a", "Cheirin Bao Canoas Shopping", 0),
              poi("b", "CVC Canoas Shopping Center", 8),
              poi("c", "Renner Canoas Shopping", 16),
              poi("d", "Riachuelo Canoas Shopping", 24)]
    ded, vin = dedup(linhas)
    assert len(ded) == 4, "nenhum desses e o mesmo estabelecimento"
    nucleo = ded.loc[ded.id_fonte == "a", "nucleo_discriminante"].iloc[0] or ""
    assert "canoas" not in nucleo and "shopping" not in nucleo
    assert "cheirin" in nucleo and "bao" in nucleo


def test_bancas_de_advocacia_no_mesmo_predio():
    """Quatro escritorios distintos: `advocacia` e generico, o resto nao casa."""
    linhas = [poi("a", "Mazzardo & Mosqueiro Advocacia", 0),
              poi("b", "Goncalves Advocacia", 5),
              poi("c", "Souza Nunes Advocacia", 10),
              poi("d", "Stephani Lietz Advocacia", 15)]
    assert len(dedup(linhas)[0]) == 4


def test_bairro_como_token_compartilhado():
    """`Casa de Carnes Camobi` + `Auto Posto Camobi` + `Restaurante Grill Camobi`."""
    linhas = [poi("a", "Casa de Carnes Camobi", 0, seg="Comércio Varejista", cat="Açougue"),
              poi("b", "Auto Posto Camobi", 10, seg="Automotivo", cat="Posto"),
              poi("c", "Restaurante Grill Camobi", 20, seg="Alimentação", cat="Restaurante"),
              poi("d", "Farmacia Camobi", 30, seg="Saúde", cat="Farmácia")]
    assert len(dedup(linhas)[0]) == 4


def test_setor_a_ate_e_nao_colapsa():
    """`_name_core` da v2 descarta token de 1 caractere — o unico diferenciador."""
    linhas = [poi("s%d" % i, "Setor %s" % L, i * 6) for i, L in enumerate("ABCDE")]
    assert len(dedup(linhas)[0]) == 5


def test_subconjunto_de_nome_nao_basta():
    """`Farmacia Universitaria` x `Abastecedora Universitaria`: token_set_ratio da 100
    quando um nome e subconjunto do outro. Jaccard nao."""
    linhas = [poi("a", "Farmacia Universitaria", 0, seg="Saúde", cat="Farmácia"),
              poi("b", "Abastecedora Universitaria", 6, seg="Comércio Varejista", cat="Atacado")]
    assert len(dedup(linhas)[0]) == 2


def test_nucleo_curto_exige_mesmo_segmento():
    """`Lancheria X9` x `Estacionamento X9` — mesma sigla, negocios diferentes."""
    linhas = [poi("a", "Lancheria X9", 0, seg="Alimentação", cat="Lanchonete"),
              poi("b", "Estacionamento X9", 5, seg="Automotivo", cat="Estacionamento")]
    assert len(dedup(linhas)[0]) == 2


def test_mesmo_estabelecimento_entre_fontes_ainda_funde():
    """A trava nao pode matar o objetivo do dedup."""
    linhas = [poi("ov1", "Banrisul Agencia Centro", 0, seg="Financeiro", cat="Banco"),
              poi("node/1", "Banrisul", 5, seg="Financeiro", cat="Banco",
                  conf=None, fonte="osm")]
    ded, _ = dedup(linhas)
    assert len(ded) == 1
    assert ded["fontes"].iloc[0] == "osm,overture"


# --------------------------------------------------------- §1 telefone e rede
def test_telefone_sozinho_e_longe_NAO_funde_mais():
    """PATCH LOCAL comercialRadar, 25/08/2026 — politica trocada, com medicao.

    Este teste cobrava o oposto: `Padaria Sao Jose` + `Panificadora Central` a
    120 m com o mesmo telefone FUNDIAM. Media-se no RS o que essa regra custava:
    das 11.676 fusoes suspeitas, 8.483 eram por telefone, e a IA julgou 400 pares
    sorteados dizendo que 74,4% das unioes por telefone sao estabelecimentos
    DISTINTOS — cerca de 7.800 lojas apagadas numa UF.

    No varejo brasileiro o mesmo numero atende dois negocios do mesmo dono, ou e
    o numero da galeria. Duas padarias do mesmo dono a 120 m sao o caso tipico,
    nao a excecao — e o nome, aqui, diz justamente que sao duas.

    O par nao se perde: sai marcado como candidato para o julgamento da camada
    de cima, que decide com dados completos e devolve confianca de 1 a 10.
    """
    linhas = [poi("ov1", "Padaria Sao Jose", 0, tel="(51) 3476-1122"),
              poi("node/2", "Panificadora Central", 120, tel="+55 51 34761122",
                  conf=None, fonte="osm")]
    ded, vin = dedup(linhas)
    assert len(ded) == 2, "telefone voltou a decidir sozinho"
    assert (vin["motivo"] == "candidato_contato_sem_apoio").any(),         "o par foi descartado em silencio em vez de virar candidato"


def test_telefone_perto_e_com_apoio_de_nome_funde():
    """A evidencia de telefone continua valendo — SOMADA a outra.

    E o caso que ela existe para resolver: a mesma loja vista por duas fontes,
    escrita de dois jeitos, a metros de distancia. `Padaria Sao Jose` e
    `Panificadora Sao Jose` compartilham o nucleo `sao jose`, e isso e o apoio.
    """
    linhas = [poi("ov1", "Padaria Sao Jose", 0, tel="(51) 3476-1122"),
              poi("node/2", "Panificadora Sao Jose", 8, tel="+55 51 34761122",
                  conf=None, fonte="osm")]
    ded, _ = dedup(linhas)
    assert len(ded) == 1, "a fusao legitima por telefone+nome deixou de acontecer"


def test_site_pesa_mais_que_telefone():
    """Dominio e mais discriminativo que numero de telefone.

    Era o inverso: telefone valia 100 e site 95. Um dominio com caminho aponta
    para UM estabelecimento; um telefone aponta para quem atende, que pode ser o
    dono de tres lojas.
    """
    import poi_estadual.vendor.dedup_v3 as dv
    linhas = [poi("ov1", "Loja Alfa", 0, site="https://x.com/alfa"),
              poi("node/2", "Comercio Beta", 9, site="https://x.com/alfa",
                  conf=None, fonte="osm")]
    ded, _ = dedup(linhas)
    assert len(ded) == 1, "site igual e perto deixou de fundir"
    assert dv.PARAMS["raio_contato_m"] == 20.0
    assert dv.PARAMS["tel_exige_apoio"] is True


def test_telefone_divergente_veta_nome_identico():
    linhas = [poi("a", "Subway", 0, marca="Subway", tel="(51) 3000-1111"),
              poi("b", "Subway", 20, marca="Subway", tel="(51) 3000-2222")]
    ded, vin = dedup(linhas)
    assert len(ded) == 2
    assert (vin["motivo"] == "veto_telefone_divergente").any()


def test_telefone_hub_nao_e_evidencia():
    """Telefone de call center/condominio aparece em muitos lugares e nao distingue."""
    linhas = [poi("h%d" % i, "Loja %d" % i, i * 30, tel="(51) 4004-0000") for i in range(6)]
    ded, _ = dedup(linhas)
    assert len(ded) == 6


def test_marca_igual_exige_proximidade_curta():
    linhas = [poi("a", "Subway", 0, marca="Subway"), poi("b", "Subway", 25, marca="Subway")]
    assert len(dedup(linhas)[0]) == 2
    perto = [poi("a", "Subway", 0, marca="Subway"), poi("b", "Subway", 8, marca="Subway")]
    assert len(dedup(perto)[0]) == 1


# ------------------------------------------------------------- §1 diametro
def test_diametro_limita_a_cadeia():
    """Diametro observado na auditoria: 85,2 m para raio de 30 m."""
    linhas = [poi("c%d" % i, "Bar do Zeca", i * 28) for i in range(5)]   # 112 m de ponta a ponta
    ded, vin = dedup(linhas)
    assert len(ded) > 1
    assert (vin["motivo"] == "recusa_diametro").any()
    assert len(dedup(linhas, diam_max_m=500.0)[0]) == 1


# ------------------------------------------------------------ §1 bug do NaN
def test_nan_nao_e_nome():
    assert tp._name_core(float("nan")) == ""
    assert dv.tokens_nome(float("nan")) == []
    linhas = [dict(poi("a", None, 0, seg="Saúde", cat="Farmácia")),
              dict(poi("b", None, 28, seg="Automotivo", cat="Oficina"))]
    ded, _ = dedup(linhas)
    assert len(ded) == 2, "dois POIs sem nome, de categorias diferentes, nao sao o mesmo"


def test_sem_nome_e_absorvido_por_nomeado_proximo():
    linhas = [poi("ov1", "Farmacia Sao Joao", 0, seg="Saúde", cat="Farmácia"),
              poi("node/9", None, 8, seg="Saúde", cat="Farmácia", conf=None, fonte="osm")]
    ded, _ = dedup(linhas)
    assert len(ded) == 1
    assert "absorcao_semnome" in ded["dedup_motivos"].iloc[0]
    assert len(dedup(linhas, semnome_modo="marcar")[0]) == 2


def test_coordenada_empilhada_nao_funde():
    """901 pontos (5,6%) dividem coordenada exata — centroide de CEP, nao posicao."""
    linhas = []
    for i in range(3):
        p = poi("e%d" % i, None, 0)
        p["lat"], p["lon"] = -29.91, -51.18          # 2 casas: ~1,1 km de incerteza
        linhas.append(p)
    ded, vin = dedup(linhas)
    assert len(ded) == 3
    assert (vin["motivo"] == "recusa_coord_suspeita").any()


# ------------------------------------------------------------------- §2 ancora
def test_ancora_osm_vence_overture_de_confianca_baixa():
    """1.682 fusoes nas duas cidades: ancora foi Overture em 100,0%. Determinismo,
    porque `baixa` empatava com `sem` e o id hexadecimal ordena antes de `node/`."""
    linhas = [poi("0ov", "Mercado Bom Preco", 0, conf=0.05),
              poi("node/9", "Mercado Bom Preco", 5, conf=None, fonte="osm")]
    ded, _ = dedup(linhas)
    assert len(ded) == 1 and ded["id_fonte"].iloc[0] == "node/9"


def test_ancora_alta_confianca_ainda_vence():
    linhas = [poi("0ov", "Mercado Bom Preco", 0, conf=0.95),
              poi("node/9", "Mercado Bom Preco", 5, conf=None, fonte="osm")]
    assert dedup(linhas)[0]["id_fonte"].iloc[0] == "0ov"


# --------------------------------------------------------------- §7 precisao
def test_sinais_de_precisao():
    d = dv.sinais_precisao(pd.DataFrame([
        {"lat": -29.91, "lon": -51.18}, {"lat": -29.91, "lon": -51.18},
        {"lat": -29.9123456, "lon": -51.1812345}]))
    assert d["precisao_coord_m"].iloc[0] == pytest.approx(1113.2, rel=1e-3)
    assert d["precisao_coord_m"].iloc[2] < 0.1
    assert list(d["coord_empilhada"]) == [2, 2, 1]


# ------------------------------------------------------- §3 e §4 endereco
def test_locality_nao_vira_bairro():
    df = pd.DataFrame([{"fonte": "overture", "id_fonte": "a", "nome": "Padaria",
                        "lat": LAT0, "lon": LON0, "categoria_orig": "bakery",
                        "endereco_raw": "Rua Nair, 192", "bairro": None,
                        "localidade_fonte": "CANOAS", "cep": "92035-270",
                        "telefone": None, "site": None, "email": None, "instagram": None,
                        "marca": None, "confianca": 0.9, "status": None,
                        "data_atualizacao": None, "categoria_hier": None}])
    t = tp.tratar(df, min_conf=0.0, dedup="none")
    assert t["localidade_fonte"].iloc[0] == "Canoas"
    assert t["bairro"].iloc[0] is None
    assert "Canoas" not in t["endereco_completo"].iloc[0]


def test_logradouro_troca_com_residuo():
    """`Cvc RS - Canoas Shopping, Avenida Guilherme Schell` — a rua real ia p/ o residuo."""
    pe = tp.parse_endereco("Cvc RS - Canoas Shopping, Avenida Guilherme Schell, 6000")
    assert pe["logradouro"].startswith("Avenida Guilherme Schell")
    assert pe["metodo"] == "heuristica_troca"


def test_logradouro_normal_nao_e_trocado():
    pe = tp.parse_endereco("Avenida Farroupilha, 1234, Centro")
    assert pe["logradouro"].startswith("Avenida Farroupilha")
    assert pe["metodo"] != "heuristica_troca"


# ------------------------------------------------------------ §6 taxonomia
@pytest.mark.parametrize("cat,hier,seg", [
    ("atms", None, "Financeiro"),
    ("transportation", None, "Serviços"),
    ("cosmetic_and_beauty_supplies", None, "Comércio Varejista"),
    ("categoria_inexistente_xyz", "eat_and_drink;bakery", "Alimentação"),
    ("categoria_inexistente_xyz", "health_and_medical", "Saúde"),
])
def test_hierarquia_resolve_outros(cat, hier, seg):
    assert traduzir(cat, hier)[1] == seg


def test_categoria_primaria_tem_precedencia():
    assert traduzir("bakery", "retail;whatever") == ("Padaria", "Alimentação")


# ------------------------------------------------------------- §5 predicado
def test_predicado_ampliado_cobre_as_chaves_que_faltavam():
    p = op._pred("tags", ampliado=True)
    for k in ("healthcare", "craft", "aeroway", "military", "railway", "man_made", "building"):
        assert k in p
    assert "healthcare" not in op._pred("tags", ampliado=False)


def test_predicado_ampliado_e_sql_valido():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    q = ("WITH t AS (SELECT MAP(['railway'],['station']) tags "
         "UNION ALL SELECT MAP(['railway'],['switch']) "
         "UNION ALL SELECT MAP(['amenity'],['pharmacy'])) "
         "SELECT count(*) FROM t WHERE %s" % op._pred("tags", ampliado=True))
    assert con.execute(q).fetchone()[0] == 2, "estacao e farmacia entram; chave de manobra nao"


def test_categoria_de_respeita_o_modo():
    tags = {"building": "retail", "name": "Loja"}
    assert op.categoria_de(tags, ampliado=True) == "retail"
    assert op.categoria_de(tags, ampliado=False) is None


# -------------------------------------------------------------- §8 defaults
def test_min_conf_default_zero():
    assert Config(uf="RS", fontes=("osm",)).min_conf == 0.0


def test_config_recusa_parametro_incoerente():
    with pytest.raises(ConfigInvalida):
        Config(uf="RS", fontes=("osm",), dedup_diam_max_m=10.0, dedup_raio_m=30)
    with pytest.raises(ConfigInvalida):
        Config(uf="RS", fontes=("osm",), dedup_modo="inventado")
    with pytest.raises(ConfigInvalida):
        Config(uf="RS", fontes=("osm",), osm_predicado="inventado")


def test_predicado_e_semnome_entram_no_hash_da_coleta():
    a = Config(uf="RS", fontes=("osm",))
    b = Config(uf="RS", fontes=("osm",), osm_predicado="classico")
    c = Config(uf="RS", fontes=("osm",), osm_sem_nome=False)
    assert a.hash_etapa("fetch") != b.hash_etapa("fetch") != c.hash_etapa("fetch")
    assert a.hash_etapa("init") == b.hash_etapa("init"), "init nao depende do predicado"


def test_parametro_de_dedup_nao_invalida_a_coleta():
    a = Config(uf="RS", fontes=("osm",))
    b = Config(uf="RS", fontes=("osm",), dedup_jaccard_min=0.9)
    assert a.hash_etapa("fetch") == b.hash_etapa("fetch")
    assert a.hash_etapa("dedup") != b.hash_etapa("dedup")


# --------------------------------------------------------- §11 gate semantico
def test_gate_semantico_enxerga_fusao_intra_fonte():
    """O criterio que a auditoria usou: mesma fonte, nomes divergentes, uma linha."""
    vin = pd.DataFrame([{"id_a": "a", "id_b": "b", "motivo": "nome", "dist_m": 10.0,
                         "score": 95.0, "aceito": True},
                        {"id_a": "a", "id_b": "c", "motivo": "nome", "dist_m": 10.0,
                         "score": 95.0, "aceito": True}])
    origem = {"a": ("overture", "cheirin bao"), "b": ("overture", "cvc"),
              "c": ("osm", "cheirin bao")}
    susp = dv.fusoes_suspeitas(vin, origem)
    assert list(susp["id_b"]) == ["b"], "so a intra-fonte com nome divergente e suspeita"


def test_determinismo_independe_da_ordem_de_entrada():
    linhas = [poi("ov1", "Banrisul Agencia Centro", 0, seg="Financeiro", cat="Banco"),
              poi("node/1", "Banrisul", 5, seg="Financeiro", cat="Banco", conf=None, fonte="osm"),
              poi("ov2", "Padaria Central", 60, seg="Alimentação", cat="Padaria")]
    a, _ = dedup(linhas)
    b, _ = dedup(list(reversed(linhas)))
    assert sorted(a["id_fonte"]) == sorted(b["id_fonte"])


# ------------------------------- v3.1.0: exclusao nao pode tocar o snapshot bruto
def _tem_rede():
    import urllib.request
    try:
        urllib.request.urlopen(
            "https://servicodados.ibge.gov.br/api/v1/localidades/estados/RR", timeout=20)
        return True
    except Exception:                                          # noqa: BLE001
        return False


@pytest.mark.skipif(not _tem_rede(), reason="sem rede para a malha IBGE")
def test_bbox_de_coleta_nao_depende_de_excluir(tmp_path):
    """`excluir` nao entra no hash de `fetch`; logo nao pode encolher a area lida.

    Ate a v3.0.0 o bbox saia do alvo: excluir um municipio de extremidade mudava a
    area coletada e a grade de tiles, com o hash do fetch dizendo que nada mudou."""
    from poi_estadual import ibge
    from poi_estadual.manifest import Manifesto

    inteiro = Config(uf="RR", fontes=("osm",), base_dir=str(tmp_path / "a")).preparar()
    # Uiramuta (1400472) fica na extremidade norte de RR — encolhe o bbox se contar
    recortado = Config(uf="RR", fontes=("osm",), excluir=("1400472",),
                       base_dir=str(tmp_path / "b")).preparar()

    from poi_estadual import procedencia as _prov
    for c in (inteiro, recortado):
        _prov.salvar(c, "osm", _prov.snapshot("osm", "fix", digest="f", algoritmo="md5"))
    ga, bba = ibge.executar(inteiro, Manifesto(inteiro))
    gb, bbb = ibge.executar(recortado, Manifesto(recortado))

    assert len(gb) == len(ga) - 1, "o alvo encolhe"
    assert bba == bbb, "o bbox de COLETA nao pode encolher com --excluir"
    assert ibge.tiles(bba, 1.0) == ibge.tiles(bbb, 1.0), "a grade de tiles tem de ser a mesma"
    assert tuple(gb.total_bounds) != tuple(ga.total_bounds), \
        "o bbox do ALVO muda mesmo — e por isso que ele nao pode reger a coleta"
    assert inteiro.hash_etapa("fetch") == recortado.hash_etapa("fetch")
    assert inteiro.hash_etapa("territory") != recortado.hash_etapa("territory")


# --------------- v3.2.0: divisa municipal deixa de ser parede no matching
def test_grade_com_halo_da_o_mesmo_resultado_do_bloco_unico():
    """A particao so escolhe quais PARES sao avaliados; o cluster fecha uma vez, no
    global. Logo o tamanho da celula nao pode mudar o resultado."""
    linhas = []
    for i in range(40):
        linhas.append(poi("ov%d" % i, "Loja Alfa %d" % i, i * 37, dlon_m=i * 11))
    linhas.append(poi("node/1", "Loja Alfa 7", 7 * 37 + 4, dlon_m=7 * 11, conf=None, fonte="osm"))
    unico, _ = dedup(linhas)
    grade, _, _ = dv.dedup_particionado(pd.DataFrame(linhas), celula_m=300.0)
    assert len(grade) == len(unico) == 40
    assert sorted(grade["id_fonte"]) == sorted(unico["id_fonte"])
    assert sorted(grade["cluster_id"]) == sorted(unico["cluster_id"])


def test_par_na_divisa_nao_se_perde_mais():
    """Mesmo estabelecimento visto por duas fontes, a 5 m, em municipios diferentes.
    Com dedup particionado por municipio isso nunca era avaliado."""
    a = poi("ov1", "Supermercado Guarani", 0, seg="Comércio Varejista", cat="Supermercado")
    b = poi("node/7", "Supermercado Guarani", 5, seg="Comércio Varejista",
            cat="Supermercado", conf=None, fonte="osm")
    a["COD_MUNICIPIO"], b["COD_MUNICIPIO"] = "4304606", "4314902"   # lados opostos da divisa
    ded, _, _ = dv.dedup_particionado(pd.DataFrame([a, b]), celula_m=2000.0)
    assert len(ded) == 1, "a divisa nao pode impedir o match"
    assert ded["fontes"].iloc[0] == "osm,overture"

    # por municipio (o caminho da v3.1) os dois ficam separados — a regressao que se evita
    por_municipio = pd.concat([dv.dedup_evidencia(pd.DataFrame([a]))[0],
                               dv.dedup_evidencia(pd.DataFrame([b]))[0]], ignore_index=True)
    assert len(por_municipio) == 2


def test_halo_menor_que_o_raio_forte_e_recusado():
    with pytest.raises(ValueError):
        dv.dedup_particionado(pd.DataFrame([poi("a", "X", 0), poi("b", "Y", 50)]),
                              celula_m=1000.0, halo_m=10.0)


def test_cluster_id_e_a_chave_natural_da_ancora():
    linhas = [poi("0ov", "Mercado Bom Preco", 0, conf=0.05),
              poi("node/9", "Mercado Bom Preco", 5, conf=None, fonte="osm")]
    ded, _, obs = dv.dedup_auditado(pd.DataFrame(linhas))
    assert ded["cluster_id"].iloc[0] == "osm:node/9"
    assert set(obs["cluster_id"]) == {"osm:node/9"}
    assert obs["ancora"].sum() == 1, "exatamente uma observacao e ancora do cluster"
    assert len(obs) == 2, "nenhuma observacao se perde no elo"
