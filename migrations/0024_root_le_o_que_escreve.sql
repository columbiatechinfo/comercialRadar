-- 0024 — o `root` volta a LER as tabelas em que ele já podia ESCREVER
--
-- O DEFEITO, achado ao revisar a rota da ficha antes de escrever a tela
--
--     GET /api/fila/{id}/ficha  como root
--     -> psycopg2.errors.InsufficientPrivilege:
--        permission denied for table fachada_triagem
--
-- Dez tabelas estavam assim, e o padrão era o mesmo em todas: **escreve e não
-- lê**. É a pior combinação possível, no papel que existe justamente para ver
-- tudo — o `root` podia apagar um registro que nunca conseguiria consultar.
--
--     atribuicao_divergente   campo_catalogo    chat_anexo
--     chat_conversa           chat_mensagem     cruzamento
--     fachada_triagem         foto_maps_triagem ifood_merchant
--     memoria
--
-- A CAUSA, que é uma letra
--
-- Havia dois conjuntos de privilégio padrão no schema, um por dono de tabela:
--
--     dono postgres  ->  comercialradar_root=r      -- r = SELECT
--     dono worker    ->  comercialradar_root=awd    -- a,w,d = INSERT/UPDATE/DELETE
--
-- Falta o `r` no segundo. E as migrations rodam como `comercialradar_worker`,
-- então TODA tabela criada por elas nasceu com esse buraco. As dez acima são as
-- criadas depois que aquele default foi definido.
--
-- Não foi um esquecimento pontual: era uma regra errada produzindo o mesmo erro
-- a cada tabela nova. Por isso a correção tem duas partes — as que existem e as
-- que virão.

begin;

-- ─── 1 · as tabelas que já existem ───────────────────────────────────────────

grant select on all tables in schema comercialradar to comercialradar_root;

-- Sequências também: sem `usage, select` o `root` não lê `currval` nem enxerga
-- o próximo valor, e algumas rotas de leitura tocam nisso.
grant usage, select on all sequences in schema comercialradar to comercialradar_root;

-- ─── 2 · as que ainda vão nascer ─────────────────────────────────────────────
--
-- O `for role comercialradar_worker` é o ponto: é ele quem cria as tabelas nas
-- migrations. Sem essa cláusula, a regra valeria só para o que o `postgres`
-- criasse — e o buraco reapareceria na próxima migration.

alter default privileges for role comercialradar_worker in schema comercialradar
  grant select on tables to comercialradar_root;

alter default privileges for role comercialradar_worker in schema comercialradar
  grant usage, select on sequences to comercialradar_root;

-- NÃO se declara aqui o default do `postgres`, por duas razões: ele já concede
-- `r` (conferido em `pg_default_acl`), e só o próprio dono pode alterar os
-- privilégios padrão de um papel — esta migration roda como
-- `comercialradar_worker`, e a tentativa devolve
-- "permission denied to change default privileges".

commit;
