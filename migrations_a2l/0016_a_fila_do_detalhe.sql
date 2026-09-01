-- 0016 — a fila do detalhe, para duas máquinas trabalharem na mesma quadra
--
-- Até aqui o minerador carregava os 90 POIs na memória e distribuía entre seus
-- próprios navegadores. Isso funciona numa máquina e não funciona em duas: as
-- duas carregariam a mesma lista e fariam o mesmo trabalho duas vezes.
--
-- A fila não precisa de infraestrutura nova. O Postgres já resolve isto há
-- décadas com `FOR UPDATE SKIP LOCKED`: cada worker pede o próximo POI livre,
-- o banco entrega um diferente para cada um, e quem chega depois PULA o que já
-- está reservado em vez de esperar por ele. Sem fila externa, sem broker, sem
-- coordenador.
--
-- `detalhado_em` é a reserva e a conclusão ao mesmo tempo: preenchido, o POI
-- saiu da fila. Se uma máquina morrer no meio, os POIs que ela tomou ficam
-- marcados sem terem sido gravados — por isso `--refazer` existe, e por isso a
-- gravação de cada POI acontece LOGO depois de colhê-lo, e não toda no fim.
--
-- `detalhado_por` guarda qual máquina fez, que é o que permite medir o ganho
-- da paralelização em vez de estimá-lo.

begin;

alter table radar_comercial.pois
  add column if not exists detalhado_em  timestamptz,
  add column if not exists detalhado_por text;

comment on column radar_comercial.pois.detalhado_em is
  'Quando este POI saiu da fila do detalhe. Nulo = ainda por fazer. É a '
  'reserva (FOR UPDATE SKIP LOCKED) e a conclusão no mesmo campo.';
comment on column radar_comercial.pois.detalhado_por is
  'Qual máquina detalhou. Existe para medir o ganho da paralelização, não '
  'para o processo — nenhuma decisão depende deste campo.';

-- O índice da fila: parcial, e com `id_empresa` na frente como todo índice
-- daqui — política de RLS é avaliada por linha.
create index if not exists ix_pois_a_detalhar
    on radar_comercial.pois (id_empresa, sessao, id)
 where fonte = 'maps' and place_id is not null and detalhado_em is null;

do $$
declare n int;
begin
  select count(*) into n from information_schema.columns
   where table_schema = 'radar_comercial' and table_name = 'pois'
     and column_name in ('detalhado_em', 'detalhado_por');
  if n <> 2 then raise exception 'esperava 2 colunas novas, achei %', n; end if;

  if to_regclass('radar_comercial.ix_pois_a_detalhar') is null then
    raise exception 'o indice da fila nao foi criado';
  end if;

  select count(*) into n from radar_comercial.pois
   where fonte = 'maps' and place_id is not null and detalhado_em is null;
  raise notice 'fila pronta; % POI(s) do Maps aguardando detalhe', n;
end $$;

commit;
