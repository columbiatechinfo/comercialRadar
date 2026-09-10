-- 0096 · o que a IA VÊ na rua deixa de morrer no parágrafo.
--
-- O PROBLEMA. Até aqui a IA olhava quatro fotos de rua e devolvia um veredito
-- e um texto. Tudo o mais que ela enxergava — o número pregado no muro, dois
-- hidrômetros lado a lado, a tampa de esgoto na calçada, o letreiro da loja do
-- vizinho — ia junto na justificativa, em prosa, e não servia para mais nada.
--
-- Regra do dono do produto em 10/09/2026: "isso tem que vir estruturado, pois
-- serve pra outros POIs se alimentarem dos achados uns dos outros. Numerações
-- lidas na imagem, na via que já sabemos qual é, podem ajudar outros pontos a
-- ser identificados".
--
-- POR QUE ISSO VALE TANTO. A foto de rua é tirada de um ponto conhecido — o
-- panorama tem coordenada — e olha para uma via que já sabemos qual é. Quando
-- a IA lê "952" num muro daquela foto, o que se aprendeu não é sobre o POI
-- fotografado: é sobre a RUA. O número 952 daquela via fica ALI, a poucos
-- metros da câmera. E há 10.048 POIs na fila de alocação cujo endereço
-- publicado bate com uma ligação e cuja coordenada está a 228 m de mediana —
-- exatamente o problema que um número lido resolve.
--
-- TRÊS TABELAS, E NÃO UMA COM `tipo`. Cada uma tem consumidor próprio e
-- cardinalidade própria: uma linha por julgamento, N números por julgamento, N
-- fachadas por julgamento. Uma tabela de pares chave-valor serviria às três e
-- a nenhuma — não daria para indexar número por rua, que é a consulta que faz
-- o enriquecimento cruzado existir.

-- ── 1 · o que se viu neste julgamento, uma linha por ligação ──────────────
create table if not exists radar_comercial.leitura_visual (
  id_empresa        uuid not null,
  ligacao           text not null,
  poi_id            bigint references radar_comercial.pois (id) on delete set null,
  -- O NÚMERO DO ALVO, quando deu para ler NA casa julgada. É a leitura mais
  -- valiosa das três: ela confirma ou desmente o vínculo por endereço sem
  -- depender de nenhuma base.
  numero_na_fachada text,
  medidores_agua    smallint,
  medidores_energia smallint,
  -- TAMPA DE ESGOTO na calçada do imóvel: indica coleta na via. Para a
  -- companhia de saneamento é dado de rede, e para o radar é mais um sinal de
  -- que o endereço é servido — uma casa sem coleta raramente abriga comércio
  -- de alimentação.
  tampa_esgoto      boolean,
  lido_em           timestamptz not null default now(),
  primary key (id_empresa, ligacao)
);

-- ── 2 · cada número lido na via, com a câmera que o viu ───────────────────
--
-- ESTA É A TABELA QUE ALIMENTA OS OUTROS PONTOS. `logradouro` vem da ligação
-- julgada (a via é conhecida, não é palpite da IA); `numero` é o que ela leu;
-- `cam_lat/cam_lng` é de onde a foto foi tirada.
create table if not exists radar_comercial.numero_lido (
  id          bigserial primary key,
  id_empresa  uuid not null,
  ligacao     text not null,
  poi_id      bigint references radar_comercial.pois (id) on delete cascade,
  logradouro  text not null,
  cidade      text,
  numero      text not null,
  onde        text,
  certeza     text check (certeza in ('alta', 'media')),
  cam_lat     double precision,
  cam_lng     double precision,
  lido_em     timestamptz not null default now(),
  unique (id_empresa, ligacao, numero, onde)
);

-- A CONSULTA QUE JUSTIFICA A TABELA: "quem mais mora nesta rua com este
-- número?". Sem este índice ela varre tudo, e ela roda uma vez por POI órfão.
create index if not exists numero_lido_via_ix
  on radar_comercial.numero_lido (id_empresa, logradouro, numero);

-- ── 3 · fachada comercial vista, seja ela o alvo ou não ───────────────────
--
-- "NÃO NECESSARIAMENTE O BUSCADO" é o ponto: o letreiro do vizinho prova que
-- há comércio NAQUELA via e naquela quadra, e é candidato a POI que nenhuma
-- base registrou. É a única fonte do projeto que descobre estabelecimento
-- olhando, em vez de cruzar cadastro.
create table if not exists radar_comercial.fachada_vista (
  id          bigserial primary key,
  id_empresa  uuid not null,
  ligacao     text not null,
  poi_id      bigint references radar_comercial.pois (id) on delete cascade,
  texto       text,
  ramo        text,
  e_o_alvo    boolean not null default false,
  onde        text,
  cam_lat     double precision,
  cam_lng     double precision,
  visto_em    timestamptz not null default now()
);

create index if not exists fachada_vista_lig_ix
  on radar_comercial.fachada_vista (id_empresa, ligacao);

-- ── RLS, o mesmo desenho das demais tabelas do radar ──────────────────────
alter table radar_comercial.leitura_visual enable row level security;
alter table radar_comercial.numero_lido    enable row level security;
alter table radar_comercial.fachada_vista  enable row level security;

do $$
declare t text;
begin
  foreach t in array array['leitura_visual', 'numero_lido', 'fachada_vista']
  loop
    -- SUBSELECT NA POLICY, e não a função nua: sem ele o Postgres chama
    -- `empresa_atual()` UMA VEZ POR LINHA. Medido neste projeto: 52x mais
    -- lento na `cadastro_corsan`.
    execute format('drop policy if exists p_%1$s_le on radar_comercial.%1$I', t);
    execute format(
      'create policy p_%1$s_le on radar_comercial.%1$I for select '
      'using (id_empresa = (select core.empresa_atual()))', t);
    execute format('drop policy if exists p_%1$s_escreve on radar_comercial.%1$I', t);
    execute format(
      'create policy p_%1$s_escreve on radar_comercial.%1$I for all '
      'using (id_empresa = (select core.empresa_atual())) '
      'with check (id_empresa = (select core.empresa_atual()))', t);
    -- O GRANT NÃO É O RLS. A policy diz QUAIS linhas; o grant diz SE a tabela
    -- pode ser tocada. A migração 0095 esqueceu exatamente isto e a leitura
    -- morreu com "permission denied" — a quinta vez no projeto.
    execute format('grant select, insert, update, delete '
                   'on radar_comercial.%1$I to app_user', t);
    execute format('grant select on radar_comercial.%1$I to authenticated', t);
  end loop;
end $$;

grant usage, select on sequence radar_comercial.numero_lido_id_seq to app_user;
grant usage, select on sequence radar_comercial.fachada_vista_id_seq to app_user;
