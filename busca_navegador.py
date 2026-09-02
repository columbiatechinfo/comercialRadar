# -*- coding: utf-8 -*-
"""busca_navegador.py — buscar e baixar com o navegador do repositório.

POR QUE O `urllib` SAIU

O SearXNG funcionava: `Habibs Canoas RS CNPJ` devolvia 20 resultados no começo
de 02/09/2026. Depois de algumas dezenas de consultas de teste ele passou a
devolver **0 resultados em 0,0 s para toda pergunta** — não é lentidão, é
resposta vazia instantânea, que é como um motor upstream diz que cortou.

E não é só a busca. Medido no mesmo dia, com `urllib`: `cnpj.biz` responde 422,
`econodata` 403, `cnpja` 429. Três formas de dizer "não por HTTP simples".

O repositório já tem a resposta para isso e usa nas outras etapas: Camoufox com
o pool de proxies, que é o que passa no Cloudflare do iFood e do Airbnb.

O QUE ESTE MÓDULO DÁ

    buscar(consultas)   os resultados, do DuckDuckGo, um IP por sessão
    baixar(url)         o texto de uma página que recusa HTTP simples

UM IP POR SESSÃO, e isso não é zelo. Processo novo começa com `_in_use` vazio, e
pedir "o menos usado" devolve sempre o MESMO IP — foi o que fez o Google parar
de responder numa sondagem inteira. Os proxies são tomados de uma vez, num
`asyncio.run` só, e distribuídos em rodízio.

DUCKDUCKGO, E NÃO GOOGLE. Medido em 24/07/2026 e registrado: IPs da Webshare
penduravam no `google.com` só pelo navegador, e o sintoma virava "IP queimado".
O DuckDuckGo tem versão HTML sem JavaScript, não pede CAPTCHA de saída e
responde ao mesmo tipo de sessão. Quando ele não achar, o Bing entra atrás.

O CUSTO É REAL, e por isso a etapa que chama isto pede pouco: uma sessão de
navegador leva segundos, contra milissegundos do `urllib`. Vale para o que o
`urllib` não alcança — que é justamente o que interessa.
"""
from __future__ import annotations

import re
import sys
import time
import urllib.parse
from collections import deque

# `lite` antes de `html`: MEDIDO em 02/09/2026, o `html` devolveu 202
# (desafio) atras de proxy e estourou os 120 s esperando o seletor. O
# `lite` e uma pagina de tabela, sem JS e sem desafio.
DDG_LITE = "https://lite.duckduckgo.com/lite/?q=%s"
DDG = "https://html.duckduckgo.com/html/?q=%s"
BING = "https://www.bing.com/search?q=%s&setlang=pt-BR"

# 35 s, e nao 120. Tres motores em serie com 120 s cada faziam uma
# consulta sem resposta custar 126 s — e a maioria das que nao acham
# nada nao acha nos primeiros segundos. Falha tem de ser barata.
TIMEOUT_BUSCA = 35000
# A reserva espera pouco: ela so existe para o dia em que o
# principal cair, e cada segundo dela e pago em toda consulta.
TIMEOUT_RESERVA = 12000
TIMEOUT_PAGINA = 60000

# O DuckDuckGo HTML não usa JavaScript: os resultados já vêm no corpo. O
# seletor é o âncora do resultado, e esperar por ele é o mesmo que esperar a
# página ter conteúdo — `network_idle` não serve, a página mantém tráfego.
ESPERA_DDG = "a.result__a"
ESPERA_LITE = "a.result-link"
ESPERA_BING = "li.b_algo h2 a"

# Extrai do DOM, e não do HTML cru: o DuckDuckGo embrulha o destino real num
# redirecionador (`/l/?uddg=...`), e quem lê o `href` cru guarda o
# redirecionador em vez do site.
JS_LITE = r"""() => {
  const saida = [];
  for (const a of document.querySelectorAll('a.result-link')) {
    const tr = a.closest('tr');
    const prox = tr ? tr.nextElementSibling : null;
    const s = prox ? prox.querySelector('.result-snippet') : null;
    saida.push({titulo: (a.textContent || '').trim(),
                url: a.getAttribute('href') || '',
                resumo: s ? (s.textContent || '').trim() : ''});
  }
  return saida;
}"""

