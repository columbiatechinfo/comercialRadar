#!/bin/bash
# Canoas, 13/09/2026 (noite): a reserva do Google pelo Camoufox quente (dono do
# produto). Medido na fila da reserva: 65 de 68 com resultado. Mas o pico de
# 107 buscas/min queimou os IPs, e o disjuntor abriu duas rodadas seguidas (30
# de 30 bloqueadas) com pausa de 15 min. O castigo do IP e de UMA hora: a pausa
# agora e de uma hora ANTES de cada rodada, ate 10 rodadas pela noite, com o
# ritmo fixo de 40/min do `buscar_web.py`.
D=$HOME/producao_canoas
S=$D/busca_nova
REPO=$HOME/Documentos/sistemas/radarComercial
P=$S/progresso
cd $REPO || exit 1
passo() { echo "$(date +%T) $*" >> $P; }
RUN="docker run --rm --init --shm-size 8g --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -w /app"
IMGQ=radar-busca-camoufox
BUSCA="-v $REPO/buscar_web.py:/app/buscar_web.py:ro -v $REPO/busca_navegador.py:/app/busca_navegador.py:ro"
passo "B. reserva do Google: pausa de 1 h antes de cada rodada (o castigo do IP), ate 10 rodadas"
for rodada in $(seq 1 10); do
  # "agora": a primeira rodada sem a pausa (religada com os IPs ainda bons)
  if [ $rodada -gt 1 ] || [ "${1:-}" != "agora" ]; then sleep 3600; fi
  $RUN --name radar-busca-reserva -e RADAR_CONEXOES=2 $BUSCA $IMGQ \
    python3 -u buscar_web.py --cidade Canoas --reserva-google --trabalhadores 8 --aplicar 2>&1 \
    | grep --line-buffered -v "INFO: Fetched\|WARNING\|Call log\|navigating to\|Retrying in" >> $S/reserva.log
  passo "   reserva $rodada: $(grep '■' $S/reserva.log | tail -1 | cut -c10-)"
  grep "▶ busca web" $S/reserva.log | tail -1 | grep -q " 0 consulta(s)" && break
done
passo "B. FIM"
