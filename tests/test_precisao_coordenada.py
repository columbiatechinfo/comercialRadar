# -*- coding: utf-8 -*-
"""A precisão da coordenada: vocabulário, piso e onde ela aparece.

Nada aqui vai à rede. O que se prova é a REGRA — o que vira ponto no mapa, o
que se recusa a virar, e que a precisão viaja até a ficha e o filtro.
"""
from __future__ import annotations

import io
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# O vocabulário fechado, na ordem do melhor para o pior. Vive aqui repetido de
# propósito: se alguém acrescentar uma classe sem pensar, o teste força a
# decisão de onde ela entra na ordem — e é a ordem que o filtro e o piso usam.
CLASSES = ["porta", "porta_aprox", "via", "bairro", "municipio", "desconhecida"]


@pytest.fixture(scope="module")
def geo():
    return pytest.importorskip("geocodificar")


@pytest.fixture(scope="module")
def cad():
    return pytest.importorskip("cadastur")


# ── O piso ───────────────────────────────────────────────────────────────────

def test_ponto_grosseiro_nao_vira_poi(cad):
    """`bairro` (800 m) e `municipio` (5 km) NÃO entram no mapa.

    Sem este piso, a primeira execução criou 117 pontos no centróide da cidade
    — em Encantado, quatro estabelecimentos empilhados na mesma coordenada. É
    exatamente o que o resto do módulo promete nunca fazer.
    """
    assert cad.PISO_PARA_POI == ("porta", "porta_aprox", "via")
    for grosseira in ("bairro", "municipio", "desconhecida"):
        assert grosseira not in cad.PISO_PARA_POI


def test_quem_fica_abaixo_do_piso_registra_o_motivo():
    """Recusar sem dizer por quê faria o prestador ser reprocessado para sempre."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    assert "coordenada_grosseira:" in fonte
    assert "endereco_nao_encontrado" in fonte


def test_o_vinculo_e_gravado_em_lote_e_nao_so_no_fim(cad):
    """Uma execução estadual interrompida no meio deixou 4.442 POIs no banco
    sem que nenhum prestador apontasse para eles. Processo longo não pode
    depender de terminar para deixar o banco coerente."""
    assert hasattr(cad, "_descarregar")
    assert isinstance(cad.LOTE_VINCULO, int) and 0 < cad.LOTE_VINCULO <= 1000
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def gerar("):]
    corpo = corpo[:corpo.index("\n# ── Encadeamento")]
    # Uma chamada DENTRO do laço e uma no fim.
    assert corpo.count("_descarregar(con, marcas, ligados, gerados, simular)") == 2


# ── O geocodificador ─────────────────────────────────────────────────────────

def test_casar_texto_nunca_vale_porta(geo):
    """Nem quando o Photon responde `house`.

    Ele achou UMA casa naquele logradouro, não necessariamente o número pedido
    — e a diferença é tocar a campainha errada. `porta` fica reservado a quem
    encontra o ESTABELECIMENTO, que é só o painel do Maps.
    """
    for classe in geo._PHOTON.values():
        assert classe != "porta"
    for classe in geo._NOMINATIM.values():
        assert classe != "porta"
    # E o Maps, que encontra o negócio, ganha `porta`.
    fonte = io.open(RAIZ / "geocodificar.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def por_maps"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert '"porta"' in corpo


def test_resultado_fora_do_municipio_e_descartado(geo):
    """Nome de rua se repete no Brasil inteiro. Um resultado noutro município
    não é uma coordenada pior — é outro lugar."""
    assert geo._confere_municipio({"city": "Canoas"}, "Canoas")
    assert geo._confere_municipio({"display_name": "Rua X, Canoas, RS"}, "canoas")
    assert not geo._confere_municipio({"city": "Porto Alegre"}, "Canoas")
    # Sem município pedido, não há o que conferir.
    assert geo._confere_municipio({"city": "Qualquer"}, "")


def test_coordenada_fora_do_brasil_e_recusada(geo):
    assert geo._no_brasil(-29.9, -51.1)
    assert not geo._no_brasil(48.8, 2.3)        # Paris
    assert not geo._no_brasil(-29.9, 2.3)


def test_a_consulta_limpa_entra_sempre(geo):
    """A primeira versão só mandava a forma limpa quando o nome da rua NÃO
    aparecia no endereço cru — o que nunca acontece, já que ela é extraída
    dali. O endereço cru sozinho devolvia zero em 14 de 14."""
    qs = geo._consultas("Padre Urbano Thiesen  Esteio Santo Inácio CEP: 93290000",
                        "Esteio", "RS")
    assert len(qs) >= 2
    assert any(q.startswith("Padre Urbano Thiesen,") for q in qs)


def test_a_incerteza_acompanha_a_classe(geo):
    """O número serve para ordenar e filtrar. Classe sem raio declarado deixaria
    o filtro sem o que comparar."""
    for classe in ("porta", "porta_aprox", "via", "bairro", "municipio"):
        assert geo.INCERTEZA[classe] > 0
    assert (geo.INCERTEZA["porta"] < geo.INCERTEZA["porta_aprox"]
            < geo.INCERTEZA["via"] < geo.INCERTEZA["bairro"]
            < geo.INCERTEZA["municipio"])


def test_melhor_escolhe_a_classe_mais_fina(geo):
    a = {"precisao": "via"}
    b = {"precisao": "porta_aprox"}
    assert geo.melhor(a, b) is b
    assert geo.melhor(b, a) is b
    assert geo.melhor(None, a) is a
    assert geo.melhor(a, None) is a


# ── Onde ela aparece ─────────────────────────────────────────────────────────

def test_o_escritor_unico_grava_as_tres_colunas():
    """Todo POI que entra por qualquer caminho passa por `ingerir_registro`."""
    fonte = io.open(RAIZ / "realtime_ingest.py", encoding="utf-8").read()
    for coluna in ("coord_precisao", "coord_fonte", "coord_incerteza_m"):
        assert f'"{coluna}"' in fonte


def test_a_precisao_entra_no_merge_nao_destrutivo():
    """Uma etapa que não sabe de onde veio a coordenada não pode APAGAR a
    declaração de quem sabia. Enriquecer telefone não rebaixa o ponto."""
    fonte = io.open(RAIZ / "realtime_ingest.py", encoding="utf-8").read()
    bloco = fonte[fonte.index("_MERGE = ("):]
    bloco = bloco[:bloco.index(")\n")]
    assert "coord_precisao" in bloco


def test_a_ficha_traduz_o_codigo_para_frase():
    """`porta_aprox` não diz nada a quem vai a campo; "prédio certo, número
    aproximado" diz. E o raio vem junto, porque é o que separa "toque a
    campainha" de "procure na quadra"."""
    fa = pytest.importorskip("ficha_abas")
    for classe in CLASSES:
        frase = fa._formatar(classe, "precisao")
        assert frase != classe, f"`{classe}` não tem frase"
        if classe != "desconhecida":
            assert "m)" in frase or "km)" in frase, f"`{classe}` não declara o raio"


