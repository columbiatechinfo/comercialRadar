# -*- coding: utf-8 -*-
"""extracao-poi-estadual — extracao multifonte de POIs em escala estadual.

Entrada unica: `poi_estadual.py`. Nenhum modulo deste pacote contem UF, municipio,
caminho de sessao ou nome de arquivo fixo — tudo vem de `config.Config`.
"""
import os

from .config import Config, ConfigInvalida  # noqa: F401
from .manifest import EtapaBloqueada, Manifesto  # noqa: F401


def versao():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "desconhecida"


__version__ = versao()
