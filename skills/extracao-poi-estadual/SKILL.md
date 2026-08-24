---
name: extracao-poi-estadual
description: "Extracao de POIs/estabelecimentos/comercios em ESCALA ESTADUAL (UF inteira, com exclusao opcional de municipios), multifonte e SEM PERDA SILENCIOSA: todo descarte e toda fusao gravados linha a linha. Entrypoint parametrizado (--uf/--excluir/--fontes/--base-dir), sem caminho fixo no codigo. Fetch-once: Overture, OpenStreetMap (.pbf via DuckDB ST_ReadOSM, predicado ampliado) e Foursquare coletados 1x no bbox da UF e recortados set-based; trata para PT, atribui territorio IBGE e faz DEDUP POR EVIDENCIA (contexto, telefone, diametro) com blocking espacial — a divisa municipal nao e parede. Entrega padronizado (CSV+GeoParquet), BRUTO POR FONTE (OSM com tags JSON p/ PostGIS/JSONB), elo OBSERVACAO->ENTIDADE, vinculos, rejeitados, procedencia, MAPA A2L e relatorio com gate SEMANTICO. Retomavel, com MANIFESTO. Acione para: extrair POIs de um estado, base estadual de estabelecimentos, POIs por UF exceto a capital, mapa A2L. Evolucao estadual da extracao-dados-poi-global. CORSAN/Aegea, COPEL, SABESP, SANEAGO, AGESPISA."
---

# Extração de POIs — Estadual (multifonte, PT, alto desempenho)

<!-- ============================================================= -->
<!--  Versão:        v3.6.1                                        -->
<!--  Atualizado em: 2026-08-10 (BRT)                              -->
<!--  Autor:         A2L Engenharia e Consultoria                  -->
<!--  Base:          v2.0.0 (ver references/MIGRACAO_V2_V3.md)     -->
<!-- ============================================================= -->

> **v3.6.1 — versão única.** `VERSION` virou a única origem do número: `ADAPTER_VERSAO`
> e o `User-Agent` (que ainda anunciavam `2.0` e `3.4`) passaram a derivar dele.
> Sem mudança de `processor_version` — nenhum artefato existente é invalidado.
>
> **v3.6.0 — snapshot declarado = bytes consumidos = código que processou.** O blob
> do OSM só é promovido ao snapshot **depois de conferir o digest**; `pinned` e `cache`
> não materializam nada sob snapshot indeterminado e `pinned` deixou de se autopinar;
> etapa sem `fingerprint` (manifesto legado) é **stale**; `processor_version` entrou na
> linhagem; `run` carrega a versão do código, não a do workspace.
>
> **v3.5.0 — a identidade do dado passou a governar o ESTADO DA ETAPA e os BYTES.**
> `reutilizavel()` agora compara também o **fingerprint de entrada** (linhagem
> snapshot/collection/work), então snapshot novo torna `fetch` — e toda a cadeia —
> stale. `pinned` virou literal: FSQ lê exatamente `dt=<release>`, Overture manda
> `--release` no comando, OSM exige o **blob do snapshot** guardado. Derivados do OSM
> passaram para collection/work; `cache` não reescreve identidade já resolvida; IDs de
> collection/work são por adapter; `content_digest` + `digest_algorithm` +
> `digest_scope` no lugar de um `content_sha256` que guardava md5.
>
> **v3.4.0 — `snapshot_id` passou a identificar a VERSÃO DA FONTE, não a forma de
> coletar.** Três identidades separadas: `snapshot_id` (versão/conteúdo real da fonte),
> `collection_id` (o que se pediu: bbox, UF, filtros) e `work_id` (como se materializou:
> tiles, strips). Como o diretório desce do snapshot, **fonte nova ⇒ diretório novo ⇒
> coleta nova** — congelar a fonte deixou de ser possível. `--source-mode
> cache|latest|pinned` e `--refresh-source` separam frescor de reprocessamento.
>
> **v3.3.0 — o cache passou a ter a mesma identidade do hash, e `relation` do OSM
> entrou.** O hash de escopo da etapa virou parte do CAMINHO: hash diferente,
> diretório diferente, reuso acidental deixa de ser possível. Cache de FONTE ficou
> separado do cache de PROCESSAMENTO e isolado por UF; `relation`/multipolygon do OSM
> é lida, com supressão do membro homônimo.
>
> **v3.2.0 — a divisa municipal deixou de ser parede no matching, e a base passou a
> entregar o elo OBSERVAÇÃO → ENTIDADE.** O dedup faz blocking por grade + halo e
> consolida uma vez só, no global; o município virou atributo da entidade. Cada
> registro de fonte que entrou no dedup sai em `poi_observacoes_*` com o `cluster_id`
> da entidade que ajudou a sustentar — a entrega deixa de ser só "o registro que
> sobreviveu à fusão".
>
> **v3.1.0 — o BBOX de coleta deixou de depender de `--excluir`.** Exclusão de
> município agora só recorta o consumo, nunca o snapshot bruto da fonte: baixa a UF
> uma vez e produz qualquer combinação de municípios sem nova coleta. Ver o
> CHANGELOG.
>
> **v3.0.0 — EXATIDÃO. Mudança incompatível de esquema, de defaults e de motor de
> dedup.** A auditoria de duas execuções reais (Canoas-RS e Santa Maria-RS) mediu que
> a fusão da v2.0.0 **apagava estabelecimentos reais**: 42 (10,1%) e 66 (4,5%) fusões
> indevidas comprovadas, ~71 e ~115 estabelecimentos distintos sumindo na consolidação
> — com as duas entregas **APROVADAS 14/14** no gate antigo, que conferia aritmética e
> não semântica. Ao editar, incremente a versão aqui, no `VERSION` e no
> `pyproject.toml`, e registre em `references/CHANGELOG.md`.

