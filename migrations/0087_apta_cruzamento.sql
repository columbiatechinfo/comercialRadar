-- 0087 · a coluna que decide quem passa pelo enriquecimento caro.
--
-- REGRA DO DONO DO PRODUTO, 09/09/2026: "toda a base deve ser cruzada com os
-- POIs localizados, mas apenas os marcados como sim passam pelos passos de
-- enriquecimento (Google Maps, Street View, Instagram, avaliacao da IA); os
-- demais apenas fazem o cruzamento e, seguindo a regra de endereco estrito, sao
-- ligados a instalacao equivalente sem passar pela parte cara".
--
-- POR QUE TRES ESTADOS E NAO DOIS. `boolean` com `not null default false`
-- pareceria mais simples e seria pior: nao distinguiria "o cliente marcou NAO"
-- de "a base veio sem essa coluna". As duas coisas levam ao mesmo lugar hoje —
-- nao enriquece —, mas por motivos opostos, e so uma delas e um pedido de
-- preenchimento. NULL e a ausencia; false e a decisao.
--
-- VAZIO NAO ENRIQUECE, decisao do mesmo dia: erro de preenchimento custa uma
-- ligacao nao enriquecida, e nao uma fatura de extracao.
alter table resources_root.cadastro_corsan
    add column if not exists apta_cruzamento boolean;

comment on column resources_root.cadastro_corsan.apta_cruzamento is
    'O cliente marcou esta ligacao para o enriquecimento caro? NULL = a base '
    'nao declarou; false = declarou que nao. Ausencia e decisao levam ao mesmo '
    'lugar, mas so uma delas pede preenchimento.';

-- O INDICE E PARCIAL, e so cobre o `true`.
--
-- A fila do enriquecimento pergunta "quais estao aptas", e as aptas sao a
-- minoria. Um indice cheio guardaria 102 mil linhas para responder sobre
-- algumas dezenas de milhares; o parcial guarda so as que interessam e cabe em
-- memoria. `id_empresa` vem primeiro porque toda consulta do produto e por
-- empresa — regra do CLAUDE.md.
create index if not exists ix_corsan_apta
    on resources_root.cadastro_corsan (id_empresa, cidade)
 where apta_cruzamento;
