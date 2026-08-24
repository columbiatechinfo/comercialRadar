-- 0029 — as quatro tabelas que nasceram sem política de isolamento
--
-- O DEFEITO, achado ao medir o estado do sistema em 24/08/2026
--
--     select relname, relrowsecurity from pg_class ... -- schema comercialradar
--     -> 24 tabelas com RLS ligada e policy
--        4 sem nada:  atribuicao_divergente  fachada_triagem
--                     foto_maps_triagem      ifood_merchant
--
-- O `docs/estado.json` dizia `tabelas_sem_rls: []`. Dizia errado desde 13/08.
--
-- E o padrão é o mesmo da 0024 — são as MESMAS quatro daquela lista, mais as
-- que já foram corrigidas lá. Não é esquecimento pontual: **tabela nova não
-- herda a política**. Quem cria tabela precisa escrever as quatro linhas
-- (enable, force, policy, trigger), e quatro vezes isso não aconteceu.
--
-- O QUE MUDA NA PRÁTICA, e o que NÃO muda
--
-- `comercialradar_worker` e `comercialradar_root` têm `rolbypassrls`. O
-- pipeline, portanto, não sente nada — continua lendo e escrevendo tudo.
-- Quem é submetido é `comercialradar_app`, o papel do servidor web. Ou seja:
-- **este é exatamente o buraco pelo qual o painel de uma empresa mostrava
-- linha de outra**, nas quatro tabelas.
--
-- O tenant_id do ifood_merchant
--
-- As outras três já tinham `tenant_id` preenchido em todas as linhas. O
-- `ifood_merchant` não tinha em NENHUMA das 1.598 — ligar a RLS sem preencher
-- tornaria as 1.598 invisíveis para o painel, que é pior que o problema.
--
-- Elas vêm de uma raspagem única de 19/08 (1h21 de execução) sobre a região
-- metropolitana inteira, sem vínculo a POI e com CNPJ em 16 das 1.598. A
-- atribuição escolhida pelo dono do produto é **por cidade**: cada linha vai
-- para a empresa que mais tem POI naquele município. Fica registrada aqui
-- porque é decisão, não derivação óbvia — o critério diz onde cada empresa
-- trabalhou, não de quem o dado é.
--
--     canoas 770 -> Aegea (21.935 POIs)      porto-alegre 310 -> Columbia (884)
--     gravatai 251 -> Columbia (37)          cachoeirinha 196 -> Aegea (254)
--     esteio 35 -> Aegea (298)               ... 1.002 Aegea / 596 Columbia

begin;

-- ─── 1 · o tenant do ifood_merchant ──────────────────────────────────────────
--
-- Uma linha tem a cidade grafada 'porto-alergre'. Corrigida antes do
-- cruzamento, senão ela ficaria sem dono e sumiria do painel em silêncio —
-- exatamente o modo de falha que esta migration existe para fechar.

update comercialradar.ifood_merchant
   set cidade = 'porto-alegre'
 where cidade = 'porto-alergre';

-- `translate` em vez de `unaccent`: a extensão vive no schema `extensions`, e
-- o papel que roda as migrations não tem acesso a ele.
with dono as (
  select translate(lower(p.cidade), 'áàâãéêíóôõúüç', 'aaaaeeiooouuc') as cidade_norm,
         p.tenant_id,
         row_number() over (
           partition by translate(lower(p.cidade), 'áàâãéêíóôõúüç', 'aaaaeeiooouuc')
           order by count(*) desc, p.tenant_id) as rn
    from comercialradar.pois p
   where p.cidade is not null
     and p.tenant_id is not null
   group by 1, 2)
update comercialradar.ifood_merchant m
   set tenant_id = d.tenant_id
  from dono d
 where m.tenant_id is null
   and d.rn = 1
   and d.cidade_norm = replace(lower(m.cidade), '-', ' ');

-- Se sobrar linha sem dono, a migration PARA. Uma linha órfã com RLS ligada é
-- uma linha que ninguém mais vê, e ninguém seria avisado disso.
do $$
declare orfas int;
begin
  select count(*) into orfas
    from comercialradar.ifood_merchant where tenant_id is null;
  if orfas > 0 then
    raise exception 'ifood_merchant: % linha(s) sem tenant_id. '
                    'Ligar RLS agora as tornaria invisiveis.', orfas;
  end if;
end $$;

alter table comercialradar.ifood_merchant
  alter column tenant_id set not null;

-- ─── 2 · as quatro, no mesmo padrão das outras 24 ────────────────────────────

alter table comercialradar.atribuicao_divergente enable row level security;
alter table comercialradar.atribuicao_divergente force  row level security;
drop policy if exists tenant_isolado on comercialradar.atribuicao_divergente;
create policy tenant_isolado on comercialradar.atribuicao_divergente
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);
drop trigger if exists tg_tenant on comercialradar.atribuicao_divergente;
create trigger tg_tenant before insert on comercialradar.atribuicao_divergente
  for each row execute function comercialradar.preencher_tenant();

alter table comercialradar.fachada_triagem enable row level security;
alter table comercialradar.fachada_triagem force  row level security;
drop policy if exists tenant_isolado on comercialradar.fachada_triagem;
create policy tenant_isolado on comercialradar.fachada_triagem
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);
drop trigger if exists tg_tenant on comercialradar.fachada_triagem;
create trigger tg_tenant before insert on comercialradar.fachada_triagem
  for each row execute function comercialradar.preencher_tenant();

alter table comercialradar.foto_maps_triagem enable row level security;
alter table comercialradar.foto_maps_triagem force  row level security;
drop policy if exists tenant_isolado on comercialradar.foto_maps_triagem;
create policy tenant_isolado on comercialradar.foto_maps_triagem
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);
drop trigger if exists tg_tenant on comercialradar.foto_maps_triagem;
create trigger tg_tenant before insert on comercialradar.foto_maps_triagem
  for each row execute function comercialradar.preencher_tenant();

alter table comercialradar.ifood_merchant enable row level security;
alter table comercialradar.ifood_merchant force  row level security;
drop policy if exists tenant_isolado on comercialradar.ifood_merchant;
create policy tenant_isolado on comercialradar.ifood_merchant
  using      (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid)
  with check (tenant_id = (select nullif(current_setting('app.tenant_id', true), ''))::uuid);
drop trigger if exists tg_tenant on comercialradar.ifood_merchant;
create trigger tg_tenant before insert on comercialradar.ifood_merchant
  for each row execute function comercialradar.preencher_tenant();

-- ─── 3 · o índice que torna a policy viável sob carga ────────────────────────
--
-- A RLS é avaliada POR LINHA. Sem `tenant_id` na primeira coluna, o filtro da
-- policy não usa índice e o custo cresce com a base inteira, não com a fatia
-- da empresa. `atribuicao_divergente` e `ifood_merchant` não tinham; as outras
-- duas já têm (`fachada_triagem_tenant_idx`, `foto_maps_triagem_tenant_idx`).

create index if not exists ix_atribuicao_div_tenant
  on comercialradar.atribuicao_divergente (tenant_id);

create index if not exists ix_ifood_merchant_tenant
  on comercialradar.ifood_merchant (tenant_id, cidade);

commit;
