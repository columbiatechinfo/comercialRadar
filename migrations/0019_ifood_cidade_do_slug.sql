-- 0019 — a cidade da loja passa a ser explícita, lida do slug
--
-- O feed do iFood devolve tudo dentro do RAIO DE ENTREGA do ponto de busca, e
-- esse raio chega a 14,3 km (medido no próprio campo `distance`). Buscando dos
-- bairros de Canoas, voltam lojas de Gravataí, Porto Alegre, Cachoeirinha,
-- Esteio, Sapucaia, Alvorada e São Leopoldo.
--
-- Tratar tudo como "Canoas" produziu três erros encadeados:
--
--   1. o bairro era lido casando o fim do slug contra a lista de bairros de
--      CANOAS — então um "centro" de Gravataí virava o Centro de Canoas;
--   2. o cruzamento com a Receita rodava contra os ativos de Canoas, enquanto
--      metade das lojas não é de Canoas;
--   3. endereços que o Maps devolveu em Porto Alegre foram descartados como
--      "cidade errada" quando provavelmente estavam certos.
--
-- A cidade vem do slug porque o slug é do próprio iFood: `canoas-rs/<loja>` é o
-- que ELE afirma, não o que nós inferimos da distância.

alter table comercialradar.ifood_merchant
  add column if not exists cidade text,
  add column if not exists uf     text;

-- `canoas-rs` → cidade "canoas", uf "RS". O último par de letras após o último
-- hífen é a UF; o resto é o município.
update comercialradar.ifood_merchant
   set cidade = regexp_replace(split_part(slug, '/', 1), '-[a-z]{2}$', ''),
       uf     = upper(right(split_part(slug, '/', 1), 2))
 where slug is not null and slug like '%/%';

-- O bairro que foi atribuído fora de Canoas é palpite contra a lista errada.
-- Apagar é o certo: bairro nulo é honesto, bairro de outra cidade é mentira que
-- o cruzamento por âncora de bairro consumiria sem perceber.
update comercialradar.ifood_merchant
   set bairro = null
 where cidade is distinct from 'canoas';

create index if not exists ix_ifood_cidade
  on comercialradar.ifood_merchant (tenant_id, cidade);

comment on column comercialradar.ifood_merchant.cidade is
  'Município do slug do iFood. O feed cobre o raio de entrega, não o município da busca.';
