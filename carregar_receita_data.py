# -*- coding: utf-8 -*-
"""carregar_receita_data.py — o que a base bruta da Receita sabe de cada POI da Receita.

POR QUE ISTO EXISTE. `receita_data` nasceu vazia em 03/09/2026, esperando um
enriquecimento que nunca rodou. A situacao cadastral de cada CNPJ so morava em
`resources_root.rf_estabelecimentos` — 72,8 milhoes de linhas, 14 GB, sem
indice —, e por isso nenhum dos 58.803 POIs da Receita em Canoas sabia se o
estabelecimento estava ativo.

Regra do dono do produto em 11/09/2026: da Receita, SO OS ATIVOS sao
consultados. Sem a situacao guardada aqui, cumprir a regra obrigaria cada etapa
a varrer os 14 GB de novo.

UMA VARREDURA SO. Os CNPJs dos POIs vao para uma tabela temporaria e o banco
casa os dois lados num hash join — a base bruta e lida uma vez, em paralelo, e
nenhuma consulta volta a ela depois. Criar indice em 72 milhoes de linhas para
uma leitura so custaria mais do que a propria leitura.

O QUE VEM JUNTO, porque sai da mesma linha: bairro (o que a Receita escreveu),
complemento (sala, apto, loja — o que separa unidades num mesmo numero), nome
fantasia e CNAE principal. O resto da linha vai em `bruto`.

    python carregar_receita_data.py            # ensaio: conta e mostra
    python carregar_receita_data.py --aplicar  # grava em receita_data
"""
import argparse
import json
import time
from collections import Counter

from psycopg2.extras import execute_values

import base_comum as bc

#: A Receita codifica a situacao com dois digitos. So a 02 e ATIVA.
SITUACAO = {"01": "NULA", "02": "ATIVA", "03": "SUSPENSA", "04": "INAPTA",
            "08": "BAIXADA"}


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("set statement_timeout = '1800s'")
    cur.execute("""select id, cnpj from radar_comercial.pois
                    where lower(fonte) = 'receita' and fundido_em is null
                      and cnpj ~ '^[0-9]{14}$'""")
    pois = cur.fetchall()
    _log("%d POIs da Receita com CNPJ de 14 digitos" % len(pois))

    cur.execute("create temp table t_cnpj (poi_id bigint, basico text, "
                "ordem text, dv text) on commit drop")
    execute_values(cur, "insert into t_cnpj values %s",
                   [(pid, c[:8], c[8:12], c[12:]) for pid, c in pois],
                   page_size=5000)
    cur.execute("analyze t_cnpj")

    t0 = time.time()
    _log("lendo a base bruta da Receita (uma varredura)...")
    cur.execute("""
        select t.poi_id, t.basico || t.ordem || t.dv,
               e.situacao_cadastral, e.data_situacao, e.motivo_situacao,
               e.bairro, e.complemento, e.nome_fantasia, e.cnae_principal,
               e.tipo_logradouro, e.logradouro, e.numero, e.cep, e.municipio,
               e.uf, e.matriz_filial, e.data_inicio
          from t_cnpj t
          join resources_root.rf_estabelecimentos e
            on e.cnpj_basico = t.basico and e.cnpj_ordem = t.ordem
           and e.cnpj_dv = t.dv
    """)
    linhas = cur.fetchall()
    _log("   %d achados em %.0f s" % (len(linhas), time.time() - t0))

    sit = Counter(SITUACAO.get((l[2] or "").zfill(2), l[2] or "?") for l in linhas)
    _log("   situacao: " + " · ".join("%s %d" % kv for kv in sit.most_common()))
    com_bairro = sum(1 for l in linhas if (l[5] or "").strip())
    _log("   com bairro escrito: %d · com complemento: %d"
         % (com_bairro, sum(1 for l in linhas if (l[6] or "").strip())))
    achados = {l[0] for l in linhas}
    _log("   POIs sem linha na base bruta: %d" % (len(pois) - len(achados)))

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.rollback()
        con.close()
        return 0

    valores = []
    for (pid, cnpj, sc, dsit, msit, bairro, compl, fant, cnae, tlog, logr,
         num, cep, mun, uf, mf, dini) in linhas:
        bruto = {"data_situacao": dsit, "motivo_situacao": msit,
                 "tipo_logradouro": tlog, "logradouro": logr, "numero": num,
                 "cep": cep, "municipio_rf": mun, "uf": uf,
                 "matriz_filial": mf, "data_inicio": dini}
        valores.append((pid, cnpj, (sc or "").zfill(2) if sc else None,
                        (fant or "").strip() or None, cnae,
                        (bairro or "").strip() or None,
                        (compl or "").strip() or None, json.dumps(bruto)))
    # `id_empresa` NAO vai no INSERT: quem carimba e a trigger
    # `receita_data_empresa`, como em toda tabela de fonte.
    execute_values(cur, """
        insert into radar_comercial.receita_data
            (poi_id, cnpj, situacao_cadastral, nome_fantasia, cnae, bairro,
             complemento, bruto)
        values %s
        on conflict (poi_id) do update set
            cnpj = excluded.cnpj,
            situacao_cadastral = excluded.situacao_cadastral,
            nome_fantasia = coalesce(excluded.nome_fantasia, radar_comercial.receita_data.nome_fantasia),
            cnae = coalesce(excluded.cnae, radar_comercial.receita_data.cnae),
            bairro = excluded.bairro,
            complemento = excluded.complemento,
            bruto = excluded.bruto
    """, valores, template="(%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        page_size=2000)
    con.commit()
    # O ROWCOUNT DO BANCO, e nao a ausencia de excecao: RLS com `using` filtra
    # calada. Ver a memoria rls-using-filtra-calado.
    cur.execute("select count(*), count(*) filter (where situacao_cadastral = '02') "
                "from radar_comercial.receita_data")
    tot, ativos = cur.fetchone()
    _log("receita_data tem %d linhas, %d ativas (pedidas %d)" % (tot, ativos, len(valores)))
    con.close()
    return 0 if tot >= len(valores) else 1


if __name__ == "__main__":
    raise SystemExit(main())
