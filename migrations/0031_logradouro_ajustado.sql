-- 0031 — a forma canônica do logradouro, marcada pela skill `ajuste-logradouro`.
--
-- O QUE ESTA TABELA É
--
-- A chave de junção das bases cruzadas, unificada. `pois`, `cadastro_cliente`,
-- `ifood_merchant` e o CNEFE escrevem a mesma rua de jeitos diferentes; aqui
-- cada registro ganha a forma canônica sugerida, com o TIER dizendo quanto
-- confiar nela.
--
-- O QUE ELA NÃO É: substituição do original. A skill MARCA e nunca sobrescreve,
-- e essa propriedade tem de sobreviver à volta para o banco — por isso
-- `logradouro_original` viaja junto e a tabela é separada das tabelas de
-- cadastro. Quem aplica a correção é o processo a jusante, olhando o tier.
--
-- `tier` É O CAMPO OPERACIONAL, e é o que evita os dois erros opostos:
--
--   CONFIRMA  dicionário fechado em posição fixa (`R` -> `RUA`) — aplicável em massa
--   ALTA      léxico com prova: support >= 2 imóveis DISTINTOS — aplicável com amostragem
--   REVISAR   houve PERDA DE TEXTO (segmento entre parênteses, poda) — fila humana
--   HUMANO    sobrou pouco ou nada de via — fila humana, prioridade
--
-- Sem graduação, quem consome não tem como aplicar em massa o que é seguro e
-- reter o que é lossy — e acaba fazendo uma coisa só com os dois.
--
-- `run_id` É O QUE TORNA A MARCAÇÃO AUDITÁVEL. A skill publica cada execução
-- num diretório imutável com manifesto e hashes; guardar o id aqui é o que
-- permite responder "de onde veio esta forma" três meses depois, e reprocessar
-- só o que um run específico marcou.

create table if not exists comercialradar.logradouro_ajustado (
  fonte                   text not null,
  record_id               text not null,
  scope_id                text not null,
  logradouro_original     text,
  logradouro_marcado      text,
  tier                    text,
  origem                  text,
  risco                   text,
  numero_canonico         text,
  complemento_organizado  text,
  run_id                  text,
  ajustado_em             timestamptz not null default now(),
  primary key (fonte, record_id)
);

-- O uso real é "me dê a forma canônica desta rua neste município" — o cruzamento
-- entre bases junta por (scope, forma marcada), não por registro.
create index if not exists ix_logradouro_ajustado_marcado
  on comercialradar.logradouro_ajustado (scope_id, logradouro_marcado);

-- A fila humana é consulta de rotina, e é pequena perto da tabela: índice
-- parcial em vez de varrer tudo para achar o que precisa de gente.
create index if not exists ix_logradouro_ajustado_fila
  on comercialradar.logradouro_ajustado (scope_id, tier)
  where tier in ('REVISAR', 'HUMANO');

-- SEM `tenant_id`, pelo mesmo motivo da 0030 e com a mesma ressalva escrita.
--
-- A regra da 0029 é condicional: tabela QUE TEM `tenant_id` precisa de RLS,
-- policy, gatilho e índice. Esta não tem, e é decisão.
--
-- "RUA CORONEL MARCOS é a forma canônica de R. Cel. Marcos em Canoas" é fato
-- sobre a via pública, não sobre a carteira de ninguém. Carimbar tenant faria
-- cada concessionária pagar de novo o mesmo aprendizado e permitiria que a
-- mesma rua tivesse duas verdades no banco — que é exatamente o problema que
-- esta tabela existe para acabar.
--
-- O vínculo com o cliente está no `record_id`, que aponta para `pois` e
-- `cadastro_cliente` — essas sim com `tenant_id` e RLS. Quem não pode ver o
-- registro não chega até aqui.
comment on table comercialradar.logradouro_ajustado is
  'Forma canonica do logradouro por (fonte, record_id), marcada pela skill '
  'ajuste-logradouro. Nunca sobrescreve o original. Sem tenant_id de proposito '
  '(ver migracao 0031): e fato sobre via publica, nao dado de cliente.';
