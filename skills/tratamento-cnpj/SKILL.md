---
name: tratamento-cnpj
description: >
  Enriquece bases de CNPJ da Receita Federal com coordenadas CNEFE/IBGE,
  validação de endereço, evidência de existência física e classificação de
  potencial comercial. Use para cruzar CNPJ × CNEFE, geocodificar empresas,
  diagnosticar CNPJs sem coordenada, avaliar estrutura física por CNAE, separar
  reclassificação 1:1 de individualização multi-economia, ou gerar uma base
  auditável de potencial de cruzamento. Suporta CNPJ alfanumérico. A execução é
  determinística, preserva 100% das linhas, valida CNPJ/CEP/datas/valores,
  declara a incerteza posicional em metros e bloqueia matches fuzzy ambíguos.
---

# Tratamento CNPJ × CNEFE/IBGE — v2.2

## Objetivo

Produzir uma base confiável para cruzamento comercial e geográfico, mantendo
separados cinco conceitos que não podem ser confundidos:

1. **qualidade cadastral** — CNPJ, CEP, data, CNAE e endereço foram lidos corretamente;
2. **match geográfico** — existe coordenada defensável para o endereço;
3. **precisão da coordenada** — quanto vale, em metros, a coordenada entregue;
4. **evidência CNEFE** — o IBGE registrou estabelecimento ou nome compatível no local;
5. **potencial comercial** — a empresa ativa exerce atividade que pressupõe ponto físico.

Nenhum desses conceitos substitui os demais.

## Princípio da v2.2

> O pipeline declara o que não sabe.

Nenhum rótulo de alta confiança é emitido sobre premissa não verificada. Onde a
v2.1 assumia em silêncio — o nível da coordenada, a espécie do endereço, a
similaridade do logradouro, a integridade da gravação — a v2.2 confere, expõe e,
quando não sabe, diz que não sabe.

## Execução padrão

```bash
python scripts/pipeline_cnpj_ibge.py \
  --cnpj BASE_CNPJ.xlsx \
  --ibge MUNICIPIO_CNEFE.zip \
  --out RESULTADO_CNPJ_IBGE.xlsx \
  --data-referencia 2026-08-06
```

A data de referência deve ser informada em execuções oficiais. O resultado é
reprodutível para os mesmos arquivos e parâmetros — e o `RESUMO_EXECUCAO` grava
os parâmetros e o `sha256` dos insumos para provar isso.

Dependências:

```bash
pip install pandas numpy rapidfuzz openpyxl xlsxwriter pyarrow
```

### Comparativo com a v2.1

```bash
python scripts/comparar_versoes.py \
  --cnpj BASE_CNPJ.xlsx --ibge MUNICIPIO_CNEFE.zip \
  --out COMPARATIVO.xlsx --data-referencia 2026-08-06
```

Roda os dois motores nos mesmos insumos e emite o delta linha a linha: matriz de
transição de confiança e de passe, registros que mudaram de decisão, registros
que ganharam, perderam ou moveram a coordenada. Custa ~2× o tempo de uma execução.
Use ao promover a v2.2 numa base que já rodou na v2.1.

## Contrato de execução obrigatório

### 1. Fazer preflight antes do match

- identificar aliases de colunas sem depender de maiúsculas, acentos ou espaços;
- falhar quando duas colunas diferentes disputarem o mesmo campo canônico;
- falhar quando faltar uma coluna obrigatória;
- registrar status de parsing para CNPJ, CEP, logradouro, número, data e capital;
- manter `_RID` único para provar que nenhuma linha foi perdida ou duplicada;
- selecionar o CSV correto dentro do ZIP pelo esquema **lido no cabeçalho**, nunca
  carregando o arquivo inteiro para inspecionar;
- registrar o encoding vencedor e o `sha256` de cada insumo.

Nunca inventar coluna ausente, exceto campos opcionais explicitamente definidos
com valor vazio.

### 2. Preservar a semântica do endereço

O tipo do logradouro é preservado separadamente:

- `LOGR_TIPO`, `LOGR_BASE`, `LOGR_FULL`, `LOGR_NUCLEO`.

