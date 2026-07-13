"""
streetview_capture.py — Print do Street View para cada POI localizado (GRÁTIS).

Para cada POI válido do banco com coordenada e sem streetview_path, abre o
panorama do Street View na coordenada via Playwright headless (o Google Earth
Pro é app desktop — automatizá-lo por mouse/teclado para milhares de POIs é
inviável; o panorama do Maps é o mesmo acervo, em lote e headless), tira um
screenshot e salva em streetview/<poi_id>.jpg. Atualiza pois.streetview_path
direto no banco (o modal do frontend exibe via /streetview/<arquivo>).

Sem pano na coordenada → marca 'NA' (não tenta de novo).

USO:
  .venv\\Scripts\\python streetview_capture.py [--workers 3] [--limit N] [--refazer]
"""

import re
import math
import time
import asyncio
import argparse
from pathlib import Path

from playwright.async_api import async_playwright

import config
import realtime_ingest

BASE = Path(__file__).resolve().parent
SV_DIR = BASE / "streetview"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Depois que o pano carrega, a URL vira /@CAM_LAT,CAM_LNG,3a,... (posição da câmera).
_RE_CAM = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),3a")


def _bearing(lat1, lng1, lat2, lng2) -> float:
    """Ângulo (0-360°, N=0) da câmera (1) em direção ao estabelecimento (2)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb.20220419-08-p0.pt+FX+410",
     "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAESHAgBEhJnd3NfMjAyMjA0MTktMF9SQzEaAnB0IAEaBgiAo_KTBg",
     "domain": ".google.com", "path": "/"},
]


def carregar_alvos(limit: int, refazer: bool, ids: list = None) -> list:
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            if ids:
                cur.execute("""
                    SELECT id, nome, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                    FROM pois
                    WHERE id = ANY(%s) AND COALESCE(maps_lat, lat_origem) IS NOT NULL
                    ORDER BY id""", (ids,))
            else:
                filtro = "" if refazer else "AND (streetview_path IS NULL OR streetview_path = '')"
                cur.execute(f"""
                    SELECT id, nome, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                    FROM pois
                    WHERE match_valido IS NOT FALSE
                      AND COALESCE(maps_lat, lat_origem) IS NOT NULL
                      {filtro}
                    ORDER BY id""")
            alvos = [{"id": i, "nome": n, "lat": la, "lng": lo} for i, n, la, lo in cur.fetchall()]
            return alvos[:limit] if limit > 0 else alvos
    finally:
        conn.close()


def _gravar_path(poi_id: int, path: str, conn):
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE pois SET streetview_path = %s WHERE id = %s", (path, poi_id))


async def _achar_pano(page, lat, lng, heading=None) -> tuple | None:
    """Abre o pano na coordenada (com heading opcional). Retorna (cam_lat, cam_lng)
    lidos da URL, ou None se não houver pano."""
    q = (f"https://www.google.com/maps/@?api=1&map_action=pano"
         f"&viewpoint={lat},{lng}&hl=pt-BR")
    if heading is not None:
        q += f"&heading={heading:.0f}&pitch=5&fov=80"
    await page.goto(q, wait_until="domcontentloaded", timeout=30000)
    # o pano real redireciona para /maps/@cam_lat,cam_lng,3a,...  ('3a,' = panorama)
    fim = time.time() + 12
    while time.time() < fim:
        m = _RE_CAM.search(page.url)
        if m:
            return float(m.group(1)), float(m.group(2))
        await page.wait_for_timeout(400)
    return None


async def _capturar(page, alvo: dict) -> str | None:
    """Captura o pano ENCARANDO a fachada (heading câmera→estabelecimento).
    Retorna nome do arquivo salvo, 'NA' se sem pano."""
    lat, lng = alvo["lat"], alvo["lng"]
    try:
        # 1) abre sem heading só para descobrir a posição da câmera (o pano)
        cam = await _achar_pano(page, lat, lng, None)
        if not cam:
            return "NA"
        # 2) calcula o ângulo câmera→estabelecimento e reabre encarando a fachada
        heading = _bearing(cam[0], cam[1], lat, lng)
        cam2 = await _achar_pano(page, lat, lng, heading)
        if not cam2:  # fallback: fica com a vista padrão já carregada
            await _achar_pano(page, lat, lng, None)
        await page.wait_for_timeout(2500)  # tiles do panorama carregarem
        arq = f"{alvo['id']}.jpg"
        await page.screenshot(path=str(SV_DIR / arq), type="jpeg", quality=72,
                              clip={"x": 0, "y": 64, "width": 1280, "height": 656})
        return arq
    except Exception:
        return None


async def worker(wid, fila: asyncio.Queue, ctx, counter, total, lock):
    page = await ctx.new_page()
    await page.set_viewport_size({"width": 1280, "height": 800})
    conn = realtime_ingest.conectar()
    try:
        while True:
            try:
                alvo = fila.get_nowait()
            except asyncio.QueueEmpty:
                return
            res = await _capturar(page, alvo)
            async with lock:
                counter["n"] += 1
                if res and res != "NA":
                    counter["ok"] += 1
                    _gravar_path(alvo["id"], res, conn)
                elif res == "NA":
                    counter["na"] += 1
                    _gravar_path(alvo["id"], "NA", conn)
                print(f"📸 POIs {counter['n']}/{total} | capturados {counter['ok']} | "
                      f"sem pano {counter['na']} | {alvo['nome'][:34]}", flush=True)
            await page.wait_for_timeout(500)
    finally:
        conn.close()
        try:
            await page.close()
        except Exception:
            pass


async def run(workers: int, limit: int, refazer: bool, ids: list = None):
    SV_DIR.mkdir(exist_ok=True)
    alvos = carregar_alvos(limit, refazer, ids)
    total = len(alvos)
    print(f"📸 Street View: {total} POIs para capturar | workers: {workers}", flush=True)
    if not total:
        print("Nada a capturar — todos os POIs válidos já têm print (ou 'NA').")
        return

    fila = asyncio.Queue()
    for a in alvos:
        fila.put_nowait(a)
    counter = {"n": 0, "ok": 0, "na": 0}
    lock = asyncio.Lock()
    ini = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="pt-BR", user_agent=UA)
        await ctx.add_cookies(CONSENT_COOKIES)
        await asyncio.gather(*[worker(i, fila, ctx, counter, total, lock)
                               for i in range(workers)])
        await browser.close()

    print(f"\n{'═'*52}")
    print(f"📸 Street View | Resumo")
    print(f"{'═'*52}")
    print(f"   Capturados : {counter['ok']}/{total}")
    print(f"   Sem pano   : {counter['na']}")
    print(f"   Tempo      : {(time.time()-ini)/60:.1f} min")
    print(f"   💾 {SV_DIR}")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--refazer", action="store_true", help="Recaptura mesmo quem já tem print")
    p.add_argument("--ids", default="", help="POIs específicos, ex: 1644,31861")
    a = p.parse_args()
    ids = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else []
    asyncio.run(run(a.workers, a.limit, a.refazer, ids))


if __name__ == "__main__":
    main()
