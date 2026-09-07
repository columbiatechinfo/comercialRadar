-- ESTADUAL E IBGE ENTRAM NA AVALIACAO DA IA.
--
-- `categoria_catalogo.avaliar` decide o que chega ao julgamento por imagem.
-- Estava FALSO em todas as 1.111 categorias da base estadual e nas 5 do IBGE —
-- as duas fontes inteiras fora, o que nao parece decisao e sim catalogo nunca
-- curado para elas.
--
-- O QUE ESTAVA BLOQUEADO, medido em 07/09/2026 sobre os POIs de Canoas que
-- cruzaram com ligacao RESIDENCIAL ativa:
--
--     ibge      outras finalidades      9.753   <- comercio, servico, industria
--     ibge      religioso                 461
--     estadual  Salao de Beleza           393
--     estadual  Cabeleireiro              249
--     estadual  Oficina Mecanica          233
--     estadual  Barbearia                 176
--     estadual  Restaurante               152
--     estadual  Padaria                   142
--     estadual  Bar                       132
--
-- E exatamente o comercio que o produto procura. E `outras finalidades`, a
-- maior de todas, e a especie do CNEFE para estabelecimento de comercio,
-- servico ou industria — o negocio pequeno que nao aparece em fonte digital e
-- por isso nunca foi cobrado como comercio.
--
-- MARCA TUDO, E A IA FILTRA. Decisao do dono do produto (07/09/2026). Ha
-- categorias que nunca serao comercio — "Apartamentos Or Condo", "Marco/
-- Edificio Historico", "Road", "Nao Informado" — e marca-las gasta chamada de
-- modelo a toa. Mas curar 1.116 linhas a mao antes de saber o que rende e
-- gastar o tempo de uma pessoa para poupar o de uma GPU ociosa: o julgamento
-- ja reprova o que nao e comercio, e o custo dele e conhecido (1,6 s por POI).
--
-- Se a medicao mostrar desperdicio, a curadoria vem depois, com os numeros na
-- mesa em vez de com palpite.

update radar_comercial.categoria_catalogo
   set avaliar = true
 where fonte in ('estadual', 'ibge')
   and not avaliar;
