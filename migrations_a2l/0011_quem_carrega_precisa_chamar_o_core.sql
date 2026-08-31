-- 0011 — quem carrega base com RLS precisa poder CHAMAR o core
--
-- A `0010` moveu a `cadastro_corsan` para `resources_root` e concedeu
-- select/insert/update/delete a `resources_loader`, e eu escrevi que com isso
-- "a carga da próxima versão da base continua funcionando pelo mesmo caminho".
-- Era falso, e quem mediu foi a sessão de infra:
--
--     set local role resources_loader;
--     set local request.jwt.claims = '{"sub":"<root>","role":"authenticated"}';
--     insert into resources_root.cadastro_corsan (...) values (...);
--     ERROR:  permission denied for function nivel_atual
--
-- O GRANT DE TABELA NÃO BASTA QUANDO A TABELA TEM RLS. A política
-- `p_corsan_escreve` chama `core.nivel_atual()`; sem EXECUTE nela, o INSERT
-- morre ao avaliar a própria política que deveria autorizá-lo. Medido:
--
--     has_function_privilege('resources_loader','core.nivel_atual()','execute') = false
--     has_function_privilege('migrator',        'core.nivel_atual()','execute') = false
--     app_user, readonly, authenticated                                         = true
--
-- É o mesmo buraco que derrubou o `count(*)` da 0010 — eu o encontrei ali,
-- concluí "confere pelo catálogo em vez de contar linha" e segui, sem notar
-- que ele também estava no caminho de ESCRITA. Encontrar um defeito e desviar
-- dele não é o mesmo que corrigi-lo.
--
-- `SECURITY DEFINER` NÃO RESOLVE ISTO, e vale registrar porque foi minha
-- primeira ideia. As três funções já são security definer — isso governa o que
-- elas acessam por DENTRO, não quem pode CHAMÁ-LAS. O EXECUTE continua sendo
-- exigido.
--
-- Por que estas três e não outras: são as únicas que as políticas de RLS
-- chamam. Todas leem apenas o JWT de quem chamou; não expõem dado de ninguém,
-- e a quem não tem crachá respondem nulo.
--
-- Por que uma migração nova e não editar a 0010: a 0010 já foi aplicada.
-- Reescrever migração aplicada faz o histórico mentir sobre o que aconteceu, e
-- deixa quem reconstrói do zero com um caminho diferente do que foi percorrido.
--
-- ────────────────────────────────────────────────────────────────────────────
-- ESTA MIGRAÇÃO RODA COMO `supabase_admin`, NÃO COMO `migrator`
--
--     docker exec supabase-db psql -U supabase_admin -d a2l -f 0011.sql
--
-- É a única até aqui que sai do caminho padrão, e o motivo é de propriedade:
-- quem concede EXECUTE numa função é o dono dela, e as três pertencem a
-- `supabase_admin` — não a `migrator`, que é dono do schema `core` mas não das
-- funções dentro dele. Rodando pelo caminho normal, a migração falha com a
-- mesma mensagem que ela existe para consertar:
--
--     ERROR:  permission denied for function nivel_atual
--
-- Que é quase engraçado, e é também a prova de que o diagnóstico está certo.
-- ────────────────────────────────────────────────────────────────────────────

begin;

grant execute on function core.nivel_atual()   to resources_loader, migrator;
grant execute on function core.empresa_atual() to resources_loader, migrator;
grant execute on function core.eh_suporte()    to resources_loader, migrator;

-- ── conferência ─────────────────────────────────────────────────────────────
do $$
declare
  falta text;
begin
  select string_agg(p.papel || ' -> ' || p.fn, ', ')
    into falta
    from (select r.rolname as papel, f.fn
            from (values ('resources_loader'), ('migrator')) r(rolname)
           cross join (values ('core.nivel_atual()'),
                              ('core.empresa_atual()'),
                              ('core.eh_suporte()')) f(fn)
         ) p
   where not has_function_privilege(p.papel, p.fn, 'execute');

  if falta is not null then
    raise exception 'sem EXECUTE: %', falta;
  end if;
  raise notice 'resources_loader e migrator podem chamar as tres funcoes do core';
end $$;

commit;
