-- =====================================================================
-- 0015 — a camada do iFood
--
-- O valor aqui não é o cardápio: é o CNPJ. O `merchant-info/graphql` devolve
-- `documents.CNPJ.value` com endereço completo e coordenada DO ESTABELECIMENTO
-- — e CNPJ é a chave que cruza com a Receita, que é justamente o que falta na
-- maioria dos POIs minerados.
--
-- Tabela própria, e não colunas em `pois`, por três razões:
--   · o merchant existe mesmo sem casar com POI nosso, e jogar fora o que não
--     casa seria perder o que a plataforma sabe e nós não;
--   · um POI pode ter mais de um merchant (a mesma cozinha com duas marcas no
--     app), e coluna não comporta isso;
--   · o estado da coleta (`BLOQUEADO` × vazio) é atributo do merchant, não do
--     POI, e é o dado que impede a base de mentir sobre cobertura.
-- =====================================================================

set local search_path = comercialradar, public;

create table if not exists ifood_merchant (
  merchant_id    text primary key,          -- UUID do iFood; a chave técnica
  nome           text,
  categoria      text,
  slug           text,
  nota           numeric,
  cnpj           text,
  telefone       text,
  avaliacoes     integer,
  rua            text,
  numero         text,
  bairro         text,
  cep            text,
  lat            double precision,
  lng            double precision,
  -- DETALHE_OK | BLOQUEADO | ERRO. `BLOQUEADO` é diferente de vazio, e essa
  -- distinção é o coração da honestidade desta tabela: o piloto descobriu
  -- 1.139 lojas e conseguiu detalhar UMA — dizer que as outras 1.138 "não têm
  -- CNPJ" seria falso.
  estado_detalhe text,
  poi_id         integer references pois(id) on delete set null,
  tenant_id      uuid references tenants(id),
  bruto          jsonb,
  visto_em       timestamp not null default now()
);

-- A consulta que o enriquecimento faz: quem ainda falta detalhar.
create index if not exists ifood_estado_idx on ifood_merchant (estado_detalhe);
-- O CNPJ é a ponte para a Receita e para o nosso cadastro.
create index if not exists ifood_cnpj_idx on ifood_merchant (cnpj)
  where cnpj is not null and cnpj <> '';
create index if not exists ifood_poi_idx on ifood_merchant (poi_id)
  where poi_id is not null;
-- Casamento por proximidade quando não há CNPJ.
create index if not exists ifood_geo_idx on ifood_merchant (lat, lng)
  where lat is not null;
