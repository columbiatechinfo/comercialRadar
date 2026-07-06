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


def _cnpj_limpo(c: str) -> str:
    d = re.sub(r"\D", "", c)
    return d if len(d) == 14 else ""


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

    def __init__(self, n: int, usar_proxy: bool = True):
        self.n = n
        self.usar_proxy = usar_proxy
        self.fila: asyncio.Queue = asyncio.Queue()
        self._pw = None
        self._pool = None
        self._seq = 0

    async def _nova_sessao(self) -> dict:
        from human_browser import HumanSession
        proxy = await self._pool.acquire_blocking() if self._pool else None
        self._seq += 1
        profile = config.BROWSER_PROFILES_DIR / f"serpweb_{self._seq}"
        sess = await HumanSession.create(self._pw, proxy, profile, layer="maps",
                                         headless=True)
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
                res = await _serp_yahoo(slot["sess"].page, query)
                if res is None:
                    # challenge: cooldown do IP e tenta 1x com sessão nova
                    print("  ♻️  Yahoo bloqueou — trocando proxy...", flush=True)
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
_LLM_BACKEND = "openai" if os.environ.get("OPENAI_API_KEY", "").strip() else "gemini-flash"
_LLM_NOME = OPENAI_MODEL if _LLM_BACKEND == "openai" else "gemini-2.5-flash (sem thinking)"
_PRECO = (0.15, 0.60) if _LLM_BACKEND == "openai" else (0.30, 2.50)  # US$/M tokens


def _custo() -> float:
    return _USO["in"] / 1e6 * _PRECO[0] + _USO["out"] / 1e6 * _PRECO[1]


def _openai():
    global _OPENAI
    if _OPENAI is None:
        from openai import OpenAI
        _OPENAI = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())
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
    resp = _openai().chat.completions.create(
        model=OPENAI_MODEL, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
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
    if cidade_arg:
        return cidade_arg
    end = reg.get("endereco_planilha") or ""
    m = re.search(r"([A-Za-zÀ-ú ]{3,})\s*-\s*[A-Z]{2}", end)
    return (m.group(1).strip() if m else "") or "Parnaíba"


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
        rf_ok = None
        cid_n = _sem_acento(cidade)
        for c in cnpjs[:5]:
            rf = await _receita(session, c)
            if not rf:
                continue
            # Filtro por UF + município com IGUALDADE normalizada (sem acento).
            # Antes: "parnaíba" (acento) nunca casava com "PARNAIBA" da Receita →
            # todo CNPJ rejeitado; e substring casava "Santana de Parnaíba"/SP.
            rf_uf = (rf.get("uf") or "").upper()
            rf_mun = _sem_acento(rf.get("municipio") or "")
            if uf and rf_uf and rf_uf != uf.upper():
                continue  # outra UF (ex.: Santana de Parnaíba-SP)
            if cid_n and rf_mun and rf_mun != cid_n:
                continue  # outro município
            rf_ok = rf
            break

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
        tmp = json_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(json_path)

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
