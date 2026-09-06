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
import socket
import sys
import time
import zlib

sys.path.insert(0, "/app")
import config  # noqa: F401,E402

import area_utils  # noqa: E402
import base_comum as bc  # noqa: E402
from proxy_pool import ProxyPool  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CHAVE = os.environ.get("MAPS_JS_KEY", "").strip()
MAP_ID = os.environ.get("MAPS_MAP_ID", "33696f50cbe8e2d298796ada")
ZOOM = 20
# Quem esta trabalhando. Vai para `pois.detalhado_por`, e e o que permite
# medir a divisao entre maquinas em vez de estima-la.
MAQUINA = os.environ.get("RADAR_MAQUINA") or socket.gethostname()
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


def _indexar_tile(pasta, nome, lat, lng, caixa):
    """Acrescenta o tile ao indice da pasta, com a caixa que o mapa reportou.

    UM ARQUIVO POR PASTA, reescrito inteiro a cada tile. E barato — algumas
    centenas de linhas — e sobrevive a interrupcao: uma varredura morta no meio
    deixa o indice coerente com o que existe no disco, em vez de um arquivo
    truncado pela metade. `os.replace` troca o arquivo de uma vez so, entao nem
    a troca tem janela ruim.

    SEM TRAVA, E DE PROPOSITO. As dez capturas sao TAREFAS do mesmo laco
    asyncio, nao threads: codigo sincrono entre dois `await` roda inteiro sem
    ser interrompido. Um `threading.Lock` aqui daria a impressao de proteger
    algo que ja e atomico, e esconderia que a garantia real vem do laco.
    """
    caminho = os.path.join(pasta, "_tiles.json")
    try:
        dados = json.load(open(caminho, encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        dados = {}
    dados[nome] = {
        "lat": lat, "lng": lng, "zoom": ZOOM,
        "largura_px": LARG, "altura_px": ALT,
    }
    if isinstance(caixa, dict) and all(k in caixa for k in "snol"):
        dados[nome].update({
            "lat_min": caixa["s"], "lat_max": caixa["n"],
            "lng_min": caixa["o"], "lng_max": caixa["l"],
        })
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, caminho)


async def varrer_tile(nav, lat, lng, passo_px, pasta, rotulo):
    """Uma posicao: colhe os placeId visiveis e fotografa o tile.

    RECEBE O NAVEGADOR, NAO O PLAYWRIGHT — e a diferenca custa horas.

    A versao anterior fazia `pw.chromium.launch()` aqui dentro: um navegador
    NOVO por posicao, aberto e fechado. Subir Chromium leva alguns segundos, e
    a varredura de uma cidade tem dezenas de milhares de posicoes — sao dias
    gastos abrindo e fechando o mesmo programa.

    O contexto continua sendo novo a cada posicao, e e ele que importa para o
    isolamento: cookie, cache e estado de pagina nao atravessam. Contexto e
    barato; processo nao.
    """
    arq = "/tmp/mp_%s.html" % rotulo
    open(arq, "w", encoding="utf-8").write(
        MAPA_HTML % {"l": LARG, "a": ALT, "lat": lat, "lng": lng,
                     "zoom": ZOOM, "mapid": MAP_ID, "chave": CHAVE})
    ctx = await nav.new_context(viewport={"width": LARG, "height": ALT})
    try:
        pg = await ctx.new_page()
        cobradas = []
        pg.on("request", lambda r: cobradas.append(r.url)
              if "places.googleapis.com" in r.url else None)
        # `commit`, E NAO `load`. O padrao do goto espera o evento `load` da
        # pagina — e essa pagina carrega a API JS do Maps mais os tiles, com
        # dez navegadores fazendo o mesmo ao mesmo tempo. Medido na rodada 24
        # (04/09/2026): 27 de 97 posicoes e 30 de ~70 refinos morreram em
        # "Page.goto: Timeout 30000ms" ANTES de qualquer colheita — 28% da
        # area nunca foi varrida, sem erro na etapa. Quem sabe se o mapa esta
        # pronto e `window.__pronto`, logo abaixo, com o seu proprio prazo;
        # o goto so precisa ter comecado a navegar.
        await pg.goto("file://" + arq, wait_until="commit")
        await pg.wait_for_function("window.__pronto === true", timeout=40000)
        await pg.wait_for_timeout(1600)

        if pasta:
            bruto = await pg.screenshot()
            # A COORDENADA VAI NO NOME, e ate 03/09/2026 nao ia.
            #
            # O arquivo se chamava `r_000.webp` — o rotulo diz a ORDEM da
            # varredura e nada mais. A funcao recebe `lat` e `lng`, o mapa ainda
            # publica a caixa exata em `window.__caixa`, e as duas informacoes
            # eram jogadas fora na hora de gravar. O resultado: 566 imagens de
            # Canoas no disco que ninguem consegue situar no mundo.
            #
            # Nao e um detalhe de arquivo: ESTE estilo de mapa desenha as
            # construcoes como poligonos chapados, e e a melhor entrada que o
            # sistema tem para o passo dos telhados — melhor que satelite, que
            # vem com sombra, arvore e perspectiva. Sem a coordenada, ela nao
            # serve para nada.
            #
            # O nome segue a convencao que os tiles PNG ja usavam, para o mesmo
            # leitor achar os dois.
            nome = "tile_%s_%.5f_%.5f" % (rotulo, lat, lng)
            try:
                import io
                from PIL import Image
                Image.open(io.BytesIO(bruto)).convert("RGB").save(
                    os.path.join(pasta, nome + ".webp"), "WEBP",
                    quality=90, method=6)
            except Exception:
                open(os.path.join(pasta, nome + ".png"), "wb").write(bruto)
            # A CAIXA MEDIDA, e nao a calculada. `window.__caixa` e o que o
            # proprio mapa reporta ter desenhado; deduzi-la do zoom e da
            # latitude da quase o mesmo, e o "quase" ja custou caro uma vez —
            # `cruzar_ligacao.Telhado` supunha um tile de 200 m onde ele tem
            # 994, e por isso nunca achava o tile de ponto nenhum.
            try:
                caixa = await pg.evaluate("window.__caixa")
                _indexar_tile(pasta, nome, lat, lng, caixa)
            except Exception:                                  # noqa: BLE001
                pass

        # A GRADE, SEM ESPERA ENTRE CLIQUES.
        #
        # Havia um `wait_for_timeout(15)` aqui. Com 1.504 cliques por posicao
        # ele sozinho somava 23 s, e uma posicao custava 27,5 s — era ELE o
        # motivo de a cidade inteira projetar 161 h.
        #
        # Medido no mesmo ponto, mesma grade, mesmo mapa (03/09/2026):
        #
        #     com 15 ms   25,2 s   27 placeIds
        #     sem espera   1,2 s   27 placeIds   <- o MESMO conjunto
        #
        # Nao e "quase o mesmo": o conjunto e identico, nenhum perdido e nenhum
        # a mais. A espera nao era necessaria porque o ouvinte de `click` roda
        # dentro da pagina e os eventos ficam na fila; quem os recolhe e a
        # espera de 1.800 ms LOGO ABAIXO, e essa continua onde estava — tirar
        # ela, sim, perderia POI.
        #
        # E NAO ADIANTA DISPARAR OS CLIQUES POR JAVASCRIPT. Foi medido junto:
        # despachar a grade inteira com PointerEvent/MouseEvent de dentro da
        # pagina leva 0,7 s e devolve ZERO placeId — o Maps ignora evento
        # sintetico. O clique tem de vir do navegador de verdade.
        for x in range(50, LARG - 30, passo_px):
            for y in range(50, ALT - 30, passo_px):
                await pg.mouse.click(x, y)
        await pg.wait_for_timeout(1800)
        ids = await pg.evaluate("window.__ids")
        return {i["placeId"]: i for i in ids}, len(cobradas)
    finally:
        # SO O CONTEXTO. O navegador e da vaga, e serve a proxima posicao.
        await ctx.close()


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

    def toca_o_desenho(la, lo):
        """O tile centrado aqui encosta no poligono?

        A grade e montada sobre a CAIXA do desenho, que e um retangulo — e um
        municipio nao e retangular. Em Canoas, 23% das posicoes da caixa caem
        inteiras fora da divisa: 8.399 posicoes que eram varridas por completo
        para o resultado ser descartado no fim, quando `ponto_no_poligono`
        finalmente rodava. Sao 2,7 h de clique jogadas fora.

        Testa nove pontos — o centro, os quatro cantos e o meio de cada lado.
        Bastar UM dentro ja mantem a posicao, entao um tile que so encosta na
        divisa continua sendo varrido, e o POI da esquina nao se perde. O caso
        que escaparia e uma lingua de terra mais estreita que meio tile (58 m)
        atravessando o quadro sem tocar nenhum dos nove — margem de rio, onde
        nao ha POI.
        """
        for dy in (0.0, -0.5, 0.5):
            for dx in (0.0, -0.5, 0.5):
                if area_utils.ponto_no_poligono(la + dy * alt_graus,
                                                lo + dx * lar_graus, poligono):
                    return True
        return False

    pos, caixa = [], 0
    y = s
    while y <= n + passo_lat:
        x = o
        while x <= l + passo_lng:
            caixa += 1
            if toca_o_desenho(y, x):
                pos.append((y, x))
            x += passo_lng
        y += passo_lat

    print("  area %.0f m x %.0f m · tile %.0f m x %.0f m · %d posicoes"
          "  (%d da caixa, %d fora do desenho)"
          % ((n - s) * 111320, (l - o) * 111320 * math.cos(math.radians(lat_c)),
             ALT * mpp, LARG * mpp, len(pos), caixa, caixa - len(pos)))

    achados, cobradas_total = {}, 0
    trava = asyncio.Lock()
    a_refinar = []

    # UMA FILA DE NAVEGADORES NO LUGAR DO SEMAFORO.
    #
    # O semaforo so contava vagas; quem pegava a vaga abria o proprio navegador
    # e o fechava no fim. Agora a vaga E o navegador: sao `paralelo` deles,
    # abertos uma vez, e cada posicao pega um da fila e devolve.
    #
    # O `Queue` faz o papel do semaforo — pegar da fila vazia espera — e ainda
    # carrega o objeto, que era o que faltava.
    vagas: "asyncio.Queue" = asyncio.Queue()
    for _ in range(paralelo):
        vagas.put_nowait(await pw.chromium.launch(headless=False, args=ARGS))

    # UMA POSICAO QUE FALHA TENTA DE NOVO, uma vez, com navegador novo.
    #
    # Ate 04/09/2026 a posicao que falhava era simplesmente descartada — e
    # posicao descartada e um pedaco da area que nunca foi varrido, sem
    # nenhum aviso na etapa. Uma segunda tentativa custa segundos; o tile
    # perdido custa os POIs que estavam nele.
    TENTATIVAS_POR_TILE = 2

    async def uma(i, lat, lng, marca):
        nonlocal cobradas_total
        nav = await vagas.get()
        try:
            ids = cob = None
            for tentativa in range(1, TENTATIVAS_POR_TILE + 1):
                try:
                    ids, cob = await varrer_tile(nav, lat, lng, passo_px, pasta,
                                                 "%s_%03d" % (marca, i))
                    break
                except Exception as e:
                    async with trava:
                        print("    %s %03d %s: %s"
                              % (marca, i,
                                 "FALHOU" if tentativa == TENTATIVAS_POR_TILE
                                 else "falhou, tentando de novo",
                                 str(e)[:70]))
                    # NAVEGADOR QUE FALHOU PODE ESTAR MORTO, e devolve-lo
                    # assim contaminaria a vaga para sempre: as posicoes
                    # seguintes que a pegassem falhariam todas, e o log
                    # culparia cada uma delas. Troca-se por um novo.
                    try:
                        await nav.close()
                    except Exception:
                        pass
                    try:
                        nav = await pw.chromium.launch(headless=False, args=ARGS)
                    except Exception:
                        nav = None
                        break
            if ids is None:
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
        finally:
            if nav is not None:
                vagas.put_nowait(nav)

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

    # OS NAVEGADORES SAO DE `colher`, E MORREM COM ELA.
    #
    # Antes cada posicao fechava o seu no `finally`, e nao havia o que limpar
    # no fim. Agora eles sobrevivem a posicao de proposito — entao alguem
    # precisa fecha-los, ou ficam `paralelo` processos Chromium vivos depois
    # que a colheita termina, cada um segurando a sua memoria.
    while not vagas.empty():
        try:
            await vagas.get_nowait().close()
        except Exception:
            pass

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

    # So os do pais ativo: metade do pool e de outro pais e esta
    # reservada, e um proxy reservado devolve pagina em branco.
    bons = [p for p in pool._proxies
            if not pool.pais or p.get("country") == pool.pais] or pool._proxies
    px = bons[random.randrange(len(bons))]
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


async def validar_cookie(pw, pool, caminho, alvo):
    """Abre UM POI e confere se o cookie ainda serve. Devolve True/False.

    POR QUE ISTO EXISTE. Cookie vencido nao da erro: o Google devolve uma
    pagina sem `h1`, e o minerador grava "SEM NOME · 0 aval" em tudo. Aconteceu
    em 01/09/2026 — o i9 processou 40 POIs assim enquanto o Predator, com um
    cookie recem-feito, trabalhava normalmente ao lado. Nada falhou, nada
    avisou, e o resultado vazio parecia dado.

    Uma pagina, cinco segundos. Barato demais para nao fazer antes de subir
    vinte navegadores.
    """
    bons = [p for p in pool._proxies
            if not pool.pais or p.get("country") == pool.pais] or pool._proxies
    px = bons[random.randrange(len(bons))]
    nav = None
    try:
        nav = await pw.chromium.launch(headless=False, args=ARGS, proxy={
            "server": px["server"], "username": px["username"],
            "password": px["password"]})
        ctx = await nav.new_context(
            viewport={"width": 1360, "height": 1000}, locale="pt-BR",
            timezone_id="America/Sao_Paulo", storage_state=caminho)
        pg = await ctx.new_page()
        await pg.goto("https://www.google.com/maps/place/?q=place_id:" + alvo,
                      wait_until="domcontentloaded", timeout=60000)
        try:
            await pg.wait_for_selector("h1", timeout=25000)
        except Exception:
            return False
        nome = await pg.evaluate(
            "() => { const h = document.querySelector('h1');"
            "        return h ? h.textContent.trim() : null; }")
        abas = await pg.evaluate(
            """[...document.querySelectorAll('[role="tab"]')].length""")
        print("  cookie conferido em um POI: %r · %d abas" % (nome, abas))
        return bool(nome)
    except Exception as e:
        print("  cookie nao pode ser conferido: %s" % str(e)[:80])
        return False
    finally:
        if nav:
            await nav.close()


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


# TITULOS QUE NAO SAO POI.
#
# O nome do POI sai do `h1` do painel. Quando o Maps nao abre — sem rede, aba
# recriada, bloqueio — a PROPRIA pagina de erro tem um `h1`, e ele entrava como
# nome: "Maps sem acesso a Internet" gravou 3 POIs fantasma em Canoas
# (03/09/2026), com categoria e endereco vazios. Sao poucos titulos e fixos; a
# comparacao e sem acento e sem caixa, porque o navegador roda em pt-BR mas a
# mensagem varia com o idioma que o Google resolver servir.
_TITULOS_DE_ERRO = frozenset({
    "maps sem acesso a internet", "sem acesso a internet",
    "sem conexao com a internet", "voce esta offline",
    "no internet", "youre offline", "you re offline",
})


def _e_titulo_de_erro(nome) -> bool:
    import unicodedata
    n = unicodedata.normalize("NFKD", (nome or "").strip().lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.replace("'", "").replace("`", "")
    return n in _TITULOS_DE_ERRO


class _ProxyQueimado(Exception):
    """O Google respondeu, e respondeu vazio. Culpa do IP, nao do ponto."""


def _pagina_vazia(d) -> bool:
    """A resposta veio, e nao tem POI nenhum dentro.

    PROXY QUEIMADO NAO LEVANTA EXCECAO — e essa e a razao de existir desta
    funcao. O IP punido pelo Google devolve a pagina do Maps SEM CONTEUDO:
    status 200, DOM montado, nenhum `h1`. Para o codigo isso era sucesso, o
    contador de falhas seguidas voltava a zero, e o navegador seguia a rodada
    inteira no mesmo IP morto.

    MEDIDO em 05/09/2026, nas faixas de Canoas: a faixa 1 detalhou com 1% de
    "SEM NOME"; as faixas 2 e 3, com 55%. Metade do trabalho jogado fora e
    refeito, sem nenhum sinal de erro — o `ProxyPool` tem descanso de 2 h para
    IP punido e nunca era chamado, porque nada aqui reconhecia a punicao.

    O nome vazio e o unico sinal que o Google da. Ele ja era conhecido — o
    comentario de `usaveis` diz "devolve pagina em branco, e o sintoma vira
    SEM NOME" — mas a conclusao tinha parado em filtrar por pais.
    """
    if not d:
        return True
    nome = (d.get("nome") or "").strip()
    return not nome


async def detalhar(ctx, alvo):
    """O caminho do Chromium: abre a pagina no contexto e extrai."""
    pg = await ctx.new_page()
    try:
        return await _extrair_do_ponto(pg, alvo, navegar=True)
    finally:
        try:
            await pg.close()
        except Exception:                                      # noqa: BLE001
            pass


async def _extrair_do_ponto(pg, alvo, navegar=True):
    """Tudo o que se tira de UM ponto, a partir de uma pagina ja aberta.

    `navegar=False` para quem ja chegou na URL certa — e o caso do Camoufox,
    que navega por conta propria antes de entregar a pagina.
    """
    cobradas = []
    pg.on("request", lambda r: cobradas.append(r.url)
          if "places.googleapis.com" in r.url else None)
    try:
        if navegar:
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
        # QUEM ABRIU A PAGINA E QUEM A FECHA. Aqui o extrator so devolve o
        # ouvinte de requisicao que pendurou, para nao vazar entre pontos
        # quando a pagina for reaproveitada.
        try:
            pg.remove_listener("request", None)
        except Exception:                                      # noqa: BLE001
            pass


# ------------------------------------------------------------------ gravacao --

def reservar(con, sessoes, maquina):
    """Toma UM POI da fila. Devolve (id, place_id, lat, lng) ou None.

    `sessoes` e uma LISTA. Uma rodada normal tem uma so; o reprocesso de
    pendencias tem varias, porque os POIs sem detalhe de uma cidade ficaram
    espalhados pelas sessoes que os colheram — em Canoas, 6.696 deles em doze
    sessoes (06/09/2026). Rodar uma por vez pagaria doze vezes o custo fixo de
    subir dez navegadores e aquecer o cookie.

    `FOR UPDATE SKIP LOCKED` e o que faz duas maquinas trabalharem na mesma
    quadra sem combinarem nada: o banco entrega um POI diferente para cada
    pedido, e quem chega depois PULA o que ja esta reservado em vez de esperar
    por ele. Sem fila externa, sem coordenador, sem uma maquina mandando na
    outra — se uma cair, a outra termina o servico sozinha.
    """
    with con.cursor() as k:
        k.execute("""
            update radar_comercial.pois p
               set detalhado_em = now(), detalhado_por = %s
              from (select id from radar_comercial.pois
                     where fonte = 'maps' and sessao = any(%s)
                       and place_id is not null and detalhado_em is null
                     order by id
                     for update skip locked
                     limit 1) q
             where p.id = q.id
         returning p.id, p.place_id, p.maps_lat, p.maps_lng""",
                  (maquina, sessoes))
        linha = k.fetchone()
    con.commit()
    if not linha:
        return None
    i, pid, la, lo = linha
    return {"poi_id": i, "placeId": pid,
            "lat": float(la) if la is not None else None,
            "lng": float(lo) if lo is not None else None}


def devolver(con, poi_id):
    """Devolve a fila o POI que nao deu certo, para outro tentar."""
    try:
        with con.cursor() as k:
            k.execute("""update radar_comercial.pois
                            set detalhado_em = null, detalhado_por = null
                          where id = %s""", (poi_id,))
        con.commit()
    except Exception:
        con.rollback()


def gravar_um(con, poi_id, d):
    """Grava UM POI, logo depois de colhe-lo.

    Antes a gravacao era toda no fim: uma queda no minuto 10 jogava fora dez
    minutos de coleta. Com duas maquinas isso piora — a que cai leva junto os
    POIs que tinha reservado. Gravando na hora, o pior caso e perder o POI que
    estava na tela.
    """
    if _e_titulo_de_erro(d.get("nome")):
        # PAGINA DE ERRO DO MAPS, nao POI. O `gravar` em lote tinha esta guarda,
        # mas o caminho VIVO e este (grava um a um, em streaming), e ele nao
        # tinha — "Maps sem acesso a Internet" entrou como nome de POI no teste.
        # Nao grava o titulo de erro; devolve o POI para a fila tentar de novo.
        devolver(con, poi_id)
        return 0
    hist = d.get("histograma") or {}
    extra = json.dumps({"histograma_estrelas": hist,
                        "horarios_de_pico": d.get("horariosDePico") or {},
                        "assuntos": d.get("assuntos") or [],
                        "localizado_em": d.get("dentroDe")},
                       ensure_ascii=False)
    with con.cursor() as k:
        # O QUE E DO ESTABELECIMENTO FICA NA `pois`.
        #
        # Nome, categoria, endereco, telefone e site sao verdade sobre o lugar
        # — o Maps foi quem contou desta vez, mas o iFood, a Receita ou o OSM
        # contariam a mesma coisa. Sao os unicos campos gerais que este passo
        # descobre, e por isso os unicos que ele escreve aqui.
        # A ESCRITA DUPLA ACABOU NA MIGRACAO 0052. Ate 03/09/2026 estas seis
        # colunas — plus_code, maps_url, avaliacao, total_avaliacoes,
        # resumo_avaliacoes e status_horario — eram gravadas aqui E em
        # `maps_data`, porque o painel ainda lia da `pois`. O painel passou a
        # ler da view `pois_completo`, as colunas sairam da tabela, e o unico
        # lugar que as recebe agora e o `insert` em `maps_data`, logo abaixo.
        k.execute("""
            update radar_comercial.pois set
                   nome = coalesce(%s, nome),
                   categoria = coalesce(%s, categoria),
                   endereco = coalesce(%s, endereco),
                   telefone = coalesce(%s, telefone),
                   website = coalesce(%s, website),
                   ia_resposta = %s
             where id = %s""",
            (d.get("nome"), d.get("categoria"), d.get("endereco"),
             d.get("telefone"), d.get("site"), extra, poi_id))

        # O QUE E SO DO MAPS VAI PARA `maps_data` (migracao 0048).
        #
        # Plus Code, URL da ficha, nota, quantidade de avaliacoes, resumo da IA
        # do Google e status de horario nao existem fora do Maps. Enquanto
        # moravam na `pois`, cada fonte nova pedia mais uma coluna que nascia
        # nula para os outros 300 mil POIs.
        #
        # A NOTA VIRA NUMERO AQUI. Ela chegava como texto com virgula ('4,5') e
        # ficava assim no banco — sem ordenar e sem somar, porque '10,0' vem
        # antes de '2,0' na ordem alfabetica. `maps_data.avaliacao` e `numeric`,
        # e a conversao acontece neste ponto, uma vez, em vez de em cada
        # consulta que quiser usar o valor.
        #
        # ESCRITA DUPLA, E E TEMPORARIA. As mesmas colunas continuam sendo
        # gravadas na `pois` acima porque o painel e o `server.py` ainda leem de
        # la. A remocao delas e a fase seguinte da refatoracao: primeiro os
        # leitores passam para `maps_data`, so entao as colunas caem. Apagar
        # agora deixaria a ficha do POI sem nota e sem horario.
        _nota = d.get("nota")
        if isinstance(_nota, str):
            _nota = _nota.replace(",", ".").strip() or None
        # `detalhado_em`/`detalhado_por` NAO ESTAO AQUI, e a migracao 0060 diz
        # por que: elas sao a TRAVA DA FILA desta etapa, marcadas la em cima
        # quando a maquina PEGA o POI — antes de o Maps ter dito qualquer coisa.
        # Estado de processo, e nao dado do Maps. Elas moram na `pois`.
        k.execute("""
            insert into radar_comercial.maps_data
                   (poi_id, id_empresa, place_id, maps_url, plus_code,
                    avaliacao, total_avaliacoes, resumo_avaliacoes,
                    status_horario)
            select %s, p.id_empresa,
                   -- SO O PLACE_ID DO GOOGLE ENTRA AQUI. A coluna `pois.place_id`
                   -- carrega duas coisas incompativeis: o id do Google e o
                   -- `estadual:<cluster_id>` da fonte estadual. Copiar sem olhar
                   -- poria id de Overture dentro da tabela do Maps — a mesma
                   -- mentira que `maps_lat` conta hoje. A fila desta etapa filtra
                   -- `fonte='maps'` e por isso nao chega aqui um estadual; a
                   -- guarda existe para quem chamar esta gravacao de outro lugar.
                   case when p.place_id like 'estadual:%%' then null
                        else p.place_id end,
                   %s, %s,
                   nullif(%s::text,'')::numeric, %s, %s, %s
              from radar_comercial.pois p where p.id = %s
            on conflict (poi_id) do update set
                   maps_url          = coalesce(excluded.maps_url, maps_data.maps_url),
                   plus_code         = coalesce(excluded.plus_code, maps_data.plus_code),
                   avaliacao         = excluded.avaliacao,
                   total_avaliacoes  = excluded.total_avaliacoes,
                   resumo_avaliacoes = excluded.resumo_avaliacoes,
                   status_horario    = excluded.status_horario""",
            (poi_id, d.get("url"), d.get("plusCode"), _nota,
             d.get("totalAval"), d.get("resumoIA"), d.get("statusHorario"),
             poi_id))

        k.execute("delete from radar_comercial.comentarios where poi_id=%s",
                  (poi_id,))
        n_com = 0
        for a in (d.get("avaliacoes") or []):
            texto = a.get("texto") or ""
            if a.get("resposta"):
                texto = (texto + "\n\n[RESPOSTA DO PROPRIETARIO] "
                         + a["resposta"]).strip()
            if not texto and a.get("nota") is None:
                continue
            # `fonte` E OBRIGATORIA DESDE A MIGRACAO 0048. A tabela guarda
            # avaliacao de qualquer fonte — Maps hoje, iFood e Airbnb depois —
            # e sem a coluna nao havia como saber de quem era cada linha.
            k.execute("""insert into radar_comercial.comentarios
                           (poi_id, fonte, autor, data, nota, texto)
                         values (%s,'maps',%s,%s,%s,%s)""",
                      (poi_id, a.get("autor"), a.get("quando"),
                       a.get("nota"), texto or None))
            n_com += 1

        k.execute("delete from radar_comercial.horario_funcionamento "
                  "where poi_id=%s", (poi_id,))
        for dia, h in (d.get("horarioSemana") or {}).items():
            k.execute("""insert into radar_comercial.horario_funcionamento
                           (poi_id, dia, horario) values (%s,%s,%s)""",
                      (poi_id, dia, h))

        k.execute("delete from radar_comercial.images_urls where poi_id=%s",
                  (poi_id,))
        for i, u in enumerate(d.get("fotos") or []):
            k.execute("""insert into radar_comercial.images_urls
                           (poi_id, fonte, url, ordem)
                         values (%s,'maps',%s,%s)""",
                      (poi_id, u, i))
    con.commit()
    return n_com


# A `gravar` EM LOTE FOI REMOVIDA EM 04/09/2026.
#
# Ela estava DEFINIDA E NUNCA CHAMADA — o caminho vivo e `gravar_um`, que grava
# em streaming, um POI por vez, e foi para onde os consertos foram desde
# 03/09/2026. A morta ficou para tras carregando dois defeitos:
#
#   · escrevia `maps_url`, `plus_code`, `avaliacao`, `total_avaliacoes`,
#     `resumo_avaliacoes` e `status_horario` na `pois`, colunas que a migracao
#     0052 mudou para `maps_data`. Se alguem a chamasse, morreria com
#     "column does not exist" — que foi exatamente o que aconteceu com a etapa
#     4 por causa de uma irma dela.
#
#   · gravava `"Canoas", "RS"` CHUMBADO como cidade e UF. A colheita teve esse
#     mesmo defeito, corrigido em 03/09/2026 para `municipio_da_area`; esta
#     copia nao foi junto.
#
# Codigo morto que quebra e mente sobre a cidade e pior que nenhum codigo: ele
# passa em revisao por parecer alternativa, e so falha no dia em que alguem o
# liga. O historico esta no git.

# ---------------------------------------------------------------------- main --

async def principal(a):
    # O BANCO PRIMEIRO, E COM O ERRO DE VERDADE.
    #
    # `carregar_area` engole a excecao e devolve None, entao QUALQUER falha de
    # conexao virava "a area nao existe em area_trabalho". Foi o que apareceu
    # quando as duas maquinas subiram juntas e estouraram o limite de conexoes:
    # a mensagem mandou procurar a area, e o problema era outro. Mesmo vicio do
    # `{FALHOU: 566}` de 31/08.
    try:
        _c = bc.conectar()
        with _c.cursor() as _k:
            _k.execute("select 1")
        _c.close()
        # UMA conexao por MAQUINA, nao por worker.
        #
        # A porta 7100 e o Supavisor em modo sessao com `pool_size: 20`. Nao
        # adianta olhar `max_connections` do Postgres (100): o teto que vale e
        # o do pooler, e 20 workers do i9 o consumiam inteiro — o Predator
        # chegava e nao havia vaga.
        #
        # E nao precisa de uma por worker: reservar e gravar levam
        # milissegundos, contra vinte segundos de navegacao. Uma conexao
        # compartilhada com trava atende os 20 sem fila perceptivel.
        print("  banco: ok · 1 conexao para os %d navegadores desta maquina"
              % a.workers)
    except Exception as e:
        print("NAO CONSEGUI FALAR COM O BANCO: %s" % str(e)[:200])
        return 3

    poligono = area_utils.carregar_area(a.area)
    if not poligono:
        print("area '%s' nao existe em area_trabalho — o banco respondeu, "
              "entao e a area mesmo que falta" % a.area)
        return 2
    print("area '%s': %d vertices" % (a.area, len(poligono)))

    pasta = None
    if not a.sem_tiles:
        pasta = "/app/capturas/%s/tiles_z%d" % (a.sessao.split(",")[0], ZOOM)
        os.makedirs(pasta, exist_ok=True)

    pool = ProxyPool()
    pool.start()

    # UMA RODADA, VARIAS SESSOES.
    #
    # A colheita continua gravando numa sessao so — ela e quem cria os POIs, e
    # dois desenhos diferentes nao podem virar a mesma rodada. Ja o DETALHE le
    # de uma fila do banco, e essa fila nao tem por que respeitar a fronteira
    # de sessao: sao place_ids esperando reconsulta, venham de onde vierem.
    SESSOES = [s.strip() for s in a.sessao.split(",") if s.strip()]
    if len(SESSOES) > 1 and not a.sem_colheita:
        print("  varias sessoes so com --sem-colheita: a colheita grava POI "
              "novo, e ele precisa de UMA sessao para ser rastreavel")
        return 2

    t0 = time.time()
    con0 = bc.conectar()
    try:
        if a.refazer:
            with con0.cursor() as k:
                k.execute("""update radar_comercial.pois
                                set detalhado_em = null, detalhado_por = null
                              where fonte = 'maps' and sessao = any(%s)""",
                          (SESSOES,))
                n = k.rowcount
            con0.commit()
            print("  fila reaberta: %d POI(s) voltaram para o comeco" % n)
        with con0.cursor() as k:
            k.execute("""select count(*) from radar_comercial.pois
                          where fonte = 'maps' and sessao = any(%s)
                            and place_id is not null and detalhado_em is null""",
                      (SESSOES,))
            na_fila = k.fetchone()[0]
    finally:
        con0.close()

    async with async_playwright() as pw:
        t_colheita = 0.0
        if a.sem_colheita:
            print("\n⟦A⟧ pulada — %d POI(s) esperando na fila" % na_fila)
        else:
            print("\n⟦A⟧ colheita de placeId (de graca)")
            alvos = await colher(pw, poligono, pasta, a.passo, a.workers,
                                 a.refinar_acima_de)
            t_colheita = time.time() - t0
            if not alvos:
                return 1
            # A colheita ainda entrega em memoria; a fila do banco so existe
            # para POIs ja gravados. Enquanto a fila de placeId nao existir,
            # a colheita grava primeiro e o detalhe consome depois.
            print("  %d placeIds colhidos — gravando o esqueleto para a fila"
                  % len(alvos))
            # A CIDADE VEM DO DESENHO, E NAO E FIXA.
            #
            # Ate 03/09/2026 este insert gravava 'Canoas','RS' HARDCODED — a
            # colheita nasceu para Canoas e ninguem parametrizou. Minerar
            # qualquer outra cidade rotulava TODOS os POIs como Canoas; o teste
            # ponta-a-ponta em Gravatai pegou 132 POIs marcados Canoas. A cidade
            # sai do municipio do proprio desenho, como no resto do pipeline.
            _cid_area, _uf_area = area_utils.municipio_da_area(poligono)
            con0 = bc.conectar()
            try:
                with con0.cursor() as k:
                    for v in alvos.values():
                        k.execute("""
                            insert into radar_comercial.pois
                              (fonte, fonte_dado, nome, place_id, maps_lat,
                               maps_lng, cidade, uf, sessao, coord_fonte,
                               coord_precisao)
                            values ('maps','maps:place_id', %s, %s, %s, %s,
                                    %s, %s, %s, 'maps','porta')
                            on conflict (id_empresa, place_id)
                              where place_id is not null and place_id <> ''
                            do update set detalhado_em = null,
                                          detalhado_por = null""",
                            (v["placeId"], v["placeId"], v["lat"], v["lng"],
                             _cid_area, _uf_area, a.sessao))
                con0.commit()
            finally:
                con0.close()
            na_fila = len(alvos)

        if not na_fila:
            print("  a fila esta vazia — use --refazer para reabri-la")
            return 0

        print("\n⟦B⟧ detalhe: %d navegadores nesta maquina (%s), "
              "cada um num proxy, todos com a MESMA sessao quente"
              % (a.workers, MAQUINA))
        print("  a fila e do BANCO: outras maquinas podem trabalhar junto")
        t1 = time.time()
        trava = asyncio.Lock()

        async def trabalhador(i):
            """Um navegador, muitos POIs. Nunca fecha entre um e outro.

            Cada trabalhador tem a SUA conexao com o banco: ele reserva o
            proximo POI, colhe, grava, e volta para a fila. Duas maquinas
            rodando isto atendem a mesma quadra sem combinarem nada.
            """
            # QUANTOS IPs UM NAVEGADOR QUEIMA ANTES DE DESISTIR DE VEZ.
            #
            # Comecou em 3, e 3 era pouco. Na faixa 10 de Canoas (06/09/2026,
            # 00h30) o Google voltou a punir em massa: os dez navegadores
            # gastaram os tres IPs cada um, os dez desistiram, e a etapa seguiu
            # adiante com 600 dos 712 pontos SEM DETALHE. Trocar "insistir num
            # IP morto para sempre" por "desistir no terceiro" foi trocar um
            # defeito por outro — com 250 proxies na mao, parar no terceiro e
            # jogar fora 247.
            #
            # O TETO CONTINUA EXISTINDO porque desistir precisa ser possivel:
            # se o Maps estiver fora do ar, ou o cookie tiver morrido, nenhum IP
            # vai funcionar e o laco infinito queimaria a lista inteira em
            # castigo de 2 h — deixando as faixas seguintes sem proxy nenhum.
            # Doze cobre uma punicao ampla e para antes de torrar a reserva.
            PROXIES_POR_NAVEGADOR = 12
            # O ORCAMENTO CONTA TROCAS SEGUIDAS SEM RENDER NADA, e nao trocas
            # na vida inteira do navegador.
            #
            # Com `for ... in range(12)` o contador nunca voltava: um navegador
            # que trabalhou bem por quinhentos pontos e depois trocou doze vezes
            # morria, mesmo havendo 135 IPs saudaveis na reserva. Medido no
            # reprocesso da faixa 2 (06/09/2026, 04h26): nove dos dez
            # navegadores desistiram com 638 de 1.225 pontos feitos, e o decimo
            # ficou sozinho arrastando o resto.
            #
            # Trocar de IP e caro, mas nao e fracasso: fracasso e trocar doze
            # vezes SEM colher um ponto entre elas. Um ponto colhido prova que o
            # caminho funciona, e devolve o orcamento inteiro.
            troca_de_ip = 0
            rendeu_algo = False
            while troca_de_ip < PROXIES_POR_NAVEGADOR:
              # PULA QUEM ESTA DE CASTIGO. O indice fixo pegava o proximo da
              # lista sem perguntar; agora anda ate achar um IP fora do
              # descanso, dando no maximo uma volta completa.
              base = (i * 7 + desloca + troca_de_ip * 131) % len(usaveis)
              px = usaveis[base]
              for salto in range(len(usaveis)):
                  cand = usaveis[(base + salto) % len(usaveis)]
                  try:
                      castigado = pool.em_castigo(cand)
                  except Exception:                            # noqa: BLE001
                      castigado = False
                  if not castigado:
                      px = cand
                      break
              desistiu = False
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
                seguidas = 0
                while True:
                    async with db:
                        alvo = await asyncio.to_thread(reservar, conexao,
                                                       SESSOES, MAQUINA)
                    if not alvo:
                        return                      # a fila secou
                    try:
                        d = await detalhar(ctx, alvo)
                        if _pagina_vazia(d):
                            # NAO E FALHA DO POI, E DO IP. Levantar aqui faz o
                            # POI voltar para a fila e o contador de falhas
                            # seguidas subir — que e o caminho que ja existe
                            # para trocar de proxy.
                            raise _ProxyQueimado(
                                "pagina sem nome — IP provavelmente punido")
                        seguidas = 0
                    except Exception as e:
                        # Nao deu certo aqui: volta para a fila e outro tenta.
                        async with db:
                            await asyncio.to_thread(devolver, conexao,
                                                    alvo["poi_id"])
                        seguidas += 1
                        async with trava:
                            feitos.append(None)
                            print("    %3d %s ERRO(%d) %s"
                                  % (len(feitos), MAQUINA[:12], seguidas,
                                     str(e)[:55]))
                        # NAVEGADOR MORTO NAO SE RECUPERA SOZINHO.
                        #
                        # Devolver o POI e tentar o proximo e certo quando a
                        # falha e do POI. Quando e do NAVEGADOR, vira laco
                        # infinito: reserva, falha, devolve, reserva o mesmo de
                        # novo. Aconteceu — 13.798 erros em segundos, queimando
                        # CPU e nao entregando nada.
                        if seguidas >= 3:
                            # O IP VAI DESCANSAR, e nao so sai de cena. Sem
                            # isto ele volta para a rotacao no proximo
                            # trabalhador e queima de novo: o `ProxyPool` tem
                            # castigo de 2 h justamente para isso, e ate hoje
                            # ninguem o chamava a partir daqui.
                            try:
                                await pool.mark_cooldown(px)
                            except Exception:                  # noqa: BLE001
                                pass
                            async with trava:
                                print("    navegador %02d: 3 falhas seguidas "
                                      "no proxy %s — 2 h de castigo, trocando "
                                      "de IP (%d/%d)"
                                      % (i, px["server"][-15:],
                                         troca_de_ip + 1,
                                         PROXIES_POR_NAVEGADOR))
                            desistiu = True
                            break
                        continue
                    async with db:
                        n_com = await asyncio.to_thread(gravar_um, conexao,
                                                        alvo["poi_id"], d)
                    rendeu_algo = True
                    async with trava:
                        feitos.append(d)
                        print("    %3d %-12s %-34s %-20s %3d aval"
                              % (len(feitos), MAQUINA[:12],
                                 (d.get("nome") or "SEM NOME")[:34],
                                 (d.get("categoria") or "-")[:20], n_com))
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
              if not desistiu:
                  return                    # a fila secou, ou o erro nao e de IP
              # PONTO COLHIDO DEVOLVE O ORCAMENTO. Se este navegador entregou
              # algo desde a ultima troca, ele provou que ainda serve.
              if rendeu_algo:
                  troca_de_ip = 0
                  rendeu_algo = False
              else:
                  troca_de_ip += 1
            async with trava:
                print("    navegador %02d desiste: %d proxies seguidos falharam"
                      % (i, PROXIES_POR_NAVEGADOR))
                desistencias.append(i)

        cookie = await garantir_cookie(pw, pool, a.cookie, a.renovar_cookie)

        # Confere o cookie num POI antes de subir os navegadores. Se ele nao
        # serve mais, faz um novo e confere de novo — uma vez so, para um
        # bloqueio de verdade nao virar laco.
        amostra = None
        _c = bc.conectar()
        try:
            with _c.cursor() as _k:
                _k.execute("""select place_id from radar_comercial.pois
                               where fonte = 'maps' and sessao = any(%s)
                                 and place_id is not null limit 1""",
                           (SESSOES,))
                r = _k.fetchone()
                amostra = r[0] if r else None
        finally:
            _c.close()
        if amostra and not await validar_cookie(pw, pool, cookie, amostra):
            print("  o cookie nao serve mais — fazendo um novo")
            cookie = await garantir_cookie(pw, pool, a.cookie, True)
            if not await validar_cookie(pw, pool, cookie, amostra):
                print("  MESMO COM COOKIE NOVO a pagina volta sem nome. "
                      "Nao e o cookie: pode ser bloqueio ou queda do Maps. "
                      "Parando antes de gravar 90 POIs vazios.")
                return 4

        colhidos, feitos, desistencias = [], [], []
        # Uma conexao para a maquina inteira, com trava: o pooler da porta
        # 7100 so aceita 20 sessoes NO TOTAL, entre todas as maquinas.
        conexao = bc.conectar()
        db = asyncio.Lock()
        # Duas maquinas na mesma quadra nao podem cair nos MESMOS proxies. O
        # deslocamento vem do nome da maquina, entao cada uma pega uma faixa
        # diferente do pool sem ninguem coordenar nada.
        #
        # `crc32`, e nao `hash()`: o hash de string em Python e ALEATORIO por
        # processo (PYTHONHASHSEED). Com ele, duas maquinas podiam sortear a
        # mesma faixa, e a mesma maquina mudava de faixa a cada execucao — o
        # oposto do que se queria.
        desloca = (zlib.crc32(MAQUINA.encode("utf-8")) % 97) * 5

        # SO OS PROXIES DO PAIS ATIVO. O pool tem 500 IPs, mas metade e de
        # outro pais e esta reservada — usa-los devolve pagina em branco, e o
        # sintoma vira "SEM NOME · 0 aval", que parece defeito de extracao.
        #
        # Isso passou despercebido enquanto o indice era `(i*7) % 500` com 20
        # workers: nunca passava de 133, sempre dentro da metade boa. Bastou o
        # deslocamento por maquina para cair na metade errada.
        usaveis = [p for p in pool._proxies
                   if not pool.pais or p.get("country") == pool.pais] \
                  or pool._proxies
        print("  proxies utilizaveis: %d de %d (pais %s) · faixa a partir de %d"
              % (len(usaveis), len(pool._proxies), pool.pais or "qualquer",
                 desloca % max(1, len(usaveis))))
        await asyncio.gather(*(trabalhador(i) for i in range(a.workers)))
        # FILA QUE SOBROU E AVISO, e nao silencio. A faixa 10 terminou com 600
        # de 712 pontos sem detalhe e nada no log dizia isso: a etapa seguiu
        # para a proxima como se tivesse acabado.
        if desistencias:
            _sobrou = 0
            try:
                with conexao.cursor() as _k:
                    _k.execute("""select count(*) from radar_comercial.pois
                                   where sessao = any(%s) and detalhado_em is null""",
                               (SESSOES,))
                    _sobrou = _k.fetchone()[0]
            except Exception:                                  # noqa: BLE001
                pass
            print("  ATENCAO: %d navegador(es) desistiram e %d ponto(s) ficaram "
                  "SEM DETALHE. Rode a mesma area de novo para completar."
                  % (len(desistencias), _sobrou))
        conexao.close()
        cresceu = engordar_cookie(a.cookie, colhidos)
        if cresceu:
            print("    cookie engordado: %d → %d cookies" % cresceu)
        registros = [d for d in feitos if d]
        t_detalhe = time.time() - t1

    # A gravacao nao acontece mais aqui: cada POI foi gravado logo depois de
    # colhido, para uma queda no meio nao levar o trabalho junto — e porque com
    # duas maquinas nao ha um "fim" comum onde gravar tudo.
    bons = [r for r in registros if r]
    cobradas = sum(r.get("cobradas", 0) for r in bons)
    print("\n" + "=" * 62)
    print("  esta maquina           : %s" % MAQUINA)
    print("  POIs que ELA detalhou  : %d" % len(bons))
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
    print("  tempo colheita/detalhe : %.1f min / %.1f min"
          % (t_colheita / 60, t_detalhe / 60))
    if bons:
        print("  ritmo                  : %.1f s por POI nesta maquina"
              % (t_detalhe / len(bons)))

    # Quem fez o que, na quadra inteira — e como duas maquinas dividiram.
    try:
        con = bc.conectar()
        with con.cursor() as k:
            k.execute("""select coalesce(detalhado_por, '(pendente)'), count(*)
                           from radar_comercial.pois
                          where fonte = 'maps' and sessao = any(%s)
                          group by 1 order by 2 desc""", (SESSOES,))
            print("  a quadra toda, por maquina:")
            for m, n in k.fetchall():
                print("    %-28s %d" % (m, n))
        con.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default="quadra-canoas-centro")
    p.add_argument("--sessao", default="canoas_quadra",
                   help="uma sessao, ou varias separadas por virgula. Varias "
                        "so com --sem-colheita: e uma fila de detalhe, nao "
                        "uma colheita nova")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--passo", type=int, default=26,
                   help="passo do clique em pixels; o icone de POI tem ~24 px")
    p.add_argument("--refinar-acima-de", type=int, default=3,
                   help="refina a posicao que ainda trouxe N ou mais POIs novos")
    p.add_argument("--limite", type=int, default=0,
                   help="so os N primeiros POIs, para teste")
    p.add_argument("--sem-tiles", action="store_true")
    p.add_argument("--refazer", action="store_true",
                   help="devolve TODOS os POIs desta sessao para a fila, para "
                        "reprocessar do zero")
    p.add_argument("--sem-colheita", action="store_true",
                   help="pula a varredura e consome a fila que ja esta no banco")
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
