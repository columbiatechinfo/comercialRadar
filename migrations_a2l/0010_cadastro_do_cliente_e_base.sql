-- 0010 — a base do cliente é BASE, e base mora em resources_root
--
-- A `cadastro_corsan` nasceu em `radar_comercial` (2.516.709 linhas, 1686 MB,
-- carregada de `assets/completa.csv`). Está no schema errado, e a regra que a
-- desloca é a mesma desde o início: `radar_comercial` guarda o que o sistema
-- GERA — imagem, análise de IA, cruzamento. Base fica em `resources_root`.
--
-- O cadastro do cliente é base. Não importa que o radar seja hoje o único a
-- lê-lo: ele descreve o mundo, não o nosso trabalho sobre o mundo, e outros
-- sistemas do A2L vão consumi-lo. Base não é alterada por quem a usa.
--
-- ────────────────────────────────────────────────────────────────────────────
-- ESTA TABELA É A ÚNICA DE `resources_root` COM RLS, E ISSO É DELIBERADO
--
-- Todas as outras bases do schema são públicas na origem — IBGE, Receita,
-- Ministério do Turismo. `ibge_malha` concede `SELECT` a `authenticated` e não
-- tem RLS, porque não há o que proteger: o dado já é público.
--
-- Esta não. São 2,5 milhões de ligações de água de UM cliente, com documento,
-- endereço e situação cadastral. Seguir o padrão do schema aqui — desligar a
-- RLS e conceder `SELECT` a `authenticated` — exporia a carteira inteira da
-- Corsan a qualquer usuário autenticado de qualquer ferramenta do A2L.
--
-- Então a RLS e as duas políticas VIAJAM COM A TABELA. `SET SCHEMA` preserva
-- as duas coisas, mais os sete índices e a chave primária; este arquivo só
-- confere que preservou.
--
-- O modo de falha disso é silencioso e vale ser dito: quem ler sem
-- `request.jwt.claims` no lugar recebe ZERO LINHA, não erro de permissão.
-- ────────────────────────────────────────────────────────────────────────────
--
-- POR QUE A CONFERÊNCIA NÃO CONTA LINHA
--
-- A primeira versão deste arquivo fazia `count(*)` antes e depois. Falhou:
--
--     ERROR: permission denied for function nivel_atual
--     CONTEXT: SQL statement "select count(*) from radar_comercial.cadastro_corsan"
--
-- Quem executa a migração é o `migrator`, que não tem BYPASSRLS — por desenho.
-- Contar linhas faz a política rodar, a política chama `core.nivel_atual()`, e
-- o migrator não tem EXECUTE nela. A recusa está certa; o teste é que estava
-- errado.
--
-- O que se confere no lugar é MAIS FORTE que a contagem: `relfilenode` igual
-- antes e depois prova que o heap não foi reescrito — os mesmos bytes, no mesmo
-- arquivo, apenas com outro nome de schema no catálogo. Uma contagem igual
-- provaria menos, e ainda dependeria de ler 2,5 milhões de linhas.

begin;

-- Estado de partida, guardado numa tabela temporária para o `do` de depois.
create temporary table _antes on commit drop as
select c.oid, c.relfilenode, c.reltuples::bigint as tuplas,
       c.relrowsecurity as rls, c.relforcerowsecurity as forca,
       (select count(*) from pg_policies p
         where p.schemaname = 'radar_comercial' and p.tablename = 'cadastro_corsan') as politicas,
       (select count(*) from pg_indexes i
         where i.schemaname = 'radar_comercial' and i.tablename = 'cadastro_corsan') as indices
  from pg_class c
 where c.oid = 'radar_comercial.cadastro_corsan'::regclass;

alter table radar_comercial.cadastro_corsan set schema resources_root;

-- ── conferência: o que tinha de viajar, viajou ──────────────────────────────
do $$
declare a record; d record;
begin
  select * into a from _antes;
  select c.oid, c.relfilenode, c.reltuples::bigint as tuplas,
         c.relrowsecurity as rls, c.relforcerowsecurity as forca,
         (select count(*) from pg_policies p
           where p.schemaname = 'resources_root' and p.tablename = 'cadastro_corsan') as politicas,
         (select count(*) from pg_indexes i
           where i.schemaname = 'resources_root' and i.tablename = 'cadastro_corsan') as indices
    into d
    from pg_class c
   where c.oid = 'resources_root.cadastro_corsan'::regclass;

  if d.oid <> a.oid or d.relfilenode <> a.relfilenode then
    raise exception 'o heap foi reescrito: oid %->% filenode %->%',
      a.oid, d.oid, a.relfilenode, d.relfilenode;
  end if;
  if not d.rls or not d.forca then
    raise exception 'RLS nao sobreviveu: rowsecurity=% force=%', d.rls, d.forca;
  end if;
  if d.politicas <> a.politicas then
    raise exception 'politicas: % antes, % depois', a.politicas, d.politicas;
  end if;
  if d.indices <> a.indices then
    raise exception 'indices: % antes, % depois', a.indices, d.indices;
  end if;

  raise notice 'mesmo heap (filenode %), % politicas, % indices, RLS on+FORCE, ~% linhas',
    d.relfilenode, d.politicas, d.indices, d.tuplas;
end $$;

-- Nada de `grant select to authenticated` aqui, ao contrário das demais bases
-- do schema: quem lê esta tabela passa pela RLS, e a RLS precisa de um usuário
-- com empresa. Ver o bloco no topo.

-- ── o dono continua sendo `migrator`, e não `resources_loader` ──────────────
--
-- Toda outra tabela de `resources_root` pertence a `resources_loader`, que é
-- dono do schema. Esta fica com `migrator`, e a razão é mecânica: quem executa
-- esta migração É o migrator, e passar a posse exige ser membro do papel que
-- recebe. `migrator` não é membro de `resources_loader` — nem deveria ser, já
-- que é essa separação que impede a migração de escrever dado e o carregador
-- de mudar estrutura.
--
-- Em vez de furar essa parede por uma questão estética, o carregador recebe o
-- que precisa para atualizar a base quando o cliente mandar versão nova.
grant select, insert, update, delete on resources_root.cadastro_corsan to resources_loader;

commit;
