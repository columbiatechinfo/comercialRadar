#!/bin/bash
# =============================================================================
# O orquestrador do julgamento de Canoas (dono do produto, 15/09/2026)
#
# RODA SOZINHO NO i9 E VOLTA PELO CRON. Em 15/09/2026 o rejulgamento das aprovadas
# acabou as 04:46 e o passo seguinte dependia da sessao do assistente, que caiu com o
# notebook: ~3 h de i9 e Spark paradas. Daqui em diante nada depende de sessao:
#
#   */5 * * * * flock -n $HOME/producao_canoas/orquestrador/.trava \
#       bash $HOME/producao/radarComercial/scripts/producao_canoas/orquestrador_julgamento.sh
#
# O `flock -n` garante um so; se o processo morrer ou o i9 reiniciar, o cron religa em
# ate 5 min e ele retoma: cada etapa marca o que ja fez.
#
# O QUE FAZ, em ordem:
#   A. (uma vez) CONSOLIDAR AS APROVADAS de 15/09: ficha do CNPJ no Serasa e datas das
#      fotos que faltaram, e rejulga so as ligacoes em que algo mudou;
#   C. (uma vez) EVIDENCIAS CORRIGIDAS e rejulgamento de todos os ja julgados;
#   B. (sempre) O LACO: pega a fila de Canoas (a mesma do julgamento), recaptura TUDO das
#      ligacoes do lote — ficha do Maps sem vizinhos e com data, fotos no Storage, foto de
#      rua de frente, ficha do CNPJ — e so entao julga no processo leve v3, gravando.
#
# PARAR: touch ~/producao_canoas/orquestrador/PARAR (para no fim da etapa em curso e o
# cron nao religa enquanto o arquivo existir).
# Progresso: ~/producao_canoas/orquestrador/progresso
# =============================================================================
REPO=$HOME/producao/radarComercial
O=$HOME/producao_canoas/orquestrador
mkdir -p $O/lotes
cd $REPO || exit 1
P=$O/progresso
log() { echo "$(date '+%d/%m %T') $*" >> $P; }
[ -f $O/PARAR ] && exit 0

RUN="docker run --rm --init --network host --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -w /app"
IMG=radar-comercial-minerador-worker
IMGQ=radar-busca-camoufox
SQL="docker exec -i supabase-db psql -U supabase_admin -d a2l -At"
limpar() { docker rm -f "$@" > /dev/null 2>&1; }

# $1: arquivo de ligacoes relativo a $O · $2: rotulo dos logs
recapturar() {
  local arq=$1 tag=$2
  limpar radar-orq-fichas radar-orq-storage radar-orq-frente radar-orq-cnpj
  $RUN --user 0 --name radar-orq-fichas -e RADAR_CONEXOES=2 -v $REPO:/app -v /app/node_modules -v $O:/o $IMG \
    sh scripts/com_tela.sh python -u recoletar_fichas.py --ligacoes-arquivo /o/$arq --sem-data-apos-horas ${HORAS_SEM_DATA:-24} --navegadores 8 --por-ip 25 \
    > $O/lotes/${tag}_fichas.log 2>&1
  log "   fichas do Maps: $(grep -h '■\|▶' $O/lotes/${tag}_fichas.log | tail -1 | cut -c1-150)"
  $RUN --name radar-orq-storage -e RADAR_CONEXOES=1 -v $REPO:/app:ro $IMG python -u imagens_para_storage.py --fotos \
    > $O/lotes/${tag}_storage.log 2>&1
  log "   storage: $(tail -1 $O/lotes/${tag}_storage.log | xargs)"
  $RUN --user 0 --name radar-orq-frente -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG \
    sh scripts/com_tela.sh python -u recapturar_frente.py --ligacoes-arquivo /o/$arq --abas 8 --saida /o/lotes/${tag}_frente \
    > $O/lotes/${tag}_frente.log 2>&1
  log "   foto de rua: $(grep -h 'ligacoes ·' $O/lotes/${tag}_frente.log | tail -1 | xargs) $(grep -h '■' $O/lotes/${tag}_frente.log | tail -1 | cut -c1-120)"
  limpar radar-orq-leitura
  $RUN --name radar-orq-leitura -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG     python -u ler_fotos_de_rua.py --ligacoes-arquivo /o/$arq --simultaneas 60 > $O/lotes/${tag}_leitura.log 2>&1
  log "   leitura das placas: $(grep -h '■\|▶' $O/lotes/${tag}_leitura.log | tail -1 | cut -c1-150)"
  docker run --rm --init --name radar-orq-cnpj --network host --shm-size 2g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e RADAR_CONEXOES=1 -v $REPO:/app:ro -v $O:/o -w /app $IMGQ \
    python3 -u fichas_cnpj.py --ligacoes-arquivo /o/$arq --navegadores 3 --aplicar > $O/lotes/${tag}_cnpj.log 2>&1
  log "   ficha do CNPJ: $(grep -h '■\|▶' $O/lotes/${tag}_cnpj.log | tail -1 | cut -c1-150)"
}

julgar() {
  local arq=$1 tag=$2
  limpar radar-orq-julgamento
  $RUN --name radar-orq-julgamento -e RADAR_CONEXOES=4 -v $REPO:/app:ro -v $O:/o $IMG \
    python -u avaliar_enxuto.py --leve --ligacoes-arquivo /o/$arq --julgar-sem-foto --trabalhadores 120 --aplicar \
    > $O/lotes/${tag}_julgamento.log 2>&1
  log "   julgamento: $(grep 'por minuto' $O/lotes/${tag}_julgamento.log | tail -1 | xargs) · aprovadas $(grep -cE '^\s+[0-9]+\s+aprovado' $O/lotes/${tag}_julgamento.log) · falhas $(grep -ciE 'FALHOU|ERRO:' $O/lotes/${tag}_julgamento.log)"
}

