#!/bin/bash
# Recaptura da foto de rua em alta (DENSIDADE 2), em lotes de 150 POIs, retomavel.
# Uso: captura_lote.sh <k> <n> <trabalhadores>   (a fatia k de n da lista)
K=$1; N=$2; T=${3:-8}
D=$HOME/producao_canoas
cd $HOME/Documentos/sistemas/radarComercial || exit 1
awk -v k=$K -v n=$N 'NR % n == k' $D/recaptura_pois.txt > $D/recaptura_fatia_$K.txt
[ -e $D/lote_${K}_0000 ] || split -l 150 -d -a 4 $D/recaptura_fatia_$K.txt $D/lote_${K}_
echo "$(date +%T) inicio · $(wc -l < $D/recaptura_fatia_$K.txt) POIs" >> $D/captura_$K.progresso
for f in $(ls $D/lote_${K}_* | grep -v "\.feito$"); do
  [ -f "$f.feito" ] && continue
  ARGS=$(sed 's/^/--poi /' $f | tr '\n' ' ')
  docker run --rm --name radar-recaptura-$K --shm-size 2g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=2 -e PYTHONIOENCODING=utf-8 -w /app radar-comercial-minerador-worker \
    timeout 3600 xvfb-run -a python3 -u capturar_evidencia.py $ARGS --trabalhadores $T --aplicar >> $D/captura_$K.log 2>&1
  touch $f.feito
  echo "$(date +%T) $(basename $f) pronto" >> $D/captura_$K.progresso
done
echo "$(date +%T) FIM" >> $D/captura_$K.progresso
