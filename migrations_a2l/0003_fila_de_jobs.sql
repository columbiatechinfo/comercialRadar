-- 0003_fila_de_jobs.sql — o trabalho pesado deixa de ser um `Popen`.
--
-- POR QUE ISTO EXISTE
--
-- Até 31/08/2026 a API disparava a mineração como subprocesso e acompanhava
-- pelo cano do stdout, com o estado do job vivendo num dicionário na memória do
-- servidor. Funcionava enquanto API e mineração eram o mesmo processo na mesma
-- máquina.
--
-- Elas deixaram de ser: a API sobe `read_only` como o resto da stack, e dez
-- Chromium não cabem num contêiner somente-leitura. São dois contêineres agora,
-- e um `Popen` não atravessa essa fronteira.
--
-- O QUE MUDA DE VERDADE, além do lugar:
--
--   · o estado do job SOBREVIVE ao restart da API. Antes, reiniciar o servidor
--     no meio de uma rodada apagava o que se sabia dela — a mineração seguia
--     órfã e o painel dizia "ocioso" com dez navegadores abertos;
--   · o log fica. Ele já ia para arquivo desde 14/08/2026, justamente porque
--     morria com a aba; agora fica no banco, ao lado do job, e quem abre o
--     painel no meio da rodada vê o que passou em vez de só o que vem;
--   · a fila é fila. Dois pedidos ao mesmo tempo enfileiram em vez de disputar.
--
-- Rodar como `migrator`.

set local search_path = radar_comercial, public;


-- ─────────────────────────────────────────────────────────────────────
-- O job
-- ─────────────────────────────────────────────────────────────────────

create table if not exists job (
  id                bigint generated always as identity primary key,
  id_empresa        uuid not null references core.tb_empresas(id),

  -- QUEM PEDIU. Não é enfeite de auditoria: é daqui que o minerador tira a
  -- identidade para gravar. Ele declara este uuid em `request.jwt.claim.sub`, e
  -- então `core.empresa_atual()` responde a empresa certa sem que o worker
  -- precise saber de empresa nenhuma.
  pedido_por        uuid not null,

  tipo              text not null check (tipo in ('mineracao', 'planilha')),
  argumentos        jsonb not null default '{}'::jsonb,

  -- `fila` -> `rodando` -> (`ok` | `erro` | `cancelado`)
  --
  -- `cancelado` é estado de destino e não de origem: quem cancela marca o
  -- pedido, e o worker é quem confirma tendo parado. Sem essa distinção, o
  -- painel diria "cancelado" com a captura ainda rodando.
  estado            text not null default 'fila'
                    check (estado in ('fila','rodando','ok','erro','cancelado')),
  cancelar_pedido   boolean not null default false,

  -- Quem pegou o job, e quando deu o último sinal de vida. O worker atualiza o
  -- `visto_em` a cada passo; job `rodando` com `visto_em` velho é worker morto,
  -- e é assim que se distingue "demorando" de "travado" — que a versão anterior
  -- não distinguia, e por isso uma produção dada como viva estava morta havia
  -- meia hora.
  worker            text,
  visto_em          timestamptz,

  progresso         jsonb not null default '{}'::jsonb,
  codigo_saida      integer,
  erro              text,

  criado_em         timestamptz not null default now(),
  iniciado_em       timestamptz,
  terminado_em      timestamptz
);

comment on table job is
  'Fila de trabalho pesado. A API enfileira, o minerador consome. O estado vive '
  'aqui porque os dois sao conteineres separados e sobrevivem ao restart um do '
  'outro.';


-- ─────────────────────────────────────────────────────────────────────
-- O log do job
-- ─────────────────────────────────────────────────────────────────────
--
-- Tabela separada, e não uma coluna `text` que cresce: são milhares de linhas
-- por rodada, e um `update` que concatena reescreve a linha inteira a cada
-- linha nova — o custo cresce com o quadrado do tamanho.

create table if not exists job_log (
  id       bigint generated always as identity primary key,
  id_job   bigint not null references job(id) on delete cascade,
  em       timestamptz not null default now(),
  linha    text not null
);

comment on table job_log is
  'Uma linha por linha de log. Separada de `job` porque sao milhares por rodada '
  'e concatenar num campo texto custa o quadrado do tamanho.';


-- ─────────────────────────────────────────────────────────────────────
-- Isolamento
-- ─────────────────────────────────────────────────────────────────────

alter table job enable row level security;
alter table job force  row level security;
create policy p_job on job for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

create trigger trg_empresa_job before insert on job
  for each row execute function preencher_empresa();

-- `job_log` NÃO TEM `id_empresa`, e isso é decisão, não esquecimento.
--
-- Ela é filha de `job` por chave estrangeira, e a política pergunta ao pai.
-- Repetir a coluna aqui criaria duas fontes para a mesma verdade — e o dia em
-- que as duas divergissem, o log de uma empresa apareceria para outra sem que
-- nada acusasse. O custo é um subselect por linha; o índice abaixo o cobre.
alter table job_log enable row level security;
alter table job_log force  row level security;
create policy p_job_log on job_log for all
  using (exists (select 1 from job j where j.id = job_log.id_job))
  with check (exists (select 1 from job j where j.id = job_log.id_job));


-- ─────────────────────────────────────────────────────────────────────
-- Índices
-- ─────────────────────────────────────────────────────────────────────

-- A CONSULTA DO WORKER: o próximo da fila, mais antigo primeiro.
create index if not exists ix_job_fila on job (estado, criado_em)
  where estado = 'fila';

-- A CONSULTA DO PAINEL: o job corrente daquela empresa.
create index if not exists ix_job_empresa on job (id_empresa, criado_em desc);

-- A CONSULTA DO LOG AO VIVO: as linhas novas de um job, em ordem.
create index if not exists ix_job_log_id on job_log (id_job, id);


-- ─────────────────────────────────────────────────────────────────────
-- Permissões
-- ─────────────────────────────────────────────────────────────────────

grant select, insert, update, delete on job, job_log to app_user;
grant usage, select on all sequences in schema radar_comercial to app_user;
