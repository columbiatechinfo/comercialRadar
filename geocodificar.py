# -*- coding: utf-8 -*-
"""geocodificar.py — o endereço vira coordenada, com a precisão declarada.

QUANDO ISTO ENTRA

Depois que as chaves EXATAS falharam. O CNPJ contra a `cnpj_tratado` e o
endereço contra o CNEFE casam por igualdade: ou casam, ou não. Quando não casam,
sobra o endereço em texto livre — e jogá-lo fora é perder o ponto por causa de
uma vírgula.

A ORDEM, E POR QUE ELA É ESTA

    1. PHOTON           geocodificador do OSM, aqui no i9. Tolerante a endereço
                        sujo: devolve algo em 11 de 14 endereços que o Nominatim
                        recusa inteiros. Custa 0,1 s.
    2. NOMINATIM        mais estrito, também no i9. Segunda chance quando o
                        Photon não achou.
    3. PAINEL DO MAPS   busca o ESTABELECIMENTO pelo nome e devolve o pin que o
                        Google desenha para ele. É a única fonte que ganha
                        `porta`, porque é a única que encontra o NEGÓCIO em vez
                        de interpretar um texto — "Hotel Kleinville, Esteio"
                        acha o hotel; "Rua Tal, 402, 1a" acha, no melhor caso,
                        a rua.

O Maps é o MELHOR e vem por ÚLTIMO, e isso não é contradição: ele abre um
navegador com proxy e leva dezenas de segundos por consulta. Numa varredura de
doze mil endereços a diferença entre ele e o OSM é a diferença entre minutos e
dias. Então o barato tenta primeiro, e o caro só entra no resíduo — que é onde
ele vale cada segundo.

**Só depois que os três falharem é que existe "não encontrado".**

CASAR TEXTO NUNCA VALE "PORTA"

Mesmo quando o Photon responde `type: house`, o resultado é `porta_aprox`. Ele
achou UMA casa naquele logradouro, e não necessariamente o número pedido — a
diferença entre as duas coisas é justamente o que manda alguém tocar a
campainha errada. `porta` é reservado a quem encontra o estabelecimento.

O MUNICÍPIO É CONFERIDO

Nome de rua se repete no Brasil inteiro. O resultado que cai em município
diferente do pedido é DESCARTADO, não rebaixado: não é uma coordenada pior, é
outro lugar.
"""
from __future__ import annotations

import json
import os
import unicodedata
import urllib.parse
import urllib.request

import config  # noqa: F401

NOMINATIM = (os.environ.get("NOMINATIM_URL") or "").rstrip("/")
PHOTON = (os.environ.get("PHOTON_URL") or "").rstrip("/")
UA = {"User-Agent": "ComercialRadar/1.0 (geocodificacao interna)"}

# Classe → raio DECLARADO, em metros. Igual ao vocabulário da migração 0028.
INCERTEZA = {"porta": 15, "porta_aprox": 40, "via": 150,
             "bairro": 800, "municipio": 5000}

# O que o Photon devolve em `type` → a nossa classe.
_PHOTON = {
    "house": "porta_aprox", "building": "porta_aprox",
    "street": "via",
    "district": "bairro", "locality": "bairro", "neighbourhood": "bairro",
    "city": "municipio", "county": "municipio", "state": "municipio",
}

# O que o Nominatim devolve em `addresstype` → a nossa classe.
_NOMINATIM = {
    "building": "porta_aprox", "house": "porta_aprox",
    "house_number": "porta_aprox", "place": "porta_aprox",
    "amenity": "porta_aprox", "shop": "porta_aprox", "tourism": "porta_aprox",
    "road": "via", "highway": "via",
    "suburb": "bairro", "neighbourhood": "bairro", "quarter": "bairro",
    "village": "municipio", "town": "municipio", "city": "municipio",
    "municipality": "municipio", "state": "municipio",
}


def _norm(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").upper())
                   if unicodedata.category(c) != "Mn").strip()


def _json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _no_brasil(la: float, lo: float) -> bool:
    """A mesma guarda do ingestor. Coordenada fora do país é dado errado."""
    return -34 <= la <= 6 and -74 <= lo <= -34


