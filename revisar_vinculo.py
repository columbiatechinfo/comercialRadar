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

UM POI, UMA LIGAÇÃO; UMA LIGAÇÃO, VÁRIOS POIs — o último passo, desde
11/09/2026. Vários POIs podem compor a mesma ligação (galeria, sobrado,
shopping); o mesmo POI nunca fica em duas. Regra do dono do produto, dita três
vezes: "uma ligação é uma casa física/prédio". A "Doces da Rafa" estava em 19
ligações do mesmo prédio, e a IA aprovaria as 19 por causa de uma padaria.

QUEM DECIDE É A IA, na avaliação. Decisão do dono do produto em 11/09/2026:
"você envia todos os candidatos; somente caso a IA não decida qual é o correto
pra ligação é que você usa esses critérios". Por isso este passo vem DESLIGADO
e só roda com `--um-poi-por-ligacao`, depois da avaliação. Até lá, cada
ligação leva todos os POIs que passam na regra, e o mesmo POI pode estar em
várias — como candidato, não como vínculo decidido.

Quando ligado, entre as ligações que aceitam o POI, ele fica, nesta ordem:
  1. na de critério mais forte (endereço exato antes de nome de âncora);
  2. na que tem a MESMA UNIDADE — "CASA 12" no complemento da Receita e no
     endereço da ligação na Corsan. Num condomínio de casas é o único
     desempate que não é sorteio;
  3. na mais próxima. A distância não recusa ninguém: só desempata.
"""
import argparse
import re
import time
from collections import Counter, defaultdict

import base_comum as bc
import regra_vinculo as rv
import bairro as bz

#: A ordem dos criterios quando o mesmo POI e aceito por varias ligacoes.
FORCA_DO_ACEITE = {"endereco_exato": 0, "nome_de_ancora": 1,
                   "airbnb_rua_telhado": 2, "airbnb_telhado_nome": 3}

SQL = """
select lp.ligacao, lp.poi_id, lp.mesmo_endereco, lp.mesmo_numero,
       lp.mesmo_telhado, lp.metros, coalesce(p.nome,''),
       coalesce(lr.numero,''), coalesce(c.nro,''),
       coalesce(c.nom_bairro,''), coalesce(lr.bairro,''),
       coalesce(lr.forca,'') = 'prova' as num_e_prova,
       -- A FONTE DECIDE SE A REGRA ESTRITA VALE. O Airbnb nao publica numero
       -- de porta — e do desenho da plataforma —, e exigi-lo dele excluiria a
       -- fonte inteira. Ver `regra_vinculo.SEM_ENDERECO_EXATO`.
       coalesce(p.fonte,''),
       -- 11/09/2026: a ligacao precisa estar marcada SIM, e da Receita so o
       -- estabelecimento ativo conta.
       coalesce(c.qualificacao,'') like 'SIM%%'{nao},
       lower(coalesce(p.fonte,'')) = 'receita'
         and ltrim(coalesce(rd.situacao_cadastral,''),'0') <> '2',
       c.cod_latitude::float8, c.cod_longitude::float8,
       coalesce(rd.complemento,''), coalesce(c.end_ligacao,'')
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join resources_root.cadastro_corsan c on c.num_ligacao::text = lp.ligacao
  left join radar_comercial.logradouro_resolvido lr on lr.poi_id = lp.poi_id
  left join radar_comercial.receita_data rd on rd.poi_id = lp.poi_id
 where lp.descartado_em is null
   and p.fundido_em is null
   {fora_nao}
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

