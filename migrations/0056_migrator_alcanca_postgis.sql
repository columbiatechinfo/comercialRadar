-- 0056 — o papel `migrator` alcança o PostGIS
--
-- As funções de reaproveitamento (migração 0055) são `security definer` e
-- pertencem a `migrator`, então rodam COM OS DIREITOS DELE. Elas leem
-- `pt_geo`, que é `geography`, e para extrair latitude precisam de
-- `st_y(...::geometry)` — tipo e funções que moram no schema `extensions`
-- nesta instalação.
--
-- `migrator` não tinha `usage` ali. O erro veio como
--
--     permission denied for schema extensions
--     CONTEXT: SQL function "reuso_resumo" during startup
--
-- e a primeira leitura dele foi errada: parecia "o tipo geometry não existe",
-- porque a mensagem que o cliente mostrava primeiro era a LINHA da consulta e
-- não o motivo. Qualificar o tipo como `extensions.geometry` não resolveu nada
-- — só trocou a linha citada —, e foi ler a mensagem inteira que resolveu.
--
-- `app_user` já alcançava: o `search_path` do papel dele é
-- `radar_comercial, extensions, public`. Quem não alcançava era o dono da
-- função, que é justamente quem executa numa `security definer`.

grant usage on schema extensions to migrator;
grant execute on all functions in schema extensions to migrator;
