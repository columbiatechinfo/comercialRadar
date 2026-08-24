-- 0023 — a quarta saída da decisão, e o catálogo que faz a tela envelhecer bem
--
-- DUAS COISAS, e as duas vêm do modelo em `assets/modelo_frontend`. A decisão
-- está no ADR 0005.
--
-- 1 · VISITA DE CAMPO NÃO É UM RÓTULO, É UMA PAUTA
--
-- Hoje a decisão tem três saídas: aprovado, reprovado, devolvido. Falta a que
-- diz "só o local resolve". Mas mandar alguém a campo sem dizer O QUE verificar
-- é mandar de novo depois — por isso a pauta é obrigatória por CHECK, do mesmo
-- jeito que reprovar já exige motivo.
--
-- Cada item da pauta só entra se um fato do caso o justifica. Item sem fato
-- fica de fora; não se inventa justificativa para encher lista.
--
-- 2 · A TELA LÊ CATÁLOGO, NÃO COLUNA FIXA
--
-- As abas por fonte (POI, Google, Receita, Redes Sociais, Delivery, IA das
-- Imagens) hoje precisariam de HTML novo a cada campo novo. Com o catálogo,
-- campo novo numa skill de tratamento entra numa linha de `campo_catalogo` e
-- aparece na aba sozinho.
--
-- É o que separa uma tela que dura de uma que precisa de deploy toda vez que a
-- extração aprende algo.

begin;

-- ─── 1 · a quarta saída ──────────────────────────────────────────────────────

alter type comercialradar.decisao_fila add value if not exists 'campo';

commit;
begin;

-- Catálogo do que se pode pedir para verificar no local. É enum e não texto
-- livre porque a pauta vira ordem de serviço: campo aberto não se agrupa, não
-- se conta, e cada operador escreve de um jeito.
do $$ begin
  create type comercialradar.pauta_campo as enum (
    'confirmar_atividade',      -- a placa existe? o comércio opera?
    'confirmar_numero',         -- o número da porta bate com o cadastro
    'contar_unidades',          -- quantas lojas há de fato no imóvel
    'fotografar_fachada',       -- não há imagem, ou a que há está velha
    'registrar_coordenada',     -- a coordenada está imprecisa
    'confirmar_endereco'        -- mais de um endereço plausível
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type comercialradar.prioridade_campo as enum ('normal', 'alta');
exception when duplicate_object then null; end $$;

alter table comercialradar.atribuicao
  add column if not exists pauta comercialradar.pauta_campo[],
  add column if not exists pauta_porque jsonb,
  add column if not exists prioridade comercialradar.prioridade_campo
      not null default 'normal';

comment on column comercialradar.atribuicao.pauta_porque is
  'O FATO que sustenta cada item da pauta: {"confirmar_numero": "o Maps devolveu '
  'S/N e a Receita registra 1842"}. Sem isto a pauta vira lista de desejos, e '
  'quem vai a campo não sabe o que motivou cada item.';

-- Mandar a campo sem pauta é mandar de novo depois. O CHECK recusa.
alter table comercialradar.atribuicao
  drop constraint if exists campo_exige_pauta;
alter table comercialradar.atribuicao
  add constraint campo_exige_pauta check (
    status <> 'campo'
    or (pauta is not null and array_length(pauta, 1) >= 1)
  );

-- ─── 2 · o catálogo de campos das abas ───────────────────────────────────────

do $$ begin
  create type comercialradar.fonte_aba as enum (
    'poi', 'google', 'receita', 'redes_sociais', 'delivery', 'imagens'
  );
exception when duplicate_object then null; end $$;

-- Peso da evidência: o que o campo FAZ pela conclusão, não o que ele vale em
-- número. "CONTRARIA" existe porque evidência contra é evidência — esconder o
-- que desmente é o jeito mais rápido de fabricar confiança falsa.
do $$ begin
  create type comercialradar.peso_evidencia as enum (
    'forte', 'media', 'neutra', 'contraria'
  );
exception when duplicate_object then null; end $$;

create table if not exists comercialradar.campo_catalogo (
  id            bigserial primary key,
  tenant_id     uuid not null,
  fonte         comercialradar.fonte_aba not null,
  chave         text not null,          -- de onde o valor sai no payload
  rotulo        text not null,          -- o que o humano lê
  grupo         text,                   -- subtítulo dentro da aba
  ordem         integer not null default 100,
  peso          comercialradar.peso_evidencia not null default 'neutra',
  formato       text not null default 'texto',   -- texto|numero|data|moeda|lista|url
  -- Procedência é o nome da BASE (IBGE, Google, Receita). O RBAC diz que só o
  -- `root` a enxerga; a coluna existe para ele, e a API a omite para os demais.
  procedencia   text,
  ajuda         text,                   -- o `title` da linha, quando há dúvida
  ativo         boolean not null default true,
  criado_em     timestamptz not null default now(),
  unique (tenant_id, fonte, chave)
);

comment on table comercialradar.campo_catalogo is
  'O que cada aba mostra. A tela lê ESTA tabela, não colunas fixas: campo novo '
  'numa skill de tratamento entra aqui e aparece na aba sem deploy de HTML.';

-- `tenant_id` primeiro em todo índice — RLS é avaliada por linha, e sem isso a
-- policy força varredura completa e o isolamento vira o gargalo.
create index if not exists ix_campo_catalogo_aba
  on comercialradar.campo_catalogo (tenant_id, fonte, ordem)
  where ativo;

alter table comercialradar.campo_catalogo enable row level security;
alter table comercialradar.campo_catalogo force  row level security;

-- Mesma forma das policies que ja existem: `nullif` antes do cast, senao
-- variavel vazia vira ERRO de conversao em vez de negar acesso. E o
-- `(select ...)` embrulha a chamada num InitPlan — sem ele a funcao roda por
-- LINHA, e numa varredura grande a diferenca e de milissegundos para segundos.
drop policy if exists tenant_isolado on comercialradar.campo_catalogo;
create policy tenant_isolado on comercialradar.campo_catalogo
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

-- O mesmo gatilho que as demais tabelas usam: `tenant_id` vem da sessao, nunca
-- do corpo da requisicao. Se viesse do corpo, o cliente escolheria qual tenant
-- quer ler.
drop trigger if exists tg_tenant on comercialradar.campo_catalogo;
create trigger tg_tenant before insert on comercialradar.campo_catalogo
  for each row execute function comercialradar.preencher_tenant();

commit;
