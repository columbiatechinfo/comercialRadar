-- 0092 · vinculo nao sobrevive ao POI.
--
-- ONZE VINCULOS VIVOS APONTAVAM PARA `poi_id` QUE NAO EXISTE. Achados em
-- 10/09/2026 porque sobraram sem `aceito_por` depois da revisao: a consulta da
-- regra faz `join radar_comercial.pois`, entao referencia pendurada nao chega
-- a ser examinada — e continua contando como vinculo vivo em toda consulta que
-- so olha `descartado_em`.
--
-- E A TERCEIRA VEZ NO MESMO DIA que uma linha escapa por um `join` interno:
-- primeiro o POI fundido, depois a cidade fora do `--cidade`, agora o POI
-- apagado. Todas com o mesmo formato — a consulta que aplica a regra so ve o
-- que o `join` deixa passar, e o que ela nao ve permanece vivo por omissao.
--
-- POR QUE `on delete cascade`, E NAO UMA GUARDA NO CODIGO. Seis lugares
-- apagam POI — `server.py` (dois), `conferir_municipio.py`,
-- `scripts/consolidar_duplicatas.py` e os testes — e nenhum deles limpa
-- `ligacao_poi`. Uma guarda teria de ser repetida nos seis e lembrada no
-- setimo. O banco garante de uma vez: vinculo para um POI que deixou de
-- existir nao e vinculo descartado, e' vinculo que nunca teve sujeito.
--
-- OS ONZE SAO APAGADOS, e nao marcados como descartados: `descartado_motivo`
-- serve para explicar uma decisao sobre um par que existe. Aqui nao ha par.
delete from radar_comercial.ligacao_poi lp
 where not exists (select 1 from radar_comercial.pois p where p.id = lp.poi_id);

alter table radar_comercial.ligacao_poi
  add constraint ligacao_poi_poi_fk
  foreign key (poi_id) references radar_comercial.pois (id)
  on delete cascade;
