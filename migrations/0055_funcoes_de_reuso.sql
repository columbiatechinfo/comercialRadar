-- 0055 — as três funções do reaproveitamento entre empresas
--
-- POR QUE ISTO É FUNÇÃO E NÃO CONSULTA
--
-- Reaproveitar é, por definição, uma operação ENTRE EMPRESAS: ela pergunta o
-- que as OUTRAS já extraíram naquela área. A política de `pois` esconde isso de
-- uma sessão comum, e está certa em esconder — é ela que garante o isolamento.
--
-- Havia dois jeitos de atravessar. O primeiro é elevar a sessão a suporte, que
-- foi como o cruzamento por ligação funcionou até 03/09/2026: a elevação valia
-- a conexão inteira, milhares de transações rodavam com a trava desligada, e um
-- engano em qualquer consulta enxergaria as três empresas do banco. Aquilo foi
-- removido por isso.
--
-- O segundo é este: `security definer` em funções ESTREITAS, cada uma com um
-- trabalho só, e nenhuma delas capaz de devolver dado bruto de outra empresa.
--
--   `reuso_resumo`      devolve CONTAGEM e DATA por empresa. Nem nome de POI.
--   `reuso_candidatos`  devolve id, coordenada e nome dos POIs que a empresa
--                       do chamador AINDA NÃO TEM — o mínimo para desenhar a
--                       prévia e recortar pelo polígono.
--   `reusar_pois`       copia os POIs indicados PARA A EMPRESA DO CHAMADOR.
--                       Ela não aceita empresa de destino como parâmetro: o
--                       destino é sempre `core.empresa_atual()`, e por isso
--                       ninguém consegue usá-la para gravar na empresa alheia.
--
-- O QUE A CÓPIA LEVA — decidido pelo dono do produto em 04/09/2026
--
-- Tudo que DESCREVE O LUGAR, inclusive o que custou caro: as tabelas de cada
-- fonte, avaliações, fotos, horários, Street View e o veredito da IA de visão.
-- É isso que dá sentido a reaproveitar.
--
-- Não leva o que é JULGAMENTO da outra empresa sobre o ponto — vínculo com a
-- base, triagem de fachada, atribuição de campo, tratamento de CNPJ. Esses
-- nascem do trabalho de quem reaproveita.
--
-- AS FOTOS COMPARTILHAM O ARQUIVO. A linha nova aponta para o mesmo
-- `storage_path`; o disco não dobra a cada cliente na mesma cidade. Hoje isso é
-- gratuito porque nenhuma imagem foi baixada ainda (2.599 linhas, todas só com
-- URL) e nada no código apaga arquivo de imagem. NO DIA EM QUE APAGAR, vai
-- precisar contar referência antes — está dito aqui porque é o tipo de coisa
-- que se descobre tarde.

-- extensions no caminho: o PostGIS mora la nesta instalacao, e sem ele o
-- tipo geometry nao existe dentro de uma funcao com search_path proprio.
set search_path to radar_comercial, extensions, public;

-- O que é copiado junto com o POI. UMA LISTA SÓ, e é esta: o Python chama a
-- função, não repete a lista. Acrescentar uma tabela aqui é o jeito de fazer o
-- reaproveitamento cobrir mais.
--
-- `logradouro_resolvido` fica de fora de propósito, e é a candidata mais óbvia
-- a entrar: ela guarda o endereço resolvido até a quadra e a face, que é a
-- etapa 7 inteira. Não entrou porque não estava na lista que o dono do produto
-- nomeou.
create or replace function radar_comercial.reuso_tabelas_filhas()
returns text[] language sql immutable as $$
    select array[
        'maps_data', 'osm_data', 'overture_data', 'foursquare_data',
        'receita_data', 'cadastur_data', 'ifood_merchant', 'airbnb_anuncio',
        'comentarios', 'images_urls', 'horario_funcionamento',
        'streetview_imgs', 'analise_ia'
    ]
$$;

-- ── 1. o resumo: o que existe na área, de quem, e de quando ────────────────
create or replace function radar_comercial.reuso_resumo(
    p_lat_min double precision, p_lat_max double precision,
    p_lng_min double precision, p_lng_max double precision)