**O campo de tipo é autoritativo.** Quando a base traz o tipo em coluna própria
(`tipo_logradouro` no cadastro, `NOM_TIPO_SEGLOGR` no CNEFE), a primeira palavra
do nome só é removida se repetir o mesmo tipo. Do contrário `RUA` + `VILA NOVA`
viraria `tipo=VILA, base=NOVA` e deixaria de casar com `RUA VILA NOVA`.

**Não tratar RUA BRASIL e AVENIDA BRASIL como o mesmo logradouro.** Quando o nome
é igual e há mais de um tipo no mesmo CEP, o resultado é ambíguo e fica sem match.

### 3. Nunca usar CEP isolado como coordenada

O CEP restringe o universo de candidatos, mas não resolve endereço sozinho. Todo
match exige logradouro e, para coordenada individual, número válido.

Quando o CEP do cadastro não existe no índice CNEFE, o motivo é
`SEM_CEP_NO_INDICE` — nunca `SEM_LOGRADOURO`. Separar as duas causas é o que
permite o PDCA de cobertura: CEP errado e logradouro não encontrado exigem ações
diferentes.

### 4. Bloquear fuzzy ambíguo

A similaridade usa **apenas `token_sort_ratio`**. `token_set_ratio` está proibido:
devolve 100 quando um nome é subconjunto do outro (`BRASIL` × `BRASIL NOVO`), e
nenhum limiar barra um score máximo com margem larga.

O fuzzy só é aceito quando:

- o melhor score alcança `--fuzzy-thr`;
- a diferença para o segundo colocado alcança `--fuzzy-margin`;
- o logradouro não está em conflito explícito de tipo.

A bateria de `tests/test_similaridade.py` fixa a separação: typos legítimos ficam
acima de 94, truncamentos abaixo de 78.

### 5. Escolher a melhor coordenada, não a última linha

Para duplicidades no mesmo endereço, ordenar por coordenada válida, menor
`NV_GEO_COORD` e código de endereço estável; depois usar a primeira linha.

A chave de endereço inclui o **sufixo/modificador do número** (`123`, `123-A`,
`123 FUNDOS` e o `DSC_MODIFICADOR` do CNEFE são endereços distintos).

### 6. Separar precisão do match e precisão da coordenada

- `XFERA_MATCH_CONF`: qualidade da associação CNPJ × endereço;
- `XFERA_NV_GEO` / `XFERA_NV_CLASSE`: qualidade declarada da coordenada CNEFE;
- `XFERA_DESVIO_METROS` / `XFERA_DESVIO_FONTE`: incerteza posicional estimada;
- `XFERA_CONF_MOTIVO`: por que a confiança foi rebaixada, quando foi;
- `XFERA_LOGR_SCORE` e `XFERA_LOGR_SCORE_SEGUNDO`: evidência do fuzzy.

### 7. Nunca declarar incerteza nula

Toda coordenada entregue carrega `XFERA_DESVIO_METROS > 0`. Há check de QA que
falha a execução se alguma não carregar.

## Nível da coordenada (`NV_GEO_COORD`)

Domínio oficial do `Dicionario_CNEFE_Censo_2022.xls`:

| NV | Classe | Significado |
|---|---|---|
| 1 | `ENDERECO_ORIGINAL` | coordenada original do Censo 2022 |
| 2 | `ENDERECO_MODIFICADO` | apartamentos num mesmo número no logradouro |
| 3 | `ENDERECO_ESTIMADO` | sem coordenada original ou coordenada inválida |
| 4 | `FACE_QUADRA` | face de quadra |
| 5 | `LOCALIDADE` | localidade |
| 6 | `SETOR_CENSITARIO` | setor censitário |
| **90** | `SINTETICO_LOGRADOURO` | **centróide calculado pela A2L, fora do domínio IBGE** |

Regras duras:

