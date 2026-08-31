-- 0002_resources_root.sql — base publica e cache compartilhado.
--
-- ESTE ARQUIVO MEXE EM INFRAESTRUTURA COMPARTILHADA. `resources_root` e lido
-- por todas as ferramentas do A2L; mudanca aqui afeta radarTelhados,
-- radarColetivas e o resto. Nao rodar sem combinar.
--
-- Dono do schema: `resources_loader` — nao `migrator`. Rodar como o dono, ou
-- conceder antes. Ver o rodape.
--
-- SEM RLS, e de proposito: base publica nao tem de quem esconder. O CNPJ da
-- Receita e o CNEFE do IBGE sao os mesmos para toda empresa, e por isso a
-- leitura e livre e a escrita e so de processo.
--
-- AS DUAS ULTIMAS TABELAS CHEGARAM AQUI EM 31/08/2026, e vale o registro: elas
-- viviam no schema da ferramenta. Sao cache de normalizacao de endereco — o
-- mesmo texto se separa igual para qualquer cliente E para qualquer ferramenta,
-- e cada segmentacao custa uma chamada ao modelo. Mante-las por ferramenta
-- seria pagar o mesmo trabalho tres vezes.

set local search_path = resources_root, public;

create table if not exists ibge_cnefe (
  cep                        text,
  cod_municipio              text
);

create table if not exists ibge_malha (
  cod_municipio              text,
  geom                       jsonb,
  nome                       text,
  uf                         text
);

create table if not exists rf_empresas (
  capital_social             text,
  cnpj_basico                text,
  ente_federativo            text,
  natureza_juridica          text,
  porte                      text,
  qualificacao_responsavel   text,
  razao_social               text
);

create table if not exists rf_estabelecimentos (
  bairro                     text,
  cep                        text,
  cidade_exterior            text,
  cnae_principal             text,
  cnae_secundaria            text,
  cnpj_basico                text,
  cnpj_dv                    text,
  cnpj_ordem                 text,
  complemento                text,
  data_inicio                text,
  data_situacao              text,
  data_situacao_especial     text,
  ddd1                       text,
  ddd2                       text,
  ddd_fax                    text,
  email                      text,
  fax                        text,
  logradouro                 text,
  matriz_filial              text,
  motivo_situacao            text,
  municipio                  text,
  nome_fantasia              text,
  numero                     text,
  pais                       text,
  situacao_cadastral         text,
  situacao_especial          text,
  tel1                       text,
  tel2                       text,
  tipo_logradouro            text,
  uf                         text
);

create table if not exists rf_socios (
  cnpj_basico                text,
  cnpj_cpf_socio             text,
  data_entrada               text,
  faixa_etaria               text,
  identificador_socio        text,
  nome_representante         text,
  nome_socio                 text,
  pais                       text,
  qualificacao_representante text,
  qualificacao_socio         text,
  representante_cpf          text
);

create table if not exists rf_simples (
  cnpj_basico                text,
  data_exclusao_mei          text,
  data_exclusao_simples      text,
  data_opcao_mei             text,
  data_opcao_simples         text,
  opcao_mei                  text,
  opcao_simples              text
);

create table if not exists rf_cnaes (
  codigo                     text,
  descricao                  text
);

create table if not exists rf_municipios (
  codigo                     text,
  descricao                  text
);

create table if not exists rf_naturezas (
  codigo                     text,
  descricao                  text
);

create table if not exists rf_paises (
  codigo                     text,
  descricao                  text
);

create table if not exists rf_qualificacoes (
  codigo                     text,
  descricao                  text
);

create table if not exists rf_motivos (
  codigo                     text,
  descricao                  text
);

create table if not exists endereco_segmentado (
  bairro                     text,
  cep                        text,
  cidade                     text,
  complemento                text,
  endereco                   text,
  logradouro                 text,
  metodo                     text not null,
  modelo                     text,
  motivo                     text,
  numero                     text,
  segmentado_em              timestamptz not null default now(),
  uf                         text
);

create table if not exists logradouro_ajustado (
  ajustado_em                timestamptz not null default now(),
  complemento_organizado     text,
  fonte                      text not null,
  logradouro_corrigido       text,
  logradouro_marcado         text,
  logradouro_original        text,
  numero_canonico            text,
  origem                     text,
  record_id                  text not null,
  revisado_em                timestamptz,
  revisado_por               text,
  revisao_nota               text,
  revisao_status             text not null default 'pendente' check (revisao_status in ('pendente', 'corrigido', 'confirmado', 'descartado')),
  risco                      text,
  run_id                     text,
  scope_id                   text not null,
  tier                       text
);


-- Leitura para todos, escrita so para quem carrega.
grant usage on schema resources_root to app_user, readonly, authenticated;
grant select on all tables in schema resources_root to app_user, readonly, authenticated;
alter default privileges in schema resources_root
  grant select on tables to app_user, readonly, authenticated;

-- O QUE FALTA, E QUE NAO DA PARA FAZER DAQUI: o pipeline do Radar Comercial
-- passa a ESCREVER o cache de endereco, e hoje ele nao tem essa permissao —
-- `resources_root` e de `resources_loader`. Alguem com o papel do dono precisa:
--
--   grant insert, update on resources_root.endereco_segmentado  to app_user;
--   grant insert, update on resources_root.logradouro_ajustado  to app_user;
--
-- Sem isso a segmentacao falha com "permission denied for table", que e erro
-- claro — mas so na primeira vez que rodar.
