-- 0099 · a busca web do enriquecimento: uma linha por consulta, com o print e o
-- que a IA leu da página.
--
-- O DESENHO, do dono do produto em 11/09/2026: toda ligação do alvo é buscada
-- pelo ENDEREÇO (logradouro normalizado + número + bairro + cidade + UF +
-- "empresa"), e todo POI com nome de negócio também pelo NOME + cidade + UF.
-- Google na frente, Bing só quando o Google falha em três IPs. A página vira
-- print e texto, e o modelo da Spark devolve os estabelecimentos que ela
-- mostra, de qualquer fonte, oficial ou não; o de OUTRO endereço vem marcado e
-- não entra no dossiê. Provado em 22 + 15 ligações de Canoas (docs/RETOMAR-11-09-2026.md).
--
-- QUEM LÊ. `dossie_ligacao.montar(..., busca_web=...)`, pela função
-- `avaliar_ligacao.busca_web_da(con, ligacao)`: por consulta, a linha do Google
-- que deu certo, e a do Bing só quando a do Google falhou.
--
-- QUEM ESCREVE. `buscar_web.py`. Falha transitória (bloqueio, erro de rede)
-- também vira linha, com `bloqueado`/`erro` — e não conta como feita: a fila
-- olha `ia is not null`.
create table if not exists radar_comercial.busca_web (
    id               bigserial   primary key,
    id_empresa       uuid        not null,
    ligacao          text        not null,
    tipo             text        not null check (tipo in ('endereco', 'nome')),
    -- O POI cujo nome foi buscado; nulo na busca pelo endereço.
    poi_id           bigint,
    consulta         text        not null,
    motor            text        not null check (motor in ('google', 'bing')),
    bloqueado        boolean     not null default false,
    erro             text,
    tentativas       smallint,
    url              text,
    -- O PRINT DA PÁGINA, como a foto de rua em `poi_evidencia`: bytes no banco
    -- ou caminho no Storage.
    dados            bytea,
    storage_path     text,
    bytes_tam        integer,
    chars_texto      integer,
    -- O que o modelo leu: estabelecimentos, pois_confirmados, relacao, justificativa.
    ia               jsonb,
    modelo           text,
    segundos_captura real,
    segundos_ia      real,
    feito_em         timestamptz not null default now()
);
create index if not exists busca_web_por_ligacao
    on radar_comercial.busca_web (id_empresa, ligacao);

alter table radar_comercial.busca_web enable row level security;
drop policy if exists busca_web_ver on radar_comercial.busca_web;
create policy busca_web_ver on radar_comercial.busca_web for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists busca_web_mexer on radar_comercial.busca_web;
create policy busca_web_mexer on radar_comercial.busca_web for all
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()))
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
grant select, insert, update on radar_comercial.busca_web to authenticated;
grant select, insert, update on radar_comercial.busca_web to app_user;
grant usage, select on sequence radar_comercial.busca_web_id_seq to app_user;
grant usage, select on sequence radar_comercial.busca_web_id_seq to authenticated;
comment on table radar_comercial.busca_web is
    'Busca web do enriquecimento por LIGACAO: uma linha por consulta (endereco '
    'ou nome), com o print da pagina e o que o modelo leu dela. Lida pelo '
    'dossie da ligacao.';
