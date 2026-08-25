# -*- coding: utf-8 -*-
"""
lexico_seguro.py — léxico contextual, territorial e auditável (A2L v3.3)
===============================================================================
Unidade de aplicação nominal:
    (scope_id, contexto, variante) -> canônico

O vocabulário estrutural global (R->RUA, AV->AVENIDA etc.) permanece fora daqui,
em vocabulario_aprendido.json. O léxico minerado NUNCA atravessa scope_id.

v3.3 adiciona:
- escopo territorial obrigatório no pipeline;
- decay por last_reinforced (replay não rejuvenesce regra);
- reativação de arquivado com nova evidência independente;
- autoridade hierárquica;
- INDEFINIDO simétrico e resolvível;
- proveniência por evidência, com teto finito;
- migração segura 3.2 -> 3.3: conhecimento sem escopo é preservado como legado,
  mas não é reaplicado silenciosamente.
"""
from __future__ import annotations
import datetime
import math
from collections import Counter

import lexico_primitivas as L

INDEFINIDO = "INDEFINIDO"
CONFLITO_AUTORIDADE = "CONFLITO_AUTORIDADE"
RATIO_DOMINANCIA = 5.0
MIN_FREQ_DOMINANCIA = 3
RAIO_INDEPENDENCIA_M = 100.0
MAX_ANCORAS = 100
MAX_SUPPORT = MAX_ANCORAS
MAX_EVIDENCIAS = MAX_ANCORAS
_R_TERRA = 6371008.8
DEFAULT_SCOPE = "__DEFAULT__"   # somente API/testes; pipeline v3.3 exige scope_id explícito
LEGACY_SCOPE = "__LEGADO_SEM_ESCOPO__"


# ---------------------------------------------------------------------------
# contexto
# ---------------------------------------------------------------------------
def contexto_de(tokens, i: int) -> str:
    return " ".join("{}" if j == i else t for j, t in enumerate(tokens))


def frequencias_contextuais(series_logr, pesos=None) -> Counter:
    c = Counter()
    if pesos is None:
        pesos = {}
    for s in series_logr:
        s = str(s)
        toks = s.split()
        w = pesos.get(s, 1)
        for i, t in enumerate(toks):
            c[(contexto_de(toks, i), t)] += w
    return c


def contexto_do_par(la: str, lb: str, a: str, b: str):
    ta, tb = la.split(), lb.split()
    if len(ta) != len(tb):
        return None
    dif = []
    for i, (x, y) in enumerate(zip(ta, tb)):
        if x != y:
            dif.append((i, x, y))
    if len(dif) != 1:
        return None
    i, x, y = dif[0]
    if {x, y} != {a, b}:
        return None
    return contexto_de(ta, i)


# ---------------------------------------------------------------------------
# distância geográfica
# ---------------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _R_TERRA * math.asin(min(1.0, math.sqrt(h)))


def independente(anc: dict, ancoras, raio: float = RAIO_INDEPENDENCIA_M) -> bool:
    for a0 in ancoras:
        if a0.get("num") != anc.get("num"):
            continue
        try:
            if haversine_m(float(anc["lat"]), float(anc["lon"]),
                           float(a0["lat"]), float(a0["lon"])) <= raio:
                return False
        except Exception:
            # âncora legada/malformada nunca prova duplicidade física
            continue
    return True


# ---------------------------------------------------------------------------
# direção do canônico / autoridade hierárquica
# ---------------------------------------------------------------------------
def _nivel_aut(valor_bool=False, nivel=None) -> int:
    if nivel is not None:
        try:
            return max(0, int(nivel))
        except Exception:
            return 0
    return 100 if bool(valor_bool) else 0


def decidir_canone(a, b, ctx: str, freq_ctx: Counter,
                   aut_a: bool = False, aut_b: bool = False,
                   nivel_a=None, nivel_b=None,
                   ratio: float = RATIO_DOMINANCIA,
                   min_freq: int = MIN_FREQ_DOMINANCIA):
    """Retorna (variante, canônico|None, base_decisao).

    Níveis diferentes: maior autoridade decide.
    Níveis iguais e >0 com divergência: CONFLITO_AUTORIDADE, nunca frequência.
    Sem autoridade: dominância contextual pode decidir.
    """
    na = _nivel_aut(aut_a, nivel_a)
    nb = _nivel_aut(aut_b, nivel_b)
    if na != nb:
        return (a, b, "AUTORIDADE_NIVEL") if nb > na else (b, a, "AUTORIDADE_NIVEL")
    if na > 0:  # duas autoridades do mesmo nível discordam
        return min(a, b), None, CONFLITO_AUTORIDADE
    fa, fb = freq_ctx.get((ctx, a), 0), freq_ctx.get((ctx, b), 0)
    if max(fa, fb) >= min_freq:
        if fb >= ratio * max(fa, 1):
            return a, b, "FREQUENCIA_CONTEXTO"
        if fa >= ratio * max(fb, 1):
            return b, a, "FREQUENCIA_CONTEXTO"
    return min(a, b), None, INDEFINIDO


