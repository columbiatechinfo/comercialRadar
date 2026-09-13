# -*- coding: utf-8 -*-
"""corrigir_frente.py — a mira na coordenada do POI, nas visadas que ja existem.

Regra do dono do produto (13/09/2026): "a mira deve ser colocada na coordenada
do POI, simples assim". Para cada POI com as quatro visadas de rua:

  1. o rumo e o da camera ate a coordenada ATUAL do POI;
  2. a visada em que esse rumo aparece e a frente (`frente_da_rua.escolher`);
  3. a mira antiga, desenhada no centro ate 10/09/2026, sai quando nao coincide
     com o rumo (coordenada corrigida depois da captura); onde ela ja coincide,
     a imagem fica como esta;
  4. a mira nova vai no pixel do rumo; se a visada certa nao for a `sv_frente`,
     as quatro giram de rotulo junto.

SEM RECAPTURA: as quatro visadas ja cobrem a volta inteira. SEM --aplicar NAO
GRAVA NADA: salva antes/depois em JPEG para conferir. Com --aplicar, a imagem
anterior vai para `poi_evidencia_antes_mira` antes de ser trocada.

    python corrigir_frente.py --pois 120858 --saida /saida
    python corrigir_frente.py --todos --trabalhadores 4 --aplicar
"""
import argparse
import json
import os
import queue
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, "/app")
import base_comum as bc  # noqa: E402
import imagens  # noqa: E402
import frente_da_rua as fr  # noqa: E402

ORDEM = ["sv_frente", "sv_lado_a", "sv_fundo", "sv_lado_b"]
CORTE_MIRA = "2026-09-10 11:19-03"
#: a mira antiga ficou no meio da imagem; o rumo da camera cai em x = 0,479
TOLERANCIA_MIRA_ANTIGA = 0.03


def carregar(cur, poi):
    cur.execute("""select st_y(pt_geo::geometry), st_x(pt_geo::geometry) from radar_comercial.pois
                    where id = %s and pt_geo is not null""", (poi,))
    p = cur.fetchone()
    cur.execute("""select id, id_empresa, tipo, heading, fov, cam_lat, cam_lng, capturado_em < %s
                     from radar_comercial.poi_evidencia
                    where poi_id = %s and tipo like 'sv_%%' and (bytes_tam is not null or storage_path is not null)""",
                (CORTE_MIRA, poi))
    vis = {t: dict(id=i, emp=e, heading=h, fov=f, cla=cla, clo=clo, antiga=ant)
           for i, e, t, h, f, cla, clo, ant in cur.fetchall()}
    return p, vis


def _img(con, poi, tipo):
    b = imagens._buscar(con, "poi_evidencia", "poi_id = %s and tipo = %s", (poi, tipo), 1)
    return cv2.imdecode(np.frombuffer(b[0], np.uint8), cv2.IMREAD_COLOR) if b else None


def _webp(arr):
    ok, buf = cv2.imencode(".webp", arr, [cv2.IMWRITE_WEBP_QUALITY, 93])
    return buf.tobytes() if ok else None