def _resposta(la, lo, precisao: str, fonte: str, texto: str = "",
              via: str = "") -> dict:
    return {"lat": float(la), "lng": float(lo), "precisao": precisao,
            "fonte": fonte, "incerteza_m": INCERTEZA.get(precisao),
            "encontrado": texto, "via": via}


# Tipo de via e conectivo saem da comparacao: um lado escreve "Rua Bento
# Goncalves" e o outro "BENTO GONCALVES", e comparar com o tipo dentro faria
# tudo divergir.
_TIPO_VIA = {"RUA", "AVENIDA", "AV", "TRAVESSA", "TV", "RODOVIA", "ROD",
             "ESTRADA", "ESTR", "PRACA", "ALAMEDA", "AL", "LARGO", "BECO",
             "LINHA", "SERVIDAO", "ACESSO", "VIELA", "R"}
_SEM_PESO = {"DE", "DA", "DO", "DAS", "DOS", "E", "SAO", "SANTA", "SANTO",
             "DOM", "DONA", "GENERAL", "CORONEL", "DOUTOR", "PROFESSOR"}


# Numeral por extenso -> algarismo. "Rua Sete de Setembro" e "Rua 7 de
# Setembro" sao a MESMA rua, e o Brasil esta cheio delas: 15 de Novembro, 1o de
# Maio, 24 de Outubro. Sem esta tabela elas reprovavam todas.
_NUMERAL = {
    "PRIMEIRO": "1", "UM": "1", "DOIS": "2", "TRES": "3", "QUATRO": "4",
    "CINCO": "5", "SEIS": "6", "SETE": "7", "OITO": "8", "NOVE": "9",
    "DEZ": "10", "ONZE": "11", "DOZE": "12", "TREZE": "13", "QUATORZE": "14",
    "CATORZE": "14", "QUINZE": "15", "DEZESSEIS": "16", "DEZESSETE": "17",
    "DEZOITO": "18", "DEZENOVE": "19", "VINTE": "20", "TRINTA": "30",
}


def _nucleo_via(nome: str) -> set:
    """As palavras que dao IDENTIDADE a uma via.

    NUMERO DE PORTA FICA DE FORA. O leitor de endereco nem sempre consegue
    separa-lo — "Santana 402 1a" e "RUA DAS NACOES 640 SANDER" chegam aqui com
    o numero colado no nome —, e um "402" dentro do nucleo faz a via nunca casar
    com a "Rua Santana" que o geocodificador devolve.

    A EXCECAO e o numero que ABRE o nome: em "7 de Setembro" ele E a via. Numero
    no comeco fica; nos demais lugares, sai.
    """
    palavras = [p for p in _norm(nome).replace(",", " ").replace("-", " ").split()
                if p]
    nucleo = set()
    for i, p in enumerate(palavras):
        p = _NUMERAL.get(p, p)
        if p.isdigit():
            # So o que abre o nome. "7 de Setembro" fica; "Santana 402" nao.
            if i <= 1:
                nucleo.add(p)
            continue
        if len(p) > 2 and p not in _TIPO_VIA and p not in _SEM_PESO:
            nucleo.add(p)
    return nucleo


