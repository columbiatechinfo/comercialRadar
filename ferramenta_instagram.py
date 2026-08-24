# -*- coding: utf-8 -*-
"""Lê o perfil público de um estabelecimento no Instagram.

POR QUE INSTAGRAM E NÃO OS AGREGADORES DE CNPJ

Testei Econodata, Serasa e CNPJCheck pela sessão stealth: os três recusaram a
navegação (redirecionamento em laço, conexão negada). Mas o ponto maior é que
eles não são necessários — a base da Receita está no i9, com 72 milhões de
estabelecimentos. Buscar CNPJ neles é consultar quem copiou a fonte que já
temos.

O Instagram entrega o que a Receita NÃO tem, e que decide se um ponto está vivo:

    horário de funcionamento     que a Receita nunca soube
    bio e telefone de contato    escritos pelo dono, atualizados
    seguidores                   tamanho e movimento do negócio
    links                        iFood, WhatsApp, cardápio — com o slug junto

Medido no `xerifeburger_the`: 1.565 seguidores, "De 18h às 23:30", e o link do
iFood com o UUID da loja dentro.

LIMITE: perfil privado devolve só o cabeçalho, e isso é dito em vez de
devolvido como vazio.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import tempfile

MAX_SEGUNDOS = 120

# O rodapé do Instagram é sempre o mesmo e ocupa mais que a bio. Cortar ali
# evita mandar "Meta, Sobre, Blog, Carreiras…" para o modelo como se fosse
# conteúdo do perfil.
_FIM = re.compile(r"\bMostrar mais posts\b|\bMeta\s*\|?\s*Sobre\b|"
                  r"\bPublicações\s*Sobre\s*Blog\b")
_SEGUIDORES = re.compile(r"([\d.,]+)\s*(mil|mi|m)?\s*seguidores", re.I)
_SEGUINDO = re.compile(r"([\d.,]+)\s*seguindo", re.I)
_TEL = re.compile(r"(?:\(?\d{2}\)?\s?)?9?\d{4}[-.\s]?\d{4}")
_URL_TEXTO = re.compile(r"(?:https?://)?(?:www\.)?[\w.-]+\.[a-z]{2,}/[^\s|]+",
                        re.I)
_HORA = re.compile(r"\d{1,2}[h:]\d{0,2}\s*(?:às|as|a|-|até)\s*\d{1,2}[h:]\d{0,2}",
                   re.I)


def _numero(txt: str, sufixo: str | None) -> int | None:
    """"1.565" -> 1565 · "12,3 mil" -> 12300 — o Instagram abrevia acima de mil."""
    if not txt:
        return None
    try:
        n = float(txt.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    if sufixo and sufixo.lower() == "mil":
        n *= 1_000
    elif sufixo and sufixo.lower() in ("mi", "m"):
        n *= 1_000_000
    return int(n)


async def _uma(usuario: str, usar_proxy: bool) -> dict:
    from playwright.async_api import async_playwright

    from human_browser import HumanSession

    pool = proxy = None
    async with async_playwright() as pw:
        if usar_proxy:
            from proxy_pool import ProxyPool
            pool = ProxyPool().start()
            proxy = await pool.acquire_blocking(intervalo=2.0, tentativas=30)

        perfil = pathlib.Path(tempfile.gettempdir()) / "cr_instagram"
        sess = await HumanSession.create(pw, proxy, perfil, layer="maps",
                                         headless=True)
        try:
            url = f"https://www.instagram.com/{usuario}/"
            await sess.page.goto(url, wait_until="domcontentloaded",
                                 timeout=60000)
            await asyncio.sleep(6)
            texto = await sess.page.inner_text("body")
            # os links externos da bio saem do DOM, não do texto: o texto mostra
            # o rótulo encurtado com "…", e o href tem o endereço inteiro
            # O rodapé do Instagram é feito de links da Meta, e eles vinham
            # como se fossem da bio. O que interessa é o link QUE O DONO pôs —
            # e ele passa pelo redirecionador `l.instagram.com/?u=<destino>`.
            links = await sess.page.evaluate(
                """() => {
                  const meta = /(meta\.com|facebook\.com|threads\.|whatsapp\.com\/?$)/i;
                  return [...document.querySelectorAll('a[href]')]
                    .map(a => {
                      const h = a.href || '';
                      const m = h.match(/l\.instagram\.com\/\?u=([^&]+)/);
                      return m ? decodeURIComponent(m[1]) : h;
                    })
                    .filter(h => h && !h.includes('instagram.com') && !meta.test(h))
                    .slice(0, 10);
                }""")
            # QUANDO FOI A ÚLTIMA PUBLICAÇÃO, e as miniaturas da grade.
            #
            # Perfil parado há dois anos e perfil que postou ontem dizem coisas
            # opostas sobre o mesmo endereço. Num radar de pontos comerciais é
            # o que separa loja viva de fachada que sobrou no mapa.
            #
            # A data sai do `<time datetime>`, que é o carimbo do próprio
            # Instagram. Nada é estimado a partir de "há 3 semanas" na tela:
            # data inventada é pior que ausente, porque decide uma visita.
            publicacoes = await sess.page.evaluate(
                """() => {
                  const datas = [...document.querySelectorAll('time[datetime]')]
                    .map(t => t.getAttribute('datetime'))
                    .filter(Boolean).sort().reverse();
                  const fotos = [...document.querySelectorAll('article img, main img')]
                    .map(i => i.src || '')
                    .filter(s => s.startsWith('http') &&
                                 !s.includes('/s150x150/'))   // o avatar
                    .slice(0, 6);
                  const posts = [...document.querySelectorAll('a[href*="/p/"]')]
                    .map(a => a.getAttribute('href'))
                    .filter(Boolean).slice(0, 3);
                  return {ultima: datas[0] || null, quantas_datas: datas.length,
                          fotos, posts};
                }""")

            # A DATA SÓ EXISTE DENTRO DO POST. A grade do perfil é só imagem:
            # o `<time datetime>` aparece na página da publicação. Sem abrir a
            # primeira, `ultima_publicacao` voltava sempre nula — foi o que o
            # primeiro teste mostrou.
            if not publicacoes.get("ultima") and publicacoes.get("posts"):
                try:
                    await sess.page.goto(
                        "https://www.instagram.com" + publicacoes["posts"][0],
                        wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                    publicacoes["ultima"] = await sess.page.evaluate(
                        """() => {
                          const t = document.querySelector('time[datetime]');
                          return t ? t.getAttribute('datetime') : null;
                        }""")
                    publicacoes["post_lido"] = publicacoes["posts"][0]
                except Exception:
                    pass      # sem data é melhor que data errada

            return {"url": url, "texto": texto, "links": links,
                    "publicacoes": publicacoes}
        finally:
            try:
                await sess.close()
            except Exception:
                pass
            if pool and proxy:
                try:
                    await pool.release(proxy)
                except Exception:
                    pass


def _interpretar(bruto: dict, usuario: str) -> dict:
    texto = bruto.get("texto") or ""
    corte = _FIM.search(texto)
    util = texto[:corte.start()] if corte else texto[:2500]

    # O cabeçalho de visitante ("Entrar / Cadastre-se") entra no texto e não é
    # do perfil. Sai antes de a bio ser montada.
    util = re.sub(r"^\s*(Entrar|Cadastre-se|Cadastrar)\s*", "", util,
                  flags=re.I | re.M).strip()

    seg = _SEGUIDORES.search(util)
    sgd = _SEGUINDO.search(util)
    horas = _HORA.findall(util)
    tels = [t.strip() for t in _TEL.findall(util) if len(re.sub(r"\D", "", t)) >= 10]

    privado = "Esta conta é privada" in texto or "conta privada" in texto.lower()

    pub = bruto.get("publicacoes") or {}
    ultima = pub.get("ultima")
    n_seg = _numero(seg.group(1), seg.group(2)) if seg else None

    # PERFIL PEQUENO E SEM PUBLICACAO provavelmente e homonimo, nao a loja.
    #
    # Medido: `@vancosty` tem 15 seguidores e nenhum post; o do supermercado e
    # `@supermercadovancosty`, com 37.600 seguidores e post em 29/09/2025. O
    # modelo pegou o primeiro handle que a busca mostrou e concluiu "perfil
    # inativo" — quando o perfil certo estava ativo e era OUTRO.
    #
    # O aviso nao escolhe por ele: diz o que o numero mostra e manda procurar o
    # handle que leva o nome do estabelecimento.
    suspeito = (not ultima) and (n_seg is not None) and n_seg < 300
    aviso_perfil = (
        f"perfil com {n_seg} seguidores e nenhuma publicacao legivel — "
        f"provavelmente NAO e o do estabelecimento, e sim um homonimo. "
        f"Procure o handle que contenha o nome da loja."
        if suspeito else None)

    # "há quantos dias" é o que a pessoa quer saber; a data crua fica junto para
    # quem for conferir. A conta é sobre o carimbo do Instagram, não sobre
    # texto lido da tela.
    dias = None
    if ultima:
        try:
            import datetime as _dt
            quando = _dt.datetime.fromisoformat(ultima.replace("Z", "+00:00"))
            dias = (_dt.datetime.now(_dt.timezone.utc) - quando).days
        except Exception:
            dias = None

    return {
        "usuario": usuario,
        "url": bruto.get("url"),
        "aviso_perfil": aviso_perfil,
        "ultima_publicacao": ultima,
        "dias_desde_a_ultima_publicacao": dias,
        "sinal_de_atividade": (
            None if dias is None else
            "ativo" if dias <= 30 else
            "pouco ativo" if dias <= 180 else
            "PARADO — última publicação há mais de 6 meses"),
        "fotos": pub.get("fotos") or [],

        "seguidores": n_seg,
        "seguindo": _numero(sgd.group(1), None) if sgd else None,
        # a bio inteira vai junto: o modelo lê melhor o texto corrido do que
        # campos que eu tentaria adivinhar com regex
        "bio": " ".join(util.split())[:900],
        "horarios_citados": horas[:4] or None,
        "telefones_citados": tels[:3] or None,
        # links do DOM E do texto: o Instagram às vezes escreve o endereço na
        # bio sem transformar em âncora, e é justamente aí que aparece o iFood
        "links": (list(dict.fromkeys((bruto.get("links") or [])
                  + _URL_TEXTO.findall(util))) or None),
        "privado": privado or None,
        "aviso": ("perfil privado: só o cabeçalho é público"
                  if privado else None),
    }


def consultar_instagram(usuario: str, usar_proxy: bool = True) -> dict:
    """Perfil público: seguidores, bio, horário, telefone e links.

    `usuario` é o @ sem arroba. Também aceita a URL inteira.
    """
    u = (usuario or "").strip().strip("@")
    m = re.search(r"instagram\.com/([^/?#]+)", u)
    if m:
        u = m.group(1)
    if not u:
        return {"erro": "informe o usuário do Instagram"}
    try:
        bruto = asyncio.run(asyncio.wait_for(_uma(u, usar_proxy),
                                             timeout=MAX_SEGUNDOS))
    except asyncio.TimeoutError:
        return {"erro": f"o Instagram não respondeu em {MAX_SEGUNDOS}s"}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
    return _interpretar(bruto, u)


ESQUEMA = {
    "type": "function", "function": {
        "name": "consultar_instagram",
        "description": (
            "Le o perfil publico de um estabelecimento no Instagram: "
            "seguidores, bio, HORARIO de funcionamento, telefone e os links da "
            "bio (iFood, WhatsApp, cardapio). E a fonte do que a Receita nao "
            "tem — horario e contato atual, escritos pelo dono. "
            "Use quando o usuario pedir horario, contato ou 'esta ativo?', e "
            "quando a busca web tiver mostrado o perfil. LENTA: abre navegador."),
        "parameters": {"type": "object", "properties": {
            "usuario": {"type": "string",
                        "description": "o @ sem arroba, ou a URL do perfil"}},
            "required": ["usuario"]}},
}
