# -*- coding: utf-8 -*-
"""
telhados_cv.py — Identificador de telhados: conta cada edificação de uma área, mede
área (m²) e ALTURA (m/pavimentos) e infere o tipo em 4 categorias simples:
casa_terrea | predio | galpao (empresa) | terreno_vazio.

ALTURA/TIPO (analisar_e_tipar — calibrado com o cliente em Teresina):
  • sombra projetada no chão (comprimento → metros; direção do sol detectada sozinha);
  • fachada visível no z21 oblíquo (autocalibrada px→m pelos que mediram sombra) —
    resolve bloco denso onde a sombra cai no telhado vizinho;
  • grade de JANELAS pequenas na fachada (≥12 = prédio; casa tem 1-2, galpão parede lisa);
  • FRISOS direcionais do telhado metálico (≥0.25 = galpão, veto);
  • vizinhança: fragmento alto cercado por ≥2 prédios do mesmo telhado = prédio
    (condomínio); resgate de altura pela mediana do grupo (raio 80m, sem percolar).

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
from segmentar_telhados import _num, _inv, mosaico, GOOGLE, OLLAMA, MODELO  # geo/tiles já testados

Z = 21   # zoom Google padrão. z21 ~0,075 m/px = detalhe REAL (telha, caixa d'água, carro);
         # z22 já é upscale (borra, 4x mais tiles). z20 era grosseiro demais p/ o tipo.
BASE = Path(__file__).resolve().parent
EXEMPLOS = BASE / "exemplos" / "telhados"          # exemplos/telhados/<tipo>/*.jpg

# 4 categorias, simples (decisão do cliente: sem "alto padrão")
TIPOS = ["casa_terrea", "predio", "galpao", "terreno_vazio"]
ROTULO_PT = {"casa_terrea": "casa térrea", "predio": "prédio", "galpao": "galpão / empresa",
             "terreno_vazio": "terreno vazio", "indeterminado": "indeterminado"}

DDL = """
CREATE TABLE IF NOT EXISTS telhados (
  area_ref text, idx int, material text, area_m2 double precision,
  centro_lat double precision, centro_lng double precision,
  largura_m double precision, comprimento_m double precision,
  tipo text, origem_tipo text, just text, criado_em timestamptz DEFAULT now()
);
ALTER TABLE telhados ADD COLUMN IF NOT EXISTS altura_m double precision;
ALTER TABLE telhados ADD COLUMN IF NOT EXISTS andares int;
ALTER TABLE telhados ADD COLUMN IF NOT EXISTS janelas int;
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