def via_confere(pedida: str, devolvida: str) -> bool:
    """A via devolvida e mesmo a que se pediu?

    ISTO EXISTE PORQUE O PHOTON E DIFUSO. Medido em 24/08/2026, comparando o
    endereco pedido com o que ele devolveu:

        pedido "Rua Hipolito Jose da Costa"  ->  "Rua Daniel Cruz da Costa"
        pedido "Avenida Esperanca"           ->  "Avenida Getulio Vargas"
        pedido "Rua Bruna Mastroiani"        ->  "Rua Emilio Grando"

    Ele casa por semelhanca e devolve OUTRA RUA com cara de acerto. Sem esta
    conferencia, 7% dos pontos saiam rotulados `via` — que promete "a rua
    certa" — apontando para uma rua que ninguem pediu. Rotulo errado e pior que
    ausencia, porque rotulo e acreditado.

    A regra e 60% das palavras de identidade. "Metade" era frouxa demais: com
    tres palavras ela pedia uma so, e "Hipolito Jose da Costa" passava contra
    "Daniel Cruz da Costa" — os dois tem "Costa". Exigir TODAS reprovaria
    abreviacao legitima ("Frederico Augusto Ritter" contra "Frederico Ritter").

    A comparacao entre palavras tolera GRAFIA. O Cadastur escreve "Aristides
    Stumph" e o OSM tem "Aristides Stumpf"; ha "Mello" contra "Melo" e
    "Goncalves" contra "Gonsalves" aos montes. Com casamento exato, 29,7% dos
    pontos reprovavam — e a maioria era a mesma rua escrita de outro jeito.

    O piso de 0,82 foi medido: aceita Stumph/Stumpf (0,833) e Mello/Melo
    (0,889), e recusa Hipolito/Daniel (0,286) e Esperanca/Getulio (0,125).
    """
    from difflib import SequenceMatcher

    a, b = _nucleo_via(pedida), _nucleo_via(devolvida)
    if not a:
        return True          # nao havia o que conferir
    if not b:
        return False
    def cobertura(alvo, contra):
        n = sum(1 for x in alvo
                if any(x == y or SequenceMatcher(None, x, y).ratio() >= 0.82
                       for y in contra))
        return n / len(alvo)

    # NOS DOIS SENTIDOS, e vale o maior. O lado pedido chega sujo — com bairro e
    # complemento que o leitor nao conseguiu separar —, enquanto o devolvido
    # vem limpo. Medir so a cobertura do sujo reprovava "NACOES 640 SANDER"
    # contra "Rua das Nacoes", que e a mesma rua: 1 de 2. Pelo lado limpo, 1 de
    # 1. O maior dos dois e o que reflete se as duas falam da mesma via.
    return max(cobertura(a, b), cobertura(b, a)) >= 0.6



def _consultas(endereco: str, cidade: str, uf: str) -> list:
    """As formas de perguntar, da mais específica para a mais tolerante.

    O ENDEREÇO COMO ESTÁ vai primeiro, e não a versão limpa. Ele carrega
    complemento, bairro e CEP, e o geocodificador usa tudo isso — o Photon é
    tolerante justamente para receber texto humano. A versão limpa entra depois,
    como segunda chance para quando o ruído atrapalha em vez de ajudar.
    """
    lugar = ", ".join(x for x in (cidade, uf, "Brasil") if x)
    saida = []
    if endereco:
        saida.append(f"{endereco}, {lugar}")
    # OS DOIS FORMATOS. `partes_cadastur` le o do MTur, que nao tem separador
    # nenhum ("Getulio Vargas 4861 Canoas Centro CEP: 9201024"); `partes_endereco`
    # le o do Maps, separado por virgula e traco ("R. Vera Cruz - Mato Grande,
    # Canoas - RS, 92415-000").
    #
    # Tentar so um deixava o outro sem consulta limpa: numa conferencia de 13
    # POIs vindos do Maps, DEZ nao geocodificaram — e nao por falta de dado, mas
    # porque o leitor errado devolvia o endereco inteiro como nome de rua.
    for ler in ("partes_endereco", "partes_cadastur"):
        try:
            import cruzar_bases as cb
            rua, numero, bairro = getattr(cb, ler)(endereco, cidade)
            if not rua:
                continue
            # A forma LIMPA entra sempre, mesmo quando o nome da rua já aparece
            # no endereço cru — é obvio que aparece, ela foi extraída dali. A
            # primeira versão testava justamente isso e descartava a consulta
            # que funciona.
            limpo = f"{rua}, {numero}" if numero else rua
            saida.append(f"{limpo}, {lugar}")
            # Com o bairro, para desempatar rua homônima dentro da cidade.
            if bairro:
                saida.append(f"{limpo}, {bairro}, {lugar}")
        except Exception:
            continue
    # Vistos, mas sem repetir — perguntar duas vezes a mesma coisa é uma ida à
    # rede por nada.
    vistos, unicas = set(), []
    for q in saida:
        if q not in vistos:
            vistos.add(q)
            unicas.append(q)
    return unicas


def _confere_municipio(achado: dict, cidade: str) -> bool:
    """O resultado tem de cair no município pedido.

    Nome de rua se repete no Brasil inteiro: "Rua das Flores" existe em quase
    toda cidade. Um resultado noutro município não é uma coordenada pior — é
    outro lugar, e mandaria alguém para a cidade errada.
    """
    if not cidade:
        return True
    alvo = _norm(cidade)
    for chave in ("city", "town", "village", "municipality", "county", "name"):
        v = achado.get(chave)
        if v and _norm(v) == alvo:
            return True
    # `display_name` do Nominatim é a linha inteira; conter o município basta.
    return alvo in _norm(achado.get("display_name", ""))


