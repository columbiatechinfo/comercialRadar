# -*- coding: utf-8 -*-
"""origem_estadual.py — de qual das três fontes veio cada POI do dataset estadual.

O QUE FALTAVA

Os POIs do dataset estadual entram todos com `fonte = 'estadual'` e
`fonte_dado` NULO. Em Canoas são 27.527 assim — e "estadual" é a soma de três
fontes que não valem o mesmo:

    Overture     agregado, com endereço estruturado e categoria hierárquica
    OSM          colaborativo, cobertura irregular e nome frequentemente melhor
    Foursquare   comercial, forte em varejo e restauração

Sem saber qual delas trouxe cada ponto, três perguntas ficam sem resposta: qual
fonte está cobrindo mal aquela cidade; se um POI duvidoso veio de uma fonte que
erra naquele segmento; e o que se perde se uma delas sair.

O DADO NUNCA FOI PERDIDO — só não foi importado

O `poi_padronizado_*.parquet` traz, por linha:

    cluster_id   o mesmo que virou `place_id = 'estadual:<cluster_id>'`
    fonte        a fonte primária daquele ponto
    fontes       TODAS as que contribuíram, quando o ponto é fusão de várias

Então isto é junção, não reprocessamento: casa-se pelo `place_id` que já está
no banco e grava-se a procedência. Nenhuma extração roda de novo.

`fonte_dado` E NÃO `fonte`

`fonte` continua `'estadual'`. Trocá-la por `'overture'` quebraria toda consulta
que hoje separa o que veio do dataset do que veio do Maps, do iFood ou do
Cadastur — e a pergunta "veio do dataset?" continua valendo tanto quanto a nova.
`fonte_dado` é onde o detalhe mora nas outras fontes (`ifood:<id>`,
`maps:place_id`), e passa a valer aqui igual: `estadual:overture` quando é uma
só, `estadual:overture+osm` quando o ponto é fusão.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter

import psycopg2.extras

import base_comum as bc

PADRAO = "/app/dados_externos/estadual/%s/saida/poi_padronizado_*.parquet"


def _log(m: str) -> None:
    print(m, flush=True)


def _normalizar_fontes(fonte, fontes) -> str:
    """`overture` ou `overture+osm`, em ordem estável.

    O campo `fontes` vem como texto separado por vírgula ou ponto e vírgula,
    conforme a versão da extração. Ordenar evita que o mesmo conjunto apareça
    como duas grafias e vire dois valores distintos na contagem.
    """
    bruto = (fontes or fonte or "").strip()
    if not bruto:
        return ""
    partes = [p.strip().lower() for p in bruto.replace(";", ",").split(",")]
    partes = [p for p in partes if p]
    if not partes:
        return ""
    return "+".join(sorted(set(partes)))


def do_uf(uf: str, aplicar: bool = False) -> dict:
    try:
        import duckdb
    except ImportError:
        _log("   ⚠️  sem duckdb nesta imagem — este passo lê Parquet")
        return {"erro": "sem duckdb"}

    arquivos = sorted(glob.glob(PADRAO % uf.upper()))
    if not arquivos:
        _log("   ⚠️  nenhum poi_padronizado para %s em %s"
             % (uf.upper(), os.path.dirname(PADRAO % uf.upper())))
        _log("      A procedência vem do Parquet da extração; sem ele não há o")
        _log("      que casar. Rode a extração da UF, ou aponte outro caminho.")
        return {"erro": "sem parquet"}
    _log("   %d arquivo(s) padronizado(s) de %s" % (len(arquivos), uf.upper()))

    d = duckdb.connect()
    linhas = d.execute(
        "select cluster_id, fonte, fontes from read_parquet(?) "
        "where cluster_id is not null", [arquivos]).fetchall()
    _log("   %d clusters no Parquet" % len(linhas))

    por_place = {}
    contagem = Counter()
    for cluster, fonte, fontes in linhas:
        chave = _normalizar_fontes(fonte, fontes)
        if not chave:
            continue
        por_place["estadual:%s" % cluster] = "estadual:%s" % chave
        contagem[chave] += 1

    _log("\n   procedência no Parquet:")
    for k, n in contagem.most_common(12):
        _log("      %-26s %8d" % (k, n))

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select count(*) from radar_comercial.pois
         where fonte = 'estadual' and coalesce(fonte_dado,'') = ''
    """)
    pendentes = cur.fetchone()[0]
    _log("\n   POIs 'estadual' sem procedência no banco: %d" % pendentes)

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"clusters": len(por_place), "pendentes": pendentes, "gravados": 0}

    # A junção é pelo `place_id`, que já é único por empresa e já está indexado.
    # Mandar em lote de pares evita 27 mil UPDATEs individuais.
    pares = list(por_place.items())
    gravados = 0
    for i in range(0, len(pares), 5000):
        fatia = pares[i:i + 5000]
        psycopg2.extras.execute_values(cur, """
            update radar_comercial.pois p
               set fonte_dado = v.fonte_dado
              from (values %s) as v(place_id, fonte_dado)
             where p.place_id = v.place_id
               and p.fonte = 'estadual'
               and coalesce(p.fonte_dado,'') = ''
        """, fatia, page_size=1000)
        gravados += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        con.commit()

    cur.execute("""
        select coalesce(fonte_dado,'(sem procedência)'), count(*)
          from radar_comercial.pois where fonte = 'estadual'
         group by 1 order by 2 desc
    """)
    _log("\n   depois, no banco:")
    for fd, n in cur.fetchall():
        _log("      %-30s %8d" % (fd, n))

    nas_tabelas = _povoar_tabelas_de_fonte(con)
    con.close()
    return {"clusters": len(por_place), "pendentes": pendentes,
            "gravados": gravados, "tabelas": nas_tabelas}


