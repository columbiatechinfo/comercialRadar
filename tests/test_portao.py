# -*- coding: utf-8 -*-
"""Prova que NENHUMA rota de /api/ responde sem sessão, exceto as declaradas.

Este teste é o que impede a regressão que já aconteceu: a tela de login existia,
parecia proteger, e 29 rotas continuavam servindo dado a quem chamasse direto.

Ele descobre as rotas do próprio `app`, então rota NOVA entra no teste sozinha —
que é o ponto. Teste com lista fixa de rotas envelhece no dia seguinte.
"""
import os
import sys

import pytest  # noqa: F401


def test_imagem_aceita_token_na_query_e_so_ela():
    """`<img src>` nao manda cabecalho — nao existe API para isso. Sem esta
    excecao, toda fachada do modal do mapa vinha 401 e o navegador desenhava
    imagem quebrada, sem ninguem entender por que.

    E a excecao TEM de ser curta: token em URL vai para log de servidor e para
    historico do navegador. Vale para imagem e para o que abre em aba nova —
    nao para a API inteira."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import server

    assert "/api/sv/" in server.TOKEN_NA_QUERY
    assert "/api/eu/foto" in server.TOKEN_NA_QUERY
    assert "/api/dossie/" in server.TOKEN_NA_QUERY
    for proibido in ("/api/", "/api/pois", "/api/usuarios", "/api/fila",
                     "/api/jobs", "/api/empresas"):
        assert proibido not in server.TOKEN_NA_QUERY, (
            f"{proibido} nao pode autenticar por query: e rota de dado, nao de imagem")
    assert len(server.TOKEN_NA_QUERY) <= 5, "a excecao esta crescendo demais"
from fastapi.testclient import TestClient

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
import server  # noqa: E402

cliente = TestClient(server.app)

# Rotas de escrita ficam fora da varredura automática: chamá-las sem token deve
# dar 401, mas se um dia derem 200 o teste teria EXECUTADO a ação. A varredura
# usa GET; POST/PATCH/DELETE são conferidos com corpo vazio, que o portão barra
# antes de qualquer validação.
def _rotas(metodo: str):
    for r in server.app.routes:
        caminho = getattr(r, "path", "")
        if not caminho.startswith("/api/"):
            continue
        if caminho in server.PUBLICAS:
            continue
        if "{" in caminho:                       # precisa de id real; cobertas à parte
            continue
        if metodo in getattr(r, "methods", set()):
            yield caminho


@pytest.mark.parametrize("caminho", sorted(set(_rotas("GET"))))
def test_get_sem_token_e_401(caminho):
    r = cliente.get(caminho)
    assert r.status_code == 401, (
        f"{caminho} respondeu {r.status_code} sem token — rota aberta")


@pytest.mark.parametrize("caminho", sorted(set(_rotas("POST"))))
def test_post_sem_token_e_401(caminho):
    r = cliente.post(caminho, json={})
    assert r.status_code == 401, (
        f"{caminho} respondeu {r.status_code} sem token — rota aberta")


def test_publicas_continuam_abertas():
    """A página e o login PRECISAM abrir sem sessão — senão não há como entrar."""
    assert cliente.get("/").status_code == 200
    # senha errada = 401 do Auth, não do portão: prova que a rota foi alcançada
    r = cliente.post("/api/login", json={"email": "ninguem@exemplo.com", "senha": "x"})
    assert r.status_code == 401 and "senha" in r.json().get("detail", "")


def test_websocket_sem_token_e_recusado():
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_text()
