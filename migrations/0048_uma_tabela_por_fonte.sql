-- 0048 — uma tabela por fonte, e a `pois` volta a ser só o que é geral
--
-- O DIAGNÓSTICO, MEDIDO EM 03/09/2026
--
-- A `pois` tem 70 colunas e 301.297 linhas. Contando o preenchimento coluna a
-- coluna: 22 colunas estão COMPLETAMENTE vazias (`socios`, `ocr_texto`,
-- `situacao_cadastral`, `streetview_path`, `similaridade`, `distancia_m`…) e
-- outras 18 ficam abaixo de 1%. Vinte e nove por cento da tabela nunca guardou
-- nada, e o que guarda mistura três coisas diferentes:
--
--     o que é do ESTABELECIMENTO   nome, endereço, telefone, coordenada
--     o que é de UMA FONTE         place_id e plus_code (Maps), razao_social
--                                  (Receita), presente_no_ifood (iFood)
--     o que é de PROCESSO          match_valido, detalhado_por, revisar_manual
--
-- O custo não é estético. Cada fonte nova pedia mais uma coluna, que nascia
-- nula para os outros 300 mil POIs; e a fonte que não coubesse numa coluna
-- ficava sem lugar — foi o que aconteceu com OSM, Overture e Foursquare.
--
-- ELAS JÁ ESTÃO NO BANCO, E ESSE É O PONTO
--
-- Não é dado que falta coletar. Está lá, escondido atrás de um rótulo genérico:
--
--     fonte='estadual', fonte_dado='estadual:overture'   14.596 POIs
--     fonte='estadual', fonte_dado='estadual:fsq'        11.735 POIs
--     fonte='estadual', fonte_dado='estadual:osm'         1.196 POIs
--     fonte='estadual', fonte_dado NULO                 195.983 POIs
--
-- Os 195.983 são os que `origem_estadual.py` ainda não classificou — ele rodou
-- em Canoas e parou. E `place_id` carrega duas coisas incompatíveis: o place_id
-- do Google E `estadual:<cluster_id>`. Quem consulta a coluna não sabe qual dos
-- dois vai receber.
--
-- O DESENHO NOVO
--
--     pois              o que é verdade sobre o LUGAR, venha de onde vier
--     <fonte>_data      o que só aquela fonte sabe, 1:1 com o POI, por FK
--     comentarios       as avaliações de TODAS as fontes, com `fonte` na linha
--     images_urls       as imagens de TODAS as fontes, com `fonte` na linha
--     streetview_imgs   as do Street View, que já eram à parte
--     tile_captura      os tiles, presos à RUN que os capturou
--
-- POR QUE `comentarios` E `images_urls` NÃO VIRAM TABELAS NOVAS
--
-- Elas já SÃO as transversais que o desenho pede: `poi_id`, e uma linha por
-- item. Faltava só dizer de onde a linha veio. Criar `avaliacao` e `imagem` ao
-- lado significaria migrar 4.299 linhas e reescrever as rotas do painel para
-- ganhar o mesmo que uma coluna resolve. A adição é o conserto; a tabela nova
-- seria retrabalho pago com risco.
--
-- NADA É APAGADO AQUI. As colunas antigas de `pois` continuam no lugar, e o
-- sistema segue lendo delas enquanto a carga não estiver conferida. A remoção é
-- uma migração posterior, depois que os escritores e leitores mudarem.

set search_path to radar_comercial, public;

-- ── 1. as fontes que já existem, cada uma com a sua tabela ──────────────────

-- MAPS. Hoje mora em `pois`: 223.887 place_ids, 369 maps_url, 296 avaliações.
-- `place_id` aqui é SÓ o do Google — o `estadual:<cluster_id>` vai para a
-- tabela da fonte estadual, que é de onde ele veio.
create table if not exists maps_data (
    poi_id            bigint primary key references pois(id) on delete cascade,
    id_empresa        uuid not null,
    place_id          text,
    maps_url          text,
    plus_code         text,
    maps_lat          double precision,
    maps_lng          double precision,
    avaliacao         numeric,
    total_avaliacoes  integer,
    resumo_avaliacoes text,
    status_horario    text,
    preco_medio       text,
    ocr_texto         text,
    detalhado_em      timestamptz,
    detalhado_por     text,
    bruto             jsonb
);
create index if not exists maps_data_place_id on maps_data (id_empresa, place_id);

