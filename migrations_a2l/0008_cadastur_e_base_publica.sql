-- 0008_cadastur_e_base_publica.sql — o Cadastur vai para onde as bases moram.
--
-- A REGRA, dita pelo dono do produto em 31/08/2026:
--
--   `radar_comercial`  só o que o SISTEMA GERA — POI minerado, imagem,
--                      análise de IA, cruzamento, fila de trabalho.
--   `resources_root`   toda BASE: pública (CNEFE, CNPJ, Cadastur, Overture,
--                      malha do IBGE) e, mais adiante, a do cliente.
--
-- O Cadastur estava do lado errado. Eu o pus em `radar_comercial` com RLS por
-- empresa, e ele é base pública do MTur: o mesmo prestador de turismo é o mesmo
-- para todo cliente e para toda ferramenta do A2L. Mantê-lo por empresa
-- significaria baixar e guardar os mesmos 322 MB uma vez por cliente.
--
-- COMO ISTO APARECEU. A carga baixou os 322 MB e morreu ao gravar, com
-- `new row violates row-level security policy for table "cadastur_prestador"` —
-- porque a tabela tinha política e o carregador de base pública não declara
-- identidade nenhuma (nem deveria). O erro estava certo; a tabela é que estava
-- no lugar errado.
--
-- NADA SE PERDE: as duas estão com zero linhas. A carga foi parada antes de
-- gravar, e o snapshot baixado continua em disco.
--
-- Rodar como `migrator` — ele é o dono das duas hoje.

set local search_path = radar_comercial, public;


-- ─────────────────────────────────────────────────────────────────────
-- 1 · Sai a política, que não faz sentido em base pública
-- ─────────────────────────────────────────────────────────────────────
drop policy if exists p_cadastur_prestador on cadastur_prestador;
drop policy if exists p_cadastur_total_pf  on cadastur_total_pf;

alter table cadastur_prestador no force row level security;
alter table cadastur_prestador disable row level security;
alter table cadastur_total_pf  no force row level security;
alter table cadastur_total_pf  disable row level security;

drop trigger if exists trg_empresa_cadastur_prestador on cadastur_prestador;
drop trigger if exists trg_empresa_cadastur_total_pf  on cadastur_total_pf;


-- ─────────────────────────────────────────────────────────────────────
-- 2 · Sai a coluna de empresa
-- ─────────────────────────────────────────────────────────────────────
-- Base pública não pertence a cliente nenhum. Deixar a coluna vazia seria pior
-- que tirá-la: quem lesse o schema concluiria que há isolamento, e não há.
alter table cadastur_prestador drop column if exists id_empresa;
alter table cadastur_total_pf  drop column if exists id_empresa;


-- ─────────────────────────────────────────────────────────────────────
-- 3 · Mudam de schema, com os índices junto
-- ─────────────────────────────────────────────────────────────────────
-- `set schema` leva os índices e as restrições da tabela — inclusive os dois
-- únicos criados no 0006, que sustentam o `ON CONFLICT` do `cadastur.py`.
alter table cadastur_prestador set schema resources_root;
alter table cadastur_total_pf  set schema resources_root;


-- ─────────────────────────────────────────────────────────────────────
-- 4 · Quem carrega ganha permissão, e não posse
-- ─────────────────────────────────────────────────────────────────────
--
-- A POSSE FICA COM `migrator`, ao contrário das outras 14 do schema. Não é
-- inconsistência: as outras precisam de posse porque seus carregadores
-- DERRUBAM E RECRIAM a tabela (`base_cnefe` monta o DDL do cabeçalho do CSV).
-- `cadastur.py` não tem uma linha de DDL — só insere com `ON CONFLICT`. Para
-- isso, permissão basta.
--
-- A primeira versão desta migração tentava `owner to resources_loader` e morreu
-- na validação com `must be able to SET ROLE "resources_loader"`: `migrator`
-- não é membro daquele papel. Pedir a alguém com `supabase_admin` para
-- transferir seria resolver com privilégio um problema que não existe.
grant insert, update, select, delete
   on resources_root.cadastur_prestador, resources_root.cadastur_total_pf
   to resources_loader;

grant select on resources_root.cadastur_prestador to app_user, readonly, authenticated;
grant select on resources_root.cadastur_total_pf  to app_user, readonly, authenticated;
