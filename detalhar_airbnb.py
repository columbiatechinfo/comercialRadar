# -*- coding: utf-8 -*-
"""A ficha de cada hospedagem do Airbnb — e o print que vira endereço.

A DIVISÃO DE TRABALHO

    descobrir     `extrair_airbnb.py`   caixa delimitadora da área, coordenada
    detalhar      este módulo           a ficha inteira, uma sessão por anúncio
    endereçar     a IA, depois          o print vai ao assistente

SÓ QUEM ESTÁ DENTRO DO DESENHO É DETALHADO. A descoberta grava tudo que a caixa
trouxer — a caixa é retângulo e o polígono não é — mas abrir ficha custa uma
sessão de navegador por anúncio, e isso é a parte cara. `na_area` decide.

O DADO ESTÁ EMBUTIDO. A ficha vem renderizada com o estado num
`<script data-deferred-state-0>`, e dali saem título, tipo, comodidades, regras,
fotos, destaques, descrição, anfitrião, coanfitriões e a coordenada. Não se
raspa DOM: seletor de classe do Airbnb muda a cada deploy, caminho de payload
não.

DUAS COISAS NÃO ESTÃO NO PAYLOAD, e cada uma exige um jeito:

  avaliações   a seção existe mas vem vazia; os textos carregam sob demanda.
               Pede-se pela rota própria, /rooms/<id>/reviews
  capacidade   hóspedes/quartos/camas/banheiros não têm campo; vêm formatados
               em `sharingConfig.ugcTitle` ("Loft · Canoas · 1 quarto · 1 cama
               · 1 banheiro"). Lê-se de lá, e NÃO do texto da página:
               `body.textContent` concatena sem separador e "9"+"347" vira
               "9347" — foi assim que apareceram "11 camas" e "96158
               avaliações", números plausíveis e falsos

O PRINT DA FICHA é gravado de propósito. O Airbnb arredonda a coordenada em 92%
dos anúncios e não publica endereço; mandando a captura ao assistente, ela vira
"Avenida Getúlio Vargas, 4831" com CEP e condomínio. É a única ponte medida
entre a hospedagem e um endereço com número.

Uso:
    python detalhar_airbnb.py                      # pendentes dentro da área
    python detalhar_airbnb.py --area minha_area
    python detalhar_airbnb.py --limite 5 --simular
    python detalhar_airbnb.py --incluir-fora       # também os de fora do desenho
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

from psycopg2.extras import execute_values

import base_comum as bc

PRINTS = "/app/capturas/airbnb"

PENDENTES = """
select anuncio_id
  from airbnb_anuncio
 where coalesce(estado_detalhe, 'PENDENTE') <> 'OK'
   and (%(fora)s or na_area)
   and (%(area)s is null or area_ref = %(area)s)
 order by visto_em desc nulls last
"""

GRAVAR = """
update airbnb_anuncio a set
  nome         = coalesce(v.nome,        a.nome),
  tipo_resumo  = coalesce(v.tipo_resumo, a.tipo_resumo),
  bairro       = coalesce(v.bairro,      a.bairro),
  cidade       = coalesce(v.cidade,      a.cidade),
  uf           = coalesce(v.uf,          a.uf),
  lat          = coalesce(v.lat,         a.lat),
  lng          = coalesce(v.lng,         a.lng),
  hospedes     = coalesce(v.hospedes,    a.hospedes),
  quartos      = coalesce(v.quartos,     a.quartos),
  camas        = coalesce(v.camas,       a.camas),
  banheiros    = coalesce(v.banheiros,   a.banheiros),
  nota         = coalesce(v.nota,        a.nota),
  avaliacoes_qtd = coalesce(v.avaliacoes_qtd, a.avaliacoes_qtd),
  anfitriao    = coalesce(v.anfitriao,   a.anfitriao),
  anfitriao_taxa_resposta = coalesce(v.taxa, a.anfitriao_taxa_resposta),
  coanfitrioes = coalesce(v.coanfitrioes, a.coanfitrioes),
  descricao    = coalesce(v.descricao,   a.descricao),
  preco_total  = coalesce(v.preco_total, a.preco_total),
  comodidades  = coalesce(v.comodidades, a.comodidades),
  regras       = coalesce(v.regras,      a.regras),
  destaques    = coalesce(v.destaques,   a.destaques),
  fotos        = coalesce(v.fotos,       a.fotos),
  avaliacoes   = coalesce(v.avaliacoes,  a.avaliacoes),
  print_ficha  = coalesce(v.print_ficha, a.print_ficha),
  estado_detalhe = 'OK',
  visto_em     = now()
