-- 0046_pois_ponto_indexado.sql
--
-- O PONTO DO POI, MATERIALIZADO E INDEXADO.
--
-- O cruzamento por ligacao (cruzar_ligacao.py) casa cada POI com as ligacoes
-- da base do cliente por distancia (st_dwithin, 60 m). A coordenada do POI vem
-- de `coalesce(maps_lat, lat_origem)` / `coalesce(maps_lng, lng_origem)`, e ate
-- 03/09/2026 o ponto era montado na hora, dentro da query:
--
--     st_setsrid(st_makepoint(coalesce(maps_lng, lng_origem),
--                             coalesce(maps_lat, lat_origem)), 4326)
--
-- Ponto calculado nao tem indice. O join entao so podia usar o GiST do lado da
-- `cadastro_corsan`, e para os 104 mil POIs de Canoas de uma vez a query ficou
-- CPU-bound por mais de 14 minutos. A escala alvo (base estadual da Corsan, 2,5
-- milhoes de ligacoes) torna isso inviavel: desempenho e criterio de primeira
-- classe, nao "depois a gente otimiza".
--
-- A coluna e GERADA (stored): sai das mesmas colunas de origem, entao nunca
-- diverge delas, e o GiST sobre ela deixa o join usar indice dos dois lados.
-- Nula quando o POI nao tem coordenada — e o filtro `pt_geo is not null` no
-- cruzamento substitui o antigo `coalesce(maps_lat, lat_origem) is not null`.

alter table radar_comercial.pois
  add column if not exists pt_geo geography(Point, 4326)
  generated always as (
    case when coalesce(maps_lat, lat_origem) is not null
              and coalesce(maps_lng, lng_origem) is not null
         then st_setsrid(st_makepoint(coalesce(maps_lng, lng_origem),
                                       coalesce(maps_lat, lat_origem)), 4326)::geography
    end) stored;

create index if not exists ix_pois_ptgeo
  on radar_comercial.pois using gist (pt_geo);

analyze radar_comercial.pois;
