#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tratar_pois.py — TRATAMENTO para português claro PRESERVANDO TODAS AS COLUNAS.

Mantém intactas as colunas nativas (ov.*, fsq.*, osm.*) e ADICIONA/normaliza as
harmonizadas. Destaques desta versão:

  • PARSER DE ENDEREÇO ROBUSTO (BR): extrai LOGRADOURO, NÚMERO, QUADRA, LOTE, CEP, KM
    por heurística determinística; usa libpostal (postal) SE disponível para refinar
    logradouro/número; e NUNCA perde silenciosamente — o que não casa vai para
    `endereco_nao_parseado`, e `endereco_parse_metodo` registra o caminho usado
    (libpostal | heuristica | parcial | sem_parse | vazio).

  • DEDUP (v3.0.0): o motor default passou a ser `dedup_v3.dedup_evidencia` — token de
    contexto, Jaccard, telefone como evidencia e veto, trava de diametro, guarda de
    precisao. O motor da v2 (blocking espacial por raio + `token_set_ratio` do nucleo,
    unido por union-find sem limite de cadeia) continua acessivel em `--dedup legado`,
    para reproduzir execucoes antigas; ele apagava estabelecimento real em galeria e
    predio comercial (medido: 10,1% e 4,5% dos clusters com fusao). Modos 'exato'
    (nome normalizado identico + ~30 m) e 'none' preservados.

Uso:
  python tratar_pois.py --in raw.parquet --out-prefix pt --min-conf 0.0            # evidencia (default)
  python tratar_pois.py --in raw.parquet --out-prefix pt --dedup legado            # motor da v2
  python tratar_pois.py --in raw.parquet --out-prefix pt --dedup exato             # exato
  python tratar_pois.py --in raw.parquet --out-prefix pt --dedup none              # sem dedup