# ── altura + tipo: sombra, fachada, janelas, frisos e vizinhança ─────────────────
# Calibrado em Teresina (amostra revisada pelo cliente): casas térreas 100%, prédios
# de condomínio 28/28, galpões separados por textura. Ver DOCUMENTACAO.md §14.
def analisar_e_tipar(roofs, arr, mpp):
    """Anota cada telhado com altura_m/andares/janelas e decide o TIPO (4 categorias).
    Física usada: sombra projetada no chão (altura), fachada visível no z21 oblíquo
    (altura autocalibrada onde a sombra está tampada), grade de janelas (prédio),
    frisos direcionais (galpão metálico), vizinhança de rótulo (condomínio)."""
    import cv2
    import numpy as np
    H, W = arr.shape[:2]
    lum = arr.mean(2)
    Rr, Gg, Bb = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
    verde_px = (Gg > Rr + 8) & (Gg > Bb + 8)
    build = np.zeros((H, W), np.uint8)
    for r in roofs:
        cv2.fillPoly(build, [np.array(r["box"], np.int32)], 1)
    shadow = ((lum < 75) & (~verde_px) & (build == 0)).astype(np.uint8)

    # direção global da sombra (desloca a máscara de prédios e vê onde cai em sombra)
    bf, sf = build.astype(np.float32), shadow.astype(np.float32)
    best = (-1, 1.0, 0.0)
    for ang in range(0, 360, 8):
        for mag in (10, 18, 28):
            dx, dy = mag * math.cos(math.radians(ang)), mag * math.sin(math.radians(ang))
            ov = float((cv2.warpAffine(bf, np.float32([[1, 0, dx], [0, 1, dy]]), (W, H)) * sf).sum())
            if ov > best[0]:
                best = (ov, math.cos(math.radians(ang)), math.sin(math.radians(ang)))
    ux, uy = best[1], best[2]

    MAXK = 70

    def _mask_local(r, pad):
        poly = np.array(r["box"], np.int32)
        x, y, w, h = cv2.boundingRect(poly)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, [poly - [x0, y0]], 1)
        return m, x0, y0, x1, y1

    def sombra_len(r):
        """Sombra no chão (m). None = pé da sombra tampado pelo vizinho (não mede)."""
        m, x0, y0, x1, y1 = _mask_local(r, MAXK + 4)
        sh = shadow[y0:y1, x0:x1]; bd = build[y0:y1, x0:x1]
        fr, ini_ocl, ini_n = [], 0, 0
        for k in range(2, MAXK, 2):
            sm = cv2.warpAffine(m, np.float32([[1, 0, ux * k], [0, 1, uy * k]]), (m.shape[1], m.shape[0]))
            novo = (sm == 1) & (m == 0)
            if novo.sum() < 15:
                continue
            livre = novo & (bd == 0)
            if k <= 12:
                ini_n += 1
                if livre.sum() < max(15, novo.sum() * 0.30):
                    ini_ocl += 1
            if livre.sum() < max(15, novo.sum() * 0.25):
                continue
            fr.append((k, float(sh[livre].mean())))
        if ini_n and ini_ocl / ini_n >= 0.5:
            return None
        run = 0
        for k, f in fr:
            if f >= 0.45:
                run = k
            elif k > run + 6:
                break
        return run * mpp

    def fachada_px(r):
        """Largura (px) da banda escura da FACHADA na borda do lado da sombra."""
        m, x0, y0, x1, y1 = _mask_local(r, 36)
        lu = lum[y0:y1, x0:x1]
        core = cv2.erode(m, np.ones((9, 9), np.uint8), 2)
        if core.sum() < 30:
            core = m
        ref = float(lu[core > 0].mean())
        npx = 0
        for k in range(1, 34):
            smk = cv2.warpAffine(m, np.float32([[1, 0, -ux * k], [0, 1, -uy * k]]), (m.shape[1], m.shape[0]))
            anel = (m == 1) & (smk == 0)
            if k > 1:
                sm2 = cv2.warpAffine(m, np.float32([[1, 0, -ux * (k - 1)], [0, 1, -uy * (k - 1)]]), (m.shape[1], m.shape[0]))
                anel = anel & ~((m == 1) & (sm2 == 0))
            if anel.sum() < 8:
                break
            if float(lu[anel].mean()) < ref - 22:
                npx = k
            elif k > npx + 3:
                break
        return npx

    def janelas_fachada(r):
        """Nº de janelas pequenas na fachada visível (grade de janelas = prédio)."""
        m, x0, y0, x1, y1 = _mask_local(r, 40)
        if m.shape[0] < 10 or m.shape[1] < 10:
            return 0
        kb = max(8, r["fach_px"] + 6)
        smk = cv2.warpAffine(m, np.float32([[1, 0, -ux * kb], [0, 1, -uy * kb]]), (m.shape[1], m.shape[0]))
        banda = cv2.dilate(((m == 1) & (smk == 0)).astype(np.uint8), np.ones((5, 5), np.uint8), 1)
        if banda.sum() < 60:
            return 0
        lu = lum[y0:y1, x0:x1]
        med = float(lu[banda > 0].mean()); sd = float(lu[banda > 0].std())
        if sd < 6:
            return 0
        n = 0
        for mm in (((lu > med + 0.9 * sd) & (banda > 0)), ((lu < med - 0.9 * sd) & (banda > 0))):
            nc, _, stats, _ = cv2.connectedComponentsWithStats(mm.astype(np.uint8), 8)
            for i in range(1, nc):
                if 4 <= stats[i, cv2.CC_STAT_AREA] <= 160 and \
                   stats[i, cv2.CC_STAT_WIDTH] <= 22 and stats[i, cv2.CC_STAT_HEIGHT] <= 22:
                    n += 1
        return n

    def friso(r):
        """Concentração direcional do gradiente no miolo: frisos metálicos ≥0.25."""
        m, x0, y0, x1, y1 = _mask_local(r, 0)
        if m.shape[0] < 8 or m.shape[1] < 8:
            return 0.0
        m = cv2.erode(m, np.ones((5, 5), np.uint8), 1)
        lu = lum[y0:y1, x0:x1].astype(np.float32)
        gx = cv2.Sobel(lu, cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(lu, cv2.CV_32F, 0, 1, 3)
        mag = np.sqrt(gx * gx + gy * gy)
        sel = (m > 0) & (mag > 25)
        if sel.sum() < 40:
            return 0.0
        ang = (np.degrees(np.arctan2(gy[sel], gx[sel])) + 180) % 180
        hist, _ = np.histogram(ang, bins=18, range=(0, 180), weights=mag[sel])
        return float(hist.max() / max(1e-6, hist.sum()))

    def cor_miolo(r):
        m, x0, y0, x1, y1 = _mask_local(r, 0)
        m = cv2.erode(m, np.ones((7, 7), np.uint8), 1)
        px = arr[y0:y1, x0:x1][m > 0]
        return px.mean(axis=0) if len(px) > 20 else np.array([0., 0., 0.])

    def vazio(r):
        """Footprint sem estrutura: chão batido/vegetação, sem fachada nem sombra."""
        if r["altura_m"] >= 1.5 or r["fach_px"] >= 3:
            return False
        m, x0, y0, x1, y1 = _mask_local(r, 0)
        px = arr[y0:y1, x0:x1][m > 0].astype("float32")
        if len(px) < 40:
            return False
        rr, gg, bb = px[:, 0].mean(), px[:, 1].mean(), px[:, 2].mean()
        verde = gg > rr + 6 and gg > bb + 6
        solo = rr > gg > bb and rr - bb > 18 and rr > 110
        return (verde or solo) and float(px.mean(axis=1).std()) < 14

    # ── medições por telhado ──
    for r in roofs:
        r["somb_raw"] = sombra_len(r)
        r["fach_px"] = fachada_px(r)
        poly = np.array(r["box"], np.float32)
        (_, (rw, rh), _) = cv2.minAreaRect(poly)
        r["fill_rect"] = cv2.contourArea(poly) / max(1.0, rw * rh)
        r["janelas"] = janelas_fachada(r)
        r["friso"] = friso(r)

    # autocalibração da fachada (px→m) pelos que mediram sombra alta
    calib = [(r["fach_px"], r["somb_raw"]) for r in roofs
             if r["somb_raw"] is not None and r["somb_raw"] >= 3.5 and r["fach_px"] >= 3]
    escala = float(np.median([s / f for f, s in calib])) if len(calib) >= 3 else None

    # fusão sombra+fachada
    for r in roofs:
        s = r["somb_raw"] if r["somb_raw"] is not None else 0.0
        f = (r["fach_px"] * escala) if (escala and r["fach_px"] >= 3) else 0.0
        r["altura_m"] = max(s, f)

    # resgate pelo grupo: bloco cuja medição falhou herda dos ≥2 blocos altos ≤80m
    seeds = [q for q in roofs if q["material"] == "clara" and q["altura_m"] >= 3.5
             and q["area_m2"] >= 140]
    for r in roofs:
        if r["material"] != "clara" or r["altura_m"] >= 3.5 or not (60 <= r["area_m2"] <= 900):
            continue
        viz = [q["altura_m"] for q in seeds if q is not r
               and math.hypot(q["cxp"] - r["cxp"], q["cyp"] - r["cyp"]) <= 80 / mpp]
        if len(viz) >= 2:
            r["altura_m"] = float(np.median(viz))
            r["cluster_alto"] = True

    # padrão de condomínio: grupo de blocos altos com porte de bloco
    for r in roofs:
        if r["material"] == "clara" and r["altura_m"] >= 3.5 and r["area_m2"] >= 60:
            nviz = sum(1 for q in roofs
                       if q is not r and q["material"] == "clara"
                       and q["altura_m"] >= 3.5 and q["area_m2"] >= 140
                       and math.hypot(q["cxp"] - r["cxp"], q["cyp"] - r["cyp"]) <= 120 / mpp)
            if nviz >= 2:
                r["cluster_alto"] = True

    for r in roofs:
        s = r["altura_m"]
        r["andares"] = max(1, int(round(s / 3.0)) + (1 if s >= 2 else 0))

    # ── tipo (4 categorias) ──
    def tipo_de(r):
        s, a = r["altura_m"], r["area_m2"]
        if vazio(r):
            return "terreno_vazio", "footprint sem estrutura (chão batido/vegetação)"
        if r["material"] == "ceramica":
            return "casa_terrea", "telha cerâmica (residência)"
        if s >= 3.5:
            if a < 60:
                return "casa_terrea", "estrutura pequena (telheiro/anexo)"
            if r["friso"] >= 0.25:
                return "galpao", f"telhado metálico de frisos (~{s:.0f}m) — galpão/empresa"
            if a > 900:
                return "galpao", f"pavilhão alto de grande porte (~{s:.0f}m)"
            if a <= 900 and r.get("cluster_alto") and r["fill_rect"] < 0.98:
                return "predio", f"multiandar: ~{s:.0f}m ≈ {r['andares']} pav., {r['janelas']} janelas"
            if 140 <= a and r["fill_rect"] < 0.96 and r["janelas"] >= 12:
                return "predio", f"multiandar isolado: ~{s:.0f}m ≈ {r['andares']} pav., {r['janelas']} janelas"
            if a >= 100:
                return "galpao", f"construção alta retangular/lisa (~{s:.0f}m) — galpão/empresa"
            return "casa_terrea", "estrutura pequena alta (anexo/caixa d'água)"
        if a >= 160:
            return "galpao", "laje/metálica ampla e térrea (galpão/empresa)"
        return "casa_terrea", "laje pequena térrea (casa de laje)"

    for r in roofs:
        r["tipo"], r["just"] = tipo_de(r)

    # 2ª passada: "galpão" alto de porte de bloco cercado por ≥2 prédios com o MESMO
    # telhado (cor) é fragmento do condomínio → prédio.
    preds = [q for q in roofs if q["tipo"] == "predio"]
    for q in preds:
        q["_cor"] = cor_miolo(q)
    for r in roofs:
        if r["tipo"] != "galpao" or r["altura_m"] < 3.5 or not (100 <= r["area_m2"] <= 900):
            continue
        if r["friso"] >= 0.25:
            continue
        viz = [q for q in preds
               if math.hypot(q["cxp"] - r["cxp"], q["cyp"] - r["cyp"]) <= 80 / mpp]
        if len(viz) < 2:
            continue
        cor_v = np.median(np.array([q["_cor"] for q in viz]), axis=0)
        if float(np.abs(cor_miolo(r) - cor_v).mean()) <= 22:
            r["tipo"] = "predio"
            r["just"] = f"fragmento de bloco: {len(viz)} prédios vizinhos, telhado idêntico (~{r['altura_m']:.0f}m)"
    return roofs


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
           ".card.casa_terrea{border-top-color:#4cc38a}.card.terreno_vazio{border-top-color:#7a828c}"
           ".card.predio{border-top-color:#e08571}.card.galpao{border-top-color:#dcb04a}.card.indeterminado,.card.erro{border-top-color:#7a828c}"
           ".card img{display:block;width:100%;height:220px;object-fit:cover}"
           ".n{position:absolute;top:6px;left:6px;background:#000b;color:#ffeb00;font:700 11px ui-monospace;padding:2px 7px;border-radius:6px}"
           ".h{position:absolute;top:6px;right:6px;background:#000b;color:#7fd7ff;font:700 11px ui-monospace;padding:2px 7px;border-radius:6px}"
           "figcaption{padding:9px 11px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:6px}"
           ".tipo{font-weight:700;font-size:.74rem;padding:3px 9px;border-radius:99px;background:#222831;color:#cfd6de}"
           ".card.casa_terrea .tipo{background:#123024;color:#4cc38a}.card.terreno_vazio .tipo{background:#23272d;color:#9aa4af}"
           ".card.predio .tipo{background:#331b17;color:#e08571}.card.galpao .tipo{background:#33280f;color:#dcb04a}"
           ".area{font:600 .8rem ui-monospace;color:#96a0ac;white-space:nowrap}"
           ".dim{font:.72rem ui-monospace;color:#6b7480}.just{font-size:.78rem;color:#96a0ac;line-height:1.4;margin-top:3px}")
    figs = []
    for c in cards:
        cls = c["tipo"] if c["tipo"] in TIPOS else "indeterminado"
        uri = "data:image/jpeg;base64," + base64.b64encode(c["raw"]).decode()
        alt = c.get("altura", 0.0)
        hlabel = f'{alt:.0f}m·{c.get("andares",1)}pav' if alt >= 1 else "térreo"
        figs.append(
            f'<figure class="card {cls}"><span class="n">#{c["idx"]}</span><span class="h">{hlabel}</span><img src="{uri}">'
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
           f'🏢 {n["predio"]} prédios · 🏭 {n["galpao"]} galpões · ⬜ {n["terreno_vazio"]} terrenos vazios</p>'
           f'<div class="leg"><span>Contorno amarelo = o telhado medido. Etiqueta azul = altura (m·pavimentos).</span>'
           f'<span>Detecção: {det}. {"Tipo refinado pela IA com exemplos." if usou_ia else "Tipo por altura (sombra+fachada) + janelas + frisos + vizinhança."}</span></div>'
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
    roofs = [r for r in roofs if r["area_m2"] >= 20]        # corta ruído de footprint
    if not roofs:
        print("  (nada detectado — confira a área/fonte)"); return
    print("  medindo altura (sombra+fachada) e tipando…", flush=True)
    analisar_e_tipar(roofs, arr, mpp)
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
            tipo, just = r["tipo"], r["just"]              # de analisar_e_tipar
        origem = f"{fonte}+{'ia_exemplos' if usar_ia else 'altura_v2'}"
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO telhados (area_ref, idx, material, area_m2, centro_lat, centro_lng, "
                "largura_m, comprimento_m, tipo, origem_tipo, just, altura_m, andares, janelas) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (ref, idx, r["material"], float(r["area_m2"]), float(r["lat"]), float(r["lng"]),
                 float(r["larg"]), float(r["comp"]), tipo, origem, just,
                 float(r.get("altura_m", 0.0)), int(r.get("andares", 1)), int(r.get("janelas", 0))))
        cards.append({"idx": idx, "area": r["area_m2"], "larg": r["larg"], "comp": r["comp"],
                      "material": r["material"], "tipo": tipo, "just": just, "raw": raw,
                      "altura": r.get("altura_m", 0.0), "andares": r.get("andares", 1)})
        if usar_ia and idx % 20 == 0:
            print(f"    IA {idx}/{len(roofs)}", flush=True)
    conn.close()
    out = galeria(ref, cards, usar_ia, fonte)
    n = {t: sum(1 for c in cards if c["tipo"] == t) for t in TIPOS}
    area_tot = sum(c["area"] for c in cards)
    print("\n  === RESUMO ===")
    print(f"  {len(cards)} edificações | 🏠 {n['casa_terrea']} casas térreas · 🏢 {n['predio']} prédios · "
          f"🏭 {n['galpao']} galpões/empresas · ⬜ {n['terreno_vazio']} terrenos vazios")
    print(f"  Área construída: {area_tot:,.0f} m²")
    print(f"  Galeria: {out}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", help="minlat,minlng,maxlat,maxlng (comece por 1 quadra)")
    p.add_argument("--poligono", help="recorte por polígono: 'lat,lng;lat,lng;...' (deriva a bbox)")
    p.add_argument("--rotulo", default="area", help="nome da área (chave em telhados)")
    p.add_argument("--fonte", default="overture", choices=["overture", "cor"],
                   help="detecção: overture (footprints de IA, padrão) | cor (nosso CV, fallback)")
    p.add_argument("--zoom", type=int, default=Z, help=f"zoom Google (padrão {Z}; z21 nítido, z22 upscale)")
    p.add_argument("--release", help="release do Overture (padrão = o de telhados_area.py)")
    p.add_argument("--ia", action="store_true", help="refina o tipo com a IA + exemplos de referência")
    p.add_argument("--exemplo", help="curar exemplo: tipo=ref:idx (ex: casa_terrea=quadra1:7)")
    a = p.parse_args()
    Z = a.zoom   # rebind global; as funções leem Z em tempo de chamada
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
