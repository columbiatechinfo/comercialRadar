-- 0050 — o POI passa a ser do cliente, e o pipeline ganha identidade nele
--
-- A CORREÇÃO QUE O DONO DO PRODUTO PEDIU
--
-- "Os POIs só podem ser tratados e alterados por membros da mesma empresa de
-- quem os gerou." Até aqui o desenho contrariava isso: os 301.297 POIs eram da
-- A2L, e as 2.516.709 ligações eram da Corsan. O cruzamento entre eles só
-- funcionava porque `cruzar_ligacao.py` assumia um usuário nível 9 e usava
-- `core.eh_suporte()` para atravessar a fronteira.
--
-- ISSO NÃO ERA UM DETALHE DE PERMISSÃO. Era o produto inteiro rodando com a
-- trava de isolamento desligada: um bug em qualquer consulta do cruzamento
-- veria as três empresas do banco, não uma. A elevação a suporte existia para
-- contornar uma modelagem errada, e a modelagem certa é a que o documento diz —
-- o POI mineirado PARA um cliente É do cliente.
--
-- Depois desta migração o cruzamento é intra-empresa e a elevação sai do código.
--
-- POR QUE O PIPELINE PRECISA DE UM USUÁRIO NOVO
--
-- `pipeline@a2l.servico.invalido` é nível 1 e da A2L. Com os POIs na Corsan ele
-- deixaria de enxergá-los — e mesmo que enxergasse, o documento exige nível
-- Administrador para alterar POI. Reaproveitá-lo subindo de nível resolveria
-- hoje e quebraria no próximo cliente: um usuário de serviço pertence a UMA
-- empresa, e cada cliente precisa do seu. `pipeline@corsan.servico.invalido`
-- nasce nível 4 na Corsan; o da A2L continua existindo para o que for da A2L.
--
-- O QUE NÃO MUDA DE EMPRESA, e é decisão, não esquecimento: `proxy_ip` e
-- `proxy_evento`. O plano de proxy é custo operacional da A2L, contratado por
-- ela e compartilhado entre clientes. Ele não é dado da Corsan e não deve
-- aparecer para ela.
--
-- REVERSÍVEL: a volta é trocar `d4939b46…` por `4f5624c7…` nos mesmos comandos.
-- Nada é apagado.

-- ── 1. o usuário de serviço da Corsan ──────────────────────────────────────
--
-- A linha de `auth.users` é CLONADA da do pipeline da A2L em vez de escrita
-- campo a campo. `auth.users` é tabela do Supabase, com colunas obrigatórias que
-- mudam de versão; clonar uma linha que o Auth já aceita é mais seguro que
-- adivinhar o formato de cada uma.
do $$
declare
    v_novo     uuid := '7f3a9c21-5b48-4e07-9d16-2c8ab4f10e93';
    v_corsan   uuid := 'd4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2';
    v_modelo   uuid;
    v_cols     text;
begin
    if exists (select 1 from auth.users where id = v_novo) then
        raise notice 'usuario de servico da Corsan ja existe, nada a fazer';
        return;
    end if;

    select id into v_modelo from auth.users
     where email = 'pipeline@a2l.servico.invalido';
    if v_modelo is null then
        raise exception 'nao achei o pipeline da A2L para clonar';
    end if;

    create temp table _clone on commit drop as
        select * from auth.users where id = v_modelo;
    update _clone set
        id                = v_novo,
        email             = 'pipeline@corsan.servico.invalido',
        -- os tokens do modelo são de OUTRA conta; carregar cópia deles seria
        -- criar dois usuários com o mesmo token de confirmação.
        confirmation_token     = '',
        recovery_token         = '',
        email_change_token_new = '',
        email_change_token_current = '',
        reauthentication_token = '',
        phone                  = null,
        created_at             = now(),
        updated_at             = now();

    -- AS COLUNAS SÃO LISTADAS, e não `select *`: `auth.users` tem coluna
    -- GERADA (`confirmed_at`), que o Postgres recusa receber valor. A lista sai
    -- do catálogo em vez de ficar escrita aqui — assim uma atualização do
    -- Supabase que acrescente coluna não quebra esta migração.
    select string_agg(quote_ident(column_name), ', ' order by ordinal_position)
      into v_cols
      from information_schema.columns
     where table_schema = 'auth' and table_name = 'users'
       and is_generated = 'NEVER';
    execute format('insert into auth.users (%s) select %s from _clone', v_cols, v_cols);

    insert into core.tb_users (id, id_empresa, id_nivel_user, email, name)
    values (v_novo, v_corsan, 4, 'pipeline@corsan.servico.invalido',
            'Pipeline Radar (servico)');

    raise notice 'criado pipeline@corsan.servico.invalido nivel 4 -> %', v_novo;
end $$;

-- ── 2. o dado passa para a empresa do cliente ──────────────────────────────
--
-- A ordem não importa (é tudo uma transação), mas a LISTA importa: deixar uma
-- tabela filha para trás produziria linhas invisíveis — o POI na Corsan e a sua
-- foto na A2L, com a RLS escondendo a foto de quem vê o POI. É o modo de falhar
-- mais caro desta migração, e por isso a lista é explícita em vez de um laço
-- sobre todas as tabelas com `id_empresa`.
do $$
declare
    t         text;
    v_a2l     uuid := '4f5624c7-c4fe-48b8-bff4-d392ea1abe6f';
    v_corsan  uuid := 'd4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2';
    n         bigint;
    total     bigint := 0;
begin
    foreach t in array array[
        -- o POI e as tabelas da sua fonte
        'pois', 'maps_data', 'osm_data', 'overture_data', 'foursquare_data',
        'receita_data', 'cadastur_data', 'ifood_merchant', 'airbnb_anuncio',
        -- o que se pendura no POI
        'comentarios', 'images_urls', 'streetview_imgs', 'horario_funcionamento',
        'analise_ia', 'cnpj_tratado', 'cadastur_vinculo', 'fachada_anotacao',
        'fachada_triagem', 'foto_maps_triagem', 'atribuicao',
        'atribuicao_divergente', 'vinculo_poi', 'cruzamento',
        -- o endereço trabalhado a partir do POI
        'endereco_segmentado', 'logradouro_ajustado', 'logradouro_resolvido',
        -- a base do cliente e o vínculo com ela
        'base_cliente', 'ligacao_poi', 'cadastro_cliente', 'area_trabalho',
        -- o trabalho: runs, arquivos, chat e memória sobre esses POIs
        'job', 'job_log', 'fonte_arquivos', 'chat_conversa', 'chat_mensagem',
        'chat_anexo', 'memoria', 'cnefe_coletiva', 'campo_catalogo'
    ]
    loop
        if not exists (select 1 from information_schema.columns
                        where table_schema = 'radar_comercial'
                          and table_name = t and column_name = 'id_empresa') then
            raise notice 'PULADA (sem id_empresa): %', t;
            continue;
        end if;
        execute format(
            'update radar_comercial.%I set id_empresa = $1 where id_empresa = $2', t)
            using v_corsan, v_a2l;
        get diagnostics n = row_count;
        total := total + n;
        if n > 0 then
            raise notice '% -> % linhas', rpad(t, 24), n;
        end if;
    end loop;
    raise notice 'TOTAL MOVIDO PARA A CORSAN: % linhas', total;
end $$;
