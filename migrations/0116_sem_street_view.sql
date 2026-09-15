-- 0116 · o alvo da foto de rua SEM PANORAMA DO STREET VIEW por perto (15/09/2026).
--
-- Decisao do dono do produto: a ligacao cujo alvo da foto de rua (o pin do Maps do registro ou o hidrometro)
-- nao tem panorama a ate 12 m do pe da perpendicular nem de frente a ate 30 m vai DIRETO PARA REVISAO HUMANA,
-- sem a IA, com prioridade baixa. No R_003 foram 45 ligacoes (8%), 28 delas no condominio da Rua Brigadeiro Ivo
-- Borges, com o panorama mais perto a 60-130 m. Tabela a parte, e nao linha sem imagem em `poi_evidencia` ou
-- `ligacao_evidencia`: quem le essas tabelas conta a linha como foto.
-- Quem escreve: `recapturar_frente.py` (grava no erro "sem panorama", apaga quando a captura da certo).
-- Quem le: `avaliar_enxuto.uma` (revisao direta) e `conferir_evidencias.py` (nao conta como foto faltando).
create table if not exists radar_comercial.sem_street_view (
    alvo            text        primary key,        -- 'poi:<id>' ou 'ligacao:<numero>'
    id_empresa      uuid,
    lat             double precision,
    lng             double precision,
    motivo          text        not null,
    verificado_em   timestamptz not null default now()
);
alter table radar_comercial.sem_street_view enable row level security;
drop policy if exists sem_street_view_ver on radar_comercial.sem_street_view;
create policy sem_street_view_ver on radar_comercial.sem_street_view for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists sem_street_view_gravar on radar_comercial.sem_street_view;
create policy sem_street_view_gravar on radar_comercial.sem_street_view for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
drop policy if exists sem_street_view_atualizar on radar_comercial.sem_street_view;
create policy sem_street_view_atualizar on radar_comercial.sem_street_view for update
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists sem_street_view_apagar on radar_comercial.sem_street_view;
create policy sem_street_view_apagar on radar_comercial.sem_street_view for delete
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
grant select on radar_comercial.sem_street_view to authenticated, readonly;
grant select, insert, update, delete on radar_comercial.sem_street_view to app_user;