- `--nv-max` (default 3) define o que conta como nível de endereço;
- **NV ≥ 4 nunca produz `MATCH_CONF = ALTA`**, qualquer que seja o passe;
- `--nv-strict` recusa a coordenada em vez de rebaixar a confiança;
- o centróide sintético usa **NV = 90**, nunca 4 — 4 é face de quadra do IBGE, e
  uma mediana de logradouro não é isso.

### Incerteza posicional

```text
DESVIO_METROS = hypot(piso_do_NV, delta_numerico × metros_por_numero_da_rua)
```

O `metros_por_numero` é medido na própria rua (mediana das razões
distância/delta entre números consecutivos, com clamp em 0,5–60 m). O piso por
classe de NV é declarado, não medido: 8 · 15 · 40 · 60 · 250 · 800 m para NV 1–6.
O centróide S/N usa o p95 das distâncias ao centróide, com piso de 60 m — nunca
melhor que uma face de quadra.

`XFERA_DESVIO_FONTE` diz de onde veio: `PISO_NV`, `PISO_NV+INTERPOLACAO` ou
`CENTROIDE_P95`.

## Cascata de match

| Passo | Regra | Confiança (antes do teto por NV) |
|---|---|---|
| P1 | tipo + logradouro + número (e sufixo) exatos | ALTA |
| P2 | logradouro sem tipo, único no CEP + número exato | MEDIA |
| P3 | logradouro exato + número próximo | MEDIA |
| P4 | logradouro fuzzy único + número exato | MEDIA |
| P5 | logradouro fuzzy único + número próximo | MEDIA |
| P6 | logradouro exato + número em faixa ampla | BAIXA |
| P7 | logradouro fuzzy + número em faixa ampla | BAIXA |
| P8 | S/N + centróide robusto do logradouro | BAIXA |

Critérios de desempate numérico: menor `delta + penalidade_de_paridade`; menor
delta bruto; mesma paridade; melhor NV; menor número.

Defaults: `delta_proximo=20`, `delta_amplo=100`, `penalidade_paridade=3`,
`fuzzy_threshold=90`, `fuzzy_margin=5`, `nv_max=3`.

## Leitura e normalização dos dados

### CNPJ — numérico e alfanumérico

Desde 31/07/2026 a RFB emite CNPJ alfanumérico (IN RFB nº 2.229/2024): as 12
primeiras posições aceitam `0-9` e `A-Z`, os dois DV continuam numéricos, e o
módulo 11 usa `ASCII(c) − 48` (`A=17`, `B=18`, …).

- remover máscara e artefato `.0` de Excel, preservando letras;
- recuperar CNPJ com 13 caracteres por `zfill(14)` somente se o DV validar;
- rejeitar sequência repetida e DV não numérico;
- preservar `CNPJ_STATUS` e `CNPJ_FORMATO`.

Por padrão, CNPJ inválido não entra em `POTENCIAL_CRUZAMENTO`. A exceção exige
`--accept-invalid-cnpj` e fica registrada no resumo.

### CEP

8 dígitos; 7 dígitos completam com zero à esquerda e marcam `AJUSTADO_ZFILL`;
demais casos ficam inválidos, sem correção silenciosa.

### Número

- aceitar número, sufixo e faixa simples: `123`, `123A`, `123-B`, `123/125`;
- preservar o sufixo **e usá-lo na chave de match**;
- `S/N` e variantes textuais → `SEM_NUMERO`;
- `0`, `00` e `000` → `SEM_NUMERO_ZERO` (mesma convenção do CNEFE e da RFB),
  tratados como S/N com status próprio;
- nunca fazer `0 == 0` como match de número.

### Datas

`dayfirst=True`; serial Excel plausível; `AAAAMMDD` da RFB; data ausente ou
inválida não torna o CNEFE automaticamente aplicável; idade calculada com
`--data-referencia`.

### Capital social

`R$ 1.000,50` → `1000.50`; `1.000` → `1000` em base brasileira; valor inválido →
zero apenas com `CAPITAL_STATUS=INVALIDO_ASSUMIDO_ZERO`.

**Capital ausente não é capital zero.** O score estrutural só avalia o capital
quando `CAPITAL_STATUS=VALIDO`, e registra a omissão em
`SCORE_ESTRUTURA_NAO_AVALIADO`. Mesma regra para data de abertura ausente.

