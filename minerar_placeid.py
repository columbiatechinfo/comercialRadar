# -*- coding: utf-8 -*-
"""Etapa 4 — colher placeId de graca e abrir cada POI pelo id.

Substitui `minerar_captura.py`. A diferenca nao e de implementacao, e de
caminho:

    antes   tile -> OCR le o nome -> BUSCA esse nome no Maps -> torce
    agora   tile -> placeId sai no evento de clique -> ABRE aquele POI

Buscar por nome lido em pixel erra, e erra de um jeito que nao da para auditar:
sao os baldes `Match valido`, `Distancia alta` e `Nao encontrado`. Navegando por
id nao ha o que errar.

E nao custa: o `placeId` chega no proprio evento, antes de qualquer requisicao.
Com `event.stop()` o cartao nao abre, e e o cartao que dispara o `GetPlace`
cobrado — 64 chamadas por tile viram zero.

Tres passos:

  A. COLHEITA   varre o poligono em tiles z20 com passo de meio tile, clicando
                em grade. Adaptativo: onde ainda aparece POI novo, refina.
                Guarda tambem a imagem do tile.
  B. DETALHE    abre `maps/place/?q=place_id:<ID>` por proxy e le tudo —
                nome, categoria, endereco, telefone, nota, histograma por
                estrela, resumo do Gemini, avaliacoes com resposta do dono,
                horario dos sete dias, movimento hora a hora, fotos.
  C. GRAVACAO   grava em `pois`, `comentarios`, `horario_funcionamento` e
                `images_urls`.

Tudo por ancora semantica (`aria-label`, `data-*`, `role`), nunca por classe:
as classes do Maps sao ofuscadas e trocam a cada release.
"""
import argparse
import asyncio
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, "/app")
import config  # noqa: F401,E402

import area_utils  # noqa: E402
import base_comum as bc  # noqa: E402
from proxy_pool import ProxyPool  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CHAVE = os.environ.get("MAPS_JS_KEY", "").strip()
MAP_ID = os.environ.get("MAPS_MAP_ID", "33696f50cbe8e2d298796ada")
ZOOM = 20
LARG, ALT = 1280, 900

# `chrome-headless-shell` estoura com SIGSEGV ao subir e ainda se anuncia:
# webdriver=true, plugins=0, e `HeadlessChrome` nas marcas. O Chromium completo
# sob Xvfb nao faz nem uma coisa nem outra.
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]

# ------------------------------------------------------------------ colheita --

