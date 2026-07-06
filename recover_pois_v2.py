"""
recover_pois_v2.py — Recuperação otimizada (scraping puro, sem Places API)

Roda sobre as FALHAS do search_pois_v2 (status != ok e != ocr_curto) e tenta
recuperá-las com a MESMA arquitetura otimizada (lotes + IP estático + fingerprint
+ humanização + block agressivo), porém com matching RELAXADO:
  - distância aceitável maior (RECOVER_MAX_DIST_M)
  - similaridade de card menor (RECOVER_SIM_MIN)

Diferença vs recover_pois.py (v1): o v1 usa Google Places API (paga). Este v2 é
scraping puro do Maps — sem custo de API, respeitando o spec.

Saída: recover_resultado.json (schema compatível com enrich_pois.py).

USO:
  py recover_pois_v2.py capturas/<sessao>/session.json [--workers 10]
"""

import json
import time
import asyncio
import argparse
import shutil
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

import config
from proxy_pool import ProxyPool
from human_browser import HumanSession
from spatial_clustering import clusterizar_pois, resumo_clusters

# Reusa os helpers de interação com o Maps do search v2 (sem efeitos colaterais no import)
from search_pois_v2 import (
    haversine, similaridade, salvar_json,
    abrir_coordenada_para_n2, nivel2, abrir_maps,
)

# Thresholds relaxados para recuperação
RECOVER_MAX_DIST_M = 250
RECOVER_SIM_MIN = 0.55


async def recover_one(sess, reg, wid, full=False) -> dict:
    """Re-tenta um POI que falhou, com critério relaxado."""
    page = sess.page
    nome = reg.get("ocr_texto", "").strip()
    orig_lat = reg["lat"]
    orig_lng = reg["lng"]

    # Preserva campos originais; reseta status de recuperação
    result = {
        **reg,
        "status": "nao_encontrado_no_raio",
        "match_valido": False,
        "poi": reg.get("poi", {}) or {},
    }

    try:
        motivo = await abrir_coordenada_para_n2(page, orig_lat, orig_lng)
        if motivo != "ok":
            result.setdefault("erros", []).append(f"REC_PREP: {motivo}")
            return result

        poi2, motivo2 = await nivel2(sess, orig_lat, orig_lng, nome, wid)
        if not poi2 or not poi2.get("nome"):
            result.setdefault("erros", []).append(f"REC_N2: {motivo2}")
            return result

        nome_poi = poi2.get("nome", "")
        sim = similaridade(nome, nome_poi) if nome else 0
        dist = None
        if poi2.get("maps_lat") and poi2.get("maps_lng"):
            dist = haversine(orig_lat, orig_lng, poi2["maps_lat"], poi2["maps_lng"])

        # Critério relaxado: perto OU nome razoavelmente parecido
        match_dist = dist is not None and dist <= RECOVER_MAX_DIST_M
        match_nome = sim >= RECOVER_SIM_MIN
        if match_dist or match_nome:
            if full:
                try:
                    from extract_full import enriquecer_poi
                    poi2 = await enriquecer_poi(sess, poi2)
                except Exception:
                    pass
            result["poi"] = poi2
            result["match_valido"] = True
            result["status"] = "recuperado_v2"
            result["distancia_m"] = round(dist, 1) if dist is not None else None
            result["similaridade"] = round(sim, 3)
    except Exception as e:
        result.setdefault("erros", []).append(f"REC_EXC: {e}")

    return result


async def worker(wid, queue, state, pw, pool, counter, total, out_json, lock, metrics, full=False):
    while True:
        try:
            batch_idx, batch = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        proxy = await pool.acquire_blocking()
        if not proxy:
            queue.put_nowait((batch_idx, batch))
            await asyncio.sleep(5)
            continue

        ip_label = f"{proxy['address']}:{proxy['port']}"
        profile_dir = config.BROWSER_PROFILES_DIR / f"rec_w{wid}_b{batch_idx}"
        sess = None
        captcha_no_lote = False
        idx_parou = 0

        try:
            sess = await HumanSession.create(pw, proxy, profile_dir, layer="maps",
                                             tz_hint_lng=batch[0].get("lng"))
            if not await abrir_maps(sess):
                queue.put_nowait((batch_idx, batch))
                await pool.mark_cooldown(proxy, segundos=600)
                continue

            for j, reg in enumerate(batch):
                idx_parou = j
                if await sess.is_captcha():
                    captcha_no_lote = True
                    print(f"\n  🚫 [W{wid}] CAPTCHA no lote rec {batch_idx} IP={ip_label}")
                    break

                res = await recover_one(sess, reg, wid, full=full)

                async with lock:
                    # Atualiza item existente (por idx) ou adiciona
                    by_idx = {r.get("idx"): i for i, r in enumerate(state["results"])}
                    if res.get("idx") in by_idx:
                        state["results"][by_idx[res["idx"]]] = res
                    else:
                        state["results"].append(res)
                    state["results"].sort(key=lambda x: x.get("idx", 0))
                    salvar_json(out_json, state["results"])

                counter["done"] += 1
                metrics["pois"] += 1
                ok_mark = "✅" if res["match_valido"] else "❌"
                nome = reg.get("ocr_texto", "")[:30]
                print(f"\r♻️  [W{wid}] lote {batch_idx+1}/{metrics['n_lotes']} "
                      f"IP={ip_label} | {ok_mark} {counter['done']}/{total} "
                      f"{res['status']:22} {nome}", flush=True)

                if j < len(batch) - 1:
                    await sess.humanized_wait()
        except Exception as e:
            print(f"\n  W{wid} erro no lote rec {batch_idx}: {str(e)[:100]}")
        finally:
            if sess:
                try:
                    await sess.close()
                    metrics["bytes"] += sess.bytes_used
                except Exception:
                    pass
            shutil.rmtree(profile_dir, ignore_errors=True)

            if captcha_no_lote:
                metrics["captcha_lotes"] += 1
                await pool.mark_cooldown(proxy)
                restantes = batch[idx_parou:]
                if restantes:
                    novo_idx = metrics["n_lotes"]
                    metrics["n_lotes"] += 1
                    queue.put_nowait((novo_idx, restantes))
            else:
                await pool.release(proxy)


