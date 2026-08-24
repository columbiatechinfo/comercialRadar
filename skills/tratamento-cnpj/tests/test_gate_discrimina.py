#!/usr/bin/env python3
"""Prova de discriminação do gate: os mesmos cenários rodados na v2.1.0 vendorizada.

Um caso que passa nas duas versões não prova correção nenhuma. Este teste roda
o motor ANTIGO nos cenários do selftest e exige que ele falhe onde deveria.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("legado", ROOT / "scripts" / "_legacy_v2_1_0.py")
LEG = importlib.util.module_from_spec(spec)
sys.modules["legado"] = LEG
spec.loader.exec_module(LEG)

CNEFE_COLS_V21 = ["COD_UNICO_ENDERECO", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR",
                  "NUM_ENDERECO", "CEP", "NV_GEO_COORD", "LATITUDE", "LONGITUDE",
                  "COD_ESPECIE", "DSC_ESTABELECIMENTO"]


def cnpj_valido(base12):
    def dv(b, w):
        t = sum(int(c) * x for c, x in zip(b, w)); r = t % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + d1 + dv(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def rodar_v21(cnpj_rows, cnefe_rows, tmp):
    p = tmp / "c.xlsx"; pd.DataFrame(cnpj_rows).to_excel(p, index=False)
    csv = tmp / "n.csv"; pd.DataFrame(cnefe_rows, columns=CNEFE_COLS_V21).to_csv(csv, sep=";", index=False)
    zp = tmp / "n.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(csv, arcname="m.csv")
    ns = argparse.Namespace(cnpj=str(p), ibge=str(zp), out=str(tmp / "o.xlsx"), sheet=None,
                            data_referencia="2026-08-06", cnefe_cutoff="2022-08-01",
                            delta_proximo=20, delta_amplo=100, fuzzy_thr=90, fuzzy_margin=5,
                            parity_penalty=3, existence_threshold=82,
                            accept_invalid_cnpj=False, no_strict=True)
    return LEG.run_pipeline(ns)[0]


def emp(cnpj, **kw):
    d = dict(cnpj=cnpj, situacao="ATIVA", logradouro="RUA DOUTOR FLORES", numero="100",
             cep="97010140", cnae="4711302", nome_fantasia="MERCADO FLORES",
             razao_social="MERCADO FLORES LTDA", tipo="Matriz", data_abertura="01/02/2020",
             natureza_juridica="2135", capital_social="1000", complemento="LOJA",
             municipio="SANTA MARIA", uf="RS")
    d.update(kw); return d


def end(cod, nome, num, cep, nv, lat, lon, esp, dsc="", tipo="RUA"):
    return [cod, tipo, "", nome, num, cep, nv, lat, lon, esp, dsc]


RESULT = []


def checar(nome, condicao_defeito, detalhe=""):
    RESULT.append((nome, bool(condicao_defeito), detalhe))


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)

    df = rodar_v21([emp(cnpj_valido("123456780001"))],
                   [end("A1", "DOUTOR FLORES", "100", "97010140", "6", "-29.69", "-53.80", "6", "MERCADO FLORES")], tmp)
    r = df.iloc[0]
    checar("F-01 v2.1 emite ALTA sobre NV=6", r["MATCH_CONF"] == "ALTA",
           f"conf={r['MATCH_CONF']} nv={r['NV_GEO_COORD']} desvio={r['DESVIO_METROS']}")

    checar("F-02 v2.1 declara desvio 0.0 em match posicional",
           float(r["DESVIO_METROS"]) == 0.0, f"desvio={r['DESVIO_METROS']}")

    df = rodar_v21([emp(cnpj_valido("123456780001"), logradouro="RUA SEM NUMERO", numero="S/N", cep="97030000")],
                   [end("C1", "SEM NUMERO", "10", "97030000", "1", "-29.67", "-53.82", "6", "BAR"),
                    end("C2", "SEM NUMERO", "20", "97030000", "1", "-29.671", "-53.821", "1")], tmp)
    checar("F-03 v2.1 grava NV=4 (face de quadra) no centroide sintetico",
           int(df.iloc[0]["NV_GEO_COORD"]) == 4, f"nv={df.iloc[0]['NV_GEO_COORD']}")

    df = rodar_v21([emp(cnpj_valido("123456780001"), numero="100", nome_fantasia="X", razao_social="X"),
                    emp(cnpj_valido("123456780002"), numero="200", nome_fantasia="Y", razao_social="Y")],
                   [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "7"),
                    end("A2", "DOUTOR FLORES", "200", "97010140", "1", "-29.691", "-53.801", "8", "IGREJA CENTRAL")], tmp)
    checar("F-05 v2.1 marca obra (especie 7) como comercial",
           int(df.iloc[0]["IBGE_FLAG_COMERCIAL"]) == 1, f"flag={df.iloc[0]['IBGE_FLAG_COMERCIAL']}")

    df = rodar_v21([emp(cnpj_valido("123456780001"), nome_fantasia="X", razao_social="X")],
                   [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "2", "EDIFICIO PORTAL DO SOL")], tmp)
    checar("F-06 v2.1 marca condominio nomeado (especie 2) como comercial",
           int(df.iloc[0]["IBGE_FLAG_COMERCIAL"]) == 1, f"flag={df.iloc[0]['IBGE_FLAG_COMERCIAL']}")

    linhas = [end("T1", "TOMAZ EDISON", "3500", "93025674", "1", "-29.75", "-51.14", "6", "LEO ART TATTOO", tipo="AVENIDA")]
    linhas += [end(f"D{i}", "TOMAZ EDISON", "3500", "93025674", "2", "-29.75", "-51.14", "1", tipo="AVENIDA") for i in range(60)]
    df = rodar_v21([emp(cnpj_valido("123456780001"), logradouro="AVENIDA TOMAZ EDISON", numero="3500",
                        cep="93025674", nome_fantasia="LEO ART TATTOO", razao_social="LEO ART TATTOO", cnae="9602501")],
                   linhas, tmp)
    r = df.iloc[0]
    checar("F-07 v2.1 nao tem rota nem indicador oficial; so QTD=61",
           ("XFERA_ROTA_TRATAMENTO" not in df.columns) and int(r["IBGE_QTD_UNIDADES"]) == 61,
           f"qtd={r['IBGE_QTD_UNIDADES']}, sem coluna de rota")

    df = rodar_v21([emp(cnpj_valido("123456780001"), logradouro="RUA BRASIL", numero="150", cep="97050000")],
                   [end("Y1", "BRASIL NOVO", "150", "97050000", "1", "-29.70", "-53.90", "6", "OUTRA"),
                    end("Y2", "PRESIDENTE VARGAS", "150", "97050000", "1", "-29.701", "-53.901", "1")], tmp)
    r = df.iloc[0]
    checar("F-09 v2.1 casa RUA BRASIL com RUA BRASIL NOVO (score 100)",
           r["MATCH_CONF"] != "SEM_MATCH", f"casou com {r['LOGR_MATCH']} score={r['LOGR_SCORE']}")

    df = rodar_v21([emp(cnpj_valido("123456780001"), cep="99999999")],
                   [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "1")], tmp)
    checar("F-10 v2.1 rotula CEP ausente como SEM_LOGRADOURO",
           df.iloc[0]["MATCH_DETALHE"] == "SEM_LOGRADOURO", f"detalhe={df.iloc[0]['MATCH_DETALHE']}")

    df = rodar_v21([emp(cnpj_valido("123456780001"), logradouro="BRASIL", numero="10", cep="97040000",
                        nome_fantasia="X", razao_social="X")],
                   [end("D1", "BRASIL", "10", "97040000", "1", "-29.66", "-53.83", "1", tipo="RUA"),
                    end("D2", "BRASIL", "10", "97040000", "1", "-29.65", "-53.84", "6", "X", tipo="AVENIDA")], tmp)
    checar("F-12 v2.1 sem tipo_logradouro: nao resolve, fica ambiguo",
           df.iloc[0]["MATCH_CONF"] == "SEM_MATCH", f"detalhe={df.iloc[0]['MATCH_DETALHE']}")

    checar("F-14 v2.1 trata '0' e '00' de formas diferentes",
           LEG.parse_numero("0")[1] != LEG.parse_numero("00")[1],
           f"'0'->{LEG.parse_numero('0')[1]} '00'->{LEG.parse_numero('00')[1]}")

    n, ok, st = LEG.normalize_cnpj("12.ABC.345/01DE-35")
    checar("F-26 v2.1 destroi CNPJ alfanumerico", not ok, f"-> {n} / {st}")

    try:
        LEG.validate_output(pd.DataFrame({
            "_RID": [0], "LATITUDE": [None], "LONGITUDE": [None], "NUM_DELTA": [pd.NA],
            "MATCH_PASS": ["SEM_MATCH"], "NUM_INT": [0], "POTENCIAL_CRUZAMENTO": [False],
            "SITUACAO_NORM": ["ATIVA"]}), 1, strict=False)
        erro = ""
    except Exception as exc:
        erro = f"{type(exc).__name__}: {exc}"
    checar("F-24 v2.1 levanta KeyError no QA", erro.startswith("KeyError"), erro)

print("Discriminação do gate — cenários rodados no motor v2.1.0 vendorizado\n")
falhas = 0
for nome, defeito_presente, det in RESULT:
    marca = "DEFEITO CONFIRMADO" if defeito_presente else "NAO REPRODUZIU  "
    if not defeito_presente:
        falhas += 1
    print(f"  {marca}  {nome}")
    if det:
        print(f"                      {det}")
print(f"\n{len(RESULT) - falhas}/{len(RESULT)} defeitos confirmados na v2.1")
raise SystemExit(1 if falhas else 0)
