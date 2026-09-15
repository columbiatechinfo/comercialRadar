# -*- coding: utf-8 -*-
"""fila_rejulgamento.py — a ordem do rejulgamento de todos os vereditos (dono do produto, 15/09/2026).

"Primeiro as atualmente aprovadas, depois as que tem iFood em qualquer que seja o status da IA, depois as que tem
Google Maps POI seja em qual status e em seguida em qualquer ordem mas executando todas [...] seguindo a ordem de
SIM e depois o com avaliacao." Entram as ligacoes julgadas no processo leve (etapa C) e as do processo de 13/09 —
estas nao entravam no rejulgamento e 215 das 216 reprovadas com iFood vinham delas.

So SIM e SIM_COM_ANALISE_HUMANA (a regra basica da base). Uma ligacao por linha, na ordem; o orquestrador parte em
lotes.

    python fila_rejulgamento.py --etapa-c /o/C_ligacoes.txt --saida /o/R_fila.txt
"""
import argparse
import collections
import sys

sys.path.insert(0, "/app")
import base_comum as bc  # noqa: E402

GRUPOS = ("1 aprovadas hoje", "2 com iFood", "3 com Google Maps", "4 o resto")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--etapa-c", dest="etapa_c", required=True)
    p.add_argument("--saida", required=True)
    a = p.parse_args(argv)
    ligs = {x.strip() for x in open(a.etapa_c) if x.strip()}
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select ligacao, veredito from radar_comercial.ligacao_veredito
                    where percepcao::jsonb->>'processo' like 'enxuto de 13/09/2026%%'
                       or percepcao::jsonb->>'processo' like 'enxuto de 15/09/2026%%'""")
    veredito = {}
    for lig, v in cur.fetchall():
        veredito[lig] = v
        ligs.add(lig)
    lista = sorted(x for x in ligs if x.isdigit())
    cur.execute("""select num_ligacao::text, qualificacao from resources_root.cadastro_corsan
                    where num_ligacao = any(%s::bigint[])""", ([int(x) for x in lista],))
    qual = dict(cur.fetchall())
    cur.execute("""select ligacao, array_agg(distinct fonte_poi) from radar_comercial.ligacao_poi
                    where ligacao = any(%s) and descartado_em is null group by 1""", (lista,))
    fontes = {l: set(fs or []) for l, fs in cur.fetchall()}
    con.close()
    cont = collections.Counter()
    fila = []
    for lig in lista:
        q = (qual.get(lig) or "").upper()
        if q not in ("SIM", "SIM_COM_ANALISE_HUMANA"):
            cont["fora: qualificação %s" % (q or "vazia")] += 1
            continue
        fs = fontes.get(lig, set())
        g = (0 if veredito.get(lig) == "aprovado" else 1 if "ifood" in fs else 2 if "maps" in fs else 3)
        fila.append((g, 0 if q == "SIM" else 1, lig))
        cont["%s · %s" % (GRUPOS[g], "SIM" if q == "SIM" else "SIM_COM")] += 1
    fila.sort()
    open(a.saida, "w").write("".join("%s\n" % lig for _g, _q, lig in fila))
    for k, n in sorted(cont.items()):
        print("   %6d  %s" % (n, k))
    print("■ fila do rejulgamento: %d ligações em %s" % (len(fila), a.saida), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
