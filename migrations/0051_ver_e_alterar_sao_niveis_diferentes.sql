-- 0051 — ver o POI e alterar o POI passam a ser permissões diferentes
--
-- O QUE O DOCUMENTO PEDIU
--
-- "Os POIs só podem ser tratados, alterados e etc por membros da mesma empresa
-- de quem os gerou e de nível administrador para cima. Mesmo a visualização
-- deles só é permitida por membros da mesma empresa, mas essa permissão é de
-- qualquer nível dentro da empresa."
--
-- São DUAS regras, e a política de hoje só sabe expressar uma. `p_pois` é uma
-- policy `ALL`: quem passa nela lê e escreve igual. Um usuário nível 1 da
-- empresa podia apagar POI.
--
-- Depois desta migração:
--
--     SELECT                  qualquer nível da mesma empresa
--     INSERT/UPDATE/DELETE    nível 4 (Administrador) ou 9 (Root), mesma empresa
--
-- CONFIRMAR VÍNCULO CONTA COMO ALTERAR — decidido pelo dono do produto em
-- 03/09/2026. Editor (2) e Supervisor (3) passam a só visualizar. É a leitura
-- literal do documento e a mais restrita das duas que estavam em jogo.
--
-- O PIPELINE JÁ ESTÁ DO LADO CERTO DESSA LINHA: a migração 0050 criou
-- `pipeline@corsan.servico.invalido` no nível 4 justamente porque ele altera
-- POI o tempo todo. Se ele ainda fosse o nível 1 da A2L, esta migração o
-- desligaria — e o sintoma seria uma run que roda até o fim sem gravar nada.
--
-- O SUBSELECT NÃO É ENFEITE, e é o que separa esta migração de uma que derruba
-- o painel. Política de RLS é avaliada POR LINHA: `core.nivel_atual() >= 4`
-- escrito direto rodaria a função 301.297 vezes numa varredura da `pois`.
-- Envolvido em `(select ...)`, o Postgres o promove a InitPlan e executa UMA
-- vez por consulta. Vale para `eh_suporte()` e `empresa_atual()` também, e por
-- isso as três aparecem embrulhadas aqui.

-- ── o nível de quem está falando ───────────────────────────────────────────
-- SECURITY DEFINER porque `core.tb_users` tem RLS: sem isso a função não
-- conseguiria ler a própria linha do usuário para descobrir o nível dele.
create or replace function core.nivel_atual()
returns smallint
language sql
stable
security definer
set search_path to 'core', 'public'
as $$
    select coalesce((select id_nivel_user from core.tb_users
                      where id = (select auth.uid())), 0)::smallint
$$;

comment on function core.nivel_atual() is
    'Nível do usuário da sessão: 1 Usuario, 2 Editor, 3 Supervisor, '
    '4 Administrador, 9 Root. Zero quando não há usuário identificado.';

grant execute on function core.nivel_atual() to app_user, readonly;

-- ── a troca, tabela a tabela ───────────────────────────────────────────────
do $$
declare
    t     text;
    -- Os parênteses são explícitos de propósito. `and` liga mais forte que
    -- `or`, então a versão sem eles até funcionaria — mas quem revisar esta
    -- policy daqui a um ano precisa VER que o suporte escapa da regra de nível,
    -- e não deduzir isso da precedência de operador.
    ver   text := '((select core.eh_suporte()) '
                  'or (id_empresa = (select core.empresa_atual())))';
    mexer text := '((select core.eh_suporte()) '
                  'or ((id_empresa = (select core.empresa_atual())) '
                  'and ((select core.nivel_atual()) >= 4)))';
begin
    foreach t in array array[
        -- o POI e tudo que o descreve
        'pois', 'maps_data', 'osm_data', 'overture_data', 'foursquare_data',
        'receita_data', 'cadastur_data', 'ifood_merchant', 'airbnb_anuncio',
        'comentarios', 'images_urls', 'streetview_imgs', 'horario_funcionamento',
        'analise_ia', 'cnpj_tratado', 'cadastur_vinculo', 'fachada_anotacao',
        'fachada_triagem', 'foto_maps_triagem', 'atribuicao',
        'atribuicao_divergente', 'vinculo_poi', 'cruzamento',
        -- o endereço trabalhado a partir do POI
        'endereco_segmentado', 'logradouro_ajustado', 'logradouro_resolvido',
        -- o vínculo com a base do cliente: confirmar é alterar
        'ligacao_poi'
    ]
    loop
        if not exists (select 1 from pg_tables
                        where schemaname = 'radar_comercial' and tablename = t) then
            raise notice 'PULADA (nao existe): %', t;
            continue;
        end if;

        -- a policy `ALL` antiga sai; entram quatro, uma por comando.
        execute format('drop policy if exists %I on radar_comercial.%I', 'p_' || t, t);
        execute format('drop policy if exists %I on radar_comercial.%I', t || '_por_empresa', t);
        execute format('drop policy if exists %I on radar_comercial.%I', t || '_por_empresa_pol', t);

        execute format(
            'create policy %I on radar_comercial.%I for select using %s',
            t || '_ver', t, ver);
        execute format(
            'create policy %I on radar_comercial.%I for insert with check %s',
            t || '_criar', t, mexer);
        execute format(
            'create policy %I on radar_comercial.%I for update using %s with check %s',
            t || '_alterar', t, mexer, mexer);
        execute format(
            'create policy %I on radar_comercial.%I for delete using %s',
            t || '_apagar', t, mexer);

        raise notice 'ver/alterar separados em %', t;
    end loop;
end $$;
