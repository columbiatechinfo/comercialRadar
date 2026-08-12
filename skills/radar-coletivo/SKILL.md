---
name: tratamento-imoveis-ibge
description: >
  Radar Coletivo — descoberta de candidatos em bases de enderecos e domicilios (CNEFE/IBGE
  2022): normalizacao de logradouro e complemento em 7 camadas, sanidade geoespacial, anomalia
  de coleta, coletivas por bloco/torre, declarado x contado, polos comerciais (DBSCAN),
  RADAR_ID estavel. Emite o RCC (Relatorio Canonico de Coletividade): grao UNIDADE, 104 campos,
  proveniencia campo a campo, 19 gates executaveis. Toda hipotese nasce GRADUADA
  (SUSTENTADA/PLAUSIVEL/ESPECULATIVA) e nenhuma e descartada. Bloco SET traz ocupacao por setor
  censitario (domicilios vagos e de uso ocasional). CEP nao decide quando a coordenada
  confirma. Portatil: municipio, UF ou multi-UF. Acione para: tratar CNEFE, base IBGE, RCC,
  relatorio de coletividade, classificar coletiva, polo comercial, quadra lote, fachada ativa,
  uso misto, anomalia de coleta, gate de qualidade, imovel desocupado, domicilio vago, vacancia
  por setor, hipotese graduada, amostra de campo.
---

# Radar Coletivo — Tratamento de Bases IBGE v4.11.0

**POSICIONAMENTO HONESTO (v4.5, pós-revisão externa):** este é um **motor de
DESCOBERTA DE CANDIDATOS** com gates de integridade executáveis — não um
motor de decisão comprovada. Fato observado e hipótese inferida nunca se
misturam; toda exclusão é contabilizada no manifest de linhagem; e decisão
comercial exige a camada L6 (validação externa: cadastro da concessionária,
ligações, medidores, vistoria). Heurísticas (anomalia de coleta, faixas de
confiabilidade, gaps) são declaradas como heurísticas até calibração contra
verdade externa (amostra rotulada, precision/recall por classe — ver M6).

Modelo PADRÃO e PORTÁTIL que transforma qualquer arquivo bruto do IBGE — um
município ou uma UF do layout CNEFE 2022 — em banco estruturado de
endereços e atividades econômicas georreferenciadas, com confiabilidade como
gate e granularidade por bloco/torre/unidade. **Não há obrigatoriedade de
cidade:** a sanidade geoespacial opera POR PARTIÇÃO de município (bbox próprio
por COD_MUNICIPIO); anomalia de coleta e polos usam chaves naturalmente locais (CHAVE de
endereço, COD_SETOR, raio DBSCAN em metros), nunca a base como extensão única.
Execução 100% local (file-based): Pandas + openpyxl. DuckDB/Polars/Parquet =
roadmap para escala estadual (ver Tiering).

**Escala — o que é suportado, o que é experimental:**

```
Tier 1  MUNICÍPIO      SUPORTADO   — POA 762 mil registros, ciclo completo selado
Tier 2  UF             EXPERIMENTAL — roda, mas sem benchmark; xlsxwriter em
                                     constant-memory ainda é roadmap e há
                                     `apply(axis=1)` no caminho da confiança
Tier 3  MULTI-UF / BR  ROADMAP      — depende de Polars/DuckDB/Parquet
```

Canoas/RS (176.899 reg., ~30s) e Santa Maria/RS (149.483 reg., 19s) são as
FIXTURES DE REGRESSÃO (golden tests). **ATENÇÃO v4.3:** a CHAVE de agrupamento
passou a usar o logradouro HARMONIZADO (clusters antes fragmentados por typo se
fundem) — os números-fixture precisam de RE-BASELINE na próxima rodada real
antes de voltarem a travar regressão.

**Escopo:** apenas arquivos IBGE. Cadastro concessionária → skill `cadastro-adequacao`.

**Scripts:**
- `radar_pipeline.py` — **NÚCLEO ÚNICO (v4.5.2, X1)**: `preparar_base(input, ...)` executa a ordem canônica (load tipado → schema → dedup exato → harmonização → sanidade geo → complementos → Q/L → uso → enrich → RADAR_ID → declarado×contado → anomalia S1-S5 → F7 → coletivas → gate) e devolve `(df, stats)`; `gate_universal(df)` e `gate_conf(df)` são as máscaras canônicas. TODO entrypoint consome daqui; writer nunca recalcula regra de negócio.
- `radar_potenciais.py` — **runner canônico (6 abas + manifest de linhagem)**: consome `preparar_base` e monta Coletivas + Cond_Horizontais + Nao_Residenciais + Polos_Comerciais + Hipoteses_Expansao + Dicionario. `python radar_potenciais.py --input <base.csv> --output <xlsx>`
- `radar_utils.py` — biblioteca (normalização, sanidade geo, anomalia de coleta, confiabilidade, condomínio horizontal, polos, RADAR_ID, declarado×contado, dicionário, gates fail-closed)
- `cnefe_coletivas.py` — load tipado + writer Excel legado/diagnóstico (não-canônico; consome `preparar_base`)
- `cnefe_mapa.py` + `map_template.html` — mapa Leaflet interativo (não-canônico; consome `preparar_base`)
- `cnefe_download.py` — download FTP IBGE + enriquecimento
- `rcc_emissor.py` — **EMISSOR RCC (v4.6)**: base canônica de COLETIVIDADE no
  grão UNIDADE, 104 colunas, só com IBGE.
  `python rcc_emissor.py --input <base.csv> --output <rcc.csv> [--store <ids.csv>]`
  (acha o dicionário sozinho em `RCC_dicionario.csv` na raiz da skill)
- `rcc_dicionario_gerador.py` — gera `RCC_dicionario.csv`. Documento e base saem
  do MESMO gerador: não podem divergir. Campo sem `ORIGEM_DADO` ABORTA a geração.
- `tests/test_rcc.py` — 32 testes do emissor, METADE NEGATIVOS (provam que cada
  gate aborta). `python tests/test_rcc.py`
- `tests/gen_sintetico.py` + `tests/test_unit.py` — smoke sintético (2 municípios, casos de borda: CEP com zero à esquerda, Q/L, anomalia S1-S3, ESQ/DIR, dupla TERREO/ANDAR, dupes) e 17 testes unitários dos invariantes (sanidade 3 camadas, R7, gate negativo + python -O, schema, nunique, S2 por unidades distintas, dedup-primeiro, S1-nulos, DBSCAN sem peso, F7 fail-closed, RADAR_ID, declarado×contado, harmonização, paridade preparar_base×runner). Rodar após qualquer mudança: `python tests/gen_sintetico.py && python radar_potenciais.py --input tests/base_sintetica.csv --output /tmp/pot.xlsx && python tests/test_unit.py`

---

## REGRAS INVIOLÁVEIS

```
R1 — NUMERO=0 NÃO EXISTE PARA CRUZAMENTO
  fillna(0) na ingestão. NUM=0 excluído de oportunidade, tripla validação,
  delta, agrupamento. df_zero separado ANTES do groupby. 0==0 não é match.
  Padrão Q/L/C usa chave composta, nunca NRO=0.

R2 — ANOMALIA DE COLETA EXCLUÍDA ANTES DE QUALQUER OPORTUNIDADE
  v4.5: o motor NÃO acusa "fraude" — detecta ANOMALIA DE COLETA (sequência
  perfeita demais, coordenada compartilhada, repetição em massa). Fraude
  exige validação externa; o algoritmo sinaliza padrão atípico. Ordem
  canônica (v4.5.2, radar_pipeline.preparar_base — a MESMA para todo
  entrypoint): dedupe EXATO na INGESTÃO (W2) → enrich (S2/S3 dependem de
  CHAVE/UNIDADE_VALOR) → F0.5 → F7 → gate → abas. NENHUM registro com
  FLAG_ANOMALIA_COLETA=1 passa do GATE. Decomposição (v4.5.1, W5):
  N_SINAIS_PRIMARIOS conta só evidências diretas S1/S2/S3; S4 é contexto
  (FLAG_CONTEXTO_S4) e S5 é reforço (FLAG_REFORCO_S5) — os sinais NÃO são
  independentes entre si. Graduação futura (1 observação / 2 quarentena /
  3 exclusão) a calibrar contra o golden real ANTES de mudar thresholds.

R3 — CONFIABILIDADE É GATE, NÃO DECORAÇÃO
  ALTA exige NV 1-2 + tipo forte. NV 4-6 NÃO entra em oportunidade.

R4 — DELTA CONTRA O ENDEREÇO, NÃO CONTRA A LIGAÇÃO
  DELTA_REAL = FONTE_TOTAL - SOMA_ECON_TOTAL (todas lig. do endereço)

R5 — NEUTRALIDADE DE FONTE (não é anonimização — v4.5.1)
  Zero referência a "CNEFE"/"IBGE"/marca legada "XFERA" em RÓTULOS e
  METADADOS de output (abas, colunas, descrições, títulos, arquivos);
  sempre "Radar Coletivo" e prefixo RADAR_. O gate aborta em resquício.
  NÃO é anonimização de dados pessoais: endereço, coordenada e nome de
  estabelecimento permanecem no produto. A LINHAGEM técnica (fonte, hash,
  parâmetros, funil) é PRESERVADA no manifest .json — remover marca da
  apresentação nunca destrói proveniência. Texto livre do recenseamento
  é DADO, não referência.

R6 — DEDUPE EXATO NA INGESTÃO; NUNCA POR COORDENADA OU PARCIAL
  v4.5.1 (W2): o dedupe por HASH COMPLETO da linha roda na INGESTÃO —
  linha idêntica NÃO é evidência independente e, rodando depois, uma
  duplicata FABRICAVA sinal de anomalia (14 unidades + 1 dup = 15 linhas
  → flagrava S2). O que a regra sempre proibiu continua proibido: dedupe
  por COORDENADA (coordenada repetida com logradouros distintos É o sinal
  S1) e dedupe parcial por COD_UNICO_ENDERECO (pode repetir legitimamente
  entre economias do mesmo endereço).

R8 — IDENTIFICADOR NUNCA TRANSITA POR REPRESENTAÇÃO INEXATA (v4.5.3)
  Origem: PDCA-01. `pd.DataFrame(list_of_dicts)` misturando id de 19 dígitos
  com None infere float64 e ARREDONDA acima de 2^53 — sem exceção, sem
  warning, preservando unicidade e destruindo o JOIN. É defeito de
  REPRESENTAÇÃO: qualquer etapa que reintroduza o float reintroduz o bug.
  PROIBIDO   float32/float64/np.floating; to_numeric() sem dtype seguro;
             célula numérica de Excel; round-trip por JSON number.
  PERMITIDO  INTERNO Int64 nullable (o id é 63 bits: cabe exato) ou str;
             EXTERNO UTF-8 STRING sempre — CSV, Excel, Parquet, Power BI e
             o pandas do consumidor recriam o defeito se receberem número.
  Verificação: `varredura_ids()` no gate, sobre TODOS os frames de saída,
  por PADRÃO de nome (RADAR_ID*, *_ID, ID_*) — nunca lista branca, senão a
  próxima coluna nova escapa. Valor é verificado por `verificar_id_
  recomputavel()` (recalcula do RADAR_CHAVE_CANONICA) e pela integridade
  referencial hipótese→endereço-pai. NÃO se julga valor por heurística:
  um id corrompido é um inteiro válido, a informação perdida não está nele.

R13 — ARTEFATO NÃO É ENTREGA ATÉ SER RELIDO E LACRADO (v4.9)
  DataFrame aprovado NÃO implica arquivo aprovado. Todo produto canônico é
  escrito em área temporária, reaberto pelo PARSER DO PRÓPRIO FORMATO,
  reconciliado contra o modelo, validado em três níveis — REPRESENTAÇÃO,
  SEMÂNTICA e DERIVAÇÃO — e só então publicado por rename atômico.
  `GATES OK` NÃO AUTORIZA ENTREGA. Só QUALITY_SEAL ∈ {SEALED,
  CORRECTED_AND_SEALED} autoriza; em modo exploratório,
  SEALED_WITH_WARNINGS. QUARANTINED e FAILED nunca saem.
  Prova de necessidade: o D15 (100,00% dos 279.297 identificadores acima de
  2^53 arredondados no artefato de POA) passou por 19 gates e 49 testes,
  porque todos olhavam a representação e o valor já estava errado.
  REPARÁVEL   ordem de coluna, sufixo .0 em id, encoding, ordenação — UMA
              tentativa, seguida de revalidação COMPLETA.
  NUNCA REPARÁVEL  classificação, atividade, coletividade, natureza da
              linha, contagem, polo, grau, destino de campo → QUARENTENA.
              "Corrigir até passar" é o oposto de qualidade.

R14 — AUDITOR NÃO CONFIA NO PRODUTOR (v4.9)
  O validador crítico RECALCULA o invariante por caminho independente da
  função que gerou o campo. Se o auditor chamar o produtor, os dois
  concordam no mesmo erro e o rigor vira teatro.
  `qualidade_artefato.py` NÃO importa radar_utils, rcc_emissor nem
  ibge_setor_ocupacao — e o teste QA-23 varre o arquivo para garantir.
  Recalculados de forma independente hoje: contagem por grupo
  (COL_QTD_OBSERVADA/INFERIDA), constância dos atributos de grupo,
  fechamento aritmético do bloco SET, domínios fechados e hash por linha.

R9 — TODA HIPÓTESE NASCE GRADUADA, E NENHUMA É DESCARTADA (v4.7)
  Critério ÚNICO e verificável: por quantos lados a OBSERVAÇÃO cerca a
  hipótese. Dois lados → SUSTENTADA (vai a campo). Um lado e perto →
  PLAUSIVEL (amostra dirigida). Extrapolação livre → ESPECULATIVA (fica na
  base, não sai para rota).
  POR QUE NÃO DESCARTAR: um filtro que apaga deixa um quadrante VAZIO na
  matriz de confusão. O campo só consegue devolver "fui lá e não existe"
  (falso positivo) e nunca "a régua matou e existia" (falso negativo) — e
  sem esse quadrante o limiar NUNCA calibra. Graduar preserva a medição;
  filtrar a destrói.
  CONSEQUÊNCIA OPERACIONAL: erro visível ao analista custa minutos; erro
  visível ao técnico custa a adoção do produto. Por isso o corte é
  conservador — a régua erra por omissão, e falso negativo a próxima safra
  recupera.
  `EVD_DISTANCIA` é gravada para que recalibrar o corte depois seja um
  UPDATE sobre a coluna, não uma nova safra.
  PROIBIDO  gerar hipótese sem EVD_GRAU (G16); editar ACT_DESTINO_CAMPO à
            mão (G17: ele DERIVA do grau); gravar EVD_DISTANCIA fora de
            C_EXTRAPOLADA (G18); somar qualquer grau em contagem — a
            exclusão de agregados vale para os três igualmente.

R10 — ATRIBUTO DE SETOR NUNCA VIRA NÚMERO DE IMÓVEL (v4.7)
  O bloco SET traz ocupação de domicílios do agregado por setor censitário
  (V0007/V0008/V0009). É atributo do SETOR. Multiplicar
  `SET_TX_DESOCUPACAO` pelas unidades da coletiva para "estimar quantas
  estão vagas" produz um número que PARECE dado e é palpite.
  A taxa qualifica o setor; ela não sabe qual apartamento está vazio.
  Gate G19 é executável e varre por padrão de nome (VAGO/DESOCUP/OCIOS
  fora do prefixo SET_), além de exigir o fechamento
  ocupados+ocasional+vagos = particulares.

R7 — CEP NÃO É DECISÓRIO QUANDO A COORDENADA CONFIRMA (v4.3)
  CEP é entrada DIGITADA — sujeita a erro de quem preencheu. Papel do CEP:
  harmonizar GRAFIA de logradouro (moda por CEP+TIPO) e exibição. Nunca:
  chave de agrupamento, match de cruzamento ou penalização de confiabilidade
  quando NV 1-2 + sanidade OK (a coordenada SUPRE o CEP: COORD_SUPRE_CEP).
```


---

## Correções v4.1 (validação Santa Maria/RS)

```
C1 — promover_quadra_lote: CASA capturada SEM exigir LOTE
  Padrão Q+C (QUADRA 48 + CASA 22) é comum; v4.0 perdia a casa.
  LOTE e CASA disputam o slot MORADIA — o perdedor é recuperado
  dos pares brutos. Validado: 917/1.066 chaves Q/L com casa.

C2 — detectar_fraude: 100% VETORIZADO
  Loop por groupby estourava memória em bases >100K (OOM kill).
  Sinal 2 via agg vetorizado (count/nunique/min/max por CHAVE);
  Sinal 3 via transform('count'). 149K registros: 2.1s.

C3 — detectar_polos_comerciais: filtro SETORES_POLO
  DOMICILIO_COLETIVO e AGROPECUARIO excluídos — campus universitário
  gerava polo falso de 505 estabelecimentos.

C4 — Polos: CLASSE_POLO por porte
  ZONA_CENTRAL (≥500 end.) | POLO_REGIONAL (≥50) | POLO_LOCAL (<50)
  O encadeamento DBSCAN no centro é legítimo mas precisa de rótulo próprio.

C5 — Merge de PK: cast str obrigatório nos DOIS lados
  COD_UNICO_ENDERECO como str em qualquer merge (pickle pode tipar int64).
```

---

## Mudanças v4.2 — modelo padrão portátil (STATUS REAL v4.3)

Cada item abaixo carrega o status verificado em auditoria de código. Itens
`ROADMAP` são direção de projeto, NÃO comportamento atual — não presuma que
existem ao operar a skill.

```
M1 — PARTIÇÃO É A UNIDADE, NÃO A BASE          [IMPLEMENTADO (sanidade) v4.3]
  sanidade_geo(df, pct, particao='COD_MUNICIPIO'): bbox percentil POR
  MUNICÍPIO (1 partição → idêntico ao global). Fraude e polos operam por
  chaves naturalmente locais (CHAVE, COD_SETOR, eps em metros) — partição
  explícita neles e partição espacial sem código = ROADMAP.

M2 — GATE EXECUTÁVEL (pós-condições que ABORTAM o export)   [IMPLEMENTADO v4.3]
  xu.gate_pos_condicoes(coletivas, cond_horiz, nres, polos, par_faltante,
  df_base, dicionario, nomes_abas) roda ANTES de qualquer to_excel.
  Violação de R1/R2/R3/R5/EIXO1 → AssertionError, nada é emitido.

M3 — PERFIL DE REGIME → THRESHOLDS AUTOMÁTICOS               [ROADMAP]
  perfil_regime/eps por regime NÃO existem. O que JÁ é automático: a exceção
  Q/L de S2 (seq_min=30/cobertura=0.98) está embutida em detectar_fraude.
  Overrides manuais: --fraude-seq-min/--fraude-cobertura/--dbscan-eps.

M4 — CONTRATO DE SCHEMA + GUARDA DE COERÇÃO      [PARCIAL v4.3]
  Implementado: validar_schema (colunas obrigatórias ABORTAM na ingestão) +
  tipagem str de CEP/COD_SETOR/NUM_ENDERECO/COD_UNICO_ENDERECO no load
  (CEP int64 perdia zero à esquerda — 0xxxx/SP). Teto percentual de
  coerção→null por coluna (--coercao-max) = ROADMAP.

M5 — DETERMINISMO                                 [PARCIAL]
  Determinismo funcional: mesma entrada → mesmos valores (groupby ordena
  chaves; mode() desempata alfabético). Sort estável explícito pré-DBSCAN e
  byte-a-byte de xlsx (openpyxl grava timestamp) = ROADMAP.

M6 — CALIBRAÇÃO OBSERVÁVEL DA CONFIABILIDADE      [ROADMAP]
  Curva por faixa e gate de DRIFT não implementados (exigem cross-ref).

M7 — HONESTIDADE EM BASE RALA (LOW-GEO)           [ROADMAP]
  FLAG_LOW_GEO_BASE e HARM_ORIGEM/fallback por token não implementados.
  Nota: com R7, CEP ausente/raro já não penaliza registro com coordenada
  confirmada — o dano de base rala de CEP caiu.
```

