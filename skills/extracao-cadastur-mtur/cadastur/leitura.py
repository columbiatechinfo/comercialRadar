"""Leitura sem descarte das geracoes do Cadastur.

Principios:
- formato e' detectado pelos bytes;
- nenhuma linha CSV malformada e' descartada silenciosamente;
- cabecalhos vazios/duplicados recebem nomes sinteticos e continuam rastreaveis;
- Excel preserva aba + linha fisica; HTML preserva tabela + linha;
- tudo entra como texto, sem conversao destrutiva de NA/zeros/identificadores.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd

SEPARADORES = [";", ",", "\t", "|"]
ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]


def formato_real(caminho: Path) -> str:
    with caminho.open("rb") as f:
        cab = f.read(512)
    if cab[:2] == b"PK":
        return "xlsx"
    if cab[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"
    if cab.lstrip()[:1] == b"<":
        return "html"
    return "csv"


def _cabecalhos_unicos(valores: list[object]) -> list[str]:
    """Preserva todos os campos mesmo com rotulo vazio ou duplicado."""
    usados: dict[str, int] = {}
    out: list[str] = []
    for i, valor in enumerate(valores, 1):
        base = str(valor).strip() if valor is not None else ""
        if not base or base.lower() == "nan":
            base = f"__SEM_ROTULO_{i:03d}"
        n = usados.get(base, 0) + 1
        usados[base] = n
        out.append(base if n == 1 else f"{base}__DUP_{n:02d}")
    return out


def _separador(txt: str) -> str:
    amostra = txt[:65536]
    try:
        d = csv.Sniffer().sniff(amostra, delimiters="".join(SEPARADORES))
        return d.delimiter
    except csv.Error:
        primeira = next((x for x in txt.splitlines() if x.strip()), "")
        return max(SEPARADORES, key=primeira.count)


def _ler_csv(caminho: Path) -> tuple[pd.DataFrame, dict]:
    bruto = caminho.read_bytes()
    ultimo: Exception | None = None
    for enc in ENCODINGS:
        try:
            txt = bruto.decode(enc)
        except UnicodeDecodeError as e:
            ultimo = e
            continue
        sep = _separador(txt)
        try:
            linhas = list(csv.reader(io.StringIO(txt, newline=""), delimiter=sep))
        except csv.Error as e:
            ultimo = e
            continue
        if not linhas:
            return pd.DataFrame(), {"encoding": enc, "separador": sep, "aba": None,
                                    "linhas_fisicas": 0, "linhas_ragged": 0}

        largura = max(len(r) for r in linhas)
        if largura == 0:
            return pd.DataFrame(), {"encoding": enc, "separador": sep, "aba": None,
                                    "linhas_fisicas": len(linhas), "linhas_ragged": 0}
        cab = list(linhas[0]) + [""] * (largura - len(linhas[0]))
        colunas = _cabecalhos_unicos(cab)
        dados, fisicas = [], []
        ragged = 0
        for numero, row in enumerate(linhas[1:], 2):
            if len(row) != largura:
                ragged += 1
            row = list(row) + [""] * (largura - len(row))
            dados.append(row[:largura])
            fisicas.append(str(numero))
        df = pd.DataFrame(dados, columns=colunas, dtype=str)
        df["_LINHA_FISICA_ORIGEM"] = fisicas
        return df, {"encoding": enc, "separador": sep, "aba": None,
                    "linhas_fisicas": len(linhas), "linhas_ragged": ragged}
    raise ValueError(f"CSV ilegivel sem descarte seguro: {caminho}: {ultimo}")


def _ler_excel(caminho: Path, motor: str) -> tuple[pd.DataFrame, dict]:
    """Le todas as abas e preserva endereco fisico da celula/linha."""
    xl = pd.ExcelFile(caminho, engine=motor)
    partes: list[pd.DataFrame] = []
    for aba in xl.sheet_names:
        raw = xl.parse(aba, header=None, dtype=object, keep_default_na=False, na_filter=False)
        if raw.empty:
            continue
        # primeira linha e' cabecalho; se a planilha tiver apenas cabecalho, gera 0 dados
        headers = _cabecalhos_unicos(raw.iloc[0].tolist())
        d = raw.iloc[1:].copy()
        d.columns = headers
        # Converte somente agora para preservar exatamente a representacao lida.
        d = d.apply(lambda s: s.map(lambda v: "" if v is None else str(v)))
        d["_ABA"] = str(aba)
        d["_LINHA_FISICA_ORIGEM"] = [str(i) for i in range(2, len(d) + 2)]
        partes.append(d.reset_index(drop=True))
    if not partes:
        return pd.DataFrame(), {"encoding": None, "separador": None,
                                "aba": ",".join(map(str, xl.sheet_names)),
                                "linhas_fisicas": 0, "linhas_ragged": 0}
    df = pd.concat(partes, ignore_index=True, sort=False).fillna("")
    return df, {"encoding": None, "separador": None,
                "aba": ",".join(map(str, xl.sheet_names)),
                "linhas_fisicas": len(df) + len(partes), "linhas_ragged": 0}


def _ler_html(caminho: Path) -> tuple[pd.DataFrame, dict]:
    tabelas = pd.read_html(caminho, dtype=str, keep_default_na=False)
    partes: list[pd.DataFrame] = []
    for ti, t in enumerate(tabelas, 1):
        t = t.fillna("").astype(str)
        t.columns = _cabecalhos_unicos(list(t.columns))
        t["_TABELA_ORIGEM"] = str(ti)
        t["_LINHA_FISICA_ORIGEM"] = [str(i) for i in range(2, len(t) + 2)]
        partes.append(t)
    df = pd.concat(partes, ignore_index=True, sort=False).fillna("") if partes else pd.DataFrame()
    return df, {"encoding": None, "separador": None, "aba": None,
                "linhas_fisicas": len(df) + len(partes), "linhas_ragged": 0}


def ler(caminho: Path) -> tuple[pd.DataFrame, dict]:
    fmt = formato_real(caminho)
    if fmt == "csv":
        df, meta = _ler_csv(caminho)
    elif fmt == "xlsx":
        df, meta = _ler_excel(caminho, "openpyxl")
    elif fmt == "xls":
        df, meta = _ler_excel(caminho, "xlrd")
    else:
        df, meta = _ler_html(caminho)
    df.columns = [str(c).strip() for c in df.columns]
    meta |= {"formato_real": fmt, "linhas_origem": len(df),
             "colunas_origem": list(df.columns)}
    return df, meta
