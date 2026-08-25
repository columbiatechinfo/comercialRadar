# -*- coding: utf-8 -*-
"""
ajustar_logradouro.py — MARCA logradouro e ORGANIZA complemento em N bases
===========================================================================
Derivada enxuta de `ferramenta-logradouro-padrao`. Faz três coisas e só três:

  1. NORMALIZAÇÃO   base + hardening + vocabulário de tipo de via
  2. LÉXICO APRENDIDO   minera equivalências com prova (mesmo número + raio),
     promove por support, aplica as ATIVAS
  3. COMPLEMENTO ORGANIZADO   componentes tipados, string canônica, auditoria
     do ruído descartado

Contrato de saída — MARCAÇÃO, nunca sobrescrita: toda coluna de entrada volta
intacta; o que a skill produz entra em colunas novas com prefixo `aj_`. Quem
aplica a correção é o processo a jusante.

NÃO faz (por decisão de escopo): resolução de entidade, `imovel_id`, crosswalk,
correção por IA/CNEFE, segmentação de campo único grudado.

Uso:
    python ajustar_logradouro.py config.yaml [--out DIR]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import uuid
import shutil
import datetime as dt
import traceback
import contextlib
import hashlib
import socket
import platform
import importlib.metadata as importlib_metadata

import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import normalizacao_base as N          # noqa: E402
import numero as NU                    # noqa: E402
import validacao as V                  # noqa: E402
import lexico_io as LIO                # noqa: E402
import complemento_organizador as C    # noqa: E402
import pareamento_leve as P            # noqa: E402
import geo as G                        # noqa: E402
import similaridade as S               # noqa: E402
import lexico_primitivas as L           # noqa: E402
import lexico_seguro as LS             # noqa: E402
import complemento_organizador as CO   # noqa: E402

# [v3.1] Namespace interno. Antes o motor usava `gid`, `x`, `y`, `logr_pre` e
# `_src_*` DENTRO do mesmo DataFrame da entrada: base que já tivesse uma dessas
# colunas tinha o valor sobrescrito e depois removido pelo drop — o contrato de
# preservação caía sem erro. Agora nada interno colide, e `aj_*`/`__a2l_*` são
# namespaces RESERVADOS: se a entrada trouxer um deles, a execução aborta.
INTERNO = "__a2l_"
GID, XI, YI, LOGR_PRE = INTERNO + "gid", INTERNO + "x", INTERNO + "y", INTERNO + "logr_pre"

NAVY, TEAL, GOLD, INK = "#1B2A4A", "#1B9AAA", "#C9963B", "#0E1726"
ROW_A, ROW_B = "#FFFFFF", "#F4F7F9"
VERSION = "3.3.5"

DEFAULTS = {
    "raio_aprendizado_m": 30.0,
    "min_support_equiv": 2,
    "teto_bloco": 200,
    "apenas_entre_fontes": False,
    "modo_numero": "estrito",          # estrito: 279-A != 279-B != 279
    "abortar_em_alerta": False,
    "ratio_dominancia": 5.0,           # dominância mínima p/ decidir canônico
    "min_freq_dominancia": 3,
    "raio_independencia_m": 100.0,     # abaixo disso é a MESMA evidência física
    "max_linhas_xlsx": 50000,
    "reconciliacao_stale_s": 3600.0,
    "retencao_runs": 0,
    "xlsx": True,
    "parquet": True,
    "marcacao_por_fonte": True,
    "reparar_mojibake": False,
}
# taxonomia ÚNICA: a do organizador. A lista fixa anterior divergia da fonte e
# perdia ANDAR, TERREO, QUITINETE e SOBRELOJA na saída — componente reconhecido
# que nunca virava coluna.
COMPONENTES = list(CO.ORDEM)


def log(msg):
    print(msg, flush=True)


def por_valor_unico(serie: pd.Series, fn):
    """Aplica fn UMA vez por valor distinto e reprojeta na série.

    Normalizar linha a linha é o gargalo real: numa base municipal os
    logradouros distintos são ordens de grandeza menos que os registros
    (1,5M ligações ↔ ~50k logradouros). Memoizar por valor troca O(linhas)
    chamadas Python por O(distintos) + um map vetorizado, sem mudar o
    resultado (a função é pura)."""
    unicos = pd.unique(serie)
    tab = {u: fn(u) for u in unicos}
    return [tab[v] for v in serie]


# ---------------------------------------------------------------------------
# LOAD — preserva 100% das colunas originais de cada fonte
# ---------------------------------------------------------------------------
def carregar(cfg: dict) -> pd.DataFrame:
    base_dir = cfg["_base_dir"]
    frames = []
    for f in cfg["fontes"]:
        path = os.path.join(base_dir, f["arquivo"])
        df = pd.read_csv(path, dtype=str, encoding=f.get("encoding", "utf-8"),
                         keep_default_na=False)
        m = f["colunas"]
        df["aj_source_id"] = f["source_id"]
        df["aj_scope_id"] = str(cfg.get("scope_id", ""))
        nivel_aut = int(f.get("autoridade_nivel", 100 if f.get("autoridade", False) else 0) or 0)
        df["aj_autoridade_nivel"] = nivel_aut
        df["aj_autoridade"] = nivel_aut > 0
        df["aj_record_id"] = (df[m["record_id"]] if m.get("record_id") in df.columns
                              else pd.Series(range(len(df))).astype(str))
        for canon in ("logradouro", "numero", "complemento", "lat", "lon"):
            src = m.get(canon)
            df[f"{INTERNO}src_{canon}"] = df[src] if (src and src in df.columns) else ""
        frames.append(df)
        log(f"  [{f['source_id']:<10}] {len(df):>7} registros  <- {f['arquivo']}")
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
def _novo_run_id() -> str:
    agora = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{agora}-{uuid.uuid4().hex[:8]}"


def _json_atomico(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{time.time_ns()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _hash_cfg(cfg: dict) -> str:
    limpo = {k: v for k, v in cfg.items() if k not in ("_base_dir", "_config_path")}
    raw = json.dumps(limpo, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hash_code() -> str:
    """Hash do código/contrato carregado, independente do ZIP externo."""
    base = os.path.dirname(os.path.abspath(__file__))
    nomes = sorted([n for n in os.listdir(base)
                    if n.endswith(".py") or n in ("vocabulario_aprendido.json", "SKILL.md")])
    h = hashlib.sha256()
    for nome in nomes:
        path = os.path.join(base, nome)
        h.update(nome.encode("utf-8")); h.update(b"\0")
        with open(path, "rb") as fh:
            for bloco in iter(lambda: fh.read(1 << 20), b""):
                h.update(bloco)
        h.update(b"\0")
    return h.hexdigest()


def _runtime_versions() -> dict:
    pacotes = ["pandas", "numpy", "PyYAML", "rapidfuzz", "pyproj", "pyarrow",
               "xlsxwriter", "ftfy"]
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for nome in pacotes:
        try:
            out[nome] = importlib_metadata.version(nome)
        except Exception:
            out[nome] = None
    return out


def _input_paths(cfg: dict) -> dict[str, str]:
    base = cfg.get("_base_dir", ".")
    out = {}
    fontes = cfg.get("fontes") if isinstance(cfg.get("fontes"), list) else []
    for f in fontes:
        if isinstance(f, dict) and f.get("arquivo") and f.get("source_id"):
            out[f"fonte:{f['source_id']}"] = os.path.realpath(os.path.abspath(
                os.path.join(base, f["arquivo"])))
    vp = cfg.get("vocab_path")
    if vp:
        out["vocabulario"] = os.path.realpath(os.path.abspath(os.path.join(base, vp)))
    cp = cfg.get("_config_path")
    if cp:
        out["config_arquivo"] = os.path.realpath(os.path.abspath(cp))
    return out


def _hash_inputs(cfg: dict) -> dict[str, dict]:
    ret = {}
    for nome, path in _input_paths(cfg).items():
        if os.path.isfile(path):
            st = os.stat(path)
            ret[nome] = {"path": path, "size": int(st.st_size),
                         "mtime_ns": int(st.st_mtime_ns), "sha256": _sha256_file(path)}
        else:
            ret[nome] = {"path": path, "missing": True}
    return ret


def _inputs_iguais(a: dict, b: dict) -> bool:
    return {k: v.get("sha256") for k, v in a.items()} == {k: v.get("sha256") for k, v in b.items()}


def _pid_vivo(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0); return True
    except PermissionError:
        return True
    except OSError:
        return False


def _fsync_dir(path: str) -> None:
    """Durabilidade do rename em POSIX; no Windows falha silenciosamente."""
    if os.name != "posix":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass



def _preparar_saida_segura(out_root: str) -> None:
    """Cria/valida a árvore controlada sem seguir symlinks.

    A v3.2.4 validava caminhos configurados, mas um ``runs/`` preexistente como
    symlink ainda podia desviar publicação/retenção para fora de ``--out``.
    Esta guarda roda antes de QUALQUER escrita e também no recovery/retenção.
    """
    root = os.path.abspath(out_root)
    if os.path.lexists(root) and os.path.islink(root):
        raise RuntimeError(f"OUT_ROOT_SYMLINK: {root} -> {os.path.realpath(root)}")
    if os.path.exists(root) and not os.path.isdir(root):
        raise RuntimeError(f"OUT_ROOT_NOT_DIR: {root}")
    os.makedirs(root, exist_ok=True)
    root_real = os.path.realpath(root)

    dirs = [os.path.join(root, x) for x in ("runs", ".staging", "failed")]
    dirs.append(os.path.join(root, "failed", "corrupted"))
    for d in dirs:
        if os.path.lexists(d) and os.path.islink(d):
            raise RuntimeError(f"CONTROLLED_PATH_SYMLINK: {d} -> {os.path.realpath(d)}")
        if os.path.exists(d) and not os.path.isdir(d):
            raise RuntimeError(f"CONTROLLED_PATH_NOT_DIR: {d}")
        if not os.path.exists(d):
            os.mkdir(d)
        real = os.path.realpath(d)
        try:
            dentro = os.path.commonpath([real, root_real]) == root_real
        except ValueError:
            dentro = False
        if not dentro:
            raise RuntimeError(f"CONTROLLED_PATH_ESCAPES_OUT: {d} -> {real}")

    latest = os.path.join(root, "latest.json")
    if os.path.lexists(latest) and os.path.islink(latest):
        raise RuntimeError(f"CONTROLLED_PATH_SYMLINK: {latest} -> {os.path.realpath(latest)}")
    if os.path.exists(latest) and not os.path.isfile(latest):
        raise RuntimeError(f"LATEST_NOT_REGULAR_FILE: {latest}")

    # Filhos imediatos das áreas mutáveis também não podem ser links: recovery
    # escreve dentro deles e retenção pode removê-los recursivamente.
    for base in (os.path.join(root, "runs"), os.path.join(root, ".staging"),
                 os.path.join(root, "failed")):
        for nome in os.listdir(base):
            child = os.path.join(base, nome)
            if os.path.islink(child):
                raise RuntimeError(f"CONTROLLED_CHILD_SYMLINK: {child} -> {os.path.realpath(child)}")

def _validar_integridade_run(run_dir: str) -> dict:
    """Valida um run publicado contra seu manifest.

    O manifest não é uma assinatura contra agente malicioso; é uma âncora de
    integridade para detectar truncamento, corrupção e publicação incompleta.
    Nenhum run entra/reentra em `latest.json` sem passar por este gate.
    """
    erros = []
    nome = os.path.basename(os.path.normpath(run_dir))
    em_path = os.path.join(run_dir, "execucao.json")
    mf_path = os.path.join(run_dir, "manifest.json")
    em = mf = None
    try:
        em = json.load(open(em_path, encoding="utf-8"))
    except Exception as e:
        erros.append(f"execucao.json ilegível: {e.__class__.__name__}: {e}")
    try:
        mf = json.load(open(mf_path, encoding="utf-8"))
    except Exception as e:
        erros.append(f"manifest.json ilegível: {e.__class__.__name__}: {e}")
    if isinstance(em, dict):
        if em.get("status") != "RUN_COMPLETED":
            erros.append(f"status={em.get('status')!r}, esperado RUN_COMPLETED")
        if em.get("run_id") and em.get("run_id") != nome:
            erros.append(f"run_id do execucao.json diverge do diretório: {em.get('run_id')} != {nome}")
    if isinstance(mf, dict):
        if mf.get("run_id") != nome:
            erros.append(f"run_id do manifest diverge do diretório: {mf.get('run_id')} != {nome}")
        declarados = mf.get("artifacts")
        if not isinstance(declarados, dict):
            erros.append("manifest.artifacts ausente ou inválido")
        else:
            try:
                atuais = _hash_artifacts(run_dir)
                kd, ka = set(declarados), set(atuais)
                for rel in sorted(kd - ka):
                    erros.append(f"artefato ausente: {rel}")
                for rel in sorted(ka - kd):
                    erros.append(f"artefato não declarado: {rel}")
                for rel in sorted(kd & ka):
                    d, a = declarados.get(rel) or {}, atuais[rel]
                    if int(d.get("size", -1)) != int(a["size"]):
                        erros.append(f"size divergente: {rel}")
                    if str(d.get("sha256", "")) != a["sha256"]:
                        erros.append(f"sha256 divergente: {rel}")
            except Exception as e:
                erros.append(f"falha ao recalcular artefatos: {e.__class__.__name__}: {e}")
    return {"ok": not erros, "run_id": nome, "erros": erros,
            "execucao": em if isinstance(em, dict) else {},
            "manifest": mf if isinstance(mf, dict) else {}}


def _reconciliar_saida(out_root: str, stale_s: float = 3600.0, lex_path: str | None = None) -> dict:
    """Recupera órfãos e reconstrói `latest` SOMENTE com run íntegro.

    - staging com owner do MESMO host e PID morto -> failed imediatamente;
    - staging sem owner só é recuperado após stale_s;
    - `runs/` com status diferente de RUN_COMPLETED só é movido após stale_s;
    - candidato RUN_COMPLETED precisa passar por `manifest.json` + SHA-256;
    - candidato corrompido vai para `failed/corrupted/` e jamais vira latest.
    """
    agora = time.time(); host = socket.gethostname()
    stage_root = os.path.join(out_root, ".staging")
    runs_root = os.path.join(out_root, "runs")
    fail_root = os.path.join(out_root, "failed")
    corrupt_root = os.path.join(fail_root, "corrupted")
    _preparar_saida_segura(out_root)
    rec = {"staging_recuperados": 0, "runs_quarentenados": 0,
           "runs_corrompidos": 0, "latest_reconstruido": False}

    def mover_falha(src, run_id, motivo, lex_local=None, categoria=""):
        raiz = os.path.join(fail_root, categoria) if categoria else fail_root
        os.makedirs(raiz, exist_ok=True)
        dst = os.path.join(raiz, run_id)
        if os.path.exists(dst):
            dst += "-recovered-" + uuid.uuid4().hex[:6]
        _json_atomico(os.path.join(src, "falha.json"), {
            "run_id": run_id, "status": "RUN_FAILED", "motivo": motivo,
            "quando": dt.datetime.now(dt.timezone.utc).isoformat()})
        os.replace(src, dst)
        # v3.2.5: estado operacional do run NÃO reescreve o léxico. A verdade
        # operacional mora em runs/failed; o léxico registra somente seu commit.
        return dst

    for nome in list(os.listdir(stage_root)):
        src = os.path.join(stage_root, nome)
        if not os.path.isdir(src):
            continue
        owner_path = os.path.join(src, "owner.json")
        idade = max(0.0, agora - os.path.getmtime(src))
        recuperar = False
        motivo = "STAGING_STALE"
        lex_owner = None
        try:
            owner = json.load(open(owner_path, encoding="utf-8"))
            lex_owner = owner.get("lexico_path")
            if owner.get("host") == host and not _pid_vivo(int(owner.get("pid", 0))):
                recuperar = True; motivo = "OWNER_PID_MORTO"
            elif owner.get("host") == host and _pid_vivo(int(owner.get("pid", 0))):
                recuperar = False
            elif idade > max(float(stale_s), 86400.0):
                recuperar = True; motivo = "STAGING_REMOTO_STALE_24H"
        except Exception:
            recuperar = idade > float(stale_s)
        if recuperar:
            run_id = nome.removesuffix(".tmp").removesuffix(".failed.tmp")
            with contextlib.suppress(Exception):
                mover_falha(src, run_id, motivo, lex_owner)
                rec["staging_recuperados"] += 1

    # Primeiro remove published dirs estruturalmente incompletos/ilegíveis.
    for nome in list(os.listdir(runs_root)):
        src = os.path.join(runs_root, nome)
        if not os.path.isdir(src):
            continue
        em = os.path.join(src, "execucao.json")
        status = None; lex_run = None
        try:
            emj = json.load(open(em, encoding="utf-8"))
            status = emj.get("status"); lex_run = emj.get("lexico_path")
        except Exception:
            pass
        if status != "RUN_COMPLETED" and agora - os.path.getmtime(src) > float(stale_s):
            with contextlib.suppress(Exception):
                mover_falha(src, nome, f"RUN_DIR_STATUS_{status or 'ILEGIVEL'}", lex_run)
                rec["runs_quarentenados"] += 1

    # Ordena os runs declarados como completos do mais novo para o mais antigo.
    candidatos = []
    for nome in os.listdir(runs_root):
        d = os.path.join(runs_root, nome)
        if not os.path.isdir(d):
            continue
        try:
            em = json.load(open(os.path.join(d, "execucao.json"), encoding="utf-8"))
            if em.get("status") == "RUN_COMPLETED":
                candidatos.append((em.get("completed_at") or nome, nome, em))
        except Exception:
            pass
    candidatos.sort(reverse=True)

    # Somente o primeiro candidato íntegro pode ser latest. Corrompidos mais novos
    # são explicitamente quarentenados e a busca recua para o último run saudável.
    escolhido = None
    for _quando, nome, em in candidatos:
        d = os.path.join(runs_root, nome)
        integ = _validar_integridade_run(d)
        if integ["ok"]:
            escolhido = (nome, em)
            break
        motivo = "RUN_CORRUPTED:" + " | ".join(integ["erros"][:6])
        with contextlib.suppress(Exception):
            mover_falha(d, nome, motivo, em.get("lexico_path"), categoria="corrupted")
            rec["runs_corrompidos"] += 1

    latest_path = os.path.join(out_root, "latest.json")
    atual = None
    try:
        atual = json.load(open(latest_path, encoding="utf-8"))
    except Exception:
        atual = None
    if escolhido:
        nome, em = escolhido
        desejado_path = os.path.join("runs", nome)
        precisa = (not isinstance(atual, dict) or atual.get("run_id") != nome
                   or atual.get("path") != desejado_path or atual.get("status") != "RUN_COMPLETED")
        if precisa:
            _json_atomico(latest_path, {"run_id": nome, "status": "RUN_COMPLETED",
                         "path": desejado_path, "projeto": em.get("projeto", ""),
                         "registros": em.get("registros", 0),
                         "completado_em": em.get("completed_at", ""),
                         "skill_version": em.get("skill_version", VERSION)})
            rec["latest_reconstruido"] = True
    else:
        # Nunca deixe ponteiro stale quando não existe run íntegro para sustentá-lo.
        if os.path.exists(latest_path):
            with contextlib.suppress(Exception):
                os.remove(latest_path)
            rec["latest_reconstruido"] = True
    return rec


def _hash_artifacts(stage: str) -> dict[str, dict]:
    """Hash de artefatos sem seguir symlinks.

    Runs são imutáveis e autocontidos; link simbólico em qualquer profundidade
    viola esse contrato e poderia fazer recovery/downstream ler conteúdo fora da
    árvore publicada.
    """
    ret = {}
    stage_real = os.path.realpath(stage)
    for raiz, dirs, files in os.walk(stage, followlinks=False):
        for d in list(dirs):
            dp = os.path.join(raiz, d)
            if os.path.islink(dp):
                raise RuntimeError(f"RUN_ARTIFACT_SYMLINK: {os.path.relpath(dp, stage)}")
            if os.path.commonpath([os.path.realpath(dp), stage_real]) != stage_real:
                raise RuntimeError(f"RUN_ARTIFACT_ESCAPES_RUN: {dp}")
        for nome in sorted(files):
            if nome in ("manifest.json", "owner.json"):
                continue
            path = os.path.join(raiz, nome)
            rel = os.path.relpath(path, stage).replace(os.sep, "/")
            if os.path.islink(path):
                raise RuntimeError(f"RUN_ARTIFACT_SYMLINK: {rel}")
            real = os.path.realpath(path)
            if os.path.commonpath([real, stage_real]) != stage_real:
                raise RuntimeError(f"RUN_ARTIFACT_ESCAPES_RUN: {rel}")
            st = os.stat(path, follow_symlinks=False)
            if not os.path.isfile(path):
                raise RuntimeError(f"RUN_ARTIFACT_NOT_REGULAR: {rel}")
            ret[rel] = {"size": int(st.st_size), "sha256": _sha256_file(path)}
    return ret


def _aplicar_retencao(out_root: str, manter: int, latest_run_id: str) -> int:
    if not manter or manter <= 0:
        return 0
    _preparar_saida_segura(out_root)
    root = os.path.join(out_root, "runs")
    runs = []
    for nome in os.listdir(root):
        p = os.path.join(root, nome)
        if os.path.islink(p):
            raise RuntimeError(f"RETENTION_REFUSES_SYMLINK: {p}")
        if os.path.isdir(p):
            runs.append((os.path.getmtime(p), nome, p))
    runs.sort(reverse=True)
    preservados = {nome for _t, nome, _p in runs[:manter]}
    preservados.add(latest_run_id)
    removidos = 0
    for _t, nome, p in runs:
        if nome not in preservados:
            shutil.rmtree(p, ignore_errors=True); removidos += 1
    return removidos


def _publicar_falha(out_root: str, run_id: str, rel: dict, erro: str = "") -> str:
    _preparar_saida_segura(out_root)
    tmp = os.path.join(out_root, ".staging", f"{run_id}.failed.tmp")
    final = os.path.join(out_root, "failed", run_id)
    os.makedirs(tmp, exist_ok=False)
    _json_atomico(os.path.join(tmp, "validacao.json"), rel)
    _json_atomico(os.path.join(tmp, "falha.json"), {
        "run_id": run_id, "status": "RUN_FAILED", "erro": erro,
        "quando": dt.datetime.now(dt.timezone.utc).isoformat()})
    os.replace(tmp, final)
    return final


EQ_COLS = ["scope_id", "contexto", "variante", "canonico", "status", "base_decisao",
           "support", "saturado", "score", "first_seen", "last_observed", "last_reinforced"]
INDEF_COLS = ["scope_id", "contexto", "token_a", "token_b", "support", "status", "motivo",
              "first_seen", "last_observed", "last_reinforced", "canonical",
              "resolved_run_id", "resolved_at"]
PROMO_COLS = ["variante", "canonico", "n_contextos"]
QUAR_COLS = ["scope_id", "chave", "motivo", "visto", "detalhes_json"]
AUD_COLS = ["gid_a", "gid_b", "fonte_a", "fonte_b", "numero_chave",
            "logr_a", "logr_b", "dist_m", "sim_logr"]


def _exportar_artefatos(stage: str, saida: pd.DataFrame, lex: dict,
                        aud: pd.DataFrame, meta: dict, teto_xlsx: int,
                        opcoes: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Escreve o conjunto COMPLETO de artefatos no staging. Schema é estável."""
    opcoes = opcoes or {}
    if opcoes.get("parquet", True):
        try:
            saida.to_parquet(os.path.join(stage, "ajuste_logradouro.parquet"), index=False)
        except Exception as e:
            log(f"     [!] parquet não gravado ({e.__class__.__name__}); CSV segue completo")
    saida.to_csv(os.path.join(stage, "ajuste_logradouro.csv"), index=False)
    if opcoes.get("marcacao_por_fonte", True):
        for sid, g in saida.groupby("aj_source_id", sort=True):
            # Não remove colunas totalmente vazias: contrato da tabela é estável entre cidades.
            g.to_csv(os.path.join(stage, f"marcacao_{sid}.csv"), index=False)

    eq_rows = []
    indef_rows = []
    quar_rows = []
    for scope_id, sd in sorted(lex.get("scopes", {}).items()):
        if not isinstance(sd, dict):
            continue
        for ctx, vs in sorted(sd.get("equiv", {}).items()):
            for v, e in sorted(vs.items()):
                eq_rows.append({
                    "scope_id": scope_id, "contexto": ctx, "variante": v,
                    "canonico": e.get("canonical", ""), "status": e.get("status", ""),
                    "base_decisao": e.get("base_decisao", ""), "support": e.get("support", 0),
                    "saturado": e.get("status_support", ""), "score": e.get("score", 0.0),
                    "first_seen": e.get("first_seen", ""),
                    "last_observed": e.get("last_observed", ""),
                    "last_reinforced": e.get("last_reinforced", ""),
                })
        for e in sd.get("indefinidos", {}).values():
            indef_rows.append({
                "scope_id": scope_id, "contexto": e.get("contexto", ""),
                "token_a": e.get("token_a", ""), "token_b": e.get("token_b", ""),
                "support": e.get("support", 0), "status": e.get("status", ""),
                "motivo": e.get("motivo", ""), "first_seen": e.get("first_seen", ""),
                "last_observed": e.get("last_observed", ""),
                "last_reinforced": e.get("last_reinforced", ""),
                "canonical": e.get("canonical", ""),
                "resolved_run_id": e.get("resolved_run_id", ""),
                "resolved_at": e.get("resolved_at", ""),
            })
        for chave, v in sorted(sd.get("quarentena", {}).items()):
            base = {"scope_id": scope_id, "chave": chave, "motivo": v.get("motivo", ""),
                    "visto": v.get("visto", "")}
            extra = {k: val for k, val in v.items() if k not in ("motivo", "visto")}
            base["detalhes_json"] = json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else ""
            quar_rows.append(base)

    eq = pd.DataFrame(eq_rows, columns=EQ_COLS)
    eq.to_csv(os.path.join(stage, "lexico_equivalencias.csv"), index=False)
    pd.DataFrame(indef_rows, columns=INDEF_COLS).to_csv(
        os.path.join(stage, "lexico_indefinidos.csv"), index=False)
    promo = pd.DataFrame(LS.candidatas_globais(lex), columns=PROMO_COLS)
    promo.to_csv(os.path.join(stage, "lexico_promocao_global.csv"), index=False)
    pd.DataFrame(quar_rows, columns=QUAR_COLS).to_csv(
        os.path.join(stage, "lexico_quarentena.csv"), index=False)

    aud_fix = aud.copy() if aud is not None else pd.DataFrame()
    for c in AUD_COLS:
        if c not in aud_fix.columns:
            aud_fix[c] = pd.Series(dtype="object")
    aud_fix = aud_fix[AUD_COLS]
    aud_fix.to_csv(os.path.join(stage, "pares_mineracao.csv"), index=False)

    _json_atomico(os.path.join(stage, "execucao.json"), meta)
    if opcoes.get("xlsx", True):
        _excel(os.path.join(stage, "ajuste_logradouro.xlsx"), saida, eq, aud_fix, meta, teto_xlsx)
    return eq, aud_fix


