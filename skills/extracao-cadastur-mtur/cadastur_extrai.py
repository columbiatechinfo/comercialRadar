#!/usr/bin/env python3
"""Extracao Cadastur/MTur v3 — ingestao industrial reconciliada e observavel."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from cadastur import catalogo, coleta, elo_geo, leitura, saida, qualidade, reconciliacao, observabilidade, postgres
from cadastur.estado import Estado, catalog_hash, scope_hash
from cadastur.esquema import PROCEDENCIA, canoniza
from cadastur.historico import commit_registry, comparar_entidades, detectar_drift, fingerprint_schema, gravar_snapshot
from cadastur.materializa import consolidar, escrever_shard, sha256_arquivo, snapshot_entidades

VERSAO = (Path(__file__).parent / "VERSION").read_text().strip()


def _prefetch(recursos, cache: Path, refresh: bool, workers: int) -> dict[str, dict]:
    if workers <= 1:
        return {r.recurso_id: coleta.baixar(r, cache, refresh) for r in recursos}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cadastur-dl") as ex:
        futs = {ex.submit(coleta.baixar, r, cache, refresh): r for r in recursos}
        for fut in as_completed(futs):
            r = futs[fut]
            out[r.recurso_id] = fut.result()
    return out


def _ordenar_colunas(colunas: set[str]) -> list[str]:
    dados = sorted(c for c in colunas if c not in PROCEDENCIA and c != "_extras")
    return dados + ["_extras"] + [c for c in PROCEDENCIA if c in colunas]


def _transformar_recurso(rec, proc: dict, shards_dir: Path, agora: str) -> dict:
    """Transformação isolada por recurso; segura para execução concorrente."""
    df, meta_leitura = leitura.ler(Path(proc["arquivo"]))
    n_origem = len(df)
    novas: list[str] = []
    if n_origem:
        canon, novas = canoniza(df)
        canon["_linha_origem"] = range(1, n_origem + 1)
        canon["_dataset"] = rec.dataset
        canon["_atividade"] = rec.atividade
        canon["_recurso_id"] = rec.recurso_id
        canon["_recurso_nome"] = rec.recurso_nome
        canon["_ref_ano"] = rec.ref_ano
        canon["_ref_trimestre"] = rec.ref_trimestre
        canon["_ref_periodo"] = rec.ref_periodo
        canon["_formato_ckan"] = rec.formato_ckan
        canon["_formato_real"] = meta_leitura["formato_real"]
        canon["_encoding"] = meta_leitura.get("encoding") or ""
        canon["_separador"] = meta_leitura.get("separador") or ""
        canon["_aba"] = meta_leitura.get("aba") or ""
        canon["_sha256"] = proc["sha256"]
        canon["_extraido_em"] = agora
        shard = escrever_shard(canon, shards_dir / f"{rec.recurso_id}.parquet")
        shard_sha256 = sha256_arquivo(shard)
        colunas_canon = list(canon.columns)
        preenchidos = {c: int(df[c].astype(str).str.strip().ne("").sum()) for c in novas if c in df.columns}
    else:
        shard = shards_dir / f"{rec.recurso_id}.parquet"
        shard_sha256 = ""
        colunas_canon = []
        preenchidos = {}
    meta_estado = {
        "recurso": {"recurso_id": rec.recurso_id, "recurso_nome": rec.recurso_nome,
                    "dataset": rec.dataset, "atividade": rec.atividade,
                    "ref_ano": rec.ref_ano, "ref_trimestre": rec.ref_trimestre,
                    "ref_periodo": rec.ref_periodo, "formato_ckan": rec.formato_ckan},
        "download": proc, "leitura": meta_leitura, "linhas_saida": n_origem,
        "colunas_desconhecidas": novas, "preenchidos_desconhecidas": preenchidos,
        "colunas_canonicas": colunas_canon, "shard_sha256": shard_sha256,
    }
    return {"rec": rec, "n": n_origem, "shard": shard, "meta": meta_estado, "novas": novas,
            "meta_leitura": meta_leitura}


def main() -> int:
    ap = argparse.ArgumentParser(description="Extracao Cadastur/MTur (A2L) v3")
    ap.add_argument("--saida", type=Path, default=Path("./cadastur_out"))
    ap.add_argument("--cache", type=Path, default=None, help="default: <saida>/_cache")
    ap.add_argument("--datasets", default="", help="lista separada por virgula; vazio = todos")
    ap.add_argument("--desde", type=int, default=None)
    ap.add_argument("--ate", type=int, default=None)
    ap.add_argument("--cnpj-geocod", type=Path, default=None)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--workers-download", type=int, default=4)
    ap.add_argument("--workers-transform", type=int, default=2, help="leitura/canonizacao concorrente por recurso (1..8)")
    ap.add_argument("--nova-execucao", action="store_true",
                    help="ignora execucao incompleta do mesmo escopo e inicia novo run")
    ap.add_argument("--manter-shards", action="store_true",
                    help="mantem shards de checkpoint apos sucesso (debug; default remove para economizar disco)")
    ap.add_argument("--postgres-dsn", default="", help="opcional: carrega/atualiza PostgreSQL em staging transacional")
    ap.add_argument("--dq-falha-em-erro", action="store_true",
                    help="transforma ocorrencia DQ severidade ERRO em gate bloqueante")
    ap.add_argument("--falhar-em", default="", help=argparse.SUPPRESS)  # injecao de falha para selftest
    args = ap.parse_args()
    if not 1 <= args.workers_download <= 16:
        ap.error("--workers-download deve estar entre 1 e 16")
    if not 1 <= args.workers_transform <= 8:
        ap.error("--workers-transform deve estar entre 1 e 8")

    t0_total = time.monotonic()
    duracoes: dict[str, float] = {}
    saida_dir = args.saida
    saida_dir.mkdir(parents=True, exist_ok=True)
    cache = args.cache or saida_dir / "_cache"
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()] or None
    recursos = catalogo.descobrir(datasets, args.desde, args.ate)
    print(f"[catalogo] {len(recursos)} recursos | v{VERSAO}", flush=True)
    if not recursos:
        print("[erro] catalogo sem recursos para o recorte")
        return 1

    filtros = {"datasets": datasets or [], "desde": args.desde, "ate": args.ate}
    escopo = scope_hash(recursos, filtros)
    catalogo_fp = catalog_hash(recursos)
    estado = Estado(saida_dir / ".state")
    run_id, retomada = estado.iniciar_ou_retomar(escopo, catalogo_fp, VERSAO, args.nova_execucao)
    run_dir = saida_dir / ".state" / "runs" / run_id
    shards_dir = run_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)
    print(f"[run] {run_id} | {'RETOMADA' if retomada else 'NOVO'}", flush=True)

    try:
        feitos = estado.listar_ok(run_id)
        # Revalida o proprio shard: checkpoint corrompido vira pendencia, nunca sucesso silencioso.
        for rid, x in list(feitos.items()):
            if int(x.get("linhas_saida") or 0) == 0:
                continue
            sp = Path(x.get("shard") or "")
            esperado = x.get("meta", {}).get("shard_sha256", "")
            valido = sp.exists() and bool(esperado) and sha256_arquivo(sp) == esperado
            if not valido:
                estado.invalidar_recurso(run_id, rid, "shard ausente/corrompido no checkpoint")
                feitos.pop(rid, None)
        pendentes = [r for r in recursos if r.recurso_id not in feitos]
        print(f"[checkpoint] {len(feitos)} concluido(s) validado(s), {len(pendentes)} pendente(s)", flush=True)

        downloads = {}
        if pendentes:
            print(f"[coleta] {len(pendentes)} recurso(s), {args.workers_download} worker(s)", flush=True)
            downloads = _prefetch(pendentes, cache, args.refresh, args.workers_download)

        agora = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if pendentes:
            print(f"[transformacao] {len(pendentes)} recurso(s), {args.workers_transform} worker(s)", flush=True)
        def registrar_resultado(result: dict, i: int) -> None:
            rec = result["rec"]; n_origem = result["n"]; meta_estado = result["meta"]
            estado.marcar_ok(run_id, rec.recurso_id, sha256=meta_estado["download"]["sha256"],
                              linhas_origem=n_origem, linhas_saida=n_origem,
                              shard=result["shard"], meta=meta_estado)
            ml = result["meta_leitura"]; novas = result["novas"]
            print(f"  [{i}/{len(pendentes)}] {rec.dataset[:30]:32s} {rec.ref_rotulo[:20]:22s} "
                  f"{ml['formato_real']:5s} {n_origem:7d} linhas"
                  + (f" +{len(novas)} col. novas" if novas else "")
                  + (f" ragged={ml.get('linhas_ragged',0)}" if ml.get("linhas_ragged") else ""), flush=True)

        if args.workers_transform <= 1:
            for i, rec in enumerate(pendentes, 1):
                try:
                    if args.falhar_em and rec.recurso_id == args.falhar_em:
                        raise RuntimeError("falha injetada para teste de retomada")
                    registrar_resultado(_transformar_recurso(rec, downloads[rec.recurso_id], shards_dir, agora), i)
                except Exception as e:
                    estado.marcar_erro(run_id, rec.recurso_id, repr(e)); raise
        else:
            with ThreadPoolExecutor(max_workers=args.workers_transform, thread_name_prefix="cadastur-xform") as ex:
                futs = {}
                for rec in pendentes:
                    if args.falhar_em and rec.recurso_id == args.falhar_em:
                        estado.marcar_erro(run_id, rec.recurso_id, "falha injetada para teste de retomada")
                        raise RuntimeError("falha injetada para teste de retomada")
                    futs[ex.submit(_transformar_recurso, rec, downloads[rec.recurso_id], shards_dir, agora)] = rec
                concluidos = 0
                for fut in as_completed(futs):
                    rec = futs[fut]
                    try:
                        result = fut.result(); concluidos += 1; registrar_resultado(result, concluidos)
                    except Exception as e:
                        estado.marcar_erro(run_id, rec.recurso_id, repr(e))
                        for f in futs: f.cancel()
                        raise

        feitos = estado.listar_ok(run_id)
        if len(feitos) != len(recursos):
            faltam = len(recursos) - len(feitos)
            raise RuntimeError(f"checkpoint incompleto: faltam {faltam} recurso(s)")

        # Reconstrucao integral dos metadados a partir do journal: funciona igual em retomada.
        funil, desconhecidas, manifestos = [], [], []
        colunas_union: set[str] = set()
        schema_origem: dict[str, set[str]] = {}
        shards: list[Path] = []
        by_id = {r.recurso_id: r for r in recursos}
        for rec in recursos:  # ordem do catalogo = ordem deterministica da consolidacao
            x = feitos[rec.recurso_id]
            m = x["meta"]
            leitura_m = m.get("leitura", {})
            n0, n1 = int(x["linhas_origem"] or 0), int(x["linhas_saida"] or 0)
            funil.append({"recurso_id": rec.recurso_id, "recurso": rec.recurso_nome,
                          "dataset": rec.dataset, "linhas_origem": n0, "linhas_saida": n1,
                          "delta": n0 - n1, "motivo": "" if n0 else "recurso sem linhas de dados",
                          "linhas_ragged": leitura_m.get("linhas_ragged", 0)})
            for c in m.get("colunas_desconhecidas", []):
                desconhecidas.append({"recurso_id": rec.recurso_id, "dataset": rec.dataset,
                                      "coluna": c, "n": n0,
                                      "preenchidos": m.get("preenchidos_desconhecidas", {}).get(c, 0)})
            manifestos.append(m.get("download", {}) | leitura_m | {
                "linhas_saida": n1, "colunas_desconhecidas": m.get("colunas_desconhecidas", [])
            })
            colunas_union.update(m.get("colunas_canonicas", []))
            schema_origem.setdefault(rec.dataset, set()).update(map(str, leitura_m.get("colunas_origem", [])))
            if n1:
                sp = Path(x["shard"])
                if not sp.exists():
                    raise RuntimeError(f"checkpoint aponta para shard ausente: {sp}")
                shards.append(sp)

        if not shards:
            raise RuntimeError("nenhum recurso com linhas lido")

        colunas = _ordenar_colunas(colunas_union)
        print(f"[materializacao] {len(shards)} shards -> Parquet/CSV sem concat global", flush=True)
        consolidar(shards, colunas, saida_dir / "bronze_parquet", saida_dir / "bronze_cadastur.csv.gz")

        # Auditorias e manifesto tambem usam troca atomica.
        man_tmp = saida_dir / "manifesto.jsonl.parcial"
        with man_tmp.open("w", encoding="utf-8") as f:
            for m in manifestos:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        man_tmp.replace(saida_dir / "manifesto.jsonl")
        saida.gravar_auditoria(saida_dir, funil, desconhecidas, colunas)
        saida.gerar_ddl(colunas, saida_dir)
        drift, registry = detectar_drift(saida_dir, schema_origem, escopo)
        pd.DataFrame(drift or [{"dataset":"", "evento":"SEM_DRIFT", "coluna":""}]).to_csv(
            saida_dir / "schema_drift.csv", index=False, sep=";"
        )
        geo = elo_geo.elo_parquet(saida_dir / "bronze_parquet", saida_dir, args.cnpj_geocod)

        t_dq = time.monotonic()
        dq = qualidade.avaliar(saida_dir / "bronze_parquet", saida_dir)
        duracoes["data_quality"] = time.monotonic() - t_dq
        if args.dq_falha_em_erro and int(dq.get("dq_erros", 0)):
            raise RuntimeError(f"gate Data Quality bloqueou o run: {dq['dq_erros']} ocorrencia(s) ERRO")

        # Snapshot temporal compacto por entidade, isolado pelo mesmo escopo.
        entidades = snapshot_entidades(saida_dir / "bronze_parquet")
        ent_dir = saida_dir / ".state" / "entity_snapshots"
        ent_dir.mkdir(parents=True, exist_ok=True)
        ent_latest = ent_dir / f"{escopo[:16]}.parquet"
        eventos = comparar_entidades(entidades, ent_latest if ent_latest.exists() else None, saida_dir)
        ent_tmp = ent_latest.with_suffix(".parquet.parcial")
        entidades.to_parquet(ent_tmp, index=False, compression="zstd")

        t_rec = time.monotonic()
        rec = reconciliacao.reconciliar(saida_dir, funil, len(recursos))
        duracoes["reconciliacao"] = time.monotonic() - t_rec
        if rec["gate_reconciliacao"] != "OK":
            raise RuntimeError(f"reconciliacao ponta a ponta falhou: {rec['checks']}")

        pg = {"gate_postgres": "NAO_SOLICITADO"}
        if args.postgres_dsn:
            t_pg = time.monotonic()
            pg = postgres.carregar(args.postgres_dsn, saida_dir, run_id)
            duracoes["postgres"] = time.monotonic() - t_pg

        delta = sum(f["delta"] for f in funil)
        resumo = {
            "versao": VERSAO, "run_id": run_id, "retomada": retomada,
            "scope_hash": escopo, "catalog_hash": catalogo_fp, "recursos": len(recursos),
            "recursos_reaproveitados_checkpoint": len(recursos) - len(pendentes),
            "linhas_origem": sum(f["linhas_origem"] for f in funil),
            "linhas_bronze": sum(f["linhas_saida"] for f in funil),
            "delta_funil": delta,
            "linhas_csv_ragged_preservadas": sum(int(f.get("linhas_ragged",0)) for f in funil),
            "colunas": len(colunas),
            "colunas_desconhecidas": len({d["coluna"] for d in desconhecidas}),
            "schema_fingerprint": fingerprint_schema(colunas),
            "schema_drift_eventos": len(drift),
            "gate_zero_perda": "OK" if delta == 0 else "FALHOU",
            "gate_checkpoint": "OK",
            "artifact_set_sha256": rec.get("artifact_set_sha256", ""),
            "gate_reconciliacao": rec.get("gate_reconciliacao", "FALHOU"),
        } | geo | dq | pg | eventos
        duracoes["total_ate_gates"] = time.monotonic() - t0_total
        resumo_tmp = saida_dir / "resumo.json.parcial"
        resumo_tmp.write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
        resumo_tmp.replace(saida_dir / "resumo.json")
        observabilidade.gravar(saida_dir, resumo, duracoes)

        gravar_snapshot(saida_dir, run_id,
                        [saida_dir / "manifesto.jsonl", saida_dir / "funil.csv",
                         saida_dir / "colunas_desconhecidas.csv", saida_dir / "schema_drift.csv",
                         saida_dir / "eventos_entidade.csv.gz", saida_dir / "reconciliacao.json",
                         saida_dir / "artefatos_sha256.csv", saida_dir / "qualidade_campos.csv",
                         saida_dir / "quarentena.csv.gz", saida_dir / "metricas_operacionais.json",
                         saida_dir / "metricas.prom"],
                        resumo, registry, drift)
        # Baselines so avancam quando TODOS os artefatos do run estao consistentes.
        commit_registry(saida_dir, escopo, registry)
        ent_tmp.replace(ent_latest)
        estado.finalizar(run_id, "OK")
        if not args.manter_shards:
            import shutil
            shutil.rmtree(shards_dir, ignore_errors=True)
        print(json.dumps(resumo, ensure_ascii=False, indent=1))
        return 0 if delta == 0 else 2
    except Exception as e:
        estado.pausar_retomavel(run_id, repr(e))
        print(f"[erro] run {run_id} preservado para retomada: {e}", flush=True)
        return 1
    finally:
        estado.close()


if __name__ == "__main__":
    raise SystemExit(main())
