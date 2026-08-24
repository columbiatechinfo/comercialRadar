#!/usr/bin/env python3
"""Leitura das fontes — v2.2.

Mudanças sobre a v2.1:
- F-17  ZIP lido em STREAMING; a inspeção de esquema lê só o cabeçalho (nrows=5),
        não o arquivo inteiro, e não decodifica o conteúdo todo para str.
- F-23  o encoding vencedor é devolvido e registrado no resumo.
- F-07  colunas opcionais do CNEFE são ingeridas quando presentes.
- F-12  alias próprio para tipo_logradouro.
- F-13  guarda de conteúdo no alias 'tipo' (matriz/filial x tipo de logradouro).
"""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from _normalizacao import TIPO_CANON, deaccent, slug_header

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin1")

CNPJ_ALIASES: dict[str, list[str]] = {
    "cnpj_completo": ["cnpj_completo", "cnpj", "nr_cnpj", "numero_cnpj", "cnpj_basico_completo"],
    "razao_social": ["razao_social", "nome_empresarial", "razao"],
    "nome_fantasia": ["nome_fantasia", "fantasia", "nome_de_fantasia"],
    "tipo": ["identificador_matriz_filial", "matriz_filial", "tipo"],
    "situacao": ["situacao", "situacao_cadastral", "descricao_situacao_cadastral"],
    "data_inicio_atividade": ["data_inicio_atividade", "data_abertura", "inicio_atividade"],
    "cnae_principal_cod": ["cnae_principal_cod", "cnae_fiscal_principal", "cnae_principal", "cnae"],
    "cnae_principal_descricao": ["cnae_principal_descricao", "descricao_cnae", "cnae_descricao"],
    "natureza_juridica": ["natureza_juridica", "codigo_natureza_juridica", "nat_jur"],
    "porte": ["porte", "porte_empresa", "descricao_porte"],
    "capital_social": ["capital_social", "capital"],
    "tipo_logradouro": ["tipo_logradouro", "tipo_de_logradouro", "tp_logradouro", "descricao_tipo_de_logradouro"],
    "logradouro": ["logradouro", "nome_logradouro", "endereco_logradouro"],
    "numero": ["numero", "numero_endereco", "num_endereco", "nro"],
    "complemento": ["complemento", "complemento_endereco"],
    "bairro": ["bairro", "nome_bairro"],
    "municipio": ["municipio", "nome_municipio", "cidade"],
    "uf": ["uf", "sigla_uf", "estado"],
    "cep": ["cep", "codigo_cep"],
}

CNPJ_REQUIRED = {"cnpj_completo", "situacao", "logradouro", "numero", "cep", "cnae_principal_cod"}

# Núcleo mínimo — a execução falha sem estas.
CNEFE_REQUIRED = {
    "COD_UNICO_ENDERECO", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR",
    "NUM_ENDERECO", "CEP", "NV_GEO_COORD", "LATITUDE", "LONGITUDE",
    "COD_ESPECIE", "DSC_ESTABELECIMENTO",
}

# F-07 — ingeridas quando presentes; degradam com aviso quando ausentes.
CNEFE_OPCIONAIS = {
    "COD_MUNICIPIO", "COD_SETOR", "NUM_QUADRA", "NUM_FACE", "DSC_MODIFICADOR",
    "COD_INDICADOR_ESTAB_ENDERECO", "COD_TIPO_ESPECIE", "DSC_LOCALIDADE",
    "NOM_COMP_ELEM1", "VAL_COMP_ELEM1", "NOM_COMP_ELEM2", "VAL_COMP_ELEM2",
    "NOM_COMP_ELEM3", "VAL_COMP_ELEM3", "NOM_COMP_ELEM4", "VAL_COMP_ELEM4",
    "NOM_COMP_ELEM5", "VAL_COMP_ELEM5",
}

OPTIONAL_DEFAULTS = {
    "razao_social": "", "nome_fantasia": "", "tipo": "", "data_inicio_atividade": "",
    "cnae_principal_descricao": "", "natureza_juridica": "", "porte": "",
    "capital_social": "", "complemento": "", "bairro": "", "municipio": "", "uf": "",
    "tipo_logradouro": "",
}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def _peek(stream_factory, nbytes: int = 65_536) -> tuple[str, str, str]:
    """Lê só os primeiros bytes e devolve (texto, encoding, separador)."""
    with stream_factory() as fh:
        head = fh.read(nbytes)
    for enc in ENCODINGS:
        try:
            text = head.decode(enc)
        except UnicodeDecodeError:
            continue
        # descarta a última linha, que pode estar cortada
        text = text[: text.rfind("\n") + 1] or text
        return text, enc, detect_delimiter(text)
    raise ValueError("Não foi possível decodificar o cabeçalho com nenhum encoding conhecido")


