-- 0026 — Cadastur/MTur: prestadores de serviço turístico
--
-- O QUE É, E POR QUE ENTRA NO PRODUTO
--
-- Cadastro obrigatório da Lei 11.771/2008: hotel, pousada, restaurante
-- turístico, agência, parque, acampamento. Para uma companhia de saneamento é
-- uma fonte de RÓTULO de alta precisão — o Estado afirma que ali funciona um
-- meio de hospedagem, e hospedagem é consumo de água por leito.
--
-- É a primeira fonte que traz CAPACIDADE declarada (`uh`, `leitos`). Nenhuma
-- das outras diz quantas pessoas dormem no imóvel.
--
-- O QUE NÃO ENTRA, E ISSO É DECISÃO, NÃO ESQUECIMENTO
--
-- O Cadastur tem 15 atividades, e duas delas são de PESSOA FÍSICA — guia de
-- turismo, principalmente. Aquelas linhas trazem CPF, data de nascimento, nome
-- social, documento de identificação, nacionalidade, sexo e até tipo
-- sanguíneo.
--
-- Nada disso entra aqui. Não há coluna para receber, então não há como entrar
-- por descuido. Dois motivos, e o segundo bastaria sozinho:
--
--   1. Guia de turismo é pessoa que presta serviço, não imóvel que consome
--      água. Não é o alvo do produto.
--   2. LGPD. Dado pessoal só se coleta com finalidade — e a finalidade aqui é
--      identificar ECONOMIA comercial. Guardar CPF "porque veio no arquivo" é
--      exatamente o que a lei chama de tratamento sem base legal.
--
-- ZERO PERDA, MESMO ASSIM
--
-- A skill de origem tem uma regra inviolável: coluna sem mapa vai íntegra para
-- `_extras`. Aqui vale o mesmo para o que É de pessoa jurídica: as 60 colunas
-- de cauda por atividade (área de montagem, tipo de estrutura náutica,
-- embarcações de cruzeiro fluvial) não ganham coluna própria — vão para
-- `extras` jsonb. Criar 60 colunas com 0,3% de preenchimento é desenhar a
-- tabela para o caso raro.
--
-- A CHAVE
--
-- CNPJ **não** é chave: repete entre trimestres (é uma série temporal) e entre
-- atividades (o mesmo CNPJ pode ser hotel e restaurante). A chave física é a
-- linha de origem — (recurso_id, linha_origem) —, que é única por definição.

begin;

create table if not exists comercialradar.cadastur_prestador (
  id                  bigserial primary key,
  tenant_id           uuid not null,

  -- ── Identidade da empresa ────────────────────────────────────────────
  cnpj                text,
  razao_social        text,
  nome_fantasia       text,
  cnae                text,
  natureza_juridica   text,
  porte               text,

  -- ── O que ela é, no vocabulário do MTur ──────────────────────────────
  atividade_turistica text,        -- "Meio de Hospedagem", "Restaurante"...
  tipo_hospedagem     text,        -- hotel, pousada, hostel, resort...
  categorias          text,

  -- ── Onde ─────────────────────────────────────────────────────────────
  -- DOIS endereços, e os dois ficam. O da Receita é o cadastro fiscal; o
  -- comercial é onde a atividade acontece. Divergem com frequência, e ficar
  -- só com um perderia justamente o caso interessante: sede num escritório,
  -- pousada noutro lugar.
  endereco_comercial  text,
  endereco_rfb        text,
  municipio           text,
  uf                  text,
  cod_municipio       text,        -- IBGE, preenchido no carregamento

  -- ── Capacidade declarada ─────────────────────────────────────────────
  -- AUTODECLARADA, e o número é ruim: no 2T/2026 havia 64 valores não
  -- numéricos, 9 zerados, 20 com leitos < UH, e máximos de 8.500 UH e 17.000
  -- leitos. Por isso o texto original fica ao lado do número: quando o
  -- outlier aparecer, dá para ver o que estava escrito em vez de discutir o
  -- que o parser fez.
  uh                  integer,
  uh_texto            text,
  leitos              integer,
  leitos_texto        text,

  -- ── Contato ──────────────────────────────────────────────────────────
  telefone            text,
  email               text,
  website             text,

  -- ── Situação ─────────────────────────────────────────────────────────
  -- "Regular" NÃO é o mesmo que certificado válido: o snapshot já vem
  -- filtrado por regularidade, e a validade é outra data. Quem quiser
  -- vigência compara `validade` com hoje.
  situacao_cadastral  text,
  situacao_atividade  text,
  numero_certificado  text,
  validade            date,
  validade_texto      text,        -- o que veio escrito, quando não é data

  -- ── Procedência (R3 da skill: linha sem carimbo é dado órfão) ────────
  dataset             text not null,
  recurso_id          text not null,
  recurso_nome        text,
  ref_periodo         date,        -- fim do trimestre a que o snapshot se refere
  linha_origem        integer not null,
  sha256              text,
  extraido_em         timestamptz,

  extras              jsonb not null default '{}'::jsonb,

  -- ── Ligação com o resto do sistema ───────────────────────────────────
  -- Preenchido pelo cruzamento. Nulo significa "ainda não cruzado" ou
  -- "cruzou com nada" — e são coisas diferentes, por isso `cruzado_em`.
  poi_id              bigint references comercialradar.pois(id) on delete set null,
  cruzado_em          timestamptz,
  -- Por que este registro NÃO virou POI. Vazio quando virou, ou quando ainda
  -- não se tentou. Sem isto, "não virou" e "ninguém tentou" ficam iguais.
  sem_poi_motivo      text,

  criado_em           timestamptz not null default now(),

  -- A chave física da skill: a linha do arquivo de origem. Reimportar o mesmo
  -- trimestre não duplica; importar o trimestre seguinte acrescenta a nova
  -- observação da série, que é o comportamento certo para dado temporal.
  unique (tenant_id, recurso_id, linha_origem)
);

