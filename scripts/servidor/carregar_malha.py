# -*- coding: utf-8 -*-
"""Carrega a malha municipal do IBGE das 27 UFs de uma vez.

POR QUE ISTO EXISTE, se a malha já entra sozinha.

Ela entra SOB DEMANDA: `/api/malha` busca no IBGE quando alguém navega o mapa
por uma UF que ainda não está no banco. Funciona, e é o que manteve o painel de
pé até aqui — mas tem duas consequências que só aparecem em uso:

  · a PRIMEIRA visita a uma UF paga a espera. São 120 s de timeout contra o
    servidor do IBGE, com o mapa parado, e quem está olhando não sabe por quê;

  · a mineração precisa da malha ANTES de rodar — é ela que traduz "Canoas/RS"
    em `4304606`, e o código é o que liga o município ao Cadastur, ao CNEFE, à
    base estadual e à normalização de endereço. Sem ele, quatro das sete etapas
    caem juntas. Em 29/08/2026 uma área em Goiás quebrou exatamente assim: o
    banco tinha 20 das 27 UFs, e GO era uma das sete que ninguém tinha visitado.

Carregar as 27 de uma vez, quando o servidor está ocioso e a rede é boa, tira as
duas. É a mesma ideia da carga do CNEFE e do CNPJ.

NÃO REIMPLEMENTA NADA. Ele chama `area_utils.garantir_malha`, que é a mesma
função que o `/api/malha` usa — inclusive o `on conflict` que a torna repetível e
o tratamento do GZIP que o IBGE manda sem avisar. Uma segunda implementação da
mesma carga é onde as duas divergem seis meses depois.

USO
    python scripts/servidor/carregar_malha.py            # as 27
    python scripts/servidor/carregar_malha.py --uf GO,MT # só essas
    python scripts/servidor/carregar_malha.py --faltantes  # só as que faltam
"""
from __future__ import annotations

import argparse
import sys
import time

import config  # noqa: F401  — carrega o .env
import area_utils
import base_comum as bc

UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
       "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
       "SP", "SE", "TO"]


def ja_no_banco() -> dict:
    """`{uf: municipios}` do que já está carregado."""
    con = bc.conectar_referencia()
    try:
        with con.cursor() as cur:
            cur.execute("select uf, count(*) from ibge_malha group by uf")
            return {u: n for u, n in cur.fetchall()}
    finally:
        con.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uf", default="", help="UFs separadas por virgula")
    p.add_argument("--faltantes", action="store_true",
                   help="so as UFs que ainda nao tem municipio no banco")
    a = p.parse_args(argv)

    tem = ja_no_banco()
    alvos = [u.strip().upper() for u in a.uf.split(",") if u.strip()] or UFS
    if a.faltantes:
        alvos = [u for u in alvos if not tem.get(u)]

    print("🗺️  malha municipal do IBGE | %d UF(s) | %d ja no banco"
          % (len(alvos), len(tem)), flush=True)
    if not alvos:
        print("✅ nada a fazer: todas as UFs pedidas ja estao carregadas.")
        return 0

    # AS FALHAS SAO CONTADAS, e o codigo de saida diz a verdade.
    #
    # `garantir_malha` NAO levanta excecao de proposito — perder a malha de uma
    # UF nao pode derrubar uma mineracao inteira por causa de uma queda de rede
    # do IBGE. Ela devolve 0. Numa carga em lote, porem, engolir isso e repetir
    # o defeito que o CNEFE tinha hoje: terminar com codigo 0 tendo falhado
    # tudo, e alguem concluir que a base esta carregada.
    falhas, total = [], 0
    for i, uf in enumerate(alvos, 1):
        antes = tem.get(uf, 0)
        if antes:
            print("  %2d/%d  %s  ja tem %d municipios — pulando"
                  % (i, len(alvos), uf, antes), flush=True)
            continue
        print("  %2d/%d  %s  buscando no IBGE…" % (i, len(alvos), uf), flush=True)
        t0 = time.time()
        n = area_utils.garantir_malha(uf)
        if n:
            total += n
            print("        ✓ %d municipios em %.0fs" % (n, time.time() - t0),
                  flush=True)
        else:
            falhas.append(uf)
            print("        ✗ nada gravado", flush=True)
        # UM SEGUNDO ENTRE UFs. O servico do IBGE nao publica limite, e 27
        # requisicoes de malha intermediaria em rajada e o tipo de coisa que faz
        # um servico publico comecar a recusar. O custo total sao 27 segundos.
        time.sleep(1)

    print("", flush=True)
    if falhas:
        print("❌ %d UF(s) sem malha: %s" % (len(falhas), ", ".join(falhas)),
              flush=True)
        print("   Rodar de novo custa so o que falta: `--faltantes`.", flush=True)
        return 1

    print("✅ malha completa: %d municipios gravados nesta rodada." % total,
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
