# -*- coding: utf-8 -*-
"""Cruza as lojas do iFood com os POIs existentes: quem já temos, quem é novo.

A ordem importa e é a que o usuário pediu: **primeiro descobrir a duplicação,
só depois falar em ponto novo.** Criar POI para uma loja que já está na base
gera dois registros do mesmo comércio, e é o supervisor que descobre isso em
campo — tarde e caro.

O que este módulo faz:

  1. casa `ifood_merchant` × `pois` por nome, com âncora de bairro
  2. nos que casaram, marca `presente_no_ifood` e a data — o POI é ENRIQUECIDO,
     nunca duplicado
  3. conta os que sobraram: esses são os candidatos a ponto novo

O que ele NÃO faz: criar os POIs novos. Isso fica para depois de a loja ter
endereço (pelo `ler_lojas_no_meu_chrome.py`), porque POI sem coordenada não
serve para o mapa nem para o roteiro de campo — viraria uma lista de nomes.

Uso:
    python marcar_ifood_nos_pois.py --cidade canoas --simular
    python marcar_ifood_nos_pois.py --cidade canoas
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict

from psycopg2.extras import execute_values

import base_comum as bc
from cruzar_bases import EMPATE, MIN_NOME, parecenca, sem_acento, tokens

POIS = """
select id::text, nome, nome_fantasia, razao_social, endereco, cidade,
       maps_lat, maps_lng
  from radar_comercial.pois
 where (%(cidade)s is null or lower(cidade) = lower(%(cidade)s))
"""

LOJAS = """
select merchant_id, nome, categoria, bairro
  from radar_comercial.ifood_merchant where nome is not null
"""

MARCAR = """
update radar_comercial.pois as p
   set presente_no_ifood = true, ifood_visto_em = now()
  from (values %s) as v(id)
 where p.id::text = v.id
"""

GRAVAR_CRUZ = """
insert into radar_comercial.cruzamento
       (base_a, id_a, base_b, id_b, chave, score, evidencia, ambiguo,
        concorrentes)
values %s
on conflict (id_empresa, base_a, id_a, base_b, id_b, chave) do update set
  score = excluded.score, evidencia = excluded.evidencia,
  ambiguo = excluded.ambiguo, criado_em = now()
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--simular", action="store_true")
    args = p.parse_args()

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(POIS, {"cidade": args.cidade})
        pois = k.fetchall()
        k.execute(LOJAS)
        lojas = k.fetchall()
    print("⟦fase⟧ ifood-x-pois", flush=True)
    print(f"  POIs: {len(pois):,} · lojas iFood: {len(lojas):,}\n", flush=True)

    # O bairro do POI está dentro do endereço em texto livre do Maps; sem ele
    # não há âncora, e nome sem âncora casa a cidade inteira.
    from cruzar_bases import partes_endereco
    por_bairro = defaultdict(list)
    sem_bairro = 0
    for pid, nome, fant, razao, endereco, cidade, lat, lng in pois:
        _, _, bairro = partes_endereco(endereco, cidade)
        conj = [t for t in (tokens(nome), tokens(fant), tokens(razao)) if t]
        if not conj:
            continue
        if not bairro:
            sem_bairro += 1
            continue
        por_bairro[sem_acento(bairro)].append((pid, nome, conj))
    print(f"  POIs com bairro legível: "
          f"{sum(len(v) for v in por_bairro.values()):,} "
          f"({sem_bairro:,} sem bairro ficam de fora do casamento por nome)",
          flush=True)

    t0 = time.time()
    marcados, pares, ambig, novos = set(), [], 0, 0
    for mid, nome, categoria, bairro in lojas:
        alvo = tokens(nome)
        cands = por_bairro.get(sem_acento(bairro or ""), ())
        if not alvo or not cands:
            novos += 1
            continue
        m = []
        for pid, pnome, conj in cands:
            s = max((parecenca(alvo, c) for c in conj), default=0.0)
            if s >= MIN_NOME:
                m.append((s, pid, pnome))
        if not m:
            novos += 1
            continue
        m.sort(key=lambda x: -x[0])
        melhor = m[0][0]
        emp = [x for x in m if melhor - x[0] <= EMPATE]
        ambig += len(emp) > 1
        for s, pid, pnome in emp[:3]:
            marcados.add(pid)
            pares.append(("ifood_merchant", mid, "pois", pid, "nome",
                          round(s, 3),
                          json.dumps({"nome_ifood": nome, "nome_poi": pnome,
                                      "bairro": bairro, "ancora": "bairro"},
                                     ensure_ascii=False),
                          len(emp) > 1, len(emp) - 1))

    print(f"\n  {len(lojas) - novos} lojas casaram com POI existente "
          f"({ambig} com empate)", flush=True)
    print(f"  {len(marcados)} POIs distintos recebem a marca", flush=True)
    print(f"  {novos} lojas SEM POI correspondente — candidatas a ponto novo",
          flush=True)
    print(f"  ({time.time()-t0:.0f}s)", flush=True)

    if args.simular:
        print("\n  (simulação — nada gravado)", flush=True)
        con.close()
        return 0

    with con.cursor() as k:
        if pares:
            execute_values(k, GRAVAR_CRUZ, pares, page_size=500)
        if marcados:
            execute_values(k, MARCAR, [(i,) for i in marcados], page_size=500)
    con.commit()
    with con.cursor() as k:
        k.execute("""select count(*) from radar_comercial.pois
                      where presente_no_ifood""")
        print(f"\n  gravado · POIs marcados no total: {k.fetchone()[0]}",
              flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
