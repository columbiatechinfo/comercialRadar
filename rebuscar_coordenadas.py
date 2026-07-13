"""
rebuscar_coordenadas.py — Re-busca no Google Maps por NOME + CIDADE + UF para
recuperar a COORDENADA PRECISA dos POIs com coord_compartilhada (imprecisa).

Reaproveita a mecânica robusta do pipeline (HumanSession + seletores do Maps).
Extrai o ponto exato (!3d!4d da URL) e o place_id. Por padrão é DRY-RUN: só
mostra os retornos (coord antiga → nova, distância, place_id, se resolve a
colisão). Com --aplicar, grava maps_lat/maps_lng/place_id e recalcula a flag.

USO:
  .venv\\Scripts\\python rebuscar_coordenadas.py [--limit N] [--ids ...] [--proxy] [--aplicar]
"""
import re
import sys
import json
import math
import asyncio
import argparse
import unicodedata
from pathlib import Path

# resultados da busca ficam AQUI (a busca é cara; aplicar deve ser offline/instantâneo)
RESULT_FILE = Path(__file__).resolve().parent / "rebusca_resultados.json"


def _carregar_cache() -> dict:
    if RESULT_FILE.exists():
        try:
            return {int(k): v for k, v in json.loads(RESULT_FILE.read_text("utf-8")).items()}
        except Exception:
            return {}
    return {}


def _salvar_cache(cache: dict):
    RESULT_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")

# genéricos que NÃO distinguem um estabelecimento de outro
_GEN = {"escola", "municipal", "estadual", "federal", "praca", "restaurante",
        "universitario", "clinica", "centro", "loja", "bar", "auto", "servicos",
        "instituto", "colegio", "universidade", "unidade", "casa", "shopping",
        "comercial", "ltda", "eireli", "parnaiba", "piaui", "the", "avenida",
        "operacional", "educacional", "de", "da", "do", "dos", "das", "sao"}


def _tok(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if len(t) >= 3 and t not in _GEN}


def _nome_forte(query, resultado) -> bool:
    """True se o nome do resultado casa forte com a busca (token distintivo comum)."""
    a, b = _tok(query), _tok(resultado)
    return bool(a and b and (a & b))

from playwright.async_api import async_playwright

import config  # carrega .env
import realtime_ingest
from human_browser import HumanSession
from proxy_pool import ProxyPool
from search_pois_v2 import (_preencher_busca, aguarda_painel_ou_lista, extract_panel,
                            selecionar_melhor_card, _dispensar_consent, _caixa_busca_ok)
from search_from_sheet import extrair_place_id


async def abrir_maps_rapido(sess) -> bool:
    """Abertura enxuta: 1 goto com timeout curto pra descartar proxy ruim rápido
    (o abrir_maps padrão gasta 3×35s = 105s por proxy morto)."""
    page = sess.page
    try:
        await sess.humanized_goto("https://www.google.com/maps?hl=pt-BR", timeout=26000)
    except Exception:
        return False
    try:
        if "consent.google" in (page.url or ""):
            await _dispensar_consent(page)
            await page.wait_for_timeout(1200)
        await _dispensar_consent(page)
    except Exception:
        pass
    try:
        return await _caixa_busca_ok(page)
    except Exception:
        return False

ROTA = 35   # troca proxy/sessão a cada N POIs (ou ao detectar CAPTCHA)
# Gate geográfico: o Maps pega homônimos de outras cidades (RU em Teresina, 265km).
# Só aceita resultado dentro do raio de Parnaíba (cobre Luís Correia/Ilha Grande).
PARNAIBA_LAT, PARNAIBA_LNG = -2.9055, -41.7768
GATE_KM = 25