returns table (id_empresa uuid, empresa text, pois bigint,
               coleta_mais_antiga date, coleta_mais_recente date,
               e_minha boolean)
language sql
stable
security definer
set search_path to 'radar_comercial', 'core', 'extensions', 'public'
as $$
    select p.id_empresa,
           coalesce(e.name, '?')::text,
           count(*),
           min(coalesce(p.coletado_em, p.criado_em))::date,
           max(coalesce(p.coletado_em, p.criado_em))::date,
           p.id_empresa = (select core.empresa_atual())
      from radar_comercial.pois p
      left join core.tb_empresas e on e.id = p.id_empresa
     where p.pt_geo is not null
       and p.fundido_em is null
       and st_y(p.pt_geo::extensions.geometry) between p_lat_min and p_lat_max
       and st_x(p.pt_geo::extensions.geometry) between p_lng_min and p_lng_max
     group by 1, 2, 6
     order by 3 desc
$$;

-- ── 2. os candidatos: o que a minha empresa ainda não tem ──────────────────
create or replace function radar_comercial.reuso_candidatos(
    p_lat_min double precision, p_lat_max double precision,
    p_lng_min double precision, p_lng_max double precision)
returns table (poi_id bigint, lat double precision, lng double precision,
               nome text, coletado_em timestamptz)
language sql
stable
security definer
set search_path to 'radar_comercial', 'core', 'extensions', 'public'
as $$
    -- "Já tem" usa as MESMAS chaves que o ingestor usa para deduplicar —
    -- place_id, e nome+endereço. É isso que faz a cópia e a coleta concordarem
    -- sobre o que é o mesmo ponto; chaves diferentes produziriam um POI
    -- duplicado dentro da própria empresa na primeira mineração seguinte.
    select o.id,
           st_y(o.pt_geo::extensions.geometry), st_x(o.pt_geo::extensions.geometry),
           o.nome,
           coalesce(o.coletado_em, o.criado_em)
      from radar_comercial.pois o
     where o.id_empresa is distinct from (select core.empresa_atual())
       and o.pt_geo is not null
       and o.fundido_em is null
       and st_y(o.pt_geo::extensions.geometry) between p_lat_min and p_lat_max
       and st_x(o.pt_geo::extensions.geometry) between p_lng_min and p_lng_max
       and not exists (
             select 1 from radar_comercial.pois meu
              where meu.id_empresa = (select core.empresa_atual())
                and ((meu.place_id is not null and meu.place_id <> ''
                      and meu.place_id = o.place_id)
                  or (upper(btrim(meu.nome)) = upper(btrim(o.nome))
                      and upper(btrim(coalesce(meu.endereco, '')))
                        = upper(btrim(coalesce(o.endereco, '')))))
           )
$$;

-- ── 3. a cópia ─────────────────────────────────────────────────────────────
create or replace function radar_comercial.reusar_pois(p_ids bigint[])
returns table (tabela text, linhas bigint)
language plpgsql
security definer
set search_path to 'radar_comercial', 'core', 'extensions', 'public'
as $$
declare
    v_destino uuid := (select core.empresa_atual());
    v_cols    text;
    v_sel     text;
    v_t       text;
    v_fcols   text;
    v_fsel    text;
    v_n       bigint;
    v_velhos  bigint[];
    v_novos   bigint[];
