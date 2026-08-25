-- 0035 — a chave única do Cadastur perde a empresa junto com a coluna.
--
-- A 0034 tornou as bases gerais renomeando `tenant_id` para `importado_por`. O
-- Postgres seguiu o rename dentro dos índices, e as chaves únicas ficaram assim:
--
--   cadastur_prestador  unique (importado_por, recurso_id, linha_origem)
--   cadastur_total_pf   unique (importado_por, dataset, uf, municipio, ref_periodo)
--
-- DUAS COISAS QUEBRAM AÍ, e nenhuma delas aparece como erro na hora certa.
--
-- 1. O `cadastur.py` faz `on conflict (tenant_id, recurso_id, linha_origem)`. A
--    coluna não existe mais com esse nome: a carga inteira morre com "column
--    tenant_id does not exist" — que é o mesmo tipo de erro que a etapa 3 já
--    deu hoje, por outro motivo.
--
-- 2. Pior, e silencioso: `importado_por` agora aceita NULO, e em índice único o
--    NULO é DISTINTO de outro NULO. Duas cargas da mesma base, ambas sem dono
--    declarado, passariam as duas — o upsert viraria insert e o mesmo prestador
--    entraria de novo a cada rodada. É exatamente a duplicação que a regra
--    "rodar de novo ACRESCENTA, não repete" existe para impedir.
--
-- A identidade de uma base pública não inclui quem a baixou: o prestador
-- `recurso_id` na linha `linha_origem` é o mesmo registro do MTur, tenha sido
-- trazido pela Corsan ou por outra concessionária.

alter table comercialradar.cadastur_prestador
  drop constraint if exists cadastur_prestador_tenant_id_recurso_id_linha_origem_key;

alter table comercialradar.cadastur_prestador
  add constraint cadastur_prestador_recurso_linha_key
  unique (recurso_id, linha_origem);

alter table comercialradar.cadastur_total_pf
  drop constraint if exists cadastur_total_pf_tenant_id_dataset_uf_municipio_ref_period_key;

alter table comercialradar.cadastur_total_pf
  add constraint cadastur_total_pf_dataset_periodo_key
  unique (dataset, uf, municipio, ref_periodo);

comment on constraint cadastur_prestador_recurso_linha_key
  on comercialradar.cadastur_prestador is
  'A identidade e do registro do MTur, nao de quem o baixou (migracao 0035).';
