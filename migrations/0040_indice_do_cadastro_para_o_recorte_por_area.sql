-- 0040 — índice do cadastro para o recorte por área desenhada
--
-- POR QUE ELE EXISTE
--
-- "Já comerciais no cadastro" passou a respeitar a área desenhada, e o recorte
-- corta pela caixa envolvente sobre `cadastro_cliente.lat/lng` — 102.065
-- ligações, 100% com coordenada.
--
-- O `ix_cad_geo` já existia em `(lat, lng)`, e é ele que sai: SEM `tenant_id`
-- na frente. A regra deste banco é `tenant_id` como primeira coluna de todo
-- índice, porque a RLS filtra por empresa antes de qualquer outra coisa — um
-- índice geográfico global faz o Postgres varrer as ligações das outras
-- empresas para depois descartá-las. Com um cliente por município isso passa
-- despercebido; com vários, é a diferença entre um index scan e varrer a base
-- inteira de todo mundo.
--
-- A ORDEM IMPORTA: cria o novo ANTES de apagar o velho, para que nenhuma
-- consulta em voo fique sem índice no meio do caminho.

CREATE INDEX IF NOT EXISTS ix_cad_geo_por_tenant
    ON cadastro_cliente (tenant_id, lat, lng)
    WHERE lat IS NOT NULL AND lng IS NOT NULL;

DROP INDEX IF EXISTS ix_cad_geo;
