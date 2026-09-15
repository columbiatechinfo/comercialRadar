-- 0113 · a ficha do CNPJ consultada na web: Serasa e Casa dos Dados (15/09/2026).
--
-- Decisao do dono do produto: so se consulta a ficha do CNPJ que NAO veio com data
-- e situacao da nossa base da Receita (o CNPJ que o iFood ou o Cadastur trazem, por
-- exemplo). O print fica guardado para a SEEK exibir (`storage_path`); a IA recebe
-- o TEXTO estruturado (`texto_ia`), que custa uma fracao dos tokens da imagem.
-- Quem escreve: `fichas_cnpj.py`. Quem le: `avaliar_enxuto._fichas_cnpj_em_texto`.
--
-- UMA LINHA POR (empresa, cnpj, fonte): reconsultar atualiza a linha.
create table if not exists radar_comercial.ficha_cnpj_web (
    id              bigserial   primary key,
    id_empresa      uuid        not null,
    cnpj            text        not null,
    fonte           text        not null,          -- serasa | casa_dos_dados
    url             text,
    consultado_em   timestamptz not null default now(),
    bloqueado       boolean     not null default false,
    erro            text,
    situacao        text,
    data_abertura   date,
    campos          jsonb,
    texto_ia        text,
    storage_path    text
);
create unique index if not exists ficha_cnpj_web_por_cnpj
    on radar_comercial.ficha_cnpj_web (id_empresa, cnpj, fonte);
alter table radar_comercial.ficha_cnpj_web enable row level security;
drop policy if exists ficha_cnpj_web_ver on radar_comercial.ficha_cnpj_web;
create policy ficha_cnpj_web_ver on radar_comercial.ficha_cnpj_web for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists ficha_cnpj_web_gravar on radar_comercial.ficha_cnpj_web;
create policy ficha_cnpj_web_gravar on radar_comercial.ficha_cnpj_web for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
drop policy if exists ficha_cnpj_web_atualizar on radar_comercial.ficha_cnpj_web;
create policy ficha_cnpj_web_atualizar on radar_comercial.ficha_cnpj_web for update
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
grant select on radar_comercial.ficha_cnpj_web to authenticated, readonly;
grant select, insert, update on radar_comercial.ficha_cnpj_web to app_user;
grant usage on sequence radar_comercial.ficha_cnpj_web_id_seq to app_user;
