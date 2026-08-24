#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entrypoint unico da skill extracao-poi-estadual (v3).

    python poi_estadual.py run --uf RS --excluir 4314902 \
        --fontes overture,osm,fsq --min-conf 0.0 \
        --formatos csv,geoparquet --gerar-mapa --base-dir ./execucao_rs

Etapas: init -> fetch -> raw -> territory -> normalize -> dedup -> export -> map -> validate
Cada uma e retomavel e so reaproveita artefato cujo hash de escopo bate com a
config atual. `--ate <etapa>` para no meio; `--etapa <etapa>` roda so uma.
"""
import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from poi_estadual import foursquare, ibge, mapa, normalizacao, osm, overture  # noqa: E402
from poi_estadual import territorio, validacao, exportacao  # noqa: E402
from poi_estadual.config import ETAPAS, Config, ConfigInvalida  # noqa: E402
from poi_estadual.manifest import EtapaBloqueada, Manifesto  # noqa: E402


def _lista(s):
    return tuple(x.strip() for x in str(s).split(",") if x.strip())


def _config(a):
    return Config(
        uf=a.uf, excluir=_lista(a.excluir or ""), fontes=_lista(a.fontes),
        min_conf=a.min_conf, formatos=_lista(a.formatos), gerar_mapa=a.gerar_mapa,
        base_dir=a.base_dir, malha_qualidade=a.malha_qualidade,
        simplificar_graus=a.simplificar_graus,
        osm_predicado=a.osm_predicado, osm_sem_nome=not a.osm_exigir_nome,
        dedup_modo=a.dedup, dedup_raio_m=a.dedup_raio_m,
        dedup_sim_min=a.dedup_sim_min, dedup_sim_cross=a.dedup_sim_cross,
        dedup_jaccard_min=a.dedup_jaccard, dedup_diam_max_m=a.dedup_diametro,
        dedup_ctx_raio_m=a.dedup_ctx_raio, dedup_ctx_min=a.dedup_ctx_min,
        dedup_semnome_modo=a.dedup_semnome, dedup_celula_m=a.dedup_celula,
        dedup_halo_m=a.dedup_halo, max_fusao_suspeita=a.max_fusao_suspeita,
        budget_s=a.budget, ov_cap=a.ov_cap, ov_tile_graus=a.ov_tile_graus,
        treat_batch=a.treat_batch, clip_chunk=a.clip_chunk, fsq_strips=a.fsq_strips,
        duckdb_memory=a.duckdb_memory, threads=a.threads,
        source_mode=a.source_mode, refresh_fontes=_lista(a.refresh_source or ""),
        malha_parquet=a.malha_parquet or "").preparar()


def _fetch(cfg, man, bbox):
    """Coleta fetch-once por fonte. So marca `completed` se TODAS concluirem."""
    man.iniciar("fetch")
    estado = {}
    if "fsq" in cfg.fontes:
        foursquare.gate(cfg)     # falha em segundos, antes dos ~421 MB do OSM
    if "overture" in cfg.fontes:
        estado["overture"] = overture.executar(cfg, man)
    if "osm" in cfg.fontes:
        estado["osm"] = osm.executar(cfg, man)
    if "fsq" in cfg.fontes:
        estado["fsq"] = foursquare.executar(cfg, man, bbox)
    completo = all(e.get("completo") for e in estado.values())
    contagens = {"%s_%s" % (f, k): v for f, e in estado.items()
                 for k, v in e.items() if isinstance(v, int)}
    if completo:
        man.concluir("fetch", **contagens)
    else:
        man.parcial("fetch", **contagens)
        print("FETCH incompleto — reexecute a mesma linha de comando para retomar.")
    return completo


def cmd_run(a):
    cfg = _config(a)
    man = Manifesto(cfg)
    print("workspace=%s | run_id=%s | source-mode=%s%s | base_dir=%s"
          % (man.d.get("workspace_id"), man.d["run_id"], cfg.source_mode,
             (" | refresh=%s" % ",".join(cfg.refresh_fontes)) if cfg.refresh_fontes else "",
             cfg.base_dir))

    ate = a.ate or "validate"
    if a.etapa:
        alvos = [a.etapa]
    else:
        alvos = list(ETAPAS[:ETAPAS.index(ate) + 1])
    if not cfg.gerar_mapa and "map" in alvos:
        alvos.remove("map")

    alvo_gdf = bbox = None
    df = None
    for etapa in alvos:
        if etapa != "init" and alvo_gdf is None:
            alvo_gdf, bbox, _ = ibge.alvo(cfg, man)
        try:
            if etapa == "init":
                alvo_gdf, bbox = ibge.executar(cfg, man)
            elif etapa == "fetch":
                if man.reutilizavel("fetch") and not a.force:
                    print("FETCH: reaproveitado")
                elif not _fetch(cfg, man, bbox):
                    return 2
            elif etapa == "raw":
                territorio.consolidar(cfg, man, bbox)
            elif etapa == "territory":
                if territorio.clipar(cfg, man, alvo_gdf) is None:
                    return 2
            elif etapa == "normalize":
                if normalizacao.normalizar(cfg, man) is None:
                    return 2
            elif etapa == "dedup":
                if normalizacao.deduplicar(cfg, man) is None:
                    return 2
            elif etapa == "export":
                df = exportacao.executar(cfg, man)
            elif etapa == "map":
                if df is None:
                    df = normalizacao.carregar_padronizado(cfg)
                mapa.executar(cfg, man, df)
            elif etapa == "validate":
                rel = validacao.executar(cfg, man, alvo_gdf)
                if rel["resultado"] == "REPROVADO":
                    man.finalizar()
                    return 1
        except EtapaBloqueada as e:
            print("BLOQUEADA: %s" % e)
            return 2
        except Exception as e:  # noqa: BLE001
            man.falhar(etapa, e)
            traceback.print_exc()
            print("ERRO na etapa '%s': %s" % (etapa, e))
            return 3
    man.finalizar()
    print("\nOK — manifesto: %s" % man.salvar())
    return 0


def cmd_status(a):
    cfg = _config(a)
    man = Manifesto(cfg)
    print("run_id=%s | status=%s" % (man.d["run_id"], man.d["status"]))
    for e in ETAPAS:
        d = man.d["etapas"][e]
        marca = "reaproveitavel" if man.reutilizavel(e) else (
            "OBSOLETA (parametros mudaram)" if man.obsoleta(e) else "-")
        print("  %-10s %-10s %-16s %s" % (e, d["status"], marca, d.get("contagens") or ""))
    for f in man.d["funil"]:
        print("  funil %-22s %8d -> %8d  (-%d) %s"
              % (f["etapa"], f["entrada"], f["saida"], f["descartados"], f["motivo"]))
    return 0


def cmd_diag(a):
    """Checagem de instalacao: dependencias, CLI externa, token e base de limites."""
    import importlib
    falhas = 0
    for m in ("pandas", "numpy", "geopandas", "shapely", "pyarrow", "duckdb", "rapidfuzz"):
        try:
            mod = importlib.import_module(m)
            print("  OK   %-12s %s" % (m, getattr(mod, "__version__", "?")))
        except ImportError:
            print("  ERRO %-12s ausente" % m)
            falhas += 1
    try:
        from poi_estadual.vendor import sha_vendor
        for k, v in sha_vendor().items():
            print("  OK   vendor/%-14s sha=%s" % (k, v))
    except Exception as e:  # noqa: BLE001
        print("  ERRO vendor: %s" % e)
        falhas += 1
    import shutil
    print("  %-4s overturemaps  %s" % ("OK" if shutil.which("overturemaps") else "AVISO",
                                       shutil.which("overturemaps") or "nao encontrado no PATH"))
    print("  %-4s HF_TOKEN      %s" % ("OK" if os.environ.get("HF_TOKEN") else "AVISO",
                                       "definido" if os.environ.get("HF_TOKEN") else "ausente (fsq indisponivel)"))
    print("DIAG: %d falha(s) bloqueante(s)" % falhas)
    return 1 if falhas else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="poi_estadual.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def comuns(p):
        p.add_argument("--uf", required=True, help="sigla da UF, ex.: RS")
        p.add_argument("--excluir", default="", help="COD IBGE separados por virgula")
        p.add_argument("--fontes", default="overture,osm,fsq")
        # v3.0.0 — default 0.0: `confianca` viaja no dado, filtrar e de quem consome
        p.add_argument("--min-conf", dest="min_conf", type=float, default=0.0)
        p.add_argument("--formatos", default="csv,geoparquet")
        p.add_argument("--gerar-mapa", dest="gerar_mapa", action="store_true")
        p.add_argument("--base-dir", dest="base_dir", default="./execucao")
        p.add_argument("--malha-qualidade", dest="malha_qualidade",
                       default="maxima", choices=["maxima", "intermediaria"])
        p.add_argument("--malha-parquet", dest="malha_parquet", default="")
        p.add_argument("--simplificar-graus", dest="simplificar_graus", type=float, default=0.0)
        p.add_argument("--source-mode", dest="source_mode", default="cache",
                       choices=["cache", "latest", "pinned"],
                       help="cache: nao consulta a fonte | latest: resolve a versao atual "
                            "e recoleta se mudou | pinned: exige versao resolvida")
        p.add_argument("--refresh-source", dest="refresh_source", default="",
                       help="reconsulta estas fontes (ex.: osm,fsq), independente do modo")
        p.add_argument("--osm-predicado", dest="osm_predicado", default="ampliado",
                       choices=["ampliado", "classico"],
                       help="ampliado inclui healthcare/craft/transporte/industria")
        p.add_argument("--osm-exigir-nome", dest="osm_exigir_nome", action="store_true",
                       help="volta a exigir `name` no OSM (v2); por padrao POI sem nome entra")
        p.add_argument("--dedup", default="evidencia",
                       choices=["evidencia", "legado", "exato", "none"])
        p.add_argument("--dedup-raio-m", dest="dedup_raio_m", type=int, default=30)
        p.add_argument("--dedup-sim-min", dest="dedup_sim_min", type=int, default=85)
        p.add_argument("--dedup-sim-cross", dest="dedup_sim_cross", type=int, default=92)
        p.add_argument("--dedup-jaccard", dest="dedup_jaccard", type=float, default=0.60)
        p.add_argument("--dedup-diametro", dest="dedup_diametro", type=float, default=90.0)
        p.add_argument("--dedup-ctx-raio", dest="dedup_ctx_raio", type=float, default=200.0)
        p.add_argument("--dedup-ctx-min", dest="dedup_ctx_min", type=int, default=3)
        p.add_argument("--dedup-semnome", dest="dedup_semnome", default="absorver",
                       choices=["absorver", "marcar"])
        p.add_argument("--dedup-celula", dest="dedup_celula", type=float, default=2000.0,
                       help="lado da celula de blocking em m; 0 = particionar por municipio")
        p.add_argument("--dedup-halo", dest="dedup_halo", type=float, default=0.0,
                       help="halo da celula em m; 0 = auto (>= raio de evidencia forte)")
        p.add_argument("--max-fusao-suspeita", dest="max_fusao_suspeita", type=float,
                       default=0.02, help="fracao de clusters com fusao suspeita que reprova")
        p.add_argument("--budget", type=float, default=0.0, help="time-box em s (0=sem)")
        p.add_argument("--ov-cap", dest="ov_cap", type=int, default=20000)
        p.add_argument("--ov-tile-graus", dest="ov_tile_graus", type=float, default=1.0)
        p.add_argument("--treat-batch", dest="treat_batch", type=int, default=40000)
        p.add_argument("--clip-chunk", dest="clip_chunk", type=int, default=120000)
        p.add_argument("--fsq-strips", dest="fsq_strips", type=int, default=7)
        p.add_argument("--duckdb-memory", dest="duckdb_memory", default="2.6GB")
        p.add_argument("--threads", type=int, default=8)
        return p

    r = comuns(sub.add_parser("run", help="executa o pipeline"))
    r.add_argument("--ate", choices=list(ETAPAS), help="para nesta etapa (inclusive)")
    r.add_argument("--etapa", choices=list(ETAPAS), help="roda SO esta etapa")
    r.add_argument("--force-stage", "--force", dest="force", action="store_true",
                   help="reconstroi o PROCESSAMENTO sobre o MESMO snapshot; "
                        "para trocar de snapshot use --source-mode latest / --refresh-source")
    r.set_defaults(fn=cmd_run)

    comuns(sub.add_parser("status", help="estado das etapas e funil")).set_defaults(fn=cmd_status)

    d = sub.add_parser("diag", help="checagem de instalacao")
    d.set_defaults(fn=cmd_diag)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except ConfigInvalida as e:
        print("CONFIG INVALIDA: %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
