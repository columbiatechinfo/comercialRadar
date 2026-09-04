-- 0060 — `detalhado_em`/`detalhado_por` voltam para a `pois`
--
-- ERRO DE CLASSIFICACAO MEU, NA MIGRACAO 0052.
--
-- Eu as tratei como dado do Maps e mandei para `maps_data`, junto com nota,
-- plus code e horario. Elas nao sao isso. Sao a TRAVA DA FILA da etapa 4:
--
--     update pois set detalhado_em = now(), detalhado_por = <maquina>
--       from (select id from pois
--              where fonte='maps' and place_id is not null
--                and detalhado_em is null
--              for update skip locked limit 1) q
--
-- E o mecanismo que faz duas maquinas trabalharem a mesma quadra sem combinarem
-- nada. `detalhado_em` e marcado ANTES de existir qualquer dado do Maps —
-- marcar e justamente dizer "peguei este, nao pegue". Estado de processo, como
-- `sessao` e `status`, e nao opiniao de fonte.
--
-- O SINTOMA foi a etapa 4 morrer com `column "detalhado_em" does not exist` na
-- primeira run depois da 0052, DEPOIS de a etapa 2 ter rodado. Eu tinha
-- corrigido um dos usos e deixado outros cinco.
--
-- A prova de que a classificacao estava errada e simples: se fosse dado do
-- Maps, nao daria para consultar antes de o Maps ter dito qualquer coisa.

-- roda como superusuario: mexe em DADO, e `pois` tem FORCE RLS
set search_path to radar_comercial, extensions, public;

alter table pois
    add column if not exists detalhado_em  timestamptz,
    add column if not exists detalhado_por text;

-- O que a 0052 moveu volta para onde estava. Sao 287 linhas.
update pois p
   set detalhado_em  = m.detalhado_em,
       detalhado_por = m.detalhado_por
  from maps_data m
 where m.poi_id = p.id
   and (m.detalhado_em is not null or m.detalhado_por is not null);

-- A VIEW SAI DA FRENTE E VOLTA. Ela seleciona `m.detalhado_em`, e enquanto
-- existir nao da para derrubar a coluna.
drop view if exists pois_completo;

alter table maps_data
    drop column if exists detalhado_em,
    drop column if exists detalhado_por;

create view pois_completo as
select p.*,
       m.maps_url,
       m.plus_code,
       m.avaliacao,
       m.total_avaliacoes,
       m.resumo_avaliacoes,
       m.status_horario,
       coalesce(c.razao_social, r.razao_social)   as razao_social,
       coalesce(c.nome_fantasia, r.nome_fantasia) as nome_fantasia,
       coalesce(c.cnae, r.cnae)                   as cnae
  from pois p
  left join maps_data     m on m.poi_id = p.id
  left join cadastur_data c on c.poi_id = p.id
  left join receita_data  r on r.poi_id = p.id;

alter view pois_completo owner to migrator;
grant select on pois_completo to app_user, readonly;

comment on column pois.detalhado_em is
    'Quando a etapa 4 PEGOU este POI para detalhar. E trava de fila, nao dado '
    'do Maps: e marcada antes de o Maps dizer qualquer coisa, e limpa quando o '
    'POI volta para a fila.';
comment on column pois.detalhado_por is
    'Que maquina pegou. Permite medir a divisao do trabalho entre elas.';

create index if not exists pois_fila_detalhe
    on pois (id_empresa, sessao)
 where fonte = 'maps' and detalhado_em is null;

do $$
declare n int;
begin
    select count(*) into n from pois where detalhado_em is not null;
    raise notice 'detalhado_em preenchido em % POIs', n;
end $$;
