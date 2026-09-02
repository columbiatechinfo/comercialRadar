# -*- coding: utf-8 -*-
"""Airbnb + ChatGPT — o print do anuncio vira endereco com numero.

O BURACO QUE ISSO FECHA. O Airbnb entrega coordenada ARREDONDADA em 92% dos
anuncios (4 casas, ~11 m) e endereco nenhum. Eu tinha dado isso como limite da
fonte: sem rua e numero, o POI nao casa com CNEFE nem com a base de clientes.

O usuario mostrou a saida: mandar a IMAGEM do anuncio ao ChatGPT. No exemplo
dele, o print de uma cabana devolveu "Alameda das Pedras Negras, 565 — Parque
Dom Joao VI, Nova Friburgo - RJ, 28616-090", com fonte, e ainda o aviso de que o
Airbnb publica area aproximada.

Isso muda o que o extrator precisa produzir: alem dos campos do payload, um
SCREENSHOT da ficha.

O que esta sonda mede:

    o ChatGPT sem login aceita upload de imagem?
    a partir do print, ele identifica a hospedagem?
    chega a rua e numero — e diz de onde tirou?
    quanto custa, em tempo, por anuncio?
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, "/app")

COOKIE = "/app/estado/cookie_maps.json"
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]
GPT = "https://chatgpt.com/"

# anuncios reais de Canoas, colhidos pela sonda do Airbnb
ANUNCIOS = [
    {"id": "1741584102271278344", "bairro": "Nossa Senhora das Graças",
     "cidade": "Canoas", "uf": "RS"},
    {"id": "1234716769928608285", "bairro": "Centro", "cidade": "Canoas", "uf": "RS"},
]

CHAVES = ("nome_anuncio", "predio_ou_condominio", "endereco", "numero", "bairro",
          "cidade", "uf", "cep", "telefone", "site", "instagram", "confianca",
          "fontes")


def prompt_da_imagem(a):
    return (
        "Essa imagem é a captura de um anúncio de hospedagem do Airbnb que fica "
        "no bairro %s, em %s %s. "
        "Identifica a hospedagem e me diz o endereço completo com rua e número, "
        "o CEP, o nome do prédio ou condomínio, o telefone, o site e as redes "
        "sociais do local. "
        "Manda em JSON com as chaves: %s. "
        "Em 'fontes', um objeto com o link (URL) de onde tirou cada dado; sem "
        "link, o nome da fonte. Em 'confianca', escreve alta, media ou baixa "
        "para o endereço, e por quê. "
        "Só o que souber e tiver confirmação, não inventa nada: null no que não "
        "tiver. Se o Airbnb só mostrar a área aproximada, diz isso."
        % (a["bairro"], a["cidade"], a["uf"], ", ".join(CHAVES))
    )


BLOCOS = r"""(corte) => {
  const s = [];
  for (const e of document.querySelectorAll('code, pre')) {
    const t = (e.textContent || '').trim();
    if (t.length > 60) s.push(t);
  }
  return s.slice(corte);
}"""
CONTAR = """() => [...document.querySelectorAll('code, pre')]
    .filter(e => (e.textContent || '').trim().length > 60).length"""
TEXTO = r"""() => {
  const p = [];
  const anda = (n) => {
    if (!n) return;
    const tag = (n.tagName || '').toLowerCase();
    if (tag === 'style' || tag === 'script') return;
    if (n.children.length === 0) { const t=(n.textContent||'').trim(); if (t) p.push(t); return; }
    for (const f of n.children) anda(f);
  };
  anda(document.body);
  return p.join(' | ').slice(-2500);
}"""


def extrair(textos):
    for t in reversed(textos or []):
        b = t.strip()
        for abre, fecha in (("{", "}"), ("[", "]")):
            i, j = b.find(abre), b.rfind(fecha)
            if i < 0 or j <= i:
                continue
            try:
                return json.loads(b[i:j + 1])
            except Exception:
                continue
    return None


# ---------------------------------------------------------------- Airbnb ----
def printar_anuncio(proxy_url, anuncio):
    """A ficha aberta, fotografada. E o que vai ao GPT."""
    from scrapling.fetchers import StealthySession
    caminho = "/sondas/anuncio-%s.png" % anuncio["id"]
    url = "https://www.airbnb.com.br/rooms/%s" % anuncio["id"]
    dados = {}

    def acao(page):
        page.wait_for_timeout(7000)
        dados["titulo"] = (page.title() or "")[:80]
        # a ficha inteira num quadro: titulo, fotos, tipo e bairro juntos —
        # foi assim que o print do usuario identificou a hospedagem
        page.screenshot(path=caminho, full_page=False)

    with StealthySession(headless=True, solve_cloudflare=True,
                         wait_selector="h1", wait_selector_state="attached",
                         proxy=proxy_url, locale="pt-BR",
                         timezone_id="America/Sao_Paulo") as s:
        s.fetch(url, page_action=acao, timeout=300000)
    dados["arquivo"] = caminho
    dados["bytes"] = os.path.getsize(caminho) if os.path.exists(caminho) else 0
    return dados


# ---------------------------------------------------------------- ChatGPT ---
def perguntar_com_imagem(pg, caminho, pergunta):
    # O upload e por <input type=file>, que costuma estar escondido.
    entradas = pg.locator('input[type="file"]')
    if not entradas.count():
        return None, "sem input de arquivo", 0
    entradas.first.set_input_files(caminho)
    pg.wait_for_timeout(6000)          # a miniatura precisa subir antes do envio

    alvo = None
    for sel in ("#prompt-textarea", "textarea", 'div[contenteditable="true"]'):
        try:
            e = pg.locator(sel).first
            e.wait_for(state="visible", timeout=10000)
            alvo = e
            break
        except Exception:
            continue
    if alvo is None:
        return None, "sem caixa", 0

    antes_n = pg.evaluate(CONTAR) or 0
    alvo.click(timeout=10000)
    alvo.type(pergunta, delay=4)
    pg.wait_for_timeout(1500)

    # O ENTER NAO ENVIA no ChatGPT — medido. O botao e obrigatorio.
    enviou = False
    for sel in ('button[data-testid="send-button"]', 'button[aria-label*="nviar"]',
                'button[aria-label*="end"]'):
        try:
            pg.locator(sel).first.click(timeout=8000)
            enviou = True
            break
        except Exception:
            continue
    if not enviou:
        pg.keyboard.press("Enter")

    t0 = time.time()
    blocos, anterior, estavel = [], -1, 0
    while time.time() - t0 < 150:
        if (pg.evaluate(CONTAR) or 0) > antes_n:
            blocos = pg.evaluate(BLOCOS, antes_n) or []
            tam = sum(len(b) for b in blocos)
            if tam and tam == anterior:
                estavel += 1
                if estavel >= 2:
                    break
            else:
                estavel = 0
            anterior = tam
        pg.wait_for_timeout(2500)
    return extrair(blocos), None, round(time.time() - t0, 1)


from proxy_pool import ProxyPool  # noqa: E402
import asyncio  # noqa: E402

pool = ProxyPool(pais="BR")
pool.start()


async def pegar(n):
    return [await pool.acquire() for _ in range(n)]


px = [p for p in asyncio.run(pegar(3)) if p]


def url_de(p):
    return "http://%s:%s@%s" % (p.get("username", ""), p.get("password", ""),
                                str(p["server"]).replace("http://", ""))


print("== 1. fotografar os anúncios ==")
prints = []
for i, a in enumerate(ANUNCIOS):
    try:
        d = printar_anuncio(url_de(px[i % len(px)]), a)
        d.update(a)
        prints.append(d)
        print("   %s · %d bytes · %s" % (a["id"], d["bytes"], d["titulo"][:60]))
    except Exception as e:
        print("   %s FALHOU: %s: %s" % (a["id"], type(e).__name__, str(e)[:70]))

print("\n== 2. mandar ao ChatGPT ==")
from playwright.sync_api import sync_playwright  # noqa: E402

saidas = []
with sync_playwright() as pw:
    p = px[-1]
    proxy = {"server": p["server"], "username": p.get("username"),
             "password": p.get("password")}
    nav = pw.chromium.launch(headless=False, args=ARGS, proxy=proxy)
    try:
        for d in prints:
            ctx = nav.new_context(viewport={"width": 1360, "height": 1000},
                                  locale="pt-BR", timezone_id="America/Sao_Paulo",
                                  storage_state=COOKIE if os.path.exists(COOKIE) else None)
            pg = ctx.new_page()
            pg.goto(GPT, timeout=90000, wait_until="domcontentloaded")
            pg.wait_for_timeout(random.randint(9000, 12000))
            for texto in ("Fechar", "Stay logged out", "Agora não"):
                try:
                    b = pg.get_by_role("button", name=texto)
                    if b.count():
                        b.first.click(timeout=4000)
                        pg.wait_for_timeout(2000)
                        break
                except Exception:
                    pass
            try:
                dados, erro, dt = perguntar_com_imagem(pg, d["arquivo"],
                                                       prompt_da_imagem(d))
            except Exception as e:
                dados, erro, dt = None, "%s: %s" % (type(e).__name__, str(e)[:80]), 0
            print("\n   -- anúncio %s (%ss) --" % (d["id"], dt))
            if erro:
                print("      ERRO: %s" % erro)
            if dados:
                for k in CHAVES:
                    if k in dados and k != "fontes":
                        print("      %-22s %s" % (k, str(dados[k])[:66]))
                f = dados.get("fontes") or {}
                links = sum(1 for v in f.values()
                            if isinstance(v, str) and v.startswith("http"))
                print("      %-22s %d campos · %d com link" % ("fontes", len(f), links))
            else:
                print("      sem JSON. Trecho da tela:")
                print("      %s" % (pg.evaluate(TEXTO) or "")[-400:])
            try:
                pg.screenshot(path="/sondas/gpt-img-%s.png" % d["id"][:12])
            except Exception:
                pass
            saidas.append({"anuncio": d["id"], "dados": dados, "erro": erro,
                           "segundos": dt})
            ctx.close()
    finally:
        nav.close()

with open("/sondas/airbnb_imagem.json", "w", encoding="utf-8") as f:
    json.dump(saidas, f, ensure_ascii=False, indent=1)
print("\n  gravado /sondas/airbnb_imagem.json")
