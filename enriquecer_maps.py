"""
enriquecer_maps.py — Enriquecimento COMPLETO via Maps dos POIs que já existem lá.

Alvo: registros do banco com maps_url mas dados rasos — 'descoberto' (candidatos
vizinhos, só nome+coord) e 'recuperado_ia' (escolhidos pela OpenAI, só campos do
card). Eles EXISTEM no Google Maps, então: abre cada maps_url individualmente
(browser + proxy + fingerprint, mesma infra do pipeline), extrai o painel completo
e a coleta rica (telefone, site, horários, até MAX_FOTOS fotos, até MAX_REVIEWS
avaliações). Grátis — só banda de proxy.

Saída: JSON incremental no formato do ingester → o watcher do server ingere
(substitui por place_id preservando enriquecimentos) e atualiza o mapa ao vivo.

USO:
  .venv\\Scripts\\python enriquecer_maps.py --out mineracao/enrich_maps_db.json
     [--workers 6] [--no-proxy] [--limit N] [--area areas/area_atual.json]
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
_SEM_INGEST = False  # via server (--sem-ingest): o watcher grava; senão ingere direto
from proxy_pool import ProxyPool
from human_browser import HumanSession
import re

from search_pois_v2 import aguarda_painel_ou_lista, extract_panel, salvar_json, abrir_maps
from search_from_sheet import extrair_place_id
from extract_full import enriquecer_poi

# "Ctrl+F" no texto do painel — independe de seletor/data-item-id (que renderizam
# tarde ou mudam de classe). Cobre os formatos que o Maps BR usa:
#   (86) 99999-9999 · +55 86 99999-9999 · 86 99999-9999  (o hífen antes dos 4
# últimos dígitos é a âncora que evita casar CEP/outros números).
_RE_TEL_BR = re.compile(r"(?:\+55\s?)?(?:\(\d{2}\)|\d{2})\s?\d{4,5}-\d{4}")
_RE_SITE = re.compile(r"\b(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]+\.(?:com|com\.br|net|net\.br|org|org\.br|br)\b(?:/\S*)?", re.I)
_SITE_LIXO = ("google.", "gstatic", "schema.org", "example.", "w3.org")


async def _texto_painel(page) -> str:
    for sel in ('div[role="main"]', "div.m6QErb", "body"):
        try:
            t = await page.locator(sel).first.inner_text(timeout=2500)
            if t and len(t) > 40:
                return t
        except Exception:
            continue
    return ""


def _completa_por_texto(poi: dict, texto: str):
    """Preenche telefone/site varrendo o texto do painel (sem depender de IDs)."""
    if not poi.get("telefone"):
        m = _RE_TEL_BR.search(texto or "")
        if m:
            poi["telefone"] = re.sub(r"\s+", " ", m.group(0)).strip()
    if not poi.get("website"):
        for m in _RE_SITE.finditer(texto or ""):
            cand = m.group(0)
            if not any(x in cand.lower() for x in _SITE_LIXO) and "." in cand:
                poi["website"] = cand
                break


def carregar_alvos(limit: int = 0) -> list:
    """POIs do banco que existem no Maps mas estão rasos (sem telefone E sem fotos)."""
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.place_id, p.maps_url, p.nome, p.fonte, p.status, p.sessao,
                       p.nome_original, p.endereco_original, p.lat_origem, p.lng_origem
                FROM pois p
                WHERE p.maps_url ~ '/maps/place/'
                  -- todo POI que TEM place no Maps mas cujo endereço NÃO veio do Maps:
                  -- reabre o painel para trazer endereço/telefone/fotos oficiais.
                  AND (p.endereco_fonte IS DISTINCT FROM 'maps')
                ORDER BY p.id""")
            cols = ["db_id", "place_id", "maps_url", "nome", "fonte", "status", "sessao",
                    "nome_original", "endereco_original", "lat_origem", "lng_origem"]
            alvos = [dict(zip(cols, r)) for r in cur.fetchall()]
            return alvos[:limit] if limit > 0 else alvos
    finally:
        conn.close()


