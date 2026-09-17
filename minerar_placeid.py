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
</head><body><div id="map"></div><div id="__saida" style="display:none"></div><script>
window.__ids=[];
// O RESULTADO TAMBEM VAI PARA O HTML, e nao so para `window` (17/09/2026).
//
// O Camoufox e um Firefox furtivo, e nele o `evaluate` do Playwright roda num MUNDO ISOLADO: enxerga o DOM da
// pagina, mas nao as variaveis dela. Lendo so `window.__pronto`, a varredura parecia nunca ficar pronta — com o
// mapa desenhado na tela, os tiles baixados e a API carregada. O elemento acima e a ponte, porque o DOM e a unica
// coisa que os dois mundos compartilham. No Chromium nada muda: la os dois caminhos levam ao mesmo lugar.
function _publicar(){
  document.getElementById('__saida').textContent = JSON.stringify(window.__ids);
}
function initMap(){
  const map = new google.maps.Map(document.getElementById('map'), {
    center:{lat:%(lat)s,lng:%(lng)s}, zoom:%(zoom)d, mapId:'%(mapid)s',
    mapTypeId:'roadmap', disableDefaultUI:true, clickableIcons:true });
  map.addListener('click', e => {
    if (e.placeId) {
      window.__ids.push({placeId:e.placeId, lat:e.latLng.lat(), lng:e.latLng.lng()});
      _publicar();
      e.stop();   // sem isto o cartao abre, e o cartao e o que custa
    }
  });
  google.maps.event.addListenerOnce(map,'idle',()=>{
    const b = map.getBounds();
    window.__caixa = {s:b.getSouthWest().lat(), o:b.getSouthWest().lng(),
                      n:b.getNorthEast().lat(), l:b.getNorthEast().lng()};
    const s = document.getElementById('__saida');
    s.dataset.caixa = JSON.stringify(window.__caixa);
    _publicar();
    s.dataset.pronto = '1';
    window.__pronto = true;
  });
}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=%(chave)s&callback=initMap&loading=async" async defer></script>
</body></html>"""


#: A VARREDURA SAI POR PROXY, PELA FROTA (17/09/2026).
#:
#: Medido no mesmo zoom 20, nas mesmas quatro posicoes do centro de Santa Maria, duas vezes cada:
#:
#:     Chromium pelo IP da casa   40 placeIds   4,3 s por posicao
#:     Camoufox pela frota        40 placeIds   6,1 s por posicao
#:
#: Os conjuntos sao IDENTICOS, id por id — nao e "quase o mesmo", e o mesmo. O custo e 1,8 s por posicao, e o que
#: ele compra e o que faltava: as duas maquinas varrendo ao mesmo tempo. Ate aqui i9 e notebook saiam pelo MESMO IP
#: publico, disputavam a cota do Maps e uma atrapalhava a outra; pela frota cada vaga reserva o seu proxy e nenhuma
#: encosta na outra. Quem cuida de castigo, troca de IP e navegador morto e a frota.
#:
#: `MAPS_SEM_PROXY=1` volta ao caminho antigo, que continua inteiro aqui embaixo — a queda para o IP da casa nao
#: depende de codigo novo no dia em que os proxies faltarem.
POR_PROXY = os.environ.get("MAPS_SEM_PROXY") != "1"

#: As tres leituras da pagina do mapa, todas pelo DOM (ver o comentario dentro de `MAPA_HTML`).
PRONTO = "!!document.querySelector('#__saida[data-pronto]')"
CAIXA = "JSON.parse((document.getElementById('__saida').dataset.caixa) || 'null')"
IDS = "JSON.parse(document.getElementById('__saida').textContent || '[]')"


#: PARA QUAL COLUNA VAI O LINK QUE O MAPS CHAMA DE "site".
#:
#: Nos negocios de bairro esse campo quase nunca e um site: e o Instagram, a
#: pagina do Facebook, um `wa.me` ou um Linktree. Medido em 07/09/2026 sobre os
#: 4.249 links colhidos: 902 Instagram, 249 Facebook, 79 WhatsApp, 43 Linktree
#: — 30% do total. Guardar tudo em `website` fazia a coluna `instagram` ficar
#: zerada nos 11.048 POIs de Maps, e quem procurasse rede social ali concluiria
#: que o Maps nao tem, quando tem.
_REDES = (("instagram", ("instagram.com",)),
          ("facebook", ("facebook.com", "fb.com", "fb.me")))


def _separar_link(url):
    """Devolve `(website, instagram, facebook)` — so um deles vem preenchido."""
    if not url:
        return None, None, None
    baixo = str(url).lower()
    for coluna, dominios in _REDES:
        if any(d in baixo for d in dominios):
            return (None, url, None) if coluna == "instagram" else (None, None, url)
    return url, None, None


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
        await pg.wait_for_function(PRONTO, timeout=40000)
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
                caixa = await pg.evaluate(CAIXA)
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
        ids = await pg.evaluate(IDS)
        return {i["placeId"]: i for i in ids}, len(cobradas)
    finally:
        # SO O CONTEXTO. O navegador e da vaga, e serve a proxima posicao.
        await ctx.close()


def varrer_tile_pela_frota(p, lat, lng, passo_px, pasta, rotulo):
    """Uma posicao pela frota: a mesma colheita de `varrer_tile`, com a pagina que a frota entrega.

    OS PORQUES ESTAO NO GEMEO ACIMA e valem os dois: o `commit` no goto em vez de `load`, o prazo proprio do mapa,
    a coordenada no nome do tile, a grade sem espera entre cliques. O que muda aqui e so QUEM abre o navegador —
    a frota entrega a pagina com o IP ja reservado, e o castigo, a troca de IP e o navegador morto sao dela.

    Devolve o mesmo par que o gemeo: os pontos achados e quantas chamadas cobradas sairam.
    """
    page = p.page
    arq = "/tmp/mapa_%d_%s.html" % (os.getpid(), rotulo)
    open(arq, "w", encoding="utf-8").write(
        MAPA_HTML % {"l": LARG, "a": ALT, "lat": lat, "lng": lng,
                     "zoom": ZOOM, "mapid": MAP_ID, "chave": CHAVE})
    cobradas = []

    def contar(r):
        if "places.googleapis.com" in r.url:
            cobradas.append(r.url)

    # O NAVEGADOR DA FROTA SERVE MUITAS POSICOES, entao o ouvinte tem de sair no fim: deixa-lo preso a pagina
    # somaria um ouvinte por posicao e a conta do que foi cobrado cresceria sozinha.
    page.on("request", contar)
    try:
        page.goto("file://" + arq, wait_until="commit")
        page.wait_for_function(PRONTO, timeout=40000)
        page.wait_for_timeout(1600)

        if pasta:
            # O RECORTE EXATO DO MAPA. A janela da frota e maior que o quadro de 1.280x900, e a foto da janela inteira
            # traria margem branca — o tile alimenta o passo dos telhados, e la a margem vira area sem construcao.
            bruto = page.screenshot(clip={"x": 0, "y": 0, "width": LARG, "height": ALT})
            nome = "tile_%s_%.5f_%.5f" % (rotulo, lat, lng)
            try:
                import io
                from PIL import Image
                Image.open(io.BytesIO(bruto)).convert("RGB").save(
                    os.path.join(pasta, nome + ".webp"), "WEBP", quality=90, method=6)
            except Exception:                                  # noqa: BLE001
                open(os.path.join(pasta, nome + ".png"), "wb").write(bruto)
            try:
                _indexar_tile(pasta, nome, lat, lng, page.evaluate(CAIXA))
            except Exception:                                  # noqa: BLE001
                pass

        for x in range(50, LARG - 30, passo_px):
            for y in range(50, ALT - 30, passo_px):
                page.mouse.click(x, y)
        page.wait_for_timeout(1800)
        ids = page.evaluate(IDS) or []
        # POSICAO VAZIA NAO SE MARCA, e a licao custou tres IPs em 17/09/2026. A frota castiga o IP que volta vazio
        # tres vezes seguidas — regra certa para iFood e Airbnb, onde vazio significa recusa. Na varredura do mapa
        # vazio significa QUADRA SEM PONTO, e ha muitas: numa area de 300 m com 40 posicoes, a maioria nao tem um
        # unico estabelecimento. Marcar aqui queimava IP bom por causa de um quarteirao residencial.
        return {i["placeId"]: i for i in ids}, len(cobradas)
    finally:
        try:
            page.remove_listener("request", contar)
        except Exception:                                      # noqa: BLE001
            pass


def celulas_com_ligacao(s, n, o, l, passo_lat, passo_lng):
    """{(i, j)}: as posicoes da grade cujo tile cobre ao menos uma ligacao do cadastro, ou None sem cadastro na caixa.

    A grade anda de meio tile a partir de (s, o), e o tile centrado na posicao cobre meio tile para cada lado: um
    ponto entre duas linhas e duas colunas da grade cai nos quatro tiles vizinhos. Le so a caixa do desenho, pelo
    indice espacial (`ix_corsan_geom`), com a identidade de quem pediu — cada empresa ve o proprio cadastro."""
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""select cod_latitude::float8, cod_longitude::float8
                             from resources_root.cadastro_corsan
                            where geom && st_makeenvelope(%s, %s, %s, %s, 4326)::geography""",
                        (o, s, l, n))
            pontos = cur.fetchall()
    finally:
        con.close()
    if not pontos:
        return None
    cel = set()
    for la, lo in pontos:
        iy, ix = (la - s) / passo_lat, (lo - o) / passo_lng
        for y in {math.floor(iy), math.ceil(iy)}:
            for x in {math.floor(ix), math.ceil(ix)}:
                cel.add((y, x))
    return cel


