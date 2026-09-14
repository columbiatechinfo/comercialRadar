-- 0110 · o chamado que a SEEK abriu no Hippo para uma ligacao (14/09/2026).
--
-- Decisao do dono do produto: na ficha da ligacao ha o botao "Abrir chamado para
-- esta ligacao". Quem clica escolhe a empresa que vai atender e escreve o texto; o
-- chamado nasce no Hippo (a2l_gcp) pela rota de integracao, com resumo, link e as
-- fotos do veredito. Ver `seek_chamado.py` e, no Hippo, a ADR 0005 e a 0021.
--
-- ESTA TABELA E O ESPELHO DO LADO DE CA, nao a fonte: a situacao do chamado vive
-- no Hippo e e perguntada a ele quando a ficha lista os abertos. Aqui fica o que a
-- SEEK precisa sem ir ao Hippo — qual chamado, de que ligacao, para quem, por quem.
--
-- UMA LINHA POR CHAMADO: clique duplo devolve o mesmo chamado do Hippo, e o
-- `on conflict (id_empresa, chamado_id) do nothing` do codigo nao duplica.
--
-- A EMPRESA E A DA LIGACAO, e nao a de quem clicou — o mesmo criterio de
-- `seek_decisao` (0102): o suporte (root) age por qualquer empresa.
create table if not exists radar_comercial.seek_chamado (
    id                      bigserial   primary key,
    id_empresa              uuid        not null,
    ligacao                 text        not null,
    chamado_id              uuid        not null,
    numero                  bigint      not null,
    empresa_atendente       uuid        not null,
    empresa_atendente_nome  text,
    responsavel             uuid,
    responsavel_nome        text,
    url                     text,
    quem                    uuid        not null,
    quem_nome               text,
    em                      timestamptz not null default now()
);
create unique index if not exists seek_chamado_por_chamado
    on radar_comercial.seek_chamado (id_empresa, chamado_id);
create index if not exists seek_chamado_por_ligacao
    on radar_comercial.seek_chamado (id_empresa, ligacao, em desc);
alter table radar_comercial.seek_chamado enable row level security;
drop policy if exists seek_chamado_ver on radar_comercial.seek_chamado;
create policy seek_chamado_ver on radar_comercial.seek_chamado for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_chamado_gravar on radar_comercial.seek_chamado;
create policy seek_chamado_gravar on radar_comercial.seek_chamado for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
grant select, insert on radar_comercial.seek_chamado to authenticated;
grant select, insert on radar_comercial.seek_chamado to app_user;
grant usage on sequence radar_comercial.seek_chamado_id_seq to authenticated;
grant usage on sequence radar_comercial.seek_chamado_id_seq to app_user;
