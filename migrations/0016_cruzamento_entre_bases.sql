-- 0016 — cruzamento entre bases, com a CHAVE e a EVIDÊNCIA registradas
--
-- Por que uma tabela e não mais uma coluna `poi_id`:
--
-- O `poi_id` que cada base já carrega diz QUE casou, mas não diz POR QUÊ nem
-- COM QUE FORÇA. Casar por CNPJ (documento igual) e casar por nome parecido a
-- 40 m são coisas de confiabilidade muito diferente, e um FK único achata as
-- duas no mesmo valor. Quando o supervisor perguntar "por que este imóvel virou
-- esta empresa?", a resposta precisa estar gravada.
--
-- Também é N-para-N de propósito: um endereço com três lojas gera três
-- cruzamentos, e esconder isso atrás de um FK escolheria uma arbitrariamente.

create table if not exists comercialradar.cruzamento (
  id           bigserial primary key,
  tenant_id    uuid not null,

  -- o par, sempre gravado em ordem canônica (base_a < base_b) para que
  -- A↔B e B↔A não virem duas linhas dizendo a mesma coisa
  base_a       text not null,
  id_a         text not null,
  base_b       text not null,
  id_b         text not null,

  -- 'cnpj' | 'endereco' | 'nome' | 'geo'
  chave        text not null,
  -- 0..1. Em 'cnpj' é sempre 1: documento igual não tem gradação.
  score        numeric(4,3) not null,
  distancia_m  numeric(8,1),

  -- o que casou, em texto, para auditoria humana: os dois nomes normalizados,
  -- os dois endereços, o CNPJ. Sem isto a linha é inauditável.
  evidencia    jsonb not null default '{}'::jsonb,

  -- Um casamento AMBÍGUO é registrado, não descartado nem escolhido: quando o
  -- mesmo lado A casa com vários B de score equivalente, a decisão é humana.
  -- Silenciar aqui produziria base "limpa" e errada.
  ambiguo      boolean not null default false,
  concorrentes integer not null default 0,

  criado_em    timestamptz not null default now(),

  constraint cruzamento_chave_valida
    check (chave in ('cnpj', 'endereco', 'nome', 'geo')),
  constraint cruzamento_ordem_canonica
    check (base_a <= base_b),
  constraint cruzamento_score_faixa
    check (score >= 0 and score <= 1)
);

-- `tenant_id` como primeira coluna de todo índice: a RLS é avaliada por linha e
-- sem isso a política vira varredura completa. É a regra da casa.
create unique index if not exists ux_cruzamento_par
  on comercialradar.cruzamento (tenant_id, base_a, id_a, base_b, id_b, chave);

create index if not exists ix_cruzamento_a
  on comercialradar.cruzamento (tenant_id, base_a, id_a);
create index if not exists ix_cruzamento_b
  on comercialradar.cruzamento (tenant_id, base_b, id_b);
create index if not exists ix_cruzamento_revisar
  on comercialradar.cruzamento (tenant_id, ambiguo) where ambiguo;

alter table comercialradar.cruzamento enable row level security;
alter table comercialradar.cruzamento force  row level security;

-- fail-closed: sem `app.tenant_id` declarado, `current_setting` devolve NULL e
-- a comparação nega. Zero linhas é o modo de falhar certo.
drop policy if exists isolamento on comercialradar.cruzamento;
create policy isolamento on comercialradar.cruzamento
  using      (tenant_id = (select current_setting('app.tenant_id', true))::uuid)
  with check (tenant_id = (select current_setting('app.tenant_id', true))::uuid);

drop trigger if exists preencher_tenant_cruzamento on comercialradar.cruzamento;
create trigger preencher_tenant_cruzamento
  before insert on comercialradar.cruzamento
  for each row execute function comercialradar.preencher_tenant();