Extrai estabelecimentos/POIs de fontes abertas para **uma UF inteira**, com **exclusão
opcional de municípios**. Autocontida: os módulos da `extracao-dados-poi-global` estão
vendorizados em `poi_estadual/vendor/`.

## ⚠️ Alinhamento OBRIGATÓRIO — perguntar antes de rodar
1. **UF** e **exclusões** (COD IBGE de 7 dígitos, ex.: capital).
2. **Fontes** — todas ou subconjunto? (`fsq` exige `HF_TOKEN` no ambiente.)
3. **`--min-conf`** — default **0.0** desde a v3.0.0. Confirmar se o consumidor quer
   filtro de confiança; se quiser, filtrar no consumo, não na coleta.
4. **Coleta OSM** — `--osm-predicado ampliado` (default) traz transporte, saúde,
   indústria e equipamentos públicos; POI sem `name` entra marcado em `sem_nome`.
   **Mudar isso invalida o `fetch`** e força nova extração do `.pbf`.
5. **Entrega** — formatos (`csv`, `geoparquet`), gerar o **mapa A2L**?

## Uso

```bash
python poi_estadual.py diag                    # checa deps, vendor, CLI, token

python poi_estadual.py run \
  --uf RS --excluir 4314902 \
  --fontes overture,osm,fsq --min-conf 0.0 \
  --formatos csv,geoparquet --gerar-mapa \
  --base-dir ./execucao_rs

python poi_estadual.py status --uf RS --excluir 4314902 --base-dir ./execucao_rs
```

`--ate <etapa>` para no meio · `--etapa <etapa>` roda só uma · `--budget <s>` time-box
· `--force` reexecuta etapa concluída.

**Chaves de exatidão (v3.0.0):** `--dedup evidencia|legado|exato|none` ·
`--dedup-ctx-raio 200 --dedup-ctx-min 3` (token de contexto) · `--dedup-jaccard 0.60` ·
`--dedup-diametro 90` · `--dedup-semnome absorver|marcar` · `--dedup-celula 2000`
`--dedup-halo 0` (auto) · `--max-fusao-suspeita 0.02` ·
`--osm-predicado ampliado|classico` · `--osm-exigir-nome`.

