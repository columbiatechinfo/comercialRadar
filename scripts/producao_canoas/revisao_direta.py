# -*- coding: utf-8 -*-
"""Manda uma ligacao direto para revisao humana, sem passar pela IA (14/09/2026).

Uso unico, decisao do dono do produto: a ligacao 2563698 tem 162 registros
candidatos, e a lista nao cabe numa chamada da IA (contexto de 32.768 tokens) —
a resposta vinha cortada a cada lote. O veredito fica gravado no mesmo formato
do julgamento, com o processo dizendo que nao houve IA."""
import sys

sys.path.insert(0, "/app")
import base_comum as bc  # noqa: E402
import avaliar_enxuto as ae  # noqa: E402
import avaliar_ligacao as al  # noqa: E402

MOTIVO = ("Prédio com {n} registros candidatos: a lista não cabe numa chamada da IA (contexto de "
          "32.768 tokens) e a resposta vinha cortada. Por decisão do dono do produto em 14/09/2026, "
          "vai direto para revisão humana, sem parecer da IA.")

con = bc.conectar()
for lig in sys.argv[1:]:
    dados, fotos, rot, ids, n_fontes, refs = ae.montar_com_refs(con, lig)
    if dados is None:
        print(lig, "sem registros — nada gravado")
        continue
    motivo = MOTIVO.format(n=len(ids))
    r = {"veredito": "revisao_humana", "motivo": motivo, "justificativa": motivo,
         "aderentes": [], "nao_combinam": [], "pois_de_outro_endereco": []}
    percepcao = {"processo": "revisão humana direta (grande demais para a IA)", "dados": dados,
                 "fotos": rot, "fotos_ref": refs, "resposta": r, "ids": ids}
    al.gravar(con, lig, "revisao_humana", r, percepcao, {"pois": len(ids), "fontes": n_fontes, "ids": ids},
              "nenhum (revisão direta)", len(fotos), 0.0)
    con.commit()
    print(lig, "gravada em revisao_humana ·", len(ids), "registros")
con.close()
