# -*- coding: utf-8 -*-
"""validacao_executor.py — o executor das tarefas de validação, um por máquina (dono do produto, 16/09/2026).

Pega as tarefas de `validacao_tarefa` do SEU ambiente que cabem nos tetos globais (`validacao.pegar`) e roda cada uma
num contêiner próprio, com nome próprio — os mesmos comandos, imagens e montagens do orquestrador de Canoas
(`scripts/producao_canoas/orquestrador_julgamento.sh`), agora por tarefa e sem cidade fixa.

O CONTÊINER RODA DESTACADO (`docker run -d`), e isso é o que deixa o executor cair sem perder trabalho: ao voltar,
ele acha os contêineres da máquina pela etiqueta, continua dando sinal das tarefas deles e colhe o código de saída
quando terminam. Máquina desligada não dá sinal; em `validacao.SEM_SINAL_MIN` a tarefa volta para a fila e outra
máquina pega.

Variáveis (compose `deploy/compose.radar-validacao.yml`):
    RADAR_AMBIENTE             producao | desenvolvimento — só pega tarefas deste
    RADAR_MAQUINA              nome desta máquina (i9, notebook); vai para `validacao_tarefa.worker`
    RADAR_VALIDACAO_REPO       o repositório NO HOST (quem monta é o daemon, não este processo)
    RADAR_VALIDACAO_DADOS      a pasta dos lotes e logs NO HOST, montada aqui no mesmo caminho
    RADAR_VALIDACAO_ETAPAS     etapas que esta máquina aceita, separadas por vírgula (vazio = todas)
    RADAR_VALIDACAO_VAGAS      tarefas ao mesmo tempo nesta máquina
    RADAR_VALIDACAO_DB_HOST    `A2L_DB_HOST` passado às tarefas (o i9 pela LAN)
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_comum as bc  # noqa: E402
import validacao as va  # noqa: E402

AMBIENTE = va.AMBIENTE
CURTO = "d" if AMBIENTE == "desenvolvimento" else "p"
MAQUINA = (os.environ.get("RADAR_MAQUINA") or "i9").strip()
REPO = os.environ.get("RADAR_VALIDACAO_REPO", "").strip()
DADOS = os.environ.get("RADAR_VALIDACAO_DADOS", "").strip()
ACEITAS = [x.strip() for x in (os.environ.get("RADAR_VALIDACAO_ETAPAS") or "").split(",") if x.strip()]
VAGAS = int(os.environ.get("RADAR_VALIDACAO_VAGAS") or 4)
DB_HOST = os.environ.get("RADAR_VALIDACAO_DB_HOST", "").strip()
ENV_FILE = os.environ.get("RADAR_VALIDACAO_ENV_FILE") or "/app/.env"
PAUSA = 10
PARAR = False


def _log(m):
    print("%s %s" % (time.strftime("%d/%m %H:%M:%S"), m), flush=True)


def docker(*args, timeout=120):
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _pasta(vid):
    return os.path.join(DADOS, "v%d" % vid)


def _rotulo(t):
    return "L%03d" % t["lote"]


def nomes(t):
    e = va.ETAPAS[t["etapa"]]
    base = "radar-val-%s-%d-%s" % (CURTO, t["id"], t["etapa"])
    return [base] if e.partes == 1 else ["%s-%d" % (base, k) for k in range(e.partes)]


def comando(t, k, nome):
    """O `docker run` de uma parte da tarefa — o mesmo do orquestrador de Canoas."""
    e = va.ETAPAS[t["etapa"]]
    pasta = _pasta(t["validacao"])
    lote = _rotulo(t)
    arq = "/o/%s.txt" % lote
    log = "/o/%s_%s%s.log" % (lote, t["etapa"], ("_%d" % k) if e.partes > 1 else "")
    cmd = e.cmd.format(arq=arq, pasta="/o", lote=lote, k=k)
    if e.sonda:
        # A SONDA ANTES DA BUSCA (orquestrador): o DuckDuckGo que não responde vira 3 tentativas com 5 min entre elas
        sonda = cmd.split(" --ligacoes-arquivo")[0] + " --sonda"
        corpo = ("for t in 1 2 3; do if %s > /o/%s_sonda.log 2>&1; then exec %s > %s 2>&1; fi; sleep 300; done; "
                 "echo '■ busca web: a sonda não respondeu 3 vezes' > %s; exit 3" % (sonda, lote, cmd, log, log))
    else:
        corpo = "exec %s > %s 2>&1" % (cmd, log)
    run = ["run", "-d", "--init", "--name", nome, "--network", "host",
           "--label", "radar.validacao=%s" % AMBIENTE, "--label", "radar.validacao.maquina=%s" % MAQUINA,
           "--label", "radar.validacao.tarefa=%d" % t["id"],
           "--env-file", ENV_FILE,
           "-e", "PYTHONIOENCODING=utf-8", "-e", "PYTHONDONTWRITEBYTECODE=1",
           "-e", "RADAR_CONEXOES=%d" % e.conexoes, "-e", "RADAR_AMBIENTE=%s" % AMBIENTE,
           "-e", "CR_TENANT_ID=%s" % t["empresa"]]
    if DB_HOST:
        run += ["-e", "A2L_DB_HOST=%s" % DB_HOST]
    if e.imagem == va.IMG:
        run += ["-e", "HOME=/tmp"]
    if e.root:
        run += ["--user", "0"]
    if e.shm:
        run += ["--shm-size", e.shm]
    run += ["-v", "%s:/app%s" % (REPO, "" if e.escreve_repo else ":ro")]
    if e.escreve_repo:
        run += ["-v", "/app/node_modules"]
    run += ["-v", "%s:/o" % pasta, "-w", "/app", e.imagem, "sh", "-c", corpo]
    return run


def iniciar(con, t):
    pasta = _pasta(t["validacao"])
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, "%s.txt" % _rotulo(t)), "w") as f:
        f.write("".join("%s\n" % x for x in t["ligacoes"]))
    for k, nome in enumerate(nomes(t)):
        docker("rm", "-f", nome)
        rc, out, err = docker(*comando(t, k, nome))
        if rc != 0:
            for n in nomes(t):
                docker("rm", "-f", n)
            r = va.encerrar(con, t, None, erro="o contêiner não subiu: %s" % (err or out)[:300])
            _log("✗ #%d %s lote %d: o contêiner não subiu (%s) → %s" % (t["id"], t["etapa"], t["lote"], err[:120], r))
            return False
    _log("▶ #%d %s · validação %d · lote %d · %d ligações · tentativa %d"
         % (t["id"], t["etapa"], t["validacao"], t["lote"], len(t["ligacoes"]), t["tentativas"]))
    return True


def _resumo(t):
    """A última linha de placar (■ ou ▶) dos logs da tarefa."""
    e = va.ETAPAS[t["etapa"]]
    pasta = _pasta(t["validacao"])
    linhas = []
    for k in range(e.partes):
        caminho = os.path.join(pasta, "%s_%s%s.log" % (_rotulo(t), t["etapa"], ("_%d" % k) if e.partes > 1 else ""))
        try:
            with open(caminho, encoding="utf-8", errors="replace") as f:
                marcadas = [x.strip() for x in f if "■" in x or "▶" in x]
            if marcadas:
                linhas.append(marcadas[-1][:200])
        except OSError:
            pass
    return " · ".join(linhas)


def minhas_rodando(con):
    cur = con.cursor()
    cur.execute("""select t.id, t.etapa, t.id_validacao, l.n, t.id_lote, v.id_empresa::text, t.tentativas, v.estado
                     from radar_comercial.validacao_tarefa t
                     join radar_comercial.validacao v on v.id = t.id_validacao
                     join radar_comercial.validacao_lote l on l.id = t.id_lote
                    where t.estado = 'rodando' and t.ambiente = %s and t.worker = %s""", (AMBIENTE, MAQUINA))
    saida = [{"id": r[0], "etapa": r[1], "validacao": r[2], "lote": r[3], "id_lote": r[4], "empresa": r[5],
              "tentativas": r[6], "estado_validacao": r[7]} for r in cur.fetchall()]
    con.commit()
    return saida


def colher(con):
    """Dá sinal das tarefas desta máquina, derruba as canceladas e encerra as que terminaram."""
    rodando = minhas_rodando(con)
    vivas = []
    for t in rodando:
        estados = []
        for nome in nomes(t):
            rc, out, _ = docker("inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", nome)
            estados.append(out.split() if rc == 0 and out else None)
        if t["estado_validacao"] == "cancelada":
            for nome in nomes(t):
                docker("rm", "-f", nome)
            cur = con.cursor()
            cur.execute("""update radar_comercial.validacao_tarefa set estado = 'cancelada', terminado_em = now()
                            where id = %s and estado = 'rodando'""", (t["id"],))
            con.commit()
            _log("■ #%d %s cancelada com a validação %d" % (t["id"], t["etapa"], t["validacao"]))
            continue
        if any(s is None for s in estados) and all(s is None or s[0] != "running" for s in estados):
            # o contêiner sumiu (removido por fora, ou o executor caiu antes de subir): volta para a fila
            for nome in nomes(t):
                docker("rm", "-f", nome)
            r = va.encerrar(con, t, None, erro="o contêiner da tarefa sumiu")
            _log("✗ #%d %s lote %d: contêiner sumiu → %s" % (t["id"], t["etapa"], t["lote"], r))
            continue
        if all(s and s[0] in ("exited", "dead") for s in estados):
            codigo = max(int(s[1]) for s in estados)
            resumo = _resumo(t)
            for nome in nomes(t):
                docker("rm", "-f", nome)
            r = va.encerrar(con, t, codigo, resumo)
            _log("■ #%d %s lote %d: código %d → %s · %s" % (t["id"], t["etapa"], t["lote"], codigo, r, resumo[:140]))
            continue
        vivas.append(t["id"])
    if vivas:
        cur = con.cursor()
        cur.execute("update radar_comercial.validacao_tarefa set visto_em = now() where id = any(%s)", (vivas,))
        con.commit()
    return len(vivas)


def _parar(*_):
    global PARAR
    PARAR = True
    _log("sinal de parada: termino a volta e saio (os contêineres seguem; o próximo executor os adota)")


def main():
    if not REPO or not DADOS:
        raise SystemExit("RADAR_VALIDACAO_REPO e RADAR_VALIDACAO_DADOS são obrigatórios (caminhos NO HOST)")
    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)
    rc, out, err = docker("version", "--format", "{{.Server.Version}}")
    if rc != 0:
        raise SystemExit("sem acesso ao docker: %s" % (err or out))
    _log("executor · %s · máquina %s · docker %s · vagas %d · etapas %s · tetos %s"
         % (AMBIENTE, MAQUINA, out, VAGAS, ",".join(ACEITAS) or "todas", va.TETO))
    con = None
    while not PARAR:
        try:
            if con is None or con.closed:
                con = bc.conectar()
            ocupadas = colher(con)
            va.avancar(con, AMBIENTE)
            while ocupadas < VAGAS and not PARAR:
                t = va.pegar(con, MAQUINA, ACEITAS, AMBIENTE)
                if not t:
                    break
                if iniciar(con, t):
                    ocupadas += 1
        except Exception as erro:                                   # noqa: BLE001
            _log("✗ volta do executor falhou: %s: %s" % (type(erro).__name__, str(erro)[:300]))
            try:
                if con is not None:
                    con.close()
            except Exception:                                       # noqa: BLE001
                pass
            con = None
        for _ in range(PAUSA):
            if PARAR:
                break
            time.sleep(1)
    if con is not None:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
