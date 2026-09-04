# -*- coding: utf-8 -*-
"""reusar_area.py — trazer para a sua empresa o que outra já extraiu na área.

O MAPA É NEUTRO; O POI TEM DONO.

O mesmo estabelecimento existe uma vez por empresa. Não é duplicata por
descuido: é o desenho. Os índices únicos da `pois` são `(id_empresa, place_id)`
e `(id_empresa, nome, endereço)` — duas empresas podem ter o mesmo ponto, cada
uma com o seu.

O que faltava era a escolha antes de extrair:

    REAPROVEITAR   copia o que já existe dentro da área, de qualquer empresa, e
                   a coleta roda só sobre o que é novo. Poupa processamento; o
                   dado pode estar velho, e `coletado_em` diz quanto.
    DO ZERO        coleta tudo de novo. As duas empresas ficam com os mesmos
                   POIs, cada uma com os SEUS.

Este arquivo faz o primeiro. O segundo é a mineração normal, sem ele.

A TRAVESSIA ENTRE EMPRESAS NÃO ESTÁ AQUI, e isso é o ponto do desenho.

Perguntar "o que as OUTRAS empresas já extraíram" é atravessar o isolamento, e
havia dois jeitos. O primeiro é elevar a sessão a suporte — foi assim que o
cruzamento por ligação funcionou até 03/09/2026, e o preço era a conexão INTEIRA
rodando com a trava desligada. O segundo, que é este, é deixar a travessia em
três funções `security definer` estreitas (migração 0055), cada uma com um
trabalho só. `reusar_pois` não aceita empresa de destino como parâmetro: o
destino é sempre `core.empresa_atual()`, e por isso ela não serve para gravar na
empresa alheia.

Este arquivo é o que sobra depois disso: chamar as funções e recortar pelo
polígono, que é a única parte que o SQL não faz bem.

    python reusar_area.py --area area_atual              # o que existe, sem copiar
    python reusar_area.py --area area_atual --aplicar    # copia
"""
from __future__ import annotations

import argparse

import area_utils
import base_comum as bc


def _log(m):
    print(m, flush=True)


def _caixa(poligono):
    lats = [p[0] if isinstance(p, (list, tuple)) else p["lat"] for p in poligono]
    lngs = [p[1] if isinstance(p, (list, tuple)) else p["lng"] for p in poligono]
    return min(lats), max(lats), min(lngs), max(lngs)


def resumo_da_area(con, poligono) -> list:
    """O que existe na área, por empresa, com a data da coleta.

    É o que a tela mostra antes de perguntar "reaproveitar ou do zero?".

    A CAIXA BASTA AQUI. O recorte fino pelo polígono mudaria a contagem em
    alguns por cento e custaria trazer todos os pontos para o Python — e esta é
    uma tela de decisão, não um relatório. Quem precisa do número exato é a
    cópia, e ela recorta.
    """
    a, b, c, d = _caixa(poligono)
    with con.cursor() as cur:
        cur.execute("select * from radar_comercial.reuso_resumo(%s,%s,%s,%s)",
                    (a, b, c, d))
        return [{"id_empresa": str(r[0]), "empresa": r[1], "pois": r[2],
                 "coleta_mais_antiga": str(r[3]) if r[3] else None,
                 "coleta_mais_recente": str(r[4]) if r[4] else None,
                 "e_minha": r[5]} for r in cur.fetchall()]


def candidatos_na_area(con, poligono) -> list:
    """Os POIs de outra empresa, DENTRO do polígono, que a minha não tem."""
    a, b, c, d = _caixa(poligono)
    with con.cursor() as cur:
        cur.execute("select * from radar_comercial.reuso_candidatos(%s,%s,%s,%s)",
                    (a, b, c, d))
        brutos = cur.fetchall()
    dentro = [r for r in brutos
              if area_utils.ponto_no_poligono(r[1], r[2], poligono)]
    return dentro, len(brutos) - len(dentro)


def reusar(area: str, aplicar: bool = False) -> dict:
    poligono = area_utils.carregar_area(area)
    if not poligono:
        _log("   área '%s' não existe. Desenhe a área no mapa antes." % area)
        return {"erro": "sem area"}

    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("select core.empresa_atual()")
        destino = cur.fetchone()[0]
    if not destino:
        _log("   sem empresa na sessão — não há para quem copiar.")
        con.close()
        return {"erro": "sem empresa"}

    resumo = resumo_da_area(con, poligono)
    _log("")
    _log("   o que já existe nesta área:")
    if not resumo:
        _log("      (nada — esta área nunca foi extraída)")
    for d in resumo:
        _log("      %-24s %8d POIs   coleta de %s a %s%s"
             % (d["empresa"][:24], d["pois"], d["coleta_mais_antiga"],
                d["coleta_mais_recente"], "   <- sua empresa" if d["e_minha"] else ""))

    cands, fora = candidatos_na_area(con, poligono)
    _log("")
    if fora:
        _log("   %d ponto(s) descartado(s) por cair fora do desenho "
             "(a caixa é maior que o polígono)" % fora)
    _log("   %d POI(s) para reaproveitar" % len(cands))

    if not cands:
        con.close()
        return {"resumo": resumo, "candidatos": 0, "copiados": 0}

    if not aplicar:
        for r in cands[:8]:
            _log("      %8d  %-38s coletado em %s"
                 % (r[0], (r[3] or "")[:38], str(r[4])[:10]))
        if len(cands) > 8:
            _log("      ... e mais %d" % (len(cands) - 8))
        _log("   (ensaio: nada copiado. Use --aplicar)")
        con.close()
        return {"resumo": resumo, "candidatos": len(cands), "copiados": 0}

    # EM LOTES, e não de uma vez: a função monta um array de ids no servidor e
    # uma cidade inteira passaria de duzentos mil. O lote também dá um número
    # que se mexe no log de uma cópia longa.
    LOTE = 5000
    placar = {}
    ids = [r[0] for r in cands]
    for i in range(0, len(ids), LOTE):
        fatia = ids[i:i + LOTE]
        with con.cursor() as cur:
            cur.execute("select tabela, linhas from radar_comercial.reusar_pois(%s)",
                        (fatia,))
            for tabela, n in cur.fetchall():
                placar[tabela] = placar.get(tabela, 0) + int(n or 0)
        con.commit()
        _log("      %d/%d POIs copiados" % (min(i + LOTE, len(ids)), len(ids)))

    _log("")
    _log("   copiado para a sua empresa:")
    for t, n in sorted(placar.items(), key=lambda kv: -kv[1]):
        if n:
            _log("      %-24s %8d linha(s)" % (t, n))
    con.close()
    return {"resumo": resumo, "candidatos": len(cands),
            "copiados": placar.get("pois", 0), "filhas": placar}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ reaproveitar o que já foi extraído na área '%s'" % a.area)
    r = reusar(a.area, a.aplicar)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
