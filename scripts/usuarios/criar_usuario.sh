#!/bin/bash
#
# criar_usuario.sh — cria um usuario que ENTRA no painel.
#
#   bash scripts/usuarios/criar_usuario.sh <email> "<nome>" <empresa> <nivel>
#
#   empresa   nome da empresa em core.tb_empresas (ex.: "Corsan - Aegea RS")
#   nivel     1 Usuario · 2 Editor · 3 Supervisor · 4 Administrador · 9 Root
#
# TRES ARMADILHAS QUE ESTE SCRIPT EVITA, e as tres custaram tempo em 04/09/2026.
#
# 1. `auth.identities` E OBRIGATORIA. O GoTrue procura a identidade no login por
#    e-mail; sem a linha o usuario existe no banco e nao entra, sem erro que
#    explique.
#
# 2. AS COLUNAS DE TOKEN NAO PODEM SER NULAS. O GoTrue le `confirmation_token`,
#    `recovery_token` e mais seis como `string` do Go — um NULL do banco nao
#    cabe ali, e o servico responde `500 Database error querying schema`, uma
#    mensagem que aponta para o SCHEMA quando o problema e UMA LINHA. Usuario
#    criado pela API do Supabase nasce com string vazia; criado por INSERT
#    direto, nasce nulo. Aqui nascem vazias.
#
# 3. `email_confirmed_at` PRECISA ESTAR PREENCHIDO. Nao ha caixa de e-mail para
#    confirmar num ambiente interno, e sem ele o login falha com "email not
#    confirmed" — que quem esta digitando le como senha errada.
#
# A SENHA NASCE NO SERVIDOR E FICA NO SERVIDOR. Ela nao vai por argumento de
# linha de comando (argv e visivel no `ps` para qualquer processo da maquina)
# nem para a saida do script. O SQL que a contem e escrito com modo 600, roda, e
# e apagado. O que sobra e o arquivo da senha, tambem 600.
set -e

EMAIL="${1:?informe o e-mail}"
NOME="${2:?informe o nome}"
EMPRESA="${3:?informe a empresa}"
NIVEL="${4:?informe o nivel (1,2,3,4,9)}"

SUPORTE="${RADAR_USUARIO_SUPORTE_ADMIN:-b5b544cb-ed45-470f-bb10-4cbda574d3f2}"
ARQ_SENHA="$HOME/$(echo "$EMAIL" | cut -d@ -f1).senha.txt"

SENHA="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 22)"
umask 077
printf '%s\n' "$SENHA" > "$ARQ_SENHA"

SQL=$(mktemp /tmp/criar_user.XXXXXX.sql)
chmod 600 "$SQL"
cat > "$SQL" <<SQLFIM
begin;
-- Como o SUPORTE: o gatilho \`tg_trava_escalada\` dispara no INSERT tambem, e
-- recusa quem tenta definir nivel sem autoridade para isso.
select set_config('request.jwt.claim.sub', '${SUPORTE}', true);

do \$\$
declare
    v_id      uuid := gen_random_uuid();
    v_email   text := '${EMAIL}';
    v_empresa uuid;
begin
    select id into v_empresa from core.tb_empresas where name = '${EMPRESA}';
    if v_empresa is null then
        raise exception 'nao achei a empresa %', '${EMPRESA}';
    end if;
    if exists (select 1 from auth.users where email = v_email) then
        raise exception 'ja existe usuario com o e-mail %', v_email;
    end if;

    insert into auth.users (
        id, instance_id, aud, role, email, encrypted_password,
        email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
        created_at, updated_at,
        -- vazias, e nao nulas: ver a armadilha 2 no topo do script
        confirmation_token, recovery_token, email_change_token_new,
        email_change_token_current, email_change, phone_change,
        phone_change_token, reauthentication_token)
    values (
        v_id, '00000000-0000-0000-0000-000000000000',
        'authenticated', 'authenticated', v_email,
        extensions.crypt('${SENHA}', extensions.gen_salt('bf')),
        now(),
        '{"provider": "email", "providers": ["email"]}'::jsonb,
        '{"email_verified": true}'::jsonb,
        now(), now(),
        '', '', '', '', '', '', '', '');

    insert into auth.identities (
        id, user_id, provider, provider_id, identity_data,
        created_at, updated_at)
    values (
        gen_random_uuid(), v_id, 'email', v_id::text,
        jsonb_build_object('sub', v_id::text, 'email', v_email,
                           'email_verified', true, 'phone_verified', false),
        now(), now());

    insert into core.tb_users (
        id, id_empresa, id_nivel_user, email, name,
        confirmated_email, email_confirmado_em, ativo)
    values (v_id, v_empresa, ${NIVEL}, v_email, '${NOME}', true, now(), true);

    raise notice 'criado % (%) — % nivel %', v_email, v_id, '${EMPRESA}', ${NIVEL};
end \$\$;
commit;
SQLFIM

docker cp "$SQL" supabase-db:/tmp/criar_user.sql >/dev/null
docker exec supabase-db psql -U supabase_admin -d a2l -v ON_ERROR_STOP=1 \
    -f /tmp/criar_user.sql 2>&1 | sed 's/^psql.*NOTICE:  //'
docker exec supabase-db rm -f /tmp/criar_user.sql
rm -f "$SQL"

echo
echo "senha em: $ARQ_SENHA (modo $(stat -c '%a' "$ARQ_SENHA"))"
echo "   leia com:  cat $ARQ_SENHA"
