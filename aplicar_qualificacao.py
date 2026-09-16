# -*- coding: utf-8 -*-
"""A planilha de qualificação: marca quem é apta e insere quem falta.

NÃO É TROCA DE BASE. É conciliação: a planilha traz as mesmas ligações de
Canoas mais a coluna de decisão, e o que se quer é (a) marcar `apta_cruzamento`
em quem já existe e (b) inserir as que a base ainda não tem. Pedido do dono do
produto em 09/09/2026, textual: "adicionando as que faltam e dando update na
nova coluna".

POR QUE PASSA PELA TABELA CRUA. Podia-se ler o CSV em Python e disparar 102 mil
`update`. Seriam 102 mil viagens de ida e volta; o `COPY` é uma, e o casamento
vira um `join` que o Postgres resolve com índice. Mesma razão de
`carregar_base.py` existir.

O QUE "SIM" QUER DIZER. A coluna chega como texto livre — SIM, NAO, S, N, 1, 0,
true. O de-para está em `_verdade` e é deliberadamente restrito: o que não for
reconhecido vira NULL, e NULL não enriquece. Erro de preenchimento custa uma
ligação de fora da fila cara; um "sim" inventado custa extração paga.
"""
import argparse
import re
import os
import time

import base_comum as bc
import carregar_base as cb

TABELA = "canoas_v6"
SIM = {"sim", "s", "1", "true", "t", "y", "yes", "verdadeiro", "v"}
NAO = {"nao", "não", "n", "0", "false", "f", "no", "falso"}


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


#: A COLUNA DE DECISÃO VIRA BOOLEANO NO SQL, e não em Python.
#:
#: Trazer 102 mil valores para cá só para classificá-los e devolvê-los seria
#: pagar duas viagens pelo que o banco resolve numa expressão. O de-para fica
#: legível porque está escrito por extenso, e não escondido num `case` gerado.
#: O TEXTO DA PLANILHA VIRA UM DOS TRES STATUS OFICIAIS.
#:
#: Decisao do dono do produto em 09/09/2026: SIM, SIM_COM_ANALISE_HUMANA e NAO.
#: O segundo se comporta como o primeiro no pipeline — enriquece igual, o
#: veredito vale igual — e existe para o usuario FILTRAR o que quer ver e levar
#: a campo. Sao 37% da base; colapsa-lo em "sim" apagaria a pergunta que ele faz
#: depois.
#:
#: AS DUAS VARIANTES DE "NAO" VIRAM UMA SO. `NAO, SEM ECONOMIA FATURADA` nao e
#: outro destino: e o motivo de o `NAO` valer. O motivo vai para a coluna ao
#: lado, e nao para o status.
#:
#: A VIRGULA E EXIGIDA nos prefixos de proposito: sem ela, "SIMPLES" comecaria
#: com "sim" e viraria um sim.
#: O TEXTO NORMALIZADO ANTES DA DECISAO (dono do produto, 16/09/2026). A primeira regra exigia a virgula e a palavra
#: "analise": medido no teste, `Sim com revisao humana` ficava VAZIO (fora do cruzamento), `SIM, COM REVISAO HUMANA`
#: virava SIM puro e ate o codigo oficial `SIM_COM_ANALISE_HUMANA` ficava vazio. Agora caixa, acento, virgula e
#: sublinhado saem antes; "sim ... humana" (analise ou revisao) e o SIM com analise humana.
#:
#: SEM `%` E SEM CHAVES DE PROPOSITO: a expressao entra num SQL montado com `%` aqui e com `.format` no
#: `materializar_base`, e qualquer um dos dois quebraria.
_NORMAL = ("btrim(regexp_replace(translate(lower(coalesce(\"{col}\", '')), "
           "'áàâãäéèêëíìîïóòôõöúùûüç_', 'aaaaaeeeeiiiiooooouuuuc '), '[^a-z0-9]+', ' ', 'g'))")
