# Changelog

## 2.2.0

Origem: auditoria PDCA da v2.1.0 (30 achados, 3 bloqueadores). Cada item abaixo
tem um caso em `tests/test_pipeline.py` que **falha na v2.1.0 e passa na v2.2.0**,
verificado por `tests/test_gate_discrimina.py` contra o motor antigo vendorizado
em `scripts/_legacy_v2_1_0.py`.

### Contrato com a fonte CNEFE

- **F-01** piso e classificação de `NV_GEO_COORD`. Não havia filtro nenhum: uma
  coordenada NV=6 (centróide de setor censitário, erro de km) saía como
  `P1_EXATO` / `ALTA` / desvio 0. Agora `--nv-max` (default 3) define o nível de
  endereço, NV ≥ 4 nunca produz `ALTA`, e `--nv-strict` recusa em vez de rebaixar.
  Novas colunas `XFERA_NV_CLASSE` e `XFERA_CONF_MOTIVO`.
- **F-03** o centróide sintético usava NV=4, que no domínio oficial é *face de
  quadra*. Passou a NV=90, fora do domínio 1–6, com check de QA.
- **F-05** `ESP_COMERCIAL` incluía a espécie 7 (edificação em construção) e
  excluía a 8 (estabelecimento religioso). Corrigido para `{3,4,5,6,8}`.
- **F-06** a flag comercial era `espécie OR nome preenchido`, o que fazia qualquer
  condomínio residencial nomeado virar estabelecimento. Separado em `_is_estab`
  (espécie) e `_is_nomeado`.
- **F-07** ingestão de `COD_MUNICIPIO`, `NUM_FACE`, `DSC_MODIFICADOR`,
  `COD_INDICADOR_ESTAB_ENDERECO`. O indicador oficial do IBGE passou a decidir
  `XFERA_ROTA_TRATAMENTO` (reclassificação 1:1 × individualização multi-economia)
  no lugar da contagem bruta de registros, que misturava domicílio com loja.
- **F-08** gate de município: alerta quando o CNEFE tem mais de um município ou
  quando mais de 50% dos CEPs do cadastro não existem nele.

### Precisão do match

- **F-02** `DESVIO_METROS` era a constante 0.0 em 7 dos 8 passes. Agora é medido:
  piso declarado por classe de NV em quadratura com a interpolação
  `delta × metros_por_número`, onde o metros-por-número é medido na própria rua.
  `XFERA_DESVIO_FONTE` expõe a composição. Nenhuma coordenada pode declarar
  incerteza nula — inclusive o centróide, que tem piso de 60 m.
- **F-09** a similaridade usava `max(token_set_ratio, token_sort_ratio)`.
  `token_set_ratio` devolve 100 quando um nome é subconjunto do outro, e o `max`
  garantia que o componente permissivo vencesse: `RUA BRASIL` casava com
  `RUA BRASIL NOVO` com score 100 e margem 50. Agora só `token_sort_ratio`.
  Bateria de 18 casos em `tests/test_similaridade.py`: v2.2 acerta 18, v2.1 acerta 10.
- **F-10** CEP ausente do índice era rotulado `SEM_LOGRADOURO`, o que impedia
  separar *CEP errado no cadastro* de *logradouro não encontrado*. Motivo próprio
  `SEM_CEP_NO_INDICE`.
- **F-11** o sufixo do número era extraído e descartado. Agora entra na chave de
  match e na chave de dedup, junto com o `DSC_MODIFICADOR` do CNEFE.
- **F-12** alias `tipo_logradouro` e `tipo_hint` no lado do cadastro. Bases da RFB
  trazem o tipo em campo separado; sem isso o P1 nunca disparava.
- **F-13** guarda de conteúdo no alias `tipo`: uma coluna `TIPO` com tipos de
  logradouro era lida como matriz/filial. Detectada e remapeada com alerta.
- **F-14** `0`, `00` e `000` tinham tratamentos divergentes. Unificados como
  `SEM_NUMERO_ZERO`, seguindo a convenção do CNEFE e da RFB (zero significa sem
  número), com status próprio e caminho de centróide.
- Correção de precedência do tipo de logradouro: o campo de tipo é autoritativo, e
  a primeira palavra do nome só é removida quando repete o mesmo tipo — antes
  `RUA` + `VILA NOVA` virava `tipo=VILA, base=NOVA`.

### Escala

