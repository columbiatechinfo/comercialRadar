> **NOTA v3.0.0 (2026-08-09).** Este documento registra a metodologia da execução do RS
> em 26/06/2026, com os parâmetros da época (`--min-conf 0.5`, dedup por nome + raio
> 30 m, `bairro` recebendo o `locality` do Overture). Esses três pontos foram
> **revistos** na v3.0.0 — ver `MIGRACAO_V2_V3.md`. O método de coleta (fetch-once,
> clip set-based, shards densos do FSQ, `.pbf` no DuckDB) permanece válido.

# Metodologia + Melhorias para as Skills
**Projeto:** Extração de POIs do Rio Grande do Sul (exceto Porto Alegre) + Mapa A2L
**Data:** 26/06/2026 · **Skills tocadas:** `extracao-dados-poi-global`, `roteirizacao-simulacao-mapas`/`mapas-a2l`

> Este documento consolida a metodologia aplicada e **tudo que foi aprendido** durante a execução,
> em formato pronto para **atualizar as skills**. Observação: nesta sessão as skills são um cache
> somente-leitura — para gravar as mudanças, aplicar via **Configurações ▸ Capacidades** (ou pela
> skill `skill-creator`). Os scripts de produção referenciados estão em `./scripts/`.

---

## 1. Metodologia aplicada (ponta a ponta)

1. **Limites oficiais IBGE** (parquet `divisoes_territoriais_BR.parquet`, EPSG 4674→4326). Alvo = 497
   municípios do RS **menos Porto Alegre** (COD 4314902). Atenção: a malha traz **499 feições** de RS
   (497 municípios + 2 "lagoas": Lagoa dos Patos 4300001 e Lagoa Mirim 4300002) — inofensivas (sem POIs),
   mas devem ser contadas como unidades territoriais, não como municípios.
2. **Coleta set-based, cada fonte UMA vez no bbox do RS** (e não município a município):
   - **Overture Places**: via CLI `overturemaps` (S3 anônimo), por **tiles ~1°** (grade 8×7 = 56).
   - **OpenStreetMap**: **dump `.pbf` da macrorregião Sul** (Geofabrik, ~399 MB) lido no **DuckDB
     `ST_ReadOSM`** — nós-POI + centroide de ways (resolução de refs por SEMI JOIN).
   - **Foursquare OS Places**: parquet no **Hugging Face**; os shards são **clusterizados por
     geografia** — só **3 de 100** continham dados do RS (529.931 linhas no bbox).
3. **Clip set-based** nos polígonos de RS-menos-POA (ponto-em-polígono, SRID 4326) → atribuição de
   território IBGE por ponto (placa, município, UF, urbanização). 1.125.158 → **830.318** em RS.
4. **Tratamento PT** (skill): categoria→PT + segmento (15 classes), nome Title Case, telefone
   `(DD) NNNNN-NNNN`, parser de endereço BR (logradouro/número/quadra/lote/CEP + resíduo preservado).
5. **Filtro de confiança Overture ≥ 0,5** e **dedup fuzzy** (nome-núcleo + raio 30 m) **por município**
   → **632.234** POIs no padronizado (56.746 confirmados por ≥2 fontes).
6. **Bruto sem perda por fonte** + **dicionário** + **mapa A2L** (HTML único com seletor de cidade).

---

## 2. Melhorias para `extracao-dados-poi-global`

### 2.1 Escala estadual: trocar o loop por município por *fetch-once* + clip set-based
O modo `--uf` busca **Overture e FSQ município a município (497×)**. Para FSQ (scan remoto no HF) isso é
**inviável** (centenas de scans). **Recomendação:** no escopo ESTADO, buscar **cada fonte uma vez no
bbox da UF** e depois **clipar/atribuir território de forma vetorizada** (ponto-em-polígono em lote).
Reduz a coleta de O(nº municípios × fontes) para O(fontes) e cai de horas para minutos.

### 2.2 Foursquare no Hugging Face — três correções de alto impacto
1. **Shards são clusterizados por geografia.** Rodar primeiro um **scan de distribuição** para achar os
   poucos shards densos, e processar só eles:
   ```sql
   SELECT filename, count(*) FROM read_parquet([... 100 arquivos ...], filename=true)
   WHERE longitude BETWEEN W AND E AND latitude BETWEEN S AND N GROUP BY filename;
   ```
   Com *row-group pruning* os shards vazios saem em ~0 s; sobra(ram) **3/100**.
2. **NÃO filtrar por `name IS NOT NULL` no scan remoto.** Esse predicado força ler a coluna `name`
   (texto) de **todos** os row-groups (~18 s/shard). Filtrar **só por lat/lon** (stats pruning, ~1 s/shard)
   e aplicar `name`/limpezas **depois, localmente**, no pandas.
3. **Materializar via `COPY ... TO parquet` (streaming DuckDB)**, não via loop `iterrows()` por linha.
   Para shards densos (ex.: 495 k linhas), o build linha-a-linha estoura tempo/memória; o `COPY` por
   **faixa de latitude** (7 faixas) é I/O puro e resumível, e o *flatten* nativo deve ser **vetorizado**.

### 2.3 OSM — a explosão de `osm.tags.*` quebra pandas E o PostGIS
O *flatten* "sem perda" gera **1.000–1.500+ colunas** (uma por chave de tag). Consequências:
- `pandas.concat`/dedup desses parquets **trava/estoura memória**;
- a união com Overture/FSQ passa de **1.600 colunas → acima do limite do PostgreSQL** (uma tabela não
  carrega), tornando o "bruto unificado em CSV" **inútil para PostGIS**.

