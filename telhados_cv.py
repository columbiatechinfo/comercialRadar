# -*- coding: utf-8 -*-
"""
telhados_cv.py — Identificador de telhados: conta cada edificação de uma área, mede a
área (m²) e infere o tipo (casa térrea / alto padrão / galpão-empresa / prédio).

DUAS FONTES DE DETECÇÃO (achar cada edificação):
  • overture (PADRÃO) — footprints já extraídos por IA (Google Open Buildings + Microsoft,
      via Overture Maps) lidos do GeoParquet público com DuckDB. Pega TODA edificação,
      inclusive laje cinza que a cor perde; traz a área geodésica de cada uma. É "IA
      pronta": nada roda na sua máquina, custo zero. Melhor onde a cobertura é boa.
  • cor (FALLBACK) — nosso CV clássico (OpenCV) na imagem Google z20: máscara de cor da
      telha (cerâmica laranja / laje clara) + watershed pra separar casas GEMINADAS pelo
      tamanho de lote + filtro de saturação contra falso-positivo em solo batido. Útil
      onde o Overture é ruim (ex.: cidades pequenas com footprint grosseiro).

CLASSIFICAÇÃO DO TIPO (sempre pela nossa imagem):
  1) heurística (padrão): material (cor da telha) + área + forma + sombra. Offline.
  2) por EXEMPLOS (--ia): manda o recorte + 1 exemplo de referência de cada tipo pro
     qwen2.5vl (SEU Ollama) "se balizar". Curadoria: revise a galeria e rode
       --exemplo casa_terrea=quadra1:7   (grava o recorte #7 como exemplo).

USO
  # área por polígono (4+ vértices), detecção Overture — o caso principal:
  .venv\\Scripts\\python telhados_cv.py --poligono="-5.1279,-42.7981;-5.1259,-42.7940;-5.1320,-42.7914;-5.1346,-42.7954" --rotulo=area1
  # ou por bbox:
  .venv\\Scripts\\python telhados_cv.py --bbox=-2.9165,-41.775,-2.9150,-41.772 --rotulo=quadra1
  .venv\\Scripts\\python telhados_cv.py --bbox=... --fonte=cor        # nosso CV (fallback)
  .venv\\Scripts\\python telhados_cv.py --bbox=... --ia               # refina tipo c/ exemplos
  .venv\\Scripts\\python telhados_cv.py --exemplo casa_terrea=quadra1:7   # curar exemplo
  (bbox/pontos = lat,lng ; sempre minlat,minlng,maxlat,maxlng na bbox)
"""
import io
import os
import sys
import json
import math
import base64
import argparse
import urllib.request
from pathlib import Path

import realtime_ingest
from segmentar_telhados import _num, _inv, mosaico, Z, GOOGLE, OLLAMA, MODELO  # geo/tiles já testados

BASE = Path(__file__).resolve().parent
EXEMPLOS = BASE / "exemplos" / "telhados"          # exemplos/telhados/<tipo>/*.jpg

# tipos que o identificador conhece (ordem = ordem que a IA vê os exemplos)
TIPOS = ["casa_terrea", "casa_alto_padrao", "galpao", "predio"]
ROTULO_PT = {"casa_terrea": "casa térrea", "casa_alto_padrao": "casa de alto padrão",
             "galpao": "galpão / empresa", "predio": "prédio (multiandar)",
             "laje": "laje/cobertura clara", "indeterminado": "indeterminado"}

DDL = """
CREATE TABLE IF NOT EXISTS telhados (
  area_ref text, idx int, material text, area_m2 double precision,
  centro_lat double precision, centro_lng double precision,
  largura_m double precision, comprimento_m double precision,
  tipo text, origem_tipo text, just text, criado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_telhados_ref ON telhados (area_ref);
"""


def _dentro(lat, lng, poly):
    """Ponto-em-polígono (ray casting). poly = lista de (lat,lng)."""
    x, y, n, ins = lng, lat, len(poly), False
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if ((xi > x) != (xj > x)) and (y < (yj - yi) * (x - xi) / (xj - xi) + yi):
            ins = not ins
        j = i
    return ins


