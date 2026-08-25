# -*- coding: utf-8 -*-
"""
numero.py — número estritamente numérico; anotação migra para complemento
=======================================================================
Contrato v3.3.5:

* O CAMPO DE NÚMERO publicado pela skill contém apenas o número base (inteiro).
* Tudo que qualifica o número é ANOTAÇÃO/MODIFICADOR e deve migrar para o
  complemento, sem ser perdido.
* A anotação continua participando da CHAVE INTERNA DE PROVA para evitar
  colapsar 1A e 1B durante o aprendizado.

Exemplos:
    1B          -> numero=1   complemento_derivado="IMOVEL B"
    125-A       -> numero=125 complemento_derivado="IMOVEL A"
    100 FUNDOS  -> numero=100 complemento_derivado="FUNDOS"
    500 AP 201  -> numero=500 complemento_derivado="AP 201"
    7 KM        -> numero=7   complemento_derivado="MODIFICADOR KM"
    279/281     -> numero=279 complemento_derivado="MODIFICADOR 281"

A separação segue o princípio do CNEFE/IBGE: o atributo número comporta apenas
valores numéricos e a informação adicional fica fora dele. A A2L dá um passo
adicional orientado ao seu contrato cadastral: a anotação é integrada à camada
semântica de complemento.
"""
from __future__ import annotations
import re
import unicodedata

try:
    import normalizacao_hardening as H
except Exception:                                    # pragma: no cover
    H = None

_SN = {"", "S/N", "SN", "S N", "S.N", "S.N.", "SNUM", "SEM NUMERO", "SEM N",
       "0", "00", "000", "0000", "S/NUMERO", "SN.", "-", "."}

_RE_KM_PREFIX = re.compile(r"^KM\s*\.?\s*0*(\d+(?:[.,]\d+)?)$")
_RE_PREFIX = re.compile(r"^0*(\d+)(.*)$")
_RE_INT = re.compile(r"\d+")

# Rótulos que, quando aparecem após o número, já são complemento explícito e
# não devem ser embrulhados como IMOVEL/MODIFICADOR.
_COMP_PREFIX = re.compile(
    r"^(?:Q(?:D|DA|UADRA)?|L(?:T|TE|OTE)?|BLOCO|BL|TORRE|TR|CASA|CS|SOBRADO|"
    r"ANDAR|PAV(?:IMENTO)?|TERREO|SUBSOLO|COBERTURA|AP(?:TO|ARTAMENTO)?|APT|"
    r"CONJ(?:UNTO)?|SALA|SL|SALAO|LOJA|LJ|BOX|GARAGEM|VAGA|DEPOSITO|DEP|"
    r"QUARTO|QTO|COMODO|ANEXO|FUNDOS?|FRENTE|LADO|ENTRADA|PORTARIA|PALAFITA|"
    r"RUA\s+INTERNA)(?=\s|\d|$)"
)