def por_photon(endereco: str, cidade: str = "", uf: str = "") -> dict | None:
    """Geocodificador do OSM, tolerante a endereço sujo."""
    if not PHOTON:
        return None
    for consulta in _consultas(endereco, cidade, uf):
        try:
            d = _json(PHOTON + "/api?" + urllib.parse.urlencode(
                {"q": consulta, "limit": 5, "lang": "pt"}))
        except Exception:
            continue
        for f in (d.get("features") or []):
            pr = f.get("properties") or {}
            if not _confere_municipio(pr, cidade):
                continue
            coord = (f.get("geometry") or {}).get("coordinates") or []
            if len(coord) != 2:
                continue
            lo, la = coord
            if not _no_brasil(la, lo):
                continue
            classe = _PHOTON.get(pr.get("type"))
            if not classe:
                continue
            return _resposta(la, lo, classe, "photon",
                             ", ".join(x for x in (pr.get("name"),
                                                   pr.get("street"),
                                                   pr.get("housenumber"),
                                                   pr.get("city")) if x),
                             via=(pr.get("street") or pr.get("name") or ""))
    return None


def por_nominatim(endereco: str, cidade: str = "", uf: str = "") -> dict | None:
    """Mais estrito que o Photon. Segunda chance, e confirmação."""
    if not NOMINATIM:
        return None
    for consulta in _consultas(endereco, cidade, uf):
        try:
            d = _json(NOMINATIM + "/search?" + urllib.parse.urlencode(
                {"q": consulta, "format": "jsonv2", "limit": 5,
                 "addressdetails": 1, "countrycodes": "br"}))
        except Exception:
            continue
        for a in (d or []):
            end = a.get("address") or {}
            if not _confere_municipio({**end, "display_name":
                                       a.get("display_name", "")}, cidade):
                continue
            try:
                la, lo = float(a["lat"]), float(a["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not _no_brasil(la, lo):
                continue
            classe = _NOMINATIM.get(a.get("addresstype"))
            if not classe:
                # `place_rank` como rede de segurança: 30 é casa, 26-27 é rua.
                # O domínio do `addresstype` cresce com o tempo, e cair fora
                # dele não pode significar descartar um resultado bom.
                rank = a.get("place_rank")
                classe = ("porta_aprox" if rank and int(rank) >= 30 else
                          "via" if rank and int(rank) >= 25 else None)
            if not classe:
                continue
            return _resposta(la, lo, classe, "nominatim",
                             a.get("display_name", "")[:120],
                             via=(end.get("road") or end.get("pedestrian")
                                  or a.get("name") or ""))
    return None


def por_maps(nome: str, cidade: str = "", endereco: str = "") -> dict | None:
    """O painel do Google, buscando o ESTABELECIMENTO.

    A ÚNICA fonte que ganha `porta`, porque é a única que encontra o negócio em
    vez de interpretar um texto. Custa uma sessão de navegador com proxy, então
    é opcional e vem por último no custo, mesmo sendo a primeira em qualidade.
    """
    if not nome:
        return None
    try:
        from ferramenta_maps import consultar_maps
    except Exception:
        return None
    r = consultar_maps(nome, cidade) or {}
    if r.get("erro") or r.get("confere_com_o_nome_pedido") is False:
        return None
    la, lo = r.get("lat"), r.get("lng")
    if la is None or lo is None or not _no_brasil(float(la), float(lo)):
        return None
    return _resposta(la, lo, "porta", "maps_painel",
                     r.get("endereco") or r.get("nome") or "")


# Malha do IBGE por (municipio, UF), em memoria. Uma consulta por municipio, e
# nao por endereco: numa execucao estadual sao 369 municipios contra milhares de
# enderecos.
_MALHA: dict = {}


def dentro_do_municipio(la: float, lo: float, cidade: str, uf: str) -> bool | None:
    """O ponto cai dentro do polígono do município? `None` = não deu para saber.

    A CONFERÊNCIA POR NOME NÃO BASTA, e a medição provou: em 4.445 endereços
    geocodificados, três passaram pelo nome e caíram longe —

        General Câmara   171 km
        Rio Grande       270 km
        Sarandi          499 km  ← era Sarandi do PARANÁ

    O geocodificador devolve o `city` que ele acha, e nem sempre é o que se
    pediu; homônimo entre estados passa direto. O polígono do IBGE não tem essa
    ambiguidade.

    `None` quando não há malha daquele município — e aí quem chama decide. Não
    ter malha não pode virar "reprovado", senão município sem cobertura para de
    geocodificar inteiro.
    """
    if not cidade or not uf:
        return None
    chave = (_norm(cidade), (uf or "").upper())
    if chave not in _MALHA:
        try:
            import base_comum as bc
            ref = bc.conectar_referencia()
            try:
                with ref.cursor() as k:
                    k.execute("""select cod_municipio, nome from ibge_malha
                                  where uf = %s""", (chave[1],))
                    for cod, nome_m in k.fetchall():
                        _MALHA.setdefault((_norm(nome_m), chave[1]), cod)
            finally:
                ref.close()
        except Exception:
            return None
        _MALHA.setdefault(chave, None)
    cod = _MALHA.get(chave)
    if not cod:
        return None
    try:
        import base_comum as bc
        ref = bc.conectar_referencia()
        try:
            with ref.cursor() as k:
                # 2 km de tolerancia: o poligono do IBGE e simplificado, e um
                # endereco na divisa cai fora por dezenas de metros. Recusar
                # esses seria perder ponto bom por causa do desenho da malha.
                k.execute("""select ST_DWithin(geom::geography,
                                    ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,
                                    2000)
                               from ibge_malha where cod_municipio = %s""",
                          (lo, la, cod))
                linha = k.fetchone()
                return bool(linha[0]) if linha else None
        finally:
            ref.close()
    except Exception:
        return None


def buscar(endereco: str = "", cidade: str = "", uf: str = "",
           nome: str = "", usar_maps: bool = False) -> dict | None:
    """A cascata inteira. Devolve o melhor achado, ou None.

    `usar_maps` liga o ÚLTIMO degrau. Ele abre navegador com proxy, e numa
    rodada de 13 mil POIs isso é a diferença entre minutos e dias — por isso é
    opção, e não padrão. Mas para o resíduo que o OSM não resolve ele vale cada
    segundo: é a única fonte que dá `porta`.

    Sem ele, "não encontrado" significa apenas "o OSM não achou".
    """
    # A VIA PEDIDA, para conferir contra a devolvida.
    via_pedida = ""
    for ler in ("partes_endereco", "partes_cadastur"):
        try:
            import cruzar_bases as cb
            v = getattr(cb, ler)(endereco, cidade)[0]
            if v:
                via_pedida = v
                break
        except Exception:
            continue

    def confere(r):
        """O achado vale? Município pelo polígono, via pelo nome."""
        if not r:
            return None
        if dentro_do_municipio(r["lat"], r["lng"], cidade, uf) is False:
            return None
        # O achado do MAPS é o estabelecimento, não uma via — conferir nome de
        # rua ali reprovaria o acerto. As outras duas fontes interpretam texto,
        # e é lá que a semelhança engana.
        if r["fonte"] != "maps_painel" and via_pedida:
            if not via_confere(via_pedida, r.get("via") or r.get("encontrado", "")):
                return None
        return r

    # O BARATO PRIMEIRO. Photon e Nominatim moram no i9 e respondem em
    # centésimos de segundo.
    for tentativa in (por_photon, por_nominatim):
        r = confere(tentativa(endereco, cidade, uf))
        if r:
            return r

    # E SÓ ENTÃO O MAPS, que abre navegador com proxy. Ele é o único que dá
    # precisão de PORTA, porque procura o estabelecimento em vez de interpretar
    # o texto do endereço — e é justamente no resíduo que o OSM não resolve que
    # essa diferença aparece.
    if usar_maps:
        r = confere(por_maps(nome, cidade, endereco))
        if r:
            return r
    return None


def melhor(a: dict | None, b: dict | None) -> dict | None:
    """Entre dois achados, o de classe melhor. Empate fica com o primeiro."""
    ordem = ["porta", "porta_aprox", "via", "bairro", "municipio"]
    if not a:
        return b
    if not b:
        return a
    return a if ordem.index(a["precisao"]) <= ordem.index(b["precisao"]) else b