## Pipeline

```
init → fetch → raw → territory → normalize → dedup → export → map → validate
```

1. **init** — malha municipal: parquet local (`--malha-parquet` / `DIVISOES_PARQUET`) ou,
   na ausência dele, **API IBGE** (`/localidades` + `/malhas v3`, EPSG:4674). Alvo = UF
   menos exclusões; **bbox e grade de tiles derivam da geometria**, nunca de constante.
   **v3.1.0:** o bbox que rege a COLETA sai da malha **completa** da UF, antes das
   exclusões (`bbox_coleta.json`) — `--excluir` não entra no hash de `fetch`/`raw`, logo
   não pode encolher a área lida. Consequência: excluir a capital **não** reduz o
   download; reduz o processamento de `territory` em diante. Se a grade de tiles mudar
   em relação ao disco, os marcadores `DONE` do Overture são invalidados (o marcador é
   por índice de tile e passaria a atestar outro retângulo).
2. **fetch** (fetch-once, O(fontes) e não O(municípios×fontes)):
   - **Overture** — CLI `overturemaps` por tiles ~1°. Tile baixado **1×**; se denso,
     fatia-se **por linhas** lendo o geoparquet em batches (pyarrow). Marcador `DONE`
     por tile em arquivo próprio. `locality` vai para `localidade_fonte` (não para
     `bairro`) e a taxonomia hierárquica vai para `categoria_hier`.
   - **OSM** — `.pbf` da macrorregião (Geofabrik) no DuckDB `ST_ReadOSM`, em **2 scans**.
     Posição do way por `representative_point()`. **Predicado ampliado** (v3.0.0):
     `amenity, shop, office, leisure, tourism, healthcare, craft, historic, government,
     military` como chave aberta + `railway, public_transport, aeroway, man_made,
     building` restritas à lista de valores que é POI de verdade. `name` deixou de ser
     obrigatório. **v3.3.0: `relation`/multipolygon é lida** — members resolvidos,
     anéis remontados com `polygonize`, posição por `representative_point()`; o
     membro que compartilha nome ou marca com a relation entra em `supressao.parquet`
     e é descartado no `raw`, contabilizado no funil e gravado em `rejeitados`.
   - **FSQ** — `GROUP BY filename` acha os shards densos; filtro **só por lat/lon**
     (stats pruning); materialização por `COPY ... TO parquet` por faixa de latitude.
     **Gate de acesso no início do `fetch`**: dataset gated derruba em segundos, antes
     dos ~421 MB do OSM.
3. **raw** — consolida as fontes no esquema COMUNS (projeção de coluna: nunca
   materializar o OSM largo) e aplica gate de coordenada (nula, `(0,0)`, fora do bbox).
   Cada linha descartada vai para `rejeitados`.
4. **territory** — ponto-em-polígono em chunks retomáveis + atributos IBGE.
   `--simplificar-graus 0` por padrão. Ponto fora do alvo vai para `rejeitados`.
5. **normalize** — categoria→PT + segmento (**com fallback pela hierarquia da fonte**),
   Title Case, telefone, parser de endereço BR (**com troca logradouro↔resíduo** quando
   o resíduo é que tem tipo de via), `precisao_coord_m`, `coord_empilhada` e
   `flag_localidade_divergente`. `--min-conf` filtra Overture — descarte gravado.
6. **dedup** — **por evidência**, com blocking por **grade + halo** e consolidação
   **global**. Detalhado abaixo.
7. **export** — padronizado (CSV/GeoParquet) + **bruto POR FONTE** + **vínculos do
   dedup** + **rejeitados** + dicionário.
8. **map** — HTML único A2L: seletor de cidade, render só da cidade ativa (canvas),
   payload gzip+base64, cor por confiança, Street View. Todo texto passa por `esc()`.
