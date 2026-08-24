# -*- coding: utf-8 -*-
"""Dá ferramentas — internet e banco — ao modelo que roda na Spark.

O QUE ISTO RESOLVE, E POR QUE A PERGUNTA ERA BOA

Nenhum modelo acessa a internet. Nem o daqui, nem o GPT, nem o Gemini: o modelo
é uma função que recebe texto e devolve texto. O que existe é um PROGRAMA em
volta que lê o pedido do modelo, faz a busca de verdade, e devolve o resultado
na conversa. A OpenAI e o Google já embrulharam esse laço no produto deles, e
por isso parece que o modelo navega.

Aqui o embrulho é este arquivo. A Spark serve `qwen3vl-moe` por API compatível
com OpenAI; o SearXNG do i9 faz as buscas; o Postgres responde as consultas.

FERRAMENTAS

  buscar_web        pergunta ao SearXNG (instância própria, sem chave, sem cota)
  abrir_pagina      baixa uma URL e devolve o texto
  consultar_banco   SELECT no banco do produto — SÓ leitura
  consultar_receita procura empresa por nome ou CNPJ nas tabelas rf_*

A restrição a SELECT não é decoração: o modelo escreve a consulta, e um modelo
que pode escrever DELETE mais cedo ou mais tarde escreve. O bloqueio é sintático
e roda antes de qualquer coisa chegar ao banco.

Uso:
    python agente_local.py "quantas lojas do iFood temos em Canoas?"
    python agente_local.py --interativo
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import base_comum as bc

SPARK = os.environ.get("SPARK_LLM_URL", "http://100.85.164.54:8000/v1")
MODELO = os.environ.get("SPARK_MODELO", "qwen3vl-moe")
SEARX = (os.environ.get("SEARXNG_URL", "http://100.115.117.49:8888")
         .split(",")[0].strip())
NL = chr(10)
MARCA_INSTRUCOES = "<<ferramentas-por-texto>>"
MARCA_MEMORIA = "<<memoria>>"
MAX_VOLTAS = 8          # teto de idas ao modelo, para não girar sem fim
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ─── as ferramentas ──────────────────────────────────────────────────────────

# Só o começo da consulta é inspecionado, mas é onde a decisão mora: `with` e
# `select` são as únicas entradas legítimas de leitura.
_INICIO_OK = re.compile(r"^\s*(with|select)\b", re.I)
# palavras que não têm o que fazer numa leitura, em qualquer posição
_PROIBIDO = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|"
    r"vacuum|call|do|merge)\b", re.I)


def _so_leitura(sql: str) -> str | None:
    """Devolve o motivo da recusa, ou None se a consulta é leitura pura."""
    if not _INICIO_OK.match(sql or ""):
        return "só SELECT ou WITH são aceitos"
    if _PROIBIDO.search(sql or ""):
        return "a consulta contém comando de escrita"
    if ";" in (sql or "").strip().rstrip(";"):
        return "uma consulta por vez"
    return None


_RUIDO_BUSCA = {"de", "da", "do", "em", "no", "na", "e", "o", "a", "para",
                "com", "endereco", "endereço", "telefone", "cnpj", "horario",
                "horário", "informacoes", "informações"}




async def _outros_motores(consulta: str, quantos: int) -> list:
    """Roda a consulta nos motores do `minerar_web`, na ordem que ele aprendeu.

    Reaproveita a infraestrutura que a camada web usa: mesma sessão stealth,
    mesmo pool, mesma contabilidade de bloqueio por motor. Nada novo — só o
    agente passando a usar o que já existia.
    """
    from playwright.async_api import async_playwright

    import minerar_web as MW

    achados = []
    async with async_playwright() as pw:
        try:
            serp = await MW.SerpPool(1, usar_proxy=True).start()
        except Exception as e:
            return [{"erro_motores": f"{type(e).__name__}: {str(e)[:120]}"}]
        try:
            res = await serp.buscar(consulta)
            for url, trecho in (res or [])[:quantos]:
                achados.append({"titulo": (trecho or "")[:90], "url": url,
                                "trecho": (trecho or "")[:400],
                                "motor": "alternativo"})
        except Exception as e:
            achados.append({"erro_motores": f"{type(e).__name__}: {str(e)[:120]}"})
        finally:
            try:
                await serp.close()
            except Exception:
                pass
    return achados


def _buscar_outros(consulta: str, quantos: int) -> list:
    """Ponte síncrona para os motores alternativos."""
    import asyncio
    try:
        return asyncio.run(asyncio.wait_for(
            _outros_motores(consulta, quantos), timeout=180))
    except Exception as e:
        return [{"erro_motores": f"{type(e).__name__}: {str(e)[:120]}"}]


def _relevancia(consulta: str, achado: dict) -> float:
    """Quantas palavras da consulta aparecem no título, na URL e no resumo.

    Palavras genéricas ("endereço", "telefone") saem da conta: elas aparecem em
    qualquer página e empatariam tudo. O que discrimina é o NOME procurado.
    """
    import unicodedata

    def limpar(s):
        s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                    if unicodedata.category(c) != "Mn")
        return {p for p in re.split(r"[^a-z0-9]+", s) if len(p) > 2}

    alvo = limpar(consulta) - _RUIDO_BUSCA
    if not alvo:
        return 0.0
    texto = limpar(f"{achado.get('titulo')} {achado.get('url')} "
                   f"{achado.get('trecho')}")
    return len(alvo & texto) / len(alvo)


def buscar_web(consulta: str, quantos: int = 6,
               aprofundar: bool = True) -> dict:
    """Busca no SearXNG e ABRE as primeiras páginas.

    O piso de 5 resultados é proposital: o modelo pediu `quantos: 1`, recebeu
    um resumo de duas linhas e desistiu da pergunta. Com um resultado não há o
    que cruzar, e cruzar é o que separa resposta útil de resposta rasa.

    `aprofundar` traz o texto das 3 primeiras páginas na MESMA chamada. Sem
    isso o modelo precisaria decidir sozinho abrir cada link — encadeamento que
    ele erra com frequência, deixando a resposta no resumo do buscador.
    """
    quantos = max(5, min(int(quantos or 6), 10))
    u = f"{SEARX}/search?q={urllib.parse.quote(consulta)}&format=json"
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())

    achados = []
    for x in (d.get("results") or [])[:quantos]:
        achados.append({"titulo": x.get("title"), "url": x.get("url"),
                        "trecho": (x.get("content") or "")[:500]})

    # Se o SearXNG não trouxe nada RELEVANTE, desce para os outros motores.
    # Não é "não achou": é "este motor não achou". O Bing mostra o painel de
    # endereço em consultas onde o SearXNG devolve diário oficial.
    if achados and max((_relevancia(consulta, a) for a in achados),
                       default=0.0) < 0.5:
        achados += [a for a in _buscar_outros(consulta, quantos)
                    if not a.get("erro_motores")][:quantos]
    elif not achados:
        achados += _buscar_outros(consulta, quantos)

    if aprofundar:
        # Abre as MAIS RELEVANTES, não as primeiras. A ordem do buscador sobe
        # PDF de governo e página antiga por peso de domínio: numa busca por
        # "Xerife Burger Teresina" as três primeiras foram um diário oficial,
        # um hotel no Ceará e um blog de rock — e as duas úteis (iFood e
        # Instagram) estavam em 4º e 5º.
        for a in sorted(achados, key=lambda x: -_relevancia(consulta, x))[:3]:
            if not a.get("url"):
                continue
            try:
                pag = abrir_pagina(a["url"], limite=3500)
                if pag.get("texto"):
                    a["conteudo"] = pag["texto"]
            except Exception as e:
                a["conteudo_erro"] = type(e).__name__

    # PERFIS SOCIAIS ficam VISÍVEIS, com a porta certa indicada.
    #
    # `abrir_pagina` usa urllib e toma HTTPError no Facebook e no Instagram:
    # eles exigem navegador. O link aparecia na lista com `conteudo_erro:
    # HTTPError` e nada mais, e o modelo concluía "não encontrado" — enquanto
    # uma busca comum no Google mostra o Instagram e o Facebook da loja.
    #
    # Quem atravessa esse muro é `consultar_instagram`, que abre navegador. Aqui
    # só se extrai o usuário e se diz qual é a porta. A decisão de bater nela
    # continua sendo do modelo.
    perfis = []
    for a in achados:
        u = (a.get("url") or "").lower()
        for sitio, dominio in (("instagram", "instagram.com/"),
                               ("facebook", "facebook.com/")):
            if dominio in u:
                usuario = u.split(dominio, 1)[1].strip("/").split("/")[0]
                if usuario and usuario not in ("p", "reel", "explore", "pages"):
                    perfis.append({"rede": sitio, "usuario": usuario,
                                   "url": a.get("url")})
    # CNPJ ACHADO EM TEXTO DE PÁGINA vem com a ordem de conferir COLADA nele.
    #
    # A regra "confirme o CNPJ na Receita antes de afirmar" já está na mensagem
    # de sistema — e foi ignorada duas vezes. No caso "Los Chiapas" o número
    # saiu de um trecho da Serasa e a resposta creditou "Fonte: Diário Cidade";
    # antes disso, outro saiu de uma busca creditado ao "Econodata". Regra longe
    # do dado não segura; aqui o aviso viaja junto do número.
    achados_cnpj = []
    for a in achados:
        for bruto in re.findall(r"\b\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}\b",
                                f"{a.get('trecho','')} {a.get('conteudo','')}"):
            so = "".join(c for c in bruto if c.isdigit())
            if len(so) == 14 and so not in achados_cnpj:
                achados_cnpj.append(so)

    extra = {}
    if achados_cnpj:
        extra["cnpjs_no_texto"] = achados_cnpj
        extra["nota_cnpj"] = (
            "Estes CNPJs vieram do TEXTO de páginas, não de fonte oficial. "
            "Antes de afirmar qualquer um: chame `consultar_receita(cnpj=...)`, "
            "que diz a razão social, o endereço e se está ATIVA ou BAIXADA. "
            "Citar como fonte o site onde o número apareceu, e nunca uma base "
            "que você não consultou.")

    if perfis:
        return {"consulta": consulta, "resultados": achados, "perfis": perfis,
                **extra,
                "nota_perfis": ("Estes perfis NÃO podem ser lidos por "
                                "`abrir_pagina` (a rede social exige navegador). "
                                "Para o Instagram use `consultar_instagram` com "
                                "o `usuario`. Confira antes se o perfil é DESTE "
                                "estabelecimento — a busca traz homônimos.")}
    return {"consulta": consulta, "resultados": achados, **extra}


def abrir_pagina(url: str, limite: int = 6000) -> dict:
    """Baixa a página e devolve o texto, sem marcação."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=40) as r:
        bruto = r.read(400_000).decode("utf-8", "replace")
        status = r.status
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", bruto, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return {"status": status, "texto": txt[:limite]}


