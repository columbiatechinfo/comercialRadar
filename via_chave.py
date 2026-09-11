# -*- coding: utf-8 -*-
"""via_chave.py — a chave de comparacao da rua: a normalizacao da skill E as
equivalencias que ela nao faz (12/09/2026).

Medido com os POIs do iFood que nunca se vincularam: a Corsan escreve o numero
da rua por extenso e com artigo, e o iFood e o Google em algarismo e sem:

    RUA 22 DE OUTUBRO 75          VINTE E DOIS DE OUTUBRO 75
    RUA PETUNIAS 132              DAS PETUNIAS 132
    SENADOR DARCY RIBEIRO 55      SENADOR DARCI RIBEIRO 55

`norm_logradouro` (skill ajuste-logradouro) resolve tipo, abreviatura e acento.
Esta chave vem DEPOIS dela e so junta variantes do mesmo nome: numero em
algarismo vira extenso, artigo e preposicao saem, Y vira I, letra dobrada vira
uma. Nunca separa o que ela ja juntava.
"""
import re

_UNI = ["ZERO", "UM", "DOIS", "TRES", "QUATRO", "CINCO", "SEIS", "SETE", "OITO", "NOVE", "DEZ",
        "ONZE", "DOZE", "TREZE", "QUATORZE", "QUINZE", "DEZESSEIS", "DEZESSETE", "DEZOITO", "DEZENOVE"]
_DEZ = ["", "", "VINTE", "TRINTA", "QUARENTA", "CINQUENTA", "SESSENTA", "SETENTA", "OITENTA", "NOVENTA"]
_CEM = ["", "CENTO", "DUZENTOS", "TREZENTOS", "QUATROCENTOS", "QUINHENTOS", "SEISCENTOS",
        "SETECENTOS", "OITOCENTOS", "NOVECENTOS"]


def extenso(n):
    """0 a 1999 por extenso, como a Corsan escreve ('VINTE E DOIS')."""
    if n < 20:
        return _UNI[n]
    if n < 100:
        d, u = divmod(n, 10)
        return _DEZ[d] + ("" if u == 0 else " E " + _UNI[u])
    if n == 100:
        return "CEM"
    if n < 1000:
        c, r = divmod(n, 100)
        return _CEM[c] + ("" if r == 0 else " E " + extenso(r))
    if n < 2000:
        r = n - 1000
        return "MIL" + ("" if r == 0 else " E " + extenso(r))
    return str(n)


_ARTIGO = {"DA", "DAS", "DE", "DO", "DOS"}
# grafias do mesmo som: QUATORZE/CATORZE, CINQUENTA/CINCOENTA
_SOM = [("CATORZE", "QUATORZE"), ("CINCOENTA", "CINQUENTA")]


def chave(via_normalizada):
    """A chave de comparacao, a partir da via JA normalizada pela skill."""
    t = " ".join(extenso(int(x)) if x.isdigit() and int(x) < 2000 else x
                 for x in re.findall(r"[A-Z0-9]+", (via_normalizada or "").upper()))
    for a, b in _SOM:
        t = t.replace(a, b)
    t = t.replace("Y", "I").replace("PH", "F").replace("TH", "T")
    t = re.sub(r"([A-Z])\1+", r"\1", t)
    partes = [p for p in t.split() if p not in _ARTIGO]
    return " ".join(partes)