STATUS = """
    case
      when {n} ~ '^sim .*humana'                                         then 'SIM_COM_ANALISE_HUMANA'
      when {n} in ('sim','s','1','true','t','y','yes','verdadeiro','v')  then 'SIM'
      when {n} ~ '^sim '                                                 then 'SIM'
      when {n} in ('nao','n','0','false','f','no','falso')               then 'NAO'
      when {n} ~ '^nao '                                                 then 'NAO'
      else null
    end
""".replace("{n}", _NORMAL)

#: O QUE VEIO COLADO A DECISAO. Nao muda o destino; explica a decisao, e e o
#: que alguem vai querer ler ao auditar por que uma ligacao ficou de fora.
#:
#: SO EXISTE MOTIVO SE HOUVER VIRGULA. A primeira versao usava `regexp_replace`
#: sozinho, e ele devolve o texto INTACTO quando o padrao nao casa: as 49.477
#: linhas `SIM` ficaram com motivo "SIM", e as 13.174 `NAO` com motivo "NAO".
#: Ruido que parece dado — pior do que campo vazio, porque quem le acredita.
MOTIVO = """
    case when position(',' in "{col}") > 0
         then nullif(btrim(regexp_replace("{col}", '^[^,]*,\\s*', '')), '')
    end
"""

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--arquivo", default="/tmp/canoas_v6.csv")
    p.add_argument("--coluna", default="APTA_CRUZAMENTO_COMERCIAL")
    p.add_argument("--cidade", default="",
                   help="so para o resumo; sem ela, o resumo e das cidades das ligacoes da planilha")
    # A TABELA CRUA DA CARGA (16/09/2026): a pela tela usa a propria, e a de Canoas fica como estava.
    p.add_argument("--tabela", default="")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    global TABELA
    TABELA = re.sub(r"[^a-z0-9_]", "", (a.tabela or TABELA).lower()) or "canoas_v6"

    # A CONEXAO E A DO PIPELINE, e nao a do `migrator`. Duas razoes, e as
    # duas foram medidas hoje:
    #
    # 1. `cadastro_corsan` tem `force row level security` e o `migrator` NAO
    #    tem `bypassrls`. Conectado por ele, `core.empresa_atual()` devolve
    #    NULL, a policy nega tudo e a tabela parece VAZIA: o primeiro ensaio
    #    anunciou "0 ligacoes de CANOAS na base" e "102.131 nao existem" com
    #    as 102.131 la dentro. Aplicado, teria inserido a base inteira
    #    duplicada.
    #
    # 2. E o conserto obvio — declarar o claim — e proibido aqui. GUC DE
    #    SESSAO NA 7110 VAZA: `realtime_ingest` documenta a medicao de
    #    31/08/2026, em que `set_config('request.jwt.claim.sub', ..., false)`
    #    naquela porta sujou o backend de forma permanente e dez conexoes
    #    seguintes herdaram a empresa do teste — inclusive as do painel, de
    #    outros clientes. Um vazamento entre clientes, calado.
    #
    # `bc.conectar()` ja resolve as duas: porta certa, identidade declarada do
    # jeito que o resto do pipeline declara.
    con = bc.conectar()
    cur = con.cursor()

    # ── 1 · a planilha vai para a tabela crua ────────────────────────────
    colunas_info, sep, amostra = cb.espiar(a.arquivo)
    if not colunas_info:
        raise SystemExit("arquivo sem cabeçalho")
    cab = [c["arquivo"] for c in colunas_info]
    _log("%d coluna(s) · separador %r" % (len(cab), sep))
    alvo = None
    for c in colunas_info:
        if c["arquivo"].strip().upper() == a.coluna.strip().upper():
            alvo = c["coluna"]
    if not alvo:
        raise SystemExit("não achei a coluna %r. Tem: %s"
                         % (a.coluna, ", ".join(cab[-6:])))
    _log("coluna de decisão: %s → %s" % (a.coluna, alvo))

    _log("COPY para base_bruta.%s ..." % TABELA)
    t0 = time.time()
    colunas, n = cb.carregar(con, a.arquivo, TABELA, cab, sep)
    _log("%d linha(s) em %.1f s" % (n, time.time() - t0))

    lig = None
    for c in colunas_info:
        if c["arquivo"].strip().upper() == "NUM_LIGACAO":
            lig = c["coluna"]
    if not lig:
        raise SystemExit("a planilha não tem NUM_LIGACAO")

    # ── 2 · o que ela diz, e o que a base já tem ─────────────────────────
    cur.execute("""
        with p as (
          select nullif(regexp_replace("%s", '[^0-9]', '', 'g'), '')::bigint as lig,
                 %s as status
            from base_bruta.%s
        )
        select count(*) as na_planilha,
               count(*) filter (where status = 'SIM') as s1,
               count(*) filter (where status = 'SIM_COM_ANALISE_HUMANA') as s2,
               count(*) filter (where status = 'NAO') as s3,
               count(*) filter (where status is null) as sem_decisao,
               count(*) filter (where c.num_ligacao is null) as faltam_na_base
          from p left join resources_root.cadastro_corsan c
                        on c.num_ligacao = p.lig
    """ % (lig, STATUS.format(col=alvo), TABELA))
    na_planilha, s1, s2, s3, vazio, faltam = cur.fetchone()
    sim, nao = s1 + s2, s3
    _log("")
    _log("A PLANILHA DIZ:")
    _log("   %7d linhas" % na_planilha)
    _log("   %7d SIM                      (enriquece)" % s1)
    _log("   %7d SIM_COM_ANALISE_HUMANA   (enriquece igual; filtrável)" % s2)
    _log("   %7d NAO" % s3)
    _log("   %7d sem decisão reconhecível (ficam NULL, não enriquecem)" % vazio)
    _log("   %7d NÃO EXISTEM na base hoje" % faltam)

    if a.cidade:
        cur.execute("""select count(*) from resources_root.cadastro_corsan
                        where upper(coalesce(cidade,'')) = upper(%s)""",
                    (a.cidade,))
        _log("   %7d ligações de %s na base hoje" % (cur.fetchone()[0], a.cidade))

    if vazio:
        cur.execute("""
            select distinct btrim("%s"), count(*) over () from base_bruta.%s
             where %s is null and coalesce(btrim("%s"),'') <> '' limit 8
        """ % (alvo, TABELA, STATUS.format(col=alvo), alvo))
        estranhos = [r[0] for r in cur.fetchall()]
        if estranhos:
            _log("   valores não reconhecidos: %s" % ", ".join(map(repr, estranhos)))

    # O GUARDA CONTRA O ENGANO QUE EU MESMO COMETI.
    #
    # "Todas as linhas faltam na base" nao e um resultado plausivel para uma
    # planilha da MESMA cidade que ja esta carregada — e a assinatura exata de
    # uma leitura filtrada por RLS, que devolve vazio sem erro. Aplicar nesse
    # estado insere a base inteira duplicada.
    if faltam and faltam == na_planilha:
        raise SystemExit(
            "TODAS as %d linhas aparecem como ausentes. Isso quase nunca e "
            "verdade: e o que uma leitura filtrada por RLS devolve. Conferir a "
            "conexao antes de aplicar — nao vou inserir a base duplicada."
            % faltam)

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    # ── 3 · o update da decisão ──────────────────────────────────────────
    _log("")
    _log("marcando `apta_cruzamento`...")
    cur.execute("""
        update resources_root.cadastro_corsan c
           set qualificacao = p.status, qualificacao_motivo = p.motivo
          from (select nullif(regexp_replace("%s", '[^0-9]', '', 'g'), '')::bigint as lig,
                       %s as status, %s as motivo
                  from base_bruta.%s) p
         where c.num_ligacao = p.lig
           and (c.qualificacao is distinct from p.status
                or c.qualificacao_motivo is distinct from p.motivo)
    """ % (lig, STATUS.format(col=alvo), MOTIVO.format(col=alvo), TABELA))
    _log("   %d ligação(ões) marcadas" % cur.rowcount)
    con.commit()

    # ── AS COLUNAS COMUNS TAMBEM ─────────────────────────────────────
    #
    # Medido antes de escrever, comparando a planilha v6 com a base coluna por
    # coluna nas 102.131 ligacoes:
    #
    #   iguais em tudo: logradouro, CEP, bairro, categoria, situacao, medidor,
    #                   coordenada, classificacao, subcategoria, faturamento
    #   `end_ligacao`:  "diverge" em 100%, e e SUFIXO — a base traz
    #                   "...CANOAS-RS-CEP:92440288" e a planilha para em
    #                   "...CANOAS-RS". A base tem MAIS, nao menos; sobrescrever
    #                   seria perder e chamar de atualizar.
    #   economias:      divergencia real — 5.320 em residencial, 802 em
    #                   comercial, 151 industrial, 105 publica.
    #
    # SO ONDE A PLANILHA DECLARA. Em 4.932 linhas ela vem VAZIA onde a base tem
    # valor; vazio nao corrige, apenas nao diz. `coalesce(novo, antigo)` guarda
    # o que a base ja sabia e aplica so o que a planilha afirma.
    #
    # POR QUE ISSO IMPORTA: `qtd_eco_com` alimenta a regra dura que reprova a
    # ligacao que JA paga tarifa comercial. Sao 808 ligacoes que passam de "ja
    # paga" para "nao paga" — voltam a ser alvo — e 114 no sentido oposto.
    _log("")
    # AS ECONOMIAS SAO OPCIONAIS (16/09/2026): a planilha que traz so a decisao gravava a qualificacao e parava com
    # erro aqui ("column economias_residencial does not exist"). Cada coluna que a planilha nao tem fica como a base.
    presentes = {c["coluna"] for c in colunas_info}
    eco = {k: ('nullif(regexp_replace("%s",\'[^0-9]\',\'\',\'g\'),\'\')::smallint' % k) if k in presentes else "null::smallint"
           for k in ("economias_residencial", "economias_comercial", "economias_industrial", "economias_publica")}
    if not any(k in presentes for k in eco):
        _log("a planilha não traz economias: só a qualificação foi atualizada")
        eco = None
    else:
        _log("atualizando as economias onde a planilha declara (%s)..."
             % ", ".join(k for k in eco if k in presentes))
    cur.execute("select 1" if eco is None else """
        update resources_root.cadastro_corsan c
           set qtd_eco_res = coalesce(p.res, c.qtd_eco_res),
               qtd_eco_com = coalesce(p.com, c.qtd_eco_com),
               qtd_eco_ind = coalesce(p.ind, c.qtd_eco_ind),
               qtd_eco_pub = coalesce(p.pub, c.qtd_eco_pub)
          from (select nullif(regexp_replace("%s",'[^0-9]','','g'),'')::bigint as lig,
                       %s as res, %s as com, %s as ind, %s as pub
                  from base_bruta.%s) p
         where c.num_ligacao = p.lig
           and (coalesce(p.res, c.qtd_eco_res) is distinct from c.qtd_eco_res
             or coalesce(p.com, c.qtd_eco_com) is distinct from c.qtd_eco_com
             or coalesce(p.ind, c.qtd_eco_ind) is distinct from c.qtd_eco_ind
             or coalesce(p.pub, c.qtd_eco_pub) is distinct from c.qtd_eco_pub)
    """ % ((lig,) + tuple(eco.values()) + (TABELA,)))
    if eco is not None:
        _log("   %d ligação(ões) com economias corrigidas" % cur.rowcount)
    con.commit()

    # SEM CIDADE, O RESUMO E DAS LIGACOES DA PLANILHA (16/09/2026), por cidade
    if a.cidade:
        cur.execute("""select coalesce(qualificacao,'(sem decisão)'), count(*)
                         from resources_root.cadastro_corsan
                        where upper(coalesce(cidade,'')) = upper(%s)
                        group by 1 order by 2 desc""", (a.cidade,))
    else:
        cur.execute("""select coalesce(c.cidade,'?') || ' · ' || coalesce(c.qualificacao,'(sem decisão)'), count(*)
                         from resources_root.cadastro_corsan c
                         join (select distinct nullif(regexp_replace("%s", '[^0-9]', '', 'g'), '')::bigint as lig
                                 from base_bruta.%s) p on p.lig = c.num_ligacao
                        group by 1 order by 2 desc""" % (lig, TABELA))
    _log("   na base agora:")
    for st, q in cur.fetchall():
        _log("      %-26s %7d" % (st, q))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
