-- =====================================================================
-- 0008 — saída da skill `tratamento-cnpj`
--
-- A skill devolve QUATRO EIXOS INDEPENDENTES sobre cada CNPJ, e a tabela os
-- guarda separados de propósito. Achatá-los num "score" perderia justamente o
-- que ela existe para dizer: um cadastro pode ter perfil comercial forte E
-- coordenada ruim, e essas duas coisas levam a ações diferentes — uma vira
-- visita, a outra vira pedido de endereço.
-- =====================================================================

set local search_path = comercialradar, public;

create table if not exists cnpj_tratado (
  id              bigserial primary key,
  tenant_id       uuid not null references tenants(id),
  cnpj            text not null,
  cod_municipio   text,
  razao_social    text,
  nome_fantasia   text,
  cnae            text,
  situacao        text,

  -- os quatro eixos, como a skill os entrega
  aptidao_geo     text,   -- APTO_COORDENADA … NAO_APTO
  perfil_comercial text,  -- COMERCIO_ATENDIMENTO … NAO_COMERCIAL
  evidencia       text,   -- EXISTE_QUASE_CERTO … SEM_EVIDENCIA
  potencial       text,   -- a decisão operacional
  rota            text,   -- RECLASSIFICACAO_1_1 | INDIVIDUALIZACAO_MULTI | …

  lat             double precision,
  lng             double precision,
  incerteza_m     integer,        -- a skill DECLARA a incerteza posicional
  poi_id          integer references pois(id) on delete set null,
  criado_em       timestamptz not null default now(),
  constraint cnpj_tratado_unico unique (tenant_id, cnpj)
);

create index if not exists cnpj_tratado_tenant_idx on cnpj_tratado (tenant_id, potencial);
create index if not exists cnpj_tratado_cnpj_idx   on cnpj_tratado (tenant_id, cnpj);
create index if not exists cnpj_tratado_poi_idx    on cnpj_tratado (tenant_id, poi_id);

drop trigger if exists trg_tenant_cnpj_tratado on cnpj_tratado;
create trigger trg_tenant_cnpj_tratado before insert on cnpj_tratado
  for each row execute function preencher_tenant();

alter table cnpj_tratado enable row level security;
alter table cnpj_tratado force  row level security;

drop policy if exists tenant_isolado on cnpj_tratado;
create policy tenant_isolado on cnpj_tratado
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

grant select, insert, update, delete on cnpj_tratado to comercialradar_app, comercialradar_root;
