-- =====================================================================
-- 0013 — a leitura de fachada em QUATRO FASES
--
-- Até aqui a avaliação era UMA chamada por POI: manda a foto, recebe tudo.
-- O processo novo separa em quatro perguntas, cada uma isolada da seguinte:
--
--   1. a FOTO presta?          (por imagem do Street View)
--   2. cada foto do Maps é DO ALVO?  (por foto do Maps)
--   3. o que a cena mostra e o que fazer  (por POI, sobre as imagens que passaram)
--   4. ONDE está cada coisa na imagem     (caixas, por imagem — sob demanda)
--
-- Isolar não é preciosismo: enquanto as perguntas vinham juntas, o modelo
-- respondia a segunda por coerência com a primeira em vez de olhar a imagem.
--
-- As fases 1 e 2 ganham tabela porque têm GRÃO DIFERENTE do POI — uma é por
-- imagem do Street View, a outra é por foto do Maps. Enfiá-las em
-- `fachada_anotacao` obrigaria a repetir a linha do POI por imagem.
--
-- A fase 3 ESTENDE `fachada_anotacao` em vez de criar tabela nova, porque a
-- fila do supervisor, o `server.py` e o frontend já leem de lá. Tabela nova
-- significaria reescrever os três para não ganhar nada.
--
-- A fase 4 (as caixas) fica em coluna jsonb e é preenchida SOB DEMANDA: ela
-- custou 61% do tempo da rodada de medição e só serve para a imagem anotada
-- que o supervisor abre. Calcular para 24 mil POIs que ninguém vai abrir é
-- pagar três vezes o preço da produção inteira por um desenho.
-- =====================================================================

set local search_path = comercialradar, public;

-- ---------------------------------------------------------------------
-- FASE 1 — a triagem da foto do Street View
--
-- Grão: uma imagem. `nota_total` e `veredito` viram coluna porque são o que
-- a tela filtra ("me mostra o que precisa de recaptura"); as nove notas ficam
-- em jsonb porque são diagnóstico — servem para entender POR QUE caiu, e
-- ninguém ordena 24 mil linhas por `exposicao`.
-- ---------------------------------------------------------------------
create table if not exists fachada_triagem (
  id             serial primary key,
  sv_id          integer not null references streetview_imgs(id) on delete cascade,
  poi_id         integer not null references pois(id) on delete cascade,
  tenant_id      uuid    references tenants(id),
  -- fachada_nivel_rua | aerea_ou_panoramica | interior |
  -- de_dentro_de_veiculo | sem_fachada_visivel
  tipo_de_foto   text    not null,
  notas          jsonb   not null,
  nota_total     smallint,
  -- aprovada | revisar | recapturar
  veredito       text    not null,
  motivo         text,
  modelo         text,
  schema_versao  text    not null default '2.0.0',
  criado_em      timestamp not null default now()
);

alter table fachada_triagem drop constraint if exists triagem_nota_0_100;
alter table fachada_triagem add constraint triagem_nota_0_100
  check (nota_total is null or nota_total between 0 and 100);

-- tenant na frente, como em todo índice deste schema: a policy é avaliada por
-- linha e sem isso o isolamento vira o gargalo.
create index if not exists fachada_triagem_tenant_idx
  on fachada_triagem (tenant_id, veredito, nota_total desc);
-- Buscar a triagem mais recente de uma imagem é a leitura mais frequente.
create index if not exists fachada_triagem_sv_idx
  on fachada_triagem (sv_id, id desc);
create index if not exists fachada_triagem_poi_idx
  on fachada_triagem (poi_id, id desc);

-- ---------------------------------------------------------------------
-- FASE 2 — a foto do Maps é DO ALVO?
--
-- Existe porque 16% a 19% das fotos que o Google pendura num POI são de outro
-- estabelecimento. Sem este filtro o modelo lia letreiro verdadeiro na foto
-- errada e aprovava o vizinho.
-- ---------------------------------------------------------------------
create table if not exists foto_maps_triagem (
  id             serial primary key,
  imagem_id      integer not null references images_urls(id) on delete cascade,
  poi_id         integer not null references pois(id) on delete cascade,
  tenant_id      uuid    references tenants(id),
  -- mostra_o_alvo | mostra_atividade_compativel | mostra_outro |
  -- indefinido | nao_e_estabelecimento
  veredito       text    not null,
  -- O texto que ele conseguiu LER na foto. É a evidência do veredito: quando
  -- diz `mostra_o_alvo`, isto aqui mostra com base em quê.
  texto_legivel  text,
  motivo         text,
  -- Repetida da `images_urls` de propósito: o peso da foto é RELATIVO à data
  -- da fachada, e a consulta que decide não deveria precisar de mais um join.
  data_imagem    text,
  modelo         text,
  schema_versao  text    not null default '2.0.0',
  criado_em      timestamp not null default now()
);

