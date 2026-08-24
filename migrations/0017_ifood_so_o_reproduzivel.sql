-- 0017 — `ifood_merchant` fica só com o que nossos scripts produzem
--
-- A tabela tinha três procedências misturadas, e isso é pior que ter menos
-- dado: quem consulta não sabe o que é reproduzível.
--
--   1.598  do `home:fallback`, lidas pelo `extrair_ifood.py`      ← fica
--     158  do zip do piloto + restos de execuções quebradas        ← sai
--       1  com endereço, vindo do zip, não de extração nossa       ← campos limpos
--
-- O critério é único: se um script do projeto não sabe produzir aquele valor
-- hoje, ele não fica na tabela. Dado que não se sabe refazer é dado que ninguém
-- pode auditar nem atualizar — e numa base comercial ele acaba virando decisão
-- de campo em cima de coisa que ninguém consegue conferir.

begin;

-- 1 · fora as linhas que não vieram do feed
--
-- `bruto->>'origem'` é carimbado pelo `extrair_ifood.py` em toda linha que ele
-- grava. Ausência de origem = a linha entrou por importação manual.
delete from comercialradar.ifood_merchant
 where coalesce(bruto->>'origem', '') <> 'home:fallback';

-- 2 · zera o que o feed NÃO entrega
--
-- O feed dá: merchant_id, nome, categoria, slug, nota, disponibilidade e a
-- distância ao ponto de busca. Endereço, CEP, coordenada e telefone vivem no
-- `merchant-info/graphql`, que devolve 403 para automação — medido, cinco
-- caminhos testados. O único registro que tinha esses campos veio do zip.
--
-- Quem preenche daqui em diante é o `ler_lojas_no_meu_chrome.py`, lendo o
-- JSON-LD na sessão do próprio usuário, onde a mesma chamada responde 200.
update comercialradar.ifood_merchant set
  rua = null, numero = null, cep = null,
  lat = null, lng = null,
  telefone = null, avaliacoes = null,
  cnpj = null,
  -- PENDENTE quer dizer "ainda não colhi", que é diferente de "não existe".
  -- Os estados OK/BLOQUEADO que havia aqui descreviam a tentativa antiga.
  estado_detalhe = 'PENDENTE';

-- 3 · o bairro fica: ele é derivado do slug, que o feed entrega
--    (o `bairro_do_slug` casa contra a lista conhecida e devolve nulo quando
--     não tem certeza — não é palpite)

commit;
