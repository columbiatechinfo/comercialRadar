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
                           max_scroll: int = 8) -> tuple:
    """Todos os estabelecimentos de um ramo perto de uma coordenada.

    Devolve `(achados, motivo)`. `achados` é `[(nome, lat, lng, termo)]`, com a
    coordenada tirada do `!3d!4d` do href — o ponto REAL do lugar, não o centro
    do mapa. `motivo` é "" quando deu certo e o QUE HOUVE quando não deu.

    POR QUE ELA DEVOLVE MOTIVO, e não só a lista

    A versão anterior devolvia `[]` em dois pontos sem dizer nada: o `goto` que
    falha e o feed que não aparece. De fora, os dois viravam a mesma linha —
    "0 no Maps" — indistinguível de "esta área não tem padaria".

    Em 26/08/2026 isso escondeu uma run inteira: as 46 categorias reportaram 0,
    e a causa era o perfil de navegador apodrecido, com TODA navegação em
    timeout. O painel dizia "varreu tudo, não achou nada". Não varreu nada.

    Área sem resultado e busca que não aconteceu são fatos diferentes, e quem
    lê o log precisa poder distinguir os dois.
    """
    from urllib.parse import quote
    url = (f"https://www.google.com/maps/search/{quote(termo)}/"
           f"@{lat},{lng},15z?hl=pt-BR")
    try:
        await page.goto(url, timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        return [], f"navegação falhou ({type(e).__name__})"
    await asyncio.sleep(3)
    await _dispensar_consent(page)
    await asyncio.sleep(1.5)

    feed = page.locator('div[role="feed"]')
    if not await feed.count():
        try:
            titulo = await page.title()
        except Exception:
            titulo = ""
        if not (titulo or "").strip():
            return [], "página veio em branco (sessão morta)"
        for sel, causa in (('form[action*="consent"]', "muro de consentimento"),
                           ('iframe[src*="recaptcha"]', "CAPTCHA")):
            try:
                if await page.locator(sel).count():
                    return [], causa
            except Exception:
                pass
        return [], "sem lista de resultados no DOM"

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
    return achados, ""


# SEIS WORKERS, e não três — cada um com seu IP e seu navegador.
#
# `_worker` pega um proxy próprio do pool (`acquire_blocking`) e um perfil
# próprio (`cr_descobre_{wid}`), então subir o número é literalmente mais IPs
# consultando o Maps ao mesmo tempo. O Google vê clientes separados, não um
# cliente insistente.
#
# MEDIDO em 27/08/2026: 46 categorias em ~3,7 min com 3 workers — cerca de 14 s
# por categoria. Com 6, a mesma varredura cai para perto de 1,8 min.
#
# Por que 6 e não 10 como a busca: a etapa 4 já usa 10, e as duas não rodam ao
# mesmo tempo — mas cada worker segura um IP do pool durante a varredura
# inteira, e o pool é o mesmo recurso que a etapa seguinte vai pedir.
WORKERS_PADRAO = 6


async def varrer(poligono, termos: list, workers: int = WORKERS_PADRAO,
                 usar_proxy: bool = True,
                 visivel: bool = False) -> list:
    """Roda as categorias e devolve o que caiu DENTRO do polígono, sem repetir.

    O centro de busca é o centroide da área; o `15z` cobre o entorno, e o
    recorte fino pelo polígono acontece aqui — a mesma disciplina do resto.
    """
    from human_browser import HumanSession
    from playwright.async_api import async_playwright
    from proxy_pool import ProxyPool
    import shutil
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
    falhas: list = []          # categoria que NAO foi buscada, e por que
    lock = asyncio.Lock()

    # PERFIL PODRE DERRUBA A ETAPA INTEIRA -- e foi o que aconteceu em 26/08/2026.
    #
    # O perfil de navegador e reaproveitado entre runs de proposito: sessao
    # morna toma menos CAPTCHA que sessao recem-nascida. So que quando ele
    # apodrece, TODA navegacao daquele worker passa a dar timeout, e as 46
    # categorias saem com "0 no Maps" -- que se le como "a area nao tem nada".
    #
    # MEDIDO na hora do conserto, mesma URL, mesmo proxy, mesmo momento:
    #     perfil cr_descobre_0 (velho) -> goto TIMEOUT, feed 0, links 0
    #     perfil novo em branco        -> feed 1, 20 links
    # Headless ou headful, com ou sem tz_hint: so o perfil mudava o resultado.
    #
    # Por isso o worker agora se cura: na primeira falha de NAVEGACAO ele joga
    # o perfil fora, refaz a sessao e tenta a mesma categoria outra vez. Uma
    # vez so -- se falhar de novo, o problema nao e o perfil, e insistir apenas
    # gastaria proxy.
    async def _worker(wid, pw):
        proxy = None
        if pool:
            proxy = await pool.acquire_blocking()
        perfil = Path(tempfile.gettempdir()) / f"cr_descobre_{wid}"

        async def _abrir():
            return await HumanSession.create(pw, proxy, perfil, layer="maps",
                                             tz_hint_lng=clng)

        # A CURA TROCA O IP TAMBEM, e nao so o perfil.
        #
        # A primeira versao refazia a sessao com o MESMO proxy. Se o problema
        # fosse o IP -- bloqueado, morto, lento demais --, a segunda tentativa
        # falhava identica. Foi o que aconteceu com `tabacarias` em 27/08/2026:
        # curou, tentou de novo pelo mesmo IP e desistiu. Uma de 46 categorias
        # ficou sem buscar, e o resumo teve de dizer que o numero nao cobria a
        # area.
        #
        # E O CONTADOR SUBSTITUIU O `curou = True`, que nunca voltava a False:
        # depois da primeira cura, aquele worker nao se curava mais pelo resto
        # da run. Perfil apodrece e IP cai a qualquer momento, nao so uma vez.
        # O teto de 3 existe para que um problema que a cura nao resolve pare de
        # consumir proxy -- se tres IPs e tres perfis nao abriram, o que esta
        # errado nao e nenhum deles.
        MAX_CURAS = 3
        curas = 0
        sess = await _abrir()
        try:
            while True:
                try:
                    termo = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                lista, motivo = await buscar_categoria(sess.page, termo, clat, clng)

                if motivo and curas < MAX_CURAS and (
                        "navegação" in motivo or "branco" in motivo):
                    curas += 1
                    print(f"  [{termo}] {motivo} — trocando perfil E IP "
                          f"(cura {curas}/{MAX_CURAS})", flush=True)
                    try:
                        await sess.close()
                    except Exception:
                        pass
                    shutil.rmtree(perfil, ignore_errors=True)
                    if pool and proxy:
                        # 10 min de castigo: se ele nao abriu o Maps agora, os
                        # proximos workers nao devem tropecar nele tambem.
                        await pool.mark_cooldown(proxy, segundos=600)
                        proxy = await pool.acquire_blocking()
                    sess = await _abrir()
                    lista, motivo = await buscar_categoria(sess.page, termo,
                                                           clat, clng)

                dentro = [x for x in lista
                          if au.ponto_no_poligono(x[1], x[2], poligono)]
                async with lock:
                    for nome, la, lo, term in dentro:
                        ch = (ev.norm_nome(nome), round(la, 5), round(lo, 5))
                        achados.setdefault(ch, {"nome": nome, "lat": la, "lng": lo,
                                                "categoria": term})
                if motivo:
                    falhas.append((termo, motivo))
                    print(f"  [{termo}] ⚠️  NÃO BUSCADO — {motivo}", flush=True)
                else:
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

    # O RESUMO TEM DE DIZER O QUE NAO FOI FEITO. Silencio aqui e o que fez uma
    # run inteira parecer "varreu 46 categorias" quando nao varreu nenhuma.
    if falhas:
        print(f"\n  ⚠️  {len(falhas)} de {len(termos)} categorias NÃO foram "
              f"buscadas — o número abaixo não cobre a área toda:", flush=True)
        for termo, motivo in falhas[:10]:
            print(f"       {termo}: {motivo}", flush=True)
        if len(falhas) > 10:
            print(f"       … e mais {len(falhas) - 10}", flush=True)
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
               and id_empresa = core.empresa_atual()
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
    p.add_argument("--workers", type=int, default=WORKERS_PADRAO)
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
    bc.assumir_empresa(cur, a.empresa)

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

    # A CIDADE VEM DA ÁREA, e não cravada no código.
    #
    # A primeira versão gravava "Canoas" para todo mundo. Em 26/08/2026 isso pôs
    # cinco POIs de Bento Gonçalves como sendo de Canoas — um deles chamado,
    # literalmente, "Loja Todeschini BENTO GONÇALVES". O teste de coerência do
    # projeto acusou no mesmo dia: 5 POIs a 81 km da cidade que declaram.
    #
    # `municipio_da_area` resolve pelo polígono, que é a única fonte que sabe
    # onde a mineração está acontecendo.
    cidade, uf = au.municipio_da_area(poly)
    if not cidade:
        raise SystemExit("não consegui resolver o município da área — sem isso os "
                         "POIs entrariam com cidade errada, e cidade errada "
                         "estraga o recorte de toda etapa seguinte")
    print(f"  gravando como {cidade}/{uf}")

    # O ENDEREÇO É GERADO PELA COORDENADA, ANTES DE GRAVAR.
    #
    # A varredura por categoria devolve nome e coordenada, nunca endereço — o
    # Maps não o entrega na lista. E POI sem endereço deixou de ser um estado
    # válido: regra do dono do produto de 27/08/2026, com o trigger
    # `poi_comparavel` recusando a linha no banco.
    #
    # A cascata é CNEFE e depois Maps. MEDIDO em 300 POIs de Canoas: o CNEFE
    # resolve 95% em 2 ms; os outros 5% custam dezenas de segundos no Maps, e
    # por isso ele fica atrás. Quem nem assim obtiver endereço com número NÃO
    # ENTRA — é a regra, e é o que impede o ponto incomparável de nascer.
    import endereco_reverso as rev
    cod = au.codigo_ibge_da_area(poly)
    if not cod:
        raise SystemExit("não resolvi o código IBGE da área — sem ele o CNEFE "
                         "não sabe em que município procurar a porta")

    com_endereco, sem_endereco = [], []
    for x in novos:
        achado = rev.endereco_de(x["lat"], x["lng"], cod,
                                 nome=x["nome"], cidade=cidade, usar_maps=True)
        if achado:
            x["endereco"] = achado["endereco"]
            x["gerado_por"] = achado["fonte"]
            com_endereco.append(x)
        else:
            sem_endereco.append(x)

    if sem_endereco:
        print(f"  {len(sem_endereco)} sem endereço nem pelo CNEFE nem pelo Maps — "
              f"não entram (a regra exige endereço com número):")
        for x in sem_endereco[:5]:
            print(f"      {x['nome'][:44]}")

    if not com_endereco:
        print("\n  nenhum POI com endereço — nada gravado")
        con.close()
        return 0

    import psycopg2.extras
    dados = [(x["nome"], x["endereco"], x["gerado_por"], "maps_categoria",
              x["lat"], x["lng"], x["lat"], x["lng"],
              f"maps_cat:{ev.norm_nome(x['nome'])}:{round(x['lat'],5)}:{round(x['lng'],5)}",
              x["categoria"], cidade, uf, "descoberto", True) for x in com_endereco]
    psycopg2.extras.execute_values(cur, """
        insert into pois (nome, endereco, endereco_gerado_por, fonte,
                          lat_origem, lng_origem, maps_lat, maps_lng,
                          place_id, categoria, cidade, uf, status, match_valido)
        values %s on conflict do nothing""", dados, page_size=500)
    con.commit()
    por_fonte = {}
    for x in com_endereco:
        por_fonte[x["gerado_por"]] = por_fonte.get(x["gerado_por"], 0) + 1
    detalhe = " · ".join(f"{k}: {v}" for k, v in sorted(por_fonte.items()))
    print(f"\n  GRAVADO: {len(com_endereco):,} POIs novos descobertos por categoria"
          f"  (endereço gerado — {detalhe})")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
