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
from conftest import ler_credenciais  # noqa: E402

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
    cred = ler_credenciais()
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
    # As duas fontes gratuitas não estão mais num `<select>` — elas rodam as
    # duas, e o painel descreve as etapas em vez de oferecer escolha. Ver
    # `test_a_mineracao_roda_as_duas_fontes`.
    assert "Bases públicas" in html and "Captura + OCR" in html, \
        "o painel deixou de descrever as duas fontes da mineração"

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


def test_a_mineracao_roda_as_duas_fontes():
    """Deixaram de ser alternativas: o painel não oferece escolha de motor.

    A regra vale nos dois lados. No HTML, porque um `<select>` de motor é o que
    convida a desligar metade do processo. No servidor, porque é ele quem
    executa — e é `minerar_tudo.py`, não `minerar_captura.py` direto, que roda as
    duas em sequência.
    """
    html = open(os.path.join(RAIZ, "frontend", "index.html"), encoding="utf-8").read()
    assert 'id="op-motor"' not in html, "o seletor de motor voltou ao painel"

    srv = open(os.path.join(RAIZ, "server.py"), encoding="utf-8").read()
    i = srv.index('elif modo == "mineracao":')
    bloco = srv[i:i + 2600]
    assert '"minerar_tudo.py"' in bloco,         "a mineracao voltou a chamar so uma fonte"
    assert '"minerar_captura.py"' not in bloco,         "a mineracao chama a captura direto — pularia as bases publicas"


def test_o_dataset_da_uf_e_reaproveitado():
    """Produzir a UF inteira é caro; fazer isso a cada mineração seria absurdo.

    O marcador é escrito só quando a skill termina INTEIRA. Uma execução
    interrompida na etapa 6 de 8 tem pasta e tem arquivos, e não serve para
    importar — por isso a existência da pasta não basta como prova.
    """
    sys.path.insert(0, RAIZ)
    import minerar_tudo
    fonte = open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    assert "MARCADOR" in fonte and "_pronto.txt" in fonte
    assert minerar_tudo.DATASETS.name == "estadual", minerar_tudo.DATASETS

    # A REGRA, e não o literal `MARCADOR).exists()` que a versão anterior
    # cobrava. Aquela forma casava com UMA implementação: quando a conferência
    # passou a perguntar TAMBÉM ao i9 — porque o dataset mora lá e o notebook
    # dizia "não há dataset de RS" com o RS pronto —, o teste reprovou uma
    # melhoria. Teste que descreve o COMO impede consertar o QUÊ.
    i = fonte.index("def garantir_dataset")
    corpo = fonte[i:i + 1600]
    assert "dataset_pronto(uf)" in corpo,         "garantir_dataset nao confere se a UF ja esta pronta antes de reproduzi-la"

    # E quem confere tem de olhar o MARCADOR, não a pasta: execução interrompida
    # na etapa 6 de 8 tem pasta e tem arquivos, e não serve para importar.
    j = fonte.index("def dataset_pronto")
    conf = fonte[j:j + 1600]
    assert "MARCADOR" in conf, "a conferência deixou de exigir o marcador"
    assert "I9_DIR" in conf or "i9" in conf.lower(),         "a conferência voltou a olhar só o disco local; o dataset mora no i9"


def test_ha_saida_quando_o_dataset_da_uf_nao_existe():
    """A etapa 1 exige o dataset da UF, e produzi-lo leva horas no i9.

    Sem uma saída, quem ainda não o tem fica sem poder minerar — foi o que
    aconteceu em 24/08/2026, no mesmo dia em que as duas fontes deixaram de ser
    alternativas. A caixa nasce DESMARCADA: ela destrava um teste, não é o jeito
    normal de rodar.
    """
    html = open(os.path.join(RAIZ, "frontend", "index.html"), encoding="utf-8").read()
    i = html.index('id="op-pular-bases"')
    assert "checked" not in html[i:i + 120],         "a caixa de pular as bases públicas nasce MARCADA — vira o padrão sem ninguém decidir"

    js = open(os.path.join(RAIZ, "frontend", "app.js"), encoding="utf-8").read()
    assert "pular_bases:" in js, "o painel não envia a opção ao servidor"

    srv = open(os.path.join(RAIZ, "server.py"), encoding="utf-8").read()
    # Do início do bloco até o próximo modo — e não uma contagem de caracteres:
    # a primeira versão deste teste olhava 2.600 chars e reprovava porque a
    # linha estava logo depois. Teste que depende de quanto comentário existe
    # acima falha quando alguém documenta melhor.
    i = srv.index('elif modo == "mineracao":')
    bloco = srv[i:srv.index('elif modo ==', i + 10)]
    assert 'op.get("pular_bases")' in bloco,         "o servidor não lê a opção que o painel manda"
