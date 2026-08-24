# -*- coding: utf-8 -*-
"""Tratamento PT (`normalize`) e dedup por evidencia (`dedup`), por municipio.

`normalize` roda em lotes retomaveis: categoria->PT (com fallback pela hierarquia
da fonte), Title Case, telefone, parser de endereco BR, sinais de precisao da
coordenada e separacao entre `bairro` e `localidade_fonte`. `--min-conf` filtra
Overture de baixa confianca — DESCARTE POR REGRA DE NEGOCIO, contabilizado no funil
E gravado linha a linha em `rejeitados_normalize.parquet` (v3.0.0).

`dedup` (v3.2.0) faz blocking por GRADE + halo e consolida GLOBALMENTE. Ate a v3.1.0
a particao era o MUNICIPIO — eficiente, mas uma parede: o mesmo estabelecimento visto
por duas fontes a 2 m e 3 m de lados opostos da divisa nunca entrava no mesmo universo
de matching. Agora a grade decide so quais PARES sao avaliados; o cluster fecha uma vez,
no global, e o municipio vira ATRIBUTO da entidade (herdado da ancora).
Ordenacao estavel antes do dedup garante o mesmo sobrevivente para a mesma entrada.

v3.0.0 — o motor passou a ser o `dedup_v3` (contexto + telefone + trava de diametro)
e cada municipio grava a TABELA DE VINCULOS par a par. A auditoria de Canoas e Santa
Maria mediu 42 e 66 fusoes indevidas com o motor anterior; sem a tabela de vinculos
elas eram invisiveis — sobrava so o nome do sobrevivente.
"""
import glob
import hashlib
import os
import time
import unicodedata

import pandas as pd

from .config import salvar_atomico
from .territorio import TERR
from .vendor import extrair_pois as ep
from .vendor import tratar_pois as tp

PADRAO = ["cluster_id", "id_fonte", "fonte", "fontes", "nome", "sem_nome",
          "segmento", "categoria_pt", "categoria_orig", "categoria_hier",
          "lat", "lon", "precisao_coord_m", "coord_empilhada",
          "logradouro", "numero", "quadra", "lote", "bairro", "localidade_fonte",
          "flag_localidade_divergente", "cep",
          "endereco_completo", "endereco_parse_metodo", "endereco_nao_parseado",
          "telefone", "site", "email", "instagram", "marca",
          "confianca", "confianca_classe", "status", "data_atualizacao",
          "n_registros_fundidos", "dedup_motivos", "nucleo_discriminante"] + TERR

OBSERVACAO = ["observation_id", "cluster_id", "id_fonte", "fonte", "snapshot_id",
              "observed_at", "ancora", "lat", "lon", "precisao_coord_m", "COD_MUNICIPIO"]

REJEITADOS = ["id_fonte", "fonte", "nome", "lat", "lon", "etapa", "motivo", "valor"]


def _chave(s):
    """Comparacao de nome de lugar sem acento/caixa/pontuacao."""
    if s is None or (isinstance(s, float) and s != s):
        return ""
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(t.lower().replace("-", " ").split())


def _params_dedup(cfg):
    return dict(raio_nome_m=float(cfg.dedup_raio_m), sim_min=float(cfg.dedup_sim_min),
                sim_cross=float(cfg.dedup_sim_cross), jaccard_min=float(cfg.dedup_jaccard_min),
                diam_max_m=float(cfg.dedup_diam_max_m), ctx_raio_m=float(cfg.dedup_ctx_raio_m),
                ctx_min=int(cfg.dedup_ctx_min), semnome_modo=cfg.dedup_semnome_modo)


