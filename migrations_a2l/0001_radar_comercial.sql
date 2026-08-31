-- 0001_radar_comercial.sql — o schema da ferramenta, no padrao A2L.
--
-- DE ONDE ELE SAIU. Nao e copia das 42 migrations do banco antigo: e o schema
-- montado do que o codigo de fato le e grava, com o tipo de cada coluna tirado
-- de cinco fontes independentes (docs/tipos_*.json) e a estrutura — quem tem
-- empresa, qual e a chave natural — tirada das migrations (docs/estrutura_*).
--
-- O cruzamento fechou em zero: das 22 tabelas que as migrations criavam, todas
-- as 22 sao usadas pelo codigo. Nada se perdeu ao comecar do zero.
--
-- O QUE MUDOU EM RELACAO AO BANCO ANTIGO, e por que:
--
--   tenant_id -> id_empresa   O `core` e a autoridade de identidade e chama
--                             assim. Dois nomes para a mesma coisa e o tipo de
--                             divergencia que so aparece num JOIN errado.
--   FORCE ROW LEVEL SECURITY  Vale inclusive para o DONO da tabela. Sem o
--                             FORCE, quem e dono atravessa a propria policy.
--   politicas via core.*      `core.eh_suporte() or id_empresa =
--                             core.empresa_atual()`, a mesma forma do `core`.
--                             Nao ha papel com BYPASSRLS neste banco: o root
--                             atravessa DENTRO da politica, nunca por papel.
--   id_empresa 1a no indice   RLS e avaliada POR LINHA. Indice que nao comeca
--                             por id_empresa nao serve a policy, e o
--                             isolamento vira o gargalo.
--
-- Rodar como `migrator` (A2L_MIGRATOR_URL), nunca como app_user.

set local search_path = radar_comercial, public;

-- ─────────────────────────────────────────────────────────────────────
-- Tipos proprios
-- ─────────────────────────────────────────────────────────────────────
--
-- Vem antes das tabelas porque as tabelas os usam. `if not exists` nao existe
-- para `create type`, entao o bloco `do $$ ... exception when duplicate_object`
-- e o que torna esta migracao repetivel.

-- Onde a triagem para: aprovado entra na base, reprovado sai, devolvido volta para quem mandou.
do $$ begin
  create type decisao_fila as enum ('pendente', 'aprovado', 'reprovado', 'devolvido');
exception when duplicate_object then null; end $$;

-- Por que reprovou.
-- E ENUM e nao texto livre porque estes sete sao contados em relatorio — texto livre viraria sete grafias do mesmo motivo.
do $$ begin
  create type motivo_reprova as enum ('fachada_residencial', 'endereco_divergente', 'comercio_encerrado', 'duplicado', 'evidencia_insuficiente', 'ja_e_comercial', 'outro');
exception when duplicate_object then null; end $$;

-- As abas da ficha.
-- Cada campo do catalogo pertence a uma.
do $$ begin
  create type fonte_aba as enum ('poi', 'google', 'receita', 'redes_sociais', 'delivery', 'imagens');
exception when duplicate_object then null; end $$;

-- Quanto um campo pesa no veredito comercial.
do $$ begin
  create type peso_evidencia as enum ('forte', 'media', 'neutra', 'contraria');
exception when duplicate_object then null; end $$;

-- Prioridade da visita de campo.
do $$ begin
  create type prioridade_campo as enum ('normal', 'alta');
exception when duplicate_object then null; end $$;

-- O que o agente vai fazer na visita: a placa existe e o comercio opera; o numero da porta bate com o cadastro; quantas lojas ha de fato no imovel; nao ha imagem ou a que ha esta velha; a coordenada esta imprecisa; mais de um endereco plausivel.
do $$ begin
  create type pauta_campo as enum ('confirmar_atividade', 'confirmar_numero', 'contar_unidades', 'fotografar_fachada', 'registrar_coordenada', 'confirmar_endereco');
exception when duplicate_object then null; end $$;


-- ─────────────────────────────────────────────────────────────────────
-- Tabelas
-- ─────────────────────────────────────────────────────────────────────

create table if not exists pois (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  avaliacao                  text,
  categoria                  text,
  cidade                     text,
  cnae                       text,
  cnpj                       character varying(20),
  cnpj_conf                  real,
  coord_compartilhada        boolean,
  coord_fonte                text,
  coord_grupo                integer,
  coord_incerteza_m          integer,
  coord_precisao             text,
  criado_em                  timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
  cruzado_em                 timestamptz,
  descoberto_de              bigint,
  distancia_m                double precision,
  email                      text,
  endereco                   text,
  endereco_fonte             character varying(12),
  endereco_gerado_por        text,
  endereco_original          text,
  facebook                   text,
  fonte                      text NOT NULL,
  fonte_dado                 text,
  fontes_web                 text,
  fundido_em                 timestamptz,
  fundido_para               bigint,
  fundido_por                text,
  ia_resposta                text,
  ifood_visto_em             timestamptz,
  instagram                  text,
  lat_origem                 double precision,
  lng_origem                 double precision,
  maps_lat                   double precision,
  maps_lng                   double precision,
  maps_url                   text,
  match_valido               boolean,
  multiloja                  boolean not null default false,
  natureza_juridica          text,
  nome                       text NOT NULL,
  nome_fantasia              text,
  nome_original              text,
  ocr_texto                  text,
  place_id                   text,
  plus_code                  text,
  preco_medio                text,
  presente_no_ifood          boolean not null default false,
  razao_social               text,
  resumo_avaliacoes          text,
  revisar_manual             boolean,
  revisar_motivo             text,
  sessao                     text,
  similaridade               double precision,
  situacao_cadastral         text,
  socios                     text,
  status                     text,
  status_horario             text,
  streetview_path            text,
  telefone                   text,
  total_avaliacoes           integer,
  uf                         text,
  website                    text
);

