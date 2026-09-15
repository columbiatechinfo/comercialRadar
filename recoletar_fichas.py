# -*- coding: utf-8 -*-
"""recoletar_fichas.py — colhe de novo a ficha do Maps de POIs dados (14/09/2026).

O retroativo das fotos dos vizinhos: a coleta antiga gravou, na ficha do lugar,
as fotos de "Lugares tambem pesquisados". O extrator novo (`minerar_placeid`,
commit 510d1a2) so grava as do proprio lugar, com data, e os "Resultados da
Web". Isto roda o extrator novo sobre uma lista de POIs — os que deram foto a
algum veredito — sem passar pela fila da etapa 4.

    python recoletar_fichas.py --arquivo pois.txt --navegadores 6
    python recoletar_fichas.py --dos-vereditos --navegadores 6     # a lista sai do banco

Um navegador por IP; troca de IP a cada `--por-ip` fichas, e na hora quando a
pagina vem vazia (o IP e posto de castigo no pool compartilhado). Grava ficha a
ficha (`gravar_um`): cair no meio perde so a que estava na tela, e `--retomar`
pula as que ja tem foto com `secao` ou ficha lida depois do inicio.
"""
import argparse
import asyncio
import sys
import time

sys.path.insert(0, "/app")
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402
import minerar_placeid as mp  # noqa: E402
from proxy_pool import ProxyPool  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

SQL_DOS_VEREDITOS = """
    select distinct (r->>'poi')::bigint
      from radar_comercial.ligacao_veredito v
      cross join lateral jsonb_array_elements(coalesce(v.percepcao::jsonb->'fotos_ref','[]')) r
     where r->>'tipo' = 'foto publicada' and r->>'poi' ~ '^[0-9]+$'"""


def _alvos(con, ids, retomar):
    with con.cursor() as k:
        k.execute("""select p.id, p.place_id from radar_comercial.pois p
                      where p.id = any(%s) and p.place_id like 'ChIJ%%' and p.fundido_em is null
                        and (not %s or not exists (select 1 from radar_comercial.maps_data m
                                                    where m.poi_id = p.id and m.resultados_web_estado is not null))
                      order by p.id""", (ids, retomar))
        r = k.fetchall()
    con.commit()
    return r


async def main(a):
    con = bc.conectar()
    if a.dos_vereditos:
        with con.cursor() as k:
            k.execute(SQL_DOS_VEREDITOS)
            ids = [x[0] for x in k.fetchall()]
        con.commit()
    else:
        ids = [int(x) for x in open(a.arquivo).read().split() if x.strip().isdigit()]
    alvos = _alvos(con, ids, a.retomar)
    if a.limite:
        alvos = alvos[:a.limite]
    print("▶ recoleta de fichas: %d POIs pedidos, %d a colher, %d navegadores" % (len(ids), len(alvos), a.navegadores),
          flush=True)
    fila = asyncio.Queue()
    for x in alvos:
        fila.put_nowait(x)
    pool = ProxyPool()
    pool.start()
    usaveis = [p for p in pool._proxies if not pool.pais or p.get("country") == pool.pais] or pool._proxies
    trava_db, placar, t0 = asyncio.Lock(), {"ok": 0, "vazia": 0, "falha": 0, "descartadas": 0, "datadas": 0,
                                           "web_lido": 0}, time.time()
    proximo = {"i": 0}
    tentativas = {}

    def outro_ip():
        for _ in range(len(usaveis)):
            c = usaveis[proximo["i"] % len(usaveis)]
            proximo["i"] += 1
            try:
                if pool.em_castigo(c):
                    continue
            except Exception:                                  # noqa: BLE001
                pass
            return c
        return usaveis[proximo["i"] % len(usaveis)]

    async def trabalhador(pw, n):
        while not fila.empty():
            px = outro_ip()
            nav = await pw.chromium.launch(headless=False, args=mp.ARGS, proxy={
                "server": px["server"], "username": px["username"], "password": px["password"]})
            try:
                ctx = await nav.new_context(viewport={"width": 1360, "height": 1000}, locale="pt-BR",
                                            timezone_id="America/Sao_Paulo", storage_state=a.cookie)
                for _ in range(a.por_ip):
                    try:
                        poi_id, place = fila.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        d = await asyncio.wait_for(mp.detalhar(ctx, {"placeId": place, "lat": None, "lng": None}), 150)
                    except Exception as e:                     # noqa: BLE001
                        placar["falha"] += 1
                        tentativas[poi_id] = tentativas.get(poi_id, 0) + 1
                        if tentativas[poi_id] < 3:              # outra chance, com outro IP
                            fila.put_nowait((poi_id, place))
                        print("   nav%02d poi %s falhou (%d): %s" % (n, poi_id, tentativas[poi_id], str(e)[:80]),
                              flush=True)
                        break
                    # PAGINA DE ERRO DO MAPS E IP RUIM, como a pagina vazia. `gravar_um` ja
                    # recusa gravar o titulo de erro, mas devolve 0 em silencio e o POI
                    # vai para a fila da etapa 4 (`devolver`); aqui ele contava como "ok".
                    # Medido em 15/09/2026: 35 das 844 fichas ficaram com as fotos antigas.
                    if mp._pagina_vazia(d) or mp._e_titulo_de_erro(d.get("nome")):
                        placar["vazia"] += 1
                        tentativas[poi_id] = tentativas.get(poi_id, 0) + 1
                        if tentativas[poi_id] < 3:
                            fila.put_nowait((poi_id, place))
                        try:
                            await pool.mark_cooldown(px)
                        except Exception:                      # noqa: BLE001
                            pass
                        break                                  # o IP, e nao o POI
                    async with trava_db:
                        await asyncio.to_thread(mp.gravar_um, con, poi_id, d)
                        placar["ok"] += 1
                        placar["descartadas"] += d.get("fotosDescartadas") or 0
                        placar["datadas"] += d.get("fotosDatadas") or 0
                        placar["web_lido"] += 1 if (d.get("resultadosWeb") or {}).get("estado") == "lido" else 0
                        if placar["ok"] % 20 == 0:
                            ritmo = placar["ok"] / max(1, time.time() - t0) * 60
                            print("   [%d/%d] %.1f fichas/min · falta ~%.0f min · %s"
                                  % (placar["ok"], len(alvos), ritmo, (len(alvos) - placar["ok"]) / max(ritmo, 0.1),
                                     placar), flush=True)
            finally:
                try:
                    await nav.close()
                except Exception:                              # noqa: BLE001
                    pass

    async with async_playwright() as pw:
        await asyncio.gather(*(trabalhador(pw, i) for i in range(a.navegadores)))
    con.close()
    print("■ recoleta pronta em %.0f min · %s" % ((time.time() - t0) / 60, placar), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arquivo")
    p.add_argument("--dos-vereditos", dest="dos_vereditos", action="store_true")
    p.add_argument("--navegadores", type=int, default=6)
    p.add_argument("--por-ip", dest="por_ip", type=int, default=25)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--retomar", action="store_true")
    p.add_argument("--cookie", default="/app/estado/cookie_maps.json")
    a = p.parse_args()
    if not a.arquivo and not a.dos_vereditos:
        p.error("--arquivo ou --dos-vereditos")
    asyncio.run(main(a))
