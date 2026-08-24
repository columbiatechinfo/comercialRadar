-- =====================================================================
-- 0010 — a procedência é POR EMPRESA
--
-- `fonte_arquivos` nasceu antes do multi-cliente, com chave (fonte, referencia).
-- Depois ganhou `tenant_id` e RLS, e a chave ficou para trás. O resultado é uma
-- falha que se esconde:
--
--   A Corsan processa o município 4303103 e grava a linha. A Fimm processa o
--   mesmo município. A RLS esconde dela a linha da Corsan, então
--   `ja_carregado()` responde "não carregado" — correto do ponto de vista dela.
--   O INSERT então bate na chave primária de uma linha que ela não pode ver, e
--   o erro fala de duplicidade de um registro inexistente para quem o leu.
--
-- Duas empresas processando o mesmo município é o caso NORMAL: o dado público é
-- o mesmo, quem muda é o dono do resultado.
-- =====================================================================

set local search_path = comercialradar, public;

-- Só é seguro porque (fonte, referencia) já é único e nenhuma linha está sem
-- empresa — conferido antes de escrever esta migração.
alter table fonte_arquivos alter column tenant_id set not null;

alter table fonte_arquivos drop constraint fonte_arquivos_pkey;
alter table fonte_arquivos add constraint fonte_arquivos_pkey
  primary key (tenant_id, fonte, referencia);

-- O NOME da constraint continua o mesmo de propósito. `base_comum.py` roda
-- contra os DOIS bancos — o do produto, que tem empresa, e o de referência, que
-- não tem — e por isso os gravadores usam `ON CONFLICT ON CONSTRAINT
-- fonte_arquivos_pkey`: a mesma linha de código serve às duas chaves.
