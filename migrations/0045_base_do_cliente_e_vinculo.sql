-- 0045 — a base do cliente vira o eixo: como ela é declarada, e o que se pendura nela
--
-- O QUE MUDA NO DESENHO DA FASE 1
--
-- Até aqui a base do cliente era uma fonte entre outras, e o cruzamento juntava
-- POI com POI. O fluxo definido pelo dono do produto inverte as duas coisas:
--
--   · a base do cliente é o EIXO. Cada linha dela é uma instalação, e o que o
--     Radar faz é pendurar nela os POIs das outras fontes que falam do mesmo
--     lugar — idealmente o mesmo ponto visto por várias fontes, que é o que
--     confirma atividade comercial.
--
--   · o cruzamento entre fontes deixa de existir como etapa própria. Duas
--     fontes só se encontram quando as duas apontam para a MESMA instalação.
--
-- POR QUE UMA TABELA PARA DESCREVER A BASE
--
-- Cada cliente entrega a base num formato. A coluna de latitude pode se chamar
-- `lat`, `LATITUDE`, `y` ou `coord_y`; o número da ligação pode ser
-- `num_ligacao`, `instalacao` ou `matricula`. Sem alguém DECLARAR o que é o
-- quê, o sistema adivinha — e adivinhar errado a coluna de tipo de cliente faz
-- o produto inteiro classificar comércio como residência.
--
-- `base_cliente` guarda essa declaração: o arquivo, o de-para de colunas, e
-- quais valores da coluna de tipo significam ATIVIDADE COMERCIAL. A IA sugere,
-- uma pessoa confirma, e só então a base fica `pronta`. Antes disso não se
-- escolhe cidade nem se desenha área — é a trava que o fluxo pede.
--
-- `mapa_colunas` É JSONB, E NÃO COLUNAS FIXAS. As seis obrigatórias de hoje
-- (latitude, longitude, ligação, endereço, tipo de cliente, quais tipos são
-- comerciais) são o mínimo; o próximo cliente traz uma sétima. Colunas fixas
-- obrigariam uma migração por cliente.
--
-- POR QUE O VÍNCULO É TABELA, E NÃO UMA COLUNA EM `pois`
--
-- `pois.id_base` e `pois.id_ligacao_base` respondem "de que instalação este
-- ponto é", e existem para a consulta simples e para o filtro do painel. Mas
-- uma instalação tem VÁRIOS POIs — é esse o ponto do fluxo, e é assim que a
-- adesão entre fontes vira número. Uma coluna só guardaria o último.
--
-- `ligacao_poi` guarda cada par, com o que sustentou a decisão. As colunas de
-- criterio são booleanas de propósito: quem lê a linha vê O QUE bateu, não só
-- um número que ninguém sabe recompor.
--
-- A CONFIANÇA É CONTAGEM, NÃO OPINIÃO
--
-- Cinco critérios, cada um vale um:
--
--     1  endereço e número iguais
--     2  coordenada a até 20 m
--     3  o mesmo telhado no tile capturado da área
--     4  o telhado é comercial (salmão, no estilo que a captura usa)
--     5  a fonte do POI é distinta das que já aderiram àquela ligação
--
-- `criterios_ok` é a soma, de 0 a 5, e `confianca` é `criterios_ok / 5`. Não há
-- peso escondido: quem discordar da régua muda a conta lendo as cinco colunas.
--
-- `fontes_aderentes` e `fontes_no_momento` respondem a pergunta que o dono do
-- produto pediu em percentual E em absoluto: quantas das fontes que existiam
-- quando o vínculo foi gerado apontaram para aquela ligação. Guardar o
-- denominador junto é o que impede o número de mentir quando uma fonte nova
-- entrar depois.

set local search_path = radar_comercial, public;

-- ═══════════════════════════════════════════════ a base declarada ═══════════
create table if not exists base_cliente (
    id              bigserial primary key,
    id_empresa      uuid not null,

    nome            text not null,          -- como o usuário chamou a base
    cliente         text,                   -- 'corsan', 'sanepar'...
    arquivo         text,                   -- caminho ou nome do que foi enviado
    tabela_dados    text,                   -- onde as linhas ficaram

    linhas          bigint,
    colunas_brutas  jsonb,                  -- os títulos como vieram

    -- O de-para. Chaves conhecidas hoje: latitude, longitude, ligacao,
    -- endereco, tipo_cliente. Valores: o nome da coluna na base do cliente.
    mapa_colunas    jsonb not null default '{}'::jsonb,

    -- Quais VALORES da coluna de tipo significam atividade comercial. É o campo
    -- que decide o que o produto procura, e por isso não tem padrão: sem
    -- alguém dizer, a base não fica pronta.
    tipos_comerciais jsonb not null default '[]'::jsonb,

    -- 'rascunho' → a IA sugeriu, ninguém confirmou
    -- 'pronta'   → uma pessoa confirmou; libera escolher área/cidade
    estado          text not null default 'rascunho',
    sugerido_por_ia jsonb,                  -- a sugestão, guardada para conferência
    confirmado_por  text,
    confirmado_em   timestamptz,

    criado_em       timestamptz not null default now(),

    constraint base_cliente_nome_unico unique (id_empresa, nome),
    constraint base_cliente_estado_valido check (estado in ('rascunho', 'pronta')),
    -- Base `pronta` sem as seis declaradas seria a trava existindo no papel.
    -- O banco recusa: latitude, longitude, ligacao, endereco e tipo_cliente no
    -- mapa, e ao menos um tipo marcado como comercial.
    constraint base_cliente_pronta_esta_declarada check (
        estado <> 'pronta' or (
            mapa_colunas ? 'latitude' and mapa_colunas ? 'longitude'
            and mapa_colunas ? 'ligacao' and mapa_colunas ? 'endereco'
            and mapa_colunas ? 'tipo_cliente'
            and jsonb_array_length(tipos_comerciais) > 0))
);

