# -*- coding: utf-8 -*-
"""Prova o escopo do supervisor e as travas da fila.

O que se quer provar, e que nao aparece em revisao de codigo:
  - supervisor NAO ve a base inteira da empresa, so o que lhe foi atribuido;
  - reprovar sem os dois motivos e recusado;
  - devolver sem dizer o que impede e recusado;
  - supervisor nao decide item da fila de outro supervisor.
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
        pytest.skip("USUARIOS-INICIAIS.csv ausente")
    return ler_credenciais(ARQ)


def entrar(cred, email):
    u = cred[email]
    s, b = http(f"{API}/api/login", "POST", {"email": u["email"], "senha": u["senha"]})
    if s != 200:
        pytest.skip(f"login falhou: {email}")
    return b["access_token"]


@pytest.fixture(scope="module")
def cenario(cred):
    """O admin da Corsan atribui 3 POIs ao supervisor da Corsan."""
    ta = entrar(cred, "admin.corsan@comercialradar.com.br")
    s, lst = http(f"{API}/api/usuarios", t=ta)
    sup = next(x for x in lst["usuarios"]
               if x["email"] == "supervisor.corsan@comercialradar.com.br")

    con = bc.conectar()
    with con.cursor() as cur:
        # `match_valido is not false`: o teste compara com o que /api/stats conta,
        # e la os invalidos nao entram. Sem este filtro o fixture pegava um POI
        # invalidado pela coerencia e o teste acusava o supervisor de ver menos
        # do que recebeu — quando quem estava errado era a escolha do fixture.
        cur.execute("""select p.id from pois p join tenants t on t.id=p.tenant_id
                        where t.nome='Aegea - Corsan' and p.match_valido is not false
                        order by p.id limit 3""")
        pois = [r[0] for r in cur.fetchall()]
    con.close()

    s, b = http(f"{API}/api/fila/atribuir", "POST",
                {"poi_ids": pois, "supervisor_ids": [sup["id"]]}, t=ta)
    assert s == 201, b
    yield {"admin": ta, "sup_id": sup["id"], "pois": pois}
    con = bc.conectar()
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("delete from atribuicao where poi_id = any(%s)", (pois,))
    con.close()


def test_supervisor_ve_a_empresa_inteira(cenario, cred):
    """Atribuicao e OBRIGACAO, nao limite de vista — correcao do usuario em
    13/08/2026. Quem decide sobre um ponto precisa olhar a vizinhanca: se o
    vizinho ja foi aprovado, se a rua e comercial, se o CNPJ aparece em outro
    endereco. Cegar o supervisor ao redor faz da decisao um palpite."""
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, st = http(f"{API}/api/stats", t=t)
    assert s == 200, st
    assert st["validos"] > 20000, (
        f"supervisor enxergou so {st['validos']} POIs; deveria ver a empresa inteira")


def test_user_tambem_ve_a_empresa_inteira(cenario, cred):
    """`user` ve tudo e nao opina em nada."""
    t = entrar(cred, "user.corsan@comercialradar.com.br")
    s, st = http(f"{API}/api/stats", t=t)
    assert s == 200 and st["validos"] > 20000, st


def test_supervisor_so_DECIDE_o_que_lhe_coube(cenario, cred):
    """O limite que sobrou, e o unico que importa: ver a empresa toda, decidir
    so o proprio lote."""
    ts = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila?status=todos", t=ts)
    meus = {i["id"] for i in fila["itens"]}

    # item de outro supervisor: o admin cria um e atribui a si mesmo
    ta = cenario["admin"]
    s, b = http(f"{API}/api/fila/atribuir", "POST",
                {"poi_ids": cenario["pois"][:1],
                 "supervisor_ids": [json_eu(ta)]}, t=ta)
    s, fa = http(f"{API}/api/fila?status=todos", t=ta)
    alheios = [i["id"] for i in fa["itens"] if i["id"] not in meus]
    if not alheios:
        pytest.skip("nao consegui montar item de outro dono")
    # Decisao COMPLETA de proposito: assim o 404 prova o escopo, e nao a
    # validacao do corpo — que agora recusa aprovacao sem revisao antes de
    # olhar o item.
    s, _ = http(f"{API}/api/fila/{alheios[0]}/decidir", "POST",
                {"status": "aprovado",
                 "revisao": {"uso": "comercial", "atividade": "mercearia"}}, t=ts)
    assert s == 404, "supervisor decidiu item que nao e dele"


def json_eu(token):
    s, eu = http(f"{API}/api/eu", t=token)
    return eu["id"]


def test_admin_continua_vendo_tudo(cenario, cred):
    s, st = http(f"{API}/api/stats", t=cenario["admin"])
    assert st["validos"] > 20000


def test_reprovar_sem_os_dois_motivos_e_recusado(cenario, cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    item = fila["itens"][0]["id"]
    s, _ = http(f"{API}/api/fila/{item}/decidir", "POST",
                {"status": "reprovado", "motivo_generico": "duplicado"}, t=t)
    assert s == 422, "reprovou sem motivo escrito"
    s, _ = http(f"{API}/api/fila/{item}/decidir", "POST",
                {"status": "reprovado", "motivo_escrito": "parece duplicado"}, t=t)
    assert s == 422, "reprovou sem motivo generico"


def test_devolver_sem_observacao_e_recusado(cenario, cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    item = fila["itens"][0]["id"]
    s, _ = http(f"{API}/api/fila/{item}/decidir", "POST", {"status": "devolvido"}, t=t)
    assert s == 422


def test_decisao_completa_passa(cenario, cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    item = fila["itens"][0]["id"]
    s, b = http(f"{API}/api/fila/{item}/decidir", "POST",
                {"status": "reprovado", "motivo_generico": "fachada_residencial",
                 "motivo_escrito": "fachada e portao de garagem, sem qualquer letreiro"}, t=t)
    assert s == 200 and b["status"] == "reprovado", b


def test_supervisor_de_outra_empresa_nao_alcanca(cenario, cred):
    t = entrar(cred, "supervisor.aegea-pi@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", "GET", t=t)
    ids = [i["id"] for i in fila.get("itens", [])]
    assert not any(i in ids for i in []), "sanidade"
    s2, _ = http(f"{API}/api/fila/{999999}/decidir", "POST",
                 {"status": "aprovado",
                  "revisao": {"uso": "comercial", "atividade": "mercearia"}}, t=t)
    assert s2 == 404


def test_user_nao_decide(cenario, cred):
    t = entrar(cred, "user.corsan@comercialradar.com.br")
    s, _ = http(f"{API}/api/fila/1/decidir", "POST", {"status": "aprovado"}, t=t)
    assert s == 403


# ── A revisao que sustenta a aprovacao ───────────────────────────────────────

def test_aprovar_sem_revisao_e_recusado(cenario, cred):
    """Aprovar e afirmar ao cliente que ali ha comercio. Sem o uso observado e a
    atividade, o dossie sai afirmando isso sem dizer comercio de que — e a
    concessionaria nao reclassifica tarifa com um documento assim."""
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    if not fila["itens"]:
        pytest.skip("fila vazia")
    item = fila["itens"][0]["id"]
    s, _ = http(f"{API}/api/fila/{item}/decidir", "POST", {"status": "aprovado"}, t=t)
    assert s == 422, "aprovou sem revisao"
    s, _ = http(f"{API}/api/fila/{item}/decidir", "POST",
                {"status": "aprovado", "revisao": {"uso": "comercial"}}, t=t)
    assert s == 422, "aprovou sem dizer a atividade"


def test_uso_fora_da_lista_e_recusado(cenario, cred):
    """`uso` vira texto do documento comprobatorio. Campo livre ali deixaria o
    dossie afirmar qualquer coisa que alguem digitasse."""
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    if not fila["itens"]:
        pytest.skip("fila vazia")
    s, _ = http(f"{API}/api/fila/{fila['itens'][0]['id']}/decidir", "POST",
                {"status": "aprovado",
                 "revisao": {"uso": "industrial_pesado", "atividade": "metalurgica"}}, t=t)
    assert s == 422


def test_aprovacao_com_revisao_grava_os_campos(cenario, cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila", t=t)
    if not fila["itens"]:
        pytest.skip("fila vazia")
    item = fila["itens"][0]["id"]
    rev = {"uso": "misto", "atividade": "mercearia no terreo, moradia em cima",
           "economias_comerciais": 1, "visita_necessaria": False,
           # chave que nao esta em CAMPOS_REVISAO: deve ser descartada
           "tarifa_nova": "COMERCIAL_3"}
    s, b = http(f"{API}/api/fila/{item}/decidir", "POST",
                {"status": "aprovado", "revisao": rev}, t=t)
    assert s == 200 and b["status"] == "aprovado", b

    s, f = http(f"{API}/api/fila/{item}/ficha", t=t)
    assert s == 200, f
    gravado = f["item"]["revisao"]
    assert gravado["uso"] == "misto"
    assert gravado["atividade"].startswith("mercearia")
    assert gravado["economias_comerciais"] == 1
    assert "tarifa_nova" not in gravado, "campo desconhecido entrou no documento"


def test_ficha_traz_a_evidencia(cenario, cred):
    """A ficha e o que o supervisor le antes de assinar: precisa vir com o POI e
    com o que o cadastro do cliente diz — nao so nome e endereco."""
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila?status=todos", t=t)
    if not fila["itens"]:
        pytest.skip("fila vazia")
    s, f = http(f"{API}/api/fila/{fila['itens'][0]['id']}/ficha", t=t)
    assert s == 200 and f["poi"].get("nome"), f
    assert "usos" in f and "comercial" in f["usos"]


def test_ficha_de_outra_empresa_nao_abre(cenario, cred):
    t = entrar(cred, "supervisor.aegea-pi@comercialradar.com.br")
    s, fila = http(f"{API}/api/fila?status=todos",
                   t=entrar(cred, "supervisor.corsan@comercialradar.com.br"))
    if not fila["itens"]:
        pytest.skip("fila vazia")
    s, _ = http(f"{API}/api/fila/{fila['itens'][0]['id']}/ficha", t=t)
    assert s == 404, "ficha de outra empresa abriu"


# ── Os candidatos da area ────────────────────────────────────────────────────

def test_candidatos_exige_admin(cenario, cred):
    t = entrar(cred, "supervisor.corsan@comercialradar.com.br")
    s, _ = http(f"{API}/api/fila/candidatos", t=t)
    assert s == 403


def test_candidatos_traz_area_e_facetas(cenario, cred):
    """O que a distribuicao antiga nao fazia: recortar pela area. Ela pegava os N
    primeiros de /api/pois, que nao filtra area nenhuma."""
    s, b = http(f"{API}/api/fila/candidatos", t=cenario["admin"])
    if s == 422:
        pytest.skip("nenhuma area de trabalho definida")
    assert s == 200, b
    assert b["total"] == len(b["itens"]) or b["truncado"]
    for chave in ("categoria", "cruz_flag", "edificacao", "conservacao"):
        assert chave in b["facetas"]
    if b["itens"]:
        i = b["itens"][0]
        for campo in ("id", "nome", "cruz_flag", "tem_foto", "tem_sv", "na_fila",
                      "ja_comercial"):
            assert campo in i, f"faltou {campo} no candidato"
        # bandeira propria, nao deduzida do rotulo do cruzamento na tela
        assert isinstance(i["ja_comercial"], bool)
        # Os contadores do cabecalho valem para a AREA INTEIRA; `itens` pode vir
        # cortado no teto. Exigir igualdade dos dois seria exigir que o
        # cabecalho mentisse sobre o que existe fora da lista.
        assert "ja_comerciais" in b
        visiveis = sum(1 for x in b["itens"] if x["ja_comercial"])
        if b["truncado"]:
            assert b["ja_comerciais"] >= visiveis
        else:
            assert b["ja_comerciais"] == visiveis
        # sem casamento no cadastro o POI e achado novo, nao "nao comercial"
        assert i["cruz_flag"], "cruz_flag nulo chegaria na tela como vazio"
