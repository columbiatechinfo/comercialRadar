# Release atual: v3.3.5

---
name: ajuste-logradouro
description: "MARCA a forma canonica do LOGRADOURO e ORGANIZA o COMPLEMENTO em N bases de cadastro (saneamento/energia), sem sobrescrever o registro original. Tres camadas - normalizacao deterministica (abreviacao de tipo de via, titulo/patente, numeral por extenso e romano, vocabulario de truncamento de tipo), LEXICO APRENDIDO por mineracao com prova (mesmo numero exato mais raio metrico, substituicao 1a1 plausivel, support, decay, quarentena) e COMPLEMENTO ORGANIZADO em componentes tipados (QUADRA, LOTE, BLOCO, TORRE, CASA, APTO, SALA, LOJA, BOX) com auditoria do ruido. Acione para 'ajustar, padronizar ou normalizar logradouro', 'corrigir endereco sujo do cadastro', 'expandir abreviacao de rua', 'aprender abreviacoes da base', 'lexico de equivalencias', 'organizar complemento', 'quadra lote grudado QD5LT3', 'marcar sem alterar o original'. Derivada de ferramenta-logradouro-padrao: NAO resolve entidade e NAO segmenta campo unico grudado. CORSAN/Aegea, COPEL, SABESP, Enel."
---

# Ajuste de Logradouro (A2L) — v3.3.5

Fork enxuto de `ferramenta-logradouro-padrao`. Mantém **normalização + léxico aprendido +
complemento organizado**; descarta o motor de resolução de entidade.




**v3.3.5 — Número Estrito + Complemento CNEFE/RS.** O campo de número publicado passa a ser
**exclusivamente numérico**. Qualquer anotação/modificador é removida do número e incorporada
à visão de complemento, sem perder poder de discriminação no gate interno. Ex.: `1B` → número
`1` + `IMOVEL B`; `125-A` → `125` + `IMOVEL A`; `100 FUNDOS` → `100` + `FUNDOS`;
`500 AP 201` → `500` + `APARTAMENTO 201`. A chave interna de prova continua distinguindo
`1A` de `1B`. A taxonomia de complemento foi ampliada com elementos do padrão CNEFE/IBGE
(`ENTRADA`, `RUA_INTERNA`, `COMODO`, `COBERTURA`, `SUBSOLO`, `PORTARIA`, `SALAO`,
`PALAFITA`) e o contrato permanece **100% textual**: não há validação por POI, imagem ou mapa.

**v3.3.4 — Complement Intelligence.** Mantém todo o contrato v3.3.3 e adiciona uma camada de inteligência operacional sobre o complemento: `aj_compl_endereco_limpo`, segmentação estruturada em JSON (`ENDERECO`, `REFERENCIA`, `ACESSO`, `DESCRICAO`, `EMPREENDIMENTO`), utilidade operacional, método explícito do score heurístico, preservação da frase original de referência; campos legados de validação externa permanecem apenas por compatibilidade e não são utilizados (`referencia_status=NAO_APLICAVEL_TEXTUAL`, distância/fontes vazias). A gramática passa a reconhecer infraestrutura, logradouro/cruzamento, relações acima/abaixo/dentro/direita/esquerda e orientações sequenciais como `SEGUNDA CASA DEPOIS DA IGREJA`, sem promover referência para endereço cadastral.

**v3.3.3 — Complemento Semântico e Referencial.** O campo complemento deixa de ser tratado apenas como sublocalizador e passa a ser segmentado por natureza: parte real do endereço, ponto de referência/proximidade, instrução de acesso, descrição visual e nome de empreendimento. A saída mantém as colunas tipadas existentes e adiciona decisão cadastral explícita (`USAR_NO_ENDERECO`, `NAO_USAR_NO_ENDERECO`, `USAR_PARCIAL`, `REVISAR`), endereço real separado da referência e relações espaciais como `PROXIMO_A`, `EM_FRENTE_A`, `AO_LADO_DE`, `AO_FUNDO_DE`, `ATRAS_DE`, `ANTES_DE` e `DEPOIS_DE`. Conflitos do mesmo componente nunca são escolhidos silenciosamente.

**v3.3.2 — Léxico Nacional / RS hardening.** Esta release congela o hardening 3.2.x e
muda deliberadamente a unidade de conhecimento nominal para
`(scope_id, contexto, variante) -> canônico`. O vocabulário estrutural curado continua global;
regras nominais mineradas nunca atravessam município/território. Também entram decay por
`last_reinforced`, reativação de regra arquivada, autoridade hierárquica, indefinido simétrico,
proveniência de evidência, idempotência forte e `DIRECT > REFERENTIAL` no complemento.

**Gate v3.3:** zero vazamento entre scopes; replay não renova decay; arquivado reativa com prova
nova; conflito entre autoridades do mesmo nível nunca é resolvido por frequência; indefinido fecha
quando resolvido; `N(N(x)) == N(x)` na matriz adversarial; complemento direto vence referência.


