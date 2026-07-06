"""
enriquecer_tudo.py — Enriquecimento em CASCATA (não opcional): cada POI pobre passa
por TODOS os métodos até ficar completo.

Fluxo, sobre os POIs válidos do banco que estão pobres (falta telefone OU endereço
OU categoria OU não tem foto):

  FASE 1 — Maps        : quem tem maps_url é aberto individualmente (painel completo:
                         fotos, reviews, telefone, horário). Grátis (proxy).
  FASE 2 — Web         : quem CONTINUA pobre → Yahoo + páginas + pré-filtro local +
                         LLM barato (só estrutura) + CNPJ/QSA na Receita Federal.
  FASE 3 — Street View : TODO POI localizado sem print ganha a foto da fachada.

Cada registro enriquecido é salvo num JSON incremental → o watcher do server ingere
(substitui por place_id/origem preservando o que já havia) e atualiza o mapa ao vivo.
O estado de "pobre" é mantido em memória entre as fases (não depende do watcher).

USO:
  .venv\\Scripts\\python enriquecer_tudo.py --out mineracao/enrich_tudo_db.json
     [--workers 6] [--no-proxy] [--limit N] [--area areas/area_atual.json]
     [--pular-maps] [--pular-web] [--pular-streetview]
"""

import json
import time
import shutil
import asyncio
import argparse
from pathlib import Path

from playwright.async_api import async_playwright

import config
import area_utils
import realtime_ingest
from proxy_pool import ProxyPool
from human_browser import HumanSession
from search_pois_v2 import aguarda_painel_ou_lista, extract_panel, abrir_maps
from search_from_sheet import extrair_place_id
from extract_full import enriquecer_poi

import enriquecer_maps as EM
import minerar_web as MW
import streetview_capture as SV

BASE = Path(__file__).resolve().parent


# ──────────────────────────────────────────────────────────────────────────
# Carrega os POIs pobres do banco como registros (formato do ingester)
# ──────────────────────────────────────────────────────────────────────────
def _carente(reg: dict) -> bool:
    """Falta algo essencial → ainda vale a pena tentar mais um método."""
    return not reg.get("telefone") or not reg.get("endereco") or not reg.get("categoria")


_SEM_INGEST = False  # via server (--sem-ingest): o watcher ingere, evita dois
                     # processos fazendo delete+recreate do mesmo place_id.


def _ingerir(reg: dict, poligono):
    """Grava o registro DIRETO no Postgres (não depende do watcher do server).
    Assim o enriquecer_tudo funciona rodado no terminal, sem o web ligado.
    Com --sem-ingest (rodando sob o server) NÃO ingere: quem grava é o watcher."""
    if _SEM_INGEST:
        return
    try:
        realtime_ingest.ingerir_registro(reg, poligono)
    except Exception as e:
        print(f"  ⚠️ ingest: {str(e)[:90]}", flush=True)


