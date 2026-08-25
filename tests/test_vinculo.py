# -*- coding: utf-8 -*-
"""Desvincular uma fonte de um POI — a saída para a fusão que a máquina errou.

A fusão é feita com evidência incompleta e vai errar: medido no RS, 66,2% das
fusões suspeitas uniram estabelecimentos distintos. O que não pode é errar sem
saída. Estes testes cobram o comportamento da saída — sobretudo os casos em que
ela poderia destruir mais do que conserta.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402
import vinculo as V  # noqa: E402


@pytest.fixture()
def cenario():
    """Um POI feito de duas fontes que na verdade são dois negócios.

    `Bah Burger` e `Figurati Pizza`, unidos por telefone a 7 m — um par real da
    amostra de Canoas.
    """
    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    cur.execute("select id from tenants where ativo order by nome limit 1")
    tenant = str(cur.fetchone()[0])
    cur.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
    cur.execute("""
        insert into pois (nome, fonte, lat_origem, lng_origem, maps_lat, maps_lng,
                          place_id, cidade, uf, status, match_valido)
        values ('Bah Burger', 'estadual', -29.91, -51.18, -29.91, -51.18,
                'overture:teste-bah', 'Canoas', 'RS', 'teste', true)
        returning id""")
    poi = cur.fetchone()[0]
    V.vincular(con, poi, "overture", "teste-bah", 9, "regra_forte",
               nome="Bah Burger", lat=-29.91, lng=-51.18,
               dados={"categoria": "Hamburgueria", "telefone": "5133330000"})
    V.vincular(con, poi, "osm", "teste-figurati", 4, "ia",
               nome="Figurati Pizza Napolitana", lat=-29.9101, lng=-51.1801,
               dados={"categoria": "Pizzaria", "telefone": "5133330000"},
               motivo="telefone igual", modelo="qwen3vl-moe")
    yield con, poi
    con.rollback()
    con.close()


def test_a_ficha_mostra_uma_aba_por_fonte(cenario):
    con, poi = cenario
    abas = V.fontes_do_poi(con, poi)
    assert [a["fonte"] for a in abas] == ["overture", "osm"], \
        "a ordem de exibição mudou"
    osm = [a for a in abas if a["fonte"] == "osm"][0]
    assert osm["confianca"] == 4 and osm["confianca_origem"] == "ia"
    assert osm["dados"]["categoria"] == "Pizzaria", \
        "o que a fonte afirmou não chegou na aba"


def test_o_desvinculado_vira_poi_novo_e_o_resto_segue(cenario):
    """A regra central: o que sai ganha identidade própria, o que fica continua.

    Sem o POI novo, desvincular seria apagar — e o estabelecimento que a fusão
    tinha escondido sumiria de vez, agora por decisão nossa.
    """
    con, poi = cenario
    r = V.desvincular(con, poi, "osm", "teste-figurati", por="fulano@empresa")

    assert r["poi_novo"] != poi and r["restantes"] == 1

    with con.cursor() as cur:
        cur.execute("select nome, place_id from pois where id = %s", (r["poi_novo"],))
        nome, place_id = cur.fetchone()
    assert nome == "Figurati Pizza Napolitana", "o POI novo nasceu sem os dados da fonte"
    assert place_id == "osm:teste-figurati"

    # O POI de origem segue com a fonte que ficou.
    restantes = V.fontes_do_poi(con, poi)
    assert [a["fonte"] for a in restantes] == ["overture"]

    # E o novo tem a própria aba, agora com a confiança de quem decidiu: gente.
    novas = V.fontes_do_poi(con, r["poi_novo"])
    assert len(novas) == 1
    assert novas[0]["confianca"] == 10 and novas[0]["confianca_origem"] == "manual"


def test_desvincular_nao_apaga_a_trilha(cenario):
    """Trilha editada depois do fato vale menos que trilha completa.

    A linha antiga fica, com quem desfez, quando, e para onde o registro foi.
    """
    con, poi = cenario
    r = V.desvincular(con, poi, "osm", "teste-figurati", por="fulano@empresa")
    todas = V.fontes_do_poi(con, poi, incluir_desvinculados=True)
    morta = [a for a in todas if a["estado"] == "desvinculado"]
    assert len(morta) == 1
    assert morta[0]["desvinculado_por"] == "fulano@empresa"
    assert morta[0]["desvinculado_para"] == r["poi_novo"]
    assert morta[0]["desvinculado_em"] is not None


def test_a_ultima_fonte_nao_pode_sair(cenario):
    """Deixaria um ponto que nenhuma fonte afirma.

    Quem quer isso quer APAGAR o POI — outra operação, outra confirmação. Um
    ponto sem origem não some da tela; fica lá, sem ninguém conseguir explicar
    de onde veio.
    """
    con, poi = cenario
    V.desvincular(con, poi, "osm", "teste-figurati", por="fulano@empresa")
    with pytest.raises(V.NaoPodeDesvincular) as e:
        V.desvincular(con, poi, "overture", "teste-bah", por="fulano@empresa")
    assert "uma fonte só" in str(e.value)


def test_tirar_a_ancora_reancora_o_poi_de_origem(cenario):
    """Senão o ponto seguiria se apresentando como algo que já não é.

    O POI carrega nome e coordenada da fonte-âncora. Se ela sai e nada muda, a
    ficha continua dizendo "Bah Burger" sobre um ponto que agora é feito só do
    registro do OSM — e ninguém liga o sintoma à causa.
    """
    con, poi = cenario
    r = V.desvincular(con, poi, "overture", "teste-bah", por="fulano@empresa")
    assert r["virou_ancora"] is True
    with con.cursor() as cur:
        cur.execute("select nome, place_id from pois where id = %s", (poi,))
        nome, place_id = cur.fetchone()
    assert nome == "Figurati Pizza Napolitana"
    assert place_id == "osm:teste-figurati"


def test_confianca_fora_da_escala_e_recusada(cenario):
    """1 a 10, e o banco também cobra. Uma escala que aceita 47 não é escala."""
    con, poi = cenario
    with pytest.raises(ValueError):
        V.vincular(con, poi, "fsq", "x", 11, "ia")
    with pytest.raises(ValueError):
        V.vincular(con, poi, "fsq", "x", 0, "ia")


def test_o_mesmo_registro_nao_compoe_dois_pois_ao_mesmo_tempo(cenario):
    """Um registro de fonte afirma UM lugar. Se ele compusesse dois POIs, os
    dois estariam dizendo a mesma coisa sobre pontos diferentes."""
    con, poi = cenario
    with con.cursor() as cur:
        cur.execute("""
            insert into pois (nome, fonte, lat_origem, lng_origem, place_id,
                              cidade, uf, status, match_valido)
            values ('Outro', 'estadual', -29.9, -51.1, 'x:outro', 'Canoas', 'RS',
                    'teste', true) returning id""")
        outro = cur.fetchone()[0]
    import psycopg2
    with pytest.raises(psycopg2.errors.UniqueViolation):
        V.vincular(con, outro, "osm", "teste-figurati", 8, "ia")
    con.rollback()
