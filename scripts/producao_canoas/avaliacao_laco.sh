#!/bin/bash
# Avaliacao de Canoas pela IA, em rodadas de 5 min (noite de 11/09/2026).
#
# Cada rodada julga as ligacoes com busca web feita, na ordem Maps, iFood e
# demais, e as que ganharam POI depois do veredito. A cada 6 rodadas o
# casamento por endereco religa os POIs que a IA descartou. Quando a busca
# acaba nas duas maquinas, entram as ligacoes com imagem cuja busca falhou,
# julgadas sem ela (decisao do dono do produto).
#
# O avaliar_ligacao DO REPOSITORIO por cima da imagem: o recorte --cidade
# entrou depois do build da noite.
D=$HOME/producao_canoas
REPO=$HOME/Documentos/sistemas/radarComercial
cd $REPO || exit 1
rodada=0; vazias=0
busca_acabou() {
  grep -q FIM $D/busca_i9.progresso 2>/dev/null && \
  ssh -o BatchMode=yes -o ConnectTimeout=10 notebook \
    "grep -q FIM ~/producao_canoas/busca_notebook.progresso" 2>/dev/null
}
while true; do
  rodada=$((rodada + 1))
  if [ $((rodada % 6)) -eq 0 ]; then
    echo "$(date +%T) casamento dos órfãos" >> $D/avaliacao.progresso
    docker run --rm --name radar-casar-orfaos --env-file .env -e A2L_DB_HOST=192.168.3.10 \
      -e RADAR_CONEXOES=1 -e PYTHONIOENCODING=utf-8 -w /app radar-comercial-minerador-worker \
      timeout 1800 python3 -u casar_por_endereco.py --cidade Canoas --so-orfaos --aplicar \
      >> $D/casar.log 2>&1
  fi
  BUSCA="--exigir-busca-web"
  if busca_acabou; then BUSCA=""; fi
  echo "$(date +%T) rodada $rodada ${BUSCA:-sem exigir busca web}" >> $D/avaliacao.progresso
  docker run --rm --name radar-avaliacao-canoas --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e RADAR_CONEXOES=3 -e PYTHONIOENCODING=utf-8 -w /app \
    -v $REPO/avaliar_ligacao.py:/app/avaliar_ligacao.py:ro \
    radar-comercial-minerador-worker \
    python3 -u avaliar_ligacao.py --cidade Canoas $BUSCA --vinculo-novo --sem-catalogo \
      --trabalhadores 12 --aplicar >> $D/avaliacao.log 2>&1
  if tail -60 $D/avaliacao.log | grep -q "   0 ligação(ões) na fila"; then
    vazias=$((vazias + 1))
  else
    vazias=0
  fi
  if [ -z "$BUSCA" ] && [ $vazias -ge 2 ]; then
    break
  fi
  sleep 300
done
echo "$(date +%T) FIM" >> $D/avaliacao.progresso
