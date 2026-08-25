# -*- coding: utf-8 -*-
"""
adversarial.py — bateria PERMANENTE de fidelidade semântica
============================================================
A revisão externa expôs uma lacuna de método, não só de código: medir COLISÃO
entre nomes normalizados não mede fidelidade. `AV BEIRA MAR` → `AVENIDA BEIRA
MARECHAL` não colide com nada e continua sendo destruição de informação.

Por isso a régua aqui é outra: para cada entrada existe UMA saída correta
esperada e um TIER máximo aceitável. Qualquer regressão de normalização quebra
o gate, mesmo que não gere colisão.

Formato: (entrada, esperado, tier_maximo, nota)
Cresça esta lista sempre que aparecer um caso real — é a memória de regressão
da skill.
"""

CASOS = [
    # --- P0 da revisão: expansão de título fora de posição ------------------
    ("AV BEIRA MAR",            "AVENIDA BEIRA MAR",          "CONFIRMA", "MAR é mar, não Marechal"),
    ("RUA DO MAR",              "RUA DO MAR",                 "CONFIRMA", "artigo antes do token"),
    ("RUA MAR AZUL",            "RUA MAR AZUL",               "CONFIRMA", "homógrafo bloqueado no slot"),
    ("RUA BAR GRANDE",          "RUA BAR GRANDE",             "CONFIRMA", "BAR nunca vira BARAO"),
    ("RUA JOAO DR",             "RUA JOAO DR",                "CONFIRMA", "título no fim é nome"),
    ("JOAO DR SILVA",           "JOAO DR SILVA",              "CONFIRMA", "sem tipo de via, slot é o 0"),
    ("DR XAVIER",               "DOUTOR XAVIER",              "ALTA",     "sem tipo, título no índice 0"),
    # --- P0 da revisão: numeral por extenso indiscriminado ------------------
    ("RUA MEIA PRAIA",          "RUA MEIA PRAIA",             "CONFIRMA", "MEIA não é 6 aqui"),
    ("RUA SETE",                "RUA SETE",                   "CONFIRMA", "fora do idioma de data"),
    ("RUA DOIS IRMAOS",         "RUA DOIS IRMAOS",            "CONFIRMA", "numeral é parte do nome"),
    # --- o que DEVE continuar funcionando ------------------------------------
    ("R Dr Xavier",             "RUA DOUTOR XAVIER",          "ALTA",     "título no slot"),
    ("RUA MAL OSORIO",          "RUA MARECHAL OSORIO",        "ALTA",     "forma inequívoca"),
    ("RUA PROF DR SILVA",       "RUA PROFESSOR DOUTOR SILVA", "ALTA",     "títulos encadeados"),
    ("RUA PE ANCHIETA",         "RUA PADRE ANCHIETA",         "ALTA",     "abreviação DNE"),
    ("RUA N SRA DE FATIMA",     "RUA NOSSA SENHORA DE FATIMA", "ALTA",    "composto"),
    ("AVENIDA QUINZE DE NOVEMBRO", "AVENIDA 15 DE NOVEMBRO",  "ALTA",     "idioma de data"),
    ("R XV DE NOVEMBRO",        "RUA 15 DE NOVEMBRO",         "ALTA",     "romano no idioma"),
    ("RUA 1º DE MAIO",          "RUA 1 DE MAIO",              "ALTA",     "ordinal no idioma"),
    ("AVENI SETE DE SETEMBRO",  "AVENIDA 7 DE SETEMBRO",      "ALTA",     "tipo truncado + idioma"),
    ("R. Cons Xavier",          "RUA CONS XAVIER",            "CONFIRMA", "sem léxico ainda"),
    # --- perda de texto tem que ser SINALIZADA -------------------------------
    ("AV BRASIL (PROX ESCOLA)", "AVENIDA BRASIL",             "REVISAR",  "segmento removido"),
    ("RUA JOAO FUNDOS",         "RUA JOAO",                   "REVISAR",  "poda de lixo posicional"),
    ("RUA DA FRENTE",           "RUA DA FRENTE",              "CONFIRMA", "guarda de artigo protege o nome"),
]
