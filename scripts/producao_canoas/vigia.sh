#!/bin/bash
# Vigia da producao de Canoas: checa a cada 2 min e so fala quando precisa —
# erro novo, busca parada ha 15 min, ou o resumo de cada 30 min.
D=$HOME/producao_canoas
ult_feitas=-1; ult_mud=$(date +%s); ult_tb=-1; ult_resumo=0; ult_av=-1
while true; do
  linha=$($D/status.sh 2>&1)
  agora=$(date +%s)
  feitas=$(echo "$linha" | grep -o "busca web: [0-9]*" | grep -o "[0-9]*$")
  tb=$(echo "$linha" | grep -o "Traceback i9 [0-9]* · notebook [0-9]*" | grep -o "[0-9]*" | paste -sd+ | bc 2>/dev/null)
  tb=${tb:-0}
  av=$(grep -c "" $D/avaliacao.log 2>/dev/null)
  alerta=""
  if [ "$feitas" != "$ult_feitas" ]; then ult_feitas=$feitas; ult_mud=$agora; fi
  if [ $ult_tb -ge 0 ] && [ "$tb" -gt "$ult_tb" ]; then alerta="ALERTA: erro novo (Traceback)"; fi
  ult_tb=$tb
  busca_fim=0
  grep -q FIM $D/busca_i9.progresso 2>/dev/null && \
    ssh -o BatchMode=yes -o ConnectTimeout=8 notebook "grep -q FIM ~/producao_canoas/busca_notebook.progresso" 2>/dev/null && busca_fim=1
  if [ $busca_fim -eq 0 ] && [ $((agora - ult_mud)) -ge 900 ]; then
    alerta="${alerta:+$alerta · }ALERTA: busca web parada há $(( (agora - ult_mud) / 60 )) min"
  fi
  if grep -q FIM $D/avaliacao.progresso 2>/dev/null; then
    echo "$(date +%H:%M) FIM da avaliação · $linha"
    exit 0
  fi
  if [ -n "$alerta" ] || [ $((agora - ult_resumo)) -ge 1800 ]; then
    echo "$(date +%H:%M) ${alerta:+$alerta · }$linha"
    ult_resumo=$agora
  fi
  sleep 120
done
