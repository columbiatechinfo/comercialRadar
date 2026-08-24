# -*- coding: utf-8 -*-
"""cadastur.py — o Cadastur/MTur como passo do processo e gerador de POIs.

O QUE ESTE PASSO ACRESCENTA QUE NENHUM OUTRO TEM

As demais fontes dizem *que existe um comércio ali*. O Cadastur diz *o que o
Estado registrou que ali funciona* — e, para meio de hospedagem, **quantos
leitos**. Nenhuma outra fonte do sistema traz capacidade declarada, e leito é
consumo de água por pessoa/dia. Para uma companhia de saneamento, é a diferença
entre "tem um hotel nesta quadra" e "tem um hotel de 180 leitos nesta quadra".

O CAMINHO, EM QUATRO PASSOS

    1. BAIXAR   a skill `extracao-cadastur-mtur` puxa o snapshot do portal do
                MTur (API CKAN) e entrega Parquet auditável.
    2. CARREGAR o recorte do município entra em `cadastur_prestador`.
    3. CRUZAR   `cruzar_bases.py` cruza com POIs, cadastro do cliente, Receita
                e iFood — como qualquer outra base. O Cadastur cruza por CNPJ
                (chave limpa) e por nome com âncora emprestada.
    4. GERAR    o que NÃO cruzou com nenhum POI vira POI novo.

Os passos 3 e 4 são separados de propósito, e o 4 EXIGE o 3. Se o gerador
decidisse sozinho o que já existe, teríamos duas implementações da mesma
pergunta — e a hora em que divergissem seria a hora em que o banco ganhasse
duplicata.

DE ONDE VEM A COORDENADA, JÁ QUE O CADASTUR NÃO TEM

A skill **não geocodifica**, e isso é decisão dela, não limitação: o endereço
moderno é texto livre (número em 74% das linhas, CEP em 55,5%), e adivinhar
coordenada a partir daquilo produz ponto errado com cara de ponto certo.

A âncora entra por CNPJ, contra a `cnpj_tratado` — que já tem 21.826
coordenadas vindas do CNEFE. CNPJ é chave exata: ou casa, ou não casa.

Quem não tem CNPJ ou não tem coordenada **não vira POI**, e a linha registra
por quê em `sem_poi_motivo`. Não vira POI sem posição, e não ganha posição
inventada — um ponto no centroide do município mandaria alguém a campo no
lugar errado, o que custa mais que não ter o ponto.

O QUE NÃO ENTRA

O Cadastur tem 15 atividades; uma delas — guia de turismo — é PESSOA FÍSICA,
com CPF, data de nascimento, nome social e até tipo sanguíneo. Não entra: não é
economia que consome água, e guardar dado pessoal sem finalidade é o que a LGPD
chama de tratamento sem base legal. A tabela nem tem coluna para receber.

USO

    python cadastur.py --listar
    python cadastur.py --uf RS --municipio Canoas
    python cadastur.py --uf RS --municipio Canoas --gerar
    python cadastur.py --uf RS --municipio Canoas --simular
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

from psycopg2.extras import execute_values

import config  # noqa: F401  (carrega o .env)
import base_comum as bc
import realtime_ingest

RAIZ = Path(__file__).resolve().parent
SKILL = RAIZ / "skills" / "extracao-cadastur-mtur"
SAIDA = Path(os.environ.get("CADASTUR_SAIDA") or (RAIZ / "dados_externos" / "cadastur"))

# As 14 atividades de PESSOA JURÍDICA. A décima quinta —
# `prestadores-de-servicos-turisticos-guia-turismo_2` — está fora por decisão,
# não por esquecimento: ver o cabeçalho.
#
# A lista é explícita em vez de "todos menos o guia" porque o portal já criou
# conjunto novo duas vezes. Com lista negativa, um conjunto de pessoa física
# que nascesse amanhã entraria sozinho e ninguém veria.
DATASETS_PJ = [
    "meios-de-hospedagem",
    "restaurantes-cafeterias-e-bares",
    "acampamento-turistico",
    "parque-tematico",
    "centro-de-convencoes",
    "empreendimento-de-entretenimento-e-lazer-e-parques-aquaticos",
    "empreendimento-de-apoio-ao-turismo-nautico-ou-a-pesca-desportiva",
    "casas-de-espetaculos-e-equipamentos-de-animacao-turistica",
    "agencia-de-turismo",
    "locadora-de-veiculos",
    "organizador-de-eventos",
    "prestador-de-servicos-de-infraestrutura-de-apoio-a-eventos",
    "prestador-especializado-em-segmentos-turisticos",
    "transportadora-turistica",
]

# Coluna do Parquet → coluna nossa. O que não está aqui vai para `extras`
# (R0 da skill: zero perda). Os aliases existem porque as três gerações de
# layout nomeiam a mesma coisa de jeitos diferentes.
MAPA = {
    "cnpj": "cnpj",
    "razao_social": "razao_social",
    "nome_fantasia": "nome_fantasia",
    "cnae": "cnae",
    "natureza_juridica": "natureza_juridica",
    "porte": "porte",
    "_atividade": "atividade_turistica",
    "atividade_turistica": "atividade_turistica",
    "tipo_hospedagem": "tipo_hospedagem",
    "categorias": "categorias",
    "endereco_comercial": "endereco_comercial",
    "endereco_rfb": "endereco_rfb",
    "municipio": "municipio",
    "uf": "uf",
    "telefone_comercial": "telefone",
    "telefone_institucional": "telefone",
    "email_comercial": "email",
    "email_institucional": "email",
    "website": "website",
    "situacao_cadastral": "situacao_cadastral",
    "situacao_atividade": "situacao_atividade",
    "numero_certificado": "numero_certificado",
    "_dataset": "dataset",
    "_recurso_id": "recurso_id",
    "_recurso_nome": "recurso_nome",
    "_linha_origem": "linha_origem",
    "_sha256": "sha256",
}

# Dado PESSOAL. Mesmo vindo num conjunto de pessoa jurídica — e vem, porque o
# responsável pela empresa é uma pessoa —, não entra nem em `extras`. A regra
# fica em código, e não só no cabeçalho, porque `extras` é justamente o balde
# do "tudo o que sobrar": sem esta lista, o CPF do responsável entraria por ele.
PESSOAL = {
    "cpf", "data_nascimento", "nome_social", "documento_identificacao",
    "orgao_expedidor", "nacionalidade", "sexo", "tipo_sanguineo",
    "carteira_estrangeiro", "nome_responsavel", "email_administrador",
}

# Colunas com tratamento PRÓPRIO abaixo — número mais texto original, ou data
# mais texto original. Não passam pelo laço genérico, senão entrariam em
# `extras` além de já estarem na coluna, e o mesmo valor ficaria em dois
# lugares que podem divergir na próxima carga.
TRATADAS = {"uh", "leitos", "validade_certificado", "validade",
            "_ref_periodo", "_extraido_em"}

COLS = ("cnpj", "razao_social", "nome_fantasia", "cnae", "natureza_juridica",
        "porte", "atividade_turistica", "tipo_hospedagem", "categorias",
        "endereco_comercial", "endereco_rfb", "municipio", "uf",
        "uh", "uh_texto", "leitos", "leitos_texto",
        "telefone", "email", "website",
        "situacao_cadastral", "situacao_atividade", "numero_certificado",
        "validade", "validade_texto",
        "dataset", "recurso_id", "recurso_nome", "ref_periodo",
        "linha_origem", "sha256", "extraido_em", "extras")

# Recarregar o mesmo trimestre ATUALIZA a linha; nunca a duplica, e nunca apaga
# um campo bom com um nulo novo. `poi_id` e `sem_poi_motivo` ficam de fora do
# update de propósito: são resultado do nosso trabalho, não do arquivo de
# origem, e uma recarga não pode desfazê-lo.
GRAVAR = f"""
insert into comercialradar.cadastur_prestador ({', '.join(COLS)})
values %s
on conflict (tenant_id, recurso_id, linha_origem) do update set
""" + ",\n  ".join(
    f"{c} = coalesce(excluded.{c}, cadastur_prestador.{c})"
    for c in COLS if c not in ("recurso_id", "linha_origem")
)


def _s(v):
    """Texto limpo, ou None. Pandas devolve NaN, e NaN vira a string 'nan'."""
    if v is None:
        return None
    t = str(v).strip()
    if not t or t.lower() in ("nan", "none", "nat", "-", "--"):
        return None
    return t


def _num(v):
    """O número dentro do texto, quando há um.

    UH e leitos são AUTODECLARADOS e vêm sujos: no 2T/2026 havia 64 valores não
    numéricos. Devolve (numero, texto_original) — os dois, sempre. Guardar só o
    número perderia o que estava escrito, e é justamente o texto que explica o
    outlier de 17.000 leitos quando ele aparecer.
    """
    t = _s(v)
    if t is None:
        return None, None
    limpo = "".join(c for c in t if c.isdigit())
    if not limpo:
        return None, t
    try:
        n = int(limpo)
    except ValueError:
        return None, t
    # Acima disto não é hotel, é erro de digitação. Guarda o texto e deixa o
    # número nulo, em vez de gravar um leito que ninguém acredita.
    return (n if n <= 100_000 else None), t


def _data(v):
    """Data ISO ou DD/MM/AAAA. Qualquer outra coisa devolve (None, texto).

    A skill avisa: `validade` tem formato livre. Um parser que estoura aqui
    derruba a carga inteira por causa de uma linha — e o valor que não é data
    ainda assim é informação, então ele fica no campo de texto.
    """
    t = _s(v)
    if t is None:
        return None, None
    import datetime as dt
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(t[:len(f) + 2].strip(), f).date(), t
        except ValueError:
            continue
    return None, t


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                   if unicodedata.category(c) != "Mn").strip()


# ── 1. Baixar ────────────────────────────────────────────────────────────────

def _ultimo_ano(datasets: list) -> dict:
    """Ano do trimestre mais recente de cada conjunto, lido do catálogo.

    NEM TODO CONJUNTO É ATUALIZADO. Medido em 24/08/2026: doze estão em 2026T2,
    mas `parque-tematico` e `empreendimento-de-entretenimento-e-lazer-e-parques-aquaticos`
    pararam em 2024T4.

    Isto não é detalhe: parque aquático é o MAIOR consumidor de água da lista
    inteira. Um `--desde 2026` fixo os deixaria de fora sem uma linha de aviso
    — a execução terminaria com sucesso, o relatório fecharia, e faltariam
    justamente as economias que mais importam. Erro que não reclama é o pior
    tipo, e este é do tipo que só apareceria numa conferência manual.
    """
    sys.path.insert(0, str(SKILL))
    from cadastur import catalogo          # noqa: E402  (a skill vendorizada)
    ultimo = {}
    for r in catalogo.descobrir(datasets=list(datasets)):
        if r.dataset in datasets:
            atual = ultimo.get(r.dataset)
            if not atual or (r.ref_ano, r.ref_trimestre) > atual:
                ultimo[r.dataset] = (r.ref_ano, r.ref_trimestre)
    return {d: a for d, (a, _) in ultimo.items()}


def baixar(datasets: list, desde: int | None, refresh: bool = False) -> Path:
    """Roda a skill. Devolve a pasta de saída.

    SÓ O ANO MAIS RECENTE DE CADA CONJUNTO, por padrão. A série vai a 2006 e são
    742 recursos; para saber quais economias existem HOJE, os trimestres de 2019
    não ajudam. Quem quiser a série passa `--desde`.

    Como cada conjunto para num ano diferente, a skill roda uma vez POR ANO, com
    os conjuntos daquele ano. Rodar tudo desde o mais antigo baixaria três anos
    de trimestres dos doze conjuntos que estão em dia.

    A skill é retomável: se cair no meio, a próxima execução do mesmo escopo
    reaproveita o que já baixou. Por isso a saída é fixa em `dados_externos/`,
    e não numa pasta temporária que some.
    """
    SAIDA.mkdir(parents=True, exist_ok=True)

    if desde:
        grupos = {desde: (list(datasets), None)}
    else:
        anos = _ultimo_ano(datasets)
        faltando = [d for d in datasets if d not in anos]
        if faltando:
            print(f"  ⚠ sem recurso no catálogo: {', '.join(faltando)}", flush=True)
        grupos = {}
        for d, ano in anos.items():
            grupos.setdefault(ano, ([], ano))[0].append(d)
        parados = {d: a for d, a in anos.items() if a < max(anos.values())}
        if parados:
            print("  ⚠ conjuntos SEM atualização recente — o dado vem do ano "
                  "indicado, e é o mais novo que existe:", flush=True)
            for d, a in sorted(parados.items()):
                print(f"      {d}  →  {a}", flush=True)

    for ano, (lista, ate) in sorted(grupos.items(), reverse=True):
        cmd = [sys.executable, str(SKILL / "cadastur_extrai.py"),
               "--saida", str(SAIDA), "--datasets", ",".join(lista),
               "--desde", str(ano)]
        if ate:
            cmd += ["--ate", str(ate)]
        if refresh:
            cmd += ["--refresh"]

        print(f"⟦fase⟧ baixando Cadastur · {len(lista)} conjuntos · {ano}"
              + (f"–{ate}" if ate and ate != ano else ""), flush=True)
        r = subprocess.run(cmd, cwd=str(SKILL))
        # 0 = funil fechado · 2 = delta ≠ 0 · 1 = nada lido. O 2 não é fatal
        # para nós: a skill leu e avisou de divergência, e o relatório dela diz
        # onde. Parar aqui esconderia o dado que veio bom.
        if r.returncode == 1:
            raise SystemExit("a skill não leu nada — confira a rede e o portal "
                             "do MTur")
        if r.returncode == 2:
            print("  ⚠ a skill fechou com delta ≠ 0 — veja funil.csv e "
                  "reconciliacao.json antes de confiar no total", flush=True)
    return SAIDA


# ── 2. Carregar ──────────────────────────────────────────────────────────────

def _linhas_do_parquet(pasta: Path, uf: str | None, municipio: str | None):
    """Lê o bronze e devolve só o recorte pedido, do trimestre mais recente.

    O TRIMESTRE MAIS RECENTE, e não todos: o Cadastur é uma SÉRIE, e carregar
    quatro trimestres do mesmo ano traz a mesma pousada quatro vezes. Para
    "quais economias existem hoje", a observação que vale é a última. As
    anteriores continuam no Parquet para quem quiser a série.
    """
    import pandas as pd

    arquivos = sorted((pasta / "bronze_parquet").rglob("*.parquet"))
    if not arquivos:
        raise SystemExit(f"nenhum parquet em {pasta / 'bronze_parquet'} — "
                         "rode sem --so-carregar para baixar primeiro")

    uf_n, mun_n = _norm(uf), _norm(municipio)
    quadros = []
    for arq in arquivos:
        df = pd.read_parquet(arq)
        if "uf" in df.columns and uf_n:
            df = df[df["uf"].map(lambda v: _norm(str(v))) == uf_n]
        if "municipio" in df.columns and mun_n:
            df = df[df["municipio"].map(lambda v: _norm(str(v))) == mun_n]
        if len(df):
            quadros.append(df)
    if not quadros:
        return []

    df = pd.concat(quadros, ignore_index=True)
    if "_ref_periodo" in df.columns and "_dataset" in df.columns:
        ultimo = df.groupby("_dataset")["_ref_periodo"].transform("max")
        df = df[df["_ref_periodo"] == ultimo]
    return df.to_dict("records")


def carregar(con, linhas: list, simular: bool = False) -> dict:
    """Grava o recorte. Devolve a contagem por conjunto."""
    prontas, conta = [], {}
    for r in linhas:
        alvo = {c: None for c in COLS}
        extras = {}

        # O `_extras` da skill é ela mesma guardando o que não soube mapear.
        # Ele chega como STRING de JSON: guardá-lo assim faria um JSON dentro
        # de outro, e ninguém consulta `extras->'_extras'->>'x'`. Abre e
        # mistura, que é o que torna o campo pesquisável.
        bruto = r.get("_extras")
        if bruto:
            try:
                de_dentro = json.loads(bruto) if isinstance(bruto, str) else bruto
                if isinstance(de_dentro, dict):
                    for kk, vv in de_dentro.items():
                        if kk not in PESSOAL and _s(vv) is not None:
                            extras[kk] = _s(vv)
            except (ValueError, TypeError):
                extras["_extras_bruto"] = str(bruto)[:4000]

        for origem, valor in r.items():
            if origem in PESSOAL or origem in TRATADAS or origem == "_extras":
                continue                     # dado pessoal não entra, nem aqui
            destino = MAPA.get(origem)
            if destino:
                # Alias concorrente não sobrescreve o que já veio preenchido —
                # `telefone_comercial` e `telefone_institucional` caem no mesmo
                # campo, e o primeiro preenchido é o que vale. O segundo não se
                # perde: vai para `extras`.
                if alvo.get(destino) is None:
                    alvo[destino] = _s(valor)
                    continue
            v = _s(valor)
            if v is not None:
                extras[origem] = v

        alvo["uh"], alvo["uh_texto"] = _num(r.get("uh"))
        alvo["leitos"], alvo["leitos_texto"] = _num(r.get("leitos"))
        alvo["validade"], alvo["validade_texto"] = _data(
            r.get("validade_certificado") or r.get("validade"))
        alvo["ref_periodo"], _ = _data(r.get("_ref_periodo"))
        alvo["extraido_em"] = _s(r.get("_extraido_em"))
        alvo["linha_origem"] = int(r.get("_linha_origem") or 0)
        alvo["extras"] = json.dumps(extras, ensure_ascii=False)

        if not alvo["recurso_id"] or not alvo["dataset"]:
            continue                          # linha sem procedência é órfã
        conta[alvo["dataset"]] = conta.get(alvo["dataset"], 0) + 1
        prontas.append(tuple(alvo[c] for c in COLS))

    if prontas and not simular:
        # `execute_values` e não um execute por linha: são dezenas de milhares
        # numa UF, e uma ida ao banco por linha multiplica o tempo pelo RTT.
        with con.cursor() as k:
            execute_values(k, GRAVAR, prontas, page_size=1000)
        con.commit()
    return conta


# ── 3+4. Gerar POIs a partir do que não cruzou ───────────────────────────────

# O que o CRUZAMENTO já decidiu, COM o POI que ele encontrou. Ler daqui, e não
# recalcular, é o que garante que "já existe" signifique a mesma coisa nas duas
# telas. E trazer o `poi_id` junto é o que permite LIGAR o prestador ao ponto
# que já existe, em vez de só anotar que ele não é inédito.
#
# O `min()` resolve o caso do prestador que cruzou com mais de um POI: liga ao
# mais antigo, que é o que acumulou fachada, foto e análise. Escolher o mais
# novo jogaria fora o trabalho já pago.
JA_CRUZADOS = """
select cad_id, min(poi_id) from (
  select case when base_a = 'cadastur' then id_a else id_b end as cad_id,
         (case when base_a = 'pois' then id_a else id_b end)::bigint as poi_id
    from comercialradar.cruzamento
   where 'cadastur' in (base_a, base_b) and 'pois' in (base_a, base_b)
) t group by cad_id
"""

PENDENTES = """
select id, cnpj, nome_fantasia, razao_social, atividade_turistica,
       tipo_hospedagem, coalesce(endereco_comercial, endereco_rfb),
       municipio, uf, telefone, email, website, cnae, uh, leitos
  from comercialradar.cadastur_prestador
 where poi_id is null and sem_poi_motivo is null
   and (%(uf)s is null or upper(uf) = upper(%(uf)s))
   and (%(municipio)s is null or lower(municipio) = lower(%(municipio)s))
 order by id
