#!/usr/bin/env python3
"""Pipeline auditável de enriquecimento CNPJ × CNEFE/IBGE.

Princípios:
- nenhum match usa CEP isolado;
- nenhum registro de entrada é descartado;
- normalização e parsing são explícitos e auditáveis;
- fuzzy exige limiar e margem de unicidade;
- coordenadas duplicadas escolhem a melhor qualidade de forma determinística;
- evidência CNEFE é separada da decisão de potencial comercial;
- mesma entrada + mesmos parâmetros = mesma saída.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import re
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    from rapidfuzz import fuzz
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Dependência ausente: rapidfuzz. Instale com: pip install rapidfuzz") from exc


VERSION = "2.1.0"
DEFAULT_CNEFE_CUTOFF = "2022-08-01"
DEFAULT_DELTA_PROXIMO = 20
DEFAULT_DELTA_AMPLO = 100
DEFAULT_FUZZY_THR = 90
DEFAULT_FUZZY_MARGIN = 5
DEFAULT_PARITY_PENALTY = 3
ESP_COMERCIAL = {3, 4, 5, 6, 7}
BRASIL_BBOX = (-35.0, 6.5, -75.0, -32.0)  # lat_min, lat_max, lon_min, lon_max


# ---------------------------------------------------------------------------
# Esquemas e aliases
# ---------------------------------------------------------------------------
CNPJ_ALIASES: dict[str, list[str]] = {
    "cnpj_completo": ["cnpj_completo", "cnpj", "nr_cnpj", "numero_cnpj"],
    "razao_social": ["razao_social", "nome_empresarial", "razao"],
    "nome_fantasia": ["nome_fantasia", "fantasia"],
    "tipo": ["tipo", "identificador_matriz_filial", "matriz_filial"],
    "situacao": ["situacao", "situacao_cadastral", "descricao_situacao_cadastral"],
    "data_inicio_atividade": ["data_inicio_atividade", "data_abertura", "inicio_atividade"],
    "cnae_principal_cod": ["cnae_principal_cod", "cnae_fiscal_principal", "cnae_principal", "cnae"],
    "cnae_principal_descricao": ["cnae_principal_descricao", "descricao_cnae", "cnae_descricao"],
    "natureza_juridica": ["natureza_juridica", "codigo_natureza_juridica", "nat_jur"],
    "porte": ["porte", "porte_empresa", "descricao_porte"],
    "capital_social": ["capital_social", "capital"],
    "logradouro": ["logradouro", "nome_logradouro", "endereco_logradouro"],
    "numero": ["numero", "numero_endereco", "num_endereco", "nro"],
    "complemento": ["complemento", "complemento_endereco"],
    "bairro": ["bairro", "nome_bairro"],
    "municipio": ["municipio", "nome_municipio", "cidade"],
    "uf": ["uf", "sigla_uf", "estado"],
    "cep": ["cep", "codigo_cep"],
}

CNPJ_REQUIRED = {
    "cnpj_completo",
    "situacao",
    "logradouro",
    "numero",
    "cep",
    "cnae_principal_cod",
}

CNEFE_REQUIRED = {
    "COD_UNICO_ENDERECO",
    "NOM_TIPO_SEGLOGR",
    "NOM_TITULO_SEGLOGR",
    "NOM_SEGLOGR",
    "NUM_ENDERECO",
    "CEP",
    "NV_GEO_COORD",
    "LATITUDE",
    "LONGITUDE",
    "COD_ESPECIE",
    "DSC_ESTABELECIMENTO",
}

OPTIONAL_DEFAULTS = {
    "razao_social": "",
    "nome_fantasia": "",
    "tipo": "",
    "data_inicio_atividade": "",
    "cnae_principal_descricao": "",
    "natureza_juridica": "",
    "porte": "",
    "capital_social": "",
    "complemento": "",
    "bairro": "",
    "municipio": "",
    "uf": "",
}


# ---------------------------------------------------------------------------
# Normalização genérica
# ---------------------------------------------------------------------------
def deaccent(value: Any) -> str:
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )


def slug_header(value: Any) -> str:
    text = deaccent(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def strip_excel_numeric_artifact(value: Any) -> str:
    raw = clean_text(value)
    return re.sub(r"\.0+$", "", raw)


def digits(value: Any) -> str:
    return re.sub(r"\D", "", strip_excel_numeric_artifact(value))


def normalize_cep(value: Any) -> tuple[str, str]:
    raw = strip_excel_numeric_artifact(value)
    ds = digits(raw)
    if len(ds) == 8:
        return ds, "VALIDO"
    if len(ds) == 7:
        return ds.zfill(8), "AJUSTADO_ZFILL"
    if not ds:
        return "", "AUSENTE"
    return ds[:8], "INVALIDO_TAMANHO"


def normalize_cnpj(value: Any) -> tuple[str, bool, str]:
    ds = digits(value)
    adjusted = False
    if len(ds) == 13:
        ds = ds.zfill(14)
        adjusted = True
    if len(ds) != 14:
        return ds, False, "TAMANHO_INVALIDO"
    if len(set(ds)) == 1:
        return ds, False, "DIGITOS_REPETIDOS"

    def calc(base: str, weights: list[int]) -> str:
        total = sum(int(d) * w for d, w in zip(base, weights))
        rem = total % 11
        return "0" if rem < 2 else str(11 - rem)

    d1 = calc(ds[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = calc(ds[:12] + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    ok = ds[-2:] == d1 + d2
    if ok and adjusted:
        return ds, True, "VALIDO_AJUSTADO_ZFILL"
    return ds, ok, "VALIDO" if ok else "DIGITO_VERIFICADOR_INVALIDO"


def parse_money_br(value: Any) -> tuple[float, str]:
    raw = clean_text(value)
    if not raw:
        return 0.0, "AUSENTE_ASSUMIDO_ZERO"
    s = re.sub(r"[^0-9,.-]", "", raw)
    if not s or s in {"-", ".", ","}:
        return 0.0, "INVALIDO_ASSUMIDO_ZERO"

    # Decide o separador decimal pelo último separador, preservando milhares.
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
        elif len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) >= 1:
            # Em base brasileira, 1.000 normalmente é milhar.
            s = "".join(parts)
    try:
        val = float(s)
        return max(val, 0.0), "VALIDO"
    except ValueError:
        return 0.0, "INVALIDO_ASSUMIDO_ZERO"


def parse_date_br(value: Any) -> tuple[pd.Timestamp | pd.NaT, str]:
    raw = strip_excel_numeric_artifact(value)
    if not raw:
        return pd.NaT, "AUSENTE"
    if re.fullmatch(r"\d{5}", raw):
        serial = int(raw)
        if 20_000 <= serial <= 80_000:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(serial, unit="D"), "VALIDO_SERIAL_EXCEL"
    dt = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    return (dt, "VALIDO") if pd.notna(dt) else (pd.NaT, "INVALIDO")


def normalize_status(value: Any) -> str:
    s = deaccent(clean_text(value)).upper()
    aliases = {
        "ATIVA": "ATIVA",
        "02": "ATIVA", "2": "ATIVA",
        "BAIXADA": "BAIXADA",
        "08": "BAIXADA", "8": "BAIXADA",
        "INAPTA": "INAPTA",
        "04": "INAPTA", "4": "INAPTA",
        "SUSPENSA": "SUSPENSA",
        "03": "SUSPENSA", "3": "SUSPENSA",
        "NULA": "NULA",
        "01": "NULA", "1": "NULA",
    }
    return aliases.get(s, s or "DESCONHECIDA")


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
    formatted = f"{ds7[:4]}-{ds7[4]}/{ds7[5:]}"
    return ds7, ds7[:2], formatted


# ---------------------------------------------------------------------------
# Endereço
# ---------------------------------------------------------------------------
TIPO_CANON = {
    "R": "RUA", "RUA": "RUA",
    "AV": "AVENIDA", "AVENIDA": "AVENIDA",
    "TV": "TRAVESSA", "TRAV": "TRAVESSA", "TRAVESSA": "TRAVESSA",
    "EST": "ESTRADA", "ESTRADA": "ESTRADA",
    "ROD": "RODOVIA", "RODOVIA": "RODOVIA",
    "AL": "ALAMEDA", "ALAMEDA": "ALAMEDA",
    "PCA": "PRACA", "PRACA": "PRACA",
    "VIELA": "VIELA", "BECO": "BECO", "VIA": "VIA",
    "PARQUE": "PARQUE", "DISTRITO": "DISTRITO", "LINHA": "LINHA",
    "SERVIDAO": "SERVIDAO", "CONDOMINIO": "CONDOMINIO",
    "LOTEAMENTO": "LOTEAMENTO", "PASSAGEM": "PASSAGEM",
    "QUADRA": "QUADRA", "SETOR": "SETOR",
}

TITULO_EXPAND = {
    "DR": "DOUTOR", "DRA": "DOUTORA", "ENG": "ENGENHEIRO",
    "PROF": "PROFESSOR", "PROFA": "PROFESSORA", "COR": "CORONEL",
    "CEL": "CORONEL", "PE": "PADRE", "FR": "FREI", "DEP": "DEPUTADO",
    "VER": "VEREADOR", "MAJ": "MAJOR", "CAP": "CAPITAO", "TTE": "TENENTE",
    "GEN": "GENERAL", "GOV": "GOVERNADOR", "PRES": "PRESIDENTE",
    "BRIG": "BRIGADEIRO", "ALM": "ALMIRANTE", "VISC": "VISCONDE",
    "STO": "SANTO", "STA": "SANTA", "NSA": "NOSSA", "SR": "SENHOR",
    "SRA": "SENHORA",
}

NUM_EXTENSO = {
    "UM": "1", "DOIS": "2", "TRES": "3", "QUATRO": "4", "CINCO": "5",
    "SEIS": "6", "SETE": "7", "OITO": "8", "NOVE": "9", "DEZ": "10",
    "ONZE": "11", "DOZE": "12", "TREZE": "13", "QUATORZE": "14",
    "CATORZE": "14", "QUINZE": "15", "DEZESSEIS": "16",
    "DEZESSETE": "17", "DEZOITO": "18", "DEZENOVE": "19", "VINTE": "20",
}

# Mantém termos semânticos como SAO/SANTA; remove só conectivos e títulos profissionais.
NUCLEO_STOPWORDS = {
    "DE", "DA", "DO", "DAS", "DOS", "E",
    "DOUTOR", "DOUTORA", "PROFESSOR", "PROFESSORA", "CORONEL", "PADRE",
    "GENERAL", "CAPITAO", "MAJOR", "PRESIDENTE", "GOVERNADOR", "DEPUTADO",
    "VEREADOR", "VISCONDE", "FREI", "TENENTE", "BRIGADEIRO", "ALMIRANTE",
    "SENHOR", "SENHORA",
}

SN_RE = re.compile(
    r"^\s*(S\s*/?\s*N|SN|SEM\s+NUMERO|SEM\s+NRO|SNUM|SNO|0)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Logradouro:
    tipo: str
    base: str
    full: str
    nucleo: str
    status: str


def normalize_logradouro(value: Any, tipo_hint: Any = "") -> Logradouro:
    raw = deaccent(clean_text(value)).upper()
    raw = re.sub(r"[^A-Z0-9 ]", " ", raw)
    words = [w for w in raw.split() if w]

    tipo = ""
    if tipo_hint:
        hint = deaccent(clean_text(tipo_hint)).upper().replace(".", "")
        tipo = TIPO_CANON.get(hint, "")
    if words:
        first = words[0].replace(".", "")
        if first in TIPO_CANON:
            tipo = TIPO_CANON[first]
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
    return Logradouro(tipo, base, full, nucleo, status)


def parse_numero(value: Any) -> tuple[int, str, str]:
    raw = deaccent(clean_text(value)).upper()
    if not raw:
        return 0, "AUSENTE", ""
    if SN_RE.match(raw):
        return 0, "SEM_NUMERO", ""
    # Aceita 123, 123A, 123-B, 123/125; preserva sufixo para auditoria.
    m = re.match(r"^\s*(?:N(?:O|RO|UMERO)?\s*)?(\d{1,9})(.*)$", raw)
    if not m:
        return 0, "INVALIDO", ""
    num = int(m.group(1))
    suffix = re.sub(r"\s+", "", m.group(2) or "")[:20]
    if num <= 0:
        return 0, "ZERO", suffix
    status = "VALIDO" if not suffix else "VALIDO_COM_SUFIXO"
    return num, status, suffix


# ---------------------------------------------------------------------------
# Leitura robusta
# ---------------------------------------------------------------------------
def apply_aliases(df: pd.DataFrame, aliases: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, str]]:
    normalized = {slug_header(c): c for c in df.columns}
    rename: dict[str, str] = {}
    used_original: set[str] = set()
    for canonical, options in aliases.items():
        found = [normalized[o] for o in options if o in normalized]
        if len(found) > 1:
            raise ValueError(f"Colunas ambíguas para '{canonical}': {found}")
        if found:
            original = found[0]
            if original in used_original:
                raise ValueError(f"A coluna '{original}' foi mapeada para mais de um campo")
            used_original.add(original)
            rename[original] = canonical
    return df.rename(columns=rename), rename


def read_cnpj(path: Path, sheet: str | int | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Base CNPJ não encontrada: {path}")
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        df = pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, dtype=str, engine="openpyxl")
    elif suffix in {".csv", ".txt"}:
        df = read_csv_flexible(path.read_bytes(), source_name=path.name)
    else:
        raise ValueError("Base CNPJ deve ser .xlsx, .xlsm, .csv ou .txt")

    df, mapping = apply_aliases(df, CNPJ_ALIASES)
    missing = sorted(CNPJ_REQUIRED - set(df.columns))
    if missing:
        raise ValueError(f"Base CNPJ sem colunas obrigatórias: {missing}")
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    return df, {"mapeamento_colunas": mapping, "linhas": len(df)}


def detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def read_csv_flexible(data: bytes, source_name: str = "arquivo.csv") -> pd.DataFrame:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
            continue
        sep = detect_delimiter(text[:20_000])
        try:
            return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, low_memory=False)
        except Exception as exc:  # pragma: no cover - depende do arquivo
            errors.append(f"{encoding}/{sep}: {exc}")
    raise ValueError(f"Não foi possível ler {source_name}. Tentativas: {' | '.join(errors)}")


def read_cnefe(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"CNEFE não encontrado: {path}")
    candidates: list[tuple[str, pd.DataFrame]] = []

    def inspect(name: str, raw: bytes) -> None:
        try:
            df = read_csv_flexible(raw, source_name=name)
        except Exception:
            return
        upper = {deaccent(c).upper().strip().lstrip("\ufeff"): c for c in df.columns}
        if CNEFE_REQUIRED.issubset(upper):
            df = df.rename(columns={upper[c]: c for c in CNEFE_REQUIRED})
            candidates.append((name, df))

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".csv", ".txt")) and not name.endswith("/"):
                    inspect(name, zf.read(name))
    else:
        inspect(path.name, path.read_bytes())

    if not candidates:
        raise ValueError("Nenhum CSV/TXT do CNEFE contém todas as colunas obrigatórias")
    if len(candidates) > 1:
        names = [name for name, _ in candidates]
        raise ValueError(f"Mais de um arquivo CNEFE válido encontrado; selecione um ZIP unitário: {names}")
    name, df = candidates[0]
    return df, {"arquivo_interno": name, "linhas": len(df)}


# ---------------------------------------------------------------------------
# Preparação das bases
# ---------------------------------------------------------------------------
def valid_coord(lat: Any, lon: Any) -> bool:
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= latf <= 90 and -180 <= lonf <= 180 and not (latf == 0 and lonf == 0)


def in_brazil_bbox(lat: Any, lon: Any) -> bool:
    if not valid_coord(lat, lon):
        return False
    lat_min, lat_max, lon_min, lon_max = BRASIL_BBOX
    return lat_min <= float(lat) <= lat_max and lon_min <= float(lon) <= lon_max


def prepare_cnefe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    out = df.copy()
    out["COD_ESPECIE"] = pd.to_numeric(out["COD_ESPECIE"], errors="coerce").astype("Int64")
    out["NV_GEO_COORD"] = pd.to_numeric(out["NV_GEO_COORD"], errors="coerce").astype("Int64")
    out["LATITUDE"] = pd.to_numeric(out["LATITUDE"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["LONGITUDE"] = pd.to_numeric(out["LONGITUDE"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    out["COORD_VALIDA"] = [valid_coord(a, b) for a, b in zip(out["LATITUDE"], out["LONGITUDE"])]
    out["COORD_BRASIL"] = [in_brazil_bbox(a, b) for a, b in zip(out["LATITUDE"], out["LONGITUDE"])]

    ceps = out["CEP"].apply(normalize_cep)
    out[["CEP_NORM", "CEP_STATUS"]] = pd.DataFrame(ceps.tolist(), index=out.index)

    logs = []
    for t, tit, nome in zip(out["NOM_TIPO_SEGLOGR"], out["NOM_TITULO_SEGLOGR"], out["NOM_SEGLOGR"]):
        combined = " ".join(x for x in [clean_text(tit), clean_text(nome)] if x)
        logs.append(normalize_logradouro(combined, t))
    out["LOGR_TIPO"] = [x.tipo for x in logs]
    out["LOGR_BASE"] = [x.base for x in logs]
    out["LOGR_FULL"] = [x.full for x in logs]
    out["LOGR_NUCLEO"] = [x.nucleo for x in logs]
    out["LOGR_STATUS"] = [x.status for x in logs]

    nums = out["NUM_ENDERECO"].apply(parse_numero)
    out[["NUM_INT", "NUM_STATUS", "NUM_SUFIXO"]] = pd.DataFrame(nums.tolist(), index=out.index)

    out["DSC_ESTABELECIMENTO"] = out["DSC_ESTABELECIMENTO"].apply(clean_text)
    out["DSC_NORM"] = out["DSC_ESTABELECIMENTO"].apply(normalize_nome)
    out["_is_com"] = out["COD_ESPECIE"].isin(ESP_COMERCIAL) | out["DSC_ESTABELECIMENTO"].ne("")

    qa = {
        "cnefe_total": len(out),
        "cnefe_coord_invalida": int((~out["COORD_VALIDA"]).sum()),
        "cnefe_coord_fora_brasil": int((out["COORD_VALIDA"] & ~out["COORD_BRASIL"]).sum()),
        "cnefe_cep_invalido": 0,
        "cnefe_num_sem_numero": int((out["NUM_INT"] == 0).sum()),
    }
    qa["cnefe_cep_invalido"] = int((~out["CEP_STATUS"].isin(["VALIDO", "AJUSTADO_ZFILL"])).sum())
    return out, qa


def normalize_nome(value: Any) -> str:
    s = deaccent(clean_text(value)).upper()
    s = re.sub(r"\b(LTDA|ME|EPP|EIRELI|S\s*A|SA|CIA|EI|MEI)\b", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def prepare_cnpj(df: pd.DataFrame, reference_date: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, int]]:
    out = df.copy().reset_index(drop=True)
    out["_RID"] = np.arange(len(out), dtype=np.int64)

    cnpjs = out["cnpj_completo"].apply(normalize_cnpj)
    out[["CNPJ_NORM", "CNPJ_VALIDO", "CNPJ_STATUS"]] = pd.DataFrame(cnpjs.tolist(), index=out.index)

    ceps = out["cep"].apply(normalize_cep)
    out[["CEP_NORM", "CEP_STATUS"]] = pd.DataFrame(ceps.tolist(), index=out.index)

    logs = out["logradouro"].apply(normalize_logradouro)
    out["LOGR_TIPO"] = [x.tipo for x in logs]
    out["LOGR_BASE"] = [x.base for x in logs]
    out["LOGR_FULL"] = [x.full for x in logs]
    out["LOGR_NUCLEO"] = [x.nucleo for x in logs]
    out["LOGR_STATUS"] = [x.status for x in logs]

    nums = out["numero"].apply(parse_numero)
    out[["NUM_INT", "NUM_STATUS", "NUM_SUFIXO"]] = pd.DataFrame(nums.tolist(), index=out.index)
    out["FLAG_SN"] = out["NUM_STATUS"].eq("SEM_NUMERO")

    out["SITUACAO_NORM"] = out["situacao"].apply(normalize_status)
    out["TIPO_EMPRESA_NORM"] = out["tipo"].apply(normalize_tipo_empresa)

    cnaes = out["cnae_principal_cod"].apply(normalize_cnae)
    out[["CNAE7", "CNAE_DIV", "CNAE_FORMATADO"]] = pd.DataFrame(cnaes.tolist(), index=out.index)
    out["NAT_JUR_DIGITOS"] = out["natureza_juridica"].apply(digits)

    caps = out["capital_social"].apply(parse_money_br)
    out[["CAPITAL_NUM", "CAPITAL_STATUS"]] = pd.DataFrame(caps.tolist(), index=out.index)

    dates = out["data_inicio_atividade"].apply(parse_date_br)
    out[["DATA_ABERTURA_DT", "DATA_ABERTURA_STATUS"]] = pd.DataFrame(dates.tolist(), index=out.index)
    out["ATIVO_ANOS"] = ((reference_date - out["DATA_ABERTURA_DT"]).dt.days / 365.25).round(2)
    out.loc[out["ATIVO_ANOS"] < 0, "ATIVO_ANOS"] = 0

    out["NOME_NORM"] = out["nome_fantasia"].apply(normalize_nome)
    missing_name = out["NOME_NORM"].eq("")
    out.loc[missing_name, "NOME_NORM"] = out.loc[missing_name, "razao_social"].apply(normalize_nome)
    out["RAZAO_NORM"] = out["razao_social"].apply(normalize_nome)

    qa = {
        "cnpj_total": len(out),
        "cnpj_invalido": int((~out["CNPJ_VALIDO"]).sum()),
        "cnpj_duplicado": int(out["CNPJ_NORM"].duplicated(keep=False).sum()),
        "cnpj_cep_invalido": int((~out["CEP_STATUS"].isin(["VALIDO", "AJUSTADO_ZFILL"])).sum()),
        "cnpj_logradouro_invalido": 0,
        "cnpj_numero_invalido": int(out["NUM_STATUS"].isin(["INVALIDO", "AUSENTE", "ZERO"]).sum()),
        "cnpj_data_invalida_ou_ausente": int(out["DATA_ABERTURA_DT"].isna().sum()),
        "cnpj_capital_parse_invalido": int(out["CAPITAL_STATUS"].eq("INVALIDO_ASSUMIDO_ZERO").sum()),
    }
    qa["cnpj_logradouro_invalido"] = int((~out["LOGR_STATUS"].eq("VALIDO")).sum())
    return out, qa


# ---------------------------------------------------------------------------
# Índice CNEFE e matching
# ---------------------------------------------------------------------------
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def robust_centroid(group: pd.DataFrame) -> tuple[float, float, float]:
    coords = group.loc[group["COORD_VALIDA"], ["LATITUDE", "LONGITUDE"]].dropna()
    if coords.empty:
        return np.nan, np.nan, np.nan
    lat = float(coords["LATITUDE"].median())
    lon = float(coords["LONGITUDE"].median())
    distances = [haversine_m(lat, lon, float(a), float(b)) for a, b in coords.itertuples(index=False)]
    p95 = float(np.percentile(distances, 95)) if distances else 0.0
    return lat, lon, p95


@dataclass
class StreetEntry:
    cep: str
    tipo: str
    base: str
    full: str
    nucleo: str
    numbers: dict[int, dict[str, Any]]
    centroid: dict[str, Any]


@dataclass
class MatchConfig:
    delta_proximo: int
    delta_amplo: int
    fuzzy_thr: int
    fuzzy_margin: int
    parity_penalty: int


def build_cnefe_index(cnefe: pd.DataFrame) -> tuple[dict[str, list[StreetEntry]], pd.DataFrame]:
    valid = cnefe[
        cnefe["COORD_VALIDA"]
        & cnefe["COORD_BRASIL"]
        & cnefe["CEP_NORM"].ne("")
        & cnefe["LOGR_BASE"].ne("")
    ].copy()

    # Melhor coordenada por endereço. Menor NV é melhor; desempate estável pelo código.
    valid["NV_SORT"] = pd.to_numeric(valid["NV_GEO_COORD"], errors="coerce").fillna(99)
    valid["COD_SORT"] = valid["COD_UNICO_ENDERECO"].astype(str)
    addr = valid[valid["NUM_INT"] > 0].sort_values(
        ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT", "NV_SORT", "COD_SORT"],
        kind="mergesort",
    )
    best_addr = addr.drop_duplicates(
        ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT"], keep="first"
    )

    # Anotação comercial por endereço exato.
    grouped = cnefe.groupby(["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT"], dropna=False, sort=False)
    agg = grouped.agg(
        IBGE_FLAG_COMERCIAL=("_is_com", "max"),
        IBGE_QTD_UNIDADES=("COD_UNICO_ENDERECO", "size"),
        IBGE_ESPECIES_COD=("COD_ESPECIE", lambda s: ",".join(sorted({str(int(x)) for x in s.dropna()}))),
        IBGE_DSC_ESTAB=("DSC_ESTABELECIMENTO", lambda s: "|".join(dict.fromkeys(x for x in s if x)) or None),
        _DSC_LIST=("DSC_NORM", lambda s: list(dict.fromkeys(x for x in s if x))),
    ).reset_index()

    best_addr = best_addr.merge(
        agg,
        on=["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "NUM_INT"],
        how="left",
        validate="one_to_one",
    )

    index: dict[str, list[StreetEntry]] = {}
    street_cols = ["CEP_NORM", "LOGR_TIPO", "LOGR_BASE", "LOGR_FULL", "LOGR_NUCLEO"]
    for keys, g in valid.groupby(street_cols, sort=True, dropna=False):
        cep, tipo, base, full, nucleo = keys
        gaddr = best_addr[
            (best_addr["CEP_NORM"] == cep)
            & (best_addr["LOGR_TIPO"] == tipo)
            & (best_addr["LOGR_BASE"] == base)
        ]
        numbers: dict[int, dict[str, Any]] = {}
        for _, row in gaddr.iterrows():
            numbers[int(row["NUM_INT"])] = {
                "lat": float(row["LATITUDE"]),
                "lon": float(row["LONGITUDE"]),
                "nv": int(row["NV_GEO_COORD"]) if pd.notna(row["NV_GEO_COORD"]) else None,
                "cod": row["COD_UNICO_ENDERECO"],
                "flag_com": int(bool(row["IBGE_FLAG_COMERCIAL"])) if pd.notna(row["IBGE_FLAG_COMERCIAL"]) else 0,
                "qtd": int(row["IBGE_QTD_UNIDADES"]) if pd.notna(row["IBGE_QTD_UNIDADES"]) else 0,
                "especies": row["IBGE_ESPECIES_COD"],
                "dsc": row["IBGE_DSC_ESTAB"],
                "dsc_list": row["_DSC_LIST"] if isinstance(row["_DSC_LIST"], list) else [],
            }
        clat, clon, p95 = robust_centroid(g)
        centroid = {
            "lat": clat,
            "lon": clon,
            "nv": 4,
            "cod": None,
            "p95": p95,
        }
        index.setdefault(str(cep), []).append(
            StreetEntry(str(cep), str(tipo), str(base), str(full), str(nucleo), numbers, centroid)
        )
    return index, agg


def street_similarity(cnpj_tipo: str, cnpj_base: str, cnpj_nucleo: str, entry: StreetEntry) -> float:
    if not cnpj_base or len(cnpj_base) < 3:
        return 0.0
    score_base = fuzz.token_set_ratio(cnpj_base, entry.base)
    score_nuc = fuzz.token_sort_ratio(cnpj_nucleo, entry.nucleo) if cnpj_nucleo and entry.nucleo else 0
    score = max(score_base, score_nuc)
    if cnpj_tipo and entry.tipo and cnpj_tipo != entry.tipo:
        score -= 6
    return max(0.0, float(score))


def choose_street(
    entries: list[StreetEntry],
    tipo: str,
    base: str,
    nucleo: str,
    config: MatchConfig,
) -> tuple[StreetEntry | None, str, float, float, int]:
    if not entries or not base:
        return None, "SEM_LOGRADOURO", 0.0, 0.0, 0

    exact_type = [e for e in entries if e.base == base and e.tipo == tipo and tipo]
    if len(exact_type) == 1:
        return exact_type[0], "EXATO_TIPO", 100.0, 100.0, 1
    if len(exact_type) > 1:
        return None, "AMBIGUO_EXATO_TIPO", 100.0, 100.0, len(exact_type)

    exact_base = [e for e in entries if e.base == base]
    if len(exact_base) == 1:
        return exact_base[0], "EXATO_BASE_UNICO", 98.0, 98.0, 1
    if len(exact_base) > 1:
        return None, "AMBIGUO_TIPO_LOGRADOURO", 98.0, 98.0, len(exact_base)

    scored = sorted(
        ((street_similarity(tipo, base, nucleo, e), e) for e in entries),
        key=lambda x: (-x[0], x[1].full, x[1].tipo),
    )
    if not scored:
        return None, "SEM_CANDIDATO", 0.0, 0.0, 0
    top_score, top = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    margin = top_score - second
    if top_score < config.fuzzy_thr:
        return None, "FUZZY_ABAIXO_LIMIAR", top_score, second, len(scored)
    if len(scored) > 1 and margin < config.fuzzy_margin:
        return None, "FUZZY_AMBIGUO", top_score, second, len(scored)
    return top, "FUZZY_UNICO", top_score, second, len(scored)


def choose_number(numbers: dict[int, dict[str, Any]], num: int, max_delta: int, parity_penalty: int) -> tuple[int, dict[str, Any], int, int] | None:
    candidates = []
    for n, data in numbers.items():
        delta = abs(num - n)
        if delta > max_delta:
            continue
        parity = 0 if (num % 2) == (n % 2) else 1
        nv = data.get("nv") if data.get("nv") is not None else 99
        effective = delta + parity * parity_penalty
        candidates.append((effective, delta, parity, nv, n, data))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3], x[4]))
    _, delta, parity, _, n, data = candidates[0]
    return n, data, delta, parity


def empty_match(reason: str, score: float = 0.0, second: float = 0.0, candidates: int = 0) -> dict[str, Any]:
    return {
        "LATITUDE": np.nan,
        "LONGITUDE": np.nan,
        "NV_GEO_COORD": pd.NA,
        "COD_UNICO_ENDERECO": None,
        "MATCH_PASS": "SEM_MATCH",
        "MATCH_CONF": "SEM_MATCH",
        "MATCH_DETALHE": reason,
        "NUM_DELTA": pd.NA,
        "NUM_IBGE": pd.NA,
        "PARIDADE_DIVERGENTE": pd.NA,
        "LOGR_MATCH": None,
        "LOGR_SCORE": score,
        "LOGR_SCORE_SEGUNDO": second,
        "LOGR_CANDIDATOS": candidates,
        "DESVIO_METROS": np.nan,
        "IBGE_FLAG_COMERCIAL": pd.NA,
        "IBGE_QTD_UNIDADES": pd.NA,
        "IBGE_ESPECIES_COD": None,
        "IBGE_DSC_ESTAB": None,
        "_DSC_LIST": [],
    }


def resolve_match(row: pd.Series, index: dict[str, list[StreetEntry]], config: MatchConfig) -> dict[str, Any]:
    cep = row["CEP_NORM"]
    entries = index.get(cep, [])
    street, method, score, second, count = choose_street(
        entries,
        row["LOGR_TIPO"],
        row["LOGR_BASE"],
        row["LOGR_NUCLEO"],
        config,
    )
    if street is None:
        return empty_match(method, score, second, count)

    if row["NUM_INT"] == 0:
        if row["NUM_STATUS"] != "SEM_NUMERO":
            return empty_match(f"{method}+NUMERO_INVALIDO", score, second, count)
        if not valid_coord(street.centroid["lat"], street.centroid["lon"]):
            return empty_match(f"{method}+SEM_CENTROIDE", score, second, count)
        result = empty_match("P8_CENTROIDE_LOGRADOURO", score, second, count)
        result.update({
            "LATITUDE": street.centroid["lat"],
            "LONGITUDE": street.centroid["lon"],
            "NV_GEO_COORD": 4,
            "MATCH_PASS": "P8_CENTROIDE_SN",
            "MATCH_CONF": "BAIXA",
            "MATCH_DETALHE": f"{method}+CENTROIDE_SEM_NUMERO",
            "LOGR_MATCH": street.full,
            "DESVIO_METROS": street.centroid["p95"],
            # Deliberadamente não herda anotação comercial de um endereço específico.
            "IBGE_FLAG_COMERCIAL": pd.NA,
        })
        return result

    num = int(row["NUM_INT"])
    if num in street.numbers:
        data = street.numbers[num]
        pass_name = "P1_EXATO" if method == "EXATO_TIPO" else (
            "P2_EXATO_BASE" if method == "EXATO_BASE_UNICO" else "P4_FUZZY_EXATO"
        )
        conf = "ALTA" if pass_name == "P1_EXATO" else "MEDIA"
        return build_match_result(street, data, pass_name, conf, method, score, second, count, num, 0, 0)

    near = choose_number(street.numbers, num, config.delta_proximo, config.parity_penalty)
    if near:
        n, data, delta, parity = near
        pass_name = "P3_PROX_EXATO" if method.startswith("EXATO") else "P5_FUZZY_PROX"
        return build_match_result(street, data, pass_name, "MEDIA", method, score, second, count, n, delta, parity)

    wide = choose_number(street.numbers, num, config.delta_amplo, config.parity_penalty)
    if wide:
        n, data, delta, parity = wide
        pass_name = "P6_PROX_AMPLO" if method.startswith("EXATO") else "P7_FUZZY_AMPLO"
        return build_match_result(street, data, pass_name, "BAIXA", method, score, second, count, n, delta, parity)

    return empty_match(f"{method}+SEM_NUMERO_PROXIMO", score, second, count)


def build_match_result(
    street: StreetEntry,
    data: dict[str, Any],
    pass_name: str,
    conf: str,
    method: str,
    score: float,
    second: float,
    count: int,
    num_ibge: int,
    delta: int,
    parity: int,
) -> dict[str, Any]:
    return {
        "LATITUDE": data["lat"],
        "LONGITUDE": data["lon"],
        "NV_GEO_COORD": data["nv"],
        "COD_UNICO_ENDERECO": data["cod"],
        "MATCH_PASS": pass_name,
        "MATCH_CONF": conf,
        "MATCH_DETALHE": method,
        "NUM_DELTA": delta,
        "NUM_IBGE": num_ibge,
        "PARIDADE_DIVERGENTE": parity,
        "LOGR_MATCH": street.full,
        "LOGR_SCORE": score,
        "LOGR_SCORE_SEGUNDO": second,
        "LOGR_CANDIDATOS": count,
        "DESVIO_METROS": 0.0,
        "IBGE_FLAG_COMERCIAL": data["flag_com"],
        "IBGE_QTD_UNIDADES": data["qtd"],
        "IBGE_ESPECIES_COD": data["especies"],
        "IBGE_DSC_ESTAB": data["dsc"],
        "_DSC_LIST": data["dsc_list"],
    }


def apply_matching(cnpj: pd.DataFrame, index: dict[str, list[StreetEntry]], config: MatchConfig) -> pd.DataFrame:
    rows = [resolve_match(row, index, config) for _, row in cnpj.iterrows()]
    match_df = pd.DataFrame(rows, index=cnpj.index)
    return pd.concat([cnpj, match_df], axis=1)


# ---------------------------------------------------------------------------
# Perfil, empresa, existência e decisão
# ---------------------------------------------------------------------------
CNAE_DIV_MAP = {
    **{x: ("PRESENCIAL", True) for x in ["45", "47", "55", "56", "75", "85", "86", "87", "90", "91", "93", "95", "96"]},
    **{x: ("PRESENCIAL", False) for x in [
        "01", "02", "03", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "35", "36", "37", "38", "39", "41", "42", "43", "46", "84"
    ]},
    **{x: ("HIBRIDO", False) for x in ["52", "61", "64", "65", "68", "71", "72", "74", "77", "79", "81", "88", "94"]},
    **{x: ("MOVEL", False) for x in ["49", "50", "51", "53", "97"]},
    **{x: ("PAPEL", False) for x in ["58", "59", "60", "62", "63", "66", "69", "70", "73", "78", "80", "82", "83", "99"]},
}

CNAE_EXCECAO = {
    "5612100": ("MOVEL", True),
    "4923002": ("MOVEL", False),
    "4923001": ("MOVEL", False),
    "8230002": ("PRESENCIAL", True),
    "4924800": ("HIBRIDO", True),
    "9700500": ("MOVEL", False),
    "5320201": ("MOVEL", False),
    "5320202": ("MOVEL", False),
    "4930201": ("MOVEL", False),
    "4930202": ("MOVEL", False),
    "8712300": ("MOVEL", True),
    "5310502": ("PRESENCIAL", True),
    "8219901": ("PRESENCIAL", True),
    "8299707": ("PRESENCIAL", True),
}

# Classes de 4 dígitos que normalmente exigem um ponto físico operacional.
CNAE_EXIGE_ESTRUTURA_PREFIX4 = {
    "4711", "4712", "4713", "4721", "4722", "4723", "4724", "4729",
    "4520", "4530", "4541", "4542", "4731", "4732", "4784", "5510", "5590",
    "5611", "8610", "8630", "8640", "8650", "8660", "4741", "4742", "4743",
    "4744", "4751", "4752", "4753", "4754", "4755", "4756", "4757", "4759",
    "4761", "4762", "4763", "4771", "4772", "4773", "4774", "4781", "4782",
    "4783", "4785", "4789",
}

SEG_MAP = {
    **{x: "AGRONEGOCIO" for x in ["01", "02", "03", "05", "06", "07", "08", "09"]},
    "10": "INDUSTRIA_ALIMENTOS", "11": "INDUSTRIA_ALIMENTOS",
    **{x: "INDUSTRIA" for x in ["12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33"]},
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
    "94": "ASSOCIACOES_ONG", "96": "SERVICOS_PESSOAIS", "97": "SERVICOS_PESSOAIS",
    "99": "OUTROS",
}

PORTE_BINS = [-1, 0, 999, 9_999, 99_999, 999_999, 9_999_999, 99_999_999, float("inf")]
PORTE_LABELS = [
    "CAP_ZERO", "CAP_MICRO_MIN", "CAP_MICRO", "CAP_MICRO_PLUS",
    "CAP_PEQUENO", "CAP_MEDIO", "CAP_GRANDE", "CAP_CORPORATIVO",
]
PORTE_SIMPLES = {
    "CAP_ZERO": "MICRO", "CAP_MICRO_MIN": "MICRO", "CAP_MICRO": "MICRO",
    "CAP_MICRO_PLUS": "PEQUENO", "CAP_PEQUENO": "MEDIO", "CAP_MEDIO": "MEDIO",
    "CAP_GRANDE": "GRANDE", "CAP_CORPORATIVO": "GRANDE",
}

RESID_RE = re.compile(r"\b(APT|APTO|APARTAMENTO|AP|QUARTO|QTO|QT|CASA|RESIDENCIA|KITNET|KITINETE|QUITINETE|MORADIA|DORMITORIO|SUITE|BLOCO|BL)\b")
COMERC_RE = re.compile(r"\b(LOJA|SALA|SL|CONJ|CONJUNTO|GALPAO|BOX|QUIOSQUE|QUIOSK|ESCRITORIO|ESCRIT|PAVILHAO|PAVLH|DEPOSITO|ANDAR|PISO|SOBRELOJA|TERREO|MEZANINO|COMERCIAL|SALAO|PREDIO|FRENTE)\b")


def complement_type(value: Any) -> str:
    s = deaccent(clean_text(value)).upper()
    if not s:
        return "VAZIO"
    resid, comerc = bool(RESID_RE.search(s)), bool(COMERC_RE.search(s))
    if resid and comerc:
        return "AMBIGUO"
    if resid:
        return "RESIDENCIAL"
    if comerc:
        return "COMERCIAL"
    return "OUTRO"


def classify_profile(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    profiles = []
    for cnae7, div in zip(out["CNAE7"], out["CNAE_DIV"]):
        if cnae7 in CNAE_EXCECAO:
            p, atend = CNAE_EXCECAO[cnae7]
            motivo = "EXCECAO_CNAE7"
            conf = "ALTA"
        elif div in CNAE_DIV_MAP:
            p, atend = CNAE_DIV_MAP[div]
            motivo = "REGRA_DIVISAO_CNAE"
            conf = "MEDIA"
        else:
            p, atend = "INCERTO", False
            motivo = "CNAE_NAO_MAPEADO"
            conf = "BAIXA"
        profiles.append((p, atend, motivo, conf))
    out[["PERFIL_ESTRUTURA", "ATEND_PUBLICO", "PERFIL_MOTIVO", "PERFIL_CONFIANCA"]] = pd.DataFrame(profiles, index=out.index)
    out["COMPLEMENTO_TIPO"] = out["complemento"].apply(complement_type)
    out["CNAE_EXIGE_ESTRUTURA"] = out["CNAE7"].str[:4].isin(CNAE_EXIGE_ESTRUTURA_PREFIX4)
    out["FLAG_ESTABELECIDO"] = out["SITUACAO_NORM"].eq("ATIVA") & out["ATIVO_ANOS"].ge(2)
    out["FLAG_RECENTE"] = out["ATIVO_ANOS"].lt(1) & out["ATIVO_ANOS"].notna()
    out["FLAG_OPERACAO_MOVEL"] = out["PERFIL_ESTRUTURA"].eq("MOVEL")
    return out


def classify_company(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["SEGMENTO"] = out["CNAE_DIV"].map(SEG_MAP).fillna("OUTROS")
    out["PORTE_CAPITAL"] = pd.cut(
        out["CAPITAL_NUM"], bins=PORTE_BINS, labels=PORTE_LABELS, right=True
    ).astype(str)
    out["PORTE_SIMPLES"] = out["PORTE_CAPITAL"].map(PORTE_SIMPLES).fillna("INDEFINIDO")
    out["PERFIL_EMPRESA"] = out["SEGMENTO"] + "_" + out["PORTE_SIMPLES"]

    roots = out.loc[out["TIPO_EMPRESA_NORM"].eq("FILIAL"), "CNPJ_NORM"].str[:8].value_counts()
    out["CNPJ_RAIZ"] = out["CNPJ_NORM"].str[:8]
    out["N_FILIAIS_NA_BASE"] = out["CNPJ_RAIZ"].map(roots).fillna(0).astype(int)
    emp_ind = out["NAT_JUR_DIGITOS"].str.startswith("2135")
    out["PAPEL_ESTRUTURAL"] = np.select(
        [
            out["TIPO_EMPRESA_NORM"].eq("FILIAL"),
            out["N_FILIAIS_NA_BASE"].gt(0),
            emp_ind,
        ],
        ["FILIAL", "MATRIZ_COM_REDE", "UNIDADE_UNICA"],
        default="MATRIZ",
    )
    return out


def validate_name(row: pd.Series, threshold: int) -> tuple[str, float, str | None]:
    dsc_list = row.get("_DSC_LIST", [])
    if not isinstance(dsc_list, list) or not dsc_list:
        return "SEM_DSC", 0.0, None
    names = [x for x in [row.get("NOME_NORM", ""), row.get("RAZAO_NORM", "")] if x]
    if not names:
        return "SEM_NOME_CNPJ", 0.0, None
    best_score, best_dsc = 0.0, None
    for dsc in dsc_list:
        for name in names:
            score = float(fuzz.token_set_ratio(name, dsc))
            if score > best_score:
                best_score, best_dsc = score, dsc
    if best_score >= threshold:
        return "CONFIRMADO", best_score, best_dsc
    if best_score >= 65:
        return "PROVAVEL", best_score, best_dsc
    return "NAO_CONFIRMADO", best_score, None


def is_one(value: Any) -> bool:
    return bool(pd.notna(value) and int(value) == 1)


def existence_score(row: pd.Series, cutoff: pd.Timestamp) -> tuple[float, str, str, str]:
    if pd.isna(row["LATITUDE"]):
        return 0.0, "SEM_EVIDENCIA", "SEM_COORDENADA", "NAO_APLICAVEL_SEM_COORDENADA"
    dt = row["DATA_ABERTURA_DT"]
    if pd.isna(dt):
        applicability = "INDETERMINADO_DATA_AUSENTE"
    elif dt > cutoff:
        applicability = "NAO_POS_CNEFE"
    else:
        applicability = "SIM"

    if applicability != "SIM":
        label = "INDETERMINADA_POS_CNEFE" if applicability == "NAO_POS_CNEFE" else "INDETERMINADA_DATA_AUSENTE"
        return 0.0, label, "EVIDENCIA_CNEFE_NAO_DECISORIA", applicability

    score = 0.0
    reasons = []
    name_status = row["XFERA_EXISTE_NO_LOCAL"]
    if name_status == "CONFIRMADO":
        score += 6; reasons.append("NOME_CONFIRMADO_IBGE:+6")
    elif name_status == "PROVAVEL":
        score += 3; reasons.append("NOME_PROVAVEL_IBGE:+3")
    if is_one(row["IBGE_FLAG_COMERCIAL"]):
        score += 4; reasons.append("ATIV_COMERCIAL_LOCAL:+4")
    if row["SITUACAO_NORM"] == "ATIVA":
        score += 1; reasons.append("ATIVA:+1")
    if row["MATCH_CONF"] in {"ALTA", "MEDIA"}:
        score += 1; reasons.append("MATCH_PRECISO:+1")
    if row["MATCH_PASS"].startswith("P4") or row["MATCH_PASS"].startswith("P5"):
        score += 0.5; reasons.append("MATCH_FUZZY:+0.5")
    if row["NV_GEO_COORD"] == 1:
        score += 1; reasons.append("NV1:+1")
    if row["MATCH_PASS"] == "P8_CENTROIDE_SN":
        score -= 1; reasons.append("CENTROIDE:-1")
    if row["MATCH_CONF"] == "BAIXA" and pd.notna(row["NUM_DELTA"]):
        score -= 1; reasons.append("DELTA_AMPLO:-1")
    score = max(0.0, min(15.0, score))
    if score >= 9:
        label = "EXISTE_QUASE_CERTO"
    elif score >= 6:
        label = "EXISTE_PROVAVEL"
    elif score >= 4:
        label = "EXISTE_POSSIVEL"
    elif score > 0:
        label = "EXISTE_INCERTO"
    else:
        label = "SEM_EVIDENCIA"
    return score, label, " | ".join(reasons) or "SEM_EVIDENCIA_POSITIVA", applicability


def score_structure(row: pd.Series) -> tuple[int, str, str]:
    score = 0
    reasons = []
    p = row["PERFIL_ESTRUTURA"]
    if p == "PRESENCIAL": score += 3; reasons.append("CNAE_PRESENCIAL:+3")
    if bool(row["ATEND_PUBLICO"]): score += 2; reasons.append("ATEND_PUBLICO:+2")
    if row["PAPEL_ESTRUTURAL"] == "FILIAL": score += 2; reasons.append("FILIAL:+2")
    if is_one(row["IBGE_FLAG_COMERCIAL"]) and row["XFERA_IBGE_APLICAVEL"] == "SIM": score += 2; reasons.append("IBGE_CONFIRMA:+2")
    if p == "HIBRIDO": score += 1; reasons.append("CNAE_HIBRIDO:+1")
    if row["PAPEL_ESTRUTURAL"] == "MATRIZ_COM_REDE": score += 1; reasons.append("MATRIZ_COM_REDE:+1")
    if bool(row["FLAG_ESTABELECIDO"]): score += 1; reasons.append("ESTABELECIDO:+1")
    if row["CAPITAL_NUM"] >= 100_000: score += 1; reasons.append("CAP_EXPRESSIVO:+1")
    if bool(row["FLAG_RECENTE"]): score -= 1; reasons.append("RECENTE:-1")
    if row["PAPEL_ESTRUTURAL"] == "UNIDADE_UNICA" and p not in {"PRESENCIAL", "HIBRIDO"}:
        score -= 1; reasons.append("UNIDADE_UNICA_SEM_PRESENCIAL:-1")
    score = int(max(0, min(12, score)))
    conf = "MUITO_ALTA" if score >= 8 else "ALTA" if score >= 6 else "MEDIA" if score >= 4 else "BAIXA" if score >= 2 else "MUITO_BAIXA"
    return score, conf, " | ".join(reasons) or "SEM_SINAIS"


def apply_business_rules(
    df: pd.DataFrame,
    cutoff: pd.Timestamp,
    existence_threshold: int,
    accept_invalid_cnpj: bool,
) -> pd.DataFrame:
    out = classify_company(classify_profile(df))

    name_results = out.apply(lambda r: validate_name(r, existence_threshold), axis=1, result_type="expand")
    name_results.columns = ["XFERA_EXISTE_NO_LOCAL", "XFERA_EXIST_NOME_SCORE", "XFERA_EXIST_DSC"]
    out = pd.concat([out, name_results], axis=1)

    existence = out.apply(lambda r: existence_score(r, cutoff), axis=1, result_type="expand")
    existence.columns = ["XFERA_EXIST_SCORE", "XFERA_EXISTENCIA_FISICA", "XFERA_EXIST_MOTIVOS", "XFERA_IBGE_APLICAVEL"]
    out = pd.concat([out, existence], axis=1)

    def commercial_profile(row: pd.Series) -> str:
        comp = row["COMPLEMENTO_TIPO"]
        p = row["PERFIL_ESTRUTURA"]
        atend = bool(row["ATEND_PUBLICO"])
        required = bool(row["CNAE_EXIGE_ESTRUTURA"])
        if comp == "RESIDENCIAL" and not required:
            return "RESIDENCIAL_NAO_COMERCIAL"
        if comp == "COMERCIAL" or required:
            if p == "PRESENCIAL" and atend:
                return "COMERCIO_ATENDIMENTO"
            if p == "PRESENCIAL":
                return "PRESENCIAL_SEM_PUBLICO"
            return "PONTO_COMERCIAL_OUTRO"
        if p == "PRESENCIAL" and atend:
            return "COMERCIO_ATENDIMENTO"
        if p == "PRESENCIAL":
            return "PRESENCIAL_SEM_PUBLICO"
        if p == "HIBRIDO" and atend:
            return "COMERCIO_EVENTUAL"
        return "NAO_COMERCIAL"

    out["PERFIL_COMERCIAL"] = out.apply(commercial_profile, axis=1)

    def aptitude(row: pd.Series) -> str:
        if pd.notna(row["LATITUDE"]):
            return "APTO_COORDENADA"
        logr = row["LOGR_STATUS"] == "VALIDO"
        num = row["NUM_INT"] > 0
        city = bool(clean_text(row["municipio"])) and bool(clean_text(row["uf"]))
        cep_ok = row["CEP_STATUS"] in {"VALIDO", "AJUSTADO_ZFILL"}
        if logr and num and city:
            return "APTO_ENDERECO_COMPLETO"
        if logr and city:
            return "APTO_ENDERECO_PARCIAL"
        if cep_ok:
            return "APTO_CEP_APENAS"
        return "NAO_APTO"

    out["APTIDAO_CRUZAMENTO"] = out.apply(aptitude, axis=1)
    out["STATUS_GEOCODIFICACAO"] = np.where(out["LATITUDE"].notna(), "GEOCODIFICADO", "PENDENTE_OUTRA_FONTE")

    profiles_point = {
        "COMERCIO_ATENDIMENTO", "COMERCIO_EVENTUAL", "PONTO_COMERCIAL_OUTRO", "PRESENCIAL_SEM_PUBLICO"
    }

    def decision(row: pd.Series) -> tuple[bool, str]:
        if not accept_invalid_cnpj and not bool(row["CNPJ_VALIDO"]):
            return False, f"CNPJ_INVALIDO_{row['CNPJ_STATUS']}"
        if row["SITUACAO_NORM"] != "ATIVA":
            return False, f"NAO_ATIVA_{row['SITUACAO_NORM']}"
        if row["PERFIL_COMERCIAL"] in profiles_point:
            return True, ""
        reasons = []
        if row["PERFIL_COMERCIAL"] == "RESIDENCIAL_NAO_COMERCIAL": reasons.append("COMPLEMENTO_RESIDENCIAL")
        if row["PERFIL_ESTRUTURA"] == "PAPEL": reasons.append("ATIVIDADE_PAPEL_HOME_OFFICE")
        if row["PERFIL_ESTRUTURA"] == "MOVEL": reasons.append("ATIVIDADE_MOVEL_ITINERANTE")
        if row["PERFIL_ESTRUTURA"] == "INCERTO": reasons.append("CNAE_NAO_CLASSIFICADO")
        if not reasons: reasons.append("SEM_ATENDIMENTO_PRESENCIAL")
        return False, " + ".join(reasons)

    decisions = out.apply(decision, axis=1, result_type="expand")
    decisions.columns = ["POTENCIAL_CRUZAMENTO", "MOTIVO_SEM_CRUZAMENTO"]
    out = pd.concat([out, decisions], axis=1)

    structures = out.apply(score_structure, axis=1, result_type="expand")
    structures.columns = ["SCORE_ESTRUTURA", "CONFIANCA_ESTRUTURA", "SCORE_ESTRUTURA_MOTIVOS"]
    out = pd.concat([out, structures], axis=1)
    return out


# ---------------------------------------------------------------------------
# QA, saída e dicionário
# ---------------------------------------------------------------------------
def validate_output(df: pd.DataFrame, input_rows: int, strict: bool = True) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"CHECK": name, "STATUS": "APROVADO" if ok else "FALHA", "DETALHE": detail})

    add("CONTAGEM_LINHAS", len(df) == input_rows, f"entrada={input_rows}; saída={len(df)}")
    add("RID_UNICO", df["_RID"].is_unique, f"duplicados={df['_RID'].duplicated().sum()}")
    add("ORDEM_PRESERVADA", df["_RID"].tolist() == list(range(input_rows)), "ordem original")
    required_out = [
        "SEGMENTO", "PORTE_CAPITAL", "PAPEL_ESTRUTURAL", "PERFIL_EMPRESA",
        "CONFIANCA_ESTRUTURA", "SCORE_ESTRUTURA", "APTIDAO_CRUZAMENTO",
        "POTENCIAL_CRUZAMENTO", "PERFIL_COMERCIAL",
    ]
    missing_values = {c: int(df[c].isna().sum()) for c in required_out if c not in df or df[c].isna().any()}
    add("CLASSIFICACOES_SEM_NULOS", not missing_values, str(missing_values))

    matched = df["LATITUDE"].notna()
    coord_ok = all(valid_coord(a, b) for a, b in zip(df.loc[matched, "LATITUDE"], df.loc[matched, "LONGITUDE"]))
    add("COORDENADAS_VALIDAS", coord_ok, f"matchados={int(matched.sum())}")
    coord_br = all(in_brazil_bbox(a, b) for a, b in zip(df.loc[matched, "LATITUDE"], df.loc[matched, "LONGITUDE"]))
    add("COORDENADAS_NO_BRASIL", coord_br, f"matchados={int(matched.sum())}")

    delta_check = pd.to_numeric(df["NUM_DELTA"], errors="coerce").fillna(-1)
    exact_bad = df["MATCH_PASS"].isin(["P1_EXATO", "P2_EXATO_BASE", "P4_FUZZY_EXATO"]) & delta_check.ne(0)
    add("EXATO_DELTA_ZERO", not exact_bad.any(), f"violações={int(exact_bad.sum())}")

    centroid_bad = df["MATCH_PASS"].eq("P8_CENTROIDE_SN") & df["NUM_INT"].ne(0)
    add("CENTROIDE_SO_SEM_NUMERO", not centroid_bad.any(), f"violações={int(centroid_bad.sum())}")

    potential_bad = df["POTENCIAL_CRUZAMENTO"] & df["SITUACAO_NORM"].ne("ATIVA")
    add("POTENCIAL_SO_ATIVAS", not potential_bad.any(), f"violações={int(potential_bad.sum())}")

    if strict:
        failed = [x for x in checks if x["STATUS"] == "FALHA"]
        if failed:
            raise AssertionError("Falha de QA: " + " | ".join(f"{x['CHECK']}: {x['DETALHE']}" for x in failed))
    return checks


def build_summary(df: pd.DataFrame, meta: dict[str, Any], qa_counts: dict[str, int], checks: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[tuple[str, Any]] = [
        ("VERSAO_SKILL", VERSION),
        ("DATA_REFERENCIA", meta["data_referencia"]),
        ("CNEFE_CUTOFF", meta["cnefe_cutoff"]),
        ("ARQUIVO_CNPJ", meta["arquivo_cnpj"]),
        ("ARQUIVO_CNEFE", meta["arquivo_cnefe"]),
        ("ARQUIVO_INTERNO_CNEFE", meta.get("arquivo_interno_cnefe", "")),
        ("TOTAL_CNPJ", len(df)),
        ("POTENCIAL_CRUZAMENTO", int(df["POTENCIAL_CRUZAMENTO"].sum())),
        ("SEM_POTENCIAL", int((~df["POTENCIAL_CRUZAMENTO"]).sum())),
        ("GEOCODIFICADOS", int(df["LATITUDE"].notna().sum())),
        ("MATCH_ALTA", int(df["MATCH_CONF"].eq("ALTA").sum())),
        ("MATCH_MEDIA", int(df["MATCH_CONF"].eq("MEDIA").sum())),
        ("MATCH_BAIXA", int(df["MATCH_CONF"].eq("BAIXA").sum())),
        ("SEM_MATCH", int(df["MATCH_CONF"].eq("SEM_MATCH").sum())),
    ]
    rows.extend((k.upper(), v) for k, v in sorted(qa_counts.items()))
    rows.extend((f"QA_{c['CHECK']}", c["STATUS"]) for c in checks)
    return pd.DataFrame(rows, columns=["INDICADOR", "VALOR"])


def data_dictionary() -> pd.DataFrame:
    rows = [
        ("CNPJ_NORM", "RFB", "CNPJ somente dígitos"),
        ("CNPJ_VALIDO", "QA", "Validação de tamanho e dígitos verificadores"),
        ("CNPJ_STATUS", "QA", "Motivo da validação do CNPJ"),
        ("CEP_NORM", "NORMALIZAÇÃO", "CEP normalizado com 8 dígitos"),
        ("CEP_STATUS", "QA", "Status do parsing do CEP"),
        ("LOGR_TIPO", "NORMALIZAÇÃO", "Tipo canônico do logradouro"),
        ("LOGR_BASE", "NORMALIZAÇÃO", "Nome do logradouro sem tipo"),
        ("LOGR_FULL", "NORMALIZAÇÃO", "Tipo + nome canônicos"),
        ("LOGR_STATUS", "QA", "Validade mínima do logradouro"),
        ("NUM_INT", "NORMALIZAÇÃO", "Número inteiro extraído"),
        ("NUM_STATUS", "QA", "VALIDO, VALIDO_COM_SUFIXO, SEM_NUMERO, INVALIDO"),
        ("XFERA_LAT", "CNEFE", "Latitude da coordenada escolhida"),
        ("XFERA_LON", "CNEFE", "Longitude da coordenada escolhida"),
        ("XFERA_NV_GEO", "CNEFE", "Nível de qualidade da coordenada"),
        ("XFERA_MATCH_PASS", "MATCH", "Passo determinístico que resolveu o registro"),
        ("XFERA_MATCH_CONF", "MATCH", "ALTA, MEDIA, BAIXA ou SEM_MATCH"),
        ("XFERA_MATCH_DETALHE", "MATCH", "Método de escolha do logradouro"),
        ("XFERA_LOGR_SCORE", "MATCH", "Score do melhor candidato de logradouro"),
        ("XFERA_LOGR_SCORE_SEGUNDO", "MATCH", "Score do segundo candidato"),
        ("XFERA_NUM_DELTA", "MATCH", "Diferença entre número CNPJ e número CNEFE"),
        ("XFERA_PARIDADE_DIVERGENTE", "MATCH", "1 quando paridade do número diverge"),
        ("XFERA_DESVIO_METROS", "MATCH", "Incerteza p95 do centróide para S/N"),
        ("XFERA_FLAG_COMERCIAL", "CNEFE", "Atividade não residencial no endereço exato"),
        ("XFERA_EXISTE_NO_LOCAL", "EXISTÊNCIA", "Similaridade do nome com fachada CNEFE"),
        ("XFERA_EXIST_SCORE", "EXISTÊNCIA", "Score multi-evidência 0 a 15"),
        ("XFERA_EXISTENCIA_FISICA", "EXISTÊNCIA", "Faixa da evidência física"),
        ("XFERA_IBGE_APLICAVEL", "EXISTÊNCIA", "SIM, NAO_POS_CNEFE ou INDETERMINADO_DATA_AUSENTE"),
        ("PERFIL_ESTRUTURA", "CNAE", "PRESENCIAL, HIBRIDO, MOVEL, PAPEL ou INCERTO"),
        ("PERFIL_CONFIANCA", "CNAE", "Confiança da regra de classificação"),
        ("PERFIL_COMERCIAL", "DECISÃO", "Classificação do ponto físico"),
        ("SEGMENTO", "EMPRESA", "Segmento amplo por divisão CNAE"),
        ("PORTE_CAPITAL", "EMPRESA", "Faixa do capital social declarado"),
        ("PAPEL_ESTRUTURAL", "EMPRESA", "Matriz, filial, matriz com rede ou unidade única"),
        ("SCORE_ESTRUTURA", "EMPRESA", "Score estrutural 0 a 12"),
        ("APTIDAO_CRUZAMENTO", "DECISÃO", "Aptidão geográfica independentemente do perfil comercial"),
        ("POTENCIAL_CRUZAMENTO", "DECISÃO", "Ativa, CNPJ válido e atividade com ponto físico"),
        ("MOTIVO_SEM_CRUZAMENTO", "DECISÃO", "Motivo auditável quando não potencial"),
    ]
    return pd.DataFrame(rows, columns=["COLUNA", "GRUPO", "DESCRICAO"])


def rename_output(df: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "LATITUDE": "XFERA_LAT",
        "LONGITUDE": "XFERA_LON",
        "NV_GEO_COORD": "XFERA_NV_GEO",
        "COD_UNICO_ENDERECO": "XFERA_COD_IBGE",
        "MATCH_PASS": "XFERA_MATCH_PASS",
        "MATCH_CONF": "XFERA_MATCH_CONF",
        "MATCH_DETALHE": "XFERA_MATCH_DETALHE",
        "NUM_DELTA": "XFERA_NUM_DELTA",
        "NUM_IBGE": "XFERA_NUM_IBGE",
        "PARIDADE_DIVERGENTE": "XFERA_PARIDADE_DIVERGENTE",
        "LOGR_MATCH": "XFERA_LOGR_MATCH",
        "LOGR_SCORE": "XFERA_LOGR_SCORE",
        "LOGR_SCORE_SEGUNDO": "XFERA_LOGR_SCORE_SEGUNDO",
        "LOGR_CANDIDATOS": "XFERA_LOGR_CANDIDATOS",
        "DESVIO_METROS": "XFERA_DESVIO_METROS",
        "IBGE_FLAG_COMERCIAL": "XFERA_FLAG_COMERCIAL",
        "IBGE_QTD_UNIDADES": "XFERA_QTD_UNID",
        "IBGE_ESPECIES_COD": "XFERA_ESPECIES_COD",
        "IBGE_DSC_ESTAB": "XFERA_DSC_ESTAB",
    }
    return df.rename(columns=rename)


def export_excel(
    df_internal: pd.DataFrame,
    out_path: Path,
    summary: pd.DataFrame,
    dictionary: pd.DataFrame,
    checks: list[dict[str, Any]],
) -> None:
    out = rename_output(df_internal.copy())
    out = out.sort_values("_RID", kind="mergesort").reset_index(drop=True)

    hidden = ["_RID", "_DSC_LIST", "NOME_NORM", "RAZAO_NORM", "DATA_ABERTURA_DT"]
    base = out.drop(columns=hidden, errors="ignore")
    potential = base[base["POTENCIAL_CRUZAMENTO"]].copy()
    no_potential = base[~base["POTENCIAL_CRUZAMENTO"]].copy()
    qa_df = pd.DataFrame(checks)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    options = {"strings_to_formulas": False, "strings_to_urls": False, "constant_memory": False}
    with pd.ExcelWriter(out_path, engine="xlsxwriter", engine_kwargs={"options": options}) as writer:
        summary.to_excel(writer, sheet_name="RESUMO_EXECUCAO", index=False)
        qa_df.to_excel(writer, sheet_name="QA_ACEITE", index=False)
        dictionary.to_excel(writer, sheet_name="DICIONARIO", index=False)
        potential.to_excel(writer, sheet_name="POTENCIAL_CRUZAMENTO", index=False)
        no_potential.to_excel(writer, sheet_name="SEM_POTENCIAL", index=False)
        base.to_excel(writer, sheet_name="BASE_COMPLETA", index=False)

        wb = writer.book
        header = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        header_green = wb.add_format({"bold": True, "bg_color": "#375623", "font_color": "white", "border": 1})
        header_red = wb.add_format({"bold": True, "bg_color": "#843C0C", "font_color": "white", "border": 1})
        for name, data in [
            ("RESUMO_EXECUCAO", summary),
            ("QA_ACEITE", qa_df),
            ("DICIONARIO", dictionary),
            ("POTENCIAL_CRUZAMENTO", potential),
            ("SEM_POTENCIAL", no_potential),
            ("BASE_COMPLETA", base),
        ]:
            ws = writer.sheets[name]
            fmt = header_green if name == "POTENCIAL_CRUZAMENTO" else header_red if name == "SEM_POTENCIAL" else header
            for idx, col in enumerate(data.columns):
                ws.write(0, idx, col, fmt)
            ws.freeze_panes(1, 0)
            if len(data.columns):
                ws.autofilter(0, 0, max(len(data), 1), len(data.columns) - 1)
                ws.set_column(0, len(data.columns) - 1, 18)
            if name == "DICIONARIO":
                ws.set_column(0, 0, 32)
                ws.set_column(1, 1, 20)
                ws.set_column(2, 2, 72)


def run_pipeline(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    started = time.time()
    cnpj_path = Path(args.cnpj)
    cnefe_path = Path(args.ibge)
    out_path = Path(args.out)
    reference_date = pd.Timestamp(args.data_referencia)
    cutoff = pd.Timestamp(args.cnefe_cutoff)
    config = MatchConfig(
        delta_proximo=args.delta_proximo,
        delta_amplo=args.delta_amplo,
        fuzzy_thr=args.fuzzy_thr,
        fuzzy_margin=args.fuzzy_margin,
        parity_penalty=args.parity_penalty,
    )

    sheet = int(args.sheet) if isinstance(args.sheet, str) and args.sheet.isdigit() else args.sheet
    cnpj_raw, cnpj_meta = read_cnpj(cnpj_path, sheet)
    cnefe_raw, cnefe_meta = read_cnefe(cnefe_path)
    cnpj, qa_cnpj = prepare_cnpj(cnpj_raw, reference_date)
    cnefe, qa_cnefe = prepare_cnefe(cnefe_raw)
    index, _ = build_cnefe_index(cnefe)
    matched = apply_matching(cnpj, index, config)
    final = apply_business_rules(matched, cutoff, args.existence_threshold, args.accept_invalid_cnpj)
    final = final.sort_values("_RID", kind="mergesort").reset_index(drop=True)
    checks = validate_output(final, len(cnpj_raw), strict=not args.no_strict)

    qa_counts = {**qa_cnpj, **qa_cnefe}
    meta = {
        "versao": VERSION,
        "data_referencia": str(reference_date.date()),
        "cnefe_cutoff": str(cutoff.date()),
        "arquivo_cnpj": cnpj_path.name,
        "arquivo_cnefe": cnefe_path.name,
        "arquivo_interno_cnefe": cnefe_meta.get("arquivo_interno", ""),
        "parametros": vars(args),
        "duracao_segundos": round(time.time() - started, 2),
        "mapeamento_cnpj": cnpj_meta.get("mapeamento_colunas", {}),
    }
    summary = build_summary(final, meta, qa_counts, checks)
    export_excel(final, out_path, summary, data_dictionary(), checks)
    meta["duracao_segundos"] = round(time.time() - started, 2)
    return final, meta


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enriquecimento CNPJ × CNEFE/IBGE auditável")
    parser.add_argument("--cnpj", required=True, help="Base CNPJ .xlsx/.csv")
    parser.add_argument("--ibge", required=True, help="CNEFE municipal .zip/.csv")
    parser.add_argument("--out", required=True, help="Workbook .xlsx de saída")
    parser.add_argument("--sheet", default=None, help="Nome/índice da aba da base CNPJ")
    parser.add_argument("--data-referencia", default=str(date.today()), help="Data YYYY-MM-DD usada para idade da empresa")
    parser.add_argument("--cnefe-cutoff", default=DEFAULT_CNEFE_CUTOFF)
    parser.add_argument("--delta-proximo", type=int, default=DEFAULT_DELTA_PROXIMO)
    parser.add_argument("--delta-amplo", type=int, default=DEFAULT_DELTA_AMPLO)
    parser.add_argument("--fuzzy-thr", type=int, default=DEFAULT_FUZZY_THR)
    parser.add_argument("--fuzzy-margin", type=int, default=DEFAULT_FUZZY_MARGIN)
    parser.add_argument("--parity-penalty", type=int, default=DEFAULT_PARITY_PENALTY)
    parser.add_argument("--existence-threshold", type=int, default=82)
    parser.add_argument("--accept-invalid-cnpj", action="store_true", help="Não exclui CNPJ inválido da decisão de potencial")
    parser.add_argument("--no-strict", action="store_true", help="Registra falhas de QA sem interromper")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        final, meta = run_pipeline(args)
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    print(f"OK — versão {VERSION} — {len(final):,} registros — {meta['duracao_segundos']}s")
    print(f"Saída: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
