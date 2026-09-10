-- 0086 · a presenca em rede social, com DATA — que e o que a torna prova.
--
-- POR QUE UMA TABELA E NAO COLUNAS EM `pois`. O perfil e do NEGOCIO, e o mesmo
-- negocio existe varias vezes na base — a Madeireira Maravilha aparece seis.
-- Guardar a coleta em `pois` faria a mesma visita ser refeita seis vezes e
-- guardada seis vezes, e as copias divergiriam assim que uma falhasse.
--
-- A CHAVE E O PERFIL, nao o POI: `arroba` normalizado. Quem tem o mesmo
-- Instagram compartilha a mesma leitura, e a coleta acontece UMA vez.
--
-- O QUE ISTO ALIMENTA. O score de vinculo pontua "posts em rede social
-- recentes (<= 6 meses)" — decisao do dono do produto em 09/09/2026. Sem
-- `ultimo_post`, esse criterio nao tem como ser avaliado: hoje o banco guarda
-- 15.627 arrobas de Instagram em Canoas e nenhuma data de publicacao.
--
-- POR QUE `conferido_em` E `erro` EXISTEM. Perfil que nao respondeu e perfil
-- que respondeu vazio sao coisas diferentes, e sem separar as duas a fila
-- refaz para sempre o que ja se sabe que nao tem resposta — foi o defeito que
-- a memoria do projeto chama de "falha marcada nao volta".
create table if not exists radar_comercial.rede_social (
    id            bigserial primary key,
    -- SEM DEFAULT, como todas as outras tabelas do schema: nenhuma delas
    -- declara default para `id_empresa`, e quem insere o preenche. Postgres
    -- tambem nao aceitaria `(select ...)` num DEFAULT, mas a razao de seguir
    -- a convencao e outra — um default calculado esconderia de quem le o
    -- insert qual empresa esta sendo gravada.
    id_empresa    uuid not null,
    rede          text not null default 'instagram',
    arroba        text not null,
    perfil_url    text,
    -- O QUE A COLETA TROUXE
    ultimo_post   timestamptz,
    posts         integer,
    seguidores    integer,
    e_comercial   boolean,      -- o proprio Instagram marca conta profissional
    categoria     text,         -- e a categoria que o dono declarou
    nome_exibido  text,
    bio           text,
    site          text,
    -- O RASTRO DA COLETA
    conferido_em  timestamptz,
    erro          text,
    tentativas    smallint not null default 0,
    criado_em     timestamptz not null default now(),
    constraint uq_rede_social unique (id_empresa, rede, arroba)
);

comment on table radar_comercial.rede_social is
    'Presenca em rede social por PERFIL, nao por POI: o mesmo negocio aparece '
    'varias vezes na base e a coleta acontece uma vez so.';
comment on column radar_comercial.rede_social.ultimo_post is
    'Data da publicacao mais recente. E o que o score pontua — perfil sem data '
    'e presenca, nao prova.';

create index if not exists ix_rede_social_fila
    on radar_comercial.rede_social (id_empresa, conferido_em nulls first);

-- RLS como todo o resto, e com subselect: a migracao 0081 mediu que funcao nua
-- em policy custa uma chamada POR LINHA.
alter table radar_comercial.rede_social enable row level security;

create policy p_rede_social_le on radar_comercial.rede_social for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));

create policy p_rede_social_escreve on radar_comercial.rede_social for all
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));

grant select, insert, update on radar_comercial.rede_social to app_user;
grant usage on sequence radar_comercial.rede_social_id_seq to app_user;
