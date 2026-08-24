# -*- coding: utf-8 -*-
"""
dedup_v3.py — dedup por EVIDENCIA com TOKEN DE CONTEXTO e trava de diametro.

Motivado pela auditoria de Canoas-RS e Santa Maria-RS sobre a v2.0.0, que mediu
**42 (10,1%) e 66 (4,5%) fusoes indevidas** entre clusters — estabelecimentos
reais apagados da entrega. Casos: `Cheirin Bao Canoas Shopping` + `CVC Canoas
Shopping Center`; quatro bancas de advocacia distintas no mesmo predio;
`Casa de Carnes Camobi` + `Auto Posto Camobi` + `Restaurante Grill Camobi`;
`Setor A` + `Setor B` + ... + `Setor E`.

CAUSA RAIZ. `_name_core` remove tokens genericos de ramo (`casa`, `posto`,
`farmacia`, `advocacia`...) para achar o nucleo distintivo. Em galeria, shopping
ou predio comercial, o que SOBRA e justamente o token COMPARTILHADO — o nome do
shopping, do bairro, um adjetivo ou uma sigla. `token_set_ratio` devolve 90-100
e o union-find funde tudo a <=30 m, sem limite de cadeia.

O QUE MUDA

  1. TOKEN DE CONTEXTO (IDF local). Token que aparece em >= `ctx_min` POIs dentro
     de `ctx_raio_m` deixa de distinguir: `canoas`, `shopping`, `camobi`, `setor`.
     O nucleo DISCRIMINANTE e o que sobra depois de tirar contexto. Nucleo
     discriminante vazio => NAO funde por nome. Mata a familia inteira de casos.
  2. JACCARD nos tokens discriminantes, alem do `token_set_ratio`. `token_set_ratio`
     da 100 quando um nome e SUBCONJUNTO do outro (`Farmacia Universitaria` x
     `Abastecedora Universitaria`); Jaccard nao.
  3. TOKEN DE 1 CARACTERE PRESERVADO. A v2 descarta `len(t) <= 1`, entao
     `Setor A` e `Setor B` viram o mesmo nucleo. Aqui o token curto vale quando e
     o unico diferenciador.
  4. NUCLEO CURTO EXIGE MESMO SEGMENTO. `Lancheria X9` x `Estacionamento X9`.
  5. TELEFONE/SITE COMO EVIDENCIA INDEPENDENTE DO NOME. Coincidencia autoriza raio
     maior; DIVERGENCIA VETA a fusao mesmo com nome identico (duas lojas da mesma
     rede no mesmo quarteirao). Telefone presente em > `tel_max_locais` posicoes
     (call center, condominio) deixa de ser evidencia e deixa de vetar.
  6. TRAVA DE DIAMETRO (Kruskal com restricao). Diametro observado na auditoria:
     85,2 m para um raio configurado de 30 m. Aresta so entra se o cluster
     resultante couber na tolerancia.
  7. SEM NOME. Funde com outro sem nome (categoria compativel + raio curto) e e
     absorvido por ponto nomeado proximo (`semnome_modo='absorver'`). Sem isso,
     admitir POI sem `name` do OSM injeta duplicata.
  8. GUARDA DE PRECISAO. Coordenada empilhada (5,6% da base — centroide de CEP) ou
     de baixa precisao decimal nao funde por proximidade sozinha.
  9. ANCORA. `sem` (OSM, que nao publica confianca) passa a ganhar de `baixa`;
     desempate por precisao posicional e so entao por id. Na auditoria, o Overture
     era ancora em 100,0% de 1.682 fusoes — determinismo, nao tendencia.
 10. AUDITORIA. Devolve `vinculos` par a par (motivo, distancia, score, aceito),
     incluindo as fusoes RECUSADAS e o porque.

DEFEITO CORRIGIDO. `_name_core(nan)` devolve `'nan'` (float NaN e truthy, entao
`str(v or "")` nao neutraliza). Na v2, dois registros SEM NOME casam com
`token_set_ratio('nan','nan') = 100` e fundem a 30 m mesmo com segmentos
divergentes; o ramo dos sem-nome (12 m) e codigo morto.

Determinismo: arestas ordenadas por (peso desc, i asc, j asc); o resultado
independe da ordem das linhas de entrada.
"""
import math
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tratar_pois import _STOP_NOME, _ascii, _scorer  # noqa: E402

