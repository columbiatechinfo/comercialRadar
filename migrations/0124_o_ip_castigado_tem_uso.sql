-- 0124 · o IP castigado tem uso (dono do produto, 17/09/2026).
--
-- MEDIDO NO iFOOD HOJE: a sessão que o site ACEITA recebe o feed principal, com 20 lojas por página, e pedir a página
-- seguinte chama a verificação "Pressione e segure". A sessão que o site RECUSA (403 do anti-robô) cai no feed de
-- reserva, que entrega a lista INTEIRA de uma vez — 489 lojas num ponto do centro de Santa Maria, sem clique nenhum.
-- Ou seja: para esta coleta, o IP malvisto rende mais que o IP limpo, e o castigo que ele levou em outro processo
-- deixa de ser desperdício.
--
-- `pegar_proxy` ganha `p_castigo`: 'evitar' (o de sempre, e o padrão), 'preferir' (castigado primeiro, e o castigo
-- não exclui ninguém) ou 'indiferente'. A reserva continua valendo — dois navegadores nunca pegam o mesmo IP no
-- mesmo site.
set lock_timeout = '5s';
begin;
set local role migrator;

create or replace function navegacao.pegar_proxy(p_site text, p_dono text, p_segundos integer default 1800,
                                                 p_paises text[] default array['BR', 'CO'],
                                                 p_castigo text default 'evitar')
returns text language plpgsql as $$
declare c text; w text;
begin
    for c in
        select p.id
          from navegacao.proxy p
          left join navegacao.proxy_site s on s.site = p_site and s.proxy_id = p.id
          left join navegacao.reserva r on r.site = p_site and r.proxy_id = p.id and r.ate > now()
         where p.ativo and p.pais = any (p_paises)
           and r.proxy_id is null
           and (p_castigo <> 'evitar' or s.castigo_ate is null or s.castigo_ate <= now())
         order by array_position(p_paises, p.pais),
                  case when p_castigo = 'preferir'
                       then -coalesce(s.castigo_nivel, 0)            -- castigado primeiro
                       else coalesce(s.castigo_nivel, 0) end,
                  s.usado_em nulls first,
                  random()
         limit 32
    loop
        insert into navegacao.reserva as r (site, proxy_id, dono, ate)
        values (p_site, c, p_dono, now() + make_interval(secs => p_segundos))
        on conflict (site, proxy_id) do update set dono = excluded.dono, ate = excluded.ate, em = now()
           where r.ate <= now()
        returning r.proxy_id into w;
        if w is not null then
            insert into navegacao.proxy_site (site, proxy_id, usado_em) values (p_site, w, now())
            on conflict (site, proxy_id) do update set usado_em = now();
            return w;
        end if;
    end loop;
    return null;
end $$;

-- A DE QUATRO ARGUMENTOS SAI: com as duas no catálogo, uma chamada sem `p_castigo` ficaria ambígua.
drop function if exists navegacao.pegar_proxy(text, text, integer, text[]);
grant execute on function navegacao.pegar_proxy(text, text, integer, text[], text) to app_user;
commit;
