"""
search_pois_v2.py — Coleta rica otimizada (Camada 2) do ComercialRadar

Refatoração do search_pois.py com:
  - Regionalização espacial (DBSCAN) → lotes geograficamente coesos de 8-15 POIs
  - 10 workers persistentes; cada lote = 1 browser + 1 IP estático + 1 fingerprint
  - Humanização (cadência, pausas longas, exploração do painel, stealth)
  - Block agressivo de recursos (CSS/font/media/telemetria/avatars) → ~70% menos banda
  - Proxy nativo por contexto, rotação por lote, cooldown em CAPTCHA
  - Galeria: extrai só URLs das fotos (download é etapa separada, sem proxy)

Schema de saída IDÊNTICO ao search_pois.py v1 → compatível com recover_pois.py.

USO:
  py search_pois_v2.py capturas/<sessao>/session.json [--workers 10] [--max-dist 100]
"""

import json
import math
import re
import time
import asyncio
import argparse
import shutil
import random
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

import config
from proxy_pool import ProxyPool
from human_browser import HumanSession
from spatial_clustering import clusterizar_pois, resumo_clusters


# ══════════════════════════════════════════════════════════════════════════
# Helpers puros (mesma lógica do v1 — mantém compatibilidade de matching)
# ══════════════════════════════════════════════════════════════════════════
def haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def similaridade(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def salvar_json(out_json: Path, results: list):
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def log_erro(wid, nivel, etapa, erro, nome):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n  [{ts}] W{wid} N{nivel} ❌ {etapa}: {erro[:120]} | POI: {nome[:40]}")


def coords_from_url(url: str):
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


async def extract_panel(page) -> dict:
    data = {}
    try:
        data["nome"] = await page.locator("h1.DUwDvf").first.inner_text(timeout=5000)
    except Exception:
        data["nome"] = ""
    try:
        data["categoria"] = await page.locator("button.DkEaL").first.inner_text(timeout=3000)
    except Exception:
        data["categoria"] = ""
    try:
        data["avaliacao"] = await page.locator('div.F7nice span[aria-hidden="true"]').first.inner_text(timeout=3000)
    except Exception:
        data["avaliacao"] = ""
    try:
        txt = await page.locator('div.F7nice span[role="img"]').first.get_attribute("aria-label", timeout=3000)
        m = re.search(r"(\d+)", txt or "")
        data["total_avaliacoes"] = int(m.group(1)) if m else 0
    except Exception:
        data["total_avaliacoes"] = 0
    try:
        data["endereco"] = await page.locator('button[data-item-id="address"] div.Io6YTe').inner_text(timeout=3000)
    except Exception:
        data["endereco"] = ""
    try:
        data["telefone"] = await page.locator('button[data-item-id^="phone"] div.Io6YTe').inner_text(timeout=3000)
    except Exception:
        data["telefone"] = ""
    try:
        data["website"] = await page.locator('a[data-item-id="authority"] div.Io6YTe').inner_text(timeout=3000)
    except Exception:
        data["website"] = ""
    try:
        data["status_horario"] = await page.locator("span.ZDu9vd span").first.inner_text(timeout=3000)
    except Exception:
        data["status_horario"] = ""
    try:
        toggle = page.locator("div.OMl5r.hH0dDd")
        if await toggle.count() > 0:
            expanded = await toggle.get_attribute("aria-expanded")
            if expanded != "true":
                await toggle.click()
                await page.wait_for_timeout(600)
        rows = page.locator("table.eK4R0e tr.y0skZc")
        count = await rows.count()
        horarios = {}
        for i in range(count):
            row = rows.nth(i)
            dia = await row.locator("td.ylH6lf").inner_text(timeout=2000)
            hora = await row.locator("td.mxowUb").get_attribute("aria-label", timeout=2000)
            horarios[dia.strip()] = hora.strip() if hora else ""
        data["horarios"] = horarios
    except Exception:
        data["horarios"] = {}
    try:
        data["plus_code"] = await page.locator('button[data-item-id="oloc"] div.Io6YTe').inner_text(timeout=3000)
    except Exception:
        data["plus_code"] = ""
    try:
        review_el = page.locator("div.jftiEf, div.wiI7pd").first
        data["ultima_avaliacao"] = (await review_el.inner_text(timeout=3000)).strip()[:300]
    except Exception:
        data["ultima_avaliacao"] = ""
    # Galeria: SÓ extrai URLs (download é etapa separada, sem proxy)
    try:
        fotos = []
        imgs = page.locator("div.RZ66Rb img, button.K4UgGe img, img.DaSXdd")
        count = await imgs.count()
        for i in range(min(count, 10)):
            src = await imgs.nth(i).get_attribute("src", timeout=1000)
            if src and "googleusercontent" in src and src not in fotos:
                fotos.append(src)
        data["fotos"] = fotos
    except Exception:
        data["fotos"] = []

    url = page.url
    lat, lng = coords_from_url(url)
    data["maps_lat"] = lat
    data["maps_lng"] = lng
    data["maps_url"] = url
    return data


async def aguarda_painel_ou_lista(page) -> str:
    # Espera o Google trazer o resultado (lento != inexistente): aguarda painel
    # OU lista aparecerem, o que vier primeiro, com timeout generoso.
    try:
        await page.locator('h1.DUwDvf, div[role="feed"]').first.wait_for(
            state="visible", timeout=config.WAIT_PAINEL_MS)
    except Exception:
        return "nada"
    # Já apareceu algo — distingue painel de lista
    try:
        if await page.locator("h1.DUwDvf").first.is_visible(timeout=500):
            return "painel"
    except Exception:
        pass
    try:
        if await page.locator('div[role="feed"]').is_visible(timeout=500):
            return "lista"
    except Exception:
        pass
    return "nada"


async def selecionar_melhor_card(sess, nome_ocr, orig_lat, orig_lng):
    page = sess.page
    SELETORES_CARD = ['div[role="feed"] div[role="article"]', "div.Nv2PK"]
    cards = None
    for sel in SELETORES_CARD:
        try:
            els = page.locator(sel)
            if await els.count() > 0:
                cards = els
                break
        except Exception:
            continue
    if not cards:
        return None

    count = await cards.count()
    melhor_card = None
    melhor_score = -1.0
    melhor_coord = None      # coord do !3d!4d do href do card (ponto REAL do place)

    for i in range(min(count, 8)):
        card = cards.nth(i)
        try:
            nome_card = ""
            for ns in ["div.qBF1Pd", "span.fontHeadlineSmall", "div.fontHeadlineSmall"]:
                try:
                    nome_card = await card.locator(ns).first.inner_text(timeout=800)
                    if nome_card:
                        break
                except Exception:
                    continue
            if not nome_card:
                continue

            sim = similaridade(nome_ocr, nome_card)
            dist_card = 9999.0
            coord_card = None
            try:
                href = await card.locator('a[href*="/maps/place/"]').first.get_attribute("href", timeout=800)
                m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", href or "")
                if m:
                    coord_card = (float(m.group(1)), float(m.group(2)))
                    dist_card = haversine(orig_lat, orig_lng, *coord_card)
            except Exception:
                pass

            prox_score = max(0, 1 - dist_card / 2000)
            score = sim * 0.7 + prox_score * 0.3

            if score > melhor_score and sim >= config.SIM_CARD_MIN:
                melhor_score = score
                melhor_card = card
                melhor_coord = coord_card
        except Exception:
            continue

    if melhor_card is None:
        return None

    try:
        link = melhor_card.locator('a[href*="/maps/place/"]').first
        await sess.humanized_click(link, timeout=3000)
    except Exception:
        await melhor_card.click()

    try:
        await page.locator("h1.DUwDvf").first.wait_for(state="visible", timeout=config.WAIT_PAINEL_MS)
        await sess.explore_panel()
        poi = await extract_panel(page)
        # a coord da URL pode ser o CENTRO da lista, não o place; prioriza a do card.
        if poi and melhor_coord:
            poi["maps_lat"], poi["maps_lng"] = melhor_coord
        return poi
    except Exception:
        return None


async def _preencher_busca(page, texto: str):
    """Escreve na barra de busca via fill() — ignora o canvas do mapa que
    intercepta cliques. Mais robusto que click()+keyboard.type."""
    box = page.locator('input[name="q"], input[role="combobox"]').first
    await box.wait_for(state="visible", timeout=8000)
    await box.fill("")
    await box.fill(texto)
    await page.wait_for_timeout(200)


async def abrir_coordenada_para_n2(page, lat, lng) -> str:
    try:
        await _preencher_busca(page, f"{lat},{lng}")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2500)
        return "ok"
    except Exception as e:
        return f"abrir coordenada falhou: {e}"


