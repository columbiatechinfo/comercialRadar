-- 0107 · o registro de cada exportacao da gestao das aprovacoes (14/09/2026)
--
-- Pedido do dono do produto: a pagina de gestao exporta as decisoes (XLSX e CSV)
-- e CADA exportacao fica registrada — quem, quando, com que filtros, que colunas,
-- quantas linhas, em que formato e de que IP. A planilha leva endereco, titular e
-- decisao para fora do sistema; o registro e o que responde "quem levou isto".
--
-- SO INSERE E LE: nao ha grant de update nem delete. Registro de auditoria que o
-- proprio autor apaga nao e registro.
--
--   id_empresa  a empresa exportada. NULO quando o suporte (root) exporta todas as
--               empresas de uma vez — e so o suporte ve essas linhas.
--   filtros     o que foi pedido: periodo, usuario, status, cidade, vigentes/todas.
--   colunas     as colunas escolhidas, na ordem do arquivo.
--   linhas      quantas linhas sairam no arquivo (sem o cabecalho).
--
-- A LEITURA E DE ADMINISTRADOR PARA CIMA, tambem no banco (hierarquia >= 80): a
-- rota ja exige, e a politica garante o mesmo para quem chegar por outro caminho.
create table if not exists radar_comercial.seek_exportacao (
    id          bigserial   primary key,
    id_empresa  uuid,
    quem        uuid        not null,
    quem_nome   text,
    em          timestamptz not null default now(),
    formato     text        not null check (formato in ('xlsx', 'csv')),
    filtros     jsonb       not null default '{}'::jsonb,
    colunas     text[]      not null,
    linhas      integer     not null default 0,
    ip          text,
    agente      text
);
create index if not exists seek_exportacao_por_periodo
    on radar_comercial.seek_exportacao (id_empresa, em desc);

alter table radar_comercial.seek_exportacao enable row level security;
drop policy if exists seek_exportacao_ver on radar_comercial.seek_exportacao;
create policy seek_exportacao_ver on radar_comercial.seek_exportacao for select
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual())
               and (select core.hierarquia_atual()) >= 80));
drop policy if exists seek_exportacao_gravar on radar_comercial.seek_exportacao;
create policy seek_exportacao_gravar on radar_comercial.seek_exportacao for insert
    with check ((select core.eh_suporte())
                or (id_empresa = (select core.empresa_atual())
                    and (select core.hierarquia_atual()) >= 80
                    and quem = (select auth.uid())));
grant select, insert on radar_comercial.seek_exportacao to authenticated;
grant select, insert on radar_comercial.seek_exportacao to app_user;
grant usage on sequence radar_comercial.seek_exportacao_id_seq to authenticated;
grant usage on sequence radar_comercial.seek_exportacao_id_seq to app_user;
