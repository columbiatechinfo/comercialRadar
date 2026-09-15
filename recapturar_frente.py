# -*- coding: utf-8 -*-
"""recapturar_frente.py — a foto de rua de frente, com seta, por cima da `sv_frente` (14/09/2026).

Decisao do dono do produto: a IA recebe UMA foto de rua, de frente para o imovel
(`visada_perpendicular`: perpendicular da via pelo OSRM, fallback o panorama mais
de frente), com a seta semitransparente ate a coordenada e a distancia da camera
(`desenho_seta`). Ela SOBRESCREVE a `sv_frente` do POI — mesma linha, mesmo
`gravar` da captura antiga, que ja zera o `storage_path` para a imagem nova
valer. As laterais e o fundo ficam como estao.

A RESOLUCAO E A DA CAPTURA (1280x900 em densidade 2, menos o corte da
interface), em WebP: quem reduz para a IA e a montagem da chamada, nao o acervo.

FALHA NAO GRAVA. O `gravar` com `dados` nulo apagaria a foto antiga; o POI que
nao achou panorama ou nao abriu fica com a que tinha, e vai para o relatorio.

    python recapturar_frente.py --alvos /saida/alvos.json --abas 8 --saida /saida
    python recapturar_frente.py --alvos /saida/alvos.json --limite 40 --abas 8 --saida /saida
    python recapturar_frente.py ... --retomar 2026-09-14T23:00-03:00   # pula quem ja foi
    python recapturar_frente.py ... --parte 0/3     # um navegador por processo, tres processos
    python recapturar_frente.py --ligacoes-arquivo ligs.txt --abas 8 --saida /saida   # antes do julgamento leve
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import base_comum as bc  # noqa: E402
import capturar_evidencia as ce  # noqa: E402
import frente_da_rua as fr  # noqa: E402
import streetview_geo as sv  # noqa: E402
import visada_perpendicular as vp  # noqa: E402
from desenho_seta import CORTE_TOPO_EXTRA, seta  # noqa: E402


def _ja_feitos(desde):
    if not desde:
        return set()
    con = bc.conectar()
    with con.cursor() as k:
        k.execute("""select poi_id from radar_comercial.poi_evidencia
                      where tipo = 'sv_frente' and capturado_em >= %s and fov = %s and dados is not null""",
                  (desde, float(vp.FOV)))
        r = {x[0] for x in k.fetchall()}
    con.close()
    return r


def alvos_das_ligacoes(arquivo):
    """Os POIs da foto de rua (o mais perto do hidrometro, `avaliar_enxuto.poi_da_foto_de_rua`) das ligacoes do
    arquivo que AINDA NAO TEM a captura nova — o passo antes do julgamento leve (15/09/2026): nenhuma foto com
    a mira antiga vai para a IA."""
    import avaliar_enxuto as ae
    ligs = [x.strip() for x in open(arquivo) if x.strip()]
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select lp.ligacao, array_agg(p.id) from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                    where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                    group by 1""", (ligs,))
    alvos, vistos, ja = [], set(), 0
    for lig, ids in cur.fetchall():
        e = ae.poi_da_foto_de_rua(cur, lig, ids)
        if not e:
            continue
        pid, _d, tem_nova, la, lo, rua = e
        if tem_nova:
            ja += 1
        elif pid not in vistos and la is not None:
            vistos.add(pid)
            alvos.append({"poi": pid, "lat": float(la), "lng": float(lo), "rua": rua, "ligacao": lig})
    con.close()
    print("   %d ligacoes · %d ja com a foto de rua nova · %d POIs a capturar" % (len(ligs), ja, len(alvos)), flush=True)
    return alvos


