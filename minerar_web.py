"""
minerar_web.py — Recuperação do RESÍDUO via mineração web barata (substitui o Gemini).

Para cada POI que falhou no Maps (nao_encontrado / erro / encontrado_divergente):
  1. Busca "nome cidade UF" no YAHOO BR via Playwright + proxy + stealth. Por quê
     Yahoo: Google dá CAPTCHA na hora; Bing detecta o fingerprint e serve
     resultados-ISCA (lixo proposital); DDG/Mojeek/Ecosia bloqueiam. O Yahoo usa o
     índice do Bing (acha Instagram/iFood/Aiqfome igual) com gating tolerante.
  2. Coleta snippets do SERP + visita as top-5 páginas (aiohttp, sites comuns não bloqueiam).
  3. PRÉ-FILTRO LOCAL (regex/heurística, custo zero): telefones BR, CNPJs, CEPs,
     endereços compatíveis com a cidade, instagram, e-mail, horários, avaliações.
  4. Digest MÍNIMO (evidências úteis) → OpenAI gpt-4o-mini (o LLM mais barato) só
     para limpar/estruturar em JSON.
  5. CNPJ candidato → BrasilAPI (dados abertos da Receita Federal, GRÁTIS):
     razão social, nome fantasia, natureza jurídica, CNAE, situação cadastral,
     QSA (sócios), e-mail/telefone oficiais. Dado VERIFICADO, sem alucinação.
  6. Atualiza o registro no próprio JSON da planilha (status='recuperado_web',
     fonte_dado='web') — o watcher do server ingere e joga no mapa em tempo real.

Custo típico: ~US$0,0004/POI de tokens (gpt-4o-mini) + buscas/Receita grátis.

USO:
  .venv\\Scripts\\python minerar_web.py --json uploads/planilha_db.json
     [--limit N] [--workers 6] [--area areas/area_atual.json]
"""

import os
import re
import json
import time
import base64
import asyncio
import argparse
import threading
from pathlib import Path
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import aiohttp
from playwright.async_api import async_playwright

import config  # .env + UTF-8
import area_utils
import io_atomico

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"}

MAX_PAG = 5           # páginas visitadas por POI
MAX_HTML = 400_000    # bytes máximos lidos por página
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# domínios que não valem visita (motores, redes que exigem login pesado)
SKIP_DOM = ("bing.", "microsoft.", "duckduckgo.", "google.", "youtube.", "facebook.com/login",
            "linkedin.", "twitter.", "x.com", "tiktok.")

