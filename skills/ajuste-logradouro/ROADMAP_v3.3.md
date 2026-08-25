# Roadmap pós-v3.3.5

A camada textual de endereço está deliberadamente fechada dentro de uma fronteira clara: **esta skill não consulta nem valida POI, imagem, mapa ou qualquer fonte externa durante a execução**. A v3.3.5 incorpora o corte CNEFE/IBGE entre número, modificador e complemento: o número publicado é exclusivamente numérico e toda anotação posterior migra para a camada de complemento sem perder a chave interna de prova.

Próximos saltos, se necessários, devem continuar textuais (ampliação de linguagem de campo, novos elementos comprovados por bases oficiais, calibração empírica de regras) ou trocar apenas o backend de persistência do léxico para volumes nacionais prolongados, sem alterar essa fronteira.

# v3.3.3 — Léxico Nacional + Complemento Semântico / requisitos e gates de release

A v3.2.5 congelou o hardening operacional/filesystem e a cadeia de auditoria semântica. A v3.3.2 implementa a mudança deliberada de MODELO do léxico nacional descrita abaixo. Estes itens agora são requisitos de regressão, não trabalho futuro.

## Escopo obrigatório

1. `scope_id` / `municipio_id` como primeira dimensão da chave do léxico.
   - Estrutural global continua em `vocabulario_aprendido.json`.
   - Equivalência nominal vira `(scope_id, contexto, variante) -> canonical`.
   - Migração 3.2 -> 3.3 preserva legado, mas não reaplica regra sem escopo.

2. Decay por reforço real.
   - separar `last_observed` de `last_reinforced`;
   - reapresentação da mesma âncora atualiza apenas `last_observed`;
   - meia-vida usa `last_reinforced`.

3. Máquina de estados reativável.
   - `candidato -> ativo -> arquivado`;
   - nova evidência independente pode levar `arquivado -> candidato/ativo`.

4. Autoridade hierárquica.
   - `autoridade_nivel`/`prioridade_autoridade` por fonte;
   - níveis diferentes: maior autoridade decide;
   - mesmo nível divergente: `CONFLITO_AUTORIDADE`, nunca frequência.

5. `INDEFINIDO` simétrico e resolvível.
   - chave `min(token_a, token_b)~max(token_a, token_b)`;
   - guardar ambos os tokens, fontes, support, primeiro/último visto;
   - ao resolver: `RESOLVIDO`, canonical e `resolved_run_id`.

6. Proveniência por evidência.
   - source_id, record_id, run_id, posição WGS84 e decisão do par;
   - trilha suficiente para reconstruir por que uma equivalência entrou/ficou ativa.

7. Idempotência forte de normalização.
   - propriedade obrigatória `N(N(x)) == N(x)` em property tests;
   - reorganizar deduplicação de tipo antes do slot de título.

8. Complemento: evidência direta vence referência.
   - cada match classificado `DIRECT` ou `REFERENTIAL`;
   - seleção `DIRECT > REFERENTIAL` independentemente da ordem textual.

## Gate de liberação v3.3

- zero vazamento de regra entre dois `scope_id`;
- decay não renova por replay da mesma evidência;
- regra arquivada reativa com reforço independente;
- conflito de duas autoridades de mesmo nível fica indefinido;
- `INDEFINIDO` fecha quando resolvido;
- normalização passa property test de idempotência em massa;
- complemento é invariável à ordem `PROX CASA 10 CASA 2` vs `CASA 2 PROX CASA 10`.


## Status da release

Todos os gates acima são cobertos por `selftest.py` na v3.3.1. A próxima evolução de escala deve trocar apenas o backend de persistência (JSON -> SQLite/PostgreSQL) sem alterar esta semântica.


## Gate adicional v3.3.2 — Rio Grande do Sul

- município IBGE de 7 dígitos precisa ser comprovado por `colunas.scope_id` em todas as fontes de aprendizado;
- dados de município divergente abortam antes do commit;
- datas compostas convergem para forma numérica canônica;
- base que cruza UTM 21S/22S continua obrigada a particionar;
- acesso a um scope já migrado não pode revarrer os demais scopes do estado.


## Gate adicional v3.3.3 — Complemento Semântico

- endereço real e referência nunca compartilham silenciosamente o mesmo significado;
- números citados em proximidade (`AO LADO DO 125`) nunca viram número/endereço do registro;
- acesso (`ENTRADA LATERAL`, `ACESSO PELO PORTAO 2`) é não-endereço;
- descrição visual (`CASA AMARELA`, `PORTAO PRETO`) é evidência operacional, não componente cadastral;
- conflito de valores do mesmo componente ou posição força `REVISAR`;
- `S/N` em subunidade não produz valor falso;
- empreendimento é preservado sem contaminar o sublocalizador físico.


## Gate adicional v3.3.5 — Número estrito + Complemento CNEFE/RS

- `aj_num_canonico` e `aj_numero_int` carregam somente o inteiro base;
- `1B` -> número `1` + complemento derivado `IMOVEL B`;
- `125-A` -> número `125` + complemento derivado `IMOVEL A`;
- `100 FUNDOS` -> número `100` + complemento `FUNDOS`;
- a chave interna continua distinguindo `1A` de `1B`;
- modificadores retirados do número são preservados em `aj_num_anotacao`, `aj_num_anotacao_tipo` e `aj_num_complemento_derivado`;
- a taxonomia textual de complemento inclui elementos CNEFE/IBGE observados/documentados, incluindo `ENTRADA`, `RUA_INTERNA`, `COMODO`, `COBERTURA`, `SUBSOLO`, `PORTARIA`, `SALAO` e `PALAFITA`;
- a ordem canônica segue o princípio mais abrangente -> mais específico;
- referência/proximidade continua separada do endereço real e **não é validada externamente**.
