#!/bin/bash
# =============================================================================
# O julgamento em paralelo com a coleta (dono do produto, 15/09/2026)
#
# "Vai rodando a avaliacao dos ja aprovados que coletamos evidencia ainda ontem enquanto os demais seguem
# coletando e atualizando evidencias." O orquestrador (`orquestrador_julgamento.sh`, etapa R) so COLETA: prepara
# cada lote e marca `lotes/R_xxx.evidencias`. Este laco JULGA, na ordem da fila, o proximo lote com as evidencias
# prontas — nunca pula um lote — e marca `.feito`.
#
#   */5 * * * * flock -n $HOME/producao_canoas/orquestrador/.trava_julgamento \
#       bash $HOME/producao/radarComercial/scripts/producao_canoas/julgamento_paralelo.sh
#
# 60 SIMULTANEOS, e nao 120: a Spark divide a carga com a leitura das fotos (60, a 1.088 px) da coleta; 120
# julgamentos com imagem grande ja derrubaram a Spark por memoria (14/09/2026).
# PARAR: o mesmo arquivo do orquestrador.
# =============================================================================
REPO=$HOME/producao/radarComercial
O=$HOME/producao_canoas/orquestrador
cd $REPO || exit 1
P=$O/progresso
log() { echo "$(date '+%d/%m %T') $*" >> $P; }
[ -f $O/PARAR ] && exit 0
RUN="docker run --rm --init --network host --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -w /app"
IMG=radar-comercial-minerador-worker
SIMULTANEOS=${JULGAMENTO_SIMULTANEOS:-60}

while true; do
  [ -f $O/PARAR ] && { log "J. parado pelo arquivo PARAR"; exit 0; }
  [ -f $O/R_feito ] && exit 0
  proximo=""
  for arq in $(ls $O/lotes/R_[0-9][0-9][0-9].txt 2>/dev/null | sort); do
    tag=$(basename $arq .txt)
    [ -f $O/lotes/$tag.feito ] && continue
    [ -f $O/lotes/$tag.evidencias ] && proximo=$tag
    break
  done
  if [ -z "$proximo" ]; then
    sleep 60
    continue
  fi
  log "J. $proximo: julgamento ($(wc -l < $O/lotes/$proximo.txt) ligações, evidências prontas, $SIMULTANEOS simultâneos)"
  docker rm -f radar-orq-julgamento > /dev/null 2>&1
  $RUN --name radar-orq-julgamento -e RADAR_CONEXOES=4 -v $REPO:/app:ro -v $O:/o $IMG \
    python -u avaliar_enxuto.py --leve --ligacoes-arquivo /o/lotes/$proximo.txt --julgar-sem-foto --trabalhadores $SIMULTANEOS --aplicar \
    > $O/lotes/${proximo}_julgamento.log 2>&1
  L=$O/lotes/${proximo}_julgamento.log
  if grep -q "ligação(ões) em" $L; then
    log "   J. $proximo: $(grep 'por minuto' $L | tail -1 | xargs) · aprovadas $(grep -cE '^\s+[0-9]+\s+aprovado' $L) · revisão $(grep -cE '^\s+[0-9]+\s+revisao_humana' $L) · reprovadas $(grep -cE '^\s+[0-9]+\s+reprovado' $L) · falhas $(grep -ciE 'FALHOU' $L)"
    touch $O/lotes/$proximo.feito
  else
    log "   J. $proximo: o julgamento não terminou ($(grep -hE 'Traceback|Error' $L | tail -1 | cut -c1-150)); nova tentativa em 5 min"
    sleep 300
  fi
done
