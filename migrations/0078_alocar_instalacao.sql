-- 0078 · ALOCAR INSTALACAO: o veredito existe, a ligacao nao.
--
-- O sistema so julgava POI que ja tivesse uma ligacao RESIDENCIAL ATIVA
-- vinculada — e a regra faz sentido para quem vem de foto: sem saber qual
-- hidrometro serve aquele imovel, o veredito nao vira cobranca.
--
-- Mas ela deixava 873 pontos de iFood e Airbnb parados no vazio. Neles quem
-- prova o comercio e a FICHA DA PLATAFORMA — a loja esta no ar, o anuncio
-- recebeu hospede no mes passado —, e nao a fachada. Sao os casos de MAIOR
-- certeza que o sistema tem, e eram exatamente os que ninguem olhava, porque
-- o cruzamento automatico nao achou ligacao para eles.
--
-- Esta coluna troca "descartar" por "encaminhar": o veredito da IA e gravado
-- normalmente, e a bandeira diz que falta o unico dado que a maquina nao
-- conseguiu descobrir. Um humano abre a lista, olha o endereco e escolhe a
-- ligacao. O trabalho da maquina nao se perde por causa do que ela nao sabia.
--
-- E DERIVADA, E NAO DIGITADA. `avaliar_ia.gravar` a calcula no proprio
-- INSERT, com um `not exists` sobre `ligacao_poi`: assim ela nunca discorda
-- da realidade por esquecimento de quem chamou a funcao, e se o vinculo
-- aparecer depois basta rejulgar para ela cair sozinha.
alter table radar_comercial.poi_veredito
    add column if not exists alocar_instalacao boolean not null default false;

comment on column radar_comercial.poi_veredito.alocar_instalacao is
    'Ha veredito mas nao ha ligacao vinculada: um humano precisa escolher qual '
    'instalacao recebe este ponto. Derivada de ligacao_poi no momento do INSERT.';

-- O INDICE E PARCIAL porque a pergunta e sempre "quais faltam alocar", nunca
-- "quais nao faltam". Indexar os dois lados guardaria centenas de milhares de
-- `false` para responder a uma pergunta que ninguem faz.
create index if not exists poi_veredito_alocar_idx
    on radar_comercial.poi_veredito (id_empresa, poi_id)
 where alocar_instalacao;
