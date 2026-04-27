"""
search_pois.py — Pesquisa POIs no Google Maps via Playwright
Salva incrementalmente. Retoma de onde parou.

FLUXO:
  N2: Digita lat,lng na barra de busca apenas para posicionar o Maps,
      clica em "Próximo" → digita nome → Enter → painel ou lista.
      O N1 foi removido: não extrai POI direto pela coordenada.

USO:
  py search_pois.py capturas/santamaria1/session.json --workers 1
"""

import json
import math
import asyncio
import argparse
import re
import random
import time
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, Page
from proxy_manager import ProxyManager, BASE_PORT

MAX_DIST_M   = 100
MIN_OCR_LEN  = 5
WORKERS      = 3
SIMILARIDADE = 0.80
CIDADE       = 'Brasil'

VIEWPORT_W = 1920
VIEWPORT_H = 1080


def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p) * math.cos(lat2*p) * math.sin((lng2-lng1)*p/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def similaridade(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def salvar_json(out_json: Path, results: list):
    out_json.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8'
    )


def log_erro(worker_id: int, nivel: int, etapa: str, erro: str, nome: str):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"\n  [{ts}] W{worker_id} N{nivel} ❌ {etapa}: {erro[:120]} | POI: {nome[:40]}")


def coords_from_url(url: str):
    m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
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
        rows  = page.locator('table.eK4R0e tr.y0skZc')
        count = await rows.count()
        horarios = {}
        for i in range(count):
            row  = rows.nth(i)
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
    try:
        review_el = page.locator('div.jftiEf, div.wiI7pd').first
        data['ultima_avaliacao'] = (await review_el.inner_text(timeout=3000)).strip()[:300]
    except:
        data['ultima_avaliacao'] = ''
    try:
        fotos = []
        imgs  = page.locator('div.RZ66Rb img, button.K4UgGe img, img.DaSXdd')
        count = await imgs.count()
        for i in range(min(count, 10)):
            src = await imgs.nth(i).get_attribute('src', timeout=1000)
            if src and 'googleusercontent' in src and src not in fotos:
                fotos.append(src)
        data['fotos'] = fotos
    except:
        data['fotos'] = []

    url = page.url
    lat, lng = coords_from_url(url)
    data['maps_lat'] = lat
    data['maps_lng'] = lng
    data['maps_url'] = url
    return data


async def aguarda_painel_ou_lista(page: Page) -> str:
    try:
        await page.locator('h1.DUwDvf').first.wait_for(state='visible', timeout=5000)
        return 'painel'
    except:
        pass
    try:
        await page.locator('div[role="feed"]').wait_for(state='visible', timeout=3000)
        return 'lista'
    except:
        pass
    return 'nada'


async def selecionar_melhor_card(page: Page, nome_ocr: str, orig_lat: float, orig_lng: float) -> dict | None:
    SELETORES_CARD = ['div[role="feed"] div[role="article"]', 'div.Nv2PK']
    cards = None
    for sel in SELETORES_CARD:
        try:
            els = page.locator(sel)
            if await els.count() > 0:
                cards = els
                break
        except:
            continue
    if not cards:
        return None

    count        = await cards.count()
    melhor_card  = None
    melhor_score = -1.0

    for i in range(min(count, 8)):
        card = cards.nth(i)
        try:
            nome_card = ''
            for ns in ['div.qBF1Pd', 'span.fontHeadlineSmall', 'div.fontHeadlineSmall']:
                try:
                    nome_card = await card.locator(ns).first.inner_text(timeout=800)
                    if nome_card:
                        break
                except:
                    continue
            if not nome_card:
                continue

            sim = similaridade(nome_ocr, nome_card)
            dist_card = 9999.0
            try:
                href = await card.locator('a[href*="/maps/place/"]').first.get_attribute('href', timeout=800)
                m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', href or '')
                if m:
                    dist_card = haversine(orig_lat, orig_lng, float(m.group(1)), float(m.group(2)))
            except:
                pass

            prox_score = max(0, 1 - dist_card / 2000)
            score = sim * 0.7 + prox_score * 0.3

            if score > melhor_score and sim >= 0.60:
                melhor_score = score
                melhor_card  = card
        except:
            continue

    if melhor_card is None:
        return None

    try:
        link = melhor_card.locator('a[href*="/maps/place/"]').first
        await link.click(timeout=3000)
    except:
        await melhor_card.click()

    try:
        await page.locator('h1.DUwDvf').first.wait_for(state='visible', timeout=6000)
        return await extract_panel(page)
    except:
        return None