async def run(session_path: Path, n_workers: int, full=False, ingest=False):
    search_json = session_path.parent / "crops" / "search_resultado.json"
    if not search_json.exists():
        print(f"Erro: {search_json} não encontrado. Rode search_pois_v2.py primeiro.")
        return

    data_orig = json.loads(search_json.read_text(encoding="utf-8"))
    out_json = session_path.parent / "crops" / "recover_resultado.json"

    # Falhas recuperáveis: não-ok, mas com coordenada e OCR útil
    falhas = [
        r for r in data_orig
        if not r.get("match_valido")
        and r.get("status") != "ocr_curto"
        and r.get("lat") is not None
        and len(r.get("ocr_texto", "").strip()) >= config.MIN_OCR_LEN
    ]
    validos = [r for r in data_orig if r.get("match_valido")]
    resto = [r for r in data_orig
             if not r.get("match_valido")
             and r not in falhas]

    # Retomada: se já existe recover_resultado, parte dele
    if out_json.exists():
        try:
            ja = json.loads(out_json.read_text(encoding="utf-8"))
            ja_recuperados = {r["idx"] for r in ja if r.get("status") == "recuperado_v2"}
            falhas = [r for r in falhas if r.get("idx") not in ja_recuperados]
            print(f"\n♻️  Retomando recover: {len(ja_recuperados)} já recuperados.")
        except Exception:
            pass

    lotes = clusterizar_pois(falhas)

    print(f"\n🔧 ComercialRadar — Recover POIs v2 (scraping puro)")
    print(f"   Sessão    : {session_path.parent.name}")
    print(f"   Válidos   : {len(validos)} | Falhas p/ recuperar: {len(falhas)}")
    print(f"   Clusters  : {resumo_clusters(lotes)}")
    print(f"   Workers   : {n_workers}\n")

    # Estado inicial = todos os itens originais (validos + resto + falhas serão atualizadas)
    state = {"results": list(data_orig)}
    lock = asyncio.Lock()
    counter = {"done": 0}
    total = len(falhas)
    salvar_json(out_json, state["results"])

    if not falhas:
        print("✅ Nada a recuperar.")
        return

    pool = ProxyPool().start()
    queue = asyncio.Queue()
    for i, batch in enumerate(lotes):
        queue.put_nowait((i, batch))

    metrics = {"bytes": 0, "pois": 0, "captcha_lotes": 0,
               "n_lotes": len(lotes), "inicio": time.time()}
    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    n_efetivo = min(n_workers, config.MAX_WORKERS, len(lotes))
    async with async_playwright() as pw:
        await asyncio.gather(*[
            worker(i, queue, state, pw, pool, counter, total, out_json, lock, metrics, full)
            for i in range(n_efetivo)
        ])

    shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)

    dur = time.time() - metrics["inicio"]
    recuperados = sum(1 for r in state["results"] if r.get("status") == "recuperado_v2")
    mb = metrics["bytes"] / (1024 * 1024)
    mb_poi = (mb / metrics["pois"]) if metrics["pois"] else 0

    print(f"\n\n{'═' * 56}")
    print(f"🔧 Recover v2 | Resumo")
    print(f"{'═' * 56}")
    print(f"   ♻️  Recuperados         : {recuperados}/{total}")
    print(f"   📡 Banda total         : {mb:.1f} MB ({mb_poi:.2f} MB/POI)")
    print(f"   🔥 IPs queimados       : {pool.burned_count}")
    print(f"   🚫 Lotes com CAPTCHA   : {metrics['captcha_lotes']}")
    print(f"   ⏱  Tempo total         : {dur/60:.1f} min")
    print(f"   💾 {out_json}")
    print(f"{'═' * 56}")

    if ingest:
        try:
            import db_export
            out = db_export.exportar(session_path, source="recover")
            if out:
                db_export.ingerir(out)
        except Exception as e:
            print(f"⚠️  Ingestão falhou: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", help="Caminho para session.json")
    parser.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    parser.add_argument("--full", action="store_true",
                        help="Extração completa (fotos + avaliações) nos recuperados")
    parser.add_argument("--ingest", action="store_true",
                        help="Após recuperar, grava no PostgreSQL via Prisma")
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        return

    asyncio.run(run(session_path, args.workers, full=args.full, ingest=args.ingest))


if __name__ == "__main__":
    main()
