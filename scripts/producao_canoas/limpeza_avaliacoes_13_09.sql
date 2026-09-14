-- Limpeza das avaliacoes de IA de Canoas (13/09/2026), pedido do dono do produto:
-- "todas as avaliacoes de IA, mas nao os vinculos entre POI e ligacao — apenas
-- vamos reiniciar as avaliacoes da IA". Motivo: a busca web complementar
-- pesquisou dentro do Google Maps (erro meu) e contaminou os vereditos.
-- Os vinculos (ligacao_poi) NAO sao tocados. A copia de tudo fica guardada.
\set ON_ERROR_STOP 1
begin;
set local lock_timeout = '15s';
create table radar_comercial.ligacao_veredito_antes_13_09 as select * from radar_comercial.ligacao_veredito;
create table radar_comercial.poi_veredito_antes_13_09 as select * from radar_comercial.poi_veredito;
alter table radar_comercial.ligacao_veredito_antes_13_09 enable row level security;
alter table radar_comercial.poi_veredito_antes_13_09 enable row level security;
select 'copiados', (select count(*) from radar_comercial.ligacao_veredito_antes_13_09),
                   (select count(*) from radar_comercial.poi_veredito_antes_13_09);
delete from radar_comercial.ligacao_veredito;
delete from radar_comercial.poi_veredito;
select 'restam', (select count(*) from radar_comercial.ligacao_veredito),
                 (select count(*) from radar_comercial.poi_veredito);
commit;
