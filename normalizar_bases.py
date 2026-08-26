# -*- coding: utf-8 -*-
"""normalizar_bases.py — as bases FIXAS se normalizam quando mudam, não por área.

A REGRA, do dono do produto em 26/08/2026:

    "a normalização roda sempre que as bases grandes forem atualizadas [...] mas
     em se tratando de POIs roda apenas na área selecionada, que aí sim, com as
     bases comparativas já normalizadas, surte efeito e agrupa mais"

E ela conserta um desperdício que estava acontecendo todo dia.

O QUE ESTAVA ERRADO

A etapa de endereços normalizava POIs **e** CNEFE juntos, recortados pela área.
Só que o CNEFE não muda: são os mesmos 69.150 endereços de Bento Gonçalves hoje,
amanhã e no mês que vem. Recortá-lo por área significava:

  — refazer o mesmo trabalho a cada área nova do mesmo município;
  — e, pior, dar à skill um pedaço PEQUENO da autoridade. Ela aprende
    `tokenA ≡ tokenB` por prova (mesmo número, 30 m, support de dois imóveis
    distintos); com 262 endereços ela quase não tem o que provar, e com 69.150
    tem. Medido em Bento: 84 marcações, 80 delas `CONFIRMA` (dicionário fechado)
    e apenas 4 `ALTA` — quase nenhum aprendizado.

O QUE FICA CERTO

    BASE FIXA (CNEFE, cadastro do cliente)   município INTEIRO, quando ela muda
    POI                                       só a área, a cada mineração

O léxico é persistente por município (`lexico_<cod>.json`). Então normalizar o
CNEFE inteiro uma vez ENSINA o léxico daquela cidade — e toda área minerada
depois herda esse aprendizado de graça. É o oposto de refazer: é acumular.

COMO ELE SABE QUE PRECISA RODAR

`fonte_arquivos` guarda `carregado_em` por fonte. Se o CNEFE foi carregado
depois da última normalização daquele município, ela está velha. Sem isso, o
comando não faz nada — e diz que não fez.

USO
    python normalizar_bases.py --municipio 4302105            # se precisar
    python normalizar_bases.py --municipio 4302105 --forcar   # de qualquer jeito
    python normalizar_bases.py --uf RS --todos                # todos os já usados
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config  # noqa: F401
import base_comum as bc

BASE = Path(__file__).resolve().parent
PYTHON = sys.executable

# As fontes cuja atualização torna a normalização velha. POI não entra: ele muda
# a cada mineração, e é justamente por isso que ele é normalizado por área.
FONTES_FIXAS = ("cnefe", "cadastro_cliente", "tratamento-cnpj")


def _linhas_cnefe(cod: str) -> int:
    """Quantos endereços o CNEFE tem neste município — o denominador da
    cobertura. Vem do banco de REFERÊNCIA, que é onde o CNEFE mora."""
    ref = bc.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select count(*) from ibge_cnefe where cod_municipio = %s",
                        (str(cod),))
            return (cur.fetchone() or [0])[0]
    except Exception:  # noqa: BLE001 — sem referência, cobertura não é critério
        return 0
    finally:
        ref.close()


def precisa(cur, cod: str) -> tuple:
    """`(precisa, motivo)` — a normalização deste município está velha?"""
    cur.execute("""
        select max(carregado_em) from fonte_arquivos
         where fonte = any(%s)""", (list(FONTES_FIXAS),))
    base_em = (cur.fetchone() or [None])[0]

    cur.execute("""
        select max(ajustado_em) from logradouro_ajustado
         where scope_id = %s and fonte = 'cnefe'""", (str(cod),))
    norm_em = (cur.fetchone() or [None])[0]

    if norm_em is None:
        return True, "o CNEFE deste município nunca foi normalizado"

    # DATA NÃO BASTA — COBERTURA TAMBÉM CONTA.
    #
    # A etapa 7 normaliza por ÁREA, e isso grava linhas de CNEFE recentes com o
    # `scope_id` do município. Olhando só a data, o município parecia "em dia"
    # com 0,1% de cobertura: medido em 26/08, Bento Gonçalves tinha 67 de 64.356
    # linhas normalizadas e foi pulado, enquanto Canoas e Cachoeirinha estavam
    # em 99%.
    #
    # A pergunta certa não é "quando rodou" e sim "rodou INTEIRO". Abaixo de 80%
    # o que existe é resíduo de área, não a base normalizada.
    cur.execute("select count(*) from logradouro_ajustado "
                " where scope_id = %s and fonte = 'cnefe'", (str(cod),))
    tem = (cur.fetchone() or [0])[0]
    total = _linhas_cnefe(cod)
    if total and tem / total < 0.8:
        return True, (f"só {tem:,} de {total:,} linhas do CNEFE normalizadas "
                      f"({tem / total:.0%}) — é resíduo de área, não o município")

    if base_em is None:
        return False, f"{tem:,} linhas normalizadas; sem registro de carga de base"
    if base_em > norm_em:
        return True, (f"base fixa carregada em {base_em:%d/%m/%Y} e normalização "
                      f"de {norm_em:%d/%m/%Y}")
    return False, (f"{tem:,} de {total:,} linhas ({tem / max(1, total):.0%}), "
                   f"normalização de {norm_em:%d/%m/%Y}")


def normalizar(cod: str, forcar: bool = False, log=print) -> int:
    """Roda a normalização do MUNICÍPIO INTEIRO para as bases fixas."""
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            vai, motivo = precisa(cur, cod)
    finally:
        con.close()

    if not vai and not forcar:
        log(f"  {cod}: nada a fazer — {motivo}")
        return 0
    log(f"  {cod}: normalizando o município inteiro — {motivo}")

    # SEM `--area`: é o município todo, de propósito. É o único lugar do sistema
    # onde isso é certo, e o docstring acima diz por quê.
    cmd = [PYTHON, "ajuste_logradouro.py", "--municipio", str(cod), "--aplicar"]
    p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, bufsize=1, text=True,
                         encoding="utf-8", errors="replace")
    for linha in p.stdout:
        log("    " + linha.rstrip())
    return p.wait()


def municipios_em_uso(cur) -> list:
    """Os municípios que já têm POI — são esses que valem normalizar.

    Normalizar os 497 do RS seria produzir léxico para cidade que ninguém
    minerou. O critério é o uso, não o cadastro.
    """
    cur.execute("""
        select distinct m.cod_municipio, m.nome
          from pois p
          join ibge_malha m on upper(translate(m.nome,
                 'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ','aaaaeeiooouucAAAAEEIOOOUUC'))
             = upper(translate(coalesce(p.cidade,''),
                 'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ','aaaaeeiooouucAAAAEEIOOOUUC'))
         where p.cidade is not null and p.cidade <> ''
         order by 1""")
    return cur.fetchall()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--municipio", default="", help="código IBGE")
    p.add_argument("--todos", action="store_true",
                   help="todos os municípios que já têm POI")
    p.add_argument("--forcar", action="store_true")
    p.add_argument("--listar", action="store_true",
                   help="só diz quem está velho, sem rodar")
    p.add_argument("--min-pois", dest="min_pois", type=int, default=50,
                   help="só municípios com pelo menos N POIs (padrão 50): 17 "
                        "deles concentram 98% do dado")
    a = p.parse_args(argv)

    min_pois = a.min_pois
    if a.municipio:
        return normalizar(a.municipio, a.forcar)

    if not (a.todos or a.listar):
        p.print_help()
        return 2

    # `municipios_em_uso` cruza `pois` (banco do produto) com `ibge_malha`
    # (banco de referência) — são bancos diferentes, então a lista de municípios
    # vem de um e os códigos do outro.
    # SÓ ONDE HÁ TRABALHO DE VERDADE, e o número decide.
    #
    # Medido em 26/08/2026: 320 municípios têm POI, mas 17 deles concentram 98%
    # dos 82.977 pontos. Os outros 303 têm menos de 50 POIs cada — normalizar o
    # CNEFE inteiro deles (dezenas de milhares de endereços por município) seria
    # horas de trabalho para 2% do dado.
    #
    # Eles não ficam de fora para sempre: quando uma área ali for minerada, a
    # etapa 7 normaliza os POIs daquela área do jeito de sempre. O que não se
    # faz é preparar o município inteiro antes de alguém pedir.
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select cidade, count(*) from pois
                    where cidade is not null and cidade <> ''
                    group by 1 having count(*) >= %s order by 2 desc""",
                (min_pois,))
    cidades = [r[0] for r in cur.fetchall()]
    con.close()

    ref = bc.conectar_referencia()
    rc_total = 0
    try:
        with ref.cursor() as c2:
            c2.execute("""select cod_municipio, nome from ibge_malha
                           where uf = 'RS'""")
            por_nome = {n.lower(): c for c, n in c2.fetchall()}
    finally:
        ref.close()

    alvos = [(por_nome[c.lower()], c) for c in cidades if c.lower() in por_nome]
    print(f"  {len(alvos)} municípios com POI no banco")

    con = bc.conectar()
    try:
        for cod, nome in alvos:
            with con.cursor() as cur:
                vai, motivo = precisa(cur, cod)
            print(f"    {nome[:28]:30} {cod}  "
                  + ("PRECISA" if vai else "em dia") + f" — {motivo}")
    finally:
        con.close()

    if a.listar:
        return 0
    for cod, nome in alvos:
        print(f"\n▶ {nome} ({cod})")
        rc_total |= normalizar(cod, a.forcar)
    return rc_total


if __name__ == "__main__":
    raise SystemExit(main())