# ──────────────────────────────────────────────────────────────────────────
# Regex de extração (pré-filtro local — não gasta token)
# ──────────────────────────────────────────────────────────────────────────
RE_TEL = re.compile(r"\(?\b(\d{2})\)?[\s.-]?(9?\d{4})[\s.-]?(\d{4})\b")
RE_CNPJ = re.compile(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b")
RE_CEP = re.compile(r"\b\d{5}-?\d{3}\b")
RE_INSTA = re.compile(r"instagram\.com/([A-Za-z0-9_.]{3,30})")
RE_ARROBA = re.compile(r"@([a-z0-9_.]{3,30})\b")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
RE_NOTA = re.compile(r"\b([1-5][.,]\d)\s*(?:de 5|/5|estrelas|★)|nota\s*([1-5][.,]\d)", re.I)
RE_END = re.compile(r"\b(rua|av\.|avenida|travessa|tv\.|pra[çc]a|rodovia|br-\d+|estrada|beco|quadra)\b[^|\n<>]{5,90}", re.I)
RE_HORA = re.compile(r"[^|\n<>]{0,30}\b(seg|ter|qua|qui|sex|s[áa]b|dom|segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)\w*[^|\n<>]{0,60}\d{1,2}[:h]\d{0,2}[^|\n<>]{0,40}", re.I)
RE_TAG = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>")


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", RE_TAG.sub(" ", html))


_MUN_RF: dict = {}


def _receita_local(d: str) -> dict | None:
    """O CNPJ na base nacional que já está no banco. Mesmo formato da BrasilAPI.

    Devolve None quando não existe — e não existir é resposta boa: significa que
    o número achado na página não é um estabelecimento de verdade."""
    try:
        import base_comum as _bc
        con = _bc.conectar()
    except Exception:
        return None
    try:
        with con.cursor() as cur:
            cur.execute("""
                SELECT m.razao_social, e.nome_fantasia, e.situacao_cadastral,
                       e.cnae_principal, e.uf, e.municipio, m.natureza_juridica,
                       e.logradouro, e.numero, e.bairro, e.cep
                  FROM rf_estabelecimentos e
                  LEFT JOIN rf_empresas m ON m.cnpj_basico = e.cnpj_basico
                 WHERE e.cnpj_basico=%s AND e.cnpj_ordem=%s AND e.cnpj_dv=%s""",
                (d[:8], d[8:12], d[12:]))
            r = cur.fetchone()
            if not r:
                return None
            cod = str(r[5] or "")
            if cod and cod not in _MUN_RF:
                cur.execute("SELECT descricao FROM rf_municipios WHERE codigo=%s",
                            (cod,))
                m = cur.fetchone()
                _MUN_RF[cod] = (m[0] if m else "")
        return {"cnpj": d, "razao_social": r[0] or "", "nome_fantasia": r[1] or "",
                "situacao_cadastral": r[2] or "", "cnae_fiscal": r[3] or "",
                "uf": r[4] or "", "municipio": _MUN_RF.get(cod, ""),
                "natureza_juridica": r[6] or "", "logradouro": r[7] or "",
                "numero": r[8] or "", "bairro": r[9] or "", "cep": r[10] or "",
                "qsa": [], "_fonte": "receita_local"}
    except Exception:
        return None
    finally:
        try:
            con.close()
        except Exception:
            pass


def _cnpj_valido(d: str) -> bool:
    """Dígito verificador do CNPJ. 14 dígitos não é o mesmo que CNPJ.

    Um pedaço de CPF, um código de nota, um telefone colado — tudo isso tem 14
    dígitos e passava como CNPJ, ia para a Receita e voltava vazio, gastando uma
    consulta. O DV custa nada e barra na origem."""
    if len(d) != 14 or d == d[0] * 14:
        return False
    for tam, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                       (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        soma = sum(int(d[i]) * pesos[i] for i in range(tam))
        resto = soma % 11
        if int(d[tam]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def _cnpj_limpo(c: str) -> str:
    """ACEITA pela ESTRUTURA: 14 dígitos. Regra do usuário.

    Dígito verificador, existir na base nacional e ser do município são
    CONFIANÇA (`_confianca_cnpj`), não porteiros. Exigi-los fazia perder CNPJ
    correto — e o que o usuário quer é o dado bruto para tratar depois."""
    d = re.sub(r"\D", "", c)
    return d if len(d) == 14 and d != d[0] * 14 else ""


def _confianca_cnpj(d: str, rf: dict | None, cidade: str, uf: str) -> tuple:
    """(nível 0–4, rótulo). Quanto mais evidência, maior o nível — nada barra."""
    n, marcas = 0, []
    if _cnpj_valido(d):
        n += 1
        marcas.append("dv")
    if rf:
        n += 1
        marcas.append("base_nacional")
        if uf and (rf.get("uf") or "").upper() == uf.upper():
            n += 1
            marcas.append("uf")
        if cidade and _sem_acento(rf.get("municipio") or "") == _sem_acento(cidade):
            n += 1
            marcas.append("municipio")
    return n, "+".join(marcas) or "so_estrutura"


import unicodedata


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode().strip()


_RE_UM_TEL = re.compile(r"(?:\+55\s?)?(?:\(\d{2}\)|\d{2})\s?\d{4,5}-?\d{4}")


def _tokens_nome(nome: str) -> set:
    stop = {"loja", "casa", "ponto", "studio", "salao", "salão", "ltda", "the", "das", "dos"}
    return {t for t in re.findall(r"[a-z0-9]{4,}", nome.lower()) if t not in stop}


def _relacionado(nome: str, texto: str) -> bool:
    """True se o texto compartilha algum token distintivo com o nome do lugar."""
    t = (texto or "").lower()
    return any(tok in t for tok in _tokens_nome(nome))


# ──────────────────────────────────────────────────────────────────────────
# Busca no Yahoo BR via Playwright (pool de sessões stealth) — retorna [(url, snippet)]
# ──────────────────────────────────────────────────────────────────────────
def _resolver_yahoo_url(href: str) -> str:
    """r.search.yahoo.com/...;_ylt=.../RU=<url-encoded>/RK=... → URL real."""
    if "search.yahoo.com" not in href:
        return href
    m = re.search(r"/RU=([^/]+)/", href)
    return unquote(m.group(1)) if m else ""


# ── Cascata de buscadores ───────────────────────────────────────────────────
# O Yahoo passou a devolver HTTP 500 com corpo VAZIO para os IPs do pool
# (medido em 05/08/2026: 4 IPs diferentes, todos 500; do IP de casa, 200 com os
# 7 resultados). Pelos MESMOS IPs, outros buscadores respondem normalmente:
#
#   duckduckgo  200 · 31.277 chars · 72 ocorrências do termo
#   lite_ddg    200 · 22.341 chars · 40
#   marginalia  200 · 37.868 chars · 32
#   bing        200 · 74.819 chars ·  8   (⚠ histórico de resultado-ISCA)
#   mojeek      200 ·  5.790 chars ·  1
#   yahoo       500 ·      0 chars ·  0
#
# Então trocar de BUSCADOR vem antes de trocar de IP: o IP não é o problema.
#
# Cascata de buscadores. Tudo passa pelo NAVEGADOR da HumanSession — a mesma
# infra que sustenta a coleta no Google Maps, que é o alvo mais difícil que
# existe. Buscar por `aiohttp` cru, com User-Agent fixo e sem cookie, é entregar
# o pescoço ao detector: foi por isso que o DuckDuckGo começou a recusar assim
# que o job subiu para 6 workers.
MOTORES = ("searxng", "duckduckgo", "lite_ddg", "mojeek", "brave", "yahoo", "bing")
# SearXNG PRÓPRIO é o melhor caminho: metabuscador que consulta dezenas de
# motores numa consulta só, sem chave e SEM LIMITE quando roda na sua máquina.
# Sem `SEARXNG_URL` no .env ele é pulado sem custo.
# Aceita VÁRIAS urls separadas por vírgula, em ordem de preferência — o i9
# (servidor, sempre ligado) na frente e o WSL local de reserva. Quando a
# primeira falha ou limita, a consulta desce para a seguinte na hora; a punição
# de motor continua valendo para o "searxng" como um todo, não por instância.
SEARXNG_URLS = [u.strip().rstrip("/")
                for u in os.environ.get("SEARXNG_URL", "").split(",") if u.strip()]
SEARXNG_URL = SEARXNG_URLS[0] if SEARXNG_URLS else ""   # compat: quem só lê a 1ª

# NADA de matar buscador. Bloqueio é do MOMENTO, não da execução: o motor vai
# para o FIM da fila e continua sendo tentado. Antes, 3 recusas seguidas — que
# eram só limite de taxa — tiravam o DuckDuckGo do ar pelo resto do job e a
# cascata desabava no Bing, que serve resultado-isca.
_PENA: dict = {}          # motor → peso; maior = mais para o fim da fila
PENA_MAX = 8.0            # teto: nenhum motor some de vez
PENA_ALIVIO = 0.5         # cada acerto perdoa metade da pena acumulada


def _ordem_motores() -> list:
    return sorted(MOTORES, key=lambda m: _PENA.get(m, 0.0))


def _punir(m: str, peso: float = 1.0):
    _PENA[m] = min(PENA_MAX, _PENA.get(m, 0.0) + peso)


def _perdoar(m: str):
    if _PENA.get(m):
        _PENA[m] = max(0.0, _PENA[m] * PENA_ALIVIO)

_URL_MOTOR = {
    "yahoo":      "https://br.search.yahoo.com/search?p={q}&n=10",
    "duckduckgo": "https://html.duckduckgo.com/html/?q={q}",
    "lite_ddg":   "https://lite.duckduckgo.com/lite/?q={q}",
    "mojeek":     "https://www.mojeek.com/search?q={q}",
    "brave":      "https://search.brave.com/search?q={q}",
    "bing":       "https://www.bing.com/search?q={q}&setlang=pt-br",
}
# Extração por REGEX porque não há parser HTML no ambiente (sem bs4/lxml). Os
# buscadores de HTML puro têm marcação regular o bastante para isso.
_RE_LINK_MOTOR = {
    "duckduckgo": re.compile(r'class="result__a"[^>]*href="([^"]+)"'),
    "lite_ddg":   re.compile(r'class="result-link"[^>]*href="([^"]+)"'),
    "mojeek":     re.compile(r'<a class="ob"[^>]*href="([^"]+)"'),
    "marginalia": re.compile(r'<a[^>]+class="[^"]*result[^"]*"[^>]*href="([^"]+)"'),
}
_RE_UDDG = re.compile(r"uddg=([^&]+)")

# (container do resultado, link do título) por buscador — usado no DOM da página
_SEL_MOTOR = {
    "yahoo":      ("#web li", "h3 a"),
    "duckduckgo": ("div.result", "a.result__a"),
    "lite_ddg":   ("tr", "a.result-link"),
    "mojeek":     ("ul.results-standard li", "a.ob"),
    "brave":      ("div.snippet", "a"),
    "bing":       ("li.b_algo", "h2 a"),
}


def _limpa_link(u: str) -> str:
    """O DuckDuckGo embrulha o destino em `?uddg=<url>`; desembrulha."""
    m = _RE_UDDG.search(u or "")
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    if u.startswith("//"):
        return "https:" + u
    return u


# 429/503 é "vá mais devagar", não "você está banido". O motor apenas vai
# para o fim da fila (`_punir` leve) e continua no jogo.
LIMITADO = object()


async def _searxng(session, query: str):
    """SearXNG próprio, em JSON. Uma consulta → dezenas de motores agregados.

    Vai por HTTP DIRETO, **sem proxy e sem navegador**: o serviço é seu, roda na
    sua máquina, e não há detector para enganar. Passá-lo pelo proxy Webshare
    fazia `localhost` ser resolvido do lado do proxy — ou seja, lugar nenhum.
    Também não precisa de stealth: aqui a preocupação é o oposto, é velocidade.

    Ele já traz a resiliência de graça: numa consulta de teste o Brave estava
    suspenso por excesso e o Startpage em CAPTCHA, e ainda vieram 29 resultados
    dos outros motores agregados."""
    if not SEARXNG_URLS:
        return LIMITADO          # não configurado: pula sem punir
    limitado = False
    for base in SEARXNG_URLS:    # i9 primeiro; o local só se ele falhar
        url = (f"{base}/search?q={quote_plus(query)}"
               f"&format=json&language=pt-BR")
        try:
            async with session.get(url,
                                   timeout=aiohttp.ClientTimeout(total=40)) as r:
                if r.status in (429, 503):
                    limitado = True
                    continue
                if r.status >= 400:
                    continue
                dados = await r.json(content_type=None)
        except Exception:
            continue             # instância fora do ar → tenta a próxima
        return [(x.get("url", ""), (x.get("content") or "")[:340])
                for x in dados.get("results", [])[:12] if x.get("url")]
    return LIMITADO if limitado else None


async def _humanizar(page):
    """Pausa e mexe a página como gente. O detector não olha só o IP: olha
    cadência, movimento e se a sessão parece um robô batendo em sequência.
    A coleta do Maps sobrevive por causa disso — o SERP passou a ter o mesmo."""
    await page.wait_for_timeout(600 + int(1400 * os.urandom(1)[0] / 255))
    try:
        await page.mouse.move(120 + os.urandom(1)[0] % 600,
                              180 + os.urandom(1)[0] % 400)
        if os.urandom(1)[0] > 160:
            await page.mouse.wheel(0, 200 + os.urandom(1)[0] % 500)
            await page.wait_for_timeout(200 + os.urandom(1)[0] % 400)
    except Exception:
        pass


# Home + campo de busca de cada motor. DIGITAR a consulta no campo é bem mais
# parecido com gente do que montar a URL do resultado na mão: gera a navegação
# da home, o foco no input, o ritmo de tecla e o submit — sinais que o detector
# usa. É o mesmo princípio que sustenta a coleta no Maps.
_HOME_MOTOR = {
    "duckduckgo": ("https://html.duckduckgo.com/html/", "input[name=q]"),
    "lite_ddg":   ("https://lite.duckduckgo.com/lite/", "input[name=q]"),
    "mojeek":     ("https://www.mojeek.com/", "input[name=q]"),
    "brave":      ("https://search.brave.com/", "input[name=q]"),
    "yahoo":      ("https://br.search.yahoo.com/", "input[name=p]"),
    "bing":       ("https://www.bing.com/", "textarea[name=q], input[name=q]"),
}


async def _digitar_busca(page, motor: str, query: str) -> bool:
    """Abre a home e DIGITA a consulta. False se o campo não apareceu."""
    home, sel = _HOME_MOTOR.get(motor, (None, None))
    if not home:
        return False
    try:
        await page.goto(home, wait_until="domcontentloaded", timeout=20000)
        campo = page.locator(sel).first
        fim = time.time() + 6
        while time.time() < fim and await campo.count() == 0:
            await page.wait_for_timeout(300)
        if await campo.count() == 0:
            return False
        await campo.click(timeout=4000)
        # tecla a tecla, com ritmo irregular — digitação instantânea denuncia bot
        for ch in query:
            await page.keyboard.type(ch, delay=0)
            await page.wait_for_timeout(35 + os.urandom(1)[0] % 90)
        await page.wait_for_timeout(180 + os.urandom(1)[0] % 320)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=20000)
        return True
    except Exception:
        return False


async def _serp_motor(page, motor: str, query: str, digitar: bool = True) -> list | None:
    """Busca num buscador. None = BLOQUEADO (não é 'sem resultado').

    A diferença importa: o pool trata `None` como problema de IP e queima o
    proxy. Um HTTP 500 de corpo vazio — que é como o Yahoo recusa hoje — não tem
    a palavra 'captcha' em lugar nenhum, então era lido como `[]` = 'não achei',
    e o pool passou a queimar IP atrás de IP por um bloqueio que nenhum IP
    resolveria. 20 perfis criados numa execução só."""
    cont, link = _SEL_MOTOR[motor]
    resp = None
    if not (digitar and await _digitar_busca(page, motor, query)):
        # a digitação falhou (layout mudou, campo não veio): cai para a URL
        url = _URL_MOTOR[motor].format(q=quote_plus(query))
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            return None
    if resp is not None and resp.status in (429, 503):
        return LIMITADO
    if resp is not None and resp.status >= 400:
        return None                      # 500/403 = recusa, não vazio
    n = 0
    fim = time.time() + 8
    while time.time() < fim:
        n = await page.locator(cont).count()
        if n:
            break
        await page.wait_for_timeout(400)
    if not n:
        corpo = ""
        try:
            corpo = await page.inner_text("body", timeout=1500)
        except Exception:
            pass
        baixo = corpo.lower()
        if not corpo.strip():
            return None                  # corpo vazio = recusa silenciosa
        if "captcha" in baixo or "unusual" in baixo or "robot" in baixo:
            return None
        return []                        # respondeu, mas não achou nada
    brutos = await page.evaluate(
        """([cont, link]) => [...document.querySelectorAll(cont)]
             .filter(li => li.querySelector(link)).slice(0, 9).map(li => ({
               href: li.querySelector(link)?.href || '',
               texto: (li.textContent || '').replace(/\\s+/g, ' ').slice(0, 340),
             }))""", [cont, link])
    out = []
    for b in brutos:
        u = _resolver_yahoo_url(b.get("href", ""))
        if u and u.startswith("http"):
            out.append((u, b.get("texto", "")))
    return out


async def _serp_yahoo(page, query: str) -> list | None:
    """
    SERP no Yahoo Brasil (powered by Bing, mas com gating separado e tolerante).
    Bing direto DETECTA o fingerprint e serve resultados-ISCA (TikTok/counterfeit
    para qualquer query) — inutilizável. Google dá CAPTCHA. DDG/Mojeek bloqueiam.
    None = página de bloqueio (trocar IP). [] = sem resultados.
    """
    url = f"https://br.search.yahoo.com/search?p={quote_plus(query)}&n=10"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        # NÃO usar wait_for_selector: o HumanSession bloqueia CSS e o Playwright
        # nunca considera o elemento "visível" — polling por PRESENÇA no DOM.
        n = 0
        fim = time.time() + 8
        while time.time() < fim:
            n = await page.locator("#web h3").count()
            if n:
                break
            await page.wait_for_timeout(400)
        if not n:
            corpo = ""
            try:
                corpo = await page.inner_text("body", timeout=1500)
            except Exception:
                pass
            baixo = corpo.lower()
            if "captcha" in baixo or "unusual" in baixo or "robot" in baixo:
                return None  # bloqueio — este IP precisa descansar
            return []
        brutos = await page.evaluate(
            """() => [...document.querySelectorAll('#web li')].filter(li => li.querySelector('h3 a')).slice(0, 9).map(li => ({
                 href: li.querySelector('h3 a')?.href || '',
                 texto: (li.textContent || '').replace(/\\s+/g, ' ').slice(0, 340),
               }))"""
        )
    except Exception:
        return []
    out = []
    for b in brutos:
        u = _resolver_yahoo_url(b.get("href", ""))
        if u and u.startswith("http"):
            out.append((u, b.get("texto", "")))
    return out


class SerpPool:
    """
    Pool de sessões stealth (HumanSession + proxy Webshare) para o SERP do Yahoo BR.
    A busca crua (aiohttp) e o headless puro tomam challenge; com os IPs
    residenciais + fingerprint (mesma infra do Maps) o Bing responde normal.
    Challenge → cooldown do IP + sessão recriada com outro proxy.
    """

    def __init__(self, n: int, usar_proxy: bool = True, visivel: bool = False):
        self.n = n
        self.usar_proxy = usar_proxy
        # visivel=True abre o navegador na tela: mais parecido com uso real
        # (e o usuario ve o que esta acontecendo). Detector olha cadencia e
        # comportamento, nao so o IP.
        self.visivel = visivel
        self.fila: asyncio.Queue = asyncio.Queue()
        self._pw = None
        self._pool = None
        self._http = None
        self._seq = 0

    async def _nova_sessao(self) -> dict:
        from human_browser import HumanSession
        proxy = await self._pool.acquire_blocking() if self._pool else None
        if self._pool and proxy is None:
            # `acquire_blocking` desiste em ~120 s e devolve None. A sessão então
            # subia SEM PROXY, em silêncio, batendo no Yahoo pelo IP de casa —
            # que é justamente o que a infra de proxy existe para evitar.
            print("   ⚠️  Nenhum proxy livre (todos em cooldown) — esta sessão vai "
                  "pelo SEU IP. Se repetir, pare o job e espere o cooldown.",
                  flush=True)
        self._seq += 1
        profile = config.BROWSER_PROFILES_DIR / f"serpweb_{self._seq}"
        sess = await HumanSession.create(self._pw, proxy, profile, layer="maps",
                                         headless=not self.visivel)
        return {"sess": sess, "proxy": proxy, "profile": profile}

    async def _descartar(self, slot: dict, cooldown: int = 0):
        try:
            await slot["sess"].close()
        except Exception:
            pass
        import shutil
        shutil.rmtree(slot["profile"], ignore_errors=True)
        if slot["proxy"] and self._pool:
            if cooldown:
                await self._pool.mark_cooldown(slot["proxy"], cooldown)
            await self._pool.release(slot["proxy"])

    async def start(self):
        from proxy_pool import ProxyPool
        # sessão HTTP própria: os buscadores de HTML puro não passam pelo
        # navegador (a HumanSession corta recursos e eles voltam vazios)
        self._http = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=32, ssl=False))
        self._pw = await async_playwright().start()
        self._pool = ProxyPool().start() if self.usar_proxy else None
        config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for _ in range(self.n):
            self.fila.put_nowait(await self._nova_sessao())
        return self

    async def buscar(self, query: str) -> list:
        slot = await self.fila.get()
        try:
            for tentativa in range(2):
                # CASCATA: só depois que TODOS os buscadores recusarem é que o
                # problema pode ser o IP. Antes, um bloqueio do Yahoo condenava
                # o proxy — e o proxy não tinha nada a ver com isso.
                res = None
                pagina = slot["sess"].page
                for motor in _ordem_motores():
                    if motor == "searxng":
                        r = await _searxng(self._http, query)
                    else:
                        r = await _serp_motor(pagina, motor, query)
                    if r is LIMITADO:
                        # vivo, só pedindo calma: pena LEVE, vai para o fim da
                        # fila e continua no jogo
                        _punir(motor, 0.5)
                        await asyncio.sleep(1.5 + os.urandom(1)[0] / 160)
                        continue
                    if r is None:
                        _punir(motor, 1.0)   # recusou agora; volta depois
                        continue
                    _perdoar(motor)
                    res = r
                    if r:
                        await _humanizar(pagina)
                        break             # achou: para por aqui
                if res is None:
                    # nenhum buscador respondeu — AGORA sim o IP é suspeito
                    print("  ♻️  nenhum buscador respondeu — trocando proxy...",
                          flush=True)
                    await self._descartar(slot, cooldown=1800)
                    slot = await self._nova_sessao()
                    continue
                if res:
                    slot["vazias"] = 0
                    await slot["sess"].page.wait_for_timeout(
                        500 + int(900 * os.urandom(1)[0] / 255))
                    return res
                # vazio: pode ser query obscura OU IP degradado (SERP serve página
                # sem resultados). 3 vazios seguidos no mesmo slot → recicla o IP.
                slot["vazias"] = slot.get("vazias", 0) + 1
                if slot["vazias"] >= 3 and tentativa == 0:
                    print("  ♻️  IP degradado no SERP (vazios seguidos) — trocando...", flush=True)
                    await self._descartar(slot, cooldown=900)
                    slot = await self._nova_sessao()
                    continue
                return []
            return []
        finally:
            self.fila.put_nowait(slot)

    async def close(self):
        try:
            await self._http.close()
        except Exception:
            pass
        while not self.fila.empty():
            try:
                await self._descartar(self.fila.get_nowait())
            except Exception:
                pass
        try:
            await self._pw.stop()
        except Exception:
            pass


