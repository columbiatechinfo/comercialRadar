#!/usr/bin/env python3
"""Pipeline auditável de enriquecimento CNPJ × CNEFE/IBGE — v2.2.

Princípios (herdados da v2.1 e reforçados):
- nenhum match usa CEP isolado;
- nenhum registro de entrada é descartado;
- normalização e parsing são explícitos e auditáveis;
- fuzzy exige limiar, margem de unicidade e cardinalidade compatível de tokens;
- coordenadas duplicadas escolhem a melhor qualidade de forma determinística;
- evidência CNEFE é separada da decisão de potencial comercial;
- mesma entrada + mesmos parâmetros = mesma saída.

Princípio novo da v2.2:
- o pipeline declara o que não sabe. Nenhum rótulo de alta confiança é emitido
  sobre premissa não verificada: NV, espécie, similaridade e integridade da
  gravação passaram a ser conferidos e expostos.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _indice_match import (  # noqa: E402
    MatchConfig, apply_matching, build_cnefe_index, map_unicos, prepare_cnefe,
)
from _io_fontes import read_cnefe, read_cnpj  # noqa: E402
from _normalizacao import (  # noqa: E402
    digits, normalize_cep, normalize_cnae, normalize_cnpj, normalize_logradouro,
    normalize_nome, normalize_status, normalize_tipo_empresa, parse_date_br,
    parse_money_br, parse_numero,
)
from _regras import apply_business_rules  # noqa: E402
from _saida import build_summary, data_dictionary, export_result, validate_output  # noqa: E402

VERSION = "2.2.0"
DEFAULT_CNEFE_CUTOFF = "2022-08-01"
DEFAULT_DELTA_PROXIMO = 20
DEFAULT_DELTA_AMPLO = 100
DEFAULT_FUZZY_THR = 90
DEFAULT_FUZZY_MARGIN = 5
DEFAULT_PARITY_PENALTY = 3
DEFAULT_NV_MAX = 3


def prepare_cnpj(df: pd.DataFrame, reference_date: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, int]]:
    out = df.copy().reset_index(drop=True)
    out["_RID"] = np.arange(len(out), dtype=np.int64)

    cnpjs = map_unicos(out["cnpj_completo"], normalize_cnpj)
    out["CNPJ_NORM"] = [c[0] for c in cnpjs]
    out["CNPJ_VALIDO"] = [c[1] for c in cnpjs]
    out["CNPJ_STATUS"] = [c[2] for c in cnpjs]
    out["CNPJ_FORMATO"] = [c[3] for c in cnpjs]

    ceps = map_unicos(out["cep"], normalize_cep)
    out["CEP_NORM"] = [c[0] for c in ceps]
    out["CEP_STATUS"] = [c[1] for c in ceps]

    # F-12 — o tipo do logradouro entra pelo campo próprio quando existe
    chave = out["tipo_logradouro"].fillna("").astype(str) + "\x1f" + out["logradouro"].fillna("").astype(str)
    logs = map_unicos(chave, lambda k: normalize_logradouro(k.split("\x1f", 1)[1], k.split("\x1f", 1)[0]))
    out["LOGR_TIPO"] = [x.tipo for x in logs]
    out["LOGR_BASE"] = [x.base for x in logs]
    out["LOGR_FULL"] = [x.full for x in logs]
    out["LOGR_NUCLEO"] = [x.nucleo for x in logs]
    out["LOGR_STATUS"] = [x.status for x in logs]
    out["LOGR_TOKENS"] = [x.n_tokens for x in logs]

    nums = map_unicos(out["numero"], parse_numero)
    out["NUM_INT"] = [x[0] for x in nums]
    out["NUM_STATUS"] = [x[1] for x in nums]
    out["NUM_SUFIXO"] = [x[2] for x in nums]
    out["FLAG_SN"] = out["NUM_STATUS"].isin(["SEM_NUMERO", "SEM_NUMERO_ZERO"])

    out["SITUACAO_NORM"] = map_unicos(out["situacao"], normalize_status)
    out["TIPO_EMPRESA_NORM"] = map_unicos(out["tipo"], normalize_tipo_empresa)

    cnaes = map_unicos(out["cnae_principal_cod"], normalize_cnae)
    out["CNAE7"] = [c[0] for c in cnaes]
    out["CNAE_DIV"] = [c[1] for c in cnaes]
    out["CNAE_FORMATADO"] = [c[2] for c in cnaes]
    out["NAT_JUR_DIGITOS"] = map_unicos(out["natureza_juridica"], digits)

    caps = map_unicos(out["capital_social"], parse_money_br)
    out["CAPITAL_NUM"] = [c[0] for c in caps]
    out["CAPITAL_STATUS"] = [c[1] for c in caps]

    datas = map_unicos(out["data_inicio_atividade"], parse_date_br)
    out["DATA_ABERTURA_DT"] = pd.to_datetime(pd.Series([d[0] for d in datas], index=out.index))
    out["DATA_ABERTURA_STATUS"] = [d[1] for d in datas]
    out["ATIVO_ANOS"] = ((reference_date - out["DATA_ABERTURA_DT"]).dt.days / 365.25).round(2)
    out.loc[out["ATIVO_ANOS"] < 0, "ATIVO_ANOS"] = 0

    out["NOME_NORM"] = map_unicos(out["nome_fantasia"], normalize_nome)
    vazio = out["NOME_NORM"].eq("")
    razao = pd.Series(map_unicos(out["razao_social"], normalize_nome), index=out.index)
    out.loc[vazio, "NOME_NORM"] = razao[vazio]
    out["RAZAO_NORM"] = razao

    qa = {
        "cnpj_total": len(out),
        "cnpj_invalido": int((~pd.Series(out["CNPJ_VALIDO"])).sum()),
        "cnpj_alfanumerico": int((out["CNPJ_FORMATO"] == "ALFANUMERICO").sum()),
        "cnpj_duplicado": int(out["CNPJ_NORM"].duplicated(keep=False).sum()),
        "cnpj_cep_invalido": int((~out["CEP_STATUS"].isin(["VALIDO", "AJUSTADO_ZFILL"])).sum()),
        "cnpj_logradouro_invalido": int((~out["LOGR_STATUS"].eq("VALIDO")).sum()),
        "cnpj_logradouro_sem_tipo": int(out["LOGR_TIPO"].eq("").sum()),
        "cnpj_numero_invalido": int(out["NUM_STATUS"].isin(["INVALIDO", "AUSENTE"]).sum()),
        "cnpj_numero_sem_numero": int(out["NUM_STATUS"].isin(["SEM_NUMERO", "SEM_NUMERO_ZERO"]).sum()),
        "cnpj_numero_com_sufixo": int(out["NUM_SUFIXO"].ne("").sum()),
        "cnpj_data_invalida_ou_ausente": int(out["DATA_ABERTURA_DT"].isna().sum()),
        "cnpj_capital_parse_invalido": int(out["CAPITAL_STATUS"].eq("INVALIDO_ASSUMIDO_ZERO").sum()),
    }
    return out, qa


def gate_municipio(cnpj: pd.DataFrame, cnefe: pd.DataFrame, estrito: bool) -> list[str]:
    """F-08 — confere se o CNEFE cobre o universo de CEPs da base CNPJ."""
    alertas: list[str] = []
    munis = cnefe["COD_MUNICIPIO"].replace("", np.nan).dropna().unique()
    if len(munis) > 1:
        alertas.append(f"CNEFE contém {len(munis)} municípios; o índice é por CEP e não separa por município.")
    ceps_cnpj = set(cnpj.loc[cnpj["CEP_NORM"].ne(""), "CEP_NORM"])
    ceps_cnefe = set(cnefe.loc[cnefe["CEP_NORM"].ne(""), "CEP_NORM"])
    if ceps_cnpj:
        fora = len(ceps_cnpj - ceps_cnefe) / len(ceps_cnpj)
        if fora > 0.5:
            msg = (f"{fora:.0%} dos CEPs da base CNPJ não existem no CNEFE fornecido — "
                   "provável incompatibilidade de município.")
            if estrito:
                raise ValueError(msg)
            alertas.append(msg)
    return alertas


def run_pipeline(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    t0 = time.time()
    cnpj_path, cnefe_path, out_path = Path(args.cnpj), Path(args.ibge), Path(args.out)
    reference_date = pd.Timestamp(args.data_referencia)
    cutoff = pd.Timestamp(args.cnefe_cutoff)
    config = MatchConfig(
        delta_proximo=args.delta_proximo, delta_amplo=args.delta_amplo,
        fuzzy_thr=args.fuzzy_thr, fuzzy_margin=args.fuzzy_margin,
        parity_penalty=args.parity_penalty, nv_max=args.nv_max, nv_strict=args.nv_strict,
    )

    sheet = int(args.sheet) if isinstance(args.sheet, str) and args.sheet.isdigit() else args.sheet
    cnpj_raw, cnpj_meta = read_cnpj(cnpj_path, sheet)
    cnefe_raw, cnefe_meta = read_cnefe(cnefe_path)

    cnpj, qa_cnpj = prepare_cnpj(cnpj_raw, reference_date)
    cnefe, qa_cnefe = prepare_cnefe(cnefe_raw)
    alertas = list(cnpj_meta.get("alertas", [])) + list(cnefe_meta.get("alertas", []))
    alertas += gate_municipio(cnpj, cnefe, args.gate_municipio_estrito)

    index, dsc_store, _ = build_cnefe_index(cnefe, config)
    matched = apply_matching(cnpj, index, config)
    final = apply_business_rules(matched, dsc_store, cutoff, args.existence_threshold, args.accept_invalid_cnpj)
    final = final.sort_values("_RID", kind="mergesort").reset_index(drop=True)

    checks = validate_output(final, len(cnpj_raw), strict=not args.no_strict)

    meta = {
        "versao": VERSION,
        "data_referencia": str(reference_date.date()),
        "cnefe_cutoff": str(cutoff.date()),
        "arquivo_cnpj": cnpj_path.name, "sha256_cnpj": cnpj_meta.get("sha256", ""),
        "encoding_cnpj": cnpj_meta.get("encoding", ""),
        "arquivo_cnefe": cnefe_path.name, "sha256_cnefe": cnefe_meta.get("sha256", ""),
        "encoding_cnefe": cnefe_meta.get("encoding", ""),
        "arquivo_interno_cnefe": cnefe_meta.get("arquivo_interno", ""),
        "parametros_motor": {
            "delta_proximo": args.delta_proximo, "delta_amplo": args.delta_amplo,
            "fuzzy_thr": args.fuzzy_thr, "fuzzy_margin": args.fuzzy_margin,
            "parity_penalty": args.parity_penalty, "nv_max": args.nv_max,
            "nv_strict": args.nv_strict, "existence_threshold": args.existence_threshold,
            "accept_invalid_cnpj": args.accept_invalid_cnpj,
        },
        "mapeamento_cnpj": cnpj_meta.get("mapeamento_colunas", {}),
        "alertas": alertas,
        "duracao_segundos": round(time.time() - t0, 2),
    }
    summary = build_summary(final, meta, {**qa_cnpj, **qa_cnefe}, checks)
    formatos = {f.strip().lower() for f in args.formatos.split(",") if f.strip()}
    gerados = export_result(final, out_path, summary, data_dictionary(), checks, formatos)
    meta["saida"] = gerados
    meta["alertas"] = alertas + gerados.get("alertas", [])
    meta["duracao_segundos"] = round(time.time() - t0, 2)
    return final, meta


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Enriquecimento CNPJ × CNEFE/IBGE auditável (v2.2)")
    p.add_argument("--cnpj", required=True, help="Base CNPJ .xlsx/.csv/.parquet")
    p.add_argument("--ibge", required=True, help="CNEFE municipal .zip/.csv")
    p.add_argument("--out", required=True, help="Workbook .xlsx de saída (Parquet/CSV ao lado)")
    p.add_argument("--sheet", default=None, help="Nome/índice da aba da base CNPJ")
    p.add_argument("--data-referencia", default=str(date.today()))
    p.add_argument("--cnefe-cutoff", default=DEFAULT_CNEFE_CUTOFF)
    p.add_argument("--delta-proximo", type=int, default=DEFAULT_DELTA_PROXIMO)
    p.add_argument("--delta-amplo", type=int, default=DEFAULT_DELTA_AMPLO)
    p.add_argument("--fuzzy-thr", type=int, default=DEFAULT_FUZZY_THR)
    p.add_argument("--fuzzy-margin", type=int, default=DEFAULT_FUZZY_MARGIN)
    p.add_argument("--parity-penalty", type=int, default=DEFAULT_PARITY_PENALTY)
    p.add_argument("--nv-max", type=int, default=DEFAULT_NV_MAX,
                   help="NV do CNEFE considerado nível de endereço; acima disso a confiança é rebaixada")
    p.add_argument("--nv-strict", action="store_true",
                   help="Recusa coordenadas com NV acima de --nv-max em vez de rebaixar")
    p.add_argument("--existence-threshold", type=int, default=82)
    p.add_argument("--accept-invalid-cnpj", action="store_true")
    p.add_argument("--gate-municipio-estrito", action="store_true",
                   help="Falha quando o CNEFE não cobre os CEPs da base CNPJ")
    p.add_argument("--formatos", default="xlsx,parquet,csv",
                   help="Formatos de saída separados por vírgula: xlsx,parquet,csv")
    p.add_argument("--no-strict", action="store_true", help="Registra falhas de QA sem interromper")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        final, meta = run_pipeline(args)
    except Exception as exc:
        print(f"ERRO: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"OK — versão {VERSION} — {len(final):,} registros — {meta['duracao_segundos']}s")
    for a in meta.get("alertas", []):
        print(f"  ALERTA: {a}")
    for f in meta["saida"]["arquivos"]:
        print(f"  Saída: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
