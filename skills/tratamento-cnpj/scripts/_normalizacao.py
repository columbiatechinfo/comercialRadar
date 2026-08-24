#!/usr/bin/env python3
"""Normalização e parsing determinísticos — v2.2.

Mudanças sobre a v2.1:
- F-26  CNPJ alfanumérico (IN RFB 2.229/2024, produção desde 31/07/2026):
        aceita A-Z nas 12 primeiras posições, DV por módulo 11 com ord(c)-48.
- F-14  '0', '00' e '000' passam a ser o mesmo status (ZERO); S/N é só literal S/N.
- F-12  normalize_logradouro recebe tipo_hint também do lado CNPJ.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------
def deaccent(value: Any) -> str:
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")


def slug_header(value: Any) -> str:
    text = deaccent(value).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def strip_excel_numeric_artifact(value: Any) -> str:
    return re.sub(r"\.0+$", "", clean_text(value))


def digits(value: Any) -> str:
    return re.sub(r"\D", "", strip_excel_numeric_artifact(value))


def normalize_nome(value: Any) -> str:
    s = deaccent(clean_text(value)).upper()
    s = re.sub(r"\b(LTDA|ME|EPP|EIRELI|S\s*A|SA|CIA|EI|MEI)\b", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# CEP
# ---------------------------------------------------------------------------
def normalize_cep(value: Any) -> tuple[str, str]:
    ds = digits(strip_excel_numeric_artifact(value))
    if len(ds) == 8:
        return ds, "VALIDO"
    if len(ds) == 7:
        return ds.zfill(8), "AJUSTADO_ZFILL"
    if not ds:
        return "", "AUSENTE"
    return ds[:8], "INVALIDO_TAMANHO"


# ---------------------------------------------------------------------------
# CNPJ — numérico e alfanumérico (F-26)
# ---------------------------------------------------------------------------
_CNPJ_ALNUM_RE = re.compile(r"[^0-9A-Z]")
_PESO_D1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESO_D2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def _valor_caractere(ch: str) -> int:
    """Regra RFB: valor decimal ASCII do caractere menos 48. '0'->0 ... 'A'->17 ... 'Z'->42."""
    return ord(ch) - 48


def _dv(base: str, pesos: tuple[int, ...]) -> str:
    total = sum(_valor_caractere(c) * w for c, w in zip(base, pesos))
    rem = total % 11
    return "0" if rem < 2 else str(11 - rem)


def normalize_cnpj(value: Any) -> tuple[str, bool, str, str]:
    """Devolve (cnpj_norm, valido, status, formato).

    formato ∈ {NUMERICO, ALFANUMERICO, INDEFINIDO}. As duas últimas posições são
    sempre numéricas em ambos os formatos.
    """
    raw = _CNPJ_ALNUM_RE.sub("", strip_excel_numeric_artifact(value).upper())
    ajustado = False

    if len(raw) == 13:
        raw = raw.zfill(14)
        ajustado = True

    formato = "ALFANUMERICO" if any(c.isalpha() for c in raw) else "NUMERICO"

    if len(raw) != 14:
        return raw, False, "TAMANHO_INVALIDO", "INDEFINIDO" if not raw else formato
    if not raw[-2:].isdigit():
        return raw, False, "DV_NAO_NUMERICO", formato
    if len(set(raw)) == 1:
        return raw, False, "DIGITOS_REPETIDOS", formato

    d1 = _dv(raw[:12], _PESO_D1)
    d2 = _dv(raw[:12] + d1, _PESO_D2)
    ok = raw[-2:] == d1 + d2
    if not ok:
        return raw, False, "DIGITO_VERIFICADOR_INVALIDO", formato
    return raw, True, ("VALIDO_AJUSTADO_ZFILL" if ajustado else "VALIDO"), formato


def cnpj_raiz(cnpj_norm: str) -> str:
    return cnpj_norm[:8] if len(cnpj_norm) == 14 else ""


# ---------------------------------------------------------------------------
# Dinheiro e data
# ---------------------------------------------------------------------------
def parse_money_br(value: Any) -> tuple[float, str]:
    raw = clean_text(value)
    if not raw:
        return 0.0, "AUSENTE_ASSUMIDO_ZERO"
    s = re.sub(r"[^0-9,.-]", "", raw)
    if not s or s in {"-", ".", ","}:
        return 0.0, "INVALIDO_ASSUMIDO_ZERO"
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        tail = s.rsplit(",", 1)[1]
        s = s.replace(".", "")
        s = s.replace(",", "." if len(tail) in {1, 2} else "")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:
            s = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3:
            s = "".join(parts)
    try:
        return max(float(s), 0.0), "VALIDO"
    except ValueError:
        return 0.0, "INVALIDO_ASSUMIDO_ZERO"


def parse_date_br(value: Any) -> tuple[Any, str]:
    raw = strip_excel_numeric_artifact(value)
    if not raw:
        return pd.NaT, "AUSENTE"
    if re.fullmatch(r"\d{5}", raw):
        serial = int(raw)
        if 20_000 <= serial <= 80_000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D"), "VALIDO_SERIAL_EXCEL"
    if re.fullmatch(r"\d{8}", raw):  # AAAAMMDD da RFB
        dt = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
        if pd.notna(dt):
            return dt, "VALIDO_AAAAMMDD"
    dt = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    return (dt, "VALIDO") if pd.notna(dt) else (pd.NaT, "INVALIDO")


# ---------------------------------------------------------------------------
# Situação, tipo, CNAE
# ---------------------------------------------------------------------------
_STATUS_ALIAS = {
    "ATIVA": "ATIVA", "02": "ATIVA", "2": "ATIVA",
    "BAIXADA": "BAIXADA", "08": "BAIXADA", "8": "BAIXADA",
    "INAPTA": "INAPTA", "04": "INAPTA", "4": "INAPTA",
    "SUSPENSA": "SUSPENSA", "03": "SUSPENSA", "3": "SUSPENSA",
    "NULA": "NULA", "01": "NULA", "1": "NULA",
}


def normalize_status(value: Any) -> str:
    s = deaccent(clean_text(value)).upper()
    return _STATUS_ALIAS.get(s, s or "DESCONHECIDA")


def normalize_tipo_empresa(value: Any) -> str:
    s = deaccent(strip_excel_numeric_artifact(value)).upper()
    if s in {"2", "FILIAL"} or "FILIAL" in s:
        return "FILIAL"
    if s in {"1", "MATRIZ"} or "MATRIZ" in s:
        return "MATRIZ"
    return "DESCONHECIDO"


def normalize_cnae(value: Any) -> tuple[str, str, str]:
    ds = digits(value)
    if len(ds) < 2:
        return ds, "", "INVALIDO"
    ds7 = ds[:7].zfill(7) if len(ds) <= 7 else ds[:7]
    return ds7, ds7[:2], f"{ds7[:4]}-{ds7[4]}/{ds7[5:]}"


# ---------------------------------------------------------------------------
# Logradouro
# ---------------------------------------------------------------------------
TIPO_CANON = {
    "R": "RUA", "RUA": "RUA",
    "AV": "AVENIDA", "AVN": "AVENIDA", "AVENIDA": "AVENIDA",
    "TV": "TRAVESSA", "TRAV": "TRAVESSA", "TRV": "TRAVESSA", "TRAVESSA": "TRAVESSA",
    "EST": "ESTRADA", "ESTR": "ESTRADA", "ESTRADA": "ESTRADA",
    "ROD": "RODOVIA", "RODOVIA": "RODOVIA",
    "AL": "ALAMEDA", "ALAMEDA": "ALAMEDA",
    "PC": "PRACA", "PCA": "PRACA", "PRACA": "PRACA",
    "VIELA": "VIELA", "BECO": "BECO", "VIA": "VIA", "VL": "VILA", "VILA": "VILA",
    "PARQUE": "PARQUE", "PQ": "PARQUE", "DISTRITO": "DISTRITO", "LINHA": "LINHA",
    "SERVIDAO": "SERVIDAO", "CONDOMINIO": "CONDOMINIO", "COND": "CONDOMINIO",
    "LOTEAMENTO": "LOTEAMENTO", "LOT": "LOTEAMENTO", "PASSAGEM": "PASSAGEM",
    "QUADRA": "QUADRA", "QD": "QUADRA", "SETOR": "SETOR", "ST": "SETOR",
    "LARGO": "LARGO", "LGO": "LARGO", "JARDIM": "JARDIM", "JD": "JARDIM",
    "NUCLEO": "NUCLEO", "CHACARA": "CHACARA", "SITIO": "SITIO", "FAZENDA": "FAZENDA",
}

TITULO_EXPAND = {
    "DR": "DOUTOR", "DRA": "DOUTORA", "ENG": "ENGENHEIRO",
    "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "COR": "CORONEL",
    "CEL": "CORONEL", "PE": "PADRE", "FR": "FREI", "DEP": "DEPUTADO",
    "VER": "VEREADOR", "MAJ": "MAJOR", "CAP": "CAPITAO", "TTE": "TENENTE",
    "GEN": "GENERAL", "GOV": "GOVERNADOR", "PRES": "PRESIDENTE",
    "BRIG": "BRIGADEIRO", "ALM": "ALMIRANTE", "VISC": "VISCONDE",
    "STO": "SANTO", "STA": "SANTA", "NSA": "NOSSA", "SR": "SENHOR", "SRA": "SENHORA",
}

NUM_EXTENSO = {
    "UM": "1", "DOIS": "2", "TRES": "3", "QUATRO": "4", "CINCO": "5",
    "SEIS": "6", "SETE": "7", "OITO": "8", "NOVE": "9", "DEZ": "10",
    "ONZE": "11", "DOZE": "12", "TREZE": "13", "QUATORZE": "14",
    "CATORZE": "14", "QUINZE": "15", "DEZESSEIS": "16",
    "DEZESSETE": "17", "DEZOITO": "18", "DEZENOVE": "19", "VINTE": "20",
}

NUCLEO_STOPWORDS = {
    "DE", "DA", "DO", "DAS", "DOS", "E",
    "DOUTOR", "DOUTORA", "PROFESSOR", "PROFESSORA", "CORONEL", "PADRE",
    "GENERAL", "CAPITAO", "MAJOR", "PRESIDENTE", "GOVERNADOR", "DEPUTADO",
    "VEREADOR", "VISCONDE", "FREI", "TENENTE", "BRIGADEIRO", "ALMIRANTE",
    "SENHOR", "SENHORA",
}


@dataclass(frozen=True)
class Logradouro:
    tipo: str
    base: str
    full: str
    nucleo: str
    status: str
    n_tokens: int


def normalize_logradouro(value: Any, tipo_hint: Any = "") -> Logradouro:
    raw = deaccent(clean_text(value)).upper()
    raw = re.sub(r"[^A-Z0-9 ]", " ", raw)
    words = [w for w in raw.split() if w]

    tipo = ""
    if tipo_hint:
        hint = deaccent(clean_text(tipo_hint)).upper().replace(".", "")
        tipo = TIPO_CANON.get(hint, "")

    if tipo:
        # O campo de tipo é AUTORITATIVO. Só removemos a primeira palavra do nome
        # quando ela repete o mesmo tipo — do contrário 'RUA' + 'VILA NOVA' viraria
        # tipo=VILA, base=NOVA, e deixaria de casar com 'RUA VILA NOVA' do cadastro.
        if words and TIPO_CANON.get(words[0]) == tipo:
            words = words[1:]
    elif words and words[0] in TIPO_CANON:
        tipo = TIPO_CANON[words[0]]
        words = words[1:]

    words = [TITULO_EXPAND.get(w, w) for w in words]
    words = [NUM_EXTENSO.get(w, w) for w in words]
    base = " ".join(words).strip()
    full = f"{tipo} {base}".strip()
    nucleo = " ".join(w for w in words if w not in NUCLEO_STOPWORDS)

    if not base:
        status = "AUSENTE"
    elif len(base) < 3:
        status = "CURTO"
    else:
        status = "VALIDO"
    return Logradouro(tipo, base, full, nucleo, status, len(words))


# ---------------------------------------------------------------------------
# Número — F-14 e F-11
# ---------------------------------------------------------------------------
# 'S/N' e variantes textuais. O literal '0' NÃO entra aqui (vira ZERO).
SN_RE = re.compile(r"^\s*(S\s*[/\.\-]?\s*N(?:UM|RO|\.)?|SN|SEM\s+N(?:UMERO|RO|\.)?|SNUM|SNO)\s*$", re.IGNORECASE)
_NUM_RE = re.compile(r"^\s*(?:N(?:O|RO|UMERO|\.)?\s*)?(\d{1,9})(.*)$")


def normalize_sufixo(value: Any) -> str:
    """Sufixo/modificador canônico do número: 'A', 'B', 'FUNDOS', 'FRENTE'..."""
    s = deaccent(clean_text(value)).upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    if s in {"SN", "SNUM", "SEMNUMERO"}:
        return ""
    return s[:20]


def parse_numero(value: Any) -> tuple[int, str, str]:
    raw = deaccent(clean_text(value)).upper()
    if not raw:
        return 0, "AUSENTE", ""
    if SN_RE.match(raw):
        return 0, "SEM_NUMERO", ""
    m = _NUM_RE.match(raw)
    if not m:
        return 0, "INVALIDO", ""
    num = int(m.group(1))
    sufixo = normalize_sufixo(m.group(2))
    if num <= 0:
        # Convenção da fonte: tanto o CNEFE quanto a RFB usam 0 para "sem número".
        # '0', '00' e '000' passam a ter o MESMO tratamento (a v2.1 divergia), com
        # status próprio para permanecer auditável. Zero nunca casa com zero como
        # número — segue para o centróide do logradouro, igual a S/N.
        return 0, "SEM_NUMERO_ZERO", sufixo
    return num, ("VALIDO_COM_SUFIXO" if sufixo else "VALIDO"), sufixo
