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
from functools import lru_cache

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

# MAIS DE DOIS NOMES NA MESMA PORTA E UM PREDIO, NAO UMA DUVIDA DE NOME.
#
# Regra do dono do produto, 27/08/2026: "mais de 2 itens de nome diferente no
# mesmo lugar ja nao e apenas ambiguidade de nome do mesmo estabelecimento
# (...) mais de 2 significa um shopping ou multilojas, nesse caso cada um e um
# estabelecimento mesmo".
#
# DOIS ainda pode ser o mesmo negocio escrito de duas formas -- "Restaurante
# Tempero e Arte" e "Tempero & Arte" no mesmo numero. TRES ou mais nao: e
# galeria, shopping, centro clinico, campus.
#
# O QUE ISSO CONSERTOU, medido em Canoas, 27/08/2026:
#
#     ParkShoppingCanoas   + Pista de Patinacao (Iceland)   -- o shopping
#                            fundido com a pista dentro dele, pelo dominio
#                            `parkshoppingcanoas.com.br`, que todas as lojas
#                            compartilham
#     UniRitter - Bloco C  + Predio B Uniritter             -- predios
#                            diferentes do mesmo campus
#
# Nos dois casos a evidencia que fundia -- mesmo endereco, mesmo dominio, 12 m
# -- e verdadeira e nao identifica ninguem: no 4545 da Farroupilha ha 91
# estabelecimentos ativos, e todos tem esse endereco, esse dominio e essa
# coordenada.
TETO_MULTILOJA = 2         # ate 2 nomes na mesma porta ainda e duvida de nome

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


