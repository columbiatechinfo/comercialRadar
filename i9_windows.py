# -*- coding: utf-8 -*-
"""i9_windows.py — falar com o lado WINDOWS do i9.

POR QUE EXISTE UM LADO WINDOWS

O i9 é uma máquina só com dois ambientes. O WSL Linux é onde moram o banco, os
datasets e a captura; o Windows é onde há **desktop**. E há etapa que abre
navegador: no WSL o Chromium visível não sobe — medido em 26/08/2026, cinco
variantes (sem `WAYLAND_DISPLAY`, `--ozone-platform=x11`, `XDG_RUNTIME_DIR` do
WSLg e as combinações), todas estourando o launch em ~46 s. O WSLg existe e o
socket X responde, mas não serve uma sessão que chega por SSH. No Windows do i9
sobe em 1,1 s, e o banco responde de lá (medido: 83.204 POIs).

Então a etapa que precisa de tela roda no Windows do i9, que tem sessão
interativa de verdade, e o orquestrador a chama daqui. O notebook fica fora do
caminho — que era o pedido.

O QUE ESTE ARQUIVO **NÃO** RESOLVE, e eu já escrevi o contrário uma vez

Ter tela não faz o iFood voltar a funcionar. Em 25/08 a etapa gravou 1.598
lojas; em 26/08 devolve zero — no notebook E no Windows do i9, com proxy E sem
proxy. O Cloudflare passou de desafio automático para **Turnstile interativo**
("Confirme que é humano"), e navegador automatizado não clica. É bloqueio
externo, e trocar de máquina não o contorna.

Escolher o Windows do i9 continua certo pelo primeiro motivo (é onde a etapa
pode ao menos ser tentada, longe do notebook do operador). Só não é a cura que
eu cheguei a afirmar que era.

POR QUE UM UTILITÁRIO E NÃO UM `scp` SOLTO

Entre este processo e o PowerShell do i9 há três camadas que reinterpretam
aspas: o ssh, o shell local e o próprio PowerShell. Montar comando por
concatenação de string quebra de formas que o erro não denuncia — a primeira
tentativa morreu em `ExpectedExpression`, que não diz nada sobre a causa.

Aqui o roteiro vai SEMPRE por `-EncodedCommand` (UTF-16LE em base64, que é o que
o PowerShell espera) e o dado grande vai pela ENTRADA PADRÃO. Assim nenhuma
camada tem o que comer: a linha de comando remota é só `powershell
-EncodedCommand <base64>`, sem uma aspa sequer.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent

SSH = "orbisgrid@100.115.117.49"
DIR = r"C:\comercialradar"

# Os avisos do ssh saem no mesmo canal da resposta. Não são erro; tratá-los como
# erro esconde o erro de verdade.
_RUIDO = ("post-quantum", "store now", "WARNING:", "Warning:", "#< CLIXML",
          "<Objs", "progress", "openssh.com/pq", "may need to be upgraded")


def _limpar(saida: bytes) -> str:
    txt = saida.decode("utf-8", "replace")
    return "\n".join(l for l in txt.splitlines()
                     if l.strip() and not any(r in l for r in _RUIDO))


def executar(roteiro: str, entrada: bytes = b"", timeout: int = 300):
    """Roda um roteiro PowerShell no Windows do i9.

    `-EncodedCommand` exige UTF-16LE em base64. Passar o roteiro como texto na
    linha de comando é o que produz erro de sintaxe a cada acento ou aspa.
    """
    cod = base64.b64encode(roteiro.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SSH,
         "powershell -NoProfile -EncodedCommand " + cod],
        input=entrada, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout)
    return p.returncode, _limpar(p.stdout)


def enviar(arquivos, destino: str = DIR, log=print) -> int:
    """Copia arquivos locais para o Windows do i9. Devolve quantos foram."""
    n = 0
    for rel in arquivos:
        origem = BASE / rel
        if not origem.exists():
            log(f"  ⚠️  {rel} não existe aqui")
            continue
        alvo = destino + "\\" + rel.replace("/", "\\")
        pasta = alvo.rsplit("\\", 1)[0]
        roteiro = (
            f"New-Item -ItemType Directory -Force '{pasta}' | Out-Null\n"
            "$b = [Console]::In.ReadToEnd()\n"
            f"[IO.File]::WriteAllBytes('{alvo}', [Convert]::FromBase64String($b.Trim()))\n"
            f"Write-Output ('OK ' + (Get-Item '{alvo}').Length)\n"
        )
        rc, saida = executar(roteiro, base64.b64encode(origem.read_bytes()))
        if rc == 0 and "OK" in saida:
            n += 1
        else:
            log(f"  ⚠️  falhou {rel}: {saida[:140]}")
    return n


def copiar_do_wsl(caminhos, log=print) -> int:
    """Traz arquivo do WSL do i9 para o Windows do i9, SEM passar por aqui.

    É como o `.env` chega ao lado Windows. Ele tem credencial: fazê-lo viajar
    até este processo para depois voltar seria expor segredo sem necessidade —
    a máquina é a mesma, e o WSL enxerga o disco do Windows em `/mnt/c`.
    """
    import shlex
    linhas = ["cd /home/orbisgrid/comercialradar || exit 1"]
    for c in caminhos:
        alvo = "/mnt/c/comercialradar/" + c
        linhas.append(f"mkdir -p $(dirname {shlex.quote(alvo)})")
        linhas.append(f"cp -f {shlex.quote(c)} {shlex.quote(alvo)} && "
                      f"echo 'copiado {c}' || echo 'FALHOU {c}'")
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SSH,
         "wsl -d Ubuntu -- bash -s"],
        input=("\n".join(linhas) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    saida = _limpar(p.stdout)
    for l in saida.splitlines():
        log("  " + l)
    return saida.count("copiado")


def rodar_no_windows(argumentos, log=print, timeout: int = 3600) -> int:
    """Roda um comando Python do projeto no WINDOWS do i9, e ecoa a saída.

    Não há streaming linha a linha como no WSL: o PowerShell por `-EncodedCommand`
    devolve tudo ao final. Para a etapa do iFood isso basta — ela leva minutos,
    não horas, e o painel já mostra o nome da etapa enquanto ela corre.
    """
    args = " ".join("'" + a.replace("'", "''") + "'" for a in argumentos)
    roteiro = (
        f"Set-Location '{DIR}'\n"
        "$env:PYTHONUTF8 = '1'\n"
        "$env:PYTHONIOENCODING = 'utf-8'\n"
        f"python -u {args} 2>&1 | ForEach-Object {{ Write-Output $_ }}\n"
        "exit $LASTEXITCODE\n"
    )
    try:
        rc, saida = executar(roteiro, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"  ⚠️  o Windows do i9 não respondeu em {timeout}s")
        return 124
    for linha in saida.splitlines():
        log("    " + linha.rstrip())
    return rc
