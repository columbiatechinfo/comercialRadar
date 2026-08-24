#!/usr/bin/env python3
"""Classificação, evidência e decisão — v2.2.

Mudanças sobre a v2.1:
- F-16  as seis passagens `.apply(axis=1)` viram máscaras booleanas / np.select.
- F-27  ser fuzzy deixa de somar no score de existência (evidência fraca não é evidência).
- F-28  capital AUSENTE deixa de ser indistinguível de capital zero.
- F-29  data AUSENTE deixa de rebaixar silenciosamente o score estrutural.
- F-01  NV de baixa precisão passa a penalizar o score de existência.
- F-07  o indicador oficial do IBGE passa a decidir a rota 1:1 x multi-economia.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

# --- CNAE -------------------------------------------------------------------
CNAE_DIV_MAP = {
    **{x: ("PRESENCIAL", True) for x in ["45", "47", "55", "56", "75", "85", "86", "87", "90", "91", "93", "95", "96"]},
    **{x: ("PRESENCIAL", False) for x in [
        "01", "02", "03", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16",
        "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31",
        "32", "33", "35", "36", "37", "38", "39", "41", "42", "43", "46", "84"]},
    **{x: ("HIBRIDO", False) for x in ["52", "61", "64", "65", "68", "71", "72", "74", "77", "79", "81", "88", "94"]},
    **{x: ("MOVEL", False) for x in ["49", "50", "51", "53", "97"]},
    **{x: ("PAPEL", False) for x in ["58", "59", "60", "62", "63", "66", "69", "70", "73", "78", "80", "82", "83", "99"]},
}

CNAE_EXCECAO = {
    "5612100": ("MOVEL", True), "4923002": ("MOVEL", False), "4923001": ("MOVEL", False),
    "8230002": ("PRESENCIAL", True), "4924800": ("HIBRIDO", True), "9700500": ("MOVEL", False),
    "5320201": ("MOVEL", False), "5320202": ("MOVEL", False), "4930201": ("MOVEL", False),
    "4930202": ("MOVEL", False), "8712300": ("MOVEL", True), "5310502": ("PRESENCIAL", True),
    "8219901": ("PRESENCIAL", True), "8299707": ("PRESENCIAL", True),
}

CNAE_EXIGE_ESTRUTURA_PREFIX4 = {
    "4711", "4712", "4713", "4721", "4722", "4723", "4724", "4729", "4520", "4530", "4541",
    "4542", "4731", "4732", "4784", "5510", "5590", "5611", "8610", "8630", "8640", "8650",
    "8660", "4741", "4742", "4743", "4744", "4751", "4752", "4753", "4754", "4755", "4756",
    "4757", "4759", "4761", "4762", "4763", "4771", "4772", "4773", "4774", "4781", "4782",
    "4783", "4785", "4789",
}

SEG_MAP = {
    **{x: "AGRONEGOCIO" for x in ["01", "02", "03", "05", "06", "07", "08", "09"]},
    "10": "INDUSTRIA_ALIMENTOS", "11": "INDUSTRIA_ALIMENTOS",
    **{x: "INDUSTRIA" for x in ["12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22",
                                "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33"]},
    **{x: "ENERGIA_UTILIDADES" for x in ["35", "36", "37", "38", "39"]},
    **{x: "CONSTRUCAO" for x in ["41", "42", "43"]},
    "45": "COMERCIO_VEICULOS", "46": "COMERCIO_ATACADO", "47": "COMERCIO_VAREJO",
    **{x: "TRANSPORTE_LOGISTICA" for x in ["49", "50", "51", "52", "53"]},
    **{x: "ALIMENTACAO_HOSPEDAGEM" for x in ["55", "56"]},
    **{x: "TI_DIGITAL" for x in ["58", "59", "60", "61", "62", "63"]},
    **{x: "FINANCEIRO" for x in ["64", "65", "66"]},
    "68": "IMOBILIARIO",
    **{x: "SERVICOS_PROFISSIONAIS" for x in ["69", "70", "71", "72", "73", "74", "77", "78", "79"]},
    "75": "SAUDE", "80": "FACILITIES_SEGURANCA", "81": "FACILITIES_SEGURANCA",
    "82": "SERVICOS_ADMIN", "83": "SERVICOS_ADMIN", "84": "ADMINISTRACAO_PUBLICA",
    "85": "EDUCACAO", "86": "SAUDE", "87": "SAUDE", "88": "SAUDE",
    "90": "ENTRETENIMENTO", "91": "ENTRETENIMENTO", "93": "ENTRETENIMENTO",
    "94": "ASSOCIACOES_ONG", "96": "SERVICOS_PESSOAIS", "97": "SERVICOS_PESSOAIS", "99": "OUTROS",
}

PORTE_BINS = [-1, 0, 999, 9_999, 99_999, 999_999, 9_999_999, 99_999_999, float("inf")]
PORTE_LABELS = ["CAP_ZERO", "CAP_MICRO_MIN", "CAP_MICRO", "CAP_MICRO_PLUS",
                "CAP_PEQUENO", "CAP_MEDIO", "CAP_GRANDE", "CAP_CORPORATIVO"]
PORTE_SIMPLES = {"CAP_ZERO": "MICRO", "CAP_MICRO_MIN": "MICRO", "CAP_MICRO": "MICRO",
                 "CAP_MICRO_PLUS": "PEQUENO", "CAP_PEQUENO": "MEDIO", "CAP_MEDIO": "MEDIO",
                 "CAP_GRANDE": "GRANDE", "CAP_CORPORATIVO": "GRANDE"}

RESID_RE = re.compile(r"\b(APT|APTO|APARTAMENTO|AP|QUARTO|QTO|QT|CASA|RESIDENCIA|KITNET|KITINETE|QUITINETE|MORADIA|DORMITORIO|SUITE|BLOCO|BL)\b")
COMERC_RE = re.compile(r"\b(LOJA|SALA|SL|CONJ|CONJUNTO|GALPAO|BOX|QUIOSQUE|QUIOSK|ESCRITORIO|ESCRIT|PAVILHAO|PAVLH|DEPOSITO|ANDAR|PISO|SOBRELOJA|TERREO|MEZANINO|COMERCIAL|SALAO|PREDIO|FRENTE)\b")

IND_ESTAB_LABEL = {
    1: "ESTAB_UNICO", 2: "ESTAB_MULTIPLO_ATE_10",
    3: "ESTAB_MULTIPLO_MAIS_10", 4: "ESTAB_MULTIPLO_QTD_DESCONHECIDA",
}


# ---------------------------------------------------------------------------
def complement_type_vec(serie: pd.Series) -> pd.Series:
    from _normalizacao import deaccent
    codes, uniques = pd.factorize(serie.fillna("").astype(str), sort=False)
    tabela = []
    for v in uniques:
        s = deaccent(str(v)).upper().strip()
        if not s:
            tabela.append("VAZIO"); continue
        r, c = bool(RESID_RE.search(s)), bool(COMERC_RE.search(s))
        tabela.append("AMBIGUO" if (r and c) else "RESIDENCIAL" if r else "COMERCIAL" if c else "OUTRO")
    return pd.Series([tabela[i] if i >= 0 else "VAZIO" for i in codes], index=serie.index)


def classify_profile(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    cnae7, div = out["CNAE7"], out["CNAE_DIV"]

    exc_p = cnae7.map(lambda c: CNAE_EXCECAO.get(c, (None, None))[0])
    exc_a = cnae7.map(lambda c: CNAE_EXCECAO.get(c, (None, None))[1])
    div_p = div.map(lambda d: CNAE_DIV_MAP.get(d, (None, None))[0])
    div_a = div.map(lambda d: CNAE_DIV_MAP.get(d, (None, None))[1])

    tem_exc, tem_div = exc_p.notna(), div_p.notna()
    out["PERFIL_ESTRUTURA"] = np.select([tem_exc, tem_div], [exc_p, div_p], default="INCERTO")
    out["ATEND_PUBLICO"] = np.select([tem_exc, tem_div], [exc_a.fillna(False), div_a.fillna(False)], default=False).astype(bool)
    out["PERFIL_MOTIVO"] = np.select([tem_exc, tem_div], ["EXCECAO_CNAE7", "REGRA_DIVISAO_CNAE"], default="CNAE_NAO_MAPEADO")
    out["PERFIL_CONFIANCA"] = np.select([tem_exc, tem_div], ["ALTA", "MEDIA"], default="BAIXA")

    out["COMPLEMENTO_TIPO"] = complement_type_vec(out["complemento"])
    out["CNAE_EXIGE_ESTRUTURA"] = out["CNAE7"].str[:4].isin(CNAE_EXIGE_ESTRUTURA_PREFIX4)
    out["FLAG_ESTABELECIDO"] = out["SITUACAO_NORM"].eq("ATIVA") & out["ATIVO_ANOS"].ge(2)
    out["FLAG_RECENTE"] = out["ATIVO_ANOS"].lt(1) & out["ATIVO_ANOS"].notna()
    out["FLAG_OPERACAO_MOVEL"] = out["PERFIL_ESTRUTURA"].eq("MOVEL")
    return out


def classify_company(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["SEGMENTO"] = out["CNAE_DIV"].map(SEG_MAP).fillna("OUTROS")
    porte = pd.cut(out["CAPITAL_NUM"], bins=PORTE_BINS, labels=PORTE_LABELS, right=True)
    out["PORTE_CAPITAL"] = porte.astype(object).where(porte.notna(), "INDEFINIDO").astype(str)
    out["PORTE_SIMPLES"] = out["PORTE_CAPITAL"].map(PORTE_SIMPLES).fillna("INDEFINIDO")
    out["PERFIL_EMPRESA"] = out["SEGMENTO"] + "_" + out["PORTE_SIMPLES"]

    out["CNPJ_RAIZ"] = out["CNPJ_NORM"].str[:8]
    filiais = out.loc[out["TIPO_EMPRESA_NORM"].eq("FILIAL"), "CNPJ_RAIZ"].value_counts()
    out["N_FILIAIS_NA_BASE"] = out["CNPJ_RAIZ"].map(filiais).fillna(0).astype(int)
    emp_ind = out["NAT_JUR_DIGITOS"].str.startswith("2135")
    out["PAPEL_ESTRUTURAL"] = np.select(
        [out["TIPO_EMPRESA_NORM"].eq("FILIAL"), out["N_FILIAIS_NA_BASE"].gt(0), emp_ind],
        ["FILIAL", "MATRIZ_COM_REDE", "UNIDADE_UNICA"], default="MATRIZ",
    )
    return out


# ---------------------------------------------------------------------------
# Evidência de nome no CNEFE
# ---------------------------------------------------------------------------
def validate_name_vec(df: pd.DataFrame, dsc_store: dict[int, tuple[str, ...]],
                      threshold: int) -> pd.DataFrame:
    status = np.full(len(df), "SEM_DSC", dtype=object)
    score = np.zeros(len(df), dtype=float)
    melhor = np.full(len(df), None, dtype=object)

    dsc_id = df["_DSC_ID"].fillna(-1).astype(int).to_numpy()
    nomes = df["NOME_NORM"].fillna("").to_numpy()
    razoes = df["RAZAO_NORM"].fillna("").to_numpy()

    alvo = np.nonzero(dsc_id >= 0)[0]
    cache: dict[tuple, tuple] = {}
    for i in alvo:
        chave = (dsc_id[i], nomes[i], razoes[i])
        r = cache.get(chave)
        if r is None:
            candidatos = [x for x in (nomes[i], razoes[i]) if x]
            if not candidatos:
                r = ("SEM_NOME_CNPJ", 0.0, None)
            else:
                lista = dsc_store.get(dsc_id[i], ())
                best_s, best_d = 0.0, None
                for d in lista:
                    for nome in candidatos:
                        s = float(fuzz.token_set_ratio(nome, d))
                        if s > best_s:
                            best_s, best_d = s, d
                if best_s >= threshold:
                    r = ("CONFIRMADO", best_s, best_d)
                elif best_s >= 65:
                    r = ("PROVAVEL", best_s, best_d)
                else:
                    r = ("NAO_CONFIRMADO", best_s, None)
            cache[chave] = r
        status[i], score[i], melhor[i] = r
    return pd.DataFrame(
        {"XFERA_EXISTE_NO_LOCAL": status, "XFERA_EXIST_NOME_SCORE": score, "XFERA_EXIST_DSC": melhor},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Score de existência (vetorizado)
# ---------------------------------------------------------------------------
def existence_score_vec(df: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    n = len(df)
    tem_coord = df["LATITUDE"].notna()
    dt = df["DATA_ABERTURA_DT"]

    aplic = np.where(dt.isna(), "INDETERMINADO_DATA_AUSENTE",
                     np.where(dt > cutoff, "NAO_POS_CNEFE", "SIM"))
    aplic = np.where(~tem_coord, "NAO_APLICAVEL_SEM_COORDENADA", aplic)

    score = np.zeros(n, dtype=float)
    motivos = [[] for _ in range(n)]

    def add(mask, pts, tag):
        m = np.asarray(mask, dtype=bool)
        score[m] += pts
        for i in np.nonzero(m)[0]:
            motivos[i].append(f"{tag}:{pts:+g}")

    calc = np.asarray(tem_coord) & (aplic == "SIM")
    nome = df["XFERA_EXISTE_NO_LOCAL"].to_numpy()
    nv = pd.to_numeric(df["NV_GEO_COORD"], errors="coerce").fillna(99).to_numpy()
    conf = df["MATCH_CONF"].to_numpy()
    passe = df["MATCH_PASS"].fillna("").to_numpy()
    delta = pd.to_numeric(df["NUM_DELTA"], errors="coerce")

    add(calc & (nome == "CONFIRMADO"), 6, "NOME_CONFIRMADO_IBGE")
    add(calc & (nome == "PROVAVEL"), 3, "NOME_PROVAVEL_IBGE")
    add(calc & (pd.to_numeric(df["IBGE_FLAG_COMERCIAL"], errors="coerce").fillna(0).to_numpy() == 1),
        4, "ESTABELECIMENTO_NO_LOCAL")
    add(calc & (df["SITUACAO_NORM"].to_numpy() == "ATIVA"), 1, "ATIVA")
    add(calc & np.isin(conf, ["ALTA", "MEDIA"]), 1, "MATCH_PRECISO")
    add(calc & np.isin(nv, [1, 2]), 1, "COORD_NIVEL_ENDERECO")
    # F-27 — ser fuzzy não soma; e NV ruim penaliza (F-01)
    add(calc & (nv >= 4) & (nv != 90), -1, "COORD_BAIXA_PRECISAO")
    add(calc & (passe == "P8_CENTROIDE_SN"), -1, "CENTROIDE")
    add(calc & (conf == "BAIXA") & delta.notna().to_numpy() & (delta.fillna(0).to_numpy() > 0), -1, "DELTA_AMPLO")

    score = np.clip(score, 0.0, 15.0)
    label = np.select(
        [score >= 9, score >= 6, score >= 4, score > 0],
        ["EXISTE_QUASE_CERTO", "EXISTE_PROVAVEL", "EXISTE_POSSIVEL", "EXISTE_INCERTO"],
        default="SEM_EVIDENCIA",
    ).astype(object)
    label = np.where(~np.asarray(tem_coord), "SEM_EVIDENCIA",
                     np.where(aplic == "NAO_POS_CNEFE", "INDETERMINADA_POS_CNEFE",
                              np.where(aplic == "INDETERMINADO_DATA_AUSENTE",
                                       "INDETERMINADA_DATA_AUSENTE", label)))
    score = np.where(calc, score, 0.0)
    texto = np.array([" | ".join(m) if m else "" for m in motivos], dtype=object)
    texto = np.where(~np.asarray(tem_coord), "SEM_COORDENADA",
                     np.where(aplic != "SIM", "EVIDENCIA_CNEFE_NAO_DECISORIA",
                              np.where(texto == "", "SEM_EVIDENCIA_POSITIVA", texto)))
    return pd.DataFrame({
        "XFERA_EXIST_SCORE": score, "XFERA_EXISTENCIA_FISICA": label,
        "XFERA_EXIST_MOTIVOS": texto, "XFERA_IBGE_APLICAVEL": aplic,
    }, index=df.index)


# ---------------------------------------------------------------------------
# Score estrutural (vetorizado, F-28/F-29)
# ---------------------------------------------------------------------------
def score_structure_vec(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    score = np.zeros(n, dtype=float)
    motivos = [[] for _ in range(n)]

    def add(mask, pts, tag):
        m = np.asarray(mask, dtype=bool)
        score[m] += pts
        for i in np.nonzero(m)[0]:
            motivos[i].append(f"{tag}:{pts:+g}")

    p = df["PERFIL_ESTRUTURA"].to_numpy()
    papel = df["PAPEL_ESTRUTURAL"].to_numpy()
    cap_ok = df["CAPITAL_STATUS"].eq("VALIDO").to_numpy()
    data_ok = df["DATA_ABERTURA_STATUS"].str.startswith("VALIDO").fillna(False).to_numpy()

    add(p == "PRESENCIAL", 3, "CNAE_PRESENCIAL")
    add(df["ATEND_PUBLICO"].to_numpy(dtype=bool), 2, "ATEND_PUBLICO")
    add(papel == "FILIAL", 2, "FILIAL")
    add((pd.to_numeric(df["IBGE_FLAG_COMERCIAL"], errors="coerce").fillna(0).to_numpy() == 1)
        & (df["XFERA_IBGE_APLICAVEL"].to_numpy() == "SIM"), 2, "IBGE_CONFIRMA")
    add(p == "HIBRIDO", 1, "CNAE_HIBRIDO")
    add(papel == "MATRIZ_COM_REDE", 1, "MATRIZ_COM_REDE")
    # F-29 — só pontua/penaliza quando a data foi lida de fato
    add(df["FLAG_ESTABELECIDO"].to_numpy(dtype=bool) & data_ok, 1, "ESTABELECIDO")
    add(df["FLAG_RECENTE"].to_numpy(dtype=bool) & data_ok, -1, "RECENTE")
    # F-28 — capital só conta quando foi parseado com sucesso
    add((df["CAPITAL_NUM"].to_numpy() >= 100_000) & cap_ok, 1, "CAP_EXPRESSIVO")
    add((papel == "UNIDADE_UNICA") & ~np.isin(p, ["PRESENCIAL", "HIBRIDO"]), -1, "UNIDADE_UNICA_SEM_PRESENCIAL")

    score = np.clip(score, 0, 12).astype(int)
    conf = np.select([score >= 8, score >= 6, score >= 4, score >= 2],
                     ["MUITO_ALTA", "ALTA", "MEDIA", "BAIXA"], default="MUITO_BAIXA")
    texto = np.array([" | ".join(m) if m else "SEM_SINAIS" for m in motivos], dtype=object)
    # marca explicitamente quando um sinal ficou de fora por dado ausente
    faltas = []
    for i in range(n):
        f = []
        if not cap_ok[i]:
            f.append("CAPITAL_NAO_AVALIADO")
        if not data_ok[i]:
            f.append("DATA_NAO_AVALIADA")
        faltas.append(" | ".join(f))
    return pd.DataFrame({"SCORE_ESTRUTURA": score, "CONFIANCA_ESTRUTURA": conf,
                         "SCORE_ESTRUTURA_MOTIVOS": texto,
                         "SCORE_ESTRUTURA_NAO_AVALIADO": faltas}, index=df.index)


# ---------------------------------------------------------------------------
# Perfil do endereço e rota de tratamento (F-07)
# ---------------------------------------------------------------------------
def address_profile_vec(df: pd.DataFrame) -> pd.DataFrame:
    ind = pd.to_numeric(df["IBGE_IND_ESTAB"], errors="coerce")
    n_estab = pd.to_numeric(df["IBGE_N_ESTAB"], errors="coerce")
    n_dom = pd.to_numeric(df["IBGE_N_DOMICILIO"], errors="coerce")
    tem_match = df["LATITUDE"].notna()

    perfil = np.select(
        [~tem_match,
         ind.isin([1]), ind.isin([2]), ind.isin([3]), ind.isin([4]),
         n_estab.fillna(0) > 0,
         n_dom.fillna(0) > 0],
        ["SEM_MATCH", IND_ESTAB_LABEL[1], IND_ESTAB_LABEL[2], IND_ESTAB_LABEL[3],
         IND_ESTAB_LABEL[4], "ESTAB_SEM_INDICADOR", "SOMENTE_DOMICILIO"],
        default="INDETERMINADO",
    )
    # A rota vem do indicador oficial, nunca da contagem bruta de registros.
    rota = np.select(
        [~tem_match,
         ind.isin([1]),
         ind.isin([2, 3, 4]),
         n_estab.fillna(0) > 1,
         n_estab.fillna(0) == 1,
         n_dom.fillna(0) > 0],
        ["SEM_ROTA", "RECLASSIFICACAO_1_1", "INDIVIDUALIZACAO_MULTI",
         "INDIVIDUALIZACAO_MULTI", "RECLASSIFICACAO_1_1", "VERIFICAR_CAMPO"],
        default="VERIFICAR_CAMPO",
    )
    misto = (n_estab.fillna(0) > 0) & (n_dom.fillna(0) > 0)
    return pd.DataFrame({
        "XFERA_PERFIL_ENDERECO": perfil,
        "XFERA_ROTA_TRATAMENTO": rota,
        "XFERA_ENDERECO_MISTO": misto.fillna(False),
    }, index=df.index)


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------
PERFIS_PONTO = {"COMERCIO_ATENDIMENTO", "COMERCIO_EVENTUAL", "PONTO_COMERCIAL_OUTRO", "PRESENCIAL_SEM_PUBLICO"}


def apply_business_rules(df: pd.DataFrame, dsc_store, cutoff: pd.Timestamp,
                         existence_threshold: int, accept_invalid_cnpj: bool) -> pd.DataFrame:
    out = classify_company(classify_profile(df))
    out = pd.concat([out, validate_name_vec(out, dsc_store, existence_threshold)], axis=1)
    out = pd.concat([out, existence_score_vec(out, cutoff)], axis=1)
    out = pd.concat([out, address_profile_vec(out)], axis=1)

    comp = out["COMPLEMENTO_TIPO"].to_numpy()
    p = out["PERFIL_ESTRUTURA"].to_numpy()
    atend = out["ATEND_PUBLICO"].to_numpy(dtype=bool)
    exige = out["CNAE_EXIGE_ESTRUTURA"].to_numpy(dtype=bool)

    resid = (comp == "RESIDENCIAL") & ~exige
    ponto = (comp == "COMERCIAL") | exige
    out["PERFIL_COMERCIAL"] = np.select(
        [resid,
         ponto & (p == "PRESENCIAL") & atend,
         ponto & (p == "PRESENCIAL"),
         ponto,
         (p == "PRESENCIAL") & atend,
         (p == "PRESENCIAL"),
         (p == "HIBRIDO") & atend],
        ["RESIDENCIAL_NAO_COMERCIAL", "COMERCIO_ATENDIMENTO", "PRESENCIAL_SEM_PUBLICO",
         "PONTO_COMERCIAL_OUTRO", "COMERCIO_ATENDIMENTO", "PRESENCIAL_SEM_PUBLICO",
         "COMERCIO_EVENTUAL"],
        default="NAO_COMERCIAL",
    )

    logr_ok = out["LOGR_STATUS"].eq("VALIDO").to_numpy()
    num_ok = (out["NUM_INT"].to_numpy() > 0)
    cidade_ok = (out["municipio"].fillna("").str.strip().ne("") & out["uf"].fillna("").str.strip().ne("")).to_numpy()
    cep_ok = out["CEP_STATUS"].isin(["VALIDO", "AJUSTADO_ZFILL"]).to_numpy()
    out["APTIDAO_CRUZAMENTO"] = np.select(
        [out["LATITUDE"].notna().to_numpy(),
         logr_ok & num_ok & cidade_ok,
         logr_ok & cidade_ok,
         cep_ok],
        ["APTO_COORDENADA", "APTO_ENDERECO_COMPLETO", "APTO_ENDERECO_PARCIAL", "APTO_CEP_APENAS"],
        default="NAO_APTO",
    )
    out["STATUS_GEOCODIFICACAO"] = np.where(out["LATITUDE"].notna(), "GEOCODIFICADO", "PENDENTE_OUTRA_FONTE")

    cnpj_ok = out["CNPJ_VALIDO"].to_numpy(dtype=bool) | bool(accept_invalid_cnpj)
    ativa = (out["SITUACAO_NORM"].to_numpy() == "ATIVA")
    tem_ponto = np.isin(out["PERFIL_COMERCIAL"].to_numpy(), list(PERFIS_PONTO))
    out["POTENCIAL_CRUZAMENTO"] = cnpj_ok & ativa & tem_ponto

    motivo = np.full(len(out), "", dtype=object)
    m = ~cnpj_ok
    motivo[m] = "CNPJ_INVALIDO_" + out.loc[m, "CNPJ_STATUS"].astype(str)
    m2 = cnpj_ok & ~ativa
    motivo[m2] = "NAO_ATIVA_" + out.loc[m2, "SITUACAO_NORM"].astype(str)
    m3 = cnpj_ok & ativa & ~tem_ponto
    partes = np.full(len(out), "", dtype=object)
    for cond, txt in [
        (out["PERFIL_COMERCIAL"].to_numpy() == "RESIDENCIAL_NAO_COMERCIAL", "COMPLEMENTO_RESIDENCIAL"),
        (p == "PAPEL", "ATIVIDADE_PAPEL_HOME_OFFICE"),
        (p == "MOVEL", "ATIVIDADE_MOVEL_ITINERANTE"),
        (p == "INCERTO", "CNAE_NAO_CLASSIFICADO"),
    ]:
        sel = m3 & cond
        partes[sel] = np.where(partes[sel] == "", txt, partes[sel] + " + " + txt)
    partes[m3 & (partes == "")] = "SEM_ATENDIMENTO_PRESENCIAL"
    motivo[m3] = partes[m3]
    out["MOTIVO_SEM_CRUZAMENTO"] = motivo

    out = pd.concat([out, score_structure_vec(out)], axis=1)
    return out
