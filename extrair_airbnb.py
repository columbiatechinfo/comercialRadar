# -*- coding: utf-8 -*-
"""Descoberta de hospedagens do Airbnb, pela CAIXA DELIMITADORA da área.

O MÉTODO, e por que é este

A busca do Airbnb aceita a caixa na própria URL — `ne_lat`, `ne_lng`, `sw_lat`,
`sw_lng` mais `search_by_map=true`. Medido em 02/09/2026, num retângulo de
1,1 km × 1,5 km no centro de Canoas:

    caixa delimitadora    18 de 18 anúncios DENTRO do polígono
    "Perto de você"        0 de 18 — devolve a região metropolitana inteira,
                           com cabanas a 30 km do desenho

O "Perto de você" existe e obedece à geolocalização injetada, como o do iFood.
Ele só não serve para o que se quer aqui: é busca regional, não recorte. A caixa
serve, e ainda dispensa clique — que foi a parte frágil de tudo nesta sessão.

O DADO ESTÁ EMBUTIDO, NÃO HÁ FEED PARA ESCUTAR

Diferente do iFood. O Airbnb entrega a página renderizada com o estado num
`<script data-deferred-state-0>`, e é de lá que sai cada resultado, com
coordenada:

    searchResults[].demandStayListing.location.coordinate

E `network_idle` NÃO serve nesta página — ela mantém tráfego de fundo e nunca
fica ociosa. Espera-se o seletor do primeiro cartão.

O TETO DE 270, E COMO SE FURA

Uma busca devolve 18 por página e pagina até 15 — 270, digam os resultados o que
disserem ("mais de mil acomodações em Canoas"). Por isso a caixa é DIVIDIDA em
quatro quando satura: cada quadrante é uma busca nova, com seu próprio teto. É a
mesma ideia da grade do `pontos_de_busca`, só que o corte aqui é geométrico e
não depende de endereço.

O QUE CAI FORA DO POLÍGONO É GRAVADO ASSIM MESMO

A caixa é um retângulo; o desenho do usuário não é. O que cai no retângulo e
fora do polígono entra com `na_area = false` — a mesma política de
`area_utils`: achar custa busca, descartar o que já foi achado é jogar fora
trabalho pago. O que `na_area` decide é quem recebe a parte cara, no
`detalhar_airbnb`.

Uso:
    python extrair_airbnb.py --area                       # a área desenhada
    python extrair_airbnb.py --area minha_area --simular
    python extrair_airbnb.py --cidade Canoas --uf RS      # o município inteiro
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse

from psycopg2.extras import execute_values

import area_utils as au
import base_comum as bc

BASE = "https://www.airbnb.com.br/s/homes"

# Uma busca devolve 18 por página e pagina até 15. Chegar perto disso significa
# que a caixa tem mais do que ela consegue mostrar — e o que não coube não
# aparece em lugar nenhum, silenciosamente.
POR_PAGINA = 18
TETO_BUSCA = 15 * POR_PAGINA
# Abaixo disto não vale dividir: o custo de uma sessão nova supera o que se
# ganharia, e quadrante minúsculo devolve os mesmos vizinhos.
SATURADO = int(TETO_BUSCA * 0.85)
LADO_MINIMO_GRAU = 0.0025          # ~275 m; abaixo disso não se subdivide mais

NL = chr(10)

COLHER = r"""() => {
  for (const s of document.querySelectorAll('script[id^="data-deferred-state"]')) {
    let d; try { d = JSON.parse(s.textContent || ''); } catch (e) { continue; }
    let res = null;
    const cacar = (no, prof) => {
      if (res || !no || typeof no !== 'object' || prof > 14) return;
      if (Array.isArray(no)) { for (const x of no.slice(0, 40)) cacar(x, prof + 1); return; }
      if (Array.isArray(no.searchResults)) { res = no.searchResults; return; }
      for (const k of Object.keys(no)) cacar(no[k], prof + 1);
    };
    cacar(d, 0);
    if (!res) continue;
    return res.map(r => {
      const l = r.demandStayListing || {};
      const c = (l.location && l.location.coordinate) || {};
      const d2 = (l.description && l.description.name) || {};
      const p = (r.structuredDisplayPrice || {}).primaryLine || {};
      return {
        // `l.id` vem em base64; o id do /rooms/ está no href do cartão, e é
        // esse que serve de chave — é por ele que a ficha é aberta depois.
        nome: d2.localizedStringWithTranslationPreference || null,
        titulo: r.title || null,
        subtitulo: r.subtitle || null,
        lat: c.latitude, lng: c.longitude,
        preco: p.price || p.discountedPrice || null,
        avaliacao: r.avgRatingA11yLabel || null,
      };
    });
  }
  return null;
}"""

IDS = """() => {
  const s = [];
  for (const a of document.querySelectorAll('a[href*="/rooms/"]')) {
    const m = (a.getAttribute('href') || '').match(/\\/rooms\\/(\\d+)/);
    if (m && s.indexOf(m[1]) < 0) s.push(m[1]);
  }
  return s;
}"""

GRAVAR = """
insert into airbnb_anuncio
       (anuncio_id, nome, titulo, tipo_resumo, lat, lng, coord_exata,
        na_area, area_ref, nota, avaliacoes_qtd, preco_total, bruto, visto_em)
