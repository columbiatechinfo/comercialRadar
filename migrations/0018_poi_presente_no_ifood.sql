-- 0018 — marcar no POI que ele também aparece no iFood
--
-- Estar no iFood é SINAL DE VIDA, e é a informação que nenhuma outra base do
-- projeto tem: a Receita diz que a empresa existe no cadastro, o CNEFE diz que
-- o endereço existe, o Maps diz que já existiu um ponto ali. O iFood diz que
-- alguém está operando HOJE, porque loja fechada sai do aplicativo.
--
-- Por isso é coluna e não apenas uma linha em `cruzamento`: a pergunta
-- "quais dos meus POIs estão comprovadamente ativos?" precisa ser filtro barato
-- na listagem, não junção.

alter table comercialradar.pois
  add column if not exists presente_no_ifood boolean not null default false,
  -- quando foi visto lá pela última vez; um sinal de vida de seis meses atrás
  -- vale menos que o de ontem, e sem a data ninguém consegue distinguir
  add column if not exists ifood_visto_em timestamptz;

-- `tenant_id` primeiro, como em todo índice da casa: a RLS é avaliada por
-- linha e sem isso a política vira varredura completa.
create index if not exists ix_pois_ifood
  on comercialradar.pois (tenant_id, presente_no_ifood)
  where presente_no_ifood;

comment on column comercialradar.pois.presente_no_ifood is
  'Casou com uma loja em ifood_merchant. Sinal de operação atual, não de cadastro.';