# ── detecção por cor+forma ───────────────────────────────────────────────────────
def _mascaras(hsv):
    """Duas máscaras binárias: telha cerâmica (casa) e cobertura clara (galpão/laje)."""
    import cv2
    import numpy as np
    # cerâmica: laranja/terracota. V baixo inclui a água do telhado que está na sombra.
    ceramica = cv2.inRange(hsv, (3, 70, 40), (22, 255, 255))
    ceramica = cv2.morphologyEx(ceramica, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), 1)
    ceramica = cv2.morphologyEx(ceramica, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), 2)
    # clara: laje/metálica muito clara e dessaturada (só vira telhado se for grande+compacta)
    clara = cv2.inRange(hsv, (0, 0, 175), (180, 50, 255))
    clara = cv2.morphologyEx(clara, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), 1)
    clara = cv2.morphologyEx(clara, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), 2)
    return {"ceramica": ceramica, "clara": clara}


def _formato(c, mpp):
    """Extrai medidas de um contorno: área(m²), lados do retângulo mínimo(m), fill, aspect
    e a CAIXA rotacionada (4 cantos) — o 'retângulo padrão' que marca a edificação."""
    import cv2
    area_px = cv2.contourArea(c)
    x, y, w, h = cv2.boundingRect(c)
    rect = cv2.minAreaRect(c)                          # retângulo rotacionado = medidas reais
    (_, _), (rw, rh), _ = rect
    box = cv2.boxPoints(rect)                          # 4 cantos do retângulo
    lado_menor, lado_maior = sorted([rw * mpp, rh * mpp])
    fill = area_px / (w * h + 1)
    aspect = lado_maior / max(0.1, lado_menor)
    return {"area_m2": area_px * mpp * mpp, "area_px": float(area_px),
            "larg": lado_menor, "comp": lado_maior, "fill": fill, "aspect": aspect,
            "bbox": (x, y, w, h), "box": box.tolist()}


def _sombra_ratio(arr, mask_roof, bbox):
    """Quanta SOMBRA (pixel escuro) cerca a edificação, relativa ao próprio telhado.
    Prédio multiandar projeta sombra longa; casa/galpão térreo quase não projeta.
    (Perto do equador a sombra é curta — por isso é indício forte quando aparece.)"""
    import numpy as np
    import cv2
    x, y, w, h = bbox
    exp = int(max(w, h) * 0.8)
    H, W = arr.shape[:2]
    l, u, r, d = max(0, x - exp), max(0, y - exp), min(W, x + w + exp), min(H, y + h + exp)
    reg = arr[u:d, l:r].astype("float32")
    lum = reg.mean(axis=2)
    escuro = lum < 55                                   # sombra dura (não é telhado nem vegetação)
    roof_px = int(mask_roof[y:y + h, x:x + w].sum()) or int(w * h * 0.5)
    return float(escuro.sum()) / (roof_px + 1)


def _pureza(hsv, c, bbox):
    """Saturação/valor médios dos pixels do contorno — telha real é saturada; solo
    batido de cor parecida é fosco. Devolve (S_médio, V_médio)."""
    import cv2
    import numpy as np
    x, y, w, h = bbox
    sub = hsv[y:y + h, x:x + w]
    m = np.zeros((h, w), np.uint8)
    cv2.drawContours(m, [c], -1, 255, -1, offset=(-x, -y))
    px = sub[m > 0]
    if len(px) == 0:
        return 0.0, 0.0
    return float(px[:, 1].mean()), float(px[:, 2].mean())


def _instancias_ws(mask, img3, mpp):
    """Separa telhados COLADOS (casas geminadas) em instâncias por watershed, usando o
    tamanho de lote pra espaçar as sementes. Devolve um contorno por edificação."""
    import cv2
    import numpy as np
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    if dist.max() < 3:
        return []
    pr = max(4, int(3.2 / mpp))                       # sementes a ~3,2 m (meia largura de lote)
    k = np.ones((2 * pr + 1, 2 * pr + 1), np.uint8)
    localmax = cv2.dilate(dist, k)
    peaks = ((dist >= localmax - 0.5) & (dist > pr * 0.55)).astype(np.uint8)
    n, lab = cv2.connectedComponents(peaks)
    if n <= 1:
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return cnts
    markers = np.zeros(mask.shape, np.int32)
    markers[mask == 0] = 1                            # fundo
    markers[peaks > 0] = lab[peaks > 0] + 1          # sementes 2..n
    cv2.watershed(np.ascontiguousarray(img3[:, :, ::-1]), markers)   # gradiente na imagem real
    out = []
    for l in range(2, n + 1):
        comp = (markers == l).astype(np.uint8)
        if comp.sum() < 40:
            continue
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            out.append(max(cnts, key=cv2.contourArea))
    return out


