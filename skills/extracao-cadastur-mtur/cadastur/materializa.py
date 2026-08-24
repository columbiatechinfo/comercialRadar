"""Materializacao incremental sem concatenar todo o bronze em RAM."""
from __future__ import annotations

import csv
import gzip
import json
import shutil
from pathlib import Path

import pandas as pd
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:  # gate explicito; selftest logico continua executavel sem dependencia opcional instalada
    pa = None
    pq = None


def _require_pyarrow() -> None:
    if pa is None or pq is None:
        raise RuntimeError("pyarrow nao instalado; execute: pip install -r requirements.txt")

from .esquema import PROCEDENCIA


def normalizar_colunas(df: pd.DataFrame, colunas: list[str]) -> pd.DataFrame:
    for c in colunas:
        if c not in df.columns:
            df[c] = "{}" if c == "_extras" else ""
    return df[colunas].fillna("")


def escrever_shard(df: pd.DataFrame, alvo: Path) -> Path:
    _require_pyarrow()
    alvo.parent.mkdir(parents=True, exist_ok=True)
    tmp = alvo.with_suffix(alvo.suffix + ".parcial")
    tmp.unlink(missing_ok=True)
    df.to_parquet(tmp, index=False, engine="pyarrow", compression="zstd")
    tmp.replace(alvo)
    return alvo


def consolidar(shards: list[Path], colunas: list[str], parquet_base: Path, csv_alvo: Path) -> None:
    _require_pyarrow()
    """Consolida em streaming. Pico de RAM ~ tamanho de um shard, nao do acervo."""
    novo = parquet_base.with_name(parquet_base.name + ".novo")
    backup = parquet_base.with_name(parquet_base.name + ".anterior")
    shutil.rmtree(novo, ignore_errors=True)
    novo.mkdir(parents=True, exist_ok=True)

    writers: dict[tuple[str,str], pq.ParquetWriter] = {}
    csv_tmp = csv_alvo.with_name(csv_alvo.name + ".parcial")
    csv_tmp.unlink(missing_ok=True)
    primeira = True
    try:
        with gzip.open(csv_tmp, "wt", encoding="utf-8", newline="") as gz:
            for shard in shards:
                df = pd.read_parquet(shard).fillna("")
                df = normalizar_colunas(df, colunas)
                # CSV incremental deterministico
                df.to_csv(gz, index=False, sep=";", quoting=csv.QUOTE_ALL, header=primeira, lineterminator="\n")
                primeira = False
                # Parquet agrupado por dataset x ano, writer reaproveitado
                for (ds, ano), parte in df.groupby(["_dataset","_ref_ano"], sort=True, dropna=False):
                    key = (str(ds), str(ano))
                    tabela = pa.Table.from_pandas(parte, preserve_index=False)
                    if key not in writers:
                        alvo = novo / str(ds) / f"{ds}_{ano}.parquet"
                        alvo.parent.mkdir(parents=True, exist_ok=True)
                        writers[key] = pq.ParquetWriter(alvo, tabela.schema, compression="zstd")
                    writers[key].write_table(tabela)
        for w in writers.values():
            w.close()
        writers.clear()
        csv_tmp.replace(csv_alvo)
        shutil.rmtree(backup, ignore_errors=True)
        if parquet_base.exists():
            parquet_base.replace(backup)
        novo.replace(parquet_base)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        for w in writers.values():
            try: w.close()
            except Exception: pass
        shutil.rmtree(novo, ignore_errors=True)
        csv_tmp.unlink(missing_ok=True)
        raise


def identidade_df(parquet_base: Path) -> pd.DataFrame:
    _require_pyarrow()
    """Lê apenas colunas mínimas e calcula chave_entidade em Python."""
    partes = []
    for p in sorted(parquet_base.rglob("*.parquet")):
        schema = pq.read_schema(p)
        nomes = set(schema.names)
        cols = [c for c in ["_dataset","_recurso_id","_linha_origem","cnpj","cpf","numero_certificado"] if c in nomes]
        d = pd.read_parquet(p, columns=cols).fillna("")
        for c in ["cnpj","cpf","numero_certificado"]:
            if c not in d.columns: d[c] = ""
        cnpj = d["cnpj"].astype(str).str.replace(r"\D", "", regex=True)
        cpf = d["cpf"].astype(str).str.replace(r"\D", "", regex=True)
        cert = d["numero_certificado"].astype(str).str.strip()
        d["chave_entidade"] = "ROW:" + d["_recurso_id"].astype(str) + ":" + d["_linha_origem"].astype(str)
        d.loc[cert.ne(""), "chave_entidade"] = "CERT:" + cert[cert.ne("")]
        d.loc[cpf.str.len().eq(11), "chave_entidade"] = "CPF:" + cpf[cpf.str.len().eq(11)]
        d.loc[cnpj.str.len().eq(14), "chave_entidade"] = "CNPJ:" + cnpj[cnpj.str.len().eq(14)]
        partes.append(d[["_dataset","chave_entidade"]])
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=["_dataset","chave_entidade"])


def snapshot_entidades(parquet_base: Path) -> pd.DataFrame:
    _require_pyarrow()
    """Cria snapshot compacto da versao vigente de cada entidade.

    O fingerprint ignora procedencia volatil e mede mudanca do conteudo canonico.
    Mantem em RAM apenas 1 registro por entidade, nao o bronze historico inteiro.
    """
    melhores: dict[tuple[str, str], tuple[str, str]] = {}
    volateis = set(PROCEDENCIA) | {"_linha_fisica_origem", "_aba_origem", "_tabela_origem"}
    volateis.discard("_dataset")
    volateis.discard("_ref_periodo")
    for p in sorted(parquet_base.rglob("*.parquet")):
        d = pd.read_parquet(p).fillna("")
        for c in ["cnpj", "cpf", "numero_certificado"]:
            if c not in d.columns: d[c] = ""
        cnpj = d["cnpj"].astype(str).str.replace(r"\D", "", regex=True)
        cpf = d["cpf"].astype(str).str.replace(r"\D", "", regex=True)
        cert = d["numero_certificado"].astype(str).str.strip()
        chave = "ROW:" + d["_recurso_id"].astype(str) + ":" + d["_linha_origem"].astype(str)
        chave = chave.mask(cert.ne(""), "CERT:" + cert)
        chave = chave.mask(cpf.str.len().eq(11), "CPF:" + cpf)
        chave = chave.mask(cnpj.str.len().eq(14), "CNPJ:" + cnpj)
        conteudo_cols = sorted(c for c in d.columns if c not in volateis and not c.startswith("_"))
        if "_extras" in d.columns:
            conteudo_cols.append("_extras")
        # hash estavel de cada linha considerando apenas conteudo de negocio
        h = pd.util.hash_pandas_object(d[conteudo_cols].astype(str), index=False).astype(str)
        for ds, key, per, fp in zip(d["_dataset"].astype(str), chave.astype(str),
                                    d["_ref_periodo"].astype(str), h.astype(str)):
            k = (ds, key)
            prev = melhores.get(k)
            if prev is None or per >= prev[0]:
                melhores[k] = (per, fp)
    return pd.DataFrame([
        {"_dataset": ds, "chave_entidade": key, "_ref_periodo": per, "fingerprint_entidade": fp}
        for (ds, key), (per, fp) in melhores.items()
    ])


def sha256_arquivo(caminho: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()