def run(cfg: dict, out_dir: str) -> dict:
    """Executa um run v3.3 com léxico territorial + auditoria semântica."""
    if not isinstance(cfg, dict):
        raise SystemExit("configuração inválida: raiz YAML deve ser objeto/mapa")
    t0 = time.time()
    p = {**DEFAULTS, **(cfg.get("parametros") or {})} if isinstance(cfg.get("parametros") or {}, dict) else DEFAULTS.copy()
    hard = cfg.get("hardening", True)
    aprende = cfg.get("aprendizado")
    scope_id = str(cfg.get("scope_id", ""))
    reparar_mojibake = bool(p.get("reparar_mojibake", False))
    out_requested = os.path.abspath(out_dir)
    out_root = os.path.realpath(out_requested)
    run_id = _novo_run_id()
    lex_path = os.path.realpath(os.path.abspath(os.path.join(
        cfg.get("_base_dir", "."), cfg.get("lexico_path", "lexico.json"))))
    lex_committed = False
    final_renamed = False
    latest_published = False
    stage = os.path.join(out_root, ".staging", f"{run_id}.tmp")
    final = os.path.join(out_root, "runs", run_id)
    input_hashes_initial = _hash_inputs(cfg)
    lex_loaded_info = {"sha256": None, "file_sha256": None, "versao": 0,
                       "revision": 0, "state_version": 0, "state_sha256": None}
    lex_tx = {"sha256_before": None, "sha256_after": None,
              "file_sha256_before": None, "file_sha256_after": None,
              "versao_before": 0, "versao_after": 0,
              "revision_before": 0, "revision_after": 0,
              "state_version_before": 0, "state_version_after": 0,
              "state_sha256_before": None, "state_sha256_after": None,
              "state_changed": False, "committed_at": "", "committed": False}
    recovery = {}

    log(f"0/7 Validando schema (run {run_id})...")
    rel = V.validar(cfg)
    rel["erros"].extend(V.validar_destino(cfg, out_requested))
    # TOCTOU durante a própria validação: se o arquivo mudou enquanto era inspecionado,
    # não existe garantia de que o schema medido corresponde ao conteúdo que será lido.
    input_hashes_validated = _hash_inputs(cfg)
    if not _inputs_iguais(input_hashes_initial, input_hashes_validated):
        rel["erros"].append("arquivo(s) de entrada/config/vocabulário mudaram durante a validação — execute novamente")
    rel["ok"] = not rel["erros"]
    # Guardas de configuração/pathname falham SEM tocar a saída: se `failed/`
    # for justamente o symlink malicioso, publicar a falha já seria vulnerável.
    V.exigir(rel, abortar_em_alerta=bool(p.get("abortar_em_alerta", False)))
    _preparar_saida_segura(out_root)

    recovery = _reconciliar_saida(out_root, float(p.get("reconciliacao_stale_s", 3600.0)), lex_path)
    os.makedirs(stage, exist_ok=False)
    _json_atomico(os.path.join(stage, "owner.json"), {
        "run_id": run_id, "pid": os.getpid(), "host": socket.gethostname(),
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "version": VERSION,
        "lexico_path": lex_path})
    _json_atomico(os.path.join(stage, "validacao.json"), rel)

    try:
        log("1/7 Carregando fontes...")
        df = carregar(cfg)
        # Entrada não pode ter mudado entre o hash validado e a carga.
        if not _inputs_iguais(input_hashes_validated, _hash_inputs(cfg)):
            raise RuntimeError("INPUT_CHANGED_AFTER_VALIDATION")
        df[GID] = range(len(df))

        log("2/7 Normalizando (contextual + vocabulário) e parseando número...")
        vpath = cfg.get("vocab_path")
        vmap = N.carregar_vocab_tipo(os.path.join(cfg["_base_dir"], vpath) if vpath else None)
        df[LOGR_PRE] = por_valor_unico(
            df[INTERNO + "src_logradouro"],
            lambda v: N.marcar(v, hard=hard, vmap=vmap,
                              reparar_mojibake=reparar_mojibake)["logr_marcado"])
        nums = por_valor_unico(df[INTERNO + "src_numero"], NU.parse)
        df["aj_num_tipo"] = [n["tipo"] for n in nums]
        df["aj_num_base"] = [n["base"] for n in nums]
        # Compatibilidade/auditoria: modificador continua disponível, mas NÃO compõe
        # mais o campo de número publicado.
        df["aj_num_modificador"] = [n["modificador"] for n in nums]
        df["aj_num_anotacao"] = [n.get("anotacao", n.get("modificador", "")) for n in nums]
        df["aj_num_anotacao_tipo"] = [n.get("anotacao_tipo", "") for n in nums]
        df["aj_num_complemento_derivado"] = [n.get("complemento_derivado", "") for n in nums]
        df["aj_num_secundario"] = [n["secundario"] for n in nums]
        df["aj_num_km"] = [n["km"] for n in nums]
        df["aj_num_canonico"] = [n["canonico"] for n in nums]
        df["aj_num_chave"] = [n["chave"] for n in nums]
        # Contrato v3.3.5: número é somente o inteiro base.
        df["aj_numero_int"] = df["aj_num_base"]

        # Estado e hash lidos sob o mesmo lock: o manifest sabe exatamente qual
        # fotografia do léxico foi usada para iniciar esta execução.
        lex, lex_loaded_info = LIO.carregar_travado(lex_path)
        lex_tx.update({
            "sha256_before": lex_loaded_info.get("file_sha256"),
            "sha256_after": lex_loaded_info.get("file_sha256"),
            "file_sha256_before": lex_loaded_info.get("file_sha256"),
            "file_sha256_after": lex_loaded_info.get("file_sha256"),
            "versao_before": int(lex_loaded_info.get("revision", 0)),
            "versao_after": int(lex_loaded_info.get("revision", 0)),
            "revision_before": int(lex_loaded_info.get("revision", 0)),
            "revision_after": int(lex_loaded_info.get("revision", 0)),
            "state_version_before": int(lex_loaded_info.get("state_version", 0)),
            "state_version_after": int(lex_loaded_info.get("state_version", 0)),
            "state_sha256_before": lex_loaded_info.get("state_sha256"),
            "state_sha256_after": lex_loaded_info.get("state_sha256"),
        })
        diag, aud = {}, pd.DataFrame(columns=AUD_COLS)
        novos = []

        if aprende:
            log("3/7 Projetando + pareamento leve (gate de número estruturado + raio)...")
            lat = pd.to_numeric(df[INTERNO + "src_lat"], errors="coerce")
            lon = pd.to_numeric(df[INTERNO + "src_lon"], errors="coerce")
            x, y, srid, modo, gdiag = G.projetar(lon.to_numpy(), lat.to_numpy(),
                                                 cfg.get("srid_metrico"))
            df[XI], df[YI] = x, y
            log(f"     EPSG:{srid} ({gdiag['familia_srid']}, "
                f"{gdiag['zona_utm']}{gdiag['hemisferio_utm']}, {modo})"
                + f" | coord inválidas saneadas: {gdiag['coord_invalidas']}")
            cand, aud, diag = P.gerar_candidatos(
                df, raio_m=float(p["raio_aprendizado_m"]), teto_bloco=int(p["teto_bloco"]),
                apenas_entre_fontes=p["apenas_entre_fontes"],
                modo_numero=str(p["modo_numero"]))
            diag.update(gdiag); diag["modo_projecao"] = modo
            log(f"     {diag.get('pares', 0)} pares na trava (≤{p['raio_aprendizado_m']} m) | "
                f"sem número/coord: {diag['sem_numero_ou_coord']} | "
                f"blocos podados: {diag['blocos_podados']} | "
                f"barrados por modificador: {diag['pares_bloqueados_modificador']}")

            log("4/7 Minerando equivalências e atualizando o léxico (commit auditável)...")
            info = {}
            for g, lp, nb, la_, lo_, nv, sid, rid in zip(
                    df[GID], df[LOGR_PRE], df["aj_num_base"], lat, lon,
                    df["aj_autoridade_nivel"], df["aj_source_id"], df["aj_record_id"]):
                info[int(g)] = {"logr": lp, "num": nb, "lat": la_, "lon": lo_,
                                "autoridade_nivel": int(nv), "source_id": sid,
                                "record_id": rid}
            pesos = df[LOGR_PRE].value_counts().to_dict()
            freq_ctx = LS.frequencias_contextuais(pesos.keys(), pesos)
            diag["fontes_autoridade"] = sorted(
                df.loc[df["aj_autoridade_nivel"] > 0, "aj_source_id"].unique().tolist())
            diag["autoridade_niveis"] = {str(sid): int(nv) for sid, nv in
                df[["aj_source_id", "aj_autoridade_nivel"]].drop_duplicates().itertuples(index=False, name=None)}
            diag["contextos_observados"] = len({k[0] for k in freq_ctx})
            achados = LS.minerar(cand, info, lex, freq_ctx, scope_id=scope_id, run_id=run_id,
                                 ratio=float(p["ratio_dominancia"]),
                                 min_freq=int(p["min_freq_dominancia"]))
            diag["achados_indefinidos"] = sum(1 for a in achados if a.get("canonico") is None)
            # A fonte precisa continuar exatamente a mesma ATÉ o commit do estado compartilhado.
            if not _inputs_iguais(input_hashes_validated, _hash_inputs(cfg)):
                raise RuntimeError("INPUT_CHANGED_BEFORE_LEXICON_COMMIT")
            lex, novos, commit_info = LIO.atualizar_travado(
                lex_path, achados, min_support=int(p["min_support_equiv"]),
                raio_independencia=float(p["raio_independencia_m"]), run_id=run_id,
                scope_id=scope_id)
            lex_tx.update(commit_info); lex_tx["committed"] = True
            lex_committed = True
            sd_scope = lex.get("scopes", {}).get(scope_id, {})
            todas = [e for vs in sd_scope.get("equiv", {}).values() for e in vs.values()]
            n_at = sum(1 for e in todas if e.get("status") == "ativo")
            n_cd = sum(1 for e in todas if e.get("status") == "candidato")
            n_ind = LS.contar_indefinidos(lex, scope_id, apenas_abertos=True)
            n_qua = len(sd_scope.get("quarentena", {}))
            log(f"     {len(achados)} observações | scope={scope_id} ativos={n_at} candidatos={n_cd}"
                f" indefinidos={n_ind} quarentena={n_qua}"
                + (f" | promovidos agora: "
                   f"{', '.join(f'{ctx}: {v}→{c}' for _sid, ctx, v, c in novos[:3])}"
                   if novos else ""))
        else:
            log("3/7 Pareamento desligado (aprendizado=false) — léxico só em aplicação.")
            log("4/7 —")

        log("5/7 Marcando logradouro (léxico ativo, com tier) + organizando complemento...")
        ativos = LS.mapa_ativo(lex, scope_id)
        marcas = por_valor_unico(
            df[INTERNO + "src_logradouro"],
            lambda v: N.marcar(v, hard=hard, vmap=vmap, ativos=ativos,
                              reparar_mojibake=reparar_mojibake))
        df["aj_logr_base"] = [m["logr_base"] for m in marcas]
        df["aj_logr_marcado"] = [m["logr_marcado"] for m in marcas]
        df["aj_logr_tier"] = [m["tier"] for m in marcas]
        df["aj_logr_origem"] = [m["origem"] for m in marcas]
        df["aj_logr_risco"] = [m["risco"] for m in marcas]
        df["aj_logr_alterado"] = [m["alterado"] for m in marcas]

        # A anotação retirada do campo número é incorporada à visão de complemento
        # sem alterar a coluna original da fonte. Ex.: 1B + "" -> numero=1, complemento=IMOVEL B.
        compl_orig = df[INTERNO + "src_complemento"].tolist()
        compl_proc = [NU.combinar_complemento(n, c) for n, c in zip(nums, compl_orig)]
        # Deduplica por texto processado para manter o ganho do cache por valor único.
        cache_org = {}
        orgs = []
        for cp in compl_proc:
            key = str(cp or "")
            if key not in cache_org:
                cache_org[key] = C.organizar(cp)
            orgs.append(cache_org[key])
        compl_cols = {
            "aj_compl_original": ["" if c is None else str(c) for c in compl_orig],
            "aj_compl_processado": compl_proc,
            "aj_compl_organizado": [o["complemento_organizado"] for o in orgs],
            "aj_compl_tier": [o["tier"] for o in orgs],
            "aj_compl_risco": ["|".join(o["risco"]) for o in orgs],
            "aj_compl_origem": ["|".join(f"{k}:{v}" for k, v in sorted(o.get("componentes_origem", {}).items())) for o in orgs],
            "aj_compl_natureza_endereco": [o.get("natureza_endereco", "INDEFINIDO") for o in orgs],
            "aj_compl_classe": [o.get("classe", "NAO_CLASSIFICADO") for o in orgs],
            "aj_compl_subclasses": ["|".join(o.get("subclasses", [])) for o in orgs],
            "aj_compl_confianca": [o.get("confianca", 0.0) for o in orgs],
            "aj_compl_decisao_cadastral": [o.get("decisao_cadastral", "REVISAR") for o in orgs],
            "aj_compl_endereco_real": [o.get("complemento_endereco_real", "") for o in orgs],
            "aj_compl_referencia": [o.get("referencia", "") for o in orgs],
            "aj_compl_referencia_tipo": [o.get("referencia_tipo", "") for o in orgs],
            "aj_compl_relacao_referencia": [o.get("relacao_referencia", "") for o in orgs],
            "aj_compl_acesso": [o.get("acesso", "") for o in orgs],
            "aj_compl_descricao": [o.get("descricao", "") for o in orgs],
            "aj_compl_empreendimento": [o.get("empreendimento", "") for o in orgs],
            "aj_compl_endereco_limpo": [o.get("complemento_endereco_limpo", o.get("complemento_endereco_real", "")) for o in orgs],
            "aj_compl_segmentos_json": [o.get("segmentos_json", "[]") for o in orgs],
            "aj_compl_confianca_metodo": [o.get("confianca_metodo", "HEURISTICA_V1") for o in orgs],
            "aj_compl_utilidade_operacional": [o.get("utilidade_operacional", "BAIXA") for o in orgs],
            "aj_compl_referencia_original": [o.get("referencia_original", "") for o in orgs],
            "aj_compl_referencia_status": [o.get("referencia_status", "") for o in orgs],
            "aj_compl_referencia_dist_m": [o.get("referencia_dist_m", "") for o in orgs],
            "aj_compl_referencia_fontes": [o.get("referencia_fontes", "") for o in orgs],
            "aj_compl_conflitos": ["|".join(f"{k}:{','.join(map(str,v))}" for k,v in sorted(o.get("conflitos", {}).items())) for o in orgs],
            "aj_compl_identificador": ["|".join(o["identificador_sem_rotulo"]) for o in orgs],
            "aj_compl_descartado": ["|".join(o["descartado"]) for o in orgs],
            "aj_compl_residuo": ["|".join(o.get("residuo", [])) for o in orgs],
        }
        for comp in COMPONENTES:
            compl_cols[f"aj_compl_{comp}"] = [o["componentes"].get(comp) or "" for o in orgs]
            compl_cols[f"aj_compl_{comp}_origem"] = [o.get("componentes_origem", {}).get(comp, "") for o in orgs]
            compl_cols[f"aj_compl_{comp}_candidatos"] = ["|".join(map(str, o.get("componentes_candidatos", {}).get(comp, []))) for o in orgs]
        df = pd.concat([df, pd.DataFrame(compl_cols, index=df.index)], axis=1)

        # TOCTOU final: nenhuma saída é publicada se o insumo mudou no meio do run.
        input_hashes_final = _hash_inputs(cfg)
        if not _inputs_iguais(input_hashes_validated, input_hashes_final):
            raise RuntimeError("INPUT_CHANGED_DURING_RUN")

        log("6/7 Gravando staging completo + manifest...")
        saida = df.drop(columns=[c for c in df.columns if c.startswith(INTERNO)])
        completed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        meta = {"projeto": cfg.get("projeto", ""), "scope_id": scope_id, "run_id": run_id,
                "status": "RUN_COMPLETED", "completed_at": completed_at,
                "skill_version": VERSION, "skill_code_sha256": _hash_code(),
                "config_sha256": _hash_cfg(cfg), "manifest_file": "manifest.json",
                "lexico_committed": lex_committed, "lexico_path": lex_path,
                "lexico_file_sha256_loaded": lex_loaded_info.get("file_sha256"),
                "lexico_file_sha256_before": lex_tx.get("file_sha256_before"),
                "lexico_file_sha256_after": lex_tx.get("file_sha256_after"),
                "lexico_revision_loaded": int(lex_loaded_info.get("revision", 0)),
                "lexico_revision_before": int(lex_tx.get("revision_before", 0)),
                "lexico_revision_after": int(lex_tx.get("revision_after", 0)),
                "lexico_state_version_loaded": int(lex_loaded_info.get("state_version", 0)),
                "lexico_state_version_before": int(lex_tx.get("state_version_before", 0)),
                "lexico_state_version_after": int(lex_tx.get("state_version_after", 0)),
                "lexico_state_sha256_loaded": lex_loaded_info.get("state_sha256"),
                "lexico_state_sha256_before": lex_tx.get("state_sha256_before"),
                "lexico_state_sha256_after": lex_tx.get("state_sha256_after"),
                "lexico_state_changed": bool(lex_tx.get("state_changed", False)),
                # compatibilidade de relatório: "versão" agora aponta ao conhecimento
                "lexico_versao": int(lex_tx.get("state_version_after", lex.get("state_version", 0))),
                "lexico_indefinidos": LS.contar_indefinidos(lex, scope_id, apenas_abertos=True),
                "lexico_contextos": len(lex.get("scopes", {}).get(scope_id, {}).get("equiv", {})),
                "lexico_scopes": len(lex.get("scopes", {})),
                "lexico_legado_sem_escopo": len(lex.get("legado_sem_escopo", {}).get("equiv", {})),
                "lexico_legado_global": len(lex.get("equiv_legado_global", {})),
                "lexico_quarentena": len(lex.get("scopes", {}).get(scope_id, {}).get("quarentena", {})),
                "registros": int(len(df)), "fontes": [f["source_id"] for f in cfg["fontes"]],
                "hardening": hard, "aprendizado": aprende,
                "motor_similaridade": S.MOTOR, "parametros": p, "diag": diag,
                "marcados": int(df["aj_logr_alterado"].sum()),
                "tier": df["aj_logr_tier"].value_counts().to_dict(),
                "numero_com_modificador": int((df["aj_num_modificador"] != "").sum()),
                "numero_anotacao_migrada_complemento": int((df["aj_num_complemento_derivado"] != "").sum()),
                "validacao": {"erros": rel["erros"], "alertas": rel["alertas"]},
                "com_complemento_organizado": int((df["aj_compl_organizado"] != "").sum()),
                "complemento_referencial": int((df["aj_compl_risco"] != "").sum()),
                "lexico_ativos": len(ativos), "promovidos_nesta_rodada": novos,
                "run_dir": os.path.relpath(final, out_root), "recovery": recovery,
                "runtime": _runtime_versions(), "segundos": round(time.time() - t0, 2)}
        _eq, aud_fix = _exportar_artefatos(stage, saida, lex, aud, meta,
                                           int(p["max_linhas_xlsx"]), p)
        # Excel/Parquet podem demorar: repete a trava TOCTOU antes da publicação.
        input_hashes_publish = _hash_inputs(cfg)
        if not _inputs_iguais(input_hashes_validated, input_hashes_publish):
            raise RuntimeError("INPUT_CHANGED_DURING_ARTIFACT_WRITE")
        meta["segundos"] = round(time.time() - t0, 2)
        _json_atomico(os.path.join(stage, "execucao.json"), meta)
        manifest = {"schema": "a2l-run-manifest/4", "skill_version": VERSION,
                    "run_id": run_id, "scope_id": scope_id, "created_at": completed_at,
                    "skill_code_sha256": meta["skill_code_sha256"],
                    "config_sha256": meta["config_sha256"],
                    "inputs": input_hashes_validated,
                    "lexico": {
                        "path": lex_path,
                        "committed": bool(lex_tx.get("committed")),
                        "file_sha256_loaded": lex_loaded_info.get("file_sha256"),
                        "file_sha256_before": lex_tx.get("file_sha256_before"),
                        "file_sha256_after": lex_tx.get("file_sha256_after"),
                        "revision_loaded": int(lex_loaded_info.get("revision", 0)),
                        "revision_before": int(lex_tx.get("revision_before", 0)),
                        "revision_after": int(lex_tx.get("revision_after", 0)),
                        "state_version_loaded": int(lex_loaded_info.get("state_version", 0)),
                        "state_version_before": int(lex_tx.get("state_version_before", 0)),
                        "state_version_after": int(lex_tx.get("state_version_after", 0)),
                        "state_sha256_loaded": lex_loaded_info.get("state_sha256"),
                        "state_sha256_before": lex_tx.get("state_sha256_before"),
                        "state_sha256_after": lex_tx.get("state_sha256_after"),
                        "state_changed": bool(lex_tx.get("state_changed", False)),
                        "migration_from": lex_tx.get("migration_from"),
                        "schema_before": lex_tx.get("schema_before"),
                        "schema_after": lex_tx.get("schema_after", "3.3"),
                        "committed_at": lex_tx.get("committed_at", ""),
                    },
                    "runtime": meta["runtime"],
                    "artifacts": _hash_artifacts(stage)}
        _json_atomico(os.path.join(stage, "manifest.json"), manifest)
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(stage, "owner.json"))
        _fsync_dir(stage)

        # PUBLICAÇÃO REALMENTE IMUTÁVEL: RUN_COMPLETED já está escrito no staging.
        os.makedirs(os.path.dirname(final), exist_ok=True)
        os.replace(stage, final)
        final_renamed = True
        _fsync_dir(os.path.dirname(final))
        latest = {"run_id": run_id, "status": "RUN_COMPLETED",
                  "path": os.path.relpath(final, out_root),
                  "projeto": meta["projeto"], "registros": meta["registros"],
                  "completado_em": completed_at, "skill_version": VERSION}
        _json_atomico(os.path.join(out_root, "latest.json"), latest)
        _fsync_dir(out_root)
        latest_published = True
        removidos = _aplicar_retencao(out_root, int(p.get("retencao_runs", 0)), run_id)
        if removidos:
            log(f"     retenção: {removidos} run(s) antigo(s) removido(s)")
        log("7/7 " + " ".join(f"{k}={v}" for k, v in sorted(meta["tier"].items())))
        log(f"OK  {len(df)} registros | {meta['marcados']} marcações | "
            f"{meta['lexico_ativos']} equivalências ativas | {meta['segundos']}s -> {final}")
        return meta

    except BaseException as e:
        # Status operacional é exclusivo da árvore de runs/failed. O commit do
        # léxico, se ocorreu, permanece auditável e nunca é reescrito pelo erro.
        # staging parcial ou diretório já renomeado mas ainda sem latest nunca fica válido.
        with contextlib.suppress(Exception):
            failed = os.path.join(out_root, "failed", run_id)
            os.makedirs(os.path.join(out_root, "failed"), exist_ok=True)
            origem = stage if os.path.isdir(stage) else (final if final_renamed and not latest_published
                                                         and os.path.isdir(final) else None)
            if origem:
                falha = {"run_id": run_id, "status": "RUN_FAILED",
                         "erro": f"{e.__class__.__name__}: {e}",
                         "traceback": traceback.format_exc()[-8000:],
                         "lexico_committed": lex_committed, "skill_version": VERSION,
                         "quando": dt.datetime.now(dt.timezone.utc).isoformat()}
                _json_atomico(os.path.join(origem, "falha.json"), falha)
                if os.path.exists(os.path.join(origem, "execucao.json")):
                    try:
                        em = json.load(open(os.path.join(origem, "execucao.json"), encoding="utf-8"))
                        em["status"] = "RUN_FAILED"; em["erro"] = falha["erro"]
                        _json_atomico(os.path.join(origem, "execucao.json"), em)
                    except Exception:
                        pass
                os.replace(origem, failed)
        raise


