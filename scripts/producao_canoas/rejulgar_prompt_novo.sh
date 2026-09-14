#!/bin/bash
# Rejulga as ligacoes de Canoas julgadas pelo prompt anterior (13/09/2026, noite):
# o prompt novo diz em campo proprio se as fotos mostram o comercio e quais
# resultados da busca confirmam, e o motivo nao tem limite de frases. Pedido do
# dono do produto para a tela SEEK. Roda ao lado do julgamento continuo, 40 e 40.
D=$HOME/producao_canoas
R=$D/rejulgar_prompt_novo
REPO=$HOME/producao/radarComercial  # copia de producao desde 14/09/2026 (docs/DESENVOLVIMENTO.md)
mkdir -p $R
cd $REPO || exit 1
AVALIA="-v $REPO/avaliar_enxuto.py:/app/avaliar_enxuto.py:ro -v $REPO/avaliar_ligacao.py:/app/avaliar_ligacao.py:ro -v $REPO/checagem_veredito.py:/app/checagem_veredito.py:ro"
for rodada in 1 2 3; do
  echo "$(date +%T) rodada $rodada" >> $R/progresso
  docker run --rm --name radar-rejulgar-prompt-novo --env-file .env -e A2L_DB_HOST=192.168.3.10 -e RADAR_CONEXOES=4 \
    -e PYTHONIOENCODING=utf-8 -w /app $AVALIA radar-comercial-minerador-worker \
    python3 -u avaliar_enxuto.py --cidade Canoas --prompt-antigo --trabalhadores 40 --aplicar > $R/rodada_$rodada.log 2>&1
  echo "$(date +%T)    $(grep 'julgadas por outro processo\|por minuto' $R/rodada_$rodada.log | xargs)" >> $R/progresso
  grep -q "nenhuma ligação julgada por outro processo" $R/rodada_$rodada.log && break
  sleep 60
done
echo "$(date +%T) FIM" >> $R/progresso
