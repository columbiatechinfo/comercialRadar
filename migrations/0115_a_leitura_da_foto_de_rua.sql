-- 0115 · a LEITURA da foto de rua: placas, letreiros e sinais, lidos numa chamada so da imagem (15/09/2026).
--
-- Medido no lote 1 do julgamento v3: a IA que julga com o texto inteiro na frente ignorava placa e
-- letreiro visiveis (pet shop, "KALIVAS", "CAFE", parede pintada de grafica) — 0 de 30 fotos com sinal.
-- Uma chamada so com a foto e a tarefa de transcrever o que esta escrito achou 5. A leitura e evidencia:
-- fica gravada junto da foto e vai em texto para o julgamento; rejulgar nao le de novo.
-- Quem escreve: `ler_fotos_de_rua.py`. Quem le: `avaliar_enxuto.montar_leve`.
set lock_timeout = '5s';
alter table radar_comercial.poi_evidencia add column if not exists leitura jsonb;
alter table radar_comercial.poi_evidencia add column if not exists leitura_em timestamptz;
alter table radar_comercial.ligacao_evidencia add column if not exists leitura jsonb;
alter table radar_comercial.ligacao_evidencia add column if not exists leitura_em timestamptz;
