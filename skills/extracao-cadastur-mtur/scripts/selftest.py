#!/usr/bin/env python3
"""Selftest da extracao-cadastur-mtur. Gate nas duas pontas (diretrizes-a2l §4).

  python scripts/selftest.py                # so os testes offline
  python scripts/selftest.py --saida ./out  # + auditoria de uma execucao real
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from cadastur.catalogo import parse_periodo
from cadastur.esquema import MAPA, PII, canoniza, chave
from cadastur.leitura import _cabecalhos_unicos
from cadastur.estado import Estado
from cadastur.historico import commit_registry, detectar_drift
from cadastur import saida
from cadastur.qualidade import _dv_doc
from cadastur.postgres import _ident

FALHAS: list[str] = []


def ok(cond: bool, nome: str, detalhe: str = "") -> None:
    print(f"  {'OK  ' if cond else 'FALHA'} {nome}" + (f" — {detalhe}" if detalhe else ""))
    if not cond:
        FALHAS.append(nome)


def dv_cnpj(s: str) -> bool:
    if len(s) != 14 or not s.isdigit():
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    d1 = (11 - sum(int(a) * b for a, b in zip(s, p1)) % 11) % 11
    d1 = 0 if d1 > 9 else d1
    d2 = (11 - sum(int(a) * b for a, b in zip(s[:12] + str(d1), p2)) % 11) % 11
    d2 = 0 if d2 > 9 else d2
    return s[12:] == f"{d1}{d2}"


def offline() -> None:
    print("[1] parse de periodo")
    ok(parse_periodo("Segundo Trimestre de 2024") == (2024, 2), "trimestre por extenso")
    ok(parse_periodo("2006") == (2006, 0), "arquivo anual legado")
    ok(parse_periodo("Quarto Trimestre 2016") == (2016, 4), "trimestre sem 'de'")
    try:
        parse_periodo("sem ano")
        ok(False, "rotulo sem ano deve falhar")
    except ValueError:
        ok(True, "rotulo sem ano falha explicitamente")

    print("[2] canonizacao — invariante de zero perda")
    df = pd.DataFrame({"RAZÃO SOCIAL": ["A"], "Nome Fantasia": ["B"],
                       "COLUNA QUE NAO EXISTE": ["C"], "UH": ["10"]})
    out, novas = canoniza(df)
    ok(list(novas) == ["COLUNA QUE NAO EXISTE"], "rotulo novo detectado")
    ok(json.loads(out["_extras"].iloc[0])["COLUNA QUE NAO EXISTE"] == "C",
       "valor desconhecido preservado integro em _extras")
    ok(out["razao_social"].iloc[0] == "A" and out["uh"].iloc[0] == "10", "rotulo mapeado")

    print("[3] chave de rotulo insensivel a acento/caixa/espaco")
    ok(chave("  Número  de Inscrição do CNPJ ") == "NUMERO DE INSCRICAO DO CNPJ", "normalizacao")
    ok(MAPA[chave("CNPJ")] == MAPA[chave("Número de Inscrição do CNPJ")],
       "geracoes convergem para a mesma coluna")

    print("[4] campos semanticamente distintos nao sao fundidos")
    ok(MAPA["DATA INICIO OPERACAO"] != MAPA["DATA DE ABERTURA"], "data de operacao != abertura")
    ok(MAPA["E-MAIL2"] != MAPA["E-MAIL INSTITUCIONAL"], "e-mail legado != institucional")

    print("[5] determinismo")
    a, _ = canoniza(df)
    b, _ = canoniza(df)
    ok(a.equals(b), "mesma entrada -> mesma saida")

    print("[5b] aliases e cabecalhos nunca perdem valor")
    dfa = pd.DataFrame({"CNPJ": ["111"], "Número de Inscrição do CNPJ": ["222"]})
    ca, _ = canoniza(dfa)
    ok(ca["cnpj"].iloc[0] == "111", "precedencia deterministica de alias")
    ok(json.loads(ca["_extras"].iloc[0])["Número de Inscrição do CNPJ"] == "222",
       "alias concorrente preservado em _extras")
    hs = _cabecalhos_unicos(["A", "A", "", "B"])
    ok(len(hs) == len(set(hs)) == 4 and hs[2].startswith("__SEM_ROTULO_"),
       "cabecalho duplicado/vazio recebe identidade rastreavel")

    print("[6] inventario LGPD")
    ok("tipo_sanguineo" in PII and "SENSIVEL" in PII["tipo_sanguineo"].upper(),
       "dado de saude classificado como sensivel")
    ok(all(v in set(MAPA.values()) for v in PII if not v.startswith("_")),
       "toda coluna classificada existe no mapa")

    print("[6b] checkpoint transacional e drift por escopo")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        est = Estado(base / ".state")
        run1, retom1 = est.iniciar_ou_retomar("escopo-A", "catalogo-1", "2.0.0")
        ok(not retom1, "primeiro run e novo")
        run2, retom2 = est.iniciar_ou_retomar("escopo-A", "catalogo-1", "2.0.0")
        ok(retom2 and run2 == run1, "run incompleto e retomado")
        run_changed, ret_changed = est.iniciar_ou_retomar("escopo-A", "catalogo-2", "2.0.0")
        ok(not ret_changed and run_changed != run1, "catalogo alterado nao mistura retomada")
        est.finalizar(run_changed, "OK")
        est.finalizar(run1, "OK")
        run3, retom3 = est.iniciar_ou_retomar("escopo-A", "catalogo-1", "2.0.0")
        ok(not retom3 and run3 != run1, "run concluido nao e reaproveitado")
        est.close()

        drift0, reg0 = detectar_drift(base, {"ds": {"A", "B"}}, "escopo-A")
        ok(drift0 == [], "primeiro schema vira baseline, nao falso drift")
        commit_registry(base, "escopo-A", reg0)
        drift1, reg1 = detectar_drift(base, {"ds": {"A", "C"}}, "escopo-A")
        eventos = {(x["evento"], x["coluna"]) for x in drift1}
        ok(("COLUNA_ADICIONADA", "C") in eventos and ("COLUNA_REMOVIDA", "B") in eventos,
           "drift detecta adicao e remocao")
        drift_outro, _ = detectar_drift(base, {"ds": {"X"}}, "escopo-B")
        ok(drift_outro == [], "escopos diferentes nao contaminam baseline")

    print("[6c] gates v3: DDL, documentos e identificadores")
    ok(_dv_doc("11222333000181", "cnpj"), "CNPJ valido reconhecido")
    ok(not _dv_doc("00000000000000", "cnpj"), "CNPJ repetido rejeitado")
    ok(_dv_doc("52998224725", "cpf"), "CPF valido reconhecido")
    try:
        _ident("x;drop table")
        ok(False, "identificador SQL inseguro deve falhar")
    except ValueError:
        ok(True, "identificador SQL inseguro falha explicitamente")
    with tempfile.TemporaryDirectory() as td:
        ddl = saida.gerar_ddl(["cnpj","_extras","_dataset","_recurso_id","_ref_periodo","_linha_origem"], Path(td))
        sql = ddl.read_text(encoding="utf-8").upper()
        ok("DROP TABLE" not in sql, "DDL nao destrutivo")
        ok("ADD COLUMN IF NOT EXISTS" in sql, "DDL evolutivo/idempotente")


def auditar(saida: Path) -> None:
    print(f"[7] auditoria da execucao em {saida}")
    resumo = json.loads((saida / "resumo.json").read_text())
    funil = pd.read_csv(saida / "funil.csv", sep=";")
    ok(resumo["delta_funil"] == 0, "funil fecha", f"delta={resumo['delta_funil']}")
    ok(int(funil.linhas_origem.sum()) == resumo["linhas_bronze"],
       "soma do funil == linhas do bronze")

    arqs = glob.glob(str(saida / "bronze_parquet" / "**" / "*.parquet"), recursive=True)
    df = pd.concat([pd.read_parquet(a) for a in arqs], ignore_index=True)
    ok(len(df) == resumo["linhas_bronze"], "parquet == bronze declarado",
       f"{len(df)} vs {resumo['linhas_bronze']}")
    ok(not df.duplicated(["_recurso_id", "_linha_origem"]).any(),
       "PK (_recurso_id,_linha_origem) unica")
    ok(df["_sha256"].notna().all() and df["_ref_periodo"].notna().all(),
       "procedencia carimbada em toda linha")

    if "cnpj" in df.columns:
        c = df["cnpj"].astype(str).str.replace(r"\D", "", regex=True)
        c = c[c.str.len() == 14]
        taxa = c.map(dv_cnpj).mean() if len(c) else 1.0
        ok(taxa > 0.99, "DV do CNPJ valido", f"{taxa:.4f}")

    n_manif = sum(1 for _ in (saida / "manifesto.jsonl").open())
    ok(n_manif == resumo["recursos"], "manifesto cobre todos os recursos")
    ok((saida / "inventario_pii.csv").exists() and (saida / "ddl_cadastur.sql").exists(),
       "artefatos de auditoria e DDL emitidos")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", type=Path, default=None)
    a = ap.parse_args()
    offline()
    if a.saida:
        auditar(a.saida)
    print(f"\n{'SELFTEST VERDE' if not FALHAS else 'SELFTEST VERMELHO: ' + ', '.join(FALHAS)}")
    raise SystemExit(0 if not FALHAS else 1)