# ---------------------------------------------------------------------------
# mineração + proveniência
# ---------------------------------------------------------------------------
def _info_row(v):
    if isinstance(v, dict):
        return {
            "logr": str(v.get("logr", "")), "num": v.get("num"),
            "lat": v.get("lat"), "lon": v.get("lon"),
            "autoridade_nivel": int(v.get("autoridade_nivel", 100 if v.get("autoridade") else 0) or 0),
            "source_id": str(v.get("source_id", "")),
            "record_id": str(v.get("record_id", "")),
        }
    vals = list(v)
    logr, num, lat, lon = vals[:4]
    aut = vals[4] if len(vals) > 4 else False
    nivel = vals[5] if len(vals) > 5 else (100 if aut else 0)
    source = vals[6] if len(vals) > 6 else ""
    record = vals[7] if len(vals) > 7 else ""
    return {"logr": str(logr), "num": num, "lat": lat, "lon": lon,
            "autoridade_nivel": int(nivel or 0), "source_id": str(source),
            "record_id": str(record)}


def minerar(candidatos, info, lex, freq_ctx: Counter, *, scope_id: str = DEFAULT_SCOPE,
            run_id: str = "", ratio: float = RATIO_DOMINANCIA,
            min_freq: int = MIN_FREQ_DOMINANCIA):
    achados = []
    for ga, gb, _d in candidatos:
        ia, ib = _info_row(info[ga]), _info_row(info[gb])
        la, lb = ia["logr"], ib["logr"]
        sub = L.substituicao_unitaria(la, lb)
        if sub is None:
            continue
        a, b = sub
        if not L.plausivel(a, b):
            continue
        ctx = contexto_do_par(la, lb, a, b)
        if ctx is None:
            continue
        var, canon, base = decidir_canone(
            a, b, ctx, freq_ctx,
            nivel_a=ia["autoridade_nivel"], nivel_b=ib["autoridade_nivel"],
            ratio=ratio, min_freq=min_freq)
        try:
            anc = {"num": int(ia.get("num") or 0), "lat": round(float(ia["lat"]), 6),
                   "lon": round(float(ia["lon"]), 6)}
        except Exception:
            continue
        achados.append({
            "scope_id": str(scope_id), "contexto": ctx,
            "token_a": a, "token_b": b,
            "variante": var, "canonico": canon, "base_decisao": base,
            "ancora": anc, "run_id": run_id,
            "evidencia": {
                "run_id": run_id,
                "source_a": ia["source_id"], "record_a": ia["record_id"],
                "source_b": ib["source_id"], "record_b": ib["record_id"],
                "autoridade_nivel_a": ia["autoridade_nivel"],
                "autoridade_nivel_b": ib["autoridade_nivel"],
                "lat": anc["lat"], "lon": anc["lon"], "num": anc["num"],
                "decisao": base,
            },
        })
    return achados


# ---------------------------------------------------------------------------
# schema / migração
# ---------------------------------------------------------------------------
def _scope_vazio():
    return {"equiv": {}, "indefinidos": {}, "quarentena": {}}


def lexico_vazio_v33():
    return {"versao": 0, "schema": "3.3", "scopes": {}, "blacklist": []}


def _normalizar_entry_temporal(e: dict) -> None:
    if not isinstance(e, dict):
        return
    last = e.get("last_reinforced") or e.get("last_seen") or e.get("last_observed")
    obs = e.get("last_observed") or e.get("last_seen") or last
    first = e.get("first_seen") or last or obs
    if first:
        e.setdefault("first_seen", first)
    if obs:
        e["last_observed"] = obs
    if last:
        e["last_reinforced"] = last
    e.pop("last_seen", None)
    e.setdefault("evidencias", [])


