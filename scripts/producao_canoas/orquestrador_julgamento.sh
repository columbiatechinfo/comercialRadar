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
#   R. (uma vez) REJULGAMENTO ORDENADO de todos os vereditos (aprovadas, iFood, Maps, resto; SIM antes de SIM_COM);
#   B. (sempre) O LACO: pega a fila de Canoas (a mesma do julgamento), recaptura TUDO das
#      ligacoes do lote — ficha do Maps sem vizinhos e com data, fotos no Storage, foto de
#      rua de frente, leitura das placas, ficha do CNPJ, busca web — e so entao julga, gravando.
#
# ANTES DE JULGAR, A CONFERENCIA (dono do produto, 15/09/2026): "quem estiver com visada errada tem
# que ser recoletado, os dados acessorios como fotos com data e Serasa e web tambem tem que estar
# pronto, so entao os itens marcados como aprovados comecam a ser reavaliados". `conferir_evidencias`
# conta o que falta com a mesma selecao de cada passo; sobrou algo, a recaptura roda de novo (cada
# passo so faz o que falta) e o que ainda restar vai para o progresso com o numero.
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
  limpar radar-orq-fichas radar-orq-storage radar-orq-frente radar-orq-frente-0 radar-orq-frente-1 radar-orq-frente-2 radar-orq-cnpj
  $RUN --user 0 --name radar-orq-fichas -e RADAR_CONEXOES=2 -v $REPO:/app -v /app/node_modules -v $O:/o $IMG \
    sh scripts/com_tela.sh python -u recoletar_fichas.py --ligacoes-arquivo /o/$arq --sem-data-apos-horas ${HORAS_SEM_DATA:-24} --navegadores 8 --por-ip 25 \
    > $O/lotes/${tag}_fichas.log 2>&1
  log "   fichas do Maps: $(grep -h '■\|▶' $O/lotes/${tag}_fichas.log | tail -1 | cut -c1-150)"
  $RUN --name radar-orq-storage -e RADAR_CONEXOES=1 -v $REPO:/app:ro $IMG python -u imagens_para_storage.py --fotos \
    > $O/lotes/${tag}_storage.log 2>&1
  log "   storage: $(tail -1 $O/lotes/${tag}_storage.log | xargs)"
  # TRES PROCESSOS, um navegador cada (o render do Chromium no Xvfb trava num nucleo so): --parte k/3
  for k in 0 1 2; do
    $RUN --user 0 --name radar-orq-frente-$k -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG \
      sh scripts/com_tela.sh python -u recapturar_frente.py --ligacoes-arquivo /o/$arq --abas 8 --parte $k/3 --saida /o/lotes/${tag}_frente \
      > $O/lotes/${tag}_frente_$k.log 2>&1 &
  done
  wait
  log "   foto de rua: $(grep -h 'ligacoes ·' $O/lotes/${tag}_frente_0.log | tail -1 | xargs) · $(grep -h '■' $O/lotes/${tag}_frente_[012].log | cut -c1-120 | tr '\n' ' ')"
  limpar radar-orq-leitura
  $RUN --name radar-orq-leitura -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG     python -u ler_fotos_de_rua.py --ligacoes-arquivo /o/$arq --simultaneas 60 > $O/lotes/${tag}_leitura.log 2>&1
  log "   leitura das placas: $(grep -h '■\|▶' $O/lotes/${tag}_leitura.log | tail -1 | cut -c1-150)"
  docker run --rm --init --name radar-orq-cnpj --network host --shm-size 2g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
    -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e RADAR_CONEXOES=1 -v $REPO:/app:ro -v $O:/o -w /app $IMGQ \
    python3 -u fichas_cnpj.py --ligacoes-arquivo /o/$arq --navegadores 3 --aplicar > $O/lotes/${tag}_cnpj.log 2>&1
  log "   ficha do CNPJ: $(grep -h '■\|▶' $O/lotes/${tag}_cnpj.log | tail -1 | cut -c1-150)"
  # A BUSCA WEB (DuckDuckGo e Yahoo pelo Camoufox quente; so as ligacoes ainda sem busca)
  limpar radar-orq-sonda radar-orq-busca
  for t in 1 2 3; do
    if docker run --rm --init --name radar-orq-sonda --network host --shm-size 2g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
         -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e RADAR_CONEXOES=1 -v $REPO:/app:ro -w /app $IMGQ \
         python3 -u buscar_web.py --sonda > $O/lotes/${tag}_sonda.log 2>&1; then
      docker run --rm --init --name radar-orq-busca --network host --shm-size 8g --env-file .env -e A2L_DB_HOST=192.168.3.10 \
        -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o -w /app $IMGQ \
        python3 -u buscar_web.py --ligacoes-arquivo /o/$arq --trabalhadores 16 --aplicar 2>&1 \
        | grep --line-buffered -v "INFO: Fetched\|WARNING\|Call log\|navigating to\|Retrying in" > $O/lotes/${tag}_busca.log
      break
    fi
    log "   busca web: a sonda nao respondeu ($t/3), nova sonda em 5 min"
    sleep 300
  done
  log "   busca web: $(grep -h '■\|▶' $O/lotes/${tag}_busca.log 2>/dev/null | tail -1 | cut -c1-150)"
}

# $1: arquivo de ligacoes · $2: rotulo. Sai 0 se nada falta.
conferir() {
  local arq=$1 tag=$2
  limpar radar-orq-conferir
  $RUN --name radar-orq-conferir -e RADAR_CONEXOES=2 -v $REPO:/app:ro -v $O:/o $IMG \
    python -u conferir_evidencias.py --ligacoes-arquivo /o/$arq --sem-data-apos-horas ${HORAS_SEM_DATA:-24} \
    > $O/lotes/${tag}_conferencia.log 2>&1
  local r=$?
  log "   $(grep -h '■' $O/lotes/${tag}_conferencia.log | tail -1 | cut -c3-220)"
  return $r
}

