-- 0057 — um papel só para o reaproveitamento, e só ele atravessa
--
-- O QUE A MIGRAÇÃO 0055 ERROU
--
-- Ela pôs as três funções de reúso como `security definer` pertencentes a
-- `migrator`, supondo que isso bastasse para ler POI de outra empresa. Não
-- basta, e o motivo é sutil o suficiente para merecer estar escrito:
--
--   `security definer` troca o PAPEL de execução. A política de `pois` não
--   olha para o papel — ela chama `core.empresa_atual()`, que lê
--   `request.jwt.claim.sub`, um GUC de sessão. Trocar de papel não troca o
--   GUC. A função continuava enxergando exatamente a mesma empresa de quem a
--   chamou.
--
-- Medido: chamada pelo pipeline da Corsan, `reuso_resumo` devolveu "Corsan,
-- 495 POIs" e mais nada — como se a área nunca tivesse sido extraída por
-- ninguém. A resposta errada com cara de certa.
--
-- Para um usuário NÍVEL 9 teria funcionado, porque a política tem o ramo
-- `eh_suporte()`. Mas quem usa o painel do cliente é o administrador DELE,
-- nível 4, e para esse a resposta seria sempre "não há nada a reaproveitar".
--
-- O CONSERTO É UM PAPEL, E É ESTREITO DE PROPÓSITO
--
-- `reuso_servico` tem `bypassrls` e `nologin`: ninguém entra com ele, e a única
-- coisa que roda com os direitos dele são as três funções abaixo. O que as
-- torna seguras não é o papel — é o que elas devolvem:
--
--   `reuso_resumo`      contagem e data por empresa. Nem nome de POI sai.
--   `reuso_candidatos`  id, coordenada e nome do que a empresa do chamador
--                       ainda não tem. É o mínimo para desenhar a prévia.
--   `reusar_pois`       copia PARA `core.empresa_atual()`. O destino não é
--                       parâmetro, então a função não serve para gravar na
--                       empresa alheia nem para ler dado bruto da vizinha.
--
-- Dar `bypassrls` ao `migrator` teria funcionado igual e seria muito pior: ele
-- é o dono de todas as tabelas e roda toda migração. Um papel que existe só
-- para isto é o que permite responder "quem pode atravessar o isolamento?" com
-- uma consulta em vez de uma auditoria.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'reuso_servico') then
        create role reuso_servico nologin bypassrls;
    else
        alter role reuso_servico nologin bypassrls;
    end if;
end $$;

grant usage on schema radar_comercial, core, extensions to reuso_servico;
grant select, insert on all tables in schema radar_comercial to reuso_servico;
grant usage, select on all sequences in schema radar_comercial to reuso_servico;
grant select on core.tb_empresas, core.tb_users to reuso_servico;
grant execute on all functions in schema extensions to reuso_servico;
grant execute on function core.empresa_atual(), core.eh_suporte() to reuso_servico;

alter function radar_comercial.reuso_resumo(double precision, double precision,
                                            double precision, double precision)
    owner to reuso_servico;
alter function radar_comercial.reuso_candidatos(double precision, double precision,
                                                double precision, double precision)
    owner to reuso_servico;
alter function radar_comercial.reusar_pois(bigint[]) owner to reuso_servico;

-- Trocar o dono zera os privilégios de execução; eles voltam aqui.
grant execute on function radar_comercial.reuso_resumo(double precision, double precision,
                                                       double precision, double precision)
    to app_user, readonly;
grant execute on function radar_comercial.reuso_candidatos(double precision, double precision,
                                                           double precision, double precision)
    to app_user;
grant execute on function radar_comercial.reusar_pois(bigint[]) to app_user;

comment on role reuso_servico is
    'Papel do reaproveitamento entre empresas. `bypassrls` e `nologin`: só as '
    'três funções de reúso rodam com ele, e nenhuma devolve dado bruto de '
    'outra empresa nem grava fora de core.empresa_atual().';
