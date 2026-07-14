# -*- coding: utf-8 -*-
"""
base_comum.py — motor compartilhado dos módulos de bases externas (CNPJ, CNEFE, ANEEL).

Puramente ETL: baixa arquivo → descompacta → **COPY direto** pro Postgres do
comercialRadar (sem parsear linha a linha em Python — é o único jeito de aguentar
dezenas de GB). Idempotente: cada arquivo carregado é registrado em `fonte_arquivos`
e pulado numa re-execução.

Não mistura com os POIs — grava só nas tabelas próprias das bases (rf_*, ibge_*, aneel_*).
Ver [[ferramentas-separadas]] e DOCUMENTACAO.md.
"""
import io
import os
import ssl
import time
import base64
import zipfile
import tempfile
import urllib.request
from pathlib import Path

import realtime_ingest

TMP = Path(tempfile.gettempdir()) / "bases_externas"
TMP.mkdir(parents=True, exist_ok=True)
_SSL = ssl.create_default_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def conectar():
    return realtime_ingest.conectar()


# ── Controle de arquivos já carregados (idempotência) ───────────────────────────
def garantir_controle(conn):
    with conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fonte_arquivos (
                fonte        text NOT NULL,
                referencia   text NOT NULL,
                tabela       text,
                linhas       bigint,
                bytes        bigint,
                status       text DEFAULT 'ok',
                carregado_em timestamptz DEFAULT now(),
                PRIMARY KEY (fonte, referencia)
            )""")


def ja_carregado(conn, fonte: str, referencia: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM fonte_arquivos WHERE fonte=%s AND referencia=%s AND status='ok'",
                    (fonte, referencia))
        return cur.fetchone() is not None


def marcar(conn, fonte, referencia, tabela, linhas, bytes_):
    with conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO fonte_arquivos (fonte, referencia, tabela, linhas, bytes)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (fonte, referencia) DO UPDATE
                         SET tabela=EXCLUDED.tabela, linhas=EXCLUDED.linhas,
                             bytes=EXCLUDED.bytes, status='ok', carregado_em=now()""",
                    (fonte, referencia, tabela, linhas, bytes_))


# ── Download com retomada (Range) e retry ───────────────────────────────────────
def baixar(url: str, destino: Path, auth: str = None, tentativas: int = 4) -> Path:
    """Baixa url→destino com retomada (HTTP Range) se o arquivo parcial existir."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    for t in range(tentativas):
        pos = destino.stat().st_size if destino.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        if auth:
            req.add_header("Authorization", "Basic " + auth)
        if pos:
            req.add_header("Range", f"bytes={pos}-")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_SSL) as r:
                modo = "ab" if (pos and r.status == 206) else "wb"
                if modo == "wb":
                    pos = 0
                with open(destino, modo) as f:
                    while True:
                        chunk = r.read(1 << 20)          # 1 MB
                        if not chunk:
                            break
                        f.write(chunk)
            return destino
        except Exception as e:
            if t == tentativas - 1:
                raise
            time.sleep(5 * (t + 1))                     # backoff


# ── COPY: streaming de um CSV (file-like binário) direto pro Postgres ───────────
def copy_csv(conn, tabela: str, fobj, delimiter=";", header=False,
             encoding="LATIN1", quote='"', colunas=None) -> int:
    """COPY tabela FROM STDIN lendo o CSV cru de fobj (sem parsear em Python).
    Retorna o nº de linhas inseridas. fobj deve entregar bytes."""
    cols = f" ({', '.join(colunas)})" if colunas else ""
    sql = (f"COPY {tabela}{cols} FROM STDIN WITH (FORMAT csv, DELIMITER '{delimiter}', "
           f"QUOTE '{quote}', ENCODING '{encoding}'"
           + (", HEADER true" if header else "") + ")")
    with conn.cursor() as cur:
        cur.copy_expert(sql, fobj)          # rowcount = nº de linhas copiadas (sem scan)
        n = cur.rowcount
    conn.commit()
    return n if (n is not None and n >= 0) else 0


def copy_de_zip(conn, tabela: str, zip_path: Path, **kw) -> int:
    """Abre o(s) CSV(s) de dentro de um .zip e COPY cada um pra tabela."""
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for nome in z.namelist():
            if nome.endswith("/"):
                continue
            with z.open(nome) as f:
                total += copy_csv(conn, tabela, f, **kw)
    return total


def limpar_tmp(*paths):
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass


def b64_token(token: str) -> str:
    return base64.b64encode(f"{token}:".encode()).decode()
