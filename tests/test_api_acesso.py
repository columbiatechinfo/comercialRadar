# -*- coding: utf-8 -*-
"""Prova a cadeia inteira pela HTTP: login, crachá, isolamento e guarda de nível.

O teste de isolamento vizinho (`test_isolamento_tenant.py`) prova a RLS no
banco. Este prova o que o usuário de fato encosta: a API. São coisas diferentes
— a policy pode estar perfeita e a rota entregar tudo mesmo assim, se conectar
com o papel errado.

Exige o servidor no ar e o `USUARIOS-INICIAIS.csv` gerado.
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

# O painel mudou de máquina em 24/08/2026: ele roda no i9, atrás do Caddy.
# O padrão continua o local para quem depura aqui; `CR_API_URL` aponta para
# onde ele de fato está — sem isso a suíte testaria um servidor que não é o
# que serve o usuário, e passaria dizendo nada.
API = os.environ.get("CR_API_URL", "http://127.0.0.1:8765").rstrip("/")
GW = f"http://{os.environ.get('I9_POSTGRES_HOST', '100.115.117.49')}:8000"
CHAVE = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
CSV = os.path.join(RAIZ, "USUARIOS-INICIAIS.csv")


def _http(url, metodo="GET", corpo=None, token=None, apikey=None):
    r = urllib.request.Request(
        url, method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={k: v for k, v in {
            "Content-Type": "application/json",
            "apikey": apikey,
            "Authorization": f"Bearer {token}" if token else None}.items() if v})
    try:
        with urllib.request.urlopen(r, timeout=25) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        pytest.skip(f"servico indisponivel: {type(e).__name__} {str(e)[:60]}")


@pytest.fixture(scope="module")
def credenciais():
    if not os.path.exists(CSV):
        pytest.skip("USUARIOS-INICIAIS.csv nao existe")
    with open(CSV, encoding="utf-8") as f:
        return {l["email"]: l for l in csv.DictReader(f)}


def _entrar(cred, email):
    u = cred.get(email)
    if not u or u["senha"].startswith("("):
        pytest.skip(f"sem senha utilizavel para {email}")
    s, b = _http(f"{GW}/auth/v1/token?grant_type=password", "POST",
                 {"email": email, "password": u["senha"]}, apikey=CHAVE)
    assert s == 200, f"login falhou para {email}: {s} {b}"
    return b["access_token"]


def test_sem_token_e_recusado():
    s, _ = _http(f"{API}/api/empresas")
    assert s == 401, "rota autenticada respondeu sem token"


def test_token_invalido_e_recusado():
    s, _ = _http(f"{API}/api/empresas", token="isto.nao.e.um.token")
    assert s == 401


def test_root_ve_todas_as_empresas(credenciais):
    t = _entrar(credenciais, "columbiatechinfo@gmail.com")
    s, eu = _http(f"{API}/api/eu", token=t)
    assert s == 200 and eu["nivel"] == "root"
    assert eu["fontes_visiveis"] is True, "root deveria ver a procedencia"
    s, b = _http(f"{API}/api/empresas", token=t)
    assert s == 200 and len(b["empresas"]) >= 4, b


def test_admin_ve_apenas_a_propria_empresa(credenciais):
    t = _entrar(credenciais, "admin.corsan@comercialradar.com.br")
    s, eu = _http(f"{API}/api/eu", token=t)
    assert s == 200 and eu["nivel"] == "admin"
    assert eu["fontes_visiveis"] is False, "admin NAO pode ver a procedencia"
    s, b = _http(f"{API}/api/empresas", token=t)
    assert s == 200, b
    nomes = [e["nome"] for e in b["empresas"]]
    assert nomes == ["Aegea - Corsan"], f"admin enxergou empresa alheia: {nomes}"


def test_admin_nao_cria_empresa(credenciais):
    """Criar empresa e exclusivo do root. Sem esta guarda, cada admin abriria
    concorrente dentro do proprio sistema."""
    t = _entrar(credenciais, "admin.corsan@comercialradar.com.br")
    s, _ = _http(f"{API}/api/empresas", "POST", {"nome": "ZZ NAO DEVIA EXISTIR"}, token=t)
    assert s == 403, f"admin conseguiu criar empresa (HTTP {s})"


def test_user_nao_cria_empresa(credenciais):
    t = _entrar(credenciais, "user.corsan@comercialradar.com.br")
    s, _ = _http(f"{API}/api/empresas", "POST", {"nome": "ZZ NAO DEVIA EXISTIR"}, token=t)
    assert s == 403


def test_root_cria_e_desativa(credenciais):
    t = _entrar(credenciais, "columbiatechinfo@gmail.com")
    s, b = _http(f"{API}/api/empresas", "POST", {"nome": "ZZ Empresa de Teste"}, token=t)
    assert s == 201, b
    eid = b["id"]
    s, b = _http(f"{API}/api/empresas/{eid}", "DELETE", token=t)
    assert s == 200 and b["ativo"] is False, b
