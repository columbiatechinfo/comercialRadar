-- 0126 · a Receita se acha pelo CNPJ básico, e não lendo a tabela inteira (18/09/2026).
--
-- MEDIDO NA VALIDAÇÃO DE BENTO GONÇALVES: a checagem do veredito (checagem_veredito.py) pergunta, por ligação,
-- se o CNPJ é MEI (`rf_simples`, 49,9 M de linhas, 4 GB) e qual a natureza jurídica (`rf_empresas`, 69,5 M de
-- linhas, 6,4 GB). Nenhuma das duas tinha índice (o carregador base_cnpj.py os declara em INDICES, mas não estavam
-- no banco): cada pergunta era uma varredura inteira, 0,9 a 1,6 s em média e 3 a 10 s com o i9 carregado. Somadas,
-- ~120 mil chamadas e ~40 h de banco; na hora da medição o Postgres gastava ~6 núcleos só nisso.
--
-- Os nomes são os do carregador (INDICES em base_cnpj.py), para o `if not exists` dele reconhecer os mesmos.
--
-- RODA FORA DE TRANSAÇÃO — `concurrently` não aceita transação — e não trava leitura nem escrita enquanto constrói:
--     docker exec -i supabase-db psql -U supabase_admin -d a2l -v ON_ERROR_STOP=1 < migrations/0126_a_receita_se_acha_pelo_basico.sql

create index concurrently if not exists ix_simples_basico
    on resources_root.rf_simples (cnpj_basico);
create index concurrently if not exists ix_empresas_basico
    on resources_root.rf_empresas (cnpj_basico);
