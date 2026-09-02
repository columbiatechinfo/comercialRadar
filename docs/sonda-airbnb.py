# -*- coding: utf-8 -*-
"""Airbnb, sonda 11 — os quatro restos, e as avaliacoes pela rota delas.

A sonda 10 acertou o que a 9 errava: os numeros passaram a sair do payload
(`sharingConfig.ugcTitle` = "Apartamento · Canoas · ★4,87 · 1 quarto · 1 cama ·
1 banheiro") e nao mais do `body.textContent`, que concatenava "9"+"347" em
"9347". Quartos, camas, banheiros e quantidade de avaliacoes ficaram certos.

Sobraram quatro, e tres sao erro meu de expressao regular ou de alvo:

  hospedes      o `ugcTitle` nao traz; a frase com "hóspedes" e outra, e eu
                pegava "a mais curta com qualquer um dos termos" — que era a de
                quartos. Procura-se a frase que fala de HOSPEDE, so ela.
  camas         `cama\\b` nao casa "3 camas". Faltou o plural.
  preco         as folhas mostram "R$ 505", "R$ 531"... que sao o CALENDARIO,
                nao o total. Procura-se o rotulo "Total" e o valor ao lado —
                preco sem rotulo e numero solto, e numero solto engana.
  avaliacoes    o clique erra o alvo em fichas diferentes. Existe rota propria:
                /rooms/<id>/reviews. Pedir a pagina certa e mais firme que
                cacar o botao que a abre.
"""
import datetime as dt
import json
import re
import sys
import time

sys.path.insert(0, "/app")

BUSCA = "https://www.airbnb.com.br/s/Canoas--RS/homes"
QUANTOS = 3
_hoje = dt.date.today()
_sexta = _hoje + dt.timedelta(days=(4 - _hoje.weekday()) % 7 + 7)
CHECKIN, CHECKOUT = _sexta.isoformat(), (_sexta + dt.timedelta(days=2)).isoformat()

FRASES = r"""() => {
  const f = new Set();
  const visitar = (no, prof) => {
    if (prof > 18 || no == null || f.size > 600) return;
    if (typeof no === 'string') {
      const s = no.trim();
      if (s.length > 3 && s.length < 90) f.add(s);
      return;
    }
    if (Array.isArray(no)) { for (const x of no.slice(0, 40)) visitar(x, prof + 1); return; }
    if (typeof no !== 'object') return;
    for (const k of Object.keys(no)) visitar(no[k], prof + 1);
  };
  for (const s of document.querySelectorAll('script[id^="data-deferred-state"]')) {
    let d; try { d = JSON.parse(s.textContent || ''); } catch (e) { continue; }
    visitar(d, 0);
  }
  return [...f];
}"""

# Rotulo E valor, lado a lado. Percorre folhas para nao concatenar elementos.
PRECO = r"""() => {
  const folhas = [];
  const anda = (e) => {
    if (!e) return;
    if (e.children.length === 0) {
      const t = (e.textContent || '').replace(/ /g, ' ').trim();
      if (t && t.length < 60) folhas.push(t);
      return;
    }
    for (const f of e.children) anda(f);
  };
  anda(document.body);
  let total = null, noite = null;
  for (let i = 0; i < folhas.length; i++) {
    const t = folhas[i];
    if (/^Total/i.test(t)) {
      for (let j = i; j < Math.min(i + 4, folhas.length); j++) {
        const m = folhas[j].match(/R\$\s?([\d.]+)/);
        if (m) { total = 'R$ ' + m[1]; break; }
      }
    }
    if (!noite && /por noite|noite$/i.test(t)) {
      for (let j = Math.max(0, i - 3); j <= i; j++) {
        const m = folhas[j].match(/R\$\s?([\d.]+)/);
        if (m) { noite = 'R$ ' + m[1]; break; }
      }
    }
  }
  return {preco_total: total, preco_noite: noite};
}"""

# As avaliacoes, do DOM da pagina /reviews: autor, data e texto vizinhos.
REVIEWS_DOM = r"""() => {
  const blocos = [];
  for (const e of document.querySelectorAll('[data-review-id], [id^="review-"]')) {
    const folhas = [];
    const anda = (n) => {
      if (!n) return;
      if (n.children.length === 0) {
        const t = (n.textContent || '').trim();
        if (t) folhas.push(t);
        return;
      }
      for (const f of n.children) anda(f);
    };
    anda(e);
    if (folhas.length) blocos.push(folhas.slice(0, 12));
  }
  return blocos.slice(0, 40);
}"""


def avaliacoes_de(corpos):
    achadas, vistos = [], set()

    def varrer(no, prof=0):
        if prof > 14 or no is None or len(achadas) > 120:
            return
        if isinstance(no, list):
            for x in no[:120]:
                varrer(x, prof + 1)
            return
        if not isinstance(no, dict):
            return
        if isinstance(no.get("comments"), str) and no["comments"].strip():
            rev = no.get("reviewer") or {}
            chave = no["comments"][:70]
            if chave not in vistos:
                vistos.add(chave)
                achadas.append({"texto": no["comments"],
                                "autor": rev.get("firstName") or rev.get("smartName"),
                                "data": no.get("localizedDate"),
                                "nota": no.get("rating")})
            return
        for v in no.values():
            varrer(v, prof + 1)

    for c in corpos:
        varrer(c)
    return achadas


