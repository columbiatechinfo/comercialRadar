-- 0120 · o job `qualificacao` na fila (dono do produto, 16/09/2026).
--
-- A tela da base do cliente passa a atualizar só a qualificação (SIM / SIM com análise humana / NÃO) por ligação,
-- sem trocar a tabela. O arquivo sobe para o volume `radar-uploads`, que só o worker da fila monta; por isso vira
-- um tipo de job (`minerador_worker._comando` → `aplicar_qualificacao.py`).
--
-- A troca da `check` pega um lock curto na `job`, que o worker consulta a cada poucos segundos: `lock_timeout` para
-- não enfileirar leitores atrás de um DDL esperando (memória: DDL trava a fila inteira).
set lock_timeout = '5s';
begin;
alter table radar_comercial.job drop constraint if exists job_tipo_check;
alter table radar_comercial.job add constraint job_tipo_check
    check (tipo = any (array['mineracao'::text, 'planilha'::text, 'qualificacao'::text]));
commit;
