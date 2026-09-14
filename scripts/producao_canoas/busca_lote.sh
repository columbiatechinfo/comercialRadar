#!/bin/bash
# Busca web de Canoas, retomavel: a fila pula consulta ja feita.
# Uso: busca_lote.sh <fatia k[,k2]/n> <navegadores> <etiqueta>
#   i9:       busca_lote.sh 0,1/3 20 i9
#   notebook: busca_lote.sh 2/3 10 notebook
FATIA=$1; T=${2:-10}; TAG=${3:-$(hostname)}
D=$HOME/producao_canoas
cd $HOME/producao/radarComercial || exit 1  # copia de producao desde 14/09/2026
RUN="docker run --rm --shm-size 8g --env-file .env -e A2L_DB_HOST=192.168.3.10 -e RADAR_CONEXOES=2 -e PYTHONIOENCODING=utf-8 -w /app"
IMG=radar-comercial-minerador-worker
# PELO GOOGLE MAPS desde 12/09/2026 (a pagina de busca bloqueia por qualquer
# IP). A busca que falha volta na rodada seguinte. Ate 60 rodadas, 2 min entre
# elas. ANTES DE CADA RODADA, A SONDA: uma busca so. Bloqueada, espera 15 min e
# sonda de novo, sem abrir os navegadores — insistir so estende o castigo.
rodada=0
while [ $rodada -lt 60 ]; do
  $RUN --name radar-sonda-$TAG $IMG python3 -u buscar_web.py --sonda 2>&1 | grep --line-buffered "sonda:" >> $D/busca_$TAG.log
  if tail -1 $D/busca_$TAG.log | grep -q "sonda: ok"; then
    rodada=$((rodada + 1))
    echo "$(date +%T) rodada $rodada" >> $D/busca_$TAG.progresso
    $RUN --name radar-busca-web-$TAG $IMG python3 -u buscar_web.py --cidade Canoas --fatia "$FATIA" --trabalhadores $T --aplicar 2>&1 \
      | grep --line-buffered -v "INFO: Fetched" >> $D/busca_$TAG.log
    grep "▶ busca web" $D/busca_$TAG.log | tail -1 | grep -q " 0 consulta(s)" && break
    sleep 120
  elif tail -1 $D/busca_$TAG.log | grep -q "sonda: BLOQUEADA"; then
    echo "$(date +%T) Google bloqueando (sonda) — nova sonda em 15 min" >> $D/busca_$TAG.progresso
    sleep 900
  else
    # FALHA DE CONEXAO NAO E BLOQUEIO (12/09/2026: um ERR_TIMED_OUT do proxy
    # parou o i9 por 15 min): outra sonda, por outro IP, em 30 s.
    echo "$(date +%T) sonda sem resposta (conexao) — outra em 30 s" >> $D/busca_$TAG.progresso
    sleep 30
  fi
done
echo "$(date +%T) FIM" >> $D/busca_$TAG.progresso
