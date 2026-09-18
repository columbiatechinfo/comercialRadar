-- 0125 · a ligação se acha pelo índice, e não lendo o cadastro inteiro (18/09/2026).
--
-- MEDIDO NA VALIDAÇÃO DE BENTO GONÇALVES: `where num_ligacao::text = ...` numa tabela de 2.516.709 linhas (3,2 GB)
-- fazia varredura sequencial — 672 a 1.208 ms para achar UMA ligação, com o banco em ~8 núcleos só disso. A consulta
-- estava no julgamento, no dossiê, na checagem, na foto de rua e em avaliar_ligacao, uma ou mais vezes por ligação.
--
-- POR QUE UM ÍNDICE SIMPLES NO NÚMERO, E O CÓDIGO COMPARANDO NÚMERO: com a RLS ativa o Postgres só usa um índice em
-- comparação "à prova de vazamento". `int8eq` é; a conversão de bigint para texto não é. Um índice na expressão
-- `num_ligacao::text` funcionou para o administrador e foi IGNORADO pela conexão do sistema — criado e removido no
-- mesmo dia. A chave primária (id_empresa, num_ligacao) não serve: a empresa entra pela RLS num OR que o planejador não
-- usa como prefixo. Por isso este índice é a exceção à regra "empresa na frente": com ela, não seria usado.
--
-- Depois: 0,5 a 1,9 ms por ligação, pela conexão do sistema, com a RLS ativa. O código acompanha a mudança no mesmo
-- commit (`num_ligacao = %s` e `num_ligacao = any(%s::bigint[])`).
--
-- RODA FORA DE TRANSAÇÃO (`concurrently`) e como supabase_admin (o dono da tabela é o migrator; o `postgres` do
-- Supabase não é superusuário):
--     docker exec -i supabase-db psql -U supabase_admin -d a2l -v ON_ERROR_STOP=1 < migrations/0125_a_ligacao_se_acha_pelo_indice.sql

create index concurrently if not exists ix_corsan_num_ligacao
    on resources_root.cadastro_corsan (num_ligacao);

drop index concurrently if exists resources_root.ix_corsan_num_ligacao_txt;