create table if not exists images_urls (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  bytes_tam                  integer,
  content_type               character varying(40),
  dados                      bytea,
  data_imagem                character varying(10),
  ordem                      integer,
  poi_id                     bigint NOT NULL,
  storage_path               text,
  url                        text NOT NULL
);

create table if not exists streetview_imgs (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  angulo                     double precision,
  bytes_tam                  integer,
  cam_lat                    double precision,
  cam_lng                    double precision,
  criado_em                  timestamptz DEFAULT now(),
  dados                      bytea,
  data_captura               character varying(7),
  fov                        integer,
  heading                    double precision,
  lat                        double precision,
  lng                        double precision,
  pano_id                    character varying(40),
  poi_id                     bigint,
  storage_path               text
);

create table if not exists comentarios (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  autor                      text,
  data                       text,
  nota                       double precision,
  poi_id                     bigint NOT NULL,
  texto                      text
);

create table if not exists horario_funcionamento (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  dia                        text NOT NULL,
  horario                    text,
  poi_id                     bigint NOT NULL
);

create table if not exists analise_ia (
  -- sem `id` sintetico: a identidade e `poi_id`,
  poi_id                     bigint not null,
  id_empresa                 uuid not null references core.tb_empresas(id),
  atividade_real             text,
  confere                    boolean,
  criado_em                  timestamptz,
  equivalencia               text,
  imagem_fonte               text,
  modelo                     text,
  motivo                     text,
  n_imagens                  integer,
  outro_estabelecimento      text,
  pessoas_estimadas          text,
  porte                      text,
  recomendacao_motivo        text,
  recomendar_visita          boolean,
  resposta_json              jsonb,
  tipo_construcao            text,
  veredito                   text,
  veredito_motivo            text
);
alter table analise_ia add constraint pk_analise_ia primary key (poi_id);

create table if not exists fachada_anotacao (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  acao_recomendada           text,
  alvo_encontrado            text,
  anotacao                   jsonb,
  apta                       boolean,
  atividade_economica_aparente text,
  atividade_no_alvo          text,
  comercio_divergente        text,
  comercios_encontrados      jsonb,
  confianca                  real,
  criado_em                  timestamptz DEFAULT now(),
  custo_usd                  numeric(10,6),
  descricao                  text,
  deteccoes                  jsonb,
  deteccoes_em               timestamptz,
  e_imovel                   boolean,
  economias_base             integer,
  elementos                  jsonb,
  energia_entrada            text,
  estado_conservacao         text,
  gap_uc_economias           integer,
  habitacoes_distintas       integer,
  hidrometros                integer,
  identificacao              jsonb,
  imagens_usadas             jsonb,
  indicio_comercial          boolean,
  limite_ambiguo             boolean,
  marcador_google_visivel    boolean,
  medicao_abrigo             text,
  medicao_acesso             text,
  medicao_coletiva           boolean,
  medicao_desc               text,
  medicao_estado             text,
  medicao_posicao            text,
  metodo_habitacoes          text,
  modelo                     text,
  multiplas_unidades         boolean,
  nota_comercial             smallint,
  numero_confere             boolean,
  numero_lido                text,
  numero_na_parede           text,
  oportunidades              jsonb,
  padrao_construtivo         text,
  pavimentos                 integer,
  pessoas_na_imagem          text,
  poi_id                     bigint NOT NULL,
  posicao_na_imagem          text,
  ressalva                   text,
  schema_versao              text,
  status                     text,
  status_ocupacao            text,
  texto_do_letreiro          text,
  tipo_cliente               text,
  tipo_edificacao            text,
  tipo_imovel                text,
  tipo_via                   text,
  tipologia                  text,
  tokens_in                  integer,
  tokens_out                 integer,
  ucs_energia                integer,
  unidades_fisicas           integer,
  uso_observado              text,
  validacao                  jsonb,
  veredito_comercial         text,
  veredito_fatores           jsonb,
  veredito_justificativa     text
);

create table if not exists fachada_triagem (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  angulo                     text,
  criado_em                  timestamptz not null default now(),
  modelo                     text,
  motivo                     text,
  nota_total                 smallint,
  notas                      jsonb not null,
  poi_id                     bigint not null,
  schema_versao              text not null default '2.0.0',
  sv_id                      integer not null,
  tipo_de_foto               text not null,
  veredito                   text not null
);

