---
name: extracao-cadastur-mtur
description: >
  Extrai o Cadastur/MTur INTEIRO — 15 atividades turísticas × série trimestral
  completa (742 recursos, 2006→hoje) — direto da API CKAN do Ministério do
  Turismo, SEM PERDA DE NENHUM DADO: toda coluna de origem vira coluna canônica
  ou vai íntegra para `_extras` (JSON), e todo rótulo novo é registrado. Reconcilia
  as TRÊS gerações de layout (CSV legado com endereço estruturado, XLS OLE,
  XLSX moderno com endereço em texto livre), detecta o formato pelos BYTES e não
  pelo rótulo do portal (recursos marcados "CSV" entregam .xlsx), carimba
  procedência e sha256 em toda linha, e entrega Parquet + CSV + DDL PostgreSQL/
  PostGIS + elo por CNPJ para geocodificação CNEFE. Acione para "baixar Cadastur",
  "base de meios de hospedagem", "pousadas e hotéis cadastrados", "prestadores de
  serviços turísticos", "dados abertos do turismo", "quantos leitos tem a cidade",
  "série histórica do Cadastur", "guias de turismo", "agências de turismo",
  "restaurantes cadastrados no MTur", "hospedagem por município", "UH e leitos",
  "atualizar a base do turismo". Fonte de RÓTULO de alta precisão para gap
  tarifário, recadastramento e estudo de mercado imobiliário/temporada.
  CORSAN/Aegea, COPEL, SABESP, SANEAGO, COPASA, Enel e similares.
---

# extracao-cadastur-mtur

Camada de **ingestão** do Cadastro de Prestadores de Serviços Turísticos (Lei
11.771/2008). Produz base analítica auditável; **não** geocodifica e **não**
classifica — isso é `tratamento-cnpj` e `tratamento-orquestrador-comercial`.

**Precedência:** `diretrizes-a2l` vale integralmente. Em conflito de contrato de
saída, vence o consumidor (`tratamento-cnpj`).

**Versão 3.0.0** — 2026-08-22.

## REGRAS INVIOLÁVEIS

```
R0 — ZERO PERDA É INVARIANTE, NÃO META
     Coluna de origem sem mapa NÃO é descartada: vai íntegra para `_extras`
     (JSON) e entra em colunas_desconhecidas.csv. Aliases concorrentes que
     convergem para a mesma coluna canônica também preservam os valores
     secundários em `_extras`. CSV irregular nunca usa `on_bad_lines=skip/warn`:
     linhas ragged são alargadas e preservadas. O funil fecha por recurso:
     linhas_origem == linhas_saída, delta zero.

R1 — NADA HARDCODED NA FONTE
     Datasets e URLs vêm de package_search?fq=tags:Cadastur. O portal já
     renomeou arquivos e trocou de formato duas vezes; URL fixa = skill morta
     em silêncio.

R2 — FORMATO VEM DOS BYTES
     O campo `format` do CKAN mente: recursos rotulados CSV entregam .xlsx
     desde 2022 (ex.: "Terceiro Trimestre 2022CSV" → meio-de-hospedagem-.xlsx).
     Magic bytes decidem: PK→xlsx, D0CF11E0→xls, '<'→html, resto→csv.

R3 — PROCEDÊNCIA EM TODA LINHA
     _dataset, _recurso_id, _ref_periodo, _sha256, _linha_origem, _formato_real
     e mais 9 campos. Linha sem carimbo de snapshot é dado órfão.

R4 — CNPJ NÃO É CHAVE GLOBAL
     Repete entre trimestres (é uma série) e entre atividades (o mesmo CNPJ
     pode ser hotel e restaurante). Guia de Turismo é PESSOA FÍSICA e pode não
     ter CNPJ. PK física = (_recurso_id, _linha_origem).

R5 — LOCALIZAÇÃO FÍSICA DA ORIGEM É PARTE DO DADO
     Excel é lido por TODAS as abas, com aba e linha física preservadas.
     HTML preserva tabela e linha. Cabeçalho vazio/duplicado recebe identidade
     sintética rastreável em vez de ser sobrescrito.

R6 — O SNAPSHOT JÁ VEM FILTRADO
     Só Regular e Em Implantação aparecem. CNPJ que some entre dois trimestres
     perdeu regularidade — a saída é evento, não erro. Nunca reconcilie por
     "sumiu, então apaga".
```

## Como rodar

