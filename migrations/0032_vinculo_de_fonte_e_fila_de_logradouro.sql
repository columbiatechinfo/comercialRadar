-- 0032 — de que fontes um POI é feito, com que confiança, e como desfazer.
--
-- ============================ POR QUE ISTO EXISTE ============================
--
-- Até aqui um POI era uma linha e pronto: a fusão acontecia dentro da skill de
-- extração e o que sobrava era o sobrevivente. Quem quisesse saber de quais
-- registros ele foi feito tinha de ir aos arquivos da extração; quem quisesse
-- DESFAZER uma fusão errada tinha de reextrair a UF inteira.
--
-- E fusão errada não é caso raro. Medido no RS em 25/08/2026: das 11.676 fusões
-- suspeitas, a IA julgou 400 pares sorteados e disse que 66,2% uniram
-- estabelecimentos DISTINTOS — cerca de 7.800 lojas apagadas numa UF.
--
-- A própria skill diz onde ela para: "quem promove observação a entidade
-- canônica com identidade perene é a camada de cima". Esta migração é essa
-- camada. O vínculo passa a morar no banco, e por isso desfazê-lo é uma
-- transação, não uma reextração de horas.

create table if not exists comercialradar.vinculo_poi (
  id              bigserial primary key,
  poi_id          integer not null references comercialradar.pois(id) on delete cascade,

  -- A FONTE E O ID DELA. `cadastro_cliente` entra aqui como qualquer outra:
  -- a carteira da concessionária é mais uma aba na ficha do ponto, e o
  -- cruzamento com ela é o produto.
  fonte           text not null,          -- overture | osm | fsq | ifood | cadastro | estadual | maps
  id_fonte        text not null,

  -- O QUE AQUELA FONTE AFIRMOU, guardado aqui e não só referenciado.
  --
  -- Duas necessidades pedem a mesma coisa. A ficha do POI mostra UMA ABA POR
  -- FONTE, com o que cada uma diz — nome, categoria, telefone, endereço — e
  -- essas afirmações divergem entre si, que é justamente o que o operador
  -- precisa ver para julgar. E quando ele desvincula uma aba, o registro
  -- desvinculado VIRA UM POI NOVO: sem os dados aqui, esse POI nasceria vazio,
  -- ou seria preciso voltar aos arquivos da extração — que são de outra máquina,
  -- de outro run, e podem já ter sido substituídos.
  --
  -- Nome e coordenada saem como coluna porque são o que cria o POI novo e o que
  -- se consulta; o resto vive em `dados`, que preserva o vocabulário da fonte
  -- em vez de espremê-lo no nosso.
  nome            text,
  lat             double precision,
  lng             double precision,
  dados           jsonb not null default '{}'::jsonb,

  -- CONFIANÇA DE 1 A 10, e ela precisa dizer de onde veio.
  --
  -- "9" decidido por regra forte e "9" decidido por modelo de linguagem são
  -- afirmações diferentes sobre o mundo, e quem opera precisa poder separá-las
  -- — para filtrar, para auditar, e para reprocessar só o que uma delas decidiu
  -- quando a regra ou o modelo mudar.
  confianca       smallint not null check (confianca between 1 and 10),
  confianca_origem text not null,         -- regra_forte | ia | manual | importacao
  motivo          text,                   -- o parecer, em uma frase
  modelo          text,                   -- qual modelo julgou, quando foi `ia`

  -- ESTADO. Desvincular NÃO apaga a linha.
  --
  -- Trilha de vínculo editada depois do fato vale menos que trilha completa —
  -- é a mesma regra que fez os 66 registros de auditoria com tenant nulo não
  -- serem retro-atribuídos. Quem desfez, quando, e para onde o registro foi.
  estado          text not null default 'vinculado'
                  check (estado in ('vinculado', 'desvinculado')),
  desvinculado_por  text,
  desvinculado_em   timestamptz,
  desvinculado_para integer references comercialradar.pois(id) on delete set null,

  tenant_id       uuid not null default (current_setting('app.tenant_id', true))::uuid,
  criado_em       timestamptz not null default now(),

  -- O MESMO REGISTRO DE FONTE NÃO PODE COMPOR DOIS POIs AO MESMO TEMPO.
  -- Parcial de propósito: depois de desvinculado, ele PODE aparecer de novo,
  -- ligado ao POI novo que nasceu dele. Sem o `where`, desvincular e revincular
  -- esbarraria na própria trilha.
  constraint vinculo_poi_unico unique (fonte, id_fonte, poi_id)
);

