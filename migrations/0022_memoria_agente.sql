-- 0022 — memória do agente entre conversas
--
-- O histórico da `chat_mensagem` guarda O QUE FOI DITO numa conversa. Memória é
-- outra coisa: o que ficou VALENDO depois que a conversa acabou. Sem separar as
-- duas, lembrar de algo exigiria reler conversas inteiras, e o que decidiu não
-- se distingue do que foi só tentativa.
--
-- O desenho segue a ideia do claude-mem: índice compacto primeiro, detalhe só
-- quando pedido. Por isso `resumo` é curto e obrigatório, e `detalhe` é longo e
-- opcional — o agente vê a lista de resumos e pede o que precisar.

create table if not exists comercialradar.memoria (
  id         bigserial primary key,
  tenant_id  uuid not null,

  -- frase única, do jeito que se diria a um colega. É o que entra no índice.
  resumo     text not null,
  detalhe    text,

  -- 'fato' o que é verdade sobre o sistema ou os dados
  -- 'decisao' o que se escolheu fazer, e por quê
  -- 'preferencia' como o usuário quer que se trabalhe
  -- 'limite' o que NÃO funciona, para não se tentar de novo
  tipo       text not null default 'fato',

  -- de onde veio: qual conversa, para poder voltar à origem
  conversa_id uuid references comercialradar.chat_conversa(id) on delete set null,

  -- palavras para o agente achar. Busca por texto simples, sem vetor: a base
  -- é de dezenas de memórias, não de milhões, e ILIKE resolve sem infra nova.
  chaves     text[] not null default '{}',

  criado_em  timestamptz not null default now(),
  usado_em   timestamptz,
  vezes_usada integer not null default 0,

  constraint memoria_tipo_valido
    check (tipo in ('fato', 'decisao', 'preferencia', 'limite'))
);

create index if not exists ix_memoria_recente
  on comercialradar.memoria (tenant_id, criado_em desc);
create index if not exists ix_memoria_chaves
  on comercialradar.memoria using gin (chaves);

alter table comercialradar.memoria enable row level security;
alter table comercialradar.memoria force  row level security;

drop policy if exists isolamento on comercialradar.memoria;
create policy isolamento on comercialradar.memoria
  using      (tenant_id = (select current_setting('app.tenant_id', true))::uuid)
  with check (tenant_id = (select current_setting('app.tenant_id', true))::uuid);

drop trigger if exists preencher_tenant_memoria on comercialradar.memoria;
create trigger preencher_tenant_memoria
  before insert on comercialradar.memoria
  for each row execute function comercialradar.preencher_tenant();