9. **validate** — gate de entrega: schema, coordenada, bbox, município fora do alvo,
   município excluído, `id_fonte` duplicado, `min_conf`, funil, manifesto, **FUSÃO
   SUSPEITA**, cobertura da tabela de rejeitados **e — v3.2.0 — cobertura do elo
   observação→entidade** (toda observação normalizada tem de aparecer no elo). Grava
   `relatorio_qualidade_*.json`; **reprova a entrega** se algo falhar.

## Dedup por evidência (o núcleo da v3.0.0)

O motor da v2 casava **nome** (`token_set_ratio` do núcleo distintivo) dentro de 30 m e
unia por union-find sem limite de cadeia. Em galeria, shopping ou prédio comercial, o
núcleo que sobrava era o token **compartilhado** — nome do shopping, do bairro, uma
sigla — e o resultado era apagar estabelecimento real:

| Fundidos numa linha pela v2 | O que eram |
|---|---|
| `Cheirin Bão Canoas Shopping` + `CVC Canoas Shopping Center` | cafeteria e agência de viagens |
| 4× `... Advocacia` no mesmo prédio | quatro bancas diferentes |
| `Casa de Carnes Camobi` + `Auto Posto Camobi` + `Restaurante Grill Camobi` | açougue, posto e restaurante |
| `Setor A` … `Setor E` | cinco setores (o diferenciador é 1 caractere, descartado) |

Regras da v3.0.0, todas configuráveis e todas testadas em `tests/test_v3.py`:

- **Token de contexto (IDF local)** — token presente em ≥ `ctx_min` POIs dentro de
  `ctx_raio_m` não distingue nada. O **núcleo discriminante** é o que sobra. Núcleo
  vazio ⇒ **não funde por nome**. Exceção: nomes **literalmente** iguais (mesmo conjunto
  de tokens brutos) continuam podendo fundir — contexto explica dois nomes *diferentes*
  parecerem iguais, não dois nomes iguais.
- **Jaccard** sobre o núcleo, além do `token_set_ratio` — que devolve 100 quando um nome
  é **subconjunto** do outro (`Farmácia Universitária` × `Abastecedora Universitária`).
- **Token de 1 caractere preservado** quando é o único diferenciador.
- **Núcleo curto exige mesmo segmento** (`Lancheria X9` × `Estacionamento X9`).
- **Telefone e site como evidência independente do nome**: coincidência autoriza raio
  maior (200 m); **divergência VETA** a fusão mesmo com nome idêntico. Telefone presente
  em mais de `tel_max_locais` posições é **hub** (call center, condomínio) e deixa de
  valer nos dois sentidos.
- **Marca igual exige proximidade curta** (15 m) — duas lojas da mesma rede no mesmo
  quarteirão não são a mesma loja.
- **Trava de diâmetro** (Kruskal com restrição): a aresta só entra se o cluster couber
  na tolerância. Diâmetro medido na auditoria: 85,2 m para raio configurado de 30 m.
- **Ponto sem nome**: funde com outro sem nome (categoria compatível + 12 m) e é
  **absorvido** por ponto nomeado próximo. Sem essa regra, admitir POI sem `name` do OSM
  injeta duplicata.
- **Guarda de precisão**: coordenada empilhada (5,6% da base — centroide de CEP) ou de
  baixa precisão decimal não funde por proximidade sozinha.
- **Blocking por grade, não por município** (v3.2.0). A partição decide apenas quais
  **pares** são avaliados; o cluster fecha uma vez, no global. Antes, dois registros da
  mesma loja a 2 m e 3 m de lados opostos da divisa nunca entravam no mesmo universo de
  matching. O halo (≥ `raio_forte_m`) garante que nenhum par válido fica partido entre
  células, e a aresta repetida em células vizinhas é deduplicada na consolidação. O
  município passa a ser **atributo** da entidade, herdado da âncora. `--dedup-celula 0`
  volta à partição por município.