async def _fetch_pagina(session, url: str) -> str:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=18),
                               allow_redirects=True) as r:
            raw = await r.content.read(MAX_HTML)
        html = raw.decode("utf-8", "replace")
        # meta descriptions carregam a bio do Instagram/iFood mesmo com login-wall
        metas = re.findall(r'<meta[^>]+(?:name="description"|property="og:description"|property="og:title")[^>]+content="([^"]*)"', html)
        return " | ".join(metas) + " || " + _texto(html)[:6000]
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────
# Evidências → digest compacto
# ──────────────────────────────────────────────────────────────────────────
def _evidencias(texto: str, cidade: str) -> dict:
    ev = {"tel": set(), "cnpj": set(), "insta": set(), "email": set(),
          "end": [], "hora": [], "nota": [], "cep": set()}
    for m in RE_TEL.finditer(texto):
        ev["tel"].add(f"({m.group(1)}) {m.group(2)}-{m.group(3)}")
    for m in RE_CNPJ.finditer(texto):
        c = _cnpj_limpo(m.group(1))
        if c:
            ev["cnpj"].add(c)
    for m in RE_INSTA.finditer(texto):
        h = m.group(1).rstrip(".")
        if h not in ("p", "reel", "explore", "accounts", "stories"):
            ev["insta"].add(h.lower())
    for m in RE_EMAIL.finditer(texto):
        if not m.group(0).endswith((".png", ".jpg", ".svg")):
            ev["email"].add(m.group(0).lower())
    ev["cep"] |= set(RE_CEP.findall(texto))
    cid = cidade.lower()
    for m in RE_END.finditer(texto):
        linha = m.group(0).strip()
        ctx = texto[max(0, m.start() - 60):m.end() + 60].lower()
        # endereço só interessa se o contexto citar a cidade (compatibilidade geográfica)
        if cid and cid in ctx:
            ev["end"].append(linha[:110])
        elif not cid:
            ev["end"].append(linha[:110])
    for m in RE_HORA.finditer(texto):
        ev["hora"].append(m.group(0).strip()[:100])
    for m in RE_NOTA.finditer(texto):
        ev["nota"].append((m.group(1) or m.group(2)))
    return ev


