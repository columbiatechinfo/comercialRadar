# -*- coding: utf-8 -*-
"""bairro.py — o bairro de um ponto: o que a fonte publicou, limpo, e o da coordenada.

POR QUE UM MODULO PROPRIO, e nao funcoes dentro de `resolver_logradouro`.
Importar o resolvedor poe `skills/ajuste-logradouro` na frente do `sys.path`, e
a biblioteca de normalizacao de logradouro que ele carrega NAO e a mesma que
`cruzar_ligacao._via` usa — as duas se chamam `normalizacao_base`, e a que for
importada primeiro fica. Medido em 11/09/2026: com o resolvedor importado antes,
`_via("RUA IRMA MARIA HILTGARDIS")` deixava de tirar o "RUA", nada batia com o
nome sem tipo da Corsan, e o casamento por endereco caiu de 40 mil POIs para
238 — sem um erro sequer.

Quem precisa do bairro (o casamento, a revisao) importa ESTE modulo, que nao
carrega biblioteca de logradouro nenhuma.
"""
import json
import os
import urllib.request

import regra_vinculo as rv

#: O BAIRRO DA COORDENADA sai do Nominatim, e nao do OSRM: o OSRM responde com
#: o no da malha e o nome da rua, e nao sabe o que e bairro. Medido em
#: 11/09/2026 em 600 ligacoes de Canoas, com o bairro da Corsan de gabarito:
#: Nominatim 558 iguais, Photon 541, registro mais proximo do CNEFE 533 — e 600
#: consultas em 1,5 s.
NOMINATIM = (os.environ.get("NOMINATIM_REVERSO_URL")
             or "http://%s:7200" % (os.environ.get("A2L_LAN") or "192.168.3.10"))

#: As fontes cujo bairro publicado PERDE para o da coordenada quando os dois
#: divergem. Medido em 11/09/2026 contra a Corsan, no mesmo rua+numero:
#: Receita concorda 95%, IBGE 91%, Maps 92%, Cadastur 99%; iFood 20% — o Park
#: Shopping aparece nele em tres bairros — e Airbnb 5 de 17. O Airbnb nem
#: publica bairro de verdade: o dele ja e geocodificacao reversa, feita pela
#: plataforma sobre um pino que ela embaralha de proposito. Com a troca, o iFood
#: passou a concordar com a Corsan em 264 de 277 enderecos.
FONTES_BAIRRO_FRACO = ("ifood", "airbnb")


def _pegar(url: str, timeout: int = 15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "radar/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as h:
            return json.load(h)
    except Exception:                                          # noqa: BLE001
        return None


#: O bairro publicado, conferido contra o vocabulario do IBGE da cidade, por
#: valor. Sao poucos milhares de grafias distintas para 120 mil POIs.
_CONHECIDO: dict = {}


def _bairro_conhecido(valor: str, conhecidos: set) -> bool:
    k = " ".join(rv._tokens_bairro(valor))
    if k not in _CONHECIDO:
        _CONHECIDO[k] = any(rv.bairros_iguais(valor, x) for x in conhecidos)
    return _CONHECIDO[k]


def limpar_bairro(valor, cidade: str, conhecidos: set = None) -> tuple:
    """`(bairro, duvida)`: o bairro publicado, limpo, e por que ele nao basta.

    `duvida` e None quando o bairro serve como esta. Quando nao serve, o bairro
    volta assim mesmo (se houver) e quem chamou decide consultar a coordenada.
    """
    if valor is None or not str(valor).strip():
        return None, "a fonte nao publicou bairro"
    v = " ".join(str(valor).split()).upper()
    if not rv.bairro_util(v):
        return None, "a fonte escreveu %r no lugar do bairro" % str(valor)[:30]
    k = " ".join(rv._tokens_bairro(v))
    c = " ".join(rv._tokens_bairro(cidade))
    # O NOME DA CIDADE NO LUGAR DO BAIRRO. E o que o Foursquare faz: 10.003
    # dos 10.373 "bairros" dele em Canoas sao "Canoas".
    if c and (k == c or k.startswith(c + " ")):
        return None, "a fonte escreveu o nome da cidade no lugar do bairro"
    if conhecidos and not _bairro_conhecido(v, conhecidos):
        return v, "o bairro publicado nao existe no cadastro do IBGE da cidade"
    return v, None


_REVERSO: dict = {}


def bairro_reverso(lat, lng, cidade: str = "") -> str:
    """O bairro onde a coordenada cai, pelo Nominatim local. None se nao der.

    A FALHA NAO ENTRA NO CACHE: um Nominatim lento num instante nao pode
    carimbar a coordenada como "sem bairro" pelo resto da execucao.
    """
    if lat is None or lng is None:
        return None
    k = (round(float(lat), 5), round(float(lng), 5))
    if k in _REVERSO:
        return _REVERSO[k]
    d = _pegar("%s/reverse?lat=%s&lon=%s&format=jsonv2&zoom=18&addressdetails=1"
               % (NOMINATIM, lat, lng))
    if d is None:
        return None
    a = d.get("address") or {}
    b = (a.get("suburb") or a.get("neighbourhood") or a.get("quarter")
         or a.get("city_district"))
    v, duvida = limpar_bairro(b, cidade)
    r = v if (v and duvida is None) else None
    _REVERSO[k] = r
    return r
