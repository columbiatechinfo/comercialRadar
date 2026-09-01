-- 0015 — o vínculo do Cadastur sai da base e vai para o schema do sistema
--
-- O DEFEITO, ENCONTRADO RODANDO O TESTE DE 01/09/2026
--
-- A etapa 3 gerou zero POI numa quadra onde antes gerara 77. A causa não estava
-- no código dela: `resources_root.cadastur_prestador` guarda três colunas que
-- são ESCRITURAÇÃO DO SISTEMA — `poi_id`, `sem_poi_motivo`, `cruzado_em` — e a
-- limpeza dos dados de teste não as alcançou, porque limpar tabela base é
-- proibido, e com razão.
--
-- Sobraram 102 linhas apontando para POIs que não existiam mais e 84 marcadas
-- `endereco_nao_encontrado`. A consulta que monta a fila é
--
--     where poi_id is null and sem_poi_motivo is null
--
-- e ela não viu nenhum pendente. Nada falhou, nada avisou: a etapa disse
-- "pendentes 0" e seguiu.
--
-- POR QUE ISSO É ERRADO DE PRINCÍPIO, E NÃO SÓ INCÔMODO
--
-- A regra do produto é: `resources_root` guarda o que o mundo publica,
-- `radar_comercial` guarda o que nós geramos. "Este prestador virou o POI 4321"
-- é julgamento nosso sobre a base, não um fato que o Ministério do Turismo
-- publicou. Do lado errado da fronteira ele produz três problemas:
--
--   1. não dá para apagar o que os testes geraram sem tocar em tabela base;
--   2. `cadastur_prestador` NÃO TEM `id_empresa` — é base compartilhada desde a
--      0008. Então o vínculo de uma empresa ficava visível para todas as
--      outras, e a segunda a rodar sobrescrevia o `poi_id` da primeira. Hoje há
--      uma empresa só, então não houve vazamento; com duas, haveria;
--   3. um `poi_id` que aponta para linha apagada não tem quem o invalide — não
--      há chave estrangeira possível entre schemas com donos diferentes.
--
-- O QUE MUDA
--
-- Nasce `radar_comercial.cadastur_vinculo`, com `id_empresa`, RLS e gatilho, e
-- com chave estrangeira de verdade para `pois` — agora um POI apagado leva o
-- vínculo junto, em vez de deixar ponteiro morto.
--
-- Migra-se o que dá para migrar com honestidade: as linhas COM `poi_id` sabem a
-- que empresa pertencem, porque o POI sabe. As que têm só `sem_poi_motivo` e
-- nenhum `poi_id` não sabem — e são justamente as descartáveis: dizem "não
-- virou POI porque X", e a próxima execução da etapa 3 recalcula isso. Elas
-- somem, e voltam sozinhas.

begin;

create table if not exists radar_comercial.cadastur_vinculo (
  id             bigserial primary key,
  id_empresa     uuid   not null references core.tb_empresas(id),
  -- `cadastur_prestador.id` é bigint. Guardar o id, e não o CNPJ, porque
  -- prestador pessoa física não tem CNPJ e mesmo assim entra na fila.
  cadastur_id    bigint not null,
  -- ON DELETE CASCADE, e não SET NULL: um vínculo sem POI e sem motivo é
  -- exatamente o estado "pendente", e mantê-lo como linha órfã faria a fila
  -- pular o prestador para sempre — que foi o defeito que originou tudo isto.
  poi_id         bigint references radar_comercial.pois(id) on delete cascade,
  sem_poi_motivo text,
  cruzado_em     timestamptz not null default now()
);

-- A POSSE VAI PARA `migrator`, como as outras 28 tabelas de radar_comercial.
--
-- Esta migração roda como `supabase_admin` porque `migrator` não autentica por
-- peer no socket do container, e senha não passa por aqui. Sem esta linha a
-- tabela ficaria de outro dono, e dono diferente muda quem pode alterar
-- política de RLS depois — o tipo de divergência que só aparece meses adiante.
alter table radar_comercial.cadastur_vinculo owner to migrator;
alter sequence radar_comercial.cadastur_vinculo_id_seq owner to migrator;

comment on table radar_comercial.cadastur_vinculo is
  'O que o sistema concluiu sobre cada prestador do Cadastur: virou o POI tal, '
  'ou não virou por tal motivo. Julgamento nosso — por isso mora aqui, e não '
  'em resources_root junto com o dado publicado pelo MTur.';
