# Auditoria: versão recebida × v2.1

## Falhas críticas corrigidas

1. **A melhor coordenada podia ser sobrescrita pela pior.** A versão anterior
   ordenava por NV crescente e construía um dicionário em que a última linha
   vencia. A v2 deduplica explicitamente e mantém a primeira.

2. **Tipo do logradouro era descartado.** Isso permitia casar RUA BRASIL com
   AVENIDA BRASIL. A v2 preserva o tipo e bloqueia conflito.

3. **Fuzzy não verificava ambiguidade.** Agora exige diferença mínima para o
   segundo candidato.

4. **Data de referência estava fixa em 2026-06-01.** Agora é parâmetro de execução.

5. **Datas brasileiras podiam inverter dia e mês.** Agora usam `dayfirst=True`.

6. **Capital com ponto decimal podia ser multiplicado por 100.** O parser agora
   distingue separadores de milhar e decimal.

7. **CNPJ não era validado.** Agora há validação de dígitos verificadores e status.

8. **Centróide S/N podia herdar comércio de número 0.** Agora não atribui evidência
   de endereço específico.

9. **O score multi-evidência estava documentado, mas não implementado.** A v2
   calcula score, faixa, motivos e aplicabilidade temporal.

10. **A saída prometia três ou quatro abas, enquanto o código gerava três.** A v2
    define e gera seis abas consistentes.

11. **A base completa não era entregue.** Agora nenhuma linha é descartada.

12. **Scripts auxiliares tinham caminhos absolutos locais.** A v2 usa um único
    pipeline parametrizado e testes reproduzíveis.

## Mudanças de comportamento intencionais

- proximidade numérica não recebe confiança ALTA;
- CNPJ inválido é sem potencial por padrão;
- capital baixo não muda PRESENCIAL para HÍBRIDO;
- data de abertura ausente torna a evidência CNEFE indeterminada;
- fuzzy ambíguo fica sem match, mesmo que isso reduza cobertura.

Essas mudanças priorizam precisão e rastreabilidade sobre cobertura artificial.