**v3.3.2 — fechamento RS / escopo comprovado.** Para `scope_id` municipal IBGE (7 dígitos),
o aprendizado exige `colunas.scope_id` em cada fonte e valida 100% das linhas contra o município
declarado. Isso impede gravar dados de um município dentro do léxico de outro. Numerais compostos
de datas convergem (`VINTE E QUATRO DE MAIO` -> `24 DE MAIO`) e o hot path do léxico deixa de
revarrer todos os scopes a cada acesso, permitindo escala estadual com centenas de municípios.

**v3.2.5 — Filesystem & Audit Closure.** Última release de infraestrutura da série 3.2.x;
o núcleo semântico permanece byte a byte fora do escopo desta mudança.

| Achado pós-v3.2.4 | v3.2.5 |
|---|---|
| `runs/`, `.staging/` ou `failed/` podiam ser symlink e desviar publicação/retenção para fora de `--out` | áreas controladas e seus filhos imediatos são validados por `lstat/realpath`; symlink = **ERRO antes de qualquer escrita** |
| `lexico_path` podia ser o mesmo arquivo de `vocab_path`, config ou entrada | matriz de papéis por `realpath`; estado mutável tem pathname exclusivo e colisão aborta antes do commit |
| schema era estrito no topo, mas `autoridadee`/coluna desconhecida dentro da fonte passava | schema estrito em `fontes[]` e `colunas`; typo = ERRO |
| `vocab_path` explícito inexistente virava vocabulário vazio | caminho explicitamente informado precisa existir, ser JSON válido e conter schema mínimo de `tipo_via` |
| `source_id` `A` e `a` colidem em filesystem case-insensitive | unicidade por `casefold()` em todos os SOs |
| SHA físico do léxico mudava por status operacional e não formava cadeia semântica | `revision` = revisão física; `state_version` só cresce quando conhecimento muda; `state_sha256` exclui histórico/timestamps |
| `RUN_COMPLETED/RUN_FAILED` reescrevia `lexico.json` depois do commit | status operacional mora apenas em `runs/`/`failed/`; `marcar_run_status` virou no-op compatível |
| manifest misturava hash físico com versão do conhecimento | `a2l-run-manifest/3` registra separadamente `file_sha256`, `revision`, `state_sha256`, `state_version` e `state_changed` |

**Contrato de auditoria v3.2.5:** `state_sha256_after(run N) == state_sha256_before(run N+1)`
sempre que nenhum outro commit semântico intervier. Um run sem mudança pode aumentar `revision`,
mas mantém `state_version` e `state_sha256` inalterados.

**v3.2.4 — baseline anterior de recovery criptográfico.** O núcleo semântico permanece intacto.
A revisão posterior à v3.2.3 encontrou três riscos de auditoria/recovery que agora são gates:

| Achado | v3.2.4 |
|---|---|
| recovery confiava em `RUN_COMPLETED` mesmo com artefato truncado/corrompido | candidato a `latest` recalcula `size` + SHA-256 de todos os artefatos declarados; divergência vai para `failed/corrupted/` |
| `lexico_sha256_before/after` era medido fora do lock e podia descrever uma transação impossível sob concorrência | `LIO.atualizar_travado()` devolve SHA/version `before -> after` capturados dentro do mesmo lock do read-modify-write |
| fallback ENU podia tomar decisão diferente do `pyproj` na borda do raio | `aprendizado=true` exige `pyproj`; fallback continua permitido apenas no modo sem alteração de estado |
| manifest não distinguia fotografia carregada da transação efetivamente commitada | `a2l-run-manifest/2` registra `sha/version loaded`, `before`, `after` e `committed_at` |

**Recovery v3.2.4:** `latest.json` só é criado/reconstruído a partir do primeiro run
`RUN_COMPLETED` cujo `manifest.json` confere integralmente. Um run corrompido é removido da
árvore de publicação e preservado para perícia em `failed/corrupted/<run_id>/`.

**v3.2.3 — hardening operacional, recuperação e reprodutibilidade (baseline anterior).** O núcleo semântico da
v3.2.2 foi preservado. Esta release fechou os defeitos encontrados na revisão de crash, configuração,
entrada e artefatos:

