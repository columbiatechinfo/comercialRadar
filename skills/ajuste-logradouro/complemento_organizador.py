# -*- coding: utf-8 -*-
"""
ORGANIZADOR DE COMPLEMENTO — limpa e padroniza o campo complemento do cadastro.

O complemento mistura DUAS coisas: localizadores físicos da sub-unidade (que importam ao
cadastro) e ruído (que não importa). Este módulo:
  1. EXTRAI os localizadores em COMPONENTES TIPADOS (quadra, lote, bloco, torre, casa, andar,
     apartamento, sala, loja, box, posição...), com abreviação expandida e valor anexado.
  2. MONTA a string canônica ORDENADA numa hierarquia fixa (grosso → fino).
  3. DESCARTA o ruído (refs institucionais, nomes de loteamento, landmarks, 'SN', texto livre)
     e devolve a AUDITORIA do que saiu.
Padrão de organização ancorado em (logradouro, número): essa dupla é a IDENTIDADE do imóvel;
o complemento organizado é o sub-localizador da unidade dentro dela.

Whitelist-only: só vira componente o que é reconhecido como localizador de sub-unidade; todo o
resto é descartado (auditado) — robusto ao ruído aberto. Valor numérico/letra sem rótulo vira
'identificador' (mantido, sinalizado), não descartado.

Taxonomia ancorada no vocabulário real do CNEFE (Parnaíba): QUADRA, CASA, APARTAMENTO, BLOCO,
CONJUNTO, ANDAR, FUNDOS, TERREO, SOBRADO, LOTE, LADO, FRENTE, SALA, LOJA, QUITINETE, ANEXO,
BOX, QUARTO, SOBRELOJA, GARAGEM, DEPOSITO.
"""
import re, unicodedata, json

# alias -> (rótulo canônico, slot na hierarquia)
LABELS = {
    # v3.3.5: modificadores retirados do campo NUMERO entram aqui sem contaminar o número.
    "MODIFICADOR": ("MODIFICADOR", 0),
    "IMOVEL": ("IMOVEL", 4),
    # Elementos observados no padrão CNEFE/IBGE além da taxonomia histórica A2L.
    "RUA INTERNA": ("RUA_INTERNA", 1),
    "RUA_INTERNA": ("RUA_INTERNA", 1),
    "ENTRADA": ("ENTRADA", 3),
    "PALAFITA": ("PALAFITA", 4),
    "SUBSOLO": ("SUBSOLO", 5),
    "COBERTURA": ("COBERTURA", 5),
    "PORTARIA": ("PORTARIA", 7),
    "SALAO": ("SALAO", 7),
    "COMODO": ("COMODO", 7),
    "QUADRA": ("QUADRA", 1), "QD": ("QUADRA", 1), "QDA": ("QUADRA", 1), "QUA": ("QUADRA", 1), "Q": ("QUADRA", 1),
    "LOTE": ("LOTE", 2), "LT": ("LOTE", 2), "LTE": ("LOTE", 2), "LOT": ("LOTE", 2), "L": ("LOTE", 2),
    "BLOCO": ("BLOCO", 3), "BL": ("BLOCO", 3), "BLC": ("BLOCO", 3), "BLO": ("BLOCO", 3),
    "TORRE": ("TORRE", 3), "TR": ("TORRE", 3),
    "CASA": ("CASA", 4), "CS": ("CASA", 4),
    "SOBRADO": ("SOBRADO", 4),
    "ANDAR": ("ANDAR", 5), "PAVIMENTO": ("ANDAR", 5), "PAV": ("ANDAR", 5),
    "TERREO": ("TERREO", 5),
    "APARTAMENTO": ("APARTAMENTO", 6), "APTO": ("APARTAMENTO", 6), "APT": ("APARTAMENTO", 6),
    "AP": ("APARTAMENTO", 6), "APO": ("APARTAMENTO", 6),
    "CONJUNTO": ("CONJUNTO", 6), "CONJ": ("CONJUNTO", 6), "CJ": ("CONJUNTO", 6),
    "QUITINETE": ("QUITINETE", 6), "KITNET": ("QUITINETE", 6), "QUIT": ("QUITINETE", 6),
    "SALA": ("SALA", 7), "SL": ("SALA", 7),
    "LOJA": ("LOJA", 7), "LJ": ("LOJA", 7),
    "SOBRELOJA": ("SOBRELOJA", 7),
    "BOX": ("BOX", 7),
    "GARAGEM": ("GARAGEM", 7), "VAGA": ("GARAGEM", 7),
    "DEPOSITO": ("DEPOSITO", 7), "DEP": ("DEPOSITO", 7),
    "QUARTO": ("QUARTO", 7), "QTO": ("QUARTO", 7),
    "ANEXO": ("ANEXO", 7),
    "FUNDOS": ("POSICAO", 8), "FUNDO": ("POSICAO", 8), "FDS": ("POSICAO", 8),
    "FRENTE": ("POSICAO", 8), "FRT": ("POSICAO", 8),
    "LADO": ("POSICAO", 8),
}
SEM_VALOR = {"TERREO", "SOBRADO", "POSICAO"}                 # presença importa; valor é opcional
ORDEM = ["MODIFICADOR", "IMOVEL", "QUADRA", "LOTE", "RUA_INTERNA", "ENTRADA", "BLOCO", "TORRE", "CASA", "PALAFITA", "SOBRADO", "ANDAR", "TERREO", "SUBSOLO", "COBERTURA",
         "APARTAMENTO", "CONJUNTO", "QUITINETE", "SALA", "SALAO", "LOJA", "SOBRELOJA", "BOX",
         "GARAGEM", "DEPOSITO", "QUARTO", "COMODO", "ANEXO", "PORTARIA", "POSICAO"]
