-- 0020 — conversas com histórico, para o painel de chat
--
-- Duas tabelas e não uma: a conversa é o que o usuário vê e renomeia, a
-- mensagem é o que o modelo consome. Juntar as duas obrigaria a reescrever a
-- linha da conversa a cada fala.
--
-- O histórico é gravado no formato que a API do modelo espera (papel + texto),
-- inclusive as chamadas de ferramenta. Guardar só o que apareceu na tela
-- pareceria mais limpo e quebraria a retomada: o modelo precisa ver o que ele
-- mesmo pediu e o que a ferramenta respondeu, ou repete a pergunta.

create table if not exists comercialradar.chat_conversa (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null,
  usuario_id  uuid,
  titulo      text not null default 'Nova conversa',
  criada_em   timestamptz not null default now(),
  mexida_em   timestamptz not null default now(),
  arquivada   boolean not null default false
);

create table if not exists comercialradar.chat_mensagem (
  id         bigserial primary key,
  tenant_id  uuid not null,
  conversa_id uuid not null
    references comercialradar.chat_conversa(id) on delete cascade,
  -- 'user' | 'assistant' | 'system' | 'tool'
  papel      text not null,
  conteudo   text not null default '',
  -- as chamadas de ferramenta e seus retornos, como o modelo os viu
  ferramenta text,
  dados      jsonb,
  criada_em  timestamptz not null default now(),

  constraint chat_papel_valido
    check (papel in ('user', 'assistant', 'system', 'tool'))
);

-- `tenant_id` como primeira coluna: a RLS é avaliada por linha e sem isso a
-- política vira varredura completa. Regra da casa.
create index if not exists ix_chat_conversa_recente
  on comercialradar.chat_conversa (tenant_id, mexida_em desc)
  where not arquivada;
create index if not exists ix_chat_mensagem_conversa
  on comercialradar.chat_mensagem (tenant_id, conversa_id, id);

alter table comercialradar.chat_conversa  enable row level security;
alter table comercialradar.chat_conversa  force  row level security;
alter table comercialradar.chat_mensagem  enable row level security;
alter table comercialradar.chat_mensagem  force  row level security;

drop policy if exists isolamento on comercialradar.chat_conversa;
create policy isolamento on comercialradar.chat_conversa
  using      (tenant_id = (select current_setting('app.tenant_id', true))::uuid)
  with check (tenant_id = (select current_setting('app.tenant_id', true))::uuid);

drop policy if exists isolamento on comercialradar.chat_mensagem;
create policy isolamento on comercialradar.chat_mensagem
  using      (tenant_id = (select current_setting('app.tenant_id', true))::uuid)
  with check (tenant_id = (select current_setting('app.tenant_id', true))::uuid);

drop trigger if exists preencher_tenant_chat_conversa on comercialradar.chat_conversa;
create trigger preencher_tenant_chat_conversa
  before insert on comercialradar.chat_conversa
  for each row execute function comercialradar.preencher_tenant();

drop trigger if exists preencher_tenant_chat_mensagem on comercialradar.chat_mensagem;
create trigger preencher_tenant_chat_mensagem
  before insert on comercialradar.chat_mensagem
  for each row execute function comercialradar.preencher_tenant();
