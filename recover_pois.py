"""
recover_pois.py — Tenta recuperar POIs que falharam no search_pois.py
Para cada falha (distância alta, não encontrado, OCR curto):
  1. Abre o Maps na coordenada exata do ícone detectado
  2. Clica no centro e em cruz (N/S/L/O a ~30px) tentando abrir painel de POI
  3. Extrai dados se encontrar algo
  4. Salva recover_resultado.json mesclado com os matches válidos do search

USO:
  py recover_pois.py capturas/teresinabairro1/session.json
  py recover_pois.py capturas/teresinabairro1/session.json --workers 5 --max-dist 100
"""

import json
import math
import asyncio
import time
import argparse
import re
import random
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, Page, BrowserContext
from proxy_manager import ProxyManager, BASE_PORT

MAPS_MAP_ID  = '33696f50cbe8e2d228094f61'
MAPS_API_KEY = 'AIzaSyA0BLzeqU8_-ksq8QSfKbm0ObmMyRqoSmY'
MAX_DIST_M   = 100
WORKERS      = 10

# Zoom alto pra ver POIs individuais
ZOOM = 19

# Offsets de clique em px (centro, depois cruz)
CLICK_OFFSETS = [
    (0,   0),    # centro exato
    (0,  -30),   # norte
    (0,   30),   # sul
    (30,   0),   # leste
    (-30,  0),   # oeste
    (20, -20),   # NE
    (-20,-20),   # NO
    (20,  20),   # SE
    (-20, 20),   # SO
]

VIEWPORT_W = 1280
VIEWPORT_H = 900


