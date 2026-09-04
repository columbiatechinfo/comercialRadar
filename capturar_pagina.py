# -*- coding: utf-8 -*-
"""capturar_pagina.py — a página do anúncio como evidência, para iFood e Airbnb.

POR QUE SÓ O AIRBNB

    O Airbnb não publica o endereço exato. Mirar a fachada a partir de uma
    coordenada aproximada seria julgar o vizinho. O que prova atividade num
    anúncio de hospedagem é a própria página: o mapa, os comentários recentes
    e o calendário de datas disponíveis.

O IFOOD FICOU DE FORA, e a razão é medida, não preferência. A página da loja
(`ifood.com.br/delivery/<cidade>/<slug>`) responde 200 e então cobre o cardápio
com o desafio "Pressione e segure para confirmar que você é um humano" do
PerimeterX — capturado em 04/09/2026 no POI 84666: 860×3930 px de esqueleto
cinza atrás do modal. É um teste anti-robô, e este projeto não constrói
solucionador de teste anti-robô.

Não é perda: a prova de atividade da loja já existe, em dado e de graça, no
`ifood_merchant` que o `detalhar_ifood.py` preenche pelo endpoint `/extra` —
`bruto->>'disponivel'`, nota, número de avaliações, CNPJ, telefone e
`visto_em`. É mais forte que um print do cardápio, porque diz QUANDO foi visto.
Quem leva isso à IA é o avaliador, como texto. E a loja do iFood continua indo
ao Street View pelo `capturar_evidencia.py`, porque coordenada exata ela tem —
1.948 das 1.960 com cinco casas decimais.

O PRINT É DO LEIAUTE MÓVEL, e isso é decisão de orçamento de pixels, não
estética. A página de um anúncio no desktop tem 1280 px de largura e passa dos
8.000 de altura: 10 milhões de pixels que o servidor de visão reduz até o texto
virar borrão. A mesma página em 430 px de largura empilha tudo numa coluna,
cabe em ~1,3 milhão de pixels e o texto fica no tamanho em que foi desenhado
para ser lido.

O CONTEÚDO PRECISA SER ROLADO ANTES. Mapa, comentários e calendário entram por
carregamento preguiçoso: `full_page=True` sem rolar fotografa retângulos
vazios onde deviam estar justamente as três provas.

Uso:
    python capturar_pagina.py --area area_atual --limite 20 --aplicar
    python capturar_pagina.py --fonte airbnb --aplicar
"""
from __future__ import annotations

import argparse
import random
import sys
import threading
import time

import area_utils
import base_comum as bc

# A JANELA DO TELEFONE. 430×930 é a de um aparelho grande atual — larga o
# bastante para o Airbnb não cair no leiaute de relógio, estreita o bastante
# para empilhar.
LARG, ALT = 430, 930

# TETO DE ALTURA DO PRINT, em pixels de página (isto é, depois de voltar a
# 1× — ver `_ajustar`). Anúncio não tem fim: rodapé, "outras opções", "explore
# outros lugares". O que interessa está no topo, e 5.200 px a 430 de largura
# são 2,2 milhões de pixels — cabe no orçamento do servidor de visão sem que
# ele precise reduzir nada.
ALT_MAX = 5200

PASSOS_ROLAGEM = 14                # quantas telas descer para acordar o lazy
PAUSA_ROLAGEM_MS = 700

LOTE_MIN, LOTE_MAX = 6, 12         # a mesma regra de lote do detalhamento

TIPO_POR_FONTE = {"airbnb": "pagina_airbnb"}


def _log(m):
    print(m, flush=True)


# ── quem entra ─────────────────────────────────────────────────────────────
#
# A FILA É A MESMA DA EVIDÊNCIA DE RUA, e de propósito: categoria marcada para
# a IA, vínculo com ligação RESIDENCIAL ativa. Um POI de iFood que não está na
# fila da IA não precisa de print — ninguém vai olhar para ele.
SQL_ALVO = """
    select distinct on (p.id)
           p.id, p.fonte, coalesce(p.nome,''), pl.url
      from radar_comercial.pois p
      join radar_comercial.ligacao_poi lp on lp.poi_id = p.id
      join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
      join radar_comercial.categoria_catalogo cc
            on cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
      join radar_comercial.poi_link pl
            on pl.poi_id = p.id and pl.fonte = p.fonte and pl.ativo
     where p.fonte = any(%s)
       and upper(l.categoria) = 'RESIDENCIAL'
       and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
       and cc.avaliar
       and not exists (select 1 from radar_comercial.poi_evidencia e
                        where e.poi_id = p.id
                          and e.tipo = 'pagina_' || p.fonte
                          and e.dados is not null)
     order by p.id, pl.visto_em desc nulls last
"""


