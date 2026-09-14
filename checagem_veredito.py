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

REGRA = "checagem do codigo de 12/09/2026"

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
        cur = con.cursor()
        cur.execute("set statement_timeout = '300s'")
        ligs = sorted({str(x) for x in ligacoes})
        ids = sorted({int(x) for x in pois if str(x).isdigit()})
        cur.execute("""select num_ligacao::text, coalesce(end_ligacao,''), coalesce(nom_bairro,''),
                              coalesce(qualificacao,'')
                         from resources_root.cadastro_corsan where num_ligacao::text = any(%s)""", (ligs,))
        self.compl_inst, self.qualificacao = {}, {}
        for l, e, b, q in cur.fetchall():
            self.compl_inst[l] = complemento_da_instalacao(e, b)
            self.qualificacao[l] = q.upper()
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


def validar(ctx, lig, base, ids):
    """(validos, removidos): a regra 1, por ligacao."""
    ids = {int(i) for i in (ids or []) if str(i).isdigit()}
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
        if pid not in validos:
            validos.append(pid)
    return validos, removidos


def decidir(ctx, validos, perdeu_por_duvida, lig=None):
    """O veredito final de uma ligacao que a IA aprovou: regras 1, 3 e 4."""
    if not validos:
        if perdeu_por_duvida:
            return "revisao_humana", ("o registro que aprovava também é candidato de outra(s) instalação(ões) "
                                      "e não dá para dizer de qual é")
        return "reprovado", "nenhum registro válido sustenta a aprovação"
    if all(ctx.e_mei(p) for p in validos):
        return "revisao_humana", "aprovada só por MEI (%s)" % ", ".join("#%s" % p for p in validos)
    # REGRA 4: a qualificacao SIM com analise humana nao aprova sozinha.
    if lig is not None and ctx.qualificacao.get(str(lig)) == "SIM_COM_ANALISE_HUMANA":
        return "revisao_humana", "qualificação SIM com análise humana: a decisão é do usuário"
    return "aprovado", None


def revisar(con, aplicar=False, log=print, saida_antes=None):
    """A checagem de TODAS as ligacoes que a IA aprovou, com a exclusividade.

    Recomeca sempre do veredito da IA (`percepcao.checagem.veredito_ia`), entao
    rodar de novo nao acumula efeito. Devolve o placar das mudancas.
    """
    cur = con.cursor()
    cur.execute("set statement_timeout = '600s'")
    cur.execute("""select ligacao, veredito, percepcao::jsonb->'resposta', percepcao::jsonb->'ids',
                          coalesce(percepcao::jsonb->>'processo','antigo'), percepcao::jsonb->'checagem',
                          justificativa
                     from radar_comercial.ligacao_veredito
                    where veredito = 'aprovado'
                       or percepcao::jsonb->'checagem'->>'veredito_ia' = 'aprovado'
                    order by ligacao""")
    linhas = cur.fetchall()
    base, info = {}, {}
    todos = set()
    for lig, v, resp, ids, proc, chk, just in linhas:
        lig = str(lig)
        b = base_da_aprovacao(resp, proc)
        base[lig] = (b, ids or [])
        info[lig] = (v, chk or {}, just, proc)
        todos.update(_pid(x) for x in b if _pid(x) is not None)
        todos.update(int(i) for i in (ids or []) if str(i).isdigit())
    ctx = Contexto(con, base.keys(), todos)
    validos, removidos = {}, {}
    for lig, (b, ids) in base.items():
        validos[lig], removidos[lig] = validar(ctx, lig, b, ids)

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
        v_novo, porque = decidir(ctx, validos[lig], lig in duvida and not validos[lig], lig)
        placar["%s -> %s" % (v_ia, v_novo)] += 1
        novo_chk = {"regra": REGRA, "veredito_ia": v_ia, "veredito": v_novo, "porque": porque,
                    "validos": validos[lig], "removidos": removidos[lig], "em": agora}
        mesmo = (v_novo == v_atual and (chk.get("validos") == validos[lig])
                 and (chk.get("removidos") == removidos[lig]) and chk.get("porque") == porque)
        if mesmo or (not chk and v_novo == v_atual and not removidos[lig]):
            continue
        just_ia = chk.get("justificativa_ia", just) if chk else just
        nova_just = just_ia if v_novo == "aprovado" else "[checagem: %s] %s" % (porque, just_ia or "")
        novo_chk["justificativa_ia"] = just_ia
        mudancas.append((lig, v_atual, v_novo, nova_just, novo_chk, just))
    placar.update({"exclusividade · " + k: n for k, n in exclus.items()})
    log("   checagem: %d aprovadas pela IA · %s" % (sum(n for k, n in placar.items() if "->" in k),
                                                    dict(sorted(placar.items()))))
    log("   %d ligação(ões) mudam de veredito ou de nota" % len(mudancas))
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


def checar_uma(con, lig, v, resposta, ids, processo="enxuto de 12/09/2026"):
    """As regras 1 e 3 para UMA ligacao, na hora do veredito. A regra 2 (um POI,
    uma instalacao) precisa das outras aprovadas: roda no fim da rodada."""
    if v != "aprovado":
        return v, None
    b = base_da_aprovacao(resposta, processo)
    ctx = Contexto(con, [lig], [_pid(x) for x in b if _pid(x) is not None] + list(ids or []))
    validos, removidos = validar(ctx, lig, b, ids)
    v_novo, porque = decidir(ctx, validos, False, lig)
    return v_novo, {"regra": REGRA, "veredito_ia": v, "veredito": v_novo, "porque": porque,
                    "validos": validos, "removidos": removidos,
                    "em": datetime.datetime.now().isoformat(timespec="seconds")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--antes", default="", help="arquivo JSON para guardar o veredito anterior das que mudam")
    a = p.parse_args(argv)
    con = bc.conectar()
    r = revisar(con, a.aplicar, saida_antes=a.antes or None)
    con.close()
    return r


if __name__ == "__main__":
    main()