JS_DDG = r"""() => {
  const saida = [];
  for (const a of document.querySelectorAll('a.result__a')) {
    let u = a.getAttribute('href') || '';
    try {
      const p = new URL(u, location.href);
      const real = p.searchParams.get('uddg');
      if (real) u = real;
    } catch (e) {}
    const bloco = a.closest('.result') || a.parentElement;
    const s = bloco ? bloco.querySelector('.result__snippet') : null;
    saida.push({titulo: (a.textContent || '').trim(),
                url: u,
                resumo: s ? (s.textContent || '').trim() : ''});
  }
  return saida;
}"""

# O BING EMBRULHA O DESTINO num redirecionador — `bing.com/ck/a?...&u=a1<base64>`.
# Guardar o `href` cru guardava o redirecionador, e a etapa seguinte baixava o
# proprio Bing em vez do site. O destino real esta no parametro `u`, em base64
# com o prefixo `a1`, e o `cite` mostra o dominio como confirmacao.
JS_BING = r"""() => {
  const real = (u) => {
    try {
      const p = new URL(u, location.href);
      let e = p.searchParams.get('u') || '';
      if (e.startsWith('a1')) e = e.slice(2);
      if (!e) return u;
      e = e.replace(/-/g, '+').replace(/_/g, '/');
      while (e.length % 4) e += '=';
      const d = atob(e);
      return d.startsWith('http') ? d : u;
    } catch (err) { return u; }
  };
  const saida = [];
  for (const h of document.querySelectorAll('li.b_algo')) {
    const a = h.querySelector('h2 a');
    if (!a) continue;
    const p = h.querySelector('.b_caption p, .b_snippet');
    const c = h.querySelector('cite');
    saida.push({titulo: (a.textContent || '').trim(),
                url: real(a.getAttribute('href') || ''),
                dominio: c ? (c.textContent || '').trim() : '',
                resumo: p ? (p.textContent || '').trim() : ''});
  }
  return saida;
}"""

# O texto de uma página qualquer, sem marcação. `innerText` depende de layout e
# volta vazio para elemento fora de vista — foi o que escondeu as avaliações do
# Maps. `textContent` não depende.
JS_TEXTO = r"""() => {
  for (const e of document.querySelectorAll('script,style,noscript,svg,nav,footer'))
    e.remove();
  return (document.body ? document.body.textContent || '' : '')
         .replace(/\s+/g, ' ').trim().slice(0, 120000);
}"""


def _log(m: str) -> None:
    print(m, flush=True)


def rodizio(sem_proxy: bool = False, quantos: int = 12):
    """Uma função que devolve o PRÓXIMO proxy a cada chamada.

    Os IPs são tomados de uma vez, num `asyncio.run` só: o `acquire` guarda os
    em uso num lock que não atravessa event loop, então dois `asyncio.run`
    separados devolvem o mesmo IP as duas vezes.
    """
    if sem_proxy:
        return lambda: None
    try:
        import asyncio

        from proxy_pool import ProxyPool
    except Exception as e:                                     # noqa: BLE001
        _log("   ⚠️  pool indisponível (%s) — IP direto" % type(e).__name__)
        return lambda: None

    async def _pegar(pool, n):
        return [await pool.acquire() for _ in range(n)]

    try:
        pool = ProxyPool(pais="BR")
        pool.start()
        escolhidos = asyncio.run(_pegar(pool, quantos))
    except Exception as e:                                     # noqa: BLE001
        _log("   ⚠️  pool falhou (%s) — IP direto" % type(e).__name__)
        return lambda: None

    urls = []
    for px in escolhidos:
        if not px:
            continue
        cfg = ProxyPool.to_playwright(px)
        servidor = str(cfg.get("server") or "").replace("http://", "")
        if not servidor:
            continue
        if cfg.get("username"):
            urls.append("http://%s:%s@%s"
                        % (cfg["username"], cfg.get("password") or "", servidor))
        else:
            urls.append("http://%s" % servidor)
    if not urls:
        _log("   ⚠️  nenhum proxy no pool — IP direto")
        return lambda: None
    _log("   %d IPs no rodízio" % len(urls))
    fila = deque(urls)

    def proximo():
        fila.rotate(-1)
        return fila[0]
    return proximo


