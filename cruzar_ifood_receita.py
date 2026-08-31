# -*- coding: utf-8 -*-
"""Casa as lojas do iFood com a Receita, por nome fantasia e razão social.

O iFood mostra o nome de fachada — "Croc Frangos", "Dogão do Rei". A Receita
guarda dois nomes: o `nome_fantasia` do estabelecimento, que costuma ser o
mesmo, e a `razao_social` da empresa, que num MEI é o nome da PESSOA
("KAUAN BOONE BATISTA"). Casar contra os dois é o que dá cobertura; casar só
contra a razão social perderia todo o MEI, que é a maior parte do delivery.

DUAS DECISÕES QUE MUDAM O RESULTADO

1. **Nome sozinho não decide.** "Pizzaria do Zé" existe várias vezes na cidade.
   Quando mais de um estabelecimento empata no topo, o par entra marcado como
   ambíguo e vai para revisão — não se escolhe o primeiro. Uma escolha errada
   aqui vira visita perdida em campo.

2. **Índice invertido, não produto cartesiano.** 1.598 lojas contra 168.358
   estabelecimentos são 269 milhões de comparações. Indexando por token e só
   comparando quem divide token raro, cai para uma fração disso — é o que
   torna a coisa executável na cidade inteira, e não numa amostra.

Uso:
    python cruzar_ifood_receita.py --cidade canoas            # grava
    python cruzar_ifood_receita.py --cidade canoas --simular  # só mede
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict

from psycopg2.extras import execute_values

import base_comum as bc
from cruzar_bases import sem_acento, tokens


def parecenca(a: frozenset, b: frozenset) -> float:
    """Sobreposição de nomes, exigindo DUAS palavras em comum.

    A versão do `cruzar_bases` divide pela lista menor, o que serve para razão
    social contra nome fantasia (um é prefixo informativo do outro). Aqui isso
    produzia falso positivo em massa: um estabelecimento cujo fantasia é só
    "SUPLEMENTOS" casava 1.0 com "intense Life Suplementos - Mv", e "PASTEL"
    casava 1.0 com "A Casa do Pastel".

    Uma palavra em comum não identifica ninguém numa cidade com 168 mil
    empresas. Duas, sim — e quando os dois lados têm uma palavra só, ela
    precisa ser a mesma.
    """
    if not a or not b:
        return 0.0
    inter = a & b
    if len(inter) < 2 and not (len(a) == 1 and len(b) == 1 and inter):
        return 0.0
    return len(inter) / min(len(a), len(b))

# Código do município NA RECEITA — não é o do IBGE. Canoas é 8589 lá e 4304606
# no IBGE; trocar um pelo outro devolve município errado em silêncio.
MUNICIPIO_RF = {"canoas": ("RS", "8589")}

MIN_NOME = 0.72     # mesma régua do `cruzar_bases`, para não ter duas verdades
EMPATE = 0.05       # dentro disto do melhor = a máquina não sabe escolher

# Token que aparece em meia cidade não ajuda a achar ninguém — e arrasta o
# candidato errado. Estes saem do índice, mas continuam contando na nota.
COMUNS = {"canoas", "rs", "delivery", "lanches", "lanche", "pizzaria", "pizza",
          "bar", "restaurante", "mercado", "loja", "casa", "cia", "express",
          "food", "burger", "acai", "sabor", "point", "center", "grill"}

CARREGAR_RF = """
select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv as cnpj,
       e.nome_fantasia, m.razao_social, e.situacao_cadastral,
       e.cnae_principal, e.tipo_logradouro, e.logradouro, e.numero,
       e.bairro, e.cep
  from public.rf_estabelecimentos e
  left join public.rf_empresas m on m.cnpj_basico = e.cnpj_basico
 where e.uf = %s and e.municipio = %s
   -- SÓ ATIVAS. Medido em Canoas: 168.358 estabelecimentos, apenas 58.221
   -- ativos. Casar uma loja que entrega hoje com uma empresa baixada é erro
   -- que vira visita a porta fechada — e ainda inflava o empate, porque a
   -- mesma fachada aparece na versão velha e na nova do cadastro.
   and e.situacao_cadastral = '02'
"""

LOJAS = """
select merchant_id, nome, categoria, bairro
  from radar_comercial.ifood_merchant
 where nome is not null
 order by nome
"""

GRAVAR = """
insert into radar_comercial.cruzamento
       (base_a, id_a, base_b, id_b, chave, score, evidencia, ambiguo,
        concorrentes)
