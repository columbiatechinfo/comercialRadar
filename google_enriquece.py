# -*- coding: utf-8 -*-
"""google_enriquece.py — a última fonte de enriquecimento, pelo painel do Maps.

QUEM ENTRA AQUI

Só o POI que não tem nada para oferecer ainda: sem telefone, sem CNPJ, sem rede
social — e que não veio do próprio Google, porque desse a captura já trouxe o
que havia. Para os outros, abrir navegador é gastar IP e tempo em quem já está
atendido. Quem sai daqui sem nada fica marcado `esgotado`: as fontes de
enriquecimento acabaram para ele, e a próxima rodada não repete a busca.

POR QUE PELO CAMINHO DO PROJETO, E NÃO POR UM NOVO

A primeira versão desta etapa abria o `/search` do Google com navegador
próprio. Funcionou por uma rodada — 216 de 300 POIs, 54% com algum dado — e
depois parou de funcionar por completo. O diagnóstico, medido em 02/09/2026:

    do mesmo IP, na mesma hora        example.com    veio
                                      duckduckgo     veio
                                      google/search  CAPTCHA
                                      google/maps    veio

E do IP direto do i9, sem proxy nenhum, o mesmo par. O bloqueio não é do
endereço, é do endpoint — o mesmo padrão do iFood, onde a listagem responde 200
e o detalhe da loja responde 403 sempre. Rotacionar IP, renovar cookie ou
espaçar as buscas não muda nada no `/search`: 12 tentativas com 8 IPs, três
cookies e três ritmos deram 12 CAPTCHAs.

O projeto nunca usou o `/search`, e as regras que o mantêm fora de bloqueio
estão escritas no `search_from_sheet.worker` — que esta etapa repete, item por
item, porque é a mesma máquina alimentada pelo banco em vez da planilha:

    lote por sessão        `_chunk(BATCH_MIN=8, BATCH_MAX=15)`, tamanho sorteado
    um IP por lote         e não um IP por requisição, que é o que denuncia
    `abrir_maps` falhou    devolve o lote e põe o IP de castigo por 600 s
    `is_captcha()`         abandona o lote em vez de insistir e queimar o IP
    `humanized_wait()`     entre um POI e o outro
    perfil apagado         cada lote tem o seu, e ele morre com o lote

O proxy vai pelo `relay_proxy`, e não pelo `ProxyPool` direto: passar usuário e
senha ao Chromium pendura o `google.com/maps` — 35,3 s falhando contra 1,7 s
pelo relay, medido e escrito lá. O relay tem a mesma interface do pool.

A CONFERÊNCIA DE IDENTIDADE JÁ VEM PRONTA

`buscar_linha` devolve `match_valido`, e recebe `target_uf` — o filtro de
estado que impede o painel de outra cidade virar dado. Sem ele, uma busca por
`Padaria Bela Vista, Canoas` devolveu uma padaria de Curitiba com semelhança
0,78. Nada entra nas colunas sem `match_valido`; o que veio divergente fica em
`ia_resposta` para conferência humana.

O QUE SE PERDE E O QUE SE GANHA

O `/search` trazia CNPJ em 21% dos POIs; o painel do Maps não tem CNPJ. Em
troca, traz telefone, site, endereço e perfil social sem CAPTCHA.

    python google_enriquece.py --cidade Canoas --uf RS --limite 60 \
        --trabalhadores 4 --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import time
from collections import Counter

import base_comum as bc

# QUEM ENTRA NESTA ETAPA — a peneira em SQL, para não trazer POI que já está
# atendido só para descobrir isso depois de abrir o navegador.
SQL = """
    select p.id, p.nome, p.cidade, p.uf
      from radar_comercial.pois p
     where p.fundido_em is null and coalesce(p.nome,'') <> ''
       and coalesce(p.cnpj,'')      = ''
       and coalesce(p.telefone,'')  = ''
       and coalesce(p.instagram,'') = ''
       and coalesce(p.facebook,'')  = ''
       and coalesce(p.fonte,'') <> 'maps'
       and p.ia_resposta is null
       and length(coalesce(p.nome,'')) >= %s
       %s
     order by p.id
