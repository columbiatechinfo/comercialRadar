-- 0118 · a VALIDAÇÃO de uma área ou cidade, por tarefas no banco (dono do produto, 16/09/2026).
--
-- POR QUE EXISTE. Até aqui, tudo o que vem depois do vínculo — recoleta das fichas do Maps, foto de rua nova,
-- leitura das placas, ficha do CNPJ, busca web, conferência e o julgamento leve — rodava só por cron no i9, com
-- Canoas fixo e o estado em arquivos (`.evidencias`, `.feito`). Uma cidade nova não rodava pela tela, e o
-- notebook ficava ocioso.
--
-- O DESENHO. Uma `validacao` é a execução para uma cidade (ou uma área desenhada). As ligações dela são divididas
-- em `validacao_lote`s, e cada lote tem uma `validacao_tarefa` por etapa, em ordem. Uma etapa só entra na fila
-- quando a anterior do mesmo lote terminou; lotes diferentes andam em paralelo, em máquinas diferentes.
--   · SEM IA (fichas, storage, foto de rua, Serasa, busca web, conferência): o i9 e o notebook pegam.
--   · COM IA (leitura das placas, julgamento): chamam a Spark, e cada tarefa declara quanto dela ocupa
--     (`simultaneas`); o worker só pega se a soma do que já roda couber no teto global.
--
-- AMBIENTE EM CADA LINHA. Dev e produção usam o mesmo banco; cada worker só pega as tarefas do seu ambiente.
-- A fila antiga `radar_comercial.job` não é usada de propósito: o worker de produção pega QUALQUER tipo dela.
--
-- Quem escreve: `validacao.py` (cria, avança) e `minerador_worker.py` (pega, roda, encerra).
-- Quem lê: a tela de extração (andamento) e o próprio worker.
create table if not exists radar_comercial.validacao (
    id            bigint      generated always as identity primary key,
    id_empresa    uuid        not null references core.tb_empresas(id),
    pedido_por    uuid,
    ambiente      text        not null check (ambiente in ('producao', 'desenvolvimento')),
    cidade        text        not null,
    area          text,
    ligacoes      integer     not null default 0,
    estado        text        not null default 'preparando'
                  check (estado in ('preparando', 'rodando', 'pausada', 'ok', 'erro', 'cancelada')),
    parametros    jsonb       not null default '{}'::jsonb,
    progresso     jsonb       not null default '{}'::jsonb,
    erro          text,
    criado_em     timestamptz not null default now(),
    terminado_em  timestamptz
);
create index if not exists ix_validacao_empresa on radar_comercial.validacao (id_empresa, criado_em desc);

create table if not exists radar_comercial.validacao_lote (
    id            bigint      generated always as identity primary key,
    id_empresa    uuid        not null,
    id_validacao  bigint      not null references radar_comercial.validacao(id) on delete cascade,
    n             integer     not null,
    ligacoes      text[]      not null,
    unique (id_validacao, n)
);

create table if not exists radar_comercial.validacao_tarefa (
    id            bigint      generated always as identity primary key,
    id_empresa    uuid        not null,
    id_validacao  bigint      not null references radar_comercial.validacao(id) on delete cascade,
    id_lote       bigint      not null references radar_comercial.validacao_lote(id) on delete cascade,
    ambiente      text        not null check (ambiente in ('producao', 'desenvolvimento')),
    etapa         text        not null
                  check (etapa in ('fichas', 'storage', 'frente', 'leitura', 'cnpj', 'busca', 'conferencia', 'julgamento')),
    ordem         smallint    not null,
    precisa_ia    boolean     not null default false,
    simultaneas   integer     not null default 1,
    estado        text        not null default 'espera'
                  check (estado in ('espera', 'fila', 'rodando', 'ok', 'erro', 'cancelada')),
    worker        text,
    visto_em      timestamptz,
    iniciado_em   timestamptz,
    terminado_em  timestamptz,
    tentativas    smallint    not null default 0,
    codigo_saida  integer,
    resumo        text,
    erro          text,
    unique (id_lote, etapa)
);
-- a fila: só o que está pronto para rodar, por ambiente, na ordem das etapas
create index if not exists ix_validacao_tarefa_fila
    on radar_comercial.validacao_tarefa (ambiente, precisa_ia, ordem, id) where estado = 'fila';
create index if not exists ix_validacao_tarefa_rodando
    on radar_comercial.validacao_tarefa (precisa_ia) where estado = 'rodando';
create index if not exists ix_validacao_tarefa_validacao
    on radar_comercial.validacao_tarefa (id_empresa, id_validacao, estado);

alter table radar_comercial.validacao enable row level security;
alter table radar_comercial.validacao_lote enable row level security;
alter table radar_comercial.validacao_tarefa enable row level security;
do $$
declare t text;
begin
  foreach t in array array['validacao', 'validacao_lote', 'validacao_tarefa'] loop
    execute format('drop policy if exists %1$s_acesso on radar_comercial.%1$s', t);
    execute format('create policy %1$s_acesso on radar_comercial.%1$s
                      using ((select core.eh_suporte()) or id_empresa = (select core.empresa_atual()))
                      with check ((select core.eh_suporte()) or id_empresa = (select core.empresa_atual()))', t);
    execute format('grant select on radar_comercial.%s to authenticated, readonly', t);
    execute format('grant select, insert, update on radar_comercial.%s to app_user', t);
  end loop;
end $$;