R_TERRA = 6378137.0

PARAMS = dict(
    raio_nome_m=30.0,         # fusao por similaridade de nome
    raio_forte_m=200.0,       # fusao por telefone/site discriminativo
    raio_semnome_m=12.0,      # fusao envolvendo ponto sem nome
    raio_marca_m=15.0,        # mesma marca => rede: exige proximidade curta
    sim_min=85.0,             # limiar de nome, mesmo segmento
    sim_cross=92.0,           # limiar de nome, segmentos divergentes
    jaccard_min=0.60,         # sobreposicao minima dos tokens discriminantes
    diam_max_m=90.0,          # diametro maximo de cluster por nome/categoria
    diam_forte_m=300.0,       # diametro maximo quando ha aresta de telefone/site
    ctx_raio_m=200.0,         # vizinhanca onde se mede se o token distingue
    ctx_min=3,                # token em >= N POIs da vizinhanca => contexto
    tel_max_locais=3,         # telefone em > N posicoes => hub, nao e evidencia
    precisao_max_m=60.0,      # pior precisao aceita p/ fusao por proximidade pura
    nucleo_curto_chars=5,     # nucleo com menos que isso exige mesmo segmento
    semnome_modo="absorver",  # 'absorver' | 'marcar'
)

# `sem` (OSM) acima de `baixa`: fonte que nao declara confianca nao pode ser
# tratada como fonte de confianca ruim.
_PRIO_V3 = {"alta": 4, "média": 3, "media": 3, "sem": 2, "baixa": 1}
_GEN_SEG = {None, "", "Outros", "Não Informado", "nan"}
ANCORA = ["id_fonte", "fonte", "nome", "lat", "lon"]

COLS_AUDITORIA = ["precisao_coord_m", "coord_empilhada", "n_registros_fundidos",
                  "dedup_motivos", "nucleo_discriminante"]


# --------------------------------------------------------------- normalizadores
def _texto(v):
    """String util ou '' — neutraliza None, NaN e os literais 'nan'/'none'."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "nan", "none", "null", "<na>") else s


def tokens_brutos(v):
    """Todos os tokens do nome, SEM remover generico. Serve para decidir se dois
    nomes sao literalmente o mesmo — `Bar do Zeca` x `Bar do Zeca` — o que o nucleo
    discriminante nao consegue dizer quando todo o nome e contexto."""
    s = _texto(v)
    return [t for t in re.split(r"[^a-z0-9]+", _ascii(s).lower()) if t] if s else []


def tokens_nome(v):
    """Tokens do nome sem acento/pontuacao, sem generico de ramo.

    Diferenca da v2: token de 1 caractere e PRESERVADO (`Setor A` x `Setor B`) e
    NaN nunca vira o token 'nan'."""
    s = _texto(v)
    if not s:
        return []
    toks = [t for t in re.split(r"[^a-z0-9]+", _ascii(s).lower()) if t]
    core = [t for t in toks if t not in _STOP_NOME]
    return core if core else toks


def tel_key(s):
    """Telefone reduzido a DDD+numero. None se nao for telefone BR plausivel."""
    s = _texto(s)
    if not s:
        return None
    d = re.sub(r"\D", "", s)
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]
    return d if len(d) in (10, 11) else None


def site_key(s):
    """host+path sem esquema/www/query. Host puro NAO e discriminativo (franquia)."""
    s = _texto(s)
    if not s:
        return None
    u = re.sub(r"^https?://", "", s.lower())
    u = re.sub(r"^www\.", "", u).split("?")[0].split("#")[0].rstrip("/")
    return u or None


def _casas(v):
    """Casas decimais efetivas do valor entregue (proxy de precisao da fonte)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    s = repr(round(f, 9))
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def sinais_precisao(df):
    """Adiciona `precisao_coord_m` e `coord_empilhada`. O(n), sem descarte.

    `precisao_coord_m` = 111320 m x 10^-casas (casas = min entre lat e lon).
    `coord_empilhada` = quantos pontos dividem o par lat/lon exato — assinatura de
    geocodificacao por centroide de CEP/logradouro, nao de posicao levantada."""
    df = df.copy()
    ca = [_casas(v) for v in df["lat"]]
    co = [_casas(v) for v in df["lon"]]
    df["precisao_coord_m"] = [
        None if (a is None or b is None) else round(111320.0 * 10.0 ** (-min(a, b)), 1)
        for a, b in zip(ca, co)]
    chave = (pd.to_numeric(df["lat"], errors="coerce").astype(str) + "|"
             + pd.to_numeric(df["lon"], errors="coerce").astype(str))
    df["coord_empilhada"] = chave.map(chave.value_counts()).astype("int64")
    return df


