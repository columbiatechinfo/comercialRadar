#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LÉXICO AUTO-INCREMENTAL DE LOGRADOURO — aprende do dado, nunca do palpite.
================================================================================

Metodologia adotada da `ferramenta-logradouro-padrao` (pares provados +
support + decay + quarentena) e reimplementada aqui para caber na doutrina
desta skill: **a origem do que é aprendido é sempre o IBGE**.

O que aprende
-------------
Equivalência de TOKEN (`CONS ≡ CONSELHEIRO`) extraída de pares que a PRÓPRIA
base prova serem o mesmo endereço: mesmo município, mesma localidade, **mesmo
número exato** e centróides a menos de `RAIO_PROVA_M`, cujos logradouros
diferem por **exatamente um token 1↔1**. Aí não é semelhança de string — é o
mesmo lugar escrito de dois jeitos, e o IBGE é quem disse isso.

O que NUNCA aprende sozinho
---------------------------
- **adição** de token (`SANTOS` vs `SANTOS DUMONT`) — 0↔1 é rua diferente;
- **truncamento** (`XAVIER` vs `XAVIER DA SILVA`) — 0↔2;
- substituição **implausível** (tokens sem prefixo, sem parentesco fonético e
  com edição > 1) — vai para `quarentena`, não para o léxico.

Por que isto não contradiz "não invente nada"
---------------------------------------------
Nada aqui é hipótese sobre o significado de um token. A afirmação é apenas
"estes dois textos nomeiam o mesmo endereço" — e quem afirma é a coincidência
de número e coordenada no arquivo do IBGE. O léxico não corrige o dado: ele
alimenta o canal de **marcação graduada** (`END_LOGR_EQUIV_*`). O identificador
nunca o enxerga, então nenhuma safra é renumerada por algo que a skill aprendeu.

Governança
----------
- `support` conta **imóveis DISTINTOS** (dedup por município+localidade+número):
  um condomínio com 300 unidades não vale 300 provas, vale uma.
- promoção a `ativo` só com `support >= MIN_SUPPORT`;
- **decay** por meia-vida: equivalência que não é reforçada perde score e é
  arquivada — léxico que só cresce vira dívida;
- **guarda de ciclo**: token que já aponta para outro canônico vai para
  quarentena, nunca sobrescreve;
- `blacklist` manual no JSON tem a palavra final;
- o SHA do léxico entra no `run_fingerprint`: duas execuções com léxicos
  diferentes NÃO são equivalentes, e o lacre sabe disso.
