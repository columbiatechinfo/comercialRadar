-- 0039 — índice de coordenada para o recorte por área desenhada
--
-- POR QUE ELE EXISTE
--
-- O `/api/stats` passou a aceitar a área desenhada à mão, e não só o município.
-- São 8 consultas por carregamento do cartão, cada uma filtrando pela CAIXA
-- ENVOLVENTE da área. Sem índice, cada uma é um seq scan da tabela inteira — e
-- a tabela cresce por cliente e por cidade minerada.
--
-- POR QUE NÃO É UM ÍNDICE ESPACIAL
--
-- Seria o natural, e não dá: o PostGIS deste banco vive no schema `extensions`,
-- e o papel `comercialradar_worker` não tem USAGE nele. Nem o tipo `geometry`
-- resolve pela conexão do produto — `ST_Contains` só funciona contra o banco de
-- REFERÊNCIA (5443), onde a `ibge_malha` mora. Liberar o schema exigiria
-- superusuário no Postgres do produto, e a decisão do dono do produto em
-- 28/08/2026 foi não depender disso.
--
-- O desenho que ficou: o SQL corta pelo RETÂNGULO (aqui, com índice) e o teste
-- exato do polígono roda em Python sobre esse subconjunto, com o mesmo
-- algoritmo de raio que a ficha do polígono já usa no navegador. Para uma área
-- de bairro o retângulo já elimina quase tudo; o custo que sobra é proporcional
-- ao que o operador desenhou, não ao tamanho da base.
--
-- A COORDENADA EFETIVA É UMA EXPRESSÃO, não uma coluna: `maps_lat` é a do Maps,
-- `lat_origem` é a de quem descobriu o ponto, e o sistema usa
-- `COALESCE(maps_lat, lat_origem)` em todo lugar. O índice indexa exatamente
-- essa expressão, senão o planejador não o usa.
--
-- TENANT_ID PRIMEIRO, como em todo índice deste banco: a RLS filtra por empresa
-- antes de qualquer outra coisa.
--
-- Só POI VIVO entra: ponto fundido não aparece no mapa, e mantê-lo no índice só
-- engorda a estrutura que se lê a cada carregamento.
--
-- MEDIDO ANTES DELE: 20 ms para contar 22.719 POIs dentro de um retângulo de
-- bairro, em 36.620 ativos, por seq scan.

CREATE INDEX IF NOT EXISTS pois_coord_por_tenant
    ON pois (
        tenant_id,
        (COALESCE(maps_lat, lat_origem)),
        (COALESCE(maps_lng, lng_origem))
    )
    WHERE fundido_em IS NULL
      AND COALESCE(maps_lat, lat_origem) IS NOT NULL
      AND COALESCE(maps_lng, lng_origem) IS NOT NULL;
