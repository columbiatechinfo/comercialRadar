# -*- coding: utf-8 -*-
"""
normalizacao_base.py — normalização determinística do LOGRADOURO (camada 1)
===========================================================================
Extraído VERBATIM de `ferramenta-logradouro-padrao/resolver_imovel.py`
(dicionários `_ABREV_LOGR` e `_SN`, `norm_logradouro`, `parse_numero`) para que
esta skill não carregue o motor de resolução de entidade.

Cascata de MARCAÇÃO (nunca sobrescreve a entrada — devolve sugestão + origem):

    entrada crua
      └─ 1 BASE       ascii/upper, pontuação → espaço, expande o TIPO no 1º token
      └─ 2 HARDENING  título/patente, numeral extenso/romano, lixo posicional  (opcional)
      └─ 3 VOCAB      truncamento de tipo de via (NUCLE→NÚCLEO), só no 1º token (opcional)
      └─ 4 LEXICO     equivalências ATIVAS mineradas (CONS→CONSELHEIRO)         (opcional)

Cada estágio é comparado com o anterior; a proveniência sai como lista de
origens que efetivamente alteraram o texto. Determinístico: mesmo léxico +
mesmo vocabulário ⇒ mesma saída.
"""
from __future__ import annotations
import json
import os
import re
import unicodedata

try:
    import normalizacao_hardening as H
    import normalizacao_contextual as X
    _HARD = True
except Exception:                                    # pragma: no cover
    _HARD = False

try:
    import lexico_seguro as L                        # aplicação CONTEXTUAL (v3.2)
    _LEX = True
except Exception:                                    # pragma: no cover
    _LEX = False


_ABREV_LOGR = {
    "R": "RUA", "RUA": "RUA", "AV": "AVENIDA", "AVN": "AVENIDA", "AVE": "AVENIDA",
    "AVENIDA": "AVENIDA", "TV": "TRAVESSA", "TRAV": "TRAVESSA", "TRV": "TRAVESSA",
    "TRAVESSA": "TRAVESSA", "AL": "ALAMEDA", "ALAMEDA": "ALAMEDA", "PC": "PRACA",
    "PCA": "PRACA", "PRACA": "PRACA", "PR": "PRACA", "ROD": "RODOVIA",
    "RODOVIA": "RODOVIA", "EST": "ESTRADA", "ESTR": "ESTRADA", "ESTRADA": "ESTRADA",
    "LGO": "LARGO", "LARGO": "LARGO", "BC": "BECO", "BECO": "BECO", "VL": "VILA",
    "VILA": "VILA", "JD": "JARDIM", "JARDIM": "JARDIM", "QD": "QUADRA",
    "LT": "LOTE", "CONJ": "CONJUNTO", "CONJUNTO": "CONJUNTO",
    # variantes reais observadas em bases de produção (só expandem no slot do tipo)
    "AVENI": "AVENIDA", "AVE": "AVENIDA", "AVEN": "AVENIDA",      # RS 5-char / PI 3-char
    "TRAVE": "TRAVESSA", "TRV": "TRAVESSA", "TRAV": "TRAVESSA",
    "ESTRA": "ESTRADA", "ESTR": "ESTRADA",
    "RODOV": "RODOVIA",
    "QUADR": "QUADRA",
    "CON": "CONJUNTO", "CONJ ": "CONJUNTO",                       # PI: conjunto
    "LOT": "LOTEAMENTO", "LOTE ": "LOTEAMENTO",
    "RES": "RESIDENCIAL", "RESID": "RESIDENCIAL",
    "PRC": "PRACA", "PCA ": "PRACA",
    "SERV": "SERVIDAO", "SERVD": "SERVIDAO",                      # SC: servidão
    # tipos RURAIS / de LOCALIDADE (padrão IBGE/CNEFE) — formas plenas + abreviações seguras
    "POVOADO": "POVOADO", "POV": "POVOADO", "PVD": "POVOADO",
    "LUGAREJO": "LUGAREJO", "COMUNIDADE": "COMUNIDADE", "COMUN": "COMUNIDADE",
    "ASSENTAMENTO": "ASSENTAMENTO", "ASSENT": "ASSENTAMENTO",
    "AGLOMERADO": "AGLOMERADO", "VILAREJO": "VILAREJO", "SEDE": "SEDE",
    "ENTRONCAMENTO": "ENTRONCAMENTO", "CAMINHO": "CAMINHO", "RUELA": "RUELA",
    "ENTRADA": "ENTRADA", "SAIDA": "SAIDA", "PROPRIEDADE": "PROPRIEDADE", "PROP": "PROPRIEDADE",
    "SERRA": "SERRA", "CHAPADA": "CHAPADA", "LAGOA": "LAGOA", "ALTO": "ALTO",
    "BAIXAO": "BAIXAO", "BAIXA": "BAIXA", "RESIDENCIAL": "RESIDENCIAL",
    "SETOR": "SETOR", "SET": "SETOR",
    # ribeirinhos/N e rurais nacionais
    "RIO": "RIO", "IGARAPE": "IGARAPE", "IGAR": "IGARAPE", "LAGO": "LAGO",
    "RAMAL": "RAMAL", "RML": "RAMAL", "ALDEIA": "ALDEIA", "AGROVILA": "AGROVILA",
    "PRAIA": "PRAIA", "RIACHO": "RIACHO", "BREJO": "BREJO",
    # NE (PE/RN) e urbanos RJ
    "ENGENHO": "ENGENHO", "QUILOMBO": "QUILOMBO", "TRAVESSIA": "TRAVESSIA",
    "ESCADA": "ESCADA", "ESCADAO": "ESCADAO", "BOULEVARD": "BOULEVARD", "BLVD": "BOULEVARD",
    "ILHA": "ILHA", "LINHA": "LINHA", "BARRA": "BARRA", "PROJETADA": "PROJETADA",
    "SUBIDA": "SUBIDA", "MARGEM": "MARGEM",
}
_SN = {"", "S/N", "SN", "S N", "S.N", "S.N.", "SNUM", "SEM NUMERO", "SEM N",
       "0", "00", "000", "0000", "S/NUMERO", "SN.", "-", "."}


