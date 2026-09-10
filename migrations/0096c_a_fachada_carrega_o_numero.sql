-- 0096c · a fachada vista carrega o número que estava com ela.
--
-- Regra do dono do produto em 10/09/2026: "se o número tiver sido identificado
-- e o nome, busca nas instalações um par pra eles".
--
-- O par só é possível se as duas coisas vierem JUNTAS. A IA já devolvia as
-- duas, mas em listas separadas — `numeros_vistos` e `fachadas_vistas` — e
-- casá-las depois seria adivinhar qual número pertence a qual letreiro numa
-- cena com quatro portas. Quem sabe é quem olhou: o número passa a ser campo
-- da própria fachada.
alter table radar_comercial.fachada_vista
  add column if not exists numero text,
  add column if not exists ligacao_par text,
  add column if not exists casado_em timestamptz;

comment on column radar_comercial.fachada_vista.numero is
  'O numero de porta que aparecia COM este letreiro, quando legivel.';
comment on column radar_comercial.fachada_vista.ligacao_par is
  'A ligacao do cadastro cujo endereco bate com (via da cena, numero). '
  'E um par candidato: ninguem confirmou que o letreiro e daquela ligacao.';

create index if not exists fachada_vista_par_ix
  on radar_comercial.fachada_vista (id_empresa, ligacao_par)
 where ligacao_par is not null;