"""


def _coordenada_por_cnpj(con, cnpjs: list) -> dict:
    """CNPJ → (lat, lng), da `cnpj_tratado`.

    Um SELECT para todos, e não um por linha: são milhares de consultas
    idênticas, e o RTT é o custo real aqui.
    """
    if not cnpjs:
        return {}
    with con.cursor() as k:
        k.execute("""select regexp_replace(cnpj, '[^0-9]', '', 'g'), lat, lng
                       from comercialradar.cnpj_tratado
                      where lat is not null
                        and regexp_replace(cnpj, '[^0-9]', '', 'g') = any(%s)""",
                  (cnpjs,))
        return {c: (float(la), float(lo)) for c, la, lo in k.fetchall()}


# A SEGUNDA ÂNCORA: a coordenada que o CRUZAMENTO encontrou.
#
# A primeira é o CNPJ contra a `cnpj_tratado`. Ela falha quando o CNPJ não está
# lá — e falhou, nos dois hotéis de Canoas que sobraram na primeira execução.
#
# Mas o cruzamento por ENDEREÇO casou cinco prestadores com o `cadastro_cliente`,
# que é a base de imóveis do próprio cliente e tem 102 mil coordenadas. Para uma
# companhia de saneamento essa coordenada é melhor que qualquer outra: é a do
# imóvel que ela fatura.
#
# A ordem de preferência é por QUALIDADE da chave, não por conveniência:
#   1. cnpj_tratado por CNPJ — documento igual, sem gradação.
#   2. cadastro_cliente — o imóvel do cliente, casado por endereço.
#   3. ifood_merchant — casado por endereço, coordenada da plataforma.
#
# `ambiguo` fica de fora: quando o melhor candidato empata com o segundo, o
# cruzamento marca a linha para revisão humana em vez de escolher. Herdar uma
# coordenada ambígua colocaria o ponto num de dois imóveis, com 50% de chance —
# e alguém iria a campo no endereço errado sem nada indicando o risco.
ANCORA_DO_CRUZAMENTO = """
with pares as (
  select (case when base_a = 'cadastur' then id_a else id_b end)::bigint as cad_id,
         (case when base_a = 'cadastur' then base_b else base_a end)     as outra,
         (case when base_a = 'cadastur' then id_b else id_a end)         as outro_id,
         chave, score
    from comercialradar.cruzamento
   where 'cadastur' in (base_a, base_b)
     and not ambiguo
     and (case when base_a = 'cadastur' then base_b else base_a end)
         in ('cnpj_tratado', 'cadastro_cliente', 'ifood_merchant')
),
com_geo as (
  select p.cad_id, p.outra, p.score, c.lat, c.lng
    from pares p join comercialradar.cadastro_cliente c on c.id::text = p.outro_id
   where p.outra = 'cadastro_cliente' and c.lat is not null
  union all
  select p.cad_id, p.outra, p.score, t.lat, t.lng
    from pares p join comercialradar.cnpj_tratado t on t.id::text = p.outro_id
   where p.outra = 'cnpj_tratado' and t.lat is not null
  union all
  select p.cad_id, p.outra, p.score, i.lat, i.lng
    from pares p join comercialradar.ifood_merchant i on i.merchant_id = p.outro_id
   where p.outra = 'ifood_merchant' and i.lat is not null
)
select distinct on (cad_id) cad_id, outra, lat, lng
  from com_geo
 order by cad_id,
          case outra when 'cnpj_tratado' then 1
                     when 'cadastro_cliente' then 2 else 3 end,
          score desc
