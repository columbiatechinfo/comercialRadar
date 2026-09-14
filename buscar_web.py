# -*- coding: utf-8 -*-
"""Busca web do enriquecimento, por LIGACAO. Producao desde 11/09/2026.

DESDE 13/09/2026: DUCKDUCKGO E YAHOO, E O GOOGLE SO NA RESERVA.
Decisao do dono do produto. De 12 a 13/09 a busca pesquisou DENTRO DO GOOGLE
MAPS — erro meu, contra o desenho: o Maps devolve os lugares da regiao do mapa,
de outras ruas e numeros, e a IA aprovou a ligacao 293187 (Sao Pedro, 54) por
uma empresa do nº 64b. Medido nos mesmos 20 enderecos:

    DuckDuckGo   20 paginas · 0 bloqueios · 11 com empresa na mesma rua e numero
    Yahoo        20 paginas · 0 bloqueios · 10
    Google       12 paginas · 8 bloqueios mesmo com 3 tentativas · 11
    Bing         20 paginas vazias

- cada ligacao e buscada no DuckDuckGo E no Yahoo (uma linha por motor);
- `--reserva-google`: o Google so para a ligacao em que os dois falharam
  (bloqueio, erro ou nenhum resultado no endereco);
- SO O RESULTADO NO ENDERECO VAI PARA A IA: o que cita a rua e o numero da
  instalacao, e ainda a cidade, o bairro ou o CEP. O resto fica em
  `resultados`, para conferir, e nunca no texto do dossie (`no_endereco`).

DESDE 13/09/2026 (NOITE): CAMOUFOX COM SESSAO QUENTE, e nao o Chromium do
Scrapling. Decisao do dono do produto. O Yahoo falhava em rajadas pelo Chromium
(ERR_HTTP_RESPONSE_CODE_FAILURE, ate 98%), e a busca abria um navegador frio
por consulta. Medido nas consultas do Yahoo que tinham acabado de falhar:

    Chromium frio, uma por vez           1 de 20
    Camoufox frio, uma por vez          20 de 20
    Camoufox quente, 8 em paralelo      Yahoo 80 de 80 · DuckDuckGo 40 de 40 · 2,4 s por busca

Cada trabalhador guarda UM Camoufox com UM proxy fixo e troca os dois a cada
`BUSCAS_POR_SESSAO` buscas ou no primeiro erro (`SessaoQuente`). `--chromium`
volta ao navegador antigo. O Google da reserva continua no Chromium.

O DESENHO, do dono do produto, provado em 22 + 15 ligacoes de Canoas antes de
entrar aqui (docs/RETOMAR-11-09-2026.md):

- toda ligacao do alvo e buscada pelo ENDERECO: o logradouro NORMALIZADO (o
  nome canonico do IBGE, e nao o texto cru da Corsan), numero, bairro, cidade,
  UF e "empresa";
- a busca pelo NOME de cada POI esta DESLIGADA (`BUSCAR_NOMES`): uma busca
  por instalacao, pelo endereco (correcao do dono do produto, 11/09/2026);
- Google na frente; o Bing so quando o Google falha em tres IPs;
- navegador: a sessao furtiva do Scrapling (a do iFood), com o pool de
  proxies, um IP por sessao, HTTP/2 desligado;
- a pagina vira print e texto, e o modelo da Spark devolve TODO estabelecimento
  que ela mostra, de qualquer fonte, oficial ou nao. O de OUTRO endereco vem
  marcado e fica fora do dossie; o resto entra inteiro, sem corte.

Grava em `radar_comercial.busca_web` (migracao 0099), uma linha por consulta.
Quem le e `avaliar_ligacao.busca_web_da`.

A Spark so roda a IA: a captura, o navegador e a gravacao rodam aqui, no i9,
e transbordam para o notebook com `--fatia 1/2`.

Uso:
    python buscar_web.py --cidade Canoas --aplicar
    python buscar_web.py --cidade Canoas --fatia 0/2 --trabalhadores 6 --aplicar
    python buscar_web.py --ligacao 2051224 --aplicar
"""
import argparse
import concurrent.futures as cf
import io
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import zlib

import base_comum as bc
import busca_navegador as bn
import descrever_imagens as di
import resolver_logradouro as rl

MODELO = os.environ.get("SPARK_MODELO") or os.environ.get("MODELO_VISAO") or "ia-principal"
#: Nome que e so CNPJ + pessoa ("12.345.678 FULANO DE TAL"): o MEI. Buscar o
#: nome dele devolve o proprio cadastro; ele e achado pela busca do endereco.
MEI = re.compile(r"(^\s*\d[\d.\-/]{7,}|\d[\d.\-/]{7,}\s*$)")
MOTORES = {
    "google": "https://www.google.com/search?q=%s&hl=pt-BR&gl=br&num=10",
    "google_maps": "https://www.google.com/maps/search/%s?hl=pt-BR&gl=br",
    "bing": "https://www.bing.com/search?q=%s&setlang=pt-BR&cc=BR",
    "duckduckgo": "https://html.duckduckgo.com/html/?q=%s&kl=br-pt",
    "yahoo": "https://search.yahoo.com/search?p=%s&vl=lang_pt",
}
#: Quantas vezes a mesma consulta tenta, cada vez por um IP, antes de passar ao
#: Bing. Esperar ate 15 s em "Verificando sua solicitacao" zerou os bloqueios
#: na sonda; os tres IPs cobrem o resto.
TENTATIVAS = 3
#: O IP QUE O GOOGLE BLOQUEOU fica uma hora fora do Google. O /sorry/ ("trafego
#: incomum") dura horas por IP; insistir nele queima as tres tentativas e manda
#: a busca para o Bing, que traz menos (medido na noite de 11/09/2026).
CASTIGO_S = 3600
TENTATIVAS_GOOGLE = 1
#: A LEITURA DA PAGINA PELA IA, numa chamada separada: DESLIGADA no processo
#: enxuto (12/09/2026) — o texto vai direto para a avaliacao. `--ler-com-ia`.
LER_COM_IA = False
#: OS DOIS PRINCIPAIS desde 13/09/2026 (ver o cabecalho). O Bing continua fora:
#: pagina vazia em 20 de 20. O Maps saiu de vez: nao e busca web.
MOTORES_EM_USO = ("duckduckgo", "yahoo")
#: O GOOGLE SO QUANDO OS DOIS PRINCIPAIS FALHAM (dono do produto, 13/09/2026).
RESERVA = "google"
#: O GOOGLE DE QUE SE FALA: a pagina de busca e o Maps. Os dois tem castigo de IP.
GOOGLES = ("google", "google_maps")
#: O NAVEGADOR DO DUCKDUCKGO E DO YAHOO: "camoufox" (sessao quente, desde a
#: noite de 13/09/2026) ou "chromium" (o do Scrapling, frio). Ver o cabecalho.
NAVEGADOR_WEB = "camoufox"
#: A SESSAO QUENTE troca de navegador e de IP a cada tantas buscas. No teste
#: cada sessao fez 15 sem erro; o quanto um IP aguenta ainda nao foi medido.
BUSCAS_POR_SESSAO = 50
#: Largura da pagina que vai para o modelo: densidade normal (decisao de 11/09).
LARGURA = 1366
ALVO = ("SIM", "SIM_COM_ANALISE_HUMANA")
#: BUSCA PELO NOME DO POI: DESLIGADA. Uma busca por instalacao, pelo endereco
#: normalizado + "empresa" (dono do produto, 11/09/2026) — a busca por nome
#: triplicava o tempo e o processamento. O codigo fica para quem o religar.
BUSCAR_NOMES = False