# A MESMA DERIVAÇÃO, SEM A FILA — para reconferir um POI apontado na tela.
SQL_POR_ID = """
    select distinct on (p.id)
           p.id, p.fonte, coalesce(p.nome,''), pl.url
      from radar_comercial.pois p
      join radar_comercial.poi_link pl
            on pl.poi_id = p.id and pl.fonte = p.fonte and pl.ativo
     where p.id = any(%s)
     order by p.id, pl.visto_em desc nulls last
"""


def alvos(con, fontes, poligono, limite, pois=None):
    cur = con.cursor()
    if pois:
        cur.execute(SQL_POR_ID, (list(pois),))
    else:
        cur.execute(SQL_ALVO, (list(fontes),))
    linhas = cur.fetchall()
    # A ÁREA FILTRA DEPOIS, e não no SQL: `pt_geo` pode ser nula num anúncio de
    # hospedagem sem coordenada, e um `and` no SQL o eliminaria em silêncio.
    # Aqui a ausência de coordenada é decisão explícita — entra.
    saida, fora = [], 0
    if poligono:
        cur.execute("""select p.id, st_y(p.pt_geo::geometry),
                              st_x(p.pt_geo::geometry)
                         from radar_comercial.pois p
                        where p.id = any(%s) and p.pt_geo is not null""",
                    ([r[0] for r in linhas],))
        onde = {i: (la, lo) for i, la, lo in cur.fetchall()}
    else:
        onde = {}
    for pid, fonte, nome, url in linhas:
        if poligono and pid in onde:
            la, lo = onde[pid]
            if not area_utils.ponto_no_poligono(la, lo, poligono):
                fora += 1
                continue
        saida.append({"id": pid, "fonte": fonte, "nome": nome, "url": url})
        if limite and len(saida) >= limite:
            break
    return saida, fora


def gravar(con, poi_id, tipo, dados, **extra):
    import psycopg2
    campos = ["poi_id", "tipo", "dados", "bytes_tam"]
    vals = [poi_id, tipo,
            psycopg2.Binary(dados) if dados else None,
            len(dados) if dados else None]
    for k, v in extra.items():
        if v is not None:
            campos.append(k)
            vals.append(v)
    sets = ", ".join("%s = excluded.%s" % (c, c) for c in campos
                     if c not in ("poi_id", "tipo"))
    with con.cursor() as k:
        k.execute("insert into radar_comercial.poi_evidencia (%s) values (%s) "
                  "on conflict (id_empresa, poi_id, tipo) do update set %s, "
                  "capturado_em = now()"
                  % (", ".join(campos), ", ".join(["%s"] * len(campos)), sets),
                  vals)
    con.commit()


def _ajustar(png: bytes):
    """Volta o print a 1× e corta o rodapé que passar do teto.

    DUAS OPERAÇÕES, E A ORDEM IMPORTA.

    1. VOLTAR A 1×. O Camoufox desenha com fator de escala 2: uma janela de
       430 px sai como imagem de 860. Isso não acrescenta informação nenhuma —
       a página foi desenhada para ser lida a 430 —, e custa QUATRO VEZES mais
       pixels no orçamento do servidor de visão, que reduz o que passa do teto
       dele e devolve texto borrado. Reduzir para 430 aqui é devolver a imagem
       ao tamanho em que ela foi composta.

    2. CORTAR O QUE SOBRA. Só depois, e sobre a imagem já em 1×, de modo que o
       teto conte PÁGINA e não pixel de tela: 5.200 px a 1× são cinco mil e
       duzentos pixels de anúncio, contra dois mil e seiscentos se o corte
       viesse antes. É a diferença entre alcançar o calendário e parar nas
       fotos.

    O corte é sempre do FIM — o começo da página é onde estão título, mapa e
    nota. Devolve `(bytes, largura, altura)`; sem OpenCV a imagem passa
    inteira, porque print grande demais é melhor que print nenhum.
    """
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return png, None, None
        h, w = arr.shape[:2]
        mexeu = False
        if w > LARG:
            arr = cv2.resize(arr, (LARG, max(int(h * LARG / w), 1)),
                             interpolation=cv2.INTER_AREA)
            h, w = arr.shape[:2]
            mexeu = True
        if h > ALT_MAX:
            arr = arr[:ALT_MAX]
            h = ALT_MAX
            mexeu = True
        if mexeu:
            ok, buf = cv2.imencode(".png", arr)
            if ok:
                return buf.tobytes(), w, h
        return png, w, h
    except Exception:                                          # noqa: BLE001
        return png, None, None


