# -*- coding: utf-8 -*-
"""validacao.py — a validação de uma cidade ou área desenhada, por tarefas no banco (dono do produto, 16/09/2026).

O QUE ERA. Tudo o que vem depois do vínculo — fotos para o Storage, foto de rua de
frente, leitura das placas, ficha do CNPJ no Serasa, busca web, conferência e o julgamento leve — rodava só pelo
`scripts/producao_canoas/orquestrador_julgamento.sh`, no cron do i9, com Canoas fixo e o estado em arquivos. Uma
cidade nova não rodava pela tela, e o notebook ficava parado.

O QUE É. Uma `validacao` (migração 0118) divide as ligações aptas da cidade ou da área em lotes, na ordem do dono do
produto (iFood, Google Maps, o resto; dentro de cada grupo SIM antes de SIM com análise humana), e cada lote ganha uma
tarefa por etapa. Os comandos são OS MESMOS do orquestrador de Canoas — nenhum script de etapa foi reescrito.

    uma etapa só entra na fila quando as que ela precisa, NO MESMO LOTE, terminaram (`ETAPAS[..].depende`);
    lotes diferentes andam em paralelo, e em máquinas diferentes (`validacao_executor.py`);
    o que é recurso de todos tem TETO GLOBAL, contado no banco na hora de pegar a tarefa (`pegar`):
        Google (fichas do Maps e foto de rua) — os 250 proxies estão todos numa /24, e duas coletas ao mesmo
            tempo não somam: dividem o mesmo limitador (medido em 06/09/2026);
        Serasa e busca web — um navegador Camoufox pesado cada;
        Spark — a soma das chamadas simultâneas declaradas pelas tarefas de IA;
        conexões do pooler — são 20 no total para i9, notebook, API e painel.

    python validacao.py criar --cidade CHUVISCA --area teste_chuvisca [--ia-qualificacoes SIM,SIM_COM_ANALISE_HUMANA,NAO]
    python validacao.py status [--id N]
    python validacao.py pausar|retomar|cancelar --id N
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_comum as bc  # noqa: E402

AMBIENTE = "desenvolvimento" if os.environ.get("RADAR_AMBIENTE", "").strip() == "desenvolvimento" else "producao"
LOTE = int(os.environ.get("RADAR_VALIDACAO_LOTE") or 600)
IMG = os.environ.get("RADAR_IMAGEM_MINERADOR") or "radar-comercial-minerador-worker"
IMGQ = os.environ.get("RADAR_IMAGEM_BUSCA") or "radar-busca-camoufox"

# OS TETOS GLOBAIS, de todas as máquinas e dos dois ambientes (o proxy, a Spark e o pooler são os mesmos).
TETO = {
    "google": int(os.environ.get("RADAR_TETO_GOOGLE") or 1),
    "cnpj": int(os.environ.get("RADAR_TETO_SERASA") or 1),
    "busca": int(os.environ.get("RADAR_TETO_BUSCA") or 1),
    "spark": int(os.environ.get("RADAR_TETO_SPARK") or 180),
    "conexoes": int(os.environ.get("RADAR_TETO_CONEXOES") or 8),
}
# O POOLER DE VERDADE (20 sessões para i9, notebook, APIs e painel): antes de soltar uma tarefa, as sessões abertas no
# banco são contadas — inclusive as dos laços do cron de Canoas, que não passam por aqui. Em 16/09/2026 eram 17 de 20
# com os laços rodando; em 15/09 um contêiner a mais estourou `EMAXCONNSESSION` e derrubou um julgamento.
POOL_TOTAL = int(os.environ.get("RADAR_POOL_TOTAL") or 20)
POOL_FOLGA = int(os.environ.get("RADAR_POOL_FOLGA") or 3)
TENTATIVAS = 3
SEM_SINAL_MIN = 3        # tarefa rodando sem sinal do executor por mais que isso volta para a fila


@dataclass(frozen=True)
class Etapa:
    ordem: int
    depende: tuple
    recurso: str            # google, cnpj, busca, spark, local
    imagem: str
    cmd: str
    conexoes: int = 1
    simultaneas: int = 0    # chamadas à Spark
    precisa_ia: bool = False
    root: bool = False      # o Xvfb só serve para root (ver TELA_VIRTUAL no server.py)
    escreve_repo: bool = False
    partes: int = 1
    shm: str = ""
    sonda: bool = False
    tolerante: bool = True  # como no orquestrador: o código de saída não segura o lote; a conferência é quem cobra


# `{arq}` é o arquivo do lote e `{pasta}` a pasta da validação, os dois vistos de dentro do contêiner (/o);
# `{k}` é a parte (0..partes-1).
ETAPAS = {
    # A ETAPA `fichas` SAIU EM 17/09/2026, por decisão do dono do produto, e o motivo está medido.
    #
    # Ela recoletava a ficha do Maps das ligações do lote. Para o POI que VEIO do Maps isso é refazer o que a etapa 4
    # da extração já fez, um por um, pelo id. Sobrava o POI de outra fonte — e buscá-lo no Maps POR NOME custa caro e
    # rende pouco: no ensaio de 17/09 (10 ligações, 20 POIs, sem gravar), 25,2 s por POI e só 2 acertos em 20. Em
    # Canoas o alvo seria de 24.103 POIs, ~42 h de navegador com 8 em paralelo para achar algo em 1 de cada 8.
    #
    # O julgamento não ficou sem prova: 97,9% das ligações julgadas tinham foto de rua e 99,99% tinham busca web.
    # `recoletar_fichas.py` continua no repositório para uso à mão.
    "storage": Etapa(2, (), "local", IMG, conexoes=1,
                     cmd="python -u imagens_para_storage.py --fotos"),
    # TRES PROCESSOS, um navegador cada: o render do Chromium no Xvfb trava num núcleo só (orquestrador de Canoas)
    "frente": Etapa(3, (), "google", IMG, conexoes=1, root=True, partes=3,
                    cmd="sh scripts/com_tela.sh python -u recapturar_frente.py --ligacoes-arquivo {arq} --abas 8 "
                        "--parte {k}/3 --saida {pasta}/{lote}_frente"),
    "leitura": Etapa(4, ("frente",), "spark", IMG, conexoes=2, simultaneas=60, precisa_ia=True,
                     cmd="python -u ler_fotos_de_rua.py --ligacoes-arquivo {arq} --simultaneas 60"),
    "cnpj": Etapa(5, (), "cnpj", IMGQ, conexoes=1, shm="2g",
                  cmd="python3 -u fichas_cnpj.py --ligacoes-arquivo {arq} --navegadores 3 --aplicar"),
    # A MARCAÇÃO DA IA DEPOIS DE `--ligacoes-arquivo` (17/09/2026): a sonda do executor é o comando cortado ali
    "busca": Etapa(6, (), "busca", IMGQ, conexoes=2, shm="8g", sonda=True,
                   cmd="python3 -u buscar_web.py --ligacoes-arquivo {arq} --trabalhadores 16 --aplicar "
                       "--qualificacoes {coleta}"),
    "conferencia": Etapa(7, ("storage", "leitura", "cnpj", "busca"), "local", IMG, conexoes=2,
                         cmd="python -u conferir_evidencias.py --ligacoes-arquivo {arq} --sem-data-apos-horas 24"),
    # a IA só julga as qualificações marcadas na validação; as outras vão direto para revisão humana (17/09/2026)
    "julgamento": Etapa(8, ("conferencia",), "spark", IMG, conexoes=4, simultaneas=120, precisa_ia=True,
                        tolerante=False,
                        cmd="python -u avaliar_enxuto.py --leve --ligacoes-arquivo {arq} --julgar-sem-foto "
                            "--trabalhadores 120 --aplicar --qualificacoes {qualificacoes} --validacao {validacao}"),
}
RECAPTURA = ("storage", "frente", "leitura", "cnpj", "busca")
#: Quem não depende de ninguém volta direto para a fila numa segunda passada; o resto espera a sua vez.
SEM_DEPENDENCIA = tuple(n for n, e in ETAPAS.items() if not e.depende)
GRUPOS = ("iFood", "Google Maps", "o resto")


# ──────────────────────────────────────────────────────────────── as qualificações que a IA pode tratar ──
# A IA SÓ TRATA O QUE FOI MARCADO (dono do produto, 17/09/2026). A tela de extração e a de validação ganharam três
# caixas — "A IA pode tratar as ligações com qualificação: SIM · SIM com análise humana · NÃO" — e a escolha fica
# gravada no processo (argumentos do job, parâmetros da validação), com quem marcou e quando. Dela saem duas regras:
#   COLETA (vínculo da etapa 8, ligações da validação, busca web): SIM e SIM com análise humana sempre; o NÃO só
#       quando marcado;
#   JULGAMENTO: a IA só julga a qualificação marcada; a coletada e não marcada vai direto para revisão humana, sem
#       parecer da IA (`avaliar_enxuto --qualificacoes`).
# O PADRÃO — SIM e SIM com análise humana marcados, NÃO de fora — é exatamente o comportamento de antes das caixas.
QUALIFICACOES = ("SIM", "SIM_COM_ANALISE_HUMANA", "NAO")
IA_PADRAO = ("SIM", "SIM_COM_ANALISE_HUMANA")
COLETA_SEMPRE = ("SIM", "SIM_COM_ANALISE_HUMANA")
ROTULO_QUALIFICACAO = {"SIM": "SIM", "SIM_COM_ANALISE_HUMANA": "SIM com análise humana", "NAO": "NÃO"}


def ler_qualificacoes(valor, padrao=IA_PADRAO):
    """As qualificações marcadas, na ordem de `QUALIFICACOES` e sem repetição. `valor` é a lista da tela ou o texto
    separado por vírgula da linha de comando; ausente (None ou texto vazio) é `padrao`.

    NADA PASSA SEM SER UM DOS TRÊS: o texto vai para dentro do `sh -c` do executor e decide o que a IA julga. Nome
    desconhecido, ou a lista sem nenhuma marcada, levanta ValueError — "nenhuma" na tela é quase sempre engano, e
    mandaria a validação inteira para revisão humana em silêncio."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return tuple(padrao)
    if isinstance(valor, str):
        itens = valor.split(",")
    elif isinstance(valor, (list, tuple, set, frozenset)):
        itens = list(valor)
    else:
        raise ValueError("qualificações em formato desconhecido: %r" % (valor,))
    vistas = set()
    for x in itens:
        q = _sem_acento(str(x)).replace(" ", "_")
        if not q:
            continue
        if q not in QUALIFICACOES:
            raise ValueError("qualificação desconhecida: %r (as possíveis: %s)" % (x, ", ".join(QUALIFICACOES)))
        vistas.add(q)
    if not vistas:
        raise ValueError("marque ao menos uma qualificação que a IA pode tratar")
    return tuple(q for q in QUALIFICACOES if q in vistas)


