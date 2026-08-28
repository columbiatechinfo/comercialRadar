-- 0041 — inventário e consumo dos proxies
--
-- POR QUE ELAS EXISTEM
--
-- O rodízio de IPs vivia inteiro dentro do processo de mineração, no i9: quem
-- estava em uso, quem tomou castigo e quanto cada um trabalhou morria junto com
-- o processo. O servidor não tinha como responder "o que eu estou pagando e em
-- que estado está", e o operador descobria um IP queimado lendo log.
--
-- Em 28/08/2026 o plano passou de 100 para 500 IPs (250 BR + 250 CO), e a
-- pergunta "quanto gastei" deixou de caber num log que rola para cima.
--
-- DUAS TABELAS, E ELAS RESPONDEM PERGUNTAS DIFERENTES
--
--   `proxy_ip`      o que se PAGA: o plano, como a Webshare o descreve. Uma
--                   linha por IP, atualizada a cada carga do pool.
--   `proxy_evento`  o que se USA: uma linha por pegar/devolver/castigo. É o
--                   histórico, e é dele que sai o consumo por período.
--
-- `proxy_ip` NÃO TEM `tenant_id`, e isto é escolha. O plano é infraestrutura
-- compartilhada: os mesmos 500 IPs servem todas as empresas, e carimbar um dono
-- neles seria inventar uma separação que não existe. Já o EVENTO tem dono — é
-- ele que responde "quanto a Corsan consumiu" — e por isso `tenant_id` vem na
-- primeira coluna do índice, como em todo índice deste banco.
--
-- O ÍNDICE DE EVENTO É POR TEMPO DECRESCENTE porque toda pergunta do monitor é
-- "o que aconteceu ultimamente": as últimas 24 h, a última run, o último uso
-- deste IP. Ordenar ascendente obrigaria a varrer a tabela inteira para mostrar
-- a primeira tela.

CREATE TABLE IF NOT EXISTS proxy_ip (
    id            text PRIMARY KEY,          -- id da Webshare, estável entre cargas
    endereco      text NOT NULL,
    porta         integer NOT NULL,
    pais          text,
    cidade        text,
    -- `ativo` diz se ele veio na ÚLTIMA carga. IP que sai do plano não é
    -- apagado: o histórico de eventos continua apontando para ele, e apagá-lo
    -- transformaria consumo passado em linha órfã.
    ativo         boolean NOT NULL DEFAULT true,
    visto_em      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_proxy_ip_pais ON proxy_ip (pais, ativo);

CREATE TABLE IF NOT EXISTS proxy_evento (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid,
    proxy_id    text NOT NULL,
    -- pegou | devolveu | castigo
    tipo        text NOT NULL,
    motivo      text,
    etapa       text,
    segundos    integer,                     -- duração do castigo
    bytes       bigint,                      -- tráfego, quando conhecido
    em          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_proxy_evento_tenant ON proxy_evento (tenant_id, em DESC);
CREATE INDEX IF NOT EXISTS ix_proxy_evento_proxy ON proxy_evento (proxy_id, em DESC);
CREATE INDEX IF NOT EXISTS ix_proxy_evento_castigo ON proxy_evento (em DESC)
    WHERE tipo = 'castigo';
