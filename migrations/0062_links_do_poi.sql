-- 0062 — os links do POI, um por fonte
--
-- O QUE FALTAVA
--
-- As pecas do link existem espalhadas e nenhuma e um link:
--
--     maps_data.maps_url          414   URL completa
--     ifood_merchant.slug       1.960   so o slug (`canoas-rs/chapa-quente`)
--     airbnb_anuncio.anuncio_id    39   so o id
--     pois.website             71.227   o site DO ESTABELECIMENTO, outra coisa
--
-- Quem quer "abra o anuncio deste POI" precisa saber montar tres URLs
-- diferentes, e a IA que vai avaliar precisa do link para tirar o print.
--
-- UMA TABELA, E NAO UMA COLUNA POR FONTE — decidido pelo dono do produto.
--
-- Um POI tem VARIOS links ao mesmo tempo: a ficha do Maps, a loja no iFood, o
-- anuncio no Airbnb, o site proprio, o Instagram. Coluna por fonte obrigaria
-- uma migracao a cada fonte nova, que e exatamente o defeito que a refatoracao
-- de ontem tirou da `pois`.
--
-- `url` E A CHAVE, junto com a fonte: o mesmo POI pode ter dois links da mesma
-- fonte (duas lojas do iFood no mesmo endereco acontece), e o par (fonte, url)
-- e o que de fato identifica.

set search_path to radar_comercial, extensions, public;

create table if not exists poi_link (
    id         bigserial primary key,
    id_empresa uuid not null,
    poi_id     bigint not null references pois(id) on delete cascade,
    fonte      text not null,
    url        text not null,
    -- O rotulo e o que a tela mostra: "Ver no iFood", "Anuncio no Airbnb".
    rotulo     text,
    -- Quando o link foi visto pela ultima vez respondendo. Link de anuncio
    -- morre — a loja sai do iFood, o anuncio some do Airbnb — e saber QUANDO
    -- ele ainda estava vivo e o que separa "nao existe mais" de "nunca abri".
    visto_em   timestamptz not null default now(),
    ativo      boolean
);

create unique index if not exists poi_link_unico
    on poi_link (id_empresa, poi_id, fonte, url);
create index if not exists poi_link_por_poi on poi_link (id_empresa, poi_id);

alter table poi_link owner to migrator;
grant select, insert, update, delete on poi_link to app_user;
grant select on poi_link to readonly;
grant usage, select on sequence poi_link_id_seq to app_user;
alter table poi_link enable row level security;
drop policy if exists poi_link_ver on poi_link;
create policy poi_link_ver on poi_link for select
    using ((select core.eh_suporte()) or (id_empresa = (select core.empresa_atual())));
drop policy if exists poi_link_mexer on poi_link;
create policy poi_link_mexer on poi_link for all
    using ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)))
    with check ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)));
drop trigger if exists poi_link_empresa on poi_link;
create trigger poi_link_empresa before insert on poi_link
    for each row execute function radar_comercial.preencher_empresa();

comment on table poi_link is
    'Os links do POI, um por fonte. Um POI tem varios ao mesmo tempo — ficha '
    'do Maps, loja no iFood, anuncio no Airbnb — e coluna por fonte obrigaria '
    'migracao a cada fonte nova.';

-- ── a carga do que ja existe ───────────────────────────────────────────────
-- As tres montagens de URL ficam AQUI e nao no codigo: quem consultar o banco
-- daqui a um ano precisa saber como o slug vira link sem abrir o Python.
insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
select m.poi_id, m.id_empresa, 'maps', m.maps_url, 'Ficha no Google Maps'
  from maps_data m
 where coalesce(m.maps_url, '') <> ''
on conflict do nothing;

insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
select i.poi_id, i.id_empresa, 'ifood',
       'https://www.ifood.com.br/delivery/' || i.slug, 'Loja no iFood'
  from ifood_merchant i
 where i.poi_id is not null and coalesce(i.slug, '') <> ''
on conflict do nothing;

insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
select a.poi_id, a.id_empresa, 'airbnb',
       'https://www.airbnb.com.br/rooms/' || a.anuncio_id, 'Anúncio no Airbnb'
  from airbnb_anuncio a
 where a.poi_id is not null and coalesce(a.anuncio_id, '') <> ''
on conflict do nothing;

-- O site do proprio estabelecimento e link tambem, e e o unico que vale para
-- POI que nao veio de plataforma nenhuma.
insert into poi_link (poi_id, id_empresa, fonte, url, rotulo)
select p.id, p.id_empresa, 'site', p.website, 'Site do estabelecimento'
  from pois p
 where coalesce(p.website, '') ~ '^https?://'
on conflict do nothing;

do $$
declare r record;
begin
    for r in select fonte, count(*) as n from poi_link group by 1 order by 2 desc
    loop
        raise notice 'links %: %', rpad(r.fonte, 10), r.n;
    end loop;
end $$;
