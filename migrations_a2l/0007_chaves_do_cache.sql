-- 0007_chaves_do_cache.sql — as chaves naturais do cache de normalizacao.
--
-- SEPARADA DA 0006 POR CAUSA DO DONO, e nao por organizacao. As tabelas de
-- `resources_root` pertencem a `resources_loader`, e `migrator` nao cria indice
-- em tabela alheia — a 0006 morreu com `must be owner of table
-- endereco_segmentado` na validacao, antes de tocar no banco.
--
-- Rodar com `A2L_RECURSOS_DB_URL` (resources_loader), e nao com o migrator.

set local search_path = resources_root, public;

-- A CHAVE E O PROPRIO TEXTO DO ENDERECO, e e isso que torna a tabela um cache:
-- perguntar duas vezes pelo mesmo endereco devolve a mesma segmentacao, sem
-- pagar a chamada ao modelo de novo.
create unique index if not exists ux_endereco_segmentado
  on endereco_segmentado (endereco);

-- `record_id` e o identificador na fonte; `fonte` distingue duas origens que
-- usem a mesma numeracao.
create unique index if not exists ux_logradouro_ajustado
  on logradouro_ajustado (fonte, record_id);
