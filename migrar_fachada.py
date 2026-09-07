# -*- coding: utf-8 -*-
"""A captura antiga vira captura nova, sem abrir o Street View de novo.

A FACHADA JA MIRA NO ALVO. `streetview_capture` calculava
`heading = rumo(camera -> estabelecimento)` antes de fotografar, e gravou esse
rumo nas 83.287 linhas. Ou seja: na visada `facade` o imovel alvo esta no centro
horizontal por construcao — a mesma premissa que `capturar_evidencia` usa para
desenhar a mira. Entao adequar e trabalho local: recortar a interface do Google
e desenhar a mira. Zero proxy, zero Street View.

O QUE NAO DA PARA CRIAR: a visada de 180 graus (o fundo). A captura antiga fez
tres tomadas e a nova faz quatro. Os POIs migrados ficam com tres.
"""
import argparse
import concurrent.futures
import io
import sys
import threading
import time

sys.path.insert(0, "/app")
import base_comum as bc                                        # noqa: E402
import capturar_evidencia as ce                                # noqa: E402
import imagens                                                 # noqa: E402

#: DE QUE ANGULO ANTIGO PARA QUE TIPO NOVO.
#:
#: `g0`, `g60`, `g120`, `g240` e `g300` (407 linhas ao todo) sao de um ensaio de
#: varredura de 360 graus e nao tem lugar no padrao de quatro visadas. Ficam de
#: fora — inventar um `tipo` para elas poluiria a leitura da IA, que espera
#: exatamente frente, lado A, fundo e lado B.
DE_PARA = {"facade": "sv_frente", "g90": "sv_lado_a",
           "g270": "sv_lado_b", "g180": "sv_fundo"}

#: ONDE O BYTE VAI PARAR. Ver a migracao 0077: `poi_evidencia` guardava imagem
#: em bytea e virou a maior tabela do banco, 53 GB de 104 GB. As adequadas
#: entram pelo Storage e a linha guarda so o caminho.
def caminho_no_storage(poi_id, tipo):
    return "evidencia/%d/%d_%s.webp" % (poi_id // 1000, poi_id, tipo)


SQL = """
    select s.id, s.poi_id, s.angulo, s.storage_path, s.pano_id, s.heading,
           s.cam_lat, s.cam_lng, s.fov, s.data_captura
      from radar_comercial.streetview_imgs s
     where s.storage_path is not null
       and s.angulo = any(%s)
       and not exists (select 1 from radar_comercial.poi_evidencia e
                        where e.poi_id = s.poi_id and e.dados is not null)
     order by s.poi_id, s.angulo
"""


def _log(m):
    print(m, flush=True)


def uma(linha, aplicar, con, trava, placar):
    (sid, poi_id, angulo, caminho, pano, heading, clat, clng, fov,
     data_img) = linha
    tipo = DE_PARA[angulo]
    bruto = imagens.baixar(caminho)
    if not bruto:
        with trava:
            placar["sem_bytes"] += 1
        return
    limpo = ce._cortar_interface(bruto)
    # A MIRA SO NA FRENTE, exatamente como na captura nova: os giros de 90 e
    # 270 graus nao apontam para o alvo, e marcar o centro deles seria apontar
    # para a casa do vizinho.
    img = ce._marcar_centro(limpo, "") if tipo == "sv_frente" else limpo

    # WEBP SEM ENCOLHER. O corte ja tirou um terco dos pixels; reduzir mais
    # custaria leitura de letreiro, que foi medido em 07/09/2026 e nao vale.
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(img)).convert("RGB")
        larg, alt = im.size
        b = io.BytesIO()
        im.save(b, "WEBP", quality=90, method=4)
        img = b.getvalue()
    except Exception:                                          # noqa: BLE001
        larg = alt = None

    with trava:
        placar["prontos"] += 1
        placar["bytes"] += len(img)
    if not aplicar:
        return

    # O BYTE SOBE ANTES DA LINHA. Gravar o caminho de um objeto que nao subiu
    # seria a mesma promessa vazia que os tiles fizeram: quem le acredita.
    destino = caminho_no_storage(poi_id, tipo)
    if not imagens.enviar(destino, img, "image/webp"):
        with trava:
            placar["storage_falhou"] += 1
        return

    with con.pegar() as c:
        with c.cursor() as cur:
            cur.execute("""
                insert into radar_comercial.poi_evidencia
                       (poi_id, tipo, storage_path, data_imagem, pano_id,
                        cam_lat, cam_lng, heading, fov, largura_px, altura_px,
                        bytes_tam, capturado_em)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                on conflict (id_empresa, poi_id, tipo) do nothing
            """, (poi_id, tipo, destino, data_img, pano, clat, clng,
                  heading, fov, larg, alt, len(img)))
        c.commit()
    with trava:
        placar["gravados"] += 1


class Poco:
    """Conexoes emprestadas por gravacao — o mesmo desenho do resto do sistema."""

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
    con0 = bc.conectar()
    cur = con0.cursor()
    cur.execute(SQL, (list(DE_PARA),))
    linhas = cur.fetchall()
    con0.close()
    if limite:
        linhas = linhas[:limite]
    _log("▶ %d imagem(ns) de %d POI(s) a adequar"
         % (len(linhas), len({r[1] for r in linhas})))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")

    poco = Poco(4)
    trava = threading.Lock()
    placar = {"prontos": 0, "gravados": 0, "sem_bytes": 0,
              "storage_falhou": 0, "bytes": 0}
    t0 = time.time()

    def obreiro(bloco):
        for l in bloco:
            try:
                uma(l, aplicar, poco, trava, placar)
            except Exception as e:                             # noqa: BLE001
                with trava:
                    placar["sem_bytes"] += 1
                    if placar["sem_bytes"] < 6:
                        _log("   %s FALHOU %s: %s"
                             % (l[1], type(e).__name__, str(e)[:70]))
            with trava:
                n = placar["prontos"]
            if n and n % 500 == 0:
                _log("   %d/%d · %.0f img/s · %.2f GB"
                     % (n, len(linhas), n / max(time.time() - t0, 1),
                        placar["bytes"] / 1073741824.0))

    blocos = [linhas[i::trabalhadores] for i in range(trabalhadores)]
    with concurrent.futures.ThreadPoolExecutor(trabalhadores) as p:
        list(p.map(obreiro, blocos))

    _log("■ prontas %d · gravadas %d · sem bytes %d · Storage falhou %d"
         " · %.2f GB · %.1f min"
         % (placar["prontos"], placar["gravados"], placar["sem_bytes"],
            placar["storage_falhou"], placar["bytes"] / 1073741824.0,
            (time.time() - t0) / 60.0))
    return placar


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