# --------------------------------------------------------- tokens de contexto
def tokens_contexto(tokens, x, y, raio_m, ctx_min):
    """Para cada ponto, os tokens que NAO distinguem na vizinhanca.

    Um token e contexto para o ponto i se aparece em >= `ctx_min` pontos dentro de
    `raio_m` de i (contando i). Nome de shopping, de bairro e generico de galeria
    caem aqui; nome proprio de estabelecimento nao.

    Custo: so tokens com frequencia global >= ctx_min sao testados, e cada um monta
    uma arvore so com seus proprios pontos. Barato mesmo em base estadual."""
    from scipy.spatial import cKDTree
    inv = {}
    for i, ts in enumerate(tokens):
        for t in set(ts):
            inv.setdefault(t, []).append(i)
    ctx = [set() for _ in tokens]
    for t, idxs in inv.items():
        if len(idxs) < ctx_min:
            continue                                   # nao ha como ser contexto
        arr = np.asarray(idxs)
        tree = cKDTree(np.c_[x[arr], y[arr]])
        viz = tree.query_ball_point(np.c_[x[arr], y[arr]], r=float(raio_m))
        for k, v in enumerate(viz):
            if len(v) >= ctx_min:
                ctx[int(arr[k])].add(t)
    return ctx


def _nucleo(tokens, ctx):
    """Nucleo discriminante = tokens que sobram depois de tirar o contexto."""
    return {t for t in tokens if t not in ctx}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ------------------------------------------------------------------ union-find
class _UF:
    """Union-find com bbox por raiz e tolerancia de diametro por cluster.

    O bbox e guardado em GRAUS e o diametro e medido em metros na latitude do
    proprio cluster. Assim a consolidacao pode ser GLOBAL (estado inteiro) sem
    depender de uma projecao metrica unica — que erraria ~0,5% da escala em x nas
    bordas de uma UF larga e poderia virar uma decisao de diametro."""

    def __init__(self, lat, lon, tol0):
        n = len(lat)
        self.p = np.arange(n)
        self.minla, self.maxla = lat.copy(), lat.copy()
        self.minlo, self.maxlo = lon.copy(), lon.copy()
        self.tol = np.full(n, float(tol0))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return int(i)

    @staticmethod
    def _diam(mnla, mxla, mnlo, mxlo):
        dy = (mxla - mnla) * 111320.0
        dx = (mxlo - mnlo) * 111320.0 * math.cos(math.radians((mxla + mnla) / 2.0))
        return math.hypot(dx, dy)

    def tenta_unir(self, i, j, tol_aresta):
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        mnla, mxla = min(self.minla[ri], self.minla[rj]), max(self.maxla[ri], self.maxla[rj])
        mnlo, mxlo = min(self.minlo[ri], self.minlo[rj]), max(self.maxlo[ri], self.maxlo[rj])
        tol = max(self.tol[ri], self.tol[rj], tol_aresta)
        if self._diam(mnla, mxla, mnlo, mxlo) > tol:
            return False
        r, o = (ri, rj) if ri < rj else (rj, ri)
        self.p[o] = r
        self.minla[r], self.maxla[r] = mnla, mxla
        self.minlo[r], self.maxlo[r] = mnlo, mxlo
        self.tol[r] = tol
        return True


