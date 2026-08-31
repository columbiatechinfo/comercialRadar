-- 0004_comparavel.sql — os gatilhos que exigem que um registro seja comparável.
--
-- DE ONDE ELES VIERAM, e por que esta migração precisou existir.
--
-- `poi_comparavel` e `vinculo_comparavel` são citados em seis arquivos do
-- código como gatilhos existentes, com data ("desde 27/08/2026") e com o
-- comportamento descrito em detalhe. Nenhum arquivo do repositório os cria.
--
-- Eles foram feitos à mão, direto no banco antigo, e nunca versionados — então
-- morreram com a máquina. É a mesma classe de perda que o `.gitignore` já
-- registra para as 26 migrações originais: o banco tinha o resultado e ninguém
-- tinha a receita.
--
-- Reconstruídos a partir do que o CÓDIGO afirma sobre eles:
--
--   realtime_ingest.py:205   "recusa POI sem ele — um registro sem nome nem
--                             endereço tem teto de 1 ponto de evidência e nunca
--                             funde com ninguém"
--   extracao_estadual.py:272 "recusa desde 27/08/2026: registro sem nome"
--   tests/test_processo_fase1.py:83  "o INSERT levantaria CheckViolation"
--
-- ATENÇÃO A QUEM FOR MEXER: esta é uma RECONSTRUÇÃO, e não a cópia do
-- original — que não existe em lugar nenhum. Ela impõe o que o código diz que
-- era imposto. Se aparecer um comportamento que dependia de detalhe não
-- documentado, é aqui que ele vai faltar.
--
-- Rodar como `migrator`.

set local search_path = radar_comercial, public;


-- ─────────────────────────────────────────────────────────────────────
-- O que torna um registro comparável
-- ─────────────────────────────────────────────────────────────────────
--
-- POR QUE RECUSAR NA ENTRADA, e não filtrar na saída.
--
-- Um POI sem nome E sem endereço não é comparável com nada: ele não funde, não
-- casa com o cadastro e não aparece numa busca. Deixá-lo entrar é criar uma
-- linha que só faz peso — e que aparece na contagem, dando ao operador um
-- número que não corresponde a trabalho nenhum.
--
-- É `raise exception` e não `return null` porque descartar em silêncio é pior:
-- a rodada terminaria com menos POIs do que capturou e ninguém saberia onde
-- foram parar. Quem chama sabe tratar — `realtime_ingest._endereco_pela_
-- coordenada` resolve o endereço pela coordenada antes de tentar, e pula o que
-- nem assim obtiver.

create or replace function exigir_comparavel() returns trigger
language plpgsql as $$
begin
  if coalesce(nullif(btrim(new.nome), ''), '') = ''
     and coalesce(nullif(btrim(new.endereco), ''), '') = '' then
    raise exception
      'POI sem nome e sem endereco nao e comparavel com nada: nao funde, nao '
      'casa com o cadastro e nao aparece em busca. Resolva o endereco pela '
      'coordenada antes de inserir, ou pule o ponto.'
      using errcode = 'check_violation';
  end if;
  return new;
end $$;

comment on function exigir_comparavel() is
  'Recusa registro sem nome e sem endereco. RECONSTRUIDO em 31/08/2026 a partir '
  'do que o codigo afirma: o original foi criado a mao no banco antigo e nunca '
  'versionado.';

create trigger poi_comparavel before insert or update on pois
  for each row execute function exigir_comparavel();


-- ─────────────────────────────────────────────────────────────────────
-- O mesmo para o vínculo
-- ─────────────────────────────────────────────────────────────────────
--
-- `vinculo_poi` guarda de qual registro de qual fonte veio cada parte de um
-- POI. Um vínculo sem `fonte` ou sem `id_fonte` não aponta para lugar nenhum:
-- ele não pode ser desfeito nem auditado, e a ficha mostraria uma aba vazia.

create or replace function exigir_vinculo_comparavel() returns trigger
language plpgsql as $$
begin
  if coalesce(nullif(btrim(new.fonte), ''), '') = ''
     or coalesce(nullif(btrim(new.id_fonte::text), ''), '') = '' then
    raise exception
      'vinculo sem fonte ou sem id_fonte nao aponta para lugar nenhum: nao da '
      'para desfazer nem auditar.'
      using errcode = 'check_violation';
  end if;
  return new;
end $$;

create trigger vinculo_comparavel before insert or update on vinculo_poi
  for each row execute function exigir_vinculo_comparavel();
