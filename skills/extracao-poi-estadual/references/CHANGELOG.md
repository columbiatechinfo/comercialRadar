# CHANGELOG — extracao-poi-estadual

> Formato: **versão · data/hora (BRT/UTC) · mudanças**. Sempre incrementar ao editar.

## v3.6.0 — 2026-08-10 (BRT)

### Corrigido — P0
- **Bytes antigos eram carimbados com snapshot novo.** Com `sul-latest.osm.pbf` de
  agosto em cache e snapshot resolvido de setembro, `_pbf()` caía em
  `baixar_pbf(force=False)`, que devolvia o arquivo existente, e o `os.replace` o
  promovia para dentro de `snapshots/<snapshot_setembro>/`. `baixar_verificando()`
  baixa para `.part`, **confere o digest** e só então promove; divergência remove o
  parcial e falha. Protege também contra a fonte mudar entre a leitura do checksum e
  o download.
- **`fingerprint` ausente era tratado como confiável.** `fp is None or ...` fazia
  manifesto legado passar por reutilizável. Agora `fp is None` ⇒ **stale**.
- **`pinned` se autopinava.** Sem snapshot em disco, `consulta_permitida` devolvia
  `True` e o modo resolvia a versão corrente sozinho. Só `latest` toca a rede; criar
  pin é operação explícita.
- **`cache` materializava sob `indeterminado`.** Agora erro. E `_identificar_observacoes`
  **recusa** gerar `observation_id` com `snapshot_id="indeterminado"`.

### Corrigido — semântica e linhagem
- **`processor_version` entrou no fingerprint** (`PROCESSOR_VERSAO` por etapa) — a
  terceira versão que faltava, ao lado de DATA (snapshot) e REQUEST (config/collection).
- **Versão do adapter no `work_id` das três fontes** (antes só o OSM).
- **`skill_versao` era a do workspace.** Passou a ser a do run, com
  `workspace_criado_com` guardando a original e `runs[].skill_versao` no histórico.
- **`fingerprint` invalidava demais** — `init` é IBGE e não pode ficar stale porque o
  OSM mudou. Dependência por etapa.
- **`row_count_raw` misturava shard, tile e município com registro.** Virou
  `partition_count`, `source_object_count` e `raw_record_count`.
- **`OVERTURE_RELEASE` do ambiente marcava `verified_latest`.** Release informada é
  `pinned`; nada comprovou que é a mais recente.

### Adicionado
- 11 testes (`tests/test_v36.py`): digest divergente não promove e remove o parcial;
  digest correto promove; etapa sem fingerprint é stale; `pinned` sem snapshot falha
  sem tocar a rede; `cache` sem snapshot falha; observação sob indeterminado é
  proibida; versão do processador invalida a etapa; adapter no `work_id` das três
  fontes; `init` não depende do snapshot de POI; release do ambiente não é
  `verified_latest`; `skill_versao` é do run. Suíte: **109 testes**.

## v3.5.0 — 2026-08-10 (BRT)

### Corrigido — P0
- **Snapshot novo não invalidava `fetch`.** `reutilizavel()` comparava só o hash da
  CONFIG. O sistema sabia que a versão da fonte tinha mudado (collection nova,
  diretório novo) e considerava o `fetch` antigo reutilizável — a promessa "fonte nova
  ⇒ coleta nova" morria no orquestrador. Cada etapa passou a gravar um
  **`fingerprint`** = hash da config + linhagem (`collection_id`/`work_id` por fonte);
  linhagem diferente ⇒ etapa stale, e a cadeia inteira cai junto.
- **`pinned` não era pinned.** O FSQ com snapshot fixado em `2026-08-01` ainda chamava
  "última release" e podia consumir `2026-10-01`. `listar_releases(cfg, release=...)`
  lê exatamente `dt=<release>` e falha se ela não existir.
- **Overture: proveniência dizia uma release, o download pedia "Places".** O comando
  passou a levar `--release <versão>`; em `pinned` sem release resolvida, **recusa**.
- **OSM: `pinned` podia baixar `-latest` e carimbá-lo com o snapshot antigo.** O blob
  passou a ser guardado em `fontes/osm/snapshots/<snapshot_id>/source.osm.pbf`; em
  `pinned`, blob ausente é erro, nunca download do atual.
