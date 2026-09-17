-- 0123 · a fila de tarefas da frota permanente (dono do produto, 17/09/2026).
--
-- A frota deixa de nascer e morrer com o processo da etapa: vira um SERVIÇO por máquina (`frota_servico.py`), sempre
-- no ar, com navegadores quentes que atravessam jobs e só fecham ao degradar (regra 2). As etapas não abrem navegador:
-- gravam tarefas aqui (`frota_cliente.enviar`), o serviço executa e grava o resultado, e a etapa lê pelo `lote`.
--
-- AMBIENTE EM CADA LINHA, como na `validacao_tarefa`: dev e produção usam o mesmo banco, e cada serviço só pega o seu.
-- O `tipo` é um nome do REGISTRO do serviço (ex.: 'ifood.ponto'), nunca um módulo livre: a fila não executa código
-- arbitrário.
-- Quem escreve: `frota_cliente.py` (cria, cancela) e `frota_servico.py` (pega, batimento, resultado).
set lock_timeout = '5s';
begin;
set local role migrator;

create table if not exists navegacao.tarefa (
    id            bigserial   primary key,
    ambiente      text        not null check (ambiente in ('producao', 'desenvolvimento')),
    site          text        not null,
    tipo          text        not null,
    argumentos    jsonb       not null default '{}'::jsonb,
    lote          text        not null,                 -- quem pediu espera por ele (ex.: 'job68:ifood:1726600000')
    pedido_por    text,                                 -- processo que enviou
    prioridade    smallint    not null default 5,       -- menor primeiro
    estado        text        not null default 'fila'
                  check (estado in ('fila', 'rodando', 'ok', 'erro', 'cancelada')),
    tentativas    smallint    not null default 0,
    maquina       text,
    dono          text,                                 -- a vaga que executa: máquina/contêiner:pid:site:vaga
    resultado     jsonb,
    erro          text,
    criado_em     timestamptz not null default now(),
    pego_em       timestamptz,
    visto_em      timestamptz,
    terminado_em  timestamptz
);
create index if not exists ix_nav_tarefa_fila on navegacao.tarefa (ambiente, site, prioridade, id) where estado = 'fila';
create index if not exists ix_nav_tarefa_lote on navegacao.tarefa (lote, estado);
create index if not exists ix_nav_tarefa_rodando on navegacao.tarefa (visto_em) where estado = 'rodando';

-- PEGAR: atômico entre máquinas, e devolve à fila o que ficou "rodando" sem batimento (serviço morto).
create or replace function navegacao.pegar_tarefas(p_ambiente text, p_site text, p_maquina text, p_n integer,
                                                    p_sem_sinal_s integer default 180)
returns setof navegacao.tarefa language plpgsql as $$
begin
    update navegacao.tarefa set estado = 'fila', maquina = null, dono = null
     where ambiente = p_ambiente and site = p_site and estado = 'rodando'
       and visto_em < now() - make_interval(secs => p_sem_sinal_s);
    return query
    with alvo as (
        select id from navegacao.tarefa
         where ambiente = p_ambiente and site = p_site and estado = 'fila'
         order by prioridade, id
         limit greatest(p_n, 0)
         for update skip locked)
    update navegacao.tarefa t
       set estado = 'rodando', maquina = p_maquina, pego_em = now(), visto_em = now(), tentativas = t.tentativas + 1
      from alvo where t.id = alvo.id
    returning t.*;
end $$;

comment on table navegacao.tarefa is
  'Fila da frota permanente (17/09/2026): a etapa grava tarefas por lote, o servico da maquina executa com navegadores '
  'quentes e grava resultado. tipo = nome do registro do servico, nunca modulo livre.';

grant select, insert, update on navegacao.tarefa to app_user;
grant usage, select on sequence navegacao.tarefa_id_seq to app_user;
grant execute on function navegacao.pegar_tarefas(text, text, text, integer, integer) to app_user;
grant select on navegacao.tarefa to readonly;
commit;

do $$ begin
    if exists (select 1 from pg_roles where rolname = 'frota_leitor') then
        execute 'grant select (id, ambiente, site, tipo, lote, pedido_por, prioridade, estado, tentativas, maquina, dono,
                               erro, criado_em, pego_em, visto_em, terminado_em) on navegacao.tarefa to frota_leitor';
    end if;
end $$;