def processar(con, poi, saida=None, aplicar=False):
    cur = con.cursor()
    p, vis = carregar(cur, poi)
    if not p or len(vis) < 4 or "sv_frente" not in vis or any(t not in vis for t in ORDEM):
        return {"poi": poi, "acao": "sem_as_quatro"}
    f0 = vis["sv_frente"]
    rumo = fr.rumo(f0["cla"], f0["clo"], p[0], p[1])
    esc = fr.escolher([(t, v["heading"], v["fov"]) for t, v in vis.items()], rumo)
    if not esc:
        return {"poi": poi, "acao": "fora_das_visadas", "rumo": round(rumo, 1)}
    alvo, x, borda = esc
    out = {"poi": poi, "rumo": round(rumo, 1), "visada": alvo, "mira_x": round(x, 3), "borda": borda,
           "mira_antiga": bool(f0["antiga"])}
    if f0["antiga"] and alvo == "sv_frente" and abs(x - 0.5) <= TOLERANCIA_MIRA_ANTIGA:
        out["acao"] = "mira_antiga_ja_na_coordenada"
        if aplicar:
            cur.execute("""update radar_comercial.poi_evidencia set mira_x = 0.5, mira_rumo = %s
                            where id = %s""", (rumo, f0["id"]))
            con.commit()
        return out
    novas = {}
    if f0["antiga"]:
        novas["sv_frente"] = fr.apagar_mira_antiga(_img(con, poi, "sv_frente"))
    base = novas.get(alvo)
    if base is None:
        base = _img(con, poi, alvo)
    novas[alvo] = fr.desenhar_mira(base.copy(), x)
    h0 = vis[alvo]["heading"]
    rotulo = {t: ORDEM[int(round(((v["heading"] - h0) % 360) / 90.0)) % 4] for t, v in vis.items()}
    out["acao"] = "mira_desenhada" + ("" if alvo == "sv_frente" else " · rotulos giram")
    if saida:
        for t, arr in novas.items():
            antes = _img(con, poi, t)
            for nome, im in (("antes", antes), ("depois", arr)):
                cv2.imwrite(os.path.join(saida, "%d_%s_%s.jpg" % (poi, nome, t)),
                            cv2.resize(im, (467, int(im.shape[0] * 467 / im.shape[1]))),
                            [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not aplicar:
        return out
    try:
        for t, arr in novas.items():
            v = vis[t]
            cur.execute("""insert into radar_comercial.poi_evidencia_antes_mira
                               (evidencia_id, id_empresa, poi_id, tipo, storage_path, dados, bytes_tam, heading)
                           select id, id_empresa, poi_id, tipo, storage_path,
                                  case when storage_path is null then dados end, bytes_tam, heading
                             from radar_comercial.poi_evidencia where id = %s
                           on conflict (evidencia_id) do nothing""", (v["id"],))
            b = _webp(arr)
            cur.execute("""update radar_comercial.poi_evidencia
                              set dados = %s, bytes_tam = %s, storage_path = null
                            where id = %s""", (b, len(b), v["id"]))
        cur.execute("""update radar_comercial.poi_evidencia set mira_x = null, mira_rumo = null
                        where poi_id = %s and tipo like 'sv_%%'""", (poi,))
        cur.execute("update radar_comercial.poi_evidencia set mira_x = %s, mira_rumo = %s where id = %s",
                    (x, rumo, vis[alvo]["id"]))
        if alvo != "sv_frente":
            # troca de rotulo em dois tempos: a chave unica e (empresa, poi, tipo)
            for t, v in vis.items():
                cur.execute("update radar_comercial.poi_evidencia set tipo = %s where id = %s",
                            ("tmp_%d" % v["id"], v["id"]))
            for t, v in vis.items():
                cur.execute("update radar_comercial.poi_evidencia set tipo = %s where id = %s",
                            (rotulo[t], v["id"]))
        con.commit()
    except Exception as e:                                     # noqa: BLE001
        con.rollback()
        out["acao"] = "falhou"
        out["erro"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pois", default="")
    ap.add_argument("--todos", action="store_true")
    ap.add_argument("--saida", default="")
    ap.add_argument("--trabalhadores", type=int, default=1)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    con = bc.conectar()
    cur = con.cursor()
    pois = [int(x) for x in a.pois.split(",") if x.strip()]
    if a.todos:
        cur.execute("""select distinct poi_id from radar_comercial.poi_evidencia
                        where tipo = 'sv_frente' and (bytes_tam is not null or storage_path is not null)
                          and mira_rumo is null order by poi_id""")
        pois += [r[0] for r in cur.fetchall()]
    con.close()
    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
    print("%s POIs · %s" % (len(pois), "APLICANDO" if a.aplicar else "sem gravar"), flush=True)
    fila = queue.Queue()
    for poi in pois:
        fila.put(poi)
    placar, trava, feitos, t0 = {}, threading.Lock(), [0], time.time()

    def trabalhador():
        c = bc.conectar()
        while True:
            try:
                poi = fila.get_nowait()
            except queue.Empty:
                break
            try:
                r = processar(c, poi, a.saida or None, a.aplicar)
            except Exception as e:                             # noqa: BLE001
                try:
                    c.rollback()
                except Exception:                              # noqa: BLE001
                    c = bc.conectar()
                r = {"poi": poi, "acao": "falhou", "erro": "%s: %s" % (type(e).__name__, str(e)[:160])}
            with trava:
                chave = r["acao"].split(" · ")[0] + (" · rotulos giram" if "giram" in r["acao"] else "")
                placar[chave] = placar.get(chave, 0) + 1
                feitos[0] += 1
                if len(pois) <= 30 or r["acao"] == "falhou":
                    print(json.dumps(r, ensure_ascii=False), flush=True)
                if feitos[0] % 1000 == 0:
                    print("%s %d/%d · %.0f/min · %s" % (time.strftime("%H:%M:%S"), feitos[0], len(pois),
                          feitos[0] / max(1e-9, time.time() - t0) * 60, placar), flush=True)
        c.close()
    ths = [threading.Thread(target=trabalhador) for _ in range(max(1, a.trabalhadores))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    print("FIM %d POIs em %.1f min · %s" % (len(pois), (time.time() - t0) / 60, placar), flush=True)


if __name__ == "__main__":
    main()
