# -*- coding: utf-8 -*-
"""iFood, sonda 30 — um IP por navegador, e a coordenada do medoide.

O QUE JA ESTA MEDIDO, e nao se mexe mais:

  posicionar por coordenada       8 de 8 celulas, cada uma no bairro certo
                                  (Igara, Guajuviras, Nossa Sra. das Gracas,
                                  Mathias Velho, Estancia Velha, Centro)
  o alvo do clique                div.btn-address__container — nao e <button>,
                                  e so cede a clique de MOUSE por coordenada
  por proxy, um ciclo             17 s (104.165.145.72:6205, BR)
  o teto sem proxy                470 a 713 ids, sempre; e so uma ou tres
                                  sessoes conseguem a lista, as demais
                                  posicionam certo e esperam cartao que nao vem

Esta sonda muda UMA coisa: cada navegador sai por um IP diferente dos 500 da
Webshare. Se o teto era do IP compartilhado, ele cede; se nao ceder, o gargalo
esta em outro lugar e o desenho muda de novo — e em nenhum dos dois casos a
resposta sai de palpite.

Os 8 proxies sao tirados ANTES das threads, num `asyncio.run` so: o `_lock` do
pool e um `asyncio.Lock`, que pertence ao laco onde foi criado e nao atravessa
para outro. E `acquire` marca `_in_use`, entao oito chamadas seguidas devolvem
oito IPs distintos — o que a sonda 29 NAO fez, por criar um pool novo num
processo novo e receber de volta o mesmo IP de sempre.

Sobre o cookie (sonda 29): o `storage_state` guarda `address-latitude`,
`address-longitude` e o `fstr.session` com `geoPoint`, e mesmo assim repor tudo
numa sessao nova voltou "Escolha um endereco". Nao esta explicado, e fica de
fora daqui: a 17 s por ciclo, posicionar de novo custa menos que depender de
algo que nao entendi.
"""
import asyncio
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")

MEDOIDES = [
    ("0,0", -29.924148, -51.217139, "Bianchini S.A."),
    ("0,1", -29.904190, -51.223210, "IEAD A Voz do Evangelho Pleno"),
    ("1,0", -29.922035, -51.184630, "Mapi Soccers"),
    ("1,1", -29.908444, -51.188198, "Escolainfantil_semear"),
    ("2,0", -29.929048, -51.172479, "Residencial Tuiuti"),
    ("2,1", -29.902556, -51.168809, "Residencial Armonda"),
    ("3,0", -29.921423, -51.136055, "Ferragem Santiago"),
    ("3,1", -29.904627, -51.139649, "Escolinha Encanto da Crianca"),
]

