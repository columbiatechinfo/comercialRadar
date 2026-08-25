# -*- coding: utf-8 -*-
"""vinculo.py — de que fontes um POI é feito, e como desfazer uma fusão errada.

A ficha de um POI mostra **uma aba por fonte**: o que o Overture diz, o que o
OpenStreetMap diz, o que o iFood diz, e o que a carteira do cliente diz. As
quatro divergem entre si — e é justamente essa divergência que o operador
precisa ver para julgar se são o mesmo estabelecimento.

Quando não são, ele clica no `x` da aba e desvincula. **O registro desvinculado
vira um POI novo**; os que ficam seguem sendo o mesmo POI.

POR QUE ISSO PRECISA EXISTIR

Medido no RS em 25/08/2026: das 11.676 fusões suspeitas, a IA julgou 400 pares
sorteados e disse que 66,2% uniram estabelecimentos distintos — cerca de 7.800
lojas apagadas numa UF. A fusão é feita por máquina, com evidência incompleta,
e vai errar. O que não pode é errar sem saída.

O TRABALHO PAGO FICA COM O POI ORIGINAL

Street View capturado, análise de IA, fotos e a fila do supervisor apontam para
o POI. Ao desvincular, eles **permanecem onde estão** — no POI que mantém o id.
O POI novo nasce sem evidência.

É a escolha conservadora, e é deliberada: a captura foi feita NAQUELA
coordenada, e mover evidência para um ponto que ninguém fotografou é inventar
procedência. A seção 29 da DOCUMENTACAO existe por causa de treze casos de
trabalho pago que acabaram no lugar errado. Se a evidência devia ir junto, quem
sabe é a pessoa que desvinculou — não este código.
"""
from __future__ import annotations

import json

import psycopg2.extras

import config  # noqa: F401
import base_comum as bc

# Ordem de exibição das abas. A carteira do cliente vem primeiro por ser a base
# contra a qual o trabalho todo é comparado; depois o que tem painel e foto;
# depois as fontes públicas.
ORDEM_FONTES = ["cadastro", "maps", "ifood", "estadual", "overture", "fsq", "osm"]


def _ordem(fonte: str) -> int:
    f = (fonte or "").lower()
    return ORDEM_FONTES.index(f) if f in ORDEM_FONTES else len(ORDEM_FONTES)


def fontes_do_poi(con, poi_id: int, incluir_desvinculados: bool = False) -> list:
    """As abas da ficha, na ordem de exibição."""
    with con.cursor() as cur:
        cur.execute("""
            select id, fonte, id_fonte, nome, lat, lng, dados, confianca,
                   confianca_origem, motivo, modelo, estado,
                   desvinculado_por, desvinculado_em, desvinculado_para
              from vinculo_poi
             where poi_id = %s
               and (%s or estado = 'vinculado')
             order by confianca desc, fonte
            """, (poi_id, incluir_desvinculados))
        cols = [d[0] for d in cur.description]
        linhas = [dict(zip(cols, r)) for r in cur.fetchall()]
    linhas.sort(key=lambda x: (_ordem(x["fonte"]), -(x["confianca"] or 0)))
    return linhas


def vincular(con, poi_id: int, fonte: str, id_fonte: str, confianca: int,
             origem: str, nome: str = "", lat=None, lng=None, dados: dict = None,
             motivo: str = "", modelo: str = "") -> int:
    """Registra que este POI é composto também por este registro de fonte."""
    if not 1 <= int(confianca) <= 10:
        raise ValueError(f"confianca fora de 1..10: {confianca}")
    with con.cursor() as cur:
        cur.execute("""
            insert into vinculo_poi
              (poi_id, fonte, id_fonte, nome, lat, lng, dados, confianca,
               confianca_origem, motivo, modelo)
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            on conflict (fonte, id_fonte, poi_id) do update set
              confianca = excluded.confianca,
              confianca_origem = excluded.confianca_origem,
              motivo = excluded.motivo, modelo = excluded.modelo,
              nome = coalesce(excluded.nome, vinculo_poi.nome),
              dados = case when excluded.dados = '{}'::jsonb
                           then vinculo_poi.dados else excluded.dados end
            returning id
            """, (poi_id, fonte, id_fonte, nome or None, lat, lng,
                  json.dumps(dados or {}, ensure_ascii=False), int(confianca),
                  origem, motivo or None, modelo or None))
        return cur.fetchone()[0]


class NaoPodeDesvincular(Exception):
    """A operação foi recusada, com o motivo escrito."""


