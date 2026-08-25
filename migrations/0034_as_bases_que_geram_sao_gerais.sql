-- 0034 — as bases que GERAM POI são gerais; o POI é de cada empresa.
--
-- Regra do dono do produto, 25/08/2026, e ela corta em dois lugares diferentes:
--
--   o POI é DA EMPRESA          cada uma roda a sua mineração, e o que ela
--                               achou é dela. `pois` mantém `tenant_id`.
--   a BASE que gera é GERAL     Cadastur, Receita, CNEFE, iFood. Duas
--                               concessionárias na mesma cidade leem o MESMO
--                               cadastro federal — carimbá-lo faria cada uma
--                               baixar, tratar e guardar de novo os mesmos
--                               5.443 prestadores e os mesmos 27.147 CNPJs.
--
-- É o mesmo raciocínio que já valia para `endereco_segmentado` e
-- `logradouro_ajustado`: "R. Mal. Rondon, 1199" é o mesmo endereço para
-- qualquer cliente. Faltava estendê-lo a montante.
--
-- ================= O QUE ESTA MIGRAÇÃO NÃO FAZ =============================
--
-- Não apaga a coluna. `tenant_id` vira `importado_por`, e com isso:
--
--   sai da REGRA de isolamento (a migração 0029 cobra RLS de toda tabela que
--   tenha `tenant_id`, e o teste cobra a regra, não uma lista);
--   e a informação FICA — dá para responder "quem trouxe estas linhas" depois,
--   que é exatamente o tipo de pergunta que some quando se apaga a coluna.
--
-- Passa a aceitar nulo: a partir daqui ninguém é obrigado a declarar dono de
-- uma base pública, e o `ifood_merchant` insere sem o gatilho que sumiu.

do $$
declare t text;
begin
  foreach t in array array['cadastur_prestador', 'cadastur_total_pf',
                           'cnpj_tratado', 'cnefe_coletiva', 'ifood_merchant']
  loop
    execute format('drop policy if exists tenant_isolado on comercialradar.%I', t);
    execute format('drop policy if exists %I on comercialradar.%I', t || '_por_tenant', t);
    execute format('alter table comercialradar.%I disable row level security', t);
    execute format('alter table comercialradar.%I no force row level security', t);
    execute format('drop trigger if exists %I on comercialradar.%I', 'tg_tenant_' || t, t);
    execute format('drop trigger if exists %I on comercialradar.%I', t || '_tenant', t);

    -- O gatilho pode ter outro nome: derruba QUALQUER um que chame
    -- `preencher_tenant` nesta tabela. Procurar pelo nome que eu imagino
    -- deixaria o gatilho vivo e a inserção continuaria carimbando.
    execute (
      select coalesce(string_agg(
               format('drop trigger if exists %I on comercialradar.%I;',
                      tg.tgname, t), ' '), 'select 1')
        from pg_trigger tg
        join pg_proc pr on pr.oid = tg.tgfoid
        join pg_class c on c.oid = tg.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'comercialradar' and c.relname = t
         and not tg.tgisinternal and pr.proname = 'preencher_tenant');

    if exists (select 1 from information_schema.columns
                where table_schema = 'comercialradar' and table_name = t
                  and column_name = 'tenant_id') then
      execute format('alter table comercialradar.%I alter column tenant_id drop not null', t);
      execute format('alter table comercialradar.%I alter column tenant_id drop default', t);
      execute format('alter table comercialradar.%I rename column tenant_id to importado_por', t);
    end if;
  end loop;
end $$;


-- ============ O BECO QUE O POI POR EMPRESA ABRE, e o conserto ==============
--
-- `ux_pois_place_id` (migração 0033) é único GLOBAL. Enquanto o POI era tratado
-- como coisa única no banco isso bastava. Com o POI sendo DE CADA EMPRESA, ele
-- vira uma parede: se a Corsan importa Canoas, a próxima concessionária na
-- mesma cidade não consegue importar os mesmos pontos — o índice recusa — e
-- também não os enxerga, porque a RLS filtra. A base fica presa na primeira
-- empresa que chegar.
--
-- A unicidade continua existindo, e continua sendo a mesma promessa ("rodar de
-- novo ACRESCENTA, não repete"): ela só passa a valer DENTRO da empresa, que é
-- o escopo em que a promessa faz sentido.
drop index if exists comercialradar.ux_pois_place_id;

create unique index if not exists ux_pois_place_id_por_empresa
  on comercialradar.pois (tenant_id, place_id)
  where place_id is not null and place_id <> '';

comment on index comercialradar.ux_pois_place_id_por_empresa is
  'Um POI por place_id POR EMPRESA. Global impediria a segunda concessionaria '
  'na mesma cidade de importar os mesmos pontos publicos (migracao 0034).';