def _dist(a, b, c, d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def carregar(limit, ids):
    conn = realtime_ingest.conectar(); cur = conn.cursor()
    if ids:
        cur.execute("""SELECT id,nome,cidade,uf,maps_lat,maps_lng,place_id,endereco
                       FROM pois WHERE id = ANY(%s)""", (ids,))
    else:
        # TODOS os flagados (prioriza os colididos SEM place_id). --limit só p/ teste.
        lim = "LIMIT %s" % int(limit) if limit and limit > 0 else ""
        cur.execute(f"""SELECT id,nome,cidade,uf,maps_lat,maps_lng,place_id,endereco
                        FROM pois
                        WHERE coord_compartilhada = true
                        ORDER BY (place_id IS NOT NULL), coord_grupo DESC, id
                        {lim}""")
    rows = cur.fetchall(); conn.close()
    return rows


def _coord_unica(lat, lng, poi_id) -> bool:
    conn = realtime_ingest.conectar(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pois WHERE maps_lat=%s AND maps_lng=%s AND id<>%s",
                (lat, lng, poi_id))
    n = cur.fetchone()[0]; conn.close()
    return n == 0


async def _rebuscar_um(sess, nome, cidade, uf):
    page = sess.page
    query = f"{nome}, {cidade or 'Parnaíba'} - {uf or 'PI'}"
    url_antes = page.url
    # Zera a URL antes de buscar: se a nova busca NÃO navegar, a URL segue neutra
    # e a gente não lê o painel obsoleto do POI anterior (bug de staleness).
    try:
        await page.goto("https://www.google.com/maps?hl=pt-BR",
                        wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass
    url_neutra = page.url
    await _preencher_busca(page, query)
    await page.keyboard.press("Enter")
    import time as _t
    fim = _t.time() + config.WAIT_PAINEL_MS / 1000
    mudou = False
    while _t.time() < fim:
        u = page.url
        if u != url_neutra and ("/place/" in u or "/search/" in u):
            mudou = True
            break
        await page.wait_for_timeout(300)
    if not mudou:                     # não navegou → resultado seria obsoleto
        return None
    # ESPERA A URL ESTABILIZAR: o !3d!4d (coordenada do place) demora a assentar;
    # ler cedo faz o POI herdar a coordenada do anterior (bug caçado 07/07/2026).
    fim2 = _t.time() + 10
    ultima = page.url
    estavel = _t.time()
    while _t.time() < fim2:
        await page.wait_for_timeout(400)
        atual = page.url
        if atual != ultima:
            ultima = atual
            estavel = _t.time()
        elif _t.time() - estavel >= 1.8 and "!3d" in atual:
            break                     # URL parada há 1.8s e já tem coordenada
    res = await aguarda_painel_ou_lista(page)
    if res == "painel":
        poi = await extract_panel(page)
        # sanidade: painel tem que ter coordenada nova (não pode vir vazio/obsoleto)
        return poi if poi and poi.get("maps_lat") else None
    if res == "lista":
        return await selecionar_melhor_card(sess, nome, -2.90, -41.77)
    return None


async def run(limit, ids, usar_proxy, aplicar):
    alvos = carregar(limit, ids)
    print(f"🔁 Re-busca de coordenada: {len(alvos)} POIs | "
          f"{'APLICAR' if aplicar else 'DRY-RUN'} | proxy={usar_proxy}\n")
    if not alvos:
        print("nada a rebuscar."); return

    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    pool = ProxyPool().start() if usar_proxy else None
    async with async_playwright() as pw:
        conn = realtime_ingest.conectar()
        sess = None
        proxy = None
        desde_troca = 0

        _prof_seq = [0]

        async def _nova_sessao(tentativas=25):
            """Rotaciona por vários proxies até um conseguir abrir o Maps."""
            nonlocal sess, proxy, desde_troca
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass
                sess = None
            for _ in range(tentativas):
                proxy = await pool.acquire_blocking() if usar_proxy else None
                _prof_seq[0] += 1
                prof = config.BROWSER_PROFILES_DIR / f"rebusca_{_prof_seq[0]}"
                try:
                    sess = await HumanSession.create(pw, proxy, prof, layer="maps",
                                                     tz_hint_lng=-41.77, headless=True)
                    if await abrir_maps_rapido(sess):
                        desde_troca = 0
                        return True
                except Exception as e:
                    print(f"   ⚠ sessão falhou ({str(e)[:40]}) — trocando proxy")
                # esse proxy não presta pra Maps agora
                if usar_proxy and proxy:
                    await pool.mark_cooldown(proxy, 45)
                if sess:
                    try:
                        await sess.close()
                    except Exception:
                        pass
                    sess = None
            return False

        if not await _nova_sessao():
            print("não abriu o Maps em nenhum proxy (rede/CAPTCHA geral).")
            return

        cache = _carregar_cache()       # retoma: não re-busca o que já achou
        for pid, nome, cid, uf, olat, olng, opid, end in alvos:
            if pid in cache:
                continue                # já buscado numa rodada anterior
            # rotaciona sessão/proxy periodicamente ou se caiu em CAPTCHA
            if sess is None or desde_troca >= ROTA or await sess.is_captcha():
                if proxy and usar_proxy:
                    await pool.mark_cooldown(proxy, 120)
                if not await _nova_sessao():
                    print("  ⚠ sem proxy bom no momento — parando (retomável).")
                    break
            desde_troca += 1
            try:
                poi = await _rebuscar_um(sess, nome, cid, uf)
            except Exception as e:
                poi = None
                print(f"  #{pid} {nome[:26]:26} ERRO {str(e)[:40]}")
            entry = {"nome": nome}
            if not poi or not poi.get("maps_lat"):
                entry["status"] = "nao_achou"
                print(f"  #{pid} {nome[:26]:26} → não achou place")
            else:
                nlat, nlng = poi["maps_lat"], poi["maps_lng"]
                nome_res = (poi.get("nome") or "").strip()
                npid = extrair_place_id(poi.get("maps_url", ""))
                fora = _dist(PARNAIBA_LAT, PARNAIBA_LNG, nlat, nlng)
                if fora > GATE_KM * 1000:
                    entry.update({"status": "rejeitado", "nome_res": nome_res, "km": round(fora / 1000)})
                    print(f"  #{pid} {nome[:26]:26} → \"{nome_res[:24]}\" "
                          f"REJEITADO ({fora/1000:.0f}km — homônimo)")
                else:
                    d = _dist(olat, olng, nlat, nlng) if (olat and olng) else None
                    forte = _nome_forte(nome, nome_res)
                    entry.update({"status": "forte" if forte else "fraco", "nome_res": nome_res,
                                  "lat": nlat, "lng": nlng, "place_id": npid,
                                  "moveu_m": round(d) if d else 0})
                    sel = "✅ APLICA" if forte else "⚠ REVISAR (nome fraco)"
                    print(f"  #{pid} {nome[:24]:24} → \"{nome_res[:28]}\"  {sel}  "
                          f"({('moveu %dm' % d) if d else 'nova coord'} | place_id {npid[:18] if npid else '—'})")
            cache[pid] = entry
            _salvar_cache(cache)        # persiste JÁ (resiliente a queda/Ctrl+C)
            if sess:
                await sess.humanized_wait()
        conn.close()
        if sess:
            await sess.close()
        if pool and proxy:
            await pool.release(proxy)

    cache = _carregar_cache()
    por = {}
    for e in cache.values():
        por[e.get("status")] = por.get(e.get("status"), 0) + 1
    print(f"\n{'═'*54}")
    print(f"  buscados/salvos no arquivo : {len(cache)}/{len(alvos)}")
    print(f"  ✅ nome forte (aplicáveis) : {por.get('forte', 0)}")
    print(f"  ⚠ nome fraco (revisar)     : {por.get('fraco', 0)}")
    print(f"  ❌ rejeitado (fora área)    : {por.get('rejeitado', 0)}")
    print(f"  ⊘ não achou                : {por.get('nao_achou', 0)}")
    print(f"\n  Resultados salvos em {RESULT_FILE.name}.")
    print(f"  Pra GRAVAR no banco (offline, sem internet):")
    print(f"    .venv\\Scripts\\python rebuscar_coordenadas.py --gravar-arquivo")
    print(f"{'═'*54}")


def reset_suspeitos():
    """Remove do cache os resultados 'forte' cujos POIs AINDA colidem — eram os
    que pegaram coordenada obsoleta. Assim a próxima busca os refaz com o fix."""
    cache = _carregar_cache()
    conn = realtime_ingest.conectar(); cur = conn.cursor()
    cur.execute("SELECT id FROM pois WHERE coord_compartilhada")
    flag = {r[0] for r in cur.fetchall()}; conn.close()
    antes = len(cache)
    cache = {pid: e for pid, e in cache.items()
             if not (e.get("status") == "forte" and pid in flag)}
    _salvar_cache(cache)
    print(f"🧹 {antes - len(cache)} suspeitos removidos do cache (coord obsoleta). "
          f"Rode a busca de novo — só eles serão refeitos, agora com URL estabilizada.")


def gravar_do_arquivo():
    """Aplica os resultados de nome forte do JSON no banco — OFFLINE, instantâneo,
    sem browser/proxy. Idempotente."""
    cache = _carregar_cache()
    if not cache:
        print(f"nada em {RESULT_FILE.name} — rode a busca primeiro."); return
    fortes = {pid: e for pid, e in cache.items() if e.get("status") == "forte" and e.get("lat")}
    print(f"📝 Gravando {len(fortes)} coordenadas (nome forte) do arquivo → banco...")
    conn = realtime_ingest.conectar()
    grav = 0
    for pid, e in fortes.items():
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pois WHERE maps_lat=%s AND maps_lng=%s AND id<>%s",
                        (e["lat"], e["lng"], pid))
            unica = cur.fetchone()[0] == 0
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE pois SET maps_lat=%s, maps_lng=%s,
                           place_id=COALESCE(%s,place_id), fonte_dado='maps',
                           coord_compartilhada=%s WHERE id=%s""",
                        (e["lat"], e["lng"], e.get("place_id"), (not unica), pid))
            grav += cur.rowcount
    # recomputa a flag em todo o banco (colisão muda quando um POI sai do grupo)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE pois SET coord_compartilhada=false, coord_grupo=NULL WHERE coord_compartilhada")
        cur.execute("""WITH g AS (SELECT maps_lat,maps_lng,COUNT(*) n FROM pois
                       WHERE maps_lat IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1)
                       UPDATE pois p SET coord_compartilhada=true, coord_grupo=g.n
                       FROM g WHERE p.maps_lat=g.maps_lat AND p.maps_lng=g.maps_lng""")
        cur.execute("SELECT COUNT(*) FROM pois WHERE coord_compartilhada")
        restam = cur.fetchone()[0]
    conn.close()
    print(f"✅ {grav} POIs atualizados. Flag recomputada: {restam} ainda compartilham coord "
          f"(co-localizados de shopping + os que não recuperaram).")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="0 = todos os flagados (só p/ teste)")
    p.add_argument("--ids", default="")
    p.add_argument("--proxy", action="store_true")
    p.add_argument("--aplicar", action="store_true", help="(legado) buscar E gravar na mesma passada")
    p.add_argument("--gravar-arquivo", action="store_true",
                   help="Grava no banco os resultados JÁ buscados (offline, sem internet)")
    p.add_argument("--reset-suspeitos", action="store_true",
                   help="Invalida no cache os 'forte' que ainda colidem (coord obsoleta) p/ refazer")
    a = p.parse_args()
    if a.reset_suspeitos:
        reset_suspeitos()
        return
    if a.gravar_arquivo:
        gravar_do_arquivo()
        return
    ids = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else []
    asyncio.run(run(a.limit, ids, a.proxy, a.aplicar))
    if a.aplicar:          # busca já salvou no cache; agora grava offline
        gravar_do_arquivo()


if __name__ == "__main__":
    main()