async def abrir_coordenada_para_n2(page: Page, lat: float, lng: float) -> str:
    """
    Apenas posiciona o Google Maps na coordenada para habilitar o botão "Próximo".
    Não extrai POI direto pela coordenada. Este é o antigo passo preparatório do N1,
    mas sem resultado N1.
    """
    try:
        query = f"{lat},{lng}"
        try:
            box = page.locator('input[name="q"], input[role="combobox"]').first
            await box.wait_for(state='visible', timeout=5000)
            await box.click()
            await page.keyboard.press('Control+a')
            await page.keyboard.type(query, delay=40)
        except:
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(300)
            await page.keyboard.type(query, delay=40)

        await page.wait_for_timeout(300)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(2500)
        return 'ok'

    except Exception as e:
        return f"abrir coordenada falhou: {e}"


async def nivel2(page: Page, lat: float, lng: float, nome_ocr: str, wid: int) -> tuple[dict | None, str]:
    try:
        SELETORES_PROXIMO = [
            'button[data-value="Próximo"]',
            'button[aria-label="Próximo"]',
            'button:has-text("Próximo")',
        ]
        clicou = False
        for sel in SELETORES_PROXIMO:
            try:
                el = page.locator(sel).first
                await el.wait_for(state='visible', timeout=3000)
                await el.click()
                clicou = True
                break
            except:
                continue

        if not clicou:
            return None, "botão Próximo não encontrado"

        await page.wait_for_timeout(800)
        await page.keyboard.press('Control+a')
        await page.keyboard.type(nome_ocr, delay=40)
        await page.wait_for_timeout(300)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(2000)

        resultado = await aguarda_painel_ou_lista(page)

        if resultado == 'painel':
            poi = await extract_panel(page)
            return poi, 'ok'
        elif resultado == 'lista':
            poi = await selecionar_melhor_card(page, nome_ocr, lat, lng)
            if poi:
                return poi, 'ok'
            return None, "nenhum card com similaridade suficiente"
        else:
            return None, "nem painel nem lista apareceram"

    except Exception as e:
        return None, f"nivel2 falhou: {e}"


async def search_one(page: Page, reg: dict, max_dist: float, wid: int) -> dict:
    nome     = reg.get('ocr_texto', '').strip()
    orig_lat = reg['lat']
    orig_lng = reg['lng']

    result = {
        **reg,
        'nivel': None,
        'status': 'nao_encontrado',
        'distancia_m': None,
        'match_valido': False,
        'similaridade': 0,
        'erros': [],
        'poi': {}
    }

    def montar_result(poi, nivel):
        dist     = None
        nome_poi = (poi or {}).get('nome', '')
        sim      = similaridade(nome, nome_poi) if nome and nome_poi else 0
        if poi and poi.get('maps_lat') and poi.get('maps_lng'):
            dist = haversine(orig_lat, orig_lng, poi['maps_lat'], poi['maps_lng'])
        result['poi']          = poi or {}
        result['nivel']        = nivel
        result['distancia_m']  = round(dist, 1) if dist is not None else None
        result['similaridade'] = round(sim, 3)
        match_dist = dist is not None and dist <= max_dist
        match_nome = dist is not None and dist <= 2000 and sim >= 0.90
        result['match_valido'] = match_dist or match_nome
        result['status']       = 'ok' if result['match_valido'] else 'distancia_alta'

    try:
        motivo_coord = await abrir_coordenada_para_n2(page, orig_lat, orig_lng)
        if motivo_coord != 'ok':
            result['erros'].append(f"PREP_N2: {motivo_coord}")
            log_erro(wid, 2, 'prep_n2', motivo_coord, nome)
            return result

        poi2, motivo2 = await nivel2(page, orig_lat, orig_lng, nome, wid)
        if motivo2 != 'ok':
            result['erros'].append(f"N2: {motivo2}")
            log_erro(wid, 2, 'nivel2', motivo2, nome)

        if poi2 and poi2.get('nome'):
            montar_result(poi2, 2)

    except Exception as e:
        result['status'] = 'erro'
        result['erros'].append(f"exceção: {e}")
        log_erro(wid, 0, 'search_one', str(e), nome)

    return result