#: O MESMO DESCARTE, PAR A PAR, quando a ligação NAO fica fora da rodada (dono do produto, 17/09/2026): os pares vêm
#: para o Python, saem os de ligação NAO (`validacao.ligacoes_nao`) e o motivo é o de `SQL_FUNDIDOS`, letra por letra.
MOTIVO_FUNDIDO = "o POI foi fundido em outro; quem vale e o sobrevivente, nao a copia"
SQL_FUNDIDOS_PARES = """
select lp.ligacao, lp.poi_id
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
 where p.fundido_em is not null
   and lp.descartado_em is null
"""
SQL_FUNDIDOS_POR_PAR = """
update radar_comercial.ligacao_poi lp
   set descartado_em = now(),
       descartado_motivo = v.motivo,
       descartado_por = 'regra_vinculo'
  from (values %s) as v(ligacao, poi_id, motivo)
 where lp.ligacao = v.ligacao
   and lp.poi_id = v.poi_id::bigint
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
    p.add_argument("--qualificacoes", default="",
                   help="as qualificações da coleta do processo, separadas por vírgula: SIM e "
                        "SIM_COM_ANALISE_HUMANA ficam sempre; NAO só quando listado — sem ele, o vínculo de "
                        "ligação NAO não é tocado (a marcação da IA, 17/09/2026)")
    p.add_argument("--um-poi-por-ligacao", dest="um_poi", action="store_true",
                   help="deixa cada POI numa ligacao so, pelo desempate do "
                        "cabecalho. E o RESERVA da IA: rodar depois da "
                        "avaliacao, para o que ela nao decidiu")
    a = p.parse_args(argv)
    # A COLETA DA MARCAÇÃO DA IA (dono do produto, 17/09/2026): a ligação NAO só é apta quando o processo marcou o NÃO
    # para a IA. Sem `--qualificacoes`, a consulta é a de sempre.
    import validacao as va
    try:
        coleta = va.coleta_de(va.ler_qualificacoes(a.qualificacoes, padrao=va.COLETA_SEMPRE))
    except ValueError as e:
        p.error(str(e))
    nao = " or coalesce(c.qualificacao,'') = 'NAO'" if "NAO" in coleta else ""
    # A LIGAÇÃO NAO FORA DA COLETA FICA INTOCADA (dono do produto, 17/09/2026): sem o NÃO na coleta, o vínculo de
    # ligação NAO nem entra na leitura — não é aceito, não ganha `aceito_por`, não disputa POI e não é descartado
    # (antes caía por "a ligacao nao esta marcada SIM"). SIM e SIM com análise humana: as mesmas linhas de sempre.
    fora_nao = "" if "NAO" in coleta else "and coalesce(c.qualificacao,'') <> 'NAO'"

    con = bc.conectar()
    cur = con.cursor()
    # A TABELA DE RARIDADE PRIMEIRO. `regra_vinculo.parecidos` recusa a
    # trabalhar sem ela — de proposito: sem os pesos o criterio 4 casaria
    # "Pizzaria" com "Pizzaria" e o vazamento voltaria por ali.
    _log("pesando os nomes de POI da cidade...")
    _log("   %d tokens" % rv.carregar_pesos(con, a.cidade))
    filtro = " and upper(coalesce(c.cidade,'')) = upper(%s)" if a.cidade else ""
    _log("lendo os vínculos%s..." % (" de " + a.cidade if a.cidade else ""))
    if nao:
        _log("   o processo marcou o NÃO para a IA: a ligação NAO também é apta")
    else:
        _log("   ligação NAO fica fora desta revisão: sem o NÃO na coleta, o vínculo dela não é tocado")
    cur.execute(SQL.format(cidade=filtro, nao=nao, fora_nao=fora_nao),
                (a.cidade,) if a.cidade else ())

    # O BAIRRO DA LIGACAO, quando a Corsan nao o escreveu ("BAIRRO NAO
    # INFORMADO" em 1.244 ligacoes de Canoas): o da coordenada do hidrometro,
    # pela mesma regra que vale para o POI. Uma consulta por ligacao.
    bairro_rev = {}

    def _bairro_lig(lig, b, la, lo):
        if rv.bairro_util(b):
            return b
        if lig not in bairro_rev:
            bairro_rev[lig] = bz.bairro_reverso(la, lo, a.cidade) or ""
        return bairro_rev[lig]

    por_lig = defaultdict(list)
    metros_de, unidade_bate = {}, set()
    for (lig, poi, m_end, m_num, m_tel, metros, nome, n_poi, n_lig,
         b_lig, b_poi, prova, fonte, apta, inativa, la, lo, compl,
         end_lig) in cur:
        metros_de[(lig, poi)] = metros
        if compl and rv.complemento_bate(compl, end_lig):
            unidade_bate.add((lig, poi))
        por_lig[lig].append({
            "poi": poi, "fonte": fonte,
            "mesma_rua": bool(m_end), "mesmo_numero": bool(m_num),
            "mesmo_telhado": bool(m_tel), "metros": metros, "nome": nome,
            "bairro_lig": _bairro_lig(lig, b_lig, la, lo), "bairro_poi": b_poi,
            "lig_apta": bool(apta), "receita_inativa": bool(inativa),
            "contradiz": rv.contradiz_numero(n_poi, n_lig, prova)})
    _log("%d ligações sem bairro na Corsan consultadas na coordenada"
         % len(bairro_rev))
    _log("%d ligações · %d vínculos"
         % (len(por_lig), sum(len(v) for v in por_lig.values())))

    fora, aceitos, placar = [], [], Counter()
    for lig, cands in por_lig.items():
        fica = rv.aceitar(cands)
        for c in cands:
            if c["poi"] in fica:
                aceitos.append((lig, c["poi"], fica[c["poi"]]))
            else:
                motivo = rv.motivo_da_recusa(c)
                placar["cai: " + _familia(motivo)[:52]] += 1
                fora.append((lig, c["poi"], motivo, "regra_vinculo"))

    # UM POI, UMA LIGACAO. Ver o cabecalho para a ordem do desempate. Sem
    # distancia medida, a de numero menor — so para o resultado nao depender
    # da ordem de leitura.
    por_poi = defaultdict(list)
    for (lig, poi, motivo) in aceitos:
        por_poi[poi].append((lig, motivo))
    dentro = []
    desempate = Counter()
    if not a.um_poi:
        dentro = list(aceitos)
        for (_, _, motivo) in aceitos:
            placar["fica: " + motivo] += 1
        multi = sum(1 for ops in por_poi.values() if len(ops) > 1)
        _log("   %d POIs sao candidatos de mais de uma ligacao — a IA decide "
             "(sem --um-poi-por-ligacao, nenhum sai)" % multi)
        por_poi = {}
    for poi, ops in por_poi.items():
        ops.sort(key=lambda x: (FORCA_DO_ACEITE.get(x[1], 9),
                                (x[0], poi) not in unidade_bate,
                                metros_de.get((x[0], poi)) is None,
                                metros_de.get((x[0], poi)) or 0.0, x[0]))
        lig0, motivo0 = ops[0]
        dentro.append((lig0, poi, motivo0))
        placar["fica: " + motivo0] += 1
        if len(ops) > 1:
            desempate["pela unidade (complemento)" if (lig0, poi) in unidade_bate
                      else "pela distancia"] += 1
        m0 = metros_de.get((lig0, poi))
        for (lig, motivo) in ops[1:]:
            placar["cai: um POI, uma ligacao"] += 1
            fora.append((lig, poi,
                         "um POI, uma ligacao: fica na ligacao %s%s"
                         % (lig0, "" if m0 is None else " (a %d m)" % round(m0)),
                         "um_poi_uma_ligacao"))
    for k, n in desempate.most_common():
        _log("   POI em varias ligacoes, decidido %s: %d" % (k, n))
    ficam = {lig for (lig, _, _) in dentro}
    ligs_que_zeram = sum(1 for lig in por_lig if lig not in ficam)

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
    if not fora_nao:
        cur.execute(SQL_FUNDIDOS)
        _log("   %d vínculo(s) de POI que virou copia" % cur.rowcount)
    else:
        # A LIGAÇÃO NAO FORA DA COLETA FICA INTOCADA TAMBÉM AQUI (17/09/2026). O UPDATE é global; filtrar dentro dele
        # pedia um `not exists` por linha contra o cadastro. Os pares vêm para o Python e o descarte é par a par,
        # em comandos de até 5.000 (uma página por comando: o `rowcount` é o do comando inteiro).
        cur.execute(SQL_FUNDIDOS_PARES)
        pares = cur.fetchall()
        protegidas = va.ligacoes_nao(cur, {l for l, _ in pares})
        vao = [(l, p, MOTIVO_FUNDIDO) for l, p in pares if l not in protegidas]
        n = 0
        for i in range(0, len(vao), 5000):
            execute_values(cur, SQL_FUNDIDOS_POR_PAR, vao[i:i + 5000], page_size=5000)
            n += cur.rowcount
        _log("   %d vínculo(s) de POI que virou copia · %d de ligação NAO ficaram intocados"
             % (n, len(pares) - len(vao)))
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
                    where descartado_por in ('regra_vinculo', 'um_poi_uma_ligacao')""")
    marcados_antes = int(cur.fetchone()[0] or 0)
    gravados = 0
    for i in range(0, len(fora), 5000):
        lote = fora[i:i + 5000]
        execute_values(cur, """
            update radar_comercial.ligacao_poi lp
               set descartado_em = now(),
                   descartado_motivo = v.motivo,
                   descartado_por = v.por
              from (values %s) as v(ligacao, poi_id, motivo, por)
             where lp.ligacao = v.ligacao
               and lp.poi_id = v.poi_id::bigint
               and lp.descartado_em is null
        """, lote, page_size=1000)
        con.commit()
        gravados = min(i + 5000, len(fora))
        _log("   %d/%d enviados" % (gravados, len(fora)))

    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where descartado_por in ('regra_vinculo', 'um_poi_uma_ligacao')""")
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
