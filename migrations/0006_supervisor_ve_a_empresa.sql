-- =====================================================================
-- 0006 — o supervisor volta a ver a empresa inteira
--
-- Correção de interpretação minha, apontada pelo usuário em 13/08/2026.
--
-- No 0004 eu fiz a atribuição RESTRINGIR A VISÃO: o supervisor só enxergava os
-- POIs que lhe tinham sido enviados. Está errado. A atribuição define a
-- OBRIGAÇÃO — o que ele precisa avaliar —, não o limite do que pode consultar.
--
-- A diferença é prática, não filosófica. Um supervisor decidindo sobre um ponto
-- precisa olhar a vizinhança: se o estabelecimento ao lado já foi aprovado, se
-- a rua inteira é comercial, se aquele CNPJ aparece em outro endereço. Cegá-lo
-- ao redor transforma cada decisão num palpite sobre um ponto isolado.
--
-- O que continua valendo: ele só DECIDE o que lhe foi atribuído. Isso vive na
-- policy de `atribuicao`, que não muda aqui, e no 404 de `/api/fila/{id}/decidir`
-- para item que não é dele.
--
--   ver     → da empresa (RLS por tenant)
--   opinar  → do que lhe coube (RLS por supervisor_id na atribuição)
-- =====================================================================

set local search_path = comercialradar, public;

drop policy if exists tenant_isolado on pois;
create policy tenant_isolado on pois
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);