create index if not exists foto_maps_triagem_tenant_idx
  on foto_maps_triagem (tenant_id, veredito);
create index if not exists foto_maps_triagem_img_idx
  on foto_maps_triagem (imagem_id, id desc);
create index if not exists foto_maps_triagem_poi_idx
  on foto_maps_triagem (poi_id, id desc);

-- ---------------------------------------------------------------------
-- FASE 3 — o que a cena mostra, sobre `fachada_anotacao`
--
-- Colunas soltas só para o que a tela filtra ou ordena. O resto vai em jsonb:
-- filtro sobre jsonb varre a tabela, mas coluna que ninguém filtra é peso
-- morto em toda leitura.
-- ---------------------------------------------------------------------
alter table fachada_anotacao
  -- A descrição livre da cena, antes de qualquer campo estruturado. É ela que
  -- deixa auditar o veredito: se a descrição não sustenta a classificação, a
  -- classificação está errada — e é isso que o revisor por IA confere.
  add column if not exists descricao          text,
  -- sim | compativel | incerto | nao — a pergunta que precede todas as outras
  add column if not exists alvo_encontrado    text,
  -- achei_na_esquerda | achei_no_centro | achei_na_direita | nao_achei
  add column if not exists posicao_na_imagem  text,
  add column if not exists marcador_google_visivel boolean,
  add column if not exists tipo_imovel        text,
  add column if not exists status_ocupacao    text,
  add column if not exists multiplas_unidades boolean,
  add column if not exists limite_ambiguo     boolean,
  add column if not exists indicio_comercial  boolean,
  add column if not exists atividade_economica_aparente text,
  -- O texto do letreiro é o achado que o produto vende: é o nome REAL no
  -- imóvel, que pode divergir do nome no cadastro. Coluna própria porque a
  -- busca por nome vai bater aqui.
  add column if not exists texto_do_letreiro  text,
  -- aprovar | reprovar | revisar — a decisão, e só estas três.
  add column if not exists acao_recomendada   text,
  add column if not exists ressalva           text,
  add column if not exists elementos          jsonb,
  add column if not exists identificacao      jsonb,
  -- QUAIS imagens sustentaram esta leitura, com a data de cada uma. Sem isso
  -- não dá para responder "o veredito viu a foto de 2025 ou só o muro de
  -- 2018?", que é a pergunta que decide se o veredito vale.
  add column if not exists imagens_usadas     jsonb,
  -- FASE 4, preenchida sob demanda quando o supervisor abre o POI.
  add column if not exists deteccoes          jsonb,
  add column if not exists deteccoes_em       timestamp;

alter table fachada_anotacao drop constraint if exists acao_recomendada_valida;
alter table fachada_anotacao add constraint acao_recomendada_valida
  check (acao_recomendada is null
         or acao_recomendada in ('aprovar', 'reprovar', 'revisar'));

-- A fila do supervisor é exatamente esta consulta: do meu tenant, o que a IA
-- mandou revisar, mais recente primeiro.
create index if not exists fachada_acao_idx
  on fachada_anotacao (tenant_id, acao_recomendada, criado_em desc);
create index if not exists fachada_alvo_idx
  on fachada_anotacao (tenant_id, alvo_encontrado);

-- A LEITURA CORRENTE DE CADA POI.
--
-- A tabela é append-only: reler um ponto guarda a leitura anterior, e todo card
-- do painel precisa antes reduzir a tabela a "a mais recente por POI". Sem este
-- índice esse `DISTINCT ON (poi_id) ... ORDER BY poi_id, criado_em DESC` varre e
-- ordena a tabela inteira a cada abertura de tela — e ela cresce a cada
-- releitura da base, não a cada POI novo.
create index if not exists fachada_ultima_leitura_idx
  on fachada_anotacao (poi_id, criado_em desc, id desc);

-- E o antigo `ix_fachada_poi` sai: `(poi_id)` é PREFIXO ESTRITO do índice
-- acima, então todo plano que usava um usa o outro. Manter os dois só cobrava
-- uma escrita a mais em cada uma das 20 mil inserções da carga.
drop index if exists ix_fachada_poi;

-- ---------------------------------------------------------------------
-- As colunas de medição (`ucs_energia`, `hidrometros`, `medicao_*`,
-- `energia_entrada`) NÃO são removidas.
--
-- Elas param de ser escritas: contar medidor foi tentado em quatro
-- formulações e falhou em todas, e um campo que o modelo preenche sem
-- executar polui a base com negativa não verificada. Mas as 95 linhas
-- antigas têm valor histórico e apagar coluna é irreversível — ficam onde
-- estão, marcadas pelo `schema_versao` que as separa das novas.
-- ---------------------------------------------------------------------