-- OSM. `osm_id` é o identificador estável do objeto; `tags` guarda o resto
-- porque o OSM não tem esquema fixo — hoje `shop`, amanhã `craft`.
create table if not exists osm_data (
    poi_id       bigint primary key references pois(id) on delete cascade,
    id_empresa   uuid not null,
    osm_id       text,
    osm_tipo     text,
    cluster_id   text,
    amenity      text,
    shop         text,
    marca        text,
    tags         jsonb,
    visto_em     timestamptz default now()
);
create index if not exists osm_data_osm_id on osm_data (id_empresa, osm_id);

-- OVERTURE. É agregado: `fontes` diz quais bases contribuíram para aquele
-- ponto, e `confianca` é a régua própria dele — as duas coisas se perdiam
-- quando tudo virava `fonte='estadual'`.
create table if not exists overture_data (
    poi_id              bigint primary key references pois(id) on delete cascade,
    id_empresa          uuid not null,
    overture_id         text,
    cluster_id          text,
    categoria_principal text,
    categorias          jsonb,
    confianca           real,
    fontes              jsonb,
    visto_em            timestamptz default now()
);
create index if not exists overture_data_id on overture_data (id_empresa, overture_id);

-- FOURSQUARE.
create table if not exists foursquare_data (
    poi_id              bigint primary key references pois(id) on delete cascade,
    id_empresa          uuid not null,
    fsq_id              text,
    cluster_id          text,
    categoria_principal text,
    categorias          jsonb,
    popularidade        real,
    tags                jsonb,
    visto_em            timestamptz default now()
);
create index if not exists foursquare_data_id on foursquare_data (id_empresa, fsq_id);

-- RECEITA FEDERAL. Base pública É fonte de descoberta — foi ela que trouxe
-- 58.834 dos POIs. `cnpj_tratado` já guarda o resultado do tratamento; esta
-- guarda o que a fonte disse sobre a empresa, que hoje está em `pois`.
create table if not exists receita_data (
    poi_id             bigint primary key references pois(id) on delete cascade,
    id_empresa         uuid not null,
    cnpj               text,
    cnpj_conf          text,
    razao_social       text,
    nome_fantasia      text,
    cnae               text,
    natureza_juridica  text,
    situacao_cadastral text,
    socios             jsonb,
    bruto              jsonb
);
create index if not exists receita_data_cnpj on receita_data (id_empresa, cnpj);

-- ── 2. os tiles: presos à RUN, não ao POI ──────────────────────────────────
--
-- Um tile enquadra um quarteirão inteiro e serve a dezenas de POIs e ligações.
-- Pendurá-lo num POI obrigaria a escolher um dono arbitrário e a recapturar a
-- mesma imagem para o vizinho. Ele se prende à `sessao` — a run que o pediu — e
-- à sua caixa geográfica, que é o que permite perguntar "que tile cobre este
-- ponto?", a primeira pergunta do passo dos telhados.
--
-- OS BYTES NÃO ENTRAM AQUI. Um tile de satélite é imagem grande, e são milhares
-- por cidade; a tabela guarda o CAMINHO e o suficiente para reencontrar e
-- reaproveitar. `images_urls` guarda `dados` porque foto de fachada é pequena e
-- some da origem; tile de satélite não some e não cabe.
create table if not exists tile_captura (
    id           bigserial primary key,
    id_empresa   uuid not null,
    sessao       text not null,
    tipo         text not null default 'satelite',
    lat          double precision not null,
    lng          double precision not null,
    zoom         integer,
    largura_px   integer,
    altura_px    integer,
    lat_min      double precision,
    lat_max      double precision,
    lng_min      double precision,
    lng_max      double precision,
    storage_path text,
    url          text,
    bytes_tam    bigint,
    capturado_em timestamptz default now()
);
create index if not exists tile_captura_sessao on tile_captura (id_empresa, sessao);
create index if not exists tile_captura_caixa
    on tile_captura (id_empresa, lat_min, lat_max, lng_min, lng_max);

