-- =====================================================================
-- 0009 — a revisão que o supervisor preenche
--
-- Até aqui a decisão era só um veredito: aprovado, reprovado, devolvido. Mas o
-- que o cliente recebe é a MUDANÇA DE CADASTRO, e mudança de cadastro exige
-- campo preenchido — qual é o uso observado, qual é a atividade, quantas
-- economias comerciais há no imóvel. Aprovar sem isso produz um dossiê que
-- afirma "é comercial" sem dizer comércio de quê, e a concessionária não
-- reclassifica tarifa com isso na mão.
--
-- `jsonb` e não vinte colunas: o conjunto de campos ainda vai mudar com o uso, e
-- cada mudança viraria migração de esquema numa tabela que está em produção. O
-- que é OBRIGATÓRIO, esse sim, está preso por CHECK abaixo.
-- =====================================================================

set local search_path = comercialradar, public;

alter table atribuicao add column if not exists revisao jsonb;

comment on column atribuicao.revisao is
  'O que o supervisor preencheu ao decidir. Chaves em uso: uso (comercial|misto|'
  'residencial|indefinido), atividade, nome_confirmado, cnpj, telefone, '
  'economias_comerciais, endereco_confere, endereco_corrigido, visita_necessaria, '
  'observacao_tecnica.';

-- APROVAR EXIGE OS DOIS CAMPOS QUE SUSTENTAM A RECLASSIFICAÇÃO.
--
-- Mesma postura das travas de reprovar e devolver do 0004: a regra mora no
-- banco. Se ela vivesse só na tela, o primeiro cliente de API que ninguém
-- previu aprovaria em massa com a revisão vazia, e a fila registraria
-- aprovação sem nada a mostrar.
--
-- `not valid` porque as aprovações que já existem foram feitas antes de o campo
-- existir — invalidá-las retroativamente reescreveria histórico de decisão, que
-- é justamente o que a fila não pode fazer. A trava vale das próximas em diante.
alter table atribuicao drop constraint if exists aprova_exige_revisao;
alter table atribuicao add constraint aprova_exige_revisao check (
  status <> 'aprovado'
  or (length(btrim(coalesce(revisao ->> 'uso', ''))) > 0
      and length(btrim(coalesce(revisao ->> 'atividade', ''))) >= 3)
) not valid;
