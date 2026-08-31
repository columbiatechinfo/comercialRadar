# -*- coding: utf-8 -*-
"""Prova que as travas de nivel valem no SERVIDOR, nao so no botao escondido.

Cada teste aqui chama a rota direto, como faria quem abre o console do
navegador. Esconder o botao nao impede ninguem; a recusa tem de vir da API.
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
from conftest import ler_credenciais  # noqa: E402

API = "http://127.0.0.1:8765"
CSV = os.path.join(RAIZ, "USUARIOS-INICIAIS.csv")


def http(u, m="GET", b=None, t=None):
    r = urllib.request.Request(
        u, method=m, data=json.dumps(b).encode() if b is not None else None,
        headers={k: v for k, v in {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {t}" if t else None}.items() if v})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
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
    if not os.path.exists(CSV):
        pytest.skip("USUARIOS-INICIAIS.csv ausente")
    return ler_credenciais()


def entrar(cred, email):
    u = cred[email]
    s, b = http(f"{API}/api/login", "POST", {"email": u["email"], "senha": u["senha"]})
    if s != 200:
        pytest.skip(f"login falhou para {email}")
    return b["access_token"]


def test_user_nao_lista_usuarios(cred):
    t = entrar(cred, "user.corsan@comercialradar.com.br")
    s, _ = http(f"{API}/api/usuarios", t=t)
    assert s == 403


def test_supervisor_nao_cria_usuario(cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, _ = http(f"{API}/api/usuarios", "POST",
                {"nome": "ZZ Teste", "email": "zz1@exemplo.com", "nivel": "user"}, t=t)
    assert s == 403


def test_admin_nao_cria_root(cred):
    """A escada: admin criando root viraria root em dois passos."""
    t = entrar(cred, "admin.corsan@comercialradar.com.br")
    s, b = http(f"{API}/api/usuarios", "POST",
                {"nome": "ZZ Root", "email": "zz.root@exemplo.com", "nivel": "root"}, t=t)
    assert s == 403 and "root" in b.get("detail", "")


def test_admin_nao_cria_em_empresa_alheia(cred):
    """`id_empresa` do corpo e ignorado: admin herda a PROPRIA empresa."""
    ta = entrar(cred, "columbiatechinfo@gmail.com")
    s, emp = http(f"{API}/api/empresas", t=ta)
    outra = next(e["id"] for e in emp["empresas"] if e["nome"] == "Aegea - Piaui")

    # E-mail unico por execucao: desativar preserva o historico na NOSSA tabela,
    # mas a credencial continua existindo no GoTrue — e deve continuar mesmo,
    # senao "desativado" viraria "apagado" e o registro de quem aprovou o que
    # perderia o dono. Reusar o mesmo e-mail daria 409 na segunda rodada, o que
    # e o comportamento certo do servidor e um defeito do teste.
    import uuid as _uuid
    email = f"zz.invasor.{_uuid.uuid4().hex[:10]}@exemplo.com"
    t = entrar(cred, "admin.corsan@comercialradar.com.br")
    s, b = http(f"{API}/api/usuarios", "POST",
                {"nome": "ZZ Invasor", "email": email,
                 "nivel": "user", "id_empresa": outra}, t=t)
    assert s == 201, b
    novo = b["id"]

    # nasceu na empresa do admin, nao na que ele pediu
    s, lst = http(f"{API}/api/usuarios", t=t)
    achado = [x for x in lst["usuarios"] if x["id"] == novo]
    assert achado and achado[0]["empresa"] == "Aegea - Corsan"

    s, _ = http(f"{API}/api/usuarios/{novo}", "DELETE", t=t)
    assert s == 200


def test_admin_nao_desativa_a_si_mesmo(cred):
    t = entrar(cred, "admin.corsan@comercialradar.com.br")
    s, eu = http(f"{API}/api/eu", t=t)
    s, _ = http(f"{API}/api/usuarios/{eu['id']}", "DELETE", t=t)
    assert s == 422, "admin conseguiu se desativar e trancar a empresa para fora"


def test_empresas_e_recurso_de_administracao(cred):
    """`user` e `supervisor` ja recebem o nome da propria empresa no /api/eu.
    Listar empresas e porta de administracao — flagrado na vistoria de 13/08."""
    for email in ("user.corsan@comercialradar.com.br",
                  "supervisor.corsan@comercialradar.com.br"):
        t = entrar(cred, email)
        s, _ = http(f"{API}/api/empresas", t=t)
        assert s == 403, f"{email} alcancou /api/empresas (HTTP {s})"