def normalizar(cfg, man):
    man.iniciar("normalize")
    d = cfg.dir_proc("normalize")
    rej_dir = d
    kept = pd.read_parquet(os.path.join(cfg.dir_proc("territory"), "kept.parquet"))
    nb = max(1, (len(kept) + cfg.treat_batch - 1) // cfg.treat_batch)
    t0 = time.time()
    for b in range(nb):
        outp = os.path.join(d, "n_%05d.parquet" % b)
        if os.path.exists(outp):
            continue
        if cfg.budget_s and time.time() - t0 > cfg.budget_s:
            man.parcial("normalize", lotes_feitos=len(glob.glob(os.path.join(d, "n_*.parquet"))),
                        lotes_total=nb)
            print("NORMALIZE: parcial (budget) — reexecute")
            return None
        ch = kept.iloc[b * cfg.treat_batch:(b + 1) * cfg.treat_batch].copy()
        estreito = ch[[c for c in ep.COMUNS if c in ch.columns]].reset_index(drop=True)
        for c in ep.COMUNS:
            if c not in estreito.columns:
                estreito[c] = None
        rej = []
        tr = tp.tratar(estreito[ep.COMUNS], min_conf=cfg.min_conf, dedup=False,
                       rejeitados=rej).reset_index(drop=True)
        terr = ch[["id_fonte"] + [c for c in TERR if c in ch.columns]].drop_duplicates("id_fonte")
        tr = tr.merge(terr, on="id_fonte", how="left")
        # `localidade_fonte` divergente do municipio da GEOMETRIA e endereco brigando
        # com a posicao — sinal de graca, que a v2 jogava no campo errado.
        if "NOME_MUNICIPIO" in tr.columns:
            lf = tr["localidade_fonte"].map(_chave)
            mun = tr["NOME_MUNICIPIO"].map(_chave)
            tr["flag_localidade_divergente"] = (lf != "") & (mun != "") & (lf != mun)
        else:
            tr["flag_localidade_divergente"] = False
        salvar_atomico(tr, outp)
        salvar_atomico(pd.DataFrame(rej, columns=REJEITADOS) if rej
                       else pd.DataFrame(columns=REJEITADOS),
                       os.path.join(rej_dir, "r_%05d.parquet" % b))

    partes = sorted(glob.glob(os.path.join(d, "n_*.parquet")))
    if len(partes) != nb:
        man.parcial("normalize", lotes_feitos=len(partes), lotes_total=nb)
        raise RuntimeError("normalize incompleto: %d/%d lotes" % (len(partes), nb))
    n = sum(int(pd.read_parquet(p, columns=["id_fonte"]).shape[0]) for p in partes)
    man.funil("normalize.min_conf", len(kept), n,
              "confianca Overture < %.2f (descarte por regra, gravado em rejeitados)"
              % cfg.min_conf)
    man.concluir("normalize", entrada=len(kept), saida=n, lotes=nb)
    print("NORMALIZE: %d -> %d (min_conf=%.2f, %d lotes)" % (len(kept), n, cfg.min_conf, nb))
    return n


ARESTA = ["peso", "chave_a", "chave_b", "motivo", "dist_m", "tol_m"]
VINCULO = ["chave_a", "chave_b", "motivo", "dist_m", "score", "aceito"]


def _carregar_normalizado_ordenado(cfg):
    partes = sorted(glob.glob(os.path.join(cfg.dir_proc("normalize"), "n_*.parquet")))
    df = pd.concat([pd.read_parquet(p) for p in partes], ignore_index=True)
    df["COD_MUNICIPIO"] = df["COD_MUNICIPIO"].astype(str).str.replace(r"\.0$", "", regex=True)
    # ordem estável e independente do lote: mesma base -> mesmos índices internos
    return df.sort_values(["fonte", "id_fonte"], kind="stable").reset_index(drop=True)


def deduplicar(cfg, man):
    """Dedup com blocking por GRADE + halo e consolidação GLOBAL.

    Até a v3.1.0 a partição era o MUNICÍPIO. Isso é eficiente, mas cria uma parede:
    o mesmo estabelecimento visto por duas fontes a 2 m e 3 m de lados opostos da
    divisa nunca entrava no mesmo universo de matching. Aqui a grade decide apenas
    quais PARES são avaliados; o cluster fecha uma vez só, no global, e o município
    passa a ser ATRIBUTO da entidade — herdado da âncora — e não fronteira do
    matching. `--dedup-celula-m 0` volta ao comportamento por município.
    """
    man.iniciar("dedup")
    d = cfg.dir_proc("dedup")
    dvin = cfg.dir_proc("dedup", "vinculos")
    df = _carregar_normalizado_ordenado(cfg)
    par = _params_dedup(cfg)
    if cfg.dedup_modo != "evidencia" or float(cfg.dedup_celula_m) <= 0:
        return _dedup_por_municipio(cfg, man, df, par, d, dvin)

    dv = tp._dv()
    ctx = dv.preparar(df, {**dv.PARAMS, **par})
    halo = float(cfg.dedup_halo_m) or max(float(par.get("raio_forte_m", 200.0)), 250.0)
    blocos = list(dv.blocos_grade(ctx, float(cfg.dedup_celula_m), halo))
    dir_a = cfg.dir_proc("dedup", "arestas")
    t0 = time.time()
    for (gx, gy), _nucleo, bloco in blocos:
        outp = os.path.join(dir_a, "a_%d_%d.parquet" % (gx, gy))
        if os.path.exists(outp):
            continue
        if cfg.budget_s and time.time() - t0 > cfg.budget_s:
            man.parcial("dedup", blocos_feitos=len(glob.glob(os.path.join(dir_a, "a_*.parquet"))),
                        blocos_total=len(blocos))
            print("DEDUP: parcial (budget) — reexecute")
            return None
        ar, vi = dv.arestas_do_bloco(ctx, bloco, {**dv.PARAMS, **par})
        ch = df["fonte"].astype(str) + ":" + df["id_fonte"].astype(str)
        ch = ch.to_numpy()
        salvar_atomico(pd.DataFrame(
            [(ch[i], ch[j], m, round(dd, 2), round(float(sc), 1), bool(ac))
             for i, j, m, dd, sc, ac in vi], columns=VINCULO),
            os.path.join(dir_a, "w_%d_%d.parquet" % (gx, gy)))
        salvar_atomico(pd.DataFrame(
            [(pe, ch[i], ch[j], m, round(dd, 2), float(to))
             for pe, i, j, m, dd, to in ar], columns=ARESTA), outp)

    feitos = sorted(glob.glob(os.path.join(dir_a, "a_*.parquet")))
    if len(feitos) != len(blocos):
        man.parcial("dedup", blocos_feitos=len(feitos), blocos_total=len(blocos))
        raise RuntimeError("dedup incompleto: %d/%d blocos" % (len(feitos), len(blocos)))

    pos = {c: i for i, c in enumerate(df["fonte"].astype(str) + ":" + df["id_fonte"].astype(str))}
    arestas, vinc = [], []
    for p_ in feitos:
        a = pd.read_parquet(p_)
        arestas += [(float(r.peso), pos[r.chave_a], pos[r.chave_b], r.motivo,
                     float(r.dist_m), float(r.tol_m)) for r in a.itertuples(index=False)]
    for p_ in sorted(glob.glob(os.path.join(dir_a, "w_*.parquet"))):
        w = pd.read_parquet(p_)
        vinc += [(pos[r.chave_a], pos[r.chave_b], r.motivo, float(r.dist_m),
                  float(r.score), bool(r.aceito)) for r in w.itertuples(index=False)]

    ded, dfv, obs = dv.consolidar(ctx, arestas, vinc, {**dv.PARAMS, **par})
    obs = _identificar_observacoes(obs, man)
    salvar_atomico(dfv, os.path.join(dvin, "v_global.parquet"))
    salvar_atomico(obs, os.path.join(cfg.dir_proc("dedup", "observacoes"), "obs.parquet"))
    # município da ENTIDADE = município da âncora; particiona a saída, não o matching
    ded["COD_MUNICIPIO"] = ded["COD_MUNICIPIO"].astype(str).str.replace(r"\.0$", "", regex=True)
    for cod, g in ded.groupby("COD_MUNICIPIO", sort=True):
        salvar_atomico(g.reset_index(drop=True), os.path.join(d, "d_%s.parquet" % cod))

    n = len(ded)
    cruzam = _clusters_entre_municipios(obs)
    man.funil("dedup.%s" % cfg.dedup_modo, len(df), n,
              "registro fundido em duplicata (raio=%dm, sim=%d/%d, jaccard=%.2f, diam=%dm, "
              "celula=%dm, halo=%dm)"
              % (cfg.dedup_raio_m, cfg.dedup_sim_min, cfg.dedup_sim_cross,
                 cfg.dedup_jaccard_min, cfg.dedup_diam_max_m, cfg.dedup_celula_m, halo))
    man.concluir("dedup", entrada=len(df), saida=n, blocos=len(blocos), modo=cfg.dedup_modo,
                 clusters_entre_municipios=cruzam, telefones_hub=ctx["hubs"])
    print("DEDUP: %d -> %d | %d blocos de %dm (halo %dm) | %d cluster(s) cruzando divisa"
          % (len(df), n, len(blocos), cfg.dedup_celula_m, halo, cruzam))
    return n


def _identificar_observacoes(obs, man):
    """`fonte + id_fonte` identifica o OBJETO da fonte; `+ snapshot` identifica a
    OBSERVACAO. `OSM node/123` em janeiro e em agosto nao fizeram necessariamente a
    mesma afirmacao — sem o snapshot na chave não há como manter histórico."""
    if not len(obs):
        return obs
    snap = obs["fonte"].astype(str).map(lambda f: man.snapshot(f).get("snapshot_id")
                                        or "indeterminado")
    vagas = sorted(set(obs.loc[snap == "indeterminado", "fonte"].astype(str)))
    if vagas:
        # `observation_id = f(fonte, id_fonte, snapshot_id)`. Com snapshot indeterminado
        # a observacao nao e historica — e um registro sem epoca. Nao entra na base.
        raise RuntimeError(
            "observacao sob snapshot indeterminado nas fontes %s. Resolva a versao da "
            "fonte (--source-mode latest) antes de gerar a entrega." % ", ".join(vagas))
    obs = obs.copy()
    obs["snapshot_id"] = snap
    # `observed_at` e o `retrieved_at` DAQUELA fonte, nao o nascimento do workspace
    obs["observed_at"] = obs["fonte"].astype(str).map(man.retrieved_at)
    obs["observation_id"] = [
        hashlib.sha256(("%s|%s|%s" % (f, i, s)).encode("utf-8")).hexdigest()[:16]
        for f, i, s in zip(obs["fonte"].astype(str), obs["id_fonte"].astype(str), snap)]
    return obs


def _clusters_entre_municipios(obs):
    """Quantas entidades reúnem observações de municípios diferentes — exatamente o
    que a partição por município tornava impossível."""
    if not len(obs) or "COD_MUNICIPIO" not in obs.columns:
        return 0
    g = obs.dropna(subset=["COD_MUNICIPIO"]).groupby("cluster_id")["COD_MUNICIPIO"].nunique()
    return int((g > 1).sum())


def _dedup_por_municipio(cfg, man, df, par, d, dvin):
    """Caminho v3.1: partição por município (sem halo). Mantido para `--dedup legado`,
    `exato`, `none` e para `--dedup-celula-m 0`."""
    df = df.sort_values(["COD_MUNICIPIO", "fonte", "id_fonte"], kind="stable")
    cods = sorted(c for c in df["COD_MUNICIPIO"].dropna().unique() if c and c != "nan")
    t0 = time.time()
    for cod in cods:
        outp = os.path.join(d, "d_%s.parquet" % cod)
        if os.path.exists(outp):
            continue
        if cfg.budget_s and time.time() - t0 > cfg.budget_s:
            man.parcial("dedup", municipios_feitos=len(glob.glob(os.path.join(d, "d_*.parquet"))),
                        municipios_total=len(cods))
            print("DEDUP: parcial (budget) — reexecute")
            return None
        g = df[df["COD_MUNICIPIO"] == cod]
        ded, vin = tp.dedup_pois_auditado(g, cfg.dedup_modo, **par)
        salvar_atomico(pd.DataFrame(vin), os.path.join(dvin, "v_%s.parquet" % cod))
        salvar_atomico(ded, outp)

    feitos = sorted(glob.glob(os.path.join(d, "d_*.parquet")))
    if len(feitos) != len(cods):
        man.parcial("dedup", municipios_feitos=len(feitos), municipios_total=len(cods))
        raise RuntimeError("dedup incompleto: %d/%d municipios" % (len(feitos), len(cods)))
    n = sum(int(pd.read_parquet(p, columns=["id_fonte"]).shape[0]) for p in feitos)
    man.funil("dedup.%s" % cfg.dedup_modo, len(df), n,
              "registro fundido em duplicata (particao por municipio)")
    man.concluir("dedup", entrada=len(df), saida=n, municipios=len(cods), modo=cfg.dedup_modo)
    print("DEDUP: %d -> %d em %d municipios (modo=%s, particao por municipio)"
          % (len(df), n, len(cods), cfg.dedup_modo))
    return n


def carregar_observacoes(cfg):
    """Elo OBSERVAÇÃO -> ENTIDADE. Uma linha por registro de fonte que entrou no
    dedup, com o `cluster_id` da entidade e quem é a âncora. É o que separa
    "registro sobrevivente de uma fusão" de "entidade sustentada por N evidências"
    sem perder nenhuma observação pelo caminho."""
    p = os.path.join(cfg.dir_proc("dedup", "observacoes"), "obs.parquet")
    if os.path.exists(p):
        d = pd.read_parquet(p)
        for c in OBSERVACAO:
            if c not in d.columns:
                d[c] = None
        return d[OBSERVACAO]
    return pd.DataFrame(columns=OBSERVACAO)


def carregar_vinculos(cfg):
    partes = sorted(glob.glob(os.path.join(cfg.dir_proc("dedup", "vinculos"), "v_*.parquet")))
    frames = [pd.read_parquet(p) for p in partes]
    frames = [f for f in frames if len(f)]
    return (pd.concat(frames, ignore_index=True) if frames else
            pd.DataFrame(columns=["id_a", "id_b", "motivo", "dist_m", "score", "aceito"]))


def carregar_rejeitados(cfg):
    partes = (sorted(glob.glob(os.path.join(cfg.dir_proc("raw"), "r_*.parquet")))
              + sorted(glob.glob(os.path.join(cfg.dir_proc("territory"), "r_*.parquet")))
              + sorted(glob.glob(os.path.join(cfg.dir_proc("normalize"), "r_*.parquet"))))
    frames = [pd.read_parquet(p) for p in partes]
    frames = [f for f in frames if len(f)]
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=REJEITADOS))


def carregar_normalizado(cfg):
    """Base ANTES do dedup — necessaria para auditar o que a fusao consolidou."""
    partes = sorted(glob.glob(os.path.join(cfg.dir_proc("normalize"), "n_*.parquet")))
    return pd.concat([pd.read_parquet(p) for p in partes], ignore_index=True)


def carregar_padronizado(cfg):
    partes = sorted(glob.glob(os.path.join(cfg.dir_proc("dedup"), "d_*.parquet")))
    if not partes:
        raise RuntimeError("nenhuma parte de dedup — rode a etapa dedup")
    df = pd.concat([pd.read_parquet(p) for p in partes], ignore_index=True)
    for c in PADRAO:
        if c not in df.columns:
            df[c] = None
    return df[PADRAO]