create table if not exists foto_maps_triagem (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  criado_em                  timestamptz not null default now(),
  data_imagem                text,
  imagem_id                  integer not null,
  modelo                     text,
  motivo                     text,
  poi_id                     bigint not null,
  schema_versao              text not null default '2.0.0',
  texto_legivel              text,
  veredito                   text not null
);

create table if not exists cadastro_cliente (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  bacia                      text,
  bairro                     text,
  categoria                  text,
  cep                        text,
  cidade                     text,
  classe_agua                text,
  classe_esgoto              text,
  classificacao              text,
  cliente                    text NOT NULL DEFAULT 'corsan',
  cod_categoria              text,
  cod_classe_agua            integer,
  cod_classe_esgoto          integer,
  cod_comunidade             text,
  cod_distrito               text,
  cod_faturamento            integer,
  cod_grupo                  integer,
  cod_rota_leitura           integer,
  cod_setor_comercial        integer,
  cod_tipo_consumidor        integer,
  cod_tipo_medicao           integer,
  complemento                text,
  comunidade                 text,
  cruz_conf                  text,
  cruz_dist_m                double precision,
  cruz_flag                  text,
  cruz_motivo                text,
  data_ativacao              date,
  data_encerramento          date,
  data_instalacao            date,
  dica_localizacao           text,
  e_comercial                boolean,
  economias_com              integer,
  economias_ind              integer,
  economias_out              integer,
  economias_pub              integer,
  economias_res              integer,
  empresa_codigo             integer,
  endereco                   text,
  endereco_origem            text,
  flag_distrito              text,
  flag_tarifa_minima         text,
  flag_val                   text,
  flag_vcg                   text,
  importado_em               timestamptz DEFAULT now(),
  lat                        double precision,
  lng                        double precision,
  logradouro                 text,
  num_ligacao                bigint,
  num_lote                   integer,
  num_medidor                text,
  num_medidor_master         text,
  num_quadra                 integer,
  numero                     text,
  perfil                     text,
  poi_id                     bigint,
  programa                   text,
  referencia                 date,
  remessa                    text,
  segmento                   text,
  seq_rota                   integer,
  situacao_contrato          text,
  situacao_ligacao           text,
  spe                        text,
  sta_area_risco             text,
  sta_comunidade             text,
  sta_telemetria             text,
  sub_bacia                  text,
  subcategoria               text,
  tipo_consumidor            text,
  tipo_entrega               text,
  tipo_faturamento           text,
  tipo_fonte                 text,
  tipo_ligacao               text,
  tipo_medicao               text,
  uf                         text,
  ultimo_contrato            text,
  utilizacao                 text,
  vol_caixa                  double precision,
  vol_cisterna               double precision,
  vol_piscina                double precision,
  zona_abastecimento         text,
  zona_ligacao               text
);

create table if not exists area_trabalho (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  nome                       text,
  polygon                    jsonb NOT NULL,
  salvo_em                   timestamptz DEFAULT now()
);

create table if not exists fonte_arquivos (
  id_empresa                 uuid not null references core.tb_empresas(id),
  bytes                      bigint,
  carregado_em               timestamptz DEFAULT now(),
  fonte                      text NOT NULL,
  linhas                     bigint,
  referencia                 text NOT NULL,
  status                     text DEFAULT 'ok',
  tabela                     text
);

create table if not exists atribuicao (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  atribuido_em               timestamptz not null default now(),
  atribuido_por              uuid not null,
  decidido_em                timestamptz,
  motivo_escrito             text,
  motivo_generico            motivo_reprova,
  observacao                 text,
  pauta                      pauta_campo[],
  pauta_porque               jsonb,
  poi_id                     bigint not null,
  prioridade                 prioridade_campo not null default 'normal',
  revisao                    jsonb,
  status                     decisao_fila not null default 'pendente',
  supervisor_id              uuid not null
);

create table if not exists atribuicao_divergente (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  anotacao_id                integer,
  atividade                  text,
  atribuido_em               timestamptz not null default now(),
  decidido_em                timestamptz,
  motivo_escrito             text,
  motivo_generico            text,
  nome_lido                  text,
  observacao                 text,
  poi_id                     bigint not null,
  status                     text not null default 'pendente',
  supervisor_id              uuid
);

create table if not exists cnpj_tratado (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  aptidao_geo                text,
  cnae                       text,
  cnpj                       text not null,
  cod_municipio              text,
  criado_em                  timestamptz not null default now(),
  evidencia                  text,
  incerteza_m                integer,
  lat                        double precision,
  lng                        double precision,
  nome_fantasia              text,
  perfil_comercial           text,
  poi_id                     bigint,
  potencial                  text,
  razao_social               text,
  rota                       text,
  situacao                   text
);

create table if not exists cadastur_prestador (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  atividade_turistica        text,
  categorias                 text,
  cnae                       text,
  cnpj                       text,
  cod_municipio              text,
  criado_em                  timestamptz not null default now(),
  cruzado_em                 timestamptz,
  dataset                    text not null,
  email                      text,
  endereco_comercial         text,
  endereco_rfb               text,
  extraido_em                timestamptz,
  extras                     jsonb not null default '{}'::jsonb,
  leitos                     integer,
  leitos_texto               text,
  linha_origem               integer not null,
  municipio                  text,
  natureza_juridica          text,
  nome_fantasia              text,
  numero_certificado         text,
  poi_id                     bigint,
  porte                      text,
  razao_social               text,
  recurso_id                 text not null,
  recurso_nome               text,
  ref_periodo                date,
  saiu_em                    date,
  sem_poi_motivo             text,
  sha256                     text,
  situacao_atividade         text,
  situacao_cadastral         text,
  telefone                   text,
  tipo_hospedagem            text,
  uf                         text,
  uh                         integer,
  uh_texto                   text,
  validade                   date,
  validade_texto             text,
  visto_em                   date,
  website                    text
);

