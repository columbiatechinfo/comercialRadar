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
on conflict (merchant_id) do update set
  nome      = coalesce(excluded.nome,      ifood_merchant.nome),
  categoria = coalesce(excluded.categoria, ifood_merchant.categoria),
  slug      = coalesce(excluded.slug,      ifood_merchant.slug),
  nota      = coalesce(excluded.nota,      ifood_merchant.nota),
  bairro    = coalesce(ifood_merchant.bairro, excluded.bairro),
  bruto     = excluded.bruto,
  visto_em  = now()
"""


def gravar(con, lojas: list[dict]) -> int:
    """Grava a descoberta. NUNCA rebaixa o estado de quem já tem detalhe.

    Uma loja que já teve CNPJ colhido não pode voltar a PENDENTE só porque
    reapareceu na listagem — a listagem não sabe nada sobre o detalhe.
    """
    linhas = [(
        lj["merchant_id"], lj["nome"], lj["categoria"], lj["slug"], lj["nota"],
        lj.get("bairro_busca"),
        "PENDENTE",
        json.dumps({"distancia_km": lj.get("distancia_km"),
                    "disponivel": lj.get("disponivel"),
                    "origem": "home:fallback"}, ensure_ascii=False),
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
    for pt in pontos:
        nome = pt["rotulo"]
        antes = len(todas)
        proxy = await pool.acquire() if pool else None
        # A sessão é síncrona (ver `um_ponto`); o laço continua assíncrono por
        # causa do pool, que é.
        lojas, motivo = await asyncio.to_thread(
            um_ponto, nome, pt["lat"], pt["lon"], args, proxy)
        if pool and proxy:
            # Proxy que tomou desafio vai para o DESCANSO, não para o fim da
            # fila. Devolvê-lo à rotação queima o IP de vez: o próximo ponto
            # o pega ainda marcado e toma o mesmo desafio.
            if motivo and ("desafio" in motivo or "não aplicou" in motivo):
                await pool.mark_cooldown(proxy)
            else:
                await pool.release(proxy)
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
                   help="não usa o pool; as sessões saem pelo IP direto")
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
    return asyncio.run(rodar(a))


if __name__ == "__main__":
    sys.exit(main())
