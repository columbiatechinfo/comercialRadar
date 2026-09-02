# -*- coding: utf-8 -*-
"""iFood, sonda 16 — "Ver mais" pela CLASSE, ate a lista parar de crescer.

O usuario mandou o elemento:

    <button class="... cardstack-nextcontent__button" aria-label="Ver mais">

E ele explica por que eu nao achava: eu procurava por `innerText`, que DEPENDE
DE LAYOUT e volta vazio para elemento fora de vista. E a mesma armadilha que me
custou cinco correcoes nas avaliacoes do Maps hoje — `textContent` nao depende
de layout, e a classe muito menos.

Colher aqui e: clicar em `.cardstack-nextcontent__button` ate ele sumir,
contando o crescimento a cada volta.
"""
import re
import time

ENDERECO = "Avenida Victor Barreto, 2500, Canoas"
BOTAO = ".cardstack-nextcontent__button, [aria-label='Ver mais']"
LINK = re.compile(r"/delivery/([^/]+)/[^/]+/"
                  r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
colhido = {}


def clicar(page, nome, espera=9000):
    try:
        b = page.get_by_role("button", name=nome)
        if b.count():
            b.first.click(timeout=10000)
            page.wait_for_timeout(espera)
            return True
    except Exception:
        pass
    return False


def informar_endereco(page):
    campo = page.locator(".landing-v2-address-search__input").first
    campo.click(timeout=20000)
    campo.type(ENDERECO, delay=110)
    page.wait_for_timeout(5000)
    op = page.locator("[class*='address'] li")
    if op.count():
        op.first.click(timeout=10000)
        page.wait_for_timeout(9000)
    clicar(page, "Confirmar localização")
    clicar(page, "Salvar endereço")
    page.wait_for_timeout(5000)


def colher(page):
    page.wait_for_selector("a.merchant-v2__link", timeout=60000)

    def n():
        return page.eval_on_selector_all("a.merchant-v2__link", "e => e.length")

    print("  ao abrir: %d lojas" % n())
    t0 = time.time()
    for volta in range(1, 121):
        # Existe? Pela CLASSE, nao por texto renderizado.
        quantos = page.eval_on_selector_all(BOTAO, "e => e.length")
        if not quantos:
            print("  volta %2d: o botao nao esta mais no DOM — fim (%.0fs)"
                  % (volta, time.time() - t0))
            break
        antes = n()
        ok = page.evaluate("""(sel) => {
          const b = [...document.querySelectorAll(sel)].pop();
          if (!b) return false;
          b.scrollIntoView({block: 'center'});
          b.click();
          return true;
        }""", BOTAO)
        if not ok:
            print("  volta %2d: sumiu entre olhar e clicar" % volta)
            break
        page.wait_for_timeout(2600)
        agora = n()
        if volta <= 6 or volta % 10 == 0 or agora == antes:
            print("  volta %3d: %5d -> %5d (+%d)" % (volta, antes, agora, agora - antes))
        if agora == antes:
            print("           nao cresceu; parando")
            break

    hrefs = page.eval_on_selector_all(
        "a.merchant-v2__link",
        "e => e.map(x => x.getAttribute('href') + '|' + (x.textContent||'').trim().slice(0,44))")
    for item in hrefs:
        href, _, nome = item.partition("|")
        m = LINK.search(href or "")
        if m:
            colhido[m.group(2)] = (m.group(1), nome)


from scrapling.fetchers import StealthySession  # noqa: E402

t0 = time.time()
with StealthySession(headless=True, solve_cloudflare=True, network_idle=True) as s:
    s.fetch("https://www.ifood.com.br/", page_action=informar_endereco, timeout=220000)
    print("\n== /inicio, 'Ver mais' pela classe ==")
    s.fetch("https://www.ifood.com.br/inicio", page_action=colher, timeout=1500000)

print("\n" + "=" * 72)
print("  %d ids distintos · %.1f min" % (len(colhido), (time.time() - t0) / 60))
print("=" * 72)
cid = {}
for uid, (c, nome) in colhido.items():
    cid[c] = cid.get(c, 0) + 1
print("  por cidade: %s" % dict(sorted(cid.items(), key=lambda x: -x[1])[:8]))
