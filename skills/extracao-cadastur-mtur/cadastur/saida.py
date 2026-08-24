"""Materializacao: Parquet + CSV + auditoria + DDL PostgreSQL/PostGIS."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pandas as pd

from .esquema import MAPA, PII, PROCEDENCIA


def gravar_parquet(df: pd.DataFrame, base: Path) -> Path:
    novo = base.with_name(base.name + ".novo")
    backup = base.with_name(base.name + ".anterior")
    shutil.rmtree(novo, ignore_errors=True)
    novo.mkdir(parents=True, exist_ok=True)
    for (ds, ano), parte in df.groupby(["_dataset", "_ref_ano"], sort=True):
        alvo = novo / str(ds) / f"{ds}_{ano}.parquet"
        alvo.parent.mkdir(parents=True, exist_ok=True)
        parte.to_parquet(alvo, index=False, engine="pyarrow", compression="zstd")
    # swap somente depois de a nova arvore estar completa.
    shutil.rmtree(backup, ignore_errors=True)
    if base.exists():
        base.replace(backup)
    novo.replace(base)
    shutil.rmtree(backup, ignore_errors=True)
    return base


def gravar_csv(df: pd.DataFrame, alvo: Path) -> Path:
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_name(alvo.name + ".parcial")
    tmp.unlink(missing_ok=True)
    df.to_csv(tmp, index=False, sep=";", quoting=csv.QUOTE_ALL,
              encoding="utf-8", compression="gzip")
    tmp.replace(alvo)
    return alvo


def gravar_auditoria(saida: Path, funil: list[dict], desconhecidas: list[dict],
                     colunas_finais: list[str]) -> None:
    saida.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(funil).to_csv(saida / "funil.csv", index=False, sep=";")
    pd.DataFrame(desconhecidas or [{"recurso_id": "", "coluna": "", "n": 0,
                                    "preenchidos": 0}]).to_csv(
        saida / "colunas_desconhecidas.csv", index=False, sep=";")
    inv = [{"coluna": c, "classificacao": PII.get(c, "dado de pessoa juridica"),
            "presente_na_saida": c in colunas_finais}
           for c in sorted(set(MAPA.values()) | set(PII))]
    pd.DataFrame(inv).to_csv(saida / "inventario_pii.csv", index=False, sep=";")
    pd.DataFrame([{"rotulo_origem": k, "coluna_canonica": v}
                  for k, v in sorted(MAPA.items())]).to_csv(
        saida / "mapa_colunas.csv", index=False, sep=";")


def _col(c: str, presentes: set[str], fallback: str = "NULL::text") -> str:
    return f"b.{c}" if c in presentes else fallback


def gerar_ddl(colunas: list[str], saida: Path) -> Path:
    """DDL idempotente e aditivo: nunca derruba a tabela bronze existente."""
    presentes = set(colunas)
    dados = [c for c in colunas if c not in PROCEDENCIA and c != "_extras"]

    alters: list[str] = []
    for c in dados:
        alters.append(f"ALTER TABLE cadastur.bronze_prestador ADD COLUMN IF NOT EXISTS {c} text;")
    alters.append("ALTER TABLE cadastur.bronze_prestador ADD COLUMN IF NOT EXISTS _extras jsonb;")
    for c in PROCEDENCIA:
        if c in {"_recurso_id", "_linha_origem"}:
            continue
        tipo = "int" if c in ("_ref_ano", "_ref_trimestre") else "text"
        alters.append(f"ALTER TABLE cadastur.bronze_prestador ADD COLUMN IF NOT EXISTS {c} {tipo};")

    indexes = []
    if "cnpj" in presentes:
        indexes.append("CREATE INDEX IF NOT EXISTS ix_bronze_cnpj ON cadastur.bronze_prestador (cnpj);")
    indexes.append("CREATE INDEX IF NOT EXISTS ix_bronze_periodo ON cadastur.bronze_prestador (_dataset, _ref_periodo);")
    if {"uf", "municipio"}.issubset(presentes):
        indexes.append("CREATE INDEX IF NOT EXISTS ix_bronze_uf_mun ON cadastur.bronze_prestador (uf, municipio);")
    indexes.append("CREATE INDEX IF NOT EXISTS ix_bronze_extras ON cadastur.bronze_prestador USING gin (_extras jsonb_path_ops);")

    cnpj = _col("cnpj", presentes, "''::text")
    cpf = _col("cpf", presentes, "''::text")
    cert = _col("numero_certificado", presentes, "''::text")
    uh = _col("uh", presentes, "''::text")
    leitos = _col("leitos", presentes, "''::text")
    endereco = (f"coalesce({_col('endereco_comercial', presentes, 'NULL::text')},"
                f"{_col('endereco_rfb', presentes, 'NULL::text')},'')")
    validade = _col("validade_certificado", presentes, "''::text")

    sql = f'''-- gerado por extracao-cadastur-mtur v3; DDL NAO DESTRUTIVO
CREATE SCHEMA IF NOT EXISTS cadastur;

CREATE TABLE IF NOT EXISTS cadastur.bronze_prestador (
  _recurso_id                        text NOT NULL,
  _linha_origem                      int NOT NULL,
  CONSTRAINT pk_bronze PRIMARY KEY (_recurso_id, _linha_origem)
);

{chr(10).join(alters)}

-- carga manual (CSV ; QUOTE ALL, utf-8). Para carga transacional/reconciliada,
-- prefira: python cadastur_extrai.py ... --postgres-dsn "$DATABASE_URL"

{chr(10).join(indexes)}

CREATE OR REPLACE VIEW cadastur.v_prestador AS
WITH x AS (
 SELECT b.*,
        regexp_replace(coalesce({cnpj},''),'\\D','','g') AS _cnpj_digits,
        regexp_replace(coalesce({cpf},''),'\\D','','g') AS _cpf_digits
 FROM cadastur.bronze_prestador b
)
SELECT x.*,
       CASE WHEN length(_cnpj_digits)=14 THEN _cnpj_digits END AS cnpj14,
       CASE WHEN length(_cpf_digits)=11 THEN _cpf_digits END AS cpf11,
       CASE WHEN trim(coalesce({uh.replace('b.','x.')},'')) ~ '^\\d+$'
            THEN trim({uh.replace('b.','x.')})::int END AS uh_num,
       CASE WHEN trim(coalesce({leitos.replace('b.','x.')},'')) ~ '^\\d+$'
            THEN trim({leitos.replace('b.','x.')})::int END AS leitos_num,
       substring(replace({endereco.replace('b.','x.')},'-','') from '(\\d{{8}})') AS cep_extraido,
       CASE
         WHEN trim(coalesce({validade.replace('b.','x.')},'')) ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' THEN left(trim({validade.replace('b.','x.')}),10)::date
         WHEN trim(coalesce({validade.replace('b.','x.')},'')) ~ '^\\d{{2}}/\\d{{2}}/\\d{{4}}' THEN to_date(left(trim({validade.replace('b.','x.')}),10),'DD/MM/YYYY')
         ELSE NULL
       END AS validade_dt,
       CASE
         WHEN length(_cnpj_digits)=14 THEN 'CNPJ:'||_cnpj_digits
         WHEN length(_cpf_digits)=11 THEN 'CPF:'||_cpf_digits
         WHEN nullif(trim(coalesce({cert.replace('b.','x.')},'')),'') IS NOT NULL THEN 'CERT:'||trim({cert.replace('b.','x.')})
         ELSE 'ROW:'||x._recurso_id||':'||x._linha_origem::text
       END AS chave_entidade
FROM x;

CREATE OR REPLACE VIEW cadastur.v_vigente AS
SELECT DISTINCT ON (_dataset, chave_entidade) *
FROM cadastur.v_prestador
ORDER BY _dataset, chave_entidade, _ref_periodo DESC, _recurso_id DESC, _linha_origem DESC;
'''
    alvo = saida / "ddl_cadastur.sql"
    tmp = alvo.with_suffix(".sql.parcial")
    tmp.write_text(sql, encoding="utf-8")
    tmp.replace(alvo)
    return alvo