def coleta_de(marcadas):
    """O que se COLETA para as `marcadas`: SIM e SIM com análise humana sempre, e o NÃO só quando marcado."""
    marcadas = set(marcadas or ())
    return tuple(q for q in QUALIFICACOES if q in COLETA_SEMPRE or q in marcadas)


def ia_da_validacao(parametros):
    """(marcadas, coleta) de uma validação. A criada antes de 17/09/2026 não tem a chave e fica no padrão."""
    marcadas = ler_qualificacoes((parametros or {}).get("ia_qualificacoes"))
    return marcadas, coleta_de(marcadas)


def ligacoes_nao(cur, ligacoes):
    """As ligações com qualificação NAO entre `ligacoes`, num conjunto.

    A LIGAÇÃO NAO FORA DA COLETA FICA INTOCADA (dono do produto, 17/09/2026): a rodada cuja coleta não traz o NÃO
    (extração sem a caixa, o botão "Revisar vínculos", `revisar_vinculo`, `casar_por_endereco`, `cruzar_ligacao` ou
    `telhados` sem `--qualificacoes` com NAO) não cria, não aceita, não reescreve e não descarta vínculo de ligação
    NAO — a ligação fica fora do conjunto que a rodada revisa. Eram 6.139 vínculos vivos de ligação NAO nesse dia.

    PELO ÍNDICE E EM PYTHON: `num_ligacao = any(bigint[])` usa a chave da tabela, e quem chama tira o conjunto da
    própria lista — um `not exists` por linha contra o cadastro inteiro já deixou a fila 13 minutos parada."""
    nums = sorted({int(x) for x in ligacoes if str(x).isdigit()})
    if not nums:
        return set()
    cur.execute("""select num_ligacao::text from resources_root.cadastro_corsan
                    where num_ligacao = any(%s::bigint[]) and qualificacao = 'NAO'""", (nums,))
    return {r[0] for r in cur.fetchall()}


