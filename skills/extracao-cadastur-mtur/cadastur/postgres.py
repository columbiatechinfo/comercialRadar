"""Carga PostgreSQL transacional opcional com staging e reconciliação."""
from __future__ import annotations

import csv
import gzip
import re
from pathlib import Path


def _ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"identificador PostgreSQL invalido: {name!r}")
    return name


def carregar(dsn: str, saida: Path, run_id: str, schema: str = "cadastur", tabela: str = "bronze_prestador") -> dict:
    try:
        import psycopg
    except ImportError as e:
        raise RuntimeError("psycopg nao instalado; execute pip install -r requirements.txt") from e
    schema = _ident(schema); tabela = _ident(tabela)
    stage = _ident(f"_stage_{re.sub(r'[^A-Za-z0-9_]', '_', run_id)[-24:]}")
    csv_path = saida / "bronze_cadastur.csv.gz"
    ddl = (saida / "ddl_cadastur.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=False) as cx:
        with cx.cursor() as cur:
            cur.execute(ddl)
            cur.execute(f'CREATE TEMP TABLE "{stage}" (LIKE "{schema}"."{tabela}" INCLUDING DEFAULTS) ON COMMIT DROP')
            with gzip.open(csv_path, "rt", encoding="utf-8", newline="") as src:
                header = src.readline().rstrip("\r\n")
                cols = next(csv.reader([header], delimiter=";", quotechar='"'))
                for c in cols:
                    _ident(c)
                col_sql = ",".join(f'"{c}"' for c in cols)
                copy_sql = f'''COPY "{stage}" ({col_sql}) FROM STDIN WITH (FORMAT CSV, DELIMITER ';', QUOTE '"')'''
                with cur.copy(copy_sql) as cp:
                    for line in src:
                        cp.write(line)
            cur.execute(f'SELECT count(*) FROM "{stage}"')
            staged = int(cur.fetchone()[0])
            nonpk = [c for c in cols if c not in {"_recurso_id", "_linha_origem"}]
            set_sql = ",".join(f'"{c}"=EXCLUDED."{c}"' for c in nonpk)
            if not set_sql:
                raise RuntimeError("CSV sem colunas atualizaveis")
            cur.execute(f'''INSERT INTO "{schema}"."{tabela}" ({col_sql})
                            SELECT {col_sql} FROM "{stage}"
                            ON CONFLICT (_recurso_id,_linha_origem) DO UPDATE SET {set_sql}''')
            cur.execute(f'''SELECT count(*) FROM "{schema}"."{tabela}" b
                            JOIN "{stage}" s USING (_recurso_id,_linha_origem)''')
            matched = int(cur.fetchone()[0])
            if matched != staged:
                raise RuntimeError(f"reconciliacao PostgreSQL falhou: stage={staged} destino={matched}")
        cx.commit()
    return {"postgres_linhas_stage": staged, "postgres_linhas_reconciliadas": matched, "gate_postgres": "OK"}
