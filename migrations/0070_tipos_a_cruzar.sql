-- QUAIS CATEGORIAS DE LIGACAO O CRUZAMENTO PROCURA — decisao do operador, na base.
--
-- `tipos_comerciais` ja existe e diz o que o CLIENTE cobra como nao-residencial
-- (COMERCIAL, PUBLICA, INDUSTRIAL). O cruzamento usava essa mesma lista para
-- escolher QUAIS ligacoes cruzar — e por isso nunca cruzou as 88.767 ligacoes
-- RESIDENCIAIS de Canoas, que sao justamente onde o produto procura comercio
-- escondido. As duas perguntas sao diferentes: "o que o cliente ja considera
-- comercio" e "onde eu quero procurar comercio".
--
-- Esta coluna responde a segunda. Nasce RESIDENCIAL por decisao do dono do
-- produto (06/09/2026): "deixa esse tipo como padrao, porque ele que me
-- interessa; so quem pode mudar sou eu pelo front". O pipeline le daqui e
-- passa `--tipos` ao cruzamento; o painel edita na tela da base.
--
-- E um array porque o operador pode querer cruzar mais de uma categoria de
-- uma vez (ex.: RESIDENCIAL e PUBLICA), e vazio significa "use
-- tipos_comerciais", que e o comportamento antigo — para nenhuma base
-- existente mudar de comportamento por acidente alem desta.

alter table radar_comercial.base_cliente
  add column if not exists tipos_a_cruzar text[] not null default '{RESIDENCIAL}';

comment on column radar_comercial.base_cliente.tipos_a_cruzar is
  'Categorias de ligacao que o cruzamento (etapa 8) percorre procurando POIs. '
  'Padrao RESIDENCIAL: comercio escondido em ligacao de casa. Editado no '
  'painel, na tela da base. Vazio = usar tipos_comerciais (comportamento '
  'anterior a 06/09/2026).';