"""


def _coordenada_por_cruzamento(con) -> dict:
    """id do prestador → (lat, lng, base que emprestou)."""
    with con.cursor() as k:
        k.execute(ANCORA_DO_CRUZAMENTO)
        return {cid: (float(la), float(lo), base)
                for cid, base, la, lo in k.fetchall()}


def _poi_por_cnpj(con, cnpjs: list) -> dict:
    """CNPJ → poi_id, para os que o cruzamento ligou a um POI existente."""
    if not cnpjs:
        return {}
    with con.cursor() as k:
        k.execute("""select regexp_replace(cnpj, '[^0-9]', '', 'g'), min(id)
                       from comercialradar.pois
                      where cnpj is not null
                        and regexp_replace(cnpj, '[^0-9]', '', 'g') = any(%s)
                      group by 1""", (cnpjs,))
        return dict(k.fetchall())


def gerar(con, uf: str | None, municipio: str | None,
          simular: bool = False) -> dict:
    """Transforma em POI o que não cruzou com nada. Devolve o placar.

    EXIGE o cruzamento rodado. Sem ele, tudo pareceria inédito e o banco
    ganharia uma cópia de cada hotel que já está lá.
    """
    with con.cursor() as k:
        k.execute(JA_CRUZADOS)
        cruzados = dict(k.fetchall())        # id do prestador (texto) → poi_id
        k.execute(PENDENTES, {"uf": uf, "municipio": municipio})
        pendentes = k.fetchall()

    if not pendentes:
        return {"pendentes": 0}

    # A pergunta é "o cruzamento RODOU para o Cadastur?", e não "ele achou
    # POI?". A primeira versão perguntava a segunda: numa cidade onde nenhum
    # prestador casa com POI — que é a cidade onde o gerador mais serve — ela
    # concluía que o cruzamento não tinha rodado e se recusava a trabalhar.
    with con.cursor() as k:
        k.execute("""select exists (select 1 from comercialradar.cruzamento
                                     where 'cadastur' in (base_a, base_b))""")
        rodou = k.fetchone()[0]
    if not rodou:
        raise SystemExit(
            "o cruzamento ainda não rodou para o Cadastur.\n"
            "  Rode antes:  python cruzar_bases.py"
            + (f" --cidade {municipio}" if municipio else "") + "\n"
            "  Sem ele, todo prestador pareceria inédito e cada hotel já "
            "cadastrado ganharia uma cópia.")

    def so_digitos(v):
        return "".join(ch for ch in (v or "") if ch.isdigit())

    docs = sorted({d for d in (so_digitos(linha[1]) for linha in pendentes)
                   if len(d) == 14})
    geo = _coordenada_por_cnpj(con, docs)
    geo_cruz = _coordenada_por_cruzamento(con)
    ja = _poi_por_cnpj(con, docs)

    placar = {"pendentes": len(pendentes), "gerados": 0,
              "sem_cnpj": 0, "sem_coordenada": 0, "ja_existe": 0}
    # TRÊS listas, e não uma com nulos dentro. Um `VALUES` cuja coluna é nula em
    # todas as linhas não tem tipo inferível — o Postgres responde "could not
    # determine data type" e a carga inteira cai. Separar por destino evita o
    # problema em vez de remendá-lo com cast.
    marcas, ligados, gerados = [], [], []

    for (cid, cnpj, fantasia, razao, atividade, tipo_hosp, endereco,
         mun, uf_r, tel, email, site, cnae, uh, leitos) in pendentes:
        doc = so_digitos(cnpj)

        # O cruzamento tem prioridade sobre a busca por CNPJ: ele enxerga
        # também o casamento por nome com âncora espacial, que o documento
        # sozinho não pega.
        poi_existente = cruzados.get(str(cid)) or ja.get(doc)
        if poi_existente:
            placar["ja_existe"] += 1
            ligados.append((poi_existente, cid))
            continue
        # A coordenada vem do CNPJ quando há, e do cruzamento quando não há.
        # A ordem importa: documento igual é a chave mais forte que existe.
        origem_geo = None
        if len(doc) == 14 and doc in geo:
            la, lo = geo[doc]
            origem_geo = "cnpj_tratado"
        elif cid in geo_cruz:
            la, lo, origem_geo = geo_cruz[cid]
        else:
            # Sem CNPJ E sem cruzamento é caso diferente de sem coordenada com
            # CNPJ: o primeiro nunca vai se resolver, o segundo se resolve
            # rodando o tratamento de CNPJ do município. Separar os dois é o
            # que evita reprocessar eternamente o que não tem solução.
            if len(doc) != 14:
                placar["sem_cnpj"] += 1
                marcas.append(("sem_cnpj", cid))
            else:
                placar["sem_coordenada"] += 1
                marcas.append(("sem_coordenada", cid))
            continue
        nome = fantasia or razao
        if not nome:
            placar["sem_cnpj"] += 1        # sem nome não há POI que se mostre
            marcas.append(("sem_nome", cid))
            continue

        if simular:
            placar["gerados"] += 1
            continue

        # A CATEGORIA sai do vocabulário do MTur, e não de um chute nosso: é
        # ele que sabe a diferença entre pousada e resort. `tipo_hospedagem`
        # é mais específico que `atividade_turistica` quando existe.
        categoria = tipo_hosp or atividade

        # Passa pelo ÚNICO escritor de POI do sistema, e não por um INSERT
        # daqui. É ele que conhece a deduplicação (nome + coordenada), o merge
        # não-destrutivo e o resgate de filhos caros. Um INSERT próprio
        # ignoraria as três coisas e recriaria os bugs de agosto.
        resultado, poi_id = realtime_ingest.ingerir_registro({
            "fonte": "cadastur",
            "nome": nome,
            "categoria": categoria,
            "endereco": endereco,
            "telefone": tel,
            "email": email,
            "website": site,
            "cnpj": cnpj,
            "razao_social": razao,
            "nome_fantasia": fantasia,
            "cnae": cnae,
            "cidade": mun,
            "uf": uf_r,
            "lat_origem": la, "lng_origem": lo,
            "maps_lat": la, "maps_lng": lo,
            # A coordenada é da Receita+CNEFE, não do Maps. Dizer isso na linha
            # é o que permite ao operador saber que o ponto tem a precisão do
            # CNEFE, e não a do Google.
            # De onde saiu a COORDENADA, nomeada. "cadastur+cnefe" fixo
            # mentiria quando ela viesse do cadastro do cliente, e a precisão
            # das duas é diferente — quem olha o ponto precisa saber qual é.
            "fonte_dado": f"cadastur+{origem_geo}",
            "endereco_fonte": "cadastur",
            # O ingestor pula quem não tem `match_valido`. Aqui ele é True
            # porque o casamento é por CNPJ — documento igual, sem gradação.
            "match_valido": True,
            "status": "cadastur",
            "ia_resposta": json.dumps(
                {"uh": uh, "leitos": leitos, "atividade": atividade},
                ensure_ascii=False) if (uh or leitos) else None,
        }, conn=con)

        if poi_id:
            placar["gerados"] += 1
            gerados.append((poi_id, cid))
        else:
            # O ingestor recusou. Ele tem motivo próprio — coordenada fora do
            # Brasil, por exemplo — e guardá-lo textual é o que permite saber
            # depois se foi dado ruim ou regra nossa.
            placar["sem_coordenada"] += 1
            marcas.append((f"ingestor:{resultado}", cid))

    if not simular:
        with con.cursor() as k:
            if marcas:
                execute_values(
                    k, """update comercialradar.cadastur_prestador c
                             set sem_poi_motivo = v.motivo, cruzado_em = now()
                            from (values %s) as v(motivo, id)
                           where c.id = v.id""", marcas)
            if ligados:
                execute_values(
                    k, """update comercialradar.cadastur_prestador c
                             set poi_id = v.poi, sem_poi_motivo = 'ja_existe',
                                 cruzado_em = now()
                            from (values %s) as v(poi, id)
                           where c.id = v.id""", ligados)
            if gerados:
                # `sem_poi_motivo` fica NULO: virou POI, então não há motivo
                # para não ter virado. O literal na consulta, e não um nulo na
                # lista, é o que mantém o VALUES com tipo.
                execute_values(
                    k, """update comercialradar.cadastur_prestador c
                             set poi_id = v.poi, sem_poi_motivo = null,
                                 cruzado_em = now()
                            from (values %s) as v(poi, id)
                           where c.id = v.id""", gerados)
        con.commit()
    return placar


# ── CLI ──────────────────────────────────────────────────────────────────────

def _listar():
    sys.path.insert(0, str(SKILL))
    from cadastur import catalogo          # noqa: E402  (a skill vendorizada)
    recs = catalogo.descobrir()
    por = {}
    for r in recs:
        atual = por.get(r.dataset)
        if not atual or (r.ref_ano, r.ref_trimestre) > (atual.ref_ano,
                                                        atual.ref_trimestre):
            por[r.dataset] = r
    print(f"{len(recs)} recursos em {len(por)} conjuntos\n")
    print(f"  {'conjunto':<62}{'mais recente':>14}   entra?")
    for d in sorted(por):
        r = por[d]
        marca = "sim" if d in DATASETS_PJ else "não — pessoa física"
        print(f"  {d:<62}{r.ref_ano}T{r.ref_trimestre:<12}   {marca}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uf", default=None, help="sigla, ex.: RS")
    p.add_argument("--municipio", default=None, help="nome, ex.: Canoas")
    p.add_argument("--datasets", default=None,
                   help="lista por vírgula; padrão = as 14 de pessoa jurídica")
    p.add_argument("--desde", type=int, default=None,
                   help="ano inicial; padrão = o trimestre mais recente de "
                        "cada conjunto, que nem sempre é do ano corrente")
    p.add_argument("--so-carregar", action="store_true",
                   help="não baixa; usa o que já está em dados_externos/cadastur")
    p.add_argument("--refresh", action="store_true", help="ignora o cache da skill")
    p.add_argument("--gerar", action="store_true",
                   help="gera POIs do que não cruzou (exige cruzar_bases.py antes)")
    p.add_argument("--simular", action="store_true", help="não grava nada")
    p.add_argument("--listar", action="store_true", help="mostra o catálogo e sai")
    args = p.parse_args()

    if args.listar:
        _listar()
        return 0

    datasets = ([d.strip() for d in args.datasets.split(",") if d.strip()]
                if args.datasets else DATASETS_PJ)
    fora = [d for d in datasets
            if d not in DATASETS_PJ and "guia" in d.lower()]
    if fora:
        raise SystemExit(
            f"recusado: {', '.join(fora)} é cadastro de PESSOA FÍSICA.\n"
            "  A tabela não tem coluna para CPF, data de nascimento ou nome "
            "social — e não vai ter. Guia de turismo é pessoa que presta "
            "serviço, não economia que consome água.")

    if not args.so_carregar:
        baixar(datasets, args.desde, args.refresh)

    print("⟦fase⟧ carregando o recorte", flush=True)
    linhas = _linhas_do_parquet(SAIDA, args.uf, args.municipio)
    print(f"  {len(linhas)} linhas no recorte"
          + (f" · {args.municipio}/{args.uf}" if args.municipio else
             (f" · {args.uf}" if args.uf else " · Brasil inteiro")), flush=True)

    con = bc.conectar()
    try:
        conta = carregar(con, linhas, args.simular)
        for d in sorted(conta):
            print(f"    {d:<62}{conta[d]:>7}", flush=True)
        print(f"  {sum(conta.values())} gravadas"
              + (" (simulação)" if args.simular else ""), flush=True)

        if args.gerar:
            print("\n⟦fase⟧ gerando POIs", flush=True)
            placar = gerar(con, args.uf, args.municipio, args.simular)
            for chave in ("pendentes", "gerados", "ja_existe",
                          "sem_cnpj", "sem_coordenada"):
                if chave in placar:
                    print(f"  {chave:<18}{placar[chave]:>7}", flush=True)
            if placar.get("sem_coordenada"):
                print("\n  Os sem coordenada NÃO viraram POI e não vão virar "
                      "sozinhos: falta o CNPJ deles na `cnpj_tratado`.\n"
                      "  Rode `tratamento_cnpj.py --municipio <IBGE>` para "
                      "esse município e passe aqui de novo.", flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