create table if not exists cadastur_total_pf (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  atividade                  text,
  atualizado_em              timestamptz not null default now(),
  dataset                    text not null,
  municipio                  text not null,
  quantidade                 integer not null,
  ref_periodo                date not null,
  uf                         text not null
);

create table if not exists campo_catalogo (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  ajuda                      text,
  ativo                      boolean not null default true,
  chave                      text not null,
  criado_em                  timestamptz not null default now(),
  fonte                      fonte_aba not null,
  formato                    text not null default 'texto',
  grupo                      text,
  ordem                      integer not null default 100,
  peso                       peso_evidencia not null default 'neutra',
  procedencia                text,
  rotulo                     text not null
);

create table if not exists chat_conversa (
  id                         uuid default gen_random_uuid() primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  arquivada                  boolean not null default false,
  criada_em                  timestamptz not null default now(),
  mexida_em                  timestamptz not null default now(),
  titulo                     text not null default 'Nova conversa',
  usuario_id                 uuid
);

create table if not exists chat_mensagem (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  conteudo                   text not null default '',
  conversa_id                uuid not null,
  criada_em                  timestamptz not null default now(),
  dados                      jsonb,
  ferramenta                 text,
  papel                      text not null
);

create table if not exists chat_anexo (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  bytes                      integer not null default 0,
  caminho                    text,
  conversa_id                uuid not null,
  criado_em                  timestamptz not null default now(),
  extraido                   text,
  mensagem_id                bigint,
  meta                       jsonb not null default '{}'::jsonb,
  nome                       text not null,
  tipo                       text not null
);

create table if not exists cruzamento (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  ambiguo                    boolean not null default false,
  base_a                     text not null,
  base_b                     text not null,
  chave                      text not null,
  concorrentes               integer not null default 0,
  criado_em                  timestamptz not null default now(),
  distancia_m                numeric(8,1),
  evidencia                  jsonb not null default '{}'::jsonb,
  id_a                       text not null,
  id_b                       text not null,
  score                      numeric(4,3) not null
);

create table if not exists memoria (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  chaves                     text[] not null default '{}',
  conversa_id                uuid,
  criado_em                  timestamptz not null default now(),
  detalhe                    text,
  resumo                     text not null,
  tipo                       text not null default 'fato',
  usado_em                   timestamptz,
  vezes_usada                integer not null default 0
);

create table if not exists vinculo_poi (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  confianca                  smallint not null check (confianca between 1 and 10),
  confianca_origem           text not null,
  criado_em                  timestamptz not null default now(),
  dados                      jsonb not null default '{}'::jsonb,
  desvinculado_em            timestamptz,
  desvinculado_para          integer,
  desvinculado_por           text,
  estado                     text not null default 'vinculado' check (estado in ('vinculado', 'desvinculado')),
  fonte                      text not null,
  id_fonte                   text not null,
  lat                        double precision,
  lng                        double precision,
  modelo                     text,
  motivo                     text,
  nome                       text,
  poi_id                     bigint not null
);

create table if not exists ifood_merchant (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  avaliacoes                 integer,
  bairro                     text,
  bruto                      jsonb,
  categoria                  text,
  cep                        text,
  cidade                     text,
  cnpj                       text,
  estado_detalhe             text,
  lat                        double precision,
  lng                        double precision,
  merchant_id                text,
  nome                       text,
  nota                       numeric,
  numero                     text,
  poi_id                     bigint,
  rua                        text,
  slug                       text,
  telefone                   text,
  uf                         text,
  visto_em                   timestamptz not null default now()
);

-- SEM id_empresa DE PROPOSITO: o IP e infraestrutura compartilhada; quem consumiu esta em proxy_evento, que tem empresa
create table if not exists proxy_ip (
  id                         text primary key,
  ativo                      boolean NOT NULL DEFAULT true,
  cidade                     text,
  endereco                   text NOT NULL,
  pais                       text,
  porta                      integer NOT NULL,
  visto_em                   timestamptz NOT NULL DEFAULT now()
);

create table if not exists proxy_evento (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  bytes                      bigint,
  em                         timestamptz NOT NULL DEFAULT now(),
  etapa                      text,
  motivo                     text,
  proxy_id                   text NOT NULL,
  segundos                   integer,
  tipo                       text NOT NULL
);