# O QUE SAI DA FRENTE ANTES DO PRINT.
#
# O aviso de cookies do Airbnb ocupa a metade de baixo da primeira tela e tapa
# justamente o preço e o botão de disponibilidade. Ele é REMOVIDO DO DOM, e não
# clicado: clicar em "Aceitar" ou em "Somente o necessário" é dar consentimento
# em nome do usuário, e isso não é decisão de um raspador. Tirar o elemento da
# árvore não consente nada, não envia nada e não muda o que o servidor guardou
# — só faz o print mostrar a página em vez de mostrar a tarja.
#
# Cada seletor tem um porquê, e a lista é curta de propósito: seletor demais
# começa a apagar conteúdo. Se o Airbnb trocar os nomes, o pior que acontece é
# a tarja voltar — a captura não quebra.
# A BUSCA É PELO TEXTO, e não pelo `data-testid`. Tentei primeiro os testids
# do Airbnb (`main-cookie-banner-container`) e eles não pegaram nada em
# 04/09/2026 — nome interno de componente é a coisa que mais muda num site
# grande, e quando muda o seletor falha em silêncio. "Política de Cookies" é o
# que a lei obriga a estar escrito, então é o que dura.
#
# A varredura é dos elementos POSICIONADOS — `fixed` ou `sticky`, que é o que
# fica por cima da página — e para no primeiro ancestral que já não seja só a
# tarja: sem esse limite, subir a árvore acabaria removendo o `body`.
SUMIR = """() => {
  const marca = /pol[ií]tica de cookies|usamos cookies|aceitar todos/i;
  let n = 0;
  for (const el of document.querySelectorAll('div,section,aside')) {
    const pos = getComputedStyle(el).position;
    if (pos !== 'fixed' && pos !== 'sticky') continue;
    const txt = (el.innerText || '');
    if (txt.length > 1200 || !marca.test(txt)) continue;
    el.remove(); n++;
  }
  return n;
}"""


def _rolar_e_fotografar(page):
    """Desce a página inteira para acordar o carregamento preguiçoso."""
    page.set_viewport_size({"width": LARG, "height": ALT})
    page.wait_for_timeout(2500)
    try:
        page.evaluate(SUMIR)
    except Exception:                                          # noqa: BLE001
        pass
    alt_ant = -1
    for _ in range(PASSOS_ROLAGEM):
        page.mouse.wheel(0, ALT - 120)
        page.wait_for_timeout(PAUSA_ROLAGEM_MS)
        alt = page.evaluate("document.body.scrollHeight")
        if alt == alt_ant:
            break
        alt_ant = alt
    # DE VOLTA AO TOPO ANTES DO PRINT. Com `full_page` o Playwright refaz a
    # rolagem sozinho, mas componentes fixos (a barra de reserva do Airbnb)
    # ficam grudados onde a rolagem parou e aparecem repetidos na imagem.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1200)
    # DE NOVO, e não por desconfiança gratuita: o aviso é reinjetado quando a
    # rolagem chega ao fim da página. Tirá-lo só no começo deixava a tarja no
    # print em metade das capturas.
    try:
        page.evaluate(SUMIR)
        page.wait_for_timeout(300)
    except Exception:                                          # noqa: BLE001
        pass
    return page.screenshot(full_page=True)