MAPA_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>*{margin:0;padding:0}html,body,#map{width:%(l)dpx;height:%(a)dpx}</style>
</head><body><div id="map"></div><script>
window.__ids=[];
function initMap(){
  const map = new google.maps.Map(document.getElementById('map'), {
    center:{lat:%(lat)s,lng:%(lng)s}, zoom:%(zoom)d, mapId:'%(mapid)s',
    mapTypeId:'roadmap', disableDefaultUI:true, clickableIcons:true });
  map.addListener('click', e => {
    if (e.placeId) {
      window.__ids.push({placeId:e.placeId, lat:e.latLng.lat(), lng:e.latLng.lng()});
      e.stop();   // sem isto o cartao abre, e o cartao e o que custa
    }
  });
  google.maps.event.addListenerOnce(map,'idle',()=>{
    const b = map.getBounds();
    window.__caixa = {s:b.getSouthWest().lat(), o:b.getSouthWest().lng(),
                      n:b.getNorthEast().lat(), l:b.getNorthEast().lng()};
    window.__pronto = true;
  });
}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=%(chave)s&callback=initMap&loading=async" async defer></script>
</body></html>"""


def metros_por_pixel(lat, zoom):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


async def varrer_tile(pw, lat, lng, passo_px, pasta, rotulo):
    """Uma posicao: colhe os placeId visiveis e fotografa o tile."""
    arq = "/tmp/mp_%s.html" % rotulo
    open(arq, "w", encoding="utf-8").write(
        MAPA_HTML % {"l": LARG, "a": ALT, "lat": lat, "lng": lng,
                     "zoom": ZOOM, "mapid": MAP_ID, "chave": CHAVE})
    nav = await pw.chromium.launch(headless=False, args=ARGS)
    try:
        pg = await (await nav.new_context(
            viewport={"width": LARG, "height": ALT})).new_page()
        cobradas = []
        pg.on("request", lambda r: cobradas.append(r.url)
              if "places.googleapis.com" in r.url else None)
        await pg.goto("file://" + arq)
        await pg.wait_for_function("window.__pronto === true", timeout=40000)
        await pg.wait_for_timeout(1600)

        if pasta:
            bruto = await pg.screenshot()
            try:
                import io
                from PIL import Image
                Image.open(io.BytesIO(bruto)).convert("RGB").save(
                    os.path.join(pasta, "%s.webp" % rotulo), "WEBP",
                    quality=90, method=6)
            except Exception:
                open(os.path.join(pasta, "%s.png" % rotulo), "wb").write(bruto)

        for x in range(50, LARG - 30, passo_px):
            for y in range(50, ALT - 30, passo_px):
                await pg.mouse.click(x, y)
                await pg.wait_for_timeout(15)
        await pg.wait_for_timeout(1800)
        ids = await pg.evaluate("window.__ids")
        return {i["placeId"]: i for i in ids}, len(cobradas)
    finally:
        await nav.close()


async def colher(pw, poligono, pasta, passo_px, paralelo, refinar_acima_de):
    """Varre o poligono. Passo de meio tile; refina onde ainda aparece novo.

    A sobreposicao nao e custo extra: com passo de meio tile cada ponto ja cai
    dentro de 4 tiles vizinhos por construcao. Um tile sozinho ve 59% do que
    existe no proprio retangulo — o resto some por colisao de rotulo.
    """
    s, n, o, l = area_utils.bbox(poligono)
    lat_c = (s + n) / 2.0
    mpp = metros_por_pixel(lat_c, ZOOM)
    alt_graus = (ALT * mpp) / 111320.0
    lar_graus = (LARG * mpp) / (111320.0 * math.cos(math.radians(lat_c)))

    passo_lat, passo_lng = alt_graus / 2.0, lar_graus / 2.0
    pos = []
    y = s
    while y <= n + passo_lat:
        x = o
        while x <= l + passo_lng:
            pos.append((y, x))
            x += passo_lng
        y += passo_lat

    print("  area %.0f m x %.0f m · tile %.0f m x %.0f m · %d posicoes"
          % ((n - s) * 111320, (l - o) * 111320 * math.cos(math.radians(lat_c)),
             ALT * mpp, LARG * mpp, len(pos)))

    achados, cobradas_total = {}, 0
    trava = asyncio.Lock()
    sem = asyncio.Semaphore(paralelo)
    a_refinar = []

    async def uma(i, lat, lng, marca):
        nonlocal cobradas_total
        async with sem:
            try:
                ids, cob = await varrer_tile(pw, lat, lng, passo_px, pasta,
                                             "%s_%03d" % (marca, i))
            except Exception as e:
                async with trava:
                    print("    %s %03d FALHOU: %s" % (marca, i, str(e)[:70]))
                return
            async with trava:
                cobradas_total += cob
                novos = [k for k in ids if k not in achados]
                achados.update(ids)
                print("    %s %03d: %3d na tela, %2d novos → %d"
                      % (marca, i, len(ids), len(novos), len(achados)))
                # Adaptativo: so refina onde a varredura ainda esta rendendo.
                if len(novos) >= refinar_acima_de:
                    a_refinar.append((lat, lng))

    await asyncio.gather(*(uma(i, la, lo, "t") for i, (la, lo) in enumerate(pos)))

    if a_refinar:
        print("  refinando %d posicoes que ainda rendiam (>= %d novos)"
              % (len(a_refinar), refinar_acima_de))
        finos = []
        for la, lo in a_refinar:
            for dy in (-0.25, 0.25):
                for dx in (-0.25, 0.25):
                    finos.append((la + dy * alt_graus, lo + dx * lar_graus))
        await asyncio.gather(*(uma(i, la, lo, "r")
                               for i, (la, lo) in enumerate(finos)))

    dentro = {k: v for k, v in achados.items()
              if area_utils.ponto_no_poligono(v["lat"], v["lng"], poligono)}
    print("  %d placeIds distintos · %d dentro do poligono · %d chamadas COBRADAS"
          % (len(achados), len(dentro), cobradas_total))
    return dentro


# ------------------------------------------------------------------- detalhe --

VISAO_GERAL = r"""() => {
  const lim = s => (s||'').replace(/\s+/g,' ').trim();
  const q   = s => document.querySelector(s);
  const txt = s => { const e = q(s); return e ? lim(e.textContent) : null; };
  const aria = s => { const e = q(s); return e ? lim(e.getAttribute('aria-label')) : null; };
  const semRotulo = s => s ? s.replace(/^[^:]{3,20}:\s*/, '') : null;
  const todos = [...document.querySelectorAll('[aria-label]')];
  const porAria = re => todos.filter(e => re.test(e.getAttribute('aria-label')||''));

  const hist = {};
  porAria(/^\d\s+estrelas?,\s*[\d.,]+\s+avalia/i).forEach(e => {
    const m = e.getAttribute('aria-label').match(/^(\d)\s+estrelas?,\s*([\d.,]+)/i);
    if (m) hist[m[1]] = parseInt(m[2].replace(/[.,]/g,''), 10);
  });
  const pico = {};
  porAria(/movimento\s+[aà]s/i).forEach(e => {
    const m = e.getAttribute('aria-label').match(/(\d{1,2}):\d{2}[^\d]*(\d{1,3})\s*%/);
    if (m) pico[m[1]] = parseInt(m[2], 10);
  });
  const horario = {};
  [...document.querySelectorAll('table tr')].forEach(tr => {
    const c = tr.querySelectorAll('td,th');
    if (c.length >= 2) {
      const d = lim(c[0].textContent), h = lim(c[1].textContent);
      if (/segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo/i.test(d)) horario[d] = h;
    }
  });
  const fotos = [...new Set([...document.querySelectorAll('img')]
    .map(i => i.src)
    .filter(s => /googleusercontent|streetviewpixels/.test(s))
    .filter(s => !/\/a-?\//.test(s) && !/=w\d{1,2}-h\d{1,2}/.test(s)))];

  const h1 = q('h1');
  const cab = h1 && h1.parentElement && h1.parentElement.parentElement
              ? lim(h1.parentElement.parentElement.innerText) : '';
  const mNota = cab.match(/(\d,\d)/);

  return {
    nome     : txt('h1'),
    categoria: txt('button[jsaction*="category"]'),
    nota     : mNota ? mNota[1] : null,
    histograma: hist,
    totalAval: Object.values(hist).reduce((a,b)=>a+b,0) || null,
    endereco : semRotulo(aria('button[data-item-id="address"]')),
    telefone : semRotulo(aria('button[data-item-id^="phone"]')),
    site     : semRotulo(aria('a[data-item-id="authority"]')),
    plusCode : semRotulo(aria('button[data-item-id="oloc"]')),
    dentroDe : txt('button[data-item-id*="locatedin"]'),
    statusHorario: txt('div[jsaction*="openhours"]'),
    horarioSemana: horario,
    horariosDePico: pico,
    fotos    : fotos.slice(0, 60),
    resumoIA : (() => {
      const e = q('[data-about-this-summary-url]');
      if (!e) return null;
      let no = e;
      for (let i = 0; i < 8 && no; i++) {
        const t = lim(no.innerText || '');
        if (t.length >= 120 && t.length <= 1400 && !/Vis[ãa]o geral/.test(t))
          return t.replace(/\s*\+\d+\s*Resumo feito com o Gemini.*$/i, '')
                  .replace(/\s*Summarized with Gemini.*$/i, '').trim();
        no = no.parentElement;
      }
      return null;
    })(),
    assuntos : [...document.querySelectorAll('button')]
      .map(b => lim(b.innerText))
      .filter(t => /^[\wÀ-ÿ' ]{3,24}\s+\d{1,4}$/.test(t)).slice(0, 25)
  };
}"""

AVALIACOES = r"""() => {
  const lim = s => (s||'').replace(/\s+/g,' ').trim();
  // O plural de "mes" e MESES, e alternativa de regex e ORDENADA: com
  // /m[êe]s|meses/ o "mes" casa primeiro e parte a palavra ao meio.
  const QUANDO = /(h[áa]\s+)?(uma?|\d+)\s+(minutos?|horas?|semanas?|meses|m[êe]s|dias?|anos?)(\s+atr[áa]s)?/i;
  const vistos = new Set(); const saida = [];
  for (const el of document.querySelectorAll('[data-review-id]')) {
    const id = el.getAttribute('data-review-id');
    if (!id || vistos.has(id)) continue;      // aparece na visao geral E na aba
    vistos.add(id);
    // `innerText` DEPENDE DE LAYOUT: devolve '' para elemento fora de vista.
    // A lista de avaliacoes rola, e o que sai da tela virava texto vazio — o
    // filtro do fim jogava fora. O Shopping Via Porcello vinha com 0 avaliacao
    // mesmo com um navegador so, o que descartou a hipotese de disputa de CPU.
    // `textContent` nao depende de layout.
    const t = lim(el.innerText || el.textContent);
    if (!t) continue;
    const aria = [...el.querySelectorAll('[aria-label]')]
      .map(e => e.getAttribute('aria-label') || '');
    const mNota = (aria.find(a => /^\d\s+estrela/i.test(a)) || '').match(/^(\d)/);
    const foto  = aria.find(a => /^Foto de\s+/i.test(a));
    const mQ    = t.match(QUANDO);
    const mNa   = t.match(/·\s*([\d.]+)\s+avalia/i);
    const iResp = t.search(/Resposta do propriet[áa]rio/i);
    let corpo = iResp > 0 ? t.slice(0, iResp) : t;
    if (mQ) { const i = corpo.indexOf(mQ[0]); if (i >= 0) corpo = corpo.slice(i + mQ[0].length); }
    corpo = corpo.replace(/^\s*(NOVA|NOVO|NEW)\s+/i, '')
                 .replace(/\s*(Gostei|Compartilhar|Mais|Traduzir)\s*/gi, ' ')
                 .replace(/…\s*$/, '').replace(/\s+/g, ' ').trim();
    saida.push({
      id, autor: foto ? foto.replace(/^Foto de\s*/i,'') : null,
      nota: mNota ? parseInt(mNota[1],10) : null,
      quando: mQ ? lim(mQ[0]) : null,
      localGuide: /Local Guide/i.test(t),
      avalDoAutor: mNa ? parseInt(mNa[1].replace(/\./g,''),10) : null,
      texto: corpo.slice(0,1200) || null,
      resposta: iResp > 0
        ? lim(t.slice(iResp).replace(/^Resposta do propriet[áa]rio\s*/i,'')).slice(0,600)
        : null});
  }
  return saida.filter(r => r.nota !== null || r.texto);
}"""


# Quantas avaliacoes bastam por POI. O Cafe Imperial tem 780; rolar ate o fim
# custaria minutos por POI para trazer opiniao de tres anos atras. Ordenado por
# mais recentes, as primeiras sao as que valem.
ALVO_AVALIACOES = 20

# Histograma, total, resumo do Gemini e chips de assunto — o bloco que fica no
# TOPO da aba de avaliacoes e some assim que a lista rola.
RESUMO_AVALIACOES = r"""() => {
  const lim = s => (s||'').replace(/\s+/g,' ').trim();
  const todos = [...document.querySelectorAll('[aria-label]')];
  const hist = {};
  todos.filter(e => /^\d\s+estrelas?,\s*[\d.,]+\s+avalia/i.test(
                      e.getAttribute('aria-label')||''))
       .forEach(e => {
         const m = e.getAttribute('aria-label').match(/^(\d)\s+estrelas?,\s*([\d.,]+)/i);
         if (m) hist[m[1]] = parseInt(m[2].replace(/[.,]/g,''), 10);
       });
  const q = s => document.querySelector(s);
  let resumo = null;
  const e = q('[data-about-this-summary-url]');
  if (e) {
    let no = e;
    for (let i = 0; i < 8 && no; i++) {
      const t = lim(no.innerText || '');
      if (t.length >= 120 && t.length <= 1400 && !/Vis[ãa]o geral/.test(t)) {
        resumo = t.replace(/\s*\+\d+\s*Resumo feito com o Gemini.*$/i, '')
                  .replace(/\s*Summarized with Gemini.*$/i, '').trim();
        break;
      }
      no = no.parentElement;
    }
  }
  return {
    histograma: hist,
    totalAval : Object.values(hist).reduce((a,b)=>a+b,0) || null,
    resumoIA  : resumo,
    assuntos  : [...document.querySelectorAll('button')]
                  .map(b => lim(b.innerText))
                  .filter(t => /^[\wÀ-ÿ' ]{3,24}\s+\d{1,4}$/.test(t)).slice(0, 25)
  };
}"""


async def ordenar_recentes(pg):
    """Ordena por mais recentes. Devolve o rotulo escolhido, ou None.

    Tudo por `Locator`, nunca por `ElementHandle`: o handle e resolvido uma vez
    e o Maps recria o botao logo em seguida — deu
    `Element is not attached to the DOM` em 8 de 90.

    E nada aqui pode derrubar o POI: ordenar e conveniencia, o dado do
    cabecalho ja esta na mao.
    """
    try:
        bt = pg.locator('[aria-label*="lassificar" i], [aria-label*="rdenar" i]')
        if await bt.count() == 0:
            return None
        await bt.first.click(timeout=8000)
        await pg.wait_for_timeout(1200)
        itens = pg.locator('[role="menuitemradio"], [role="menuitem"]')
        n = await itens.count()
        for i in range(n):
            it = itens.nth(i)
            t = ((await it.text_content()) or "").strip().lower()
            if "recent" in t or "newest" in t:
                await it.click(timeout=8000)
                await pg.wait_for_timeout(2500)
                return t
        # Menu aberto e nada casou: fecha, senao ele cobre a lista e a rolagem
        # acontece por cima de um menu.
        await pg.keyboard.press("Escape")
    except Exception:
        try:
            await pg.keyboard.press("Escape")
        except Exception:
            pass
    return None


async def clicar_aba(pg, padrao):
    """Clica na aba cujo rotulo casa com `padrao`. Devolve True se clicou.

    Duas armadilhas, as duas medidas:

    - **pela posicao nao serve.** `abas[1]` supoe que toda pagina tem as mesmas
      tres abas na mesma ordem, e nao tem: POI sem avaliacao vem com menos.
    - **`ElementHandle` fica obsoleto.** Pegar o elemento e clicar depois deu
      `Element is not attached to the DOM` em 11 de 90: a pagina recria a aba
      entre uma coisa e outra. `Locator` resolve o elemento NA HORA do clique e
      tenta de novo sozinho.
    """
    import re
    # `count()` NAO ESPERA. Perguntar se a aba existe antes de a pagina
    # terminar de desenha-la devolve zero — e eu concluia "este POI nao tem
    # avaliacoes". Deu 84 de 90 numa corrida e 40 em outra, com o mesmo codigo:
    # era corrida de renderizacao, nao caracteristica do POI.
    try:
        await pg.wait_for_selector('[role="tab"]', timeout=15000)
    except Exception:
        pg._rotulos_vistos = "nenhuma aba apareceu em 15s"
        return False
    alvo = pg.locator('[role="tab"]').filter(has_text=re.compile(padrao, re.I))
    for _ in range(10):
        try:
            if await alvo.count() > 0:
                # `Locator` resolve o elemento NA HORA do clique e tenta de novo
                # sozinho — `ElementHandle` fica obsoleto quando o Maps recria
                # a aba, e deu "Element is not attached to the DOM" em 11 de 90.
                await alvo.first.click(timeout=8000)
                return True
        except Exception:
            pass
        await pg.wait_for_timeout(900)
    # Desistiu: registra o que a pagina de fato mostrava, senao "sem aba" pode
    # ser tanto POI sem avaliacao quanto defeito meu — e ja foi defeito meu
    # tres vezes.
    try:
        pg._rotulos_vistos = await pg.evaluate(
            "[...document.querySelectorAll('[role=\"tab\"]')]"
            ".map(e => (e.textContent||'').trim()).join(' | ') || '(zero abas)'")
    except Exception:
        pg._rotulos_vistos = "(nao consegui ler)"
    return False


async def carregar_avaliacoes(pg):
    """Abre a aba, espera o CONTEUDO e rola ate parar de crescer.

    A primeira versao esperava por RELOGIO — 2,8 a 4,2 s depois do clique. Com
    6 navegadores em paralelo, cada um por um proxy diferente, esse tempo nao
    chega: 80 dos 90 POIs foram gravados com zero comentario, e o Cafe Imperial,
    que tem 780 avaliacoes, trouxe 5.

    Espera-se pelo que se quer ler, e para-se quando ele para de crescer.
    """
    if not await clicar_aba(pg, r"avalia|review"):
        # NAO E FALHA. Ha POI sem aba de avaliacoes nenhuma — o Shopping Via
        # Porcello tem so "Visao geral" e "Sobre". Passei tres correcoes
        # tentando consertar um zero que era verdadeiro.
        return None, [], {"_motivo": "sem aba de avaliacoes · abas vistas: %s"
                                     % getattr(pg, "_rotulos_vistos", "?")}
    try:
        # O conteudo, nao o cronometro.
        await pg.wait_for_selector("[data-review-id]", timeout=20000)
    except Exception:
        return None, [], {"_motivo": "aba aberta, nenhuma avaliacao carregou"}

    # O HISTOGRAMA E LIDO AQUI, ANTES DE ROLAR.
    # A lista de avaliacoes e virtualizada: depois de uma dezena de rolagens o
    # bloco de resumo, que e onde moram os "5 estrelas, 116 avaliacoes", ja saiu
    # do DOM. Lido depois, veio em 6 de 90; lido aqui, vem enquanto esta na tela.
    resumo = await pg.evaluate(RESUMO_AVALIACOES)

    async def rolar_e_ler():
        anterior, parado = -1, 0
        for _ in range(16):
            n = await pg.evaluate(
                "document.querySelectorAll('[data-review-id]').length")
            if n >= ALVO_AVALIACOES:      # as mais recentes bastam; 780 nao
                break
            if n == anterior:
                parado += 1
                if parado >= 3:
                    break
            else:
                parado = 0
            anterior = n
            await pg.mouse.wheel(0, random.randint(2000, 3200))
            await pg.wait_for_timeout(random.randint(800, 1500))
        return await pg.evaluate(AVALIACOES)

    # COLHE ANTES DE ORDENAR, E DEPOIS TAMBEM.
    #
    # Ordenar por "mais recentes" RECARREGA a lista inteira, e ler no meio da
    # troca devolve pouco ou nada: a mesma Flora que deu 70 avaliacoes numa
    # sonda isolada dava 0 aqui. Colhendo dos dois lados e juntando por id, a
    # ordenacao so pode ACRESCENTAR — nunca destruir o que ja estava na mao.
    achadas = {a["id"]: a for a in await rolar_e_ler()}

    ordem = await ordenar_recentes(pg)
    if ordem:
        try:
            await pg.wait_for_selector("[data-review-id]", timeout=15000)
            for a in await rolar_e_ler():
                achadas.setdefault(a["id"], a)
        except Exception:
            pass

    return ordem, list(achadas.values()), resumo


async def garantir_cookie(pw, pool, caminho, renovar):
    """O cookie que faz o Google entregar a ficha inteira. Um arquivo, alguns KB.

    NAO E OTIMIZACAO DE VELOCIDADE, E REQUISITO DE COLETA. Sem cookie e sem
    consentimento aceito, o Google serve uma ficha REDUZIDA — sem a aba de
    avaliacoes — e esse zero e indistinguivel de "este lugar nao tem
    avaliacao". Medido em 01/09/2026, mesmo instante e mesmo proxy:

        contexto virgem   O Boticario (174 aval.)  2 abas ·  0 avaliacoes
        com cookie        O Boticario              3 abas · 79 avaliacoes

    POR QUE COOKIE E NAO PERFIL PERSISTENTE. A primeira versao usava
    `launch_persistent_context`, e funcionou — na primeira execucao. No reuso,
    todas as requisicoes voltavam **HTTP 407**: o Chromium guarda estado de
    autenticacao de proxy dentro do perfil e atropela as credenciais que o
    Playwright injeta. Alem disso cada perfil pesava 52 MB, e 35 deles seriam
    1,8 GB de cache.

    `storage_state` guarda so cookie e localStorage — 1,1 KB, tres cookies — e
    **viaja entre IPs**: medido, o mesmo arquivo em outro navegador e outro
    proxy devolve as mesmas 79 avaliacoes. E isso que permite rotacionar proxy
    por POI mantendo a memoria.

    O arquivo NAO e renovado ao fim de cada execucao, de proposito: sessao
    quente vale mais que sessao nova. Renova-se por comando — `--renovar-cookie`.
    """
    if os.path.exists(caminho) and not renovar:
        idade = (time.time() - os.path.getmtime(caminho)) / 3600.0
        import json as _json
        try:
            n = len(_json.load(open(caminho)).get("cookies", []))
        except Exception:
            n = 0
        print("  cookie de %.1f h atras, %d cookies, reaproveitado "
              "(--renovar-cookie para trocar)" % (idade, n))
        return caminho

    px = pool._proxies[random.randrange(len(pool._proxies))]
    nav = await pw.chromium.launch(headless=False, args=ARGS, proxy={
        "server": px["server"], "username": px["username"],
        "password": px["password"]})
    try:
        ctx = await nav.new_context(
            viewport={"width": 1360, "height": 1000}, locale="pt-BR",
            timezone_id="America/Sao_Paulo")
        pg = await ctx.new_page()
        await pg.goto("https://www.google.com/maps", timeout=60000)
        await pg.wait_for_timeout(random.randint(4500, 7000))
        for texto in ("Aceitar tudo", "Accept all", "Concordo", "Aceito"):
            try:
                b = pg.get_by_role("button", name=texto)
                if await b.count():
                    await b.first.click(timeout=4000)
                    await pg.wait_for_timeout(2500)
                    break
            except Exception:
                pass
        os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
        await ctx.storage_state(path=caminho)
        import json as _json
        e = _json.load(open(caminho))
        print("  cookie novo: %d cookies · %d bytes · pelo %s"
              % (len(e.get("cookies", [])), os.path.getsize(caminho),
                 px["server"]))
        await ctx.close()
    finally:
        await nav.close()
    return caminho


def engordar_cookie(caminho, estados):
    """Guarda de volta o cookie SOMADO do que os navegadores trouxeram.

    Renovar e engordar sao coisas opostas. Renovar joga a sessao fora e comeca
    do zero — e por isso so acontece por comando. Engordar mantem a mesma
    sessao e acrescenta o que ela ganhou navegando: a cada execucao o Google vê
    um visitante com mais historico, nao um estranho reincidente.

    A juncao e por (nome, dominio, caminho), ficando com o de validade mais
    longa. Cada trabalhador partiu do mesmo cookie e divergiu um pouco; a uniao
    e mais rica que qualquer um deles sozinho.

    Se a execucao nao trouxe nada, o arquivo NAO e tocado: uma corrida que
    falhou nao pode apagar uma sessao boa.
    """
    import json as _json
    if not estados:
        return None
    try:
        antes = _json.load(open(caminho)) if os.path.exists(caminho) else {}
    except Exception:
        antes = {}

    juntos, origens = {}, {}
    for e in [antes] + list(estados):
        for c in (e or {}).get("cookies", []) or []:
            k = (c.get("name"), c.get("domain"), c.get("path"))
            velho = juntos.get(k)
            if not velho or (c.get("expires") or 0) > (velho.get("expires") or 0):
                juntos[k] = c
        for o in (e or {}).get("origins", []) or []:
            origens[o.get("origin")] = o

    novo = {"cookies": list(juntos.values()), "origins": list(origens.values())}
    if len(novo["cookies"]) < len(antes.get("cookies", []) or []):
        return None                      # nunca empobrecer o que ja existia

    tmp = caminho + ".novo"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(novo, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, caminho)
    return len(antes.get("cookies", []) or []), len(novo["cookies"])


async def detalhar(ctx, alvo):
    pg = await ctx.new_page()
    cobradas = []
    pg.on("request", lambda r: cobradas.append(r.url)
          if "places.googleapis.com" in r.url else None)
    try:
        await pg.goto("https://www.google.com/maps/place/?q=place_id:" + alvo["placeId"],
                      wait_until="domcontentloaded", timeout=60000)
        # Espera o nome aparecer; so entao o intervalo aleatorio, que existe
        # para nao desenhar padrao — nao para dar tempo de carregar.
        try:
            await pg.wait_for_selector("h1", timeout=25000)
        except Exception:
            pass
        await pg.wait_for_timeout(random.randint(1200, 3000))

        d = await pg.evaluate(VISAO_GERAL)
        d["placeId"] = alvo["placeId"]
        d["lat"], d["lng"] = alvo["lat"], alvo["lng"]
        d["url"] = pg.url

        # As avaliacoes sao a parte OPCIONAL. Se elas falharem, o POI continua
        # valendo: nome, categoria, endereco e telefone ja estao em `d`. Antes
        # uma falha aqui jogava tudo fora — 8 POIs perdidos por um clique numa
        # aba que o Maps tinha acabado de recriar.
        try:
            d["ordenacao"], d["avaliacoes"], resumo = await carregar_avaliacoes(pg)
        except Exception as e:
            d["ordenacao"], d["avaliacoes"], resumo = None, [], {}
            d["falha_avaliacoes"] = str(e)[:120]

        # O bloco de resumo so termina de montar na aba de avaliacoes. O que a
        # visao geral trouxer fica como reserva, mas o da aba manda.
        d["motivo_sem_avaliacao"] = resumo.get("_motivo")
        for campo in ("histograma", "totalAval", "resumoIA", "assuntos"):
            if resumo.get(campo):
                d[campo] = resumo[campo]

        d["cobradas"] = len(cobradas)
        return d
    finally:
        await pg.close()


# ------------------------------------------------------------------ gravacao --

def gravar(con, empresa, registros, sessao, simular):
    placar = {"novos": 0, "atualizados": 0, "comentarios": 0,
              "horarios": 0, "fotos": 0, "sem_nome": 0}
    with con.cursor() as k:
        for d in registros:
            if not d or not d.get("nome"):
                placar["sem_nome"] += 1
                continue
            hist = d.get("histograma") or {}
            # O histograma e os horarios de pico nao tem coluna propria ainda:
            # vao em `ia_resposta` como JSON ate a migracao existir. Fica
            # explicito no nome da chave para nao virar dado orfao.
            extra = json.dumps({"histograma_estrelas": hist,
                                "horarios_de_pico": d.get("horariosDePico") or {},
                                "assuntos": d.get("assuntos") or [],
                                "localizado_em": d.get("dentroDe")},
                               ensure_ascii=False)
            k.execute("""
                insert into radar_comercial.pois
                  (fonte, fonte_dado, nome, categoria, endereco, telefone,
                   website, place_id, maps_lat, maps_lng, maps_url, plus_code,
                   avaliacao, total_avaliacoes, resumo_avaliacoes,
                   status_horario, cidade, uf, sessao, coord_fonte,
                   coord_precisao, ia_resposta)
                values ('maps', 'maps:place_id', %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        'maps', 'porta', %s)
                on conflict (id_empresa, place_id)
                  where place_id is not null and place_id <> ''
                do update set
                   nome = excluded.nome,
                   categoria = coalesce(excluded.categoria, pois.categoria),
                   endereco = coalesce(excluded.endereco, pois.endereco),
                   telefone = coalesce(excluded.telefone, pois.telefone),
                   website = coalesce(excluded.website, pois.website),
                   avaliacao = excluded.avaliacao,
                   total_avaliacoes = excluded.total_avaliacoes,
                   resumo_avaliacoes = excluded.resumo_avaliacoes,
                   status_horario = excluded.status_horario,
                   ia_resposta = excluded.ia_resposta
                returning id, (xmax = 0) as inserido""",
                (d.get("nome"), d.get("categoria"), d.get("endereco"),
                 d.get("telefone"), d.get("site"), d["placeId"],
                 d["lat"], d["lng"], d.get("url"), d.get("plusCode"),
                 d.get("nota"), d.get("totalAval"), d.get("resumoIA"),
                 d.get("statusHorario"), "Canoas", "RS", sessao, extra))
            poi_id, inserido = k.fetchone()
            placar["novos" if inserido else "atualizados"] += 1

            k.execute("delete from radar_comercial.comentarios where poi_id=%s",
                      (poi_id,))
            for a in (d.get("avaliacoes") or []):
                # `comentarios` nao tem coluna para Local Guide nem para a
                # resposta do dono. A resposta vai colada ao texto, marcada,
                # para nao se perder ate a migracao.
                texto = a.get("texto") or ""
                if a.get("resposta"):
                    texto = (texto + "\n\n[RESPOSTA DO PROPRIETARIO] "
                             + a["resposta"]).strip()
                if not texto and a.get("nota") is None:
                    continue
                k.execute("""insert into radar_comercial.comentarios
                               (poi_id, autor, data, nota, texto)
                             values (%s,%s,%s,%s,%s)""",
                          (poi_id, a.get("autor"), a.get("quando"),
                           a.get("nota"), texto or None))
                placar["comentarios"] += 1

            k.execute("delete from radar_comercial.horario_funcionamento "
                      "where poi_id=%s", (poi_id,))
            for dia, h in (d.get("horarioSemana") or {}).items():
                k.execute("""insert into radar_comercial.horario_funcionamento
                               (poi_id, dia, horario) values (%s,%s,%s)""",
                          (poi_id, dia, h))
                placar["horarios"] += 1

            k.execute("delete from radar_comercial.images_urls where poi_id=%s",
                      (poi_id,))
            for i, u in enumerate(d.get("fotos") or []):
                k.execute("""insert into radar_comercial.images_urls
                               (poi_id, url, ordem) values (%s,%s,%s)""",
                          (poi_id, u, i))
                placar["fotos"] += 1
    if simular:
        con.rollback()
    else:
        con.commit()
    return placar


# ---------------------------------------------------------------------- main --

async def principal(a):
    poligono = area_utils.carregar_area(a.area)
    if not poligono:
        print("area '%s' nao existe em area_trabalho" % a.area)
        return 2
    print("area '%s': %d vertices" % (a.area, len(poligono)))

    pasta = None
    if not a.sem_tiles:
        pasta = "/app/capturas/%s/tiles_z%d" % (a.sessao, ZOOM)
        os.makedirs(pasta, exist_ok=True)

    pool = ProxyPool()
    pool.start()

    t0 = time.time()
    async with async_playwright() as pw:
        if a.sem_colheita:
            # Os placeId ja colhidos estao no banco. Repetir a varredura so
            # para reprocessar o detalhe custa 8 min a toa.
            con = bc.conectar()
            try:
                with con.cursor() as k:
                    k.execute("""select place_id, maps_lat, maps_lng
                                   from radar_comercial.pois
                                  where fonte = 'maps' and sessao = %s
                                    and place_id is not null""", (a.sessao,))
                    alvos = {p: {"placeId": p, "lat": float(la), "lng": float(lo)}
                             for p, la, lo in k.fetchall()}
            finally:
                con.close()
            print("\n⟦A⟧ pulada: %d placeIds vindos do banco" % len(alvos))
            t_colheita = 0.0
        else:
            print("\n⟦A⟧ colheita de placeId (de graca)")
            alvos = await colher(pw, poligono, pasta, a.passo, a.workers,
                                 a.refinar_acima_de)
            t_colheita = time.time() - t0
        if a.limite:
            alvos = dict(list(alvos.items())[:a.limite])
            print("  limitado a %d para o teste" % len(alvos))
        if not alvos:
            return 1

        print("\n⟦B⟧ detalhe: %d navegadores, cada um num proxy, "
              "todos com a MESMA sessao quente" % a.workers)
        t1 = time.time()
        registros, trava = [], asyncio.Lock()
        fila = asyncio.Queue()
        for v in alvos.values():
            fila.put_nowait(v)

        async def trabalhador(i):
            """Um navegador, muitos POIs. Nunca fecha entre um e outro."""
            px = pool._proxies[(i * 7) % len(pool._proxies)]
            nav = None
            try:
                nav = await pw.chromium.launch(headless=False, args=ARGS, proxy={
                    "server": px["server"], "username": px["username"],
                    "password": px["password"]})
                # A memoria vem do cookie, nao do perfil em disco: 1,1 KB que
                # viaja entre IPs, em vez de 52 MB presos a um proxy so.
                ctx = await nav.new_context(
                    viewport={"width": 1360, "height": 1000}, locale="pt-BR",
                    timezone_id="America/Sao_Paulo", storage_state=cookie)
                while True:
                    try:
                        alvo = fila.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        d = await detalhar(ctx, alvo)
                    except Exception as e:
                        async with trava:
                            registros.append(None)
                            print("    %3d/%d ERRO %s"
                                  % (len(registros), len(alvos), str(e)[:70]))
                        continue
                    async with trava:
                        registros.append(d)
                        print("    %3d/%d %-36s %-22s %3d aval%s"
                              % (len(registros), len(alvos),
                                 (d.get("nome") or "SEM NOME")[:36],
                                 (d.get("categoria") or "-")[:22],
                                 len(d.get("avaliacoes") or []),
                                 "  FALHA:" + d["falha_avaliacoes"][:40]
                                 if d.get("falha_avaliacoes") else ""))
                    # Intervalo aleatorio ENTRE POIs do mesmo navegador.
                    await asyncio.sleep(random.uniform(a.intervalo_min,
                                                       a.intervalo_max))
            except Exception as e:
                async with trava:
                    print("    navegador %02d caiu: %s" % (i, str(e)[:80]))
            finally:
                if nav:
                    # O que este navegador ganhou navegando volta para o
                    # arquivo — a sessao engorda em vez de recomecar.
                    try:
                        colhidos.append(await ctx.storage_state())
                    except Exception:
                        pass
                    await nav.close()

        cookie = await garantir_cookie(pw, pool, a.cookie, a.renovar_cookie)
        colhidos = []
        await asyncio.gather(*(trabalhador(i) for i in range(a.workers)))
        cresceu = engordar_cookie(a.cookie, colhidos)
        if cresceu:
            print("    cookie engordado: %d → %d cookies" % cresceu)
        t_detalhe = time.time() - t1

    print("\n⟦C⟧ gravacao")
    con = bc.conectar()
    try:
        # Resolve a identidade pelo mesmo caminho dos outros cinco scripts da
        # fase 1: --empresa vence; senao o RADAR_USUARIO_SERVICO do .env.
        with con.cursor() as k:
            empresa = bc.empresa_da_sessao(k, a.empresa or "")
        placar = gravar(con, empresa, registros, a.sessao, a.simular)
    finally:
        con.close()

    bons = [r for r in registros if r]
    cobradas = sum(r.get("cobradas", 0) for r in bons)
    print("\n" + "=" * 62)
    print("  placeIds no poligono   : %d" % len(alvos))
    print("  detalhados com nome    : %d" % len([r for r in bons if r.get("nome")]))
    print("  com endereco           : %d" % len([r for r in bons if r.get("endereco")]))
    print("  com telefone           : %d" % len([r for r in bons if r.get("telefone")]))
    print("  com resumo do Gemini   : %d" % len([r for r in bons if r.get("resumoIA")]))
    print("  com histograma         : %d" % len([r for r in bons if r.get("histograma")]))
    com_av = [r for r in bons if r.get("avaliacoes")]
    print("  com avaliacao colhida   : %d  (%d avaliacoes, media %.1f por POI)"
          % (len(com_av), sum(len(r["avaliacoes"]) for r in com_av),
             (sum(len(r["avaliacoes"]) for r in com_av) / len(com_av))
             if com_av else 0))
    motivos = {}
    for r in bons:
        if not r.get("avaliacoes"):
            m = r.get("motivo_sem_avaliacao") or "colheu zero sem motivo"
            motivos[m] = motivos.get(m, 0) + 1
    for m, q in sorted(motivos.items(), key=lambda x: -x[1]):
        print("    sem avaliacao: %-38s %d" % (m, q))
    print("  CHAMADAS COBRADAS      : %d" % cobradas)
    print("  gravado                : %s" % placar)
    print("  tempo colheita/detalhe : %.0f min / %.0f min"
          % (t_colheita / 60, t_detalhe / 60))
    if a.simular:
        print("  (--simular: nada foi gravado)")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default="quadra-canoas-centro")
    p.add_argument("--sessao", default="canoas_quadra")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--passo", type=int, default=26,
                   help="passo do clique em pixels; o icone de POI tem ~24 px")
    p.add_argument("--refinar-acima-de", type=int, default=3,
                   help="refina a posicao que ainda trouxe N ou mais POIs novos")
    p.add_argument("--limite", type=int, default=0,
                   help="so os N primeiros POIs, para teste")
    p.add_argument("--sem-tiles", action="store_true")
    p.add_argument("--sem-colheita", action="store_true",
                   help="pula a varredura e usa os placeId ja gravados nesta sessao")
    p.add_argument("--cookie", default="/app/estado/cookie_maps.json",
                   help="o cookie que faz o Google entregar a ficha inteira. "
                        "Sobrevive entre execucoes de proposito — sessao quente "
                        "vale mais que sessao nova")
    p.add_argument("--renovar-cookie", action="store_true",
                   help="descarta o cookie e faz um novo. NAO acontece "
                        "automaticamente ao fim da execucao")
    p.add_argument("--intervalo-min", type=float, default=1.5,
                   help="espera minima entre POIs do MESMO navegador")
    p.add_argument("--intervalo-max", type=float, default=5.0)
    p.add_argument("--empresa", default="")
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(principal(a)))
