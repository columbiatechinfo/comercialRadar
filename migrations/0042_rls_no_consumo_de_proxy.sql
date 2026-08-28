-- 0042 — RLS na `proxy_evento`
--
-- O QUE FALTOU NA 0041, e quem achou foi o teste.
--
-- `proxy_evento` nasceu com `tenant_id` e sem política, e o
-- `test_nenhuma_tabela_com_tenant_id_fica_sem_politica` reprovou na primeira
-- rodada: "tabelas com tenant_id e sem RLS ligada: ['proxy_evento']". Tabela
-- com dono e sem porteiro é vazamento esperando acontecer — o consumo de uma
-- concessionária apareceria no monitor da outra.
--
-- A POLÍTICA É A MESMA DAS DEMAIS, e de propósito: `tenant_id` comparado ao
-- `app.tenant_id` da conexão, com o `current_setting` DENTRO de um subselect.
-- Política se avalia POR LINHA; sem o subselect a função seria chamada uma vez
-- por linha do histórico, que é justamente a tabela que mais cresce aqui.
--
-- `proxy_ip` NÃO ENTRA, e continua sem `tenant_id`: o plano é infraestrutura
-- compartilhada — os mesmos 500 IPs servem todas as empresas — e carimbar um
-- dono neles seria inventar uma separação que não existe.

ALTER TABLE proxy_evento ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolado ON proxy_evento;
CREATE POLICY tenant_isolado ON proxy_evento
    USING (tenant_id = (SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid);

-- E O GATILHO, que o mesmo teste cobrou logo depois da política.
--
-- Sem ele a linha nasce com `tenant_id` NULO — e sob RLS uma linha sem empresa
-- não é de todo mundo, é de NINGUÉM: some do painel de todos, inclusive de quem
-- a gravou. No `proxy_evento` isso seria pior que perder o registro, porque o
-- monitor mostraria "nenhum consumo" com o rodízio trabalhando.
--
-- O `preencher_tenant` já existe e é o mesmo das outras tabelas: ele carimba a
-- empresa da conexão. O pool ainda manda `CR_TENANT_ID` quando tem, e o gatilho
-- cobre o caso em que não tem.

DROP TRIGGER IF EXISTS trg_tenant_proxy_evento ON proxy_evento;
CREATE TRIGGER trg_tenant_proxy_evento
    BEFORE INSERT ON proxy_evento
    FOR EACH ROW EXECUTE FUNCTION preencher_tenant();