values %s
-- POR EMPRESA, e nao so pelo id da fonte (migracao 0058).
-- O mesmo estabelecimento existe uma vez em CADA empresa que o extraiu ou
-- reaproveitou; um indice global impediria isso. `id_empresa` nao aparece na
-- lista de colunas do insert porque o gatilho `preencher_empresa` a carimba
-- antes — e o gatilho BEFORE INSERT roda antes da checagem de conflito, entao
-- a inferencia pelo indice funciona.
on conflict (id_empresa, anuncio_id) do update set
  nome        = coalesce(excluded.nome,        airbnb_anuncio.nome),
  titulo      = coalesce(excluded.titulo,      airbnb_anuncio.titulo),
  tipo_resumo = coalesce(excluded.tipo_resumo, airbnb_anuncio.tipo_resumo),
  lat         = coalesce(excluded.lat,         airbnb_anuncio.lat),
  lng         = coalesce(excluded.lng,         airbnb_anuncio.lng),
  coord_exata = coalesce(excluded.coord_exata, airbnb_anuncio.coord_exata),
  -- `na_area` SOBE, nunca desce: um anúncio visto dentro do desenho continua
  -- dentro, mesmo que uma varredura de caixa maior o reencontre pela borda.
  na_area     = airbnb_anuncio.na_area or coalesce(excluded.na_area, false),
  area_ref    = coalesce(airbnb_anuncio.area_ref, excluded.area_ref),
  nota        = coalesce(excluded.nota,        airbnb_anuncio.nota),
  avaliacoes_qtd = coalesce(excluded.avaliacoes_qtd, airbnb_anuncio.avaliacoes_qtd),
  preco_total = coalesce(excluded.preco_total, airbnb_anuncio.preco_total),
  bruto       = excluded.bruto,
  visto_em    = now()
