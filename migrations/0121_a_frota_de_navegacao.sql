-- 0121 · a frota de navegação (dono do produto, 17/09/2026).
--
-- Todo acesso a site de coleta passa a sair de uma frota única: navegador Camoufox que vive até degradar, proxy
-- pego de forma atômica entre as máquinas, castigo por site escalonado, cookie guardado por site e IP, e cada página
-- registrada. As 10 regras foram decididas uma a uma na caixa (memória `frota-de-navegacao-regras`).
--
-- SCHEMA PRÓPRIO E SEM RLS, DE PROPÓSITO. É infraestrutura comum a todas as empresas — IP, sessão de navegador,
-- cookie de site público — e nenhuma linha guarda dado de cliente. A credencial do proxy NÃO vem para o banco: fica
-- na API da Webshare e no `.env`, como hoje.
--
-- AS TABELAS ANTIGAS FICAM (`proxy_ip`, `proxy_evento`, `proxy_castigo`, `proxy_reserva`): os passos que ainda não
-- migraram continuam nelas até a vez deles.

set lock_timeout = '5s';
begin;
-- O schema nasce do administrador (o `migrator` não cria schema no banco) e já com o `migrator` de dono; o resto
-- é criado COMO `migrator`, igual ao `radar_comercial`.
create schema if not exists navegacao authorization migrator;
set local role migrator;

-- ── os IPs ───────────────────────────────────────────────────────────────────────────────────────────────────────
create table if not exists navegacao.proxy (
    id        text primary key,                       -- id da Webshare, estável entre cargas
    endereco  text not null,
    porta     integer not null,
    pais      text,
    cidade    text,
    provedor  text not null default 'webshare',
    ativo     boolean not null default true,          -- veio na última carga do plano
    visto_em  timestamptz not null default now()
);
create index if not exists ix_nav_proxy_pais on navegacao.proxy (pais, ativo);

-- ── a saúde de cada IP em cada site (regra 3: castigo por site, escalonado) ──────────────────────────────────────
create table if not exists navegacao.proxy_site (
    proxy_id       text not null references navegacao.proxy (id),
    site           text not null,                     -- maps_varredura, maps_ficha, streetview, busca, serasa, ifood, airbnb...
    sucessos       bigint not null default 0,
    falhas         bigint not null default 0,
    ultimo_ok      timestamptz,
    ultima_falha   timestamptz,
    ultimo_motivo  text,
    castigo_ate    timestamptz,
    castigo_nivel  smallint not null default 0,       -- 0 limpo · 1: 15 min · 2: 1 h · 3: 6 h · 4: 24 h
    usado_em       timestamptz,
    primary key (site, proxy_id)
);
create index if not exists ix_nav_proxy_site_castigo on navegacao.proxy_site (site, castigo_ate);

-- ── quem está usando cada IP em cada site agora (um IP, uma sessão por site, em qualquer máquina) ────────────────
create table if not exists navegacao.reserva (
    site      text not null,
    proxy_id  text not null references navegacao.proxy (id),
    dono      text not null,                           -- máquina:pid:sessão
    ate       timestamptz not null,
    em        timestamptz not null default now(),
    primary key (site, proxy_id)
);

-- ── cada navegador aberto ────────────────────────────────────────────────────────────────────────────────────────
create table if not exists navegacao.sessao (
    id                 bigserial primary key,
    maquina            text not null,
    processo           text,
    site               text not null,
    motor              text not null default 'camoufox',
    proxy_id           text references navegacao.proxy (id),
    estado             text not null default 'aquecendo'
                       check (estado in ('aquecendo', 'ativa', 'degradada', 'fechada')),
    aberta_em          timestamptz not null default now(),
    ultima_atividade   timestamptz not null default now(),
    paginas            integer not null default 0,
    falhas_seguidas    integer not null default 0,
    fechada_em         timestamptz,
    motivo_fechamento  text
);
create index if not exists ix_nav_sessao_viva on navegacao.sessao (site, estado) where estado <> 'fechada';

-- ── o cookie de cada site em cada IP (regra 6), reaproveitado entre máquinas ─────────────────────────────────────
create table if not exists navegacao.cookie (
    site       text not null,
    proxy_id   text not null references navegacao.proxy (id),
    estado     jsonb not null,                         -- storage_state do navegador: cookies e armazenamento
    valido     boolean not null default true,
    criado_em  timestamptz not null default now(),
    usado_em   timestamptz not null default now(),
    primary key (site, proxy_id)
);