def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p) * math.cos(lat2*p) * math.sin((lng2-lng1)*p/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def coords_from_url(url: str):
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


async def extract_panel(page: Page) -> dict:
    data = {}
    try:
        data['nome'] = await page.locator('h1.DUwDvf').first.inner_text(timeout=5000)
    except:
        data['nome'] = ''

    try:
        data['categoria'] = await page.locator('button.DkEaL').first.inner_text(timeout=3000)
    except:
        data['categoria'] = ''

    try:
        data['avaliacao'] = await page.locator('div.F7nice span[aria-hidden="true"]').first.inner_text(timeout=3000)
    except:
        data['avaliacao'] = ''

    try:
        txt = await page.locator('div.F7nice span[role="img"]').first.get_attribute('aria-label', timeout=3000)
        m = re.search(r'(\d+)', txt or '')
        data['total_avaliacoes'] = int(m.group(1)) if m else 0
    except:
        data['total_avaliacoes'] = 0

    try:
        data['endereco'] = await page.locator('button[data-item-id="address"] div.Io6YTe').inner_text(timeout=3000)
    except:
        data['endereco'] = ''

    try:
        data['telefone'] = await page.locator('button[data-item-id^="phone"] div.Io6YTe').inner_text(timeout=3000)
    except:
        data['telefone'] = ''

    try:
        data['website'] = await page.locator('a[data-item-id="authority"] div.Io6YTe').inner_text(timeout=3000)
    except:
        data['website'] = ''

    try:
        data['status_horario'] = await page.locator('span.ZDu9vd span').first.inner_text(timeout=3000)
    except:
        data['status_horario'] = ''

    try:
        toggle = page.locator('div.OMl5r.hH0dDd')
        if await toggle.count() > 0:
            expanded = await toggle.get_attribute('aria-expanded')
            if expanded != 'true':
                await toggle.click()
                await page.wait_for_timeout(600)
        rows = page.locator('table.eK4R0e tr.y0skZc')
        count = await rows.count()
        horarios = {}
        for i in range(count):
            row = rows.nth(i)
            dia  = await row.locator('td.ylH6lf').inner_text(timeout=2000)
            hora = await row.locator('td.mxowUb').get_attribute('aria-label', timeout=2000)
            horarios[dia.strip()] = hora.strip() if hora else ''
        data['horarios'] = horarios
    except:
        data['horarios'] = {}

    try:
        data['plus_code'] = await page.locator('button[data-item-id="oloc"] div.Io6YTe').inner_text(timeout=3000)
    except:
        data['plus_code'] = ''

    url = page.url
    lat, lng = coords_from_url(url)
    data['maps_lat'] = lat
    data['maps_lng'] = lng
    data['maps_url'] = url
    return data


async def recover_one(page: Page, reg: dict, max_dist: float) -> dict:
    orig_lat = reg['lat']
    orig_lng = reg['lng']

    t_inicio = time.time()
    result = {
        **reg,
        'recover_status': 'nao_encontrado',
        'distancia_m': None,
        'match_valido': False,
        'poi': reg.get('poi', {}),
        'processado_em': datetime.now().isoformat(timespec='seconds'),
        'duracao_s': None,
    }

    # Abre Maps na coordenada exata com zoom alto
    maps_url = (
        f"https://www.google.com/maps/@{orig_lat},{orig_lng},{ZOOM}z"
        f"?entry=ttu"
    )

    try:
        await page.goto(maps_url, wait_until='domcontentloaded', timeout=20000)
        await page.wait_for_timeout(3000)  # aguarda POIs renderizarem

        cx = VIEWPORT_W // 2
        cy = VIEWPORT_H // 2

        for dx, dy in CLICK_OFFSETS:
            x = cx + dx
            y = cy + dy

            await page.mouse.click(x, y)
            await page.wait_for_timeout(1200)

            # Verifica se abriu painel de POI
            try:
                await page.locator('h1.DUwDvf').first.wait_for(timeout=2500)
            except:
                continue  # não abriu painel, tenta próximo offset

            # Painel aberto — extrai dados
            poi = await extract_panel(page)

            dist = None
            if poi.get('maps_lat') and poi.get('maps_lng'):
                dist = haversine(orig_lat, orig_lng, poi['maps_lat'], poi['maps_lng'])

            result['poi']            = poi
            result['distancia_m']    = round(dist, 1) if dist is not None else None
            result['match_valido']   = dist is not None and dist <= max_dist
            result['recover_status'] = 'ok' if result['match_valido'] else 'distancia_alta'
            result['recover_offset'] = (dx, dy)
            break  # encontrou — para os cliques

    except Exception as e:
        result['recover_status'] = f'erro: {str(e)[:80]}'

    result['duracao_s'] = round(time.time() - t_inicio, 1)
    return result


async def worker(worker_id: int, queue: list, results: list,
                 pw, max_dist: float,
                 counter: dict, total: int,
                 lock: asyncio.Lock, out_json: Path,
                 n_workers: int = 10, proxy_mgr=None):
    porta_local = BASE_PORT + (worker_id % n_workers)
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox', '--disable-setuid-sandbox',
            f'--window-size={VIEWPORT_W},{VIEWPORT_H}',
            '--disable-blink-features=AutomationControlled',
            f'--proxy-server=http://127.0.0.1:{porta_local}',
        ],
    )
    ctx  = await browser.new_context(
        viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H},
        locale='pt-BR',
    )
    page = await ctx.new_page()

    for reg in queue:
        res = await recover_one(page, reg, max_dist)

        async with lock:
            results.append(res)
            results.sort(key=lambda x: x.get('idx', 0))
            out_json.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8'
            )

        counter['done'] += 1
        done = counter['done']
        st   = res['recover_status']
        dist = res['distancia_m']
        dist_s = f"{dist:.0f}m" if dist is not None else '—'
        nome   = reg.get('ocr_texto', '') or reg.get('poi', {}).get('nome', '')
        print(f"\r[{done:5}/{total}] W{worker_id} {st:18} {dist_s:8} {nome[:40]}", flush=True)

        # Rotaciona proxy a cada 10 itens ou em caso de bloqueio
        if proxy_mgr:
            erros_str = str(res.get('erros', [])).lower()
            bloqueado = 'sorry' in erros_str or erros_str.count('timeout') >= 2
            trocou = await proxy_mgr.registrar_item(worker_id, bloqueado=bloqueado)
            if trocou:
                try:
                    await page.close()
                    await ctx.close()
                    await browser.close()
                except: pass
                porta_local = proxy_mgr.porta(worker_id)
                browser = await pw.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox',
                          f'--window-size={VIEWPORT_W},{VIEWPORT_H}',
                          '--disable-blink-features=AutomationControlled',
                          f'--proxy-server=http://127.0.0.1:{porta_local}'],
                )
                ctx  = await browser.new_context(
                    viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H},
                    locale='pt-BR',
                )
                page = await ctx.new_page()

        await asyncio.sleep(random.uniform(1.5, 3.5))

    await page.close()
    await ctx.close()
    await browser.close()


