# -*- coding: utf-8 -*-
"""Tratamento barato do passo 1 (`normalize`), em lotes retomaveis.

Faz: Title Case no nome, telefone, classe de confianca, sinais de precisao da
coordenada, separacao entre `bairro` e `localidade_fonte`, e o CEP. Guarda o
endereco COMO A FONTE ESCREVEU e a categoria como ela veio. `--min-conf` filtra
Overture de baixa confianca — DESCARTE POR REGRA DE NEGOCIO, contabilizado no
funil e gravado linha a linha em `rejeitados_normalize.parquet`.

O QUE SAIU DAQUI EM 01/09/2026, e por que.

A fase `dedup` fundia por evidencia com blocking em grade, no estado inteiro.
Media na corrida do RS: 25 dos 32 minutos do passo, 41.716.356 pares avaliados,
1,5 GB so de tabela de vinculos — para fundir 8,7% (1.065.064 -> 972.728). E o
municipio que se ia usar em seguida tinha 27.527 linhas: 2,8% do estado.

O parser de endereco e a traducao de categoria seguiram junto, pela mesma razao
e com o mesmo destino: a etapa da AREA. La o trabalho custa uma fracao e e
melhor informado — fundir com o POI do Google ja na mao vale mais que fundir
tres fontes as cegas.

O passo 1 ficou com o que e barato e serve de filtro: coordenada, CEP e o
endereco da fonte. As funcoes pesadas continuam existindo em `vendor` —
`parse_endereco`, `traduzir`, `dedup_pois_auditado` — e quem as chama agora e a
etapa da area.
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

# O CONTRATO DE SAIDA DO PASSO 1, depois de 01/09/2026.
#
# Sairam as colunas que so o trabalho pesado preenchia: `categoria_pt` e
# `segmento` (traducao), `logradouro`/`numero`/`quadra`/`lote` e o metodo de
# parse (parser de endereco), e `n_registros_fundidos`/`dedup_motivos`/
# `nucleo_discriminante` (fusao). Quem as produz agora e a etapa da area.
#
# `endereco_completo` continua, mas E O ENDERECO DA FONTE, sem remontagem.
# `cep` continua, porque e filtro de municipio errado e sai de uma regex.
# `cluster_id` continua e vale `fonte:id_fonte` — identidade da linha, que sem
# fusao e o proprio registro. A etapa 2 o usa como place_id e nao muda.
PADRAO = ["cluster_id", "id_fonte", "fonte", "fontes", "nome", "sem_nome",
          "categoria_orig", "categoria_hier",
          "lat", "lon", "precisao_coord_m", "coord_empilhada",
          "bairro", "localidade_fonte", "flag_localidade_divergente", "cep",
          "endereco_completo",
          "telefone", "site", "email", "instagram", "marca",
          "confianca", "confianca_classe", "status", "data_atualizacao"] + TERR

REJEITADOS = ["id_fonte", "fonte", "nome", "lat", "lon", "etapa", "motivo", "valor"]


def _chave(s):
    """Comparacao de nome de lugar sem acento/caixa/pontuacao."""
    if s is None or (isinstance(s, float) and s != s):
        return ""
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(t.lower().replace("-", " ").split())


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
        tr = tp.tratar(estreito[ep.COMUNS], min_conf=cfg.min_conf,
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


def carregar_rejeitados(cfg):
    partes = (sorted(glob.glob(os.path.join(cfg.dir_proc("raw"), "r_*.parquet")))
              + sorted(glob.glob(os.path.join(cfg.dir_proc("territory"), "r_*.parquet")))
              + sorted(glob.glob(os.path.join(cfg.dir_proc("normalize"), "r_*.parquet"))))
    frames = [pd.read_parquet(p) for p in partes]
    frames = [f for f in frames if len(f)]
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=REJEITADOS))


def carregar_padronizado(cfg):
    """O entregavel do passo 1 sai do `normalize` — nao ha mais fase de fusao."""
    partes = sorted(glob.glob(os.path.join(cfg.dir_proc("normalize"), "n_*.parquet")))
    if not partes:
        raise RuntimeError("nenhuma parte de normalize — rode a etapa normalize")
    df = pd.concat([pd.read_parquet(p) for p in partes], ignore_index=True)
    for c in PADRAO:
        if c not in df.columns:
            df[c] = None
    return df[PADRAO]
