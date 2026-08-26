# -*- coding: utf-8 -*-
"""
normalizacao_contextual.py — hardening COM guarda de posição e de homógrafo
============================================================================
Reimplementa a orquestra de `normalizacao_hardening.norm_logradouro_hard`
(que fica INTACTO, para diff limpo contra a skill-mãe) corrigindo o defeito que
a revisão flagrou: expansão token-a-token em qualquer posição produzia

    AV BEIRA MAR   -> AVENIDA BEIRA MARECHAL
    RUA DO MAR     -> RUA DO MARECHAL
    RUA MEIA PRAIA -> RUA 6 PRAIA

Três guardas, todas determinísticas:

1. HOMÓGRAFO BLOQUEADO — abreviação que também é palavra plena do português
   (`MAR`, `BAR`) NUNCA expande. Quem quer MARECHAL escreve `MAL`.
2. SLOT DE TÍTULO — título só expande logo depois do tipo de via (índice 1), ou
   no índice 2 quando o índice 1 também era título (`RUA PROF DR SILVA`), e só
   se houver nome depois dele. Título no fim da string não é título, é nome.
3. NUMERAL SÓ NO IDIOMA DE DATA — extenso e romano viram dígito apenas no
   padrão `<numeral> DE <mês>` (`QUINZE/XV DE NOVEMBRO` → `15 DE NOVEMBRO`).
   Fora dele o texto fica como está: `MEIA`, `SETE`, `XV` são palavras até prova
   em contrário.

Trade-off assumido: recall de numeral no nome cai (só o idioma é coberto), em
troca de zero corrupção semântica silenciosa. Numa skill que MARCA para revisão
humana, precisão vale mais que cobertura — o resíduo vira REVISAR, não erro.

Cada estágio devolve o TIER do que fez:
    CONFIRMA  fechado por construção (dicionário fechado + posição fixa)
    ALTA      guardado (título no slot, idioma de data, léxico com support)
    REVISAR   houve PERDA de texto (segmento entre parênteses, bairro, poda)
    HUMANO    sobrou pouco ou nada de via
"""
from __future__ import annotations
import re

import normalizacao_hardening as H

TIERS = ("CONFIRMA", "ALTA", "REVISAR", "HUMANO")
_ORD = {t: i for i, t in enumerate(TIERS)}

# 1. homógrafos: abreviação que colide com palavra plena -> nunca expande
BLOQUEADOS = {"MAR", "BAR"}
# título que só expande no slot (o resto do dicionário da mãe segue valendo)
_ARTIGOS = {"DO", "DA", "DE", "DOS", "DAS", "E", "D"}
_MESES = {"JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO",
          "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"}


def pior(a: str, b: str) -> str:
    return a if _ORD[a] >= _ORD[b] else b


# ---------------------------------------------------------------------------
def expandir_titulos_guardado(toks, tem_tipo: bool = True):
    """Expande título SÓ no slot do título. Devolve (tokens, mexeu).

    O slot é o índice 1 quando o token 0 é tipo de via reconhecido (`RUA DR X`);
    é o índice 0 quando não há tipo (`DR X`). Sem essa distinção, `JOAO DR SILVA`
    caía no slot 1 e virava `JOAO DOUTOR SILVA` — título no meio do nome.
    """
    out, mexeu = [], False
    i, n = 0, len(toks)
    slot_titulo = 1 if tem_tipo else 0                # índice elegível corrente
    while i < n:
        t = toks[i]
        no_slot = (i == slot_titulo)
        tem_nome_depois = i + 1 < n
        prev_artigo = i > 0 and toks[i - 1] in _ARTIGOS

        if (t == "N" and no_slot and i + 1 < n and toks[i + 1] in ("SRA", "SR", "S", "SA")
                and i + 2 < n):
            out += ["NOSSA", "SENHORA"]; mexeu = True; i += 2; slot_titulo = i; continue

        # "S" so expande contra a lista de santos -- ver H.s_e_sao
        if t == "S" and not H.s_e_sao(toks[i + 1] if i + 1 < n else ""):
            out.append(t); i += 1; continue

        if (t in H._TITULOS and t not in BLOQUEADOS and no_slot
                and tem_nome_depois and not prev_artigo):
            exp = H._TITULOS[t]
            if exp != t:
                mexeu = True
            out += exp.split()
            i += 1
            slot_titulo = i                           # permite 2º título encadeado
            continue

        out.append(t)
        i += 1
    return out, mexeu


