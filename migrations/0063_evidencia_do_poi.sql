-- 0063 — as imagens que vão à IA, num lugar só
--
-- POR QUE NÃO REAPROVEITAR `streetview_imgs`
--
-- O conjunto que a avaliação usa não é Street View: são três imagens de
-- naturezas diferentes — duas visadas de rua, uma vista de satélite com
-- marcador — e, para quem veio do iFood ou do Airbnb, o print da página do
-- anúncio. Enfiar satélite e print de página numa tabela chamada
-- `streetview_imgs` faria a próxima pessoa procurar no lugar errado.
--
-- `streetview_imgs` continua para o que ela é: a foto de fachada que o modal do
-- POI exibe.
--
-- O QUE CADA `tipo` GUARDA
--
--   sv_frente       o panorama encarando a fachada. `heading` é o ângulo
--                   câmera→POI, então O ALVO FICA NO CENTRO DA IMAGEM — é isso
--                   que permite desenhar o marcador sem projeção nenhuma.
--   sv_fundo        o mesmo panorama, heading + 180. Mostra o outro lado da
--                   rua, que é o que diz se o quarteirão é comercial.
--   satelite        vista de cima no zoom máximo, com marcador sobre a
--                   coordenada.
--   pagina_ifood    print da página inteira da loja. O iFood não tem fachada
--                   para olhar; o que prova atividade é a página estar no ar,
--                   com horário e entrega.
--   pagina_airbnb   print da página inteira do anúncio. O Airbnb não publica
--                   endereço exato, mas a DISPONIBILIDADE já é prova.
--
-- OS BYTES FICAM AQUI, e não só o caminho. São 3 imagens por POI e o conjunto é
-- o que a IA recebe: perder um arquivo do disco significaria reavaliar sem
-- saber que a evidência mudou. Um tile de satélite de quarteirão inteiro não
-- cabia na tabela (`tile_captura` guarda caminho); um recorte de POI cabe.

set search_path to radar_comercial, extensions, public;

create table if not exists poi_evidencia (
    id           bigserial primary key,
    id_empresa   uuid not null,
    poi_id       bigint not null references pois(id) on delete cascade,
    tipo         text not null,
    dados        bytea,
    storage_path text,
    url_origem   text,
    -- só as visadas de rua têm estes
    pano_id      text,
    cam_lat      double precision,
    cam_lng      double precision,
    heading      double precision,
    pitch        double precision,
    fov          double precision,
    distancia_m  double precision,
    largura_px   integer,
    altura_px    integer,
    bytes_tam    bigint,
    -- Quando a imagem foi tirada. A avaliação por IA guarda a data do veredito;
    -- a diferença entre as duas é o que diz se o veredito olhou foto velha.
    capturado_em timestamptz not null default now(),
    motivo_falha text
);

create unique index if not exists poi_evidencia_unica
    on poi_evidencia (id_empresa, poi_id, tipo);
create index if not exists poi_evidencia_por_poi on poi_evidencia (id_empresa, poi_id);

alter table poi_evidencia owner to migrator;
grant select, insert, update, delete on poi_evidencia to app_user;
grant select on poi_evidencia to readonly;
grant usage, select on sequence poi_evidencia_id_seq to app_user;
alter table poi_evidencia enable row level security;
drop policy if exists poi_evidencia_ver on poi_evidencia;
create policy poi_evidencia_ver on poi_evidencia for select
    using ((select core.eh_suporte()) or (id_empresa = (select core.empresa_atual())));
drop policy if exists poi_evidencia_mexer on poi_evidencia;
create policy poi_evidencia_mexer on poi_evidencia for all
    using ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)))
    with check ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)));
drop trigger if exists poi_evidencia_empresa on poi_evidencia;
create trigger poi_evidencia_empresa before insert on poi_evidencia
    for each row execute function radar_comercial.preencher_empresa();

comment on table poi_evidencia is
    'As imagens que a avaliação por IA recebe, uma linha por (POI, tipo). '
    'Guarda os BYTES: o conjunto É a evidência do veredito, e perder um '
    'arquivo do disco significaria reavaliar sem saber que a prova mudou.';
comment on column poi_evidencia.motivo_falha is
    'Por que ESTA imagem não existe. Sem panorama, página fora do ar, tempo '
    'esgotado. Linha com motivo e sem bytes é resposta, não ausência — é o '
    'que separa "não consegui" de "ninguém tentou".';