- **Derivados do OSM sobreviviam à troca do PBF.** `nodes/ways/relations/supressao`
  viviam sob `sig_osm()` (região+predicado). Passaram para collection/work — portanto
  sob o snapshot.
- **`cache` reescrevia a identidade com outro algoritmo.** Snapshot oficial (md5 da
  Geofabrik) virava outro `snapshot_id` na execução seguinte, sem rede, por causa do
  sha256 local do mesmo arquivo. Snapshot já resolvido manda; hash local é verificação
  de integridade, não identidade.
- **Acoplamento entre adapters.** `collection_id`/`work_id` eram iguais para todas as
  fontes: `osm_sem_nome` mudava a collection do FSQ, `fsq_strips` mudava o work do
  Overture. Agora são específicos por adapter (`ids_da_fonte`).
- **`workspace_id` nascia `None`** — a chave aparecia duas vezes em `_novo()` e a
  segunda sobrescrevia a primeira.
- **`is_latest_at_collection` era derivado do modo.** `latest` com consulta falhando e
  fallback registrava `True`. Passou a descender de `resolution_status`
  (`verified_latest` | `cached` | `pinned` | `indeterminate` |
  `network_failed_fallback`), que nasce no resolvedor.
- **`content_sha256` guardava md5.** Virou `content_digest` + `digest_algorithm` +
  `digest_scope` (`full` | `first_64mb` | `shard_manifest`).

### Adicionado
- 14 testes (`tests/test_v35.py`) para os cenários que faltavam: snapshot novo →
  `fetch` stale; fingerprint propaga na cadeia; `pinned` FSQ nunca chama latest;
  `pinned` Overture exige release no comando; `pinned` OSM sem blob falha e com blob
  usa o blob; PBF novo → derivados novos; `cache` mantém o snapshot oficial; IDs por
  adapter sem acoplamento; `workspace_id` != None. Suíte: **98 testes**.

## v3.4.0 — 2026-08-10 (BRT)

### Corrigido — P0
- **`snapshot_id` não identificava snapshot.** Derivava dos parâmetros de coleta
  (região+predicado, grade, bbox+strips, uf+qualidade): identificava **como** se
  coletou, não **qual versão do mundo**. A mesma UF com a mesma grade em agosto e em
  dezembro dava a mesma assinatura. Agora são três identidades: `snapshot_id`
  (versão/conteúdo real), `collection_id` (snapshot + UF/bbox/filtros) e `work_id`
  (tiles/strips). `observation_id = sha256(fonte|id_fonte|snapshot_id)` passou a
  significar o que dizia significar.
- **Fonte podia congelar.** Como o diretório descia da assinatura de coleta e o marcador
  `DONE` continuava válido, tile e shard nunca eram recoletados. Agora o diretório
  desce do snapshot: **fonte nova ⇒ collection nova ⇒ diretório novo ⇒ coleta nova**.
- **FSQ nunca reperguntava a release.** `_release()` devolvia `files.json` do cache;
  podia rodar em outubro servindo a release de agosto. `listar_releases()` consulta a
  fonte, e a release entra no `snapshot_id` junto com o SHA do manifesto de shards.
- **OSM: `sul-latest.osm.pbf` é alvo móvel com nome fixo.** A versão passou a vir do
  `<arquivo>.md5` da Geofabrik + `Last-Modified`/`ETag`; sem rede, SHA-256 de amostra do
  arquivo local. Snapshot diferente do que está em disco força o rebaixe do `.pbf`.
- **Overture registrava a versão da CLI como se fosse a do dado.** CLI é software.
  A release vem de `OVERTURE_RELEASE` ou da listagem do prefixo público; sem nenhuma
  das duas o snapshot fica **`indeterminado`** e `--source-mode pinned` recusa rodar.
- **`--malha-parquet` não entrava em identidade nenhuma** — trocar `a.parquet` por
  `b.parquet` mantinha `sig_ibge` e o hash de `init` idênticos. Agora o snapshot do
  IBGE é o SHA-256 do artefato.
- **`ov_tile_graus`/`fsq_strips` mudavam o diretório da fonte e não o hash de `fetch`** —
  o pipeline dizia "FETCH: reaproveitado" apontando para um diretório novo e vazio.
  Entraram no escopo de coleta; a identidade do **dado** (snapshot/collection) segue
  intocada por eles, só o `work_id` muda.