comment on table comercialradar.cadastur_prestador is
  'Cadastro de Prestadores de Serviços Turísticos (MTur). Fonte de rótulo: o '
  'Estado afirma que ali opera hospedagem/turismo. Traz capacidade declarada '
  '(UH e leitos), que nenhuma outra fonte tem. Sem dado de pessoa física — '
  'guia de turismo não entra, por finalidade e por LGPD.';

comment on column comercialradar.cadastur_prestador.uh is
  'Unidades habitacionais, AUTODECLARADAS. Trate outlier como suspeita, não '
  'como medida — `uh_texto` guarda o que veio escrito.';

comment on column comercialradar.cadastur_prestador.sem_poi_motivo is
  'Por que não virou POI: sem_cnpj, sem_coordenada, ja_existe. Vazio = virou, '
  'ou ainda não se tentou. Distinguir os dois é o que evita reprocessar o que '
  'já se sabe que não tem como resolver.';

-- `tenant_id` primeiro em todo índice: a RLS é avaliada por linha, e sem isso
-- a policy força varredura completa.
create index if not exists ix_cadastur_municipio
  on comercialradar.cadastur_prestador (tenant_id, uf, municipio);
create index if not exists ix_cadastur_cnpj
  on comercialradar.cadastur_prestador (tenant_id, cnpj)
  where cnpj is not null;
-- Os que ainda não viraram POI e ninguém explicou por quê: é esta a fila de
-- trabalho do gerador, e ela precisa ser barata de ler.
create index if not exists ix_cadastur_pendente
  on comercialradar.cadastur_prestador (tenant_id, uf, municipio)
  where poi_id is null and sem_poi_motivo is null;
create index if not exists ix_cadastur_poi
  on comercialradar.cadastur_prestador (tenant_id, poi_id)
  where poi_id is not null;

alter table comercialradar.cadastur_prestador enable row level security;
alter table comercialradar.cadastur_prestador force  row level security;

drop policy if exists tenant_isolado on comercialradar.cadastur_prestador;
create policy tenant_isolado on comercialradar.cadastur_prestador
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

drop trigger if exists tg_tenant on comercialradar.cadastur_prestador;
create trigger tg_tenant before insert on comercialradar.cadastur_prestador
  for each row execute function comercialradar.preencher_tenant();

-- O root lê o que o worker escreve. Sem isto a tabela nasce invisível para
-- quem administra — foi o que aconteceu com dez tabelas antes da 0024.
grant select, insert, update, delete
  on comercialradar.cadastur_prestador to comercialradar_app;
grant usage, select on sequence comercialradar.cadastur_prestador_id_seq
  to comercialradar_app;

commit;