def um_lote(lote, StealthySession, proxy, placar, trava, con_fab, aplicar):
    """Um IP, uma sessão, os POIs do lote."""
    con = con_fab()
    try:
        with StealthySession(headless=True, solve_cloudflare=True,
                             proxy=proxy, locale="pt-BR",
                             timezone_id="America/Sao_Paulo") as ses:
            for alvo in lote:
                tipo = TIPO_POR_FONTE[alvo["fonte"]]
                png = {}

                def acao(page, _png=png):
                    _png["b"] = _rolar_e_fotografar(page)

                try:
                    ses.fetch(alvo["url"], page_action=acao, timeout=120000)
                    b = png.get("b")
                    if not b:
                        raise RuntimeError("print vazio")
                    b, w, h = _ajustar(b)
                    if aplicar:
                        gravar(con, alvo["id"], tipo, b, url_origem=alvo["url"],
                               largura_px=w, altura_px=h)
                    with trava:
                        placar[tipo] += 1
                        _log("   %8d %-30s %6.0f KB  %sx%s"
                             % (alvo["id"], alvo["nome"][:30], len(b) / 1024,
                                w or "?", h or "?"))
                except Exception as e:                         # noqa: BLE001
                    if aplicar:
                        gravar(con, alvo["id"], tipo, None,
                               url_origem=alvo["url"],
                               motivo_falha="%s: %s" % (type(e).__name__,
                                                        str(e)[:80]))
                    with trava:
                        placar["falha"] += 1
                        _log("   %8d %-30s FALHOU %s"
                             % (alvo["id"], alvo["nome"][:30], type(e).__name__))
    except Exception as e:                                     # noqa: BLE001
        # LOTE INTEIRO PERDIDO, UM SÓ AVISO — quando a sessão nem sobe, o
        # motivo é um: o IP, ou o Cloudflare. Doze linhas iguais não ajudam.
        with trava:
            placar["lote_perdido"] += len(lote)
            _log("   lote de %d perdido na sessão — %s: %s"
                 % (len(lote), type(e).__name__, str(e)[:70]))
    finally:
        con.close()


def rodar(area, fontes, limite, aplicar, trabalhadores, sem_proxy, pois=None):
    # O POI EXPLÍCITO PASSA POR CIMA DA ÁREA. Quem digitou o id já disse qual
    # ponto quer; cruzá-lo com o desenho só criaria "não achei" sem motivo.
    poligono = None if pois else (area_utils.carregar_area(area) if area else None)
    con = bc.conectar()
    lista, fora = alvos(con, fontes, poligono, limite, pois)
    _log("   %d página(s) na fila" % len(lista))
    if fora:
        _log("   %d fora do desenho" % fora)
    if not lista:
        con.close()
        return {"alvos": 0}
    for a in lista[:5]:
        _log("      %8d %-28s %s" % (a["id"], a["nome"][:28], a["url"][:60]))
    if not aplicar:
        _log("   (ensaio: nada capturado. Use --aplicar)")
        con.close()
        return {"alvos": len(lista), "capturados": 0}
    con.close()

    sys.path.insert(0, "skills/extracao-poi-estadual")
    from scrapling.fetchers import StealthySession
    import detalhar_airbnb as da
    proximo = da._rodizio(sem_proxy)

    lotes, resto = [], list(lista)
    while resto:
        n = min(random.randint(LOTE_MIN, LOTE_MAX), len(resto))
        lotes.append(resto[:n])
        resto = resto[n:]

    placar = {t: 0 for t in TIPO_POR_FONTE.values()}
    placar.update({"falha": 0, "lote_perdido": 0})
    trava = threading.Lock()
    t0 = time.time()
    _log("   %d lote(s) de %d a %d · %d em paralelo · um IP por lote"
         % (len(lotes), LOTE_MIN, LOTE_MAX, trabalhadores))

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, trabalhadores)) as piscina:
        list(piscina.map(
            lambda lo: um_lote(lo, StealthySession, proximo(), placar, trava,
                               bc.conectar, aplicar),
            lotes))

    dt = time.time() - t0
    _log("")
    for k in sorted(placar):
        if placar[k]:
            _log("   %-16s %5d" % (k, placar[k]))
    _log("   %d página(s) em %.1f min · %.1f s por página"
         % (len(lista), dt / 60, dt / max(len(lista), 1)))
    return {"alvos": len(lista), **placar, "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--fonte", action="append",
                   choices=sorted(TIPO_POR_FONTE),
                   help="repetível; hoje só existe airbnb")
    # POI EXPLÍCITO, e não só a fila. Serve para reconferir um caso que o
    # usuário apontou na tela sem ter de mexer no filtro da fila.
    p.add_argument("--poi", action="append", type=int,
                   help="repetível; captura estes POIs ignorando a fila")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=2)
    p.add_argument("--sem-proxy", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    fontes = a.fonte or sorted(TIPO_POR_FONTE)
    _log("▶ página do anúncio como evidência — %s" % ", ".join(fontes))
    r = rodar(a.area, fontes, a.limite, a.aplicar, a.trabalhadores, a.sem_proxy,
              a.poi)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
