# -*- coding: utf-8 -*-
"""visada_perpendicular.py — a foto de rua unica da ligacao (dono do produto, 14/09/2026).

A IA passa a receber UMA foto de rua, de frente para o imovel, em boa resolucao,
com uma seta verde semitransparente do topo ate a coordenada do registro e a
distancia da camera. Como escolher o panorama e para onde olhar:

  METODO 1 — PERPENDICULAR DA VIA (preferido)
    O OSRM `nearest` da o ponto da via mais perto da coordenada: o pe da
    perpendicular. Das vias que ele devolve, fica a que tem o nome da rua do
    cadastro da ligacao (terreno de esquina). O panorama e o mais perto desse pe
    (ate 12 m), e a camera olha NA DIRECAO DA PERPENDICULAR — paralela a fachada —
    com campo de 100 graus. A coordenada pode ficar fora do centro; a seta vai
    ate ela.

  METODO 2 — O PANORAMA MAIS DE FRENTE (fallback)
    Quando a coordenada esta em cima da via (menos de 2 m: nao ha lado), o OSRM
    nao acha via, ou nao ha panorama perto do pe: entre o panorama mais perto e
    os vizinhos (ate 30 m, data igual ou mais nova), o de rumo mais perto da
    perpendicular da rua (estimada pelos vizinhos), mirando a coordenada.

Sem banco aqui: devolve a escolha; quem captura e grava e o chamador.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.request

import streetview_geo as sv

OSRM = "http://127.0.0.1:7300"
FOV = 100
PE_ATE_PANO_MAX_M = 12
COORD_NA_VIA_M = 2
#: pontos da via, a partir do pe da perpendicular, em que se procura panorama (15/09/2026)
PASSOS_NA_VIA_M = (0, 6, 12)
#: distancia minima da camera ate a fachada, e o que a fachada fica alem da coordenada (15/09/2026)
FACHADA_MIN_M = 8.0
FACHADA_ALEM_M = 3.0


def mover(lat, lng, rumo, metros):
    dy = metros * math.cos(math.radians(rumo)) / 111320
    dx = metros * math.sin(math.radians(rumo)) / (111320 * math.cos(math.radians(lat)))
    return lat + dy, lng + dx


def dif(a, b):
    return abs((a - b + 180) % 360 - 180)


_FORA = {"rua", "r", "avenida", "av", "travessa", "tv", "estrada", "est", "alameda", "al", "rodovia", "rod",
         "praca", "pca", "largo", "beco", "de", "da", "do", "das", "dos", "e"}


def _tokens_via(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return {x for x in re.sub(r"[^a-z0-9 ]", " ", t).split() if x not in _FORA and len(x) > 1}


def mesma_via(a, b):
    ta, tb = _tokens_via(a), _tokens_via(b)
    return bool(ta and tb) and len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


def _nearest(lat, lng):
    try:
        url = "%s/nearest/v1/driving/%s,%s?number=5" % (OSRM, lng, lat)
        return json.loads(urllib.request.urlopen(url, timeout=10).read()).get("waypoints") or []
    except Exception:                                          # noqa: BLE001
        return []


def metodo_perpendicular(lat, lng, rua):
    wps = _nearest(lat, lng)
    via = next((w for w in wps if rua and mesma_via(w.get("name"), rua)), None) or (wps[0] if wps else None)
    if not via:
        return None, "o OSRM nao achou via"
    if (via.get("distance") or 0) < COORD_NA_VIA_M:
        return None, "coordenada em cima da via (%.1f m): sem lado" % (via.get("distance") or 0)
    pe_lng, pe_lat = via["location"]
    # O PANORAMA MAIS RECENTE PERTO DO PE (dono do produto, 15/09/2026).
    # Girar a camera para a perpendicular da via tirava o imovel do quadro: com a coordenada a 3 m do
    # panorama, o rumo do pe calculado pelo OSRM erra dezenas de graus (ligacao 319464: a placa KALIVAS
    # saiu da foto e a seta foi para a borda). O que deixa a foto de frente e o LUGAR da camera, nao o giro:
    # procura panorama no pe e a 6 e 12 m para cada lado da via, fica o de data mais nova ("sempre preferir
    # o mais recente") e, na mesma data, o mais perto do pe.
    perp = sv._bearing(pe_lat, pe_lng, lat, lng)
    cands = {}
    for passo in PASSOS_NA_VIA_M:
        for lado in ((90, -90) if passo else (0,)):
            la, lo = mover(pe_lat, pe_lng, (perp + lado) % 360, passo) if passo else (pe_lat, pe_lng)
            m = sv.metadados_pano(la, lo, raio=8 if passo else PE_ATE_PANO_MAX_M)
            if m and m["pano_id"] not in cands and sv._dist_m(m["lat"], m["lng"], pe_lat, pe_lng) <= PE_ATE_PANO_MAX_M:
                cands[m["pano_id"]] = m
    if not cands:
        return None, "sem panorama ate %d m do pe da perpendicular" % PE_ATE_PANO_MAX_M
    p = max(cands.values(), key=lambda m: (m.get("data") or "", -sv._dist_m(m["lat"], m["lng"], pe_lat, pe_lng)))
    # A MIRA SAI DA GEOMETRIA DA VIA, NAO DO RUMO DO PANORAMA ATE A COORDENADA. O GPS do panorama e a linha da
    # via do OSM erram metros para o lado: na 319464 o panorama ficou a 2,8 m da coordenada, que esta a 6,2 m
    # do eixo — mirar a coordenada deu a foto ao longo da rua. O quanto o panorama andou AO LONGO da via (a
    # projecao no eixo) e confiavel; o desvio lateral nao. A camera olha a perpendicular e gira so o que o
    # avanco ao longo da via pede ate a fachada: atan(avanco / distancia ate a fachada).
    eixo = (perp + 90) % 360
    pe_pano = sv._dist_m(pe_lat, pe_lng, p["lat"], p["lng"])
    avanco = pe_pano * math.cos(math.radians(sv._bearing(pe_lat, pe_lng, p["lat"], p["lng"]) - eixo)) if pe_pano > 0.5 else 0.0
    fachada = max(FACHADA_MIN_M, float(via.get("distance") or 0) + FACHADA_ALEM_M)
    alvo = (perp + math.degrees(math.atan2(-avanco, fachada))) % 360
    return {"metodo": "perpendicular", "pano": p, "heading": alvo, "panoramas_perto": len(cands),
            "avanco_na_via_m": round(avanco, 1),
            "rumo_alvo": alvo, "dist": math.hypot(avanco, float(via.get("distance") or 0)),
            "via": via.get("name") or "", "via_casou_rua": bool(rua and mesma_via(via.get("name"), rua)),
            "coord_ate_via_m": via.get("distance"), "pe_ate_pano_m": sv._dist_m(p["lat"], p["lng"], pe_lat, pe_lng)}, None


def metodo_mais_de_frente(lat, lng):
    m0 = sv.metadados_pano(lat, lng)
    if not m0:
        return None, "sem panorama"
    vizinhos = {}
    for rumo in range(0, 360, 45):
        for passo in (10, 18):
            la, lo = mover(m0["lat"], m0["lng"], rumo, passo)
            m = sv.metadados_pano(la, lo, raio=8)
            if m and m["pano_id"] != m0["pano_id"]:
                vizinhos[m["pano_id"]] = m
    eixo = None
    for dist, v in sorted(((sv._dist_m(m0["lat"], m0["lng"], v["lat"], v["lng"]), v) for v in vizinhos.values()),
                          key=lambda x: -x[0]):
        if 6 <= dist <= 25:
            eixo = sv._bearing(m0["lat"], m0["lng"], v["lat"], v["lng"])
            break
    data0 = m0.get("data") or ""
    cands = []
    for m in [m0] + list(vizinhos.values()):
        if (m.get("data") or "") < data0:
            continue
        d = sv._dist_m(m["lat"], m["lng"], lat, lng)
        if d > 30:
            continue
        rumo = sv._bearing(m["lat"], m["lng"], lat, lng)
        obliq = 0.0 if eixo is None else dif(rumo, min((eixo + 90) % 360, (eixo - 90) % 360, key=lambda n: dif(n, rumo)))
        cands.append((m, d, rumo, obliq))
    if not cands:
        # todos os panoramas perto sao mais velhos que o mais proximo ou passam de 30 m
        return None, "sem panorama de frente ate 30 m"
    frente =[c for c in cands if c[3] <= 20 and c[1] >= 5] or sorted(cands, key=lambda c: c[3])[:1]
    frente.sort(key=lambda c: (-min(c[1], 30), c[3]))
    m, d, rumo, obliq = frente[0]
    return {"metodo": "mais de frente", "pano": m, "heading": rumo, "rumo_alvo": rumo, "dist": d,
            "obliquo_graus": obliq}, None


def escolher(lat, lng, rua):
    """A visada da ligacao: metodo 1, e o 2 quando o 1 nao se aplica (com o porque)."""
    esc, porque = metodo_perpendicular(lat, lng, rua)
    if esc:
        return esc
    esc2, porque2 = metodo_mais_de_frente(lat, lng)
    if esc2:
        esc2["por_que_fallback"] = porque
        return esc2
    return {"erro": "%s; %s" % (porque, porque2)}
