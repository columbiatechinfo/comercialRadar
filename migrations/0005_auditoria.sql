-- =====================================================================
-- 0005 — log de auditoria
--
-- A matriz do RBAC diz quem PODE; isto registra quem FEZ. Em sistema
-- multi-cliente, o dia em que perguntarem "quem aprovou isto?" precisa ter
-- resposta — e o dossiê aprovado é documento que sai da empresa.
--
-- Por TRIGGER, e não por código de rota, por dois motivos:
--   1. rota esquecida não escapa do registro — foi assim que 29 rotas ficaram
--      sem autenticação e ninguém percebeu por semanas;
--   2. o pipeline e os scripts também passam a ser registrados, e eles nunca
--      passariam por um decorator de FastAPI.
-- =====================================================================

set local search_path = comercialradar, public;

create table if not exists auditoria (
  id          bigserial primary key,
  em          timestamptz not null default now(),
  tenant_id   uuid,
  usuario_id  uuid,            -- nulo = pipeline/script, que não tem usuário
  papel_bd    text not null default current_user,
  acao        text not null,   -- INSERT | UPDATE | DELETE
  tabela      text not null,
  registro    text,            -- chave do registro, como texto
  antes       jsonb,
  depois      jsonb
);

comment on table auditoria is
  'Append-only. Sem UPDATE e sem DELETE por policy: log que se pode editar não '
  'serve para auditar, e o primeiro a querer editá-lo é justamente quem tem o '
  'que esconder.';

create index if not exists auditoria_tenant_idx  on auditoria (tenant_id, em desc);
create index if not exists auditoria_usuario_idx on auditoria (usuario_id, em desc);
create index if not exists auditoria_tabela_idx  on auditoria (tabela, em desc);

create or replace function registrar_auditoria() returns trigger
language plpgsql security definer as $$
declare
  linha   jsonb;
  chave   text;
  usuario uuid;
  empresa uuid;
begin
  linha := to_jsonb(coalesce(new, old));
  chave := coalesce(linha ->> 'id', linha ->> 'poi_id', '');
  usuario := nullif(current_setting('app.usuario_id', true), '')::uuid;
  empresa := coalesce(nullif(current_setting('app.tenant_id', true), '')::uuid,
                      (linha ->> 'tenant_id')::uuid);

  insert into auditoria (tenant_id, usuario_id, acao, tabela, registro, antes, depois)
  values (empresa, usuario, tg_op, tg_table_name, chave,
          case when tg_op in ('UPDATE','DELETE') then to_jsonb(old) end,
          case when tg_op in ('INSERT','UPDATE') then to_jsonb(new) end);
  return coalesce(new, old);
end $$;

-- SECURITY DEFINER: a trigger grava com os direitos do DONO da função, não do
-- usuário. Sem isso, o papel de aplicação precisaria de INSERT em `auditoria` —
-- e quem pode inserir no log escolhe o que o log diz.

do $$
declare t text;
begin
  -- Decisão e identidade. `pois` fica de fora de propósito: o pipeline insere
  -- dezenas de milhares por rodada, e auditar isso encheria a tabela com ruído
  -- que esconde justamente o evento raro que se quer achar. O DELETE de POI,
  -- esse sim, é registrado.
  foreach t in array array['atribuicao', 'usuarios', 'tenants'] loop
    execute format('drop trigger if exists trg_auditoria on %I', t);
    execute format(
      'create trigger trg_auditoria after insert or update or delete on %I '
      'for each row execute function registrar_auditoria()', t);
  end loop;

  execute 'drop trigger if exists trg_auditoria_del on pois';
  execute 'create trigger trg_auditoria_del after delete on pois '
          'for each row execute function registrar_auditoria()';
end $$;

alter table auditoria enable row level security;
alter table auditoria force  row level security;

-- Leitura: root vê tudo (BYPASSRLS); admin vê a própria empresa. Ninguém abaixo
-- de admin lê o log — ele contém o `antes` de cada mudança.
drop policy if exists auditoria_leitura on auditoria;
create policy auditoria_leitura on auditoria
  for select using (
    tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid
    and coalesce((select nullif(current_setting('app.nivel', true), '')), 'user')
        in ('admin', 'root'));

-- NÃO existe policy de UPDATE nem de DELETE. Com RLS ligada, o que não tem
-- policy é negado — então o log é append-only por construção, e não por
-- combinado. Nem o dono da tabela escapa: FORCE alcança ele também.

grant select on auditoria to comercialradar_app, comercialradar_root;