def migrar_schema(lex):
    if not isinstance(lex, dict):
        return lexico_vazio_v33()
    if lex.get("schema") == "3.3":
        lex.setdefault("scopes", {})
        lex.setdefault("blacklist", [])
        for sd in lex["scopes"].values():
            if not isinstance(sd, dict):
                continue
            sd.setdefault("equiv", {}); sd.setdefault("indefinidos", {}); sd.setdefault("quarentena", {})
            for vars_ in sd["equiv"].values():
                if isinstance(vars_, dict):
                    for e in vars_.values(): _normalizar_entry_temporal(e)
            for e in sd["indefinidos"].values(): _normalizar_entry_temporal(e)
        return lex

    # v3.1 plano: guarda como legado global, nunca aplica automaticamente.
    equiv_old = lex.get("equiv", {}) if isinstance(lex.get("equiv"), dict) else {}
    planas = {k: v for k, v in equiv_old.items()
              if isinstance(v, dict) and "canonical" in v}
    if planas:
        lex.setdefault("equiv_legado_global", {}).update(planas)
        for k in planas: equiv_old.pop(k, None)

    # v3.2 contextual SEM scope: preservar integralmente fora de scopes.
    if equiv_old:
        leg = lex.setdefault("legado_sem_escopo", {})
        leg.setdefault("equiv", {}).update(equiv_old)
    indef_old = lex.get("indefinidos", {}) if isinstance(lex.get("indefinidos"), dict) else {}
    if indef_old:
        globais = {k: v for k, v in indef_old.items()
                   if not isinstance(v, dict) or "contexto" not in v}
        contextuais = {k: v for k, v in indef_old.items() if k not in globais}
        if globais:
            lex.setdefault("indefinidos_legado_global", {}).update(globais)
        if contextuais:
            leg = lex.setdefault("legado_sem_escopo", {})
            leg.setdefault("indefinidos", {}).update(contextuais)
    quar_old = lex.get("quarentena", {}) if isinstance(lex.get("quarentena"), dict) else {}
    if quar_old:
        leg = lex.setdefault("legado_sem_escopo", {})
        leg.setdefault("quarentena", {}).update(quar_old)

    # campos antigos deixam de ser fontes de aplicação.
    lex.pop("equiv", None); lex.pop("indefinidos", None); lex.pop("quarentena", None)
    lex.setdefault("scopes", {})
    lex.setdefault("blacklist", [])
    lex["schema"] = "3.3"
    return lex


def carregar(path):
    return migrar_schema(L.carregar(path))


def _scope(lex: dict, scope_id: str, create: bool = True):
    # v3.3.2: não revarrer TODOS os scopes a cada acesso. A migração completa
    # ocorre na carga/entrada de atualizar(); depois disso o acesso é O(1).
    if lex.get("schema") != "3.3":
        migrar_schema(lex)
    else:
        lex.setdefault("scopes", {})
        lex.setdefault("blacklist", [])
    scopes = lex.setdefault("scopes", {})
    sid = str(scope_id or DEFAULT_SCOPE)
    if create:
        sd = scopes.setdefault(sid, _scope_vazio())
        sd.setdefault("equiv", {}); sd.setdefault("indefinidos", {}); sd.setdefault("quarentena", {})
        return sd
    return scopes.get(sid, _scope_vazio())


def contar_ativos(lex, scope_id: str | None = None) -> int:
    sids = [str(scope_id)] if scope_id is not None else list(migrar_schema(lex).get("scopes", {}))
    total = 0
    for sid in sids:
        sd = _scope(lex, sid, create=False)
        total += sum(1 for vars_ in sd.get("equiv", {}).values()
                     if isinstance(vars_, dict)
                     for e in vars_.values() if isinstance(e, dict) and e.get("status") == "ativo")
    return total


def contar_indefinidos(lex, scope_id: str | None = None, apenas_abertos: bool = True) -> int:
    sids = [str(scope_id)] if scope_id is not None else list(migrar_schema(lex).get("scopes", {}))
    n = 0
    for sid in sids:
        for e in _scope(lex, sid, create=False).get("indefinidos", {}).values():
            if not apenas_abertos or e.get("status") == "indefinido":
                n += 1
    return n


def _pair_tokens(obs: dict):
    a = str(obs.get("token_a") or obs.get("variante") or "")
    b = str(obs.get("token_b") or obs.get("canonico") or "")
    return tuple(sorted((a, b))) if a and b else (a, b)


def _indef_key(ctx: str, a: str, b: str) -> str:
    x, y = sorted((str(a), str(b)))
    return f"{ctx}\u2503{x}~{y}"


