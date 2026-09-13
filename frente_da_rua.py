# -*- coding: utf-8 -*-
"""frente_da_rua.py — onde a coordenada do POI cai nas quatro visadas de rua.

A REGRA (dono do produto, 13/09/2026): "a mira deve ser colocada na coordenada
do POI, simples assim". O rumo e o da camera ate a coordenada do POI; a visada
em que esse rumo aparece e a frente, e a mira vai no pixel dele.

A PROJECAO FOI MEDIDA, e nao suposta (13/09/2026): o `fov` que se pede ao Maps e
o campo VERTICAL do viewport 1280x900, limitado a ~90 graus. fov 80 -> foco de
532 px; fov 100 -> 447 px. Girar 20 graus e medir quanto a imagem anda fechou em
19,86 graus com 247 de 247 pares de pontos. O corte da interface e assimetrico
(15% a esquerda, 12% a direita): o rumo da camera cai em x = 0,479 da imagem, e
nao no meio — a mira antiga, desenhada no meio, ficava 2% a direita.
"""
from __future__ import annotations

import math

VIEW_W, VIEW_H = 1280, 900
FOV_VERTICAL_MAX = 90.4
CORTE = {"esq": 0.15, "dir": 0.12, "topo": 0.20, "baixo": 0.17}
Y_MIRA = 0.52


def rumo(la1, lo1, la2, lo2):
    my, mx = 111320.0, 111320.0 * math.cos(math.radians(la1))
    return math.degrees(math.atan2((lo2 - lo1) * mx, (la2 - la1) * my)) % 360


def dif(a, b):
    return (a - b + 180) % 360 - 180


def x_na_visada(rumo_alvo, heading, fov):
    """Posicao horizontal do rumo na imagem CORTADA (0 = borda esquerda, 1 = direita).
    Fora de 0..1 o rumo nao aparece nesta imagem; None se estiver para tras."""
    delta = dif(rumo_alvo, heading)
    if abs(delta) >= 85:
        return None
    foco = (VIEW_H / 2) / math.tan(math.radians(min(float(fov), FOV_VERTICAL_MAX)) / 2)
    xv = VIEW_W / 2 + foco * math.tan(math.radians(delta))
    return (xv / VIEW_W - CORTE["esq"]) / (1 - CORTE["esq"] - CORTE["dir"])


def escolher(visadas, rumo_alvo):
    """visadas: [(tipo, heading, fov)]. Devolve (tipo, x, na_borda).

    O CORTE DA INTERFACE ABRE FRESTAS de 2 a 3 graus entre visadas vizinhas. O
    rumo que cai numa delas vai para a imagem mais proxima, com a mira na borda."""
    dentro, fora = None, None
    for tipo, h, f in visadas:
        x = x_na_visada(rumo_alvo, h, f)
        if x is None:
            continue
        if 0.03 <= x <= 0.97:
            if dentro is None or abs(x - 0.5) < abs(dentro[1] - 0.5):
                dentro = (tipo, x, False)
        else:
            excesso = -x if x < 0.03 else x - 0.97
            if fora is None or excesso < fora[3]:
                fora = (tipo, min(0.97, max(0.03, x)), True, excesso)
    if dentro:
        return dentro
    return fora[:3] if fora else None


def _desenho_mira(arr, cx, cy, r, escala, cores):
    import cv2
    for cor, esp in cores:
        esp = max(1, int(round(esp * escala)))
        for a0 in (225, 45, 135, 315):
            cv2.ellipse(arr, (cx, cy), (r, r), 0, a0, a0 + 40, cor, esp)
        braco = int(14 * escala)
        cv2.line(arr, (cx, cy - braco), (cx, cy + braco), cor, max(1, esp - 1))
        cv2.line(arr, (cx - braco, cy), (cx + braco, cy), cor, max(1, esp - 1))


def apagar_mira_antiga(arr):
    """A mira que `_marcar_centro` desenhava ate 10/09/2026: forma e posicao conhecidas.
    Retoca a mascara exata (dilatada 2 px, por causa da compressao) com os vizinhos."""
    import cv2
    import numpy as np
    h, w = arr.shape[:2]
    masc = np.zeros((h, w), np.uint8)
    _desenho_mira(masc, w // 2, int(h * 0.52), int(min(w, h) * 0.13), w / 934.0, [((255,), 7)])
    masc = cv2.dilate(masc, np.ones((5, 5), np.uint8))
    return cv2.inpaint(arr, masc, 4, cv2.INPAINT_TELEA)


def desenhar_mira(arr, x_rel):
    h, w = arr.shape[:2]
    _desenho_mira(arr, int(x_rel * w), int(h * Y_MIRA), int(min(w, h) * 0.13), w / 934.0,
                  [((0, 0, 0), 7), ((0, 255, 90), 3)])
    return arr