```bash
python cadastur_extrai.py --saida ./out
python cadastur_extrai.py --saida ./out --datasets meios-de-hospedagem --desde 2024
python cadastur_extrai.py --saida ./out --workers-download 6 --workers-transform 4
python cadastur_extrai.py --saida ./out --cnpj-geocod cnpj_cnefe.parquet
python cadastur_extrai.py --saida ./out --nova-execucao           # ignora run incompleto
python scripts/doctor.py                                         # dependências + disco
python scripts/selftest.py --saida ./out                          # gate
python scripts/e2e_sintetico.py                                  # E2E sem rede
```

Filtros (`--datasets/--desde/--ate`) definem explicitamente o escopo da execução.
O cache é por `recurso_id`, revalidado por SHA-256 e reaproveitado entre recortes.
Downloads podem ser paralelos (`--workers-download`, default 4). A transformação
agora é **incremental por recurso**: cada recurso vira um shard Parquet transacional
com SHA-256 próprio e checkpoint em SQLite/WAL. O bronze inteiro nunca é concatenado
em RAM. Em falha, a próxima execução do mesmo escopo e mesmo fingerprint de catálogo
retoma somente os recursos pendentes; shard ausente/corrompido é invalidado e
reprocessado. Se o catálogo do MTur mudar no intervalo, a execução antiga não é
misturada com a nova. Arquivos finais são trocados apenas depois da materialização
completa. Shards de checkpoint são removidos após sucesso por padrão para não duplicar
armazenamento (`--manter-shards` existe apenas para diagnóstico).

## Saída

| Artefato | Conteúdo |
|---|---|
| `bronze_parquet/<dataset>/<dataset>_<ano>.parquet` | bronze completo, texto, 1 arquivo por dataset×ano |
| `bronze_cadastur.csv.gz` | mesmo bronze, `;` + QUOTE ALL, para `\copy` |
| `ddl_cadastur.sql` | schema `cadastur`, tabela bronze, `_extras` jsonb + GIN, views `v_prestador` (projeção tipada não destrutiva) e `v_vigente` |
| `chaves_cnpj.csv` | contrato para `tratamento-cnpj` (CNPJ distintos) |
| `cadastur_geo.parquet` + `ddl_elo_geo.sql` | elo geocodificado EPSG:4674, GiST (só com `--cnpj-geocod`) |
| `geo_conflitos.csv` / `geo_rejeitadas.csv` | coordenadas ambíguas ou inválidas; nunca há escolha arbitrária silenciosa |
| `funil.csv` | por recurso: origem, saída, delta |
| `colunas_desconhecidas.csv` | rótulo novo → recurso onde apareceu |
| `inventario_pii.csv` | classificação LGPD coluna a coluna |
| `mapa_colunas.csv` | rótulo de origem → coluna canônica (as 3 gerações) |
| `manifesto.jsonl` | 1 linha por recurso: url, sha256, bytes, formato declarado × real, encoding, abas, linhas |
| `schema_drift.csv` | mudanças de colunas contra o baseline do **mesmo escopo lógico** |
| `eventos_entidade.csv.gz` | comparação temporal: `ENTROU`, `SAIU`, `PERMANECEU`, `ALTEROU` |
| `historico/<run_id>/` | manifesto, funil, drift, eventos e resumo de cada execução concluída |
| `.state/estado.sqlite` | journal transacional/checkpoints; SQLite WAL |
| `resumo.json` | gates, hashes, totais e métricas de retomada/histórico |
| `reconciliacao.json` | prova ponta a ponta: origem → funil → Parquet → CSV → manifesto |
| `artefatos_sha256.csv` | hash SHA-256 e tamanho de cada artefato material |
| `qualidade_campos.csv` | completude/cardinalidade aproximada/classificação LGPD por campo |
| `quarentena.csv.gz` | anomalias por PK, regra e severidade; não remove linha do bronze |
| `metricas_operacionais.json` / `metricas.prom` | observabilidade operacional + Prometheus textfile |

Retorno: `0` funil fechado · `2` delta ≠ 0 · `1` nada lido.

## Decisões que valem conhecer antes de usar

- **Não geocodifica.** O endereço moderno é texto livre (número em 74%, CEP em
  55,5%, e casos como `Rua do Bonfim  Pirenópolis`). O elo entra por CNPJ, que é
  chave limpa. O **legado 2006–2022 é mais geocodificável que o atual** — tem
  CEP, UF, LOCALIDADE, BAIRRO e LOGRADOURO em colunas separadas; a geração XLSX
  colapsou tudo em uma string.
