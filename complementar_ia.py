# -*- coding: utf-8 -*-
"""complementar_ia.py — CNPJ, telefone e redes: o SearXNG busca, a Spark lê.

O QUE SAIU, E POR QUÊ

A primeira versão abria ChatGPT e Gemini num navegador com proxy. Parou de
devolver resposta em 02/09/2026: 152 s de espera, modal fechado, pergunta
enviada, e nenhum bloco de volta. Depender de assistente público sempre foi
frágil — ele muda de tela, fecha o acesso sem login, e não avisa.

O QUE ENTRA NO LUGAR

    SearXNG    busca de verdade, local, na porta 7500
    HTTP       as páginas que a busca achou, baixadas direto
    Spark      lê o que veio e responde estruturado — o modelo da casa
    Receita    a conferência final, no banco, sem sair da rede

Nada disso tem login, sessão que expira ou tela que muda. E o modelo é de
VISÃO, então o mesmo caminho serve para o print do Airbnb.

DOIS FILTROS ENTRE A BUSCA E O MODELO, e os dois nasceram de medição

Buscando `LANCHERIA XIS LENA Canoas` na web aberta, dos sete resultados um era
um dicionário no academia.edu, outro um vocabulário no huggingface, outro um
PDF do Ministério de Minas e Energia. A IA leu isso e tirou um telefone de um
vídeo do TikTok.

    ruído        `sites_de_dados.e_ruido` corta enciclopédia, dicionário,
                 repositório de código e diário oficial. Um PDF de diário
                 CONTÉM CNPJs — de outras empresas, e o modelo pega o mais
                 próximo.
    preferência  o que está no catálogo vai primeiro. Na mesma rodada,
                 `MECANICA DIESEL CRIATIVA` saiu completa porque a busca trouxe
                 o `advdinamico` por acaso. O catálogo existe para tirar o acaso.

A CONFERÊNCIA DO CNPJ É LOCAL, e é a melhor parte

CNPJ que a web devolve passa por três portas, nesta ordem:

    1. dígitos verificadores — conta, local, derruba número inventado
    2. `rf_estabelecimentos` — os 72,7 milhões da Receita já estão no banco.
       Se o CNPJ existe, sabe-se a razão social, o município e a situação.
    3. o município bate com o do POI? Se o CNPJ é de outra cidade, é da rede ou
       é de outro estabelecimento — e isso vira aviso, não dado.

`cnpj_conf` guarda o que sobrou dessa peneira: 0,9 quando a Receita confirma no
mesmo município, 0,5 quando confirma noutro, 0,3 quando só os dígitos fecham.

NADA É SOBRESCRITO. Campo que o POI já tem vence o que a web disser.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import base_comum as bc
import sites_de_dados as sd

SEARX = os.environ.get("SEARXNG_URL", "http://127.0.0.1:7500")
SPARK = os.environ.get("SPARK_LLM_URL", "http://192.168.3.20:7400/v1")
MODELO = os.environ.get("SPARK_MODELO", "ia-principal")
THREADS = int(os.environ.get("IA_THREADS", "4"))
PAGINAS = int(os.environ.get("IA_PAGINAS", "5"))       # quantas baixar por POI
TETO_TEXTO = 14000                                     # o que cabe no contexto


def _log(m: str) -> None:
    print(m, flush=True)


# ─────────────────────────────────────────────────────── o CNPJ ─────────────
def cnpj_valido(bruto) -> bool:
    """Os dígitos verificadores fecham? Não diz que a empresa existe — diz que
    o número não foi inventado ao acaso."""
    n = re.sub(r"\D", "", str(bruto or ""))
    if len(n) != 14 or len(set(n)) == 1:
        return False
    for tamanho in (12, 13):
        pesos = list(range(tamanho - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(n[:tamanho], pesos)) % 11
        if int(n[tamanho]) != (0 if resto < 2 else 11 - resto):
            return False
    return True


def conferir_na_receita(cur, cnpj: str, cod_municipio: str) -> dict:
    """A Receita já está no banco. Perguntar a ela é instantâneo e definitivo.

    Devolve o que ela sabe, e — o que mais importa — se o CNPJ é DESTE
    município. CNPJ de outra cidade quase sempre é a matriz da rede, e gravar a
    matriz no lugar da filial estraga o cruzamento inteiro.
    """
    n = re.sub(r"\D", "", cnpj or "")
    if len(n) != 14:
        return {"existe": False}
    cur.execute("""
        select e.municipio, e.situacao_cadastral,
               coalesce(nullif(btrim(e.nome_fantasia),''), em.razao_social),
               em.razao_social, m.descricao
          from resources_root.rf_estabelecimentos e
          left join resources_root.rf_empresas em on em.cnpj_basico = e.cnpj_basico
          left join resources_root.rf_municipios m on m.codigo = e.municipio
         where e.cnpj_basico = %s and e.cnpj_ordem = %s and e.cnpj_dv = %s
         limit 1
    """, (n[:8], n[8:12], n[12:]))
    r = cur.fetchone()
    if not r:
        return {"existe": False}
    mun, sit, fantasia, razao, nome_mun = r
    return {"existe": True, "municipio": mun, "municipio_nome": nome_mun,
            "situacao": sit, "nome_fantasia": fantasia, "razao_social": razao,
            "mesmo_municipio": None}


# ────────────────────────────────────────────────── busca e leitura ─────────
def buscar(q: str, n: int = 8) -> list:
    try:
        req = urllib.request.Request(
            "%s/search?q=%s&format=json&language=pt-BR"
            % (SEARX, urllib.parse.quote(q)),
            headers={"User-Agent": "radar/1.0"})
        with urllib.request.urlopen(req, timeout=30) as h:
            d = json.load(h)
    except Exception:                                          # noqa: BLE001
        return []
    saida = []
    for r in (d.get("results") or []):
        u = r.get("url") or ""
        if not u or sd.e_ruido(u):
            continue
        saida.append({"titulo": r.get("title") or "", "url": u,
                      "resumo": (r.get("content") or "")[:300]})
        if len(saida) >= n:
            break
    return saida


def baixar(url: str, timeout: int = 18) -> str:
    """O texto da página, sem marcação.

    Quem precisa de navegador (medido: 403, 422, 429) é pulado aqui — o resumo
    que a busca já trouxe entra no lugar. Abrir navegador por página faria a
    etapa custar minutos por POI, e o resumo costuma bastar.
    """
    if sd.precisa_navegador(url):
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9"})
        with urllib.request.urlopen(req, timeout=timeout) as h:
            bruto = h.read(400000)
    except Exception:                                          # noqa: BLE001
        return ""
    t = bruto.decode("utf-8", "ignore")
    t = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t.replace("&nbsp;", " ")).strip()


def perguntar(conteudo, teto: int = 1400):
    dados = json.dumps({"model": MODELO,
                        "messages": [{"role": "user", "content": conteudo}],
                        "temperature": 0, "max_tokens": teto}).encode()
    req = urllib.request.Request(SPARK + "/chat/completions", data=dados,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as h:
        t = h.read().decode("utf-8", "ignore")
    try:
        t = json.loads(t)["choices"][0]["message"]["content"]
    except Exception:                                          # noqa: BLE001
        return None
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None


PROMPT = (
    "Estes são resultados de busca sobre um estabelecimento em {cidade}/{uf}.\n"
    "Estabelecimento: {nome}\n"
    "Endereço já confirmado: {onde}\n\n{corpo}\n\n"
    "Diz o que estes textos CONFIRMAM sobre ESTE estabelecimento — não sobre "
    "outro de nome parecido, não sobre a matriz da rede, não sobre uma unidade "
    "em outra cidade.\n"
    "Responde APENAS um JSON com as chaves: cnpj, razao_social, telefone, "
    "whatsapp, site, instagram, facebook, ramo, fontes.\n"
    "'fontes' é um objeto com uma entrada por campo preenchido, cujo valor é a "
    "URL de onde saiu aquele campo.\n"
    "Usa null no que os textos não disserem. Não completa com conhecimento "
    "próprio e não inventa: dizer que não achou é a resposta certa quando não "
    "achou."
)


def investigar(poi: dict) -> dict:
    """Busca, filtra, baixa e pergunta. Devolve o que o modelo respondeu."""
    consultas = sd.consultas(poi["nome"], poi["cidade"], poi.get("uf") or "",
                             poi.get("onde") or "")
    achados, vistos = [], set()
    with ThreadPoolExecutor(max_workers=4) as ex:
        for lote in ex.map(lambda c: buscar(c["q"], 6), consultas[:6]):
            for a in lote:
                if a["url"] in vistos:
                    continue
                vistos.add(a["url"])
                achados.append(a)

    # PREFERÊNCIA PELO CATÁLOGO: quem publica dado de empresa vai na frente, e é
    # quem sobra quando o teto de páginas corta.
    achados.sort(key=lambda a: 0 if any(
        s["dominio"] in a["url"] for s in sd.SITES.values()) else 1)
    escolhidas = achados[:PAGINAS]
    if not escolhidas:
        return {"paginas": 0, "resposta": None}

    with ThreadPoolExecutor(max_workers=PAGINAS) as ex:
        textos = list(ex.map(baixar, [a["url"] for a in escolhidas]))

    corpo = "\n\n".join(
        "FONTE: %s\nTITULO: %s\nTEXTO: %s"
        % (a["url"], a["titulo"], (t or a["resumo"])[:2600])
        for a, t in zip(escolhidas, textos))[:TETO_TEXTO]

    d = perguntar(PROMPT.format(
        cidade=poi["cidade"], uf=poi.get("uf") or "", nome=poi["nome"],
        onde=poi.get("onde") or "(sem endereço confirmado)", corpo=corpo))
    return {"paginas": len(escolhidas), "resposta": d,
            "urls": [a["url"] for a in escolhidas]}


# ─────────────────────────────────────────────────────────── o banco ────────
SQL_ALVOS = """
    select p.id, p.nome, p.cidade, p.uf, p.cnpj, p.telefone,
           coalesce(l.logradouro,'') as via, coalesce(l.numero,'') as numero,
           coalesce(l.bairro,'') as bairro, coalesce(l.forca,'sem') as forca
      from radar_comercial.pois p
      left join radar_comercial.logradouro_resolvido l on l.poi_id = p.id
     where p.fundido_em is null and coalesce(p.nome,'') <> ''
       and (coalesce(p.cnpj,'') = '' or coalesce(p.telefone,'') = '')
       and p.ia_resposta is null
       and length(coalesce(p.nome,'')) >= %s
       %s
     order by p.id