from (values %s) as v(anuncio_id, nome, tipo_resumo, bairro, cidade, uf,
                      lat, lng, hospedes, quartos, camas, banheiros, nota,
                      avaliacoes_qtd, anfitriao, taxa, coanfitrioes, descricao,
                      preco_total, comodidades, regras, destaques, fotos,
                      avaliacoes, print_ficha)
where a.anuncio_id = v.anuncio_id
"""

MARCAR = """
update airbnb_anuncio set estado_detalhe = 'SEM_RETORNO', visto_em = now()
 where anuncio_id = any(%s)
"""

# ------------------------------------------------------------------ payload --
DA_FICHA = r"""() => {
  let node = null, porId = {};
  for (const s of document.querySelectorAll('script[id^="data-deferred-state"]')) {
    let d; try { d = JSON.parse(s.textContent || ''); } catch (e) { continue; }
    const cacar = (no, prof) => {
      if (!no || typeof no !== 'object' || prof > 14) return;
      if (Array.isArray(no)) { for (const x of no.slice(0, 30)) cacar(x, prof + 1); return; }
      if (!node && no.pdpPresentation) node = no;
      if (Array.isArray(no.sections))
        for (const sec of no.sections) if (sec && sec.sectionId)
          porId[sec.sectionId] = sec.section || sec;
      for (const k of Object.keys(no)) cacar(no[k], prof + 1);
    };
    cacar(d, 0);
  }
  if (!node) return {erro: 'sem payload'};
  const pp = node.pdpPresentation || {};
  const t = (v) => {
    if (v == null) return null;
    if (typeof v === 'string') return v;
    if (typeof v !== 'object') return String(v);
    return v.localizedStringWithTranslationPreference || v.localizedContent
        || v.text || v.title || (v.content ? t(v.content) : null);
  };
  const coord = (node.location && node.location.coordinate)
             || (pp.location && pp.location.coordinate) || {};
  const comod = [];
  for (const g of ((pp.amenities || {}).seeAllAmenitiesGroups || []))
    for (const a of (g.amenities || []))
      if (a && a.title && a.available !== false) comod.push(a.title);
  const regras = [];
  for (const g of ((pp.rules || {}).groupItems || []))
    for (const i of (g.items || [])) if (i && i.title) regras.push(i.title);
  const fotos = new Set();
  const bf = (no, prof) => {
    if (!no || typeof no !== 'object' || prof > 10 || fotos.size > 60) return;
    if (Array.isArray(no)) { for (const x of no) bf(x, prof + 1); return; }
    if (typeof no.baseUrl === 'string') fotos.add(no.baseUrl);
    for (const k of Object.keys(no)) bf(no[k], prof + 1);
  };
  bf(porId.HERO_DEFAULT, 0); bf(porId.PHOTO_TOUR_SCROLLABLE_MODAL, 0);

  // As frases curtas do payload: e delas que saem capacidade e n. de avaliacoes,
  // NUNCA do texto da pagina.
  const frases = new Set();
  const varrer = (no, prof) => {
    if (prof > 16 || no == null || frases.size > 500) return;
    if (typeof no === 'string') {
      const s = no.trim();
      if (s.length > 3 && s.length < 90) frases.add(s);
      return;
    }
    if (Array.isArray(no)) { for (const x of no.slice(0, 40)) varrer(x, prof + 1); return; }
    if (typeof no !== 'object') return;
    for (const k of Object.keys(no)) varrer(no[k], prof + 1);
  };
  varrer(node, 0);

  return {
    titulo: t(node.description && node.description.name),
    tipo_resumo: t(pp.overview && pp.overview.title),
    resumo_curto: t(pp.sharingConfig && pp.sharingConfig.ugcTitle),
    localidade: t(pp.localizedLocation),
    local_subtitulo: t(pp.location && pp.location.subtitle),
    lat: coord.latitude, lng: coord.longitude,
    descricao: t((pp.descriptions || {}).longDescriptionHtml),
    destaques: (pp.highlights || []).map(h => t(h.headline)).filter(Boolean),
    comodidades: comod, regras: regras, fotos: [...fotos],
    anfitriao: ((pp.hostInfo || {}).passportData || {}).name || null,
    taxa_resposta: (pp.hostInfo || {}).responseRateText || null,
    coanfitrioes: (node.cohosts || []).map(c => c.displayFirstName),
    frases: [...frases],
  };
}"""

PRECO = r"""() => {
  // So os nos-folha: percorrer o body inteiro concatena elementos e inventa
  // numeros. "Total" e o rotulo; o valor esta ao lado dele.
  const folhas = [];
  const anda = (e) => {
    if (!e) return;
    if (e.children.length === 0) {
      const t = (e.textContent || '').replace(/ /g, ' ').trim();
      if (t && t.length < 60) folhas.push(t);
      return;
    }
    for (const f of e.children) anda(f);
  };
  anda(document.body);
  for (let i = 0; i < folhas.length; i++) {
    if (!/^Total/i.test(folhas[i])) continue;
    for (let j = i; j < Math.min(i + 4, folhas.length); j++) {
      const m = folhas[j].match(/R\$\s?([\d.]+)/);
      if (m) return 'R$ ' + m[1];
    }
  }
  return null;
}"""


def numeros(resumo, frases):
    """De 'Loft · Canoas · 1 quarto · 1 cama · 1 banheiro' aos campos."""
    def n(texto, re_):
        m = re.search(re_, texto or "", re.I)
        return int(m.group(1)) if m else None

    hosp = None
    for f in sorted([x for x in (frases or []) if re.search(r"\d+\s*hóspede", x, re.I)],
                    key=len):
        hosp = n(f, r"(\d+)\s*hóspede")
        break
    qtd, nota = None, None
    for f in sorted([x for x in (frases or []) if re.search(r"\d+\s*avaliaç", x, re.I)],
                    key=len):
        qtd = n(f, r"(\d+)\s*avaliaç")
        break
    m = re.search(r"★\s*([\d,\.]+)", resumo or "")
    if m:
        try:
            nota = float(m.group(1).replace(",", "."))
        except ValueError:
            nota = None
    return {
        "hospedes": hosp,
        "quartos": n(resumo, r"(\d+)\s*quarto"),
        # `camas?` — sem o plural, "3 camas" não casa
        "camas": n(resumo, r"(\d+)\s*camas?\b"),
        "banheiros": n(resumo, r"(\d+)\s*banheiro"),
        "avaliacoes_qtd": qtd,
        "nota": nota,
    }


def avaliacoes_de(corpos):
    achadas, vistos = [], set()

    def varrer(no, prof=0):
        if prof > 14 or no is None or len(achadas) > 120:
            return
        if isinstance(no, list):
            for x in no[:120]:
                varrer(x, prof + 1)
            return
        if not isinstance(no, dict):
            return
        if isinstance(no.get("comments"), str) and no["comments"].strip():
            rev = no.get("reviewer") or {}
            chave = no["comments"][:70]
            if chave not in vistos:
                vistos.add(chave)
                achadas.append({"texto": no["comments"],
                                "autor": rev.get("firstName") or rev.get("smartName"),
                                "data": no.get("localizedDate"),
                                "nota": no.get("rating")})
            return
        for v in no.values():
            varrer(v, prof + 1)

    for c in corpos:
        varrer(c)
    return achadas


def uma_ficha(sessao, anuncio_id, com_print=True):
    corpos = []
    saida = {"anuncio_id": anuncio_id}

    def ouvir(resp):
        try:
            if ("/api/" in resp.url or "graphql" in resp.url.lower()) \
                    and resp.status == 200:
                corpos.append(resp.json())
        except Exception:
            pass

    def acao(page):
        page.on("response", ouvir)
        page.wait_for_timeout(7000)
        saida.update(page.evaluate(DA_FICHA) or {})
        saida.update(numeros(saida.get("resumo_curto"), saida.get("frases")))
        saida["preco_total"] = page.evaluate(PRECO)
        if com_print:
            # A CAPTURA DA FICHA. É ela que vai ao assistente quando só existe
            # coordenada aproximada — e foi assim que um anúncio sem endereço
            # virou "Avenida Getúlio Vargas, 4831".
            os.makedirs(PRINTS, exist_ok=True)
            caminho = os.path.join(PRINTS, "%s.png" % anuncio_id)
            try:
                page.screenshot(path=caminho, full_page=False)
                saida["print_ficha"] = caminho
            except Exception:
                saida["print_ficha"] = None

    # TETO CURTO DE PROPOSITO — ver a nota em `extrair_airbnb`: teto longo
    # nao espera pagina lenta, espera navegador morto. Sete minutos de
    # silencio com zero conexao foi o que aconteceu em 03/09/2026.
    sessao.fetch("https://www.airbnb.com.br/rooms/%s" % anuncio_id,
                 page_action=acao, timeout=120000)

    # As avaliações têm rota própria; caçar o botão que as abre erra o alvo em
    # fichas diferentes.
    def acao_rev(page):
        page.on("response", ouvir)
        page.wait_for_timeout(8000)
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(1600)

    try:
        sessao.fetch("https://www.airbnb.com.br/rooms/%s/reviews" % anuncio_id,
                     page_action=acao_rev, timeout=120000)
    except Exception:
        pass
    saida["avaliacoes"] = avaliacoes_de(corpos)
    return saida


def linha_para_banco(f):
    local = f.get("local_subtitulo") or ""      # "Canoas, Rio Grande do Sul, Brasil"
    partes = [p.strip() for p in local.split(",")]
    cidade = partes[0] if partes else None
    uf = "RS" if len(partes) > 1 and "Rio Grande do Sul" in partes[1] else None
    return (
        f["anuncio_id"], f.get("titulo"), f.get("tipo_resumo"),
        f.get("localidade"), cidade, uf,
        f.get("lat"), f.get("lng"),
        f.get("hospedes"), f.get("quartos"), f.get("camas"), f.get("banheiros"),
        f.get("nota"), f.get("avaliacoes_qtd"),
        f.get("anfitriao"), f.get("taxa_resposta"),
        f.get("coanfitrioes") or None,
        f.get("descricao"), f.get("preco_total"),
        json.dumps(f.get("comodidades") or [], ensure_ascii=False),
        json.dumps(f.get("regras") or [], ensure_ascii=False),
        json.dumps(f.get("destaques") or [], ensure_ascii=False),
        json.dumps(f.get("fotos") or [], ensure_ascii=False),
        json.dumps(f.get("avaliacoes") or [], ensure_ascii=False),
        f.get("print_ficha"),
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--area", default=None, help="recorta por area_ref")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--incluir-fora", dest="fora", action="store_true",
                   help="detalha também o que caiu fora do desenho")
    p.add_argument("--sem-print", dest="sem_print", action="store_true")
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--simular", action="store_true")
    p.add_argument("--trabalhadores", type=int, default=6,
                   help="lotes em paralelo; cada um tem a sua sessao e o seu IP")
    a = p.parse_args()

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(PENDENTES, {"fora": a.fora, "area": a.area})
        ids = [r[0] for r in k.fetchall()]
    if a.limite:
        ids = ids[:a.limite]

    print("⟦fase⟧ airbnb-detalhe", flush=True)
    print("%d anúncios pendentes%s%s"
          % (len(ids), " (área %s)" % a.area if a.area else "",
             "" if a.fora else " — só os dentro do desenho"), flush=True)
    if not ids:
        print("nada a detalhar.", flush=True)
        con.close()
        return 0

    proximo = _rodizio(a.sem_proxy)
    from scrapling.fetchers import StealthySession

    # EM LOTES, EM PARALELO, E COM A SESSAO REAPROVEITADA DENTRO DO LOTE.
    #
    # Isto era um laco sequencial que abria uma `StealthySession` NOVA por
    # anuncio: subir o Camoufox, atravessar o Cloudflare, buscar a ficha,
    # buscar as avaliacoes, e derrubar tudo. Medido em 03/09/2026, ~30 s por
    # anuncio, dos quais boa parte era a sessao — e um de cada vez.
    #
    # A regra de lote e a MESMA que `search_from_sheet` documenta e que a etapa
    # 4 segue: 8 a 15 por sessao, UM IP POR LOTE. Ela nao e burocracia, e o que
    # mantem o ritmo parecido com o de gente: trocar de IP a cada pagina chama
    # mais atencao do que ficar um tempo com o mesmo, e reaproveitar a sessao
    # dentro do lote paga o Cloudflare uma vez em vez de quinze.
    #
    # O paralelismo e seguro justamente por causa dessa regra: cada trabalhador
    # tem o SEU lote e o SEU IP, entao subir trabalhador nao aumenta a pressao
    # sobre nenhum IP — so usa mais IPs ao mesmo tempo.
    #
    # `ThreadPoolExecutor` e nao `asyncio` porque `uma_ficha` e sincrona: o
    # `sessao.fetch` bloqueia, e thread e exatamente o que serve para isso.
    import concurrent.futures
    import random
    import threading

    LOTE_MIN, LOTE_MAX = 8, 15
    lotes, resto = [], list(ids)
    while resto:
        n = min(random.randint(LOTE_MIN, LOTE_MAX), len(resto))
        lotes.append(resto[:n])
        resto = resto[n:]

    fichas, mortos = [], []
    t0 = time.time()
    trava = threading.Lock()
    feitos = [0]

    def um_lote(lote):
        """Um IP, uma sessao, os 8 a 15 anuncios do lote."""
        try:
            with StealthySession(headless=True, solve_cloudflare=True,
                                 wait_selector="h1", wait_selector_state="attached",
                                 proxy=proximo(), locale="pt-BR",
                                 timezone_id="America/Sao_Paulo") as ses:
                for anuncio_id in lote:
                    try:
                        f = uma_ficha(ses, anuncio_id, com_print=not a.sem_print)
                    except Exception as e:                     # noqa: BLE001
                        with trava:
                            print("  %-16s %s: %s"
                                  % (anuncio_id, type(e).__name__, str(e)[:60]),
                                  flush=True)
                        continue
                    with trava:
                        feitos[0] += 1
                        if f.get("erro") or not f.get("titulo"):
                            mortos.append(anuncio_id)
                            print("  %-16s sem payload — SEM_RETORNO"
                                  % anuncio_id, flush=True)
                        else:
                            fichas.append(f)
                            print("  %-16s %-40s %d comodidades · %d fotos · "
                                  "%d avaliações%s"
                                  % (anuncio_id, str(f.get("titulo"))[:40],
                                     len(f.get("comodidades") or []),
                                     len(f.get("fotos") or []),
                                     len(f.get("avaliacoes") or []),
                                     " · print" if f.get("print_ficha") else ""),
                                  flush=True)
                        if feitos[0] % 20 == 0:
                            print("    %d de %d · %.1f min"
                                  % (feitos[0], len(ids),
                                     (time.time() - t0) / 60), flush=True)
        except Exception as e:                                 # noqa: BLE001
            # LOTE INTEIRO PERDIDO E UM SO AVISO, e nao quinze iguais: quando a
            # sessao nem sobe, o motivo e um — o IP, ou o Cloudflare.
            with trava:
                print("  lote de %d perdido na sessao — %s: %s"
                      % (len(lote), type(e).__name__, str(e)[:60]), flush=True)

    print("  %d lotes de %d a %d anuncios · %d em paralelo · um IP por lote"
          % (len(lotes), LOTE_MIN, LOTE_MAX, a.trabalhadores), flush=True)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, a.trabalhadores)) as piscina:
        list(piscina.map(um_lote, lotes))

    print("%s%d detalhados · %d sem retorno · %.1f min"
          % (chr(10), len(fichas), len(mortos), (time.time() - t0) / 60), flush=True)

    if a.simular:
        for f in fichas[:5]:
            print("   %s" % json.dumps(
                {k: v for k, v in f.items()
                 if k in ("titulo", "lat", "lng", "hospedes", "quartos", "camas",
                          "banheiros", "nota", "avaliacoes_qtd", "anfitriao")},
                ensure_ascii=False)[:200], flush=True)
        print("(simulação — nada gravado)", flush=True)
        con.close()
        return 0

    with con.cursor() as k:
        if fichas:
            execute_values(k, GRAVAR, [linha_para_banco(f) for f in fichas],
                           page_size=200,
                           template="(%s,%s,%s,%s,%s,%s,%s::float8,%s::float8,"
                                    "%s::int,%s::int,%s::int,%s::int,%s::numeric,"
                                    "%s::int,%s,%s,%s::text[],%s,%s,%s::jsonb,"
                                    "%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)")
        if mortos:
            k.execute(MARCAR, (mortos,))
        k.execute("""select estado_detalhe, count(*) from airbnb_anuncio
                      where na_area group by 1""")
        print("gravados %d · dentro da área: %s"
              % (len(fichas), dict(k.fetchall())), flush=True)
    con.commit()
    con.close()
    return 0


def _rodizio(sem_proxy):
    if sem_proxy:
        return lambda: None
    try:
        from proxy_pool import ProxyPool
    except Exception as e:                                     # noqa: BLE001
        print("  ⚠️  pool indisponível (%s) — IP direto" % type(e).__name__,
              flush=True)
        return lambda: None
    pool = ProxyPool(pais="BR")
    pool.start()

    async def pegar(n):
        return [await pool.acquire() for _ in range(n)]

    urls = []
    for px in asyncio.run(pegar(24)):
        if not px:
            continue
        cfg = ProxyPool.to_playwright(px)
        servidor = str(cfg.get("server") or "").replace("http://", "")
        if cfg.get("username"):
            urls.append("http://%s:%s@%s" % (cfg["username"],
                                             cfg.get("password") or "", servidor))
        elif servidor:
            urls.append("http://%s" % servidor)
    if not urls:
        return lambda: None
    estado = {"i": -1}

    def proximo():
        estado["i"] = (estado["i"] + 1) % len(urls)
        return urls[estado["i"]]
    return proximo


if __name__ == "__main__":
    sys.exit(main())
