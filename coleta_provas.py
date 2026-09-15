# -*- coding: utf-8 -*-
"""coleta_provas.py — a coleta nova de provas por LIGACAO (em TESTE desde 14/09/2026).

Decisoes do dono do produto em 14/09/2026. Para cada ligacao de agua:

1. SUSPEITOS, SEM WEB: os CNPJs ATIVOS (situacao 02) da base da Receita na MESMA
   RUA e MESMO NUMERO da ligacao, no mesmo municipio. Quando a ligacao tem
   complemento, so vale o CNPJ com o mesmo complemento ou sem complemento
   nenhum. A rua e comparada pelo nome normalizado (`resolver_logradouro.norm`,
   sem acento e sem o tipo "RUA/AV/R."); o numero, exato.
2. SERASA para todo suspeito: recorte PNG da ficha, na resolucao nativa, e o
   texto dos campos.
3. CASA DOS DADOS so para CNPJ que NAO esta na nossa base (achado por outra
   fonte). Mesmo formato.
4. REDES SOCIAIS PELO GOOGLE, so dos suspeitos: `<nome> <cidade>
   instagram/facebook/tiktok`, e de cada rede o PRIMEIRO resultado: recorte PNG
   do bloco, texto, URL, a data que o Google mostrar. Um nome por busca; no
   maximo 3 nomes por ligacao.

NADA E GRAVADO NO BANCO AINDA. Cada prova sai no formato da tabela que vira
depois (`nova_prova`): cnpj, fonte, url, texto, campos, png, data_da_prova,
consultado_em, bloqueado. A conexao e so de leitura (`readonly`).

A BASE DA RECEITA NAO TEM INDICE (medido em 14/09/2026): `rf_estabelecimentos`
72,8 M de linhas / 14 GB, `rf_empresas` 6,4 GB, `rf_simples` 4 GB, `rf_socios`
3 GB, e nenhuma delas tem indice. Toda consulta e varredura inteira. Por isso a
consulta e UMA POR MUNICIPIO para todas as ligacoes pedidas (nunca uma por
ligacao), o numero e filtrado no banco, a rua em Python, e cada tabela de
complemento e lida uma vez para o lote todo.

BLOQUEIO: CAPTCHA, desafio ou "trafego incomum" e registrado como bloqueado e a
coleta segue; o IP vai para o castigo e o navegador troca. Nada e resolvido nem
contornado.

Uso (teste, sem gravar):
    python coleta_provas.py --ligacao 354915 --ligacao 2564034 --saida /saida
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
import difflib
import json
import os
import random
import re
import threading
import time
import unicodedata
import urllib.parse

import base_comum as bc
import busca_navegador as bn
import provas_datadas as pdat
import resolver_logradouro as rl
from buscar_web import BLOQUEIO_WEB, bn_proxy_dict, tipo_por_extenso

# ------------------------------------------------------------------ ajustes --

#: A ficha e recortada nesta largura, em densidade 1 (pixel da tela = pixel do PNG).
VIEWPORT = {"width": 1280, "height": 900}
#: Buscas no Google por minuto, somando os navegadores. O pool de IPs do Google
#: bloqueia depois de muitas buscas (a 40/min os bloqueios foram de 8% a 24% em
#: 15 min, madrugada de 14/09/2026).
GOOGLE_POR_MIN = 20
#: Tentativas da MESMA busca no Google. 1: bloqueou, registra e segue.
TENTATIVAS_GOOGLE = 1
#: Nomes buscados por ligacao, no maximo (dono do produto).
NOMES_POR_LIGACAO = 3
#: Paginas por navegador antes de trocar de navegador e de IP.
PAGINAS_POR_SESSAO = 30
#: O IP bloqueado fica uma hora fora (o mesmo castigo da busca web).
CASTIGO_S = 3600
#: Disjuntor do Google: tantas buscas seguidas bloqueadas param o Google na rodada.
DISJUNTOR_GOOGLE = 5
#: Espera maxima pela ficha depois do carregamento.
ESPERA_FICHA_MS = 20000

REDES = {"instagram": "instagram.com", "facebook": "facebook.com", "tiktok": "tiktok.com"}
SERASA = "https://empresas.serasaexperian.com.br/consulta-gratis/%s"
CASA_DOS_DADOS = "https://casadosdados.com.br/solucao/cnpj/%s"
GOOGLE = "https://www.google.com/search?q=%s&hl=pt-BR&gl=br"

SITUACAO = {"01": "NULA", "02": "ATIVA", "03": "SUSPENSA", "04": "INAPTA", "08": "BAIXADA"}
PORTE = {"00": "NAO INFORMADO", "01": "MICRO EMPRESA", "03": "EMPRESA DE PEQUENO PORTE", "05": "DEMAIS"}
#: Palavras de pagina de bloqueio/desafio, alem das da busca web.
BLOQUEIO_PAGINA = tuple(BLOQUEIO_WEB) + (
    "tráfego incomum", "verifique se você é humano", "não sou um robô", "acesso negado", "just a moment",
    "attention required", "request unsuccessful", "pardon our interruption", "checking your browser",
    "verificando seu navegador", "verificando se você é humano", "ddos protection", "cf-challenge")

_trava_log = threading.Lock()


def _log(m):
    with _trava_log:
        print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def agora_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper().strip()


def nova_prova(fonte, ligacao=None, cnpj=None, url=None):
    """A linha da futura tabela de provas. `png` e o caminho do recorte no teste
    (em producao, o objeto no Storage)."""
    return {"ligacao": str(ligacao) if ligacao is not None else None, "cnpj": cnpj, "fonte": fonte,
            "url": url, "texto": None, "campos": {}, "png": None, "data_da_prova": None,
            "consultado_em": agora_iso(), "bloqueado": False, "erro": None, "segundos": None}


# ================================================================ ligacao ==

def ler_ligacoes(con, numeros):
    """{num_ligacao: {...}} do cadastro da Corsan, com o complemento separado."""
    cur = con.cursor()
    cur.execute("""select num_ligacao::text, coalesce(cidade,''), coalesce(nom_logradouro,''), coalesce(nro,''),
                          coalesce(end_ligacao,''), coalesce(cod_cep,''), coalesce(nom_bairro,''),
                          categoria, sit_ligacao, qualificacao
                     from resources_root.cadastro_corsan where num_ligacao = any(%s)""",
                ([int(x) for x in numeros],))
    saida = {}
    for lig, cidade, logr, nro, end, cep, bairro, cat, sit, qual in cur.fetchall():
        saida[lig] = {"ligacao": lig, "cidade": cidade, "logradouro": logr, "nro": nro.strip(),
                      "end_ligacao": end, "cep": re.sub(r"\D", "", cep), "bairro": bairro,
                      "complemento": complemento_da_ligacao(end, nro, bairro, cidade, cep),
                      "tipo_via": tipo_por_extenso(end), "categoria": cat, "sit_ligacao": sit,
                      "qualificacao": qual}
    return saida


def complemento_da_ligacao(end_ligacao, nro, bairro, cidade, cep):
    """'RUA VIAMAO,565-FUNDOS-MATHIAS VELHO-CANOAS-RS-CEP:92330490' -> 'FUNDOS'.

    O formato da Corsan e '<via>,<nro>[-<complemento>]-<bairro>-<cidade>-RS-CEP:<cep>':
    tira o que se sabe do fim e o numero do comeco; o que sobra e o complemento."""
    if "," not in (end_ligacao or ""):
        return ""
    resto = end_ligacao.split(",", 1)[1].strip()
    resto = re.sub(r"-?\s*CEP:\s*\d*\s*$", "", resto, flags=re.I)
    resto = re.sub(r"-\s*RS\s*$", "", resto, flags=re.I)
    for fim in (cidade, bairro):
        f = sem_acento(fim)
        if f and sem_acento(resto).endswith("-" + f):
            resto = resto[: len(resto) - len(f) - 1]
    n = (nro or "").strip()
    if n and resto.startswith(n):
        resto = resto[len(n):]
    return resto.strip(" -")


_ABREV_COMPL = {"FDS": "FUNDOS", "FD": "FUNDOS", "FUND": "FUNDOS", "FUNDO": "FUNDOS", "DIR": "DIREITA",
                "ESQ": "ESQUERDA", "DIREITO": "DIREITA", "ESQUERDO": "ESQUERDA", "AP": "APTO", "APT": "APTO", "APARTAMENTO": "APTO", "SL": "SALA",
                "LJ": "LOJA", "CS": "CASA", "BL": "BLOCO", "BLC": "BLOCO", "FT": "FRENTE", "FRT": "FRENTE",
                "TER": "TERREO", "TERR": "TERREO", "PAV": "PAVIMENTO", "GAL": "GALPAO", "GALP": "GALPAO",
                "CJ": "CONJUNTO", "CONJ": "CONJUNTO", "LT": "LOTE", "QD": "QUADRA"}
_VAZIAS_COMPL = {"DE", "DA", "DO", "E", "N", "NO", "NR", "NUM", "NRO", "LADO"}


def _tokens_compl(s):
    t = re.sub(r"[^A-Z0-9]+", " ", sem_acento(s)).split()
    saida = set()
    for x in t:
        if x in _VAZIAS_COMPL:
            continue
        if x.isdigit():
            x = x.lstrip("0") or "0"
        saida.add(_ABREV_COMPL.get(x, x))
    return saida


def comparar_complemento(da_ligacao, do_cnpj):
    """(relacao, vale). Sem complemento na ligacao, todo CNPJ do numero vale; com
    complemento, vale o igual e o CNPJ sem complemento (dono do produto)."""
    a, b = _tokens_compl(da_ligacao), _tokens_compl(do_cnpj)
    if not a:
        return "ligacao_sem_complemento", True
    if not b:
        return "cnpj_sem_complemento", True
    na, nb_ = {x for x in a if x.isdigit()}, {x for x in b if x.isdigit()}
    if na and nb_ and na != nb_:
        return "diferente", False
    if a == b or a <= b or b <= a:
        return "igual", True
    return "diferente", False


# ================================================================ Receita ==

_CACHE_RUA = {}


def chave_rua(tipo, logradouro):
    """O nome da via sem tipo e sem acento, na forma canonica da skill de logradouro."""
    cru = ("%s %s" % (tipo or "", logradouro or "")).strip()
    if cru not in _CACHE_RUA:
        _CACHE_RUA[cru] = rl.so_nome(rl.norm(cru))
    return _CACHE_RUA[cru]


def chave_numero(n):
    return re.sub(r"\s+", "", (n or "").upper()).lstrip("0")


def _data_rf(s):
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s) and s != "00000000":
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return None


def codigos_do_municipio(cur, cidade):
    """Codigos da Receita (`rf_municipios`) com este nome. A tabela nao tem UF: o
    filtro `uf = 'RS'` da consulta e que separa os homonimos de outros estados."""
    cur.execute("select codigo, descricao from resources_root.rf_municipios")
    alvo = sem_acento(cidade)
    return sorted({c for c, d in cur.fetchall() if sem_acento(d) == alvo})


COLUNAS_ESTAB = ("cnpj_basico", "cnpj_ordem", "cnpj_dv", "matriz_filial", "nome_fantasia", "situacao_cadastral",
                 "data_situacao", "motivo_situacao", "situacao_especial", "data_situacao_especial", "data_inicio",
                 "cnae_principal", "cnae_secundaria", "tipo_logradouro", "logradouro", "numero", "complemento",
                 "bairro", "cep", "uf", "municipio", "ddd1", "tel1", "ddd2", "tel2", "ddd_fax", "fax", "email")


def ativos_no_numero(cur, codigos, numeros, uf="RS"):
    """Os estabelecimentos ATIVOS do municipio com um dos numeros pedidos.

    VARREDURA INTEIRA de 14 GB: a tabela nao tem indice. Uma consulta por
    municipio para o lote todo de ligacoes, nunca uma por ligacao."""
    sql = ("select %s from resources_root.rf_estabelecimentos "
           "where uf = %%s and municipio = any(%%s) and situacao_cadastral in ('02', '2')"
           % ", ".join(COLUNAS_ESTAB))
    args = [uf, list(codigos)]
    if numeros:
        sql += " and ltrim(btrim(numero), '0') = any(%s)"
        args.append(sorted({chave_numero(n) for n in numeros if chave_numero(n)}))
    cur.execute(sql, args)
    return [dict(zip(COLUNAS_ESTAB, r)) for r in cur.fetchall()]


def casar_com_ligacao(lig, estabs):
    """(suspeitos, descartados) de uma ligacao entre os estabelecimentos do municipio.

    Mesmo numero (exato) e mesma rua: o nome normalizado igual, ou o MESMO CEP
    com o nome parecido (grafia: 'HORTENSIAS'/'HORTENCIAS', abreviacao que a
    normalizacao nao desfaz). `casou_por` diz qual."""
    rua = chave_rua(lig["tipo_via"] or "RUA", lig["logradouro"])
    num = chave_numero(lig["nro"])
    suspeitos, descartados = [], []
    if not num or not rua:
        return suspeitos, descartados
    for e in estabs:
        if chave_numero(e["numero"]) != num:
            continue
        rua_e = chave_rua(e["tipo_logradouro"], e["logradouro"])
        cep_e = re.sub(r"\D", "", e["cep"] or "")
        if rua_e == rua:
            casou = "rua_e_numero"
        elif len(lig["cep"]) == 8 and cep_e == lig["cep"] and (
                difflib.SequenceMatcher(None, rua_e.replace(" ", ""), rua.replace(" ", "")).ratio() >= 0.85
                or (min(len(rua_e), len(rua)) >= 6 and (rua_e in rua or rua in rua_e))):
            casou = "cep_numero_e_rua_parecida"
        else:
            continue
        relacao, vale = comparar_complemento(lig["complemento"], e["complemento"])
        item = dict(e, casou_por=casou, complemento_relacao=relacao)
        (suspeitos if vale else descartados).append(item)
    return suspeitos, descartados


def completar_da_receita(cur, basicos, tempos):
    """Razao social, natureza, porte, Simples/MEI e socios dos CNPJs basicos.

    Uma leitura de cada tabela para o lote inteiro (sem indice, cada uma e
    varredura inteira: 6,4 GB, 4 GB e 3 GB)."""
    b = sorted(set(basicos))
    saida = {x: {"empresa": None, "simples": None, "socios": []} for x in b}
    if not b:
        return saida
    t0 = time.time()
    cur.execute("""select e.cnpj_basico, e.razao_social, e.natureza_juridica, e.porte, e.capital_social,
                          e.qualificacao_responsavel, e.ente_federativo
                     from resources_root.rf_empresas e join unnest(%s::text[]) t(b) on e.cnpj_basico = t.b""", (b,))
    for cb, rs, nj, porte, cap, qual, ente in cur.fetchall():
        saida[cb]["empresa"] = {"razao_social": rs, "natureza_juridica": nj, "porte": porte,
                                "capital_social": cap, "qualificacao_responsavel": qual, "ente_federativo": ente}
    tempos["rf_empresas_s"] = round(time.time() - t0, 1)
    t0 = time.time()
    cur.execute("""select s.cnpj_basico, s.opcao_simples, s.data_opcao_simples, s.data_exclusao_simples,
                          s.opcao_mei, s.data_opcao_mei, s.data_exclusao_mei
                     from resources_root.rf_simples s join unnest(%s::text[]) t(b) on s.cnpj_basico = t.b""", (b,))
    for cb, os_, dos, des, om, dom, dem in cur.fetchall():
        saida[cb]["simples"] = {"opcao_simples": os_, "data_opcao_simples": _data_rf(dos),
                                "data_exclusao_simples": _data_rf(des), "opcao_mei": om,
                                "data_opcao_mei": _data_rf(dom), "data_exclusao_mei": _data_rf(dem)}
    tempos["rf_simples_s"] = round(time.time() - t0, 1)
    t0 = time.time()
    cur.execute("""select s.cnpj_basico, s.nome_socio
                     from resources_root.rf_socios s join unnest(%s::text[]) t(b) on s.cnpj_basico = t.b""", (b,))
    for cb, nome in cur.fetchall():
        if nome and nome.strip() not in saida[cb]["socios"]:
            saida[cb]["socios"].append(nome.strip())
    tempos["rf_socios_s"] = round(time.time() - t0, 1)
    return saida


def _tabelas_pequenas(cur):
    cur.execute("select codigo, descricao from resources_root.rf_cnaes")
    cnaes = dict(cur.fetchall())
    cur.execute("select codigo, descricao from resources_root.rf_naturezas")
    naturezas = dict(cur.fetchall())
    cur.execute("select codigo, descricao from resources_root.rf_motivos")
    motivos = dict(cur.fetchall())
    return cnaes, naturezas, motivos


def _telefone(ddd, tel):
    ddd, tel = (ddd or "").strip(), (tel or "").strip()
    if not tel.strip("0"):                     # a base traz "(0000) 00000000" no lugar de vazio
        return None
    return ("(%s) %s" % (ddd, tel)) if ddd.strip("0") else tel


def nome_para_busca(nome_fantasia, razao_social):
    """O nome fantasia; sem ele, a razao social sem o CNPJ/CPF do MEI e sem a
    forma societaria ('33.721.656 DOELI X' -> 'DOELI X')."""
    n = (nome_fantasia or "").strip()
    if not n:
        n = (razao_social or "").strip()
        n = re.sub(r"^\s*\d{2}\.?\d{3}\.?\d{3}\s+", "", n)          # MEI novo: CNPJ basico na frente
        n = re.sub(r"\s+\d{11}\s*$", "", n)                          # MEI antigo: CPF no fim
    n = re.sub(r"[\s\-]+(LTDA|EPP|ME|EIRELI|S/?A|SLU|SS)\.?(?=(\s|-|$))", " ", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" -.")


def ficha_receita(e, extra, cnaes, naturezas, motivos, base_receita):
    """O suspeito com tudo o que a base publica tem, no formato de prova."""
    cnpj = "%s%s%s" % (e["cnpj_basico"], e["cnpj_ordem"], e["cnpj_dv"])
    emp = extra.get("empresa") or {}
    simp = extra.get("simples") or {}
    secund = [c.strip() for c in (e["cnae_secundaria"] or "").split(",") if c.strip()]
    campos = {
        "cnpj": cnpj,
        "cnpj_formatado": "%s.%s.%s/%s-%s" % (cnpj[:2], cnpj[2:5], cnpj[5:8], cnpj[8:12], cnpj[12:]),
        "razao_social": emp.get("razao_social"),
        "nome_fantasia": (e["nome_fantasia"] or "").strip() or None,
        "matriz_filial": {"1": "MATRIZ", "2": "FILIAL"}.get((e["matriz_filial"] or "").strip(), e["matriz_filial"]),
        "situacao_cadastral": SITUACAO.get((e["situacao_cadastral"] or "").zfill(2), e["situacao_cadastral"]),
        "data_situacao": _data_rf(e["data_situacao"]),
        "motivo_situacao": motivos.get(e["motivo_situacao"], e["motivo_situacao"]),
        "situacao_especial": (e["situacao_especial"] or "").strip() or None,
        "data_inicio": _data_rf(e["data_inicio"]),
        "cnae_principal": {"codigo": e["cnae_principal"], "descricao": cnaes.get(e["cnae_principal"])},
        "cnaes_secundarios": [{"codigo": c, "descricao": cnaes.get(c)} for c in secund],
        "natureza_juridica": {"codigo": emp.get("natureza_juridica"),
                              "descricao": naturezas.get(emp.get("natureza_juridica"))},
        "porte": PORTE.get((emp.get("porte") or "").zfill(2), emp.get("porte")),
        "capital_social": emp.get("capital_social"),
        "endereco": {"tipo_logradouro": e["tipo_logradouro"], "logradouro": e["logradouro"], "numero": e["numero"],
                     "complemento": (e["complemento"] or "").strip() or None, "bairro": e["bairro"], "cep": e["cep"],
                     "municipio_rf": e["municipio"], "uf": e["uf"]},
        "telefones": [t for t in (_telefone(e["ddd1"], e["tel1"]), _telefone(e["ddd2"], e["tel2"])) if t],
        "fax": _telefone(e["ddd_fax"], e["fax"]),
        "email": (e["email"] or "").strip() or None,
        "simples": simp.get("opcao_simples"),
        "mei": simp.get("opcao_mei"),
        "simples_detalhe": simp or None,
        "socios": extra.get("socios") or [],
        "casou_por": e["casou_por"],
        "complemento_relacao": e["complemento_relacao"],
    }
    campos["nome_para_busca"] = nome_para_busca(campos["nome_fantasia"], campos["razao_social"])
    p = nova_prova("receita", cnpj=cnpj)
    p["campos"] = campos
    p["texto"] = "; ".join(x for x in (
        campos["cnpj_formatado"], campos["razao_social"], campos["nome_fantasia"], campos["situacao_cadastral"],
        "inicio %s" % campos["data_inicio"], "%s %s" % (campos["cnae_principal"]["codigo"],
                                                        campos["cnae_principal"]["descricao"] or "")) if x)
    p["data_da_prova"] = base_receita.isoformat() if base_receita else None
    return p


def _ordem_suspeito(p):
    c = p["campos"]
    rel = {"igual": 0, "cnpj_sem_complemento": 1, "ligacao_sem_complemento": 1}.get(c["complemento_relacao"], 2)
    # complemento igual primeiro, depois quem tem nome fantasia, depois o mais novo
    return (rel, 0 if c["nome_fantasia"] else 1, -int((c["data_inicio"] or "0000-00-00").replace("-", "")))


def suspeitos(con, ligacoes):
    """{ligacao: {"suspeitos": [prova], "descartados_complemento": [...]}}, tempos.

    As ligacoes vem de `ler_ligacoes`. Agrupa por municipio e le cada tabela da
    Receita UMA vez para o lote."""
    tempos = {}
    cur = con.cursor()
    cur.execute("set statement_timeout = '1800s'")
    t0 = time.time()
    cnaes, naturezas, motivos = _tabelas_pequenas(cur)
    try:
        base_receita = pdat._referencia_receita(cur)
    except Exception:                                          # noqa: BLE001
        con.rollback()
        base_receita = None
    tempos["tabelas_pequenas_s"] = round(time.time() - t0, 1)
    por_cidade = {}
    for lig in ligacoes.values():
        por_cidade.setdefault(sem_acento(lig["cidade"]), []).append(lig)
    brutos = {}
    for cidade, ligs in por_cidade.items():
        codigos = codigos_do_municipio(cur, cidade)
        t0 = time.time()
        estabs = ativos_no_numero(cur, codigos, [l["nro"] for l in ligs]) if codigos else []
        tempos["rf_estabelecimentos_%s_s" % cidade.lower().replace(" ", "_")] = round(time.time() - t0, 1)
        tempos["ativos_nos_numeros_%s" % cidade.lower().replace(" ", "_")] = len(estabs)
        t0 = time.time()
        for lig in ligs:
            brutos[lig["ligacao"]] = casar_com_ligacao(lig, estabs)
        tempos["casar_em_python_s"] = round(tempos.get("casar_em_python_s", 0) + time.time() - t0, 2)
        _log("   Receita %s: codigos %s, %d ativos nos numeros pedidos" % (cidade, codigos, len(estabs)))
    basicos = [e["cnpj_basico"] for s, _d in brutos.values() for e in s]
    extra = completar_da_receita(cur, basicos, tempos)
    saida = {}
    for lig, (sus, desc) in brutos.items():
        provas = [ficha_receita(e, extra.get(e["cnpj_basico"], {}), cnaes, naturezas, motivos, base_receita)
                  for e in sus]
        for p in provas:
            p["ligacao"] = lig
        provas.sort(key=_ordem_suspeito)
        saida[lig] = {"suspeitos": provas,
                      "descartados_complemento": [{"cnpj": "%s%s%s" % (e["cnpj_basico"], e["cnpj_ordem"], e["cnpj_dv"]),
                                                   "nome_fantasia": e["nome_fantasia"],
                                                   "complemento": e["complemento"]} for e in desc]}
    con.rollback()
    return saida, tempos


def cnpjs_fora_da_base(con, cnpjs):
    """Os CNPJs (14 digitos) que NAO estao em `rf_estabelecimentos`: esses vao para
    a Casa dos Dados. Sem indice e uma varredura inteira dos 14 GB por chamada;
    chamar uma vez por lote."""
    alvo = sorted({re.sub(r"\D", "", c) for c in cnpjs if len(re.sub(r"\D", "", c)) == 14})
    if not alvo:
        return []
    cur = con.cursor()
    cur.execute("set statement_timeout = '1800s'")
    cur.execute("""select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv
                     from resources_root.rf_estabelecimentos e
                     join unnest(%s::text[]) t(c) on e.cnpj_basico = left(t.c, 8)
                      and e.cnpj_ordem = substr(t.c, 9, 4) and e.cnpj_dv = right(t.c, 2)""", (alvo,))
    na_base = {r[0] for r in cur.fetchall()}
    con.rollback()
    return [c for c in alvo if c not in na_base]


# ============================================================== navegador ==

class Navegador:
    """Um Camoufox por thread, com um proxy fixo, reaproveitado entre paginas.

    Como a `buscar_web.SessaoQuente`, mas com a pagina exposta (os recortes
    precisam dela) e a janela de 1280 px em densidade 1. Troca navegador e IP a
    cada `PAGINAS_POR_SESSAO`, no erro e no bloqueio; o IP bloqueado fica de
    castigo. `aquecer_google`: abre a pagina inicial do Google antes (cookies)."""

    def __init__(self, proximo_ip, aquecer_google=False, paginas_por_sessao=PAGINAS_POR_SESSAO):
        self.proximo_ip = proximo_ip
        self.aquecer_google = aquecer_google
        self.paginas_por_sessao = paginas_por_sessao
        self.local = threading.local()
        self.castigo = {}
        self.trava = threading.Lock()
        self.aberturas = 0

    def _ip(self):
        agora = time.time()
        px = self.proximo_ip()
        for _ in range(200):
            with self.trava:
                if self.castigo.get(px, 0) <= agora:
                    return px
            px = self.proximo_ip()
        return px

    def _abrir(self):
        from camoufox.sync_api import Camoufox
        ultimo = None
        for _ in range(3):
            px = self._ip()
            cm = Camoufox(headless=True, proxy=bn_proxy_dict(px), geoip=True, locale="pt-BR")
            try:
                nav = cm.__enter__()
                page = nav.new_page(viewport=dict(VIEWPORT), device_scale_factor=1, color_scheme="light")
                self.local.s = {"cm": cm, "px": px, "usos": 0, "page": page}
                self.aberturas += 1
                if self.aquecer_google:
                    try:
                        page.goto("https://www.google.com.br/?hl=pt-BR", timeout=45000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                    except Exception:                          # noqa: BLE001
                        pass
                return page
            except Exception as e:                             # noqa: BLE001
                ultimo = e
                try:
                    cm.__exit__(None, None, None)
                except Exception:                              # noqa: BLE001
                    pass
                self.local.s = None
        raise RuntimeError("abrir o Camoufox: %s: %s" % (type(ultimo).__name__, str(ultimo)[:160]))

    def pagina(self):
        s = getattr(self.local, "s", None)
        if s is None or s["usos"] >= self.paginas_por_sessao:
            self.fechar()
            self._abrir()
            s = self.local.s
        s["usos"] += 1
        return s["page"]

    def ip(self):
        s = getattr(self.local, "s", None)
        return urllib.parse.urlsplit(s["px"]).hostname if s and s.get("px") else None

    def bloqueou(self):
        s = getattr(self.local, "s", None)
        if s and s.get("px"):
            with self.trava:
                self.castigo[s["px"]] = time.time() + CASTIGO_S
        self.fechar()

    def fechar(self):
        s = getattr(self.local, "s", None)
        self.local.s = None
        if s:
            try:
                s["cm"].__exit__(None, None, None)
            except Exception:                                  # noqa: BLE001
                pass


class RitmoGoogle:
    """Intervalo minimo entre buscas no Google, somando as threads, e o disjuntor."""

    def __init__(self, por_min=GOOGLE_POR_MIN):
        self.intervalo = 60.0 / por_min
        self.proxima = 0.0
        self.trava = threading.Lock()
        self.seguidas_bloqueadas = 0
        self.inicios = []

    def esperar(self):
        with self.trava:
            agora = time.time()
            minha = max(agora, self.proxima)
            self.proxima = minha + self.intervalo
        time.sleep(max(0.0, minha - agora) + random.uniform(0.0, 1.0))
        with self.trava:
            self.inicios.append(time.time())

    def resultado(self, bloqueado):
        with self.trava:
            self.seguidas_bloqueadas = self.seguidas_bloqueadas + 1 if bloqueado else 0

    @property
    def aberto(self):
        return self.seguidas_bloqueadas >= DISJUNTOR_GOOGLE

    def por_minuto(self):
        with self.trava:
            if len(self.inicios) < 2:
                return None
            return round(60.0 * (len(self.inicios) - 1) / max(1e-6, self.inicios[-1] - self.inicios[0]), 1)


def _texto_da_pagina(page):
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:                                          # noqa: BLE001
        return ""


def parece_bloqueio(url, corpo, status=None):
    baixo = (corpo or "").lower()
    if status in (403, 429, 503):
        return "http %s" % status
    if "/sorry/" in (url or ""):
        return "google /sorry/"
    for b in BLOQUEIO_PAGINA:
        if b in baixo:
            return b
    return None


def esperar_estabilizar(page, maximo_ms=6000):
    """Espera a altura da pagina parar de mudar (anuncio, painel do Google que
    carrega depois) para o recorte nao sair deslocado."""
    antes = -1
    fim = time.time() + maximo_ms / 1000.0
    while time.time() < fim:
        h = page.evaluate("() => document.documentElement.scrollHeight")
        if h == antes:
            return h
        antes = h
        page.wait_for_timeout(700)
    return antes


def recortar(page, caixa, caminho, margem=12):
    """PNG da regiao `caixa` ({x, y, w, h} em coordenadas da pagina), densidade 1."""
    dim = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
    x = max(0, int(caixa["x"] - margem))
    y = max(0, int(caixa["y"] - margem))
    w = int(min(dim[0] - x, caixa["w"] + 2 * margem + (caixa["x"] - margem - x)))
    h = int(min(dim[1] - y, caixa["h"] + 2 * margem + (caixa["y"] - margem - y)))
    png = page.screenshot(clip={"x": x, "y": y, "width": w, "height": h}, full_page=True, type="png",
                          animations="disabled")
    with open(caminho, "wb") as f:
        f.write(png)
    return {"arquivo": os.path.basename(caminho), "largura": w, "altura": h, "bytes": len(png)}


# ================================================================= Serasa ==

JS_SERASA = r"""() => {
  const txt = (e) => (e ? (e.innerText || '') : '').replace(/[ \t]+/g, ' ').trim();
  const folha = (re, tags) => Array.from(document.querySelectorAll(tags)).find(e => re.test((e.textContent || '').trim()));
  const titulo = folha(/^Resultados da consulta/i, 'p,h1,h2,h3,span');
  const loc = folha(/^Localiza..o e contato/i, 'h1,h2,h3,p');
  if (!titulo || !loc) return null;
  const cartao = (e) => {
    const c = e.closest('[class*="contentData"]');
    if (c) return c;
    let x = e;
    while (x.parentElement && x.getBoundingClientRect().width < 600) x = x.parentElement;
    return x;
  };
  const a = cartao(titulo), b = cartao(loc);
  const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
  const top = Math.min(ra.top, rb.top), bottom = Math.max(ra.bottom, rb.bottom);
  const left = Math.min(ra.left, rb.left), right = Math.max(ra.right, rb.right);
  const caixa = {x: left + window.scrollX, y: top + window.scrollY, w: right - left, h: bottom - top};
  const escopo = a.closest('section') || a.parentElement;
  const cartoes = Array.from(escopo.querySelectorAll('[class*="contentData"]'))
      .filter(c => { const r = c.getBoundingClientRect(); return r.top >= top - 1 && r.bottom <= bottom + 1; });
  const campos = [];
  for (const li of escopo.querySelectorAll('li')) {
    const ps = li.querySelectorAll('p');
    if (ps.length >= 2) campos.push([txt(ps[0]), txt(ps[1]).replace(/\s+/g, ' ')]);
  }
  const h1 = escopo.querySelector('h1');
  const sobre = escopo.querySelector('[class*="businessDescription"]');
  return {caixa, campos, titulo: txt(h1), sobre: txt(sobre),
          texto: (cartoes.length ? cartoes : [a, b]).map(txt).join('\n\n')};
}"""

_CAMPOS_SERASA = (("razão social", "razao_social"), ("nome fantasia", "nome_fantasia"),
                  ("data de fundação", "fundacao"), ("situação cadastral", "situacao"),
                  ("natureza jurídica", "natureza_juridica"), ("matriz/filial", "matriz_filial"),
                  ("atividade econômica principal", "cnae_principal"),
                  ("atividade econômica secundária", "cnae_secundaria"), ("logradouro", "logradouro"),
                  ("bairro", "bairro"), ("cep", "cep"), ("município", "municipio"), ("uf", "uf"),
                  ("telefone", "telefone"), ("e-mail", "email"), ("email", "email"))


def _mapear(pares, mapa):
    campos = {"brutos": {}}
    for rotulo, valor in pares:
        campos["brutos"][rotulo] = valor
        r = rotulo.lower().rstrip(":").strip()
        for chave_rotulo, chave in mapa:
            if chave_rotulo in r and chave not in campos:
                campos[chave] = valor
                break
    return campos


def _ir(page, url):
    """(status, erro) do carregamento."""
    try:
        resp = page.goto(url, timeout=60000, wait_until="domcontentloaded")
        return (resp.status if resp else None), None
    except Exception as e:                                     # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:160])


def coletar_serasa(nav, cnpj, pasta, ligacao=None):
    """A ficha gratuita do Serasa: `serasa_<cnpj>.png` + `.json` em `pasta`."""
    cnpj = re.sub(r"\D", "", cnpj)
    p = nova_prova("serasa", ligacao=ligacao, cnpj=cnpj, url=SERASA % cnpj)
    t0 = time.time()
    try:
        page = nav.pagina()
    except Exception as e:                                     # noqa: BLE001
        p["erro"] = str(e)[:200]
        return _fechar_prova(p, t0, pasta, "serasa_%s" % cnpj)
    status, erro = _ir(page, p["url"])
    dados = None
    if not erro:
        try:
            page.wait_for_selector("text=Resultados da consulta", timeout=ESPERA_FICHA_MS)
        except Exception:                                      # noqa: BLE001
            pass
        try:
            esperar_estabilizar(page, 5000)
            page.evaluate("() => document.querySelectorAll('.grecaptcha-badge').forEach(e => e.style.visibility = 'hidden')")
            dados = page.evaluate(JS_SERASA)
        except Exception as e:                                 # noqa: BLE001
            erro = "%s: %s" % (type(e).__name__, str(e)[:160])
    p["url"] = page.url if (page.url or "").startswith("http") else p["url"]
    if dados:
        campos = _mapear(dados["campos"], _CAMPOS_SERASA)
        campos["titulo"] = dados["titulo"]
        campos["sobre"] = dados["sobre"]
        if campos.get("telefone", "").startswith("*"):
            campos["telefone"] = "oculto pelo Serasa"
        p["campos"] = campos
        p["texto"] = dados["texto"]
        try:
            p["png"] = recortar(page, dados["caixa"], os.path.join(pasta, "serasa_%s.png" % cnpj))
        except Exception as e:                                 # noqa: BLE001
            p["erro"] = "recorte: %s: %s" % (type(e).__name__, str(e)[:160])
    else:
        corpo = _texto_da_pagina(page)
        motivo = parece_bloqueio(page.url, corpo, status)
        if motivo:
            p["bloqueado"] = True
            p["erro"] = "bloqueio: %s" % motivo
            _print_da_falha(page, pasta, "serasa_%s_bloqueio.png" % cnpj)
            nav.bloqueou()
        else:
            p["erro"] = erro or "ficha nao encontrada (http %s)" % status
            p["campos"] = {"inicio_da_pagina": corpo[:400]}
            if erro:
                nav.fechar()
    return _fechar_prova(p, t0, pasta, "serasa_%s" % cnpj)


def _print_da_falha(page, pasta, nome):
    try:
        page.screenshot(path=os.path.join(pasta, nome), full_page=False, type="png")
    except Exception:                                          # noqa: BLE001
        pass


def _fechar_prova(p, t0, pasta, nome_json):
    p["segundos"] = round(time.time() - t0, 1)
    if pasta and nome_json:
        with open(os.path.join(pasta, nome_json + ".json"), "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
    return p


# ========================================================= Casa dos Dados ==

JS_CASA = r"""() => {
  const txt = (e) => (e ? (e.innerText || '') : '').replace(/[ \t]+/g, ' ').trim();
  const rotulos = Array.from(document.querySelectorAll('label')).filter(l => /:\s*$/.test((l.textContent || '').trim()));
  const cnpjLbl = rotulos.find(l => /^CNPJ:/i.test((l.textContent || '').trim()));
  if (!cnpjLbl) return null;
  const coluna = cnpjLbl.closest('.column') || cnpjLbl.parentElement.parentElement;
  const campos = [];
  const blocos = [];
  for (const l of rotulos) {
    if (!coluna.contains(l)) continue;
    const v = [];
    let n = l.nextElementSibling;
    while (n && n.tagName !== 'LABEL') {
      if (!(n.className || '').toString().includes('tooltip')) v.push(txt(n));
      n = n.nextElementSibling;
    }
    campos.push([(l.textContent || '').trim().replace(/:\s*$/, ''), v.filter(Boolean).join('\n')]);
    if (!blocos.includes(l.parentElement)) blocos.push(l.parentElement);
  }
  const status = document.querySelector('button.is-success, button.is-danger, button.is-warning');
  const caixas = blocos.map(b => b.getBoundingClientRect());
  if (status) caixas.push(status.getBoundingClientRect());
  const top = Math.min(...caixas.map(r => r.top)), bottom = Math.max(...caixas.map(r => r.bottom));
  const left = Math.min(...caixas.map(r => r.left)), right = Math.max(...caixas.map(r => r.right));
  const h1 = document.querySelector('h1');
  return {caixa: {x: left + window.scrollX, y: top + window.scrollY, w: right - left, h: bottom - top},
          campos, titulo: txt(h1), status: txt(status),
          texto: (status ? txt(status) + '\n' : '') + campos.map(c => c[0] + ': ' + c[1]).join('\n')};
}"""

_CAMPOS_CASA = (("ultima atualiza", "ultima_atualizacao"), ("cnpj", "cnpj"), ("razão social", "razao_social"),
                ("nome fantasia", "nome_fantasia"), ("situação cadastral", "situacao"),
                ("data da situação", "data_situacao"), ("data de abertura", "abertura"),
                ("matriz ou filial", "matriz_filial"), ("natureza jurídica", "natureza_juridica"),
                ("empresa mei", "mei"), ("capital social", "capital_social"), ("logradouro", "logradouro"),
                ("número", "numero"), ("complemento", "complemento"), ("bairro", "bairro"), ("cep", "cep"),
                ("municipio", "municipio"), ("município", "municipio"), ("estado", "uf"), ("email", "email"),
                ("telefone", "telefone"), ("cnae principal", "cnae_principal"),
                ("cnaes secundários", "cnaes_secundarios"), ("simples", "simples"), ("sócios", "socios"))


def _data_br(s):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1)) if m else None


def coletar_casa_dos_dados(nav, cnpj, pasta, ligacao=None):
    """A ficha da Casa dos Dados (so para CNPJ fora da nossa base):
    `casadosdados_<cnpj>.png` + `.json`. A data da prova e a 'Ultima Atualizacao'
    que a pagina declara."""
    cnpj = re.sub(r"\D", "", cnpj)
    p = nova_prova("casadosdados", ligacao=ligacao, cnpj=cnpj, url=CASA_DOS_DADOS % cnpj)
    t0 = time.time()
    try:
        page = nav.pagina()
    except Exception as e:                                     # noqa: BLE001
        p["erro"] = str(e)[:200]
        return _fechar_prova(p, t0, pasta, "casadosdados_%s" % cnpj)
    status, erro = _ir(page, p["url"])
    dados = None
    if not erro:
        try:
            page.wait_for_selector("label:has-text('CNPJ:')", timeout=ESPERA_FICHA_MS)
        except Exception:                                      # noqa: BLE001
            pass
        try:
            esperar_estabilizar(page, 5000)
            dados = page.evaluate(JS_CASA)
        except Exception as e:                                 # noqa: BLE001
            erro = "%s: %s" % (type(e).__name__, str(e)[:160])
    p["url"] = page.url or p["url"]
    if dados:
        campos = _mapear(dados["campos"], _CAMPOS_CASA)
        campos["titulo"] = dados["titulo"]
        campos["status"] = dados["status"]
        if campos.get("socios"):
            campos["socios"] = [s.strip() for s in campos["socios"].split("\n") if s.strip()]
        p["campos"] = campos
        p["texto"] = dados["texto"]
        p["data_da_prova"] = _data_br(campos.get("ultima_atualizacao"))
        try:
            p["png"] = recortar(page, dados["caixa"], os.path.join(pasta, "casadosdados_%s.png" % cnpj))
        except Exception as e:                                 # noqa: BLE001
            p["erro"] = "recorte: %s: %s" % (type(e).__name__, str(e)[:160])
    else:
        corpo = _texto_da_pagina(page)
        motivo = parece_bloqueio(page.url, corpo, status)
        if motivo:
            p["bloqueado"] = True
            p["erro"] = "bloqueio: %s" % motivo
            _print_da_falha(page, pasta, "casadosdados_%s_bloqueio.png" % cnpj)
            nav.bloqueou()
        else:
            p["erro"] = erro or "ficha nao encontrada (http %s)" % status
            p["campos"] = {"inicio_da_pagina": corpo[:400]}
            if erro:
                nav.fechar()
    return _fechar_prova(p, t0, pasta, "casadosdados_%s" % cnpj)


# ========================================================= Google: redes ==

#: O PRIMEIRO RESULTADO DE CADA REDE. Cada titulo (h3 / role=heading dentro de um
#: link) e um resultado; a rede sai do DESTINO do link quando ele e direto, e do
#: NOME DO SITE que o Google escreve no cabecalho ("Instagram · perfil",
#: "Facebook", "TikTok · nome") ou do endereco no `cite` quando o link e o
#: redirecionador opaco `/goto?url=...` — desde set/2026 o Google nao poe mais
#: o destino no href (medido na busca de 14/09/2026). O bloco e o maior
#: ancestral que so contem este resultado (um titulo so, ate 800 px); ele ganha
#: `data-coleta-rede` para o recorte pelo proprio elemento.
JS_REDES = r"""(redes) => {
  const txt = (e) => (e ? (e.innerText || e.textContent || '') : '').trim();
  const NOMES = {instagram: /^instagram\b/i, facebook: /^facebook\b/i, tiktok: /^tiktok\b/i};
  const destino = (href) => {
    try {
      let u = new URL(href, /^http/.test(location.href) ? location.href : 'https://www.google.com/');
      if (/(^|\.)google\./.test(u.hostname)) {
        const q = u.pathname === '/url' ? (u.searchParams.get('q') || u.searchParams.get('url')) : null;
        if (!q) return {opaco: true, href: u.href};
        u = new URL(q);
      }
      return {opaco: false, href: u.href, host: u.hostname.toLowerCase()};
    } catch (e) { return null; }
  };
  const redeDoHost = (h) => { for (const [nome, dom] of Object.entries(redes)) if (h === dom || h.endsWith('.' + dom)) return nome; return null; };
  document.querySelectorAll('[data-coleta-rede]').forEach(e => e.removeAttribute('data-coleta-rede'));
  const raiz = document.querySelector('#rso') || document.querySelector('#search') || document.querySelector('#center_col') || document.body;
  const nTitulos = (el) => el.querySelectorAll('h3, [role="heading"]').length;
  const achados = {}, contagem = {};
  for (const titulo of raiz.querySelectorAll('h3, [role="heading"]')) {
    const a = titulo.closest('a[href]');
    if (!a) continue;
    const d = destino(a.getAttribute('href'));
    if (!d) continue;
    let bloco = a;
    while (bloco.parentElement && bloco.parentElement !== raiz) {
      const p = bloco.parentElement;
      if (nTitulos(p) > 1 || p.getBoundingClientRect().height > 800) break;
      bloco = p;
    }
    const cite = bloco.querySelector('cite');
    let rede = null, pelo = null;
    if (!d.opaco) { rede = redeDoHost(d.host); pelo = 'link'; }
    if (!rede) {
      const linhas = txt(a).split('\n').map(x => x.trim()).filter(Boolean);
      for (const l of linhas.slice(1, 4))
        for (const [nome, re] of Object.entries(NOMES)) if (!rede && re.test(l) && /^[A-Za-z]+(\s*·|$)/.test(l)) { rede = nome; pelo = 'nome_do_site'; }
    }
    if (!rede && cite) {
      for (const [nome, dom] of Object.entries(redes)) if (!rede && txt(cite).toLowerCase().includes(dom)) { rede = nome; pelo = 'cite'; }
    }
    if (!rede) continue;
    contagem[rede] = (contagem[rede] || 0) + 1;
    if (achados[rede]) continue;
    const r = bloco.getBoundingClientRect();
    if (r.width < 50 || r.height < 20) continue;
    bloco.setAttribute('data-coleta-rede', rede);
    const linhas = txt(a).split('\n').map(x => x.trim()).filter(Boolean);
    achados[rede] = {rede, href: d.href, opaco: d.opaco, rede_pelo: pelo, titulo: txt(titulo),
                     site: linhas.find(l => /·/.test(l) || Object.values(NOMES).some(re => re.test(l))) || null,
                     cite: txt(cite), texto: txt(bloco),
                     caixa: {x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height}};
  }
  return {achados, contagem, titulos: raiz.querySelectorAll('h3, [role="heading"]').length};
}"""


def destino_do_goto(page, href, espera_ms=8000):
    """A URL de destino do redirecionador opaco do Google (`/goto?url=...`).

    O Google nao publica mais o destino no link. Abre o redirecionador numa aba
    do MESMO navegador (mesmo proxy, mesmos cookies — e o que o clique de uma
    pessoa faz), pega o primeiro pedido que sai do Google e fecha a aba antes de
    a rede social carregar. Custa um pedido ao Google por rede achada.
    (url, como) — como: 'redirecionamento', 'pagina_de_aviso' ou None."""
    url = urllib.parse.urljoin("https://www.google.com", href)
    aba = page.context.new_page()
    try:
        fora = lambda req: not re.match(r"^https?://([^/]*\.)?(google|gstatic)\.[^/]+/", req.url)  # noqa: E731
        try:
            with aba.expect_event("request", predicate=fora, timeout=espera_ms) as ev:
                aba.evaluate("u => { window.location.href = u; }", url)
            return ev.value.url, "redirecionamento"
        except Exception:                                      # noqa: BLE001
            pass
        try:
            m = re.search(r'href="(https?://(?!(?:[^/"]*\.)?google\.)[^"]+)"', aba.content())
            if m:
                return m.group(1).replace("&amp;", "&"), "pagina_de_aviso"
        except Exception:                                      # noqa: BLE001
            pass
        return None, None
    finally:
        try:
            aba.close()
        except Exception:                                      # noqa: BLE001
            pass


def url_do_cite(rede, cite):
    """'https://www.facebook.com › steiner.radiadores' -> a URL, quando o cite e endereco."""
    c = (cite or "").strip()
    if not c.lower().startswith("http") or REDES[rede] not in c.lower():
        return None
    partes = [x.strip() for x in c.split("›")]
    if any(x in ("...", "…") for x in partes):
        return None
    return partes[0].rstrip("/") + "/" + "/".join(partes[1:]) if len(partes) > 1 else partes[0]


_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10,
          "nov": 11, "dez": 12}


def data_no_texto(texto, consultado_em=None):
    """(o que o Google escreveu, data ISO aproximada). 'há 5 meses' conta a partir
    do dia da consulta; '12 de mar. de 2025' e '12/03/2025' valem como estao."""
    ref = consultado_em or datetime.date.today()
    t = texto or ""
    m = re.search(r"\bh[áa]\s+(\d+|um|uma)\s+(minutos?|horas?|dias?|semanas?|m[eê]s(?:es)?|anos?)\b", t, re.I)
    if m:
        dias = pdat.dias_atras(m.group(0).replace("há", "").replace("Há", ""))
        if dias is not None:
            return m.group(0), (ref - datetime.timedelta(days=dias)).isoformat()
        return m.group(0), None
    m = re.search(r"\b(\d{1,2})\s+de\s+(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-zç]*\.?\s+de\s+(\d{4})\b",
                  t, re.I)
    if m:
        try:
            return m.group(0), datetime.date(int(m.group(3)), _MESES[m.group(2).lower()], int(m.group(1))).isoformat()
        except ValueError:
            return m.group(0), None
    m = re.search(r"\b(\d{2})/(\d{2})/(20\d{2})\b", t)
    if m:
        return m.group(0), "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
    return None, None


def handle_da_url(rede, url):
    try:
        partes = [x for x in urllib.parse.urlsplit(url).path.split("/") if x]
    except Exception:                                          # noqa: BLE001
        return None
    if not partes:
        return None
    if rede == "tiktok":
        return next((x for x in partes if x.startswith("@")), None)
    if rede == "instagram":
        return None if partes[0] in ("p", "reel", "reels", "explore", "stories", "tv") else "@" + partes[0]
    if rede == "facebook":
        return None if partes[0] in ("people", "groups", "watch", "reel", "story.php", "profile.php",
                                     "photo.php", "photos", "events", "p", "share") else partes[0]
    return None


def ler_resultado(rede, achado, consultado_em):
    t = achado.get("texto") or ""
    seg = re.search(r"((?:mais de\s+)?[\d.,]+\s*(?:mil|mi|milh[õo]es|milh[ãa]o)?\+?\s+seguidores)", t, re.I)
    curt = re.search(r"([\d.,]+\s*(?:mil|mi)?\+?\s+(?:curtidas|likes?|like|reações|reacoes|visualizações))", t, re.I)
    quando, data = data_no_texto(t, consultado_em)
    cab = re.search(r"^(Instagram|Facebook|TikTok)\s*·\s*(.+)$", achado.get("site") or t, re.I | re.M)
    handle = handle_da_url(rede, achado.get("url"))
    if not handle and cab and rede in ("instagram", "tiktok") and " " not in cab.group(2).strip():
        handle = "@" + cab.group(2).strip().lstrip("@")
    return {"rede": rede, "titulo": achado.get("titulo"), "linha_do_site": achado.get("site"),
            "cite": achado.get("cite"), "nome_no_cabecalho": cab.group(2).strip() if cab else None,
            "handle": handle, "seguidores": seg.group(1) if seg else None,
            "interacoes": curt.group(1) if curt else None, "data_mostrada": quando, "data_aproximada": data}


def buscar_redes(nav, ritmo, nome, cidade, pasta, n, ligacao=None, cnpjs=None):
    """Uma busca no Google e o primeiro resultado de cada rede.

    Arquivos: `google_<n>_pagina.png` (pagina inteira), `redes_<n>_<rede>.png`
    (bloco), `redes_<n>.json` (a busca e as provas, uma por rede achada)."""
    consulta = "%s %s instagram/facebook/tiktok" % (nome.lower(), cidade.lower())
    registro = {"n": n, "ligacao": ligacao, "nome": nome, "cnpjs": cnpjs or [], "consulta": consulta,
                "url": GOOGLE % urllib.parse.quote(consulta), "bloqueado": False, "erro": None,
                "consultado_em": agora_iso(), "ip": None, "pagina_png": None, "contagem": {},
                "redes": {r: None for r in REDES}, "provas": [], "segundos": None}
    if ritmo.aberto:
        registro["erro"] = "disjuntor do Google aberto (%d bloqueios seguidos)" % DISJUNTOR_GOOGLE
        return _gravar_json(registro, pasta, "redes_%d" % n)
    ritmo.esperar()
    t0 = time.time()
    try:
        page = nav.pagina()
    except Exception as e:                                     # noqa: BLE001
        registro["erro"] = str(e)[:200]
        registro["segundos"] = round(time.time() - t0, 1)
        return _gravar_json(registro, pasta, "redes_%d" % n)
    registro["ip"] = nav.ip()
    status, erro = _ir(page, registro["url"])
    if erro:
        registro["erro"] = erro
        nav.fechar()
        ritmo.resultado(False)
        registro["segundos"] = round(time.time() - t0, 1)
        return _gravar_json(registro, pasta, "redes_%d" % n)
    try:
        page.wait_for_selector("#rso h3, #search h3, #captcha-form, form[action*='sorry']", timeout=12000)
    except Exception:                                          # noqa: BLE001
        pass
    corpo = _texto_da_pagina(page)
    for _ in range(3):                         # "Verificando sua solicitacao" libera sozinho
        if "verificando" not in corpo.lower():
            break
        page.wait_for_timeout(2000)
        corpo = _texto_da_pagina(page)
    registro["url"] = page.url
    motivo = parece_bloqueio(page.url, corpo, status)
    if not motivo and page.query_selector("#captcha-form, iframe[src*='recaptcha']") is not None \
            and page.query_selector("#rso h3, #search h3") is None:
        motivo = "captcha"
    if not motivo:
        esperar_estabilizar(page, 6000)
    nome_pag = "google_%d_pagina.png" % n
    try:
        page.screenshot(path=os.path.join(pasta, nome_pag), full_page=True, type="png", animations="disabled")
        registro["pagina_png"] = nome_pag
    except Exception as e:                                     # noqa: BLE001
        registro["erro"] = "print da pagina: %s" % str(e)[:120]
    ritmo.resultado(bool(motivo))
    if motivo:
        registro["bloqueado"] = True
        registro["erro"] = "bloqueio: %s" % motivo
        nav.bloqueou()
        registro["segundos"] = round(time.time() - t0, 1)
        return _gravar_json(registro, pasta, "redes_%d" % n)
    try:
        res = page.evaluate(JS_REDES, REDES) or {}
    except Exception as e:                                     # noqa: BLE001
        res = {}
        registro["erro"] = "leitura dos resultados: %s" % str(e)[:160]
    registro["contagem"] = res.get("contagem") or {}
    registro["titulos_na_pagina"] = res.get("titulos")
    hoje = datetime.date.today()
    achados = res.get("achados") or {}
    # 1) os recortes, com a pagina parada; 2) so depois o destino dos links
    for rede, achado in achados.items():
        arq = "redes_%d_%s.png" % (n, rede)
        try:
            png = page.locator('[data-coleta-rede="%s"]' % rede).first.screenshot(type="png", animations="disabled")
            with open(os.path.join(pasta, arq), "wb") as f:
                f.write(png)
            achado["png"] = {"arquivo": arq, "bytes": len(png)}
            achado["png"].update(_dimensoes_png(png))
        except Exception as e:                                 # noqa: BLE001
            try:
                achado["png"] = recortar(page, achado["caixa"], os.path.join(pasta, arq), margem=0)
            except Exception as e2:                            # noqa: BLE001
                achado["erro"] = "recorte: %s / %s" % (str(e)[:80], str(e2)[:80])
    for rede, achado in achados.items():
        if not achado.get("opaco"):
            achado["url"], achado["url_por"] = achado["href"], "link"
        else:
            u, como = destino_do_goto(page, achado["href"])
            if not u:
                u, como = url_do_cite(rede, achado.get("cite")), "cite"
            achado["url"], achado["url_por"] = u, (como if u else None)
    for ordem, (rede, achado) in enumerate(achados.items()):
        p = nova_prova("google:%s" % rede, ligacao=ligacao, url=achado.get("url"))
        p["cnpj"] = cnpjs[0] if len(cnpjs or []) == 1 else None
        p["texto"] = achado.get("texto")
        p["campos"] = dict(ler_resultado(rede, achado, hoje), consulta=consulta, nome_buscado=nome,
                           cnpjs_do_nome=cnpjs or [], ordem_na_pagina=ordem, url_por=achado.get("url_por"),
                           rede_pelo=achado.get("rede_pelo"), link_do_google=achado.get("href"),
                           resultados_da_rede_na_pagina=registro["contagem"].get(rede))
        p["data_da_prova"] = p["campos"]["data_aproximada"]
        p["consultado_em"] = registro["consultado_em"]
        p["png"] = achado.get("png")
        p["erro"] = achado.get("erro")
        registro["redes"][rede] = p["campos"]
        registro["provas"].append(p)
    registro["segundos"] = round(time.time() - t0, 1)
    return _gravar_json(registro, pasta, "redes_%d" % n)


def _dimensoes_png(png):
    """Largura e altura lidas do proprio PNG (confere a densidade 1)."""
    try:
        import struct
        w, h = struct.unpack(">II", png[16:24])
        return {"largura": w, "altura": h}
    except Exception:                                          # noqa: BLE001
        return {}


def _gravar_json(obj, pasta, nome):
    if pasta:
        with open(os.path.join(pasta, nome + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    return obj


# ============================================================ orquestra ==

def nomes_da_ligacao(provas, limite=NOMES_POR_LIGACAO):
    """([(nome, [cnpjs])] a buscar, [nomes que ficaram fora]) na ordem dos suspeitos."""
    por_nome = {}
    ordem = []
    for p in provas:
        nome = p["campos"].get("nome_para_busca")
        if not nome:
            continue
        k = sem_acento(nome)
        if k not in por_nome:
            por_nome[k] = (nome, [])
            ordem.append(k)
        por_nome[k][1].append(p["cnpj"])
    escolhidos = [por_nome[k] for k in ordem[:limite]]
    return escolhidos, [por_nome[k][0] for k in ordem[limite:]]


def coletar(ligacoes_pedidas, saida, serasa=True, google=True, cache_receita=None,
            ip_web=None, ip_google=None):
    """A coleta inteira, em modo de TESTE: nada vai para o banco.

    `saida/<ligacao>/`: suspeitos.json, serasa_<cnpj>.png/.json,
    google_<n>_pagina.png, redes_<n>_<rede>.png, redes_<n>.json, resumo.json.
    `cache_receita`: arquivo JSON com as ligacoes e os suspeitos ja lidos (so
    no teste, para repetir a parte web sem varrer a Receita de novo).
    `ip_web` / `ip_google`: funcoes que devolvem o proximo proxy de cada fonte
    (o padrao e o rodizio do pool, `bn.rodizio(quantos=30)`)."""
    geral = {"inicio": agora_iso(), "ligacoes": {}, "tempos": {}}
    if cache_receita and os.path.exists(cache_receita):
        with open(cache_receita, encoding="utf-8") as f:
            c = json.load(f)
        ligs, sus = c["ligacoes"], c["suspeitos"]
        geral["tempos"] = dict(c["tempos"], veio_do_cache=True)
        _log("▶ Receita lida do cache %s" % cache_receita)
    else:
        t0 = time.time()
        con = bc.conectar()
        try:
            con.rollback()
            con.set_session(readonly=True)
        except Exception as e:                                 # noqa: BLE001
            _log("   aviso: sessao so-leitura nao aplicada (%s)" % e)
        ligs = ler_ligacoes(con, ligacoes_pedidas)
        geral["tempos"]["ler_ligacoes_s"] = round(time.time() - t0, 1)
        faltam = [str(x) for x in ligacoes_pedidas if str(x) not in ligs]
        if faltam:
            _log("   ligacoes que nao estao no cadastro: %s" % faltam)
        t0 = time.time()
        sus, tempos_rf = suspeitos(con, ligs)
        con.close()
        geral["tempos"]["receita_total_s"] = round(time.time() - t0, 1)
        geral["tempos"]["receita"] = tempos_rf
        _log("▶ Receita pronta em %.0f s · %s" % (time.time() - t0, tempos_rf))
        if cache_receita:
            _gravar_json({"ligacoes": ligs, "suspeitos": sus, "tempos": geral["tempos"]},
                         os.path.dirname(cache_receita), os.path.basename(cache_receita)[:-5])

    proximo = bn.rodizio(quantos=30, pais="", embaralhar=True) if (serasa or google) and not (ip_web and ip_google)         else None
    nav_web = Navegador(ip_web or proximo) if serasa else None
    nav_google = Navegador(ip_google or proximo, aquecer_google=True) if google else None
    # UMA THREAD POR FONTE. A API sincrona do Playwright nao aceita dois
    # navegadores na mesma thread ("Sync API inside the asyncio loop", medido
    # no primeiro ensaio): o Serasa e o Google rodam cada um na sua thread, com
    # o seu Camoufox, e em paralelo dentro da ligacao.
    ex_web = cf.ThreadPoolExecutor(1, thread_name_prefix="serasa") if nav_web else None
    ex_google = cf.ThreadPoolExecutor(1, thread_name_prefix="google") if nav_google else None
    ritmo = RitmoGoogle()
    n_busca = 0
    try:
        for lig in [str(x) for x in ligacoes_pedidas if str(x) in ligs]:
            info = ligs[lig]
            pasta = os.path.join(saida, lig)
            os.makedirs(pasta, exist_ok=True)
            provas = sus.get(lig, {}).get("suspeitos", [])
            desc = sus.get(lig, {}).get("descartados_complemento", [])
            _gravar_json({"ligacao": info, "suspeitos": provas, "descartados_complemento": desc}, pasta, "suspeitos")
            resumo = {"ligacao": lig, "endereco": info["end_ligacao"], "complemento": info["complemento"],
                      "suspeitos": len(provas), "descartados_complemento": len(desc),
                      "cnpjs": [{"cnpj": p["cnpj"], "razao_social": p["campos"]["razao_social"],
                                 "nome_fantasia": p["campos"]["nome_fantasia"], "mei": p["campos"]["mei"],
                                 "cnae": p["campos"]["cnae_principal"]["descricao"],
                                 "inicio": p["campos"]["data_inicio"], "complemento": p["campos"]["endereco"]["complemento"],
                                 "casou_por": p["campos"]["casou_por"]} for p in provas],
                      "serasa": {"ok": 0, "bloqueado": 0, "erro": 0, "segundos": 0.0, "itens": []},
                      "google": {"buscas": 0, "bloqueadas": 0, "erros": 0, "segundos": 0.0,
                                 "redes": {r: 0 for r in REDES}, "datas": [], "nomes": [], "nomes_fora": []},
                      "segundos_da_ligacao": None}
            _log("▶ ligação %s · %s · complemento %r · %d suspeito(s)%s"
                 % (lig, info["end_ligacao"], info["complemento"], len(provas),
                    (" · %d descartado(s) pelo complemento" % len(desc)) if desc else ""))
            t_lig = time.time()
            fut_serasa = [(p, ex_web.submit(coletar_serasa, nav_web, p["cnpj"], pasta, lig)) for p in provas]                 if ex_web else []
            fut_google = []
            if ex_google:
                nomes, fora = nomes_da_ligacao(provas)
                resumo["google"]["nomes_fora"] = fora
                for nome, cnpjs in nomes:
                    n_busca += 1
                    fut_google.append((nome, ex_google.submit(buscar_redes, nav_google, ritmo, nome, info["cidade"],
                                                              pasta, n_busca, lig, cnpjs)))
            for p, fut in fut_serasa:
                s = fut.result()
                chave = "bloqueado" if s["bloqueado"] else ("ok" if s["png"] and not s["erro"] else "erro")
                resumo["serasa"][chave] += 1
                resumo["serasa"]["segundos"] = round(resumo["serasa"]["segundos"] + s["segundos"], 1)
                resumo["serasa"]["itens"].append({"cnpj": p["cnpj"], "resultado": chave, "erro": s["erro"],
                                                  "segundos": s["segundos"],
                                                  "razao_social": s["campos"].get("razao_social"),
                                                  "situacao": s["campos"].get("situacao")})
                _log("   Serasa %s: %s (%.1f s)%s" % (p["cnpj"], chave, s["segundos"],
                                                    (" · " + s["erro"]) if s["erro"] else ""))
            for nome, fut in fut_google:
                r = fut.result()
                g = resumo["google"]
                g["buscas"] += 1
                g["segundos"] = round(g["segundos"] + (r["segundos"] or 0), 1)
                if r["bloqueado"]:
                    g["bloqueadas"] += 1
                elif r["erro"]:
                    g["erros"] += 1
                achou = [x for x in REDES if r["redes"].get(x)]
                for x in achou:
                    g["redes"][x] += 1
                    if r["redes"][x].get("data_mostrada"):
                        g["datas"].append({"n": r["n"], "rede": x, "mostrada": r["redes"][x]["data_mostrada"],
                                           "aproximada": r["redes"][x]["data_aproximada"]})
                g["nomes"].append({"n": r["n"], "nome": nome, "consulta": r["consulta"], "bloqueado": r["bloqueado"],
                                   "erro": r["erro"], "redes": achou, "ip": r["ip"], "segundos": r["segundos"]})
                _log("   Google #%d %r: %s (%.1f s, ip %s)%s" % (
                    r["n"], r["consulta"], "BLOQUEADO" if r["bloqueado"] else (achou or "nenhuma rede"),
                    r["segundos"] or 0, r["ip"], (" · " + r["erro"]) if r["erro"] and not r["bloqueado"] else ""))
            resumo["segundos_da_ligacao"] = round(time.time() - t_lig, 1)
            _gravar_json(resumo, pasta, "resumo")
            geral["ligacoes"][lig] = resumo
    finally:
        # cada navegador e fechado pela thread que o abriu
        if ex_web:
            ex_web.submit(nav_web.fechar).result()
            ex_web.shutdown()
        if ex_google:
            ex_google.submit(nav_google.fechar).result()
            ex_google.shutdown()
    geral["google_buscas_por_minuto"] = ritmo.por_minuto()
    geral["fim"] = agora_iso()
    _gravar_json(geral, saida, "resumo_geral")
    return geral


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ligacao", action="append", required=True)
    p.add_argument("--saida", required=True)
    p.add_argument("--sem-serasa", dest="serasa", action="store_false")
    p.add_argument("--sem-google", dest="google", action="store_false")
    p.add_argument("--cache-receita", dest="cache_receita", default=None,
                   help="so no teste: JSON com os suspeitos ja lidos (cria se nao existir)")
    a = p.parse_args(argv)
    os.makedirs(a.saida, exist_ok=True)
    return coletar(a.ligacao, a.saida, serasa=a.serasa, google=a.google, cache_receita=a.cache_receita)


if __name__ == "__main__":
    main()