async def nivel2(sess, lat, lng, nome_ocr, wid):
    page = sess.page
    try:
        # Botão "Próximo" (= "pesquisar nas proximidades"): localiza pelo NOME
        # ACESSÍVEL, independente de tag/atributo — equivale a um Ctrl+F. O
        # elemento real é um [role=button] aria-label="Próximo", não um <button>.
        proximo = page.get_by_role("button", name="Próximo", exact=True)
        try:
            await proximo.first.wait_for(state="visible", timeout=config.WAIT_PROXIMO_MS)
        except Exception:
            proximo = page.locator('[aria-label="Próximo"]')
            try:
                await proximo.first.wait_for(state="visible", timeout=2500)
            except Exception:
                return None, "botão Próximo não encontrado"

        await proximo.first.click()
        await page.wait_for_timeout(800)

        # Digita o nome via fill() (robusto contra o canvas)
        await _preencher_busca(page, nome_ocr)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(config.WAIT_APOS_BUSCA_MS)

        resultado = await aguarda_painel_ou_lista(page)

        if resultado == "painel":
            await sess.explore_panel()
            poi = await extract_panel(page)
            return poi, "ok"
        elif resultado == "lista":
            poi = await selecionar_melhor_card(sess, nome_ocr, lat, lng)
            if poi:
                return poi, "ok"
            return None, "nenhum card com similaridade suficiente"
        else:
            return None, "nem painel nem lista apareceram"
    except Exception as e:
        return None, f"nivel2 falhou: {e}"


