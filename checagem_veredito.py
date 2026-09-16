# -*- coding: utf-8 -*-
"""checagem_veredito.py — a checagem do veredito DEPOIS da IA, no codigo.

Pedido do dono do produto em 12/09/2026, depois da analise dos aprovados:

1. REGISTRO QUE NAO VALE NAO APROVA. Sai da base da aprovacao o registro que
   a IA citou e nao estava na lista que ela recebeu, o que tem complemento
   diferente do da instalacao (CASA 52-C aprovada pela empresa da CASA 62-C) e
   o que nao e comercio nem servico: natureza juridica de administracao
   publica ou de entidade sem fins lucrativos (associacao 3999, condominio
   3085, organizacao religiosa 3220...), ou igreja, templo, condominio,
   associacao, escola publica pelo nome ou categoria. Sem registro valido, a
   ligacao e reprovada.
2. UM POI, UMA INSTALACAO. O mesmo POI aprovava ate 27 ligacoes de um
   condominio. Se o complemento do POI casa com o de UMA so das instalacoes,
   ele fica com ela e sai das outras (que, sem outro registro, sao
   reprovadas). Se nao ha como dizer de qual e, ele nao aprova nenhuma: a
   duvida e de unidade, e vai para revisao humana.
3. APROVADA SO POR MEI VAI PARA REVISAO HUMANA. MEI e o optante pelo MEI na
   base do Simples da Receita (`rf_simples.opcao_mei = 'S'`). Se outra
   empresa tambem confirma — CNPJ que nao e MEI, ficha do Maps, loja do iFood,
   base estadual, IBGE —, a ligacao segue aprovada.
4. SIM COM ANALISE HUMANA NAO APROVA (dono do produto, 14/09/2026): a ligacao
   cuja qualificacao no cadastro e SIM_COM_ANALISE_HUMANA e que sairia aprovada
   vai para revisao humana — "cabendo ao usuario gerar o status atual". A
   reprovada continua reprovada.
5. NUMERO DIFERENTE NAO E DESTA INSTALACAO (dono do produto, 14/09/2026): sai da
   base o registro que publica outro numero (e o que a IA disse ter visto com
   numero diferente). "Se na imagem nao for possivel achar o numero exato buscado
   nao tem problema, mas se identificar o diferente ai sim."
6. SEM PROVA RECENTE NAO APROVA (dono do produto, 14/09/2026): a aprovada precisa
   de ao menos um registro valido com prova de ate 2 anos (`provas_datadas`) —
   CNPJ ativo na base atual da Receita, avaliacao de cliente, foto que mostra o
   comercio, loja vista no iFood, atualizacao no Overture/Foursquare. A busca
   web nunca conta. Sem isso, vai para revisao humana, com a idade das provas.

O VEREDITO DA IA FICA GUARDADO em `percepcao.checagem.veredito_ia`, e a
revisao recomeca sempre dele: rodar de novo nao acumula efeito.

    python checagem_veredito.py            # so conta o que mudaria
    python checagem_veredito.py --aplicar  # grava (e guarda o antes em JSON)
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys

import base_comum as bc
import provas_datadas as pdat

REGRA = "checagem do codigo de 16/09/2026 v7 (fachada da seta com sinal ou rede social + outra fonte, so SIM) · v6 (fonte única promove, foto do Google datada, iFood 6m, Maps 12m) · v5 (número, complemento, cada fonte com prova recente, fonte única, rede social, vizinho, ficha do Maps, aluga/vende e anúncio)"

#: AS FONTES INDEPENDENTES (dono do produto, 14 e 15/09/2026). Cada uma conta uma vez; o Serasa e a
#: Casa dos Dados SAO a Receita — a IA do teste contou "Receita" e "Serasa" como duas e aprovou.
#: 15/09/2026 (madrugada): A BUSCA NA WEB E A RECEITA SAO A MESMA FONTE — o que a busca acha no endereco sao
#: os agregadores de CNPJ (Solutudo, Econodata, Kompass), a Receita republicada.
FONTES_INDEPENDENTES = {"receita", "google maps", "foto de rua", "instagram", "facebook", "tiktok", "ifood",
                        "base estadual", "youtube", "linkedin", "kwai"}
#: as redes sociais: so contam confirmadas pelo codigo (`fonte_da_busca.redes_sociais_no_endereco`)
REDES_SOCIAIS = {"instagram", "facebook", "tiktok", "youtube", "linkedin", "kwai"}
#: As fontes que so a IMAGEM prova: nao entram pela lista da IA, so pela imagem com sinal descrito.
FONTES_DE_IMAGEM = {"foto de rua"}
#: O que a IA escreve quando nao ha sinal na imagem.
_SEM_SINAL = ("", "nenhum", "nenhuma", "sem sinal", "não", "nao", "-", "—")
SINONIMOS_DE_FONTE = {"serasa": "receita", "casa dos dados": "receita", "cnpj": "receita",
                      "receita federal": "receita", "base da receita": "receita", "busca na web": "receita",
                      "busca": "receita", "web": "receita", "google": "google maps",
                      "maps": "google maps", "comentário": "google maps", "comentario": "google maps",
                      "foto publicada": "google maps", "street view": "foto de rua", "estadual": "base estadual", "foursquare": "base estadual",
                      "overture": "base estadual"}


#: o rotulo da foto de rua com placa de aluguel ou venda na fachada da seta (auditoria das 40, 15/09/2026)
MARCA_ALUGA_NA_RUA = "placa de aluga/vende na fachada da seta"
#: o rotulo da foto de rua em que nada vale para a instalacao (so placa de vizinho sem o nome em outra fonte)
MARCA_SEM_SINAL_NA_RUA = "sem sinal que valha para esta instalação"


def sem_foto_de_vizinho(resposta, fotos):
    """A PLACA DO VIZINHO SO COM O NOME EM OUTRA FONTE (dono do produto, 15/09/2026). A foto de rua marcada por
    `fachada_da_seta` sai das imagens que confirmam: a IA que julga ve a placa ao lado e aprovava por ela."""
    marcadas = {i + 1 for i, rot in enumerate(fotos or []) if MARCA_SEM_SINAL_NA_RUA in str(rot)}
    f = (resposta or {}).get("fotos")
    if not marcadas or not isinstance(f, dict):
        return resposta
    r, f = dict(resposta), dict(f)
    quais = []
    for n in f.get("quais") or []:
        try:
            if int(n) in marcadas:
                continue
        except (TypeError, ValueError):
            pass
        quais.append(n)
    f["quais"] = quais
    if not quais:
        f["confirmam"] = False
        f["sinal"] = "nenhum (a foto de rua só mostra placa de vizinho sem o nome em outra fonte)"
    r["fotos"] = f
    return r


def sem_ficha_do_maps_vazia(con, resposta, ids, fotos):
    """A FICHA DO MAPS SO E FONTE COM COMENTARIO DE CLIENTE DATADO OU FOTO REAL DO LUGAR (dono do produto, 15/09/2026).
    Na 2555907 a ficha "Pedroso entrega de agua mineral" nao tinha comentario e a unica foto era arte de logo: com o
    MEI, aprovava como 2 fontes. Sem comentario datado nos registros e sem foto publicada que a IA diga mostrar o lugar
    com sinal, "Google Maps" sai das fontes e os comentarios deixam de confirmar."""
    provas = pdat.carregar(con, ids or [])
    comentario = any(p.get("fonte") == "maps" and "avaliação" in str(p.get("o_que") or "") and p.get("data")
                     for d in provas.values() for p in (d.get("provas") or []))
    f = (resposta or {}).get("fotos") or {}
    foto_real = False
    if isinstance(f, dict) and f.get("confirmam") and imagem_tem_sinal(resposta):
        for n in f.get("quais") or []:
            try:
                foto_real |= str((fotos or [])[int(n) - 1]).lower().startswith("foto publicada")
            except (TypeError, ValueError, IndexError):
                pass
    usou_maps = any(_fonte(x) == "google maps" for x in ((resposta or {}).get("fontes") or [])) or any(
        _fonte(x) == "google maps" for a in ((resposta or {}).get("aderentes") or []) if isinstance(a, dict)
        for x in (a.get("fontes") or [])) or bool(((resposta or {}).get("comentarios") or {}).get("confirmam"))
    if comentario or foto_real or not usou_maps:
        return resposta, None
    r = dict(resposta or {})
    tira = lambda fs: [x for x in (fs or []) if _fonte(x) != "google maps"]   # noqa: E731
    r["fontes"] = tira(r.get("fontes"))
    r["aderentes"] = [dict(a, fontes=tira(a.get("fontes"))) if isinstance(a, dict) else a for a in (r.get("aderentes") or [])]
    if isinstance(r.get("comentarios"), dict):
        r["comentarios"] = dict(r["comentarios"], confirmam=False)
    return r, "a ficha do Google Maps não tem comentário de cliente datado nem foto real do lugar: não conta como fonte"


def _fonte(f):
    f = str(f or "").strip().lower()
    return SINONIMOS_DE_FONTE.get(f, f)


def fonte_da_imagem(rotulo):
    r = str(rotulo or "").lower()
    if r.startswith("foto de rua") or r.startswith("sv_"):
        return "foto de rua"
    if r.startswith("foto publicada"):
        return "google maps"
    return None


def fontes_confirmadas(resposta, fotos=None):
    """As fontes independentes que confirmam o uso, pela UNIAO do que a IA disse: a lista `fontes`, as
    fontes dos aderentes confirmados no mesmo numero, as imagens que mostram o uso, os comentarios e as
    fichas/buscas que confirmam. Normalizada: Serasa e Receita viram uma so (15/09/2026)."""
    r = resposta or {}
    fs = {_fonte(x) for x in (r.get("fontes") or [])}
    for a in r.get("aderentes") or []:
        if isinstance(a, dict) and a.get("confirmado") and str(a.get("numero") or "") != "diferente":
            fs |= {_fonte(x) for x in (a.get("fontes") or [])}
    # A FOTO DE RUA SO PELA IMAGEM COM SINAL (15/09/2026): a IA punha "foto de rua" na lista de fontes
    # para casa residencial sem letreiro nem nada.
    fs -= FONTES_DE_IMAGEM
    f = r.get("fotos") or {}
    if isinstance(f, dict) and f.get("confirmam") and imagem_tem_sinal(r):
        for n in f.get("quais") or []:
            try:
                fi = fonte_da_imagem((fotos or [])[int(n) - 1])
            except (TypeError, ValueError, IndexError):
                fi = None
            if fi:
                fs.add(fi)
    c = r.get("comentarios") or {}
    if isinstance(c, dict) and c.get("confirmam"):
        fs.add("google maps")
    for b in r.get("busca") or []:
        if isinstance(b, dict) and b.get("confirma"):
            fs.add(_fonte(b.get("fonte")))
    return sorted(fs & FONTES_INDEPENDENTES)


#: ARTE DE DIVULGACAO NAO E FOTO DO LUGAR (auditoria das aprovadas do R_000, 296679 e 335469): com o prompt reforcado a
#: IA ainda escrevia "Arte com logo e dados de contato" e contava a imagem. O codigo le a descricao que ela mesma deu.
RE_ARTE = re.compile(r"\b(arte|artes|card|flyer|folder|panfleto|divulgacao|montagem|ilustracao|captura de tela|"
                     r"print de tela|post promocional|banner digital|cartaz digital|imagem promocional)\b")


def _sem_acento(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")


def imagem_tem_sinal(resposta):
    """A IA descreveu um sinal concreto de uso na imagem (letreiro, vitrine, porta de loja, patio...)."""
    f = (resposta or {}).get("fotos") or {}
    sinal = str(f.get("sinal") or "").strip().lower() if isinstance(f, dict) else ""
    if isinstance(f, dict) and RE_ARTE.search(_sem_acento("%s %s" % (sinal, f.get("o_que_mostram") or ""))):
        return False
    return bool(f.get("confirmam")) and sinal not in _SEM_SINAL and not sinal.startswith("nenhum")


#: Meses em que a loja vista no iFood e a avaliacao de cliente no Google bastam SOZINHAS (dono do produto, 22h de 15/09/2026)
IFOOD_SOZINHO_MESES = 6
MAPS_SOZINHO_MESES = 12


def fonte_unica_basta(ctx, validos, resposta, fotos):
    """(basta, texto): a unica fonte que aprova sozinha (dono do produto, 15/09/2026) — a fachada no Street
    View que mostra o uso (de ate 2 anos), ou a foto publicada de menos de 1 ano que mostra o uso; o
    comentario de menos de 1 ano so com uma imagem que confirma."""
    r = resposta or {}
    f = r.get("fotos") or {}
    # O IFOOD E O COMENTARIO RECENTE BASTAM SOZINHOS (dono do produto, 15/09/2026, 22h): o iFood so lista quem
    # opera, e o comentario de cliente de ate 12 meses diz que alguem foi atendido ali.
    for pid in validos or []:
        for p in (ctx.provas.get(pid) or {}).get("provas") or []:
            if not p.get("data"):
                continue
            m = pdat.meses(p["data"])
            if p.get("fonte") == "ifood" and m <= IFOOD_SOZINHO_MESES:
                return True, "loja vista no iFood em %s" % pdat.mes_ano(p["data"])
            if (p.get("fonte") == "maps" and "avaliação" in str(p.get("o_que") or "")
                    and m <= MAPS_SOZINHO_MESES):
                return True, "avaliação de cliente no Google em %s" % pdat.mes_ano(p["data"])
    if not imagem_tem_sinal(r):
        return False, "nenhuma imagem mostra sinal de uso"
    for n in f.get("quais") or []:
        try:
            rot = (fotos or [])[int(n) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        tipo, dt = fonte_da_imagem(rot), pdat.data_do_rotulo(rot)
        if tipo == "foto de rua" and dt and pdat.recente(dt):
            return True, "a fachada no Street View de %s mostra o uso" % pdat.mes_ano(dt)
        if tipo == "google maps" and dt and pdat.meses(dt) < 12:
            return True, "a foto publicada de %s (menos de 1 ano) mostra o uso" % pdat.mes_ano(dt)
    c = r.get("comentarios") or {}
    if isinstance(c, dict) and c.get("confirmam"):
        coments = pdat.comentarios_recentes(ctx.con, validos, n=1, ate_meses=11) if getattr(ctx, "con", None) else {}
        if any(coments.get(p) for p in validos):
            return True, "comentário de cliente de menos de 1 ano, com imagem que confirma o uso"
    return False, "a fonte única não é fachada no Street View nem foto ou comentário de menos de 1 ano"


def vago_depois_das_provas(ctx, validos, fotos, redes=()):
    """(vago, texto): A PLACA DE ALUGA/VENDE MAIS NOVA QUE A ULTIMA PROVA DE ATIVIDADE (auditoria das 40, 15/09/2026).
    Na 310148 a foto de out/2025 mostrava o muro do galpao com "Imobiliaria Vital ALUGA", depois do ultimo comentario
    do Google (set/2025), e a IA contou a placa como sinal de uso. Prova de atividade: Google, iFood, base estadual e
    post de rede social datados — o CNPJ ativo e o CNEFE nao dizem se o imovel segue ocupado."""
    for rot in fotos or []:
        if MARCA_ALUGA_NA_RUA not in str(rot):
            continue
        dt_foto = pdat.data_do_rotulo(rot)
        if not dt_foto:
            continue
        datas = [p["data"] for pid in validos for p in ((ctx.provas.get(pid) or {}).get("provas") or [])
                 if p.get("fonte") in ("maps", "ifood", "estadual") and p.get("data")]
        datas += [pdat.data_de_texto(x.get("data")) for x in redes or [] if x.get("data")]
        datas = [d for d in datas if d]
        ultima = max(datas) if datas else None
        if not ultima or dt_foto >= ultima:
            return True, ("a foto de rua de %s mostra placa de aluguel/venda na fachada da seta, %s"
                          % (pdat.mes_ano(dt_foto), ("mais nova que a última prova de atividade (%s)" % pdat.mes_ano(ultima))
                             if ultima else "e não há prova de atividade datada depois dela"))
    return False, None


def fontes_com_prova_recente(ctx, validos, resposta, fotos):
    """{fonte: prova}: CADA FONTE COM A PROPRIA PROVA DE ATE 2 ANOS (dono do produto, 15/09/2026). A auditoria das
    aprovadas do R_000 achou a Receita de ago/2026 somando com a base estadual SEM DATA (304938), com o post de 2018
    (344980) e com a busca web que so repetia a ficha do Maps (319419): uma prova recente e uma fonte velha ou sem
    data aprovavam. Aqui a fonte so vale com a prova datada DELA, lida do banco — nunca da data que a IA escreveu:
    Receita pelo CNPJ ativo na base; Google Maps pelo comentario de cliente (a foto do Google so pela imagem que a
    IA disse mostrar o lugar, com a data do rotulo); iFood pela loja vista; base estadual pela data que publica;
    foto de rua pela data do rotulo; rede social pela data do post. A busca web nunca e fonte por si."""
    saida = {}
    nomes = {"receita": "receita", "ifood": "ifood", "estadual": "base estadual"}
    for pid in validos or []:
        for p in (ctx.provas.get(pid) or {}).get("provas") or []:
            if not p.get("recente") or not p.get("data"):
                continue
            f = nomes.get(p.get("fonte"))
            if p.get("fonte") == "maps" and "avaliação" in str(p.get("o_que") or ""):
                f = "google maps"
            # A FOTO PUBLICADA DATADA E PROVA DO GOOGLE MAPS (dono do produto, 15/09/2026, 22h): a regra dele diz
            # "comentario datado OU foto real do lugar", e exigir que a IA descrevesse um letreiro derrubava a
            # 335172 (Receita ago/2026 + foto do Google ago/2026) como se fosse fonte unica. So nao vale quando a
            # propria IA descreveu a imagem como arte de divulgacao.
            if (p.get("fonte") == "maps" and "foto publicada" in str(p.get("o_que") or "")
                    and not RE_ARTE.search(_sem_acento(json.dumps((resposta or {}).get("fotos") or {},
                                                                  ensure_ascii=False)))):
                f = f or "google maps"
            if f and f not in saida:
                saida[f] = "#%s %s" % (pid, p.get("o_que"))
    fo = (resposta or {}).get("fotos") or {}
    if isinstance(fo, dict) and fo.get("confirmam") and imagem_tem_sinal(resposta):
        for n in fo.get("quais") or []:
            try:
                rot = (fotos or [])[int(n) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            fi, dt = fonte_da_imagem(rot), pdat.data_do_rotulo(rot)
            if fi and dt and pdat.recente(dt) and fi not in saida:
                saida[fi] = "imagem %s de %s" % (n, pdat.mes_ano(dt))
    for x in (resposta or {}).get("_redes") or []:
        dt = pdat.data_de_texto(x.get("data"))
        rede = str(x.get("rede") or "").lower()
        if dt and pdat.recente(dt) and rede and rede not in saida:
            saida[rede] = "post no %s de %s" % (x.get("rede"), pdat.mes_ano(dt))
    return saida


def _ultima_prova_de_atividade(ctx, validos, redes=()):
    """A data mais recente que diz que o negocio segue ativo: Google, iFood, base estadual e post de rede social.
    O CNPJ ativo e o CNEFE nao dizem se o imovel segue ocupado."""
    datas = [p["data"] for pid in validos for p in ((ctx.provas.get(pid) or {}).get("provas") or [])
             if p.get("fonte") in ("maps", "ifood", "estadual") and p.get("data")]
    datas += [pdat.data_de_texto(x.get("data")) for x in redes or [] if x.get("data")]
    datas = [d for d in datas if d]
    return max(datas) if datas else None


def contraprova_de_anuncio(con, ctx, lig, validos, redes=()):
    """ANUNCIO DE ALUGUEL OU VENDA DO IMOVEL NA BUSCA, DATADO E MAIS NOVO QUE A ULTIMA PROVA DE ATIVIDADE (auditoria
    das aprovadas do R_000, 333197: casa a venda anunciada em mar/2026 e a IA aprovou a doceria). Sem data no trecho
    nao conta: anuncio antigo de outro momento do imovel nao desmente o negocio."""
    try:
        import fonte_da_busca as fdb
        with con.cursor() as k:
            anuncios = [a for a in fdb.anuncios_no_endereco(k, lig) if a.get("data")]
    except Exception:                                          # noqa: BLE001
        return None
    if not anuncios:
        return None
    ultima = _ultima_prova_de_atividade(ctx, validos, redes)
    a = max(anuncios, key=lambda x: x["data"])
    dt = pdat.data_de_texto(a["data"])
    if dt and (not ultima or dt >= ultima):
        return ("anúncio de aluguel/venda do imóvel na busca de %s, %s" % (pdat.mes_ano(dt), (
            "mais novo que a última prova de atividade (%s)" % pdat.mes_ano(ultima)) if ultima else
            "sem prova de atividade datada depois dele"))
    return None


def processo_leve(processo):
    return "leve" in str(processo or "")

#: Natureza juridica que nao e comercio nem servico: administracao publica (1xxx)
#: e entidade sem fins lucrativos (3xxx), menos o cartorio (3034), que cobra.
def _natureza_nao_comercio(nat):
    nat = (nat or "").strip()
    return bool(nat) and (nat[0] == "1" or (nat[0] == "3" and nat != "3034"))


#: Pelo nome ou categoria, para as fontes que nao tem natureza juridica.
RE_NAO_COMERCIO = re.compile(
    r"igreja|templo|centro esp[ií]rita|terreiro|umbanda|par[oó]quia|capela|assembl[eé]ia de deus|"
    r"congrega[cç][aã]o|comunidade evang|condom[ií]nio|associa[cç][aã]o|sindicato|"
    r"escola (estadual|municipal|p[uú]blica)|\bemef\b|\beeef\b|\bemei\b|senai|\bsesi\b|\bsesc\b|senac|"
    r"prefeitura|secretaria (municipal|estadual)|posto de sa[uú]de|\bubs\b|centro de tradi[cç]|\bctg\b|"
    r"marco/edif[ií]cio|"
    # 2a analise (12/09/2026): passavam 'Organizacao religiosa' (Ile Axe Abaya
    # Bomi) e 'COMITE POLITICO' do IBGE
    r"organiza[cç][aã]o religiosa|candombl|il[eê] ax[eé]|kardec|"
    r"sal[aã]o do reino|testemunhas de jeov|mesquita|sinagoga|ma[cç]onaria|loja ma[cç][oô]nica|"
    r"comit[eê] pol[ií]tico|partido pol[ií]tico|diret[oó]rio (municipal|do partido)|"
    # 14/09/2026: o 'Clube de Maes Santa Ines' (ligacao 314534) aprovou como
    # "servico social", com categoria 'Comunitario Servicos Non Profits'
    r"clube (de )?m[aã]es|servi[cç]o social|centro comunit[aá]rio|servi[cç]os? comunit[aá]rio|comunit[aá]rio servi[cç]|"
    r"beneficente|filantr[oó]pic|sem fins lucrativos|non[- ]?profit|obra social|\bong\b", re.I)


def _numeros(txt):
    return {int(n) for n in re.findall(r"\d+", txt or "") if len(n) <= 5}


def _letras(txt):
    return {x.upper() for x in re.findall(r"(?<![A-Za-zÀ-ú])([A-Za-z])(?![A-Za-zÀ-ú])", txt or "")}


def complemento_da_instalacao(end_ligacao, bairro):
    """'RUA X,1000-CASA 52-C -SAO LUIS-CANOAS-RS-CEP:...' -> 'CASA 52-C'.

    Logradouro,numero - complemento - bairro - cidade - RS - CEP. O complemento
    pode ter hifen; o bairro, o municipio, a UF e o CEP sao os quatro ultimos.
    """
    partes = (end_ligacao or "").split("-")
    if len(partes) < 6:
        return ""
    comp = "-".join(partes[1:-4]).strip()
    # O NUMERO DA CASA REPETIDO NAO E COMPLEMENTO: 'RUA BAMBUS,77-77-...' fez a
    # 2a analise reprovar a CASA 02 do numero 77 por 'complemento diferente'.
    nro = re.sub(r"\D", "", partes[0].split(",")[-1]) if "," in partes[0] else ""
    if nro and re.fullmatch(r"0*%s\s*[A-Z]?" % re.escape(nro.lstrip("0") or "0"), comp.upper()):
        return ""
    return comp


def complemento_diverge(inst, reg):
    """Os dois tem numero e nenhum numero em comum; ou o mesmo numero com letra
    de bloco diferente. Registro sem numero no complemento ('CASA', 'FUNDOS')
    nao diverge: sem complemento vale (regra do dono do produto)."""
    ni, nr = _numeros(inst), _numeros(reg)
    if not ni or not nr:
        return False
    if not (ni & nr):
        return True
    li, lr = _letras(inst), _letras(reg)
    return len(li) == 1 and len(lr) == 1 and li != lr


class Contexto:
    """O que a checagem precisa das ligacoes e dos POIs, lido de uma vez."""

    def __init__(self, con, ligacoes, pois):
        self.con = con                                     # a fonte unica le os comentarios (regra 7)
        cur = con.cursor()
        cur.execute("set statement_timeout = '300s'")
        ligs = sorted({str(x) for x in ligacoes})
        ids = sorted({int(x) for x in pois if str(x).isdigit()})
        cur.execute("""select num_ligacao::text, coalesce(end_ligacao,''), coalesce(nom_bairro,''),
                              coalesce(qualificacao,''), coalesce(nro::text,'')
                         from resources_root.cadastro_corsan where num_ligacao::text = any(%s)""", (ligs,))
        self.compl_inst, self.qualificacao, self.nro_inst = {}, {}, {}
        for l, e, b, q, nro in cur.fetchall():
            self.compl_inst[l] = complemento_da_instalacao(e, b)
            self.qualificacao[l] = q.upper()
            self.nro_inst[l] = nro
        # REGRAS 5 E 6: o numero e as provas datadas de cada POI, as mesmas que o prompt mostrou
        self.provas = pdat.carregar(con, ids)
        cur.execute("""select p.id, lower(coalesce(p.fonte,'')), coalesce(p.nome,''), coalesce(p.categoria,''),
                              rd.cnpj, coalesce(rd.complemento,'')
                         from radar_comercial.pois p
                         left join radar_comercial.receita_data rd on rd.poi_id = p.id
                        where p.id = any(%s)""", (ids,))
        self.poi = {}
        basicos = set()
        for pid, fonte, nome, cat, cnpj, compl in cur.fetchall():
            b = (cnpj or "").zfill(14)[:8] if cnpj else None
            self.poi[pid] = {"fonte": fonte, "nome": nome, "categoria": cat, "basico": b, "complemento": compl}
            if b:
                basicos.add(b)
        # A UNIDADE NO ENDERECO PUBLICADO (auditoria das 40, 15/09/2026): o registro do Maps ou do iFood nao tem
        # complemento da Receita, e a loja 14 do Park Mall confirmava a LOJA 026 (2900611)
        cur.execute("select id, coalesce(endereco, '') from radar_comercial.pois where id = any(%s)", (ids,))
        for pid, end in cur.fetchall():
            if pid in self.poi and not self.poi[pid]["complemento"]:
                m = re.search(r"\b(loja|lj|sala|sl|box|conjunto|conj|bloco|bl|apto|apartamento|ap)\.?\s*(\d+[a-z]?)\b", end.lower())
                if m:
                    self.poi[pid]["complemento"] = "%s %s" % (m.group(1).upper(), m.group(2).upper())
        basicos = sorted(basicos)
        cur.execute("""select cnpj_basico, opcao_mei from resources_root.rf_simples
                        where cnpj_basico = any(%s)""", (basicos,))
        self.mei = {b: (o or "").upper() == "S" for b, o in cur.fetchall()}
        cur.execute("""select cnpj_basico, natureza_juridica from resources_root.rf_empresas
                        where cnpj_basico = any(%s)""", (basicos,))
        self.natureza = dict(cur.fetchall())

    def e_mei(self, pid):
        p = self.poi.get(pid) or {}
        return p.get("fonte") == "receita" and bool(p.get("basico")) and self.mei.get(p["basico"], False)

    def nao_comercio(self, pid):
        p = self.poi.get(pid) or {}
        if p.get("basico") and _natureza_nao_comercio(self.natureza.get(p["basico"])):
            return "natureza jurídica %s" % self.natureza.get(p["basico"])
        m = RE_NAO_COMERCIO.search("%s %s" % (p.get("nome", ""), p.get("categoria", "")))
        return ("'%s'" % m.group(0)) if m else None


def base_da_aprovacao(resposta, processo):
    """Os POIs em que a IA apoiou a aprovacao.

    Enxuto: os aderentes confirmados (ou, sem nenhum confirmado, os aderentes).
    Antigo: os registros que 'pertence: sim' e sao comercio ou servico.
    """
    r = resposta or {}
    # QUALQUER ENXUTO (14/09/2026): o prompt de 13/09 ganhou outro nome de processo, e a
    # comparacao exata com "enxuto de 12/09/2026" reprovou as 6.883 aprovadas da rodada.
    if str(processo or "").startswith("enxuto de "):
        ader = [a for a in (r.get("aderentes") or []) if isinstance(a, dict)]
        conf = [a for a in ader if a.get("confirmado")]
        return [a.get("poi") for a in (conf or ader)]
    regs = [x for x in (r.get("registros") or []) if isinstance(x, dict)]
    sim = [x for x in regs if str(x.get("pertence") or "").lower() == "sim" and x.get("comercio_ou_servico") is not False]
    return [x.get("poi") for x in sim]


def _pid(x):
    try:
        return int(str(x).lstrip("#").strip())
    except (TypeError, ValueError):
        return None


def _numero_visto_diferente(resposta):
    """Os POIs que a IA disse ter visto com numero diferente (campo `numero` do aderente)."""
    saida = set()
    for a in (resposta or {}).get("aderentes") or []:
        if isinstance(a, dict) and str(a.get("numero") or "").strip().lower() == "diferente":
            pid = _pid(a.get("poi"))
            if pid is not None:
                saida.add(pid)
    return saida


def validar(ctx, lig, base, ids, resposta=None):
    """(validos, removidos): as regras 1 e 5, por ligacao."""
    ids = {int(i) for i in (ids or []) if str(i).isdigit()}
    visto_diferente = _numero_visto_diferente(resposta)
    validos, removidos = [], []
    for x in base:
        pid = _pid(x)
        if pid is None or (ids and pid not in ids):
            removidos.append({"poi": x, "porque": "a IA citou um registro que não estava na lista"})
            continue
        nc = ctx.nao_comercio(pid)
        if nc:
            removidos.append({"poi": pid, "porque": "não é comércio nem serviço (%s)" % nc})
            continue
        ci, cr = ctx.compl_inst.get(str(lig), ""), (ctx.poi.get(pid) or {}).get("complemento", "")
        if complemento_diverge(ci, cr):
            removidos.append({"poi": pid, "porque": "complemento diferente (instalação %s, registro %s)" % (ci, cr)})
            continue
        # REGRA 5: numero diferente e outro imovel — o publicado, ou o que a IA viu
        num_reg = (ctx.provas.get(pid) or {}).get("numero")
        if pdat.numero_confere(num_reg, ctx.nro_inst.get(str(lig))) == "diferente":
            removidos.append({"poi": pid, "porque": "número diferente (instalação %s, registro %s)"
                              % (ctx.nro_inst.get(str(lig)), num_reg)})
            continue
        if pid in visto_diferente:
            removidos.append({"poi": pid, "porque": "a IA viu número diferente na foto ou no resultado"})
            continue
        if pid not in validos:
            validos.append(pid)
    return validos, removidos


def prova_recente(ctx, validos, resposta=None, fotos=None):
    """(tem, texto): a regra 6. Alguma prova de ate 2 anos sustenta a aprovacao?

    Conta a prova datada de qualquer registro valido e a foto que a IA disse
    mostrar o comercio, pela data do rotulo dela. A busca web nunca conta.
    """
    achadas, antigas = [], []
    for pid in validos:
        for p in (ctx.provas.get(pid) or {}).get("provas") or []:
            (achadas if p["recente"] else antigas).append("#%s %s" % (pid, p["o_que"]))
    f = (resposta or {}).get("fotos") or {}
    if isinstance(f, dict) and f.get("confirmam") and fotos:
        for n in f.get("quais") or []:
            try:
                rot = fotos[int(n) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            dt = pdat.data_do_rotulo(rot)
            texto = "foto %s de %s mostra o comércio" % (n, pdat.mes_ano(dt) if dt else "data desconhecida")
            (achadas if dt and pdat.recente(dt) else antigas).append(texto)
    # o post de rede social confirmado, pela data dele (15/09/2026)
    for x in (resposta or {}).get("_redes") or []:
        dt = pdat.data_de_texto(x.get("data"))
        texto = "post no %s de %s" % (x.get("rede"), pdat.mes_ano(dt) if dt else "data desconhecida")
        (achadas if dt and pdat.recente(dt) else antigas).append(texto)
    if achadas:
        return True, "; ".join(achadas)
    return False, ("sem prova de até 2 anos" + (": só " + "; ".join(antigas) if antigas
                                                 else ": só a busca na web ou nenhuma prova datada"))


def fachada_ou_rede_basta(ctx, lig, validos, resposta, fotos, ids):
    """(basta, texto): REGRA 8 (dono do produto, 16/09/2026). Com a IA aprovando e a qualificacao SIM, a fachada da seta
    com sinal de uso OU a rede social identificada no endereco, de qualquer data, mais outra fonte valida, aprovam sem
    as regras 6 e 7. Medido em Canoas antes: 3.354 aprovacoes da IA fora de aprovado; com esta regra e as travas que
    seguem depois (so MEI, aluga/vende, um POI uma instalacao), 65 sobem.

    A fachada e a da SETA, decidida pela posicao da ponta na leitura da foto de rua (`fachada_da_seta`), e nao a que a
    IA citou; a placa do vizinho conta com o nome confirmado em outra fonte. A foto publicada no Google nao
    dispara (arte de divulgacao e foto de banco de imagens passavam), e o que o texto da IA diz ser igreja, templo,
    associacao ou escola publica fica de fora."""
    r = resposta or {}
    if lig is None or ctx.qualificacao.get(str(lig)) != "SIM" or not validos:
        return False, None
    uso = r.get("uso") if isinstance(r.get("uso"), dict) else {}
    if RE_NAO_COMERCIO.search("%s %s" % (uso.get("o_que") or "", r.get("motivo") or "")):
        return False, None
    imovel = r.get("imovel") if isinstance(r.get("imovel"), dict) else {}
    if str(imovel.get("estado") or "") == "abandonado":
        return False, None
    f = r.get("fotos") if isinstance(r.get("fotos"), dict) else {}
    rua = []
    if imagem_tem_sinal(r):
        for n in f.get("quais") or []:
            try:
                rot = (fotos or [])[int(n) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            if fonte_da_imagem(rot) == "foto de rua":
                rua.append(pdat.data_do_rotulo(rot))
    if rua:
        vale, onde = _sinal_na_fachada_da_seta(ctx, lig, ids)
        if vale:
            datas = [d for d in rua if d]
            return True, "%s (foto de rua%s) e mais %d registro(s) válido(s)" % (
                onde, (" de %s" % pdat.mes_ano(max(datas))) if datas else "", len(validos))
    redes = r.get("_redes") or []
    rede_pois = {x.get("poi") for x in redes if isinstance(x, dict)}
    if redes and any(p not in rede_pois for p in validos):
        return True, "rede social no endereço (%s) e outra fonte válida" % ", ".join(
            sorted({str(x.get("rede")) for x in redes if isinstance(x, dict)}))
    return False, None


def _sinal_na_fachada_da_seta(ctx, lig, ids):
    """(vale, texto): a leitura da foto de rua do julgamento leve — a de frente do registro com pin do Maps a ate 60 m,
    senao a do hidrometro, como `seek_api._foto_de_rua` — mostra uso na fachada da seta, ou a placa de um vizinho tem
    o nome em outra fonte. So roda para quem ia cair nas regras 6 e 7."""
    import avaliar_enxuto as ae                                # ciclo: o avaliar_enxuto importa esta checagem
    import fachada_da_seta as fds
    ids = [int(i) for i in (ids or []) if str(i).isdigit()]
    with ctx.con.cursor() as cur:
        esc = ae.poi_da_foto_de_rua(cur, lig, ids) if ids else None
        if esc and esc[2]:
            cur.execute("""select leitura, mira_x from radar_comercial.poi_evidencia
                            where poi_id = %s and tipo = 'sv_frente'""", (esc[0],))
        elif not esc:
            cur.execute("""select leitura, mira_x from radar_comercial.ligacao_evidencia
                            where ligacao = %s and tipo = 'sv_frente' and (dados is not null or storage_path is not null)""",
                        (str(lig),))
        else:
            return False, None
        linha = cur.fetchone()
        if not linha or not isinstance(linha[0], dict) or linha[0].get("fachadas") is None:
            return False, None
        leitura, mira_x = linha
        alvo, divisa = fds.escolher_final(leitura, mira_x)
        if alvo and fds.seta_tem_sinal(alvo):
            return True, "a fachada da seta mostra uso"
        _texto, vale = fds.para_julgamento(leitura, mira_x, *fds.fontes_de_nome(cur, lig, ids))
        return (True, "a placa do vizinho tem o nome confirmado em outra fonte") if vale else (False, None)


def decidir(ctx, validos, perdeu_por_duvida, lig=None, resposta=None, fotos=None, processo=None, ia_aprovou=True,
            ids=None):
    """O veredito final de uma ligacao que a IA aprovou: regras 1, 3, 4, 6, 8 e, no processo leve, 7."""
    if not validos:
        if perdeu_por_duvida:
            return "revisao_humana", ("o registro que aprovava também é candidato de outra(s) instalação(ões) "
                                      "e não dá para dizer de qual é")
        return "reprovado", "nenhum registro válido sustenta a aprovação"
    # REGRA 8 (16/09/2026): a fachada da seta ou a rede social + outra fonte, com a IA aprovando, dispensa as regras
    # 6 e 7. Calculada so quando uma delas ia derrubar: le a foto de rua no banco.
    regra8 = []

    def _regra8():
        if not regra8:
            regra8.append(fachada_ou_rede_basta(ctx, lig, validos, resposta, fotos, ids) if ia_aprovou else (False, None))
        return regra8[0][0]

    # REGRA 6: sem prova recente a pessoa decide
    tem, texto = prova_recente(ctx, validos, resposta, fotos)
    if not tem and not _regra8():
        return "revisao_humana", texto
    # REGRA 7 (15/09/2026), so no processo leve: ao menos 2 fontes independentes confirmam o uso. O
    # codigo conta, e nao a IA: ela somava Receita e Serasa como duas.
    if processo_leve(processo):
        imovel = (resposta or {}).get("imovel") or {}
        if isinstance(imovel, dict) and str(imovel.get("estado") or "") == "abandonado":
            return "revisao_humana", "a IA viu o imóvel abandonado ou sem uso: %s" % (imovel.get("por") or "")
        fs_ia = fontes_confirmadas(resposta, fotos)
        recentes = fontes_com_prova_recente(ctx, validos, resposta, fotos)
        fs = [f for f in fs_ia if f in recentes]
        if len(fs) < 2:
            basta, texto_unica = fonte_unica_basta(ctx, validos, resposta, fotos)
            if not basta and not _regra8():
                sem = [f for f in fs_ia if f not in recentes]
                return "revisao_humana", ("só %d fonte com prova de até 2 anos confirma o uso (%s)%s e ela não basta "
                                          "sozinha: %s" % (len(fs), ", ".join(fs) or "nenhuma",
                                                           ("; sem prova recente própria: " + ", ".join(sem)) if sem else "",
                                                           texto_unica))
    if all(ctx.e_mei(p) for p in validos):
        return "revisao_humana", "aprovada só por MEI (%s)" % ", ".join("#%s" % p for p in validos)
    # REGRA 4: a qualificacao SIM com analise humana nao aprova sozinha.
    if lig is not None and ctx.qualificacao.get(str(lig)) == "SIM_COM_ANALISE_HUMANA":
        return "revisao_humana", "qualificação SIM com análise humana: a decisão é do usuário"
    return "aprovado", None


def revisar(con, aplicar=False, log=print, saida_antes=None, saida_mudancas=None, so_veredito=None,
            so_qualificacao=None):
    """A checagem de TODAS as ligacoes que a IA aprovou, com a exclusividade.

    Recomeca sempre do veredito da IA (`percepcao.checagem.veredito_ia`), entao
    rodar de novo nao acumula efeito. Devolve o placar das mudancas.

    `so_veredito` e `so_qualificacao` (16/09/2026): a checagem calcula com TODAS as aprovadas pela IA (a regra 2
    precisa delas), mas so grava a mudanca das ligacoes com o veredito atual e a qualificacao pedidos — "rodar de novo
    as que cairam para analise, e nao as reprovadas, so nas SIM".
    """
    cur = con.cursor()
    cur.execute("set statement_timeout = '600s'")
    cur.execute("""select ligacao, veredito, percepcao::jsonb->'resposta', percepcao::jsonb->'ids',
                          coalesce(percepcao::jsonb->>'processo','antigo'), percepcao::jsonb->'checagem',
                          justificativa, percepcao::jsonb->'fotos'
                     from radar_comercial.ligacao_veredito
                    where (veredito = 'aprovado'
                           or percepcao::jsonb->'checagem'->>'veredito_ia' = 'aprovado')
                      -- AGUARDANDO O REJULGAMENTO (dono do produto, 15/09/2026): o veredito que ainda nao passou pelo
                      -- metodo novo esta em revisao humana ate ser julgado de novo. Aqui ele nao volta a aprovado nem
                      -- a reprovado, e a aprovacao antiga da IA nao disputa o registro (regra 2) com as novas.
                      and not coalesce((percepcao::jsonb->>'aguardando_rejulgamento')::boolean, false)
                    order by ligacao""")
    linhas = cur.fetchall()
    base, info, resposta_de, fotos_de = {}, {}, {}, {}
    todos = set()
    for lig, v, resp, ids, proc, chk, just, fotos in linhas:
        lig = str(lig)
        # a resposta que a checagem conferiu (vizinho, ficha do Maps, redes) e a base que ela usou, quando gravadas
        resp = (chk or {}).get("resposta_checada") or resp
        b = (chk or {}).get("base") or base_da_aprovacao(resp, proc)
        base[lig] = (b, ids or [])
        info[lig] = (v, chk or {}, just, proc)
        resposta_de[lig], fotos_de[lig] = resp or {}, fotos or []
        todos.update(_pid(x) for x in b if _pid(x) is not None)
        todos.update(int(i) for i in (ids or []) if str(i).isdigit())
    ctx = Contexto(con, base.keys(), todos)
    validos, removidos = {}, {}
    for lig, (b, ids) in base.items():
        validos[lig], removidos[lig] = validar(ctx, lig, b, ids, resposta_de[lig])

    # REGRA 2: UM POI, UMA INSTALACAO
    por_poi = collections.defaultdict(list)
    for lig, vs in validos.items():
        for p in vs:
            por_poi[p].append(lig)
    duvida = set()
    exclus = collections.Counter()
    for p, ligs in por_poi.items():
        if len(ligs) < 2:
            continue
        cr = (ctx.poi.get(p) or {}).get("complemento", "")
        casam = [l for l in ligs if _numeros(cr) and _numeros(ctx.compl_inst.get(l, "")) and
                 not complemento_diverge(ctx.compl_inst.get(l, ""), cr)]
        dono = casam[0] if len(casam) == 1 else None
        for l in ligs:
            if l == dono:
                continue
            validos[l] = [x for x in validos[l] if x != p]
            if dono:
                removidos[l].append({"poi": p, "porque": "o registro é da instalação %s (complemento %s)" % (dono, cr)})
                exclus["poi dado a uma instalação pelo complemento"] += 1
            else:
                removidos[l].append({"poi": p, "porque": "candidato de %d instalações aprovadas, sem complemento que decida"
                                     % len(ligs)})
                duvida.add(l)
                exclus["poi sem dono entre várias instalações"] += 1

    # A ORDEM NAO PODE VIRAR MUDANCA: o banco devolve as linhas em qualquer
    # ordem, e sem isto a mesma checagem regravava 19 ligacoes a cada passada.
    for lig in removidos:
        removidos[lig] = sorted(removidos[lig], key=lambda r: (str(r.get("poi")), r.get("porque") or ""))
    agora = datetime.datetime.now().isoformat(timespec="seconds")
    placar = collections.Counter()
    mudancas = []
    for lig in base:
        v_atual, chk, just, proc = info[lig]
        v_ia = chk.get("veredito_ia") or v_atual
        if v_ia != "aprovado":
            continue
        if so_veredito and v_atual not in so_veredito:
            continue
        if so_qualificacao and ctx.qualificacao.get(lig) not in so_qualificacao:
            continue
        v_novo, porque = decidir(ctx, validos[lig], lig in duvida and not validos[lig], lig,
                                 resposta_de[lig], fotos_de[lig], proc, ids=base[lig][1])
        if v_novo == "aprovado" and chk.get("contraprova"):
            v_novo, porque = "revisao_humana", chk["contraprova"]
        placar["%s -> %s" % (v_ia, v_novo)] += 1
        novo_chk = {"regra": REGRA, "veredito_ia": v_ia, "veredito": v_novo, "porque": porque,
                    "validos": validos[lig], "removidos": removidos[lig], "em": agora}
        for chave in ("resposta_checada", "base", "contraprova", "redes_sociais", "ficha_do_maps"):
            if chave in chk:
                novo_chk[chave] = chk[chave]
        mesmo = (v_novo == v_atual and (chk.get("validos") == validos[lig])
                 and (chk.get("removidos") == removidos[lig]) and chk.get("porque") == porque)
        if mesmo or (not chk and v_novo == v_atual and not removidos[lig]):
            continue
        just_ia = chk.get("justificativa_ia", just) if chk else just
        nova_just = just_ia if v_novo == "aprovado" else "[checagem: %s] %s" % (porque, just_ia or "")
        novo_chk["justificativa_ia"] = just_ia
        mudancas.append((lig, v_atual, v_novo, nova_just, novo_chk, just))
        # O QUE MUDA, DE QUE PARA QUE (16/09/2026): o ensaio contava as mudanças sem dizer quantas ganham ou perdem
        placar["muda · %s -> %s" % (v_atual, v_novo)] += 1
    placar.update({"exclusividade · " + k: n for k, n in exclus.items()})
    log("   checagem: %d aprovadas pela IA · %s" % (sum(n for k, n in placar.items() if "->" in k),
                                                    dict(sorted(placar.items()))))
    log("   %d ligação(ões) mudam de veredito ou de nota" % len(mudancas))
    # A LISTA DO ENSAIO (16/09/2026): para ver exemplos do que mudaria antes de aplicar
    if saida_mudancas:
        with open(saida_mudancas, "w", encoding="utf-8") as f:
            json.dump([{"ligacao": l, "de": va, "para": vn, "porque": c.get("porque")}
                       for l, va, vn, _nj, c, _ja in mudancas], f, ensure_ascii=False)
    if aplicar and mudancas:
        if saida_antes:
            with open(saida_antes, "w", encoding="utf-8") as f:
                json.dump([{"ligacao": l, "veredito": va, "justificativa": ja} for l, va, _vn, _nj, _c, ja in mudancas],
                          f, ensure_ascii=False)
        gravadas = 0
        for lig, _va, v_novo, nova_just, novo_chk, _ja in mudancas:
            cur.execute("""update radar_comercial.ligacao_veredito
                              set veredito = %s, justificativa = %s,
                                  percepcao = jsonb_set(percepcao::jsonb, '{checagem}', %s::jsonb)
                            where ligacao = %s""", (v_novo, nova_just, json.dumps(novo_chk, ensure_ascii=False), lig))
            gravadas += cur.rowcount
        con.commit()
        # RLS FILTRA CALADO: conta o que o banco gravou, e nao o que se pediu.
        log("   o banco gravou %d de %d" % (gravadas, len(mudancas)))
        placar["gravadas"] = gravadas
    return placar


def checar_uma(con, lig, v, resposta, ids, processo="enxuto de 12/09/2026", fotos=None):
    """As regras 1, 3, 4, 5 e 6 para UMA ligacao, na hora do veredito. A regra 2
    (um POI, uma instalacao) precisa das outras aprovadas: roda no fim da rodada.
    `fotos`: os rotulos das fotos que a IA viu, na ordem — a regra 6 le a data neles."""
    if v not in ("aprovado", "revisao_humana"):
        return v, None
    promover = v == "revisao_humana"
    if promover and not processo_leve(processo):
        return v, None
    resposta = sem_foto_de_vizinho(resposta, fotos)
    resposta, ficha_vazia = sem_ficha_do_maps_vazia(con, resposta, ids, fotos)
    # A REDE SOCIAL DA BUSCA E FONTE PROPRIA (dono do produto, 15/09/2026): confirmada pelo codigo — post no endereco
    # com o nome de um registro —, entra nas fontes; a que so a IA citou sai.
    redes = []
    try:
        import fonte_da_busca as fdb
        with con.cursor() as k:
            redes = fdb.redes_sociais_no_endereco(k, lig, ids)
    except Exception:                                          # noqa: BLE001
        redes = []
    confirmadas = {x["rede"].lower() for x in redes}
    resposta = dict(resposta or {})
    resposta["fontes"] = [f for f in (resposta.get("fontes") or []) if _fonte(f) not in REDES_SOCIAIS or _fonte(f) in confirmadas]
    resposta["fontes"] += sorted(confirmadas)
    resposta["aderentes"] = [dict(a, fontes=[f for f in (a.get("fontes") or []) if _fonte(f) not in REDES_SOCIAIS or _fonte(f) in confirmadas])
                             if isinstance(a, dict) else a for a in (resposta.get("aderentes") or [])]
    resposta["_redes"] = redes
    b = base_da_aprovacao(resposta, processo)
    if not b:
        # A IA APROVOU SEM LISTAR ADERENTES (353327, 15/09/2026): a base sai das provas que ela citou — a busca que
        # confirma o registro e a rede social com o nome dele —, e com um registro so na ligacao, ele
        b = [x.get("poi") for x in (resposta.get("busca") or []) if isinstance(x, dict) and x.get("confirma") and _pid(x.get("poi"))]
        b += [x["poi"] for x in redes]
        if not b and len(ids or []) == 1:
            b = list(ids)
        b = list(dict.fromkeys(_pid(x) for x in b if _pid(x) is not None))
    ctx = Contexto(con, [lig], [_pid(x) for x in b if _pid(x) is not None] + list(ids or []))
    validos, removidos = validar(ctx, lig, b, ids, resposta)
    if promover:
        # A FONTE UNICA TAMBEM PROMOVE (dono do produto, 15/09/2026, 22h): o codigo so derrubava. A 309179 tinha
        # o letreiro "FUNILARIA OLIVEIRA" na fachada de out/2025 e a IA mandou para revisao por "falta a segunda
        # fonte" — a regra dele diz que a fachada com sinal de ate 2 anos basta.
        basta, texto = fonte_unica_basta(ctx, validos, resposta, fotos) if validos else (False, "sem registro válido")
        if not basta:
            return v, None
        v_novo, porque = decidir(ctx, validos, False, lig, resposta, fotos, processo, ia_aprovou=False)
        if v_novo != "aprovado":
            return v, None
        porque = "promovida pela fonte única: %s" % texto
    else:
        v_novo, porque = decidir(ctx, validos, False, lig, resposta, fotos, processo, ids=ids)
    # AS CONTRAPROVAS FICAM GRAVADAS (auditoria das aprovadas do R_000): o fim de rodada (`revisar`) refazia a decisao
    # so com `decidir` e reaprovava o que a placa de aluga/vende, a ficha do Maps vazia, a placa do vizinho e a rede
    # social nao confirmada tinham segurado.
    _vago, contraprova = vago_depois_das_provas(ctx, validos, fotos, redes)
    contraprova = contraprova or contraprova_de_anuncio(con, ctx, lig, validos, redes)
    if v_novo == "aprovado" and contraprova:
        v_novo, porque = "revisao_humana", contraprova
    if promover and v_novo != "aprovado":
        return v, None
    return v_novo, {"regra": REGRA, "veredito_ia": v, "veredito": v_novo, "porque": porque,
                    "promovida": promover or None,
                    "validos": validos, "removidos": removidos, "redes_sociais": redes, "ficha_do_maps": ficha_vazia,
                    "resposta_checada": resposta, "base": b, "contraprova": contraprova,
                    "em": datetime.datetime.now().isoformat(timespec="seconds")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--antes", default="", help="arquivo JSON para guardar o veredito anterior das que mudam")
    p.add_argument("--mudancas", default="", help="arquivo JSON com a lista do que mudaria (vale no ensaio)")
    p.add_argument("--so-veredito", default="", help="só grava a mudança de quem tem hoje estes vereditos (vírgula)")
    p.add_argument("--so-qualificacao", default="", help="só grava a mudança destas qualificações (vírgula), ex.: SIM")
    a = p.parse_args(argv)
    con = bc.conectar()
    r = revisar(con, a.aplicar, saida_antes=a.antes or None, saida_mudancas=a.mudancas or None,
                so_veredito={x.strip() for x in a.so_veredito.split(",") if x.strip()} or None,
                so_qualificacao={x.strip().upper() for x in a.so_qualificacao.split(",") if x.strip()} or None)
    con.close()
    return r


if __name__ == "__main__":
    main()
