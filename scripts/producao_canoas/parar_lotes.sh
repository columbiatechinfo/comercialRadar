#!/bin/bash
# Para os lacos de producao e os conteineres deles nesta maquina.
for s in captura_lote busca_lote avaliacao_laco; do
  for P in $(ps -eo pid,args | grep "[/]${s}.sh" | awk '{print $1}'); do
    kill "$P" && echo "$(hostname) parou $s ($P)"
  done
done
for c in $(docker ps --format '{{.Names}}' | grep -E '^radar-(recaptura|busca-web|avaliacao)'); do
  docker stop -t 5 "$c" >/dev/null && echo "$(hostname) parou $c"
done