def _digest(nome, cidade, uf, blocos: list) -> str:
    """blocos = [(fonte_curta, evidencias, trecho_meta)] → texto mínimo p/ o LLM."""
    linhas = []
    for fonte, ev, meta in blocos:
        partes = []
        if meta:
            partes.append(meta[:340])
        if ev["end"]:
            partes.append("END: " + " ; ".join(dict.fromkeys(ev["end"]))[:250])
        if ev["tel"]:
            partes.append("TEL: " + ", ".join(sorted(ev["tel"])[:4]))
        if ev["hora"]:
            partes.append("HORA: " + " ; ".join(dict.fromkeys(ev["hora"]))[:180])
        if ev["nota"]:
            partes.append("NOTA: " + ", ".join(ev["nota"][:3]))
        if ev["insta"]:
            partes.append("IG: " + ", ".join(sorted(ev["insta"])[:3]))
        if ev["email"]:
            partes.append("EMAIL: " + ", ".join(sorted(ev["email"])[:2]))
        if ev["cnpj"]:
            partes.append("CNPJ: " + ", ".join(sorted(ev["cnpj"])[:3]))
        if partes:
            linhas.append(f"[{fonte}] " + " | ".join(partes))
    return "\n".join(linhas)[:3200]


# ──────────────────────────────────────────────────────────────────────────
# LLM barato — só ESTRUTURA o que o pré-filtro achou (não busca nada).
# OpenAI gpt-4o-mini se houver chave; senão Gemini flash SEM thinking
# (para extração de texto curto o flash dá conta — a fraqueza dele era buscar).
# ──────────────────────────────────────────────────────────────────────────
_OPENAI = None
_USO = {"in": 0, "out": 0}
# Sem isto a chamada NÃO TEM TETO. Ela roda via `asyncio.to_thread`, então uma
# requisição pendurada segura a thread para sempre — e como a fase Web dispara
# tudo num `gather` único, bastam poucas presas para o job inteiro parar sem
# erro nenhum. Foi o que travou o enriquecimento de Canoas: 7 POIs e 1h33 de
# silêncio, processo vivo com CPU zero. A perna do Gemini já tinha timeout=60.
LLM_TIMEOUT_S = 45.0
LLM_TENTATIVAS = 2
_LLM_BACKEND = "openai" if os.environ.get("OPENAI_API_KEY", "").strip() else "gemini-flash"
_LLM_NOME = OPENAI_MODEL if _LLM_BACKEND == "openai" else "gemini-2.5-flash (sem thinking)"
_PRECO = (0.15, 0.60) if _LLM_BACKEND == "openai" else (0.30, 2.50)  # US$/M tokens


