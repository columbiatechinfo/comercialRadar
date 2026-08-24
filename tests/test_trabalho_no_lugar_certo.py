# -*- coding: utf-8 -*-
"""Nenhum trabalho pago pode viver numa coordenada fora do município declarado.

O QUE ISTO IMPEDE

Uma foto de fachada é tirada NA COORDENADA do POI. Se a coordenada está errada,
a foto é de outro prédio — e a análise de IA que a lê julga um estabelecimento
que não é aquele. O veredito sai com cara de veredito e não vale nada.

Em 24/08/2026 havia 13 POIs assim. Três deles eram pousadas do Piauí com **seis
fotos e uma análise de IA cada**, capturadas a até 20 km do próprio endereço.
Ninguém tinha como notar: a tela mostra a foto ao lado do nome, e nada dizia que
as duas coisas não se encontram.

O CUSTO DE CADA CLASSE, que é o que justifica um teste e não um aviso

    foto errada     leva alguém ao endereço errado, ou pior: aprova um imóvel
                    olhando a fachada de outro;
    análise de IA   US$ 0,017 gastos para julgar o prédio errado;
    captura         2,6 s de proxy por foto, refeitos do zero.

POR QUE POLÍGONO E NÃO DISTÂNCIA

Distância do centróide reprova ponto legítimo em município grande — Santa
Vitória do Palmar tem 5.244 km². E não pega o inverso: um ponto a 3 km, mas
dentro do vizinho, é outro município e passa despercebido.

O CAMPO `cidade` NÃO É AUTORIDADE — E ESSA LIÇÃO CUSTOU CARO

A primeira versão deste teste comparava a coordenada contra o polígono do
município que o campo `cidade` declara. Com base nela apaguei 8 fotos e 3
análises de IA de três POIs — e estavam certas. O que errava era o rótulo:

    Centro Distribuição CORSAN   `cidade`=Esteio     CEP 92420 = Canoas
    Camping Porto Batista        `cidade`=Triunfo    CEP 92330 = Canoas
    MBK pousada                  `cidade`=Luís Corr. CEP 64200 = Parnaíba

Nos três, o pin do Google e o CEP concordavam entre si e discordavam do rótulo.
O CEP é uma terceira fonte independente — vem do CNEFE do IBGE — e vale mais
que um campo de texto que qualquer etapa do enriquecimento pode ter escrito.

Então o teste só acusa quando a coordenada discorda do rótulo **E** do CEP.
Discordar só do rótulo é problema de rótulo, e rótulo não invalida foto.
"""
from __future__ import annotations

import os
import sys
import unicodedata

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402

# 2 km de folga: a malha do IBGE é simplificada e um endereço na divisa cai fora
# por dezenas de metros. É a mesma tolerância do `geocodificar.dentro_do_municipio`
# e do `test_coerencia_local` — três lugares com o mesmo número, de propósito.
FOLGA_M = 2000


def _norm(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                   if unicodedata.category(c) != "Mn")