# ---------------------------------------------------------------- coalescencia
def coalescer(res, grp):
    """1 linha por grupo. Identidade/geo da ANCORA; demais colunas coalescidas.

    Ancora = maior `_PRIO_V3` -> melhor `precisao_coord_m` -> menor `id_fonte`.
    O ultimo criterio existe so para determinismo; os dois primeiros e que decidem."""
    res = res.copy()
    res["_g"] = grp
    res["_p"] = res["confianca_classe"].map(_PRIO_V3).fillna(2).astype(int)
    res["_prec"] = pd.to_numeric(res.get("precisao_coord_m"), errors="coerce").fillna(9e9)
    res["_id"] = res["id_fonte"].astype(str)
    res = res.sort_values(["_p", "_prec", "_id"], ascending=[False, True, True], kind="stable")
    g = res.groupby("_g", sort=False)
    fontes = g["fontes"].agg(
        lambda s: ",".join(sorted(set(",".join(s.dropna().astype(str)).split(","))))).to_dict()
    anchor = res.drop_duplicates("_g", keep="first").set_index("_g")
    cols = [c for c in res.columns if c not in ("_g", "_p", "_prec", "_id")]
    ded = g[cols].first()
    for c in ANCORA:
        if c in ded.columns:
            ded[c] = anchor[c]
    ded["fontes"] = ded.index.map(fontes)
    return ded.reset_index(drop=True)


# ---------------------------------------------------------------------- dedup
def _params(kw):
    p = dict(PARAMS)
    p.update({k: v for k, v in kw.items() if k in PARAMS and v is not None})
    return p


def preparar(res, p):
    """Sinais que valem para a BASE INTEIRA, calculados uma vez.

    Token de contexto e telefone-hub sao propriedades da VIZINHANCA, nao da
    particao de processamento: medi-los por municipio faria a mesma marca ser
    contexto de um lado da divisa e discriminante do outro. Aqui sao globais."""
    res = res.reset_index(drop=True)
    n = len(res)
    lat = pd.to_numeric(res["lat"], errors="coerce").to_numpy()
    lon = pd.to_numeric(res["lon"], errors="coerce").to_numpy()
    ok = ~(np.isnan(lat) | np.isnan(lon))
    idx = np.where(ok)[0]
    lat0 = math.radians(float(np.nanmean(lat[idx]))) if len(idx) else 0.0
    # projecao GROSSA: serve para grade de blocos e contagem de contexto (tolerantes
    # a ~0,5% de escala). Distancia de matching NAO usa esta — ver `_xy_local`.
    x = np.zeros(n)
    y = np.zeros(n)
    x[idx] = np.radians(lon[idx]) * R_TERRA * math.cos(lat0)
    y[idx] = np.radians(lat[idx]) * R_TERRA

    def col(nome):
        return res[nome].tolist() if nome in res.columns else [None] * n

    nomes = res["nome"].tolist()
    toks = [tokens_nome(v) for v in nomes]
    ctx = tokens_contexto(toks, x, y, p["ctx_raio_m"], int(p["ctx_min"]))
    tels = [tel_key(v) for v in col("telefone")]
    pos = {}
    for i, t in enumerate(tels):
        if t:
            pos.setdefault(t, set()).add((round(x[i]), round(y[i])))
    hub = {t for t, s in pos.items() if len(s) > p["tel_max_locais"]}

    return dict(
        res=res, n=n, lat=lat, lon=lon, ok=ok, idx=idx, x=x, y=y,
        toks=toks, brutos=[set(tokens_brutos(v)) for v in nomes],
        nucleos=[_nucleo(t, c) for t, c in zip(toks, ctx)],
        segs=[_texto(v) or None for v in col("segmento")],
        cats=[_texto(v) or None for v in col("categoria_pt")],
        marcas=[(" ".join(sorted(tokens_nome(v))) or None) for v in col("marca")],
        tels=[None if (t in hub) else t for t in tels],
        sites=[site_key(v) for v in col("site")],
        prec=pd.to_numeric(res["precisao_coord_m"], errors="coerce").fillna(1e9).to_numpy(),
        emp=pd.to_numeric(res["coord_empilhada"], errors="coerce").fillna(1).to_numpy(),
        scorer=_scorer()[0], hubs=len(hub))