def _read_stream(stream_factory, encoding: str, sep: str, usecols=None, nrows=None) -> pd.DataFrame:
    with stream_factory() as fh:
        return pd.read_csv(
            fh, sep=sep, dtype=str, encoding=encoding, low_memory=False,
            usecols=usecols, nrows=nrows, on_bad_lines="warn",
        )


def apply_aliases(df: pd.DataFrame, aliases: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    normalized = {slug_header(c): c for c in df.columns}
    rename: dict[str, str] = {}
    used: set[str] = set()
    alertas: list[str] = []
    for canonical, options in aliases.items():
        found = [normalized[o] for o in options if o in normalized]
        found = [f for f in found if f not in used]
        if len({slug_header(f) for f in found}) > 1:
            raise ValueError(f"Colunas ambíguas para '{canonical}': {found}")
        if found:
            rename[found[0]] = canonical
            used.add(found[0])
    out = df.rename(columns=rename)

    # F-13 — guarda de conteúdo: coluna 'tipo' que na verdade é tipo de logradouro
    if "tipo" in out.columns and "tipo_logradouro" not in out.columns:
        amostra = out["tipo"].dropna().astype(str).head(200).str.upper().str.replace(".", "", regex=False)
        if len(amostra) and (amostra.isin(TIPO_CANON).mean() > 0.5):
            out = out.rename(columns={"tipo": "tipo_logradouro"})
            rename = {k: ("tipo_logradouro" if v == "tipo" else v) for k, v in rename.items()}
            alertas.append(
                "A coluna mapeada como 'tipo' (matriz/filial) contém tipos de logradouro; "
                "remapeada para 'tipo_logradouro'."
            )
    return out, rename, alertas


def read_cnpj(path: Path, sheet: str | int | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Base CNPJ não encontrada: {path}")
    suffix = path.suffix.lower()
    encoding = "n/a (xlsx)"
    if suffix in {".xlsx", ".xlsm"}:
        df = pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, dtype=str, engine="openpyxl")
    elif suffix in {".csv", ".txt"}:
        _, encoding, sep = _peek(lambda: path.open("rb"))
        df = _read_stream(lambda: path.open("rb"), encoding, sep)
    elif suffix == ".parquet":
        df = pd.read_parquet(path).astype(str)
        encoding = "n/a (parquet)"
    else:
        raise ValueError("Base CNPJ deve ser .xlsx, .xlsm, .csv, .txt ou .parquet")

    df, mapping, alertas = apply_aliases(df, CNPJ_ALIASES)
    missing = sorted(CNPJ_REQUIRED - set(df.columns))
    if missing:
        raise ValueError(f"Base CNPJ sem colunas obrigatórias: {missing}")
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    meta = {
        "mapeamento_colunas": mapping,
        "linhas": len(df),
        "encoding": encoding,
        "sha256": sha256_file(path),
        "alertas": alertas,
    }
    return df, meta


def read_cnefe(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Lê o CNEFE em streaming. Inspeciona só o cabeçalho para escolher o arquivo."""
    if not path.exists():
        raise FileNotFoundError(f"CNEFE não encontrado: {path}")

    candidatos: list[tuple[str, Any, str, str, dict[str, str]]] = []

    def avaliar(nome: str, factory) -> None:
        try:
            texto, enc, sep = _peek(factory)
            head = _read_stream(factory, enc, sep, nrows=5)
        except Exception:
            return
        upper = {deaccent(c).upper().strip().lstrip("\ufeff"): c for c in head.columns}
        if CNEFE_REQUIRED.issubset(upper):
            candidatos.append((nome, factory, enc, sep, upper))

    if path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(path)
        for nome in zf.namelist():
            if nome.lower().endswith((".csv", ".txt")) and not nome.endswith("/"):
                avaliar(nome, (lambda n=nome: zf.open(n)))
    else:
        avaliar(path.name, lambda: path.open("rb"))

    if not candidatos:
        raise ValueError("Nenhum CSV/TXT do CNEFE contém todas as colunas obrigatórias")
    if len(candidatos) > 1:
        raise ValueError(
            "Mais de um arquivo CNEFE válido encontrado; selecione um ZIP unitário: "
            f"{[n for n, *_ in candidatos]}"
        )

    nome, factory, enc, sep, upper = candidatos[0]
    presentes = set(upper)
    usar = CNEFE_REQUIRED | (CNEFE_OPCIONAIS & presentes)
    df = _read_stream(factory, enc, sep, usecols=[upper[c] for c in usar])
    df = df.rename(columns={upper[c]: c for c in usar})

    ausentes = sorted(CNEFE_OPCIONAIS - presentes)
    meta = {
        "arquivo_interno": nome,
        "linhas": len(df),
        "encoding": enc,
        "separador": sep,
        "sha256": sha256_file(path),
        "colunas_opcionais_ausentes": ausentes,
        "alertas": (
            [f"CNEFE sem colunas opcionais (degradação declarada): {', '.join(ausentes)}"]
            if ausentes else []
        ),
    }
    return df, meta
