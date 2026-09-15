#!/bin/bash
# =============================================================================
# Rejulgamento das aprovadas no processo LEVE v3 (dono do produto, 15/09/2026)
#
# RODA DA COPIA DE PRODUCAO, depois de publicar a versao com `avaliar_enxuto --leve`.
# As fichas do Maps e a foto de rua de frente das aprovadas ja foram recapturadas
# (recoletar_fichas + recapturar_frente, 14 e 15/09/2026). Em ordem:
#
#   1. a lista das aprovadas de hoje;
#   2. a copia do veredito atual delas (radar_comercial.ligacao_veredito_antes_leve_v3);
#   3. a foto de rua de frente do POI mais perto do hidrometro que ainda nao tem a
#      captura nova — nenhuma foto com a mira antiga vai para a IA;
#   4. o julgamento leve com gravacao; no fim da rodada, a checagem (um POI, uma
#      instalacao) roda sobre todas as aprovadas.
#
#   bash rejulgar_aprovadas_leve.sh          # tudo
#   bash rejulgar_aprovadas_leve.sh 3        # a partir do passo 3
#
# Progresso em ~/producao_canoas/rejulgar_leve/progresso.
# =============================================================================
REPO=$HOME/producao/radarComercial
D=$HOME/producao_canoas/rejulgar_leve
mkdir -p $D
cd $REPO || exit 1
P=$D/progresso
passo() { echo "$(date +%T) $*" | tee -a $P; }
DESDE=${1:-1}
RUN="docker run --rm --init --network host --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -w /app"
MONTA="-v $REPO:/app:ro -v $D:/rejulgar"
IMG=radar-comercial-minerador-worker
SQL="docker exec -i supabase-db psql -U supabase_admin -d a2l -At -v ON_ERROR_STOP=1"

if [ $DESDE -le 1 ]; then
  passo "1. lista das aprovadas"
  $SQL -c "select ligacao::text from radar_comercial.ligacao_veredito where veredito = 'aprovado' order by 1" > $D/ligacoes.txt
  passo "   $(wc -l < $D/ligacoes.txt) ligacoes"
fi

if [ $DESDE -le 2 ]; then
  passo "2. copia do veredito atual"
  $SQL <<'EOF'
set lock_timeout = '10s';
create table if not exists radar_comercial.ligacao_veredito_antes_leve_v3 as
  select now() as copiado_em, v.* from radar_comercial.ligacao_veredito v where false;
insert into radar_comercial.ligacao_veredito_antes_leve_v3
  select now(), v.* from radar_comercial.ligacao_veredito v where v.veredito = 'aprovado';
grant select on radar_comercial.ligacao_veredito_antes_leve_v3 to app_user, readonly;
EOF
  passo "   $($SQL -c "select count(*) from radar_comercial.ligacao_veredito_antes_leve_v3") linhas na copia"
fi

if [ $DESDE -le 3 ]; then
  passo "3. foto de rua de frente que falta"
  $RUN --user 0 --name radar-rejulgar-frente -e RADAR_CONEXOES=2 $MONTA $IMG sh scripts/com_tela.sh \
    python -u recapturar_frente.py --ligacoes-arquivo /rejulgar/ligacoes.txt --abas 8 --saida /rejulgar/frente \
    > $D/frente.log 2>&1
  passo "   $(grep -h 'ligacoes ·' $D/frente.log | tail -1 | xargs) · $(grep -h '■' $D/frente.log | tail -1 | xargs)"
fi

if [ $DESDE -le 4 ]; then
  passo "4. julgamento leve com gravacao"
  $RUN --name radar-rejulgar-leve -e RADAR_CONEXOES=4 $MONTA $IMG \
    python -u avaliar_enxuto.py --leve --ligacoes-arquivo /rejulgar/ligacoes.txt --julgar-sem-foto \
    --trabalhadores 120 --aplicar > $D/julgamento.log 2>&1
  passo "   $(grep 'por minuto' $D/julgamento.log | tail -1 | xargs)"
fi
passo "■ rejulgamento leve pronto"
