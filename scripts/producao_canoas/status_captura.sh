#!/bin/bash
# Status da captura de fotos das ligacoes sem imagem (14/09/2026), mais uma linha
# do julgamento e da reserva do Google.
export LC_NUMERIC=C
D=$HOME/producao_canoas
C=$D/captura_sem_imagem
q() { docker exec supabase-db psql -U supabase_admin -d a2l -Atc "$1" 2>/dev/null; }
TOTAL=$(wc -l < $C/pois.txt)
LOTES=$(ls $C/lote_* 2>/dev/null | grep -vc "\.feito$")
FEITOS=$(ls $C/lote_*.feito 2>/dev/null | wc -l)
IDS=$(paste -sd, $C/pois.txt)
COM=$(q "select count(distinct poi_id) from radar_comercial.poi_evidencia
          where poi_id = any('{$IDS}'::bigint[]) and tipo like 'sv_%'
            and (dados is not null or storage_path is not null)")
ULT=$(grep -oE '[0-9]+/150 · [0-9.]+ s/POI' $C/captura.log 2>/dev/null | tail -1)
VIVO=$(docker ps --format '{{.Names}}' | grep -q '^radar-captura-sem-imagem$' && echo "rodando" \
       || (tail -1 $C/progresso | grep -q FIM && echo "TERMINOU" || echo "PARADO"))
echo "captura: $COM de $TOTAL POIs com foto · lote $((FEITOS + 1)) de $LOTES ($ULT) · $VIVO"
bash $D/status_julgamento.sh | head -2 | sed 's/^julgamento (prompt novo): /julgamento: /; s/ · Spark.*//'
