-- 0028 — a precisão da coordenada, declarada em TODO POI
--
-- O PROBLEMA
--
-- Os 27 mil POIs da base têm coordenadas de origens muito diferentes, e nada
-- dizia qual é qual. Medido em 24/08/2026:
--
--   · 9.691 do `pipeline` e 560 `descobertos` têm o pin do próprio Google Maps
--     para aquele estabelecimento — precisão de porta;
--   · 12.535 vieram da PLANILHA do cliente e nunca passaram pelo Maps: a
--     coordenada é a que o cliente mandou, e ninguém sabe como ele a produziu;
--   · 300 da extração estadual têm `maps_lat` preenchido — mas com o centróide
--     do Overture/OSM, não com o pin do Google;
--   · 177 do `ia_fachada` carregam a coordenada do POI VIZINHO de onde foram
--     descobertos: a IA leu uma fachada e achou outro negócio ali.
--
-- Todos apareciam no mapa como pontos iguais. Quem vai a campo tratava um
-- centróide de quadra com a mesma confiança de um pin de porta, e a diferença
-- entre os dois é uma visita perdida.
--
-- O VOCABULÁRIO
--
-- Fechado, ordenado do melhor para o pior, com a incerteza DECLARADA em metros.
-- O número não é medição de erro real — é o raio dentro do qual aquela classe
-- de coordenada costuma cair, e serve para ordenar e filtrar. Chamar de medição
-- o que é declaração foi o erro que a skill `tratamento-cnpj` levou uma
-- auditoria para corrigir.
--
--   porta        o ponto é do próprio estabelecimento              ~15 m
--   porta_aprox  o prédio ou a face certos, o número interpolado   ~40 m
--   via          a rua certa, o número não localizado             ~150 m
--   bairro       o bairro ou a localidade                         ~800 m
--   municipio    só o município                                  ~5000 m
--   desconhecida ninguém declarou — NÃO é o mesmo que ruim            null
--
-- `desconhecida` e `municipio` são coisas diferentes, e confundi-las seria o
-- pior resultado possível: a primeira é um ponto que pode ser ótimo e ninguém
-- conferiu; a segunda é um ponto que se sabe ruim. A primeira vai para a fila
-- de busca de endereço; a segunda, para a de revisão.

begin;

alter table comercialradar.pois
  add column if not exists coord_precisao   text,
  add column if not exists coord_fonte      text,
  add column if not exists coord_incerteza_m integer;

comment on column comercialradar.pois.coord_precisao is
  'Quão perto da porta o ponto está: porta | porta_aprox | via | bairro | '
  'municipio | desconhecida. Vocabulário fechado. `desconhecida` significa que '
  'ninguém declarou, e NÃO que a coordenada é ruim.';

comment on column comercialradar.pois.coord_fonte is
  'Quem deu a coordenada: maps_painel, maps_tile, cnefe, cnpj_tratado, '
  'cadastro_cliente, photon, nominatim, overture_osm, vizinho, planilha, chat.';

comment on column comercialradar.pois.coord_incerteza_m is
  'Raio DECLARADO da classe, em metros — não é erro medido. Serve para ordenar '
  'e filtrar; quem precisar de erro real tem de medir em campo.';

-- ── Retroativo, e SÓ o que se pode provar ────────────────────────────────
--
-- Cada regra abaixo sai do caminho de ingestão daquele `fonte`, conferido no
-- código. O que não se pode provar fica `desconhecida` — inventar precisão
-- seria pior que não ter a coluna, porque um rótulo errado é acreditado.

-- 1. Pin do painel do Google, para aquele estabelecimento.
update comercialradar.pois
   set coord_precisao = 'porta', coord_fonte = 'maps_painel',
       coord_incerteza_m = 15
 where coord_precisao is null
   and maps_lat is not null
   and place_id is not null
   and fonte in ('pipeline', 'descoberto', 'planilha', 'captura');

-- 1b. Captura + OCR sem `place_id`. O pin foi lido do TILE do Maps em vez de
--     vir da API — é a mesma posição que o Google desenha, só que capturada da
--     imagem. Sem esta regra, 100 POIs do processo PRINCIPAL do projeto
--     ficavam marcados como precisão desconhecida.
update comercialradar.pois
   set coord_precisao = 'porta', coord_fonte = 'maps_tile',
       coord_incerteza_m = 15
 where coord_precisao is null
   and maps_lat is not null
   and place_id is null
   and fonte in ('pipeline', 'captura', 'descoberto');

-- 2. Extração estadual: `maps_lat` vem do centróide do Overture/OSM, e não do
--    Google. Parece pin e não é — foi a descoberta que motivou esta migração.
update comercialradar.pois
   set coord_precisao = 'porta_aprox', coord_fonte = 'overture_osm',
       coord_incerteza_m = 40
 where coord_precisao is null and fonte = 'estadual';

-- 3. `ia_fachada`: a coordenada é a do POI de onde o ponto foi descoberto. O
--    prédio é o certo — a IA leu aquela fachada —, o número não.
update comercialradar.pois
   set coord_precisao = 'porta_aprox', coord_fonte = 'vizinho',
       coord_incerteza_m = 40
 where coord_precisao is null and fonte = 'ia_fachada';

-- 4. Cadastur: a origem já vinha declarada em `fonte_dado`.
update comercialradar.pois
   set coord_precisao = case
         when fonte_dado like '%cnefe:porta_face%' then 'porta_aprox'
         else 'porta' end,
       coord_fonte = split_part(fonte_dado, '+', 2),
       coord_incerteza_m = case
         when fonte_dado like '%cnefe:porta_face%' then 40 else 15 end
 where coord_precisao is null and fonte = 'cadastur'
   and fonte_dado is not null;

-- 5. O RESTO: a planilha do cliente, sem passagem pelo Maps. A coordenada pode
--    ser excelente — muitas vêm do cadastro do próprio cliente — mas ninguém
--    conferiu, e dizer "porta" sem prova seria mentir com número.
update comercialradar.pois
   set coord_precisao = 'desconhecida',
       coord_fonte = case when fonte = 'planilha' then 'planilha' else fonte end
 where coord_precisao is null;

-- O filtro do mapa e os cards leem por aqui. `tenant_id` primeiro: a RLS é
-- avaliada por linha.
create index if not exists ix_pois_coord_precisao
  on comercialradar.pois (tenant_id, coord_precisao);
-- E a fila de quem precisa ser procurado por endereço.
create index if not exists ix_pois_coord_a_buscar
  on comercialradar.pois (tenant_id, cidade)
  where coord_precisao in ('desconhecida', 'via', 'bairro', 'municipio');

commit;