async def run(session_path: Path, n_workers: int, max_dist: float):
    search_json = session_path.parent / 'crops' / 'search_resultado.json'
    if not search_json.exists():
        print(f"Erro: {search_json} não encontrado. Rode search_pois.py primeiro.")
        return

    todos = json.loads(search_json.read_text(encoding='utf-8'))

    # Separa: já ok não precisa recuperar
    validos  = [r for r in todos if r.get('match_valido')]
    falhas   = [r for r in todos if not r.get('match_valido')]

    print(f"\n🔧 ComercialRadar — Recover POIs")
    print(f"   Sessão   : {session_path.parent.name}")
    print(f"   Válidos  : {len(validos)} (já ok, não reprocessados)")
    print(f"   Falhas   : {len(falhas)} (serão recuperados)")
    print(f"   Workers  : {n_workers}\n")

    fatias  = [falhas[i::n_workers] for i in range(n_workers)]
    results = []
    counter = {'done': 0}

    out_json = session_path.parent / 'crops' / 'recover_resultado.json'

    proxy_mgr = ProxyManager(n_workers=n_workers)
    await proxy_mgr.start()

    lock = asyncio.Lock()
    sessao_inicio = datetime.now()

    async with async_playwright() as pw:
        await asyncio.gather(*[
            worker(i, fatias[i], results, pw, max_dist, counter, len(falhas),
                   lock, out_json, n_workers, proxy_mgr)
            for i in range(n_workers) if fatias[i]
        ])

    await proxy_mgr.stop()
    sessao_fim = datetime.now()
    duracao = sessao_fim - sessao_inicio
    h = int(duracao.total_seconds()//3600)
    m = int((duracao.total_seconds()%3600)//60)
    s = int(duracao.total_seconds()%60)

    # Mescla: válidos originais + recuperados
    final = validos + results
    final.sort(key=lambda x: x.get('idx', 0))

    meta = {
        'iniciado_em': sessao_inicio.isoformat(timespec='seconds'),
        'concluido_em': sessao_fim.isoformat(timespec='seconds'),
        'duracao': f"{h:02d}:{m:02d}:{s:02d}",
        'duracao_s': round(duracao.total_seconds(), 1),
        'total_falhas': len(falhas),
        'recuperados': sum(1 for r in results if r.get('recover_status') == 'ok'),
        'workers': n_workers,
    }
    (out_json.parent / 'recover_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"\n\n💾 JSON: {out_json}")
    print(f"⏱  Duração: {h:02d}:{m:02d}:{s:02d} | {len(falhas)} falhas | {n_workers} workers")

    ok_rec  = sum(1 for r in results if r.get('recover_status') == 'ok')
    alta    = sum(1 for r in results if r.get('recover_status') == 'distancia_alta')
    nao_enc = sum(1 for r in results if r.get('recover_status') == 'nao_encontrado')
    erros   = sum(1 for r in results if str(r.get('recover_status','')).startswith('erro'))

    print(f"\n📊 Resumo recover:")
    print(f"   ✅ Recuperados válidos : {ok_rec}")
    print(f"   ⚠️  Distância alta      : {alta}")
    print(f"   ❌ Não encontrado       : {nao_enc}")
    print(f"   💥 Erros               : {erros}")
    print(f"   🏁 Total final válidos : {len(validos) + ok_rec}/{len(final)}")

    _gerar_html(final, session_path)


def _gerar_html(results: list, session_path: Path):
    rows = ''
    for r in results:
        poi    = r.get('poi', {})
        match  = r.get('match_valido', False)
        st     = r.get('recover_status', r.get('status', ''))
        dist   = r.get('distancia_m')
        dist_s = f"{dist:.0f}m" if dist is not None else '—'
        bg     = '#0a2a0a' if match else ('#2a1a00' if 'alta' in st else '#2a0a0a')

        horarios_html = ''.join(
            f"<div>{d}: {h}</div>" for d, h in (poi.get('horarios') or {}).items()
        )
        rows += f"""
        <tr style="background:{bg}">
          <td style="font-size:0.8em;color:#888">{r.get('idx','')}</td>
          <td style="font-size:1em;font-weight:bold;color:#ffe">{r.get('ocr_texto','')}</td>
          <td style="color:{'#4fc' if match else '#f84'}">{st}<br><b>{dist_s}</b></td>
          <td style="color:#eee"><b>{poi.get('nome','')}</b><br>
            <span style="color:#aaa;font-size:0.85em">{poi.get('categoria','')}</span></td>
          <td style="color:#fd8">{'⭐ '+poi.get('avaliacao','')+' ('+str(poi.get('total_avaliacoes',''))+')' if poi.get('avaliacao') else '—'}</td>
          <td style="font-size:0.8em;color:#ccc">{poi.get('endereco','')}<br>
            <span style="color:#888">{poi.get('telefone','')}</span></td>
          <td style="font-size:0.75em;color:#aaa">{poi.get('status_horario','')}<br>{horarios_html}</td>
          <td style="font-size:0.75em;color:#888">{poi.get('website','')}</td>
          <td style="font-size:0.75em;color:#666">{r['lat']:.5f},{r['lng']:.5f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Recover POIs — ComercialRadar</title>
<style>
  body  {{ font-family:sans-serif; background:#111; color:#eee; margin:0; }}
  h1    {{ padding:14px; color:#4fc; margin:0; font-size:1.2em; }}
  table {{ border-collapse:collapse; width:100%; }}
  th    {{ background:#222; padding:8px; text-align:left; font-size:0.8em; position:sticky; top:0; }}
  td    {{ border-bottom:1px solid #1e1e1e; padding:8px; vertical-align:top; }}
</style></head><body>
<h1>🔧 Recover POIs — {session_path.parent.name} — {datetime.now().strftime('%d/%m/%Y %H:%M')}</h1>
<table>
  <tr><th>#</th><th>OCR</th><th>Status/Dist</th><th>Nome Maps</th><th>Avaliação</th>
      <th>Endereço / Tel</th><th>Horários</th><th>Website</th><th>Coords</th></tr>
  {rows}
</table></body></html>"""

    out_html = session_path.parent / 'crops' / 'recover_resumo.html'
    out_html.write_text(html, encoding='utf-8')
    print(f"🌐 HTML: {out_html}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session', help='Caminho para session.json')
    parser.add_argument('--workers', type=int, default=WORKERS)
    parser.add_argument('--max-dist', type=float, default=MAX_DIST_M)
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        return

    asyncio.run(run(session_path, args.workers, args.max_dist))


if __name__ == '__main__':
    main()