ROTULO_POS = {"FUNDOS": "FUNDOS", "FUNDO": "FUNDOS", "FDS": "FUNDOS",
              "FRENTE": "FRENTE", "FRT": "FRENTE", "LADO": "LADO"}
RUIDO_INST = {"CEPISA", "SUCAM", "FNS", "CHESF", "FUNASA", "SUDENE", "ELETROBRAS",
              "EMBRATEL", "COMPESA", "EQUATORIAL"}           # refs institucionais (descartar)
RUIDO_REF = {"PROX", "PROXIMO", "PERTO", "DEFRONTE", "ATRAS", "ESQUINA", "PROXIMA"}
# [A2L v3.1] MARCADOR REFERENCIAL: 'PROX CASA 10' não é a casa 10, é PERTO dela.
# O componente continua sendo extraído (a informação existe), mas a extração inteira
# nasce REVISAR com risco CONTEXTO_REFERENCIAL — e o trecho consumido pelo próprio
# marcador ('EM FRENTE AO') não vira componente.
_REF_RE = re.compile(r"\b(EM\s+FRENTE|DE\s+FRENTE\s+PARA|AO\s+LADO|AO\s+FUNDO|"
                     r"PROXIM[OA]|PROX|PERTO|DEFRONTE|ATRAS|VIZINHO|EN?\s*FRENTE)\b")

# [A2L v3 — divergência da skill-mãe] o alias era casado por PREFIXO de palavra:
# APARECIDA->APARTAMENTO, CASAMENTO->CASA, BLOQUEIO->BLOCO, SALAO->SALA, LOTEAMENTO->LOTE,
# TRAS->TORRE. Sujeira sistemática, silenciosa, em base de milhões. A guarda `(?![A-Z])`
# exige que o alias termine ali (fim, espaço ou dígito: 'AP201' segue válido).
_ALTS = sorted(LABELS, key=len, reverse=True)
# rótulo não pode estar colado a uma letra à esquerda (evita casar dentro de palavra);
# valor: número+letra-final (5A) só se a letra não for seguida de outra (evita 5LT3→"5L");
# senão só número; senão letra isolada (BL A) não-seguida de letra (evita AMARELA)
_VAL = r"\d+[A-Z](?![A-Z])|[A-Z]\s*\d+|\d+|[A-Z](?![A-Z])"
_RE = re.compile(r"(?<![A-Z])(" + "|".join(re.escape(a) for a in _ALTS) +
                 r")(?![A-Z])(?:\s*(" + _VAL + r"))?")

# Valores textuais livres só são aceitos nos dois componentes sintéticos controlados
# pela migração do campo número. Isso permite IMOVEL B / MODIFICADOR FNS sem
# reabrir falsos positivos como CASA AMARELA.
_RE_NUM_DERIVADO = re.compile(r"(?<![A-Z])(IMOVEL|MODIFICADOR)\s+([A-Z0-9]{1,24})(?![A-Z0-9])")