# ==========================================================================
# MEMORIA DAS FUNCOES DE TEXTO -- e o que torna a comparacao viavel.
#
# MEDIDO em Canoas, 27/08/2026, perfilando 200 mil pares reais:
#
#     72,4 s no total, e o gargalo NAO era a decisao:
#         unicodedata.category .... 39,3 milhoes de chamadas
#         str.join ................  2,9 milhoes (37 s acumulados)
#         re.sub ..................  1,9 milhao
#
# Tudo isso e tirar acento e limpar pontuacao. O trabalho era REPETIDO: sao
# 35.321 POIs e 2,5 milhoes de pares, entao cada POI aparece em ~140 pares e o
# nome dele era normalizado 140 vezes, sempre com o mesmo resultado.
#
# As cinco funcoes abaixo sao PURAS -- mesma string entra, mesma string sai --
# e por isso podem ser lembradas sem mudar nenhum veredito. O cache e por
# string, nao por par: 35 mil normalizacoes em vez de 5 milhoes.
#
# O TETO DE 200.000 cobre uma capital inteira com folga (nome, logradouro,
# telefone e site de cada POI). Passando disso o LRU descarta o menos usado, o
# que degrada o desempenho e nunca a correcao.
#
# `tokens` devolve FROZENSET de proposito: conjunto mutavel em cache seria
# corrompido pelo primeiro chamador que o alterasse. As operacoes `&` e `|`
# funcionam igual e devolvem conjunto novo.
# ==========================================================================
@lru_cache(maxsize=200_000)
def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=200_000)
def norm_nome(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", _sem_acento(s)).strip()


def nome_util(s: str) -> str:
    """O nome, ou vazio quando ele não identifica nada.

    ISTO FALTAVA, E CUSTOU CINCO PISCINAS. Em 26/08/2026, em Bento Gonçalves,
    quatro fusões foram aplicadas entre POIs a 96–119 m um do outro com o
    motivo "nomes iguais (100%)". Os nomes eram, os dois, a palavra "nan" — o
    vazio do pandas que a importação do i9 ainda grava porque roda código
    antigo. Eram cinco clubes com piscina distintos, virados um só.

    Eu já protegia domínio, telefone e logradouro contra esse vazio; esqueci o
    campo mais óbvio. A guarda vale para qualquer buraco escrito como texto, e
    também para o nome vazio de verdade — dois POIs sem nome não são "o mesmo
    negócio", são dois pontos sobre os quais o nome não diz nada.
    """
    t = _sem_acento(s or "").strip()
    return "" if t in _VAZIO else (s or "")


@lru_cache(maxsize=200_000)
def tokens(s: str) -> frozenset:
    """Tokens com mais de 2 letras. `de`, `da`, `do` não distinguem nada e
    inflariam a semelhança de qualquer par."""
    return frozenset(t for t in norm_nome(s).split() if len(t) > 2)


def semelhanca_nome(a: str, b: str) -> float:
    """Jaccard sobre tokens, e não distância de edição.

    "Padaria Silva" e "Silva Padaria" são o mesmo negócio com as palavras
    trocadas; `difflib` os trata como distantes.
    """
    a, b = nome_util(a), nome_util(b)
    if not a or not b:
        return 0.0                 # sem nome nao ha semelhanca a medir
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


@lru_cache(maxsize=200_000)
def so_digitos(s: str) -> str:
    if _sem_acento(s or "").strip() in _VAZIO:
        return ""
    d = re.sub(r"\D", "", s or "")
    # DDI do Brasil quando o número vem completo: 5551999998888 e 51999998888
    # são o mesmo telefone escrito por duas fontes.
    if len(d) > 11 and d.startswith("55"):
        d = d[2:]
    return d


@lru_cache(maxsize=200_000)
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

    # A MULTILOJA VIRA MARCA, E NAO CORTE -- e este bloco ja foi um corte.
    #
    # Em 27/08/2026 esta funcao passou a DESCARTAR o par quando os dois POIs
    # estavam no mesmo lugar, o lugar tinha mais de dois nomes distintos e os
    # nomes deles diferiam. A intencao era certa: no 4545 da Farroupilha ha 181
    # estabelecimentos, e endereco, dominio e coordenada sao identicos para os
    # 181 -- nenhum identifica ninguem.
    #
    # O CORTE ERRAVA 32% DAS VEZES, medido sobre as recusas reais em Canoas:
    #
    #     Master Sonho Colchoes  + Master Sonho Colchoes | Canoas   0 m
    #     Preciosa Boutique Ataca+ Preciosa Boutique Atacado        0 m
    #     Crazy Som - Locacao    + Crazy Som                        7 m
    #
    # Sao o MESMO negocio. `semelhanca_nome` e Jaccard sobre tokens, e um nome
    # que e o outro MAIS UM SUFIXO cai para 0,75 -- abaixo do limiar de 0,8.
    # Um numero fixo nao distingue "sufixo de filial" de "outra loja".
    #
    # A IA DISTINGUE. Perguntada sobre 120 desses pares:
    #
    #     Unimed Porto Alegre + Coloprocto ......... DIFERENTE   certo
    #     Agah + Agencia Treehauss ................. DIFERENTE   certo
    #     NGA Moveis Hospitalares + NGA Metalurgica  DIFERENTE   certo
    #     Master Sonho Colchoes + ... | Canoas ..... MESMO       certo
    #     Crazy Som - Locacao + Crazy Som .......... MESMO       certo
    #
    # Regra do dono do produto, 28/08/2026: e melhor que mais pares CHEGUEM a
    # IA e ela resolva -- "mesmo o shopping tendo varios no mesmo endereco,
    # cada um viraria um ponto individual porque seus nomes mostram que
    # claramente sao pontos diferentes". O nome e a evidencia; ler nome e o que
    # a IA faz melhor que um limiar.
    #
    # A marca continua existindo e vai para o banco (`pois.multiloja`), porque
    # saber que um ponto esta num predio de varias lojas e util na tela e na
    # revisao. Ela so nao decide mais nada sozinha.

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

    # MESMO NOME + MESMA RUA = CONFIANÇA MÁXIMA, e a distância não derruba.
    #
    # Regra do dono do produto, 26/08/2026: "se tiver o mesmo nome e mesmo
    # logradouro é confiança máxima, mesmo a 50 metros ou 100".
    #
    # E ela está certa contra a medição: dos pares de mesmo nome e mesma rua em
    # Canoas, 89% estão a menos de 20 m — mas 11% ficam além, e o excedente
    # dessas duplicatas é de 11.983 POIs. A distância que os separa não é o
    # estabelecimento ser outro: é a coordenada de uma das fontes errar. Duas
    # bases dizendo o MESMO nome na MESMA rua é evidência mais forte que
    # qualquer proximidade.
    #
    # O teto de 1 km existe para não unir a filial do outro bairro: "Farmácia
    # São João" na "Avenida Brasil" pode legitimamente ser duas lojas se a
    # avenida cruza a cidade. Dentro de 1 km, numa mesma rua, é o mesmo ponto.
    # O NÚMERO DA PORTA DESEMPATA, e sem ele esta regra fundia rede legítima.
    #
    # MEDIDO em Canoas, 27/08/2026, sobre os 266 pares que a regra fundiria:
    #
    #     mesmo número de porta ....  167   "Posto Ipiranga, Guilherme Schell
    #                                        1046" x o mesmo, a 7,7 km — a
    #                                        coordenada de uma fonte é que erra
    #     número DIFERENTE .........   48   "Saque e Pague" nos números 1011 e
    #                                        1623 da mesma avenida: caixas
    #                                        eletrônicos distintos da mesma rede
    #     sem número num dos lados ..   51   ambíguo
    #
    # Os 48 são o contra-exemplo que faltava. Nome de rede numa avenida longa
    # repete de verdade, e fundir apagaria ponto real do mapa. Já quando os dois
    # dizem a MESMA porta, ser 7 km é a coordenada mentindo — nunca a identidade.
    #
    # Por isso: número igual funde a qualquer distância (o endereço já provou o
    # que a coordenada nega). Número diferente NÃO usa este atalho — cai nos
    # pontos e, se houver evidência, na IA. Faltando número, o teto de 1 km
    # volta a valer, porque aí só resta a proximidade para sustentar.
    if mesma_rua and sem >= 0.85:
        num_a, num_b = logradouro_de(a)[1], logradouro_de(b)[1]
        if num_a and num_b and num_a == num_b:
            motivos.append("mesmo nome, mesma rua e mesmo número")
            return {"confianca": 10, "pontos": max(pontos, 10), "motivos": motivos,
                    "dist_m": d, "decisao": "fundir",
                    "porque": "mesmo endereço exato — a distância é a coordenada "
                              "errando, não outro estabelecimento"}
        if not (num_a and num_b) and d <= 1000:
            motivos.append("mesmo nome na mesma rua")
            return {"confianca": 10, "pontos": max(pontos, 10), "motivos": motivos,
                    "dist_m": d, "decisao": "fundir",
                    "porque": "mesmo nome e mesmo logradouro — a distância não desmente"}

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
