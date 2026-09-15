# -*- coding: utf-8 -*-
"""A seta semitransparente e o JPEG da foto de rua nova (14/09/2026)."""
import math

import cv2
import numpy as np

#: corte mais alto que o da captura antiga: some o cartao do Maps no canto (so ceu sai junto)
CORTE_TOPO_EXTRA = 0.06
#: a altura da ponta da seta, em fracao da imagem: `fachada_da_seta.escolher` procura a fachada nela
PONTA_REL = 0.52


def seta(arr, x_rel, distancia):
    h, w = arr.shape[:2]
    x = int(min(max(x_rel, 0.02), 0.98) * w)
    # SETA MAIOR E COM CONTORNO (dono do produto, 15/09/2026): semitransparente, a ponta no alto dos
    # telhados deixava duvida de qual imovel ela marca. A ponta desce ate o meio da foto e a cabeca
    # ganha contorno escuro; a haste continua fina e translucida para nao esconder placa.
    topo, ponta = int(h * 0.03), int(h * PONTA_REL)
    haste, cab_l, cab_a = max(12, w // 80), max(48, w // 18), max(60, h // 8)
    cabeca = np.array([[x - cab_l, ponta - cab_a], [x + cab_l, ponta - cab_a], [x, ponta]], np.int32)
    camada = arr.copy()
    cv2.rectangle(camada, (x - haste // 2, topo), (x + haste // 2, ponta - cab_a), (40, 220, 60), -1)
    cv2.fillPoly(camada, [cabeca], (40, 220, 60))
    arr = cv2.addWeighted(camada, 0.6, arr, 0.4, 0)
    cv2.polylines(arr, [cabeca], True, (10, 60, 20), max(2, w // 600), cv2.LINE_AA)
    rot = "%.0f m" % distancia
    esc = max(0.9, w / 1200)
    (tw, th), _ = cv2.getTextSize(rot, cv2.FONT_HERSHEY_SIMPLEX, esc, 2)
    tx = x + cab_l // 2 + 10 if x + cab_l // 2 + 16 + tw < w else x - cab_l // 2 - 16 - tw
    ty = topo + th + 10
    cv2.rectangle(arr, (tx - 6, ty - th - 8), (tx + tw + 6, ty + 8), (20, 20, 20), -1)
    cv2.putText(arr, rot, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, esc, (255, 255, 255), 2, cv2.LINE_AA)
    return arr


def _xy(lat0, lng0, lat, lng):
    return (lng - lng0) * 111320 * math.cos(math.radians(lat0)), (lat - lat0) * 111320


def planta(arr, cam, heading, fov, alvo, pe=None, lado_m=40.0):
    """A PLANTA VISTA DE CIMA no canto inferior direito (dono do produto, 15/09/2026): a rua (faixa cinza, pelo pe
    da perpendicular), a camera (ponto branco) com o cone do que a foto mostra (verde claro) e o alvo (ponto verde,
    a mesma cor da seta). Norte para cima, sem texto — para a IA e para quem revisa entenderem de onde a foto
    foi tirada. cam, alvo, pe: (lat, lng)."""
    h, w = arr.shape[:2]
    lado = int(min(w, h) * 0.34)
    x0, y0 = w - lado - 12, h - lado - 12
    lat0, lng0 = (cam[0] + alvo[0]) / 2, (cam[1] + alvo[1]) / 2
    esc = lado / lado_m

    def p(lat, lng):
        x, y = _xy(lat0, lng0, lat, lng)
        return int(lado / 2 + x * esc), int(lado / 2 - y * esc)
    tela = np.full((lado, lado, 3), 32, np.uint8)
    ax, ay = p(*alvo)
    if pe:
        fx, fy = p(*pe)
        ang = math.atan2(ay - fy, ax - fx) + math.pi / 2
        dx, dy = int(math.cos(ang) * lado * 2), int(math.sin(ang) * lado * 2)
        cv2.line(tela, (fx - dx, fy - dy), (fx + dx, fy + dy), (120, 120, 120), max(8, int(7 * esc)))
    cx, cy = p(*cam)
    alcance = int(lado * 0.9)
    cone = [(cx, cy)] + [(int(cx + math.sin(math.radians(heading + g)) * alcance),
                          int(cy - math.cos(math.radians(heading + g)) * alcance))
                         for g in range(-int(fov // 2), int(fov // 2) + 1, 5)]
    camada = tela.copy()
    cv2.fillPoly(camada, [np.array(cone, np.int32)], (90, 190, 90))
    tela = cv2.addWeighted(camada, 0.45, tela, 0.55, 0)
    r = max(6, lado // 28)
    cv2.circle(tela, (cx, cy), r, (255, 255, 255), -1, cv2.LINE_AA)
    cv2.circle(tela, (ax, ay), r + 3, (40, 220, 60), -1, cv2.LINE_AA)
    cv2.circle(tela, (ax, ay), r + 3, (10, 60, 20), 2, cv2.LINE_AA)
    cv2.rectangle(tela, (0, 0), (lado - 1, lado - 1), (210, 210, 210), 1)
    arr = arr.copy()
    arr[y0:y0 + lado, x0:x0 + lado] = cv2.addWeighted(tela, 0.88, arr[y0:y0 + lado, x0:x0 + lado], 0.12, 0)
    return arr


def jpeg(arr, largura, q=85):
    h, w = arr.shape[:2]
    if w > largura:
        arr = cv2.resize(arr, (largura, int(h * largura / w)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes(), arr.shape[1], arr.shape[0]


