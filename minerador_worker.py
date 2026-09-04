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
import re
import socket
import shlex
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


def encerrar(con, id_job: int, codigo: int, erro: str | None = None,
             cancelado: bool = False) -> None:
    """Fecha o job. `cancelado` vem de quem parou, e nao do codigo de saida.

    O CODIGO DE SAIDA NAO SABE POR QUE MORREU. `proc.terminate()` manda SIGTERM
    e o processo sai com -15; a regra antiga so reconhecia -9 (SIGKILL), entao
    toda rodada que o operador mandou parar aparecia como ERRO. Medido em
    04/09/2026: as rodadas #3 e #4 foram canceladas por mim e o painel disse que
    tinham falhado.

    Quem sabe que foi cancelamento e quem PEDIU o cancelamento — a thread do
    ponto, que leu `cancelar_pedido` e chamou `terminate`. E ela que informa.
    """
    if cancelado:
        estado = "cancelado"
    else:
        estado = "ok" if codigo == 0 else ("cancelado" if codigo in (-9, -15)
                                           else "erro")
    with con.cursor() as cur:
        cur.execute("""update job set estado = %s, codigo_saida = %s,
                              erro = %s, terminado_em = now()
                        where id = %s""", (estado, codigo, erro, id_job))
    con.commit()


# ──────────────────────────────────────────────────────────────────────────
# Rodar o job
# ──────────────────────────────────────────────────────────────────────────
#: Quantos trabalhadores de navegador esta MÁQUINA aguenta, no máximo.
#:
#: O TETO É DA MÁQUINA, E NÃO DO PEDIDO, e essa é a distinção que faz a fila
#: poder ser atendida por hardware diferente. Quem clica no painel pede uma
#: rodada; ele não sabe — nem deveria saber — se ela vai cair no servidor de
#: 123 GB ou no notebook de 45. Se o pedido carregasse o número, uma rodada
#: pedindo 30 afogaria o notebook, e uma pedindo 10 desperdiçaria o servidor.
#:
#: MEDIDO em 04/09/2026: cada trabalhador custa ~430 MB e ~0,3 núcleo; trinta
#: levaram o contêiner a 13,8 GiB e 9,4 núcleos de 32. O servidor principal
#: (123 GB, 32 vCPU) fica em 30; o notebook (45 GB, 32 vCPU, com nominatim,
#: photon e dois OSRM de pé) fica em 10.
MAX_TRABALHADORES = int(os.environ.get("RADAR_MAX_TRABALHADORES") or 30)

#: As chaves que contam trabalhadores de navegador e por isso obedecem ao teto.
_CHAVES_DE_TRABALHADOR = ("workers", "capture_workers", "trabalhadores")


#: O comando que ACENDE UMA TELA onde nao ha nenhuma, e depois vira o pipeline.
#:
#: A ETAPA 4 ABRE NAVEGADOR COM JANELA, e nao ha janela num conteiner. O Maps
#: recusa o modo sem cabeca, entao a captura sobe Chromium `headless=False`; sem
#: `DISPLAY` ele morre em menos de um segundo com "Missing X server or $DISPLAY"
#: e a rodada segue sem a etapa mais cara que tem.
#:
#: FOI EXATAMENTE ISSO QUE ACONTECEU. Ate 31/08 quem disparava era a API, e ela
#: embrulhava o comando nisto (`server.TELA_VIRTUAL`). Quando a fila passou a
#: disparar, o embrulho ficou para tras — e as rodadas 3 a 8, em 04/09/2026, nas
#: duas maquinas, terminaram com "a etapa 4 terminou com codigo 1" e um `ok` no
#: painel. Rodada verde sem captura nenhuma.
#:
#: TRES DETALHES QUE ELE RESOLVE, e cada um ja custou uma sessao:
#:   · `xvfb-run` pendura quando o processo filho segura o terminal — daí o
#:     Xvfb ser subido a mao, em segundo plano;
#:   · o display `:99`, que e o padrao de todo exemplo, colide com o do host —
#:     daí procurar um livre entre `:200` e `:260` pelo arquivo de trava;
#:   · o `exec` do fim faz o python SUBSTITUIR o shell, e nao virar filho dele.
#:     Sem isso, `proc.terminate()` mata o `sh` e a mineracao continua rodando
#:     orfa — o painel diria "parada" com os navegadores ainda queimando IP.
TELA_VIRTUAL = (
    "mkdir -p /tmp/.X11-unix; D=0; for n in $(seq 200 260); do "
    "if [ ! -e /tmp/.X$n-lock ]; then "
    "Xvfb :$n -screen 0 1920x1080x24 -nolisten tcp > /tmp/xvfb.err 2>&1 & "
    "sleep 2; if [ -e /tmp/.X$n-lock ] && ! grep -q already /tmp/xvfb.err; "
    "then D=$n; break; fi; fi; done; export DISPLAY=:$D; exec "
)


