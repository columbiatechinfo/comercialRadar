# -*- coding: utf-8 -*-
"""Descoberta reproduzível de lojas do iFood, por bairro.

O MÉTODO, e por que é este:

O feed da home tem dois endereços. O primário, `POST site-api/v2/bm/home`,
devolve 403 do PerimeterX em execução automatizada. O que o próprio site usa
quando aquele falha, `GET site-api/v2/home:fallback?search_token=...`, devolve
200 — e é ele que carrega a lista.

O `search_token` não é forjável de fora: quem o produz é a própria aplicação.
Então este módulo NÃO monta requisição nenhuma. Ele abre o navegador, muda a
localização como uma pessoa mudaria, e **escuta** a resposta que o site já ia
buscar de qualquer jeito. É a diferença entre ler o que chegou e arrombar o que
não chegou.

O QUE ESTE MÓDULO NÃO FAZ:

Não colhe CNPJ nem endereço da loja. Esses vivem no `merchant-info/graphql`,
que responde 403 do PerimeterX depois de poucas chamadas automatizadas. Cada
loja fica gravada com `estado_detalhe='PENDENTE'` — que quer dizer "não colhi",
nunca "não existe". Confundir os dois envenena todo cruzamento posterior.

Uso:
    python extrair_ifood.py --cidade canoas                # todos os bairros
    python extrair_ifood.py --cidade canoas --bairros 2    # amostra
    python extrair_ifood.py --cidade canoas --simular      # não grava
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
import json
import random
import sys
import time
import urllib.parse
from collections import defaultdict

from psycopg2.extras import execute_values

import base_comum as bc
# Só o que este módulo usa. `trocar_bairro` saiu de propósito: uma sessão por
# bairro nasce no lugar certo e nunca troca de localização — era esse o passo
# frágil que derrubava a varredura.
from enriquecer_ifood import (BASE, abrir_navegador, entrar_no_app,
                              tem_captcha, _humano, _n)

# O endereço do feed que responde 200. O primário (`bm/home`) fica de fora de
# propósito: ele devolve 403 e insistir nele só produz ruído no log.
ALVO_FEED = "home:fallback"
CARD_LOJAS = "MERCHANT_LIST_V2"

# Quanto esperar o SPA carregar o feed depois de trocar a localização. É o feed
# que dita o ritmo — não adianta pedir mais rápido do que ele responde.
NL = chr(10)
ESPERA_FEED = 12.0

# Um endereço REAL, com número, por bairro — não o centroide.
#
# O iFood exige número da casa para salvar o endereço e não oferece "sem
# número": o piloto registrou `address_number_required` em 2 de 9 pontos por
# usar coordenada de centroide. Estes saíram do `cadastro_cliente`, escolhendo
# em cada bairro o imóvel mais próximo da MEDIANA das coordenadas do bairro.
#
# Mediana, e não média: 135 imóveis gravados em (0,0) deslocavam todo centroide
# calculado por média para o mesmo canto da cidade.
PONTOS = {
    "canoas": [
        ("Centro", "Victor Barreto, 2301, Canoas"),
        ("Mathias Velho", "Rio Grande do Sul, 2770, Canoas"),
        ("Niteroi", "Farroupilha, 654, Canoas"),
        ("Harmonia", "Da Associacao, 98, Canoas"),
        ("Guajuviras", "Da Vitoria (guajuviras), 528, Canoas"),
        ("Estancia Velha", "Imbe, 556, Canoas"),
        ("Rio Branco", "Jose de Alencar, 369, Canoas"),
        ("Igara", "Luis Mauricio Scolari, 249, Canoas"),
        ("Olaria", "Santa Raquel, 189, Canoas"),
        ("Nossa Senhora das Gracas", "Santa Terezinha, 518, Canoas"),
        ("Sao Jose", "Celso Pedro Luft, 60, Canoas"),
        ("Fatima", "Buttenbender, 865, Canoas"),
        ("Mato Grande", "Atlanta, 222, Canoas"),
        ("Marechal Rondon", "Dona Rafaela, 653, Canoas"),
        ("Sao Luis", "Senador Salgado Filho, 556, Canoas"),
        ("Brigadeira", "Le Mans, 175, Canoas"),
    ],
}



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

    async def __call__(self, resp):
        if ALVO_FEED not in resp.url:
            return
        try:
            corpo = json.loads(await resp.text())
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
insert into comercialradar.ifood_merchant
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
        k.execute("""update comercialradar.ifood_merchant
                        set estado_detalhe = 'OK'
                      where cnpj is not null or rua is not null""")
    con.commit()
    return len(linhas)


async def um_bairro(pw, nome: str, endereco: str, args) -> tuple:
    """Abre uma sessão só para este bairro, lê o feed e fecha.

    Devolve (lojas, motivo). `motivo` é None quando deu certo — e quando não
    deu, diz o que houve, para a rodada seguinte não ser às cegas.
    """
    ouvinte = Escuta()
    ouvinte.bairro = nome
    br = None

    # PERFIL PRÓPRIO POR BAIRRO, descartado no fim.
    #
    # Sem isto as sessões compartilham o perfil persistente, que já tem um
    # endereço salvo — e aí `entrar_no_app` encontra a landing sem o campo de
    # busca, cai no atalho do /inicio e devolve True SEM TROCAR NADA. Foi o que
    # aconteceu: três bairros, o mesmo feed, 0 lojas inéditas nos dois últimos.
    #
    # A sessão virgem é obrigada a percorrer o fluxo de endereço, que é o que
    # de fato muda a praça do feed.
    perfil = os.path.join(tempfile.gettempdir(),
                          "cr_ifood_" + _n(nome).replace(" ", "_"))
    shutil.rmtree(perfil, ignore_errors=True)
    try:
        br, ctx, page = await abrir_navegador(pw, args.visivel, perfil_path=perfil)
        page.on("response", ouvinte)

        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        if await tem_captcha(page):
            return [], "desafio na abertura"
        if not await entrar_no_app(page, endereco):
            return [], "não consegui salvar o endereço"

        # o feed é buscado sozinho ao entrar; só se espera por ele
        limite = time.time() + ESPERA_FEED + 12.0
        while time.time() < limite and not ouvinte.lojas:
            await asyncio.sleep(0.5)
        await asyncio.sleep(2.5)          # deixa chegar o resto das seções
        await _humano(page)

        if not ouvinte.lojas and await tem_captcha(page):
            return [], "desafio antes do feed"
        return list(ouvinte.lojas.values()), None
    except Exception as e:
        return [], f"{type(e).__name__}"
    finally:
        if br:
            try:
                await br.close()
            except Exception:
                pass
        shutil.rmtree(perfil, ignore_errors=True)


async def rodar(args) -> int:
    from playwright.async_api import async_playwright
    pontos = PONTOS.get(args.cidade.lower())
    if not pontos:
        print(f"! não tenho os pontos de '{args.cidade}'.", flush=True)
        return 1
    pontos = pontos[:args.bairros or len(pontos)]

    print("⟦fase⟧ ifood-descoberta", flush=True)
    print(f"{len(pontos)} bairros · UMA SESSÃO POR BAIRRO · navegador "
          f"{'visível' if args.visivel else 'oculto'}", flush=True)

    todas: dict[str, dict] = {}
    secos = 0
    t0 = time.time()
    async with async_playwright() as pw:
        for nome, endereco in pontos:
            antes = len(todas)
            lojas, motivo = await um_bairro(pw, nome, endereco, args)
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
            # ponto, cada bairro devolve o que já se tem. Insistir é gastar
            # requisição no servidor deles e tempo seu. O piloto viu isso em
            # Harmonia, que rendeu 0,6%.
            secos = secos + 1 if razao < SATURADO else 0
            if secos >= 2:
                print(f"  ── saturado: dois bairros seguidos abaixo de "
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
                           from comercialradar.ifood_merchant group by 1""")
            print("estado:", dict(k.fetchall()), flush=True)
    finally:
        con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--bairros", type=int, default=0)
    p.add_argument("--simular", action="store_true")
    # Visível por padrão: é assim que se vê o que o iFood mostrou quando algo
    # falha. `--oculto` serve para rodada longa, desacompanhada.
    p.add_argument("--oculto", dest="visivel", action="store_false",
                   help="roda sem janela")
    p.set_defaults(visivel=True)
    return asyncio.run(rodar(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
