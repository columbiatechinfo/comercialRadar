#!/bin/bash
# Avaliacao ENXUTA de Canoas (processo de 12/09/2026), em rodadas de 3 min.
#
# Cada rodada julga as ligacoes COM IMAGEM — com ou sem busca web (pedido do
# dono do produto em 12/09/2026, com a busca bloqueada) — na ordem SIM, depois
# SIM com analise humana, e dentro de cada uma Maps, iFood e demais. As
# reprovadas voltam para a fila quando a busca do Google delas chega
# (--vinculo-novo), e tambem as que ganharam POI depois do veredito. A Spark
# fica so com a avaliacao. O casamento dos descartados roda no casar_laco.sh.
# OS PREDIOS (mais de 20 POIs) VAO PARA O predios_laco.sh (12/09/2026, 18:10).
# RODADAS DE 3.000 (~43 min) e 15 s entre elas: a fila e montada no inicio da rodada, e uma
# rodada de 19 mil segurava por 4,5 h a volta das reprovadas que ganharam a
# ficha do Google Maps (6 de 12 viraram aprovadas no teste de 12/09).
D=$HOME/producao_canoas
REPO=$HOME/producao/radarComercial  # copia de producao desde 14/09/2026 (docs/DESENVOLVIMENTO.md)
cd $REPO || exit 1
rodada=0; vazias=0
busca_acabou() {
  grep -q FIM $D/busca_i9.progresso 2>/dev/null && \
  ssh -o BatchMode=yes -o ConnectTimeout=10 notebook \
    "grep -q FIM ~/producao_canoas/busca_notebook.progresso" 2>/dev/null
}
while true; do
  rodada=$((rodada + 1))
  echo "$(date +%T) rodada $rodada enxuta · todas com imagem" >> $D/avaliacao.progresso
  docker run --rm --name radar-avaliacao-canoas --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=4 -e PYTHONIOENCODING=utf-8 -w /app \
    -v $REPO/avaliar_enxuto.py:/app/avaliar_enxuto.py:ro -v $REPO/checagem_veredito.py:/app/checagem_veredito.py:ro \
    radar-comercial-minerador-worker \
    python3 -u avaliar_enxuto.py --cidade Canoas --vinculo-novo --adiar-grandes 20 --limite 3000 --trabalhadores 80 --aplicar \
    >> $D/avaliacao.log 2>&1
  if tail -40 $D/avaliacao.log | grep -q "   0 ligação(ões) na fila"; then
    vazias=$((vazias + 1))
  else
    vazias=0
  fi
  if busca_acabou && [ $vazias -ge 2 ]; then
    break
  fi
  if [ $vazias -gt 0 ]; then sleep 180; else sleep 15; fi
done
echo "$(date +%T) FIM" >> $D/avaliacao.progresso
