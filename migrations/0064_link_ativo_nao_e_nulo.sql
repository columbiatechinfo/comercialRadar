-- 0064 · um link é vivo ou morto, nunca "não sei"
--
-- O QUE QUEBROU. `poi_link.ativo` ficou NULL nas 72.341 linhas carregadas pelo
-- 0062. `where pl.ativo` descarta NULL do mesmo jeito que descarta FALSE, então
-- a fila de captura de página do iFood e do Airbnb devolveu ZERO — sem erro,
-- sem aviso, com o POI existindo, o link existindo e a categoria marcada.
--
-- POR QUE NINGUÉM PERCEBEU. A coluna nasceu no 0062 como `ativo boolean`, sem
-- default e sem NOT NULL, e as quatro cargas daquela migração simplesmente não
-- a listavam. Nenhum erro: o Postgres preencheu com NULL, que era o combinado.
-- O defeito estava na modelagem, não na carga — uma coluna cujo valor ausente
-- significa "morto" para todo `where` que a consulta.
--
-- A REGRA A PARTIR DAQUI: `ativo` é NOT NULL. Um link só vira FALSE quando
-- alguém verificou que a página morreu; enquanto ninguém verificou, ele está
-- vivo, que é a leitura honesta de "acabei de colher esta URL".

begin;

update radar_comercial.poi_link set ativo = true where ativo is null;

alter table radar_comercial.poi_link
    alter column ativo set default true,
    alter column ativo set not null;

commit;

select count(*) filter (where ativo)       as vivos,
       count(*) filter (where not ativo)   as mortos,
       count(*)                            as total
  from radar_comercial.poi_link;