| Achado | v3.2.3 |
|---|---|
| `aprendizadoo: false` era chave desconhecida e o default ligava aprendizado | chave desconhecida de topo = **ERRO**; `aprendizado` é obrigatório e explícito |
| `fontes:` malformado podia gerar `AttributeError` | estrutura YAML é validada antes de acessar qualquer campo |
| CSV com cabeçalho duplicado era renomeado pelo pandas (`x` → `x.1`) | leitura bruta do cabeçalho; coluna duplicada = **ERRO** |
| 100% das coordenadas inválidas podia chegar à projeção | `aprendizado=true` + zero coordenadas válidas = **ERRO** |
| `lexico_path`/entrada/config podiam colidir com `latest.json` | `latest.json`, `runs/`, `.staging/` e `failed/` são namespace operacional reservado |
| crash brutal deixava staging/run incompleto | startup reconciliation recolhe PID local morto, staging stale e `runs/` não concluído |
| janela entre rename e `RUN_COMPLETED` | `RUN_COMPLETED` é escrito **antes** do rename; `runs/<run_id>` nasce imutável e completo |
| mudança de arquivo durante a execução (TOCTOU) podia misturar versões | SHA-256 antes/depois da validação, antes do commit e antes da publicação; divergência aborta |
| run não registrava ambiente/código/inputs | `manifest.json`: versão/hash do código, config, inputs, léxico, runtime e hash dos artefatos |
| conteúdo `=...` virava fórmula real no XLSX | `strings_to_formulas=False` e `strings_to_urls=False` |
| file-based acumulava muitos artefatos | flags `xlsx`, `parquet`, `marcacao_por_fonte` e `retencao_runs` (default 0 = nunca apagar) |

**Publicação v3.2.5:**
```text
saida/
  latest.json
  runs/<run_id>/
    execucao.json            # já RUN_COMPLETED antes do publish
    manifest.json            # hashes + ambiente + linhagem dos insumos
    ajuste_logradouro.csv
    ...
  failed/<run_id>/
  .staging/<run_id>.tmp/      # contém owner.json enquanto o processo está vivo
```
No startup, a skill reconcilia staging órfão e runs incompletos sem tocar em processo concorrente
vivo do mesmo host. `latest.json` só aponta para run concluído **e íntegro segundo o manifest**.

A série **3.2.x fica congelada como baseline operacional**. A v3.3 implementa o modelo nacional
descrito originalmente em `ROADMAP_v3.3.md`; o arquivo agora funciona como registro de requisitos
e gates atendidos pela release.

**v3.2.2 — hardening operacional e de publicação (baseline anterior).** O núcleo semântico permanece igual; esta
versão fecha riscos de produção encontrados depois do gate semântico:

| Achado | v3.2.2 |
|---|---|
| `--out` na pasta da entrada podia sobrescrever um arquivo chamado `ajuste_logradouro.csv` | execução escreve em staging privado e publica em `runs/<run_id>`; entrada nunca compartilha pathname com artefato |
| reutilizar a pasta deixava `pares_mineracao.csv`/`marcacao_<fonte>.csv` velhos parecendo atuais | cada run é imutável; `latest.json` aponta apenas para o último run concluído |
| falha depois do commit do léxico deixava estado avançado sem explicar o destino da execução | `runs[].run_status`: `LEXICO_COMMITTED` → `RUN_COMPLETED` ou `RUN_FAILED`; run falho vai para `failed/<run_id>` e nunca vira latest |
| validação calculava zona por mediana das medianas, diferente da projeção | `geo.diagnosticar()` é a fonte única; mediana ponderada por todos os registros válidos |
| base de aprendizado multi-zona/multi-hemisfério seguia silenciosamente | ERRO; particione por partição UTM antes de aprender |
| `aprendizado: "false"` era string truthy e ligava aprendizado | booleanos top-level e parâmetros são estritos; sem coerção |
| `A→B→C→A` podia permanecer `ativo` embora não aplicável | saneamento completo do grafo por contexto; cadeia/ciclo inteiro vai para quarentena |
| CSV vazio saía sem cabeçalho e complemento variava colunas por cidade | schemas fixos para artefatos e todas as `aj_compl_*` sempre presentes |
| `source_id` com `/` quebrava filenames | regex segura `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` |
| `run_id` repetia no mesmo processo/segundo | timestamp UTC em microssegundos + UUID curto |
| fallback de lock podia ficar órfão | POSIX `fcntl`, Windows `msvcrt`, fallback `lockdir` com detecção de stale |

**Modelo de publicação v3.2.2:**
```text
saida/
  latest.json                  # ponteiro atômico para o último run concluído
  runs/<run_id>/               # conjunto completo e imutável de artefatos
  failed/<run_id>/             # perícia de execuções falhas (nunca é latest)
  .staging/                    # temporário; somente enquanto o processo está rodando
```
O consumidor deve ler `latest.json` e então abrir o `path` indicado. Não consuma arquivos soltos
da raiz: eles não existem mais por desenho.

**v3.2.1 — hardening de migração de estado.** O motor não mudou; o que faltava era o caminho
de UPGRADE de quem já tem léxico da v3.1:

| Achado | v3.2.1 |
|---|---|
| a migração só rodava dentro de `atualizar()`: com `aprendizado: false` o léxico v3.1 chegava cru em `mapa_ativo()` → `AttributeError: 'str' object has no attribute 'get'` | `LS.migrar_schema()` público, chamado por `LS.carregar()` — **toda** carga migra, independente do modo |
| `indefinidos` no formato antigo derrubava a exportação → `KeyError: 'contexto'` | migram para `indefinidos_legado_global` (guardados, nunca aplicados) |
| `runs[].ativos` contava um nível só e gravava `0` com equivalência ativa | `LS.contar_ativos()` percorre `contexto -> variante`; o histórico também passa a registrar `contextos` |