- **`--force` prometia recoleta e não recoletava.** Virou `--force-stage` (reconstrói o
  processamento sobre o mesmo snapshot), com `--source-mode`/`--refresh-source` para a
  outra intenção. O alias `--force` continua aceito.
- **`run_id` e `criado_em` eram do workspace, não da execução.** `observed_at` e
  `retrieved_at` vinham do primeiro uso do diretório. Agora `workspace_id` é estável,
  cada `run` tem `run_id`/`started_at`/`finished_at` com histórico, e `retrieved_at` é
  por fonte.
- **Supressão de member de relation comparava só `nome`,** embora a regra fosse "nome
  **ou** marca". Passou a comparar os dois nos dois lados.

### Adicionado
- `poi_estadual/procedencia.py` — modelo de snapshot/collection/work, resolvedores por
  fonte, store com histórico (`snapshots/<snapshot_id>.json`, nunca apagado) e os modos
  `cache`/`latest`/`pinned`.
- `poi_source_snapshot` definitivo: `snapshot_id`, `source_version`,
  `source_determinado`, `source_url`, `etag`, `last_modified`, `content_length`,
  `content_sha256`, `retrieved_at`, `collection_id`, `work_id`, `source_mode`,
  `is_latest_at_collection`, `uf`, licença, `adapter_name`, `adapter_versao`,
  `row_count_raw`, `workspace_id`, `run_id`.
- 19 testes (`tests/test_v34.py`): versão da fonte muda o snapshot; `.pbf` com o mesmo
  nome e conteúdo diferente é outro snapshot; bbox muda a collection e não o snapshot;
  strips/tile mudam só o `work_id`; `pinned` recusa indeterminado; `cache` não toca a
  rede; malha local diferente = snapshot diferente; release FSQ nova = snapshot novo;
  snapshot novo = diretório novo; workspace ≠ run. Suíte: **84 testes**.

### Pendência declarada
- Relation dentro de relation, `inner`/`outer` explícitos e multipolygon multipart
  seguem fora (P2). A posição atual vem de `polygonize` sobre os members.

## v3.3.0 — 2026-08-10 (BRT)

### Corrigido — P0
- **Cache físico não estava vinculado ao hash da etapa.** O manifesto marcava a etapa
  como OBSOLETA e o worker reaproveitava o artefato assim mesmo, porque o caminho não
  dependia do hash (`if os.path.exists(outp): continue`). Mudar `--min-conf` deixava os
  lotes `n_*.parquet` da execução anterior no mesmo diretório. Valia para `nodes/ways`,
  `files.json`/`dist.json`/`raw/`/`fsq.parquet`, `parts/ov_*`, `kept_parts/k_*`,
  `n_*.parquet` e os artefatos de dedup. Agora **o hash de escopo é parte do caminho**
  (`cache/proc/<etapa>/<hash>/`) — reuso acidental deixa de ser possível por construção,
  sem `if hash mudou: apaga` espalhado por módulo.
- **Cache de fonte misturado com cache de processamento.** Separados:
  `cache/fontes/<fonte>/<assinatura>/` e `cache/proc/<etapa>/<hash>/`. A mesma coleta
  alimenta qualquer recorte, `min_conf` ou parâmetro de dedup sem novo download; o
  `.pbf` da macrorregião é compartilhado entre UFs da mesma região, de propósito.
- **Contaminação entre UFs no mesmo `--base-dir`.** `bbox_coleta.json` e a malha eram
  gravados sem discriminar a UF e `bbox_coleta()` devolvia o arquivo encontrado sem
  conferir. Agora tudo é chaveado por `sig(uf, malha_qualidade)` e os caminhos de
  processamento são disjuntos entre UFs.
- **Resíduo de tiles do Overture.** Grade nova não enxerga `ov_7_3.parquet` de um
  fatiamento antigo: a grade **é** a assinatura do diretório.
- **`poi_source_snapshot.coletado_em` vinha `None`** — `iniciado_em`/`em` não existem no
  manifesto; a chave gravada é `criado_em`.