- **Token de contexto e telefone-hub são globais** (v3.2.0) — são propriedades da
  vizinhança, não da partição: medi-los por município faria a mesma marca ser contexto
  de um lado da divisa e discriminante do outro.
- **Âncora**: `sem` (OSM, que não publica confiança) passa à frente de `baixa`, com
  desempate por `precisao_coord_m` e só então por `id_fonte`. Na auditoria, o Overture
  era âncora em **100,0%** de 1.682 fusões — determinismo, não tendência.

## Entregáveis

| Arquivo | Conteúdo |
|---|---|
| `poi_padronizado_*` | base tratada (CSV + GeoParquet) |
| `poi_bruto_<fonte>_*` | colunas nativas por fonte (OSM com `osm_tags_json`) |
| `poi_dedup_vinculos_*` | **par a par**: motivo, distância, score, aceito — inclusive as recusas (`veto_telefone_divergente`, `recusa_contexto_sem_nucleo`, `recusa_diametro`, `recusa_jaccard`, `recusa_coord_suspeita`) |
| `poi_rejeitados_*` | cada linha descartada em `raw`/`territory`/`normalize`, com motivo |
| `poi_fusao_suspeita_*` | fusões intra-fonte com nomes divergentes (só quando houver) |
| `poi_observacoes_*` | **elo observação → entidade**: uma linha por registro de fonte que entrou no dedup, com `observation_id`, `snapshot_id`, `observed_at`, `cluster_id`, `ancora`, posição e município. Nenhuma observação se perde |
| `poi_source_snapshot_*` | procedência tabular: `snapshot_id`, release, URL de origem, data de coleta, UF, licença, versão do adaptador e `run_id` por fonte |
| `poi_dicionario_*` | dicionário de campos |
| `relatorio_qualidade_*.json` | verificações item a item + funil + hashes |
| `mapa_pois_*.html` | mapa A2L (opcional) |

## Procedência temporal: snapshot ≠ collection ≠ work (v3.4.0)

```
SOURCE VERSION  ─► snapshot_id      versão/conteúdo real da fonte
REQUEST         ─► collection_id    snapshot + UF/bbox/filtros
EXECUTION PLAN  ─► work_id          tiles, strips
```

Até a v3.3.0 havia uma assinatura só, derivada dos **parâmetros de coleta**. A mesma UF
com a mesma grade em agosto e em dezembro dava a mesma assinatura — e, como o diretório
não mudava e o marcador `DONE` seguia lá, a fonte podia **congelar**: dado velho,
perfeitamente cacheado e perfeitamente auditado como se fosse atual.

| Fonte | De onde sai a versão |
|---|---|
| OSM | `<arquivo>.md5` da Geofabrik + `Last-Modified`/`ETag`; sem rede, SHA-256 de amostra do `.pbf` local |
| FSQ | release `dt=YYYY-MM-DD` + SHA do manifesto de shards |
| Overture | `OVERTURE_RELEASE` ou listagem do prefixo público; sem nenhuma das duas, **`indeterminado`** — declarado, nunca presumido |
| IBGE | SHA-256 do parquet local (`--malha-parquet` **agora conta**) ou do artefato baixado |

**Integridade (v3.6.0).** Três invariantes lacrados: o **blob** do OSM é baixado para
`.part`, tem o digest conferido e só então é promovido a
`snapshots/<snapshot_id>/source.osm.pbf` — bytes não verificados nunca recebem
identidade; **nenhum modo materializa dado sob `snapshot_id="indeterminado"`** (`cache`
e `pinned` exigem versão já resolvida, e `pinned` **não** consulta a fonte para criar o
próprio pin — isso é `--source-mode latest`, explícito); e o **`processor_version`**
entra no fingerprint, então mudar o algoritmo de `normalize` ou `dedup` invalida o
artefato mesmo com snapshot e config idênticos. Etapa sem `fingerprint` (manifesto
anterior à v3.6) é tratada como **stale** e reconstruída uma vez.