async def _extrair(sess, alvo: dict) -> dict | None:
    page = sess.page
    try:
        await page.goto(alvo["maps_url"], wait_until="domcontentloaded", timeout=35000)
        # Abrir por maps_url NÃO cai no heurístico painel/lista de forma confiável:
        # a URL /place/ traz o painel embutido, mas os botões de detalhe (endereço/
        # telefone) renderizam com atraso (às vezes >4s). Espera ATIVA pela presença
        # do painel de detalhes — senão extraía telefone vazio em ~todos os POIs.
        detalhe = 'button[data-item-id="address"], button[data-item-id^="phone"], ' \
                  'h1.DUwDvf, div.fontHeadlineLarge'
        try:
            await page.wait_for_selector(detalhe, state="attached", timeout=12000)
        except Exception:
            pass
        # o telefone costuma ser o último a aparecer — dá um respiro extra se ainda não veio
        if await page.locator('button[data-item-id^="phone"]').count() == 0:
            await page.wait_for_timeout(2500)
        poi = await extract_panel(page)
        if not poi.get("nome"):
            return None
        # "Ctrl+F": se o seletor não pegou telefone/site, varre o texto do painel.
        if not poi.get("telefone") or not poi.get("website"):
            _completa_por_texto(poi, await _texto_painel(page))
        poi = await enriquecer_poi(sess, poi)  # fotos + reviews + horários
        return poi
    except Exception:
        return None


def _monta_registro(alvo: dict, poi: dict) -> dict:
    return {
        "fonte": alvo["fonte"], "sessao": alvo["sessao"],
        "nome_planilha": alvo.get("nome_original"),
        "endereco_planilha": alvo.get("endereco_original"),
        "lat_origem": alvo.get("lat_origem"), "lng_origem": alvo.get("lng_origem"),
        "nome": poi.get("nome") or alvo["nome"],
        "categoria": poi.get("categoria", ""),
        "endereco": poi.get("endereco", ""),
        "telefone": poi.get("telefone", ""),
        "website": poi.get("website", ""),
        "avaliacao": poi.get("avaliacao", ""),
        "total_avaliacoes": poi.get("total_avaliacoes", 0),
        "plus_code": poi.get("plus_code", ""),
        "status_horario": poi.get("status_horario", ""),
        "maps_lat": poi.get("maps_lat") if poi.get("maps_lat") is not None else alvo.get("lat_origem"),
        "maps_lng": poi.get("maps_lng") if poi.get("maps_lng") is not None else alvo.get("lng_origem"),
        "maps_url": poi.get("maps_url") or alvo["maps_url"],
        "place_id": extrair_place_id(poi.get("maps_url", "")) or alvo["place_id"],
        "status": alvo["status"],          # mantém a classificação de origem
        "match_valido": True, "fonte_dado": "maps",
        "endereco_fonte": "maps" if poi.get("endereco") else None,
        "fotos": poi.get("fotos", []),
        "comentarios": poi.get("comentarios", []),
        "horarios": poi.get("horarios", {}),
    }


def _chunk(itens, n):
    return [itens[i:i + n] for i in range(0, len(itens), n)]