# ── A. consolidar as aprovadas de 15/09 (uma vez) ────────────────────────────
if [ ! -f $O/A_feito ]; then
  log "A. consolidar as aprovadas rejulgadas em 15/09: ficha do CNPJ e fotos sem data"
  if [ ! -f $O/A_recaptura_feita ]; then
    cp $HOME/producao_canoas/rejulgar_leve/ligacoes.txt $O/A_ligacoes.txt
    date -u '+%Y-%m-%d %H:%M:%S+00' > $O/A_inicio
    HORAS_SEM_DATA=0 recapturar A_ligacoes.txt A
    touch $O/A_recaptura_feita
  fi
  INI=$(cat $O/A_inicio)
  $SQL > $O/A_afetadas.txt <<EOF
with ligs as (select unnest(string_to_array('$(tr '\n' ',' < $O/A_ligacoes.txt | sed 's/,$//')', ',')) ligacao)
select distinct lp.ligacao from radar_comercial.ligacao_poi lp join ligs using (ligacao)
  join radar_comercial.pois p on p.id = lp.poi_id
 where lp.descartado_em is null and (
       exists (select 1 from radar_comercial.ficha_cnpj_web f where f.cnpj = regexp_replace(coalesce(p.cnpj,''), '\D', '', 'g')
                  and f.consultado_em >= '$INI' and f.texto_ia is not null)
    or exists (select 1 from radar_comercial.maps_data m where m.poi_id = p.id and m.resultados_web_em >= '$INI')
    or exists (select 1 from radar_comercial.poi_evidencia e where e.poi_id = p.id and e.tipo = 'sv_frente' and e.capturado_em >= '$INI'))
order by 1;
EOF
  log "   $(wc -l < $O/A_afetadas.txt) ligações mudaram (ficha do CNPJ, fotos ou foto de rua): rejulgando"
  if [ -s $O/A_afetadas.txt ]; then julgar A_afetadas.txt A; fi
  touch $O/A_feito
  log "A. pronto"
fi

# ── C. evidencias corrigidas e rejulgamento de todos os ja julgados (uma vez, 15/09/2026) ─────
# A foto de rua descentrada (987 de 6.106), a ligacao sem foto (sem registro a 60 m: foto no hidrometro) e as
# placas que a IA ignorava (leitura so da foto). Dono do produto: "obtem primeiro as evidencias, so entao roda
# novamente todos os que ja rodaram com prompt corrigido e evidencias corrigidas".
if [ ! -f $O/C_feito ]; then
  log "C. evidências corrigidas de todos os já julgados, e só então o rejulgamento"
  if [ ! -f $O/C_evidencias_feitas ]; then
    $SQL -c "select ligacao::text from radar_comercial.ligacao_veredito where percepcao::jsonb->>'processo' like 'enxuto de 15/09/2026 v3%' order by 1" > $O/C_ligacoes.txt
    [ -s $O/lotes/lote_2.txt ] && sort -u $O/C_ligacoes.txt $O/lotes/lote_2.txt -o $O/C_ligacoes.txt
    log "   $(wc -l < $O/C_ligacoes.txt) ligações (as julgadas no v3 e o lote 2 interrompido)"
    recapturar C_ligacoes.txt C
    touch $O/C_evidencias_feitas
  fi
  log "   rejulgando as $(wc -l < $O/C_ligacoes.txt) com o prompt v4"
  julgar C_ligacoes.txt C
  [ -s $O/lotes/lote_2.txt ] && touch $O/lotes/lote_2.feito
  touch $O/C_feito
  log "C. pronto"
fi

# ── B. o laco ────────────────────────────────────────────────────────────────
n=$(ls $O/lotes/lote_*.txt 2>/dev/null | sed 's/.*lote_\([0-9]*\)\.txt/\1/' | sort -n | tail -1)
n=${n:-0}
# o lote que o i9 ou o cron interrompeu no meio e refeito (as etapas pulam o que ja esta feito)
if [ $n -gt 0 ] && [ ! -f $O/lotes/lote_${n}.feito ] && [ -s $O/lotes/lote_${n}.txt ]; then
  log "B. retomando o lote $n interrompido"
  recapturar lotes/lote_${n}.txt lote_${n}
  julgar lotes/lote_${n}.txt lote_${n}
  touch $O/lotes/lote_${n}.feito
fi
while true; do
  [ -f $O/PARAR ] && { log "parado pelo arquivo PARAR"; exit 0; }
  n=$((n + 1))
  tag=lote_$n
  limpar radar-orq-fila
  $RUN --name radar-orq-fila -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG \
    python -u avaliar_enxuto.py --cidade Canoas --exigir-busca-web --vinculo-novo --limite 600 --listar-fila /o/lotes/$tag.txt \
    > $O/lotes/${tag}_fila.log 2>&1
  q=$(wc -l < $O/lotes/$tag.txt 2>/dev/null || echo 0)
  if [ "${q:-0}" -eq 0 ]; then
    rm -f $O/lotes/$tag.txt
    n=$((n - 1))
    log "B. fila vazia ($(tail -1 $O/lotes/${tag}_fila.log | cut -c1-100)); nova olhada em 15 min"
    sleep 900
    continue
  fi
  log "B. $tag: $q ligações — recaptura e julgamento"
  recapturar lotes/$tag.txt $tag
  julgar lotes/$tag.txt $tag
  touch $O/lotes/$tag.feito
  sleep 60
done
