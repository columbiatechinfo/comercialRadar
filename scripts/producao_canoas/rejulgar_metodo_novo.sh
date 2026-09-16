#!/bin/bash
# =============================================================================
# rejulgar_metodo_novo.sh — o rejulgamento das reprovadas e das que foram para revisão humana, no método novo
# (dono do produto, 15/09/2026, 22h: "volte todas as reprovadas e enviadas pra verificação humana julgadas no
# método v6 na ordem de prioridade já conhecida pra ser julgadas novamente nesse método novo").
#
# O QUE MUDOU NO MÉTODO, e por isso elas voltam: a fonte única agora também PROMOVE (a fachada com sinal de até
# 2 anos aprova sozinha, e o código só derrubava), a loja vista no iFood há até 6 meses e a avaliação de cliente
# no Google de até 12 meses bastam sozinhas, a foto publicada datada conta como prova do Maps, e o prompt diz que
# fachada confirmativa recente pesa muito.
#
# A FILA SAI DO BANCO A CADA VOLTA, e não de um arquivo: assim ela pega também os lotes R_ que forem julgados
# com o método anterior enquanto isto roda, e termina sozinha quando não sobra ninguém. A ordem é a do dono do
# produto: iFood, depois Google Maps, depois o resto; em cada grupo SIM antes de SIM_COM_ANALISE_HUMANA.
#
# Roda por cron com trava (não acumula duas cópias) e respeita o arquivo PARAR:
#   */5 * * * * flock -n $HOME/producao_canoas/orquestrador/.trava_metodo_novo bash $HOME/producao/radarComercial/scripts/producao_canoas/rejulgar_metodo_novo.sh >> $HOME/producao_canoas/orquestrador/cron_metodo_novo.log 2>&1
# =============================================================================
set -u
REPO=$HOME/producao/radarComercial
O=$HOME/producao_canoas/orquestrador
L=$O/lotes
BLOCO=${METODO_NOVO_BLOCO:-500}
SIMULTANEOS=${METODO_NOVO_SIMULTANEOS:-40}
MARCA="fonte única promove"
cd $REPO || exit 1
RUN="docker run --rm --init --network host --env-file .env -e A2L_DB_HOST=192.168.3.10 -e PYTHONIOENCODING=utf-8 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -w /app"
IMG=radar-comercial-minerador-worker
SQL="docker exec -i supabase-db psql -U supabase_admin -d a2l -At"
diga() { echo "$(date '+%d/%m %T') W. $*" >> $O/progresso; }

[ -f $O/PARAR ] && exit 0
[ -f $O/W_feito ] && exit 0
mkdir -p $L
n=0
while true; do
  [ -f $O/PARAR ] && { diga "parado pelo arquivo PARAR"; exit 0; }
  # A PRÓXIMA LEVA, na ordem de prioridade. `processo not like '%MARCA%'` é o que separa quem já passou pelo
  # método novo de quem ainda não passou.
  $SQL > $O/W_bloco.txt <<EOF
select lv.ligacao
  from radar_comercial.ligacao_veredito lv
  join resources_root.cadastro_corsan c on c.num_ligacao = lv.ligacao::bigint
 where (lv.percepcao->>'processo' like 'enxuto de 15/09/2026 v5%'
     or lv.percepcao->>'processo' like 'enxuto de 15/09/2026 v6%')
   and lv.percepcao->>'processo' not like '%$MARCA%'
   and lv.veredito <> 'aprovado'
 order by case when exists (select 1 from radar_comercial.ligacao_poi lp join radar_comercial.pois p on p.id = lp.poi_id
                             where lp.ligacao = lv.ligacao and lp.descartado_em is null and p.fonte = 'ifood') then 1
               when exists (select 1 from radar_comercial.ligacao_poi lp join radar_comercial.pois p on p.id = lp.poi_id
                             where lp.ligacao = lv.ligacao and lp.descartado_em is null and p.fonte = 'maps') then 2
               else 3 end,
          case when c.qualificacao = 'SIM' then 1 else 2 end,
          lv.ligacao
 limit $BLOCO;
EOF
  quantas=$(grep -c . $O/W_bloco.txt)
  if [ "$quantas" = 0 ]; then
    touch $O/W_feito
    diga "rejulgamento no método novo terminado: não sobrou ligação com o método anterior"
    exit 0
  fi
  n=$((n + 1))
  cp $O/W_bloco.txt $L/W_atual.txt
  docker rm -f radar-w-julgamento > /dev/null 2>&1
  $RUN --name radar-w-julgamento -e RADAR_CONEXOES=4 -v $REPO:/app:ro -v $O:/o $IMG \
    python -u avaliar_enxuto.py --leve --ligacoes-arquivo /o/lotes/W_atual.txt --julgar-sem-foto \
    --trabalhadores $SIMULTANEOS --aplicar > $L/W_julgamento.log 2>&1
  diga "bloco de $quantas: $(grep -a 'por minuto' $L/W_julgamento.log | tail -1 | xargs | cut -c1-80) · aprovadas $(grep -acE '^   [0-9]+ +aprovado' $L/W_julgamento.log) · falta $(($($SQL -c "select count(*) from radar_comercial.ligacao_veredito lv where (lv.percepcao->>'processo' like 'enxuto de 15/09/2026 v5%' or lv.percepcao->>'processo' like 'enxuto de 15/09/2026 v6%') and lv.percepcao->>'processo' not like '%$MARCA%' and lv.veredito <> 'aprovado'")))"
  # o log de cada bloco fica guardado
  cp $L/W_julgamento.log $L/W_$(printf '%03d' $n)_julgamento.log 2>/dev/null
done
