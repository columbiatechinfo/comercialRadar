"""
human_browser.py — Sessão de browser humanizada e econômica (Playwright)

Encapsula um launch_persistent_context com:
  - Fingerprint coeso randomizado (UA, viewport, timezone BR, locale pt-BR)
  - Proxy nativo por contexto (1 IP por lote)
  - playwright-stealth (mascara webdriver/canvas/WebGL)
  - Route blocking agressivo por camada (SERP barata vs Maps coleta rica)
  - Medição de banda real (request.sizes → responseBodySize+headers)
  - Métodos humanizados: goto, wait, click, explore_panel

A "fase fotos" NÃO baixa imagens via proxy — só extraímos URLs no scraper.
"""

import random
import asyncio
from pathlib import Path
from typing import Optional

import config
import os

#: A SESSAO HUMANA NO CAMOUFOX (18/09/2026). A etapa 9 da extracao (telefone e site pelo painel do Maps) abria
#: Chromium com identidade forjada a mao — o mesmo navegador que, com proxy Webshare, pendurava no google.com e
#: queimava IP no detalhe do Maps (19 trocas em 3 min, contra 0 no Camoufox). O Camoufox monta idioma, fuso, tela e
#: agente coerentes com o IP (`geoip`); forcar isso por fora e justamente o que o site procura. Liga com
#: `HUMANO_CAMOUFOX=1` ate a medicao dizer que pode ser o padrao.
HUMANO_CAMOUFOX = os.environ.get("HUMANO_CAMOUFOX") == "1"

# Stealth (API nova: Stealth().apply_stealth_async). Degrada se indisponível.
try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
except Exception:
    _STEALTH = None


async def _aplicar_stealth(context):
    if _STEALTH is None:
        return
    try:
        await _STEALTH.apply_stealth_async(context)
    except Exception:
        pass


def tz_para_lng(lng: Optional[float]) -> str:
    """Timezone BR coerente com a longitude do POI (heurística simples)."""
    if lng is None:
        return random.choice(config.TIMEZONES_BR)
    # Faixa oeste (Acre/Amazonas) UTC-5/-4; centro-oeste UTC-4; resto UTC-3
    if lng < -67:
        return "America/Rio_Branco"
    if lng < -58:
        return "America/Manaus"
    if lng < -52:
        return "America/Cuiaba"
    return "America/Sao_Paulo"