def detectar(arr, mpp):
    """Devolve a lista de telhados {material, contorno, centroide_px, medidas, sombra}.
    Cerâmica passa por watershed (separa geminadas); clara fica inteira (galpão)."""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)   # arr vem RGB (PIL); NÃO inverter p/ BGR
    masks = _mascaras(hsv)
    roofs = []
    # cerâmica (casas, muitas vezes coladas) → watershed
    for c in _instancias_ws(masks["ceramica"], arr, mpp):
        f = _formato(c, mpp)
        if not (18 <= f["area_m2"] <= 600):
            continue
        if f["fill"] < 0.3 or f["aspect"] > 6:
            continue
        satS, _ = _pureza(hsv, c, f["bbox"])   # telha é saturada; solo batido é fosco
        if satS < 90:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        roofs.append({"material": "ceramica", "cnt": c, "sombra": 0.0,
                      "cxp": M["m10"] / M["m00"], "cyp": M["m01"] / M["m00"], **f})
    # clara (galpão/laje ampla) → contorno inteiro
    cnts, _ = cv2.findContours(masks["clara"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        f = _formato(c, mpp)
        if not (150 <= f["area_m2"] <= 6000):
            continue
        if f["fill"] < 0.5 or f["aspect"] > 7:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        sombra = _sombra_ratio(arr, masks["clara"], f["bbox"])
        roofs.append({"material": "clara", "cnt": c, "sombra": sombra,
                      "cxp": M["m10"] / M["m00"], "cyp": M["m01"] / M["m00"], **f})
    return roofs


# ── detecção por footprints de IA (Overture) ─────────────────────────────────────
def _latlng_px(lat, lng, ox, oy):
    fx, fy = _num(lat, lng, Z)
    return fx * 256 - ox, fy * 256 - oy


def _material_footprint(hsv, poly_px):
    """Material amostrando a cor DENTRO do footprint (encolhido p/ fugir do leve
    descasamento footprint×tile): laranja saturado = cerâmica; senão = clara."""
    import cv2
    import numpy as np
    xs = [p[0] for p in poly_px]
    ys = [p[1] for p in poly_px]
    x0, y0 = int(max(0, min(xs))), int(max(0, min(ys)))
    x1, y1 = int(min(hsv.shape[1], max(xs))), int(min(hsv.shape[0], max(ys)))
    if x1 <= x0 or y1 <= y0:
        return "clara"
    sub = hsv[y0:y1, x0:x1]
    m = np.zeros(sub.shape[:2], np.uint8)
    cv2.fillPoly(m, [np.array([(int(x - x0), int(y - y0)) for x, y in poly_px])], 255)
    m = cv2.erode(m, np.ones((5, 5), np.uint8), 1)
    px = sub[m > 0]
    if len(px) < 10:
        px = sub.reshape(-1, 3)
    laranja = ((px[:, 0] >= 3) & (px[:, 0] <= 22) & (px[:, 1] >= 75)).mean()
    return "ceramica" if laranja >= 0.30 else "clara"


def detectar_overture(bbox, ox, oy, arr, mpp, release=None):
    """Footprints de IA da Overture (Google Open Buildings + Microsoft): polígono e área
    geodésica de CADA edificação — inclusive laje cinza que a cor perde. O TIPO continua
    saindo da nossa imagem (cor/tamanho/sombra) porque altura/andares vem nulo no BR."""
    import cv2
    import re
    import numpy as np
    import telhados_area as TA
    rows = TA.puxar_overture(bbox[0], bbox[1], bbox[2], bbox[3], release or TA.RELEASE)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    claraM = _mascaras(hsv)["clara"]
    roofs = []
    for r in rows:
        area_m2, clat, clng, wkt = r[5], r[6], r[7], r[8]
        pts = re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", wkt or "")
        if len(pts) < 3:
            continue
        poly_px = [_latlng_px(float(la), float(ln), ox, oy) for ln, la in pts]  # WKT = lng lat
        (_, _), (rw, rh), _ = cv2.minAreaRect(np.array(poly_px, np.float32))
        larg, comp = sorted([rw * mpp, rh * mpp])
        xs = [p[0] for p in poly_px]
        ys = [p[1] for p in poly_px]
        bx = (int(min(xs)), int(min(ys)), int(max(xs) - min(xs)) + 1, int(max(ys) - min(ys)) + 1)
        mat = _material_footprint(hsv, poly_px)
        sombra = _sombra_ratio(arr, claraM, bx) if mat == "clara" else 0.0
        roofs.append({"material": mat, "area_m2": float(area_m2 or 0.0),
                      "larg": float(larg), "comp": float(comp), "aspect": comp / max(0.1, larg),
                      "sombra": sombra, "box": poly_px, "bbox": bx,
                      "cxp": bx[0] + bx[2] / 2, "cyp": bx[1] + bx[3] / 2,
                      "lat": float(clat), "lng": float(clng)})
    return roofs


# ── classificação heurística (offline, determinística) ───────────────────────────
def tipo_heuristico(r):
    if r["material"] == "ceramica":
        if r["area_m2"] < 350:
            return "casa_terrea", "telha cerâmica no porte de um lote (1 pavimento)"
        return "casa_alto_padrao", "telha cerâmica de grande porte (casarão/sobrado)"
    # clara = laje/metálica. Laje PEQUENA (porte de lote) é casa de laje, não galpão.
    if r["area_m2"] < 160:
        return "casa_terrea", "casa de laje (telhado plano claro, porte de lote)"
    # Prédio (multiandar) SÓ com indício de altura = sombra longa em planta compacta.
    if r["sombra"] > 0.55 and r["aspect"] < 1.9 and r["area_m2"] >= 200:
        return "predio", "planta compacta com sombra longa (indício de vários andares)"
    return "galpao", "cobertura ampla clara e térrea (galpão ou empresa)"


# ── classificação por EXEMPLOS (opcional, --ia) ──────────────────────────────────
def _carregar_exemplos():
    """Um exemplo (o 1º .jpg) de cada tipo que tiver pasta em exemplos/telhados/."""
    ex = {}
    for t in TIPOS:
        d = EXEMPLOS / t
        if d.is_dir():
            fs = sorted(d.glob("*.jpg"))
            if fs:
                ex[t] = fs[0].read_bytes()
    return ex


def _classificar_ia(alvo_jpg, exemplos):
    """Manda exemplos rotulados + alvo (contorno amarelo) e pede o tipo mais parecido."""
    imgs, legenda = [], []
    for t, raw in exemplos.items():
        imgs.append(base64.b64encode(raw).decode())
        legenda.append(f"- imagem {len(imgs)}: exemplo de {ROTULO_PT[t]} ({t})")
    imgs.append(base64.b64encode(alvo_jpg).decode())
    tipos_txt = " | ".join(exemplos.keys())
    prompt = (
        "Você é um fotointérprete de imagens de satélite (vista de cima). As primeiras "
        "imagens são EXEMPLOS de referência, um de cada tipo de edificação:\n"
        + "\n".join(legenda) +
        f"\n- imagem {len(imgs)}: o ALVO a classificar (edificação com CONTORNO AMARELO).\n"
        "Compare o ALVO com os exemplos e diga de qual ele mais se aproxima, olhando cor do "
        "telhado, textura (telha de duas águas x laje lisa), tamanho relativo, sombra "
        "(prédio projeta sombra longa) e caixa d'água. Responda SOMENTE JSON: "
        f'{{"tipo":"{tipos_txt}","justificativa":"1 frase"}}')
    try:
        payload = json.dumps({"model": MODELO, "prompt": prompt, "images": imgs,
                              "stream": False, "format": "json",
                              "options": {"num_ctx": 4096, "temperature": 0, "num_predict": 120}}).encode()
        req = urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"})
        r = json.loads(json.loads(urllib.request.urlopen(req, timeout=180).read()).get("response", "{}"))
        return (r.get("tipo") or "indeterminado"), (r.get("justificativa") or "")
    except Exception as e:
        return "erro", str(e)[:80]


# ── recorte nítido com contorno (galeria + IA) ───────────────────────────────────
def recorte(mos, r, idx, marcar=True):
    from PIL import Image, ImageDraw
    import numpy as np
    x, y, w, h = r["bbox"]
    mg = int(max(w, h) * 0.45) + 12
    l, u = max(0, x - mg), max(0, y - mg)
    rt, dn = min(mos.width, x + w + mg), min(mos.height, y + h + mg)
    lado = max(rt - l, dn - u)
    crop = mos.crop((l, u, l + lado, u + lado))
    sc = 360 / lado
    crop = crop.resize((360, 360))
    if marcar:
        ov = Image.new("RGBA", crop.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)
        box = [((px - l) * sc, (py - u) * sc) for px, py in r["box"]]   # retângulo padrão
        dr.line(box + [box[0]], fill=(255, 235, 0, 255), width=3)
        dr.text((6, 6), f"#{idx}", fill=(255, 235, 0, 255))
        crop = Image.alpha_composite(crop.convert("RGBA"), ov).convert("RGB")
    b = io.BytesIO()
    crop.save(b, "JPEG", quality=88)
    return b.getvalue()


def galeria(ref, cards, usou_ia, fonte="cor"):
    import html
    css = ("*{box-sizing:border-box}body{margin:0;background:#0f1215;color:#e7ebef;font:15px/1.5 -apple-system,Segoe UI,sans-serif}"
           ".wrap{max-width:1240px;margin:0 auto;padding:24px 16px 60px}h1{font-size:1.35rem;margin:0 0 4px}"
           ".sub{color:#96a0ac;margin:0 0 6px}.leg{display:flex;gap:14px;flex-wrap:wrap;color:#96a0ac;font-size:.82rem;margin:8px 0 18px}"
           ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}"
           ".card{position:relative;background:#181c21;border:1px solid #282f37;border-radius:12px;overflow:hidden;border-top:4px solid #282f37}"
           ".card.casa_terrea{border-top-color:#4cc38a}.card.casa_alto_padrao{border-top-color:#3aa0e0}"
           ".card.predio{border-top-color:#e08571}.card.galpao{border-top-color:#dcb04a}.card.laje,.card.indeterminado,.card.erro{border-top-color:#7a828c}"
           ".card img{display:block;width:100%;height:220px;object-fit:cover}"
           ".n{position:absolute;top:6px;left:6px;background:#000b;color:#ffeb00;font:700 11px ui-monospace;padding:2px 7px;border-radius:6px}"
           "figcaption{padding:9px 11px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:6px}"
           ".tipo{font-weight:700;font-size:.74rem;padding:3px 9px;border-radius:99px;background:#222831;color:#cfd6de}"
           ".card.casa_terrea .tipo{background:#123024;color:#4cc38a}.card.casa_alto_padrao .tipo{background:#10283a;color:#3aa0e0}"
           ".card.predio .tipo{background:#331b17;color:#e08571}.card.galpao .tipo{background:#33280f;color:#dcb04a}"
           ".area{font:600 .8rem ui-monospace;color:#96a0ac;white-space:nowrap}"
           ".dim{font:.72rem ui-monospace;color:#6b7480}.just{font-size:.78rem;color:#96a0ac;line-height:1.4;margin-top:3px}")
    figs = []
    for c in cards:
        cls = c["tipo"] if c["tipo"] in ("casa_terrea", "casa_alto_padrao", "predio", "galpao") else "laje"
        uri = "data:image/jpeg;base64," + base64.b64encode(c["raw"]).decode()
        figs.append(
            f'<figure class="card {cls}"><span class="n">#{c["idx"]}</span><img src="{uri}">'
            f'<figcaption><div class="top"><span class="tipo">{html.escape(ROTULO_PT.get(c["tipo"], c["tipo"]))}</span>'
            f'<span class="area">{c["area"]:.0f} m²</span></div>'
            f'<div class="dim">{c["larg"]:.0f}×{c["comp"]:.0f} m · {c["material"]}</div>'
            f'<div class="just">{html.escape(c["just"])}</div></figcaption></figure>')
    tot = len(cards)
    n = {t: sum(1 for c in cards if c["tipo"] == t) for t in TIPOS}
    det = "footprints Overture (IA)" if fonte == "overture" else "cor da telha + watershed"
    doc = (f'<title>Telhados — {html.escape(ref)}</title><style>{css}</style><div class="wrap">'
           f'<h1>Identificador de telhados ({det}{"+IA" if usou_ia else ""}) — {html.escape(ref)}</h1>'
           f'<p class="sub">{tot} edificações · 🏠 {n["casa_terrea"]} casas térreas · '
           f'🏡 {n["casa_alto_padrao"]} alto padrão · 🏢 {n["predio"]} prédios · 🏭 {n["galpao"]} galpões</p>'
           f'<div class="leg"><span>Contorno amarelo = o telhado medido.</span>'
           f'<span>Detecção: {det}. {"Tipo refinado pela IA com exemplos." if usou_ia else "Tipo por heurística (material/área/forma/sombra)."}</span></div>'
           f'<div class="grid">{"".join(figs)}</div></div>')
    out = BASE / "exemplos" / f"_telhados_{ref}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(doc, "utf-8")
    return out


# ── curadoria de exemplo de referência ───────────────────────────────────────────
def curar_exemplo(spec):
    """--exemplo casa_terrea=quadra1:7  → grava o recorte do telhado #7 de quadra1
    (do banco) como exemplo de referência de casa_terrea."""
    tipo, resto = spec.split("=", 1)
    ref, idx = resto.split(":")
    if tipo not in TIPOS:
        sys.exit(f"tipo inválido: {tipo}. Use um de {TIPOS}")
    conn = realtime_ingest.conectar()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT centro_lat, centro_lng, largura_m, comprimento_m FROM telhados "
                    "WHERE area_ref=%s AND idx=%s", (ref, int(idx)))
        row = cur.fetchone()
    conn.close()
    if not row:
        sys.exit(f"não achei o telhado {ref}:{idx} no banco (rode a bbox primeiro)")
    lat, lng, larg, comp = row
    # recorta um quadrado ao redor do centro, no tamanho da edificação (+margem)
    lado_m = max(float(larg or 20), float(comp or 20)) * 1.6
    mpp = 156543.03 * math.cos(math.radians(lat)) / 2 ** Z
    lado_px = lado_m / mpp
    cx, cy = _num(lat, lng, Z)
    cxp, cyp = cx * 256, cy * 256
    bbox = _inv(cxp - lado_px / 2, cyp + lado_px / 2, Z), _inv(cxp + lado_px / 2, cyp - lado_px / 2, Z)
    (minlat, minlng), (maxlat, maxlng) = bbox
    mos, ox, oy = mosaico((min(minlat, maxlat), min(minlng, maxlng),
                           max(minlat, maxlat), max(minlng, maxlng)), Z)
    from PIL import Image
    lado = min(mos.size)
    crop = mos.crop((0, 0, lado, lado)).resize((360, 360))
    d = EXEMPLOS / tipo
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{ref}_{idx}.jpg"
    crop.save(dest, "JPEG", quality=90)
    print(f"✅ exemplo de {ROTULO_PT[tipo]} salvo: {dest}")


