-- 0093 · os tres status oficiais, no score.
--
-- REGRA DO DONO DO PRODUTO, 21/09 e 10/09/2026: os status do sistema inteiro
-- sao SIM, SIM_COM_ANALISE_HUMANA e NAO — os mesmos tres que a base cadastral
-- ja usa na coluna `qualificacao`, escritos do mesmo jeito. Um vocabulario so
-- para a decisao que entra e para a flag que sai.
--
-- O SEGUNDO STATUS NAO E UM MEIO-TERMO DE CONFIANCA, e sim um recorte que o
-- cliente filtra: "e igual ao sim, mas o usuario consegue filtrar o que deseja
-- ver e aplicar em campo". Quem decide continua sendo ele.
--
-- QUANDO O SIM VIRA SIM_COM_ANALISE_HUMANA, hoje: quando a unica coisa que
-- liga o POI aquela ligacao e a semelhanca de nome com outro POI da mesma rua
-- e do mesmo telhado — `aceito_por = 'nome_de_ancora'` — e nenhum vinculo
-- daquela ligacao identificou a porta. Decisao de 10/09/2026: "ainda assim nao
-- vira aprovacao, e sim SIM_COM_ANALISE_HUMANA".
--
-- `status_motivo` existe para a lista crescer sem migracao nova: o dia em que
-- outra condicao rebaixar um SIM, ela entra como outro texto aqui.
alter table radar_comercial.ligacao_score
  add column if not exists status text,
  add column if not exists status_motivo text;

alter table radar_comercial.ligacao_score
  drop constraint if exists ligacao_score_status_ck;
alter table radar_comercial.ligacao_score
  add constraint ligacao_score_status_ck
  check (status is null or status in ('SIM', 'SIM_COM_ANALISE_HUMANA', 'NAO'));

comment on column radar_comercial.ligacao_score.status is
  'A flag oficial: SIM, SIM_COM_ANALISE_HUMANA ou NAO. O sistema mede e '
  'sinaliza; quem decide converter e o cliente.';

create index if not exists ligacao_score_status_ix
  on radar_comercial.ligacao_score (id_empresa, status, total desc);