def _ascii_upper(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper().strip()


def norm_logradouro(s, hard: bool = False, reparar_mojibake: bool = False) -> str:
    """Expande abreviação de tipo de via, remove acento/ruído, colapsa espaço.
    hard=True ativa a cascata CONTEXTUAL (título só no slot, numeral só no idioma
    de data) — nunca o token-a-token da skill-mãe."""
    if hard and _HARD:
        return X.normalizar(s, _ABREV_LOGR, reparar_mojibake=reparar_mojibake)["texto"]
    s = _ascii_upper(s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    toks = s.split(" ")
    if toks[0] in _ABREV_LOGR:
        toks[0] = _ABREV_LOGR[toks[0]]
    return " ".join(toks).strip()


def parse_numero(s, hard: bool = False):
    """Compatibilidade: devolve só a BASE inteira. O modelo completo (modificador,
    faixa, KM) está em `numero.parse` — é ele que alimenta o gate de prova."""
    import numero as NU
    return NU.parse(s)["base"]


# ---------------------------------------------------------------------------
# Vocabulário aprendido por LEITURA (truncamento de TIPO DE VIA, só 1º token)
# ---------------------------------------------------------------------------
def carregar_vocab_tipo(path) -> dict:
    """Lê vocabulario_aprendido.json -> {token_truncado: forma_plena} do slot tipo_via."""
    if not path or not os.path.exists(path):
        return {}
    v = json.load(open(path, encoding="utf-8"))
    return {tok: e["expansao"] for tok, e in v.get("tipo_via", {}).items()}


def aplicar_vocab_1tok(s, vmap: dict) -> str:
    """Expande SÓ o 1º token (slot do tipo de via). Nome/complemento nunca são tocados."""
    if not vmap:
        return s
    toks = str(s).split()
    if toks and toks[0] in vmap:
        toks[0] = vmap[toks[0]]
    return " ".join(toks)


# ---------------------------------------------------------------------------
# MARCAÇÃO com proveniência (a entrada NUNCA é sobrescrita)
# ---------------------------------------------------------------------------
def marcar(logradouro, hard: bool = True, vmap: dict | None = None,
           ativos: dict | None = None, reparar_mojibake: bool = False) -> dict:
    """Devolve a forma canônica SUGERIDA, de onde veio e QUANTO confiar nela.

    Retorno:
      logr_base      normalização mínima (tipo expandido, sem acento/pontuação)
      logr_marcado   forma sugerida ao fim da cascata
      tier           CONFIRMA | ALTA | REVISAR | HUMANO — pior estágio acionado
      origem         estágios que efetivamente alteraram o texto
      risco          motivos que rebaixaram o tier (perda de texto, poda...)
      alterado       True se a sugestão difere da entrada em caixa alta

    O tier é o que separa `R`→`RUA` (fechado por construção) de uma poda de
    token (lossy). Sem ele, o processo a jusante não tem como aplicar em massa
    o que é seguro e reter o resto.
    """
    cru = _ascii_upper(logradouro)
    base = norm_logradouro(logradouro, hard=False, reparar_mojibake=False)

    if hard and _HARD:
        r = X.normalizar(logradouro, _ABREV_LOGR, reparar_mojibake=reparar_mojibake)
        atual, tier = r["texto"], r["tier"]
        origem, risco = list(r["origem"]), list(r["risco"])
    else:
        atual, tier = base, "CONFIRMA"
        origem = ["TIPO_VIA"] if base != re.sub(r"\s+", " ", re.sub(
            r"[^\w\s]", " ", cru)).strip() else []
        risco = []

    if vmap:
        v = aplicar_vocab_1tok(atual, vmap)
        if v != atual:
            origem.append("VOCAB")
            tier = X.pior(tier, "ALTA") if _HARD else "ALTA"
        atual = v

    if ativos and _LEX:
        x = L.aplicar(atual, ativos)
        if x != atual:
            origem.append("LEXICO")
            tier = X.pior(tier, "ALTA") if _HARD else "ALTA"
        atual = x

    return {"logr_base": base, "logr_marcado": atual, "tier": tier,
            "origem": "|".join(origem), "risco": "|".join(risco),
            "alterado": atual != cru}
