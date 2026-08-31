# -*- coding: utf-8 -*-
"""Prova que o dossie reune a evidencia e respeita quem pode ve-lo."""
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


def http(u, t=None):
    r = urllib.request.Request(u, headers={"Authorization": f"Bearer {t}"} if t else {})
    try:
        with urllib.request.urlopen(r, timeout=180) as x:
            return x.status, x.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception as e:
        pytest.skip(f"API indisponivel: {type(e).__name__}")


@pytest.fixture(scope="module")
def ctx():
    if not os.path.exists(ARQ):
        pytest.skip("csv ausente")
    cred = ler_credenciais(ARQ)

    def entrar(email):
        u = cred[email]
        r = urllib.request.Request(
            f"{API}/api/login", method="POST",
            data=json.dumps({"email": u["email"], "senha": u["senha"]}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(r, timeout=20).read())["access_token"]
        except Exception:
            pytest.skip(f"login falhou: {email}")

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select p.id from pois p join core.tb_empresas t on t.id=p.id_empresa
                        where t.nome='Aegea - Corsan'
                          and exists(select 1 from streetview_imgs s where s.poi_id=p.id)
                        order by p.id limit 1""")
        poi = cur.fetchone()[0]
    con.close()
    return {"entrar": entrar, "poi": poi}


def test_html_reune_a_evidencia(ctx):
    s, b = http(f"{API}/api/dossie/{ctx['poi']}", ctx["entrar"]("admin.corsan@comercialradar.com.br"))
    assert s == 200, b
    txt = b.decode("utf-8", "ignore")
    for secao in ("Identificação", "Evidência visual", "Procedência do dado"):
        assert secao in txt, f"faltou a seção {secao}"
    # As imagens vao EMBUTIDAS: PDF que aponta para URL vira papel em branco
    # quando o cliente abre o arquivo meses depois, offline.
    assert "data:image" in txt, "as imagens nao foram embutidas"
    assert "http://127.0.0.1" not in txt, "o documento depende do servidor para exibir imagem"


def test_pdf_sai_valido(ctx):
    """Gerado pelo ADMIN. O supervisor tambem gera — mas so do que esta na fila
    dele, e e por isso que este teste nao usa supervisor com um POI qualquer:
    a primeira versao pegou 404 e o 404 estava certo."""
    s, b = http(f"{API}/api/dossie/{ctx['poi']}/pdf",
                ctx["entrar"]("admin.corsan@comercialradar.com.br"))
    assert s == 200, b[:200]
    assert b[:5] == b"%PDF-", "nao e um PDF"
    assert len(b) > 20000, "PDF pequeno demais para conter a evidencia"


def test_supervisor_gera_de_qualquer_poi_da_empresa(ctx):
    """Corrigido em 13/08/2026, junto com o escopo.

    A primeira versao exigia 404 aqui — eu tinha entendido que a atribuicao
    limitava a VISTA. Nao limita: ela define a obrigacao de avaliar. O supervisor
    enxerga a empresa inteira e produz documento de qualquer ponto dela; o que
    ele nao faz e DECIDIR sobre item que nao lhe coube, e isso e testado em
    test_fila_supervisor."""
    t = ctx["entrar"]("supervisor.corsan@comercialradar.com.br")
    s, b = http(f"{API}/api/dossie/{ctx['poi']}/pdf", t)
    assert s == 200 and b[:5] == b"%PDF-", b[:120]


def test_user_le_mas_nao_gera_documento(ctx):
    t = ctx["entrar"]("user.corsan@comercialradar.com.br")
    s, _ = http(f"{API}/api/dossie/{ctx['poi']}", t)
    assert s == 200, "user deveria poder LER o dossie"
    s, _ = http(f"{API}/api/dossie/{ctx['poi']}/pdf", t)
    assert s == 403, "user gerou documento comprobatorio"


def test_outra_empresa_recebe_404_e_nao_403(ctx):
    """404 e nao 403: responder 403 confirmaria que o registro existe em outra
    empresa, e isso ja e informacao que nao lhe pertence."""
    t = ctx["entrar"]("admin.aegea-pi@comercialradar.com.br")
    s, _ = http(f"{API}/api/dossie/{ctx['poi']}", t)
    assert s == 404
