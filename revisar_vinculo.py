# -*- coding: utf-8 -*-
"""Aplica a regra de vínculo ao que JÁ está gravado, e descarta o que não passa.

POR QUE UMA PASSADA SEPARADA, e não só corrigir o cruzamento: o `insert` de
`cruzar_ligacao` é um upsert — `on conflict do update` — e nunca apaga. Uma
regra mais restrita, sozinha, deixaria de criar vínculo novo e não removeria
um só dos 350.494 que já estão lá. O cruzamento corrigido cuida do futuro;
esta passada cuida do passado.

O DESCARTE NÃO APAGA. Escreve `descartado_em`, `descartado_motivo` e
`descartado_por` — as colunas da migração 0083, que o dossiê da ligação já
respeita. O vínculo continua no banco para quem quiser auditar por que ele
saiu, e o POI fica livre para achar outra ligação.

NÃO TOCA NO QUE A IA DESCARTOU. Aqueles têm `descartado_por = 'ia'` e o motivo
que o modelo escreveu; sobrescrever isso apagaria a única explicação que
existe para eles.
"""
import argparse
import re
import time
from collections import Counter, defaultdict

import base_comum as bc
import regra_vinculo as rv

SQL = """
select lp.ligacao, lp.poi_id, lp.mesmo_endereco, lp.mesmo_numero,
       lp.mesmo_telhado, lp.metros, coalesce(p.nome,''),
       coalesce(lr.numero,''), coalesce(c.nro,''),
       coalesce(c.nom_bairro,''), coalesce(lr.bairro,''),
       coalesce(lr.forca,'') = 'prova' as num_e_prova,
       -- A FONTE DECIDE SE A REGRA ESTRITA VALE. O Airbnb nao publica numero
       -- de porta — e do desenho da plataforma —, e exigi-lo dele excluiria a
       -- fonte inteira. Ver `regra_vinculo.SEM_ENDERECO_EXATO`.
       coalesce(p.fonte,'')
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join resources_root.cadastro_corsan c on c.num_ligacao::text = lp.ligacao
  left join radar_comercial.logradouro_resolvido lr on lr.poi_id = lp.poi_id
 where lp.descartado_em is null
   and p.fundido_em is null
   {cidade}
"""


#: O VINCULO DE POI FUNDIDO NAO E VINCULO, e escapava de todo mundo.
#:
#: `SQL` filtra `p.fundido_em is null` — de proposito, porque a regra fala de
#: POIs que existem. So que o filtro tinha um efeito que eu nao previ: o
#: vinculo de um POI FUNDIDO nunca era examinado, e continuava com
#: `descartado_em is null`, ou seja, VIVO para toda consulta que so olha essa
#: coluna. Inclusive as minhas contagens.
#:
#: Achado em 10/09/2026 auditando os dois unicos vinculos de Canoas sem rua: os
#: dois eram da Madeireira Maravilha, POI 78458, fundido em 08/09, apontando
#: para ligacoes da Indio Sepe enquanto o POI publica "Rua das Costureiras".
#:
#: O julgamento nunca os viu — o dossie e a fila tambem filtram fundido —,
#: entao o estrago era de contagem, nao de veredito. Mas numero que ninguem
#: consegue explicar e numero que nao serve.
SQL_FUNDIDOS = """
update radar_comercial.ligacao_poi lp
   set descartado_em = now(),
       descartado_motivo = 'o POI foi fundido em outro; quem vale e o '
                           'sobrevivente, nao a copia',
       descartado_por = 'regra_vinculo'
  from radar_comercial.pois p
 where p.id = lp.poi_id
   and p.fundido_em is not null
   and lp.descartado_em is null
"""


