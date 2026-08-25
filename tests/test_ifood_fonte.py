# -*- coding: utf-8 -*-
"""A fonte `ifood` da skill estadual: mapeamento de campos e recusa honesta.

POR QUE ESTE TESTE EXISTE

Duas coisas já saíram erradas aqui, e as duas eram invisíveis por serem
plausíveis:

1. `marca` vinha "RESTAURANT" em TODAS as lojas. O primeiro adapter pegava o
   primeiro `group` cujo nome diferisse do nome da loja — e o iFood manda cinco
   tipos de grupo, sendo que só `CHAIN` é rede. O campo ficava cheio, coerente e
   inteiramente falso. Um campo vazio se percebe; um campo errado, não.

2. `poi_estadual/vendor/__init__.py` importa `geopandas`. Importá-lo no topo
   fazia `detalhar()` — que é HTTP puro — exigir a pilha geoespacial inteira
   para buscar um CNPJ.

Skill VENDORADA: uma atualização dela sobrescreve o arquivo. Este teste avisa
em segundos.
"""
import json
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "skills", "extracao-poi-estadual"))

ifood = pytest.importorskip("poi_estadual.ifood")


# Resposta real de `/v1/merchants/{id}/extra`, reduzida ao que o adapter lê.
# Uma loja INDEPENDENTE: os grupos técnicos existem, `CHAIN` não.
LOJA = {
    "id": "e23e65a6-dd44-4004-bfcd-3b4db9ad9f6a", "shortId": 2041027,
    "name": "Açaí do Gordinho", "type": "RESTAURANT", "enabled": True,
    "areaCode": 51, "phoneIf": "996393228", "priceRange": "CHEAPEST",
    "userRatingCount": 60.0, "preparationTime": 20,
    "address": {"district": "Duque de Caxias", "city": "SAO LEOPOLDO",
                "state": "RS", "latitude": -29.796619, "longitude": -51.132236,
                "zipCode": "93037350", "streetName": "Rua Arthur Bernardes",
                "streetNumber": "429", "streetCompl": "Sala1"},
    "mainCategory": {"code": "AC1", "description": "Açaí"},
    "documents": {"MCC": {"type": "MCC", "value": 5812},
                  "CNPJ": {"type": "CNPJ", "value": "47162579000152"}},
    "groups": [{"type": "STORE_TYPE", "name": "RESTAURANT"},
               {"type": "BUSINESS_MODEL", "name": "Marketplace"},
               {"type": "COMPANY", "name": "IFOOD"}],
    "features": ["DELIVERY", "CANCELABLE"],
    "business": {"operations": ["TAKEOUT", "DELIVERY"],
                 "salesChannels": ["IFOOD", "IFOOD_SITE"]},
}

# A mesma resposta para uma loja de REDE. Só o `groups` muda.
REDE = dict(LOJA, id="outro-id", name="Burger King - Shop do Vale",
            groups=LOJA["groups"] + [{"type": "CHAIN", "name": "Burger King"},
                                     {"type": "REGION", "name": "Gravatai"}])


def test_o_cnpj_chega_ao_parquet():
    """É o campo que justifica a fonte existir: a ponte para a Receita."""
    assert ifood._linha(LOJA)["if.cnpj"] == "47162579000152"


def test_marca_so_sai_de_CHAIN():
    """O bug que ficou invisível por ser plausível."""
    assert ifood._linha(LOJA)["marca"] is None, \
        "loja independente não tem rede — marca preenchida aqui é invenção"
    assert ifood._linha(REDE)["marca"] == "Burger King"


def test_o_endereco_leva_o_numero():
    """Sem número não há porta, e sem porta não há prospecção."""
    ln = ifood._linha(LOJA)
    assert ln["if.numero"] == "429"
    assert "429" in ln["endereco_raw"]
    assert ln["cep"] == "93037350"


def test_o_telefone_junta_ddd_e_numero():
    """`areaCode` e `phoneIf` vêm separados; nenhum dos dois sozinho disca."""
    assert ifood._linha(LOJA)["telefone"] == "(51) 996393228"


def test_a_hierarquia_tem_os_dois_niveis():
    """`Açaí` sozinho não separa de uma loja de conveniência; `RESTAURANT` sim."""
    assert ifood._linha(LOJA)["categoria_hier"] == "RESTAURANT;Açaí"