## Correções v4.3 — auditoria de código + doutrina CEP

```
N1 — LAYER_MAP FONTE ÚNICA (radar_utils)
  A cópia em cnefe_coletivas havia divergido: perdia ESQ/DIR/MEIO/LATERAL
  da camada POSICAO — gap_posicional DIR×ESQ era código morto no runner.
  Agora cnefe_coletivas importa LAYER_MAP/CAMADAS de radar_utils.

N2 — CHAVE DE AGRUPAMENTO HARMONIZADA E SEM CEP (R7)
  CHAVE = NOM_SEGLOGR_HARM|NUMERO|LOCALIDADE (antes: logradouro BRUTO —
  typo fragmentava cluster e derrubava QTD_END). CEP fora da chave: entrada
  digitada não decide agrupamento. EXIGE re-baseline das fixtures.
  Pré-requisito descoberto no smoke: harmonização passou a rodar por
  CEP+TIPO+NOME_NORMALIZADO — a moda por CEP+TIPO puro colapsava todos os
  logradouros de cidade de CEP único no nome modal e fundiria ruas na CHAVE.

N3 — R7 NA CONFIABILIDADE: COORD_SUPRE_CEP
  EIXO2: CEP 8 dígitos +7; senão, NV 1-2 + sanidade OK ganha os MESMOS +7
  (fator COORD_SUPRE_CEP). Registro confirmado em campo nunca perde ponto
  por dígito de CEP.

N4 — R5 SANEADO NOS OUTPUTS
  'indicador IBGE'/'IBGE confirma' removidos de TIPO_DESC/CONF_DESC (iam
  parar na coluna TIPO da aba Coletivas). Entrypoint legado: título
  'Radar Coletivo — Município …' e arquivo default RADAR_*.xlsx.

N5 — DEDUP HASH COMPLETO PÓS-F0.5 + RE-ENRICH (R6)
  drop_duplicates() de linha inteira após F0.5; se removeu algo, QTD_END/
  N_BLOCOS são recalculados (enriquecer é idempotente).

N6 — CNAE CURADO: POSTO/PET/LAVA
  POSTO DE SAUDE→SAUDE; AUTO POSTO/POSTO exact_or_prefix→combustível
  (antes 'PET' capturava 'POSTO PETROBRAS' e 'TAPETE'); PET restrito a
  PET SHOP/PETSHOP/exact_or_prefix; LAVANDERIA própria antes do lava-rápido
  (antes 'LAVA' capturava lavanderia).

N7 — DUPLA_END REFINADO (checklist cumprido)
  2 registros + split de PAVIMENTO (TERREO/ANDAR) + coordenada VALIDADA →
  MEDIA (era BAIXA). POSICAO+VALIDADA já subia via HORIZ_SUBDIV.

N8 — COND_HORIZ_LOTES POR nunique
  Gate de 3+ lotes passa a contar lotes ÚNICOS (count duplicado não fabrica
  condomínio); confiança ALTA também por nunique>=5.

N9 — MAPA RESPEITA T0/F0.5
  cnefe_mapa harmoniza, roda sanidade por partição + fraude e EXCLUI
  geo insana/fraude do payload (antes o mapa exibia tudo).

N10 — Não_Residencial ganha FLAG_USO_MISTO por endereço (set-based, via
  transform sobre CHAVE) — a aba agora bate com a especificação.

N11 — SANIDADE DE REGIME DUPLO (partição pequena)
  Percentil só em partição com n >= max(20, 1/pct); abaixo disso, erro
  grosseiro por haversine à mediana (>300 km). Percentil em n pequeno
  recortava os extremos LEGÍTIMOS (8/10 numa cidade de 10 registros).

N12 — NUM_ENDERECO COAGIDO NA INGESTÃO (todos os fluxos)
  load_cnefe converte NUM str→int (nulo→0) na fonte — runner, coletivas
  standalone e mapa recebem o mesmo contrato; utils fazem coerção
  defensiva no ponto de uso (R1 vale fora do runner também).

N13 — RADAR_ID_ENDERECO / RADAR_ID_BLOCO (chave numérica de rastreamento)
  id64 = BLAKE2b-63bits da chave canônica em caracteres padrão
  (MUN|TIPO+NOME norm|N<num> ou QL <chave>|LOCALIDADE norm). Estável entre
  execuções e safras; presente nas abas Coletivas, Condominios_Horizontais
  e Nao_Residencial; PAR_FALTANTE herda o id do pai; gate verifica
  injetividade (colisão aborta); TEXTO no Excel, BIGINT em banco.

N14 — CHAVE OPERACIONAL COM PREFIXO DE MUNICÍPIO
  CHAVE = MUN|LOGR_HARM|NUM|LOCALIDADE — sem o prefixo, endereços iguais
  de cidades diferentes colidiam no groupby em run multi-município
  (falsa coletiva interestadual). Run mono-município: idêntico ao anterior.
```

## Anotações decisórias da fonte — inventário de campos (N16)

Campos do layout oficial 2022 que carregam ANOTAÇÃO DIRETA do recenseador
sobre multiplicidade/agrupamento — estado real de uso no pipeline:

| Campo | Domínio | Uso atual | Valor decisório |
|---|---|---|---|
| `COD_INDICADOR_ESTAB_ENDERECO` | 1 Único; 2 Múltiplo ≤10; 3 Múltiplo >10; 4 Múltiplo qtd desconhecida | S4 anomalia + tipologia MULT_ESTAB (só q=1); **v4.4: decodificado nas abas** (`DSC_MULT_ESTAB` no Não-Residencial, `MULT_ESTAB_DECL` pior-caso do bloco em Coletivas) | Percepção DECLARADA de estabelecimentos agrupados — confirma porte de coletiva comercial sem inferência; ind 2-4 com QTD_END=1 denuncia sub-enumeração |
| `COD_INDICADOR_CONST_ENDERECO` | 1 Única; 2-4 Múltipla | **v4.4: decodificado** (`DSC_MULT_CONST`) | Construções agrupadas em obra (espécie 7) = economias FUTURAS no mesmo lote |
| `COD_INDICADOR_FINALIDADE_CONST` | 1 Residencial; 2 Não-res; 3 Misto; 4 Indet. | **v4.4: decodificado** (`DSC_FINALIDADE_CONST`) | Pipeline de crescimento: obra multi+residencial/mista = futura coletiva; hoje espécie 7 é só excluída |
| `NV_GEO_COORD = 2` | "Endereço MODIFICADO (aptos com mesmo número)" | Ramo `nv==2 → VERT_APTO ALTA` | A semântica oficial FUNDAMENTA o ramo: NV2 é anotação de verticalização (aptos no mesmo número), não só nível de GPS — a auditoria v4.3 o marcara como agressivo; retificado |
| `NUM_QUADRA` + `NUM_FACE` | quadra/face de quadra do setor | NUM_FACE só no SCORE_NUMERACAO N4 (não integrado) | Contiguidade FÍSICA: mesma (SETOR,QUADRA,FACE) = vizinhos reais → valida cluster, desambigua localidade ruim, dá identidade a SEM_NUMERO — ROADMAP v4.5 |
| `DSC_MODIFICADOR` | SN, KM… | ignorado | Separa "sem número DECLARADO" (SN) de número ausente por falha — refina R1 — ROADMAP |
| `COD_DISTRITO`/`COD_SUBDISTRITO` | subdivisões | ignorado | Partição intermediária p/ município grande — ROADMAP |
| Situação do setor (malha Censo 2022, cruzamento externo) | urbano/rural 1-8 | ignorado | Alimenta `perfil_regime` (M3) com dado oficial em vez de heurística de densidade — ROADMAP |

**Doutrina N16 — ESTRUTURA DECIDE, DECLARAÇÃO VALIDA.** A declaração da
fonte NUNCA reclassifica: ela confronta. `VALIDACAO_ESTAB_DECL` compara a
faixa declarada (1→1; 2→2..10; 3→≥11; 4→≥2) com `QTD_ESTAB_CONTADA`
(contagem estrutural por CHAVE): CONFIRMADO reforça a leitura;
DUVIDA_SUB_ENUMERACAO = o campo viu mais do que a base registrou (alvo de
verificação, nunca descarte); DUVIDA_SUPER_CONTAGEM = declaração
curta/defasada; SEM_DECLARACAO = indicador ausente. Presente em
Nao_Residencial (registro) e Coletivas GRANDE/PEQUENA (endereço).

## Mudanças v4.4 — rebrand XFERA → radar_coletivo

```
N15 — REBRAND COMPLETO (breaking rename de contrato, lógica INALTERADA)
  Marca de exibição: "Mineração Xfera" -> "Radar Coletivo".
  Módulos:  xfera_utils.py -> radar_utils.py;
            xfera_potenciais.py -> radar_potenciais.py.
  Arquivos default: RADAR_<base>_COLETIVAS.xlsx, RADAR_MAPA_<base>.html.
  Guard: 'xfera' entrou na regex proibida do gate (R5) — resquício de
  rename em qualquer output ABORTA o export.

  DE -> PARA (colunas de output; consumidores fazem RENAME, não re-match):
    XFERA_ID_ENDERECO        -> RADAR_ID_ENDERECO
    XFERA_ID_BLOCO           -> RADAR_ID_BLOCO
    XFERA_CHAVE_CANONICA     -> RADAR_CHAVE_CANONICA
    XFERA_CONF_COORD         -> RADAR_CONF_COORD (+ _DESC)
    XFERA_CONF_ENDERECO      -> RADAR_CONF_ENDERECO (+ _FATORES)
    XFERA_CONF_CONTEXTO      -> RADAR_CONF_CONTEXTO (+ _FATORES)
    XFERA_CONF_LOCALIZACAO   -> RADAR_CONF_LOCALIZACAO
    XFERA_CONF_FAIXA         -> RADAR_CONF_FAIXA

  GARANTIA DE RASTREAMENTO: o VALOR de RADAR_ID_ENDERECO/BLOCO é IDÊNTICO
  ao do XFERA_ID_* da v4.3 — o hash é da chave canônica (município,
  logradouro, número/QL, localidade), sem marca. Bases emitidas antes e
  depois do rebrand continuam casando id a id; só o nome da coluna muda.

N16 — DECLARAÇÃO DO RECENSEADOR COMO CHAVE DE VALIDAÇÃO OU DÚVIDA
  Doutrina (decisão de projeto): ESTRUTURA DECIDE, DECLARAÇÃO VALIDA. A
  classificação estrutural permanece a autoridade; os indicadores oficiais
  de multiplicidade entram como confronto declarado×contado por endereço
  via confrontar_declaracao_estab():
    DSC_MULT_ESTAB / DSC_MULT_CONST / DSC_FINALIDADE_CONST (decodificados)
    QTD_ESTAB_CONTADA (contagem estrutural, espécies 3/4/5/6/8 por CHAVE)
    VALIDACAO_ESTAB_DECL ∈ CONFIRMADO | DUVIDA_SUB_ENUMERACAO |
                           DUVIDA_SUPER_CONTAGEM | SEM_DECLARACAO
  Faixas declaradas: 1→exato 1; 2→2..10; 3→>=11; 4→>=2.
  Zero interferência em classificação/gate/score — é auditoria e
  priorização: DUVIDA_SUB_ENUMERACAO = o campo viu mais do que a base
  registrou (alvo de verificação, nunca descarte).
  Retificação: NV_GEO_COORD=2 = "aptos com mesmo número" (semântica oficial)
  — o ramo VERT_APTO/ALTA por NV2 é fundamentado na fonte.
```

Tudo acima endurece a máquina ao redor das REGRAS INVIOLÁVEIS — R1–R6 intactas,
R7 (CEP não-decisório) adicionada como inviolável nova.

---

## Correções v4.5 — revisão metodológica externa

Origem: auditoria independente do pacote (código + execução). Diagnóstico
aceito: "a engenharia de regras está acima da média, mas o sistema prometia
mais confiabilidade metodológica do que provava". Aplicado:

```
V1 — GATES IMUNES A `python -O`                                      [P0]
  Todos os gates de produção (validar_schema, gate_pos_condicoes, R5,
  colisão de id) trocaram `assert` por RadarQualityError (RuntimeError).
  Testado sob `python -O` no caso 12 da suíte.

V2 — SANIDADE GEO EM 3 CAMADAS; PERCENTIL NUNCA MAIS EXCLUI          [P0]
  Um quantil não identifica erro — define extremos (os ~600 ERRO_GEO de
  Santa Maria eram ≈ os 4×0,1% de cauda por construção). Agora:
  determinístico (nulo, fora do bbox Brasil, (0,0), lat/lon trocadas) +
  grosseiro (>300 km da mediana da partição) = ERRO_GEOMETRIA;
  cauda percentil = FLAG_OUTLIER_GEO informativa (nunca gate).
  ROADMAP: camada territorial ST_Covers na malha municipal + buffer.

V3 — "CONFIABILIDADE É GATE" AGORA É LITERAL                         [P0]
  F7 roda ANTES do gate; Nao_Residencial exige RADAR_CONF_FAIXA ALTA+
  (verificado no gate) e os polos só clusterizam endereços ALTA+.
  Dimensões nomeadas separadamente: CONF_TIPOLOGIA (coletivas),
  RADAR_CONF_* (localização), CONF_INFERENCIA (hipóteses).

V4 — FRAUDE → ANOMALIA DE COLETA + FIX S1-NULOS                      [P0]
  Rename integral (algoritmo não acusa fraude); BUG corrigido: coords
  nulas caíam todas no hash 'nan|nan' e se flagravam mutuamente no S1;
  N_SINAIS_ANOMALIA para graduação futura. THRESHOLDS CONGELADOS até o
  re-baseline real — recalibrar no escuro repetiria o erro criticado.

V5 — DBSCAN SEM sample_weight                                        [P0]
  O peso participava do critério de core point: 1 endereço com 5 estab.
  virava "polo" sozinho, violando min_samples=endereços. Clustering por
  densidade PURA de endereços; QTD_ESTAB caracteriza o cluster depois.
  Testado: 1 endereço/5 estab → 0 polos; 5 endereços → 1 polo.

V6 — HIPÓTESE NUNCA SE FANTASIA DE FATO                              [P0]
  PAR_FALTANTE saiu de Coletivas → aba própria Hipoteses_Expansao com
  TIPO_HIPOTESE, EVIDENCIA, REGRA_ORIGEM, CONF_INFERENCIA,
  REQUER_CONFIRMACAO=SIM (verificado no gate). Nunca somar com observado.

V7 — SCHEMA SEMÂNTICO EM COLETIVAS                                   [P1]
  QTD → QTD_ECONOMIAS (grandeza única e agregável); CONF →
  CONF_TIPOLOGIA; STATUS removido (aba é 100% observada).

V8 — CONDOMÍNIO HORIZONTAL SEM EXTRAPOLAR EVIDÊNCIA                  [P1]
  "provável hidrômetro por casa" → SITUACAO_MEDICAO=DESCONHECIDA +
  ACAO_RECOMENDADA=CRUZAR_COM_CADASTRO_COMERCIAL (estrutura física não
  prova situação hidrométrica; 6 casas/1 ligação vs 6/6 decide o valor).

V9 — R7 RESTAURADA NO GAP Q/L                                        [P1]
  groupby(CEP, LOGRADOURO) → groupby(COD_SETOR, LOGRADOURO): o setor
  censitário é identidade espacial oficial; CEP voltou a ser só descritivo.

V10 — MANIFEST DE LINHAGEM (R5 ≠ destruir proveniência)              [P1]
  R5 remove a MARCA da apresentação comercial; a linhagem técnica é
  preservada em sidecar <output>.manifest.json: fonte, vintage, SHA-256
  do input, parâmetros, versão do pipeline, timestamp e FUNIL COMPLETO
  (entrada → dedup → anomalia → geo → gate → saídas). Fora do scan R5
  por design.

V11 — ENTRYPOINT LEGADO GATEADO                                      [P1]
  cnefe_coletivas.py standalone aplica sanidade+anomalia (fim da
  "verdade paralela" sem gates). Consolidação total em radar_pipeline
  .run() + writers puros = ROADMAP v5.

V12 — RADAR_ID REPOSICIONADO                                         [P1]
  É id determinístico da REPRESENTAÇÃO CANÔNICA — não identidade física
  permanente (rename de rua/localidade muda o id). RADAR_ENTITY_ID
  persistente com entity resolution = skill ferramenta-logradouro-padrao
  / ROADMAP. Aviso gravado no manifest.

V13 — DEPENDÊNCIAS PINADAS                                           [P2]
  scripts/requirements.txt com faixas de major travadas.

CONGELADO ATÉ O GOLDEN REAL (decisão explícita, não omissão):
  thresholds de anomalia (S1-S5), pesos 40/30/30 da confiabilidade e a
  graduação observação/quarentena/exclusão — a própria revisão põe o
  re-baseline como pré-requisito de mudança estrutural. Próximo passo
  metodológico: amostra rotulada 500-1.000 endereços estratificada +
  precision/recall/FPR/FNR por classe (M6).
```

## Correções v4.5.1 — 2ª revisão externa (bugs determinísticos + governança)

```
W1 — S2 POR UNIDADES DISTINTAS (bug objetivo)          [teste 13]
  Threshold aplicado a nun (nunique), não a n (linhas): 8 unidades ×
  2 registros = 16 linhas NÃO é "16 unidades em sequência".

W2 — DEDUP EXATO MOVIDO PARA A INGESTÃO (bug objetivo) [teste 13]
  Antes: enrich → anomalia → dedup — uma duplicata exata fabricava S2
  antes de ser removida. Agora: F1 → dedup → estatísticas. R6 reescrita.

W3 — F7 FAIL-CLOSED                                    [teste 14]
  Coluna RADAR_CONF_FAIXA ausente em Nao_Residencial → gate ABORTA
  (antes: passe livre silencioso). detectar_polos_comerciais idem
  (exigir_conf=True default; biblioteca opt-out explícito).

W4 — DICIONARIO EMITIDO CORRIGIDO (governança)
  O contrato dentro do Excel dizia a verdade da versão anterior:
  "fraude de recenseador" → anomalia de coleta; "medição individualizada
  por casa (NÃO é alvo)" → situação DESCONHECIDA + cruzar cadastro;
  "Anonimização total" → neutralidade de fonte; RADAR_ID → representação
  canônica (alinhado ao manifest); POLO_ID → sem "ponderado".

W5 — LIMPEZA DOCUMENTAL INTEGRAL
  "5 abas"→6; "7 testes"→14; exemplo de código com sample_weight
  removido; "sanidade+fraude"→anomalia; "CNAE simplificado"→
  classificação LEXICAL de atividade (código: _LEXICO_ATIVIDADE).

W6 — FUNIL POR CAUSA PRIORITÁRIA (fecha por construção)
  entrada_raw → dedup → exclusões com causa ÚNICA prioritária
  (NUMERO=0 > geometria > anomalia) → gate; consistência aritmética
  gravada no manifest; sobreposições brutas à parte; NUM=0 com chave
  Q/L elegível a hipótese contabilizado.

W7 — FONTE NUNCA INVENTADA
  source.dataset: declarado por argumento > inferido por nome de
  arquivo > UNKNOWN; metadata_mode explícito; verified=false até
  existir sidecar assinado do downloader (roadmap). Base sintética
  sai como UNKNOWN — provenance honesta.

W8 — RUNTIME NO MANIFEST + LOCK REAL
  requirements.in (faixas) + requirements.lock.txt (versões EXATAS
  validadas) + manifest.runtime{python,pandas,numpy,sklearn,openpyxl}.
  FutureWarnings de pandas corrigidos (fillna downcast, concat vazio);
  suíte roda limpa sob -W error::FutureWarning — mas ISSO É POR RUNTIME.
  Revisão externa reportou 3 falhas em pandas 2.2.3 onde o lock pede 3.0.2:
  `.replace('', np.nan)` emite downcast no 2.2.x e é silencioso no 3.x.
  A afirmação da doutrina valia só no runtime do lock, que não é imposto.
  v4.9: trocado por `.mask(lambda s: s.eq(''))`, limpo nas duas versões.
  Lição: afirmação de qualidade que depende de versão precisa DIZER a versão.

W9 — SINAIS DECOMPOSTOS P/ CALIBRAÇÃO FUTURA
  N_SINAIS_PRIMARIOS (S1/S2/S3) + FLAG_CONTEXTO_S4 + FLAG_REFORCO_S5 —
  S1+S5 não são duas evidências independentes; a graduação
  observação/quarentena/exclusão usará os PRIMÁRIOS.

W10 — DETERMINISMO SEMÂNTICO DOCUMENTADO
  Valores idênticos entre execuções; SHA-256 do .xlsx varia por
  metadados internos do formato (aviso no manifest).

DIVERGÊNCIA REGISTRADA: o raio grosseiro de 300 km permanece HARD gate
(agora parametrizável: --geo-raio-grosseiro) — rebaixar p/ flag mudaria
volumetria sem golden; é dívida aberta com saída definida (malha
municipal ST_Covers + buffer). Thresholds e pesos seguem CONGELADOS
até golden + amostra rotulada (concordância mútua das duas revisões).
```

