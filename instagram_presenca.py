# -*- coding: utf-8 -*-
"""A data do último post do perfil — o que transforma presença em prova.

O QUE ESTE MÓDULO RESOLVE. O banco guarda 15.627 arrobas de Instagram em
Canoas e nenhuma data de publicação. "Tem Instagram" diz que alguém já
manteve uma vitrine daquele negócio; "publicou há três semanas" diz que ele
está aberto HOJE — e é essa a diferença entre presença e prova, e o que o
score pontua.

────────────────────────────────────────────────────────────────────────────
POR QUE PELO NAVEGADOR, E NÃO POR `requests`

Três defesas derrubam raspador direto do Instagram, e duas delas não têm
conserto em Python:

  1. IP de datacenter é recusado na PRIMEIRA requisição.
  2. `requests` e `httpx` são identificados pela IMPRESSÃO DIGITAL DO TLS,
     antes de o servidor olhar o conteúdo. Não é cabeçalho a ajustar: é o
     formato do aperto de mão.
  3. O `doc_id` das consultas GraphQL rotaciona a cada 2-4 semanas, de
     propósito. Raspador que o fixa quebra sozinho nesse ritmo.

O projeto já derrubou as três por outro caminho. `human_browser.HumanSession`
é Chromium de verdade com stealth — não há impressão digital de `requests` a
corrigir porque não há `requests`. `proxy_pool` entrega os IPs residenciais
que já pagamos. E o `doc_id` deixa de ser problema nosso: quem monta a chamada
é o próprio JavaScript da página, e a rotação acompanha sozinha.

────────────────────────────────────────────────────────────────────────────
COMO A LEITURA ACONTECE

Navega uma vez em `instagram.com` para o contexto ganhar cookie e origem, e
daí em diante cada perfil é um `fetch` DE DENTRO DA PÁGINA para
`/api/v1/users/web_profile_info/`. É o mesmo endereço que o app usa deslogado,
e chamá-lo de dentro faz o navegador anexar cookie, origem e cabeçalhos
sozinho.

O `x-ig-app-id` é LIDO DA PÁGINA, e não escrito aqui. Ele aparece no pacote
JavaScript do próprio Instagram; fixá-lo no código seria criar a mesma dívida
do `doc_id`, com a diferença de que a quebra viria calada — a resposta volta
403 e pareceria bloqueio de IP.
"""
import argparse
import asyncio
import json
import os
import random
import re
import time
from pathlib import Path

import base_comum as bc

#: Quantos perfis por sessão de navegador antes de reciclar o IP.
#:
#: Um IP residencial satura por volta de 200 requisições por hora. 40 por
#: sessão deixa margem para a navegação inicial e para o que a página carrega
#: sozinha, e mantém o lote curto o bastante para uma sessão morta custar
#: pouco.
POR_SESSAO = 40

#: Espera entre perfis. Não é educação: é o que separa uma leitura de uma
#: varredura aos olhos de quem mede.
ESPERA = (2.5, 6.0)

SQL_FILA = """
select distinct s.arroba
  from (
    select lower(btrim(regexp_replace(
             coalesce(p.instagram,''),
             '^(https?://)?(www\\.)?instagram\\.com/|/.*$|^@', '', 'g'))) as arroba
      from radar_comercial.pois p
     where p.fundido_em is null
       and coalesce(p.instagram,'') <> ''
       {cidade}
  ) s
 where s.arroba <> ''
   and s.arroba !~ '[^a-z0-9._]'
   and length(s.arroba) between 2 and 30
   and not exists (select 1 from radar_comercial.rede_social r
                    where r.rede = 'instagram' and r.arroba = s.arroba
                      and (r.conferido_em is not null or r.tentativas >= 3))
 order by 1
 limit %s
"""

#: O LEITOR NÃO MORA AQUI, e não deve.
#:
#: `ferramenta_instagram.ler_perfil` é quem sabe conversar com o Instagram —
#: era ele quem já lia seguidores, bio, horário e links com sessão stealth, e
#: ganhou a data do último post no mesmo lugar. Este módulo é a FILA e a
#: PERSISTÊNCIA: quem entra, em que ritmo, com qual IP e onde o resultado é
#: gravado.
#:
#: Duas cópias da mesma leitura divergiriam no primeiro ajuste do Instagram, e
#: seria a versão velha que continuaria rodando em lote — 15.627 perfis lidos
#: pelo código desatualizado enquanto a ferramenta do chat funcionava bem.