create unique index if not exists ix_vinculo_poi_ativo
  on comercialradar.vinculo_poi (tenant_id, fonte, id_fonte)
  where estado = 'vinculado';

-- A leitura quente é "abra a ficha deste POI": todas as fontes dele, de uma vez.
-- `tenant_id` primeiro porque a RLS é avaliada POR LINHA e o índice precisa
-- servir à política antes de servir à consulta.
create index if not exists ix_vinculo_poi_ficha
  on comercialradar.vinculo_poi (tenant_id, poi_id, estado);

-- A fila de baixa confiança é consulta de rotina — e é pequena perto da tabela.
create index if not exists ix_vinculo_poi_duvidoso
  on comercialradar.vinculo_poi (tenant_id, confianca)
  where estado = 'vinculado' and confianca <= 5;

-- O GATILHO, e não só o `default` da coluna.
--
-- A regra da migração 0029 cobra o gatilho, e o motivo é que `default` só age
-- quando ninguém informa a coluna: um `insert` que passa `tenant_id` como NULL
-- explicitamente fura o default, e a linha nasce sem empresa — invisível para
-- todo mundo, inclusive para quem a criou. O teste `test_isolamento_tenant`
-- reprovou esta tabela por isso, que é exatamente o serviço dele.
drop trigger if exists tg_tenant_vinculo_poi on comercialradar.vinculo_poi;
create trigger tg_tenant_vinculo_poi
  before insert on comercialradar.vinculo_poi
  for each row execute function comercialradar.preencher_tenant();

alter table comercialradar.vinculo_poi enable row level security;
alter table comercialradar.vinculo_poi force  row level security;

drop policy if exists tenant_isolado on comercialradar.vinculo_poi;
create policy tenant_isolado on comercialradar.vinculo_poi
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- O subselect em volta do `current_setting` não é enfeite: sem ele o Postgres
-- reavalia a função POR LINHA, e a política vira o gargalo da consulta.


-- ==================== A FILA DE REVISÃO DO LOGRADOURO =======================
--
-- `logradouro_ajustado` já guarda o tier. O que faltava era o outro lado: o que
-- a pessoa fez com o que caiu em `REVISAR` e `HUMANO`.
--
-- Sem `revisado_por`, a correção humana é indistinguível da automática três
-- meses depois — e a pergunta "quem decidiu que esta rua se chama assim" fica
-- sem resposta justamente nos casos em que ela importa, que são os que a
-- máquina não soube resolver.

alter table comercialradar.logradouro_ajustado
  add column if not exists revisao_status text not null default 'pendente'
    check (revisao_status in ('pendente', 'corrigido', 'confirmado', 'descartado')),
  add column if not exists logradouro_corrigido text,
  add column if not exists revisado_por text,
  add column if not exists revisado_em timestamptz,
  add column if not exists revisao_nota text;

-- Só o que precisa de gente entra na fila. Índice parcial porque `CONFIRMA` é
-- 98% da tabela e nunca aparece aqui.
create index if not exists ix_logradouro_fila_humana
  on comercialradar.logradouro_ajustado (scope_id, revisao_status, tier)
  where tier in ('REVISAR', 'HUMANO');

comment on column comercialradar.logradouro_ajustado.revisao_status is
  'pendente: espera gente · corrigido: humano reescreveu · confirmado: humano '
  'aprovou a forma marcada · descartado: nao e logradouro utilizavel';