def _normalizar_achado(a, scope_id: str, run_id: str):
    if isinstance(a, dict):
        o = dict(a)
        o.setdefault("scope_id", scope_id)
        o.setdefault("run_id", run_id)
        o.setdefault("base_decisao", INDEFINIDO if o.get("canonico") is None else "")
        o.setdefault("evidencia", {"run_id": o.get("run_id", run_id)})
        return o
    vals = list(a)
    if len(vals) < 5:
        raise ValueError("achado legado precisa de (contexto,variante,canonico,base,ancora)")
    ctx, var, canon, base, anc = vals[:5]
    peer = vals[5] if len(vals) > 5 else (canon or "")
    return {"scope_id": scope_id, "contexto": ctx, "token_a": var,
            "token_b": peer, "variante": var, "canonico": canon,
            "base_decisao": base, "ancora": anc, "run_id": run_id,
            "evidencia": {"run_id": run_id, "decisao": base,
                           "lat": anc.get("lat") if isinstance(anc, dict) else None,
                           "lon": anc.get("lon") if isinstance(anc, dict) else None,
                           "num": anc.get("num") if isinstance(anc, dict) else None}}


# ---------------------------------------------------------------------------
# atualização / decay / reativação / indefinidos
# ---------------------------------------------------------------------------
def _nivel_decisao(evid: dict | None) -> int:
    """Maior nível de autoridade que sustentou a decisão corrente."""
    evid = evid or {}
    try:
        return max(int(evid.get("autoridade_nivel_a", 0) or 0),
                   int(evid.get("autoridade_nivel_b", 0) or 0))
    except Exception:
        return 0

