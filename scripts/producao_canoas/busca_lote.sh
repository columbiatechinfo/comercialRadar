#!/bin/bash
# Busca web de Canoas, retomavel: a fila pula consulta ja feita.
# Uso: busca_lote.sh <fatia k[,k2]/n> <navegadores> <etiqueta>
#   i9:       busca_lote.sh 0,1/3 20 i9
#   notebook: busca_lote.sh 2/3 10 notebook
FATIA=$1; T=${2:-10}; TAG=${3:-$(hostname)}
D=$HOME/producao_canoas
cd $HOME/Documentos/sistemas/radarComercial || exit 1
for rodada in 1 2 3 4 5; do
  echo "$(date +%T) rodada $rodada" >> $D/busca_$TAG.progresso
  docker run --rm --name radar-busca-web-$TAG --shm-size 8g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=2 -e PYTHONIOENCODING=utf-8 -w /app radar-comercial-minerador-worker \
    python3 -u buscar_web.py --cidade Canoas --fatia "$FATIA" --trabalhadores $T --aplicar 2>&1 \
    | grep --line-buffered -v "INFO: Fetched" >> $D/busca_$TAG.log
  grep "▶ busca web" $D/busca_$TAG.log | tail -1 | grep -q " 0 consulta(s)" && break
  sleep 30
done
echo "$(date +%T) FIM" >> $D/busca_$TAG.progresso
