-- 0102 · a decisao OFICIAL de cada ligacao, tomada por uma pessoa na tela SEEK.
--
-- Decisao do dono do produto em 13/09/2026: cada fonte da o seu veredito
-- (achou ou nao achou), a IA da o dela, e o do USUARIO e o oficial — gravado a
-- parte, sem tocar no da IA (`ligacao_veredito`) nem no das fontes. As acoes
-- sao as do SEEK: aprovar, mandar a campo, deixar em revisao e rejeitar; rejeitar
-- exige o motivo escrito.
--
-- HISTORICO, E NAO ESTADO: cada decisao e uma linha nova, e a vigente e a mais
-- recente da ligacao. Quem mudou de ideia, quando e por que fica registrado.
-- `lote` agrupa as linhas gravadas de uma vez ("aplicar a todos os filtrados").
create table if not exists radar_comercial.seek_decisao (
    id           bigserial   primary key,
    id_empresa   uuid        not null,
    ligacao      text        not null,
    acao         text        not null check (acao in ('aprovar', 'campo', 'revisar', 'rejeitar')),
    motivo       text,
    observacoes  jsonb,
    lote         uuid,
    quem         uuid,
    quem_nome    text,
    em           timestamptz not null default now(),
    constraint seek_decisao_rejeitar_com_motivo
        check (acao <> 'rejeitar' or length(btrim(coalesce(motivo, ''))) > 0)
);
create index if not exists seek_decisao_por_ligacao
    on radar_comercial.seek_decisao (id_empresa, ligacao, em desc);
alter table radar_comercial.seek_decisao enable row level security;
drop policy if exists seek_decisao_ver on radar_comercial.seek_decisao;
create policy seek_decisao_ver on radar_comercial.seek_decisao for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_decisao_gravar on radar_comercial.seek_decisao;
create policy seek_decisao_gravar on radar_comercial.seek_decisao for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
grant select, insert on radar_comercial.seek_decisao to authenticated;
grant select, insert on radar_comercial.seek_decisao to app_user;
grant usage on sequence radar_comercial.seek_decisao_id_seq to authenticated;
grant usage on sequence radar_comercial.seek_decisao_id_seq to app_user;
