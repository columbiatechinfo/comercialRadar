-- 0085c · o `migrator` tambem CRIA no caixote, e nao so le.
--
-- A 0085b deu `usage` e `select` supondo que quem cria a tabela crua e sempre
-- o `app_user` — verdade para `carregar_base.py`, que roda no pipeline. Mas
-- `aplicar_qualificacao.py` conecta como `migrator` (ele altera a tabela
-- canonica no mesmo passo) e precisa da tabela crua para casar por `join` em
-- vez de disparar 102 mil `update` de ida e volta.
--
-- O sintoma foi "permission denied for schema base_bruta" no `create table`,
-- depois de o arquivo de 36 MB ja ter sido enviado. Terceira vez em dois dias
-- que uma permissao aparece so na hora de escrever: o padrao vale anotar —
-- conceder no schema novo a TODOS os papeis que vao toca-lo, e nao ao primeiro
-- que aparecer.
grant create on schema base_bruta to migrator;

-- E o inverso do que a 0085b ja fez: o `app_user` precisa enxergar o que o
-- `migrator` criar la, ou a proxima carga pelo pipeline nao acha a tabela.
alter default privileges for role migrator in schema base_bruta
    grant select on tables to app_user;