# ── pipeline principal ────────────────────────────────────────────────────────────
def run(bbox, ref, usar_ia, fonte="overture", poligono=None, release=None):
    import numpy as np
    print(f"🛰️  Identificando telhados [{ref}] | fonte={fonte}{' +IA' if usar_ia else ''}", flush=True)
    mos, ox, oy = mosaico(bbox, Z)
    print(f"  mosaico {mos.size[0]}x{mos.size[1]}px", flush=True)
    arr = np.array(mos)
    mpp = 156543.03 * math.cos(math.radians((bbox[0] + bbox[2]) / 2)) / 2 ** Z
    if fonte == "overture":
        roofs = detectar_overture(bbox, ox, oy, arr, mpp, release)
        print(f"  {len(roofs)} edificações (footprints Overture)", flush=True)
    else:
        roofs = detectar(arr, mpp)
        print(f"  {len(roofs)} telhados (cor+forma+watershed)", flush=True)
    # garante lat/lng em todos (Overture já traz; cor calcula do pixel)
    for r in roofs:
        if "lat" not in r:
            la, ln = _inv(ox + r["cxp"], oy + r["cyp"], Z)
            r["lat"], r["lng"] = float(la), float(ln)
    if poligono:
        antes = len(roofs)
        roofs = [r for r in roofs if _dentro(r["lat"], r["lng"], poligono)]
        print(f"  {len(roofs)}/{antes} dentro do polígono", flush=True)
    if not roofs:
        print("  (nada detectado — confira a área/fonte)"); return
    roofs.sort(key=lambda r: (r["cyp"], r["cxp"]))          # ordem de leitura

    exemplos = _carregar_exemplos() if usar_ia else {}
    if usar_ia and not exemplos:
        print("  ⚠️  --ia pedido mas não há exemplos em exemplos/telhados/. Caindo p/ heurística.", flush=True)
        usar_ia = False
    elif usar_ia:
        print(f"  exemplos de referência: {', '.join(exemplos)}", flush=True)

    conn = realtime_ingest.conectar()
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("DELETE FROM telhados WHERE area_ref=%s", (ref,))
    cards = []
    for idx, r in enumerate(roofs, 1):
        raw = recorte(mos, r, idx)
        if usar_ia:
            tipo, just = _classificar_ia(raw, exemplos)
        else:
            tipo, just = tipo_heuristico(r)
        origem = f"{fonte}+{'ia_exemplos' if usar_ia else 'heuristica'}"
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telhados (area_ref, idx, material, area_m2, centro_lat, centro_lng, "
                "largura_m, comprimento_m, tipo, origem_tipo, just) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ref, idx, r["material"], float(r["area_m2"]), float(r["lat"]), float(r["lng"]),
                 float(r["larg"]), float(r["comp"]), tipo, origem, just))
        cards.append({"idx": idx, "area": r["area_m2"], "larg": r["larg"], "comp": r["comp"],
                      "material": r["material"], "tipo": tipo, "just": just, "raw": raw})
        if usar_ia and idx % 20 == 0:
            print(f"    IA {idx}/{len(roofs)}", flush=True)
    conn.close()
    out = galeria(ref, cards, usar_ia, fonte)
    n = {t: sum(1 for c in cards if c["tipo"] == t) for t in TIPOS}
    area_tot = sum(c["area"] for c in cards)
    print("\n  === RESUMO ===")
    print(f"  {len(cards)} edificações | 🏠 {n['casa_terrea']} térreas · 🏡 {n['casa_alto_padrao']} alto padrão · "
          f"🏭 {n['galpao']} galpões/empresas · 🏢 {n['predio']} prédios")
    print(f"  Área construída: {area_tot:,.0f} m²")
    print(f"  Galeria: {out}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", help="minlat,minlng,maxlat,maxlng (comece por 1 quadra)")
    p.add_argument("--poligono", help="recorte por polígono: 'lat,lng;lat,lng;...' (deriva a bbox)")
    p.add_argument("--rotulo", default="area", help="nome da área (chave em telhados)")
    p.add_argument("--fonte", default="overture", choices=["overture", "cor"],
                   help="detecção: overture (footprints de IA, padrão) | cor (nosso CV, fallback)")
    p.add_argument("--release", help="release do Overture (padrão = o de telhados_area.py)")
    p.add_argument("--ia", action="store_true", help="refina o tipo com a IA + exemplos de referência")
    p.add_argument("--exemplo", help="curar exemplo: tipo=ref:idx (ex: casa_terrea=quadra1:7)")
    a = p.parse_args()
    if a.exemplo:
        curar_exemplo(a.exemplo)
        sys.exit()
    poly = None
    if a.poligono:
        poly = [tuple(float(x) for x in par.split(",")) for par in a.poligono.split(";") if par.strip()]
        if len(poly) < 3:
            sys.exit("--poligono precisa de ao menos 3 pontos 'lat,lng'")
        lats = [q[0] for q in poly]
        lngs = [q[1] for q in poly]
        bbox = (min(lats), min(lngs), max(lats), max(lngs))
    elif a.bbox:
        v = [float(x) for x in a.bbox.split(",")]
        if len(v) != 4:
            sys.exit("bbox precisa de 4 números: minlat,minlng,maxlat,maxlng")
        bbox = (min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))
    else:
        sys.exit("informe --bbox, --poligono ou --exemplo")
    run(bbox, a.rotulo, a.ia, fonte=a.fonte, poligono=poly, release=a.release)
