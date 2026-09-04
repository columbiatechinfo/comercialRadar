# -*- coding: utf-8 -*-
"""catalogar_categorias.py — o que existe de categoria, para o operador escolher.

POR QUE ISTO EXISTE

A avaliação por IA custa captura e Spark por POI. Mandar tudo é gastar o
orçamento em quem não tem nada para ser visto: traduzindo os CNAE mais comuns
dos POIs de Canoas, quatro dos sete maiores são MEI SEM PORTA DE RUA —
transporte rodoviário de carga (1.590 POIs), obras de alvenaria (1.364),
promoção de vendas (2.253), apoio administrativo (1.632). Já "comércio
varejista de vestuário" (2.270) e "cabeleireiros" (2.267) valem a olhada.

Este passo levanta o que EXISTE; quem escolhe é o operador, na tela.

DOIS LADOS, E ELES NÃO SE MISTURAM

    CNAE      os 869 códigos da Receita, traduzidos pela `rf_cnaes` (869 de
              869 casam) e arrumados em seção → divisão → classe
    RÓTULOS   os 1.383 textos das outras fontes, como vieram, agrupados por
              fonte

Mapear rótulo para CNAE exigiria adivinhar 1.383 vezes, e adivinhação errada
aqui não aparece como erro — aparece como categoria que o operador achou que
tinha marcado e não marcou.

    python catalogar_categorias.py            # mostra o que acharia
    python catalogar_categorias.py --aplicar  # grava o catálogo
"""
from __future__ import annotations

import argparse
import re

import base_comum as bc


def _log(m):
    print(m, flush=True)


SQL_CATEGORIAS = """
    select p.fonte, btrim(p.categoria) as valor, count(*) as pois
      from radar_comercial.pois p
     where coalesce(btrim(p.categoria), '') <> ''
     group by 1, 2
"""

# Um CNAE e sete dígitos. `4781400`, `4781-4/00` e `47814 00` sao o mesmo.
RE_SO_DIGITO = re.compile(r"\D")


def _cnae(valor: str):
    """Devolve o codigo de 7 digitos, ou None se nao for CNAE."""
    d = RE_SO_DIGITO.sub("", valor or "")
    return d if len(d) == 7 else None


def catalogar(aplicar: bool = False) -> dict:
    con = bc.conectar()
    cur = con.cursor()

    cur.execute("select codigo, descricao from resources_root.rf_cnaes")
    cnaes = {r[0]: r[1] for r in cur.fetchall()}
    _log("   %d CNAE na tabela da Receita" % len(cnaes))

    cur.execute("select letra, nome, div_ini, div_fim from radar_comercial.cnae_secao")
    secoes = cur.fetchall()

    def secao_de(divisao):
        for letra, _nome, ini, fim in secoes:
            if ini <= divisao <= fim:
                return letra
        return None

    cur.execute(SQL_CATEGORIAS)
    linhas = cur.fetchall()
    _log("   %d par(es) (fonte, categoria) nos POIs" % len(linhas))

    registros, sem_desc, por_lado = [], [], {"cnae": 0, "rotulo": 0}
    for fonte, valor, pois in linhas:
        cod = _cnae(valor)
        if cod and cod in cnaes:
            div = int(cod[:2])
            registros.append((fonte, valor, cod, div, secao_de(div),
                              cnaes[cod], pois))
            por_lado["cnae"] += 1
        else:
            if cod:
                # Parece CNAE e nao esta na tabela da Receita. Nao vira rotulo
                # calado: um codigo sem descricao na tela e uma escolha as
                # cegas, e o operador nao tem como saber que faltou traducao.
                sem_desc.append((fonte, valor, pois))
            registros.append((fonte, valor, None, None, None, valor, pois))
            por_lado["rotulo"] += 1

    _log("")
    _log("   lado CNAE ...... %5d categoria(s)" % por_lado["cnae"])
    _log("   lado rótulos ... %5d categoria(s)" % por_lado["rotulo"])
    if sem_desc:
        _log("   ⚠️  %d parecem CNAE e não estão na rf_cnaes — entram como rótulo:"
             % len(sem_desc))
        for f, v, n in sem_desc[:5]:
            _log("        %-10s %-12s %d POIs" % (f, v, n))

    # o quadro por seção, que é o que a tela vai mostrar
    quadro = {}
    for fonte, valor, cod, div, sec, rot, pois in registros:
        if sec:
            quadro.setdefault(sec, [0, 0])
            quadro[sec][0] += 1
            quadro[sec][1] += pois
    _log("")
    _log("   por seção da CNAE:")
    nomes = {l: n for l, n, _i, _f in secoes}
    for sec in sorted(quadro):
        n_cat, n_pois = quadro[sec]
        _log("      %s  %-52s %4d cat · %6d POIs"
             % (sec, nomes.get(sec, "?")[:52], n_cat, n_pois))

    porfonte = {}
    for fonte, valor, cod, div, sec, rot, pois in registros:
        if not sec:
            porfonte.setdefault(fonte, [0, 0])
            porfonte[fonte][0] += 1
            porfonte[fonte][1] += pois
    _log("")
    _log("   rótulos por fonte:")
    for f in sorted(porfonte, key=lambda k: -porfonte[k][1]):
        _log("      %-10s %4d rótulo(s) · %6d POIs" % (f, *porfonte[f]))

    if not aplicar:
        _log("")
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"categorias": len(registros), "gravadas": 0}

    from psycopg2.extras import execute_values
    execute_values(cur, """
        insert into radar_comercial.categoria_catalogo
               (fonte, valor, cnae_classe, cnae_divisao, cnae_secao,
                rotulo, pois)
        values %s
        on conflict (id_empresa, fonte, valor) do update set
               cnae_classe  = excluded.cnae_classe,
               cnae_divisao = excluded.cnae_divisao,
               cnae_secao   = excluded.cnae_secao,
               rotulo       = excluded.rotulo,
               pois         = excluded.pois,
               visto_em     = now()
    """, registros, page_size=500)
    con.commit()
    cur.execute("select count(*), count(*) filter (where avaliar) "
                "from radar_comercial.categoria_catalogo")
    total, marcadas = cur.fetchone()
    _log("")
    _log("   catálogo com %d categoria(s); %d marcada(s) para avaliar" % (total, marcadas))
    _log("   A marcação é do operador, na tela — nada nasce marcado.")
    con.close()
    return {"categorias": len(registros), "gravadas": total}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ catálogo de categorias")
    r = catalogar(a.aplicar)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