async def search_one(sess, reg, max_dist, wid, full=False) -> dict:
    page = sess.page
    nome = reg.get("ocr_texto", "").strip()
    orig_lat = reg["lat"]
    orig_lng = reg["lng"]

    result = {
        **reg,
        "nivel": None,
        "status": "nao_encontrado",
        "distancia_m": None,
        "match_valido": False,
        "similaridade": 0,
        "erros": [],
        "poi": {},
    }

    def montar_result(poi, nivel):
        dist = None
        nome_poi = (poi or {}).get("nome", "")
        sim = similaridade(nome, nome_poi) if nome and nome_poi else 0
        if poi and poi.get("maps_lat") and poi.get("maps_lng"):
            dist = haversine(orig_lat, orig_lng, poi["maps_lat"], poi["maps_lng"])
        result["poi"] = poi or {}
        result["nivel"] = nivel
        result["distancia_m"] = round(dist, 1) if dist is not None else None
        result["similaridade"] = round(sim, 3)
        match_dist = dist is not None and dist <= max_dist
        match_nome = dist is not None and dist <= 2000 and sim >= config.SIMILARIDADE_FORTE
        result["match_valido"] = match_dist or match_nome
        result["status"] = "ok" if result["match_valido"] else "distancia_alta"

    try:
        motivo_coord = await abrir_coordenada_para_n2(page, orig_lat, orig_lng)
        if motivo_coord != "ok":
            result["erros"].append(f"PREP_N2: {motivo_coord}")
            log_erro(wid, 2, "prep_n2", motivo_coord, nome)
            return result

        poi2, motivo2 = await nivel2(sess, orig_lat, orig_lng, nome, wid)
        if motivo2 != "ok":
            result["erros"].append(f"N2: {motivo2}")
            log_erro(wid, 2, "nivel2", motivo2, nome)

        if poi2 and poi2.get("nome"):
            montar_result(poi2, 2)
            # Extração completa (todas as fotos + avaliações) só nos matches válidos
            if full and result["match_valido"]:
                try:
                    from extract_full import enriquecer_poi
                    result["poi"] = await enriquecer_poi(sess, result["poi"])
                except Exception:
                    pass
    except Exception as e:
        result["status"] = "erro"
        result["erros"].append(f"exceção: {e}")
        log_erro(wid, 0, "search_one", str(e), nome)

    return result


