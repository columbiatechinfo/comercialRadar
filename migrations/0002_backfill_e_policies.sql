-- =====================================================================
-- 0002 — dono para o acervo, NOT NULL, e as policies
--
-- Três coisas que precisam acontecer nesta ordem, e a ordem não é gosto:
-- backfill antes de NOT NULL (senão a restrição falha em 322 mil linhas), e
-- NOT NULL antes das policies (senão linha órfã fica invisível para todos e
-- parece perda de dado).
-- =====================================================================

set local search_path = comercialradar, public;

-- ── 1 · A primeira empresa ────────────────────────────────────────────
-- O acervo existente (26,5 mil POIs em Canoas/RS, 102 mil linhas de cadastro)
-- foi levantado antes de existir o conceito de empresa cliente. Ele precisa de
-- um dono para não ficar invisível quando a RLS ligar.
insert into tenants (nome, documento)
select 'Empresa inicial — RENOMEAR', null
where not exists (select 1 from tenants);

-- ── 2 · Backfill ──────────────────────────────────────────────────────
do $$
declare
  t   text;
  tid uuid;
begin
  select id into tid from tenants order by criado_em limit 1;
  foreach t in array array[
    'pois', 'cadastro_cliente', 'area_trabalho', 'fachada_anotacao',
    'analise_ia', 'fonte_arquivos', 'comentarios', 'horario_funcionamento',
    'images_urls', 'streetview_imgs', 'cnefe_coletiva'
  ] loop
    execute format('update %I set tenant_id = $1 where tenant_id is null', t) using tid;
  end loop;
end $$;

-- ── 3 · Preencher sozinho, a partir da sessão ─────────────────────────
-- Sem isto, ligar NOT NULL quebraria o pipeline: `realtime_ingest` insere POI
-- sem saber de empresa nenhuma. A trigger tira o valor da MESMA variável de
-- sessão que as policies leem, então gravar e enxergar passam a concordar por
-- construção — não por disciplina de quem escreve INSERT.
create or replace function preencher_tenant() returns trigger
language plpgsql as $$
begin
  if new.tenant_id is null then
    new.tenant_id := nullif(current_setting('app.tenant_id', true), '')::uuid;
  end if;
  return new;
end $$;

comment on function preencher_tenant() is
  'Preenche tenant_id a partir de app.tenant_id. Variável ausente deixa NULL e o '
  'NOT NULL recusa — falha barulhenta é melhor que linha órfã e invisível.';

do $$
declare t text;
begin
  foreach t in array array[
    'pois', 'cadastro_cliente', 'area_trabalho', 'fachada_anotacao',
    'analise_ia', 'fonte_arquivos', 'comentarios', 'horario_funcionamento',
    'images_urls', 'streetview_imgs', 'cnefe_coletiva'
  ] loop
    execute format('drop trigger if exists %I on %I', 'trg_tenant_' || t, t);
    execute format(
      'create trigger %I before insert on %I for each row execute function preencher_tenant()',
      'trg_tenant_' || t, t);
    execute format('alter table %I alter column tenant_id set not null', t);
  end loop;
end $$;

-- ── 4 · Ligar a RLS onde faltava ──────────────────────────────────────
-- `images_urls` e `streetview_imgs` estavam com RLS DESLIGADO — 40 mil fotos e
-- 32 mil fachadas sem proteção de linha, justamente o dado mais caro de produzir.
do $$
declare t text;
begin
  foreach t in array array[
    'pois', 'cadastro_cliente', 'area_trabalho', 'fachada_anotacao',
    'analise_ia', 'fonte_arquivos', 'comentarios', 'horario_funcionamento',
    'images_urls', 'streetview_imgs', 'cnefe_coletiva', 'usuarios', 'tenants'
  ] loop
    execute format('alter table %I enable row level security', t);
    -- FORCE alcança também o DONO da tabela. Sem ele, `comercialradar_worker`
    -- ignoraria toda policy só por ser proprietário.
    execute format('alter table %I force  row level security', t);
  end loop;
end $$;

-- ── 5 · As policies ───────────────────────────────────────────────────
-- A identidade vem de variável de sessão declarada pela API, não de auth.jwt():
-- o navegador nunca fala com o Postgres neste projeto, quem fala é o FastAPI.
--
-- `current_setting(..., true)` devolve NULL quando a variável não existe, e NULL
-- não casa com nada — esquecer de declarar resulta em ZERO linhas, jamais em
-- vazamento. É o mesmo motivo de nunca escrever `or current_setting(...) is null`.
--
-- O subselect não é estilo: `(select current_setting(...))` é avaliado UMA vez,
-- como InitPlan. Solto, seria chamado POR LINHA — em `cadastro_cliente` são 102
-- mil chamadas por consulta.
do $$
declare t text;
begin
  foreach t in array array[
    'pois', 'cadastro_cliente', 'area_trabalho', 'fachada_anotacao',
    'analise_ia', 'fonte_arquivos', 'comentarios', 'horario_funcionamento',
    'images_urls', 'streetview_imgs', 'cnefe_coletiva'
  ] loop
    execute format('drop policy if exists tenant_isolado on %I', t);
    execute format($f$
      create policy tenant_isolado on %I
        using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
        with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
    $f$, t);
  end loop;
end $$;

-- `usuarios`: cada um enxerga os da própria empresa. Root passa por BYPASSRLS.
drop policy if exists usuarios_da_empresa on usuarios;
create policy usuarios_da_empresa on usuarios
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- `tenants`: a empresa vê a si mesma, e mais nenhuma.
drop policy if exists tenant_proprio on tenants;
create policy tenant_proprio on tenants
  using (id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- ── 6 · Grants do papel de aplicação ──────────────────────────────────
-- `images_urls` e `streetview_imgs` não tinham grant algum para o app: a API não
-- conseguia servir foto nem fachada por esse papel.
grant usage on schema comercialradar to comercialradar_app;
grant select, insert, update, delete on all tables in schema comercialradar to comercialradar_app;
grant usage, select on all sequences in schema comercialradar to comercialradar_app;
alter default privileges in schema comercialradar
  grant select, insert, update, delete on tables to comercialradar_app;