**Gate de empacotamento.** `scripts/validar_skill.py` valida o ENVELOPE antes do
zip: `description` ≤ 1.024 caracteres, `name` ≤ 64 e em minúsculas/hífen, e
`name` igual ao diretório. A v4.7.0 foi empacotada uma vez com description de
1.759 caracteres e o instalador recusou — o defeito não estava no motor nem nos
dados, estava no envelope, que nenhuma das 49 provas do produto olhava. Cada
versão vinha somando frase à description sem ninguém medir. Restrição não
verificada é restrição que será violada.

```bash
python scripts/validar_skill.py && zip -r ../skill.skill .   # nesta ordem
```

---

### v4.11.0 — revisão externa do PACOTE: um P0 de identidade e a terceira camada

Revisão que leu o executável, não o `SKILL.md`. Achou o que eu tinha medido
errado.

**P0 — o TÍTULO do logradouro não entrava no identificador.** A CHAVE
operacional leva `MUNICIPIO|TIPO|TITULO|NOME|NUMERO|LOCALIDADE` desde a v4.8. O
identificador ficou com `TIPO + NOME`. Duas entidades que o pipeline trata como
distintas recebiam o **mesmo** `RADAR_ID_ENDERECO`.

Eu tinha medido isso na v4.9.4 e concluído que era seguro — **medi errado**.
Agrupei os casos "com título × sem título" junto com os "título A × título B" e
olhei os primeiros exemplos, que eram todos `DOUTOR`/vazio. Separando a classe
perigosa, o CNEFE de Porto Alegre tem **7 colisões reais**:

```
RUA BARAO DO GRAVATAI      ×  RUA BARONESA DO GRAVATAI   (204 e 429)
RUA PAI JOAQUIM            ×  RUA SAO JOAQUIM            (0, 11, 35)
RUA ACADEMICO ...PORTO     ×  RUA DEPUTADO ...PORTO      (15 e 25)
```

`BARÃO` e `BARONESA DO GRAVATAÍ` são duas ruas da cidade. Estavam com o mesmo
id.

E o gate N13 não podia pegar: ele procura **um hash apontando para duas
canônicas**, e aqui as duas linhas viravam a **mesma canônica antes do hash**.
O gate respondia corretamente a uma pergunta menor do que o nome prometia — o
mesmo padrão do `verificar_lacre` da v4.9.3.

Correções, nesta ordem:

1. **`CANON_VERSAO = 3`** — a canônica passa a levar `TIPO + TÍTULO + NOME`, com
   ponte para a versão anterior publicada dentro do pacote lacrado.
2. **A harmonização de título deixou de ser por MODA.** A moda escolhia um
   título e apagava o outro: `BARAO` e `BARONESA` do mesmo nome de rua viravam
   o mesmo título e colapsavam de novo, um estágio antes. Agora a união exige
   **parentesco** — prefixo (`PRES` ⊂ `PRESIDENTE`) ou mesma chave fonética.
   Uniformizar por frequência destrói distinção real.
3. **Omissão ≠ via diferente.** Levar o título ao id sem tratar a omissão
   partiria 12 ruas de POA em duas (`RUA RAUL MOREIRA` × `RUA DOUTOR RAUL
   MOREIRA`). Quando o município registra **um único** título para aquele
   tipo+nome, a ausência é omissão e é preenchida; com **dois**, não se
   preenche nada. Resultado medido: **5 separações, e são exatamente as 5
   corretas** — zero regressão.
4. **Gate N14** — duas CHAVEs operacionais distintas não podem receber o mesmo
   id. É a pergunta que faltava: N13 olha depois do hash e não vê o que foi
   perdido antes dele.

