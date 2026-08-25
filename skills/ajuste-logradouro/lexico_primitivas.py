# -*- coding: utf-8 -*-
"""
lexico_primitivas.py — SÓ as primitivas seguras do léxico
==========================================================
[A2L v3.1] `lexico_aprendido.py` foi REMOVIDO do pacote. Ele continha
`minerar()`/`atualizar()`/`mapa_ativo()` com a regra insegura

    # canonical = forma mais longa

que produzia `SILVA -> SILVAA`. O pipeline já não a chamava, mas deixar a
implementação no pacote é armadilha de manutenção: daqui a seis meses alguém
importa o módulo antigo e ressuscita o defeito. Sobrou aqui apenas o que é
neutro quanto à DIREÇÃO da equivalência:

    plausivel()              os dois tokens parecem equivalentes?
    substituicao_unitaria()  os logradouros diferem por exatamente 1↔1?
    aplicar()                troca token por canônico já decidido
    carregar() / lexico_vazio() / _dias_desde()

Quem decide QUAL forma é a correta é `lexico_seguro.py`, com evidência.
"""
import datetime
import json
import os
from collections import Counter

try:
    import normalizacao_hardening as H
    _fon = H.fonetica_token
except Exception:                                    # pragma: no cover
    def _fon(t):
        return t

MEIA_VIDA_DIAS = 180.0          # decay: score cai à metade a cada 180 dias sem reforço


def lexico_vazio():
    return {"versao": 1, "equiv": {}, "quarentena": {}, "blacklist": []}


def carregar(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return lexico_vazio()


def _lev_ratio(a, b):
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, lb + 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev = cur
    return 1 - dp[lb] / max(la, lb)


def _esqueleto(t):
    return "".join(c for c in t if c not in "AEIOU")


def plausivel(a, b):
    """Critério para aceitar a≡b como candidato (substituição plausível)."""
    if a == b or not a or not b:
        return False
    curto, longo = (a, b) if len(a) <= len(b) else (b, a)
    if len(curto) >= 3 and longo.startswith(curto):
        return True                                  # abreviação por prefixo
    if _fon(a) == _fon(b):
        return True                                  # mesma chave fonética
    if _lev_ratio(a, b) >= 0.80:
        return True                                  # erro de digitação
    if len(curto) >= 3 and _esqueleto(a) == _esqueleto(b):
        return True                                  # mesmo esqueleto consonantal
    return False


def substituicao_unitaria(la, lb):
    """Se logradouros diferem por exatamente 1 token de cada lado, retorna (a,b)."""
    ca, cb = Counter(la.split()), Counter(lb.split())
    ra = list((ca - cb).elements())
    rb = list((cb - ca).elements())
    if len(ra) == 1 and len(rb) == 1:
        return ra[0], rb[0]
    return None                                      # 0↔1 (adição) ou 0↔2 (trunc) -> None


def _dias_desde(d0, d1):
    try:
        a = datetime.date.fromisoformat(d0)
        b = datetime.date.fromisoformat(d1)
        return max(0, (b - a).days)
    except Exception:
        return 0


def aplicar(logr_norm, ativos):
    """Substitui tokens conhecidos pela forma canônica (ex.: CONS->CONSELHEIRO)."""
    if not ativos:
        return logr_norm
    return " ".join(ativos.get(t, t) for t in str(logr_norm).split())
