# -*- coding: utf-8 -*-
"""Validacao de entrega: schema, funil, espacial e conformidade do manifesto.

A v1.0.0 nao tinha validacao — a execucao terminava sem prova de que o resultado
fecha. Aqui a etapa `validate` reprova a entrega se qualquer verificacao falhar, e
grava `relatorio_qualidade.json` com o resultado item a item.

Numero reportado = numero auditavel: as contagens do relatorio saem do proprio
arquivo entregue, nao das variaveis do processo.
"""
import json
import os

import geopandas as gpd
import pandas as pd

from .normalizacao import PADRAO, carregar_padronizado, carregar_rejeitados
from .vendor import sha_vendor
from .vendor import tratar_pois as tp


def _chk(nome, ok, detalhe=""):
    return {"verificacao": nome, "resultado": "OK" if ok else "FALHA", "detalhe": str(detalhe)}


def _do_arquivo(cfg):
    """Le o padronizado ENTREGUE (nao o dataframe em memoria)."""
    p = cfg.arq_saida("poi_padronizado_%s.parquet" % cfg.rotulo.lower())
    if os.path.exists(p):
        g = gpd.read_parquet(p)
        return pd.DataFrame(g.drop(columns=[c for c in ["geometry"] if c in g.columns]))
    p = cfg.arq_saida("poi_padronizado_%s.csv" % cfg.rotulo.lower())
    if os.path.exists(p):
        return pd.read_csv(p, low_memory=False)
    raise RuntimeError("padronizado nao encontrado em %s" % cfg.saida)


