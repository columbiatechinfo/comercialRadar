# -*- coding: utf-8 -*-
"""Quanta evidência sustenta o vínculo de cada ligação. O sistema mede.

O SISTEMA NÃO DECIDE. Regra do dono do produto em 09/09/2026: "quem decide é o
cliente; o sistema só mostra os dados e dá uma flag em cada um, e aí ele filtra
a combinação que para ele passa a ser adequada de alvos a converter".

Por isso este módulo não tem corte, nem limiar, nem "aprovado por score". Ele
grava as PARCELAS e as FLAGS, e a tela filtra. Um operador que só confia em
prova física pede "Street View conclusivo e menos de 10 m"; outro que caça
delivery pede "avaliação recente" e não liga para a fachada. Com um número
único gravado, nenhuma das duas perguntas teria resposta.

OS PESOS SÃO DO DONO DO PRODUTO, escritos como ele os passou:

    distância < 10 m ..................................... 15
    mesmo telhado ......................................... 5
    mesmo telhado + telhado comercial .................... 10
    Street View confirma presença EXATA, recente ......... 20
    Street View confirma presença EXATA, antiga ........... 5
    Street View confirma comércio, não exata, recente ..... 5
    Street View confirma comércio, não exata, antiga ...... 2
    avaliações recentes (<= 1 ano) ....................... 10
    fotos do Google validadas pela IA .................... 10
    posts em rede social (<= 6 meses) .................... 15

    teto: 80

────────────────────────────────────────────────────────────────────────────
O QUE "RECENTE" SIGNIFICA EM CADA SINAL, E POR QUE NÃO É O MESMO

Street View: a data está em `poi_evidencia.data_imagem`, e é a data da FOTO.
Um ano contado de hoje.

Avaliações: o Google não publica data de calendário — publica "5 meses atrás".
A coleta guardou o texto. Para virar data falta a âncora, que é
`pois.detalhado_em`: quando a página foi lida. `avaliar_ia._dias_atras` já faz
essa conta, e é ele que se usa aqui em vez de uma segunda implementação.

Fotos do Google: NENHUMA das 55.649 tem data no banco. Então esta parcela não
pode ser sobre recência — ela é sobre a IA ter olhado a foto e dito que ela
mostra o negócio funcionando. O nome do critério fala em "recentes"; o dado não
permite, e fingir que permite seria pontuar por um teste que não acontece.

Rede social: nasce ZERADA. Em 09/09/2026 o Instagram passou a redirecionar o
perfil para login em 5 de 5 IPs dos dois únicos blocos /24 do pool. A parcela
existe implementada para que ligar o sinal seja carregar dado, não mexer na
fórmula.
"""
import argparse
import json
import time

import base_comum as bc
import avaliar_ia as ia

#: Um ano, em dias. O corte do dono do produto para "recente".
UM_ANO = 365
#: Seis meses, para rede social.
SEIS_MESES = 182

PESOS = {
    "perto_10m": 15,
    "telhado": 5,
    "telhado_comercial": 10,
    "sv_exata_recente": 20,
    "sv_exata_antiga": 5,
    "sv_comercial_recente": 5,
    "sv_comercial_antiga": 2,
    "avaliacoes": 10,
    "fotos": 10,
    "rede": 15,
}

def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _sv_recente(ano_visadas):
    """A visada e do ultimo ano?"""
    if not ano_visadas:
        return False
    try:
        return (time.localtime().tm_year - int(str(ano_visadas)[:4])) <= 1
    except (TypeError, ValueError):
        return False


#: Os tres status oficiais do sistema, escritos como a base cadastral os
#: escreve. Ver a migracao 0093.
SIM = "SIM"
SIM_HUMANO = "SIM_COM_ANALISE_HUMANA"
NAO = "NAO"


#: A fonte que so existe se ALGUEM ESTEVE LA.
#:
#: Maps e iFood dependem de gente: avaliacao escrita, foto tirada, pedido
#: entregue. Receita, Estadual, IBGE, Cadastur e Airbnb sao CADASTRO — dizem
#: que alguem se registrou naquele endereco, nao que ha comercio na porta. A
#: distincao ja e' velha neste projeto: um CNPJ no endereco residencial e' o
#: falso positivo classico, e por isso a Receita nem entra no mapa por padrao.
FONTES_CONCLUSIVAS = ("maps", "ifood")


