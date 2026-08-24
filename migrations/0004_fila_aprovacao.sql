-- =====================================================================
-- 0004 — fila de aprovação, e o escopo do supervisor
--
-- As duas coisas são a mesma tabela, e é por isso que vêm juntas: o supervisor
-- enxerga "o que lhe foi atribuído", e atribuição É a fila. Modelar em separado
-- criaria duas verdades sobre quem responde por qual ponto.
-- =====================================================================

set local search_path = comercialradar, public;

do $$ begin
  create type decisao_fila as enum ('pendente', 'aprovado', 'reprovado', 'devolvido');
exception when duplicate_object then null; end $$;

-- Motivo genérico é o que VIRA ESTATÍSTICA — quantas reprovações por fachada
-- residencial, quantas por comércio encerrado. O motivo escrito explica o caso
-- que nenhuma lista prevê. Exigir os dois não é burocracia: só o genérico apaga
-- a nuance, só o escrito não se mede.
do $$ begin
  create type motivo_reprova as enum (
    'fachada_residencial', 'endereco_divergente', 'comercio_encerrado',
    'duplicado', 'evidencia_insuficiente', 'ja_e_comercial', 'outro');
exception when duplicate_object then null; end $$;

create table if not exists atribuicao (
  id              bigserial primary key,
  tenant_id       uuid not null references tenants(id),
  poi_id          integer not null references pois(id) on delete cascade,
  supervisor_id   uuid not null references usuarios(id),
  atribuido_por   uuid not null references usuarios(id),
  atribuido_em    timestamptz not null default now(),
  status          decisao_fila not null default 'pendente',
  decidido_em     timestamptz,
  motivo_generico motivo_reprova,
  motivo_escrito  text,
  observacao      text,
  -- Reprovar SEM os dois motivos é recusado pelo banco, não só pela tela. A
  -- regra de negócio que só vive no frontend some no primeiro cliente de API.
  constraint reprova_exige_motivo check (
    status <> 'reprovado'
    or (motivo_generico is not null
        and motivo_escrito is not null and length(btrim(motivo_escrito)) >= 3)),
  -- Devolver exige dizer O QUE impede; senão o ponto volta ao fluxo sem
  -- ninguém saber o que corrigir, e volta de novo igual.
  constraint devolve_exige_observacao check (
    status <> 'devolvido'
    or (observacao is not null and length(btrim(observacao)) >= 3))
);

-- Um POI não é atribuído duas vezes ao mesmo supervisor.
create unique index if not exists atribuicao_unica
  on atribuicao (poi_id, supervisor_id);

-- tenant_id na frente, como em todo índice deste schema: a policy é avaliada
-- por linha, e sem isso o isolamento vira o gargalo.
create index if not exists atribuicao_tenant_idx     on atribuicao (tenant_id, status);
-- Este é o índice que sustenta o escopo do supervisor: a policy de `pois` faz
-- um EXISTS por linha contra ele.
create index if not exists atribuicao_supervisor_idx on atribuicao (supervisor_id, poi_id);

drop trigger if exists trg_tenant_atribuicao on atribuicao;
create trigger trg_tenant_atribuicao before insert on atribuicao
  for each row execute function preencher_tenant();

alter table atribuicao enable row level security;
alter table atribuicao force  row level security;

drop policy if exists atribuicao_escopo on atribuicao;
create policy atribuicao_escopo on atribuicao
  using (
    tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid
    and (
      -- admin e acima veem a fila inteira da empresa; supervisor vê a sua.
      coalesce((select nullif(current_setting('app.nivel', true), '')), 'user') <> 'supervisor'
      or supervisor_id = (select nullif(current_setting('app.usuario_id', true), ''))::uuid
    ))
  with check (
    tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- ── O escopo do supervisor sobre os POIs ──────────────────────────────
-- Até aqui a policy de `pois` era só tenant. O supervisor passava a ver a base
-- inteira da empresa, e "distribuir para supervisores" seria rótulo, não
-- divisão de trabalho.
drop policy if exists tenant_isolado on pois;
create policy tenant_isolado on pois
  using (
    tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid
    and (
      coalesce((select nullif(current_setting('app.nivel', true), '')), 'user') <> 'supervisor'
      or exists (select 1 from atribuicao a
                  where a.poi_id = pois.id
                    and a.supervisor_id =
                        (select nullif(current_setting('app.usuario_id', true), ''))::uuid)
    ))
  with check (
    tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- ── Grants ────────────────────────────────────────────────────────────
-- A tabela nasceu DEPOIS dos grants do 0002, e lá o `alter default privileges`
-- do papel de aplicação cobria apenas TABLES. Resultado: `bigserial` novo
-- criava sequência sem permissão, e o INSERT morria com "permission denied for
-- sequence" — erro que aponta para a sequência e não para a causa, que é o
-- default privilege incompleto. As duas linhas de DEFAULT PRIVILEGES abaixo
-- evitam que a próxima tabela repita isto.
grant select, insert, update, delete on atribuicao to comercialradar_app, comercialradar_root;
grant usage, select on all sequences in schema comercialradar
  to comercialradar_app, comercialradar_root;
alter default privileges in schema comercialradar
  grant usage, select on sequences to comercialradar_app;
alter default privileges in schema comercialradar
  grant usage, select on sequences to comercialradar_root;
