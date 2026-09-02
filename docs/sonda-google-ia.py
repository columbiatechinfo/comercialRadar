# -*- coding: utf-8 -*-
"""Google, sonda 9 — a coordenada entra pelo Nominatim, nao pela caixa de busca.

O QUE A SONDA 8 MEDIU, uma variavel por vez, mesmo alvo, um IP para cada:

    A  curto, SEM coordenada   373 chars   68 folhas   8,5 s   passa
    B  curto, COM coordenada   290 chars    0 folhas    39 s   nao passa
    C  longo, SEM coordenada   528 chars   68 folhas   9,1 s   passa
    D  longo, COM coordenada   483 chars    0 folhas    40 s   nao passa

E A COORDENADA, e nao o tamanho: o prompt longo sem ela funciona igual ao curto.
Um par lat/lng na caixa de busca faz o Google classificar a consulta como
geografica e nao gerar a Visao Geral. Mandar a coordenada crua desliga
exatamente o que se queria usar.

A intencao do usuario continua valendo, e o caminho e outro: a coordenada entra
pelo NOMINATIM, que ja roda no i9 (127.0.0.1:7200), e o que vai para o Google e
o ENDERECO que ela resolve. Assim:

    coordenada -> Nominatim reverso -> "Rua Sao Nicolau, 95, Estancia Velha"
    esse texto -> Google -> CNPJ, razao social, telefone, redes

E o endereco do reverso vai junto na pergunta para ser CONFERIDO, que era o
outro pedido: se a IA conhecer outro endereco para o estabelecimento, ela diz os
dois, e a divergencia fica registrada em vez de escondida.
"""
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")

COOKIE = "/app/estado/cookie_maps.json"
NOMINATIM = "http://127.0.0.1:7200"
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]

ALVOS = [
    {"nome": "Ferragem Santiago", "lat": -29.921423, "lng": -51.136055},
    {"nome": "Habib's", "lat": -29.9179, "lng": -51.1839},
    # so coordenada, como o Airbnb entrega
    {"nome": "Loft Maxplaza", "lat": -29.9238, "lng": -51.1768},
]


def reverso(lat, lng):
    """Coordenada -> endereco, pelo Nominatim que ja roda no servidor."""
    url = ("%s/reverse?format=jsonv2&lat=%s&lon=%s&zoom=18&addressdetails=1"
           % (NOMINATIM, lat, lng))
    try:
        with urllib.request.urlopen(url, timeout=20) as h:
            d = json.load(h)
    except Exception as e:
        return {"erro": "%s" % type(e).__name__}
    a = d.get("address") or {}
    via = a.get("road") or a.get("pedestrian") or a.get("residential")
    return {
        "via": via,
        "numero": a.get("house_number"),
        "bairro": a.get("suburb") or a.get("neighbourhood") or a.get("city_district"),
        "cidade": a.get("city") or a.get("town") or a.get("municipality"),
        "uf": a.get("state"),
        "cep": a.get("postcode"),
        "linha": d.get("display_name"),
    }


def montar(nome, rev, uf="RS"):
    """Do reverso vao para o prompt SO CEP, BAIRRO e CIDADE. A rua fica de fora.

    A primeira versao mandava a rua e o numero do Nominatim, e isso PIOROU: para
    o Habib's o reverso devolveu "Colégio Maria Auxiliadora, 5888, Avenida
    Guilherme Schell" — o vizinho — e a IA respondeu o endereco do colegio. Para
    a Ferragem Santiago devolveu "Rua PQ12", a mesma rua fantasma que ja consta
    nas armadilhas deste projeto.

    CEP, bairro e cidade sobrevivem aos ~11 m de erro da coordenada do Airbnb; a
    rua e o numero, nao. Os tres situam a busca sem plantar endereco falso — e
    quem tem de dizer a rua e a fonte, nao eu.

    O endereco reverso INTEIRO continua sendo guardado, com a origem escrita:
    ele e util para conferir depois, e a unica coisa que nao se pode e deixa-lo
    passar por endereco apurado.
    """
    onde = ", ".join(x for x in (rev.get("bairro"), rev.get("cidade"), uf) if x)
    cep = rev.get("cep")
    local = ("%s, CEP %s" % (onde, cep)) if cep else onde
    return (
        "%s, em %s: qual o CNPJ e a razão social DESTA unidade, o telefone "
        "dela, o endereço completo com rua e número, e os perfis oficiais "
        "DESTA unidade nas redes sociais — Instagram, Facebook, LinkedIn, "
        "TikTok, YouTube e WhatsApp — além do site? "
        "Responda item a item. Se algum não estiver publicado, escreva "
        "'não encontrado' nesse item — não deduza, não estime, não preencha "
        "por semelhança e não invente. "
        "Não use o CNPJ da rede, da franqueadora, da matriz nem de outra "
        "unidade: se só existir o da rede, diga que é da rede. "
        "Não use perfil da rede nacional se a unidade tiver o seu."
        % (nome, local)
    )


