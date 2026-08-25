# -*- coding: utf-8 -*-
"""
lexico_io.py — estado compartilhado com lock cross-platform e cadeia semântica
=============================================================================
v3.3 preserva a separação entre estado físico e conhecimento semântico:

* ``revision``: revisão física do arquivo — cresce a cada gravação auditável;
* ``state_version``: versão do CONHECIMENTO — cresce somente quando o estado
  semântico muda;
* ``state_sha256``: hash canônico somente do estado semântico. Histórico de
  execução, timestamps e revisão física não entram nesse hash.

O status operacional do run (RUN_COMPLETED/RUN_FAILED) mora exclusivamente nos
artefatos do run. O léxico é append-only apenas quanto ao evento do commit do
conhecimento; ele não é reescrito depois para refletir sucesso/falha operacional.
"""
from __future__ import annotations
import contextlib
import datetime as _dt
import hashlib
import json
import os
import shutil
import socket
import time

import lexico_seguro as LS

try:
    import fcntl
    _FCNTL = True
except Exception:                                    # pragma: no cover
    _FCNTL = False
try:                                                 # pragma: no cover - Windows
    import msvcrt
    _MSVCRT = True
except Exception:                                    # pragma: no cover
    _MSVCRT = False


def _pid_vivo(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@contextlib.contextmanager
def travar(path: str, timeout: float = 60.0, intervalo: float = 0.15):
    """Lock exclusivo. Timeout falha alto — nunca segue sem lock.

    O domínio do lock é o caminho CANÔNICO do estado. Sidecars de lock não
    podem ser symlink, evitando alias/travessia de filesystem.
    """
    path = os.path.realpath(os.path.abspath(path))
    if os.path.isfile(path) and not os.path.islink(path):
        try:
            if os.stat(path, follow_symlinks=False).st_nlink > 1:
                raise RuntimeError(f"LEXICO_HARDLINK_REFUSED: {path} nlink>1")
        except FileNotFoundError:
            pass
    t0 = time.time()
    lockfile = path + ".lock"
    if os.path.lexists(lockfile) and os.path.islink(lockfile):
        raise RuntimeError(f"LEXICO_LOCK_SYMLINK: {lockfile} -> {os.path.realpath(lockfile)}")
    if _FCNTL:
        fh = open(lockfile, "a+")
        try:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() - t0 > timeout:
                        raise TimeoutError(f"lock do léxico ocupado: {lockfile}")
                    time.sleep(intervalo)
            yield
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        return

    if _MSVCRT:                                      # pragma: no cover - Windows
        fh = open(lockfile, "a+b")
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"0"); fh.flush()
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() - t0 > timeout:
                        raise TimeoutError(f"lock do léxico ocupado: {lockfile}")
                    time.sleep(intervalo)
            yield
        finally:
            with contextlib.suppress(Exception):
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            fh.close()
        return

    lockdir = path + ".lockdir"                       # pragma: no cover
    if os.path.lexists(lockdir) and os.path.islink(lockdir):
        raise RuntimeError(f"LEXICO_LOCKDIR_SYMLINK: {lockdir} -> {os.path.realpath(lockdir)}")
    owner = os.path.join(lockdir, "owner.json")
    while True:
        try:
            os.mkdir(lockdir)
            with open(owner, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                           "criado_em": time.time()}, f)
            break
        except FileExistsError:
            stale = False
            try:
                d = json.load(open(owner, encoding="utf-8"))
                idade = time.time() - float(d.get("criado_em", 0))
                stale = idade > timeout and not _pid_vivo(int(d.get("pid", 0)))
            except Exception:
                idade = time.time() - os.path.getmtime(lockdir)
                stale = idade > max(timeout * 2, 120)
            if stale:
                with contextlib.suppress(Exception):
                    shutil.rmtree(lockdir)
                continue
            if time.time() - t0 > timeout:
                raise TimeoutError(f"lock do léxico ocupado: {lockdir}")
            time.sleep(intervalo)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(lockdir)


