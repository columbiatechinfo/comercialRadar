-- 0047_pois_coordenada_anterior.sql
--
-- A COORDENADA DE ANTES, PARA A CORRECAO PODER SER DESFEITA.
--
-- `corrigir_coordenada.py` reposiciona o POI pela porta do endereco (CNEFE) e,
-- antes de sobrescrever `maps_lat/lng`, guarda a coordenada que estava la em
-- `coord_anterior_lat/lng` — para dar para auditar e reverter. So que as duas
-- colunas nunca foram criadas: o `feat(coordenada)` que passou a grava-las nao
-- veio com a migracao. Resultado, medido em 03/09/2026 numa run de Canoas: o
-- UPDATE estourava com `column p.coord_anterior_lat does not exist`, a etapa de
-- correcao de coordenada falhava INTEIRA (exit 1) e o pipeline seguia sem ela —
-- as coordenadas tortas nunca eram corrigidas, sem ninguem ver, porque a falha
-- era tolerada.
--
-- `double precision` para casar com `maps_lat/lng` e `lat_origem/lng_origem`.

alter table radar_comercial.pois
  add column if not exists coord_anterior_lat double precision,
  add column if not exists coord_anterior_lng double precision;