begin
    if v_destino is null then
        raise exception 'sem empresa na sessao: nao ha para quem copiar';
    end if;

    -- As colunas da `pois` que passam inteiras. As cinco de fora são reescritas
    -- na cópia: `id` é novo, `id_empresa` é a de quem chamou, `criado_em` é
    -- agora, `reusado_de` aponta a origem e `coletado_em` guarda quando o dado
    -- foi de fato colhido — é ela que mostra a idade do que se reaproveitou.
    select string_agg(quote_ident(column_name), ', ' order by ordinal_position),
           string_agg('o.' || quote_ident(column_name), ', ' order by ordinal_position)
      into v_cols, v_sel
      from information_schema.columns
     where table_schema = 'radar_comercial' and table_name = 'pois'
       and is_generated = 'NEVER'
       and column_name not in ('id', 'id_empresa', 'criado_em',
                               'reusado_de', 'coletado_em');

    -- `if exists` + `truncate`, e não `create ... on commit drop`: a função
    -- pode ser chamada duas vezes na mesma transação (um lote por vez), e o
    -- `create temp table` estouraria na segunda com "already exists".
    drop table if exists _mapa;
    create temp table _mapa (velho bigint, novo bigint);

    -- O DE-PARA VEM DO PRÓPRIO INSERT, por CTE.
    --
    -- A primeira versão descartava o `returning` do `execute` e reconsultava a
    -- `pois` por `reusado_de = any(p_ids)`. Parecia equivalente e não era: uma
    -- cópia FEITA ANTES, de outro lote, tem o mesmo `reusado_de` e entraria no
    -- de-para — e as tabelas filhas seriam copiadas outra vez, para o POI
    -- errado. O `returning` devolve exatamente as linhas que ESTE insert criou.
    execute format(
        'with ins as ( '
        '  insert into radar_comercial.pois '
        '         (id_empresa, criado_em, reusado_de, coletado_em, %s) '
        '  select $1, now(), o.id, coalesce(o.coletado_em, o.criado_em), %s '
        '    from radar_comercial.pois o '
        '   where o.id = any($2) '
        '  returning reusado_de, id) '
        'insert into _mapa (velho, novo) select reusado_de, id from ins',
        v_cols, v_sel)
        using v_destino, p_ids;

    select count(*) into v_n from _mapa;
    tabela := 'pois'; linhas := v_n; return next;

    select array_agg(velho), array_agg(novo) into v_velhos, v_novos from _mapa;
    if v_velhos is null then
        return;
    end if;

    foreach v_t in array radar_comercial.reuso_tabelas_filhas()
    loop
        if not exists (select 1 from information_schema.columns
                        where table_schema = 'radar_comercial'
                          and table_name = v_t and column_name = 'poi_id') then
            continue;
        end if;
        select string_agg(quote_ident(column_name), ', ' order by ordinal_position),
               string_agg('f.' || quote_ident(column_name), ', ' order by ordinal_position)
          into v_fcols, v_fsel
          from information_schema.columns
         where table_schema = 'radar_comercial' and table_name = v_t
           and is_generated = 'NEVER'
           and column_name not in ('id', 'poi_id', 'id_empresa');

        execute format(
            'insert into radar_comercial.%I (poi_id, id_empresa%s) '
            'select m.novo, $1%s '
            '  from radar_comercial.%I f '
            '  join unnest($2::bigint[], $3::bigint[]) as m(velho, novo) '
            '    on m.velho = f.poi_id',
            v_t,
            case when v_fcols is null then '' else ', ' || v_fcols end,
            case when v_fsel is null then '' else ', ' || v_fsel end,
            v_t)
            using v_destino, v_velhos, v_novos;
        get diagnostics v_n = row_count;
        tabela := v_t; linhas := v_n; return next;
    end loop;
end $$;

alter function radar_comercial.reuso_tabelas_filhas() owner to migrator;
alter function radar_comercial.reuso_resumo(double precision, double precision,
                                            double precision, double precision)
    owner to migrator;
alter function radar_comercial.reuso_candidatos(double precision, double precision,
                                                double precision, double precision)
    owner to migrator;
alter function radar_comercial.reusar_pois(bigint[]) owner to migrator;

grant execute on function radar_comercial.reuso_tabelas_filhas() to app_user;
grant execute on function radar_comercial.reuso_resumo(double precision, double precision,
                                                       double precision, double precision)
    to app_user, readonly;
grant execute on function radar_comercial.reuso_candidatos(double precision, double precision,
                                                           double precision, double precision)
    to app_user;
grant execute on function radar_comercial.reusar_pois(bigint[]) to app_user;

comment on function radar_comercial.reusar_pois(bigint[]) is
    'Copia POIs de outras empresas para a empresa da SESSÃO. O destino não é '
    'parâmetro de propósito: é sempre core.empresa_atual(), e por isso esta '
    'função não serve para gravar na empresa alheia.';
