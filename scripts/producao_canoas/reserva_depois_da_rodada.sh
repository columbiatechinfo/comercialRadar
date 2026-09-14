#!/bin/bash
# Troca da reserva do Google para a copia de producao (14/09/2026) sem cortar a
# rodada em curso: o laco antigo foi encerrado, o conteiner dele segue ate o fim.
# Aqui se espera o conteiner sair, registra-se a rodada no progresso (o que o laco
# antigo faria) e religa o laco novo, que comeca pela pausa de 1 h.
S=$HOME/producao_canoas/busca_nova
while [ -n "$(docker ps -q -f name=^radar-busca-reserva$)" ]; do sleep 30; done
echo "$(date +%T)    reserva (rodada da troca): $(grep '■' $S/reserva.log | tail -1 | cut -c10-)" >> $S/progresso
cd $HOME/producao_canoas || exit 1
exec bash reserva_camoufox.sh
