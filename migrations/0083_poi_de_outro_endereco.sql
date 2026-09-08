-- 0083 · o POI que caiu no hidrometro errado sai do dossie, e nao da base.
--
-- O CASO QUE PEDIU ISTO. A ligacao 347087 tem seis POIs. Quatro sao a
-- Madeireira Maravilha, no numero 292, vistos por quatro fontes diferentes —
-- e essa convergencia e o que da confianca ao achado. Os outros dois sao a
-- "Associação Beneficente e Educadora Vo Maria" e a "EMEI VO MARIA", no numero
-- 261, a 18 e 21 m: escola, nao loja, e endereco diferente.
--
-- O vinculo nasceu por PROXIMIDADE, que e um criterio legitimo — sem ele o
-- ponto sem numero nunca acharia hidrometro. Mas proximidade erra, e ate hoje
-- o erro entrava no julgamento como se fosse testemunha.
--
-- NAO SE APAGA O VINCULO, pelo mesmo motivo que nao se apaga o POI fundido: a
-- decisao e da IA, a IA erra, e uma marca reversivel custa uma coluna enquanto
-- um DELETE custa o dado. Ver a medicao que ja consta do `cruzar_fontes`:
-- 66,2 por cento de erro nas fusoes automaticas por evidencia fraca. Aqui a
-- evidencia e melhor — o modelo ve o endereco, o numero, a distancia e o ramo
-- de todos ao mesmo tempo — mas a licao vale igual.
--
-- O vinculo marcado sai do DOSSIE e continua na tela, com o motivo escrito.
alter table radar_comercial.ligacao_poi
    add column if not exists descartado_em timestamptz,
    add column if not exists descartado_motivo text,
    add column if not exists descartado_por text;

-- O INDICE E PARCIAL porque a pergunta e sempre "quais foram descartados",
-- nunca "quais nao foram". Guardar centenas de milhares de nulos para
-- responder a pergunta que ninguem faz e pagar espaco por nada.
create index if not exists ligacao_poi_descartado_idx
    on radar_comercial.ligacao_poi (id_empresa, ligacao)
 where descartado_em is not null;

comment on column radar_comercial.ligacao_poi.descartado_motivo is
    'Por que este POI nao entra no dossie da ligacao: a IA leu o dossie '
    'inteiro e concluiu que ele e outro estabelecimento, noutro endereco. '
    'Reversivel — o vinculo continua na linha, so nao alimenta o julgamento.';