def _au(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _limpar_cauda(tail: str) -> str:
    # O separador pertence à forma original, não ao conteúdo da anotação.
    tail = re.sub(r"^[\s\-/,;:.]+", "", tail or "").strip()
    return re.sub(r"\s+", " ", tail)


def _classificar_anotacao(tail_raw: str, *, veio_de_faixa: bool = False) -> tuple[str, str, int | None]:
    """Retorna (tipo_anotacao, complemento_derivado, secundario)."""
    tail = _limpar_cauda(tail_raw)
    if not tail:
        return "", "", None

    # 279/281 ou 279-281: primeiro número é o campo NUMERO; o segundo fica
    # preservado fora dele. Não o chamamos automaticamente de unidade B/etc.
    if veio_de_faixa or re.fullmatch(r"\d+", tail):
        sec = int(tail)
        return "NUMERO_SECUNDARIO", f"MODIFICADOR {sec}", sec

    if tail == "KM":
        return "QUILOMETRAGEM", "MODIFICADOR KM", None

    # Um sufixo curto é a forma clássica de distinguir unidades no mesmo número.
    # Se vier seguido de outro complemento, separa as duas semânticas:
    # 1B FUNDOS -> IMOVEL B FUNDOS.
    mu = re.match(r"^([A-Z]|[A-Z]\d{1,4}|\d{1,4}[A-Z])(?:\s+(.+))?$", tail)
    if mu:
        unidade, resto = mu.group(1), (mu.group(2) or "").strip()
        der = f"IMOVEL {unidade}" + (f" {resto}" if resto else "")
        return "DISTINCAO_MESMO_NUMERO", der, None

    # Se o texto já declara o tipo de complemento, preserva-o literalmente.
    if _COMP_PREFIX.match(tail):
        return "COMPLEMENTO_EXPLICITO", tail, None

    # Outros modificadores permanecem auditáveis sem inventar uma categoria
    # de unidade. O complemento_organizador conhece MODIFICADOR como componente.
    return "OUTRO_MODIFICADOR", f"MODIFICADOR {tail}", None


def parse(s) -> dict:
    raw = _au(s)
    out = {
        "original": s,
        "tipo": "SN",
        "base": None,
        # `modificador` é mantido por compatibilidade e para o gate interno.
        "modificador": "",
        "anotacao": "",
        "anotacao_tipo": "",
        "complemento_derivado": "",
        "secundario": None,
        "km": None,
        # v3.3.5: forma publicada do número nunca carrega anotação.
        "canonico": "",
        "chave": None,
    }
    if raw in _SN:
        out.update(anotacao="SN", anotacao_tipo="SEM_NUMERO")
        return out

    # Forma histórica "KM 7" também é reduzida ao número 7 + anotação KM.
    m = _RE_KM_PREFIX.match(raw)
    if m:
        numtxt = m.group(1).replace(",", ".")
        km = float(numtxt)
        # O campo numérico da A2L precisa ser inteiro. Para quilometragem decimal,
        # conserva o inteiro principal e mantém a medida completa na anotação.
        base = int(float(numtxt))
        if base <= 0:
            return out
        anot = "KM" if km == base else f"KM {numtxt}"
        out.update(tipo="KM", base=base, modificador=anot, anotacao=anot,
                   anotacao_tipo="QUILOMETRAGEM", complemento_derivado=f"MODIFICADOR {anot}",
                   km=km, canonico=str(base), chave=f"{base}|{anot}")
        return out

    m = _RE_PREFIX.match(raw)
    if m:
        base = int(m.group(1))
        if base <= 0:
            return out
        tail_original = m.group(2) or ""
        # Detecta faixa explicitamente pelo separador original.
        faixa = re.match(r"^\s*[/\-]\s*0*(\d+)\s*$", tail_original)
        if faixa:
            sec = int(faixa.group(1))
            anot = f"/{sec}"
            out.update(tipo="FAIXA", base=base, modificador=anot, anotacao=anot,
                       anotacao_tipo="NUMERO_SECUNDARIO",
                       complemento_derivado=f"MODIFICADOR {sec}", secundario=sec,
                       canonico=str(base), chave=f"{base}|/{sec}")
            return out

        tail = _limpar_cauda(tail_original)
        tipo_anot, comp_der, sec = _classificar_anotacao(tail)
        mod = tail
        km = None
        tipo = "NUMERO"
        if tail.startswith("KM"):
            tipo = "KM"
            mm = re.search(r"\d+(?:[.,]\d+)?", tail)
            km = float(mm.group().replace(",", ".")) if mm else float(base)
        out.update(tipo=tipo, base=base, modificador=mod, anotacao=mod,
                   anotacao_tipo=tipo_anot, complemento_derivado=comp_der,
                   secundario=sec, km=km, canonico=str(base),
                   chave=f"{base}|{mod}")
        return out

    # Último recurso: número por extenso. Continua estritamente numérico.
    if H is not None:
        v = H.extenso_para_numero(raw)
        if v:
            out.update(tipo="NUMERO", base=v, canonico=str(v), chave=f"{v}|")
    return out


def compativel(a: dict, b: dict, modo: str = "estrito") -> bool:
    """Os dois números podem ser o MESMO endereço? Base do gate de prova.

    A publicação usa apenas a base numérica; a prova interna continua levando a
    anotação em conta. Assim 1A e 1B não se fundem silenciosamente.
    """
    if a["chave"] is None or b["chave"] is None:      # S/N não ancora nada
        return False
    if a["chave"] == b["chave"]:
        return True
    if modo == "compativel" and a["tipo"] in {"NUMERO", "KM"} and b["tipo"] in {"NUMERO", "KM"}:
        return a["base"] == b["base"] and "" in (a["modificador"], b["modificador"])
    return False


def combinar_complemento(parsed: dict, complemento_original) -> str:
    """Injeta a anotação retirada do número na camada de complemento.

    Não altera o texto original da fonte; devolve apenas a visão de processamento.
    Evita duplicar o mesmo modificador quando a fonte já o repetiu no complemento.
    """
    der = _au(parsed.get("complemento_derivado", ""))
    comp = _au(complemento_original)
    anot = _au(parsed.get("anotacao", ""))
    if not der:
        return comp
    if not comp:
        return der
    if comp == der or (anot and comp == anot):
        return der
    # Se o complemento já contém explicitamente o componente derivado, não duplica.
    if der in comp:
        return comp
    return f"{der} {comp}".strip()
