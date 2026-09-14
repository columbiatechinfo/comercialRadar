# -*- coding: utf-8 -*-
"""datas_estaduais.py — a data de atualizacao do ponto estadual, do Parquet ao banco.

A extracao estadual (`dados_externos/estadual/<UF>/saida/poi_padronizado_*.parquet`)
guarda, por ponto, `data_atualizacao` e `status` declarados pelo Overture e pelo
Foursquare. A carga no banco nao os trouxe, e desde 14/09/2026 a data da prova
entra no veredito (migracao 0112). Isto e juncao pelo `cluster_id`, como o
`origem_estadual.py` faz com a procedencia: nenhuma extracao roda de novo.

    python datas_estaduais.py --uf RS            # so conta
    python datas_estaduais.py --uf RS --aplicar  # grava

Roda na imagem do minerador (tem duckdb).
"""
import argparse
import glob
import sys

import psycopg2.extras

import base_comum as bc

PADRAO = "/app/dados_externos/estadual/%s/saida/poi_padronizado_*.parquet"
TABELAS = {"overture": "overture_data", "fsq": "foursquare_data"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uf", default="RS")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    import duckdb
    arquivos = sorted(glob.glob(PADRAO % a.uf.upper()))
    if not arquivos:
        print("sem Parquet padronizado para", a.uf)
        return 2
    d = duckdb.connect()
    con = bc.conectar()
    cur = con.cursor()
    for prefixo, tabela in TABELAS.items():
        # A DATA VEM COMO TEXTO ('2012-08-15', ou vazia): `try_cast` devolve nulo no
        # que nao for data, em vez de derrubar a leitura inteira por uma linha torta.
        linhas = d.execute(
            "select cluster_id, try_cast(nullif(trim(cast(data_atualizacao as varchar)), '') as date), "
            "nullif(trim(cast(status as varchar)), '') "
            "from read_parquet(?) where cluster_id like ?", [arquivos, prefixo + ":%"]).fetchall()
        com_data = sum(1 for _c, dt, _s in linhas if dt)
        cur.execute("select count(*), count(atualizado_na_fonte) from radar_comercial." + tabela)
        n_tab, ja = cur.fetchone()
        print("%-16s Parquet %d pontos (%d com data) · banco %d linhas (%d ja com data)"
              % (tabela, len(linhas), com_data, n_tab, ja))
        if not a.aplicar:
            continue
        gravadas = 0
        for i in range(0, len(linhas), 5000):
            psycopg2.extras.execute_values(cur, """
                update radar_comercial.""" + tabela + """ t
                   set atualizado_na_fonte = v.dt::date, status_na_fonte = v.st
                  from (values %s) as v(cluster_id, dt, st)
                 where t.cluster_id = v.cluster_id
                   and (t.atualizado_na_fonte is distinct from v.dt::date
                        or t.status_na_fonte is distinct from v.st)""",
                [(c, dt.isoformat() if dt else None, st) for c, dt, st in linhas[i:i + 5000]], page_size=1000)
            gravadas += max(cur.rowcount, 0)
            con.commit()
        cur.execute("select count(atualizado_na_fonte), min(atualizado_na_fonte), max(atualizado_na_fonte) "
                    "from radar_comercial." + tabela)
        print("   gravadas %d · no banco agora: %s com data, de %s a %s" % ((gravadas,) + cur.fetchone()))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