def _sha256_file(path: str):
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _semantic_projection(lex: dict) -> dict:
    """Recorta somente conhecimento persistente para o hash semântico.

    ``last_observed`` é telemetria de replay/observação: pode mudar sem qualquer
    novo reforço físico. Portanto não participa do hash do conhecimento. Já
    ``last_reinforced``, ``score`` e ``status`` permanecem semânticos porque
    alteram a confiança/aplicabilidade da regra.
    """
    operacionais_raiz = {"versao", "revision", "state_version", "state_sha256",
                         "atualizado_em", "runs"}
    operacionais_recursivos = {"last_observed"}

    def limpar(v):
        if isinstance(v, dict):
            return {k: limpar(x) for k, x in v.items() if k not in operacionais_recursivos}
        if isinstance(v, list):
            return [limpar(x) for x in v]
        return v

    return {k: limpar(v) for k, v in lex.items() if k not in operacionais_raiz}


def state_sha256(lex: dict) -> str:
    raw = json.dumps(_semantic_projection(lex), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _tem_conhecimento(lex: dict) -> bool:
    if isinstance(lex.get("scopes"), dict) and lex.get("scopes"):
        return True
    for k in ("blacklist", "equiv", "indefinidos", "quarentena",
              "equiv_legado_global", "indefinidos_legado_global", "legado_sem_escopo"):
        v = lex.get(k)
        if isinstance(v, dict) and v:
            return True
        if isinstance(v, list) and v:
            return True
    return False


def _normalizar_meta(lex: dict) -> dict:
    """Compatibilidade com 3.2.4 e anteriores sem reescrever o arquivo na leitura."""
    revision = int(lex.get("revision", lex.get("versao", 0)) or 0)
    if "state_version" in lex:
        sv = int(lex.get("state_version", 0) or 0)
    else:
        # Arquivo legado não dizia quantas mudanças semânticas ocorreram. Em vez
        # de inventar uma contagem, inauguramos a linhagem em 1 quando já há
        # conhecimento e 0 quando o léxico está vazio.
        sv = 1 if _tem_conhecimento(lex) else 0
    lex["revision"] = revision
    lex["state_version"] = sv
    lex["state_sha256"] = state_sha256(lex)
    # ``versao`` permanece como alias DEPRECADO de revisão física para leitores
    # antigos. Novos consumidores devem usar state_version.
    lex["versao"] = revision
    return lex


def carregar_travado(path: str):
    """Lê estado + hashes físico/semântico sob o MESMO lock."""
    with travar(path):
        file_sha = _sha256_file(path)
        lex = _normalizar_meta(LS.carregar(path))
        info = {
            "sha256": file_sha,                         # compat: hash físico
            "file_sha256": file_sha,
            "versao": int(lex.get("revision", 0)),     # compat
            "revision": int(lex.get("revision", 0)),
            "state_version": int(lex.get("state_version", 0)),
            "state_sha256": lex.get("state_sha256") or state_sha256(lex),
        }
    return lex, info


def _agora() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def salvar_atomico(path: str, lex: dict, run_id: str = "", *,
                    semantic_changed: bool, state_sha: str) -> None:
    """Grava uma revisão; ``state_version`` só cresce se o conhecimento mudou."""
    _normalizar_meta(lex)
    lex["revision"] = int(lex.get("revision", 0)) + 1
    lex["versao"] = lex["revision"]  # compatibilidade legada
    if semantic_changed:
        lex["state_version"] = int(lex.get("state_version", 0)) + 1
    lex["state_sha256"] = state_sha
    lex["atualizado_em"] = _agora()
    hist = lex.setdefault("runs", [])
    hist.append({
        "run_id": run_id,
        "quando": lex["atualizado_em"],
        "revision": lex["revision"],
        "versao": lex["revision"],             # compat
        "state_version": lex["state_version"],
        "state_sha256": state_sha,
        "state_changed": bool(semantic_changed),
        "evento": "LEXICO_STATE_CHANGED" if semantic_changed else "LEXICO_NOOP",
        "ativos": LS.contar_ativos(lex),
        "contextos": sum(len(sd.get("equiv", {})) for sd in lex.get("scopes", {}).values()
                         if isinstance(sd, dict)),
        "indefinidos": LS.contar_indefinidos(lex, apenas_abertos=True),
        "quarentena": sum(len(sd.get("quarentena", {})) for sd in lex.get("scopes", {}).values()
                         if isinstance(sd, dict)),
    })
    del hist[:-500]
    _gravar(path, lex)


def _gravar(path: str, lex: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{time.time_ns()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lex, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    # Durabilidade do rename após perda de energia: fsync do diretório pai.
    try:
        dfd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:
        pass


def atualizar_travado(path: str, achados: list, min_support: int = 2,
                      raio_independencia: float = LS.RAIO_INDEPENDENCIA_M,
                      run_id: str = "", scope_id: str = LS.DEFAULT_SCOPE):
    """Read-modify-write sob lock com linhagem física E semântica exata."""
    with travar(path):
        file_sha_before = _sha256_file(path)

        # A migração 3.2.x -> 3.3 muda a semântica de aplicação (regras sem
        # scope deixam de autoaplicar). Logo ela é um evento semântico real e
        # precisa incrementar state_version, mesmo que não haja novos achados.
        raw = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except Exception as e:
                raise RuntimeError(f"LEXICO_CORRUPTO: {path}: {e.__class__.__name__}: {e}") from e
            if not isinstance(raw, dict):
                raise RuntimeError(f"LEXICO_CORRUPTO: {path}: raiz JSON deve ser objeto")
        schema_before = str(raw.get("schema") or "LEGACY") if raw else "EMPTY"
        migration_from = schema_before if raw and schema_before != "3.3" else None
        revision_before = int(raw.get("revision", raw.get("versao", 0)) or 0) if raw else 0
        if raw and "state_version" in raw:
            state_version_before = int(raw.get("state_version", 0) or 0)
        else:
            state_version_before = 1 if raw and _tem_conhecimento(raw) else 0
        # Hash do estado REAL persistido antes da migração, usando a mesma
        # projeção semântica. Isso encadeia corretamente a última revisão 3.2.x
        # com o primeiro commit 3.3.
        state_sha_before = state_sha256(raw) if raw else state_sha256({})

        lex = _normalizar_meta(LS.migrar_schema(raw if raw else {}))
        lex["revision"] = revision_before
        lex["versao"] = revision_before
        lex["state_version"] = state_version_before

        novos = LS.atualizar(lex, achados, min_support=min_support,
                             raio_independencia=raio_independencia,
                             scope_id=scope_id, run_id=run_id)
        state_sha_after = state_sha256(lex)
        semantic_changed = bool(migration_from) or state_sha_after != state_sha_before
        salvar_atomico(path, lex, run_id=run_id, semantic_changed=semantic_changed,
                       state_sha=state_sha_after)
        file_sha_after = _sha256_file(path)
        info = {
            "run_id": run_id,
            # aliases 3.2.4: hashes/versões físicos, NÃO cadeia semântica
            "sha256_before": file_sha_before,
            "sha256_after": file_sha_after,
            "versao_before": revision_before,
            "versao_after": int(lex.get("revision", 0)),
            # nomes explícitos novos
            "file_sha256_before": file_sha_before,
            "file_sha256_after": file_sha_after,
            "revision_before": revision_before,
            "revision_after": int(lex.get("revision", 0)),
            "state_version_before": state_version_before,
            "state_version_after": int(lex.get("state_version", 0)),
            "state_sha256_before": state_sha_before,
            "state_sha256_after": state_sha_after,
            "state_changed": bool(semantic_changed),
            "migration_from": migration_from,
            "schema_before": schema_before,
            "schema_after": "3.3",
            "committed_at": lex.get("atualizado_em", ""),
        }
    return lex, novos, info


def marcar_run_status(path: str, run_id: str, status: str, detalhe: str = "") -> bool:
    """DEPRECADO v3.2.5: status operacional não pertence ao estado do léxico.

    Mantido como no-op para não quebrar chamadores legados. RUN_COMPLETED/FAILED
    deve ser lido em ``runs/<run_id>/execucao.json`` ou ``failed/<run_id>``.
    """
    return False
