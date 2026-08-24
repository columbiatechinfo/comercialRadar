# -*- coding: utf-8 -*-
"""A área de trabalho grava mesmo, e a tela não pode mentir sobre isso.

O DEFEITO QUE ORIGINOU ESTE ARQUIVO (24/08/2026)

O usuário desenhou 4 quadras em Cachoeirinha, a tela disse "Área salva ✔", e a
mineração varreu **264 tiles do município de Canoas** — a área que estava no
banco desde 15/08, nove dias antes.

Duas metades, e as duas precisavam existir para o erro ficar mudo:

  SERVIDOR  `POST /api/area` respondia 500. Dentro de uma requisição,
            `realtime_ingest.conectar()` devolve a conexão do USUÁRIO, e
            `auth.conectar_como` só declara `app.tenant_id` quando o usuário tem
            empresa. O `root` não tem — é o único assim, de propósito. A trigger
            `preencher_tenant` não achava o que carimbar e o `NOT NULL` recusava
            a linha.

  FRONT     `salvarArea` chamava `fetch` sem olhar a resposta, e o chamador
            anunciava sucesso incondicionalmente.

Sozinho, o 500 teria aparecido na tela. Sozinha, a mentira do front não teria o
que esconder. Este arquivo cobra as duas.
"""
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402

API = os.environ.get("CR_API_URL", "http://127.0.0.1:8765")
GW = f"http://{os.environ.get('I9_POSTGRES_HOST', '100.115.117.49')}:8000"
CHAVE = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
CSV = os.path.join(RAIZ, "USUARIOS-INICIAIS.csv")

# Nome próprio: estes testes NUNCA tocam em `area_atual`. Escrever na área real
# durante uma suíte apagaria o recorte de quem estiver trabalhando.
NOME = "_teste_area_trabalho"
QUAD = [[-29.90, -51.15], [-29.90, -51.14], [-29.91, -51.14], [-29.91, -51.15]]


def _http(url, metodo="GET", corpo=None, token=None, apikey=None):
    r = urllib.request.Request(
        url, method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={k: v for k, v in {
            "Content-Type": "application/json",
            "apikey": apikey,
            "Authorization": f"Bearer {token}" if token else None}.items() if v})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        pytest.skip(f"servidor indisponivel: {type(e).__name__} {str(e)[:60]}")


@pytest.fixture(scope="module")
def token_root():
    if not os.path.exists(CSV):
        pytest.skip("USUARIOS-INICIAIS.csv nao existe")
    with open(CSV, encoding="utf-8") as f:
        cred = {l["email"]: l for l in csv.DictReader(f)}
    u = next((v for v in cred.values() if v["nivel"] == "root"), None)
    if not u or u["senha"].startswith("("):
        pytest.skip("sem senha utilizavel para o root")
    s, b = _http(f"{GW}/auth/v1/token?grant_type=password", "POST",
                 {"email": u["email"], "password": u["senha"]}, apikey=CHAVE)
    if s != 200:
        pytest.skip(f"login do root falhou: {s}")
    tok = b["access_token"]
    yield tok
    _http(f"{API}/api/area", "POST", {"polygon": [], "nome": NOME}, token=tok)


def test_root_consegue_salvar_area(token_root):
    """O root não pertence a empresa nenhuma — e mesmo assim precisa definir área.

    A convenção é a MESMA dos jobs desde 13/08/2026: sem empresa no crachá, vale
    `CR_TENANT_ID` do ambiente do servidor. É de propósito que seja a mesma
    variável — é o que faz a área em que ele grava e a empresa em que a mineração
    dele escreve serem a mesma coisa.
    """
    s, b = _http(f"{API}/api/area", "POST",
                 {"polygon": QUAD, "nome": NOME}, token=token_root)
    if s == 409:
        pytest.skip("servidor sem CR_TENANT_ID: o proprio 409 e o comportamento certo")
    assert s == 200, f"root nao conseguiu salvar area: {s} {b}"
    assert b.get("vertices") == 4, b


def test_a_rota_nunca_responde_500(token_root):
    """Falta de empresa é 409 (conflito de estado), não 500.

    O 500 é o que o front não conseguia explicar: vem sem corpo, então a tela não
    tinha o que mostrar nem que quisesse.
    """
    s, _ = _http(f"{API}/api/area", "POST",
                 {"polygon": QUAD, "nome": NOME}, token=token_root)
    assert s != 500, "a rota voltou a estourar em vez de responder"
    assert s in (200, 409), f"status inesperado: {s}"


def test_o_que_foi_salvo_e_o_que_volta(token_root):
    """Gravar e ler precisam concordar — foi a discordância que custou a rodada."""
    s, _ = _http(f"{API}/api/area", "POST",
                 {"polygon": QUAD, "nome": NOME}, token=token_root)
    if s == 409:
        pytest.skip("servidor sem CR_TENANT_ID")
    sys.path.insert(0, RAIZ)
    import area_utils
    volta = area_utils.carregar_area(NOME)
    assert volta and len(volta) == 4, f"o banco nao devolveu o que a rota aceitou: {volta}"


def test_places_api_foi_removida(token_root):
    """A Places API saiu em 24/08/2026 — e a rota RECUSA, não ignora.

    Ignorar em silêncio faria uma aba antiga do navegador disparar a captura
    achando que pediu a Places: o job rodaria, o resultado seria outro, e nada
    diria por quê.
    """
    s, b = _http(f"{API}/api/jobs", "POST",
                 {"modo": "mineracao", "opcoes": {"motor": "places"}}, token=token_root)
    assert s == 410, f"o motor pago ainda e aceito: {s} {b}"
    assert "places" in str(b.get("erro", "")).lower()


def test_a_tela_nao_oferece_mais_o_motor_pago():
    """O `<option>` sumiu do HTML, e o JS não decide mais nada por ele."""
    html = open(os.path.join(RAIZ, "frontend", "index.html"), encoding="utf-8").read()
    assert 'value="places"' not in html, "o motor pago ainda aparece no seletor"
    assert 'value="captura"' in html and 'value="estadual"' in html, \
        "as duas fontes gratuitas precisam continuar no seletor"

    js = open(os.path.join(RAIZ, "frontend", "app.js"), encoding="utf-8").read()
    # Comentário citando a remoção é bem-vindo; CÓDIGO que ramifica por 'places'
    # não — foi o que sobreviveria a uma remoção pela metade.
    codigo = "\n".join(l for l in js.splitlines()
                       if not l.lstrip().startswith("//"))
    assert not re.search(r'motor\s*===\s*"places"', codigo), \
        "o app.js ainda decide alguma coisa pelo motor pago"


def test_o_front_confere_a_resposta_antes_de_dizer_que_salvou():
    """A metade do defeito que morava no navegador.

    Não dá para exercitar o clique aqui, então a trava é sobre a FORMA: quem
    salva devolve o resultado, e o `toast` de sucesso fica atrás dele.
    """
    js = open(os.path.join(RAIZ, "frontend", "app.js"), encoding="utf-8").read()
    i = js.index("async function salvarArea")
    corpo = js[i:i + 1200]
    assert "r.ok" in corpo, "salvarArea nao olha o status da resposta"
    assert "return false" in corpo and "return true" in corpo, \
        "salvarArea precisa dizer ao chamador se o banco aceitou"
    assert re.search(r"if \(await salvarArea\(latlngs\)\)", js), \
        'o "Área salva ✔" precisa depender do retorno de salvarArea'
