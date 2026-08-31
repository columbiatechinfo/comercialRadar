-- 0014 — correção de endereço é coisa que o sistema GERA, e muda de schema
--
-- O cruzamento de Canoas parou aqui:
--
--     psycopg2.errors.UndefinedTable: relation "logradouro_ajustado" does not exist
--     LINE 11:   left join logradouro_ajustado la
--
-- `cruzar_fontes.py` consulta a tabela sem qualificar o schema, então procura
-- em `radar_comercial` — e ela está em `resources_root`, onde a minha
-- `0002_resources_root.sql` a criou.
--
-- E o schema errado não é detalhe de nomenclatura, é a regra do produto ao
-- contrário. `logradouro_ajustado` guarda o logradouro CORRIGIDO por nós, com
-- `revisado_por`, `revisao_nota` e `risco`: é julgamento do sistema sobre um
-- endereço, não um endereço publicado por alguém. `endereco_segmentado` idem —
-- tem `metodo` e `modelo`, o registro de COMO nós quebramos aquele texto.
--
-- Base descreve o mundo; isto descreve o nosso trabalho sobre o mundo. Vai para
-- `radar_comercial`, e com o que morar lá exige: `id_empresa`, RLS, gatilho.
--
-- A CORREÇÃO DE UMA EMPRESA NÃO PODE VAZAR PARA OUTRA, e é isso que a mudança
-- de schema traz junto. Em `resources_root` as duas tabelas não tinham
-- `id_empresa` nenhum: a revisão manual que um cliente pagou apareceria para
-- todos os outros. Estavam vazias, então não houve vazamento — mas a primeira
-- rodada de `ajuste_logradouro` teria criado um.
--
-- As duas estão VAZIAS (0 linhas), então a mudança é de catálogo apenas.

begin;

alter table resources_root.logradouro_ajustado  set schema radar_comercial;
alter table resources_root.endereco_segmentado  set schema radar_comercial;

-- ── a forma que radar_comercial exige ───────────────────────────────────────
alter table radar_comercial.logradouro_ajustado
  add column id_empresa uuid not null references core.tb_empresas(id);
alter table radar_comercial.endereco_segmentado
  add column id_empresa uuid not null references core.tb_empresas(id);

-- O gatilho carimba a empresa da sessão em toda inserção — quem grava não
-- passa `id_empresa`, e não deve mesmo: a empresa vem de quem está declarado.
create trigger trg_empresa_logradouro_ajustado
  before insert on radar_comercial.logradouro_ajustado
  for each row execute function radar_comercial.preencher_empresa();
create trigger trg_empresa_endereco_segmentado
  before insert on radar_comercial.endereco_segmentado
  for each row execute function radar_comercial.preencher_empresa();

alter table radar_comercial.logradouro_ajustado enable row level security;
alter table radar_comercial.logradouro_ajustado force row level security;
alter table radar_comercial.endereco_segmentado enable row level security;
alter table radar_comercial.endereco_segmentado force row level security;

create policy p_logradouro_ajustado on radar_comercial.logradouro_ajustado
  for all using      (core.eh_suporte() or id_empresa = core.empresa_atual())
          with check (core.eh_suporte() or id_empresa = core.empresa_atual());
create policy p_endereco_segmentado on radar_comercial.endereco_segmentado
  for all using      (core.eh_suporte() or id_empresa = core.empresa_atual())
          with check (core.eh_suporte() or id_empresa = core.empresa_atual());

-- ── os índices únicos ganham a empresa NA FRENTE ────────────────────────────
--
-- Duas razões, e as duas valem. A de correção: sem a empresa na chave, a
-- correção de um cliente para `(pois, 12345)` impediria outro cliente de ter a
-- dele. A de desempenho, do CLAUDE.md: política de RLS é avaliada POR LINHA, e
-- `id_empresa` como primeira coluna de todo índice é o que faz o filtro de
-- tenant acontecer no índice em vez de linha a linha.
drop index if exists radar_comercial.ux_logradouro_ajustado;
drop index if exists radar_comercial.ux_endereco_segmentado;
create unique index ux_logradouro_ajustado
    on radar_comercial.logradouro_ajustado (id_empresa, fonte, record_id);
create unique index ux_endereco_segmentado
    on radar_comercial.endereco_segmentado (id_empresa, endereco);

grant select, insert, update, delete
   on radar_comercial.logradouro_ajustado, radar_comercial.endereco_segmentado
   to app_user;

-- ── conferência ─────────────────────────────────────────────────────────────
do $$
declare t text; n int;
begin
  foreach t in array array['logradouro_ajustado', 'endereco_segmentado'] loop
    if to_regclass('radar_comercial.' || t) is null then
      raise exception '% nao chegou em radar_comercial', t;
    end if;
    if to_regclass('resources_root.' || t) is not null then
      raise exception '% continua em resources_root', t;
    end if;
    select count(*) into n from pg_policies
     where schemaname = 'radar_comercial' and tablename = t;
    if n <> 1 then
      raise exception '%: esperava 1 politica, achei %', t, n;
    end if;
    select count(*) into n from pg_trigger
     where tgrelid = ('radar_comercial.' || t)::regclass and not tgisinternal;
    if n <> 1 then
      raise exception '%: esperava 1 gatilho, achei %', t, n;
    end if;
  end loop;
  raise notice 'as duas em radar_comercial, com id_empresa, RLS, politica e gatilho';
end $$;

commit;
