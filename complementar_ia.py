# -*- coding: utf-8 -*-
"""complementar_ia.py — CNPJ, telefone e redes pelo que os assistentes acham.

O QUE ESTA ETAPA FAZ, E O QUE ELA NÃO FAZ

Ela pergunta a um assistente público — sem login, por proxy, no mesmo navegador
que já serve ao Maps — onde fica um estabelecimento, qual o telefone, o CNPJ, a
razão social e as redes sociais, **com a fonte de cada campo**. É a última
peneira da fase 1: entra só o POI que as fontes estruturadas não completaram.

**O que sai daqui é PISTA, não verdade.** A sonda que provou o caminho registrou
o motivo: `Kampeki Sushi 33.300.010/0001-00` tem cara de número redondo demais,
e num outro caminho "Loft Maxplaza" virou "Loft Brasil Tecnologia Ltda" — que é
uma proptech, não a hospedagem. Por isso três travas, nesta ordem:

    1. o CNPJ só entra se os DÍGITOS VERIFICADORES fecharem. É conta, é local,
       é de graça, e derruba número inventado na hora.
    2. campo que o POI já tem NUNCA é sobrescrito. O dado da fonte estruturada
       vence o do assistente, sempre.
    3. `cnpj_conf` registra o quanto vale: 0,5 quando veio com link de fonte,
       0,3 quando veio sem. Quem consumir decide o que fazer com isso.

CHATGPT É O PRIMÁRIO, GEMINI É SEGUNDA OPINIÃO

Medido em 02/09/2026, lote de cinco numa pergunta só: 5 de 5 completos em 22,5 s,
com CNPJ, telefone e fonte com link em quase tudo. O ChatGPT devolve endereço com
número, telefone, CNPJ, razão social e fonte por campo; o Gemini erra mais a
entidade e preenche menos. O Gemini entra só para o que voltar incompleto — duas
opiniões com fonte valem mais que uma, e discordância entre elas é informação.

AS TRÊS ARMADILHAS QUE CUSTARAM A SONDA, e que este arquivo já traz resolvidas:

    o banner de cookies da OpenAI trava a primeira aba, e a tela fica esperando
    resposta de uma pergunta que está atrás de um modal — 150 s por nada. Ele é
    fechado, e pela opção que RECUSA o não essencial.

    a caixa precisa estar PRONTA, não só visível. Digitar antes de o campo
    aceitar foco fazia o primeiro lote de cada aba voltar vazio. Confere-se que
    o texto entrou antes de enviar.

    conversa NOVA a cada lote. Na mesma, o Gemini trata a pergunta seguinte como
    "complete a anterior" e devolve os itens do lote passado com mais campos —
    parece dado novo e não é.

E duas que continuam valendo: **o Enter não envia no ChatGPT** (só o botão), e a
resposta vive em `code`/`pre`, não em `[data-message-author-role]`.

ONDE ISTO RODA. No container `scrapling`, que tem Playwright e navegador. O
`radar-minerador` puro não serve.
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
ARGS = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]

ASSISTENTES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "caixa": ("#prompt-textarea", "textarea", 'div[contenteditable="true"]',
                  '[role="textbox"]'),
        "botao": ('button[data-testid="send-button"]',
                  'button[aria-label*="nviar"]', 'button[aria-label*="end"]'),
    },
    "gemini": {
        "url": "https://gemini.google.com/app",
        "caixa": ('div[contenteditable="true"]',
                  'rich-textarea div[contenteditable]', '[role="textbox"]'),
        "botao": None,          # aqui o Enter envia
    },
}

# A ordem importa: recusar o não essencial vem antes de "aceitar tudo", que fica
# só como último recurso para não ficar preso atrás do modal.
CONSENTIMENTO = ("Rejeitar não essenciais", "Reject non-essential", "Recusar",
                 "Reject all", "Rejeitar tudo", "Continuar sem aceitar",
                 "Aceitar tudo", "Accept all", "Fechar", "Close", "OK",
                 "Entendi", "Got it", "Agora não", "Not now", "Dispensar")

CHAVES = ("n", "nome", "endereco", "numero", "bairro", "cep", "telefone",
          "whatsapp", "cnpj", "razao_social", "situacao_cadastral", "site",
          "instagram", "instagram_seguidores", "facebook",
          "facebook_seguidores", "fontes")

ESSENCIAIS = ("endereco", "numero", "telefone", "cnpj", "razao_social")


def _log(m: str) -> None:
    print(m, flush=True)


# ------------------------------------------------------------ o CNPJ --------
def cnpj_valido(bruto) -> bool:
    """Os dígitos verificadores fecham?

    Não diz que a empresa existe nem que é ESTA — diz que o número não foi
    inventado ao acaso. É conta de somar, roda em microssegundos e derruba a
    maior parte do que um modelo produz quando não sabe e não quer dizer que
    não sabe.
    """
    n = re.sub(r"\D", "", str(bruto or ""))
    if len(n) != 14 or len(set(n)) == 1:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum(int(d) * p for d, p in zip(n[:tamanho], pesos))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if int(n[tamanho]) != digito:
            return False
    return True


def so_digitos(s):
    d = re.sub(r"\D", "", str(s or ""))
    return d or None


# ------------------------------------------------------- o navegador --------
def montar(lote):
    """Pergunta natural primeiro; as regras curtas no fim.

    Prompt defensivo demais faz o modelo se recolher: numa rodada o Gemini
    devolveu null em tudo e escreveu na fonte que os dados vieram "obtidos
    diretamente do prompt" — não foi procurar nada.
    """
    def uma(i, a):
        partes = [a["nome"]]
        if a.get("onde"):
            partes.append(a["onde"])
        if a.get("bairro"):
            partes.append(a["bairro"])
        partes.append("%s %s" % (a.get("cidade") or "", a.get("uf") or ""))
        if a.get("cep"):
            partes.append("CEP %s" % a["cep"])
        return "%d) %s" % (i, " - ".join(str(x).strip() for x in partes
                                         if str(x).strip()))

    linhas = "\n".join(uma(i, a) for i, a in enumerate(lote, 1))
    return (
        "Para cada estabelecimento da lista, me diz onde fica (rua e número), o "
        "telefone, o WhatsApp, o CNPJ, a razão social, a situação cadastral, o "
        "site e as redes sociais com o total de seguidores em cada uma:\n"
        + linhas + "\n"
        "Manda em JSON, um array com um objeto por estabelecimento, com as "
        "chaves: " + ", ".join(CHAVES) + " — 'n' é o número da lista. "
        "Em 'fontes', um objeto com uma entrada por campo preenchido, cujo valor "
        "é o LINK (URL completa) de onde tirou; sem link, o nome da fonte. "
        "Só o que souber e tiver confirmação, não inventa nada: null no que não "
        "tiver. Não usa CNPJ, telefone ou perfil da rede, da matriz ou de outra "
        "unidade — se for da rede, diz isso na fonte."
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
        for abre, fecha in (("[", "]"), ("{", "}")):
            i, j = b.find(abre), b.rfind(fecha)
            if i < 0 or j <= i:
                continue
            try:
                return json.loads(b[i:j + 1])
            except Exception:                                  # noqa: BLE001
                continue
    return None


def fechar_modais(pg):
    fechou = []
    for _ in range(3):                     # podem vir empilhados
        achou = False
        for texto in CONSENTIMENTO:
            try:
                b = pg.get_by_role("button", name=texto)
                if b.count():
                    b.first.click(timeout=4000)
                    fechou.append(texto)
                    pg.wait_for_timeout(2000)
                    achou = True
                    break
            except Exception:                                  # noqa: BLE001
                continue
        if not achou:
            break
    return fechou


def escrever(pg, seletores, texto):
    """Digita e CONFERE que entrou. Campo visível nem sempre aceita foco ainda."""
    for _ in range(3):
        alvo = sel = None
        for s in seletores:
            try:
                e = pg.locator(s).first
                e.wait_for(state="visible", timeout=12000)
                alvo, sel = e, s
                break
            except Exception:                                  # noqa: BLE001
                continue
        if alvo is None:
            pg.wait_for_timeout(4000)
            continue
        try:
            alvo.click(timeout=10000)
            pg.wait_for_timeout(600)
            alvo.type(texto, delay=4)
            pg.wait_for_timeout(900)
            entrou = pg.evaluate(
                """(sel) => {
                     const e = document.querySelector(sel);
                     if (!e) return 0;
                     return ((e.value || e.textContent || '') + '').trim().length;
                   }""", sel)
            if entrou and entrou > 40:
                return True, sel
        except Exception:                                      # noqa: BLE001
            pass
        pg.wait_for_timeout(3000)
    return False, None


def perguntar(pg, cfg, pergunta, teto=150):
    ok, _ = escrever(pg, cfg["caixa"], pergunta)
    if not ok:
        return None, "o texto não entrou na caixa", 0

    antes_n = pg.evaluate(CONTAR) or 0
    enviou = False
    if cfg["botao"]:
        # O ENTER NÃO ENVIA no ChatGPT. O botão é obrigatório.
        for s in cfg["botao"]:
            try:
                pg.locator(s).first.click(timeout=6000)
                enviou = True
                break
            except Exception:                                  # noqa: BLE001
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


def abrir(nav, qual):
    cfg = ASSISTENTES[qual]
    ctx = nav.new_context(viewport={"width": 1360, "height": 1000},
                          locale="pt-BR", timezone_id="America/Sao_Paulo",
                          storage_state=COOKIE if os.path.exists(COOKIE) else None)
    pg = ctx.new_page()
    pg.goto(cfg["url"], timeout=90000, wait_until="domcontentloaded")
    pg.wait_for_timeout(random.randint(9000, 12000))
    return ctx, pg, fechar_modais(pg)


def consultar(pw, proxy, qual, lotes, visivel=True):
    """Vários lotes na MESMA janela, cada um em ABA e conversa NOVAS.

    A janela é o caro — perfil, IP e cookie ficam de pé. A aba custa ~10 s e é
    o que garante conversa nova: recarregar a mesma deixava a caixa num estado
    em que o envio não sai, e o lote seguinte ficava 150 s sem resposta.
    """
    cfg = ASSISTENTES[qual]
    saidas = []
    nav = pw.chromium.launch(headless=not visivel, args=ARGS, proxy=proxy)
    try:
        for n, lote in enumerate(lotes, 1):
            ctx, pg, fechados = abrir(nav, qual)
            if n == 1 and fechados:
                _log("      modais fechados: %s" % fechados)
            dados, erro, dt = perguntar(pg, cfg, montar(lote))
            saidas.append({"lote": n, "itens": lote, "dados": dados,
                           "erro": erro, "segundos": dt})
            ctx.close()
    finally:
        nav.close()
    return saidas


def incompletos(dados):
    return [d.get("nome") for d in (dados or [])
            if isinstance(d, dict) and any(not d.get(c) for c in ESSENCIAIS)]


# ---------------------------------------------------------- o banco ---------
# O ENDEREÇO JÁ RESOLVIDO VAI NA PERGUNTA, e isso muda a qualidade da resposta.
#
# Perguntar por "Locadora Gold, Canoas" faz o assistente escolher entre homônimos
# da região metropolitana. Perguntar por "Locadora Gold — RUA HUMAITA, 1258,
# Canoas RS" ancora a entidade — e desancorar foi exatamente o erro que a sonda
# registrou: "Loft Maxplaza" virou "Loft Brasil Tecnologia Ltda", uma proptech,
# porque o nome sozinho não dizia de qual lugar se falava.
#
# A etapa 7 já deixou esse endereço em `logradouro_resolvido`, provado. Usá-lo é
# de graça; não usá-lo seria pagar duas vezes pela mesma pergunta.
SQL_ALVOS = """
    select p.id, p.nome, p.cidade, p.uf,
           coalesce(l.logradouro, '') as via,
           coalesce(l.numero, '')     as numero,
           coalesce(l.bairro, '')     as bairro,
           coalesce(l.cep, '')        as cep,
           coalesce(l.forca, 'sem')   as forca
      from radar_comercial.pois p
      left join radar_comercial.logradouro_resolvido l on l.poi_id = p.id
     where p.fundido_em is null
       and coalesce(p.nome,'') <> ''
       and (coalesce(p.cnpj,'') = '' or coalesce(p.telefone,'') = '')
       and p.ia_resposta is null
       %s
     order by p.id
