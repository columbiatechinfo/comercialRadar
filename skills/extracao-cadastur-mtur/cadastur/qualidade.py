"""Data Quality não destrutivo: mede, sinaliza e quarentena por evidência.

Nenhuma linha é removida do bronze. A quarentena contém apenas referência à PK,
regra, severidade e valor mascarado/hash quando necessário.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None

from .esquema import PII


def _digits(v: object) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _dv_doc(s: str, kind: str) -> bool:
    if kind == "cnpj":
        if len(s) != 14 or not s.isdigit() or len(set(s)) == 1:
            return False
        p1 = [5,4,3,2,9,8,7,6,5,4,3,2]
        p2 = [6] + p1
        d1 = (11 - sum(int(a)*b for a,b in zip(s,p1)) % 11) % 11
        d1 = 0 if d1 > 9 else d1
        d2 = (11 - sum(int(a)*b for a,b in zip(s[:12] + str(d1),p2)) % 11) % 11
        d2 = 0 if d2 > 9 else d2
        return s[12:] == f"{d1}{d2}"
    if kind == "cpf":
        if len(s) != 11 or not s.isdigit() or len(set(s)) == 1:
            return False
        nums = [int(x) for x in s]
        d1 = (sum(nums[i]*(10-i) for i in range(9))*10) % 11
        d1 = 0 if d1 == 10 else d1
        d2 = (sum(nums[i]*(11-i) for i in range(10))*10) % 11
        d2 = 0 if d2 == 10 else d2
        return nums[9] == d1 and nums[10] == d2
    return False


def _mask(col: str, value: object) -> tuple[str, str]:
    s = str(value or "").strip()
    if not s:
        return "", ""
    h = hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()
    if col in PII:
        if col in {"cnpj", "cpf"}:
            d = _digits(s)
            return ("*" * max(0, len(d)-4) + d[-4:]) if d else "***", h
        if "email" in col:
            parts = s.split("@",1)
            return (parts[0][:1] + "***@" + parts[1]) if len(parts)==2 else "***", h
        return "***", h
    return s[:120], h


def _issue(rows: list[dict], r: pd.Series, col: str, rule: str, severity: str, value: object) -> None:
    masked, h = _mask(col, value)
    rows.append({
        "_recurso_id": str(r.get("_recurso_id", "")),
        "_linha_origem": str(r.get("_linha_origem", "")),
        "_dataset": str(r.get("_dataset", "")),
        "campo": col,
        "regra": rule,
        "severidade": severity,
        "valor_mascarado": masked,
        "valor_sha256": h,
    })


def avaliar(parquet_base: Path, saida: Path) -> dict:
    if pq is None:
        raise RuntimeError("pyarrow nao instalado; qualidade requer leitura Parquet")
    metricas = defaultdict(lambda: {"linhas": 0, "preenchidos": 0, "distintos_aprox": set()})
    issues: list[dict] = []
    total = 0
    for p in sorted(parquet_base.rglob("*.parquet")):
        d = pd.read_parquet(p).fillna("")
        total += len(d)
        for c in d.columns:
            s = d[c].astype(str)
            nonempty = s.str.strip().ne("")
            m = metricas[c]
            m["linhas"] += len(s)
            m["preenchidos"] += int(nonempty.sum())
            # amostra limitada para cardinalidade aproximada sem memória descontrolada
            if len(m["distintos_aprox"]) < 5000:
                m["distintos_aprox"].update(s[nonempty].head(5000).tolist())

        if "cnpj" in d.columns:
            raw = d["cnpj"].astype(str); dig = raw.str.replace(r"\D", "", regex=True)
            preench = raw.str.strip().ne("")
            tam_bad = preench & dig.str.len().ne(14)
            dv_bad = preench & ~tam_bad & ~dig.map(lambda x: _dv_doc(x, "cnpj"))
            for _, r in d[tam_bad].iterrows(): _issue(issues, r, "cnpj", "CNPJ_TAMANHO", "ERRO", r["cnpj"])
            for _, r in d[dv_bad].iterrows(): _issue(issues, r, "cnpj", "CNPJ_DV_INVALIDO", "ERRO", r["cnpj"])
        if "cpf" in d.columns:
            raw = d["cpf"].astype(str); dig = raw.str.replace(r"\D", "", regex=True)
            preench = raw.str.strip().ne("")
            tam_bad = preench & dig.str.len().ne(11)
            dv_bad = preench & ~tam_bad & ~dig.map(lambda x: _dv_doc(x, "cpf"))
            for _, r in d[tam_bad].iterrows(): _issue(issues, r, "cpf", "CPF_TAMANHO", "ERRO", r["cpf"])
            for _, r in d[dv_bad].iterrows(): _issue(issues, r, "cpf", "CPF_DV_INVALIDO", "ERRO", r["cpf"])
        if "uf" in d.columns:
            mask = d["uf"].astype(str).str.strip().ne("") & ~d["uf"].astype(str).str.upper().str.match(r"^[A-Z]{2}$")
            for _, r in d[mask].iterrows(): _issue(issues, r, "uf", "UF_FORMATO", "ALERTA", r["uf"])
        if "cep" in d.columns:
            mask = d["cep"].astype(str).str.strip().ne("")
            for _, r in d[mask].iterrows():
                if len(_digits(r["cep"])) != 8: _issue(issues, r, "cep", "CEP_FORMATO", "ALERTA", r["cep"])
        for col in ["uh", "leitos", "uh_acessiveis", "leitos_acessiveis", "qtd_veiculos", "qtd_embarcacoes"]:
            if col in d.columns:
                mask = d[col].astype(str).str.strip().ne("")
                for _, r in d[mask].iterrows():
                    v = str(r[col]).strip().replace(".", "", 1).replace(",", ".", 1)
                    try:
                        n = float(v)
                        if n < 0: _issue(issues, r, col, "NUMERO_NEGATIVO", "ERRO", r[col])
                    except ValueError:
                        _issue(issues, r, col, "NUMERO_INVALIDO", "ALERTA", r[col])
        if "uh" in d.columns and "leitos" in d.columns:
            uh = pd.to_numeric(d["uh"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
            le = pd.to_numeric(d["leitos"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
            mask = uh.notna() & le.notna() & (le < uh)
            for _, r in d[mask].iterrows(): _issue(issues, r, "leitos", "LEITOS_MENOR_QUE_UH", "ALERTA", r["leitos"])

    rows = []
    for c in sorted(metricas):
        m = metricas[c]
        preench = m["preenchidos"]
        rows.append({
            "campo": c,
            "linhas": m["linhas"],
            "preenchidos": preench,
            "nulos_vazios": m["linhas"] - preench,
            "completude_pct": round((preench / m["linhas"] * 100) if m["linhas"] else 100, 4),
            "distintos_aprox_ate_5000": len(m["distintos_aprox"]),
            "classe_lgpd": PII.get(c, ""),
        })
    pd.DataFrame(rows).to_csv(saida / "qualidade_campos.csv", sep=";", index=False)
    q = pd.DataFrame(issues, columns=["_recurso_id","_linha_origem","_dataset","campo","regra","severidade","valor_mascarado","valor_sha256"])
    q.to_csv(saida / "quarentena.csv.gz", sep=";", index=False, compression="gzip")
    sev = Counter(x["severidade"] for x in issues)
    return {
        "dq_linhas_avaliadas": total,
        "dq_ocorrencias": len(issues),
        "dq_erros": int(sev.get("ERRO",0)),
        "dq_alertas": int(sev.get("ALERTA",0)),
        "gate_data_quality": "OK" if sev.get("ERRO",0) == 0 else "ALERTA",
    }
