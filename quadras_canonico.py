# -*- coding: utf-8 -*-
"""Nome canônico de uma via — lança um ponto no Maps e lê o título do panorama.

O OSM erra e abrevia nome de rua; o Google tem o nome que a prefeitura usa. Para
descobrir o canônico basta abrir o panorama sobre a via e ler o título
("645 R. do Alecrim - Google Maps") — sem print, sem OCR, sem IA.

UMA consulta por via, na coordenada central dela. Antes eram 3 pontos por FACE
de quadra, o que repetia a mesma rua várias vezes: numa área com 40 quadras a
mesma avenida era consultada dezenas de vezes. Por via, cada rua é perguntada
uma vez só e todas as faces que encostam nela herdam a resposta.

O proxy segue o padrão da busca de POIs: um RELAY local que injeta a
autenticação: passar usuário e senha direto no `--proxy-server` do Chromium
pendura a conexão no google.com (o urllib com o mesmo IP responde em 1,9 s).
"""
from __future__ import annotations

import asyncio
import base64
import re

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_RE_CAM = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),3a")
# o número é OPCIONAL: para nomear a via só interessa o nome, e rua sem
# endereçamento devolve título só com o logradouro
_RE_TITULO = re.compile(r"^\s*(?:(\d{1,6})\s+)?(.+?)\s+-\s+Google\s+Maps", re.I)
# depois de N vias seguidas sem resposta o IP está pendurado — troca de passada
SEM_RESPOSTA_MAX = 4
# abas simultâneas: o custo por via é espera de rede, não CPU
ABAS = 6
# quanto esperar o redirecionamento que revela o panorama. Ele acontece nos
# primeiros segundos; os 30 s antigos eram pagos INTEIROS por toda via sem
# panorama, e uma delas sozinha segurava a passada.
ESPERA_PANO_S = 12.0
# e o título, que vira endereço depois de aparecer como coordenada
TENTATIVAS_TITULO = 10


class RelayProxy:
    """Repassa o tráfego do navegador ao proxy autenticado, injetando o
    `Proxy-Authorization`. O Chromium nunca vê a credencial."""

    def __init__(self, host: str, porta: int, usuario: str, senha: str):
        self.up = (host, int(porta))
        cred = base64.b64encode(f"{usuario}:{senha}".encode()).decode()
        self.auth = f"Proxy-Authorization: Basic {cred}\r\n".encode()
        self.server = None
        self.porta_local = None
        self._vivos: set = set()

    async def start(self):
        self.server = await asyncio.start_server(self._cliente, "127.0.0.1", 0)
        self.porta_local = self.server.sockets[0].getsockname()[1]
        return self.porta_local

    async def _cliente(self, leitor, escritor):
        alvo_r = alvo_w = None
        self._vivos.add(asyncio.current_task())
        try:
            cab = await asyncio.wait_for(leitor.readuntil(b"\r\n\r\n"), timeout=20)
            alvo_r, alvo_w = await asyncio.wait_for(
                asyncio.open_connection(*self.up), timeout=20)
            linha, _, resto = cab.partition(b"\r\n")
            alvo_w.write(linha + b"\r\n" + self.auth + resto)
            await alvo_w.drain()
            await asyncio.gather(self._pipe(leitor, alvo_w),
                                 self._pipe(alvo_r, escritor),
                                 return_exceptions=True)
        except Exception:
            pass
        finally:
            self._vivos.discard(asyncio.current_task())
            for w in (escritor, alvo_w):
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass

    @staticmethod
    async def _pipe(r, w):
        try:
            while True:
                b = await r.read(65536)
                if not b:
                    break
                w.write(b)
                await w.drain()
        except Exception:
            pass

    async def stop(self):
        """Cancela as conexões ainda abertas antes de fechar. Sem isso o
        interpretador despeja 'Task was destroyed but it is pending' para cada
        túnel vivo — ruído que esconde erro de verdade no log."""
        for t in list(self._vivos):
            t.cancel()
        if self._vivos:
            await asyncio.gather(*self._vivos, return_exceptions=True)
        self._vivos.clear()
        if self.server:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass


def _upstreams() -> list[tuple]:
    """O mesmo pool que a busca de POIs usa."""
    try:
        import proxy_manager
        return list(proxy_manager.PROXIES or [])
    except Exception:
        return []


def nome_do_titulo(titulo: str) -> str:
    m = _RE_TITULO.match(titulo or "")
    return (m.group(2) or "").strip() if m else ""


def endereco_do_titulo(titulo: str):
    """(numero, via) do título. O número é o que ancora a numeração de uma face
    no chão — sem ele a régua só sabe proporção, não sabe onde começa."""
    m = _RE_TITULO.match(titulo or "")
    if not m:
        return None, ""
    n = None
    if m.group(1):
        try:
            v = int(m.group(1))
            n = v if 0 < v < 100000 else None
        except ValueError:
            n = None
    return n, (m.group(2) or "").strip()


async def _achar_pano(page, lat, lng, espera=ESPERA_PANO_S):
    """Abre o panorama e devolve a posição da câmera, ou None se não houver.

    Espera o `commit`, não o `load`: pelo proxy residencial o Maps leva mais de
    45 s para terminar de carregar, mas o redirecionamento que revela o panorama
    acontece nos primeiros segundos."""
    q = (f"https://www.google.com/maps/@?api=1&map_action=pano"
         f"&viewpoint={lat},{lng}&hl=pt-BR")
    try:
        await page.goto(q, wait_until="commit", timeout=90000)
    except Exception:
        return None
    fim = asyncio.get_event_loop().time() + espera
    while asyncio.get_event_loop().time() < fim:
        if _RE_CAM.search(page.url):
            return True
        # ',0a,' é a marca de "não há panorama aqui"
        if re.search(r"/@-?\d+\.\d+,-?\d+\.\d+,0a", page.url):
            return None
        await page.wait_for_timeout(400)
    return None


async def _titulo_do_ponto(page, lat, lng) -> str:
    if not await _achar_pano(page, lat, lng):
        return ""
    titulo = ""
    for _ in range(TENTATIVAS_TITULO):
        await page.wait_for_timeout(500)
        titulo = await page.title()
        # o título começa como coordenada em graus e depois vira o endereço
        if titulo and "Google Maps" in titulo and "°" not in titulo:
            break
    return titulo


async def _nome_do_ponto(page, lat, lng) -> str:
    return nome_do_titulo(await _titulo_do_ponto(page, lat, lng))


# abaixo desta fração de leitura a passada não valeu — aí sim vale trocar de IP
FRACAO_OK = 0.70


async def _passada_endereco(alvos: list[dict], porta: int | None, abas: int = ABAS) -> int:
    """Como `_passada`, mas guarda NÚMERO e via — é a âncora de campo de uma face.

    Preenche 'numero' e 'via' em cada alvo. Usa a mesma infraestrutura de relay e
    abas; só muda o que se extrai do título."""
    from playwright.async_api import async_playwright
    pend = [a for a in alvos if a.get("numero") is None and not a.get("lido")]
    if not pend:
        return 0
    lidos = 0
    async with async_playwright() as p:
        arg = [f"--proxy-server=http://127.0.0.1:{porta}"] if porta else []
        nav = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"] + arg)
        ctx = await nav.new_context(user_agent=UA, locale="pt-BR",
                                    viewport={"width": 1000, "height": 700})
        await ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
            {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAnB0IAEaBgiA_LyaBg",
             "domain": ".google.com", "path": "/"}])
        fila = asyncio.Queue()
        for a in pend:
            fila.put_nowait(a)
        estado = {"sem": 0}

        async def worker():
            page = await ctx.new_page()
            try:
                while True:
                    try:
                        a = fila.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if porta and estado["sem"] >= SEM_RESPOSTA_MAX:
                        return
                    try:
                        t = await _titulo_do_ponto(page, a["lat"], a["lng"])
                        a["numero"], a["via"] = endereco_do_titulo(t)
                        a["titulo"] = t
                    except Exception as e:
                        a["erro"] = str(e)[:120]
                    a["lido"] = True
                    estado["sem"] = 0 if a.get("via") else estado["sem"] + 1
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

        await asyncio.gather(*[worker() for _ in range(min(abas, len(pend)))],
                             return_exceptions=True)
        await ctx.close()
        await nav.close()
    return sum(1 for a in alvos if a.get("numero") is not None)