create table if not exists cnefe_coletiva (
  id                         bigint generated always as identity primary key,
  id_empresa                 uuid not null references core.tb_empresas(id),
  acuracia_m                 real,
  atividades                 text,
  cod_municipio              text NOT NULL,
  coletiva_id                text NOT NULL,
  com_atividade              integer,
  economias_cnefe            integer,
  forma                      text,
  importado_em               timestamptz DEFAULT now(),
  lat                        double precision,
  lng                        double precision,
  localidade                 text,
  logradouro                 text,
  numero                     integer,
  poi_dist_m                 real,
  poi_id                     bigint,
  qtd_blocos                 integer,
  qtd_inferida               integer,
  qtd_observada              integer,
  recomendacao               text,
  unidades                   integer,
  uso                        text,
  veredito                   text
);


-- ─────────────────────────────────────────────────────────────────────
-- Isolamento por empresa
-- ─────────────────────────────────────────────────────────────────────
--
-- `core.eh_suporte()` e `core.empresa_atual()` sao STABLE SECURITY DEFINER e
-- leem `(select auth.uid())` em subselect — o planejador as chama uma vez por
-- consulta, e nao uma vez por linha.

alter table analise_ia enable row level security;
alter table analise_ia force  row level security;
create policy p_analise_ia on analise_ia for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table area_trabalho enable row level security;
alter table area_trabalho force  row level security;
create policy p_area_trabalho on area_trabalho for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table atribuicao enable row level security;
alter table atribuicao force  row level security;
create policy p_atribuicao on atribuicao for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table atribuicao_divergente enable row level security;
alter table atribuicao_divergente force  row level security;
create policy p_atribuicao_divergente on atribuicao_divergente for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cadastro_cliente enable row level security;
alter table cadastro_cliente force  row level security;
create policy p_cadastro_cliente on cadastro_cliente for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cadastur_prestador enable row level security;
alter table cadastur_prestador force  row level security;
create policy p_cadastur_prestador on cadastur_prestador for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cadastur_total_pf enable row level security;
alter table cadastur_total_pf force  row level security;
create policy p_cadastur_total_pf on cadastur_total_pf for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table campo_catalogo enable row level security;
alter table campo_catalogo force  row level security;
create policy p_campo_catalogo on campo_catalogo for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table chat_anexo enable row level security;
alter table chat_anexo force  row level security;
create policy p_chat_anexo on chat_anexo for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table chat_conversa enable row level security;
alter table chat_conversa force  row level security;
create policy p_chat_conversa on chat_conversa for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table chat_mensagem enable row level security;
alter table chat_mensagem force  row level security;
create policy p_chat_mensagem on chat_mensagem for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cnefe_coletiva enable row level security;
alter table cnefe_coletiva force  row level security;
create policy p_cnefe_coletiva on cnefe_coletiva for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cnpj_tratado enable row level security;
alter table cnpj_tratado force  row level security;
create policy p_cnpj_tratado on cnpj_tratado for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table comentarios enable row level security;
alter table comentarios force  row level security;
create policy p_comentarios on comentarios for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table cruzamento enable row level security;
alter table cruzamento force  row level security;
create policy p_cruzamento on cruzamento for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table fachada_anotacao enable row level security;
alter table fachada_anotacao force  row level security;
create policy p_fachada_anotacao on fachada_anotacao for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table fachada_triagem enable row level security;
alter table fachada_triagem force  row level security;
create policy p_fachada_triagem on fachada_triagem for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table fonte_arquivos enable row level security;
alter table fonte_arquivos force  row level security;
create policy p_fonte_arquivos on fonte_arquivos for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table foto_maps_triagem enable row level security;
alter table foto_maps_triagem force  row level security;
create policy p_foto_maps_triagem on foto_maps_triagem for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table horario_funcionamento enable row level security;
alter table horario_funcionamento force  row level security;
create policy p_horario_funcionamento on horario_funcionamento for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table ifood_merchant enable row level security;
alter table ifood_merchant force  row level security;
create policy p_ifood_merchant on ifood_merchant for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table images_urls enable row level security;
alter table images_urls force  row level security;
create policy p_images_urls on images_urls for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table memoria enable row level security;
alter table memoria force  row level security;
create policy p_memoria on memoria for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table pois enable row level security;
alter table pois force  row level security;
create policy p_pois on pois for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table proxy_evento enable row level security;
alter table proxy_evento force  row level security;
create policy p_proxy_evento on proxy_evento for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table streetview_imgs enable row level security;
alter table streetview_imgs force  row level security;
create policy p_streetview_imgs on streetview_imgs for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());

alter table vinculo_poi enable row level security;
alter table vinculo_poi force  row level security;
create policy p_vinculo_poi on vinculo_poi for all
  using      (core.eh_suporte() or id_empresa = core.empresa_atual())
  with check (core.eh_suporte() or id_empresa = core.empresa_atual());


-- ─────────────────────────────────────────────────────────────────────
-- Quem carimba a empresa
-- ─────────────────────────────────────────────────────────────────────
--
-- POR GATILHO, e nao por codigo de rota. Sao mais de trinta INSERTs espalhados
-- pelo pipeline e pela API; exigir `id_empresa` em cada um significa que o
-- primeiro esquecido grava linha sem dono. E linha sem dono NAO some: ela nasce
-- invisivel para todo mundo, e ninguem procura o que nao sabe que perdeu.
--
-- Variavel ausente deixa NULL, e o `not null` recusa. Falha barulhenta e melhor
-- que linha orfa: o erro aparece no primeiro insert, junto da causa.

