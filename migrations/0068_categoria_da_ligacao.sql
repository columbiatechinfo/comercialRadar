-- A CATEGORIA DA LIGACAO PASSA A MORAR NO VINCULO.
--
-- O mapa precisa saber, por ponto, se a ligacao que ele achou e RESIDENCIAL:
-- e a leitura que separa "ja esta cobrado" de "comercio pagando tarifa de
-- casa", que e o produto. O dado existe em `resources_root.cadastro_corsan`,
-- 2,5 milhoes de linhas, num schema de OUTRO sistema.
--
-- POR QUE COPIAR EM VEZ DE JUNTAR NA HORA. Medido em 04/09/2026, com EXPLAIN:
-- a juncao a cada requisicao varria a tabela inteira — 2.516.709 linhas,
-- 10 milhoes de buffers, 15,0 s POR AREA, e 162 s para desenhar um mapa de 54
-- pontos. O motivo esta no plano: a politica RLS daquela tabela chama
-- `core.empresa_atual()` SEM subselect, entao o Postgres a avalia por linha e
-- nao consegue usar a chave primaria `(id_empresa, num_ligacao)`.
--
-- Consertar a politica seria o certo — e ela nao e nossa. `resources_root` e do
-- sistema de recursos, e mexer na RLS dele por causa de uma tela nossa e
-- exatamente o tipo de acoplamento que a separacao de schemas existe para
-- evitar. Copiar a categoria no momento do vinculo resolve do nosso lado, e de
-- quebra congela o que valia QUANDO o vinculo foi feito.
--
-- O vinculo ja e reescrito a cada rodada (`on conflict ... do update`), entao a
-- copia nao envelhece sozinha: ela se atualiza junto com o resto.

alter table radar_comercial.ligacao_poi
  add column if not exists categoria_ligacao text;

comment on column radar_comercial.ligacao_poi.categoria_ligacao is
  'Categoria da ligacao na base do cliente (RESIDENCIAL, COMERCIAL, '
  'INDUSTRIAL, PUBLICA) no momento do vinculo. Copiada de '
  'resources_root.cadastro_corsan para o mapa nao precisar cruzar 2,5 milhoes '
  'de linhas por requisicao — ver o cabecalho desta migracao.';

-- BACKFILL DO QUE JA EXISTE. Uma varredura so, aqui, em vez de uma por
-- requisicao. As 65.772 linhas atuais ganham a categoria; o pipeline preenche
-- as proximas na origem.
--
-- NAO SE JUNTA POR `id_base`, embora as duas tabelas tenham a coluna:
-- `ligacao_poi` carimba 1 (o id de `base_cliente`, que so tem essa linha) e
-- `cadastro_corsan` carimba 7, resto de uma importacao anterior. Juntar pelos
-- dois campos devolve zero — em silencio.
update radar_comercial.ligacao_poi lp
   set categoria_ligacao = cc.categoria
  from resources_root.cadastro_corsan cc
 where cc.id_empresa = lp.id_empresa
   and cc.num_ligacao = lp.ligacao::bigint
   and lp.categoria_ligacao is distinct from cc.categoria;

-- O indice serve a pergunta que a tela faz: "quais pontos desta area estao
-- numa ligacao residencial". Parcial porque so essa categoria interessa ao
-- recorte — as outras tres somam 63.762 das 65.772 linhas e um indice sobre
-- elas nao seria usado.
create index if not exists ligacao_poi_residencial
    on radar_comercial.ligacao_poi (id_empresa, poi_id)
 where categoria_ligacao = 'RESIDENCIAL';
