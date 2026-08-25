# -*- coding: utf-8 -*-
"""iFood como fonte de POI: o id vem do feed, o dado vem do endpoint publico.

O QUE MUDOU, E POR QUE ISTO NAO E O EXTRATOR ANTIGO

O caminho anterior do projeto colhia CNPJ raspando o TEXTO RENDERIZADO da
pagina da loja, uma loja por vez, com navegador. Rendia 1%: 16 CNPJs em 1.598
lojas descobertas. O resto ficava `PENDENTE`, que quer dizer "nao colhi" e
nunca "nao existe" — a distincao que impede a base de mentir sobre cobertura.

Medido em 25/08/2026 contra 60 ids reais do banco:

    GET marketplace.ifood.com.br/v1/merchants/{id}/extra

    60 de 60 -> HTTP 200          CNPJ presente        100%
    0,37 s de latencia media      numero do endereco   100%
    0,05 s por loja (8 threads)   CEP                  100%

Sem autenticacao, sem token, sem navegador. O `merchant-info/graphql` (403 do
PerimeterX) e o `/v1/merchants/{id}` sem sufixo (400) continuam fechados — o
`/extra` e a porta aberta, e e a unica de que precisamos.

A DIVISAO DE TRABALHO QUE ISSO CRIA

    enumerar ids   caro     navegador + proxy; o feed `home:fallback` exige um
                            `search_token` que so a propria aplicacao produz
    detalhar       barato   HTTP puro — este modulo

Este modulo faz SO a metade barata. Os ids entram por um provedor injetado
(`sementes`), do mesmo jeito que `listar_fsq` entra no resolvedor do Foursquare:
a skill nao sabe — e nao precisa saber — se vieram de um navegador, de uma
tabela do produto ou de um arquivo. Amarrar a skill ao Playwright seria trocar
uma dependencia de 1 GB por nada.

PROXY MESMO RESPONDENDO DIRETO

O endpoint responde 200 do IP direto. Ainda assim as chamadas saem pelo pool,
por decisao explicita de 25/08/2026: uma varredura estadual concentra dezenas de
milhares de requisicoes num IP so, e o Akamai que ja devolve 403 no
`home:fallback` protege o mesmo dominio. Descobrir isso no meio de uma UF custa
a UF inteira. Sem pool disponivel o modulo AVISA e segue direto — degradar em
silencio seria pior que degradar.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .config import salvar_atomico

# `extrair_pois` NAO e importado no topo, e isso e deliberado.
#
# O pacote `vendor` puxa `geopandas` no proprio `__init__`. Esta fonte e a unica
# que nao precisa de geometria nenhuma — e HTTP e dicionario. Importar no topo
# faria `detalhar()` exigir a pilha geoespacial inteira para buscar um CNPJ, e
# e justamente `detalhar()` que se quer reusar fora do pipeline (num backfill,
# num script do produto, num teste). O esquema continua vindo de la, uma vez so,
# quando o DataFrame e montado — nunca duplicado aqui, para nao divergir.

BASE = "https://marketplace.ifood.com.br/v1/merchants/%s/extra"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
CABECALHOS = {"User-Agent": UA, "Accept": "application/json",
              "Accept-Language": "pt-BR,pt;q=0.9",
              "Referer": "https://www.ifood.com.br/"}

# Resposta FINAL: a loja saiu do ar ou o id nunca existiu. Repetir nao a traz de
# volta, e numa varredura estadual seriam milhares de tentativas inuteis.
FINAIS = (400, 404, 410)

# Colunas nativas que este adapter produz. Declaradas aqui, e nao inferidas do
# primeiro registro: uma loja sem `groups` faria a coluna sumir do parquet, e o
# `concat` da retomada acabaria com esquemas diferentes no mesmo arquivo.
NATIVAS = ("if.cnpj", "if.mcc", "if.short_id", "if.tipo", "if.categoria",
           "if.faixa_preco", "if.avaliacoes", "if.tempo_preparo_min",
           "if.tempo_entrega_min", "if.pedido_minimo", "if.complemento",
           "if.logradouro", "if.numero", "if.uf", "if.operacoes", "if.canais",
           "if.recursos", "if.modelo", "if.regiao")


def _dir(cfg, man):
    col, wk = man.colecao("ifood")
    return cfg.dir_colecao("ifood", col or "sem_colecao", wk or "sem_work")


def _proxies_do_pool(pool):
    """Proxies para o `urllib`, vindos de um pool do produto OU de um arquivo.

    A skill nao importa o `ProxyPool`: ela aceita o objeto por injecao, ou um
    arquivo com uma URL por linha. Importar o modulo do produto amarraria a
    skill a este repositorio, e ela e vendorada em outros.
    """
    if pool is None:
        return ()
    if isinstance(pool, (str, bytes, os.PathLike)):
        if not os.path.exists(pool):
            return ()
        with open(pool, encoding="utf-8") as fh:
            return tuple(l.strip() for l in fh if l.strip() and not l.startswith("#"))
    if isinstance(pool, (list, tuple)) and all(isinstance(x, str) for x in pool):
        return tuple(pool)
    try:
        itens = list(getattr(pool, "_proxies", ()) or ())
    except Exception:                                          # noqa: BLE001
        return ()
    saida = []
    for p in itens:
        srv = (p.get("server") or "").replace("http://", "")
        if not srv:
            continue
        usuario, senha = p.get("username"), p.get("password") or ""
        saida.append("http://%s:%s@%s" % (usuario, senha, srv) if usuario
                     else "http://" + srv)
    return tuple(saida)


def sementes_de_arquivo(caminho):
    """Provedor de ids a partir de arquivo: `.txt` (um por linha) ou parquet/csv.

    E o contrato entre a enumeracao (que precisa de navegador e mora no produto)
    e esta fonte (que nao precisa de nada). Quem enumerou grava os ids; a skill
    le. Nenhum dos dois precisa conhecer o outro.
    """
    def _ler():
        if not os.path.exists(caminho):
            raise RuntimeError(
                "arquivo de sementes do iFood nao existe: %s\n"
                "Ele e produzido pela ENUMERACAO (navegador). Sem ele a fonte "
                "nao tem o que buscar." % caminho)
        ext = os.path.splitext(caminho)[1].lower()
        if ext in (".parquet", ".pq"):
            d = pd.read_parquet(caminho)
            col = "merchant_id" if "merchant_id" in d.columns else d.columns[0]
            return [str(x) for x in d[col].dropna()]
        if ext == ".csv":
            d = pd.read_csv(caminho, dtype=str)
            col = "merchant_id" if "merchant_id" in d.columns else d.columns[0]
            return [str(x) for x in d[col].dropna()]
        with open(caminho, encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    return _ler


def _abrir(url, proxy=None, timeout=25):
    if proxy:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        op = urllib.request.build_opener()
    return op.open(urllib.request.Request(url, headers=CABECALHOS), timeout=timeout)


def _tel(d):
    """`areaCode` e `phoneIf` vem separados; nenhum dos dois sozinho disca."""
    ddd = str(d.get("areaCode") or "").strip()
    num = str(d.get("phoneIf") or "").strip()
    if not num:
        return None
    return ("(%s) %s" % (ddd, num)) if ddd else num


def _endereco(a):
    partes = [a.get("streetName"), a.get("streetNumber"), a.get("streetCompl")]
    txt = ", ".join(str(x).strip() for x in partes if x and str(x).strip())
    return txt or None


def _grupo(d, tipo):
    """Nome do `group` de um tipo. Medido em 25 lojas reais (25/08/2026):

        STORE_TYPE      25/25   RESTAURANT, PHARMACY        — ja vem em `type`
        BUSINESS_MODEL  25/25   Entrega+, Full Service, Marketplace
        COMPANY         25/25   IFOOD                       — sempre a plataforma
        REGION          15/25   Canoas, Gravatai
        CHAIN            4/25   Burger King, Cacau Show, Quiero Cafe

    So `CHAIN` e marca. A primeira versao deste adapter pegava o primeiro grupo
    de nome diferente do da loja e gravava "RESTAURANT" como marca em TODAS —
    um campo cheio, plausivel e inteiramente falso.
    """
    for g in (d.get("groups") or ()):
        if (g.get("type") or "") == tipo:
            nome = (g.get("name") or "").strip()
            if nome:
                return nome
    return None


def _linha(d):
    """JSON do `/extra` -> esquema COMUNS + nativas `if.*`. None se nao georreferencia."""
    a = d.get("address") or {}
    docs = d.get("documents") or {}
    cnpj = ((docs.get("CNPJ") or {}).get("value") or "").strip() or None
    cat = (d.get("mainCategory") or {}).get("description")
    tipo = d.get("type")
    lat, lon = a.get("latitude"), a.get("longitude")
    if lat is None or lon is None:
        return None
    negocio = d.get("business") or {}
    comum = dict(
        fonte="ifood", id_fonte=d.get("id"), nome=d.get("name"),
        lat=float(lat), lon=float(lon),
        categoria_orig=cat or tipo,
        # A hierarquia do iFood tem exatamente dois niveis, e os dois importam:
        # `RESTAURANT;Lanches`, `PHARMACY;Farmacia`. O segundo sozinho nao separa
        # uma farmacia de uma loja de conveniencia.
        categoria_hier=";".join(x for x in (tipo, cat) if x) or None,
        endereco_raw=_endereco(a), bairro=a.get("district"),
        localidade_fonte=a.get("city"),
        cep=(str(a.get("zipCode") or "").strip() or None),
        telefone=_tel(d), site=None, email=None, instagram=None,
        marca=_grupo(d, "CHAIN"), confianca=None,
        # `enabled` diz se a loja esta ATIVA na plataforma, nao se esta aberta
        # agora. Loja fechada as 3h da manha continua `enabled=True`.
        status=("ATIVA" if d.get("enabled") else "INATIVA"),
        data_atualizacao=None)
    nativas = {
        "if.cnpj": cnpj,
        "if.mcc": ((docs.get("MCC") or {}).get("value") or None),
        "if.short_id": d.get("shortId"),
        "if.tipo": tipo, "if.categoria": cat,
        "if.faixa_preco": d.get("priceRange"),
        "if.avaliacoes": d.get("userRatingCount"),
        "if.tempo_preparo_min": d.get("preparationTime"),
        "if.tempo_entrega_min": d.get("deliveryTime"),
        "if.pedido_minimo": d.get("minimumOrderValueV2") or d.get("minimumOrderValue"),
        "if.complemento": a.get("streetCompl"),
        "if.logradouro": a.get("streetName"),
        "if.numero": a.get("streetNumber"),
        "if.uf": a.get("state"),
        "if.operacoes": ",".join(negocio.get("operations") or ()) or None,
        "if.canais": ",".join(negocio.get("salesChannels") or ()) or None,
        "if.recursos": ",".join(d.get("features") or ()) or None,
        # `Entrega+` / `Full Service` / `Marketplace`: quem entrega, o iFood ou a
        # loja. Separa operacao propria de operacao terceirizada.
        "if.modelo": _grupo(d, "BUSINESS_MODEL"),
        # A regiao comercial do iFood. Nao substitui o municipio do IBGE (o
        # `territory` continua mandando), mas denuncia coordenada suspeita
        # quando discorda dele.
        "if.regiao": _grupo(d, "REGION"),
    }
    return {**comum, **nativas}


def detalhar(ids, proxies=(), threads=8, tentativas=3, ao_vivo=None):
    """Busca o `/extra` de cada id. Devolve (linhas, falhas).

    `falhas` NAO e ruido de log: e a lista de ids que ficaram sem dado, e ela
    precisa sobreviver ate o relatorio. Loja que deu 500 tres vezes e diferente
    de loja que deu 404 — a primeira volta na proxima rodada, a segunda nao.
    """
    linhas, falhas = [], []
    ids = list(ids)

    def um(i):
        erro = "sem_tentativa"
        for t in range(max(1, int(tentativas))):
            px = random.choice(proxies) if proxies else None
            try:
                with _abrir(BASE % i, px) as h:
                    return i, json.load(h), None
            except urllib.error.HTTPError as e:
                if e.code in FINAIS:
                    return i, None, "http_%d" % e.code
                erro = "http_%d" % e.code
            except Exception as e:                             # noqa: BLE001
                erro = type(e).__name__
            # Recuo com jitter: sem o jitter, as threads que tomam 429 juntas
            # voltam juntas e tomam 429 de novo, no mesmo instante.
            time.sleep(min(2 ** t, 8) * (0.6 + random.random() * 0.8))
        return i, None, erro

    if not ids:
        return linhas, falhas
    with ThreadPoolExecutor(max_workers=max(1, int(threads))) as ex:
        for n, (i, d, erro) in enumerate(ex.map(um, ids), 1):
            if d is None:
                falhas.append({"id_fonte": i, "motivo": erro})
            else:
                ln = _linha(d)
                if ln is None:
                    falhas.append({"id_fonte": i, "motivo": "sem_coordenada"})
                else:
                    linhas.append(ln)
            if ao_vivo and n % 200 == 0:
                ao_vivo(n, len(linhas), len(falhas))
    return linhas, falhas


def _quadro(linhas):
    """DataFrame com o esquema COMPLETO mesmo vazio ou com registros parciais."""
    from .vendor import extrair_pois as ep          # tardio: ver o topo do modulo
    colunas = list(ep.COMUNS) + list(NATIVAS)
    df = pd.DataFrame(linhas, columns=colunas) if linhas else pd.DataFrame(columns=colunas)
    return df.reindex(columns=colunas)


def executar(cfg, man, sementes=None, pool=None):
    """Etapa `fetch` da fonte iFood.

    `sementes` e um callable que devolve os merchant ids do escopo. Sem ele a
    fonte NAO inventa ids — ela para. Fonte que devolve zero em silencio vira
    dataset sem iFood com cara de dataset completo, e foi assim que uma UF
    inteira saiu OSM-only sem ninguem perceber.
    """
    # A guarda vem ANTES de `man.iniciar`: recusar depois de marcar a etapa como
    # iniciada deixa o manifesto dizendo que a coleta comecou quando ela nem
    # tinha o que coletar.
    if sementes is None:
        raise RuntimeError(
            "fonte ifood sem provedor de sementes: os merchant ids vem da "
            "enumeracao (navegador), nao deste modulo. Injete `sementes` ou "
            "tire `ifood` de --fontes.")
    man.iniciar("fetch")
    destino = os.path.join(_dir(cfg, man), "ifood.parquet")
    ids = [str(x).strip() for x in (sementes() or ()) if str(x).strip()]
    ids = list(dict.fromkeys(ids))                    # ordem estavel, sem duplicata

    # RETOMAVEL: o que ja esta no parquet nao volta para a rede. Uma varredura de
    # dezenas de milhares de lojas nao pode recomecar do zero porque caiu.
    ja = set()
    if os.path.exists(destino):
        try:
            ja = set(pd.read_parquet(destino, columns=["id_fonte"])["id_fonte"].astype(str))
        except Exception:                                      # noqa: BLE001
            ja = set()
    faltam = [i for i in ids if i not in ja]

    proxies = _proxies_do_pool(pool)
    if not proxies:
        print("  IFOOD: sem pool de proxy — as chamadas saem pelo IP direto.", flush=True)
    print("  IFOOD: %d ids no escopo | %d ja em disco | %d a buscar | %d proxies"
          % (len(ids), len(ja), len(faltam), len(proxies)), flush=True)

    t0 = time.time()
    novas, falhas = detalhar(
        faltam, proxies=proxies, threads=getattr(cfg, "threads", 8),
        ao_vivo=lambda n, ok, ruim: print("    %d/%d · %d ok · %d falha"
                                          % (n, len(faltam), ok, ruim), flush=True))

    df = _quadro(novas)
    if ja and os.path.exists(destino):
        df = pd.concat([pd.read_parquet(destino), df], ignore_index=True)
        df = df.drop_duplicates(subset=["id_fonte"], keep="last")
    salvar_atomico(df, destino)

    if falhas:
        salvar_atomico(pd.DataFrame(falhas), os.path.join(_dir(cfg, man), "falhas.parquet"))

    com_cnpj = int(df["if.cnpj"].notna().sum()) if "if.cnpj" in df.columns else 0
    print("IFOOD: %d lojas | %d com CNPJ (%.1f%%) | %d falhas | %.0fs"
          % (len(df), com_cnpj, 100.0 * com_cnpj / max(len(df), 1),
             len(falhas), time.time() - t0), flush=True)
    col, wk = man.colecao("ifood")
    man.versao_fonte("ifood", ids_escopo=len(ids), lojas=len(df),
                     com_cnpj=com_cnpj, falhas=len(falhas),
                     collection_id=col, work_id=wk,
                     snapshot_id=(man.snapshot("ifood") or {}).get("snapshot_id"))
    return {"lojas": len(df), "com_cnpj": com_cnpj, "falhas": len(falhas),
            "pontos": len(df), "completo": not falhas}