Migração idempotente (`v3.1→v3.2`, `v3.2→v3.2`, `{}→v3.2`) e verificada no gate com pipeline
completo nos dois modos. Rodada sem aprendizado **não reescreve** o arquivo do léxico: a migração
vale em memória, e o estado em disco só muda quando há algo novo a gravar.

**v3.2 — fechamento (3 guardas de persistência e semântica).** O léxico deixou de ser artefato
de uma execução e virou patrimônio entre cidades; estes são os defeitos que só aparecem nessa
escala:

| Achado | v3.2 |
|---|---|
| âncora persistida em `x/y` UTM, sem SRID: (lon −51, lat −30) na zona 22S e (lon −45, lat −30) na 23S dão o MESMO x/y — dois imóveis a 578 km viravam a mesma evidência | âncora em **WGS84** (`{num, lat, lon}`) e independência por **haversine**. UTM continua só no blocking da execução |
| `support` crescia sem limite enquanto só 100 âncoras eram lembradas: da 101ª em diante o mesmo imóvel votava de novo | `MAX_SUPPORT == MAX_ANCORAS == 100`; ao saturar, o support para e o registro é marcado `saturado` |
| `SOUZA→SOUSA` aplicado por token corrigia `RUA MARIA SOUZA`, onde nada foi provado | equivalência **contextual**: a unidade é `(contexto, variante)` — `RUA JOAO {}` + `SOUZA` → `SOUSA`. Outros nomes ficam intactos |
| `tokens_autoridade()` virava um set global de palavras do estado | **autoridade DO PAR**: vale o registro oficial que participou daquele par; sem ele, dominância de frequência **dentro do contexto** |
| `srid_metrico` da zona errada passava | zona do SRID informado × zona da mediana dos dados: divergência é **ERRO**; CRS não-UTM (3857) é ALERTA |

Migração automática: léxico v3.1 tem suas equivalências globais movidas para
`equiv_legado_global` (guardadas, **nunca aplicadas**) e as âncoras UTM viram contagem em
`ancoras_legado` — o support acumulado é preservado, a comparação espacial recomeça limpa.

**v3.1 — terceira rodada (4 gates + limpeza).** Sem mexer na arquitetura:

| Achado | v3.1 |
|---|---|
| `srid_metrico: 4326` era aceito e o blocking tratava GRAU como METRO (`dist_m = 14,87` entre pontos a milhares de km) | `geo.validar_srid`: CRS tem de ser projetado e em metros; whitelist preferencial SIRGAS 2000/UTM 31965–31985. A validação aborta antes de processar e `projetar()` também recusa |
| entrada com coluna `gid`/`x`/`logr_pre` era sobrescrita e sumia no drop | namespace interno `__a2l_*`; `aj_*` e `__a2l_*` na entrada **abortam** a execução, nunca são sobrescritos |
| `PROX CASA 10` virava componente físico `CASA 10` | marcador referencial detectado: componente mantido, mas `aj_compl_tier=REVISAR` + `aj_compl_risco=CONTEXTO_REFERENCIAL`; o trecho do próprio marcador (`EM FRENTE AO`) não vira `POSICAO` |
| `support` contava jitter de GPS de 26 m como 2 imóveis | voto por **âncora independente**: mesmo número a menos de `raio_independencia_m` (100 m) é a mesma evidência física |
| `lexico_aprendido.py` continuava no pacote com `# canonical = forma mais longa` | módulo **removido**; sobrou `lexico_primitivas.py` (plausibilidade, substituição 1↔1, aplicar, carregar) — as funções inseguras não existem mais |

**v3 — segunda rodada de revisão externa (9 gates).** A v2 corrigiu o que a 1ª revisão
apontou; a 2ª bateria atacou código que a v2 não tinha tocado e achou coisa pior. Corrigido:

