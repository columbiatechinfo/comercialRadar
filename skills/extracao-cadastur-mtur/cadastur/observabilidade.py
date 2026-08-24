"""Métricas operacionais em formato JSON e Prometheus textfile."""
from __future__ import annotations
import json, time
from pathlib import Path


def gravar(saida: Path, resumo: dict, duracoes: dict[str,float] | None = None) -> None:
    duracoes = duracoes or {}
    payload = {"gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "resumo": resumo, "duracoes_s": duracoes}
    (saida / "metricas_operacionais.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# HELP cadastur_linhas_bronze Linhas materializadas no bronze",
        "# TYPE cadastur_linhas_bronze gauge",
        f"cadastur_linhas_bronze {int(resumo.get('linhas_bronze',0))}",
        "# HELP cadastur_recursos Recursos do catalogo no run",
        "# TYPE cadastur_recursos gauge",
        f"cadastur_recursos {int(resumo.get('recursos',0))}",
        f"cadastur_dq_erros {int(resumo.get('dq_erros',0))}",
        f"cadastur_dq_alertas {int(resumo.get('dq_alertas',0))}",
        f"cadastur_schema_drift_eventos {int(resumo.get('schema_drift_eventos',0))}",
    ]
    for k,v in sorted(duracoes.items()):
        safe = ''.join(ch if ch.isalnum() or ch=='_' else '_' for ch in k)
        lines.append(f"cadastur_etapa_duracao_segundos{{etapa=\"{safe}\"}} {float(v):.6f}")
    (saida / "metricas.prom").write_text("\n".join(lines)+"\n", encoding="utf-8")
