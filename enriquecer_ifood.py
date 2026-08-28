from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
import time
import pathlib
import unicodedata
import shutil

import base_comum as bc
from proxy_pool import ProxyPool

BASE = "https://www.ifood.com.br"


CATEGORIAS = ["restaurantes", "mercados", "bebidas", "farmacias", "pets", "shopping"]


BAIRROS = {
    "canoas": [
        ("Centro", -29.9177, -51.1836), ("Igara", -29.9070, -51.1730),
        ("Niterói", -29.9310, -51.1780), ("Mathias Velho", -29.9005, -51.2328),
        ("Guajuviras", -29.8790, -51.1470), ("Olaria", -29.9250, -51.1900),
        ("Harmonia", -29.9430, -51.1620), ("Marechal Rondon", -29.8930, -51.1900),
        ("Fátima", -29.9350, -51.1550), ("São José", -29.9120, -51.1650),
        ("Rio Branco", -29.9390, -51.1740), ("Estância Velha", -29.9280, -51.2050),
    ],
}

_RE_CNPJ = re.compile(r"CNPJ[:\s]*([\d./-]{14,20})")

CNPJ_DA_PLATAFORMA = {"14380200000121"}
_RE_CEP = re.compile(r"CEP[:\s]*([\d-]{8,10})")


def _pausa() -> float:
    return random.uniform(2.6, 6.4)


def _n(s: str | None) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def _digitos(s: str | None) -> str:
    return "".join(c for c in (s or "") if c.isdigit())


async def _humano(page) -> None:
    try:
        for _ in range(random.randint(2, 4)):
            await page.mouse.move(random.randint(150, 1250), random.randint(150, 780),
                                  steps=random.randint(12, 28))
            await asyncio.sleep(random.uniform(0.2, 0.7))
        for _ in range(random.randint(1, 3)):
            await page.mouse.wheel(0, random.randint(180, 460))
            await asyncio.sleep(random.uniform(0.5, 1.4))
    except Exception:
        pass


PERFIL = os.environ.get(
    "IFOOD_PERFIL",
    str(pathlib.Path.home() / ".comercialradar" / "perfil_ifood"))


