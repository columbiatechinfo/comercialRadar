# -*- coding: utf-8 -*-
"""enderecar_airbnb.py — o print da ficha vira rua e número.

O BURACO QUE ISTO FECHA

O Airbnb **não publica endereço** e arredonda a coordenada em 92% dos anúncios
(4 casas, ~11 m). Sem rua e número, a hospedagem não casa com o cadastro do IBGE
nem com a carteira do cliente — e eu tinha dado isso como limite da fonte.

A saída não estava no payload: está na IMAGEM. A ficha aberta, fotografada e
levada ao assistente devolve o endereço completo. No exemplo que provou o
caminho, o print de uma cabana virou "Alameda das Pedras Negras, 565 — Parque
Dom João VI, Nova Friburgo - RJ, 28616-090", com fonte, e com o aviso de que o
Airbnb publica área aproximada.

O PRINT JÁ EXISTE. `detalhar_airbnb.py` grava a ficha em
`/app/capturas/airbnb/<id>.png` justamente para isto. Este arquivo é o passo
seguinte: pega o print, pergunta, e grava o que voltar.

O QUE É GRAVADO, E COM QUE ETIQUETA

`endereco_fonte = 'ia_imagem'` fica em toda linha que passou por aqui. Não é
detalhe de auditoria: é a diferença entre um endereço que o Airbnb declarou e um
que um assistente leu de uma foto. Quem cruzar depois precisa poder separar os
dois, e a coluna existe desde a migração 0043 exatamente para isso.

`confianca` vem do próprio assistente — alta, média ou baixa, com o motivo — e é
gravada junto. Endereço de confiança baixa entra marcado, não entra escondido.

NADA É SOBRESCRITO. Se o anúncio já tem endereço vindo de outro caminho, ele
fica. A imagem é o último recurso, não o primeiro.

ONDE ISTO RODA. No container `scrapling`: precisa de Playwright com navegador
para conversar com o assistente. O `radar-minerador` puro não serve.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter

import base_comum as bc

COOKIE = "/app/estado/cookie_maps.json"
CAPTURAS = "/app/capturas/airbnb"
GPT = "https://chatgpt.com/"
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]

CHAVES = ("nome_anuncio", "predio_ou_condominio", "endereco", "numero", "bairro",
          "cidade", "uf", "cep", "telefone", "site", "instagram", "confianca",
          "fontes")

MODAIS = ("Rejeitar não essenciais", "Reject non-essential", "Recusar",
          "Fechar", "Close", "Stay logged out", "Agora não", "Not now",
          "Aceitar tudo", "Accept all", "OK", "Entendi")


def _log(m: str) -> None:
    print(m, flush=True)


def prompt_da_imagem(a) -> str:
    return (
        "Essa imagem é a captura de um anúncio de hospedagem do Airbnb que fica "
        "no bairro %s, em %s %s. "
        "Identifica a hospedagem e me diz o endereço completo com rua e número, "
        "o CEP, o nome do prédio ou condomínio, o telefone, o site e as redes "
        "sociais do local. "
        "Manda em JSON com as chaves: %s. "
        "Em 'fontes', um objeto com o link (URL) de onde tirou cada dado; sem "
        "link, o nome da fonte. Em 'confianca', escreve alta, media ou baixa "
        "para o endereço, e por quê. "
        "Só o que souber e tiver confirmação, não inventa nada: null no que não "
        "tiver. Se o Airbnb só mostrar a área aproximada, diz isso."
        % (a.get("bairro") or "?", a.get("cidade") or "?", a.get("uf") or "",
           ", ".join(CHAVES))
    )


BLOCOS = r"""(corte) => {
  const s = [];
  for (const e of document.querySelectorAll('code, pre')) {
    const t = (e.textContent || '').trim();
    if (t.length > 60) s.push(t);
  }
  return s.slice(corte);
}"""
CONTAR = """() => [...document.querySelectorAll('code, pre')]
    .filter(e => (e.textContent || '').trim().length > 60).length"""


def extrair(textos):
    for t in reversed(textos or []):
        b = t.strip()
        for abre, fecha in (("{", "}"), ("[", "]")):
            i, j = b.find(abre), b.rfind(fecha)
            if i < 0 or j <= i:
                continue
            try:
                d = json.loads(b[i:j + 1])
                return d[0] if isinstance(d, list) and d else d
            except Exception:                                  # noqa: BLE001
                continue
    return None


def perguntar_com_imagem(pg, caminho, pergunta, teto=150):
    """Sobe a imagem, escreve a pergunta, envia pelo BOTÃO.

    O upload é por `<input type=file>`, que no ChatGPT fica escondido — daí
    `set_input_files` direto no elemento, sem clicar em nada. E a miniatura
    precisa terminar de subir antes do envio: mandar antes faz a pergunta
    chegar sem a imagem, e a resposta sai sobre nada.
    """
    entradas = pg.locator('input[type="file"]')
    if not entradas.count():
        return None, "sem input de arquivo", 0
    entradas.first.set_input_files(caminho)
    pg.wait_for_timeout(6000)

    alvo = None
    for sel in ("#prompt-textarea", "textarea", 'div[contenteditable="true"]'):
        try:
            e = pg.locator(sel).first
            e.wait_for(state="visible", timeout=10000)
            alvo = e
            break
        except Exception:                                      # noqa: BLE001
            continue
    if alvo is None:
        return None, "sem caixa", 0

    antes_n = pg.evaluate(CONTAR) or 0
    alvo.click(timeout=10000)
    alvo.type(pergunta, delay=4)
    pg.wait_for_timeout(1500)

    # O ENTER NÃO ENVIA no ChatGPT — medido. O botão é obrigatório.
    enviou = False
    for sel in ('button[data-testid="send-button"]',
                'button[aria-label*="nviar"]', 'button[aria-label*="end"]'):
        try:
            pg.locator(sel).first.click(timeout=8000)
            enviou = True
            break
        except Exception:                                      # noqa: BLE001
            continue
    if not enviou:
        pg.keyboard.press("Enter")

    t0 = time.time()
    blocos, anterior, estavel = [], -1, 0
    while time.time() - t0 < teto:
        if (pg.evaluate(CONTAR) or 0) > antes_n:
            blocos = pg.evaluate(BLOCOS, antes_n) or []
            tam = sum(len(b) for b in blocos)
            if tam and tam == anterior:
                estavel += 1
                if estavel >= 2:
                    break
            else:
                estavel = 0
            anterior = tam
        pg.wait_for_timeout(2500)
    return extrair(blocos), None, round(time.time() - t0, 1)


# ---------------------------------------------------------------- banco -----
SQL_ALVOS = """
    select anuncio_id, nome, titulo, bairro, cidade, uf, print_ficha
      from radar_comercial.airbnb_anuncio
     where coalesce(endereco,'') = ''
       and coalesce(print_ficha,'') <> ''
       and na_area is not false
       %s
     order by anuncio_id
