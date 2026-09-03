-- 0049 — a carga: o que já estava em `pois` vai para a tabela da sua fonte
--
-- O CENSO QUE DEFINIU CADA LINHA DESTA MIGRAÇÃO (medido em 03/09/2026)
--
--   fonte      POIs     o que de fato está preenchido
--   ────────────────────────────────────────────────────────────────────────
--   estadual  223.510   place_id='estadual:<cluster>', maps_lat/lng
--   receita    58.834   cnpj, e SÓ cnpj
--   ibge       17.055   nada além do geral
--   ifood       1.324   cnpj em 1.317
--   maps          377   place_id do Google, maps_url, plus_code, avaliação,
--                       total_avaliacoes, status_horario, detalhado_em/por
--   cadastur      178   cnpj; razao_social/nome_fantasia em 77; cnae em 74
--   airbnb         19   nada além do geral (o resto já vive em airbnb_anuncio)
--
-- TRÊS COISAS QUE O CENSO DESMENTIU, e que mudaram o que esta migração faz:
--
-- 1. `maps_lat`/`maps_lng` NÃO SÃO DO MAPS nos 223.510 estaduais. Ali elas
--    guardam a coordenada que a fonte estadual entregou. O nome da coluna
--    mentia, e copiar tudo para `maps_data` teria oficializado a mentira —
--    seriam 223.510 linhas afirmando um enriquecimento que nunca houve. Só os
--    377 de `fonte='maps'` viram `maps_data`.
--
-- 2. `razao_social`, `nome_fantasia` e `cnae` NÃO vieram da Receita. Os 58.834
--    POIs da Receita têm apenas CNPJ; quem tem razão social é o CADASTUR, em
--    77 linhas. `receita_data` nasce vazia de propósito — a tabela existe para
--    o enriquecimento que ainda vai rodar, e fingir carga seria pior que vazio.
--
-- 3. O CNPJ FICA EM `pois`. Ele não é opinião de fonte: é a identidade do
--    estabelecimento, e três fontes diferentes chegam ao mesmo número. Mover
--    para a tabela da fonte obrigaria a consultar cinco tabelas para responder
--    "qual o CNPJ deste ponto".
--
-- O QUE ESTA MIGRAÇÃO NÃO RESOLVE, e é dito aqui em vez de escondido: 195.983
-- POIs estaduais continuam sem saber de qual das três fontes vieram, porque
-- `fonte_dado` está nulo neles. `origem_estadual.py` é quem responde isso, lendo
-- o parquet — rodou em Canoas e parou. Enquanto não rodar no estado inteiro,
-- esses 195.983 não entram em `osm_data`, `overture_data` nem `foursquare_data`.
-- O `cluster_id` que permite a atribuição está guardado em `place_id`.
--
-- Roda como superusuário: `pois` tem RLS, e a carga precisa ver todas as linhas.

set search_path to radar_comercial, public;

-- ── CADASTUR ganha a sua tabela ────────────────────────────────────────────
-- O documento do dono do produto corrige uma classificação: Cadastur é base
-- pública como CNEFE e Receita, com a mesma função e utilidade. `cadastur_vinculo`
-- guarda o CRUZAMENTO (quem casou com quem); faltava onde guardar o DADO.
create table if not exists cadastur_data (
    poi_id         bigint primary key references pois(id) on delete cascade,
    id_empresa     uuid not null,
    cadastur_id    text,
    cnpj           text,
    razao_social   text,
    nome_fantasia  text,
    cnae           text,
    atividade      text,
    situacao       text,
    incerteza_m    double precision,
    bruto          jsonb
);
create index if not exists cadastur_data_cnpj on cadastur_data (id_empresa, cnpj);

alter table cadastur_data owner to migrator;
grant select, insert, update, delete on cadastur_data to app_user;
grant select on cadastur_data to readonly;
alter table cadastur_data enable row level security;
drop policy if exists cadastur_data_por_empresa on cadastur_data;
create policy cadastur_data_por_empresa on cadastur_data
    using (core.eh_suporte() or (id_empresa = core.empresa_atual()))
    with check (core.eh_suporte() or (id_empresa = core.empresa_atual()));