def _xy_local(ctx, sel):
    """Projecao metrica LOCAL do bloco: lat0 do proprio bloco, erro desprezivel."""
    lat, lon = ctx["lat"][sel], ctx["lon"][sel]
    lat0 = math.radians(float(np.nanmean(lat))) if len(lat) else 0.0
    return (np.radians(lon) * R_TERRA * math.cos(lat0), np.radians(lat) * R_TERRA)


def arestas_do_bloco(ctx, sel, p):
    """Arestas candidatas dentro de um bloco. `sel` = indices GLOBAIS a considerar.

    Devolve `(arestas, vinculos)` com indices globais — o bloco decide o PAR, nunca
    o cluster. Quem fecha o cluster e a consolidacao global, e e por isso que um par
    separado pela divisa municipal nao se perde mais."""
    from scipy.spatial import cKDTree
    sel = np.asarray([i for i in sel if ctx["ok"][i]], dtype=np.int64)
    arestas, vinc = [], []
    if len(sel) < 2:
        return arestas, vinc
    xs, ys = _xy_local(ctx, sel)
    tree = cKDTree(np.c_[xs, ys])
    pares = tree.query_pairs(r=float(p["raio_forte_m"]), output_type="ndarray")
    toks, nucleos, brutos = ctx["toks"], ctx["nucleos"], ctx["brutos"]
    segs, cats, marcas = ctx["segs"], ctx["cats"], ctx["marcas"]
    tels, sites, emp, prec = ctx["tels"], ctx["sites"], ctx["emp"], ctx["prec"]
    scorer = ctx["scorer"]
    for a, b in pares:
        i, j = int(sel[a]), int(sel[b])
        if i > j:
            i, j = j, i
            a, b = b, a
        d = math.hypot(xs[a] - xs[b], ys[a] - ys[b])
        ti, tj, si, sj = tels[i], tels[j], sites[i], sites[j]
        gi, gj = segs[i], segs[j]

        # VETO — telefones discriminativos e divergentes sao unidades distintas
        if ti and tj and ti != tj:
            vinc.append((i, j, "veto_telefone_divergente", d, 0.0, False))
            continue

        peso = motivo = None
        tol = p["diam_max_m"]
        if ti and tj and ti == tj:
            peso, motivo, tol = 100.0, "telefone", p["diam_forte_m"]
        elif si and sj and si == sj and "/" in si:        # host+path; host puro nao basta
            peso, motivo, tol = 95.0, "site", p["diam_forte_m"]
        elif toks[i] and toks[j]:
            if d <= p["raio_nome_m"]:
                peso, motivo = _aresta_nome(i, j, d, nucleos[i], nucleos[j],
                                            brutos[i], brutos[j], gi, gj, marcas,
                                            scorer, p, vinc)
        elif not toks[i] and not toks[j]:
            peso, motivo = _aresta_semnome(i, j, d, cats, gi, gj, emp, prec, p, vinc)
        else:
            peso, motivo = _aresta_absorcao(i, j, d, toks, cats, gi, gj, emp, prec, p, vinc)

        if peso is not None:
            arestas.append((peso, i, j, motivo, d, tol))
    return arestas, vinc


