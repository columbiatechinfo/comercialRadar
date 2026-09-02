-- 0043 — o Airbnb como fonte, e a área desenhada como recorte
--
-- POR QUE ESTA TABELA EXISTE
--
-- O Airbnb é a última fonte de terceiros do Radar, e não se parece com as
-- outras. O iFood entrega estabelecimento com CNPJ; aqui o que existe é
-- HOSPEDAGEM — com anfitrião, avaliações, comodidades e fotos, e sem CNPJ
-- nenhum. Guardar isso dentro de `pois` misturaria duas coisas que se
-- respondem de formas diferentes, e é por isso que ela é tabela própria, ao
-- lado de `ifood_merchant`.
--
-- ELA SEGUE O PADRÃO DA IRMÃ, e isso não é estilo: a primeira versão desta
-- migração criou a tabela com `tenant_id`, dono `supabase_admin` e sem RLS —
-- copiando a migração 0015 como ela foi ESCRITA, e não como a tabela existe
-- hoje. O pipeline levou `permission denied` na primeira gravação. O que vale
-- é o estado atual: `id_empresa`, dono `migrator`, RLS ligada, e a trigger
-- `preencher_empresa` que carimba o dono no INSERT.
--
-- A COORDENADA É APROXIMADA, E A COLUNA DIZ ISSO
--
-- Medido em 02/09/2026 sobre 72 anúncios de Canoas: 66 vêm com 3 a 5 casas
-- decimais (~11 m) e só 6 com precisão real. O Airbnb publica área aproximada
-- até a reserva, e o zoom no mapa não recupera nada — o marcador é elemento
-- posicionado, não desenho que ganha resolução.
--
-- `coord_exata` guarda esse fato em vez de deixá-lo implícito. Sem ela, um
-- cruzamento por proximidade trataria 11 m de erro como se fosse GPS, casaria
-- o POI vizinho, e nada no dado denunciaria.
--
-- `na_area` É O RECORTE, E NÃO UM FILTRO DE GRAVAÇÃO
--
-- Quando o trabalho é uma área desenhada, a busca sai pela CAIXA DELIMITADORA
-- do polígono (medido: 18 de 18 dentro, contra 0 de 18 pelo "Perto de você",
-- que devolve a região metropolitana inteira). Mas a caixa é um retângulo e o
-- polígono não é: o que cai no retângulo e fora do desenho continua sendo
-- gravado, marcado `na_area = false`.
--
-- É a mesma política de `area_utils`: achar custa tempo de busca, descartar o
-- que já foi achado é jogar esse tempo fora. O que `na_area` decide é quem
-- recebe a parte CARA — ficha, screenshot e consulta à IA.
--
-- `estado_detalhe` REPETE O VOCABULÁRIO DO IFOOD de propósito: PENDENTE não é
-- "falhou", é "não colhi"; SEM_RETORNO é resposta final da fonte. Quem lê os
-- dois módulos não precisa aprender duas convenções.

set local search_path = radar_comercial, public;

create table if not exists airbnb_anuncio (
    id              bigserial primary key,
    id_empresa      uuid not null,
    anuncio_id      text not null unique,    -- o id de /rooms/<id>

    -- identidade, do payload da busca
    nome            text,
    titulo          text,                    -- "Apartamento ⋅ Bairro Fátima"
    tipo_resumo     text,                    -- "Espaço inteiro: loft em Canoas"

    -- localização
    lat             double precision,
    lng             double precision,
    -- false = o Airbnb arredondou (4 casas, ~11 m). Ver o cabeçalho.
    coord_exata     boolean,
    bairro          text,
    cidade          text,
    uf              text,
    cep             text,
    endereco        text,                    -- rua, quando apurada
    numero          text,
    -- de onde veio o endereço: 'airbnb' | 'nominatim' | 'ia_imagem' | 'ia_texto'
    endereco_origem text,
    predio          text,                    -- condomínio/edifício, quando há

    -- recorte
    na_area         boolean,
    area_ref        text,

    -- capacidade
    hospedes        integer,
    quartos         integer,
    camas           integer,
    banheiros       integer,

    -- reputação
    nota            numeric,
    avaliacoes_qtd  integer,

    -- anfitrião
    anfitriao       text,
    anfitriao_taxa_resposta text,
    coanfitrioes    text[],

    -- conteúdo
    descricao       text,
    preco_total     text,
    comodidades     jsonb,
    regras          jsonb,
    destaques       jsonb,
    fotos           jsonb,
    avaliacoes      jsonb,
    -- caminho do screenshot da ficha: é ele que vai à IA quando só há
    -- coordenada aproximada, e foi assim que "Avenida Getúlio Vargas, 4831"
    -- saiu de um anúncio que não publica endereço nenhum
    print_ficha     text,

    poi_id          integer references pois(id) on delete set null,
    estado_detalhe  text not null default 'PENDENTE',
    bruto           jsonb,
    visto_em        timestamptz not null default now()
);

-- `id_empresa` na PRIMEIRA coluna de todo índice: a RLS é avaliada por linha, e
-- sem isso o filtro do dono não usa índice nenhum.
create index if not exists ix_airbnb_empresa_area
    on airbnb_anuncio (id_empresa, area_ref, na_area);

-- A fila do detalhe: pendentes DENTRO da área, mais recentes primeiro.
create index if not exists ix_airbnb_pendente
    on airbnb_anuncio (id_empresa, estado_detalhe, visto_em desc)
    where na_area;

-- Casamento por proximidade com os POIs.
create index if not exists ix_airbnb_coord
    on airbnb_anuncio (id_empresa, lat, lng)
    where lat is not null;

create index if not exists ix_airbnb_poi
    on airbnb_anuncio (poi_id) where poi_id is not null;

-- ---------------------------------------------------------------------------
-- Dono, permissões e RLS — o que faltou na primeira versão desta migração.
-- ---------------------------------------------------------------------------
alter table airbnb_anuncio owner to migrator;

grant insert, select, update, delete on airbnb_anuncio to app_user;
grant select on airbnb_anuncio to readonly;
grant usage, select on sequence airbnb_anuncio_id_seq to app_user;

alter table airbnb_anuncio enable row level security;

drop policy if exists p_airbnb_anuncio on airbnb_anuncio;
create policy p_airbnb_anuncio on airbnb_anuncio for all
    using (core.eh_suporte() or (id_empresa = core.empresa_atual()))
    with check (core.eh_suporte() or (id_empresa = core.empresa_atual()));

-- Quem carimba o dono no INSERT. A mesma função que a `ifood_merchant` usa —
-- sem ela, `id_empresa not null` recusaria toda gravação do pipeline.
drop trigger if exists trg_empresa_airbnb_anuncio on airbnb_anuncio;
create trigger trg_empresa_airbnb_anuncio
    before insert on airbnb_anuncio
    for each row execute function radar_comercial.preencher_empresa();
