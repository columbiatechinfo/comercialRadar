"""
test_proxy.py — Testa se o proxy.py local consegue tunelar para o Google
"""
import asyncio
import subprocess
import socket
import sys
import time
from playwright.async_api import async_playwright
from proxy_manager import PROXIES

async def test():
    host, port, user, pwd = PROXIES[0]
    porta_local = 18200

    print(f"Subindo proxy.py local na porta {porta_local} → {user}@{host}:{port}")
    proc = subprocess.Popen(
        [sys.executable, '-m', 'proxy',
         '--hostname', '127.0.0.1',
         '--port', str(porta_local),
         '--proxy-pool', f'http://{user}:{pwd}@{host}:{port}',
         '--log-level', 'ERROR'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Aguarda subir
    for _ in range(20):
        try:
            s = socket.create_connection(('127.0.0.1', porta_local), timeout=1)
            s.close()
            print(f"  ✅ proxy.py local pronto na porta {porta_local}")
            break
        except:
            time.sleep(0.3)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                '--no-sandbox',
                f'--proxy-server=http://127.0.0.1:{porta_local}',
            ],
        )
        ctx  = await browser.new_context()
        page = await ctx.new_page()

        for url in ['http://ipv4.webshare.io/', 'https://example.com', 'https://www.google.com', 'https://www.google.com/maps']:
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                title = await page.title()
                print(f"  ✅ {url} → {title[:50]}")
            except Exception as e:
                print(f"  ❌ {url} → {str(e)[:80]}")

        input("\nPressione Enter para fechar...")
        await browser.close()

    proc.terminate()

asyncio.run(test())
