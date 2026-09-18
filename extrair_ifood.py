# -*- coding: utf-8 -*-
"""Descoberta reproduzível de lojas do iFood, ponto a ponto por coordenada.

O MÉTODO, e por que é este:

O feed da home tem dois endereços. O que carrega a lista inteira é
`GET site-api/v2/home:fallback?search_token=...`; o primário,
`POST site-api/v2/bm/home`, traz só a primeira dobra. (Até 26/08/2026 o
primário devolvia 403 do PerimeterX em execução automatizada. Em 02/09, com
Camoufox e proxy, os dois respondem 200 — e a nota fica porque quem ler este
arquivo vai encontrar o 403 escrito em outros lugares do repositório.)

O `search_token` não é forjável de fora: quem o produz é a própria aplicação.
Então este módulo NÃO monta requisição nenhuma. Ele abre o navegador, muda a
localização como uma pessoa mudaria — clicando em "Usar minha localização" — e
**escuta** a resposta que o site já ia buscar de qualquer jeito. É a diferença
entre ler o que chegou e arrombar o que não chegou.

DESDE 02/09/2026 A PRAÇA MUDA POR COORDENADA, e não por endereço digitado. O
que morreu em 26/08 foi o Chromium contra o Turnstile interativo, não o método:
o Camoufox atravessa. Ver `um_ponto` para o alvo do clique, que é a parte não
óbvia.

O QUE ESTE MÓDULO NÃO FAZ, E QUEM FAZ:

Não colhe CNPJ nem endereço da loja — e isso deixou de ser um limite para virar
uma divisão de trabalho. Cada loja sai daqui com `estado_detalhe='PENDENTE'`,
que quer dizer "não colhi", nunca "não existe".

Quem detalha é `poi_estadual/ifood.py` (a fonte `ifood` da skill estadual), pelo
endpoint público `/v1/merchants/{id}/extra`: JSON com CNPJ, endereço com número,
CEP, MCC e telefone, sem login e sem navegador. Medido em 25/08/2026 sobre as
1.598 lojas descobertas por este módulo — **1.598 respostas, zero falhas, 53 s,
CNPJ em 99,7%**.

O caminho antigo raspava o CNPJ do texto renderizado da página e rendia 1%. Não
era proteção intransponível: era a porta errada. O `merchant-info/graphql`
continua 403 e continua irrelevante.

Aqui fica só a metade CARA — a que precisa de navegador, porque o feed exige um
`search_token` que só a aplicação produz.

Uso:
    python extrair_ifood.py --cidade Canoas --uf RS        # o município inteiro
    python extrair_ifood.py --area                         # a área desenhada
    python extrair_ifood.py --cidade Canoas --uf RS --limite 3 --simular

Os pontos de busca saem do CNEFE (`pontos_de_busca.py`) — endereços reais com
número, numa grade, do trecho mais denso para o menos. Funciona para qualquer
município do país e para qualquer área desenhada, inclusive atravessando divisa.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import os
import re
import sys
import time
import urllib.parse
from collections import defaultdict

from psycopg2.extras import execute_values

import area_utils as au
import base_comum as bc
# Só o que este módulo usa. `trocar_bairro` saiu de propósito: uma sessão por
# ponto nasce no lugar certo e nunca troca de localização — era esse o passo
# frágil que derrubava a varredura.
#
# `abrir_navegador`, `entrar_no_app`, `tem_captcha` e `_humano` saíram em
# 02/09/2026 junto com o Chromium: os quatro serviam ao fluxo de endereço
# digitado, que deixou de existir aqui. Continuam em `enriquecer_ifood`, que os
# usa no sentido contrário (POI → link → id).
from enriquecer_ifood import BASE
# só a exceção: a `Frota` entra sob demanda em `rodar_frota`
from frota_navegacao import ErroDaPagina

# O rótulo dentro do modal de endereço, e o que o cabeçalho da página passa a
# exibir quando a praça muda. São os dois sinais do posicionamento: um para
# clicar, outro para CONFERIR que pegou.
ROTULO_LOCALIZACAO = "Usar minha localização"
EXIBIDO = """() => {
  const a = [...document.querySelectorAll("header button, [class*='address']")];
  const t = a.map(e => (e.textContent||'').trim())
             .filter(s => /Próximo de|Escolha um endereço/.test(s));
  return t.length ? t[0] : null;
}"""

# O endereço do feed que carrega a lista inteira. O primário (`bm/home`) fica
# de fora de propósito — mas o motivo mudou, e vale registrar: ele NÃO devolve
# mais 403. Medido em 02/09/2026, com Camoufox e proxy, os dois respondem 200:
#
#     site-api/v2/bm/home?latitude=…&longitude=…    200 ·  20 lojas
#     site-api/v2/home:fallback?search_token=…      200 · 480 lojas
#
# O primário traz só a primeira dobra. Ler os dois custaria uma linha e daria
# zero loja nova, porque as 20 estão dentro das 480.
ALVO_FEED = "home:fallback"
CARD_LOJAS = "MERCHANT_LIST_V2"

# Quanto esperar o SPA carregar o feed depois de trocar a localização. É o feed
# que dita o ritmo — não adianta pedir mais rápido do que ele responde.
NL = chr(10)
ESPERA_FEED = 12.0

# OS PONTOS DE BUSCA VÊM DO CNEFE, e não mais de uma lista escrita à mão.
#
# Deles hoje se usa só a COORDENADA. O `endereco` continua vindo e continua
# útil para ler o log, mas não entra mais em campo nenhum: desde 02/09/2026 a
# praça muda pelo "Usar minha localização" do modal, e o `address_number_required`
# que motivou o filtro de número da casa (`num_endereco ~ '^[1-9]'`, em
# `pontos_de_busca`) deixou de poder acontecer.
#
# O filtro fica onde está, de propósito: afrouxá-lo aumentaria a cobertura de
# células em área rural, e é mudança para medir sozinha, não de carona nesta.
#
# `pontos_de_busca` resolve isso para qualquer cidade OU área desenhada, tirando
# endereços reais dos 111 milhões do CNEFE e espalhando-os numa grade, do mais
# denso para o menos. Ver o cabeçalho de lá para o caminho que escala.


# Fração de lojas inéditas abaixo da qual o bairro não compensa. Dois
# seguidos encerram a varredura.
SATURADO = 0.02


def _slug_da_acao(acao: str | None) -> str | None:
    """O slug vem embutido no deep-link do card, percent-encoded.

        merchant?identifier=<uuid>&name=<nome>&slug=canoas-rs%2Fmega-x...
    """
    if not acao or "slug=" not in acao:
        return None
    bruto = acao.split("slug=", 1)[1].split("&", 1)[0]
    return urllib.parse.unquote(bruto) or None


def imagem_cheia(corpo: dict, lojas: list[dict]) -> None:
    """Completa `imagem` com o `baseImageUrl` da resposta, quando ele existir."""
    base = (corpo or {}).get("baseImageUrl") or ""
    if not base:
        return
    for lj in lojas:
        if lj.get("imagem") and not str(lj["imagem"]).startswith("http"):
            lj["imagem"] = base.rstrip("/") + "/" + str(lj["imagem"]).lstrip("/")


def lojas_do_feed(corpo: dict) -> list[dict]:
    """Extrai as lojas de uma resposta do feed.

    Percorre todos os cards em vez de assumir o primeiro: o feed reordena as
    seções conforme a hora e a praça, e fixar índice quebra em silêncio.
    """
    saida = []
    for secao in corpo.get("sections") or ():
        for card in secao.get("cards") or ():
            if card.get("cardType") != CARD_LOJAS:
                continue
            for c in (card.get("data") or {}).get("contents") or ():
                uuid = c.get("id") or c.get("Uuid")
                if not uuid:
                    continue
                saida.append({
                    "merchant_id": uuid,
                    "nome": c.get("name"),
                    "categoria": c.get("mainCategory"),
                    "slug": _slug_da_acao(c.get("action")),
                    # o iFood devolve o rating como float longo; duas casas é o
                    # que a tela mostra e o que significa alguma coisa
                    "nota": (round(float(c["userRating"]), 2)
                             if c.get("userRating") is not None else None),
                    "distancia_km": c.get("distance"),
                    "disponivel": c.get("available"),
                    # A IMAGEM VEM DO FEED, e não de print (17/09/2026): é o caminho do logo da loja, que se completa
                    # com o `baseImageUrl` da resposta — quem faz isso é quem chamou, que tem a resposta inteira.
                    "imagem": c.get("imageUrl"),
                })
    return saida


class Escuta:
    """Guarda as respostas do feed que o navegador receber.

    Fica pendurada no evento de resposta e não pede nada: se o site não buscar
    o feed, não há o que ler, e isso aparece no relatório como zero — não como
    erro inventado.
    """

    def __init__(self):
        self.lojas: dict[str, dict] = {}
        self.respostas = 0
        self.por_bairro: dict[str, int] = defaultdict(int)
        self.bairro = "?"

    def __call__(self, resp):
        # Síncrona desde 02/09/2026: quem dirige o navegador agora é o Camoufox
        # pelo Scrapling, e o `page.on` dele chama retorno comum.
        if ALVO_FEED not in resp.url:
            return
        try:
            corpo = json.loads(resp.text())
        except Exception:
            return
        self.respostas += 1
        novas = 0
        for lj in lojas_do_feed(corpo):
            lj["bairro_busca"] = self.bairro
            if lj["merchant_id"] not in self.lojas:
                novas += 1
            # o registro mais recente vence: o mesmo UUID visto de dois bairros
            # traz a mesma loja, e a última leitura é a mais atual
            self.lojas[lj["merchant_id"]] = lj
        self.por_bairro[self.bairro] += novas


GRAVAR = """
insert into radar_comercial.ifood_merchant
       (merchant_id, nome, categoria, slug, nota, bairro, estado_detalhe, bruto,
        visto_em)