NOMINATIM = os.environ.get("NOMINATIM_URL", "http://100.115.117.49:8080")


def buscar_lugar(nome: str, cidade: str = "", uf: str = "", limite: int = 5) -> dict:
    """Endereço de um estabelecimento no OpenStreetMap (Nominatim do i9).

    Serve para o que a busca de páginas não alcança: o endereço de um comércio
    costuma estar no painel do buscador, que não é página nenhuma. Aqui vem da
    base de mapa, com rua, número, bairro e coordenada.

    Ausência aqui significa "não está mapeado", não "não existe" — negócio
    recém-aberto costuma faltar no OpenStreetMap.
    """
    consulta = " ".join(x for x in (nome, cidade, uf) if x).strip()
    u = (f"{NOMINATIM}/search?q={urllib.parse.quote(consulta)}"
         f"&format=json&addressdetails=1&limit={max(1, min(int(limite or 5), 10))}")
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:150]}"}

    saida = []
    for x in d:
        end = x.get("address") or {}
        saida.append({
            "nome": end.get("shop") or end.get("amenity") or x.get("name")
                    or (x.get("display_name") or "").split(",")[0],
            "endereco": x.get("display_name"),
            "rua": end.get("road"),
            "numero": end.get("house_number"),
            "bairro": end.get("suburb") or end.get("neighbourhood"),
            "cidade": end.get("city") or end.get("town") or end.get("municipality"),
            "cep": end.get("postcode"),
            "lat": x.get("lat"), "lng": x.get("lon"),
            "tipo": x.get("type"),
        })
    return {"consulta": consulta, "encontrados": len(saida), "lugares": saida,
            "nota": ("nada no OpenStreetMap — pode ser negócio novo, não "
                     "significa que não existe") if not saida else None}


def consultar_banco(sql: str, limite: int = 50) -> dict:
    """SELECT no banco do produto."""
    recusa = _so_leitura(sql)
    if recusa:
        return {"erro": recusa}
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute(sql)
            cols = [c.name for c in k.description] if k.description else []
            linhas = k.fetchmany(limite)
        return {"colunas": cols,
                "linhas": [[None if v is None else str(v) for v in l]
                           for l in linhas]}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        con.close()


# A Receita guarda a situação como código. Devolver "08" ao modelo é devolver
# nada: ele não sabe, e se souber vai chutar. Por extenso, ele decide.
SITUACAO_CADASTRAL = {"01": "NULA", "02": "ATIVA", "03": "SUSPENSA",
                      "04": "INAPTA", "08": "BAIXADA"}


def _codigo_municipio(nome: str, uf: str) -> str | None:
    """"Teresina" -> "1219". A Receita usa código próprio, não o do IBGE.

    Existe porque a ferramenta recebia o nome da cidade e filtrava por ele como
    se fosse código — resultado vazio, e o agente concluindo que a empresa não
    estava na Receita.
    """
    if not nome:
        return None
    alvo = "".join(c for c in unicodedata.normalize("NFD", nome.upper())
                   if unicodedata.category(c) != "Mn").strip()
    if alvo.isdigit():
        return alvo
    con = bc.conectar_referencia()
    try:
        with con.cursor() as k:
            k.execute("""select codigo from public.rf_municipios
                          where upper(descricao) = %s limit 1""", (alvo,))
            r = k.fetchone()
            if r:
                return r[0]
            k.execute("""select codigo from public.rf_municipios
                          where upper(descricao) like %s limit 2""",
                      (f"{alvo}%",))
            achados = k.fetchall()
            # duas cidades começando igual: não se escolhe no chute
            return achados[0][0] if len(achados) == 1 else None
    except Exception:
        return None
    finally:
        con.close()