#: O NOME QUE E, NA VERDADE, UM DOCUMENTO.
#:
#: A Receita registra o MEI e o empresario individual pelo nome da PESSOA, com
#: o CNPJ na frente ou o CPF atras: "12.345.678 FULANO DE TAL", "FULANA DE TAL
#: 98765432100". Medido em 10/09/2026: 9.591 das 21.727 ligacoes candidatas de
#: Canoas — 44,1% — se apoiam SO em registros assim.
#:
#: ISSO NAO E COMERCIO NA PORTA, e tambem nao e nada. E um CNPJ registrado
#: naquele endereco: pode ser a costureira que atende em casa, o caminhoneiro
#: que so tem o carro, ou a loja de verdade que nunca trocou a razao social.
#: A diferenca entre os tres nao esta em nenhum dado que temos.
#:
#: Decisao do dono do produto: quando a ligacao esta marcada SIM na tabela
#: cadastral e o que a sustenta e so isso, ela vira SIM_COM_ANALISE_HUMANA —
#: continua alvo, mas alguem olha antes de alguem ir.
import re as _re

_DOCUMENTO_NO_NOME = _re.compile(r"(^\s*\d[\d.\-/]{7,}|\d[\d.\-/]{7,}\s*$)")


def parece_mei(nome):
    """O nome do POI carrega o documento colado, do jeito da Receita?"""
    return bool(_DOCUMENTO_NO_NOME.search(str(nome or "")))


def status_oficial(veredito, tem_telhado, tem_conclusiva, so_mei=False):
    """`(status, motivo)` — a flag que o cliente filtra na aba inicial.

    O SISTEMA NAO DECIDE, ele sinaliza. Regra do dono do produto em 09/09/2026:
    "quem decide e o cliente, o sistema so mostra os dados e da uma flag em
    cada um na aba inicial". Por isso a flag e' um recorte, nao um veto: o
    SIM_COM_ANALISE_HUMANA "e igual ao sim, mas o usuario consegue filtrar o
    que deseja ver e aplicar em campo".

    O QUE REBAIXA UM SIM. Regra do dono do produto em 10/09/2026: "quando for
    de fonte inconclusiva, tipo airbnb, e nao tiver no mesmo telhado da
    instalacao". As duas coisas juntas — uma so nao rebaixa.

    A LOGICA DAS DUAS. Sao dois jeitos independentes de provar que ha comercio
    NAQUELA porta, e a ligacao precisa de um deles: ou alguem esteve la (Maps,
    iFood) ou os dois pontos caem sobre a mesma construcao (telhado). Cadastro
    sem telhado nao tem nenhum dos dois: e' um registro num endereco de texto,
    que e' exatamente como o CNPJ de fundo de quintal aparece.

    A PRIMEIRA VERSAO DESTA FUNCAO REBAIXAVA PELO VINCULO DE NOME, e dava zero
    sempre. O motivo e' estrutural: o criterio de nome exige uma ANCORA, e a
    ancora e' um vinculo de endereco exato da MESMA ligacao. O vinculo por nome
    e' testemunha a mais, nunca a unica — logo nao existe ligacao sustentada so
    por ele, e a condicao nao podia acontecer.
    """
    if (veredito or "").startswith("reprovado"):
        return (NAO, "a IA nao viu comercio nas fontes nem na fachada")
    if so_mei:
        return (SIM_HUMANO,
                "toda a evidencia e CNPJ de pessoa fisica registrado no "
                "endereco: pode ser comercio, pode ser so o registro")
    if not tem_conclusiva and not tem_telhado:
        return (SIM_HUMANO,
                "so cadastro (Receita, Estadual, IBGE) e nenhum vinculo no "
                "mesmo telhado da ligacao: ninguem esteve la nem a geometria "
                "confirma a porta")
    return (SIM, "endereco exato, e a evidencia tem presenca fisica ou telhado")


