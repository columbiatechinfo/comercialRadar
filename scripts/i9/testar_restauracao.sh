#!/usr/bin/env bash
# Restaura o backup mais recente num banco DESCARTAVEL e confere linha a linha.
#
# Existe porque backup nunca restaurado e hipotese, nao backup. O modo de falhar
# de um dump e silencioso: ele e gerado, ocupa disco, parece certo, e so no dia
# do desastre se descobre que faltava um schema, uma extensao ou uma permissao.
#
# Restaura num banco separado de proposito — nunca por cima do que esta em uso.
set -uo pipefail
DEST=/home/orbisgrid/comercialradar-infra/backup
ULTIMO=$(ls -1t "$DEST"/bancos/produto-*.dump 2>/dev/null | head -1)
[ -z "$ULTIMO" ] && { echo "  nenhum dump encontrado"; exit 1; }
echo "  restaurando: $(basename "$ULTIMO") ($(du -h "$ULTIMO" | cut -f1))"

docker exec supabase-db psql -U postgres -d postgres -q -c "drop database if exists teste_restauracao" >/dev/null 2>&1
docker exec supabase-db psql -U postgres -d postgres -q -c "create database teste_restauracao" >/dev/null 2>&1

docker cp "$ULTIMO" supabase-db:/tmp/r.dump >/dev/null
# Erro de dono e de extensao ja existente e ruido esperado ao restaurar em banco
# novo; o que importa e a CONTAGEM no fim.
docker exec supabase-db pg_restore -U postgres -d teste_restauracao --no-owner --no-privileges -j 4 /tmp/r.dump >/dev/null 2>&1
docker exec supabase-db rm -f /tmp/r.dump

echo "  conferindo contagem: original x restaurado"
FALHAS=0
# QUALIFICADAS POR SCHEMA, e de TODAS as ferramentas. O dump ja e do banco
# inteiro, entao `radartelhados` sempre veio junto — o que faltava era CONFERIR.
# Uma restauracao "validada" que so olha um schema atesta menos do que parece:
# o schema novo poderia voltar vazio e o teste continuaria dizendo que passou.
for T in comercialradar.pois comercialradar.cadastro_cliente \
         comercialradar.fachada_anotacao comercialradar.cnefe_coletiva \
         comercialradar.comentarios comercialradar.horario_funcionamento \
         comercialradar.analise_ia comercialradar.streetview_imgs \
         comercialradar.images_urls \
         radartelhados.telhado radartelhados.telhado_fonte \
         radartelhados.quadra radartelhados.quadra_face \
         radartelhados.quadra_ponto radartelhados.via_osm \
         radartelhados.varredura radartelhados.analise_sessao; do
  A=$(docker exec supabase-db psql -U postgres -d postgres          -tAc "select count(*) from $T" 2>/dev/null | tr -d ' ')
  B=$(docker exec supabase-db psql -U postgres -d teste_restauracao -tAc "select count(*) from $T" 2>/dev/null | tr -d ' ')
  if [ "$A" = "$B" ] && [ -n "$A" ]; then
    printf "    %-34s %12s  confere\n" "$T" "$A"
  else
    printf "    %-34s %12s  x %-12s  DIVERGE\n" "$T" "$A" "${B:-vazio}"
    FALHAS=$((FALHAS+1))
  fi
done
# O Auth tambem tem de voltar: banco com dado e sem usuario nao serve.
U=$(docker exec supabase-db psql -U postgres -d teste_restauracao -tAc "select count(*) from auth.users" 2>/dev/null | tr -d ' ')
printf "    %-24s %12s  (schema auth veio junto)\n" "auth.users" "${U:-ausente}"
[ -z "$U" ] && FALHAS=$((FALHAS+1))

docker exec supabase-db psql -U postgres -d postgres -q -c "drop database teste_restauracao" >/dev/null 2>&1
echo "  banco de teste removido"
[ "$FALHAS" -eq 0 ] && echo "  RESTAURACAO VALIDADA" || echo "  $FALHAS divergencia(s) — backup NAO confiavel"
exit $FALHAS