### CNAE e natureza jurídica

CNAE reduzido a 7 dígitos e formatado para auditoria; natureza jurídica comparada
pelos dígitos; regras por divisão CNAE têm confiança menor que exceções por CNAE
completo.

## Espécie do endereço e perfil do imóvel

Domínio oficial de `COD_ESPECIE`:

```text
1 domicílio particular   2 domicílio coletivo   3 estabelecimento agropecuário
4 estabelecimento de ensino   5 estabelecimento de saúde
6 estabelecimento de outras finalidades
7 edificação em construção ou reforma   8 estabelecimento religioso
```

- **estabelecimento = {3, 4, 5, 6, 8}**. O 7 é obra, não estabelecimento. O 8 é
  estabelecimento e conta.
- a flag **não** pode ser `espécie OR nome preenchido`: um condomínio residencial
  nomeado (espécie 2 com `DSC_ESTABELECIMENTO`) não é estabelecimento.
- `XFERA_N_ESTAB` e `XFERA_N_DOMICILIO` são contagens separadas.
- `XFERA_QTD_REGISTROS_CNEFE` conta **registros CNEFE**, incluindo domicílios.
  **Não é contagem de estabelecimentos** e não deve ser usada como tal.

### Rota de tratamento

`COD_INDICADOR_ESTAB_ENDERECO` do IBGE (`1` único, `2` até 10, `3` mais de 10,
`4` quantidade desconhecida) decide `XFERA_ROTA_TRATAMENTO`:

| Indicador | `XFERA_PERFIL_ENDERECO` | `XFERA_ROTA_TRATAMENTO` |
|---|---|---|
| 1 | `ESTAB_UNICO` | `RECLASSIFICACAO_1_1` |
| 2 / 3 / 4 | `ESTAB_MULTIPLO_*` | `INDIVIDUALIZACAO_MULTI` |
| ausente, com estabelecimento | `ESTAB_SEM_INDICADOR` | por contagem de estabelecimento |
| só domicílio | `SOMENTE_DOMICILIO` | `VERIFICAR_CAMPO` |

**A rota nunca vem da contagem bruta de registros.** Uma torre residencial com
uma loja no térreo tem centenas de registros CNEFE e indicador `1`: é
reclassificação direta, não individualização.

## Evidência de existência física

Comparação de nome entre `nome_fantasia`/`razao_social` e os
`DSC_ESTABELECIMENTO` do endereço exato.

- nome confirmado no CNEFE: +6
- nome provável: +3
- estabelecimento no endereço: +4
- situação ativa: +1
- match ALTA/MEDIA: +1
- coordenada nível de endereço (NV 1–2): +1
- coordenada de baixa precisão (NV ≥ 4): −1
- centróide S/N: −1
- proximidade ampla: −1

Ser fuzzy **não soma**: match aproximado é evidência mais fraca, não evidência
adicional.

Faixas: 9–15 `EXISTE_QUASE_CERTO`; 6–8,5 `EXISTE_PROVAVEL`; 4–5,5
`EXISTE_POSSIVEL`; 0,5–3,5 `EXISTE_INCERTO`; 0 `SEM_EVIDENCIA`.

### Corte temporal CNEFE

Default `2022-08-01`, configurável por `--cnefe-cutoff`. Abertura após o corte →
`NAO_POS_CNEFE`; data ausente → `INDETERMINADO_DATA_AUSENTE`. Nesses casos a
coordenada continua válida, mas a ausência de nome ou estabelecimento no CNEFE
não pode ser usada contra a empresa.

## Decisão de potencial

`POTENCIAL_CRUZAMENTO=True` exige CNPJ válido (salvo exceção explícita), situação
`ATIVA` e perfil comercial compatível com ponto físico. A existência de coordenada
não é requisito — empresas com ponto físico e sem coordenada ficam
`PENDENTE_OUTRA_FONTE`.

