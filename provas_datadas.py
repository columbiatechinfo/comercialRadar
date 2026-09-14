# -*- coding: utf-8 -*-
"""provas_datadas.py — o numero e as provas DATADAS de cada registro (14/09/2026).

Decisoes do dono do produto em 14/09/2026, depois de ver a IA aprovar pelo
vizinho e por foto antiga:

- A BUSCA WEB E COMPLEMENTAR. Sozinha nunca aprova.
- NUMERO DIFERENTE NAO E DESTA INSTALACAO. Registro que publica outro numero e
  outro imovel. Numero que nao aparece nao atrapalha; o diferente, sim.
- A DATA DA PROVA E ESSENCIAL. Prova de ate 2 anos vale; mais velha nao aprova.
  Sem prova recente, a ligacao vai para revisao humana. Entre fotos, a mais
  recente pesa mais.
- RECEITA ATIVA NA BASE ATUAL CONTA COMO RECENTE — e a Receita dizendo que o CNPJ
  existe hoje, mesmo que a situacao seja de anos atras. O estadual vale pela
  data de atualizacao que o Overture e o Foursquare declaram (migracao 0112).

UM LUGAR SO PARA AS DUAS PONTAS. O prompt (`avaliar_enxuto`) mostra estas provas
a IA, e a checagem (`checagem_veredito`) as confere depois dela. Se cada um
calculasse a idade do seu jeito, a IA aprovaria por uma prova que o codigo
julga velha — e o motivo gravado contaria outra historia.

O que conta como prova datada, por fonte:

    receita    CNPJ ativo na base da Receita (data = mes da base carregada)
    maps       avaliacao de cliente mais recente (data relativa + dia da leitura);
               foto publicada DO PROPRIO LUGAR com data (so a coleta desde
               14/09/2026 separa a foto do lugar da dos vizinhos)
    ifood      loja vista no iFood (quando a coleta a viu)
    estadual   atualizacao declarada pelo Overture / Foursquare
    ibge       CNEFE do Censo 2022 (sempre antiga para este fim)

A foto de rua (Street View) nao e de um registro: e da fachada da instalacao.
Ela prova quando a IA diz que mostra o comercio, e a idade dela vem no rotulo.
"""
from __future__ import annotations

import datetime
import re

#: Prova com ate este numero de meses e recente (dono do produto, 14/09/2026).
MESES_RECENTE = 24

_MES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
_UNIDADE_EM_DIAS = (("minuto", 0), ("hora", 0), ("dia", 1), ("semana", 7),
                    ("mes", 30), ("mês", 30), ("meses", 30), ("ano", 365))
#: O CNEFE usado e o do Censo 2022.
DATA_CNEFE = datetime.date(2022, 8, 1)


def hoje():
    return datetime.date.today()


def meses(data, ref=None):
    """Meses inteiros entre `data` e hoje (ou `ref`)."""
    if not data:
        return None
    ref = ref or hoje()
    return (ref.year - data.year) * 12 + (ref.month - data.month)


def recente(data, ref=None):
    m = meses(data, ref)
    return m is not None and m <= MESES_RECENTE


def mes_ano(data):
    return "%s/%d" % (_MES[data.month - 1], data.year) if data else "sem data"