def consultar_receita(nome: str = "", cnpj: str = "", uf: str = "RS",
                      municipio: str = "canoas", endereco: str = "",
                      numero: str = "", limite: int = 15,
                      cidade: str = "") -> dict:
    """Procura empresa na Receita por CNPJ, nome ou ENDEREÇO.

    `municipio` aceita o nome da cidade ("Teresina") ou o código da Receita.

    A busca por `endereco` existe para revelar os vizinhos: no Atacadão de
    Teresina havia DOIS CNPJs ativos no mesmo número, e responder com um só
    esconde a ambiguidade de quem vai a campo.
    """
    # `cidade` é apelido de `municipio`. O modelo escreve "cidade", que é como
    # se fala; o TypeError daí matava o degrau inteiro e a resposta era montada
    # sem a Receita — com o CNPJ vindo de um trecho de busca e fonte inventada.
    if cidade:
        municipio = cidade

    con = bc.conectar_referencia()
    try:
        with con.cursor() as k:
            campos = ("cnpj", "nome_fantasia", "razao_social", "cnae",
                      "situacao", "logradouro", "numero", "bairro", "cep")
            SEL = """select e.cnpj_basico||e.cnpj_ordem||e.cnpj_dv,
                            e.nome_fantasia, m.razao_social,
                            e.cnae_principal, e.situacao_cadastral,
                            e.logradouro, e.numero, e.bairro, e.cep
                       from public.rf_estabelecimentos e
                       left join public.rf_empresas m
                         on m.cnpj_basico = e.cnpj_basico """

            if cnpj:
                d = "".join(c for c in cnpj if c.isdigit())
                k.execute(SEL + "where e.cnpj_basico||e.cnpj_ordem||e.cnpj_dv = %s",
                          (d,))
                achados = [dict(zip(campos, l)) for l in k.fetchall()]
                for a in achados:
                    a["situacao"] = SITUACAO_CADASTRAL.get(a["situacao"],
                                                           a["situacao"])
                return {"empresas": achados,
                        "nota": "CNPJ não existe na Receita" if not achados
                                else None}

            cod = _codigo_municipio(municipio, uf)
            if not cod:
                return {"erro": f"não achei o município '{municipio}' em {uf}"}

            if endereco:
                # o logradouro na Receita vem sem o tipo da via; comparar o
                # núcleo evita "AVENIDA X" não casar com "X"
                #
                # O PONTO DA ABREVIAÇÃO importa: a lista compara com "AV" e o
                # Maps escreve "Av.". Sem tirar a pontuação, "Av. Prefeito Wall
                # Ferraz" ia inteiro para o ILIKE e devolvia ZERO — enquanto
                # "Prefeito Wall Ferraz" devolvia 15. Medido.
                TIPOS = {"RUA", "AVENIDA", "AV", "R", "TRAVESSA", "TV",
                         "ALAMEDA", "AL", "PRACA", "PCA", "RODOVIA", "ROD",
                         "ESTRADA", "ESTR"}
                nucleo = " ".join(
                    p for p in (w.strip(".,;:") for w in endereco.upper().split())
                    if p and p not in TIPOS)
                # O NÚMERO FILTRA NO BANCO, não depois. Filtrar em Python sobre
                # as 15 primeiras linhas era inútil: numa avenida com centenas
                # de empresas, a do número certo quase nunca cai nas 15 que o
                # `limit` deixou passar — e o cruzamento voltava vazio parecendo
                # ausência de registro.
                if numero:
                    so_digitos = "".join(c for c in str(numero) if c.isdigit())
                    k.execute(SEL + """where e.uf=%s and e.municipio=%s
                                         and e.logradouro ilike %s
                                         and regexp_replace(e.numero,
                                                            '[^0-9]', '', 'g') = %s
                                       order by (e.situacao_cadastral='02') desc
                                       limit %s""",
                              (uf, cod, f"%{nucleo}%", so_digitos, limite))
                else:
                    k.execute(SEL + """where e.uf=%s and e.municipio=%s
                                         and e.logradouro ilike %s
                                       order by (e.situacao_cadastral='02') desc
                                       limit %s""",
                              (uf, cod, f"%{nucleo}%", limite))
            else:
                # SEM o filtro de ATIVA. Escondê-las devolvia ZERO para os
                # dois "Los Chiapas" de Canoas — ambos baixados — e o modelo foi
                # inventar o CNPJ num trecho de busca. Num radar comercial,
                # empresa baixada no endereço de uma loja aberta é achado.
                k.execute(SEL + """where e.uf=%s and e.municipio=%s
                                     and (e.nome_fantasia ilike %s
                                          or m.razao_social ilike %s)
                                   order by (e.situacao_cadastral='02') desc
                                   limit %s""",
                          (uf, cod, f"%{nome}%", f"%{nome}%", limite))

            achados = [dict(zip(campos, l)) for l in k.fetchall()]

            # SEGUNDA TENTATIVA SO COM A PALAVRA DISTINTIVA.
            #
            # A busca e por FRASE INTEIRA, e o ramo na frente do nome mata a
            # consulta. Medido: "Supermercado Vancosty" em Canoas devolvia 0, e
            # "Vancosty" devolvia 1 — ATIVA, fantasia VANCOSTY, o CNPJ certo.
            # A Receita registra "VANCOSTY COMERCIO E DISTRIBUICAO"; o
            # "Supermercado" so existe na placa.
            #
            # E o que uma pessoa faz: tira o generico e procura pelo que
            # distingue. Sem isso, o degrau devolvia vazio e o modelo ia buscar
            # o CNPJ em trecho de pagina, com fonte inventada.
            alargou = None
            if not achados and nome:
                GENERICAS = {"supermercado", "mercado", "restaurante", "padaria",
                             "loja", "comercio", "comercial", "distribuidora",
                             "lanchonete", "bar", "cafe", "farmacia", "acougue",
                             "pizzaria", "hamburgueria", "sorveteria", "de",
                             "da", "do", "dos", "das", "e"}
                nucleo = [p for p in nome.split()
                          if p.lower().strip(".,") not in GENERICAS and len(p) > 2]
                if nucleo and " ".join(nucleo) != nome:
                    alvo = max(nucleo, key=len)     # a palavra que distingue
                    k.execute(SEL + """where e.uf=%s and e.municipio=%s
                                         and (e.nome_fantasia ilike %s
                                              or m.razao_social ilike %s)
                                       order by (e.situacao_cadastral='02') desc
                                       limit %s""",
                              (uf, cod, f"%{alvo}%", f"%{alvo}%", limite))
                    achados = [dict(zip(campos, l)) for l in k.fetchall()]
                    if achados:
                        alargou = (f"nada com '{nome}'; a busca foi refeita so "
                                   f"com '{alvo}' — confira se e o mesmo negocio")

            for a in achados:
                a["situacao"] = SITUACAO_CADASTRAL.get(a["situacao"],
                                                       a["situacao"])
            ativas = [a for a in achados if a["situacao"] == "ATIVA"]

            nota = None
            if not achados:
                nota = ("nada com esse nome nesse município, nem ativo nem "
                        "baixado — pode ser que o nome fantasia não esteja "
                        "registrado (comum em MEI). Tente pelo endereço.")
            elif not ativas:
                # o caso Los Chiapas: os dois baixados, e esconder isso fazia o
                # modelo inventar o CNPJ num trecho de página
                nota = (f"ATENÇÃO: {len(achados)} encontrado(s), NENHUM ativo "
                        f"(situação: {', '.join(sorted({a['situacao'] for a in achados}))}). "
                        f"Empresa baixada no endereço de uma loja em "
                        f"funcionamento costuma ser troca de titularidade ou o "
                        f"mesmo dono por outro CNPJ — não descarte, confira o "
                        f"endereço de cada uma.")
            elif len(achados) > 1:
                # o alerta que faltou no Atacadão: dois no mesmo número
                portas = {}
                for a in achados:
                    portas.setdefault((a["logradouro"], a["numero"]), []).append(a["cnpj"])
                juntos = {f"{r}, {n}": c for (r, n), c in portas.items()
                          if len(c) > 1}
                if juntos:
                    nota = (f"ATENÇÃO: mais de um CNPJ ativo no mesmo endereço "
                            f"({juntos}). Cite todos — não escolha um calado.")
            if alargou:
                nota = f"{alargou}. {nota}" if nota else alargou
            return {"municipio_usado": cod, "empresas": achados, "nota": nota}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        con.close()


