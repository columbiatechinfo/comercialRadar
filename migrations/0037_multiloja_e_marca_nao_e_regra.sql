-- 0037 — a multiloja vira MARCA no ponto, e deixa de decidir a fusão.
--
-- O QUE ACONTECEU ANTES DESTA MIGRAÇÃO
--
-- Em 27/08/2026 a fusão passou a DESCARTAR o par quando os dois POIs estavam
-- no mesmo lugar, o lugar reunia mais de dois nomes distintos e os nomes deles
-- diferiam. A observação por trás disso é verdadeira e continua valendo: no
-- 4545 da Avenida Farroupilha há 181 estabelecimentos, e endereço, domínio e
-- coordenada são idênticos para os 181 — nenhum identifica ninguém.
--
-- O CORTE ERRAVA 32% DAS VEZES. Medido sobre as recusas reais em Canoas:
--
--     Master Sonho Colchões       + Master Sonho Colchões | Canoas    0 m
--     Preciosa Boutique Atacado   + Preciosa Boutique Atacado         0 m
--     Crazy Som - Locação         + Crazy Som                         7 m
--
-- São o mesmo negócio. `semelhanca_nome` é Jaccard sobre tokens, e um nome que
-- é o outro MAIS UM SUFIXO cai para 0,75 — abaixo do limiar de 0,8. Um número
-- fixo não distingue "sufixo de filial" de "outra loja".
--
-- A IA DISTINGUE, e foi verificada nos mesmos pares. De 120 perguntados:
--
--     Unimed Porto Alegre + Coloprocto ............ DIFERENTE   certo
--     Agah + Agência Treehauss .................... DIFERENTE   certo
--     NGA Móveis Hospitalares + NGA Metalúrgica ... DIFERENTE   certo
--     Master Sonho Colchões + ... | Canoas ........ MESMO       certo
--     Crazy Som - Locação + Crazy Som ............. MESMO       certo
--
-- Decisão do dono do produto, 28/08/2026: é melhor que mais pares CHEGUEM à IA
-- e ela resolva — "mesmo o shopping tendo vários no mesmo endereço, cada um
-- viraria um ponto individual porque seus nomes mostram que claramente são
-- pontos diferentes". O nome é a evidência, e ler nome é o que a IA faz melhor
-- que um limiar.
--
-- O QUE ESTA MIGRAÇÃO FAZ
--
-- A marca não some — ela deixa de decidir e passa a ser dado. Saber que um
-- ponto está num prédio de várias lojas vale na tela, na revisão humana e em
-- qualquer regra futura; o que não vale é ela recusar a fusão sozinha.
--
--   `multiloja`  o lugar deste ponto reúne mais de 2 nomes distintos.
--
-- Ela é DERIVADA: o cruzamento a recalcula a cada passada, porque o número de
-- estabelecimentos numa porta muda quando POIs entram, saem ou se fundem.
-- Guardá-la evita que o painel tenha de refazer a contagem para exibi-la.

begin;

alter table pois add column if not exists multiloja boolean not null default false;

-- `tenant_id` primeiro, como toda consulta filtrada por empresa exige, e
-- parcial: os marcados são a minoria (21% em Canoas) e são eles que se busca.
create index if not exists pois_multiloja_por_tenant
    on pois (tenant_id, id) where multiloja;

commit;
