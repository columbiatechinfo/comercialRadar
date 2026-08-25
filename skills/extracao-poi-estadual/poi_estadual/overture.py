# -*- coding: utf-8 -*-
"""Overture Places por tiles do bbox da UF.

Regras herdadas da v1 (validadas em producao) e mantidas:
  - o tile e baixado UMA vez; se vier denso, fatia-se o resultado POR LINHAS.
    Re-baixar quadrantes (o caminho antigo do `rs_driver`) multiplica o download.
  - marcador `DONE` por tile em arquivo proprio: detectar conclusao por prefixo de
    nome de parte faz um tile fatiado parcialmente parecer completo e perde dados.

Correcao v2: o geoparquet baixado e lido em BATCHES (pyarrow), nao inteiro em
pandas. Na v1 um tile muito denso estourava a memoria antes de chegar ao
fatiamento — o corte acontecia tarde demais.
"""
import glob
import json
import os
import subprocess
import tempfile
import time

import pandas as pd
import pyarrow.parquet as pq
import shapely

from .config import salvar_atomico
from .vendor import extrair_pois as ep


def _base(cfg, man):
    col, wk = man.colecao("overture")
    return cfg.dir_colecao("overture", col or "sem_colecao", wk or "sem_work")


def _partes(cfg, sig):
    return cfg.dir("fontes", "overture", "collections", *sig.split("|"), "parts")


def _marcador(cfg, sig, idx):
    return os.path.join(cfg.dir("fontes", "overture", "collections", *sig.split("|"), "done"),
                        "%s.ok" % idx)