#: A CORSAN ABREVIA O TIPO DA RUA em cinco letras ("AVENI GETULIO VARGAS").
#: Na primeira rodada da sonda o Bing leu "AVENI" como palavra. O tipo vai por
#: extenso quando o logradouro nao pareia com o IBGE.
TIPO_DA_VIA = (("AV", "Avenida"), ("TRAV", "Travessa"), ("TV", "Travessa"),
               ("EST", "Estrada"), ("AL", "Alameda"), ("ROD", "Rodovia"),
               ("PRA", "Praça"), ("PC", "Praça"), ("BEC", "Beco"),
               ("LAR", "Largo"), ("R", "Rua"))

PROMPT = """Você recebe a página de resultados de uma busca na web, como imagem e como texto, e uma
ficha do que o nosso cadastro sabe sobre um imóvel. Extraia da página TODO estabelecimento que
ela mostra para esta busca, de qualquer fonte, oficial ou não: painel do Google, site próprio, rede
social, plataforma, guia, diretório de empresas ou de CNPJ. Dado de fonte não oficial é dado obtido,
e não se descarta: quem julga se ele casa com algum registro da ficha é a etapa seguinte. Diga
também se a página confirma ou complementa algum dos registros da ficha.

REGRAS
- Não invente. Campo que a página não mostra fica null.
- Resultado de outra cidade ou de outro endereço não entra, ou entra com
  "mesmo_endereco": false.
- Painel lateral de empresa (com endereço, telefone, horário, nota) vale mais que um link solto.
- "avaliacao_mais_recente" só se a página mostrar a data ou "há X dias/meses".
- Se a página for de bloqueio (CAPTCHA, "tráfego incomum", "Verificando sua solicitação"),
  devolva "bloqueado": true e mais nada.
- "status": "fechado_permanente" SÓ quando a página disser "Fechado permanentemente",
  "Fechou definitivamente" ou equivalente. "Fechado · Abre às 09:00" é o HORÁRIO de agora:
  o negócio está ativo, e o status é "aberto".
- "tipo_da_prova": de onde vem o dado de cada estabelecimento:
  "perfil_google" (painel do Google, ficha do Maps), "site_proprio", "rede_social"
  (Instagram, Facebook), "plataforma" (iFood, Booking, Airbnb, OLX), "cadastro_cnpj"
  (sites de consulta de CNPJ: Econodata, CNPJ.biz, Casa dos Dados, Solutudo) ou "outro" (guia,
  diretório, blog, notícia).
- "dominio": o site de onde saiu o resultado, quando for resultado e não painel.
- "pois_confirmados": os números dos registros da ficha que a página comprova neste endereço.

Responda SOMENTE um JSON:
{"bloqueado": false,
 "estabelecimentos": [{"nome": "", "endereco": "", "bairro": "", "telefone": "", "site": "",
   "instagram": "", "facebook": "", "horario": "", "nota": null, "avaliacoes": null,
   "avaliacao_mais_recente": {"quando": "", "texto": ""}, "cnpj": "", "categoria": "",
   "email": "", "data_abertura": "", "descricao": "<o que a página diz do negócio, até 20 palavras>",
   "status": "aberto|fechado_permanente|desconhecido", "onde_na_pagina": "painel|resultado|mapa",
   "tipo_da_prova": "perfil_google|site_proprio|rede_social|plataforma|cadastro_cnpj|outro",
   "dominio": "", "mesmo_endereco": true}],
 "pois_confirmados": [],
 "relacao": "confirma|complementa|nada",
 "justificativa": "<até 40 palavras>"}

A FICHA E A BUSCA (o que muda a cada chamada vem aqui no fim):
"""

#: O TEXTO DA PAGINA DE RESULTADOS DO GOOGLE: a ficha do lugar (#rhs) na
#: frente, com titulo, e os resultados (#search) depois, com as quebras de linha
#: que a pagina mostra. O `JS_TEXTO` do repositorio junta tudo num bloco so, e
#: a ficha ficava perdida no meio (ligacao 2089611, 12/09/2026).
JS_TEXTO_GOOGLE = r"""() => {
  const pega = (sel) => { const e = document.querySelector(sel); return e ? (e.innerText || '').trim() : ''; };
  const painel = pega('#rhs');
  const busca = pega('#search') || pega('#rso') || pega('#center_col') || (document.body ? document.body.innerText : '');
  let s = '';
  if (painel) s += 'FICHA DO LUGAR NO PAINEL DO GOOGLE:\n' + painel + '\n\n';
  s += 'RESULTADOS DA BUSCA:\n' + busca;
  return s.replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim().slice(0, 120000);
}"""

#: O GOOGLE BLOQUEOU O POOL INTEIRO em 12/09/2026: 130 de 130 buscas no /sorry,
#: nas duas faixas de IP, ate nos nunca usados. O disjuntor para a rodada quando
#: quase tudo volta bloqueado (o resto fica na fila), o ritmo se ajusta ao que o
#: Google responde, e a `--sonda` testa uma busca so antes de abrir os navegadores.
DISJUNTOR_JANELA = 30
DISJUNTOR_BLOQUEADAS = 27
RITMO_INICIAL = 40.0
RITMO_MAX = 90.0
RITMO_MIN = 4.0
CONSULTA_SONDA = "Rua Albani 114 Canoas RS empresa"

#: O TEXTO DO PAINEL DO GOOGLE MAPS: a lista de lugares (`feed`) quando a busca
#: acha varios, a ficha (`main`) quando acha um so. O resto da pagina e mapa e
#: menu. Desde 12/09/2026 a busca e pelo Maps: a pagina de busca bloqueia.
JS_TEXTO_MAPS = r"""() => {
  const t = (e) => e ? (e.innerText || '').trim() : '';
  const feed = document.querySelector('div[role="feed"]');
  const mains = Array.from(document.querySelectorAll('div[role="main"]')).map(t).filter(Boolean);
  let s;
  if (feed) s = 'LUGARES NO GOOGLE MAPS PARA ESTA BUSCA:\n' + t(feed);
  else if (mains.length) s = 'FICHA DO LUGAR NO GOOGLE MAPS:\n' + mains.join('\n\n');
  else s = 'PAGINA DO GOOGLE MAPS:\n' + t(document.body);
  return s.replace(/[ \t]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim().slice(0, 120000);
}"""
JS_POR_MOTOR = {"google": "JS_TEXTO_GOOGLE", "google_maps": "JS_TEXTO_MAPS"}


