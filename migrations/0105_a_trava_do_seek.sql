-- 0105 · a trava da ligacao em analise na SEEK (14/09/2026)
--
-- Decisao do dono do produto: "um usuario nao consegue editar uma ligacao que
-- esta em visualizacao por outro". Quem abre uma ligacao para decidir fica com
-- ela; na fila dos outros ela aparece cinza, "em analise por <nome>", e o
-- `POST /api/seek/decidir` recusa a decisao de quem nao e o dono (409).
--
-- ESTADO, E NAO HISTORICO: uma linha por ligacao travada, sobrescrita quando
-- outro assume uma trava vencida. O historico das aberturas mora em
-- `seek_abertura` (0106).
--
-- NO BANCO, E NAO NA MEMORIA DA API: producao e desenvolvimento sao dois
-- processos sobre o mesmo banco. Trava guardada em memoria valeria so para quem
-- abriu pelo mesmo processo.
--
-- VENCE SOZINHA: `expira_em` e empurrado a cada sinal da tela (a cada 30 s); sem
-- sinal por 2 minutos a trava deixa de valer mesmo que ninguem apague a linha.
-- A linha vencida e varrida pela API (delete ... returning) e a liberacao vai
-- por evento para as outras telas.
--
--   sessao   a ABA que travou. A mesma pessoa com duas abas nao se bloqueia, e a
--            troca de ligacao numa aba solta so a trava daquela aba.
--   desde    quando esta pessoa abriu a ligacao (nao muda com o sinal).
--
-- SOB CARGA a tabela fica do tamanho das pessoas decidindo agora (uma linha por
-- aba ativa), e nao do cadastro: as vencidas saem na varredura. Os acessos sao
-- todos pela chave (id_empresa, ligacao) ou por (id_empresa, quem, sessao).
--
-- O SINAL E UM UPDATE A CADA 30 s POR ABA, e por isso NENHUM INDICE leva
-- `sinal_em` ou `expira_em`: sem coluna indexada mudando, o Postgres faz o update
-- HOT (na mesma pagina, sem entrada nova em indice) — com 10 mil abas sao ~330
-- updates/s que nao incham indice. O `fillfactor` deixa espaco na pagina para
-- isso. A varredura das vencidas vai pelo prefixo da chave (id_empresa) e filtra
-- `expira_em` nas poucas linhas vivas da empresa.
create table if not exists radar_comercial.seek_trava (
    id_empresa  uuid        not null,
    ligacao     text        not null,
    quem        uuid        not null,
    quem_nome   text,
    sessao      uuid        not null,
    desde       timestamptz not null default now(),
    sinal_em    timestamptz not null default now(),
    expira_em   timestamptz not null,
    primary key (id_empresa, ligacao)
) with (fillfactor = 70);
alter table radar_comercial.seek_trava set (fillfactor = 70);
-- (a primeira versao desta migracao, aplicada em 14/09/2026, criava um indice em
-- (id_empresa, expira_em) que impedia o update HOT do sinal; sai aqui)
drop index if exists radar_comercial.seek_trava_vencimento;
create index if not exists seek_trava_por_quem
    on radar_comercial.seek_trava (id_empresa, quem, sessao);

alter table radar_comercial.seek_trava enable row level security;
drop policy if exists seek_trava_ver on radar_comercial.seek_trava;
create policy seek_trava_ver on radar_comercial.seek_trava for select
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_trava_gravar on radar_comercial.seek_trava;
create policy seek_trava_gravar on radar_comercial.seek_trava for insert
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
-- ASSUMIR UMA TRAVA VENCIDA DE OUTRO e UPDATE da linha dele: a politica e da
-- empresa, e a regra "so o dono ou vencida" fica no `where` do upsert da API.
drop policy if exists seek_trava_mudar on radar_comercial.seek_trava;
create policy seek_trava_mudar on radar_comercial.seek_trava for update
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()))
    with check ((select core.eh_suporte())
                or id_empresa = (select core.empresa_atual()));
drop policy if exists seek_trava_soltar on radar_comercial.seek_trava;
create policy seek_trava_soltar on radar_comercial.seek_trava for delete
    using ((select core.eh_suporte())
           or id_empresa = (select core.empresa_atual()));
grant select, insert, update, delete on radar_comercial.seek_trava to authenticated;
grant select, insert, update, delete on radar_comercial.seek_trava to app_user;
