-- 0052 — a `pois` emagrece: 17 colunas saem, e uma view guarda o formato antigo
--
-- O QUE SAI, E POR QUE EM DOIS GRUPOS
--
-- GRUPO 1 — SEIS COLUNAS QUE NUNCA GUARDARAM NADA e que nenhum arquivo vivo
-- menciona. `coord_compartilhada`, `coord_grupo`, `descoberto_de`,
-- `endereco_gerado_por`, `fundido_para` e `fundido_por` são restos de desenhos
-- anteriores: zero linhas preenchidas nas 301.297, e as únicas citações estão
-- em scripts de uma vez só que não fazem parte do pipeline nem da API.
--
-- GRUPO 2 — ONZE COLUNAS QUE MUDARAM DE CASA na migração 0048 e vêm sendo
-- gravadas nos dois lugares desde então. A carga foi conferida linha a linha
-- antes desta remoção, e o resultado foi ZERO em todas:
--
--     coluna              na `pois`   perderia
--     maps_url                  369          0
--     plus_code                 366          0
--     avaliacao                 296          0
--     total_avaliacoes          296          0
--     detalhado_em/por          287          0
--     status_horario            279          0
--     nome_fantasia              77          0
--     razao_social               77          0
--     cnae                       74          0
--     resumo_avaliacoes          23          0
--
-- `place_id` NÃO SAI, e essa foi uma correção de rumo. Ela parece campo do
-- Maps, mas é a CHAVE DE DEDUPLICAÇÃO do ingestor inteiro — `select id from
-- pois where place_id = %s` é a primeira coisa que ele faz, para qualquer
-- fonte. Nos POIs estaduais ela vale `estadual:<cluster_id>`; no Maps, o id do
-- Google. É identidade externa do ponto, não opinião de uma fonte. Tirá-la
-- quebraria a deduplicação de tudo.
--
-- A VIEW EXISTE PARA QUEM PRECISA DO FORMATO CHAPADO
--
-- O painel mostra a ficha do POI com nota, horário e razão social lado a lado
-- com nome e telefone; obrigá-lo a juntar quatro tabelas para desenhar um
-- balão seria trocar um problema de modelagem por um de front. `pois_completo`
-- devolve exatamente as colunas de antes, vindas de onde elas moram agora.
--
-- ELA É SÓ LEITURA, de propósito. Escrever pela view exigiria gatilho `instead
-- of` decidindo em qual tabela cada campo cai — e essa decisão já existe, em
-- `realtime_ingest.TABELA_POR_FONTE`, onde dá para lê-la.

set search_path to radar_comercial, public;

-- ── as colunas caem PRIMEIRO, e a view vem depois ──────────────────────────
--
-- A ordem inversa parecia melhor — criar a view, repontar os leitores, só então
-- derrubar — e não compila: a view usa `p.*` para não ter de listar as 53
-- colunas que ficam, e enquanto `pois.avaliacao` existe o `p.*` já traz uma
-- `avaliacao`, que colide com a de `maps_data`. O Postgres recusa com "column
-- specified more than once".
--
-- Como tudo aqui é uma transação só, a ordem não abre janela: quem consultar
-- durante a migração vê o estado antigo inteiro ou o novo inteiro.

-- ── grupo 1: as que nunca guardaram nada ───────────────────────────────────
alter table pois
    drop column if exists coord_compartilhada,
    drop column if exists coord_grupo,
    drop column if exists descoberto_de,
    drop column if exists endereco_gerado_por,
    drop column if exists fundido_para,
    drop column if exists fundido_por;

-- ── grupo 2: as que mudaram de casa ────────────────────────────────────────
alter table pois
    drop column if exists maps_url,
    drop column if exists plus_code,
    drop column if exists avaliacao,
    drop column if exists total_avaliacoes,
    drop column if exists resumo_avaliacoes,
    drop column if exists status_horario,
    drop column if exists detalhado_em,
    drop column if exists detalhado_por,
    drop column if exists razao_social,
    drop column if exists nome_fantasia,
    drop column if exists cnae;

-- ── a view com o formato de antes ──────────────────────────────────────────
create or replace view pois_completo as
select p.*,
       m.maps_url,
       m.plus_code,
       m.avaliacao,
       m.total_avaliacoes,
       m.resumo_avaliacoes,
       m.status_horario,
       m.detalhado_em,
       m.detalhado_por,
       -- CADASTUR E RECEITA FALAM DA MESMA EMPRESA, e as duas podem ter a
       -- linha. O Cadastur vem primeiro porque hoje é ele quem tem o dado
       -- (77 linhas contra zero da Receita) e porque é dado conferido pelo
       -- MTur; a Receita entra quando o enriquecimento por CNPJ rodar.
       coalesce(c.razao_social, r.razao_social)   as razao_social,
       coalesce(c.nome_fantasia, r.nome_fantasia) as nome_fantasia,
       coalesce(c.cnae, r.cnae)                   as cnae
  from pois p
  left join maps_data     m on m.poi_id = p.id
  left join cadastur_data c on c.poi_id = p.id
  left join receita_data  r on r.poi_id = p.id;

alter view pois_completo owner to migrator;
grant select on pois_completo to app_user, readonly;

comment on view pois_completo is
    'A `pois` com o que as tabelas de fonte sabem, no formato chapado que o '
    'painel usa. Só leitura: quem escreve passa por realtime_ingest, que sabe '
    'em qual tabela cada campo cai.';

-- ── o placar ───────────────────────────────────────────────────────────────
do $$
declare n int;
begin
    select count(*) into n from information_schema.columns
     where table_schema = 'radar_comercial' and table_name = 'pois';
    raise notice 'A `pois` agora tem % colunas (eram 70).', n;
end $$;