def consultar_maps(nome: str, cidade: str = "") -> dict:
    """Abre o Google Maps e lê o painel do estabelecimento."""
    from ferramenta_maps import consultar_maps as _m
    return _m(nome, cidade)


def lembrar(resumo: str, detalhe: str = "", tipo: str = "fato") -> dict:
    """Guarda algo que deve sobreviver a esta conversa."""
    from ferramenta_memoria import lembrar as _l
    return _l(resumo, detalhe, tipo)


def recordar(assunto: str = "", detalhado: bool = False) -> dict:
    """O que já se sabe sobre um assunto, de conversas anteriores."""
    from ferramenta_memoria import recordar as _r
    return _r(assunto, detalhado)


def consultar_instagram(usuario: str) -> dict:
    """Perfil público no Instagram: seguidores, bio, horário, links."""
    from ferramenta_instagram import consultar_instagram as _i
    return _i(usuario)


def usar_skill(nome: str) -> dict:
    """Carrega o método documentado do projeto para um assunto."""
    from ferramenta_skills import usar_skill as _s
    return _s(nome)


def guardar_ponto(**kw) -> dict:
    """Grava o estabelecimento confirmado, fotografa a fachada e cruza.

    É o ÚLTIMO DEGRAU que faltava ao chat. Todas as outras ferramentas leem: o
    agente descobria telefone, CNPJ e endereço, a conversa terminava e o banco
    continuava sem nada. Esta fecha o ciclo pelo mesmo caminho dos demais
    processos — o escritor único de POI, a captura de fachada e o cruzamento.
    """
    from ferramenta_ponto import guardar_ponto as _g
    return _g(**kw)


FERRAMENTAS = {
    "buscar_web": buscar_web,
    "recordar": recordar,
    "lembrar": lembrar,
    "usar_skill": usar_skill,
    "consultar_instagram": consultar_instagram,
    "consultar_maps": consultar_maps,
    "abrir_pagina": abrir_pagina,
    "buscar_lugar": buscar_lugar,
    "consultar_banco": consultar_banco,
    "consultar_receita": consultar_receita,
    "guardar_ponto": guardar_ponto,
}

# ─── onde os degraus pesados executam ────────────────────────────────────────
#
# Maps e Instagram abrem Chromium, ~300 MB cada. No notebook isso impunha teto
# de quatro; o i9 tem 51 GB livres e comporta dezesseis. Com WORKER_REDE_URL
# definida, eles executam lá; sem ela, ou com o i9 fora do ar, seguem aqui —
# a queda é automática e vem registrada em `executado_em`.
try:
    import cliente_rede
    FERRAMENTAS = cliente_rede.envolver(FERRAMENTAS)
except Exception as _e:      # nunca deixar a ligação derrubar o agente
    print(f"   ⚠️  worker de rede indisponível ({_e}); tudo roda local")


# O lote NÃO é envolvido de propósito: ele chama as ferramentas por dentro, no
# processo onde roda. Envolvê-lo mandaria cada degrau à rede outra vez.
try:
    from ferramenta_lote import buscar_varios as _buscar_varios
    FERRAMENTAS["buscar_varios"] = _buscar_varios
except Exception:
    pass

# Confirmar o CNPJ e afirmar o CNPJ sao coisas diferentes. `consultar_receita`
# acha candidatos; esta diz se o candidato E o ponto — inclusive dizendo que NAO
# e, que e resultado util: poupa a visita e diz onde procurar em seguida.
try:
    from julgar_identidade import confirmar_cnpj as _confirmar_cnpj
    FERRAMENTAS["confirmar_cnpj"] = _confirmar_cnpj
except Exception:
    pass

# Quando a Receita nao acha pelo nome — e nao achar e COMUM, porque o nome da
# placa nao e o do registro — a web sabe ligar um ao outro. Mas so como pista:
# cada candidato volta para ser confirmado contra a Receita.
try:
    from buscar_empresa import buscar_dados_empresariais as _buscar_empresa
    FERRAMENTAS["buscar_dados_empresariais"] = _buscar_empresa
except Exception:
    pass


def _esquema_memoria():
    try:
        from ferramenta_memoria import ESQUEMA as e
        return list(e)
    except Exception:
        return []


def _esquema_instagram():
    try:
        from ferramenta_instagram import ESQUEMA as e
        return [e]
    except Exception:
        return []


def _esquema_skills():
    """O esquema da skill lista o catálogo REAL — skill nova aparece sozinha."""
    try:
        from ferramenta_skills import ESQUEMA as e
        return [e]
    except Exception:
        return []


def _esquema_empresa():
    try:
        from buscar_empresa import ESQUEMA as e
        return [e]
    except Exception:
        return []


def _esquema_cnpj():
    try:
        from julgar_identidade import ESQUEMA_CNPJ as e
        return [e]
    except Exception:
        return []


def _esquema_lote():
    try:
        from ferramenta_lote import ESQUEMA as e
        return [e]
    except Exception:
        return []


def _esquema_ponto():
    """`guardar_ponto`: o degrau que fecha o ciclo do chat."""
    try:
        from ferramenta_ponto import ESQUEMA as e
        return [e]
    except Exception:
        return []


ESQUEMA = (_esquema_memoria() + _esquema_skills() + _esquema_instagram()
           + _esquema_lote() + _esquema_cnpj() + _esquema_empresa()
           + _esquema_ponto()) + [
    {"type": "function", "function": {
        "name": "buscar_web",
        "description": "Busca na internet E JÁ ABRE as primeiras páginas, "
                       "devolvendo o texto delas. Use para achar endereço, "
                       "telefone, CNPJ, horário, avaliações, redes sociais.",
        "parameters": {"type": "object", "properties": {
            "consulta": {"type": "string"},
            "quantos": {"type": "integer"},
            "aprofundar": {"type": "boolean"}}, "required": ["consulta"]}}},
    {"type": "function", "function": {
        "name": "abrir_pagina",
        "description": "Baixa uma URL e devolve o texto. Use nos links que a "
                       "busca trouxer.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "consultar_maps",
        "description": "Abre o Google Maps e le o PAINEL do estabelecimento: "
                       "endereco completo, telefone, horario, avaliacao e "
                       "coordenada. E o cartao a direita na busca, que NENHUMA "
                       "busca de paginas alcanca. LENTA (abre navegador, "
                       "dezenas de segundos): use quando o usuario pedir "
                       "endereco/telefone/horario de um comercio especifico. "
                       "Confira `confere_com_o_nome_pedido` — falso significa "
                       "que o Maps abriu OUTRO estabelecimento.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "cidade": {"type": "string"}}, "required": ["nome"]}}},
    {"type": "function", "function": {
        "name": "buscar_lugar",
        "description": "Endereço de um ESTABELECIMENTO no mapa (OpenStreetMap): "
                       "rua, número, bairro, CEP e coordenada. Use quando "
                       "precisar do endereço de uma loja, restaurante ou "
                       "mercado — a busca de páginas raramente traz isso. "
                       "Vazio significa 'não mapeado', não 'não existe'.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"}, "cidade": {"type": "string"},
            "uf": {"type": "string"}}, "required": ["nome"]}}},
    {"type": "function", "function": {
        "name": "consultar_banco",
        "description": "SELECT no banco do produto (schema comercialradar). "
                       "Tabelas: pois, ifood_merchant, cadastro_cliente, "
                       "cnpj_tratado, cruzamento. Só leitura.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string"},
            "limite": {"type": "integer"}}, "required": ["sql"]}}},
    {"type": "function", "function": {
        "name": "consultar_receita",
        "description": "Procura empresa na Receita por CNPJ, nome ou ENDEREÇO. "
                       "`municipio` aceita o NOME da cidade ('Teresina'). "
                       "Busque por `endereco` para ver todos os CNPJs de uma "
                       "mesma porta — rede grande costuma ter dois.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"}, "cnpj": {"type": "string"},
            "uf": {"type": "string"},
            "municipio": {"type": "string", "description": "nome da cidade"},
            "endereco": {"type": "string",
                         "description": "logradouro, para achar vizinhos"},
            "numero": {"type": "string",
                       "description": "numero da porta; use SEMPRE junto com `endereco`, senao a rua inteira volta e o cruzamento nao vale"}}}}},
]