async def worker(wid, queue, state, pw, pool, out_json, lock, counter, total,
                 usar_proxy, poligono, headless=True):
    while True:
        try:
            bidx, lote = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        proxy = await pool.acquire_blocking() if usar_proxy else None
        if usar_proxy and not proxy:
            queue.put_nowait((bidx, lote))
            await asyncio.sleep(5)
            continue
        profile = config.BROWSER_PROFILES_DIR / f"enr_w{wid}_b{bidx}"
        sess = None
        try:
            tz_lng = next((a.get("lng_origem") for a in lote if a.get("lng_origem")), None)
            sess = await HumanSession.create(pw, proxy, profile, layer="maps",
                                             tz_hint_lng=tz_lng, headless=headless)
            if not await abrir_maps(sess):
                queue.put_nowait((bidx, lote))
                if proxy:
                    await pool.mark_cooldown(proxy, 600)
                continue
            for alvo in lote:
                if await sess.is_captcha():
                    print(f"\n  🚫 [W{wid}] CAPTCHA no lote {bidx}", flush=True)
                    break
                poi = await _extrair(sess, alvo)
                async with lock:
                    counter["n"] += 1
                    if poi:
                        reg = _monta_registro(alvo, poi)
                        if area_utils.gate_registro(reg, poligono):
                            counter["ok"] += 1
                            if not _SEM_INGEST:
                                try:
                                    realtime_ingest.ingerir_registro(reg, poligono)
                                except Exception as e:
                                    print(f"  ⚠️ ingest: {str(e)[:80]}", flush=True)
                        state["results"].append(reg)
                        salvar_json(out_json, state["results"])
                    nf = len(poi.get("fotos", [])) if poi else 0
                    print(f"🔗 [W{wid}] POIs {counter['n']}/{total} | enriquecidos "
                          f"{counter['ok']} | 📷{nf} | {alvo['nome'][:32]}", flush=True)
                await sess.humanized_wait()
        except Exception as e:
            print(f"\n  W{wid} erro lote {bidx}: {str(e)[:90]}", flush=True)
        finally:
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass
            shutil.rmtree(profile, ignore_errors=True)
            if proxy:
                await pool.release(proxy)


async def run(out_json: Path, n_workers: int, usar_proxy: bool, limit: int,
              area_path: str, headless: bool = True):
    poligono = area_utils.carregar_area(area_path) if area_path else None
    alvos = carregar_alvos(limit)
    total = len(alvos)
    print(f"🔗 Enriquecimento via Maps: {total} POIs rasos (descoberto/recuperado_ia) "
          f"| Proxy: {'sim' if usar_proxy else 'NÃO'} | Workers: {n_workers}", flush=True)
    if not total:
        print("Nada a enriquecer — todos os POIs com maps_url já têm dados.")
        return

    state = {"results": []}
    if out_json.exists():
        try:
            state["results"] = json.loads(out_json.read_text(encoding="utf-8"))
            feitos_pid = {r.get("place_id") for r in state["results"]}
            alvos = [a for a in alvos if a.get("place_id") not in feitos_pid]
            print(f"♻️  Retomando: {total - len(alvos)} já enriquecidos.", flush=True)
            total = len(alvos)
        except Exception:
            state["results"] = []

    lock = asyncio.Lock()
    counter = {"n": 0, "ok": 0}
    lotes = _chunk(alvos, 12)
    queue = asyncio.Queue()
    for i, b in enumerate(lotes):
        queue.put_nowait((i, b))

    pool = ProxyPool().start() if usar_proxy else None
    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    ini = time.time()
    n_ef = min(n_workers, config.MAX_WORKERS, max(1, len(lotes)))
    async with async_playwright() as pw:
        await asyncio.gather(*[
            worker(i, queue, state, pw, pool, out_json, lock, counter, total,
                   usar_proxy, poligono, headless)
            for i in range(n_ef)
        ])
    shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)

    print(f"\n{'═'*52}")
    print(f"🔗 Enriquecimento Maps | Resumo")
    print(f"{'═'*52}")
    print(f"   Enriquecidos : {counter['ok']}/{total}")
    print(f"   Tempo        : {(time.time()-ini)/60:.1f} min")
    print(f"   💾 {out_json}")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="mineracao/enrich_maps_db.json")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--area", default="")
    p.add_argument("--headful", action="store_true")
    p.add_argument("--sem-ingest", action="store_true",
                   help="Não grava direto (sob o server, o watcher ingere).")
    a = p.parse_args()
    global _SEM_INGEST
    _SEM_INGEST = a.sem_ingest
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(out, 1 if a.headful else a.workers, not a.no_proxy, a.limit,
                    a.area, headless=not a.headful))


if __name__ == "__main__":
    main()
