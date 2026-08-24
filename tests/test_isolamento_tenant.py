# -*- coding: utf-8 -*-
"""Prova que uma empresa não alcança o dado de outra.

Este é o teste que impede a matriz do RBAC de virar documentação. Ele roda com o
papel REAL da API (`comercialradar_app`, sem BYPASSRLS), porque testar com o
worker provaria nada: BYPASSRLS ignora toda policy.

Quatro perguntas, e as quatro precisam de resposta:

  1. A empresa A LÊ linha da empresa B?
  2. A empresa A GRAVA carimbando a empresa B?  ← o `with check`
  3. Sem declarar a empresa, o que se vê?       ← fail-closed
  4. O `root` atravessa mesmo?

A pergunta 2 é a que costuma faltar. Sem ela, o cliente não *vê* a linha alheia
mas consegue *criar* uma dentro do vizinho — e ninguém percebe até o vizinho
reclamar de dado que não é dele.
"""
import os
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402

HOST = os.environ["I9_POSTGRES_HOST"]
PORT = int(os.environ.get("I9_POSTGRES_PORT", "5444"))
DB = os.environ.get("I9_POSTGRES_DB", "postgres")
OPCOES = "-c search_path=comercialradar,public"


def _conectar(papel: str, senha_var: str):
    senha = (os.environ.get(senha_var) or "").strip()
    if not senha:
        pytest.skip(f"{senha_var} ausente no .env")
    return psycopg2.connect(host=HOST, port=PORT, user=papel, password=senha,
                            dbname=DB, options=OPCOES, connect_timeout=20)


@pytest.fixture(scope="module")
def cenario():
    """Duas empresas, um POI em cada. Criado e removido pelo worker."""
    con = bc.conectar()
    con.autocommit = True
    ids = {}
    with con.cursor() as cur:
        for lado in ("A", "B"):
            cur.execute("insert into tenants (nome) values (%s) returning id",
                        (f"ZZ TESTE {lado} {uuid.uuid4().hex[:8]}",))
            ids[lado] = cur.fetchone()[0]
            cur.execute(
                """insert into pois (nome, fonte, lat_origem, lng_origem, tenant_id)
                   values (%s,'teste',-29.9,-51.2,%s) returning id""",
                (f"ZZ POI {lado}", ids[lado]))
            ids[f"poi_{lado}"] = cur.fetchone()[0]
    yield ids
    # `::uuid[]` explícito: psycopg2 manda a lista como text[], e o Postgres não
    # tem operador `uuid = text`. Sem o cast a limpeza falha e deixa lixo no banco.
    with con.cursor() as cur:
        cur.execute("delete from pois where tenant_id = any(%s::uuid[])",
                    ([str(ids["A"]), str(ids["B"])],))
        cur.execute("delete from tenants where id = any(%s::uuid[])",
                    ([str(ids["A"]), str(ids["B"])],))
    con.close()


def _como_empresa(tenant_id):
    """Conexão do app já declarando a empresa, como a API fará por requisição."""
    con = _conectar("comercialradar_app", "CR_APP_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    return con


def test_a_nao_le_o_poi_de_b(cenario):
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_B"],))
        assert cur.fetchone()[0] == 0, "empresa A alcançou o POI da empresa B"
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_A"],))
        assert cur.fetchone()[0] == 1, "empresa A não enxerga o próprio POI"
    con.close()


def test_a_nao_grava_carimbando_b(cenario):
    """O `with check`. Sem ele, A cria linha DENTRO de B.

    A exceção é `InsufficientPrivilege` (SQLSTATE 42501), não `CheckViolation`:
    violar o `with check` de uma policy é recusa de PRIVILÉGIO no Postgres, não
    de restrição de tabela. Esperar a classe errada faria este teste falhar com
    o isolamento funcionando — que foi o que aconteceu na primeira execução."""
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege) as erro:
            cur.execute(
                """insert into pois (nome, fonte, lat_origem, lng_origem, tenant_id)
                   values ('ZZ INVASOR','teste',-29.9,-51.2,%s)""", (cenario["B"],))
        assert "row-level security" in str(erro.value)
    con.rollback()
    con.close()


def test_sem_empresa_declarada_nao_ve_nada(cenario):
    """Fail-closed: variável ausente devolve NULL, e NULL não casa com nada.

    É o motivo de a policy nunca conter `or current_setting(...) is null` — esse
    OR é a 'correção' que aparece quando as consultas voltam vazias, e ele reabre
    a base inteira para qualquer conexão sem identidade."""
    con = _conectar("comercialradar_app", "CR_APP_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select count(*) from pois")
        assert cur.fetchone()[0] == 0, "conexão sem empresa declarada enxergou linhas"
    con.close()


def test_root_atravessa_as_empresas(cenario):
    con = _conectar("comercialradar_root", "CR_ROOT_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select count(*) from pois where id = any(%s)",
                    ([cenario["poi_A"], cenario["poi_B"]],))
        assert cur.fetchone()[0] == 2, "root não enxergou as duas empresas"
    con.close()


def test_policy_usa_indice_e_nao_varre(cenario):
    """RLS é avaliado POR LINHA. Sem índice começando por tenant_id, a policy
    vira o gargalo: em `cadastro_cliente` são 102 mil linhas por consulta."""
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        cur.execute("explain (analyze, buffers) select count(*) from cadastro_cliente")
        plano = " ".join(r[0] for r in cur.fetchall())
    con.close()
    assert "Seq Scan" not in plano, f"varredura completa apesar do índice:\n{plano}"
