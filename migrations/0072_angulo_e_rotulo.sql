-- `streetview_imgs.angulo` E UM ROTULO, E NAO UM NUMERO.
--
-- A coluna era `double precision` e TODO o codigo sempre escreveu texto nela:
-- 'facade', 'g90', 'g180', 'g270', 'p1', 'p2' — o angulo distingue a foto de
-- frente dos giros e dos panoramas deslocados. `imagens.gravar_streetview`
-- declara `angulo: str = "facade"`; `descrever_imagens` procura por
-- `angulo='facade'`; `streetview_capture` idem.
--
-- CONSEQUENCIA MEDIDA em 06/09/2026: `streetview_imgs` tem ZERO linhas, e a
-- captura de fachada morria antes do primeiro POI com
--
--     invalid input syntax for type double precision: "facade"
--
-- Nao era "a etapa nunca rodou": era "a etapa nao conseguia rodar". E como o
-- veredito da IA olha fachada, isso deixou 26.209 POIs de Canoas sem a imagem
-- de que a IA depende — o gargalo que parecia ser da IA e era da tabela.
--
-- O ANGULO NUMERICO JA TEM COLUNA: `heading` (double precision) guarda o
-- rumo da camera em graus. Por isso `angulo` como texto nao perde nada — ele
-- sempre foi o NOME da tomada, nao a medida dela.
--
-- Trocar o tipo e de graca: a tabela esta vazia. Feito agora, antes da
-- primeira captura, em vez de depois com dado dentro.

alter table radar_comercial.streetview_imgs
  alter column angulo type text using angulo::text;

comment on column radar_comercial.streetview_imgs.angulo is
  'NOME da tomada: facade, g90, g180, g270, p1, p2. O rumo em graus fica em '
  '`heading`. Era double precision ate 06/09/2026, o que impedia qualquer '
  'gravacao — a tabela tinha zero linhas por isso.';