def _pagina_leve(page):
    """O Maps sem o que pesa: imagem, midia, fonte, ladrilho do mapa, Street View.
    O painel de texto, que e o que vai para a IA, vem inteiro."""
    def rota(r):
        req = r.request
        u = req.url
        if (req.resource_type in ("image", "media", "font", "imageset")
                or "/maps/vt" in u or "/kh/v=" in u or "streetviewpixels" in u
                or "googleusercontent.com" in u):
            r.abort()
        else:
            r.continue_()
    page.route("**/*", rota)


def _js_texto(motor):
    return globals().get(JS_POR_MOTOR.get(motor, ""), None) or bn.JS_TEXTO


#: OS RESULTADOS UM A UM, e nao a pagina corrida: e o que deixa filtrar pelo
#: endereco. Seletores conferidos em 13/09/2026 numa pagina real de cada motor
#: (10 resultados no DuckDuckGo, 7 no Yahoo, "Rua Lacador 162 Canoas").
JS_RESULTADOS = {
    "duckduckgo": r"""() => Array.from(document.querySelectorAll('.result')).map(r => ({
        titulo: ((r.querySelector('.result__a') || {}).innerText || '').trim(),
        url: ((r.querySelector('.result__url') || {}).innerText || '').trim(),
        trecho: ((r.querySelector('.result__snippet') || {}).innerText || '').trim()}))
        .filter(x => x.titulo || x.trecho)""",
    "yahoo": r"""() => Array.from(document.querySelectorAll('#web ol > li')).map(r => ({
        titulo: ((r.querySelector('h3') || {}).innerText || '').trim(),
        url: ((r.querySelector('a') || {}).href || ''),
        trecho: ((r.querySelector('.compText, p') || {}).innerText || '').trim()}))
        .filter(x => x.titulo || x.trecho)""",
}
#: Pagina de bloqueio dos motores sem Google (a do Google e tratada em `capturar`).
BLOQUEIO_WEB = ("captcha", "unusual traffic", "are you a robot", "verify you are human", "not a robot",
                "bots use duckduckgo", "anomaly", "too many requests", "access denied")


