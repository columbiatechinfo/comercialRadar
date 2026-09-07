# -*- coding: utf-8 -*-
"""As evidencias em PNG viram WebP, nos mesmos pixels.

MEDIDO em 07/09/2026: as visadas gravadas pela captura ocupam de 837 a 998 KB
cada, porque `cv2.imencode(".png")` grava sem perda e sem compressao util para
fotografia. As mesmas imagens, NOS MESMOS PIXELS, ficam perto de 52 KB em WebP
— 88% menor.

O preco era cobrado duas vezes: em disco, `poi_evidencia` virou a maior tabela
do banco com 53 GB de 104 GB; e em tempo de modelo, agora que a percepcao
recebe seis imagens e carrega 3,9 MB por chamada.

A RESOLUCAO NAO MUDA. Reduzir largura foi medido no mesmo dia e reprovado: o
modelo passou a ler letreiro errado e num caso trocou o comercio por outro que
nao existia. O que muda e so o formato.

LOTE PEQUENO E COMMIT POR LOTE: sao 53 GB de UPDATE, e interromper no meio nao
pode deixar nada quebrado — cada lote que fechou, fechou.
"""
import argparse
import io
import sys
import time

sys.path.insert(0, "/app")
import base_comum as bc                                        # noqa: E402

LOTE = 150


def _log(m):
    print(m, flush=True)


def ja_e_webp(b):
    return len(b) > 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"


def para_webp(png, qualidade=88):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    s = io.BytesIO()
    im.save(s, "WEBP", quality=qualidade, method=4)
    d = s.getvalue()
    return d if len(d) < len(png) else None


def rodar(limite, aplicar):
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select count(*), coalesce(sum(bytes_tam),0)::float8
                     from radar_comercial.poi_evidencia where dados is not null""")
    n_total, bytes_total = cur.fetchone()
    _log("▶ %d linha(s) com bytes no banco, %.1f GB"
         % (n_total, bytes_total / 1073741824.0))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")

    ultimo = 0
    feitos = ja = pulados = 0
    antes = depois = 0
    t0 = time.time()
    while True:
        cur.execute("""select id, dados from radar_comercial.poi_evidencia
                        where dados is not null and id > %s
                        order by id limit %s""", (ultimo, LOTE))
        linhas = cur.fetchall()
        if not linhas:
            break
        for rid, d in linhas:
            ultimo = rid
            b = bytes(d)
            if ja_e_webp(b):
                ja += 1
                continue
            try:
                w = para_webp(b)
            except Exception:                                  # noqa: BLE001
                w = None
            if not w:
                pulados += 1
                continue
            antes += len(b)
            depois += len(w)
            feitos += 1
            if aplicar:
                cur.execute("""update radar_comercial.poi_evidencia
                                  set dados = %s, bytes_tam = %s
                                where id = %s""",
                            (_bin(w), len(w), rid))
        if aplicar:
            con.commit()
        if feitos and (feitos // LOTE) != ((feitos - len(linhas)) // LOTE):
            _log("   %d convertidas · %d ja eram · %.1f GB -> %.1f GB · %.0f/s"
                 % (feitos, ja, antes / 1073741824.0, depois / 1073741824.0,
                    feitos / max(time.time() - t0, 1)))
        if limite and feitos >= limite:
            break

    _log("■ convertidas %d · ja eram WebP %d · puladas %d · %.2f GB -> %.2f GB"
         " (%.0f%% menor) · %.1f min"
         % (feitos, ja, pulados, antes / 1073741824.0, depois / 1073741824.0,
            100 * (1 - depois / max(antes, 1)), (time.time() - t0) / 60.0))
    con.close()


def _bin(b):
    import psycopg2
    return psycopg2.Binary(b)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    rodar(a.limite, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