def _log(m):
    print(m, flush=True)


def _sem_acento(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().upper().strip()


def _dentro(lat, lng, pol):
    """Ponto no polígono [[lat, lng], ...] por raio — em Python, e não PostGIS por linha."""
    dentro = False
    j = len(pol) - 1
    for i in range(len(pol)):
        yi, xi = pol[i]
        yj, xj = pol[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            dentro = not dentro
        j = i
    return dentro


# ───────────────────────────────────────────────────────────────────────────────────────── as ligações ──
def ligacoes_para_validar(cur, empresa, cidade, poligono=None, refazer=False, qualificacoes=None):
    """(ordenadas, contagem) — as ligações aptas da cidade (ou da área) com POI vinculado.

    A MESMA REGRA DA FILA DO JULGAMENTO (`avaliar_ligacao.SQL_FILA`): residencial, ativa, apta ao cruzamento
    (SIM ou SIM com análise humana) e com vínculo não descartado. SEM o corte "tem imagem" de lá: aqui a foto de
    rua é uma das etapas, e a ligação sem foto nenhuma é julgada com `--julgar-sem-foto`.

    AS QUALIFICAÇÕES DA COLETA (dono do produto, 17/09/2026): `qualificacoes` é o conjunto de coleta do processo
    (`coleta_de`). No padrão — SIM e SIM com análise humana — a consulta é a de sempre, por `apta_cruzamento` (e o
    índice parcial `ix_corsan_apta`); com o NÃO marcado, ela pede `qualificacao = any(...)`, e o NÃO entra na fila
    depois do SIM com análise humana."""
    cont = collections.Counter()
    coleta = tuple(qualificacoes) if qualificacoes else COLETA_SEMPRE
    if set(coleta) == set(COLETA_SEMPRE):
        filtro_q, args_q = "apta_cruzamento", ()
    else:
        filtro_q, args_q = "qualificacao = any(%s)", (list(coleta),)
    base = """select num_ligacao::text, cod_latitude::float8, cod_longitude::float8, qualificacao
                from resources_root.cadastro_corsan
               where id_empresa = %s and """ + filtro_q + """
                 and upper(categoria) = 'RESIDENCIAL' and upper(coalesce(sit_ligacao, '')) = 'ATIVA'"""
    if poligono:
        lats = [p[0] for p in poligono]
        lngs = [p[1] for p in poligono]
        cur.execute(base + " and geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)",
                    (empresa,) + args_q + (min(lngs), min(lats), max(lngs), max(lats)))
        linhas = [r for r in cur.fetchall() if r[1] is not None and _dentro(r[1], r[2], poligono)]
    else:
        cur.execute(base + " and upper(cidade) = %s", (empresa,) + args_q + (_sem_acento(cidade),))
        linhas = cur.fetchall()
    cont["aptas na %s" % ("área" if poligono else "cidade")] = len(linhas)
    ligs = [r[0] for r in linhas]
    qual = {r[0]: r[3] for r in linhas}
    if not ligs:
        return [], cont
    cur.execute("""select ligacao, array_agg(distinct lower(coalesce(fonte_poi, ''))) from radar_comercial.ligacao_poi
                    where ligacao = any(%s) and descartado_em is null group by 1""", (ligs,))
    fontes = {l: set(fs or []) for l, fs in cur.fetchall()}
    julgadas = set()
    if not refazer:
        # A REVISÃO DIRETA POR "IA NÃO AUTORIZADA" NÃO É JULGAMENTO (dono do produto, 17/09/2026): a ligação que foi
        # para revisão humana só porque a qualificação dela não estava marcada volta na validação seguinte, e a IA a
        # julga se lá a caixa estiver marcada. Até 17/09/2026 nenhum veredito tinha a chave: o conjunto é o de sempre.
        cur.execute("""select ligacao from radar_comercial.ligacao_veredito
                        where ligacao = any(%s) and (percepcao::jsonb->>'ia_nao_autorizada') is null""", (ligs,))
        julgadas = {r[0] for r in cur.fetchall()}
    fila = []
    for lig in ligs:
        if lig not in fontes:
            cont["fora: sem POI vinculado"] += 1
            continue
        if lig in julgadas:
            cont["fora: já julgada"] += 1
            continue
        fs = fontes[lig]
        g = 0 if "ifood" in fs else 1 if "maps" in fs else 2
        # o NÃO, quando marcado, vem depois do SIM com análise humana (17/09/2026)
        q = 0 if qual.get(lig) == "SIM" else 2 if qual.get(lig) == "NAO" else 1
        fila.append((g, q, int(lig) if lig.isdigit() else 0, lig))
        cont["%s · %s" % (GRUPOS[g], ("SIM", "SIM com análise", "NÃO")[q])] += 1
    fila.sort()
    return [x[-1] for x in fila], cont


# ──────────────────────────────────────────────────────────────────────────────────────────── criar ──
def criar(con, empresa, cidade, area=None, pedido_por=None, ambiente=AMBIENTE, refazer=False, lote=LOTE,
          ia_qualificacoes=None, ia_marcado_por=None, ia_marcado_em=None):
    """Cria a validação, os lotes e as tarefas. Devolve (id, mensagem). Uma validação ativa por cidade e área.

    `ia_qualificacoes` (17/09/2026): as qualificações que a IA pode julgar, marcadas na tela; ausente é o padrão (SIM
    e SIM com análise humana). Vão para `parametros` com a coleta que sai delas, quem marcou e quando — a auditoria."""
    import area_utils
    try:
        marcadas = ler_qualificacoes(ia_qualificacoes)
    except ValueError as e:
        raise SystemExit(str(e))
    coleta = coleta_de(marcadas)
    cur = con.cursor()
    cur.execute("""select id from radar_comercial.validacao
                    where id_empresa = %s and ambiente = %s and upper(cidade) = %s
                      and coalesce(area, '') = coalesce(%s, '') and estado in ('preparando', 'rodando', 'pausada')""",
                (empresa, ambiente, _sem_acento(cidade), area))
    r = cur.fetchone()
    if r:
        return r[0], "já existe uma validação ativa desta cidade/área (#%d)" % r[0]
    poligono = area_utils.carregar_area(area) if area else None
    if area and not poligono:
        raise SystemExit("a área '%s' não existe no banco" % area)
    ligs, cont = ligacoes_para_validar(cur, empresa, cidade, poligono, refazer, qualificacoes=coleta)
    # AS MESMAS LIGAÇÕES JÁ EM ANDAMENTO (16/09/2026): dois cliques em "Avaliar com IA" criaram #2 e #3 em Paverama
    # com as mesmas 8 ligações — cada clique guarda a área com nome próprio, e a trava por área acima não as via.
    if ligs:
        cur.execute("""select v.id, array_agg(x.lig order by x.lig)
                         from radar_comercial.validacao v
                         join radar_comercial.validacao_lote l on l.id_validacao = v.id
                         cross join lateral unnest(l.ligacoes) as x(lig)
                        where v.id_empresa = %s and v.ambiente = %s and v.estado in ('preparando', 'rodando', 'pausada')
                        group by v.id""", (empresa, ambiente))
        mesmas = sorted(ligs)
        for vid_existente, do_existente in cur.fetchall():
            if sorted(do_existente) == mesmas:
                con.rollback()
                return vid_existente, "as mesmas %d ligações já estão na validação #%d" % (len(ligs), vid_existente)
    if not ligs:
        con.rollback()
        return None, "nenhuma ligação para validar (%s)" % ", ".join("%s %d" % kv for kv in cont.items())
    cur.execute("""insert into radar_comercial.validacao (id_empresa, pedido_por, ambiente, cidade, area, ligacoes,
                                                          estado, parametros, progresso)
                   values (%s, %s, %s, %s, %s, %s, 'rodando', %s, %s) returning id""",
                (empresa, pedido_por, ambiente, _sem_acento(cidade), area, len(ligs),
                 json.dumps({"lote": lote, "refazer": refazer, "tetos": TETO,
                             # A MARCAÇÃO DA IA, PARA AUDITORIA (17/09/2026): quais, a coleta, quem marcou e quando
                             "ia_qualificacoes": list(marcadas), "coleta_qualificacoes": list(coleta),
                             "ia_marcado_por": ia_marcado_por or None,
                             "ia_marcado_em": ia_marcado_em or datetime.datetime.now().astimezone().isoformat(
                                 timespec="seconds")}),
                 json.dumps({"ordem": dict(cont)}, ensure_ascii=False)))
    vid = cur.fetchone()[0]
    for n, i in enumerate(range(0, len(ligs), lote)):
        cur.execute("""insert into radar_comercial.validacao_lote (id_empresa, id_validacao, n, ligacoes)
                       values (%s, %s, %s, %s) returning id""", (empresa, vid, n, ligs[i:i + lote]))
        id_lote = cur.fetchone()[0]
        for nome, e in ETAPAS.items():
            cur.execute("""insert into radar_comercial.validacao_tarefa
                               (id_empresa, id_validacao, id_lote, ambiente, etapa, ordem, precisa_ia, simultaneas,
                                estado)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (empresa, vid, id_lote, ambiente, nome, e.ordem, e.precisa_ia, e.simultaneas,
                         "fila" if not e.depende else "espera"))
    con.commit()
    return vid, "validação #%d: %d ligações em %d lote(s) · %s%s" % (
        vid, len(ligs), (len(ligs) + lote - 1) // lote, ", ".join("%s %d" % kv for kv in cont.items()),
        "" if marcadas == IA_PADRAO else " · a IA pode tratar: %s" % ", ".join(ROTULO_QUALIFICACAO[q] for q in marcadas))


# ─────────────────────────────────────────────────────────────────────────────────────────── avançar ──
def avancar(con, ambiente=AMBIENTE):
    """Libera as etapas cujas dependências terminaram, devolve à fila o que perdeu o executor e fecha a validação."""
    cur = con.cursor()
    cur.execute("select pg_advisory_xact_lock(hashtext('radar_validacao'))")
    # o executor que parou (máquina desligada, contêiner morto): a tarefa volta, até TENTATIVAS
    cur.execute("""update radar_comercial.validacao_tarefa
                      set estado = case when tentativas >= %s then 'erro' else 'fila' end,
                          erro = case when tentativas >= %s then 'o executor ' || coalesce(worker, '?') ||
                                      ' parou de dar sinal ' || tentativas || ' vezes' else erro end,
                          worker = null
                    where ambiente = %s and estado = 'rodando' and visto_em < now() - make_interval(mins => %s)""",
                (TENTATIVAS, TENTATIVAS, ambiente, SEM_SINAL_MIN))
    cur.execute("""select t.id_lote, t.etapa, t.estado, t.id
                     from radar_comercial.validacao_tarefa t
                     join radar_comercial.validacao v on v.id = t.id_validacao
                    where v.estado = 'rodando' and t.ambiente = %s
                      and exists (select 1 from radar_comercial.validacao_tarefa e
                                   where e.id_lote = t.id_lote and e.estado = 'espera')""", (ambiente,))
    por_lote = collections.defaultdict(dict)
    ids = {}
    for lote, etapa, estado, tid in cur.fetchall():
        por_lote[lote][etapa] = estado
        ids[(lote, etapa)] = tid
    liberar = [ids[(lote, etapa)] for lote, est in por_lote.items() for etapa, e in est.items()
               if e == "espera" and all(est.get(d) == "ok" for d in ETAPAS[etapa].depende)]
    if liberar:
        cur.execute("update radar_comercial.validacao_tarefa set estado = 'fila' where id = any(%s)", (liberar,))
    # a validação termina quando nada mais anda
    cur.execute("""update radar_comercial.validacao v
                      set estado = case when s.erros > 0 then 'erro' else 'ok' end,
                          erro = case when s.erros > 0 then s.erros || ' tarefa(s) com erro' end,
                          terminado_em = now()
                     from (select id_validacao, count(*) filter (where estado = 'erro') erros,
                                  count(*) filter (where estado in ('espera', 'fila', 'rodando')) andando
                             from radar_comercial.validacao_tarefa where ambiente = %s group by 1) s
                    where s.id_validacao = v.id and v.estado = 'rodando' and s.andando = 0""", (ambiente,))
    con.commit()
    return len(liberar)


# ───────────────────────────────────────────────────────────────────────────────────────────── pegar ──
def pegar(con, maquina, aceitas=None, ambiente=AMBIENTE):
    """A próxima tarefa que cabe nos tetos globais, marcada como desta máquina. None se nada cabe."""
    cur = con.cursor()
    cur.execute("select pg_advisory_xact_lock(hashtext('radar_validacao'))")
    cur.execute("select etapa, simultaneas from radar_comercial.validacao_tarefa where estado = 'rodando'")
    uso = collections.Counter()
    for etapa, simult in cur.fetchall():
        e = ETAPAS.get(etapa)
        if not e:
            continue
        uso[e.recurso] += 1
        uso["spark_simult"] += simult or 0
        uso["conexoes"] += e.conexoes * e.partes
    cur.execute("select count(*) from pg_stat_activity where usename = current_user")
    livres = POOL_TOTAL - POOL_FOLGA - cur.fetchone()[0]
    cur.execute("""select t.id, t.etapa, t.id_validacao, t.id_lote, t.tentativas, v.id_empresa::text, l.n, v.parametros
                     from radar_comercial.validacao_tarefa t
                     join radar_comercial.validacao v on v.id = t.id_validacao
                     join radar_comercial.validacao_lote l on l.id = t.id_lote
                    where t.estado = 'fila' and t.ambiente = %s and v.estado = 'rodando'
                      and (cardinality(%s::text[]) = 0 or t.etapa = any(%s::text[]))
                    order by t.id_validacao, l.n, t.ordem
                    limit 200""", (ambiente, list(aceitas or []), list(aceitas or [])))
    espera_spark = False
    for tid, etapa, vid, id_lote, tent, empresa, n, parametros in cur.fetchall():
        e = ETAPAS[etapa]
        if e.recurso in ("google", "cnpj", "busca") and uso[e.recurso] >= TETO[e.recurso]:
            continue
        # A FILA NAO SE FURA (18/09/2026): com `continue` aqui, o julgamento do lote 4 de Bento (120 vagas da Spark e 4
        # conexoes) nao cabia, e a leitura e a foto de rua dos lotes 7 e 8, menores, passavam na frente a cada volta e
        # ocupavam de novo o que ia sobrar — o julgamento esperou mais de uma hora com os lotes 4, 5 e 6 prontos.
        # QUEM ESPERA A SPARK SEGURA SO A SPARK: as outras da Spark esperam atras dele, e a foto de rua (processador e
        # Google) segue rodando — com `break` aqui o i9 ficou com carga 5 durante o julgamento. Conexao e de todos:
        # quem nao cabe segura os de tras. Os tetos por site (google, cnpj, busca) seguem pulando.
        if e.recurso == "spark" and (espera_spark or uso["spark_simult"] + e.simultaneas > TETO["spark"]):
            espera_spark = True
            continue
        if uso["conexoes"] + e.conexoes * e.partes > TETO["conexoes"] or e.conexoes * e.partes > livres:
            break
        cur.execute("""update radar_comercial.validacao_tarefa
                          set estado = 'rodando', worker = %s, iniciado_em = now(), visto_em = now(),
                              terminado_em = null, tentativas = tentativas + 1, codigo_saida = null, erro = null
                        where id = %s and estado = 'fila'""", (maquina, tid))
        if cur.rowcount != 1:
            continue
        cur.execute("select ligacoes from radar_comercial.validacao_lote where id = %s", (id_lote,))
        ligacoes = cur.fetchone()[0]
        con.commit()
        # os `parametros` levam a marcação da IA (17/09/2026): o executor monta o julgamento e a busca com ela
        return {"id": tid, "etapa": etapa, "validacao": vid, "lote": n, "id_lote": id_lote, "empresa": empresa,
                "tentativas": tent + 1, "ligacoes": ligacoes, "parametros": parametros or {}}
    con.commit()
    return None


# ──────────────────────────────────────────────────────────────────────────────────────────── encerrar ──
_CONFERENCIA = re.compile(r"conferência: (\d+) ligações.*?fichas do Maps (\d+) · foto de rua (\d+) · leitura (\d+) · "
                          r"Serasa (\d+) · busca web (\d+)")


def _so_falta_foto_sem_panorama(resumo):
    """No R_000 de Canoas faltaram 2 fotos de 600 — panorama que não existe — e a segunda passada gastou ~2,5 min
    para nada. Com todo o resto completo e até 2% de fotos faltando, segue sem ela (orquestrador, 15/09/2026)."""
    m = _CONFERENCIA.search(resumo or "")
    if not m:
        return False
    n_lig, fichas, foto, leitura, serasa, busca = (int(x) for x in m.groups())
    # `fichas` FICOU FORA DA CONTA em 17/09/2026: a etapa que a buscava saiu, então uma segunda passada não a traria.
    return leitura == serasa == busca == 0 and foto * 50 <= n_lig


def encerrar(con, tarefa, codigo, resumo="", erro=None):
    """Grava o fim da tarefa. A conferência que achou falta devolve a recaptura do lote UMA vez."""
    e = ETAPAS[tarefa["etapa"]]
    cur = con.cursor()
    cur.execute("select pg_advisory_xact_lock(hashtext('radar_validacao'))")
    cur.execute("select estado, tentativas from radar_comercial.validacao_tarefa where id = %s", (tarefa["id"],))
    r = cur.fetchone()
    if not r or r[0] != "rodando":
        con.commit()
        return "ignorada (estado %s)" % (r[0] if r else "?")
    tentativas = r[1]
    resumo = (resumo or "")[:500]
    if erro is None and codigo == 0:
        estado = "ok"
    elif tarefa["etapa"] == "conferencia" and erro is None:
        if tentativas < 2 and not _so_falta_foto_sem_panorama(resumo):
            cur.execute("""update radar_comercial.validacao_tarefa
                              set estado = case when etapa = any(%s) then 'fila' else 'espera' end,
                                  worker = null, terminado_em = null
                            where id_lote = %s and etapa = any(%s)""",
                        (list(SEM_DEPENDENCIA), tarefa["id_lote"], list(RECAPTURA)))
            cur.execute("""update radar_comercial.validacao_tarefa
                              set estado = 'espera', worker = null, codigo_saida = %s, resumo = %s, visto_em = now()
                            where id = %s""", (codigo, "faltou evidência, segunda passada: " + resumo, tarefa["id"]))
            con.commit()
            return "segunda passada da recaptura"
        estado = "ok"
        resumo = "o que restou não se resolve recapturando: " + resumo
    elif e.tolerante and erro is None:
        estado = "ok"
        resumo = "(código %s) %s" % (codigo, resumo)
    elif tentativas < TENTATIVAS:
        estado = "fila"
    else:
        estado = "erro"
    cur.execute("""update radar_comercial.validacao_tarefa
                      set estado = %s, codigo_saida = %s, resumo = %s, erro = %s, visto_em = now(),
                          terminado_em = case when %s in ('ok', 'erro') then now() end,
                          worker = case when %s = 'fila' then null else worker end
                    where id = %s""", (estado, codigo, resumo, (erro or None) and str(erro)[:500], estado, estado,
                                       tarefa["id"]))
    con.commit()
    return estado


# ───────────────────────────────────────────────────────────────────────────────────────────── status ──
def status(con, vid=None, ambiente=AMBIENTE, limite=20):
    cur = con.cursor()
    cur.execute("""select id, cidade, area, ligacoes, estado, criado_em, terminado_em, erro, progresso, parametros
                     from radar_comercial.validacao
                    where ambiente = %s and (%s::bigint is null or id = %s)
                    order by id desc limit %s""", (ambiente, vid, vid, limite))
    vals = [dict(zip(("id", "cidade", "area", "ligacoes", "estado", "criado_em", "terminado_em", "erro", "progresso",
                      "parametros"), r)) for r in cur.fetchall()]
    if not vals:
        return []
    cur.execute("""select t.id_validacao, l.n, t.etapa, t.estado, t.worker, t.tentativas, t.resumo, t.erro,
                          t.iniciado_em, t.terminado_em
                     from radar_comercial.validacao_tarefa t
                     join radar_comercial.validacao_lote l on l.id = t.id_lote
                    where t.id_validacao = any(%s) order by t.id_validacao, l.n, t.ordem""",
                ([v["id"] for v in vals],))
    tarefas = collections.defaultdict(list)
    for vid_, n, etapa, estado, worker, tent, resumo, erro, ini, fim in cur.fetchall():
        tarefas[vid_].append({"lote": n, "etapa": etapa, "estado": estado, "worker": worker, "tentativas": tent,
                              "resumo": resumo, "erro": erro, "iniciado_em": ini, "terminado_em": fim})
    for v in vals:
        v["tarefas"] = tarefas.get(v["id"], [])
        v["por_etapa"] = {nome: dict(collections.Counter(t["estado"] for t in v["tarefas"] if t["etapa"] == nome))
                          for nome in ETAPAS}
    return vals


def mudar_estado(con, vid, acao):
    cur = con.cursor()
    if acao == "pausar":
        cur.execute("update radar_comercial.validacao set estado = 'pausada' where id = %s and estado = 'rodando'",
                    (vid,))
    elif acao == "retomar":
        cur.execute("update radar_comercial.validacao set estado = 'rodando' where id = %s and estado = 'pausada'",
                    (vid,))
    elif acao == "cancelar":
        cur.execute("""update radar_comercial.validacao set estado = 'cancelada', terminado_em = now()
                        where id = %s and estado in ('preparando', 'rodando', 'pausada')""", (vid,))
        if cur.rowcount:
            # o que roda é derrubado pelo executor dono dele, que vê a validação cancelada no próximo sinal
            cur.execute("""update radar_comercial.validacao_tarefa set estado = 'cancelada', terminado_em = now()
                            where id_validacao = %s and estado in ('espera', 'fila')""", (vid,))
    n = cur.rowcount
    con.commit()
    return n


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("criar")
    c.add_argument("--cidade", required=True)
    c.add_argument("--area", default=None, help="nome da área desenhada (area_trabalho); sem ela, a cidade inteira")
    c.add_argument("--empresa", default=os.environ.get("CR_TENANT_ID", ""))
    c.add_argument("--refazer", action="store_true", help="inclui as ligações que já têm veredito")
    c.add_argument("--lote", type=int, default=LOTE)
    c.add_argument("--ia-qualificacoes", dest="ia_qualificacoes", default=None,
                   help="as qualificações que a IA pode tratar, separadas por vírgula (padrão: SIM,SIM_COM_ANALISE_HUMANA)")
    c.add_argument("--ia-marcado-por", dest="ia_marcado_por", default=None, help="quem marcou (auditoria)")
    c.add_argument("--ia-marcado-em", dest="ia_marcado_em", default=None, help="quando marcou, em ISO (auditoria)")
    s = sub.add_parser("status")
    s.add_argument("--id", type=int, default=None)
    for acao in ("pausar", "retomar", "cancelar"):
        x = sub.add_parser(acao)
        x.add_argument("--id", type=int, required=True)
    a = p.parse_args(argv)
    con = bc.conectar()
    try:
        if a.cmd == "criar":
            # A EMPRESA PELA CONEXÃO (16/09/2026): o worker da fila não passa `CR_TENANT_ID` — ele declara o usuário de
            # serviço do job (`RADAR_USUARIO_SERVICO`), e a empresa sai de `core.empresa_atual()`. Sem isto a extração
            # de Paverama (job 64) terminou e a validação da área falhou com "sem empresa".
            empresa = a.empresa
            if not empresa:
                cur = con.cursor()
                cur.execute("select core.empresa_atual()::text")
                empresa = (cur.fetchone() or [None])[0]
                con.commit()
            if not empresa:
                raise SystemExit("sem empresa: --empresa, CR_TENANT_ID ou RADAR_USUARIO_SERVICO")
            vid, msg = criar(con, empresa, a.cidade, a.area, refazer=a.refazer, lote=a.lote,
                             ia_qualificacoes=a.ia_qualificacoes, ia_marcado_por=a.ia_marcado_por,
                             ia_marcado_em=a.ia_marcado_em)
            _log("■ " + msg)
            return 0
        if a.cmd == "status":
            for v in status(con, a.id):
                _log("#%d %s%s · %d ligações · %s" % (v["id"], v["cidade"], (" / " + v["area"]) if v["area"] else "",
                                                      v["ligacoes"], v["estado"]))
                for nome, cont in v["por_etapa"].items():
                    _log("   %-12s %s" % (nome, " ".join("%s %d" % kv for kv in sorted(cont.items()))))
            return 0
        n = mudar_estado(con, a.id, a.cmd)
        _log("■ %s #%d: %s" % (a.cmd, a.id, "feito" if n else "nada mudou (estado não permite)"))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
