-- 0025 — a logo da empresa cliente
--
-- O cabeçalho passa a ter TRÊS marcas, com papéis distintos (ADR 0005):
--
--     ComercialRadar   ·   para <cliente>   ·   por A2L
--
-- A imagem é do TENANT, não do usuário: todos os usuários da mesma empresa
-- veem a mesma marca. Já existe `usuarios.foto_path` para a foto pessoal — são
-- coisas diferentes e não se misturam.
--
-- POR QUE CAMINHO E NÃO BYTES
--
-- Segue o mesmo padrão de `usuarios.foto_path` e das imagens de fachada:
-- o arquivo vive no Storage e a coluna guarda o ponteiro. Guardar binário em
-- coluna faz o dump do banco crescer sem limite, e o backup diário já é o que
-- mais pesa na rotina.

begin;

alter table comercialradar.tenants
  add column if not exists logo_path text;

comment on column comercialradar.tenants.logo_path is
  'Ponteiro para a logo da empresa no Storage. Aparece no terceiro espaço do '
  'cabeçalho, entre o nome do produto e a assinatura da A2L. Nulo mostra só o '
  'nome da empresa em texto — ausência de logo não pode virar espaço quebrado.';

commit;