def _com_tela(cmd: list) -> list:
    """Embrulha o comando na tela virtual, quando nao ha uma herdada.

    SEPARADO DE `_comando` DE PROPOSITO: o que vai para o log da rodada e a
    linha legivel (`minerar_tudo.py --area ...`), e nao trinta caracteres de
    shell. Quem le o log quer saber o que rodou, nao como a tela nasceu.

    Se a maquina ja entregou um `DISPLAY` — desenvolvimento com X de verdade,
    ou um conteiner com o socket montado — nao se mexe em nada.
    """
    if os.environ.get("DISPLAY"):
        return cmd
    return ["sh", "-c", TELA_VIRTUAL
            + " ".join(shlex.quote(p) for p in cmd)]


def _comando(job: dict) -> list:
    """A linha de comando do job. O `minerar_tudo` não mudou para caber aqui."""
    a = dict(job["argumentos"])
    if job["tipo"] == "mineracao":
        # O TETO APARADO É DITO, e não aplicado em silêncio. Uma rodada que
        # pediu 30 e rodou com 10 demora três vezes mais; quem for ler o tempo
        # depois precisa saber por quê, sem ter de descobrir em qual máquina
        # ela caiu.
        for chave in _CHAVES_DE_TRABALHADOR:
            try:
                pedido = int(a.get(chave))
            except (TypeError, ValueError):
                continue
            if pedido > MAX_TRABALHADORES:
                a[chave] = MAX_TRABALHADORES
                a.setdefault("_aparado", []).append(
                    "%s: %d → %d" % (chave, pedido, MAX_TRABALHADORES))
        aparado = a.pop("_aparado", None)
        if aparado:
            print("[%s] teto desta máquina aplicado — %s"
                  % (EU, "; ".join(aparado)), flush=True)
        # O AJUDANTE POR SSH SAIU DE CENA. `_acordar_predator` existia para a
        # etapa 4 mandar metade do trabalho para a outra maquina por SSH —
        # antes da fila. Hoje a outra maquina e um worker igual a este, que
        # pega o proprio job; o ajudante so produzia um aviso assustador em
        # toda rodada ("o Predator nao entrou") e, rodando NO notebook,
        # tentava abrir SSH para si mesmo.
        a.setdefault("sem_predator", True)
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


# ── ler o progresso das linhas que a mineração já imprime ──────────────────
#
# NADA MUDA NO `minerar_tudo.py`, e isso é regra desta casa: quem minera não
# aprende a falar com a fila. Ele já imprime tudo o que a barra precisa —
#
#     ▶ 4/10 Maps pelo placeId — abre cada ponto pelo id, sem OCR
#     ... célula 12/340 | ...
#
# — e o que faltava era alguém escutar. Antes disso a coluna `progresso` existia
# e ficava nula: o painel sabia que o job estava vivo e não sabia onde ele
# estava, o que na prática é uma barra que anda sozinha e não informa nada.
#
# DUAS ESCALAS, e as duas importam. A ETAPA (4 de 10) é o que responde "quanto
# falta para acabar"; o contador de dentro (célula 12 de 340) é o que responde
# "isto travou?". Uma barra só, com a etapa, fica parada dez minutos e parece
# pendurada; só com o contador, volta ao começo a cada etapa e parece regredir.
_RE_ETAPA = re.compile(r"^▶\s*(\d+)/(\d+)\s+(.+?)\s*$")
_RE_DENTRO = (
    re.compile(r"c[eé]lula\s+(\d+)/(\d+)"),
    re.compile(r"POIs\s+(\d+)/(\d+)"),
    re.compile(r"(\d+)/(\d+)\s*·\s*[\d.,]+\s*s/POI"),
    re.compile(r"[✅❌]\s+(\d+)/(\d+)"),
    re.compile(r"fotos\s+(\d+)/(\d+)"),
)


