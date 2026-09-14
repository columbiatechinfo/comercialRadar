-- Limpeza das avaliacoes de IA do prompt anterior (13/09/2026, noite), pedido do
-- dono do produto: "so mantem a coleta da busca web, pq ela nao tem nada a ver
-- com o novo prompt". O prompt novo diz em campo proprio se as fotos mostram o
-- comercio e quais resultados da busca confirmam, e o motivo e detalhado.
-- FICAM: a busca web, os vinculos (ligacao_poi) e as avaliacoes ja feitas pelo
-- prompt novo (processo "enxuto de 13/09/2026 ..."). A copia do que sai fica guardada.
\set ON_ERROR_STOP 1
begin;
set local lock_timeout = '15s';
create table radar_comercial.ligacao_veredito_antes_prompt_novo as
  select * from radar_comercial.ligacao_veredito
   where coalesce(percepcao::jsonb->>'processo', '') not like 'enxuto de 13/09/2026%';
alter table radar_comercial.ligacao_veredito_antes_prompt_novo enable row level security;
select 'copiados', count(*) from radar_comercial.ligacao_veredito_antes_prompt_novo;
delete from radar_comercial.ligacao_veredito
 where coalesce(percepcao::jsonb->>'processo', '') not like 'enxuto de 13/09/2026%';
select 'restam (prompt novo)', count(*) from radar_comercial.ligacao_veredito;
commit;
