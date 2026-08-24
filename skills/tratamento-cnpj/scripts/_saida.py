#!/usr/bin/env python3
"""QA, dicionário e exportação — v2.2.

Mudanças sobre a v2.1:
- F-24  coluna obrigatória ausente vira FALHA de QA, não KeyError.
- F-25  o check de nulos passa a enxergar sentinelas de string ('nan', 'None', '').
- F-19  guarda do limite físico do Excel + reconferência do arquivo gravado.
- F-20  Parquet e CSV gerados ao lado do xlsx (contrato de handoff).
- F-21  parâmetros do motor, hashes e encodings no RESUMO_EXECUCAO.
- F-01/F-02  novos checks: nenhuma ALTA com NV>=4; desvio coerente com o passe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from _indice_match import in_brazil_bbox, valid_coord

EXCEL_MAX_ROWS = 1_048_576
SENTINELAS = {"", "nan", "NaN", "None", "<NA>", "NAT"}

COLUNAS_OBRIGATORIAS_SAIDA = [
    "SEGMENTO", "PORTE_CAPITAL", "PAPEL_ESTRUTURAL", "PERFIL_EMPRESA",
    "CONFIANCA_ESTRUTURA", "SCORE_ESTRUTURA", "APTIDAO_CRUZAMENTO",
    "POTENCIAL_CRUZAMENTO", "PERFIL_COMERCIAL", "XFERA_ROTA_TRATAMENTO",
    "XFERA_PERFIL_ENDERECO",
]

RENAME_SAIDA = {
    "LATITUDE": "XFERA_LAT", "LONGITUDE": "XFERA_LON", "NV_GEO_COORD": "XFERA_NV_GEO",
    "NV_CLASSE": "XFERA_NV_CLASSE", "COD_UNICO_ENDERECO": "XFERA_COD_IBGE",
    "NUM_FACE": "XFERA_NUM_FACE", "MATCH_PASS": "XFERA_MATCH_PASS",
    "MATCH_CONF": "XFERA_MATCH_CONF", "MATCH_DETALHE": "XFERA_MATCH_DETALHE",
    "CONF_MOTIVO": "XFERA_CONF_MOTIVO", "NUM_DELTA": "XFERA_NUM_DELTA",
    "NUM_IBGE": "XFERA_NUM_IBGE", "NUM_SUFIXO_MATCH": "XFERA_NUM_SUFIXO_MATCH",
    "PARIDADE_DIVERGENTE": "XFERA_PARIDADE_DIVERGENTE", "LOGR_MATCH": "XFERA_LOGR_MATCH",
    "LOGR_SCORE": "XFERA_LOGR_SCORE", "LOGR_SCORE_SEGUNDO": "XFERA_LOGR_SCORE_SEGUNDO",
    "LOGR_CANDIDATOS": "XFERA_LOGR_CANDIDATOS", "DESVIO_METROS": "XFERA_DESVIO_METROS",
    "DESVIO_FONTE": "XFERA_DESVIO_FONTE", "IBGE_FLAG_COMERCIAL": "XFERA_FLAG_COMERCIAL",
    "IBGE_IND_ESTAB": "XFERA_IND_ESTAB_IBGE", "IBGE_N_ESTAB": "XFERA_N_ESTAB",
    "IBGE_N_DOMICILIO": "XFERA_N_DOMICILIO", "IBGE_QTD_UNIDADES": "XFERA_QTD_REGISTROS_CNEFE",
    "IBGE_N_ENDERECOS": "XFERA_N_ENDERECOS", "IBGE_ESPECIES_COD": "XFERA_ESPECIES_COD",
    "IBGE_DSC_ESTAB": "XFERA_DSC_ESTAB",
}
# compatibilidade com consumidores da v2.1
ALIAS_COMPAT = {"XFERA_QTD_UNID": "XFERA_QTD_REGISTROS_CNEFE"}

OCULTAR = ["_RID", "_DSC_ID", "NOME_NORM", "RAZAO_NORM", "DATA_ABERTURA_DT", "LOGR_TOKENS"]


def validate_output(df: pd.DataFrame, input_rows: int, strict: bool = True) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(nome, ok, detalhe):
        checks.append({"CHECK": nome, "STATUS": "APROVADO" if ok else "FALHA", "DETALHE": str(detalhe)})

    add("CONTAGEM_LINHAS", len(df) == input_rows, f"entrada={input_rows}; saída={len(df)}")
    add("RID_UNICO", df["_RID"].is_unique, f"duplicados={int(df['_RID'].duplicated().sum())}")
    add("ORDEM_PRESERVADA", df["_RID"].to_numpy().tolist() == list(range(len(df))), "ordem original")

    # F-24 — coluna ausente é FALHA, não KeyError
    ausentes = [c for c in COLUNAS_OBRIGATORIAS_SAIDA if c not in df.columns]
    add("COLUNAS_OBRIGATORIAS_PRESENTES", not ausentes, f"ausentes={ausentes}")

    # F-25 — nulo real e sentinela de string
    nulos = {}
    for c in COLUNAS_OBRIGATORIAS_SAIDA:
        if c not in df.columns:
            continue
        s = df[c]
        n = int(s.isna().sum() + s.astype(str).isin(SENTINELAS).sum())
        if n:
            nulos[c] = n
    add("CLASSIFICACOES_SEM_NULOS", not nulos, nulos)

    m = df["LATITUDE"].notna()
    add("COORDENADAS_VALIDAS",
        bool(m.sum() == 0 or all(valid_coord(a, b) for a, b in zip(df.loc[m, "LATITUDE"], df.loc[m, "LONGITUDE"]))),
        f"matchados={int(m.sum())}")
    add("COORDENADAS_NO_BRASIL",
        bool(m.sum() == 0 or all(in_brazil_bbox(a, b) for a, b in zip(df.loc[m, "LATITUDE"], df.loc[m, "LONGITUDE"]))),
        f"matchados={int(m.sum())}")

    delta = pd.to_numeric(df["NUM_DELTA"], errors="coerce").fillna(-1)
    exato_ruim = df["MATCH_PASS"].isin(["P1_EXATO", "P2_EXATO_BASE", "P4_FUZZY_EXATO"]) & delta.ne(0)
    add("EXATO_DELTA_ZERO", not bool(exato_ruim.any()), f"violações={int(exato_ruim.sum())}")

    centroide_ruim = df["MATCH_PASS"].eq("P8_CENTROIDE_SN") & df["NUM_INT"].ne(0)
    add("CENTROIDE_SO_SEM_NUMERO", not bool(centroide_ruim.any()), f"violações={int(centroide_ruim.sum())}")

    pot_ruim = df["POTENCIAL_CRUZAMENTO"] & df["SITUACAO_NORM"].ne("ATIVA")
    add("POTENCIAL_SO_ATIVAS", not bool(pot_ruim.any()), f"violações={int(pot_ruim.sum())}")

    # F-01 — nenhuma ALTA sobre coordenada de baixa precisão
    nv = pd.to_numeric(df["NV_GEO_COORD"], errors="coerce")
    alta_ruim = df["MATCH_CONF"].eq("ALTA") & nv.ge(4)
    add("SEM_ALTA_COM_NV_BAIXO", not bool(alta_ruim.any()), f"violações={int(alta_ruim.sum())}")

    # F-02 — todo match com coordenada declara desvio
    dev = pd.to_numeric(df["DESVIO_METROS"], errors="coerce")
    sem_desvio = m & (dev.isna() | dev.le(0))
    add("DESVIO_DECLARADO", not bool(sem_desvio.any()),
        f"violações={int(sem_desvio.sum())} (nenhuma coordenada pode declarar incerteza nula)")

    # F-03 — o NV sintético não invade o domínio oficial (1..6)
    sintetico_ruim = df["MATCH_PASS"].eq("P8_CENTROIDE_SN") & nv.between(1, 6)
    add("NV_SINTETICO_FORA_DO_DOMINIO", not bool(sintetico_ruim.any()), f"violações={int(sintetico_ruim.sum())}")

    if strict:
        falhas = [c for c in checks if c["STATUS"] == "FALHA"]
        if falhas:
            raise AssertionError("Falha de QA: " + " | ".join(f"{c['CHECK']}: {c['DETALHE']}" for c in falhas))
    return checks


def build_summary(df, meta, qa_counts, checks) -> pd.DataFrame:
    linhas: list[tuple[str, Any]] = [
        ("VERSAO_SKILL", meta["versao"]),
        ("DATA_REFERENCIA", meta["data_referencia"]),
        ("CNEFE_CUTOFF", meta["cnefe_cutoff"]),
        ("ARQUIVO_CNPJ", meta["arquivo_cnpj"]),
        ("SHA256_CNPJ", meta.get("sha256_cnpj", "")),
        ("ENCODING_CNPJ", meta.get("encoding_cnpj", "")),
        ("ARQUIVO_CNEFE", meta["arquivo_cnefe"]),
        ("SHA256_CNEFE", meta.get("sha256_cnefe", "")),
        ("ENCODING_CNEFE", meta.get("encoding_cnefe", "")),
        ("ARQUIVO_INTERNO_CNEFE", meta.get("arquivo_interno_cnefe", "")),
        ("DURACAO_SEGUNDOS", meta.get("duracao_segundos", "")),
    ]
    # F-21 — os parâmetros que mudam o resultado ficam gravados
    for k, v in sorted(meta.get("parametros_motor", {}).items()):
        linhas.append((f"PARAM_{k.upper()}", v))

    linhas += [
        ("TOTAL_CNPJ", len(df)),
        ("POTENCIAL_CRUZAMENTO", int(df["POTENCIAL_CRUZAMENTO"].sum())),
        ("SEM_POTENCIAL", int((~df["POTENCIAL_CRUZAMENTO"]).sum())),
        ("GEOCODIFICADOS", int(df["LATITUDE"].notna().sum())),
        ("MATCH_ALTA", int(df["MATCH_CONF"].eq("ALTA").sum())),
        ("MATCH_MEDIA", int(df["MATCH_CONF"].eq("MEDIA").sum())),
        ("MATCH_BAIXA", int(df["MATCH_CONF"].eq("BAIXA").sum())),
        ("SEM_MATCH", int(df["MATCH_CONF"].eq("SEM_MATCH").sum())),
    ]
    for classe, q in df["NV_CLASSE"].value_counts().items():
        linhas.append((f"NV_CLASSE_{classe}", int(q)))
    for rota, q in df["XFERA_ROTA_TRATAMENTO"].value_counts().items():
        linhas.append((f"ROTA_{rota}", int(q)))
    dev = pd.to_numeric(df["DESVIO_METROS"], errors="coerce").dropna()
    if len(dev):
        linhas += [("DESVIO_METROS_MEDIANA", round(float(dev.median()), 1)),
                   ("DESVIO_METROS_P95", round(float(dev.quantile(0.95)), 1))]
    linhas += [(k.upper(), v) for k, v in sorted(qa_counts.items())]
    linhas += [(f"QA_{c['CHECK']}", c["STATUS"]) for c in checks]
    for i, a in enumerate(meta.get("alertas", []), 1):
        linhas.append((f"ALERTA_{i}", a))
    return pd.DataFrame(linhas, columns=["INDICADOR", "VALOR"])


def data_dictionary() -> pd.DataFrame:
    linhas = [
        ("CNPJ_NORM", "RFB", "CNPJ normalizado (0-9 e A-Z); alfanumérico suportado"),
        ("CNPJ_VALIDO", "QA", "Validação de tamanho e dígitos verificadores (módulo 11, ord-48)"),
        ("CNPJ_STATUS", "QA", "Motivo da validação do CNPJ"),
        ("CNPJ_FORMATO", "QA", "NUMERICO ou ALFANUMERICO (IN RFB 2.229/2024)"),
        ("CEP_NORM", "NORMALIZAÇÃO", "CEP normalizado com 8 dígitos"),
        ("CEP_STATUS", "QA", "Status do parsing do CEP"),
        ("LOGR_TIPO", "NORMALIZAÇÃO", "Tipo canônico do logradouro"),
        ("LOGR_BASE", "NORMALIZAÇÃO", "Nome do logradouro sem o tipo"),
        ("LOGR_FULL", "NORMALIZAÇÃO", "Tipo + nome canônicos"),
        ("LOGR_STATUS", "QA", "Validade mínima do logradouro"),
        ("NUM_INT", "NORMALIZAÇÃO", "Número inteiro extraído"),
        ("NUM_STATUS", "QA", "VALIDO, VALIDO_COM_SUFIXO, SEM_NUMERO, SEM_NUMERO_ZERO, AUSENTE, INVALIDO"),
        ("NUM_SUFIXO", "NORMALIZAÇÃO", "Sufixo do número no cadastro (A, B, FUNDOS...)"),
        ("XFERA_LAT", "CNEFE", "Latitude da coordenada escolhida"),
        ("XFERA_LON", "CNEFE", "Longitude da coordenada escolhida"),
        ("XFERA_NV_GEO", "CNEFE", "Nível de geocodificação do IBGE (1..6) ou 90 = sintético da A2L"),
        ("XFERA_NV_CLASSE", "CNEFE", "1 ENDERECO_ORIGINAL, 2 ENDERECO_MODIFICADO, 3 ENDERECO_ESTIMADO, 4 FACE_QUADRA, 5 LOCALIDADE, 6 SETOR_CENSITARIO, 90 SINTETICO_LOGRADOURO"),
        ("XFERA_COD_IBGE", "CNEFE", "COD_UNICO_ENDERECO do endereço escolhido"),
        ("XFERA_NUM_FACE", "CNEFE", "Face de quadra do endereço (NUM_FACE do CNEFE)"),
        ("XFERA_MATCH_PASS", "MATCH", "Passo determinístico que resolveu o registro (P1..P8)"),
        ("XFERA_MATCH_CONF", "MATCH", "ALTA, MEDIA, BAIXA ou SEM_MATCH — já com teto por NV"),
        ("XFERA_CONF_MOTIVO", "MATCH", "Por que a confiança foi rebaixada, quando foi"),
        ("XFERA_MATCH_DETALHE", "MATCH", "Método de escolha do logradouro ou motivo da não-cobertura"),
        ("XFERA_LOGR_SCORE", "MATCH", "Score do melhor candidato de logradouro"),
        ("XFERA_LOGR_SCORE_SEGUNDO", "MATCH", "Score do segundo candidato"),
        ("XFERA_LOGR_CANDIDATOS", "MATCH", "Quantidade de logradouros avaliados no CEP"),
        ("XFERA_NUM_DELTA", "MATCH", "Diferença entre o número do cadastro e o número do CNEFE"),
        ("XFERA_NUM_IBGE", "MATCH", "Número do CNEFE efetivamente usado"),
        ("XFERA_NUM_SUFIXO_MATCH", "MATCH", "Sufixo/modificador casado, quando houve"),
        ("XFERA_PARIDADE_DIVERGENTE", "MATCH", "1 quando a paridade do número diverge"),
        ("XFERA_DESVIO_METROS", "MATCH", "Incerteza posicional estimada, em metros"),
        ("XFERA_DESVIO_FONTE", "MATCH", "PISO_NV, PISO_NV+INTERPOLACAO ou CENTROIDE_P95"),
        ("XFERA_FLAG_COMERCIAL", "CNEFE", "1 quando há estabelecimento (espécies 3,4,5,6,8) no endereço exato"),
        ("XFERA_IND_ESTAB_IBGE", "CNEFE", "COD_INDICADOR_ESTAB_ENDERECO do IBGE: 1 único, 2 até 10, 3 mais de 10, 4 quantidade desconhecida"),
        ("XFERA_N_ESTAB", "CNEFE", "Quantidade de registros de estabelecimento no endereço"),
        ("XFERA_N_DOMICILIO", "CNEFE", "Quantidade de registros de domicílio no endereço"),
        ("XFERA_QTD_REGISTROS_CNEFE", "CNEFE", "Registros CNEFE no endereço — inclui domicílios; NÃO é contagem de estabelecimentos"),
        ("XFERA_N_ENDERECOS", "CNEFE", "COD_UNICO_ENDERECO distintos no endereço"),
        ("XFERA_ESPECIES_COD", "CNEFE", "Códigos de espécie presentes no endereço"),
        ("XFERA_DSC_ESTAB", "CNEFE", "Nomes de estabelecimento registrados no endereço"),
        ("XFERA_PERFIL_ENDERECO", "CNEFE", "Perfil do endereço derivado do indicador oficial do IBGE"),
        ("XFERA_ROTA_TRATAMENTO", "DECISÃO", "RECLASSIFICACAO_1_1, INDIVIDUALIZACAO_MULTI, VERIFICAR_CAMPO ou SEM_ROTA"),
        ("XFERA_ENDERECO_MISTO", "CNEFE", "True quando o endereço tem domicílio e estabelecimento"),
        ("XFERA_EXISTE_NO_LOCAL", "EXISTÊNCIA", "Similaridade do nome com a fachada registrada no CNEFE"),
        ("XFERA_EXIST_SCORE", "EXISTÊNCIA", "Score multi-evidência 0 a 15"),
        ("XFERA_EXISTENCIA_FISICA", "EXISTÊNCIA", "Faixa da evidência física"),
        ("XFERA_EXIST_MOTIVOS", "EXISTÊNCIA", "Composição auditável do score"),
        ("XFERA_IBGE_APLICAVEL", "EXISTÊNCIA", "SIM, NAO_POS_CNEFE, INDETERMINADO_DATA_AUSENTE ou NAO_APLICAVEL_SEM_COORDENADA"),
        ("PERFIL_ESTRUTURA", "CNAE", "PRESENCIAL, HIBRIDO, MOVEL, PAPEL ou INCERTO"),
        ("PERFIL_CONFIANCA", "CNAE", "Confiança da regra de classificação"),
        ("PERFIL_COMERCIAL", "DECISÃO", "Classificação do ponto físico"),
        ("SEGMENTO", "EMPRESA", "Segmento amplo por divisão CNAE"),
        ("PORTE_CAPITAL", "EMPRESA", "Faixa do capital social declarado"),
        ("PAPEL_ESTRUTURAL", "EMPRESA", "Matriz, filial, matriz com rede ou unidade única"),
        ("SCORE_ESTRUTURA", "EMPRESA", "Score estrutural 0 a 12"),
        ("SCORE_ESTRUTURA_NAO_AVALIADO", "QA", "Sinais que ficaram de fora por dado ausente"),
        ("APTIDAO_CRUZAMENTO", "DECISÃO", "Aptidão geográfica, independente do perfil comercial"),
        ("POTENCIAL_CRUZAMENTO", "DECISÃO", "Ativa, CNPJ válido e atividade com ponto físico"),
        ("MOTIVO_SEM_CRUZAMENTO", "DECISÃO", "Motivo auditável quando não é potencial"),
    ]
    return pd.DataFrame(linhas, columns=["COLUNA", "GRUPO", "DESCRICAO"])


def rename_output(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=RENAME_SAIDA)
    for alias, origem in ALIAS_COMPAT.items():
        if origem in out.columns and alias not in out.columns:
            out[alias] = out[origem]
    return out


def export_result(df_internal: pd.DataFrame, out_path: Path, summary: pd.DataFrame,
                  dictionary: pd.DataFrame, checks: list[dict], formatos: set[str]) -> dict[str, Any]:
    out = rename_output(df_internal.copy()).sort_values("_RID", kind="mergesort").reset_index(drop=True)
    base = out.drop(columns=OCULTAR, errors="ignore")
    potencial = base[base["POTENCIAL_CRUZAMENTO"]].copy()
    sem_potencial = base[~base["POTENCIAL_CRUZAMENTO"]].copy()
    qa_df = pd.DataFrame(checks)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gerados: dict[str, Any] = {"arquivos": [], "alertas": []}

    # --- Parquet / CSV (F-20): sempre, e são a fonte de verdade em base grande ---
    stem = out_path.with_suffix("")
    if "parquet" in formatos:
        p = Path(f"{stem}_BASE_COMPLETA.parquet")
        base.to_parquet(p, index=False)
        gerados["arquivos"].append(str(p))
    if "csv" in formatos:
        p = Path(f"{stem}_BASE_COMPLETA.csv")
        base.to_csv(p, index=False, sep=";", encoding="utf-8-sig")
        gerados["arquivos"].append(str(p))

    if "xlsx" not in formatos:
        return gerados

    # --- F-19: guarda do limite físico do Excel ---
    limite = EXCEL_MAX_ROWS - 1
    excede = {n: len(d) for n, d in
              [("POTENCIAL_CRUZAMENTO", potencial), ("SEM_POTENCIAL", sem_potencial), ("BASE_COMPLETA", base)]
              if len(d) > limite}
    if excede:
        msg = (f"Excel não comporta {excede}; limite por aba = {limite:,}. "
               "O xlsx foi gerado apenas com RESUMO/QA/DICIONARIO; os dados estão em Parquet/CSV.")
        gerados["alertas"].append(msg)
        with pd.ExcelWriter(out_path, engine="xlsxwriter") as w:
            summary.to_excel(w, sheet_name="RESUMO_EXECUCAO", index=False)
            qa_df.to_excel(w, sheet_name="QA_ACEITE", index=False)
            dictionary.to_excel(w, sheet_name="DICIONARIO", index=False)
            pd.DataFrame([{"AVISO": msg}]).to_excel(w, sheet_name="LEIA_ME", index=False)
        gerados["arquivos"].append(str(out_path))
        return gerados

    options = {"strings_to_formulas": False, "strings_to_urls": False, "constant_memory": False}
    abas = [("RESUMO_EXECUCAO", summary), ("QA_ACEITE", qa_df), ("DICIONARIO", dictionary),
            ("POTENCIAL_CRUZAMENTO", potencial), ("SEM_POTENCIAL", sem_potencial),
            ("BASE_COMPLETA", base)]
    with pd.ExcelWriter(out_path, engine="xlsxwriter", engine_kwargs={"options": options}) as writer:
        for nome, dados in abas:
            dados.to_excel(writer, sheet_name=nome, index=False)
        wb = writer.book
        h = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        hg = wb.add_format({"bold": True, "bg_color": "#375623", "font_color": "white", "border": 1})
        hr = wb.add_format({"bold": True, "bg_color": "#843C0C", "font_color": "white", "border": 1})
        for nome, dados in abas:
            ws = writer.sheets[nome]
            fmt = hg if nome == "POTENCIAL_CRUZAMENTO" else hr if nome == "SEM_POTENCIAL" else h
            for i, col in enumerate(dados.columns):
                ws.write(0, i, col, fmt)
            ws.freeze_panes(1, 0)
            if len(dados.columns):
                ws.autofilter(0, 0, max(len(dados), 1), len(dados.columns) - 1)
                ws.set_column(0, len(dados.columns) - 1, 18)
            if nome == "DICIONARIO":
                ws.set_column(0, 0, 34); ws.set_column(1, 1, 16); ws.set_column(2, 2, 88)
    gerados["arquivos"].append(str(out_path))

    # --- F-19: reconferência do arquivo efetivamente gravado ---
    try:
        lidas = pd.read_excel(out_path, sheet_name="BASE_COMPLETA", usecols=[0]).shape[0]
        if lidas != len(base):
            raise AssertionError(f"Excel gravou {lidas:,} linhas para {len(base):,} esperadas em BASE_COMPLETA")
        gerados["linhas_reconferidas"] = lidas
    except AssertionError:
        raise
    except Exception as exc:  # leitura de conferência é best-effort
        gerados["alertas"].append(f"Reconferência do xlsx não pôde ser feita: {exc}")
    return gerados
