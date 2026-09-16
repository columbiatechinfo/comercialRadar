# -*- coding: utf-8 -*-
"""classificacao.py — o SEGMENTO do negócio e O QUE A IA VIU NAS IMAGENS, gravados em cada veredito.

Dono do produto (16/09/2026): a SEEK precisa filtrar os vereditos por ramo (supermercado, restaurante, oficina,
banca...) e pelo tipo de prova visual (fachada de rua que confirma, letreiro, vitrine, foto do Google, nenhuma
imagem). Antes isso só existia em consulta ad-hoc; agora todo julgamento grava `percepcao.classe` e a fila da
SEEK devolve os dois campos prontos para o filtro.

O SEGMENTO SAI DOS REGISTROS que sustentaram o veredito (os `validos` da checagem; sem eles, os candidatos da
ligação), lidos em três lugares, nesta ordem: o CNAE da Receita, a categoria publicada (Maps, iFood, estadual) e
o nome. Uma ligação pode ter mais de um segmento — um prédio com padaria e salão devolve os dois.

O VISUAL SAI DA RESPOSTA DA IA: quais imagens ela disse que confirmam o uso e que sinal concreto ela descreveu.
"""
from __future__ import annotations

import re
import unicodedata

#: (id, rótulo na tela, regex sobre categoria+nome, prefixos de CNAE)
SEGMENTOS = [
    ("supermercado", "supermercado / mercado / atacado",
     r"supermercado|hipermercado|minimercado|mercearia|atacad|armazem", ("4711", "4712", "4639", "4691")),
    ("restaurante", "restaurante / lanchonete / padaria",
     r"restaurante|lanchonete|pizza|churrasc|cafeteria|cafe |padaria|confeitaria|doceria|marmita|lanche|"
     r"acai|hamburg|food|pastel|sorvet|doces|bolos|comida|sushi|esfiha|japonesa|brasileira|bar\b|boteco|petisc",
     ("5611", "5612", "5620", "4721", "1091")),
    ("oficina", "oficina mecânica / auto",
     r"oficina|mecanic|funilaria|auto center|autopec|auto pec|borracharia|lava.?jato|lava.?rapido|"
     r"retifica|eletrica automotiva|som automotivo|revendedora de carros|concessionaria",
     ("4520", "4530", "4511", "4541")),
    # SO A BANCA DE VERDADE (16/09/2026): o CNAE 4789 e "comercio varejista de outros produtos" e trazia
    # 535 ambulantes e prestadores como se fossem banca de jornal.
    ("banca", "banca / jornal / revista", r"banca de |jornaleir|revistaria|\bbanca\b", ("4761",)),
    ("posto", "posto de combustível", r"posto de|combustivel|gasolina|posto ipiranga|posto shell", ("4731", "4732")),
    ("supermat", "construção / materiais",
     r"material de constru|constru|engenharia|reforma|vidracaria|marmoraria|serralheria|madeireira|"
     r"eletrica e hidraulica|ferragem|tintas", ("41", "42", "43", "4744", "4741", "4742", "4743")),
    ("industria", "indústria / fabricação",
     r"industria|fabrica|metalurgi|fundicao|marcenaria|graficas?|grafica|costura|confeccao|panificadora industrial",
     ("10", "11", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28",
      "29", "30", "31", "32", "33")),
    ("transporte", "transporte / logística",
     r"transport|logistic|mudanca|frete|entrega|guincho|taxi|motoboy", ("492", "493", "494", "521", "522")),
    ("saude", "saúde / farmácia",
     r"clinica|hospital|laboratorio|odonto|dentista|farmacia|drogaria|fisioterap|psicolog|veterinari|pet shop",
     ("86", "4771", "4772", "7500")),
    ("escola", "escola / curso / creche", r"escola|colegio|creche|faculdade|curso|autoescola|idiomas", ("85",)),
    ("academia", "academia / esporte", r"academia|ginastica|crossfit|pilates|muay|jiu.?jitsu|futebol society", ("931",)),
    ("hotel", "hotel / pousada / motel", r"hotel|pousada|motel|hostel", ("551", "552")),
    ("beleza", "salão / barbearia / estética",
     r"salao|beleza|barbearia|cabeleir|estetica|manicure|design de sobrancelha|studio de unhas|tatuagem",
     ("9602", "9609")),
    ("loja", "loja / comércio varejista",
     r"loja|comercio|vestuario|roupa|calcad|moveis|papelaria|presente|otica|joalheria|bazar|variedades|"
     r"eletrodomestic|celular|informatica|floricultura|distribuidora",
     ("47", "4530", "4649")),
    ("servico", "serviço / escritório",
     r"escritorio|contabil|advocacia|advogad|imobiliaria|corretora|consultoria|arquitet|despachante|"
     r"assistencia tecnica|conserto|chaveiro|grafica rapida|seguros|agencia",
     ("69", "70", "71", "73", "74", "77", "78", "80", "81", "82", "68", "66", "64")),
    ("religioso", "igreja / associação", r"igreja|templo|centro espirita|associacao|sindicato|ong\b", ("94",)),
]

