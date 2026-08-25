## v3.3.5 — Número Estrito + Complemento CNEFE/RS

- `aj_num_canonico` e `aj_numero_int` passam a conter somente a base numérica.
- `1B` -> número `1`, anotação `B`, complemento derivado `IMOVEL B`.
- A anotação permanece na chave interna de prova; `1A` e `1B` continuam incompatíveis no modo estrito.
- Novas colunas: `aj_num_anotacao`, `aj_num_anotacao_tipo`, `aj_num_complemento_derivado`, `aj_compl_original`, `aj_compl_processado`.
- Taxonomia CNEFE ampliada: `ENTRADA`, `RUA_INTERNA`, `COMODO`, `COBERTURA`, `SUBSOLO`, `PORTARIA`, `SALAO`, `PALAFITA`, além de `IMOVEL` e `MODIFICADOR` sintéticos.
- Hierarquia `ENTRADA -> BLOCO -> APARTAMENTO` e demais ordens existentes permanecem canônicas.
- `SALAO` é elemento próprio e nunca é reduzido a `SALA`.
- Contrato textual explícito: referência não é validada contra POI/mapa/imagem; campos legados ficam `NAO_APLICAVEL_TEXTUAL`/vazios.
- Gate `8j` permanente cobre número estrito e taxonomia CNEFE/RS.

## v3.3.4 — Complement Intelligence

- adiciona `aj_compl_endereco_limpo` como visão exclusivamente cadastral do complemento;
- adiciona `aj_compl_segmentos_json` para consumo `jsonb`/auditoria;
- separa utilidade cadastral de `aj_compl_utilidade_operacional`;
- score de complemento declara `aj_compl_confianca_metodo=HEURISTICA_V1`;
- preserva `aj_compl_referencia_original`;
- mantém `aj_compl_referencia_status`, `aj_compl_referencia_dist_m` e `aj_compl_referencia_fontes` apenas por compatibilidade de schema; nesta skill textual o status fica `NAO_APLICAVEL_TEXTUAL` e distância/fontes permanecem vazias;
- amplia relações para `ACIMA_DE`, `ABAIXO_DE`, `DENTRO_DE`, `DIREITA_DE`, `ESQUERDA_DE`, cruzamentos e relações sequenciais `APOS_N_ELEMENTOS` / `ANTES_N_ELEMENTOS`;
- amplia tipos de referência para número, imóvel, logradouro, cruzamento, infraestrutura, equipamento público, estabelecimento, condomínio, ponto geográfico, característica visual e outro;
- orientação como `SEGUNDA CASA DEPOIS DA IGREJA` é estruturada sem contaminar o endereço;
- colunas de complemento são anexadas ao DataFrame em bloco, eliminando fragmentação do pandas;
- gate `8i` permanente cobre a inteligência nova; toda regressão anterior permanece verde.

## v3.3.3 — Complemento Semântico e Referencial

- separa complemento em endereço real, referência/proximidade, acesso, descrição visual e empreendimento;
- adiciona `aj_compl_natureza_endereco` e `aj_compl_decisao_cadastral`;
- `PROX/EM FRENTE/AO LADO/AO FUNDO/ATRAS/ANTES/DEPOIS` não contaminam o endereço real;
- `DIRECT > REFERENTIAL` permanece obrigatório;
- `CASA 1 CASA 2` e posições contraditórias viram `REVISAR`, sem escolha silenciosa;
- `APTO S/N` não é mais interpretado como `APARTAMENTO S`;
- preserva nome de condomínio/edifício/residencial como evidência separada;
- reconhece aliases conservadores `Q 5` e `L 3`;
- classifica referências por tipo básico (`NUMERO`, `ESCOLA`, `IGREJA`, `MERCADO`, `POSTO`, `PRACA`, `SAUDE`, `FARMACIA`, `OUTRO`);
- adiciona origem/candidatos por componente para auditoria;
- versão interna `3.3.3`; toda a regressão 3.2.x/3.3.x permanece verde.

## v3.3.2 — RS hardening / scope proof / datas compostas / escala

- `VERSION` interno corrigido para `3.3.2`, alinhando manifest/latest com a release real.
- Para `scope_id` municipal IBGE (7 dígitos), cada fonte de aprendizado precisa mapear `colunas.scope_id`; 100% das linhas devem coincidir com o scope declarado.
- Bloqueia contaminação territorial por configuração errada (ex.: dados de Uruguaiana gravados em Canoas).
- Datas com numeral composto são canônicas: `VINTE E QUATRO DE MAIO` -> `24 DE MAIO`, `TRINTA E UM DE MARCO` -> `31 DE MARCO`.
- Hot path de `_scope()` é O(1) para schema 3.3 já migrado; não revarre os demais municípios.
- Gate RS cobre 497 scopes e partições UTM 21S/22S.


## v3.3.1 — adversarial closure

- `scope_id` canônico: identificadores alfabéticos devem estar em MAIÚSCULAS; evita dois territórios por variação de caixa.
- replay sem novo reforço não altera o hash apenas por `last_observed`; decay real continua semântico.
- conflito de autoridades do mesmo nível suspende regra antiga para o mesmo par/contexto.
- autoridade estritamente superior pode substituir direção antiga sustentada por evidência mais fraca, preservando a regra anterior em quarentena.
- normalização converge em uma passagem mesmo com duplicatas iniciais degeneradas (`N(N(x)) = N(x)`).
# Ajuste de Logradouro — v3.3.2

## Objetivo

Primeira release do **Léxico Nacional**. A série 3.2.x fica congelada como baseline de
hardening operacional; a v3.3 muda o modelo semântico do aprendizado nominal.

## Mudanças principais

- `scope_id` obrigatório: equivalência nominal é `(scope_id, contexto, variante) -> canônico`.
- `aj_scope_id` em todas as linhas de saída para empilhamento nacional.
- Migração segura de léxico 3.1/3.2 para 3.3: regras sem escopo ficam em legado e não autoaplicam.
- Decay usa `last_reinforced`; replay da mesma evidência atualiza observação, não rejuvenescimento.
- Máquina de estados reativável: `arquivado -> candidato/ativo` com nova prova independente.
- `autoridade_nivel` hierárquico; empate de autoridades divergentes => `CONFLITO_AUTORIDADE`.
- `INDEFINIDO` simétrico (`token_a`, `token_b`) e resolvível com `resolved_run_id`.
- Proveniência das evidências: fonte, registro, run, posição WGS84 e decisão.
- Normalização reforçada para `N(N(x)) == N(x)` na matriz adversarial.
- Complemento classifica `DIRECT` e `REFERENTIAL`; dado direto sempre vence referência.
- Léxico corrompido falha alto; hardlink do estado e symlink do lock/artefato são recusados.
- Durabilidade do rename do léxico reforçada com `fsync` do diretório pai.

## Compatibilidade

- O vocabulário estrutural curado continua global em `vocabulario_aprendido.json`.
- Léxico 3.2 sem `scope_id` é preservado como legado, mas não é aplicado automaticamente.
- `autoridade: true` continua aceito como alias legado de `autoridade_nivel: 100`, desde que não haja contradição.
- Configuração v3.3 exige `scope_id` explícito.

## Gates de release

- regressão completa 3.2.x: PASS;
- gate nacional v3.3: PASS;
- concorrência multi-scope: 12 processos / 4 scopes / mesmo léxico: PASS;
- cadeia `state_sha256`: PASS;
- zero staging e zero failed no teste concorrente: PASS.