# ══════════════════════════════════════════════════════════════════════════
# Worker — consome lotes, recicla browser+IP a cada lote
# ══════════════════════════════════════════════════════════════════════════
async def _dispensar_consent(page):
    """Dispensa o muro de consentimento do Google (várias variações de UI/idioma)."""
    seletores = [
        'button[aria-label*="Aceitar"]',
        'button[aria-label*="Accept"]',
        'button:has-text("Aceitar tudo")',
        'button:has-text("Accept all")',
        'button:has-text("Concordo")',
        'form[action*="consent"] button',
        '#L2AGLb',  # "Aceitar tudo" no consent.google.com
    ]
    for sel in seletores:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click()
                await page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


async def _caixa_busca_ok(page) -> bool:
    """Confirma que a barra de busca do Maps está visível e interagível."""
    for sel in ('input#searchboxinput', 'input[name="q"]', 'input[role="combobox"]'):
        try:
            box = page.locator(sel).first
            if await box.is_visible(timeout=2500):
                return True
        except Exception:
            continue
    return False


async def abrir_maps(sess) -> bool:
    page = sess.page
    for _t in range(3):
        try:
            await sess.humanized_goto("https://www.google.com/maps", timeout=35000)

            # consent.google.com pode interceptar antes do Maps
            if "consent.google" in (page.url or ""):
                await _dispensar_consent(page)
                await page.wait_for_timeout(1500)

            await _dispensar_consent(page)

            # Sessão só é boa se a caixa de busca estiver realmente interagível
            if await _caixa_busca_ok(page):
                return True

            print(f"\n  ⚠️  Sessão sem caixa de busca (tentativa {_t+1}/3) — recarregando")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"\n  Maps tentativa {_t+1}/3: {str(e)[:60]}")
            await asyncio.sleep(3)
    return False


async def worker(wid, queue: asyncio.Queue, state, pw, pool: ProxyPool,
                 counter, total, out_json, lock, max_dist, metrics, full=False, usar_proxy=True):
    while True:
        try:
            batch_idx, batch = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        proxy = await pool.acquire_blocking() if usar_proxy else None
        if usar_proxy and not proxy:
            print(f"\n  W{wid} sem proxy disponível — lote {batch_idx} re-enfileirado")
            queue.put_nowait((batch_idx, batch))
            await asyncio.sleep(5)
            continue

        ip_label = f"{proxy['address']}:{proxy['port']}" if proxy else "direto"
        profile_dir = config.BROWSER_PROFILES_DIR / f"w{wid}_b{batch_idx}"
        sess = None
        captcha_no_lote = False
        idx_parou = 0

        try:
            tz_lng = batch[0].get("lng")
            sess = await HumanSession.create(pw, proxy, profile_dir, layer="maps", tz_hint_lng=tz_lng)

            if not await abrir_maps(sess):
                print(f"\n  🌐 [W{wid}] lote {batch_idx} IP={ip_label} | Maps não abriu")
                queue.put_nowait((batch_idx, batch))
                if proxy:
                    await pool.mark_cooldown(proxy, segundos=600)
                continue

            for j, reg in enumerate(batch):
                idx_parou = j
                if await sess.is_captcha():
                    captcha_no_lote = True
                    print(f"\n  🚫 [W{wid}] CAPTCHA no lote {batch_idx} IP={ip_label} — abortando")
                    break

                res = await search_one(sess, reg, max_dist, wid, full=full)

                async with lock:
                    state["results"].append(res)
                    state["results"].sort(key=lambda x: x.get("idx", 0))
                    salvar_json(out_json, state["results"])

                counter["done"] += 1
                metrics["pois"] += 1
                done = counter["done"]
                pct = done / total * 100 if total else 0
                st = res["status"]
                ok_mark = "✅" if res["match_valido"] else "❌"
                nome = reg.get("ocr_texto", "")[:30]
                print(f"\r🌐 [W{wid}] lote {batch_idx+1}/{metrics['n_lotes']} "
                      f"IP={ip_label} | {ok_mark} POI {j+1}/{len(batch)} "
                      f"[{done}/{total} {pct:.0f}%] {st:14} {nome}", flush=True)

                # Cadência humanizada entre buscas (exceto após o último do lote)
                if j < len(batch) - 1:
                    await sess.humanized_wait()

        except Exception as e:
            print(f"\n  W{wid} erro no lote {batch_idx}: {str(e)[:100]}")
        finally:
            if sess:
                try:
                    await sess.close()
                    metrics["bytes"] += sess.bytes_used
                except Exception:
                    pass
            # Limpa o profile do lote (cookies/cache descartados na rotação)
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
            except Exception:
                pass

            if captcha_no_lote:
                metrics["captcha_lotes"] += 1
                if proxy:
                    await pool.mark_cooldown(proxy)
                restantes = batch[idx_parou:]
                if restantes:
                    novo_idx = metrics["n_lotes"]
                    metrics["n_lotes"] += 1
                    queue.put_nowait((novo_idx, restantes))
            elif proxy:
                await pool.release(proxy)