- **Unicidade validada só por `id_fonte`.** Passou a `(fonte, id_fonte)`, que é a chave
  real do sistema, mais uma checagem de `cluster_id` duplicado.

### Adicionado
- **OSM `relation`/multipolygon** — members resolvidos (relation → ways → nós), anéis
  remontados com `polygonize`, posição por `representative_point()`; sem anel fechado,
  fecho convexo, com a origem declarada em `osm.geom_origem`. O membro que compartilha
  nome ou marca com a relation vai para `supressao.parquet` e é descartado no `raw`,
  contabilizado no funil e gravado em `rejeitados` — **supressão por contenção e
  identidade, não por raio**. Testado contra um `.pbf` REAL gerado com `osmium` na
  suíte, que era exatamente o que faltava para entregar isso na v3.2.0.
- **`observation_id`, `snapshot_id` e `observed_at`** em `poi_observacoes`.
  `fonte + id_fonte` identifica o objeto da fonte; `+ snapshot` identifica a
  observação — `OSM node/123` em janeiro e em agosto não fizeram a mesma afirmação.
- **`source_snapshot` formal**: `snapshot_id`, release, `source_url`, `coletado_em`,
  `uf`, licença, `adapter_versao`, `run_id` e detalhe bruto.
- **Assinaturas de fonte no manifesto** (`assinaturas`), o que liga a entrega à coleta
  exata que a produziu.
- 9 testes novos (`tests/test_v33.py`): invalidação por `min_conf`, por `--excluir`, por
  predicado OSM, isolamento entre UFs, caminho == hash da etapa, leitura de relation,
  posição dentro do polígono, supressão do membro homônimo e não-supressão sem
  identidade. Suíte: **65 testes**.

## v3.2.0 — 2026-08-10 (BRT)

### Corrigido
- **Divisa municipal era parede no matching** — o dedup particionava por município, então
  o mesmo estabelecimento visto por duas fontes a 2 m e 3 m de lados opostos da divisa
  nunca entrava no mesmo universo de comparação. Agora o blocking é por **grade + halo**
  (`--dedup-celula`, default 2.000 m; halo ≥ `raio_forte_m`) e a consolidação é
  **global**: a partição decide só quais PARES são avaliados, o cluster fecha uma vez.
  O município virou **atributo** da entidade, herdado da âncora.
- **Token de contexto e telefone-hub eram medidos por partição** — são propriedades da
  vizinhança. Por município, a mesma marca podia ser contexto de um lado da divisa e
  discriminante do outro. Passaram a ser globais.
- **Diâmetro do cluster media em projeção única** — o `_UF` guarda o bbox em graus e
  mede em metros na latitude do próprio cluster, para a consolidação global não herdar
  o erro de escala de uma projeção métrica única sobre uma UF larga.

### Adicionado
- **`poi_observacoes_*`** — elo observação → entidade: uma linha por registro de fonte
  que entrou no dedup, com `cluster_id`, `ancora`, posição, precisão e município. A
  entrega deixa de ser apenas "o registro que sobreviveu à fusão".
- **`cluster_id`** no padronizado — chave natural da âncora (`fonte:id_fonte`),
  determinística. Declarado no SKILL.md como **não permanente entre execuções**: ID
  perene exige store append-only com política de merge/split, fora do escopo.
- **`poi_source_snapshot_*`** — procedência tabular (release/snapshot, licença,
  `run_id`, data de coleta) viajando junto do dado, não só no manifesto.
- **Verificação de rastreabilidade** no `validate`: toda observação normalizada tem de
  aparecer no elo, e nenhuma entidade pode ficar órfã. Relatório: 13 verificações.
- Contagem de `clusters_entre_municipios` no manifesto — quantas entidades reúnem
  observações de municípios diferentes, ou seja, o que a partição antiga perdia.
- 5 testes novos (grade × bloco único, par na divisa, halo insuficiente recusado,
  `cluster_id` da âncora, elo sem perda). Suíte: **56 testes**.

### Alterado
- `--dedup-celula 0` reproduz a partição por município da v3.1.0. Modos `legado`,
  `exato` e `none` seguem por município e não produzem elo (declarado no relatório).
- `dedup_celula_m` e `dedup_halo_m` entram no hash de escopo de `dedup`.