def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def fila(con, limite, cidade=""):
    cur = con.cursor()
    filtro = ("and upper(coalesce(p.cidade,'')) = upper(%s)" % "%s") if cidade else ""
    sql = SQL_FILA.format(cidade=filtro)
    cur.execute(sql, ((cidade, limite) if cidade else (limite,)))
    return [r[0] for r in cur.fetchall()]


def gravar(con, arroba, d, empresa):
    """Uma linha por PERFIL. Erro também é resultado e também se grava."""
    cur = con.cursor()
    erro = d.get("erro")
    up = d.get("ultimo_post")
    cur.execute("""
        insert into radar_comercial.rede_social
            (id_empresa, rede, arroba, perfil_url, ultimo_post, posts,
             seguidores, e_comercial, categoria, nome_exibido, bio, site,
             conferido_em, erro, tentativas)
        values (%s, 'instagram', %s, %s,
                case when %s is null then null
                     else to_timestamp(%s) end,
                %s, %s, %s, %s, %s, %s, %s,
                case when %s is null then now() else null end, %s, 1)
        on conflict (id_empresa, rede, arroba) do update set
            perfil_url = excluded.perfil_url,
            ultimo_post = coalesce(excluded.ultimo_post,
                                   radar_comercial.rede_social.ultimo_post),
            posts = coalesce(excluded.posts, radar_comercial.rede_social.posts),
            seguidores = coalesce(excluded.seguidores,
                                  radar_comercial.rede_social.seguidores),
            e_comercial = coalesce(excluded.e_comercial,
                                   radar_comercial.rede_social.e_comercial),
            categoria = coalesce(excluded.categoria,
                                 radar_comercial.rede_social.categoria),
            nome_exibido = coalesce(excluded.nome_exibido,
                                    radar_comercial.rede_social.nome_exibido),
            bio = coalesce(excluded.bio, radar_comercial.rede_social.bio),
            site = coalesce(excluded.site, radar_comercial.rede_social.site),
            conferido_em = coalesce(excluded.conferido_em,
                                    radar_comercial.rede_social.conferido_em),
            erro = excluded.erro,
            -- A TENTATIVA SEMPRE SOBE, inclusive quando deu certo. É ela que
            -- tira da fila o perfil que nunca vai responder: sem isso, os
            -- privados e os removidos voltariam para sempre.
            tentativas = radar_comercial.rede_social.tentativas + 1
    """, (empresa, arroba, "https://www.instagram.com/%s/" % arroba,
          up, up, d.get("posts"), d.get("seguidores"), d.get("e_comercial"),
          d.get("categoria"), d.get("nome"), d.get("bio"), d.get("site"),
          erro, erro))
    con.commit()


async def _um_lote(pw, proxy, arrobas, con, empresa, placar, pasta):
    import ferramenta_instagram as fi
    import human_browser as hb
    ses = None
    try:
        ses = await hb.HumanSession.create(pw, proxy, pasta, layer="serp",
                                           headless=True)
        pg = ses.page or (ses.context.pages[0] if ses.context.pages
                          else await ses.context.new_page())
        await pg.goto("https://www.instagram.com/", wait_until="domcontentloaded",
                      timeout=45000)
        await pg.wait_for_timeout(3500)
        # ONDE A PÁGINA PAROU, e não se ela carregou. `domcontentloaded` volta
        # verdadeiro numa tela de login igualzinho — e dali o endpoint responde
        # 429 para todo mundo. Sem esta linha, o diagnóstico vira "o IP está
        # queimado" quando a causa é outra: o Instagram serviu o muro.
        try:
            onde = pg.url
            titulo = (await pg.title())[:60]
        except Exception:                                      # noqa: BLE001
            onde, titulo = "?", "?"
        _log("   página inicial: %s · %r" % (onde, titulo))
        if "/accounts/login" in onde:
            _log("   MURO DE LOGIN na porta de entrada — este IP não vai ler")
            _log("   perfil nenhum. Devolvendo o lote.")
            placar["bloqueio"] += 1
            return
        for arroba in arrobas:
            t0 = time.time()
            d = await fi.ler_perfil(pg, arroba)
            dt = time.time() - t0
            gravar(con, arroba, d or {"erro": "sem resposta"}, empresa)
            if d and not d.get("erro"):
                placar["ok"] += 1
                quando = d.get("ultimo_post")
                if quando:
                    placar["com_data"] += 1
                _log("   %-28s %s · %s posts · %s seg · %.1fs"
                     % (arroba[:28],
                        time.strftime("%d/%m/%Y", time.localtime(quando))
                        if quando else "sem post",
                        d.get("posts"), d.get("seguidores"), dt))
            else:
                placar["falha"] += 1
                _log("   %-28s FALHOU: %s · %.1fs"
                     % (arroba[:28], (d or {}).get("erro", "?")[:60], dt))
                if (d or {}).get("status") in (401, 429):
                    placar["bloqueio"] += 1
                    _log("   IP parece queimado — encerrando o lote")
                    break
            await asyncio.sleep(random.uniform(*ESPERA))
    finally:
        if ses and ses.context:
            try:
                await ses.context.close()
            except Exception:                                  # noqa: BLE001
                pass