LINK = re.compile(r"/delivery/([^/]+)/[^/]+/"
                  r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
VER_MAIS = ".cardstack-nextcontent__button, [aria-label='Ver mais']"
trava = threading.Lock()
TUDO = {}
PLACAR = []

EXIBIDO = """() => {
  const a = [...document.querySelectorAll("header button, [class*='address']")];
  const t = a.map(e => (e.textContent||'').trim())
             .filter(s => /Próximo de|Escolha um endereço/.test(s));
  return t.length ? t[0] : null;
}"""


def posicionar(page, lat, lng, diario):
    ctx = page.context
    ctx.grant_permissions(["geolocation"], origin="https://www.ifood.com.br")
    ctx.set_geolocation({"latitude": lat, "longitude": lng, "accuracy": 20})
    diario["navegador_diz"] = page.evaluate("""() => new Promise(r => {
      navigator.geolocation.getCurrentPosition(
        p => r(p.coords.latitude + ',' + p.coords.longitude),
        e => r('erro ' + e.code), {timeout: 8000});
    })""")

    alvo = page.get_by_text("Usar minha localização", exact=False).first
    alvo.wait_for(state="visible", timeout=60000)
    alvo.scroll_into_view_if_needed(timeout=10000)
    caixa = alvo.bounding_box()
    if not caixa:
        raise RuntimeError("o rotulo nao tem caixa — modal fechado?")
    page.mouse.click(caixa["x"] + caixa["width"] / 2, caixa["y"] + caixa["height"] / 2)
    page.wait_for_timeout(8000)

    diario["exibido"] = page.evaluate(EXIBIDO)
    if not diario["exibido"] or "Escolha" in diario["exibido"]:
        raise RuntimeError("a coordenada nao aplicou: %r" % diario["exibido"])


def colher(page, destino, diario):
    # `networkidle` nao serve: a pagina do iFood mantem trafego de fundo e nunca
    # fica ociosa. Espera-se o DOM, e entao o primeiro cartao.
    page.reload(wait_until="domcontentloaded", timeout=120000)
    page.wait_for_selector("a.merchant-v2__link", timeout=150000)

    def quantos():
        return page.eval_on_selector_all("a.merchant-v2__link", "e => e.length")

    diario["ao_abrir"] = n = quantos()
    for _ in range(60):
        if not page.eval_on_selector_all(VER_MAIS, "e => e.length"):
            break
        antes = n
        page.evaluate("""(sel) => { const b = [...document.querySelectorAll(sel)].pop();
          if (b) { b.scrollIntoView({block:'center'}); b.click(); } }""", VER_MAIS)
        page.wait_for_timeout(2400)
        n = quantos()
        if n == antes:
            break
    diario["ao_fim"] = n

    for href in page.eval_on_selector_all(
            "a.merchant-v2__link", "e => e.map(x => x.getAttribute('href'))"):
        m = LINK.search(href or "")
        if m:
            destino[m.group(2)] = m.group(1)


def uma_celula(par):
    (celula, lat, lng, poi), proxy_url, ip = par
    from scrapling.fetchers import StealthySession
    t0 = time.time()
    achado = {}
    diario = {"navegador_diz": None, "exibido": None, "ao_abrir": None, "ao_fim": None}

    def acao(page):
        posicionar(page, lat, lng, diario)
        colher(page, achado, diario)

    try:
        with StealthySession(headless=True, solve_cloudflare=True, network_idle=True,
                             proxy=proxy_url, locale="pt-BR",
                             timezone_id="America/Sao_Paulo") as s:
            s.fetch("https://www.ifood.com.br/inicio", page_action=acao, timeout=420000)
        estado = "ok" if achado else "colheu ZERO"
    except Exception as e:
        estado = "%s: %s" % (type(e).__name__, str(e)[:100])

    with trava:
        novos = [k for k in achado if k not in TUDO]
        TUDO.update(achado)
        PLACAR.append((celula, ip, len(achado), len(novos), time.time() - t0, estado, dict(diario)))
        print("  %-4s %-22s %-28s %4d lojas · %4d ineditas · %3.0fs · %s"
              % (celula, ip, poi[:28], len(achado), len(novos), time.time() - t0, estado))
        print("       exibido=%r  cartoes %s -> %s"
              % (diario["exibido"], diario["ao_abrir"], diario["ao_fim"]))
    return achado


from proxy_pool import ProxyPool  # noqa: E402

pool = ProxyPool(pais="BR")
pool.start()


async def pegar(n):
    return [await pool.acquire() for _ in range(n)]


escolhidos = asyncio.run(pegar(len(MEDOIDES)))
tarefas = []
for alvo, px in zip(MEDOIDES, escolhidos):
    if not px:
        print("  pool sem IP livre para a celula %s" % alvo[0])
        continue
    cfg = ProxyPool.to_playwright(px)
    servidor = cfg["server"].replace("http://", "")
    url = "http://%s:%s@%s" % (cfg.get("username", ""), cfg.get("password", ""), servidor)
    tarefas.append((alvo, url, servidor))

print("\n== 8 medoides · UM IP POR NAVEGADOR ==")
for alvo, _, ip in tarefas:
    print("   %-4s %s" % (alvo[0], ip))
print()

t0 = time.time()
with ThreadPoolExecutor(max_workers=len(tarefas)) as ex:
    list(ex.map(uma_celula, tarefas))

print("\n" + "=" * 82)
print("  %d ids DISTINTOS em %.1f min" % (len(TUDO), (time.time() - t0) / 60))
print("=" * 82)
ok = [p for p in PLACAR if p[5] == "ok"]
print("  celulas que colheram: %d de %d" % (len(ok), len(tarefas)))
for p in sorted(PLACAR):
    if p[5] != "ok":
        print("    FALHOU %s (%s) -> %s" % (p[0], p[1], p[5]))
soma = sum(p[2] for p in PLACAR)
if soma:
    print("  soma das celulas: %d · distintos: %d · repeticao: %.0f%%"
          % (soma, len(TUDO), 100.0 * (1 - len(TUDO) / float(soma))))
cid = {}
for c in TUDO.values():
    cid[c] = cid.get(c, 0) + 1
print("  por cidade no href: %s" % dict(sorted(cid.items(), key=lambda x: -x[1])[:8]))

import io, json  # noqa: E402
with io.open("/sondas/ids_ifood_canoas.json", "w", encoding="utf-8") as f:
    json.dump(TUDO, f, ensure_ascii=False, indent=1)
print("  %d ids gravados em /sondas/ids_ifood_canoas.json" % len(TUDO))