## v3.1.0 — 2026-08-10 (BRT)

### Corrigido
- **BBOX de coleta dependia de `--excluir`** — `ibge.alvo()` removia os municípios
  excluídos **antes** de calcular `total_bounds`, e esse bbox alimentava a grade de tiles
  do Overture, o gate `raw.bbox` e o recorte do FSQ. Só que `excluir` não entra no hash de
  escopo de `fetch`/`raw`: duas verdades em conflito. Com um município excluído numa
  extremidade do estado, o bbox encolhia e uma execução posterior sem exclusão
  reaproveitava esse `fetch` como válido, com cobertura menor. Pior: a grade mudava de
  tamanho e o marcador `DONE` do tile `i` passava a atestar outro retângulo.
  Agora `ibge.bbox_coleta()` deriva o bbox da malha **completa** da UF e o congela em
  `bbox_coleta.json`. **Exclusão nunca toca o snapshot bruto — só o recorte de consumo.**
- **Guarda de grade** — se a grade de tiles calculada diverge da que está em disco, os
  marcadores `DONE` do Overture são apagados e o fato é registrado no manifesto
  (`marcadores_invalidados`). Cache gerado até a v3.0.0 cai exatamente nesse caso.

### Alterado
- `--excluir` **não reduz mais o volume de download** (reduz `territory` em diante). É o
  preço do reuso: uma coleta da UF serve para qualquer recorte municipal depois.
- `alvo()` devolve `bbox_coleta`; o `meta` do manifesto passa a trazer `bbox_coleta` e
  `bbox_alvo` separados.

### Adicionado
- `test_bbox_de_coleta_nao_depende_de_excluir` — prova, com a malha real de RR e um
  município de extremidade, que o bbox do alvo muda e o de coleta não. Suíte: **52 testes**.

## v3.0.0 — 2026-08-09 (BRT)

**Exatidão.** Mudança incompatível de esquema, defaults e motor de dedup, motivada pela
auditoria de duas execuções reais da v2.0.0 (Canoas-RS e Santa Maria-RS), ambas
APROVADAS 14/14 no gate antigo. Ver `references/MIGRACAO_V2_V3.md`.

### Corrigido
- **A fusão apagava estabelecimento real** (42 e 66 fusões indevidas comprovadas; ~71 e
  ~115 estabelecimentos distintos consolidados em outro). Novo motor `dedup_v3`:
  **token de contexto** por IDF local (200 m / 3 ocorrências), **Jaccard** sobre o núcleo
  discriminante, **token de 1 caractere preservado**, **núcleo curto exige mesmo
  segmento**, **telefone/site como evidência e como VETO**, **telefone-hub**, **marca
  igual exige 15 m**, **trava de diâmetro** (Kruskal com restrição).
- **`_name_core(nan)` devolvia `'nan'`** — NaN é truthy, `str(v or "")` não neutraliza.
  Dois registros SEM NOME casavam com `token_set_ratio('nan','nan') = 100` e fundiam a
  30 m mesmo com segmentos divergentes; o ramo dos sem-nome (12 m) era código morto.
  Corrigido também no modo `legado`.
- **Âncora sempre Overture** (100,0% de 1.682 fusões): `baixa` empatava com `sem` em
  `_PRIO` e o id hexadecimal do Overture ordenava antes de `node/`. Agora
  `alta > média > sem > baixa`, com desempate por `precisao_coord_m` e só então por id.
- **`bairro` não continha bairro** (95,4% e 81,5% traziam o nome do próprio município):
  o `locality` do Overture foi para `localidade_fonte`, `bairro` passou a ser bairro de
  verdade, `endereco_completo` deixou de ser contaminado e a divergência entre
  `localidade_fonte` e o município da geometria virou `flag_localidade_divergente`.
- **Nome de estabelecimento entrando como logradouro** (206 linhas detectáveis em
  Canoas): quando o logradouro não tem tipo de via e o resíduo tem, os dois são
  trocados (`endereco_parse_metodo = heuristica_troca`).
- **Recall do OSM em 47%**: predicado ampliado (`healthcare`, `craft`, `historic`,
  `government`, `military` abertas; `railway`, `public_transport`, `aeroway`,
  `man_made`, `building` por lista de valores) e `name` deixou de ser obrigatório.
