-- 0098 · o bairro que a fonte PUBLICOU passa a ser guardado, e o da coordenada também.
--
-- O DEFEITO. `resolver_logradouro` gravava em `logradouro_resolvido.bairro` o
-- bairro que a PENEIRA devolvia, e não o que a fonte escreveu. A peneira do
-- CEP, que resolve dois terços dos POIs, não devolve bairro nenhum; a do
-- endereço devolve o bairro do primeiro registro do CNEFE daquela rua, que
-- muitas vezes é outro bairro. O libpostal separava o bairro publicado
-- (`suburb`) e ninguém o lia.
--
-- Medido em 11/09/2026, nos POIs vivos de Canoas, quem PUBLICA bairro contra
-- quem chegava com bairro nesta tabela:
--
--     Receita    58.600 publicam     5.135 chegavam
--     IBGE       16.161              303
--     Maps        9.813            5.430
--     iFood         949              116   (109 destes, errados)
--     Airbnb        267               27
--     Cadastur      178               13
--
-- Apontado pelo dono do produto: "se não guardou a culpa é do processo".
--
-- AS QUATRO COLUNAS. `bairro` continua sendo o que a regra de vínculo lê — o
-- bairro FINAL. As outras dizem de onde ele veio, para que ninguém precise
-- adivinhar depois:
--
--   bairro_publicado  o que a fonte escreveu, limpo de lixo ("?", "-", o nome
--                     da cidade). Nulo quando a fonte não publicou.
--   bairro_reverso    o do geocodificador reverso (Nominatim) sobre a
--                     coordenada do POI. Só é consultado quando há dúvida.
--   bairro_fonte      publicado | reverso | peneira — qual dos três virou o final.
--   bairro_motivo     por que houve dúvida, quando houve.
--
-- POR QUE O NOMINATIM, e não o OSRM. O OSRM responde com o nó da malha e o
-- nome da rua; não sabe o que é bairro. Medido em 600 ligações de Canoas, com o
-- bairro da Corsan como gabarito: Nominatim 558 iguais, Photon 541, registro
-- mais próximo do CNEFE 533. E responde 600 consultas em 1,5 s.
alter table radar_comercial.logradouro_resolvido
    add column if not exists bairro_publicado text,
    add column if not exists bairro_reverso text,
    add column if not exists bairro_fonte text,
    add column if not exists bairro_motivo text;

alter table radar_comercial.logradouro_resolvido
    drop constraint if exists logradouro_resolvido_bairro_fonte_valida;
alter table radar_comercial.logradouro_resolvido
    add constraint logradouro_resolvido_bairro_fonte_valida
    check (bairro_fonte is null
           or bairro_fonte in ('publicado', 'reverso', 'peneira'));

comment on column radar_comercial.logradouro_resolvido.bairro is
  'Bairro FINAL, o que a regra de vinculo le. Ver bairro_fonte.';
comment on column radar_comercial.logradouro_resolvido.bairro_publicado is
  'Bairro como a fonte escreveu, limpo de lixo. Nulo se a fonte nao publicou.';
comment on column radar_comercial.logradouro_resolvido.bairro_reverso is
  'Bairro do Nominatim sobre a coordenada do POI, consultado so na duvida.';
comment on column radar_comercial.logradouro_resolvido.bairro_fonte is
  'publicado | reverso | peneira: qual virou o bairro final.';
comment on column radar_comercial.logradouro_resolvido.bairro_motivo is
  'Por que o bairro publicado nao bastou, quando nao bastou.';

-- AS FONTES PASSAM A GUARDAR O BAIRRO QUE TRAZEM.
--
-- As três bases estaduais têm bairro no Parquet da extração (`bairro`: OSM
-- addr:suburb ou leitura do endereço) e ele nunca entrou no banco. Em Canoas:
-- OSM 347 de 2.169 com bairro de verdade; Foursquare traz o NOME DA CIDADE no
-- campo (10.003 "Canoas" em 10.373); Overture, nenhum. Guarda-se o que a fonte
-- disse — a limpeza é do resolvedor, que sabe o nome da cidade.
alter table radar_comercial.osm_data        add column if not exists bairro text;
alter table radar_comercial.overture_data   add column if not exists bairro text;
alter table radar_comercial.foursquare_data add column if not exists bairro text;

-- A RECEITA GANHA O QUE A BASE BRUTA TEM E O RADAR NÃO TINHA.
--
-- `receita_data` existia vazia. A situação cadastral só morava em
-- `resources_root.rf_estabelecimentos` — 72,8 milhões de linhas, 14 GB, sem
-- índice —, e por isso nenhum POI da Receita sabia se estava ativo. Regra do
-- dono do produto em 11/09/2026: da Receita, só os ativos são consultados.
-- O complemento (sala, apto, loja) vem junto: é o que distingue unidades num
-- mesmo número.
alter table radar_comercial.receita_data
    add column if not exists bairro text,
    add column if not exists complemento text;
