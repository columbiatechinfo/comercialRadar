# -*- coding: utf-8 -*-
"""Renderiza a amostra da galeria e fotografa — claro e escuro."""
import asyncio

from playwright.async_api import async_playwright

ARQ = "file:///app/saida/amostra_galeria.html"


async def main():
    async with async_playwright() as pw:
        nav = await pw.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        for tema, nome in (("light", "claro"), ("dark", "escuro")):
            ctx = await nav.new_context(viewport={"width": 1280, "height": 1400},
                                        color_scheme=tema)
            page = await ctx.new_page()
            erros = []
            page.on("pageerror", lambda e: erros.append(str(e)))
            page.on("console", lambda m: erros.append("console:" + m.text)
                    if m.type == "error" else None)
            await page.goto(ARQ, wait_until="networkidle")
            await page.wait_for_timeout(2500)
            await page.screenshot(path="/app/saida/render_%s_topo.png" % nome)
            await page.evaluate("window.scrollTo(0, 1250)")
            await page.wait_for_timeout(900)
            await page.screenshot(path="/app/saida/render_%s_meio.png" % nome)
            # abre os dois <details> do primeiro cartao
            await page.evaluate(
                "document.querySelectorAll('.poi details')"
                ".forEach((d,i)=>{ if(i<2) d.open = true; })")
            await page.wait_for_timeout(400)
            await page.screenshot(path="/app/saida/render_%s_aberto.png" % nome)
            n = await page.evaluate("document.querySelectorAll('.poi').length")
            vis = await page.evaluate(
                "Array.from(document.querySelectorAll('.poi'))"
                ".filter(c=>!c.hidden).length")
            conta = await page.evaluate(
                "document.getElementById('conta').textContent")
            fonte = await page.evaluate(
                "getComputedStyle(document.querySelector('h1')).fontFamily")
            print("%-7s cartoes=%d visiveis=%d contador=%r fonte=%s"
                  % (nome, n, vis, conta, fonte.split(",")[0]))
            if erros:
                print("   ERROS:", erros[:4])
            await ctx.close()
        await nav.close()


asyncio.run(main())
