-- 0036 — a fusão ganha colunas próprias e para de destruir o `status`.
--
-- O PROBLEMA, e como ele apareceu
--
-- Absorver um POI fazia `update pois set status = 'fundido'`. `status` não é
-- um campo de fusão: ele guarda a ORIGEM do ponto — `estadual`, `descoberto`,
-- `ok`, `recuperado_web`, `cadastur` — e é exibido na ficha (`server.py:936`) e
-- contado nas estatísticas do painel (`server.py:1123`, `:1368`). Sobrescrevê-lo
-- apagava esse dado para sempre.
--
-- Duas consequências, as duas medidas em 27/08/2026:
--
-- 1. NÃO HÁ COMO DESFAZER UMA FUSÃO EM LOTE. `--recruzar` reavalia os pares,
--    mas o `carregar` exclui `status = 'fundido'` — os 15.399 POIs já fundidos
--    de Canoas nunca voltam a ser comparados. Fusões erradas ficam erradas:
--    `ParkShoppingCanoas` segue absorvido pela `Pista de Patinação (Iceland)`
--    de dentro dele, e a regra nova de multiloja não alcança o passado.
--
-- 2. NENHUMA COLUNA GUARDA O DESTINO. Quando 6 vínculos ficaram apontando para
--    POIs fundidos, não havia como saber para onde cada um deveria ir — a
--    cadeia só existia na memória do processo que a decidiu. Foi preciso
--    rastreá-la pelo nome do vínculo, e 11,7% dos casos ficariam ambíguos.
--
-- O DESENHO
--
--   `fundido_em`    quando foi absorvido. NULO = é um ponto. Passa a ser este
--                   o teste de "ativo", no lugar de `status <> 'fundido'`.
--   `fundido_para`  em qual POI. Gravado no momento da fusão, quando a cadeia
--                   ainda existe.
--
-- Desfazer volta a ser possível e não perde nada: `fundido_em = null`.
--
-- A RESTAURAÇÃO DO QUE JÁ FOI PERDIDO
--
-- O `status` original dos 15.399 não está gravado em lugar nenhum, mas é
-- DEDUZÍVEL: POIs da mesma origem (`fonte`, `fonte_dado`, `sessao`) receberam o
-- mesmo `status`. Medido antes de escrever esta migração:
--
--     cohorte determina o status ....  15.260  (99,1%)
--     cohorte ambígua ...............     138  ( 0,9%)
--     sem cohorte ativa .............       1
--
-- Só os grupos de status ÚNICO restauram. Os 139 restantes ficam com
-- `status = 'fundido'` — e é honesto que fiquem: o dado deles se perdeu, e
-- inventar um valor plausível seria pior que a lacuna visível.
--
-- `fundido_para` NÃO é preenchido aqui de propósito. Rastreá-lo exige o
-- normalizador de nomes que vive no Python (`evidencia.norm_nome`), e em 11,7%
-- dos casos daria mais de um destino. A coluna nasce vazia para o passado e
-- correta para tudo que vier — que é o que importa para não repetir o
-- problema.

begin;

alter table pois add column if not exists fundido_em timestamptz;
alter table pois add column if not exists fundido_para bigint;

-- SET NULL e não CASCADE: se o sobrevivente for apagado um dia, o absorvido
-- não deve sumir junto — ele volta a ser um ponto sem destino, que é a
-- verdade. CASCADE apagaria dado que ninguém mandou apagar.
do $$
begin
  alter table pois add constraint pois_fundido_para_fkey
    foreign key (fundido_para) references pois(id) on delete set null;
exception when duplicate_object then null;
end $$;

-- `tenant_id` PRIMEIRO, como manda a regra de escala do projeto: a RLS avalia
-- por linha, e todo índice tem de começar pelo tenant para servir ao filtro.
-- Parcial em `fundido_em is null` porque é essa a consulta quente — o mapa
-- pede os POIs ATIVOS, e os fundidos são 33% da tabela em Canoas.
create index if not exists pois_ativos_por_tenant
    on pois (tenant_id, id) where fundido_em is null;

-- 1. A MARCA, ANTES DE TUDO -- e a ordem aqui e' obrigatoria em dois sentidos.
--
--    Se o `status` fosse restaurado antes, nao haveria mais como saber quem
--    estava fundido. E se o indice abaixo fosse recriado antes, `fundido_em`
--    ainda seria nulo para TODOS: o indice parcial pegaria a tabela inteira,
--    duplicatas incluidas, e falharia igual.
update pois set fundido_em = now()
 where status = 'fundido' and fundido_em is null;

-- O ÍNDICE DE DUPLICATA TAMBÉM TESTAVA O `status`, e foi ele que recusou a
-- primeira tentativa desta migração:
--
--     duplicate key ... (IGREJA NOSSA SENHORA DO ROSÁRIO, RUA DUQUE DE CAXIAS)
--
-- `pois_sem_duplicata` é parcial em `status <> 'fundido'`. Restaurar o status
-- de um POI absorvido o fazia REENTRAR no índice e colidir com o sobrevivente
-- — que é justamente o par que a fusão uniu. A condição tem de acompanhar a
-- mudança, senão a regra de duplicata passa a olhar um campo que deixou de
-- significar fusão.
drop index if exists pois_sem_duplicata;
create unique index pois_sem_duplicata
    on pois (upper(trim(nome)), upper(trim(endereco)))
 where fundido_em is null;

-- 2. O `status` de volta, só onde a cohorte não deixa dúvida.
with cohorte as (
    select fonte, fonte_dado, coalesce(sessao, '') as ss, min(status) as unico
      from pois
     where coalesce(status, '') <> 'fundido' and status is not null
     group by 1, 2, 3
    having count(distinct status) = 1
)
update pois p set status = c.unico
  from cohorte c
 where p.status = 'fundido'
   and p.fundido_em is not null
   and c.fonte is not distinct from p.fonte
   and c.fonte_dado is not distinct from p.fonte_dado
   and c.ss = coalesce(p.sessao, '');

commit;
