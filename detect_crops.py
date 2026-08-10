"""
detect_crops.py — Detecta ícones de POI e gera recortes centralizados.
Para cada tile da sessão:
  1. Detecta ícones via OpenCV (HoughCircles + HSV)
  2. Gera recorte 360x70px centralizado no ícone
  3. Salva em crops/ dentro da pasta da sessão
  4. Gera crops/resumo.html para visualização

USO:
  py detect_crops.py capturas/teresinabairro1/session.json
  py detect_crops.py capturas/teresinabairro1/session.json --max-tiles 10
"""

import sys
import math
from datetime import datetime
import json
import argparse
import re
from pathlib import Path

import time
import cv2
import numpy as np

# ── Cores dos ícones do Maps (HSV) ───────────────────────────────────────────
COLOR_RANGES = [
    ("vermelho",  np.array([0,   120, 100]), np.array([10,  255, 255])),
    ("vermelho2", np.array([170, 120, 100]), np.array([180, 255, 255])),
    ("laranja",   np.array([11,  120,  80]), np.array([25,  255, 255])),
    ("amarelo",   np.array([26,  120,  80]), np.array([34,  255, 255])),
    ("verde",     np.array([40,   80,  80]), np.array([90,  255, 255])),
    ("azul",      np.array([95,   80,  80]), np.array([130, 255, 255])),
    ("roxo",      np.array([131,  60,  80]), np.array([160, 255, 255])),
    ("rosa",      np.array([161,  60,  80]), np.array([169, 255, 255])),
]

PAD_H      = 180   # margem horizontal — ícone fica em center_x=PAD_H
PAD_V      = 35    # margem vertical
MIN_RADIUS = 6
MAX_RADIUS = 40

# ── Ícone GENÉRICO (o circulozinho cinza) ────────────────────────────────────
# As faixas acima exigem saturação ≥ 60, e o ícone que a MAIORIA dos comércios
# tem no Maps é cinza-claro com um ponto escuro — saturação ~0. Ele nunca entrava
# na máscara, o HoughCircles não tinha o que achar e o POI simplesmente não
# existia para o processo. Medido num tile de Canoas: 43 ícones coloridos
# detectados contra 97 genéricos ignorados — 38% de cobertura.
#
# O desenho é SEMPRE o mesmo (o estilo do mapa é fixo pelo MAPS_MAP_ID em
# src/capture.ts, e a captura é sempre 3840×2160), então casar por template
# resolve. Se o estilo ou a resolução mudarem, o template tem de ser refeito:
#   py detect_crops.py <session.json> --refazer-template <px> <py>
TEMPLATE_POI  = Path(__file__).resolve().parent / "assets" / "icone_poi_generico.png"
TEMPLATE_MIN  = 0.72   # correlação mínima (TM_CCOEFF_NORMED)
DEDUP_PX      = 22     # dois centros a menos disto são o MESMO ícone


_TPL = None


def _template():
    """Carrega o template uma vez por processo. Ausente, o detector segue só com
    as cores — perde os genéricos, mas não quebra."""
    global _TPL
    if _TPL is None:
        _TPL = (cv2.imread(str(TEMPLATE_POI), cv2.IMREAD_GRAYSCALE)
                if TEMPLATE_POI.exists() else False)
        if _TPL is False:
            print(f"      ⚠️  {TEMPLATE_POI.name} não encontrado — só ícones "
                  f"coloridos serão detectados", flush=True)
    return _TPL if _TPL is not False else None


def detect_genericos(gray) -> list:
    """Centros dos ícones cinza, por correlação com o template.

    Devolve (px, py). O raio não sai daqui: o template tem tamanho fixo, então
    o raio é sempre o mesmo — e é só o que `make_crop` usa para desenhar o
    círculo de conferência."""
    tpl = _template()
    if tpl is None:
        return []
    r = tpl.shape[0] // 2
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= TEMPLATE_MIN)
    if not len(xs):
        return []
    # do mais forte para o mais fraco, para o vencedor de cada aglomerado ser o
    # de melhor correlação e não o que aparecer primeiro na varredura
    ordem = sorted(zip(xs, ys), key=lambda p: -res[p[1], p[0]])
    achados = []
    for x, y in ordem:
        cx, cy = int(x + r), int(y + r)
        if any((cx - ax) ** 2 + (cy - ay) ** 2 < DEDUP_PX ** 2 for ax, ay in achados):
            continue
        achados.append((cx, cy))
    return achados