def _custo() -> float:
    return _USO["in"] / 1e6 * _PRECO[0] + _USO["out"] / 1e6 * _PRECO[1]


def _openai():
    global _OPENAI
    if _OPENAI is None:
        from openai import OpenAI
        _OPENAI = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip(),
                         timeout=LLM_TIMEOUT_S, max_retries=LLM_TENTATIVAS)
    return _OPENAI


def _prompt_estrutura(nome, cidade, uf, digest: str) -> str:
    return (
        f'Estabelecimento buscado: "{nome}" em {cidade}/{uf}.\n'
        f"Evidências coletadas na web (linhas [fonte] dado):\n{digest}\n\n"
        "Monte o cadastro usando SOMENTE as evidências acima (null quando não houver; "
        "não invente). Ignore dados claramente de OUTRO estabelecimento ou outra cidade.\n"
        'Responda SOMENTE JSON: {"encontrado":bool, "confianca":0a1, "endereco":str, '
        '"telefone":str, "categoria":str, "horario":str, "avaliacao":number, '
        '"resumo_avaliacoes":str, "instagram":str, "website":str, "email":str, "cnpj":str}'
    )


def _estruturar_gemini(prompt: str) -> dict:
    import urllib.request
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return {}
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"thinkingConfig": {"thinkingBudget": 0},
                             "responseMimeType": "application/json",
                             "temperature": 0},
    }).encode()
    for i in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "x-goog-api-key": key})
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            um = r.get("usageMetadata", {})
            _USO["in"] += um.get("promptTokenCount", 0)
            _USO["out"] += max(0, um.get("totalTokenCount", 0) - um.get("promptTokenCount", 0))
            return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])
        except Exception:
            time.sleep(2 * (i + 1))
    return {}


