-- 0088 · os TRES status oficiais da qualificacao.
--
-- A 0087 gravou isto como `boolean`, e estava errado. Booleano responde "passa
-- pelo enriquecimento?" e apaga a pergunta que o dono do produto faz depois:
-- QUAL DELAS eu quero ver e levar a campo. Sao 37.796 ligacoes marcadas
-- "SIM, COM ANALISE HUMANA" — 37% da base — que no pipeline se comportam como
-- qualquer SIM, mas que o usuario precisa poder separar na tela.
--
-- OS TRES, decididos em 09/09/2026:
--
--   SIM                       49.477   apta, direto
--   SIM_COM_ANALISE_HUMANA    37.796   apta, e o cliente quer poder filtrar
--   NAO                       14.858   fora do enriquecimento caro
--
-- POR QUE AS DUAS VARIANTES DE "NAO" VIRAM UMA SO. A planilha traz `NAO` e
-- `NAO, SEM ECONOMIA FATURADA`. A segunda nao e outro destino: e o MOTIVO de a
-- primeira valer. As 1.684 e as 13.174 recebem tratamento identico, e o motivo
-- fica guardado ao lado, em `qualificacao_motivo`, para quem auditar.
--
-- POR QUE `apta_cruzamento` VIRA COLUNA GERADA. Meia duzia de consultas vao
-- perguntar so "e apta?", e escrever `qualificacao like 'SIM%'` em cada uma
-- criaria seis lugares para errar. Gerada, a resposta sai do status e nao pode
-- divergir dele — que e o defeito que uma segunda coluna mantida a mao sempre
-- acaba tendo.
alter table resources_root.cadastro_corsan
    drop column if exists apta_cruzamento;

alter table resources_root.cadastro_corsan
    add column if not exists qualificacao text,
    add column if not exists qualificacao_motivo text;

-- NULL E PERMITIDO, e nao e descuido: a base tem 2,5 milhoes de linhas e a
-- qualificacao chegou so para Canoas. NULL significa "nao declarado", que e
-- diferente de `NAO` — e as duas nao enriquecem, mas so uma delas e um pedido
-- de preenchimento.
alter table resources_root.cadastro_corsan
    drop constraint if exists ck_corsan_qualificacao;
alter table resources_root.cadastro_corsan
    add constraint ck_corsan_qualificacao
    check (qualificacao is null
           or qualificacao in ('SIM', 'SIM_COM_ANALISE_HUMANA', 'NAO'))
    not valid;

-- `not valid` acima e `validate` aqui: a checagem em duas etapas evita o lock
-- de tabela inteira que um `add constraint` valido tomaria em 2,5 milhoes de
-- linhas com o painel no ar.
alter table resources_root.cadastro_corsan
    validate constraint ck_corsan_qualificacao;

alter table resources_root.cadastro_corsan
    add column if not exists apta_cruzamento boolean
    generated always as (qualificacao like 'SIM%') stored;

comment on column resources_root.cadastro_corsan.qualificacao is
    'SIM | SIM_COM_ANALISE_HUMANA | NAO. Os tres status oficiais do sistema. '
    'NULL = a base nao declarou, que e diferente de NAO.';
comment on column resources_root.cadastro_corsan.qualificacao_motivo is
    'O texto que veio colado a decisao, quando veio — por exemplo '
    '"SEM ECONOMIA FATURADA". Nao muda o destino; explica a decisao.';
comment on column resources_root.cadastro_corsan.apta_cruzamento is
    'Derivada de `qualificacao`: passa pelo enriquecimento caro? Gerada de '
    'proposito — mantida a mao, divergiria do status.';

create index if not exists ix_corsan_apta
    on resources_root.cadastro_corsan (id_empresa, cidade)
 where apta_cruzamento;

-- E O INDICE DO FILTRO DA TELA. O usuario vai separar os tres status dentro de
-- uma cidade; sem isto, filtrar 37.796 numa tabela de 2,5 milhoes varre tudo.
create index if not exists ix_corsan_qualificacao
    on resources_root.cadastro_corsan (id_empresa, cidade, qualificacao)
 where qualificacao is not null;