| Ataque | Antes | v3 |
|---|---|---|
| `SILVA × SILVAA`, `JOSE × JOSEE` | aprendia a forma ERRADA (canônico = mais longa) | direção por AUTORIDADE → DOMINÂNCIA → `INDEFINIDO` |
| `SOUSA × SOUZA` | escolhia uma na marra | equivalência sem canônico, nunca aplicada, exportada p/ decisão |
| `AAA→BBB` + `BBB→AAA` | ambos ativos | ciclo → quarentena dos dois |
| `STA→SANTA` depois `STA→SANTO` | mantinha o vigente | conflito REBAIXA o vigente também |
| cadeia `A→B→C` | dependia da ordem de iteração | bloqueada |
| `APARECIDA`→APARTAMENTO, `CASAMENTO`→CASA, `BLOQUEIO`→BLOCO, `SALAO`→SALA, `LOTEAMENTO`→LOTE, `TRAS`→TORRE | alias casava por prefixo | guarda `(?![A-Z])` — alias tem de terminar ali |
| Boa Vista → EPSG 32020 (NAD27 Dakota do Norte) | fórmula `32000+zona` no hemisfério norte | tabela real: 31974 (Boa Vista), 31976 (Macapá) |
| coordenada `999,999` | virava `inf` e contaminava distância/célula | saneada para NaN antes de projetar |
| `modo_numero: estrtio` | caía no default compatível | typo em enum vira ERRO |
| `279/281` × `279` no modo compatível | formavam par | compatibilidade só entre tipo `NUMERO` |
| `JOAO DR SILVA` | virava `JOAO DOUTOR SILVA` | slot de título depende de haver tipo de via |
| aprendizado sem coluna `numero` | validação aceitava | ERRO |
| versão do léxico | sempre 1 | incrementa a cada gravação + trilha `runs` |
| `ANDAR/TERREO/QUITINETE/SOBRELOJA` | perdidos na saída | taxonomia única (`complemento_organizador.ORDEM`) |
| gate dizia "byte-a-byte" | comparava listas pós-pandas | comparação de campo bruto via `csv.reader`, com o nome certo |

**v2 — 1ª revisão externa:** os cinco achados que atingiam
esta skill foram corrigidos; os que pertencem ao motor de entidade (registry, transitividade
QD/LT, MATCH×MERGE, `teto_componente`, S/N com 2ª evidência, hierarquia lote→economia) foram
deliberadamente **não** absorvidos — reconstruí-los aqui desfaz o escopo.

| Achado | Correção nesta versão |
|---|---|
| Título expandido fora de posição (`AV BEIRA MAR`→`AVENIDA BEIRA MARECHAL`) | `normalizacao_contextual.py`: slot de título + homógrafo bloqueado |
| Numeral por extenso indiscriminado (`RUA MEIA PRAIA`→`RUA 6 PRAIA`) | conversão só no idioma `<numeral> DE <mês>` |
| Marca sem graduação de confiança | `aj_logr_tier` = CONFIRMA / ALTA / REVISAR / HUMANO |
| Número misturava base e anotação (`12A`) | `numero.py`: publica apenas a base numérica (`12`), migra `A` para complemento (`IMOVEL A`) e mantém a anotação somente na chave interna de prova |
| Fallback silencioso de schema | `validacao.py`: ERRO aborta antes de processar |
| Léxico last-write-wins | `lexico_io.py`: lock + re-leitura + `os.replace` atômico |

**Faz:** marca a forma canônica do logradouro, aprende equivalências da própria base, tipa e
ordena o complemento, e devolve auditoria de tudo que sugeriu ou descartou.

**Não faz (decisão de escopo, não limitação escondida):** `imovel_id`, crosswalk, union-find,
store append-only de IDs, correção por IA/CNEFE, libpostal, segmentação de campo único grudado.
Precisa disso → use a skill-mãe.

---

## Contrato de saída: MARCAÇÃO, nunca sobrescrita

Toda coluna e todo **valor lógico** de entrada volta intacto (invariante testado com `csv.reader`, sem inferência do pandas). O que a skill produz
entra em colunas novas, prefixo `aj_`:

| Coluna | Conteúdo |
|---|---|
| `aj_logr_base` | normalização mínima (tipo expandido, sem acento/pontuação) |
| `aj_logr_marcado` | **forma canônica sugerida** ao fim da cascata |
| `aj_logr_tier` | **quanto confiar**: `CONFIRMA` / `ALTA` / `REVISAR` / `HUMANO` |
| `aj_logr_origem` | estágios que alteraram: `SEGMENTO\|TIPO_VIA\|TITULO\|NUMERAL_DATA\|PODA\|VOCAB\|LEXICO` |
| `aj_logr_risco` | motivo do rebaixamento (`REMOCAO_SEGMENTO`, `PODA_LIXO`, `SEM_TEXTO`) |
| `aj_logr_alterado` | bool |
| `aj_num_tipo` | `NUMERO` / `FAIXA` / `KM` / `SN` |
| `aj_num_base`, `aj_num_modificador`, `aj_num_anotacao` | `279-A` → número publicado `279`; anotação `A` preservada e migrada ao complemento |
| `aj_num_secundario`, `aj_num_km`, `aj_num_complemento_derivado` | preservam a semântica retirada do campo número (`IMOVEL A`, `MODIFICADOR KM`, etc.) |
| `aj_num_canonico`, `aj_num_chave` | forma de exibição e identidade usada no gate |
| `aj_autoridade` | se o registro veio de fonte oficial declarada |
| `aj_numero_int` | base inteira — mantida só para compatibilidade com a v1 |
| `aj_compl_organizado` | string canônica ordenada (`QUADRA 5 · LOTE 3`) |
| `aj_compl_<COMPONENTE>` | schema fixo: uma coluna para **todo** componente da taxonomia, vazia quando ausente |
| `aj_compl_tier` | `CONFIRMA` ou `REVISAR` (contexto referencial) |
| `aj_compl_risco` | `CONTEXTO_REFERENCIAL` quando o complemento aponta para OUTRO imóvel |
| `aj_compl_identificador` | valor sem rótulo (mantido, sinalizado — não descartado) |
| `aj_compl_descartado` | ruído removido, para auditoria |