def atualizar(lex, achados, min_support: int = 2, agora=None,
              raio_independencia: float = RAIO_INDEPENDENCIA_M,
              scope_id: str = DEFAULT_SCOPE, run_id: str = ""):
    agora = agora or datetime.date.today().isoformat()
    migrar_schema(lex)
    bl = set(lex.get("blacklist", []))
    novos = []
    scopes_tocados = {str(scope_id)}

    for raw in achados:
        o = _normalizar_achado(raw, str(scope_id), run_id)
        sid = str(o.get("scope_id") or scope_id)
        scopes_tocados.add(sid)
        sd = _scope(lex, sid)
        ctx = str(o.get("contexto") or "")
        var = str(o.get("variante") or "")
        canon = o.get("canonico")
        base = str(o.get("base_decisao") or "")
        anc = o.get("ancora") or {}
        evid = dict(o.get("evidencia") or {})
        evid.setdefault("run_id", o.get("run_id") or run_id)
        if var in bl:
            continue

        ta, tb = _pair_tokens(o)
        if canon is None:
            if not (ta and tb and ta != tb):
                # legado sem o outro lado: preserva, mas não finge simetria.
                tb = tb or "?"

            # Autoridades do mesmo nível divergindo invalidam qualquer regra
            # nominal já aplicável para o mesmo par. Não é seguro manter uma
            # regra antiga por frequência enquanto existe conflito oficial.
            if base == CONFLITO_AUTORIDADE and ta and tb and ta != tb:
                vars_ctx = sd.get("equiv", {}).get(ctx, {})
                for vv in (ta, tb):
                    ee = vars_ctx.get(vv)
                    if ee and ee.get("canonical") in {ta, tb} and ee.get("canonical") != vv:
                        ee["status"] = "quarentena"
                        _quarentenar(sd, f"{ctx}: {ta}<->{tb}", CONFLITO_AUTORIDADE, agora,
                                     {"regra_rebaixada": vv, "canonical": ee.get("canonical")})

            k = _indef_key(ctx, ta, tb)
            e = sd["indefinidos"].setdefault(k, {
                "contexto": ctx, "token_a": min(ta, tb), "token_b": max(ta, tb),
                "support": 0, "ancoras": [], "evidencias": [], "status": "indefinido",
                "motivo": base or INDEFINIDO, "first_seen": agora,
                "last_observed": agora, "last_reinforced": None,
            })
            aceitou = _votar(e, anc, raio_independencia, evid)
            e["last_observed"] = agora
            if aceitou:
                e["last_reinforced"] = agora
            if base == CONFLITO_AUTORIDADE:
                e["motivo"] = CONFLITO_AUTORIDADE
            continue

        canon = str(canon)
        if canon == var:
            continue

        # Uma decisão posterior fecha o INDEFINIDO simétrico correspondente.
        if ta and tb and ta != tb:
            ik = _indef_key(ctx, ta, tb)
            ie = sd["indefinidos"].get(ik)
            if ie and ie.get("status") == "indefinido":
                ie["status"] = "resolvido"
                ie["canonical"] = canon
                ie["resolved_run_id"] = o.get("run_id") or run_id
                ie["resolved_at"] = agora
                ie["last_observed"] = agora

        vars_ = sd["equiv"].setdefault(ctx, {})
        alvo = vars_.get(canon)
        if alvo and alvo.get("canonical") == var:
            nivel_novo = _nivel_decisao(evid)
            nivel_antigo = int(alvo.get("autoridade_nivel_decisao", 0) or 0)
            alvo["status"] = "quarentena"
            motivo_rev = "SUBSTITUIDA_POR_AUTORIDADE_SUPERIOR" if (base == "AUTORIDADE_NIVEL" and nivel_novo > nivel_antigo) else "CICLO"
            _quarentenar(sd, f"{ctx}: {var}<->{canon}", motivo_rev, agora,
                         {"nivel_antigo": nivel_antigo, "nivel_novo": nivel_novo})
            if not (base == "AUTORIDADE_NIVEL" and nivel_novo > nivel_antigo):
                vars_.pop(var, None)
                continue
            # Autoridade estritamente superior pode estabelecer a direção nova;
            # a regra antiga permanece preservada em quarentena para auditoria.
        if alvo and alvo.get("status") == "ativo":
            _quarentenar(sd, f"{ctx}: {var}->{canon}", "CADEIA", agora,
                         {"canon_ja_e_chave_de": alvo.get("canonical")})
            continue

        e = vars_.get(var)
        if e and e.get("canonical") != canon:
            _quarentenar(sd, f"{ctx}: {var}->{canon}", "CONFLITO", agora,
                         {"conflito_com": e.get("canonical"), "rebaixado": True})
            e["status"] = "quarentena"
            continue
        if e is None:
            e = vars_[var] = {
                "canonical": canon, "support": 0, "score": 0.0,
                "status": "candidato", "base_decisao": base,
                "autoridade_nivel_decisao": _nivel_decisao(evid),
                "first_seen": agora, "last_observed": agora,
                "last_reinforced": None, "ancoras": [], "evidencias": [],
            }
        e["base_decisao"] = base
        e["autoridade_nivel_decisao"] = max(int(e.get("autoridade_nivel_decisao", 0) or 0), _nivel_decisao(evid))
        aceitou = _votar(e, anc, raio_independencia, evid)
        e["last_observed"] = agora
        if aceitou:
            e["last_reinforced"] = agora
            if e.get("status") == "arquivado":
                e["status"] = "candidato"   # máquina de estados reativável

    # Decay somente nos scopes desta execução; replay não altera last_reinforced.
    for sid in scopes_tocados:
        sd = _scope(lex, sid)
        for ctx, vars_ in sd["equiv"].items():
            for var, e in vars_.items():
                if e.get("status") == "quarentena":
                    continue
                ref = e.get("last_reinforced") or e.get("first_seen") or agora
                dias = L._dias_desde(ref, agora)
                e["score"] = round(float(e.get("support", 0)) *
                                   (0.5 ** (dias / L.MEIA_VIDA_DIAS)), 3)
                if e.get("status") == "candidato" and e.get("support", 0) >= min_support:
                    e["status"] = "ativo"
                    novos.append((sid, ctx, var, e.get("canonical")))
                if e.get("status") == "ativo" and e["score"] < 0.25:
                    e["status"] = "arquivado"
        _sanear_grafos(lex, agora, [sid])

    novos = [(sid, ctx, v, c) for sid, ctx, v, c in novos
             if _scope(lex, sid, False).get("equiv", {}).get(ctx, {}).get(v, {}).get("status") == "ativo"]
    return novos


def _votar(e: dict, anc: dict, raio: float, evidencia: dict | None = None) -> bool:
    """Retorna True SOMENTE quando houve novo reforço físico independente."""
    ancoras = e.setdefault("ancoras", [])
    if int(e.get("support", 0)) >= MAX_SUPPORT:
        e["status_support"] = "saturado"
        return False
    if not isinstance(anc, dict) or not {"num", "lat", "lon"}.issubset(anc):
        return False
    if independente(anc, ancoras, raio):
        e["support"] = int(e.get("support", 0)) + 1
        if len(ancoras) < MAX_ANCORAS:
            ancoras.append({"num": anc.get("num"), "lat": anc.get("lat"), "lon": anc.get("lon")})
        evs = e.setdefault("evidencias", [])
        if evidencia is not None and len(evs) < MAX_EVIDENCIAS:
            ev = dict(evidencia)
            ev.setdefault("num", anc.get("num")); ev.setdefault("lat", anc.get("lat")); ev.setdefault("lon", anc.get("lon"))
            evs.append(ev)
        if e["support"] >= MAX_SUPPORT:
            e["status_support"] = "saturado"
        return True
    return False