**Reprodutibilidade (v3.5.0).** O estado da etapa passou a depender da **linhagem do
dado**, não só da config: `etapas[<e>].fingerprint` = hash da config + `collection_id`/
`work_id` de cada fonte. Snapshot novo ⇒ fingerprint novo ⇒ `fetch` e tudo abaixo ficam
stale. Em `pinned`, o FSQ lê exatamente `dt=<source_version>`, o Overture manda
`--release <versão>` (e **recusa rodar** sem release resolvida) e o OSM exige o **blob**
guardado em `fontes/osm/snapshots/<snapshot_id>/source.osm.pbf` — baixar `-latest` de
novo traria outra versão do mundo com o mesmo nome de arquivo.

`--source-mode` **cache** (não toca a rede) · **latest** (resolve a versão atual e
recoleta se mudou) · **pinned** (exige versão resolvida de toda fonte e recusa rodar com
`indeterminado`). `--refresh-source osm,fsq` reconsulta fontes específicas.
`--force-stage` reconstrói o **processamento** sobre o **mesmo** snapshot — as duas
intenções deixaram de compartilhar uma flag.

## Cache: identidade física = identidade lógica (v3.3.0)

Até a v3.2.0 o manifesto sabia que uma etapa estava **obsoleta** e o worker
reaproveitava o arquivo assim mesmo — `if os.path.exists(outp): continue` num caminho
que não dependia do hash. Mudar `--min-conf` marcava `normalize` como obsoleta e os
lotes `n_*.parquet` da execução anterior continuavam lá.

Agora o hash de escopo **é parte do caminho**, e o cache de fonte é separado do de
processamento:

```
cache/
  fontes/                       # sobrevive a mudança de parâmetro de processamento
    ibge/<sig(uf,qualidade)>/       malha.parquet · bbox_coleta.json
    osm/pbf/                        .pbf da macrorregião (compartilhado entre UFs)
    osm/<sig(regiao,predicado)>/    nodes · ways · relations · supressao
    <fonte>/snapshots/              resolvido.json + histórico por snapshot_id
    <fonte>/collections/<collection_id>/<work_id>/    artefatos materializados
  proc/
    <etapa>/<hash_etapa>/           artefatos da etapa
```

Três consequências: **(a)** hash diferente ⇒ diretório diferente, sem
`if hash mudou: apaga` espalhado por módulo; **(b)** grade nova não enxerga resíduo
`ov_7_3.parquet` de um fatiamento antigo, porque a grade **é** a assinatura do
diretório; **(c)** rodar `--uf SC` no mesmo `--base-dir` de um `--uf RS` não
contamina nada — os caminhos são disjuntos, e o `.pbf` da macrorregião é o único
compartilhado, de propósito.

## Manifesto e retomada

`manifesto.json` guarda `run_id`, config, **hash de escopo por etapa**, versão de cada
fonte, sha dos módulos vendorizados, contagens e o **funil**. Duas travas: **hash por
etapa** (artefato só é reaproveitado se o escopo da config bate) e **gate de
precedência** (`export` não roda com `dedup` em `partial`).

Na v3.0.0, `osm_predicado` e `osm_sem_nome` entram no escopo de `fetch` — mudá-los
invalida a coleta, de propósito. Os parâmetros de dedup **não** invalidam a coleta.

## Funil e o termo "sem perda"

Toda etapa registra `entrada = saída + descartados por motivo`, e `validate` reprova se
alguma linha não fechar. **Desde a v3.0.0 todo descarte também é gravado linha a linha**
— o que a skill faz é **preservação máxima do bruto com filtros auditáveis**, não
ausência de perda.