def converter_numerais_idioma(toks: list[str]) -> tuple[list[str], bool]:
    """Numeral -> dígito SÓ no idioma de data, inclusive numeral composto.

    Exemplos: ``VINTE E QUATRO DE MAIO`` -> ``24 DE MAIO`` e
    ``TRINTA E UM DE MARCO`` -> ``31 DE MARCO``. Fora de ``... DE <mês>``
    nenhuma frase numérica é alterada.
    """
    out, mexeu = [], False
    i, n = 0, len(toks)
    while i < n:
        # Procura o próximo DE <MES> e tenta interpretar todos os tokens
        # desde i como um único numeral PT-BR. Limite curto evita transformar
        # nomes arbitrários e cobre folgadamente datas de 1..31.
        convertido = False
        for de_idx in range(i + 1, min(n - 1, i + 6)):
            if toks[de_idx] != "DE" or toks[de_idx + 1] not in _MESES:
                continue
            frase = " ".join(toks[i:de_idx])
            val = None
            if len(toks[i:de_idx]) == 1:
                t = toks[i]
                m = re.fullmatch(r"(\d+)[OA]?", t)
                if m:
                    val = int(m.group(1))
                elif t in H._NUM:
                    val = H.extenso_para_numero(t)
                else:
                    val = H.romano_para_int(t)
            else:
                val = H.extenso_para_numero(frase)
            if val and 1 <= int(val) <= 31:
                if frase != str(val):
                    mexeu = True
                out.append(str(int(val)))
                i = de_idx
                convertido = True
                break
        if convertido:
            continue
        out.append(toks[i]); i += 1
    return out, mexeu


# ---------------------------------------------------------------------------
def normalizar(s, abrev_logr: dict, reparar_mojibake: bool = False) -> dict:
    """Cascata determinística com proveniência e tier.

    Retorno: {texto, tier, origem[list], risco[list]}
    """
    origem, risco, tier = [], [], "CONFIRMA"
    if reparar_mojibake:
        import ftfy
        s = ftfy.fix_text(str(s))
    bruto = H._ascii_upper(s)

    cortado = H._remover_segmentos(bruto)
    if cortado.strip() != bruto.strip():
        origem.append("SEGMENTO"); risco.append("REMOCAO_SEGMENTO")
        tier = pior(tier, "REVISAR")

    txt = re.sub(r"[^\w\s]", " ", cortado)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return {"texto": "", "tier": "HUMANO", "origem": ["VAZIO"], "risco": ["SEM_TEXTO"]}

    toks = txt.split(" ")
    canon_tipos = set(abrev_logr.values())
    tem_tipo = toks[0] in abrev_logr or toks[0] in canon_tipos
    if toks[0] in abrev_logr and abrev_logr[toks[0]] != toks[0]:
        toks[0] = abrev_logr[toks[0]]
        origem.append("TIPO_VIA")

    # v3.3 — idempotência estrutural ANTES do slot de título.
    # "RUA RUA DR SILVA" precisa virar "RUA DOUTOR SILVA" numa única passada.
    if tem_tipo and toks:
        tipo0 = abrev_logr.get(toks[0], toks[0])
        toks[0] = tipo0
        removeu = False
        while len(toks) > 1:
            tipo1 = abrev_logr.get(toks[1], toks[1])
            if tipo1 != tipo0 or tipo1 not in canon_tipos:
                break
            toks.pop(1); removeu = True
        if removeu:
            origem.append("TIPO_DUPLICADO")
            tier = pior(tier, "ALTA")

    toks, mexeu = expandir_titulos_guardado(toks, tem_tipo=tem_tipo)
    if mexeu:
        origem.append("TITULO"); tier = pior(tier, "ALTA")

    # Idempotência forte: após expandir, títulos idênticos adjacentes são um
    # único qualificador nominal. Evita DR DR DR -> DOUTOR DOUTOR -> DOUTOR
    # em múltiplas passagens. Restrito ao vocabulário de títulos para não
    # colapsar palavras comuns legítimas do nome.
    canon_titulos = {x for v in H._TITULOS.values() for x in v.split()}
    if toks:
        compact = [toks[0]]
        removeu_titulo_dup = False
        for t in toks[1:]:
            if t == compact[-1] and t in canon_titulos:
                removeu_titulo_dup = True
                continue
            compact.append(t)
        toks = compact
        if removeu_titulo_dup:
            origem.append("TITULO_DUPLICADO"); tier = pior(tier, "ALTA")

    toks, mexeu = converter_numerais_idioma(toks)
    if mexeu:
        origem.append("NUMERAL_DATA"); tier = pior(tier, "ALTA")

    antes = " ".join(toks)
    depois = antes
    # Fechamento em ponto fixo: limpar_lixo remove uma duplicata inicial por
    # chamada. Iterar até estabilidade garante N(N(x)) == N(x) também para
    # entradas degeneradas como "JOAO JOAO JOAO ...". O limite é defensivo.
    for _ in range(16):
        novo = H.limpar_lixo(depois)
        if novo == depois:
            break
        depois = novo
    if depois != antes:
        origem.append("PODA"); risco.append("PODA_LIXO")
        tier = pior(tier, "REVISAR")

    texto = re.sub(r"\s+", " ", depois).strip()
    if not texto:
        return {"texto": "", "tier": "HUMANO", "origem": origem + ["VAZIO"],
                "risco": risco + ["SEM_TEXTO"]}
    return {"texto": texto, "tier": tier, "origem": origem, "risco": risco}