"""


def caminho_do_print(guardado, anuncio_id) -> str:
    """O print pode estar gravado como caminho absoluto ou só como nome."""
    for tentativa in (guardado,
                      os.path.join(CAPTURAS, os.path.basename(guardado or "")),
                      os.path.join(CAPTURAS, "%s.png" % anuncio_id)):
        if tentativa and os.path.exists(tentativa):
            return tentativa
    return ""


def gravar(con, cur, anuncio_id, d) -> list:
    """Só onde está vazio, e só o que tem forma de endereço."""
    campos, valores, notas = [], [], []

    via = (d.get("endereco") or "").strip()
    if via and via.lower() != "null":
        campos.append("endereco = coalesce(nullif(btrim(endereco), ''), %s)")
        valores.append(via[:300])
        campos.append("endereco_fonte = coalesce(nullif(btrim(endereco_fonte), ''), %s)")
        valores.append("ia_imagem")
    else:
        notas.append("o assistente não chegou à rua")

    for coluna, chave in (("numero", "numero"), ("bairro", "bairro"),
                          ("cep", "cep")):
        v = d.get(chave)
        if v and str(v).strip().lower() not in ("", "null", "none"):
            texto = str(v).strip()
            if coluna == "cep":
                digitos = re.sub(r"\D", "", texto)
                if len(digitos) != 8:
                    notas.append("CEP fora de forma: %s" % texto[:20])
                    continue
                texto = "%s-%s" % (digitos[:5], digitos[5:])
            campos.append("%s = coalesce(nullif(btrim(%s), ''), %%s)"
                          % (coluna, coluna))
            valores.append(texto[:120])

    if not campos:
        return notas or ["nada aproveitável"]

    cur.execute("update radar_comercial.airbnb_anuncio set %s where anuncio_id = %%s"
                % ", ".join(campos), valores + [anuncio_id])
    con.commit()
    return notas


def rodar(area="", limite=0, aplicar=False, visivel=False) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    filtro, args = "", []
    if area:
        filtro = "and area_ref = %s"
        args.append(area)
    sql = SQL_ALVOS % filtro
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, args)
    alvos = [{"id": r[0], "nome": r[1], "titulo": r[2], "bairro": r[3],
              "cidade": r[4], "uf": r[5], "print": r[6]}
             for r in cur.fetchall()]
    _log("   %d anúncios sem endereço e COM print da ficha" % len(alvos))

    prontos = []
    for a in alvos:
        p = caminho_do_print(a["print"], a["id"])
        if p:
            a["arquivo"] = p
            prontos.append(a)
    if len(prontos) < len(alvos):
        _log("   %d ficaram de fora: o print está no banco mas não no disco"
             % (len(alvos) - len(prontos)))
    if not prontos:
        _log("   nada a endereçar. Rode `detalhar_airbnb.py` antes — é ele que")
        _log("   fotografa a ficha.")
        con.close()
        return {"alvos": 0}

    if not aplicar:
        _log("   (ensaio: nada perguntado nem gravado. Use --aplicar)")
        for a in prontos[:5]:
            _log("      %-22s %-28s %s" % (a["id"][:22], str(a["nome"])[:28],
                                           a["arquivo"]))
        con.close()
        return {"alvos": len(prontos), "gravados": 0}

    from playwright.sync_api import sync_playwright
    from proxy_pool import ProxyPool
    import asyncio

    pool = ProxyPool(pais="BR")
    pool.start()

    async def pegar(n):
        return [await pool.acquire() for _ in range(n)]

    px = [p for p in asyncio.run(pegar(1)) if p]
    if not px:
        _log("   ⚠️  sem proxy — a etapa não roda pelo IP da casa")
        con.close()
        return {"alvos": len(prontos), "erro": "sem proxy"}
    p = px[0]
    proxy = {"server": p["server"], "username": p.get("username"),
             "password": p.get("password")}

    placar = Counter()
    t0 = time.time()
    with sync_playwright() as pw:
        nav = pw.chromium.launch(headless=not visivel, args=ARGS, proxy=proxy)
        try:
            for a in prontos:
                # ABA NOVA POR ANÚNCIO: a conversa precisa ser nova, senão o
                # assistente responde sobre a imagem anterior — e o texto sai
                # convincente do mesmo jeito.
                ctx = nav.new_context(
                    viewport={"width": 1360, "height": 1000}, locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    storage_state=COOKIE if os.path.exists(COOKIE) else None)
                pg = ctx.new_page()
                try:
                    pg.goto(GPT, timeout=90000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(random.randint(9000, 12000))
                    for texto in MODAIS:
                        try:
                            b = pg.get_by_role("button", name=texto)
                            if b.count():
                                b.first.click(timeout=4000)
                                pg.wait_for_timeout(1500)
                                break
                        except Exception:                      # noqa: BLE001
                            continue
                    d, erro, dt = perguntar_com_imagem(
                        pg, a["arquivo"], prompt_da_imagem(a))
                except Exception as e:                         # noqa: BLE001
                    d, erro, dt = None, "%s: %s" % (type(e).__name__, str(e)[:70]), 0

                if erro or not isinstance(d, dict):
                    placar["sem_resposta"] += 1
                    _log("      %s · %ss · sem JSON (%s)" % (a["id"][:18], dt, erro))
                else:
                    notas = gravar(con, cur, a["id"], d)
                    achou = bool((d.get("endereco") or "").strip())
                    placar["com_endereco" if achou else "sem_endereco"] += 1
                    conf = str(d.get("confianca") or "?")[:40]
                    _log("      %s · %ss · %s%s · confiança: %s"
                         % (a["id"][:18], dt, (d.get("endereco") or "—")[:34],
                            (", %s" % d["numero"]) if d.get("numero") else "",
                            conf))
                    for n in notas:
                        _log("         %s" % n)
                ctx.close()
        finally:
            nav.close()

    _log("\n   %d anúncios · %.1f min" % (len(prontos), (time.time() - t0) / 60.0))
    for k, v in placar.most_common():
        _log("      %-16s %4d" % (k, v))
    con.close()
    return {"alvos": len(prontos), **dict(placar)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="O print da ficha do Airbnb vira endereço com número.")
    p.add_argument("--area", default="", help="restringe a uma área desenhada")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--visivel", action="store_true",
                   help="mostra o navegador; padrão é sem janela")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    _log("▶ endereço do Airbnb pela imagem%s"
         % ((" · área %s" % a.area) if a.area else ""))
    rodar(a.area, a.limite, a.aplicar, a.visivel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