def test_nao_declarada_nao_e_apresentada_como_ruim():
    """`desconhecida` significa que ninguém conferiu, e NÃO que a coordenada é
    ruim. Confundi-las faria descartar 13 mil pontos que podem ser ótimos."""
    fa = pytest.importorskip("ficha_abas")
    frase = fa._formatar("desconhecida", "precisao").lower()
    assert "não declarada" in frase or "nao declarada" in frase
    for palavra in ("ruim", "inválida", "invalida", "errada"):
        assert palavra not in frase


def test_a_origem_composta_e_traduzida():
    """A origem vem como `photon:via` em alguns caminhos — quem responde e com
    que classe. Sem tratar o composto, a tela mostrava o código cru."""
    fa = pytest.importorskip("ficha_abas")
    assert fa._formatar("photon:via", "fonte_coord") != "photon:via"
    assert fa._formatar("cnefe:porta", "fonte_coord") != "cnefe:porta"


def test_o_mapa_entrega_a_precisao_em_cada_ponto():
    srv = io.open(RAIZ / "server.py", encoding="utf-8").read()
    assert "coalesce(p.coord_precisao, 'desconhecida')" in srv
    assert "coord_precisao coord_fonte coord_incerteza_m" in srv


def test_o_filtro_existe_e_vale_no_desenho():
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    assert "const PRECISOES" in js
    assert "precisaoFiltro" in js
    # e é aplicado em `visivel`, senão o chip acende e nada muda
    corpo = js[js.index("function visivel("):]
    corpo = corpo[:corpo.index("\nfunction ")]
    assert "precisaoFiltro.size" in corpo


def test_as_classes_do_filtro_cobrem_o_vocabulario():
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    bloco = js[js.index("const PRECISOES"):]
    bloco = bloco[:bloco.index("];")]
    for classe in CLASSES:
        assert f'key: "{classe}"' in bloco, f"o filtro não cobre `{classe}`"


def test_nao_declarada_nao_e_vermelha_no_filtro():
    """Pintar de vermelho o que significa "não conferido" faria descartar 13
    mil pontos que podem ser ótimos."""
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    linha = [l for l in js.splitlines() if 'key: "desconhecida"' in l][0]
    cor = re.search(r'cor: "(#[0-9a-fA-F]{6})"', linha).group(1)
    r, g, b = (int(cor[i:i + 2], 16) for i in (1, 3, 5))
    assert not (r > g + 40 and r > b + 40), f"`desconhecida` está avermelhada ({cor})"


def test_a_migracao_nao_inventa_precisao():
    """O retroativo só classifica o que se pode provar pelo caminho de
    ingestão. Rótulo errado é pior que coluna vazia, porque rótulo é
    acreditado."""
    sql = io.open(RAIZ / "migrations" / "0028_precisao_da_coordenada.sql",
                  encoding="utf-8").read()
    # A última regra é o balde: tudo que sobrou vira `desconhecida`.
    assert "coord_precisao = 'desconhecida'" in sql
    # E a extração estadual NÃO é tratada como pin do Google, apesar de ter
    # `maps_lat` — foi a descoberta que motivou a migração.
    corpo = sql[sql.index("fonte = 'estadual'") - 400:sql.index("fonte = 'estadual'")]
    assert "overture_osm" in corpo
