# -*- coding: utf-8 -*-
"""ler_fotos_de_rua.py — a leitura da foto de rua de frente, uma imagem e uma tarefa (dono do produto, 15/09/2026).

A IA que julga, com o cadastro, os registros e as fichas na frente, deixava passar placa e letreiro visiveis:
no lote 1 do julgamento v3, 0 de 30 fotos "com sinal", e a galeria mostrava pet shop, "KALIVAS", "CAFE" e
parede pintada de grafica. Uma chamada so com a foto, pedindo para TRANSCREVER o que esta escrito e listar os
sinais sem texto, achou 5 das 30. A leitura fica gravada junto da foto (`leitura`, 0115) e vai em texto para o
julgamento — rejulgar nao le de novo; a foto recapturada e lida outra vez.

    python ler_fotos_de_rua.py --ligacoes-arquivo ligs.txt --simultaneas 60
"""
import argparse
import base64
import concurrent.futures as cf
import json
import sys
import threading
import time

sys.path.insert(0, "/app")
import avaliar_enxuto as ae  # noqa: E402
import avaliar_ligacao as al  # noqa: E402
import base_comum as bc  # noqa: E402
import descrever_imagens as di  # noqa: E402

LARGURA = 1088
PROMPT = """Esta é uma foto de rua (Street View). A seta verde aponta o imóvel investigado; a coordenada pode ter alguns metros de erro, então considere também os imóveis colados à ponta da seta. O quadro escuro no canto inferior direito é uma planta vista de cima (rua, câmera, cone da visada e o ponto da seta): não é parte do lugar, não liste nada dele.

Examine a FOTO INTEIRA, com atenção a detalhes pequenos, e liste o texto escrito que aparece: placas, letreiros, faixas, banners, adesivos, anúncios pintados em parede ou muro, toldos, telefones, nomes comerciais, placas de aluga/vende. No máximo 15 textos, os mais importantes; texto longo, só o essencial em até 8 palavras. Não liste marca d'água do Google, nome de rua nem a distância da seta.

Liste também sinais de atividade não residencial SEM texto: vitrine com mercadoria, porta de loja aberta, balcão, mesas de bar, oficina com carros ou peças em serviço, pátio com caminhões ou máquinas, material à venda, sucata, marcador de estabelecimento do Google sobre o imóvel. Portão de garagem de casa e porta fechada de casa NÃO são sinal.

Responda SOMENTE um JSON:
{"textos": [{"texto": "<o que está escrito>", "tipo": "letreiro|placa|faixa|pintura|adesivo|toldo|aluga_vende|outro", "onde": "no_imovel_da_seta|colado_ao_imovel|longe"}],
 "sinais_sem_texto": [{"sinal": "<o que se vê>", "onde": "no_imovel_da_seta|colado_ao_imovel|longe"}],
 "uso_nao_residencial_no_imovel": true|false,
 "resumo": "<até 20 palavras>"}"""


def _log(m):
    print(m, flush=True)


def alvos(con, ligacoes):
    """[(tabela, chave, dados)]: a foto de rua de frente de cada ligacao (a do POI mais perto do hidrometro ou
    a do proprio hidrometro) ainda sem leitura, ou lida antes da captura."""
    cur = con.cursor()
    cur.execute("""select lp.ligacao, array_agg(p.id) from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                    where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                    group by 1""", (list(ligacoes),))
    pois = set()
    for lig, ids in cur.fetchall():
        e = ae.poi_da_foto_de_rua(cur, lig, ids)
        if e and e[2]:
            pois.add(e[0])
    saida = []
    if pois:
        cur.execute("""select poi_id from radar_comercial.poi_evidencia
                        where poi_id = any(%s) and tipo = 'sv_frente' and dados is not null
                          and (leitura is null or leitura_em < capturado_em)""", (sorted(pois),))
        saida += [("poi_evidencia", r[0]) for r in cur.fetchall()]
    cur.execute("""select ligacao from radar_comercial.ligacao_evidencia
                    where ligacao = any(%s) and tipo = 'sv_frente' and dados is not null
                      and (leitura is null or leitura_em < capturado_em)""", (sorted(set(ligacoes)),))
    saida += [("ligacao_evidencia", r[0]) for r in cur.fetchall()]
    con.rollback()
    return saida


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", required=True)
    p.add_argument("--simultaneas", type=int, default=60)
    a = p.parse_args(argv)
    ligs = [x.strip() for x in open(a.ligacoes_arquivo) if x.strip()]
    con = bc.conectar()
    lista = alvos(con, ligs)
    _log("▶ leitura da foto de rua: %d ligacoes · %d fotos a ler" % (len(ligs), len(lista)))
    if not lista:
        return 0
    trava = threading.Lock()
    placar = {"lida": 0, "com_uso": 0, "falha": 0}
    t0 = time.time()

    def uma(item):
        tabela, chave = item
        coluna = "poi_id" if tabela == "poi_evidencia" else "ligacao"
        with trava:
            with con.cursor() as k:
                k.execute("select dados from radar_comercial.%s where %s = %%s and tipo = 'sv_frente'" % (tabela, coluna), (chave,))
                b = k.fetchone()[0]
            con.rollback()
        img = base64.b64encode(ae._jpeg_768(b, largura=LARGURA)).decode()
        r = None
        for tentativa in (1, 2):
            try:
                r = di._chat_local(al.MODELO_PADRAO, PROMPT, [img], max_tokens=1500, timeout=600)
                break
            except Exception as e:                                  # noqa: BLE001
                r = {"erro": str(e)[:200]}
        with trava:
            if r and not r.get("erro"):
                with con.cursor() as k:
                    k.execute("update radar_comercial.%s set leitura = %%s::jsonb, leitura_em = now() where %s = %%s and tipo = 'sv_frente'"
                              % (tabela, coluna), (json.dumps(r, ensure_ascii=False), chave))
                con.commit()
                placar["lida"] += 1
                placar["com_uso"] += 1 if r.get("uso_nao_residencial_no_imovel") else 0
            else:
                placar["falha"] += 1
            n = sum(placar.values()) - placar["com_uso"]
            if n % 50 == 0 or n == len(lista):
                ritmo = n / max(1, time.time() - t0) * 60
                _log("   [%d/%d] %.1f fotos/min · %s" % (n, len(lista), ritmo, placar))

    with cf.ThreadPoolExecutor(max(1, a.simultaneas)) as ex:
        list(ex.map(uma, lista))
    con.close()
    _log("■ leitura da foto de rua pronta em %.1f min · %s" % ((time.time() - t0) / 60, placar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
