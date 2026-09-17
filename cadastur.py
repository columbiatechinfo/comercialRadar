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

PESSOA FÍSICA: CONTADA, NÃO GUARDADA

O Cadastur tem 15 atividades; uma delas — guia de turismo — é PESSOA FÍSICA,
com CPF, data de nascimento, nome social e até tipo sanguíneo.

Nenhuma linha dela entra em `cadastur_prestador` — a tabela não tem coluna para
receber. O que entra é o NÚMERO, em `cadastur_total_pf`: quantos guias há
naquele município, naquele trimestre. Contar não identifica ninguém; guardar
identificaria, e não há finalidade num produto que procura economia que consome
água.

Ignorar o conjunto inteiro — o que a primeira versão fazia — jogava fora uma
informação legítima de mercado junto com o dado pessoal.

ATUALIZAÇÃO

A skill já traz a metodologia: cache por `recurso_id` revalidado por SHA-256, e
`eventos_entidade.csv.gz` classificando cada entidade em ENTROU / ALTEROU /
PERMANECEU / SAIU. Aqui isso vira carga incremental — `PERMANECEU` não é
regravado — e quem SAIU ganha `saiu_em` sem ser apagado: sumir do arquivo
significa ter perdido regularidade, e isso é motivo para olhar de novo.

USO

    python cadastur.py --listar
    python cadastur.py --uf RS --municipio Canoas --gerar --encadear
    python cadastur.py --uf RS --municipio Canoas --so-carregar --gerar
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
import geocodificar
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

# PESSOA FÍSICA. Guia de turismo é a única, hoje.
#
# Ela É baixada — mas nenhuma linha dela entra em `cadastur_prestador`. Vira um
# NÚMERO em `cadastur_total_pf`: quantos guias há naquele município, naquele
# trimestre. Contar não identifica ninguém; guardar CPF, data de nascimento e
# tipo sanguíneo identifica, e não tem finalidade num produto que procura
# economia que consome água.
#
# Ignorar o conjunto inteiro — o que a versão anterior fazia — jogava fora uma
# informação legítima de mercado junto com o dado pessoal.
DATASETS_PF = ["prestadores-de-servicos-turisticos-guia-turismo_2"]

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
insert into resources_root.cadastur_prestador ({', '.join(COLS)})
values %s
on conflict (recurso_id, linha_origem) do update set
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

def _linhas_do_parquet(pasta: Path, uf: str | None, municipio: str | None,
                       datasets: list | None = None):
    """Lê o bronze e devolve só o recorte pedido, do trimestre mais recente.

    O TRIMESTRE MAIS RECENTE, e não todos: o Cadastur é uma SÉRIE, e carregar
    quatro trimestres do mesmo ano traz a mesma pousada quatro vezes. Para
    "quais economias existem hoje", a observação que vale é a última. As
    anteriores continuam no Parquet para quem quiser a série.
    """
    import pandas as pd

    arquivos = sorted((pasta / "bronze_parquet").rglob("*.parquet"))
    if datasets:
        # O bronze fica em `bronze_parquet/<dataset>/<dataset>_<ano>.parquet`,
        # então o nome da PASTA é o conjunto. Filtrar por aqui evita abrir 14
        # arquivos para depois descartar 13.
        alvo = set(datasets)
        arquivos = [a for a in arquivos if a.parent.name in alvo]
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


# ── 2b. Pessoa física: só o total ────────────────────────────────────────────

TOTAL_PF = """
insert into resources_root.cadastur_total_pf
       (dataset, atividade, uf, municipio, ref_periodo, quantidade)
values %s
on conflict (dataset, uf, municipio, ref_periodo) do update set
  quantidade    = excluded.quantidade,
  atividade     = coalesce(excluded.atividade, cadastur_total_pf.atividade),
  atualizado_em = now()
"""


def contar_pf(con, linhas: list, simular: bool = False) -> dict:
    """Conta prestadores pessoa física por município e trimestre.

    O laço NUNCA guarda a linha: lê município, UF, período e atividade, soma, e
    descarta. O CPF passa pela memória do processo e não chega ao banco — que é
    a diferença entre ler um dado público e tratá-lo.
    """
    contagem: dict = {}
    for r in linhas:
        municipio = _s(r.get("municipio"))
        uf_r = _s(r.get("uf"))
        periodo, _ = _data(r.get("_ref_periodo"))
        dataset = _s(r.get("_dataset"))
        if not (municipio and uf_r and periodo and dataset):
            continue
        chave = (dataset, _s(r.get("_atividade")), uf_r, municipio, periodo)
        contagem[chave] = contagem.get(chave, 0) + 1

    if contagem and not simular:
        with con.cursor() as k:
            execute_values(k, TOTAL_PF,
                           [(d, a, u, m, p, n)
                            for (d, a, u, m, p), n in contagem.items()])
        con.commit()
    return {f"{m}/{u}": n for (_, _, u, m, _), n in contagem.items()}


# ── Atualização incremental ──────────────────────────────────────────────────
#
# A SKILL JÁ RESOLVE A METADE DIFÍCIL, e é importante não reimplementá-la:
#
#   · cache por `recurso_id`, revalidado por SHA-256 — trimestre que não mudou
#     não é baixado de novo;
#   · `eventos_entidade.csv.gz`, comparando o snapshot novo com o anterior e
#     classificando cada entidade em ENTROU / ALTEROU / PERMANECEU / SAIU;
#   · `historico/<run_id>/` guardando cada execução.
#
# O que faltava era do NOSSO lado: a carga regravava as 388 mil linhas a cada
# execução, incluindo as que não mudaram uma vírgula.
#
# A chave da skill é `CNPJ:<14 dígitos>` por conjunto — casa direto com o nosso
# par (dataset, cnpj).
#
# SAIU NÃO APAGA. O arquivo do MTur só traz quem está regular, então sumir
# significa ter perdido regularidade: pode ter fechado, mudado de dono ou só
# atrasado a renovação. Nos três casos vale olhar de novo, e apagar destruiria
# justamente o sinal. A linha fica, com `saiu_em` preenchido.