def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").upper()
    s = re.sub(r"[\-/,;.+]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _relacoes(s):
    """Retorna relações referenciais normalizadas com spans no texto normalizado."""
    pats = [
        (r"\bEM\s+FRENTE(?:\s+(?:A|AO|AOS|DA|DO|DE))?\b", "EM_FRENTE_A"),
        (r"\bDE\s+FRENTE\s+PARA\b", "EM_FRENTE_A"),
        (r"\bAO\s+LADO(?:\s+(?:DE|DA|DO))?\b", "AO_LADO_DE"),
        (r"\bAO\s+FUNDO(?:\s+(?:DE|DA|DO))?\b", "AO_FUNDO_DE"),
        (r"\bATRAS(?:\s+(?:DE|DA|DO))?\b", "ATRAS_DE"),
        (r"\bDEFRONTE(?:\s+(?:A|AO|DA|DO|DE))?\b", "DEFRONTE_A"),
        (r"\bPROXIM[OA]?(?:\s+(?:A|AO|DA|DO|DE))?\b|\bPROX(?:\s+(?:A|AO|DA|DO|DE))?\b|\bPERTO(?:\s+(?:DE|DA|DO))?\b", "PROXIMO_A"),
        (r"\bNA\s+ESQUINA(?:\s+(?:DE|DA|DO))?\b|\bESQUINA(?:\s+(?:DE|DA|DO))?\b", "NA_ESQUINA_DE"),
        (r"\bANTES(?:\s+(?:DE|DA|DO))?\b", "ANTES_DE"),
        (r"\bDEPOIS(?:\s+(?:DE|DA|DO))?\b", "DEPOIS_DE"),
        (r"\bEM\s+CIMA(?:\s+(?:DE|DA|DO))?\b|\bACIMA(?:\s+(?:DE|DA|DO))?\b", "ACIMA_DE"),
        (r"\bEMBAIXO(?:\s+(?:DE|DA|DO))?\b|\bABAIXO(?:\s+(?:DE|DA|DO))?\b", "ABAIXO_DE"),
        (r"\bDENTRO(?:\s+(?:DE|DA|DO))?\b", "DENTRO_DE"),
        (r"\bA\s+DIREITA(?:\s+(?:DE|DA|DO))?\b|\bDIREITA(?:\s+(?:DE|DA|DO))?\b", "DIREITA_DE"),
        (r"\bA\s+ESQUERDA(?:\s+(?:DE|DA|DO))?\b|\bESQUERDA(?:\s+(?:DE|DA|DO))?\b", "ESQUERDA_DE"),
        (r"\bCRUZAMENTO\s+COM\b|\bESQUINA\s+COM\b", "NA_ESQUINA_DE"),
        (r"\bVIZINH[OA](?:\s+(?:DE|DA|DO))?\b", "AO_LADO_DE"),
    ]
    out=[]
    for pat, rel in pats:
        for m in re.finditer(pat, s):
            out.append({"start":m.start(), "end":m.end(), "relacao":rel, "texto":m.group(0)})
    ordered=sorted(out, key=lambda d:(d["start"], -(d["end"]-d["start"])))
    keep=[]
    for x in ordered:
        if any(x["relacao"]==y["relacao"] and x["start"] < y["end"] and y["start"] < x["end"] for y in keep):
            continue
        keep.append(x)
    return keep


def _dedupe_contidos(xs):
    xs=list(dict.fromkeys(x.strip() for x in xs if x and x.strip()))
    return [x for x in xs if not any(x != y and x in y for y in xs)]


def _acesso_matches(s):
    pats = [
        r"\bENTRADA\s+(?:PELA|PELO|PELAS|PELOS)\s+(?:LATERAL|FRENTE|FUNDOS|PORTAO\s*\w+|CORREDOR)\b",
        r"\bENTRADA\s+(?:LATERAL|FRENTE|FUNDOS)\b",
        r"\bACESSO\s+(?:PELA|PELO|PELAS|PELOS)\s+[^,;]{1,60}",
        r"\bENTRAR\s+(?:PELA|PELO|PELAS|PELOS)\s+[^,;]{1,60}",
        r"\bPORTAO\s+(?:DOS\s+FUNDOS|LATERAL|\d+[A-Z]?)\b",
        r"\bSEGUNDA\s+ENTRADA\b",
    ]
    out=[]
    for pat in pats:
        for m in re.finditer(pat,s): out.append({"start":m.start(),"end":m.end(),"texto":m.group(0).strip()})
    # remove matches fully contained in a longer access phrase
    keep=[]
    for x in out:
        if not any(x is not y and y["start"] <= x["start"] and y["end"] >= x["end"] and (y["end"]-y["start"])>(x["end"]-x["start"]) for y in out):
            keep.append(x)
    return sorted(keep,key=lambda d:d["start"])


def _descricao_matches(s):
    cores = r"AMAREL[OA]|AZUL|VERDE|BRANC[OA]|PRET[OA]|VERMELH[OA]|CINZA|BEGE|MARROM|LARANJA"
    pats = [
        rf"\b(?:CASA|PREDIO|EDIFICIO|PORTAO|MURO|FACHADA)\s+(?:{cores})\b",
        r"\b(?:CASA|PREDIO|EDIFICIO|IMOVEL)\s+COM\s+(?:GRADE|MURO|PORTAO|CERCA)\b",
        r"\bCASA\s+DE\s+ESQUINA\b",
    ]
    out=[]
    for pat in pats:
        for m in re.finditer(pat,s): out.append({"start":m.start(),"end":m.end(),"texto":m.group(0).strip()})
    return sorted(out,key=lambda d:d["start"])


def _extrair_semantica_textual(s):
    am=_acesso_matches(s); dm=_descricao_matches(s)
    acesso=_dedupe_contidos([x["texto"] for x in am])
    descricao=_dedupe_contidos([x["texto"] for x in dm])
    empreendimento=[]
    comp_alt = "|".join(sorted((re.escape(a) for a in LABELS), key=len, reverse=True))
    pat_emp = re.compile(
        rf"\b(CONDOMINIO|COND|EDIFICIO|RESIDENCIAL)\s+(.+?)(?=\s+(?:{comp_alt})(?:\s|\d|$)|\s+(?:PROX|PROXIMO|PERTO|EM\s+FRENTE|AO\s+LADO|AO\s+FUNDO|ATRAS|DEFRONTE|ENTRADA|ACESSO)\b|$)"
    )
    for m in pat_emp.finditer(s):
        nome=(m.group(1)+" "+m.group(2)).strip()
        if len(nome.split()) >= 2: empreendimento.append(nome)
    return acesso, descricao, list(dict.fromkeys(empreendimento)), am, dm


def _sem_numero_token(s):
    return bool(re.search(r"\b(?:S\s*/\s*N|SN|SEM\s+NUMERO)\b", str(s or "").upper()))



_ORDINAIS = {"PRIMEIRA":1,"PRIMEIRO":1,"SEGUNDA":2,"SEGUNDO":2,"TERCEIRA":3,"TERCEIRO":3,
             "QUARTA":4,"QUARTO":4,"QUINTA":5,"QUINTO":5}

def _relacao_sequencial(s):
    """Instruções do tipo 'SEGUNDA CASA DEPOIS DA IGREJA'."""
    pat = re.compile(r"\b(PRIMEIR[OA]|SEGUND[OA]|TERCEIR[OA]|QUART[OA]|QUINT[OA])\s+"
                     r"(CASA|PORTAO|ENTRADA|RUA|IMOVEL)\s+(DEPOIS|APOS|ANTES)\s+"
                     r"(?:DA|DO|DE)\s+(.+)$")
    m=pat.search(s)
    if not m: return None
    ordem=_ORDINAIS.get(m.group(1),0)
    rel="ANTES_N_ELEMENTOS" if m.group(3)=="ANTES" else "APOS_N_ELEMENTOS"
    return {"start":m.start(),"end":m.end(),"relacao":rel,"texto":m.group(0),
            "ordem":ordem,"elemento":m.group(2),"referencia":m.group(4).strip()}

def _tipo_referencia_v2(txt):
    t=(txt or "").upper().strip()
    if re.match(r"^NUMERO\s+\d+", t): return "NUMERO"
    if re.search(r"\b(?:RUA|AVENIDA|AV|R|TRAVESSA|TRV|ESTRADA|RODOVIA|BR\s*\d+|RS\s*\d+)\b", t): return "LOGRADOURO"
    if any(x in t for x in ("CRUZAMENTO","ESQUINA")): return "CRUZAMENTO"
    if any(x in t for x in ("PONTE","VIADUTO","TUNEL","PASSARELA","TREVO","ROTULA","RODOVIARIA")): return "INFRAESTRUTURA"
    if any(x in t for x in ("ESCOLA","COLEGIO","CRECHE","PREFEITURA","POSTO DE SAUDE","UBS","HOSPITAL","PRACA","IGREJA","CAPELA","PAROQUIA")): return "EQUIPAMENTO_PUBLICO"
    if any(x in t for x in ("MERCADO","SUPERMERCADO","FARMACIA","DROGARIA","POSTO","LOJA","BAR","RESTAURANTE","PADARIA")): return "ESTABELECIMENTO"
    if any(x in t for x in ("CONDOMINIO","RESIDENCIAL","EDIFICIO")): return "CONDOMINIO"
    if any(x in t for x in ("RIO","ARROIO","MORRO","LAGOA","PARQUE","CAMPO")): return "PONTO_GEOGRAFICO"
    if re.match(r"^(?:CASA|APARTAMENTO|APTO|BLOCO|LOTE)\b", t): return "IMOVEL"
    if any(x in t for x in ("PORTAO","MURO","FACHADA","CASA AMARELA","CASA AZUL","PREDIO")): return "CARACTERISTICA_VISUAL"
    return "OUTRO"

def _utilidade_operacional(natureza, referencias, acesso, descricao, conflitos, residuo):
    if conflitos: return "REVISAR"
    if referencias or acesso: return "ALTA"
    if descricao: return "MEDIA"
    if natureza in ("PARTE_DO_ENDERECO","MISTO"): return "MEDIA"
    if residuo: return "REVISAR"
    return "BAIXA"

def _segmentos_json(comps_endereco, referencias, acesso, descricao, empreendimento):
    seg=[]
    for canon in ORDEM:
        if canon in comps_endereco:
            seg.append({"tipo":"ENDERECO","subtipo":canon,"valor":comps_endereco[canon],"origem":"DIRECT"})
    for r in referencias:
        d={"tipo":"REFERENCIA","subtipo":r.get("tipo","OUTRO"),"texto":r.get("texto",""),
           "relacao":r.get("relacao",""),"original":r.get("original",r.get("texto",""))}
        for k in ("ordem","elemento"):
            if k in r: d[k]=r[k]
        seg.append(d)
    for x in acesso: seg.append({"tipo":"ACESSO","texto":x})
    for x in descricao: seg.append({"tipo":"DESCRICAO","texto":x})
    for x in empreendimento: seg.append({"tipo":"EMPREENDIMENTO","texto":x})
    return json.dumps(seg, ensure_ascii=False, separators=(",",":"))

def organizar(complemento, logradouro=None, numero=None):
    # Preserva a informação S/N antes de normalizar pontuação, para que APTO S/N
    # nunca seja interpretado como APARTAMENTO S + identificador N.
    tinha_sem_numero = _sem_numero_token(complemento)
    s = _norm(complemento)
    if tinha_sem_numero:
        s = re.sub(r"\bS\s+N\b|\bSN\b|\bSEM\s+NUMERO\b", "SEMNUMERO", s)

    rels = _relacoes(s)
    seq_rel = _relacao_sequencial(s)
    if seq_rel:
        # marcador sequencial mais específico substitui marcadores genéricos contidos
        rels = [r for r in rels if not (seq_rel["start"] <= r["start"] and r["end"] <= seq_rel["end"])] + [seq_rel]
        rels = sorted(rels, key=lambda d:(d["start"], -(d["end"]-d["start"])))
    refs = [(d["start"], d["end"]) for d in rels]
    acesso, descricao, empreendimento, acesso_matches, descricao_matches = _extrair_semantica_textual(s)
    semantic_mask = [(d["start"], d["end"]) for d in acesso_matches + descricao_matches]
    spans = []
    conectores_ref = {"", "A", "AO", "AOS", "AS", "O", "OS", "DO", "DA", "DE", "DOS", "DAS",
                      "NO", "NA", "NOS", "NAS", "EM"}

    candidatos = {}   # canon -> [{valor, origem, pos, end, alias}]
    last_component_end = -1

    # Componentes derivados do número aceitam valor textual multi-letra. Como os
    # rótulos IMOVEL/MODIFICADOR são explícitos, esta exceção é segura.
    special_spans=[]
    for sm in _RE_NUM_DERIVADO.finditer(s):
        canon=sm.group(1); val=sm.group(2)
        candidatos.setdefault(canon, []).append({"valor":val,"origem":"DIRECT",
                                                  "pos":sm.start(),"end":sm.end(),"alias":canon})
        special_spans.append((sm.start(),sm.end()))
        spans.append((sm.start(),sm.end()))
        last_component_end=max(last_component_end, sm.end())

    for m in _RE.finditer(s):
        if any(m.start() < b and a < m.end() for a,b in special_spans):
            continue
        if any(m.start() < fim and ini < m.end() for ini, fim in semantic_mask):
            spans.append((m.start(), m.end()))
            continue
        if any(m.start() < fim and ini < m.end() for ini, fim in refs):
            spans.append((m.start(), m.end()))
            continue
        alias, val = m.group(1), m.group(2)
        # Q/L só são aliases quando acompanhados de identificador; nunca palavras soltas.
        if alias in {"Q", "L"} and not val:
            continue
        canon, _slot = LABELS[alias]
        if canon == "POSICAO":
            val = ROTULO_POS.get(alias, "")
        v = val if val is not None else ""
        # Não promove o S de S/N como valor.
        if tinha_sem_numero and v == "S" and s[m.end():].lstrip().startswith("EMNUMERO"):
            v = ""

        origem = "DIRECT"
        prev_refs = [(a, b) for a, b in refs if b <= m.start() and b > last_component_end]
        if prev_refs:
            _a, b = max(prev_refs, key=lambda z: z[1])
            gap = s[b:m.start()].strip()
            toks_gap = gap.split() if gap else []
            if all(t in conectores_ref for t in toks_gap):
                origem = "REFERENTIAL"
        candidatos.setdefault(canon, []).append({"valor": v, "origem": origem,
                                                  "pos": m.start(), "end":m.end(), "alias": alias})
        spans.append((m.start(), m.end()))
        last_component_end = m.end()

    # Compatibilidade: componentes mantém o melhor candidato. Nova camada separa
    # componentes realmente cadastrais de componentes apenas referenciais.
    comps, comp_origem, comps_endereco = {}, {}, {}
    conflitos, candidatos_txt = {}, {}
    for canon, arr0 in candidatos.items():
        arr = sorted(arr0, key=lambda d: (0 if d["origem"] == "DIRECT" else 1,
                                          0 if d["valor"] else 1, d["pos"]))
        best = arr[0]
        vals_direct = []
        for d in arr:
            if d["origem"] == "DIRECT":
                val = d["valor"] or canon
                if val not in vals_direct:
                    vals_direct.append(val)
        candidatos_txt[canon] = [d["valor"] for d in arr]
        if len(vals_direct) > 1:
            conflitos[canon] = vals_direct
        comps[canon] = best["valor"]
        comp_origem[canon] = best["origem"]
        if best["origem"] == "DIRECT" and canon not in conflitos:
            comps_endereco[canon] = best["valor"]

    # Referências: primeiro componente REFERENTIAL após o marcador é a referência;
    # se não houver componente tipado, preserva o texto livre até a próxima fronteira.
    referencias=[]
    direct_positions=sorted(d["pos"] for arr in candidatos.values() for d in arr if d["origem"]=="DIRECT")
    for r in rels:
        if r.get("relacao") in {"APOS_N_ELEMENTOS","ANTES_N_ELEMENTOS"} and r.get("referencia"):
            referencias.append({"relacao":r["relacao"], "texto":r["referencia"], "start":r["start"], "end":r["end"],
                                "original":r.get("texto",""), "ordem":r.get("ordem"), "elemento":r.get("elemento")})
            continue
        refcands=[]
        for canon, arr in candidatos.items():
            for d in arr:
                if d["origem"]=="REFERENTIAL" and d["pos"] >= r["end"]:
                    refcands.append((d["pos"], d["end"], canon, d["valor"]))
        refcands.sort()
        if refcands:
            pos, endc, canon, val = refcands[0]
            texto=(canon + (" " + val if val else "")).strip()
            referencias.append({"relacao":r["relacao"], "texto":texto, "start":r["start"], "end":endc, "original":s[r["start"]:endc].strip()})
        else:
            boundaries=[p for p in direct_positions if p > r["end"]]
            boundaries += [a["start"] for a in acesso_matches if a["start"] > r["end"]]
            boundaries += [d["start"] for d in descricao_matches if d["start"] > r["end"]]
            nxt=min(boundaries or [len(s)])
            txt=s[r["end"]:nxt].strip()
            txt=re.sub(r"^(?:A|AO|AOS|AS|O|OS|DO|DA|DE|DOS|DAS|NO|NA|NOS|NAS)\s+", "", txt)
            if txt:
                if re.fullmatch(r"\d+[A-Z]?", txt):
                    txt = "NUMERO " + txt
                referencias.append({"relacao":r["relacao"], "texto":txt, "start":r["start"], "end":nxt, "original":s[r["start"]:nxt].strip()})

    def _tipo_referencia(txt):
        t = txt.upper()
        if re.match(r"^NUMERO\s+\d+", t): return "NUMERO"
        for tipo, termos in {
            "ESCOLA": ("ESCOLA","COLEGIO","CRECHE"),
            "IGREJA": ("IGREJA","CAPELA","PAROQUIA","TEMPLO"),
            "MERCADO": ("MERCADO","SUPERMERCADO","ARMAZEM"),
            "POSTO": ("POSTO","IPIRANGA","BR MANIA"),
            "PRACA": ("PRACA",),
            "SAUDE": ("HOSPITAL","UBS","POSTO DE SAUDE","CLINICA"),
            "FARMACIA": ("FARMACIA","DROGARIA"),
        }.items():
            if any(x in t for x in termos): return tipo
        return "OUTRO"
    for rr in referencias:
        rr["tipo"] = _tipo_referencia_v2(rr["texto"])

    # Descrição visual como 'CASA AMARELA' não transforma a palavra CASA, sem
    # identificador, em parte do endereço.
    if any(x.startswith("CASA ") for x in descricao) and comps_endereco.get("CASA", None) == "":
        comps_endereco.pop("CASA", None)

    # AUDITORIA legado: segmentos contíguos não casados.
    descartado, identificador = [], []
    prev = 0
    for a, b in sorted(spans):
        seg = s[prev:a].strip()
        if seg:
            (identificador if re.fullmatch(r"\d+[A-Z]?|[A-Z]\d+|[A-Z]", seg) else descartado).append(seg)
        prev = max(prev, b)
    seg = s[prev:].strip()
    if seg:
        (identificador if re.fullmatch(r"\d+[A-Z]?|[A-Z]\d+|[A-Z]", seg) else descartado).append(seg)

    def _montar(dic):
        partes=[]
        for canon in ORDEM:
            if canon in dic:
                v=dic[canon]
                partes.append(f"{canon} {v}".strip() if v else canon)
        return " · ".join(partes)
    organizado=_montar(comps)
    endereco_real=_montar(comps_endereco)

    risco=[]; tier="CONFIRMA"
    if rels or referencias:
        risco.append("CONTEXTO_REFERENCIAL")
    if tinha_sem_numero and candidatos:
        risco.append("SEM_NUMERO_COMPLEMENTO")
    if conflitos:
        risco.append("CONFLITO_COMPONENTE")
    if identificador and candidatos:
        risco.append("IDENTIFICADOR_AMBIGUO")
    if conflitos.get("POSICAO"):
        risco.append("CONFLITO_POSICAO")
    if any(v == "REFERENTIAL" for v in comp_origem.values()) or conflitos or (identificador and candidatos):
        tier="REVISAR"

    tem_endereco=bool(comps_endereco)
    tem_nao_end=bool(referencias or acesso or descricao or empreendimento)
    if tem_endereco and tem_nao_end:
        natureza="MISTO"
    elif tem_endereco:
        natureza="PARTE_DO_ENDERECO"
    elif tem_nao_end:
        natureza="NAO_ENDERECO"
    elif s:
        natureza="INDEFINIDO"
    else:
        natureza="NAO_ENDERECO"

    classes=[]
    if tem_endereco:
        classes.append("SUBUNIDADE" if any(k in comps_endereco for k in ("IMOVEL","ENTRADA","APARTAMENTO","SALA","SALAO","LOJA","CONJUNTO","QUITINETE","BOX","GARAGEM","QUARTO","COMODO","PORTARIA")) else "ENDERECO_REAL")
    if referencias:
        classes.append("PONTO_REFERENCIA")
    if acesso:
        classes.append("ACESSO")
    if descricao:
        classes.append("DESCRICAO_IMOVEL")
    if empreendimento:
        classes.append("EMPREENDIMENTO")
    classe = "MISTO" if len(set(classes)) > 1 else (classes[0] if classes else "NAO_CLASSIFICADO")

    # Confiança operacional, não probabilidade estatística calibrada.
    confianca = 0.98 if natureza == "PARTE_DO_ENDERECO" and not risco else 0.90
    if natureza == "NAO_ENDERECO": confianca = 0.88
    if natureza == "MISTO": confianca = 0.90
    if conflitos or "IDENTIFICADOR_AMBIGUO" in risco: confianca = 0.55
    if natureza == "INDEFINIDO": confianca = 0.40

    if conflitos or "IDENTIFICADOR_AMBIGUO" in risco:
        decisao_cadastral = "REVISAR"
    elif natureza == "PARTE_DO_ENDERECO":
        decisao_cadastral = "USAR_NO_ENDERECO"
    elif natureza == "NAO_ENDERECO":
        decisao_cadastral = "NAO_USAR_NO_ENDERECO"
    elif natureza == "MISTO":
        decisao_cadastral = "USAR_PARCIAL"
    else:
        decisao_cadastral = "REVISAR"

    # Remove do resíduo os textos semânticos explicitamente preservados. O campo legado
    # 'descartado' continua intacto por compatibilidade; 'residuo' é a visão nova.
    semantic_tokens = set()
    for x in referencias: semantic_tokens.update(x["texto"].split())
    for x in acesso + descricao + empreendimento: semantic_tokens.update(x.split())
    residuo=[]
    rel_marker_tokens=set()
    for r in rels: rel_marker_tokens.update(r["texto"].split())
    for d in descartado:
        toks=[t for t in d.split() if t not in semantic_tokens and t not in rel_marker_tokens and t != "SEMNUMERO"]
        # conectores isolados depois da remoção semântica não são resíduo útil
        toks=[t for t in toks if t not in {"A","AO","AOS","AS","O","OS","DO","DA","DE","DOS","DAS","NO","NA","NOS","NAS"}]
        if toks: residuo.append(" ".join(toks))

    referencia_original = " | ".join(x.get("original", x.get("texto","")) for x in referencias)
    utilidade_operacional = _utilidade_operacional(natureza, referencias, acesso, descricao, conflitos, residuo)
    segmentos_json = _segmentos_json(comps_endereco, referencias, acesso, descricao, empreendimento)
    referencia_status = "NAO_APLICAVEL_TEXTUAL" if referencias else ""

    return {
        "tier": tier,
        "risco": list(dict.fromkeys(risco)),
        "complemento_original": complemento,
        "componentes": comps,
        "componentes_endereco": comps_endereco,
        "componentes_origem": comp_origem,
        "componentes_candidatos": candidatos_txt,
        "conflitos": conflitos,
        "complemento_organizado": organizado,
        "complemento_endereco_real": endereco_real,
        "natureza_endereco": natureza,
        "classe": classe,
        "subclasses": list(dict.fromkeys(classes)),
        "confianca": confianca,
        "confianca_metodo": "HEURISTICA_V1",
        "decisao_cadastral": decisao_cadastral,
        "utilidade_operacional": utilidade_operacional,
        "complemento_endereco_limpo": endereco_real,
        "segmentos_json": segmentos_json,
        "referencias": referencias,
        "referencia_tipo": " | ".join(x.get("tipo", "OUTRO") for x in referencias),
        "referencia": " | ".join(x["texto"] for x in referencias),
        "referencia_original": referencia_original,
        "relacao_referencia": " | ".join(x["relacao"] for x in referencias),
        "referencia_status": referencia_status,
        "referencia_dist_m": "",
        "referencia_fontes": "",
        "acesso": " | ".join(acesso),
        "descricao": " | ".join(descricao),
        "empreendimento": " | ".join(empreendimento),
        "identificador_sem_rotulo": identificador,
        "descartado": descartado,
        "residuo": residuo,
        "chave": (logradouro, str(numero) if numero not in (None, "") else None),
    }


def agrupar_por_endereco(registros):
    """Padrão de organização: agrupa por (logradouro, número) = identidade do imóvel;
    o complemento organizado de cada registro é a unidade dentro dela. registros = lista de
    dicts com 'logradouro','numero','complemento'. Retorna {chave: [organizados...]}."""
    from collections import defaultdict
    g = defaultdict(list)
    for r in registros:
        o = organizar(r.get("complemento", ""), r.get("logradouro"), r.get("numero"))
        g[o["chave"]].append(o)
    return g


# ----------------------------------------------------------------------------
# DEMO + validação
# ----------------------------------------------------------------------------
def _demo():
    print("=== complemento sujo → organizado + auditoria ===")
    casos = [
        ("QD 13 CASA 2 COLINA DO ALVORADA I", "RUA GENESIO PIRES", 185),
        ("APTO 302 BL A PROX ESCOLA", "AVENIDA PINHEIRO MACHADO", 500),
        ("QD5LT3 FUNDOS", "RUA PAUL HARRIS", 27),
        ("CASA AMARELA SUCAM SN", "RUA SATURNINO DUTRA", 40),
        ("AP 101 TERREO CEPISA", "RUA TIA CELESTE", 12),
        ("LOJA 4 SOBRELOJA", "AVENIDA SAO SEBASTIAO", 900),
    ]
    for comp, logr, num in casos:
        o = organizar(comp, logr, num)
        print(f"\n  original  : '{comp}'   @ {logr}, {num}")
        print(f"  organizado: {o['complemento_organizado'] or '—'}")
        print(f"  colunas   : {o['componentes']}")
        if o["identificador_sem_rotulo"]:
            print(f"  id s/rótulo: {o['identificador_sem_rotulo']}")
        print(f"  descartado: {o['descartado'] or '—'}")

    # validação medida contra CNEFE (complemento estruturado = gabarito)
    print("\n=== padrão de organização: agrupar por (logradouro, número) = o imóvel; complementos = unidades ===")
    registros = [
        {"logradouro": "AVENIDA PINHEIRO MACHADO", "numero": 500, "complemento": "APTO 101 BL A"},
        {"logradouro": "AVENIDA PINHEIRO MACHADO", "numero": 500, "complemento": "AP 102 BLOCO A PROX ESCOLA"},
        {"logradouro": "AVENIDA PINHEIRO MACHADO", "numero": 500, "complemento": "APTO 201 BL B CEPISA"},
        {"logradouro": "RUA GENESIO PIRES", "numero": 185, "complemento": "QD 13 CASA 2 COLINA DO ALVORADA"},
        {"logradouro": "RUA GENESIO PIRES", "numero": 185, "complemento": "QUADRA 13 CS 3"},
    ]
    for chave, unidades in agrupar_por_endereco(registros).items():
        print(f"\n  ▸ {chave[0]}, {chave[1]}   ({len(unidades)} unidade(s))")
        for u in unidades:
            print(f"      {u['complemento_organizado'] or '—':28s}  (descartado: {u['descartado'] or '—'})")
    print("\n=== validação em complementos reais (Parnaíba) — reconstrói sujo + ruído, mede ===")
    import duckdb, random
    random.seed(7)
    con = duckdb.connect()
    df = con.execute("""
        SELECT NOM_COMP_ELEM1 l1, VAL_COMP_ELEM1 v1, NOM_COMP_ELEM2 l2, VAL_COMP_ELEM2 v2,
               NOM_COMP_ELEM3 l3, VAL_COMP_ELEM3 v3
        FROM read_csv_auto('/tmp/22_PI.csv', delim=';', header=true, sample_size=-1)
        WHERE CAST(COD_MUNICIPIO AS VARCHAR)='2207702' AND NOM_COMP_ELEM1 IS NOT NULL
        ORDER BY random() LIMIT 600
    """).fetchdf()
    ALIAS = {"QUADRA": ["QUADRA", "QD"], "CASA": ["CASA", "CS"], "APARTAMENTO": ["APTO", "AP", "APARTAMENTO"],
             "BLOCO": ["BLOCO", "BL"], "CONJUNTO": ["CONJUNTO", "CJ"], "ANDAR": ["ANDAR"],
             "LOTE": ["LOTE", "LT"], "SALA": ["SALA", "SL"], "LOJA": ["LOJA", "LJ"]}
    POS = {"FUNDOS": "POSICAO", "FRENTE": "POSICAO", "LADO": "POSICAO", "TERREO": "TERREO", "SOBRADO": "SOBRADO"}
    RUIDOS = ["PROX ESCOLA", "COLINA DO ALVORADA I", "CEPISA", "PORTAO AZUL", "SUCAM", "EM FRENTE AO POSTO"]
    cs = lambda x: "" if x is None or (isinstance(x, float) and x != x) else str(x).strip().upper()
    comp_tot = comp_ok = nome_tot = nome_ok = ruido_tot = ruido_ok = 0
    falhas = []
    for row in df.to_dict("records"):
        gt = {}                                            # canonico -> valor esperado
        partes = []
        for li, vi in [("l1", "v1"), ("l2", "v2"), ("l3", "v3")]:
            lab = cs(row[li]); val = cs(row[vi])
            if not lab:
                continue
            if lab in ALIAS:
                canon = LABELS[ALIAS[lab][0]][0]
                gt[canon] = val
                partes.append(f"{random.choice(ALIAS[lab])} {val}".strip())
            elif lab in POS:
                gt[POS[lab]] = "" if POS[lab] != "POSICAO" else lab
                partes.append(lab)
        if not partes:
            continue
        ruido = random.sample(RUIDOS, random.randint(0, 2))
        entrada = " ".join(random.sample(partes, len(partes)) + ruido)
        o = organizar(entrada)
        for canon, val in gt.items():
            eh_codigo = (val == "") or not re.search(r"[A-Z]{3,}", val)
            got = o["componentes"].get(canon, None)
            ok = (canon in o["componentes"]) and (val == "" or str(got) == str(val))
            if eh_codigo:
                comp_tot += 1; comp_ok += int(ok)
            else:
                nome_tot += 1; nome_ok += int(ok)
            if eh_codigo and not ok and len(falhas) < 8:
                falhas.append((entrada, canon, val, o["componentes"]))
        for rnoise in ruido:
            ruido_tot += 1
            # ruído ok = não virou componente (está no descartado ou simplesmente ausente)
            virou_comp = any(tok in " ".join(f"{k} {v}" for k, v in o["componentes"].items())
                             for tok in [rnoise.split()[0]] if tok in LABELS)
            ruido_ok += int(not virou_comp)
    print(f"  componentes c/ valor-código (quadra/lote/bloco/apto nº): {100*comp_ok/max(comp_tot,1):5.1f}%  (n={comp_tot})")
    print(f"  componentes c/ valor-NOME (ex.: CONJUNTO BETANIA):        {100*nome_ok/max(nome_tot,1):5.1f}%  (n={nome_tot})  ← decisão: nome≈localidade, fica como presença")
    print(f"  ruído corretamente fora:                                  {100*ruido_ok/max(ruido_tot,1):5.1f}%  (n={ruido_tot})")
    if falhas:
        print("  -- amostra de falhas de componente --")
        for ent, canon, val, got in falhas[:6]:
            print(f"     '{ent}'  esperado {canon}={val}  obteve {got}")


if __name__ == "__main__":
    _demo()
