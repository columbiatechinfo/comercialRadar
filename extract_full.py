"""
extract_full.py — Extração COMPLETA de um POI no painel do Google Maps

Além dos dados básicos (que o extract_panel já pega), coleta:
  - TODAS as fotos (até config.MAX_FOTOS) — vêm como background-image (lh3...)
  - TODAS as avaliações (até config.MAX_REVIEWS) — autor, nota, texto, data
  - Horários por dia (dict)

Seletores confirmados via probe no DOM real (Carrefour Canoas):
  reviews : div.jftiEf  → autor .d4r55 | nota span.kvMYJc[aria-label] | texto .wiI7pd | data .rsqaWe
  fotos   : background-image url("https://lh3...") nos elementos da galeria
  aba     : button[role=tab][aria-label^="Avaliações"]
  hero    : button[aria-label^="Foto de"]

Uso: chamado após extract_panel(), recebendo a `page` posicionada no painel.
"""

import re
import asyncio

import config


def _parse_nota(aria: str):
    if not aria:
        return None
    m = re.search(r"(\d+[.,]?\d*)", aria)
    return float(m.group(1).replace(",", ".")) if m else None


async def extrair_fotos(page, max_n: int = None) -> list:
    """Abre a galeria e coleta URLs (background-image lh3). Volta ao painel."""
    max_n = max_n or config.MAX_FOTOS
    urls = []
    try:
        hero = page.locator('button[aria-label^="Foto de"]').first
        if await hero.count() == 0:
            return []
        await hero.click(timeout=4000)
        await page.wait_for_timeout(2200)
        # rola a galeria para carregar mais miniaturas (cap baixo → poucas voltas)
        for _ in range(2):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(800)

        urls = await page.evaluate(
            """(max) => {
                const set = new Set();
                document.querySelectorAll('img').forEach(i => {
                    if (i.src && i.src.includes('googleusercontent')) set.add(i.src.split('=')[0]);
                });
                document.querySelectorAll('a,div,button').forEach(e => {
                    const st = (e.style && e.style.backgroundImage) || '';
                    const m = st.match(/url\\(\"?(https:\\/\\/lh3[^\")]+)\"?\\)/);
                    if (m) set.add(m[1].split('=')[0]);
                });
                return [...set].slice(0, max);
            }""",
            max_n,
        )
        # volta ao painel
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(700)
    except Exception:
        pass
    return urls or []


async def extrair_avaliacoes(page, max_n: int = None) -> list:
    """Abre a aba Avaliações, rola, expande 'Mais' e coleta as reviews."""
    max_n = max_n or config.MAX_REVIEWS
    reviews = []
    try:
        tab = page.locator('button[role=tab][aria-label^="Avaliações"]').first
        if await tab.count() == 0:
            return []
        await tab.click(timeout=4000)
        await page.wait_for_timeout(2500)

        # rola até ter reviews suficientes (ou esgotar tentativas)
        for _ in range(4):
            if await page.locator("div.jftiEf").count() >= max_n:
                break
            await page.mouse.wheel(0, 2200)
            await page.wait_for_timeout(1000)

        # expande botões "Mais" para pegar o texto completo
        try:
            mais = page.locator("button.w8nwRe")
            qt = min(await mais.count(), max_n)
            for i in range(qt):
                try:
                    await mais.nth(i).click(timeout=600)
                except Exception:
                    continue
            await page.wait_for_timeout(400)
        except Exception:
            pass

        data = await page.evaluate(
            """(max) => {
                const out = [];
                const els = document.querySelectorAll('div.jftiEf');
                for (let i = 0; i < Math.min(els.length, max); i++) {
                    const e = els[i];
                    const autor = e.querySelector('.d4r55')?.innerText || '';
                    const notaEl = e.querySelector('span.kvMYJc');
                    const nota = notaEl ? notaEl.getAttribute('aria-label') : '';
                    const texto = e.querySelector('.wiI7pd')?.innerText || '';
                    const data = e.querySelector('.rsqaWe')?.innerText || '';
                    out.push({ autor, nota, texto, data });
                }
                return out;
            }""",
            max_n,
        )
        for r in data:
            reviews.append({
                "autor": (r.get("autor") or "").strip(),
                "nota": _parse_nota(r.get("nota")),
                "texto": (r.get("texto") or "").strip(),
                "data": (r.get("data") or "").strip(),
            })
    except Exception:
        pass
    return reviews


async def enriquecer_poi(sess, poi: dict) -> dict:
    """
    Recebe um poi já com dados básicos (de extract_panel) e adiciona:
      poi['fotos']       → lista de URLs (até MAX_FOTOS)
      poi['comentarios'] → lista de reviews (até MAX_REVIEWS)
    Os horários (poi['horarios']) já vêm do extract_panel.

    Ordem: avaliações primeiro (partindo do painel limpo via aba), depois volta
    para "Visão geral" e abre a galeria de fotos. A ordem inversa bagunçava o
    estado (a galeria + Escape tirava a aba Avaliações do alcance).
    """
    page = sess.page

    try:
        poi["comentarios"] = await extrair_avaliacoes(page)
    except Exception:
        poi["comentarios"] = []

    # volta para a aba "Visão geral" antes de abrir as fotos
    try:
        vg = page.locator('button[role=tab][aria-label^="Visão geral"]').first
        if await vg.count() > 0:
            await vg.click(timeout=3000)
            await page.wait_for_timeout(1200)
    except Exception:
        pass

    # RESULTADO VAZIO NÃO APAGA O QUE JÁ EXISTE. O `extract_panel` já colheu as
    # miniaturas do painel — é de lá que vieram as 24.515 fotos de Canoas — e
    # `extrair_fotos` devolve [] sempre que não encontra o botão da galeria
    # (`button[aria-label^="Foto de"]`). Atribuindo direto, o vazio da galeria
    # apagava as fotos boas do painel: no enriquecimento de Canoas, 86 POIs
    # seguidos saíram com zero foto. Mesmo princípio do merge do ingestor.
    try:
        novas = await extrair_fotos(page)
    except Exception:
        novas = []
    if novas:
        poi["fotos"] = novas
    else:
        poi["fotos"] = poi.get("fotos", []) or []

    return poi
