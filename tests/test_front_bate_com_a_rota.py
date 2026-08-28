# -*- coding: utf-8 -*-
"""O FRONT MANDA O QUE A ROTA DECLARA — e a falha não pode ser muda.

O DEFEITO, em 28/08/2026. O operador escolheu **Rio Grande** na lista de
cidades. A mineração rodou **Canoas**.

    painel.js   fetch("/api/area/municipio", {body: JSON.stringify({cod})})
    server.py   def area_do_municipio(cod: str)      <- QUERY, não corpo

`cod: str` sem `Body(...)` é parâmetro de QUERY no FastAPI. A rota não lê corpo
nenhum: `query=['cod']`, `corpo=[]`. Então o servidor respondia **422** por não
achar `cod` — e o `.catch(() => {})` engolia, porque **`fetch` não rejeita em
erro HTTP**, só em falha de rede.

O resultado: a tela pintava "Rio Grande", o banco continuava com a área de
Canoas (`salvo_em` parado às 17:22), e a extração rodou a cidade errada sem uma
linha de aviso em lugar nenhum. A tela ANTIGA sempre chamou pela query — o
defeito nasceu quando reescrevi a nova.

Este arquivo cobra as duas metades: a chamada certa, e o fim do silêncio.
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

FRONT = os.path.join(RAIZ, "frontend")


def _codigo_js(nome):
    caminho = os.path.join(FRONT, nome)
    return "\n".join(l for l in io.open(caminho, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("//"))


def _rotas():
    import server
    fora = {}
    for r in server.app.routes:
        caminho = getattr(r, "path", "")
        if not caminho.startswith("/api/"):
            continue
        for m in (getattr(r, "methods", None) or []):
            fora[(m, caminho)] = {
                "query": [d.name for d in r.dependant.query_params],
                "corpo": [d.name for d in r.dependant.body_params],
            }
    return fora


def _chamadas_com_corpo(codigo, rotas):
    """(método, rota) de toda chamada que manda corpo para rota que só lê query."""
    ruins = []
    for m in re.finditer(r'fetch\(\s*[`"\']([^`"\']*?/api/[^`"\'?]*)', codigo):
        url = m.group(1)
        trecho = codigo[m.start():m.start() + 400]
        if "body:" not in trecho:
            continue
        metodo = ("POST" if re.search(r'["\']POST["\']', trecho)
                  else "DELETE" if "DELETE" in trecho else "GET")
        for (mm, cam), d in rotas.items():
            if mm != metodo:
                continue
            if not re.match(re.sub(r"\{[^}]+\}", "[^/]+", cam) + "$", url):
                continue
            if d["query"] and not d["corpo"]:
                ruins.append((metodo, cam, d))
            break
    return ruins


def test_nenhuma_chamada_manda_corpo_para_rota_de_query():
    """A varredura é do arquivo INTEIRO, e não só da chamada que quebrou: o
    mesmo engano cabe em qualquer outra rota, e ninguém vai lembrar de conferir
    à mão na próxima."""
    rotas = _rotas()
    assert rotas, "não consegui ler as rotas do servidor"
    for arq in ("painel.js", "app.js"):
        ruins = _chamadas_com_corpo(_codigo_js(arq), rotas)
        assert not ruins, (
            "%s manda CORPO para rota que só declara QUERY — o FastAPI responde "
            "422 e o front nem fica sabendo: %s" % (arq, ruins))


def test_a_escolha_de_municipio_vai_pela_query():
    js = _codigo_js("painel.js")
    assert '"/api/area/municipio?cod=" + encodeURIComponent(' in js, \
        "a escolha de município voltou a mandar o código fora da query"


def test_falhar_a_area_nao_pode_ser_silencioso():
    """FALHA DE ÁREA É FALHA DE TUDO: sem ela a extração vai para o município
    anterior. Melhor parar em voz alta do que a tela dizer "Rio Grande" com
    Canoas no banco."""
    js = _codigo_js("painel.js")
    i = js.index("async function escolherMunicipio(")
    corpo = js[i:js.index("\n  }", i)]
    assert "if (!r || !r.ok)" in corpo, (
        "voltou a ignorar o status HTTP: `fetch` não rejeita em erro, e o "
        "`.catch` sozinho deixa o 422 passar por sucesso")
    assert "linhaLog(" in corpo, "a falha de área voltou a não aparecer no log da tela"
    assert "estado.cidade = null" in corpo, (
        "a tela continua marcando o município escolhido mesmo quando a área "
        "não foi gravada — é essa divergência que fez minerar a cidade errada")