def data_de_texto(s):
    """'2025-10', '2025-10-01', '2025-10-01T..' -> date(2025, 10, 1)."""
    m = re.match(r"^(\d{4})-(\d{2})", str(s or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


def dias_atras(texto):
    """'5 meses atrás' -> 150; 'um ano atrás' -> 365. None quando nao reconhece."""
    s = str(texto or "").strip().lower()
    if not s:
        return None
    if s.startswith("hoje") or s.startswith("agora"):
        return 0
    m = re.search(r"(\d+)", s)
    if m:
        n = int(m.group(1))
    elif re.search(r"\b(um|uma)\b", s):
        n = 1
    else:
        return None
    for unidade, dias in _UNIDADE_EM_DIAS:
        if unidade in s:
            return n * dias
    return None


# ----------------------------------------------------------------- numero --

def _faixa(txt):
    m = re.search(r"(\d{1,6})\s*(?:-|/|a|ao)\s*(\d{1,6})", str(txt or ""))
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if a < b and b - a <= 40 else None


def numero_confere(num_registro, num_instalacao):
    """'igual' | 'diferente' | 'sem_numero'. Faixa ("120-126") casa quem esta dentro."""
    inst = re.search(r"\d+", str(num_instalacao or ""))
    reg = re.search(r"\d+", str(num_registro or ""))
    if not inst or not reg:
        return "sem_numero"
    ni = int(inst.group(0))
    if ni == 0:
        return "sem_numero"
    fx = _faixa(num_registro)
    if fx and fx[0] <= ni <= fx[1]:
        return "igual"
    nr = int(reg.group(0))
    if nr == 0 or len(reg.group(0).lstrip("0")) > 6:
        return "sem_numero"
    return "igual" if nr == ni else "diferente"


def numero_do_endereco(endereco):
    """O numero da porta num endereco livre: 'Av. X, 2288 - Fatima, Canoas' -> '2288'.

    O CEP NAO E NUMERO DE PORTA. Endereco sem numero termina em 'Canoas - RS,
    92200-051', e a primeira versao leu o CEP como a porta: 12 registros sairam
    da base por "numero diferente (instalacao 570, registro 92200-051)" no
    ensaio de 14/09/2026.
    """
    for m in re.finditer(r",\s*(\d{1,6}(?:\s*[-/]\s*\d{1,6})?)\b", str(endereco or "")):
        g = m.group(1)
        if re.fullmatch(r"\d{5}\s*-\s*\d{3}", g) or re.fullmatch(r"\d{8}", g):
            continue
        return g
    return None


# ----------------------------------------------------------------- provas --

def _referencia_receita(cur):
    cur.execute("""select max(left(referencia, 7)) from resources_root.fonte_arquivos
                    where referencia ~ '^[0-9]{4}-[0-9]{2}/'""")
    r = cur.fetchone()
    return data_de_texto(r[0]) if r and r[0] else None


def carregar(con, poi_ids, ref=None):
    """{poi_id: {"numero": str|None, "provas": [{fonte, o_que, data, meses, recente}]}}.

    Tudo em consultas por lote (`= any(ids)`): a checagem revisa milhares de
    aprovadas de uma vez, e uma consulta por POI seria o gargalo.
    """
    ids = sorted({int(x) for x in (poi_ids or []) if str(x).lstrip("#").isdigit()})
    saida = {pid: {"numero": None, "fonte": None, "provas": []} for pid in ids}
    if not ids:
        return saida
    ref = ref or hoje()
    cur = con.cursor()

    def prova(pid, fonte, o_que, data):
        saida[pid]["provas"].append({"fonte": fonte, "o_que": o_que, "data": data,
                                     "meses": meses(data, ref), "recente": recente(data, ref)})

    cur.execute("""select p.id, lower(coalesce(p.fonte,'')), p.endereco, p.detalhado_em, p.ifood_visto_em,
                          lr.numero, rd.situacao_cadastral, rd.bruto->>'numero'
                     from radar_comercial.pois p
                     left join lateral (select numero from radar_comercial.logradouro_resolvido l
                                         where l.poi_id = p.id and l.forca = 'prova' and l.numero is not null
                                         order by l.resolvido_em desc nulls last limit 1) lr on true
                     left join radar_comercial.receita_data rd on rd.poi_id = p.id
                    where p.id = any(%s)""", (ids,))
    linhas = cur.fetchall()
    ancora = {}
    base_receita = None
    for pid, fonte, endereco, detalhado_em, ifood_visto, num_lr, situacao, num_rf in linhas:
        s = saida[pid]
        s["fonte"] = fonte
        s["numero"] = num_rf or num_lr or numero_do_endereco(endereco)
        ancora[pid] = detalhado_em
        if fonte == "receita" and str(situacao or "").strip() in ("02", "2", "ATIVA"):
            if base_receita is None:
                base_receita = _referencia_receita(cur) or ref
            prova(pid, "receita", "CNPJ ativo na base da Receita de %s" % mes_ano(base_receita), base_receita)
        if fonte == "ibge":
            prova(pid, "ibge", "endereço no CNEFE do Censo 2022", DATA_CNEFE)
        if ifood_visto:
            prova(pid, "ifood", "loja vista no iFood em %s" % mes_ano(ifood_visto.date()), ifood_visto.date())

    # iFood pela tabela da loja (o numero dela vale mais que o endereco livre)
    cur.execute("""select poi_id, max(visto_em), max(numero) from radar_comercial.ifood_merchant
                    where poi_id = any(%s) group by poi_id""", (ids,))
    for pid, visto, numero in cur.fetchall():
        if numero:
            saida[pid]["numero"] = numero
        if visto and not any(p["fonte"] == "ifood" for p in saida[pid]["provas"]):
            prova(pid, "ifood", "loja vista no iFood em %s" % mes_ano(visto.date()), visto.date())

    # Maps: a avaliacao mais recente, pela data relativa ancorada no dia da leitura
    cur.execute("""select poi_id, array_agg(data) from radar_comercial.comentarios
                    where poi_id = any(%s) and fonte = 'maps' and data is not null group by poi_id""", (ids,))
    for pid, datas in cur.fetchall():
        dias = [d for d in (dias_atras(x) for x in datas) if d is not None]
        if dias and ancora.get(pid):
            quando = (ancora[pid] - datetime.timedelta(days=min(dias))).date()
            quando = datetime.date(quando.year, quando.month, 1)
            prova(pid, "maps", "avaliação de cliente mais recente no Google: %s" % mes_ano(quando), quando)

    # Maps: foto publicada do proprio lugar, com data (coleta desde 14/09/2026)
    cur.execute("""select poi_id, max(data_imagem) from radar_comercial.images_urls
                    where poi_id = any(%s) and secao is not null and data_imagem is not null
                      and url like '%%googleusercontent%%' group by poi_id""", (ids,))
    for pid, d in cur.fetchall():
        dt = data_de_texto(d)
        if dt:
            prova(pid, "maps", "foto publicada mais recente no Google: %s" % mes_ano(dt), dt)

    # Estadual: a atualizacao declarada pela fonte (migracao 0112)
    for tabela, nome in (("overture_data", "Overture"), ("foursquare_data", "Foursquare")):
        try:
            cur.execute("""select poi_id, atualizado_na_fonte, status_na_fonte from radar_comercial.""" + tabela
                        + """ where poi_id = any(%s) and atualizado_na_fonte is not null""", (ids,))
            for pid, dt, st in cur.fetchall():
                o_que = "atualizado no %s em %s" % (nome, mes_ano(dt))
                if st:
                    o_que += " (status %s)" % st
                prova(pid, "estadual", o_que, dt)
        except Exception:                                      # noqa: BLE001
            # antes da migracao 0112 a coluna nao existe: segue sem a data estadual
            con.rollback()
    return saida


def recentes(info):
    """As provas recentes de um registro (sem a busca web, que nunca entra aqui)."""
    return [p for p in (info or {}).get("provas") or [] if p["recente"]]


def resumo(info):
    """'CNPJ ativo na base da Receita de ago/2026; avaliação ... mar/2021 (antiga)'."""
    partes = []
    for p in sorted((info or {}).get("provas") or [], key=lambda x: x["data"] or datetime.date.min, reverse=True):
        partes.append(p["o_que"] + ("" if p["recente"] else " — ANTIGA, mais de 2 anos"))
    return "; ".join(partes)


def data_do_rotulo(rotulo):
    """A data que o rotulo da foto traz: 'sv_frente (2025-10)' / 'foto publicada no Google (ago/2025)'."""
    m = re.search(r"\((\d{4}-\d{2})", str(rotulo or ""))
    if m:
        return data_de_texto(m.group(1))
    m = re.search(r"\((%s)/(\d{4})\)" % "|".join(_MES), str(rotulo or ""))
    if m:
        return datetime.date(int(m.group(2)), _MES.index(m.group(1)) + 1, 1)
    return None