async def colher(pw, poligono, pasta, passo_px, paralelo, refinar_acima_de, todas_as_posicoes=False):
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

    # SO ONDE HA LIGACAO (dono do produto, 17/09/2026): a cidade inteira de Santa Maria deu 374.619 posicoes e ~95 h
    # de varredura, e 96% delas caiam onde a Corsan nao tem ligacao nenhuma — zona rural. Com o cadastro, a grade fica
    # so com os tiles que cobrem alguma ligacao (14.653, ~3,7 h), e o teste da divisa roda so neles. Sem cadastro na
    # caixa (outra empresa, area sem base), varre tudo como antes; `--todas-as-posicoes` tambem volta ao antigo.
    cel = None if todas_as_posicoes else celulas_com_ligacao(s, n, o, l, passo_lat, passo_lng)
    pos, caixa = [], 0
    if cel is not None:
        ny, nx = int((n + passo_lat - s) / passo_lat), int((l + passo_lng - o) / passo_lng)
        caixa = (ny + 1) * (nx + 1)
        for i, j in sorted(cel):
            if 0 <= i <= ny and 0 <= j <= nx:
                y, x = s + i * passo_lat, o + j * passo_lng
                if toca_o_desenho(y, x):
                    pos.append((y, x))
        print("  so onde ha ligacao do cadastro: %d tile(s) com ligacao na caixa" % len(cel))
    else:
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
    frota = None
    if POR_PROXY:
        from frota_navegacao import Frota
        # UMA VAGA DA FROTA PARA CADA NAVEGADOR QUE A ETAPA JA PEDIA: o paralelismo e o mesmo, muda o IP de cada um.
        frota = Frota("maps_varredura", navegadores=paralelo, paises=("BR",), usar_cookie=False,
                      processo="colheita_maps", log=lambda *a: print("   ", *a, flush=True))
    else:
        for _ in range(paralelo):
            vagas.put_nowait(await pw.chromium.launch(headless=False, args=ARGS))

    # UMA POSICAO QUE FALHA TENTA DE NOVO, uma vez, com navegador novo.
    #
    # Ate 04/09/2026 a posicao que falhava era simplesmente descartada — e
    # posicao descartada e um pedaco da area que nunca foi varrido, sem
    # nenhum aviso na etapa. Uma segunda tentativa custa segundos; o tile
    # perdido custa os POIs que estavam nele.
    TENTATIVAS_POR_TILE = 2

    def registrar(i, marca, lat, lng, ids, cob):
        """O que a posicao rendeu. A conta e a mesma pelos dois caminhos, e roda sempre com a trava na mao."""
        nonlocal cobradas_total
        cobradas_total += cob
        novos = [k for k in ids if k not in achados]
        achados.update(ids)
        print("    %s %03d: %3d na tela, %2d novos → %d"
              % (marca, i, len(ids), len(novos), len(achados)))
        # Adaptativo: so refina onde a varredura ainda esta rendendo.
        if len(novos) >= refinar_acima_de:
            a_refinar.append((lat, lng))

    async def uma(i, lat, lng, marca):
        nonlocal cobradas_total
        if frota is not None:
            # A FROTA JA TENTA DE NOVO por conta propria, em outro navegador e outro IP; aqui so se registra o que
            # voltou. Uma posicao que falhe as tres vezes vira uma linha no log, e nao um pedaco de area perdido em
            # silencio — o mesmo cuidado que o caminho antigo tomava com `TENTATIVAS_POR_TILE`.
            try:
                ids, cob = await asyncio.wrap_future(
                    frota.enviar(varrer_tile_pela_frota, lat, lng, passo_px, pasta, "%s_%03d" % (marca, i)))
            except Exception as e:                             # noqa: BLE001
                async with trava:
                    print("    %s %03d FALHOU: %s" % (marca, i, str(e)[:70]))
                return
            async with trava:
                registrar(i, marca, lat, lng, ids, cob)
            return
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
                registrar(i, marca, lat, lng, ids, cob)
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
    if frota is not None:
        # A FROTA TAMBEM MORRE COM A COLHEITA: fechar devolve os IPs reservados e fecha as sessoes com motivo, para
        # o painel nao mostrar navegador vivo que nao existe mais.
        frota.fechar()
        print("  frota da colheita: %s" % frota.resumo())
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
  // A FOTO VEM EM TAMANHO UTIL, E NAO NO DA MINIATURA.
  //
  // O `src` da grade traz o sufixo do tamanho renderizado — `=w156-h114-p-k-no`
  // — e era ISSO que ficava gravado. Medido em 07/09/2026: 37.321 das 55.638
  // fotos tinham menos de 15 KB, media de 6 KB, cerca de 156x114 px. Nessa
  // resolucao a IA nao le letreiro nem ve mercadoria: a foto existia e nao
  // servia para nada.
  //
  // Trocar o sufixo por `=w1280-h920-p-k-no` entrega a foto grande na mesma
  // URL, de graca. TROCAR, e nao acrescentar: dois sufixos fazem o Google
  // devolver 400.
  const TAMANHO_FOTO = '=w1280-h920-p-k-no';
  const grande = s => /=w\d+-h\d+(-[a-z0-9-]+)?$/.test(s)
                      ? s.replace(/=w\d+-h\d+(-[a-z0-9-]+)?$/, TAMANHO_FOTO)
                      : s + TAMANHO_FOTO;
  // SO A FOTO DO PROPRIO LUGAR (14/09/2026, dono do produto).
  //
  // Ate aqui entrava TODA `<img>` da ficha, e a ficha mostra fotos de OUTROS
  // lugares: "Lugares tambem pesquisados", "Hoteis semelhantes por perto",
  // "Aluguel por temporada na regiao". Sem foto propria, a primeira foto
  // publicada que sobrava era a do vizinho de categoria, e a IA a recebia como
  // "foto publicada no Google" do lugar — a Marmitt Pizzaria foi julgada com a
  // foto da Pizzaria Tommatti's. Medido: 2.060 vereditos de Canoas usaram foto
  // da ficha, 795 com sinal de foto alheia.
  //
  // LISTA DO QUE ENTRA, e nao do que sai. As secoes de sugestao mudam de nome
  // conforme a categoria (hotel tem duas), e uma secao nova que o Google criar
  // amanha entraria calada numa lista de exclusao. Entram tres lugares, todos
  // conferidos na sonda de 14/09/2026:
  //   · a foto de capa — botao "Foto de <nome do lugar>";
  //   · a grade da secao "Fotos e videos" (Tudo, Mais recentes, Exterior...);
  //   · foto anexada a uma avaliacao DESTE lugar (`data-review-id`).
  const nomeLugar = lim((q('h1') || {}).textContent).toLowerCase();
  const cabecalhos = [...document.querySelectorAll('h2')];
  const secaoDe = (no) => {
    let ult = null;
    for (const h of cabecalhos)
      if (h.compareDocumentPosition(no) & Node.DOCUMENT_POSITION_FOLLOWING) ult = lim(h.textContent);
    return ult;
  };
  const origemDaFoto = (img) => {
    let no = img.parentElement;
    for (let i = 0; i < 8 && no; i++) {
      const r = lim(no.getAttribute && no.getAttribute('aria-label'));
      if (/^Foto de /i.test(r))
        return r.slice(8).toLowerCase() === nomeLugar ? 'capa' : null;
      if (no.hasAttribute && no.hasAttribute('data-review-id')) return 'avaliacao';
      no = no.parentElement;
    }
    return /^Fotos?( e v[ií]deos)?$/i.test(secaoDe(img) || '') ? 'fotos' : null;
  };
  const doLugar = [...document.querySelectorAll('img')]
    .filter(i => /googleusercontent|streetviewpixels/.test(i.src))
    .filter(i => !/\/a-?\//.test(i.src) && !/=w\d{1,2}-h\d{1,2}/.test(i.src))
    .map(i => ({src: i.src, secao: origemDaFoto(i)}));
  const fotosSecao = {};
  doLugar.filter(o => o.secao).forEach(o => { fotosSecao[o.src.split('=')[0]] = fotosSecao[o.src.split('=')[0]] || o.secao; });
  const fotosDescartadas = doLugar.filter(o => !o.secao).length;
  const fotos = [...new Set(doLugar.filter(o => o.secao).map(o => o.src))]
    .map(s => /streetviewpixels/.test(s) ? s : grande(s));

  // A DATA DA FOTO, AMARRADA A FOTO — e nao varrida da pagina.
  //
  // A PRIMEIRA VERSAO VARRIA TODO `[aria-label]` da ficha procurando "mes de
  // ano" e devolvia uma lista solta de ate oito datas. Dois defeitos nisso, e
  // o segundo e pior que o primeiro:
  //
  //   · a lista nao dizia de QUAL foto era cada data, entao nem gravando
  //     daria para preencher `images_urls.data_imagem`;
  //   · "out. de 2025" aparece tambem na AVALIACAO do cliente, e o rotulo do
  //     autor tem esse formato. A lista misturava data de foto com data de
  //     comentario e ninguem saberia dizer qual era qual.
  //
  // Agora sobe pelo DOM a partir de CADA IMG ate achar um ancestral cujo
  // rotulo traga a data, e so aceita rotulo que fale de FOTO. O que sobra e
  // atribuivel: `{url, data}`.
  //
  // NAO ACHAR CONTINUA NAO SENDO ERRO. O Maps so escreve a data no
  // visualizador em boa parte das fichas, e abrir cada foto custaria um clique
  // por imagem. A data da foto e a terceira melhor que temos, depois da
  // avaliacao do Google e do panorama do Street View.
  const MES_ANO = /(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*\.?\s+de\s+(\d{4})/i;
  const dataDaImg = (img) => {
    let no = img;
    for (let i = 0; i < 6 && no; i++) {
      const r = (no.getAttribute && no.getAttribute('aria-label')) || '';
      // SO ROTULO QUE FALA DE FOTO. Sem esta condicao o laco sobe ate um
      // ancestral que embrulha a lista de avaliacoes e adota a data do
      // comentario mais recente como se fosse da imagem.
      if (r && /foto|imagem/i.test(r) && MES_ANO.test(r)) {
        const m = r.match(MES_ANO);
        return m ? (m[1] + '/' + m[2]) : null;
      }
      no = no.parentElement;
    }
    return null;
  };
  const fotosComData = [...document.querySelectorAll('img')]
    .filter(i => /googleusercontent|streetviewpixels/.test(i.src))
    .filter(i => !/\/a-?\//.test(i.src) && !/=w\d{1,2}-h\d{1,2}/.test(i.src))
    .filter(i => origemDaFoto(i))
    .map(i => ({ src: i.src, data: dataDaImg(i) }))
    .filter(o => o.data);
  const datasFoto = [...new Set(fotosComData.map(o => o.data))].slice(0, 8);

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
    // O HREF, E NAO O ROTULO.
    //
    // `aria-label` do link mostra o que o Maps EXIBE, que e so o dominio:
    // "instagram.com". O href tem "https://instagram.com/padariadoze". Medido
    // em 07/09/2026: os 4.249 sites ja colhidos estavam TODOS truncados no
    // dominio — 902 diziam apenas "instagram.com", 249 "facebook.com", 79
    // "wa.me". Saber que o negocio tem Instagram sem saber QUAL perfil nao
    // serve para nada: nao da para abrir, nao da para conferir a atividade e
    // nao da para mandar para a IA.
    site     : (() => {
      const a = q('a[data-item-id="authority"]');
      return a ? (a.href || semRotulo(a.getAttribute('aria-label'))) : null;
    })(),
    siteHref : (() => {
      const a = q('a[data-item-id="authority"]');
      return a ? a.href : null;
    })(),
    plusCode : semRotulo(aria('button[data-item-id="oloc"]')),
    dentroDe : txt('button[data-item-id*="locatedin"]'),
    statusHorario: txt('div[jsaction*="openhours"]'),
    horarioSemana: horario,
    horariosDePico: pico,
    fotos    : fotos.slice(0, 60),
    fotosSecao: fotosSecao,
    fotosDescartadas: fotosDescartadas,
    datasFoto: datasFoto,
    // A FOTO COM A DATA DELA, quando o rotulo a trouxe. A url aqui e a
    // MINIATURA, do jeito que estava no DOM; o pareamento com a lista `fotos`
    // — que ja veio com o sufixo grande — e feito do lado do Python, pelo
    // trecho estavel da url.
    fotosComData: fotosComData.slice(0, 60),
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


# ---------------------------------------------- resultados da web e datas --

# A SECAO "RESULTADOS DA WEB" DA FICHA (14/09/2026, dono do produto).
#
# Fica abaixo de "Lugares tambem pesquisados" e traz o que a web diz do lugar:
# o Instagram, o site, o cardapio, com um trecho de texto. E enriquecimento —
# a foto dos lugares sugeridos logo acima NAO entra (ver `origemDaFoto`), mas
# isto entra.
#
# OS CARTOES SO NASCEM NA TELA. O titulo "Resultados da Web" existe no DOM
# desde o carregamento, mas os cartoes sao desenhados quando a secao aparece:
# lida sem rolar, a secao vem vazia. E as vezes o Google responde "Nao foi
# possivel exibir resultados da Web" (visto na Panca Cheia) — isso fica
# registrado como `indisponivel`, para a proxima rodada tentar de novo, e nao
# como "o lugar nao tem nada na web".
RESULTADOS_WEB = r"""() => {
  const lim = s => (s||'').replace(/\s+/g,' ').trim();
  const h = [...document.querySelectorAll('h2')].find(e => /^Resultados da Web$/i.test(lim(e.textContent)));
  if (!h) return {estado: 'sem_secao', cartoes: []};
  const fim = [...document.querySelectorAll('a, button, div')].find(e =>
      (h.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING) && /^Sobre esses dados$/i.test(lim(e.textContent)));
  const dentro = e => (h.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING)
                      && (!fim || (e.compareDocumentPosition(fim) & Node.DOCUMENT_POSITION_FOLLOWING));
  // o texto da secao, do titulo ate "Sobre esses dados", pelo painel inteiro
  const painel = h.closest('[role="main"]') || document.body;
  const tudo = painel.innerText || '';
  const i0 = tudo.indexOf(h.innerText.trim());
  let texto = i0 >= 0 ? tudo.slice(i0 + h.innerText.trim().length) : '';
  const i1 = texto.search(/\n\s*Sobre esses dados/i);
  if (i1 >= 0) texto = texto.slice(0, i1);
  if (/N[ãa]o foi poss[íi]vel exibir resultados da Web/i.test(texto)) return {estado: 'indisponivel', cartoes: []};
  // cada cartao abre com o endereco em migalhas: "https://www.instagram.com › _pancacheia"
  const linhas = texto.split('\n').map(lim).filter(Boolean);
  const cartoes = [];
  for (const l of linhas) {
    if (/^https?:\/\/\S+/.test(l)) cartoes.push({url: l.split(' ')[0], migalha: l.slice(0, 200), titulo: null, trecho: ''});
    else if (cartoes.length) {
      const c = cartoes[cartoes.length - 1];
      if (!c.titulo) c.titulo = l.slice(0, 200);
      else c.trecho = (c.trecho + ' ' + l).trim().slice(0, 600);
    }
  }
  // o link de verdade, quando a ancora existir (o google embrulha em /url?q=)
  const hrefs = [...painel.querySelectorAll('a[href]')].filter(dentro).map(a => {
    const u = a.getAttribute('href') || '';
    const m = u.match(/[?&](?:q|url)=([^&]+)/);
    return m && /google\./.test(u) ? decodeURIComponent(m[1]) : u;
  }).filter(u => /^https?:/.test(u) && !/support\.google|google\.[a-z.]+\/maps/.test(u));
  cartoes.forEach(c => {
    const dom = (c.url.match(/^https?:\/\/([^/\s]+)/) || [])[1];
    const casa = dom && hrefs.find(u => u.includes(dom));
    if (casa) c.url = casa;
  });
  return {estado: cartoes.length ? 'lido' : (texto.trim() ? 'sem_cartao' : 'vazio'), cartoes: cartoes.slice(0, 12)};
}"""

# Quantas fotos da galeria ganham data. Abrir a galeria custa uma troca de foto
# por data (~1 s cada, medido na sonda de 14/09/2026); a IA recebe uma ou duas
# fotos do Maps, e o que importa nelas e ser RECENTE. Por isso a galeria abre
# por "Mais recentes" quando o lugar tem esse botao, e as primeiras bastam.
DATAR_FOTOS = int(os.environ.get("RADAR_DATAR_FOTOS") or 6)

# O rotulo da foto aberta no visualizador: "Foto - nov. de 2021" / "Video - ago. de 2021".
FOTO_ABERTA = r"""() => {
  const lim = s => (s||'').replace(/\s+/g,' ').trim();
  const RE = /^(Foto|V[ií]deo)\s*-\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*\.?\s+de\s+(\d{4})$/i;
  const e = [...document.querySelectorAll('div,span')].find(x => x.children.length === 0 && RE.test(lim(x.textContent)));
  const m = e ? lim(e.textContent).match(RE) : null;
  return {href: location.href, tipo: m ? m[1].toLowerCase() : null, data: m ? (m[2].toLowerCase() + '/' + m[3]) : null};
}"""


def _foto_grande(src):
    """O mesmo `grande()` do JS: troca o sufixo de tamanho, nunca acrescenta dois."""
    import re
    tam = "=w1280-h920-p-k-no"
    if re.search(r"=w\d+-h\d+(-[a-z0-9-]+)?$", src):
        return re.sub(r"=w\d+-h\d+(-[a-z0-9-]+)?$", tam, src)
    return src.split("=")[0] + tam


def _imagem_da_url(href):
    """A foto aberta no visualizador vem na URL da pagina: `!6s<url codificada>`."""
    import re
    from urllib.parse import unquote
    m = re.search(r"!6s(https?[^!]+)", href or "")
    return unquote(m.group(1)) if m else None


CAPA_SRC = r"""() => ((document.querySelector('button[aria-label^="Foto de"] img') || {}).src || '').split('=')[0]"""


async def datar_as_que_faltam(pg, d, limite=10):
    """Abre no visualizador cada foto da ficha que ficou sem data. Devolve [{src, data}].

    `datar_fotos` percorre a galeria a partir de "Mais recentes": as fotos que a
    GRADE da ficha mostra (capa, "Fotos e videos") muitas vezes nao estao entre as
    primeiras dali, e quando a capa e Street View ele nem abre a galeria. Medido na
    recaptura das aprovadas (15/09/2026): 590 fotos do proprio lugar, em 332 POIs,
    gravadas sem data. Aqui cada uma e clicada na propria grade; a data so vale se
    a foto aberta for ELA (mesmo id antes do `=`).
    """
    datadas = {(o.get("src") or "").split("=")[0] for o in (d.get("fotosComData") or []) if o.get("data")}
    faltam = []
    for u in d.get("fotos") or []:
        chave = u.split("=")[0]
        if "googleusercontent" in u and chave not in datadas and chave not in faltam:
            faltam.append(chave)
    achadas = []
    if not faltam:
        return achadas
    try:
        await clicar_aba(pg, r"vis[ãa]o geral|overview")
        await pg.wait_for_timeout(800)
    except Exception:                                          # noqa: BLE001
        pass

    # A CAPA PELO BOTAO, AS OUTRAS PELA COPIA VISIVEL (15/09/2026). A mesma foto aparece
    # 2 ou 3 vezes na pagina, algumas escondidas; `.first` clicava na escondida e o
    # visualizador nao abria — 128 capas e 88 fotos ficaram sem data na recaptura.
    capa = await pg.evaluate(CAPA_SRC)
    capa_primeiro = sorted(faltam[:limite], key=lambda c: c != capa)
    for chave in capa_primeiro:
        try:
            if chave == capa:
                alvo = pg.locator('button[aria-label^="Foto de"]').first
            else:
                alvo = pg.locator('img[src^="%s"]:visible' % chave.replace('"', '\\"')).first
            if await alvo.count() == 0:
                continue
            await alvo.scroll_into_view_if_needed(timeout=3000)
            await alvo.click(timeout=5000)
            f = None
            for _ in range(25):
                f = await pg.evaluate(FOTO_ABERTA)
                img = _imagem_da_url(f["href"])
                if f["data"] and img and img.split("=")[0] == chave:
                    achadas.append({"src": img, "data": f["data"]})
                    break
                await pg.wait_for_timeout(200)
            await pg.keyboard.press("Escape")
            await pg.wait_for_timeout(600)
        except Exception:                                      # noqa: BLE001
            try:
                await pg.keyboard.press("Escape")
            except Exception:                                  # noqa: BLE001
                pass
    return achadas


def _data_da_miniatura_street_view(url):
    """"out/2024" do panorama da miniatura de Street View da ficha, pela API de metadados (gratis)."""
    import re
    m = re.search(r"panoid=([^&]+)", url or "")
    if not m:
        return None
    try:
        from capturar_evidencia import _data_do_pano
        aaaamm = _data_do_pano(m.group(1))
    except Exception:                                          # noqa: BLE001
        return None
    if not aaaamm or len(aaaamm) < 7:
        return None
    meses = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    return "%s/%s" % (meses[int(aaaamm[5:7]) - 1], aaaamm[:4])


async def ler_resultados_web(pg):
    """Leva a secao "Resultados da Web" ate a tela e le os cartoes. Nunca levanta."""
    # ROLAR O PAINEL AOS POUCOS, e nao pular direto para o titulo. As secoes de
    # baixo tambem chegam depois do cabecalho: lida logo apos o `h1`, a ficha
    # ainda nao tinha o titulo (Panca Cheia: "sem_secao" num teste, secao
    # presente na sonda). E o `scrollIntoView` sozinho deixou os cartoes da
    # Marmitt sem desenhar; a rolagem de 700 px por passo da sonda os desenhou.
    rolar = ("() => { const m = document.querySelector('div[role=\"main\"]');"
             " const s = m && [m, ...m.querySelectorAll('div')]"
             ".find(x => x.scrollHeight > x.clientHeight + 50); if (s) s.scrollTop += 700; }")
    try:
        r = {"estado": "sem_secao", "cartoes": []}
        for _ in range(16):
            await pg.evaluate(rolar)
            await pg.wait_for_timeout(500)
            r = await pg.evaluate(RESULTADOS_WEB)
            if r.get("estado") in ("lido", "indisponivel"):
                break
        if r.get("estado") in ("vazio", "sem_cartao"):
            # o titulo esta na tela e os cartoes nao: a resposta da web ainda vem
            for _ in range(6):
                await pg.wait_for_timeout(700)
                r = await pg.evaluate(RESULTADOS_WEB)
                if r.get("estado") in ("lido", "indisponivel"):
                    break
        return r
    except Exception as e:                                     # noqa: BLE001
        return {"estado": "falhou", "erro": str(e)[:120], "cartoes": []}


async def datar_fotos(pg, limite=DATAR_FOTOS):
    """Abre a galeria e le a data das primeiras fotos. Devolve [{src, data, recente}].

    A data so existe no visualizador — a grade da ficha nao a escreve (0 de
    71.547 fotos com data ate 14/09/2026). Cada foto aberta poe a URL da imagem
    no endereco da pagina (`!6s...`), e e por ela que a data casa com a foto:
    pelo id antes do `=`, o mesmo pareamento de `fotosComData`.

    "Mais recentes" primeiro: e a ordem que interessa ao veredito. Sem esse
    botao (lugar com poucas fotos), a capa. Street View no meio da galeria nao
    conta — ele ja tem data propria, da API de metadados.
    """
    saida, vistos = [], set()
    try:
        # `count()` nao espera: logo depois de voltar a visao geral a grade
        # ainda nao existe, e o hotel do teste caiu na capa com "Mais recentes"
        # na pagina. Espera-se a capa, que vem antes da grade.
        try:
            await pg.wait_for_selector('button[aria-label^="Foto de"]', timeout=6000)
            await pg.wait_for_timeout(500)
        except Exception:                                      # noqa: BLE001
            pass
        botao = pg.locator('button[aria-label^="Mais recentes"]')
        recentes = await botao.count() > 0
        if not recentes:
            botao = pg.locator('button[aria-label^="Foto de"]')
            if await botao.count() == 0:
                return saida
            # CAPA DE STREET VIEW = LUGAR SEM FOTO PUBLICADA. A galeria abriria o
            # panorama, e cada seta giraria a camera sem nunca dar uma data
            # (55 s na Marmitt Pizzaria no teste de 14/09/2026).
            capa = await botao.first.evaluate("b => (b.querySelector('img') || {}).src || ''")
            if "streetviewpixels" in capa:
                return saida
        await botao.first.click(timeout=8000)
        anterior, sem_data = None, 0
        for _ in range(limite * 2):
            # espera a foto trocar pelo endereco, e nao por relogio
            for _ in range(20):
                f = await pg.evaluate(FOTO_ABERTA)
                if f["href"] != anterior and (f["data"] or "streetview" in f["href"]):
                    break
                await pg.wait_for_timeout(200)
            anterior = f["href"]
            img = _imagem_da_url(f["href"])
            if img and "googleusercontent" in img and f["data"]:
                chave = img.split("=")[0]
                if chave in vistos:
                    break                                  # deu a volta
                vistos.add(chave)
                saida.append({"src": img, "data": f["data"], "tipo": f["tipo"],
                              "recente": recentes})
                sem_data = 0
                if len(saida) >= limite:
                    break
            else:
                # duas seguidas sem data: acabaram as fotos e comecou o Street View
                sem_data += 1
                if sem_data >= 2:
                    break
            await pg.keyboard.press("ArrowRight")
        await pg.keyboard.press("Escape")
    except Exception:                                          # noqa: BLE001
        pass
    return saida


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

    # OUTRO IP ANTES DE DESISTIR.
    #
    # Antes de 06/09/2026 so UM cookie era aquecido por rodada, e um proxy
    # ruim aqui custava uma tentativa. Com um cookie por navegador sao seis ou
    # dez aquecimentos, e a chance de pelo menos um cair num IP morto vira
    # quase certeza. Medido no notebook no mesmo dia: um
    # `ERR_TUNNEL_CONNECTION_FAILED` num dos seis derrubou a RODADA INTEIRA,
    # porque `asyncio.gather` propaga a primeira excecao.
    #
    # A resposta e a mesma que o detalhe ja usa: o IP falhou, pega outro.
    ultimo_erro = None
    for tentativa in range(TENTATIVAS_DE_AQUECIMENTO):
        px = bons[random.randrange(len(bons))]
        try:
            return await _aquecer(pw, px, caminho)
        except Exception as e:                                 # noqa: BLE001
            ultimo_erro = e
            print("  aquecimento de %s falhou pelo %s (%d/%d): %s"
                  % (os.path.basename(caminho), px["server"][-15:],
                     tentativa + 1, TENTATIVAS_DE_AQUECIMENTO, str(e)[:60]))
    raise ultimo_erro


#: Quantos IPs o aquecimento de um cookie queima antes de desistir.
TENTATIVAS_DE_AQUECIMENTO = 4


async def _aquecer(pw, px, caminho):
    """Abre o Maps por ESTE proxy, aceita o consentimento e salva o estado."""
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

        # "Resultados da Web" ANTES das avaliacoes: a secao mora na visao
        # geral, e a aba de avaliacoes a tira da pagina.
        d["resultadosWeb"] = await ler_resultados_web(pg)

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

        # A DATA DAS FOTOS, POR ULTIMO: abrir a galeria troca a pagina, e o que
        # vem antes (avaliacoes) nao pode depender de voltar dela. Volta-se a
        # visao geral, onde moram a capa e o botao "Mais recentes".
        if DATAR_FOTOS > 0:
            try:
                await clicar_aba(pg, r"vis[ãa]o geral|overview")
                await pg.wait_for_timeout(800)
                datadas = await datar_fotos(pg)
            except Exception:                                  # noqa: BLE001
                datadas = []
            if datadas:
                d.setdefault("fotosComData", []).extend(
                    {"src": o["src"], "data": o["data"]} for o in datadas)
                secao = d.setdefault("fotosSecao", {})
                ja = {u.split("=")[0] for u in (d.get("fotos") or [])}
                # AS DATADAS VAO NA FRENTE. Sao as que o veredito deve ver
                # primeiro — e as de "Mais recentes" podem nem estar na grade.
                novas = []
                for o in datadas:
                    chave = o["src"].split("=")[0]
                    secao.setdefault(chave, "recentes" if o["recente"] else "galeria")
                    if chave not in ja:
                        novas.append(_foto_grande(o["src"]))
                        ja.add(chave)
                d["fotos"] = (novas + list(d.get("fotos") or []))[:60]
            # AS QUE FICARAM SEM DATA, uma a uma pela grade (15/09/2026), e a
            # miniatura de Street View pelo panorama.
            try:
                faltas = await datar_as_que_faltam(pg, d)
            except Exception:                                  # noqa: BLE001
                faltas = []
            for u in d.get("fotos") or []:
                if "streetviewpixels" in u:
                    dt = await asyncio.to_thread(_data_da_miniatura_street_view, u)
                    if dt:
                        faltas.append({"src": u, "data": dt})
            if faltas:
                d.setdefault("fotosComData", []).extend(faltas)
            d["fotosDatadas"] = len(datadas) + len(faltas)

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


_UM_POI_NAO_DERRUBA = True


#: "out/2025" -> date(2025, 10, 1). O Maps nao publica o dia da foto, so o mes.
_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def _mes_ano(s):
    """A data que o Maps escreve, virando `date`. Sem dia, o primeiro do mes.

    NAO INVENTAR PRECISAO: o Maps diz "out. de 2025", e o dia 1 e uma
    convencao para caber na coluna `date` — quem usa a data compara ANO e MES,
    nunca o dia. Isso esta dito aqui porque a coluna nao consegue dize-lo.
    """
    if not s:
        return None
    try:
        import datetime
        m, a = str(s).split("/")
        mes = _MESES.get(m.strip().lower()[:3])
        return datetime.date(int(a), mes, 1) if mes else None
    except Exception:                                          # noqa: BLE001
        return None


def gravar_um(con, poi_id, d):
    """Grava UM POI. Falha de UMA linha nao derruba os outros navegadores.

    O DEFEITO, medido em 06/09/2026 no redetalhamento de Canoas. A conexao com
    o banco e UMA para os dez navegadores desta maquina — o log diz isso na
    partida: "1 conexao para os 10 navegadores". Quando o navegador 00 bateu
    numa chave duplicada, a transacao ficou abortada, e os nove seguintes
    morreram na escrita seguinte com "current transaction is aborted". A
    rodada parou aos 42,9 min com 1.722 de 6.696 feitos, e nada no log dizia
    que a causa fora uma linha so.

    Duas coisas consertam isso, e as duas moram aqui:

    1. ROLLBACK SEMPRE, em qualquer erro. Sem ele a conexao compartilhada fica
       envenenada e o proximo a escrever paga pelo erro do anterior.
    2. DUPLICATA NAO E ERRO, E DESCOBERTA. `pois_sem_duplicata` guarda
       (empresa, nome, endereco): bater nela significa que a ficha que o Maps
       acabou de entregar JA EXISTE na base, vinda de outra fonte. O esqueleto
       vira fundido — sai da fila e para de ser tentado — em vez de virar
       excecao.
    """
    try:
        return _gravar_um_cru(con, poi_id, d)
    except Exception as e:                                     # noqa: BLE001
        try:
            con.rollback()
        except Exception:                                      # noqa: BLE001
            pass
        if "pois_sem_duplicata" in str(e):
            _marcar_fundido(con, poi_id, d)
            return 0
        # QUALQUER OUTRO ERRO tambem para aqui, e de proposito: a conexao ja
        # esta limpa, e devolver 0 deixa este navegador seguir para o proximo
        # POI. O que nao pode acontecer e um POI estranho custar a rodada
        # inteira. O ponto fica com `detalhado_em` marcado e sem ficha; a
        # medicao de pendencia (nome = place_id) o encontra de novo.
        print("    poi %s nao gravou: %s" % (poi_id, str(e)[:100]))
        return 0


def _marcar_fundido(con, poi_id, d):
    """O esqueleto e duplicata de um POI que ja existe: sai da fila.

    `fundido_em` e o que a base ja usa para "este ponto foi absorvido" — o
    mesmo marcador do `cruzar_fontes`. Sem isto o POI voltaria para a fila a
    cada rodada e bateria na mesma chave para sempre.
    """
    try:
        with con.cursor() as k:
            k.execute("""update radar_comercial.pois
                            set fundido_em = now(),
                                revisar_motivo = coalesce(revisar_motivo, %s)
                          where id = %s""",
                      ("duplicata pelo nome+endereco do Maps: %s"
                       % (d.get("nome") or "")[:80], poi_id))
        con.commit()
        print("    poi %s e duplicata de outro ja na base — fundido" % poi_id)
    except Exception:                                          # noqa: BLE001
        try:
            con.rollback()
        except Exception:                                      # noqa: BLE001
            pass


def _gravar_um_cru(con, poi_id, d):
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
                   website  = coalesce(%s, website),
                   instagram = coalesce(%s, instagram),
                   facebook  = coalesce(%s, facebook),
                   ia_resposta = %s
             where id = %s""",
            (d.get("nome"), d.get("categoria"), d.get("endereco"),
             d.get("telefone"), *_separar_link(d.get("site")),
             extra, poi_id))

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
        # OS "RESULTADOS DA WEB" DA FICHA (migracao 0111) ficam aqui, e nao nas
        # colunas `instagram`/`website` da `pois`: a secao lista o que a web
        # ACHOU perto do nome — na Panca Cheia veio uma imobiliaria "perto de
        # Panca Cheia" junto do Instagram certo. E prova para ler, nao cadastro.
        # `indisponivel` (o Google nao exibiu) nao apaga o que uma rodada
        # anterior leu.
        _web = d.get("resultadosWeb") or {}
        _web_estado = _web.get("estado")
        _web_cartoes = (json.dumps(_web.get("cartoes") or [], ensure_ascii=False)
                        if _web_estado in ("lido", "sem_cartao", "vazio", "sem_secao") else None)
        k.execute("""
            insert into radar_comercial.maps_data
                   (poi_id, id_empresa, place_id, maps_url, plus_code,
                    avaliacao, total_avaliacoes, resumo_avaliacoes,
                    status_horario, resultados_web, resultados_web_estado,
                    resultados_web_em)
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
                   nullif(%s::text,'')::numeric, %s, %s, %s,
                   %s::jsonb, %s, case when %s::text is null then null else now() end
              from radar_comercial.pois p where p.id = %s
            on conflict (poi_id) do update set
                   maps_url          = coalesce(excluded.maps_url, maps_data.maps_url),
                   plus_code         = coalesce(excluded.plus_code, maps_data.plus_code),
                   avaliacao         = excluded.avaliacao,
                   total_avaliacoes  = excluded.total_avaliacoes,
                   resumo_avaliacoes = excluded.resumo_avaliacoes,
                   status_horario    = excluded.status_horario,
                   resultados_web    = coalesce(excluded.resultados_web, maps_data.resultados_web),
                   resultados_web_estado = coalesce(excluded.resultados_web_estado, maps_data.resultados_web_estado),
                   resultados_web_em = coalesce(excluded.resultados_web_em, maps_data.resultados_web_em)""",
            (poi_id, d.get("url"), d.get("plusCode"), _nota,
             d.get("totalAval"), d.get("resumoIA"), d.get("statusHorario"),
             _web_cartoes, _web_estado, _web_cartoes,
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
        # A DATA CASA COM A FOTO PELO ID DA URL, e nao pela posicao.
        #
        # `fotos` sai do JS ja com o sufixo de tamanho trocado para
        # `=w1280-h920-p-k-no`; `fotosComData` traz a url como estava no DOM,
        # com o sufixo da miniatura. Comparar as duas inteiras nunca casaria.
        # O que nao muda e o trecho antes do `=`: e o identificador da imagem
        # no googleusercontent.
        por_id = {}
        for o in (d.get("fotosComData") or []):
            src = (o or {}).get("src") or ""
            if src and o.get("data"):
                por_id[src.split("=")[0]] = o["data"]
        # `secao` (migracao 0111): de onde da ficha a foto veio — capa, fotos,
        # avaliacao, recentes, galeria. Foto sem secao nao chega mais aqui.
        secoes = d.get("fotosSecao") or {}
        # UMA LINHA POR IMAGEM. A capa aparece na ficha em dois tamanhos, e o
        # `Set` do JS compara a url antes da troca de sufixo: a mesma foto vinha
        # duas vezes (Panca Cheia, ordem 0 e 2).
        unicas, _vistas = [], set()
        for u in (d.get("fotos") or []):
            if u.split("=")[0] not in _vistas:
                _vistas.add(u.split("=")[0])
                unicas.append(u)
        for i, u in enumerate(unicas):
            k.execute("""insert into radar_comercial.images_urls
                           (poi_id, fonte, url, ordem, data_imagem, secao)
                         values (%s,'maps',%s,%s,%s,%s)""",
                      (poi_id, u, i, _mes_ano(por_id.get(u.split("=")[0])),
                       secoes.get(u.split("=")[0])))
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
                                 a.refinar_acima_de, a.todas_as_posicoes)
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
            # QUANTO TEMPO A RESERVA VALE. Generosa de proposito: um
            # navegador saudavel fica horas no mesmo IP, e renovar de minuto em
            # minuto seria trafego a toa. Se o processo morrer, o pior caso e
            # este IP ficar de molho ate o prazo vencer — barato perto de dois
            # navegadores no mesmo IP.
            RESERVA_S = 3600
            troca_de_ip = 0
            rendeu_algo = False
            while troca_de_ip < PROXIES_POR_NAVEGADOR:
              # PULA QUEM ESTA DE CASTIGO OU JA E DE OUTRO, E TOMA O QUE SOBROU.
              #
              # O indice fixo pegava o proximo da lista sem perguntar. Duas
              # rodadas com a mesma formula caiam no MESMO IP ao mesmo tempo —
              # e como o `_in_use` vivia na memoria de cada processo, nenhuma
              # das duas sabia. Agora a escolha consulta o estado compartilhado
              # (migração 0071) e RESERVA o que escolheu: quem chegar depois ve
              # a reserva e anda para o proximo.
              #
              # A reserva tem prazo e e renovada implicitamente a cada troca;
              # processo morto solta o IP sozinho quando o prazo vence.
              base = (i * 7 + desloca + troca_de_ip * 131) % len(usaveis)
              px = usaveis[base]
              for salto in range(len(usaveis)):
                  cand = usaveis[(base + salto) % len(usaveis)]
                  try:
                      if pool.em_castigo(cand) or pool.de_outro_dono(cand):
                          continue
                      if not pool.reservar(cand, segundos=RESERVA_S,
                                           sufixo="nav%02d" % i):
                          continue          # outro fechou entre a pergunta e a tomada
                  except Exception:                            # noqa: BLE001
                      pass
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
                    timezone_id="America/Sao_Paulo",
                    storage_state=cookies[i] if i < len(cookies) else cookie)
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
                        _e = await ctx.storage_state()
                        colhidos.append(_e)
                        if i < len(colhidos_por):
                            colhidos_por[i].append(_e)
                    except Exception:
                        pass
                    # A LIMPEZA NAO DERRUBA A RODADA.
                    #
                    # `nav.close()` estava nu num `finally`, fora do `except`
                    # que protege o corpo — entao um erro AQUI escapava do
                    # trabalhador, subia pelo `gather` e matava as dez.
                    # Aconteceu em 06/09/2026, com 1.523 POIs ainda na fila:
                    # "Browser.close: Connection closed while reading from the
                    # driver". Nao foi memoria (1,75 GB de 24 GB) nem punicao
                    # (0,6% de erro) — o driver do Playwright simplesmente
                    # morreu, e fechar um navegador ja morto virou o fim de
                    # tudo.
                    #
                    # Fechar e educacao com o sistema operacional, nao
                    # trabalho: se falhar, o processo termina e o sistema
                    # recolhe. Nada disso vale uma rodada.
                    try:
                        await nav.close()
                    except Exception as _e_fechar:              # noqa: BLE001
                        async with trava:
                            print("    navegador %02d nao fechou limpo: %s"
                                  % (i, str(_e_fechar)[:70]))
              # O IP VOLTA PARA A PRATELEIRA assim que este navegador larga
              # dele — por sair ou por trocar. Sem isto ele ficaria reservado
              # ate o prazo vencer, e uma rodada de dez navegadores tiraria de
              # circulacao dez IPs bons por vinte minutos a cada troca.
              try:
                  pool.soltar(px, sufixo="nav%02d" % i)
              except Exception:                                # noqa: BLE001
                  pass
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

        # UM COOKIE POR NAVEGADOR, e nao um para todos.
        #
        # Ate 06/09/2026 os dez navegadores recebiam `storage_state=cookie` — o
        # MESMO arquivo. Cada um saia por um IP proprio, e a rotacao de IP era
        # tratada como se isolasse um do outro. Nao isolava: o cookie E a
        # identidade. Uma sessao do Google aparecendo de dez enderecos ao mesmo
        # tempo e um sinal mais forte que dez enderecos aparecendo uma vez cada.
        #
        # O CUSTO E REAL E VALE DIZER: cada arquivo comeca frio. Nao da para
        # semear os dez a partir do cookie quente que ja existe — seria
        # justamente clonar a identidade que estamos separando. O aquecimento
        # dos dez acontece em paralelo, uma vez, e dali em diante cada um
        # engorda com a propria navegacao.
        #
        # `--cookie-unico` volta ao comportamento antigo, para comparar.
        if a.cookie_unico:
            cookies = [cookie] * a.workers
            print("  cookie UNICO para os %d navegadores (--cookie-unico)"
                  % a.workers)
        else:
            # `return_exceptions=True` E O PONTO. Sem ele a primeira falha
            # cancela as outras e mata a rodada — foi o que aconteceu no
            # notebook em 06/09/2026. Quem nao conseguiu cookie proprio cai
            # para o compartilhado: pior isolamento naquele navegador, e nao
            # rodada nenhuma.
            _r = await asyncio.gather(*[
                garantir_cookie(pw, pool, "%s.nav%02d" % (a.cookie, w),
                                a.renovar_cookie)
                for w in range(a.workers)], return_exceptions=True)
            cookies = [cookie if isinstance(x, BaseException) else x
                       for x in _r]
            _caiu = sum(1 for x in _r if isinstance(x, BaseException))
            print("  %d cookies, um por navegador — identidades separadas%s"
                  % (len(set(cookies)),
                     ("  (%d nao aqueceram e usam o compartilhado)" % _caiu)
                     if _caiu else ""))

        colhidos, feitos, desistencias = [], [], []
        # O que cada navegador trouxe volta para o SEU arquivo. Numa lista so,
        # `engordar_cookie` misturaria as dez identidades de volta numa.
        colhidos_por = [[] for _ in range(a.workers)]
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
        # UM TRABALHADOR QUE MORRE NAO LEVA OS OUTROS. Mesma razao do
        # aquecimento dos cookies: sem `return_exceptions`, a primeira
        # excecao cancela as nove tarefas irmas no meio do POI delas. A fila e
        # do banco e sobrevive — o que nao sobrevive e o tempo ja gasto.
        _res = await asyncio.gather(*(trabalhador(i) for i in range(a.workers)),
                                    return_exceptions=True)
        for _i, _x in enumerate(_res):
            if isinstance(_x, BaseException):
                print("    navegador %02d morreu: %s: %s"
                      % (_i, type(_x).__name__, str(_x)[:90]))
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
        if a.cookie_unico:
            cresceu = engordar_cookie(a.cookie, colhidos)
            if cresceu:
                print("    cookie engordado: %d → %d cookies" % cresceu)
        else:
            _n = 0
            for _w, _cam in enumerate(cookies):
                if _w < len(colhidos_por) and colhidos_por[_w]:
                    if engordar_cookie(_cam, colhidos_por[_w]):
                        _n += 1
            print("    %d de %d cookies engordaram com a propria navegacao"
                  % (_n, len(cookies)))
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
    p.add_argument("--cookie-unico", action="store_true",
                   help="volta ao comportamento anterior a 06/09/2026: um "
                        "cookie compartilhado por todos os navegadores. So "
                        "para comparar — a mesma identidade em N IPs e o "
                        "sinal que a rotacao de IP tentava evitar")
    p.add_argument("--renovar-cookie", action="store_true",
                   help="descarta o cookie e faz um novo. NAO acontece "
                        "automaticamente ao fim da execucao")
    p.add_argument("--intervalo-min", type=float, default=1.5,
                   help="espera minima entre POIs do MESMO navegador")
    p.add_argument("--intervalo-max", type=float, default=5.0)
    p.add_argument("--empresa", default="")
    p.add_argument("--todas-as-posicoes", action="store_true",
                   help="varre a grade inteira do desenho, mesmo onde o cadastro nao tem ligacao")
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()
    sys.exit(asyncio.run(principal(a)))
