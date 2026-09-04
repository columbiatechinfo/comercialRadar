-- 0066 · o veredito da IA sobre o POI
--
-- O QUE ESTA TABELA GUARDA, e por que ela não é uma coluna na `pois`.
--
-- O veredito não é um atributo do POI: é o resultado de UM julgamento, feito
-- por UM modelo, sobre UM conjunto de imagens, num dia. Trocar o modelo ou
-- recapturar a evidência produz outro veredito sobre o mesmo POI, e o que
-- separa "mudou porque o mundo mudou" de "mudou porque o modelo mudou" é ter
-- guardado o modelo, a contagem de imagens e a data ao lado da conclusão.
-- Coluna na `pois` guardaria só a última palavra e perderia a pergunta.
--
-- QUATRO VEREDITOS, e a escala é do dono do produto:
--
--   aprovado_exato       o estabelecimento do cadastro está ali, identificado
--   aprovado_comercial   não se identificou AQUELE, mas o imóvel tem atividade
--                        comercial visível — vale a visita do mesmo jeito
--   revisao_humana       há indício e não há prova; alguém precisa olhar
--   reprovado            nenhuma atividade econômica relevante no imóvel
--
-- Os dois primeiros aprovam. A diferença entre eles não é confiança, é ALVO:
-- no `exato` a equipe sabe por quem perguntar; no `comercial` ela vai ao
-- endereço e descobre. Fundir os dois apagaria essa informação.
--
-- DUAS CLASSIFICAÇÕES, E ELAS RESPONDEM COISAS DIFERENTES — decidido pelo dono
-- do produto: a ESPÉCIE do CNEFE (1 a 8) diz o que é a EDIFICAÇÃO, e é o que
-- conversa com o recadastramento do IBGE; a SEÇÃO do CNAE (A a U) diz qual é a
-- ATIVIDADE, e é o que conversa com a Receita e com a tarifa. Um galpão pode
-- ser espécie 6 e seção C; uma casa com salão na frente pode ser espécie 1 e
-- seção G. Guardar só uma delas obrigaria a adivinhar a outra.
--
-- A PERCEPÇÃO CEGA FICA GUARDADA em `percepcao`. Ela é a descrição que o modelo
-- fez das imagens ANTES de ver qualquer dado do cadastro, e é o que permite
-- auditar um veredito estranho sem reprocessar: dá para ler o que ele viu e
-- separar erro de visão de erro de julgamento.

set search_path to radar_comercial, extensions, public;

create table if not exists poi_veredito (
    id             bigserial primary key,
    id_empresa     uuid not null,
    poi_id         bigint not null references pois(id) on delete cascade,

    veredito       text not null,
    justificativa  text,

    -- 1 domicílio particular · 2 domicílio coletivo · 3 agropecuário ·
    -- 4 ensino · 5 saúde · 6 outras finalidades · 7 em construção ·
    -- 8 religioso. São os oito códigos do CNEFE 2022, e estão todos presentes
    -- na `resources_root.ibge_cnefe` — a lista não é inventada aqui.
    especie_cnefe  smallint,
    -- A a U, as 21 seções da CNAE 2.0, conforme `cnae_secao`.
    secao_cnae     char(1) references cnae_secao(letra),

    -- Quantos medidores de energia ou água aparecem na fachada. Vários
    -- medidores num imóvel de ligação residencial é indício forte de que ali
    -- há mais de uma unidade consumidora — que é exatamente o que a Corsan
    -- procura. Nulo quer dizer "não foi possível contar", nunca zero.
    medidores      smallint,

    percepcao      jsonb,
    modelo         text,
    imagens        smallint,
    segundos       numeric(8,2),
    avaliado_em    timestamptz not null default now(),

    constraint poi_veredito_valor check (veredito in (
        'aprovado_exato', 'aprovado_comercial', 'revisao_humana', 'reprovado')),
    constraint poi_veredito_especie check (
        especie_cnefe is null or especie_cnefe between 1 and 8)
);

create unique index if not exists poi_veredito_unico
    on poi_veredito (id_empresa, poi_id);
create index if not exists poi_veredito_por_veredito
    on poi_veredito (id_empresa, veredito);

alter table poi_veredito owner to migrator;
grant select, insert, update, delete on poi_veredito to app_user;
grant select on poi_veredito to readonly;
grant usage, select on sequence poi_veredito_id_seq to app_user;

alter table poi_veredito enable row level security;
drop policy if exists poi_veredito_ver on poi_veredito;
create policy poi_veredito_ver on poi_veredito for select
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual())));
drop policy if exists poi_veredito_mexer on poi_veredito;
create policy poi_veredito_mexer on poi_veredito for all
    using ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)))
    with check ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)));
drop trigger if exists poi_veredito_empresa on poi_veredito;
create trigger poi_veredito_empresa before insert on poi_veredito
    for each row execute function radar_comercial.preencher_empresa();

comment on table poi_veredito is
    'O julgamento da IA sobre um POI, com o modelo e a data ao lado — veredito '
    'e resultado de um julgamento, nao atributo do ponto.';
comment on column poi_veredito.percepcao is
    'A descricao que o modelo fez das imagens ANTES de ver o cadastro. Serve '
    'para separar erro de visao de erro de julgamento sem reprocessar.';
comment on column poi_veredito.medidores is
    'Medidores de energia/agua contados na fachada. NULL = nao foi possivel '
    'contar; zero = contou e nao havia.';

select 'poi_veredito criada' as feito;
