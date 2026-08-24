# -*- coding: utf-8 -*-
"""Modulos vendorizados da skill `extracao-dados-poi-global` (copia verbatim).

Vendorizados para a skill ser autocontida: a v1.0.0 importava por caminho absoluto
de sessao (`/sessions/.../extracao-dados-poi-global/scripts`) e quebrava com
ModuleNotFoundError fora da sessao de origem.

Trade-off aceito: duplicacao do lexico de categorias -> risco de drift em relacao
a skill-mae. `VENDOR_ORIGEM` e `VENDOR_SHA` registram a procedencia; a validacao
compara o sha com a skill-mae quando ela estiver instalada.
"""
import hashlib
import os
import sys

_D = os.path.dirname(os.path.abspath(__file__))
if _D not in sys.path:
    sys.path.insert(0, _D)

VENDOR_ORIGEM = "extracao-dados-poi-global/scripts"
# `dedup_v3` NAO vem da skill-mae: nasceu aqui na v3.0.0. Entra no sha por ser o
# modulo que decide o que sobrevive a fusao — drift nele muda a entrega.
MODULOS = ("extrair_pois", "tratar_pois", "dedup_v3", "osm_pbf", "limites", "categorias_pt")


def sha_vendor():
    """SHA-256 por modulo vendorizado — entra no manifesto para rastrear drift."""
    out = {}
    for m in MODULOS:
        p = os.path.join(_D, m + ".py")
        with open(p, "rb") as fh:
            out[m] = hashlib.sha256(fh.read()).hexdigest()[:16]
    return out


import extrair_pois  # noqa: E402,F401
import tratar_pois   # noqa: E402,F401
import dedup_v3      # noqa: E402,F401
import osm_pbf       # noqa: E402,F401
import limites       # noqa: E402,F401
import categorias_pt  # noqa: E402,F401