def _sessao(proxy, espera, solve=False):
    from scrapling.fetchers import StealthySession
    return StealthySession(headless=True, solve_cloudflare=solve,
                           wait_selector=espera, wait_selector_state="attached",
                           proxy=proxy, locale="pt-BR",
                           timezone_id="America/Sao_Paulo")


def buscar(consulta: str, proxy=None, teto: int = 8) -> list:
    """Os resultados do DuckDuckGo; o Bing entra quando ele não achar."""
    # O BING NA FRENTE, e isso e o MEDIDO de 02/09/2026 sobre cinco POIs reais.
    #
    # Em toda consulta da rodada:
    #     ddg-lite   35 s esperando `a.result-link`, que nunca apareceu
    #     ddg-html   202 — desafio — e mais espera
    #     bing       200 em 2 s, sempre
    #
    # Eram ~37 s jogados fora por busca, e o unico que responde ficava por
    # ultimo. Os dois do DuckDuckGo continuam atras porque um dia voltam, e
    # porque o Bing tambem cansa — mas com timeout curto: o que nao respondeu em
    # 12 s ja mostrou o que tinha a mostrar.
    for url, espera, js, quem, teto_espera in (
            (BING, ESPERA_BING, JS_BING, "bing", TIMEOUT_BUSCA),
            (DDG_LITE, ESPERA_LITE, JS_LITE, "ddg-lite", TIMEOUT_RESERVA),
            (DDG, ESPERA_DDG, JS_DDG, "ddg", TIMEOUT_RESERVA)):
        caixa = {}

        def acao(page, _js=js, _caixa=caixa):
            page.wait_for_timeout(900)
            _caixa["r"] = page.evaluate(_js) or []

        try:
            with _sessao(proxy, espera) as s:
                s.fetch(url % urllib.parse.quote(consulta),
                        page_action=acao, timeout=teto_espera)
        except Exception as e:                                 # noqa: BLE001
            _log("      %s falhou (%s)" % (quem, type(e).__name__))
            continue
        saida = []
        for r in (caixa.get("r") or []):
            u = (r.get("url") or "").strip()
            if not u.startswith("http"):
                continue
            # Recusa o que sobrou como redirecionador: baixar o proprio
            # buscador em vez do site foi o defeito da primeira versao.
            if "bing.com/ck/" in u or "duckduckgo.com/l/" in u:
                continue
            saida.append({"titulo": (r.get("titulo") or "")[:200], "url": u,
                          "dominio": (r.get("dominio") or "")[:120],
                          "resumo": (r.get("resumo") or "")[:400]})
            if len(saida) >= teto:
                break
        if saida:
            return saida
    return []


# Instagram e Facebook recusam sessao sem login e devolvem
# ERR_HTTP_RESPONSE_CODE_FAILURE — tres tentativas cada, todas perdidas, na
# rodada de 02/09/2026. O resumo que a busca ja trouxe diz o mesmo e custa zero.
SEM_BAIXAR = ("instagram.com", "facebook.com", "linkedin.com", "threads.net")


def baixar(url: str, proxy=None, solve: bool = True) -> str:
    """O texto de uma página que recusa HTTP simples.

    `solve_cloudflare` fica ligado aqui e desligado na busca: o DuckDuckGo não
    tem desafio, e esperar por um que não existe custa segundos por consulta.
    """
    if any(d in (url or "").lower() for d in SEM_BAIXAR):
        return ""
    caixa = {}

    def acao(page):
        page.wait_for_timeout(700)
        caixa["t"] = page.evaluate(JS_TEXTO) or ""

    try:
        with _sessao(proxy, "body", solve=solve) as s:
            s.fetch(url, page_action=acao, timeout=TIMEOUT_PAGINA)
    except Exception:                                          # noqa: BLE001
        return ""
    return caixa.get("t") or ""


if __name__ == "__main__":
    proximo = rodizio(sem_proxy="--sem-proxy" in sys.argv)
    for q in ('"MECANICA DIESEL CRIATIVA" Canoas RS',
              'SALA DE COSTURA Canoas RS CNPJ telefone'):
        t0 = time.time()
        rs = buscar(q, proximo())
        print("\n== %s ==" % q)
        print("   %d resultados · %.1f s" % (len(rs), time.time() - t0))
        for r in rs[:6]:
            print("      %-56s %s" % (r["titulo"][:56], r["url"][:64]))
