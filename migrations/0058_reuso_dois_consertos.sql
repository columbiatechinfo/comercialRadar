-- 0058 — dois defeitos que o primeiro teste do reaproveitamento expôs
--
-- ── 1. `bypassrls` DESLIGOU A TRAVA DE NÍVEL, e isso é sério ───────────────
--
-- A migração 0051 estabeleceu que alterar POI exige nível Administrador. A
-- 0057 deu `bypassrls` ao papel que executa as funções de reúso, para que elas
-- pudessem LER POI de outra empresa — e `bypassrls` não é seletivo: ele desliga
-- a política inteira, para leitura E escrita.
--
-- O resultado, medido no primeiro teste: o usuário de serviço da A2L, NÍVEL 1,
-- copiou POIs com sucesso. A trava que a 0051 construiu não foi contornada por
-- engano de quem chamou; ela simplesmente não se aplicava ali.
--
-- Uma política que se pode desligar precisa ser reafirmada por quem desliga. A
-- função passa a checar o nível explicitamente, e recusa antes de escrever.
--
-- ── 2. DOIS ÍNDICES ÚNICOS SÃO GLOBAIS onde deveriam ser por empresa ───────
--
-- `ux_ifood_merchant (merchant_id)` e `airbnb_anuncio_anuncio_id_key
-- (anuncio_id)` impedem que a MESMA loja do iFood exista em duas empresas —
-- exatamente o que o modelo prevê. A cópia estourou com
--
--     duplicate key value violates unique constraint "ux_ifood_merchant"
--
-- e a mensagem estava certa: o índice é que estava errado. `pois` já resolvia
-- isso desde sempre com `(id_empresa, place_id)`; estas duas ficaram para trás.
--
-- Os demais índices únicos das tabelas filhas são chaves primárias sobre `id`
-- ou `poi_id`, e essas não atrapalham: a cópia gera id novo e aponta para o POI
-- novo.

set search_path to radar_comercial, extensions, public;

-- ── os índices ─────────────────────────────────────────────────────────────
drop index if exists radar_comercial.ux_ifood_merchant;
create unique index if not exists ux_ifood_merchant_por_empresa
    on radar_comercial.ifood_merchant (id_empresa, merchant_id);

alter table radar_comercial.airbnb_anuncio
    drop constraint if exists airbnb_anuncio_anuncio_id_key;
create unique index if not exists ux_airbnb_anuncio_por_empresa
    on radar_comercial.airbnb_anuncio (id_empresa, anuncio_id);

comment on index radar_comercial.ux_ifood_merchant_por_empresa is
    'Por empresa, e não global: a mesma loja do iFood existe uma vez em cada '
    'empresa que a extraiu ou reaproveitou.';
comment on index radar_comercial.ux_airbnb_anuncio_por_empresa is
    'Por empresa, e não global — mesmo motivo do índice do iFood.';

-- ── a trava de nível, reafirmada dentro da função ──────────────────────────
create or replace function radar_comercial.reusar_pois(p_ids bigint[])
returns table (tabela text, linhas bigint)
language plpgsql
security definer
set search_path to 'radar_comercial', 'core', 'extensions', 'public'
as $$
declare
    v_destino uuid := (select core.empresa_atual());
    v_nivel   int  := (select core.nivel_atual());
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

    -- A TRAVA DE NIVEL, REAFIRMADA AQUI.
    --
    -- Esta funcao roda com `bypassrls` para poder LER POI de outra empresa, e
    -- `bypassrls` desliga a politica para escrita tambem. Sem esta checagem, um
    -- usuario nivel 1 cria POI por aqui — foi o que aconteceu no primeiro
    -- teste. Reaproveitar CRIA POI, e criar POI exige Administrador (migracao
    -- 0051).
    if not ((select core.eh_suporte()) or v_nivel >= 4) then
        raise exception
            'reaproveitar cria POI, e criar POI exige nivel Administrador (4). '
            'O seu nivel e %.', coalesce(v_nivel, 0);
    end if;

    select string_agg(quote_ident(column_name), ', ' order by ordinal_position),
           string_agg('o.' || quote_ident(column_name), ', ' order by ordinal_position)
      into v_cols, v_sel
      from information_schema.columns
     where table_schema = 'radar_comercial' and table_name = 'pois'
       and is_generated = 'NEVER'
       and column_name not in ('id', 'id_empresa', 'criado_em',
                               'reusado_de', 'coletado_em');

    drop table if exists _mapa;
    create temp table _mapa (velho bigint, novo bigint);

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

alter function radar_comercial.reusar_pois(bigint[]) owner to reuso_servico;
grant execute on function radar_comercial.reusar_pois(bigint[]) to app_user;

comment on function radar_comercial.reusar_pois(bigint[]) is
    'Copia POIs de outras empresas para a empresa da SESSÃO. O destino não é '
    'parâmetro: é sempre core.empresa_atual(). Exige nível Administrador — a '
    'checagem é explícita porque o `bypassrls` do papel desliga a política.';
