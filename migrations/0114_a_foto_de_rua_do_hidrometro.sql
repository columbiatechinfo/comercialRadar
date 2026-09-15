-- 0114 · a foto de rua de frente tirada no HIDROMETRO da ligacao (15/09/2026).
--
-- Decisao do dono do produto: a ligacao sem nenhum registro a ate 60 m do hidrometro
-- ia para a IA sem imagem (85 das 600 do lote 1). Agora a foto de rua de frente e tirada
-- na coordenada do hidrometro, com a seta nele, antes do julgamento. A foto e da
-- LIGACAO, e nao de um POI — por isso nao cabe em `poi_evidencia`.
-- Quem escreve: `recapturar_frente.py`. Quem le: `avaliar_enxuto.montar_leve`.
create table if not exists radar_comercial.ligacao_evidencia (
    id              bigserial   primary key,
    id_empresa      uuid        not null,
    ligacao         text        not null,
    tipo            text        not null,          -- sv_frente
    dados           bytea,
    storage_path    text,
    pano_id         text,
    cam_lat         double precision,
    cam_lng         double precision,
    heading         double precision,
    fov             double precision,
    distancia_m     double precision,
    largura_px      integer,
    altura_px       integer,
    data_imagem     text,
    mira_x          real,
    capturado_em    timestamptz not null default now()
);
create unique index if not exists ligacao_evidencia_por_ligacao
    on radar_comercial.ligacao_evidencia (id_empresa, ligacao, tipo);
alter table radar_comercial.ligacao_evidencia enable row level security;
drop policy if exists ligacao_evidencia_ver on radar_comercial.ligacao_evidencia;
create policy ligacao_evidencia_ver on radar_comercial.ligacao_evidencia for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists ligacao_evidencia_gravar on radar_comercial.ligacao_evidencia;
create policy ligacao_evidencia_gravar on radar_comercial.ligacao_evidencia for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
drop policy if exists ligacao_evidencia_atualizar on radar_comercial.ligacao_evidencia;
create policy ligacao_evidencia_atualizar on radar_comercial.ligacao_evidencia for update
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
grant select on radar_comercial.ligacao_evidencia to authenticated, readonly;
grant select, insert, update on radar_comercial.ligacao_evidencia to app_user;
grant usage on sequence radar_comercial.ligacao_evidencia_id_seq to app_user;
