# -*- coding: utf-8 -*-
"""Recupera o DESTINO das fusões feitas antes da migração 0036.

POR QUE ESTE ARQUIVO EXISTE

Até 27/08/2026 absorver um POI era só `update pois set status = 'fundido'`. Em
quem ele entrou nunca foi gravado: a cadeia existia na memória do processo que
a decidiu e morria com ele. A 0036 criou `fundido_para` e a fusão passou a
gravá-lo — mas os 15.399 POIs já absorvidos ficaram sem destino.

Isso não é histórico: sem destino, `--desfundir` não pode devolvê-los ao mapa.
A evidência deles (telefone, site, endereço) mora num `vinculo_poi` que a fusão
moveu para o sobrevivente, e sem saber quem é o sobrevivente o ponto voltaria
oco — invisível no mapa, que exige vínculo ativo. Medido: 15.388 pontos assim.

COMO O DESTINO É RECUPERÁVEL

A fusão move o vínculo do absorvido para o sobrevivente, e o vínculo carrega o
NOME do absorvido. Então o POI ativo que hoje hospeda um vínculo com o nome do
POI absorvido é o sobrevivente dele.

    ParkShoppingCanoas (absorvido)  →  vínculo "ParkShoppingCanoas"
                                        hoje em Pista de Patinação (Iceland)

É o mesmo rastreio que reparou os 6 vínculos órfãos daquele dia, e acertou os 4
que restavam — inclusive um cujos "dois candidatos" eram dois vínculos para o
MESMO POI.

O QUE ELE NÃO FAZ

Nome que aponta para mais de um POI ativo fica sem destino. Escolher um seria
devolver a evidência de um estabelecimento a outro — dado errado com cara de
dado certo. Medido em Canoas: 86,2% com destino único, 11,7% ambíguos, 2,1%
sem nenhum alvo.

Rodar de novo é seguro: só preenche o que está vazio.
"""
import sys
from collections import defaultdict

import base_comum as bc
import evidencia as ev


def backfill(con, aplicar: bool) -> dict:
    cur = con.cursor()

    # Os vínculos ATIVOS, agrupados pelo nome normalizado. O mesmo normalizador
    # da fusão — casar por texto cru separaria "ParkShoppingCanoas" de
    # "PARKSHOPPING CANOAS" e o destino se perderia por acento e espaço.
    # O DECISOR VEM JUNTO, e sai do mesmo vinculo. A fusao grava `regra` ou
    # `ia` em `confianca_origem` do vinculo que ela move; e esse o dado que
    # `pois.fundido_por` passou a guardar (migracao 0038). Sem ele, `--desfundir`
    # reabre as decisoes da IA toda passada e nunca converge.
    cur.execute("""select v.nome, v.poi_id, v.confianca_origem
                     from vinculo_poi v join pois p on p.id = v.poi_id
                    where p.fundido_em is null and v.estado = 'vinculado'
                      and v.nome is not null and v.nome <> ''""")
    por_nome = defaultdict(set)
    quem = {}
    for nome, poi_id, origem in cur.fetchall():
        n = ev.norm_nome(nome)
        por_nome[n].add(poi_id)
        if origem in ("regra", "ia"):
            quem[(n, poi_id)] = origem

    cur.execute("""select id, nome, fundido_para from pois
                    where fundido_em is not null
                      and (fundido_para is null or fundido_por is null)
                      and nome is not null and nome <> ''""")
    alvos = cur.fetchall()

    achados, ambiguos, sem_alvo = [], 0, 0
    for pid, nome, ja_tem in alvos:
        n = ev.norm_nome(nome)
        destinos = por_nome.get(n, ())
        alvo = ja_tem if ja_tem else (next(iter(destinos))
                                      if len(destinos) == 1 else None)
        if alvo is None:
            if destinos:
                ambiguos += 1
            else:
                sem_alvo += 1
            continue
        achados.append((pid, alvo, quem.get((n, alvo))))

    if achados and aplicar:
        import psycopg2.extras
        psycopg2.extras.execute_values(cur, """
            update pois p
               set fundido_para = coalesce(p.fundido_para, f.vive),
                   fundido_por  = coalesce(p.fundido_por, f.quem)
              from (values %s) as f(morre, vive, quem)
             where p.id = f.morre""",
            achados, template="(%s::bigint, %s::bigint, %s::text)")
        con.commit()

    return {"sem_destino": len(alvos), "recuperados": len(achados),
            "ambiguos": ambiguos, "sem_alvo": sem_alvo}


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--aplicar", action="store_true", help="sem isto, só relata")
    a = p.parse_args(argv)

    con = bc.conectar()
    con.autocommit = False
    try:
        r = backfill(con, a.aplicar)
    finally:
        if not a.aplicar:
            con.rollback()
        con.close()

    print(f"  fusões sem destino gravado ..: {r['sem_destino']:,}")
    print(f"    destino recuperado ........: {r['recuperados']:,}")
    print(f"    nome aponta para vários ...: {r['ambiguos']:,}  (ficam sem — "
          f"escolher um daria a evidência ao estabelecimento errado)")
    print(f"    nenhum alvo ...............: {r['sem_alvo']:,}")
    if not a.aplicar:
        print("\n  ensaio — nada gravado. Use --aplicar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
