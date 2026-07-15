# -*- coding: utf-8 -*-
"""
segmentar_telhados.py — Detecta CADA telhado individual de uma bbox e classifica.

Ao contrário do telhados_area.py (que usa os footprints do Overture, que fundem
quadras), este SEGMENTA os telhados a partir da imagem NÍTIDA (Google z20) com o
FastSAM — devolve o polígono de cada casa, igual numerar à mão. Para cada telhado:
área (m²) + tipo (prédio/casa) pela IA (qwen2.5vl) no crop nítido.

Grava em `telhados_seg` + gera uma galeria pra avaliação. Comece por UMA quadra.

DEPENDÊNCIAS (na máquina que roda o script):
    .venv\\Scripts\\python -m pip install ultralytics
  (baixa o FastSAM-s ~23MB na 1ª vez; roda em CPU, mais rápido com GPU via --device cuda)
  A classificação usa o Ollama remoto (mesmo do descrever_imagens).

USO:
    .venv\\Scripts\\python segmentar_telhados.py --bbox=-2.9165,-41.775,-2.9150,-41.772 --rotulo=quadra1
    (bbox = minlat,minlng,maxlat,maxlng — comece pequeno, ~1 quadra)   [--device cpu|cuda]
"""
import io
import ssl
import math
import json
import base64
import os
import sys
import argparse
import urllib.request
from pathlib import Path

import realtime_ingest

GOOGLE = "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"   # mesma fonte XYZ do QGIS
Z = 20                                                          # ~0.15 m/px (telhado nítido)
OLLAMA = os.environ.get("OLLAMA_HOST", "http://100.115.117.49:11434").rstrip("/") + "/api/generate"
MODELO = "qwen2.5vl:7b"
_SSL = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"
BASE = Path(__file__).resolve().parent

PROMPT = """Recorte de satélite (vista de cima), NÍTIDO, de UMA edificação com o \
CONTORNO AMARELO. Classifique só ela: PRÉDIO (vários andares/apartamentos — telhado \
grande de laje, sombra LONGA) ou CASA TÉRREA (1 pavimento — telhado de cerâmica de duas \
águas, sombra curta). Responda SOMENTE JSON: {"tipo":"predio"|"casa_terrea"|"incerto","justificativa":"1 frase"}"""

DDL = """
CREATE TABLE IF NOT EXISTS telhados_seg (
  area_ref text, idx int, area_m2 double precision, centro_lat double precision,
  centro_lng double precision, tipo text, just text, criado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_telseg_ref ON telhados_seg (area_ref);
"""


# ── tiles / geo ────────────────────────────────────────────────────────────────
def _num(lat, lng, z):
    n = 2 ** z
    return ((lng + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def _inv(gpx, gpy, z):
    """pixel global -> (lat, lng)."""
    n = 2 ** z
    lng = gpx / 256 / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (gpy / 256 / n)))))
    return lat, lng


def _tile(z, x, y):
    from PIL import Image
    req = urllib.request.Request(GOOGLE.format(z=z, x=x, y=y), headers={"User-Agent": UA})
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=40, context=_SSL).read())).convert("RGB")


