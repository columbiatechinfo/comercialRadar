"""
ocr_pois.py — Extrai texto dos recortes gerados pelo detect_crops.py via EasyOCR
Lê crops.json, processa todos os recortes e salva:
  crops/ocr_resultado.json
  crops/ocr_resumo.html

USO:
  py ocr_pois.py capturas/teresinabairro1/session.json
  py ocr_pois.py capturas/teresinabairro1/session.json --max-crops 50
"""

import json
import sys
import time
from datetime import datetime
import re
import argparse
from pathlib import Path
import easyocr
import cv2
import numpy as np

PAD_H      = 180   # espelho do detect_crops.py — center_x fixo do ícone
MAX_DIST_X = 120   # janela horizontal máxima dentro do lado vencedor (px)
MIN_CONF   = 0.40

RE_ONLY_DIGITS = re.compile(r'^\d+$')


def run(session_path: Path, max_crops: int | None, max_dist_x: int, min_conf: float):
    crops_json = session_path.parent / 'crops.json'
    if not crops_json.exists():
        print(f"Erro: {crops_json} não encontrado. Rode detect_crops.py primeiro.")
        sys.exit(1)

    registros = json.loads(crops_json.read_text())
    if max_crops:
        registros = registros[:max_crops]

    crops_dir = session_path.parent / 'crops'

    print(f"\n🔍 ComercialRadar — OCR de POIs (EasyOCR)")
    print(f"   Sessão : {session_path.parent.name}")
    print(f"   Crops  : {len(registros)}")
    print(f"\n🤖 Carregando EasyOCR (pt + en)...")
    reader = easyocr.Reader(['pt', 'en'], gpu=False, verbose=False)
    print(f"   ✅ Pronto\n")

    resultados = []
    inicio = datetime.now()
    for i, reg in enumerate(registros):
        t_item = time.time()
        crop_path = crops_dir / Path(reg['crop']).name
        if not crop_path.exists():
            continue

        img = cv2.imread(str(crop_path))
        h, w = img.shape[:2]
        center_x = PAD_H

        ocr_raw = reader.readtext(img, detail=1, paragraph=False)

        validos = [
            t for t in ocr_raw
            if t[2] > min_conf and not RE_ONLY_DIGITS.match(t[1].strip())
        ]

        def box_center(box):
            return (box[0][0]+box[2][0])/2, (box[0][1]+box[2][1])/2

        def dominant_color_hsv(box):
            x0 = max(0, int(min(p[0] for p in box)))
            y0 = max(0, int(min(p[1] for p in box)))
            x1 = min(w, int(max(p[0] for p in box)))
            y1 = min(h, int(max(p[1] for p in box)))
            if x1 <= x0 or y1 <= y0:
                return None
            roi = img[y0:y1, x0:x1]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            mask_bg = cv2.inRange(hsv, (0, 0, 200), (180, 30, 255))
            mask_fg = cv2.bitwise_not(mask_bg)
            pixels = hsv[mask_fg > 0]
            if len(pixels) == 0:
                return None
            return float(np.mean(pixels[:, 0])), float(np.mean(pixels[:, 1]))

        if not validos:
            texto = ''
            ancora = None
        else:
            # Âncora: maior (conf / distância ao center_x)
            # O fragmento mais colado ao ícone com maior confiança elege o lado
            def score(t):
                cx, cy = box_center(t[0])
                dist = abs(cx - center_x) + 1
                return t[2] / dist

            ancora = max(validos, key=score)
            ancora_cx    = box_center(ancora[0])[0]
            ancora_cy    = box_center(ancora[0])[1]
            ancora_h     = max(ancora[0][2][1] - ancora[0][0][1], 12)
            ancora_color = dominant_color_hsv(ancora[0])

            lado_dir   = ancora_cx >= center_x
            MAX_DIST_Y = max(ancora_h * 1.5 + 8, 28)

            grupo = []
            for t in validos:
                tcx, tcy = box_center(t[0])

                # Filtro 1: lado do âncora com janela X assimétrica
                # Direita: center_x → center_x + max_dist_x
                # Esquerda: sem limite inferior (label pode estar cortado na borda)
                if lado_dir:
                    if not (center_x <= tcx <= center_x + max_dist_x):
                        continue
                else:
                    if tcx > center_x:
                        continue

                # Filtro 2: janela vertical ao âncora
                if abs(tcy - ancora_cy) > MAX_DIST_Y:
                    continue

                # Filtro 3: cor similar ao âncora
                cor_ok = True
                if ancora_color is not None:
                    t_color = dominant_color_hsv(t[0])
                    if t_color is not None:
                        dh = abs(t_color[0] - ancora_color[0])
                        dh = min(dh, 180 - dh)
                        ds = abs(t_color[1] - ancora_color[1])
                        cor_ok = dh < 25 and ds < 60

                if cor_ok:
                    grupo.append(t)

            # Ordena por (linha-bucket, cx) — leitura natural
            BUCKET_Y = max(int(ancora_h * 0.9), 12)
            items_sorted = sorted(
                grupo,
                key=lambda t: (
                    int(box_center(t[0])[1] // BUCKET_Y),
                    box_center(t[0])[0]
                )
            )
            texto = ' '.join(t[1] for t in items_sorted).strip()

        reg['ocr_texto']    = texto
        reg['ocr_ancora']   = ancora[1] if ancora else ''
        reg['ocr_detalhes'] = [
            {'texto': t[1], 'conf': round(t[2], 3),
             'cx': int((t[0][0][0]+t[0][2][0])/2)}
            for t in ocr_raw
        ]
        reg['processado_em'] = datetime.now().isoformat(timespec='seconds')
        reg['duracao_s']     = round(time.time() - t_item, 2)
        resultados.append(reg)

        status = '✔' if texto else '✗'
        print(f"  [{i+1:4}/{len(registros)}] {status} {texto[:70]}")

    print(f"\n\n✅ OCR concluído: {len(resultados)} crops")

    out_json = crops_dir / 'ocr_resultado.json'
    out_json.write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    fim = datetime.now()
    duracao = fim - inicio
    h = int(duracao.total_seconds()//3600)
    m = int((duracao.total_seconds()%3600)//60)
    s = int(duracao.total_seconds()%60)

    meta = {
        'iniciado_em': inicio.isoformat(timespec='seconds'),
        'concluido_em': fim.isoformat(timespec='seconds'),
        'duracao': f"{h:02d}:{m:02d}:{s:02d}",
        'duracao_s': round(duracao.total_seconds(), 1),
        'total_crops': len(resultados),
        'duracao_media_s': round(sum(r.get('duracao_s',0) for r in resultados)/len(resultados), 2) if resultados else 0,
    }
    (crops_dir / 'ocr_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"💾 JSON: {out_json}")
    print(f"⏱  Duração: {h:02d}:{m:02d}:{s:02d} | {len(resultados)} crops")

    rows = ""
    for r in resultados:
        nome   = Path(r['crop']).name
        texto  = r.get('ocr_texto', '')
        conf   = r.get('ocr_detalhes', [])
        conf_s = ' | '.join(f"{d['texto']} ({d['conf']:.2f})" for d in conf)
        bg     = '#0d2a0d' if texto and len(texto) > 3 else '#2a0d0d'
        rows += f"""
        <tr style="background:{bg}">
          <td><img src="{nome}" style="max-width:360px;height:auto;display:block;image-rendering:auto"></td>
          <td style="font-size:1.15em;font-weight:bold;color:#ffe">{texto or '<em style="color:#888">—</em>'}<br>
              <span style="font-size:0.7em;color:#4fc">âncora: {r.get("ocr_ancora","")}</span></td>
          <td style="font-size:0.75em;color:#888">{conf_s}</td>
          <td style="font-size:0.8em;color:#aaa">{r['cor']}<br>{r['lat']:.5f},{r['lng']:.5f}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>OCR POIs — ComercialRadar</title>
<style>
  body  {{ font-family:sans-serif; background:#111; color:#eee; margin:0; }}
  h1    {{ padding:14px; color:#4fc; margin:0; font-size:1.2em; }}
  table {{ border-collapse:collapse; width:100%; }}
  th    {{ background:#222; padding:8px; text-align:left; position:sticky; top:0; font-size:0.85em; }}
  td    {{ border-bottom:1px solid #1e1e1e; padding:8px; vertical-align:middle; }}
</style></head><body>
<h1>🔤 OCR POIs — {len(resultados)} recortes de {session_path.parent.name}</h1>
<table>
  <tr><th>Recorte</th><th>Texto OCR</th><th>Detalhes</th><th>Info</th></tr>
  {rows}
</table></body></html>"""

    out_html = crops_dir / 'ocr_resumo.html'
    out_html.write_text(html, encoding='utf-8')
    print(f"🌐 HTML: {out_html}")


def main():
    parser = argparse.ArgumentParser(description="OCR dos recortes de POIs")
    parser.add_argument('session', help='Caminho para session.json')
    parser.add_argument('--max-crops', type=int, default=None,
                        help='Limita crops processados (testes rápidos)')
    parser.add_argument('--max-dist-x', type=int, default=MAX_DIST_X,
                        help='Janela horizontal em px a partir do ícone (default: 120)')
    parser.add_argument('--min-conf', type=float, default=MIN_CONF,
                        help='Confiança mínima do OCR (default: 0.40)')
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        sys.exit(1)

    run(session_path, args.max_crops, args.max_dist_x, args.min_conf)


if __name__ == '__main__':
    main()
