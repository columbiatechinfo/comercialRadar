-- 0085b · quem materializa e o `migrator`, e ele precisa LER o caixote.
--
-- A 0085 concedeu leitura ao `resources_loader` supondo que ele materializaria
-- a tabela canonica. Nao e ele: `resources_root.cadastro_corsan` pertence ao
-- `migrator`, e `materializar_base.py` conecta com `A2L_MIGRATOR_URL` porque
-- criar tabela e indice e operacao estrutural.
--
-- O sintoma foi "permission denied for schema base_bruta" no meio do
-- `insert ... select`, DEPOIS de a tabela nova ja ter sido criada — o pior
-- lugar para descobrir uma permissao faltando.
grant usage on schema base_bruta to migrator;
grant select on all tables in schema base_bruta to migrator;

-- E o que ainda nao existe: `carregar_base.py` cria a tabela crua a cada
-- carga, e sem isto a proxima nasceria invisivel para quem vai le-la.
alter default privileges for role app_user in schema base_bruta
    grant select on tables to migrator;
