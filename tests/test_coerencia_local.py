# -*- coding: utf-8 -*-
"""Prova o portao de nome e a coerencia entre coordenada e cidade declarada.

Os tres nomes abaixo NAO sao inventados: sao os casos reais que escaparam ate
13/08/2026 e colaram a identidade de um estabelecimento em outro, a centenas de
quilometros. Se alguem baixar o limiar de novo, este teste reprova.
"""
import math
import os
import sys
import unicodedata

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402
from search_from_sheet import nome_match  # noqa: E402

REAIS = [
    ("Colegio diocesano- quadra", "Colégio Diocesano São Francisco"),
    ("Teresina Shopping - NÃO EXISTE", "Teresina Shopping"),
    ("ESF João XXIII", "UBS PSF João XXIII"),
    ("Restaurante Bamboo's", "Coco Bambu Teresina"),
]


@pytest.mark.parametrize("planilha,maps", REAIS)
def test_sem_distancia_nao_casa_por_similaridade_media(planilha, maps):
    """Sem coordenada nao da para distinguir homonimo de verdadeiro."""
    casou, _ = nome_match(planilha, maps, None)
    assert not casou, f"'{planilha}' casou com '{maps}' as cegas"


@pytest.mark.parametrize("planilha,maps", REAIS)
def test_com_distancia_grande_nunca_casa(planilha, maps):
    casou, _ = nome_match(planilha, maps, 300_000)
    assert not casou


def test_nome_praticamente_identico_ainda_passa_sem_distancia():
    """A trava nao pode matar o caso legitimo: mesmo nome, so acentuacao."""
    casou, _ = nome_match("Padaria Sao Jorge", "Padaria São Jorge", None)
    assert casou


def test_nenhum_poi_valido_longe_da_cidade_que_declara():
    """A varredura que achou os 73. Se voltar a crescer, algo no enriquecimento
    esta colando dado de outro lugar de novo."""
    def norm(s):
        return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                       if unicodedata.category(c) != "Mn")

    def hav(a, b, c, d):
        R = 6371.0
        p1, p2 = math.radians(a), math.radians(c)
        dp, dl = p2 - p1, math.radians(d - b)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(h))

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select id, cidade, uf,
                              coalesce(maps_lat, lat_origem), coalesce(maps_lng, lng_origem)
                         from pois
                        where cidade is not null and uf is not null
                          and coalesce(maps_lat, lat_origem) is not null
                          and match_valido is not false""")
        linhas = cur.fetchall()
    con.close()

    ref = bc.conectar_referencia()
    with ref.cursor() as rc:
        rc.execute("""select upper(nome), uf, ST_Y(ST_Centroid(geom)),
                             ST_X(ST_Centroid(geom)) from ibge_malha""")
        cent = {(norm(n), u): (la, lo) for n, u, la, lo in rc.fetchall()}
    ref.close()

    fora = [i for i, ci, uf, la, lo in linhas
            if cent.get((norm(ci), uf)) and hav(la, lo, *cent[(norm(ci), uf)]) > 50]
    assert not fora, (
        f"{len(fora)} POIs validos a mais de 50 km da cidade que declaram: {fora[:10]}")