#: (id, rótulo na tela) — o que a IA disse das imagens
VISUAL = [
    ("fachada_rua", "fachada na foto de rua confirma"),
    ("foto_google", "foto publicada do Google confirma"),
    ("letreiro", "letreiro, placa ou faixa"),
    ("vitrine", "vitrine, porta de loja ou mercadoria"),
    ("sem_imagem", "nenhuma imagem confirma"),
]

_LETREIRO = re.compile(r"letreir|plac|totem|luminos|plotad|adesiv|faixa|banner|fachada com nome|nome na fachada")
_VITRINE = re.compile(r"vitrine|porta de loja|balcao|mercadoria|toldo|expositor|produtos a mostra|mesas na calcada")


def _normal(t) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t)


def segmentos_de(registros) -> list:
    """[id] dos segmentos de uma lista de registros [{fonte, categoria, nome, cnae}]. Sem casar nada: ['outros']."""
    achados = []
    for r in registros or []:
        texto = _normal("%s %s" % (r.get("categoria") or "", r.get("nome") or ""))
        cnae = re.sub(r"\D", "", str(r.get("cnae") or ""))
        for sid, _rot, regex, cnaes in SEGMENTOS:
            if sid in achados:
                continue
            if (regex and re.search(regex, texto)) or (cnae and cnaes and cnae.startswith(tuple(cnaes))):
                achados.append(sid)
                break
    return achados or ["outros"]


def visual_de(resposta, rotulos) -> list:
    """[id] do que a IA viu nas imagens, pela resposta dela e pelos rótulos das fotos que recebeu."""
    f = (resposta or {}).get("fotos") or {}
    if not isinstance(f, dict) or f.get("confirmam") is not True:
        return ["sem_imagem"]
    achados = []
    for n in f.get("quais") or []:
        try:
            rot = str((rotulos or [])[int(n) - 1])
        except (TypeError, ValueError, IndexError):
            continue
        if rot.startswith("foto de rua") and "fachada_rua" not in achados:
            achados.append("fachada_rua")
        elif rot.startswith("foto publicada") and "foto_google" not in achados:
            achados.append("foto_google")
    sinal = _normal("%s %s" % (f.get("sinal") or "", f.get("o_que_mostram") or ""))
    if _LETREIRO.search(sinal):
        achados.append("letreiro")
    if _VITRINE.search(sinal):
        achados.append("vitrine")
    return achados or ["sem_imagem"]


def registros_da_ligacao(cur, ligacao, pois=None) -> list:
    """Os registros que interessam: os `pois` dados (os válidos da checagem) ou os candidatos da ligação."""
    ids = sorted({int(x) for x in (pois or []) if str(x).lstrip("#").isdigit()})
    if ids:
        cur.execute("""select lower(coalesce(p.fonte,'')), coalesce(p.categoria,''), coalesce(p.nome,''),
                              coalesce(rd.cnae,'')
                         from radar_comercial.pois p
                         left join radar_comercial.receita_data rd on rd.poi_id = p.id
                        where p.id = any(%s)""", (ids,))
    else:
        cur.execute("""select lower(coalesce(p.fonte,'')), coalesce(p.categoria,''), coalesce(p.nome,''),
                              coalesce(rd.cnae,'')
                         from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id and p.fundido_em is null
                         left join radar_comercial.receita_data rd on rd.poi_id = p.id
                        where lp.ligacao = %s and lp.descartado_em is null""", (str(ligacao),))
    return [{"fonte": f, "categoria": c, "nome": n, "cnae": e} for f, c, n, e in cur.fetchall()]


def classificar(con, ligacao, resposta, rotulos, pois=None) -> dict:
    """{'segmentos': [...], 'visual': [...]} — o que vai para `percepcao.classe` e para a fila da SEEK."""
    with con.cursor() as cur:
        regs = registros_da_ligacao(cur, ligacao, pois)
    return {"segmentos": segmentos_de(regs), "visual": visual_de(resposta, rotulos)}
