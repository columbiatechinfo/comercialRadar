#!/usr/bin/env python3
"""Índice CNEFE e motor de match — v2.2.

Mudanças sobre a v2.1:
- F-15  o índice deixa de varrer `best_addr` inteiro por grupo (termo quadrático);
        usa groupby pré-materializado em dict.
- F-16  normalizações caras rodam sobre valores ÚNICOS (factorize), não linha a linha;
        o match resolve o logradouro uma vez por chave distinta, não por registro.
- F-09  o fuzzy deixa de usar token_set_ratio (que pontua 100 para subconjunto);
        passa a token_sort_ratio com penalidade por cardinalidade de tokens.
- F-01  NV_GEO_COORD ganha classe e teto de confiança; NV>=4 nunca é ALTA.
- F-02  DESVIO_METROS medido: piso por classe de NV somado em quadratura à
        incerteza de interpolação (metros por unidade de número, empírico da rua).
- F-03  centróide sintético usa NV=90, fora do domínio oficial do IBGE.
- F-05  espécies de estabelecimento = {3,4,5,6,8}; 7 (construção) sai; 2 não entra por nome.
- F-07  ingere COD_MUNICIPIO, NUM_FACE, DSC_MODIFICADOR, COD_INDICADOR_ESTAB_ENDERECO.
- F-10  CEP fora do índice tem motivo próprio (SEM_CEP_NO_INDICE).
- F-11  sufixo/modificador do número entra na chave de match.
- F-18  as listas de DSC saem do DataFrame para um dicionário lateral.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

from _normalizacao import (
    clean_text, normalize_cep, normalize_logradouro, normalize_nome,
    normalize_sufixo, parse_numero,
)

# --- domínios oficiais (Dicionario_CNEFE_Censo_2022.xls) --------------------
ESP_ESTABELECIMENTO = {3, 4, 5, 6, 8}   # agropecuário, ensino, saúde, outras finalidades, religioso
ESP_DOMICILIO = {1, 2}                  # particular, coletivo
ESP_CONSTRUCAO = {7}                    # edificação em construção ou reforma

NV_CLASSE = {
    1: "ENDERECO_ORIGINAL", 2: "ENDERECO_MODIFICADO", 3: "ENDERECO_ESTIMADO",
    4: "FACE_QUADRA", 5: "LOCALIDADE", 6: "SETOR_CENSITARIO",
    90: "SINTETICO_LOGRADOURO",
}
# Piso de incerteza DECLARADO por classe (ordem de grandeza, não medido em campo).
NV_FLOOR_M = {1: 8.0, 2: 15.0, 3: 40.0, 4: 60.0, 5: 250.0, 6: 800.0, 90: 60.0}
# 90 = centróide sintético de logradouro. Nunca é melhor que uma face de quadra,
# então o p95 medido recebe esse piso — rua com um único ponto tem p95=0, e
# declarar 0 m de incerteza para um centróide seria falso.
NV_SINTETICO = 90
NV_CONF_TETO = {1: "ALTA", 2: "ALTA", 3: "MEDIA", 4: "BAIXA", 5: "BAIXA", 6: "BAIXA", 90: "BAIXA"}
_CONF_ORD = {"ALTA": 3, "MEDIA": 2, "BAIXA": 1, "SEM_MATCH": 0}

BRASIL_BBOX = (-35.0, 6.5, -75.0, -32.0)
M_POR_NUM_DEFAULT = 6.0
M_POR_NUM_CLAMP = (0.5, 60.0)


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def haversine_vec(lat1, lon1, lat2, lon2) -> np.ndarray:
    r = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def valid_coord(lat, lon) -> bool:
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    if math.isnan(latf) or math.isnan(lonf):
        return False
    return -90 <= latf <= 90 and -180 <= lonf <= 180 and not (latf == 0 and lonf == 0)


def in_brazil_bbox(lat, lon) -> bool:
    if not valid_coord(lat, lon):
        return False
    a, b, c, d = BRASIL_BBOX
    return a <= float(lat) <= b and c <= float(lon) <= d


def _valid_coord_series(lat: pd.Series, lon: pd.Series) -> pd.Series:
    return (
        lat.notna() & lon.notna()
        & lat.between(-90, 90) & lon.between(-180, 180)
        & ~((lat == 0) & (lon == 0))
    )


def _in_bbox_series(lat: pd.Series, lon: pd.Series) -> pd.Series:
    a, b, c, d = BRASIL_BBOX
    return lat.between(a, b) & lon.between(c, d)


# ---------------------------------------------------------------------------
# Aplicação de função sobre valores únicos (F-16)
# ---------------------------------------------------------------------------
def map_unicos(serie: pd.Series, fn) -> list:
    """Aplica fn sobre os valores distintos e reexpande. Determinístico."""
    codes, uniques = pd.factorize(serie.astype("string").fillna(""), sort=False)
    tabela = [fn(u) for u in uniques]
    return [tabela[c] if c >= 0 else fn("") for c in codes]


# ---------------------------------------------------------------------------
# Preparação do CNEFE
# ---------------------------------------------------------------------------
def prepare_cnefe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    out = df.copy()
    n = len(out)

    for col, default in [
        ("COD_MUNICIPIO", ""), ("NUM_FACE", ""), ("DSC_MODIFICADOR", ""),
        ("COD_INDICADOR_ESTAB_ENDERECO", ""), ("COD_SETOR", ""),
    ]:
        if col not in out.columns:
            out[col] = default

    out["COD_ESPECIE"] = pd.to_numeric(out["COD_ESPECIE"], errors="coerce").astype("Int64")
    out["NV_GEO_COORD"] = pd.to_numeric(out["NV_GEO_COORD"], errors="coerce").astype("Int64")
    out["IND_ESTAB"] = pd.to_numeric(out["COD_INDICADOR_ESTAB_ENDERECO"], errors="coerce").astype("Int64")
    out["LATITUDE"] = pd.to_numeric(out["LATITUDE"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["LONGITUDE"] = pd.to_numeric(out["LONGITUDE"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["COORD_VALIDA"] = _valid_coord_series(out["LATITUDE"], out["LONGITUDE"])
    out["COORD_BRASIL"] = out["COORD_VALIDA"] & _in_bbox_series(out["LATITUDE"], out["LONGITUDE"])

    ceps = map_unicos(out["CEP"], normalize_cep)
    out["CEP_NORM"] = [c[0] for c in ceps]
    out["CEP_STATUS"] = [c[1] for c in ceps]

    chave_logr = (
        out["NOM_TIPO_SEGLOGR"].fillna("").astype(str) + "\x1f"
        + out["NOM_TITULO_SEGLOGR"].fillna("").astype(str) + "\x1f"
        + out["NOM_SEGLOGR"].fillna("").astype(str)
    )

    def _logr(k: str):
        t, tit, nome = (k.split("\x1f") + ["", "", ""])[:3]
        combinado = " ".join(x for x in [clean_text(tit), clean_text(nome)] if x)
        return normalize_logradouro(combinado, t)

    logs = map_unicos(chave_logr, _logr)
    out["LOGR_TIPO"] = [x.tipo for x in logs]
    out["LOGR_BASE"] = [x.base for x in logs]
    out["LOGR_FULL"] = [x.full for x in logs]
    out["LOGR_NUCLEO"] = [x.nucleo for x in logs]
    out["LOGR_STATUS"] = [x.status for x in logs]

    nums = map_unicos(out["NUM_ENDERECO"], parse_numero)
    out["NUM_INT"] = [x[0] for x in nums]
    out["NUM_STATUS"] = [x[1] for x in nums]
    sufixo_num = [x[2] for x in nums]
    sufixo_mod = map_unicos(out["DSC_MODIFICADOR"], normalize_sufixo)
    out["NUM_SUFIXO"] = [a or b for a, b in zip(sufixo_num, sufixo_mod)]

    out["DSC_ESTABELECIMENTO"] = out["DSC_ESTABELECIMENTO"].fillna("").astype(str).str.strip()
    out["DSC_NORM"] = map_unicos(out["DSC_ESTABELECIMENTO"], normalize_nome)

    esp = out["COD_ESPECIE"]
    out["_is_estab"] = esp.isin(ESP_ESTABELECIMENTO).fillna(False)
    out["_is_domicilio"] = esp.isin(ESP_DOMICILIO).fillna(False)
    out["_is_construcao"] = esp.isin(ESP_CONSTRUCAO).fillna(False)
    out["_is_nomeado"] = out["DSC_ESTABELECIMENTO"].ne("")

    qa = {
        "cnefe_total": n,
        "cnefe_coord_invalida": int((~out["COORD_VALIDA"]).sum()),
        "cnefe_coord_fora_brasil": int((out["COORD_VALIDA"] & ~out["COORD_BRASIL"]).sum()),
        "cnefe_cep_invalido": int((~out["CEP_STATUS"].isin(["VALIDO", "AJUSTADO_ZFILL"])).sum()),
        "cnefe_num_sem_numero": int(out["NUM_STATUS"].isin(["SEM_NUMERO", "SEM_NUMERO_ZERO"]).sum()),
        "cnefe_linhas_estabelecimento": int(out["_is_estab"].sum()),
        "cnefe_linhas_domicilio": int(out["_is_domicilio"].sum()),
        "cnefe_linhas_construcao": int(out["_is_construcao"].sum()),
        "cnefe_municipios_distintos": int(out["COD_MUNICIPIO"].replace("", np.nan).nunique()),
    }
    for k in (1, 2, 3, 4, 5, 6):
        qa[f"cnefe_nv{k}"] = int((out["NV_GEO_COORD"] == k).sum())
    return out, qa


# ---------------------------------------------------------------------------
# Índice
# ---------------------------------------------------------------------------
@dataclass
class StreetEntry:
    cep: str
    tipo: str
    base: str
    full: str
    nucleo: str
    n_tokens: int
    numbers: dict[int, dict[str, Any]] = field(default_factory=dict)
    numbers_sufixo: dict[tuple[int, str], dict[str, Any]] = field(default_factory=dict)
    centroid: dict[str, Any] = field(default_factory=dict)
    m_por_num: float = M_POR_NUM_DEFAULT


@dataclass
class MatchConfig:
    delta_proximo: int
    delta_amplo: int
    fuzzy_thr: int
    fuzzy_margin: int
    parity_penalty: int
    nv_max: int
    nv_strict: bool


def _m_por_numero(nums: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> float:
    """Metros por unidade de número, medido na própria rua (mediana dos pares consecutivos)."""
    if len(nums) < 3:
        return M_POR_NUM_DEFAULT
    ordem = np.argsort(nums, kind="mergesort")
    n, la, lo = nums[ordem], lats[ordem], lons[ordem]
    dn = np.diff(n).astype(float)
    ok = dn > 0
    if not ok.any():
        return M_POR_NUM_DEFAULT
    dist = haversine_vec(la[:-1], lo[:-1], la[1:], lo[1:])
    razao = dist[ok] / dn[ok]
    razao = razao[np.isfinite(razao) & (razao > 0)]
    if not len(razao):
        return M_POR_NUM_DEFAULT
    return float(np.clip(np.median(razao), *M_POR_NUM_CLAMP))


def build_cnefe_index(cnefe: pd.DataFrame, config: MatchConfig):
    """Devolve (index, dsc_store, agg). O dsc_store mantém as listas fora do DataFrame."""
    valid = cnefe[
        cnefe["COORD_VALIDA"] & cnefe["COORD_BRASIL"]
        & cnefe["CEP_NORM"].ne("") & cnefe["LOGR_BASE"].ne("")
    ].copy()
    if config.nv_strict:
        valid = valid[valid["NV_GEO_COORD"].fillna(99) <= config.nv_max]

    valid["NV_SORT"] = pd.to_numeric(valid["NV_GEO_COORD"], errors="coerce").fillna(99)
    valid["COD_SORT"] = valid["COD_UNICO_ENDERECO"].astype(str)

    addr = valid[valid["NUM_INT"] > 0].sort_values(
        ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT", "NUM_SUFIXO", "NV_SORT", "COD_SORT"],
        kind="mergesort",
    )
    # F-11 — a chave inclui o sufixo/modificador
    best_addr = addr.drop_duplicates(
        ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT", "NUM_SUFIXO"], keep="first"
    )

    # F-05/F-06/F-07 — agregação por endereço exato, com os conceitos separados.
    # As agregações de texto NÃO usam lambda por grupo: com dezenas de milhares de
    # grupos, um `s.dropna()` por grupo domina o tempo de construção do índice.
    # Aqui elas são reduzidas set-based sobre frames já deduplicados.
    chaves = ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT"]
    grupo = cnefe.groupby(chaves, dropna=False, sort=False)
    agg = grupo.agg(
        IBGE_N_ESTAB=("_is_estab", "sum"),
        IBGE_N_DOMICILIO=("_is_domicilio", "sum"),
        IBGE_N_CONSTRUCAO=("_is_construcao", "sum"),
        IBGE_N_REGISTROS=("COD_UNICO_ENDERECO", "size"),
        IBGE_N_ENDERECOS=("COD_UNICO_ENDERECO", "nunique"),
        IBGE_IND_ESTAB=("IND_ESTAB", "max"),
    ).reset_index()

    esp = cnefe.loc[cnefe["COD_ESPECIE"].notna(), chaves + ["COD_ESPECIE"]].copy()
    if len(esp):
        esp["_E"] = esp["COD_ESPECIE"].astype("int64").astype(str)
        esp = (esp.drop_duplicates(chaves + ["_E"])
                  .sort_values(chaves + ["_E"], kind="mergesort")
                  .groupby(chaves, dropna=False, sort=False)["_E"]
                  .agg(",".join).rename("IBGE_ESPECIES_COD").reset_index())
        agg = agg.merge(esp, on=chaves, how="left", validate="one_to_one")
    else:
        agg["IBGE_ESPECIES_COD"] = None

    nomeados = cnefe.loc[cnefe["_is_nomeado"], chaves + ["DSC_ESTABELECIMENTO", "DSC_NORM"]]
    if len(nomeados):
        nomeados = nomeados.drop_duplicates(chaves + ["DSC_ESTABELECIMENTO"])
        g = nomeados.groupby(chaves, dropna=False, sort=False)
        dsc = pd.DataFrame({
            "IBGE_DSC_ESTAB": g["DSC_ESTABELECIMENTO"].agg("|".join),
            "_DSC_LIST": g["DSC_NORM"].agg(lambda s: tuple(dict.fromkeys(x for x in s if x))),
        }).reset_index()
        agg = agg.merge(dsc, on=chaves, how="left", validate="one_to_one")
    else:
        agg["IBGE_DSC_ESTAB"] = None
        agg["_DSC_LIST"] = None
    agg["IBGE_FLAG_ESTAB"] = (agg["IBGE_N_ESTAB"] > 0).astype(int)

    # F-18 — as listas saem para um store lateral, referenciadas por id inteiro
    dsc_store: dict[int, tuple[str, ...]] = {}
    ids = []
    for i, lst in enumerate(agg["_DSC_LIST"]):
        if isinstance(lst, tuple) and lst:
            dsc_store[i] = lst
            ids.append(i)
        else:
            ids.append(-1)
    agg["_DSC_ID"] = ids
    agg = agg.drop(columns=["_DSC_LIST"])

    best_addr = best_addr.merge(
        agg, on=["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT"], how="left", validate="many_to_one"
    )

    # F-15 — groupby pré-materializado: nada de varrer best_addr por grupo
    cols = ["NUM_INT", "NUM_SUFIXO", "LATITUDE", "LONGITUDE", "NV_GEO_COORD",
            "COD_UNICO_ENDERECO", "NUM_FACE", "IBGE_FLAG_ESTAB", "IBGE_N_ESTAB",
            "IBGE_N_DOMICILIO", "IBGE_N_REGISTROS", "IBGE_N_ENDERECOS",
            "IBGE_IND_ESTAB", "IBGE_ESPECIES_COD", "IBGE_DSC_ESTAB", "_DSC_ID"]
    addr_por_rua: dict[tuple, list[dict]] = {
        k: g[cols].to_dict("records")
        for k, g in best_addr.groupby(["CEP_NORM", "LOGR_TIPO", "LOGR_BASE"], sort=False)
    }

    index: dict[str, list[StreetEntry]] = {}
    street_cols = ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "LOGR_FULL", "LOGR_NUCLEO"]
    for keys, g in valid.groupby(street_cols, sort=True, dropna=False):
        cep, tipo, base, full, nucleo = [str(x) for x in keys]
        entry = StreetEntry(cep, tipo, base, full, nucleo, len(base.split()))

        for row in addr_por_rua.get((cep, tipo, base), []):
            nv = int(row["NV_GEO_COORD"]) if pd.notna(row["NV_GEO_COORD"]) else None
            data = {
                "lat": float(row["LATITUDE"]), "lon": float(row["LONGITUDE"]), "nv": nv,
                "cod": row["COD_UNICO_ENDERECO"], "face": row.get("NUM_FACE") or "",
                "flag_estab": int(row["IBGE_FLAG_ESTAB"]) if pd.notna(row["IBGE_FLAG_ESTAB"]) else 0,
                "n_estab": int(row["IBGE_N_ESTAB"]) if pd.notna(row["IBGE_N_ESTAB"]) else 0,
                "n_domicilio": int(row["IBGE_N_DOMICILIO"]) if pd.notna(row["IBGE_N_DOMICILIO"]) else 0,
                "n_registros": int(row["IBGE_N_REGISTROS"]) if pd.notna(row["IBGE_N_REGISTROS"]) else 0,
                "n_enderecos": int(row["IBGE_N_ENDERECOS"]) if pd.notna(row["IBGE_N_ENDERECOS"]) else 0,
                "ind_estab": int(row["IBGE_IND_ESTAB"]) if pd.notna(row["IBGE_IND_ESTAB"]) else None,
                "especies": row["IBGE_ESPECIES_COD"], "dsc": row["IBGE_DSC_ESTAB"],
                "dsc_id": int(row["_DSC_ID"]) if pd.notna(row["_DSC_ID"]) else -1,
            }
            num = int(row["NUM_INT"])
            suf = str(row["NUM_SUFIXO"] or "")
            if suf:
                entry.numbers_sufixo[(num, suf)] = data
            if num not in entry.numbers or (nv or 99) < (entry.numbers[num]["nv"] or 99):
                entry.numbers[num] = data

        coords = g.loc[g["COORD_VALIDA"], ["LATITUDE", "LONGITUDE"]].dropna()
        if len(coords):
            clat = float(coords["LATITUDE"].median())
            clon = float(coords["LONGITUDE"].median())
            d = haversine_vec(clat, clon, coords["LATITUDE"].to_numpy(), coords["LONGITUDE"].to_numpy())
            p95 = float(np.percentile(d, 95)) if len(d) else 0.0
        else:
            clat = clon = p95 = float("nan")
        entry.centroid = {"lat": clat, "lon": clon, "nv": NV_SINTETICO, "cod": None,
                          "p95": p95, "n": int(len(coords))}

        if entry.numbers:
            ns = np.fromiter(entry.numbers.keys(), dtype=float, count=len(entry.numbers))
            la = np.fromiter((v["lat"] for v in entry.numbers.values()), dtype=float, count=len(entry.numbers))
            lo = np.fromiter((v["lon"] for v in entry.numbers.values()), dtype=float, count=len(entry.numbers))
            entry.m_por_num = _m_por_numero(ns, la, lo)

        index.setdefault(cep, []).append(entry)
    return index, dsc_store, agg


# ---------------------------------------------------------------------------
# Similaridade de logradouro (F-09)
# ---------------------------------------------------------------------------
PENALIDADE_TIPO_DIVERGENTE = 6.0


def street_similarity(cnpj_tipo: str, cnpj_base: str, cnpj_nucleo: str, cnpj_tokens: int,
                      entry: StreetEntry) -> float:
    """Somente token_sort_ratio. Sem token_set_ratio, sem penalidade artificial.

    A v2.1 usava max(token_set_ratio, token_sort_ratio). O token_set_ratio devolve 100
    quando um nome é subconjunto do outro ('BRASIL' x 'BRASIL NOVO'), e o `max` garantia
    que o componente permissivo sempre vencesse — falso positivo com score máximo e
    margem larga, que nenhum limiar conseguia barrar.

    O token_sort_ratio sozinho já separa as duas famílias com folga larga
    (ver tests/test_similaridade.py): typos legítimos ficam acima de 94 e truncamentos
    abaixo de 78. Qualquer penalidade adicional só produz falso negativo em typo que
    funde duas palavras ('SEMINARISTAWENDELINO PLEIN'), sem ganho de precisão.
    """
    if not cnpj_base or len(cnpj_base) < 3:
        return 0.0
    s_base = fuzz.token_sort_ratio(cnpj_base, entry.base)
    s_nuc = fuzz.token_sort_ratio(cnpj_nucleo, entry.nucleo) if cnpj_nucleo and entry.nucleo else 0.0
    score = float(max(s_base, s_nuc))
    if cnpj_tipo and entry.tipo and cnpj_tipo != entry.tipo:
        score -= PENALIDADE_TIPO_DIVERGENTE
    return max(0.0, score)


def choose_street(entries, tipo, base, nucleo, n_tokens, config):
    if not entries or not base:
        return None, "SEM_LOGRADOURO", 0.0, 0.0, 0

    exato_tipo = [e for e in entries if e.base == base and e.tipo == tipo and tipo]
    if len(exato_tipo) == 1:
        return exato_tipo[0], "EXATO_TIPO", 100.0, 0.0, 1
    if len(exato_tipo) > 1:
        return None, "AMBIGUO_EXATO_TIPO", 100.0, 100.0, len(exato_tipo)

    exato_base = [e for e in entries if e.base == base]
    if len(exato_base) == 1:
        return exato_base[0], "EXATO_BASE_UNICO", 98.0, 0.0, 1
    if len(exato_base) > 1:
        return None, "AMBIGUO_TIPO_LOGRADOURO", 98.0, 98.0, len(exato_base)

    scored = sorted(
        ((street_similarity(tipo, base, nucleo, n_tokens, e), e) for e in entries),
        key=lambda x: (-x[0], x[1].full, x[1].tipo),
    )
    if not scored:
        return None, "SEM_CANDIDATO", 0.0, 0.0, 0
    top_score, top = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if top_score < config.fuzzy_thr:
        return None, "FUZZY_ABAIXO_LIMIAR", top_score, second, len(scored)
    if len(scored) > 1 and (top_score - second) < config.fuzzy_margin:
        return None, "FUZZY_AMBIGUO", top_score, second, len(scored)
    return top, "FUZZY_UNICO", top_score, second, len(scored)


def choose_number(numbers, num, max_delta, parity_penalty):
    candidatos = []
    for n, data in numbers.items():
        delta = abs(num - n)
        if delta > max_delta:
            continue
        paridade = 0 if (num % 2) == (n % 2) else 1
        nv = data.get("nv") if data.get("nv") is not None else 99
        candidatos.append((delta + paridade * parity_penalty, delta, paridade, nv, n, data))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    _, delta, paridade, _, n, data = candidatos[0]
    return n, data, delta, paridade


# ---------------------------------------------------------------------------
# Incerteza (F-02)
# ---------------------------------------------------------------------------
def desvio_metros(nv, delta: int, m_por_num: float) -> tuple[float, str]:
    piso = NV_FLOOR_M.get(int(nv) if nv is not None else 99, 800.0)
    if delta == 0:
        return round(piso, 1), "PISO_NV"
    interp = float(delta) * float(m_por_num)
    return round(math.hypot(piso, interp), 1), "PISO_NV+INTERPOLACAO"


def _cap_conf(conf: str, nv) -> tuple[str, str]:
    teto = NV_CONF_TETO.get(int(nv) if nv is not None else 99, "BAIXA")
    if _CONF_ORD[teto] < _CONF_ORD[conf]:
        return teto, f"TETO_POR_NV_{NV_CLASSE.get(int(nv) if nv is not None else 99, 'DESCONHECIDO')}"
    return conf, ""


# ---------------------------------------------------------------------------
# Resolução
# ---------------------------------------------------------------------------
CAMPOS_MATCH = [
    "LATITUDE", "LONGITUDE", "NV_GEO_COORD", "NV_CLASSE", "COD_UNICO_ENDERECO", "NUM_FACE",
    "MATCH_PASS", "MATCH_CONF", "MATCH_DETALHE", "CONF_MOTIVO", "NUM_DELTA", "NUM_IBGE",
    "NUM_SUFIXO_MATCH", "PARIDADE_DIVERGENTE", "LOGR_MATCH", "LOGR_SCORE", "LOGR_SCORE_SEGUNDO",
    "LOGR_CANDIDATOS", "DESVIO_METROS", "DESVIO_FONTE", "IBGE_FLAG_COMERCIAL", "IBGE_IND_ESTAB",
    "IBGE_N_ESTAB", "IBGE_N_DOMICILIO", "IBGE_QTD_UNIDADES", "IBGE_N_ENDERECOS",
    "IBGE_ESPECIES_COD", "IBGE_DSC_ESTAB", "_DSC_ID",
]


def empty_match(reason, score=0.0, second=0.0, candidates=0) -> dict:
    d = {c: None for c in CAMPOS_MATCH}
    d.update({
        "LATITUDE": np.nan, "LONGITUDE": np.nan, "NV_GEO_COORD": pd.NA, "NV_CLASSE": "SEM_COORDENADA",
        "MATCH_PASS": "SEM_MATCH", "MATCH_CONF": "SEM_MATCH", "MATCH_DETALHE": reason,
        "CONF_MOTIVO": "", "NUM_DELTA": pd.NA, "NUM_IBGE": pd.NA, "NUM_SUFIXO_MATCH": "",
        "PARIDADE_DIVERGENTE": pd.NA, "LOGR_SCORE": score, "LOGR_SCORE_SEGUNDO": second,
        "LOGR_CANDIDATOS": candidates, "DESVIO_METROS": np.nan, "DESVIO_FONTE": "",
        "IBGE_FLAG_COMERCIAL": pd.NA, "IBGE_IND_ESTAB": pd.NA, "IBGE_N_ESTAB": pd.NA,
        "IBGE_N_DOMICILIO": pd.NA, "IBGE_QTD_UNIDADES": pd.NA, "IBGE_N_ENDERECOS": pd.NA,
        "NUM_FACE": "", "_DSC_ID": -1,
    })
    return d


def build_match_result(street, data, pass_name, conf, method, score, second, count,
                       num_ibge, delta, paridade, sufixo_match) -> dict:
    nv = data["nv"]
    conf_final, motivo = _cap_conf(conf, nv)
    dev, fonte = desvio_metros(nv, delta, street.m_por_num)
    return {
        "LATITUDE": data["lat"], "LONGITUDE": data["lon"], "NV_GEO_COORD": nv,
        "NV_CLASSE": NV_CLASSE.get(nv, "DESCONHECIDO") if nv is not None else "DESCONHECIDO",
        "COD_UNICO_ENDERECO": data["cod"], "NUM_FACE": data.get("face", ""),
        "MATCH_PASS": pass_name, "MATCH_CONF": conf_final, "MATCH_DETALHE": method,
        "CONF_MOTIVO": motivo, "NUM_DELTA": delta, "NUM_IBGE": num_ibge,
        "NUM_SUFIXO_MATCH": sufixo_match, "PARIDADE_DIVERGENTE": paridade,
        "LOGR_MATCH": street.full, "LOGR_SCORE": score, "LOGR_SCORE_SEGUNDO": second,
        "LOGR_CANDIDATOS": count, "DESVIO_METROS": dev, "DESVIO_FONTE": fonte,
        "IBGE_FLAG_COMERCIAL": data["flag_estab"], "IBGE_IND_ESTAB": data["ind_estab"],
        "IBGE_N_ESTAB": data["n_estab"], "IBGE_N_DOMICILIO": data["n_domicilio"],
        "IBGE_QTD_UNIDADES": data["n_registros"], "IBGE_N_ENDERECOS": data["n_enderecos"],
        "IBGE_ESPECIES_COD": data["especies"], "IBGE_DSC_ESTAB": data["dsc"],
        "_DSC_ID": data["dsc_id"],
    }


def resolve_match(cep, tipo, base, nucleo, n_tokens, num, num_status, sufixo, index, config) -> dict:
    entries = index.get(cep)
    if entries is None:
        # F-10 — motivo próprio: o CEP não existe no CNEFE
        return empty_match("SEM_CEP_NO_INDICE" if cep else "CEP_AUSENTE")

    street, method, score, second, count = choose_street(entries, tipo, base, nucleo, n_tokens, config)
    if street is None:
        return empty_match(method, score, second, count)

    if num == 0:
        if num_status not in {"SEM_NUMERO", "SEM_NUMERO_ZERO"}:
            return empty_match(f"{method}+NUMERO_{num_status}", score, second, count)
        c = street.centroid
        if not valid_coord(c["lat"], c["lon"]):
            return empty_match(f"{method}+SEM_CENTROIDE", score, second, count)
        r = empty_match("P8_CENTROIDE_LOGRADOURO", score, second, count)
        r.update({
            "LATITUDE": c["lat"], "LONGITUDE": c["lon"], "NV_GEO_COORD": NV_SINTETICO,
            "NV_CLASSE": NV_CLASSE[NV_SINTETICO], "MATCH_PASS": "P8_CENTROIDE_SN",
            "MATCH_CONF": "BAIXA", "MATCH_DETALHE": f"{method}+CENTROIDE_SEM_NUMERO",
            "CONF_MOTIVO": "CENTROIDE_SINTETICO", "LOGR_MATCH": street.full,
            "DESVIO_METROS": (round(max(c["p95"], NV_FLOOR_M[NV_SINTETICO]), 1)
                              if c["p95"] == c["p95"] else float(NV_FLOOR_M[NV_SINTETICO])),
            "DESVIO_FONTE": "CENTROIDE_P95",
            "IBGE_FLAG_COMERCIAL": pd.NA,  # não herda evidência de um número específico
        })
        return r

    # F-11 — tenta primeiro número+sufixo, depois número
    if sufixo and (num, sufixo) in street.numbers_sufixo:
        data = street.numbers_sufixo[(num, sufixo)]
        pass_name = {"EXATO_TIPO": "P1_EXATO", "EXATO_BASE_UNICO": "P2_EXATO_BASE"}.get(method, "P4_FUZZY_EXATO")
        conf = "ALTA" if pass_name == "P1_EXATO" else "MEDIA"
        return build_match_result(street, data, pass_name, conf, method, score, second, count, num, 0, 0, sufixo)

    if num in street.numbers:
        data = street.numbers[num]
        pass_name = {"EXATO_TIPO": "P1_EXATO", "EXATO_BASE_UNICO": "P2_EXATO_BASE"}.get(method, "P4_FUZZY_EXATO")
        conf = "ALTA" if pass_name == "P1_EXATO" else "MEDIA"
        return build_match_result(street, data, pass_name, conf, method, score, second, count, num, 0, 0, "")

    perto = choose_number(street.numbers, num, config.delta_proximo, config.parity_penalty)
    if perto:
        n, data, delta, paridade = perto
        pass_name = "P3_PROX_EXATO" if method.startswith("EXATO") else "P5_FUZZY_PROX"
        return build_match_result(street, data, pass_name, "MEDIA", method, score, second, count, n, delta, paridade, "")

    amplo = choose_number(street.numbers, num, config.delta_amplo, config.parity_penalty)
    if amplo:
        n, data, delta, paridade = amplo
        pass_name = "P6_PROX_AMPLO" if method.startswith("EXATO") else "P7_FUZZY_AMPLO"
        return build_match_result(street, data, pass_name, "BAIXA", method, score, second, count, n, delta, paridade, "")

    return empty_match(f"{method}+SEM_NUMERO_PROXIMO", score, second, count)


def apply_matching(cnpj: pd.DataFrame, index, config: MatchConfig) -> pd.DataFrame:
    """F-16 — resolve o logradouro uma vez por chave distinta, não por registro."""
    chave = list(zip(
        cnpj["CEP_NORM"], cnpj["LOGR_TIPO"], cnpj["LOGR_BASE"], cnpj["LOGR_NUCLEO"],
        cnpj["LOGR_TOKENS"], cnpj["NUM_INT"], cnpj["NUM_STATUS"], cnpj["NUM_SUFIXO"],
    ))
    cache: dict[tuple, dict] = {}
    linhas = []
    for k in chave:
        r = cache.get(k)
        if r is None:
            r = resolve_match(k[0], k[1], k[2], k[3], k[4], int(k[5]), k[6], k[7], index, config)
            cache[k] = r
        linhas.append(r)
    match_df = pd.DataFrame(linhas, index=cnpj.index, columns=CAMPOS_MATCH)
    return pd.concat([cnpj, match_df], axis=1)