# ---------------------------------------------------------------------------
def _excel(path, saida, eq, aud, meta, teto):
    try:
        import xlsxwriter                                   # noqa: F401
    except Exception:                                       # pragma: no cover
        return
    # Dados cadastrais externos nunca podem virar fórmula/URL executável no Excel.
    with pd.ExcelWriter(path, engine="xlsxwriter",
                        engine_kwargs={"options": {"strings_to_formulas": False,
                                                   "strings_to_urls": False}}) as xw:
        wb = xw.book
        h = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": NAVY,
                           "border": 1, "align": "center", "valign": "vcenter"})
        t = wb.add_format({"bold": True, "font_size": 14, "font_color": INK})
        k = wb.add_format({"bold": True, "font_color": TEAL})

        ws = wb.add_worksheet("RESUMO")
        ws.set_column("A:A", 34); ws.set_column("B:B", 62)
        ws.write(0, 0, "Ajuste de Logradouro — A2L", t)
        linhas = [("Projeto", meta["projeto"]), ("Fontes", ", ".join(meta["fontes"])),
                  ("Registros", meta["registros"]), ("Logradouros marcados", meta["marcados"]),
                  ("Complementos organizados", meta["com_complemento_organizado"]),
                  ("Complementos com contexto referencial", meta["complemento_referencial"]),
                  ("Equivalências ativas", meta["lexico_ativos"]),
                  ("Tier das marcações", " ".join(f"{k}={v}" for k, v in
                                                  sorted(meta["tier"].items()))),
                  ("Número com modificador", meta["numero_com_modificador"]),
                  ("Alertas de validação", len(meta["validacao"]["alertas"])),
                  ("Léxico — versão", meta["lexico_versao"]),
                  ("Léxico — contextos aprendidos", meta["lexico_contextos"]),
                  ("Léxico — indefinidos (sem canônico)", meta["lexico_indefinidos"]),
                  ("Léxico — quarentena", meta["lexico_quarentena"]),
                  ("Fontes de autoridade", ", ".join(meta["diag"].get("fontes_autoridade", [])) or "—"),
                  ("Promovidas nesta rodada",
                   ", ".join(f"{ctx}: {v}→{c}"
                             for _sid, ctx, v, c in meta["promovidos_nesta_rodada"][:5]) or "—"),
                  ("Hardening", meta["hardening"]), ("Aprendizado", meta["aprendizado"]),
                  ("Motor de similaridade", meta["motor_similaridade"]),
                  ("SRID / projeção", f"{meta['diag'].get('srid', '—')} "
                                      f"({meta['diag'].get('modo_projecao', '—')})"),
                  ("Pares na trava", meta["diag"].get("pares", 0)),
                  ("Sem número ou coordenada", meta["diag"].get("sem_numero_ou_coord", 0)),
                  ("Blocos podados (teto)", meta["diag"].get("blocos_podados", 0)),
                  ("Tempo (s)", meta["segundos"])]
        for i, (a, b) in enumerate(linhas, start=2):
            ws.write(i, 0, a, k); ws.write(i, 1, str(b))

        for nome, d in (("MARCACOES", saida[saida["aj_logr_alterado"] == True]),  # noqa: E712
                        ("LEXICO", eq), ("PARES", aud)):
            if d is None or len(d) == 0:
                continue
            d = d.head(teto)
            d.to_excel(xw, sheet_name=nome, index=False, startrow=1)
            w = xw.sheets[nome]
            for j, c in enumerate(d.columns):
                w.write(1, j, c, h)
                w.set_column(j, j, min(40, max(12, len(str(c)) + 6)))
            w.freeze_panes(2, 0)
            w.autofilter(1, 0, 1 + len(d), len(d.columns) - 1)


def main():
    ap = argparse.ArgumentParser(description="Marca logradouro e organiza complemento (A2L)")
    ap.add_argument("config")
    ap.add_argument("--out", default="saida")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    if not isinstance(cfg, dict):
        print(f"ERRO configuração: raiz YAML deve ser objeto/mapa, veio {type(cfg).__name__}", file=sys.stderr)
        raise SystemExit(2)
    cfg["_base_dir"] = os.path.dirname(os.path.abspath(a.config))
    cfg["_config_path"] = os.path.abspath(a.config)
    run(cfg, a.out)


if __name__ == "__main__":
    main()