"""


def alvos(cur, cidade: str, limite: int) -> list:
    filtro, args = "", []
    if cidade:
        filtro = ("and translate(upper(coalesce(p.cidade,'')), "
                  "'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC') = "
                  "translate(upper(%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC')")
        args.append(cidade)
    sql = SQL_ALVOS % filtro
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, args)
    saida = []
    for pid, nome, cid, uf, via, numero, bairro, cep, forca in cur.fetchall():
        d = re.sub(r"\D", "", cep or "")
        # ENDEREÇO INFERIDO POR PROXIMIDADE NÃO ENTRA NA PERGUNTA.
        #
        # `indicio` é o que a etapa 7 obteve da coordenada, a até 20 m — pode
        # ser a rua vizinha. Ancorar o assistente num indício errado faz dele
        # uma máquina de confirmar o erro, com fonte e tudo. Sem âncora ele ao
        # menos hesita; com âncora errada, não.
        onde = ""
        if forca == "prova" and via:
            onde = via + ((", %s" % numero) if numero else "")
        saida.append({"id": pid, "nome": nome, "cidade": cid, "uf": uf,
                      "onde": onde, "bairro": bairro,
                      "cep": ("%s-%s" % (d[:5], d[5:])) if len(d) == 8 else None})
    return saida


def gravar(con, cur, poi, resposta) -> dict:
    """Escreve só onde está vazio, e só o que passou nas travas."""
    fontes = resposta.get("fontes") or {}
    tem_link = any(isinstance(v, str) and v.startswith("http")
                   for v in fontes.values())

    campos, valores, recusas = [], [], []

    cnpj = so_digitos(resposta.get("cnpj"))
    if cnpj:
        if cnpj_valido(cnpj):
            campos += ["cnpj = coalesce(nullif(btrim(cnpj), ''), %s)",
                       "cnpj_conf = coalesce(cnpj_conf, %s)"]
            valores += [cnpj, 0.5 if tem_link else 0.3]
        else:
            recusas.append("cnpj com dígito verificador inválido: %s" % cnpj)

    for coluna, chave in (("telefone", "telefone"),
                          ("razao_social", "razao_social"),
                          ("situacao_cadastral", "situacao_cadastral"),
                          ("website", "site"),
                          ("instagram", "instagram"),
                          ("facebook", "facebook")):
        v = resposta.get(chave)
        if v and str(v).strip() and str(v).strip().lower() != "null":
            campos.append("%s = coalesce(nullif(btrim(%s), ''), %%s)"
                          % (coluna, coluna))
            valores.append(str(v).strip()[:400])

    # A resposta inteira fica gravada — inclusive quando nada foi aproveitado.
    # É ela que impede a mesma pergunta de ser feita de novo, e é onde se
    # confere depois de onde veio cada campo.
    campos.append("ia_resposta = %s")
    valores.append(json.dumps(resposta, ensure_ascii=False)[:8000])
    if fontes:
        campos.append("fontes_web = coalesce(nullif(btrim(fontes_web), ''), %s)")
        valores.append(json.dumps(fontes, ensure_ascii=False)[:2000])

    cur.execute("update radar_comercial.pois set %s where id = %%s"
                % ", ".join(campos), valores + [poi["id"]])
    con.commit()
    return {"recusas": recusas, "com_link": tem_link}


def rodar(cidade="", limite=0, lote=5, aplicar=False, visivel=True) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    lista = alvos(cur, cidade, limite)
    _log("   %d POIs sem CNPJ ou sem telefone, ainda não perguntados" % len(lista))
    if not lista:
        con.close()
        return {"alvos": 0}

    lotes = [lista[i:i + lote] for i in range(0, len(lista), lote)]
    _log("   %d lotes de até %d" % (len(lotes), lote))
    if not aplicar:
        _log("   (ensaio: nada perguntado nem gravado. Use --aplicar)")
        for a in lista[:5]:
            _log("      %s" % montar([a]).splitlines()[-2][:98])
        con.close()
        return {"alvos": len(lista), "gravados": 0}

    from playwright.sync_api import sync_playwright

    from proxy_pool import ProxyPool
    import asyncio

    # DOIS PROXIES DE UMA VEZ, num `asyncio.run` só.
    #
    # O `acquire` é async e guarda os IPs em uso num lock que não atravessa
    # event loop. Chamar duas vezes em `asyncio.run` separados devolve o MESMO
    # IP as duas vezes — o segundo processo começa com a lista de usados vazia.
    pool = ProxyPool(pais="BR")
    pool.start()

    async def pegar(n):
        return [await pool.acquire() for _ in range(n)]

    px = [{"server": p["server"], "username": p.get("username"),
           "password": p.get("password")} for p in asyncio.run(pegar(2)) if p]
    if not px:
        _log("   ⚠️  sem proxy disponível — a etapa não roda pelo IP da casa")
        con.close()
        return {"alvos": len(lista), "erro": "sem proxy"}

    placar = Counter()
    t0 = time.time()
    with sync_playwright() as pw:
        _log("   ChatGPT (primário)")
        respostas = consultar(pw, px[0], "chatgpt", lotes, visivel)

        faltando = []
        for r in respostas:
            dados = r["dados"] if isinstance(r["dados"], list) else []
            _log("      lote %d · %ss · %d respostas · erro=%s"
                 % (r["lote"], r["segundos"], len(dados), r["erro"]))
            por_n = {}
            for d in dados:
                try:
                    por_n[int(d.get("n"))] = d
                except (TypeError, ValueError):
                    pass
            for i, poi in enumerate(r["itens"], 1):
                d = por_n.get(i)
                if not isinstance(d, dict):
                    placar["sem_resposta"] += 1
                    continue
                info = gravar(con, cur, poi, d)
                placar["gravados"] += 1
                if info["com_link"]:
                    placar["com_fonte_com_link"] += 1
                for motivo in info["recusas"]:
                    placar["cnpj_recusado"] += 1
                    _log("         recusado em %s: %s" % (poi["nome"][:24], motivo))
            faltando += [poi for i, poi in enumerate(r["itens"], 1)
                         if isinstance(por_n.get(i), dict)
                         and any(not por_n[i].get(c) for c in ESSENCIAIS)]

        if faltando and len(px) > 1:
            _log("   Gemini (segunda opinião: %d incompletos)" % len(faltando))
            g_lotes = [faltando[i:i + lote]
                       for i in range(0, len(faltando), lote)]
            for r in consultar(pw, px[1], "gemini", g_lotes, visivel):
                dados = r["dados"] if isinstance(r["dados"], list) else []
                por_n = {}
                for d in dados:
                    try:
                        por_n[int(d.get("n"))] = d
                    except (TypeError, ValueError):
                        pass
                for i, poi in enumerate(r["itens"], 1):
                    d = por_n.get(i)
                    if isinstance(d, dict):
                        gravar(con, cur, poi, d)
                        placar["gemini_completou"] += 1

    dt = time.time() - t0
    _log("\n   %d POIs · %.1f min" % (len(lista), dt / 60.0))
    for k, v in placar.most_common():
        _log("      %-22s %5d" % (k, v))
    con.close()
    return {"alvos": len(lista), **dict(placar), "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="CNPJ, telefone e redes pelos assistentes públicos.")
    p.add_argument("--cidade", default="")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--lote", type=int, default=5,
                   help="quantos estabelecimentos por pergunta (medido: 5)")
    p.add_argument("--headless", action="store_true",
                   help="sem janela; use onde não há sessão gráfica")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    _log("▶ complemento por IA%s" % ((" · %s" % a.cidade) if a.cidade else ""))
    rodar(a.cidade, a.limite, a.lote, a.aplicar, visivel=not a.headless)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