| Onde | Motivo |
|---|---|
| `raw` | coordenada nula, `(0,0)` ou fora do bbox |
| `territory` | ponto fora dos municípios alvo |
| `normalize` | `confianca` Overture < `--min-conf` (default 0.0 = nenhum) |
| `dedup` | registro fundido em duplicata (com vínculo gravado) |
| `fetch` (OSM) | cobre node+way, **não** relation/multipolygon |

## Onde esta skill para (e por quê)

`cluster_id` é a **chave natural da âncora** (`fonte:id_fonte`) — determinística,
legível e estável enquanto a âncora não muda. **Não é um ID permanente entre
execuções**: isso exige store append-only com política de merge/split e linhagem
(`merged_into`, `split_from`), que é trabalho de uma camada de resolução de entidade
com persistência — na linha A2L, `ferramenta-logradouro-padrao`. Esta skill é o
**produtor de observações**: entrega o que cada fonte afirmou, o que foi fundido, por
qual evidência, e o que foi recusado. Quem promove observação a entidade canônica com
identidade perene é a camada de cima. Do mesmo modo, âncora de endereço (CNEFE) e
identidade oficial (CNPJ, CNES, INEP, ANP) pertencem a adaptadores próprios —
reimplementá-los aqui criaria duas verdades de endereço.

## Limitações declaradas

`relation` **entrou na v3.3.0** e é testada contra um `.pbf` real gerado com `osmium`
na própria suíte. Fica declarado que a posição vem de `polygonize` sobre os members;
quando os anéis não fecham, cai no fecho convexo e a linha sai marcada em
`osm.geom_origem` (`poligono` | `fecho_convexo` | `media`).

Fora do escopo: temas `address`/`building` do Overture, e validação posicional
contra CNEFE — esta última pertence às skills `ferramenta-logradouro-padrao` e
`construcao-vias-publicas`, não a esta. Reimplementar aqui criaria duas verdades de
endereço na linha A2L.

## Regras de robustez (não regredir)
- Escrita atômica (`.tmp` + `os.replace`) e resume por unidade (tile/shard/faixa/lote/município).
- SRID explícito: malha IBGE em **4674**, pontos e clip em **4326**. Nada implícito.
- DuckDB para I/O pesado, com `memory_limit` e `temp_directory` configuráveis.
- Determinismo: ordenação estável antes de dedup; arestas ordenadas por
  `(peso desc, i, j)`; mesma entrada → mesmo sobrevivente, em qualquer ordem de linhas.
- Leitura tolerante a parte vazia (tile sem POI → parquet de esquema vazio).

## Testes

```bash
python -m pytest tests -q     # 109 testes
```

O teste de `relation` gera um `.pbf` real com `osmium` (extra `dev`); sem a lib, ele é
pulado, não falha. `tests/test_pipeline.py` cobre parametrização, retomada, escape do
mapa e amostra mínima ponta a ponta. `tests/test_v33.py` cobre a identidade do cache
(invalidação por `min_conf`, por `--excluir`, por predicado OSM, isolamento entre UFs)
e a leitura de `relation`. `tests/test_v3.py` cobre **cada caso real medido na auditoria**
como teste de regressão — contexto, Jaccard, `Setor A/B`, telefone, hub, marca,
diâmetro, NaN, sem-nome, coordenada empilhada, âncora, taxonomia, predicado, defaults
e gate semântico.

## Referências
- `references/MIGRACAO_V2_V3.md` — o que muda, o que quebra e como reprocessar.
- `references/MIGRACAO_V1_V2.md` — histórico da reescrita anterior.
- `references/ARQUITETURA.md` — módulos, contratos e pontos de extensão.
- `references/METODOLOGIA.md` — método de coleta e tratamento.
- `references/CHANGELOG.md` — histórico e pendências abertas.

## Licenças (atribuição obrigatória ao redistribuir)
Overture (CDLA-Permissive 2.0) · OpenStreetMap (ODbL, "© OpenStreetMap contributors") ·
Foursquare OS Places (Apache-2.0, manter NOTICE) · malhas IBGE (uso público).
Dados gratuitos; sem custo por registro.
