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
from desenho_seta import CORTE_TOPO_EXTRA, planta, seta  # noqa: E402


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


#: fotos de rua capturadas antes da correcao da mira (15/09/2026) que ficaram com o imovel fora do meio
CORRECAO_DA_MIRA = "2026-09-15 11:30-03"
#: a foto de antes disto nao tem a seta maior nem a planta: e sempre capturada de novo, mesmo com a mira igual
FORMATO_DA_FOTO = "2026-09-15 11:30-03"
#: a foto de antes da correcao so e capturada de novo se o panorama escolhido mudou ou a mira girou mais que isto
MIRA_IGUAL_GRAUS = 8
MIRA_MEIO = (0.3, 0.7)


def alvos_das_ligacoes(arquivo, forcar=False):
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
    alvos, vistos, ja, sem_poi, descentradas = [], set(), 0, [], 0
    for lig, ids in cur.fetchall():
        e = ae.poi_da_foto_de_rua(cur, lig, ids)
        if not e:
            sem_poi.append(lig)
            continue
        pid, _d, tem_nova, la, lo, rua = e
        if tem_nova:
            # A MIRA ANTIGA DEIXAVA O IMOVEL DE LADO: refaz a descentrada capturada antes da correcao
            # A FOTO DE ANTES DA CORRECAO (15/09/2026, mira no centro e panorama mais recente) volta para a fila
            # com o panorama e a mira que tem: `uma` so captura de novo se a escolha nova for diferente
            cur.execute("""select case when capturado_em >= %s then pano_id end, heading, capturado_em < %s
                             from radar_comercial.poi_evidencia where poi_id = %s and tipo = 'sv_frente'""",
                        (FORMATO_DA_FOTO, CORRECAO_DA_MIRA, pid))
            pano0, head0, antiga = cur.fetchone() or (None, None, False)
            if forcar or antiga:
                tem_nova = False
                descentradas += 1
        else:
            pano0 = head0 = None
        if tem_nova:
            ja += 1
        elif pid not in vistos and la is not None:
            vistos.add(pid)
            alvos.append({"poi": pid, "lat": float(la), "lng": float(lo), "rua": rua, "ligacao": lig,
                          "antes": None if forcar or not pano0 else {"pano_id": pano0, "heading": head0}})
    # SEM REGISTRO A ATE 60 M: a foto e tirada no hidrometro (15/09/2026) — a ligacao nao vai mais sem imagem
    cur.execute("""select num_ligacao::text from resources_root.cadastro_corsan c
                    where num_ligacao::text = any(%s) and not exists (select 1 from radar_comercial.ligacao_poi lp
                          where lp.ligacao = c.num_ligacao::text and lp.descartado_em is null)""", (ligs,))
    sem_poi += [r[0] for r in cur.fetchall()]
    hidro = 0
    if sem_poi:
        cur.execute("""select c.num_ligacao::text, c.id_empresa::text, c.cod_latitude::float, c.cod_longitude::float,
                              c.nom_logradouro,
                              e.capturado_em >= %s and not %s, case when e.capturado_em >= %s then e.pano_id end, e.heading
                         from resources_root.cadastro_corsan c
                         left join radar_comercial.ligacao_evidencia e
                           on e.ligacao = c.num_ligacao::text and e.tipo = 'sv_frente' and e.dados is not null
                        where c.num_ligacao = any(%s::bigint[])""",
                    (CORRECAO_DA_MIRA, forcar, FORMATO_DA_FOTO, [int(x) for x in set(sem_poi) if x.isdigit()]))
        for lig, emp, la, lo, rua, ja_tem, pano0, head0 in cur.fetchall():
            if ja_tem:
                ja += 1
            elif la is not None and lo is not None:
                hidro += 1
                alvos.append({"poi": None, "ligacao": lig, "id_empresa": emp, "lat": la, "lng": lo, "rua": rua,
                              "antes": None if forcar or not pano0 else {"pano_id": pano0, "heading": head0}})
    con.close()
    print("   %d ligacoes · %d ja com a foto de rua nova · %d a capturar (%d descentradas refeitas, %d no hidrometro)"
          % (len(ligs), ja, len(alvos), descentradas, hidro), flush=True)
    return alvos


def gravar_da_ligacao(poco, a, img, **extra):
    """A foto de rua do hidrometro vai para `ligacao_evidencia` (0114); regrava por cima."""
    campos = ["id_empresa", "ligacao", "tipo", "dados"]
    vals = [a["id_empresa"], a["ligacao"], "sv_frente", ce.psycopg2_bin(img)]
    for k, v in extra.items():
        if v is not None:
            campos.append(k)
            vals.append(v)
    sets = ", ".join("%s = excluded.%s" % (c, c) for c in campos if c not in ("id_empresa", "ligacao", "tipo"))
    with poco.pegar() as con:
        with con.cursor() as k:
            k.execute("insert into radar_comercial.ligacao_evidencia (%s) values (%s) "
                      "on conflict (id_empresa, ligacao, tipo) do update set %s, storage_path = null, capturado_em = now()"
                      % (", ".join(campos), ", ".join(["%s"] * len(campos)), sets), vals)
        con.commit()