class HumanSession:
    """Uma sessão = um lote. Cria, processa POIs, fecha e recicla o IP."""

    def __init__(self):
        self.context = None
        self.page = None
        self.proxy = None
        self.fingerprint = {}
        self.layer = "maps"
        self.bytes_used = 0
        self._tasks = []

    # ──────────────────────────────────────────────────────────────────
    @classmethod
    async def create(cls, pw, proxy: dict, user_data_dir: Path,
                     layer: str = "maps", tz_hint_lng: float = None, headless: bool = True):
        self = cls()
        self.proxy = proxy
        self.layer = layer

        ua = random.choice(config.USER_AGENTS)
        viewport = random.choice(config.VIEWPORTS)
        timezone_id = tz_para_lng(tz_hint_lng)

        self.fingerprint = {
            "user_agent": ua,
            "viewport": viewport,
            "timezone_id": timezone_id,
            "locale": config.LOCALE,
        }

        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        proxy_cfg = None
        if proxy:
            from proxy_pool import ProxyPool
            proxy_cfg = ProxyPool.to_playwright(proxy)

        if HUMANO_CAMOUFOX:
            from camoufox.async_api import AsyncCamoufox
            self._camoufox = AsyncCamoufox(headless=headless, geoip=True, locale="pt-BR", proxy=proxy_cfg,
                                           persistent_context=True, user_data_dir=str(user_data_dir))
            self.context = await self._camoufox.__aenter__()
            self.fingerprint = {"navegador": "camoufox", "locale": "pt-BR"}
            try:
                await self.context.add_cookies([
                    {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
                    {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAnB0IAEaBgiA_LyaBg",
                     "domain": ".google.com", "path": "/"},
                ])
            except Exception:
                pass
            await self.context.route("**/*", self._route_handler)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.on("requestfinished", self._on_request_finished)
            return self

        self.context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            proxy=proxy_cfg,
            viewport=viewport,
            locale=config.LOCALE,
            timezone_id=timezone_id,
            user_agent=ua,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                # HTTP/2 + PROXY + GOOGLE = TRAVA TOTAL. Medido em 26/08/2026.
                #
                # O sintoma não parece de rede: `ERR_TIMED_OUT` no
                # `page.goto("https://www.google.com/maps")`, o pool marca o IP
                # como queimado, entra em cooldown de 10 min e vai para o
                # próximo — que trava igual. Na run de Bento Gonçalves foram 15
                # IPs perdidos assim, e a leitura fácil ("os proxies morreram")
                # é justamente a errada.
                #
                # O MESMO IP, no MESMO instante, respondia 200 em 0,9 s num GET
                # direto ao mesmo endereço. E `example.com` e `bing.com` abriam
                # normalmente PELO NAVEGADOR através do mesmo proxy. O que
                # falhava era só a combinação Chromium + proxy + Google.
                #
                # Nem `wait_until="commit"` escapava: o navegador não recebia o
                # primeiro byte. Então não é página pesada nem sub-recurso
                # travado — é a negociação do protocolo. `--disable-quic` não
                # resolve; `--disable-http2` resolve.
                #
                # MEDIDO em 8 IPs: 0/8 abriam o Maps sem a flag (média 17,8 s
                # até estourar), 8/8 abriam com ela (média 2,8 s).
                #
                # É a mesma família da cicatriz de 24/07/2026, quando IPs da
                # Webshare "penduravam no google.com só pelo navegador".
                "--disable-http2",
                f"--window-size={viewport['width']},{viewport['height']}",
            ],
        )

        await _aplicar_stealth(self.context)

        # Cookies de consentimento: pulam a tela consent.google.com (que em alguns
        # IPs trava toda a interação). YES+ dispensa o interstitial de cookies.
        try:
            await self.context.add_cookies([
                {"name": "CONSENT", "value": "YES+cb", "domain": ".google.com", "path": "/"},
                {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAnB0IAEaBgiA_LyaBg",
                 "domain": ".google.com", "path": "/"},
            ])
        except Exception:
            pass

        # Bloqueio de recursos no nível do contexto (vale para todas as páginas)
        await self.context.route("**/*", self._route_handler)

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

        # Medição de banda: soma o tamanho real transferido de cada request
        self.page.on("requestfinished", self._on_request_finished)

        return self

    # ──────────────────────────────────────────────────────────────────
    # Route blocking
    # ──────────────────────────────────────────────────────────────────
    async def _route_handler(self, route):
        try:
            req = route.request
            rt = req.resource_type
            url = req.url

            # Sempre bloqueia CSS, fontes, mídia (vídeo/áudio)
            if rt in config.BLOCK_RESOURCE_TYPES_ALWAYS:
                return await route.abort()

            # Telemetria / ads sempre fora
            if any(s in url for s in config.BLOCK_URL_SUBSTRINGS):
                return await route.abort()

            if rt == "image":
                if self.layer == "serp":
                    # Camada 1 (validação barata): sem imagens
                    return await route.abort()
                # Camada 2 (Maps): permite imagens, MAS bloqueia avatars de reviewers
                if any(s in url for s in config.AVATAR_URL_SUBSTRINGS):
                    return await route.abort()

            return await route.continue_()
        except Exception:
            try:
                return await route.continue_()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────────
    # Medição de banda
    # ──────────────────────────────────────────────────────────────────
    def _on_request_finished(self, request):
        if getattr(self, "_closing", False):
            return
        try:
            self._tasks.append(asyncio.create_task(self._acc_size(request)))
        except Exception:
            pass

    async def _acc_size(self, request):
        try:
            s = await request.sizes()
            self.bytes_used += int(s.get("responseBodySize", 0) or 0)
            self.bytes_used += int(s.get("responseHeadersSize", 0) or 0)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # Ações humanizadas
    # ──────────────────────────────────────────────────────────────────
    async def humanized_goto(self, url: str, timeout: int = 30000):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await self.page.wait_for_timeout(int(random.uniform(800, 1800)))

    async def humanized_wait(self):
        """Delay natural entre buscas, com 5% de chance de pausa longa."""
        if random.random() < config.LONG_PAUSE_CHANCE:
            pausa = random.uniform(config.LONG_PAUSE_MIN_S, config.LONG_PAUSE_MAX_S)
            await asyncio.sleep(pausa)
            return ("longa", pausa)
        d = random.uniform(config.DELAY_MIN_S, config.DELAY_MAX_S)
        await asyncio.sleep(d)
        return ("normal", d)

    async def humanized_click(self, locator, timeout: int = 3000):
        try:
            await locator.hover(timeout=timeout)
            await self.page.wait_for_timeout(int(random.uniform(120, 400)))
        except Exception:
            pass
        await locator.click(timeout=timeout)

    async def explore_panel(self):
        """Antes de extrair: espera 2-5s e dá um scroll leve no painel."""
        await self.page.wait_for_timeout(int(random.uniform(
            config.EXPLORE_MIN_S * 1000, config.EXPLORE_MAX_S * 1000)))
        try:
            await self.page.mouse.wheel(0, random.randint(150, 500))
            await self.page.wait_for_timeout(int(random.uniform(300, 900)))
            await self.page.mouse.wheel(0, -random.randint(50, 200))
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    async def is_captcha(self) -> bool:
        """Detecta página de bloqueio do Google (sorry/index) ou reCAPTCHA."""
        try:
            url = self.page.url or ""
            if "/sorry/" in url or "sorry/index" in url:
                return True
            # reCAPTCHA visível
            if await self.page.locator('iframe[src*="recaptcha"]').count() > 0:
                return True
            corpo = await self.page.title()
            if corpo and ("unusual traffic" in corpo.lower()):
                return True
        except Exception:
            pass
        return False

    async def close(self):
        self._closing = True
        # Aguarda tarefas de medição pendentes (curtas)
        if self._tasks:
            try:
                await asyncio.wait(self._tasks, timeout=2)
            except Exception:
                pass
        try:
            if getattr(self, "_camoufox", None) is not None:
                await self._camoufox.__aexit__(None, None, None)
            else:
                await self.context.close()
        except Exception:
            pass
