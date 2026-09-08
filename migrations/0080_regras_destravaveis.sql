-- 0080 · regra do sistema com trava: o padrao decidido, e o botao para mudar.
--
-- Duas decisoes de 07/09/2026 nasceram como comportamento fixo no codigo, e
-- as duas sao o tipo de coisa que muda de resposta conforme a operacao:
--
--   · o TETO de revisao humana quando a unica prova e uma foto publicada;
--   · CAPTURAR fachada de POI que nao tem ligacao vinculada.
--
-- Escrever isso em `if` no Python resolve hoje e cobra depois: mudar de ideia
-- vira commit, build, deploy e reinicio de container, para inverter um
-- booleano. Pior, ninguem que opera o sistema consegue ver qual regra esta
-- valendo sem ler codigo — e a regra que ninguem ve e a regra que surpreende.
--
-- Aqui a decisao fica DECLARADA e DESTRAVAVEL: o padrao e o que foi decidido,
-- e a trava abre no painel, com o texto explicando o que muda e o que custa.
create table if not exists radar_comercial.regra (
    -- A CHAVE E O NOME NO CODIGO, e nao um numero. Quem le
    -- `regra_ativa("teto_prova_so_foto")` no Python entende sem consultar
    -- tabela nenhuma.
    chave         text        not null,
    id_empresa    uuid        not null,
    ativo         boolean     not null default false,
    rotulo        text        not null,
    -- O TEXTO E PARTE DA REGRA, nao enfeite. Ele responde "o que muda se eu
    -- destravar isto" para quem nao acompanhou a conversa em que se decidiu.
    descricao     text        not null,
    -- O QUE CUSTA DESTRAVAR, em palavras de operacao — horas de proxy,
    -- chamadas de modelo, risco de visita perdida.
    custo         text        not null default '',
    atualizado_em timestamptz not null default now(),
    atualizado_por text,
    primary key (id_empresa, chave)
);

-- `id_empresa` E A PRIMEIRA COLUNA DA CHAVE, por regra de escala do projeto:
-- politica RLS e avaliada por linha, e o indice so ajuda se o tenant vier na
-- frente.
alter table radar_comercial.regra enable row level security;

drop policy if exists regra_ver on radar_comercial.regra;
create policy regra_ver on radar_comercial.regra for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));

-- MEXER E NIVEL 4, o mesmo do catalogo de categorias. Destravar a captura de
-- 19 mil pontos ou mudar o teto do veredito e decisao de quem responde pelo
-- produto, nao de quem opera a tela.
--
-- A CHAMADA VAI EM SUBSELECT — `(select core.empresa_atual())` e nao
-- `core.empresa_atual()`. Sem isso o Postgres avalia a funcao UMA VEZ POR
-- LINHA; com isso, uma vez por consulta. Vale pouco nesta tabela de duas
-- linhas e vale muito no habito.
drop policy if exists regra_mexer on radar_comercial.regra;
create policy regra_mexer on radar_comercial.regra for all
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual())
               and (select core.nivel_atual()) >= 4))
    with check ((select core.eh_suporte())
                or (id_empresa = (select core.empresa_atual())
                    and (select core.nivel_atual()) >= 4));

grant select, insert, update on radar_comercial.regra to authenticated;

-- ── as duas regras de hoje ───────────────────────────────────────────────
insert into radar_comercial.regra
    (chave, id_empresa, ativo, rotulo, descricao, custo)
values
 ('teto_prova_so_foto', 'd4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2', true,
  'Prova só-foto não aprova sozinha',
  'Quando a fachada não mostra nenhum sinal comercial e a única prova é a '
  'foto que o dono publicou no Google, o veredito não passa de "revisão '
  'humana". A foto de uma instalação fixa — toldo, letreiro montado, balcão, '
  'oficina, freezer de produção — continua aprovando; a foto de ofício — uma '
  'mão com unhas feitas, um notebook na bancada, um panfleto — não, porque '
  'prova que o negócio existe e não que ele funciona NESTE endereço. '
  'Avaliação de cliente dos últimos dois anos dispensa o teto.',
  'Destravado (regra ativa): ~28% das aprovações caem para revisão humana e '
  'viram fila de gente. Travado: aprova mais, e parte das visitas vai a '
  'endereço onde ninguém provou que o negócio opera.'),
 ('capturar_sem_ligacao', 'd4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2', false,
  'Fotografar POI sem ligação vinculada',
  'A fila da captura de fachada exige que o POI já tenha uma ligação de água '
  'vinculada — sem saber qual imóvel é, não há o que fotografar. Com esta '
  'regra ativa a captura também sai para os pontos que o cruzamento não '
  'conseguiu ligar a hidrômetro nenhum, e eles passam a poder ser julgados, '
  'ficando marcados com ALOCAR INSTALAÇÃO para alguém escolher a ligação.',
  'Destravar: 19.509 pontos com coordenada, cerca de 11 h de proxy, mais a '
  'fila da IA depois. Desses, 6.166 são endereço de registro de CNPJ da '
  'Receita — a fonte com maior reprovação medida (51,7%).')
on conflict (id_empresa, chave) do nothing;