def desvincular(con, poi_id: int, fonte: str, id_fonte: str, por: str) -> dict:
    """Tira uma fonte do POI. O que sai vira POI novo; o que fica continua junto.

    Devolve `{poi_novo, poi_origem, restantes, virou_ancora}`.
    """
    with con.cursor() as cur:
        cur.execute("""
            select id, nome, lat, lng, dados, confianca
              from vinculo_poi
             where poi_id = %s and fonte = %s and id_fonte = %s
               and estado = 'vinculado'
            """, (poi_id, fonte, id_fonte))
        alvo = cur.fetchone()
        if not alvo:
            raise NaoPodeDesvincular(
                f"{fonte}:{id_fonte} não está vinculado ao POI {poi_id}")

        cur.execute("""select count(*) from vinculo_poi
                        where poi_id = %s and estado = 'vinculado'""", (poi_id,))
        total = cur.fetchone()[0]
        if total <= 1:
            # Desvincular a última fonte deixaria um POI sem nenhuma origem —
            # um ponto que ninguém afirma. Quem quer isso quer APAGAR o POI, que
            # é outra operação, com outra confirmação.
            raise NaoPodeDesvincular(
                f"o POI {poi_id} tem uma fonte só; desvincular deixaria um ponto "
                "sem origem. Para removê-lo, apague o POI.")

        vid, nome, lat, lng, dados, _conf = alvo

        # O POI NOVO herda o que a fonte desvinculada afirma — e só isso.
        cur.execute("""select cidade, uf, tenant_id from pois where id = %s""", (poi_id,))
        cidade, uf, _tenant = cur.fetchone()
        d = dados or {}
        cur.execute("""
            insert into pois
              (nome, fonte, lat_origem, lng_origem, maps_lat, maps_lng, place_id,
               categoria, endereco, telefone, website, cidade, uf, status, match_valido)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
            returning id
            """, (nome or d.get("nome") or "(sem nome)", fonte, lat, lng, lat, lng,
                  f"{fonte}:{id_fonte}", d.get("categoria"), d.get("endereco"),
                  d.get("telefone"), d.get("site") or d.get("website"),
                  cidade, uf, "desvinculado"))
        poi_novo = cur.fetchone()[0]

        cur.execute("""
            update vinculo_poi
               set estado = 'desvinculado', desvinculado_por = %s,
                   desvinculado_em = now(), desvinculado_para = %s
             where id = %s
            """, (por, poi_novo, vid))

        # O registro passa a compor o POI novo — com confiança 10 e origem
        # `manual`: uma pessoa afirmou que este ponto existe sozinho, e isso é a
        # evidência mais forte que o sistema tem.
        cur.execute("""
            insert into vinculo_poi
              (poi_id, fonte, id_fonte, nome, lat, lng, dados, confianca,
               confianca_origem, motivo)
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, 10, 'manual', %s)
            """, (poi_novo, fonte, id_fonte, nome, lat, lng,
                  json.dumps(d, ensure_ascii=False),
                  f"desvinculado do POI {poi_id} por {por}"))

        # SE A FONTE QUE SAIU ERA A ÂNCORA, o POI de origem ficou com nome e
        # coordenada de um registro que não o compõe mais. Reancora na fonte de
        # maior confiança que restou — senão o ponto seguiria se apresentando
        # como algo que já não é.
        cur.execute("""select place_id from pois where id = %s""", (poi_id,))
        place_id = (cur.fetchone() or [""])[0] or ""
        virou_ancora = False
        if place_id.endswith(f":{id_fonte}") or place_id == id_fonte:
            cur.execute("""
                select fonte, id_fonte, nome, lat, lng from vinculo_poi
                 where poi_id = %s and estado = 'vinculado'
                 order by confianca desc, id
                 limit 1""", (poi_id,))
            nova = cur.fetchone()
            if nova:
                cur.execute("""
                    update pois set nome = coalesce(%s, nome),
                           maps_lat = coalesce(%s, maps_lat),
                           maps_lng = coalesce(%s, maps_lng),
                           place_id = %s
                     where id = %s
                    """, (nova[2], nova[3], nova[4], f"{nova[0]}:{nova[1]}", poi_id))
                virou_ancora = True

        # A trilha vai para a `auditoria` do sistema, com o estado ANTES e
        # DEPOIS. Desvincular é uma decisão humana sobre a identidade de um
        # ponto: sem o antes, ninguém consegue reconstruir o que foi desfeito.
        cur.execute("""
            insert into auditoria (papel_bd, acao, tabela, registro, antes, depois)
            values (current_user, 'desvincular_fonte', 'vinculo_poi', %s, %s::jsonb, %s::jsonb)
            """, (str(vid),
                  json.dumps({"poi_id": poi_id, "fonte": fonte,
                              "id_fonte": id_fonte, "estado": "vinculado"},
                             ensure_ascii=False),
                  json.dumps({"poi_id": poi_novo, "fonte": fonte,
                              "id_fonte": id_fonte, "estado": "desvinculado",
                              "por": por, "reancorou_origem": virou_ancora},
                             ensure_ascii=False)))

    return {"poi_novo": poi_novo, "poi_origem": poi_id,
            "restantes": total - 1, "virou_ancora": virou_ancora}
