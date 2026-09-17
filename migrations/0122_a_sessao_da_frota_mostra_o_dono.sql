-- 0122 · a sessão da frota diz quem a abriu e o que acabou de acontecer (pedido do painel da frota, 17/09/2026).
--
-- O painel "Frota e máquinas" (plataforma root) cruza o processo visto em /proc com a sessão e o IP. Para isso:
--   · `dono` na sessão, no MESMO formato de `navegacao.reserva.dono` (máquina:pid:site:vaga);
--   · `ultimo_resultado` e `ultimo_em`, gravados em lote pela faxina da frota, para o retrato não buscar em `evento`
--     uma vez por sessão;
--   · índices para o histórico de uma sessão (`evento` por sessão e tempo) e para o clique num IP (`sessao` por IP).
-- Quem escreve: `frota_navegacao.py`. Quem lê: o painel da frota (papel `frota_leitor`, grant por coluna).
set lock_timeout = '5s';
begin;
set local role migrator;
alter table navegacao.sessao add column if not exists dono text,
                             add column if not exists ultimo_resultado text,
                             add column if not exists ultimo_em timestamptz;
create index if not exists ix_nav_sessao_proxy on navegacao.sessao (proxy_id, id);
create index if not exists ix_nav_evento_sessao on navegacao.evento (sessao_id, em);
commit;
do $$ begin
    if exists (select 1 from pg_roles where rolname = 'frota_leitor') then
        execute 'grant select (dono, ultimo_resultado, ultimo_em) on navegacao.sessao to frota_leitor';
    end if;
end $$;