async def abrir_navegador(pw, visivel: bool, proxy: dict = None, perfil_path: str = None):
    path_perfil = perfil_path or PERFIL
    pathlib.Path(path_perfil).mkdir(parents=True, exist_ok=True)
    
    proxy_cfg = None
    if proxy:
        proxy_cfg = ProxyPool.to_playwright(proxy)
        
    ctx = await pw.chromium.launch_persistent_context(
        path_perfil, headless=not visivel,
        proxy=proxy_cfg,
        args=["--disable-blink-features=AutomationControlled", "--lang=pt-BR"],
        locale="pt-BR", timezone_id="America/Sao_Paulo",
        viewport={"width": 1440, "height": 900},
        permissions=["geolocation"],
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"))
    await ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    return ctx, ctx, page


async def tem_captcha(page) -> bool:
    """Há um muro de verificação na frente? Os DOIS muros, não só o antigo.

    ELE PROCURAVA A PROTEÇÃO DO ANO PASSADO. A única marca testada era
    `px-captcha` — o PerimeterX, com o botão "Pressione e segure". O iFood
    trocou para **Cloudflare Turnstile** em algum momento antes de 26/08/2026, e
    as marcas do Turnstile não têm nada a ver com aquelas.

    O CUSTO DE NÃO DETECTAR não é falhar: é falhar SEM DIZER. Medido em
    28/08/2026, com IP residencial brasileiro do plano novo:

        goto          OK em 0,9 s
        título        "Um momento…"        (o interstício do Cloudflare)
        marcas        turnstile + cloudflare no HTML
        campo endereço  NENHUM — o app nunca montou
        tem_captcha() False   ← e por isso o fluxo seguiu como se estivesse ok

    O resultado era `extrair_ifood` gastar 3,2 min e um IP para terminar com um
    `TimeoutError` genérico, quando a resposta certa estava na tela desde o
    primeiro segundo. Agora ele diz "desafio na abertura" e devolve o IP.

    O TÍTULO ENTRA NA CONTA porque o interstício do Cloudflare troca o
    documento inteiro: não há seletor do app para procurar, e "Um momento…" /
    "Just a moment…" é o que sobra.
    """
    try:
        if await page.locator(
                "#px-captcha-modal, [id*='px-captcha'], "
                "[class*='cf-turnstile'], #cf-chl-widget, "
                "iframe[src*='challenges.cloudflare.com']").count() > 0:
            return True
        titulo = (await page.title() or "").strip().lower()
        return titulo.startswith("um momento") or titulo.startswith("just a moment")
    except Exception:
        return False


async def insistir_no_captcha(page, max_tentativas=5) -> bool:
    for tentativa in range(1, max_tentativas + 1):
        if not await tem_captcha(page):
            return True

        print(f"   🤖 Lutando contra o CAPTCHA (Tentativa {tentativa}/{max_tentativas})...", flush=True)
        try:
            botoes = page.locator("text='Pressione e segure'")
            if await botoes.count() == 0:
                botoes = page.locator("[id*='px-captcha']")

            if await botoes.count() > 0:
                alvo = botoes.first
                box = await alvo.bounding_box()
                
                if box:
                    x = box["x"] + box["width"] / 2 + random.uniform(-15, 15)
                    y = box["y"] + box["height"] / 2 + random.uniform(-8, 8)

                    await page.mouse.move(x, y, steps=random.randint(20, 40))
                    await asyncio.sleep(random.uniform(0.5, 1.2))

                    await page.mouse.down()

                    tempo_total = random.uniform(8.0, 14.0)
                    passos = int(tempo_total * 5) 

                    for _ in range(passos):
                        await page.mouse.move(
                            x + random.uniform(-3, 3),
                            y + random.uniform(-3, 3),
                            steps=2
                        )
                        await asyncio.sleep(tempo_total / passos)

                    await page.mouse.up()
                    print(f"   🤏 Clique concluído ({tempo_total:.1f}s). Aguardando...", flush=True)

                    await asyncio.sleep(random.uniform(4.0, 7.0))
        except Exception as e:
            print(f"   ⚠️ Erro ao interagir com o desafio: {e}", flush=True)

    return not await tem_captcha(page)


async def continuar_noutra_aba(ctx, desafiada):
    nova = await ctx.new_page()
    await nova.goto(f"{BASE}/inicio", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(3)
    if await tem_captcha(nova):
        if not await insistir_no_captcha(nova):
            await nova.close()
            return desafiada, False
    return nova, True


def ja_lidas(con) -> set:
    with con.cursor() as cur:
        cur.execute("""SELECT merchant_id FROM ifood_merchant
                        WHERE COALESCE(cnpj, '') <> '' OR COALESCE(rua, '') <> ''""")
        return {r[0] for r in cur.fetchall()}


async def entrar_no_app(page, endereco: str) -> bool:
    """Salva o endereço e entra no app. Devolve False DIZENDO onde parou."""
    await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(_pausa())

    if "/inicio" in page.url:
        return True

    campo = page.locator("input.landing-v2-address-search__input").first
    if not await campo.count():
        await page.goto(f"{BASE}/inicio", wait_until="domcontentloaded",
                        timeout=60000)
        await asyncio.sleep(_pausa())
        return "/inicio" in page.url

    await campo.click()
    await campo.type(endereco, delay=random.randint(90, 150))

    # ESPERA PELA SUGESTÃO, não pelo relógio. O autocomplete é do Google Places
    # e a latência varia — com proxy, mais ainda. Um `sleep` fixo acertava na
    # conexão direta e errava na indireta: a lista ainda não tinha renderizado,
    # nada era clicado, e a função devolvia False sem dizer o motivo.
    ACHAR = """() => [...document.querySelectorAll('li,button,div')].find(
         e => e.offsetHeight && (e.innerText || '').length < 90
              && /[0-9]/.test(e.innerText || '')
              && (e.innerText || '').includes(','))"""
    el = None
    limite = time.time() + 15.0
    while time.time() < limite:
        el = (await page.evaluate_handle(ACHAR)).as_element()
        if el:
            break
        await asyncio.sleep(0.5)
    if not el:
        print(f"   entrar_no_app[{endereco[:34]}]: nenhuma sugestão em 15 s.",
              flush=True)
        return False
    await el.click()
    await asyncio.sleep(2.0)

    # Modal do mapa → painel de complemento. A CLASSE vem antes do texto: o
    # rótulo tem acento, o elemento nem sempre é <button> e o texto muda com o
    # teste A/B. `address-maps__submit` é o seletor que o mapa de contratos
    # registrou observando o fluxo real.
    PASSOS = (
        ("mapa", (".address-maps__submit", "[class*='address-maps__submit']",
                  "[class*='maps'] button[type='submit']")),
        ("complemento", ("[class*='address-complement'] button[type='submit']",
                         "[class*='address'] button[type='submit']")),
    )
    ROTULOS = ("Confirmar localização", "Salvar endereço", "Continuar")

    async def clicar_quando_surgir(seletores, segundos):
        """ESPERA o botão existir. O modal é montado DEPOIS da sugestão, e
        procurá-lo uma vez só encontrava a landing vazia."""
        fim = time.time() + segundos
        while time.time() < fim:
            for quadro in page.frames:
                for sel in seletores:
                    try:
                        b = quadro.locator(sel).first
                        if await b.count() and await b.is_visible():
                            await b.click()
                            return sel
                    except Exception:
                        continue
                for rot in ROTULOS:
                    try:
                        b = quadro.get_by_role("button", name=rot,
                                               exact=False).first
                        if await b.count() and await b.is_visible():
                            await b.click()
                            return f"texto:{rot}"
                    except Exception:
                        continue
            await asyncio.sleep(0.5)
        return None

    clicados = []
    for etapa, seletores in PASSOS:
        achado = await clicar_quando_surgir(seletores,
                                            20.0 if not clicados else 10.0)
        if achado:
            clicados.append(f"{etapa}:{achado}")
            await asyncio.sleep(2.0)

    try:
        await page.wait_for_url("**/inicio**", timeout=12000)
    except Exception:
        pass
    if "/inicio" in page.url:
        return True

    print(f"   entrar_no_app[{endereco[:34]}]: sugestão ok, botões "
          f"{clicados or 'nenhum'}, parou em {page.url[:60]}", flush=True)
    if not clicados:
        for quadro in page.frames:
            try:
                vis = await quadro.evaluate(
                    """() => [...document.querySelectorAll('button,[role=button]')]
                         .filter(e => e.offsetHeight)
                         .map(e => ((e.innerText||'').trim().slice(0,34) + ' @' +
                                    (e.className||'').toString().slice(0,44)))""")
            except Exception:
                continue
            if vis:
                print(f"      quadro {quadro.url[:50]} · {len(vis)} botões",
                      flush=True)
                for v in vis:
                    print(f"         {v}", flush=True)
    return False


async def trocar_bairro(page, ctx, nome: str, lat: float, lng: float) -> bool:
    await ctx.set_geolocation({"latitude": lat, "longitude": lng})
    if "/inicio" not in page.url:
        await page.goto(f"{BASE}/inicio", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(_pausa())
    aberto = False
    for alvo in ("button:has-text('Próximo de')", "header button:has-text('Rua')",
                 "header [class*='address']", "[class*='address-header']"):
        try:
            b = page.locator(alvo).first
            if await b.count() and await b.is_visible():
                await b.click()
                await asyncio.sleep(2.2)
                aberto = True
                break
        except Exception:
            continue
    if not aberto:
        return False
    for rot in ("Usar minha localização", "Usar localização atual"):
        try:
            b = page.get_by_text(rot, exact=False).first
            if await b.count() and await b.is_visible():
                await b.click()
                await asyncio.sleep(3.5)
                break
        except Exception:
            continue
    for rot in ("Confirmar localização", "Salvar endereço", "Confirmar"):
        try:
            b = page.locator(f"button:has-text('{rot}')").first
            if await b.count() and await b.is_visible():
                await b.click()
                await asyncio.sleep(2.6)
        except Exception:
            continue
    await asyncio.sleep(2.0)
    return True


async def lojas_da_categoria(page, categoria: str, teto: int) -> list:
    await page.goto(f"{BASE}/{categoria}", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(_pausa())
    vistos, parado = {}, 0
    while parado < 3 and len(vistos) < teto:
        antes = len(vistos)
        for a in await page.locator("a[href*='/delivery/']").all():
            try:
                href = await a.get_attribute("href")
                if not href or "/delivery/" not in href:
                    continue
                slug = href.split("/delivery/")[-1].split("?")[0].strip("/")
                if slug.count("/") != 1:
                    continue
                if slug not in vistos:
                    vistos[slug] = (await a.inner_text() or "").split("\n")[0].strip()
            except Exception:
                continue
        await _humano(page)
        await page.mouse.wheel(0, random.randint(900, 1600))
        await asyncio.sleep(random.uniform(1.1, 2.0))
        parado = parado + 1 if len(vistos) == antes else 0
    return [{"slug": s, "nome": n, "categoria_aba": categoria}
            for s, n in list(vistos.items())[:teto]]


async def ler_loja(page, slug: str) -> dict:
    out = {"estado": "SEM_DADO"}
    await page.goto(f"{BASE}/delivery/{slug}", wait_until="domcontentloaded",
                    timeout=60000)
    await asyncio.sleep(_pausa())
    corpo = (await page.inner_text("body"))[:4000]
    if "Acesso negado" in corpo or "unusual traffic" in corpo.lower():
        out["estado"] = "BLOQUEADO"
        return out
    for alvo in ("text=Ver mais", "button:has-text('Ver mais')",
                 "[class*='merchant-info']"):
        try:
            b = page.locator(alvo).first
            if await b.count() and await b.is_visible():
                await b.click(force=True)
                await asyncio.sleep(random.uniform(1.2, 2.2))
                break
        except Exception:
            continue
    texto = await page.inner_text("body")
    achados = [_digitos(x) for x in _RE_CNPJ.findall(texto)]
    do_lojista = [c for c in achados if c not in CNPJ_DA_PLATAFORMA and len(c) == 14]
    if do_lojista:
        out["cnpj"] = do_lojista[0]
    mc = _RE_CEP.search(texto)
    if mc:
        out["cep"] = _digitos(mc.group(1))
    if "Endereço" in texto:
        depois = texto.split("Endereço", 1)[1].strip().split("\n")
        linhas = [l.strip() for l in depois[:3] if l.strip()][:2]
        if linhas:
            out["endereco"] = " · ".join(linhas)
    cab = await page.locator("h1").first.inner_text() if await page.locator("h1").count() else None
    if cab:
        out["nome_pagina"] = cab.strip()
    nota = re.search(r"([0-5][.,]\d)\s*(?:\n|★|estrela)", texto)
    if nota:
        out["nota"] = nota.group(1).replace(",", ".")
    if out.get("cnpj"):
        out["estado"] = "OK"
    elif await tem_captcha(page):
        out["estado"] = "BLOQUEADO"
    else:
        out["estado"] = "SEM_CNPJ"
    return out


def gravar(con, itens: list, cidade: str) -> tuple:
    from psycopg2.extras import Json, execute_values
    linhas, casados = [], 0
    with con.cursor() as cur:
        for m in itens:
            poi, cnpj = None, m.get("cnpj")
            if cnpj:
                cur.execute(r"""SELECT id FROM pois
                                WHERE regexp_replace(COALESCE(cnpj,''),'\D','','g') = %s
                                LIMIT 1""", (cnpj,))
                r = cur.fetchone()
                poi = r[0] if r else None
            if poi is None and m.get("nome"):
                cur.execute("""SELECT id, nome FROM pois
                                WHERE lower(cidade) = lower(%s) AND nome IS NOT NULL""",
                            (cidade,))
                alvo = _n(m["nome"])
                for pid, nome in cur.fetchall():
                    nn = _n(nome)
                    if nn and len(nn) > 5 and (nn in alvo or alvo in nn):
                        poi = pid
                        break
            casados += 1 if poi else 0
            linhas.append((m["slug"], m.get("nome_pagina") or m.get("nome"),
                           m.get("categoria_aba"), m["slug"], m.get("nota"),
                           cnpj or None, None, None,
                           m.get("endereco"), None, m.get("bairro_busca"),
                           m.get("cep"), None, None, m.get("estado"), poi, Json(m)))
        if linhas:
            execute_values(cur, """
                INSERT INTO ifood_merchant
                    (merchant_id, nome, categoria, slug, nota, cnpj, telefone,
                     avaliacoes, rua, numero, bairro, cep, lat, lng,
                     estado_detalhe, poi_id, bruto) VALUES %s
                ON CONFLICT (merchant_id) DO UPDATE SET
                    nome = COALESCE(EXCLUDED.nome, ifood_merchant.nome),
                    cnpj = COALESCE(EXCLUDED.cnpj, ifood_merchant.cnpj),
                    rua = COALESCE(EXCLUDED.rua, ifood_merchant.rua),
                    cep = COALESCE(EXCLUDED.cep, ifood_merchant.cep),
                    nota = COALESCE(EXCLUDED.nota, ifood_merchant.nota),
                    estado_detalhe = EXCLUDED.estado_detalhe,
                    poi_id = COALESCE(EXCLUDED.poi_id, ifood_merchant.poi_id),
                    visto_em = now()""", linhas)
    con.commit()
    return len(linhas), casados


async def _nova_sessao_limpa(pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil):
    print("   ♻️ Sessão suja/quebrada. Fechando, trocando IP e limpando perfil físico...", flush=True)
    if br:
        try:
            await br.close()
        except Exception:
            pass
    
    if perfil_atual:
        shutil.rmtree(perfil_atual, ignore_errors=True)
    
    if proxy_atual:
        await pool.mark_cooldown(proxy_atual, 1800)
        await pool.release(proxy_atual)
    
    novo_proxy = await pool.acquire_blocking(intervalo=2.0, tentativas=60)
    if not novo_proxy:
        print("   ⚠️ Sem proxies livres (todos em cooldown). Abortando rotação.", flush=True)
        return None, None, None, None, None
        
    nova_seq = seq_perfil + 1
    novo_perfil = f"{PERFIL}_{nova_seq}"
    
    n_br, n_ctx, n_page = await abrir_navegador(pw, args.visivel, novo_proxy, novo_perfil)
    return n_br, n_ctx, n_page, novo_proxy, novo_perfil


async def _rotacionar_sessao(pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil, bairro, la, lo):
    n_br, n_ctx, n_page, novo_proxy, novo_perfil = await _nova_sessao_limpa(
        pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil
    )
    if not novo_proxy:
        return None, None, None, None, None
        
    if not await entrar_no_app(n_page, args.endereco):
        print("! Falha ao reentrar no app após rotação.", flush=True)
        return n_br, n_ctx, n_page, novo_proxy, novo_perfil
        
    if not await trocar_bairro(n_page, n_ctx, bairro, la, lo):
        print("! Falha ao trocar o bairro após rotação.", flush=True)
        return n_br, n_ctx, n_page, novo_proxy, novo_perfil
        
    return n_br, n_ctx, n_page, novo_proxy, novo_perfil


async def rodar(args) -> int:
    from playwright.async_api import async_playwright
    bairros = BAIRROS.get(args.cidade.lower())
    if not bairros:
        print(f"! não tenho a lista de bairros de '{args.cidade}'.")
        return 1
    con = bc.conectar()
    print("⟦fase⟧ ifood", flush=True)
    print(f"{len(bairros)} bairros x {len(CATEGORIAS)} categorias · "
          f"navegador {'visível' if args.visivel else 'oculto'} · com proxy via Webshare",
          flush=True)
          
    # LIMPEZA DE PERFIS RESIDUAIS VAZADOS NO DISCO
    base_path = pathlib.Path(PERFIL)
    parent_dir = base_path.parent
    prefix = base_path.name + "_"
    removidos = 0
    if parent_dir.exists():
        for p in parent_dir.iterdir():
            if p.is_dir() and p.name.startswith(prefix):
                shutil.rmtree(p, ignore_errors=True)
                removidos += 1
    if removidos > 0:
        print(f"   🧹 Limpeza: {removidos} perfis residuais removidos do disco.", flush=True)
          
    pool = ProxyPool().start()

    async with async_playwright() as pw:
        proxy_atual = await pool.acquire_blocking()
        seq_perfil = 1
        perfil_atual = f"{PERFIL}_{seq_perfil}"
        
        br, ctx, page = None, None, None
        if proxy_atual:
            br, ctx, page = await abrir_navegador(pw, args.visivel, proxy_atual, perfil_atual)
            
        t0, lidas, com_cnpj, bloqueios = time.time(), 0, 0, 0
        estacionada = None
        feitas = ja_lidas(con)
        if feitas:
            print(f"   retomando: {len(feitas)} lojas já lidas ficam de fora",
                  flush=True)
        try:
            if not br:
                print("! Falha ao obter proxy inicial. Abortando.", flush=True)
                return 1
                
            tentativas_entrada = 0
            entrou = False
            while tentativas_entrada < 3:
                await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(3)
                
                if await tem_captcha(page):
                    tentativas_entrada += 1
                    print(f"   ⚠️ Desafio na entrada (tentativa IP {tentativas_entrada}/3).", flush=True)
                    if not await insistir_no_captcha(page):
                        if tentativas_entrada >= 3:
                            print(f"! Falha na entrada: {tentativas_entrada} IPs testados e todos caíram no desafio inquebrável. Abortando.", flush=True)
                            return 1
                            
                        br, ctx, page, proxy_atual, perfil_atual = await _nova_sessao_limpa(
                            pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil
                        )
                        if not proxy_atual:
                            return 1
                        seq_perfil += 1
                        continue
                
                if not await entrar_no_app(page, args.endereco):
                    tentativas_entrada += 1
                    print(f"   ⚠️ Falha ao preencher endereço inicial (tentativa {tentativas_entrada}/3). Ocorreu um erro de carregamento ou layout.", flush=True)
                    if tentativas_entrada >= 3:
                        print("! Falha na entrada: não consegui entrar no app com o endereço inicial após 3 tentativas. Abortando.", flush=True)
                        return 1
                        
                    # Se falhou ao entrar, rotaciona e tenta de novo
                    br, ctx, page, proxy_atual, perfil_atual = await _nova_sessao_limpa(
                        pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil
                    )
                    if not proxy_atual:
                        return 1
                    seq_perfil += 1
                    continue
                    
                entrou = True
                break
                
            if not entrou:
                return 1
                
            print(f"   dentro do app · {page.url[:56]}", flush=True)
            for bairro, la, lo in bairros[:args.bairros or len(bairros)]:
                if not await trocar_bairro(page, ctx, bairro, la, lo):
                    print(f"  {bairro}: não consegui trocar a localização", flush=True)
                    continue
                print(f"\n  ── {bairro} ──", flush=True)
                for cat in CATEGORIAS:
                    try:
                        lojas = await lojas_da_categoria(page, cat, args.limit or 9999)
                    except Exception as e:
                        print(f"     {cat:<14}erro: {type(e).__name__}", flush=True)
                        continue
                    print(f"     {cat:<14}{len(lojas)} lojas", flush=True)
                    lojas = [x for x in lojas if x["slug"] not in feitas]
                    novas = []
                    for lj in lojas:
                        lj["bairro_busca"] = bairro
                        desafiado = False
                        try:
                            lj.update(await ler_loja(page, lj["slug"]))
                            if await tem_captcha(page):
                                desafiado = True
                                print("   ⚠️ Desafio detectado na loja. Engajando robô de resolução...", flush=True)
                                if await insistir_no_captcha(page):
                                    print("   ✅ Desafio vencido! Relendo os dados da loja...", flush=True)
                                    lido = await ler_loja(page, lj["slug"])
                                    lj.update(lido)
                                else:
                                    bloqueios += 1
                                    if estacionada is not None or bloqueios >= 3:
                                        br, ctx, page, proxy_atual, perfil_atual = await _rotacionar_sessao(
                                            pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil, bairro, la, lo
                                        )
                                        if not proxy_atual:
                                            bloqueios = 999
                                            break
                                        seq_perfil += 1
                                        bloqueios = 0
                                        estacionada = None
                                        continue
                                    
                                    print("   ⚠️ Desafio na aba atual resistiu a automação; sigo noutra aba.", flush=True)
                                    estacionada = page
                                    page, ok = await continuar_noutra_aba(ctx, page)
                                    if not ok:
                                        print("   a aba nova também caiu no desafio: a sessão toda está barrada. Rotacionando.", flush=True)
                                        br, ctx, page, proxy_atual, perfil_atual = await _rotacionar_sessao(
                                            pw, br, args, pool, proxy_atual, perfil_atual, seq_perfil, bairro, la, lo
                                        )
                                        if not proxy_atual:
                                            bloqueios = 999
                                            break
                                        seq_perfil += 1
                                        bloqueios = 0
                                        estacionada = None
                                        continue
                                        
                                    lido = await ler_loja(page, lj["slug"])
                                    if lido.get("cnpj") or lido.get("endereco"):
                                        lj.update(lido)
                        except Exception as e:
                            lj["estado"] = f"ERRO {type(e).__name__}"
                        lidas += 1
                        if lj.get("cnpj"):
                            com_cnpj += 1
                            bloqueios = 0
                            print(f"        {str(lj.get('nome'))[:30]:<32}"
                                  f"CNPJ {lj['cnpj']}  {str(lj.get('endereco') or '')[:38]}",
                                  flush=True)
                        elif lj.get("estado") == "BLOQUEADO":
                            if not desafiado:
                                bloqueios += 1
                            print(f"        {str(lj.get('nome'))[:30]:<32}BLOQUEADO",
                                  flush=True)
                        novas.append(lj)
                        if lj.get("cnpj") or lj.get("endereco"):
                            feitas.add(lj["slug"])
                            
                        if bloqueios >= 999:
                            break
                            
                        await asyncio.sleep(_pausa() + 12.0 * bloqueios)
                        await _humano(page)
                    if novas:
                        n, c = gravar(con, novas, args.cidade)
                        print(f"     · {n} gravadas, {c} casadas com POI", flush=True)
                    if bloqueios >= 999:
                        break
                if bloqueios >= 999:
                    break
        finally:
            if proxy_atual:
                await pool.release(proxy_atual)
            if br:
                try:
                    await br.close()
                except Exception:
                    pass
            if perfil_atual:
                shutil.rmtree(perfil_atual, ignore_errors=True)
    con.close()
    print(f"\nfim — {lidas} lojas abertas, {com_cnpj} com CNPJ, "
          f"{(time.time()-t0)/60:.0f} min", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--endereco", default="Rua Quinze de Janeiro, 181, Canoas")
    p.add_argument("--limit", type=int, default=0, help="lojas por categoria")
    p.add_argument("--bairros", type=int, default=0, help="quantos bairros")
    p.add_argument("--oculto", dest="visivel", action="store_false",
                   help="roda sem janela (o padrão é VISÍVEL, como pedido)")
    p.set_defaults(visivel=True)
    return asyncio.run(rodar(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())