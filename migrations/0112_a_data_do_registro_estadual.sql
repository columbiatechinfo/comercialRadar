-- 0112 · a data do registro estadual na fonte (14/09/2026)
--
-- Decisao do dono do produto: a data das provas entra no veredito — prova de ate
-- 2 anos vale, mais velha nao aprova — e "os dados estaduais e de receita que
-- levam data de atualizacao/registro devem ser considerados quanto a data".
--
-- O Overture e o Foursquare declaram quando atualizaram o ponto (`update_time`,
-- `date_refreshed`) e o `status` dele; a extracao estadual guardou isso no Parquet
-- (`data_atualizacao`, `status`), mas a carga no banco nao trouxe. Medido: ha
-- ponto do Foursquare atualizado pela ultima vez em 2012. O OSM nao tem data.
--
-- `datas_estaduais.py` povoa pelo `cluster_id`, que ja casa a linha com o Parquet.
-- So colunas anulaveis.
set lock_timeout = '5s';

alter table radar_comercial.overture_data
    add column if not exists atualizado_na_fonte date,
    add column if not exists status_na_fonte text;

alter table radar_comercial.foursquare_data
    add column if not exists atualizado_na_fonte date,
    add column if not exists status_na_fonte text;

comment on column radar_comercial.overture_data.atualizado_na_fonte is
    'Quando o Overture atualizou este ponto (update_time do Parquet da extracao estadual).';
comment on column radar_comercial.foursquare_data.atualizado_na_fonte is
    'Quando o Foursquare atualizou este ponto (date_refreshed do Parquet da extracao estadual).';