def consolidar(ctx, arestas, vinc, p):
    """Kruskal com restricao de diametro sobre TODAS as arestas, e coalescencia.

    Deduplica aresta vista em mais de um bloco (halo) mantendo o maior peso; a
    ordem `(peso desc, i asc, j asc)` faz o resultado nao depender de como a base
    foi particionada nem da ordem das linhas."""
    res, n = ctx["res"], ctx["n"]
    vistas = {}
    for peso, i, j, motivo, d, tol in arestas:
        k = (i, j)
        atual = vistas.get(k)
        if atual is None or peso > atual[0]:
            vistas[k] = (peso, i, j, motivo, d, tol)
    unicas = sorted(vistas.values(), key=lambda e: (-e[0], e[1], e[2]))

    uf = _UF(ctx["lat"], ctx["lon"], p["diam_max_m"])
    vinc = list(vinc)
    for peso, i, j, motivo, d, tol in unicas:
        aceito = uf.tenta_unir(i, j, tol)
        vinc.append((i, j, motivo if aceito else "recusa_diametro", d, peso, aceito))

    grp = [uf.find(i) for i in range(n)]
    res = res.copy()
    res["_g_tmp"] = grp
    res["n_registros_fundidos"] = res.groupby("_g_tmp")["id_fonte"].transform("size").astype(int)
    mot = {}
    for i, j, m, d, s, acc in vinc:
        if acc:
            mot.setdefault(uf.find(i), set()).add(m)
    res["dedup_motivos"] = res["_g_tmp"].map(lambda g: ",".join(sorted(mot.get(g, []))) or None)
    res["nucleo_discriminante"] = [" ".join(sorted(v)) or None for v in ctx["nucleos"]]
    res = res.drop(columns=["_g_tmp"])

    ids = res["id_fonte"].astype(str).to_numpy()
    fon = (res["fonte"].astype(str).to_numpy() if "fonte" in res.columns
           else np.array(["?"] * n))
    chave = np.array(["%s:%s" % (f, i) for f, i in zip(fon, ids)])

    # `cluster_id` = chave natural da ANCORA (mesma regra de desempate do coalescer).
    # Deterministico e legivel, e sobrevive a entrada de um membro de menor
    # prioridade. ID PERMANENTE entre execucoes exige store append-only — fora do
    # escopo desta skill; ver SKILL.md.
    ordem = pd.DataFrame({
        "_g": grp,
        "_p": res["confianca_classe"].map(_PRIO_V3).fillna(2).astype(int).to_numpy(),
        "_prec": pd.to_numeric(res.get("precisao_coord_m"), errors="coerce").fillna(9e9).to_numpy(),
        "_id": ids, "_chave": chave})
    ancora = (ordem.sort_values(["_p", "_prec", "_id"], ascending=[False, True, True],
                                kind="stable")
              .drop_duplicates("_g", keep="first").set_index("_g")["_chave"].to_dict())
    res["cluster_id"] = [ancora[g] for g in grp]

    obs = pd.DataFrame({
        "id_fonte": ids, "fonte": fon, "cluster_id": res["cluster_id"].to_numpy(),
        "ancora": [chave[i] == ancora[grp[i]] for i in range(n)],
        "lat": ctx["lat"], "lon": ctx["lon"],
        "precisao_coord_m": res.get("precisao_coord_m"),
        "COD_MUNICIPIO": (res["COD_MUNICIPIO"] if "COD_MUNICIPIO" in res.columns else None),
    }).sort_values(["cluster_id", "fonte", "id_fonte"], kind="stable").reset_index(drop=True)

    dfv = pd.DataFrame(
        [(ids[i], ids[j], m, round(d, 2), round(float(s), 1), bool(acc))
         for i, j, m, d, s, acc in vinc],
        columns=["id_a", "id_b", "motivo", "dist_m", "score", "aceito"]
    ).drop_duplicates(subset=["id_a", "id_b", "motivo"], keep="first") \
     .sort_values(["id_a", "id_b", "motivo"], kind="stable").reset_index(drop=True)
    return coalescer(res, grp), dfv, obs


def dedup_evidencia(res, **kw):
    """Dedup multi-sinal em bloco unico. Retorna `(df_dedup, df_vinculos)`."""
    ded, dfv, _ = dedup_auditado(res, **kw)
    return ded, dfv


def dedup_auditado(res, **kw):
    """Igual ao `dedup_evidencia`, devolvendo tambem o elo observacao -> entidade."""
    p = _params(kw)
    if "precisao_coord_m" not in res.columns or "coord_empilhada" not in res.columns:
        res = sinais_precisao(res)
    res = res.reset_index(drop=True)
    vazio = pd.DataFrame(columns=["id_a", "id_b", "motivo", "dist_m", "score", "aceito"])
    if len(res) < 2:
        res = res.copy()
        for c, v in (("n_registros_fundidos", 1), ("dedup_motivos", None),
                     ("nucleo_discriminante", None)):
            if c not in res.columns:
                res[c] = v
        chave = ["%s:%s" % (f, i) for f, i in zip(res.get("fonte", pd.Series(dtype=str)),
                                                  res.get("id_fonte", pd.Series(dtype=str)))]
        res["cluster_id"] = chave
        obs = pd.DataFrame({"id_fonte": res.get("id_fonte"), "fonte": res.get("fonte"),
                            "cluster_id": chave, "ancora": [True] * len(res)})
        return res, vazio, obs
    ctx = preparar(res, p)
    arestas, vinc = arestas_do_bloco(ctx, np.arange(ctx["n"]), p)
    return consolidar(ctx, arestas, vinc, p)


