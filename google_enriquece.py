# -*- coding: utf-8 -*-
"""google_enriquece.py — o Google direto, em janela quente, sem IA no meio.

O QUE SAIU, E POR QUÊ

O passo anterior buscava no Bing, baixava cinco páginas por POI e mandava tudo
para o modelo da Spark ler. Medido em 02/09/2026 sobre oito POIs do dataset
estadual, com nome próprio de verdade:

    8 gravados · `ramo` em 3 · ZERO CNPJ · ZERO telefone · 8,9 s por POI

Muito trabalho para pouco retorno. O modelo lia bem — o problema era o que
chegava até ele: o Bing não indexa os agregadores de CNPJ, então não havia o que
extrair. Trocar o leitor não resolveria; trocar a fonte, sim.

O QUE ENTRA NO LUGAR

O Google, direto, na mesma janela quente que já serve ao Maps — com o
`cookie_maps.json`, os mesmos 1,2 KB de três cookies que fazem o Maps mostrar
três abas em vez de duas. E sem leitor no meio: a página de resultados do Google
JÁ TRAZ o dado estruturado no painel lateral, com rótulo. Telefone é `Telefone:`,
endereço é `Endereço:`. Não há o que interpretar.

VÁRIAS GUIAS NA MESMA JANELA. A janela é o caro — perfil, IP, cookie. A guia
custa quase nada e é o que multiplica. Cada uma puxa da mesma fila e devolve o
resultado; nenhuma espera a outra.

`--disable-http2` NÃO É OPCIONAL. Chromium com proxy contra o Google trava sem
ele, e o sintoma vira "IP queimado" — foi diagnosticado em 24/07/2026 e custou
uma sondagem inteira.

O QUE É EXTRAÍDO, E COM QUE CERTEZA

    do painel      telefone, endereço, site — vêm rotulados pelo próprio Google
    do texto       CNPJ, quando aparece em algum resultado
    das âncoras    instagram e facebook, pelo domínio do link

O CNPJ passa pelas mesmas três portas de sempre: dígitos verificadores, a
`rf_estabelecimentos` local, e o município. CNPJ de outra cidade é a matriz da
rede, e gravar a matriz no lugar da filial estraga o cruzamento.

QUEM NÃO FOR ENCONTRADO ESGOTOU AS FONTES. Não é falha da etapa: é a resposta.
O POI recebe `{"esgotado": true}` em `ia_resposta` e não é perguntado de novo —
insistir custaria uma janela por POI para chegar ao mesmo lugar.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from collections import Counter

import base_comum as bc

COOKIE = "/app/estado/cookie_maps.json"
# `--disable-http2` é o que impede o Chromium com proxy de travar no Google.
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]
BUSCA = "https://www.google.com/search?q=%s&hl=pt-BR&gl=br"

# O painel do Google rotula o que mostra, e é isso que dispensa um leitor. O
# resto da página entra como texto para o CNPJ, que não tem rótulo fixo.
JS_COLHER = r"""() => {
  // `innerText`, NAO `textContent`.
  //
  // `textContent` devolve tambem o conteudo de <style> e <script>. Na pagina
  // do Google isso e a maior parte do texto: a depuracao de 02/09/2026 leu
  // `:root{--COEmY:#1f1f1f...}` e tirou dali quatro "telefones" que eram
  // numeros de folha de estilo. `innerText` devolve o que esta na tela.
  const texto = (document.body ? document.body.innerText || '' : '')
                  .replace(/\s+/g, ' ');

  // O painel lateral e o bloco local trazem `Telefone:` e `Endereço:` escritos.
  const rotulado = (rot) => {
    const re = new RegExp(rot + '\\s*:?\\s*([^|]{4,80}?)(?=\\s{2,}|$|[A-ZÀ-Ú][a-zà-ú]+:)', 'i');
    const m = texto.match(re);
    return m ? m[1].trim() : null;
  };

  // Telefone brasileiro, com ou sem DDD entre parênteses.
  const fones = [...texto.matchAll(/\(?\b(\d{2})\)?\s?9?\d{4}[-\s]?\d{4}\b/g)]
                  .map(m => m[0].trim());

  // CNPJ com ou sem pontuação.
  const cnpjs = [...texto.matchAll(/\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b/g)]
                  .map(m => m[0]);

  // As redes vêm dos próprios links, e não do texto: o domínio não mente.
  const rede = {};
  for (const a of document.querySelectorAll('a[href]')) {
    const h = a.getAttribute('href') || '';
    if (!rede.instagram && /instagram\.com\/[A-Za-z0-9_.]+/.test(h))
      rede.instagram = (h.match(/https?:\/\/[^&"]*instagram\.com\/[A-Za-z0-9_.]+/) || [])[0];
    if (!rede.facebook && /facebook\.com\/[A-Za-z0-9_.-]+/.test(h))
      rede.facebook = (h.match(/https?:\/\/[^&"]*facebook\.com\/[A-Za-z0-9_.-]+/) || [])[0];
  }

  // O SITE PRECISA SER DAQUELA EMPRESA — E HA DUAS ORIGENS.
  //
  // Pegar "o primeiro link que nao e do Google" gravou `cna.oab.org.br`, o
  // Cadastro Nacional dos Advogados, para um POI que e a escola de idiomas CNA
  // do Canoas Shopping: o primeiro organico era outra entidade com a sigla.
  //
  // Mas o painel lateral tem um botao `Site` que aponta o site oficial DAQUELE
  // lugar — esse nao precisa de conferencia, ja veio identificado. O organico
  // precisa. Por isso a origem sobe junto: o Python confere so o que e organico.
  let site = null, siteTitulo = null, siteOrigem = null;
  for (const a of document.querySelectorAll('a[href^="http"]')) {
    const h = a.getAttribute('href');
    if (/google\.|gstatic|youtube\.|instagram\.|facebook\.|schema\.org/.test(h))
      continue;
    const h3 = a.querySelector('h3');
    const rot = (a.textContent || '').trim();
    site = h;
    siteTitulo = h3 ? h3.textContent.trim() : rot.slice(0, 120);
    siteOrigem = (!h3 && /^(site|website|site oficial)$/i.test(rot))
                 ? 'painel' : 'organico';
    break;
  }

  // O ROTULO ACHA O TRECHO; O PADRAO ACHA O NUMERO.
  //
  // `rotulado('Telefone')` devolve o texto que vem depois da palavra, e o
  // Google nem sempre poe separador: veio `(51) 99161-8166Horario de` para o
  // CNA. Guardar isso como telefone deixa o campo inutilizavel para discar.
  // Entao do trecho rotulado extrai-se so o que tem forma de telefone.
  const soFone = (t) => {
    if (!t) return null;
    const m = t.match(/\(?\b\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b/);
    return m ? m[0].trim() : null;
  };

  return {telefone_rotulado: soFone(rotulado('Telefone')),
          endereco_rotulado: rotulado('Endereço'),
          telefones: [...new Set(fones)].slice(0, 4),
          cnpjs: [...new Set(cnpjs)].slice(0, 4),
          instagram: rede.instagram || null,
          facebook: rede.facebook || null,
          site: site,
          site_titulo: siteTitulo,
          site_origem: siteOrigem,
          captcha: /nossos sistemas detectaram|unusual traffic|not a robot/i.test(texto),
          amostra: texto.slice(0, 600)};
}"""


def _log(m: str) -> None:
    print(m, flush=True)


# ────────────────────────────────────────────────────────── o CNPJ ──────────
def cnpj_valido(bruto) -> bool:
    n = re.sub(r"\D", "", str(bruto or ""))
    if len(n) != 14 or len(set(n)) == 1:
        return False
    # RAIZ ZERADA NAO EXISTE. `00000000477249` passou nos digitos verificadores
    # e foi gravado como CNPJ de um POI chamado `Bairro Niteroi` — era um numero
    # qualquer da pagina que por acaso fechou a conta. Empresa nenhuma tem
    # cnpj_basico 00000000.
    if n[:8] == "00000000":
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(n[:tamanho], pesos)) % 11
        if int(n[tamanho]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def rf_do_ibge(cur, cod_ibge: str):
    """O codigo do municipio na Receita NAO e o do IBGE.

    A Receita numera municipio pelo proprio catalogo (`rf_municipios.codigo`),
    e o IBGE pelo dele — 4304606 para Canoas. Comparar os dois direto dava
    sempre diferente, e a etapa dizia "na Receita, mas em CANOAS" sobre um CNPJ
    que ESTAVA em Canoas. A ponte e o nome.
    """
    if not cod_ibge:
        return None
    cur.execute("""
        select r.codigo from resources_root.rf_municipios r
          join resources_root.ibge_malha m
            on translate(upper(m.nome), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC')
             = translate(upper(r.descricao), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC')
         where m.cod_municipio = %s limit 1
    """, (str(cod_ibge),))
    r = cur.fetchone()
    return r[0] if r else None


def na_receita(cur, cnpj: str) -> dict:
    n = re.sub(r"\D", "", cnpj or "")
    if len(n) != 14:
        return {"existe": False}
    cur.execute("""
        select e.municipio, e.situacao_cadastral, em.razao_social, m.descricao
          from resources_root.rf_estabelecimentos e
          left join resources_root.rf_empresas em on em.cnpj_basico = e.cnpj_basico
          left join resources_root.rf_municipios m on m.codigo = e.municipio
         where e.cnpj_basico = %s and e.cnpj_ordem = %s and e.cnpj_dv = %s
         limit 1
    """, (n[:8], n[8:12], n[12:]))
    r = cur.fetchone()
    if not r:
        return {"existe": False}
    return {"existe": True, "municipio": r[0], "situacao": r[1],
            "razao_social": r[2], "municipio_nome": r[3]}


# ──────────────────────────────────────────────────────── as guias ──────────
def consulta_de(poi: dict) -> str:
    partes = ['"%s"' % poi["nome"]]
    if poi.get("onde"):
        partes.append(poi["onde"])
    partes += [poi.get("cidade") or "", poi.get("uf") or ""]
    return " ".join(x for x in partes if str(x).strip())


async def uma_guia(sessao, fila, saidas, nome_guia: str, pausa: tuple) -> None:
    """Uma guia consome a fila ate ela secar.

    ASSINCRONO, E NAO THREAD. A primeira versao usava `threading.Thread` sobre a
    API sincrona e morreu com "Cannot switch to a different thread": a sessao
    sincrona pertence ao fio que a criou e nao atravessa nenhum outro. As oito
    buscas viraram oito `TargetClosedError`.

    A guia termina se o Google mostrar CAPTCHA: a sessao esta queimada e
    insistir gasta o IP a toa. O POI volta para a fila e outra guia — com outro
    IP, porque o rodizio troca a cada requisicao — tenta de novo.
    """
    import asyncio
    import random

    while True:
        try:
            poi = fila.get_nowait()
        except asyncio.QueueEmpty:
            return
        alvo = BUSCA % urllib.parse.quote(consulta_de(poi))
        caixa = {}

        async def acao(pagina, _c=caixa, _p=pausa):
            await pagina.wait_for_timeout(random.randint(*_p))
            _c["d"] = await pagina.evaluate(JS_COLHER)
            return pagina

        try:
            await sessao.fetch(alvo, page_action=acao, timeout=45000)
            d = caixa.get("d") or {"erro": "sem retorno"}
        except Exception as e:                                 # noqa: BLE001
            d = {"erro": "%s" % type(e).__name__}
        if d.get("captcha"):
            fila.put_nowait(poi)
            _log("      %s: CAPTCHA — guia encerrada" % nome_guia)
            return
        if d.get("erro"):
            # ERRO NAO E FONTE ESGOTADA. Uma pagina que navegou no meio da
            # leitura ("Execution context was destroyed") nao respondeu nada
            # sobre o estabelecimento; marca-la como esgotada impediria a
            # proxima rodada de tentar. Fica de fora do placar e volta depois.
            _log("      %s: %s em %s — fica para a próxima"
                 % (nome_guia, d["erro"], str(poi.get("nome"))[:28]))
            continue
        saidas.append((poi, d))


async def _colher(pois: list, janelas: int, guias: int, proxies: list,
                  pausa: tuple) -> list:
    import asyncio
    import json

    from scrapling.fetchers import AsyncStealthySession

    fila: "asyncio.Queue" = asyncio.Queue()
    for x in pois:
        fila.put_nowait(x)
    saidas = []

    # UM IP POR JANELA, FIXO — E NAO UM RODIZIO POR REQUISICAO.
    #
    # Medido em 02/09/2026, mesma busca, MESMO IP (104.165.145.72):
    #
    #     proxy_rotator=   CAPTCHA
    #     proxy=           passou, com site e telefone
    #
    # Nao e o endereco que queima: e trocar de endereco no meio da sessao. O
    # rotador do Scrapling troca a cada requisicao, entao as sub-requisicoes de
    # uma mesma pagina saem de IPs diferentes, e isso o Google marca. Com um IP
    # fixo por janela a sessao fica coerente do inicio ao fim.
    #
    # O paralelismo entao vem de VARIAS JANELAS, cada uma com o seu IP, e de
    # guias dentro de cada uma. O IP direto do i9 ja esta queimado — o Google
    # serve /sorry para ele —, entao sem proxy esta etapa nao roda.
    biscoitos = None
    if os.path.exists(COOKIE):
        try:
            biscoitos = json.load(open(COOKIE, encoding="utf-8")).get("cookies")
        except Exception:                                      # noqa: BLE001
            biscoitos = None

    async def uma_janela(i):
        sessao = AsyncStealthySession(
            max_pages=guias, headless=True, google_search=True,
            timezone_id="America/Sao_Paulo",
            cookies=biscoitos or None,
            proxy=proxies[i % len(proxies)] if proxies else None)
        await sessao.start()
        try:
            await asyncio.gather(*[
                uma_guia(sessao, fila, saidas,
                         "janela %d/guia %d" % (i + 1, g + 1), pausa)
                for g in range(guias)])
        finally:
            try:
                await sessao.close()
            except Exception:                                  # noqa: BLE001
                pass

    await asyncio.gather(*[uma_janela(i) for i in range(janelas)])
    return saidas


def colher(pois: list, janelas: int, guias: int, proxies: list,
           pausa: tuple) -> list:
    import asyncio
    return asyncio.run(_colher(pois, janelas, guias, proxies, pausa))


# QUEM ENTRA NESTA ETAPA.
#
# So o POI que nao tem NADA para oferecer ainda: sem telefone, sem CNPJ, sem
# rede social — e que nao veio do proprio Google, porque desse a captura ja
# trouxe o que o Google tem. Abrir o navegador para os outros e gastar IP e
# tempo em quem ja esta atendido.
SQL = """
    select p.id, p.nome, p.cidade, p.uf,
           coalesce(l.logradouro,'') as via, coalesce(l.numero,'') as numero,
           coalesce(l.forca,'sem') as forca
      from radar_comercial.pois p
      left join radar_comercial.logradouro_resolvido l on l.poi_id = p.id
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

VAZIAS = {
    "ltda", "eireli", "epp", "sa", "mei", "com", "the", "and", "dos", "das",
    "comercio", "servicos", "servico", "industria", "empresa", "loja", "casa",
    "centro", "brasil", "brazil", "www", "http", "https", "site", "org", "net",
    "canoas", "porto", "alegre", "rio", "grande", "sul", "shopping", "bairro",
}


def alvos(cur, cidade: str, limite: int, fontes: list, nome_min: int) -> list:
    """Os POIs que ainda tem o que ganhar aqui, ja com o endereco pronto."""
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
    saida = []
    for pid, nome, cid, uf, via, numero, forca in cur.fetchall():
        # O endereco so entra na busca quando e PROVA: indicio veio da
        # coordenada, e endereco chutado na consulta troca o resultado certo
        # por outro estabelecimento da mesma rua.
        onde = ""
        if forca == "prova" and via:
            onde = ("%s %s" % (via, numero)).strip()
        saida.append({"id": pid, "nome": nome, "cidade": cid, "uf": uf,
                      "onde": onde})
    return saida


def _palavras(nome: str) -> set:
    """As palavras do nome que servem para reconhecer a empresa.

    Fora ficam as que qualquer empresa tem — LTDA, COMERCIO, o nome da cidade —
    porque casar por elas aceita qualquer coisa. Sobra o que distingue.
    """
    t = unicodedata.normalize("NFKD", str(nome or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return {p for p in re.split(r"[^a-z0-9]+", t)
            if len(p) >= 3 and p not in VAZIAS}


def site_confere(nome: str, site: str, titulo: str, origem: str):
    """O link organico e daquela empresa? Devolve (aceita, motivo).

    O painel do Google ja diz de quem e o site — esse entra sem exame. O
    organico e so o primeiro resultado da busca, e primeiro resultado nao e
    prova: para `Cna - Canoas Shopping` o primeiro foi `cna.oab.org.br`, a
    Ordem dos Advogados. Exige-se que uma palavra do nome apareca no dominio ou
    no titulo do resultado.
    """
    if not site:
        return False, "sem site"
    if origem == "painel":
        return True, "site do painel do Google"
    palavras = _palavras(nome)
    if not palavras:
        return False, "nome sem palavra própria para conferir"
    alvo = unicodedata.normalize("NFKD", ("%s %s" % (site, titulo or "")).lower())
    alvo = "".join(c for c in alvo if not unicodedata.combining(c))
    batem = sorted(p for p in palavras if p in alvo)
    if batem:
        return True, "orgânico, bate em %s" % ", ".join(batem[:3])
    return False, "orgânico e nenhuma palavra do nome aparece no link"


def gravar(con, cur, poi, d, cod_rf) -> list:
    """Grava o que veio, e marca ESGOTADO quem não trouxe nada.

    `esgotado` não é fracasso registrado por desencargo: é o que impede a
    próxima rodada de abrir outra janela para o mesmo POI e chegar ao mesmo
    lugar. As fontes de enriquecimento acabaram para ele.
    """
    notas, campos, valores, gravados = [], [], [], []
    d = d or {}

    # SO O TELEFONE ROTULADO VALE.
    #
    # A pagina do Google tem numeros de tudo: de outros resultados, de anuncios,
    # do proprio Google. Pegar "o primeiro que parece telefone" deu telefone a 8
    # de 8 POIs — inclusive a um chamado `Bairro Niteroi`, que e um bairro. O
    # painel lateral escreve `Telefone:` na frente do numero DAQUELE lugar, e e
    # so esse que se aproveita.
    tel = d.get("telefone_rotulado")
    if tel and len(re.sub(r"\D", "", tel)) >= 10:
        campos.append("telefone = coalesce(nullif(btrim(telefone),''), %s)")
        valores.append(tel.strip()[:60])
        gravados.append("telefone")

    for cnpj in (d.get("cnpjs") or []):
        n = re.sub(r"\D", "", cnpj)
        if not cnpj_valido(n):
            continue
        r = na_receita(cur, n)
        if not r["existe"]:
            conf, nota = 0.3, "dígitos fecham, não está na Receita"
        elif cod_rf and str(r.get("municipio") or "") == str(cod_rf):
            conf, nota = 0.9, "confirmado na Receita, mesmo município"
        else:
            conf = 0.5
            nota = "na Receita, mas em %s" % (r.get("municipio_nome") or "?")
        notas.append("cnpj %s: %s" % (n, nota))
        campos += ["cnpj = coalesce(nullif(btrim(cnpj),''), %s)",
                   "cnpj_conf = coalesce(cnpj_conf, %s)"]
        valores += [n, conf]
        gravados.append("cnpj")
        if r["existe"] and r.get("razao_social"):
            campos.append("razao_social = coalesce(nullif(btrim(razao_social),''), %s)")
            valores.append(str(r["razao_social"])[:300])
        break                                   # o primeiro válido basta

    ok, motivo = site_confere(poi.get("nome"), d.get("site"),
                              d.get("site_titulo"), d.get("site_origem"))
    if d.get("site") and not ok:
        notas.append("site descartado: %s" % motivo)

    for coluna, chave in (("website", "site" if ok else "_nao"),
                          ("instagram", "instagram"), ("facebook", "facebook")):
        v = d.get(chave)
        if v:
            campos.append("%s = coalesce(nullif(btrim(%s),''), %%s)"
                          % (coluna, coluna))
            valores.append(str(v)[:400])
            gravados.append("site" if coluna == "website" else coluna)

    achou = bool(campos)
    registro = {"fonte": "google", "achou": achou,
                "telefone": tel, "cnpjs": d.get("cnpjs"),
                "site": d.get("site") if ok else None,
                "site_motivo": motivo,
                "instagram": d.get("instagram"),
                "facebook": d.get("facebook"),
                "endereco_google": d.get("endereco_rotulado")}
    if not achou:
        registro["esgotado"] = True
    campos.append("ia_resposta = %s")
    valores.append(json.dumps(registro, ensure_ascii=False))
    cur.execute("update radar_comercial.pois set %s where id = %%s"
                % ", ".join(campos), valores + [poi["id"]])
    con.commit()
    return notas, gravados


def rodar(cidade="", cod="", limite=0, fontes=None, nome_min=12, guias=4,
          sem_proxy=False, pausa=(1200, 2600), aplicar=False,
          janelas=4) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    lista = alvos(cur, cidade, limite, fontes or [], nome_min)
    _log("   %d POIs sem telefone, sem CNPJ, sem rede social e que não vieram"
         % len(lista))
    _log("   do Google — os únicos que ainda têm o que ganhar aqui")
    if not lista:
        con.close()
        return {"alvos": 0}
    if not aplicar:
        _log("   (ensaio: nada buscado nem gravado. Use --aplicar)")
        for a in lista[:5]:
            _log("      %s" % consulta_de(a)[:88])
        con.close()
        return {"alvos": len(lista)}

    proxies = []
    if not sem_proxy:
        try:
            import busca_navegador as bn
            proxies = bn.lista_de_proxies(max(janelas * 2, 12))
        except Exception as e:                                 # noqa: BLE001
            _log("   ⚠️  sem proxy (%s) — IP direto" % type(e).__name__)
    _log("   %d IPs — um fixo por janela" % len(proxies))

    _log("   %d janelas × %d guias no navegador do repositório%s"
         % (janelas, guias,
            " · com o cookie do Maps" if os.path.exists(COOKIE) else ""))
    t0 = time.time()
    saidas = colher(lista, janelas, guias, proxies, pausa)
    dt = time.time() - t0
    cod_rf = rf_do_ibge(cur, cod)
    if cod and not cod_rf:
        _log("   ⚠️  não achei o código da Receita para o IBGE %s — a"
             % cod)
        _log("      conferência de município do CNPJ fica sem base.")

    placar = Counter()
    for poi, d in saidas:
        notas, achou = gravar(con, cur, poi, d, cod_rf)
        placar["achou_algo" if achou else "esgotado"] += 1
        for k in achou:
            placar["campo_" + k] += 1
        _log("      %-32s %s" % (poi["nome"][:32], ", ".join(achou) or "esgotado"))
        for n in notas:
            _log("         %s" % n)

    nao_voltaram = len(lista) - len(saidas)
    if nao_voltaram:
        _log("   %d não voltaram (CAPTCHA ou guia encerrada) — ficam para a"
             % nao_voltaram)
        _log("   próxima rodada, sem marca de esgotado.")
    _log("\n   %d POIs · %.1f min · %.1f s por POI"
         % (len(saidas), dt / 60.0, dt / max(1, len(saidas))))
    for k, v in placar.most_common():
        _log("      %-18s %5d" % (k, v))
    con.close()
    return {"alvos": len(lista), **dict(placar)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Enriquece pelo Google, em janela quente, sem IA no meio.")
    p.add_argument("--cidade", default="")
    p.add_argument("--municipio", default="",
                   help="código IBGE — confere o CNPJ contra a Receita local")
    p.add_argument("--fonte", action="append", default=[])
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--guias", type=int, default=4,
                   help="guias na mesma janela; a janela é o caro, a guia não")
    p.add_argument("--nome-minimo", type=int, default=12)
    p.add_argument("--janelas", type=int, default=4,
                   help="janelas simultâneas, uma por IP")
    p.add_argument("--sem-proxy", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    _log("▶ Google, janela quente%s" % ((" · %s" % a.cidade) if a.cidade else ""))
    rodar(a.cidade, a.municipio, a.limite, a.fonte, a.nome_minimo, a.guias,
          a.sem_proxy, aplicar=a.aplicar, janelas=a.janelas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