def carregar_carentes(poligono, limit: int) -> list:
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.place_id, p.maps_url, p.fonte, p.sessao, p.status,
                       p.nome, p.categoria, p.endereco, p.telefone, p.website,
                       p.avaliacao, p.total_avaliacoes, p.status_horario,
                       p.nome_original, p.endereco_original,
                       COALESCE(p.maps_lat, p.lat_origem), COALESCE(p.maps_lng, p.lng_origem),
                       p.lat_origem, p.lng_origem, p.cnpj, p.instagram,
                       (SELECT COUNT(*) FROM images_urls i WHERE i.poi_id = p.id) AS nfotos
                FROM pois p
                WHERE p.match_valido IS NOT FALSE
                  AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL
                  AND (p.telefone IS NULL OR p.endereco IS NULL OR p.categoria IS NULL
                       -- rasos que têm maps_url e podem ganhar fotos/painel:
                       OR (p.status IN ('descoberto','recuperado_ia','minerado','recuperado_gemini','recuperado_web')
                           AND NOT EXISTS (SELECT 1 FROM images_urls i WHERE i.poi_id = p.id)))
                ORDER BY p.id""")
            cols = ["db_id", "place_id", "maps_url", "fonte", "sessao", "status",
                    "nome", "categoria", "endereco", "telefone", "website",
                    "avaliacao", "total_avaliacoes", "status_horario",
                    "nome_original", "endereco_original", "lat", "lng",
                    "lat_origem", "lng_origem", "cnpj", "instagram", "nfotos"]
            regs = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                regs.append({
                    # identidade / dedup
                    "place_id": d["place_id"], "fonte": d["fonte"], "sessao": d["sessao"],
                    "status": d["status"], "match_valido": True,
                    "nome": d["nome"], "nome_planilha": d["nome_original"],
                    "endereco_planilha": d["endereco_original"],
                    "maps_url": d["maps_url"],
                    "lat_origem": d["lat_origem"], "lng_origem": d["lng_origem"],
                    "maps_lat": d["lat"], "maps_lng": d["lng"],
                    # dados atuais (para saber o que falta)
                    "categoria": d["categoria"], "endereco": d["endereco"],
                    "telefone": d["telefone"], "website": d["website"],
                    "avaliacao": d["avaliacao"], "total_avaliacoes": d["total_avaliacoes"],
                    "status_horario": d["status_horario"], "cnpj": d["cnpj"],
                    "instagram": d["instagram"],
                    "fotos": [], "comentarios": [], "horarios": {},
                    "_nfotos": d["nfotos"], "_db_id": d["db_id"],
                })
            return regs[:limit] if limit > 0 else regs
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# FASE 1 — Maps
# ──────────────────────────────────────────────────────────────────────────
async def fase_maps(regs, pool, usar_proxy, poligono, salvar, prog):
    alvos = [r for r in regs if r.get("maps_url")]
    total = len(alvos)
    prog["fase"] = "Maps"
    print(f"🔗 FASE 1/3 — Maps: {total} POIs com maps_url", flush=True)
    if not total:
        return
    lotes = [alvos[i:i + 12] for i in range(0, len(alvos), 12)]
    queue = asyncio.Queue()
    for i, b in enumerate(lotes):
        queue.put_nowait((i, b))
    counter = {"n": 0, "ok": 0}
    lock = asyncio.Lock()

    async def _worker(wid):
        while True:
            try:
                bidx, lote = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            proxy = await pool.acquire_blocking() if usar_proxy else None
            profile = config.BROWSER_PROFILES_DIR / f"et_maps_w{wid}_b{bidx}"
            sess = None
            try:
                tz = next((a.get("lng_origem") for a in lote if a.get("lng_origem")), None)
                sess = await HumanSession.create(_PW, proxy, profile, layer="maps",
                                                 tz_hint_lng=tz, headless=True)
                if not await abrir_maps(sess):
                    queue.put_nowait((bidx, lote))
                    if proxy:
                        await pool.mark_cooldown(proxy, 600)
                    continue
                for reg in lote:
                    if await sess.is_captcha():
                        break
                    poi = await EM._extrair(sess, reg)
                    async with lock:
                        counter["n"] += 1
                        if poi and poi.get("nome"):
                            _aplica_maps(reg, poi)
                            reg["_tocado"] = True   # Maps trouxe painel real
                            if area_utils.gate_registro(reg, poligono):
                                counter["ok"] += 1
                                _ingerir(reg, poligono)   # grava DIRETO no banco
                            salvar()
                        prog["feitos"] += 1
                        print(f"🔗 [Maps W{wid}] POIs {counter['n']}/{total} | "
                              f"enriquecidos {counter['ok']} | {reg['nome'][:30]}", flush=True)
                    await sess.humanized_wait()
            except Exception as e:
                print(f"  Maps W{wid} erro: {str(e)[:80]}", flush=True)
            finally:
                if sess:
                    try:
                        await sess.close()
                    except Exception:
                        pass
                shutil.rmtree(profile, ignore_errors=True)
                if proxy:
                    await pool.release(proxy)

    n = min(6, config.MAX_WORKERS, len(lotes))
    await asyncio.gather(*[_worker(i) for i in range(n)])


def _aplica_maps(reg: dict, poi: dict):
    for k_reg, k_poi in [("nome", "nome"), ("categoria", "categoria"), ("endereco", "endereco"),
                         ("telefone", "telefone"), ("website", "website"),
                         ("avaliacao", "avaliacao"), ("status_horario", "status_horario")]:
        v = poi.get(k_poi)
        if v not in (None, "", []):
            reg[k_reg] = v
    if poi.get("total_avaliacoes"):
        reg["total_avaliacoes"] = poi["total_avaliacoes"]
    if poi.get("endereco"):
        reg["endereco_fonte"] = "maps"
    if poi.get("maps_lat") is not None:
        reg["maps_lat"], reg["maps_lng"] = poi["maps_lat"], poi["maps_lng"]
    reg["maps_url"] = poi.get("maps_url") or reg.get("maps_url")
    pid = extrair_place_id(poi.get("maps_url", ""))
    if pid:
        reg["place_id"] = pid
    reg["fotos"] = poi.get("fotos", []) or reg.get("fotos", [])
    reg["comentarios"] = poi.get("comentarios", []) or reg.get("comentarios", [])
    reg["horarios"] = poi.get("horarios", {}) or reg.get("horarios", {})
    reg["fonte_dado"] = "maps"


# ──────────────────────────────────────────────────────────────────────────
# FASE 2 — Web (Yahoo + Receita) sobre quem continua pobre
# ──────────────────────────────────────────────────────────────────────────
async def fase_web(regs, uf, cidade_arg, usar_proxy, workers, poligono, salvar, prog):
    import aiohttp
    ainda = [r for r in regs if _carente(r)]
    total = len(ainda)
    prog["fase"] = "Web"
    print(f"🌐 FASE 2/3 — Web: {total} POIs ainda pobres", flush=True)
    if not total:
        return
    serp = await MW.SerpPool(min(workers, 6), usar_proxy=usar_proxy).start()
    sem = asyncio.Semaphore(workers)
    counter = {"n": 0, "ok": 0}
    lock = asyncio.Lock()
    conn = aiohttp.TCPConnector(limit=workers * 4, ssl=False)
    try:
        async with aiohttp.ClientSession(connector=conn) as session:
            async def _um(reg):
                cidade = MW._cidade_do(reg, cidade_arg)
                antes = (reg.get("telefone"), reg.get("endereco"), reg.get("cnpj"))
                try:
                    await MW._processar_poi(session, serp, reg, cidade, uf, sem)
                except Exception:
                    pass
                if poligono:
                    area_utils.gate_registro(reg, poligono)
                mudou = (reg.get("telefone"), reg.get("endereco"), reg.get("cnpj")) != antes
                async with lock:
                    counter["n"] += 1
                    if mudou and reg.get("match_valido"):
                        reg["_tocado"] = True   # a web acrescentou algo real
                        counter["ok"] += 1
                        _ingerir(reg, poligono)   # grava DIRETO no banco
                        salvar()
                    prog["feitos"] += 1
                    print(f"🌐 [Web] POIs {counter['n']}/{total} | enriquecidos {counter['ok']} | "
                          f"tokens {MW._USO['in']+MW._USO['out']} (US${MW._custo():.3f}) | "
                          f"{(reg.get('nome') or '')[:28]}", flush=True)
            await asyncio.gather(*[_um(r) for r in ainda])
    finally:
        await serp.close()


# ──────────────────────────────────────────────────────────────────────────
# FASE 3 — Street View para todos os localizados sem print
# ──────────────────────────────────────────────────────────────────────────
async def fase_streetview(workers, prog):
    prog["fase"] = "Street View"
    print("📸 FASE 3/3 — Street View", flush=True)
    # roda o capturador completo (grava streetview_path direto no banco)
    await SV.run(workers=workers, limit=0, refazer=False)


# ──────────────────────────────────────────────────────────────────────────
# Orquestração
# ──────────────────────────────────────────────────────────────────────────
_PW = None


async def run(out_json: Path, workers: int, usar_proxy: bool, limit: int, area_path: str,
              pular_maps: bool, pular_web: bool, pular_sv: bool):
    global _PW
    poligono = area_utils.carregar_area(area_path) if area_path else None
    regs = carregar_carentes(poligono, limit)
    total = len(regs)
    # total = fase1(maps c/ url) + fase2(estimado) — a barra usa o total de POIs a tocar
    uf = ""
    for r in regs:
        end = (r.get("endereco") or r.get("endereco_planilha") or "")
        import re
        m = re.search(r"\b([A-Z]{2})\b(?:,|\s|$)", end)
        if m:
            uf = m.group(1)
            break
    uf = uf or "PI"
    print(f"💎 Enriquecimento em cascata: {total} POIs pobres | UF {uf} | "
          f"Proxy: {'sim' if usar_proxy else 'NÃO'} | Workers: {workers}", flush=True)
    print(f"POIs : {total} | Pendentes: {total}", flush=True)  # p/ o server captar o total
    if not total:
        print("✅ Nenhum POI pobre — banco já está completo.")
        return

    lock_io = asyncio.Lock()
    prog = {"fase": "-", "feitos": 0}

    def salvar():
        tmp = out_json.with_suffix(".tmp")
        # SÓ os que foram GENUINAMENTE enriquecidos AGORA (flag _tocado). Sem isso,
        # re-ingeriríamos registros que só "tinham telefone de antes" — e a re-ingestão
        # apagaria as fotos deles (a fase Maps que falhou volta com fotos=[]).
        payload = [r for r in regs if r.get("_tocado")]
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out_json)

    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    ini = time.time()
    async with async_playwright() as pw:
        _PW = pw
        pool = ProxyPool().start() if usar_proxy else None
        if not pular_maps:
            await fase_maps(regs, pool, usar_proxy, poligono, salvar, prog)
        if not pular_web:
            await fase_web(regs, uf, "", usar_proxy, workers, poligono, salvar, prog)
        salvar()
    shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)

    if not pular_sv:
        await fase_streetview(min(workers, 4), prog)

    completos = sum(1 for r in regs if not _carente(r))
    print(f"\n{'═'*52}")
    print(f"💎 Enriquecimento em cascata | Resumo")
    print(f"{'═'*52}")
    print(f"   POIs pobres tratados : {total}")
    print(f"   Ficaram completos    : {completos}")
    print(f"   Ainda incompletos    : {total - completos}")
    print(f"   Custo web (LLM)      : US$ {MW._custo():.4f}")
    print(f"   Tempo                : {(time.time()-ini)/60:.1f} min")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="mineracao/enrich_tudo_db.json")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--area", default="")
    p.add_argument("--pular-maps", action="store_true")
    p.add_argument("--pular-web", action="store_true")
    p.add_argument("--pular-streetview", action="store_true")
    p.add_argument("--sem-ingest", action="store_true",
                   help="Não ingere direto (rodando sob o server web, o watcher grava).")
    a = p.parse_args()
    global _SEM_INGEST
    _SEM_INGEST = a.sem_ingest
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(out, a.workers, not a.no_proxy, a.limit, a.area,
                    a.pular_maps, a.pular_web, a.pular_streetview))


if __name__ == "__main__":
    main()