- **23%–30% da base em "Outros"** com taxonomia disponível e não usada: `categoria_hier`
  chega ao normalize e `traduzir()` sobe a hierarquia até um nível curado; mapa
  `OV_TOPO` e ~45 categorias curadas novas.
- **FSQ falhava tarde** (401 depois de ~421 MB de OSM baixados): `foursquare.gate()`
  roda no início do `fetch`.

### Alterado (quebra compatibilidade)
- `--min-conf` default **0.50 → 0.0**. O corte apagava 22,1% de Canoas e mais de um
  terço de Santa Maria, em silêncio; `confianca` e `confianca_classe` viajam no dado.
- `COMUNS` ganhou `localidade_fonte` e `categoria_hier` (preenchidas no ADAPTADOR de
  cada fonte, não por alargamento da projeção). `territorio._compat_v3` deriva as duas
  de cache da v2 — não exige re-fetch.
- `PADRAO` ganhou `sem_nome`, `categoria_hier`, `precisao_coord_m`, `coord_empilhada`,
  `localidade_fonte`, `flag_localidade_divergente`, `n_registros_fundidos`,
  `dedup_motivos`, `nucleo_discriminante`.
- `osm_predicado` e `osm_sem_nome` entram no escopo de hash do `fetch` — mudá-los
  invalida a coleta, de propósito.

### Adicionado
- **`poi_dedup_vinculos_*`** e **`poi_rejeitados_*`** — as duas pendências abertas da
  v2.0.0. Vínculos trazem também as recusas e o motivo; rejeitados trazem cada linha
  descartada em `raw`/`territory`/`normalize`.
- **Gate semântico no `validate`**: fusão intra-fonte com nomes divergentes acima de
  `--max-fusao-suspeita` (default 2% dos clusters com fusão) **reprova a entrega**, com
  os pares gravados em `poi_fusao_suspeita_*`. Mais a checagem de cobertura da tabela de
  rejeitados. O relatório passou de 10 para 12 verificações.
- `tests/test_v3.py` — 36 testes, cada um reproduzindo um caso medido na auditoria.
  Suíte total: **51 testes**.
- `description` do SKILL.md compactada para **1.005 caracteres** (limite de 1.024 do
  instalador de skills), por sinônimos e supressão de redundância — nenhum termo de
  acionamento perdido: fontes, entregáveis, dedup, mapa A2L, manifesto e concessionárias
  seguem no texto.

### Pendências abertas
- **OSM `relation`/multipolygon** continua fora (Base Aérea de Canoas, 1.226 relations
  nomeadas na região Sul). Desenho especificado no SKILL.md; não entregue por não ser
  validável sem `.pbf` real em teste.
- **Temas `address`/`building` do Overture** — adiados; o CNEFE resolve melhor o mesmo
  problema.
- **Validação posicional contra CNEFE** — pertence a `ferramenta-logradouro-padrao` e
  `construcao-vias-publicas`; não deve ser reimplementada aqui.
- **`sul-latest.osm.pbf` é alvo móvel** — a Geofabrik publica versões datadas; pinar a
  data tornaria a execução repetível. O snapshot (mtime) já vai no manifesto.
- **`ov.operating_status` vem vazio** nesta release do Overture — a base não distingue
  ativo de encerrado sem o `date_closed` do Foursquare.

## v2.0.0 — 2026-07-31 23:40 (BRT) / 2026-08-01 02:40 (UTC)

**Reescrita do pacote.** A v1.0.0 não executava em instalação limpa
(`ModuleNotFoundError: No module named 'extrair_pois'`) e era a execução do RS
empacotada como skill genérica.

### Corrigido
- **Autocontenção** — removidos os `sys.path.insert("/sessions/kind-beautiful-bohr/...")`
  dos 8 scripts. Os módulos `extrair_pois`, `tratar_pois`, `osm_pbf`, `limites` e
  `categorias_pt` foram **vendorizados** em `poi_estadual/vendor/`, com sha por módulo
  no manifesto para rastrear drift em relação à skill-mãe.