async def enderecos(alvos: list[dict], usar_proxy: bool = True) -> int:
    """Lê o endereço COM NÚMERO de cada alvo. Mesma política de `nomear`."""
    ups = _upstreams() if usar_proxy else []
    for host, porta, usuario, senha in ups[:2]:
        relay = RelayProxy(host, porta, usuario, senha)
        try:
            p = await relay.start()
            n = await _passada_endereco(alvos, p)
            if n >= FRACAO_OK * len(alvos):
                return n
        except Exception:
            pass
        finally:
            await relay.stop()
    await _passada_endereco(alvos, None)
    return sum(1 for a in alvos if a.get("numero") is not None)


async def _passada(alvos: list[dict], porta: int | None, abas: int = ABAS) -> int:
    """Uma varredura dos alvos ainda sem nome, em N abas ao mesmo tempo.

    Cada via custa ~6 s de espera de rede — tempo ocioso, não CPU. Em série,
    24 vias levavam 139 s. As abas dividem a fila e esperam juntas."""
    from playwright.async_api import async_playwright
    pend = [a for a in alvos if not a.get("nome_canonico")]
    if not pend:
        return len(alvos)
    async with async_playwright() as p:
        arg = [f"--proxy-server=http://127.0.0.1:{porta}"] if porta else []
        nav = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"] + arg)
        ctx = await nav.new_context(user_agent=UA, locale="pt-BR",
                                    viewport={"width": 1000, "height": 700})
        await ctx.add_cookies([
            {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
            {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAnB0IAEaBgiA_LyaBg",
             "domain": ".google.com", "path": "/"}])
        fila = asyncio.Queue()
        for a in pend:
            fila.put_nowait(a)
        # o contador de "sem resposta" é COMPARTILHADO: IP pendurado derruba
        # todas as abas juntas, e insistir só gasta tempo
        estado = {"sem": 0}

        async def worker():
            page = await ctx.new_page()
            try:
                while True:
                    try:
                        a = fila.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if porta and estado["sem"] >= SEM_RESPOSTA_MAX:
                        return
                    try:
                        nome = await _nome_do_ponto(page, a["lat"], a["lng"])
                    except Exception as e:
                        nome, a["erro"] = "", str(e)[:120]
                    a["nome_canonico"] = nome or None
                    estado["sem"] = 0 if nome else estado["sem"] + 1
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

        await asyncio.gather(*[worker() for _ in range(min(abas, len(pend)))],
                             return_exceptions=True)
        await ctx.close()
        await nav.close()
    return sum(1 for a in alvos if a.get("nome_canonico"))


async def nomear(alvos: list[dict], usar_proxy: bool = True) -> int:
    """`alvos` = [{'lat','lng', ...}]. Preenche 'nome_canonico' em cada um.

    Uma nova passada custa um navegador novo (~10 s), então ela só acontece se a
    anterior foi RUIM. Perseguir a última via faltante trocando de proxy gastava
    45 s para ler 6 vias — e rua sem panorama não vai aparecer por insistência.
    Se nada foi lido, aí sim o IP está pendurado e vale trocar; ao fim, tenta
    DIRETO — foi assim que se viu que o problema era o Chromium com credencial
    inline, não o IP."""
    def _lidos():
        return sum(1 for a in alvos if a.get("nome_canonico"))

    if _lidos() == len(alvos):
        return len(alvos)
    ups = _upstreams() if usar_proxy else []
    for host, porta, usuario, senha in ups[:3]:
        relay = RelayProxy(host, porta, usuario, senha)
        try:
            p = await relay.start()
            await _passada(alvos, p)
            if _lidos() >= FRACAO_OK * len(alvos):
                return _lidos()
        except Exception:
            pass
        finally:
            await relay.stop()
    if _lidos() < FRACAO_OK * len(alvos):
        await _passada(alvos, None)
    return _lidos()
