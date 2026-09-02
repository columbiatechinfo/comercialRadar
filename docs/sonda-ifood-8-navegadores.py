# -*- coding: utf-8 -*-
"""iFood, sonda 18 — 8 navegadores, um por bairro, so a aba /inicio.

O QUE JA ESTA MEDIDO E VALE COMO BASE:

  Camoufox atravessa o PerimeterX; TLS puro e Playwright comum dao 403
  sessao quente derruba a pagina de 13-38 s para 2-3 s
  o endereco entra em dois botoes: "Confirmar localizacao" e "Salvar endereco"
  o id da loja esta no HREF do cartao — nao se abre loja nenhuma
  `/inicio` traz TODAS as categorias juntas
  a lista vem COMPLETA: 490 de uma vez, sem precisar clicar "Ver mais".
    Num navegador de gente aparecem ~20 e o botao; aqui o `network_idle`
    espera o carregamento inteiro e a paginacao ja vem resolvida.

O QUE ESTA SONDA ACRESCENTA:

  cada bairro e um endereco diferente, e cada endereco ve um RAIO diferente —
  por isso 8 navegadores em paralelo, um por bairro, e a uniao cobre a cidade.

  E tolerancia a falha: em Porto Alegre o endereco nao resolveu e a corrida
  inteira morreu. Aqui um bairro que falha vira uma linha no relatorio, nao o
  fim do trabalho.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

BAIRROS = [
    "Centro, Canoas",
    "Mathias Velho, Canoas",
    "Guajuviras, Canoas",
    "Igara, Canoas",
    "Niterói, Canoas",
    "Marechal Rondon, Canoas",
    "São José, Canoas",
    "Harmonia, Canoas",
]
LINK = re.compile(r"/delivery/([^/]+)/[^/]+/"
                  r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
trava = threading.Lock()
TUDO = {}
PLACAR = []


def clicar(page, nome, espera=8000):
    try:
        b = page.get_by_role("button", name=nome)
        if b.count():
            b.first.click(timeout=9000)
            page.wait_for_timeout(espera)
            return True
    except Exception:
        pass
    return False


def fazer_endereco(endereco):
    def acao(page):
        campo = page.locator(".landing-v2-address-search__input").first
        campo.click(timeout=25000)
        campo.type(endereco, delay=100)
        page.wait_for_timeout(5000)
        op = page.locator("[class*='address'] li")
        if not op.count():
            raise RuntimeError("nenhuma sugestao para %r" % endereco)
        op.first.click(timeout=12000)
        page.wait_for_timeout(9000)
        clicar(page, "Confirmar localização")
        clicar(page, "Salvar endereço")
        for alt in ("Salvar", "Continuar", "Confirmar"):
            if clicar(page, alt, 6000):
                break
        page.wait_for_timeout(5000)
    return acao


def fazer_colheita(destino):
    def acao(page):
        page.wait_for_selector("a.merchant-v2__link", timeout=70000)
        # A lista ja vem completa; ainda assim rola-se ate o fim e confere-se
        # que parou de crescer — barato, e cobre o caso de vir paginada.
        anterior, parado = -1, 0
        for _ in range(20):
            n = page.eval_on_selector_all("a.merchant-v2__link", "e => e.length")
            if n == anterior:
                parado += 1
                if parado >= 2:
                    break
            else:
                parado = 0
            anterior = n
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1400)
        for item in page.eval_on_selector_all(
                "a.merchant-v2__link",
                "e => e.map(x => x.getAttribute('href') + '|' "
                "+ (x.textContent||'').trim().slice(0,44))"):
            href, _, nome = item.partition("|")
            m = LINK.search(href or "")
            if m:
                destino[m.group(2)] = (m.group(1), nome)
    return acao


def um_bairro(bairro):
    from scrapling.fetchers import StealthySession
    t0 = time.time()
    achado = {}
    try:
        with StealthySession(headless=True, solve_cloudflare=True,
                             network_idle=True) as s:
            s.fetch("https://www.ifood.com.br/",
                    page_action=fazer_endereco(bairro), timeout=240000)
            s.fetch("https://www.ifood.com.br/inicio",
                    page_action=fazer_colheita(achado), timeout=300000)
        estado = "ok"
    except Exception as e:
        estado = str(e)[:70]
    with trava:
        novos = [k for k in achado if k not in TUDO]
        TUDO.update(achado)
        PLACAR.append((bairro, len(achado), len(novos), time.time() - t0, estado))
        print("  %-26s %4d lojas · %4d ineditas · %4.0fs · %s"
              % (bairro, len(achado), len(novos), time.time() - t0, estado))
    return achado


print("== 8 bairros de Canoas, um navegador cada, em paralelo ==")
t0 = time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    list(ex.map(um_bairro, BAIRROS))

print("\n" + "=" * 74)
print("  %d ids DISTINTOS em %.1f min" % (len(TUDO), (time.time() - t0) / 60))
print("=" * 74)
soma = sum(p[1] for p in PLACAR)
print("  soma dos bairros: %d · distintos: %d · repeticao: %.0f%%"
      % (soma, len(TUDO), 100.0 * (1 - len(TUDO) / max(1, soma))))
cid = {}
for uid, (c, nome) in TUDO.items():
    cid[c] = cid.get(c, 0) + 1
print("  por cidade no href: %s" % dict(sorted(cid.items(), key=lambda x: -x[1])[:8]))
