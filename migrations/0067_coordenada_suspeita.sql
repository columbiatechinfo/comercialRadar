-- 0067 · a coordenada que não é do endereço passa a ser dita
--
-- O QUE ISTO REGISTRA. `fontes_para_poi.Cnefe` indexa o CNEFE **só por CEP** e,
-- dentro do CEP, escolhe o número mais próximo. A rua nunca entra na conta. Num
-- CEP genérico — `92330-000` cobre o bairro Mathias Velho inteiro — procurar
-- "número 43" acha o 43 de QUALQUER rua daquele CEP. O resultado ainda é
-- carimbado `coord_fonte = 'cnefe_numero_exato'`, que se lê como "casei o
-- número exato".
--
-- Medido em 04/09/2026 sobre os 143 POIs da quadra de Canoas:
--
--     76   a coordenada é do (rua, número) do endereço
--     45   o número NÃO EXISTE naquela rua, e a coordenada veio do CNEFE
--     10   está a mais de 80 m do número certo — várias a 5 km
--      6   o endereço não traz rua ou número para conferir
--      6   número inexistente, coordenada de outra fonte
--
-- Casos flagrantes: "AVENIDA RIO GRANDE DO SUL, 43" recebeu a coordenada do
-- "BECO DEODORO DA FONSECA, 43"; "RIO GRANDE DO SUL, 177" caiu na "AYRTON
-- SENNA", a 5,1 km. Os números altos da avenida (5395, 5465) estão certos — é
-- a faixa baixa, que não existe naquela via, que se espalha pelo bairro.
--
-- POR QUE UMA COLUNA, E NÃO APAGAR O PONTO. O POI existe: a Receita registrou
-- aquele CNPJ naquele endereço. O que não se sabe é ONDE ele fica. Apagar a
-- coordenada perderia a pista; deixá-la sem aviso faz o Street View fotografar
-- a casa de um terceiro e a IA julgar quem não tem nada com isso. A coluna
-- separa "não sei onde" de "sei, e é aqui".
--
-- QUEM ESCREVE: `scripts/qualidade/auditar_coord.py`. Ele não conserta nada —
-- conserto é mudar o geocodificador, e isso muda quais POIs caem dentro da área
-- desenhada, o que é decisão de quem opera, não de uma migração.

set search_path to radar_comercial, extensions, public;

alter table pois
    add column if not exists coord_conferida_em timestamptz,
    add column if not exists coord_suspeita     boolean,
    add column if not exists coord_motivo       text;

comment on column pois.coord_suspeita is
    'TRUE quando a coordenada nao pode ser confirmada como sendo do (rua, '
    'numero) do endereco. NULL = ainda nao conferida. Ver a migracao 0067.';
comment on column pois.coord_motivo is
    'Por que a coordenada e suspeita, em uma linha — inclui a rua em que o '
    'ponto REALMENTE cai, que e o flagrante.';

-- Índice parcial: quem consulta isto quer a lista dos suspeitos, que é a
-- minoria. Indexar a coluna inteira gastaria espaço com os bons.
create index if not exists pois_coord_suspeita
    on pois (id_empresa, coord_suspeita) where coord_suspeita;

select 'colunas de auditoria de coordenada criadas' as feito;