def _familia(motivo):
    """O motivo sem os numeros, para o placar.

    O MOTIVO DO TETO CARREGA A DISTANCIA — "endereco exato, mas a 954 m" —
    porque na linha do banco ela e a explicacao inteira: sem ela o operador nao
    sabe se caiu por 51 m ou por 6 km. No PLACAR isso vira uma chave por
    distancia: a primeira corrida imprimiu mais de mil linhas com contagem 1 e
    escondeu os quatro totais que interessavam.
    """
    return re.sub(r"\d+", "N", motivo)


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Descarta os vínculos que não passam na regra")
    p.add_argument("--cidade", default="")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()
    # A TABELA DE RARIDADE PRIMEIRO. `regra_vinculo.parecidos` recusa a
    # trabalhar sem ela — de proposito: sem os pesos o criterio 4 casaria
    # "Pizzaria" com "Pizzaria" e o vazamento voltaria por ali.
    _log("pesando os nomes de POI da cidade...")
    _log("   %d tokens" % rv.carregar_pesos(con, a.cidade))
    filtro = " and upper(coalesce(c.cidade,'')) = upper(%s)" if a.cidade else ""
    _log("lendo os vínculos%s..." % (" de " + a.cidade if a.cidade else ""))
    cur.execute(SQL.format(cidade=filtro),
                (a.cidade,) if a.cidade else ())

    por_lig = defaultdict(list)
    for (lig, poi, m_end, m_num, m_tel, metros, nome, n_poi, n_lig,
         b_lig, b_poi, prova, fonte) in cur:
        por_lig[lig].append({
            "poi": poi, "fonte": fonte,
            "mesma_rua": bool(m_end), "mesmo_numero": bool(m_num),
            "mesmo_telhado": bool(m_tel), "metros": metros, "nome": nome,
            "bairro_lig": b_lig, "bairro_poi": b_poi,
            "contradiz": rv.contradiz_numero(n_poi, n_lig, prova)})
    _log("%d ligações · %d vínculos"
         % (len(por_lig), sum(len(v) for v in por_lig.values())))

    fora, dentro, placar = [], [], Counter()
    ligs_que_zeram = 0
    for lig, cands in por_lig.items():
        fica = rv.aceitar(cands)
        for c in cands:
            if c["poi"] in fica:
                dentro.append((lig, c["poi"], fica[c["poi"]]))
                placar["fica: " + fica[c["poi"]]] += 1
            else:
                motivo = rv.motivo_da_recusa(c)
                placar["cai: " + _familia(motivo)[:52]] += 1
                fora.append((lig, c["poi"], motivo))
        if not fica:
            ligs_que_zeram += 1

    print()
    for k in sorted(placar):
        print("   %-56s %8d" % (k, placar[k]))
    print()
    _log("%d vínculos a descartar · %d ligações ficam sem prova nenhuma"
         % (len(fora), ligs_que_zeram))

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    # O ROWCOUNT É A MEDIDA, E NÃO A AUSÊNCIA DE EXCEÇÃO. Uma policy de RLS
    # com `using` filtra calada: o UPDATE atinge 0 linhas e retorna sucesso.
    from psycopg2.extras import execute_values
    # O PLACAR VEM DO BANCO, e nao de `cur.rowcount`.
    #
    # A primeira versao somava `cur.rowcount` depois de cada `execute_values`
    # com `page_size=1000`. Isso e errado e me enganou na primeira corrida:
    # `execute_values` com pagina menor que o lote roda VARIOS comandos, e o
    # `rowcount` guarda so o da ultima pagina. Cada lote de 5.000 se declarava
    # 1.000, o guarda acusou "pedi 263.415 e o banco marcou 52.415", e o banco
    # tinha gravado os 263.415 — conferido na tabela logo depois.
    #
    # Guarda que grita a toa e pior do que guarda nenhuma: ensina a ignorar o
    # alarme. Entao a medida agora e a pergunta direta ao banco.
    # ── O VINCULO DE POI FUNDIDO, que a consulta principal nao enxerga ──
    _log("descartando vínculos de POI fundido...")
    cur.execute(SQL_FUNDIDOS)
    _log("   %d vínculo(s) de POI que virou copia" % cur.rowcount)
    con.commit()

    # ── POR QUE CADA UM QUE FICOU, FICOU ──────────────────────────────────
    # Ate 10/09/2026 so o descarte deixava rastro. Ver a migracao 0091.
    for i in range(0, len(dentro), 5000):
        execute_values(cur, """
            update radar_comercial.ligacao_poi lp
               set aceito_por = v.motivo
              from (values %s) as v(ligacao, poi_id, motivo)
             where lp.ligacao = v.ligacao
               and lp.poi_id = v.poi_id::bigint
               and lp.descartado_em is null
        """, dentro[i:i + 5000], page_size=1000)
        con.commit()
    _log("%d vínculos ficaram, com o motivo do aceite gravado" % len(dentro))

    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where descartado_por = 'regra_vinculo'""")
    marcados_antes = int(cur.fetchone()[0] or 0)
    gravados = 0
    for i in range(0, len(fora), 5000):
        lote = fora[i:i + 5000]
        execute_values(cur, """
            update radar_comercial.ligacao_poi lp
               set descartado_em = now(),
                   descartado_motivo = v.motivo,
                   descartado_por = 'regra_vinculo'
              from (values %s) as v(ligacao, poi_id, motivo)
             where lp.ligacao = v.ligacao
               and lp.poi_id = v.poi_id::bigint
               and lp.descartado_em is null
        """, lote, page_size=1000)
        con.commit()
        gravados = min(i + 5000, len(fora))
        _log("   %d/%d enviados" % (gravados, len(fora)))

    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where descartado_por = 'regra_vinculo'""")
    marcados = int(cur.fetchone()[0] or 0) - marcados_antes
    _log("o banco marcou %d descartes" % marcados)
    if marcados != len(fora):
        _log("ATENÇÃO: pedi %d descartes e o banco marcou %d. Diferença de %d "
             "— conferir RLS (uma policy com `using` filtra calada) ou vínculo "
             "já descartado por outro."
             % (len(fora), marcados, len(fora) - marcados))

    # OS POIs QUE FICARAM SEM LIGAÇÃO NENHUMA. São os que vão à fila de
    # alocação — decisão do dono do produto: "devem buscar uma nova que os
    # aceite, ficando como POI a alocar só se não achar".
    cur.execute("""
        select count(*) from radar_comercial.pois p
         where p.fundido_em is null
           and exists (select 1 from radar_comercial.ligacao_poi lp
                        where lp.poi_id = p.id and lp.descartado_em is not null)
           and not exists (select 1 from radar_comercial.ligacao_poi lp
                            where lp.poi_id = p.id and lp.descartado_em is null)
    """)
    _log("%d POIs perderam toda a ligação e vão procurar outra"
         % int(cur.fetchone()[0] or 0))

    # `pois.id_ligacao_base` APONTAVA PARA UM VÍNCULO QUE MORREU. Sem isto o
    # mapa e a ficha continuam mostrando a ligação errada para esses POIs.
    cur.execute("""
        update radar_comercial.pois p
           set id_ligacao_base = v.ligacao, id_base = v.id_base
          from (select distinct on (poi_id) poi_id, id_base, ligacao
                  from radar_comercial.ligacao_poi
                 where descartado_em is null
                 order by poi_id, confianca desc, metros nulls last) v
         where p.id = v.poi_id
           and p.id_ligacao_base is distinct from v.ligacao
    """)
    _log("%d POIs tiveram a ligação principal trocada" % cur.rowcount)
    cur.execute("""
        update radar_comercial.pois p
           set id_ligacao_base = null
         where p.id_ligacao_base is not null
           and not exists (select 1 from radar_comercial.ligacao_poi lp
                            where lp.poi_id = p.id and lp.descartado_em is null)
    """)
    _log("%d POIs ficaram sem ligação principal" % cur.rowcount)
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
