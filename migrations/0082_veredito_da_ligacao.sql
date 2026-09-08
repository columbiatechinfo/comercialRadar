-- 0082 · o veredito passa a ser da LIGACAO, e nao do POI.
--
-- O QUE ESTAVA ERRADO, medido em 08/09/2026 a partir de um caso trazido pelo
-- dono do produto: a Madeireira Maravilha existe SEIS vezes na base — duas do
-- estadual, duas do IBGE, uma da Receita e uma do Maps. Cada cópia foi julgada
-- sozinha, com o pedaço de evidência que por acaso trouxe, e o resultado foram
-- vereditos que se contradizem sobre o MESMO hidrômetro:
--
--     ligação 347087   estadual, ibge, maps   (sem) | aprovado | revisão
--     ligação 347100   estadual, ibge         aprovado | revisão
--
-- E o POI do Maps — o único com nota 4,0, 18 comentários e o endereço certo —
-- era justamente o "(sem)": ainda não julgado.
--
-- NA BASE INTEIRA: 91.199 ligações têm POI vinculado, 77.564 delas (85 por
-- cento) têm MAIS DE UM, e 18.249 carregam vereditos contraditórios — 11.442
-- com aprovado e revisão ao mesmo tempo, 1.889 com aprovado e reprovado.
--
-- POR QUE NAO E DUPLICATA A ELIMINAR. Foi a leitura que eu tentei primeiro, e
-- o dono do produto corrigiu: "o foco é a instalação, não os POIs; eles
-- enriquecem a instalação a que se fundem, dando confiabilidade a ela". Seis
-- fontes independentes apontando o mesmo hidrômetro é o sistema funcionando —
-- é convergência, não ruído. `fontes_para_poi.py` já diz isso no cabeçalho:
-- "NÃO HÁ CRUZAMENTO AQUI. Cada fonte insere o que tem, com fonte própria."
--
-- O erro não era existirem seis. Era cada um decidir sozinho.
create table if not exists radar_comercial.ligacao_veredito (
    id            bigserial   primary key,
    id_empresa    uuid        not null,
    -- `ligacao` E TEXTO, como em `ligacao_poi`. A base do cliente guarda
    -- `num_ligacao` como bigint, mas o vínculo carrega texto, e converter aqui
    -- criaria um lugar a mais onde os dois formatos podem divergir.
    ligacao       text        not null,
    id_base       bigint,
    veredito      text        not null,
    justificativa text,
    confianca     numeric,
    -- QUANTOS POIs SUSTENTAM ESTE VEREDITO, e de quantas fontes distintas.
    -- É a medida de confiabilidade que o dono do produto descreveu: uma
    -- ligação com estadual, IBGE e Maps concordando vale mais que uma com um
    -- POI só, e quem for à porta precisa ver essa diferença.
    pois          smallint    not null default 0,
    fontes        smallint    not null default 0,
    imagens       smallint    not null default 0,
    -- O DOSSIÊ INTEIRO, como foi montado e o que o modelo respondeu. Sem isto
    -- não há como auditar por que uma ligação foi aprovada meses depois.
    percepcao     jsonb,
    modelo        text,
    segundos      numeric,
    avaliado_em   timestamptz not null default now(),
    -- Mesma bandeira da migração 0078, agora no nível certo: há veredito e
    -- falta escolher a instalação. Aqui ela só existe para o caso inverso —
    -- POI julgado que ainda não achou ligação continua em `poi_veredito`.
    alocar_instalacao boolean not null default false
);

create unique index if not exists ligacao_veredito_chave
    on radar_comercial.ligacao_veredito (id_empresa, ligacao);

-- `id_empresa` PRIMEIRO, por regra de escala do projeto: a política RLS é
-- avaliada por linha, e o índice só ajuda se o tenant vier na frente.
create index if not exists ligacao_veredito_por_veredito
    on radar_comercial.ligacao_veredito (id_empresa, veredito);

alter table radar_comercial.ligacao_veredito enable row level security;

drop policy if exists ligacao_veredito_ver on radar_comercial.ligacao_veredito;
create policy ligacao_veredito_ver on radar_comercial.ligacao_veredito for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));

-- AS CHAMADAS EM SUBSELECT, e não nuas. Ver a migração 0081: sem isso o
-- Postgres avalia a função uma vez POR LINHA, e foi assim que uma consulta
-- passou a custar 14,5 s.
drop policy if exists ligacao_veredito_mexer on radar_comercial.ligacao_veredito;
create policy ligacao_veredito_mexer on radar_comercial.ligacao_veredito for all
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()))
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));

grant select, insert, update on radar_comercial.ligacao_veredito to authenticated;
grant select, insert, update on radar_comercial.ligacao_veredito to app_user;
grant usage, select on sequence radar_comercial.ligacao_veredito_id_seq to app_user;
grant usage, select on sequence radar_comercial.ligacao_veredito_id_seq to authenticated;

comment on table radar_comercial.ligacao_veredito is
    'Veredito por LIGACAO de agua, decidido com o dossie de TODOS os POIs '
    'vinculados a ela. Substitui poi_veredito como resposta oficial; os '
    'vereditos por POI seguem valendo como evidencia dentro do dossie.';
