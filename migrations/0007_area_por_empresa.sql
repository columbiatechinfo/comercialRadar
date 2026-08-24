-- =====================================================================
-- 0007 — a área de trabalho passa a ser POR EMPRESA
--
-- `area_trabalho` nasceu com `nome` como chave primária, de quando o sistema
-- atendia um cliente só. Com várias empresas isso vira colisão: a Corsan e a
-- Aegea-PI querem, cada uma, a sua "area_atual".
--
-- O efeito seria pior que um erro: a RLS esconde a linha da outra empresa, mas
-- a UNICIDADE continua valendo sobre a tabela inteira. A segunda empresa a
-- desenhar receberia "chave duplicada" para um registro que ela nem enxerga —
-- ou, com o ON CONFLICT DO UPDATE de `salvar_area`, sobrescreveria a área da
-- primeira sem que ninguém visse acontecer.
-- =====================================================================

set local search_path = comercialradar, public;

alter table area_trabalho drop constraint if exists area_trabalho_pkey;
alter table area_trabalho add  constraint area_trabalho_pkey primary key (tenant_id, nome);
