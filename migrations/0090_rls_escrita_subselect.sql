-- 0090 · a politica de ESCRITA da cadastro_corsan tambem precisa do subselect.
--
-- A 0081 corrigiu so a de leitura, e mediu o estrago: funcao nua numa policy e
-- avaliada UMA VEZ POR LINHA, e `count(*)` na tabela de 2.516.709 linhas caiu
-- de 14,2 s para 0,1 s com o subselect.
--
-- `p_corsan_escreve` ficou como estava:
--
--   core.eh_suporte() OR (id_empresa = core.empresa_atual()
--                         AND (select core.hierarquia_atual()) >= 40)
--
-- Duas das tres chamadas estao nuas. Como a policy e `for all`, ela vale
-- tambem para SELECT — as permissivas sao OR-adas —, mas o custo que importa e
-- o da ESCRITA: um `update` em massa paga duas chamadas de funcao por linha.
-- Em 09/09/2026 um update de 102.131 linhas levou 15 s; nos 2,5 milhoes seria
-- o dobro do que precisa ser.
--
-- O subselect faz o Postgres avaliar UMA vez e reusar — as funcoes sao STABLE,
-- entao o resultado nao muda dentro da consulta. Nao ha troca de semantica
-- aqui, so de plano.
alter policy p_corsan_escreve on resources_root.cadastro_corsan
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual())
               and (select core.hierarquia_atual()) >= 40));

-- E a de leitura, conferida: ja estava com subselect desde a 0081. Repetir o
-- `alter` aqui e barato e garante que as duas fiquem iguais mesmo se alguem
-- tiver mexido no meio do caminho.
alter policy p_corsan_le on resources_root.cadastro_corsan
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