create or replace function preencher_empresa() returns trigger
language plpgsql as $$
begin
  if new.id_empresa is null then
    new.id_empresa := core.empresa_atual();
  end if;
  return new;
end $$;

comment on function preencher_empresa() is
  'Carimba id_empresa a partir de core.empresa_atual(), que resolve pelo uuid '
  'do usuario em request.jwt.claim.sub. Ausente deixa NULL e o NOT NULL recusa.';

create trigger trg_empresa_analise_ia before insert on analise_ia
  for each row execute function preencher_empresa();
create trigger trg_empresa_area_trabalho before insert on area_trabalho
  for each row execute function preencher_empresa();
create trigger trg_empresa_atribuicao before insert on atribuicao
  for each row execute function preencher_empresa();
create trigger trg_empresa_atribuicao_divergente before insert on atribuicao_divergente
  for each row execute function preencher_empresa();
create trigger trg_empresa_cadastro_cliente before insert on cadastro_cliente
  for each row execute function preencher_empresa();
create trigger trg_empresa_cadastur_prestador before insert on cadastur_prestador
  for each row execute function preencher_empresa();
create trigger trg_empresa_cadastur_total_pf before insert on cadastur_total_pf
  for each row execute function preencher_empresa();
create trigger trg_empresa_campo_catalogo before insert on campo_catalogo
  for each row execute function preencher_empresa();
create trigger trg_empresa_chat_anexo before insert on chat_anexo
  for each row execute function preencher_empresa();
create trigger trg_empresa_chat_conversa before insert on chat_conversa
  for each row execute function preencher_empresa();
create trigger trg_empresa_chat_mensagem before insert on chat_mensagem
  for each row execute function preencher_empresa();
create trigger trg_empresa_cnefe_coletiva before insert on cnefe_coletiva
  for each row execute function preencher_empresa();
create trigger trg_empresa_cnpj_tratado before insert on cnpj_tratado
  for each row execute function preencher_empresa();
create trigger trg_empresa_comentarios before insert on comentarios
  for each row execute function preencher_empresa();
create trigger trg_empresa_cruzamento before insert on cruzamento
  for each row execute function preencher_empresa();
create trigger trg_empresa_fachada_anotacao before insert on fachada_anotacao
  for each row execute function preencher_empresa();
create trigger trg_empresa_fachada_triagem before insert on fachada_triagem
  for each row execute function preencher_empresa();
create trigger trg_empresa_fonte_arquivos before insert on fonte_arquivos
  for each row execute function preencher_empresa();
create trigger trg_empresa_foto_maps_triagem before insert on foto_maps_triagem
  for each row execute function preencher_empresa();
create trigger trg_empresa_horario_funcionamento before insert on horario_funcionamento
  for each row execute function preencher_empresa();
create trigger trg_empresa_ifood_merchant before insert on ifood_merchant
  for each row execute function preencher_empresa();
create trigger trg_empresa_images_urls before insert on images_urls
  for each row execute function preencher_empresa();
create trigger trg_empresa_memoria before insert on memoria
  for each row execute function preencher_empresa();
create trigger trg_empresa_pois before insert on pois
  for each row execute function preencher_empresa();
create trigger trg_empresa_proxy_evento before insert on proxy_evento
  for each row execute function preencher_empresa();
create trigger trg_empresa_streetview_imgs before insert on streetview_imgs
  for each row execute function preencher_empresa();
create trigger trg_empresa_vinculo_poi before insert on vinculo_poi
  for each row execute function preencher_empresa();


-- ─────────────────────────────────────────────────────────────────────
-- Auditoria — quem FEZ, e nao quem PODE
-- ─────────────────────────────────────────────────────────────────────
--
-- Reusa `core.registrar_auditoria()`, que ja escreve em `core.tb_auditoria`
-- (particionada por mes) e ja e usada pelas oito tabelas do `core`. Escrever
-- uma segunda funcao de auditoria daria duas tabelas de log com formatos
-- diferentes para a mesma pergunta.
--
-- POR GATILHO, e nao por decorator: rota esquecida nao escapa do registro — foi
-- assim que 29 rotas ficaram sem autenticacao e ninguem percebeu por semanas —
-- e o pipeline, que nunca passaria por um decorator de FastAPI, tambem entra.
--
-- `pois` FICA DE FORA DO INSERT, DE PROPOSITO. O pipeline grava dezenas de
-- milhares por rodada; auditar isso encheria o log de ruido e esconderia
-- justamente o evento raro que se quer achar. O DELETE de POI, esse sim, e
-- registrado: apagar e o que ninguem deveria fazer sem deixar rastro.

create trigger tg_auditoria after insert or update or delete on atribuicao
  for each row execute function core.registrar_auditoria();

create trigger tg_auditoria_del after delete on pois
  for each row execute function core.registrar_auditoria();


-- ─────────────────────────────────────────────────────────────────────
-- Chaves naturais — o que impede a mesma coisa de entrar duas vezes
-- ─────────────────────────────────────────────────────────────────────

-- O mesmo lugar pode existir para duas empresas — cada uma minerou o seu.
-- A unicidade e POR EMPRESA.
create unique index if not exists ux_pois_place_id_por_empresa
  on pois (id_empresa, place_id)
  where place_id is not null and place_id <> '';

