#!/usr/bin/env python3
"""Comparativo v2.1.0 × v2.2.0 sobre a MESMA base — prova de não-regressão.

Roda os dois motores nos mesmos insumos e parâmetros, casa por `_RID` e emite:
  RESUMO_COMPARATIVO   volumetria lado a lado
  MATRIZ_CONFIANCA     transição de MATCH_CONF (v2.1 -> v2.2)
  MATRIZ_PASSE         transição de MATCH_PASS
  DELTA_DECISAO        registros que mudaram POTENCIAL_CRUZAMENTO
  DELTA_COORDENADA     registros que ganharam, perderam ou moveram a coordenada
  DELTA_LINHA_A_LINHA  todo registro com qualquer mudança material

Uso:
  python scripts/comparar_versoes.py --cnpj BASE.xlsx --ibge MUN.zip \
      --out COMPARATIVO.xlsx --data-referencia 2026-08-06
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

from _indice_match import haversine_vec  # noqa: E402
import pipeline_cnpj_ibge as NOVO  # noqa: E402


def _carregar_legado():
    spec = importlib.util.spec_from_file_location("legado_v2_1", AQUI / "_legacy_v2_1_0.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["legado_v2_1"] = mod
    spec.loader.exec_module(mod)
    return mod


COLS_CHAVE = ["MATCH_PASS", "MATCH_CONF", "MATCH_DETALHE", "LATITUDE", "LONGITUDE",
              "NV_GEO_COORD", "POTENCIAL_CRUZAMENTO", "XFERA_EXISTENCIA_FISICA",
              "XFERA_EXIST_SCORE", "CNPJ_VALIDO", "IBGE_FLAG_COMERCIAL"]


def _matriz(a: pd.Series, b: pd.Series, nome_a: str, nome_b: str) -> pd.DataFrame:
    m = pd.crosstab(a.fillna("-"), b.fillna("-"), dropna=False)
    m.index.name = nome_a
    m.columns.name = nome_b
    return m.reset_index()


def comparar(args) -> tuple[pd.DataFrame, dict]:
    legado = _carregar_legado()
    tmp = Path(tempfile.mkdtemp(prefix="cmp_cnpj_"))

    ns_legado = argparse.Namespace(
        cnpj=args.cnpj, ibge=args.ibge, out=str(tmp / "v210.xlsx"), sheet=args.sheet,
        data_referencia=args.data_referencia, cnefe_cutoff=args.cnefe_cutoff,
        delta_proximo=args.delta_proximo, delta_amplo=args.delta_amplo,
        fuzzy_thr=args.fuzzy_thr, fuzzy_margin=args.fuzzy_margin,
        parity_penalty=args.parity_penalty, existence_threshold=args.existence_threshold,
        accept_invalid_cnpj=args.accept_invalid_cnpj, no_strict=True,
    )
    t0 = time.time()
    df_old, meta_old = legado.run_pipeline(ns_legado)
    t_old = time.time() - t0

    ns_novo = argparse.Namespace(
        **vars(ns_legado) | {
            "out": str(tmp / "v220.xlsx"), "nv_max": args.nv_max, "nv_strict": args.nv_strict,
            "gate_municipio_estrito": False, "formatos": "xlsx", "no_strict": True,
        }
    )
    t0 = time.time()
    df_new, meta_new = NOVO.run_pipeline(ns_novo)
    t_new = time.time() - t0

    a = df_old.set_index("_RID")
    b = df_new.set_index("_RID")
    comum = a.index.intersection(b.index)
    a, b = a.loc[comum], b.loc[comum]

    resumo = []

    def linha(ind, va, vb):
        resumo.append({"INDICADOR": ind, "V2_1_0": va, "V2_2_0": vb,
                       "DELTA": (vb - va) if isinstance(va, (int, float, np.integer, np.floating))
                                and isinstance(vb, (int, float, np.integer, np.floating)) else ""})

    linha("REGISTROS", len(a), len(b))
    linha("DURACAO_SEGUNDOS", round(t_old, 2), round(t_new, 2))
    linha("GEOCODIFICADOS", int(a["LATITUDE"].notna().sum()), int(b["LATITUDE"].notna().sum()))
    for c in ["ALTA", "MEDIA", "BAIXA", "SEM_MATCH"]:
        linha(f"MATCH_CONF_{c}", int((a["MATCH_CONF"] == c).sum()), int((b["MATCH_CONF"] == c).sum()))
    linha("POTENCIAL_CRUZAMENTO", int(a["POTENCIAL_CRUZAMENTO"].sum()), int(b["POTENCIAL_CRUZAMENTO"].sum()))
    linha("CNPJ_VALIDO", int(a["CNPJ_VALIDO"].sum()), int(b["CNPJ_VALIDO"].sum()))
    linha("EXISTE_QUASE_CERTO",
          int((a["XFERA_EXISTENCIA_FISICA"] == "EXISTE_QUASE_CERTO").sum()),
          int((b["XFERA_EXISTENCIA_FISICA"] == "EXISTE_QUASE_CERTO").sum()))
    linha("FLAG_COMERCIAL_1",
          int(pd.to_numeric(a["IBGE_FLAG_COMERCIAL"], errors="coerce").fillna(0).eq(1).sum()),
          int(pd.to_numeric(b["IBGE_FLAG_COMERCIAL"], errors="coerce").fillna(0).eq(1).sum()))
    nv_a = pd.to_numeric(a["NV_GEO_COORD"], errors="coerce")
    nv_b = pd.to_numeric(b["NV_GEO_COORD"], errors="coerce")
    linha("ALTA_SOBRE_NV_MAIOR_IGUAL_4",
          int((a["MATCH_CONF"].eq("ALTA") & nv_a.ge(4)).sum()),
          int((b["MATCH_CONF"].eq("ALTA") & nv_b.ge(4)).sum()))
    linha("DESVIO_METROS_DECLARADO_ZERO",
          int(pd.to_numeric(a["DESVIO_METROS"], errors="coerce").eq(0).sum()),
          int(pd.to_numeric(b["DESVIO_METROS"], errors="coerce").eq(0).sum()))
    resumo_df = pd.DataFrame(resumo)

    matriz_conf = _matriz(a["MATCH_CONF"], b["MATCH_CONF"], "V2_1_0", "V2_2_0")
    matriz_pass = _matriz(a["MATCH_PASS"], b["MATCH_PASS"], "V2_1_0", "V2_2_0")

    mudou_dec = a["POTENCIAL_CRUZAMENTO"].ne(b["POTENCIAL_CRUZAMENTO"])
    delta_dec = pd.DataFrame({
        "CNPJ": b["CNPJ_NORM"], "LOGRADOURO": b["LOGR_FULL"], "NUMERO": b["NUM_INT"],
        "POTENCIAL_V21": a["POTENCIAL_CRUZAMENTO"], "POTENCIAL_V22": b["POTENCIAL_CRUZAMENTO"],
        "MOTIVO_V21": a["MOTIVO_SEM_CRUZAMENTO"], "MOTIVO_V22": b["MOTIVO_SEM_CRUZAMENTO"],
    })[mudou_dec].reset_index()

    tem_a, tem_b = a["LATITUDE"].notna(), b["LATITUDE"].notna()
    dist = pd.Series(np.nan, index=a.index)
    ambos = tem_a & tem_b
    if ambos.any():
        dist[ambos] = haversine_vec(
            a.loc[ambos, "LATITUDE"].to_numpy(), a.loc[ambos, "LONGITUDE"].to_numpy(),
            b.loc[ambos, "LATITUDE"].to_numpy(), b.loc[ambos, "LONGITUDE"].to_numpy())
    mudou_coord = (tem_a ^ tem_b) | (dist.fillna(0) > 1.0)
    delta_coord = pd.DataFrame({
        "CNPJ": b["CNPJ_NORM"], "LOGRADOURO": b["LOGR_FULL"], "NUMERO": b["NUM_INT"],
        "SITUACAO": np.where(tem_a & ~tem_b, "PERDEU_COORDENADA",
                    np.where(~tem_a & tem_b, "GANHOU_COORDENADA", "MOVEU")),
        "DISTANCIA_M": dist.round(1),
        "PASS_V21": a["MATCH_PASS"], "PASS_V22": b["MATCH_PASS"],
        "CONF_V21": a["MATCH_CONF"], "CONF_V22": b["MATCH_CONF"],
        "NV_V21": a["NV_GEO_COORD"], "NV_V22": b["NV_GEO_COORD"],
        "NV_CLASSE_V22": b["NV_CLASSE"], "DESVIO_M_V22": b["DESVIO_METROS"],
        "DETALHE_V21": a["MATCH_DETALHE"], "DETALHE_V22": b["MATCH_DETALHE"],
    })[mudou_coord].reset_index()

    mudou = pd.Series(False, index=a.index)
    for c in COLS_CHAVE:
        if c in a.columns and c in b.columns:
            mudou |= a[c].astype(str).ne(b[c].astype(str))
    linha_a_linha = pd.concat([
        a[COLS_CHAVE].add_suffix("_V21"), b[COLS_CHAVE].add_suffix("_V22"),
        b[["CNPJ_NORM", "LOGR_FULL", "NUM_INT", "NV_CLASSE", "DESVIO_METROS",
           "XFERA_ROTA_TRATAMENTO", "XFERA_IND_ESTAB_IBGE" if "XFERA_IND_ESTAB_IBGE" in b.columns
           else "IBGE_IND_ESTAB"]],
    ], axis=1)[mudou].reset_index()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="xlsxwriter",
                        engine_kwargs={"options": {"strings_to_formulas": False}}) as w:
        resumo_df.to_excel(w, sheet_name="RESUMO_COMPARATIVO", index=False)
        matriz_conf.to_excel(w, sheet_name="MATRIZ_CONFIANCA", index=False)
        matriz_pass.to_excel(w, sheet_name="MATRIZ_PASSE", index=False)
        delta_dec.head(1_000_000).to_excel(w, sheet_name="DELTA_DECISAO", index=False)
        delta_coord.head(1_000_000).to_excel(w, sheet_name="DELTA_COORDENADA", index=False)
        linha_a_linha.head(1_000_000).to_excel(w, sheet_name="DELTA_LINHA_A_LINHA", index=False)
        h = w.book.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        for nome, dados in [("RESUMO_COMPARATIVO", resumo_df), ("MATRIZ_CONFIANCA", matriz_conf),
                            ("MATRIZ_PASSE", matriz_pass), ("DELTA_DECISAO", delta_dec),
                            ("DELTA_COORDENADA", delta_coord), ("DELTA_LINHA_A_LINHA", linha_a_linha)]:
            ws = w.sheets[nome]
            for i, c in enumerate(dados.columns):
                ws.write(0, i, str(c), h)
            ws.freeze_panes(1, 0)
            if len(dados.columns):
                ws.set_column(0, len(dados.columns) - 1, 20)

    return resumo_df, {
        "arquivo": str(out), "n_mudou": int(mudou.sum()),
        "n_delta_decisao": len(delta_dec), "n_delta_coord": len(delta_coord),
        "t_old": t_old, "t_new": t_new,
    }


def build_parser():
    p = argparse.ArgumentParser(description="Comparativo v2.1.0 × v2.2.0")
    p.add_argument("--cnpj", required=True)
    p.add_argument("--ibge", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--sheet", default=None)
    p.add_argument("--data-referencia", default="2026-08-06")
    p.add_argument("--cnefe-cutoff", default=NOVO.DEFAULT_CNEFE_CUTOFF)
    p.add_argument("--delta-proximo", type=int, default=NOVO.DEFAULT_DELTA_PROXIMO)
    p.add_argument("--delta-amplo", type=int, default=NOVO.DEFAULT_DELTA_AMPLO)
    p.add_argument("--fuzzy-thr", type=int, default=NOVO.DEFAULT_FUZZY_THR)
    p.add_argument("--fuzzy-margin", type=int, default=NOVO.DEFAULT_FUZZY_MARGIN)
    p.add_argument("--parity-penalty", type=int, default=NOVO.DEFAULT_PARITY_PENALTY)
    p.add_argument("--nv-max", type=int, default=NOVO.DEFAULT_NV_MAX)
    p.add_argument("--nv-strict", action="store_true")
    p.add_argument("--existence-threshold", type=int, default=82)
    p.add_argument("--accept-invalid-cnpj", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    resumo, info = comparar(args)
    print(resumo.to_string(index=False))
    print(f"\nregistros com mudança material: {info['n_mudou']:,}")
    print(f"mudaram de decisão: {info['n_delta_decisao']:,} | mudaram de coordenada: {info['n_delta_coord']:,}")
    print(f"tempo v2.1={info['t_old']:.1f}s  v2.2={info['t_new']:.1f}s")
    print(f"Saída: {info['arquivo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