def confirmar_foto(poco, a):
    """A foto de antes da correcao que a mira nova repetiria (mesmo panorama, mesma mira) VALE COMO CAPTURADA
    AGORA: sai da fila da recaptura e da conferencia. A leitura das placas, se ja era desta foto, continua
    valendo — a imagem e a mesma."""
    tabela, filtro, vals = (("poi_evidencia", "poi_id = %s", [a["poi"]]) if a.get("poi") is not None else
                            ("ligacao_evidencia", "ligacao = %s and id_empresa::text = %s", [a["ligacao"], a["id_empresa"]]))
    with poco.pegar() as con:
        with con.cursor() as k:
            k.execute("""update radar_comercial.%s
                            set capturado_em = now(),
                                leitura_em = case when leitura is not null and leitura_em >= capturado_em
                                                  then now() else leitura_em end
                          where %s and tipo = 'sv_frente'""" % (tabela, filtro), vals)
        con.commit()


async def uma(page, poco, a):
    esc = await asyncio.to_thread(vp.escolher, a["lat"], a["lng"], a.get("rua"))
    if esc.get("erro"):
        return {"erro": esc["erro"]}
    p = esc["pano"]
    antes = a.get("antes")
    if antes and antes.get("pano_id") == p["pano_id"] and antes.get("heading") is not None \
            and vp.dif(float(antes["heading"]), esc["heading"]) <= MIRA_IGUAL_GRAUS:
        await asyncio.to_thread(confirmar_foto, poco, a)
        return {"metodo": esc["metodo"], "igual": True, "data": p.get("data")}
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
    arr = planta(arr, (p["lat"], p["lng"]), esc["heading"], vp.FOV, (a["lat"], a["lng"]), esc.get("pe"))
    ok_png, buf = cv2.imencode(".png", arr)
    if not ok_png:
        return {"erro": "png", "metodo": esc["metodo"]}
    img = ce._para_webp(buf.tobytes())
    data = p.get("data") or await asyncio.to_thread(ce._data_do_pano, p["pano_id"])
    if a.get("poi") is None:
        await asyncio.to_thread(gravar_da_ligacao, poco, a, img, pano_id=p["pano_id"], cam_lat=p["lat"], cam_lng=p["lng"],
                                heading=float(esc["heading"]), fov=float(vp.FOV), distancia_m=float(esc["dist"]),
                                largura_px=int(arr.shape[1]), altura_px=int(arr.shape[0]), data_imagem=data,
                                mira_x=float(x) if x is not None else None)
        return {"metodo": esc["metodo"], "data": data, "dist": round(esc["dist"], 1), "kb": len(img) // 1024,
                "px": [int(arr.shape[1]), int(arr.shape[0])], "via": esc.get("via"), "hidrometro": True,
                "por_que_fallback": esc.get("por_que_fallback")}
    await asyncio.to_thread(ce.gravar, poco, a["poi"], "sv_frente", img,
                            pano_id=p["pano_id"], cam_lat=p["lat"], cam_lng=p["lng"], heading=float(esc["heading"]),
                            pitch=5.0, fov=float(vp.FOV), distancia_m=float(esc["dist"]),
                            largura_px=int(arr.shape[1]), altura_px=int(arr.shape[0]), data_imagem=data,
                            mira_x=float(x) if x is not None else None, mira_rumo=float(esc["rumo_alvo"]))
    return {"metodo": esc["metodo"], "data": data, "dist": round(esc["dist"], 1), "kb": len(img) // 1024,
            "px": [int(arr.shape[1]), int(arr.shape[0])], "via": esc.get("via"),
            "por_que_fallback": esc.get("por_que_fallback")}


async def main(a):
    alvos = alvos_das_ligacoes(a.ligacoes_arquivo, a.forcar) if a.ligacoes_arquivo else json.load(open(a.alvos, encoding="utf-8"))["sv"]
    feitos_antes = _ja_feitos(a.retomar)
    fila_l = [x for x in alvos if x["poi"] not in feitos_antes]
    # UM NAVEGADOR POR PROCESSO, e varios processos: o processo grafico do Chromium
    # (render por software no Xvfb) e um so por navegador e trava em 100% de um
    # nucleo. 12 abas num navegador renderam o mesmo que 8 (36 x 38 POIs/min).
    if a.parte:
        k, n = (int(x) for x in a.parte.split("/"))
        fila_l = [x for x in fila_l if (x["poi"] if x["poi"] is not None else int(x["ligacao"])) % n == k]
    if a.limite:
        fila_l = fila_l[:a.limite]
    print("▶ foto de rua de frente: %d POIs nos alvos, %d ja feitos, %d a capturar, %d abas"
          % (len(alvos), len(feitos_antes), len(fila_l), a.abas), flush=True)
    os.makedirs(a.saida, exist_ok=True)
    rel = open(os.path.join(a.saida, "frente.jsonl"), "a", encoding="utf-8")
    fila = asyncio.Queue()
    for x in fila_l:
        fila.put_nowait(x)
    placar = {"ok": 0, "igual": 0, "perpendicular": 0, "mais de frente": 0, "sem_data": 0, "erro": 0}
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
                elif r.get("igual"):
                    placar["igual"] += 1
                else:
                    placar["ok"] += 1
                    placar[r["metodo"]] += 1
                    placar["sem_data"] += 0 if r.get("data") else 1
                rel.write(json.dumps({"poi": alvo["poi"], "ligacao": alvo.get("ligacao"), **r}, ensure_ascii=False,
                                     default=str) + "\n")
                rel.flush()
                n_feitos = placar["ok"] + placar["erro"] + placar["igual"]
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
    p.add_argument("--forcar", action="store_true", help="com --ligacoes-arquivo: refaz a foto mesmo que ja exista a nova")
    p.add_argument("--saida", default="/saida")
    asyncio.run(main(p.parse_args()))
