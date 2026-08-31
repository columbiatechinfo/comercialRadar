-- 0006_chaves_naturais.sql — os índices que todo `ON CONFLICT` exige.
--
-- COMO ISTO FOI DESCOBERTO, e por que de uma vez só.
--
-- A primeira tentativa de salvar uma área de teste morreu com
-- `InvalidColumnReference: there is no unique or exclusion constraint matching
-- the ON CONFLICT specification`. A mensagem diz o sintoma e não diz qual
-- índice falta.
--
-- Em vez de consertar aquele e esperar o próximo, levantei TODO `ON CONFLICT`
-- do código (`docs/chaves_on_conflict.json`, 16 chaves) e cruzei com os índices
-- únicos que existem. **Doze não tinham.** Consertar um por um custaria uma
-- rodada por chave — e cada uma só aparece quando alguém executa aquela etapa.
--
-- DE ONDE VEIO O BURACO. É a mesma história do `poi_comparavel`: essas chaves
-- existiam no banco antigo, criadas à mão, e nunca entraram em migração
-- nenhuma. O `git` tinha o código que as usa e não tinha o que as cria.
--
-- DUAS DELAS SÃO ARMADILHA MAIS FINA. `atribuicao` e `vinculo_poi` JÁ têm
-- índice único sobre as colunas certas — mas PARCIAL (`where status =
-- 'pendente'`, `where estado = 'vinculado'`). Índice parcial não serve a
-- `ON CONFLICT` a menos que o comando repita a mesma cláusula. Então eles
-- existem, cobrem as colunas, e o INSERT é recusado assim mesmo — o tipo de
-- coisa que faz perder uma tarde comparando nomes de coluna.
--
-- Rodar como `migrator`.

set local search_path = radar_comercial, public;


-- ─────────────────────────────────────────────────────────────────────
-- Uma área por nome, dentro da empresa
-- ─────────────────────────────────────────────────────────────────────
-- Com `(nome)` sozinho, a segunda empresa a desenhar "Canoas" sobrescreveria a
-- área da primeira — que ela nem enxerga, porque a RLS esconde a linha mas a
-- unicidade vale sobre a tabela inteira.
create unique index if not exists ux_area_empresa_nome
  on area_trabalho (id_empresa, nome);


-- ─────────────────────────────────────────────────────────────────────
-- Um POI não é atribuído duas vezes ao mesmo supervisor
-- ─────────────────────────────────────────────────────────────────────
-- O `atribuicao_unica` do 0001 é PARCIAL (só as pendentes) e por isso não
-- sustenta o `ON CONFLICT` do `server.py`. Este é total: reatribuir um POI
-- resolvido ao mesmo supervisor atualiza a linha em vez de criar a segunda.
create unique index if not exists ux_atribuicao_poi_supervisor
  on atribuicao (poi_id, supervisor_id);


-- ─────────────────────────────────────────────────────────────────────
-- A ligação do cliente
-- ─────────────────────────────────────────────────────────────────────
-- `num_ligacao` é a identidade da ligação no cadastro da concessionária, e
-- `cliente` distingue duas concessionárias que usem a mesma numeração. Sem
-- isto, reimportar a planilha duplicaria as 102 mil linhas em vez de atualizar.
create unique index if not exists ux_cadastro_cliente_ligacao
  on cadastro_cliente (cliente, num_ligacao);


-- ─────────────────────────────────────────────────────────────────────
-- Cadastur
-- ─────────────────────────────────────────────────────────────────────
-- `linha_origem` é o número da linha no arquivo do MTur: reprocessar o mesmo
-- recurso atualiza, não duplica.
create unique index if not exists ux_cadastur_prestador_linha
  on cadastur_prestador (recurso_id, linha_origem);

-- Totais são por (dataset, UF, município, período) — reimportar o mesmo
-- período substitui o número, em vez de somar duas vezes.
create unique index if not exists ux_cadastur_total_pf
  on cadastur_total_pf (dataset, uf, municipio, ref_periodo);


-- ─────────────────────────────────────────────────────────────────────
-- Catálogo de campos da ficha
-- ─────────────────────────────────────────────────────────────────────
create unique index if not exists ux_campo_catalogo
  on campo_catalogo (id_empresa, fonte, chave);


-- ─────────────────────────────────────────────────────────────────────
-- CNPJ tratado
-- ─────────────────────────────────────────────────────────────────────
-- Por empresa: o mesmo CNPJ pode ser tratado por dois clientes, e cada um tem o
-- seu veredito.
create unique index if not exists ux_cnpj_tratado_empresa
  on cnpj_tratado (id_empresa, cnpj);


-- ─────────────────────────────────────────────────────────────────────
-- Fachada e iFood: um por POI, um por loja
-- ─────────────────────────────────────────────────────────────────────
create unique index if not exists ux_fachada_poi
  on fachada_anotacao (poi_id);

create unique index if not exists ux_ifood_merchant
  on ifood_merchant (merchant_id);


-- ─────────────────────────────────────────────────────────────────────
-- Vínculo: total, e não só o ativo
-- ─────────────────────────────────────────────────────────────────────
-- O `vinculo_sem_duplicata` do 0001 é PARCIAL (`where estado = 'vinculado'`) e
-- guarda outra regra: um registro de fonte compõe UM poi por vez. Este aqui é a
-- identidade da LINHA — o mesmo registro, do mesmo poi, não entra duas vezes
-- nem depois de desvinculado. São regras diferentes e os dois índices convivem.
create unique index if not exists ux_vinculo_linha
  on vinculo_poi (fonte, id_fonte, poi_id);


-- As duas chaves que faltam — `endereco_segmentado` e `logradouro_ajustado` —
-- moram em `resources_root` e vao no 0007: aquelas tabelas pertencem a
-- `resources_loader`, e `migrator` nao cria indice em tabela alheia. Dono
-- diferente, migracao diferente, como ja e entre o 0001 e o 0002.