def _versao_cli():
    try:
        r = subprocess.run(["overturemaps", "--version"], capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip()[:80]
    except (OSError, subprocess.SubprocessError):
        return "desconhecida"


def _hierarquia(r):
    """Hierarquia de categoria do Overture, em ';', do topo para a folha.

    A release muda o nome do campo (`taxonomy.hierarchy`, `categories.alternate`,
    `basic_category`); aqui todas as formas conhecidas caem na mesma coluna
    harmonizada `categoria_hier`, e o que não existir vira None."""
    for chave, sub in (("taxonomy", "hierarchy"), ("categories", "hierarchy"),
                       ("categories", "alternate")):
        v = ep._g(r.get(chave), sub) if r.get(chave) is not None else None
        if v is None:
            continue
        if isinstance(v, str):
            return v or None
        try:
            partes = [str(x) for x in list(v) if x is not None]
        except TypeError:
            continue
        if partes:
            return ";".join(partes)
    b = r.get("basic_category")
    return str(b) if (b is not None and str(b) not in ("", "nan", "None")) else None


def _linhas(tab):
    """Converte um RecordBatch/Table Overture para o esquema COMUNS + nativas ov.*"""
    g = tab.to_pandas()
    if not len(g):
        return pd.DataFrame(columns=ep.COMUNS)
    geom = shapely.from_wkb(g["geometry"].values)
    cols = [c for c in g.columns if c != "geometry"]
    rows = []
    for i, r in g.reset_index(drop=True).iterrows():
        a0 = ep._first(r.get("addresses"))
        comum = dict(
            fonte="overture", id_fonte=r.get("id"), nome=ep._g(r.get("names"), "primary"),
            lat=float(shapely.get_y(geom[i])), lon=float(shapely.get_x(geom[i])),
            categoria_orig=ep._g(r.get("categories"), "primary"),
            categoria_hier=_hierarquia(r),
            endereco_raw=ep._g(a0, "freeform"),
            # `locality` do Overture é o MUNICÍPIO em ~96% das linhas (medido em
            # Canoas e Santa Maria), não o bairro. Vai para o seu próprio campo.
            bairro=None, localidade_fonte=ep._g(a0, "locality"),
            cep=ep._g(a0, "postcode"), telefone=ep._first(r.get("phones")),
            site=ep._first(r.get("websites")), email=ep._first(r.get("emails")),
            instagram=ep._first(r.get("socials")),
            marca=ep._g(r.get("brand"), "names", "primary"),
            confianca=float(r["confidence"]) if pd.notna(r.get("confidence")) else None,
            status=r.get("operating_status"), data_atualizacao=ep._py(r.get("update_time")))
        rows.append({**comum, **ep._native(r, cols, "ov")})
    return pd.DataFrame(rows)


def _cmd(cfg, release, bbox, saida):
    """Comando de download. Com release resolvida, ela vai EXPLICITA no comando —
    senao a proveniencia diz `2026-08-19.1` e a materializacao pede so "Places"."""
    W, S, E, N = bbox
    cmd = ["overturemaps", "download", "--bbox=%s,%s,%s,%s" % (W, S, E, N),
           "-f", "geoparquet", "--type=place", "-o", saida]
    if release:
        cmd += ["--release", release]
    elif _SEM_RELEASE["ativo"]:
        # Queda deliberada do patch local (ver `_tile`): o CLI recusou a release
        # e estamos baixando pelo caminho padrao de proposito.
        pass
    elif cfg.source_mode == "pinned":
        raise RuntimeError(
            "--source-mode pinned: release do Overture indeterminada. Sem amarrar o "
            "download a uma release, a materializacao nao corresponde a proveniencia. "
            "Defina OVERTURE_RELEASE ou rode com --source-mode latest.")
    return cmd


# PATCH LOCAL — 25/08/2026, comercialRadar. NAO E DA SKILL DE ORIGEM.
#
# O `--release` do CLI do Overture esta QUEBRADO, e ele se contradiz sozinho:
#
#     overturemaps releases latest        ->  2026-08-19.0
#     overturemaps releases exists 2026-08-19.0
#                                         ->  Error: Release ... not found
#     overturemaps download --release 2026-08-19.0
#                                         ->  Error: Release '2026-08-19.0' is no
#                                             longer available. Overture keeps only
#                                             the last two monthly releases
#     overturemaps download               ->  OK, 835.648 bytes
#
# Testado com as duas releases que existem: as duas recusadas com `--release`,
# e o caminho PADRAO funcionando. Nao e a nossa escolha de versao — e o
# subsistema de release do CLI, que erra ate contra o que ele proprio declara.
#
# Isto derrubou uma rodada inteira: 56 tiles de 56 com erro, em toda UF.
#
# A SAIDA, e por que ela nao mente sobre a proveniencia: tenta COM a release e,
# se o CLI a recusar, repete SEM ela — registrando o fato. A identidade
# declarada continua sendo `2026-08-19.0`, que e o que o proprio
# `releases latest` diz que o download padrao busca. Declarar isso e honesto;
# o que seria desonesto e declarar uma release que nao foi consultada.
_RECUSA = ("no longer available", "not found", "is not available")
# `cfg` e um dataclass CONGELADO — nao aceita atributo novo
# (`FrozenInstanceError`). Por isso o sinal vive no modulo, e nao nele.
_SEM_RELEASE = {"avisado": False, "ativo": False}


def _cli_recusou(err: bytes) -> bool:
    t = (err or b"").decode("utf-8", "replace").lower()
    return any(m in t for m in _RECUSA)


def _tile(cfg, sig, bbox, idx, release=None):
    tmp = tempfile.mktemp(suffix=".geoparquet")
    try:
        r = subprocess.run(_cmd(cfg, release, bbox, tmp), capture_output=True)
        if r.returncode != 0 and release and _cli_recusou(r.stderr + r.stdout):
            if not _SEM_RELEASE["avisado"]:
                print("  OVERTURE: o CLI recusou --release %s; baixando pelo caminho "
                      "padrao (releases latest concorda com esta versao)." % release,
                      flush=True)
                _SEM_RELEASE["avisado"] = True
            try:
                _SEM_RELEASE["ativo"] = True
                r = subprocess.run(_cmd(cfg, None, bbox, tmp), capture_output=True)
            finally:
                _SEM_RELEASE["ativo"] = False
        if r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, "overturemaps download",
                                                r.stdout, r.stderr)
        pf = pq.ParquetFile(tmp)
        if pf.metadata.num_rows == 0:
            salvar_atomico(pd.DataFrame(columns=ep.COMUNS),
                           os.path.join(_partes(cfg, sig), "ov_%s.parquet" % idx))
            return 0
        n = 0
        buf, nbuf, parte = [], 0, 0
        for batch in pf.iter_batches(batch_size=min(cfg.ov_cap, 20000)):
            df = _linhas(batch)
            if not len(df):
                continue
            buf.append(df)
            nbuf += len(df)
            n += len(df)
            if nbuf >= cfg.ov_cap:
                salvar_atomico(pd.concat(buf, ignore_index=True),
                               os.path.join(_partes(cfg, sig), "ov_%s_%d.parquet" % (idx, parte)))
                buf, nbuf, parte = [], 0, parte + 1
        if buf:
            salvar_atomico(pd.concat(buf, ignore_index=True),
                           os.path.join(_partes(cfg, sig), "ov_%s_%d.parquet" % (idx, parte)))
        elif n == 0:
            salvar_atomico(pd.DataFrame(columns=ep.COMUNS),
                           os.path.join(_partes(cfg, sig), "ov_%s.parquet" % idx))
        return n
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def executar(cfg, man):
    """Resumivel: so processa tile sem marcador. Time-box opcional via --budget."""
    man.iniciar("fetch")
    col, wk = man.colecao("overture")
    sig = "%s|%s" % (col or "sem_colecao", wk or "sem_work")
    with open(os.path.join(_base(cfg, man), "tiles.json"), encoding="utf-8") as fh:
        grade = json.load(fh)
    snap = man.snapshot("overture")
    release = snap.get("source_version")
    man.versao_fonte("overture", adapter_cli=_versao_cli(), tiles=len(grade),
                     collection_id=col, work_id=wk,
                     release=snap.get("source_version"), snapshot_id=snap.get("snapshot_id"))

    t0 = time.time()
    feitos = erros = pontos = 0
    for idx, bbox in enumerate(grade):
        if os.path.exists(_marcador(cfg, sig, idx)):
            continue
        if cfg.budget_s and time.time() - t0 > cfg.budget_s:
            break
        try:
            pontos += _tile(cfg, sig, bbox, str(idx), release)
            open(_marcador(cfg, sig, idx), "w").close()
            feitos += 1
        except subprocess.CalledProcessError as e:
            erros += 1
            print("  tile %d ERRO: %s" % (idx, (e.stderr or b"")[:200]))
    restam = sum(1 for i in range(len(grade)) if not os.path.exists(_marcador(cfg, sig, i)))
    partes = len(glob.glob(os.path.join(_partes(cfg, sig), "ov_*.parquet")))
    print("OVERTURE: +%d tiles | restam %d/%d | partes=%d | erros=%d"
          % (feitos, restam, len(grade), partes, erros))
    return {"tiles_feitos": feitos, "tiles_restantes": restam, "partes": partes,
            "erros": erros, "pontos": pontos, "completo": restam == 0}