async def uma(page, poco, a):
    esc = await asyncio.to_thread(vp.escolher, a["lat"], a["lng"], a.get("rua"))
    if esc.get("erro"):
        return {"erro": esc["erro"]}
    p = esc["pano"]
    ok = await sv._abrir_por_id(page, p["pano_id"], esc["heading"], vp.FOV)
    if not ok:
        await page.wait_for_timeout(1500)
        ok = await sv._abrir_por_id(page, p["pano_id"], esc["heading"], vp.FOV)
    if not ok:
        return {"erro": "o Maps nao abriu o panorama em duas tentativas", "metodo": esc["metodo"]}
    await page.wait_for_timeout(1800)
    arr = cv2.imdecode(np.frombuffer(ce._cortar_interface(await page.screenshot()), np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return {"erro": "print vazio", "metodo": esc["metodo"]}
    arr = arr[int(arr.shape[0] * CORTE_TOPO_EXTRA):, :]
    x = fr.x_na_visada(esc["rumo_alvo"], esc["heading"], vp.FOV)
    arr = seta(arr, x if x is not None else 0.5, esc["dist"])
    ok_png, buf = cv2.imencode(".png", arr)
    if not ok_png:
        return {"erro": "png", "metodo": esc["metodo"]}
    img = ce._para_webp(buf.tobytes())
    data = p.get("data") or await asyncio.to_thread(ce._data_do_pano, p["pano_id"])
    await asyncio.to_thread(ce.gravar, poco, a["poi"], "sv_frente", img,
                            pano_id=p["pano_id"], cam_lat=p["lat"], cam_lng=p["lng"], heading=float(esc["heading"]),
                            pitch=5.0, fov=float(vp.FOV), distancia_m=float(esc["dist"]),
                            largura_px=int(arr.shape[1]), altura_px=int(arr.shape[0]), data_imagem=data,
                            mira_x=float(x) if x is not None else None, mira_rumo=float(esc["rumo_alvo"]))
    return {"metodo": esc["metodo"], "data": data, "dist": round(esc["dist"], 1), "kb": len(img) // 1024,
            "px": [int(arr.shape[1]), int(arr.shape[0])], "via": esc.get("via"),
            "por_que_fallback": esc.get("por_que_fallback")}


async def main(a):
    alvos = alvos_das_ligacoes(a.ligacoes_arquivo) if a.ligacoes_arquivo else json.load(open(a.alvos, encoding="utf-8"))["sv"]
    feitos_antes = _ja_feitos(a.retomar)
    fila_l = [x for x in alvos if x["poi"] not in feitos_antes]
    # UM NAVEGADOR POR PROCESSO, e varios processos: o processo grafico do Chromium
    # (render por software no Xvfb) e um so por navegador e trava em 100% de um
    # nucleo. 12 abas num navegador renderam o mesmo que 8 (36 x 38 POIs/min).
    if a.parte:
        k, n = (int(x) for x in a.parte.split("/"))
        fila_l = [x for x in fila_l if x["poi"] % n == k]
    if a.limite:
        fila_l = fila_l[:a.limite]
    print("▶ foto de rua de frente: %d POIs nos alvos, %d ja feitos, %d a capturar, %d abas"
          % (len(alvos), len(feitos_antes), len(fila_l), a.abas), flush=True)
    os.makedirs(a.saida, exist_ok=True)
    rel = open(os.path.join(a.saida, "frente.jsonl"), "a", encoding="utf-8")
    fila = asyncio.Queue()
    for x in fila_l:
        fila.put_nowait(x)
    placar = {"ok": 0, "perpendicular": 0, "mais de frente": 0, "sem_data": 0, "erro": 0}
    t0 = time.time()
    poco = ce.Poco(min(ce.CONEXOES, a.abas))
    from playwright.async_api import async_playwright

    async def aba(nav, n):
        ctx = await nav.new_context(viewport={"width": ce.LARG, "height": ce.ALT}, device_scale_factor=ce.DENSIDADE)
        page = await ctx.new_page()
        await asyncio.sleep(ce.ESCALONAR_S * n)
        await ce._aquecer(page)
        try:
            while True:
                try:
                    alvo = fila.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    r = await asyncio.wait_for(uma(page, poco, alvo), 90)
                except Exception as e:                         # noqa: BLE001
                    r = {"erro": "%s: %s" % (type(e).__name__, str(e)[:120])}
                    if page.is_closed() or isinstance(e, asyncio.TimeoutError):
                        try:
                            await ctx.close()
                        except Exception:                      # noqa: BLE001
                            pass
                        ctx = await nav.new_context(viewport={"width": ce.LARG, "height": ce.ALT},
                                                    device_scale_factor=ce.DENSIDADE)
                        page = await ctx.new_page()
                        await ce._aquecer(page)
                if r.get("erro"):
                    placar["erro"] += 1
                else:
                    placar["ok"] += 1
                    placar[r["metodo"]] += 1
                    placar["sem_data"] += 0 if r.get("data") else 1
                rel.write(json.dumps({"poi": alvo["poi"], "ligacao": alvo.get("ligacao"), **r}, ensure_ascii=False,
                                     default=str) + "\n")
                rel.flush()
                n_feitos = placar["ok"] + placar["erro"]
                if n_feitos % 20 == 0:
                    ritmo = n_feitos / max(1, time.time() - t0) * 60
                    print("   [%d/%d] %.1f POIs/min · falta ~%.0f min · %s"
                          % (n_feitos, len(fila_l), ritmo, (len(fila_l) - n_feitos) / max(ritmo, 0.1), placar),
                          flush=True)
        finally:
            try:
                await ctx.close()
            except Exception:                                  # noqa: BLE001
                pass

    async with async_playwright() as pw:
        nav = await pw.chromium.launch(headless=False, args=ce.ARGS_NAV)
        try:
            await asyncio.gather(*(aba(nav, i) for i in range(a.abas)))
        finally:
            await nav.close()
            poco.fechar()
    rel.close()
    print("■ foto de rua pronta em %.0f min · %s" % ((time.time() - t0) / 60, placar), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alvos")
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo",
                   help="captura so o POI da foto de rua das ligacoes do arquivo que ainda nao tem a captura nova")
    p.add_argument("--abas", type=int, default=8)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--retomar", default="")
    p.add_argument("--parte", default="", help="k/n: so os POIs com id %% n == k")
    p.add_argument("--saida", default="/saida")
    asyncio.run(main(p.parse_args()))
