-- 0095 · o setor da concessionária, porque o prompt não pode falar só de água.
--
-- O PROBLEMA, apontado pelo dono do produto em 10/09/2026: "o prompt está
-- direcionado para hidrômetros, que é o foco de um cliente do ramo de água,
-- mas não do ramo de energia elétrica ou qualquer outra utility".
--
-- Ele estava certo e o texto era explícito: "a companhia cobra este hidrômetro
-- como RESIDENCIAL", "o imóvel que ele abastece", "fica a 11 m do hidrômetro".
-- Nada disso serve para uma distribuidora de energia, e trocar as palavras na
-- mão a cada cliente novo é a receita para o dia em que metade do prompt fala
-- de um setor e metade de outro.
--
-- POR QUE UMA TABELA E NÃO UMA COLUNA EM `core.tb_empresas`: aquela tabela é
-- da plataforma inteira — identidade, recursos, bases — e é lida por sistemas
-- que não têm nada a ver com o radar. Uma coluna de vocabulário de prompt lá
-- seria o radar contaminando o cadastro comum.
--
-- O PADRÃO É `agua` porque é o do único cliente ativo hoje, e porque um padrão
-- errado é mais fácil de notar que um vazio: o prompt sai falando de
-- hidrômetro e alguém corrige. Sem linha nenhuma, `setor.py` cai no vocabulário
-- genérico — "o medidor", "a concessionária" —, que é correto para todos e
-- específico para ninguém.
create table if not exists radar_comercial.empresa_setor (
  id_empresa  uuid primary key
              references core.tb_empresas (id) on delete cascade,
  setor       text not null default 'agua'
              check (setor in ('agua', 'energia', 'gas', 'generico')),
  atualizado_em timestamptz not null default now()
);

comment on table radar_comercial.empresa_setor is
  'Qual utility a empresa opera. Define o vocabulário do prompt da IA: '
  'hidrômetro x medidor de energia, abastece x atende.';

alter table radar_comercial.empresa_setor enable row level security;

drop policy if exists p_setor_le on radar_comercial.empresa_setor;
create policy p_setor_le on radar_comercial.empresa_setor for select
  using (id_empresa = (select core.empresa_atual()));

insert into radar_comercial.empresa_setor (id_empresa, setor)
select id, 'agua' from core.tb_empresas
on conflict (id_empresa) do nothing;
