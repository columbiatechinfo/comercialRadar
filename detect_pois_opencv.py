"""
detect_pois_opencv.py — ComercialRadar
1. OpenCV detecta ícones no tile PNG por cor → lat/lng
2. Playwright paralelo navega e clica em cada ícone → dados completos do Maps
3. Ícones sem dados → recorte salvo em {sessao}/crops_fallback/
4. Qwen2-VL processa os recortes e extrai nomes
5. Tudo consolidado em pois_opencv.json

Uso:
  py detect_pois_opencv.py capturas/s_t_6/session.json
  py detect_pois_opencv.py capturas/s_t_6/session.json --workers 3 --click-zoom 21
"""

import sys, json, re, time, math, asyncio, argparse, base64
from io import BytesIO
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
import httpx
from PIL import Image as PILImage
from playwright.async_api import async_playwright, Browser, Page
import endpoints

# ── Configuração ──────────────────────────────────────────────────────────────
VP_W, VP_H   = 1024, 768
CAPTURE_ZOOM = 19
CLICK_ZOOM   = 21
HEADLESS     = True
WORKERS      = 3
DELAY_NAV    = 2.5
DELAY_CLICK  = 1.5

QWEN_URL     = endpoints.VLLM + "/v1/chat/completions"
QWEN_MODEL   = "qwen2-vl"
QWEN_TIMEOUT = 60

QWEN_PROMPT = (
    "Esta é uma captura do Google Maps. "
    "Identifique TODOS os nomes de estabelecimentos, lojas, restaurantes, igrejas, "
    "academias ou qualquer ponto de interesse visível nesta imagem — "
    "incluindo os que estão atrás de botões ou parcialmente visíveis. "
    "Ignore nomes de ruas, avenidas e botões de interface como 'Fazer login'. "
    "Retorne apenas os nomes encontrados separados por vírgula, sem mais nada. "
    "Se não houver nenhum nome de estabelecimento visível, responda: DESCONHECIDO"
)

# ── Cores dos ícones ──────────────────────────────────────────────────────────
COLOR_RANGES = [
    ("laranja",  np.array([5,  120, 120]), np.array([25, 255, 255])),
    ("vermelho", np.array([0,  120, 120]), np.array([5,  255, 255])),
    ("vermelho2",np.array([170,120, 120]), np.array([180,255, 255])),
    ("verde",    np.array([40, 80,  80]),  np.array([85, 255, 255])),
    ("azul",     np.array([95, 80,  80]),  np.array([130,255, 255])),
    ("roxo",     np.array([130,60,  80]),  np.array([160,255, 255])),
    ("cinza",    np.array([0,  0,   50]),  np.array([180,25,  160])),
]

RUA_PREFIXES = ("r. ","av. ","rua ","avenida ","trav.","travessa ",
                "rod.","rodovia ","est.","estrada ","al.","alameda ")

# Itens de UI do Maps que o Qwen lê nas screenshots
UI_BLACKLIST = {
    "restaurantes", "hotéis", "hoteis", "museus", "música", "musica",
    "coisas legais para fazer", "transporte público", "transporte publico",
    "estacionamento", "farmácias", "farmacias", "caixas eletrônicos",
    "fazer login", "fazer login.", "google maps", "camadas", "salvos",
    "recentes", "baixar o aplicativo", "salvar", "rotas", "próximo",
    "compartilhar", "enviar para smartphone", "seu histórico do google maps",
    "adicionar um lugar que está faltando", "adicionar sua empresa",
    "adicionar marcador", "pesquise no google maps",
    "restaurante", "hotel", "museu", "música ao vivo",
    "caminhadas", "academia", "loja de roupa", "loja de roupas",
    # Truncados de UI
    "restaurantes, hotéis", "hotéis, coisas", "restaurantes:",
    "restaurantes, hotel", "baixar o aplicativo", "musi",
}

# ─────────────────────────────────────────────────────────────────────────────

