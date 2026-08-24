-- =====================================================================
-- 0001 — tenant, papéis e RLS
--
-- Cria o isolamento por empresa cliente e liga as políticas. Antes disto o
-- banco tinha RLS habilitado em 9 tabelas e ZERO policies: fail-closed, que é a
-- postura certa, mas significava que o papel de aplicação lia zero linha e todo
-- acesso dependia do worker com BYPASSRLS. Ou seja: não havia isolamento, havia
-- ausência de acesso.
--
-- Contexto que decide a forma das policies: o navegador NUNCA fala com o
-- Postgres. Quem fala é o FastAPI, com psycopg2. Por isso a identidade não vem
-- de `auth.jwt()` — que só existe quando o PostgREST repassa o token — e sim de
-- variável de sessão que a própria API declara por transação.
--
-- Idempotente: pode rodar de novo sem estragar o que já existe.
-- =====================================================================

set local search_path = comercialradar, public;

-- ── 1 · Empresa cliente ───────────────────────────────────────────────
create table if not exists tenants (
  id          uuid primary key default gen_random_uuid(),
  nome        text        not null,
  documento   text,                       -- CNPJ da empresa de saneamento
  ativo       boolean     not null default true,
  criado_em   timestamptz not null default now()
);

comment on table tenants is
  'Empresa cliente. Toda linha de negócio pertence a exatamente uma.';

-- ── 2 · Usuário e nível ───────────────────────────────────────────────
-- O usuário nasce no GoTrue (auth.users); aqui fica o que é NOSSO: a empresa a
-- que ele pertence e o nível. Tabela separada de propósito — o schema `auth` é
-- da pilha Supabase e não se mexe nele.
do $$ begin
  create type nivel_acesso as enum ('root', 'admin', 'supervisor', 'user');
exception when duplicate_object then null; end $$;

create table if not exists usuarios (
  id          uuid primary key,           -- = auth.users.id
  tenant_id   uuid        references tenants(id) on delete restrict,
  nivel       nivel_acesso not null default 'user',
  nome        text,
  email       text,
  ativo       boolean     not null default true,
  criado_em   timestamptz not null default now()
);

-- root não pertence a empresa nenhuma: ele atravessa todas. Os demais PRECISAM
-- de empresa — usuário sem tenant enxergaria o vazio ou, pior, o tudo.
alter table usuarios drop constraint if exists usuarios_tenant_por_nivel;
alter table usuarios add  constraint usuarios_tenant_por_nivel check (
  (nivel = 'root'  and tenant_id is null) or
  (nivel <> 'root' and tenant_id is not null)
);

create index if not exists usuarios_tenant_idx on usuarios (tenant_id, nivel);

-- ── 3 · Coluna de tenant nas tabelas de negócio ───────────────────────
-- Nasce NULL para não travar a migração com 322 mil linhas; o backfill e o
-- NOT NULL vêm no 0002, depois de existir uma empresa para receber o acervo.
do $$
declare t text;
begin
  foreach t in array array[
    'pois', 'cadastro_cliente', 'area_trabalho', 'fachada_anotacao',
    'analise_ia', 'fonte_arquivos', 'comentarios', 'horario_funcionamento',
    'images_urls', 'streetview_imgs', 'cnefe_coletiva'
  ] loop
    execute format(
      'alter table %I add column if not exists tenant_id uuid references tenants(id)', t);
  end loop;
end $$;

-- ── 4 · Índices com tenant_id NA FRENTE ───────────────────────────────
-- RLS é avaliado por linha. Sem tenant_id como primeira coluna, a policy força
-- varredura completa e o isolamento vira o gargalo — em `cadastro_cliente` são
-- 102 mil linhas, em `images_urls` são 40 mil.
create index if not exists pois_tenant_idx            on pois (tenant_id, id);
create index if not exists cadastro_cliente_tenant_idx on cadastro_cliente (tenant_id, id);
create index if not exists area_trabalho_tenant_idx   on area_trabalho (tenant_id, salvo_em desc);
create index if not exists fachada_anotacao_tenant_idx on fachada_anotacao (tenant_id, poi_id);
-- `analise_ia` e `fonte_arquivos` não têm `id`: a primeira é chaveada por
-- `poi_id`, a segunda pelo arquivo carregado. Índice sobre coluna inexistente
-- derrubaria a migração inteira no meio.
create index if not exists analise_ia_tenant_idx      on analise_ia (tenant_id, poi_id);
create index if not exists fonte_arquivos_tenant_idx  on fonte_arquivos (tenant_id, carregado_em desc);
create index if not exists comentarios_tenant_idx     on comentarios (tenant_id, poi_id);
create index if not exists horario_tenant_idx         on horario_funcionamento (tenant_id, poi_id);
create index if not exists images_urls_tenant_idx     on images_urls (tenant_id, poi_id);
create index if not exists streetview_tenant_idx      on streetview_imgs (tenant_id, poi_id);
create index if not exists cnefe_coletiva_tenant_idx  on cnefe_coletiva (tenant_id, id);
