# -*- coding: utf-8 -*-
"""A seta semitransparente e o JPEG da foto de rua nova (14/09/2026)."""
import cv2
import numpy as np

#: corte mais alto que o da captura antiga: some o cartao do Maps no canto (so ceu sai junto)
CORTE_TOPO_EXTRA = 0.06


def seta(arr, x_rel, distancia):
    h, w = arr.shape[:2]
    x = int(min(max(x_rel, 0.02), 0.98) * w)
    topo, ponta = int(h * 0.03), int(h * 0.44)
    haste, cab_l, cab_a = max(10, w // 90), max(34, w // 26), max(40, h // 12)
    camada = arr.copy()
    cv2.rectangle(camada, (x - haste // 2, topo), (x + haste // 2, ponta - cab_a), (40, 220, 60), -1)
    cv2.fillPoly(camada, [np.array([[x - cab_l, ponta - cab_a], [x + cab_l, ponta - cab_a], [x, ponta]], np.int32)],
                 (40, 220, 60))
    arr = cv2.addWeighted(camada, 0.55, arr, 0.45, 0)
    rot = "%.0f m" % distancia
    esc = max(0.9, w / 1200)
    (tw, th), _ = cv2.getTextSize(rot, cv2.FONT_HERSHEY_SIMPLEX, esc, 2)
    tx = x + cab_l // 2 + 10 if x + cab_l // 2 + 16 + tw < w else x - cab_l // 2 - 16 - tw
    ty = topo + th + 10
    cv2.rectangle(arr, (tx - 6, ty - th - 8), (tx + tw + 6, ty + 8), (20, 20, 20), -1)
    cv2.putText(arr, rot, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, esc, (255, 255, 255), 2, cv2.LINE_AA)
    return arr


def jpeg(arr, largura, q=85):
    h, w = arr.shape[:2]
    if w > largura:
        arr = cv2.resize(arr, (largura, int(h * largura / w)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes(), arr.shape[1], arr.shape[0]