-- Nome+endereco iguais na mesma empresa e a mesma coisa minerada duas vezes.
-- POI ja fundido sai do indice: ele existe so como historico.
create unique index if not exists pois_sem_duplicata
  on pois (id_empresa, upper(trim(nome)), upper(trim(endereco)))
  where fundido_em is null;

-- A mesma vista do mesmo ponto nao entra duas vezes.
create unique index if not exists ux_streetview_poi_angulo
  on streetview_imgs (id_empresa, poi_id, angulo)
  where pano_id is not null and angulo is not null;

-- Um par de registros so e cruzado uma vez por chave.
create unique index if not exists ux_cruzamento_par
  on cruzamento (id_empresa, base_a, id_a, base_b, id_b, chave);

-- Um registro de fonte compoe UM poi por vez.
-- Desvinculado sai do indice e pode ser vinculado a outro.
create unique index if not exists vinculo_sem_duplicata
  on vinculo_poi (id_empresa, fonte, id_fonte)
  where estado = 'vinculado';

-- Um poi nao e atribuido duas vezes ao mesmo supervisor.
create unique index if not exists atribuicao_unica
  on atribuicao (id_empresa, poi_id, supervisor_id);

-- So uma divergencia pendente por poi; as resolvidas ficam como historico.
create unique index if not exists atribuicao_div_pendente_unico
  on atribuicao_divergente (id_empresa, poi_id)
  where status = 'pendente';


-- ─────────────────────────────────────────────────────────────────────
-- Indices de leitura
-- ─────────────────────────────────────────────────────────────────────
--
-- SAO OS INDICES QUE JA EXISTIAM, e nao um por tabela.
--
-- A primeira versao deste gerador emitia UM indice por tabela — `(id_empresa)`
-- e mais nada — e teria jogado fora 78 indices construidos para consultas
-- especificas ao longo de 42 migracoes. Nenhum teste falharia por isso: o
-- sistema funcionaria, so que varrendo. O sintoma apareceria como "o painel
-- ficou lento" meses depois, longe de qualquer commit.
--
-- Cada um foi reescrito com `id_empresa` no lugar de `tenant_id`. Politica de
-- RLS e avaliada por linha, entao indice que nao comeca pela coluna da empresa
-- nao serve a ela — e os que ja comecavam por `tenant_id` continuam servindo.

create index if not exists pois_empresa_idx on pois (id_empresa, id);
create index if not exists cadastro_cliente_empresa_idx on cadastro_cliente (id_empresa, id);
create index if not exists area_trabalho_empresa_idx on area_trabalho (id_empresa, salvo_em desc);
create index if not exists fachada_anotacao_empresa_idx on fachada_anotacao (id_empresa, poi_id);
create index if not exists analise_ia_empresa_idx on analise_ia (id_empresa, poi_id);
create index if not exists fonte_arquivos_empresa_idx on fonte_arquivos (id_empresa, carregado_em desc);
create index if not exists comentarios_empresa_idx on comentarios (id_empresa, poi_id);
create index if not exists horario_empresa_idx on horario_funcionamento (id_empresa, poi_id);
create index if not exists images_urls_empresa_idx on images_urls (id_empresa, poi_id);
create index if not exists streetview_empresa_idx on streetview_imgs (id_empresa, poi_id);
create index if not exists cnefe_coletiva_empresa_idx on cnefe_coletiva (id_empresa, id);
create index if not exists atribuicao_empresa_idx on atribuicao (id_empresa, status);
create index if not exists atribuicao_supervisor_idx on atribuicao (supervisor_id, poi_id);
create index if not exists cnpj_tratado_empresa_idx on cnpj_tratado (id_empresa, potencial);
create index if not exists cnpj_tratado_cnpj_idx on cnpj_tratado (id_empresa, cnpj);
create index if not exists cnpj_tratado_poi_idx on cnpj_tratado (id_empresa, poi_id);
create index if not exists fachada_veredito_idx on fachada_anotacao (id_empresa, veredito_comercial, nota_comercial desc);
create index if not exists fachada_tipo_cliente_idx on fachada_anotacao (id_empresa, tipo_cliente);
create index if not exists ix_sv_com_camera on streetview_imgs (poi_id)
  WHERE cam_lat IS NOT NULL AND heading IS NOT NULL;
create index if not exists fachada_triagem_empresa_idx on fachada_triagem (id_empresa, veredito, nota_total desc);
create index if not exists fachada_triagem_sv_idx on fachada_triagem (sv_id, id desc);
create index if not exists fachada_triagem_poi_idx on fachada_triagem (poi_id, id desc);
create index if not exists foto_maps_triagem_empresa_idx on foto_maps_triagem (id_empresa, veredito);
create index if not exists foto_maps_triagem_img_idx on foto_maps_triagem (imagem_id, id desc);
create index if not exists foto_maps_triagem_poi_idx on foto_maps_triagem (poi_id, id desc);
create index if not exists fachada_acao_idx on fachada_anotacao (id_empresa, acao_recomendada, criado_em desc);
create index if not exists fachada_alvo_idx on fachada_anotacao (id_empresa, alvo_encontrado);
create index if not exists fachada_ultima_leitura_idx on fachada_anotacao (poi_id, criado_em desc, id desc);
create index if not exists fachada_divergente_idx on fachada_anotacao (id_empresa, comercio_divergente)
  where comercio_divergente is not null;
