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

## O que não muda

Os laços de Canoas no cron continuam rodando até terminar. Nenhum script de etapa é reescrito: a validação chama os mesmos `recoletar_fichas`, `recapturar_frente`, `ler_fotos_de_rua`, `fichas_cnpj`, `buscar_web`, `conferir_evidencias` e `avaliar_enxuto --leve` com `--ligacoes-arquivo`.
