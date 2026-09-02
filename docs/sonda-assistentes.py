# -*- coding: utf-8 -*-
"""Assistentes — ChatGPT primario, Gemini de segunda opiniao, em lote.

Sem login, por proxy, no navegador quente do Maps. Medido em 02/09/2026, lote de
cinco estabelecimentos numa pergunta so:

    5 de 5 completos · 22,5 s · CNPJ, telefone e FONTE COM LINK em quase tudo
    Habib's 18.982.784/0004-09 (filial) — contra 27.665.906/0001-81
    (controladora) que o bloco de IA da busca insistia em dar

POR QUE O CHATGPT E O PRIMARIO. Ele devolve endereco com numero, telefone, CNPJ,
razao social e fonte por campo; o Gemini erra mais a entidade e preenche menos.
O Gemini entra so para o que voltar incompleto — duas opinioes com fonte valem
mais que uma, e discordancia entre elas e informacao, nao ruido.

AS TRES CORRECOES DESTA VERSAO, todas de defeitos que eu mesmo introduzi:

  1. O BANNER DE COOKIES DA OPENAI travava a primeira aba. A tela capturada era
     a lista de idiomas e os "Controles de dados" — 150 s esperando resposta de
     uma pergunta que estava atras de um modal. Agora ele e fechado, e pela
     opcao que RECUSA o nao essencial.

  2. A CAIXA PRECISA ESTAR PRONTA, nao so visivel. O primeiro lote de cada aba
     morria com zero blocos: eu digitava antes de o campo aceitar foco. Agora se
     confere que o texto ENTROU antes de enviar, e tenta-se de novo se nao.

  3. CONVERSA NOVA A CADA LOTE. Na mesma conversa o Gemini trata a pergunta
     seguinte como "complete a anterior" e devolve os itens do lote passado com
     mais campos preenchidos — parece dado novo e nao e.

E duas armadilhas que continuam valendo, medidas antes:

    o ENTER NAO ENVIA no ChatGPT — so o botao
    a resposta vive em `code`/`pre`, nao em `[data-message-author-role]`

O DADO DAQUI E PISTA. `Kampeki Sushi 33.300.010/0001-00` tem cara de numero
redondo demais, e noutro caminho "Loft Maxplaza" virou "Loft Brasil Tecnologia
Ltda" — a proptech. O CNPJ passa pela BrasilAPI antes de valer.
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

ASSISTENTES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "caixa": ("#prompt-textarea", "textarea", 'div[contenteditable="true"]',
                  '[role="textbox"]'),
        "botao": ('button[data-testid="send-button"]',
                  'button[aria-label*="nviar"]', 'button[aria-label*="end"]'),
    },
    "gemini": {
        "url": "https://gemini.google.com/app",
        "caixa": ('div[contenteditable="true"]',
                  'rich-textarea div[contenteditable]', '[role="textbox"]'),
        "botao": None,          # aqui o Enter envia
    },
}

# O que fechar antes de digitar. A ordem importa: recusar o nao essencial vem
# antes de "aceitar tudo", que fica so como ultimo recurso para nao ficar preso.
CONSENTIMENTO = ("Rejeitar não essenciais", "Reject non-essential", "Recusar",
                 "Rejeitar tudo", "Reject all", "Only essential",
                 "Fechar", "Stay logged out", "Agora não", "Entendi",
                 "Aceitar tudo", "Accept all")

CHAVES = ("n", "nome", "endereco", "numero", "bairro", "cep", "telefone",
          "whatsapp", "cnpj", "razao_social", "situacao_cadastral", "site",
          "instagram", "instagram_seguidores", "facebook",
          "facebook_seguidores", "fontes")

# Campos que decidem se vale pedir a segunda opiniao.
ESSENCIAIS = ("endereco", "numero", "telefone", "cnpj", "razao_social")


def montar(lote):
    """Pergunta natural primeiro; as regras curtas no fim.

    Prompt defensivo demais faz o modelo se recolher: numa rodada o Gemini
    devolveu null em tudo e escreveu em `fonte` que os dados vieram "obtidos
    diretamente do prompt" — nao foi procurar nada.
    """
    linhas = "\n".join(
        "%d) %s — %s, %s %s%s"
        % (i, a["nome"], a.get("bairro") or "", a.get("cidade") or "",
           a.get("uf") or "", (", CEP %s" % a["cep"]) if a.get("cep") else "")
        for i, a in enumerate(lote, 1))
    return (
        "Para cada estabelecimento da lista, me diz onde fica (rua e número), o "
        "telefone, o WhatsApp, o CNPJ, a razão social, a situação cadastral, o "
        "site e as redes sociais com o total de seguidores em cada uma:\n"
        + linhas + "\n"
        "Manda em JSON, um array com um objeto por estabelecimento, com as "
        "chaves: " + ", ".join(CHAVES) + " — 'n' é o número da lista. "
        "Em 'fontes', um objeto com uma entrada por campo preenchido, cujo valor "
        "é o LINK (URL completa) de onde tirou; sem link, o nome da fonte. "
        "Só o que souber e tiver confirmação, não inventa nada: null no que não "
        "tiver. Não usa CNPJ, telefone ou perfil da rede, da matriz ou de outra "
        "unidade — se for da rede, diz isso na fonte."
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


def extrair(textos):
    for t in reversed(textos or []):
        b = t.strip()
        for abre, fecha in (("[", "]"), ("{", "}")):
            i, j = b.find(abre), b.rfind(fecha)
            if i < 0 or j <= i:
                continue
            try:
                return json.loads(b[i:j + 1])
            except Exception:
                continue
    return None


def fechar_modais(pg):
    """Fecha consentimento e convites de login. Sem isto a primeira aba trava."""
    fechou = []
    for _ in range(3):                     # podem vir empilhados
        achou = False
        for texto in CONSENTIMENTO:
            try:
                b = pg.get_by_role("button", name=texto)
                if b.count():
                    b.first.click(timeout=4000)
                    fechou.append(texto)
                    pg.wait_for_timeout(2000)
                    achou = True
                    break
            except Exception:
                continue
        if not achou:
            break
    return fechou


def escrever(pg, seletores, texto):
    """Digita e CONFERE que entrou. Campo visivel nem sempre aceita foco ainda."""
    for tentativa in range(3):
        alvo = None
        for sel in seletores:
            try:
                e = pg.locator(sel).first
                e.wait_for(state="visible", timeout=12000)
                alvo = e
                break
            except Exception:
                continue
        if alvo is None:
            pg.wait_for_timeout(4000)
            continue
        try:
            alvo.click(timeout=10000)
            pg.wait_for_timeout(600)
            alvo.type(texto, delay=4)
            pg.wait_for_timeout(900)
            # entrou mesmo? textarea guarda em `value`, contenteditable em texto
            entrou = pg.evaluate(
                """(sel) => {
                     const e = document.querySelector(sel);
                     if (!e) return 0;
                     return ((e.value || e.textContent || '') + '').trim().length;
                   }""", sel)
            if entrou and entrou > 40:
                return True, sel
        except Exception:
            pass
        pg.wait_for_timeout(3000)
    return False, None


def perguntar(pg, cfg, pergunta):
    ok, sel = escrever(pg, cfg["caixa"], pergunta)
    if not ok:
        return None, "o texto não entrou na caixa", 0

    antes_n = pg.evaluate(CONTAR) or 0
    enviou = False
    if cfg["botao"]:
        # O ENTER NAO ENVIA no ChatGPT. O botao e obrigatorio.
        for s in cfg["botao"]:
            try:
                pg.locator(s).first.click(timeout=6000)
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


def abrir(nav, qual):
    """Aba nova, modais fechados, pronta para perguntar."""
    cfg = ASSISTENTES[qual]
    ctx = nav.new_context(viewport={"width": 1360, "height": 1000},
                          locale="pt-BR", timezone_id="America/Sao_Paulo",
                          storage_state=COOKIE if os.path.exists(COOKIE) else None)
    pg = ctx.new_page()
    pg.goto(cfg["url"], timeout=90000, wait_until="domcontentloaded")
    pg.wait_for_timeout(random.randint(9000, 12000))
    fechados = fechar_modais(pg)
    return ctx, pg, fechados


def consultar(pw, proxy, qual, lotes):
    """Varios lotes na MESMA aba, cada um em conversa NOVA."""
    cfg = ASSISTENTES[qual]
    saidas = []
    nav = pw.chromium.launch(headless=False, args=ARGS, proxy=proxy)
    try:
        for n, lote in enumerate(lotes, 1):
            # ABA NOVA POR LOTE, e não `goto` na mesma.
            #
            # A conversa precisa ser nova — na mesma, o Gemini completa a
            # anterior em vez de responder a atual, e devolve os itens do lote
            # passado. Mas recarregar a aba deixou a caixa num estado em que o
            # envio não sai: o lote 2 ficou 150 s sem resposta. Aba nova custa
            # ~10 s e o NAVEGADOR continua o mesmo, que é o caro (perfil, IP e
            # cookie ficam de pé).
            ctx, pg, fechados = abrir(nav, qual)
            if n == 1 and fechados:
                print("     modais fechados: %s" % fechados)
            dados, erro, dt = perguntar(pg, cfg, montar(lote))
            saidas.append({"lote": n, "dados": dados, "erro": erro, "segundos": dt})
            ctx.close()
    finally:
        nav.close()
    return saidas


def incompletos(dados):
    """Os que voltaram sem campo essencial — sao esses que vao ao Gemini."""
    return [d.get("nome") for d in (dados or [])
            if isinstance(d, dict) and any(not d.get(c) for c in ESSENCIAIS)]


if __name__ == "__main__":
    LOTES = [[
        {"nome": "Habib's", "bairro": "Centro", "cidade": "Canoas", "uf": "RS", "cep": "92010-011"},
        {"nome": "Mokai Sushi", "bairro": "Marechal Rondon", "cidade": "Canoas", "uf": "RS"},
        {"nome": "Ferragem Santiago", "bairro": "Estância Velha", "cidade": "Canoas", "uf": "RS"},
    ], [
        {"nome": "Kampeki Sushi", "bairro": "Centro", "cidade": "Canoas", "uf": "RS"},
        {"nome": "Mercado Thomé", "bairro": "Mato Grande", "cidade": "Canoas", "uf": "RS"},
    ]]

    from proxy_pool import ProxyPool
    import asyncio

    pool = ProxyPool(pais="BR")
    pool.start()

    async def pegar(n):
        return [await pool.acquire() for _ in range(n)]

    PX = [{"server": p["server"], "username": p.get("username"),
           "password": p.get("password")} for p in asyncio.run(pegar(2)) if p]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        print("== ChatGPT (primário) · %d lotes na mesma aba ==" % len(LOTES))
        rs = consultar(pw, PX[0], "chatgpt", LOTES)
        faltando = []
        for r in rs:
            d = r["dados"]
            print("   lote %d · %ss · %s · erro=%s"
                  % (r["lote"], r["segundos"],
                     "array de %d" % len(d) if isinstance(d, list) else type(d).__name__,
                     r["erro"]))
            for x in (d if isinstance(d, list) else []):
                f = x.get("fontes") or {}
                links = sum(1 for v in f.values()
                            if isinstance(v, str) and v.startswith("http"))
                print("      %-22s cnpj=%-20s tel=%-16s · %d fontes, %d com link"
                      % (str(x.get("nome"))[:22], str(x.get("cnpj"))[:20],
                         str(x.get("telefone"))[:16], len(f), links))
            faltando += incompletos(d if isinstance(d, list) else [])

        if faltando:
            print("\n== Gemini (segunda opinião: %s) ==" % faltando)
            alvos = [a for lote in LOTES for a in lote if a["nome"] in faltando]
            for r in consultar(pw, PX[1 % len(PX)], "gemini", [alvos]):
                for x in (r["dados"] if isinstance(r["dados"], list) else []):
                    print("      %-22s cnpj=%-20s tel=%s"
                          % (str(x.get("nome"))[:22], str(x.get("cnpj"))[:20],
                             x.get("telefone")))
        else:
            print("\n  nada incompleto — o Gemini não foi consultado")
