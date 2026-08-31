set -u
E=~/Documentos/sistemas/radarComercial/.env
set -a; . <(tr -d '\r' < "$E"); set +a
IMG=$(docker inspect supabase-db --format '{{.Config.Image}}')

roda() {
  echo "=== $1 ==="
  { echo "begin;"; cat "/tmp/ddl/$1"; echo "rollback;"; } > /tmp/ddl/_roda.sql
  # O codigo de saida do PSQL, nao o do `tail`. A versao anterior lia `$?`
  # depois de um cano e imprimia "codigo 0" com o erro na linha de cima.
  docker run --rm --network host -e U="$A2L_MIGRATOR_URL" -v /tmp/ddl:/ddl "$IMG" \
    bash -c 'psql "$U" -v ON_ERROR_STOP=1 -q -f /ddl/_roda.sql' > /tmp/ddl/_saida 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "  OK — sem erro (rc=0). Tudo foi criado e desfeito."
  else
    echo "  FALHOU (rc=$rc):"
    grep -E "ERROR|LINE|\^" /tmp/ddl/_saida | head -10 | sed 's/^/    /'
  fi
  echo
}
roda 0001_radar_comercial.sql
roda 0002_resources_root.sql

echo "=== ficou alguma coisa? (tem de ser 0 e 0) ==="
docker exec supabase-db psql -U supabase_admin -d a2l -Atc \
  "select n.nspname, count(c.oid) from pg_namespace n
     left join pg_class c on c.relnamespace = n.oid and c.relkind = 'r'
    where n.nspname in ('radar_comercial','resources_root')
    group by n.nspname order by 1"