class Progresso:
    """O estado da barra, escrito pelo leitor de log e lido pela batida de ponto.

    As duas coisas rodam em threads diferentes de propósito — a captura passa
    minutos sem imprimir linha, e o ponto não pode depender disso. O dicionário
    é pequeno e as escritas são atômicas o bastante em CPython; um lock aqui
    custaria mais atenção do que compra.
    """

    def __init__(self):
        self.d = {"etapa": 0, "etapas": 0, "titulo": "", "feitos": 0,
                  "total": 0, "linha": ""}

    def ler(self, linha: str) -> None:
        m = _RE_ETAPA.match(linha)
        if m:
            self.d.update(etapa=int(m.group(1)), etapas=int(m.group(2)),
                          titulo=m.group(3)[:120], feitos=0, total=0)
            return
        for r in _RE_DENTRO:
            m = r.search(linha)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                # O CONTADOR NÃO ANDA PARA TRÁS dentro da mesma etapa. Várias
                # linhas diferentes casam, e uma que reporte um lote menor faria
                # a barra recuar — que é o único jeito de uma barra mentir de
                # um jeito que o usuário percebe na hora.
                if b == self.d.get("total"):
                    self.d["feitos"] = max(self.d.get("feitos", 0), a)
                else:
                    self.d["feitos"], self.d["total"] = a, b
                break
        if linha.strip():
            self.d["linha"] = linha.strip()[:200]


def rodar(con, job: dict) -> None:
    id_job = job["id"]
    cmd = _comando(job)
    registrar(con, id_job, "▶ " + " ".join(cmd[1:]))
    registrar(con, id_job, "  worker %s · teto de %d trabalhador(es) nesta máquina"
              % (EU, MAX_TRABALHADORES))

    # A IDENTIDADE VAI NO AMBIENTE DO SUBPROCESSO, e é isso que faz o resultado
    # nascer na empresa certa. O `minerar_tudo` abre a própria conexão; sem a
    # variável, `core.empresa_atual()` voltaria nulo lá dentro e o gatilho não
    # teria o que carimbar — o `not null` recusaria cada linha, e a rodada
    # terminaria com zero POIs e nenhum erro que apontasse para cá.
    env = dict(os.environ,
               PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               RADAR_USUARIO_SERVICO=str(job["pedido_por"]))

    proc = subprocess.Popen(_com_tela(cmd), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)

    # O PONTO BATE NUMA THREAD, e não entre as linhas do log.
    #
    # A captura passa minutos sem imprimir nada. Se o ponto dependesse de sair
    # linha, um job perfeitamente vivo seria dado como morto e voltaria para a
    # fila — e aí rodaria duas vezes, que é pior que demorar.
    parar = threading.Event()
    prog = Progresso()
    #: Marcado pela thread do ponto quando ELA manda encerrar. E o unico lugar
    #: que sabe distinguir "o operador parou" de "o processo quebrou".
    pedido_de_cancelamento = threading.Event()

    def _ponto():
        c2 = _conectar()
        try:
            while not parar.wait(30):
                if not bater_ponto(c2, id_job, dict(prog.d)):
                    pedido_de_cancelamento.set()
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
                prog.ler(linha)
                registrar(con, id_job, linha)
                print(linha, flush=True)
        codigo = proc.wait()
    finally:
        parar.set()

    # O ÚLTIMO PONTO ANTES DE ENCERRAR. Sem ele a barra congela no penúltimo
    # tique de 30 s, e uma rodada que terminou aparece como "9 de 10".
    try:
        bater_ponto(con, id_job, dict(prog.d))
    except Exception:                                          # noqa: BLE001
        pass
    encerrar(con, id_job, codigo, cancelado=pedido_de_cancelamento.is_set())
    registrar(con, id_job, "■ %s (código %d)"
              % ("cancelada a pedido" if pedido_de_cancelamento.is_set()
                 else "terminou", codigo))


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
