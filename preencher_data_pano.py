# -*- coding: utf-8 -*-
"""Preenche `poi_evidencia.data_imagem` nas linhas que ficaram sem.

UMA CHAMADA POR PANORAMA, e nao por linha. As quatro visadas de um POI saem do
MESMO panorama, entao a data e a mesma; e panorama se repete entre POIs vizinhos
da mesma rua. Sem agrupar, seriam 63 mil chamadas para algo que cabe em muito
menos.

A API de metadados do Street View nao e cobrada pelo Google — metadata request
e de graca, ao contrario da imagem.
"""
import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
import base_comum as bc                                        # noqa: E402

# A CHAVE TEM TRES NOMES POSSIVEIS neste projeto e o container usa o
# terceiro: `MAPS_JS_KEY`. Procurar so os dois primeiros fazia a funcao
# devolver None em silencio, que e o pior jeito de falhar — a data
# simplesmente nao aparecia e nada dizia por que.
CHAVE = (os.environ.get("MAPS_API_KEY") or os.environ.get("MAPS_KEY")
         or os.environ.get("MAPS_JS_KEY") or "")


def _log(m):
    print(m, flush=True)


def data_do_pano(pano):
    url = ("https://maps.googleapis.com/maps/api/streetview/metadata?"
           + urllib.parse.urlencode({"pano": pano, "key": CHAVE}))
    try:
        md = json.loads(urllib.request.urlopen(url, timeout=12).read())
        return md.get("date") if md.get("status") == "OK" else None
    except Exception:                                          # noqa: BLE001
        return None


def rodar(limite, trabalhadores, aplicar):
    if not CHAVE:
        _log("   sem MAPS_API_KEY no ambiente — nada a fazer")
        return
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select pano_id, count(*)
          from radar_comercial.poi_evidencia
         where pano_id is not null and data_imagem is null
         group by 1 order by 2 desc""")
    panos = cur.fetchall()
    if limite:
        panos = panos[:limite]
    linhas = sum(n for _, n in panos)
    _log("▶ %d panorama(s) distinto(s) cobrindo %d linha(s) sem data"
         % (len(panos), linhas))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")

    trava = threading.Lock()
    placar = {"com_data": 0, "sem_data": 0, "linhas": 0}
    t0 = time.time()

    def obreiro(bloco):
        c = bc.conectar()
        try:
            for pano, quantas in bloco:
                d = data_do_pano(pano)
                with trava:
                    placar["com_data" if d else "sem_data"] += 1
                if d and aplicar:
                    with c.cursor() as k:
                        k.execute("""update radar_comercial.poi_evidencia
                                        set data_imagem = %s
                                      where pano_id = %s and data_imagem is null""",
                                  (d, pano))
                        n = k.rowcount
                    c.commit()
                    with trava:
                        placar["linhas"] += n
                with trava:
                    feitos = placar["com_data"] + placar["sem_data"]
                if feitos % 400 == 0:
                    _log("   %d/%d panoramas · %d linhas · %.0f/s"
                         % (feitos, len(panos), placar["linhas"],
                            feitos / max(time.time() - t0, 1)))
        finally:
            c.close()

    blocos = [panos[i::trabalhadores] for i in range(trabalhadores)]
    with concurrent.futures.ThreadPoolExecutor(trabalhadores) as p:
        list(p.map(obreiro, blocos))

    _log("■ panoramas com data %d · sem data %d · linhas preenchidas %d · %.1f min"
         % (placar["com_data"], placar["sem_data"], placar["linhas"],
            (time.time() - t0) / 60.0))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=8)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    rodar(a.limite, a.trabalhadores, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
