# Cidade nova ponta a ponta pela tela

Decisões do dono do produto em 16/09/2026, depois da revisão que mostrou que a tela só fazia extração e vínculo e que todo o resto rodava por cron fixo em Canoas.

## Decisões

- **Qualificação** (SIM / SIM_COM_ANALISE_HUMANA / NÃO) vem como **coluna na base do cliente**: a declaração de colunas passa a pedir esse papel.
- **Várias cidades em paralelo**, com **teto global** de navegadores, proxies e chamadas à Spark.
- **Notebook soma ao i9** nos processos **sem IA**; tudo que envolve IA é na **Spark**.
- **Visual da SEEK junto**, casca primeiro; cores do mapa só depois de mostrar alternativas.
- **Tudo em desenvolvimento** e testado em :8446; produção só com aprovação.
- **Teste**: a menor cidade da base (Chuvisca, 285 ligações), só ~4 quadras desenhadas, dados reais; vereditos marcados como teste de dev e escondidos da SEEK de produção.

## O limite de dev e produção

Dev e produção usam o mesmo banco `a2l` e a mesma fila `radar_comercial.job`. O worker de produção pega qualquer tipo de job, então nada novo entra nessa fila durante o desenvolvimento: a validação ganha **tabelas próprias com `ambiente` em cada tarefa**, e cada worker só pega as do seu ambiente. O código de produção atual não conhece essas tabelas.

## Fases

| Fase | O que entra | Onde |
|---|---|---|
| 1 · Base e isolamento | Migração `validacao` + `validacao_tarefa` (etapa × lote, `ambiente`, `precisa_ia`, estado, worker, `visto_em`, tentativas, erro). Veredito de teste com `percepcao.ambiente = 'dev'`; SEEK e gestão de produção passam a esconder. Papel `qualificacao` na declaração da base. | migrations, seek_api, seek_gestao, materializar_base |
| 2 · Extração completa por área | Receita/CNPJ e CNEFE como registros da área dentro do `minerar_tudo`; ao terminar, a extração cria a validação da área. | minerar_tudo |
| 3 · Validação por tarefas | Lotes da área (qualificação + vínculo), na ordem iFood → Maps → resto e SIM antes de SIM com análise. Etapas sem IA: fichas, fotos para o storage, foto de rua, Serasa, busca web, conferência. Com IA: leitura das placas e julgamento leve. O worker ganha um segundo laço para as tarefas, com filtro por ambiente e por "sem IA", e um teto global de chamadas à Spark. Nome de contêiner por tarefa; nenhuma cidade fixa. | validacao.py, minerador_worker |
| 4 · Telas no visual da SEEK | Tokens e componentes compartilhados gerados pelo `montar.py`; casca da extração (filete, logo, topo com chips); painel de validação com andamento por etapa e lote; "Avaliar com IA" vira a validação da área. | frontend |
| 5 · Teste em dev e publicação | Chuvisca, 4 quadras: extração → vínculo → validação → SEEK de dev. Galeria com prints e números. Publicar reconstrói o worker e sincroniza o notebook a partir da tag. | dev → aprovação → produção |

## Andamento em desenvolvimento (16/09/2026)

| Fase | Estado | Onde |
|---|---|---|
| 1 | feita: migrações 0118 e 0119 aplicadas; `gravar` marca `ambiente`; SEEK e gestão de produção escondem o teste (prova em transação desfeita, 12/12); papel `qualificacao` na base | `f494ecf` |
| 2 | feita: Receita e CNEFE viram ponto na etapa 2; a extração cria a validação ao terminar | `3a5dd9c` |
| 3 | feita: `validacao.py` (lotes, dependências, tetos), `validacao_executor.py` (um por máquina, contêiner destacado por tarefa), compose com proxy do docker; o teto de conexões conta as sessões reais do banco | `3a5dd9c`, `df610b2` |
| 4 | casca feita: `/validacao` no visual da SEEK; extração com filete, logo e azul da marca; "Avaliar com IA" cria a validação; `sessao.js` não sobrescreve mais os tokens da SEEK | `df610b2`, `8ebf8a7` |
| 5 | feito em dev: Chuvisca, ~4 quadras, 18 vínculos, validação #1 com 4 ligações e 8 tarefas em 5 min 10 s, 4 revisão humana, 0 na SEEK de produção; qualificação revertida. Galeria: https://claude.ai/artifact/9Y9tqaP3rEvjHCGi9QvraP | `producao-2026-09-16.6` |
| produção | pronto para o dono do produto disparar pela tela: `producao-2026-09-16.7` (API), worker da fila reconstruído da tag, executor de produção no i9 (todas as etapas) e no notebook (fotos e conferência); revisão de vínculos só na cidade; índice da Receita por município agendado para 23:30 de 16/09 | `producao-2026-09-16.7` |

### Achados no caminho

- **Receita sem índice.** `resources_root.rf_estabelecimentos` tem 72,8 milhões de linhas (14 GB) e nenhum índice: cada cidade nova varre a tabela inteira no mesmo Postgres que atende a SEEK. Onde quebra: várias cidades em paralelo são várias varreduras de 14 GB. Saída: índice por `municipio` (e situação), criado `concurrently`, com decisão do dono.
- **iFood e Airbnb resolvem o desafio da Cloudflare** (etapas 5 e 6 da extração). As etapas da validação só detectam o bloqueio. Testes de desenvolvimento rodam com `--pular-ifood --pular-airbnb`; a decisão sobre produção é do dono.
- **Pooler no limite.** Com os laços de Canoas eram 17 a 19 sessões de 20. O executor mede as sessões reais antes de soltar uma tarefa (`RADAR_POOL_TOTAL`, `RADAR_POOL_FOLGA`).
- **783 ligações aptas de Canoas com POI vinculado nunca entraram na fila de julgamento**: a fila antiga exigia imagem antes; a validação não exige (a foto de rua é uma etapa).

## O que não muda

Os laços de Canoas no cron continuam rodando até terminar. Nenhum script de etapa é reescrito: a validação chama os mesmos `recoletar_fichas`, `recapturar_frente`, `ler_fotos_de_rua`, `fichas_cnpj`, `buscar_web`, `conferir_evidencias` e `avaliar_enxuto --leve` com `--ligacoes-arquivo`.
