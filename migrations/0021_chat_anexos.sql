-- 0021 — anexos das conversas
--
-- Guardar o arquivo inteiro no banco seria caro e desnecessário: o que o modelo
-- consome é o TEXTO extraído (ou a imagem já reduzida). O original fica em
-- disco, referenciado — quem quiser reabrir tem o caminho, e a conversa não
-- carrega dezenas de megabytes que ninguém relê.
--
-- `extraido` guarda o que foi de fato para o modelo. Sem isso, reabrir a
-- conversa exigiria reprocessar o arquivo, e uma extração diferente faria a
-- conversa contar uma história que não foi a que aconteceu.

create table if not exists comercialradar.chat_anexo (
  id          bigserial primary key,
  tenant_id   uuid not null,
  conversa_id uuid not null
    references comercialradar.chat_conversa(id) on delete cascade,
  mensagem_id bigint
    references comercialradar.chat_mensagem(id) on delete set null,

  nome        text not null,
  tipo        text not null,          -- imagem | pdf | word | planilha | zip | texto
  bytes       integer not null default 0,
  caminho     text,                   -- onde o original ficou em disco
  extraido    text,                   -- o texto que o modelo leu
  meta        jsonb not null default '{}'::jsonb,
  criado_em   timestamptz not null default now()
);

create index if not exists ix_chat_anexo_conversa
  on comercialradar.chat_anexo (tenant_id, conversa_id, id);

alter table comercialradar.chat_anexo enable row level security;
alter table comercialradar.chat_anexo force  row level security;

drop policy if exists isolamento on comercialradar.chat_anexo;
create policy isolamento on comercialradar.chat_anexo
  using      (tenant_id = (select current_setting('app.tenant_id', true))::uuid)
  with check (tenant_id = (select current_setting('app.tenant_id', true))::uuid);

drop trigger if exists preencher_tenant_chat_anexo on comercialradar.chat_anexo;
create trigger preencher_tenant_chat_anexo
  before insert on comercialradar.chat_anexo
  for each row execute function comercialradar.preencher_tenant();
