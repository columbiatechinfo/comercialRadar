#!/usr/bin/env bash
# lancar.sh — dispara trabalho LONGO no i9 e volta na hora.
#
# RODA NO NOTEBOOK.
#
# O PROBLEMA, medido em 25/08/2026
#
# Todo trabalho pesado mora no i9 (a base estadual, a captura do Maps, o OCR) e
# nenhum deles cabe numa sessao de terminal: sao horas. O jeito obvio NAO
# funciona la, e falha em silencio:
#
#     ssh i9 "wsl -d Ubuntu -- bash -lc 'setsid nohup ./script > log 2>&1 &'"
#
# O OpenSSH do Windows derruba a sessao inteira quando a conexao fecha, e o WSL
# leva junto os processos daquela invocacao. `setsid`, `nohup` e `disown` nao
# mudam isso. `Start-Process` do PowerShell tambem nao: o processo nasce na
# mesma sessao efemera do SSH. Medido com um `sleep 300`: some no instante em
# que o ssh volta, sem uma linha em lugar nenhum.
#
# Naquele dia isso custou caro. Uma producao "disparada" assim morreu calada, e
# como quem disparou nao conferiu, disparou de novo — e as duas acabaram vivas
# ao mesmo tempo, gravando no mesmo diretorio por quatro horas. As rodadas que
# sobreviviam de manha sobreviviam por acidente: havia uma sessao `orbisgrid`
# DESCONECTADA desde o dia anterior segurando o WSL de pe.
#
# A SOLUCAO: o trabalho passa a ser do systemd do Ubuntu, nao da sessao
#
# O WSL do i9 roda systemd como PID 1. `systemd-run --user` cria uma unidade
# transitoria cujo pai e o gerenciador do usuario — fora da arvore do ssh.
# Conferido de uma sessao NOVA: continua `active` depois que a conexao que o
# disparou ja fechou.
#
# E ganha-se de brinde a trava por nome: unidade `cr-<nome>` ja ativa faz o
# segundo disparo FALHAR em vez de duplicar o trabalho.
#
# CONFERIR NAO E OPCIONAL — e nao se confere com `pgrep`
#
# `pgrep -f dataset_brasil.sh` dentro de `bash -lc '...'` casa com a PROPRIA
# linha de comando da conferencia. O `1` que se le e ele mesmo. Passei uma tarde
# achando que uma producao estava viva por causa disso. Aqui a pergunta e feita
# ao systemd (`is-active`), que nao tem como se auto-encontrar.
#
# USO
#   bash scripts/i9/lancar.sh brasil ./scripts/i9/dataset_brasil.sh
#   bash scripts/i9/lancar.sh rs "./scripts/i9/dataset_estadual.sh RS"
#   bash scripts/i9/lancar.sh --ver brasil     # ultimas linhas do log
#   bash scripts/i9/lancar.sh --estado brasil  # rodando ou nao
#   bash scripts/i9/lancar.sh --parar brasil   # encerra
set -euo pipefail

I9="${I9_SSH:-orbisgrid@100.115.117.49}"
DIR_I9="${I9_DIR:-/home/orbisgrid/comercialradar}"
CASA_I9="${I9_HOME:-/home/orbisgrid}"

# Toda conversa com o i9 vai por ARQUIVO em vez de linha de comando. Entre este
# notebook e o bash de la ha TRES camadas (ssh -> PowerShell -> `wsl -- bash`) e
# cada uma reinterpreta aspas, `&&`, `>>` e `2>&1` — a mesma licao que o
# `checar_chromium.py` ja pagou. Como `bash -s` le da entrada padrao, a linha de
# comando do outro lado e so "bash -s": nada para as camadas comerem, e nada
# para uma busca por processo casar por engano.
_remoto() {
  ssh -o BatchMode=yes -o ConnectTimeout=30 "$I9" "wsl -d Ubuntu -- bash -s" < "$1" 2>/dev/null
}

VERBO="lancar"
case "${1:-}" in
  --ver)    VERBO="ver";    shift ;;
  --estado) VERBO="estado"; shift ;;
  --parar)  VERBO="parar";  shift ;;