"""
import argparse, math, os, re, sys, unicodedata
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from categorias_pt import traduzir

# ordem preferida das colunas harmonizadas/tratadas (nativas vêm depois)
FRENTE = ["id_fonte", "fonte", "fontes", "nome", "sem_nome",
          "categoria_orig", "categoria_hier",
          "lat", "lon", "precisao_coord_m", "coord_empilhada",
          "bairro", "localidade_fonte", "cep",
          "endereco_completo", "endereco_raw",
          "telefone", "site", "email", "instagram", "marca",
          "confianca", "confianca_classe", "status", "data_atualizacao"]

# libpostal é OPCIONAL: usado só se a lib `postal` estiver instalada
try:
    from postal.parser import parse_address as _LP_PARSE
    _HAS_LIBPOSTAL = True
except Exception:
    _HAS_LIBPOSTAL = False


def has_libpostal():
    return _HAS_LIBPOSTAL


# ------------------------------------------------------------------ nome/tel --
def norm_nome(s):
    if not isinstance(s, str) or not s.strip():
        return None
    s = re.sub(r"\s+", " ", s).strip()
    if [c for c in s if c.isalpha()] and (s == s.upper() or s == s.lower()):
        peq = {"e", "de", "da", "do", "das", "dos", "a", "o", "em", "no", "na"}
        s = " ".join(w if (w.lower() in peq) else (w[:1].upper() + w[1:].lower())
                     for w in s.lower().split())
    return s


def _ascii(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()


def _key_nome(s):
    return re.sub(r"[^a-z0-9]+", "", _ascii(s).lower())


# tokens genéricos de tipo de negócio / razão social — ruído para casar nomes entre bases
_STOP_NOME = {
    "padaria", "panificadora", "confeitaria", "restaurante", "lanchonete", "lancheria",
    "bar", "boteco", "pizzaria", "churrascaria", "sorveteria", "cafe", "cafeteria",
    "mercado", "mercadinho", "mercearia", "supermercado", "hipermercado", "minimercado",
    "loja", "lojas", "comercio", "comercial", "magazine", "armazem", "deposito", "atacado",
    "varejo", "distribuidora", "representacoes", "importadora", "exportadora",
    "farmacia", "drogaria", "perfumaria", "otica", "optica",
    "posto", "auto", "automotivo", "oficina", "mecanica", "borracharia", "lavajato", "lavacar",
    "salao", "barbearia", "estetica", "academia", "studio", "espaco", "centro", "clinica",
    "consultorio", "laboratorio", "escritorio", "agencia", "imobiliaria", "construtora",
    "servicos", "servico", "solucoes", "tecnologia", "sistemas", "engenharia", "consultoria",
    "industria", "industrial", "fabrica", "metalurgica", "grafica", "papelaria",
    "hotel", "pousada", "hostel", "motel", "churrasco", "lacheria",
    "ltda", "me", "epp", "eireli", "sa", "s/a", "cia", "and", "the", "do", "da", "de", "dos",
    "das", "e", "em", "no", "na", "o", "a", "&",
}


def _name_core(s):
    """Núcleo distintivo do nome p/ fuzzy: sem acento, minúsculo, sem tokens genéricos.

    DEFEITO CORRIGIDO (v3.0.0): NaN é truthy, então `str(v or "")` devolvia 'nan' e
    dois registros SEM NOME casavam com similaridade 100. Nulo agora é vazio."""
    if s is None or (isinstance(s, float) and s != s) or \
            (not isinstance(s, str)) or str(s).strip().lower() in ("", "nan", "none", "null"):
        return ""
    toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(s).lower()) if t]
    core = [t for t in toks if t not in _STOP_NOME and len(t) > 1]
    if not core:                                   # nome só com termos genéricos → usa tudo
        core = [t for t in toks if t]
    return " ".join(sorted(core))


def norm_tel(s):
    if not isinstance(s, str):
        return None
    d = re.sub(r"\D", "", s)
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return s.strip() or None


# ------------------------------------------------------------- endereço (BR) --
RE_QUADRA = re.compile(r"\b(?:quadra|quad\.?|qd\.?|qu\.?|q)\s*[:.\-]?\s*([0-9]{1,4}[a-zA-Z]?)\b", re.I)
RE_LOTE   = re.compile(r"\b(?:lote|lt\.?|l)\s*[:.\-]?\s*([0-9]{1,4}[a-zA-Z]?)\b", re.I)
RE_NUM    = re.compile(r"\b(?:n[º°o\.]?|num(?:ero|\.)?)\s*([0-9]{1,6})\b", re.I)
RE_CEP    = re.compile(r"\b(\d{5})[-.\s]?(\d{3})\b")
RE_KM     = re.compile(r"\bkm\s*[:.\-]?\s*([0-9]+(?:[.,][0-9]+)?)\b", re.I)
RE_SN     = re.compile(r"\b(?:s\s*/?\s*n[º°o.]?|sem\s+n[uú]mero)\b", re.I)
RE_SETOR  = re.compile(
    r"\b((?:setor|st|jardim|jd|vila|vl|residencial|res|parque|pq|cond(?:ominio)?|"
    r"bairro|distrito|loteamento|lot|conjunto|cj|conj|nucleo|chacara)\.?\s+"
    r"[0-9A-Za-zÀ-ÿ][\wÀ-ÿ .]*?)\s*$", re.I)

_TIPOS = [(r"^av\b\.?\s*", "Avenida "), (r"^r\b\.?\s*", "Rua "), (r"^al\b\.?\s*", "Alameda "),
          (r"^tv\b\.?\s*", "Travessa "), (r"^trav\b\.?\s*", "Travessa "), (r"^rod\b\.?\s*", "Rodovia "),
          (r"^estr\b\.?\s*", "Estrada "), (r"^pç\b\.?\s*", "Praça "), (r"^pc\b\.?\s*", "Praça "),
          (r"^lgo\b\.?\s*", "Largo "), (r"^p(?:ç|c)a\b\.?\s*", "Praça ")]

# tipo de via no INICIO do texto — usado para detectar logradouro que não é logradouro
RE_TIPO_VIA = re.compile(
    r"^\s*(rua|avenida|av|travessa|tv|trav|alameda|al|rodovia|rod|estrada|estr|praca|praça|"
    r"pc|pç|pca|pça|largo|lgo|via|linha|beco|servidao|servidão|passagem|viela|ladeira|"
    r"caminho|acesso|marginal|elevado|viaduto|br|rs|sc|pr|sp|mg|go|ba|pe|ce)\b\.?\s+",
    re.I)

# ruído de abreviação que não deve aparecer em `nao_parseado`
_RESID_DROP = {"av", "r", "al", "tv", "trav", "rod", "estr", "pc", "lgo", "st", "jd", "vl",
               "n", "no", "num", "nro", "esq", "qd", "lt", "q", "l"}


def _expand_tipo(lg):
    if not lg:
        return None
    for pat, full in _TIPOS:
        if re.match(pat, lg, re.I):
            lg = re.sub(pat, full, lg, flags=re.I, count=1)
            break
    lg = re.sub(r"\s+", " ", lg).strip(" -.,")
    return (lg[:1].upper() + lg[1:]) if lg else None


def _resid_limpa(txt):
    toks = [t for t in re.split(r"[\s,;.\-/]+", txt) if t]
    toks = [t for t in toks if not (len(t) <= 1 or t.lower() in _RESID_DROP)]
    return " ".join(toks) or None


def parse_endereco(s):
    """Parser determinístico BR com fallback. Retorna dict com campos + residual + método."""
    out = dict(logradouro=None, numero=None, quadra=None, lote=None, bairro=None, cep=None,
               km=None, nao_parseado=None, metodo="vazio")
    if not isinstance(s, str) or not s.strip():
        return out
    raw = re.sub(r"\s+", " ", s).strip()

    mcep = RE_CEP.search(raw); out["cep"] = f"{mcep.group(1)}-{mcep.group(2)}" if mcep else None
    mq = RE_QUADRA.search(raw); out["quadra"] = mq.group(1) if mq else None
    ml = RE_LOTE.search(raw);   out["lote"]   = ml.group(1) if ml else None
    mkm = RE_KM.search(raw);    out["km"]     = mkm.group(1) if mkm else None
    sn = bool(RE_SN.search(raw))

    # `work` sem cep/km/qd/lt → evita pegar dígito de CEP/KM como número
    work = raw
    for rgx in (RE_CEP, RE_KM, RE_QUADRA, RE_LOTE):
        work = rgx.sub(" ", work)
    numero = None
    mn = RE_NUM.search(work)
    if mn:
        numero = mn.group(1)
    if numero is None:
        mv = re.search(r",\s*(?:n[º°o\.]?\s*)?([0-9]{1,6})\b", work)
        if mv:
            numero = mv.group(1)
    if numero is None and sn:
        numero = "S/N"
    out["numero"] = numero

    # logradouro = 1º segmento, sem tokens já capturados; separa SETOR/BAIRRO colado
    seg0 = re.split(r"[,;]", work)[0]
    lg = seg0
    for rgx in (RE_SN, RE_NUM):
        lg = rgx.sub(" ", lg)
    if numero and numero != "S/N":
        lg = re.sub(rf"\b{re.escape(numero)}\b", " ", lg)
    lg = re.sub(r"\s+", " ", lg).strip(" -.,")
    ms = RE_SETOR.search(lg)
    if ms:
        out["bairro"] = re.sub(r"\s+", " ", ms.group(1)).strip(" .,")
        lg = lg[:ms.start()].strip(" -.,")
    out["logradouro"] = _expand_tipo(lg)

    # residual = raw menos tudo que foi mapeado (logradouro, número, setor/bairro)
    resid = raw
    for rgx in (RE_CEP, RE_KM, RE_QUADRA, RE_LOTE, RE_SN, RE_NUM):
        resid = rgx.sub(" ", resid)
    for piece in (out["logradouro"], out["bairro"], numero if numero != "S/N" else None):
        if piece:
            for w in re.findall(r"\w+", piece):
                resid = re.sub(rf"\b{re.escape(w)}\b", " ", resid, flags=re.I)
    resid = _resid_limpa(resid)

    got = [x for x in (out["logradouro"], numero, out["quadra"], out["lote"], out["cep"], out["bairro"]) if x]
    if not got:
        out["metodo"], out["nao_parseado"] = "sem_parse", raw
    elif resid:
        out["metodo"], out["nao_parseado"] = "parcial", resid
    else:
        out["metodo"], out["nao_parseado"] = "heuristica", None

    # O parser toma o 1º segmento antes da vírgula como logradouro. Quando o endereço
    # livre começa pelo NOME DO LUGAR ("Cvc RS - Canoas Shopping, Avenida Guilherme
    # Schell"), o logradouro entregue não existe e a rua real cai no resíduo. Se o
    # logradouro não tem tipo de via e o resíduo tem, os dois estão trocados.
    if (out["logradouro"] and out["nao_parseado"]
            and not RE_TIPO_VIA.match(out["logradouro"])
            and RE_TIPO_VIA.match(out["nao_parseado"])):
        out["logradouro"], out["nao_parseado"] = (_expand_tipo(out["nao_parseado"]),
                                                  out["logradouro"])
        out["metodo"] = "heuristica_troca"

    if _HAS_LIBPOSTAL and (out["logradouro"] is None or out["metodo"] in ("sem_parse", "parcial")):
        try:
            comps = {lab: val for val, lab in _LP_PARSE(raw)}
            if comps.get("road") and not out["logradouro"]:
                out["logradouro"] = _expand_tipo(comps["road"]); out["metodo"] = "libpostal"
            if comps.get("house_number") and not out["numero"]:
                out["numero"] = comps["house_number"]
            if comps.get("suburb") and not out["bairro"]:
                out["bairro"] = norm_nome(comps["suburb"])
            if comps.get("postcode") and not out["cep"]:
                pc = re.sub(r"\D", "", comps["postcode"])
                out["cep"] = f"{pc[:5]}-{pc[5:8]}" if len(pc) == 8 else out["cep"]
        except Exception:
            pass
    return out


def classe_conf(c):
    if c is None or (isinstance(c, float) and np.isnan(c)):
        return "sem"
    return "alta" if c >= 0.8 else ("média" if c >= 0.5 else "baixa")


def _isnull(v):
    if v is None:
        return True
    if isinstance(v, float) and np.isnan(v):
        return True
    return False


# ----------------------------------------------------------------- tratar -----
def tratar(df, min_conf, rejeitados=None):
    """Tratamento BARATO do passo 1. `rejeitados` (lista) recebe o que o
    `min_conf` descarta — descarte por regra de negócio deixa de ser apenas um
    número no funil.

    O que este tratamento NÃO faz mais, desde 01/09/2026: não parseia endereço
    e não traduz categoria. As duas coisas custavam o estado inteiro para
    servir a um município — 972.728 linhas normalizadas para se aproveitar
    27.527, 2,8%. `parse_endereco` e `traduzir` continuam neste módulo, e quem
    as chama agora é a etapa da área.

    Ficam: nome em Title Case, telefone, classe de confiança, CEP e o endereço
    COMO A FONTE ESCREVEU. Coordenada, CEP e endereço bastam como filtro
    inicial, que é para isso que o passo 1 serve.
    """
    out = []
    for row in df.to_dict("records"):
        conf = row.get("confianca")
        conf = None if _isnull(conf) else round(float(conf), 2)
        if conf is not None and conf < min_conf:
            if rejeitados is not None:
                rejeitados.append({"id_fonte": row.get("id_fonte"), "fonte": row.get("fonte"),
                                   "nome": row.get("nome"), "lat": row.get("lat"),
                                   "lon": row.get("lon"), "etapa": "normalize",
                                   "motivo": "confianca < min_conf (%.2f)" % min_conf,
                                   "valor": conf})
            continue
        cru = row.get("endereco_raw")
        cru = str(cru).strip() if (not _isnull(cru) and str(cru).strip()
                                   not in ("", "nan", "None")) else None
        # `bairro` é bairro; `localidade_fonte` é o que a fonte chamou de locality —
        # no Overture isso é o MUNICÍPIO em ~96% das linhas, não o bairro.
        bairro_n = norm_nome(row.get("bairro"))
        loc_fonte = norm_nome(row.get("localidade_fonte"))
        cv = row.get("cep")
        cep_src = str(cv).strip() if (not _isnull(cv) and str(cv).strip() not in ("", "nan", "None")) else None
        if not cep_src and cru:
            # O CEP fica: é o filtro de município errado, e uma regex sobre o
            # texto custa uma fração do parser inteiro.
            m = RE_CEP.search(cru)
            cep_src = "%s-%s" % (m.group(1), m.group(2)) if m else None
        nome_n = norm_nome(row.get("nome"))
        tratado = dict(nome=nome_n, sem_nome=not bool(nome_n),
                       bairro=bairro_n, localidade_fonte=loc_fonte, cep=cep_src,
                       endereco_completo=cru,
                       telefone=norm_tel(row.get("telefone")),
                       confianca=conf, confianca_classe=classe_conf(conf),
                       fontes=row.get("fonte"))
        out.append({**row, **tratado})          # mantém TODAS as colunas nativas
    res = pd.DataFrame(out)
    if len(res):
        res = _dv().sinais_precisao(res)
        # Sem fusão, a identidade da linha é o próprio registro da fonte:
        # `cluster_id = fonte:id_fonte`. É o que a etapa 2 usa como place_id.
        res = _cluster_id(res.reset_index(drop=True))
    front = [c for c in FRENTE if c in res.columns]
    resto = [c for c in res.columns if c not in front]
    return res[front + resto]


# ---------------------------------------------------------------- DEDUP -------
def _dv():
    """Import tardio: `dedup_v3` importa deste módulo (evita ciclo na carga)."""
    import dedup_v3
    return dedup_v3


def dedup_pois(res, modo="evidencia", raio_m=30, sim_min=85, sim_cross=92, **kw):
    """`evidencia`/`fuzzy` (default) = motor v3. `legado` = v2 (só nome + raio),
    mantido para reproduzir execuções antigas. `exato` e `none` inalterados."""
    return dedup_pois_auditado(res, modo, raio_m, sim_min, sim_cross, **kw)[0]


def dedup_pois_auditado(res, modo="evidencia", raio_m=30, sim_min=85, sim_cross=92, **kw):
    """Igual ao `dedup_pois`, devolvendo também a tabela de vínculos par a par."""
    vazio = pd.DataFrame(columns=["id_a", "id_b", "motivo", "dist_m", "score", "aceito"])
    p = dict(kw)
    p.setdefault("raio_nome_m", float(raio_m))
    p.setdefault("sim_min", float(sim_min))
    p.setdefault("sim_cross", float(sim_cross))
    if modo in (None, False, "none", "nenhum"):
        return _cluster_id(res.reset_index(drop=True)), vazio
    if modo == "exato":
        return _cluster_id(_dedup_sem_perda(res)), vazio
    if modo == "legado":
        return _cluster_id(_dedup_fuzzy(res, raio_m=p["raio_nome_m"], sim_min=p["sim_min"],
                                        sim_cross=p["sim_cross"])), vazio
    return _dv().dedup_evidencia(res, **p)


def _cluster_id(d):
    """Chave natural da linha sobrevivente. Nos modos sem motor de evidencia nao ha
    ancora eleita — o proprio registro entregue e a identidade."""
    if "cluster_id" not in d.columns and {"fonte", "id_fonte"} <= set(d.columns):
        d = d.copy()
        d["cluster_id"] = d["fonte"].astype(str) + ":" + d["id_fonte"].astype(str)
    return d


_PRIO = {"alta": 3, "média": 2, "baixa": 1, "sem": 1}


# campos de IDENTIDADE/GEO: vêm SEMPRE da âncora (representante), nunca coalescidos
ANCORA = ["id_fonte", "fonte", "nome", "lat", "lon"]


def _coalesce_por_grupo(res, grp):
    """1 linha por grupo, DETERMINÍSTICA e independente da ordem de entrada.
    Âncora (representante) = maior prioridade, desempate por id_fonte (asc).
    id_fonte/fonte/nome/lat/lon vêm da ÂNCORA (não coalescidos) → o ponto carrega a coordenada do
    próprio representante; as demais colunas são coalescidas (1º não-nulo, na mesma ordem) só p/
    preencher nulos entre as fontes; `fontes` = união."""
    res = res.copy()
    res["_g"] = grp
    res["_p"] = res["confianca_classe"].map(_PRIO).fillna(1).astype(int)
    res["_id"] = res["id_fonte"].astype(str)
    res = res.sort_values(["_p", "_id"], ascending=[False, True], kind="stable")
    g = res.groupby("_g", sort=False)
    fontes = g["fontes"].agg(
        lambda s: ",".join(sorted(set(",".join(s.dropna().astype(str)).split(","))))).to_dict()
    anchor = res.drop_duplicates("_g", keep="first").set_index("_g")
    cols = [c for c in res.columns if c not in ("_g", "_p", "_id")]
    ded = g[cols].first()                          # coalesce p/ preencher nulos
    for c in ANCORA:                               # identidade/geo = âncora (determinístico)
        if c in ded.columns:
            ded[c] = anchor[c]
    ded["fontes"] = ded.index.map(fontes)
    return ded.reset_index(drop=True)


def _dedup_sem_perda(res):
    """Modo EXATO: nome normalizado idêntico + célula de ~30 m."""
    grp = [f"{_key_nome(n)}|{round((la or 0)/0.0003)}|{round((lo or 0)/0.0003)}"
           for n, la, lo in zip(res["nome"], res["lat"], res["lon"])]
    return _coalesce_por_grupo(res, grp)


def _scorer():
    try:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio, True
    except Exception:
        from difflib import SequenceMatcher
        return (lambda a, b: 100.0 * SequenceMatcher(None, a, b).ratio()), False


def _dedup_fuzzy(res, raio_m=30, sim_min=85, sim_cross=92):
    """Blocking espacial (cKDTree em metros) + similaridade de nome + union-find.
    Pares a ≤ raio_m cujo nome (núcleo distintivo) tem similaridade ≥ sim_min são unidos.
    Se os segmentos divergem (ex.: Automotivo × Alimentação), exige sim ≥ sim_cross
    (mais conservador, evita unir negócios distintos que só compartilham um nome próprio).
    Pontos sem nome só se unem se muito próximos (≤ min(raio_m,12) m)."""
    from scipy.spatial import cKDTree
    n = len(res)
    lat = pd.to_numeric(res["lat"], errors="coerce").to_numpy()
    lon = pd.to_numeric(res["lon"], errors="coerce").to_numpy()
    ok = ~(np.isnan(lat) | np.isnan(lon))
    cores = [_name_core(x) for x in res["nome"].tolist()]
    segs = res["segmento"].tolist() if "segmento" in res.columns else [None] * n
    _GEN = {None, "Outros"}

    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    idx = np.where(ok)[0]
    if len(idx) > 1:
        lat0 = math.radians(float(np.nanmean(lat[idx])))
        R = 6378137.0
        x = np.radians(lon[idx]) * R * math.cos(lat0)
        y = np.radians(lat[idx]) * R
        tree = cKDTree(np.c_[x, y])
        pairs = tree.query_pairs(r=float(raio_m), output_type="ndarray")
        scorer, _fast = _scorer()
        tight = min(raio_m, 12.0)
        for a, b in pairs:
            i, j = int(idx[a]), int(idx[b])
            ca, cb = cores[i], cores[j]
            if ca and cb:
                si, sj = segs[i], segs[j]
                thr = sim_min if (si == sj or si in _GEN or sj in _GEN) else max(sim_min, sim_cross)
                if scorer(ca, cb) >= thr:
                    union(i, j)
            elif not ca and not cb:
                d = math.hypot(x[a] - x[b], y[a] - y[b])
                if d <= tight:
                    union(i, j)
    grp = [find(i) for i in range(n)]
    return _coalesce_por_grupo(res, grp)


# ------------------------------------------------------------------ descr -----
def _descr(col):
    base = {
        "id_fonte": "id na fonte", "fonte": "fonte de origem", "fontes": "fontes consolidadas",
        "nome": "nome tratado", "segmento": "segmento (15 classes A2L)", "categoria_pt": "categoria em PT",
        "categoria_orig": "categoria original da fonte",
        "categoria_hier": "hierarquia de categoria da fonte (raiz>...>folha)",
        "sem_nome": "registro sem nome na fonte (existe e foi posicionado, mas não nomeado)",
        "lat": "latitude WGS84", "lon": "longitude WGS84",
        "precisao_coord_m": "incerteza implícita da coordenada, pelas casas decimais (m)",
        "coord_empilhada": "quantos pontos dividem esta coordenada exata (>1 = geocodificação)",
        "logradouro": "logradouro parseado", "numero": "número (ou S/N)", "quadra": "quadra", "lote": "lote",
        "bairro": "bairro (OSM addr:suburb ou parse do endereço)",
        "localidade_fonte": "locality declarada pela fonte (no Overture é o município, não o bairro)",
        "flag_localidade_divergente": "localidade_fonte diverge do município da geometria",
        "n_registros_fundidos": "quantos registros a linha consolida",
        "dedup_motivos": "evidências que sustentaram a fusão (telefone|site|nome|...)",
        "nucleo_discriminante": "tokens do nome que distinguem na vizinhança (após remover contexto)",
        "cep": "CEP", "endereco_completo": "endereço remontado em PT",
        "endereco_parse_metodo": "método do parse (libpostal|heuristica|parcial|sem_parse|vazio)",
        "endereco_nao_parseado": "resíduo do endereço não mapeado (fallback, zero perda)",
        "endereco_raw": "endereço bruto da fonte", "telefone": "telefone (DD) NNNNN-NNNN",
        "site": "site", "email": "e-mail", "instagram": "instagram", "marca": "marca",
        "confianca": "confiança 0–1", "confianca_classe": "classe de confiança",
        "status": "status operação", "data_atualizacao": "data de atualização",
        "PLACA": "sigla de placa do município (IBGE)", "COD_MUNICIPIO": "código IBGE de 7 dígitos",
        "NOME_MUNICIPIO": "nome do município", "UF": "unidade da federação",
        "AREA_KM2": "área do município em km²", "TX_URBANIZACAO": "taxa de urbanização",
        "CLASSE_URB": "classe de urbanização do município"}
    if col in base:
        return base[col]
    if col.startswith("ov."):
        return "campo nativo Overture (achatado)"
    if col.startswith("fsq."):
        return "campo nativo Foursquare (achatado)"
    if col.startswith("osm."):
        return "campo nativo OpenStreetMap (achatado)"
    return "campo"


def _main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--min-conf", type=float, default=0.0)
    ap.add_argument("--dedup", choices=["fuzzy", "legado", "exato", "none"], default="fuzzy")
    ap.add_argument("--dedup-sim", type=float, default=85)
    ap.add_argument("--dedup-raio", type=float, default=30)
    ap.add_argument("--dedup-sim-cross", type=float, default=92)
    a = ap.parse_args()
    df = pd.read_parquet(a.inp) if a.inp.endswith(".parquet") else pd.read_csv(a.inp, low_memory=False)
    res = tratar(df, a.min_conf, dedup=a.dedup, raio_m=a.dedup_raio, sim_min=a.dedup_sim, sim_cross=a.dedup_sim_cross)
    res.to_csv(a.out_prefix + ".csv", index=False)
    print(f"{len(res):,} pontos -> {a.out_prefix}.csv | libpostal={_HAS_LIBPOSTAL} | dedup={a.dedup}")


if __name__ == "__main__":
    _main()
