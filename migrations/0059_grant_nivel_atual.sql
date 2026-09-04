-- 0059 — `reuso_servico` alcança `core.nivel_atual()`
--
-- A migração 0058 pôs a checagem de nível dentro de `reusar_pois`, e a 0057
-- tinha concedido ao papel só `empresa_atual()` e `eh_suporte()` — as duas que
-- existiam quando ela foi escrita. `nivel_atual()` nasceu na 0051, entre as
-- duas, e ficou de fora da lista.
--
-- O sintoma foi bom: `permission denied for function nivel_atual`, na linha da
-- checagem, antes de qualquer escrita. A trava falhou FECHADA — ninguém copiou
-- nada. É o modo certo de uma permissão faltar.

grant execute on function core.nivel_atual() to reuso_servico;