- **Parametrização** — `RS_BBOX`, `POA`, `sul.osm.pbf`, `rs_saida`, `rs_keep.parquet` e os
  nomes `rs_exceto_poa_*` saíram do código. Tudo vem de `--uf/--excluir/--fontes/--base-dir`.
  O bbox e a grade de tiles derivam da geometria do alvo (validado: o bbox derivado do RS
  bate dígito a dígito com o `RS_BBOX` que estava fixo).
- **Início do zero** — nova etapa `init` carrega a malha do parquet local **ou da API IBGE**
  (`/localidades` + `/malhas v3`, EPSG:4674). A v1 exigia `rs_keep.parquet`, `tiles_rs.json`,
  `fsq_files.json` e `sul.osm.pbf` já em disco.
- **Duas implementações concorrentes** — `rs_driver.py` não era só o orquestrador antigo:
  era o módulo-núcleo que os outros 7 scripts importavam (`import rs_driver as D`). Foi
  substituído pelo pacote `poi_estadual/`, e com ele saíram os caminhos antigos:
  re-download de quadrantes Overture, `name IS NOT NULL` no scan FSQ, `iterrows()` no FSQ,
  ways+nós em query única, clip sem chunk, dedup global e bruto unificado.
- **Retomada insegura** — `manifesto.json` com **hash de escopo por etapa** e **gate de
  precedência**. Etapa interrompida por `--budget` fica `partial`, nunca `completed`;
  `export` não roda sobre `dedup` incompleto. Mudar `--min-conf`/`--excluir` invalida só
  as etapas afetadas, sem refazer a coleta.
- **Posição de way OSM** — `AVG(lat), AVG(lon)` (média, não centroide; cai fora de polígono
  côncavo) trocado por `representative_point()` sobre a geometria reconstruída.
- **Fronteira municipal** — clip com polígono em qualidade máxima e
  `--simplificar-graus 0` por padrão; simplificar virou opção com o efeito declarado.
- **Injeção HTML no mapa** — `nome`, `endereço`, `telefone`, `site`, `categoria` e `data`
  iam crus para `innerHTML`. Agora `esc()` em todo texto interpolado e `safeUrl()` no
  site (bloqueia `javascript:`); `<option>` escapado no lado Python.
- **Memória** — Overture lê o geoparquet em batches (a v1 carregava o tile inteiro em
  pandas antes de fatiar); FSQ concatena via DuckDB `union_by_name` em vez de `pd.concat`.
- **Lagoas na malha** — filtro genérico por `COD_MUNICIPIO` de 7 dígitos com prefixo da UF,
  contabilizado no funil. Sem `if RS`.

### Adicionado
- Entrypoint único `poi_estadual.py` (`run` / `status` / `diag`).
- Etapa `validate`: 10 verificações + `relatorio_qualidade_*.json`; reprova a entrega.
- Funil por etapa (`entrada = saída + descartados por motivo`) no manifesto.
- Exportação do padronizado também em **GeoParquet** (a v1 só entregava CSV).
- Bruto por fonte para **Overture e FSQ** (a v1 só tinha o do OSM separado).
- `pyproject.toml` com dependências fixadas por piso e extras `overture`/`dev`.
- `tests/` com 15 testes (pytest), incluindo amostra mínima ponta a ponta.

### Pendências abertas (fora do escopo desta entrega, por decisão do usuário)
- `poi_dedup_vinculos.parquet` e `poi_rejeitados.parquet` — auditoria linha a linha do
  dedup e dos descartes. Sem elas, o funil dá os totais por motivo, mas não o registro
  original de cada fusão.
- O termo **"SEM PERDA"** foi mantido na `description` a pedido. O corpo do SKILL.md
  documenta os descartes reais; a formulação exata seria "preservação máxima do bruto
  com filtros auditáveis".
- OSM: `relation`/multipolygon e POI sem `name` continuam fora da coleta.

## v1.0.0 — 2026-06-26 22:43:46 (BRT) / 2026-06-27 01:43:46 (UTC)
- Release inicial. Evolução estadual da `extracao-dados-poi-global` com as correções de
  escala validadas em produção no RS (632.234 POIs, exceto Porto Alegre): fetch-once +
  clip set-based; FSQ por shards densos com `COPY`; OSM em 2 scans e tags em
  `osm_tags_json`; Overture com tile baixado 1× e marcador `DONE`; dedup fuzzy por
  município; mapa A2L com Street View corrigido.
