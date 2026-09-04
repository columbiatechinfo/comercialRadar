-- 0061 — o catálogo de categorias, para escolher o que vai à IA
--
-- O PROBLEMA, MEDIDO EM 04/09/2026
--
-- Os POIs trazem 2.259 categorias distintas, e elas não conversam entre si:
--
--     estadual   1.111 rótulos das taxonomias do Overture, OSM e Foursquare
--     receita      869 CÓDIGOS CNAE crus — `4781400`, `9602501`
--     maps         216 rótulos do Google, em português
--     ifood         46 · cadastur 10 · ibge 5 · airbnb 2
--
-- Uma lista crua com 2.259 opções, a maioria número, é inusável. E a escolha
-- não é cosmética: traduzindo os CNAE mais comuns pela `rf_cnaes`, quatro dos
-- sete maiores são MEI SEM PORTA DE RUA — transporte rodoviário de carga
-- (1.590), obras de alvenaria (1.364), promoção de vendas (2.253), apoio
-- administrativo (1.632). Mandar esses para uma IA olhar fachada gasta o
-- orçamento inteiro em algo que não existe para ser visto. Já "comércio
-- varejista de vestuário" e "cabeleireiros" valem a olhada.
--
-- DOIS LADOS, E ELES NÃO SE MISTURAM — decisão do dono do produto
--
-- O lado CNAE tem hierarquia de verdade: seção (21 letras) → divisão (87) →
-- classe (os 869 códigos). A `resources_root.rf_cnaes` já traz a descrição de
-- todos, e o casamento foi conferido: 869 de 869.
--
-- O lado dos RÓTULOS DE TEXTO fica como veio, agrupado por fonte. Mapeá-los
-- para a CNAE exigiria adivinhar 1.383 vezes, e adivinhação errada aqui não
-- aparece como erro — aparece como categoria que o operador achou que tinha
-- marcado e não marcou. Duas listas honestas valem mais que uma lista unificada
-- e incerta.

set search_path to radar_comercial, extensions, public;

-- ── as 21 seções da CNAE, que não estão em lugar nenhum do banco ───────────
create table if not exists cnae_secao (
    letra     char(1) primary key,
    nome      text not null,
    div_ini   smallint not null,
    div_fim   smallint not null
);

insert into cnae_secao (letra, nome, div_ini, div_fim) values
 ('A','Agricultura, pecuária, produção florestal, pesca e aquicultura',1,3),
 ('B','Indústrias extrativas',5,9),
 ('C','Indústrias de transformação',10,33),
 ('D','Eletricidade e gás',35,35),
 ('E','Água, esgoto, gestão de resíduos e descontaminação',36,39),
 ('F','Construção',41,43),
 ('G','Comércio; reparação de veículos automotores e motocicletas',45,47),
 ('H','Transporte, armazenagem e correio',49,53),
 ('I','Alojamento e alimentação',55,56),
 ('J','Informação e comunicação',58,63),
 ('K','Atividades financeiras, de seguros e serviços relacionados',64,66),
 ('L','Atividades imobiliárias',68,68),
 ('M','Atividades profissionais, científicas e técnicas',69,75),
 ('N','Atividades administrativas e serviços complementares',77,82),
 ('O','Administração pública, defesa e seguridade social',84,84),
 ('P','Educação',85,85),
 ('Q','Saúde humana e serviços sociais',86,88),
 ('R','Artes, cultura, esporte e recreação',90,93),
 ('S','Outras atividades de serviços',94,96),
 ('T','Serviços domésticos',97,97),
 ('U','Organismos internacionais e outras instituições extraterritoriais',99,99)
on conflict (letra) do update set nome = excluded.nome,
    div_ini = excluded.div_ini, div_fim = excluded.div_fim;

comment on table cnae_secao is
    'As 21 seções da CNAE 2.0 e a faixa de divisões de cada uma. Não vem da '
    'Receita: a `rf_cnaes` traz só código e descrição de classe, sem a '
    'hierarquia. É tabela de referência, igual para todo cliente.';

-- ── o catálogo: uma linha por (fonte, valor bruto) ─────────────────────────
create table if not exists categoria_catalogo (
    id           bigserial primary key,
    id_empresa   uuid not null,
    fonte        text not null,
    valor        text not null,
    -- Só o lado CNAE tem estes três. Rótulo de texto fica com eles nulos, e é
    -- isso que separa as duas listas na tela sem precisar de outra coluna.
    cnae_classe  text,
    cnae_divisao smallint,
    cnae_secao   char(1),
    rotulo       text not null,
    pois         integer not null default 0,
    avaliar      boolean not null default false,
    visto_em     timestamptz not null default now()
);

create unique index if not exists categoria_catalogo_unico
    on categoria_catalogo (id_empresa, fonte, valor);
create index if not exists categoria_catalogo_secao
    on categoria_catalogo (id_empresa, cnae_secao, cnae_divisao);
create index if not exists categoria_catalogo_avaliar
    on categoria_catalogo (id_empresa, avaliar) where avaliar;

alter table categoria_catalogo owner to migrator;
alter table cnae_secao owner to migrator;
grant select, insert, update, delete on categoria_catalogo to app_user;
grant select on categoria_catalogo to readonly;
grant usage, select on sequence categoria_catalogo_id_seq to app_user;
grant select on cnae_secao to app_user, readonly;
alter table categoria_catalogo enable row level security;
drop policy if exists categoria_catalogo_ver on categoria_catalogo;
create policy categoria_catalogo_ver on categoria_catalogo for select
    using ((select core.eh_suporte())
           or (id_empresa = (select core.empresa_atual())));
drop policy if exists categoria_catalogo_mexer on categoria_catalogo;
create policy categoria_catalogo_mexer on categoria_catalogo for all
    using ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)))
    with check ((select core.eh_suporte())
           or ((id_empresa = (select core.empresa_atual()))
               and ((select core.nivel_atual()) >= 4)));
drop trigger if exists categoria_catalogo_empresa on categoria_catalogo;
create trigger categoria_catalogo_empresa before insert on categoria_catalogo
    for each row execute function radar_comercial.preencher_empresa();

comment on table categoria_catalogo is
    'O que o operador marca antes de mandar POIs para a IA. Uma linha por '
    '(fonte, categoria crua). `avaliar` é a escolha; `pois` é quantos POIs '
    'daquela categoria existem, para a escolha ser informada.';
comment on column categoria_catalogo.avaliar is
    'Marcado = os POIs desta categoria entram na avaliação por IA. Nasce '
    'FALSO: mandar tudo por omissão gastaria o orçamento em MEI sem fachada.';
