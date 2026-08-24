#!/usr/bin/env python3
"""E2E sintético sem rede: shard -> materialização -> DQ -> reconciliação.

Requer as dependências completas do requirements.txt. Não toca o portal MTur.
"""
from __future__ import annotations
import json, tempfile, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd

from cadastur.materializa import escrever_shard, consolidar
from cadastur import qualidade, reconciliacao, saida


def row(rid, line, cnpj, nome, uf="RS", cep="92000000", uh="10", leitos="20"):
    return {
        "cnpj": cnpj, "nome_fantasia": nome, "uf": uf, "cep": cep, "uh": uh, "leitos": leitos,
        "_extras": "{}", "_dataset": "meios-de-hospedagem", "_atividade": "Meios de Hospedagem",
        "_recurso_id": rid, "_recurso_nome": rid, "_ref_ano": 2026, "_ref_trimestre": 2,
        "_ref_periodo": "2026-06-30", "_formato_ckan": "CSV", "_formato_real": "csv",
        "_encoding": "utf-8", "_separador": ";", "_aba": "", "_sha256": "a"*64,
        "_linha_origem": line, "_extraido_em": "2026-08-22T00:00:00-0300",
    }


def main() -> int:
    try:
        import pyarrow  # noqa
    except ImportError:
        print("E2E NAO EXECUTADO: pyarrow ausente. Rode pip install -r requirements.txt")
        return 2
    with tempfile.TemporaryDirectory() as td:
        base=Path(td); shards=base/"shards"; out=base/"out"; out.mkdir()
        a=pd.DataFrame([
            row("r1",1,"11222333000181","Hotel\nLinha Dois"),
            row("r1",2,"00000000000000","Hotel B", uf="RIO GRANDE DO SUL", cep="9200"),
        ])
        b=pd.DataFrame([row("r2",1,"19131243000197","Hotel C", uh="10", leitos="5")])
        cols=list(a.columns)
        s1=escrever_shard(a, shards/"r1.parquet"); s2=escrever_shard(b, shards/"r2.parquet")
        consolidar([s1,s2], cols, out/"bronze_parquet", out/"bronze_cadastur.csv.gz")
        funil=[
            {"recurso_id":"r1","recurso":"r1","dataset":"meios-de-hospedagem","linhas_origem":2,"linhas_saida":2,"delta":0,"motivo":"","linhas_ragged":0},
            {"recurso_id":"r2","recurso":"r2","dataset":"meios-de-hospedagem","linhas_origem":1,"linhas_saida":1,"delta":0,"motivo":"","linhas_ragged":0},
        ]
        saida.gravar_auditoria(out, funil, [], cols); saida.gerar_ddl(cols,out)
        with (out/"manifesto.jsonl").open("w",encoding="utf-8") as f:
            f.write(json.dumps({"recurso_id":"r1"})+"\n"); f.write(json.dumps({"recurso_id":"r2"})+"\n")
        dq=qualidade.avaliar(out/"bronze_parquet",out)
        rec=reconciliacao.reconciliar(out,funil,2)
        ddl=(out/"ddl_cadastur.sql").read_text(encoding="utf-8")
        checks={
            "3 linhas": rec["linhas_parquet"]==3==rec["linhas_csv"],
            "reconciliacao OK": rec["gate_reconciliacao"]=="OK",
            "DQ detecta anomalias": dq["dq_erros"]>=1 and dq["dq_alertas"]>=1,
            "DDL nao destrutivo": "DROP TABLE" not in ddl.upper() and "ADD COLUMN IF NOT EXISTS" in ddl.upper(),
            "hash artefatos": len(rec["artifact_set_sha256"])==64,
        }
        for k,v in checks.items(): print(("OK   " if v else "FALHA")+k)
        return 0 if all(checks.values()) else 1

if __name__ == "__main__": raise SystemExit(main())
