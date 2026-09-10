-- 0091 · por que este vinculo sobreviveu.
--
-- ATE AQUI SO O DESCARTE TINHA MOTIVO. `descartado_motivo` diz por que um par
-- caiu, e nada dizia por que um par ficou. Perguntado pelo dono do produto em
-- 10/09/2026 — "vinculos feitos por criterio de nome ou airbnb tem quais
-- niveis pra gerar confirmacao?" — a resposta teve de ser reconstruida na mao,
-- deduzindo a classe de `mesmo_endereco`, `mesmo_numero` e `fonte`. Deducao
-- nao e registro: ela reconstroi o criterio de HOJE sobre um vinculo gravado
-- ONTEM, e erra calada quando a regra muda.
--
-- POR QUE COLUNA NOVA, E NAO `origem`. `origem` ja tem dono: ela guarda como o
-- CRUZAMENTO formou o par ('criterio', 'endereco_publicado', 'telhado') —
-- 96.397 vinculos dependem desse valor. Reaproveita-la apagaria a unica
-- resposta a "de onde veio este par" para gravar "por que ele ficou", que sao
-- duas perguntas diferentes sobre o mesmo vinculo.
--
-- OS VALORES vem de `regra_vinculo.aceitar`, que ja os devolvia e ninguem
-- gravava: endereco_exato, nome_de_ancora, airbnb_telhado_nome.
alter table radar_comercial.ligacao_poi
  add column if not exists aceito_por text;

comment on column radar_comercial.ligacao_poi.aceito_por is
  'Qual criterio de regra_vinculo.aceitar deixou este vinculo entrar. '
  'Nulo = gravado antes de 10/09/2026, quando o aceite nao tinha registro.';

-- O INDICE PARCIAL, e nao o total. Vinculo descartado nunca e filtrado por
-- criterio de aceite — quem olha descarte olha `descartado_motivo`. Indexar so
-- os vivos deixa o indice do tamanho do que se consulta.
create index if not exists ligacao_poi_aceito_por_ix
  on radar_comercial.ligacao_poi (aceito_por)
 where descartado_em is null;
