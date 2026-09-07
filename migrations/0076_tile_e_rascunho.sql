-- O TILE E RASCUNHO, E NAO ACERVO.
--
-- Decisao do dono do produto em 07/09/2026: o tile e recapturado toda vez que
-- a area roda, entao guarda-lo nao poupa nada — so ocupa disco e Storage.
-- Eram 51.504 linhas de catalogo apontando para 50.880 arquivos e 1,1 GB em
-- `capturas/`.
--
-- A TABELA ERA UMA COPIA DO QUE O DIRETORIO JA SABIA. O que ela precisava
-- responder e "que tile cobre este ponto?", e para isso o disco basta: o nome
-- do arquivo carrega o centro (`tile_r_008_-29.91725_-51.19778.webp`) e o
-- `_tiles.json` ao lado guarda a caixa que o proprio mapa reportou ter
-- desenhado. Provado em 07/09/2026: os 50.880 tiles foram reencontrados a
-- partir do disco, com caixa e zoom corretos, sem consultar esta tabela.
--
-- QUEM ESCREVIA E QUEM LIA, os tres ja mudaram no mesmo commit:
--   · `telhados.registrar`      escrevia  -> agora so lista
--   · `telhados.carregar_tiles` lia       -> agora le o disco
--   · `imagens_para_storage.subir_tiles`  -> nao sobe mais nada
--
-- E A COLUNA `ligacao_poi.tile_id` VAI JUNTO, porque apontava para ca. Ela
-- estava preenchida em 98 das 356.761 linhas, e nenhuma procedencia se perde:
-- conferido que as 98 ja trazem `origem = 'telhado'` e o texto de
-- `suspeita_motivo` ("telhado de 697 m2; sem semelhanca de dados"), que
-- sobrevivem ao tile e dizem mais do que um id apontando para arquivo apagado.
alter table radar_comercial.ligacao_poi drop column if exists tile_id;

drop table if exists radar_comercial.tile_captura;
