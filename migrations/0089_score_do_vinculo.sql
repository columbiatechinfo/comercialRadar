-- 0089 · o score do vinculo: o sistema mede, o cliente decide.
--
-- REGRA DO DONO DO PRODUTO, 09/09/2026: "quem decide e o cliente, o sistema so
-- mostra os dados e da uma flag em cada um na aba inicial; ai ele filtra a
-- combinacao que pra ele passa a ser adequada de alvos a converter. O sistema
-- so da scores e dados e avaliacao."
--
-- POR QUE AS PARCELAS FICAM SEPARADAS, e nao so o total. O total ordena; as
-- parcelas EXPLICAM e, sobretudo, FILTRAM. Um operador que so confia em prova
-- fisica quer "tem Street View conclusivo E menos de 10 m"; outro que caca
-- delivery quer "tem avaliacao recente", e para ele a fachada nao importa. Com
-- um numero unico gravado, nenhuma das duas perguntas tem resposta — e a
-- promessa era justamente deixar o cliente escolher a combinacao.
--
-- OS PESOS SAO DO DONO DO PRODUTO, e estao aqui como ele os escreveu:
--
--     distancia < 10 m ..................................... 15
--     mesmo telhado ......................................... 5
--     mesmo telhado + telhado comercial .................... 10
--     Street View confirma presenca EXATA, recente ......... 20
--     Street View confirma presenca EXATA, antiga ........... 5
--     Street View confirma comercio, nao exata, recente ..... 5
--     Street View confirma comercio, nao exata, antiga ...... 2
--     avaliacoes recentes (<= 1 ano) ....................... 10
--     fotos do Google validadas pela IA .................... 10
--     posts em rede social (<= 6 meses) .................... 15
--
--     teto: 80
--
-- A PARCELA DE REDE SOCIAL NASCE ZERADA, e nao ausente. Medido em 09/09/2026:
-- o Instagram redireciona o perfil para login em 5 de 5 IPs dos dois unicos
-- blocos /24 do pool, e a API deslogada devolve 429 instantaneo. A coluna
-- existe para que ligar o sinal seja carregar dado, e nao mexer na formula.
create table if not exists radar_comercial.ligacao_score (
    id_empresa   uuid   not null,
    ligacao      text   not null,
    -- as parcelas, para filtrar
    p_distancia  smallint not null default 0,
    p_telhado    smallint not null default 0,
    p_streetview smallint not null default 0,
    p_avaliacoes smallint not null default 0,
    p_fotos      smallint not null default 0,
    p_rede       smallint not null default 0,
    -- o total, para ordenar. GERADO: somado a mao, divergiria das parcelas na
    -- primeira correcao de uma delas.
    total smallint generated always as
        (p_distancia + p_telhado + p_streetview + p_avaliacoes
         + p_fotos + p_rede) stored,
    -- as flags, que sao o que a tela oferece como filtro
    perto_10m        boolean not null default false,
    mesmo_telhado    boolean not null default false,
    telhado_comercial boolean not null default false,
    sv_exata         boolean not null default false,
    sv_comercial     boolean not null default false,
    sv_recente       boolean not null default false,
    aval_recente     boolean not null default false,
    fotos_validadas  boolean not null default false,
    rede_recente     boolean not null default false,
    -- de onde saiu cada ponto, para quem auditar um caso
    detalhe   jsonb,
    pois      smallint,
    calculado_em timestamptz not null default now(),
    constraint pk_ligacao_score primary key (id_empresa, ligacao)
);

comment on table radar_comercial.ligacao_score is
    'Quanta evidencia sustenta o vinculo desta ligacao. O sistema mede; quem '
    'decide o corte e o cliente, filtrando as parcelas na tela.';
comment on column radar_comercial.ligacao_score.p_rede is
    'Posts recentes em rede social. Nasce zerada: em 09/09/2026 o Instagram '
    'fechou a leitura deslogada nos IPs disponiveis. Ligar o sinal e carregar '
    'dado, nao mexer na formula.';

-- O INDICE E O DA TELA: filtrar por faixa de score dentro de uma cidade.
-- `id_empresa` primeiro, como manda o CLAUDE.md.
create index if not exists ix_score_total
    on radar_comercial.ligacao_score (id_empresa, total desc);

alter table radar_comercial.ligacao_score enable row level security;

create policy p_score_le on radar_comercial.ligacao_score for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
create policy p_score_escreve on radar_comercial.ligacao_score for all
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));

grant select, insert, update, delete on radar_comercial.ligacao_score to app_user;