Testes **D20**/**D21**.

**A terceira camada: DECISÃO passa a ser declarada, e a anomalia deixa de ser
binária.** A crítica central da revisão: *heurística não calibrada com poder de
gate*. Duas mudanças.

`CNF_ELEGIBILIDADE` diz, linha a linha, **por que** o registro entrou ou não no
universo de candidatos — `RETIDO_CONFIANCA`, `RETIDO_ANOMALIA`, `RETIDO_GEO`,
`RETIDO_SEM_NUMERO`, `ELEGIVEL_COM_OBSERVACAO`, `ELEGIVEL_COM_QUARENTENA`. Os
pesos 40/30/30 continuam não calibrados (M6 segue aberto) — mas a exclusão que
eles produzem parou de ser invisível, e a contagem por motivo entra no funil do
manifest. Decisão não calibrada pode ficar; invisível não pode.

E a **escada de anomalia** saiu do comentário para o código: 1 sinal primário é
observação, 2 é quarentena, 3 exclui. A política executada era binária —
qualquer sinal derrubava o registro — enquanto o próprio
`_decompor_sinais_anomalia` documentava desde a v4.5.1 que a graduação certa
era essa. Uma sequência perfeita de numeração pode ser perfeitamente verdadeira
num prédio bem enumerado.

Efeito colateral revelador: o teste **D14** parou de reproduzir sua própria
pré-condição. Ele existia porque salas anotadas `VAGO` saíam do universo
coletivo por "anomalia" e o motor então inferia justamente as salas que a fonte
declarou vazias. Com a escada, `VAGO` repetido é **um** sinal e não derruba
mais nada — o defeito sumiu na origem. A fixture foi reancorada num caminho que
a escada não absorve (coordenada reprovada na sanidade), porque o invariante
continua valendo.

**`NUM_FACE`: removida a interpretação inventada.** O eixo N4 do
`SCORE_NUMERACAO` lia `1 = par, 2 = ímpar (convenção IBGE)`. Essa convenção
**não existe** no dicionário do CNEFE, que define `NUM_FACE` apenas como
"número da face". Era semântica atribuída a um identificador, e um score que
pune o lado "errado" de uma rua a partir de uma convenção que ninguém publicou
pune o dado certo. N4 zerado, teto do score declarado em 80.

**Portabilidade: o score deixou de carregar a latitude de Porto Alegre.** O
fator longitudinal era `cos(-29,92°)` fixo, comentado como "suficientemente
geral" — em Boa Vista erra a escala em ~13%, e o DBSCAN ao lado já fazia certo
com haversine. Agora usa a latitude do próprio grupo. E o agrupamento passou a
ser por **município + logradouro**: `RUA PRINCIPAL` existe em centenas de
municípios, e agrupar só pelo nome misturava pontos de ruas sem relação.

**O remerge por proximidade só usa geometria SANEADA.** Ele aceitava qualquer
lat/lon não nulo, inclusive coordenada que a `sanidade_geo` — que roda antes no
mesmo pipeline — já havia reprovado. A informação existia e não estava sendo
usada para a decisão de fundir duas chaves.

**`DESOCUPADOS` renomeado.** `SET_DOM_DESOCUPADOS` somava vagos **e** uso
ocasional sob um rótulo que faz ler "30% de uso ocasional" como "30% de imóvel
vago". São categorias distintas do IBGE. Agora
`SET_DOM_SEM_OCUPACAO_HABITUAL` / `SET_TX_SEM_OCUPACAO_HABITUAL` /
`SET_CLASSE_SEM_OCUPACAO_HABITUAL`, com as parcelas seguindo publicadas
separadas.

**Afirmações rebaixadas ao que a prova sustenta.** *"O domicílio desocupado
está no CNEFE"* virou *"o universo de domicílios particulares do CNEFE é
compatível com o universo agregado"* — o fechamento aritmético de POA prova
compatibilidade de universos naquele teste, não identifica registro nem vale
como teorema para toda UF e vintage, e o IBGE declara expressamente que não
divulga estado de ocupação no CNEFE. *"Municípios distintos NUNCA colidem"*
virou o que é matematicamente verdade: municípios distintos geram **chaves
canônicas** distintas; colisão de hash em 63 bits tem ~8,7e-5 de probabilidade
em 40 M de chaves e é **detectada por N13, que aborta**. Um gate executável vale
mais que a palavra "nunca".

**GOLDEN de verdade (`golden.py`).** Canoas e Santa Maria estavam declaradas
como golden e, duas linhas abaixo, como "precisam de re-baseline antes de
voltarem a travar regressão" — golden que não trava nada é comentário com nome
de teste. Agora existe o mecanismo: baseline extraída do artefato real, com
`run_fingerprint` embutido, tolerância **zero** para o estrutural e banda
declarada por métrica para o distributivo. **Declarado sem rodeio:** a baseline
desta versão é de Porto Alegre, a única base real disponível nesta sessão;
Canoas e Santa Maria entram quando os arquivos estiverem à mão.

**Drift documental fechado.** O mesmo documento afirmava 99 e 103 campos, a
tabela de blocos não mostrava o bloco SET, e havia pendência listada como
aberta (`tipo+título na CHAVE`) que já estava corrigida desde a v4.8 — o tipo
de dívida que faz alguém "consertar" de novo o que já está certo. Tabela
regenerada do dicionário, contagens sincronizadas, pendência removida. O
`requirements.lock.txt` foi carimbado nesta versão: as dependências não
mudaram, mas o cabeçalho ficou parado em v4.5.2 por seis versões, e lock cuja
procedência não acompanha o código não prova ambiente nenhum.

**Promessas ajustadas ao estado real.** Saiu "vintage futuro" da descrição —
o software conhece o layout CNEFE 2022 e, se o IBGE mudar o significado
mantendo o nome, `validar_schema()` não perceberá. Fica: **portável entre
municípios e UFs do layout 2022; vintage novo exige adaptador de schema**.

Suíte: **100 testes, zero pulados**. Contrato: **104 colunas**.

**Segue aberto, com nome:** calibração M6 (precision/recall por faixa, por
município, por regime urbano/rural) — sem ela `RETIDO_CONFIANCA` é medida, não
prova; schema gate semântico (domínio, cardinalidade, taxa de nulo pós-coerção,
não só presença de coluna); golden de Canoas/Santa Maria; QA do XLSX ponta a
ponta; Q/L com subcluster espacial antes de inferir continuidade; e a separação
CI-FUNCTIONAL × CI-RELEASE, porque o modo `strict` compara o runtime com o lock
e **deve** quarentenar fora do ambiente carimbado — isso é virtude do auditor,
não defeito, mas o teste não pode fingir que roda em qualquer lugar.

---

### v4.10.0 — a metodologia de logradouro entra, mas medida antes

Incorporação da metodologia da skill `ferramenta-logradouro-padrao`. O que
mudou o desenho não foi ler a skill — foi **medir cada peça no CNEFE real de
Porto Alegre antes de adotar**. Metade do que parecia bom não passou.

**Canônica v2 — numeral por extenso entra, romano NÃO.** Medido em 6.876 nomes
de via distintos: o extenso funde 66 grupos, **todos** a mesma rua escrita de
dois jeitos (`VINTE E CINCO DE JULHO`≡`25 DE JULHO`, `SEM DENOMINACAO UM`≡`SEM
DENOMINACAO 1`), fator de colisão **1,0096×**. O romano rende **uma** fusão
legítima (`XV DE NOVEMBRO`) e estraga nomes reais: `ANNIBAL DI PRIMIO BECK`
vira `ANNIBAL 501 PRIMIO BECK` (D+I) e `BEM TE VI` vira `BEM TE 6`. Numa
pré-imagem **publicada** isso é errado na cara de quem lê. Romano ficou fora
da identidade e virou marcação.

E a soma ingênua de numeral tinha um defeito próprio: `SESSENTA SESSENTA`
virava 120 — e existe rua `120` em POA. Numeral PT-BR bem formado nunca repete
a mesma classe decimal; a sequência que não fecha sai **inteira** como veio
(converter só o pedaço que fecha daria `SESSENTA 60`, que é pior que não
converter). Testes **D17–D19**.

**A ponte v1→v2 viaja lacrada.** Mudar a canônica renumera 279.564 endereços;
sem ponte a série histórica morre calada. `.crosswalk_canon.csv` sai dentro do
pacote, com `crosswalk_sha256` no selo e `canon_versao` no manifest — quem tem
a entrega anterior faz o JOIN. Teste **qa52**.

**G20 — fator de colisão vira instrumento.** Para decidir se largar o título
era seguro, na v4.9.4 eu medi à mão. Uma decisão dessas volta a cada mudança de
canonicalização, e sem instrumento fixo ela volta como *argumento*. Agora é
gate executável com teto declarado, vai para o manifest, e o auditor recalcula
o dele por caminho próprio (R14). Fundir **grafia** é o objetivo; fundir **rua**
é o defeito, e os grupos fundidos saem listados. Teste **qa53**.

**Vocabulário de tipo de via — aponta, nunca corrige, e sempre WARN.** Porto
Alegre trouxe 10 tipos que as 7 UFs auditadas naquela skill não tinham
(`CONJUNTO HABITACIONAL`, `RUA DE PEDESTRE`, `PONTE`, `TRILHA`, `ESTANCIA`…) —
prova de que vocabulário incompleto é o estado **normal**. Reprovar a entrega
porque a nossa lista é curta inverteria quem é a fonte. `QA-END-010`, WARN por
construção. Teste **qa54**.

**Fonética: marcação graduada, nunca identidade.** Medida: 71 grupos, fator
1,0104×. Acerta `AYRTON SENNA`≡`AIRTON SENA` e `ARTHUR BOTTONA`≡`ARTUR BOTONA`
— e **funde `CEFER I` com `CEFER II`**, que são ruas diferentes. Então entra
como evidência no mesmo desenho R9 das hipóteses de unidade: a fonética/romano
**gera o candidato**, a **geometria e a numeração confirmam**, e o grau sai
graduado. `SUSTENTADA` exige as duas provas independentes — mesma coordenada
(≤50 m, a mesma régua do remerge, R7) **e** número em comum. Só grafia nunca
sustenta. Três colunas novas: `END_LOGR_EQUIV_SUGERIDA/_GRAU/_EVIDENCIA`.
Testes **qa55–qa56**.

**Léxico auto-incremental (`--lexico`).** Aprende equivalência de token
(`CONS`≡`CONSELHEIRO`) de pares que a **própria base do IBGE prova** serem o
mesmo endereço: mesmo município, mesma localidade, **mesmo número exato**,
centróides a menos de 50 m, e exatamente **um token 1↔1**. Não é hipótese sobre
o significado do token — é o arquivo dizendo que os dois textos nomeiam o mesmo
lugar.

Governança: `support` conta **imóveis distintos** (um condomínio de 300
unidades vale uma prova, não 300); promoção a `ativo` só com support ≥ 2;
**decay** por meia-vida de 180 dias, porque léxico que só cresce vira dívida;
**guarda de ciclo** (token com dois canônicos vai para quarentena, nunca
sobrescreve); adição e truncamento de token **nunca** são aprendidos
(`SANTOS` ⊂ `SANTOS DUMONT` na mesma esquina é rua diferente); blacklist manual
tem a palavra final. O léxico alimenta **só a marcação** — o identificador não
o enxerga, então nenhuma safra é renumerada por algo que a skill aprendeu — e
seu SHA entra no `run_fingerprint`, porque duas execuções com léxicos
diferentes marcam coisas diferentes. É gravado **depois do lacre**: execução
que foi para a quarentena não deixa aprendizado atrás de si. Testes
**qa57–qa60**.

**O que POA ensinou depois de tudo pronto — quatro defeitos MEUS.** O ciclo
rodou na base inteira quatro vezes, e cada volta derrubou uma peça que passava
com folga nas fixtures:

1. **Quadrática sem teto.** O canal de marcação comparava par a par dentro do
   grupo com `.iloc` — 279 mil grupos, e o emissor passou de 27 minutos sem
   chegar à publicação. Registros materializados uma vez, mais um **teto
   declarado** por grupo (acima dele a chave fonética não discrimina mais: é
   nome genérico, não abreviação) contado no manifest.

2. **O campo tratado deixou de cumprir o próprio contrato.** O dicionário de
   `END_LOGRADOURO` promete "numeral expandido"; a canônica passou a expandir e
   o campo ficou para trás — a canônica dizia `10 DE MAIO` e a linha publicava
   `RUA DEZ DE MAIO`. O auditor reprovou 10.147 linhas **antes** da publicação,
   que é exatamente para isso que o ciclo existe.

3. **Um aviso que reprovava.** Desenhei `QA-END-010` como WARN "que nunca
   bloqueia" — e em `strict` todo WARN vira quarentena. Porto Alegre reprovou
   por dois tipos de via (`ESCADA`, `TRAVESSIA`) que faltavam na NOSSA lista.
   Existe agora uma lista curta e justificada de avisos informativos que não
   escalam: um aviso que reprova não é um aviso.

4. **Irmãs de loteamento aprendidas como grafia — nos dois canais.** O léxico
   promoveu `2→3` (support 99), `B→C` (45), `R3→R4` (36), `01→02` (33): ruas
   **paralelas**, mesmo número de casa, a 30 m uma da outra. Edição ≤ 1 é
   verdade para qualquer par de ordinais consecutivos. E a marcação tinha o
   mesmo buraco por outro caminho — a fonética mapeia Z→S e `ACESSO S` saía
   como equivalente SUSTENTADO de `ACESSO Z`.

   A regra que fecha os dois não é lista negra: **um token cuja função é
   enumerar existe para distinguir irmãos, e nunca é abreviação do vizinho**.
   Depois disso o léxico de POA caiu de 76 "equivalências" para **2 reais**
   (`AFONCO→AFONSO`, `LUIS→LUIZ`), com candidatos que são todos erro de
   digitação de verdade (`LURDES→LOURDES`, `MUTONI→MUTTONI`, `CRIANAS→CRIANCAS`).

   Corolário honesto: **em entrada CNEFE o léxico rende pouco**, porque o IBGE
   já entrega limpo. Ele foi construído para a trilha de cadastro, e é lá que
   vai pagar.

5. **A marcação sugeria a rua para ela mesma.** Dois endereços da MESMA via em
   números diferentes caem na mesma chave; sem exigir slots distintos, 278.724
   linhas (34% da base) saíram marcadas. Marcação que dispara num terço do
   arquivo não é marcação, é papel de parede.

**O que foi medido e REJEITADO:** o dicionário de títulos abreviados
(`DR`→`DOUTOR`, `CEL`→`CORONEL`). Ganho ~zero nesta entrada — o CNEFE de POA
tem 111 títulos distintos e **todos vêm por extenso**. Fica reservado para
quando o RCC ingerir cadastro no confronto, que é onde a sujeira mora. Também
rejeitada a camada de correção com chamada de IA: rede e não-determinismo são
incompatíveis com lacre, fingerprint e com "não invente nada".

Suíte: **97 testes, zero pulados**. Contrato: **103 colunas**.

---

### v4.9.4 — fechando a cadeia, e um P0 encontrado por um teste PULADO

Sete itens de fechamento da cadeia de custódia — e, no meio deles, o defeito
mais caro do lote, achado por acidente.

**0. PDCA-01 na INGESTÃO: o valor do complemento lido como `float64`.**
`load_cnefe()` tipava como texto uma lista de **exceções** de 4 colunas e
deixava `VAL_COMP_ELEM1..5` — o número do apartamento, do bloco, do lote — à
inferência do pandas. Basta o arquivo ter esses valores numéricos e **alguma
linha sem complemento** para a coluna virar `float64`: o apartamento `101` vira
`'101.0'`, `num_unidade()` (que exige dígitos puros) para de reconhecê-lo, a
grade deixa de ler aquele endereço e **todas as hipóteses daquele prédio
desaparecem** — sem erro, sem aviso, sem quarentena.

Depende do DADO, não do código, e é por isso que sobreviveu: em Porto Alegre
`VAL_COMP_ELEM1` tem letra em alguma linha e vem `object`. Mas **`VAL_COMP_ELEM5`
de Porto Alegre é `float64` no arquivo real** — a classe está viva na base de
produção, só que numa camada rasa. Num município cujos complementos sejam todos
numéricos, a base inteira sai corrompida e a contagem de hipóteses cai sem que
nada acuse.

A lista **inverteu-se**: é numérico o que está *declarado* numérico
(`_NUM_COLS`); todo o resto é texto. Fail-closed também para o futuro — coluna
nova do IBGE chega como texto, e texto se converte; float arredondado não se
desfaz. Teste **D16**.

Como apareceu: dois testes de corrupção (`qa12`, hipótese virou observação;
`qa14`, grau alterado) estavam **PULADOS** havia versões, com a mensagem
`fixture sem hipotese`. Ao construir o prédio com lacuna que faria os dois
rodarem, a lacuna sumia quando havia uma padaria na base. *Um teste pulado não
é um teste que não protege: é um teste que esconde.*

**1. `RUN_FINGERPRINT` era calculado ANTES de adquirir a fonte SET.** Com o
cache vazio, `_set_sha` saía `''` e o arquivo efetivamente baixado *naquela
execução* ficava de fora — duas fontes distintas, mesmo fingerprint. A
aquisição passou para antes, e o hash é do caminho **realmente devolvido** pela
aquisição, nunca de um caminho presumido. Teste **qa42** (o downloader é
injetado no subprocesso: preencher o cache antes do CLI testava o caminho de
cache quente, onde o defeito não existe).

**2. A TAG de `UNIDADE_ID` não era derivada.** `QA-DER-004` conferia o corpo
(`partes[1]`) e ignorava o primeiro segmento: `...-LOJ-0001` virar
`...-XXX-0001` era selado. O segmento que diz **que tipo de unidade é aquela**
podia ser qualquer coisa. Agora a TAG é rederivada de `UND_NATUREZA`/`UND_TIPO`
por uma tabela **própria do auditor** (R14 — importar a constante do produtor
faria os dois concordarem por construção). Teste **qa43**.

De quebra, `UNIDADE_ID` de raiz `SEM-<mun>` — registro sem identidade de
endereço — entrou na checagem de composição; era um bolsão sem auditoria.

**3. `COLETIVA_ID` agora é confrontado com o STORE (`QA-DER-005`).** Forma,
município embutido e bijeção com o hash são propriedades **internas**: trocar
`COL-…-000001` por `COL-…-000002` preserva as três e destrói a série temporal,
que é a única razão de o id ser sequencial em vez de recalculado. Identidade
histórica não se recalcula — se consulta. Teste **qa44**.

**4. `verificar_pacote()` era fail-open.** Hash ausente do selo era `continue`:
bastava **apagar** `manifest_sha256` para o pacote voltar a "válido" — o lacre
premiava quem adultera. Agora hashes obrigatórios e campos de identidade
(`run_id`, `run_fingerprint`, `quality_seal`) ausentes **invalidam**. Teste
**qa45**.

**5. O contrato viaja no pacote lacrado.** `contract_sha256` só é prova se o
arquivo que ele lacra estiver junto; sem isso é metadata que ninguém consegue
conferir daqui a duas safras. `.contract.csv` é publicado dentro da transação e
seu hash entra no selo. Teste **qa46**.

**6. `bloco_set` reconciliado, e proveniência separada de métrica.** A
cobertura era medida no dataframe **intermediário** (antes das hipóteses) e
misturada com sha da fonte e estado do download; nada no auditor a
recalculava. Agora as métricas saem do **artefato entregue**, dos dois lados e
por caminhos separados, e o que é proveniência foi para
`bloco_set_proveniencia`, fora do confronto. E a comparação de dicionários
deixou de fazer `int(v)`: `pct_casado` 12,34 virava 12 e qualquer valor entre
12,00 e 12,99 passava. Teste **qa47**.

**7. O ciclo rodou em Porto Alegre — e achou mais dois.** Era o item que ficou
aberto desde a v4.9. 812.415 linhas, 279.564 endereços. O selo saiu
`QUARANTINED`, com dois achados que nenhuma fixture tinha como produzir:

**7a. O identificador não era reproduzível por terceiros em 19% dos
endereços.** `QA-DER-001` acusou 53.394 de 279.528 ids "que não derivam do
endereço". Não derivavam mesmo. A chave canônica usa o nome **harmonizado** e
**não leva o título** do logradouro; `END_LOGRADOURO` leva. De `RUA CORONEL
VICENTE` ninguém reconstrói `RUA VICENTE` sem saber qual token era o título —
e 23,15% das linhas de POA têm título. A doutrina dizia "reproduzível por
terceiros a partir das colunas `END_*`"; era falso para um quinto da base, e
só não aparecia porque nenhuma fixture tinha título.

Corrigir por adivinhação (aceitar o hash "com um token a menos") seria trocar
falso positivo por falso negativo. A pré-imagem passou a ser **publicada**:
`COLETIVA_CHAVE_CANONICA`, coluna 100 do contrato. O auditor rederiva dela,
100%, e — para que publicar não fosse só mover a confiança de lugar —
`QA-DER-006` amarra a canônica ao endereço da própria linha: município e
número exatos, e os tokens do logradouro canônico **contidos** nos de
`END_LOGRADOURO` (a canônica pode ter menos, nunca ter o que a linha não tem).
Forjar canônica e hash juntos é coerente e falso; o teste **qa49** faz
exatamente isso e tem de reprovar.

**7b. Dez `COLETIVA_ID` com dois nomes de rua.** `AVENIDA DOUTOR PANATIERI
363` e `AVENIDA PANATIERI 363` já compartilhavam o id (a canônica não leva
título) e saíam com `END_LOGRADOURO` divergente **dentro do grupo** — a
harmonização de título é por `(CEP, tipo, nome)` e não alcança quando o mesmo
endereço aparece com CEPs diferentes. Um id de endereço com dois nomes de rua
não é um endereço. `END_LOGRADOURO` passou a ser resolvido no grão do grupo
(contagem, desempate alfabético — determinismo, não sorteio); a grafia de cada
registro continua inteira em `END_LOGRADOURO_ORIG`.

**8. A suíte está isolada da rede.** Ela tocava o FTP do IBGE — lenta,
dependente de servidor de terceiro para dizer se o *código* está certo, e capaz
de passar pelo motivo errado. Duas camadas: `RADAR_OFFLINE` no ambiente
(herdado pelos subprocessos, honrado no único ponto onde a rede existe) e
bloqueio de `socket.connect` no processo do pytest. O teste legado que
dependia de um `cache_ibge/` relativo ao diretório de invocação passou a
popular o cache localmente.

Suíte: **80 testes, zero pulados**, verde sob `-W error::FutureWarning`.
Contrato: **100 colunas** (entrou `COLETIVA_CHAVE_CANONICA`).

**Custo medido, sem enfeite:** o ciclo completo em POA leva ~20 min, com pico
de ~5,8 GB — o grosso é o hash por linha de 812 mil registros. É caro e está
declarado; streaming do QA é dívida aberta, não surpresa.

**Segue aberto, sem rodeio:** QA do XLSX ponta a ponta — `auditar()` ainda
levanta `AttributeError` num workbook multiaba; `radar_potenciais.py` continua
fora do ciclo. Assinatura do selo, grão no contrato, cross-check RCC × Excel,
drift, streaming do QA, rebaseline do golden (Canoas/Santa Maria), calibração
M6 e as 45 hipóteses residuais de unidades existentes.

---

### v4.9.3 — auditoria do auditor, segunda leva

Sete defeitos, todos dentro da cadeia de custódia que a v4.9.2 construiu.
Nenhum é do motor; todos são do sistema que existe para garantir o motor.

**1. `RUN_FINGERPRINT` declarava equivalentes execuções diferentes.** A fonte
SET efetivamente usada não entrava. Duas execuções com taxa de desocupação de
20% e 50% produziam artefatos distintos e o **mesmo** fingerprint. O `runtime
efetivo` também faltava — o `lock_sha` diz qual ambiente *deveria* existir, não
qual existia, e em `exploratory` os dois divergem por design. Entraram
`fonte_set_sha256` e `runtime_sha256`.

**2. A publicação não era transacional para SUBSTITUIÇÃO.** Com uma entrega
anterior no lugar, falha no meio deixava o artefato ANTIGO publicado e apagava
os sidecars — artefato oficial sem linhagem. O rollback agora **restaura o
pacote anterior**: ou o novo entra inteiro, ou o antigo permanece inteiro.

**3. A quarentena preservava menos evidência que uma entrega aprovada.**
`quarentenar()` movia só o CSV e reconstruía um selo reduzido; perdiam-se
manifest, selo completo, `run_fingerprint`, `input_sha256` e runtime.
`quarentenar_bundle()` preserva o pacote inteiro — execução reprovada é
justamente onde se quer mais evidência.

**4. `QA-DER` só rederivava `COLETIVA_CHAVE_HASH`.** Adulterar `UNIDADE_ID` no
artefato **e** no modelo passava. Entraram `QA-DER-002` (forma de
`COLETIVA_ID`, município embutido × `END_MUNICIPIO`, bijeção com o hash),
`QA-DER-003` (`BLOCO_ID` = `COLETIVA_ID` + `-B` + `UND_BLOCO`) e `QA-DER-004`
(`UNIDADE_ID` deriva do grupo **e o sufixo é rederivado** de `UND_VALOR` /
`ORIGEM_REGISTRO_ID` — conferir só a forma deixava passar `...-REG-10000457X`,
que é alfanumérico e portanto "bem-formado").

**5. `QA-MAN` não reconciliava `inferidas_por_classe`.** Fechado.

**6. `verificar_lacre()` respondia menos do que o nome prometia.** Conferia só
`artifact_sha256`: alterar o manifest depois do lacre e perguntar "íntegro?"
devolvia `True`. Agora `verificar_artefato()` (uma pergunta) e
`verificar_pacote()` (o lacre de verdade, todos os hashes).

**7. `VERSAO_QA` estava parada em `1.0`** enquanto o contrato do auditor mudava
por completo. Versão que não versiona não serve para forense → `1.2.0`.

Suíte: **69 testes**, verde sob `-W error::FutureWarning`.

**Segue aberto, sem rodeio:** QA do XLSX ponta a ponta — `auditar()` ainda
levanta `AttributeError` num workbook multiaba, porque o reader aprendeu
multiaba e o auditor não; `radar_potenciais.py` continua fora do ciclo e ainda
emite `FutureWarning` no `pd.concat`. Assinatura do selo, grão no contrato,
cross-check RCC × Excel, drift, streaming. E **o ciclo ainda não rodou em POA**.

---

### v4.9.2 — falhas de segunda ordem: no próprio sistema de garantia

Seis defeitos, todos dentro do QA que a v4.9 introduziu. Todos reproduzidos
antes de corrigidos; todos com teste que falhava.

**1. `manifest_sha256` não correspondia ao manifest entregue.** O manifest era
REGRAVADO com o selo embutido depois de o hash dele entrar no selo: o selo
lacrava o manifest A, o cliente recebia o manifest B. `artifact` e `qa_report`
conferiam — `manifest`, não. Corrigido com cadeia unidirecional (artefato →
manifest → qa → **selo terminal**); o manifest guarda `seal_file`, um ponteiro,
nunca o conteúdo do selo.

**2. A publicação não era transação.** `for tmp, alvo: os.replace(...)` — N
renames sequenciais. Injetando falha no segundo, o artefato ficava publicado e
o manifest sumia, exatamente o estado que a arquitetura afirmava impedir.
Agora: staging em diretório, **um** rename atômico como ponto de commit e, como
N renames atômicos são impossíveis com nomes planos em POSIX, **ordem de falha
segura** — sidecars primeiro, artefato por último. Interrupção deixa linhagem
sem artefato (inofensivo), nunca artefato sem linhagem. `.commit-*` permanece
para recuperação. `qa30` parametrizado testa falha em cada um dos 4 renames.

**3. `RUN_ID` não identificava execução.** `blake2b(input, 3 bytes)`: 24 bits e
determinístico da entrada — duas execuções davam o mesmo id, e isso anulava a
quarentena por RUN_ID que a v4.9.1 tinha acabado de criar. Agora timestamp UTC
com milissegundo + `uuid4`.

**4. `RUN_FINGERPRINT` incompleto.** Faltavam `--municipio` (dois municípios do
mesmo arquivo davam o mesmo fingerprint), o `--store` (que participa da
estabilidade dos ids), a versão do QA, o hash do lock e o do próprio auditor.

**5. `QA-DER` auditava por amostra.** `head(5000)`: com 6.001 endereços e o erro
no 5.501, o artefato era selado — inclusive com o modelo carregando o mesmo
erro, que é o cenário exato do R14. Agora 100%, vetorizado.

**6. `QA-MAN` reconciliava 3 de 12 métricas.** `com_atividade` e
`grupos_coletivos` podiam ir a 999.999 e o pacote era selado. Reconciliação
parcial é pior que nenhuma, porque transmite a confiança inteira.
`manifest_observado()` deriva todas do artefato.

**Mais:** `reabrir()` de XLSX devolve **todas** as abas (`sheet_name=None`) — ler
só a primeira e regravar com `to_excel` achataria um workbook de seis abas;
XLSX passou a ser não-reparável por design até o QA por aba existir. A política
de reparo documentada foi **reduzida ao que o código faz** (ordem de coluna e
sufixo `.0`), com o roadmap declarado como roadmap. `runtime` efetivo entrou no
manifest e no selo. `distributable: false` marca `SEALED_WITH_WARNINGS` para que
um exploratório não seja entregue como produção. Validador duplicado da raiz
removido — um só, em `scripts/`.

**Continua aberto:** QA do XLSX ponta a ponta (depende de refatorar
`radar_potenciais.py` em `main()`), assinatura do selo, grão no contrato,
cross-check RCC × Excel, drift e streaming. E o ciclo **ainda não rodou em POA**.

---

### v4.9.1 — fechamento das brechas do ciclo

Sete P0 da revisão externa, e o primeiro é meu erro de método.

**1. `test_qa05` não testava reparo.** Ele auditava uma CÓPIA LIMPA da fixture
no passo do reparo — o `SEALED` que afirmava não provava correção nenhuma,
provava que arquivo bom passa. Exatamente o padrão que a bateria existe para
impedir, escrito por mim na sessão em que passei a criticá-lo. Agora corrompe,
repara e **confere a ordem física no disco**. Somaram-se `qa05b` (sufixo `.0`
reparado, valor original restaurado) e `qa05c` parametrizado provando que
`UND_NATUREZA`, `COL_FORMA`, `EVD_GRAU` e `ACT_DESTINO_CAMPO` **nunca** entram
em autorreparo.

**2. Ordem de julgamento estava invertida.** Ao escrever o `qa05b` descobri que
`123.0` era classificado como divergência SEMÂNTICA e quarentenado antes de o
reparo ter chance de rodar. `_norm()` passou a separar normalização de
COMPARAÇÃO de normalização de CORREÇÃO: normaliza para comparar, repara a
representação, só então julga significado.

**3. `QA-DER` — rederivação independente do identificador.** A limitação real do
R14: produtor gera id errado, grava o id errado, auditor compara e sela. O
auditor agora **recompõe** a chave canônica dos campos `END_*` com
canonicalização própria e recalcula BLAKE2b. `qa24` prova: artefato idêntico ao
modelo, modelo errado, quarentena.

**4. Manifest DENTRO do ciclo.** Reconciliá-lo depois de publicar é conferir o
troco depois de sair da loja. Agora: manifest esperado → escrito pendente →
`qa.auditar(..., manifest=man)` → lacre → publicação.

**5. Publicação do PACOTE.** `publicacao_de_pacote()` prepara artefato +
manifest + seal + qa_issues em área temporária e renomeia **em bloco**. Havia
janela em que existia entrega sem linhagem. `qa26` prova que falha não deixa
manifest órfão nem `.pending-*` no diretório.

**6. `seal.json` separado, com a cadeia completa.** `artifact_sha256`,
`semantic_sha256`, `contract_sha256`, `manifest_sha256`, `qa_report_sha256`,
`input_sha256` (**completo**, não mais truncado em 16), `run_id`,
`run_fingerprint` e `correcoes_aplicadas`. RUN_ID diz *qual* execução;
RUN_FINGERPRINT diz se *duas execuções são equivalentes*.

**7. Quarentena por `RUN_ID` e suíte offline.** `quarentena/<RUN_ID>/` — a
evidência da primeira falha não é mais sobrescrita pela segunda. E a suíte
deixou de depender de rede: `cnefe_fixture.cache_set_offline()` mais cache
validado por **cabeçalho** em vez de tamanho (`size > 1 MB` aceitava download
interrompido e recusava fixture legítima — errava nas duas direções).

Suíte: **50 testes**, `pytest -q -W error::FutureWarning` verde.

**Continua aberto:** QA do XLSX (depende da refatoração de `radar_potenciais.py`
em `main()`), assinatura Ed25519 do selo, grão no contrato, cross-check
RCC×Excel, drift entre execuções e QA em streaming. E o ciclo **ainda não rodou
sobre POA** — 812 mil linhas com readback e hash por linha é outro regime de
memória.

---

## Mudanças v4.9.0 — Artifact Quality Cycle (R13/R14)

`qualidade_artefato.py` (independente de `radar_utils`, por R14) e
`tests/test_qa_artefato.py` — **22 corrupções deliberadas, 22 detectadas**,
escritas ANTES do auditor existir.

```
MODELO → GATES → grava .pending → REABRE pelo parser → estrutura → identidade
→ domínio → bloco (recálculo independente) → linha a linha → manifest
→ reparo de REPRESENTAÇÃO (1 ciclo) → revalida → hash → SEAL → rename atômico
                                                        ↘ QUARENTENA + laudo
```

| Corrupção | Detecção |
|---|---|
| truncado, coluna removida/extra/duplicada, ordem trocada | `QA-EST` |
| linha removida, duplicada, **removida+duplicada (total igual)** | `QA-LIN` |
| id truncado, em notação científica, arredondado em float | `QA-IDENT` |
| hipótese→observada, unidade→outra coletiva, grau, destino | `QA-LIN` semântico |
| contagem de grupo alterada | `QA-COL` (recalculada) |
| fechamento do setor quebrado | `QA-SET` (recalculado) |
| manifest divergente | `QA-MAN` |
| arquivo alterado **depois** do lacre | `artifact_sha256` |

**Três hashes**, porque são três perguntas distintas: `artifact_sha256` (bytes
entregues — 1 byte invalida), `semantic_sha256` (valores normalizados; um XLSX
regravado muda de bytes sem mudar de conteúdo) e `contract_sha256` (qual
dicionário produziu).

**Publicação atômica.** O nome definitivo só passa a existir depois do lacre;
falha ou interrupção deixa `.pending-*`, nunca um arquivo com cara de entrega.
Reprovado vai para `quarentena/` com `qa_issues.csv` ao lado — bloco, regra,
severidade, linha física, chave, campo, esperado, encontrado, se é corrigível.

**`--qa-mode strict|exploratory`.** Runtime divergente do lock e bloco
obrigatório ausente deixam de ser aviso no manifest: em `strict` impedem o
lacre. O checador de runtime foi reimplementado dentro do auditor porque
`radar_potenciais.py` roda argparse no escopo global e importá-lo sequestra a
linha de comando de quem o importa — dívida estrutural que fica declarada.

**Também nesta versão:** `.replace('', np.nan)` trocado por
`.mask(lambda s: s.eq(''))`. A doutrina afirmava suíte limpa sob
`-W error::FutureWarning`, e isso era verdade **só no runtime do lock** —
revisão externa reportou 3 falhas em pandas 2.2.3. Afirmação de qualidade que
depende de versão precisa dizer a versão, ou não depender dela.

### O que NÃO entrou, e por quê

- **ROW_SEMANTIC_HASH persistido no artefato**: o hash por linha é calculado e
  comparado, mas não gravado. Gravá-lo em 812k×103 dobra a escrita para um ganho
  que a comparação em memória já entrega.
- **QA do XLSX**: `qualidade_artefato` já lê `.xlsx` e `abas_xlsx()` existe, mas
  `radar_potenciais.py` ainda grava direto no nome final. Depende da refatoração
  do runner em `main()` — é o maior item aberto da v4.9.
- **Laço de reparo**: uma tentativa, por design. `while erro: corrigir` converge
  para "consertei até passar".

---

### v4.8.1 — PDCA-01 na ORIGEM do identificador (D15)

Encontrado ao avaliar a proposta de auditoria pós-emissão, e é o argumento mais
forte a favor dela: o defeito foi achado **olhando o artefato**, não o dataframe.

```
df['RADAR_ID_ENDERECO'] = canonica.map(lut).astype('Int64')
                                    ↑ com pd.NA presente, infere float64
```

`.map()` sobre a Series INTEIRA (com `pd.NA` nas linhas sem identidade) produz
float64; todo id acima de 2^53 perde dígito; e o `.astype('Int64')` seguinte
**carimba o valor corrompido como se fosse inteiro exato**. É a linha que CRIA o
identificador — quarta instância do PDCA-01 e a mais cara.

Medido no artefato de Porto Alegre antes da correção:

```
hashes > 2^53 no store ................. 279.297
int(float(x)) == x ..................... 279.297   (100,00%)
mesmo teste em inteiros aleatorios .....       0,60%
apos a correcao ........................       0,00%
```

**Por que nenhum dos 19 gates pegou:** G11 verifica DTYPE (que era `Int64`,
correto), `varredura_ids` verifica PADRÃO DE NOME, e o round-trip do teste 17
verifica SERIALIZAÇÃO. Os três olham a REPRESENTAÇÃO. O VALOR já estava errado
antes de qualquer um deles rodar.

Lição incorporada em `_serie_id_nullable()` e no teste D15, que compara o id
com o **recomputado da chave canônica** — valor contra valor.

**Também nesta versão:** `validar_skill.py` resolve o caminho antes de comparar
com o diretório (`../SKILL.md` reprovava um pacote válido) e passou a avisar
quando a folga da `description` fica abaixo de 80 caracteres; `description`
reduzida de 1.009 para 943; e os números estruturais do SKILL.md (103 campos,
19 gates, 17+32 testes) foram sincronizados com o código.

---

## Mudanças v4.8.0 — correção dos P0 (auditoria externa + achado de campo)

Origem: um achado de campo (`COL-4314902-001003`: hipótese criada para o
apartamento 303, que existe no arquivo como `303 SINDICO`, e a linha saindo com
o complemento `APARTAMENTO 601` herdado de um irmão) e uma auditoria externa do
pacote como software executável. **Todos os P0 alegados foram reproduzidos antes
de aceitos.**

**O método mudou antes do código.** `tests/test_p0.py` foi escrito PRIMEIRO e
estava vermelho em 12 de 14 na v4.7.0. Um teste que já passa antes da correção
não testa o defeito — testa outra coisa e emite confiança. `tests/cnefe_fixture.py`
impõe a regra que faltava: **todo teste de comportamento parte do CSV bruto no
layout oficial e atravessa `preparar_base()`**. O defeito Q/L sobreviveu a uma
suíte inteira porque o teste fabricava `NUM_ENDERECO=10 + PADRAO='QUADRA_LOTE'`,
estado que o pipeline nunca produz.

| # | Defeito | O que corrompia | Correção |
|---|---|---|---|
| D1 | inclusão = exclusão | `303 SINDICO` saía da grade e voltava como lacuna | `num_unidade_ocupada()` (R11): inclusão estrita, exclusão permissiva |
| D2 | hipótese herdava complemento | linha do 303 com `APARTAMENTO 601` | campos de grão de unidade zerados |
| D3 | Q/L morto | `COND_HORIZ_LOTES` inalcançável | elegibilidade em dois regimes: número **ou** quadra/lote |
| D4 | polos fail-open | `FORA_DE_POLO` em 812.653/812.653 | base crua + `polos[1]` + ligação em dois saltos; `--sem-polos` explícito |
| D5 | posição vazia = térreo | ausência virava evidência | exige posição térrea declarada |
| D6 | downloader × loader | vírgula na escrita, `;` na leitura | `SEP_CNEFE` como fonte única + `salvar()` |
| D7 | SET multiplicava linhas | 2 → 4 linhas | `validate='many_to_one'` + G19b de cardinalidade |
| D8 | CHAVE fundia RUA/AVENIDA | 714 chaves, 3.001 endereços | tipo+título na chave + re-merge por proximidade (50 m) |
| D9 | dedupe pós-normalização | `"001"` e `"1"` colapsavam | dedupe da linha bruta + `NUM_ENDERECO_RAW`/`NUM_STATUS` |
| D10 | desconhecido → comércio | falso positivo comercial | `NAO_CLASSIFICADO` |
| D11 | G6 decorativo | `_exigir(cond or True)` | invariante real: sem número **e** sem Q/L não há `COLETIVA_ID` |
| D12 | três versões divergentes | forense inútil | `radar_utils.VERSAO`, fonte única |
| D13 | pytest não coletava | CI cego | `conftest.py` + `test_legado.py` |
| **D14** | **grade lia meio prédio** | **`FLAG_COL` não é uniforme por CHAVE** | **R12: a numeração é lida do endereço inteiro** |

**D14 foi encontrado ao medir a correção do D1, e é o mais instrutivo.** Em
`RUA CORONEL VICENTE 529` há 18 salas; `FLAG_COL=1` em 7. O motor via 7 e
devolvia as outras como lacuna. A causa raiz: sala anotada `VAGO` recebe
`SETOR_ATIVIDADE='VAGO'` e sai do conjunto coletivo — **o CNEFE dizia "esta sala
existe e está vazia" e o pipeline respondia "esta sala falta"**. Coletividade é
atributo do endereço; a numeração também. É o mesmo erro de grão que matou Q/L e
os polos, numa terceira roupa.

### Medido em Porto Alegre (762.239 endereços)

| Indicador | v4.7.0 | v4.8.0 |
|---|---|---|
| hipóteses para unidade **que já existe** | 826 | **45** (−94,6%) |
| linhas em polo comercial | **0** | 55.923 |
| fachada ativa afirmada | 27/27 na fixture | 6.885 de 66.228 com atividade |
| atividade sem casamento lexical | virava comércio | 26.499 `NAO_CLASSIFICADO` |
| suíte | 49 testes, invisível ao pytest | 17 coletados + 49 legados, `pytest -q` verde |

### Ainda em aberto, declarado

- **45 hipóteses residuais** de unidade existente (0,09% das 50.176). Não sei
  ainda a causa; é o próximo a investigar, e a métrica está no teste.
- **Re-baseline do golden** (Canoas/Santa Maria): a CHAVE mudou, então o
  `COLETIVA_ID` mudou. **O store da safra anterior tem de ser regenerado.**
- **Calibração externa** (M6) continua pendente: `SUSTENTADA` é grau de
  evidência estrutural, não probabilidade medida.
- **Estado global mutável** (`SANIDADE_RAIO_GROSSEIRO_KM`), `ST_Covers`
  municipal, `EXEC_ID` de 24 bits e tier Polars/DuckDB seguem no roadmap.

**Consumo de memória:** a primeira implementação do R12 materializava
`{chave: subframe}` para 279 mil endereços e o processo morria sem mensagem em
7 GB. A versão final guarda dois dicionários de conjuntos numéricos.

---

## Mudanças v4.7.0 — graduação da inferência (R9) e bloco SET (R10)

Origem: teste em dado real de Porto Alegre (762.239 endereços) e a observação
de campo de que a régua de faixa, ao **descartar**, estava perdendo apartamentos
reais sem deixar registro.

**1. Nenhuma hipótese é descartada — todas são graduadas (R9).**
O critério anterior era binário: gerava ou não gerava. O novo é um único
critério ordenado — por quantos lados a observação cerca a hipótese. O motivo
não é generosidade com a hipótese fraca: é que **filtrar destrói a medição**.
Com a hipótese apagada, o campo só devolve falso positivo; o falso negativo
fica invisível e o limiar nunca calibra. Com ela graduada e retida, a matriz de
confusão fecha nos quatro quadrantes.

**2. `gap_grade` ganhou a classe que faltava e perdeu a extrapolação cega.**
`B_ANDAR_AUSENTE` (andar inteiro sem rastro entre dois presentes) nunca havia
sido gerada — a grade era o produto cartesiano dos andares observados. Em POA:
190 hipóteses fortes que não existiam. Em contrapartida, `101-109` deixou de
implicar `209`: a extrapolação virou `C_EXTRAPOLADA` com `EVD_DISTANCIA`, e
38,5% dela está a distância 1 (o caso do 208/209).

**3. `gap_posicional` passou a receber o grupo.** Recebia um `set` de tokens e
era cego à composição do endereço. Agora avalia contraparte por unidade: dos
17.277 endereços que disparavam em POA, 61,7% têm irmão capaz de exercer o polo
oposto — 47% sem descritor (frente implícita) e 14% enumerados.

**4. Bloco SET — ocupação por setor censitário.** 8 campos, origem
`IBGE:AGREGADOS_SETOR_2022`. Responde "o IBGE mapeou imóvel desocupado?": sim,
o endereço está no CNEFE, o atributo está no agregado. Ver T6 e R10.

**5. Defeito real corrigido, terceira instância do PDCA-01.** `.map(str)` sobre
coluna `Int64` **com NA presente** materializa via float64:
`4700095277622949996` sai como `'4.70009527762295e+18'`, o merge com o
endereço-pai morre em silêncio e **toda** hipótese fica órfã. As duas primeiras
instâncias foram na construção do frame; esta foi na conversão implícita.
`id_seguro(..., saida='str')` é a única porta de saída para texto. Teste [31]
carrega o controle negativo que reproduz o defeito — se ele parar de
reproduzir, o teste falha em vez de passar em falso.

**6. `num_unidade()` — parse estrito.** `713E714` (apartamento 713 E 714) lido
como notação científica virava `+inf` e estourava `OverflowError`. Terceira
armadilha de coerção implícita encontrada em dado real.

**7. Novos gates.** G16 (grau obrigatório em INFERIDO), G17 (destino deriva do
grau, não pode ser editado), G18 (`EVD_DISTANCIA` ⟺ `C_EXTRAPOLADA`), G19
(atributo de setor nunca vira contagem por unidade). Todos com teste negativo
que prova o abort. Suíte: 17 + 31 = **48 testes**.

**8. Gate de empacotamento (`validar_skill.py`).** A primeira tentativa de
publicar a v4.7.0 foi recusada: `description` com 1.759 caracteres contra o teto
de 1.024. Nenhuma das 49 provas olhava o envelope. Agora o limite é verificado
antes do zip, com controle negativo.

**9. `linhas_inferidas` vetorizada.** `iterrows` sobre as hipóteses virou merge;
o `gap_posicional` ganhou curto-circuito para os endereços sem token posicional
(a maioria esmagadora).

**Em aberto, declarado:** `TIPO/TÍTULO` na CHAVE foi IMPLEMENTADO na v4.8.0
(ver D8); o que segue pendente é o re-baseline do golden que essa mudança exige,
junto com o `IMOVEL_ID`, para pagar um único re-baseline. A calibração dos limiares R9 depende do retorno de
campo da amostra estratificada.

---

## Correções v4.5.2 — 3ª revisão externa (unificação de entrypoints)

```
Origem: 3ª auditoria independente. Veredito dela: o RUNNER estava pronto
p/ congelar, mas o PACOTE não — cnefe_coletivas.py e cnefe_mapa.py ainda
orquestravam pipelines PRÓPRIOS (sem dedup-antes-anomalia, nres/dnr sem
faixa F7), então bug corrigido no runner podia renascer nos auxiliares.

X1 — NÚCLEO ÚNICO radar_pipeline.preparar_base()          [teste 15]
  dedup + sanidade + anomalia + F7 + classificação + gate em UM lugar;
  radar_potenciais, cnefe_coletivas e cnefe_mapa CONSOMEM (df, stats).
  gate_universal(df) e gate_conf(df) (fail-closed) são as únicas
  máscaras de aprovação. Writer NUNCA recalcula regra de negócio.
  nres (Excel legado) e dnr (mapa) agora filtram faixa F7 ALTA+.

X2 — LINGUAGEM NORMATIVA SANEADA (doc = código)
  Frontmatter description reescrito (motor de DESCOBERTA DE CANDIDATOS;
  sem "tratamento oficial", sem "fraude de recenseador" como capacidade,
  sem "anonimização total" — é neutralidade de fonte, R5). R2 descreve a
  ordem REAL (dedup na ingestão, W2) e a decomposição de sinais (W9).
  Checklist sem flags/funções renomeadas (FLAG_SUSPEITA_FRAUDE,
  detectar_fraude). "fraude" permanece apenas como termo legado citado.

X3 — DICIONARIO SEM FONTE FIXA
  Intro não afirma mais "base oficial (2022)" — aponta para o manifest
  (single source of truth da proveniência, que pode ser UNKNOWN).

X4 — LOCK COMPLETO + VALIDAÇÃO DE RUNTIME NO MANIFEST
  requirements.lock.txt com TRANSITIVAS (scipy, joblib, et_xmlfile...) +
  .python-version; manifest.runtime_validation compara runtime vs lock
  (VALIDATED | WARNING_RUNTIME_DIVERGENTE | LOCK_AUSENTE) — a 3ª revisão
  rodou em py3.13/pandas2.2.3 e provou que divergência silenciosa existia.

X5 — LOG DO GATE COM BASE CORRETA
  "GATE aprovados: X / N1 pós-dedup (N0 raw)" — antes o denominador
  misturava raw com pós-dedup e o funil aparentava não fechar.

STATUS: com X1-X5 a engenharia de regra CONGELA. Próxima etapa não é
código: golden real (re-baseline Canoas 176.899 / Santa Maria 149.483)
+ amostra rotulada 500-1.000 endereços → precision/recall/FPR/FNR por
classe (M6). Só então recalibrar thresholds/pesos.
```

### Arquitetura-alvo (v5 — direção aceita da revisão)

```
L0 RAW (fonte imutável + hash)  →  L1 NORMALIZAÇÃO  →  L2 IDENTIDADE
(endereço/bloco/quadra/face + ENTITY_ID persistente)  →  L3 QUALIDADE
(sanidade/anomalia/numeração SEM exclusão silenciosa)  →  L4 EVIDÊNCIAS
(verticalização, economias, uso misto, horizontal, gaps)  →  L5 MODELOS
DE OPORTUNIDADE  →  L6 VALIDAÇÃO EXTERNA (cadastro, ligações, medidores,
consumo, POI)  →  L7 DECISÃO (confirmada / vistoria / descartada /
inconclusiva). Invariante: a evidência nunca desaparece e a inferência
nunca se fantasia de fato observado.
```

## Pipeline (ordem EXECUTÁVEL — `radar_pipeline.preparar_base`, X1)

F1..GATE abaixo vivem em `radar_pipeline.preparar_base()` — NÚCLEO ÚNICO
consumido por runner, Excel legado e mapa (writer nunca recalcula regra).
CH/AGR/T4/F7b são camadas do runner canônico sobre o df preparado.

```
F1    Ingestão: load tipado (str p/ CEP/COD_SETOR/NUM/COD_UNICO) →
      validar_schema (M4, ABORTA se coluna obrigatória faltar) → NUM nulo→0
      → DEDUP EXATO (W2: linha idêntica não é evidência — remove ANTES de
      qualquer estatística de frequência; duplicata não fabrica anomalia)
T0    Harmonização logr (moda CEP+TIPO+TITULO — GRAFIA apenas, R7) +
      Sanidade geo 3 CAMADAS por partição (V2: determinístico+grosseiro
      excluem; percentil = FLAG_OUTLIER_GEO informativa)
T1    Normalização semântica complementos (7 camadas) + promoção Q/L/C
      (PADRAO_ENDERECO — insumo da calibração Q/L do S2)
T2    Classificação uso (espécie oficial + léxico curado de palavras-chave)
ENR   Chaves: CHAVE = MUN|LOGR_HARM|NUM|LOCALIDADE (sem CEP, R7) +
      QTD_END/N_BLOCOS + RADAR_ID + confronto declarado×contado (N16)
F0.5  Anomalia de coleta S1-S4 (S2 por UNIDADES DISTINTAS — W1; calibrado
      p/ Q/L; S1 ignora coords nulas — V4) + S5 (só co-ocorrência S1/S3);
      decomposição N_SINAIS_PRIMARIOS / FLAG_CONTEXTO_S4 / FLAG_REFORCO_S5
T3    Coletivas POR BLOCO + reclassificações NUM=0/solo
F7    Confiabilidade 3 eixos (R7: COORD_SUPRE_CEP) — ANTES do gate (V3)
GATE  (NUM>0) & (SANIDADE=OK) & (ANOMALIA=0); GATE_CONF adiciona faixa
      ALTA+ e trava Nao_Residencial e polos
CH    Condomínio horizontal (roteamento por rótulo; medição DESCONHECIDA)
AGR   Agrupamento 1 linha/bloco + T5 DBSCAN (densidade de ENDEREÇOS, V5)
T4    Simples (2 econ.) OBSERVADAS; gaps → aba Hipoteses_Expansao (V6)
F7b   gate_pos_condicoes (M2, RadarQualityError — imune a -O) → Excel
      6 abas + <output>.manifest.json (linhagem + funil, V10)
```

Checkpoints Parquet em staging/ = ROADMAP (hoje o run é monolítico em memória).

---

## F1 — Ingestão: contrato de schema + tipagem (M4, estado v4.3)

1. **Tipagem str na leitura** (`load_cnefe`): `CEP`, `COD_SETOR`,
   `NUM_ENDERECO`, `COD_UNICO_ENDERECO` lidos como str — CEP int64 perdia zero
   à esquerda (`01001000`/SP) e quebrava o check de 8 dígitos; COD_UNICO str
   atende C5. Headers normalizados (strip de `\r`/aspas, rename de truncados).
2. **Contrato de schema executável**: `xu.validar_schema(df)` ABORTA a ingestão
   listando as colunas obrigatórias ausentes (`xu.COLUNAS_OBRIGATORIAS` —
   identificadores, logradouro, coordenada, espécie e os 5 pares NOM/VAL de
   complemento). Arquivo/separador/encoding errado morre aqui, não vira
   "qualidade 99,9%" silenciosa rio abaixo.
3. NUM nulo → 0 via `to_numeric(errors='coerce')` (insumo de R1; o split real
   de `df_zero` ocorre em T3).
4. ROADMAP: alias de header por fonte e teto percentual de coerção→null por
   coluna (`--coercao-max`).

---

## PART — Particionamento (estado real) + regime (ROADMAP)

**Implementado (M1, v4.3):** a sanidade geoespacial roda POR `COD_MUNICIPIO`
(bbox percentil próprio por município — assinatura em T0). Fraude e polos já
são partição-locais POR CONSTRUÇÃO: S1 agrupa por coordenada, S2/S3 por CHAVE
de endereço, S4 por COD_SETOR (escopo municipal embutido no código do setor),
e o DBSCAN usa raio em metros (cidades distantes nunca se encadeiam).

**ROADMAP (M3/M7):** `perfil_regime` por partição selecionando thresholds
automáticos, partição espacial quando COD_MUNICIPIO falta, e rótulo
`FLAG_LOW_GEO_BASE` por cobertura NV 1-2. A exceção Q/L do S2 (seq_min=30,
cobertura=0.98) — que era o principal ganho do regime LOTEAMENTO — JÁ é
automática dentro de `detectar_anomalia_coleta` via `PADRAO_ENDERECO`.
Enquanto o regime não existe, calibrar por CLI: `--anomalia-seq-min`,
`--anomalia-cobertura` (aliases legados: `--fraude-*`), `--dbscan-eps`
(100 m default; 150 m loteamento; 250 m rural são as referências de projeto).

---

## T0 — Sanidade geoespacial + Harmonização  [POR PARTIÇÃO]

### Sanidade: `radar_utils.sanidade_geo(df, pct=0.001, particao='COD_MUNICIPIO')` — 3 CAMADAS (V2)
Um quantil não identifica erro — define que haverá extremos (os ~600
ERRO_GEO históricos de Santa Maria ≈ as 4 caudas de 0,1% por construção).
- CAMADA 1 determinística → ERRO_GEOMETRIA: coord nula; fora do bbox
  Brasil; (0,0); lat/lon trocadas (entraria no bbox após swap).
- CAMADA 2 grosseiro → ERRO_GEOMETRIA: haversine à MEDIANA da partição
  > `SANIDADE_RAIO_GROSSEIRO_KM` (300, ajustável) — typo de grau,
  hemisfério, município errado.
- CAMADA 3 estatística → `FLAG_OUTLIER_GEO=1` INFORMATIVA (cauda
  percentil, só partições com n>=max(20,1/pct)) — revisão/priorização,
  NUNCA gate.
ROADMAP: camada territorial (ST_Covers na malha municipal + buffer 1-3 km).
ERRO_GEOMETRIA: marcado, NÃO removido da base; contabilizado no manifest.

Equivalente set-based (motor estadual/nacional — preferir a groupby/transform):
```sql
WITH b AS (
  SELECT cod_municipio,
         quantile_cont(latitude ,0.001) lat_lo, quantile_cont(latitude ,0.999) lat_hi,
         quantile_cont(longitude,0.001) lon_lo, quantile_cont(longitude,0.999) lon_hi
  FROM base WHERE latitude IS NOT NULL AND longitude IS NOT NULL
  GROUP BY cod_municipio)
SELECT c.*, CASE WHEN c.latitude  BETWEEN b.lat_lo AND b.lat_hi
                  AND c.longitude BETWEEN b.lon_lo AND b.lon_hi
            THEN 'OK' ELSE 'ERRO_GEOMETRIA' END AS SANIDADE_GEO
FROM base c JOIN b USING (cod_municipio);
```
Métrica geoespacial canônica: **haversine** (nunca fator linear global —
`M_LNG=111320·cos(lat)` único quebra em amplitude longitudinal multi-UF). Se for
preciso plano, transformar para a zona **UTM SIRGAS 2000 por centróide da partição**
(EPSG 319xx via `ST_Transform`), não um fator global.

### Harmonização: `radar_utils.harmonizar_logradouro(df)` → `NOM_SEGLOGR_HARM`
Moda de NOM_SEGLOGR por **CEP + TIPO + NOME_NORMALIZADO** (sem acento, upper,
trim; título idem). Harmoniza FLORENÇA/florença→FLORENCA, mas NUNCA funde
nomes distintos: em cidade de **CEP ÚNICO** (milhares de municípios pequenos),
a moda por CEP+TIPO puro colapsava TODOS os logradouros no nome modal — e com
a CHAVE usando HARM (N2) isso fundiria ruas diferentes no mesmo cluster
(flagrado no smoke sintético v4.3). CEP aqui é só bucket de GRAFIA (R7),
jamais decisão de identidade. Typo real de 1 letra (FLORENSA) fica para o
cruzamento (`norm_logr`/`logr_compat`) — conservador por design.
`HARM_ORIGEM ∈ {CEP, TOKEN, RAW}` = ROADMAP (M7); a coluna de saída é
`NOM_SEGLOGR_HARM`, consumida pela CHAVE (N2) e pela confiabilidade.

---

## F0.5 — Anomalia de coleta: `radar_utils.detectar_anomalia_coleta(df)` (ex-"fraude", V4)

O motor NÃO acusa fraude — sinaliza padrão ATÍPICO de coleta; fraude exige
validação externa. 5 sinais (S1 ignora coordenadas nulas — antes o hash
'nan|nan' agrupava todos os sem-coordenada e os flagrava mutuamente):

| Sinal | Descrição | Threshold | Bloqueia sozinho? |
|---|---|---|---|
| S1 | Mesma coord, 3+ logradouros distintos | 3 logr | SIM |
| S2 | Sequência perfeita (aptos sem gap) | ≥15 un, >95% cobert. | SIM |
| S3 | Estabelecimento repetido 5+ ou genérico 10+ | 5/10 ocorrências | SIM |
| S4 | Setor 100% encontrado (≥100 reg) | 99% pct_ok | NÃO (potencializador) |
| S5 | NV 4-6 + complemento rico (BL+APTO+ANDAR) | co-ocorrência S1/S3 | NÃO |

**Termos genéricos canônicos (S3):** `radar_utils.ESTAB_GENERICOS`
```
DOMICILIO PROVISORIO, CASA, RESIDENCIA, NI, SEM DENOMINACAO,
SEM IDENTIFICACAO, SEM NOME, NAO INFORMADO, DESCONHECIDO, OUTROS, VAZIO
```

**Calibração S2 para Padrão B (Q/L):** loteamentos novos podem ter cobertura
legítima alta. Se PADRAO_ENDERECO == QUADRA_LOTE: seq_min=30, cobertura=0.98.

---

## T1 — Normalização: `radar_utils.normalizar_complemento_cnefe(df)`

### LAYER_MAP completo: 60+ keywords em 7 camadas

```
AGRUPAMENTO  → PREDIO, EDIFICIO, QUADRA, PAVILHAO, CONJUNTO...
VIA_INTERNA  → ALAMEDA INTERNA, RUA INTERNA, TRAVESSA INTERNA...
BLOCO        → BLOCO, TORRE, ENTRADA, PORTAO
PAVIMENTO    → ANDAR, TERREO, SUBSOLO, COBERTURA, SOBRELOJA, LAJE
UNIDADE      → APARTAMENTO, SALA, LOJA, BOX, QUITINETE, QUARTO...
MORADIA      → CASA, LOTE, SOBRADO, SITIO, CHACARA, FAZENDA...
POSICAO      → FUNDOS, FRENTE, LADO, ANEXO, ESQ, DIR, MEIO, LATERAL
```

### Invariante testável (REGRA DE OURO do T1):
Registro 1: `NOM1=BLOCO, VAL1=A, NOM2=APTO, VAL2=101`
Registro 2: `NOM1=APTO, VAL1=101, NOM2=BLOCO, VAL2=A`
Ambos geram: `BLOCO_TIPO=BLOCO, BLOCO_VALOR=A, UNIDADE_TIPO=APARTAMENTO, UNIDADE_VALOR=101`
E o mesmo CLUSTER_BLOCO: `LOGR_HARM|NUM|CEP|BL:A`

### Promoção Q/L/C: `radar_utils.promover_quadra_lote(row)`
```
Padrão A: logradouro + número → NRO_OFICIAL = NUM_ENDERECO
Padrão B: QUADRA/LOTE/CASA   → NRO_OFICIAL = 0, CHAVE = Q15/L8/C3
```

---

## T2 — Classificação de uso (espécie oficial + léxico curado)

**Estágio 0:** COD_ESPECIE decide o macro-uso direto (1 residencial,
2 dom. coletivo, 3 agropecuário, 4 educação, 5 saúde, 7 construção,
8 religioso). Só espécie 6 ("outras finalidades") vai ao léxico.

**Estágio 1 (léxico curado, `_CNAE_RULES`):** match por substring em ORDEM
CURADA, com dois tipos: `contains` (padrão) e `exact_or_prefix` (termos
curtos/ambíguos: `BAR` não captura BARBEARIA; `POSTO` e `PET` idem — v4.3).
Colisões conhecidas resolvidas por ordem: LAVANDERIA antes de lava-rápido,
POSTO DE SAUDE antes de combustível, POSTO antes de PET. Radicais
prefixo (`DISTRIBUI`, `ADVOGAD`, `CONTABIL`, `VETERINAR`) dependem do
`contains` — NÃO converter cegamente para word-boundary.

Setores emitidos: ALIMENTACAO, INDUSTRIAL, MANUFATURA, SERVICO_AUTO,
COMERCIO_VAREJO, COMERCIO_SERVICO, BELEZA_ESTETICA, SERVICO_PROF, SAUDE,
EDUCACAO, HOSPEDAGEM, ASSOCIACAO_ONG, LAZER, VAGO, DIVERSOS (+ macro da
espécie: RESIDENCIAL, DOMICILIO_COLETIVO, AGROPECUARIO, CONSTRUCAO,
RELIGIOSO). Resíduo sem match → COMERCIO_SERVICO com detalhe bruto.

**Estágio 2 fuzzy (RapidFuzz cdist, threshold 85, tokens ≥5) = ROADMAP** —
não há fuzzy no código atual; não prometa recuperação de resíduo por
similaridade.

---

## T3 — Coletivas POR BLOCO

### SPLIT obrigatório (R1)
```python
df_valid = df[df['NUM_ENDERECO'] > 0].copy()
df_zero  = df[df['NUM_ENDERECO'] == 0].copy()
# df_zero: FLAG_COL=0, NAO_COLETIVA, sem ID_COLETIVA
# Agrupamento roda APENAS em df_valid
```

### Chave de agrupamento (harmonizada, SEM CEP — R7/N2/N14)
```
CHAVE (cluster de endereço) = COD_MUNICIPIO + '|' + NOM_SEGLOGR_HARM + '|' + NUMERO + '|' + LOCALIDADE
CLUSTER_BLOCO               = groupby(CHAVE, BLOCO_KEY)   # BLOCO_KEY = BLOCO_VALOR ou '-'
```
CEP NÃO entra na chave: é entrada digitada (R7) — CEP errado não pode quebrar
nem fundir cluster. Logradouro HARMONIZADO evita fragmentação por typo; o
prefixo de MUNICÍPIO (N14) impede colisão de `RUA A|100|CENTRO` entre cidades
num run multi-município.

### RADAR_ID_ENDERECO / RADAR_ID_BLOCO — chave numérica estável de rastreamento (N13)

A CHAVE operacional é texto e o `ID_COLETIVA` é sequencial POR RUN (muda a
cada execução). Para rastreamento FUTURO — cruzar safras da fonte, ligar a
economia inferida de hoje à confirmação de campo de amanhã, chavear em banco —
toda aba carrega uma chave numérica determinística:

```
canônica = MUN(7díg) | canon(TIPO + NOME_LOGR_HARM) | N<numero>  | canon(LOCALIDADE)
                                                    | QL <chave>   (Padrão B, NUM=0)
canon()  = caracteres PADRÃO: sem acento, CAIXA ALTA, só [A-Z0-9], espaço único
RADAR_ID_ENDERECO = BLAKE2b-8bytes(canônica) >> 1          # int64 POSITIVO (63 bits)
RADAR_ID_BLOCO    = id64(canônica + ' BL <bloco canon>')   # grão bloco/torre
```

Propriedades garantidas (testadas em `tests/test_unit.py` caso 8):
mesma entrada → mesmo id em qualquer execução/máquina/safra (BLAKE2b, nunca
`hash()` nativo — salteado por processo); acento/caixa/pontuação NÃO mudam o
id; municípios distintos geram CHAVES CANÔNICAS distintas (código embutido) e eventual colisão de hash é detectada por N13, que ABORTA o run; NUM=0 sem chave Q/L
→ id NULO (R1: sem identidade rastreável); PAR_FALTANTE herda o id do
endereço-PAI (a economia inferida rastreia o cluster que a gerou); lote Q/L
inferido (sem pai único) → nulo. Injetividade verificada pelo gate a cada run
— colisão de hash (teórica ~5e-5 em 40M endereços) ABORTA o export; remédio:
subir `digest_size` e re-emitir.

**Armazenamento:** no Excel a coluna sai como TEXTO (19 dígitos > 2^53
estouram o float64 da célula — perda silenciosa); em banco/Parquet, BIGINT.
O id é REPRODUZÍVEL a partir das próprias colunas da aba (LOGRADOURO, NUMERO,
LOCALIDADE, município = 7 primeiros dígitos do COD_SETOR) via
`canon_rastreio()` + `id64_rastreio()` — auditável por terceiros sem acesso
ao pipeline.

### 14 tipos, 3 confiança
ALTA (NV 1-2 + tipo forte), MEDIA (NV 3 ou parcial), BAIXA (NV 4-6 ou fraca).

**DUPLA_END refinado (N7):** POSICAO (frente/fundos/lado) + coordenada
VALIDADA → HORIZ_SUBDIV MEDIA; split de PAVIMENTO (TERREO/ANDAR) + VALIDADA
→ DUPLA_END MEDIA. Sem coordenada validada permanece BAIXA (doutrina R7: a
coordenada é quem confirma). Referência histórica: +555 oportunidades
"pequenas" em Canoas — re-validar no re-baseline v4.3.

### Fachada Ativa: `radar_utils.verificar_fachada_ativa(grupo)`
```
Térreo = {'', 'TERREO', '0', 'T', 'LOJA', 'G', 'SOBRELOJA'}
Comércio = SETOR_ATIVIDADE ∈ SETORES_COMERCIAIS_TERREO
Fallback: COD_ESPECIE ∈ {3,4,5,6,8} quando T2 não classificou
```

### Uso Misto: `radar_utils.verificar_uso_misto(grupo)`
COD_ESPECIE {1,2} + COD_ESPECIE {3,4,5,6,8} no mesmo endereço.

### Reclassificação pós-split
Clusters que ficaram com QTD=1 após exclusão de NUM=0 → NAO_COLETIVA.

---

## T4 — Gap analysis

### 4 tipos emitidos + three-tier Q/L: `radar_utils.gap_*`

| Tipo (aba PAR_FALTANTE) | Função | Exemplo |
|---|---|---|
| SEQUENCIA | `gap_linear()` | CASA 1,2,5 → falta 3,4 |
| GRADE_APTO | `gap_grade()` | 101,102,201 → falta 202 (grade 3-díg completa, todas ≥100) |
| POSICIONAL | `gap_posicional()` | FRENTE sem FUNDOS; DIR sem ESQ (vivo após N1) |
| QUADRA_LOTE | `gap_quadra_lote()` | Q10,Q11,Q13 → falta Q12 (3 níveis) |

### GRADUAÇÃO R9 — as 8 classes e o que cada uma significa (v4.7)

Toda hipótese sai com `EVD_CLASSE`, `EVD_GRAU`, `EVD_DISTANCIA` e
`ACT_DESTINO_CAMPO`. Ordenadas da mais forte para a mais fraca:

| `EVD_CLASSE` | `EVD_GRAU` | Regra | O que a observação garante |
|---|---|---|---|
| `A_INTERIOR_FAIXA` | SUSTENTADA | grade | interior ao intervalo do PRÓPRIO andar: 201,202,204 → 203 |
| `A_INTERIOR_SEQUENCIA` | SUSTENTADA | linear | interpolação estrita entre os extremos |
| `A_INTERIOR_QUADRA_LOTE` | SUSTENTADA | Q/L | idem, na faixa Q/L do logradouro |
| `B_ANDAR_AUSENTE` | SUSTENTADA | grade | andar INTEIRO sem rastro, cercado acima e abaixo |
| `D_POSICIONAL_SATURADO` | SUSTENTADA | posicional | toda unidade com o mesmo polo; ninguém pode ser a contraparte |
| `C_EXTRAPOLADA` (dist ≤ 2) | PLAUSIVEL | grade | além do intervalo do andar, mas perto |
| `E_POSICIONAL_ENUMERADO` | PLAUSIVEL | posicional | o polo discrimina uma enumeração (CASA 1, 2, 5 + FRENTE) |
| `C_EXTRAPOLADA` (dist ≥ 3) | ESPECULATIVA | grade | extrapolação livre |
| `F_POSICIONAL_IMPLICITO` | ESPECULATIVA | posicional | há irmão SEM descritor: ele É a frente, o par já está completo |

**`B_ANDAR_AUSENTE` era ponto cego.** A grade era o produto cartesiano dos
andares **OBSERVADOS** — um andar sem nenhum rastro é invisível para a função,
porque ela não sente falta do que não deixou vestígio. E é a hipótese mais
forte do conjunto: prédio não pula andar. Guardas obrigatórias, medidas em
POA: sem elas a classe gerava **73.796** hipóteses; com (a) unidades do andar
ausente = **INTERSEÇÃO** dos dois vizinhos, nunca a união global, e (b) teto de
30% dos andares observados (com piso de 1), cai para **190 em 121 grupos**.
Oito andares faltando não é prédio incompleto — é coleta ruim, e a resposta
certa é marcar o grupo, não fabricar 200 apartamentos.

**`gap_posicional` recebe o GRUPO, não um `set`.** FRENTE/FUNDOS é par
CONTRASTIVO: o rótulo só informa por oposição. Se algum irmão já pode exercer
a oposição, não há lacuna. A versão anterior era cega a isso — `{FRENTE}` num
endereço com 7 casas numeradas e `{FRENTE}` num endereço de 1 unidade davam a
mesma hipótese. Medido em POA sobre 17.277 endereços: 47% têm irmão sem
descritor (frente implícita), 14% têm irmão enumerado, 38% estão saturados.

**Assimetria de direção.** Entre os saturados de unidade única, FUNDOS sozinho
é 3,2× mais frequente que FRENTE sozinho (4.624 × 1.427) e logicamente mais
forte: se a única unidade do número 100 está registrada como "fundos", a
construção da frente **não virou endereço nenhum** — é economia ausente do
cadastro, não ambiguidade de anotação.

**`num_unidade()` — parse estrito antes de qualquer grade.** `pd.to_numeric`
sobre complemento de recenseador é armadilha: o CNEFE real de POA traz
`713E714` ("apartamento 713 E 714"), lido como notação científica → `+inf` →
`OverflowError` no meio de 762 mil linhas. Só passa inteiro puro de 1 a 9
dígitos. Perder uma unidade ambígua é barato; inventar um apartamento infinito
não é.

`gap_alfa()` (BLOCO A,B,D → falta C) existe como utilitário mas NÃO alimenta a
aba — integrá-lo ao `construir_simples_e_faltantes` é backlog consciente, não
omissão silenciosa. Trava dele hoje: 2× (não os 3× do GAP_MAX_RATIO).

### TERREO → andar 0: `radar_utils.normalizar_andar()`
```
TERREO_ALIASES = {'TERREO', 'T0', 'T1', 'TER', 'T', '0', 'LOJA', 'G', 'SS'}
```
Sem esta normalização, gap_grade gera falsos entre TERREO e 2º andar.

### Trava: GAP_MAX_RATIO = 3×
`len(ausentes) > 3 × len(presentes)` → descarta. Sem trava, APTO 101 + APTO 1501
inferem 1.399 ausentes.

### Confiança: `radar_utils.classificar_gap_confianca()`
```
ALTA:  posicional sempre | alfa gap=1 | numérico gap≤2 em seq≤5
MEDIA: grade apto/andar | numérico gap≤5 | Q/L gap 1-2
BAIXA: gap grande | 1 presente | numeração irregular
```

### AUSENTE_INFERIDO (modo standalone sem cross-ref)
Unidade inferida herda coordenada do centróide do bloco.
EIXO 1 (coordenada) da confiabilidade = **ZERO** (nunca fabricar GPS).
Confiança vem exclusivamente de CONF_GAP.

---

## T7 — Amostra de campo estratificada (`amostra_campo.py`)

A graduação decide o que vai a campo. Mas se o campo **só visitar o que a régua
aprovou**, a matriz de confusão fica com um quadrante vazio para sempre:

```
                    campo confirma      campo nega
régua aprovou   →   verdadeiro pos.     falso positivo
régua reprovou  →   FALSO NEGATIVO      verdadeiro neg.
                    ↑ nunca medido se a reprovada não for visitada
```

Sem a linha de baixo, `C_DIST_MAX_PLAUSIVEL` e `B_TETO_ANDARES_AUSENTES` nunca
saem do valor arbitrado. O script sorteia **deliberadamente também o que a régua
reprovou**, estratificado por `EVD_CLASSE` × faixa de `EVD_DISTANCIA`.

Sorteio **determinístico** (`SEMENTE = 20220101`): duas execuções sobre a mesma
safra produzem a mesma lista, e a lista é citável numa ordem de serviço. A saída
já sai como formulário — traz `CAMPO_EXISTE` e `CAMPO_OBSERVACAO` vazios para o
retorno, e `LAT_REFERENCIA`/`LON_REFERENCIA` com a qualidade declarada ao lado
(a hipótese não tem coordenada própria — R2 —, o ponto é do endereço-pai e é
rotulado como REFERÊNCIA para que ninguém o leia como posição da unidade).

```bash
python scripts/amostra_campo.py --rcc POA_RCC.csv --out amostra.csv \
       --por-estrato 12 --xlsx          # 12 estratos x 12 = 144 visitas
python scripts/amostra_campo.py --rcc POA_RCC.csv --out so_reprovadas.csv \
       --so-reprovadas                  # fecha só o quadrante de falso negativo
```

Estratos gerados em POA (144 casos): 4 SUSTENTADA (A_INTERIOR_FAIXA,
A_INTERIOR_SEQUENCIA, B_ANDAR_AUSENTE, D_POSICIONAL_SATURADO), 3 PLAUSIVEL
(C d1, C d2, E_POSICIONAL_ENUMERADO), 5 ESPECULATIVA (C d3, d4-5, d6-10, d>10,
F_POSICIONAL_IMPLICITO). O estrato `C|d>10` em andar de topo é **controle
negativo esperado**: cobertura tem menos unidades por projeto arquitetônico, e o
campo deve condená-lo.

---

## T6 — Bloco SET: ocupação de domicílios por setor (`ibge_setor_ocupacao.py`)

**O CNEFE não tem campo de ocupação.** São 34 colunas e nenhuma é. O candidato
aparente, `COD_TIPO_ESPECI`, é tipologia construtiva: `101` casa, `102` casa de
vila/condomínio, `103` apartamento, `104` outros.

**Mas o domicílio desocupado ESTÁ no CNEFE** — com endereço, número,
complemento e coordenada. A prova é aritmética, verificada em Porto Alegre
sobre o município inteiro:

```
CNEFE  COD_ESPECIE=1 (domicílio particular)  = 686.846 linhas
Setor  V0003 (DPPO+DPPV+DPPUO+DPIO)          = 686.846      ← exato
CNEFE  COD_ESPECIE=2 (domicílio coletivo)    =     833
Setor  V0004 (DCCM+DCSM)                     =     833      ← exato
```

`COD_ESPECIE=1` é um universo que **inclui** vagos e de uso ocasional. O IBGE
recenseou, geocodificou e publicou o endereço; apenas suprimiu o atributo de
ocupação no arquivo endereçado. A ocupação existe no agregado por setor:

| Variável | Descrição | POA |
|---|---|---|
| V0007 | ocupados | 558.607 |
| V0008 | uso ocasional (DPPUO) | 27.242 |
| V0009 | vagos (DPPV) | 100.997 |
| — | desocupados | **128.239 — 18,7%** |

**Junção:** `CD_SETOR = COD_SETOR[:15]`. O CNEFE traz 16 caracteres com sufixo
(`...2017P`); o agregado, 15. **Truncar é a única normalização válida** — nunca
concatenar o sufixo no agregado, porque ele não é constante entre UFs. Em POA:
96,8% das linhas casam; delta de 270 domicílios em 664.657 (**0,04%**).

**Poder discriminante** (setores com ≥20 domicílios): mediana 17,7%, p95 33,3%,
p99 44,6%. Correlação com verticalização = **0,393** — o quartil mais vertical
tem 22,3% de desocupação contra 13,6% no quartil de casas. Vertical desocupa
mais, e vertical é o alvo do Radar.

**O que isso NÃO faz:** não diz qual apartamento está vazio, e nunca vai dizer.
Entrega priorização de rota, não conteúdo de carteira. Ver R10 e G19.

```bash
python scripts/ibge_setor_ocupacao.py --municipio 4314902 --out setores.csv
# no emissor: acoplado por padrão; --sem-setor desliga, --cache-ibge define o cache
```

---

## T5 — DBSCAN por densidade de ENDEREÇOS: `radar_utils.detectar_polos_comerciais(df, eps_m=100, min_samples=5)` (V5)

Roda sobre a base inteira com raio em METROS (haversine): municípios distantes
não se encadeiam por construção — partição explícita é desnecessária aqui.
`eps_m` default 100 (referências de projeto: 150 loteamento / 250 rural via
`--dbscan-eps`; seleção automática por regime = ROADMAP M3).
Base AGRUPADA (1 ponto por endereço). SEM sample_weight (V5): no
scikit-learn o peso entra no critério de core point e 1 endereço com 5
estabelecimentos virava polo sozinho. min_samples = ENDEREÇOS, literal;
QTD_ESTAB caracteriza o cluster depois. Entram no clustering só endereços
com RADAR_CONF_FAIXA ALTA+ (V3). `peso_max` aceito sem efeito (compat).
Peso: `QTD_ESTAB_END.clip(upper=500)` — anti-atrator de shopping.
Filtro: apenas `SETORES_POLO` (sem DOMICILIO_COLETIVO/AGROPECUARIO).
Saída com `CLASSE_POLO`: ZONA_CENTRAL / POLO_REGIONAL / POLO_LOCAL.

```python
mask = (agg['SANIDADE_GEO'] == 'OK') & (agg['FLAG_SUSPEITA_FRAUDE'] == 0)
coords_rad = np.radians(agg.loc[mask, ['CENTROIDE_LAT','CENTROIDE_LON']].values)
pesos = agg.loc[mask, 'QTD_REGISTROS'].clip(upper=500).values
db = DBSCAN(eps=(eps_m or 100)/6_371_000, min_samples=5,      # eps do regime; por partição
            metric='haversine', algorithm='ball_tree')
agg.loc[mask, 'POLO_ID'] = db.fit_predict(coords_rad)   # SEM peso (V5)
```

---

## F6 — Gates de qualidade

```python
GATE_UNIVERSAL = (NUM > 0) & (SANIDADE == 'OK') & (FRAUDE == 0)
GATE_COLETIVA_GRANDE = GATE_UNIVERSAL & (NV 1-2) & TIPO_FORTE & (DELTA ≥ 2)
GATE_COLETIVA_PEQUENA = GATE_UNIVERSAL & (NV 1-2) & (DELTA ≥ 1)
  # DUPLA_END com compl estrutural aceito (MEDIA, não mais BAIXA)
GATE_RES_COM = GATE_UNIVERSAL & (avaliação|telefone) & (QTD_LIG_END == 1)
```

---

## F7 — Confiabilidade: `radar_utils.calcular_confiabilidade(row)`

### 3 eixos independentes

```
EIXO 1 — COORDENADA (0-40):
  NV 1→40 | NV 2→35 | NV 3→20 | NV 4→10 | NV 5→5 | NV 6→0
  INFERIDO → 0 (nunca fabricar)

EIXO 2 — ENDEREÇO TEXTUAL (0-30):
  Logr harm +10 (bruto +7) | NRO>0 +8 | Compl norm +5
  CEP 8díg +7 — OU, se ausente/inválido com NV 1-2 + sanidade OK,
  COORD_SUPRE_CEP +7 (R7: coordenada confirmada supre o CEP digitado;
  registro confirmado em campo nunca perde ponto por dígito de CEP)

EIXO 3 — CONTEXTO (0-30):
  Sanidade OK +10 | Harm CEP +8 | Sem anomalia de coleta +7 | Real (não inferido) +5

TOTAL (0-100): >75 MUITO_ALTA | >55 ALTA | >35 MEDIA | <=35 BAIXA
  (fronteiras EXCLUSIVAS: 55→MEDIA, 35→BAIXA — igual ao código e ao Dicionario)
```

Colunas: RADAR_CONF_COORD, RADAR_CONF_ENDERECO, RADAR_CONF_CONTEXTO,
RADAR_CONF_LOCALIZACAO, RADAR_CONF_FAIXA.
KML colore por CONFIABILIDADE (nunca por score).

---

## F7b — Gate executável: `radar_utils.gate_pos_condicoes(...)` (M2 — RadarQualityError, imune a `python -O`)

Roda ao fim do pipeline, ANTES de qualquer `to_excel`. As invioláveis viram
pós-condição de código; qualquer violação levanta `RadarQualityError` (via
`_exigir`, ativo mesmo sob `python -O`) e o run falha sem emitir dado.

```python
xu.gate_pos_condicoes(
    coletivas=coletivas,          # aba única (CLASSE G/P/S/PF) — colunas reais
    cond_horiz=cond_horiz, nres=nres, polos=polos_out,
    par_faltante=par_faltante,    # frame-fonte com RADAR_CONF_COORD
    df_base=df,                   # invariantes R1/R2 sobre FLAG_COL
    dicionario=dic, nomes_abas=_ABAS)
```

| Pós-condição | Regra | Falha → |
|---|---|---|
| `NUMERO>0` em toda linha OBSERVADA (exceção única: PAR_FALTANTE de QUADRA_LOTE, chave composta) | R1 | aborta export |
| `FLAG_COL==1` ⇒ anomalia 0, sanidade OK, NUM>0 (na base) | R1/R2 | aborta export |
| GRANDE ⇒ CONF ALTA; PEQUENA ⇒ ALTA/MEDIA | R3 | aborta export |
| `RADAR_CONF_COORD==0` em todo AUSENTE_INFERIDO | EIXO1 | aborta export |
| Zero `cnefe`/`ibge` em nomes de aba, colunas e valores de RÓTULO (texto livre do recenseamento é dado — isento) | R5 | aborta export |
| `GAP_AUSENTES ≤ 3×GAP_PRESENTES` | trava T4 | descarta cluster (dentro de gap_*) |

---

## Calibração observável da confiabilidade (M6 — ROADMAP)

NÃO implementado no código: o SQL abaixo é o desenho da medição para quando
houver cross-ref. Nota v4.3: `NOM_SEGLOGR_HARM` é sempre não-nulo (fallback no
bruto), então os fatores LOGR_HARM(+10) e HARM_CEP(+8) pontuam para ~todos os
registros — a discriminação real entre faixas vem de NV/sanidade/anomalia/NRO.
Recalibrar pesos é trabalho do M6, com dado, não no escuro.

Os pesos 40/30/30 e o mapa NV→pontos são heurística fixa. Numa base nova podem
inflar a faixa ALTA e o gate seguir "verde" emitindo dado não confiável. Quando há
cross-ref (cadastro/POI), medir se a faixa ainda significa o que promete:

```sql
SELECT radar_conf_faixa, count(*) n,
       avg(CASE WHEN match_cadastro THEN 1 ELSE 0 END) AS taxa_match
FROM oportunidades GROUP BY radar_conf_faixa ORDER BY 1;
```
A curva por faixa vai no `DATA_QUALITY_REPORT`. Gate de DRIFT:
`taxa_match(MUITO_ALTA) < --drift-piso` → flag `DRIFT_CONFIABILIDADE` no run
(detecta deriva do gate entre bases sem inspeção manual). Em modo standalone
(sem cross-ref) o drift é inativo, mas a distribuição por faixa é sempre emitida.

---

## Normalização de logradouro: `radar_utils.norm_logr()` + `logr_compat()`

Utilidade transversal usada em T0, T3 e F4 (cruzamento):

```python
# Remove acento, prefixo (RUA/AV/TRAV), expande abreviações (DR→DOUTOR)
norm_logr('AV. DR. SARMENTO LEITE') → 'DOUTOR SARMENTO LEITE'
norm_logr('R. FLORENÇA')            → 'FLORENCA'

# 4 níveis de compatibilidade
logr_compat(a, b) → (bool, 'EXATO'|'PARCIAL'|'CONTEM'|'PALAVRAS'|'DIFERENTE')
```

Validado em Canoas: removeu 12 falsos positivos de esquina.
Coluna de auditoria: `MATCH_LOGR` em todo output de cruzamento.

---

## Excel — 6 abas (v4.5) + manifest de linhagem

```
1. Dicionario              Descrição da planilha + dicionário de TODAS as colunas por aba
                           (ABA · COLUNA · TIPO · DESCRIÇÃO · VALORES/DOMÍNIO). Gerado por
                           `radar_utils.construir_dicionario()`
2. Coletivas               100% OBSERVADO; CLASSE discrimina:
                           · GRANDE  — coletiva formal de porte (multibloco OU PORTE≥4), vertical
                           · PEQUENA — prédio/conjunto formal menor (tipologia ALTA/MEDIA)
                           · SIMPLES — 2 economias no lote: FRENTE_FUNDOS / SOBRADO /
                                       CASA_MULTIPLA / TERREO_SUPERIOR / LADO / ANEXO / DUPLA_GENERICA
                           Schema semântico (V7): QTD_ECONOMIAS (grandeza única e agregável),
                           CONF_TIPOLOGIA, RADAR_ID_*, FLAGs, MULT_ESTAB_DECL,
                           VALIDACAO_ESTAB_DECL, CENTROIDE
3. Hipoteses_Expansao      SÓ INFERÊNCIA (V6): TIPO_HIPOTESE (POSICIONAL/SEQUENCIA/
                           GRADE_APTO/QUADRA_LOTE), EVIDENCIA, UNIDADE_INFERIDA,
                           CONF_INFERENCIA, REGRA_ORIGEM, REQUER_CONFIRMACAO=SIM,
                           RADAR_CONF_COORD=0. A lacuna pode ser praça/lote unido/numeração
                           descontinuada — NUNCA somar com abas observadas
4. Condominios_Horizontais Estrutura horizontal (3+ casas/lotes) — fora da classe coletiva.
                           SITUACAO_MEDICAO=DESCONHECIDA (estrutura não prova hidrômetro);
                           ACAO_RECOMENDADA=CRUZAR_COM_CADASTRO_COMERCIAL (V8)
5. Nao_Residencial         Espécies ≠1 e ≠7 + setor + atividade + FLAG_USO_MISTO + confronto
                           declarado×contado; RADAR_CONF_FAIXA ALTA+ obrigatória (V3)
6. Polos_Comerciais        DBSCAN por densidade de ENDEREÇOS (V5): composição setorial,
                           centróide, POLO_ID, CLASSE_POLO, POLIGONO_WKT (convex hull,
                           EPSG:4326, p/ QGIS)
+ <output>.manifest.json   Linhagem técnica (V10): fonte, SHA-256, parâmetros, versão,
                           funil completo de exclusões, avisos metodológicos
```

**Roteamento horizontal × coletiva (autoritativo = classificador).** A determinação
horizontal/vertical é do TIPO_COLETIVA. Vertical (Edifício / Condomínio vertical) e
HORIZ_SUBDIV (frente/fundos, 2 economias) PERMANECEM em Coletivas — ligação compartilhada
= alvo de individualização. Só os rótulos `HORIZ_VILA_COND`, `COND_HORIZ_MULTIBLOCOS` e
`HORIZ_MORADIAS` vão para Condominios_Horizontais. `FLAG_COND_HORIZ` (sequência de CASA)
NÃO roteia — apenas enriquece SEQ/GAP — pois marcaria prédios verticais que tenham
registros CASA, tirando alvos legítimos da classe coletiva.

A classe PAR_FALTANTE materializa o T4: GRADE_APTO exige grade 3-dígitos COMPLETA
(todas ≥100), numeração mista cai em SEQUENCIA com trava 3×; POSICIONAL infere o par
ausente (frente⇄fundos). Toda linha AUSENTE_INFERIDO herda centróide do bloco mas NUNCA
fabrica coordenada (EIXO1=0; confiança só de CONF_GAP).

Motor REAL: openpyxl (formatação rica, fills por confiança/setor, autofiltro).
xlsxwriter constant_memory p/ abas >50K = ROADMAP do motor estadual — numa UF
inteira a aba Coletivas pode passar disso; hoje o alvo é municipal.

---

## RCC — Relatório Canônico de Coletividade (v4.6, `rcc_emissor.py`)

Saída padronizada de coletividade no **grão UNIDADE**: 1 linha = 1 economia.
Existe porque as abas do runner são recortes por tema, e auditoria exige descer
até a linha que originou o número — de um agregado não se reconstrói a evidência
por unidade.

```
ESCOPO   exclusivamente o arquivo oficial de endereços e domicílios.
         Nenhuma fonte externa. CNPJ, CNAE e POI foram REMOVIDOS do modelo:
         nenhum deles nomeia um campo do layout.
GRÃO     1 linha = 1 unidade por sistema de origem. Observadas são os
         registros; inferidas vêm da análise de lacunas, com coordenada nula.
SAÍDA    <output>.csv (104 colunas na ordem do dicionário) + .manifest.json
```

**Proveniência é obrigatória.** Toda coluna declara `ORIGEM_DADO`:
`IBGE:<campo>` (13), `DERIVADO:<regra sobre campos nomeados>` (56),
`CADASTRO:<campo>` (6, bloco de confronto) ou `RCC:<regra>` (12). Campo sem
origem aborta a geração do dicionário — é o gate que impede coluna inventada de
entrar no padrão.

**Blocos** (prefixo numérico = ordem determinística; a leitura da esquerda para a
direita percorre a cadeia de inferência):

```
00 IDENT 11  quem é         45 ATV  15  o que funciona no local
10 END   14  onde está      50 EVD   6  por que achamos
20 GEO   10  geometria      60 CNF   8  quanto confiamos
25 SET    8  ocupação do    70 CAD   6  o que o cadastro diz (vazio sem cadastro)
            setor censitário
30 COL   12  o que é        80 ACT   4  o que fazer
40 UND    6  a unidade      90 AUD   4  de onde veio
                                        TOTAL 104
```

**Identificadores em 4 camadas**, legíveis e compostos:
`COL-4300604-000123-BA-APT-0101`. `UNIDADE_ID` começa por `COLETIVA_ID` —
`LIKE 'COL-…-000123%'` traz a coletiva sem join, e o gate verifica a derivação.
`COLETIVA_ID` é surrogate de store append-only (estável entre safras);
`COLETIVA_CHAVE_HASH` fica ao lado porque é reproduzível por terceiros sem o
store. Todos em TEXTO (R8). Registro sem identidade de endereço (número ausente)
sai como `SEM-<municipio>-REG-<registro>`: a unidade existe, o agrupamento não.

**Cobertura medida:** 83 de 104 campos preenchidos só com IBGE. As 8 vazias são
declaradas e isentas por nome: 6 do bloco CAD, `ORIGEM_LIGACAO` e
`ACT_PRIORIDADE` dependem do cadastro; `IMOVEL_ID` depende da resolução de
entidade (`ferramenta-logradouro-padrao`). Schema completo, valor ausente — o
consumidor sabe que não existe, em vez de descobrir que a coluna sumiu.

**19 gates** (RccError, imunes a `python -O`):

| # | Pós-condição |
|---|---|
| G1/G2 | `UNIDADE_ID` presente e derivado do `COLETIVA_ID` |
| G3/G4/G5 | INFERIDO ⇒ `CNF_COORD`=0, confirmação exigida, coordenada nula |
| G7/G8 | atividade afirmada ⇒ campo de origem nomeado e grão declarado |
| G9 | `ATV_FLAG_GAP_TARIFARIO` sempre 0: esta skill não conhece tarifa |
| G10 | valor fora de domínio fechado |
| G11 | identificador em float (R8) |
| G12 | `UNIDADE_ID` repetido — o grão deixaria de ser unidade |
| G13 | campo de GRUPO variando dentro do grupo |
| G14 | coluna OBRIGATÓRIA (do dicionário) inteiramente vazia |

**Doutrina do bloco ATV.** `ATV_GRAO_EVIDENCIA` impede a atividade de virar
inferência disfarçada: `UNIDADE` exige complemento estruturado no próprio
registro; sem ele a atividade é do `ENDERECO` — o arquivo diz que há uma padaria
naquele número, não em qual unidade.

**Desempenho medido** (180 mil linhas, 1 município): 3m22 total, gates OK. 57%
do tempo é `preparar_base` (hotspot conhecido de `apply` linha a linha — ver
Tiering). `pos_agregados` e `coletiva_ids` foram vetorizados na v4.6 (27s→1,6s e
7,4s→1,5s): `transform(lambda)` e `iterrows` sobre dezenas de milhares de grupos
são inviáveis em escala municipal.

---

## Outros formatos

| Formato | Motor | Status |
|---|---|---|
| Excel 6 abas + manifest .json | openpyxl (`radar_potenciais.py`) | IMPLEMENTADO |
| Leaflet HTML | `cnefe_mapa.py` + map_template.html (payload gateado por sanidade+anomalia) | IMPLEMENTADO |
| GeoParquet 1.0.0 | pyarrow + shapely WKB + Zstd | ROADMAP |
| GeoPackage | pyogrio (4 layers) | ROADMAP |
| KML | lxml (CDATA nativo), colorido por RADAR_CONF_FAIXA | ROADMAP |

O `POLIGONO_WKT` da aba Polos_Comerciais já é carregável no QGIS sem
dependência extra (WKT EPSG:4326) — é a ponte geoespacial disponível hoje.


---

## Volumetria de referência (fixtures de regressão / golden tests)

**RE-BASELINE PENDENTE (v4.3):** N2 (CHAVE harmonizada) funde clusters antes
fragmentados por typo — QTD_END sobe, contagens de coletivas mudam. Na próxima
rodada real de Canoas e Santa Maria, gravar os novos números como baseline;
até lá os valores abaixo são referência de ORDEM DE GRANDEZA, não trava dura.

```
                      Canoas/RS    Santa Maria/RS
Entrada:              176.899      149.483
Fraude detectada:     ~4%          1.162 (0,8%)
Sanidade ERRO_GEO:    —            600
Logr. harmonizados:   —            13.919
Padrão Q/L:           —            1.066 (917 com casa)
Coletivas ALTA:       48.495       53.989
Polos comerciais:     —            133 (1 central, 11 regionais)
Pipeline completo:    ~30s         19s
```

---

## Parâmetros (CLI REAL do runner `radar_potenciais.py`)

| Parâmetro | Default | Descrição |
|---|---|---|
| `--input` | obrigatório | Arquivo bruto (1 município, UF ou multi-UF; .csv ou .csv.gz) |
| `--output` | obrigatório | Excel de saída (6 abas) + <output>.manifest.json |
| `--municipio` | None | Filtro opcional — processa só 1 código de município |
| `--sanidade-pct` | 0.001 | Percentil do bbox de sanidade (POR PARTIÇÃO de município) |
| `--anomalia-seq-min` | 15 | S2: mínimo de UNIDADES DISTINTAS em sequência (Q/L usa max(valor,30) automático). Alias legado: `--fraude-seq-min` |
| `--anomalia-cobertura` | 0.95 | S2: cobertura mínima (Q/L usa max(valor,0.98) automático). Alias legado: `--fraude-cobertura` |
| `--dbscan-eps` | 100 | Raio DBSCAN em metros (referências: 150 loteamento / 250 rural) |
| `--dbscan-min` | 5 | Mín. endereços por polo (literal desde V5) |
| `--dbscan-peso-max` | 500 | SEM EFEITO desde v4.5 (compat) |
| `--geo-raio-grosseiro` | 300 | km à mediana da partição p/ ERRO grosseiro (dívida aberta → malha municipal) |
| `--source-dataset` | None | Declaração de fonte p/ o manifest (senão inferência por nome ou UNKNOWN) |
| `--source-vintage` | None | Safra declarada da fonte p/ o manifest |

Constantes de código (sem CLI): `GAP_MAX_RATIO=3.0`, `COND_HORIZ_MIN=3`.
ROADMAP de CLI (NÃO existem hoje): `--particao`, `--regime`, `--formato`,
`--standalone`, `--fuzzy-threshold`, `--gap-max-ratio`, `--coercao-max`,
`--low-geo-piso`, `--drift-piso`.

Entrypoints auxiliares (v4.5.2: ambos consomem `radar_pipeline.preparar_base`
— X1; zero regra própria): `cnefe_coletivas.py --input --municipio --output
--pickle` (Excel legado 4 abas, R5 saneado, nres com faixa F7 ALTA+) e
`cnefe_mapa.py --input --municipio --output --cidade` (HTML, payload gateado,
não-residencial com faixa F7 ALTA+).

---

## CHECKLIST DE ACEITE (v4.3 — cada item confere com o código)

```
GARANTIDO PELO GATE EXECUTÁVEL (RadarQualityError — testado sob python -O):
[x] Nao_Residencial 100% com RADAR_CONF_FAIXA ALTA+ (F7 é gate, V3)
[x] Hipóteses com REQUER_CONFIRMACAO=SIM e RADAR_CONF_COORD=0 (V6)
[x] Nenhuma linha NUM=0 em aba de oportunidade OBSERVADA; exceção única
    PAR_FALTANTE de QUADRA_LOTE (chave composta) (R1)
[x] Nenhuma FLAG_ANOMALIA_COLETA=1 ou sanidade!=OK com FLAG_COL=1 (R2)
[x] GRANDE só com CONF ALTA; PEQUENA só ALTA/MEDIA (R3)
[x] AUSENTE_INFERIDO com RADAR_CONF_COORD = 0 (coord nunca fabricada)
[x] Zero cnefe/ibge em abas, colunas e rótulos gerados (R5; texto livre
    do recenseamento é dado, isento)

GARANTIDO POR CONSTRUÇÃO (verificado em auditoria + suíte de 17 testes):
[x] NÚCLEO ÚNICO radar_pipeline.preparar_base — runner, coletivas e mapa
    consomem a MESMA ordem dedup→anomalia→F7→gate (X1; teste 15)
[x] Percentil geo NUNCA exclui — só FLAG_OUTLIER_GEO; ERRO é determinístico
    + grosseiro por mediana (V2)
[x] S1 ignora coordenadas nulas — fim do grupo 'nan|nan' (V4)
[x] DBSCAN sem sample_weight — min_samples = endereços literal (V5)
[x] Gap Q/L agrupa por (COD_SETOR, LOGRADOURO) — CEP só descritivo (V9/R7)
[x] Manifest de linhagem com funil completo por run (V10)
[x] Entrypoint legado com sanidade+anomalia (V11)
[x] NUM=0 forçado NAO_COLETIVA antes do agrupamento; agrupar só NUMERO>0 (R1)
[x] Dedupe hash completo na INGESTÃO, antes de qualquer estatística (W2);
    nunca por coordenada ou parcial (R6)
[x] CHAVE = MUN|LOGR_HARM|NUM|LOCALIDADE — sem CEP, com município (R7/N2/N14)
[x] RADAR_ID_ENDERECO/BLOCO em toda aba; determinístico, injetividade no
    gate, TEXTO no Excel, PAR_FALTANTE herda id do pai (N13)
[x] CEP inválido/ausente não penaliza NV 1-2 + sanidade OK (COORD_SUPRE_CEP, R7)
[x] Sanidade geo POR PARTIÇÃO de município, regime duplo por volume (M1/N11)
[x] Harmonização por CEP+TIPO+NOME_NORM — cidade de CEP único não funde ruas (N2)
[x] NUM_ENDERECO int garantido na ingestão p/ todos os entrypoints (N12)
[x] validar_schema aborta ingestão com coluna obrigatória faltando (M4)
[x] TERREO normalizado → andar 0 antes de gap_grade
[x] GRADE_APTO exige grade 3-díg completa (todas >=100); trava 3× nos gaps
[x] DUPLA_END com PAVIMENTO split + VALIDADA → MEDIA (N7)
[x] S2 calibrado p/ Q/L automático (seq_min=30, cobertura=0.98)
[x] S4 só potencializador; S5 só com co-ocorrência S1/S3
[x] promover_quadra_lote captura CASA sem exigir LOTE (C1)
[x] detectar_anomalia_coleta vetorizado — sem loop por groupby (C2)
[x] Polos sem DOMICILIO_COLETIVO/AGROPECUARIO (C3); CLASSE_POLO presente (C4)
[x] COD_UNICO_ENDERECO tipado str na ingestão (C5)
[x] COND_HORIZ_CASAS e COND_HORIZ_LOTES gateados por nunique (N8)
[x] COND_HORIZ_LOTES só quando CHAVE_QUADRA_LOTE tem /L
[x] LAYER_MAP fonte única — ESQ/DIR/MEIO/LATERAL vivos em POSICAO (N1)
[x] Mapa HTML exclui geo insana e anomalia de coleta do payload (N9)
[x] FLAG_USO_MISTO e FLAG_FACHADA_ATIVA na aba Coletivas; FLAG_USO_MISTO
    também em Nao_Residencial (N10)
[x] R4 (DELTA_REAL) — N/A em standalone; aplica no cruzamento com cadastro

GARANTIDO PELO EMISSOR RCC (v4.6, 32 testes — metade negativos):
[x] R8: identificador nunca em float; varredura por PADRÃO de nome no gate
[x] Integridade referencial hipótese → endereço-pai (o PDCA-01 não volta)
[x] UNIDADE_ID único por origem; registro sem número vira SEM-<mun>-REG-<reg>
[x] Campo de GRUPO constante dentro do grupo (forma = a mais forte do grupo)
[x] Atividade só afirmada com campo do layout nomeado e grão declarado
[x] ATV_FLAG_GAP_TARIFARIO sempre 0 sem cadastro — não se afirma tarifa
[x] Coluna OBRIGATÓRIA vazia aborta; a lista vem do dicionário, não de
    isenção mantida a mão (a 1ª versão foi calibrada na fixture e reprovava
    base legítima sem blocos ou sem coordenada)
[x] Domínio em CÓDIGO, não em rótulo de exibição: rótulo muda com a redação
[x] Bordas cobertas: sem coletiva, todos NUM=0, unidade duplicada, sem
    coordenada, só estabelecimento, 1 registro, quadra/lote

PENDENTE / ROADMAP (não afirmar como existente):
[ ] Re-baseline das fixtures Canoas/Santa Maria pós-N2 (PRIORITÁRIO)
[ ] IMOVEL_ID no RCC: exige resolução de entidade (ferramenta-logradouro-padrao)
[ ] Bloco CAD do RCC: exige o cadastro da concessionária
[ ] FLAG_CLUSTER_DISPERSO: raio intra-chave existe no RCC (GEO_RAIO_GRUPO_M)
    mas ainda não é gate — falta a curva de falso positivo no golden
[ ] gap_alfa integrado à aba PAR_FALTANTE
[ ] SCORE_NUMERACAO e ENDERECO_REAL/DECLARADO nas abas (utilitários prontos,
    não chamados pelo runner)
[ ] perfil_regime (M3), coerção % (M4), LOW_GEO/HARM_ORIGEM (M7), drift (M6)
[ ] KML/GeoParquet/GPKG; xlsxwriter constant_memory; tiering Polars/DuckDB
[ ] norm_logr/logr_compat/MATCH_LOGR — usados só em cruzamento (F4, fora do
    runner standalone)
```

---

## Determinismo + tiering de engine (M5 — estado real)

Determinismo FUNCIONAL garantido hoje: mesma entrada → mesmos valores.
groupby ordena chaves, `mode()` desempata alfabético, agregações são
determinísticas; DBSCAN recebe a base agrupada por CHAVE (ordem estável).
Byte-a-byte de .xlsx NÃO é garantido (openpyxl grava metadado de timestamp) —
compare CONTEÚDO (valores por aba), não hash do arquivo.

ROADMAP M5: sort explícito `(COD_MUNICIPIO, COD_UNICO_ENDERECO)` pré-DBSCAN
como cinto extra e export canônico (Parquet) para diff byte-a-byte real.

Tiering por volume (direção de projeto — HOJE só o tier Pandas existe):

| Volume | Engine | Status |
|---|---|---|
| < 500K | Pandas | IMPLEMENTADO (base municipal) |
| 500K–5M | Polars lazy + projeção de coluna | ROADMAP (ETL estadual) |
| > 5M | DuckDB streaming + Parquet particionado por município | ROADMAP (nacional) |

Hotspots conhecidos p/ o tier estadual (vetorizar antes de escalar):
`classificar_uso`/`classificar_coletiva`/`calcular_confiabilidade` (apply
linha a linha), loop de `construir_simples_e_faltantes` e varreduras por
CHAVE em `detectar_condominio_horizontal`.

---

## Dependências

Pinadas em `scripts/requirements.txt` (V13) — major travado; major novo de
pandas/sklearn exige re-rodar a suíte e as fixtures antes de promover.

```
OBRIGATÓRIAS (pipeline atual):
pandas, numpy                  # Core
openpyxl                       # Excel 6 abas (motor real)
scikit-learn                   # DBSCAN (import lazy dentro de T5)

ROADMAP (instalar só quando o recurso existir):
rapidfuzz                      # Fuzzy T2 estágio 2
polars, duckdb                 # Tiers estadual/nacional
pyarrow, shapely               # GeoParquet WKB 1.0.0
lxml                           # KML com CDATA nativo
pyogrio                        # GeoPackage
```

---

## SCORE_NUMERACAO — `radar_utils.calcular_score_numeracao(df)`

**STATUS: utilitário pronto, NÃO chamado pelo runner** (integrá-lo às abas é
backlog — custo de coluna e de cômputo por rua). Use ad-hoc quando precisar.

Score de consistência de numeração predial (0-100) por registro.
Detecta erros de recenseamento sem depender exclusivamente de NV_GEO_COORD.

### 4 componentes independentes

| Componente | Max | Regra |
|---|---|---|
| N1_ORDEM | 30 | Número cresce junto com a projeção espacial ao longo da rua (Spearman simplificado) |
| N2_PAR_IMPAR | 25 | Paridade (par/ímpar) compatível com a maioria dos vizinhos da mesma rua |
| N3_DISTANCIA | 25 | Intervalo para o vizinho mais próximo ≤ 2× mediana da rua |
| N4_FACE_VIA | 20 | Paridade compatível com NUM_FACE (face 1=par, face 2=ímpar, convenção IBGE) |

### Faixas

```
ALTA           ≥ 75  → numeração confiável
MEDIA          50-74 → pequena inconsistência
BAIXA          25-49 → suspeita de erro
INCONSISTENTE  < 25  → provável erro de digitação ou inversão
```

### Colunas geradas

```
SCORE_NUM_N1, SCORE_NUM_N2, SCORE_NUM_N3, SCORE_NUM_N4
SCORE_NUMERACAO   (0-100, soma dos 4)
SCORE_NUM_FAIXA   (ALTA / MEDIA / BAIXA / INCONSISTENTE)
```

Registros com NUM=0 ou sem coordenada recebem score=0 / INCONSISTENTE.
Ruas com 1 único registro recebem score=100 (sem base de comparação).

**Integração com gates:** `SCORE_NUM_FAIXA == 'INCONSISTENTE'` pode substituir
ou reforçar o sinal de anomalia S1 em registros isolados onde S1 não dispara.

---

## ENDERECO_PROVAVEL — `radar_utils.construir_endereco_provavel(df)`

**STATUS: utilitário pronto, NÃO chamado pelo runner** — desenhado para
cruzamento com cadastro comercial (CHAVE_MATCH). Lembrar R7 ao cruzar:
match por logradouro+número+coordenada, nunca por CEP.

Reconstrói duas versões de endereço por registro.

### ENDERECO_DECLARADO — raw normalizado

Endereço como veio no CNEFE, com limpeza mínima (remove "nan" de títulos nulos):

```
RUA ERNESTO PEREIRA, 698 — APARTAMENTO 404
AVENIDA PRESIDENTE VARGAS, 104 — TERREO
RODOVIA BR 392 — S/N
```

### ENDERECO_REAL — reconstrução canônica

Versão totalmente normalizada com:
- Logradouro harmonizado (CEP moda)
- Número resolvido (padrão A) ou chave Q/L/C (padrão B)
- Complemento em ordem canônica com rótulos curtos (BL, APT, Casa, Fundos...)

```
RUA ERNESTO PEREIRA, 698 — APT 404
AV PRESIDENTE VARGAS, 104 — Terreo
RODOVIA BR 392 — S/N
RUA CAMOBI, Q15/C3
```

### Uso prático

```python
df = xu.construir_endereco_provavel(df)
# Integração com cadastro comercial:
df['CHAVE_MATCH'] = df['ENDERECO_REAL'].str.upper().str.strip()
# Deduplicação cross-source:
df_poi['CHAVE_MATCH'] = df_poi.apply(lambda r: norm_logr(r['logradouro']) + ... )
```

---

## CONDOMÍNIO HORIZONTAL — `radar_utils.detectar_condominio_horizontal(df)`

Detecta condomínios horizontais — padrão frequentemente perdido pelo foco
em verticalização. Alta oportunidade comercial: muitos são loteamentos
planejados recentes sem ligações individualizadas.

### 3 padrões reconhecidos

**COND_HORIZ_CASAS** — sequência de casas no mesmo endereço:
```
CASA 01, CASA 02, CASA 03 ... → min 3 casas únicas
Confiança ALTA se ≥5 casas e cobertura ≥80%
```

**COND_HORIZ_LOTES** — lotes sequenciais na mesma quadra:
```
Q15/L8, Q15/L9, Q15/L10 ... → min 3 lotes únicos
Detectado via CHAVE_QUADRA_LOTE
```

**COND_HORIZ_MISTO** — casas + posições (FRENTE/FUNDOS) no mesmo endereço:
```
CASA 1 + FRENTE + FUNDOS ... → min 3 registros
```

### Colunas geradas

```
FLAG_COND_HORIZ       (0/1)
TIPO_COND_HORIZ       COND_HORIZ_CASAS / COND_HORIZ_LOTES / COND_HORIZ_MISTO
COND_HORIZ_SEQ_MIN    menor número da sequência
COND_HORIZ_SEQ_MAX    maior número
COND_HORIZ_QTD        unidades únicas detectadas (≥3 garantido)
COND_HORIZ_GAP        ausentes na sequência (exibido na aba Condominios_Horizontais)
COND_HORIZ_CONF       ALTA / MEDIA / BAIXA
```

### Roteamento e integração com a saída

Condomínio horizontal NÃO entra na classe coletiva: a aba `Condominios_Horizontais`
recebe os endereços rotulados `HORIZ_VILA_COND` / `COND_HORIZ_MULTIBLOCOS` /
`HORIZ_MORADIAS` pelo classificador (TIPO_COLETIVA), com medição provavelmente
individualizada por casa. `FLAG_COND_HORIZ` (sequência de CASA) NÃO decide o
roteamento — só enriquece `COND_HORIZ_SEQ_MIN/MAX/GAP` na aba — pois marcaria prédios
verticais com registros CASA. A economia ausente inferida (classe PAR_FALTANTE da aba
Coletivas) vem do gap em coletivas + Q/L via `construir_simples_e_faltantes`, não de
`COND_HORIZ_GAP`.

### Volumetria Santa Maria/RS (aba Condominios_Horizontais)

```
Roteados por rótulo do classificador: 217 blocos
  Múltiplas moradias (casa/lote/sobrado): 132
  Vila ou condomínio horizontal (casas):   77
  Condomínio horizontal multiblocos:         8
FLAG_COND_HORIZ marcou 2.611 registros de sequência CASA (só p/ enriquecer SEQ/GAP)
```
