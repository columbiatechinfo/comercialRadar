-- 0065 · os links deixam de ser carga de mão única
--
-- O QUE FICOU PELA METADE NO 0062. Aquela migração montou as quatro URLs e
-- carregou 72.341 links — uma vez. POI minerado DEPOIS dela não ganha link
-- nenhum, porque nada no fluxo repete a derivação. O sintoma só apareceria na
-- corrida seguinte, e apareceria como "a captura de página não achou alvos",
-- que aponta para o lugar errado.
--
-- A DERIVAÇÃO CONTINUA MORANDO NO BANCO, e por decisão do 0062: quem abrir o
-- Postgres daqui a um ano precisa ver como o slug do iFood vira link sem ter
-- de achar o Python. O que muda é que ela deixa de ser um `insert` solto
-- dentro de uma migração e vira função chamável — pelo fluxo de mineração, por
-- quem estiver depurando, por um `cron` se um dia fizer sentido.
--
-- É IDEMPOTENTE por construção: `on conflict do nothing` sobre o índice único
-- (id_empresa, poi_id, fonte, url). Rodar duas vezes seguidas devolve 0 na
-- segunda, e é assim que se confere que ela está certa.
--
-- CADA EMPRESA RECARREGA OS SEUS. A função roda com os privilégios de quem
-- chama, então as RLS de `maps_data`, `ifood_merchant`, `airbnb_anuncio` e
-- `pois` já limitam a derivação à empresa do chamador. Não há `security
-- definer` aqui de propósito: ele trocaria o papel e faria uma empresa
-- carregar links em cima do dado de outra.

set search_path to radar_comercial, extensions, public;

create or replace function radar_comercial.recarregar_links()
returns table (fonte text, novos bigint)
language plpgsql
as $$
declare n bigint;
begin
    insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
    select m.poi_id, m.id_empresa, 'maps', m.maps_url, 'Ficha no Google Maps'
      from maps_data m
     where coalesce(m.maps_url, '') <> ''
    on conflict do nothing;
    get diagnostics n = row_count;
    fonte := 'maps'; novos := n; return next;

    insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
    select i.poi_id, i.id_empresa, 'ifood',
           'https://www.ifood.com.br/delivery/' || i.slug, 'Loja no iFood'
      from ifood_merchant i
     where i.poi_id is not null and coalesce(i.slug, '') <> ''
    on conflict do nothing;
    get diagnostics n = row_count;
    fonte := 'ifood'; novos := n; return next;

    insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
    select a.poi_id, a.id_empresa, 'airbnb',
           'https://www.airbnb.com.br/rooms/' || a.anuncio_id, 'Anúncio no Airbnb'
      from airbnb_anuncio a
     where a.poi_id is not null and coalesce(a.anuncio_id, '') <> ''
    on conflict do nothing;
    get diagnostics n = row_count;
    fonte := 'airbnb'; novos := n; return next;

    insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
    select p.id, p.id_empresa, 'site', p.website, 'Site do estabelecimento'
      from pois p
     where coalesce(p.website, '') ~ '^https?://'
    on conflict do nothing;
    get diagnostics n = row_count;
    fonte := 'site'; novos := n; return next;
end $$;

comment on function radar_comercial.recarregar_links() is
    'Deriva os links do POI a partir de maps_data, ifood_merchant, '
    'airbnb_anuncio e pois.website. Idempotente. Chamada ao fim da mineração.';

grant execute on function radar_comercial.recarregar_links() to app_user;

-- Primeira chamada: tem de vir tudo zero, porque o 0062 já carregou.
select * from radar_comercial.recarregar_links();
