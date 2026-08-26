# -*- coding: utf-8 -*-
"""descobrir_maps.py — descoberta por CATEGORIA no Google Maps.

O QUE ESTA ETAPA FAZ, e por que ela substitui a descoberta do iFood

A descoberta pelo iFood morreu: o Cloudflare passou a exigir Turnstile
interativo, a API de listagem virou 404/403, o app é blindado contra emulador, e
os proxis da conta são todos estrangeiros. Toda rota de DESCOBERTA pelo iFood
está fechada — só o detalhe (`/extra`) segue aberto.

Mas o Google Maps tem os botões de categoria — "Restaurantes", "Farmácias",
"Hotéis"… — e cada um devolve, no próprio DOM, a LISTA de estabelecimentos
daquele tipo na área, com nome e coordenada real. É o mesmo navegador que a
captura+OCR já usa, e responde 200 sem proxy. Medido em Canoas: 40 restaurantes
por busca, nome + coordenada, sem OCR e sem bloqueio.

A DIFERENÇA PARA A CAPTURA+OCR

A captura fotografa o tile, detecta ícones e LÊ os nomes — e o OCR erra
("Pizferia", "Hambihguer"). A busca por categoria pega o nome ESCRITO pelo
próprio Google, sem intermediário. As duas se somam: a captura vê o que tem
marcador no zoom 19; a categoria vê o que o Google classifica naquele ramo,
inclusive o que não desenhou marcador.

O QUE ELA COMPLEMENTA, E NÃO DUPLICA

Só entra o que é DIFERENTE do que já está no banco. A deduplicação é a mesma do
resto do sistema (nome normalizado + coordenada próxima); um ponto que já existe
por outra fonte não vira POI de novo. É a regra "rodar de novo ACRESCENTA".

USO
    python descobrir_maps.py --area area_atual
    python descobrir_maps.py --area area_atual --empresa "Aegea - Corsan" --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import re

import config  # noqa: F401
import area_utils as au
import base_comum as bc
import evidencia as ev

UUID_COORD = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")

# As categorias que o Maps oferece como botão, mais as de interesse comercial
# para saneamento. A lista é generosa: cada uma é uma busca barata, e uma
# categoria de fora custa os pontos que ela acharia.
#
# São consultadas em pt-BR pelo termo que o Maps entende, não pelo rótulo do
# botão — "restaurantes" acha mais que clicar no chip, que já vem filtrado.
CATEGORIAS = [
    "restaurantes", "lanchonetes", "pizzarias", "cafeterias", "bares",
    "padarias", "sorveterias", "docerias", "churrascarias",
    "mercados", "supermercados", "mercearias", "hortifruti", "açougues",
    "farmácias", "drogarias", "pet shop", "petshop", "agropecuária",
    "lojas de roupas", "lojas de calçados", "óticas", "joalherias",
    "salões de beleza", "barbearias", "academias", "estética",
    "oficinas mecânicas", "autopeças", "lava-rápido",
    "hotéis", "pousadas", "motéis",
    "lojas de material de construção", "ferragens", "móveis",
    "papelarias", "livrarias", "floriculturas", "tabacarias",
    "clínicas", "consultórios odontológicos", "laboratórios",
    "escritórios de contabilidade", "escritórios de advocacia", "imobiliárias",
]

_ACENTOS = "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ"
_LISOS = "aaaaeeiooouucAAAAEEIOOOUUC"


async def _dispensar_consent(page):
    for sel in ('button[aria-label*="Aceitar"]', 'button:has-text("Aceitar tudo")',
                '#L2AGLb', 'form[action*="consent"] button'):
        try:
            if await page.locator(sel).count():
                await page.locator(sel).first.click(timeout=2000)
                return
        except Exception:
            continue


async def buscar_categoria(page, termo: str, lat: float, lng: float,
                           max_scroll: int = 8) -> list:
    """Todos os estabelecimentos de um ramo perto de uma coordenada.

    Devolve `[(nome, lat, lng)]`. A coordenada sai do `!3d!4d` do href — é o
    ponto REAL do lugar, não o centro do mapa.
    """
    from urllib.parse import quote
    url = (f"https://www.google.com/maps/search/{quote(termo)}/"
           f"@{lat},{lng},15z?hl=pt-BR")
    try:
        await page.goto(url, timeout=45000, wait_until="domcontentloaded")
    except Exception:
        return []
    await asyncio.sleep(3)
    await _dispensar_consent(page)
    await asyncio.sleep(1.5)

    feed = page.locator('div[role="feed"]')
    if not await feed.count():
        return []

    # ROLA ATÉ O FIM. O Maps carrega os resultados por scroll; parar cedo perde
    # a cauda. Para quando a altura não cresce mais (chegou em "Você chegou ao
    # fim da lista") ou no teto de scrolls.
    ultima = -1
    for _ in range(max_scroll):
        try:
            await feed.evaluate("e => e.scrollBy(0, 3000)")
        except Exception:
            break
        await asyncio.sleep(1.2)
        try:
            altura = await feed.evaluate("e => e.scrollHeight")
        except Exception:
            break
        if altura == ultima:
            break
        ultima = altura

    cards = page.locator('div[role="feed"] a[href*="/maps/place/"]')
    n = await cards.count()
    vistos, achados = set(), []
    for i in range(n):
        a = cards.nth(i)
        try:
            href = await a.get_attribute("href")
            nome = await a.get_attribute("aria-label")
        except Exception:
            continue
        m = UUID_COORD.search(href or "")
        if not (m and nome):
            continue
        la, lo = float(m.group(1)), float(m.group(2))
        chave = (ev.norm_nome(nome), round(la, 5), round(lo, 5))
        if chave in vistos:
            continue
        vistos.add(chave)
        achados.append((nome.strip(), la, lo, termo))
    return achados


async def varrer(poligono, termos: list, workers: int = 3, usar_proxy: bool = True,
                 visivel: bool = False) -> list:
    """Roda as categorias e devolve o que caiu DENTRO do polígono, sem repetir.

    O centro de busca é o centroide da área; o `15z` cobre o entorno, e o
    recorte fino pelo polígono acontece aqui — a mesma disciplina do resto.
    """
    from human_browser import HumanSession
    from playwright.async_api import async_playwright
    from proxy_pool import ProxyPool
    import tempfile
    from pathlib import Path

    s, n, o, l = au.bbox(poligono)
    clat, clng = (s + n) / 2, (o + l) / 2

    # PROXY POR PADRAO, E NAO E ZELO EXCESSIVO.
    #
    # A varredura abre dezenas de buscas de categoria em sequencia contra o
    # Google. Do IP residencial do operador isso e o padrao que faz o Google
    # pedir CAPTCHA no navegador PESSOAL dele — o mesmo IP que ele usa para
    # trabalhar. O proxy isola esse risco: quem toma o desafio e um IP
    # descartavel, nao o dele.
    pool = ProxyPool().start() if usar_proxy else None
    if not usar_proxy:
        print("  ⚠️  SEM PROXY — a varredura vai pelo SEU IP. Dezenas de buscas "
              "seguidas ao Google podem fazer ELE pedir CAPTCHA no seu navegador. "
              "Use --proxy (o padrao) a menos que saiba o que esta fazendo.")
    fila = asyncio.Queue()
    for t in termos:
        fila.put_nowait(t)

    achados: dict = {}
    lock = asyncio.Lock()

    async def _worker(wid, pw):
        proxy = None
        if pool:
            proxy = await pool.acquire_blocking()
        perfil = Path(tempfile.gettempdir()) / f"cr_descobre_{wid}"
        sess = await HumanSession.create(pw, proxy, perfil, layer="maps",
                                         tz_hint_lng=clng)
        try:
            while True:
                try:
                    termo = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                lista = await buscar_categoria(sess.page, termo, clat, clng)
                dentro = [x for x in lista
                          if au.ponto_no_poligono(x[1], x[2], poligono)]
                async with lock:
                    for nome, la, lo, term in dentro:
                        ch = (ev.norm_nome(nome), round(la, 5), round(lo, 5))
                        achados.setdefault(ch, {"nome": nome, "lat": la, "lng": lo,
                                                "categoria": term})
                print(f"  [{termo}] {len(lista)} no Maps · {len(dentro)} na área",
                      flush=True)
        finally:
            try:
                await sess.close()
            except Exception:
                pass
            if pool and proxy:
                await pool.release(proxy)

    async with async_playwright() as pw:
        await asyncio.gather(*(_worker(i, pw) for i in range(workers)))
    return list(achados.values())


def _novos(cur, achados: list) -> list:
    """Filtra o que JÁ existe no banco. Só o diferente segue.

    Mesma dedup do sistema: nome normalizado + coordenada a ~30 m. Um ponto que
    outra fonte já trouxe não vira POI de novo.
    """
    novos = []
    for a in achados:
        cur.execute("""
            select 1 from pois
             where maps_lat is not null
               and abs(maps_lat - %s) < 0.0003 and abs(maps_lng - %s) < 0.0003
               and upper(translate(coalesce(nome,''), %s, %s))
                 = upper(translate(%s, %s, %s))
               and tenant_id = (select nullif(current_setting('app.tenant_id', true), '')::uuid)
             limit 1""",
                    (a["lat"], a["lng"], _ACENTOS, _LISOS,
                     a["nome"], _ACENTOS, _LISOS))
        if not cur.fetchone():
            novos.append(a)
    return novos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", required=True)
    p.add_argument("--empresa", default="Aegea - Corsan")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true",
                   help="RISCO: varre pelo seu IP; o Google pode pedir CAPTCHA "
                        "no seu navegador pessoal. O padrao usa proxy.")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--limite-cat", type=int, default=0,
                   help="só as N primeiras categorias (teste)")
    a = p.parse_args(argv)

    poly = au.carregar_area(a.area)
    if not poly:
        raise SystemExit(f"área {a.area!r} não existe")

    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    cur.execute("select id, nome from tenants where lower(nome)=lower(%s) and ativo",
                (a.empresa.strip(),))
    r = cur.fetchone()
    if not r:
        raise SystemExit(f"empresa {a.empresa!r} não existe")
    cur.execute("select set_config('app.tenant_id', %s, false)", (str(r[0]),))

    termos = CATEGORIAS[:a.limite_cat] if a.limite_cat else CATEGORIAS
    print(f"  varrendo {len(termos)} categorias na área {a.area!r}...")
    achados = asyncio.run(varrer(poly, termos, workers=a.workers,
                                 usar_proxy=not a.sem_proxy))
    print(f"\n  {len(achados)} estabelecimentos distintos na área")

    novos = _novos(cur, achados)
    print(f"  {len(novos)} são NOVOS (não estão no banco)")
    for x in novos[:12]:
        print(f"    {x['nome'][:40]:42} {x['categoria']:20} {x['lat']:.5f},{x['lng']:.5f}")

    if not a.aplicar:
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return 0

    import psycopg2.extras
    dados = [(x["nome"], "maps_categoria", x["lat"], x["lng"], x["lat"], x["lng"],
              f"maps_cat:{ev.norm_nome(x['nome'])}:{round(x['lat'],5)}:{round(x['lng'],5)}",
              x["categoria"], "Canoas", "RS", "descoberto", True) for x in novos]
    psycopg2.extras.execute_values(cur, """
        insert into pois (nome, fonte, lat_origem, lng_origem, maps_lat, maps_lng,
                          place_id, categoria, cidade, uf, status, match_valido)
        values %s on conflict do nothing""", dados, page_size=500)
    con.commit()
    print(f"\n  GRAVADO: {len(novos):,} POIs novos descobertos por categoria")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
