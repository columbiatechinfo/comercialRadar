-- O CASTIGO E A RESERVA DE IP PASSAM A SER DE TODOS, E NAO DE CADA PROCESSO.
--
-- O `ProxyPool` guardava `_in_use`, `_cooldown` e `_last_used` em dicionarios
-- na memoria do processo. Duas rodadas na mesma maquina, ou duas maquinas,
-- nao se enxergavam: podiam entregar O MESMO IP a dois navegadores ao mesmo
-- tempo, e o castigo que uma aprendia era invisivel para a outra, que seguia
-- batendo num IP ja punido.
--
-- Isso e o que realmente doia ao rodar em paralelo — e nao "o Google punir
-- sessoes simultaneas", que foi a explicacao errada que eu dei em 06/09/2026 e
-- que a medicao derrubou: a colheita de tiles nem usa proxy, e 20 navegadores
-- ao mesmo tempo custaram os mesmos 2,2 s de um.
--
-- DUAS TABELAS PEQUENAS, E NAO COLUNAS EM `proxy_ip`. `proxy_ip` e o
-- INVENTARIO: o que o plano tem. Estado que muda a cada segundo — quem esta
-- usando, ate quando descansa — nao pertence ao inventario; misturar faz o
-- catalogo ser reescrito o tempo todo e o histórico de eventos apontar para
-- uma linha que nao para quieta.

-- ── castigo ────────────────────────────────────────────────────────────────
--
-- Uma linha por IP punido. `ate` no passado significa "ja saiu do castigo" —
-- a linha fica, porque saber que ESTE IP foi punido tres vezes hoje vale mais
-- que a tabela estar limpa.
create table if not exists radar_comercial.proxy_castigo (
    proxy_id  text primary key,
    ate       timestamptz not null,
    motivo    text,
    por       text,                     -- maquina que aplicou
    vezes     integer not null default 1,
    em        timestamptz not null default now()
);

comment on table radar_comercial.proxy_castigo is
  'IPs em descanso, visivel a todas as maquinas. Escrito por '
  'ProxyPool.mark_cooldown; lido por em_castigo e pela escolha de IP fixo do '
  'minerar_placeid. Antes de 06/09/2026 isto era um dicionario em memoria, e '
  'duas rodadas nao compartilhavam o que aprendiam.';

create index if not exists ix_proxy_castigo_ate
    on radar_comercial.proxy_castigo (ate desc);

-- ── reserva ────────────────────────────────────────────────────────────────
--
-- Um IP emprestado, com PRAZO. O prazo e o que dispensa faxina: um processo
-- morto nao devolve o que pegou, e sem `ate` o IP ficaria reservado para
-- sempre por um dono que nao existe mais — exatamente o defeito que a fila de
-- jobs resolveu com `visto_em`.
create table if not exists radar_comercial.proxy_reserva (
    proxy_id  text primary key,
    dono      text not null,            -- maquina:pid:navegador
    ate       timestamptz not null,
    em        timestamptz not null default now()
);

comment on table radar_comercial.proxy_reserva is
  'IPs em uso agora, com prazo. `ate` no passado = livre, mesmo que a linha '
  'exista: processo morto nao devolve o que pegou. Renovada enquanto o '
  'navegador trabalha.';

create index if not exists ix_proxy_reserva_ate
    on radar_comercial.proxy_reserva (ate);
-- OS MESMOS DIREITOS DE `proxy_ip`, e nao mais.
--
-- Sem isto o `app_user` — que e quem o minerador usa — enxerga a tabela pelo
-- catalogo e leva `permission denied` na primeira escrita. O erro apareceria
-- no meio de uma rodada de horas, num caminho que so roda quando um IP e
-- punido: o pior lugar possivel para descobrir uma permissao faltando.
--
-- SEM RLS, como `proxy_ip`. Castigo e reserva de IP nao sao dado de cliente:
-- sao inventario de infraestrutura, compartilhado entre todas as empresas.
-- Ligar RLS aqui faria cada tenant punir IPs so para si, que e o oposto do
-- que estas tabelas existem para fazer.

grant select, insert, update, delete
   on radar_comercial.proxy_castigo, radar_comercial.proxy_reserva
   to app_user;

grant select, insert, update, delete, truncate, references, trigger
   on radar_comercial.proxy_castigo, radar_comercial.proxy_reserva
   to migrator;

grant select
   on radar_comercial.proxy_castigo, radar_comercial.proxy_reserva
   to readonly;