"""


def _log(m: str) -> None:
    print(m, flush=True)


def alvos(cur, cidade: str, limite: int, fontes: list, nome_min: int) -> list:
    """Os POIs, no formato de `item` que `buscar_linha` espera."""
    filtros, valores = "", [nome_min]
    if cidade:
        filtros += " and upper(coalesce(p.cidade,'')) = upper(%s)"
        valores.append(cidade)
    if fontes:
        filtros += " and coalesce(p.fonte,'') = any(%s)"
        valores.append(list(fontes))
    sql = SQL % ("%s", filtros)
    if limite:
        sql += " limit %s"
        valores.append(limite)
    cur.execute(sql, valores)
    return [{"_row": i, "id": i, "nome": n, "endereco": "",
             "lat": None, "lng": None, "cidade": c, "uf": u}
            for i, n, c, u in cur.fetchall()]


def gravar(con, cur, item: dict, rec: dict) -> tuple:
    """Grava o painel do Maps num POI. Devolve (notas, campos_gravados).

    NADA ENTRA SEM `match_valido`. O Maps responde alguma coisa para quase
    qualquer texto: sem essa conferência, o telefone do estabelecimento vizinho
    — ou de outra cidade — vira o telefone do POI. O divergente não se perde,
    fica em `ia_resposta` com o nome que o painel devolveu.
    """
    import ferramenta_maps as fm

    notas, campos, valores, gravados = [], [], [], []
    rec = rec or {}
    valido = bool(rec.get("match_valido"))

    if not valido and rec.get("nome"):
        notas.append("o painel respondeu «%s» (%s) — não gravado"
                     % (str(rec.get("nome"))[:40], rec.get("status")))

    social = fm._perfil_social(rec).get("perfil_social") or {}
    if valido:
        tel = re.sub(r"[^\d()+\- ]", "", str(rec.get("telefone") or "")).strip()
        if tel and len(re.sub(r"\D", "", tel)) >= 10:
            campos.append("telefone = coalesce(nullif(btrim(telefone),''), %s)")
            valores.append(tel[:60])
            gravados.append("telefone")

        # O `website` do painel COSTUMA SER a rede social da loja, e o próprio
        # `ferramenta_maps` já sabe separar isso. Gravar o Instagram na coluna
        # `website` perderia a informação de que aquilo é um perfil.
        rede, usuario = social.get("rede"), social.get("usuario")
        if rede in ("instagram", "facebook") and usuario:
            campos.append("%s = coalesce(nullif(btrim(%s),''), %%s)"
                          % (rede, rede))
            valores.append(usuario[:200])
            gravados.append(rede)

        site = str(rec.get("website_url") or rec.get("website") or "")
        if site.startswith("http") and not rede:
            campos.append("website = coalesce(nullif(btrim(website),''), %s)")
            valores.append(site[:400])
            gravados.append("site")

    registro = {"fonte": "maps_painel", "achou": bool(campos),
                "match_valido": valido, "status": rec.get("status"),
                "similaridade": rec.get("similaridade"),
                "nome_no_painel": rec.get("nome"),
                "telefone": rec.get("telefone"),
                "endereco": rec.get("endereco"),
                "website": rec.get("website_url") or rec.get("website"),
                "categoria": rec.get("categoria"),
                "perfil_social": social or None,
                "maps_url": rec.get("maps_url")}
    if not campos:
        registro["esgotado"] = True
    campos.append("ia_resposta = %s")
    valores.append(json.dumps(registro, ensure_ascii=False))
    cur.execute("update radar_comercial.pois set %s where id = %%s"
                % ", ".join(campos), valores + [item["id"]])
    con.commit()
    return notas, gravados


async def _trabalhador(wid, fila, pw, pool, cidade, uf, con, cur, placar,
                       usar_proxy, headless):
    """Um lote por vez, um IP por lote — a forma do `search_from_sheet`."""
    import config
    import search_from_sheet as sfs
    from human_browser import HumanSession

    while True:
        try:
            bidx, lote = fila.get_nowait()
        except asyncio.QueueEmpty:
            return

        proxy = await pool.acquire_blocking() if usar_proxy else None
        if usar_proxy and not proxy:
            fila.put_nowait((bidx, lote))
            await asyncio.sleep(5)
            continue

        # O RELAY NAO DEVOLVE `address`/`port` COMO O ProxyPool. Ele devolve
        # `server` apontando para o localhost — que e o motivo dele existir: o
        # Chromium nao pode ver credencial. Copiei o rotulo do worker do
        # pipeline sem conferir e a primeira rodada morreu em `KeyError`.
        rotulo = ("vaga %s" % proxy.get("_vaga") if proxy else "direto")
        perfil = config.BROWSER_PROFILES_DIR / ("radar_w%d_b%d" % (wid, bidx))
        sess = None
        try:
            sess = await HumanSession.create(pw, proxy, perfil, layer="maps",
                                             headless=headless)
            if not await sfs.abrir_maps(sess):
                # IP QUE NÃO ABRE O MAPS NÃO ABRE NA PRÓXIMA. Devolve o lote e
                # põe o endereço de castigo: insistir nele é gastar o pool.
                fila.put_nowait((bidx, lote))
                if proxy:
                    await pool.mark_cooldown(proxy, 600)
                continue

            for j, item in enumerate(lote):
                if await sess.is_captcha():
                    _log("      W%d lote %d (%s): CAPTCHA — lote abandonado"
                         % (wid, bidx, rotulo))
                    placar["captcha"] += 1
                    break
                rec = await sfs.buscar_linha(sess, item, cidade, "radar", uf)
                notas, achou = gravar(con, cur, item, rec)
                placar["achou_algo" if achou else "esgotado"] += 1
                for k in achou:
                    placar["campo_" + k] += 1
                _log("      %-32s %s"
                     % (str(item["nome"])[:32], ", ".join(achou) or "esgotado"))
                for n in notas:
                    _log("         %s" % n)
                if j < len(lote) - 1:
                    await sess.humanized_wait()
        except Exception as e:                                 # noqa: BLE001
            _log("      W%d lote %d: %s: %s"
                 % (wid, bidx, type(e).__name__, str(e)[:80]))
        finally:
            if sess:
                try:
                    await sess.close()
                except Exception:                              # noqa: BLE001
                    pass
            shutil.rmtree(perfil, ignore_errors=True)
            if proxy:
                try:
                    await pool.release(proxy)
                except Exception:                              # noqa: BLE001
                    pass


async def _colher(lista, cidade, uf, trabalhadores, con, cur, usar_proxy,
                  headless):
    import config
    import search_from_sheet as sfs
    from playwright.async_api import async_playwright
    from relay_proxy import PiscinaRelay

    lotes = sfs._chunk(lista, config.BATCH_MIN, config.BATCH_MAX)
    fila: "asyncio.Queue" = asyncio.Queue()
    for i, b in enumerate(lotes):
        fila.put_nowait((i, b))
    _log("   %d lotes de %d a %d POIs · um IP por lote"
         % (len(lotes), config.BATCH_MIN, config.BATCH_MAX))

    pool = PiscinaRelay(vagas=trabalhadores).start() if usar_proxy else None
    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    placar = Counter()
    n = min(trabalhadores, config.MAX_WORKERS, len(lotes))
    async with async_playwright() as pw:
        await asyncio.gather(*[
            _trabalhador(i, fila, pw, pool, cidade, uf, con, cur, placar,
                         usar_proxy, headless)
            for i in range(n)])
    shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)
    if pool:
        try:
            pool.encerrar()
        except Exception:                                      # noqa: BLE001
            pass
    return placar


def rodar(cidade="", uf="", limite=0, fontes=None, nome_min=12,
          trabalhadores=4, sem_proxy=False, aplicar=False) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    lista = alvos(cur, cidade, limite, fontes or [], nome_min)
    _log("   %d POIs sem telefone, sem CNPJ, sem rede social e que não vieram"
         % len(lista))
    _log("   do Google — os únicos que ainda têm o que ganhar aqui")
    if not lista:
        con.close()
        return {"alvos": 0}

    # A UF ALVO SAI DOS PRÓPRIOS POIs quando não é declarada — é o que
    # `search_from_sheet.run` faz com a coluna UF da planilha.
    if not uf:
        ufs = Counter(p["uf"] for p in lista if p.get("uf"))
        uf = ufs.most_common(1)[0][0] if ufs else ""
    _log("   UF alvo: %s" % (uf or "(qualquer — o painel de outra cidade passa)"))

    if not aplicar:
        _log("   (ensaio: nada buscado nem gravado. Use --aplicar)")
        for a in lista[:5]:
            _log("      %s · %s/%s" % (a["nome"][:42], a["cidade"] or "?",
                                       a["uf"] or "?"))
        con.close()
        return {"alvos": len(lista), "uf": uf}

    t0 = time.time()
    placar = asyncio.run(_colher(lista, cidade, uf, trabalhadores, con, cur,
                                 not sem_proxy, headless=True))
    dt = time.time() - t0
    feitos = sum(v for k, v in placar.items()
                 if k in ("achou_algo", "esgotado"))
    _log("\n   %d POIs · %.1f min · %.1f s por POI"
         % (feitos, dt / 60.0, dt / max(1, feitos)))
    for k, v in placar.most_common():
        _log("      %-18s %5d" % (k, v))
    con.close()
    return {"alvos": len(lista), **dict(placar)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Enriquece pelo painel do Maps quem não tem telefone, "
                    "CNPJ nem rede social")
    p.add_argument("--cidade", default="")
    p.add_argument("--uf", default="", help="UF alvo; sem ela, a maioria dos POIs")
    p.add_argument("--fonte", action="append", default=[])
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--nome-minimo", type=int, default=12,
                   help="nome curto demais não identifica estabelecimento")
    p.add_argument("--trabalhadores", type=int, default=4,
                   help="lotes simultâneos, um IP cada")
    p.add_argument("--sem-proxy", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ Maps, painel do estabelecimento%s"
         % (" · " + a.cidade if a.cidade else ""))
    rodar(a.cidade, a.uf, a.limite, a.fonte, a.nome_minimo, a.trabalhadores,
          a.sem_proxy, aplicar=a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
