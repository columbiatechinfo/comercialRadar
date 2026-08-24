"""Reconciliação ponta a ponta e hashes dos artefatos."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None

from .materializa import sha256_arquivo


def _contar_csv_gz(path: Path) -> int:
    # csv.reader conta registros lógicos; campos quoted podem conter quebra de linha.
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter=";", quotechar='"')
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def _contar_parquet(base: Path) -> tuple[int,int]:
    if pq is None: raise RuntimeError("pyarrow nao instalado")
    linhas = 0; arquivos = 0
    for p in sorted(base.rglob("*.parquet")):
        linhas += pq.ParquetFile(p).metadata.num_rows
        arquivos += 1
    return linhas, arquivos


def reconciliar(saida: Path, funil: list[dict], recursos_esperados: int) -> dict:
    origem = sum(int(x.get("linhas_origem",0)) for x in funil)
    declarada = sum(int(x.get("linhas_saida",0)) for x in funil)
    parquet_n, parquet_files = _contar_parquet(saida / "bronze_parquet")
    csv_n = _contar_csv_gz(saida / "bronze_cadastur.csv.gz")
    manifesto_n = sum(1 for _ in (saida / "manifesto.jsonl").open(encoding="utf-8"))
    checks = {
        "origem_igual_saida_funil": origem == declarada,
        "saida_funil_igual_parquet": declarada == parquet_n,
        "parquet_igual_csv": parquet_n == csv_n,
        "manifesto_cobre_recursos": manifesto_n == recursos_esperados,
    }
    # Hash dos artefatos materiais e manifestos; diretório Parquet vira Merkle simples determinístico.
    rows = []
    excluir = {"artefatos_sha256.csv", "reconciliacao.json", "resumo.json",
               "metricas_operacionais.json", "metricas.prom"}
    for p in sorted([x for x in saida.rglob("*") if x.is_file() and ".state" not in x.parts and "historico" not in x.parts]):
        if p.name in excluir or p.name.endswith(".parcial"): continue
        rows.append({"arquivo": str(p.relative_to(saida)), "bytes": p.stat().st_size, "sha256": sha256_arquivo(p)})
    import pandas as pd
    pd.DataFrame(rows).to_csv(saida / "artefatos_sha256.csv", sep=";", index=False)
    merkle_raw = "\n".join(f"{r['arquivo']}:{r['sha256']}" for r in rows).encode()
    merkle = hashlib.sha256(merkle_raw).hexdigest()
    out = {
        "linhas_origem": origem,
        "linhas_funil_saida": declarada,
        "linhas_parquet": parquet_n,
        "linhas_csv": csv_n,
        "arquivos_parquet": parquet_files,
        "recursos_manifesto": manifesto_n,
        "checks": checks,
        "artifact_set_sha256": merkle,
        "gate_reconciliacao": "OK" if all(checks.values()) else "FALHOU",
    }
    tmp = saida / "reconciliacao.json.parcial"
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(saida / "reconciliacao.json")
    return out
