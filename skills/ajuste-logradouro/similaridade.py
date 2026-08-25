# -*- coding: utf-8 -*-
"""
similaridade.py — token_sort_ratio com DOIS motores bit-idênticos
==================================================================
A skill-mãe usa `rapidfuzz.fuzz.token_sort_ratio`. Aqui ele é o caminho rápido
(C++), mas a skill NÃO pode depender dele para rodar: o fallback puro-Python
reimplementa a MESMA métrica — similaridade InDel normalizada:

    ratio(a,b) = 100 · 2·LCS(a,b) / (|a| + |b|)          ratio("","") = 100

Isso é exatamente o que o rapidfuzz calcula (distância InDel = |a|+|b|−2·LCS),
então os dois motores devolvem o mesmo número, não uma aproximação. O
`selftest.py` prova a paridade em massa (assert de igualdade a 1e-9) sempre que
o rapidfuzz estiver instalado.

Trade-off: fallback é O(|a|·|b|) em Python. Em nome de logradouro (≤60 chars)
custa microssegundos, mas em base grande instale o rapidfuzz — a diferença é de
uma ordem de grandeza no pareamento.
"""
from __future__ import annotations

try:
    from rapidfuzz import fuzz as _fz
    MOTOR = "rapidfuzz"
except Exception:                                    # pragma: no cover
    _fz = None
    MOTOR = "python"


def _lcs(a: str, b: str) -> int:
    """Comprimento da maior subsequência comum (linha rolante, O(min) memória)."""
    if not a or not b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def ratio_py(a: str, b: str) -> float:
    """Similaridade InDel normalizada em 0..100 (referência, puro Python)."""
    a, b = str(a), str(b)
    tot = len(a) + len(b)
    if tot == 0:
        return 100.0
    return 100.0 * (2.0 * _lcs(a, b)) / tot


def _sort_tokens(s: str) -> str:
    return " ".join(sorted(str(s).split()))


def token_sort_py(a: str, b: str) -> float:
    return ratio_py(_sort_tokens(a), _sort_tokens(b))


def token_sort(a: str, b: str) -> float:
    """0..1. Usa rapidfuzz quando disponível; senão o equivalente puro-Python."""
    if _fz is not None:
        return _fz.token_sort_ratio(a, b) / 100.0
    return token_sort_py(a, b) / 100.0