Quem aplica a correção é o processo a jusante. A skill sugere e prova; não decide pelo cadastro.

---

## Tier — o que cada nível significa

| Tier | Origem | Como usar a jusante |
|---|---|---|
| `CONFIRMA` | dicionário fechado em posição fixa: tipo de via no slot 0, acento, pontuação, zero à esquerda | aplicável em massa |
| `ALTA` | guardado: título no slot, idioma de data, vocabulário de tipo, léxico com support ≥ 2 | aplicável com amostragem |
| `REVISAR` | houve **perda de texto**: segmento entre parênteses, bairro após `-`, poda de lixo | fila humana |
| `HUMANO` | sobrou pouco ou nada de via | fila humana, prioridade |

## Pipeline

0. **VALIDA** — de-para × arquivo real. `ERRO` aborta (código 2); `ALERTA` fica em
   `validacao.json`; `METRICA` mede cobertura. Nada processa antes disso passar.
1. **LOAD** — N fontes CSV com de-para por fonte; todas as colunas nativas preservadas.
2. **NORMALIZA** — base → hardening → vocabulário de tipo de via. Memoizado por valor distinto.
3. **PROJETA + PAREIA** — WGS84 → SRID métrico (UTM SIRGAS 2000 autodetectado pela longitude
   mediana); blocking **set-based** por `(base do número, célula, célula)` com vizinhança 3×3 e
   filtro final pela **chave completa** do número (`279|A`).
4. **MINERA + PROMOVE** — `minerar` extrai substituições 1↔1 plausíveis dos pares; `atualizar`
   aplica support ≥ N imóveis distintos, decay (meia-vida 180 d), quarentena de conflito,
   blacklist — tudo **sob lock**, com re-leitura do disco e gravação atômica.
5. **MARCA** — reaplica a cascata com o léxico **já atualizado nesta rodada** (o ganho não espera a
   próxima execução) e organiza o complemento.
6. **GRAVA + FECHA STAGING** — gera o conjunto completo, grava `RUN_COMPLETED`, `manifest.json` e fsync; CSV vazio mantém cabeçalho.
7. **PUBLICA** — rename atômico do staging já completo para `runs/<run_id>`; depois atualiza `latest.json`. Falha vai para `failed/<run_id>` e nunca substitui o latest.

---

## Decisões justificadas

- **Por que existe cruzamento numa skill que "só ajusta texto".** O léxico só aprende
  `tokenA ≡ tokenB` a partir de par confirmado por evidência independente — **mesmo número exato +
  distância apertada**. Sem par não há prova, e sem prova o aprendizado vira chute. É o mínimo de
  cruzamento necessário, e nada além dele: os pares morrem depois da mineração.
- **Blocking set-based em vez de STRtree.** Elimina a dependência de shapely e roda como hash join:
  a célula = `floor(coord/raio)` garante que todo par a ≤ raio cai em um dos 9 offsets, e o offset
  `célula_b − célula_a` é único, então **nenhum par aparece duas vezes**. Equivalência com a força
  bruta O(N²) é testada no gate, não presumida.
- **A direção do léxico exige evidência externa.** Detectar que dois tokens são equivalentes e
  decidir qual deles é o certo são perguntas diferentes; misturá-las foi o que produziu
  `SILVA→SILVAA`. Ordem: fonte `autoridade: true` (CNEFE/base oficial) → dominância de frequência
  (default 5×, piso 3) → `INDEFINIDO`. Sem autoridade declarada, uma equivalência genuína entre
  formas igualmente frequentes **não** é aprendida — é o custo de não chutar grafia.
- **Publicação numérica, gate estruturado.** A saída de número contém apenas a base; a anotação continua no gate interno, então `279-A` ≠ `279-B` ≠
  `279` no modo estrito (default). Perde-se evidência quando uma fonte omite o sufixo — é troca
  consciente de recall por pureza da prova; `modo_numero: compativel` afrouxa quando a base exigir.
- **Expansão contextual em vez de token-a-token.** Título só expande no slot posterior ao tipo de
  via, com nome depois dele, e homógrafo (`MAR`, `BAR`) nunca expande. Numeral vira dígito só no
  idioma `<numeral> DE <mês>`. Perde-se cobertura de numeral no nome; ganha-se ausência de
  corrupção semântica silenciosa — numa skill que MARCA para revisão, precisão vale mais.
