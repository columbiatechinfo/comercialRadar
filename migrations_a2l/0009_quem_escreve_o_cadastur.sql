-- 0009_quem_escreve_o_cadastur.sql — o app grava a base pública do MTur.
--
-- POR QUE O `app_user` E NÃO O `resources_loader`, que é quem carrega as
-- outras bases públicas.
--
-- O CNEFE e o CNPJ são carga pura: um processo baixa, faz `COPY`, e acabou.
-- Ninguém mais escreve neles. O Cadastur é diferente — ele é a ETAPA 3 do
-- pipeline, e as funções que o gravam (`gerar`, `encadear`) tocam as DUAS
-- coisas na mesma transação: leem `resources_root.cadastur_prestador` e
-- escrevem em `radar_comercial.pois`, cruzando prestador com POI.
--
-- Duas conexões, uma por papel, não resolveriam: seria uma transação em cada,
-- e o cruzamento deixaria de ser atômico. Dar ao `resources_loader` acesso a
-- `pois` seria pior — ele passaria a enxergar dado de cliente para escrever
-- base pública.
--
-- Então a permissão vai para quem já roda a etapa. É estreita: `app_user`
-- escreve NESTAS DUAS tabelas e em nenhuma outra do `resources_root`, onde ele
-- continua só lendo.
--
-- Rodar como `migrator`, que é dono das duas.

grant insert, update, delete
   on resources_root.cadastur_prestador, resources_root.cadastur_total_pf
   to app_user;