def _norm_end(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def no_endereco(texto, rua, nro, cidade, bairro, cep):
    """O trecho cita a MESMA RUA com o MESMO NUMERO, e ainda a cidade, o bairro
    ou o CEP da instalacao? E a regra que separa "achou" de "a busca trouxe coisa
    de outro endereco" — a falha que o Maps expos em 13/09/2026."""
    t = " %s " % _norm_end(texto)
    n = re.sub(r"\D", "", nro or "")
    palavras = [p for p in _norm_end(rua).split() if len(p) > 2 and p not in ("RUA", "AVENIDA", "TRAVESSA")]
    if not n or not palavras:
        return False
    chave = palavras[-1]
    perto = any(re.search(r"\b%s\b" % n, t[m.end():m.end() + 25])
                for m in re.finditer(r"\b%s\b" % re.escape(chave), t))
    if not perto:
        return False
    lugar = [x for x in (_norm_end(cidade), _norm_end(bairro)) if len(x) > 2]
    c = re.sub(r"\D", "", cep or "")
    return (any(" %s " % x in t for x in lugar)
            or (len(c) == 8 and (c in re.sub(r"\D", "", texto or "") or "%s %s" % (c[:5], c[5:]) in t)))


def texto_para_dossie(motor, resultados):
    """O que vai para a IA: SO os resultados no endereco, numerados."""
    bons = [r for r in resultados if r.get("no_endereco")]
    nome = {"duckduckgo": "DuckDuckGo", "yahoo": "Yahoo", "google": "Google"}.get(motor, motor)
    if not bons:
        return "%s: nenhum resultado no endereço da instalação (%d resultado(s) de outros endereços ficaram fora)." % (
            nome, len(resultados))
    linhas = ["%s: %d resultado(s) no endereço da instalação (de %d):" % (nome, len(bons), len(resultados))]
    for i, r in enumerate(bons, 1):
        linhas.append("%d. %s — %s\n   %s" % (i, r.get("titulo") or "", r.get("url") or "", r.get("trecho") or ""))
    return "\n".join(linhas)


def capturar_resultados(consulta, motor, proxy):
    """(ok, bloqueado, resultados, url_final, erro, jpeg) no DuckDuckGo ou no Yahoo."""
    from scrapling.fetchers import StealthySession
    caixa = {}

    def acao(page):
        page.wait_for_timeout(2000)
        caixa["url"] = page.url
        caixa["resultados"] = page.evaluate(JS_RESULTADOS[motor]) or []
        corpo = (page.evaluate("() => document.body ? document.body.innerText : ''") or "")
        caixa["bloqueado"] = not caixa["resultados"] and any(b in corpo.lower() for b in BLOQUEIO_WEB)
        page.set_viewport_size({"width": LARGURA, "height": 900})
        caixa["img"] = page.screenshot(full_page=True, type="jpeg", quality=80)

    try:
        with StealthySession(headless=True, proxy=proxy, locale="pt-BR", timezone_id="America/Sao_Paulo",
                             extra_flags=["--disable-http2"], block_webrtc=True) as s:
            s.fetch(MOTORES[motor] % urllib.parse.quote(consulta), page_action=acao, timeout=45000)
    except Exception as e:                                     # noqa: BLE001
        return False, None, [], "", "%s: %s" % (type(e).__name__, str(e)[:160]), None
    return (bool(caixa.get("img")), caixa.get("bloqueado"), caixa.get("resultados") or [],
            caixa.get("url", ""), "", caixa.get("img"))


class SessaoQuente:
    """Um Camoufox por trabalhador, com um proxy fixo, reaproveitado entre buscas.

    A API sincrona do Playwright nao atravessa threads: cada thread do
    `ThreadPoolExecutor` guarda o SEU navegador (`threading.local`) e so ela o
    usa e o fecha. Troca navegador e IP a cada `BUSCAS_POR_SESSAO` buscas, no
    erro e no bloqueio. `capturar` devolve o mesmo que `capturar_resultados`.

    O GOOGLE TAMBEM, desde a noite de 13/09/2026 (a reserva). Medido na fila da
    reserva, 8 navegadores quentes: 65 de 68 buscas com resultado, 35 com empresa
    no endereco, e os 3 bloqueios na PRIMEIRA busca de IPs ja marcados — contra
    metade bloqueada e 3 buscas/min pelo Chromium frio. Para o Google a sessao
    abre a pagina inicial antes (cookies), e o IP bloqueado vai para
    `ao_bloquear` (o castigo de uma hora)."""

    def __init__(self, proximo_ip, ao_bloquear=None):
        self.proximo_ip = proximo_ip
        self.ao_bloquear = ao_bloquear
        self.local = threading.local()

    def _abrir(self, motor):
        from camoufox.sync_api import Camoufox
        px = self.proximo_ip()
        cm = Camoufox(headless=True, proxy=bn_proxy_dict(px), geoip=True, locale="pt-BR")
        self.local.s = {"cm": cm, "buscas": 0, "px": px}
        nav = cm.__enter__()
        page = nav.new_page(viewport={"width": LARGURA, "height": 900})
        self.local.s["page"] = page
        if motor in GOOGLES:
            try:
                page.goto("https://www.google.com.br/?hl=pt-BR", timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            except Exception:                                  # noqa: BLE001
                pass

    def fechar(self):
        s = getattr(self.local, "s", None)
        self.local.s = None
        if s:
            try:
                s["cm"].__exit__(None, None, None)
            except Exception:                                  # noqa: BLE001
                pass

    def capturar(self, consulta, motor):
        s = getattr(self.local, "s", None)
        if s is None or s["buscas"] >= BUSCAS_POR_SESSAO:
            self.fechar()
            try:
                self._abrir(motor)
            except Exception as e:                             # noqa: BLE001
                self.fechar()
                return False, None, [], "", "abrir o Camoufox: %s: %s" % (type(e).__name__, str(e)[:140]), None
            s = self.local.s
        s["buscas"] += 1
        page = s["page"]
        corpo_js = "() => document.body ? document.body.innerText : ''"
        try:
            page.goto(MOTORES[motor] % urllib.parse.quote(consulta), timeout=45000, wait_until="domcontentloaded")
            if motor in GOOGLES:
                page.wait_for_timeout(2000)
                corpo = page.evaluate(corpo_js) or ""
                # "Verificando sua solicitacao" libera sozinho: ate 6 s a mais
                for _ in range(3):
                    if "verificando" not in corpo.lower():
                        break
                    page.wait_for_timeout(2000)
                    corpo = page.evaluate(corpo_js) or ""
                baixo = corpo.lower()
                bloq = ("/sorry/" in page.url or "unusual traffic" in baixo or "tráfego incomum" in baixo
                        or any(b in baixo for b in BLOQUEIO_WEB))
                resultados = [] if bloq else resultados_do_google(corpo)
            else:
                page.wait_for_timeout(1500)
                resultados = page.evaluate(JS_RESULTADOS[motor]) or []
                corpo = (page.evaluate(corpo_js) or "")
                bloq = not resultados and any(b in corpo.lower() for b in BLOQUEIO_WEB)
            img = page.screenshot(full_page=True, type="jpeg", quality=80)
            url = page.url
        except Exception as e:                                 # noqa: BLE001
            self.fechar()
            return False, None, [], "", "%s: %s" % (type(e).__name__, str(e)[:160]), None
        if bloq:
            if self.ao_bloquear and s.get("px"):
                self.ao_bloquear(s["px"])
            self.fechar()
        return bool(img), bloq, resultados, url, "", img


def resultados_do_google(texto):
    """A pagina do Google em blocos (ficha e resultados), para o mesmo filtro."""
    blocos = [b.strip() for b in re.split(r"\n\s*\n", texto or "") if b.strip()]
    return [{"titulo": b.split("\n", 1)[0][:200], "url": "", "trecho": b[:1500]} for b in blocos]


class Ritmo:
    """Buscas por minuto nesta maquina, somando os navegadores, e o disjuntor."""

    def __init__(self, por_min=RITMO_INICIAL):
        import collections
        self.por_min = por_min
        self.vez = time.time()
        self.janela = collections.deque(maxlen=DISJUNTOR_JANELA)
        self.lote = []
        self.aberto = False
        self.trava = threading.Lock()

    def esperar(self):
        with self.trava:
            agora = time.time()
            minha = max(agora, self.vez)
            self.vez = minha + 60.0 / self.por_min
        time.sleep(max(0.0, minha - agora))

    def resultado(self, bloqueado):
        with self.trava:
            self.janela.append(bool(bloqueado))
            self.lote.append(bool(bloqueado))
            if getattr(self, "fixo", False):
                self.lote = []
            elif len(self.lote) >= 10:
                b = sum(self.lote)
                antes = self.por_min
                if b >= 3:
                    self.por_min = max(RITMO_MIN, self.por_min / 2)
                elif b <= 1 and self.por_min <= RITMO_MAX:
                    # acima do teto e o ritmo ABERTO dos motores sem Google: nao
                    # e para puxar para baixo quando nada bloqueia
                    self.por_min = min(RITMO_MAX, self.por_min * 1.2)
                self.lote = []
                if self.por_min != antes:
                    _log("   ritmo %.0f -> %.0f buscas/min (%d de 10 bloqueadas)" % (antes, self.por_min, b))
            if (not self.aberto and len(self.janela) == DISJUNTOR_JANELA
                    and sum(self.janela) >= DISJUNTOR_BLOQUEADAS):
                self.aberto = True
                _log("   ⛔ disjuntor: %d das últimas %d bloqueadas — a rodada para e o resto fica na fila"
                     % (sum(self.janela), DISJUNTOR_JANELA))


def sonda():
    """Uma busca so, por um IP do rodizio: 0 se o motor respondeu, 3 se bloqueou."""
    px = bn.rodizio(quantos=500, pais="", embaralhar=True)()
    if MOTORES_EM_USO[0] in JS_RESULTADOS and NAVEGADOR_WEB == "camoufox":
        s = SessaoQuente(lambda: px)
        ok, bloq, _res, url, erro, jpeg = s.capturar(CONSULTA_SONDA, MOTORES_EM_USO[0])
        s.fechar()
    elif MOTORES_EM_USO[0] in JS_RESULTADOS:
        ok, bloq, _res, url, erro, jpeg = capturar_resultados(CONSULTA_SONDA, MOTORES_EM_USO[0], px)
    else:
        ok, bloq, texto, url, erro, jpeg = capturar(CONSULTA_SONDA, MOTORES_EM_USO[0], px)
    _log("   sonda: %s · %s" % ("BLOQUEADA" if bloq else ("ok" if ok and jpeg else "falhou"),
                                (erro or url or "")[:90]))
    return 0 if ok and jpeg and not bloq else 3


_trava_log = threading.Lock()


def _log(m):
    with _trava_log:
        print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def tipo_por_extenso(end_ligacao):
    t = (end_ligacao.split(" ", 1)[0] or "").upper()
    for pref, nome in TIPO_DA_VIA:
        if t.startswith(pref):
            return nome
    return ""


def ler_fatia(texto):
    """"0,1/3" -> ({0, 1}, 3). Vazio -> None."""
    if not texto:
        return None
    ks, n = texto.split("/")
    return {int(k) for k in ks.split(",") if k.strip() != ""}, int(n)


def _na_fatia(ligacao, fatia):
    """`fatia` = (ks, n): a ligacao e deste processo quando crc32 % n esta em ks.

    O MESMO CORTE NAS DUAS MAQUINAS sem tabela de fila. VARIOS PEDACOS POR
    PROCESSO porque as maquinas nao sao iguais: 20 navegadores no i9 e 10 no
    notebook (dono do produto, 11/09/2026) — o i9 roda "0,1/3" e o notebook
    "2/3", e os dois terminam juntos.
    """
    if not fatia:
        return True
    ks, n = fatia
    return zlib.crc32(str(ligacao).encode()) % n in ks


#: A ORDEM DAS FONTES, do dono do produto em 11/09/2026: "google maps poi,
#: ifood, e as demais em seguida". Ligacao com POI do Maps primeiro.
ORDEM_DAS_FONTES = ("maps", "ifood")


def prioridade(fontes):
    for i, f in enumerate(ORDEM_DAS_FONTES):
        if f in fontes:
            return i
    return len(ORDEM_DAS_FONTES)


def ligacoes_com_imagem(cur):
    """As ligacoes com vinculo vivo cuja alguma fonte ja tem imagem.

    Foto de rua (`poi_evidencia` sv_*) ou foto publicada do Google
    (`images_urls` gps-cs-s). Dois conjuntos e um cruzamento em Python —
    nunca `exists` por linha.
    """
    cur.execute("""select distinct poi_id from radar_comercial.poi_evidencia
                    where tipo like 'sv_%%' and (bytes_tam is not null or storage_path is not null)""")
    com = {r[0] for r in cur.fetchall()}
    cur.execute("""select distinct poi_id from radar_comercial.images_urls
                    where url like '%%gps-cs-s%%' and (dados is not null or storage_path is not null)""")
    com |= {r[0] for r in cur.fetchall()}
    cur.execute("select ligacao, poi_id from radar_comercial.ligacao_poi where descartado_em is null")
    return {str(l) for l, p in cur.fetchall() if p in com}


def fila(con, cidade=None, limite=0, ligacoes=None, fatia=None, refazer_bing=False, refazer=False,
         reserva_google=False):
    """As ligacoes do alvo com consulta por fazer, e as consultas de cada uma.

    Alvo: residencial ATIVA, qualificada SIM ou SIM_COM_ANALISE_HUMANA, com
    vinculo vivo. Uma consulta esta feita, POR MOTOR, quando tem linha com texto
    ou leitura; bloqueio e erro nao contam, e voltam na proxima rodada.

    `reserva_google`: so o Google, e so para a ligacao em que os dois motores
    principais ja tentaram e nenhum trouxe resultado no endereco (dono do
    produto, 13/09/2026: "aplica a busca google apenas com os dois principais
    falharem").

    Tudo em conjuntos no Python, e nenhum `exists` por linha: a fila da
    avaliacao ja ficou minutos parada nisso.
    """
    cur = con.cursor()
    cur.execute("set statement_timeout = '300s'")
    cur.execute("""select num_ligacao::text, coalesce(end_ligacao,''), coalesce(nom_logradouro,''),
                          coalesce(nro,''), coalesce(nom_bairro,''), qualificacao, cidade, coalesce(cod_cep,'')
                     from resources_root.cadastro_corsan
                    where qualificacao in ('SIM','SIM_COM_ANALISE_HUMANA')
                      and upper(categoria)='RESIDENCIAL' and upper(sit_ligacao)='ATIVA'""")
    quero = sem_acento(cidade) if cidade else None
    so = {str(x) for x in ligacoes} if ligacoes else None
    lig = {}
    for r in cur.fetchall():
        if so is not None and r[0] not in so:
            continue
        if quero and sem_acento(r[6]) != quero:
            continue
        if not _na_fatia(r[0], fatia):
            continue
        lig[r[0]] = r[1:]
    cur.execute("select ligacao, poi_id from radar_comercial.ligacao_poi where descartado_em is null")
    por_lig = {}
    for l, p in cur.fetchall():
        l = str(l)
        if l in lig:
            por_lig.setdefault(l, []).append(p)
    ids = sorted({p for ps in por_lig.values() for p in ps})
    cur.execute("""select id, lower(coalesce(fonte,'')), coalesce(nome,''), coalesce(endereco,''),
                          coalesce(telefone,'')
                     from radar_comercial.pois where fundido_em is null and id = any(%s)""", (ids,))
    poi = {r[0]: {"id": r[0], "fonte": r[1], "nome": r[2], "endereco": r[3], "telefone": r[4]}
           for r in cur.fetchall()}
    cur.execute("""select ligacao, tipo, coalesce(poi_id, 0), motor,
                          (ia is not null or texto is not null) and not bloqueado, coalesce(no_endereco, 0)
                     from radar_comercial.busca_web""")
    por_motor = {}
    tentou, achou = {}, set()
    for a_, b_, c_, m_, feita, n_end in cur.fetchall():
        k = (str(a_), b_, c_)
        tentou.setdefault(k, set()).add(m_)
        if feita:
            por_motor.setdefault(k, set()).add(m_)
            if n_end and m_ in MOTORES_EM_USO:
                achou.add(k)
    if refazer:
        por_motor = {}
    motores = (RESERVA,) if reserva_google else MOTORES_EM_USO
    # SO QUEM JA TEM IMAGEM (dono do produto, 11/09/2026): a busca web e para
    # as ligacoes que a IA ja pode julgar com foto.
    com_imagem = ligacoes_com_imagem(cur) if not so else None
    cod = None
    municipios = {}
    saida = []

    def chave(l):
        # O SIM ANTES do SIM com analise humana (dono do produto, 12/09/2026),
        # e dentro de cada um a ordem das fontes.
        q = lig[l][4]
        return (0 if q == "SIM" else (1 if q == "SIM_COM_ANALISE_HUMANA" else 2),
                prioridade({poi[p]["fonte"] for p in por_lig[l] if p in poi}), l)

    for l in sorted(por_lig, key=chave):
        if com_imagem is not None and l not in com_imagem:
            continue
        ps = [poi[p] for p in por_lig[l] if p in poi]
        if not ps:
            continue
        end_l, logr, nro, bairro, q, cid, cep = lig[l]
        k = (l, "endereco", 0)
        tarefas = []
        for motor in motores:
            if motor in por_motor.get(k, set()):
                continue
            if reserva_google and (k in achou or not all(m in tentou.get(k, set()) for m in MOTORES_EM_USO)):
                continue
            tarefas.append(("endereco", None, motor))
        if not tarefas:
            continue
        # O LOGRADOURO NORMALIZADO, pareado com o cadastro do IBGE do municipio
        # da ligacao (correcao do dono do produto em 11/09/2026).
        cid_n = sem_acento(cid)
        if cid_n not in municipios:
            municipios[cid_n] = _cadastro_do_municipio(cur, cid)
        cad = municipios[cid_n]
        reg = cad.parear(rl.norm(logr)) if cad else None
        via = reg[0].title() if reg else " ".join(x for x in (tipo_por_extenso(end_l), logr.title()) if x)
        cidade_uf = "%s RS" % (cid or "").title()
        consulta_end = " ".join(x for x in (via, nro, bairro.title(), cidade_uf, "empresa") if x)
        saida.append({"ligacao": l, "qualificacao": q, "endereco": end_l,
                      "consulta_endereco": consulta_end, "cidade_uf": cidade_uf,
                      "rua": reg[0] if reg else logr, "nro": nro, "bairro": bairro, "cidade": cid, "cep": cep,
                      "pois": ps, "tarefas": tarefas})
        if limite and len(saida) >= limite:
            break
    return saida


_CODIGOS_RS = {}


def _cadastro_do_municipio(cur, cidade):
    """O cadastro de logradouros do IBGE do municipio, ou None.

    PELO NOME, MAS SO NO RS: a Corsan so atende o RS, e e isso que torna o nome
    seguro aqui ("Santana" existe em nove estados; ver `area_utils`). A
    comparacao sem acento e em Python, sobre as ~500 linhas do estado.
    """
    if not _CODIGOS_RS:
        cur.execute("select nome, cod_municipio from resources_root.ibge_malha where upper(uf) = 'RS'")
        for nome, cod in cur.fetchall():
            _CODIGOS_RS[sem_acento(nome)] = str(cod)
    cod = _CODIGOS_RS.get(sem_acento(cidade))
    if not cod:
        _log("   municipio sem codigo IBGE: %r — a busca usa o logradouro cru" % cidade)
        return None
    cad = rl.Cadastro(cod)
    cad.carregar(cur)
    return cad


def capturar(consulta, motor, proxy):
    """(ok, bloqueado, texto, url_final, erro, jpeg)."""
    from scrapling.fetchers import StealthySession
    caixa = {}

    def acao(page):
        # O GOOGLE SEGURA A BUSCA NUMA VERIFICACAO que se resolve sozinha:
        # "Verificando sua solicitacao". Espera ate 15 s por ela antes de
        # chamar de bloqueio (na sonda, 5 de 17 buscas eram so isso).
        corpo = ""
        for _ in range(25):
            corpo = (page.evaluate("() => document.body ? document.body.innerText : ''") or "")
            if "verificando sua solicita" not in corpo.lower() and "checking your request" not in corpo.lower():
                break
            page.wait_for_timeout(1000)
        if motor == "google_maps":
            try:
                page.wait_for_selector('div[role="feed"], div[role="main"]', timeout=15000)
            except Exception:                                  # noqa: BLE001
                pass
            page.wait_for_timeout(2500)
        page.wait_for_timeout(1500)
        corpo = (page.evaluate("() => document.body ? document.body.innerText : ''") or "")
        caixa["url"] = page.url
        baixo = corpo.lower()
        caixa["bloqueado"] = ("/sorry/" in page.url or "unusual traffic" in baixo
                              or "tráfego incomum" in baixo or "captcha" in page.url.lower()
                              or "verificando sua solicita" in baixo
                              or "checking your request" in baixo)
        page.set_viewport_size({"width": LARGURA, "height": 900})
        caixa["img"] = page.screenshot(full_page=(motor != "google_maps"), type="jpeg", quality=82)
        caixa["texto"] = page.evaluate(_js_texto(motor)) or ""

    try:
        extra = {"page_setup": _pagina_leve} if motor == "google_maps" else {}
        with StealthySession(headless=True, proxy=proxy, locale="pt-BR",
                             timezone_id="America/Sao_Paulo",
                             extra_flags=["--disable-http2"], block_webrtc=True, **extra) as s:
            s.fetch(MOTORES[motor] % urllib.parse.quote(consulta), page_action=acao,
                    timeout=45000)
    except Exception as e:                                     # noqa: BLE001
        return False, None, "", "", "%s: %s" % (type(e).__name__, str(e)[:160]), None
    return (bool(caixa.get("img")), caixa.get("bloqueado"), caixa.get("texto", ""),
            caixa.get("url", ""), "", caixa.get("img"))


def capturar_humano(consulta, proxy, motor="google"):
    """(ok, bloqueado, texto, url_final, erro, jpeg) pela sessao humanizada.

    A MESMA SESSAO QUE COLHE O GOOGLE MAPS (`human_browser.HumanSession`,
    usada por `google_enriquece`): perfil proprio, stealth, cookies de
    consentimento do Google e o proxy do pool. Pedido do dono do produto em
    12/09/2026: "tenta o Google com proxy como ja busca os itens do Google; so
    se nao conseguir vira para o navegador do repositorio".
    """
    import asyncio
    import shutil
    import tempfile
    from pathlib import Path

    async def _rodar():
        from playwright.async_api import async_playwright
        from human_browser import HumanSession
        perfil = Path(tempfile.mkdtemp(prefix="radar_busca_"))
        sess = None
        try:
            async with async_playwright() as pw:
                sess = await HumanSession.create(pw, proxy, perfil, layer="maps", headless=True)
                await sess.humanized_goto(MOTORES[motor] % urllib.parse.quote(consulta), timeout=45000)
                if motor == "google_maps":
                    try:
                        await sess.page.wait_for_selector('div[role="feed"], div[role="main"]', timeout=15000)
                    except Exception:                          # noqa: BLE001
                        pass
                    await sess.page.wait_for_timeout(2500)
                corpo = ""
                for _ in range(25):
                    corpo = (await sess.page.evaluate("() => document.body ? document.body.innerText : ''")) or ""
                    if "verificando sua solicita" not in corpo.lower() and "checking your request" not in corpo.lower():
                        break
                    await sess.page.wait_for_timeout(1000)
                await sess.page.wait_for_timeout(1500)
                baixo = ((await sess.page.evaluate("() => document.body ? document.body.innerText : ''")) or "").lower()
                bloq = (await sess.is_captcha() or "tráfego incomum" in baixo or "unusual traffic" in baixo
                        or "verificando sua solicita" in baixo or "checking your request" in baixo)
                await sess.page.set_viewport_size({"width": LARGURA, "height": 900})
                img = await sess.page.screenshot(full_page=(motor != "google_maps"), type="jpeg", quality=82)
                texto = (await sess.page.evaluate(_js_texto(motor))) or ""
                url = sess.page.url
                await sess.close()
                sess = None
                return True, bloq, texto, url, "", img
        except Exception as e:                                 # noqa: BLE001
            return False, None, "", "", "%s: %s" % (type(e).__name__, str(e)[:160]), None
        finally:
            if sess is not None:
                try:
                    await sess.close()
                except Exception:                              # noqa: BLE001
                    pass
            shutil.rmtree(perfil, ignore_errors=True)

    return asyncio.run(_rodar())


def bn_proxy_dict(url):
    """"http://user:senha@host:porta" -> o dicionario que o `ProxyPool.to_playwright` le."""
    if not url:
        return None
    u = urllib.parse.urlsplit(url)
    return {"server": "http://%s:%s" % (u.hostname, u.port), "username": u.username or "",
            "password": u.password or ""}


def _print_para_modelo(jpeg):
    """O print em densidade normal, cortado em 7.000 px: (jpeg, base64)."""
    import base64
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if im.width > LARGURA:
        im = im.resize((LARGURA, int(im.height * LARGURA / im.width)), Image.LANCZOS)
    if im.height > 7000:
        im = im.crop((0, 0, im.width, 7000))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue(), base64.b64encode(b.getvalue()).decode()


def _print_para_guardar(jpeg):
    """O print que fica no banco: WEBP q55 do JPEG que o modelo leu.

    Medido em 11/09/2026: WEBP q60 da pagina inteira dava 160 a 370 KB, e
    Canoas tem ~33 mil consultas. O corte em 7.000 px e o do modelo.
    """
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    b = io.BytesIO()
    im.save(b, "WEBP", quality=55, method=4)
    return b.getvalue()


def extrair(item, consulta, texto, b64):
    ficha = {"ligacao": item["ligacao"], "endereco_da_ligacao": item["endereco"],
             "consulta": consulta,
             "registros": [{"numero": p["id"], "fonte": p["fonte"], "nome": p["nome"],
                            "endereco": p["endereco"], "telefone": p["telefone"]} for p in item["pois"]]}
    import json
    prompt = PROMPT + json.dumps(ficha, ensure_ascii=False) + "\n\nTEXTO DA PÁGINA:\n" + texto[:9000]
    t0 = time.time()
    try:
        r = di._chat_local(MODELO, prompt, [b64] if b64 else [], max_tokens=2600,
                           timeout=int(os.environ.get("RADAR_TIMEOUT_IA") or 900))
    except Exception as e:                                     # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:200]), time.time() - t0
    if not isinstance(r, dict):
        return None, "resposta fora do formato", time.time() - t0
    return r, "", time.time() - t0


class Gravador:
    """Uma conexao, uma trava: sao ~8 linhas por minuto por processo."""

    def __init__(self, aplicar):
        self.aplicar = aplicar
        self.trava = threading.Lock()
        self.con = bc.conectar() if aplicar else None

    def linha(self, **c):
        if not self.aplicar:
            return
        import json
        sql = """insert into radar_comercial.busca_web
                    (id_empresa, ligacao, tipo, poi_id, consulta, motor, bloqueado, erro,
                     tentativas, url, dados, bytes_tam, chars_texto, ia, modelo,
                     segundos_captura, segundos_ia, texto, navegador, resultados, no_endereco)
                 values ((select core.empresa_atual()), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        args = (c["ligacao"], c["tipo"], c.get("poi_id"), c["consulta"], c["motor"],
                bool(c.get("bloqueado")), c.get("erro") or None, c.get("tentativas"),
                c.get("url") or None, c.get("dados"), len(c["dados"]) if c.get("dados") else None,
                c.get("chars_texto"), json.dumps(c["ia"], ensure_ascii=False) if c.get("ia") is not None else None,
                MODELO if c.get("ia") is not None else None,
                c.get("segundos_captura"), c.get("segundos_ia"), c.get("texto"), c.get("navegador"),
                json.dumps(c["resultados"], ensure_ascii=False) if c.get("resultados") is not None else None,
                c.get("no_endereco"))
        with self.trava:
            for tentativa in (1, 2):
                try:
                    with self.con.cursor() as cur:
                        cur.execute(sql, args)
                    self.con.commit()
                    return
                except Exception as e:                         # noqa: BLE001
                    _log("   gravar falhou (%s): %s" % (tentativa, str(e)[:120]))
                    try:
                        self.con.close()
                    except Exception:                          # noqa: BLE001
                        pass
                    self.con = bc.conectar()


def rodar(itens, trabalhadores, aplicar):
    trabalhos = []
    for it in itens:
        for tipo, p, motor in it["tarefas"]:
            q = it["consulta_endereco"] if tipo == "endereco" else "%s %s" % (p["nome"], it["cidade_uf"])
            trabalhos.append((it, tipo, p, q, motor))
    _log("▶ busca web · %d ligação(ões) · %d consulta(s) · %d trabalhadores"
         % (len(itens), len(trabalhos), trabalhadores))
    if not trabalhos:
        return {"ligacoes": 0, "consultas": 0}
    grav = Gravador(aplicar)
    # MUITOS IPS E CASTIGO PARA O BLOQUEADO. Ver o comentario de `CASTIGO_S`.
    proximo = bn.rodizio(quantos=500, pais="", embaralhar=True)
    # O RITMO SO SEGURA O GOOGLE. DuckDuckGo e Yahoo nao bloquearam nenhuma das
    # 40 buscas medidas; para eles o ritmo comeca aberto e so freia se o bloqueio
    # aparecer (3 em 10), e o disjuntor continua valendo para todos.
    so_google = all(m in GOOGLES for it in itens for _t, _p, m in it["tarefas"])
    # NO CAMOUFOX QUENTE O RITMO DO GOOGLE E FIXO (13/09/2026, noite): o bloqueio
    # vem em IP ja marcado, a sessao troca o IP e o castiga, e a busca passa na
    # tentativa seguinte (18 de 440 ficaram bloqueadas). O freio que divide por
    # dois a cada 3 tentativas bloqueadas levou a rodada de 107 a 5 buscas/min.
    # Fixo, sem freio; o disjuntor continua valendo. A 40/min os bloqueios
    # subiram de 8% a 24% em 15 min (madrugada de 14/09): 20/min por padrao,
    # `RITMO_GOOGLE_QUENTE` muda.
    if NAVEGADOR_WEB == "camoufox":
        ritmo = Ritmo(por_min=float(os.environ.get("RITMO_GOOGLE_QUENTE") or 20) if so_google else 100000.0)
        ritmo.fixo = True
    else:
        ritmo = Ritmo() if so_google else Ritmo(por_min=100000.0)
    castigo = {}
    trava_ip = threading.Lock()

    def ip_para(motor):
        """O proximo IP; para o Google, pula o que esta de castigo."""
        if motor not in GOOGLES:
            return proximo()
        agora = time.time()
        px = proximo()
        for _ in range(300):
            with trava_ip:
                livre = castigo.get(px, 0) <= agora
            if livre:
                return px
            px = proximo()
        return px

    def castigar(px):
        with trava_ip:
            castigo[px] = time.time() + CASTIGO_S
    # A sessao quente pega o IP pelo mesmo rodizio: na reserva do Google, pulando
    # o que esta de castigo, e o que bloquear vai para o castigo.
    quente = (SessaoQuente((lambda: ip_para(RESERVA)) if so_google else proximo, ao_bloquear=castigar)
              if NAVEGADOR_WEB == "camoufox" else None)
    placar = {"ok_duckduckgo": 0, "ok_yahoo": 0, "ok_google": 0, "com_resultado_no_endereco": 0,
              "bloqueado": 0, "falha": 0}
    trava = threading.Lock()
    feitos = [0]
    t_ini = time.time()

    def um(args):
        it, tipo, p, q, motor = args
        if ritmo.aberto:
            return
        t0 = time.time()
        ok = bloq = False
        texto_pagina = url = erro = ""
        jpeg = None
        resultados = []
        tentativa = 0
        # TRES TENTATIVAS, cada uma por um IP. No Google o IP bloqueado fica de
        # castigo uma hora (ver `CASTIGO_S`).
        for tentativa in range(1, TENTATIVAS + 1):
            px = ip_para(motor)
            ritmo.esperar()
            if quente and (motor in JS_RESULTADOS or motor == "google"):
                ok, bloq, resultados, url, erro, jpeg = quente.capturar(q, motor)
            elif motor in JS_RESULTADOS:
                ok, bloq, resultados, url, erro, jpeg = capturar_resultados(q, motor, px)
            else:
                ok, bloq, texto_pagina, url, erro, jpeg = capturar(q, motor, px)
                resultados = resultados_do_google(texto_pagina) if ok and not bloq else []
            ritmo.resultado(bloq)
            if motor in GOOGLES and bloq and not quente:
                castigar(px)
            if ok and not bloq and jpeg:
                break
        passou = bool(ok and not bloq and jpeg)
        for r in resultados:
            r["no_endereco"] = no_endereco("%s %s" % (r.get("titulo") or "", r.get("trecho") or ""),
                                           it.get("rua"), it.get("nro"), it.get("cidade"), it.get("bairro"),
                                           it.get("cep"))
        n_end = sum(1 for r in resultados if r.get("no_endereco"))
        reg = {"ligacao": it["ligacao"], "tipo": tipo, "poi_id": p["id"] if p else None,
               "consulta": q, "motor": motor, "bloqueado": bool(bloq), "erro": erro,
               "texto": texto_para_dossie(motor, resultados) if passou else None,
               "resultados": resultados if passou else None, "no_endereco": n_end if passou else None,
               "navegador": "camoufox" if quente and (motor in JS_RESULTADOS or motor == "google") else "repositorio",
               "tentativas": tentativa, "url": url,
               "chars_texto": len(texto_pagina) or sum(len(r.get("trecho") or "") for r in resultados),
               "segundos_captura": round(time.time() - t0, 1)}
        if passou:
            lido, _b64 = _print_para_modelo(jpeg)
            reg["dados"] = _print_para_guardar(lido)
            chave = "ok_" + motor
        elif bloq:
            chave = "bloqueado"
        else:
            chave = "falha"
        grav.linha(**reg)
        with trava:
            placar[chave] = placar.get(chave, 0) + 1
            if passou and n_end:
                placar["com_resultado_no_endereco"] += 1
            feitos[0] += 1
            n = feitos[0]
        if n % 20 == 0 or n == len(trabalhos):
            vel = n / max(1e-6, (time.time() - t_ini) / 60.0)
            _log("   [%d/%d] %.1f consultas/min · falta ~%.0f min · %s"
                 % (n, len(trabalhos), vel, (len(trabalhos) - n) / max(vel, 1e-6), placar))

    with cf.ThreadPoolExecutor(trabalhadores) as ex:
        list(ex.map(um, trabalhos))
        if quente:
            # CADA THREAD FECHA O SEU NAVEGADOR. A barreira prende uma tarefa em
            # cada thread ate todas pegarem a sua, senao uma thread livre pegaria
            # duas e o navegador de outra ficaria aberto.
            barreira = threading.Barrier(min(trabalhadores, len(trabalhos)), timeout=120)

            def fechar_meu(_):
                quente.fechar()
                try:
                    barreira.wait()
                except threading.BrokenBarrierError:
                    pass
            list(ex.map(fechar_meu, range(min(trabalhadores, len(trabalhos)))))
    _log("■ busca web pronta · %s%s" % (placar, " · DISJUNTOR ABERTO" if ritmo.aberto else ""))
    return {"ligacoes": len(itens), "consultas": len(trabalhos), **placar}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", default=None)
    p.add_argument("--ligacao", action="append")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=6)
    p.add_argument("--fatia", default="", help="k[,k2]/n: so as ligacoes com crc32 %% n num dos k")
    p.add_argument("--refazer", action="store_true",
                   help="busca de novo as ligacoes pedidas, mesmo as ja buscadas")
    p.add_argument("--ler-com-ia", dest="ler_com_ia", action="store_true",
                   help="le cada pagina com a IA numa chamada separada (o processo antigo)")
    p.add_argument("--refazer-bing", dest="refazer_bing", action="store_true",
                   help="busca de novo, no Google, as consultas que so o Bing respondeu")
    p.add_argument("--contar", action="store_true",
                   help="so conta ligacoes e consultas por fazer, sem buscar nada")
    p.add_argument("--sonda", action="store_true",
                   help="uma busca so: sai 0 se o motor principal respondeu, 3 se bloqueou")
    p.add_argument("--reserva-google", dest="reserva_google", action="store_true",
                   help="so o Google, para as ligacoes em que DuckDuckGo e Yahoo falharam")
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", default=None,
                   help="arquivo com uma ligacao por linha")
    p.add_argument("--chromium", action="store_true",
                   help="DuckDuckGo e Yahoo pelo Chromium frio do Scrapling, e nao pelo Camoufox quente")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    global NAVEGADOR_WEB
    if a.chromium:
        NAVEGADOR_WEB = "chromium"
    if a.sonda:
        sys.exit(sonda())
    fatia = ler_fatia(a.fatia)
    global LER_COM_IA
    LER_COM_IA = bool(a.ler_com_ia)
    ligs = list(a.ligacao or [])
    if a.ligacoes_arquivo:
        ligs += [x.strip() for x in open(a.ligacoes_arquivo) if x.strip()]
    if not a.cidade and not ligs:
        p.error("diga --cidade, --ligacao ou --ligacoes-arquivo")
    con = bc.conectar()
    itens = fila(con, a.cidade, a.limite, ligs or None, fatia, refazer_bing=a.refazer_bing, refazer=a.refazer,
                 reserva_google=a.reserva_google)
    con.close()
    if a.contar:
        por_motor = {}
        for it in itens:
            for _t, _p, m in it["tarefas"]:
                por_motor[m] = por_motor.get(m, 0) + 1
        _log("%d ligação(ões) com consulta por fazer · %s" % (len(itens), por_motor))
        return {"ligacoes": len(itens), **por_motor}
    return rodar(itens, a.trabalhadores, a.aplicar)


if __name__ == "__main__":
    main()