- **F-15** `build_cnefe_index` varria `best_addr` inteiro dentro do laço de grupos
  (termo `O(G×N)`). Substituído por groupby pré-materializado em dicionário.
- **F-16** as seis passagens `.apply(axis=1)` viraram máscaras booleanas e
  `np.select`; as normalizações caras rodam sobre valores únicos; o match resolve
  o logradouro uma vez por chave distinta. Medido: match 4–5×, regras 9–11×.
- **F-17** o ZIP era carregado inteiro em memória três vezes, e para todo CSV do
  arquivo só para inspecionar o cabeçalho. Agora a inspeção lê 5 linhas e a
  leitura é em streaming com `usecols`. Medido no CNEFE de São Leopoldo:
  pico de 119,5 MB → 10,7 MB.
- **F-18** as listas de `DSC_ESTABELECIMENTO` saíram do DataFrame para um store
  lateral referenciado por id.

### Saída e auditoria

- **F-19** o export não tinha guarda do limite de 1.048.576 linhas do Excel:
  `xlsxwriter.write()` devolve `-1` e ignora em silêncio, e o QA rodava antes do
  export sem reconferir o arquivo. Agora há guarda, fallback para Parquet/CSV e
  releitura do arquivo gravado.
- **F-20** Parquet e CSV gerados por padrão ao lado do xlsx.
- **F-21** `RESUMO_EXECUCAO` passou a gravar os parâmetros do motor
  (`fuzzy_thr`, `fuzzy_margin`, deltas, `parity_penalty`, `nv_max`,
  `existence_threshold`, `accept_invalid_cnpj`).
- **F-22** `sha256` de cada insumo no resumo.
- **F-23** o encoding vencedor da leitura é registrado; antes `latin1` aceitava
  qualquer byte e o mojibake era silencioso.
- **F-24** `validate_output` levantava `KeyError` quando faltava coluna
  obrigatória — morria exatamente no caso que existe para detectar. Agora é FALHA
  de QA com o nome da coluna.
- **F-25** o check de nulos era cego a sentinelas de string (`'nan'`, `'None'`).
- Novos checks: nenhuma `ALTA` sobre NV ≥ 4; toda coordenada com desvio > 0; NV
  sintético fora do domínio oficial.

### Doutrina

- **F-26** CNPJ alfanumérico (IN RFB nº 2.229/2024, em produção desde 31/07/2026).
  `digits()` apagava as letras e todo CNPJ novo virava `TAMANHO_INVALIDO`, saindo
  de `POTENCIAL_CRUZAMENTO` em silêncio. Parser aceita `A-Z`, DV por módulo 11 com
  `ASCII(c) − 48`, e `CNPJ_FORMATO` registra o formato.
- **F-27** ser fuzzy somava +0,5 no score de existência. Match aproximado é
  evidência mais fraca, não adicional: passou a 0. Em compensação, NV ≥ 4 penaliza.
- **F-28 / F-29** capital e data ausentes eram indistinguíveis de capital zero e
  empresa não estabelecida. O score só avalia esses sinais quando o parsing foi
  bem-sucedido, e registra a omissão em `SCORE_ESTRUTURA_NAO_AVALIADO`.
- **F-30** selftest reescrito: 24 casos, um por achado, rodando nos **defaults de
  produção** (a v2.1 usava `fuzzy_thr=88`, diferente do default 90).

### Modo comparativo

- `scripts/comparar_versoes.py` roda os dois motores nos mesmos insumos e emite
  matriz de transição de confiança e de passe, delta de decisão, delta de
  coordenada e o delta linha a linha. A v2.1.0 fica vendorizada em
  `scripts/_legacy_v2_1_0.py` para a comparação ser reprodutível.

### Compatibilidade

Nenhum nome de coluna da v2.1 foi removido. `XFERA_QTD_UNID` permanece como alias
de `XFERA_QTD_REGISTROS_CNEFE`. As seis abas e a ordem das linhas são idênticas.

## 2.1.0

- preflight de esquema e aliases;
- validação de CNPJ, CEP, data, número, CNAE e capital;
- tipo de logradouro preservado;
- fuzzy com margem de unicidade;
- deduplicação determinística pela melhor coordenada;
- centróide robusto e desvio p95 para S/N;
- score multi-evidência implementado;
- corte temporal CNEFE com estado para data ausente;
- seis abas de saída, incluindo QA e base completa;
- testes sintéticos de regressão;
- remoção de scripts com caminhos absolutos.
