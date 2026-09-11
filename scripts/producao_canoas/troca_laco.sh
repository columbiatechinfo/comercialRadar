#!/bin/bash
# Troca o laco da avaliacao pela versao nova quando a rodada em curso acabar.
D=$HOME/producao_canoas
while docker ps --format '{{.Names}}' | grep -q '^radar-avaliacao-canoas$'; do sleep 10; done
for P in $(ps -eo pid,args | grep '[/]avaliacao_laco.sh' | awk '{print $1}'); do kill "$P"; done
mv -f $D/avaliacao_laco.sh.novo $D/avaliacao_laco.sh && chmod +x $D/avaliacao_laco.sh
cd $D && nohup ./avaliacao_laco.sh >> avaliacao.out 2>&1 < /dev/null &
echo "$(date +%T) laço trocado: 12 avaliações simultâneas, espera de 15 min" >> $D/avaliacao.progresso