def blocos_grade(ctx, celula_m, halo_m):
    """Blocos da grade: nucleo da celula + halo dos vizinhos.

    O halo tem de ser >= `raio_forte_m`, senao um par valido fica partido entre duas
    celulas e nunca e avaliado. Cada par aparece em pelo menos um bloco; a aresta
    repetida em blocos vizinhos e deduplicada na consolidacao."""
    x, y, idx = ctx["x"], ctx["y"], ctx["idx"]
    if not len(idx):
        return
    cx = np.floor(x[idx] / celula_m).astype(np.int64)
    cy = np.floor(y[idx] / celula_m).astype(np.int64)
    nucleo = {}
    for k, i in zip(zip(cx.tolist(), cy.tolist()), idx.tolist()):
        nucleo.setdefault(k, []).append(i)
    for k in sorted(nucleo):
        gx, gy = k
        w, e = gx * celula_m - halo_m, (gx + 1) * celula_m + halo_m
        s, nn = gy * celula_m - halo_m, (gy + 1) * celula_m + halo_m
        viz = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                viz.extend(nucleo.get((gx + dx, gy + dy), ()))
        bloco = [i for i in viz if (w <= x[i] <= e and s <= y[i] <= nn)]
        yield k, sorted(nucleo[k]), sorted(set(bloco))


def dedup_particionado(res, celula_m=2000.0, halo_m=None, **kw):
    """Dedup com blocking por GRADE + halo, e consolidacao GLOBAL.

    Por que nao particionar por municipio: dois registros da mesma loja a 2 m e 3 m
    de lados opostos da divisa nunca entram no mesmo universo de matching. A divisa
    passa a ser ATRIBUTO da entidade (herdado da ancora), nao parede do matching.

    A grade so decide quais PARES sao avaliados; o cluster e fechado uma unica vez,
    globalmente. Mesma base -> mesmo resultado, com qualquer tamanho de celula (desde
    que `halo_m >= raio_forte_m`)."""
    p = _params(kw)
    if "precisao_coord_m" not in res.columns or "coord_empilhada" not in res.columns:
        res = sinais_precisao(res)
    res = res.reset_index(drop=True)
    if len(res) < 2:
        return dedup_auditado(res, **kw)
    halo = float(halo_m) if halo_m else max(float(p["raio_forte_m"]), 250.0)
    if halo < p["raio_forte_m"]:
        raise ValueError("halo_m (%s) < raio_forte_m (%s): par valido ficaria partido "
                         "entre celulas" % (halo, p["raio_forte_m"]))
    ctx = preparar(res, p)
    arestas, vinc = [], []
    for _k, _nucleo, bloco in blocos_grade(ctx, float(celula_m), halo):
        a, v = arestas_do_bloco(ctx, bloco, p)
        arestas.extend(a)
        vinc.extend(v)
    return consolidar(ctx, arestas, vinc, p)