def pixel_to_latlon(px, py, tile_lat, tile_lng, zoom, img_w, img_h):
    lng_per_tile = (img_w * 360) / (256 * (2 ** zoom))
    lat_per_tile = lng_per_tile * (img_h / img_w) / math.cos(math.radians(tile_lat))
    return (
        round(tile_lat - ((py / img_h) - 0.5) * lat_per_tile, 7),
        round(tile_lng + ((px / img_w) - 0.5) * lng_per_tile, 7),
    )


def detect_icons(image_path: Path, min_r=6, max_r=40) -> list:
    img = cv2.imread(str(image_path))
    if img is None: return []
    img_h, img_w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    combined = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for _, lo, hi in COLOR_RANGES:
        combined = cv2.bitwise_or(combined, cv2.inRange(hsv, lo, hi))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)
    blurred  = cv2.GaussianBlur(combined, (9,9), 2)
    circles  = cv2.HoughCircles(blurred, cv2.HOUGH_GRADIENT,
                  dp=1.2, minDist=20, param1=50, param2=18,
                  minRadius=min_r, maxRadius=max_r)
    if circles is None: return []
    icons, seen = [], set()
    for (px, py, r) in np.round(circles[0]).astype(int):
        key = (px//15, py//15)
        if key in seen: continue
        seen.add(key)
        roi = hsv[max(0,py-r):py+r, max(0,px-r):px+r]
        color, best = "desconhecida", 0
        for cname, lo, hi in COLOR_RANGES:
            cnt = cv2.countNonZero(cv2.inRange(roi, lo, hi))
            if cnt > best: best, color = cnt, cname
        # Falso positivo: área sem cor sólida (cruzamento de rua, sombra)
        # Exige que pelo menos 15% dos pixels do ROI sejam da cor detectada
        roi_size = max(1, roi.shape[0] * roi.shape[1])
        if best / roi_size < 0.08:
            continue
        icons.append({"px":int(px),"py":int(py),"raio":int(r),
                      "cor":color,"img_w":img_w,"img_h":img_h})
    return icons


def save_crop(tile_path: Path, ic: dict, crops_dir: Path) -> Path:
    """
    Salva recorte centralizado no ícone com padding fixo de 200px.
    Se o ícone estiver perto da borda, preenche com branco para manter tamanho.
    """
    img = cv2.imread(str(tile_path))
    if img is None: return None

    px, py = ic["px"], ic["py"]
    PAD = 200  # pixels ao redor do ícone — suficiente para ver o nome

    # Extrai região com padding — pode sair fora da imagem
    x0_src = px - PAD; y0_src = py - PAD
    x1_src = px + PAD; y1_src = py + PAD

    # Coordenadas dentro da imagem
    x0 = max(0, x0_src); y0 = max(0, y0_src)
    x1 = min(img.shape[1], x1_src); y1 = min(img.shape[0], y1_src)
    crop_raw = img[y0:y1, x0:x1]

    # Canvas branco do tamanho total (400x400)
    canvas = np.ones((PAD*2, PAD*2, 3), dtype=np.uint8) * 255

    # Posição de colagem no canvas
    cx0 = x0 - x0_src  # offset dentro do canvas
    cy0 = y0 - y0_src
    cx1 = cx0 + crop_raw.shape[1]
    cy1 = cy0 + crop_raw.shape[0]
    canvas[cy0:cy1, cx0:cx1] = crop_raw

    # Desenha cruz vermelha no centro para indicar posição do ícone
    cv2.drawMarker(canvas, (PAD, PAD), (0, 0, 220),
                   cv2.MARKER_CROSS, markerSize=20, thickness=2)

    crop_path = crops_dir / f"crop_{tile_path.stem}_{px}_{py}.jpg"
    cv2.imwrite(str(crop_path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return crop_path


def is_real_name(name: str) -> bool:
    if not name or len(name) < 3: return False
    if any(c in name for c in ['°','″','"S','"N','"W','"E',"'S","'N"]): return False
    if re.match(r'^[-\d.,\s]+$', name): return False
    nl = name.lower().strip(" .")
    if nl in {"esta área","this area","sem nome","unnamed","desconhecido",
              "deconhecido","desconhecida","nenhum","none"}: return False
    if nl in UI_BLACKLIST: return False
    # Descarta se começa com item de UI (ex: "RESTAURANTES: DESCONHECIDO")
    if any(nl.startswith(u) for u in UI_BLACKLIST if len(u) > 5): return False
    if any(nl.startswith(p) for p in RUA_PREFIXES): return False
    if re.search(r',\s*\d+\s*-\s*\w', name): return False
    # Descarta nomes genéricos sem substantivo próprio
    genericos = {"peixaria","açougue","acougue","barbearia","costureira",
                 "perfumaria","papelaria","loja de roupa","loja de roupas",
                 "mercado","padaria","loja","bar","farmácia","farmacia",
                 "comercial","academia"}
    if nl in genericos: return False
    return True


def save_progress(output_dir: Path, session_name: str, pois: list, failed: list, status: str):
    out = {
        "sessao":           session_name,
        "gerado_em":        datetime.now().isoformat(),
        "status":           status,
        "total_pois":       len(pois),
        "total_sem_dados":  len(failed),
        "pois":             pois,
        "sem_dados":        failed,
    }
    (output_dir / "pois_opencv.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Playwright ────────────────────────────────────────────────────────────────

async def dismiss_cookies(page: Page):
    for sel in ['button[aria-label="Aceitar tudo"]','button[aria-label="Accept all"]',
                'form:nth-child(2) button']:
        try:
            btn = await page.query_selector(sel)
            if btn: await btn.click(); await page.wait_for_timeout(600); return
        except: pass


async def get_name(page: Page) -> str | None:
    for sel in ['h1.DUwDvf','h1[class*="fontHeadlineLarge"]','.qBF1Pd']:
        try:
            el = await page.query_selector(sel)
            if el:
                txt = (await el.inner_text()).strip()
                if is_real_name(txt): return txt
        except: pass
    return None


async def extract_panel(page: Page, fallback_lat, fallback_lng) -> dict:
    data = {"nome": await get_name(page) or ""}
    for field, sels in {
        "categoria":      ['.DkEaL','.YhemCb'],
        "endereco":       ['[data-item-id="address"] .Io6YTe'],
        "telefone":       ['[data-item-id*="phone"] .Io6YTe'],
        "horario_status": ['.t39EBf','.OqCZI .Io6YTe'],
        "avaliacao":      ['.F7nice span[aria-hidden="true"]','.MW4etd'],
    }.items():
        for sel in sels:
            try:
                el = await page.query_selector(sel)
                if el: data[field] = (await el.inner_text()).strip(); break
            except: pass
    try:
        el = await page.query_selector('a[data-item-id="authority"]')
        if el: data["site"] = await el.get_attribute("href") or ""
    except: pass
    try:
        el = await page.query_selector('.UY7F9 span')
        if el:
            nums = re.findall(r'[\d.]+', (await el.inner_text()).replace(',','.'))
            if nums: data["total_avaliacoes"] = nums[0]
    except: pass
    await page.wait_for_timeout(300)
    m = re.search(r'!3d([-\d.]+)!4d([-\d.]+)', page.url)
    data["coordenadas"] = ({"lat":float(m.group(1)),"lng":float(m.group(2))}
                           if m else {"lat":fallback_lat,"lng":fallback_lng})
    return data


async def get_coord_from_click(page, x: int, y: int) -> tuple[float, float] | None:
    """Clica em x,y e extrai a coordenada real da URL do Maps."""
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(150)
    await page.mouse.click(x, y)
    await page.wait_for_timeout(700)
    url = page.url
    m = re.search(r"!3d([-\d.]+)!4d([-\d.]+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"@([-\d.]+),([-\d.]+),\d+z", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


async def calibrate_viewport(page) -> dict | None:
    """
    Calibra pixel→coordenada clicando nos 4 cantos do viewport.
    Usa mínimos quadrados para estimar a transformação affine real
    (corrige rotação/skew do Maps que 2 pontos não capturam).
    
    4 pontos: TL, TR, BL, BR → sistema sobredeterminado → mais preciso.
    """
    import numpy as np

    m = 100  # margem dos cantos
    corners = [
        (m,        m       ),  # topo-esquerdo
        (VP_W - m, m       ),  # topo-direito
        (m,        VP_H - m),  # baixo-esquerdo
        (VP_W - m, VP_H - m),  # baixo-direito
    ]

    coords = []
    for cx, cy in corners:
        coord = await get_coord_from_click(page, cx, cy)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(180)
        if coord:
            coords.append((cx, cy, coord[0], coord[1]))

    if len(coords) < 3:
        return None  # precisa de pelo menos 3 pontos

    # Mínimos quadrados: resolve Ax = b para transformação affine
    # px = a*lat + b*lng + c
    # py = d*lat + e*lng + f
    # Invertido: lat = p*px + q*py + r  |  lng = s*px + t*py + u
    A = np.array([[cx, cy, 1] for cx, cy, _, _ in coords], dtype=float)
    b_lat = np.array([lat for _, _, lat, _ in coords])
    b_lng = np.array([lng for _, _, _, lng in coords])

    # Resolve por mínimos quadrados
    coef_lat, _, _, _ = np.linalg.lstsq(A, b_lat, rcond=None)
    coef_lng, _, _, _ = np.linalg.lstsq(A, b_lng, rcond=None)

    # Verifica qualidade: reprojeta os pontos
    errors = []
    for cx, cy, lat, lng in coords:
        lat_pred = coef_lat[0]*cx + coef_lat[1]*cy + coef_lat[2]
        lng_pred = coef_lng[0]*cx + coef_lng[1]*cy + coef_lng[2]
        err_m = ((lat_pred - lat)**2 + (lng_pred - lng)**2)**0.5 * 111320
        errors.append(err_m)

    max_err = max(errors)
    if max_err > 50:  # mais de 50m de erro = calibração ruim
        return None

    return {
        "coef_lat": coef_lat,  # [a, b, c] para lat = a*px + b*py + c
        "coef_lng": coef_lng,  # [d, e, f] para lng = d*px + e*py + f
        "n_points": len(coords),
        "max_err_m": max_err,
    }


def latlon_to_px(lat: float, lng: float, calib: dict) -> tuple[int, int]:
    """
    Converte lat/lng para pixel (x,y) invertendo a transformação affine.
    Resolve o sistema 2x2: [coef_lat[0:2]] [px]   [lat - coef_lat[2]]
                            [coef_lng[0:2]] [py] = [lng - coef_lng[2]]
    """
    import numpy as np
    cl = calib["coef_lat"]
    cn = calib["coef_lng"]
    # A @ [px, py] = b
    A = np.array([[cl[0], cl[1]], [cn[0], cn[1]]])
    b = np.array([lat - cl[2], lng - cn[2]])
    try:
        pxpy = np.linalg.solve(A, b)
        return int(round(pxpy[0])), int(round(pxpy[1]))
    except np.linalg.LinAlgError:
        return VP_W // 2, VP_H // 2


async def process_icon(page, lat: float, lng: float) -> dict | None:
    """
    Navega para o ícone, calibra o viewport com 2 cliques nos cantos,
    calcula a posição exata em pixels e clica diretamente.
    Fallback: até 5 ajustes finos se errar por poucos pixels.
    """
    url = f"https://www.google.com/maps/@{lat},{lng},{CLICK_ZOOM}z"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(int(DELAY_NAV * 1000))
    await dismiss_cookies(page)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)

    if await get_name(page):
        return await extract_panel(page, lat, lng)

    # ── Calibração do viewport ────────────────────────────────────────────────
    calib = await calibrate_viewport(page)

    if calib:
        # Pixel exato do ícone baseado na calibração
        px, py = latlon_to_px(lat, lng, calib)
        px = max(60, min(VP_W - 60, px))
        py = max(60, min(VP_H - 60, py))

        # Clica no pixel calculado + ajustes mínimos para cauda do balão
        for dx, dy in [(0, 0), (0, -12), (0, -22), (8, -8), (-8, -8)]:
            cx, cy = px + dx, py + dy
            if not (50 < cx < VP_W-50 and 60 < cy < VP_H-50): continue
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)
            await page.mouse.click(cx, cy)
            await page.wait_for_timeout(int(DELAY_CLICK * 1000))
            if await get_name(page):
                return await extract_panel(page, lat, lng)
    else:
        # Sem calibração: clique no centro
        await page.mouse.click(VP_W // 2, VP_H // 2)
        await page.wait_for_timeout(int(DELAY_CLICK * 1000))
        if await get_name(page):
            return await extract_panel(page, lat, lng)

    return None


async def worker(worker_id, browser, queue, results, failed_list, lock,
                 output_dir, crops_dir, session_name, tile_paths_map):
    ctx  = await browser.new_context(
        viewport={"width":VP_W,"height":VP_H}, locale="pt-BR",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
        # Bloqueia requisições de autenticação que geram o botão "Fazer login"
        extra_http_headers={"X-Maps-NoLogin": "1"}
    )
    page = await ctx.new_page()

    # Bloqueia JS que injeta o botão "Fazer login" sobre o mapa
    async def block_login(route):
        url = route.request.url
        if any(x in url for x in ["accounts.google", "signin", "CheckCookie",
                                    "GetAccountInfo", "lookup?continue"]):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", block_login)
    await page.goto("https://www.google.com/maps", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await dismiss_cookies(page)

    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        i, total, ic, tile_name = item
        t0 = time.time()

        async with lock:
            print(f"  [W{worker_id}] [{i:3}/{total}] {ic['cor']:8}"
                  f" ({ic['px']:4},{ic['py']:4}) → {ic['lat']:.6f},{ic['lng']:.6f}",
                  end="", flush=True)
        try:
            data = await process_icon(page, ic["lat"], ic["lng"])
            elapsed = time.time() - t0
            if data and data.get("nome"):
                data["tile_origem"] = tile_name
                data["deteccao"]    = {"px":ic["px"],"py":ic["py"],
                                       "raio":ic["raio"],"cor":ic["cor"]}
                async with lock:
                    results.append(data)
                    save_progress(output_dir, session_name, results, failed_list, "em_progresso")
                    print(f" → ✔ {data['nome'][:40]} ({elapsed:.0f}s)")
            else:
                # Salva recorte na pasta da sessão para fallback com Qwen
                tile_path = tile_paths_map.get(tile_name)
                # Salva screenshot do browser (mostra o que o Playwright viu de fato)
                # Isso captura o POI real mesmo que "Fazer login" esteja na frente
                crop_name = f"crop_{tile_name}_{ic['px']}_{ic['py']}.jpg"
                crop_path = crops_dir / crop_name
                try:
                    await page.screenshot(path=str(crop_path), type="jpeg", quality=88,
                                          clip={"x": 0, "y": 0, "width": VP_W, "height": VP_H})
                except:
                    # Fallback: recorte do tile original
                    tile_path_obj = tile_paths_map.get(tile_name)
                    crop_path = save_crop(tile_path_obj, ic, crops_dir) if tile_path_obj else None
                    crop_name = crop_path.name if crop_path else None

                failed_entry = {
                    "tile_origem": tile_name,
                    "deteccao":    {"px":ic["px"],"py":ic["py"],
                                    "raio":ic["raio"],"cor":ic["cor"]},
                    "lat": ic["lat"], "lng": ic["lng"],
                    "crop": crop_name if crop_path else None,
                }
                async with lock:
                    failed_list.append(failed_entry)
                    save_progress(output_dir, session_name, results, failed_list, "em_progresso")
                    print(f" → ✗ sem POI ({elapsed:.0f}s)")
        except Exception as e:
            async with lock:
                print(f" → ✗ erro: {e}")
        queue.task_done()

    await ctx.close()


# ── Fallback Qwen ─────────────────────────────────────────────────────────────

WORKERS_QWEN  = 4   # workers paralelos para o Qwen
GRID_COLS     = 3   # colunas do grid composto
GRID_ROWS     = 3   # linhas do grid composto (3x3 = 9 recortes/imagem)
CROP_PX       = 400 # tamanho de cada recorte no grid


def make_grid_image(entries: list, crops_dir: Path) -> bytes | None:
    """
    Monta uma imagem composta com até GRID_COLS*GRID_ROWS recortes dispostos em grid.
    Cada célula tem CROP_PX x CROP_PX. Retorna os bytes JPEG da imagem composta.
    """
    from PIL import Image as PILImage, ImageDraw, ImageFont
    cols, rows = GRID_COLS, GRID_ROWS
    w = cols * CROP_PX
    h = rows * CROP_PX
    canvas = PILImage.new("RGB", (w, h), (240, 240, 240))
    draw   = ImageDraw.Draw(canvas)

    for idx, entry in enumerate(entries):
        if idx >= cols * rows:
            break
        col = idx % cols
        row = idx // cols
        x0, y0 = col * CROP_PX, row * CROP_PX

        crop_path = crops_dir / entry["crop"] if entry.get("crop") else None
        if crop_path and crop_path.exists():
            try:
                img = PILImage.open(crop_path).convert("RGB")
                img = img.resize((CROP_PX, CROP_PX), PILImage.LANCZOS)
                canvas.paste(img, (x0, y0))
            except:
                pass

        # Número do item no canto para referência
        draw.rectangle([x0, y0, x0+28, y0+20], fill=(0,0,0,180))
        draw.text((x0+4, y0+2), str(idx+1), fill="white")

        # Linha divisória
        draw.rectangle([x0, y0, x0+CROP_PX-1, y0+CROP_PX-1], outline=(100,100,100))

    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def parse_grid_response(raw: str, count: int) -> list:
    """
    Parseia resposta do Qwen para um grid de N recortes.
    Espera formato: "1: Nome A\n2: Nome B\n3: DESCONHECIDO\n..."
    Retorna lista de strings com len == count (None para desconhecidos).
    """
    raw = re.sub(r"```.*?```", "", raw, flags=re.DOTALL).strip()
    results = [None] * count

    # Tenta formato "N: Nome" linha por linha
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r'^(\d+)[.:)\-]\s*(.+)$', line)
        if m:
            idx = int(m.group(1)) - 1
            nome = m.group(2).strip()
            if 0 <= idx < count and is_real_name(nome):
                results[idx] = nome

    # Fallback: tenta separar por vírgula se não achou nenhum
    if not any(results):
        parts = [p.strip() for p in raw.split(',')]
        for i, p in enumerate(parts[:count]):
            if is_real_name(p):
                results[i] = p

    return results


async def process_batch(client, sem_qwen, batch_entries: list, crops_dir: Path,
                        results: list, failed_list: list,
                        output_dir: Path, session_name: str, lock,
                        batch_idx: int, total_batches: int):
    """Processa um batch de até 9 recortes enviando uma imagem grid ao Qwen."""
    async with sem_qwen:
        jpeg = await asyncio.to_thread(make_grid_image, batch_entries, crops_dir)
        if not jpeg:
            return

        b64 = base64.b64encode(jpeg).decode()
        count = min(len(batch_entries), GRID_COLS * GRID_ROWS)

        prompt = (
            f"Esta imagem contém {count} recortes do Google Maps dispostos em grid {GRID_ROWS}x{GRID_COLS}. "
            f"Cada recorte está numerado de 1 a {count} no canto superior esquerdo. "
            "Para CADA recorte, identifique o nome do estabelecimento, loja, instituição ou ponto de interesse visível. "
            "Ignore nomes de ruas, avenidas e elementos de interface. "
            "Responda EXATAMENTE neste formato (uma linha por recorte):\n"
            + "\n".join(f"{i+1}: <nome ou DESCONHECIDO>" for i in range(count))
        )

        async with lock:
            print(f"  [{batch_idx:4}/{total_batches}] grid {GRID_ROWS}x{GRID_COLS} ({count} recortes)",
                  end="", flush=True)

        try:
            async with httpx.AsyncClient() as cl:
                resp = await cl.post(
                    QWEN_URL,
                    json={"model": QWEN_MODEL, "max_tokens": 512,
                          "messages": [{"role": "user", "content": [
                              {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                              {"type": "text", "text": prompt}
                          ]}]},
                    timeout=QWEN_TIMEOUT
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                nomes = parse_grid_response(raw, count)

                recuperados = 0
                async with lock:
                    for i, entry in enumerate(batch_entries[:count]):
                        nome = nomes[i] if i < len(nomes) else None
                        if nome:
                            poi = {
                                "nome":        nome,
                                "fonte":       "qwen_grid_fallback",
                                "tile_origem": entry["tile_origem"],
                                "deteccao":    entry["deteccao"],
                                "coordenadas": {"lat": entry["lat"], "lng": entry["lng"]},
                                "crop":        entry.get("crop"),
                            }
                            results.append(poi)
                            entry["nome_qwen"]  = nome
                            entry["recuperado"] = True
                            recuperados += 1
                    save_progress(output_dir, session_name, results, failed_list, "em_progresso")
                    print(f" → ✔ {recuperados}/{count}")

        except Exception as e:
            async with lock:
                print(f" → ✗ {e}")


async def qwen_fallback(failed_list: list, crops_dir: Path,
                        results: list, output_dir: Path, session_name: str):
    """Envia recortes ao Qwen em grids compostos para máxima eficiência."""
    pendentes = [f for f in failed_list if f.get("crop")]
    if not pendentes:
        print("\n  ℹ️  Nenhum recorte para processar com Qwen.")
        return

    per_batch = GRID_COLS * GRID_ROWS
    batches   = [pendentes[i:i+per_batch] for i in range(0, len(pendentes), per_batch)]

    print(f"\n🔍 Fallback Qwen2-VL: {len(pendentes)} recortes | "
          f"grid {GRID_ROWS}x{GRID_COLS} | {len(batches)} batches | {WORKERS_QWEN} workers")
    print(f"   Modelo : {QWEN_MODEL} @ {QWEN_URL}\n")

    sem_qwen = asyncio.Semaphore(WORKERS_QWEN)
    lock     = asyncio.Lock()

    async with httpx.AsyncClient() as client:
        tasks = [
            process_batch(client, sem_qwen, batch, crops_dir,
                          results, failed_list, output_dir, session_name, lock,
                          i+1, len(batches))
            for i, batch in enumerate(batches)
        ]
        await asyncio.gather(*tasks)

    recuperados = sum(1 for f in failed_list if f.get("recuperado"))
    print(f"\n   Recuperados pelo Qwen: {recuperados}/{len(pendentes)} "
          f"({recuperados/max(len(pendentes),1)*100:.0f}%)")

async def main_async(session_path: Path, min_r, max_r, click_zoom, workers):
    global CLICK_ZOOM
    CLICK_ZOOM = click_zoom

    session      = json.loads(session_path.read_text(encoding="utf-8"))
    output_dir   = Path(session["config"]["outputDir"])
    session_name = session["sessionName"]
    tiles_png    = sorted(output_dir.glob("tile_*.png"))

    if not tiles_png:
        print(f"❌ Nenhum tile PNG em: {output_dir}"); sys.exit(1)

    # Pasta de recortes DENTRO da pasta da sessão
    crops_dir = output_dir / "crops_fallback"
    crops_dir.mkdir(exist_ok=True)

    # Mapa nome → path para acesso nos workers
    tile_paths_map = {t.name: t for t in tiles_png}

    print(f"\n🗺  ComercialRadar — OpenCV + Playwright + Qwen fallback")
    print(f"   Sessão       : {session_name}")
    print(f"   Tiles        : {len(tiles_png)}")
    print(f"   Zoom captura : {CAPTURE_ZOOM} | Zoom clique : {CLICK_ZOOM}")
    print(f"   Workers      : {workers}")
    print(f"   Recortes em  : {crops_dir}\n")

    all_results: list = []
    all_failed:  list = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", f"--window-size={VP_W},{VP_H}"]
        )

        for tile_path in tiles_png:
            m = re.search(r'_([-\d.]+)_([-\d.]+)\.png$', tile_path.name)
            if not m: continue
            tile_lat, tile_lng = float(m.group(1)), float(m.group(2))

            print(f"\n  📄 {tile_path.name}")
            icons = detect_icons(tile_path, min_r, max_r)
            print(f"     {len(icons)} ícones detectados | {workers} workers")
            if not icons: continue

            for ic in icons:
                ic["lat"], ic["lng"] = pixel_to_latlon(
                    ic["px"], ic["py"], tile_lat, tile_lng,
                    CAPTURE_ZOOM, ic["img_w"], ic["img_h"]
                )

            queue = asyncio.Queue()
            for i, ic in enumerate(icons, 1):
                queue.put_nowait((i, len(icons), ic, tile_path.name))

            lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(worker(
                    w+1, browser, queue, all_results, all_failed, lock,
                    output_dir, crops_dir, session_name, tile_paths_map
                ))
                for w in range(min(workers, len(icons)))
            ]
            await asyncio.gather(*tasks)
            print(f"     ✔ {len(all_results)} POIs | {len(all_failed)} sem dados")

        await browser.close()

    # Fallback: ícones sem dados → Qwen2-VL
    await qwen_fallback(all_failed, crops_dir, all_results, output_dir, session_name)

    # Deduplica por coordenada (~5m)
    unique = []
    for p in all_results:
        c = p.get("coordenadas", {})
        if not any(
            abs(c.get("lat",0) - u.get("coordenadas",{}).get("lat",0)) < 0.00005 and
            abs(c.get("lng",0) - u.get("coordenadas",{}).get("lng",0)) < 0.00005
            for u in unique
        ):
            unique.append(p)

    save_progress(output_dir, session_name, unique, all_failed, "concluido")
    print(f"\n✅ Concluído! Salvo em: {output_dir / 'pois_opencv.json'}")
    print(f"   POIs totais       : {len(unique)}")
    print(f"   Sem dados (falhos): {len([f for f in all_failed if not f.get('recuperado')])}")
    print(f"   Recortes salvos   : {crops_dir}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session_json")
    parser.add_argument("--min-radius", type=int, default=6)
    parser.add_argument("--max-radius", type=int, default=40)
    parser.add_argument("--click-zoom", type=int, default=21)
    parser.add_argument("--workers",    type=int, default=3)
    args = parser.parse_args()
    session_path = Path(args.session_json)
    if not session_path.exists():
        print(f"❌ Não encontrado: {session_path}"); sys.exit(1)
    asyncio.run(main_async(session_path, args.min_radius, args.max_radius,
                           args.click_zoom, args.workers))

if __name__ == "__main__":
    main()