"""
from __future__ import annotations

import json
import re
import math
import os

VERSAO_LEXICO = '1.0.0'

RAIO_PROVA_M = 50.0        # mesma régua do remerge por proximidade (R7)
MIN_SUPPORT = 2            # imóveis distintos
MEIA_VIDA_DIAS = 180.0
PISO_ARQUIVAMENTO = 0.5    # score abaixo disto: arquivado
TETO_BLOCO = 200           # pares por bloco; acima disto o bloco é DECLARADO


def carregar(path) -> dict:
    if not path or not os.path.exists(path):
        return {'versao': VERSAO_LEXICO, 'equiv': {}, 'quarentena': {},
                'blacklist': []}
    try:
        with open(path, encoding='utf-8') as fh:
            lex = json.load(fh)
    except (OSError, ValueError):
        return {'versao': VERSAO_LEXICO, 'equiv': {}, 'quarentena': {},
                'blacklist': []}
    for k in ('equiv', 'quarentena'):
        lex.setdefault(k, {})
    lex.setdefault('blacklist', [])
    lex.setdefault('versao', VERSAO_LEXICO)
    return lex


def salvar(path, lex) -> None:
    if not path:
        return
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(lex, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _edicao(a: str, b: str, teto: int = 1) -> int:
    """Levenshtein com corte — só precisamos saber se é <= teto."""
    if abs(len(a) - len(b)) > teto:
        return teto + 1
    ant = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(ant[j] + 1, cur[j - 1] + 1,
                           ant[j - 1] + (ca != cb)))
        if min(cur) > teto:
            return teto + 1
        ant = cur
    return ant[-1]


_ENUMERADOR = re.compile(r'^(?:\d+|[A-Z]|[A-Z]{1,2}\d+|\d+[A-Z]|[IVXLCDM]+)$')


def enumerador(tok: str) -> bool:
    """Token cuja FUNÇÃO é enumerar: dígito, letra sozinha, letra+dígito, romano.

    Achado rodando em Porto Alegre: o léxico promoveu `2→3` (support 99),
    `B→C` (45), `R3→R4` (36), `01→02` (33) — ruas IRMÃS de loteamento,
    paralelas, mesmo número de casa, a 30 m uma da outra. Edição ≤ 1 é verdade
    para qualquer par de ordinais consecutivos, então o gate de plausibilidade
    aceitava todas.

    A regra que fecha isso não é lista negra: um enumerador existe justamente
    para DISTINGUIR irmãos. Ele nunca é abreviação do vizinho.
    """
    return bool(_ENUMERADOR.match(str(tok or '').strip().upper()))


def plausivel(a: str, b: str, fonetica=None) -> bool:
    """Substituição plausível: prefixo, parentesco fonético ou edição ≤ 1.

    O gate existe para separar `CONS`→`CONSELHEIRO` (abreviação, prova forte)
    de `SANTOS`→`DUMONT` (duas ruas que por acaso compartilham número e
    esquina) e de `2`→`3` (irmãs de loteamento). Sem ele o léxico aprende o
    cruzamento de vias e a numeração da quadra.
    """
    a, b = str(a or '').strip(), str(b or '').strip()
    if not a or not b or a == b:
        return False
    if enumerador(a) or enumerador(b):
        return False
    curto, longo = (a, b) if len(a) <= len(b) else (b, a)
    if len(curto) >= 2 and longo.startswith(curto):
        return True
    if fonetica and fonetica(a) == fonetica(b):
        return True
    # edição ≤ 1 só entre tokens ALFABÉTICOS e longos: em token curto, uma
    # letra de diferença é o que separa dois nomes, não o que os aproxima.
    return (a.isalpha() and b.isalpha() and min(len(a), len(b)) >= 4
            and _edicao(a, b) <= 1)


def par_de_substituicao(toks_a, toks_b):
    """Devolve (tokA, tokB) quando os dois diferem por EXATAMENTE um token.

    Mesma quantidade de tokens e uma única posição divergente. Adição e
    truncamento devolvem None de propósito: são as duas formas conhecidas de
    fundir ruas distintas.
    """
    if len(toks_a) != len(toks_b):
        return None
    dif = [(x, y) for x, y in zip(toks_a, toks_b) if x != y]
    return dif[0] if len(dif) == 1 else None


def _decair(lex, hoje_ordinal: int) -> None:
    for tok, reg in lex.get('equiv', {}).items():
        visto = reg.get('visto_em_ordinal')
        if not visto:
            continue
        dias = max(0, hoje_ordinal - int(visto))
        reg['score'] = round(float(reg.get('score', 0.0))
                             * math.pow(0.5, dias / MEIA_VIDA_DIAS), 4)
        if reg.get('status') == 'ativo' and reg['score'] < PISO_ARQUIVAMENTO:
            reg['status'] = 'arquivado'


def aprender(pares, lex, hoje_ordinal: int) -> dict:
    """`pares` = iterável de (tok_a, tok_b, chave_imovel, fonetica).

    Idempotente por chave de imóvel: reprocessar a mesma base não infla
    support. Sem isso, rodar duas vezes promoveria qualquer candidato.
    """
    _decair(lex, hoje_ordinal)
    equiv, quar = lex.setdefault('equiv', {}), lex.setdefault('quarentena', {})
    black = set(lex.get('blacklist', []))
    for tok_a, tok_b, chave, fon in pares:
        curto, longo = sorted((tok_a, tok_b), key=len)
        if curto in black or longo in black:
            continue
        if not plausivel(curto, longo, fon):
            q = quar.setdefault(curto, {'canonico': longo, 'motivo':
                                        'substituicao implausivel', 'provas': []})
            if chave not in q['provas']:
                q['provas'].append(chave)
            continue
        reg = equiv.get(curto)
        if reg and reg.get('canonico') != longo:
            # GUARDA DE CICLO: um token não pode ter dois canônicos. O novo vai
            # para quarentena; sobrescrever apagaria a equivalência anterior sem
            # que ninguém soubesse.
            q = quar.setdefault(curto + '->' + longo,
                                {'canonico': longo, 'motivo':
                                 f'conflito com {reg.get("canonico")}',
                                 'provas': []})
            if chave not in q['provas']:
                q['provas'].append(chave)
            continue
        if reg is None:
            reg = {'canonico': longo, 'provas': [], 'status': 'candidato',
                   'score': 0.0}
            equiv[curto] = reg
        if chave in reg['provas']:
            continue
        reg['provas'].append(chave)
        reg['support'] = len(reg['provas'])
        reg['score'] = round(float(reg.get('score', 0.0)) + 1.0, 4)
        reg['visto_em_ordinal'] = int(hoje_ordinal)
        reg['status'] = ('ativo' if reg['support'] >= MIN_SUPPORT
                         else 'candidato')
    return lex


def ativos(lex) -> dict:
    """Só `ativo` é aplicado. Candidato e quarentena ficam visíveis e inertes."""
    black = set(lex.get('blacklist', []))
    return {t: r['canonico'] for t, r in lex.get('equiv', {}).items()
            if r.get('status') == 'ativo' and t not in black}


def aplicar(texto: str, mapa: dict) -> str:
    """Substitui token a token. NUNCA usado na chave canônica — só na marcação."""
    if not mapa:
        return texto
    return ' '.join(mapa.get(t, t) for t in str(texto or '').split())


def resumo(lex) -> dict:
    eq = lex.get('equiv', {})
    return {'versao': lex.get('versao', VERSAO_LEXICO),
            'ativos': sum(1 for r in eq.values() if r.get('status') == 'ativo'),
            'candidatos': sum(1 for r in eq.values()
                              if r.get('status') == 'candidato'),
            'arquivados': sum(1 for r in eq.values()
                              if r.get('status') == 'arquivado'),
            'quarentena': len(lex.get('quarentena', {})),
            'blacklist': len(lex.get('blacklist', []))}