-- ── cada página ou requisição (regra 7): particionada por dia, 30 dias de detalhe ─────────────────────────────────
create table if not exists navegacao.evento (
    em         timestamptz not null default now(),
    sessao_id  bigint,
    site       text not null,
    proxy_id   text,
    resultado  text not null,                          -- ok · vazio · bloqueio · captcha · captcha_resolvido · timeout · erro · http_4xx · http_5xx
    ms         integer,
    bytes      bigint,
    detalhe    text
) partition by range (em);
create index if not exists ix_nav_evento_site on navegacao.evento (site, em);
create index if not exists ix_nav_evento_proxy on navegacao.evento (proxy_id, em);

create table if not exists navegacao.resumo_diario (
    dia        date not null,
    site       text not null,
    proxy_id   text not null,
    ok         integer not null default 0,
    vazio      integer not null default 0,
    bloqueio   integer not null default 0,
    captcha    integer not null default 0,
    timeout    integer not null default 0,
    erro       integer not null default 0,
    bytes      bigint not null default 0,
    ms_medio   integer,
    primary key (dia, site, proxy_id)
);

-- ── as partições do dia ──────────────────────────────────────────────────────────────────────────────────────────
-- SECURITY DEFINER nas duas que criam e apagam partição: o `app_user` da frota não tem (nem deve ter) CREATE no schema.
create or replace function navegacao.garantir_particoes(p_dias integer default 3) returns void
language plpgsql security definer set search_path = navegacao, pg_temp as $$
declare d date;
begin
    for i in 0 .. greatest(p_dias, 1) loop
        d := current_date + i;
        execute format('create table if not exists navegacao.evento_%s partition of navegacao.evento
                          for values from (%L) to (%L)', to_char(d, 'YYYYMMDD'), d, d + 1);
    end loop;
end $$;
select navegacao.garantir_particoes(7);

-- ── pegar um IP para um site (regras 4 e 5): atômico entre máquinas, Brasil antes da Colômbia ────────────────────
-- Devolve null quando não há IP livre: quem chama ESPERA e tenta de novo — nunca cai no IP da casa.
create or replace function navegacao.pegar_proxy(p_site text, p_dono text, p_segundos integer default 1800,
                                                 p_paises text[] default array['BR', 'CO'])
returns text language plpgsql as $$
declare c text; w text;
begin
    -- A CHAVE DA RESERVA DECIDE, e não uma trava na linha do IP: duas máquinas pedindo o mesmo IP para o mesmo site
    -- disputam o `insert ... on conflict`, e só uma leva. Pedidos de sites diferentes nem se enxergam.
    for c in
        select p.id
          from navegacao.proxy p
          left join navegacao.proxy_site s on s.site = p_site and s.proxy_id = p.id
          left join navegacao.reserva r on r.site = p_site and r.proxy_id = p.id and r.ate > now()
         where p.ativo and p.pais = any (p_paises)
           and r.proxy_id is null
           and (s.castigo_ate is null or s.castigo_ate <= now())
         order by array_position(p_paises, p.pais),     -- Brasil antes; Colômbia só sem Brasil livre
                  coalesce(s.castigo_nivel, 0),         -- quem nunca foi punido neste site primeiro
                  s.usado_em nulls first,               -- rodízio: o que descansou mais
                  random()
         limit 32
    loop
        insert into navegacao.reserva as r (site, proxy_id, dono, ate)
        values (p_site, c, p_dono, now() + make_interval(secs => p_segundos))
        on conflict (site, proxy_id) do update set dono = excluded.dono, ate = excluded.ate, em = now()
           where r.ate <= now()
        returning r.proxy_id into w;
        if w is not null then
            insert into navegacao.proxy_site (site, proxy_id, usado_em) values (p_site, w, now())
            on conflict (site, proxy_id) do update set usado_em = now();
            return w;
        end if;
    end loop;
    return null;
end $$;

create or replace function navegacao.renovar_reserva(p_site text, p_proxy text, p_dono text, p_segundos integer default 1800)
returns boolean language sql as $$
    update navegacao.reserva set ate = now() + make_interval(secs => p_segundos)
     where site = p_site and proxy_id = p_proxy and dono = p_dono
    returning true
$$;

create or replace function navegacao.soltar_proxy(p_site text, p_proxy text, p_dono text) returns void
language sql as $$
    delete from navegacao.reserva where site = p_site and proxy_id = p_proxy and dono = p_dono
$$;

-- ── castigo escalonado por site (regra 3): 15 min, 1 h, 6 h, 24 h; zera depois de um dia limpo ──────────────────
create or replace function navegacao.castigar(p_site text, p_proxy text, p_motivo text) returns timestamptz
language plpgsql as $$
declare nivel smallint; ate timestamptz;
begin
    select case when s.ultima_falha is not null and s.ultima_falha > now() - interval '24 hours'
                then least(s.castigo_nivel + 1, 4) else 1 end
      into nivel
      from navegacao.proxy_site s where s.site = p_site and s.proxy_id = p_proxy;
    nivel := coalesce(nivel, 1);
    ate := now() + (array[interval '15 minutes', interval '1 hour', interval '6 hours', interval '24 hours'])[nivel];
    insert into navegacao.proxy_site (site, proxy_id, falhas, ultima_falha, ultimo_motivo, castigo_ate, castigo_nivel)
    values (p_site, p_proxy, 1, now(), p_motivo, ate, nivel)
    on conflict (site, proxy_id) do update
       set falhas = navegacao.proxy_site.falhas + 1, ultima_falha = now(), ultimo_motivo = p_motivo,
           castigo_ate = ate, castigo_nivel = nivel;
    return ate;
end $$;

-- ── o resumo diário e a limpeza dos 30 dias ──────────────────────────────────────────────────────────────────────
create or replace function navegacao.consolidar(p_guardar_dias integer default 30) returns integer
language plpgsql security definer set search_path = navegacao, pg_temp as $$
declare t record; apagadas integer := 0;
begin
    insert into navegacao.resumo_diario (dia, site, proxy_id, ok, vazio, bloqueio, captcha, timeout, erro, bytes, ms_medio)
    select em::date, site, coalesce(proxy_id, '-'),
           count(*) filter (where resultado in ('ok', 'captcha_resolvido')),
           count(*) filter (where resultado = 'vazio'),
           count(*) filter (where resultado = 'bloqueio'),
           count(*) filter (where resultado = 'captcha'),
           count(*) filter (where resultado = 'timeout'),
           count(*) filter (where resultado in ('erro', 'http_4xx', 'http_5xx')),
           coalesce(sum(bytes), 0), avg(ms)::integer
      from navegacao.evento
     where em >= coalesce((select max(dia) + 1 from navegacao.resumo_diario), current_date - p_guardar_dias)
       and em < current_date                            -- só dia fechado, e só o que ainda não foi resumido
     group by 1, 2, 3
    on conflict (dia, site, proxy_id) do update
       set ok = excluded.ok, vazio = excluded.vazio, bloqueio = excluded.bloqueio, captcha = excluded.captcha,
           timeout = excluded.timeout, erro = excluded.erro, bytes = excluded.bytes, ms_medio = excluded.ms_medio;
    for t in select c.relname from pg_inherits i join pg_class c on c.oid = i.inhrelid
              where i.inhparent = 'navegacao.evento'::regclass
                and c.relname < 'evento_' || to_char(current_date - p_guardar_dias, 'YYYYMMDD') loop
        execute format('drop table if exists navegacao.%I', t.relname);
        apagadas := apagadas + 1;
    end loop;
    perform navegacao.garantir_particoes(7);
    return apagadas;
end $$;

comment on schema navegacao is
  'Frota unica de navegacao (17/09/2026): IPs, saude por site, reservas, sessoes de navegador, cookies e eventos. '
  'Infraestrutura comum, sem dado de cliente e sem RLS. Escrito pela biblioteca navegacao/.';
comment on table navegacao.proxy_site is
  'Saude de cada IP em cada site. castigo_nivel 1..4 = 15 min, 1 h, 6 h, 24 h; volta a 1 depois de 24 h sem falha.';
comment on table navegacao.reserva is
  'IP em uso por uma sessao, por site, com prazo. ate no passado = livre: processo morto nao devolve o que pegou.';
comment on table navegacao.cookie is
  'storage_state do navegador por site x IP, reaproveitado entre maquinas. Nunca guarda credencial.';
comment on table navegacao.evento is
  'Uma linha por pagina ou requisicao. Particionada por dia; consolidar() resume em resumo_diario e apaga apos 30 dias.';

grant usage on schema navegacao to app_user, readonly;
grant select on all tables in schema navegacao to readonly;
grant select, insert, update, delete on all tables in schema navegacao to app_user;
grant usage, select on all sequences in schema navegacao to app_user;
grant execute on all functions in schema navegacao to app_user;
alter default privileges in schema navegacao grant select, insert, update, delete on tables to app_user;

commit;
