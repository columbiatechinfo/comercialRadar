# -*- coding: utf-8 -*-
"""
geo.py — projeção MÉTRICA com SRID correto, diagnóstico único e coordenada sanitizada
===================================================================================
Princípios de segurança:
  * coordenada inválida vira NaN ANTES de qualquer decisão espacial;
  * SRID informado precisa ser projetado e métrico;
  * validação e projeção usam o MESMO diagnóstico geográfico;
  * aprendizado não cruza partições UTM (zona/hemisfério) silenciosamente.

SIRGAS 2000 / UTM:
    Norte   zonas 11N..22N  ->  31965..31976   (EPSG = 31954 + zona)
    Sul     zonas 17S..25S  ->  31977..31985   (EPSG = 31960 + zona)
Fora dessas faixas, autodetecção cai para WGS84/UTM 326xx/327xx e declara a
família usada; nunca inventa EPSG.
"""
from __future__ import annotations
import math
from collections import Counter

import numpy as np

try:
    from pyproj import Transformer
    _PYPROJ = True
except Exception:                                    # pragma: no cover
    _PYPROJ = False

_R = 6378137.0
SIRGAS_N = range(11, 23)
SIRGAS_S = range(17, 26)
WHITELIST_BR = set(range(31965, 31986))


def validar_srid(srid: int):
    """(ok, motivo, info). `srid_metrico` nunca pode representar grau."""
    srid = int(srid)
    if srid in WHITELIST_BR:
        z, hem = particao_do_srid(srid)
        return True, "", {"familia": "SIRGAS2000", "unidade": "metre",
                          "preferencial_br": True, "zona": z, "hemisferio": hem}
    if _PYPROJ:
        try:
            from pyproj import CRS
            crs = CRS.from_epsg(srid)
        except Exception as e:
            return False, f"EPSG:{srid} não resolve ({e.__class__.__name__})", {}
        if not crs.is_projected:
            return False, (f"EPSG:{srid} ({crs.name}) é geográfico — coordenada em grau. "
                           f"srid_metrico exige CRS PROJETADO em metros"), {}
        un = {getattr(ax, "unit_name", "") for ax in crs.axis_info}
        if not un & {"metre", "meter", "metres", "meters"}:
            return False, f"EPSG:{srid} ({crs.name}) não está em metros: {sorted(un)}", {}
        z, hem = particao_do_srid(srid)
        return True, "", {"familia": crs.name, "unidade": "metre",
                          "preferencial_br": False, "zona": z, "hemisferio": hem}
    if 32601 <= srid <= 32660 or 32701 <= srid <= 32760:
        z, hem = particao_do_srid(srid)
        return True, "", {"familia": "WGS84/UTM", "unidade": "metre",
                          "preferencial_br": False, "zona": z, "hemisferio": hem}
    return False, (f"EPSG:{srid} não verificável sem pyproj — use SIRGAS 2000/UTM "
                   f"(31965–31985), WGS84/UTM, ou instale pyproj"), {}


