#!/bin/bash
# Status da noite de 13 para 14/09/2026: julgamento de Canoas no prompt novo e a
# reserva do Google. Uma linha por assunto, com a estimativa de conclusao.
export LC_NUMERIC=C
D=$HOME/producao_canoas
q() { docker exec supabase-db psql -U supabase_admin -d a2l -Atc "$1" 2>/dev/null; }
L=$(ls -t $D/julgamento_continuo/lote_*.log 2>/dev/null | head -1)
V=$(q "select count(*), count(*) filter (where veredito = 'aprovado'), count(*) filter (where veredito = 'revisao_humana'),
              count(*) filter (where veredito = 'reprovado'), count(*) filter (where avaliado_em > now() - interval '15 minutes')
         from radar_comercial.ligacao_veredito")
IFS='|' read -r TOT APR REV REP R15 <<< "$V"
FILA=$(grep -oE '[0-9]+ ligação\(ões\) na fila' $L 2>/dev/null | tail -1 | grep -oE '^[0-9]+')
FEITAS=$(grep -cE '^\s+[0-9]{5,} +(aprovado|reprovado|revisao_humana) ' $L 2>/dev/null)
FALHAS=$(grep -cE 'FALHOU|ERRO' $L 2>/dev/null)
POR_MIN=$(awk -v r="${R15:-0}" 'BEGIN{printf "%.1f", r/15}')
FALTA=$(( ${FILA:-0} - ${FEITAS:-0} ))
if awk -v p="$POR_MIN" 'BEGIN{exit !(p>0)}'; then
  MIN=$(awk -v f="$FALTA" -v p="$POR_MIN" 'BEGIN{printf "%d", f/p}')
  FIM=$(date -d "+$MIN minutes" '+%d/%m %H:%M')
  ETA="faltam $FALTA · ~$((MIN/60))h$(printf %02d $((MIN%60))) · termina ~$FIM"
else
  ETA="faltam $FALTA · sem ritmo nos últimos 15 min"
fi
VIVO=$(docker ps --format '{{.Names}}' | grep -q '^radar-julgamento-continuo$' && echo "rodando" || echo "PARADO (entre lotes ou caiu)")
echo "julgamento (prompt novo): $TOT vereditos · $APR aprovados · $REV revisão · $REP reprovados · $POR_MIN/min (15 min) · lote: $FEITAS de ${FILA:-?} · $ETA · falhas $FALHAS · $VIVO"
G=$(grep -oE '\[[0-9]+/[0-9]+\].*' $D/busca_nova/reserva.log 2>/dev/null | tail -1)
RV=$(docker ps --format '{{.Names}}' | grep -q '^radar-busca-reserva$' && echo "rodando" || echo "parado")
echo "reserva do Google: ${G:-sem progresso ainda} · $RV · $(tail -1 $D/busca_nova/progresso | cut -c1-90)"
echo "i9: $(uptime | sed 's/.*load average: /carga /') · Spark: $(curl -s --max-time 8 http://192.168.3.20:7400/metrics | grep '^vllm:num_requests_running' | awk '{printf "%d em curso", $2}')"