def _quarentenar(scope_data: dict, chave, motivo, agora, extra=None):
    scope_data.setdefault("quarentena", {})[chave] = {
        "motivo": motivo, "visto": agora, **(extra or {})}


# ---------------------------------------------------------------------------
# grafo
# ---------------------------------------------------------------------------
def _sanear_grafos(lex: dict, agora: str, scope_ids=None) -> None:
    ids = list(scope_ids) if scope_ids is not None else list(migrar_schema(lex).get("scopes", {}))
    for sid in ids:
        sd = _scope(lex, sid, create=False)
        for ctx, vars_ in sd.get("equiv", {}).items():
            ativos = {v: e.get("canonical") for v, e in vars_.items()
                      if isinstance(e, dict) and e.get("status") == "ativo"
                      and e.get("canonical") not in (None, INDEFINIDO)}
            ruins = {v for v, c in ativos.items() if c in ativos}
            if not ruins:
                continue
            adj = {}
            for v, c in ativos.items():
                adj.setdefault(v, set()).add(c); adj.setdefault(c, set()).add(v)
            visitados = set()
            for raiz in sorted(ruins):
                if raiz in visitados: continue
                pilha, comp = [raiz], set()
                while pilha:
                    n = pilha.pop()
                    if n in comp: continue
                    comp.add(n); visitados.add(n); pilha.extend(adj.get(n, ()))
                vars_af = sorted(v for v in comp if v in ativos)
                for v in vars_af: vars_[v]["status"] = "quarentena"
                _quarentenar(sd, f"GRAFO:{ctx}:" + "|".join(sorted(comp)),
                             "CADEIA_OU_CICLO", agora,
                             {"variantes": vars_af,
                              "arestas": [f"{v}->{ativos[v]}" for v in vars_af]})


def diagnosticar_grafo(lex: dict, scope_id: str | None = None) -> list[dict]:
    ids = [scope_id] if scope_id is not None else list(migrar_schema(lex).get("scopes", {}))
    out = []
    for sid in ids:
        sd = _scope(lex, str(sid), False)
        for ctx, vars_ in sd.get("equiv", {}).items():
            ativos = {v: e.get("canonical") for v, e in vars_.items()
                      if isinstance(e, dict) and e.get("status") == "ativo"}
            for v, c in ativos.items():
                if c in ativos:
                    out.append({"scope_id": sid, "contexto": ctx, "variante": v,
                                "canonico": c, "canonico_tambem_variante": True})
    return out


# ---------------------------------------------------------------------------
# aplicação / promoção sugerida
# ---------------------------------------------------------------------------
def mapa_ativo(lex, scope_id: str = DEFAULT_SCOPE) -> dict:
    sd = _scope(lex, str(scope_id), create=False)
    out = {}
    for ctx, vars_ in sd.get("equiv", {}).items():
        ativos = {v: e.get("canonical") for v, e in vars_.items()
                  if isinstance(e, dict) and e.get("status") == "ativo"
                  and e.get("canonical") not in (None, INDEFINIDO)}
        for v, c in ativos.items():
            if c not in ativos:
                out[(ctx, v)] = c
    return out


def aplicar(logr_norm, ativos_ctx: dict) -> str:
    if not ativos_ctx:
        return logr_norm
    toks = str(logr_norm).split(); out = list(toks)
    for i, t in enumerate(toks):
        c = ativos_ctx.get((contexto_de(toks, i), t))
        if c: out[i] = c
    return " ".join(out)


def candidatas_globais(lex, min_contextos: int = 3):
    """Sugestão estrutural; conta repetição em contextos/escopos, nunca autoaplica."""
    c = Counter()
    for sid, sd in migrar_schema(lex).get("scopes", {}).items():
        for ctx, vars_ in sd.get("equiv", {}).items():
            for v, e in vars_.items():
                if e.get("status") == "ativo":
                    c[(v, e.get("canonical"))] += 1
    return sorted(((v, k, n) for (v, k), n in c.items() if n >= min_contextos),
                  key=lambda t: -t[2])
