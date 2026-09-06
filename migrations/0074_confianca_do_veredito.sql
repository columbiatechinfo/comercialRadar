-- O VEREDITO PASSA A TER UM NUMERO, E NAO SO UM ROTULO.
--
-- Ate aqui `poi_veredito` guardava `aprovado` ou `reprovado` e nada entre os
-- dois. Isso bastava enquanto o julgamento era so a fachada: ou o letreiro
-- casava ou nao casava. Deixou de bastar quando a decisao passou a vir da
-- FICHA DA FONTE (06/09/2026), porque a ficha tem gradacao que o rotulo perde:
--
--   uma loja aberta no iFood agora nao vale o mesmo que uma loja que esta la,
--   com CNPJ, mas fora do ar;
--
--   uma hospedagem com hospede no mes passado nao vale o mesmo que uma com o
--   ultimo comentario ha onze meses — e as duas sao "anunciadas".
--
-- Sem o numero, as duas viravam `aprovado` e o operador recebia a lista sem
-- saber por onde comecar. `confianca` e o que ordena essa lista.
--
-- A ESCALA E A MESMA DE `ligacao_poi.confianca`: 0 a 1, onde 0,70 e a faixa em
-- que o produto ja trata como "vale a visita". Usar duas reguas diferentes na
-- mesma tela seria pedir para alguem comparar numeros que nao se comparam.

alter table radar_comercial.poi_veredito
  add column if not exists confianca numeric;

comment on column radar_comercial.poi_veredito.confianca is
  'Quanto o veredito se sustenta, de 0 a 1 — mesma regua de '
  'ligacao_poi.confianca. Para iFood varia com o status atual da loja '
  '(bruto.disponivel); para Airbnb decai um passo por mes desde o ultimo '
  'comentario, zerando esse passo depois de 12 meses.';

create index if not exists ix_poi_veredito_confianca
    on radar_comercial.poi_veredito (confianca desc nulls last);
