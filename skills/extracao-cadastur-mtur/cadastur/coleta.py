"""Download retomavel, atomico e verificavel. Cache por recurso_id."""
from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

from .catalogo import UA, Recurso, como_dict

TENTATIVAS = 5


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar(rec: Recurso, cache: Path, refresh: bool = False) -> dict:
    """Arquivo parcial/corrompido nunca vira sucesso; sidecar e' escrito atomicamente."""
    cache.mkdir(parents=True, exist_ok=True)
    alvo = cache / rec.recurso_id
    side = cache / f"{rec.recurso_id}.json"
    if side.exists() and alvo.exists() and not refresh:
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            tamanho_ok = meta.get("bytes") == alvo.stat().st_size
            hash_ok = bool(meta.get("sha256")) and meta["sha256"] == _sha256(alvo)
            if tamanho_ok and hash_ok:
                return meta | {"cache": True, "cache_integridade": "sha256_ok"}
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    tmp = cache / f"{rec.recurso_id}.parcial"
    side_tmp = cache / f"{rec.recurso_id}.json.parcial"
    erro: Exception | None = None
    for i in range(TENTATIVAS):
        try:
            req = urllib.request.Request(rec.url, headers=UA)
            with urllib.request.urlopen(req, timeout=600) as r, tmp.open("wb") as f:
                while True:
                    bloco = r.read(1 << 20)
                    if not bloco:
                        break
                    f.write(bloco)
            if tmp.stat().st_size == 0:
                raise RuntimeError("resposta vazia")
            break
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            erro = e
            tmp.unlink(missing_ok=True)
            if i + 1 < TENTATIVAS:
                time.sleep((2 ** i) + random.random())
    else:
        raise RuntimeError(f"falha ao baixar {rec.recurso_id} ({rec.recurso_nome}): {erro}")

    sha = _sha256(tmp)
    tmp.replace(alvo)
    meta = como_dict(rec) | {
        "bytes": alvo.stat().st_size,
        "sha256": sha,
        "coletado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arquivo": str(alvo),
        "cache": False,
        "cache_integridade": "download_sha256",
    }
    side_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    side_tmp.replace(side)
    return meta