def _estruturar(nome, cidade, uf, digest: str) -> dict:
    prompt = _prompt_estrutura(nome, cidade, uf, digest)
    if _LLM_BACKEND != "openai":
        return _estruturar_gemini(prompt)
    try:
        resp = _openai().chat.completions.create(
            model=OPENAI_MODEL, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
            timeout=LLM_TIMEOUT_S,
        )
    except Exception as e:
        # estourar o teto é resultado vazio para ESTE POI, não parada do job
        print(f"   ⚠️  LLM falhou ({type(e).__name__}) — POI sem estruturação",
              flush=True)
        return {}
    if resp.usage:
        _USO["in"] += resp.usage.prompt_tokens
        _USO["out"] += resp.usage.completion_tokens
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────────────────
# BrasilAPI — dados oficiais da Receita Federal (grátis, dados abertos gov.br)
# ──────────────────────────────────────────────────────────────────────────
# Throttle global: no máx 6 consultas de CNPJ simultâneas (evita 429 em escala),
# com retry+backoff e fallback para minhareceita.org (dados abertos, sem rate-limit
# rígido). Assim nenhum CNPJ se perde por limite de API mesmo processando a cidade toda.
_SEM_RECEITA = asyncio.Semaphore(6)
_RECEITA_FONTES = (
    "https://brasilapi.com.br/api/cnpj/v1/{}",
    "https://minhareceita.org/{}",
)


