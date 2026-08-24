-- 0027 — o Cadastur passa a ter ATUALIZAÇÃO e a contar quem não guarda
--
-- Duas coisas que a 0026 não previu.
--
-- ── 1. QUEM SAIU DO SNAPSHOT ─────────────────────────────────────────────
--
-- O Cadastur é uma SÉRIE trimestral, e o arquivo já vem filtrado: só aparece
-- quem está Regular ou Em Implantação. Um CNPJ que some entre dois trimestres
-- **perdeu regularidade** — não é erro de leitura, é fato do mundo.
--
-- Isso importa para saneamento: um meio de hospedagem que sai do cadastro pode
-- ter fechado, mudado de dono ou apenas deixado de renovar. Nos três casos vale
-- olhar de novo. Apagar a linha destruiria a informação; deixá-la igual mentiria
-- dizendo que continua regular.
--
-- Por isso `saiu_em`: a linha fica, com a data em que deixou de aparecer. Voltar
-- a aparecer limpa a data — acontece, porque renovação atrasada é comum.
--
-- ── 2. PESSOA FÍSICA: CONTADA, NÃO GUARDADA ──────────────────────────────
--
-- A 0026 simplesmente ignorava as atividades de pessoa física — guia de
-- turismo. Perdia junto uma informação legítima: **quantos** guias estão
-- registrados naquele município.
--
-- O número é dado de mercado; o CPF, a data de nascimento e o tipo sanguíneo
-- de cada guia não são. A tabela abaixo guarda só o agregado — nenhuma linha
-- individual, nenhuma coluna que possa receber dado pessoal. Contar não é
-- tratar dado pessoal: o resultado não identifica ninguém.

begin;

-- ── 1 ────────────────────────────────────────────────────────────────────
alter table comercialradar.cadastur_prestador
  add column if not exists saiu_em date,
  add column if not exists visto_em date;

comment on column comercialradar.cadastur_prestador.saiu_em is
  'Data do snapshot em que este prestador DEIXOU de aparecer. O arquivo do MTur '
  'só traz quem está regular, então sumir significa ter perdido regularidade — '
  'é evento, não erro. Nulo = continua aparecendo. Voltar a aparecer limpa.';

comment on column comercialradar.cadastur_prestador.visto_em is
  'Período de referência do último snapshot em que apareceu. Com `saiu_em`, diz '
  'há quanto tempo a ausência dura.';

-- A fila de quem saiu e ainda tem POI ligado: é ela que o operador precisa
-- rever. `tenant_id` na frente porque a RLS é avaliada por linha.
create index if not exists ix_cadastur_saiu
  on comercialradar.cadastur_prestador (tenant_id, saiu_em)
  where saiu_em is not null;

-- ── 2 ────────────────────────────────────────────────────────────────────
create table if not exists comercialradar.cadastur_total_pf (
  id            bigserial primary key,
  tenant_id     uuid not null,

  dataset       text not null,      -- ex.: prestadores-...-guia-turismo_2
  atividade     text,               -- o rótulo do MTur, legível
  uf            text not null,
  municipio     text not null,
  ref_periodo   date not null,      -- fim do trimestre a que o total se refere

  quantidade    integer not null,

  -- NÃO HÁ, e não pode haver, coluna de identificação. Nem CPF, nem nome, nem
  -- endereço. Se um dia alguém precisar do detalhe, a resposta é buscar na
  -- fonte pública com finalidade declarada — não é ampliar esta tabela.
  atualizado_em timestamptz not null default now(),

  -- Recontar o mesmo trimestre ATUALIZA; não empilha.
  unique (tenant_id, dataset, uf, municipio, ref_periodo)
);

comment on table comercialradar.cadastur_total_pf is
  'Quantos prestadores PESSOA FÍSICA (guia de turismo) o Cadastur registra por '
  'município e trimestre. Só o agregado: contar não identifica ninguém, e o '
  'detalhe individual — CPF, nascimento, tipo sanguíneo — não tem finalidade '
  'neste produto. Alimenta um card informativo, não uma lista.';

create index if not exists ix_cadastur_total_pf_lugar
  on comercialradar.cadastur_total_pf (tenant_id, uf, municipio, ref_periodo desc);

alter table comercialradar.cadastur_total_pf enable row level security;
alter table comercialradar.cadastur_total_pf force  row level security;

drop policy if exists tenant_isolado on comercialradar.cadastur_total_pf;
create policy tenant_isolado on comercialradar.cadastur_total_pf
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);

drop trigger if exists tg_tenant on comercialradar.cadastur_total_pf;
create trigger tg_tenant before insert on comercialradar.cadastur_total_pf
  for each row execute function comercialradar.preencher_tenant();

grant select, insert, update, delete
  on comercialradar.cadastur_total_pf to comercialradar_app;
grant usage, select on sequence comercialradar.cadastur_total_pf_id_seq
  to comercialradar_app;

commit;
