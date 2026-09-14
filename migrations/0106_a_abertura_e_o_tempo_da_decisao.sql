-- 0106 · quem abriu cada caso, quando, e quanto tempo levou para decidir (14/09/2026)
--
-- Pedido do dono do produto para a gestao das aprovacoes: medir o tempo medio
-- entre ABRIR uma ligacao na SEEK e DECIDIR.
--
-- `seek_abertura` e o HISTORICO das aberturas: uma linha cada vez que uma
-- pessoa assume a trava de uma ligacao (0105). O sinal de 30 s nao grava linha —
-- so a abertura nova.
--
-- `seek_decisao.aberta_em` guarda NA PROPRIA DECISAO a abertura que a precedeu
-- (a ultima desta pessoa nesta ligacao). O tempo de avaliacao vira `em - aberta_em`
-- numa linha so, sem juntar tabela por decisao — a gestao soma milhares delas.
-- Decisao em lote fica com `aberta_em` nulo: ninguem avaliou caso a caso.
--
-- COMPATIVEL COM O CODIGO NO AR: coluna nova anulavel, e a versao publicada
-- grava a decisao com lista explicita de colunas.
--
-- SOB CARGA: a abertura e append-only e cresce com as visualizacoes (uma por caso
-- aberto). Perto de 1 milhao de linhas por dia, particionar por mes como
-- `core.tb_auditoria` e definir retencao; os dois indices comecam pela empresa.

-- O ALTER DA `seek_decisao` PEDE LOCK EXCLUSIVO: se esperar atras de uma leitura
-- longa, trava a fila inteira de quem chega depois (ver ddl-trava-a-fila-inteira).
-- Melhor falhar em 5 s e rodar de novo.
set lock_timeout = '5s';

create table if not exists radar_comercial.seek_abertura (
    id          bigserial   primary key,
    id_empresa  uuid        not null,
    ligacao     text        not null,
    quem        uuid        not null,
    quem_nome   text,
    sessao      uuid,
    aberta_em   timestamptz not null default now()
);
create index if not exists seek_abertura_por_ligacao
    on radar_comercial.seek_abertura (id_empresa, ligacao, aberta_em desc);
create index if not exists seek_abertura_por_periodo
    on radar_comercial.seek_abertura (id_empresa, aberta_em desc);

alter table radar_comercial.seek_abertura enable row level security;
drop policy if exists seek_abertura_ver on radar_comercial.seek_abertura;
create policy seek_abertura_ver on radar_comercial.seek_abertura for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_abertura_gravar on radar_comercial.seek_abertura;
create policy seek_abertura_gravar on radar_comercial.seek_abertura for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
grant select, insert on radar_comercial.seek_abertura to authenticated;
grant select, insert on radar_comercial.seek_abertura to app_user;
grant usage on sequence radar_comercial.seek_abertura_id_seq to authenticated;
grant usage on sequence radar_comercial.seek_abertura_id_seq to app_user;

alter table radar_comercial.seek_decisao
    add column if not exists aberta_em timestamptz;

-- A GESTAO LE POR PERIODO E POR PESSOA; o unico indice era por ligacao.
create index if not exists seek_decisao_por_periodo
    on radar_comercial.seek_decisao (id_empresa, em desc);
create index if not exists seek_decisao_por_quem
    on radar_comercial.seek_decisao (id_empresa, quem, em desc);
