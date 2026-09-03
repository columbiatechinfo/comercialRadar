-- 0044 — o logradouro de cada POI, com a peneira que o resolveu e a força dela
--
-- POR QUE ESTA TABELA EXISTE, tendo já `logradouro_ajustado`
--
-- `logradouro_ajustado` é a saída da skill: ela MARCA a forma canônica de um
-- logradouro e organiza o complemento, indexada por `record_id` + `scope_id`.
-- Responde "como se escreve esta rua". Não responde "em que rua está este POI",
-- que é outra pergunta e tem outra resposta.
--
-- Esta tabela guarda a segunda. Um POI, um logradouro do cadastro do IBGE, e —
-- o que muda tudo — COMO se chegou nele.
--
-- `forca` É A COLUNA QUE NÃO PODE FALTAR
--
-- Nem todo acerto vale o mesmo, e tratar todos como iguais é o erro que esta
-- coluna impede.
--
--     prova    o próprio POI afirma. O CEP dele existe no cadastro e aponta a
--              rua; ou a rua que ele escreve existe no município. Se está
--              errado, o dado de origem é que está errado.
--
--     indicio  ninguém afirmou nada: a rua foi INFERIDA da coordenada, pelo
--              endereço cadastrado a até 20 metros. Um pin cai no meio do
--              terreno, no fundo do lote ou na quadra vizinha.
--
-- Medido em Canoas, 02/09/2026, sobre 27.694 POIs: 24.112 (87,1%) por prova,
-- 2.238 (8,1%) por indício, 1.344 (4,9%) sem nenhum dos dois. Sem esta coluna,
-- os 2.238 entrariam no cruzamento com o mesmo peso dos 24.112 — e é justamente
-- neles que mora o erro que ninguém veria.
--
-- POR QUE 20 METROS, E NÃO 120
--
-- A primeira versão aceitava o vizinho a até 120 m e resolvia 11,8% da cidade
-- por proximidade. Mas 120 m é uma quadra inteira: o "vizinho" pode ser a rua
-- de trás. O caso que decidiu foi o `Cachorro do Rosário`, que fica no Canoas
-- Shopping na Guilherme Schell e recebia a `Rua Mathias Velho`, a 139 m.
--
-- A 20 m cabem o recuo de calçada e um estacionamento pequeno; o outro lado da
-- rua, não. A troca foi medida: a fila de revisão humana sai de 440 para 1.344
-- POIs, e em compensação nenhuma dessas 1.344 recebe rua errada em silêncio.
--
-- `logradouro_original` NUNCA É SOBRESCRITO
--
-- O que a fonte escreveu fica ao lado do que o cadastro diz. Sem isso não há
-- como conferir uma divergência depois, e não há como voltar atrás. A regra do
-- projeto vale aqui como nas outras etapas: nada é apagado, o que não se prova
-- é marcado.
--
-- `revisao_humana` É FILA, NÃO LIXEIRA. O POI continua no banco, continua
-- valendo para tudo o mais, e só está dizendo que o endereço dele não foi
-- resolvido por nenhuma peneira.
--
-- PADRÃO DA CASA: `id_empresa`, dono `migrator`, RLS ligada e a trigger
-- `preencher_empresa` carimbando o dono no INSERT. O índice começa por
-- `id_empresa` porque a política de RLS é avaliada por linha.

set local search_path = radar_comercial, public;

create table if not exists logradouro_resolvido (
    id                   bigserial primary key,
    id_empresa           uuid not null,
    poi_id               bigint not null,
    cod_municipio        text not null,

    -- o que o cadastro do IBGE diz
    logradouro           text,
    numero               text,
    cep                  text,
    bairro               text,
    num_quadra           text,
    num_face             text,

    -- como se chegou lá
    -- 'cep' | 'endereco' | 'photon' | 'osrm' | 'cnefe' | 'nenhuma'
    peneira              text not null,
    -- 'prova' | 'indicio' | 'sem'
    forca                text not null,
    -- distância até o endereço cadastrado, quando a peneira foi por proximidade
    metros               double precision,

    -- o que a fonte tinha escrito, preservado
    logradouro_original  text,
    cep_original         text,

    revisao_humana       boolean not null default false,
    motivo               text,

    resolvido_em         timestamptz not null default now(),

    constraint logradouro_resolvido_poi_unico unique (poi_id),
    constraint logradouro_resolvido_forca_valida
        check (forca in ('prova', 'indicio', 'sem')),
    constraint logradouro_resolvido_peneira_valida
        check (peneira in ('cep', 'endereco', 'photon', 'osrm', 'cnefe', 'nenhuma')),
    -- Indício sem distância seria indício de quê? E prova com distância seria
    -- prova por proximidade, que é contradição. O banco recusa os dois.
    constraint logradouro_resolvido_metros_coerente
        check ((forca = 'indicio' and metros is not null)
               or (forca <> 'indicio' and metros is null))
);

-- `id_empresa` primeiro em todo índice: a política de RLS é avaliada por linha,
-- e sem ela na frente o planejador varre linhas de outros clientes para depois
-- descartá-las.
create index if not exists logradouro_resolvido_empresa_poi
    on logradouro_resolvido (id_empresa, poi_id);

create index if not exists logradouro_resolvido_fila_humana
    on logradouro_resolvido (id_empresa, cod_municipio)
    where revisao_humana;

create index if not exists logradouro_resolvido_por_forca
    on logradouro_resolvido (id_empresa, cod_municipio, forca);

alter table logradouro_resolvido owner to migrator;
grant select, insert, update, delete on logradouro_resolvido to app_user;
grant select on logradouro_resolvido to readonly;
grant usage, select on sequence logradouro_resolvido_id_seq to app_user;

alter table logradouro_resolvido enable row level security;

drop policy if exists logradouro_resolvido_por_empresa on logradouro_resolvido;
create policy logradouro_resolvido_por_empresa on logradouro_resolvido
    using (core.eh_suporte() or (id_empresa = core.empresa_atual()))
    with check (core.eh_suporte() or (id_empresa = core.empresa_atual()));

drop trigger if exists logradouro_resolvido_empresa on logradouro_resolvido;
create trigger logradouro_resolvido_empresa
    before insert on logradouro_resolvido
    for each row execute function radar_comercial.preencher_empresa();

comment on table logradouro_resolvido is
    'O logradouro de cada POI segundo o cadastro do IBGE, com a peneira que o '
    'resolveu e a força dela. `prova` = o POI afirmou; `indicio` = foi inferido '
    'da coordenada a até 20 m. O endereço original nunca é sobrescrito.';
comment on column logradouro_resolvido.forca is
    'prova (CEP ou endereço escrito), indicio (proximidade, ver metros), sem';
comment on column logradouro_resolvido.metros is
    'distância até o endereço cadastrado; obrigatória em indicio, proibida em prova';