SISTEMA = (
    "Você é o assistente do ComercialRadar, que mapeia pontos comerciais para "
    "concessionárias de saneamento." + NL + NL +
    "Use as ferramentas em vez de responder de memória. Dado de banco e de "
    "busca é verificável; memória não é." + NL + NL +
    "REGRA DURA SOBRE NÚMEROS: você só pode escrever um número que apareça "
    "num resultado de ferramenta DESTA conversa. Se não consultou, não "
    "escreva. Não complete a frase com o número que parece provável." + NL +
    "Pergunta com duas partes exige DUAS consultas. Depois da primeira, "
    "verifique se ainda falta algo antes de responder." + NL +
    "Quando não achar, diga que não achou — estimativa aqui vira visita de "
    "campo a endereço que não existe." + NL + NL +
    # O modelo não adivinha convenção de dados. Este bloco existe porque ele
    # consultou `cidade = 'Canoas'` numa coluna que guarda 'canoas' e concluiu
    # que a base estava vazia.
    "ESQUEMA (schema comercialradar):" + NL +
    "- ifood_merchant: merchant_id, nome, categoria, bairro, nota, cidade, uf, "
    "cnpj, rua, numero, cep, lat, lng, estado_detalhe, slug." + NL +
    "  ATENÇÃO: `cidade` é minúscula e com hífen ('canoas', 'porto-alegre')." + NL +
    "  `estado_detalhe`: PENDENTE (não colhido), OK, SEM_RETORNO." + NL +
    "- pois: id, nome, endereco, cidade, cnpj, maps_lat, maps_lng, categoria, "
    "presente_no_ifood. Aqui `cidade` vem com maiúscula ('Canoas')." + NL +
    "- cadastro_cliente: base do cliente — logradouro, numero, bairro, cep, "
    "lat, lng, e_comercial, cidade." + NL +
    "- cruzamento: base_a, id_a, base_b, id_b, chave (cnpj|endereco|nome|geo), "
    "score, ambiguo." + NL +
    "Na dúvida sobre os valores de uma coluna, faça SELECT DISTINCT antes de "
    "filtrar." + NL + NL +
    # Sem isto o agente recomeça do zero a cada conversa e repete tentativa que
    # já falhou — o usuário responde a mesma coisa pela terceira vez.
    # A escada existe porque descrição de ferramenta não diz ORDEM nem quando
    # parar. Sem ela o modelo tentava uma, não achava e devolvia a tarefa.
    # Escrita depois de o agente citar "Fonte: Econodata" para um CNPJ que veio
    # de um trecho de busca — e de haver DOIS CNPJs ativos no mesmo endereço,
    # com só um sendo mencionado.
    "SOBRE CNPJ:" + NL +
    "- Antes de afirmar um CNPJ, CONFIRME com `consultar_receita`. A ferramenta "
    "é instantânea e diz se o número existe, se está ativo e em que endereço." + NL +
    "- Se a Receita devolver MAIS DE UM estabelecimento no mesmo endereço, "
    "cite todos e diga que há mais de um. Rede grande costuma ter matriz "
    "fiscal e loja no mesmo número — escolher um calado esconde a dúvida." + NL +
    "- CNPJ que não confere com a cidade pedida NÃO é o da loja: é homônimo. "
    "Diga isso em vez de entregá-lo." + NL + NL +
    "SOBRE PROCEDÊNCIA:" + NL +
    "- Cite como fonte a FERRAMENTA que você usou, com o nome dela: "
    "`consultar_receita`, `consultar_maps`, `buscar_web`, `buscar_lugar`, "
    "`consultar_instagram`." + NL +
    "- Se o dado veio de um site que a busca trouxe, cite o SITE e diga que "
    "veio da busca. Nunca nomeie uma fonte que você não consultou: fonte "
    "errada é pior que fonte ausente, porque impede a auditoria depois." + NL + NL +
    "ESCADA DE BUSCA — quando pedirem dados de um estabelecimento:" + NL +
    "1. `recordar` — já se sabe algo sobre ele ou sobre esse tipo de busca?" + NL +
    "2. `consultar_banco` — já está em `pois` ou `ifood_merchant`?" + NL +
    "3. `consultar_maps` — **A FONTE PRINCIPAL**. O painel do Google traz "
    "endereço com número, telefone, horário da semana, nota, categoria, fotos "
    "e coordenada, tudo de uma vez. SEMPRE chame para um estabelecimento "
    "específico, mesmo que a busca web já tenha mostrado um endereço." + NL +
    "4. `consultar_receita` — CNPJ e razão social. Se o nome não achar, busque "
    "pelo `endereco` E `numero` que o Maps devolveu: é o cruzamento pela porta." + NL +
    "5. `consultar_instagram` — atividade do ponto: `ultima_publicacao` diz se "
    "a loja está viva ou parada há meses." + NL +
    "6. `buscar_web` e `buscar_lugar` — para o que faltou: redes sociais, site, "
    "notícias, ou endereço quando o Maps não achou o lugar." + NL + NL +
    "POR QUE O MAPS VEM ANTES, e não depois:" + NL +
    "- Guia local e diretório de empresas têm endereço desatualizado, raramente "
    "têm telefone, e NUNCA têm horário, foto, nota ou coordenada." + NL +
    "- Já aconteceu de a resposta creditar endereço e telefone ao 'Guia Canoas' "
    "quando o Maps tinha os MESMOS dados, mais o horário e seis fotos. Isso é "
    "trocar a fonte boa pela rápida." + NL +
    "- A sessão do Maps é reaproveitada entre consultas; ele não é mais o "
    "degrau caro que era." + NL + NL +
    "REGRAS DA ESCADA:" + NL +
    "- Endereço, telefone ou horário de um comércio: o Maps é obrigatório. Não "
    "responda esses campos por busca web sem ter chamado `consultar_maps`." + NL +
    "- Se o Maps trouxe o dado, a FONTE é `consultar_maps` — mesmo que um site "
    "repita a mesma informação." + NL +
    "- PERFIL DE REDE SOCIAL ACHADO exige `consultar_instagram` com aquele "
    "usuario. Nao basta citar o @: o que interessa e `ultima_publicacao` — "
    "perfil parado ha meses e ponto provavelmente fechado, e isso so se "
    "descobre abrindo. Citar o perfil sem a data e entregar meia informacao." + NL +
    "- RECEITA VAZIA NAO E FIM: se `consultar_receita` nao achar pelo nome, "
    "chame `buscar_dados_empresariais` com o nome, a cidade e o ENDERECO que o "
    "Maps deu. Ela pergunta a web com o vocabulario de cadastro (cnpj, razao "
    "social, inscricao) e ja devolve cada candidato confirmado ou negado. "
    "Nao achar pelo nome e comum: a placa diz 'Espetao Vancosty' e o registro "
    "diz 'Vancosty Comercio e Distribuicao'." + NL +
    "- CNPJ NUNCA se afirma sem `confirmar_cnpj`. `consultar_receita` acha "
    "CANDIDATOS; `confirmar_cnpj` diz se o candidato e ESTE ponto, comparando "
    "endereco, nome fantasia e CNAE. Nome igual em endereco diferente costuma "
    "ser outra unidade da rede — e um 'nao' e resposta util, nao fracasso: "
    "diga que o CNPJ encontrado NAO e deste ponto e por que." + NL +
    "- Ao terminar, diga DE ONDE veio cada dado e o que não foi encontrado." + NL +
    "- Se esgotar os degraus sem achar, diga isso, listando o que tentou. Isso "
    "é uma resposta legítima; mandar o usuário procurar não é." + NL + NL +
    "MEMÓRIA: chame `recordar` ANTES de responder sobre o projeto (iFood, "
    "Receita, cruzamentos, extração). Lá está o que já foi decidido e o que já "
    "se provou que não funciona." + NL +
    "Chame `lembrar` quando algo for concluído — um fato, uma decisão, uma "
    "preferência sua, ou um limite. Guarde a conclusão, não a narrativa." + NL + NL +
    # O modelo desistia na primeira busca infrutífera e devolvia "recomendo
    # verificar o nome". Uma busca sem resultado é o COMEÇO do trabalho.
    "AO BUSCAR NA WEB: uma busca sem resultado não encerra o assunto. Tente "
    "pelo menos DUAS formulações diferentes antes de dizer que não achou — "
    "com a cidade, sem a cidade, com o endereço, com o nome da rede." + NL +
    "Nunca responda \"recomendo verificar o nome\": quem tinha que verificar "
    "é você." + NL +
    "A busca já devolve o TEXTO das primeiras páginas no campo `conteudo`. "
    "Leia esse texto antes de concluir — é onde estão endereço, telefone e "
    "horário, não no resumo." + NL + NL +
    # A imagem é o material mais saliente da mensagem, e descrevê-la é a
    # saída de menor esforço: o modelo achava que já tinha respondido.
    "QUANDO HOUVER IMAGEM ANEXADA: o que você vê nela é o PONTO DE PARTIDA, "
    "não a resposta. Se o usuário pede dados que não estão na imagem — CNPJ, "
    "endereço, telefone, horário — leia o NOME na imagem e procure: "
    "`buscar_lugar` para ENDEREÇO, `buscar_web` para o resto. Descrever a imagem e mandar o usuário procurar no site não é "
    "resposta; é devolver a tarefa para quem a pediu."
)