def _aresta_nome(i, j, d, ni, nj, ti, tj, gi, gj, marcas, scorer, p, vinc):
    """Fusao por nome — so com nucleo DISCRIMINANTE dos dois lados.

    Excecao: quando os dois nomes sao LITERALMENTE o mesmo (mesmo conjunto de tokens
    brutos, sem remover generico), o contexto nao pode bloquear. Contexto explica por
    que dois nomes DIFERENTES parecem iguais; nao explica dois nomes iguais. Comparar
    o token bruto e nao o nucleo e o que separa `Bar do Zeca` x `Bar do Zeca` (mesmo
    lugar em duas fontes) de `Auto Posto Camobi` x `Farmacia Camobi` (dois lugares que
    so dividem o bairro). Nesse caso o que segura a rede continua sendo `raio_marca_m`
    e a trava de diametro."""
    identico = bool(ti) and ti == tj
    if (not ni or not nj) and not identico:
        vinc.append((i, j, "recusa_contexto_sem_nucleo", d, 0.0, False))
        return None, None
    if identico and (not ni or not nj):
        ni, nj = ti, tj
    jac = _jaccard(ni, nj)
    s = float(scorer(" ".join(sorted(ni)), " ".join(sorted(nj))))
    mesmo_seg = (gi == gj) or (gi in _GEN_SEG) or (gj in _GEN_SEG)
    thr = p["sim_min"] if mesmo_seg else max(p["sim_min"], p["sim_cross"])
    if s < thr:
        return None, None
    if jac < p["jaccard_min"]:
        # `token_set_ratio` da 100 quando um nome e subconjunto do outro
        vinc.append((i, j, "recusa_jaccard", d, s, False))
        return None, None
    curto = min(len(" ".join(ni)), len(" ".join(nj))) < p["nucleo_curto_chars"]
    if curto and not (gi and gj and gi == gj):
        vinc.append((i, j, "recusa_nucleo_curto_segmento", d, s, False))
        return None, None
    if marcas[i] and marcas[j] and marcas[i] == marcas[j] and d > p["raio_marca_m"]:
        vinc.append((i, j, "recusa_marca_rede", d, s, False))
        return None, None
    return s, ("nome_marca" if (marcas[i] and marcas[i] == marcas[j]) else "nome")


def _aresta_semnome(i, j, d, cats, gi, gj, emp, prec, p, vinc):
    """Dois pontos sem nome: so categoria compativel, raio curto e coordenada boa."""
    if d > p["raio_semnome_m"]:
        return None, None
    if not ((cats[i] and cats[i] == cats[j]) or (gi and gi == gj and gi not in _GEN_SEG)):
        return None, None
    if (emp[i] > 1 or emp[j] > 1 or prec[i] > p["precisao_max_m"]
            or prec[j] > p["precisao_max_m"]):
        vinc.append((i, j, "recusa_coord_suspeita", d, 0.0, False))
        return None, None
    return 60.0, "semnome_categoria"


def _aresta_absorcao(i, j, d, toks, cats, gi, gj, emp, prec, p, vinc):
    """Um sem nome, outro nomeado: absorve (ou so marca) o sem nome."""
    k = i if not toks[i] else j
    m = j if not toks[i] else i
    if d > p["raio_semnome_m"]:
        return None, None
    if not ((cats[i] and cats[i] == cats[j]) or (gi and gi == gj and gi not in _GEN_SEG)):
        return None, None
    if emp[k] > 1 or prec[k] > p["precisao_max_m"]:
        vinc.append((k, m, "recusa_coord_suspeita", d, 0.0, False))
        return None, None
    if p["semnome_modo"] != "absorver":
        vinc.append((k, m, "marcado_possivel_duplicata", d, 0.0, False))
        return None, None
    return 55.0, "absorcao_semnome"


# ------------------------------------------------------------- gate semantico
def fusoes_suspeitas(dfv, origem):
    """Fusoes ACEITAS entre registros da MESMA fonte com nucleos divergentes.

    Criterio da auditoria: Overture e OSM nao publicam o mesmo estabelecimento duas
    vezes com nomes distintos — fusao intra-fonte com nome divergente e, quase
    sempre, dois lugares virando um. `origem` mapeia id_fonte -> (fonte, nucleo)."""
    if not len(dfv):
        return dfv.assign(nucleo_a=[], nucleo_b=[]) if "id_a" in dfv.columns else dfv
    ok = dfv[dfv["aceito"]].copy()
    if not len(ok):
        return ok.assign(nucleo_a=None, nucleo_b=None)
    fa = ok["id_a"].map(lambda i: origem.get(i, ("", ""))[0])
    fb = ok["id_b"].map(lambda i: origem.get(i, ("", ""))[0])
    na = ok["id_a"].map(lambda i: origem.get(i, ("", ""))[1] or "")
    nb = ok["id_b"].map(lambda i: origem.get(i, ("", ""))[1] or "")
    susp = ok[(fa == fb) & (fa != "") & (na != nb) & (na != "") & (nb != "")].copy()
    susp["nucleo_a"] = na[susp.index]
    susp["nucleo_b"] = nb[susp.index]
    return susp
