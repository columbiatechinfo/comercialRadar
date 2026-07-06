"""
spatial_clustering.py — Regionalização espacial dos POIs

Recebe a lista de POIs (com lat/lng) e devolve uma lista de LOTES geograficamente
coesos. Cada lote tem entre BATCH_MIN e BATCH_MAX POIs e tende a cobrir uma
região contígua (um "bairro"), para o worker "viajar" como um humano.

Estratégia:
  1. DBSCAN com métrica haversine (eps ≈ 300m) agrupa POIs vizinhos em regiões.
  2. Ruído (label -1) e pontos soltos são ordenados por proximidade e agrupados.
  3. Cada região é fatiada em lotes de tamanho aleatório [BATCH_MIN, BATCH_MAX].
     Regiões grandes viram vários lotes; sobras pequenas são mescladas.

Sem dependência de estado global. Determinístico dado o mesmo `seed`.
"""

import math
import random
from typing import List, Dict

import numpy as np
from sklearn.cluster import DBSCAN

import config

TERRA_RAIO_M = 6371000.0


def _ordenar_por_proximidade(pontos: List[Dict]) -> List[Dict]:
    """Ordena via vizinho-mais-próximo (nearest-neighbor) para manter contiguidade."""
    if len(pontos) <= 2:
        return list(pontos)

    restantes = list(pontos)
    ordenado = [restantes.pop(0)]
    while restantes:
        ult = ordenado[-1]
        idx_mais_perto = min(
            range(len(restantes)),
            key=lambda i: (restantes[i]["lat"] - ult["lat"]) ** 2
            + (restantes[i]["lng"] - ult["lng"]) ** 2,
        )
        ordenado.append(restantes.pop(idx_mais_perto))
    return ordenado


def _fatiar_em_lotes(pontos: List[Dict], rng: random.Random) -> List[List[Dict]]:
    """Quebra uma sequência ordenada em lotes de tamanho aleatório [MIN, MAX]."""
    lotes = []
    i = 0
    n = len(pontos)
    while i < n:
        restante = n - i
        # Se o que resta cabe num lote só, fecha tudo aqui (pode passar de
        # BATCH_MAX por poucos itens, mas evita lotes minúsculos de sobra).
        if restante <= config.BATCH_MAX:
            lotes.append(pontos[i:])
            break
        tam = rng.randint(config.BATCH_MIN, config.BATCH_MAX)
        # Garante que a sobra nunca fique abaixo de BATCH_MIN.
        if restante - tam < config.BATCH_MIN:
            tam = restante - config.BATCH_MIN
        lotes.append(pontos[i:i + tam])
        i += tam
    return lotes


def clusterizar_pois(
    pois: List[Dict],
    eps_m: float = None,
    seed: int = 42,
) -> List[List[Dict]]:
    """
    Agrupa POIs em lotes geograficamente coesos.

    Args:
        pois: lista de dicts com 'lat' e 'lng'.
        eps_m: raio de vizinhança DBSCAN em metros (default config.DBSCAN_EPS_M).
        seed: semente para tamanhos de lote reprodutíveis.

    Returns:
        Lista de lotes; cada lote é uma lista de POIs (dicts originais, intactos).
    """
    eps_m = eps_m or config.DBSCAN_EPS_M
    rng = random.Random(seed)

    validos = [p for p in pois if p.get("lat") is not None and p.get("lng") is not None]
    if not validos:
        return []

    if len(validos) <= config.BATCH_MAX:
        return [_ordenar_por_proximidade(validos)]

    coords_rad = np.radians(np.array([[p["lat"], p["lng"]] for p in validos]))
    eps_rad = eps_m / TERRA_RAIO_M

    db = DBSCAN(
        eps=eps_rad,
        min_samples=config.DBSCAN_MIN_SAMPLES,
        metric="haversine",
        algorithm="ball_tree",
    ).fit(coords_rad)

    labels = db.labels_

    # Agrupa por label
    regioes: Dict[int, List[Dict]] = {}
    ruido: List[Dict] = []
    for poi, lbl in zip(validos, labels):
        if lbl == -1:
            ruido.append(poi)
        else:
            regioes.setdefault(lbl, []).append(poi)

    lotes: List[List[Dict]] = []

    # Cada região clusterizada → ordena e fatia
    for _lbl, grupo in sorted(regioes.items()):
        grupo_ord = _ordenar_por_proximidade(grupo)
        lotes.extend(_fatiar_em_lotes(grupo_ord, rng))

    # Ruído: ordena tudo por proximidade e fatia junto
    if ruido:
        ruido_ord = _ordenar_por_proximidade(ruido)
        lotes.extend(_fatiar_em_lotes(ruido_ord, rng))

    # Embaralha a ORDEM dos lotes para os workers não atacarem a mesma região
    # simultaneamente (cada lote internamente continua contíguo).
    rng.shuffle(lotes)
    return lotes


def resumo_clusters(lotes: List[List[Dict]]) -> str:
    if not lotes:
        return "0 lotes"
    tamanhos = [len(l) for l in lotes]
    total = sum(tamanhos)
    return (
        f"{len(lotes)} lotes | {total} POIs | "
        f"lote min={min(tamanhos)} max={max(tamanhos)} "
        f"média={total / len(lotes):.1f}"
    )


if __name__ == "__main__":
    # Teste rápido com o ocr_resultado de uma sessão
    import sys
    import json
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Uso: py spatial_clustering.py capturas/<sessao>/crops/ocr_resultado.json")
        sys.exit(1)

    dados = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    uteis = [r for r in dados if len(r.get("ocr_texto", "").strip()) >= config.MIN_OCR_LEN]
    lotes = clusterizar_pois(uteis)
    print(resumo_clusters(lotes))