Complemento residencial rebaixa o perfil, salvo CNAE classificado como exigência
operacional de estrutura, auditável por `COMPLEMENTO_TIPO`,
`CNAE_EXIGE_ESTRUTURA` e `PERFIL_MOTIVO`.

## Gate de município

Sem `COD_MUNICIPIO` no CNEFE não há como separar municípios — o índice é por CEP.
A execução alerta quando o CNEFE traz mais de um município, e quando mais de 50%
dos CEPs do cadastro não existem no CNEFE fornecido.
`--gate-municipio-estrito` transforma o alerta em falha.

## Saída obrigatória

Workbook com seis abas: `RESUMO_EXECUCAO`, `QA_ACEITE`, `DICIONARIO`,
`POTENCIAL_CRUZAMENTO`, `SEM_POTENCIAL`, `BASE_COMPLETA`.

Além do xlsx, `--formatos` gera **Parquet e CSV** por padrão — é o contrato de
handoff para as skills a jusante e a fonte de verdade quando a base é grande.

`RESUMO_EXECUCAO` grava os parâmetros do motor, o `sha256` e o encoding de cada
insumo, a distribuição de `NV_CLASSE` e de `XFERA_ROTA_TRATAMENTO`, a mediana e o
p95 do desvio, e todos os alertas.

### Limite físico do Excel

Excel comporta 1.048.576 linhas por aba. Acima disso o xlsx sai apenas com
`RESUMO_EXECUCAO`, `QA_ACEITE`, `DICIONARIO` e um `LEIA_ME`, e os dados vão em
Parquet/CSV — nunca truncamento silencioso. Abaixo do limite, o arquivo gravado é
**relido e reconferido** contra a contagem esperada.

### Compatibilidade com a v2.1

Todos os nomes de coluna da v2.1 foram preservados. `XFERA_QTD_UNID` continua
existindo como alias de `XFERA_QTD_REGISTROS_CNEFE` para não quebrar consumidores.
Colunas novas entram ao lado.

## Checklist de aceite

A execução oficial falha se qualquer item não for atendido:

- quantidade de linhas da saída igual à entrada, **reconferida no arquivo gravado**;
- `_RID` único e em ordem original;
- colunas essenciais presentes e sem nulos (inclusive sentinelas de string);
- toda coordenada matchada dentro dos limites geográficos válidos;
- passes exatos com delta zero;
- centróide somente para número S/N;
- nenhum potencial com situação diferente de ATIVA;
- **nenhuma `MATCH_CONF=ALTA` sobre coordenada com NV ≥ 4**;
- **toda coordenada com desvio declarado maior que zero**;
- **NV sintético fora do domínio oficial 1–6**;
- nenhum erro silencioso de esquema.

Testes obrigatórios antes de publicar uma alteração:

```bash
python tests/test_pipeline.py         # 24 casos, nos defaults de produção
python tests/test_gate_discrimina.py  # prova que os casos falham na v2.1
python tests/test_similaridade.py     # bateria de logradouro, typo x truncamento
```

Um caso que passa nas duas versões não prova correção nenhuma. Toda correção nova
entra com o par: falha na versão anterior, passa na nova.

## Limitações declaradas

- CNEFE não representa empresas abertas depois da coleta de 2022;
- endereço fiscal não prova operação comercial;
- CNAE por divisão é heurística e carrega confiança menor;
- o piso de incerteza por classe de NV é **declarado por ordem de grandeza**, não
  medido em campo — só o componente de interpolação é medido;
- numeração rural pode exigir parâmetros próprios;
- CEP incorreto não é corrigido automaticamente por serviço externo;
- match fuzzy ambíguo é recusado para preservar precisão;
- o índice é por CEP: sem `COD_MUNICIPIO` no CNEFE, colisão de CEP entre
  municípios não é detectável;
- `NUM_FACE` é ingerido e exposto, mas ainda não é usado como critério de match.

## Referências internas

- `references/contrato-dados.md`
- `references/matching-confianca.md`
- `references/decisao-saida.md`
- `references/auditoria-v1-v2.md`
- `references/auditoria-v21-v22.md`