drop trigger if exists cadastur_data_empresa on cadastur_data;
create trigger cadastur_data_empresa before insert on cadastur_data
    for each row execute function radar_comercial.preencher_empresa();
comment on table cadastur_data is
    'O que o Cadastur (MTur) diz do POI. Base pública, e fonte de descoberta '
    'como qualquer outra que forneça POIs.';

-- ── a carga ────────────────────────────────────────────────────────────────

-- MAPS: os 377. Note que `maps_lat/lng` entram aqui porque nestes POIs elas
-- SÃO do Maps — foi o Maps que descobriu o ponto.
--
-- A NOTA ERA TEXTO COM VÍRGULA — '4,5', formato brasileiro. Guardada assim ela
-- não se ordena nem se soma: '10,0' vem antes de '2,0' na ordem alfabética, e
-- média era impossível sem converter na consulta. Aqui ela vira `numeric`. O
-- `nullif`+regex descarta o que não for número em vez de derrubar a carga.
insert into maps_data (poi_id, id_empresa, place_id, maps_url, plus_code,
                       maps_lat, maps_lng, avaliacao, total_avaliacoes,
                       resumo_avaliacoes, status_horario, preco_medio,
                       ocr_texto, detalhado_em, detalhado_por)
select p.id, p.id_empresa, p.place_id, p.maps_url, p.plus_code,
       p.maps_lat, p.maps_lng,
       case when replace(p.avaliacao, ',', '.') ~ '^[0-9]+(\.[0-9]+)?$'
            then replace(p.avaliacao, ',', '.')::numeric end,
       p.total_avaliacoes,
       p.resumo_avaliacoes, p.status_horario, p.preco_medio,
       p.ocr_texto, p.detalhado_em, p.detalhado_por
  from pois p
 where p.fonte = 'maps'
on conflict (poi_id) do nothing;

-- OSM: 1.196. O `cluster_id` sai do `place_id`, tirando o prefixo.
insert into osm_data (poi_id, id_empresa, cluster_id)
select p.id, p.id_empresa, substring(p.place_id from 10)
  from pois p
 where p.fonte_dado = 'estadual:osm'
on conflict (poi_id) do nothing;

-- OVERTURE: 14.596.
insert into overture_data (poi_id, id_empresa, cluster_id)
select p.id, p.id_empresa, substring(p.place_id from 10)
  from pois p
 where p.fonte_dado = 'estadual:overture'
on conflict (poi_id) do nothing;

-- FOURSQUARE: 11.735.
insert into foursquare_data (poi_id, id_empresa, cluster_id)
select p.id, p.id_empresa, substring(p.place_id from 10)
  from pois p
 where p.fonte_dado = 'estadual:fsq'
on conflict (poi_id) do nothing;

-- CADASTUR: 178, dos quais 77 com razão social.
insert into cadastur_data (poi_id, id_empresa, cnpj, razao_social,
                           nome_fantasia, cnae, incerteza_m)
select p.id, p.id_empresa, p.cnpj, p.razao_social,
       p.nome_fantasia, p.cnae, p.coord_incerteza_m
  from pois p
 where p.fonte = 'cadastur'
on conflict (poi_id) do nothing;

-- RECEITA: nada a carregar. A tabela nasce vazia porque os 58.834 POIs da
-- Receita só têm CNPJ, que fica em `pois`. Ela existe para o enriquecimento.

-- ── o placar, impresso na própria migração ─────────────────────────────────
do $$
declare
    n_maps int; n_osm int; n_ovt int; n_fsq int; n_cad int; n_orfao int;
begin
    select count(*) into n_maps from maps_data;
    select count(*) into n_osm  from osm_data;
    select count(*) into n_ovt  from overture_data;
    select count(*) into n_fsq  from foursquare_data;
    select count(*) into n_cad  from cadastur_data;
    select count(*) into n_orfao from pois
      where fonte = 'estadual' and fonte_dado is null;
    raise notice 'CARGA: maps=% osm=% overture=% foursquare=% cadastur=%',
        n_maps, n_osm, n_ovt, n_fsq, n_cad;
    raise notice 'AINDA SEM FONTE ATRIBUIDA: % POIs estaduais (rodar origem_estadual.py)',
        n_orfao;
end $$;
