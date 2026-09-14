#!/bin/bash
# Julgamento continuo das ligacoes aptas de Canoas (13/09/2026), em paralelo com a
# busca web. Apta: DuckDuckGo feito (decisao do dono do produto: "so DuckDuckGo
# basta"), com imagem e sem veredito. A cada ~10 min um lote; para sozinho quando
# a producao chega na etapa C (a avaliacao final), para nao julgar em dobro.
D=$HOME/producao_canoas
S=$D/busca_nova
J=$D/julgamento_continuo
REPO=$HOME/Documentos/sistemas/radarComercial
mkdir -p $J
cd $REPO || exit 1
AVALIA="-v $REPO/avaliar_enxuto.py:/app/avaliar_enxuto.py:ro -v $REPO/avaliar_ligacao.py:/app/avaliar_ligacao.py:ro -v $REPO/checagem_veredito.py:/app/checagem_veredito.py:ro"
n=0
while true; do
  # PARA SO POR PEDIDO (13/09/2026, noite): o arquivo PARAR. Antes parava ao ver
  # "FIM" no progresso da busca, e o "A. FIM" da busca derrubou o julgamento.
  if [ -f $J/PARAR ]; then
    rm -f $J/PARAR
    echo "$(date +%T) parado pelo arquivo PARAR" >> $J/progresso
    break
  fi
  n=$((n + 1))
  echo "$(date +%T) lote $n" >> $J/progresso
  docker run --rm --name radar-julgamento-continuo --env-file .env -e A2L_DB_HOST=192.168.3.10 -e RADAR_CONEXOES=4 \
    -e PYTHONIOENCODING=utf-8 -w /app $AVALIA radar-comercial-minerador-worker \
    python3 -u avaliar_enxuto.py --cidade Canoas --exigir-busca-web --vinculo-novo --julgar-sem-foto --trabalhadores 120 --aplicar > $J/lote_$n.log 2>&1
  echo "$(date +%T)    $(grep 'por minuto' $J/lote_$n.log | tail -1 | xargs)" >> $J/progresso
  sleep 600
done