# O vLLM só aceita `tools` se tiver subido com --enable-auto-tool-choice e
# --tool-call-parser. Sem isso devolve 400. Como reiniciar o servidor derruba o
# que estiver rodando, o agente detecta a recusa e passa a pedir a chamada no
# próprio texto — funciona em qualquer modelo, sem tocar no servidor.
_NATIVO = None      # None = ainda não sei; True/False após a sondagem


def _instrucoes_por_texto() -> str:
    """As instruções de ferramenta escritas no prompt, para servidor sem suporte.

    Montadas a partir do MESMO `ESQUEMA` do modo nativo — assim uma ferramenta
    nova aparece nos dois caminhos sem ninguém precisar lembrar de duplicar.
    """
    linhas = []
    for f in ESQUEMA:
        fn = f["function"]
        props = fn["parameters"].get("properties", {})
        linhas.append(f'- {fn["name"]}: {fn["description"]}')
        linhas.append(f'  argumentos: {json.dumps(props, ensure_ascii=False)}')
    return (
        NL + NL + MARCA_INSTRUCOES + NL
        + "Você pode usar ferramentas. Para usar uma, responda APENAS com este"
        + " JSON, sem texto em volta:" + NL + NL
        + '{"ferramenta": "<nome>", "argumentos": {...}}' + NL + NL
        + "Ferramentas:" + NL + NL.join(linhas) + NL + NL
        + "Quando tiver a resposta final, responda em texto normal, SEM JSON."
        + NL + "Uma ferramenta por vez."
    )


_RE_JSON = re.compile(r'\{.*"ferramenta".*\}', re.S)


def _extrair_pedidos(texto: str, teto: int = 6) -> list:
    """TODOS os pedidos de ferramenta no texto, quando não há modo nativo.

    Era só o primeiro. Mas o modelo costuma planejar a escada inteira numa
    resposta só — medido: oito chamadas enfileiradas numa volta. Pegar uma e
    descartar as outras fazia ele replanejar tudo na volta seguinte: 304 s
    contra 57 s da mesma busca, e o histórico inchando de 2885 para 17937
    tokens com planos jogados fora.

    O modo nativo sempre pôde devolver vários `tool_calls`; isto só dá ao modo
    por texto a mesma capacidade.
    """
    t = texto or ""
    achados, vistos = [], set()
    for ini in range(len(t)):
        if t[ini] != "{":
            continue
        nivel, dentro_str, escapa = 0, False, False
        for fim in range(ini, len(t)):
            c = t[fim]
            if escapa:
                escapa = False
                continue
            if c == "\\":
                escapa = True
            elif c == '"':
                dentro_str = not dentro_str
            elif not dentro_str:
                if c == "{":
                    nivel += 1
                elif c == "}":
                    nivel -= 1
                    if nivel == 0:
                        try:
                            d = json.loads(t[ini:fim + 1])
                        except Exception:
                            break
                        if isinstance(d, dict) and \
                                d.get("ferramenta") in FERRAMENTAS:
                            # o mesmo pedido duas vezes no plano é engano do
                            # modelo, não intenção: entra uma vez só
                            chave = json.dumps(d, sort_keys=True,
                                               ensure_ascii=False)
                            if chave not in vistos:
                                vistos.add(chave)
                                achados.append(d)
                                if len(achados) >= teto:
                                    return achados
                        break
    return achados


def _extrair_pedido(texto: str) -> dict | None:
    """O primeiro pedido, para quem só quer um. Mantido por compatibilidade."""
    p = _extrair_pedidos(texto, teto=1)
    return p[0] if p else None




def _indice_memoria() -> str:
    """As memórias em uma linha cada, para entrar no sistema.

    Só os resumos: o detalhe fica para quem chamar `recordar`, e despejar tudo
    aqui gastaria a janela com texto que noventa por cento das perguntas não
    usa. É a divulgação progressiva — índice barato, detalhe sob demanda.
    """
    try:
        from ferramenta_memoria import recordar as _r
        d = _r("")
    except Exception:
        return ""
    itens = d.get("memorias") or []
    if not itens:
        return ""
    linhas = [f"- [{m['tipo']}] {m['resumo']}" for m in itens[:30]]
    return (NL + NL + "O QUE VOCÊ JÁ SABE (de conversas anteriores):" + NL
            + NL.join(linhas) + NL
            + "Use `recordar` com o assunto para o detalhe de qualquer uma. "
              "Não contradiga isto sem verificar.")


def _chamar(mensagens: list, usar_ferramentas: bool = True) -> dict:
    global _NATIVO
    corpo = {"model": MODELO, "messages": mensagens, "temperature": 0.2,
             "max_tokens": 1500}
    if usar_ferramentas and _NATIVO is not False:
        corpo["tools"] = ESQUEMA
        corpo["tool_choice"] = "auto"

    def _post(c):
        req = urllib.request.Request(
            f"{SPARK}/chat/completions", method="POST",
            data=json.dumps(c).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())

    try:
        d = _post(corpo)
        if "tools" in corpo:
            _NATIVO = True
        return d
    except urllib.error.HTTPError as e:
        if "tools" not in corpo:
            raise
        # servidor sem suporte nativo: cai para o modo por texto, uma vez só
        _NATIVO = False
        corpo.pop("tools", None)
        corpo.pop("tool_choice", None)
        return _post(corpo)



