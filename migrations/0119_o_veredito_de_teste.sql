-- 0119 · o veredito de TESTE DE DESENVOLVIMENTO, achado sem varrer os vereditos (16/09/2026).
--
-- Dev e produção usam o mesmo banco. Desde a fase 1 da cidade nova (docs/PLANO_CIDADE_NOVA.md), o veredito gravado
-- por processo com `RADAR_AMBIENTE=desenvolvimento` leva `percepcao.ambiente = 'desenvolvimento'`
-- (`avaliar_ligacao.gravar`), e a gestão de produção tira essas ligações dos números a cada consulta
-- (`seek_gestao._de_teste`). O índice parcial guarda só as linhas marcadas — dezenas, contra milhões de vereditos
-- de produção —, e a consulta da gestão não lê a tabela.
--
-- CONCURRENTLY: a tabela é escrita o tempo todo pelo julgamento; o índice comum trava a escrita enquanto nasce.
-- Por isso esta migração roda FORA de transação (psql -c, um comando).
create index concurrently if not exists ligacao_veredito_de_teste
    on radar_comercial.ligacao_veredito (id_empresa, ligacao)
    where percepcao->>'ambiente' = 'desenvolvimento';
