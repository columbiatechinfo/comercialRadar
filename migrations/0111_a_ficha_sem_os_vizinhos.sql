-- 0111 · a ficha do Maps sem as fotos dos vizinhos, com data e com os
--        "Resultados da Web" (14/09/2026)
--
-- Decisao do dono do produto. A coleta da ficha (`minerar_placeid.py`) gravava
-- toda imagem da pagina, e a pagina mostra fotos de OUTROS lugares ("Lugares
-- tambem pesquisados", "Hoteis semelhantes por perto"). Sem foto propria, a IA
-- recebia a foto do vizinho de categoria como "foto publicada no Google" do
-- lugar. A partir daqui so entram as fotos do proprio lugar, cada uma dizendo de
-- onde veio, e a data lida na galeria.
--
--   images_urls.secao            capa | fotos | avaliacao | recentes | galeria.
--                                Nulo = gravada antes de 14/09/2026, sem filtro.
--   maps_data.resultados_web     os cartoes da secao "Resultados da Web" da ficha:
--                                [{url, migalha, titulo, trecho}]. E o que a web
--                                achou perto do nome — prova para ler, nao cadastro.
--   maps_data.resultados_web_estado  lido | sem_cartao | vazio | sem_secao |
--                                indisponivel (o Google nao exibiu; tentar de novo).
--   maps_data.resultados_web_em  quando foi lida.
--
-- So colunas anulaveis: o codigo em producao continua gravando sem conhece-las.
--
-- `lock_timeout`: o ALTER pede trava exclusiva por um instante, e se esperar
-- atras de uma transacao longa, TODA leitura que chegar depois espera junto
-- (a fila de trava e FIFO). Melhor falhar em 5 s e tentar de novo.
set lock_timeout = '5s';

alter table radar_comercial.images_urls
    add column if not exists secao text;

alter table radar_comercial.maps_data
    add column if not exists resultados_web jsonb,
    add column if not exists resultados_web_estado text,
    add column if not exists resultados_web_em timestamptz;

comment on column radar_comercial.images_urls.secao is
    'De onde da ficha do Maps a foto veio: capa, fotos, avaliacao, recentes, galeria. Nulo = coleta anterior a 14/09/2026, que gravava tambem foto de outros lugares.';
comment on column radar_comercial.maps_data.resultados_web is
    'Cartoes da secao "Resultados da Web" da ficha do Maps: [{url, migalha, titulo, trecho}].';
