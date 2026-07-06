"""
area_utils.py — Polígono de área válida (limite geográfico do trabalho)

O frontend desenha um polígono no mapa; ele é salvo como JSON e passado aos
coletores/ingestores. Todo POI cuja coordenada cai FORA do polígono é marcado
com status 'fora_da_area' e match_valido=False (não entra no banco).

Formato do arquivo de área:
  {"nome": "parnaiba", "polygon": [[lat, lng], [lat, lng], ...]}
  (também aceita uma lista pura [[lat, lng], ...])

Sem dependências externas (ray casting puro).
"""

import json
from pathlib import Path


def carregar_area(path) -> list | None:
    """Lê o arquivo de área e retorna a lista de vértices [[lat, lng], ...]."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    poly = data.get("polygon") if isinstance(data, dict) else data
    if not poly or len(poly) < 3:
        return None
    return [[float(a), float(b)] for a, b in poly]


def ponto_no_poligono(lat, lng, poligono) -> bool:
    """Ray casting: True se (lat, lng) está dentro do polígono [[lat, lng], ...]."""
    if lat is None or lng is None or not poligono:
        return False
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i][0], poligono[i][1]
        yj, xj = poligono[j][0], poligono[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            dentro = not dentro
        j = i
    return dentro


def bbox(poligono):
    lats = [p[0] for p in poligono]
    lngs = [p[1] for p in poligono]
    return min(lats), max(lats), min(lngs), max(lngs)


def coord_do_registro(reg):
    """Coordenada efetiva de um registro do pipeline (maps_* > *_origem)."""
    la = reg.get("maps_lat")
    lo = reg.get("maps_lng")
    if la is None or lo is None:
        la = reg.get("lat_origem")
        lo = reg.get("lng_origem")
    try:
        return (float(la), float(lo)) if la is not None and lo is not None else (None, None)
    except (TypeError, ValueError):
        return (None, None)


def gate_registro(reg: dict, poligono) -> bool:
    """
    Aplica o portão de área a um registro do pipeline.
    Retorna True se o registro segue válido; False se foi marcado fora_da_area.
    Registros sem coordenada nenhuma passam (não há como julgar).
    """
    if not poligono:
        return True
    la, lo = coord_do_registro(reg)
    if la is None:
        return True
    if ponto_no_poligono(la, lo, poligono):
        return True
    if reg.get("match_valido"):
        reg["status_antes_area"] = reg.get("status")
        reg["status"] = "fora_da_area"
        reg["match_valido"] = False
    return False