values %s
on conflict (id_empresa, base_a, id_a, base_b, id_b, chave) do update set
  score = excluded.score, evidencia = excluded.evidencia,
  ambiguo = excluded.ambiguo, concorrentes = excluded.concorrentes,
  criado_em = now()
"""


class Estab:
    __slots__ = ("cnpj", "nomes", "situacao", "cnae", "endereco", "bairro", "cep")

    def __init__(self, linha):
        (self.cnpj, fantasia, razao, self.situacao, self.cnae,
         tipo, logra, num, bairro, cep) = linha
        # os dois nomes viram dois conjuntos: num MEI a razão social é o nome do
        # titular e não se parece com a fachada; no resto costuma ajudar
        self.nomes = [t for t in (tokens(fantasia), tokens(razao)) if t]
        partes = [p for p in (tipo, logra) if p]
        self.endereco = " ".join(partes).strip() or None
        if self.endereco and num:
            self.endereco += f", {num}"
        self.bairro = sem_acento(bairro) or None
        self.cep = (cep or "").strip() or None


def indexar(estabs: list) -> dict:
    """token → posições. Sem os tokens comuns, que não discriminam."""
    idx = defaultdict(list)
    for i, e in enumerate(estabs):
        for conj in e.nomes:
            for t in conj:
                if t not in COMUNS and len(t) > 2:
                    idx[t].append(i)
    return idx


def candidatos(idx: dict, nome_tokens: frozenset, teto: int = 4000) -> set:
    """Quem divide ao menos um token discriminante.

    Percorre do token mais RARO para o mais comum e para ao encher o teto: um
    token que aparece em 30 mil empresas não informa nada e só custa tempo.
    """
    uteis = [(len(idx.get(t, ())), t) for t in nome_tokens
             if t not in COMUNS and len(t) > 2 and t in idx]
    uteis.sort()
    saida = set()
    for n, t in uteis:
        if n > teto:
            continue
        saida.update(idx[t])
        if len(saida) > teto:
            break
    return saida


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--simular", action="store_true")
    args = p.parse_args()

    uf, mun = MUNICIPIO_RF[args.cidade.lower()]
    print("⟦fase⟧ ifood-receita", flush=True)

    t0 = time.time()
    ref = bc.conectar_referencia()
    with ref, ref.cursor() as k:
        k.execute(CARREGAR_RF, (uf, mun))
        estabs = [Estab(l) for l in k.fetchall()]
    print(f"  Receita: {len(estabs):,} estabelecimentos em {args.cidade} "
          f"({time.time()-t0:.0f}s)", flush=True)

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(LOJAS)
        lojas = k.fetchall()
    print(f"  iFood:   {len(lojas):,} lojas", flush=True)

    idx = indexar(estabs)
    print(f"  índice:  {len(idx):,} tokens discriminantes\n", flush=True)

    linhas, casadas, ambiguas, sem = [], 0, 0, 0
    t1 = time.time()
    for mid, nome, categoria, bairro in lojas:
        alvo = tokens(nome)
        if not alvo:
            sem += 1
            continue
        marcados = []
        for i in candidatos(idx, alvo):
            e = estabs[i]
            s = max((parecenca(alvo, t) for t in e.nomes), default=0.0)
            if s >= MIN_NOME:
                marcados.append((s, e))
        if not marcados:
            sem += 1
            continue

        marcados.sort(key=lambda x: -x[0])
        melhor = marcados[0][0]
        empatados = [m for m in marcados if melhor - m[0] <= EMPATE]
        ambiguo = len(empatados) > 1
        casadas += 1
        ambiguas += ambiguo
        for s, e in empatados[:5]:      # além de cinco, a lista não informa
            linhas.append((
                "ifood_merchant", mid, "rf_estabelecimentos", e.cnpj,
                "nome", round(s, 3),
                json.dumps({"nome_ifood": nome, "categoria": categoria,
                            "bairro_ifood": bairro,
                            "situacao_rf": e.situacao, "cnae": e.cnae,
                            "endereco_rf": e.endereco, "cep_rf": e.cep,
                            "bairro_rf": e.bairro}, ensure_ascii=False),
                ambiguo, len(empatados) - 1))

    print(f"  {casadas} lojas casaram · {ambiguas} delas com empate "
          f"(vão para revisão) · {sem} sem candidato "
          f"({time.time()-t1:.0f}s)", flush=True)
    print(f"  {len(linhas)} pares gerados", flush=True)

    if linhas and not args.simular:
        with con.cursor() as k:
            execute_values(k, GRAVAR, linhas, page_size=1000)
        con.commit()
        print("  gravados na tabela `cruzamento`", flush=True)
    elif args.simular:
        print("  (simulação — nada gravado)", flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
