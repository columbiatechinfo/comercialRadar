# -*- coding: utf-8 -*-
"""Prova que o log registra, identifica quem fez, e NAO pode ser editado."""
import csv
import json
import os
import sys
import urllib.error
import urllib.request

import psycopg2
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
from conftest import ler_credenciais  # noqa: E402
import base_comum as bc  # noqa: E402

API = "http://127.0.0.1:8765"
ARQ = os.path.join(RAIZ, "USUARIOS-INICIAIS.csv")


def http(u, m="GET", b=None, t=None):
    r = urllib.request.Request(
        u, method=m, data=json.dumps(b).encode() if b is not None else None,
        headers={k: v for k, v in {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {t}" if t else None}.items() if v})
    try:
        with urllib.request.urlopen(r, timeout=40) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        pytest.skip(f"API indisponivel: {type(e).__name__}")


@pytest.fixture(scope="module")
def cred():
    if not os.path.exists(ARQ):
        pytest.skip("csv ausente")
    return ler_credenciais(ARQ)


def entrar(cred, email):
    u = cred[email]
    s, b = http(f"{API}/api/login", "POST", {"email": u["email"], "senha": u["senha"]})
    if s != 200:
        pytest.skip("login falhou")
    return b["access_token"]


def test_decisao_fica_registrada_com_autor(cred):
    """O valor do log e o AUTOR. Registrar a mudanca sem saber quem fez responde
    'o que mudou' e deixa 'quem mudou' sem resposta — que e a pergunta que se faz."""
    ta = entrar(cred, "admin.corsan@comercialradar.com.br")
    s, lst = http(f"{API}/api/usuarios", t=ta)
    sup = next(x for x in lst["usuarios"]
               if x["email"] == "supervisor.corsan@comercialradar.com.br")

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select p.id from pois p join core.tb_empresas t on t.id=p.id_empresa
                        where t.nome='Aegea - Corsan' order by p.id desc limit 1""")
        poi = cur.fetchone()[0]
    con.close()

    s, b = http(f"{API}/api/fila/atribuir", "POST",
                {"poi_ids": [poi], "supervisor_ids": [sup["id"]]}, t=ta)
    assert s == 201, b

    ts = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=ts)
    item = next(i for i in fila["itens"] if i["poi_id"] == poi)
    # Aprovar exige a revisao preenchida desde a 0009 — e o que o dossie afirma.
    s, _ = http(f"{API}/api/fila/{item['id']}/decidir", "POST",
                {"status": "aprovado",
                 "revisao": {"uso": "comercial", "atividade": "mercearia de esquina"}},
                t=ts)
    assert s == 200

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select usuario_id, acao, depois->>'status'
                         from auditoria
                        where tabela='atribuicao' and registro=%s
                        order by em desc limit 1""", (str(item["id"]),))
        r = cur.fetchone()
    con.close()
    assert r, "a decisao nao gerou registro de auditoria"
    assert str(r[0]) == sup["id"], f"autor errado no log: {r[0]}"
    assert r[1] == "UPDATE" and r[2] == "aprovado", r

    con = bc.conectar()
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("delete from atribuicao where poi_id=%s", (poi,))
    con.close()


def test_log_nao_pode_ser_editado_nem_apagado(cred):
    """Append-only por AUSENCIA de policy: com RLS ligada, o que nao tem policy
    e negado. Log que se pode editar nao serve para auditar."""
    def como(papel, var):
        return psycopg2.connect(
            host=os.environ["I9_POSTGRES_HOST"],
            port=int(os.environ.get("I9_POSTGRES_PORT", "5444")),
            user=papel, password=os.environ[var],
            dbname=os.environ.get("I9_POSTGRES_DB", "postgres"),
            options="-c search_path=radar_comercial,public")

    # A recusa e SILENCIOSA, e isso precisa estar escrito: sem policy de UPDATE
    # a RLS nao levanta erro, ela nao encontra linha — o resultado e zero linhas
    # afetadas. Protege igual, mas quem tentar adulterar ve "0 linhas" e nao uma
    # negativa. Afirmar `pytest.raises` aqui reprovaria um mecanismo que
    # funciona, que foi o que aconteceu na primeira versao deste teste.
    con = bc.conectar()          # worker so para SABER quantas linhas existem
    with con.cursor() as cur:
        cur.execute("select count(*) from auditoria")
        antes = cur.fetchone()[0]
    con.close()
    assert antes > 0, "sem registro nenhum, o teste nao prova nada"

    con = como("app_user", "CR_APP_PASSWORD")
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("select set_config('app.nivel','admin',true)")
        cur.execute("update auditoria set acao='ADULTERADO'")
        assert cur.rowcount == 0, f"{cur.rowcount} linhas do log foram alteradas"
        cur.execute("delete from auditoria")
        assert cur.rowcount == 0, f"{cur.rowcount} linhas do log foram apagadas"
    con.close()

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("select count(*) from auditoria where acao='ADULTERADO'")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from auditoria")
        assert cur.fetchone()[0] == antes, "o log encolheu"
    con.close()


def test_supervisor_nao_le_o_log(cred):
    con = psycopg2.connect(
        host=os.environ["I9_POSTGRES_HOST"],
        port=int(os.environ.get("I9_POSTGRES_PORT", "5444")),
        user="app_user", password=os.environ["CR_APP_PASSWORD"],
        dbname=os.environ.get("I9_POSTGRES_DB", "postgres"),
        options="-c search_path=radar_comercial,public")
    with con.cursor() as cur:
        cur.execute("select id from core.tb_empresas limit 1")
        cur.execute("select set_config('app.nivel','supervisor',true)")
        cur.execute("select count(*) from auditoria")
        assert cur.fetchone()[0] == 0, "supervisor leu o log de auditoria"
    con.close()
