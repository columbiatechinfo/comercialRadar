-- O MEDOIDE de cada celula: o POI real mais proximo da mediana da celula.
--
-- Mediana de latitude e mediana de longitude, tiradas separadas, dao um ponto
-- que pode nao ser nenhum POI — numa celula em L ou cortada por rio ele cai no
-- vazio. O medoide e sempre um lugar que existe, porque e um dos proprios POIs.
--
-- A mediana e tirada sobre TODOS os POIs da celula, porque e ela que representa
-- onde a densidade esta. Ja o medoide so pode sair dos POIs com RUA E NUMERO:
-- ele vai ser digitado no campo de endereco do iFood, e la um ponto sem numero
-- nao completa o cadastro. Em Canoas isso deixa 22.276 candidatos de 27.694.
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
  select p.nome, p.endereco, p.cidade, p.uf,
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
),
-- O endereco vem da fonte como "Rua Humaita, no 1258, 92025-340". O iFood quer
-- via e numero; o "no" atrapalha o autocomplete e o CEP vai a parte.
enderecavel as (
  select p.*,
         btrim(split_part(p.endereco, ',', 1))            as via,
         (regexp_match(p.endereco, 'n[ºo°] *([0-9]+)'))[1] as numero,
         (regexp_match(p.endereco, '([0-9]{5}-[0-9]{3})'))[1] as cep
    from ponto p
)
-- Tres candidatos por celula, e nao um so: o autocomplete do iFood nao conhece
-- toda rua que existe (dois dos oito medoides nao devolveram sugestao nenhuma),
-- e uma celula sem plano B vira celula perdida.
, ranqueado as (
  select m.gx, m.gy, m.pois, e.lat, e.lng, e.via, e.numero, e.cep, e.cidade, e.nome,
         row_number() over (
           partition by m.gx, m.gy
           order by (e.lat - m.mlat) * (e.lat - m.mlat)
                  + (e.lng - m.mlng) * (e.lng - m.mlng)) as posto
    from mediana m
    join enderecavel e on e.gx = m.gx and e.gy = m.gy
   where e.numero is not null and e.via <> ''
)
select gx || ',' || gy as celula, posto, pois,
       round(lat::numeric, 6) as lat, round(lng::numeric, 6) as lng,
       via || ', ' || numero || ', ' || cidade as busca,
       coalesce(cep, '') as cep,
       left(nome, 34) as medoide
  from ranqueado
 where posto <= 3
 order by gx, gy, posto;
