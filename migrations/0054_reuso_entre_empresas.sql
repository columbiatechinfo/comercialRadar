-- 0054 — o POI reaproveitado sabe de onde veio e de quando é o dado
--
-- A DINÂMICA QUE ESTA MIGRAÇÃO SUSTENTA
--
-- O mapa é neutro; o POI tem dono. O mesmo estabelecimento existe uma vez por
-- empresa — os índices únicos da `pois` já são `(id_empresa, place_id)` e
-- `(id_empresa, nome, endereço)`, então isso sempre foi possível. O que faltava
-- era a escolha na hora de extrair:
--
--   REAPROVEITAR  copia para a sua empresa o que já foi extraído dentro da área
--                 por qualquer outra, e coleta só o que é novo. Poupa
--                 processamento; o dado pode estar velho.
--   DO ZERO       coleta tudo de novo. Cada empresa fica com os mesmos POIs,
--                 mas com os SEUS.
--
-- Nos dois casos a exibição é a mesma: cada empresa vê e conta só o que é dela.
-- Isso a RLS já garante — nenhuma tela precisa lembrar de filtrar.
--
-- POR QUE `coletado_em` E NÃO SÓ `criado_em`
--
-- Num POI copiado as duas datas são coisas diferentes: `criado_em` é quando a
-- CÓPIA nasceu, `coletado_em` é quando o dado foi de fato colhido do mundo.
-- Sem separá-las, um POI reaproveitado de uma coleta de seis meses atrás
-- apareceria como "criado hoje" — e a única desvantagem do reaproveitamento,
-- que é a idade do dado, ficaria invisível justamente para quem escolheu
-- aceitá-la.
--
-- Para POI coletado direto as duas são iguais, e o backfill abaixo faz isso.
--
-- `reusado_de` GUARDA O POI DE ORIGEM, de outra empresa. Não é chave
-- estrangeira de propósito: a linha de origem pode ser apagada pelo dono dela,
-- e isso não deve levar junto a cópia de quem reaproveitou — a cópia é dado da
-- outra empresa, não um espelho. Fica o número, que ainda serve para auditar
-- "de onde veio isto" enquanto a origem existir.

-- RODA COMO SUPERUSUÁRIO, E NÃO COMO `migrator`.
--
-- `pois` tem FORCE ROW LEVEL SECURITY: a política vale até para o DONO da
-- tabela. Numa sessão de `migrator` não há usuário declarado, então
-- `core.empresa_atual()` é nulo e nenhuma linha é visível — o backfill abaixo
-- rodou e disse "UPDATE 0" sobre 301.297 linhas, sem erro nenhum. A migração
-- teria sido dada por aplicada com a coluna inteira nula.
--
--   docker exec supabase-db psql -U supabase_admin -d a2l -f 0054.sql
--
-- Migração que só mexe em ESTRUTURA pode rodar sob `set role migrator`; esta
-- mexe em DADO, e por isso não pode.

set search_path to radar_comercial, public;

alter table pois
    add column if not exists reusado_de  bigint,
    add column if not exists coletado_em timestamptz;

-- Todo POI que existe hoje foi coletado direto: as duas datas coincidem.
update pois set coletado_em = criado_em where coletado_em is null;

alter table pois alter column coletado_em set default now();

create index if not exists pois_reusado_de on pois (id_empresa, reusado_de)
    where reusado_de is not null;

comment on column pois.reusado_de is
    'Id do POI de origem, em outra empresa, quando esta linha nasceu de um '
    'reaproveitamento. Nulo quando o POI foi coletado direto. NÃO é chave '
    'estrangeira: apagar a origem não pode apagar a cópia.';
comment on column pois.coletado_em is
    'Quando o dado foi colhido do mundo. Difere de `criado_em` só no POI '
    'reaproveitado, e é essa diferença que mostra a idade do dado.';