BLOCO = r"""() => {
  const limpa = (raiz) => {
    const p = [];
    const anda = (n) => {
      if (!n) return;
      const tag = (n.tagName || '').toLowerCase();
      if (tag === 'style' || tag === 'script' || tag === 'noscript') return;
      if (n.children.length === 0) { const t = (n.textContent||'').trim(); if (t) p.push(t); return; }
      for (const f of n.children) anda(f);
    };
    anda(raiz);
    return p;
  };
  let marca = null;
  for (const e of document.querySelectorAll('div, span, h1, h2')) {
    const t = (e.textContent || '').trim();
    if (t.length < 80 && /AI Mode reply|Visão geral criada por IA/.test(t)) { marca = e; break; }
  }
  if (!marca) return null;
  let no = marca, alvo = null;
  for (let i = 0; i < 9 && no; i++) {
    if (limpa(no).join(' ').length > 250) { alvo = no; break; }
    no = no.parentElement;
  }
  return alvo ? limpa(alvo) : null;
}"""

CAMPOS = {
    "cnpj":         (r"CNPJ", re.compile(r"\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2}")),
    "razao_social": (r"Raz[ãa]o\s+Social", re.compile(r"(?i)(ltda|s\.?a\.?\b|eireli|\bme\b|epp|mei|comerc|com[ée]rc|servi|ind[úu]str)")),
    "telefone":     (r"Telefone|Contato", re.compile(r"\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}")),
    "endereco":     (r"Endere[çc]o", re.compile(r"(?i)\b(rua|r\.|av\.?|avenida|rod\.?|estrada|travessa|praça)\b")),
    "cep":          (r"CEP", re.compile(r"\d{5}-?\d{3}")),
    "instagram":    (r"Instagram", re.compile(r"(?i)(instagram\.com|@\w{3,})")),
    "facebook":     (r"Facebook", re.compile(r"(?i)(facebook\.com|fb\.com)")),
    "linkedin":     (r"LinkedIn", re.compile(r"(?i)linkedin\.com")),
    "tiktok":       (r"TikTok", re.compile(r"(?i)(tiktok\.com|@\w{3,})")),
    "youtube":      (r"YouTube", re.compile(r"(?i)(youtube\.com|youtu\.be)")),
    "whatsapp":     (r"WhatsApp", re.compile(r"(?i)(wa\.me|whatsapp|\(?\d{2}\)?\s?9\d{4}[-\s]?\d{4})")),
    "site":         (r"Site|Website|P[áa]gina oficial", re.compile(r"(?i)(https?://|www\.|\.com|\.br)")),
}
VAZIO = re.compile(r"^(n[aã]o (encontrad|informad|publicad|localizad|divulgad)\w*|"
                   r"n/a|-|—)\.?$", re.I)


def sem_eco(folhas, pergunta):
    """Tira as folhas que sao o PROPRIO PROMPT ecoado na pagina.

    O Google repete a consulta dentro do bloco ("AI Mode reply for <pergunta>"),
    e o parser lia esse eco como resposta: `razao_social` veio com "DESTA
    unidade, o telefone dela, o endereço completo..." — um pedaco da minha
    propria pergunta, devolvido como se fosse dado da empresa.
    """
    pedacos = {p.strip().lower() for p in pergunta.split(".") if len(p.strip()) > 25}
    limpas = []
    for f in folhas or []:
        t = (f or "").strip().lower()
        if len(t) > 25 and any(t in p or p in t for p in pedacos):
            continue
        limpas.append(f)
    return limpas


