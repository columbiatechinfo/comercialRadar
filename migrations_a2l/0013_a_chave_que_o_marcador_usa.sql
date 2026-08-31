-- 0013 — a chave que o marcador de carga usa, e que nunca foi criada
--
-- A importação de Canoas processou os 28.787 POIs, filtrou, chegou a "prontas
-- para inserir: 27.649" e morreu na última linha:
--
--     psycopg2.errors.UndefinedObject: constraint "fonte_arquivos_pkey"
--     for table "fonte_arquivos" does not exist
--
-- Zero POI gravado, porque tudo estava na mesma transação.
--
-- `base_comum.marcar()` grava o marcador com `ON CONFLICT ON CONSTRAINT
-- fonte_arquivos_pkey`, e o comentário dela explica a escolha: a mesma função
-- serve os dois schemas, cuja chave tem colunas DIFERENTES — `(fonte,
-- referencia)` em `resources_root`, `(id_empresa, fonte, referencia)` em
-- `radar_comercial`, porque aqui o marcador é por empresa. Nomear a constraint
-- em vez das colunas é o que permite uma função só.
--
-- Só que a constraint existe em um lado apenas:
--
--     resources_root.fonte_arquivos   fonte_arquivos_pkey  PRIMARY KEY (fonte, referencia)
--     radar_comercial.fonte_arquivos  nenhuma — só a FK de empresa e um índice não-único
--
-- É A QUARTA VEZ QUE UMA MIGRAÇÃO MINHA DISCORDA DO CÓDIGO QUE A USA.
-- Antes foram: `ibge_cnefe` com 2 colunas em vez das 34 do CSV; as `rf_*` em
-- ordem alfabética contra a ordem da Receita; e a `cadastro_corsan` sem o
-- privilégio que a política exige. O padrão é sempre o mesmo — a migração
-- descreve a tabela que eu imaginei, e o código usa a tabela que ele precisa.
--
-- O que muda a partir daqui: antes de dar uma migração por pronta, procurar no
-- código quem escreve naquela tabela e conferir o que ele exige — nome de
-- constraint, ordem de coluna, privilégio. O teste que pegaria isto não é
-- validar o DDL sozinho; é rodar quem o consome.

begin;

-- `id_empresa` entra na chave, então não pode ser nula. A trigger
-- `preencher_empresa()` já a preenche em toda inserção; isto só torna a
-- garantia explícita para o índice.
alter table radar_comercial.fonte_arquivos
  alter column id_empresa set not null;

alter table radar_comercial.fonte_arquivos
  add constraint fonte_arquivos_pkey primary key (id_empresa, fonte, referencia);

-- ── conferência ─────────────────────────────────────────────────────────────
do $$
declare d text;
begin
  select pg_get_constraintdef(oid) into d
    from pg_constraint
   where conrelid = 'radar_comercial.fonte_arquivos'::regclass
     and conname = 'fonte_arquivos_pkey';
  if d is null then
    raise exception 'a constraint nao ficou';
  end if;
  if d not like '%id_empresa%fonte%referencia%' then
    raise exception 'a chave saiu com outras colunas: %', d;
  end if;
  raise notice 'radar_comercial.fonte_arquivos: %', d;
end $$;

commit;
