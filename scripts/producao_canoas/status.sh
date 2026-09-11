#!/bin/bash
# Status da producao de Canoas, as duas maquinas e o banco, numa linha.
D=$HOME/producao_canoas
q() { docker exec supabase-db psql -U supabase_admin -d a2l -Atc "$1" 2>/dev/null; }
b0=$(grep -o "\[[0-9]*/[0-9]*\] [0-9.]* consultas/min · falta ~[0-9]* min" $D/busca_i9.log 2>/dev/null | tail -1)
nb=$(ssh -o BatchMode=yes -o ConnectTimeout=8 notebook 'D=$HOME/producao_canoas; b=$(grep -o "\[[0-9]*/[0-9]*\] [0-9.]* consultas/min · falta ~[0-9]* min" $D/busca_notebook.log 2>/dev/null | tail -1); e=$(( $(grep -c Traceback $D/busca_notebook.log 2>/dev/null) - $(grep -c "Task exception was never retrieved" $D/busca_notebook.log 2>/dev/null) )); v=$(docker ps --format "{{.Names}}" | grep -c radar-busca-web); echo "${b:-—}|${e:-0}|$v"' 2>/dev/null)
b1=${nb%%|*}; r=${nb#*|}; e1=${r%%|*}; v1=${r##*|}
web=$(q "select count(distinct ligacao) filter (where ia is not null), count(*) filter (where bloqueado), count(*) filter (where ia is null and not bloqueado) from radar_comercial.busca_web where tipo='endereco'" | tr '|' ' ')
set -- $web
ver=$(q "select string_agg(veredito || ' ' || n, ' · ' order by veredito) from (select veredito, count(*) n from radar_comercial.ligacao_veredito group by 1) x")
e0=$(( $(cat $D/busca_i9.log $D/avaliacao.log $D/casar.log 2>/dev/null | grep -c Traceback) - $(grep -c "Task exception was never retrieved" $D/busca_i9.log 2>/dev/null) ))
v0=$(docker ps --format "{{.Names}}" | grep -cE "radar-(busca-web|avaliacao)")
echo "busca web: $1 de 21966 feitas, $2 bloqueadas, $3 falhas (i9 ${b0:-—} · notebook ${b1:-—}) | vereditos: ${ver:-nenhum} | contêineres i9 $v0 · notebook ${v1:-?} | Traceback i9 $e0 · notebook ${e1:-?}"
