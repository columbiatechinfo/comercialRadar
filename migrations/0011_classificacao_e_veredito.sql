-- =====================================================================
-- 0011 — a classificação e o veredito comercial da IA
--
-- Pedido do usuário em 14/08/2026. Até aqui a leitura de fachada respondia
-- "como é o imóvel" para efeito de cadastro de saneamento; faltava a resposta
-- que o produto vende: ISTO É COMÉRCIO OU NÃO, e com que convicção.
--
-- Colunas próprias, e não só dentro do `anotacao` jsonb, porque estas são as
-- que a tela filtra e ordena — nota 8 para cima, tipo de cliente, quem tem
-- gente na porta. Filtro sobre jsonb em 22 mil linhas varre a tabela inteira.
-- =====================================================================

set local search_path = comercialradar, public;

alter table fachada_anotacao
  add column if not exists tipo_cliente          text,
  add column if not exists habitacoes_distintas  integer,
  add column if not exists metodo_habitacoes     text,
  add column if not exists tipo_via              text,
  add column if not exists pessoas_na_imagem     text,
  add column if not exists numero_na_parede      text,
  -- aprova_comercial | reprova | recomenda_visita
  add column if not exists veredito_comercial    text,
  -- 0 = com certeza NÃO é comercial · 10 = com certeza É
  add column if not exists nota_comercial        smallint,
  add column if not exists veredito_justificativa text,
  add column if not exists veredito_fatores      jsonb;

alter table fachada_anotacao drop constraint if exists nota_comercial_0_10;
alter table fachada_anotacao add constraint nota_comercial_0_10
  check (nota_comercial is null or nota_comercial between 0 and 10);

-- tenant_id na frente, como em todo índice deste schema: a policy é avaliada
-- por linha, e sem isso o isolamento vira o gargalo.
create index if not exists fachada_veredito_idx
  on fachada_anotacao (tenant_id, veredito_comercial, nota_comercial desc);
create index if not exists fachada_tipo_cliente_idx
  on fachada_anotacao (tenant_id, tipo_cliente);
