-- A IMAGEM DE RUA SAI DO BANCO, E A DATA DELA ENTRA.
--
-- DUAS MUDANCAS, e as duas vem da mesma leitura de 07/09/2026.
--
-- 1 · `data_imagem` — QUANDO O GOOGLE FOTOGRAFOU, e nao quando nos capturamos.
--
-- `poi_evidencia.capturado_em` responde "quando nossa rodada tirou este print",
-- que nao e a pergunta que importa. A captura antiga ja guardava a outra data
-- em `streetview_imgs.data_captura`, PREENCHIDA NAS 83.287 LINHAS, e a IA nunca
-- a recebeu:
--
--     2025-10  37.044      2024-08  15.000      2024-09  11.660
--     2025-09   4.983      2024-07   4.338
--
-- Um terco das fachadas julgadas e de 2024. Uma casa fotografada em julho de
-- 2024 pode ter virado loja depois, e o modelo estava lendo aquilo como se
-- fosse hoje — sem ter como desconfiar, porque ninguem lhe disse a data.
--
-- 2 · o `storage_path` passa a ser usado de verdade.
--
-- A tabela sempre teve as duas colunas, `dados` e `storage_path`. A intencao
-- era Storage; o que aconteceu foi bytea: 63.308 linhas com bytes dentro do
-- Postgres, ZERO com caminho, e `poi_evidencia` virou a maior tabela do banco
-- com 53 GB de 104 GB. Decisao do dono do produto em 07/09/2026: as imagens
-- adequadas da captura antiga ja entram pelo Storage, e a leitura passa a
-- aceitar os dois — `imagens._de_linha` prefere o caminho e cai no bytea.
--
-- As 63.308 que ja estao em bytea ficam onde estao por enquanto: mover 53 GB e
-- operacao longa, e uma falha no meio deixaria metade em cada lugar.
alter table radar_comercial.poi_evidencia
    add column if not exists data_imagem text;

comment on column radar_comercial.poi_evidencia.data_imagem is
    'Quando o Google fotografou (AAAA-MM), e nao quando nos capturamos — '
    'capturado_em responde a segunda pergunta. Vem de streetview_imgs.'
    'data_captura na migracao da captura antiga, e do proprio Street View '
    'nas capturas novas.';