async def worker(worker_id: int, queue: list, state: dict,
                 pw, max_dist: float,
                 counter: dict, total: int,
                 out_json: Path, lock: asyncio.Lock,
                 n_workers: int = 3, proxy_mgr=None):

    porta_local = proxy_mgr.porta(worker_id) if proxy_mgr else BASE_PORT + (worker_id % n_workers)

    browser = await pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            f'--window-size={VIEWPORT_W},{VIEWPORT_H}',
            '--disable-blink-features=AutomationControlled',
            *( [f'--proxy-server=http://127.0.0.1:{porta_local}'] if porta_local else [] ),
        ],
    )
    ctx  = await browser.new_context(
        viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H},
        locale='pt-BR',
    )
    page = await ctx.new_page()

    # Abre o Maps uma vez por worker — tenta até 3 vezes
    maps_ok = False
    for _t in range(3):
        try:
            await page.goto('https://www.google.com/maps', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(2000)
            try:
                btn = page.locator('button[aria-label*="Aceitar"]').first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
            except:
                pass
            maps_ok = True
            break
        except Exception as e:
            print(f"\n  W{worker_id} Maps tentativa {_t+1}/3: {str(e)[:60]}")
            await asyncio.sleep(3)
    if not maps_ok:
        print(f"\n  W{worker_id} Maps nao carregou — worker abortado")
        return

    items_done = 0

    for reg in queue:
        # Verifica se página ainda está viva
        try:
            await page.title()
        except:
            try:
                await page.close()
                await ctx.close()
                await browser.close()
            except:
                pass
            browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox',
                      f'--window-size={VIEWPORT_W},{VIEWPORT_H}',
                      '--disable-blink-features=AutomationControlled',
                      f'--proxy-server=http://127.0.0.1:{porta_local}'],
            )
            ctx  = await browser.new_context(viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H}, locale='pt-BR')
            page = await ctx.new_page()
            await page.goto('https://www.google.com/maps', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(2000)

        res = await search_one(page, reg, max_dist, worker_id)

        async with lock:
            state['results'].append(res)
            state['results'].sort(key=lambda x: x.get('idx', 0))
            salvar_json(out_json, state['results'])

        items_done += 1
        counter['done'] += 1
        done   = counter['done']
        pct    = done / total * 100
        st     = res['status']
        nivel  = res.get('nivel') or '-'
        dist   = res['distancia_m']
        dist_s = f"{dist:.0f}m" if dist is not None else '—'
        nome   = reg.get('ocr_texto', '')[:35]
        print(f"\r[{done:6}/{total}] {pct:5.1f}% W{worker_id} N{nivel} {st:16} {dist_s:7} {nome}", flush=True)

        # Rotaciona proxy a cada 10 itens
        if proxy_mgr and items_done % 10 == 0:
            trocou = await proxy_mgr.registrar_item(worker_id, bloqueado=False)
            if trocou:
                try:
                    await page.close()
                    await ctx.close()
                    await browser.close()
                except:
                    pass
                porta_local = proxy_mgr.porta(worker_id) if proxy_mgr else BASE_PORT + (worker_id % n_workers)
                browser = await pw.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox',
                          f'--window-size={VIEWPORT_W},{VIEWPORT_H}',
                          '--disable-blink-features=AutomationControlled',
                          f'--proxy-server=http://127.0.0.1:{porta_local}'],
                )
                ctx  = await browser.new_context(viewport={'width': VIEWPORT_W, 'height': VIEWPORT_H}, locale='pt-BR')
                page = await ctx.new_page()
                await page.goto('https://www.google.com/maps', wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(2000)

        await asyncio.sleep(random.uniform(0.8, 2.0))

    try:
        await page.close()
        await ctx.close()
        await browser.close()
    except:
        pass


async def run(session_path: Path, n_workers: int, max_dist: float):
    crops_json = session_path.parent / 'crops' / 'ocr_resultado.json'
    if not crops_json.exists():
        print(f"Erro: {crops_json} não encontrado. Rode ocr_pois.py primeiro.")
        return

    todos    = json.loads(crops_json.read_text(encoding='utf-8'))
    out_json = session_path.parent / 'crops' / 'search_resultado.json'

    # Detecta cidade
    global CIDADE
    try:
        session_cfg = json.loads(session_path.read_text(encoding='utf-8'))
        bbox        = session_cfg.get('config', {}).get('boundingBox', {})
        centro_lat  = (bbox.get('north', 0) + bbox.get('south', 0)) / 2
        centro_lng  = (bbox.get('east',  0) + bbox.get('west',  0)) / 2
        import urllib.request
        url = f"https://nominatim.openstreetmap.org/reverse?lat={centro_lat}&lon={centro_lng}&format=json&zoom=10"
        req = urllib.request.Request(url, headers={'User-Agent': 'ComercialRadar/1.0'})
        geo = json.loads(urllib.request.urlopen(req, timeout=5).read())
        addr = geo.get('address', {})
        cidade_nome = addr.get('city') or addr.get('town') or addr.get('village') or ''
        estado_nome = addr.get('state', '')
        if cidade_nome:
            CIDADE = f"{cidade_nome} {estado_nome}".strip()
            print(f"   📍 Cidade: {CIDADE}")
    except:
        pass

    # Retomada
    processados_idx    = set()
    results_existentes = []

    if out_json.exists():
        try:
            results_existentes = json.loads(out_json.read_text(encoding='utf-8'))
            processados_idx    = {r['idx'] for r in results_existentes if 'idx' in r}
            print(f"\n♻️  Retomando: {len(processados_idx)} itens já processados.")
        except:
            pass

    fila_completa = [r for r in todos if len(r.get('ocr_texto', '').strip()) >= MIN_OCR_LEN]
    fila          = [r for r in fila_completa if r.get('idx') not in processados_idx]
    ignorados     = len(todos) - len(fila_completa)

    ocr_curtos = [
        {**r, 'nivel': None, 'status': 'ocr_curto', 'match_valido': False,
         'distancia_m': None, 'similaridade': 0, 'erros': [], 'poi': {}}
        for r in todos
        if len(r.get('ocr_texto', '').strip()) < MIN_OCR_LEN
        and r.get('idx') not in processados_idx
    ]

    print(f"\n🔍 ComercialRadar — Search POIs")
    print(f"   Sessão    : {session_path.parent.name}")
    print(f"   Total OCR : {len(todos)} | Na fila: {len(fila)} | Ignorados: {ignorados}")
    print(f"   Workers   : {n_workers} | Dist máx: {max_dist}m\n")

    if not fila and not ocr_curtos:
        print("✅ Todos os itens já foram processados.")
        _gerar_html(results_existentes, session_path)
        return

    state   = {'results': list(results_existentes)}
    lock    = asyncio.Lock()
    counter = {'done': len(processados_idx)}
    total   = len(fila_completa)

    if ocr_curtos:
        async with lock:
            state['results'].extend(ocr_curtos)
            state['results'].sort(key=lambda x: x.get('idx', 0))
            salvar_json(out_json, state['results'])

    fatias = [fila[i::n_workers] for i in range(n_workers)]

    proxy_mgr = ProxyManager(n_workers=n_workers)
    await proxy_mgr.start()

    async with async_playwright() as pw:
        await asyncio.gather(*[
            worker(i, fatias[i], state, pw, max_dist, counter, total, out_json, lock, n_workers, proxy_mgr)
            for i in range(n_workers) if fatias[i]
        ])

    await proxy_mgr.stop()

    print(f"\n\n💾 JSON: {out_json}")
    final     = state['results']
    ok        = sum(1 for r in final if r.get('status') == 'ok')
    alta      = sum(1 for r in final if r.get('status') == 'distancia_alta')
    nao_enc   = sum(1 for r in final if r.get('status') == 'nao_encontrado')
    ocr_c     = sum(1 for r in final if r.get('status') == 'ocr_curto')
    erros     = sum(1 for r in final if r.get('status') == 'erro')

    print(f"\n📊 Resumo:")
    print(f"   ✅ Match válido (N2)    : {ok}")
    print(f"   ⚠️  Distância alta       : {alta}")
    print(f"   ❌ Não encontrado        : {nao_enc}")
    print(f"   🔤 OCR curto (<{MIN_OCR_LEN})       : {ocr_c}")
    print(f"   💥 Erros                 : {erros}")

    _gerar_html(final, session_path)


def _gerar_html(results: list, session_path: Path):
    rows = ''
    for r in results:
        poi    = r.get('poi', {})
        st     = r.get('status', '')
        nivel  = r.get('nivel') or '—'
        dist   = r.get('distancia_m')
        dist_s = f"{dist:.0f}m" if dist is not None else '—'
        match  = r.get('match_valido', False)
        erros  = r.get('erros', [])
        bg     = '#0a2a0a' if match else ('#2a1a00' if st == 'distancia_alta' else '#2a0a0a')

        erros_html    = ''.join(f'<div style="color:#f66;font-size:0.7em">{e}</div>' for e in erros)
        horarios_html = ''.join(f"<div>{d}: {h}</div>" for d, h in (poi.get('horarios') or {}).items())
        fotos_html    = ''.join(f'<img src="{f}" style="height:60px;margin:2px;border-radius:4px">' for f in (poi.get('fotos') or [])[:4])

        rows += f"""
        <tr style="background:{bg}">
          <td style="font-size:0.8em;color:#888">{r.get('idx','')}<br>N{nivel}</td>
          <td style="font-size:1em;font-weight:bold;color:#ffe">{r.get('ocr_texto','')}{erros_html}</td>
          <td style="color:{'#4fc' if match else '#f84'}">{st}<br><b>{dist_s}</b></td>
          <td style="color:#eee"><b>{poi.get('nome','')}</b><br><span style="color:#aaa;font-size:0.85em">{poi.get('categoria','')}</span></td>
          <td style="color:#fd8">{'⭐ '+poi.get('avaliacao','')+' ('+str(poi.get('total_avaliacoes',''))+')' if poi.get('avaliacao') else '—'}</td>
          <td style="font-size:0.8em;color:#ccc">{poi.get('endereco','')}<br><span style="color:#888">{poi.get('telefone','')}</span></td>
          <td style="font-size:0.75em;color:#aaa">{poi.get('status_horario','')}<br>{horarios_html}</td>
          <td style="font-size:0.75em;color:#888">{poi.get('website','')}</td>
          <td style="font-size:0.75em;color:#ccc">{(poi.get('ultima_avaliacao','') or '')[:100]}</td>
          <td>{fotos_html}</td>
          <td style="font-size:0.75em;color:#666">{r['lat']:.5f},{r['lng']:.5f}<br>{'→ '+str(poi.get('maps_lat',''))+','+str(poi.get('maps_lng','')) if poi.get('maps_lat') else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Search POIs — ComercialRadar</title>
<style>
  body{{font-family:sans-serif;background:#111;color:#eee;margin:0}}
  h1{{padding:14px;color:#4fc;margin:0;font-size:1.2em}}
  table{{border-collapse:collapse;width:100%}}
  th{{background:#222;padding:8px;text-align:left;font-size:0.8em;position:sticky;top:0}}
  td{{border-bottom:1px solid #1e1e1e;padding:8px;vertical-align:top}}
</style></head><body>
<h1>🗺 Search POIs — {session_path.parent.name} — {datetime.now().strftime('%d/%m/%Y %H:%M')}</h1>
<table>
  <tr><th>#/N</th><th>OCR/Erros</th><th>Status/Dist</th><th>Nome Maps</th><th>Avaliação</th>
      <th>Endereço/Tel</th><th>Horários</th><th>Website</th><th>Última Avaliação</th><th>Fotos</th><th>Coords</th></tr>
  {rows}
</table></body></html>"""

    out_html = session_path.parent / 'crops' / 'search_resumo.html'
    out_html.write_text(html, encoding='utf-8')
    print(f"🌐 HTML: {out_html}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session', help='Caminho para session.json')
    parser.add_argument('--workers', type=int, default=WORKERS)
    parser.add_argument('--max-dist', type=float, default=MAX_DIST_M)
    parser.add_argument('--reprocessar-erros', action='store_true')
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        return

    asyncio.run(run(session_path, args.workers, args.max_dist))


if __name__ == '__main__':
    main()