_RE_NUM = re.compile(r"\d[\d.,]{2,}")


def _numeros(texto: str) -> set:
    """Números com 3+ dígitos, sem separadores — os que valem conferir."""
    return {re.sub(r"[.,]", "", m) for m in _RE_NUM.findall(texto or "")}


def _sem_lastro(resposta: str, retornos: list) -> set:
    """Os números da resposta que não aparecem em nenhum retorno."""
    tudo = _numeros(" ".join(retornos))
    return {n for n in _numeros(resposta) if n not in tudo}


def _detectar_nativo() -> bool:
    """O servidor aceita `tools`? Descobre ANTES do laço começar.

    Sem isto, a primeira volta gastava a pergunta: ia com ferramentas, tomava
    400, caía para o modo texto e respondia — mas o modelo ainda não tinha visto
    as instruções, então respondia de memória. Uma chamada mínima resolve a
    dúvida e o resultado fica em cache para as seguintes.
    """
    global _NATIVO
    if _NATIVO is not None:
        return _NATIVO
    corpo = {"model": MODELO, "max_tokens": 1,
             "messages": [{"role": "user", "content": "."}],
             "tools": ESQUEMA, "tool_choice": "auto"}
    req = urllib.request.Request(
        f"{SPARK}/chat/completions", method="POST",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
        _NATIVO = True
    except urllib.error.HTTPError:
        # QUALQUER erro na sondagem significa "não dá para usar nativo".
        # A versão anterior só reconhecia 400, e este servidor devolve 500
        # (biblioteca xgrammar incompatível na imagem) — o agente concluía que
        # havia suporte e quebrava na primeira pergunta de verdade.
        _NATIVO = False
    except Exception:
        _NATIVO = False
    return _NATIVO


def _parametros_de(nome_ferramenta: str) -> list:
    """O que a ferramenta realmente aceita, lido da assinatura."""
    import inspect
    fn = FERRAMENTAS.get(nome_ferramenta)
    if not fn:
        return []
    try:
        return [p for p in inspect.signature(fn).parameters
                if p not in ("self", "args", "kwargs")]
    except Exception:
        return []


def _parecido(nome_ferramenta: str, args: dict) -> str:
    """Aponta o parâmetro certo quando o errado se parece com ele.

    "cidade" e "municipio" não se parecem como texto, mas são a mesma ideia —
    por isso os sinônimos conhecidos entram à mão. O resto sai por semelhança.
    """
    import difflib
    aceitos = _parametros_de(nome_ferramenta)
    if not aceitos:
        return ""
    SINONIMOS = {"cidade": "municipio", "municipio": "cidade",
                 "estado": "uf", "empresa": "nome", "razao_social": "nome",
                 "logradouro": "endereco", "rua": "endereco",
                 "documento": "cnpj", "query": "consulta", "termo": "consulta"}
    for dado in args:
        if dado in aceitos:
            continue
        alvo = SINONIMOS.get(dado)
        if alvo and alvo in aceitos:
            return f"use `{alvo}` no lugar de `{dado}`"
        perto = difflib.get_close_matches(dado, aceitos, n=1, cutoff=0.6)
        if perto:
            return f"use `{perto[0]}` no lugar de `{dado}`"
    return f"parâmetros aceitos: {', '.join(aceitos)}"


def conversar(pergunta: str, historico: list | None = None,
              ao_vivo=None) -> tuple[str, list]:
    """Roda o laço até o modelo parar de pedir ferramenta.

    Devolve (resposta, histórico atualizado). `ao_vivo` é chamado a cada passo
    com (tipo, dado) para quem quiser mostrar o progresso.
    """
    _detectar_nativo()
    retornos: list = []
    msgs = list(historico or [{"role": "system", "content": SISTEMA}])

    # AS INSTRUÇÕES VÃO PERTO DA PERGUNTA, não só na mensagem de sistema.
    #
    # Numa conversa de 83 mensagens com uma imagem no meio, o modelo parou de
    # buscar e passou a descrever a imagem antiga. Com um lembrete logo antes
    # da pergunta ele voltou a querer chamar ferramenta na primeira tentativa.
    # O sistema fica a dezenas de mensagens da decisão; a imagem, a duas.
    #
    # O formato exato vai junto de propósito: no teste, um lembrete improvisado
    # sem o formato fez o modelo inventar a chave `chamada` no lugar de
    # `ferramenta`, e nada foi reconhecido.
    #
    # Só no modo por texto. Com suporte nativo o esquema viaja em campo próprio
    # do protocolo e não disputa atenção com o histórico.
    if _NATIVO is False and len(msgs) > 6:
        msgs.append({"role": "user", "content":
                     "Antes de responder, releia como pedir ferramenta." + NL +
                     "Imagem ou documento enviado ANTES nesta conversa é "
                     "contexto; não é o assunto da pergunta a seguir." + NL +
                     # AS RESPOSTAS ANTERIORES NÃO SÃO MODELO. Medido: numa
                     # conversa de 131 mensagens o modelo chamou só o Maps e
                     # reproduziu o formato exato das respostas velhas — com as
                     # OMISSÕES delas. As ferramentas passaram a devolver
                     # avaliações com data e o perfil de rede social já pronto,
                     # e nada disso aparecia, porque nenhuma resposta antiga
                     # tinha esses campos.
                     "SUAS RESPOSTAS ANTERIORES NESTA CONVERSA NÃO SÃO MODELO. "
                     "As ferramentas mudam e passam a devolver campos novos. "
                     "Responda pelo que o resultado da ferramenta traz AGORA, "
                     "não pelo formato que você usou antes: se vier "
                     "`avaliacoes`, mostre-as com nota e data; se vier "
                     "`perfil_social`, chame `consultar_instagram` com aquele "
                     "usuário e traga a data da última publicação; se vier "
                     "`fotos`, cite os endereços delas." +
                     _instrucoes_por_texto()})

    msgs.append({"role": "user", "content": pergunta})

    # pedidos já atendidos NESTA pergunta -> resultado. Por pergunta e
    # não por processo: a mesma consulta amanhã pode ter outra resposta.
    _ja_pedidos: dict[str, str] = {}

    for volta in range(MAX_VOLTAS):
        # no modo por texto, as instruções das ferramentas vão no sistema
        # MARCA própria, não uma palavra qualquer: a primeira versão testava
        # se "ferramenta" já aparecia no sistema — e aparece, porque o próprio
        # texto do sistema manda usar as ferramentas. A condição nunca era
        # verdadeira e as instruções nunca entravam.
        # A memória entra no COMEÇO do sistema, não no fim. No rodapé ela
        # era ignorada: o modelo preferia o número que tinha acabado de
        # calcular a uma frase a 86% do prompt.
        if msgs and msgs[0]["role"] == "system" and MARCA_MEMORIA not in msgs[0]["content"]:
            msgs[0] = {"role": "system",
                       "content": MARCA_MEMORIA + _indice_memoria()
                                  + NL + NL + msgs[0]["content"]}
        if _NATIVO is False and msgs[0]["role"] == "system"                 and MARCA_INSTRUCOES not in msgs[0]["content"]:
            msgs[0] = {"role": "system",
                       "content": msgs[0]["content"] + _instrucoes_por_texto()}
        r = _chamar(msgs)
        m = r["choices"][0]["message"]
        chamadas = m.get("tool_calls") or []
        conteudo = m.get("content") or ""
        if not chamadas and _NATIVO is False:
            pedidos = _extrair_pedidos(conteudo)
            chamadas = [{"id": f'{p["ferramenta"]}-{i}', "function": {
                "name": p["ferramenta"],
                "arguments": json.dumps(p.get("argumentos") or {},
                                        ensure_ascii=False)}}
                        for i, p in enumerate(pedidos)]
            if chamadas:
                # O PLANO NÃO VAI PARA O HISTÓRICO. Guardar o muro de JSON —
                # até 1500 tokens por volta — foi o que fez o prompt sair de
                # 2885 para 17937 tokens numa pergunta só. O que importa dele
                # já virou `chamadas`, e os resultados voltam logo abaixo.
                #
                # E fica VAZIO, não um marcador. Eu punha "[plano: maps,
                # receita, ...]" aqui, e isso VAZOU como resposta final ao
                # usuário — a mesma armadilha do "(chamei a ferramenta X)":
                # texto meu no lugar do dele acaba virando fala dele.
                conteudo = ""
        msgs.append({"role": "assistant",
                     "content": conteudo,
                     **({"tool_calls": chamadas} if chamadas and _NATIVO else {})})
        if not chamadas:
            texto = conteudo
            # Resposta VAZIA nao encerra: o modelo emitiu um plano que ja foi
            # executado, ou simplesmente nao escreveu nada. Pedir de novo custa
            # uma volta; devolver vazio custa a pergunta inteira.
            if not texto.strip() and volta < MAX_VOLTAS - 1:
                msgs.append({"role": "user", "content":
                             "Voce nao escreveu resposta. Responda a pergunta "
                             "com o que os resultados acima trouxeram."})
                continue
            # O código NÃO corrige a resposta — ele se recusa a encerrar quando
            # o modelo cita número que nenhuma ferramenta devolveu. Quem
            # conserta continua sendo o modelo, com a pergunta de volta.
            orfaos = _sem_lastro(texto, retornos)
            if orfaos and volta < MAX_VOLTAS - 1 and retornos:
                if ao_vivo:
                    ao_vivo("sem_lastro", sorted(orfaos))
                msgs.append({"role": "user", "content":
                             "Estes números não apareceram em nenhum resultado "
                             "de ferramenta: " + ", ".join(sorted(orfaos)) +
                             ". Consulte o que faltar e responda de novo, sem "
                             "citar número que você não tenha consultado."})
                continue
            if ao_vivo:
                ao_vivo("resposta", texto)
            return texto, msgs

        for c in chamadas:
            fn = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            if ao_vivo:
                ao_vivo("ferramenta", {"nome": fn, "args": args})

            # FREIO DE REPETIÇÃO. Sem suporte nativo a ferramentas o agente roda
            # no modo por texto, e nele o modelo repetia o mesmo pedido volta
            # após volta — num teste real, `consultar_receita` com um endereço
            # inventado, até o tempo acabar. O código não corrige a resposta:
            # devolve o resultado que já existia e AVISA que é repetição, para o
            # modelo escolher outro caminho em vez de bater na mesma porta.
            assinatura = fn + "|" + json.dumps(args, sort_keys=True,
                                               ensure_ascii=False)
            if assinatura in _ja_pedidos:
                saida = {"repetido": True,
                         "aviso": (f"Você já chamou `{fn}` com exatamente estes "
                                   f"argumentos nesta conversa, e o resultado "
                                   f"está acima. Repetir não traz nada novo — "
                                   f"use OUTRA ferramenta, mude os argumentos, "
                                   f"ou responda com o que já reuniu."),
                         "resultado_anterior": _ja_pedidos[assinatura][:1200]}
                if ao_vivo:
                    ao_vivo("retorno", {"nome": fn, "saida": saida})
                texto_saida = json.dumps(saida, ensure_ascii=False)
                msgs.append({"role": "tool", "tool_call_id": c.get("id", fn),
                             "name": fn, "content": texto_saida} if _NATIVO
                            else {"role": "user",
                                  "content": f"Resultado de {fn}:{NL}{texto_saida}"})
                continue

            try:
                saida = FERRAMENTAS[fn](**args) if fn in FERRAMENTAS else \
                    {"erro": f"ferramenta desconhecida: {fn}"}
            except TypeError as e:
                # ARGUMENTO ERRADO ENSINA, NÃO SÓ RECLAMA.
                #
                # Visto no chat: `consultar_receita(cidade=...)` quando o
                # parâmetro é `municipio`. O TypeError seco fazia o degrau
                # sumir, e a resposta era montada sem a Receita — com o CNPJ
                # tirado de um trecho de busca e fonte inventada.
                saida = {"erro": f"{type(e).__name__}: {str(e)[:160]}",
                         "aceita": _parametros_de(fn),
                         "dica": _parecido(fn, args)}
            except Exception as e:
                saida = {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
            if ao_vivo:
                ao_vivo("retorno", {"nome": fn, "saida": saida})
            texto_saida = json.dumps(saida, ensure_ascii=False)[:8000]
            _ja_pedidos[assinatura] = texto_saida
            retornos.append(texto_saida)
            if _NATIVO:
                msgs.append({"role": "tool", "tool_call_id": c.get("id", fn),
                             "name": fn, "content": texto_saida})
            else:
                # servidor sem suporte a ferramentas recusa o papel "tool" — o
                # resultado volta como fala do usuário, que é o que ele aceita
                msgs.append({"role": "user",
                             "content": f"Resultado de {fn}:{NL}{texto_saida}"})

    # teto atingido: pergunta uma última vez SEM ferramentas, para o modelo
    # concluir com o que já reuniu em vez de devolver a conversa pela metade
    msgs.append({"role": "user", "content":
                 "Chega de ferramentas. Responda AGORA a pergunta com tudo o "
                 "que os resultados acima trouxeram, dizendo a fonte de cada "
                 "campo e o que nao foi encontrado."})
    r = _chamar(msgs, usar_ferramentas=False)
    texto = r["choices"][0]["message"].get("content") or ""
    msgs.append({"role": "assistant", "content": texto})
    return texto, msgs


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pergunta", nargs="*")
    p.add_argument("--interativo", action="store_true")
    a = p.parse_args()

    def mostrar(tipo, dado):
        if tipo == "ferramenta":
            arg = json.dumps(dado["args"], ensure_ascii=False)[:120]
            print(f"   🔧 {dado['nome']}({arg})", flush=True)
        elif tipo == "sem_lastro":
            print(f"   ⚠ sem lastro: {', '.join(dado)} — devolvendo",
                  flush=True)
        elif tipo == "retorno":
            s = json.dumps(dado["saida"], ensure_ascii=False)
            print(f"   ↩  {len(s)} bytes"
                  + ("  ⚠ " + dado["saida"]["erro"][:70]
                     if isinstance(dado["saida"], dict)
                     and dado["saida"].get("erro") else ""), flush=True)

    if a.interativo:
        hist = None
        print(f"modelo {MODELO} na Spark · ferramentas: "
              f"{', '.join(FERRAMENTAS)}\nCtrl+C encerra.\n", flush=True)
        while True:
            try:
                q = input("você> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not q:
                continue
            resp, hist = conversar(q, hist, mostrar)
            print(f"\n{resp}\n", flush=True)

    pergunta = " ".join(a.pergunta) or "quantas lojas do iFood temos em Canoas?"
    print(f"você> {pergunta}\n", flush=True)
    resp, _ = conversar(pergunta, None, mostrar)
    print(f"\n{resp}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