async def _receita(session, cnpj: str) -> dict | None:
    async with _SEM_RECEITA:
        for base in _RECEITA_FONTES:
            for tentativa in range(3):
                try:
                    async with session.get(base.format(cnpj), headers=HEADERS,
                                           timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status == 200:
                            d = await r.json()
                            return d if d.get("cnpj") or d.get("razao_social") else None
                        if r.status == 429:            # rate limit → espera e retenta
                            await asyncio.sleep(1.5 * (tentativa + 1))
                            continue
                        if r.status in (404, 400):     # CNPJ inexistente → nem tenta a outra
                            return None
                        break                          # 5xx/outro → tenta a próxima fonte
                except Exception:
                    await asyncio.sleep(1.0 * (tentativa + 1))
        return None


def _aplica_receita(reg: dict, rf: dict):
    reg["cnpj"] = re.sub(r"(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})", r"\1.\2.\3/\4-\5", rf.get("cnpj", ""))
    reg["razao_social"] = rf.get("razao_social")
    reg["nome_fantasia"] = rf.get("nome_fantasia") or None
    reg["natureza_juridica"] = rf.get("natureza_juridica")
    cnae_c, cnae_d = rf.get("cnae_fiscal"), rf.get("cnae_fiscal_descricao")
    reg["cnae"] = f"{cnae_c} - {cnae_d}" if cnae_c else None
    reg["situacao_cadastral"] = rf.get("descricao_situacao_cadastral")
    qsa = [{"nome": s.get("nome_socio"), "qualificacao": s.get("qualificacao_socio")}
           for s in (rf.get("qsa") or [])]
    reg["socios"] = json.dumps(qsa, ensure_ascii=False) if qsa else None
    if not reg.get("email") and rf.get("email"):
        reg["email"] = str(rf["email"]).lower()
    if not reg.get("telefone") and rf.get("ddd_telefone_1"):
        reg["telefone"] = rf["ddd_telefone_1"]
    if not reg.get("endereco") and rf.get("logradouro"):
        reg["endereco"] = (f"{rf.get('descricao_tipo_de_logradouro', '')} {rf['logradouro']}, "
                           f"{rf.get('numero', '')} - {rf.get('bairro', '')}, "
                           f"{rf.get('municipio', '')} - {rf.get('uf', '')}, {rf.get('cep', '')}").strip()


# ──────────────────────────────────────────────────────────────────────────
# Pipeline por POI
# ──────────────────────────────────────────────────────────────────────────
def _cidade_do(reg: dict, cidade_arg: str) -> str:
    """Cidade DESTE POI — nunca um padrão fixo.

    Duas correções, e as duas vinham da época em que o único cliente era
    Parnaíba: só se olhava `endereco_planilha` (vazio em POI que veio da
    mineração, não de planilha), e o que sobrava era o literal "Parnaíba". Numa
    rodada de Canoas isso montava a busca `"Fulano" parnaiba RS` — cidade de um
    estado, UF de outro, lugar que não existe. Agora vale, em ordem: a cidade
    já gravada, o endereço do Maps, o da planilha. Sem nenhum: string vazia,
    que faz a busca ficar só com o nome — pior que o certo, melhor que errado."""
    if cidade_arg:
        return cidade_arg
    if reg.get("cidade"):
        return str(reg["cidade"]).strip()
    for campo in ("endereco", "endereco_planilha"):
        m = re.search(r"([A-Za-zÀ-ú ]{3,})\s*-\s*[A-Z]{2}", reg.get(campo) or "")
        if m:
            return m.group(1).strip()
    return ""


def _uf_do(reg: dict, uf_arg: str) -> str:
    """UF DESTE POI. O `run` pegava a UF do PRIMEIRO registro e aplicava a
    todos — funciona enquanto a área é de um estado só, e mente no dia em que
    não for."""
    if reg.get("uf"):
        return str(reg["uf"]).strip().upper()
    for campo in ("endereco", "endereco_planilha"):
        achados = re.findall(r"\b([A-Z]{2})\b(?:,|\s|$)", (reg.get(campo) or "").upper())
        if achados:
            return achados[-1]
    return uf_arg or ""


async def _processar_poi(session, serp: "SerpPool", reg: dict, cidade: str, uf: str, sem) -> bool:
    nome = reg.get("nome_planilha") or reg.get("nome") or ""
    async with sem:
        # 1) buscas (2 queries: dados gerais + cnpj)
        q1 = f'"{nome}" {cidade} {uf}'
        q2 = f'{nome} {cidade} cnpj'
        res = await serp.buscar(q1)
        if len(res) < 2:
            res += await serp.buscar(f"{nome} {cidade} {uf}")  # sem aspas (nomes com erro de grafia)
        res_cnpj = await serp.buscar(q2)

        # snippets do SERP já são evidência (bio do Instagram aparece aqui)
        blocos = []
        serp_txt = " || ".join(s for _, s in (res + res_cnpj) if s)
        if serp_txt:
            blocos.append(("serp", _evidencias(serp_txt, cidade), serp_txt[:400]))

        # A URL carrega o CNPJ — e SÓ o CNPJ. Os agregadores põem os 14 dígitos
        # no próprio endereço (`brasilcnpj.net/cnpj/rdk-logs-...-25025193000166`)
        # e é o único lugar de onde dá para tirá-los, porque essas páginas
        # bloqueiam scraping e voltam vazias.
        #
        # ⚠️ Jogar a URL no `_evidencias` inteiro é ARMADILHA: `RE_TEL` casa
        # qualquer corrida de 10–11 dígitos, então os dígitos do CNPJ viram
        # "telefone". Medido: dois POIs de Canoas saíram com (17) 8597-346x e um
        # com DDD 10, que não existe. Por isso aqui se colhe apenas a chave cnpj.
        urls_txt = " ".join(u for u, _ in (res + res_cnpj))
        cnpjs_url = {c for c in (_cnpj_limpo(m.group(1))
                                 for m in RE_CNPJ.finditer(urls_txt)) if c}
        if cnpjs_url:
            ev_url = {"tel": set(), "cnpj": cnpjs_url, "insta": set(), "email": set(),
                      "end": [], "hora": [], "nota": [], "cep": set()}
            blocos.append(("url", ev_url, ""))

        # 2) visita top-5 páginas úteis
        vistos, urls = set(), []
        for u, _ in res + res_cnpj:
            dom = urlparse(u).netloc.lower()
            if any(s in u.lower() or s in dom for s in SKIP_DOM):
                continue
            if dom in vistos:
                continue
            vistos.add(dom)
            urls.append(u)
            if len(urls) >= MAX_PAG:
                break
        paginas = await asyncio.gather(*[_fetch_pagina(session, u) for u in urls])
        for u, txt in zip(urls, paginas):
            if txt:
                blocos.append((urlparse(u).netloc, _evidencias(txt, cidade), ""))

        if not blocos:
            return False

        digest = _digest(nome, cidade, uf, blocos)
        if len(digest) < 30:
            return False

        # 3) LLM barato estrutura
        dados = await asyncio.to_thread(_estruturar, nome, cidade, uf, digest)
        if not dados:
            return False

        # 4) CNPJ → Receita Federal (BrasilAPI). Candidatos: LLM + regex.
        cnpjs = []
        if dados.get("cnpj"):
            c = _cnpj_limpo(str(dados["cnpj"]))
            if c:
                cnpjs.append(c)
        for _, ev, _m in blocos:
            cnpjs += [c for c in ev["cnpj"] if c not in cnpjs]
        # NADA BARRA AQUI. Regra do usuário: o critério de ACEITAÇÃO é ter a
        # estrutura de um CNPJ; dígito verificador, existir na base nacional e
        # ser do município são NÍVEIS DE CONFIANÇA, gravados junto para o
        # tratamento posterior. Filtrar por nome fazia perder CNPJ correto — o
        # nome do POI vem do Maps (fantasia, apelido, o que está na fachada) e
        # raramente parece com a razão social.
        #
        # Escolhe-se o candidato de MAIOR confiança; se todos forem fracos, o
        # dado bruto vai assim mesmo, marcado como fraco.
        rf_ok, melhor_n, melhor_c, melhor_rot = None, -1, "", ""
        for c in cnpjs[:5]:
            # A base nacional está NO BANCO (rf_*, 72 M de estabelecimentos):
            # instantânea, sem limite de taxa e sem depender da BrasilAPI, que
            # fica de reserva para CNPJ aberto depois da última carga mensal.
            rf = await asyncio.to_thread(_receita_local, c)
            if not rf:
                rf = await _receita(session, c)
            n, rot = _confianca_cnpj(c, rf, cidade, uf)
            if n > melhor_n:
                rf_ok, melhor_n, melhor_c, melhor_rot = rf, n, c, rot
            if n >= 4:
                break                      # não dá para ficar melhor que isso
        if melhor_c:
            reg["cnpj_conf"] = f"{melhor_n}/4 {melhor_rot}"
            if not rf_ok:
                # sem ficha na Receita: grava o número cru, marcado
                reg["cnpj"] = re.sub(r"(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})",
                                     r"\1.\2.\3/\4-\5", melhor_c)

        # E-mail/site só se claramente do próprio estabelecimento (evita lixo de
        # páginas alheias no SERP — ex. versões de lib parseadas como e-mail).
        for campo in ("email", "website"):
            v = dados.get(campo)
            if v and not _relacionado(nome, str(v)):
                dados[campo] = None

        # Só é válido com SINAL FORTE: endereço citando a cidade, telefone,
        # instagram ou CNPJ confirmado na Receita. Nome sozinho não conta.
        end_ok = bool(dados.get("endereco")) and cidade.lower() in str(dados.get("endereco", "")).lower()
        sinal_forte = end_ok or dados.get("telefone") or dados.get("instagram") or rf_ok
        encontrado = (bool(dados.get("encontrado"))
                      and float(dados.get("confianca") or 0) >= 0.5
                      and bool(sinal_forte))
        if not (encontrado or rf_ok):
            reg["fontes_web"] = json.dumps(urls[:5], ensure_ascii=False)
            return False

        # 5) aplica no registro (coordenada continua a da planilha → gate de área ok).
        # NÃO sobrescreve o que já veio do MAPS (endereco_fonte='maps' é mais confiável).
        maps_mandou = reg.get("endereco_fonte") == "maps"
        for campo in ("endereco", "telefone", "categoria", "avaliacao",
                      "resumo_avaliacoes", "website", "email"):
            v = dados.get(campo)
            if v not in (None, "", []) and not (campo == "endereco" and maps_mandou):
                reg[campo] = v
        if dados.get("endereco") and not maps_mandou:
            reg["endereco_fonte"] = "web"
        # telefone: fica só o PRIMEIRO número válido (o LLM às vezes concatena vários)
        if reg.get("telefone"):
            m = _RE_UM_TEL.search(str(reg["telefone"]))
            tel = m.group(0).strip() if m else None
            # rejeita placeholder (ex.: 99999-9999, 0000-0000): 8 últimos dígitos iguais
            dig = re.sub(r"\D", "", tel or "")
            if tel and len(dig) >= 8 and len(set(dig[-8:])) <= 1:
                tel = None
            reg["telefone"] = tel
        if dados.get("horario"):
            reg["status_horario"] = str(dados["horario"])[:300]
        ig = dados.get("instagram")
        if ig:
            # aceita só handle válido (URLs de breadcrumb/explore do SERP são lixo)
            m_ig = re.search(r"instagram\.com/([A-Za-z0-9_.]{3,30})\b", str(ig)) or \
                   re.fullmatch(r"@?([A-Za-z0-9_.]{3,30})", str(ig).strip())
            handle = m_ig.group(1) if m_ig else ""
            # rejeita lixo: seções do IG e handles PURAMENTE numéricos (são IDs de
            # location/post, não perfis — ex. instagram.com/292184755)
            if (handle and handle.lower() not in ("explore", "p", "reel", "www", "locations")
                    and not handle.isdigit()):
                reg["instagram"] = f"https://www.instagram.com/{handle}"
        if rf_ok:
            _aplica_receita(reg, rf_ok)
        reg["nome"] = reg.get("nome") or nome
        reg["status"] = "recuperado_web"
        reg["match_valido"] = True
        reg["fonte_dado"] = "web"
        reg["fontes_web"] = json.dumps(urls[:5], ensure_ascii=False)
        reg["ia_resposta"] = json.dumps({"web": dados, "receita": bool(rf_ok)}, ensure_ascii=False)
        return True


# ──────────────────────────────────────────────────────────────────────────
# Orquestração
# ──────────────────────────────────────────────────────────────────────────
async def run(json_path: Path, cidade_arg: str, limit: int, workers: int, area_path: str,
              usar_proxy: bool = True):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    poligono = area_utils.carregar_area(area_path) if area_path else None

    REPROC = {"nao_encontrado", "erro", "encontrado_divergente"}
    alvo = [r for r in data if isinstance(r, dict) and not r.get("match_valido")
            and r.get("_row") is not None and (r.get("status") in REPROC)]
    if limit > 0:
        alvo = alvo[:limit]
    total = len(alvo)
    uf = ""
    for r in data:
        if r.get("uf"):
            uf = r["uf"]
            break
    uf = uf or "PI"
    print(f"🌐 Mineração web do resíduo: {total} POIs | Yahoo (proxy) → pré-filtro local → "
          f"{_LLM_NOME} → Receita Federal (BrasilAPI)", flush=True)
    if not total:
        print("Nada a minerar.")
        return

    lock = threading.Lock()
    feitos = {"n": 0, "rec": 0}
    ini = time.time()
    sem = asyncio.Semaphore(workers)

    def _salvar():
        io_atomico.escrever_json(json_path, data)

    serp = await SerpPool(min(workers, 6), usar_proxy=usar_proxy).start()
    conn = aiohttp.TCPConnector(limit=workers * 4, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        async def _um(reg):
            cidade = _cidade_do(reg, cidade_arg)
            try:
                ok = await _processar_poi(session, serp, reg, cidade, uf, sem)
            except Exception:
                ok = False
            if ok and poligono:
                area_utils.gate_registro(reg, poligono)
                ok = bool(reg.get("match_valido"))
            with lock:
                feitos["n"] += 1
                feitos["rec"] += 1 if ok else 0
                if feitos["n"] % 3 == 0 or feitos["n"] == total:
                    _salvar()
                print(f"🌐 POIs {feitos['n']}/{total} | recuperados {feitos['rec']} | "
                      f"tokens {_USO['in']+_USO['out']} (US${_custo():.3f}) | "
                      f"{(time.time()-ini)/60:.1f}min", flush=True)

        try:
            await asyncio.gather(*[_um(r) for r in alvo])
        finally:
            await serp.close()

    _salvar()
    print(f"\n{'═'*52}")
    print(f"🌐 Mineração web | Resumo")
    print(f"{'═'*52}")
    print(f"   Recuperados          : {feitos['rec']}/{total}")
    print(f"   Tokens ({_LLM_NOME}) : {_USO['in']} in + {_USO['out']} out")
    print(f"   Custo LLM            : US$ {_custo():.4f}")
    print(f"   Tempo                : {(time.time()-ini)/60:.1f} min")
    print(f"   💾 {json_path}")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json", required=True, help="JSON da planilha (uploads/..._db.json)")
    p.add_argument("--cidade", default="", help="Cidade (default: extraída do endereço da planilha)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--area", default="", help="Polígono da área (gate)")
    p.add_argument("--no-proxy", action="store_true", help="SERP sem proxy (teste)")
    a = p.parse_args()
    jp = Path(a.json)
    if not jp.exists():
        print(f"❌ JSON não encontrado: {jp}")
        return
    asyncio.run(run(jp, a.cidade, a.limit, a.workers, a.area, usar_proxy=not a.no_proxy))


if __name__ == "__main__":
    main()