comment on column radar_comercial.cadastur_vinculo.sem_poi_motivo is
  'Por que NÃO virou POI. Nulo com poi_id preenchido significa "virou"; nulo '
  'com poi_id nulo não deve existir — seria uma linha que não diz nada.';

-- O gatilho carimba a empresa da sessão: quem grava não passa `id_empresa`, e
-- não deve — ela vem de quem está declarado, o mesmo caminho que a RLS lê.
create trigger trg_empresa_cadastur_vinculo
  before insert on radar_comercial.cadastur_vinculo
  for each row execute function radar_comercial.preencher_empresa();

alter table radar_comercial.cadastur_vinculo enable row level security;
alter table radar_comercial.cadastur_vinculo force  row level security;

create policy p_cadastur_vinculo on radar_comercial.cadastur_vinculo
  for all using      (core.eh_suporte() or id_empresa = core.empresa_atual())
          with check (core.eh_suporte() or id_empresa = core.empresa_atual());

-- `id_empresa` na FRENTE de todo índice: política de RLS é avaliada por linha,
-- e é isto que faz o filtro de tenant acontecer no índice.
create unique index ux_cadastur_vinculo
    on radar_comercial.cadastur_vinculo (id_empresa, cadastur_id);
create index ix_cadastur_vinculo_poi
    on radar_comercial.cadastur_vinculo (id_empresa, poi_id)
 where poi_id is not null;
-- O índice da FILA: quem ainda não tem vínculo nenhum.
create index ix_cadastur_vinculo_cad
    on radar_comercial.cadastur_vinculo (id_empresa, cadastur_id, poi_id);

grant select, insert, update, delete
   on radar_comercial.cadastur_vinculo to app_user;
grant usage, select on sequence radar_comercial.cadastur_vinculo_id_seq to app_user;

-- ── migrar o que dá para migrar com honestidade ─────────────────────────────
--
-- A empresa vem do POI, que é quem sabe. Linha sem `poi_id` não tem como dizer
-- de quem é, e é descartável por natureza: a próxima etapa 3 recalcula.
insert into radar_comercial.cadastur_vinculo
       (id_empresa, cadastur_id, poi_id, sem_poi_motivo, cruzado_em)
select p.id_empresa, c.id, c.poi_id, c.sem_poi_motivo,
       coalesce(c.cruzado_em, now())
  from resources_root.cadastur_prestador c
  join radar_comercial.pois p on p.id = c.poi_id
 where c.poi_id is not null
on conflict do nothing;

-- ── e a base volta a ser só base ────────────────────────────────────────────
alter table resources_root.cadastur_prestador drop column if exists poi_id;
alter table resources_root.cadastur_prestador drop column if exists sem_poi_motivo;
alter table resources_root.cadastur_prestador drop column if exists cruzado_em;

-- ── conferência ─────────────────────────────────────────────────────────────
do $$
declare n int;
begin
  if to_regclass('radar_comercial.cadastur_vinculo') is null then
    raise exception 'cadastur_vinculo nao foi criada';
  end if;

  select count(*) into n from information_schema.columns
   where table_schema = 'resources_root' and table_name = 'cadastur_prestador'
     and column_name in ('poi_id', 'sem_poi_motivo', 'cruzado_em');
  if n <> 0 then
    raise exception 'a base ainda tem % coluna(s) de escrituracao', n;
  end if;

  select count(*) into n from pg_policies
   where schemaname = 'radar_comercial' and tablename = 'cadastur_vinculo';
  if n <> 1 then raise exception 'esperava 1 politica, achei %', n; end if;

  select count(*) into n from pg_trigger
   where tgrelid = 'radar_comercial.cadastur_vinculo'::regclass
     and not tgisinternal;
  if n <> 1 then raise exception 'esperava 1 gatilho, achei %', n; end if;

  -- A chave estrangeira é o ponto: era ela que faltava para o ponteiro morto
  -- ser impossível.
  select count(*) into n from pg_constraint
   where conrelid = 'radar_comercial.cadastur_vinculo'::regclass
     and contype = 'f' and confrelid = 'radar_comercial.pois'::regclass;
  if n <> 1 then raise exception 'falta a chave estrangeira para pois'; end if;

  select count(*) into n from radar_comercial.cadastur_vinculo;
  raise notice 'cadastur_vinculo criada com % vinculo(s) migrado(s); a base voltou a ser so base', n;
end $$;

commit;