def test_loja_sem_coordenada_e_recusada():
    """Ponto sem geometria não é POI — e entra como falha, não como linha vazia."""
    sem = dict(LOJA, address=dict(LOJA["address"], latitude=None, longitude=None))
    assert ifood._linha(sem) is None


def test_a_fonte_sem_sementes_para_em_vez_de_devolver_zero():
    """Zero em silêncio vira dataset sem iFood com cara de dataset completo."""
    with pytest.raises(RuntimeError, match="sementes"):
        ifood.executar(cfg=None, man=None, sementes=None)


def test_detalhar_nao_exige_geopandas():
    """O módulo é HTTP puro; a pilha geoespacial só entra ao montar o DataFrame."""
    assert "geopandas" not in sys.modules, \
        "importar poi_estadual.ifood puxou geopandas — o import tardio se perdeu"


def test_todas_as_nativas_existem_na_linha():
    """`NATIVAS` é declarada à mão: se divergir do `_linha`, o parquet fica torto."""
    ln = ifood._linha(LOJA)
    faltando = [c for c in ifood.NATIVAS if c not in ln]
    assert not faltando, "colunas declaradas e não produzidas: %s" % faltando


def test_sementes_de_arquivo_le_txt(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("# comentario\nabc\n\ndef\n", encoding="utf-8")
    assert ifood.sementes_de_arquivo(str(p))() == ["abc", "def"]


def test_sementes_ausentes_falam_da_enumeracao(tmp_path):
    """A mensagem tem de apontar para quem produz o arquivo, não para o arquivo."""
    with pytest.raises(RuntimeError, match="ENUMERACAO"):
        ifood.sementes_de_arquivo(str(tmp_path / "nao_existe.txt"))()


def test_proxies_de_arquivo(tmp_path):
    p = tmp_path / "px.txt"
    p.write_text("http://a:b@1.2.3.4:9000\n# nota\nhttp://5.6.7.8:9000\n", encoding="utf-8")
    assert ifood._proxies_do_pool(str(p)) == ("http://a:b@1.2.3.4:9000",
                                              "http://5.6.7.8:9000")


def test_a_fonte_esta_declarada_na_config():
    """Sem isto o `--fontes ifood` é recusado como fonte inválida."""
    from poi_estadual.config import FONTES_VALIDAS
    assert "ifood" in FONTES_VALIDAS


def test_config_recusa_ifood_sem_sementes():
    """Falhar no `init`, não depois dos ~421 MB do OSM."""
    from poi_estadual.config import Config, ConfigInvalida
    with pytest.raises(ConfigInvalida, match="ifood-ids"):
        Config(uf="RS", fontes=("ifood",), ifood_ids="")


def test_o_snapshot_assina_a_lista_de_sementes(tmp_path):
    """O iFood não publica release: a identidade da coleta é o conjunto de ids.

    Duas listas diferentes têm de dar snapshots diferentes — senão trocar de
    cidade reaproveitaria o cache da cidade anterior.
    """
    proc = pytest.importorskip("poi_estadual.procedencia")

    class _Cfg:
        source_mode = "latest"

    a = tmp_path / "a.txt"; a.write_text("id1\nid2\n", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("id1\nid2\nid3\n", encoding="utf-8")
    _Cfg.ifood_ids = str(a)
    sa = proc.resolver_ifood(_Cfg, None, True)
    _Cfg.ifood_ids = str(b)
    sb = proc.resolver_ifood(_Cfg, None, True)
    assert sa["determinado"] and sb["determinado"]
    assert sa["snapshot_id"] != sb["snapshot_id"], \
        "acrescentar uma cidade não mudou o snapshot — o cache serviria o escopo errado"
    assert sa["resolution_status"] == "verified_latest"


def test_json_real_continua_mapeando(tmp_path):
    """Guarda contra mudança de contrato do endpoint sem quebrar tudo em silêncio."""
    ln = ifood._linha(json.loads(json.dumps(LOJA)))
    for campo in ("fonte", "id_fonte", "nome", "lat", "lon", "categoria_orig"):
        assert ln[campo] is not None, "campo obrigatório vazio: %s" % campo
    assert ln["fonte"] == "ifood"
    assert ln["status"] == "ATIVA"
