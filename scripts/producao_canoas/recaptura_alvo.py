# -*- coding: utf-8 -*-
"""A lista de POIs para recapturar a foto de rua em alta (DENSIDADE 2), por cidade.

UM POI POR LIGACAO: o mais proximo do medidor, que e de quem o dossie tira as
visadas (`dossie_ligacao.montar`, `melhor_sv`). Se ele esta a mais de 60 m, o
dossie recusa a foto dele, e captura-lo nao serve a ninguem. Quem ja tem visada
capturada desde `--desde` fica de fora: a lista e retomavel.

Uso: python recaptura_alvo.py --cidade Canoas --desde 2026-09-11 --saida /x/pois.txt
"""
import argparse
import math
import unicodedata

import base_comum as bc

RAIO_DA_FOTO_M = 60.0


def sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def metros(la1, lo1, la2, lo2):
    if None in (la1, lo1, la2, lo2):
        return 9e9
    dy = (la2 - la1) * 111320.0
    dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, dy)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cidade", required=True)
    p.add_argument("--desde", required=True)
    p.add_argument("--saida", required=True)
    a = p.parse_args()
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("set statement_timeout = '300s'")
    cur.execute("""select num_ligacao::text, cidade, cod_latitude::float8, cod_longitude::float8
                     from resources_root.cadastro_corsan
                    where qualificacao in ('SIM','SIM_COM_ANALISE_HUMANA')
                      and upper(categoria)='RESIDENCIAL' and upper(sit_ligacao)='ATIVA'""")
    quero = sem_acento(a.cidade)
    lig = {r[0]: (r[2], r[3]) for r in cur.fetchall() if sem_acento(r[1]) == quero}
    cur.execute("select ligacao, poi_id from radar_comercial.ligacao_poi where descartado_em is null")
    por_lig = {}
    for l, pid in cur.fetchall():
        if str(l) in lig:
            por_lig.setdefault(str(l), []).append(pid)
    ids = sorted({x for v in por_lig.values() for x in v})
    cur.execute("""select id, st_y(pt_geo::geometry), st_x(pt_geo::geometry)
                     from radar_comercial.pois where fundido_em is null and id = any(%s)""", (ids,))
    coord = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("""select distinct poi_id from radar_comercial.poi_evidencia
                    where tipo like 'sv_%%' and capturado_em >= %s
                      and (bytes_tam is not null or storage_path is not null)""", (a.desde,))
    ja = {r[0] for r in cur.fetchall()}
    con.close()
    escolhidos, longe, feitos = [], 0, 0
    for l, pids in por_lig.items():
        la, lo = lig[l]
        melhor = min(((pid, metros(la, lo, *coord[pid])) for pid in pids if pid in coord),
                     key=lambda x: x[1], default=None)
        if not melhor:
            continue
        if melhor[1] > RAIO_DA_FOTO_M:
            longe += 1
            continue
        if melhor[0] in ja:
            feitos += 1
            continue
        escolhidos.append(melhor[0])
    escolhidos = sorted(set(escolhidos))
    with open(a.saida, "w") as f:
        f.write("\n".join(str(x) for x in escolhidos) + "\n")
    print("ligacoes do alvo com POI: %d · POI mais proximo a mais de %.0f m: %d · ja recapturadas: %d · "
          "POIs para recapturar: %d" % (len(por_lig), RAIO_DA_FOTO_M, longe, feitos, len(escolhidos)))


if __name__ == "__main__":
    main()
