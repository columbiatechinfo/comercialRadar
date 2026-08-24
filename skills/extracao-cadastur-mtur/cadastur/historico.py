"""Historico leve de snapshots, drift de schema e eventos temporais."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd


def fingerprint_schema(colunas: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(map(str, colunas))).encode()).hexdigest()


def _load_registry(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def caminho_registry(saida: Path, scope_hash: str) -> Path:
    state = saida / ".state"
    state.mkdir(parents=True, exist_ok=True)
    return state / f"schema_registry_{scope_hash[:16]}.json"


def detectar_drift(saida: Path, atual: dict[str, set[str]], scope_hash: str) -> tuple[list[dict], dict]:
    """Detecta drift sem alterar baseline. O commit ocorre apenas no fim do run."""
    reg_path = caminho_registry(saida, scope_hash)
    primeira = not reg_path.exists()
    anterior = _load_registry(reg_path)
    eventos: list[dict] = []
    for ds in sorted(set(anterior) | set(atual)) if not primeira else []:
        old = set(anterior.get(ds, []))
        new = set(atual.get(ds, set()))
        for c in sorted(new - old):
            eventos.append({"dataset": ds, "evento": "COLUNA_ADICIONADA", "coluna": c})
        for c in sorted(old - new):
            eventos.append({"dataset": ds, "evento": "COLUNA_REMOVIDA", "coluna": c})
    novo = {k: sorted(v) for k, v in sorted(atual.items())}
    return eventos, novo


def commit_registry(saida: Path, scope_hash: str, registry: dict) -> Path:
    reg_path = caminho_registry(saida, scope_hash)
    tmp = reg_path.with_suffix(".json.parcial")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(reg_path)
    return reg_path


def gravar_snapshot(saida: Path, run_id: str, arquivos: list[Path], resumo: dict,
                    schema_registry: dict, drift: list[dict]) -> Path:
    hist = saida / "historico" / run_id
    hist.mkdir(parents=True, exist_ok=True)
    for p in arquivos:
        if p.exists():
            shutil.copy2(p, hist / p.name)
    (hist / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    (hist / "schema_registry.json").write_text(json.dumps(schema_registry, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(drift or [{"dataset":"", "evento":"SEM_DRIFT", "coluna":""}]).to_csv(
        hist / "schema_drift.csv", index=False, sep=";"
    )
    return hist


def comparar_entidades(atual: pd.DataFrame, anterior_path: Path | None, saida: Path) -> dict:
    """Compara snapshots de entidade e distingue alteracao de mera permanencia."""
    alvo = saida / "eventos_entidade.csv.gz"
    cols = ["_dataset", "chave_entidade", "fingerprint_entidade"]
    cur = atual[cols].drop_duplicates(["_dataset","chave_entidade"], keep="last")
    if anterior_path is None or not anterior_path.exists():
        ev = cur.copy()
        ev["evento"] = "ENTROU"
        ev[["evento"] + cols].to_csv(alvo, index=False, sep=";", compression="gzip")
        return {"entidades_entraram": len(ev), "entidades_sairam": 0,
                "entidades_permaneceram": 0, "entidades_alteradas": 0}
    ant = pd.read_parquet(anterior_path, columns=cols).drop_duplicates(
        ["_dataset","chave_entidade"], keep="last")
    m = cur.merge(ant, how="outer", on=["_dataset","chave_entidade"],
                  suffixes=("_atual","_anterior"), indicator=True)
    def evento(r):
        if r["_merge"] == "left_only": return "ENTROU"
        if r["_merge"] == "right_only": return "SAIU"
        return "PERMANECEU" if r["fingerprint_entidade_atual"] == r["fingerprint_entidade_anterior"] else "ALTEROU"
    m["evento"] = m.apply(evento, axis=1)
    m.to_csv(alvo, index=False, sep=";", compression="gzip")
    vc = m["evento"].value_counts()
    return {
        "entidades_entraram": int(vc.get("ENTROU", 0)),
        "entidades_sairam": int(vc.get("SAIU", 0)),
        "entidades_permaneceram": int(vc.get("PERMANECEU", 0)),
        "entidades_alteradas": int(vc.get("ALTEROU", 0)),
    }