# ══════════════════════════════════════════════════════════════════════════
# Orquestração
# ══════════════════════════════════════════════════════════════════════════
async def run(session_path: Path, n_workers: int, max_dist: float, full=False, ingest=False, usar_proxy=True):
    crops_json = session_path.parent / "crops" / "ocr_resultado.json"
    if not crops_json.exists():
        print(f"Erro: {crops_json} não encontrado. Rode ocr_pois.py primeiro.")
        return

    todos = json.loads(crops_json.read_text(encoding="utf-8"))
    out_json = session_path.parent / "crops" / "search_resultado.json"

    # Retomada
    processados_idx = set()
    results_existentes = []
    if out_json.exists():
        try:
            results_existentes = json.loads(out_json.read_text(encoding="utf-8"))
            processados_idx = {r["idx"] for r in results_existentes if "idx" in r}
            print(f"\n♻️  Retomando: {len(processados_idx)} itens já processados.")
        except Exception:
            pass

    fila_completa = [r for r in todos if len(r.get("ocr_texto", "").strip()) >= config.MIN_OCR_LEN]
    pendentes = [r for r in fila_completa if r.get("idx") not in processados_idx]
    ignorados = len(todos) - len(fila_completa)

    ocr_curtos = [
        {**r, "nivel": None, "status": "ocr_curto", "match_valido": False,
         "distancia_m": None, "similaridade": 0, "erros": [], "poi": {}}
        for r in todos
        if len(r.get("ocr_texto", "").strip()) < config.MIN_OCR_LEN
        and r.get("idx") not in processados_idx
    ]

    # Clustering espacial → lotes
    lotes = clusterizar_pois(pendentes)

    print(f"\n🔍 ComercialRadar — Search POIs v2 (otimizado)")
    print(f"   Sessão    : {session_path.parent.name}")
    print(f"   Total OCR : {len(todos)} | Pendentes: {len(pendentes)} | Ignorados: {ignorados}")
    print(f"   Clusters  : {resumo_clusters(lotes)}")
    print(f"   Workers   : {n_workers} | Dist máx: {max_dist}m\n")

    if not pendentes and not ocr_curtos:
        print("✅ Todos os itens já foram processados.")
        _gerar_html(results_existentes, session_path)
        return

    state = {"results": list(results_existentes)}
    lock = asyncio.Lock()
    counter = {"done": len(processados_idx)}
    total = len(fila_completa)

    if ocr_curtos:
        async with lock:
            state["results"].extend(ocr_curtos)
            state["results"].sort(key=lambda x: x.get("idx", 0))
            salvar_json(out_json, state["results"])

    # Pool de proxies
    pool = ProxyPool().start() if usar_proxy else None

    # Fila de lotes
    queue: asyncio.Queue = asyncio.Queue()
    for i, batch in enumerate(lotes):
        queue.put_nowait((i, batch))

    metrics = {
        "bytes": 0, "pois": 0, "captcha_lotes": 0,
        "n_lotes": len(lotes), "inicio": time.time(),
    }

    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    n_efetivo = min(n_workers, config.MAX_WORKERS, len(lotes))
    async with async_playwright() as pw:
        await asyncio.gather(*[
            worker(i, queue, state, pw, pool, counter, total, out_json, lock, max_dist, metrics, full, usar_proxy)
            for i in range(n_efetivo)
        ])

    # Limpa diretório de profiles
    try:
        shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)
    except Exception:
        pass

    metrics["_burned"] = pool.burned_count if pool else 0
    _imprimir_metricas(metrics, state["results"])
    _gerar_html(state["results"], session_path)

    # Ingestão no PostgreSQL (Prisma)
    if ingest:
        try:
            import db_export
            out = db_export.exportar(session_path, source="search")
            if out:
                db_export.ingerir(out)
        except Exception as e:
            print(f"⚠️  Ingestão falhou: {e}")


