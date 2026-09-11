-- 0097 · a lista das ligações que já têm imagem, materializada.
--
-- POR QUE ELA EXISTE. A captura precisa saber, para cada POI, se ALGUMA fonte
-- da ligação dele já trouxe imagem — regra do dono do produto: "se a mesma
-- ligação tem 3 fontes de POI e 1 deles já tem as imagens, essa instalação já
-- pode ir pra avaliação".
--
-- Perguntar isso POR POI, dentro da consulta que monta a fila, custou 33
-- MINUTOS sem terminar: uma subconsulta aninhada sobre `ligacao_poi` dentro do
-- laço dos 300 mil POIs. O sintoma parecia "a captura travou"; a causa era a
-- consulta da fila.
--
-- `unlogged` porque ela é descartável: refazê-la custa segundos, e não vale
-- gerar WAL para um cache. Quem a preenche é `capturar_evidencia.alvos`, no
-- começo de cada execução — ela reflete o estado daquele instante, e é assim
-- que tem de ser.
create unlogged table if not exists radar_comercial.tmp_lig_com_imagem (
  ligacao text primary key
);

comment on table radar_comercial.tmp_lig_com_imagem is
  'Cache da execucao da captura: ligacoes cuja alguma fonte ja tem imagem. '
  'Preenchida por capturar_evidencia.alvos; descartavel.';

grant select, insert, delete, truncate
  on radar_comercial.tmp_lig_com_imagem to app_user;