create index if not exists base_cliente_por_empresa
    on base_cliente (id_empresa, estado);

alter table base_cliente owner to migrator;
grant select, insert, update, delete on base_cliente to app_user;
grant select on base_cliente to readonly;
grant usage, select on sequence base_cliente_id_seq to app_user;
alter table base_cliente enable row level security;
drop policy if exists base_cliente_por_empresa_pol on base_cliente;
create policy base_cliente_por_empresa_pol on base_cliente
    using (core.eh_suporte() or (id_empresa = core.empresa_atual()))
    with check (core.eh_suporte() or (id_empresa = core.empresa_atual()));
drop trigger if exists base_cliente_empresa on base_cliente;
create trigger base_cliente_empresa before insert on base_cliente
    for each row execute function radar_comercial.preencher_empresa();

-- ═════════════════════════════════════ de que instalação é cada POI ═════════
alter table pois add column if not exists id_base bigint;
alter table pois add column if not exists id_ligacao_base text;

comment on column pois.id_base is
    'a base do cliente (base_cliente.id) a cuja instalação este POI foi ligado';
comment on column pois.id_ligacao_base is
    'o número da ligação/instalação, como está na base do cliente — texto '
    'porque cada cliente numera do seu jeito, e zero à esquerda importa';

-- `id_empresa` na frente: a política de RLS é avaliada por linha.
create index if not exists pois_por_ligacao
    on pois (id_empresa, id_base, id_ligacao_base)
    where id_ligacao_base is not null;

-- ═══════════════════════════════ o vínculo, com o que o sustenta ════════════
create table if not exists ligacao_poi (
    id                bigserial primary key,
    id_empresa        uuid not null,

    id_base           bigint not null,
    ligacao           text not null,        -- a instalação, na numeração do cliente
    poi_id            bigint not null,

    -- OS CINCO CRITÉRIOS, cada um visível por si. Ver o cabeçalho: guardar só
    -- a soma tornaria impossível revisar a régua depois sem refazer tudo.
    mesmo_endereco    boolean not null default false,
    mesmo_numero      boolean not null default false,
    ate_20m           boolean not null default false,
    mesmo_telhado     boolean not null default false,
    telhado_comercial boolean not null default false,

    metros            double precision,     -- a distância medida, quando houve
    criterios_ok      smallint not null default 0,
    confianca         real not null default 0,

    -- Adesão entre fontes: quantas apontaram para esta ligação, sobre quantas
    -- existiam no momento. O denominador vai junto para o número não mentir
    -- quando uma fonte nova entrar depois.
    fontes_aderentes  smallint not null default 0,
    fontes_no_momento smallint not null default 0,

    fonte_poi         text,                 -- qual fonte trouxe ESTE poi
    gerado_em         timestamptz not null default now(),

    constraint ligacao_poi_par_unico unique (id_base, ligacao, poi_id),
    constraint ligacao_poi_criterios_coerentes
        check (criterios_ok between 0 and 5),
    constraint ligacao_poi_confianca_coerente
        check (confianca >= 0 and confianca <= 1)
);

create index if not exists ligacao_poi_por_ligacao
    on ligacao_poi (id_empresa, id_base, ligacao);
create index if not exists ligacao_poi_por_poi
    on ligacao_poi (id_empresa, poi_id);
-- A consulta do painel é "as ligações de maior confiança primeiro".
create index if not exists ligacao_poi_por_confianca
    on ligacao_poi (id_empresa, id_base, confianca desc);

alter table ligacao_poi owner to migrator;
grant select, insert, update, delete on ligacao_poi to app_user;
grant select on ligacao_poi to readonly;
grant usage, select on sequence ligacao_poi_id_seq to app_user;
alter table ligacao_poi enable row level security;
drop policy if exists ligacao_poi_por_empresa on ligacao_poi;
create policy ligacao_poi_por_empresa on ligacao_poi
    using (core.eh_suporte() or (id_empresa = core.empresa_atual()))
    with check (core.eh_suporte() or (id_empresa = core.empresa_atual()));
drop trigger if exists ligacao_poi_empresa on ligacao_poi;
create trigger ligacao_poi_empresa before insert on ligacao_poi
    for each row execute function radar_comercial.preencher_empresa();

comment on table ligacao_poi is
    'Cada POI pendurado numa instalação da base do cliente, com os cinco '
    'critérios que sustentaram o vínculo visíveis um a um. `confianca` é '
    '`criterios_ok / 5` — não há peso escondido.';
comment on column ligacao_poi.fontes_no_momento is
    'quantas fontes existiam quando o vínculo foi gerado; guardar o '
    'denominador impede a adesão de mentir quando uma fonte nova entrar';
