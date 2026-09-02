# -*- coding: utf-8 -*-
"""iFood, sonda 28 — fazer o cookie de uma celula, por proxy, e dizer ONDE o
endereco mora.

O usuario pediu para imitar o que a etapa 4 faz no Maps, e o padrao de la esta
em `minerar_placeid.py` (ADR 0006):

    o cookie e feito UMA VEZ, por um navegador, e salvo com `storage_state`
    ele VIAJA ENTRE IPs — o mesmo arquivo em outro proxy devolve o mesmo
      resultado, e e isso que permite rotacionar proxy mantendo a memoria
    1,1 KB, tres cookies — contra 52 MB de perfil em disco, que ainda dava
      HTTP 407 na segunda execucao
    e ele e VALIDADO antes de subir vinte navegadores, porque cookie vencido
      nao da erro: devolve pagina vazia, e vazio parece dado

Traduzido para o iFood, o cookie e POR CELULA: cada uma tem sua coordenada, e
portanto seu endereco. Feito uma vez, a colheita seguinte abre /inicio ja
posicionada — sem modal, sem clique, sem geolocalizacao.

Mas antes de montar esse ciclo falta uma medicao, e e ela que esta sonda faz: o
`storage_state` guarda cookie E localStorage, e passar `cookies=` na sessao so
repoe a primeira metade. Se o iFood guardar a posicao no localStorage, o cookie
sozinho nao reposiciona nada — e eu descobriria isso do jeito caro, com oito
navegadores devolvendo lista errada sem reclamar.

Entao: posiciona uma celula por proxy, e RELATA onde o endereco foi parar.
"""
import io
import json
import sys
import time

sys.path.insert(0, "/app")

CELULA = "2,0"
LAT, LNG = -29.929048, -51.172479   # medoide "Residencial Tuiuti"
COOKIE = "/sondas/cookie-ifood-%s.json" % CELULA.replace(",", "-")


def anotar(passo, valor):
    print("  %-22s %s" % (passo, valor))


# Tudo que o site guardou e que se pareca com posicao: nome da chave ou valor
# contendo coordenada, endereco ou bairro.
ONDE_MORA = """() => {
  const achados = {localStorage: [], sessionStorage: [], cookies: []};
  const interessa = (k, v) => /address|endereco|location|localiza|lat|lng|city|cidade|bairro|geo/i.test(k)
                           || /-29\\.9|-51\\.[12]|Tuiuti|Gracas|Canoas/i.test(v || '');
  for (const [nome, loja] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]]) {
    for (let i = 0; i < loja.length; i++) {
      const k = loja.key(i);
      const v = loja.getItem(k) || '';
      if (interessa(k, v)) achados[nome].push({chave: k, tamanho: v.length, trecho: v.slice(0, 160)});
    }
  }
  for (const c of document.cookie.split(';')) {
    const [k, ...r] = c.trim().split('=');
    const v = r.join('=');
    if (interessa(k, v)) achados.cookies.push({chave: k, trecho: decodeURIComponent(v).slice(0, 160)});
  }
  return achados;
}"""


def posicionar(page, lat, lng):
    ctx = page.context
    ctx.grant_permissions(["geolocation"], origin="https://www.ifood.com.br")
    ctx.set_geolocation({"latitude": lat, "longitude": lng, "accuracy": 20})
    anotar("navegador diz", page.evaluate("""() => new Promise(r => {
      navigator.geolocation.getCurrentPosition(
        p => r(p.coords.latitude + ',' + p.coords.longitude),
        e => r('erro ' + e.code), {timeout: 8000});
    })"""))

    alvo = page.get_by_text("Usar minha localização", exact=False).first
    alvo.wait_for(state="visible", timeout=60000)
    alvo.scroll_into_view_if_needed(timeout=10000)
    caixa = alvo.bounding_box()
    if not caixa:
        raise RuntimeError("o rotulo nao tem caixa")
    page.mouse.click(caixa["x"] + caixa["width"] / 2, caixa["y"] + caixa["height"] / 2)
    page.wait_for_timeout(9000)

    exibido = page.evaluate("""() => {
      const a = [...document.querySelectorAll("header button, [class*='address']")];
      const t = a.map(e => (e.textContent||'').trim())
                 .filter(s => /Próximo de|Escolha um endereço/.test(s));
      return t.length ? t[0] : null;
    }""")
    anotar("o site exibe", repr(exibido))
    if not exibido or "Escolha" in exibido:
        raise RuntimeError("a coordenada nao aplicou")

    print("\n  -- onde o endereco foi guardado --")
    onde = page.evaluate(ONDE_MORA)
    for lugar, itens in onde.items():
        print("   %s: %d" % (lugar, len(itens)))
        for it in itens[:6]:
            print("      %-34s %s" % (it["chave"][:34], (it.get("trecho") or "")[:110]))
    print()
    return onde


from proxy_pool import ProxyPool  # noqa: E402
import asyncio  # noqa: E402

pool = ProxyPool(pais="BR")
pool.start()
px = asyncio.run(pool.acquire())
cfg = ProxyPool.to_playwright(px)
servidor = cfg["server"].replace("http://", "")
proxy_url = "http://%s:%s@%s" % (cfg.get("username", ""), cfg.get("password", ""), servidor)
anotar("proxy", "%s (%s)" % (cfg["server"], px.get("country")))

from scrapling.fetchers import StealthySession  # noqa: E402

onde = {}
t0 = time.time()
with StealthySession(headless=True, solve_cloudflare=True, network_idle=True,
                     proxy=proxy_url, locale="pt-BR",
                     timezone_id="America/Sao_Paulo") as s:
    s.fetch("https://www.ifood.com.br/inicio",
            page_action=lambda p: onde.update(posicionar(p, LAT, LNG) or {}),
            timeout=420000)

    # O estado, do jeito que o Maps guarda: cookie + localStorage num arquivo so.
    try:
        estado = s.context.storage_state()
        with io.open(COOKIE, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False)
        origens = estado.get("origins", [])
        chaves = sum(len(o.get("localStorage", [])) for o in origens)
        anotar("storage_state", "%d cookies · %d origens · %d chaves de localStorage"
               % (len(estado.get("cookies", [])), len(origens), chaves))
        anotar("gravado em", "%s (%d bytes)"
               % (COOKIE, len(json.dumps(estado))))
        anotar("nomes dos cookies",
               [c["name"] for c in estado.get("cookies", [])][:12])
    except Exception as e:
        anotar("storage_state", "FALHOU: %s: %s" % (type(e).__name__, str(e)[:90]))

print("\n  %.0f s" % (time.time() - t0))
