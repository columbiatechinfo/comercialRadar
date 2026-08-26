# -*- coding: utf-8 -*-
"""
normalizacao_hardening.py — endurecimento da normalização de endereço PT-BR.

Resolve patologias reais de cadastro que a normalização básica não trata:
  - número por extenso ........ "CEM"->100, "MIL E QUINHENTOS"->1500
  - algarismo romano .......... "XV"->15
  - numeral no nome da via ..... "QUINZE/XV DE NOVEMBRO"->"15 DE NOVEMBRO"
  - abreviação de título ....... DR->DOUTOR, MAL->MARECHAL, N SRA->NOSSA SENHORA
  - fonética PT-BR ............. SOUSA~SOUZA, XAVIER~CHAVIER, ASSIS~ASIS
  - texto sujo ................. parênteses, bairro grudado, sufixo FUNDOS, tipo repetido

Tudo determinístico (regras fixas). A chave fonética é usada como REFORÇO da
similaridade (max com a textual), nunca isolada — o número exato segue como âncora.
"""
import re
import unicodedata

# --------------------------------------------------------------------------
def _ascii_upper(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper().strip()


# ==========================================================================
# 1. NÚMERO POR EXTENSO  (pt-BR, até dezenas de milhar — suficiente p/ endereço)
# ==========================================================================
_NUM = {
    "ZERO": 0, "UM": 1, "UMA": 1, "DOIS": 2, "DUAS": 2, "TRES": 3, "QUATRO": 4,
    "CINCO": 5, "SEIS": 6, "MEIA": 6, "SETE": 7, "OITO": 8, "NOVE": 9, "DEZ": 10,
    "ONZE": 11, "DOZE": 12, "TREZE": 13, "QUATORZE": 14, "CATORZE": 14, "QUINZE": 15,
    "DEZESSEIS": 16, "DEZASSEIS": 16, "DEZESSETE": 17, "DEZOITO": 18, "DEZENOVE": 19,
    "VINTE": 20, "TRINTA": 30, "QUARENTA": 40, "CINQUENTA": 50, "CINCOENTA": 50,
    "SESSENTA": 60, "SETENTA": 70, "OITENTA": 80, "NOVENTA": 90, "CEM": 100,
    "CENTO": 100, "DUZENTOS": 200, "DUZENTAS": 200, "TREZENTOS": 300, "TREZENTAS": 300,
    "QUATROCENTOS": 400, "QUATROCENTAS": 400, "QUINHENTOS": 500, "QUINHENTAS": 500,
    "SEISCENTOS": 600, "SEISCENTAS": 600, "SETECENTOS": 700, "SETECENTAS": 700,
    "OITOCENTOS": 800, "OITOCENTAS": 800, "NOVECENTOS": 900, "NOVECENTAS": 900,
    "MIL": 1000,
}


def extenso_para_numero(texto):
    """Converte um numeral PT-BR escrito por extenso em int. None se não for numeral puro."""
    toks = [t for t in _ascii_upper(texto).split() if t and t != "E"]
    if not toks or any(t not in _NUM for t in toks):
        return None
    total = atual = 0
    for t in toks:
        v = _NUM[t]
        if v == 1000:
            atual = atual or 1
            total += atual * 1000
            atual = 0
        else:
            atual += v
    val = total + atual
    return val if val > 0 else None


# ==========================================================================
# 2. ALGARISMO ROMANO
# ==========================================================================
_ROMANO_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_RVAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def romano_para_int(tok):
    """Converte token romano (len>=2) em int; None se não for romano válido."""
    t = tok.upper()
    if len(t) < 2 or not _ROMANO_RE.match(t):
        return None
    total, prev = 0, 0
    for ch in reversed(t):
        v = _RVAL[ch]
        total += -v if v < prev else v
        prev = v
    return total


# ==========================================================================
# 3. NUMERAIS DENTRO DO NOME (extenso + romano -> dígito) p/ uniformizar a via
# ==========================================================================
def converter_numerais_no_texto(s):
    toks = s.split()
    out, i, n = [], 0, len(toks)
    while i < n:
        # janela de numeral por extenso a partir de i (aceita 'E' entre numerais)
        j, win = i, []
        while j < n and (toks[j] in _NUM or
                         (toks[j] == "E" and win and j + 1 < n and toks[j + 1] in _NUM)):
            win.append(toks[j]); j += 1
        if win and any(w in _NUM for w in win):
            val = extenso_para_numero(" ".join(win))
            if val is not None:
                out.append(str(val)); i = j; continue
        rom = romano_para_int(toks[i])
        if rom is not None:
            out.append(str(rom)); i += 1; continue
        out.append(toks[i]); i += 1
    return " ".join(out)


# ==========================================================================
# 4. EXPANSÃO DE TÍTULOS / PATENTES / RELIGIOSO  (token a token)
# ==========================================================================
_TITULOS = {
    "DR": "DOUTOR", "DRA": "DOUTORA", "PROF": "PROFESSOR", "PROFA": "PROFESSORA",
    "ENG": "ENGENHEIRO", "ARQ": "ARQUITETO", "DES": "DESEMBARGADOR", "DESEMB": "DESEMBARGADOR",
    "MAL": "MARECHAL", "MAR": "MARECHAL", "GEN": "GENERAL", "GAL": "GENERAL",
    "CEL": "CORONEL", "CAP": "CAPITAO", "TEN": "TENENTE", "SGT": "SARGENTO",
    "BRIG": "BRIGADEIRO", "ALM": "ALMIRANTE", "CMTE": "COMANDANTE", "CMT": "COMANDANTE",
    "PRES": "PRESIDENTE", "GOV": "GOVERNADOR", "PREF": "PREFEITO", "VER": "VEREADOR",
    "SEN": "SENADOR", "DEP": "DEPUTADO", "MIN": "MINISTRO", "VISC": "VISCONDE",
    "BAR": "BARAO", "MARQ": "MARQUES", "CONDE": "CONDE", "DOM": "DOM",
    "PE": "PADRE", "FR": "FREI", "STA": "SANTA", "STO": "SANTO", "S": "SAO", "SAO": "SAO",
    "NS": "NOSSA SENHORA", "NSA": "NOSSA SENHORA", "PCA": "PRACA",
}

# --------------------------------------------------------------------------
# "S" NAO E UM TITULO COMO OS OUTROS -- e a entrada mais ambigua desta tabela.
#
# MEDIDO em 26/08/2026 sobre a base ja normalizada: das 253 expansoes de
# `S` -> `SAO`, 222 estavam ERRADAS -- 88%. O que "S" era de verdade:
#
#     QUADR S UM, S DOIS, S CINCO   letra de QUADRA + numeral (Guajuviras,
#                                   Canoas). Nao existe santo chamado "Um".
#     BECO S NOME                   S = SEM. Virou "BECO SAO NOME", e o pior
#     ESTRADA S DENOMINACAO         nao e o santo inventado: e que se APAGA o
#                                   sinal de que a via NAO TEM NOME.
#     RUA S SALVADOR DALI           o pintor, em Rubem Berta (POA). CNEFE cru.
#
# Diferente de "DR" ou "PE", que so tem uma leitura, "S" e SAO, SEM, SETOR e
# letra de quadra ao mesmo tempo. Por isso ele deixou de expandir por padrao e
# passou a expandir CONTRA UMA LISTA.
#
# POR QUE ERRAR PARA O LADO DE NAO EXPANDIR: se as duas bases guardam "S
# FULANO", elas continuam casando entre si -- nao se perde nada. Expandir
# errado e que corrompe, porque cria uma forma canonica que nao existe e pode
# colidir duas vias diferentes.
_SANTOS = {
    "JOSE", "JOAO", "PEDRO", "PAULO", "ANTONIO", "FRANCISCO", "SEBASTIAO",
    "LUIS", "LUIZ", "MIGUEL", "JORGE", "MARCOS", "MATEUS", "LUCAS", "TOME",
    "TIAGO", "ANDRE", "FELIPE", "BARTOLOMEU", "SIMAO", "JUDAS", "MATIAS",
    "ESTEVAO", "BENTO", "BENEDITO", "CRISTOVAO", "DOMINGOS", "GABRIEL",
    "RAFAEL", "VICENTE", "LOURENCO", "LEOPOLDO", "GERALDO", "ROQUE", "BRAS",
    "BRAZ", "CAETANO", "JERONIMO", "AGOSTINHO", "AMBROSIO", "ANSELMO",
    "BERNARDO", "CAMILO", "CLEMENTE", "CONRADO", "DIMAS", "FIDELIS",
    "GONCALO", "GOTARDO", "HENRIQUE", "HILARIO", "ISIDORO", "IVO", "JACO",
    "JULIAO", "LAZARO", "LEANDRO", "LEONARDO", "MANOEL", "MANUEL", "MARCELO",
    "MARTINHO", "NICOLAU", "NORBERTO", "PATRICIO", "RAIMUNDO", "ROMUALDO",
    "SATURNINO", "SEVERINO", "SILVESTRE", "TARCISIO", "TEODORO", "VALENTIM",
    "VITOR", "AFONSO", "ALBERTO", "ALEXANDRE", "BOAVENTURA", "CARLOS",
    "CIPRIANO", "EDUARDO", "ELIAS", "FABIANO", "GREGORIO", "INACIO",
    "JOAQUIM", "JUSTINO", "MARTIM", "MAURICIO", "PIO", "TADEU", "URBANO",
    "VALERIO", "VITAL",
}
# SALVADOR fica DE FORA de proposito: o unico caso real na base e "S SALVADOR
# DALI", o pintor. "Sao Salvador" existe, mas e raro demais para pagar o preco
# de rebatizar o Dali de santo.

# S seguido de um destes nunca e santo -- e a leitura "SEM" ou "letra de quadra".
_NUNCA_SANTO = {
    "UM", "DOIS", "TRES", "QUATRO", "CINCO", "SEIS", "SETE", "OITO", "NOVE",
    "DEZ", "ONZE", "DOZE", "TREZE", "QUATORZE", "CATORZE", "QUINZE",
    "NOME", "DENOMINACAO", "DENOMINACOES", "IDENTIFICACAO", "NUMERO",
    "INFORMACAO", "SAIDA", "DADOS",
}


def s_e_sao(proximo: str) -> bool:
    """Decide se o token `S` desta posicao e mesmo `SAO`. Ver o bloco acima."""
    if not proximo:
        return False
    p = proximo.upper()
    if p in _NUNCA_SANTO or p.isdigit():
        return False
    if len(p) == 1:                     # "S M DENOMINACAO" -- inicial solta
        return False
    return p in _SANTOS


def expandir_titulos(s):
    toks = s.split()
    out = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        # "N SRA"/"N S" -> NOSSA SENHORA
        if t == "N" and i + 1 < n and toks[i + 1] in ("SRA", "SR", "S", "SA"):
            out += ["NOSSA", "SENHORA"]; i += 2; continue
        if t == "S" and not s_e_sao(toks[i + 1] if i + 1 < n else ""):
            out.append(t)               # ambiguo demais: fica como esta
        elif t in _TITULOS:
            out += _TITULOS[t].split()
        else:
            out.append(t)
        i += 1
    return " ".join(out)


# ==========================================================================
# 5. LIMPEZA DE LIXO
# ==========================================================================
# lixo posicional FINAL — só tokens que praticamente nunca são nome de via.
# REMOVIDOS (são nome real, causavam falso merge no CNEFE): FUNDO, SOBRADO, ANTIGA,
# ANTIGO, VELHO, NOVO — ex.: "Lagoa do Velho" ≠ "Lagoa do Sobrado", "Poço Fundo" ≠ "Poço".
_LIXO_FIM = {"FUNDOS", "FRENTE", "TERREO", "PROX", "PROXIMO", "DEFRONTE", "LADO"}
_ARTIGOS_FIM = {"DO", "DA", "DE", "DOS", "DAS", "E", "D"}


def _remover_segmentos(s):
    """Remove parênteses, bairro após ' - ' e 'ESQ COM...' — exige pontuação intacta."""
    s = re.sub(r"\(.*?\)", " ", s)                 # remove (PROX ESCOLA)
    s = re.split(r"\s-+\s|\s-+$|^-+\s|,", s)[0]    # bairro após ' - ' ou ','
    s = re.split(r"\bESQ(?:UINA)?\b", s)[0]         # "ESQ COM ..."
    return s


def limpar_lixo(s):
    """Trailing junk + tipo de via repetido (roda já tokenizado/sem pontuação).
    Guardas anti-falsificação: não poda se o token anterior é artigo (DA/DO/DE → parte
    do nome: 'RUA DA FRENTE') nem se fosse reduzir a via a menos de 2 tokens."""
    s = re.sub(r"\s+", " ", s).strip()
    toks = s.split()
    while len(toks) >= 3 and toks[-1] in _LIXO_FIM and toks[-2] not in _ARTIGOS_FIM:
        toks.pop()
    if len(toks) >= 2 and toks[0] == toks[1]:      # "RUA RUA"
        toks = toks[1:]
    return " ".join(toks)


# ==========================================================================
# 6. CHAVE FONÉTICA PT-BR  (reforço de similaridade)
# ==========================================================================
def fonetica_token(tok):
    t = tok
    if not t.isalpha():
        return t                                    # números intactos
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c) or c == "\u0327")
    t = t.replace("\u0327", "")                     # remove cedilha combinante
    t = t.upper().replace("Ç", "S")
    t = (t.replace("CH", "X").replace("PH", "F").replace("SH", "X")
          .replace("LH", "L").replace("NH", "N"))
    t = re.sub(r"G(?=[EI])", "J", t)
    t = re.sub(r"C(?=[EI])", "S", t)
    t = t.replace("QU", "K").replace("Q", "K")
    t = t.replace("W", "V").replace("Y", "I").replace("Z", "S")
    t = t.replace("SS", "S").replace("XC", "S")
    t = t.replace("H", "")
    t = re.sub(r"(.)\1+", r"\1", t)                 # consoante dupla -> simples
    return t


def fonetica_frase(s):
    return " ".join(fonetica_token(t) for t in str(s).split())


# ==========================================================================
# ORQUESTRA: logradouro endurecido
# ==========================================================================
def norm_logradouro_hard(s, abrev_logr):
    try:
        import ftfy
        s = ftfy.fix_text(str(s))                   # conserta mojibake se a lib existir
    except Exception:
        pass
    s = _ascii_upper(s)
    s = _remover_segmentos(s)                       # parênteses/bairro/esquina (pontuação intacta)
    s = re.sub(r"[^\w\s]", " ", s)                  # pontuação -> espaço (BR-116 -> BR 116)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    toks = s.split(" ")
    if toks and toks[0] in abrev_logr:              # tipo de via
        toks[0] = abrev_logr[toks[0]]
    s = " ".join(toks)
    s = expandir_titulos(s)
    s = converter_numerais_no_texto(s)
    s = limpar_lixo(s)
    return re.sub(r"\s+", " ", s).strip()