def pontuar(linha):
    """Devolve (parcelas, flags, detalhe) de UMA ligacao."""
    (lig, presenca, fotos_ok, ano_visadas, metros, telhado, telhado_com,
     pois, dias_aval, dias_post) = linha
    p = {"p_distancia": 0, "p_telhado": 0, "p_streetview": 0,
         "p_avaliacoes": 0, "p_fotos": 0, "p_rede": 0}
    f = {k: False for k in ("perto_10m", "mesmo_telhado", "telhado_comercial",
                            "sv_exata", "sv_comercial", "sv_recente",
                            "aval_recente", "fotos_validadas", "rede_recente")}
    d = {}

    if metros is not None and metros < 10:
        p["p_distancia"] = PESOS["perto_10m"]
        f["perto_10m"] = True
        d["distancia_m"] = round(float(metros), 1)

    # O TELHADO COMERCIAL SUBSTITUI o telhado simples, nao soma: sao 5 ou 10,
    # e nao 15. E a leitura literal da regra — "mesmo telhado = 5; mesmo
    # telhado + telhado comercial = 10".
    if telhado and telhado_com:
        p["p_telhado"] = PESOS["telhado_comercial"]
        f["mesmo_telhado"] = f["telhado_comercial"] = True
    elif telhado:
        p["p_telhado"] = PESOS["telhado"]
        f["mesmo_telhado"] = True

    recente = _sv_recente(ano_visadas)
    f["sv_recente"] = recente
    pres = (presenca or "").strip().lower()
    if pres == "exata":
        f["sv_exata"] = True
        p["p_streetview"] = (PESOS["sv_exata_recente"] if recente
                             else PESOS["sv_exata_antiga"])
    elif pres == "comercial":
        f["sv_comercial"] = True
        p["p_streetview"] = (PESOS["sv_comercial_recente"] if recente
                             else PESOS["sv_comercial_antiga"])
    if pres:
        d["presenca_na_foto"] = pres
        d["ano_das_visadas"] = ano_visadas

    if dias_aval is not None and dias_aval <= UM_ANO:
        p["p_avaliacoes"] = PESOS["avaliacoes"]
        f["aval_recente"] = True
        d["avaliacao_ha_dias"] = int(dias_aval)

    if str(fotos_ok).strip().lower() in ("true", "t", "1", "sim"):
        p["p_fotos"] = PESOS["fotos"]
        f["fotos_validadas"] = True

    if dias_post is not None and dias_post <= SEIS_MESES:
        p["p_rede"] = PESOS["rede"]
        f["rede_recente"] = True
        d["post_ha_dias"] = int(dias_post)

    return p, f, d, pois


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calcula o score de cada ligação")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("select core.empresa_atual()")
    empresa = cur.fetchone()[0]

    # TRES CONSULTAS, e nao uma por ligacao. A conta de "ha quantos dias" vive
    # no Python — `ia._dias_atras`, a mesma que o dossie usa — e registra-la
    # como funcao do banco criaria a segunda implementacao da mesma regra. A
    # saida e trazer os tres conjuntos inteiros e cruzar em memoria: sao
    # dezenas de milhares de linhas, nao milhoes.
    _log("lendo as ligações julgadas...")
    cur.execute("""
        select v.ligacao,
               v.percepcao->'resposta'->>'presenca_na_foto',
               v.percepcao->'resposta'->>'fotos_do_google_validam',
               v.percepcao->>'ano_das_visadas',
               min(lp.metros), bool_or(lp.mesmo_telhado),
               bool_or(lp.telhado_comercial), count(distinct lp.poi_id),
               v.veredito,
               -- ALGUMA FONTE PROVA QUE ALGUEM ESTEVE LA? Isso, com
               -- `bool_or(lp.mesmo_telhado)` acima, separa o SIM do
               -- SIM_COM_ANALISE_HUMANA. Ver `status_oficial`.
               bool_or(lower(coalesce(p.fonte,'')) in %(conclusivas)s),
               bool_and(coalesce(p.nome,'') ~ '(^[[:space:]]*[0-9][0-9.\-/]{7,}|[0-9][0-9.\-/]{7,}[[:space:]]*$)')
          from radar_comercial.ligacao_veredito v
          join radar_comercial.ligacao_poi lp
            on lp.ligacao = v.ligacao and lp.descartado_em is null
          join radar_comercial.pois p on p.id = lp.poi_id
         group by 1,2,3,4,9""", {"conclusivas": FONTES_CONCLUSIVAS})
    base = cur.fetchall()
    _log("   %d ligação(ões)" % len(base))
    if not base:
        _log("nenhuma ligação julgada — rode o julgamento antes")
        return 0

    # AS AVALIACOES, numa consulta so para todas. Trazer o texto relativo e
    # a ancora, e converter aqui com `ia._dias_atras` — a mesma funcao que o
    # dossie usa, para nao existirem duas contas de "ha quanto tempo".
    _log("lendo as avaliações...")
    cur.execute("""
        select lp.ligacao, cm.data, p.detalhado_em
          from radar_comercial.ligacao_poi lp
          join radar_comercial.comentarios cm on cm.poi_id = lp.poi_id
          join radar_comercial.pois p on p.id = lp.poi_id
         where lp.descartado_em is null and cm.data is not null""")
    dias_por_lig = {}
    for lig, texto, ancora in cur:
        dias = ia._dias_atras(texto)
        if dias is None:
            continue
        # A ANCORA IMPORTA: "5 meses atras" lido ha um ano sao 17 meses hoje.
        if ancora:
            dias += max(0, (time.time() - ancora.timestamp()) / 86400.0)
        atual = dias_por_lig.get(lig)
        if atual is None or dias < atual:
            dias_por_lig[lig] = dias
    _log("   %d ligação(ões) com avaliação datável" % len(dias_por_lig))

    _log("lendo a rede social...")
    cur.execute("""
        select lp.ligacao,
               extract(day from now() - rs.ultimo_post)::int
          from radar_comercial.ligacao_poi lp
          join radar_comercial.pois p on p.id = lp.poi_id
          join radar_comercial.rede_social rs
            on rs.arroba = lower(btrim(regexp_replace(
                 coalesce(p.instagram,''),
                 '^(https?://)?(www\\.)?instagram\\.com/|/.*$|^@', '', 'g')))
         where lp.descartado_em is null and rs.ultimo_post is not null""")
    post_por_lig = {}
    for lig, dias in cur:
        atual = post_por_lig.get(lig)
        if atual is None or dias < atual:
            post_por_lig[lig] = dias
    _log("   %d ligação(ões) com post datado" % len(post_por_lig))

    linhas, faixas, por_status = [], {}, {}
    for (lig, pres, fotos, ano, metros, tel, telc, pois, ver, viva,
         so_mei) in base:
        p, f, d, n = pontuar((lig, pres, fotos, ano, metros, tel, telc, pois,
                              dias_por_lig.get(lig), post_por_lig.get(lig)))
        total = sum(p.values())
        faixa = (total // 10) * 10
        faixas[faixa] = faixas.get(faixa, 0) + 1
        st = status_oficial(ver, tel, viva, bool(so_mei))
        por_status[st[0]] = por_status.get(st[0], 0) + 1
        linhas.append((empresa, lig, p["p_distancia"], p["p_telhado"],
                       p["p_streetview"], p["p_avaliacoes"], p["p_fotos"],
                       p["p_rede"], f["perto_10m"], f["mesmo_telhado"],
                       f["telhado_comercial"], f["sv_exata"], f["sv_comercial"],
                       f["sv_recente"], f["aval_recente"], f["fotos_validadas"],
                       f["rede_recente"], json.dumps(d), n) + st)

    # O PLACAR DOS STATUS VAI NO ENSAIO, e nao so depois de gravar. Ele e' o
    # unico numero que diz se a regra do SIM_COM_ANALISE_HUMANA mudou alguma
    # coisa — a distribuicao do score nao se mexe quando ela muda.
    _log("")
    _log("POR FLAG OFICIAL:")
    for k in ("SIM", "SIM_COM_ANALISE_HUMANA", "NAO"):
        _log("   %-24s %6d" % (k, por_status.get(k, 0)))

    _log("")
    _log("DISTRIBUIÇÃO DO SCORE (teto 80):")
    for faixa in sorted(faixas, reverse=True):
        _log("   %2d-%2d  %6d  %s" % (faixa, faixa + 9, faixas[faixa],
                                      "█" * min(40, faixas[faixa] // 40)))

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    from psycopg2.extras import execute_values
    cur.execute("delete from radar_comercial.ligacao_score")
    execute_values(cur, """
        insert into radar_comercial.ligacao_score
            (id_empresa, ligacao, p_distancia, p_telhado, p_streetview,
             p_avaliacoes, p_fotos, p_rede, perto_10m, mesmo_telhado,
             telhado_comercial, sv_exata, sv_comercial, sv_recente,
             aval_recente, fotos_validadas, rede_recente, detalhe, pois,
             status, status_motivo)
        values %s
    """, linhas, page_size=1000)
    con.commit()
    cur.execute("select count(*), max(total), round(avg(total),1) "
                "from radar_comercial.ligacao_score")
    n, maxi, media = cur.fetchone()
    _log("%d score(s) gravados · maior %s · média %s" % (n, maxi, media))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