def campos_numericos(frases, resumo):
    def acha(re_):
        cand = [f for f in frases if re.search(re_, f, re.I)]
        return sorted(cand, key=len)[0] if cand else None

    fr_hosp = acha(r"\d+\s*hóspede")
    alvo = resumo or ""
    def n(texto, re_):
        m = re.search(re_, texto or "", re.I)
        return int(m.group(1)) if m else None
    return {
        "hospedes": n(fr_hosp, r"(\d+)\s*hóspede"),
        "quartos": n(alvo, r"(\d+)\s*quarto"),
        "camas": n(alvo, r"(\d+)\s*camas?\b"),
        "banheiros": n(alvo, r"(\d+)\s*banheiro"),
        "frase_hospedes": fr_hosp,
    }


def extrair(sessao, quarto, DA_FICHA):
    corpos = []
    url = ("https://www.airbnb.com.br/rooms/%s?check_in=%s&check_out=%s&adults=1"
           % (quarto, CHECKIN, CHECKOUT))
    saida = {"id": quarto, "url": url}

    def ouvir(resp):
        try:
            if ("/api/" in resp.url or "graphql" in resp.url.lower()) and resp.status == 200:
                corpos.append(resp.json())
        except Exception:
            pass

    def acao(page):
        page.on("response", ouvir)
        page.wait_for_timeout(8000)
        saida.update(page.evaluate(DA_FICHA) or {})
        frases = page.evaluate(FRASES) or []
        saida.update(campos_numericos(frases, saida.get("resumo_curto")))
        fr = [f for f in frases if re.search(r"\d+\s*avaliaç", f, re.I)]
        m = re.search(r"(\d+)\s*avaliaç", sorted(fr, key=len)[0]) if fr else None
        saida["avaliacoes_qtd"] = int(m.group(1)) if m else None
        saida.update(page.evaluate(PRECO) or {})

    sessao.fetch(url, page_action=acao, timeout=300000)

    # A rota propria das avaliacoes. Mais firme que cacar o botao que a abre.
    def acao_rev(page):
        page.on("response", ouvir)
        page.wait_for_timeout(9000)
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(1800)
        saida["avaliacoes_dom"] = page.evaluate(REVIEWS_DOM)

    try:
        sessao.fetch("https://www.airbnb.com.br/rooms/%s/reviews" % quarto,
                     page_action=acao_rev, timeout=300000)
    except Exception as e:
        saida["erro_reviews"] = "%s" % type(e).__name__
    saida["avaliacoes"] = avaliacoes_de(corpos)
    return saida


# reaproveita o extrator de payload da sonda 10
DA_FICHA = open("/sondas/da_ficha.js", encoding="utf-8").read()

from proxy_pool import ProxyPool  # noqa: E402
import asyncio  # noqa: E402

pool = ProxyPool(pais="BR")
pool.start()


async def pegar(n):
    return [await pool.acquire() for _ in range(n)]


def url_de(px):
    cfg = ProxyPool.to_playwright(px)
    return "http://%s:%s@%s" % (cfg.get("username", ""), cfg.get("password", ""),
                                cfg["server"].replace("http://", ""))


proxies = [url_de(p) for p in asyncio.run(pegar(QUANTOS + 1)) if p]

from scrapling.fetchers import StealthySession  # noqa: E402

t0 = time.time()
ids = []
with StealthySession(headless=True, solve_cloudflare=True,
                     wait_selector='a[href*="/rooms/"]',
                     wait_selector_state="attached", proxy=proxies[0],
                     locale="pt-BR", timezone_id="America/Sao_Paulo") as s:
    def pega(page):
        page.wait_for_timeout(5000)
        ids.extend(page.evaluate("""() => {
          const s = new Set();
          for (const a of document.querySelectorAll('a[href*="/rooms/"]')) {
            const m = (a.getAttribute('href') || '').match(/\\/rooms\\/(\\d+)/);
            if (m) s.add(m[1]);
          }
          return [...s];
        }"""))
    s.fetch(BUSCA, page_action=pega, timeout=300000)
print("  %d anuncios na busca\n" % len(ids))

fichas = []
for i, quarto in enumerate(ids[:QUANTOS]):
    with StealthySession(headless=True, solve_cloudflare=True,
                         wait_selector="h1", wait_selector_state="attached",
                         proxy=proxies[(i + 1) % len(proxies)], locale="pt-BR",
                         timezone_id="America/Sao_Paulo") as s:
        try:
            f = extrair(s, quarto, DA_FICHA)
        except Exception as e:
            print("  %s -> %s: %s" % (quarto, type(e).__name__, str(e)[:80]))
            continue
    fichas.append(f)
    print("  %s" % f.get("titulo"))
    print("     %s hóspedes · %s quartos · %s camas · %s banheiros   (%r)"
          % (f.get("hospedes"), f.get("quartos"), f.get("camas"),
             f.get("banheiros"), f.get("frase_hospedes")))
    print("     total %s · noite %s" % (f.get("preco_total"), f.get("preco_noite")))
    print("     %s avaliações · colhidas por API %d · blocos no DOM %d"
          % (f.get("avaliacoes_qtd"), len(f.get("avaliacoes") or []),
             len(f.get("avaliacoes_dom") or [])))
    for a in (f.get("avaliacoes") or [])[:2]:
        print("       %s (%s): %s" % (a["autor"], a["data"], str(a["texto"])[:50]))
    for b in (f.get("avaliacoes_dom") or [])[:2]:
        print("       DOM: %s" % " | ".join(b)[:100])
    print()

print("=" * 72)
print("  %d fichas · %.1f min" % (len(fichas), (time.time() - t0) / 60))
for c in ("titulo", "lat", "hospedes", "camas", "preco_total", "avaliacoes_qtd",
          "avaliacoes", "avaliacoes_dom", "comodidades", "fotos"):
    print("  %-16s %d/%d" % (c, sum(1 for f in fichas if f.get(c)), len(fichas)))

with open("/sondas/airbnb_fichas.json", "w", encoding="utf-8") as f:
    json.dump(fichas, f, ensure_ascii=False, indent=1)
