-- 0003 — campos de perfil do usuário
--
-- `foto_path` guarda o CAMINHO no Storage, não os bytes. Mesma regra das fotos
-- de POI: byte de imagem no Postgres faz todo backup e toda replicação
-- carregarem o mesmo peso para sempre, e foto de perfil não precisa de transação.
alter table usuarios add column if not exists telefone      text;
alter table usuarios add column if not exists cargo         text;
alter table usuarios add column if not exists foto_path     text;
alter table usuarios add column if not exists atualizado_em timestamptz;