# a recaptura, a conferencia e, se faltou algo, a segunda passada
preparar() {
  local arq=$1 tag=$2
  recapturar $arq $tag
  if ! conferir $arq $tag; then
    log "   faltou evidência: segunda passada da recaptura"
    recapturar $arq ${tag}_2
    conferir $arq ${tag}_2 || log "   o que restou não se resolve recapturando (sem panorama, sem busca possível): julgando assim"
  fi
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

# ── R. o rejulgamento de todos os vereditos, na ordem do dono do produto (15/09/2026) ─────────────
# Substitui a etapa C (so as 4.453 do processo leve). Entram tambem os 19.506 vereditos de 13/09, que ficavam com
# as regras antigas (215 das 216 reprovadas com iFood vinham deles). Ordem (`fila_rejulgamento.py`): as aprovadas
# hoje, as com iFood, as com Google Maps, o resto; em cada grupo SIM antes de SIM_COM_ANALISE_HUMANA. Lotes de 600,
# cada um com a recaptura no padrao atual (seta, visada, planta, datas), a conferencia e so entao o julgamento.
# Antes do primeiro lote, a copia dos vereditos (`ligacao_veredito_antes_r`).
if [ ! -f $O/R_feito ]; then
  if [ ! -f $O/R_fila_pronta ]; then
    log "R. rejulgamento ordenado: cópia dos vereditos e a fila"
    [ -s $O/C_ligacoes.txt ] || $SQL -c "select ligacao::text from radar_comercial.ligacao_veredito where percepcao::jsonb->>'processo' like 'enxuto de 15/09/2026%' order by 1" > $O/C_ligacoes.txt
    [ -s $O/lotes/lote_2.txt ] && sort -u $O/C_ligacoes.txt $O/lotes/lote_2.txt -o $O/C_ligacoes.txt
    $SQL -c "create table if not exists radar_comercial.ligacao_veredito_antes_r as select * from radar_comercial.ligacao_veredito" >> $O/lotes/R_fila.log 2>&1
    limpar radar-orq-fila-r
    $RUN --name radar-orq-fila-r -e RADAR_CONEXOES=1 -v $REPO:/app:ro -v $O:/o $IMG \
      python -u fila_rejulgamento.py --etapa-c /o/C_ligacoes.txt --saida /o/R_fila.txt >> $O/lotes/R_fila.log 2>&1
    if [ -s $O/R_fila.txt ]; then
      rm -f $O/lotes/R_[0-9][0-9][0-9]
      split -l 600 -d -a 3 $O/R_fila.txt $O/lotes/R_
      for f in $O/lotes/R_[0-9][0-9][0-9]; do mv "$f" "$f.txt"; done
      touch $O/R_fila_pronta
      log "   $(grep -h '■' $O/lotes/R_fila.log | tail -1 | cut -c3-120) · $(ls $O/lotes/R_[0-9][0-9][0-9].txt | wc -l) lotes"
    else
      log "   a fila não saiu: $(tail -2 $O/lotes/R_fila.log | tr '\n' ' ' | cut -c1-200)"
      exit 1
    fi
  fi
  # O JULGAMENTO CORRE EM PARALELO (dono do produto, 15/09/2026): aqui so a coleta — cada lote preparado ganha
  # `.evidencias` e o laco segue para o proximo; `julgamento_paralelo.sh` (cron e trava proprios) julga os lotes
  # prontos, na ordem da fila.
  for arq in $(ls $O/lotes/R_[0-9][0-9][0-9].txt 2>/dev/null | sort); do
    tag=$(basename $arq .txt)
    [ -f $O/lotes/$tag.evidencias ] && continue
    [ -f $O/PARAR ] && { log "parado pelo arquivo PARAR (etapa R, antes do $tag)"; exit 0; }
    log "R. $tag: $(wc -l < $arq) ligações — recaptura e conferência (o julgamento corre em paralelo)"
    preparar lotes/$tag.txt $tag
    touch $O/lotes/$tag.evidencias
  done
  log "R. evidências de todos os lotes prontas; esperando o julgamento paralelo"
  while [ $(ls $O/lotes/R_[0-9][0-9][0-9].feito 2>/dev/null | wc -l) -lt $(ls $O/lotes/R_[0-9][0-9][0-9].txt | wc -l) ]; do
    [ -f $O/PARAR ] && { log "parado pelo arquivo PARAR (esperando o julgamento da etapa R)"; exit 0; }
    sleep 120
  done
  [ -s $O/lotes/lote_2.txt ] && touch $O/lotes/lote_2.feito
  touch $O/R_feito
  log "R. pronto"
fi

# ── B. o laco ────────────────────────────────────────────────────────────────
n=$(ls $O/lotes/lote_*.txt 2>/dev/null | sed 's/.*lote_\([0-9]*\)\.txt/\1/' | sort -n | tail -1)
n=${n:-0}
# o lote que o i9 ou o cron interrompeu no meio e refeito (as etapas pulam o que ja esta feito)
if [ $n -gt 0 ] && [ ! -f $O/lotes/lote_${n}.feito ] && [ -s $O/lotes/lote_${n}.txt ]; then
  log "B. retomando o lote $n interrompido"
  preparar lotes/lote_${n}.txt lote_${n}
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
  preparar lotes/$tag.txt $tag
  julgar lotes/$tag.txt $tag
  touch $O/lotes/$tag.feito
  sleep 60
done
