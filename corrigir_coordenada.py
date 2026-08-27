# -*- coding: utf-8 -*-
"""corrigir_coordenada.py — quando o endereço desmente a coordenada, o endereço ganha.

O QUE ACONTECE, E COMO SE SABE QUAL DOS DOIS ESTÁ ERRADO

Um POI diz "Rua 25 de Março, 55 — Rio Branco, Canoas" e está desenhado a 9,9 km
dali, no meio do Rio Jacuí. O endereço e a coordenada não podem estar os dois
certos, e a pergunta não é de opinião: o CNEFE do IBGE sabe onde fica a Rua 25
de Março, 55, e sabe em que bairro ela está.

Quando o **bairro ou o CEP que o próprio POI declara** batem com o registro do
CNEFE, a dúvida acaba. Não é o endereço que está errado — é a coordenada.

MEDIDO em Canoas, 27/08/2026, sobre os 12.478 POIs cujo endereço normalizado o
CNEFE conhece:

    ate 100 m da porta ....... 10.628 (85%)   a coordenada concorda
    100 m a 500 m .............. 1.005
    500 m a 2 km ................. 494
    mais de 2 km ................. 351

    dos 1.850 deslocados, 1.296 (70%) tem bairro OU CEP confirmando

DE ONDE VEM O ERRO

Todos os piores casos vieram de `maps_painel`: a busca por nome no Google Maps
casou com um estabelecimento homônimo em outro bairro. É a dispersão geográfica
que o README documenta — "Tóquio", "K2" e afins —, aqui com nome comum de
comércio de bairro.

O `place_id` não protege: 99% dos deslocados têm um. Ele identifica o lugar que
o Maps devolveu, não o lugar certo.

POR QUE SÓ OS CORROBORADOS (decisão do dono do produto)

Os 554 sem corroboração ficam onde estão, marcados para revisão. Mover um ponto
apoiado só na distância seria trocar um erro conhecido por um erro invisível:
se o endereço estiver errado, o ponto vai para o lugar errado com aparência de
certo — e ninguém mais desconfia.

A COORDENADA ANTIGA NÃO SE PERDE. Ela vai para `coord_anterior_lat/lng`, e é o
que permite auditar e desfazer ponto a ponto.
"""
from __future__ import annotations

import math
import re
import unicodedata

import base_comum as bc

# Acima disto o endereço e a coordenada estão falando de lugares diferentes.
# Abaixo, a diferença ainda pode ser a porta vizinha ou um terreno grande.
LONGE_M = 100.0


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                   if unicodedata.category(c) != "Mn")


def _metros(la1, lo1, la2, lo2) -> float:
    return math.hypot((la1 - la2) * 111320,
                      (lo1 - lo2) * 111320 * math.cos(math.radians(la1)))


def _portas_do_municipio(cod_ibge: str) -> dict:
    """`{(VIA, numero): [(lat, lng, bairro, cep)]}` — o CNEFE daquele município."""
    ref = bc.conectar_referencia()
    try:
        cur = ref.cursor()
        cur.execute("""
            select upper(trim(nom_tipo_seglogr || ' ' || nom_seglogr)),
                   regexp_replace(num_endereco, '\\D', '', 'g'),
                   latitude::float8, longitude::float8,
                   dsc_localidade, regexp_replace(coalesce(cep,''), '\\D', '', 'g')
              from ibge_cnefe
             where cod_municipio = %s and num_endereco is not null
               and latitude is not null and longitude is not null""", (cod_ibge,))
        portas: dict = {}
        for via, num, la, lo, bairro, cep in cur.fetchall():
            if num:
                portas.setdefault((via, num), []).append(
                    (la, lo, _sem_acento(bairro), cep))
        return portas
    finally:
        ref.close()


def _corrobora(endereco_poi: str, bairro_cnefe: str, cep_cnefe: str) -> str:
    """O que, no texto do PRÓPRIO POI, confirma o registro do CNEFE.

    Devolve "bairro", "cep", "bairro+cep" ou "" — e o vazio é o que manda o
    ponto para revisão em vez de para outro lugar.
    """
    texto = _sem_acento(endereco_poi)
    so_digitos = re.sub(r"\D", "", endereco_poi or "")
    achados = []
    if bairro_cnefe and bairro_cnefe in texto:
        achados.append("bairro")
    if cep_cnefe and cep_cnefe in so_digitos:
        achados.append("cep")
    return "+".join(achados)


