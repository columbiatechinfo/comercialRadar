-- =====================================================================
-- 0014 — a pergunta mudou: de "este comércio está aí?" para "há comércio aí?"
--
-- Até a leitura 2.0.0 a IA recebia um nome e conferia se ele aparecia na
-- fachada. Quem paga por isto, porém, é uma concessionária que quer saber onde
-- há comércio que o cadastro dela NÃO conhece — e o nome vindo da mineração é
-- justamente a parte que envelhece. Uma fachada com "Oficina do Zé" no endereço
-- onde a base dizia "Padaria Santa Rita" era reprovada, quando é o achado mais
-- valioso que existe: comércio ativo fora do cadastro.
--
-- Por isso APROVAR passa a ter duas formas:
--   `aprovar_especifico` — o comércio encontrado é o que a mineração conhecia
--   `aprovar_divergente` — há comércio, com OUTRO nome
-- e `reprovar` volta a significar o que a palavra diz: não há comércio nenhum
-- na cena. Não "não achei aquele".
-- =====================================================================

set local search_path = comercialradar, public;

-- ---------------------------------------------------------------------
-- Os novos estados
--
-- O CHECK antigo (`aprovar | reprovar | revisar`) recusaria toda linha da
-- leitura nova. Os valores antigos continuam aceitos: as anotações 2.0.0 já
-- gravadas não são reescritas, e reescrevê-las seria inventar uma distinção
-- que aquela leitura não fez.
-- ---------------------------------------------------------------------
alter table fachada_anotacao drop constraint if exists acao_recomendada_valida;
alter table fachada_anotacao add constraint acao_recomendada_valida
  check (acao_recomendada is null or acao_recomendada in
         ('aprovar_especifico', 'aprovar_divergente', 'reprovar', 'revisar',
          'aprovar'));   -- 'aprovar' = leitura 2.0.0, mantido por histórico

alter table fachada_anotacao
  -- TODO comércio que a cena mostrou, com o nome LIDO, a atividade aparente e
  -- a posição no quadro. Coluna jsonb e não tabela: são no máximo quatro por
  -- leitura, sempre lidos junto com a anotação e nunca consultados sozinhos.
  add column if not exists comercios_encontrados jsonb,
  -- O nome do comércio DIVERGENTE, promovido a coluna porque é o que a busca
  -- vai procurar e o que a tela do supervisor mostra primeiro. Filtro sobre
  -- jsonb varreria as 20 mil linhas.
  add column if not exists comercio_divergente   text;

-- A fila de divergentes é consultada por si só — "o que apareceu que o cadastro
-- não conhece?" — e por isso ganha índice próprio, com o tenant na frente.
create index if not exists fachada_divergente_idx
  on fachada_anotacao (tenant_id, comercio_divergente)
  where comercio_divergente is not null;

-- ---------------------------------------------------------------------
-- A FILA SEPARADA para o achado divergente
--
-- Tabela própria e não um `tipo` em `atribuicao` porque o trabalho é outro: no
-- fluxo normal o supervisor CONFIRMA um cadastro que já existe; aqui ele decide
-- se um estabelecimento desconhecido entra na base do cliente. As perguntas, os
-- motivos de recusa e quem pode decidir divergem — e um campo `tipo` numa
-- tabela só faria as duas telas brigarem pelas mesmas colunas.
-- ---------------------------------------------------------------------
create table if not exists atribuicao_divergente (
  id             serial primary key,
  poi_id         integer not null references pois(id) on delete cascade,
  anotacao_id    integer references fachada_anotacao(id) on delete set null,
  tenant_id      uuid    references tenants(id),
  supervisor_id  uuid    references usuarios(id),
  -- O que a IA leu na parede, congelado aqui: a anotação pode ser refeita e o
  -- que estava sendo julgado quando a fila foi montada não pode mudar embaixo
  -- de quem julga.
  nome_lido      text,
  atividade      text,
  -- pendente | aceito | recusado | devolvido
  status         text not null default 'pendente',
  motivo_generico text,
  motivo_escrito  text,
  observacao      text,
  atribuido_em   timestamp not null default now(),
  decidido_em    timestamp
);

alter table atribuicao_divergente drop constraint if exists divergente_status_valido;
alter table atribuicao_divergente add constraint divergente_status_valido
  check (status in ('pendente', 'aceito', 'recusado', 'devolvido'));

-- Recusar exige dizer POR QUÊ, como na fila normal: decisão sem motivo é
-- decisão que ninguém consegue auditar seis meses depois.
alter table atribuicao_divergente drop constraint if exists divergente_recusa_com_motivo;
alter table atribuicao_divergente add constraint divergente_recusa_com_motivo
  check (status <> 'recusado'
         or (coalesce(motivo_generico, '') <> ''
             and length(coalesce(motivo_escrito, '')) >= 10));

create index if not exists atribuicao_div_fila_idx
  on atribuicao_divergente (tenant_id, status, atribuido_em desc);
-- Qual visada do giro tirou qual nota. Sem isto as seis triagens por ponto
-- viram seis linhas indistinguíveis, e não dá para responder a pergunta que
-- justifica o giro: ele melhorou a leitura, ou só multiplicou o custo?
alter table fachada_triagem add column if not exists angulo text;

create index if not exists atribuicao_div_sup_idx
  on atribuicao_divergente (supervisor_id, status);
-- Um POI não deve entrar duas vezes na fila enquanto a primeira não for
-- decidida: sem isto, reler a base recria o item e o supervisor julga o mesmo
-- endereço várias vezes.
create unique index if not exists atribuicao_div_pendente_unico
  on atribuicao_divergente (poi_id) where status = 'pendente';