def _imprimir_metricas(metrics, results):
    dur = time.time() - metrics["inicio"]
    pois = metrics["pois"]
    mb = metrics["bytes"] / (1024 * 1024)
    mb_por_poi = (mb / pois) if pois else 0
    ok = sum(1 for r in results if r.get("status") == "ok")
    alta = sum(1 for r in results if r.get("status") == "distancia_alta")
    nao = sum(1 for r in results if r.get("status") == "nao_encontrado")
    ocrc = sum(1 for r in results if r.get("status") == "ocr_curto")
    err = sum(1 for r in results if r.get("status") == "erro")
    captcha_rate = (metrics["captcha_lotes"] / metrics["n_lotes"] * 100) if metrics["n_lotes"] else 0

    print(f"\n\n{'═' * 56}")
    print(f"📊 ComercialRadar — Search v2 | Resumo")
    print(f"{'═' * 56}")
    print(f"   ✅ Match válido (N2)   : {ok}")
    print(f"   ⚠️  Distância alta      : {alta}")
    print(f"   ❌ Não encontrado       : {nao}")
    print(f"   🔤 OCR curto            : {ocrc}")
    print(f"   💥 Erros                : {err}")
    print(f"   ─────────────────────────────")
    print(f"   📦 POIs processados     : {pois}")
    print(f"   📡 Banda total          : {mb:.1f} MB")
    print(f"   📉 Banda por POI        : {mb_por_poi:.2f} MB/POI")
    print(f"   🔥 IPs queimados        : {metrics.get('_burned', '—')}")
    print(f"   🚫 Lotes com CAPTCHA    : {metrics['captcha_lotes']} ({captcha_rate:.1f}%)")
    print(f"   ⏱  Tempo total          : {dur/60:.1f} min")
    print(f"{'═' * 56}")


