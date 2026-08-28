# -*- coding: utf-8 -*-
"""O CONTRATO DO TEMPO REAL: quem emite e quem lê têm de concordar no NOME DO CAMPO.

O DEFEITO, em 28/08/2026. O operador iniciou uma extração ponta a ponta e o log
da tela nova parou em "extração iniciada". A mineração corria normalmente no i9,
o WebSocket estava conectado, e nada aparecia.

O servidor emite `{"tipo": "log", "linha": ...}`. Eu lia `m.dados.texto`. Como
`dados` nem existe nessa mensagem, TODA linha era descartada em silêncio. Três
irmãs do mesmo erro no mesmo handler:

    tipo         servidor manda      eu lia
    log          linha               dados.texto     -> tela muda
    poi          poi                 dados           -> ponto nunca entrava
    progresso    dados               (não tratava)   -> barra e passo parados
    reload       (sem campo)         (não tratava)   -> fim de etapa ignorado

Ler o campo errado NÃO ESTOURA NADA. Não há exceção, não há log de erro, não há
`undefined` na tela: o `if` simplesmente não casa e a mensagem é jogada fora. É
o tipo de defeito que só aparece quando alguém está esperando o resultado.

Por isso este arquivo compara os dois lados em vez de conferir um só.
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

SERVER = os.path.join(RAIZ, "server.py")
NOVA = os.path.join(RAIZ, "frontend", "painel.js")
ANTIGA = os.path.join(RAIZ, "frontend", "app.js")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _codigo_js(caminho):
    """Sem comentário: asserção que lê comentário concorda com a prosa."""
    return "\n".join(l for l in _ler(caminho).splitlines()
                     if not l.lstrip().startswith("//"))


def _emitidos():
    """{tipo: {campos}} — o que o servidor de fato transmite.

    Lê os `manager.broadcast({...})` e o `send_text(json.dumps({...}))`, que é
    como a primeira mensagem de `job` chega ao conectar.
    """
    py = "\n".join(l.split("#")[0] for l in _ler(SERVER).splitlines())
    fora = {}
    for m in re.finditer(r'\{"tipo":\s*"(\w+)"([^\n]*)', py):
        tipo, resto = m.group(1), m.group(2)
        fora.setdefault(tipo, set())
        for campo in re.findall(r'"(\w+)":', resto):
            if campo != "tipo":
                fora[tipo].add(campo)
    return fora


def _handler(caminho):
    """O corpo do `onmessage`, do `onmessage` até o `};` que o fecha.

    Delimitar por "a próxima palavra `onclose`" quebrou: a tela antiga não tem
    `onclose` depois do `onmessage`. Fechar pela indentação do próprio bloco
    funciona nas duas, e continua funcionando se um `onerror` entrar no meio.
    """
    linhas = _codigo_js(caminho).splitlines()
    i = next(k for k, l in enumerate(linhas) if "onmessage" in l)
    recuo = len(linhas[i]) - len(linhas[i].lstrip())
    for j in range(i + 1, len(linhas)):
        l = linhas[j]
        if l.strip() in ("};", "}") and (len(l) - len(l.lstrip())) == recuo:
            return "\n".join(linhas[i:j + 1])
    return "\n".join(linhas[i:])


def _lidos(caminho):
    """{tipo: {campos}} — o que o JS lê em cada ramo do handler."""
    fora = {}
    corpo = _handler(caminho)
    for linha in corpo.splitlines():
        m = re.search(r'tipo === "(\w+)"', linha)
        if not m:
            continue
        fora.setdefault(m.group(1), set())
        for campo in re.findall(r'\bm(?:sg)?\.(\w+)', linha):
            if campo != "tipo":
                fora[m.group(1)].add(campo)
    return fora


def test_o_servidor_emite_os_cinco_tipos():
    e = _emitidos()
    for tipo in ("job", "log", "poi", "progresso", "reload"):
        assert tipo in e, "o servidor deixou de emitir '%s'" % tipo
    assert "linha" in e["log"], "a linha de log mudou de nome no servidor"
    assert "dados" in e["job"] and "dados" in e["progresso"], \
        "o payload de job/progresso mudou de nome no servidor"
    assert "poi" in e["poi"], "o POI ao vivo mudou de nome no servidor"


def test_a_tela_nova_le_o_campo_que_o_servidor_manda():
    """ESTE É O TESTE QUE FALTAVA. Ele reprova a versão que emudeceu a tela."""
    e, l = _emitidos(), _lidos(NOVA)
    for tipo in ("job", "log", "poi", "progresso", "reload"):
        assert tipo in l, (
            "a tela nova ignora '%s': a mensagem chega e é jogada fora sem "
            "erro nenhum" % tipo)
    for tipo, campos in l.items():
        if tipo not in e:
            continue
        for campo in campos:
            assert campo in e[tipo] or not e[tipo], (
                "a tela lê `m.%s` na mensagem '%s', e o servidor manda %s — "
                "o `if` não casa e a mensagem some em silêncio"
                % (campo, tipo, sorted(e[tipo]) or "nada"))


def test_a_tela_antiga_continua_de_acordo():
    """Ela é a referência: foi assim que o defeito da nova ficou visível."""
    e, l = _emitidos(), _lidos(ANTIGA)
    for tipo, campos in l.items():
        if tipo not in e:
            continue
        for campo in campos:
            assert campo in e[tipo] or not e[tipo], \
                "a tela antiga lê `msg.%s` em '%s', que o servidor não manda" % (campo, tipo)


def test_o_ponto_ao_vivo_nao_recarrega_a_base_inteira():
    """`carregarPois()` no ramo do POI baixa os 36 mil pontos A CADA ponto
    minerado. Numa extração isso é a tela travando quanto MAIS o processo dá
    certo."""
    js = _codigo_js(NOVA)
    for linha in _handler(NOVA).splitlines():
        if 'tipo === "poi"' in linha:
            assert "carregarPois()" not in linha, (
                "o ponto ao vivo voltou a recarregar a base inteira: uma "
                "varredura de 36 mil pontos por ponto novo")

    assert "function chegouPoi(" in js, "o ponto ao vivo deixou de entrar sozinho"
    # e a substituição é por `place_id`: o ingestor reingere por delete+recreate,
    # e o mesmo lugar volta com id novo
    i = js.index("function chegouPoi(")
    assert "place_id" in js[i:i + 1600], (
        "a substituição do POI reingerido deixou de usar `place_id`: o mapa "
        "acumula o marcador velho ao lado do novo")


def test_a_conexao_tem_ping():
    """Sem tráfego, proxy e navegador fecham um WebSocket ocioso — e numa etapa
    longa e silenciosa a tela perde a conexão justamente antes da próxima
    notícia."""
    for caminho, nome in ((NOVA, "painel.js"), (ANTIGA, "app.js")):
        js = _codigo_js(caminho)
        assert 'send("ping")' in js, "%s parou de manter a conexão viva" % nome


def test_o_soquete_cuida_da_propria_sessao():
    """O WEBSOCKET NÃO PASSA PELO `window.fetch`, então não herda a renovação.

    MEDIDO NO LOG DO SERVIDOR durante a extração de 28/08/2026:

        "WebSocket /ws" 403   connection rejected   (repetido, SEM ?token=)
        "GET /api/jobs/atual" 401 Unauthorized

    A sessão vence em 1 h. O `fetch` embrulhado renova sozinho antes de sair,
    mas o soquete é aberto direto pelo `WebSocket`. Quando a hora virava, ele
    caía e a reconexão reapresentava um token morto — ou nenhum, porque o
    `comToken` devolve a URL crua quando não há token guardado. O recuo
    exponencial só deixava o laço mais silencioso.

    As três defesas são as mesmas da tela antiga, que não tinha esse problema.
    """
    for caminho, nome in ((NOVA, "painel.js"), (ANTIGA, "app.js")):
        js = _codigo_js(caminho)

        # 1. renova ANTES de abrir
        assert "crVencendo" in js and "crRenovar" in js, (
            "%s abre o soquete sem renovar a sessão: ao virar a hora ele "
            "reconecta com token morto para sempre" % nome)

        # 2. sem sessão não tenta — espera o aviso de login
        assert "cr:sessao" in js, (
            "%s voltou a insistir sem sessão: 403 em laço, e o log do servidor "
            "fica ilegível justamente quando se quer lê-lo" % nome)

        # 3. o token vai na URL, que é como o servidor autentica o soquete
        assert "?token=" in js, "%s parou de mandar o token no soquete" % nome

    # e o `comToken` não serve aqui: ele devolve a URL CRUA quando não há
    # token, e foi assim que nasceram os `"WebSocket /ws" 403` do log
    js = _codigo_js(NOVA)
    i = js.index("function ligarWebsocket(")
    corpo = js[i:i + 3000]
    assert "comToken" not in corpo, (
        "o soquete voltou a usar `comToken`, que devolve a URL sem token "
        "quando a sessão caiu — e o servidor recusa com 403 em silêncio")
