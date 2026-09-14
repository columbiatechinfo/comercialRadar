#!/bin/bash
# Canoas: a busca web refeita sem o Google Maps, e a reavaliacao de tudo (13/09/2026).
# Decisoes do dono do produto:
#   - a busca e WEB: DuckDuckGo e Yahoo; o Google so quando os dois falham;
#   - so o resultado com a mesma rua e numero da instalacao vai para a IA;
#   - reavaliar TODAS as ligacoes julgadas de Canoas depois disso.
#   A. DuckDuckGo + Yahoo, rodadas ate nao sobrar consulta (sonda antes de cada)
#   B. reserva: o Google so onde os dois falharam, devagar, ate 4 rodadas
#   C. avaliacao de Canoas do zero, com a checagem do codigo (vereditos limpos antes)
D=$HOME/producao_canoas
S=$D/busca_nova
REPO=$HOME/producao/radarComercial  # copia de producao desde 14/09/2026 (docs/DESENVOLVIMENTO.md)
P=$S/progresso
mkdir -p $S
cd $REPO || exit 1
passo() { echo "$(date +%T) $*" >> $P; }
RUN="docker run --rm --shm-size 8g --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -w /app"
IMG=radar-comercial-minerador-worker
IMGQ=radar-busca-camoufox   # DuckDuckGo e Yahoo pelo Camoufox quente (13/09/2026)
BUSCA="-v $REPO/buscar_web.py:/app/buscar_web.py:ro -v $REPO/busca_navegador.py:/app/busca_navegador.py:ro"
AVALIA="-v $REPO/avaliar_enxuto.py:/app/avaliar_enxuto.py:ro -v $REPO/avaliar_ligacao.py:/app/avaliar_ligacao.py:ro -v $REPO/checagem_veredito.py:/app/checagem_veredito.py:ro"

passo "inicio"
passo "A. busca web: DuckDuckGo e Yahoo"
for rodada in $(seq 1 12); do
  $RUN --name radar-busca-sonda -e RADAR_CONEXOES=1 $BUSCA $IMGQ python3 -u buscar_web.py --sonda 2>&1 \
    | grep --line-buffered "sonda:" >> $S/busca.log
  if ! tail -1 $S/busca.log | grep -q "sonda: ok"; then
    passo "   sonda sem resposta ou bloqueada — nova sonda em 5 min"
    sleep 300
    continue
  fi
  $RUN --name radar-busca-nova -e RADAR_CONEXOES=2 $BUSCA $IMGQ \
    python3 -u buscar_web.py --cidade Canoas --trabalhadores 16 --aplicar 2>&1 \
    | grep --line-buffered -v "INFO: Fetched\|WARNING\|Call log\|navigating to\|Retrying in" >> $S/busca.log
  passo "   rodada $rodada: $(grep '■' $S/busca.log | tail -1 | cut -c10-)"
  grep "▶ busca web" $S/busca.log | tail -1 | grep -q " 0 consulta(s)" && break
  sleep 60
done
passo "A. FIM"

passo "B. reserva do Google: so onde DuckDuckGo e Yahoo falharam"
for rodada in 1 2 3 4; do
  $RUN --name radar-busca-reserva -e RADAR_CONEXOES=2 $BUSCA $IMG \
    python3 -u buscar_web.py --cidade Canoas --reserva-google --trabalhadores 6 --aplicar 2>&1 \
    | grep --line-buffered -v "INFO: Fetched\|WARNING\|Call log\|navigating to\|Retrying in" >> $S/reserva.log
  passo "   reserva $rodada: $(grep '■' $S/reserva.log | tail -1 | cut -c10-)"
  grep "▶ busca web" $S/reserva.log | tail -1 | grep -q " 0 consulta(s)" && break
  sleep 900
done
passo "B. FIM"

passo "C. avaliação de Canoas do zero (os vereditos antigos foram limpos em 13/09)"
$RUN --name radar-reavaliacao -e RADAR_CONEXOES=4 $AVALIA $IMG   python3 -u avaliar_enxuto.py --cidade Canoas --exigir-busca-web --trabalhadores 80 --aplicar > $S/avaliacao.log 2>&1
passo "   $(grep 'por minuto' $S/avaliacao.log | tail -1 | xargs)"
passo "FIM"
