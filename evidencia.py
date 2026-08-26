# -*- coding: utf-8 -*-
"""evidencia.py — por que dois POIs seriam o mesmo ponto, e quanto disso é prova.

AS REGRAS SÃO DO DONO DO PRODUTO, 25/08/2026, e cada uma tem um porquê:

**Endereço é o maior indício.** Logradouro canônico igual com número igual é a
evidência mais forte que existe aqui — duas fontes apontando a mesma porta.
Mesmo SEM o número bater, estar a menos de 20 m já é motivo para perguntar à IA.

**Telefone não decide sozinho.** No varejo brasileiro o mesmo número atende dois
negócios do mesmo dono, ou é o número da galeria. Medido no RS: 74,4% dos pares
unidos por telefone eram estabelecimentos DIFERENTES. Ele soma, nunca decide.

**Site pesa mais que telefone**, porque é um domínio: padariadobairro.com.br
pertence a um negócio, não a um prédio. Mas os dois só valem dentro de 20 m — o
mesmo domínio a 3 km são duas filiais, e filial é outro ponto comercial para
quem vai cobrar tarifa.

POR QUE A NORMALIZAÇÃO IMPORTA AQUI

Sem ela "Avenida Gen. Flores da Cunha" e "Avenida General Flores da Cunha" são
duas ruas, e o par mais forte que existe — mesma rua, mesmo número — nunca é
visto. A tabela `logradouro_ajustado` é o que torna esse par comparável, e é por
isso que a etapa 6 vem antes desta.

O `tier` entra na decisão: CONFIRMA é dicionário fechado (R -> RUA), ALTA é
léxico com prova de dois imóveis distintos. REVISAR e HUMANO houve perda de
texto — a forma marcada não representa mais a rua inteira, e casar por ela
produziria par falso.
"""
from __future__ import annotations

import math
import re
import unicodedata

# Peso de cada evidência; a soma vira a confiança de 1 a 10.
#
# Os números não são graduação de opinião: eles codificam a ordem que o dono do
# produto declarou (endereço > site > telefone) e o piso que cada decisão exige.
PESO = {
    "endereco_exato": 5,   # logradouro canônico igual E número igual
    "endereco_rua": 2,     # mesma rua canônica, número diferente ou ausente
    # 4 E NÃO 3, e o número tem consequência: `MIN_PARA_IA` também é 4, então o
    # site SOZINHO (dentro do raio) já manda o par para a IA — e o telefone
    # sozinho não manda. É exatamente a ordem que o dono do produto declarou:
    # "site, por ser um domínio, tem mais peso que telefone".
    "site": 4,             # mesmo domínio, dentro do raio
    "telefone": 2,         # mesmo número, dentro do raio — nunca sozinho
    "nome": 3,             # nomes praticamente iguais
    "categoria": 1,        # mesmo ramo
}

RAIO_M = 20.0              # o raio que telefone e site exigem para valer
MIN_PARA_IA = 4            # abaixo disto não se pergunta: não há o que julgar
MIN_PARA_FUNDIR = 8        # daqui para cima funde sem IA

_TIER_VALE = {"CONFIRMA", "ALTA"}

# BURACO ESCRITO COMO TEXTO. Medido 25/08/2026: 28.394 POIs têm
# `website = 'nan'` e 17.193 têm `telefone = 'nan'` — um NaN do pandas que virou
# string numa ingestão. Sem isto, "nan" é um domínio como qualquer outro, e os
# 28 mil POIs sem site passariam a compartilhar o mesmo: cada par vizinho
# ganharia 4 pontos de evidência que não existe.
_VAZIO = {"", "nan", "none", "null", "n/a", "na", "-", "--", "?", "sem site",
          "não informado", "nao informado"}


