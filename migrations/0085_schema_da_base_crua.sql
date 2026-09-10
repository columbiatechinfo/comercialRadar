-- 0085 · um schema onde o pipeline pode criar a tabela crua da base.
--
-- O PROBLEMA. Subir a base do cliente pelo painel exige criar uma tabela cujas
-- COLUNAS SAO AS DO ARQUIVO — e ninguem sabe quais sao antes de ele chegar.
-- Isso e DDL em tempo de execucao, e o pipeline (`app_user`) nao tem CREATE em
-- schema nenhum: `resources_root` e do `resources_loader`, `radar_comercial` e
-- do `migrator`.
--
-- POR QUE NAO SIMPLESMENTE CONCEDER CREATE NOS SCHEMAS QUE EXISTEM. A fronteira
-- e deliberada e vale mais que a conveniencia: migracao cria estrutura, o
-- pipeline escreve linha. Dar CREATE em `resources_root` ao papel que roda o
-- dia inteiro significa que qualquer defeito num script alcanca a tabela de
-- 2,5 milhoes de linhas do cliente. O schema abaixo e um caixote: o pipeline
-- manda nele e em nada mais.
--
-- POR QUE TABELA E NAO JSONB. A alternativa sem DDL seria uma tabela fixa
-- `(base, linha, dados jsonb)`. Funciona e nao precisaria disto — mas 2.516.709
-- linhas em jsonb sao alguns GB e cada leitura para materializar paga a
-- desserializacao. O `COPY` para colunas de texto e uma viagem e uma varredura.
--
-- A TABELA E TRANSITORIA. Ela existe entre o upload e a materializacao da
-- tabela canonica, e `carregar_base.py` a recria a cada carga. Nao ha RLS aqui
-- porque nao ha rota que a leia: quem alcanca este schema e o papel do
-- pipeline, e nao o papel que atende o navegador.
create schema if not exists base_bruta authorization app_user;

comment on schema base_bruta is
    'Caixote do pipeline: tabelas cruas da base do cliente, entre o upload e a '
    'materializacao. Transitorias — `carregar_base.py` recria a cada carga.';

revoke all on schema base_bruta from public;
grant usage, create on schema base_bruta to app_user;

-- O leitor de referencia e os administradores continuam enxergando, para
-- diagnostico. `resources_loader` entra porque e ele quem materializa a
-- tabela canonica em `resources_root` a partir daqui.
grant usage on schema base_bruta to resources_loader;
alter default privileges for role app_user in schema base_bruta
    grant select on tables to resources_loader;
