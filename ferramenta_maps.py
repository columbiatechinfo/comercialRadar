# -*- coding: utf-8 -*-
"""Lê o painel do Google Maps de UM estabelecimento — o mesmo caminho do resto.

POR QUE ISTO EXISTE

O endereço, o telefone e o horário de um comércio pequeno vivem no PAINEL do
buscador, aquele cartão à direita. Não é página web: nenhum buscador de páginas
alcança, e o SearXNG devolvia diário oficial enquanto o dado estava ali.

O projeto já resolve isso do jeito dele desde sempre — `search_from_sheet.py`
abre o Maps com navegador, proxy e fingerprint e lê o painel. É o mesmo caminho
que rodou nas 40 lojas de Canoas nesta sessão.

O que este módulo faz é empacotar UMA consulta daquele fluxo para o agente
poder chamar durante uma conversa. Nenhuma técnica nova: a mesma sessão, o
mesmo pool, a mesma extração.

CUSTO, que importa numa conversa: cada consulta abre um navegador e leva
dezenas de segundos. Por isso não é a primeira ferramenta que o agente deve
tentar — a descrição diz isso a ele.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import threading
import tempfile
import time
from concurrent.futures import TimeoutError as FuturesTimeout


# ─── laço de eventos próprio ─────────────────────────────────────────────────
#
# `asyncio.run()` fecha o laço ao terminar, e com ele morre a sessão quente que
# acabou de ser guardada — "Event loop is closed" na chamada seguinte. Recurso
# assíncrono que precisa sobreviver entre chamadas precisa de um laço que
# também sobreviva.
_LACO = None
_TRAVA = threading.Lock()


def _laco():
    """O laço de fundo, criado na primeira necessidade."""
    global _LACO
    with _TRAVA:
        if _LACO is None or _LACO.is_closed():
            _LACO = asyncio.new_event_loop()
            threading.Thread(target=_LACO.run_forever, daemon=True,
                             name="maps-loop").start()
    return _LACO


def _rodar(corrotina, segundos: float):
    """Submete ao laço de fundo e espera, com teto de tempo."""
    fut = asyncio.run_coroutine_threadsafe(corrotina, _laco())
    return fut.result(timeout=segundos)


MAX_SEGUNDOS = 240   # com sessão quente, a consulta é curta
TENTATIVAS = 3       # IPs por consulta


def _limpar(r: dict) -> dict:
    """Do registro do pipeline para o que interessa numa conversa.

    AS FOTOS VOLTARAM, limitadas a seis. Elas sempre foram coletadas e eram
    descartadas aqui, com uma razão que valia para dezenas de URLs e não vale
    para seis: fachada é o dado que decide se vale a visita, e o projeto inteiro
    gira em torno de olhar fachada.

    Os comentários continuam de fora — são parágrafos, não um endereço, e é aí
    que o contexto some de verdade.
    """
    return {
        "nome": r.get("nome") or r.get("nome_planilha"),
        "endereco": r.get("endereco"),
        "telefone": r.get("telefone"),
        "website": r.get("website"),
        "categoria": r.get("categoria"),
        "avaliacao": r.get("avaliacao"),
        "total_avaliacoes": r.get("total_avaliacoes"),
        "horarios": r.get("horarios"),
        "status_horario": r.get("status_horario"),
        "lat": r.get("maps_lat"), "lng": r.get("maps_lng"),
        "maps_url": r.get("maps_url"),
        "status": r.get("status"),
        "confere_com_o_nome_pedido": r.get("match_valido"),
        "similaridade": r.get("similaridade"),
        # Num match divergente estes DOIS são o essencial: sem eles o dado
        # chega sem a desconfiança que o acompanha, e vira afirmação.
        "nome_pedido": r.get("nome_pedido"),
        "aviso": r.get("aviso"),
        "fotos": (r.get("fotos") or [])[:6],
        # AS AVALIACOES tambem voltaram, cinco delas, com nota e data.
        # Ficavam de fora com a mesma justificativa das fotos — "sao paragrafos"
        # — e a justificativa vale para vinte, nao para cinco. Num radar
        # comercial, o que os clientes escrevem e QUANDO escreveram diz se o
        # ponto esta vivo: cinco avaliacoes de 2019 e ponto parado.
        "avaliacoes": [
            {"autor": c.get("autor"), "nota": c.get("nota"),
             "data": c.get("data"), "texto": (c.get("texto") or "")[:280]}
            for c in (r.get("comentarios") or [])[:5]],
        **_perfil_social(r),
    }


def _perfil_social(r: dict) -> dict:
    """O `website` do painel costuma SER o Instagram da loja.

    Aconteceu com o PKC Fusion: o Maps devolveu `instagram.com/pkcfusion`, a
    resposta citou "@pkcfusion (Fonte: Google Maps)" e parou ali — sem a data
    das publicacoes, que e o que diz se o ponto esta vivo.

    A regra de abrir o perfil existe na escada e foi ignorada. Entao o aviso vem
    COLADO no dado: o handle ja extraido e a porta indicada, no mesmo lugar onde
    o modelo le a URL. Nao se decide por ele — se mostra o caminho.
    """
    # UM CAMPO POR VEZ. Juntar os tres numa string so e fazer o split parecia
    # economia e produziu `usuario = "pkcfusion instagram.com https:"` — o
    # separador do proximo campo entrou junto no handle.
    #
    # `website_url` vem do href e tem a URL inteira; `website` e o texto do
    # link, que o Google trunca ao dominio e por isso quase nunca serve.
    campos = [r.get("website_url") or "", r.get("website") or "",
              r.get("maps_url") or ""]
    for rede, dominio in (("instagram", "instagram.com/"),
                          ("facebook", "facebook.com/")):
        usuario = ""
        for campo in campos:
            baixo = campo.lower()
            if dominio not in baixo:
                continue
            cru = baixo.split(dominio, 1)[1]
            # corta no primeiro separador de URL, qualquer que seja
            for corte in ("/", "?", "#", "&", " "):
                cru = cru.split(corte)[0]
            if cru and cru not in ("p", "reel", "explore", "pages"):
                usuario = cru
                break
        if not usuario:
            continue
        d = {"perfil_social": {"rede": rede, "usuario": usuario}}
        if rede == "instagram":
            d["perfil_social"]["faca_agora"] = (
                f"chame `consultar_instagram(usuario='{usuario}')` para saber "
                f"a data da ultima publicacao. Citar o perfil sem a data e "
                f"meia informacao: perfil parado ha meses e ponto provavelmente "
                f"fechado.")
        return d
    return {}


# ─── piscina de sessões quentes ──────────────────────────────────────────────
#
# Era UMA sessão de módulo. Com o lote em paralelo, quatro trabalhadores caíam
# na mesma aba e se atropelavam — o resultado voltava ora inteiro, ora com erro.
# Agora são N sessões independentes: pega, usa, devolve.
#
# O tamanho vem do ambiente porque depende da máquina: o notebook segue em 1
# (idêntico ao que era), e o i9, com 51 GB livres, comporta dezesseis.
TAMANHO_PISCINA = max(1, int(os.environ.get("MAPS_SESSOES", "1")))

VIDA_MAXIMA = 900     # segundos: sessão velha acumula estado e vira alvo
USOS_MAXIMOS = 25     # e cadência longa demais no mesmo IP também

_LIVRES = None        # asyncio.Queue das sessões prontas
_ABERTAS = 0          # quantas existem, para não passar do tamanho
_POOL = None          # o ProxyPool, um só para todas as sessões
_TRAVA_PISCINA = None


def _fila():
    """A fila de sessões livres, criada dentro do laço que vai usá-la."""
    global _LIVRES, _TRAVA_PISCINA
    if _LIVRES is None:
        _LIVRES = asyncio.Queue()
        _TRAVA_PISCINA = asyncio.Lock()
    return _LIVRES


async def _fechar(s: dict) -> None:
    """Fecha uma sessão e devolve o IP ao pool."""
    for fechar in (s.get("sess"), s.get("pw")):
        if fechar is None:
            continue
        try:
            await (fechar.close() if hasattr(fechar, "close") else fechar.stop())
        except Exception:
            pass
    if _POOL and s.get("proxy"):
        try:
            await _POOL.release(s["proxy"])
        except Exception:
            pass


def _vencida(s: dict) -> bool:
    if not s or not s.get("sess"):
        return True
    return (time.time() - s["nascida"] > VIDA_MAXIMA
            or s["usos"] >= USOS_MAXIMOS)


async def _nascer(indice: int, usar_proxy: bool):
    """Abre UMA sessão com perfil próprio. Devolve o dicionário ou None.

    O perfil leva o índice da vaga: dois Chromiums no mesmo diretório de perfil
    corrompem o perfil — seria trocar a colisão de aba por uma de disco.
    """
    global _POOL
    from playwright.async_api import async_playwright

    import search_from_sheet as sfs
    from human_browser import HumanSession

    if usar_proxy and _POOL is None:
        # RELAY, não ProxyPool. Passar usuário e senha ao Chromium faz o
        # `google.com/maps` pendurar 35 s e falhar — medido, e é a causa do
        # `Page.goto: Timeout` que enchia o log. O relay carrega a credencial
        # fora do navegador e abre em 1,7 s pelo mesmo IP.
        from relay_proxy import PiscinaRelay
        _POOL = PiscinaRelay(vagas=TAMANHO_PISCINA).start()

    pw = await async_playwright().start()
    for tentativa in range(1, TENTATIVAS + 1):
        proxy = (await _POOL.acquire_blocking(intervalo=2.0, tentativas=30)
                 if _POOL else None)
        if _POOL and not proxy:
            break
        perfil = (pathlib.Path(tempfile.gettempdir())
                  / f"cr_maps_vaga{indice}_t{tentativa}")
        try:
            sess = await HumanSession.create(pw, proxy, perfil, layer="maps",
                                             headless=True)
        except Exception:
            continue
        if await sfs.abrir_maps(sess) and not await sess.is_captcha():
            return {"vaga": indice, "pw": pw, "sess": sess, "proxy": proxy,
                    "nascida": time.time(), "usos": 0}
        # IP que não abriu o Maps não vai abrir na próxima pergunta
        try:
            await sess.close()
        except Exception:
            pass
        if _POOL and proxy:
            await _POOL.mark_cooldown(proxy, 600)
            await _POOL.release(proxy)
    try:
        await pw.stop()
    except Exception:
        pass
    return None


async def _pegar(usar_proxy: bool):
    """Empresta uma sessão pronta. Abre uma nova se ainda houver vaga."""
    global _ABERTAS
    fila = _fila()

    while True:
        # 1) alguém já devolveu uma? aproveita, se ainda estiver válida
        try:
            s = fila.get_nowait()
        except asyncio.QueueEmpty:
            s = None
        if s is not None:
            if not _vencida(s):
                return s
            await _fechar(s)
            async with _TRAVA_PISCINA:
                _ABERTAS -= 1
            continue

        # 2) ainda cabe abrir? abre
        async with _TRAVA_PISCINA:
            cabe = _ABERTAS < TAMANHO_PISCINA
            if cabe:
                _ABERTAS += 1
                vaga = _ABERTAS
        if cabe:
            nova = await _nascer(vaga, usar_proxy)
            if nova:
                return nova
            async with _TRAVA_PISCINA:
                _ABERTAS -= 1
            return None

        # 3) piscina cheia: espera alguém devolver — fila explícita, não colisão
        s = await fila.get()
        if not _vencida(s):
            return s
        await _fechar(s)
        async with _TRAVA_PISCINA:
            _ABERTAS -= 1


async def _devolver(s: dict, quebrou: bool) -> None:
    """Devolve à piscina, ou descarta se a sessão morreu."""
    global _ABERTAS
    if quebrou or _vencida(s):
        await _fechar(s)
        async with _TRAVA_PISCINA:
            _ABERTAS -= 1
        return
    _fila().put_nowait(s)


async def _descartar():
    """Fecha tudo: navegadores E relays. Órfão de qualquer um come memória."""
    global _ABERTAS, _POOL
    fila = _fila()
    while True:
        try:
            await _fechar(fila.get_nowait())
        except asyncio.QueueEmpty:
            break
    _ABERTAS = 0
    if _POOL is not None and hasattr(_POOL, "encerrar"):
        _POOL.encerrar()
        _POOL = None


async def _uma(nome: str, cidade: str, usar_proxy: bool) -> dict:
    """Pega uma sessão da piscina, consulta e devolve."""
    import search_from_sheet as sfs

    s = await _pegar(usar_proxy)
    if s is None:
        return {"erro": f"{TENTATIVAS} IPs tentados e o Maps não abriu em "
                        f"nenhum. É problema de acesso, não indica que o "
                        f"estabelecimento não exista."}

    item = {"_row": 0, "nome": nome, "endereco": "", "lat": None,
            "lng": None, "uf": None}
    quebrou = False
    try:
        # `manter_divergente`: no chat, dado suspeito COM aviso vale mais
        # que campo vazio sem explicação — quem lê consegue julgar.
        r = _limpar(await sfs.buscar_linha(s["sess"], item, cidade,
                                           "agente",
                                           manter_divergente=True))
        s["usos"] += 1
        r["sessao_reaproveitada"] = s["usos"] > 1
        r["vaga"] = s["vaga"]

        # QUEM DECIDE SE É O MESMO LUGAR É A IA, NÃO A RÉGUA DE TEXTO.
        #
        # `nome_match` compara letras. Ela deu 0,548 para "Bussbier Cerveja
        # Artesanal e Bebidas" contra "BussBier Chopp Para Festas" — mesmo
        # endereço, mesmo telefone, mesmo ramo. Qualquer pessoa vê que é a
        # mesma loja; `SequenceMatcher` não vê, porque não sabe que Chopp e
        # Cerveja Artesanal são o mesmo negócio.
        #
        # Então a régua deixa de ser o veredito e vira o GATILHO: quando ela
        # desconfia, a IA olha nome, endereço, telefone e categoria juntos e
        # decide. O número continua no resultado, mas ao lado do julgamento —
        # e não mais no lugar dele.
        #
        # Isto vale só aqui, no chat. No pipeline a régua segue sozinha: lá são
        # milhares de POIs sem revisão humana e uma chamada de IA por linha.
        if r.get("status") == "encontrado_divergente" and r.get("nome"):
            import julgar_identidade as JI

            v = await asyncio.get_event_loop().run_in_executor(
                None, lambda: JI.julgar(
                    nome_pedido=nome, nome_achado=r.get("nome") or "",
                    endereco=r.get("endereco") or "",
                    telefone=r.get("telefone") or "",
                    categoria=r.get("categoria") or "", cidade=cidade))
            r["veredito_da_ia"] = v
            r["confere_com_o_nome_pedido"] = {"sim": True, "nao": False}.get(
                v.get("mesmo"), None)      # None = incerto, e incerto se diz
            r["aviso"] = (
                f"nomes diferentes ('{nome}' x '{r.get('nome')}'), e a IA "
                f"julgou: {v.get('mesmo')} — {v.get('porque')}")

        # O INSTAGRAM DO PAINEL E LIDO AQUI, e nao pedido ao modelo.
        #
        # Tres tentativas de pedir falharam: a regra na escada, o campo
        # `faca_agora` colado no proprio dado, e o reforco perto da pergunta.
        # Na conversa nova pelo Espetao Vancosty o retorno trazia
        # `perfil_social: {"usuario": "supermercadovancosty", "faca_agora":
        # "chame consultar_instagram..."}` e cinco avaliacoes — e a escada
        # rodou seis degraus sem chamar o Instagram.
        #
        # Isto NAO e codigo decidindo no lugar da IA: e a ferramenta terminando
        # o proprio trabalho. Quando o painel diz que o SITE da loja E um
        # Instagram, ler esse Instagram faz parte de ler o painel — do mesmo
        # jeito que `enriquecer_poi` ja abre as abas de horario e de fotos em
        # vez de pedir ao modelo que abra.
        #
        # Custa ~15 s e so acontece quando o painel realmente aponta um perfil.
        perfil = (r.get("perfil_social") or {})
        if perfil.get("rede") == "instagram" and perfil.get("usuario"):
            try:
                import agente_local as A
                laco = asyncio.get_event_loop()
                r["instagram"] = await laco.run_in_executor(
                    None, lambda: A.consultar_instagram(perfil["usuario"]))
            except Exception as e:
                r["instagram"] = {"erro": f"{type(e).__name__}: {str(e)[:120]}"}
        return r
    except Exception as e:
        quebrou = True   # a sessão morreu no meio: não volta para a piscina
        return {"erro": f"{type(e).__name__}: {str(e)[:180]}"}
    finally:
        await _devolver(s, quebrou)


def consultar_maps(nome: str, cidade: str = "", usar_proxy: bool = True) -> dict:
    """Abre o Google Maps e lê o painel do estabelecimento.

    A primeira consulta paga a abertura da sessão; as seguintes reaproveitam,
    como o pipeline em lote faz — foi assim que 40 lojas saíram a 6 s cada.
    """
    if not nome or not nome.strip():
        return {"erro": "informe o nome do estabelecimento"}
    try:
        return _rodar(_uma(nome.strip(), (cidade or "").strip(), usar_proxy),
                      MAX_SEGUNDOS)
    except FuturesTimeout:
        return {"erro": f"o Maps não respondeu em {MAX_SEGUNDOS}s"}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}


def encerrar_maps() -> None:
    """Fecha a sessão quente. Serve para desligar sem deixar navegador solto."""
    try:
        _rodar(_descartar(), 30)
    except Exception:
        pass


ESQUEMA = {
    "type": "function", "function": {
        "name": "consultar_maps",
        "description": (
            "FONTE PRINCIPAL de dados de estabelecimento. Abre o Google "
            "Maps e le o PAINEL: endereco com numero, telefone, horario de "
            "cada dia, nota, categoria, fotos e coordenada — tudo de uma vez. "
            "E o cartao a direita da busca, que NENHUMA busca de paginas "
            "alcanca: guia local e diretorio de empresas nao tem horario, nem "
            "foto, nem coordenada, e costumam ter endereco desatualizado. "
            "CHAME SEMPRE que pedirem endereco, telefone ou horario de um "
            "comercio, mesmo que a busca web ja tenha mostrado algo — e cite "
            "`consultar_maps` como fonte do que vier daqui. "
            "Se `confere_com_o_nome_pedido` for falso, leia `veredito_da_ia`: "
            "outra IA ja comparou nome, endereco, telefone e categoria e disse "
            "se e o mesmo lugar."),
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "cidade": {"type": "string"}}, "required": ["nome"]}},
}