def avaliar(con, cod_ibge: str, cidade: str) -> dict:
    """Levanta quem está deslocado, sem gravar nada.

    Devolve `{"mover": [...], "revisar": [...]}` — `mover` são os corroborados,
    `revisar` os que estão longe sem prova de qual lado erra.
    """
    portas = _portas_do_municipio(cod_ibge)
    cur = con.cursor()
    cur.execute("""
        select p.id, p.nome, coalesce(p.maps_lat, p.lat_origem),
               coalesce(p.maps_lng, p.lng_origem),
               la.logradouro_marcado, la.numero_canonico, p.endereco
          from pois p
          join logradouro_ajustado la on la.fonte = 'pois'
                                     and la.record_id = p.id::text
                                     and la.scope_id = %s
         where p.cidade = %s and coalesce(p.status,'') <> 'fundido'
           and coalesce(p.maps_lat, p.lat_origem) is not null
           and coalesce(trim(la.logradouro_marcado), '') <> ''
           and coalesce(trim(la.numero_canonico), '') <> ''""", (cod_ibge, cidade))

    mover, revisar = [], []
    for pid, nome, plat, plng, via, num, endereco in cur.fetchall():
        pontos = portas.get((via.strip().upper(), num.strip()))
        if not pontos:
            continue
        plat, plng = float(plat), float(plng)
        melhor = min(pontos, key=lambda t: _metros(plat, plng, t[0], t[1]))
        d = _metros(plat, plng, melhor[0], melhor[1])
        if d <= LONGE_M:
            continue
        prova = _corrobora(endereco or "", melhor[2], melhor[3])
        registro = {"id": pid, "nome": nome, "de": (plat, plng),
                    "para": (melhor[0], melhor[1]), "dist_m": round(d, 1),
                    "endereco": f"{via}, {num}", "prova": prova}
        (mover if prova else revisar).append(registro)
    return {"mover": mover, "revisar": revisar}


def aplicar(con, plano: dict, log=print) -> dict:
    """Move os corroborados e marca os demais. Devolve o que foi feito.

    A coordenada antiga vai para `coord_anterior_lat/lng` ANTES de a nova
    entrar — sem isso a correção seria irreversível, e correção automática que
    não se desfaz é uma aposta, não um conserto.
    """
    import psycopg2.extras
    cur = con.cursor()

    if plano["mover"]:
        dados = [(r["id"], r["para"][0], r["para"][1],
                  f"corrigido pelo CNEFE ({r['prova']}), estava a "
                  f"{r['dist_m']:.0f} m") for r in plano["mover"]]
        # `page_size` COBRINDO TUDO, e não é detalhe de desempenho.
        #
        # O padrão do `execute_values` é 100: ele parte a lista em lotes e
        # `cur.rowcount` fica valendo só o ÚLTIMO. Na primeira execução o log
        # anunciou "96 POIs reposicionados" quando 1.296 tinham sido gravados —
        # 1296 mod 100. O dado estava certo e o relato, errado, que é o tipo de
        # erro que faz alguém rodar de novo achando que faltou.
        psycopg2.extras.execute_values(cur, """
            update pois p
               set coord_anterior_lat = coalesce(p.coord_anterior_lat,
                                                 coalesce(p.maps_lat, p.lat_origem)),
                   coord_anterior_lng = coalesce(p.coord_anterior_lng,
                                                 coalesce(p.maps_lng, p.lng_origem)),
                   maps_lat = v.lat, maps_lng = v.lng,
                   coord_fonte = 'cnefe_endereco', coord_precisao = 'porta',
                   coord_incerteza_m = 20,
                   revisar_motivo = v.motivo
              from (values %s) as v(id, lat, lng, motivo)
             where p.id = v.id""",
            dados, template="(%s::bigint, %s::float8, %s::float8, %s::text)",
            page_size=max(1, len(dados)))
        log(f"  {cur.rowcount:,} POIs reposicionados pelo endereço")

    if plano["revisar"]:
        ids = [r["id"] for r in plano["revisar"]]
        cur.execute("""
            update pois
               set revisar_manual = true,
                   revisar_motivo = 'coordenada distante do endereço declarado, '
                                    'sem bairro nem CEP para confirmar qual erra'
             where id = any(%s)""", (ids,))
        log(f"  {cur.rowcount:,} marcados para revisão (sem corroboração)")

    con.commit()
    return {"movidos": len(plano["mover"]), "revisar": len(plano["revisar"])}


def main() -> int:
    import argparse
    import area_utils as au

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cidade", required=True)
    p.add_argument("--municipio", default="", help="código IBGE; sai da área se omitido")
    p.add_argument("--aplicar", action="store_true", help="sem isto, só relata")
    a = p.parse_args()

    cod = a.municipio or au.codigo_ibge_da_area()
    if not cod:
        raise SystemExit("sem código IBGE não sei em que município procurar a porta")

    con = bc.conectar()
    try:
        plano = avaliar(con, cod, a.cidade)
        print(f"  {a.cidade} ({cod})")
        print(f"    a mover (corroborados) : {len(plano['mover']):,}")
        print(f"    a revisar (sem prova)  : {len(plano['revisar']):,}")
        for r in sorted(plano["mover"], key=lambda x: -x["dist_m"])[:5]:
            print(f"      {str(r['nome'])[:30]:30} {r['dist_m']/1000:5.1f} km "
                  f"-> {r['endereco'][:32]} [{r['prova']}]")
        if not a.aplicar:
            print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
            return 0
        r = aplicar(con, plano)
        print(f"\n  GRAVADO: {r['movidos']:,} movidos · {r['revisar']:,} a revisar")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