def detect_icons(image_path: Path) -> list:
    img = cv2.imread(str(image_path))
    if img is None:
        return []
    img_h, img_w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for _, lo, hi in COLOR_RANGES:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lo, hi))

    kernel  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask    = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask    = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
    blurred = cv2.GaussianBlur(mask, (9, 9), 2)

    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT,
        dp=1.2, minDist=20,
        param1=50, param2=18,
        minRadius=MIN_RADIUS, maxRadius=MAX_RADIUS,
    )
    if circles is None:
        return []

    icons, seen = [], set()
    for (px, py, r) in np.round(circles[0]).astype(int):
        key = (px // 15, py // 15)
        if key in seen:
            continue
        seen.add(key)

        roi = hsv[max(0, py-r):py+r, max(0, px-r):px+r]
        color, best = "desconhecida", 0
        for cname, lo, hi in COLOR_RANGES:
            cnt = cv2.countNonZero(cv2.inRange(roi, lo, hi))
            if cnt > best:
                best, color = cnt, cname

        roi_size = max(1, roi.shape[0] * roi.shape[1])
        if best / roi_size < 0.08:
            continue

        icons.append({
            "px": int(px), "py": int(py), "raio": int(r),
            "cor": color, "img_w": img_w, "img_h": img_h,
        })

    # segunda passada: os cinzas, que a máscara de cor não enxerga
    tpl = _template()
    if tpl is not None:
        raio = tpl.shape[0] // 2
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for cx, cy in detect_genericos(gray):
            # o ícone colorido também tem um anel claro em volta e casa com o
            # template: sem esta checagem o mesmo POI entraria duas vezes e
            # viraria duas buscas no Maps
            if any((cx - i["px"]) ** 2 + (cy - i["py"]) ** 2 < DEDUP_PX ** 2
                   for i in icons):
                continue
            icons.append({
                "px": cx, "py": cy, "raio": raio,
                "cor": "generico", "img_w": img_w, "img_h": img_h,
            })

    return icons


def make_crop(tile_path: Path, ic: dict, crops_dir: Path, idx: int) -> Path | None:
    img = cv2.imread(str(tile_path))
    if img is None:
        return None

    px, py = ic["px"], ic["py"]
    h, w   = img.shape[:2]

    x0s = px - PAD_H; y0s = py - PAD_V
    x1s = px + PAD_H; y1s = py + PAD_V

    x0 = max(0, x0s); y0 = max(0, y0s)
    x1 = min(w, x1s); y1 = min(h, y1s)
    crop_raw = img[y0:y1, x0:x1]

    cw = PAD_H * 2
    ch = PAD_V * 2
    canvas = np.ones((ch, cw, 3), dtype=np.uint8) * 255

    dst_x = x0 - x0s
    dst_y = y0 - y0s
    canvas[dst_y:dst_y+crop_raw.shape[0],
           dst_x:dst_x+crop_raw.shape[1]] = crop_raw

    cv2.line(canvas, (PAD_H, 0), (PAD_H, ch), (0, 0, 200), 1)
    cv2.circle(canvas, (PAD_H, PAD_V), ic["raio"], (0, 180, 255), 1)

    cv2.rectangle(canvas, (0, 0), (46, 16), (30, 30, 30), -1)
    cv2.putText(canvas, str(idx), (2, 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    fname = f"crop_{tile_path.stem}_{px:04d}_{py:04d}.jpg"
    out   = crops_dir / fname
    cv2.imwrite(str(out), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return out


def generate_html(crops_dir: Path, registros: list):
    rows = ""
    for r in registros:
        crop_rel = Path(r["crop"]).name
        rows += f"""
        <tr>
          <td><img src="{crop_rel}" width="360" style="image-rendering:auto"></td>
          <td>{r["tile"]}</td>
          <td>{r["cor"]}</td>
          <td>px=({r["px"]},{r["py"]})<br>r={r["raio"]}</td>
          <td>{r["lat"]:.6f}<br>{r["lng"]:.6f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Recortes POIs — ComercialRadar</title>
<style>
  body  {{ font-family:sans-serif; background:#1a1a1a; color:#eee; }}
  h1    {{ padding:16px; color:#4fc; }}
  table {{ border-collapse:collapse; width:100%; }}
  th    {{ background:#333; padding:8px; text-align:left; }}
  td    {{ border-bottom:1px solid #333; padding:8px; vertical-align:middle; }}
  img   {{ border-radius:6px; display:block; }}
</style></head><body>
<h1>🔍 Recortes de POIs — {len(registros)} detectados</h1>
<table>
  <tr><th>Recorte</th><th>Tile</th><th>Cor</th><th>Posição</th><th>Coordenada</th></tr>
  {rows}
</table></body></html>"""
    (crops_dir / "resumo.html").write_text(html, encoding="utf-8")


def pixel_to_latlon(px, py, tile_lat, tile_lng, zoom, img_w, img_h):
    lng_per_tile = (img_w * 360) / (256 * (2 ** zoom))
    lat_per_tile = (lng_per_tile * (img_h / img_w)
                    / math.cos(math.radians(tile_lat)))
    return (
        round(tile_lat - ((py / img_h) - 0.5) * lat_per_tile, 7),
        round(tile_lng + ((px / img_w) - 0.5) * lng_per_tile, 7),
    )


def run(session_path: Path, max_tiles: int | None):
    inicio    = datetime.now()
    session   = json.loads(session_path.read_text())
    zoom      = session.get("config", {}).get("zoomLevel", 19)
    tiles_dir = session_path.parent
    crops_dir = tiles_dir / "crops"
    crops_dir.mkdir(exist_ok=True)

    tiles = sorted(tiles_dir.glob("tile_*.png"))
    if max_tiles:
        tiles = tiles[:max_tiles]

    print(f"\n🔍 ComercialRadar — Detecção de POIs")
    print(f"   Sessão : {session_path.parent.name}")
    print(f"   Tiles  : {len(tiles)}")
    print(f"   Saída  : {crops_dir}\n")

    registros   = []
    total_icons = 0

    for t_idx, tile_path in enumerate(tiles):
        m = re.search(r"_(-?\d+\.\d+)_(-?\d+\.\d+)\.png$", tile_path.name)
        if not m:
            continue
        tile_lat = float(m.group(1))
        tile_lng = float(m.group(2))

        icons = detect_icons(tile_path)
        print(f"  [{t_idx+1:4}/{len(tiles)}] {tile_path.name[:50]:50} → {len(icons):3} ícones", flush=True)

        for ic in icons:
            total_icons += 1
            lat, lng = pixel_to_latlon(
                ic["px"], ic["py"],
                tile_lat, tile_lng, zoom,
                ic["img_w"], ic["img_h"],
            )
            crop_path = make_crop(tile_path, ic, crops_dir, total_icons)
            if crop_path:
                registros.append({
                    "idx":  total_icons,
                    "tile": tile_path.name,
                    "crop": str(crop_path),
                    "px":   ic["px"],
                    "py":   ic["py"],
                    "raio": ic["raio"],
                    "cor":  ic["cor"],
                    "lat":  lat,
                    "lng":  lng,
                })

    fim = datetime.now()
    duracao = fim - inicio
    h = int(duracao.total_seconds()//3600)
    m = int((duracao.total_seconds()%3600)//60)
    s = int(duracao.total_seconds()%60)

    json_path = tiles_dir / "crops.json"
    json_path.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    generate_html(crops_dir, registros)

    meta = {
        'iniciado_em': inicio.isoformat(timespec='seconds'),
        'concluido_em': fim.isoformat(timespec='seconds'),
        'duracao': f"{h:02d}:{m:02d}:{s:02d}",
        'duracao_s': round(duracao.total_seconds(), 1),
        'tiles_processados': len(tiles),
        'icones_detectados': total_icons,
    }
    (tiles_dir / 'crops_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    print(f"\n✅ Concluído em {h:02d}:{m:02d}:{s:02d}!")
    print(f"   Ícones detectados : {total_icons}")
    print(f"   JSON              : {json_path}")
    print(f"   Visualização      : {crops_dir / 'resumo.html'}")


def main():
    parser = argparse.ArgumentParser(description="Detecta POIs e gera recortes")
    parser.add_argument("session", help="Caminho para session.json")
    parser.add_argument("--max-tiles", type=int, default=None,
                        help="Limita tiles processados (testes rápidos)")
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        sys.exit(1)

    run(session_path, args.max_tiles)


if __name__ == "__main__":
    main()
