-- O MEDOIDE de cada celula: o POI real mais proximo da mediana da celula.
--
-- Mediana de latitude e mediana de longitude, tiradas separadas, dao um ponto
-- que pode nao ser nenhum POI — numa celula em L ou cortada por rio ele cai no
-- vazio. O medoide e sempre um lugar que existe, porque e um dos proprios POIs.
\pset pager off
\pset format unaligned
\pset fieldsep ' | '

with caixa as (
  select min(coalesce(maps_lat, lat_origem)) s, max(coalesce(maps_lat, lat_origem)) n,
         min(coalesce(maps_lng, lng_origem)) o, max(coalesce(maps_lng, lng_origem)) l
    from radar_comercial.pois
   where cidade ilike 'canoas' and coalesce(maps_lat, lat_origem) is not null
),
ponto as (
  select p.nome,
         coalesce(p.maps_lat, p.lat_origem) lat,
         coalesce(p.maps_lng, p.lng_origem) lng,
         least(3, floor(4 * (coalesce(p.maps_lng, p.lng_origem) - c.o)
                          / nullif(c.l - c.o, 0))::int) gx,
         least(1, floor(2 * (coalesce(p.maps_lat, p.lat_origem) - c.s)
                          / nullif(c.n - c.s, 0))::int) gy
    from radar_comercial.pois p, caixa c
   where p.cidade ilike 'canoas' and coalesce(p.maps_lat, p.lat_origem) is not null
),
mediana as (
  select gx, gy, count(*) pois,
         percentile_cont(0.5) within group (order by lat) mlat,
         percentile_cont(0.5) within group (order by lng) mlng
    from ponto group by gx, gy
)
select distinct on (m.gx, m.gy)
       m.gx || ',' || m.gy as celula, m.pois,
       round(p.lat::numeric, 6) as lat, round(p.lng::numeric, 6) as lng,
       left(p.nome, 34) as medoide
  from mediana m
  join ponto p on p.gx = m.gx and p.gy = m.gy
 order by m.gx, m.gy,
          (p.lat - m.mlat) * (p.lat - m.mlat) + (p.lng - m.mlng) * (p.lng - m.mlng);
