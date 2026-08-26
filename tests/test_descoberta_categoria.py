# -*- coding: utf-8 -*-
"""Descoberta por categoria no Maps — a substituta da descoberta do iFood.

A descoberta pelo iFood morreu em 26/08/2026: Turnstile interativo, API 404/403,
app blindado contra emulador, proxies estrangeiros. Toda rota FECHOU.

O Google Maps tem os botões de categoria, e cada busca devolve a lista daquele
ramo com nome (escrito pelo Google, sem OCR) e coordenada real do `!3d!4d`.
Medido em Canoas: 40 restaurantes por busca; em Bento Gonçalves, 3 POIs novos
que nenhuma outra fonte tinha.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import descobrir_maps as dm  # noqa: E402


def test_a_coordenada_vem_do_href_e_nao_do_centro():
    """O `!3d!4d` é o ponto REAL do lugar. Pegar o centro do mapa poria todos
    os resultados na mesma coordenada."""
    href = "/maps/place/Coco+Bambu/data=!3d-29.91622!4d-51.16444!16s"
    m = dm.UUID_COORD.search(href)
    assert m and float(m.group(1)) == -29.91622 and float(m.group(2)) == -51.16444


def test_as_categorias_cobrem_alem_de_comida():
    """O iFood deixou de ser só comida, e a base de saneamento quer todo
    comércio. A lista precisa ir além de restaurante."""
    cats = " ".join(dm.CATEGORIAS)
    for esperado in ("farmácias", "mercados", "pet shop", "óticas", "academias",
                     "material de construção", "hotéis"):
        assert esperado in cats, f"categoria ausente: {esperado}"


def test_so_o_diferente_vira_poi():
    """`_novos` é o que impede a descoberta de duplicar o que outra fonte já
    trouxe — a regra "rodar de novo ACRESCENTA, não repete"."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "def _novos(" in s
    i = s.index("def _novos(")
    corpo = s[i:i + 900]
    assert "abs(maps_lat" in corpo and "translate" in corpo, \
        "a dedup por nome+coordenada saiu do filtro de novos"


def test_o_recorte_pela_area_e_pelo_poligono():
    """A busca do Maps traz o entorno (15z); só entra o que está DENTRO do
    desenho, não o que está na caixa."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "ponto_no_poligono" in s, "voltou a aceitar tudo que o Maps devolveu"


def test_nao_depende_do_ifood():
    """Esta etapa existe justamente porque o iFood fechou. Ela não pode importar
    nada do caminho morto."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "extrair_ifood" not in s and "marketplace.ifood" not in s


def test_a_cidade_vem_da_area_e_nao_do_codigo():
    """CRAVAR A CIDADE CUSTOU CINCO POIs ERRADOS, 26/08/2026.

    A primeira versão gravava "Canoas" para todo mundo. A descoberta rodou em
    Bento Gonçalves e os cinco POIs novos entraram como sendo de Canoas — um
    deles chamado, literalmente, "Loja Todeschini BENTO GONÇALVES".

    Quem acusou foi o `test_coerencia_local` do próprio projeto, no mesmo dia:
    5 POIs a 81 km da cidade que declaram. Cidade errada não fica quieta — ela
    estraga o recorte de toda etapa seguinte, porque metade das consultas do
    sistema casa por nome de cidade.
    """
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    # O que importa é o que se GRAVA, não o que se menciona: "Canoas" aparece
    # no comentário que explica este próprio erro.
    i = s.index("dados = [(")
    insercao = s[i:i + 400]
    assert '"Canoas"' not in insercao, "a cidade voltou a ser cravada no insert"
    assert "cidade, uf" in insercao, "a cidade deixou de vir do polígono"
    assert "municipio_da_area" in s