async def rodar(limite, cidade, aplicar):
    from playwright.async_api import async_playwright
    from proxy_pool import ProxyPool

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("select core.empresa_atual()")
    empresa = cur.fetchone()[0]
    if not empresa:
        empresa = os.environ.get("RADAR_EMPRESA")
    if not empresa:
        raise SystemExit("sem empresa — defina RADAR_EMPRESA ou rode com claim")

    alvos = fila(con, limite, cidade)
    _log("%d perfil(is) na fila" % len(alvos))
    if not alvos:
        return
    if not aplicar:
        for a in alvos[:20]:
            _log("   %s" % a)
        _log("(ensaio: nada gravado. Use --aplicar)")
        return

    pool = ProxyPool().start()
    placar = {"ok": 0, "falha": 0, "com_data": 0, "bloqueio": 0}
    t0 = time.time()
    pasta = Path("/tmp/ig_perfil")
    async with async_playwright() as pw:
        for i in range(0, len(alvos), POR_SESSAO):
            lote = alvos[i:i + POR_SESSAO]
            # `acquire_blocking` E O NOME REAL, e a espera é o ponto: com
            # 100 IPs e vários processos disputando, pegar o primeiro livre
            # sem esperar devolveria `None` e o lote sairia sem proxy — que é
            # o jeito mais rápido de queimar o IP da própria máquina.
            proxy = await pool.acquire_blocking(intervalo=2.0, tentativas=30)
            # O LOG MENTIA SOBRE O PROXY. Ele lia `proxy_address`, que é o
            # nome do campo NA API da Webshare; o dicionário do pool guarda
            # `address`. O `.get` devolvia o padrão e a tela dizia "sem
            # proxy" com o proxy na mão — e eu quase diagnostiquei o 429 como
            # falta de proxy, que era o lado errado.
            if not proxy:
                _log("SEM PROXY LIVRE depois de 30 tentativas — não vou sair")
                _log("pelo IP do próprio i9: é datacenter, e o Instagram o")
                _log("recusa na primeira requisição.")
                break
            _log("lote %d/%d · %d perfis · IP %s:%s"
                 % (i // POR_SESSAO + 1,
                    (len(alvos) + POR_SESSAO - 1) // POR_SESSAO, len(lote),
                    proxy.get("address", "?"), proxy.get("port", "?")))
            try:
                await _um_lote(pw, proxy, lote, con, empresa, placar,
                               pasta / str(i))
            finally:
                # `release` E CORROTINA. Sem o `await`, o Python emitia
                # "coroutine was never awaited" e o IP ficava marcado como em
                # uso para sempre — o pool encolheria a cada lote até não
                # sobrar nenhum.
                if proxy:
                    await pool.release(proxy)
    dt = time.time() - t0
    con.close()
    _log("")
    _log("%d ok · %d com data de post · %d falha · %d bloqueio"
         % (placar["ok"], placar["com_data"], placar["falha"],
            placar["bloqueio"]))
    _log("%.1f min · %.1f perfis/min" % (dt / 60, len(alvos) / max(dt / 60, .01)))
    if placar["ok"]:
        _log("PARA DIMENSIONAR: nesse ritmo, 15.627 perfis levam %.1f h"
             % (15627 / max(len(alvos) / (dt / 3600), 1)))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Colhe a data do último post dos perfis de Instagram")
    p.add_argument("--limite", type=int, default=50)
    p.add_argument("--cidade", default="")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    asyncio.run(rodar(a.limite, a.cidade, a.aplicar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
