# -*- coding: utf-8 -*-
"""Rebaixa as fotos do Maps em tamanho util, trocando o sufixo da URL.

O QUE ESTAVA ERRADO. A coleta guardava o `src` da miniatura da grade, e o
sufixo de tamanho vinha junto: `=w156-h114-p-k-no`. Medido em 07/09/2026:
37.321 das 55.638 fotos do estabelecimento tinham menos de 15 KB — media de
6 KB, cerca de 156x114 px. Nessa resolucao a IA nao le letreiro, nao ve
mercadoria e nao identifica servico; a foto existia e nao servia para nada.

O CONSERTO E DE GRACA. A mesma URL entrega a foto grande quando o sufixo e
TROCADO — nao acrescentado, que foi o erro que me fez concluir que nao dava:
appendar um segundo `=w800-h600` faz o Google devolver 400, e eu li isso como
"nao aceita redimensionar".

    =w156-h114-p-k-no    ->    9 KB    156x114
    =w1280-h920-p-k-no   ->  254 KB   1280x920

Sem navegador, sem proxy, sem reabrir a ficha do Maps.
"""
import argparse
import concurrent.futures
import io
import re
import sys
import threading
import time
import urllib.request

sys.path.insert(0, "/app")
import base_comum as bc                                        # noqa: E402
import imagens                                                 # noqa: E402

#: O sufixo de tamanho no fim da URL do googleusercontent. As variantes vistas
#: em 07/09/2026: `-p-k-no` e `-k-no`. O grupo final e opcional porque nem toda
#: URL traz os modificadores.
SUFIXO = re.compile(r"=w\d+-h\d+(-[a-z0-9-]+)?$")
ALVO = "=w1280-h920-p-k-no"
UA = {"User-Agent": "Mozilla/5.0"}


def _log(m):
    print(m, flush=True)


def url_grande(url):
    if not url:
        return None
    if SUFIXO.search(url):
        return SUFIXO.sub(ALVO, url)
    return url + ALVO


def uma(linha, aplicar, poco, trava, placar):
    img_id, poi_id, url, caminho, tam_antes = linha
    grande = url_grande(url)
    try:
        b = urllib.request.urlopen(
            urllib.request.Request(grande, headers=UA), timeout=30).read()
    except Exception:                                          # noqa: BLE001
        with trava:
            placar["falhou"] += 1
        return
    if len(b) <= (tam_antes or 0):
        # NAO PIORA O QUE JA ESTA LA. Se a "grande" veio menor, a original ja
        # era a maior disponivel — trocar seria perder pixel.
        with trava:
            placar["ja_era_maior"] += 1
        return

    # WEBP NO MESMO TAMANHO. Mesma quantidade de pixels, menos bytes: os 254 KB
    # de JPEG caem para perto de 150 KB sem a IA perder nada.
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(b)).convert("RGB")
        larg, alt = im.size
        s = io.BytesIO()
        im.save(s, "WEBP", quality=85, method=4)
        if s.tell() < len(b):
            b = s.getvalue()
    except Exception:                                          # noqa: BLE001
        larg = alt = None

    with trava:
        placar["baixadas"] += 1
        placar["bytes"] += len(b)
    if not aplicar:
        return
    destino = caminho or ("maps_foto/%d/%d.webp" % (poi_id // 1000, img_id))
    if not imagens.enviar(destino, b, "image/webp"):
        with trava:
            placar["storage_falhou"] += 1
        return
    with poco.pegar() as c:
        with c.cursor() as k:
            k.execute("""update radar_comercial.images_urls
                            set url = %s, storage_path = %s, bytes_tam = %s
                          where id = %s""",
                      (grande, destino, len(b), img_id))
        c.commit()
    with trava:
        placar["gravadas"] += 1


class Poco:
    def __init__(self, n):
        import queue
        self.fila = queue.Queue()
        for _ in range(n):
            self.fila.put(bc.conectar())

    def pegar(self):
        import contextlib

        @contextlib.contextmanager
        def _e():
            c = self.fila.get()
            if getattr(c, "closed", 0):
                c = bc.conectar()
            try:
                yield c
            finally:
                try:
                    c.rollback()
                except Exception:                              # noqa: BLE001
                    try:
                        c.close()
                    except Exception:                          # noqa: BLE001
                        pass
                    c = None
                self.fila.put(c if c is not None else bc.conectar())
        return _e()


def rodar(limite, trabalhadores, aplicar):
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select id, poi_id, url, storage_path, bytes_tam
          from radar_comercial.images_urls
         where url like '%%gps-cs-s%%'
         order by poi_id, ordem""")
    linhas = cur.fetchall()
    con.close()
    if limite:
        linhas = linhas[:limite]
    _log("▶ %d foto(s) de estabelecimento a rebaixar em 1280 px" % len(linhas))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")

    poco = Poco(4)
    trava = threading.Lock()
    placar = {"baixadas": 0, "gravadas": 0, "falhou": 0, "ja_era_maior": 0,
              "storage_falhou": 0, "bytes": 0}
    t0 = time.time()

    def obreiro(bloco):
        for l in bloco:
            try:
                uma(l, aplicar, poco, trava, placar)
            except Exception:                                  # noqa: BLE001
                with trava:
                    placar["falhou"] += 1
            with trava:
                n = placar["baixadas"] + placar["falhou"] + placar["ja_era_maior"]
            if n % 500 == 0:
                _log("   %d/%d · %.0f/s · %.2f GB"
                     % (n, len(linhas), n / max(time.time() - t0, 1),
                        placar["bytes"] / 1073741824.0))

    blocos = [linhas[i::trabalhadores] for i in range(trabalhadores)]
    with concurrent.futures.ThreadPoolExecutor(trabalhadores) as p:
        list(p.map(obreiro, blocos))

    _log("■ baixadas %d · gravadas %d · ja eram maiores %d · falharam %d"
         " · Storage falhou %d · %.2f GB · %.1f min"
         % (placar["baixadas"], placar["gravadas"], placar["ja_era_maior"],
            placar["falhou"], placar["storage_falhou"],
            placar["bytes"] / 1073741824.0, (time.time() - t0) / 60.0))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=10)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    rodar(a.limite, a.trabalhadores, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