def executar(cfg, man, alvo):
    man.iniciar("validate")
    df = _do_arquivo(cfg)
    checagens = []

    # ---- schema
    faltando = [c for c in PADRAO if c not in df.columns]
    checagens.append(_chk("schema: colunas do padronizado (PADRAO)", not faltando,
                          "faltando: %s" % faltando if faltando else "%d colunas" % len(df.columns)))

    # ---- coordenada
    lat = pd.to_numeric(df["lat"], errors="coerce")
    lon = pd.to_numeric(df["lon"], errors="coerce")
    nulas = int(lat.isna().sum() + lon.isna().sum())
    checagens.append(_chk("coordenada: sem nula no entregavel", nulas == 0, "nulas=%d" % nulas))
    W, S, E, N = (float(x) for x in alvo.total_bounds)
    fora = int((~((lon >= W) & (lon <= E) & (lat >= S) & (lat <= N))).sum())
    checagens.append(_chk("espacial: todo ponto dentro do bbox do alvo", fora == 0,
                          "fora=%d | bbox=%s" % (fora, [round(W, 3), round(S, 3),
                                                        round(E, 3), round(N, 3)])))

    # ---- territorio
    cods_alvo = set(alvo["COD_MUNICIPIO"].astype(str))
    cods_df = set(df["COD_MUNICIPIO"].dropna().astype(str))
    intrusos = sorted(cods_df - cods_alvo)
    checagens.append(_chk("territorio: nenhum municipio fora do alvo", not intrusos,
                          "intrusos=%s" % intrusos[:5]))
    excl = sorted(cods_df & set(cfg.excluir))
    checagens.append(_chk("territorio: nenhum municipio excluido presente", not excl,
                          "presentes=%s" % excl))

    # ---- unicidade
    # a chave do sistema e (fonte, id_fonte) — `fonte:id_fonte` e o que vira
    # `cluster_id`. Conferir so `id_fonte` dependeria de OSM e FSQ nunca colidirem.
    dup = int(df[["fonte", "id_fonte"]].astype(str).duplicated().sum())
    checagens.append(_chk("unicidade: (fonte, id_fonte) sem duplicata", dup == 0,
                          "duplicados=%d" % dup))
    dupc = int(df["cluster_id"].astype(str).duplicated().sum()) if "cluster_id" in df.columns else 0
    checagens.append(_chk("unicidade: cluster_id sem duplicata no entregavel", dupc == 0,
                          "duplicados=%d" % dupc))

    # ---- confianca
    conf = pd.to_numeric(df["confianca"], errors="coerce")
    abaixo = int((conf < cfg.min_conf).sum())
    checagens.append(_chk("regra: nenhum registro abaixo de min_conf", abaixo == 0,
                          "min_conf=%.2f | abaixo=%d" % (cfg.min_conf, abaixo)))

    # As checagens de fusao sairam em 01/09/2026 junto com a propria fusao.
    # Eram tres — fusao suspeita, elo observacao->entidade e a taxa — e todas
    # perguntavam sobre um passo que o estado nao executa mais. Voltam na etapa
    # da area, onde a fusao passou a acontecer, e la elas continuam valendo:
    # foi por elas que se mediram 42 e 66 fusoes indevidas em Canoas e Santa
    # Maria.

    # ---- auditoria dos descartes
    rej = carregar_rejeitados(cfg)
    # SO O FUNIL DESTA CORRIDA. O manifesto acumula entre execucoes — seis
    # corridas do RS deixaram 46 entradas, com `raw.bbox` repetido tres vezes.
    # Somando tudo davam 1.105.491 descartes contra 279.800 rejeitados, e a
    # checagem reprovava por contabilidade, nao por descarte perdido.
    inicio = str(man.d.get("started_at") or "")
    do_run = [f for f in man.d["funil"] if str(f.get("em") or "") >= inicio]
    descartados = sum(f["descartados"] for f in do_run)
    checagens.append(_chk("auditoria: todo descarte tem linha em rejeitados",
                          len(rej) >= descartados,
                          "rejeitados=%d | descartados nesta corrida=%d "
                          "(%d entradas de funil, de %d no manifesto)"
                          % (len(rej), descartados, len(do_run),
                             len(man.d["funil"]))))

    # ---- funil
    ruins = man.funil_fecha()
    checagens.append(_chk("funil: entrada = saida + descartados em toda etapa", not ruins,
                          "divergencias=%d" % len(ruins)))

    # ---- manifesto
    etapas_ok = [e for e in ("init", "fetch", "raw", "territory", "normalize", "export")
                 if man.reutilizavel(e)]
    faltam = [e for e in ("init", "raw", "territory", "normalize", "export")
              if e not in etapas_ok]
    checagens.append(_chk("manifesto: etapas concluidas com o hash da config atual", not faltam,
                          "pendentes/obsoletas: %s" % faltam))
    versoes = man.d.get("fontes_versao", {})
    checagens.append(_chk("reprodutibilidade: versao de cada fonte registrada",
                          all(f in versoes for f in cfg.fontes),
                          "registradas=%s" % sorted(versoes)))

    falhas = [c for c in checagens if c["resultado"] == "FALHA"]
    rel = {
        "run_id": man.d["run_id"],
        "uf": cfg.uf,
        "config": cfg.campos_hash(),
        "hashes_etapa": cfg.hashes(),
        "fontes_versao": versoes,
        "vendor_sha": sha_vendor(),
        "rejeitados": len(rej),
        "linhas_entregues": len(df),
        "colunas_entregues": len(df.columns),
        "municipios": len(cods_df),
        "funil": man.d["funil"],
        "verificacoes": checagens,
        "total": len(checagens),
        "falhas": len(falhas),
        "resultado": "APROVADO" if not falhas else "REPROVADO",
    }
    p = cfg.arq_saida("relatorio_qualidade_%s.json" % cfg.rotulo.lower())
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(rel, fh, ensure_ascii=False, indent=2)
    os.replace(p + ".tmp", p)

    print("VALIDATE: %d/%d verificacoes OK -> %s"
          % (len(checagens) - len(falhas), len(checagens), rel["resultado"]))
    for c in falhas:
        print("  FALHA: %s | %s" % (c["verificacao"], c["detalhe"]))
    print("  -> %s" % p)

    if falhas:
        man.parcial("validate", falhas=len(falhas), total=len(checagens))
    else:
        man.concluir("validate", falhas=0, total=len(checagens), linhas=len(df))
    return rel