- **Tier em vez de marca plana.** Sem graduação, quem consome não tem como aplicar em massa o que
  é seguro (`R`→`RUA`) e reter o que é lossy (poda de token). O tier é o pior estágio acionado.
- **Validar antes de processar.** Custa uma leitura extra dos arquivos (3,7 s dos 17,5 s em 300k
  registros). É o preço de não processar milhões de linhas com uma coluna zerada por typo no
  de-para — trade-off aceito.
- **O que é global e o que é contextual.** Equivalência ESTRUTURAL (`ACESS→ACESSO`) é global e
  vive no `vocabulario_aprendido.json`, que é curado. Equivalência de NOME minerada da base é
  contextual por definição — sobrenome raro não é sobrenome errado. `lexico_promocao_global.csv`
  lista variantes que se repetem em ≥3 contextos como **sugestão** de promoção manual; a skill
  nunca promove sozinha.
- **Support conta evidência física, não linha.** Com `raio_independencia_m` alto o léxico aprende
  menos e mais devagar; é deliberado — "support 2" só vale se significar dois imóveis distintos.
- **Adição e truncamento nunca viram léxico.** `SANTOS` vs `SANTOS DUMONT` (0↔1) e `XAVIER` vs
  `XAVIER DA SILVA` (0↔2) são rebaixados, não aprendidos. Só substituição 1↔1 plausível
  (prefixo, mesma chave fonética ou edição ≤ 1) é prova.
- **Memoização por valor distinto.** Normalizar linha a linha é o gargalo: numa base municipal os
  logradouros distintos são ordens de grandeza menos que os registros. Uma chamada por valor +
  map vetorizado — função pura, saída idêntica. Medido: **69,0 s → 11,2 s** em 300k registros
  (6,2×), mesma contagem de marcações.
- **`teto_bloco` em vez de rezar.** Bloco `(número, célula)` acima do teto é **podado e registrado**
  — evita o custo quadrático de número popular em condomínio denso sem esconder a exclusão.
- **Dois motores de similaridade bit-idênticos.** `rapidfuzz` é o caminho rápido; o fallback
  puro-Python reimplementa a mesma métrica (InDel normalizada = `2·LCS/(|a|+|b|)`). O gate prova
  paridade < 1e-9 em 4.000 pares — a skill roda sem rapidfuzz sem virar outra skill.
- **Projeção.** Com `aprendizado=true`, `pyproj` é **obrigatório** para que todas as máquinas
  decidam o raio métrico pelo mesmo motor. Sem aprendizado, o fallback de plano tangente local
  continua disponível e o modo é registrado em `execucao.json`. Distância em graus é proibida.

---

## Parâmetros

| Parâmetro | Default | Quando mexer |
|---|---|---|
| `raio_aprendizado_m` | 30 | Trava espacial da prova. Subir só com GPS ruim/rural — raio largo polui o léxico |
| `min_support_equiv` | 2 | evidências físicas **distintas**: mesmo número dentro de `raio_independencia_m` em WGS84 conta uma vez |
| `teto_bloco` | 200 | Poda de bloco denso |
| `apenas_entre_fontes` | false | `true` = só aprende com par de fontes diferentes (mais conservador) |
| `modo_numero` | estrito | `compativel` aceita modificador ausente de um lado |
| `abortar_em_alerta` | false | `true` em produção: ALERTA de schema vira parada (código 3) |
| `ratio_dominancia` | 5 | quantas vezes a forma canônica precisa dominar, sem autoridade |
| `min_freq_dominancia` | 3 | piso de ocorrências para a dominância valer |
| `raio_independencia_m` | 100 | abaixo disso, mesmo número = mesma evidência física (não vota de novo) |

Declare a fonte oficial com `autoridade: true` na lista de fontes — sem ela o léxico só aprende
por dominância de frequência, e o alerta de validação avisa.
| `hardening` | true | Título/patente, numeral extenso/romano, lixo posicional |
| `aprendizado` | true | `false` = léxico só em aplicação (sem minerar, sem coordenada) |

O léxico é **persistente entre rodadas**: aponte sempre o mesmo `lexico_path`. Desde a v3.2.5,
`revision` mede gravações físicas e `state_version`/`state_sha256` medem o conhecimento. Support,
decay, quarentena e histórico evoluem de forma auditável. Revise `quarentena` e `candidato` antes
de confiar cegamente.

---

## Como rodar

```bash
pip install pandas pyyaml rapidfuzz pyproj pyarrow xlsxwriter   # pyproj obrigatório para aprendizado
python exemplos/gerar_bases_exemplo.py
python ajustar_logradouro.py config.exemplo.yaml --out saida
python selftest.py                                             # GATE — invariantes semânticos + operacionais
# produção reproduzível: instale/pin `requirements-production.txt` ou preserve o ambiente do manifest
```

