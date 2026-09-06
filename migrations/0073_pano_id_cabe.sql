-- `pano_id` NAO CABE EM 40 CARACTERES.
--
-- O identificador de panorama do Google tem tamanho variavel. Os antigos, de
-- coleta propria, ficam em 22; os de contribuicao trazem um blob em base64 que
-- chega a 44:
--
--     CAoSHENJQUJJaEJjS1VHMzZLWE1fLU90NUxpNko3akw.
--
-- A coluna era `varchar(40)`, e cada POI cujo panorama tem id longo morria com
-- `StringDataRightTruncation` DEPOIS de a foto ja ter sido tirada — trabalho
-- feito e jogado fora. Medido em 06/09/2026, na captura de Canoas: 34 falhas em
-- 3.723 pontos, 0,9%, e o log so dizia "falhas 34" ate o motivo passar a ser
-- impresso.
--
-- `text` e nao `varchar(64)`: no Postgres os dois tem o mesmo desempenho e o
-- mesmo armazenamento, e um numero escolhido a dedo aqui e so a proxima
-- surpresa esperando o Google publicar um id maior. Nao ha razao para limitar
-- um identificador de terceiro cujo formato nao controlamos.

alter table radar_comercial.streetview_imgs
  alter column pano_id type text;

comment on column radar_comercial.streetview_imgs.pano_id is
  'Identificador do panorama no Google. Tamanho variavel — 22 nos antigos, 44 '
  'nos de contribuicao. Era varchar(40) ate 06/09/2026 e truncava 0,9% das '
  'capturas depois de a foto ja estar tirada.';