def zona_utm(lon: float) -> int:
    """Zona UTM 1..60; longitude +180 cai corretamente na zona 60."""
    z = int((float(lon) + 180.0) // 6.0) + 1
    return max(1, min(60, z))


def hemisferio_utm(lat: float) -> str:
    return "N" if float(lat) >= 0 else "S"


def particao_do_srid(srid: int):
    """(zona, hemisfério) se o EPSG é UTM conhecido; senão (None, None)."""
    s = int(srid)
    if 31965 <= s <= 31976:
        return s - 31954, "N"
    if 31977 <= s <= 31985:
        return s - 31960, "S"
    if 32601 <= s <= 32660:
        return s - 32600, "N"
    if 32701 <= s <= 32760:
        return s - 32700, "S"
    return None, None


def autodetect_srid(lon_mediana: float, lat_mediana: float):
    """Devolve (epsg, familia). familia = SIRGAS2000 | WGS84."""
    z = zona_utm(lon_mediana)
    if lat_mediana >= 0:
        if z in SIRGAS_N:
            return 31954 + z, "SIRGAS2000"
        return 32600 + z, "WGS84"
    if z in SIRGAS_S:
        return 31960 + z, "SIRGAS2000"
    return 32700 + z, "WGS84"


def sanitizar(lon, lat):
    """Coordenada implausível -> NaN. Devolve (lon, lat, n_invalidas)."""
    lon = np.asarray(lon, dtype="float64").copy()
    lat = np.asarray(lat, dtype="float64").copy()
    ruim = (~np.isfinite(lon) | ~np.isfinite(lat)
            | (np.abs(lat) > 90) | (np.abs(lon) > 180)
            | ((lat == 0) & (lon == 0)))
    lon[ruim] = np.nan
    lat[ruim] = np.nan
    return lon, lat, int(ruim.sum())


def diagnosticar(lon, lat):
    """Diagnóstico ÚNICO consumido por validação e projeção.

    Retorna medianas sobre TODOS os registros válidos e as partições UTM
    (zona + hemisfério) realmente presentes. Assim não existe mais a antiga
    discrepância "mediana das medianas por fonte" vs mediana da base inteira.
    """
    lon, lat, n_ruins = sanitizar(lon, lat)
    validos = np.isfinite(lon) & np.isfinite(lat)
    if not validos.any():
        return {"coord_invalidas": n_ruins, "coord_validas": 0,
                "lon_mediana": None, "lat_mediana": None,
                "particoes_utm": {}, "multizona": False}, lon, lat
    lv, av = lon[validos], lat[validos]
    parts = Counter(f"{zona_utm(lo)}{hemisferio_utm(la)}" for lo, la in zip(lv, av))
    return {"coord_invalidas": n_ruins, "coord_validas": int(validos.sum()),
            "lon_mediana": float(np.median(lv)), "lat_mediana": float(np.median(av)),
            "particoes_utm": dict(sorted(parts.items())), "multizona": len(parts) > 1}, lon, lat


def exigir_particao_unica(diag: dict) -> None:
    if diag.get("multizona"):
        partes = ", ".join(f"{k}={v}" for k, v in diag.get("particoes_utm", {}).items())
        raise ValueError("base de aprendizado cruza múltiplas partições UTM "
                         f"({partes}); particione a execução por zona/hemisfério")


def projetar(lon, lat, srid=None, permitir_multizona: bool = False):
    """(lon, lat) graus -> (x, y) metros. Devolve (x, y, srid, modo, diag)."""
    diag, lon, lat = diagnosticar(lon, lat)
    if not diag["coord_validas"]:
        raise ValueError("nenhuma coordenada válida após sanitização")
    if not permitir_multizona:
        exigir_particao_unica(diag)

    lon_m = diag["lon_mediana"]
    lat_m = diag["lat_mediana"]
    if srid is None:
        srid, familia = autodetect_srid(lon_m, lat_m)
    else:
        ok, motivo, info = validar_srid(srid)
        if not ok:
            raise ValueError(motivo)
        familia = info.get("familia", "informado")
        z_srid, h_srid = info.get("zona"), info.get("hemisferio")
        if z_srid is not None:
            part_dados = next(iter(diag["particoes_utm"])) if len(diag["particoes_utm"]) == 1 else None
            esperado = f"{z_srid}{h_srid}"
            if part_dados and part_dados != esperado:
                auto, _ = autodetect_srid(lon_m, lat_m)
                raise ValueError(f"srid_metrico EPSG:{int(srid)} representa zona/hemisfério {esperado}, "
                                 f"mas os dados estão em {part_dados} (esperado EPSG:{auto})")
    diag.update({"srid": int(srid), "familia_srid": familia,
                 "zona_utm": zona_utm(lon_m), "hemisferio_utm": hemisferio_utm(lat_m)})

    if _PYPROJ:
        tr = Transformer.from_crs(4326, srid, always_xy=True)
        x, y = tr.transform(lon, lat)
        x, y = np.asarray(x, dtype="float64"), np.asarray(y, dtype="float64")
        modo = "pyproj"
    else:                                            # plano tangente local
        k = math.cos(math.radians(lat_m))
        x = math.radians(1.0) * _R * k * np.nan_to_num(lon, nan=np.nan)
        y = math.radians(1.0) * _R * lat
        modo = "enu_local"

    validos = np.isfinite(lon) & np.isfinite(lat)
    nao_finito = ~np.isfinite(x) | ~np.isfinite(y)
    x = np.where(nao_finito, np.nan, x)
    y = np.where(nao_finito, np.nan, y)
    diag["projecao_nao_finita"] = int(nao_finito.sum() - int((~validos).sum()))
    return x, y, int(srid), modo, diag