def test_nenhuma_foto_ou_analise_fora_do_municipio():
    try:
        con = bc.conectar()
    except Exception as e:
        pytest.skip(f"banco do produto inacessível: {type(e).__name__}")

    try:
        with con.cursor() as k:
            # Só quem TEM trabalho pago: é onde o erro custa dinheiro e engana
            # decisão. POI sem foto com coordenada duvidosa é assunto do
            # `test_coerencia_local`.
            k.execute("""
                select p.id, p.nome, p.cidade, p.uf,
                       coalesce(p.maps_lat, p.lat_origem),
                       coalesce(p.maps_lng, p.lng_origem),
                       (select count(*) from comercialradar.streetview_imgs s
                         where s.poi_id = p.id) as fotos,
                       (select count(*) from comercialradar.analise_ia a
                         where a.poi_id = p.id) as analises,
                       -- O CEP do endereço: a terceira fonte, que desempata
                       -- entre o rótulo e a coordenada.
                       substring(p.endereco from '[0-9]{5}') as cep
                  from comercialradar.pois p
                 where p.cidade is not null and p.uf is not null
                   and coalesce(p.maps_lat, p.lat_origem) is not null
                   and p.match_valido is not false
                   and (exists (select 1 from comercialradar.streetview_imgs s
                                 where s.poi_id = p.id)
                     or exists (select 1 from comercialradar.analise_ia a
                                 where a.poi_id = p.id))""")
            linhas = k.fetchall()
    finally:
        con.close()

    if not linhas:
        pytest.skip("nenhum POI com foto ou análise")

    try:
        ref = bc.conectar_referencia()
    except Exception as e:
        pytest.skip(f"malha do IBGE inacessível: {type(e).__name__}")

    try:
        with ref.cursor() as rc:
            rc.execute("select cod_municipio, upper(nome), uf from ibge_malha")
            cod_de = {}
            for cod, nome_m, uf in rc.fetchall():
                cod_de.setdefault((_norm(nome_m), uf), cod)

            ids, cods, las, los = [], [], [], []
            for i, _, cidade, uf, la, lo, _, _, _ in linhas:
                cod = cod_de.get((_norm(cidade), uf))
                if not cod:
                    continue     # sem malha daquele município: não dá para julgar
                ids.append(i); cods.append(cod)
                las.append(float(la)); los.append(float(lo))

            fora = []
            if ids:
                # UMA consulta. Ponto a ponto seriam milhares de idas ao banco —
                # a primeira versão levou nove minutos e derrubou a conexão.
                rc.execute("""
                    select v.id,
                           round((ST_Distance(m.geom::geography,
                             ST_SetSRID(ST_MakePoint(v.lo, v.la), 4326)::geography)
                             / 1000)::numeric, 1)
                      from unnest(%s::bigint[], %s::text[], %s::float8[], %s::float8[])
                           as v(id, cod, la, lo)
                      join ibge_malha m on m.cod_municipio = v.cod
                     where not ST_DWithin(
                             m.geom::geography,
                             ST_SetSRID(ST_MakePoint(v.lo, v.la), 4326)::geography,
                             %s)
                     order by 2 desc""", (ids, cods, las, los, FOLGA_M))
                fora = rc.fetchall()

            # ── O CEP DESEMPATA ──────────────────────────────────────────
            #
            # Para cada suspeito, pergunta ao CNEFE a que município pertence o
            # CEP do endereço. Se ele bate com onde a COORDENADA está, quem
            # errou foi o rótulo — e rótulo errado não invalida foto.
            ceps = {i: c for i, _, _, _, _, _, _, _, c in linhas if c}
            perdoados = set()
            for i, _ in fora:
                cep = ceps.get(i)
                if not cep:
                    continue
                rc.execute("""select m.cod_municipio from ibge_cnefe c
                               join ibge_malha m on m.cod_municipio = c.cod_municipio
                              where c.cep like %s
                              group by 1 order by count(*) desc limit 1""",
                           (cep + "%",))
                linha_cep = rc.fetchone()
                if not linha_cep:
                    continue
                idx = ids.index(i)
                rc.execute("""select ST_DWithin(geom::geography,
                                ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)
                               from ibge_malha where cod_municipio = %s""",
                           (los[idx], las[idx], FOLGA_M, linha_cep[0]))
                bate = rc.fetchone()
                if bate and bate[0]:
                    perdoados.add(i)
            fora = [(i, d) for i, d in fora if i not in perdoados]
    finally:
        ref.close()

    info = {i: (nome, cidade, fotos, analises)
            for i, nome, cidade, _, _, _, fotos, analises, _ in linhas}
    detalhe = [f"{i} {info[i][0][:28]!r} ({info[i][1]}) a {d} km · "
               f"{info[i][2]} fotos, {info[i][3]} análises"
               for i, d in fora]
    assert not fora, (
        f"{len(fora)} POIs têm foto ou análise de IA capturada numa coordenada "
        f"FORA do município que declaram, E o CEP do endereço não os perdoa — "
        f"a foto é de outro prédio e o veredito julgou o estabelecimento "
        f"errado:\n  " + "\n  ".join(detalhe[:10]))