esac
[ $# -ge 1 ] || { echo "uso: lancar.sh [--ver|--estado|--parar] <nome> [comando]"; exit 2; }
NOME="$1"; shift
case "$NOME" in
  *[!A-Za-z0-9_-]*) echo "nome so aceita letras, numeros, _ e - (vira nome de unidade la)"; exit 2 ;;
esac

UNIDADE="cr-$NOME"
JOB="$CASA_I9/.lancamentos/$NOME.sh"
LOG="$DIR_I9/logs/$NOME.log"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

case "$VERBO" in
  ver)
    { echo "tail -40 $LOG 2>/dev/null || echo '(sem log ainda)'"; } > "$TMP"
    _remoto "$TMP"; exit 0 ;;
  estado)
    {
      echo "echo \"unidade : \$(systemctl --user is-active $UNIDADE 2>&1)\""
      echo "echo \"log     : \$(ls -l --time-style=+%d/%m\\ %H:%M:%S $LOG 2>/dev/null | awk '{print \$5\" bytes, mexido \"\$6\" \"\$7}' || echo ausente)\""
      echo "ps -eo etime,cmd | grep -F '$JOB' | grep -v grep || true"
    } > "$TMP"
    _remoto "$TMP"; exit 0 ;;
  parar)
    { echo "systemctl --user stop $UNIDADE 2>&1 || true"; echo "echo \"agora: \$(systemctl --user is-active $UNIDADE 2>&1)\""; } > "$TMP"
    _remoto "$TMP"; exit 0 ;;
esac

[ $# -ge 1 ] || { echo "uso: lancar.sh <nome> <comando bash>"; exit 2; }
COMANDO="$*"

echo "▶ lancando '$NOME' no i9"
echo "  $COMANDO"

# ── 1 · o trabalho vira ARQUIVO no i9 ────────────────────────────────────
# Heredoc com delimitador entre aspas nao expande nada: o comando chega la
# exatamente como foi escrito aqui, com `&&`, aspas e redirecionamentos
# intactos. E o `exec` faz o processo do trabalho SER o processo da unidade —
# sem ele sobra um bash de permeio que so atrapalha o encerramento.
{
  echo "mkdir -p $CASA_I9/.lancamentos $DIR_I9/logs"
  echo "cat > $JOB <<'FIM_DO_JOB'"
  echo "#!/usr/bin/env bash"
  echo "cd $DIR_I9 || exit 1"
  echo "exec $COMANDO >> logs/$NOME.log 2>&1"
  echo "FIM_DO_JOB"
  echo "chmod +x $JOB"
  echo "echo instalado"
} > "$TMP"
_remoto "$TMP" | grep -q instalado || { echo "❌ nao consegui instalar o job no i9"; exit 1; }

# ── 2 · o systemd do Ubuntu adota o trabalho ─────────────────────────────
{
  echo "if ! systemctl --user is-system-running >/dev/null 2>&1; then"
  echo "  echo 'AVISO: o gerenciador systemd do usuario nao respondeu — o trabalho pode nao sobreviver.'"
  echo "fi"
  echo "systemctl --user reset-failed $UNIDADE >/dev/null 2>&1 || true"
  echo "systemd-run --user --unit=$UNIDADE --collect \\"
  echo "  --description='comercialRadar: $NOME' $JOB 2>&1 | head -2"
} > "$TMP"
_remoto "$TMP"

# ── 3 · conferir com o systemd, nao com pgrep ────────────────────────────
{
  echo "for i in 1 2 3 4 5; do"
  echo "  e=\$(systemctl --user is-active $UNIDADE 2>&1)"
  echo "  if [ \"\$e\" = active ]; then break; fi"
  echo "  sleep 2"
  echo "done"
  echo "echo ESTADO=\$e"
} > "$TMP"
ESTADO=$(_remoto "$TMP" | sed -n 's/^ESTADO=//p' | tr -d '[:space:]')

if [ "$ESTADO" = "active" ]; then
  echo "✅ rodando no i9 como unidade $UNIDADE"
  echo "   acompanhe:  bash scripts/i9/lancar.sh --ver $NOME"
  echo "   encerre:    bash scripts/i9/lancar.sh --parar $NOME"
else
  echo "❌ a unidade nao ficou ativa (estado: ${ESTADO:-desconhecido})."
  echo "   O log do proprio trabalho, se chegou a escrever:"
  echo "     bash scripts/i9/lancar.sh --ver $NOME"
  exit 1
fi
