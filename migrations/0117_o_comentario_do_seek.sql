-- 0117 · o COMENTÁRIO do SEEK, com edição que não apaga (dono do produto, 16/09/2026).
--
-- "O comentário tem de ser visível seja qual for o status, e editável por quem escreveu — mas mantendo o
-- primeiro registro e o status que estava, apenas adicionando outro novo."
--
-- APPEND-ONLY, como `seek_decisao`: editar é inserir uma linha nova que aponta para a que ela revisa
-- (`edita_decisao` ou `edita_comentario`). Nada é atualizado nem apagado — por isso não há policy de UPDATE
-- nem de DELETE. A linha do tempo da ligação é a união das decisões e dos comentários, por `em`.
-- Quem escreve: `seek_api.seek_comentar`. Quem lê: `seek_api.seek_caso` (a ficha).
create table if not exists radar_comercial.seek_comentario (
    id                bigserial   primary key,
    id_empresa        uuid        not null,
    ligacao           text        not null,
    texto             text        not null check (length(btrim(texto)) > 0),
    edita_decisao     bigint      references radar_comercial.seek_decisao(id),
    edita_comentario  bigint      references radar_comercial.seek_comentario(id),
    quem              uuid,
    quem_nome         text,
    em                timestamptz not null default now(),
    constraint seek_comentario_edita_um_so check (edita_decisao is null or edita_comentario is null)
);
create index if not exists seek_comentario_por_ligacao
    on radar_comercial.seek_comentario (id_empresa, ligacao, em);
alter table radar_comercial.seek_comentario enable row level security;
drop policy if exists seek_comentario_ver on radar_comercial.seek_comentario;
create policy seek_comentario_ver on radar_comercial.seek_comentario for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_comentario_gravar on radar_comercial.seek_comentario;
create policy seek_comentario_gravar on radar_comercial.seek_comentario for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
grant select, insert on radar_comercial.seek_comentario to authenticated;
grant select, insert on radar_comercial.seek_comentario to app_user;
grant usage on sequence radar_comercial.seek_comentario_id_seq to authenticated;
grant usage on sequence radar_comercial.seek_comentario_id_seq to app_user;
