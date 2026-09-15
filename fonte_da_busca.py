# -*- coding: utf-8 -*-
"""fonte_da_busca.py — de onde vem cada resultado da busca na web, e o post de rede social com data (15/09/2026).

Decisao do dono do produto: "veja dentro da busca web qual a fonte, pq redes sociais da web tem muito peso
inclusive considerando a data das postagens". Ate aqui todo resultado da busca era "a Receita republicada"; na
ligacao 353327 o reel do Instagram da A&M Imports na Rua Santa Catarina, 2767 contava como Receita e a
aprovacao caiu.

  - `classificar(url)`: rede social (Instagram, Facebook, TikTok, YouTube, LinkedIn, Kwai), site de CNPJ (a
    Receita republicada), guia de empresas, iFood, ou site proprio/outro;
  - `data_do_trecho(texto)`: a data do post que o buscador mostra no trecho ("Jan 8, 2026 ·", "8 de jan. de
    2026", "ha 3 meses");
  - `redes_sociais_no_endereco`: os posts NO ENDERECO (filtro do `buscar_web`) com o nome de um registro — fonte
    propria na checagem, e prova recente quando a data do post tem ate 2 anos;
  - `anotar_texto`: o texto da busca para o julgamento com a fonte e a data de cada resultado.
"""
import datetime
import re

import provas_datadas as pdat
from fachada_da_seta import _normal, casar, palavras_do_endereco

#: dominio -> nome da rede (o nome, em minusculas, e a fonte em checagem_veredito.FONTES_INDEPENDENTES)
REDES = {"instagram.com": "Instagram", "facebook.com": "Facebook", "fb.com": "Facebook", "fb.me": "Facebook",
         "tiktok.com": "TikTok", "youtube.com": "YouTube", "youtu.be": "YouTube", "linkedin.com": "LinkedIn",
         "kwai.com": "Kwai"}
SITES_DE_CNPJ = ("cnpj", "casadosdados", "econodata", "advdinamico", "informecadastral", "empresaqui", "consultasocio",
                 "serasaexperian", "transparencia.cc", "brasilapi", "receitaws", "empresascnpj", "listaempresas",
                 "cadastroempresa", "dadosempresa", "consultaempresa", "situacaocadastral", "infoplex", "jusbrasil")
GUIAS = ("solutudo", "apontador", "guiamais", "telelistas", "benditoguia", "cylex", "hotfrog", "yelp", "tripadvisor",
         "encontra", "guiafacil", "doctoralia", "getninjas", "habitissimo", "enfsolar", "infobel", "empresite",
         "paginasamarelas", "listamais", "achei", "guiadecanoas", "waze", "moovit", "google.com/maps", "maps.app")

_MES_PT = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10,
           "nov": 11, "dez": 12}
_MES_EN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
_UNIDADE = (("minuto", 0), ("hora", 0), ("dia", 1), ("semana", 7), ("mes", 30), ("mês", 30), ("ano", 365),
            ("minute", 0), ("hour", 0), ("day", 1), ("week", 7), ("month", 30), ("year", 365))


def classificar(url):
    """(tipo, nome): 'rede social' + a rede; 'site de CNPJ'; 'guia de empresas'; 'iFood'; 'site próprio ou outro'."""
    u = str(url or "").lower()
    for dom, nome in REDES.items():
        if dom in u:
            return "rede social", nome
    if "ifood.com" in u:
        return "iFood", "iFood"
    if any(s in u for s in SITES_DE_CNPJ):
        return "site de CNPJ", "Receita republicada"
    if any(s in u for s in GUIAS):
        return "guia de empresas", None
    return "site próprio ou outro", None


def _data(a, m, d):
    try:
        dt = datetime.date(int(a), int(m), int(d))
    except ValueError:
        return None
    return dt if datetime.date(2005, 1, 1) <= dt <= pdat.hoje() else None


