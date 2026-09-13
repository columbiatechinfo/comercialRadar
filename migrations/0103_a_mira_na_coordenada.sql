-- 0103 · a mira volta, na coordenada do POI (13/09/2026)
--
-- Regra do dono do produto: "a mira deve ser colocada na coordenada do POI,
-- simples assim". A mira tinha saido em 10/09/2026; as visadas capturadas antes
-- disso a trazem desenhada no centro, as depois nao trazem nenhuma.
-- `corrigir_frente.py` desenha a mira no pixel em que o rumo camera -> POI cai,
-- sem recapturar, e grava onde ela ficou.
--
--   mira_x     posicao horizontal da mira na imagem (0 = esquerda, 1 = direita);
--              nulo na visada sem mira
--   mira_rumo  o rumo camera -> coordenada do POI, em graus
alter table radar_comercial.poi_evidencia
    add column if not exists mira_x real,
    add column if not exists mira_rumo real;

-- A IMAGEM ANTES DA CORRECAO. Quem tem arquivo no Storage fica com o caminho
-- (o arquivo nao e apagado); quem so tem `dados` guarda os bytes. A primeira
-- copia vence: rodar de novo nao sobrescreve o original.
create table if not exists radar_comercial.poi_evidencia_antes_mira (
    evidencia_id  bigint      primary key,
    id_empresa    uuid,
    poi_id        bigint      not null,
    tipo          text        not null,
    storage_path  text,
    dados         bytea,
    bytes_tam     integer,
    heading       double precision,
    guardado_em   timestamptz not null default now()
);
alter table radar_comercial.poi_evidencia_antes_mira enable row level security;
-- quem corrige e o pipeline (app_user); o migrator e o readonly seguem o que
-- ja tem na poi_evidencia
grant select, insert on radar_comercial.poi_evidencia_antes_mira to app_user;
grant select, insert, update, delete on radar_comercial.poi_evidencia_antes_mira to migrator;
grant select on radar_comercial.poi_evidencia_antes_mira to readonly;
-- A MESMA RLS DA poi_evidencia: o pipeline nao tem BYPASSRLS e grava como
-- suporte ou como a empresa dona do POI (nivel 4+). Sem a politica, a RLS
-- ligada negava todo insert — foi o primeiro erro da correcao.
drop policy if exists poi_evidencia_antes_mira_ver on radar_comercial.poi_evidencia_antes_mira;
create policy poi_evidencia_antes_mira_ver on radar_comercial.poi_evidencia_antes_mira for select
    using ((select core.eh_suporte()) or id_empresa = (select core.empresa_atual()));
drop policy if exists poi_evidencia_antes_mira_mexer on radar_comercial.poi_evidencia_antes_mira;
create policy poi_evidencia_antes_mira_mexer on radar_comercial.poi_evidencia_antes_mira for all
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual()) and (select core.nivel_atual()) >= 4))
    with check ((select core.eh_suporte())
                or (id_empresa = (select core.empresa_atual()) and (select core.nivel_atual()) >= 4));
