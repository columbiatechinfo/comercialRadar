# -*- coding: utf-8 -*-
"""Leva para o Storage o que hoje e so caminho em disco ou link de terceiro.

DUAS DIVIDAS, E DE NATUREZAS DIFERENTES.

TILE — `tile_captura.storage_path` esta preenchido nos 51.504 registros e NENHUM
byte esta no Storage: o bucket so passou a existir em 06/09/2026 e a chave de
servico nao estava no conteiner, entao todo envio falhava em silencio enquanto o
banco gravava o caminho assim mesmo. E uma promessa vazia — pior que campo nulo,
porque quem le acredita. Os arquivos estao no disco (50.880 dos 51.504), e o
conserto e torna-los verdade.

  O TILE SOBE EM TAMANHO CHEIO, e isto nao e descuido: `telhados.py` segmenta
  construcao em cima dele, e geometria precisa de pixel. A reducao para 1024 px
  vale para a fachada, que a IA le, e nao para o tile, que o codigo mede.

FOTO DO MAPS — as 71.534 linhas de `images_urls` guardam so a URL do Google.
Funciona hoje (testadas 5 de 5 vivas), mas e link de terceiro: quando expirar, a
IA perde a evidencia e nao ha como saber que perdeu. Estas sim passam pelo
`padronizar` — 1024 px em WebP, que e o que a IA consome.

USO:
    python3 imagens_para_storage.py --tiles [--limite N]
    python3 imagens_para_storage.py --fotos [--limite N]
"""
import argparse
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/app")
import base_comum as bc                              # noqa: E402
import imagens                                       # noqa: E402


def _log(m):
    print(m, flush=True)


def caminho_no_disco(sp):
    return sp if sp.startswith("/") else os.path.join("/app", sp)


def subir_tiles(limite=0):
    """O TILE NAO SOBE MAIS PARA O STORAGE.

    # O TILE E RASCUNHO, E NAO ACERVO.
#
# Decisao do dono do produto em 07/09/2026: o tile e recapturado toda vez que a
# area roda, entao guarda-lo nao poupa nada — so ocupa disco e Storage. Sao
# 50.880 arquivos e 1,1 GB em `capturas/`, mais 51.504 linhas de catalogo.
#
# O QUE ELE PRECISA RESPONDER e "que tile cobre este ponto?", e para isso basta
# o disco: o nome do arquivo carrega o centro
# (`tile_r_008_-29.91725_-51.19778.webp`) e o `_tiles.json` ao lado guarda a
# caixa que o proprio mapa reportou ter desenhado. `achar_tiles_no_disco` ja
# lia tudo isso — a tabela era uma copia do que o diretorio ja sabia.
    #
    # A funcao fica, e avisa: o pipeline e um script antigo podem chamar
    # `--tiles`, e um erro de atributo diria "nao existe" quando a resposta
    # certa e "nao se faz mais, e por este motivo".
    """
    _log("   os tiles não vão mais para o Storage — são rascunho da rodada,")
    _log("   vivem em disco e `telhados.py --limpar` os apaga no fim.")
    return


#: Quantas fotos em voo ao mesmo tempo.
#:
#: O passo aqui e ESPERA DE REDE, e nao trabalho: baixar do CDN do Google,
#: reduzir e subir para o Storage. Em serie deu 1,3 foto/s — 15 h para as
#: 71.514 —, com a maquina parada esperando resposta. Doze em voo cabem
#: folgado no que o Pillow consome e nao enchem o Storage local.
#:
#: VOLTOU EM 15/09/2026. O refactor dos tiles (ba9470b) apagou esta constante e
#: `_uma_foto` junto com `subir_tiles`, e `--fotos` passou a morrer com
#: NameError na primeira linha — descoberto ao subir as fotos da recaptura.
FOTOS_EM_VOO = 12


def _uma_foto(iid, url):
    """(id, caminho, bytes, tipo) ou None. Roda em thread, sem tocar no banco."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            bruto = r.read()
    except Exception:                                          # noqa: BLE001
        return None
    corpo, tipo = imagens.padronizar(bruto)
    ext = "webp" if tipo == "image/webp" else "jpg"
    caminho = "foto/%02d/%d.%s" % (iid % 100, iid, ext)
    if not imagens.enviar(caminho, corpo, tipo):
        return None
    return iid, caminho, len(corpo), tipo


def subir_fotos(limite=0):
    from concurrent.futures import ThreadPoolExecutor

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select id, poi_id, url from radar_comercial.images_urls
                    where storage_path is null and url like 'http%'
                    order by id""" + (" limit %d" % limite if limite else ""))
    linhas = cur.fetchall()
    _log("   %d foto(s) so com URL · %d em voo" % (len(linhas), FOTOS_EM_VOO))
    ok = falhou = n = 0
    t0 = time.time()
    # O BANCO FICA NA THREAD PRINCIPAL. A conexao e uma so e psycopg2 nao e
    # seguro entre threads; as threads baixam e sobem, e so o resultado volta.
    with ThreadPoolExecutor(max_workers=FOTOS_EM_VOO) as pool:
        for r in pool.map(lambda x: _uma_foto(x[0], x[2]), linhas):
            n += 1
            if r is None:
                falhou += 1
            else:
                iid, caminho, tam, tipo = r
                cur.execute("""update radar_comercial.images_urls
                                  set storage_path = %s, bytes_tam = %s,
                                      content_type = %s
                                where id = %s""", (caminho, tam, tipo, iid))
                ok += 1
            if n % 500 == 0:
                con.commit()
                _log("   %d/%d · no Storage %d · falhou %d · %.1f/s"
                     % (n, len(linhas), ok, falhou,
                        n / max(time.time() - t0, 1)))
    con.commit()
    _log("   FIM fotos · subiram %d · falharam %d · %.1f min"
         % (ok, falhou, (time.time() - t0) / 60.0))
    con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tiles", action="store_true")
    p.add_argument("--fotos", action="store_true")
    p.add_argument("--limite", type=int, default=0)
    a = p.parse_args()
    if a.tiles:
        subir_tiles(a.limite)
    if a.fotos:
        subir_fotos(a.limite)
    if not (a.tiles or a.fotos):
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