- **UH e leitos são autodeclarados.** No 2T/2026: 64 não-numéricos, 9 zerados,
  20 com leitos < UH, máximos de 8.500 UH e 17.000 leitos. Use com `uh_num`/
  `leitos_num` da view e trate outlier como suspeita, não como medida.
- **Regular ≠ certificado válido.** A view expõe `validade_dt` com parser seguro
  para ISO e `DD/MM/YYYY`; valores fora desses padrões não quebram a view e viram NULL.
  O gate operacional deve comparar `validade_dt >= current_date`.
- **Recall baixo.** Obrigatoriedade legal ≠ adesão; temporada informal não está
  aqui. É rótulo de alta precisão, nunca censo.
- **ODbL tem share-alike.** Consumir e publicar o fato derivado é seguro;
  redistribuir a base derivada arrasta a licença. Atribuição: Cadastur/MTur.

## Melhorias estruturais da v3.0.0

- transformação concorrente por recurso (`--workers-transform`) além dos downloads paralelos, mantendo consolidação final determinística;
- reconciliação ponta a ponta entre contagem da origem, funil, Parquet, CSV e manifesto; divergência bloqueia promoção do run;
- `artefatos_sha256.csv` + `artifact_set_sha256` para prova de integridade do conjunto materializado;
- Data Quality não destrutivo com regras de CNPJ/CPF, UF, CEP, métricas numéricas e coerência UH/leitos;
- quarentena por evidência, nunca por exclusão: a linha problemática continua no bronze e é referenciada pela PK física;
- valores pessoais na quarentena são mascarados e acompanhados por hash, evitando replicação desnecessária de PII;
- DDL PostgreSQL idempotente/aditivo: não existe mais `DROP TABLE` na evolução normal do schema;
- carga PostgreSQL opcional via `--postgres-dsn`, usando tabela de staging temporária, UPSERT por PK e reconciliação antes do commit;
- observabilidade com `metricas_operacionais.json` e formato Prometheus textfile;
- E2E sintético reproduzível e sem rede (`scripts/e2e_sintetico.py`), inclusive campo CSV com quebra de linha quoted;
- `--dq-falha-em-erro` permite converter anomalias de severidade ERRO em gate bloqueante conforme política de produção.

## Melhorias estruturais da v2.0.0

- processamento por recurso e consolidação em streaming; não existe mais `pd.concat` do bronze completo;
- checkpoint transacional em SQLite/WAL, com retomada automática de run incompleto;
- SHA-256 do **arquivo de origem** e do **shard de checkpoint**; corrupção de shard força reprocessamento;
- separação entre `scope_hash` (recorte lógico) e `catalog_hash` (estado físico do catálogo); catálogo alterado não contamina uma retomada;
- shards temporários removidos após sucesso para impedir crescimento silencioso de disco;
- histórico leve por `run_id`, sem duplicar o bronze;
- baseline de schema isolado por escopo e commitado somente depois de o run terminar com todos os artefatos consistentes;
- `schema_drift.csv` detecta coluna adicionada/removida sem comparar recortes incompatíveis;
- snapshot compacto da entidade vigente e eventos `ENTROU`/`SAIU`/`PERMANECEU`/`ALTEROU`;
- fingerprint de entidade ignora procedência volátil e mede mudança do conteúdo canônico;
- elo geográfico lê apenas as colunas necessárias dos Parquets consolidados;
- preservadas todas as garantias da v1.1.0: CSV ragged, aliases concorrentes, linha física, escrita atômica, casts defensivos, chave de entidade e gate de coordenadas ambíguas.

## LGPD

A extração carrega **todos** os campos, inclusive os pessoais — decisão do
usuário, registrada. A skill não suprime: **documenta**, em `inventario_pii.csv`.
Ler `references/LGPD_INVENTARIO.md` antes de compartilhar qualquer saída com
terceiro. O ponto que muda a classe de risco: o dataset de Guias de Turismo é de
pessoas naturais e traz `Tipo Sanguíneo` — dado pessoal **sensível** (saúde,
LGPD art. 5º II) — além de nome, CPF, data de nascimento, sexo, nacionalidade e
documento de identificação.

## Referências

- `references/DICIONARIO.md` — dicionário do layout moderno medido no 2T/2026 e
  as três gerações lado a lado.
- `references/LGPD_INVENTARIO.md` — classificação por coluna e o que muda ao
  compartilhar.
