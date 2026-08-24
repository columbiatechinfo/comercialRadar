"""Acesso ao DGX Spark — o servidor de LLM.

Existe porque `OLLAMA_NUM_PARALLEL` e variavel do SERVICO: nao ha rota na API do
Ollama que a mude. Sem uma via de shell, a maquina fica presa na configuracao
com que subiu e nao ha como medir o ganho de lote.

Credenciais vem do .env, nunca do codigo.
"""
from __future__ import annotations

import os
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def _env() -> dict:
    d = {}
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, _, v = linha.partition("=")
                d[k.strip()] = v.strip()
    return d


def conectar():
    import paramiko

    e = _env()
    host = os.environ.get("SPARK_HOST") or e.get("SPARK_HOST")
    user = os.environ.get("SPARK_USER") or e.get("SPARK_USER", "root")
    senha = os.environ.get("SPARK_PASS") or e.get("SPARK_PASS")
    if not host or not senha:
        raise RuntimeError("SPARK_HOST/SPARK_PASS ausentes no .env")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # CHAVE PRIMEIRO. A senha fica como reserva porque o servico pode ser
    # reinstalado e perder o `authorized_keys` — foi o que aconteceu quando o
    # instalador do Ollama recriou o usuario do servico.
    try:
        c.connect(host, username=user, timeout=20)
        return c
    except paramiko.SSHException:
        if not senha:
            raise
    c.connect(host, username=user, password=senha, timeout=20)
    return c


def rodar(cliente, comando: str, segundos: int = 120) -> tuple[int, str, str]:
    _, saida, erro = cliente.exec_command(comando, timeout=segundos)
    fora = saida.read().decode(errors="replace")
    err = erro.read().decode(errors="replace")
    return saida.channel.recv_exit_status(), fora, err


if __name__ == "__main__":
    import sys

    c = conectar()
    try:
        cod, fora, err = rodar(c, sys.argv[1] if len(sys.argv) > 1 else "uname -a")
        print(fora or err, end="")
        sys.exit(cod)
    finally:
        c.close()