**Recomendações:**
- No caminho do **padronizado/clip**, ler das fontes **apenas as 17 colunas COMUNS** (projeção de coluna
  no parquet) — nunca materializar o OSM largo em pandas.
- No **bruto**, entregar **por fonte** (overture/fsq/osm separados), e para o OSM consolidar as tags em
  **um único campo `osm_tags_json`** (JSON) — **sem perda** e carregável como **JSONB**. Opcionalmente,
  oferecer o bruto como **GeoParquet** (sem limite de colunas) quando o destino não for PostGIS.

### 2.4 OSM ways — separar os scans
A query única (nós + ways + resolução de refs) pode estourar tempo/memória num único passe. **Dividir**:
(a) scan de **ways-POI** (id, tags, refs) → arquivo; (b) scan de **nós dos refs** (SEMI JOIN) + centroide.
Cada passe do `ST_ReadOSM` sobre o `.pbf` Sul (399 MB) roda em ~6 s. Definir `SET memory_limit` e
`SET temp_directory` (spill em disco) para UFs grandes.

### 2.5 Overture em estado — tiles sem re-download recursivo
A subdivisão espacial recursiva **re-baixa cada quadrante** (multiplica o download e estoura o tempo).
**Correção:** baixar o tile **uma vez**; se denso, **fatiar o dataframe em blocos de linhas** (sem novo
download). Usar **marcador `DONE` por tile** (não detectar conclusão por prefixo de arquivo) — senão uma
subdivisão parcial marca o tile como completo e **perde dados**.

### 2.6 Clip e dedup em escala
- **Clip:** simplificar os polígonos municipais (~0,0005°) e **chunkar** o `sjoin within`; pré-filtrar
  pelo bbox da UF antes do ponto-em-polígono. (1,1 M pontos clipados em ~12 s.)
- **Dedup:** o fuzzy global em ~700 k linhas estoura; **dedupar por município** captura as duplicatas
  entre fontes (co-localizadas em <30 m) com custo limitado e resumível.

### 2.7 Resumibilidade, idempotência e robustez (transversal)
- **Tudo por unidade resumível** (shard/tile/faixa/lote/município) com **escrita atômica** (`.tmp` +
  `os.replace`) — um timeout nunca corrompe parquet nem marca progresso falso.
- Tornar a leitura **tolerante a parts vazios** (ex.: tile Overture sem POIs grava parquet de schema
  vazio → `read_parquet(columns=COMUNS)` falha; capturar e pular).
- Deixar **`--min-conf` explícito no alinhamento** — o default 0,5 descarta POIs Overture de baixa
  confiança (parte da redução 830 k→632 k veio daí).

---

## 3. Melhorias para `roteirizacao-simulacao-mapas` / `mapas-a2l`

### 3.1 BUG do Street View (corrigir no template congelado)
O link `https://www.google.com/maps?q=LAT,LON&layer=c` **não abre o Street View de forma confiável**.
Trocar por:
```
https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=LAT,LON
```
(API oficial de URLs do Google Maps — abre o panorama no ponto). Aplicável a **todos** os popups da skill.

### 3.2 Generalização para POIs (não só ligações)
O padrão **HTML único + seletor de cidade + render só da cidade ativa (`preferCanvas`) + payload
gzip+base64 + `DecompressionStream` nativo** escala bem além de ligações: **632 k POIs → HTML de 27 MB**
abrindo normalmente. As dimensões de cor/filtro são **reparametrizáveis**:
- `GF → Segmento` (15 classes), `Rota → Fonte` **ou** `Nível de Confiança`;
- payload com **delta de coordenada em micro-graus** + array `info` + dicionários continua ideal.
Sugestão: expor no gerador flags `--cor {gf|segmento|confianca}` e `--bloco2 {rota|fonte|confianca}`.

### 3.3 Popup enxuto e por demanda
Renderizar **cada linha do popup só quando há valor** (esconde campos vazios) e priorizar dados
relevantes ao domínio: para POIs → **nome, segmento, categoria, endereço, telefone (tel:), site,
confiança e data de atualização**, além de coordenada e Street View. Evitar campos de baixa relevância.

---

## 4. Restrições de ambiente que moldaram a solução
(Úteis para qualquer skill que rode em sandbox como esta.)
- **Execução em janelas curtas e sem processo em background** → pipeline **stage-a-stage resumível**,
  cada etapa fechando em poucas dezenas de segundos.
- **Geração de scripts executáveis preferir conteúdo "à prova de corrupção"** (escrever via heredoc do
  shell) e **validar `ast.parse` + ausência de NUL** antes de rodar.
- **DuckDB** como motor de I/O pesado (scans HF, COPY, `ST_ReadOSM`, união por nome, escrita streaming)
  com `memory_limit`/`temp_directory` configurados — evita OOM em base grande.

---

## 5. Inventário de scripts (em `./scripts/`)
| Script | Papel |
|---|---|
| `rs_driver.py` | Orquestrador resumível (status/overture/fsq/osm-nodes/osm-ways/clip/treat/dedup/bruto). |
| `fsq.py` | FSQ: `dist` (acha shards densos) · `copy` (bbox-only, por faixa) · `build` (flatten vetorizado). |
| `osm_ways.py` | OSM ways em 2 scans (ways → refs; nós dos refs + centroide). |
| `overture.py` | Overture por tile com marcador DONE e fatiamento por linhas (sem re-download). |
| `clip.py` | Clip set-based chunked (polígonos simplificados) + território. |
| `dedup.py` | Dedup fuzzy por município + escrita do padronizado. |
| `osm_bruto.py` | Bruto OSM compacto com `osm_tags_json` (JSONB-ready). |
| `poi_map.py` | Mapa A2L de POIs (cor por confiança, sidebar Segmentos+Confiança, popup repensado). |
