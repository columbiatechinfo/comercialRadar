\pset format unaligned
\pset fieldsep '|'
\echo '###TABELAS'
select c.relname, c.reltuples::bigint,
       pg_size_pretty(pg_total_relation_size(c.oid))
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='radar_comercial' and c.relkind='r'
 order by c.relname;

\echo '###COLUNAS'
select table_name, column_name, data_type
  from information_schema.columns
 where table_schema='radar_comercial'
 order by table_name, ordinal_position;

\echo '###FK'
select cl.relname as origem, a.attname as coluna,
       cf.relname as destino
  from pg_constraint co
  join pg_class cl on cl.oid = co.conrelid
  join pg_class cf on cf.oid = co.confrelid
  join pg_attribute a on a.attrelid = co.conrelid and a.attnum = co.conkey[1]
 where co.contype='f' and cl.relnamespace='radar_comercial'::regnamespace
 order by 1,2;

\echo '###CONTAGEM'
select 'pois', count(*) from radar_comercial.pois
union all select 'ligacao_poi', count(*) from radar_comercial.ligacao_poi
union all select 'maps_data', count(*) from radar_comercial.maps_data
union all select 'osm_data', count(*) from radar_comercial.osm_data
union all select 'overture_data', count(*) from radar_comercial.overture_data
union all select 'foursquare_data', count(*) from radar_comercial.foursquare_data
union all select 'cadastur_data', count(*) from radar_comercial.cadastur_data
union all select 'receita_data', count(*) from radar_comercial.receita_data
union all select 'ifood_merchant', count(*) from radar_comercial.ifood_merchant
union all select 'airbnb_anuncio', count(*) from radar_comercial.airbnb_anuncio
union all select 'comentarios', count(*) from radar_comercial.comentarios
union all select 'images_urls', count(*) from radar_comercial.images_urls
union all select 'tile_captura', count(*) from radar_comercial.tile_captura
union all select 'cadastro_corsan', count(*) from resources_root.cadastro_corsan;
