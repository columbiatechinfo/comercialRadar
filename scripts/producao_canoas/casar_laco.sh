#!/bin/bash
# Casamento dos POIs que a IA descartou, a cada 30 min (decisao do dono do
# produto, 11/09/2026): o descartado procura outra ligacao no mesmo endereco, e
# a ligacao que ganhar POI volta para a IA na rodada seguinte (--vinculo-novo).
D=$HOME/producao_canoas
cd $HOME/Documentos/sistemas/radarComercial || exit 1
while ! grep -q FIM $D/avaliacao.progresso 2>/dev/null; do
  docker run --rm --name radar-casar-orfaos --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=1 -e PYTHONIOENCODING=utf-8 -w /app radar-comercial-minerador-worker \
    timeout 1800 python3 -u casar_por_endereco.py --cidade Canoas --so-orfaos --aplicar >> $D/casar.log 2>&1
  echo "$(date +%T) casamento: $(grep 'o banco gravou' $D/casar.log | tail -1 | cut -c10-)" >> $D/casar.progresso
  sleep 1800
done
