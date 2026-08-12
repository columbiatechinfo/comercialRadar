## O que muda para quem usa

<!-- Uma ou duas frases. Se não muda nada para o usuário, diga isso. -->

## Como foi verificado

<!-- Comando executado, consulta feita, lote rodado, com o NÚMERO.
     "Testei localmente" não é verificação.
     Ex.: "lote de 12 POIs com gpt-4o: 10 aprovados, 0 reprovados no gate". -->

## O que isto quebra sob carga

<!-- A premissa é uso em massa. Se a mudança toca consulta, índice, chamada
     externa ou laço, diga onde ela para de servir e qual é a alternativa ali.
     Se não toca nada disso, escreva "não toca caminho quente". -->

## Banco

- [ ] Não toca o banco
- [ ] Cria ou altera tabela — qual: `______`, e a alteração é idempotente?

<!-- Não há migration versionada: as tabelas nascem por CREATE TABLE IF NOT
     EXISTS no Python. Esta descrição é o único registro da mudança. -->

## Antes de pedir revisão

- [ ] `node scripts/painel.mjs --check` passa
- [ ] Nenhum segredo no diff (gitleaks passou)
- [ ] `docs/estado.json` atualizado se a mudança afeta um requisito do checklist
- [ ] CHANGELOG atualizado se muda comportamento