# `fonte_dado` -> a tabela daquela fonte, e a coluna do id NATIVO dela.
#
# O `cluster_id` chega prefixado — `osm:node/10696063176`, `overture:<uuid>`,
# `fsq:<id>` — porque no Parquet ele precisa distinguir as três. Dentro da
# tabela da fonte o prefixo é ruído: `osm_data` já é do OSM. Guardar os dois é
# de propósito: `cluster_id` é a chave para voltar ao Parquet da extração, e
# `osm_id`/`overture_id`/`fsq_id` é a chave para voltar À FONTE — consultar a
# API do Foursquare ou abrir o objeto no openstreetmap.org.
TABELA_DA_FONTE = {
    "osm":      ("osm_data", "osm_id"),
    "overture": ("overture_data", "overture_id"),
    "fsq":      ("foursquare_data", "fsq_id"),
}


def _povoar_tabelas_de_fonte(con) -> dict:
    """Cria a linha do POI na tabela da fonte que o trouxe.

    ATÉ 03/09/2026 A PROCEDÊNCIA MORRIA NUM TEXTO. `fonte_dado` dizia
    `estadual:overture`, e era só isso: não havia onde guardar o id do Overture,
    a categoria dele nem a confiança que ele próprio declara. A migração 0048 deu
    uma tabela a cada fonte, e este passo é quem as povoa.

    O `cluster_id` é o que sai daqui, e é o que importa: é por ele que se volta
    ao Parquet da extração para buscar qualquer campo que ainda não trouxemos,
    sem precisar reprocessar nada.

    UM POI PODE ESTAR EM MAIS DE UMA. `fonte_dado` vale `estadual:overture+osm`
    quando o ponto é fusão de duas fontes — e nesse caso ele é, ao mesmo tempo,
    um ponto do Overture e um ponto do OSM. Ganha linha nas duas, e é por isso
    que o casamento é por TOKEN e não por igualdade: `fonte_dado = 'estadual:osm'`
    deixaria de fora todo ponto fundido. No RS de hoje não há fusão, mas em PE há,
    e o defeito só apareceria lá.

    `on conflict do nothing` porque este passo é relido: rodar de novo depois de
    uma extração nova acrescenta o que falta sem tocar no que já está.
    """
    placar = {}
    with con.cursor() as k:
        for token, (tabela, col_id) in TABELA_DA_FONTE.items():
            # `split_part(x, ':', 1)` nao serve: o id do OSM tem dois pontos
            # dentro (`osm:node/123`), e cortar no primeiro devolveria `node/123`
            # so por sorte. Aqui remove-se o PREFIXO EXATO, do tamanho dele.
            k.execute("""
                insert into radar_comercial.""" + tabela + """
                       (poi_id, id_empresa, cluster_id, """ + col_id + """)
                select p.id, p.id_empresa,
                       substring(p.place_id from 10),
                       substring(substring(p.place_id from 10) from %s)
                  from radar_comercial.pois p
                 where p.fonte = 'estadual'
                   and p.place_id like 'estadual:%%'
                   and ('+' || substring(p.fonte_dado from 10) || '+') like %s
                on conflict (poi_id) do update
                       set """ + col_id + """ = excluded.""" + col_id + """,
                           cluster_id = excluded.cluster_id
            """, (len(token) + 2, "%+" + token + "+%"))
            placar[tabela] = k.rowcount
        con.commit()
        for tabela, _ in TABELA_DA_FONTE.values():
            k.execute("select count(*), count(cluster_id) "
                      "from radar_comercial." + tabela)
            n, com = k.fetchone()
            placar[tabela + "_total"] = n
            placar[tabela + "_com_id"] = com

    _log("\n   linhas nas tabelas de fonte:")
    for tabela, _ in TABELA_DA_FONTE.values():
        _log("      %-18s %-8d (total %d, com cluster_id %d)"
             % (tabela, placar[tabela], placar[tabela + "_total"],
                placar[tabela + "_com_id"]))
    return placar


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Grava de qual fonte veio cada POI do dataset estadual.")
    p.add_argument("--uf", required=True, help="sigla da UF (RS, PE, PI...)")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ procedência do dataset estadual · %s" % a.uf.upper())
    r = do_uf(a.uf, a.aplicar)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
