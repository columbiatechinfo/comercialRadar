#!/usr/bin/env bash
# O julgamento acompanhando a captura, em vez de esperar por ela.
#
# POR QUE UM LACO E NAO UMA CORRIDA SO. A fila do julgamento e uma CONSULTA
# tirada no comeco da execucao: quem for fotografado depois disso nao entra
# naquela passada. Com a captura levando horas, uma corrida unica julgaria o
# material de agora e ignoraria tudo o que chegasse durante.
#
# `--desatualizados` e o que fecha o ciclo: ele pega a ligacao sem veredito OU
# cujo veredito e mais velho que a foto mais nova dos POIs dela. Cada passada
# leva o que ficou pronto desde a anterior.
#
# `RADAR_CONEXOES=4` porque o pooler tem 20 sessoes NO TOTAL, e as duas
# capturas ja seguram 11. Sem esse teto o laco morre com
# `(EMAXCONNSESSION) max clients reached` — aconteceu duas vezes hoje.
#
# O INTERVALO E DE 12 MINUTOS por uma conta simples: a captura entrega cerca de
# 23 POIs por minuto somando as duas maquinas, entao em 12 minutos ha umas 280
# fotos novas — trabalho suficiente para uma passada valer a pena, e pouco o
# bastante para nada ficar parado por muito tempo.
set -u
cd /home/a2l/Documentos/sistemas/radarComercial

VOLTA=0
while true; do
  VOLTA=$((VOLTA + 1))
  echo "════ volta $VOLTA · $(date +%H:%M:%S) ════"
  docker run --rm --name radar-julga-laco \
    --network radar-comercial-api_default --env-file .env \
    -e PYTHONDONTWRITEBYTECODE=1 -e RADAR_CONEXOES=4 -w /app \
    radar-comercial-api-radar-comercial-api \
    python3 avaliar_ligacao.py --trabalhadores 40 --sem-catalogo \
      --desatualizados --aplicar 2>&1 | tail -8

  # A CAPTURA AINDA ESTA RODANDO? Quando as duas terminarem, uma ultima
  # passada pega o que sobrou e o laco encerra — deixar rodando para sempre
  # significaria um contêiner acordando de 12 em 12 minutos para nada.
  I9=$(ps aux | grep -c "[c]apturar_evid")
  NB=$(ssh -o ConnectTimeout=5 notebook 'ps aux | grep -c "[c]apturar_evid"' 2>/dev/null || echo 0)
  if [ "$I9" -le 0 ] && [ "$NB" -le 1 ]; then
    echo "captura terminou nas duas máquinas — última passada feita, encerrando"
    break
  fi
  sleep 720
done
echo "════ laço encerrado · $(date +%H:%M:%S) ════"