"""


def casas(v):
    """Quantas casas decimais o número REALMENTE tem. 4 casas ~ 11 m."""
    if v is None:
        return None
    t = repr(float(v))
    return len(t.split(".")[1]) if "." in t else 0


def url_da_caixa(sw_lat, sw_lng, ne_lat, ne_lng, pagina_cursor=None):
    p = {"refinement_paths[]": "/homes", "tab_id": "home_tab",
         "search_by_map": "true", "search_type": "user_map_move",
         "ne_lat": ne_lat, "ne_lng": ne_lng,
         "sw_lat": sw_lat, "sw_lng": sw_lng}
    if pagina_cursor:
        p["cursor"] = pagina_cursor
    return BASE + "?" + urllib.parse.urlencode(p)


def quadrantes(sw_lat, sw_lng, ne_lat, ne_lng):
    """Divide a caixa em quatro. Cada uma vira uma busca com seu próprio teto."""
    mlat = (sw_lat + ne_lat) / 2.0
    mlng = (sw_lng + ne_lng) / 2.0
    return [(sw_lat, sw_lng, mlat, mlng), (sw_lat, mlng, mlat, ne_lng),
            (mlat, sw_lng, ne_lat, mlng), (mlat, mlng, ne_lat, ne_lng)]


def uma_caixa(sessao, cx, paginas):
    """Percorre as páginas de UMA caixa. Devolve {anuncio_id: registro}."""
    sw_lat, sw_lng, ne_lat, ne_lng = cx
    achado = {}

    def acao(page):
        # Espera o cartao aparecer, mas NAO exige que ele apareca: 25 s e o
        # bastante para a lista carregar quando existe, e area sem hospedagem
        # segue adiante em vez de travar.
        try:
            page.wait_for_selector('a[href*="/rooms/"]', state="attached",
                                   timeout=25000)
        except Exception:                                      # noqa: BLE001
            pass
        page.wait_for_timeout(2500)
        ids = page.evaluate(IDS) or []
        itens = page.evaluate(COLHER) or []
        # o payload vem NA MESMA ORDEM dos cartões — é assim que o id do href
        # se cola ao registro que traz a coordenada
        for i, item in enumerate(itens):
            if i >= len(ids):
                break
            achado[ids[i]] = item
        for _ in range(paginas - 1):
            avancou = False
            for tentativa in (
                    lambda: page.locator('a[aria-label*="Próxima"]').first,
                    lambda: page.locator('button[aria-label*="Próxima"]').first,
                    lambda: page.get_by_role("link", name="Próxima").first):
                try:
                    el = tentativa()
                    el.wait_for(state="visible", timeout=6000)
                    el.scroll_into_view_if_needed(timeout=4000)
                    el.click(timeout=6000)
                    avancou = True
                    break
                except Exception:
                    continue
            if not avancou:
                break
            page.wait_for_timeout(6000)
            ids = page.evaluate(IDS) or []
            itens = page.evaluate(COLHER) or []
            for i, item in enumerate(itens):
                if i < len(ids):
                    achado.setdefault(ids[i], item)

    # O TETO ERA DE SETE MINUTOS, E ELE FOI GASTO INTEIRO.
    #
    # Em 03/09/2026 a etapa ficou nove minutos sem escrever uma linha, com
    # ZERO conexao de rede e dois `chrome_crashpad <defunct>` dentro do
    # conteiner: o navegador morreu e o `fetch` esperou o teto acabar. De
    # fora, isso e indistinguivel de travamento — e numa cidade inteira,
    # com varias caixas, vira horas de espera por nada.
    #
    # As buscas que deram certo responderam em 10 a 20 s. Cento e vinte
    # segundos e seis a doze vezes isso: folgado para uma pagina lenta ou
    # um Cloudflare demorado, e curto o bastante para um navegador morto
    # ser percebido enquanto ainda ha rodada.
    sessao.fetch(url_da_caixa(sw_lat, sw_lng, ne_lat, ne_lng),
                 page_action=acao, timeout=120000)
    return achado


def varrer(proxies, caixa_inicial, paginas, log=print, profundidade=0,
           estado=None):
    """Varre a caixa e SUBDIVIDE quando ela satura. Devolve o dicionário todo."""
    from scrapling.fetchers import StealthySession

    sw_lat, sw_lng, ne_lat, ne_lng = caixa_inicial
    lado = max(ne_lat - sw_lat, ne_lng - sw_lng)
    tudo = {}
    # `estado["buscou"]` separa DUAS COISAS que davam o mesmo resultado vazio:
    # a busca que rodou e nao achou hospedagem, e a busca que nem chegou a
    # rodar. Sem essa distincao, area sem Airbnb era relatada como falha da
    # etapa — foi o que derrubou a etapa 6 em 04/09/2026 num quarteirao
    # residencial de Canoas.
    if estado is None:
        estado = {}
    try:
        # SEM `wait_selector` NA SESSAO, e essa foi a causa da etapa 6 morrer.
        #
        # Ela esperava ate 120 s por `a[href*="/rooms/"]` — o cartao de anuncio.
        # Numa area que NAO TEM HOSPEDAGEM esse seletor nunca aparece, e o
        # timeout era relatado como falha de busca. Medido em 04/09/2026 num
        # quarteirao residencial de Canoas: a pagina respondeu 200, o Cloudflare
        # nao entrou, e a etapa gastou 2,5 min para dizer "falhou" sobre uma
        # area que so nao tem Airbnb.
        #
        # A espera desceu para dentro de `acao`, com prazo curto e sem exigir
        # que o cartao exista: quem decide se ha anuncio e a leitura, nao a
        # sessao. Area vazia passa a custar segundos e a ser relatada como
        # vazia.
        with StealthySession(headless=True, solve_cloudflare=True,
                             proxy=proxies(), locale="pt-BR",
                             timezone_id="America/Sao_Paulo") as s:
            tudo = uma_caixa(s, caixa_inicial, paginas)
        estado["buscou"] = True
    except Exception as e:                                     # noqa: BLE001
        log("  %scaixa %.4f,%.4f..%.4f,%.4f — %s"
            % ("  " * profundidade, sw_lat, sw_lng, ne_lat, ne_lng,
               type(e).__name__))
        return tudo

    log("  %scaixa %.4f,%.4f..%.4f,%.4f → %d anúncios"
        % ("  " * profundidade, sw_lat, sw_lng, ne_lat, ne_lng, len(tudo)))

    # SATUROU? O que não coube não aparece em lugar nenhum — e é por isso que a
    # divisão não é otimização, é correção.
    if len(tudo) >= SATURADO and lado > LADO_MINIMO_GRAU and profundidade < 3:
        log("  %s  saturou (>= %d) — dividindo em quatro"
            % ("  " * profundidade, SATURADO))
        for q in quadrantes(*caixa_inicial):
            tudo.update(varrer(proxies, q, paginas, log,
                               profundidade + 1, estado))
    return tudo


def gravar(con, achado, poligono, area_ref):
    """Grava tudo; `na_area` diz quem está dentro do desenho."""
    linhas = []
    dentro = 0
    for anuncio_id, r in achado.items():
        lat, lng = r.get("lat"), r.get("lng")
        na_area = bool(poligono and lat is not None
                       and au.ponto_no_poligono(lat, lng, poligono))
        if na_area:
            dentro += 1
        nota = None
        qtd = None
        rotulo = r.get("avaliacao") or ""
        import re as _re
        m = _re.search(r"([\d,\.]+)\s*de uma avalia", rotulo)
        if m:
            try:
                nota = float(m.group(1).replace(",", "."))
            except ValueError:
                nota = None
        m = _re.search(r"(\d+)\s*avaliaç", rotulo)
        if m:
            qtd = int(m.group(1))
        linhas.append((
            anuncio_id, r.get("nome"), r.get("titulo"), r.get("subtitulo"),
            lat, lng,
            (casas(lat) or 0) > 5,          # coord_exata
            na_area, area_ref, nota, qtd, r.get("preco"),
            json.dumps(r, ensure_ascii=False),
        ))
    with con.cursor() as k:
        execute_values(k, GRAVAR, linhas, page_size=500,
                       template="(%s,%s,%s,%s,%s::float8,%s::float8,%s::bool,"
                                "%s::bool,%s,%s::numeric,%s::int,%s,%s::jsonb,now())")
    con.commit()
    return len(linhas), dentro


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--area", nargs="?", const=au.AREA_PADRAO, default=None,
                   metavar="NOME", help="usa a área desenhada salva no banco")
    p.add_argument("--cidade", help="alternativa à área: o município inteiro")
    p.add_argument("--uf")
    p.add_argument("--paginas", type=int, default=15,
                   help="páginas por caixa (padrão 15, que é o teto do Airbnb)")
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()
    if not a.area and not a.cidade:
        p.error("informe --area NOME ou --cidade NOME (com --uf)")

    poligono, area_ref = None, None
    if a.area:
        poligono = au.carregar_area(a.area)
        if not poligono:
            print("! área %r não encontrada." % a.area, flush=True)
            return 1
        area_ref = a.area
        sw_lat, sw_lng, ne_lat, ne_lng = _caixa_do_poligono(poligono)
        escopo = "área %r · %d vértices" % (a.area, len(poligono))
    else:
        # Sem desenho, a caixa é a do município — e o polígono do município
        # vira o recorte, para `na_area` continuar significando alguma coisa.
        poligono = _poligono_do_municipio(a.cidade, a.uf)
        area_ref = "%s/%s" % (a.cidade, a.uf or "")
        sw_lat, sw_lng, ne_lat, ne_lng = _caixa_do_poligono(poligono)
        escopo = "município %s%s" % (a.cidade, "/" + a.uf if a.uf else "")

    print("⟦fase⟧ airbnb-descoberta", flush=True)
    print("%s · caixa lat %.4f..%.4f · lng %.4f..%.4f · %d páginas por caixa"
          % (escopo, sw_lat, ne_lat, sw_lng, ne_lng, a.paginas), flush=True)

    proxies = _rodizio(a.sem_proxy)
    t0 = time.time()
    _estado_busca = {}
    achado = varrer(proxies, (sw_lat, sw_lng, ne_lat, ne_lng), a.paginas,
                    estado=_estado_busca)

    dentro = sum(1 for r in achado.values()
                 if r.get("lat") is not None
                 and au.ponto_no_poligono(r["lat"], r["lng"], poligono))
    exatas = sum(1 for r in achado.values() if (casas(r.get("lat")) or 0) > 5)
    print("%s%d anúncios · %d dentro do desenho · %d com coordenada exata · %.1f min"
          % (NL, len(achado), dentro, exatas, (time.time() - t0) / 60), flush=True)
    if not achado:
        # VAZIO NAO E FALHA quando a busca rodou. Um quarteirao residencial nao
        # tem Airbnb, e dizer "falhou" sobre isso faz o operador procurar
        # defeito onde ha so ausencia — e faz a etapa aparecer como quebrada no
        # placar da rodada.
        if _estado_busca.get("buscou"):
            print("! nenhum anúncio nesta área. A página respondeu e a lista "
                  "veio vazia — o mais provável é que não haja hospedagem "
                  "aqui. Se a área for turística, aí sim desconfie da busca.",
                  flush=True)
            return 0
        print("! a busca NÃO CHEGOU A RODAR — nenhuma caixa carregou. Isso é "
              "bloqueio ou rede, não ausência de hospedagem.", flush=True)
        return 1

    if a.simular:
        for i, (k, r) in enumerate(list(achado.items())[:10]):
            marca = "DENTRO" if (r.get("lat") is not None and
                                 au.ponto_no_poligono(r["lat"], r["lng"], poligono)) else "fora  "
            print("   %s %-14s %-42s %s, %s"
                  % (marca, k[:14], str(r.get("nome"))[:42], r.get("lat"), r.get("lng")),
                  flush=True)
        print("(simulação — nada gravado)", flush=True)
        return 0

    con = bc.conectar()
    try:
        n, d = gravar(con, achado, poligono, area_ref)
        print("gravados %d · %d dentro da área" % (n, d), flush=True)
        with con.cursor() as k:
            k.execute("""select estado_detalhe, na_area, count(*)
                           from airbnb_anuncio group by 1, 2 order by 3 desc""")
            for est, na, c in k.fetchall():
                print("   %-12s na_area=%-5s %d" % (est, na, c), flush=True)
    finally:
        con.close()
    return 0


def _caixa_do_poligono(poligono):
    lats = [p[0] for p in poligono]
    lngs = [p[1] for p in poligono]
    return min(lats), min(lngs), max(lats), max(lngs)


def _poligono_do_municipio(cidade, uf):
    """O contorno do município, da malha do IBGE que o projeto já carrega."""
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute("""select st_asgeojson(geom)
                           from resources_root.ibge_malha
                          where translate(lower(nome), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇáàâãéêíóôõúüç', 'AAAAEEIOOOUUCaaaaeeiooouuc') = translate(lower(%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇáàâãéêíóôõúüç', 'AAAAEEIOOOUUCaaaaeeiooouuc')
                            and (%s is null or upper(uf) = upper(%s))
                          limit 1""", (cidade, uf, uf))
            linha = k.fetchone()
    finally:
        con.close()
    if not linha:
        raise SystemExit("municipio %r nao esta na ibge_malha" % cidade)
    geo = json.loads(linha[0])
    coords = geo["coordinates"]
    while isinstance(coords[0][0], list):
        coords = coords[0]
    # o GeoJSON é [lng, lat]; `area_utils` fala [lat, lng]
    return [[c[1], c[0]] for c in coords]


def _rodizio(sem_proxy):
    """Devolve uma função que dá o PRÓXIMO proxy a cada chamada.

    Um IP por sessão. Processo novo começa com `_in_use` vazio, então pedir
    sempre "o menos usado" devolveria o mesmo IP de sempre — foi o que fez o
    Google parar de responder numa sondagem inteira.
    """
    if sem_proxy:
        return lambda: None
    try:
        from proxy_pool import ProxyPool
    except Exception as e:                                     # noqa: BLE001
        print("  ⚠️  pool indisponível (%s) — sessões pelo IP direto"
              % type(e).__name__, flush=True)
        return lambda: None
    pool = ProxyPool(pais="BR")
    pool.start()
    escolhidos = asyncio.run(_pegar(pool, 24))
    urls = []
    for px in escolhidos:
        if not px:
            continue
        cfg = ProxyPool.to_playwright(px)
        servidor = str(cfg.get("server") or "").replace("http://", "")
        if cfg.get("username"):
            urls.append("http://%s:%s@%s" % (cfg["username"],
                                             cfg.get("password") or "", servidor))
        elif servidor:
            urls.append("http://%s" % servidor)
    if not urls:
        return lambda: None
    estado = {"i": -1}

    def proximo():
        estado["i"] = (estado["i"] + 1) % len(urls)
        return urls[estado["i"]]
    return proximo


async def _pegar(pool, n):
    return [await pool.acquire() for _ in range(n)]


if __name__ == "__main__":
    sys.exit(main())