def mosaico(bbox, z):
    """Costura os tiles Google que cobrem a bbox. Retorna (imagem, ox, oy) onde
    (ox,oy) é o pixel global do canto superior-esquerdo."""
    from PIL import Image
    minlat, minlng, maxlat, maxlng = bbox
    fx0, fy0 = _num(maxlat, minlng, z)     # canto superior-esquerdo (maxlat=cima, minlng=esq)
    fx1, fy1 = _num(minlat, maxlng, z)     # canto inferior-direito
    tx0, ty0, tx1, ty1 = int(fx0), int(fy0), int(fx1), int(fy1)
    nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
    if nx * ny > 400:
        sys.exit(f"bbox grande demais ({nx*ny} tiles z{z}). Comece por uma quadra menor.")
    mos = Image.new("RGB", (nx * 256, ny * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            try:
                mos.paste(_tile(z, tx, ty), ((tx - tx0) * 256, (ty - ty0) * 256))
            except Exception:
                pass
    return mos, tx0 * 256, ty0 * 256


# ── segmentação (FastSAM) ───────────────────────────────────────────────────────
def segmentar(mos, device):
    """FastSAM 'segment everything' -> lista de máscaras booleanas (numpy HxW)."""
    try:
        from ultralytics import FastSAM
    except ImportError:
        sys.exit("Falta o FastSAM. Rode:  .venv\\Scripts\\python -m pip install ultralytics")
    import numpy as np
    model = FastSAM("FastSAM-s.pt")        # baixa na 1ª vez (~23MB)
    arr = np.array(mos)
    res = model(arr, device=device, retina_masks=True, imgsz=max(mos.size),
                conf=0.4, iou=0.9, verbose=False)
    if not res or res[0].masks is None:
        return []
    return [m.astype(bool) for m in res[0].masks.data.cpu().numpy()]


def filtrar_telhados(masks, mpp):
    """Fica só com máscaras com cara de telhado: área plausível + preenchimento alto
    (exclui ruas/vegetação/quadras). mpp = metros por pixel."""
    import numpy as np
    out = []
    for m in masks:
        ys, xs = np.where(m)
        if len(xs) < 80:
            continue
        area = len(xs) * mpp * mpp
        if not (20 <= area <= 1500):        # casa ~40-300; corta ruído e quadras/ruas
            continue
        minx, maxx, miny, maxy = xs.min(), xs.max(), ys.min(), ys.max()
        bw, bh = maxx - minx + 1, maxy - miny + 1
        fill = len(xs) / (bw * bh)
        if fill < 0.35:                     # forma espalhada = rua/pátio, não telhado
            continue
        ar = max(bw, bh) / max(1, min(bw, bh))
        if ar > 6:                          # muito alongado = muro/rua
            continue
        out.append({"xs": xs, "ys": ys, "area": area, "cxp": xs.mean(), "cyp": ys.mean(),
                    "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})
    return out


# ── classificação (crop nítido + contorno) ──────────────────────────────────────
def _crop_classificar(mos, t, idx):
    from PIL import Image, ImageDraw
    import numpy as np
    mg = int(max(t["maxx"] - t["minx"], t["maxy"] - t["miny"]) * 0.5) + 12
    l, u = max(0, t["minx"] - mg), max(0, t["miny"] - mg)
    r, d = min(mos.width, t["maxx"] + mg), min(mos.height, t["maxy"] + mg)
    lado = max(r - l, d - u)
    crop = mos.crop((l, u, l + lado, u + lado)).resize((360, 360))
    sc = 360 / lado
    # contorno da máscara (via casca do conjunto de pixels)
    ov = Image.new("RGBA", crop.size, (0, 0, 0, 0)); dr = ImageDraw.Draw(ov)
    step = max(1, len(t["xs"]) // 1200)
    for i in range(0, len(t["xs"]), step):
        px = (t["xs"][i] - l) * sc; py = (t["ys"][i] - u) * sc
        dr.point((px, py), fill=(255, 235, 0, 255))
    dr.text((6, 6), f"#{idx}", fill=(255, 235, 0, 255))
    crop = Image.alpha_composite(crop.convert("RGBA"), ov).convert("RGB")
    b = io.BytesIO(); crop.save(b, "JPEG", quality=88); raw = b.getvalue()
    try:
        payload = json.dumps({"model": MODELO, "prompt": PROMPT,
                              "images": [base64.b64encode(raw).decode()], "stream": False,
                              "format": "json", "options": {"num_ctx": 4096, "temperature": 0, "num_predict": 120}}).encode()
        req = urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"})
        out = json.loads(json.loads(urllib.request.urlopen(req, timeout=180).read()).get("response", "{}"))
    except Exception:
        out = {"tipo": "erro", "justificativa": ""}
    return raw, out.get("tipo") or "incerto", out.get("justificativa", "")


def galeria(ref, cards):
    import html
    css = ("*{box-sizing:border-box}body{margin:0;background:#0f1215;color:#e7ebef;font:15px/1.5 -apple-system,Segoe UI,sans-serif}"
           ".wrap{max-width:1200px;margin:0 auto;padding:24px 16px 60px}h1{font-size:1.35rem;margin:0 0 4px}"
           ".sub{color:#96a0ac;margin:0 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}"
           ".card{position:relative;background:#181c21;border:1px solid #282f37;border-radius:12px;overflow:hidden;border-top:4px solid #282f37}"
           ".card.predio{border-top-color:#e08571}.card.casa{border-top-color:#4cc38a}.card.outro{border-top-color:#dcb04a}"
           ".card img{display:block;width:100%;height:230px;object-fit:cover}.n{position:absolute;top:6px;left:6px;background:#000b;color:#ffeb00;font:700 11px ui-monospace;padding:2px 7px;border-radius:6px}"
           "figcaption{padding:9px 11px}.top{display:flex;justify-content:space-between;margin-bottom:5px}.tipo{font-weight:700;font-size:.78rem;padding:3px 9px;border-radius:99px}"
           ".tipo.predio{background:#331b17;color:#e08571}.tipo.casa{background:#123024;color:#4cc38a}.tipo.outro{background:#33280f;color:#dcb04a}"
           ".area{font:600 .82rem ui-monospace;color:#96a0ac}.just{font-size:.8rem;color:#96a0ac;line-height:1.4}")
    figs = []
    for c in cards:
        cls = "predio" if c["tipo"] == "predio" else "casa" if c["tipo"] == "casa_terrea" else "outro"
        uri = "data:image/jpeg;base64," + base64.b64encode(c["raw"]).decode()
        figs.append(f'<figure class="card {cls}"><span class="n">#{c["idx"]}</span><img src="{uri}">'
                    f'<figcaption><div class="top"><span class="tipo {cls}">{html.escape(c["tipo"])}</span>'
                    f'<span class="area">{c["area"]:.0f} m²</span></div><div class="just">{html.escape(c["just"])}</div></figcaption></figure>')
    doc = (f'<title>Telhados segmentados — {html.escape(ref)}</title><style>{css}</style><div class="wrap">'
           f'<h1>Telhados segmentados (FastSAM + Google z20) — {html.escape(ref)}</h1>'
           f'<p class="sub">{len(cards)} telhados detectados individualmente. Contorno amarelo = a máscara do alvo.</p>'
           f'<div class="grid">{"".join(figs)}</div></div>')
    out = BASE / "exemplos" / f"_telhados_seg_{ref}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(doc, "utf-8")
    return out


def run(bbox, ref, device):
    print(f"🛰️  Segmentando telhados [{ref}] | Google z{Z} + FastSAM ({device})", flush=True)
    mos, ox, oy = mosaico(bbox, Z)
    print(f"  mosaico {mos.size[0]}x{mos.size[1]}px baixado; rodando FastSAM…", flush=True)
    masks = segmentar(mos, device)
    mpp = 156543.03 * math.cos(math.radians((bbox[0] + bbox[2]) / 2)) / 2 ** Z
    telhados = filtrar_telhados(masks, mpp)
    print(f"  {len(masks)} máscaras → {len(telhados)} telhados após filtro", flush=True)
    if not telhados:
        print("  (nenhum telhado — ajuste a bbox ou os filtros)"); return
    telhados.sort(key=lambda t: (t["cyp"], t["cxp"]))       # ordem de leitura (cima→baixo)
    conn = realtime_ingest.conectar()
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("DELETE FROM telhados_seg WHERE area_ref=%s", (ref,))
    cards, resumo = [], {"predio": 0, "casa_terrea": 0, "incerto": 0, "erro": 0}
    for idx, t in enumerate(telhados, 1):
        raw, tipo, just = _crop_classificar(mos, t, idx)
        lat, lng = _inv(ox + t["cxp"], oy + t["cyp"], Z)
        resumo[tipo] = resumo.get(tipo, 0) + 1
        cards.append({"idx": idx, "area": t["area"], "tipo": tipo, "just": just, "raw": raw})
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO telhados_seg (area_ref, idx, area_m2, centro_lat, centro_lng, tipo, just) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)", (ref, idx, t["area"], lat, lng, tipo, just))
        if idx % 10 == 0:
            print(f"    {idx}/{len(telhados)}", flush=True)
    out = galeria(ref, cards)
    conn.close()
    print(f"\n  === RESUMO ===")
    print(f"  Telhados: {len(telhados)}  |  🏢 prédios {resumo.get('predio',0)} · "
          f"🏠 casas {resumo.get('casa_terrea',0)} · ❓ {resumo.get('incerto',0)+resumo.get('erro',0)}")
    print(f"  Galeria: {out}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", required=True, help="minlat,minlng,maxlat,maxlng (comece por 1 quadra)")
    p.add_argument("--rotulo", default="area", help="nome da área (chave em telhados_seg)")
    p.add_argument("--device", default="cpu", help="cpu | cuda (GPU, mais rápido)")
    a = p.parse_args()
    v = [float(x) for x in a.bbox.split(",")]
    if len(v) != 4:
        sys.exit("bbox precisa de 4 números: minlat,minlng,maxlat,maxlng")
    run((min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])), a.rotulo, a.device)
