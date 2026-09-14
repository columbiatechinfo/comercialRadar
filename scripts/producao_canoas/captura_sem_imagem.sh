#!/bin/bash
# Canoas, 14/09/2026: a foto de rua dos POIs das ligacoes que ficaram sem veredito
# por falta de imagem (dono do produto: "capturar as fotos antes"). So o POI mais
# perto do hidrometro, a ate 60 m — o raio em que o dossie usa a foto. 965 POIs
# de 1.134 ligacoes. Lotes de 150, retomavel; o julgamento continuo pega as
# ligacoes sozinho quando a foto chega.
D=$HOME/producao_canoas
C=$D/captura_sem_imagem
REPO=$HOME/producao/radarComercial  # copia de producao desde 14/09/2026 (docs/DESENVOLVIMENTO.md)
mkdir -p $C
cd $REPO || exit 1
[ -e $C/lote_0000 ] || split -l 150 -d -a 4 $C/pois.txt $C/lote_
echo "$(date +%T) inicio · $(wc -l < $C/pois.txt) POIs" >> $C/progresso
MONTA="-v $REPO/capturar_evidencia.py:/app/capturar_evidencia.py:ro -v $REPO/frente_da_rua.py:/app/frente_da_rua.py:ro"
for f in $(ls $C/lote_* | grep -v "\.feito$"); do
  [ -f "$f.feito" ] && continue
  ARGS=$(sed 's/^/--poi /' $f | tr '\n' ' ')
  docker run --rm --init --name radar-captura-sem-imagem --shm-size 2g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=2 -e PYTHONIOENCODING=utf-8 -w /app $MONTA radar-comercial-minerador-worker \
    timeout 3600 xvfb-run -a python3 -u capturar_evidencia.py $ARGS --trabalhadores 8 --aplicar >> $C/captura.log 2>&1
  touch $f.feito
  echo "$(date +%T) $(basename $f) pronto · $(grep -cE 'gravad|salv' $C/captura.log) linhas de gravacao no log" >> $C/progresso
done
echo "$(date +%T) FIM" >> $C/progresso
