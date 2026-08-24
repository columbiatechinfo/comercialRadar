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

    # O POLIGONO, e nao a distancia do centroide.
    #
    # A regra antiga — "a mais de 50 km do centroide" — reprovava ponto
    # LEGITIMO em municipio grande: Santa Vitoria do Palmar tem 5.244 km2, e a
    # cidade fica a mais de 50 km do centro geometrico do municipio. Quatro
    # pontos corretos apareciam como erro.
    #
    # E deixava passar o inverso: um ponto a 40 km, dentro de outro municipio,
    # nao era acusado. Contencao no poligono nao tem nenhum dos dois problemas.
    #
    # UMA CONSULTA, e nao uma por POI. A primeira versao consultava o PostGIS
    # ponto a ponto: 31 mil idas ao banco, nove minutos, e a conexao caiu antes
    # de terminar. O `unnest` manda tudo de uma vez e o banco resolve o
    # conjunto — que e o que ele faz bem.
    # BANCO DE REFERENCIA FORA DO AR PULA, e nao reprova.
    #
    # A malha do IBGE mora noutra instancia (ADR 0003), no i9. Quando aquela
    # maquina cai — aconteceu em 24/08/2026 —, este teste acusava um defeito de
    # dado que nao existe. Teste que reprova por indisponibilidade da fonte
    # ensina a ignorar teste vermelho, que e o pior que pode acontecer com uma
    # suite.
    try:
        ref = bc.conectar_referencia()
    except Exception as e:
        pytest.skip(f"banco de referencia (malha do IBGE) inacessivel: "
                    f"{type(e).__name__}")
    with ref.cursor() as rc:
        rc.execute("select cod_municipio, upper(nome), uf from ibge_malha")
        cod_de = {}
        for cod, nome_m, u in rc.fetchall():
            cod_de.setdefault((norm(nome_m), u), cod)

        ids, cods, las, los = [], [], [], []
        for i, ci, uf, la, lo in linhas:
            cod = cod_de.get((norm(ci), uf))
            if not cod:
                continue          # sem malha daquele municipio: nao da para julgar
            ids.append(i); cods.append(cod); las.append(float(la)); los.append(float(lo))

        # DUAS CLASSES DE ERRO, e confundi-las custa caro nos dois sentidos.
        #
        # DESLOCAMENTO: o ponto esta noutro lugar. Medido em 24/08/2026, quando
        # o geocodificador por endereco entrou: 171 km, 270 km e 499 km — o
        # ultimo era Sarandi do PARANA em vez de Sarandi do RS. Isso e defeito,
        # e derruba a suite.
        #
        # ROTULO DE CIDADE: a coordenada esta certa e o campo `cidade` esta
        # errado. Canoas, Esteio e Sapucaia do Sul fazem divisa, e o Maps
        # atribui a cidade vizinha com frequencia — o "Zoologico sapucaia do
        # sul" esta gravado como Canoas, a 6,4 km da divisa. Sao 19 casos, de
        # 2 a 39 km, quase todos anteriores a esta medicao.
        #
        # A regra ANTIGA — 50 km do centroide — nao via nem uma coisa nem
        # outra: reprovava ponto legitimo em municipio grande (Santa Vitoria do
        # Palmar tem 5.244 km2) e deixava passar deslocamento de 40 km.
        LONGE_KM = 25          # acima disto e outro lugar, nao divisa
        ROTULO_CONHECIDOS = 19  # o que ja existia; nao pode CRESCER em silencio

        fora = []
        if ids:
            rc.execute("""
                select v.id,
                       ST_Distance(m.geom::geography,
                         ST_SetSRID(ST_MakePoint(v.lo, v.la), 4326)::geography) / 1000
                  from unnest(%s::bigint[], %s::text[], %s::float8[], %s::float8[])
                       as v(id, cod, la, lo)
                  join ibge_malha m on m.cod_municipio = v.cod
                 where not ST_DWithin(
                         m.geom::geography,
                         ST_SetSRID(ST_MakePoint(v.lo, v.la), 4326)::geography,
                         -- 2 km de folga: a malha e simplificada e um endereco
                         -- na divisa cai fora por dezenas de metros. E a mesma
                         -- tolerancia do `geocodificar.dentro_do_municipio`.
                         2000)""", (ids, cods, las, los))
            fora = [(i, float(d)) for i, d in rc.fetchall()]
    ref.close()

    longe = [(i, round(d, 1)) for i, d in fora if d > LONGE_KM]
    assert not longe, (
        f"{len(longe)} POIs a mais de {LONGE_KM} km do municipio que declaram — "
        f"isso e OUTRO LUGAR, nao divisa: {longe[:10]}")

    rotulo = [i for i, d in fora if d <= LONGE_KM]
    assert len(rotulo) <= ROTULO_CONHECIDOS, (
        f"{len(rotulo)} POIs fora do poligono do municipio que declaram (ate "
        f"{LONGE_KM} km), contra {ROTULO_CONHECIDOS} conhecidos. A coordenada "
        f"costuma estar certa e o campo `cidade` errado — mas a lista CRESCEU, "
        f"e crescer significa que algo novo esta gravando cidade errada: "
        f"{sorted(set(rotulo) )[:10]}")
