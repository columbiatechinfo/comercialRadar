#!/bin/bash
# =============================================================================
# Retroativo das fotos dos vizinhos e das regras novas (dono do produto, 14/09/2026)
#
# RODA DA COPIA DE PRODUCAO, depois de publicar a versao com o extrator novo
# (510d1a2) e as regras de numero, busca complementar e prova datada. Em ordem:
#
#   1. recoleta a ficha do Maps dos POIs que deram foto a algum veredito (1.726):
#      so as fotos do proprio lugar, com data, e os "Resultados da Web";
#   2. sobe as fotos novas para o Storage (a IA e a SEEK leem de la);
#   3. guarda o veredito atual das ligacoes a rejulgar (tabela de copia);
#   4. rejulga as aprovadas + as que usaram foto do Google (4.622), com gravacao
#      por cima (upsert): a SEEK nunca fica sem a ligacao no meio;
#   5. a checagem da rodada (um POI, uma instalacao) sobre todas as aprovadas.
#
#   bash retro_fotos_vizinhos.sh            # tudo
#   bash retro_fotos_vizinhos.sh 3          # a partir do passo 3
#
# Progresso em ~/producao_canoas/retro_fotos/progresso.
# =============================================================================
REPO=$HOME/producao/radarComercial
D=$HOME/producao_canoas/retro_fotos
mkdir -p $D
cd $REPO || exit 1
P=$D/progresso
passo() { echo "$(date +%T) $*" | tee -a $P; }
DESDE=${1:-1}
RUN="docker run --rm --init --network host --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -w /app"
MONTA="-v $REPO:/app -v /app/node_modules"
IMG=radar-comercial-minerador-worker
SQL="docker exec -i supabase-db psql -U supabase_admin -d a2l -At -v ON_ERROR_STOP=1"

if [ $DESDE -le 1 ]; then
  passo "1. recoleta das fichas do Maps"
  $RUN --user 0 --name radar-retro-fichas -e RADAR_CONEXOES=2 $MONTA $IMG sh scripts/com_tela.sh \
    python -u recoletar_fichas.py --dos-vereditos --retomar --navegadores 8 --por-ip 25 \
    2>&1 | grep --line-buffered -v "IPs est\|cache de proxies" >> $D/recoleta.log
  passo "   $(tail -1 $D/recoleta.log)"
fi

if [ $DESDE -le 2 ]; then
  passo "2. fotos novas para o Storage"
  $RUN --name radar-retro-storage -e RADAR_CONEXOES=2 $MONTA $IMG python -u imagens_para_storage.py --fotos \
    >> $D/storage.log 2>&1
  passo "   $(tail -1 $D/storage.log)"
fi

if [ $DESDE -le 3 ]; then
  passo "3. copia do veredito atual das ligacoes a rejulgar"
  $SQL > $D/ligacoes.txt <<'EOF'
select ligacao::text from radar_comercial.ligacao_veredito where veredito = 'aprovado'
union
select distinct v.ligacao::text
  from radar_comercial.ligacao_veredito v
  cross join lateral jsonb_array_elements(coalesce(v.percepcao::jsonb->'fotos_ref','[]')) r
 where r->>'tipo' = 'foto publicada'
order by 1;
EOF
  $SQL <<'EOF'
create table if not exists radar_comercial.ligacao_veredito_antes_retro_fotos as
  select now() as copiado_em, v.* from radar_comercial.ligacao_veredito v where false;
insert into radar_comercial.ligacao_veredito_antes_retro_fotos
  select now(), v.* from radar_comercial.ligacao_veredito v
   where v.veredito = 'aprovado'
      or exists (select 1 from jsonb_array_elements(coalesce(v.percepcao::jsonb->'fotos_ref','[]')) r
                  where r->>'tipo' = 'foto publicada');
EOF
  passo "   $(wc -l < $D/ligacoes.txt) ligacoes; copia em radar_comercial.ligacao_veredito_antes_retro_fotos"
fi

if [ $DESDE -le 4 ]; then
  passo "4. rejulgamento com as regras novas"
  $RUN --name radar-retro-julgamento -e RADAR_CONEXOES=4 $MONTA -v $D:/retro:ro $IMG \
    python -u avaliar_enxuto.py --ligacoes-arquivo /retro/ligacoes.txt --julgar-sem-foto --trabalhadores 120 --aplicar \
    > $D/julgamento.log 2>&1
  passo "   $(grep 'por minuto' $D/julgamento.log | tail -1 | xargs)"
fi

if [ $DESDE -le 5 ]; then
  passo "5. checagem da rodada (um POI, uma instalacao)"
  $RUN --name radar-retro-checagem -e RADAR_CONEXOES=2 $MONTA -v $D:/retro $IMG \
    python -u checagem_veredito.py --aplicar --antes /retro/checagem_antes.json > $D/checagem.log 2>&1
  passo "   $(tail -2 $D/checagem.log | xargs)"
fi
passo "■ retroativo pronto"
