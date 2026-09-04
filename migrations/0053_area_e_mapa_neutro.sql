-- 0053 — a área de trabalho é um mapa neutro, e não dado de cliente
--
-- O DEFEITO, MEDIDO EM 03/09/2026
--
-- O operador desenhou uma área em Canoas e mandou minerar. A run morreu em três
-- segundos com "Área 'area_atual' não existe no banco. Desenhe a área no mapa
-- antes." — e a área estava lá, desenhada, salva, com cinco vértices.
--
-- O que aconteceu: quem salvou foi `servidor.dados@a2lsolucoes.com`, da A2L, e
-- o gatilho carimbou `id_empresa = A2L`. O pipeline roda como
-- `pipeline@corsan.servico.invalido`, da Corsan. A política filtrava por
-- empresa, a linha ficou invisível, e a mensagem apontou para o lugar errado —
-- disse "não existe" quando o certo era "existe e você não pode ver".
--
-- Isso é regressão da migração 0050, que moveu o dado do produto para a empresa
-- do cliente. Ela moveu as três áreas que existiam; a área NOVA continuou
-- nascendo na empresa de quem desenhou.
--
-- O CONSERTO NÃO É MOVER A LINHA, É CORRIGIR O MODELO
--
-- Área não é dado de cliente. Um polígono sobre Canoas não fala sobre a Corsan,
-- não veio dela e não revela nada dela: é uma seleção num mapa que já é
-- público. Quem tem dono neste sistema são duas coisas — o POI mineirado e a
-- base cadastral do cliente —, e a área não é nenhuma das duas.
--
-- Tratá-la como dado de cliente criou uma pergunta sem resposta boa: de quem é
-- a área quando o suporte da A2L desenha um retângulo para minerar para a
-- Corsan? Da A2L, que desenhou? Da Corsan, para quem se minera? A pergunta só
-- existia porque a premissa estava errada.
--
-- `id_empresa` FICA NA TABELA, e isso não é meio-termo: ela deixa de ser
-- FILTRO e passa a ser REGISTRO. Continua respondendo "quem desenhou isto",
-- que é informação útil numa lista de áreas salvas; só não decide mais quem
-- enxerga.

set search_path to radar_comercial, public;

drop policy if exists p_area_trabalho on area_trabalho;
drop policy if exists area_trabalho_por_empresa on area_trabalho;
drop policy if exists area_trabalho_ver on area_trabalho;
drop policy if exists area_trabalho_criar on area_trabalho;
drop policy if exists area_trabalho_alterar on area_trabalho;
drop policy if exists area_trabalho_apagar on area_trabalho;

-- QUALQUER SESSÃO IDENTIFICADA, e não "qualquer um". A RLS continua ligada e a
-- tabela continua fechada para conexão anônima; o que sai é o recorte por
-- empresa, não a exigência de estar autenticado.
create policy area_trabalho_mapa_neutro on area_trabalho
    using (true) with check (true);

comment on table area_trabalho is
    'Polígonos de trabalho: municípios carregados da malha do IBGE e desenhos '
    'manuais. MAPA NEUTRO — não é dado de cliente e não é filtrado por '
    'empresa. `id_empresa` registra quem desenhou, e não quem pode ver.';

comment on column area_trabalho.id_empresa is
    'Quem desenhou. REGISTRO, não filtro: a política desta tabela não olha '
    'para esta coluna (migração 0053).';
