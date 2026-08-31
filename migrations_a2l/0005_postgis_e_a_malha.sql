-- 0005_postgis_e_a_malha.sql — geometria de verdade para a malha municipal.
--
-- POR QUE A EXTENSÃO ENTRA, e por que a decisão não foi minha.
--
-- Minha migração 0002 criou `resources_root.ibge_malha.geom` como `jsonb`,
-- porque o PostGIS não estava instalado no `a2l` e eu não instalo extensão em
-- banco compartilhado por conta própria. Era a escolha certa naquele momento e
-- estava errada como destino: quatro arquivos do projeto já usam PostGIS sobre
-- essa coluna, e a função que carrega a malha grava com
-- `ST_SetSRID(ST_GeomFromGeoJSON(...))`. Com `jsonb`, nada disso roda.
--
-- O QUE DECIDIU: descobrir de qual município é uma coordenada é ponto-em-
-- polígono contra 5.570 geometrias, e o painel faz isso a cada navegação do
-- mapa. Em Python seria uma varredura por requisição, sem índice. No banco é
-- um índice GiST. A regra da casa é que desempenho é critério de primeira
-- classe, e aqui ele aponta para um lado só.
--
-- A extensão estava DISPONÍVEL e não instalada (3.3.7), e nenhum outro banco
-- desta instância a usa — então isto é a primeira vez, e vale registrar que foi
-- deliberado.
--
-- SEGURO PORQUE A TABELA ESTÁ VAZIA. `ibge_malha` tem 0 linhas: a carga em
-- lote nunca rodou, e a carga sob demanda também não, justamente porque
-- dependia disto. Trocar o tipo de uma coluna vazia não converte dado nenhum.
--
-- RODAR COMO `supabase_admin`, e não como `migrator`. Duas razões, e as duas
-- foram descobertas tentando:
--
--   · `create extension` e `alter role ... set search_path` exigem superusuário;
--   · `alter table resources_root.ibge_malha` exige ser DONO dela — e o dono é
--     `resources_loader` desde que a posse do schema foi transferida para o
--     papel que carrega base pública. `migrator` parou com "must be owner of
--     table ibge_malha", que é a consequência certa de uma decisão certa.
--
-- É a única migração deste sistema que não roda com `migrator`, e o motivo é
-- que ela mexe em coisa que não é do sistema: uma extensão do banco e o
-- `search_path` de papéis compartilhados.

-- ─────────────────────────────────────────────────────────────────────
-- A extensão
-- ─────────────────────────────────────────────────────────────────────
--
-- No schema `extensions`, que é onde a pilha Supabase põe as demais, e não no
-- `public`: extensão espalhada pelo `public` é o que faz um `pg_dump` de
-- schema virar um arquivo que não restaura em lugar nenhum.
--
-- As chamadas do código são SEM QUALIFICAR (`ST_SetSRID(...)`), então
-- `extensions` precisa estar no `search_path` de quem chama. O `alter role`
-- abaixo cuida disso para os papéis do produto.

create extension if not exists postgis with schema extensions;


-- ─────────────────────────────────────────────────────────────────────
-- A coluna volta a ser geometria
-- ─────────────────────────────────────────────────────────────────────

set local search_path = resources_root, extensions, public;

alter table resources_root.ibge_malha
  alter column geom type extensions.geometry(Geometry, 4326)
  using null;

comment on column resources_root.ibge_malha.geom is
  'Polígono do município, SRID 4326. Era jsonb entre 31/08/2026 e esta '
  'migração, enquanto o PostGIS não existia no banco.';

-- O ÍNDICE É O MOTIVO DE TUDO ISTO. Sem ele, `ST_Contains` sobre 5.570
-- municípios é varredura completa — e aí valia mais ter ficado no jsonb.
create index if not exists ix_malha_geom
  on resources_root.ibge_malha using gist (geom);

-- E o que a resolução por nome usa: "Canoas/RS" -> 4304606.
create index if not exists ix_malha_nome_uf
  on resources_root.ibge_malha (uf, lower(nome));


-- ─────────────────────────────────────────────────────────────────────
-- Quem chama, chama sem qualificar
-- ─────────────────────────────────────────────────────────────────────
--
-- `server.py`, `area_utils.py`, `ajuste_logradouro.py` e
-- `scripts/coerencia_local.py` escrevem `ST_AsGeoJSON(...)`, e não
-- `extensions.ST_AsGeoJSON(...)`. Pôr `extensions` no `search_path` do papel
-- faz os quatro continuarem funcionando sem uma linha alterada — e evita a
-- alternativa, que era prefixar dezenas de chamadas e errar uma.

alter role app_user          set search_path = radar_comercial, extensions, public;
alter role resources_loader  set search_path = resources_root, extensions, public;

-- O QUE FALTA, E QUE ESTA MIGRAÇÃO NÃO PODE FAZER: `create extension` exige
-- superusuário. Rodar antes, uma vez, com o papel da plataforma:
--
--   docker exec supabase-db psql -U supabase_admin -d a2l \
--     -c 'create extension if not exists postgis with schema extensions'
--
-- O `create extension` acima fica no arquivo assim mesmo, e não é redundância:
-- ele documenta a dependência para quem for montar este banco do zero, e o
-- `if not exists` o torna inofensivo quando já foi criada.
