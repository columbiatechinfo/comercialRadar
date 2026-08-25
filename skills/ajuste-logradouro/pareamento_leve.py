# -*- coding: utf-8 -*-
"""
pareamento_leve.py — o MÍNIMO de cruzamento necessário para o léxico aprender
==============================================================================
Esta skill NÃO resolve entidade: não há union-find, `imovel_id`, store nem
crosswalk. O cruzamento existe por um motivo único e declarado: o
o léxico só aprende `tokenA ≡ tokenB` a partir de PARES CONFIRMADOS
POR EVIDÊNCIA INDEPENDENTE — mesmo número exato + distância apertada. Sem par,
não há prova; sem prova, o léxico viraria chute.

Blocking SET-BASED, sem shapely/STRtree:
    chave = (numero_int, célula_x, célula_y)     célula = floor(coord / raio)
    vizinhança 3×3  ⇒ todo par a ≤ raio cai em exatamente um dos 9 offsets
    equi-join em pandas (hash join) + filtro exato de distância euclidiana

Custo linear no nº de registros × densidade local, e o par (a,b) aparece uma
única vez (offset = célula_b − célula_a é único), então não há dedup a pagar.

Guardas:
  * gate no NÚMERO ESTRUTURADO — não no int truncado. O blocking usa a base
    (`279`), mas a prova exige a chave completa (`279|A`): `279-A` e `279-B` NÃO
    formam par. `S/N` e `KM` não ancoram nada e ficam fora, contabilizados.
  * `teto_bloco`: bloco (número, célula) maior que o teto é PULADO e registrado
    na auditoria — evita o custo quadrático de um número popular em condomínio
    denso, sem esconder a exclusão.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

import similaridade as S

OFFSETS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
INTERNO = "__a2l_"
GID, XI, YI, LOGR_PRE = INTERNO + "gid", INTERNO + "x", INTERNO + "y", INTERNO + "logr_pre"


def gerar_candidatos(df: pd.DataFrame, raio_m: float, teto_bloco: int = 200,
                     apenas_entre_fontes: bool = False, modo_numero: str = "estrito"):
    """df exige: __a2l_gid, aj_source_id, aj_num_base, aj_num_chave, aj_num_tipo,
    __a2l_logr_pre, __a2l_x, __a2l_y.

    Retorna (candidatos, auditoria, diag):
      candidatos: [(gid_a, gid_b, dist_m)]  — contrato de `lexico_seguro.minerar`
      auditoria : DataFrame dos mesmos pares com fonte, logradouro e similaridade
      diag      : dict com contagens (sem número, blocos podados, pares avaliados)
    """
    d = df[df["aj_num_base"].notna() & df["aj_num_chave"].notna()
           & df[XI].notna() & df[YI].notna()].copy()
    diag = {"registros": int(len(df)), "elegiveis": int(len(d)),
            "sem_numero_ou_coord": int(len(df) - len(d)),
            "modo_numero": modo_numero, "pares_bloqueados_modificador": 0,
            "blocos_podados": 0, "registros_podados": 0}
    if d.empty:
        return [], pd.DataFrame(), diag

    d["cx"] = np.floor(d[XI].to_numpy() / raio_m).astype("int64")
    d["cy"] = np.floor(d[YI].to_numpy() / raio_m).astype("int64")
    d["num_i"] = d["aj_num_base"].astype("int64")

    # poda de bloco denso (registrada, não silenciosa)
    tam = d.groupby(["num_i", "cx", "cy"], sort=False)[GID].transform("size")
    podar = tam > teto_bloco
    if bool(podar.any()):
        diag["blocos_podados"] = int(d.loc[podar, ["num_i", "cx", "cy"]]
                                     .drop_duplicates().shape[0])
        diag["registros_podados"] = int(podar.sum())
        d = d.loc[~podar]

    cols = [GID, "aj_source_id", LOGR_PRE, "aj_num_chave", "aj_num_tipo",
            XI, YI, "num_i", "cx", "cy"]
    esq = d[cols]
    partes = []
    for dx, dy in OFFSETS:
        dir_ = d[cols].copy()
        dir_["cx"] = dir_["cx"] - dx
        dir_["cy"] = dir_["cy"] - dy
        partes.append(esq.merge(dir_, on=["num_i", "cx", "cy"],
                                suffixes=("_a", "_b"), how="inner"))
    p = pd.concat(partes, ignore_index=True)
    p = p[p[GID + "_a"] < p[GID + "_b"]]                     # i<j: sem self, sem espelho
    if apenas_entre_fontes:
        p = p[p["aj_source_id_a"] != p["aj_source_id_b"]]
    if p.empty:
        return [], pd.DataFrame(), diag

    if modo_numero == "estrito":
        igual = p["aj_num_chave_a"].to_numpy() == p["aj_num_chave_b"].to_numpy()
    else:
        # compatível: modificador ausente de um lado — mas SÓ entre tipo NUMERO.
        # Antes, a chave da FAIXA ('279|/281') passava pelo mesmo teste e formava
        # par com '279', misturando faixa de numeração com número simples.
        so_numero = ((p["aj_num_tipo_a"].to_numpy() == "NUMERO")
                     & (p["aj_num_tipo_b"].to_numpy() == "NUMERO"))
        ma = p["aj_num_chave_a"].str.split("|").str[1].to_numpy()
        mb = p["aj_num_chave_b"].str.split("|").str[1].to_numpy()
        igual = so_numero & ((ma == mb) | (ma == "") | (mb == ""))
    diag["pares_bloqueados_modificador"] = int((~igual).sum())
    p = p[igual]
    if p.empty:
        return [], pd.DataFrame(), diag

    p["dist_m"] = np.hypot(p[XI + "_a"].to_numpy() - p[XI + "_b"].to_numpy(),
                           p[YI + "_a"].to_numpy() - p[YI + "_b"].to_numpy()).round(2)
    p = p[p["dist_m"] <= raio_m].copy()
    diag["pares"] = int(len(p))
    if p.empty:
        return [], pd.DataFrame(), diag

    p["sim_logr"] = [round(S.token_sort(a, b), 3)
                     for a, b in zip(p[LOGR_PRE + "_a"], p[LOGR_PRE + "_b"])]
    p = p.sort_values([GID + "_a", GID + "_b"], kind="mergesort")   # determinismo

    candidatos = list(zip(p[GID + "_a"].astype(int), p[GID + "_b"].astype(int), p["dist_m"]))
    aud = p[[GID + "_a", GID + "_b", "aj_source_id_a", "aj_source_id_b",
             "aj_num_chave_a", LOGR_PRE + "_a", LOGR_PRE + "_b", "dist_m", "sim_logr"]].rename(
        columns={GID + "_a": "gid_a", GID + "_b": "gid_b",
                 LOGR_PRE + "_a": "logr_a", LOGR_PRE + "_b": "logr_b",
                 "aj_num_chave_a": "numero_chave", "aj_source_id_a": "fonte_a",
                 "aj_source_id_b": "fonte_b"})
    return candidatos, aud.reset_index(drop=True), diag