def ler(folhas):
    """Rotulo anuncia, formato prova. O que nao prova fica vazio."""
    saida = {c: None for c in CAMPOS}
    saida["_ausentes"], saida["_recusados"] = [], []
    for i, f in enumerate(folhas or []):
        for campo, (re_rot, re_val) in CAMPOS.items():
            if saida[campo] is not None or not re.search(re_rot, f, re.I):
                continue
            resto = re.split(re_rot, f, maxsplit=1, flags=re.I)
            cand = [resto[-1]] if len(resto) > 1 and resto[-1].strip() else []
            cand += [folhas[j] for j in range(i + 1, min(i + 7, len(folhas)))]
            for v in cand:
                v = " ".join((v or "").split()).lstrip(":=-— ").strip()
                if not v:
                    continue
                if VAZIO.match(v):
                    saida["_ausentes"].append(campo)
                    break
                if re_val.search(v):
                    saida[campo] = v[:180]
                    break
                if any(re.search(r, v, re.I) for r, _ in CAMPOS.values()):
                    saida["_recusados"].append((campo, v[:40]))
                    break
    return saida


def buscar(pw, proxy, pergunta):
    nav = pw.chromium.launch(headless=False, args=ARGS, proxy=proxy)
    try:
        ctx = nav.new_context(viewport={"width": 1360, "height": 1000},
                              locale="pt-BR", timezone_id="America/Sao_Paulo",
                              storage_state=COOKIE if os.path.exists(COOKIE) else None)
        pg = ctx.new_page()
        t0 = time.time()
        pg.goto("https://www.google.com/search?q=%s&hl=pt-BR"
                % urllib.parse.quote_plus(pergunta),
                timeout=90000, wait_until="domcontentloaded")
        pg.wait_for_timeout(random.randint(4000, 6000))
        for t in ("Aceitar tudo", "Accept all", "Concordo"):
            try:
                b = pg.get_by_role("button", name=t)
                if b.count():
                    b.first.click(timeout=4000)
                    pg.wait_for_timeout(3000)
                    break
            except Exception:
                pass
        folhas, anterior = None, -1
        limite = time.time() + 35
        while time.time() < limite:
            folhas = pg.evaluate(BLOCO)
            n = len(folhas or [])
            if n and n == anterior:
                break
            anterior = n
            pg.wait_for_timeout(3000)
        r = ler(sem_eco(folhas, pergunta))
        r["folhas"] = len(folhas or [])
        r["segundos"] = round(time.time() - t0, 1)
        r["bloco"] = " | ".join(folhas or [])[:500]
        ctx.close()
        return r
    finally:
        nav.close()


from proxy_pool import ProxyPool  # noqa: E402
import asyncio  # noqa: E402

pool = ProxyPool(pais="BR")
pool.start()


async def pegar(n):
    return [await pool.acquire() for _ in range(n)]


PROXIES = [{"server": p["server"], "username": p.get("username"),
            "password": p.get("password")}
           for p in asyncio.run(pegar(len(ALVOS))) if p]
print("  %d IPs\n" % len(PROXIES))

from playwright.sync_api import sync_playwright  # noqa: E402

saidas = []
with sync_playwright() as pw:
    for i, alvo in enumerate(ALVOS):
        rev = reverso(alvo["lat"], alvo["lng"])
        # O reverso INTEIRO fica guardado, com a origem escrita — mas só CEP,
        # bairro e cidade vão para a pergunta. Ver `montar`.
        rev["origem"] = "nominatim_reverso"
        rev["origem_coordenada"] = "%s,%s" % (alvo["lat"], alvo["lng"])
        pergunta = montar(alvo["nome"], rev)
        print("  == %s (%s, %s) ==" % (alvo["nome"], alvo["lat"], alvo["lng"]))
        print("     reverso inteiro (guardado): %s"
              % (rev.get("linha") or rev.get("erro"))[:100])
        print("     ao prompt vão só: bairro=%r cidade=%r cep=%r"
              % (rev.get("bairro"), rev.get("cidade"), rev.get("cep")))
        try:
            r = buscar(pw, PROXIES[i % len(PROXIES)], pergunta)
        except Exception as e:
            print("     ERRO: %s\n" % type(e).__name__)
            continue
        r["alvo"] = alvo["nome"]
        r["reverso"] = rev
        saidas.append(r)
        for c in ("endereco", "cep", "cnpj", "razao_social", "telefone",
                  "instagram", "facebook", "linkedin", "tiktok", "youtube",
                  "whatsapp", "site"):
            print("     %-14s %s" % (c, r.get(c)))
        print("     a IA declarou ausência em: %s" % (r.get("_ausentes") or "nada"))
        print("     %d folhas · %ss\n" % (r["folhas"], r["segundos"]))

with open("/sondas/google_ia9.json", "w", encoding="utf-8") as f:
    json.dump(saidas, f, ensure_ascii=False, indent=1)
print("  gravado /sondas/google_ia9.json")