def _gerar_html(results: list, session_path: Path):
    rows = ""
    for r in results:
        poi = r.get("poi", {})
        st = r.get("status", "")
        nivel = r.get("nivel") or "—"
        dist = r.get("distancia_m")
        dist_s = f"{dist:.0f}m" if dist is not None else "—"
        match = r.get("match_valido", False)
        erros = r.get("erros", [])
        bg = "#0a2a0a" if match else ("#2a1a00" if st == "distancia_alta" else "#2a0a0a")

        erros_html = "".join(f'<div style="color:#f66;font-size:0.7em">{e}</div>' for e in erros)
        horarios_html = "".join(f"<div>{d}: {h}</div>" for d, h in (poi.get("horarios") or {}).items())
        fotos_html = "".join(f'<img src="{f}" style="height:60px;margin:2px;border-radius:4px">' for f in (poi.get("fotos") or [])[:4])

        rows += f"""
        <tr style="background:{bg}">
          <td style="font-size:0.8em;color:#888">{r.get('idx','')}<br>N{nivel}</td>
          <td style="font-size:1em;font-weight:bold;color:#ffe">{r.get('ocr_texto','')}{erros_html}</td>
          <td style="color:{'#4fc' if match else '#f84'}">{st}<br><b>{dist_s}</b></td>
          <td style="color:#eee"><b>{poi.get('nome','')}</b><br><span style="color:#aaa;font-size:0.85em">{poi.get('categoria','')}</span></td>
          <td style="color:#fd8">{'⭐ '+poi.get('avaliacao','')+' ('+str(poi.get('total_avaliacoes',''))+')' if poi.get('avaliacao') else '—'}</td>
          <td style="font-size:0.8em;color:#ccc">{poi.get('endereco','')}<br><span style="color:#888">{poi.get('telefone','')}</span></td>
          <td style="font-size:0.75em;color:#aaa">{poi.get('status_horario','')}<br>{horarios_html}</td>
          <td style="font-size:0.75em;color:#888">{poi.get('website','')}</td>
          <td style="font-size:0.75em;color:#ccc">{(poi.get('ultima_avaliacao','') or '')[:100]}</td>
          <td>{fotos_html}</td>
          <td style="font-size:0.75em;color:#666">{r['lat']:.5f},{r['lng']:.5f}<br>{'→ '+str(poi.get('maps_lat',''))+','+str(poi.get('maps_lng','')) if poi.get('maps_lat') else ''}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Search POIs v2 — ComercialRadar</title>
<style>
  body{{font-family:sans-serif;background:#111;color:#eee;margin:0}}
  h1{{padding:14px;color:#4fc;margin:0;font-size:1.2em}}
  table{{border-collapse:collapse;width:100%}}
  th{{background:#222;padding:8px;text-align:left;font-size:0.8em;position:sticky;top:0}}
  td{{border-bottom:1px solid #1e1e1e;padding:8px;vertical-align:top}}
</style></head><body>
<h1>🗺 Search POIs v2 — {session_path.parent.name} — {datetime.now().strftime('%d/%m/%Y %H:%M')}</h1>
<table>
  <tr><th>#/N</th><th>OCR/Erros</th><th>Status/Dist</th><th>Nome Maps</th><th>Avaliação</th>
      <th>Endereço/Tel</th><th>Horários</th><th>Website</th><th>Última Avaliação</th><th>Fotos</th><th>Coords</th></tr>
  {rows}
</table></body></html>"""

    out_html = session_path.parent / "crops" / "search_resumo.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"🌐 HTML: {out_html}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", help="Caminho para session.json")
    parser.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    parser.add_argument("--max-dist", type=float, default=config.MAX_DIST_M)
    parser.add_argument("--limit", type=int, default=0, help="Limita nº de POIs (teste)")
    parser.add_argument("--full", action="store_true",
                        help="Extração completa (todas as fotos + avaliações) nos matches válidos")
    parser.add_argument("--ingest", action="store_true",
                        help="Após coletar, grava no PostgreSQL via Prisma")
    parser.add_argument("--no-proxy", action="store_true",
                        help="Roda direto, sem gastar banda de proxy (teste)")
    args = parser.parse_args()

    session_path = Path(args.session)
    if not session_path.exists():
        print(f"Erro: {session_path} não encontrado")
        return

    # --limit: corta o ocr_resultado em memória para testes de aceitação
    if args.limit > 0:
        global clusterizar_pois
        _orig = clusterizar_pois

        def _limited(pois, *a, **k):
            return _orig(pois[: args.limit], *a, **k)

        clusterizar_pois = _limited

    asyncio.run(run(session_path, args.workers, args.max_dist, full=args.full,
                    ingest=args.ingest, usar_proxy=not args.no_proxy))


if __name__ == "__main__":
    main()