-- ── 3. as transversais ganham a fonte ──────────────────────────────────────
--
-- O default 'maps' NÃO é um palpite: as 1.974 avaliações e as 2.325 imagens que
-- existem hoje vieram todas do detalhamento do Maps. Ele fica só até os
-- escritores passarem a declarar a fonte; então uma migração o remove, para que
-- um escritor novo que esqueça falhe alto em vez de mentir baixo.
alter table comentarios  add column if not exists fonte text not null default 'maps';
alter table images_urls  add column if not exists fonte text not null default 'maps';
create index if not exists comentarios_fonte on comentarios (id_empresa, fonte);
create index if not exists images_urls_fonte on images_urls (id_empresa, fonte);

-- ── 4. o telhado marca suspeita na própria ligacao_poi ─────────────────────
--
-- Decidido pelo dono do produto: um lugar só para consultar o vínculo. `origem`
-- separa o que casou por critério (endereço, número, distância) da SUSPEITA
-- levantada pelo passo dos telhados — que é o último processo, alcança só o POI
-- que ficou órfão, e é o menor nível de confiança do sistema. Sem essa coluna,
-- um vínculo de telhado entraria na mesma lista dos outros e o operador não
-- teria como saber que está olhando um palpite.
alter table ligacao_poi
    add column if not exists origem          text not null default 'criterio',
    add column if not exists tile_id         bigint references tile_captura(id) on delete set null,
    add column if not exists suspeita_motivo text;
create index if not exists ligacao_poi_origem on ligacao_poi (id_empresa, origem);

comment on column ligacao_poi.origem is
    'criterio = casou por endereço/número/distância. telhado = suspeita '
    'levantada pelo passo dos telhados sobre POI órfão; confiança mais baixa '
    'do sistema, precisa de confirmação humana.';

-- ── 5. dono, permissão e RLS, no padrão da casa ────────────────────────────
do $$
declare t text;
begin
    foreach t in array array['maps_data','osm_data','overture_data',
                             'foursquare_data','receita_data','tile_captura']
    loop
        execute format('alter table %I owner to migrator', t);
        execute format('grant select, insert, update, delete on %I to app_user', t);
        execute format('grant select on %I to readonly', t);
        execute format('alter table %I enable row level security', t);
        execute format('drop policy if exists %I on %I', t || '_por_empresa', t);
        execute format(
            'create policy %I on %I '
            'using (core.eh_suporte() or (id_empresa = core.empresa_atual())) '
            'with check (core.eh_suporte() or (id_empresa = core.empresa_atual()))',
            t || '_por_empresa', t);
        execute format('drop trigger if exists %I on %I', t || '_empresa', t);
        execute format(
            'create trigger %I before insert on %I '
            'for each row execute function radar_comercial.preencher_empresa()',
            t || '_empresa', t);
    end loop;
end $$;

grant usage, select on sequence tile_captura_id_seq to app_user;

comment on table maps_data       is 'O que só o Google Maps sabe sobre o POI.';
comment on table osm_data        is 'O que só o OpenStreetMap sabe sobre o POI.';
comment on table overture_data   is 'O que só o Overture Maps sabe sobre o POI.';
comment on table foursquare_data is 'O que só o Foursquare sabe sobre o POI.';
comment on table receita_data    is 'O que a Receita Federal diz da empresa do POI.';
comment on table tile_captura    is
    'Tile capturado por uma run. Preso à sessão e à caixa geográfica, não a um '
    'POI: um tile serve a dezenas de pontos. Guarda caminho, não bytes.';