"""


def alvos(cur, cidade: str, limite: int, fontes: list, nome_min: int) -> list:
    filtro, args = "", [nome_min]
    if cidade:
        filtro += (" and translate(upper(coalesce(p.cidade,'')), "
                   "'ÁÀÂÃÉÊÍÓÔÕÚÜÇ','AAAAEEIOOOUUC') = "
                   "translate(upper(%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ','AAAAEEIOOOUUC')")
        args.append(cidade)
    if fontes:
        filtro += " and p.fonte = any(%s)"
        args.append(fontes)
    sql = SQL_ALVOS % ("%s", filtro)
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, args)
    saida = []
    for pid, nome, cid, uf, cnpj, tel, via, num, bairro, forca in cur.fetchall():
        # ENDEREÇO INFERIDO NÃO ANCORA A BUSCA. `indicio` veio da coordenada e
        # pode ser a rua vizinha; buscar por ele faria o modelo confirmar o erro.
        onde = ""
        if forca == "prova" and via:
            onde = via + ((", %s" % num) if num else "")
            if bairro:
                onde += " - %s" % bairro
        saida.append({"id": pid, "nome": nome, "cidade": cid, "uf": uf,
                      "onde": onde, "tem_cnpj": bool(cnpj),
                      "tem_telefone": bool(tel)})
    return saida


def gravar(con, cur, poi: dict, d: dict, cod_municipio: str) -> dict:
    campos, valores, notas = [], [], []
    fontes = d.get("fontes") or {}

    cnpj = re.sub(r"\D", "", str(d.get("cnpj") or ""))
    if cnpj:
        if not cnpj_valido(cnpj):
            notas.append("cnpj com dígito verificador inválido: %s" % cnpj)
        else:
            r = conferir_na_receita(cur, cnpj, cod_municipio)
            if not r["existe"]:
                conf, nota = 0.3, "não está na Receita"
            elif str(r.get("municipio") or "") == str(cod_municipio or ""):
                conf, nota = 0.9, "confirmado na Receita, mesmo município"
            else:
                conf = 0.5
                nota = ("na Receita, mas em %s — pode ser a matriz da rede"
                        % (r.get("municipio_nome") or r.get("municipio")))
            notas.append("cnpj %s: %s" % (cnpj, nota))
            campos += ["cnpj = coalesce(nullif(btrim(cnpj),''), %s)",
                       "cnpj_conf = coalesce(cnpj_conf, %s)"]
            valores += [cnpj, conf]
            if r["existe"] and r.get("razao_social"):
                campos.append("razao_social = coalesce(nullif(btrim(razao_social),''), %s)")
                valores.append(str(r["razao_social"])[:300])

    for coluna, chave in (("telefone", "telefone"), ("website", "site"),
                          ("instagram", "instagram"), ("facebook", "facebook")):
        v = d.get(chave)
        if v and str(v).strip().lower() not in ("", "null", "none"):
            campos.append("%s = coalesce(nullif(btrim(%s),''), %%s)"
                          % (coluna, coluna))
            valores.append(str(v).strip()[:400])

    campos.append("ia_resposta = %s")
    valores.append(json.dumps(d, ensure_ascii=False)[:8000])
    if fontes:
        campos.append("fontes_web = coalesce(nullif(btrim(fontes_web),''), %s)")
        valores.append(json.dumps(fontes, ensure_ascii=False)[:2000])

    cur.execute("update radar_comercial.pois set %s where id = %%s"
                % ", ".join(campos), valores + [poi["id"]])
    con.commit()
    return {"notas": notas, "campos": len(campos) - 1}


def rodar(cidade="", cod="", limite=0, fontes=None, nome_min=12,
          aplicar=False) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    lista = alvos(cur, cidade, limite, fontes or [], nome_min)
    _log("   %d POIs sem CNPJ ou sem telefone, ainda não perguntados" % len(lista))
    _log("   catálogo: %s" % sd.resumo().replace("\n", " · "))
    if not lista:
        con.close()
        return {"alvos": 0}
    if not aplicar:
        _log("   (ensaio: nada buscado nem gravado. Use --aplicar)")
        for a in lista[:5]:
            _log("      %-34s %s" % (a["nome"][:34], a["onde"][:44]))
        con.close()
        return {"alvos": len(lista), "gravados": 0}

    placar = Counter()
    t0 = time.time()
    # A busca é rede: várias ao mesmo tempo. A gravação é do laço principal,
    # com um cursor só — cursor não atravessa thread.
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        for poi, achado in zip(lista, ex.map(investigar, lista)):
            d = achado.get("resposta")
            if not isinstance(d, dict):
                placar["sem_resposta"] += 1
                continue
            preenchidos = [k for k in ("cnpj", "telefone", "site", "instagram",
                                       "facebook", "ramo") if d.get(k)]
            if not preenchidos:
                placar["nada_confirmado"] += 1
            info = gravar(con, cur, poi, d, cod)
            placar["gravados"] += 1
            for k in preenchidos:
                placar["campo_" + k] += 1
            _log("      %-30s %d páginas · %s"
                 % (poi["nome"][:30], achado["paginas"],
                    ", ".join(preenchidos) or "nada"))
            for n in info["notas"]:
                _log("         %s" % n)

    dt = time.time() - t0
    _log("\n   %d POIs · %.1f min · %.1f s por POI"
         % (len(lista), dt / 60.0, dt / max(1, len(lista))))
    for k, v in placar.most_common():
        _log("      %-22s %5d" % (k, v))
    con.close()
    return {"alvos": len(lista), **dict(placar)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="CNPJ, telefone e redes: SearXNG busca, a Spark lê.")
    p.add_argument("--cidade", default="")
    p.add_argument("--municipio", default="",
                   help="código IBGE — usado para conferir o CNPJ na Receita")
    p.add_argument("--fonte", action="append", default=[],
                   help="restringe a POIs desta fonte; pode repetir")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--nome-minimo", type=int, default=12,
                   help="ignora nome curto demais para buscar: 'LOJA' e "
                        "'MERCADO' trazem a cidade inteira")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    _log("▶ complemento pela web%s" % ((" · %s" % a.cidade) if a.cidade else ""))
    rodar(a.cidade, a.municipio, a.limite, a.fonte, a.nome_minimo, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
