#!/usr/bin/env python3
"""Selftest v2.2 — um caso por achado da auditoria, nos DEFAULTS DE PRODUÇÃO.

Regra do gate: cada caso aqui falha na v2.1.0 e passa na v2.2.0, ou é regressão
de comportamento que a v2.1 já acertava e precisa continuar acertando.
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

import pipeline_cnpj_ibge as P  # noqa: E402
from _indice_match import NV_SINTETICO, street_similarity, StreetEntry, MatchConfig  # noqa: E402
from _io_fontes import apply_aliases, CNPJ_ALIASES  # noqa: E402
from _normalizacao import normalize_cnpj, normalize_logradouro, parse_numero  # noqa: E402
from _saida import validate_output  # noqa: E402

OK, FALHOU = [], []


def caso(nome):
    def deco(fn):
        try:
            fn()
            OK.append(nome)
        except AssertionError as exc:
            FALHOU.append(f"{nome}: {exc}")
        except Exception as exc:  # erro inesperado também é falha
            FALHOU.append(f"{nome}: {type(exc).__name__}: {exc}")
        return fn
    return deco


CNEFE_COLS = ["COD_UNICO_ENDERECO", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR",
              "NUM_ENDERECO", "DSC_MODIFICADOR", "CEP", "NV_GEO_COORD", "LATITUDE", "LONGITUDE",
              "COD_ESPECIE", "DSC_ESTABELECIMENTO", "COD_INDICADOR_ESTAB_ENDERECO",
              "COD_MUNICIPIO", "NUM_FACE"]


def cnpj_valido(base12: str) -> str:
    def dv(b, w):
        t = sum((ord(c) - 48) * x for c, x in zip(b, w))
        r = t % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + d1 + d2


def rodar(cnpj_rows, cnefe_rows, tmp: Path, **over):
    cnpj_path = tmp / "cnpj.xlsx"
    pd.DataFrame(cnpj_rows).to_excel(cnpj_path, index=False)
    csv = tmp / "cnefe.csv"
    pd.DataFrame(cnefe_rows, columns=CNEFE_COLS).to_csv(csv, sep=";", index=False, encoding="utf-8")
    zp = tmp / "cnefe.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.write(csv, arcname="municipio.csv")
    args = argparse.Namespace(
        cnpj=str(cnpj_path), ibge=str(zp), out=str(tmp / "out.xlsx"), sheet=None,
        data_referencia="2026-08-06", cnefe_cutoff=P.DEFAULT_CNEFE_CUTOFF,
        delta_proximo=P.DEFAULT_DELTA_PROXIMO, delta_amplo=P.DEFAULT_DELTA_AMPLO,
        fuzzy_thr=P.DEFAULT_FUZZY_THR, fuzzy_margin=P.DEFAULT_FUZZY_MARGIN,
        parity_penalty=P.DEFAULT_PARITY_PENALTY, nv_max=P.DEFAULT_NV_MAX, nv_strict=False,
        existence_threshold=82, accept_invalid_cnpj=False, gate_municipio_estrito=False,
        formatos="xlsx,parquet", no_strict=False,
    )
    for k, v in over.items():
        setattr(args, k, v)
    return P.run_pipeline(args)


def emp(cnpj, **kw):
    d = dict(cnpj=cnpj, situacao="ATIVA", logradouro="RUA DOUTOR FLORES", numero="100",
             cep="97010140", cnae="4711302", nome_fantasia="MERCADO FLORES",
             razao_social="MERCADO FLORES LTDA", tipo="Matriz", data_abertura="01/02/2020",
             natureza_juridica="213-5", capital_social="R$ 1.000,50", complemento="LOJA",
             municipio="SANTA MARIA", uf="RS")
    d.update(kw)
    return d


def end(cod, nome, num, cep, nv, lat, lon, esp, dsc="", tipo="RUA", mod="", ind="", face="1"):
    return [cod, tipo, "", nome, num, mod, cep, nv, lat, lon, esp, dsc, ind, "4318705", face]


# ---------------------------------------------------------------------------
@caso("F-01 NV=6 (setor censitario) nunca sai como ALTA")
def _f01():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"))],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "6", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                      tmp)
        r = df.iloc[0]
        assert r["MATCH_PASS"] == "P1_EXATO", r["MATCH_PASS"]
        assert r["MATCH_CONF"] == "BAIXA", f"conf={r['MATCH_CONF']}"
        assert r["NV_CLASSE"] == "SETOR_CENSITARIO", r["NV_CLASSE"]
        assert "TETO_POR_NV" in str(r["CONF_MOTIVO"])


@caso("F-01b --nv-strict recusa a coordenada de baixa precisao")
def _f01b():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"))],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "6", "-29.69", "-53.80", "6", "X")],
                      tmp, nv_strict=True)
        assert df.iloc[0]["MATCH_CONF"] == "SEM_MATCH", df.iloc[0]["MATCH_CONF"]


@caso("F-02 desvio em metros e' declarado e cresce com o delta")
def _f02():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar(
            [emp(cnpj_valido("123456780001"), numero="100"),
             emp(cnpj_valido("123456780002"), numero="180")],
            [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.6900", "-53.8000", "6", "MERCADO FLORES"),
             end("A2", "DOUTOR FLORES", "120", "97010140", "1", "-29.6910", "-53.8000", "1"),
             end("A3", "DOUTOR FLORES", "140", "97010140", "1", "-29.6920", "-53.8000", "1")],
            tmp)
        exato, proximo = df.iloc[0], df.iloc[1]
        assert exato["DESVIO_METROS"] > 0, "match exato deve declarar o piso do NV"
        assert proximo["NUM_DELTA"] > 0 and proximo["DESVIO_METROS"] > exato["DESVIO_METROS"], \
            f"{proximo['DESVIO_METROS']} <= {exato['DESVIO_METROS']}"
        assert proximo["DESVIO_FONTE"] == "PISO_NV+INTERPOLACAO", proximo["DESVIO_FONTE"]


@caso("F-03 centroide sintetico usa NV=90, fora do dominio oficial")
def _f03():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), logradouro="RUA SEM NUMERO",
                           numero="S/N", cep="97030000")],
                      [end("C1", "SEM NUMERO", "10", "97030000", "1", "-29.67", "-53.82", "6", "BAR"),
                       end("C2", "SEM NUMERO", "20", "97030000", "1", "-29.671", "-53.821", "1")],
                      tmp)
        r = df.iloc[0]
        assert r["MATCH_PASS"] == "P8_CENTROIDE_SN", r["MATCH_PASS"]
        assert int(r["NV_GEO_COORD"]) == NV_SINTETICO == 90, r["NV_GEO_COORD"]
        assert r["NV_CLASSE"] == "SINTETICO_LOGRADOURO"
        assert pd.isna(r["IBGE_FLAG_COMERCIAL"]), "centroide nao herda evidencia de numero"
        assert r["DESVIO_METROS"] >= 60.0, f"centroide nunca declara menos que uma face de quadra: {r['DESVIO_METROS']}"


@caso("F-05 especie 7 (obra) nao e' estabelecimento; especie 8 (religioso) e'")
def _f05():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar(
            [emp(cnpj_valido("123456780001"), numero="100", nome_fantasia="X", razao_social="X"),
             emp(cnpj_valido("123456780002"), numero="200", nome_fantasia="Y", razao_social="Y")],
            [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "7"),
             end("A2", "DOUTOR FLORES", "200", "97010140", "1", "-29.691", "-53.801", "8", "IGREJA CENTRAL")],
            tmp)
        obra, templo = df.iloc[0], df.iloc[1]
        assert int(obra["IBGE_FLAG_COMERCIAL"]) == 0, "obra nao e' estabelecimento"
        assert int(templo["IBGE_FLAG_COMERCIAL"]) == 1, "templo e' estabelecimento"


@caso("F-06 condominio residencial nomeado nao vira estabelecimento")
def _f06():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), nome_fantasia="X", razao_social="X")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80",
                           "2", "EDIFICIO PORTAL DO SOL")],
                      tmp)
        assert int(df.iloc[0]["IBGE_FLAG_COMERCIAL"]) == 0, "especie 2 com nome nao e' estabelecimento"


@caso("F-07 o indicador oficial do IBGE decide a rota, nao a contagem de linhas")
def _f07():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        linhas = [end("T1", "TOMAZ EDISON", "3500", "93025674", "1", "-29.75", "-51.14", "6",
                      "LEO ART TATTOO", tipo="AVENIDA", ind="1")]
        linhas += [end(f"D{i}", "TOMAZ EDISON", "3500", "93025674", "2", "-29.75", "-51.14", "1",
                       tipo="AVENIDA") for i in range(60)]
        df, _ = rodar([emp(cnpj_valido("123456780001"), logradouro="AVENIDA TOMAZ EDISON",
                           numero="3500", cep="93025674", nome_fantasia="LEO ART TATTOO",
                           razao_social="LEO ART TATTOO", cnae="9602501")], linhas, tmp)
        r = df.iloc[0]
        assert r["IBGE_QTD_UNIDADES"] == 61, r["IBGE_QTD_UNIDADES"]
        assert r["IBGE_N_ESTAB"] == 1 and r["IBGE_N_DOMICILIO"] == 60
        assert r["XFERA_PERFIL_ENDERECO"] == "ESTAB_UNICO", r["XFERA_PERFIL_ENDERECO"]
        assert r["XFERA_ROTA_TRATAMENTO"] == "RECLASSIFICACAO_1_1", r["XFERA_ROTA_TRATAMENTO"]
        assert bool(r["XFERA_ENDERECO_MISTO"])


@caso("F-09 fuzzy recusa subconjunto (RUA BRASIL x RUA BRASIL NOVO)")
def _f09():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), logradouro="RUA BRASIL",
                           numero="150", cep="97050000")],
                      [end("Y1", "BRASIL NOVO", "150", "97050000", "1", "-29.70", "-53.90", "6", "OUTRA"),
                       end("Y2", "PRESIDENTE VARGAS", "150", "97050000", "1", "-29.701", "-53.901", "1")],
                      tmp)
        r = df.iloc[0]
        assert r["MATCH_CONF"] == "SEM_MATCH", f"{r['MATCH_CONF']} / {r['LOGR_MATCH']}"
    # e o typo legitimo continua passando
    e = StreetEntry("97020000", "AVENIDA", "RIO BRANCO", "AVENIDA RIO BRANCO", "RIO BRANCO", 2)
    a = normalize_logradouro("AVENIDA RIO BRNCO")
    s = street_similarity(a.tipo, a.base, a.nucleo, a.n_tokens, e)
    assert s >= P.DEFAULT_FUZZY_THR, f"typo legitimo deveria passar: {s}"


@caso("F-10 CEP fora do indice tem motivo proprio")
def _f10():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), cep="99999999")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "1")],
                      tmp)
        assert df.iloc[0]["MATCH_DETALHE"] == "SEM_CEP_NO_INDICE", df.iloc[0]["MATCH_DETALHE"]


@caso("F-11 sufixo do numero entra na chave de match")
def _f11():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), numero="100A", nome_fantasia="X", razao_social="X")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.6900", "-53.8000", "1"),
                       end("A2", "DOUTOR FLORES", "100", "97010140", "1", "-29.6950", "-53.8050", "6",
                           "LOJA A", mod="A")],
                      tmp)
        r = df.iloc[0]
        assert r["NUM_SUFIXO_MATCH"] == "A", r["NUM_SUFIXO_MATCH"]
        assert r["COD_UNICO_ENDERECO"] == "A2", r["COD_UNICO_ENDERECO"]


@caso("F-12 tipo_logradouro em coluna separada e' usado no match exato")
def _f12():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), logradouro="BRASIL",
                           tipo_logradouro="AVENIDA", numero="10", cep="97040000",
                           nome_fantasia="X", razao_social="X")],
                      [end("D1", "BRASIL", "10", "97040000", "1", "-29.66", "-53.83", "1", tipo="RUA"),
                       end("D2", "BRASIL", "10", "97040000", "1", "-29.65", "-53.84", "6", "X",
                           tipo="AVENIDA")],
                      tmp)
        r = df.iloc[0]
        assert r["MATCH_PASS"] == "P1_EXATO", r["MATCH_PASS"]
        assert r["COD_UNICO_ENDERECO"] == "D2", r["COD_UNICO_ENDERECO"]


@caso("F-13 coluna TIPO com tipos de logradouro e' remapeada, nao lida como matriz/filial")
def _f13():
    d = pd.DataFrame({"CNPJ": ["1"], "SITUACAO": ["ATIVA"], "TIPO": ["AVENIDA"],
                      "LOGRADOURO": ["BRASIL"], "NUMERO": ["10"], "CEP": ["97040000"], "CNAE": ["4711302"]})
    out, _, alertas = apply_aliases(d, CNPJ_ALIASES)
    assert "tipo_logradouro" in out.columns, out.columns.tolist()
    assert alertas, "a remapeacao deve gerar alerta"


@caso("F-14 '0' e '00' têm o mesmo status")
def _f14():
    assert parse_numero("0")[1] == parse_numero("00")[1] == parse_numero("000")[1] == "SEM_NUMERO_ZERO"
    assert parse_numero("S/N")[1] == "SEM_NUMERO"
    # e o zero segue para o centroide, como S/N, em vez de virar sem-match
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), numero="0", cep="97030000",
                           logradouro="RUA SEM NUMERO")],
                      [end("C1", "SEM NUMERO", "10", "97030000", "1", "-29.67", "-53.82", "6", "BAR"),
                       end("C2", "SEM NUMERO", "20", "97030000", "1", "-29.671", "-53.821", "1")],
                      tmp)
        assert df.iloc[0]["MATCH_PASS"] == "P8_CENTROIDE_SN", df.iloc[0]["MATCH_PASS"]


@caso("F-19 base acima do limite do Excel nao perde linha em silencio")
def _f19():
    from _saida import EXCEL_MAX_ROWS
    assert EXCEL_MAX_ROWS == 1_048_576
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, meta = rodar([emp(cnpj_valido("123456780001"))],
                        [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                        tmp)
        assert "linhas_reconferidas" in meta["saida"], "o xlsx gravado deve ser reconferido"
        assert meta["saida"]["linhas_reconferidas"] == 1
        assert any(a.endswith(".parquet") for a in meta["saida"]["arquivos"]), "parquet obrigatorio"


@caso("F-21 os parametros do motor e os hashes ficam no RESUMO_EXECUCAO")
def _f21():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, meta = rodar([emp(cnpj_valido("123456780001"))],
                        [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                        tmp)
        resumo = pd.read_excel(tmp / "out.xlsx", sheet_name="RESUMO_EXECUCAO")
        ind = set(resumo["INDICADOR"])
        for k in ["PARAM_FUZZY_THR", "PARAM_FUZZY_MARGIN", "PARAM_DELTA_PROXIMO", "PARAM_DELTA_AMPLO",
                  "PARAM_PARITY_PENALTY", "PARAM_NV_MAX", "PARAM_EXISTENCE_THRESHOLD",
                  "SHA256_CNPJ", "SHA256_CNEFE", "ENCODING_CNEFE"]:
            assert k in ind, f"faltou {k}"


@caso("F-24 coluna obrigatoria ausente vira FALHA de QA, nao KeyError")
def _f24():
    df = pd.DataFrame({
        "_RID": [0, 1], "LATITUDE": [None, None], "LONGITUDE": [None, None],
        "NUM_DELTA": [pd.NA, pd.NA], "MATCH_PASS": ["SEM_MATCH"] * 2, "MATCH_CONF": ["SEM_MATCH"] * 2,
        "NUM_INT": [0, 0], "NV_GEO_COORD": [pd.NA, pd.NA], "DESVIO_METROS": [None, None],
        "POTENCIAL_CRUZAMENTO": [False, False], "SITUACAO_NORM": ["ATIVA"] * 2,
    })
    checks = validate_output(df, 2, strict=False)
    por_nome = {c["CHECK"]: c["STATUS"] for c in checks}
    assert por_nome["COLUNAS_OBRIGATORIAS_PRESENTES"] == "FALHA", por_nome


@caso("F-26 CNPJ alfanumerico e' valido e entra em POTENCIAL_CRUZAMENTO")
def _f26():
    norm, ok, status, fmt = normalize_cnpj("12.ABC.345/01DE-35")
    assert ok and fmt == "ALFANUMERICO", (norm, ok, status, fmt)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp("12.ABC.345/01DE-35")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                      tmp)
        r = df.iloc[0]
        assert bool(r["CNPJ_VALIDO"]), r["CNPJ_STATUS"]
        assert bool(r["POTENCIAL_CRUZAMENTO"])


@caso("F-27/F-28/F-29 dado ausente nao vira sinal negativo silencioso")
def _f29():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), data_abertura="", capital_social="")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                      tmp)
        r = df.iloc[0]
        assert "DATA_NAO_AVALIADA" in r["SCORE_ESTRUTURA_NAO_AVALIADO"]
        assert "CAPITAL_NAO_AVALIADO" in r["SCORE_ESTRUTURA_NAO_AVALIADO"]
        assert "RECENTE" not in r["SCORE_ESTRUTURA_MOTIVOS"]


@caso("REGRESSAO v2.1: dedup escolhe a melhor coordenada, nao a ultima linha")
def _reg_dedup():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"))],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.6900", "-53.8000", "6", "MERCADO FLORES"),
                       end("A2", "DOUTOR FLORES", "100", "97010140", "3", "-29.6000", "-53.7000", "1")],
                      tmp)
        r = df.iloc[0]
        assert int(r["NV_GEO_COORD"]) == 1 and abs(float(r["LATITUDE"]) + 29.69) < 1e-9


@caso("REGRESSAO v2.1: RUA BRASIL x AVENIDA BRASIL no mesmo CEP fica ambiguo")
def _reg_ambiguo():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), logradouro="TRAVESSA BRASIL",
                           numero="10", cep="97040000", nome_fantasia="T", razao_social="T")],
                      [end("D1", "BRASIL", "10", "97040000", "1", "-29.66", "-53.83", "1", tipo="RUA"),
                       end("D2", "BRASIL", "10", "97040000", "1", "-29.65", "-53.84", "1", tipo="AVENIDA")],
                      tmp)
        r = df.iloc[0]
        assert r["MATCH_CONF"] == "SEM_MATCH" and "AMBIGUO" in r["MATCH_DETALHE"], r["MATCH_DETALHE"]


@caso("REGRESSAO v2.1: CNPJ invalido e inativa continuam fora do potencial")
def _reg_decisao():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp("11111111111111"), emp(cnpj_valido("123456780002"), situacao="Baixada")],
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                      tmp)
        assert not bool(df.iloc[0]["POTENCIAL_CRUZAMENTO"])
        assert df.iloc[0]["MOTIVO_SEM_CRUZAMENTO"].startswith("CNPJ_INVALIDO")
        assert not bool(df.iloc[1]["POTENCIAL_CRUZAMENTO"])
        assert df.iloc[1]["MOTIVO_SEM_CRUZAMENTO"].startswith("NAO_ATIVA")


@caso("REGRESSAO v2.1: paridade prefere 104 a 101 para o numero 102")
def _reg_paridade():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        df, _ = rodar([emp(cnpj_valido("123456780001"), numero="102", nome_fantasia="B", razao_social="B")],
                      [end("A3", "DOUTOR FLORES", "101", "97010140", "1", "-29.6901", "-53.8001", "1"),
                       end("A4", "DOUTOR FLORES", "104", "97010140", "1", "-29.6902", "-53.8002", "6", "LOJA B")],
                      tmp)
        r = df.iloc[0]
        assert int(r["NUM_IBGE"]) == 104 and int(r["PARIDADE_DIVERGENTE"]) == 0


@caso("REGRESSAO v2.1: seis abas, _RID unico e ordem preservada")
def _reg_saida():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [emp(cnpj_valido(f"12345678{i:04d}")) for i in range(1, 6)]
        df, _ = rodar(rows,
                      [end("A1", "DOUTOR FLORES", "100", "97010140", "1", "-29.69", "-53.80", "6", "MERCADO FLORES")],
                      tmp)
        assert df["_RID"].tolist() == list(range(5))
        assert set(pd.ExcelFile(tmp / "out.xlsx").sheet_names) == {
            "RESUMO_EXECUCAO", "QA_ACEITE", "DICIONARIO",
            "POTENCIAL_CRUZAMENTO", "SEM_POTENCIAL", "BASE_COMPLETA"}


@caso("DETERMINISMO: duas execucoes identicas produzem a mesma saida")
def _determinismo():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [emp(cnpj_valido(f"12345678{i:04d}"), numero=str(100 + i * 3)) for i in range(1, 12)]
        cnefe = [end(f"A{i}", "DOUTOR FLORES", str(100 + i * 2), "97010140", "1",
                     f"-29.69{i:02d}", f"-53.80{i:02d}", "6" if i % 2 else "1",
                     f"LOJA {i}" if i % 2 else "") for i in range(1, 15)]
        a, _ = rodar(rows, cnefe, tmp)
        b, _ = rodar(rows, cnefe, tmp)
        cols = ["MATCH_PASS", "MATCH_CONF", "LATITUDE", "LONGITUDE", "DESVIO_METROS",
                "POTENCIAL_CRUZAMENTO", "XFERA_EXIST_SCORE"]
        pd.testing.assert_frame_equal(a[cols], b[cols])


if __name__ == "__main__":
    print(f"selftest v{P.VERSION} — defaults de producao "
          f"(fuzzy_thr={P.DEFAULT_FUZZY_THR}, nv_max={P.DEFAULT_NV_MAX})\n")
    for n in OK:
        print(f"  PASSOU   {n}")
    for f in FALHOU:
        print(f"  FALHOU   {f}")
    print(f"\n{len(OK)} passaram, {len(FALHOU)} falharam")
    raise SystemExit(1 if FALHOU else 0)