`selftest.py` é gate obrigatório nas duas pontas de qualquer alteração. Além da bateria semântica, a v3.2.5 inclui ataques operacionais:
paridade de similaridade, casos-âncora de normalização, **bateria adversarial de fidelidade**
(`adversarial.py`: 23 casos com texto E tier esperados), número estruturado, complemento sem
falso positivo de prefixo, blocking × força bruta, **EPSG nos dois hemisférios**, sanitização de
coordenada, **cânone/ciclo/conflito/cadeia do léxico**, validação que aborta (schema, enum, faixa),
24 escritores concorrentes sem perda, versionamento, **SRID informado em metros**, **namespace
reservado e colunas homônimas da entrada**, **contexto referencial**, **support imune a jitter**,
**âncora entre zonas UTM**, **teto de support**, **aplicação contextual**, **zona do SRID**,
**upgrade de léxico v3.1 com e sem aprendizado**, sobrescrita de entrada, stale output,
falha pós-commit, booleanos estritos, source_id seguro, multi-zona, ciclo de 3+ nós, schema de CSV
vazio, schema fixo de complemento, `run_id` único, **config estrita**, YAML malformado, cabeçalho
duplicado, zero coordenadas válidas, colisão de `latest.json`, recovery de crash, manifest/hashes,
TOCTOU de entrada, proteção de fórmula XLSX, **corruption recovery por manifest**, **cadeia física dentro do lock + cadeia `state_sha256/state_version`**, confinamento anti-symlink, colisão de papéis por `realpath`, schema estrito em fontes/colunas, `pyproj` obrigatório no aprendizado, preservação do original e **idempotência**.

`adversarial.py` é memória de regressão: todo caso real que aparecer entra lá. A lição da revisão
é metodológica — medir colisão entre nomes normalizados não mede fidelidade, porque
`AVENIDA BEIRA MARECHAL` não colide com nada e ainda assim destrói informação.

---

## Quando escalar

- **Base que não cabe em memória** → mesma lógica em PostGIS, com o gate igual:
  ```sql
  CREATE INDEX ON cad USING GIST (geom);            -- geom em SRID métrico
  CREATE INDEX ON cad (numero_int) WHERE numero_int IS NOT NULL;
  SELECT a.gid, b.gid, ST_Distance(a.geom, b.geom) AS dist_m
    FROM cad a JOIN cad b
      ON a.numero_int = b.numero_int               -- gate de número primeiro
     AND a.gid < b.gid
     AND ST_DWithin(a.geom, b.geom, 30)            -- trava espacial em metros
   WHERE a.numero_int IS NOT NULL;
  ```
- **Milhões de linhas file-based** → trocar o `pd.concat` dos 9 offsets por DuckDB (mesmo equi-join,
  colunar, sem servidor); a similaridade continua no Python para manter o veredito idêntico.
- **Resíduo que a normalização não fecha** (typo grosseiro, rua inexistente na base) → é fronteira
  desta skill: sobe para `ferramenta-logradouro-padrao`, que aterra no logradouro oficial CNEFE.
- **`road_id` oficial antes do fuzzy** (proposto na revisão) → ganho real, mas exige CNEFE por
  município como insumo **obrigatório**. Fica como modo opcional futuro, não como pré-requisito:
  a skill precisa continuar rodando em base sem referência oficial.

---

## Alinhamento prévio (antes de base real)

Confirmar: **(1)** fontes e de-para; **(2)** SRID métrico do município (multi-UF roda por zona);
**(3)** volume; **(4)** `raio_aprendizado_m` pela qualidade do GPS; **(5)** léxico novo ou
continuação de um existente; **(6)** POC vs produção. Pular só com "pode ir direto".


## Contrato v3.3.3 — complemento semântico

Além das colunas `aj_compl_<TIPO>`, a saída passa a publicar:

- `aj_compl_natureza_endereco`: `PARTE_DO_ENDERECO`, `NAO_ENDERECO`, `MISTO` ou `INDEFINIDO`;
- `aj_compl_decisao_cadastral`: `USAR_NO_ENDERECO`, `NAO_USAR_NO_ENDERECO`, `USAR_PARCIAL` ou `REVISAR`;
- `aj_compl_endereco_real`: somente a parte estrutural que pode participar da identificação do endereço;
- `aj_compl_referencia`, `aj_compl_referencia_tipo`, `aj_compl_relacao_referencia`;
- `aj_compl_acesso`, `aj_compl_descricao`, `aj_compl_empreendimento`;
- `aj_compl_conflitos`, `aj_compl_residuo`;
- `aj_compl_<TIPO>_origem` e `aj_compl_<TIPO>_candidatos` para auditoria por componente.

Exemplo: `AP 203 BL B PROX ESCOLA ENTRADA LATERAL` resulta em endereço real `BLOCO B · APARTAMENTO 203`, referência `ESCOLA`, relação `PROXIMO_A`, acesso `ENTRADA LATERAL` e decisão `USAR_PARCIAL`.
