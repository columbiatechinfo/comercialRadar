# -*- coding: utf-8 -*-
"""Re-julga as ligações decididas ANTES das duas correções de 08/09/2026.

QUAIS SÃO AS AFETADAS, e por quê:

1. SEM IMAGEM E SEM AVISO. O dossiê não dizia nada quando não tinha foto
   nenhuma para mandar, e o prompt anuncia "AS PRIMEIRAS IMAGENS SÃO FOTOS DE
   RUA". Um modelo que lê essa frase e não recebe imagem nenhuma é convidado a
   comentar uma fachada que não viu. Medido: 29,6% dessas foram para revisão
   humana antes da correção, contra 5,0% depois.

2. MUITOS REGISTROS DE UM SISTEMA SÓ. A regra de "fontes suficientes" contava
   SISTEMAS independentes, e por isso tratava 32 CNPJs da Receita no mesmo
   endereço como testemunha fraca — a ligação 2221694 foi para revisão humana
   com a justificativa "não há fotos da fachada para confirmar". Medido: 26,7%
   dessas iam para revisão antes, 0,0% depois.

O CORTE É O PRIMEIRO DOSSIÊ COM O AVISO NOVO, e não um horário escrito à mão.
O aviso só pode existir depois do reinício que recarregou o módulo, então ele
marca o reinício com precisão — a primeira tentativa de cortar por horário
usou o fuso errado e o grupo "depois" saiu vazio, um teste que não testava
nada.
"""
import argparse
import time

import base_comum as bc
import avaliar_ligacao as al

SQL = """
with marco as (
  select min(avaliado_em) as t
    from radar_comercial.ligacao_veredito
   where modelo <> 'regra'
     and (percepcao->>'dossie') like '%%NENHUMA IMAGEM ACOMPANHA%%'
)
select v.ligacao
  from radar_comercial.ligacao_veredito v, marco
 where v.modelo <> 'regra'
   and (marco.t is null or v.avaliado_em < marco.t)
   and (coalesce(v.imagens, 0) = 0
        or (coalesce(v.pois, 0) >= 3 and coalesce(v.fontes, 0) <= 1))
   -- E SÓ QUEM AINDA TEM VÍNCULO VIVO. Uma ligação cujos POIs foram todos
   -- descartados não tem o que re-julgar, e entraria só para voltar `sem_poi`.
   and exists (select 1 from radar_comercial.ligacao_poi lp
                where lp.ligacao = v.ligacao and lp.descartado_em is null)
 order by v.ligacao
"""


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--trabalhadores", type=int, default=80)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()
    cur.execute(SQL)
    alvos = [r[0] for r in cur.fetchall()]
    con.close()
    print("%s · %d ligação(ões) a refazer"
          % (time.strftime("%H:%M:%S"), len(alvos)), flush=True)
    if not alvos:
        return 0
    if not a.aplicar:
        print("(ensaio: nada gravado. Use --aplicar)")
        return 0

    # `refazer=True` desliga o filtro de "sem veredito" — sem isso a fila
    # devolveria lista vazia, porque todas estas JÁ têm veredito.
    return 0 if al.rodar(0, True, a.trabalhadores, al.MODELO_PADRAO,
                         alvos, True) else 0


if __name__ == "__main__":
    raise SystemExit(main())