def data_do_trecho(texto):
    """A primeira data de publicacao que o buscador poe no trecho, ou None."""
    s = str(texto or "")
    b = s.lower()
    achadas = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", s):
        achadas.append((m.start(), _data(m.group(1), m.group(2), m.group(3))))
    for m in re.finditer(r"\b([a-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})\b", b):
        if m.group(1) in _MES_EN:
            achadas.append((m.start(), _data(m.group(3), _MES_EN[m.group(1)], m.group(2))))
    for m in re.finditer(r"\b(\d{1,2}) de ([a-zç]{3})[a-zç]*\.? de (\d{4})\b", b):
        if m.group(2) in _MES_PT:
            achadas.append((m.start(), _data(m.group(3), _MES_PT[m.group(2)], m.group(1))))
    for m in re.finditer(r"\b(\d{1,2}) ([a-z]{3})[a-z]*\.? (\d{4})\b", b):
        mes = _MES_EN.get(m.group(2)) or _MES_PT.get(m.group(2))
        if mes:
            achadas.append((m.start(), _data(m.group(3), mes, m.group(1))))
    for m in re.finditer(r"\b(\d{2})/(\d{2})/(\d{4})\b", s):
        achadas.append((m.start(), _data(m.group(3), m.group(2), m.group(1))))
    for m in re.finditer(r"(?:\bh[áa] (\d+|um|uma) ([a-zêç]+))|(?:\b(\d+|um|uma|an?) ([a-zêç]+) (?:atr[áa]s|ago)\b)", b):
        n, unid = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        n = 1 if n in ("um", "uma", "a", "an") else int(n)
        dias = next((d for u, d in _UNIDADE if unid.startswith(u)), None)
        if dias is not None:
            achadas.append((m.start(), pdat.hoje() - datetime.timedelta(days=n * dias)))
    achadas = [(p, d) for p, d in achadas if d]
    return min(achadas)[1] if achadas else None


def redes_sociais_no_endereco(cur, ligacao, ids):
    """REDE SOCIAL CONFIRMADA E FONTE PROPRIA (dono do produto, 15/09/2026). [{rede, poi, url, data, texto}]: o
    resultado da busca NO ENDERECO da instalacao (`no_endereco` do `buscar_web`: mesma rua e numero) que vem de
    rede social e traz o nome de um registro. Sem o nome de nenhum registro nao conta: post de imovel a venda no
    endereco tambem passa no filtro de endereco."""
    cur.execute("select id, coalesce(nome, '') from radar_comercial.pois where id = any(%s)", (list(ids or []),))
    nomes = [(pid, re.sub(r"^[\d.\s/-]+", "", nome)) for pid, nome in cur.fetchall()]
    cur.execute("""select resultados from radar_comercial.busca_web
                    where ligacao = %s and tipo = 'endereco' and not bloqueado and resultados is not null""", (str(ligacao),))
    linhas = cur.fetchall()
    excluir = palavras_do_endereco(cur, ligacao) if linhas else set()
    saida, vistos = [], set()
    for (res,) in linhas:
        for x in res or []:
            if not isinstance(x, dict) or not x.get("no_endereco"):
                continue
            tipo, rede = classificar(x.get("url"))
            if tipo != "rede social":
                continue
            texto = " ".join(("%s %s" % (x.get("titulo") or "", x.get("trecho") or "")).split())
            for pid, nome in nomes:
                if nome and casar(nome, [(rede, texto)], excluir) and (rede, pid, str(x.get("url"))) not in vistos:
                    vistos.add((rede, pid, str(x.get("url"))))
                    dt = data_do_trecho(x.get("trecho")) or data_do_trecho(x.get("titulo"))
                    saida.append({"rede": rede, "poi": pid, "url": x.get("url"), "data": dt.isoformat() if dt else None,
                                  "texto": texto[:300]})
    return saida


def anotar_texto(texto):
    """O texto da busca (`busca_web.texto`) com a FONTE e a data de cada resultado na linha do titulo."""
    linhas = str(texto or "").split("\n")
    saida = []
    for i, l in enumerate(linhas):
        m = re.match(r"^(\s*\d+\. )(.*) — (\S+)\s*$", l)
        if not m:
            saida.append(l)
            continue
        tipo, nome = classificar(m.group(3))
        trecho = linhas[i + 1] if i + 1 < len(linhas) else ""
        dt = data_do_trecho(trecho) if tipo == "rede social" else None
        rot = tipo + ((": " + nome) if nome and tipo != "site de CNPJ" else "") + \
            (" = Receita republicada" if tipo == "site de CNPJ" else "") + \
            ((" · post de %s" % pdat.mes_ano(dt)) if dt else (" · sem data do post" if tipo == "rede social" else ""))
        saida.append("%s[%s] %s — %s" % (m.group(1), rot, m.group(2), m.group(3)))
    return "\n".join(saida)