values %s
-- POR EMPRESA, e nao so pelo id da fonte (migracao 0058).
-- O mesmo estabelecimento existe uma vez em CADA empresa que o extraiu ou
-- reaproveitou; um indice global impediria isso. `id_empresa` nao aparece na
-- lista de colunas do insert porque o gatilho `preencher_empresa` a carimba
-- antes — e o gatilho BEFORE INSERT roda antes da checagem de conflito, entao
-- a inferencia pelo indice funciona.
on conflict (id_empresa, merchant_id) do update set
  nome      = coalesce(excluded.nome,      ifood_merchant.nome),
  categoria = coalesce(excluded.categoria, ifood_merchant.categoria),
  slug      = coalesce(excluded.slug,      ifood_merchant.slug),
  nota      = coalesce(excluded.nota,      ifood_merchant.nota),
  bairro    = coalesce(ifood_merchant.bairro, excluded.bairro),
  bruto     = excluded.bruto,
  visto_em  = now()
"""


def subir_prints(lojas: list[dict]) -> dict:
    """Sobe para o Storage os prints da lista e devolve {arquivo: caminho_no_storage}.

    O PRINT É A PROVA DE QUE A LOJA ESTAVA NA LISTA DAQUELA PRAÇA (dono do produto, 17/09/2026). Vinte lojas por
    print; cada loja guarda em qual print ela aparece. Só o caminho vai para o banco — os bytes ficam no Storage,
    como as fotos."""
    import os
    import imagens

    arquivos = sorted({lj.get("print_lista") for lj in lojas if lj.get("print_lista")})
    mapa = {}
    for arq in arquivos:
        caminho = os.path.join(PRINTS or "", arq)
        try:
            dados = open(caminho, "rb").read()
        except OSError:
            continue
        destino = "ifood_lista/%s" % arq
        if imagens.enviar(destino, dados, "image/jpeg"):
            mapa[arq] = destino
    if arquivos:
        print("   prints da lista: %d de %d no Storage" % (len(mapa), len(arquivos)), flush=True)
    return mapa


def gravar(con, lojas: list[dict]) -> int:
    """Grava a descoberta. NUNCA rebaixa o estado de quem já tem detalhe.

    Uma loja que já teve CNPJ colhido não pode voltar a PENDENTE só porque
    reapareceu na listagem — a listagem não sabe nada sobre o detalhe.
    """
    prints = subir_prints(lojas)
    linhas = [(
        lj["merchant_id"], lj["nome"], lj["categoria"], lj["slug"], lj["nota"],
        lj.get("bairro_busca"),
        "PENDENTE",
        json.dumps({"distancia_km": lj.get("distancia_km"),
                    "disponivel": lj.get("disponivel"),
                    "origem": "home:fallback",
                    "imagem": lj.get("imagem"),
                    # o print em que esta loja aparece na lista, e onde ele está guardado
                    "print_lista": ({"arquivo": lj["print_lista"], "storage_path": prints.get(lj["print_lista"])}
                                    if lj.get("print_lista") else None)}, ensure_ascii=False),
    ) for lj in lojas]
    with con.cursor() as k:
        execute_values(k, GRAVAR, linhas, page_size=500,
                       template="(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,now())")
        # o estado só sobe, nunca desce
        k.execute("""update radar_comercial.ifood_merchant
                        set estado_detalhe = 'OK'
                      where cnpj is not null or rua is not null""")
    con.commit()
    return len(linhas)


def um_ponto(nome: str, lat: float, lon: float, args, proxy: dict = None) -> tuple:
    """Abre uma sessão só para este ponto, lê o feed e fecha.

    Devolve (lojas, motivo). `motivo` é None quando deu certo — e quando não
    deu, diz o que houve, para a rodada seguinte não ser às cegas.

    O QUE MUDOU EM 02/09/2026, E POR QUE

    Esta função abria o Chromium e DIGITAVA um endereço. Morreu em 26/08 e o
    diagnóstico de então — "Turnstile interativo, navegador automatizado não
    clica" — estava certo sobre o Chromium e errado sobre o problema: o
    Camoufox resolve o Turnstile, e resolve em 4 segundos.

    E o endereço digitado saiu junto, porque não era preciso. O modal "Onde você
    quer receber seu pedido?" tem um "Usar minha localização" que ninguém tinha
    achado — não está na landing, está no modal, e abre sozinho em /inicio. Com
    ele, a praça muda por COORDENADA: nada de autocomplete, nada de depender de
    o CNEFE ter número da casa, nada de `address_number_required`.

    O alvo do clique é uma armadilha, e custou três rodadas. O rótulo é um
    `span`; quem recebe clique é o `div.btn-address__container`, que não tem
    `role` nem `tabindex` — `get_by_role("button")` não acha, `closest('button')`
    volta vazio, e `.click()` do DOM não move nada, porque o componente escuta
    evento de ponteiro. O que funciona é o clique de MOUSE por coordenada.

    Medido em 02/09/2026, oito pontos de Canoas em paralelo, um IP cada:
    8 de 8 posicionaram, 990 ids distintos, 30 segundos.

    É SÍNCRONA de propósito. O laço de `rodar` continua assíncrono e chama por
    `asyncio.to_thread`: o caminho síncrono é o que está medido, e a sessão do
    Scrapling não precisa do laço de eventos para nada aqui.
    """
    from scrapling.fetchers import StealthySession

    ouvinte = Escuta()
    ouvinte.bairro = nome

    url_proxy = None
    if proxy:
        # O `to_playwright` do pool devolve dict; o Scrapling quer a URL. Sem
        # usuário não se monta credencial: proxy de relay vem sem, e mandar
        # usuário vazio joga o navegador no caminho de proxy autenticado.
        servidor = str(proxy.get("server") or "").replace("http://", "")
        if proxy.get("username"):
            url_proxy = "http://%s:%s@%s" % (proxy["username"],
                                             proxy.get("password") or "", servidor)
        elif servidor:
            url_proxy = "http://%s" % servidor

    estado = {"motivo": None, "praca": None}

    def acao(page):
        page.on("response", ouvinte)

        ctx = page.context
        ctx.grant_permissions(["geolocation"], origin=BASE)
        ctx.set_geolocation({"latitude": float(lat), "longitude": float(lon),
                             "accuracy": 20})

        try:
            alvo = page.get_by_text(ROTULO_LOCALIZACAO, exact=False).first
            alvo.wait_for(state="visible", timeout=60000)
            alvo.scroll_into_view_if_needed(timeout=10000)
            caixa = alvo.bounding_box()
        except Exception:
            caixa = None
        if not caixa:
            estado["motivo"] = "o modal de endereço não abriu"
            return
        page.mouse.click(caixa["x"] + caixa["width"] / 2,
                         caixa["y"] + caixa["height"] / 2)
        page.wait_for_timeout(8000)

        # CONFERIR QUE PEGOU, e não supor. Sem endereço aplicado o site serve o
        # feed de outra praça, e a lista errada não se distingue da certa —
        # seria o mesmo vazio que parece dado do cookie do Maps.
        estado["praca"] = page.evaluate(EXIBIDO)
        if not estado["praca"] or "Escolha" in estado["praca"]:
            estado["motivo"] = "a coordenada não aplicou (%r)" % estado["praca"]
            return

        # O feed vem ao recarregar já posicionado. `networkidle` NÃO serve: a
        # página mantém tráfego de fundo e nunca fica ociosa — medido, sete de
        # oito sessões estouravam 120 s DEPOIS de posicionar certo.
        page.reload(wait_until="domcontentloaded", timeout=120000)

        # ESPERAR O CARTÃO, e não só o relógio. Em 02/09 um ponto de três
        # posicionou certo ("Próximo de Rio Branco") e foi dado como falho
        # porque o feed levou mais que os 24 s do temporizador. O primeiro
        # cartão no DOM é o sinal de que a lista renderizou — e é o mesmo
        # seletor que a colheita por coordenada usa, com o mesmo prazo medido.
        try:
            page.wait_for_selector("a.merchant-v2__link", timeout=150000)
        except Exception:
            pass                     # sem cartão, o laço abaixo decide

        limite = time.time() + ESPERA_FEED + 12.0
        while time.time() < limite and not ouvinte.lojas:
            page.wait_for_timeout(500)
        page.wait_for_timeout(2500)          # deixa chegar o resto das seções

    try:
        with StealthySession(headless=not args.visivel, solve_cloudflare=True,
                             network_idle=True, proxy=url_proxy, locale="pt-BR",
                             timezone_id="America/Sao_Paulo") as s:
            s.fetch(BASE + "/inicio", page_action=acao, timeout=420000)
    except Exception as e:                                     # noqa: BLE001
        return [], "%s" % type(e).__name__

    # O Scrapling NÃO propaga exceção de `page_action`: ele registra e devolve a
    # página assim mesmo. Sem esta conferência, ponto que não posicionou volta
    # como sucesso de zero loja.
    if estado["motivo"]:
        return [], estado["motivo"]
    if not ouvinte.lojas:
        return [], "posicionou em %r mas o feed não chegou" % estado["praca"]
    return list(ouvinte.lojas.values()), None


# ── o caminho da frota ─────────────────────────────────────────────────────────────────────────────────────────
#
# A FROTA ÚNICA DE NAVEGAÇÃO (dono do produto, 17/09/2026, regras 1 e 2): Camoufox VIVO por proxy brasileiro, que
# muda de praça a cada ponto em vez de abrir uma sessão nova por ponto. Medido em 17/09 em Santa Maria:
#
#     a verificação do Cloudflare     paga UMA vez por navegador (11 a 76 s); as páginas seguintes, 1,9 s
#     troca de praça no mesmo navegador   pelo botão do cabeçalho → "Usar minha localização" (3 de 3 pontos)
#     a lista inteira                  495 lojas num ponto do centro, 27 cliques em "Ver mais", 75 s
#
# O FEED MUDOU DE PORTA, E O MOTIVO NÃO É O iFOOD. O caminho antigo (Scrapling, hoje Chromium) toma 403 no feed
# principal (`bm/home`) e a aplicação cai no `home:fallback`, que traz a lista inteira de uma vez. No Camoufox o
# principal responde 200 com 20 lojas e um cartão `NEXT_CONTENT` com o cursor da página seguinte — que a página só
# pede quando a pessoa clica em "Ver mais". Então a frota clica, como a pessoa, e escuta as três portas.
# O BOTÃO CERTO DA LISTA, e não "o último Ver mais" (código da página, 17/09/2026): o outro "Ver mais" é o link do
# carrossel "Famosos no iFood", que NAVEGA para outra página.
BOTAO_LISTA = "section[data-card-name=NEXT_CONTENT] button.cardstack-nextcontent__button"
CARTAO = "a.merchant-v2__link"
# O ENDEREÇO DA PRAÇA MORA NESTES DOIS COOKIES: eles ficam para trás quando o ponto seguinte abre a aba limpa
COOKIES_DE_ENDERECO = ("address-latitude", "address-longitude")
# O COOKIE DO ANTI-ROBÔ NÃO VAI PARA A ABA NOVA (medido em 17/09/2026): com ele, o site recusa a chamada que traduz a
# coordenada em endereço e a praça não aplica — os três pontos da segunda leva morreram assim. O do Cloudflare
# (`cf_clearance`, `__cf_bm`) VAI: é o caro de refazer, e é ele que mantém o navegador quente.
COOKIES_DO_ANTIROBO = ("_px", "pxcts", "_pxhd", "_pxvid", "_px3")
# RECARREGAR ATÉ CAIR NO FEED DE RESERVA (dono do produto, 17/09/2026). A sessão que o site ACEITA recebe 20 lojas e
# cobra verificação para ver o resto; a RECUSADA cai no `home:fallback`, que traz a lista inteira. Por isso o passo
# prefere IP castigado e distante, e insiste algumas vezes antes de se contentar com as 20.
TENTATIVAS_RESERVA = 3
# Onde os IPs do plano ficam, para escolher o mais distante do ponto (o site desconfia de quem está longe — e é isso
# que se quer aqui). Cidade desconhecida conta como distante.
CIDADES_DO_PLANO = {"BR": (-23.5505, -46.6333), "CO": (4.7110, -74.0721)}
VER_MAIS = "Ver mais"
PAGINAS_DE_LOJAS = 80             # 80 × 20 = 1.600 lojas; um ponto de Santa Maria deu 495 em 27 cliques
LOJAS_POR_PRINT = 20              # um print por página da lista, com as lojas daquela página
FEEDS = ("bm/home", "card-content", ALVO_FEED)
# OS PRINTS DA LISTA (dono do produto, 17/09/2026): um print por página, e cada loja guarda em qual print aparece.
# NO SERVIÇO DA FROTA este módulo é IMPORTADO, e não roda como comando: por isso a pasta sai do ambiente já aqui, e
# não só no `main` — foi assim que a primeira rodada pela fila terminou com zero print.
# O PRINT DA LISTA SAIU DO PADRÃO (dono do produto, 17/09/2026): "não preciso mais de prints do iFood, eles inclusive
# atrapalham, até porque não vai pegar os 400 itens no print". O que vale é a imagem que a própria lista traz de cada
# loja — ver `lojas_do_feed`. O print continua existindo para depuração, só com `--prints PASTA`.
PRINTS = None


def _print(page, nome, fase):
    if not PRINTS:
        return
    import os
    import unicodedata
    base = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:40] or "ponto"
    try:
        page.screenshot(path=os.path.join(PRINTS, "%s_%s.jpg" % (base, fase)), type="jpeg", quality=70)
    except Exception:                                # noqa: BLE001
        pass


def _cartoes_de_lojas(o, saida):
    """Todo cartão de lojas em qualquer profundidade: a home e a página seguinte não aninham igual."""
    if isinstance(o, dict):
        if o.get("cardType") == CARD_LOJAS:
            saida.append(o)
        for v in o.values():
            _cartoes_de_lojas(v, saida)
    elif isinstance(o, list):
        for v in o:
            _cartoes_de_lojas(v, saida)
    return saida


def _latitude_da_url(u):
    try:
        return float(urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["latitude"][0])
    except (KeyError, ValueError, IndexError):
        return None


def paises_por_distancia(lat, lon):
    """Os países do plano, do mais distante do ponto para o mais perto. IP longe é o que faz o site servir a lista
    inteira pelo feed de reserva — ver `TENTATIVAS_RESERVA`."""
    import math

    def km(a, b):
        (la1, lo1), (la2, lo2) = a, b
        p = math.pi / 180
        h = (0.5 - math.cos((la2 - la1) * p) / 2
             + math.cos(la1 * p) * math.cos(la2 * p) * (1 - math.cos((lo2 - lo1) * p)) / 2)
        return 12742 * math.asin(math.sqrt(h))

    return tuple(sorted(CIDADES_DO_PLANO, key=lambda k: -km((lat, lon), CIDADES_DO_PLANO[k])))


def _desafio(page):
    """A verificação "Pressione e segure" mora num QUADRO à parte: o texto da página principal não a mostra."""
    for fr in page.frames:
        try:
            t = (fr.evaluate("() => document.body ? document.body.innerText : ''") or "").lower()
        except Exception:                                      # noqa: BLE001
            continue
        if "pressione e segure" in t or "antes de continuarmos" in t:
            return True
    return False


_RETANGULO = """(ids) => { let t=1e9,b=-1,l=1e9,r=-1,n=0;
  for (const id of ids) { const a=document.querySelector("a.merchant-v2__link[href$='" + id + "']"); if (!a) continue;
    const q=a.getBoundingClientRect(); n++; t=Math.min(t,q.top+scrollY); b=Math.max(b,q.bottom+scrollY);
    l=Math.min(l,q.left+scrollX); r=Math.max(r,q.right+scrollX); }
  return n ? {x:Math.max(0,l-8), y:Math.max(0,t-8), w:(r-l)+16, h:(b-t)+16, n:n} : null }"""


def _print_do_grupo(page, ids, caminho):
    """O print das lojas deste grupo. ROLA ATÉ ELAS ANTES, porque o navegador só desenha o que está perto da tela."""
    if not PRINTS or not ids:
        return None, 0
    import os
    try:
        os.makedirs(PRINTS, exist_ok=True)
    except OSError:
        return None, 0
    try:
        page.locator('%s[href$="%s"]' % (CARTAO, ids[0])).first.scroll_into_view_if_needed(timeout=8000)
        page.wait_for_timeout(1200)
        page.locator('%s[href$="%s"]' % (CARTAO, ids[-1])).first.scroll_into_view_if_needed(timeout=8000)
        page.wait_for_timeout(1500)
    except Exception:                                          # noqa: BLE001
        pass
    ret = page.evaluate(_RETANGULO, ids)
    if not ret:
        return None, 0
    try:
        page.screenshot(path=os.path.join(PRINTS, caminho), type="jpeg", quality=70, full_page=True,
                        clip={"x": ret["x"], "y": ret["y"], "width": ret["w"], "height": min(ret["h"], 4000)})
    except Exception:                                          # noqa: BLE001
        return None, 0
    return caminho, ret["n"]


def um_ponto_frota(p, nome: str, lat: float, lon: float, sessao: str = "painel"):
    """(lojas, meta) de UM ponto — em ate DUAS passadas no mesmo IP.

    A SEGUNDA PASSADA E A QUE TRAZ A LISTA INTEIRA (medido em 17/09/2026, noite). Em producao, com IPs que o iFood
    ainda nao conhecia, os 43 pontos de Santa Maria deram o feed principal: 200 com 20 lojas, quatro vezes (a carga e
    as tres recargas), e o clique em "Ver mais" chamou a verificacao — dali em diante o mesmo IP ja recebia 403. O
    ponto parava ali com ~20 lojas, e a cidade fechou com 218.

    E o 403 no principal e justamente a porta da lista inteira: o site recusa a sessao e cai no feed de reserva. No
    teste, mesmo ponto (Centro) e mesmo IP:

        1a passada   principal 200 x4 -> clique -> 403            37 lojas
        2a passada   principal 403, 403 -> reserva 200           487 lojas

    Entao a verificacao deixa de ser o fim do ponto e vira o comeco da segunda passada — sem passar por ela, so
    recarregando num contexto limpo. O IP nao e castigado: e exatamente por estar marcado que ele rende. Falha na
    segunda passada devolve o que a primeira trouxe, sem perder nada."""
    #
    # ATE TRES PASSADAS, e nao duas. Com a correcao no ar, 3 pontos de Santa Maria: Camobi 20 -> 433 e Nossa Senhora
    # de Lourdes 20 -> 490 na segunda passada; no Centro a segunda ja abriu com 403 (IP marcado) e mesmo assim o site
    # serviu o principal. Cada passada custa ~1 min e so acontece quando a anterior parou na verificacao.
    lojas, meta = _uma_passada_frota(p, nome, lat, lon, sessao)
    por_id = {lj["merchant_id"]: lj for lj in lojas}
    passadas = [len(lojas)]
    while (meta["via"] != "reserva" and str(meta.get("parou") or "").startswith("verificação")
           and len(passadas) < PASSADAS_POR_PONTO):
        try:
            lojas_n, meta_n = _uma_passada_frota(p, nome, lat, lon, sessao)
        except Exception as e:                                 # noqa: BLE001
            meta["passada_%d" % (len(passadas) + 1)] = "falhou (%s)" % type(e).__name__
            break
        por_id.update({lj["merchant_id"]: lj for lj in lojas_n})
        passadas.append(len(lojas_n))
        meta = meta_n
    meta["lojas_por_passada"] = passadas
    return list(por_id.values()), meta


#: Quantas passadas um ponto pode fazer no mesmo IP atras da lista inteira (ver `um_ponto_frota`).
PASSADAS_POR_PONTO = 3


def _uma_passada_frota(p, nome: str, lat: float, lon: float, sessao: str = "painel"):
    """(lojas, meta) de UMA passada pelo ponto. Falha levanta — a frota repete em outro navegador.

    CADA PONTO NUMA ABA DE CONTEXTO LIMPO, dentro do MESMO navegador quente: leva os cookies da verificação do
    Cloudflare e do anti-robô, e deixa para trás os dois cookies do endereço. Foi o que funcionou: com a praça salva,
    o modal não abre sozinho e o botão do cabeçalho fica coberto pela verificação.

    DUAS PORTAS PARA A MESMA LISTA (medido em Santa Maria em 17/09/2026):
        o site ACEITA a sessão   →  feed principal: 20 lojas, e o resto só clicando "Ver mais" (chama a verificação)
        o site RECUSA a sessão   →  feed de reserva: a lista inteira de uma vez (489 lojas num ponto do centro)
    Por isso o ponto recarrega até `TENTATIVAS_RESERVA` vezes procurando a porta de reserva, e só então se contenta
    com as 20 e tenta a paginação. Nada aqui tenta passar pela verificação: quando ela aparece e não sai, o navegador
    é fechado e o IP castigado NESTE site."""
    import os
    import time as _t

    estado = p.contexto.storage_state()
    estado["cookies"] = [c for c in estado["cookies"] if c["name"] not in COOKIES_DE_ENDERECO
                         and not c["name"].startswith(COOKIES_DO_ANTIROBO)]
    estado["origins"] = []
    ctx = p.contexto.browser.new_context(storage_state=estado)
    ordem, lojas, feeds = [], {}, []
    import unicodedata
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", nome or "") if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")[:36] or "ponto"

    def ouvir(resp):
        u = resp.url
        if "site-api" not in u or not any(f in u for f in FEEDS):
            return
        la = _latitude_da_url(u)
        try:
            corpo = resp.json()
            ls = lojas_do_feed({"sections": [{"cards": _cartoes_de_lojas(corpo, [])}]})
            imagem_cheia(corpo, ls)
        except Exception:                                      # noqa: BLE001
            ls = []
        feeds.append(("reserva" if ALVO_FEED in u else "principal" if "bm/home" in u else "pagina",
                      resp.status, len(ls)))
        if la is not None and abs(la - float(lat)) > 0.02:
            return                                             # resposta de outra praça
        for lj in ls:
            if lj["merchant_id"] not in lojas:
                ordem.append(lj["merchant_id"])
            lj["bairro_busca"] = nome
            lojas[lj["merchant_id"]] = lj

    try:
        ctx.grant_permissions(["geolocation"], origin=BASE)
        ctx.set_geolocation({"latitude": float(lat), "longitude": float(lon), "accuracy": 20})
        page = ctx.new_page()
        page.on("response", ouvir)
        page.goto(BASE + "/inicio", wait_until="domcontentloaded", timeout=120000)
        alvo = page.get_by_text(ROTULO_LOCALIZACAO, exact=False).first
        alvo.wait_for(state="visible", timeout=120000)
        caixa = alvo.bounding_box()
        if not caixa:
            raise ErroDaPagina("'Usar minha localização' sem posição na tela")
        page.mouse.click(caixa["x"] + caixa["width"] / 2, caixa["y"] + caixa["height"] / 2)
        fim = _t.time() + 30
        while _t.time() < fim and not lojas:
            page.wait_for_timeout(500)
        page.wait_for_timeout(2500)
        praca = page.evaluate(EXIBIDO)
        if not praca or "Escolha" in praca:
            raise ErroDaPagina("a coordenada não aplicou (%r)" % praca)

        def pela_reserva():
            return any(f[0] == "reserva" and f[2] for f in feeds)

        recargas = 0
        while not pela_reserva() and recargas < TENTATIVAS_RESERVA and not _desafio(page):
            recargas += 1
            page.wait_for_timeout(1500)
            page.reload(wait_until="domcontentloaded", timeout=120000)
            fim = _t.time() + 25
            while _t.time() < fim and not pela_reserva():
                page.wait_for_timeout(500)
        via = "reserva" if pela_reserva() else "principal"

        prints, visto, pagina, cliques = [], 0, 0, 0
        parou = "lista inteira pelo feed de reserva" if via == "reserva" else ""
        while True:
            pagina += 1
            novas = ordem[visto:]
            visto = len(ordem)
            for i in range(0, len(novas), LOJAS_POR_PRINT):
                grupo = novas[i:i + LOJAS_POR_PRINT]
                arq, n_cart = _print_do_grupo(page, grupo, "%s_%s_%02d.jpg" % (sessao, base, len(prints) + 1))
                prints.append({"arquivo": arq, "lojas": len(grupo), "cartoes": n_cart})
                for mid in grupo:
                    lojas[mid]["print_lista"] = arq
            if via == "reserva":
                break
            if _desafio(page):
                parou = "verificação na página %d" % pagina
                p.degradar(parou, castigar=True)
                break
            botao = page.locator(BOTAO_LISTA)
            if not botao.count() or pagina >= PAGINAS_DE_LOJAS:
                parou = "sem 'Ver mais'" if not botao.count() else "limite de páginas"
                break
            antes = len(ordem)
            page.wait_for_timeout(2500)
            try:
                botao.first.scroll_into_view_if_needed(timeout=10000)
                botao.first.click(timeout=10000)
            except Exception as e:                             # noqa: BLE001
                parou = "clique falhou (%s)" % type(e).__name__
                break
            cliques += 1
            fim = _t.time() + 20
            while _t.time() < fim and len(ordem) == antes and not _desafio(page):
                page.wait_for_timeout(500)
            if len(ordem) == antes and not _desafio(page):
                parou = "fim da lista"
                break
        if not lojas:
            raise ErroDaPagina("posicionou em %r mas nenhum feed trouxe loja" % praca)
        # os cookies atualizados voltam para o navegador quente; os do endereço não
        p.contexto.add_cookies([c for c in ctx.cookies() if c["name"] not in COOKIES_DE_ENDERECO
                                and not c["name"].startswith(COOKIES_DO_ANTIROBO)])
        # UM PONTO POR IP (medido em 17/09/2026): o IP que já aplicou uma praça não aplica outra — o ponto seguinte
        # abre em outro IP. Não é castigo: é o limite do site, e o IP volta ao rodízio na hora.
        p.degradar("iFood: um ponto por IP", castigar=False)
        return list(lojas.values()), {"praca": praca, "via": via, "recargas": recargas, "cliques": cliques,
                                      "parou": parou, "prints": [x for x in prints if x["arquivo"]],
                                      "feeds": feeds[:8]}
    finally:
        try:
            ctx.close()
        except Exception:                                      # noqa: BLE001
            pass


def rodar_pela_fila(args, pontos, escopo) -> int:
    """Os pontos vão para a fila da frota (`navegacao.tarefa`) e os navegadores QUENTES do serviço os executam.

    POR QUE PELA FILA, E NÃO ABRINDO NAVEGADOR AQUI (regra 2): o navegador vive até degradar. Preso ao processo da
    etapa, ele morre no fim dela e a verificação do Cloudflare é paga de novo na rodada seguinte. O serviço fica no
    ar (`deploy/compose.radar-frota.yml`) e a etapa só entrega trabalho.

    A saturação continua olhando os pontos NA ORDEM: o resultado que chega fora de ordem espera a vez. Saturou, o
    que sobrou na fila é cancelado."""
    from frota_cliente import acompanhar, cancelar, enviar

    print("⟦fase⟧ ifood-descoberta", flush=True)
    print(f"{escopo} · {len(pontos)} pontos do CNEFE · passo {args.passo_km} km · FILA DA FROTA (navegadores quentes "
          f"do serviço desta máquina) · um ponto por IP · lista pelo feed de reserva quando o site recusa a sessão",
          flush=True)
    sessao = str(getattr(args, "sessao", "") or "painel")
    lote = enviar("ifood", "ifood.ponto",
                  [{"nome": pt["rotulo"], "lat": pt["lat"], "lon": pt["lon"], "sessao": sessao} for pt in pontos],
                  pedido_por="extrair_ifood")
    todas: dict[str, dict] = {}
    prontos: dict[str, tuple] = {}
    secos, vez = 0, 0
    t0 = time.time()
    for t in acompanhar(lote, log=lambda m: print("  " + m, flush=True)):
        nome = (t.get("argumentos") or {}).get("nome")
        prontos[nome] = t
        # na ordem dos pontos: o que chegou adiantado espera
        while vez < len(pontos) and pontos[vez]["rotulo"] in prontos:
            pt = pontos[vez]
            vez += 1
            r = prontos.pop(pt["rotulo"])
            if r["estado"] != "ok" or not r.get("resultado"):
                print(f"  {pt['rotulo']:<26}— {str(r.get('erro') or 'sem resultado')[:120]}", flush=True)
                continue
            lojas, meta = r["resultado"][0], r["resultado"][1]
            antes = len(todas)
            for lj in lojas:
                todas.setdefault(lj["merchant_id"], lj)
            novas = len(todas) - antes
            razao = novas / max(1, len(todas))
            print(f"  {pt['rotulo']:<26}{novas:>5} novas · {len(todas):>5} no total · inéditas {razao:.1%} "
                  f"· {len(lojas)} na lista de {meta['praca']} · feed {meta['via']} · {len(meta['prints'])} print(s)"
                  f" · {meta['parou']}", flush=True)
            secos = secos + 1 if razao < SATURADO else 0
            if secos >= 2:
                n = cancelar(lote)
                print(f"  ── saturado: dois pontos seguidos abaixo de {SATURADO:.0%}. Parando por suficiência "
                      f"({n} ponto(s) cancelado(s) na fila).", flush=True)
                return _fechar_descoberta(args, list(todas.values()), t0)
    return _fechar_descoberta(args, list(todas.values()), t0)


def rodar_frota(args, pontos, escopo) -> int:
    """Os pontos em ondas do tamanho da frota; a parada por saturação olha cada onda na ordem dos pontos."""
    from frota_navegacao import Frota

    print("⟦fase⟧ ifood-descoberta", flush=True)
    print(f"{escopo} · {len(pontos)} pontos do CNEFE · passo {args.passo_km} km · FROTA: {args.navegadores} "
          f"navegadores Camoufox vivos, proxy BR, castigo por site · praça por coordenada · lista pelo 'Ver mais'",
          flush=True)
    todas: dict[str, dict] = {}
    secos = 0
    t0 = time.time()
    paises = paises_por_distancia(pontos[0]["lat"], pontos[0]["lon"])
    print(f"  IP: {'/'.join(paises)} (o mais distante do ponto primeiro) · UM PONTO POR IP · castigo não exclui — a "
          f"sessão que o iFood recusa recebe a lista inteira pelo feed de reserva", flush=True)
    with Frota("ifood", navegadores=args.navegadores, tentativas=3, paises=paises, castigo="indiferente",
               usar_cookie=False, processo="extrair_ifood") as frota:
        for i in range(0, len(pontos), args.navegadores):
            onda = pontos[i:i + args.navegadores]
            futuros = [(pt, frota.enviar(um_ponto_frota, pt["rotulo"], pt["lat"], pt["lon"],
                                         sessao=str(getattr(args, "sessao", "") or "painel")))
                       for pt in onda]
            saturou = False
            for pt, fut in futuros:
                nome = pt["rotulo"]
                try:
                    lojas, meta = fut.result()
                except Exception as e:                           # noqa: BLE001
                    print(f"  {nome:<26}— {str(e).splitlines()[0][:120]} (3 tentativas)", flush=True)
                    continue
                antes = len(todas)
                for lj in lojas:
                    todas.setdefault(lj["merchant_id"], lj)
                novas = len(todas) - antes
                razao = novas / max(1, len(todas))
                print(f"  {nome:<26}{novas:>5} novas · {len(todas):>5} no total · inéditas {razao:.1%} "
                      f"· {len(lojas)} na lista de {meta['praca']} · feed {meta['via']}"
                      f"{' (%d recargas)' % meta['recargas'] if meta['recargas'] else ''}"
                      f"{', %d Ver mais' % meta['cliques'] if meta['cliques'] else ''}"
                      f" · {len(meta['prints'])} print(s) · {meta['parou']}", flush=True)
                secos = secos + 1 if razao < SATURADO else 0
                saturou = saturou or secos >= 2
            if saturou:
                print(f"  ── saturado: dois pontos seguidos abaixo de {SATURADO:.0%}. Parando por suficiência.",
                      flush=True)
                break
        print("frota: %s" % json.dumps(frota.resumo()), flush=True)
    return _fechar_descoberta(args, list(todas.values()), t0)


def _fechar_descoberta(args, lojas, t0) -> int:
    """O fecho do `rodar`, igual para os dois caminhos: relatório, simulação ou gravação."""
    print(f"{NL}{len(lojas)} lojas únicas · {(time.time()-t0)/60:.1f} min", flush=True)
    if not lojas:
        print("! nenhuma loja. O feed não foi lido — não é 'a cidade não tem lojas'.", flush=True)
        return 1
    if args.simular:
        for lj in lojas[:10]:
            print(f"   {str(lj['nome'])[:38]:<40}{str(lj['categoria'])[:14]:<16}"
                  f"{lj['slug'] or ''}"[:110], flush=True)
        print("(simulação — nada gravado)", flush=True)
        return 0
    con = bc.conectar()
    try:
        print(f"gravadas {gravar(con, lojas)}", flush=True)
        with con.cursor() as k:
            k.execute("""select estado_detalhe, count(*)
                           from radar_comercial.ifood_merchant group by 1""")
            print("estado:", dict(k.fetchall()), flush=True)
    finally:
        con.close()
    return 0


async def rodar(args) -> int:
    from pontos_de_busca import pontos as pontos_de_busca

    escopo = (f"área desenhada {args.area!r}" if args.area
              else f"{args.cidade}{'/' + args.uf if args.uf else ''}")
    try:
        pontos = pontos_de_busca(cidade=args.cidade, uf=args.uf, area_ref=args.area,
                                 passo_km=args.passo_km, limite=args.limite)
    except SystemExit as e:
        print(f"! {e}", flush=True)
        return 1
    if not pontos:
        print(f"! nenhum endereço do CNEFE em {escopo}.", flush=True)
        return 1

    # A FROTA É O PADRÃO desde 17/09/2026, e pela FILA do serviço (navegadores quentes). `--em-processo` abre a frota
    # aqui dentro — serve para teste e para máquina sem o serviço no ar. O caminho antigo (uma sessão Scrapling por
    # ponto) só por pedido explícito.
    if not args.caminho_antigo:
        return rodar_frota(args, pontos, escopo) if args.em_processo else rodar_pela_fila(args, pontos, escopo)

    # O POOL É OPCIONAL, E A FALTA DELE É DITA EM VOZ ALTA.
    #
    # Sem proxy a varredura ainda roda — de um IP só. Descobrir isso no meio de
    # uma cidade grande custa a cidade; descobrir aqui custa uma linha de log.
    pool = None
    if not args.sem_proxy:
        try:
            from proxy_pool import ProxyPool
            pool = ProxyPool().start()
        except Exception as e:                                 # noqa: BLE001
            print(f"  ⚠️  pool de proxy indisponível ({type(e).__name__}) — "
                  f"as sessões sairão pelo IP direto", flush=True)

    print("⟦fase⟧ ifood-descoberta", flush=True)
    print(f"{escopo} · {len(pontos)} pontos do CNEFE · passo {args.passo_km} km · "
          f"UMA SESSÃO POR PONTO · Camoufox "
          f"{'visível' if args.visivel else 'oculto'} · praça por coordenada",
          flush=True)

    todas: dict[str, dict] = {}
    secos = 0
    t0 = time.time()
    # O PONTO QUE FALHA TENTA DE NOVO, com outro IP, antes de ser dado por
    # perdido. As falhas de um ponto sao transitorias — "o feed nao chegou",
    # "a coordenada nao aplicou", modal que nao abriu — e numa area pequena
    # ha UM ponto: uma falha e a etapa inteira falhando com zero lojas. Foi a
    # rodada 24 (04/09/2026): "posicionou em Harmonia mas o feed nao chegou",
    # e a rodada 25, no mesmo centro, trouxe 698 lojas. O mesmo ponto, minutos
    # depois, teria passado.
    TENTATIVAS_POR_PONTO = 3
    for pt in pontos:
        nome = pt["rotulo"]
        antes = len(todas)
        lojas, motivo = [], None
        for tentativa in range(1, TENTATIVAS_POR_PONTO + 1):
            proxy = await pool.acquire() if pool else None
            # A sessão é síncrona (ver `um_ponto`); o laço continua assíncrono
            # por causa do pool, que é.
            lojas, motivo = await asyncio.to_thread(
                um_ponto, nome, pt["lat"], pt["lon"], args, proxy)
            if pool and proxy:
                # Proxy que tomou desafio vai para o DESCANSO, não para o fim
                # da fila. Devolvê-lo à rotação queima o IP de vez: o próximo
                # ponto o pega ainda marcado e toma o mesmo desafio.
                if motivo and ("desafio" in motivo or "não aplicou" in motivo):
                    await pool.mark_cooldown(proxy)
                else:
                    await pool.release(proxy)
            if not motivo:
                break
            if tentativa < TENTATIVAS_POR_PONTO:
                print(f"  {nome:<26}— {motivo} · tentando de novo "
                      f"({tentativa + 1}/{TENTATIVAS_POR_PONTO})", flush=True)
                await asyncio.sleep(random.uniform(6.0, 12.0))
        for lj in lojas:
            todas.setdefault(lj["merchant_id"], lj)
        novas = len(todas) - antes

        if motivo:
            print(f"  {nome:<26}— {motivo}", flush=True)
            continue
        razao = novas / max(1, len(todas))
        print(f"  {nome:<26}{novas:>5} novas · {len(todas):>5} no total "
              f"· inéditas {razao:.1%}", flush=True)

        # PARADA POR SATURAÇÃO: o feed cobre um raio grande e, passado certo
        # ponto, cada ponto devolve o que já se tem. Insistir é gastar
        # requisição no servidor deles e tempo seu. O piloto viu isso em
        # Harmonia, que rendeu 0,6%.
        secos = secos + 1 if razao < SATURADO else 0
        if secos >= 2:
            print(f"  ── saturado: dois pontos seguidos abaixo de "
                  f"{SATURADO:.0%}. Parando por suficiência.", flush=True)
            break
        await asyncio.sleep(random.uniform(4.0, 9.0))

    lojas = list(todas.values())
    print(f"{NL}{len(lojas)} lojas únicas · {(time.time()-t0)/60:.1f} min",
          flush=True)
    if not lojas:
        print("! nenhuma loja. O feed não foi lido — não é 'a cidade não tem "
              "lojas'.", flush=True)
        return 1
    if args.simular:
        for lj in lojas[:10]:
            print(f"   {str(lj['nome'])[:38]:<40}{str(lj['categoria'])[:14]:<16}"
                  f"{lj['slug'] or ''}"[:110], flush=True)
        print("(simulação — nada gravado)", flush=True)
        return 0

    con = bc.conectar()
    try:
        print(f"gravadas {gravar(con, lojas)}", flush=True)
        with con.cursor() as k:
            k.execute("""select estado_detalhe, count(*)
                           from radar_comercial.ifood_merchant group by 1""")
            print("estado:", dict(k.fetchall()), flush=True)
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", help="nome do município (ex.: Canoas)")
    p.add_argument("--uf", help="sigla da UF; desempata município homônimo")
    p.add_argument("--area", nargs="?", const=au.AREA_PADRAO, default=None,
                   metavar="NOME",
                   help="usa a área desenhada no painel em vez de um município")
    p.add_argument("--passo-km", dest="passo_km", type=float, default=2.5,
                   help="lado da célula da grade de pontos (padrão 2,5 km)")
    p.add_argument("--limite", type=int, default=0,
                   help="no máximo N pontos, os mais densos primeiro (0 = todos)")
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true",
                   help="só no --caminho-antigo: não usa o pool; as sessões saem pelo IP direto")
    p.add_argument("--navegadores", type=int, default=3,
                   help="navegadores vivos da frota (padrão 3); cada um paga a verificação uma vez")
    p.add_argument("--prints", default=None, metavar="PASTA",
                   help="depuração: guarda um print por página da lista nesta pasta (fora do padrão)")
    p.add_argument("--sem-prints", dest="sem_prints", action="store_true",
                   help="não guarda os prints da lista")
    p.add_argument("--em-processo", dest="em_processo", action="store_true",
                   help="abre a frota neste processo em vez de pedir ao serviço (teste, ou máquina sem o serviço)")
    p.add_argument("--caminho-antigo", dest="caminho_antigo", action="store_true",
                   help="o caminho de antes da frota: uma sessão Scrapling por ponto, escutando o home:fallback")
    p.add_argument("--simular", action="store_true")
    # VISÍVEL SÓ ONDE HÁ TELA. Ver o que o iFood mostrou quando algo falha é
    # ótimo no notebook e é uma armadilha no i9: lá não há `DISPLAY`, o
    # Chromium headful pendura, e a etapa morria com `TimeoutError` genérico
    # depois de 3,2 min — sem chegar a avaliar a página.
    #
    # MEDIDO em 28/08/2026, mesma área e mesmo IP:
    #     headful (padrão antigo)  3,2 min  ->  "TimeoutError"
    #     headless                 0,1 min  ->  "desafio na abertura"
    #
    # A mesma execução, com o mesmo bloqueio no fim — mas uma diz o que houve e
    # a outra gasta três minutos e um IP para não dizer nada.
    p.add_argument("--oculto", dest="visivel", action="store_false",
                   help="roda sem janela (padrão onde não há DISPLAY)")
    p.add_argument("--visivel", dest="visivel", action="store_true",
                   help="força a janela, mesmo sem DISPLAY detectado")
    # O SINAL É O TERMINAL, NÃO O `DISPLAY`. Tentei `DISPLAY` primeiro e não
    # serve: o WSLg do i9 exporta `DISPLAY=:0` e `WAYLAND_DISPLAY=wayland-0`
    # mesmo numa sessão SSH sem tela nenhuma — a heurística dizia "tem tela" e
    # o Chromium headful pendurava igual.
    #
    # `isatty` responde a pergunta certa: existe uma PESSOA num terminal do
    # outro lado? Quem roda à mão no notebook vê a janela; quem é chamado por
    # subprocess (o `minerar_tudo`) ou por `ssh ... bash -s` não vê, porque
    # ninguém está lá para olhar.
    p.set_defaults(visivel=sys.stdout.isatty())
    a = p.parse_args()
    # Escopo OBRIGATORIO e EXPLICITO. Antes o padrao era `--cidade canoas`: quem
    # esquecia o argumento varria Canoas achando que varria a propria cidade.
    if not a.cidade and not a.area:
        p.error("informe --cidade NOME (com --uf) ou --area")
    if a.cidade and a.area:
        p.error("--cidade e --area sao escopos diferentes; escolha um")
    global PRINTS
    # O PRINT DA LISTA É PADRÃO: é a prova de que a loja estava na lista daquela praça, e cada loja guarda em qual
    # print ela aparece (dono do produto, 17/09/2026).
    # quem grava print e quem nao grava: pela FILA, quem tira o print é o serviço da frota (com a pasta dele). Só
    # criamos a pasta quando a frota roda NESTE processo.
    PRINTS = None if a.sem_prints else a.prints
    if PRINTS and (a.em_processo or a.caminho_antigo):
        try:
            os.makedirs(PRINTS, exist_ok=True)
        except OSError as erro:
            print("!  sem pasta para os prints (%s): seguem desligados" % erro, flush=True)
            PRINTS = None
    return asyncio.run(rodar(a))


if __name__ == "__main__":
    sys.exit(main())
