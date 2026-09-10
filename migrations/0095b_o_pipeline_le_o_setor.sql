-- 0095b · o pipeline precisa LER o setor.
--
-- A 0095 criou a tabela e a política de RLS e parou aí — RLS diz QUAIS linhas,
-- o GRANT diz SE a tabela pode ser tocada, e sem os dois a leitura morre com
-- "permission denied for table". É a quinta vez neste projeto que uma migração
-- é dada por pronta sem conferir quem lê a tabela depois.
--
-- SÓ SELECT: quem escreve o setor é o administrador, pelo painel ou pela mão,
-- e não o pipeline. Dar escrita aqui seria abrir para o processo automático
-- mudar o vocabulário do prompt sozinho.
grant select on radar_comercial.empresa_setor to app_user;
grant select on radar_comercial.empresa_setor to authenticated;
