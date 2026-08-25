# -*- coding: utf-8 -*-
"""Rodar de novo sobre a mesma área só pode ACRESCENTAR.

Regra do dono do produto, 25/08/2026. Não é preferência de arquitetura: uma
segunda passada numa área já trabalhada custa dinheiro em captura e, se
duplicar, entrega ao cliente a mesma loja duas vezes na lista de oportunidade.

O projeto já foi mordido por isso. Em julho de 2026 os `recuperado_gemini`
acumularam 455 duplicatas porque o Gemini não devolve `place_id` e o dedup era
por ele: "Livraria Harmonia" entrou seis vezes. A base estava limpa antes, e
seguiu limpa até alguém escrever um caminho novo — porque o que a segurava era
o cuidado dos ingestores, não uma restrição.

Estes testes cobram a RESTRIÇÃO. Convenção não impede reinserção; índice impede.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402


@pytest.fixture()
def con():
    c = bc.conectar()
    c.autocommit = False
    with c.cursor() as cur:
        cur.execute("select id from tenants where ativo order by nome limit 1")
        cur.execute("select set_config('app.tenant_id', %s, false)",
                    (str(cur.fetchone()[0] if cur.rowcount else ""),))
    yield c
    c.rollback()
    c.close()


def test_dois_pois_com_o_mesmo_place_id_sao_recusados(con):
    """A identidade do ponto na fonte que o produziu.

    `g/11ft3cxch8` no Google, `estadual:<cluster>` na extração. Dois POIs com o
    mesmo valor são a mesma coisa contada duas vezes — e é assim que a lista de
    oportunidade entrega a mesma loja ao cliente em duas linhas.
    """
    import psycopg2
    with con.cursor() as cur:
        cur.execute("""insert into pois (nome, fonte, place_id, cidade, uf, status,
                                         match_valido, lat_origem, lng_origem)
                       values ('Teste A', 'teste', 'teste:duplica-1', 'Canoas', 'RS',
                               'teste', true, -29.9, -51.1)""")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("""insert into pois (nome, fonte, place_id, cidade, uf, status,
                                             match_valido, lat_origem, lng_origem)
                           values ('Teste B', 'outra', 'teste:duplica-1', 'Canoas', 'RS',
                                   'teste', true, -29.9, -51.1)""")


def test_poi_sem_place_id_continua_podendo_existir(con):
    """O índice é PARCIAL de propósito.

    O Gemini não devolve `place_id`, e esses POIs são deduplicados pela origem
    (`fonte` + `sessao` + `nome_original`). Um índice que cobrisse o vazio
    impediria o segundo POI sem place_id de existir — o oposto do que se quer,
    e a regressão que essa parcialidade previne.
    """
    with con.cursor() as cur:
        for n in ("Sem id 1", "Sem id 2"):
            cur.execute("""insert into pois (nome, fonte, place_id, cidade, uf, status,
                                             match_valido, lat_origem, lng_origem)
                           values (%s, 'teste', '', 'Canoas', 'RS', 'teste', true,
                                   -29.9, -51.1)""", (n,))
        cur.execute("select count(*) from pois where place_id = '' and fonte = 'teste'")
        assert cur.fetchone()[0] >= 2


def test_a_mesma_vista_do_street_view_nao_entra_duas_vezes(con):
    """A chave é (POI, ÂNGULO) — e o giro 360° são sete ângulos legítimos.

    `facade`, `g0`, `g60`, `g120`, `g180`, `g240`, `g300`: todos do mesmo ponto,
    todos válidos. O que não pode é a mesma vista voltar numa recaptura, que é
    pagar duas vezes pela mesma imagem e inflar a contagem de evidência.
    """
    import psycopg2
    with con.cursor() as cur:
        cur.execute("""insert into pois (nome, fonte, place_id, cidade, uf, status,
                                         match_valido, lat_origem, lng_origem)
                       values ('Ponto SV', 'teste', 'teste:sv-1', 'Canoas', 'RS',
                               'teste', true, -29.9, -51.1) returning id""")
        poi = cur.fetchone()[0]

        # Os sete ângulos do giro entram sem reclamação.
        for ang in ("facade", "g0", "g60", "g120", "g180", "g240", "g300"):
            cur.execute("""insert into streetview_imgs
                             (poi_id, storage_path, pano_id, angulo, lat, lng)
                           values (%s, %s, 'PANO-TESTE', %s, -29.9, -51.1)""",
                        (poi, f"t/{poi}/{ang}.jpg", ang))

        # A recaptura da mesma vista, não.
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("""insert into streetview_imgs
                             (poi_id, storage_path, pano_id, angulo, lat, lng)
                           values (%s, %s, 'PANO-OUTRO', 'facade', -29.9, -51.1)""",
                        (poi, f"t/{poi}/facade-2.jpg"))


def test_as_capturas_de_2022_nao_foram_sacrificadas(con):
    """O índice do Street View é restrito a `pano_id is not null`, e isso é
    escolha: as linhas de 2022 carregam as 6 duplicatas que existem hoje, e
    apagá-las para poder criar uma restrição seria destruir captura paga para
    satisfazer um índice."""
    with con.cursor() as cur:
        cur.execute("""select indexdef from pg_indexes
                        where schemaname='comercialradar'
                          and indexname='ux_streetview_poi_angulo'""")
        d = (cur.fetchone() or [""])[0]
    assert "pano_id IS NOT NULL" in d, "o índice deixou de poupar as capturas antigas"


def test_as_tres_pernas_da_regra_existem(con):
    """POI, imagem e vínculo de fonte. Quem vier procurar "o que impede
    duplicar" precisa achar as três, e não duas.

    O índice do POI é POR EMPRESA desde a migração 0034. Global, ele virava
    parede: com o POI sendo de cada empresa, a segunda concessionária na mesma
    cidade não conseguiria importar os mesmos pontos públicos. A promessa
    ("rodar de novo ACRESCENTA, não repete") continua — dentro da empresa, que
    é o escopo em que ela faz sentido."""
    with con.cursor() as cur:
        cur.execute("""select indexname from pg_indexes
                        where schemaname='comercialradar'
                          and indexname in ('ux_pois_place_id_por_empresa',
                                            'ux_streetview_poi_angulo',
                                            'ix_vinculo_poi_ativo')""")
        achados = {r[0] for r in cur.fetchall()}
    faltando = {"ux_pois_place_id_por_empresa", "ux_streetview_poi_angulo",
                "ix_vinculo_poi_ativo"} - achados
    assert not faltando, f"perna da regra ausente: {faltando}"
