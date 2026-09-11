# -*- coding: utf-8 -*-
"""O que o teto de 50 m derrubou e o de 200 m aceita, de volta.

`revisar_vinculo` so enxerga vinculo VIVO — ele aplica a regra ao que esta
gravado, e nao ressuscita nada. Subir o teto de 50 para 200 m nao adianta
sozinho: os pares que caiam por distancia ja estao com `descartado_em`
preenchido e a revisao nem os le.

ESTE MODULO E O CAMINHO DE VOLTA, e ele so devolve quem passa na regra NOVA
inteira: rua e numero batendo (que ja era condicao para ter caido por
distancia), ate 200 m, e bairro concordando quando os dois lados o publicam.

O BAIRRO E O QUE TORNA ISSO SEGURO. A 200 m cabe a quadra e as vizinhas, e o
mesmo numero na mesma rua existe em bairros diferentes da mesma cidade.
"""
import argparse
import time

import base_comum as bc
import regra_vinculo as rv

SQL = """
select lp.id, lp.ligacao, lp.poi_id, lp.metros,
       coalesce(c.nom_bairro,''), coalesce(lr.bairro,'')
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join resources_root.cadastro_corsan c on c.num_ligacao::text = lp.ligacao
  left join radar_comercial.logradouro_resolvido lr on lr.poi_id = lp.poi_id
 where lp.descartado_por = 'regra_vinculo'
   and lp.descartado_motivo like 'endereco exato, mas a%'
   and p.fundido_em is null
   and lp.mesmo_endereco and lp.mesmo_numero
"""


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()
    cur.execute(SQL)
    todos = cur.fetchall()
    _log("%d vínculo(s) caíram por distância com endereço exato" % len(todos))

    volta, longe, bairro_nao = [], 0, 0
    for (lid, lig, poi, metros, b_lig, b_poi) in todos:
        if metros is not None and metros > rv.TETO_M:
            longe += 1
            continue
        if not rv._bairro_bate({"bairro_lig": b_lig, "bairro_poi": b_poi}):
            bairro_nao += 1
            continue
        volta.append(lid)

    _log("   voltam ................... %d" % len(volta))
    _log("   ainda acima de %d m ...... %d" % (round(rv.TETO_M), longe))
    _log("   bairro não concorda ...... %d" % bairro_nao)

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    from psycopg2.extras import execute_values
    for i in range(0, len(volta), 5000):
        execute_values(cur, """
            update radar_comercial.ligacao_poi lp
               set descartado_em = null, descartado_motivo = null,
                   descartado_por = null, aceito_por = 'endereco_exato'
              from (values %s) as v(id)
             where lp.id = v.id::bigint
        """, [(x,) for x in volta[i:i + 5000]], page_size=1000)
        con.commit()
    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where descartado_em is null""")
    _log("vínculos vivos agora: %d" % int(cur.fetchone()[0] or 0))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