create index if not exists atribuicao_div_fila_idx on atribuicao_divergente (id_empresa, status, atribuido_em desc);
create index if not exists atribuicao_div_sup_idx on atribuicao_divergente (supervisor_id, status);
create index if not exists ifood_estado_idx on ifood_merchant (estado_detalhe);
create index if not exists ifood_cnpj_idx on ifood_merchant (cnpj)
  where cnpj is not null and cnpj <> '';
create index if not exists ifood_poi_idx on ifood_merchant (poi_id)
  where poi_id is not null;
create index if not exists ifood_geo_idx on ifood_merchant (lat, lng)
  where lat is not null;
create index if not exists ix_cruzamento_a on cruzamento (id_empresa, base_a, id_a);
create index if not exists ix_cruzamento_b on cruzamento (id_empresa, base_b, id_b);
create index if not exists ix_cruzamento_revisar on cruzamento (id_empresa, ambiguo)
  where ambiguo;
create index if not exists ix_pois_ifood on pois (id_empresa, presente_no_ifood)
  where presente_no_ifood;
create index if not exists ix_ifood_cidade on ifood_merchant (id_empresa, cidade);
create index if not exists ix_chat_conversa_recente on chat_conversa (id_empresa, mexida_em desc)
  where not arquivada;
create index if not exists ix_chat_mensagem_conversa on chat_mensagem (id_empresa, conversa_id, id);
create index if not exists ix_chat_anexo_conversa on chat_anexo (id_empresa, conversa_id, id);
create index if not exists ix_memoria_recente on memoria (id_empresa, criado_em desc);
create index if not exists ix_campo_catalogo_aba on campo_catalogo (id_empresa, fonte, ordem)
  where ativo;
create index if not exists ix_cadastur_municipio on cadastur_prestador (id_empresa, uf, municipio);
create index if not exists ix_cadastur_cnpj on cadastur_prestador (id_empresa, cnpj)
  where cnpj is not null;
create index if not exists ix_cadastur_pendente on cadastur_prestador (id_empresa, uf, municipio)
  where poi_id is null and sem_poi_motivo is null;
create index if not exists ix_cadastur_poi on cadastur_prestador (id_empresa, poi_id)
  where poi_id is not null;
create index if not exists ix_cadastur_saiu on cadastur_prestador (id_empresa, saiu_em)
  where saiu_em is not null;
create index if not exists ix_cadastur_total_pf_lugar on cadastur_total_pf (id_empresa, uf, municipio, ref_periodo desc);
create index if not exists ix_pois_coord_precisao on pois (id_empresa, coord_precisao);
create index if not exists ix_pois_coord_a_buscar on pois (id_empresa, cidade)
  where coord_precisao in ('desconhecida', 'via', 'bairro', 'municipio');
create index if not exists ix_atribuicao_div_empresa on atribuicao_divergente (id_empresa);
create index if not exists ix_ifood_merchant_empresa on ifood_merchant (id_empresa, cidade);
create index if not exists ix_vinculo_poi_ficha on vinculo_poi (id_empresa, poi_id, estado);
create index if not exists ix_vinculo_poi_duvidoso on vinculo_poi (id_empresa, confianca)
  where estado = 'vinculado' and confianca <= 5;
create index if not exists pois_ativos_por_empresa on pois (id_empresa, id)
  where fundido_em is null;
create index if not exists pois_multiloja_por_empresa on pois (id_empresa, id)
  where multiloja;
create index if not exists pois_coord_por_empresa on pois (id_empresa, (COALESCE(maps_lat, lat_origem)), (COALESCE(maps_lng, lng_origem)))
  WHERE fundido_em IS NULL AND COALESCE(maps_lat, lat_origem) IS NOT NULL AND COALESCE(maps_lng, lng_origem) IS NOT NULL;
create index if not exists ix_cad_geo_por_empresa on cadastro_cliente (id_empresa, lat, lng)
  WHERE lat IS NOT NULL AND lng IS NOT NULL;
create index if not exists ix_proxy_ip_pais on proxy_ip (pais, ativo);
create index if not exists ix_proxy_evento_empresa on proxy_evento (id_empresa, em DESC);
create index if not exists ix_proxy_evento_proxy on proxy_evento (proxy_id, em DESC);
create index if not exists ix_proxy_evento_castigo on proxy_evento (em DESC)
  WHERE tipo = 'castigo';


-- Permissoes: `app_user` usa, nao possui. Sem isto ele enxerga o schema e nao
-- enxerga tabela nenhuma, com erro que fala de relacao inexistente.
grant usage on schema radar_comercial to app_user, readonly;
grant select, insert, update, delete on all tables in schema radar_comercial to app_user;
grant select on all tables in schema radar_comercial to readonly;
alter default privileges in schema radar_comercial
  grant select, insert, update, delete on tables to app_user;
alter default privileges in schema radar_comercial
  grant select on tables to readonly;
