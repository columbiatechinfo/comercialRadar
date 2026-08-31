# -*- coding: utf-8 -*-
"""minerador_worker.py — consome a fila de jobs e roda a mineração.

POR QUE ELE EXISTE, e o que ele substitui.

Até 31/08/2026 a API dava `subprocess.Popen(["python", "minerar_tudo.py", ...])`
e acompanhava pelo cano do stdout, com o estado do job num dicionário na memória
do servidor. Isso funcionava enquanto API e mineração eram o mesmo processo.

Elas deixaram de ser: a API sobe `read_only` como o resto da stack, e dez
Chromium não cabem num contêiner somente-leitura. São dois contêineres, e um
`Popen` não atravessa essa fronteira.

O QUE ESTE ARQUIVO **NÃO** É: ele não sabe minerar. Quem minera continua sendo o
`minerar_tudo.py`, sem uma linha alterada. Este é o carregador — pega o próximo
da fila, dispara, e devolve ao banco o que o processo disser. Reescrever a
mineração para "adaptar à fila" seria trocar de lugar E de comportamento no
mesmo commit, e aí um defeito novo não teria de onde ser distinguido.

USO
    python minerador_worker.py            # laço, é o CMD do contêiner
    python minerador_worker.py --saude    # healthcheck: 0 se o laço está vivo
    python minerador_worker.py --uma-vez  # pega um job, roda, sai (depuração)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time

import config  # noqa: F401  — carrega o .env
import psycopg2

import realtime_ingest

PYTHON = sys.executable
EU = os.environ.get("HOSTNAME") or socket.gethostname()

#: De quanto em quanto tempo o laço olha a fila quando ela está vazia.
#:
#: Não é `LISTEN/NOTIFY` de propósito: o pooler em modo transação devolve a
#: conexão a cada transação, e uma sessão que ouve não sobrevive a isso. E não é
#: 1 segundo: a fila enche por ação humana no painel, e três segundos de espera
#: não são percebidos por quem acabou de clicar.
ESPERA_S = 3

#: Job `rodando` cujo `visto_em` passou disto é worker morto — o contêiner caiu,
#: ou o Chromium pendurou o processo. Nesse caso o job volta para a fila.
#:
#: Generoso de propósito: a captura passa minutos sem produzir linha, e um limite
#: apertado devolveria à fila um job perfeitamente vivo — que então rodaria duas
#: vezes, que é pior que demorar.
MORTO_APOS_S = 15 * 60


# ──────────────────────────────────────────────────────────────────────────
# Conversa com o banco
# ──────────────────────────────────────────────────────────────────────────
def _conectar():
    """Conexão do worker. Sem identidade ainda: ela vem do job."""
    return realtime_ingest.conectar()


def _assumir(cur, pedido_por: str) -> None:
    """Passa a gravar como quem pediu o job.

    É isto que faz o resultado nascer na empresa certa. `core.empresa_atual()`
    resolve pelo uuid do usuário, e o gatilho `preencher_empresa` carimba cada
    INSERT — inclusive os que o `minerar_tudo` faz nos seus subprocessos, porque
    a variável vai no ambiente deles também.
    """
    cur.execute("select set_config('request.jwt.claim.sub', %s, false)",
                (str(pedido_por),))


def pegar_job(con):
    """O próximo da fila, travado para este worker. `None` se não há.

    `for update skip locked` é o que permite mais de um worker sem coordenação
    externa: quem chega junto pula a linha travada em vez de esperar por ela. Sem
    o `skip locked`, dois workers serializariam na mesma linha e o segundo ficaria
    parado até o primeiro terminar o job inteiro.
    """
    with con.cursor() as cur:
        cur.execute("""
            update job set estado = 'rodando',
                           worker = %s,
                           iniciado_em = now(),
                           visto_em = now()
             where id = (select id from job
                          where estado = 'fila'
                          order by criado_em
                          limit 1
                          for update skip locked)
         returning id, id_empresa, pedido_por, tipo, argumentos""", (EU,))
        r = cur.fetchone()
    con.commit()
    if not r:
        return None
    return {"id": r[0], "id_empresa": r[1], "pedido_por": r[2],
            "tipo": r[3], "argumentos": r[4] or {}}


def devolver_orfaos(con) -> int:
    """Job `rodando` sem sinal de vida volta para a fila.

    O CASO QUE ISTO COBRE é o que mais custou até hoje: o processo morre e o
    registro fica dizendo "rodando" para sempre. O painel mostra uma rodada em
    curso que não existe, e ninguém dispara outra porque parece haver uma.
    """
    with con.cursor() as cur:
        cur.execute("""
            update job
               set estado = 'fila', worker = null,
                   iniciado_em = null, visto_em = null
             where estado = 'rodando'
               and visto_em < now() - make_interval(secs => %s)
         returning id""", (MORTO_APOS_S,))
        ids = [r[0] for r in cur.fetchall()]
    con.commit()
    return ids


def registrar(con, id_job: int, linha: str) -> None:
    with con.cursor() as cur:
        cur.execute("insert into job_log (id_job, linha) values (%s, %s)",
                    (id_job, linha[:4000]))
    con.commit()


def bater_ponto(con, id_job: int, progresso: dict | None = None) -> bool:
    """Diz que este worker continua vivo. Devolve `False` se pediram cancelamento."""
    with con.cursor() as cur:
        if progresso is None:
            cur.execute("update job set visto_em = now() where id = %s "
                        "returning cancelar_pedido", (id_job,))
        else:
            cur.execute("update job set visto_em = now(), progresso = %s "
                        "where id = %s returning cancelar_pedido",
                        (json.dumps(progresso), id_job))
        r = cur.fetchone()
    con.commit()
    return not (r and r[0])


def encerrar(con, id_job: int, codigo: int, erro: str | None = None) -> None:
    estado = "ok" if codigo == 0 else ("cancelado" if codigo == -9 else "erro")
    with con.cursor() as cur:
        cur.execute("""update job set estado = %s, codigo_saida = %s,
                              erro = %s, terminado_em = now()
                        where id = %s""", (estado, codigo, erro, id_job))
    con.commit()


# ──────────────────────────────────────────────────────────────────────────
# Rodar o job
# ──────────────────────────────────────────────────────────────────────────
def _comando(job: dict) -> list:
    """A linha de comando do job. O `minerar_tudo` não mudou para caber aqui."""
    a = job["argumentos"]
    if job["tipo"] == "mineracao":
        cmd = [PYTHON, "minerar_tudo.py"]
        for chave, valor in a.items():
            if valor is None or valor is False:
                continue
            cmd.append("--" + chave.replace("_", "-"))
            if valor is not True:
                cmd.append(str(valor))
        return cmd
    if job["tipo"] == "planilha":
        return [PYTHON, "cadastro_cliente.py", "--importar",
                str(a.get("arquivo", ""))]
    raise ValueError("tipo de job desconhecido: %r" % job["tipo"])


def rodar(con, job: dict) -> None:
    id_job = job["id"]
    cmd = _comando(job)
    registrar(con, id_job, "▶ " + " ".join(cmd[1:]))

    # A IDENTIDADE VAI NO AMBIENTE DO SUBPROCESSO, e é isso que faz o resultado
    # nascer na empresa certa. O `minerar_tudo` abre a própria conexão; sem a
    # variável, `core.empresa_atual()` voltaria nulo lá dentro e o gatilho não
    # teria o que carimbar — o `not null` recusaria cada linha, e a rodada
    # terminaria com zero POIs e nenhum erro que apontasse para cá.
    env = dict(os.environ,
               PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               RADAR_USUARIO_SERVICO=str(job["pedido_por"]))

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)

    # O PONTO BATE NUMA THREAD, e não entre as linhas do log.
    #
    # A captura passa minutos sem imprimir nada. Se o ponto dependesse de sair
    # linha, um job perfeitamente vivo seria dado como morto e voltaria para a
    # fila — e aí rodaria duas vezes, que é pior que demorar.
    parar = threading.Event()

    def _ponto():
        c2 = _conectar()
        try:
            while not parar.wait(30):
                if not bater_ponto(c2, id_job):
                    registrar(c2, id_job, "⏹ cancelamento pedido — encerrando")
                    proc.terminate()
                    return
        finally:
            c2.close()

    threading.Thread(target=_ponto, daemon=True).start()

    try:
        for linha in proc.stdout:
            linha = linha.rstrip()
            if linha:
                registrar(con, id_job, linha)
                print(linha, flush=True)
        codigo = proc.wait()
    finally:
        parar.set()

    encerrar(con, id_job, codigo)
    registrar(con, id_job, "■ terminou com código %d" % codigo)


# ──────────────────────────────────────────────────────────────────────────
# O laço
# ──────────────────────────────────────────────────────────────────────────
def laco(uma_vez: bool = False) -> int:
    con = _conectar()
    print(f"[worker {EU}] ouvindo a fila", flush=True)
    try:
        while True:
            for i in devolver_orfaos(con):
                print(f"[worker] job {i} voltou para a fila: worker sem sinal "
                      f"de vida ha mais de {MORTO_APOS_S // 60} min", flush=True)

            job = pegar_job(con)
            if not job:
                if uma_vez:
                    return 0
                time.sleep(ESPERA_S)
                continue

            print(f"[worker] job {job['id']} ({job['tipo']})", flush=True)
            with con.cursor() as cur:
                _assumir(cur, job["pedido_por"])
            con.commit()
            try:
                rodar(con, job)
            except Exception as erro:                            # noqa: BLE001
                # O JOB NÃO PODE FICAR `rodando` PARA SEMPRE. Uma exceção aqui
                # já derrubou o laço e deixou o registro travado — o painel
                # mostrava uma rodada em curso que não existia, e ninguém
                # disparava outra porque parecia haver uma.
                registrar(con, job["id"], "✗ %s: %s" % (type(erro).__name__, erro))
                encerrar(con, job["id"], 1, "%s: %s" % (type(erro).__name__, erro))
                print(f"[worker] job {job['id']} falhou: {erro}", flush=True)
            if uma_vez:
                return 0
    finally:
        con.close()


def saude() -> int:
    """0 se o banco responde e a fila é legível. É o healthcheck do contêiner.

    Confere o BANCO, e não só o processo: um worker que perdeu o banco continua
    vivo e não consome nada — de fora, indistinguível de uma fila vazia.
    """
    try:
        con = _conectar()
        try:
            with con.cursor() as cur:
                cur.execute("select count(*) from job where estado = 'fila'")
                cur.fetchone()
        finally:
            con.close()
        return 0
    except Exception as erro:                                    # noqa: BLE001
        print("worker sem banco: %s: %s" % (type(erro).__name__, erro),
              file=sys.stderr)
        return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--saude", action="store_true",
                   help="responde 0 se o banco esta alcancavel")
    p.add_argument("--uma-vez", dest="uma_vez", action="store_true",
                   help="pega um job, roda e sai")
    a = p.parse_args(argv)
    if a.saude:
        return saude()
    return laco(a.uma_vez)


if __name__ == "__main__":
    raise SystemExit(main())