# Domínio de rede social identifica a PLATAFORMA, não o negócio. Deixá-los
# passar faria toda loja com página no Facebook casar com toda outra.
_NAO_IDENTIFICA = {
    "", "instagram.com", "facebook.com", "fb.com", "wa.me", "api.whatsapp.com",
    "whatsapp.com", "linktr.ee", "google.com", "goo.gl", "maps.google.com",
    "ifood.com.br", "linkedin.com", "twitter.com", "x.com", "youtube.com",
    "tiktok.com", "bit.ly",
}


def distancia_m(la1, lo1, la2, lo2) -> float:
    """Haversine. Em 20 m qualquer aproximação plana serviria, mas o mesmo
    código julga pares de bairros distintos quando o dedup roda por cidade."""
    if None in (la1, lo1, la2, lo2):
        return float("inf")
    r = 6371000.0
    f1, f2 = math.radians(la1), math.radians(la2)
    df, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def norm_nome(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", _sem_acento(s)).strip()


def tokens(s: str) -> set:
    """Tokens com mais de 2 letras. `de`, `da`, `do` não distinguem nada e
    inflariam a semelhança de qualquer par."""
    return {t for t in norm_nome(s).split() if len(t) > 2}


def semelhanca_nome(a: str, b: str) -> float:
    """Jaccard sobre tokens, e não distância de edição.

    "Padaria Silva" e "Silva Padaria" são o mesmo negócio com as palavras
    trocadas; `difflib` os trata como distantes.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    # NOME DE UM TOKEN SÓ NÃO PODE VALER 100%.
    #
    # `tokens()` descarta o que tem 2 letras ou menos, e isso é certo para
    # "de"/"da"/"do" — mas transforma "Loja A" e "Loja B" em {loja} e {loja},
    # ou seja, dois negócios diferentes com semelhança perfeita. Medido: o par
    # ia a 3 pontos por nome idêntico que idêntico não era.
    #
    # Com um token só de cada lado, a comparação passa a ser sobre o nome
    # inteiro — onde o "A" e o "B" voltam a existir.
    if min(len(ta), len(tb)) < 2:
        return 1.0 if norm_nome(a) == norm_nome(b) else 0.0
    return len(ta & tb) / len(ta | tb)


def so_digitos(s: str) -> str:
    if _sem_acento(s or "").strip() in _VAZIO:
        return ""
    d = re.sub(r"\D", "", s or "")
    # DDI do Brasil quando o número vem completo: 5551999998888 e 51999998888
    # são o mesmo telefone escrito por duas fontes.
    if len(d) > 11 and d.startswith("55"):
        d = d[2:]
    return d


def dominio(url: str) -> str:
    """O domínio, sem `www` e sem caminho.

    padaria.com.br/contato e www.padaria.com.br são o mesmo negócio. Já
    instagram.com/lojaA e instagram.com/lojaB não são — e o domínio é o mesmo,
    que é a razão de `_NAO_IDENTIFICA` existir.
    """
    u = _sem_acento(url).strip()
    if u in _VAZIO:
        return ""
    u = re.sub(r"^\w+://", "", u).split("/")[0].split("?")[0]
    u = re.sub(r"^www\.", "", u)
    return "" if u in _NAO_IDENTIFICA or u in _VAZIO else u


def logradouro_de(poi: dict) -> tuple:
    """`(rua_canonica, numero)` — a forma marcada quando ela é confiável.

    Em REVISAR/HUMANO houve perda de texto e a forma marcada não serve para
    casar ninguém; aí cai para o original, que ao menos é o que a fonte disse.
    """
    tier = (poi.get("tier") or "").upper()
    if tier in _TIER_VALE and poi.get("logr_marcado"):
        rua = poi["logr_marcado"]
    else:
        rua = poi.get("logr_original") or ""
    if _sem_acento(rua).strip() in _VAZIO:
        rua = ""                       # 7.911 POIs têm `endereco = 'nan'`
    num = (poi.get("numero_canonico") or poi.get("numero") or "").strip()
    return norm_nome(rua), re.sub(r"\D", "", num)


def avaliar(a: dict, b: dict) -> dict:
    """Devolve `{confianca, pontos, motivos, dist_m, decisao, porque}`.

    `decisao` é `fundir`, `perguntar` ou `descartar`. O que fazer com isso é de
    quem chama — este módulo não escreve no banco nem conversa com a IA.
    """
    d = distancia_m(a.get("lat"), a.get("lng"), b.get("lat"), b.get("lng"))
    perto = d <= RAIO_M

    pontos, motivos = 0, []

    rua_a, num_a = logradouro_de(a)
    rua_b, num_b = logradouro_de(b)
    mesma_rua = bool(rua_a and rua_a == rua_b)
    if mesma_rua:
        if num_a and num_a == num_b:
            pontos += PESO["endereco_exato"]
            motivos.append(f"mesmo endereço: {rua_a} {num_a}")
        else:
            pontos += PESO["endereco_rua"]
            motivos.append(f"mesma rua: {rua_a}")

    # SITE E TELEFONE SÓ VALEM DENTRO DO RAIO.
    dom_a, dom_b = dominio(a.get("site", "")), dominio(b.get("site", ""))
    tem_site = bool(dom_a and dom_a == dom_b and perto)
    if tem_site:
        pontos += PESO["site"]
        motivos.append(f"mesmo domínio: {dom_a}")

    tel_a = so_digitos(a.get("telefone", ""))
    tel_b = so_digitos(b.get("telefone", ""))
    tem_tel = bool(len(tel_a) >= 8 and tel_a == tel_b and perto)
    if tem_tel:
        pontos += PESO["telefone"]
        motivos.append(f"mesmo telefone: {tel_a}")

    sem = semelhanca_nome(a.get("nome", ""), b.get("nome", ""))
    if sem >= 0.8:
        pontos += PESO["nome"]
        motivos.append(f"nomes iguais ({sem:.0%})")
    elif sem >= 0.5:
        motivos.append(f"nomes parecidos ({sem:.0%})")

    ca, cb = norm_nome(a.get("categoria", "")), norm_nome(b.get("categoria", ""))
    if ca and ca == cb:
        pontos += PESO["categoria"]
        motivos.append(f"mesma categoria: {ca}")

    if perto:
        motivos.append(f"a {d:.0f} m")

    # TELEFONE NÃO DECIDE SOZINHO. Se ele é a única evidência o par não sobe —
    # é a regra que separa a loja de dois nomes da galeria com um telefone só.
    outras = pontos - (PESO["telefone"] if tem_tel else 0)
    if tem_tel and outras == 0:
        return {"confianca": 1, "pontos": pontos, "motivos": motivos,
                "dist_m": d, "decisao": "descartar",
                "porque": "telefone é a única evidência, e ele não decide sozinho"}

    if pontos >= MIN_PARA_FUNDIR:
        # 8 pontos -> 8; cada 2 pontos a mais sobe 1, com teto em 10.
        conf = min(10, 8 + (pontos - MIN_PARA_FUNDIR) // 2)
        return {"confianca": conf, "pontos": pontos, "motivos": motivos,
                "dist_m": d, "decisao": "fundir", "porque": "evidência suficiente"}

    if pontos >= MIN_PARA_IA:
        return {"confianca": 5, "pontos": pontos, "motivos": motivos,
                "dist_m": d, "decisao": "perguntar",
                "porque": "evidência parcial — a IA decide"}

    # ENDEREÇO É O MAIOR INDÍCIO: mesma rua a menos de 20 m merece a pergunta
    # mesmo sem somar pontos para tanto, e mesmo sem número para confirmar.
    if mesma_rua and perto:
        return {"confianca": 4, "pontos": pontos, "motivos": motivos,
                "dist_m": d, "decisao": "perguntar",
                "porque": "mesma rua a menos de 20 m, sem número para confirmar"}

    return {"confianca": max(1, pontos), "pontos": pontos, "motivos": motivos,
            "dist_m": d, "decisao": "descartar", "porque": "evidência fraca demais"}