def ler_eventos(pasta: Path) -> dict:
    """(dataset, cnpj) → evento. Vazio quando é a primeira execução."""
    arq = pasta / "eventos_entidade.csv.gz"
    if not arq.exists():
        return {}
    import csv
    import gzip
    saida = {}
    with gzip.open(arq, "rt", encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            chave = (linha.get("chave_entidade") or "")
            if not chave.startswith("CNPJ:"):
                # CERT:, CPF: e ROW: existem — a primeira é de quem não tem
                # CNPJ, a segunda é pessoa física, a terceira é linha sem
                # identidade nenhuma. Nenhuma casa com a nossa tabela, que é
                # ancorada em CNPJ.
                continue
            saida[(linha.get("_dataset"), chave[5:])] = linha.get("evento")
    return saida


def marcar_saidas(con, eventos: dict, simular: bool = False) -> int:
    """Carimba `saiu_em` em quem deixou de aparecer, e limpa quem voltou."""
    saiu = [(d, c) for (d, c), e in eventos.items() if e == "SAIU"]
    voltou = [(d, c) for (d, c), e in eventos.items()
              if e in ("ENTROU", "ALTEROU", "PERMANECEU")]
    if simular:
        return len(saiu)
    with con.cursor() as k:
        # NAO LIMPA O QUE NAO ESTA SUJO.
        #
        # `voltou` carrega todo mundo que PERMANECEU — ou seja, o snapshot
        # inteiro. Mandar 151 mil tuplas num `values` para zerar uma coluna que
        # quase ninguem tem preenchida custa minutos e nao muda uma linha. Em
        # 03/09/2026 esse `update` era a etapa 3 inteira: cinco minutos por
        # rodada, sempre com o mesmo resultado, com o snapshot parado no disco.
        #
        # Uma pergunta barata resolve: se nenhuma linha tem `saiu_em`, nao ha o
        # que limpar. Quando houver — depois de um snapshot que de fato tirou
        # alguem —, o `update` volta a rodar normalmente.
        k.execute("select 1 from resources_root.cadastur_prestador "
                  "where saiu_em is not null limit 1")
        ha_sujeira = k.fetchone() is not None
        if not ha_sujeira:
            voltou = []
        if saiu:
            execute_values(
                k, """update resources_root.cadastur_prestador c
                         set saiu_em = coalesce(c.saiu_em, current_date)
                        from (values %s) as v(dataset, cnpj)
                       where c.dataset = v.dataset
                         and regexp_replace(coalesce(c.cnpj,''), '[^0-9]', '', 'g')
                             = v.cnpj""", saiu)
        if voltou:
            # Renovação atrasada é comum: quem volta a aparecer deixa de estar
            # ausente, e manter a data antiga faria o painel acusar uma baixa
            # que já foi desfeita.
            execute_values(
                k, """update resources_root.cadastur_prestador c
                         set saiu_em = null
                        from (values %s) as v(dataset, cnpj)
                       where c.saiu_em is not null
                         and c.dataset = v.dataset
                         and regexp_replace(coalesce(c.cnpj,''), '[^0-9]', '', 'g')
                             = v.cnpj""", voltou)
    con.commit()
    return len(saiu)


def destino_vazio(con) -> bool:
    """A tabela de destino esta vazia?

    Existe para desarmar o incremental quando o marcador e o banco discordam.
    E uma consulta so, e ela roda uma vez por execucao — o custo e nada perto
    de uma carga que nao grava e diz que esta tudo certo.
    """
    with con.cursor() as cur:
        cur.execute("select 1 from resources_root.cadastur_prestador limit 1")
        return cur.fetchone() is None


def filtrar_por_evento(linhas: list, eventos: dict) -> tuple:
    """Devolve (linhas a gravar, quantas ficaram de fora).

    Sem eventos — primeira execução — grava tudo. Com eventos, só ENTROU e
    ALTEROU: PERMANECEU é idêntico ao que já está no banco, e regravá-lo é
    trabalho e I/O para chegar ao mesmo lugar.

    A PREMISSA TEM UMA CONDIÇÃO, e ela não estava escrita: "já está no banco".
    O marcador de eventos vive em DISCO e é gravado quando o snapshot é
    baixado — antes, e independentemente, da gravação no banco.

    Em 31/08/2026 as duas coisas se separaram: o download gravou o snapshot, a
    escrita no banco falhou (a tabela ainda tinha RLS por empresa), e a partir
    dali toda execução dizia "804 inalteradas — não regravadas · 0 gravadas",
    com a tabela VAZIA. Código de saída 0, e nada no banco.

    Quem chama precisa conferir o destino antes de confiar aqui — é o que
    `destino_vazio()` faz. Um marcador de incremental só vale enquanto o que ele
    marca tiver de fato chegado ao lugar.
    """
    if not eventos:
        return linhas, 0
    manter, pulou = [], 0
    for r in linhas:
        doc = "".join(c for c in str(r.get("cnpj") or "") if c.isdigit())
        ev = eventos.get((r.get("_dataset"), doc))
        if ev == "PERMANECEU":
            pulou += 1
            continue
        manter.append(r)
    return manter, pulou


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
    from radar_comercial.cruzamento
   where 'cadastur' in (base_a, base_b) and 'pois' in (base_a, base_b)
) t group by cad_id
"""

# A FILA E O QUE AINDA NAO TEM VINCULO — e o vinculo mora em radar_comercial.
#
# Ate 01/09/2026 esta consulta perguntava `where poi_id is null and
# sem_poi_motivo is null` a COLUNAS DA PROPRIA BASE. Quando os dados de teste
# foram limpos, as colunas nao foram junto (limpar tabela base e proibido), e a
# fila viu zero pendente numa cidade com 185 prestadores. Nada falhou: a etapa
# imprimiu "pendentes 0" e seguiu.
#
# A RLS de `cadastur_vinculo` faz o resto sozinha: o LEFT JOIN so enxerga os
# vinculos DESTA empresa, entao a fila e naturalmente por tenant — que e o que
# faltava, ja que `cadastur_prestador` nao tem `id_empresa`.
PENDENTES = """
select c.id, c.cnpj, c.nome_fantasia, c.razao_social, c.atividade_turistica,
       c.tipo_hospedagem, coalesce(c.endereco_comercial, c.endereco_rfb),
       c.municipio, c.uf, c.telefone, c.email, c.website, c.cnae, c.uh, c.leitos
  from resources_root.cadastur_prestador c
  left join radar_comercial.cadastur_vinculo v on v.cadastur_id = c.id
 where v.id is null
   and (%(uf)s is null or upper(c.uf) = upper(%(uf)s))
   and (%(municipio)s is null or lower(c.municipio) = lower(%(municipio)s))
 order by c.id
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
                       from radar_comercial.cnpj_tratado
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
    from radar_comercial.cruzamento
   where 'cadastur' in (base_a, base_b)
     and not ambiguo
     and (case when base_a = 'cadastur' then base_b else base_a end)
         in ('cnpj_tratado', 'cadastro_cliente', 'ifood_merchant')
),
com_geo as (
  select p.cad_id, p.outra, p.score, c.lat, c.lng
    from pares p join radar_comercial.cadastro_cliente c on c.id::text = p.outro_id
   where p.outra = 'cadastro_cliente' and c.lat is not null
  union all
  select p.cad_id, p.outra, p.score, t.lat, t.lng
    from pares p join radar_comercial.cnpj_tratado t on t.id::text = p.outro_id
   where p.outra = 'cnpj_tratado' and t.lat is not null
  union all
  select p.cad_id, p.outra, p.score, i.lat, i.lng
    from pares p join radar_comercial.ifood_merchant i on i.merchant_id = p.outro_id
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
                       from radar_comercial.pois
                      where cnpj is not null
                        and regexp_replace(cnpj, '[^0-9]', '', 'g') = any(%s)
                      group by 1""", (cnpjs,))
        return dict(k.fetchall())


# ── A TERCEIRA ÂNCORA: o CNEFE, por endereço ─────────────────────────────────
#
# As duas primeiras dependem de o CNPJ estar na `cnpj_tratado` (âncora 1) ou de
# o endereço bater com um imóvel do cliente (âncora 2). As duas falham fora de
# município já trabalhado — e é justamente lá que o Cadastur mais serve, porque
# lá não há POI nenhum.
#
# O CNEFE tem 111 milhões de endereços do Censo 2022 com coordenada, e o
# Cadastur nos dá logradouro e número em 81% das linhas. É casamento por texto,
# não por documento — e por isso a PRECISÃO É DECLARADA, nunca embutida:
#
#     porta        número idêntico, coordenada colhida no próprio endereço
#     porta_face   número idêntico, coordenada anotada na mesma face
#     nao_casou    o resto — e o resto NÃO vira POI
#
# `nv_geo_coord` diz de onde o IBGE tirou a coordenada. O domínio é do
# `Dicionario_CNEFE_Censo_2022.xls`:
#
#     1  ENDERECO_ORIGINAL    colhida naquele endereço no Censo   → aceita
#     2  ENDERECO_MODIFICADO  apartamentos no mesmo número        → aceita
#     3  ENDERECO_ESTIMADO    não havia original, ou era inválida → recusada
#     4  FACE_QUADRA          a face da quadra, não a porta       → recusada
#     5  LOCALIDADE           a localidade                        → recusada
#     6  SETOR_CENSITARIO     centróide do setor                  → recusada
#
# MEDIDO, e não estimado. Nas três cidades carregadas, os níveis 1 e 2 cobrem
# de 97% a 99% dos endereços; 3 e 4 somados ficam abaixo de 1%, o 5 não aparece
# e o 6 aparece duas vezes em 176 mil.
#
# E o teste que decide: aceitar 3 e 4 resgataria ZERO dos prestadores que hoje
# ficam sem coordenada. Eles falham por outro motivo — endereço sem número,
# nome de rua abreviado, número indexado dentro de um condomínio no CNEFE.
#
# Ou seja: o piso em 2 não custa cobertura nenhuma, e evita que um ponto de
# face de quadra vire porta no mapa. A skill `tratamento-cnpj` aceita até 3
# porque lá o número alimenta um score; aqui ele manda alguém a um endereço.
NV_ACEITO = ("1", "2")

# O PIOR PONTO QUE AINDA VIRA POI.
#
# `via` são 150 m: o ponto cai na rua certa, e quem vai a campo acha o negócio
# pelo nome naquela quadra. `bairro` são 800 m e `municipio` são 5 km — o
# segundo é literalmente o centróide da cidade, onde vários estabelecimentos
# empilham na mesma coordenada e ninguém acha nada.
#
# Quem fica abaixo do piso não é descartado: fica na tabela com o motivo, e
# volta para a fila quando ganhar CNPJ na `cnpj_tratado` ou quando alguém rodar
# a busca pelo painel do Maps, que dá precisão de porta.
PISO_PARA_POI = ("porta", "porta_aprox", "via")

# A LISTA DE MUNICÍPIOS VEM DA BASE, E NÃO DA WEB (dono do produto, 17/09/2026): "isso deveria ser base baixada e
# atualizada de tempos em tempos e não consulta web". A malha do IBGE já está no banco de REFERÊNCIA (`ibge_malha`,
# carregada por `area_utils.garantir_malha`), que é o mesmo caminho que o `minerar_tudo` usa para achar o código do
# município. O arquivo em `cache_ibge/` continua valendo como segunda opção para quem rodar sem o banco de referência.
CACHE_IBGE = RAIZ / "cache_ibge"


def _codigo_ibge(uf: str, municipio: str) -> str | None:
    """Código IBGE do município, que é a chave do CNEFE.

    O Cadastur dá NOME e UF; o CNEFE indexa por código. A tradução é obrigatória
    e tem de ser por (nome, UF) juntos: "Cachoeirinha" existe no RS e em PE, e
    pegar o primeiro código que aparece traz o município errado sem erro nenhum
    — foi exatamente assim que o `tratamento_cnpj` errou uma vez.
    """
    import json

    uf = (uf or "").strip().upper()
    if len(uf) != 2 or not municipio:
        return None

    # PRIMEIRO A BASE DE REFERÊNCIA: é o acervo que se atualiza de tempos em tempos, e não um pedido à internet no
    # meio da rodada. Sem ela, cai no arquivo já baixado; sem os dois, avisa o que fazer em vez de sair pela rede.
    try:
        import base_comum as _bc
        ref = _bc.conectar_referencia()
        try:
            with ref.cursor() as cur:
                cur.execute("select cod_municipio, nome from ibge_malha where uf = %s", (uf,))
                alvo_ref = _norm(municipio)
                for cod, nome in cur.fetchall():
                    if _norm(nome) == alvo_ref:
                        return str(cod)
        finally:
            ref.close()
    except Exception:                                          # noqa: BLE001
        pass                                                   # sem banco de referência: tenta o arquivo abaixo

    CACHE_IBGE.mkdir(parents=True, exist_ok=True)
    arq = CACHE_IBGE / f"municipios_{uf}.json"
    if arq.exists():
        lista = json.loads(arq.read_text(encoding="utf-8"))
    elif os.environ.get("CADASTUR_IBGE_WEB") == "1":
        # SÓ COM PEDIDO EXPLÍCITO (uma atualização manual do cache), nunca no meio de uma rodada.
        url = ("https://servicodados.ibge.gov.br/api/v1/localidades/"
               f"estados/{uf}/municipios")
        req = urllib.request.Request(url, headers={
            "User-Agent": "ComercialRadar", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=60) as r:
            bruto = r.read()
        # O IBGE responde GZIP mesmo pedindo `identity`, e o `urllib` não
        # descomprime sozinho. O sintoma engana: `json.loads` acusa
        # "invalid start byte 0x8b" e parece problema de acentuação — 0x8b é o
        # segundo byte do número mágico do gzip.
        #
        # A decisão vem dos BYTES, e não do cabeçalho `Content-Encoding`, pela
        # mesma razão que a skill do Cadastur decide formato por magic bytes: o
        # cabeçalho é o que o servidor DIZ, e este já disse errado uma vez.
        if bruto[:2] == b"\x1f\x8b":
            import gzip
            bruto = gzip.decompress(bruto)
        lista = json.loads(bruto.decode("utf-8"))
        arq.write_text(json.dumps(lista, ensure_ascii=False), encoding="utf-8")
    else:
        print("!  sem a malha do IBGE para %s: carregue a malha (area_utils.garantir_malha) ou rode uma vez com "
              "CADASTUR_IBGE_WEB=1 para atualizar %s" % (uf, arq), flush=True)
        return None
    alvo = _norm(municipio)
    for m in lista:
        if _norm(m.get("nome", "")) == alvo:
            return str(m.get("id"))
    return None


# Título de logradouro. O CNEFE guarda em COLUNA PRÓPRIA (`nom_titulo_seglogr`)
# o que o Cadastur escreve junto do nome: "General Flores da Cunha" está lá como
# titulo=GENERAL, seglogr=FLORES DA CUNHA. Sem tratar isso, NENHUMA via com
# título casava — são 12 títulos cobrindo 7.800 endereços só em Cachoeirinha.
_TITULOS = {
    "GENERAL", "DOUTOR", "DR", "SAO", "SANTO", "SANTA", "PAPA", "DONA",
    "MARECHAL", "CAPITAO", "DEPUTADO", "CORONEL", "MAJOR", "PROFESSOR",
    "PROFESSORA", "PADRE", "PRESIDENTE", "SENADOR", "VEREADOR", "ENGENHEIRO",
    "DESEMBARGADOR", "GOVERNADOR", "PREFEITO", "TENENTE", "SARGENTO",
    "ALMIRANTE", "BRIGADEIRO", "COMENDADOR", "VISCONDE", "BARAO", "IRMA",
    "FREI", "MONSENHOR", "CONEGO", "DOM",
}
# Conectivo que aparece num lado e não no outro: o CNEFE tem "DO CARVALHO"
# onde o Cadastur escreve "Avenida Carvalho".
_CONECTIVOS = {"DO", "DA", "DE", "DOS", "DAS", "E"}


def _variantes_via(rua: str | None) -> set:
    """Os nomes pelos quais uma via pode ser procurada.

    NÃO é similaridade: cada variante é uma string exata, e o casamento continua
    sendo igualdade. É a única forma honesta de reconciliar duas bases que
    escrevem o mesmo logradouro de jeitos diferentes sem inventar um score de
    0,87 que ninguém sabe interpretar.
    """
    import cruzar_bases as cb
    if not rua:
        return set()
    partes = cb.via_norm(rua).split()
    if partes and partes[0] in cb._TIPOS:
        partes = partes[1:]
    saida = set()
    # Vai tirando palavra da frente enquanto ela for título ou conectivo.
    while partes:
        saida.add(" ".join(partes))
        if partes[0].upper() in _TITULOS or partes[0].upper() in _CONECTIVOS:
            partes = partes[1:]
        else:
            break
    return {v for v in saida if v}


def _chave_via(rua: str | None, numero: str | None) -> str | None:
    """`logradouro|numero` na forma canônica — a primeira variante.

    Continua existindo porque os testes e a leitura humana precisam de UMA
    chave por endereço; a busca no CNEFE usa o conjunto de variantes.
    """
    variantes = _variantes_via(rua)
    digitos = "".join(c for c in str(numero or "") if c.isdigit())
    if not variantes or not digitos:
        return None
    return f"{sorted(variantes, key=len, reverse=True)[0]}|{digitos}"


def _coordenada_por_cnefe(pendentes: list, uf: str | None) -> dict:
    """id do prestador → (lat, lng, precisão).

    Um SELECT por MUNICÍPIO, e não por endereço. O CNEFE tem 111 milhões de
    linhas; consultá-lo por linha do Cadastur seria uma ida ao banco por
    prestador, e o índice do município já reduz o universo a dezenas de
    milhares — Cachoeirinha tem 69.715. O casamento acontece em memória.

    O banco de REFERÊNCIA é outra instância (ADR 0003): não existe JOIN entre
    ele e o banco do produto. Por isso o recorte vem para o Python.

    AMBIGUIDADE NÃO É RESOLVIDA, É RECUSADA. Em Cachoeirinha existem uma
    AVENIDA e uma RUA "Flores da Cunha" — logradouros diferentes, mesmo nome.
    Quando uma variante aponta para mais de uma via, o prestador fica sem esta
    âncora. Escolher a primeira produziria base limpa e ponto errado, e é a
    mesma regra do `cruzar_bases`: ambíguo se registra, não se resolve.
    """
    import cruzar_bases as cb

    # Agrupa o que perguntar, por município: prestadores do mesmo lugar
    # compartilham a mesma leitura do CNEFE.
    por_municipio: dict = {}
    for linha in pendentes:
        cid, endereco, municipio, uf_linha = (linha[0], linha[6],
                                              linha[7], linha[8])
        rua, numero, _ = cb.partes_cadastur(endereco, municipio)
        digitos = "".join(c for c in str(numero or "") if c.isdigit())
        variantes = _variantes_via(rua)
        if not digitos or not variantes:
            continue
        por_municipio.setdefault((uf_linha or uf, municipio), []).append(
            (cid, {f"{v}|{digitos}" for v in variantes}))
    if not por_municipio:
        return {}

    achados: dict = {}
    ref = bc.conectar_referencia()
    try:
        for (uf_m, municipio), alvos in por_municipio.items():
            cod = _codigo_ibge(uf_m, municipio)
            if not cod:
                print(f"  ⚠ sem código IBGE para {municipio}/{uf_m} — "
                      f"{len(alvos)} prestadores ficam sem esta âncora",
                      flush=True)
                continue
            with ref.cursor() as k:
                # `nom_titulo_seglogr` entra na consulta porque o CNEFE guarda
                # nele o que o Cadastur escreve junto do nome: "General Flores
                # da Cunha" está lá como titulo=GENERAL, seglogr=FLORES DA
                # CUNHA. Sem esta coluna, nenhuma via com título casava.
                k.execute("""select nom_tipo_seglogr, nom_titulo_seglogr,
                                    nom_seglogr, num_endereco,
                                    latitude, longitude, nv_geo_coord
                               from ibge_cnefe
                              where cod_municipio = %s
                                and nv_geo_coord = any(%s)
                                and latitude is not null""",
                          (cod, list(NV_ACEITO)))
                # chave → { identidade da via : (lat, lng, nv) }. O dicionário
                # aninhado é o que permite ver a ambiguidade: duas identidades
                # sob a mesma chave são duas ruas diferentes com o mesmo nome.
                indice: dict = {}
                for tipo, titulo, seglogr, num, la, lo, nv in k:
                    digitos = "".join(c for c in str(num or "") if c.isdigit())
                    if not digitos:
                        continue
                    nome = " ".join(x for x in (titulo, seglogr) if x)
                    identidade = (tipo or "", titulo or "", seglogr or "")
                    for v in _variantes_via(nome):
                        alvo = indice.setdefault(f"{v}|{digitos}", {})
                        atual = alvo.get(identidade)
                        # Nível 1 (colhido no endereço) vence o 2 (apartamento
                        # no mesmo número): um prédio tem dezenas de linhas
                        # apontando para a mesma porta.
                        if atual is None or (nv == "1" and atual[2] != "1"):
                            alvo[identidade] = (la, lo, nv)

            for cid, chaves in alvos:
                identidades: dict = {}
                for ch in chaves:
                    identidades.update(indice.get(ch, {}))
                if len(identidades) != 1:
                    continue          # ausente, ou ambíguo — nos dois casos, fora
                la, lo, nv = next(iter(identidades.values()))
                try:
                    achados[cid] = (float(la), float(lo),
                                    "cnefe:porta" if nv == "1"
                                    else "cnefe:porta_face")
                except (TypeError, ValueError):
                    continue
    finally:
        ref.close()
    return achados


def _descarregar(con, marcas: list, ligados: list, gerados: list,
                 simular: bool = False) -> None:
    """Grava os vínculos e esvazia as listas.

    CHAMADA EM LOTE, e não só no fim. A primeira versão acumulava tudo e
    gravava depois do último prestador — e quando a execução do RS inteiro foi
    interrompida no meio, 4.442 POIs já existiam no banco sem que nenhum
    prestador apontasse para eles. Os pontos estavam certos; o rastro de quem
    os gerou, perdido.

    Reconciliar aquilo deu para fazer pelo CNPJ, mas só porque CNPJ é chave
    exata. Um processo longo não pode depender de terminar para deixar o banco
    coerente.
    """
    if simular:
        marcas.clear(); ligados.clear(); gerados.clear()
        return
    with con.cursor() as k:
        # `id_empresa` NAO vai no INSERT: quem carimba e o gatilho
        # `preencher_empresa`, a partir da mesma identidade que a RLS le. Um
        # caminho so, sem chance de gravar numa empresa e enxergar de outra.
        # O gatilho e BEFORE INSERT, entao a coluna ja esta preenchida quando o
        # `ON CONFLICT (id_empresa, cadastur_id)` e avaliado.
        if marcas:
            execute_values(
                k, """insert into radar_comercial.cadastur_vinculo
                             (cadastur_id, sem_poi_motivo)
                      select v.id::bigint, v.motivo::text
                        from (values %s) as v(motivo, id)
                      on conflict (id_empresa, cadastur_id) do update
                         set sem_poi_motivo = excluded.sem_poi_motivo,
                             cruzado_em = now()""", marcas)
        if ligados:
            execute_values(
                k, """insert into radar_comercial.cadastur_vinculo
                             (cadastur_id, poi_id, sem_poi_motivo)
                      select v.id::bigint, v.poi::bigint, 'ja_existe'
                        from (values %s) as v(poi, id)
                      on conflict (id_empresa, cadastur_id) do update
                         set poi_id = excluded.poi_id,
                             sem_poi_motivo = 'ja_existe',
                             cruzado_em = now()""", ligados)
        if gerados:
            # `sem_poi_motivo` fica NULO: virou POI, então não há motivo para
            # não ter virado. O literal na consulta, e não um nulo na lista, é
            # o que mantém o VALUES com tipo.
            execute_values(
                k, """insert into radar_comercial.cadastur_vinculo
                             (cadastur_id, poi_id, sem_poi_motivo)
                      select v.id::bigint, v.poi::bigint, null
                        from (values %s) as v(poi, id)
                      on conflict (id_empresa, cadastur_id) do update
                         set poi_id = excluded.poi_id,
                             sem_poi_motivo = null,
                             cruzado_em = now()""", gerados)
    con.commit()
    marcas.clear(); ligados.clear(); gerados.clear()


# A cada quantos prestadores o vínculo é gravado. Pequeno o bastante para uma
# interrupção custar pouco, grande o bastante para não fazer um commit por
# linha — que numa execução estadual seriam 5.443 idas ao banco.
LOTE_VINCULO = 100


def gerar(con, uf: str | None, municipio: str | None,
          simular: bool = False, usar_cnefe: bool = True,
          buscar_endereco: bool = True, usar_maps: bool = False,
          area: str | None = None) -> dict:
    """Transforma em POI o que não cruzou com nada. Devolve o placar.

    EXIGE o cruzamento rodado. Sem ele, tudo pareceria inédito e o banco
    ganharia uma cópia de cada hotel que já está lá.
    """
    # LE POR MUNICIPIO, GRAVA POR AREA.
    #
    # O Cadastur e publicado por municipio, e e assim que ele entra: carregar
    # Canoas inteira uma vez serve todas as areas que vierem depois. Mas o que
    # o operador desenhou e o recorte do que ele quer VER — e ate 03/09/2026
    # a etapa gravava os 185 prestadores do municipio inteiro, mesmo com uma
    # quadra desenhada na tela.
    #
    # Sem `area`, nada muda: e o modo municipio, em que o desenho nao existe.
    poligono = None
    if area:
        import area_utils
        poligono = area_utils.carregar_area(area)
        if not poligono:
            print("  ⚠ area %r nao encontrada — gravando o municipio inteiro"
                  % area, flush=True)

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
        k.execute("""select exists (select 1 from radar_comercial.cruzamento
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
    geo_cnefe = _coordenada_por_cnefe(pendentes, uf) if usar_cnefe else {}
    ja = _poi_por_cnpj(con, docs)

    placar = {"pendentes": len(pendentes), "gerados": 0,
              "sem_cnpj": 0, "sem_coordenada": 0, "ja_existe": 0,
              # De onde saiu a coordenada de cada POI gerado. Sem isto, o
              # operador vê "17 gerados" sem saber que 9 deles têm precisão de
              # porta e 8 de face — e são coisas diferentes na hora da visita.
              "por_ancora": {}}
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
        # A precisão sai da âncora que resolveu, e não de um padrão: é ela que
        # sabe se o ponto é a porta, o prédio ou a rua.
        precisao, incerteza = "porta", 15
        if len(doc) == 14 and doc in geo:
            la, lo = geo[doc]
            origem_geo = "cnpj_tratado"
        elif cid in geo_cruz:
            la, lo, origem_geo = geo_cruz[cid]
            # O cruzamento com `cadastro_cliente` casa por ENDEREÇO: é o imóvel
            # certo, não necessariamente a porta da loja dentro dele.
            if origem_geo == "cadastro_cliente":
                precisao, incerteza = "porta_aprox", 40
        elif cid in geo_cnefe:
            # Terceira: endereço contra o CNEFE, por chave exata.
            la, lo, origem_geo = geo_cnefe[cid]
            if origem_geo.endswith("porta_face"):
                precisao, incerteza = "porta_aprox", 40
        elif buscar_endereco and endereco:
            # QUARTA, e a que o usuário pediu: SE TEM ENDEREÇO, PROCURA.
            #
            # As três anteriores casam por igualdade — CNPJ ou chave de
            # endereço. Quando nenhuma casa, sobra o texto como está, e é isso
            # que vai para o geocodificador. Descartar o ponto por causa de uma
            # vírgula seria perder o estabelecimento inteiro.
            #
            # A precisão vem DECLARADA pelo geocodificador e costuma ser `via`
            # (150 m), não `porta`. É menos, e é honesto: quem abrir o ponto vê
            # que a coordenada é da rua, não da porta.
            achado = geocodificar.buscar(endereco, mun, uf_r, nome=fantasia or razao,
                                         usar_maps=usar_maps)
            # PISO: `via` (150 m) é o pior ponto que ainda leva alguém ao lugar
            # — ele cai na rua certa, e quem chega lá acha o negócio pelo nome.
            #
            # `bairro` (800 m) e `municipio` (5 km) NÃO viram POI. A primeira
            # versão aceitava o que o geocodificador devolvesse, e o resultado
            # foi o que eu tinha dito que nunca faríamos: 117 pontos no
            # centróide da cidade, empilhados — em Encantado, quatro
            # estabelecimentos na mesma coordenada.
            #
            # O prestador NÃO é descartado: fica na tabela com o motivo, e a
            # coordenada grosseira não é gravada em lugar nenhum. Quando ele
            # ganhar um CNPJ na `cnpj_tratado`, ou quando alguém rodar a busca
            # pelo painel do Maps, ele volta para a fila sozinho.
            if achado and achado["precisao"] in PISO_PARA_POI:
                la, lo = achado["lat"], achado["lng"]
                origem_geo = f"{achado['fonte']}:{achado['precisao']}"
                precisao = achado["precisao"]
                incerteza = achado["incerteza_m"]
            elif achado:
                placar["sem_coordenada"] += 1
                placar["grosseira"] = placar.get("grosseira", 0) + 1
                marcas.append((f"coordenada_grosseira:{achado['precisao']}", cid))
                continue
            else:
                placar["sem_coordenada"] += 1
                marcas.append(("endereco_nao_encontrado", cid))
                continue
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

        # FORA DO DESENHO NAO ENTRA — e isto vem DEPOIS da coordenada, nao
        # antes: o prestador so tem ponto quando alguma das ancoras resolveu, e
        # e o ponto que decide se ele esta na area.
        if poligono is not None and not area_utils.ponto_no_poligono(la, lo, poligono):
            placar["fora_da_area"] = placar.get("fora_da_area", 0) + 1
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
            "coord_precisao": precisao,
            "coord_fonte": origem_geo,
            "coord_incerteza_m": incerteza,
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
            placar["por_ancora"][origem_geo] = \
                placar["por_ancora"].get(origem_geo, 0) + 1
            gerados.append((poi_id, cid))
            placar.setdefault("novos_ids", []).append(poi_id)
        else:
            # O ingestor recusou. Ele tem motivo próprio — coordenada fora do
            # Brasil, por exemplo — e guardá-lo textual é o que permite saber
            # depois se foi dado ruim ou regra nossa.
            placar["sem_coordenada"] += 1
            marcas.append((f"ingestor:{resultado}", cid))

        if len(marcas) + len(ligados) + len(gerados) >= LOTE_VINCULO:
            _descarregar(con, marcas, ligados, gerados, simular)

    _descarregar(con, marcas, ligados, gerados, simular)
    return placar


# ── Encadeamento ─────────────────────────────────────────────────────────────
#
# A sequência completa, na ordem em que uma coisa depende da outra:
#
#   1. cruzar   — o Cadastur contra POIs, cadastro do cliente, Receita e iFood
#   2. gerar    — o que não cruzou com POI nenhum vira POI
#   3. cruzar   — DE NOVO, porque agora existem POIs que não existiam no passo 1.
#                 Sem esta segunda passada, o ponto recém-nascido não cruza com
#                 o cadastro de imóveis do cliente, e é justamente esse
#                 cruzamento que diz se ele já é cobrado como comercial.
#   4. enriquecer — Maps, web, CNPJ na Receita e Street View, SÓ nos que
#                 nasceram agora. A esteira é a mesma dos demais: nada é pulado
#                 por o POI ter vindo de base pública.
#
# Cada etapa é o processo QUE JÁ EXISTE, invocado como subprocesso. Reimplementar
# qualquer uma aqui criaria uma segunda versão que envelheceria em silêncio.


def _rodar(cmd: list, titulo: str) -> int:
    print(f"\n⟦fase⟧ {titulo}", flush=True)
    print("  $ " + " ".join(cmd[1:]), flush=True)
    return subprocess.run(cmd, cwd=str(RAIZ)).returncode


def encadear(con, uf: str | None, municipio: str | None, novos: list,
             workers: int = 4, pular_streetview: bool = False) -> None:
    """Cruza de novo e enriquece os POIs recém-nascidos."""
    if not novos:
        print("\n  nenhum POI novo — nada a encadear", flush=True)
        return

    cmd = [sys.executable, "cruzar_bases.py"]
    if municipio:
        cmd += ["--cidade", municipio]
    _rodar(cmd, "cruzando de novo, agora com os POIs novos no banco")

    cmd = [sys.executable, "enriquecer_tudo.py",
           "--poi-ids", ",".join(str(i) for i in novos),
           "--workers", str(workers),
           "--out", str(RAIZ / "mineracao" / "cadastur_enriquecimento_db.json")]
    if pular_streetview:
        cmd.append("--pular-streetview")
    _rodar(cmd, f"enriquecendo os {len(novos)} POIs novos "
                "(Maps → web → CNPJ → Street View)")

    print("\n  Os POIs novos já estão visíveis no mapa e na bancada, e podem "
          "ser distribuídos a supervisor como qualquer outro — nem o mapa nem "
          "a fila filtram por fonte.", flush=True)


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
    p.add_argument("--area", default=None,
                   help="nome da area desenhada; grava so o que cai dentro. "
                        "Sem ela, grava o municipio inteiro (modo municipio).")
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
    p.add_argument("--encadear", action="store_true",
                   help="depois de gerar: cruza de novo (agora com os POIs "
                        "novos) e enriquece SÓ eles, na esteira completa")
    p.add_argument("--workers", type=int, default=4,
                   help="workers do enriquecimento encadeado")
    p.add_argument("--pular-streetview", action="store_true",
                   help="no encadeamento, não captura fachada")
    p.add_argument("--sem-pessoa-fisica", action="store_true",
                   help="não baixa nem CONTA o conjunto de guia de turismo. "
                        "A contagem não guarda dado pessoal — só o total por "
                        "município — mas o download é um arquivo a mais")
    p.add_argument("--sem-buscar-endereco", action="store_true",
                   help="não procura o endereço no geocodificador quando as "
                        "chaves exatas falham. O ponto fica sem coordenada e "
                        "não vira POI")
    p.add_argument("--coordenada-pelo-maps", action="store_true",
                   help="antes do OSM, procura o ESTABELECIMENTO no painel do "
                        "Google Maps. É a única fonte que dá precisão de PORTA "
                        "— e a mais cara: abre navegador com proxy por ponto")
    p.add_argument("--sem-cnefe", action="store_true",
                   help="não usa a âncora por endereço no CNEFE (a mais "
                        "lenta: lê o município inteiro do banco de "
                        "referência)")
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

    # O conjunto de PESSOA FÍSICA é baixado junto, mas só para ser CONTADO.
    baixar_pf = not args.sem_pessoa_fisica
    if not args.so_carregar:
        baixar(datasets + (DATASETS_PF if baixar_pf else []),
               args.desde, args.refresh)

    print("⟦fase⟧ carregando o recorte", flush=True)
    linhas = _linhas_do_parquet(SAIDA, args.uf, args.municipio, datasets)
    print(f"  {len(linhas)} linhas no recorte"
          + (f" · {args.municipio}/{args.uf}" if args.municipio else
             (f" · {args.uf}" if args.uf else " · Brasil inteiro")), flush=True)

    con = bc.conectar()
    try:
        # ── O QUE MUDOU, e só isso ──────────────────────────────────────
        eventos = ler_eventos(SAIDA)
        if eventos and destino_vazio(con):
            # O MARCADOR DIZ "NADA MUDOU" E A TABELA ESTA VAZIA. As duas coisas
            # nao podem ser verdade ao mesmo tempo: alguma gravacao anterior
            # falhou depois de o snapshot ja ter sido registrado. Confiar no
            # marcador aqui seria terminar com codigo 0 e zero linha, que foi
            # exatamente o que aconteceu em 31/08/2026.
            print("  ⚠ o marcador diz que nada mudou, mas a tabela esta VAZIA — "
                  "gravando tudo", flush=True)
            eventos = {}
        if eventos:
            linhas, pulou = filtrar_por_evento(linhas, eventos)
            if pulou:
                print(f"  {pulou} inalteradas desde o snapshot anterior — "
                      "não regravadas", flush=True)
            saiu = marcar_saidas(con, eventos, args.simular)
            if saiu:
                print(f"  ⚠ {saiu} deixaram de aparecer no Cadastur. NÃO foram "
                      "apagadas: sumir do arquivo significa ter perdido "
                      "regularidade, e isso é motivo para olhar de novo.",
                      flush=True)

        conta = carregar(con, linhas, args.simular)
        for d in sorted(conta):
            print(f"    {d:<62}{conta[d]:>7}", flush=True)
        print(f"  {sum(conta.values())} gravadas"
              + (" (simulação)" if args.simular else ""), flush=True)

        # ── Pessoa física: o total, nunca a linha ────────────────────────
        if baixar_pf:
            try:
                pf = _linhas_do_parquet(SAIDA, args.uf, args.municipio,
                                        DATASETS_PF)
            except SystemExit:
                pf = []          # o conjunto pode não ter sido baixado ainda
            if pf:
                totais = contar_pf(con, pf, args.simular)
                for lugar, n in sorted(totais.items()):
                    print(f"  {n} prestadores pessoa física em {lugar} — "
                          "contados, não guardados (sem CPF, sem nome, sem "
                          "endereço)", flush=True)

        if args.gerar:
            print("\n⟦fase⟧ gerando POIs", flush=True)
            placar = gerar(con, args.uf, args.municipio, args.simular,
                           usar_cnefe=not args.sem_cnefe,
                           buscar_endereco=not args.sem_buscar_endereco,
                           usar_maps=args.coordenada_pelo_maps, area=args.area)
            for chave in ("pendentes", "gerados", "ja_existe",
                          "sem_cnpj", "sem_coordenada"):
                if chave in placar:
                    print(f"  {chave:<18}{placar[chave]:>7}", flush=True)
            if args.encadear and not args.simular:
                encadear(con, args.uf, args.municipio,
                         placar.get("novos_ids") or [],
                         workers=args.workers,
                         pular_streetview=args.pular_streetview)
            if placar.get("por_ancora"):
                print("  de onde veio a coordenada:", flush=True)
                for a, n in sorted(placar["por_ancora"].items(),
                                   key=lambda x: -x[1]):
                    print(f"    {a:<20}{n:>5}", flush=True)
            if placar.get("sem_coordenada"):
                # A mensagem antiga culpava só o CNPJ. Com TRÊS âncoras, ficar
                # sem coordenada tem três causas possíveis — apontar a errada
                # manda o operador rodar o processo que não resolve nada.
                print(f"\n  {placar['sem_coordenada']} sem coordenada — não "
                      "viraram POI. Nenhuma das três âncoras os alcançou:\n"
                      "    · o CNPJ não está na `cnpj_tratado`;\n"
                      "    · o endereço não casou com imóvel do cliente;\n"
                      "    · o endereço não casou com o CNEFE — ou casou com "
                      "duas ruas de mesmo nome, e aí a recusa é proposital.\n"
                      "  O que costuma render: `tratamento_cnpj.py "
                      "--municipio <IBGE>` neste município, e passar aqui de "
                      "novo.\n"
                      "  Motivo linha a linha:\n"
                      "    select c.nome_fantasia, v.sem_poi_motivo, "
                      "c.endereco_comercial\n"
                      "      from radar_comercial.cadastur_vinculo v\n"
                      "      join resources_root.cadastur_prestador c "
                      "on c.id = v.cadastur_id\n"
                      "     where v.sem_poi_motivo is not null;", flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
