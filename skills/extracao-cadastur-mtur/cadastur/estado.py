"""Estado transacional e retomada de execucoes.

SQLite e usado apenas como journal de controle; o dado bruto continua em Parquet.
Uma execucao interrompida pode ser retomada sem reler recursos ja materializados.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path


def scope_hash(recursos, filtros: dict) -> str:
    """Identidade logica do recorte; permanece estavel quando o portal atualiza um recurso."""
    payload = {"filtros": filtros, "recursos": [r.recurso_id for r in recursos]}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def catalog_hash(recursos) -> str:
    """Fingerprint do catalogo usado para impedir retomada sobre fonte modificada."""
    payload = [{
        "id": r.recurso_id, "url": r.url, "formato": r.formato_ckan,
        "modificado": getattr(r, "recurso_modificado_em", ""),
        "criado": getattr(r, "recurso_criado_em", ""),
        "periodo": r.ref_periodo,
    } for r in recursos]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Estado:
    def __init__(self, base: Path):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)
        self.db = self.base / "estado.sqlite"
        self.cx = sqlite3.connect(self.db)
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.executescript("""
        CREATE TABLE IF NOT EXISTS execucao (
          run_id TEXT PRIMARY KEY,
          scope_hash TEXT NOT NULL,
          catalog_hash TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL,
          iniciado_em TEXT NOT NULL,
          finalizado_em TEXT,
          versao TEXT NOT NULL,
          erro TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_exec_scope_status
          ON execucao(scope_hash,status,iniciado_em DESC);
        CREATE TABLE IF NOT EXISTS recurso (
          run_id TEXT NOT NULL,
          recurso_id TEXT NOT NULL,
          status TEXT NOT NULL,
          sha256 TEXT,
          linhas_origem INTEGER,
          linhas_saida INTEGER,
          shard TEXT,
          meta_json TEXT,
          atualizado_em TEXT NOT NULL,
          erro TEXT,
          PRIMARY KEY (run_id,recurso_id),
          FOREIGN KEY (run_id) REFERENCES execucao(run_id)
        );
        """)
        cols = {r[1] for r in self.cx.execute("PRAGMA table_info(execucao)")}
        if "catalog_hash" not in cols:
            self.cx.execute("ALTER TABLE execucao ADD COLUMN catalog_hash TEXT NOT NULL DEFAULT ''")
        self.cx.commit()

    def iniciar_ou_retomar(self, escopo: str, catalogo: str, versao: str, forcar_nova: bool = False) -> tuple[str, bool]:
        if not forcar_nova:
            row = self.cx.execute(
                "SELECT run_id FROM execucao WHERE scope_hash=? AND catalog_hash=? AND status='EM_ANDAMENTO' "
                "ORDER BY iniciado_em DESC LIMIT 1", (escopo, catalogo)
            ).fetchone()
            if row:
                return row[0], True
        run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.cx.execute(
            "INSERT INTO execucao(run_id,scope_hash,catalog_hash,status,iniciado_em,versao) VALUES(?,?,?,?,?,?)",
            (run_id, escopo, catalogo, "EM_ANDAMENTO", time.strftime("%Y-%m-%dT%H:%M:%S%z"), versao),
        )
        self.cx.commit()
        return run_id, False

    def concluido(self, run_id: str, recurso_id: str) -> dict | None:
        row = self.cx.execute(
            "SELECT status,sha256,linhas_origem,linhas_saida,shard,meta_json "
            "FROM recurso WHERE run_id=? AND recurso_id=?", (run_id, recurso_id)
        ).fetchone()
        if not row or row[0] != "OK":
            return None
        return {
            "sha256": row[1], "linhas_origem": row[2], "linhas_saida": row[3],
            "shard": row[4], "meta": json.loads(row[5] or "{}"),
        }

    def marcar_ok(self, run_id: str, recurso_id: str, *, sha256: str, linhas_origem: int,
                  linhas_saida: int, shard: Path, meta: dict) -> None:
        self.cx.execute("""
        INSERT INTO recurso(run_id,recurso_id,status,sha256,linhas_origem,linhas_saida,shard,meta_json,atualizado_em,erro)
        VALUES(?,?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(run_id,recurso_id) DO UPDATE SET
          status=excluded.status,sha256=excluded.sha256,linhas_origem=excluded.linhas_origem,
          linhas_saida=excluded.linhas_saida,shard=excluded.shard,meta_json=excluded.meta_json,
          atualizado_em=excluded.atualizado_em,erro=NULL
        """, (run_id, recurso_id, "OK", sha256, linhas_origem, linhas_saida, str(shard),
              json.dumps(meta, ensure_ascii=False), time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        self.cx.commit()

    def invalidar_recurso(self, run_id: str, recurso_id: str, motivo: str) -> None:
        self.cx.execute(
            "UPDATE recurso SET status='INVALIDO',erro=?,atualizado_em=? WHERE run_id=? AND recurso_id=?",
            (motivo[:4000], time.strftime("%Y-%m-%dT%H:%M:%S%z"), run_id, recurso_id),
        )
        self.cx.commit()

    def marcar_erro(self, run_id: str, recurso_id: str, erro: str) -> None:
        self.cx.execute("""
        INSERT INTO recurso(run_id,recurso_id,status,atualizado_em,erro)
        VALUES(?,?,?,?,?)
        ON CONFLICT(run_id,recurso_id) DO UPDATE SET status='ERRO',atualizado_em=excluded.atualizado_em,erro=excluded.erro
        """, (run_id, recurso_id, "ERRO", time.strftime("%Y-%m-%dT%H:%M:%S%z"), erro[:4000]))
        self.cx.commit()


    def execucao_anterior_ok(self, escopo: str, run_id_atual: str) -> str | None:
        row = self.cx.execute(
            "SELECT run_id FROM execucao WHERE scope_hash=? AND status='OK' AND run_id<>? "
            "ORDER BY finalizado_em DESC LIMIT 1", (escopo, run_id_atual)
        ).fetchone()
        return row[0] if row else None

    def listar_ok(self, run_id: str) -> dict[str, dict]:
        rows = self.cx.execute(
            "SELECT recurso_id,sha256,linhas_origem,linhas_saida,shard,meta_json FROM recurso "
            "WHERE run_id=? AND status='OK' ORDER BY recurso_id", (run_id,)
        ).fetchall()
        return {r[0]: {"sha256": r[1], "linhas_origem": r[2], "linhas_saida": r[3],
                       "shard": r[4], "meta": json.loads(r[5] or "{}")} for r in rows}

    def pausar_retomavel(self, run_id: str, erro: str) -> None:
        self.cx.execute(
            "UPDATE execucao SET status='EM_ANDAMENTO',finalizado_em=NULL,erro=? WHERE run_id=?",
            (erro[:4000], run_id),
        )
        self.cx.commit()

    def finalizar(self, run_id: str, status: str, erro: str | None = None) -> None:
        self.cx.execute(
            "UPDATE execucao SET status=?,finalizado_em=?,erro=? WHERE run_id=?",
            (status, time.strftime("%Y-%m-%dT%H:%M:%S%z"), erro, run_id),
        )
        self.cx.commit()

    def close(self) -> None:
        self.cx.close()
