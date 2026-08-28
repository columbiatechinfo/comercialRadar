# ComercialRadar — Documentação Completa (fonte única de verdade)

> Objetivo: permitir retomar o projeto em **qualquer chat novo** com contexto total.
> Projeto: `C:\Users\ceo\Documents\Sistemas\comercialRadar` · Windows 11 · PowerShell + Bash (Git).
> Python no `.venv` (`.venv\Scripts\python`). Node/TypeScript via `npx ts-node`.

---

## 0. Frontend web + API local (NOVO — jeito principal de operar)

```
.venv\Scripts\python server.py   →  http://localhost:8765
```

**`server.py`** (FastAPI + WebSocket) serve o **`frontend/`** (Leaflet + CartoDB claro) e orquestra tudo:

- **Header** com 2 modos: **Importar planilha** (com download da planilha modelo em
  `/api/template`) e **Mineração de área** (achar tudo que existe, sem planilha).
- **Polígono obrigatório**: o usuário desenha a área válida no mapa (leaflet-draw), salva em
  `areas/area_atual.json`. **Nenhum job inicia sem área.**
  > **O polígono é FOCO, não filtro de gravação** (regra do usuário, 04/08/2026).
  > Todo POI encontrado é gravado; o que cai fora entra identificado por
  > `cidade`/`uf` (extraídas do endereço pela mesma regra da ingestão) e apenas
  > **não aparece na tela** enquanto aquela área está em foco — reaparece ao
  > selecionar o município dele. Antes, `gate_registro` zerava `match_valido` e o
  > ingestor descartava na primeira checagem: numa mineração de Canoas, **57 POIs
  > válidos eram jogados fora contra 26 gravados**, todos já pagos em tempo de
  > busca. O tile fotografa muito além da faixa desenhada, e no dia em que a
  > cidade vizinha for minerada ela já começa com parte do trabalho pronto.
  > `ingerir_registro` devolve `inserido` ou `inserido_fora` — os dois gravam.
  > Quem quiser o banco restrito à área usa **"Limpar banco fora da área"**, que é
  > uma decisão explícita, o oposto de um descarte silencioso.
- **Limpar banco fora da área**: botão no painel → `POST /api/limpar-fora` (com `dry_run` de
  prévia + confirmação). Remove `pois` + derivadas fora do polígono.
- **Jobs como subprocess**: `POST /api/jobs` roda `search_from_sheet.py --area ...` (planilha)
  ou `minerar_area.py` (mineração). Um watcher acompanha o JSON incremental do coletor,
  **ingere cada registro novo no Postgres na hora** (`realtime_ingest.py`) e transmite via
  **WebSocket `/ws`**: eventos `poi` (marker cai no mapa com animação), `progresso` (cards),
  `job` (estado) e `log` (console).
  - **Contagem do progresso (corrigido 02/07/2026):** o watcher fotografa um **baseline**
    (`key→assinatura`) do JSON ANTES do job e conta só o delta — senão recontava o arquivo
    inteiro (results_existentes + novos) e re-ingeria os já-gravados, inflando "processados"
    além do total (barra 100% falsa) e "gravados no banco". A barra (`processados/total`) vem
    do **log** (`POIs X/Y`, `✅ X/Y`, `célula X/Y`), que é o número exato; `sem_match` é
    derivado (`processados − sucessos`). Ver `_emitir_progresso`/`_thread_watcher` no server.
  - **Dedup por place_id no watcher (corrigido 02/07/2026):** o JSON da planilha tem place_id
    REPETIDO (várias linhas → mesmo lugar; o banco colapsa por place_id, mas o JSON preserva
    cada linha). Sem dedup, registros irmãos oscilavam a cada passada de 2s → re-ingeriam em
    loop (cada delete+recreate gera id novo → o front acumulava markers → chips e "Gravados"
    explodiam; ex. "Maps direto 8.956" > total do banco) e os `ok` duplicados eram contados
    como novidade ("Encontrados 888" num run Gemini-direto, que nem toca no Maps). `_dedup`
    mantém 1 registro (o mais rico) por chave no baseline e no watcher; "gravados" conta
    **keys distintas** (`ingeridos_keys`), não inserções.
- **Markers** estilizados por categoria (pin estilo Google/Waze com emoji + cor), clusters
  customizados, **modal** ao clicar (fotos, avaliação, horários, reviews, origem da planilha,
  link pro Maps).
- **Chips de filtro por ORIGEM** (Maps direto · Vizinhos · OpenAI · Gemini · Web · Descobertos)
  **+ por ATRIBUTO** (🏢 Com CNPJ · 📞 Com telefone · 📷 Com foto · 📸 Street View · ⚠️ Sem
  telefone), combináveis em AND. `/api/pois` devolve `tem_cnpj/tem_tel/tem_foto/tem_sv` e
  `/api/stats` os contadores. O card "Dados do banco" mostra telefone/CNPJ/street view com %.
- **Busca flutuante sobre o mapa** (estilo Google Maps): input no topo-centro; ao focar, escurece
  o mapa (overlay+blur) e mostra autocomplete ao vivo (client-side sobre `allPois`, debounce
  110ms, ranking por prefixo/nome/categoria, tags CNPJ/web, navegação ↑↓/Enter/Esc, highlight do
  termo). Selecionar → zoom 18 no ponto + abre o modal.
- **Rodar a cascata PELA WEB:** aba 💎 → botão Iniciar dispara `enriquecer_tudo.py --sem-ingest`
  (o watcher do server é quem grava no banco e transmite ao mapa — o subprocess NÃO ingere, para
  não haver dois processos no delete+recreate do mesmo place_id). Rodado no terminal (sem server),
  o `enriquecer_tudo` ingere direto. Não precisa mais rodar só via código.
- **Divisas territoriais**: `GET /api/malha?lat=&lng=` baixa a malha municipal da UF no IBGE
  (cache em `malhas/<UF>.geojson`; resposta do IBGE vem gzip — o endpoint descomprime).
  Overlay com tooltip do nome do município; toggle "🗺️ Divisas".
  **A malha segue o mapa**: ao parar de navegar (`moveend`, zoom ≥ 6) o front pede a UF do
  centro e acumula os estados visitados, uma camada `L.geoJSON` por UF em `malhaGrupo`.
  Sem `lat/lng` cai na UF majoritária do banco — era o comportamento único antes, e por isso
  só apareciam as divisas do PI (a base de POIs é de Parnaíba) mesmo trabalhando em PE.
  A UF do ponto sai da malha de estados (`malhas/_ufs.geojson`, uma vez) com
  `qualidade=intermediaria`: na `minima` as divisas são generalizadas e Itambé-PE, a 2 km da
  fronteira, caía em PB.
- **Tipo de mapa**: botão "Mapa ▾" na topbar (`#base-switch`, montado por `app.js`) com
  limpo (OSM) · satélite · satélite+ruas · escuro; a escolha persiste em `localStorage`.
  Fica na topbar de propósito: sobre o mapa todos os cantos já são de `#painel`, `#stats`,
  `#filtros` e do zoom do Leaflet, todos com z-index maior — o botão sumia atrás deles.
- **Cache do front**: `/` reescreve `style.css`/`app.js` com `?v=<mtime>` e `/static` responde
  `Cache-Control: no-cache`. Sem isso o navegador segurava css/js antigos por horas depois de
  uma edição (o seletor de mapa "não aparecia" porque o CSS dele nem tinha sido baixado).
- A área de trabalho atual é o **polígono oficial do município de Parnaíba (IBGE 2207702)**,
  salvo em `areas/area_atual.json` (55 vértices) — em 02/07/2026 o banco foi limpo por ele
  (134 removidos) e as linhas de planilha fora dele viraram `status='erro'` no JSON para
  reprocesso via `--retry-failed` (planilha + JSON de estado copiados p/ `uploads/`).

Arquivos: `server.py` · `frontend/index.html|style.css|app.js` · `area_utils.py` ·
`realtime_ingest.py` · `minerar_area.py`. Deps novas no .venv: `fastapi uvicorn python-multipart`.

**`minerar_area.py`** — mineração sem planilha: grade de células (default 150m) sobre o
polígono → Google Places **Nearby Search** por célula (até 60/célula, dedupe por place_id) →
JSON incremental no formato do ingester (`fonte='descoberto'`, status `minerado`). `--details`
liga Place Details (telefone/horários/reviews — **pago**). Chave: `MAPS_API_KEY` no .env.

**`search_from_sheet.py --area areas/area_atual.json`** — o gate de área vale em TODAS as
camadas (Maps direto, vizinhos, OpenAI, Gemini e descobertos; descoberto fora do polígono nem
entra no JSON).

**`realtime_ingest.py`** — mesmas regras do `src/ingest.ts` (pula `match_valido=False`, guard
Brasil, delete+recreate por place_id) + gate de polígono; usado pelo watcher do server. O
`src/ingest.ts` continua valendo para ingestão manual em lote.
- **Dedup sem place_id (corrigido 02/07/2026):** o Gemini NÃO retorna place_id, então os
  `recuperado_gemini` não eram deduplicáveis por place_id e cada rerun os RE-INSERIA (455
  duplicatas acumuladas, ex. "Livraria Harmonia" 6×). Agora, sem place_id, o ingestor
  deduplica pela **origem** (`fonte` + `sessao` + `nome_original`). ⚠️ O `src/ingest.ts` (lote)
  ainda NÃO tem essa regra — se reusado para dados do Gemini, replicar lá.

**Sobre os dados dos recuperados/descobertos:** o modal exibe tudo que existe no banco (testado).
Os `recuperado_gemini` costumam vir esparsos porque o grounding do Gemini responde por busca web
(dados por item variam; **nunca** traz fotos nem textos de avaliações). Os `descoberto` vêm de
candidatos vizinhos do Maps (só nome+coord+place_id, sem painel).

## 0.1 Enriquecimento em CASCATA (implementado 02/07/2026 — substitui o Gemini caro)

Aba **💎 Enriquecimento** = 1 job `enriquecer_tudo.py` que roda os métodos EM CASCATA
(complementares, não opcionais): cada POI pobre (falta telefone/endereço/categoria, ou é
descoberto/recuperado raso sem foto) passa por Maps → se continuar pobre → Web → e todo
localizado ganha Street View. Estado de "pobre" mantido em memória entre as fases.
Colunas novas em `pois` (por ALTER TABLE + schema.prisma): `cnpj, razao_social,
nome_fantasia, natureza_juridica, cnae, situacao_cadastral, socios (JSON QSA), instagram,
email, resumo_avaliacoes, streetview_path, fontes_web`. Os 3 scripts abaixo também existem
soltos (reusados pela cascata), mas o fluxo padrão é o `enriquecer_tudo.py`.

1. **🔗 `enriquecer_maps.py`** — descobertos/recuperados_ia RASOS (com maps_url, sem telefone
   nem fotos) → abre cada maps_url individualmente (browser+proxy, mesma infra) e extrai o
   painel completo (fotos/reviews/telefone/horário). GRÁTIS (banda de proxy).
   ⚠️ **Bug de telefone via maps_url (corrigido 02/07/2026):** abrir por `goto(maps_url)` NÃO
   respeita o heurístico painel/lista (a URL /place/ traz o painel embutido, mas os botões de
   detalhe — endereço/telefone — renderizam com atraso, às vezes >4s). O `_extrair` esperava só
   1,8s e desistia se `aguarda_painel_ou_lista != "painel"` → telefone vazio em ~100% (0/34 num
   run real, contra 69% na coleta original). Correção: espera ATIVA por
   `button[data-item-id=address|phone]` (até 12s) + respiro extra se o botão de telefone ainda
   não veio. Não depender do painel/lista ao abrir por URL.
2. **🌐 `minerar_web.py`** — o RESÍDUO (nao_encontrado/erro/divergente) → SERP no **Yahoo BR**
   via Playwright+proxy+stealth (⚠️ Google=CAPTCHA; **Bing detecta e serve resultado-ISCA** —
   lixo proposital tipo TikTok/counterfeit; DDG/Mojeek/Ecosia bloqueiam; Yahoo usa o índice
   do Bing com gating tolerante) → visita top-5 páginas (aiohttp) → **pré-filtro local por
   regex** (tel/CNPJ/CEP/endereço-da-cidade/IG/e-mail/horário/nota) → digest mínimo →
   **LLM barato só p/ estruturar** (gpt-4o-mini se OPENAI_API_KEY existir — ela SAIU do .env
   na rotação!; senão Gemini flash thinkingBudget=0) → CNPJ candidato validado na
   **BrasilAPI/Receita Federal (grátis, dados abertos gov.br)**: razão social, CNAE, situação,
   QSA. Validação: só é `recuperado_web` com SINAL FORTE (endereço citando a cidade, telefone,
   instagram ou CNPJ confirmado). Teste real: 9/10 recuperados do resíduo difícil por
   US$0,005. ⚠️ wait_for_selector NÃO funciona (HumanSession bloqueia CSS → nunca fica
   "visible") — usar polling por count().
3. **📸 `streetview_capture.py`** — print do panorama do Street View na coordenada de cada POI
   (Playwright headless; Google Earth Pro é desktop, inviável em lote) → `streetview/<id>.jpg`
   servido em `/streetview/`, aparece como capa no modal. Sem pano → 'NA'. GRÁTIS.

`realtime_ingest`: sem place_id deduplica por (fonte+sessao+nome_original) e o delete+recreate
**preserva** os campos de enriquecimento que o registro novo não traz.

## 0.2 Download de imagens para o banco (`baixar_imagens.py` — processo pós, à parte)

Baixa TODAS as imagens para DENTRO do banco (bytes) + a DATA de captura de cada uma:
- **Fotos do Maps** → `images_urls.dados` (BYTEA) + `data_imagem` (data do **EXIF**, que o Google
  preserva na maioria — ~60%; formato YYYY-MM-DD) + `bytes_tam`/`content_type`.
- **Street View** → tabela **`streetview_imgs`** (`dados` BYTEA, `data_captura` YYYY-MM do
  panorama via **API de metadados do Street View — grátis**, `pano_id`, `lat/lng`).
- Idempotente (só o que ainda não tem bytes). ~1,6 GB no total (1,1 GB fotos + 0,5 GB SV; o
  Postgres guarda BYTEA grande fora da linha via TOAST). `MAPS_API_KEY` no .env/hardcoded.
- ⚠️ o delete+recreate do ingestor apaga as fotos (e seus bytes) num re-enriquecimento — rodar
  ISTO por ÚLTIMO, depois de Maps/Web/StreetView finalizados.

⚠️ **Bugs do enriquecimento corrigidos (03/07/2026):**
1. **`enriquecer_tudo` não gravava standalone** — as fases Maps/Web salvavam num JSON que só o
   watcher do SERVER ingere. Rodado no terminal (sem o web ligado), o trabalho ficava preso no
   JSON (só a fase Street View, que grava direto, aparecia no banco). Corrigido: `_ingerir(reg)`
   chama `realtime_ingest.ingerir_registro` DIRETO em cada POI tocado nas fases Maps e Web.
2. **CNPJ sempre 0** — o filtro de município comparava `"parnaíba"` (com acento, da var cidade)
   com `"PARNAIBA"` (sem acento, da Receita) → `not in` sempre True → todo CNPJ rejeitado; e o
   substring casava "Santana de Parnaíba"/SP. Corrigido em `minerar_web._processar_poi`: filtro
   por **UF** (`rf.uf == uf`) + município com **igualdade normalizada sem acento** (`_sem_acento`).
   Os CNPJs vêm dos snippets do Yahoo (econodata/cnpj.biz) e a BrasilAPI valida (razão social,
   CNAE, situação, QSA).
3. **Telefone com ruído** — o LLM às vezes concatenava vários números (um POI veio com 8).
   Corrigido: fica só o 1º match de `_RE_UM_TEL`; rejeita placeholder (8 dígitos finais iguais,
   ex. 99999-9999). Instagram: rejeita handle puramente numérico (ID de location, não perfil).
4. **BrasilAPI blindada contra rate limit** — `_receita` tem semáforo global (6 simultâneas) +
   retry/backoff em 429 + **fallback para minhareceita.org** (dados abertos, mesmo formato).
   Amostra de 30 POIs: 26 recuperados, 19 com CNPJ, **0 ocorrências de 429**. Tentar extrair
   dados empresariais das páginas de CNPJ (econodata/cnpj.biz) NÃO funciona — bloqueiam
   scraping (retornam vazio); a BrasilAPI/minhareceita é a fonte. Limitação conhecida: às vezes
   pega o CNPJ do estabelecimento VIZINHO (mesma página lista vários) — o `ia_resposta` guarda a
   evidência p/ auditoria.

⚠️ **BUG do delete+recreate apagando fotos (corrigido 02/07/2026):** o ingestor recria as
derivadas (fotos/comentários/horários) a partir do `reg`. Se uma etapa de enriquecimento
reingere um POI com `fotos=[]` (ex.: a fase Maps da cascata falhou por timeout de proxy e
voltou vazia), as fotos que o Maps já tinha capturado eram **DELETADAS**. Aconteceu num run
real: fotos caíram de 12.788 → 11.580. Duas correções: (1) `realtime_ingest` agora **preserva
fotos/comentários/horários** do registro antigo quando o novo vem sem eles (igual aos campos
escalares); (2) `enriquecer_tudo` só grava no JSON quem tem a flag `_tocado` (foi genuinamente
enriquecido AGORA) — não reingere registros que só "já tinham telefone de antes". Restauração:
reinserir fotos por place_id a partir dos `uploads/*_db.json` (JSONs de coleta guardam as fotos).

---

## 1. Visão geral — dois fluxos

**Fluxo A — Pipeline principal (descobre POIs a partir de screenshots do Maps):**
```
index.ts → detect_crops.py → ocr_pois.py → search_pois_v2.py → recover_pois_v2.py
```
Fotografa o Google Maps em tiles 4K, detecta ícones de POI (OpenCV), lê o nome (EasyOCR),
busca cada nome no Maps (Playwright) e enriquece.

**Fluxo B — Busca por planilha (enriquece POIs que você JÁ sabe que existem):**
```
search_from_sheet.py  (planilha .xlsx/.csv → Maps + IA → banco → mapa)
```
Este foi o foco principal do trabalho recente (cliente Aegea PI / Parnaíba).

Ambos gravam no **PostgreSQL** e geram um **mapa HTML** (Leaflet/OpenStreetMap).

---

## 2. Ambiente (.env — NUNCA commitar, está no .gitignore)

> **O `.env` é a fonte única das chaves.** Nenhuma chave vive em código —
> `tests/test_portao.py` e o gitleaks do pre-commit reprovam quem tentar.

```
WEBSHARE_API_KEY=<chave da API Webshare (proxies)>

# Banco do PRODUTO — Supabase self-hosted no i9 desde 12/08/2026 (ADR 0003).
# Definir I9_POSTGRES_HOST liga o pipeline ao i9; esvaziar volta ao Postgres local.
I9_POSTGRES_HOST=<ip Tailscale do i9>
I9_POSTGRES_PORT=<porta>
I9_POSTGRES_DB=<banco>             # schema `comercialradar` (ADR 0004)

# Banco de REFERÊNCIA — CNEFE, malha IBGE, CNPJ. Container à parte, sem JOIN.
REF_POSTGRES_HOST=<ip>
REF_POSTGRES_PORT=<porta>
REF_POSTGRES_DB=<banco>

SUPABASE_SERVICE_ROLE_KEY=<chave>  # Storage: bytes de imagem fora do banco

# Postgres local — só o legado/fallback quando I9_POSTGRES_HOST está vazio
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<senha>          # tem '@' → no DATABASE_URL vai como %40
POSTGRES_DB=comercialradar
DATABASE_URL="postgresql://postgres:<senha-url-encoded>@localhost:5432/comercialradar?schema=public"
OPENAI_API_KEY=<chave OpenAI>      # decisor de equivalência de vizinhos
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=<chave Gemini>      # localizador via Google Search grounding (precisa BILLING p/ volume)
GEMINI_MODEL=gemini-2.5-flash

# Mapa e captura (Google)
GOOGLE_TILES_KEY=<chave Map Tiles>  # fundo do mapa; nunca vai ao navegador (server.py faz proxy)
GOOGLE_MAP_ID=<id do estilo>        # estilo do fundo no painel
MAPS_JS_KEY=<chave Maps JavaScript> # captura (src/capture.ts) — SEM ela a captura para na hora
MAPS_API_KEY=<chave Places>         # só p/ o motor pago de mineração; ausente = motor recusado

# Metabuscador próprio — várias instâncias, em ordem de preferência
SEARXNG_URL=http://100.115.117.49:8888,http://localhost:8888
```
✅ **Resolvido em 12/08/2026** (commits `c472f59`, `54c0530`, `116ffbb`). A
`MAPS_JS_KEY` viveu em texto puro no `src/capture.ts` e está no histórico do git
(commit `1c7f081`) — reescrever commit não apaga chave exposta, então ela foi
**revogada e confirmada morta** (`REQUEST_DENIED`/expired). Três chaves novas,
separadas por função, e a chave de servidor deixou de ser a mesma do navegador.
gitleaks roda no pre-commit e no CI. Pendência menor: reconferir se a restrição
de referrer está de fato sendo aplicada — em 12/08 o controle ainda passava de
endereço fora da lista.

**Dependências Python** (no .venv): `playwright, playwright-stealth, opencv-python, numpy, easyocr,
scikit-learn, openpyxl, aiohttp, python-dotenv, psycopg2-binary, openai`. `playwright install chromium`.
**Node:** `prisma@6`, `@prisma/client@6`, `ts-node`, `typescript` (Prisma 7 quebra — usar 6).

**UTF-8 no Windows:** `config.forcar_utf8()` reconfigura stdout/stderr (emojis quebram em cp1252).
Ao rodar Python via Bash tool, prefixar `PYTHONUTF8=1`.

---

## 3. Banco de dados (Postgres no i9; Prisma é legado)

> **Mudou em 12/08/2026.** O banco saiu do `localhost` para o **Supabase
> self-hosted no i9**, os bytes de imagem saíram do banco para o **Storage**, e o
> schema passou a ser `comercialradar` em vez de `public` (um schema por
> ferramenta). Ver ADRs [0002](docs/adr/0002-prisma-legado.md),
> [0003](docs/adr/0003-onde-mora-cada-banco.md) e
> [0004](docs/adr/0004-schema-por-ferramenta.md), e a seção 30 para o que quebra
> quando o i9 reinicia.

- **Banco do produto:** schema `comercialradar` no Supabase do i9, alcançado por
  Tailscale. Hoje 0,51 GB — era 60 GB antes de as imagens irem para o Storage.
- **Banco de referência:** container `cr-referencia`, à parte. CNEFE (111,1 mi de
  endereços), malha do IBGE (3.560 municípios) e CNPJ. **Nunca há JOIN entre os
  dois** — é base pública, rebaixável da fonte, e por isso fica fora do backup.
- **Migrações:** 26 arquivos `*.sql` versionados desde `f3024c0` (antes disso a
  história do schema vivia só no disco — o `.gitignore` as levava junto com os
  dumps). O `prisma/schema.prisma` continua no repositório como **legado**: ele
  não é mais quem manda no schema.
- **Banco EXCLUSIVO deste projeto** — verificado: as únicas tabelas são as nossas (`pois` +
  derivadas 1:N `images_urls`, `comentarios`, `horario_funcionamento`, `streetview_imgs`; cada
  derivada referencia `poi_id`, `onDelete: Cascade`) + `_prisma_migrations`. Os outros projetos
  do usuário têm bancos PRÓPRIOS (ex.: `saletopsellers`, `hubplanner`) — nada é misturado.
  > Correção 03/07/2026: versões antigas desta doc diziam que o banco era "compartilhado com
  > `radar_*`". **Não é** — não existe nenhuma tabela `radar_*` aqui. Anotação legada, removida.
- → **NUNCA rodar `prisma migrate dev`**: ele faz `reset` e **apagaria todos os dados deste
  projeto**. Para mudar schema use a via não-destrutiva:
  1. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` via psycopg2 (não-destrutivo),
  2. refletir a coluna em `prisma/schema.prisma`,
  3. `npx prisma generate` (regenera o client, sem migrar).

**Colunas de `pois`** (as extras foram adicionadas por SQL + refletidas no schema.prisma):
`id, fonte, nome, categoria, endereco, telefone, website, avaliacao, total_avaliacoes, plus_code,
status_horario, lat_origem, lng_origem, maps_lat, maps_lng, maps_url, place_id, status, distancia_m,
similaridade, match_valido, ocr_texto, sessao, criado_em,` **`nome_original, endereco_original,
preco_medio, fonte_dado, ia_resposta`**.
- `fonte`: `planilha` | `pipeline` | `descoberto`
- `fonte_dado`: `maps` | `gemini` (de onde vieram os dados enriquecidos)
- `nome_original`/`endereco_original`: o que a planilha forneceu (antes do processo)
- `ia_resposta`: texto completo da resposta da IA (Gemini) por caso — para auditoria
- `ocr_texto`: leitura do OCR (fluxo pipeline)

**Ingestão:** `src/ingest.ts` (Prisma Client). Idempotente por `place_id` (deleta+recria). **Pula**
registros com `match_valido===false`, sem `nome`, ou **coordenada fora do Brasil** (guard geográfico
lat∈[-34,6], lng∈[-74,-34] — impede dispersão global). Uso: `npx ts-node src/ingest.ts <arquivo.json>`.

---

## 4. Módulos v2 (compartilhados)

| Arquivo | Papel |
|---|---|
| `config.py` | Constantes + loader `.env` + fingerprints (UA/viewport/timezone BR) + UTF-8. Tetos: `MAX_FOTOS=10`, `MAX_REVIEWS=15`, delays 2-5s, `MAX_WORKERS=10`, `DBSCAN_EPS_M=300`, waits (`WAIT_PAINEL_MS=12000` etc.), `MAX_DIST_M=100`, `SIMILARIDADE_FORTE=0.90`. |
| `proxy_pool.py` | Carrega 100 IPs estáticos via API Webshare (`/proxy/list/?mode=direct`), cache 1h em `.proxy_cache.json`, cooldown 2h em CAPTCHA/429. `to_playwright(proxy)` → dict para `new_context(proxy=...)`. **IPs são US-based** (incoerência com locale pt-BR, aceitável). |
| `spatial_clustering.py` | DBSCAN (haversine, eps≈300m) agrupa POIs em lotes coesos de 8-15. |
| `human_browser.py` | `HumanSession` = 1 lote. `launch_persistent_context` com proxy nativo, stealth, **route blocking** (bloqueia css/font/media/telemetria/avatars; permite imagens na coleta rica), medição de banda (`request.sizes`), cadência humanizada, cookies de consentimento (`CONSENT=YES+`), `headless` configurável. |
| `extract_full.py` | Extração completa de um painel: `extrair_fotos` (galeria → background-image lh3, até 10) + `extrair_avaliacoes` (aba Avaliações → `div.jftiEf`, até 15) + `enriquecer_poi`. Ordem: avaliações → volta a "Visão geral" → fotos. |
| `db_export.py` | Normaliza `search/recover_resultado.json` → formato do ingester → grava no Postgres. |
| `ai_decisor.py` | **OpenAI gpt-4o-mini** — decide se algum candidato vizinho equivale ao buscado (`decidir_lote`, JSON mode). |
| `gemini_localizador.py` | **Gemini 2.5 Flash + Google Search grounding** — localiza POI por nome+município+UF. `localizar_bloco` (1 chamada), `localizar_lote` (sequencial), `TAM_LOTE=6`. Retry 429/5xx. |
| `gerar_mapa_html.py` | `gerar(fonte, out)` → HTML Leaflet+OSM+markercluster, modal com todos os dados + origem + OCR + IA. Lê do banco. |

---

## 5. Seletores/técnicas do Google Maps (aprendidos na marra)

- **Google Maps NÃO dá CAPTCHA fácil; a BUSCA WEB do Google (google.com/search) e o "Modo IA" DÃO
  CAPTCHA (`/sorry/index`) na hora**, mesmo com proxy residencial + digitação humana. Por isso NÃO
  automatizar busca web / Modo IA — usar a **API do Gemini** para grounding.
- Digitar na barra de busca: usar **`box.fill(texto)`**, NÃO `click()+type` (o canvas do mapa
  intercepta o clique → digitação cai no vazio → "não encontrado"). Bug crítico corrigido.
- Botão **"Próximo"** (pesquisar nas proximidades): localizar por **nome acessível**
  `page.get_by_role("button", name="Próximo")` (é `[role=button] aria-label`, NÃO tag `<button>`).
- Ler resultado: esperar a **URL mudar** para `/place/` ou `/search/` antes de extrair (evita ler
  painel obsoleto do POI anterior).
- Fotos vêm como **background-image** `url("https://lh3...")`, não `<img src>`.

---

## 6. Fluxo B — `search_from_sheet.py` (o principal recente)

**Entrada:** planilha com colunas detectadas por alias: `nome` (obrigatório), `endereco`
(prioriza `endereco_completo` > `logradouro`), `lat`/`lon`, `uf`. Detecta a **UF alvo** (maioria da
coluna UF) — "uma UF por execução".

**Saída:** `<planilha>_db.json` (formato do ingester) + ingestão + `mapa_pois.html`.

### Cadeia de recuperação (4 camadas) — com `--recuperar`
Para cada linha:
1. **Maps direto:** busca `"{nome}, {endereco}"`. Match por `nome_match` (SequenceMatcher ≥0.72 OU
   containment de tokens ≥0.6 + dist≤400m OU substring). **Portão de distância 20km** e **filtro de UF**
   (rejeita match em outro estado). Só enriquece (fotos/reviews) se casar.
2. **Vizinhos "próximo daqui":** se não casou, posiciona na coordenada → clica "Próximo" → busca o
   nome → coleta candidatos vizinhos (só ≤20km). Se um casa por nome → `recuperado_proximo` (enriquece).
3. **OpenAI decisor** (pós-run, escopado aos reprocessados): manda nome+candidatos vizinhos ao
   gpt-4o-mini → se decide equivalência (confiança ≥0.6) → `recuperado_ia`.
4. **Gemini grounding** (pós-run): para os ainda sem match, `gemini_localizador` busca no Google
   (grounding) em lotes de 6 → `recuperado_gemini` (endereço/coord/tel/categoria/horário/avaliação/preço
   + `ia_resposta`). Coord: usa a do Gemini só se ≤40km da coord da planilha.
- **Descoberta:** candidatos vizinhos novos (place_id fora do banco) → `fonte='descoberto'` (leads).

### Modo `--gemini-direto` (RECOMENDADO para o resíduo)
O resíduo que **já falhou no Maps** NÃO deve repetir Maps. `--gemini-direto` **pula o browser inteiro**
e manda as pendentes **direto pro Gemini** em lotes paralelos (8 threads, batch 6), com progresso ao
vivo e salvamento incremental. ~22 min para ~1650 POIs. Custo em centavos (grounding tem cota diária
grátis generosa com billing).

### Flags (`search_from_sheet.py`)
```
--workers N        paralelismo (máx 10)
--no-proxy         roda direto (teste, não gasta proxy)
--ingest           grava no Postgres ao final
--limit N          limita as PENDENTES a reprocessar (teste)
--retry-failed     reprocessa só as falhas (preserva válidos + descobertos)
--recuperar        liga as 4 camadas (vizinhos + OpenAI + Gemini + descoberta)
--gemini-direto    resíduo → direto pro Gemini (sem browser). Use com --retry-failed.
--headful          1 worker com navegador VISÍVEL (acompanhar/depurar)
--cidade "X"       enviesa quando a linha não tem endereço
--area arq.json    polígono da área válida (frontend salva em areas/area_atual.json);
                   fora do polígono → status 'fora_da_area' (não ingere)
```

### Por que o iFood cai no CAPTCHA mesmo com proxy (26/08/2026)

O Cloudflare do iFood passou a usar **Turnstile interativo** ("Confirme que é
humano"), e navegador automatizado não clica. A pergunta era por que o proxy não
resolve. Medido, isolando uma variável por vez:

| build | proxy | modo | resultado |
|---|---|---|---|
| Chromium do Playwright | não | visível | DESAFIO |
| **Chrome real** (`channel="chrome"`) | não | visível | **PASSOU** |
| Chrome real | Webshare | visível | DESAFIO (0/4 IPs) |
| Chrome real | não | oculto | DESAFIO |

**São três sinais somados, não um.** O Chromium empacotado anuncia
`navigator.webdriver=True` e é barrado sozinho — foi o que mascarou o efeito do
proxy na primeira medição, porque com ele tudo falha. Trocado por Chrome real,
o IP volta a pesar: os quatro IPs da Webshare (datacenter) foram barrados. E
mesmo o Chrome real, oculto, é barrado.

Só a combinação **Chrome real + IP residencial + visível** passa — que é
exatamente a configuração de uma pessoa usando o próprio computador.

**O que NÃO era:** nem o `--disable-http2` (testado com e sem, mesmo resultado)
nem os IPs estarem mortos (os 100 do cache são os 100 da conta, todos válidos,
e respondem 200 em GET direto).

**Consequência prática:** o iFood por navegador exige proxy RESIDENCIAL, que a
conta atual não tem. O endpoint de detalhe (`/v1/merchants/{id}/extra`) continua
aberto e sem navegador — é dele que vem o CNPJ. Toda listagem está 404 ou 403.

### Os proxies não estavam ruins — o HTTP/2 estava (26/08/2026)

A run de Bento Gonçalves perdeu **15 IPs** antes de conseguir buscar. O sintoma:
`ERR_TIMED_OUT` no `page.goto("https://www.google.com/maps")`, o pool marca o IP
como queimado, cooldown de 10 min, próximo IP, mesma coisa.

**A leitura fácil — "os proxies morreram" — é a errada.** O que a medição mostrou:

| teste | resultado |
|---|---|
| os 100 IPs do cache vs. a conta | **os mesmos 100**, todos válidos |
| o mesmo IP, GET direto ao Maps | **200 em 0,9 s** |
| `example.com` e `bing.com` pelo navegador, mesmo proxy | **abrem** |
| `google.com/maps` pelo navegador, mesmo proxy | **trava** |

Nem `wait_until="commit"` escapava: o navegador não recebia o primeiro byte. Não
é página pesada nem sub-recurso travado — é a **negociação do protocolo**.

`--disable-quic` não resolve. **`--disable-http2` resolve:**

```
sem a flag   0/8 IPs abriram o Maps · média 17,8 s até estourar
com a flag   8/8 IPs abriram o Maps · média  2,8 s
```

A flag está em `human_browser.py`, que é o launcher do `search_pois_v2`. A
captura dos tiles (`src/capture-cli.ts`) não usa proxy e nunca sofreu disso.

É a mesma família da cicatriz de 24/07/2026, quando IPs da Webshare
"penduravam no google.com só pelo navegador".

#### E a API da Webshare falhando do notebook

Sintoma separado, mesma run: `⚠️ API Webshare falhou: urlopen error timed out`,
com queda para o cache local. Do **i9** a mesma API responde em **1,1 s**. É mais
um argumento para mover a captura e o iFood para lá — veja abaixo.

### O vazio do pandas virou a palavra "nan" no banco (25/08/2026)

`str(float('nan'))` devolve `'nan'`. A importação estadual testava `if v is None`
— que não pega `NaN`, `NaT` nem `pd.NA` — e gravou **105.148 campos** com texto
onde devia haver nulo, todos numa tarde de importações:

| campo | contaminados |
|---|---|
| `pois.email` | 28.662 |
| `pois.website` | 28.394 |
| `pois.instagram` | 18.837 |
| `pois.telefone` | 17.193 |
| `pois.endereco` | 7.919 |
| `pois.nome` | 1.452 |
| outros (categoria, vinculo_poi.nome, fachada_anotacao) | 2.691 |

**O estrago não ficou no banco.** O painel conta "com telefone" por
`telefone is not null`: em Cachoeirinha ele mostrava 11.918 POIs com telefone
quando 6.559 tinham — 45% inventado; em site, 74%; em email, 73%. Era o
"Com telefone 11.850 (100%)" que aparecia na tela.

E o cruzamento contava `mesmo domínio: nan` como prova: das 1.460 fusões que ele
propunha, **1.334 (91%)** eram esse nada casando com esse nada.

**Duas guardas, de propósito.** `extracao_estadual.val()` usa `pd.isna` (pega
NaN, NaT e `pd.NA` de uma vez, com guarda de tipo porque ele devolve array para
lista) e recusa também o texto já escrito assim na fonte — o parquet do Overture
traz "None" e "null" digitados em campo de contato. Do outro lado,
`evidencia.py` trata os mesmos valores como ausência, porque uma guarda só na
entrada não protege do que já está gravado.

O `cadastur.py` já fazia certo, com o comentário "Pandas devolve NaN, e NaN vira
a string 'nan'". O conhecimento existia; faltava neste caminho.

Os 105.146 campos foram corrigidos. `pois.nome` é `NOT NULL`, então os 1.452 sem
nome viraram string vazia — que é o idioma que o resto do código já usa
(`coalesce(nome,'') <> ''`).

### O cruzamento entre as fontes (etapa 7, 25/08/2026)

`cruzar_fontes.py` substitui o antigo `povoar_vinculo.py --juntar`, que agrupava
por nome idêntico mais coordenada arredondada — e por isso **não usava nada do
que a etapa 6 produzia**. A normalização virava dado que ninguém consumia.

#### As regras, e o porquê de cada uma

| evidência | peso | condição |
|---|---|---|
| logradouro canônico + número iguais | 5 | — |
| mesma rua canônica | 2 | — |
| mesmo domínio | **4** | dentro de 20 m |
| mesmo telefone | 2 | dentro de 20 m, **nunca sozinho** |
| nomes praticamente iguais | 3 | — |
| mesma categoria | 1 | — |

Soma ≥ 8 funde direto (confiança 8–10). Entre 4 e 7 vai para a IA da Spark
(confiança 7 se ela disser MESMO). Mesma rua a menos de 20 m sem número pergunta
mesmo sem somar 4 — endereço é o maior indício.

O peso do site é **4 e não 3** de propósito: `MIN_PARA_IA` também é 4, então o
site sozinho já manda perguntar e o telefone sozinho não. É a ordem declarada
("site, por ser um domínio, tem mais peso que telefone") escrita em
comportamento, não em comentário.

#### Dois achados de medição que mudaram o resultado

**O `nan` do pandas.** 28.394 POIs têm `website = 'nan'` e 17.193 têm
`telefone = 'nan'` — string, não nulo. Tratados como valor real, os 28 mil
compartilhavam o mesmo domínio: o cruzamento de Cachoeirinha propunha **1.460
fusões**, e com a guarda propõe **126**. As outras 1.334 (91%) eram evidência
fabricada.

**Vizinhança não é suspeita.** A regra "mesma rua a menos de 20 m" enche o balde
da IA com a loja do lado: 19.879 pares, **18.220 sem um token de nome, domínio
ou telefone em comum** (Mercado Pop Latino + Óptica Caelum, 19 m). O corte de
`filtrar_para_ia` exige *algo* ligando os dois além do lugar — 415 chamadas em
vez de 4.970 — e o que sobra é "Farmácia São João" × "Farmácia São João" a 30 m.
`--tudo-para-ia` desliga o corte; é julgamento sobre custo, e quem paga decide.

#### O que a IA recebe

Tudo que o banco tem dos dois lados: fonte, categoria, endereço, logradouro
normalizado, telefone, site, CNPJ, razão social, nome fantasia, CNAE, distância
e a evidência já apurada, escrita. Campo vazio não é impresso — e "vazio" inclui
a palavra `nan`, senão a IA lê "os dois têm o mesmo telefone: nan".

`INCERTO` é resposta legítima; veredito fora do vocabulário vira `INCERTO` em vez
de virar fusão por engano de parsing. Lote que falha é **marcado**, não
descartado.

#### O POI absorvido não é apagado

Vira `status='fundido'`, mantém linha e `place_id`, e o vínculo passa para o
sobrevivente como mais uma aba — reversível pelo `x` da ficha. A transitividade é
resolvida (se A absorve B e B absorveria C, C vai para A), senão o vínculo de C
apontaria para um POI já fundido e a ficha ficaria órfã.

Sobrevive quem tem mais evidência acumulada (Street View, análise de IA): ela
aponta para um `poi_id`, e escolher o outro obrigaria a mover trabalho pago.

#### Um defeito que isto desenterrou

`logradouro_ajustado` tinha 77.737 linhas com `fonte = '?'`: a ingestão procurava
a coluna `source_id` e a skill escreve `aj_source_id` (ela prefixa com `aj_` tudo
que acrescenta). Sem a fonte não dava para ligar a marcação de volta ao POI nem
separá-la da do CNEFE. Corrigido; as linhas órfãs foram removidas depois de
confirmar que todas tinham equivalente exato sob a fonte certa.

### Clique no polígono: o que tem aqui dentro (25/08/2026)

Clicar na área desenhada abre um popup com o **total de POIs do banco dentro
dela**, a quebra por fonte, a área em hectares, o número de vértices, quantos
são multiorigem — e um botão para apagar a área.

Ele existe por causa de uma confusão real: o cabeçalho dizia "42 POIs" e o
cartão da mineração dizia "165", números da mesma área e nenhum dos dois errado
(veja a seção seguinte). Faltava um lugar onde a pergunta tivesse resposta.

A quebra é por **`fonte`**, não pelos chips de origem. Os chips agrupam por como
o POI foi *confirmado*, e a base estadual inteira cai no balde "Outros" — foi
exatamente isso que escondeu que os 42 vinham do Overture/OSM/Foursquare.

Apagar a área **não apaga POI nenhum**, e a confirmação diz isso: a área é foco
de tela, não filtro de banco.

**A área desenhada mora na `paneArea` (z-index 415), acima da malha (410).**
Ela ficava no `overlayPane` padrão, 400, e o contorno do município era desenhado
*por cima* do polígono recém-traçado: clicar no próprio desenho selecionava o
município e trocava o recorte do mapa inteiro. Quadras (620), marcadores (630) e
vias (645) continuam ganhando dela — clicar num POI dentro da área abre o POI.

**Armadilha do Leaflet, para quem for mexer:** o botão de apagar é pego por
delegação no documento. Ligar `b.onclick` logo depois de `openPopup()` falha em
silêncio — como o `bindPopup` recebe `options`, o Leaflet constrói uma `Popup`
nova a cada clique, e da segunda abertura em diante o container ainda não existe
quando `openPopup` retorna. O popup abre com os números certos e o botão não faz
nada. `tests/test_popup_da_area.py` tranca isso.

### O processo caro roda dentro do desenho — nas três etapas (25/08/2026)

A mesma doença estava em três lugares, sempre com a mesma forma: **montar o
trabalho sobre o município inteiro e só depois cruzar com o polígono**.

| etapa | antes | com o recorte |
|---|---|---|
| busca no Maps | 88 ícones do tile, 80 fora do desenho, buscados assim mesmo | 8 |
| IA de endereço | 7.223 endereços distintos do município, 360 lotes na Spark | 7 |
| normalização (CNEFE) | 69.150 linhas | 262 |
| pontos do iFood | 15 pontos de células de 2,5 km, **zero** dentro do desenho | 1 |
| cruzamento entre fontes | 11.897 POIs, 831.211 pares, 742 chamadas de IA | 22 POIs, 52 pares, 0 chamadas |

O cruzamento é a exceção que confirma a regra: ele folga a caixa em **150 m** e
mantém o par em que **um** dos lados toca a área. A fusão precisa enxergar o
vizinho de fora da linha — o duplicado do POI da borda pode estar do outro lado
dela, e cortar exato o tornaria invisível, fazendo o ponto entrar na entrega
duas vezes por causa do recorte. 150 m é a própria rede de candidatos (célula
de ~110 m mais as vizinhas), então a margem não inventa alcance.

O corte compartilhado é `area_utils.recorte_sql()`: a **caixa** vai ao banco (quatro
floats numa coluna indexada) e o **polígono exato** é julgado em Python sobre o
que sobra. Mandar `ponto_no_poligono` ao SQL viraria varredura sequencial.

**Não existe "sem área" para desligar isto, e não precisa existir.** Escolher o
município no painel *grava a divisa dele* como área de trabalho
(`/api/area/municipio` escreve no mesmo `area_atual` do desenho manual). O
polígono já diz a verdade nos dois casos — quando é o município, a caixa cobre o
município. Ramificar por "é área ou é município?" seria inventar uma distinção
que o dado não faz.

**A normalização corta pela CAIXA, não pelo polígono exato**, e isso é escolha: a
skill decide a grafia de uma *rua*, e rua não termina na linha que o operador
desenhou. Cortar exato partiria a Av. General Flores da Cunha ao meio e jogaria
fora metade da prova de que ela é a mesma da "Av. Gen. Flores da Cunha".

#### Por que o iFood dava "nenhum endereço do CNEFE em área desenhada"

A grade se formava sobre o município, escolhia um endereço por célula de 2,5 km —
o mais próximo do **centro da célula** — e só então cruzava com o polígono. Numa
área de 1,5 ha nenhum centro de célula cai dentro. Havia **262 endereços do CNEFE
disponíveis** ali, e os 15 escolhidos eram todos de outros bairros. A etapa 5
falhava inteira, e a mensagem sugeria falta de dado quando o dado estava lá.

Agora, com polígono, a ordem se inverte: recorta primeiro (`CNEFE_NA_CAIXA`),
julga o polígono em Python, e só então forma as células (`_agrupar_em_celulas`).
Sem polígono o caminho antigo continua, no banco — Porto Alegre não cabe em
memória.

#### A IA e a skill fazem coisas diferentes

Confusão que já custou explicação: `segmentar_endereco.py` **separa** o campo
grudado (`"Avenida Cruzeiro, nº 420, 94930-615"` → logradouro | número | CEP) e é
ele que chama a IA da Spark, uma vez por texto de endereço distinto, cacheado
para sempre em `endereco_segmentado`. `ajuste_logradouro.py` **decide a grafia
canônica** e **não chama IA nenhuma** — é determinístico e só aprende de par
provado.

### A busca só roda dentro da área desenhada (25/08/2026)

O tile é fotografado inteiro — o retângulo tem tamanho fixo e não encolhe com o
polígono. Isso é aceito: ler o tile é uma passada de visão computacional, barata,
e paga uma vez.

A **busca no Maps** não. Cada nome lido vira uma sessão de navegador com proxy,
e é a etapa mais cara do processo. Desde 25/08 ela recebe só os recortes cujo
ícone caiu dentro do polígono.

**Medido em Cachoeirinha:** 165 ícones detectados num tile, **8** dentro do
polígono de 3,5 ha. As outras 157 buscas eram 95% do custo, gastas fora do que o
operador pediu.

O polígono vem de `capturas/<sessao>/_area.json` — a área **como ela era quando a
captura rodou** —, não do banco. O operador minera um bairro e já desenha o
próximo; ler a área *atual* filtraria os recortes de uma área pelos limites de
outra, em silêncio. Sessão antiga sem esse arquivo busca tudo, e o processo diz
isso no log em vez de devolver zero.

#### Não confundir com o cartão "Fora da área (gravados)"

São dois "fora" diferentes, e o painel mostra os dois:

| | o que é | gravado? |
|---|---|---|
| recortes não buscados | o **ícone** caiu fora do polígono | não — nunca chegou a existir |
| `inserido_fora` | o ícone estava dentro, foi buscado, e o **endereço verdadeiro** que o Maps devolveu fica fora | **sim** |

A regra de 04/08 — "o polígono é foco, não filtro de gravação" — continua valendo
para o segundo caso, que é o que o cartão conta. O que mudou é que não se **paga**
mais para procurar fora da área. Quem quer o entorno desenha o entorno.

#### Os POIs que você vê no mapa não são os recortes

Na mesma área de 3,5 ha o banco tinha **51 POIs** (42 `estadual` + 9 `pipeline`) e
a captura detectou 8 ícones. Não é contradição: os 51 vieram das **bases públicas**
(Overture/OSM/Foursquare), que enxergam muito mais do que o Google desenha como
marcador no zoom 19. Os dois caminhos são independentes, e o filtro do polígono
não toca no primeiro.

### Status possíveis (campo `status`)
`ok` · `encontrado_divergente` · `nao_encontrado` · `fora_da_uf` · `fora_da_area` ·
`recuperado_proximo` · `recuperado_ia` · `recuperado_gemini` · `descoberto` · `minerado` · `erro`.

### Comandos típicos
```powershell
# Coleta completa da planilha (Maps + recuperação + Gemini + ingest + mapa)
.venv\Scripts\python search_from_sheet.py "caminho\planilha.xlsx" --workers 10 --recuperar --ingest

# Só o resíduo que falhou no Maps, direto pro Gemini (rápido/barato):
.venv\Scripts\python search_from_sheet.py "caminho\planilha.xlsx" --retry-failed --gemini-direto --ingest

# Observar 1 caso no navegador visível:
.venv\Scripts\python search_from_sheet.py "caminho\planilha.xlsx" --headful --no-proxy --recuperar --retry-failed --limit 5

# Ingerir manualmente / regerar mapa (fallback):
npx ts-node src/ingest.ts "caminho\planilha_db.json"
.venv\Scripts\python gerar_mapa_html.py
```

---

## 7. Fluxo A — Pipeline principal v2 (search_pois_v2 / recover_pois_v2)

- `search_pois_v2.py capturas/<sessao>/session.json --workers 10 [--full] [--ingest] [--no-proxy]`
  - Lê `ocr_resultado.json`, clusteriza (DBSCAN), 10 workers persistentes (1 lote = 1 browser + 1 IP +
    1 fingerprint), busca N2 (coordenada → "Próximo" → nome). `--full` = extração completa (fotos+reviews).
  - Schema de saída idêntico ao v1 (`search_resultado.json`). Banda-alvo <4MB/POI (sem `--full`).
- `recover_pois_v2.py` — reprocessa as falhas com matching relaxado (scraping puro, sem Places API).
- v1 (`search_pois.py`/`recover_pois.py`) preservados como backup (recover v1 usa Places API paga).
- `enrich_pois.py` — etapa opcional que enriquece via **Google Places Details API (paga)**.

---

## 8. Mapa HTML (`mapa_pois.html`)

Gerado por `gerar_mapa_html.py` a partir do banco. Leaflet + OpenStreetMap + markercluster.
Marcador por POI (azul=planilha, laranja=pipeline). Clique → **modal** com: dados Google (categoria,
avaliação, endereço, tel, site, horários, fotos, avaliações), **dados de origem** (nome/endereço da
planilha + OCR), metadados (coords, distância, place_id). Busca por nome no topo. É pesado (~10MB) mas
abre com duplo-clique. **Regenerado ao fim de todo run.**

---

## 9. Armadilhas / lições (IMPORTANTE para não repetir)

1. **Dispersão global de POIs:** nomes iguais a lugares famosos ("Tóquio", "K2") faziam o Maps/Gemini
   retornar o lugar distante. Corrigido com **portões de distância** (match 20km, candidatos 20km, Gemini
   40km) + **guard no ingester** (coord fora do Brasil = pula) + a run é **UF-scoped** (tudo fora do PI é
   lixo → limpar por bounding box do estado se reaparecer). O JSON acumulava lixo de runs antigos e a
   ingestão re-injetava → limpar o **JSON** também, não só o banco.
2. **Teardown da sessão:** runs longos em background são mortos quando o ambiente reinicia. Rodar no
   **terminal do VSCode do usuário** (sobrevive) ou confiar na **retomada** (salvamento incremental +
   pula processados; `--retry-failed` é idempotente).
3. **Gemini free tier** esgota a cota de grounding rápido (429). Precisa **billing** para volume
   (1500 grounded/dia grátis no pago). 503 = sobrecarga transitória (retry absorve). Gemini **não traz
   fotos nem textos de avaliações** (só média+contagem) — limitação do grounding.
   - **Modelo importa MUITO (corrigido 02/07/2026):** `gemini-2.5-flash` devolve endereço/telefone/
     horário/avaliação **null** na maioria dos casos; `gemini-2.5-pro` traz endereço completo (rua,
     nº, bairro, CEP), horário por dia e nota — tão bom quanto a busca manual no "Modo IA". Trocado
     `GEMINI_MODEL` para **pro** no .env + default no `gemini_localizador.py`. Também: **batch grande
     dilui** a busca (o modelo pesquisa raso cada item) → `TAM_LOTE` reduzido de 10 → **4**; e o
     prompt foi reescrito para busca DEDICADA por item + endereço completo. Resultado: de ~0 para
     ~14/20 campos num lote de teste. ⚠️ Os `recuperado_gemini` JÁ gravados seguem pobres até
     **re-rodar** (`--retry-failed --gemini-direto`); custo ~poucos dólares (pro + grounding grátis
     até 1500/dia). Os `descoberto` NÃO passam pelo Gemini — enriquecer é etapa à parte.
4. **Proxies:** a lib local `proxy` do `proxy_manager.py` (v1) não está instalada → v1 rodava sem proxy.
   O v2 usa **proxy nativo do Playwright** (`proxy_pool.py`).
5. **PowerShell:** `2>&1` em exe nativo gera ruído `NativeCommandError`; encoding UTF-16 por padrão.
   Chaining com `;` (não `&&`). Heredoc só com `@'...'@` na coluna 0.
6. **Candidatos legados sem portão (corrigido em 02/07/2026):** o JSON acumulava
   `candidatos_proximos` coletados ANTES do portão de 20km existir (dist_m de até 2.400km). A
   recuperação pós-run confiava neles: a IA (OpenAI) casava por nome sem revalidar distância
   (227 `recuperado_ia` errados, ex. barbearia de Parnaíba casada com homônima em SP) e a
   descoberta promovia qualquer candidato a lead (551 `descoberto` espalhados pelo Brasil).
   Correção em `_recuperar_ia_e_descoberta`: filtro `_cands_confiaveis` (≤20km) antes da IA e
   da descoberta + revalidação do candidato escolhido; e o portão de 40km do Gemini passou a
   valer também no caminho `--recuperar` (antes só existia no `--gemini-direto`). O JSON da
   Aegea foi saneado (backup `*.backup_pre_saneamento.json`) e 737 POIs removidos do banco.

---

## 10. Estado atual do dataset (medido em 24/08/2026)

Números tirados do banco no i9, não de memória. `verificar_servicos.py` passou 7/7
e a suíte deu **229 passando, 35 puladas** na mesma sessão.

**31.255 POIs.** A base deixou de ser de uma cidade só: Parnaíba foi a origem, Canoas
é hoje o grosso do trabalho.

| Por fonte | | Por município | |
|---|---:|---|---:|
| `planilha` | 16.287 | Canoas | 22.002 |
| `pipeline` (captura+OCR) | 9.691 | Parnaíba | 4.239 |
| `cadastur` | 4.238 | Porto Alegre | 906 |
| `descoberto` | 560 | Gramado | 381 |
| `estadual` | 300 | Esteio | 325 |
| `ia_fachada` | 177 | Cachoeirinha | 279 |
| `captura` | 2 | (demais) | ~3.100 |

**Precisão da coordenada** — a classificação da seção 28, aplicada à base inteira:

| classe | POIs | raio declarado |
|---|---:|---:|
| `porta` | 15.625 | 15 m |
| `via` | 5.952 | 150 m |
| `porta_aprox` | 1.967 | 40 m |
| `desconhecida` | 7.711 | — |

`desconhecida` **não** quer dizer ruim: é coordenada de planilha do cliente que
ninguém conferiu ainda. Confundir as duas coisas faria descartar 7,7 mil pontos que
podem ser ótimos.

**Trabalho acumulado nas derivadas:** 40.560 fotos do Maps · 37.738 imagens de Street
View · 31.777 comentários · 4.068 análises de IA.

**O banco do produto tem 0,51 GB** — 244 MB só de `cadastro_cliente`. Ele era de 60 GB
até 12/08/2026: os bytes de imagem saíram para o Storage (ADR 0003) e o que ficou é
metadado. É o que torna o dump diário de 31 MB possível.

---

## 11. Arquivos-chave (mapa mental)

**O núcleo — servidor, mapa e ingestão**
```
server.py            FastAPI + WebSocket + orquestração de jobs + APIs do painel
frontend/            index.html · app.js · style.css (mapa, abas, ficha, dashboard)
realtime_ingest.py   ESCRITOR ÚNICO de POI: dedup, merge não-destrutivo, gate de área
area_utils.py        gate de polígono      config.py  constantes + .env + UTF-8
io_atomico.py        gravação de JSON que não derruba a rodada (seção 21)
auth.py              usuários, níveis e o tenant da requisição
```

**Como um POI nasce — as fontes**
```
search_from_sheet.py planilha → Maps (+ 4 camadas de recuperação)
minerar_captura.py   captura → recortes → OCR → busca   (o motor do painel, seção 20)
detect_crops.py      ocr_pois.py      src/capture.ts · src/capture-cli.ts
minerar_area.py      polígono → Places Nearby (pago)
cadastur.py          Cadastur/MTur: baixar → carregar → cruzar → gerar POI (seção 27)
extracao_estadual.py POI em escala estadual (Overture + OSM + Foursquare)
coletivas_radar.py   unidades coletivas do CNEFE   coletivas_importar.py
extrair_ifood.py     família iFood: carregar_ifood_zip · cruzar_ifood_receita ·
                     enriquecer_ifood · marcar_ifood_nos_pois · validar_cnpj_ifood
```

**Como ele engorda — enriquecimento**
```
enriquecer_tudo.py   a cascata Maps → web → Street View (aceita --poi-ids)
enriquecer_maps.py   reabre o maps_url    minerar_web.py  SearXNG + estruturação
streetview_capture.py fachada + giro 360°  baixar_imagens.py  bytes → Storage
cnpj_local.py        CNPJ pela Receita já no banco (seção 22)
tratamento_cnpj.py   Receita × CNEFE → aptidão, perfil e rota
cadastro_cliente.py  carteira de imóveis do cliente (seção 23)
cruzar_bases.py      o cruzamento de qualquer base contra os POIs
```

**Onde ele fica no lugar certo — coordenada (seções 28 e 29)**
```
geocodificar.py           Photon → Nominatim → painel do Maps; precisão declarada
conferir_coordenadas.py   varredura da base; só move quem cai FORA do município
identificar_divergente.py acha o trabalho pago capturado no lugar errado
```

**Como ele é julgado — IA**
```
descrever_imagens.py  veredito visual, estágio 04 (seção 12)
avaliar_fachada.py    leitura de fachada com o validador da skill como porteiro
leitura_fachada.py    prompts_fachada.py  prompts_estrutura.py  julgar_identidade.py
anotar.py             fachada_anotacao / fachada_triagem
```

**O chat com ferramentas — o resgate do resíduo (seção 26)**
```
agente_local.py    chat_api.py    ferramenta_maps.py    ferramenta_instagram.py
ferramenta_ponto.py (guardar_ponto)  ferramenta_lote.py  ferramenta_memoria.py
ferramenta_skills.py  buscar_empresa.py  candidatos_receita.py  dossie.py
```

**Bases externas e infraestrutura**
```
base_comum.py  base_cnpj.py  base_cnefe.py      scripts/i9/  (banco, Storage, backup)
verificar_servicos.py  7 verificações de FUNÇÃO, não de porta (seção 30)
relay_proxy.py  worker_rede.py  cliente_rede.py  proxy_pool.py  human_browser.py
```

**Provas e documentos**
```
tests/                  229 testes (pytest)
prova_ponta_a_ponta.py  49 asserções sobre o sistema de pé
prova_carga_chat.py     capacidade do chat em paralelo
docs/PRD.md · ARQUITETURA.md · MODULOS.md · RBAC.md · INFRA.md · CHECKLIST.md
docs/adr/               5 ADRs        docs/estado.json → docs/painel.html
docs/PROCESSO-OPORTUNIDADE.md · docs/RESGATE-POI.md · docs/processo.html
skills/                 5 skills empacotadas
```

> Saiu daqui: o processo de quadras, a régua de numeração e o identificador de
> telhados foram para o **radarTelhados** em 11/08/2026. As seções 14-19 abaixo
> ficam como história — o código não mora mais neste repositório.

## 13. Bases externas (módulos separados de extração) — `base_*.py`

Módulos independentes que baixam **bases públicas oficiais completas** e gravam em
**tabelas próprias** no mesmo banco `comercialradar` (nunca tocam em `pois`). Servirão
FUTURAMENTE para enriquecer cada POI aprovado. Puramente ETL: baixa → **COPY direto**
(sem parsear linha a linha — é o que aguenta dezenas de GB) → grava. Idempotente por
arquivo (tabela `fonte_arquivos`; re-run pula o que já carregou).

Motor comum: `base_comum.py` (download com retomada/Range, `copy_csv` via `copy_expert`,
controle de carga). Ver [[ferramentas-separadas]] e [[bases-externas-fontes]].

### 13.1 CNPJ — `base_cnpj.py` (Receita Federal)
- Fonte: `arquivos.receitafederal.gov.br` — migrou p/ **Nextcloud/SERPRO+**. Acesso por
  **WebDAV do share público** (token `YggdBLfdninEJX9`): `PROPFIND` lista meses e .zip,
  `GET` autenticado baixa. Atualização mensal. CSV `;`, latin-1, sem header.
- Tabelas: `rf_empresas`, `rf_estabelecimentos` (endereço+CNAE+situação — a chave do
  cruzamento), `rf_socios`, `rf_simples`, e apoio `rf_cnaes/municipios/naturezas/
  qualificacoes/paises/motivos`. Chave de junção: `cnpj_basico`.
- Brasil inteiro: ~5 GB zipado, **~85 GB** descompactado. Rodar: `base_cnpj.py --recriar`
  (1ª carga limpa), depois `base_cnpj.py` retoma; `--indices` cria os índices no fim.

### 13.2 Residências/endereços — `base_cnefe.py` (IBGE, CNEFE Censo 2022)
- Fonte: FTP IBGE, um zip por UF (`22_PI.zip`…) em `Arquivos_CNEFE/CSV/UF/`. HTTP direto.
- **106,8 mi de endereços** com lat/lng, CEP, logradouro e ESPÉCIE (1=domicílio particular,
  comércio, etc.). Tabela `ibge_cnefe` com colunas derivadas do cabeçalho do CSV.
- Rodar: `base_cnefe.py` (Brasil) ou `--uf PI` (uma UF); `--recriar` limpa antes.

### 13.3 Energia (ANEEL/BDGD) — REMOVIDO
Avaliado e **descartado** (2026-07-20). A BDGD foi obtida (o CKAN da ANEEL está quebrado —
302-loop e datastore 404 —, mas o repositório ArcGIS oficial serve o Geodatabase por
distribuidora) e a Equatorial PI chegou a ser carregada: 1,47 mi de UCs de baixa tensão com
coordenada, consumo mensal e classe. **Os dados não atenderam** — tabelas `aneel_*`,
`base_aneel.py` e `aneel_gdb_loader.py` foram apagados. Não reintroduzir sem pedido.

> Escopo escolhido: **Brasil inteiro, base de CNPJ completa** (estabelecimentos + empresas +
> sócios + Simples). Os testes de fumaça deixaram no banco só uma amostra (CNEFE-RR + 3
> tabelas de apoio do CNPJ); a carga cheia é feita com `--recriar`.

## 12. Análise visual por IA (`descrever_imagens.py`) — estágio 04

**Mapa visual do fluxo completo: [`docs/processo.html`](docs/processo.html)** (abra no navegador).

Último estágio da esteira: um modelo multimodal local (Ollama **qwen2.5vl:7b**, 100% GPU no
servidor remoto) olha as imagens de cada POI e dá um **veredito** — o dado bate com o que a
imagem mostra? A análise vai para a tabela `analise_ia` (1:1 com o POI).

### Arquitetura: percepção cega + julgamento isolado
1. **Percepção 100% cega** — o modelo recebe SÓ as imagens (fachada + fotos do Maps), sem
   nenhum dado do cadastro (senão "papagaia" nome/categoria). Devolve JSON: `cenario`,
   `ramo_visto`, `nome_visto` (lido às cegas), `estabelecimentos[]` (todos os letreiros do
   prédio, térreo + andares), `porte`, `funcionarios_estimados`, `atividade_real`.
2. **Decisão em código** (`_decidir`) — casa a percepção com o cadastro:
   - **nome** por fuzzy (`_nome_bate`) contra TODOS os letreiros lidos → resgata co-localizado.
   - **ramo** por família (`_mesmo_ramo`): atalho textual + pergunta ISOLADA ao mesmo modelo.
   - área aberta → `sem_estabelecimento`; vago → `ponto_vago`; residência → `residencia_sem_comercio`.
3. **2ª olhada** (`_analisar_poi`) — se ia reprovar, reanalisa **foto a foto** só as fotos do
   próprio ponto (interior/produto revela o negócio). Uma por vez (evita foto-ruído).
4. **Protocolo 360°** (modo `--incremental`) — regra de ouro: só reprova depois de girar o
   panorama (base + 90/180/270) e capturar 2 panoramas deslocados (±15 m).
5. **Recomendar visita** (`_recomendar_visita`) — aprovado + sinal de vida recente
   (comentário/foto/street view ≤ 12 meses) → `recomendar_visita = true`.

### Regras de calibração (validadas em 46 casos com o cliente, 0 falso-positivo)
- **Multi-loja**: casa nome contra qualquer letreiro (clínica sobre farmácia, loja no andar).
- **Família de ramo**: armazém≈supermercado≈atacarejo; empadaria≈padaria≈restaurante; óculos⇒ótica.
- **Essência da categoria**: tira prefixo genérico ("Comércio varejista de plantas" → "plantas").
- **Stopword de nome**: tipo de negócio (escola/igreja/posto) não casa nome sozinho.
- **CP10**: foto de interior vale mais que a fachada quando divergem.
- **Dicas de dedução**: prateleiras=mercado, altar/crucifixo=igreja, piscina+crianças=natação.
- **Porte físico**: funcionários pela dimensão (galpão 15-50, não "2 a 4").
- **Contexto**: fotos do Maps ~1900 tokens; só 2 cabem em `num_ctx=4096` → 2ª olhada é foto a foto.
- **num_ctx fixo 4096**: pedir ctx diferente força reload e trava o Ollama (bug de 06/07/2026).

### Tabela `analise_ia` (colunas principais)
`veredito` (aprovado|reprovado), `motivo` (ok|atividade_divergente|imagem_insuficiente|
residencia_sem_comercio|sem_estabelecimento|ponto_vago), `equivalencia`, `atividade_real`,
`porte`, `pessoas_estimadas` (nº funcionários), `tipo_construcao` (loja_terrea|casa|predio|
area_aberta|galpao), `outro_estabelecimento` (lead), `recomendar_visita`, `recomendacao_motivo`,
`resposta_json` (rastro bruto da percepção, com `_percepcao` e marcador `_360`), `n_imagens`.
As imagens do giro 360° ficam em `streetview_imgs.angulo` (facade|g90|g180|g270|p1|p2).

### Comandos
```bash
# Passe 1 — direto (rápido, ~5h), rescata pela 2ª olhada nas fotos do Maps:
.venv\Scripts\python descrever_imagens.py --refazer --workers 3
# Passe 2 — giro 360° só nos reprovados que ainda não passaram:
.venv\Scripts\python descrever_imagens.py --so-reprovados --incremental --extras 2
# Reprocessar POIs específicos:
.venv\Scripts\python descrever_imagens.py --ids 31910,155747 --refazer
```

### Frontend (estágio 05) — o que a análise adiciona
- **Seleção por MUNICÍPIO (por padrão nada vem selecionado)**: o mapa abre enquadrado na
  malha (divisas IBGE) e **nenhum POI aparece**. O usuário **clica no polígono** de um
  município → aquele município é realçado, o mapa dá zoom, os POIs surgem e o painel +
  filtros preenchem só com os dados dele. Clicar de novo (ou no ✕ do badge) desmarca.
- **Filtros no rodapé do mapa**: ✅ Aprovados / ❌ Reprovados (seleção única) + ⭐ Recomendar
  visita + 🔍 Revisar manual. Combinam com origem e atributo; contagens por município.
- **Marcadores**: anel de veredito + estrela dourada nos recomendados.
- **Modal**: `#id` do banco ao lado do nome (clique copia — facilita cruzar com o banco),
  selo de veredito + motivo, ramo/construção/porte/funcionários, galeria 360° (servida do
  banco em `/api/sv/{poi_id}/{angulo}`) e o lead quando reprovado.
- **APIs**: `/api/pois` traz veredito/recomendação/**cidade**; `/api/pois/{id}` traz o bloco
  `ia`; `/api/stats?cidade=` filtra por município; `/api/sv/{id}/{angulo}` serve as imagens.
- **Boot do server**: `on_event`→`lifespan` (sem DeprecationWarning) + banner claro com a URL
  e `log_level=info` — o terminal mostra `✅ ComercialRadar no ar → http://127.0.0.1:8765`.

### recomendar_visita — de onde vem
Calculado no `descrever_imagens.py` (`_recomendar_visita`): fica **true** só se **aprovado
E** houver **sinal de vida recente** — comentário com data sem "ano", foto com EXIF, ou
street view dentro de **12 meses** (`RECENCIA_MESES`). Aprovado sem sinal recente → `false`
com motivo "aprovado, mas sem sinal de vida nos últimos 12 meses".

### Resultado da 1ª execução completa (Parnaíba-PI)
4.313 POIs · **1.843 aprovados** · 2.234 reprovados · **893 recomendar visita** · 973 leads ·
31 para revisão manual. A calibração levou os aprovados de 1.323 → 1.843 (+39%) sem falso-positivo.

## 14. (removido) Identificador de telhados por área

O `telhados_cv.py` e os seus antecessores (`telhados_area.py`,
`segmentar_telhados.py`) foram **apagados em 28/07/2026**, junto com todo o
processo de detecção de telhados — ver a seção 16. A numeração das seções foi
mantida para não invalidar as referências do restante do documento.

## 15. Quadras do Brasil pela topologia das vias — `quadras_br.py` (fase concluída em 22/07/2026)

Base de **quarteirões** do Brasil, construída a partir das **vias do OSM que se cruzam**.
A quadra NÃO vem de polígono pronto: o IBGE não publica malha de quadras (acervo conferido)
e o Overture traz edificação, não quarteirão. A quadra é a **face fechada pela rede viária**,
e nasce já com o que a análise seguinte precisa: as vias que a delimitam, o número de
cruzamentos na borda, e se o traçado é **retangular** ou **torto**.

### Por que retangular vs torto importa
É o campo que dita o custo do trabalho seguinte:
- **retangular** (Q12, Q25) → a quadra sai pronta das vias, recuada de meia caixa viária —
  é exatamente o que o passo 3 da seção 16 faz hoje, para toda quadra.
- **torta** (Q31 — a "quadra da árvore") → o traçado do OSM tem bico/reentrância que não é
  quadra (árvore do outro lado da rua, prolongamento espúrio). Aí entra o **refino pela imagem**.
Em Itambé-PE: das 609 faces, ~28% saem retangulares.

### Fluxo SOB DEMANDA (nunca em lote — decisão do cliente)
`garantir_municipio(cod)` é a porta de entrada: já está na base → devolve; não está → monta só
ele. Processar o Brasil inteiro levaria dias e é desnecessário. Cada município entra quando é
pedido e fica em cache. Ver memória `[[nao-superdimensionar]]`.
1. baixa o `.osm.pbf` da REGIÃO no Geofabrik (cache `dados/pbf/`, ~450 MB, uma vez);
2. **carrega** as vias da região em `osm_via` (Postgres+PostGIS, índice GIST), uma vez. Ler o
   PBF por município custava 98 s cada; carregado, ~3 s. Feito isso o `.pbf` não serve para
   mais nada e pode ser apagado — `baixar_pbf` o rebaixa se uma região nova for pedida;
3. recorta as vias do município pela caixa da malha municipal do IBGE;
4. `unary_union(linhas)` **noda todo cruzamento**, `polygonize` dá as faces;
5. classifica cada face e grava.

### Detalhes que custaram (para não repetir)
- **DuckDB lê PBF** via `st_read(..., layer='lines', open_options=['INTERLEAVED_READING=YES'])`.
  Sem o INTERLEAVED o GDAL estoura ao acumular feições. As colunas `name`/`highway` já vêm
  expostas — não usar `tags`.
- **STRtree** para achar as vias que tocam cada face: sem índice era 24× mais lento e virava
  95% do tempo do município.
- **API do IBGE devolve gzip** mesmo sem pedir → `_json_ibge()` decomprime.
- **Código IBGE tem homônimos**: Itambé-PE é `2607653` (2607505 é Itaíba). Conferir por coordenada.
- `VIAS_OK`: só tipos que fecham quarteirão. Fora trilha, calçada, ciclovia, escada — cortam a
  quadra ao meio sem serem rua.

### Onde isso mora: Postgres + PostGIS (migrado em 03/08/2026)

Estava num DuckDB de 3 GB **dentro da pasta do sistema**. Dado é do banco, não do diretório de
código — e a migração ainda saiu mais rápida:

| tabela | conteúdo | tamanho |
|---|---|---:|
| `osm_via` | 4.566.049 vias, `geometry(4326)` + GIST | 1.781 MB |
| `osm_quadra` | 139.221 quadras | 103 MB |
| `ibge_malha` | 2.746 municípios de 17 UFs | 7,5 MB |
| `osm_municipio_feito` | controle de progresso | — |

O recorte por caixa, que eram quatro colunas `xmin/xmax/ymin/ymax`, virou `&&` sobre o índice
GIST: a mesma caixa de Itambé devolve as **mesmas 3.390 vias** — conferido via a via, geometria,
nome e tipo — em **0,06 s contra 2,91 s**. E o `_PG` em `quadras_br.py` é um adaptador fino que
traduz `?`→`%s` e cursor, para não reescrever dezenas de chamadas escritas contra a API do DuckDB.

O DuckDB continua no processo, mas só como **leitor de PBF** (é ele que tem o driver do GDAL):
lê em memória e entrega ao Postgres.

As malhas do IBGE existiam em DUAS cópias (`malhas/UF.geojson` e `dados/pbf/malha_UF.json`).
Agora é `ibge_malha` com GIST — achar o município de um ponto era abrir 13 arquivos e testar
polígono a polígono em Python; hoje é um `ST_Contains` indexado (`quadras_br.municipio_do_ponto`).

### Comandos
```
python quadras_br.py mun 2607653     # garante um município (código IBGE) — sob demanda
python quadras_br.py uf PE           # lote de uma UF inteira (opcional)
python quadras_br.py regioes         # lista regiões e tamanhos dos extratos
```

### Refino da quadra torta pela imagem — (removido em 28/07/2026)

O refino do contorno pela imagem de satélite (`pan_crop.py`, `detector.py`,
`telhado_mask.py` — WHU UNet++ com TTA de brilho) saiu junto com a detecção de
telhados. Hoje a borda real vem da **largura da via** (seção 16, passo 3): é
estimativa em vez de evidência, e o recuo aplicado fica gravado para auditoria.

## 16. Quadras + endereços — o processo de 5 passos (reescrito em 28/07/2026)

> **Esta seção substitui as antigas 16 a 26.** O processo anterior (detecção de
> telhados por SAM+WHU, curadoria com banco de exemplos, escada de confiança,
> alinhamento dos pontos, unificação de endereços, conferência visual pela IA e
> resgate de telhados) foi **removido por inteiro** a pedido do usuário, junto
> com as suas tabelas. O que existe hoje é só o que está descrito abaixo.

O processo responde a UMA pergunta: **de que face de que quadra é cada endereço,
e a numeração dele condiz com aquele lado da rua?** Ele classifica — não corrige
a base. **Nenhum ponto é movido**: a coordenada desenhada é sempre a original do
CNEFE, e é isso que permite ver onde o cadastro realmente está.

```
quadras.py area    --wkt "POLYGON((...))"    # 1  a área vira sessão
quadras.py vias    --sessao S                # 2  vias do OSM + quadras
quadras.py borda   --sessao S                # 3  borda real do quarteirão
quadras.py pontos  --sessao S                # 4  endereços do CNEFE
quadras.py faces   --sessao S                # 5  via e paridade por face
quadras.py alinhar --sessao S                # 6  distribui na testada
quadras.py telhados --sessao S [--com-maps]  # 7  Overture (+ âncora no Maps)
quadras.py casar   --sessao S                # 8  ponto ↔ telhado
quadras.py tudo    --area areas/area_atual.json   # 1 a 6
quadras.py tudo    --municipio "Itambé/PE"        # 1 a 6 na CIDADE INTEIRA
quadras.py retomar --sessao S                # segue do passo que faltou
quadras.py sessoes | ver --sessao S
```

Os passos **7 e 8 ficam fora do `tudo`** e do botão azul de propósito: eles
dependem de rede (Overture, e o Maps quando ligado) e custam 30× mais que todo o
resto somado. Quem quer só classificar os pontos não deve pagar por isso.

`--municipio` roda o **município inteiro** (Itambé/PE: 598 quadras em 6 min 43 s
ponta a ponta) e é a forma mais barata de usar o sistema — ver
"[Rodar a cidade inteira](#rodar-a-cidade-inteira)".

Pelo mapa é o mesmo caminho: o botão do painel chama `POST /api/quadras/area` e
`POST /api/quadras/rodar`, que executam **o mesmo módulo** do terminal. Rodar
pela tela e rodar pelo shell não podem divergir.

### A sessão é o que torna tudo retomável

Cada execução é uma **sessão** (`analise_sessao`): a área desenhada mais o último
passo concluído e o resumo de cada um. Todo passo grava o seu resultado no banco
antes de devolver e nunca depende de estado em memória do anterior — é isso, e não
um try/except, que faz a retomada funcionar. Se um passo estoura, o erro fica
registrado na sessão e `retomar` segue do seguinte.

Refazer um passo isolado **invalida os seguintes em cascata** (`limpar_passo`):
refazer as vias apaga quadras, pontos e faces, porque todos derivam delas. Meio
resultado velho misturado com metade novo é pior que refazer.

### Passo 1 — a área

O polígono desenhado vira sessão. O município sai do CNEFE por caixa envolvente —
é a base que o processo usa, então onde não há endereço também não há o que
analisar.

*Armadilha:* no psycopg2 uma consulta que falha deixa a transação **abortada**, e
o próximo comando morre com "comandos ignorados até o fim do bloco". O `except`
aqui faz `rollback` — sem isso o passo 1 quebrava ao não achar a malha municipal.

### Passo 2 — vias, nome canônico e quadras

As vias do OSM que cruzam a área saem da base do `quadras_br.py` (DuckDB, extrato
Geofabrik por região, materializado uma vez). A região certa é a que **contém** a
área: pegar "a última tabela de vias" trazia o Sudeste para uma área de Pernambuco
e devolvia zero via.

**O nome canônico é UMA consulta por via** (`quadras_canonico.py`), na coordenada
central dela: abre o panorama do Maps e lê o título ("645 R. do Alecrim - Google
Maps"). Antes eram 3 pontos por FACE, o que repetia a mesma rua dezenas de vezes
numa área com 40 quadras. O nome vai para a coluna `via_osm.nome_canonico`, e
todas as faces que encostam naquela via herdam a resposta no passo 5.

> **O regex do título não pode exigir número.** O parser antigo era
> `^(\d{1,6})\s+(.+?)\s+-\s+Google Maps`; rua sem endereçamento devolve
> "Av. Ten. Fontoura - Google Maps", sem dígito, e voltava vazio — foi por isso
> que a nomeação aparecia como "0 de 12 pontos lidos" e parecia problema de rede.
> Com o número opcional: **24/24 vias lidas em 139 s**.

O proxy é o **relay local** do padrão da busca de POIs: passar credencial direto
no `--proxy-server` do Chromium pendura a conexão no google.com (o urllib com o
mesmo IP responde em 1,9 s). O relay injeta o `Proxy-Authorization` e o navegador
nunca vê a senha. Ao parar, ele **cancela** os túneis vivos — senão o
interpretador despeja "Task was destroyed but it is pending" para cada um.

As quadras nascem do `polygonize` da rede viária unida: unir as linhas noda a rede
em todo cruzamento, e cada face fechada é um quarteirão. Por isso a borda corre
pelo **eixo** da rua — o passo 3 conserta.

**Fatia não é quadra.** Avenida desenhada no OSM como duas vias paralelas fecha
uma FATIA entre elas, e o `polygonize` a entrega como se fosse quarteirão — numa
área de Itambé, 5 das 11 "quadras" eram isso. As larguras separam sozinhas: as
fatias tinham 6,7 · 7,2 · 7,8 · 8,1 · 11,7 m, e a quadra real mais estreita 25,8 m.
`LARGURA_MIN_QUADRA_M = 15 m` (lado menor do retângulo mínimo) fica no vão: 11
quadras viraram 6, e nenhuma abaixo de 15 m sobrou.

### Passo 3 — a borda real

Cada face recua **metade da caixa viária da sua via**. Recuo único trava no lado
mais estreito e deixa os outros sobrando via, então é face a face.

Sem telhado detectado — o processo anterior recuava até ~1 m da construção mais
próxima, e essa detecção foi removida — a largura vem do **tipo da via no OSM**
(`LARGURA_VIA_M`: residential 8 m, primary 14 m, …). É **estimativa**, e o recuo
aplicado fica gravado em `quadra_face.recuo_m` para poder ser auditado. Se a
quadra encolher a menos de 15% da área original, a borda real é descartada
(`geom_real` nulo) — ali a via não cabe e o número está errado.

Medido em Itambé: recuo médio **4,0 m**, borda real em 19/19 quadras.

### Passo 4 — os pontos

Endereços do CNEFE **a até 20 m das vias que formam a quadra** (dos DOIS lados)
mais todos os que caem **dentro** da geometria dela. O ponto entra na coordenada
original, com `origem` dizendo de onde veio (`faixa_via` ou `dentro_quadra`).

A faixa pegar os dois lados é de propósito: é o passo 5 que decide de qual lado o
endereço é. Um mesmo endereço pode ser coletado por duas quadras vizinhas.

### Passo 5 — via canônica e paridade por face

Cada face herda a via do OSM cujo eixo acompanha ela, com o nome canônico já lido
no passo 2. Cada ponto vai para a face mais próxima (até 25 m).

**Primeiro o LADO, depois a paridade.** A reprovação tem duas causas, nesta
ordem:

1. **estar do lado de fora do quarteirão** — é o outro lado da via, e vale mesmo
   com a numeração batendo;
2. **destoar da paridade** do lado.

O lado sai do **recuo COM SINAL** (`_recuo_com_sinal`): positivo para dentro do
polígono, negativo para fora. Distância absoluta não distingue nada — um ponto
8 m para dentro e outro 8 m para fora medem igual, e a face elegia a paridade do
lado ERRADO: no mapa os endereços da quadra vizinha apareciam aceitos e os desta
em vermelho.

**O zero NÃO é a fronteira.** O CNEFE põe endereço em cima do eixo da via: na
Rua Antonieta Cabral os ímpares (desta quadra) oscilam entre −0,4 e +5,9 m e os
pares (do outro lado) ficam em −4,3 m. Cortar em zero reprovava o nº 237 por
estar **20 cm** para fora — 20 cm é ruído do cadastro, não lado de rua, e a face
caía de 15 aceitos para 4.

Então a decisão é **relativa**: entre os dois grupos de paridade, ganha o que
estiver mais para dentro. O zero volta como GUARDA em duas situações:
- classificação: só reprova por geometria quem passa de **meia caixa viária**
  para fora (`TOLERANCIA_LADO_MIN_M = 3 m`, ou o `recuo_m` da face se for maior)
  — aí o ponto atravessou a rua inteira;
- paridade: se o próprio grupo vencedor está além dessa faixa para fora, os dois
  são do vizinho e a face fica sem lado.

**E a paridade é decidida SÓ entre os que estão dentro.** A faixa de 20 m traz os
dois lados de propósito, então a maioria dos pontos de uma face costuma ser do
vizinho: a face 1 da q79 tinha 8 endereços dentro contra 40 fora, e a mediana
calculada sobre todos saía negativa — a face inteira ficava indefinida.

Entre os de dentro, o recuo ainda separa. Na face 3 da mesma quadra convivem
ímpares a **+2,1 m** (colados no eixo — coordenada do vizinho que cruzou a linha)
e pares a **+7,7 m** (sobre as casas): ganha quem está mais fundo, com
`LIMIAR_PROFUNDIDADE_M = 2 m` e ≥2 pontos de cada lado. Sem essa separação decide
a contagem (≥60% de ≥2 pontos); sem nenhuma das duas a face fica **indefinida** —
e face indefinida não reprova ninguém.

Conferido na q79 de Itambé: **21 aceitos, todos dentro do quarteirão** (zero
fora); 77 reprovados, dos quais 64 do outro lado da via e 13 por paridade.

### Um endereço, uma quadra (`_resolver_duplicados`)

O passo 4 percorre quadra a quadra, e a faixa de 20 m das DUAS quadras de uma
mesma rua alcança os mesmos endereços — o mesmo `cod_unico_endereco` entrava duas
vezes, uma por quadra. Cada linha era classificada por conta própria, então um
endereço podia sair **aprovado nas duas**: medido numa área com 2 quadras, 46
duplicados e 13 aprovados em dobro. No mapa o ponto era desenhado duas vezes,
podendo aparecer verde por uma face e vermelho pela outra.

Regra do usuário: *se o ponto está no espaço de outra quadra e a numeração é da
outra, ele é da outra — não se recupera como parte desta*. A escolha, nesta
ordem: estar **dentro** da quadra → a **paridade da face bater** → a menor
**distância à face**. As linhas perdedoras são apagadas.

Roda ANTES das regras de resgate (senão cada quadra "recupera" o mesmo endereço
por conta própria) e de novo no fim, porque os resgates podem reintroduzi-lo.
Depois disso a contagem é relida do banco: somar deltas depois de apagar linhas
mente.

> **A face é RECLASSIFICADA depois do expurgo** (`_classificar_faces` roda duas
> vezes). Apagar linhas muda a composição da face, e o veredito calculado antes
> fica velho: a Rua Alcides Carneiro da q116 guardava "só 21 endereços,
> insuficiente para dizer o lado" — com os 19 que restaram o recuo já separava
> (par +2,5 m × ímpar +0,4 m) e a face é `par`. Os pontos sem julgamento naquela
> face caíram de 15 para 2. A ordem é inevitável: o expurgo precisa da paridade
> para escolher o vencedor, e a paridade precisa do expurgo para ser exata.

Verificado: 214 linhas para 214 endereços distintos, zero aprovados em duas
quadras, e dos 54 aprovados que ficam fora do próprio polígono (a faixa entre o
eixo e a borda real) **nenhum está dentro de outra quadra da sessão**.

> Coordenada repetida no mapa não é duplicata: o CNEFE põe vários endereços no
> mesmo ponto (nº 225, 227 e 229 na mesma coordenada). São `cod_unico_endereco`
> distintos, na mesma face.

### Duas regras que aprovam por OUTRA evidência

A paridade reprova por numeração, e numeração o cadastro erra. Duas regras
resgatam o reprovado quando outra evidência o defende — ambas rodam DEPOIS da
classificação principal, e o ponto resgatado fica marcado.

**1. Identidade do endereço** (`_tolerancia_por_endereco`): mesmo logradouro +
mesmo CEP + mesma paridade da face vale mesmo que a coordenada esteja do outro
lado da via. Recupera o que já foi coletado e TRAZ do CNEFE o que a faixa de 20 m
não alcançava. As três condições juntas seriam a trava — mas **não bastam**.

> **O que decide é a distância à FACE.** Sem esse corte a regra puxava os outros
> quarteirões da mesma rua: em Itambé o município inteiro tem um CEP só, então
> sobrava "mesma rua + mesma paridade", e uma rua atravessa vários quarteirões.
> Medido em 6 sessões: o ponto aprovado pela geometria fica a no máximo **24,1 m**
> da linha da face (mediana 4,3 m); o trazido só pela identidade ficava a **363 m**
> de mediana. Não há sobreposição. `DIST_MAX_FACE_M = 30 m` preserva 100% dos
> legítimos e elimina os demais — de 55 trazidos para 4.

**0. Dentro do quarteirão** (`_dentro_e_da_quadra`): regra do usuário — *se está
dentro da quadra, é da quadra*. O ponto no miolo do quarteirão não está em nenhum
dos dois lados de nenhuma rua: a paridade não fala dele e o raio que amarra ponto
a face também não o alcança. Ficava em cinza, "sem julgamento", com a evidência
mais forte que existe já dada. Entra pela face mais próxima, marcado como resgate
— tom escuro, porque é dele mas não pela regra principal. Roda por último, sobre
o que as demais não resolveram.

**2. Coordenada nível 1** (`_resgate_nv1`): `nv_geo_coord = 1` é a coordenada que
o IBGE capturou no próprio endereço. Se além disso o ponto está tão perto da
quadra real quanto a **metade mais próxima dos aprovados daquela face** (mediana
das distâncias), a geometria diz que ele é daqui mesmo com o número contra. Com
menos de `MIN_APROVADOS_MEDIANA` (4) aprovados a mediana não significa nada, e a
régua passa a ser o mais distante deles.

> A régua do resgate 2 exclui quem foi aprovado pelo resgate 1: aqueles vieram
> por IDENTIDADE, de até 1 km, e incluí-los levou a mediana de uma face a **279 m**
> — com isso a regra deixaria passar qualquer coisa. Régua de proximidade só se
> faz com quem estava perto.

No mapa o resgatado sai na cor da face em **tom escurecido** (`escurecer()`), e o
tooltip diz por que foi aprovado e qual era a reprovação anterior. Medido em
Itambé: 17 resgates por nível 1, todos a ≤1,8 m da quadra real.

`canonico` é **ternário** de propósito: `true` condiz, `false` destoa, `null` não
dá para julgar. Pintar de vermelho quem nunca teve número seria acusar o ponto de
um defeito que é do cadastro.

**Sem número, quem julga é o LOGRADOURO.** A paridade existe só para separar os
dois lados de uma rua; se o endereço não tem número, ela não tem o que dizer — mas
o nome da via tem. Endereço sem número cujo logradouro é o da face e que está do
lado dela **é daquela face**. O mesmo vale quando a face é que não tem paridade
definida.

Isso valia 24% dos pontos: 107 de 446 numa sessão de Itambé estavam parados em
cinza esperando um dado que nunca ia chegar (a Praça Getúlio Vargas tinha 27 de 41
sem número — "MATERNIDADE DE ITAMBÉ", "BANCA MONTE DE OURO"). Depois da regra:
**70 dos 107 aprovados**, e o cinza da sessão inteira caiu de 107 para **16** —
os que sobraram têm logradouro diferente do da face, que é reprovação honesta.

### No mapa

Cada face tem a sua cor e o ponto usa a cor da face a que pertence; **vermelho** =
numeração destoa daquela face (é o outro lado da rua); **cinza** = sem julgamento;
**tom escurecido** = aprovado por resgate. A legenda agrupa por quadra, e clicar
numa face isola os pontos dela — é assim que se confere um lado da rua de cada vez.

> A face é identificada por **quadra + índice**: o `face_idx` reinicia em cada
> quarteirão, então usá-lo sozinho juntava a face 0 de 19 quadras num item só.

**Uma via, duas quadras.** Quando a rua separa dois quarteirões da mesma sessão,
cada um tem a sua face ali. Colorir por `face_idx` dava a MESMA cor às duas e as
linhas caíam uma sobre a outra — não dava para saber de quem era cada ponto. A cor
passou a ser por **(quadra, face)**, única na sessão (`mapearCoresFaces`), e a
linha é **deslocada 3 m para o lado da sua quadra** (`deslocarParaDentro`), então a
rua compartilhada mostra as duas lado a lado. Só o desenho é deslocado; o dado
não. O tooltip da face abre com "quadra N · face M".

### Passo 6 — alinhar na borda real (`passo6_alinhar`)

Os cinco primeiros passos **não movem nada**. Este acrescenta uma posição
CALCULADA ao lado da original — em colunas próprias (`lat_alinhado`,
`lng_alinhado`, `ordem_face`, `desloc_m`). A coordenada do CNEFE continua
intacta, e o mapa desenha as duas ligadas por tracejado: a correção tem de ser
auditável contra o que o cadastro diz.

Três decisões, nesta ordem:

**1. Onde a face começa e termina.** A borda real é a linha da face recuada de
meia caixa viária para dentro — o mesmo recuo que o passo 3 aplicou. É ali que os
lotes começam, e é o trilho. Fica gravada em `quadra_face.anel_real_wkt` e sai no
mapa como tracejado fino na cor da face.

E ela **não vai até a esquina**: cada ponta é encurtada pela meia caixa da via
TRANSVERSAL mais `MARGEM_ESQUINA_M` (4 m). Sem encurtar, o primeiro e o último
endereço pousavam no cruzamento, visualmente em cima da face vizinha. Encurtar só
pela meia caixa não bastou — numa esquina de 90°, andar `recuo` metros ao longo da
face leva **exatamente** à linha da transversal (medido: 0,03 m). A margem afasta
a primeira e a última porta do cruzamento, e corresponde ao recuo lateral que o
lote de canto tem no chão. Resultado: pontos a menos de 2 m de outra face da mesma
quadra caíram de **27 para 0**; a menor distância passou a ser 2,6 m.

**2. Para que lado a numeração cresce.** Sai dos próprios pontos (`_sentido`):
projeta cada um no trilho e compara a ordem das projeções com a ordem dos números,
somando o sinal das inclinações par a par. É Theil–Sen reduzido ao sinal — um
ponto com coordenada torta não inverte a face inteira. Não se supõe sentido.

**3. O espaçamento é proporcional ao NÚMERO, não à contagem.** Em 10, 12, 26, 28,
30 o vão de 12→26 vale sete vezes o de 10→12: entre eles faltam endereços que o
cadastro não tem, e espaçar por igual fingiria que a rua é contínua. A posição é
`(n − n_min) / (n_max − n_min)`, ancorada nas duas pontas da face.

**4. Teto de deslocamento** (`DESLOC_MAX_M = 10 m`) — e dois modos de alinhar
(`quadra_ponto.alinhado_modo`):

- **`regua`** — distribuído pela numeração, como acima. Só quando o alvo fica a
  até 10 m da coordenada original.
- **`perpendicular`** — o ponto **não fica na coordenada crua**: vai para o PÉ DA
  PERPENDICULAR na testada, o movimento mínimo que o põe na frente do lote sem
  reordená-lo pela numeração. Três casos caem aqui: a régua o levaria além do
  teto; o endereço **não tem número** (76 na sessão de Itambé, aprovados pela
  regra do logradouro); ou a face não tem **duas âncoras numeradas** para haver
  reta.
- **`preservado`** — fica **exatamente onde o CNEFE o pôs**. Ver "Via que não
  fecha quadra" abaixo.

#### A rua que não fecha quadra vira trilho dela mesma

Beco, rua projetada e acesso de engenho não delimitam quarteirão — mas a rua
**existe**, tem eixo e tem dois lados, e é nela que esses endereços moram. Antes
eles eram projetados na testada da quadra VIZINHA (50 a 164 m de arrasto) ou
ficavam na coordenada crua, espalhados sem organização nenhuma.

`alinhar_vias_abertas` usa a própria via como trilho: recuada de meia caixa para o
lado em que o ponto está (dois trilhos por via, um por lado), com a **mesma régua
do passo 6** — sentido pelo Theil–Sen dos próprios números, espaçamento
proporcional ao NÚMERO e teto de `DESLOC_MAX_M`.

O lado sai do produto vetorial entre a direção da via e o vetor até o ponto. Não
importa qual lado é qual — importa que os dois grupos não se misturem, senão a
régua ordenaria juntos os números dos dois lados da rua.

**Teto também na perpendicular**, que aqui é a saída mais usada: ela é o
"movimento mínimo", mas mínimo de 105 m é teletransporte — o ponto simplesmente
não é daquela rua. Além de `FAIXA_PONTOS_M` (20 m, a mesma faixa com que a coleta
o trouxe) ele fica na coordenada original.

Medido em Itambé — **1.039 pontos** distribuídos (77 pela régua, 962 na
perpendicular) e 76 mantidos por estarem longe demais. A dispersão em relação à
rua colapsou:

| distância à rua | mediana | desvio | máximo |
|---|---:|---:|---:|
| antes (coordenada crua) | 4,8 m | **5,0 m** | 24,6 m |
| depois (alinhado) | 4,0 m | **0,5 m** | 7,0 m |

A mediana de 4,0 m é meia caixa de via residencial: os pontos estão exatamente
sobre a testada. No mapa saem com **anel âmbar**, tracejado quando na
perpendicular.

#### O que NÃO deve ser movido (`preservar_no_lugar`)

**Todo deslocamento acima de 20 m é `perpendicular`** — medido: régua, telhado e
interpolado nunca passam do teto de 10 m. A perpendicular é a única etapa sem
limite, e era por ela que os disparates entravam.

A condição de entrada é sempre o **telhado a menos de
`RAIO_TELHADO_VIA_ABERTA_M` (53 m)**: havendo construção ali, a coordenada do
CNEFE aponta para algo real e vale mais que qualquer projeção. A partir dela, uma
situação manda manter o ponto onde está:

**O nome do endereço não confirma a face.** Se o ponto diz "RUA TIMBAÚBA" e a
face para onde ele iria tem outro nome — ou não tem nome nenhum, porque nenhum
endereço do CNEFE caiu nela —, não há o que sustente a mudança. O nome do próprio
endereço é a evidência mais forte de onde ele fica, e nenhuma projeção passa por
cima dela.

> A discordância de nome **prevê** o disparate. Antes da regra: nome batendo →
> deslocamento médio 5,8 m; nome diferente → 9,2 m; face sem nome → **18,0 m**.
> E os deslocamentos acima de 20 m: 249 com nome igual, 208 com nome diferente,
> 89 em face sem nome — desproporção enorme, já que a categoria "nome diferente"
> tem 8× menos pontos.

Sem telhado por perto não existe evidência nenhuma, e o ponto segue com o
tratamento normal: melhor uma projeção do que uma coordenada solta no nada.

Quem fecha quadra é decidido em `via_osm.fecha_quadra`, pela **fração do
comprimento que a via corre AO LONGO de uma borda** (`_fecham_quadra`, corte em
50%). Não confundir com `_vias_das_quadras`, que só pergunta se a via ENCOSTA
numa borda: uma viela que cruza a rua perpendicularmente atravessa o buffer e
ganha ~24 m de sobreposição, o bastante para o critério de lá. A distribuição
medida é **bimodal** — 506 das 1.452 vias de Itambé abaixo de 10% e 737 acima de
90% —, então o corte exato pouco importa: entre 30% e 70% o total só varia de 597
para 688 vias.

A regra depende dos telhados, então roda no fim do passo 6 (se o 7 já tiver
rodado) e de novo no fim do 8, que é onde ela pega numa corrida normal de 1 a 8.
É idempotente.

Efeito em Itambé inteira: **2.100 pontos preservados** (1.007 pela via, 1.093
pelo nome). A perpendicular caiu de 5.166 para 3.688 pontos e sua média de
14,5 m para 7,6 m; o máximo geral de 163,8 m para 101,7 m; e os deslocamentos
acima de 53 m foram de **299 para 22**.

Por categoria de nome, depois da regra:

| categoria | n | média | máx | > 20 m |
|---|---:|---:|---:|---:|
| nome IGUAL | 10.864 | 5,8 m | 101,7 m | 245 |
| nome DIFERENTE | 1.365 | **0,0 m** | 9,4 m | **0** |
| face SEM NOME | 242 | 0,2 m | 36,6 m | 1 |

**O que sobra:** os 245 acima de 20 m estão todos na categoria *nome igual* — são
endereços na rua CERTA, apenas longe da própria testada. Não são o problema
relatado; seriam resolvidos por um teto na perpendicular, que hoje é a única
etapa sem limite, mas isso muda o comportamento de pontos legítimos e fica em
aberto.

No mapa o preservado sai com **anel azul-claro** e tooltip "📌 mantido onde
estava"; e o ponto de origem **não** é apagado, porque ele não saiu do lugar.

#### Rua que só tem um lado

A paridade existe para separar os **dois lados** de uma rua. Onde não há outro
lado — a rua margeia o fim do bairro, um rio, a zona rural — ela não tem o que
separar: par e ímpar caem todos na única face que existe, e reprovar metade deles
por "numeração destoa" é aplicar uma régua que não vale ali.

`_sem_outro_lado` anda perpendicular à face, para fora da própria quadra, um
pouco além da caixa da rua; se não cai dentro de nenhuma outra quadra da sessão,
não há outro lado. Em Itambé são **563 das 2.260 faces (25%)**.

Nessas faces **nem a paridade nem a geometria reprovam**. As duas regras só sabem
escolher ENTRE dois lados:

- a **paridade** decide se o número é do lado par ou do ímpar;
- o **teste de lado** decide se o ponto atravessou a rua.

Sem outro lado, nenhuma das duas tem o que decidir, e reprovar deixa o endereço
órfão: não há face nenhuma para onde ele possa ir. Então **todos os pontos
coletados são distribuídos** naquela face. O alcance continua limitado pela coleta
(faixa de 20 m das vias formadoras) e pela distância à face (`RAIO_FACE_M`) — não
é uma porta aberta.

O ponto entra com `resgate='via_sem_outro_lado'`, em tom escuro como os demais
resgates: é daquela face, mas não pela regra principal.

Medido em Itambé: **1.003 pontos** marcados. Reprovações por paridade de 249 para
176; por "está do outro lado da via", de 1.756 para 1.094 (as que sobram são de
faces que de fato têm outro lado). No total, os reprovados caíram de **1.932 para
1.270** e os aprovados subiram de 12.471 para **13.057**.

> **TODO aprovado termina sobre a testada da sua face.** Antes só os numerados
> entravam, e 77 aprovados sem número ficavam soltos no meio da quadra — era o
> que sobrava fora das linhas no mapa. Agora: 298 aprovados, 298 com posição na
> face, **zero** sem.

Arrastar um endereço 100 m só para ele caber na régua é trocar um dado ruim por
um palpite: a régua vale onde a coordenada já está quase certa. Mas deixá-lo na
posição bruta também não serve — ele fica fora da testada, sem alinhar com nada.
A perpendicular é o meio-termo honesto, e o ponto segue **marcado** para que o
desacordo apareça.

No mapa os perpendiculares saem com **anel tracejado**, e o tooltip diz o que
aconteceu: "posto só na perpendicular · 1 m da coordenada original — a régua o
levaria 105 m, acima do teto de 10 m". O modal de visualização traz a contagem.

Medido em Itambé (6 quadras, 17 faces): **81 pela régua** (deslocamento mediano
5,3 m, máximo 9,7 m) e **217 na perpendicular** — 140 por passar do teto, 77 por
não ter número ou âncora. Os dois modos caem exatamente sobre o trilho —
conferido no mapa desenhado, distância máxima 3×10⁻¹⁰ m. A constância do passo
confirma a proporcionalidade: +8 no número dá 9,4 m, +10 dá 11,7 m, +4 dá 4,7 m.

Os 148 que seguem fora da testada são **147 reprovados** (não são daquela face,
por definição) e 1 sem julgamento.

> Antes do teto eram 221 pela régua, com mediana de 19 m e máximo de 140 m. Ou
> seja: **quase dois terços do "alinhamento" era invenção** — a régua arrastando
> pontos cuja coordenada não tinha nada a ver com a numeração.

> Só entram pontos **aprovados e com número** — sem número não há como posicionar.
> Faces com menos de 2 pontos numerados não são alinhadas: duas âncoras é o
> mínimo para haver reta.

**Uma porta, um marcador** (`_agrupar_mesmo_endereco`): mesmo logradouro + mesmo
número é UMA porta. O CNEFE registra vários domicílios no mesmo endereço (o
prédio, a vila, os fundos) e espalha as coordenadas em volta — cada um sozinho
parece endereço diferente. O destino é o **medoide**, o membro cuja posição
minimiza a soma das distâncias aos outros, ou seja, o mais perto da maioria
deles: medoide e não centroide porque o centroide inventa um ponto onde talvez
não haja nada, e o medoide é uma coordenada que o cadastro registrou. No mapa o
grupo vira um marcador só, com raio crescendo pela raiz do tamanho (9 endereços →
11 px contra 5 px de uma porta simples). Medido em Itambé: **95 endereços
repetidos em 40 portas**.

### Modal de visualização

O mapa acumula nove camadas (área, quadra do OSM, borda real, vias, faces,
trilho, origem, alinhado e a ligação entre os dois) e quatro classes de ponto.
Ver tudo junto impede conferir qualquer coisa. O botão **👁 Visualização** abre um
modal que liga e desliga cada camada e cada classe, com a contagem do que cada
uma desenha, mais dois atalhos: **tudo** e **só alinhados** (deixa apenas a borda
real, o trilho e as posições alinhadas).

A escolha vive em `qVis`/`qMostra` e **sobrevive ao recarregar a sessão** — quem
isolou uma vista não a perde ao trocar de quadra. Camada é ligada/desligada no
mapa; classe apenas esconde o elemento, sem desmontar a camada.

### Passos 7 e 8 — telhados e o casamento com os pontos (`quadras_telhados.py`)

**Passo 7 — identificar os telhados, rápido.** Sem imagem e sem ML: os polígonos
vêm do **Overture** (parquet público na S3, lido pelo DuckDB com filtro de caixa).
A escolha da fonte foi medida:

| fonte | prédios em Itambé | custo |
|---|---|---|
| OSM (do PBF em disco) | **0** | 40–60 s por consulta |
| Overture | **11.605** na caixa | ~65 s, uma vez por sessão |

O OSM foi descartado por cobertura — a consulta está certa (92 prédios no centro
do Recife), mas footprint de cidade pequena não existe lá. Resultado na sessão de
Itambé: 385 telhados na área, **189 dentro das quadras**, 16 faces, mediana de
110 m².

**A âncora de campo** (ideia do usuário): um telhado por face vai ao Maps para ler
o número do título — o Overture não traz endereço nesta versão. Duas descobertas:

> Consultar no CENTROIDE do telhado não funciona: ele fica dentro do quarteirão e
> o Maps engata no panorama mais próximo, que pode ser de outra rua. Quatro das
> cinco primeiras âncoras voltaram com via diferente da face ("Rua Pascoal
> Carrazzone" → "Av. São Paulo"). A consulta passou a ser na projeção do telhado
> no EIXO DA VIA, onde o carro do Street View passou.

> Mesmo assim, em Itambé **só 1 de 16 faces confirma**: 6 responderam com nome de
> rua e SEM número, assinatura de panorama de outra via. A cobertura de Street
> View ali é rala. Por isso a âncora só é gravada quando a via lida **confirma** a
> da face — número certo na rua errada moveria a régua inteira, e é o pior tipo de
> erro, porque parece dado bom.

**Passo 8 — casar ponto com telhado** (`passo8_casar`). Até aqui a régua ancorava
nas duas PONTAS da face: sabia proporção, não sabia onde cada porta fica. O
telhado sabe.

1. cada telhado é projetado na testada — ali está a porta dele;
2. os pontos, na ordem da numeração, são casados um a um com as portas por
   casamento guloso pela menor distância (`CASAMENTO_MAX_M = 12 m`, uma frente de
   lote com folga), **um telhado por ponto**;
3. quem casa vai para a porta do seu telhado e vira ÂNCORA;
4. quem não casa é distribuído ENTRE as âncoras pela mesma regra proporcional ao
   número — o vão de 12→26 continua valendo sete vezes o de 10→12, só que agora
   entre duas portas reais em vez de entre as esquinas.

O teto de 10 m continua valendo contra a coordenada ORIGINAL: casar com um telhado
a 80 m não é casar, é inventar.

Medido: **100 pontos casados com telhado · 102 interpolados entre as portas**,
deslocamento mediano 5,0 m e máximo 9,9 m, nenhum acima do teto, todos sobre a
testada. Os modos finais de posicionamento (`alinhado_modo`): `telhado` 100 ·
`perpendicular` 128 · `interpolado` 102 · `regua` 18.

No mapa o casado sai com **anel branco**, e o tooltip diz com que telhado casou e
a área dele.

### Quanto custa rodar (medido em 28/07/2026, Itambé/PE)

| passo | 1 quadra | 6 quadras |
|---|---:|---:|
| 1 área | 0,2 s | — |
| 2 vias + quadras | 1,0 s | — |
| 3 borda real | 0,0 s | — |
| 4 pontos (CNEFE) | 0,2 s | — |
| 5 faces | 0,6 s | — |
| 6 alinhar | 0,0 s | — |
| **1–6 somados** | **2,0 s** | **4,4 s** |
| 7 telhados (Overture) | 66,3 s | 83,9 s |
| 8 casar | 0,0 s | 0,1 s |
| **TOTAL** | **68 s** | **88 s** |

**A análise inteira custa segundos; 97% do tempo é a consulta ao Overture.** E
esse custo é FIXO, não proporcional à área: 66 s para uma caixa de 0,019 km² e
72 s para 0,162 km² — o que se paga é a varredura do índice parquet pela rede,
não o número de telhados.

A consequência prática: **desenhe áreas grandes**. Uma quadra sozinha sai a 68 s;
seis saem a 88 s, ou **14,7 s por quadra**. Com trinta quadras o custo por quadra
cai para poucos segundos.

Se o Overture não for necessário (só a classificação dos pontos), os passos 1–6
rodam sozinhos em **2 a 4 segundos** — é por isso que 7 e 8 ficaram fora do
`tudo` e do botão azul, e são disparados à parte.

A leitura do nome no Maps (`--com-maps`, passo 2) e a âncora de campo (passo 7)
acrescentam ~10 s por via e ~30 s por sessão. Ambas estão desligadas por padrão.

### Rodar a cidade inteira

Levando a conclusão acima ao limite: dá para processar o município todo de uma
vez, e é **a forma mais barata de usar o sistema**.

```bash
python quadras.py tudo --municipio "Itambé/PE"     # passos 1 a 6
python quadras.py telhados --sessao <id>           # passo 7
python quadras.py casar    --sessao <id>           # passo 8
```

`--municipio` aceita `"Nome/UF"`, só o nome (se não repetir entre as UFs
baixadas) ou o código do IBGE, e tira o polígono da malha já em disco
(`malhas/UF.geojson`) — a mesma que identifica o município de um ponto.

Medido em Itambé/PE (**306 km²**, 29/07/2026):

| passo | tempo | resultado |
|---|---:|---|
| 1–6 | 3 min 47 s | 1.452 vias · **598 quadras** · 2.260 faces · 14.588 endereços |
| 7 telhados | 2 min 53 s | 46.182 telhados na área, **8.680 dentro das quadras** |
| 8 casar | 3 s | 4.359 casados + 5.080 interpolados |
| **TOTAL** | **6 min 43 s** | **0,67 s por quadra** |

Compare com os 68 s de uma quadra isolada: **a cidade inteira sai 100× mais
barata por quadra**. A consulta ao Overture custou 85 s para 736 km² de caixa
contra 66 s para 0,019 km² — confirmação direta de que o custo é fixo. Não há
motivo para rodar quadra a quadra.

Qualidade nessa corrida: 12.416 endereços canônicos contra 1.998 destoantes, e
**1.425 das 2.260 faces (63%) receberam via canônica**. As faces sem via são as
que não têm endereço do CNEFE — o nome sai da maioria dos endereços, então face
vazia fica sem nome. É limite do dado, não do processo.

**O gargalo passa a ser o navegador, não o Python.** A sessão inteira devolve
19,2 MB de GeoJSON (30.426 feições), que o Leaflet transforma em **52.311 paths
SVG** e ~200 MB de heap: 4,6 s de download e ~14 s de desenho. Funciona, mas é o
teto — para navegar, use a **lista de quadras tratadas** e abra uma por vez, que
é justamente para isso que ela existe.

### Esquema

`analise_sessao` · `via_osm` (com `nome_canonico`) · `quadra` (`geom_osm` e
`geom_real`) · `quadra_face` (via canônica, paridade e os dois recuos) ·
`quadra_ponto` (coordenada original, `origem`, `canonico`, `motivo`).

As tabelas do processo antigo (`telhado`, `telhado_exemplo`, `telhado_ponto_ia`,
`telhado_face`, `telhado_face_sv`, `telhado_quadra`, `tipo_construcao`, `telhados`
e `telhados_seg`) foram **apagadas**, e os módulos correspondentes removidos do
repositório.

### Medido em Itambé/PE (área de 0,1 km², 28/07/2026)

24 vias · **24/24 com nome canônico** em 139 s · 19 quadras · borda real em 19/19
(recuo médio 4,0 m) · 2.090 pontos · 74/74 faces com via canônica · 820 canônicos
× 790 destoando. Os 790 são o comportamento pedido: a faixa de 20 m traz os dois
lados da rua e a paridade marca em vermelho o lado que não é daquela face.

Conferido no mapa: 2.090 pontos desenhados, **desvio máximo da coordenada
original = 0 m**.
---

## 17. Regras de posicionamento acrescentadas em 03/08/2026

Todas nasceram de um caso concreto que o usuário mostrou no mapa, e cada uma foi
medida antes e depois. Ordem em que agem no passo 5 e no 6/8:

### Passo 5 — a quem o endereço pertence

**O contexto é a CIDADE, não o desenho** (`quadras_do_municipio`). Duas perguntas
— "esta via forma quarteirão?" e "esta rua tem outro lado?" — eram respondidas
contra as quadras da SESSÃO. Quem desenha em cima de um único quarteirão não tem
outra para comparar: 82 de 82 vias saíam como "não fecha quadra" e toda face
virava "rua de um lado só", então **nada era reprovado** e o quarteirão vizinho
entrava inteiro. Hoje consulta `osm_quadra` do município (Itambé: 609 quadras).

**Face sem via no OSM não herda o nome de uma face que tem.** O respaldo é medido
pela FRAÇÃO da face que corre ao longo da via (`_via_ao_longo`), não por
distância — numa esquina a face encosta em todas as transversais a 0,0 m. Uma
face de 51 m sem via nenhuma tomava "RUA JOAO PAES" pela maioria dos endereços e
puxava os pontos da João Paes de verdade, que tem 100% de respaldo ao lado.
Resolvido em duas passadas: primeiro as faces com respaldo, depois as sem.

**Na esquina, quem decide é o nome do logradouro** (`EMPATE_ESQUINA_M = 5 m`). O
lote de canto tem frente para duas ruas e a face mais próxima por centímetros
pode ser a errada. Medido: 714 pontos (4,9%) empatam dentro de 1 m, 1.744 dentro
de 5 m; destes, 122 estavam reprovados com o logradouro batendo com a segunda
face. Recuperou 19 — o resto depende de o nome da face já estar gravado, e na
primeira das duas passadas de `_classificar_faces` ele ainda não está.

**Face sem paridade herda o CONTRÁRIO da face de frente**
(`paridade_pela_face_oposta`). A rua tem dois lados complementares, mas cada face
decidia sozinha: **1.349 das 2.260 faces (60%)** ficavam sem paridade, e face sem
paridade não reprova ninguém. A oposta é achada andando perpendicular ao meio da
face, para fora da quadra, além da caixa; quando as duas têm nome, eles precisam
bater. **A herança propaga**, então roda em rodadas até nenhuma face nova
aprender: uma rodada ensinava 162 faces, iterando chega a 230. Os pontos
numerados parados por falta de paridade são rejulgados (213). Sobram 1.126 faces
sem paridade — a de frente também não tem, e é limite do dado.

### Passos 6 e 8 — para onde o endereço vai

**Ponto EM CIMA de um telhado não se move.** Se a coordenada do CNEFE caiu sobre o
polígono de uma construção do Overture, a porta é ali; levá-la ao trilho a joga na
rua. Vence inclusive o nome batendo com a face, e é mais forte que o "telhado a
53 m", que só indica região construída.

**O alinhamento não atravessa a rua** (`nao_atravessar_via`). O trilho corre
dentro do quarteirão, então recuo negativo projetado nele cruza a via. Mas
negativo pequeno NÃO é do outro lado: o CNEFE põe o endereço em cima do eixo — 9
dos 10 cruzamentos tinham recuo entre −0,1 e −2,8 m, e movê-los para a calçada é o
certo. O corte é a meia caixa viária. O caso real era um só: −13,4 m arrastado
18,2 m.

**Na rua aberta, o trilho é o TRECHO OCUPADO, não a rua inteira.** A face de
quadra é curta por natureza; uma via que não fecha quadra tem centenas de metros
(447 m no caso visto), e espalhar a numeração por tudo jogava cada endereço longe
da origem — o teto de 10 m barrava e quase todos caíam na perpendicular. A régua
passou a interpolar entre as projeções extremas dos próprios endereços do grupo
(via, lado). Régua na rua aberta: 77 → **267** pontos; perpendicular 927 → 737,
com deslocamento médio de 6,4 → 4,6 m.

### O que NÃO tem solução com os dados de hoje

**Cruzar coordenadas de várias fontes não é possível.** Medido em Itambé:

| fonte | endereços urbanos | veredito |
|---|---:|---|
| CNEFE 2010 | **0** de 7.386 | IBGE só coletou GPS no rural em 2010 (61–82% lá) |
| Overture `addresses` | 16.573 | **é o próprio CNEFE** — `dataset: br_ibge`, mediana 0,1 m |
| OSM `addr:housenumber` | **10** na caixa inteira | inexistente |

Não há fonte independente para tirar mediana. A única que de fato traria
coordenada urbana nova é a leitura no Maps (título do panorama), já implementada e
desligada por custar ~10 s por via. O telhado do Overture é a outra evidência
independente — vem de imagem, não do IBGE — e já é usada.

---

## 18. A via que não fecha quadra virou FACE DE VERDADE (04/08/2026)

### O que estava errado

`alinhar_vias_abertas` era um caminho paralelo: agrupava os pontos por
`(via do OSM, lado pelo produto vetorial)` e distribuía com uma régua própria.
Esse grupo não é uma face — não tem nome, não tem paridade, não tem recuo nem
borda real. Medido na sessão de 03/08 (Itambé, 306 km²):

| | |
|---|---:|
| pontos de via aberta | 1.004 |
| **com o logradouro DIFERENTE do nome da face que os carregava** | **547 (54%)** |
| grupos misturando par e ímpar numa régua só | 74 de 162 |
| estouros do teto que estavam em grupo com par+ímpar | **456 de 507 (90%)** |
| deslocamento que a régua queria (mediana) | **33,3 m** (máx 785 m) |

Casos reais: `RUA DAS MARGARIDAS nº 1029` na face `RUA AFONSO PENA`;
`RUA SETE 3 nº 26` na face `1A TRAVESSA DA RUA JOAO PAES`. O endereço herdava
nome, paridade e recuo de uma rua que não era a dele.

Os 737 que iam para a perpendicular sem ordem se dividiam em: **419** a régua
estourou o teto, **234** sem número (`numero = 0`, não NULL), **84** grupo com
menos de duas âncoras numeradas.

### O modelo novo

Cada via que **não fecha quadra** vira, no passo 2, **quadras degeneradas**:

1. o traçado é quebrado **nas transversais** (`_partir_nas_transversais`) — é a
   esquina que faz a face ser face, e sem isso uma via de 447 m era uma "face"
   só. Trecho abaixo de `COMP_MIN_FACE_ABERTA_M` (15 m) é toco de cruzamento e
   não entra;
2. cada trecho gera **duas** quadras, uma por lado (`_corredor`): o corredor vai
   do eixo até `FAIXA_PONTOS_M` para um lado, e **invade 0,5 m para o outro** de
   propósito — o CNEFE põe o endereço em cima do eixo, e um ponto exatamente
   sobre a linha não estaria contido em nenhum dos dois lados;
3. é esse polígono que responde "de que lado da rua está este endereço?", a mesma
   pergunta que o polígono do quarteirão responde numa face normal. Por isso
   `_recuo_com_sinal`, `_paridade` e `_canonico` funcionam **sem saber** que a
   quadra é degenerada.

Colunas novas em `quadra`: `via_aberta_id`, `lado`, `eixo_wkt`.

O passo 4 colhe os endereços da própria via **pelos dois lados** (é o passo 5 que
decide o lado, e para decidir ele precisa ver os dois grupos). O corredor **não**
conta como "dentro do quarteirão": `_dentro_e_da_quadra` o ignora, senão a regra
aprovaria justamente quem a paridade acabou de reprovar.

Três ajustes que o modelo exigiu:

- **"rua de um lado só" não pode ser perguntada ao município** — o corredor não
  existe no OSM, e toda via aberta responderia "um lado só", que dispensa a
  paridade e aprova os dois lados. A pergunta virou direta: *há endereço numerado
  do lado de lá da caixa?*
- **`_resolver_duplicados` ganhou um critério** — as duas faces de uma via aberta
  compartilham o MESMO eixo, então a distância à face empata sempre e não
  desempata nada. Entra `no_espaco` (estar do lado dela) antes da distância.
- **o trilho vai onde as portas ESTÃO** (`_recuo_observado`): a mediana do recuo
  do lado que venceu a paridade, e não a meia caixa contada a partir do eixo. O
  eixo do OSM quase nunca cai no centro exato da rua, e sem quadra não há
  polígono para dar a borda real. As pontas só encurtam onde o trecho encosta em
  outra via (`_pontas_de_esquina`) — beco sem saída não perde 8 m à toa.

`alinhar_vias_abertas` (172 linhas) foi **removida**, com a chamada no passo 8 e
o braço "a via mais próxima não fecha quadra" do `preservar_no_lugar`: hoje quem
mora em via aberta é julgado pelo nome como todo mundo.

### Medido (mesma área, 306 km², antes × depois)

| | antes | depois |
|---|---:|---:|
| quadras | 598 | 2.090 (598 + **1.492** corredores) |
| faces | 2.260 | 3.752 |
| pontos | 14.587 | 15.691 |
| aprovados | 12.919 | 13.867 |
| **logradouro do ponto bate com o nome da face (via aberta)** | **54%** | **97%** |

**O que funcionou:** a identidade. O endereço de via aberta está na face da rua
dele, com nome e paridade próprios — as faces saem limpas (`P37/I1`, `P0/I23`,
`P33/I0`), o que era impossível quando a régua misturava os dois lados.

**O que NÃO funcionou:** a régua. Nas faces de via aberta, 258 pela régua contra
805 na perpendicular — praticamente o mesmo 267/737 de antes. E no total geral a
perpendicular por teto **piorou**, de 6.011 para 7.003.

### A causa real, que não era a paridade

A paridade era 90% do problema *no modelo antigo*. Corrigida ela, o que sobrou é
um defeito **do processo inteiro, não só da via aberta**: a régua ancora em
`n_min` e `n_max`, então **um único número fora da série comprime todo o resto**.

`RUA JOSE CESAR MARINHO FALCÃO`, lado par, face de 293 m, 38 endereços:

```
47, 182, 216, 216, 224, 232, 240, 248, ... , 456, 460, 464, 480
 ↑ o 47 é o único abaixo de 182
```

Ancorando em 47–480, o nº 182 (o segundo da fila) cai a **91 m** do começo da
face; os 37 endereços reais ficam espremidos nos últimos 69% dela, cada um
deslocado dezenas de metros — e o teto de 10 m barra todos. Sem o 47, a série
182–480 se distribui sozinha.

Segundo caso, pior: `ESTRADA ENGENHO MEREPES` tem endereços **todos com o mesmo
número** (11). Aí `span = (n1 - n0) or 1` vale 1, `frac` dá 0 para todos e eles
se **empilham no começo do trilho**. Número igual não é régua nenhuma — esses
pontos deveriam ir para a perpendicular, não para o mesmo ponto.

### Dois defeitos que o mapa revelou (04/08/2026, mesma sessão)

O usuário apontou dois no print: pontos vermelhos parados **em cima da via** e
endereços de um lado **desenhados do outro**. Nenhum dos dois era o que parecia.

**1. Recuo POSITIVO era chamado de "outro lado da via"** (`_canonico`). O limite
acompanhava a linha das portas (`ref - meia caixa`), então numa face cujas portas
estão fundas ele subia junto:

```
motivo: "está do outro lado da via (+2 m, contra +7 m das portas desta face)"
```

`+2 m` é do lado de cá — só mais perto do eixo, que é onde o CNEFE põe boa parte
dos endereços. Eram **1.037 pontos não aprovados com o logradouro DA PRÓPRIA
FACE a 2,2 m dela**, 786 deles por esse motivo. Atravessar a rua é passar do eixo
para lá, além da meia caixa, e é só isso que o teste tem como afirmar:
`limite = min(ref - meia, -meia)`.

**2. O trilho de 11% das faces ficava do lado errado da rua** (`_face_real`) —
**399 de 3.752**, sendo **85 em quadras reais**: o defeito é anterior ao modelo
de via aberta, que só o tornou visível ao criar mais faces. Duas causas:

- a normal era a da **corda** entre o primeiro e o último ponto da face; numa
  face curva de 154 ou 200 m a corda não representa o traçado;
- o lado era escolhido pelo **centroide** da quadra, que num polígono em L ou num
  corredor estreito cai fora da face.

Agora cada vértice anda pela média das normais dos segmentos vizinhos, e o lado é
decidido por **contenção no polígono** (centroide só de reserva).

| | antes | depois |
|---|---:|---:|
| faces com o trilho fora do próprio polígono | 399 | **3** |
| pontos que terminam do outro lado do eixo | 716 | **526** |
| não aprovados com o logradouro da face, a <15 m | 1.037 | **783** |
| … destes, por "está do outro lado da via" | 786 | **103** (mediana 2,2 → 9,7 m) |
| … destes, sem número | 261 | **55** |
| aprovados | 13.867 | **14.189** |
| reprovados | 1.448 | **1.081** |
| "recuperados do outro lado da via" (resgate por identidade) | 740 | **116** |

A queda de 740 para 116 no resgate por identidade é a confirmação de que ele
estava **tapando o buraco do teste de lado**: sem o erro, quase ninguém precisa
ser resgatado.

Os 526 que ainda terminam do outro lado **não são defeito**: 308 estão em rua sem
outro lado e 213 são o `nao_atravessar_via` agindo — a regra explícita de não
arrastar ninguém para cruzar a rua. Mudá-los é decisão de produto, não correção.

---

## 19. O número fora da série: a régua não ancora nele, e ele vai para o trecho certo

`_marcar_fora_da_serie` roda no início do passo 6 e marca, por face, os números
fora do intervalo do quartil (IQR × 1,5). Salvaguardas: só age com ≥ `MIN_SERIE`
(6) endereços numerados e desiste se marcaria mais de `MAX_FORA_SERIE` (30%)
deles — quando um terço da face é exceção, quem está errado é a face.

O marcado sai da âncora `n_min`/`n_max` e da ordenação, e depois
`realocar_fora_da_serie` o leva para a face da **mesma rua**, com a **mesma
paridade**, cujo intervalo de numeração **contém** o número; havendo mais de uma,
a mais perto. Ele troca de dono e é posicionado pela régua daquele trecho. Sem
face que contenha o número, não se move — inventar destino é pior.

| | antes | depois |
|---|---:|---:|
| alinhados pela régua | 3.447 | **3.816** |
| perpendicular por estourar o teto | 7.405 | **6.743** |
| endereços com número fora da série | — | 293, em 179 faces |
| realocados | — | **95** (mediana 119 m) |
| sem trecho que contenha o número | — | 198 |

Nas faces medidas: `R. Projetada Q` par 207 m foi de 2 pela régua e 22 acima do
teto para **21 pela régua e 2 na perpendicular**; `R. João Pedro Ribeiro` ímpar
103 m, de 0 e 21 para **21 e 3**.

**Nome de rua é chave FRACA quando é genérico.** Sem teto de distância, 4 pontos
foram parar de 14 a 24 km, em homônimas do outro lado do município — `RUA SEM
DENOMINACAO`, `RUA SEM DENOMINACAO 2`, `RUA PROJETADA O`, `RUA PROJETADA Q`.
A distribuição escolheu o corte sozinha: 40 abaixo de 100 m, 31 de 100 a 250,
22 de 250 a 500, 3 até 1 km e **nada entre 1 e 5 km**. `REALOCAR_MAX_M = 500 m`,
medido do ponto até a face de destino (o deslocamento final pode passar disso, e
passa: máx 592 m, porque a régua o põe numa fração ao longo daquela face).

**A realocação troca o dono do ponto**, então a face de ORIGEM fica gravada em
`realoc_quadra_id`/`realoc_face_idx` e o `limpar_passo(6)` a devolve antes de
zerar o resto. Sem isso, refazer o passo 6 partia de um dono já trocado e a
sessão deixava de ser refazível — que é a garantia central do processo.
Verificado: `limpar_passo(6)` devolveu os 95, e refazer o passo deu exatamente os
mesmos 95 realocados e 3.816 alinhados.

**No mapa**, a ligação até a coordenada original deixa de ser um fio cinza quando
o movimento é grande — a correção tem de ser conferida, não engolida:

| ligação | quando |
|---|---|
| âmbar, 2,6 px | `alinhado_modo='realocado'` — trocou de trecho |
| vermelha, 2 px | deslocamento ≥ `DESLOC_DESTAQUE_M` (25 m, dois e meio o teto) |
| cinza fina | o resto |

O marcador realocado ganha anel âmbar de 3,2 px e o tooltip abre com
`⚠ REALOCADO para outro trecho da rua`, com o intervalo de destino e a distância.
Em Itambé, **1.014 ligações** ficam em destaque (7% dos aprovados).

### Aberto

- **198 dos 293 fora da série não têm para onde ir** — nenhuma face daquela rua
  contém o número. Parte é erro de cadastro (`RUA JOAQUIM BARBALHO` tem um nº
  **22611** numa rua que vai de 1 a 1.346), parte é trecho da rua que ficou fora
  da sessão.
- **Nome genérico de logradouro não foi tratado em geral.** `RUA SEM DENOMINACAO`
  é usado como identidade em `_nome_da_face` e `_tolerancia_por_endereco`
  também; aqui só o teto de distância o contém.
- **Reprovado por paridade com a face certa longe.** Sobraram 636 pontos a 2,0 m
  de uma face da própria rua, reprovados porque a numeração é do outro lado.
  Destes, 540 têm em algum lugar uma face da mesma rua com a paridade certa, mas
  a **mediana da distância até ela é 34,3 m** — é outro quarteirão. Só 229 estão
  a menos de `RAIO_FACE_M`. Os outros 173 não têm nenhuma face daquela rua com
  aquela paridade. **A causa não foi isolada**: pode ser cobertura de paridade
  (a face de frente também não tem) ou a coleta do passo 4 não alcançar.
- **A régua ancorada em min/max é frágil a outlier** — é hoje a maior causa de
  ponto na perpendicular em TODA a análise (7.003 de 9.941). Ancoragem robusta
  (Theil–Sen já dá o sentido; falta usá-lo para a inclinação) resolveria os dois
  casos acima. **Não implementado.**
- **`span = 0` empilha os pontos** em vez de mandá-los para a perpendicular.
- A perpendicular global subiu de 8.239 para 9.941. Parte é volume (1.104 pontos
  a mais), mas a proporção sobre os aprovados também subiu, de 46,5% para 50,5%
  — **a parcela que não é volume não foi isolada.**
- 1.392 das 1.492 faces de via aberta não têm **nenhum** endereço numerado: são
  becos e acessos sem cadastro. Não é defeito, é o dado.
- **103 dos 122 reprovados de esquina** seguem reprovados (ver seção 17).
- O botão azul do mapa roda os passos **1 a 6**; telhados (7) e casamento (8) são
  à parte, por custarem ~70 s contra 2–4 s de todo o resto.
- Custo: a área inteira de Itambé nos passos 2–6 levou **555 s** contra ~227 s
  antes — o preço de 1.492 quadras a mais (o passo 4 colhe 41.003 linhas e o
  expurgo de duplicados remove 25.532).

---

## 20. Os DOIS caminhos de POI — e como a captura virou o motor do painel

Descoberto ao investigar "a mineração não acha POI nenhum". **Existem dois
processos de POI na pasta, e só um está ligado ao painel.**

### O principal: captura + OCR (roda FORA do painel)

`src/capture.ts` — TypeScript com Playwright. É o que o usuário chama de processo
principal: **10 workers paralelos**, um mapa do Google carregado por worker e
reposicionado por coordenada (em vez de recarregar o mapa a cada ponto), usando um
Map ID com estilo vetorial limpo — **sem nomes de rua, só os markers de POI** —
para o OCR ter o que ler.

```
const WORKERS      = 10;
const MAPS_MAP_ID  = '33696f50cbe8e2d228094f61';  // mapa_pois, estilo clean
const TILE_WAIT_MS = 6000;                        // espera os labels aparecerem
```

Pipeline: `src/capture.ts` → `detect_crops.py` → `ocr_pois.py` (EasyOCR) →
`capturas/<sessão>/crops/ocr_resultado.json`. A pasta `capturas/` é SAÍDA desse
processo — não é lixo de run antiga.

**Ele nunca esteve ligado ao servidor.** É executado por linha de comando.

> ⚠️ **A chave saiu do código em 07/08/2026** — hoje vem só do `.env`
> (`MAPS_JS_KEY`, com `MAPS_API_KEY` de alternativa), e a captura **para na hora**
> se não achar nenhuma, em vez de gerar centenas de PNGs cinzentos. Mas a chave
> antiga **continua no histórico do git** (commit `1c7f081`): tirar do código não
> desfaz o que já foi publicado, então **ela precisa ser girada no console do
> Google** e a nova posta no `.env`.

### O do painel: Places API (último recurso)

O botão **Mineração de área** chama `minerar_area.py`, que usa a **Places API
paga** (`nearbysearch` + `details`). Depende de `MAPS_API_KEY` no `.env`.

**Em 04/08/2026 essa variável NÃO existe no `.env`** — por isso a mineração
retornava "4 células, 0 POIs, 0.0 min": sem chave, cada célula chama a URL com
`key=` vazio, o Google recusa na hora e o script segue em silêncio, sem erro.

Conferido no histórico: a linha que dispara `minerar_area.py` tem só **dois
commits** — o original da v2.0 e o de 04/08, que trocou o argumento da área de
caminho de arquivo para nome no banco. **O painel sempre chamou o Places**; nada
foi desconfigurado ao construir quadras/telhados.

### E o terceiro, que não é minerador

`minerar_web.py` é **recuperação de resíduo**, não varredura: pega POIs que já
falharam no Maps e busca no Yahoo (Playwright + proxy), OpenAI e BrasilAPI. Roda
sobre um JSON de coleta que já existe.

### A captura virou o motor do painel (04/08/2026)

A aba **Mineração de área** passou a rodar a captura por padrão. O seletor
**Motor** tem duas opções; a Places continua lá, agora como escolha explícita:

| motor | script | custo |
|---|---|---|
| **captura + OCR** (padrão) | `minerar_captura.py` | só tempo |
| places | `minerar_area.py` | pago, e **recusado pelo servidor** se `MAPS_API_KEY` não estiver no `.env` |

O erro em vez do silêncio é o ponto: antes, sem chave, o job terminava "com
sucesso" e 0 POIs.

**`minerar_captura.py`** encadeia quatro estágios, cada um retomável
(`--de N --ate M` roda uma fatia):

```
1 captura   src/capture-cli.ts  → capturas/<sessao>/*.png
2 recortes  detect_crops.py     → crops/*.png
3 OCR       ocr_pois.py         → crops/ocr_resultado.json
4 busca     search_pois_v2.py   → crops/search_resultado.json
                db_export       → crops/<sessao>_db.json   ← é este que o watcher lê
```

**`src/capture-cli.ts`** existe porque o `src/index.ts` é interativo: um
subprocess do servidor não tem quem responda "Iniciar captura? [s/N]" e ficaria
pendurado para sempre. Recebe tudo por argumento e imprime `— N células`, que é
a linha que o painel lê como total. Tem `--so-contar`, que só estima.

Três coisas que o caminho do painel exigiu e que o de linha de comando escondia:

1. **A área é POLÍGONO, não caixa.** `generateTiles` agora descarta o tile que
   não encosta na área desenhada (`CaptureConfig.polygon`). Sem isso a captura
   de um município paga tiles de mato — a caixa envolvente é muito maior que o
   desenho. O teste do tile é retangular (centro, 4 cantos e vértices do
   polígono dentro dele), então escapa só o sliver que atravessa sem vértice nem
   canto dentro; área desenhada à mão não produz isso.
2. **UTF-8 nos filhos.** Ligado a um PIPE em vez de um console, o Python do
   Windows escolhe cp1252 e o primeiro emoji derruba o processo:
   `detect_crops.py` morria com `UnicodeEncodeError` no PRÓPRIO CABEÇALHO, antes
   de detectar nada. Quem importa `config` chama `forcar_utf8()` e escapa; nem
   todo script importa. Resolvido no ambiente (`PYTHONIOENCODING=utf-8`), que
   cobre qualquer filho.
3. **O resultado da busca não está no formato do ingester.** `search_pois_v2`
   grava o POI ANINHADO (`{"poi": {...}, "status": ...}`) e tanto o
   `realtime_ingest` quanto o `src/ingest.ts` procuram `nome`/`maps_lat` no
   TOPO. Sem achatar, o watcher leria o arquivo, não acharia `nome` em registro
   nenhum e pularia todos **em silêncio**. Quem achata é o
   `db_export.normalizar_item`, que já existia para o caminho de terminal — o
   `minerar_captura` o chama **de 10 em 10 s durante a busca**, para o marcador
   cair no mapa ao vivo, e uma vez no fim.

A barra do painel é alimentada por tradução: o orquestrador repassa o stdout dos
filhos e acrescenta `célula X/Y` (captura) ou `POIs X/Y` (busca), que são os
formatos do `_thread_logs`. E o `_thread_logs` passou a **reiniciar `feitos`
quando o total muda**: job de várias fases troca de escala no meio, e o `max`
antigo travava a barra no fim da fase anterior.

### Medido (1 tile, Canoas/RS, 04/08/2026)

| estágio | resultado |
|---|---|
| captura | 1 tile 3840×2160, 802 KB, ~40 s com 1 navegador |
| detecção | **43** ícones recortados |
| OCR | 43 nomes, ~4 s (alguns tortos: "Prorfiessa de Deus Livrhria Evangelica") |
| busca | 41 processados → **33 match válido**, 6 não encontrados |
| normalização | **35 POIs**, 96 fotos, 26 comentários |
| gate de área | 26 passam no filtro do ingester, **9 dentro do polígono** |

A chave da Maps JavaScript API **funciona** — a captura saiu no estilo limpo, só
markers, sem nome de rua, que é o que o OCR precisa.

### O primeiro run real pelo painel revelou dois contadores mentindo

Sessão `mineracao_20260804_1516` (Canoas): 41 processados, 31 POIs encontrados,
**12 gravados**. Parecia falha do gravador; não era. A conta fecha exata:

| | |
|---|---:|
| recortes com OCR que a busca processou | 41 |
| POIs distintos encontrados no Maps (`place_id`) | **31** |
| **fora do polígono desenhado** → recusados pelo gate de área | **19** |
| dentro → gravados | **12** |

Os 19 são inerentes ao método: um tile de zoom 19 cobre **1,14 × 0,74 km** e a
área desenhada era uma faixa estreita. O `capture-cli` filtra TILES pelo
polígono, mas um tile que encosta na área fotografa POIs muito além dela — e o
gate faz o certo ao descartá-los. Para aproveitá-los, desenhe a área maior.

**Bug 1 — "fora da área 0".** O watcher classificava pelo `status` do registro,
que é `"ok"`: o POI FOI encontrado no Maps, ele só não é dali. O veredito
`fora_da_area` vem do retorno de `ingerir_registro` e era descartado. Agora o
watcher acumula as chaves recusadas (`fora_keys`) e as soma à categoria. Sem
isso a única leitura possível do painel era "o gravador está quebrado".

**Bug 2 — o POI da captura caía em "Outros".** `origemDe` tinha um caso especial
mandando `fonte === "pipeline"` para o balde cinza. Agora existe o chip
**Captura + OCR**, e ele vem ANTES de "Maps direto" na lista: o POI da captura
também tem `status === "ok"`, então o teste de lá o pegaria primeiro.

**E os chips zerados não eram bug nenhum:** `poisBase()` devolve `[]` enquanto
nenhum município estiver selecionado — é o "Clique num município no mapa" do
topo. Com Canoas selecionado, os mesmos 12 POIs aparecem como *Captura + OCR
12 · Com telefone 8 · Com foto 9 · Sem telefone 4*. Telefone e foto vêm da
própria busca; **CNPJ e Street View ficam em 0 até rodar o enriquecimento**.

### Em aberto

- **Girar a chave do Google.** Ela saiu do código para o ambiente
  (`MAPS_JS_KEY` ou `MAPS_API_KEY`), **mas o literal antigo continua como
  fallback** em `src/capture.ts` para não quebrar hoje — e está exposto no
  histórico do repositório. Ao girar: nova chave no `.env`, literal apagado.
### O detector só via ícone COLORIDO (corrigido em 04/08/2026)

O usuário circulou no mapa uma dúzia de POIs que a mineração não pegou —
Mecânica Chibiaque, OctopusLog, Ângelo Pedroni, Muzymed, Sindiconstrupolo,
Potter Motors Racing… Rastreados na cadeia, **nenhum tinha chegado ao OCR**: o
`detect_crops` não os detectava.

A causa é a máscara: `COLOR_RANGES` tem 8 faixas de matiz e **todas exigem
saturação ≥ 60**. O ícone que a MAIORIA dos comércios tem no Maps é o genérico —
circulozinho cinza-claro com um ponto escuro, saturação ~0. Ele nunca entrava na
máscara, o `HoughCircles` não tinha o que achar, e a guarda
`best / roi_size < 0.08` o descartaria de qualquer jeito. As cores dos 43
detectados confirmam: azul 35, laranja 6, roxo 2, **cinza zero**.

Medido no tile de Canoas por template matching:

| | |
|---|---:|
| ícones coloridos (o que o detector via) | 43 |
| ícones genéricos no tile | 97 |
| **cobertura** | **38%** |

**A correção é uma segunda passada por template** (`detect_genericos`). O desenho
do ícone é sempre o mesmo — o estilo do mapa é fixo pelo `MAPS_MAP_ID` e a
captura é sempre 3840×2160 —, então `TM_CCOEFF_NORMED` contra
`assets/icone_poi_generico.png` (27×27, recortado de um tile real) resolve. Os
achados são ordenados pela CORRELAÇÃO, não pela varredura, para o vencedor de
cada aglomerado ser o melhor casamento; e são descartados os que caem a menos de
`DEDUP_PX` (22 px) de um ícone já detectado — o ícone colorido também tem anel
claro e casa com o template, e sem isso o mesmo POI viraria duas buscas no Maps.

Resultado no mesmo tile, cadeia inteira:

| | antes | depois |
|---|---:|---:|
| ícones detectados | 43 | **102** (59 genéricos, 0 duplicados) |
| recortes com nome legível | 43 | **99** |
| match válido no Maps | 33 | **86** |
| POIs distintos | 31 | **82** |
| **dentro do polígono** | 12 | **27** |
| fotos · comentários | 96 · 26 | **210 · 68** |
| não encontrados · OCR curto | 6 · 0 | 8 · 4 |

**2,2× mais POIs aproveitáveis**, e o refugo quase não cresceu — 8 não
encontrados e 4 de OCR curto em 102. Dos POIs que o usuário circulou no mapa, 14
de 15 passaram a entrar; o 15º não está neste tile.

> ⚠️ **O template depende do estilo e da resolução.** Mudou o `MAPS_MAP_ID` ou o
> viewport da captura, tem de ser refeito — é um recorte 27×27 em volta de um
> ícone genérico qualquer de um tile novo. Faltando o arquivo, o detector avisa e
> segue só com as cores.

⚠️ **Isto multiplica o custo do estágio 4**: cada ícone detectado é uma busca no
Maps de ~5 s. De 43 para 102 por tile, o tempo da busca dobra e meio. Captura,
detecção e OCR continuam baratos (9 s e 20 s no tile de teste).
- O OCR erra nomes ("Excdlerite", "Informer ificos 02026 Google" — este último é
  o texto de copyright do mapa). A busca no Maps absorve parte disso, mas o
  crédito do rodapé não deveria virar candidato.
- Os estágios 2 e 3 rodam **sem paralelismo**; num município inteiro serão a
  próxima fila de espera depois da captura.

---

## 21. O save que derrubava a rodada (06/08/2026)

Rodada de enriquecimento morta aos 18 minutos, com 437 de 7.567 POIs:

```
File "enriquecer_tudo.py", line 457, in salvar
    tmp.replace(out_json)
PermissionError: [WinError 5] Acesso negado
```

Todo coletor grava num `.tmp` e renomeia por cima do `.json`, para o leitor nunca
ver meio arquivo. No Windows esse rename é a parte frágil: `os.replace` sobre um
arquivo **aberto por outro processo** devolve WinError 5, porque o `open()` do
Python não pede `FILE_SHARE_DELETE`. E há sempre outro processo lendo — o watcher
do server abre o mesmo JSON **a cada 2 segundos**.

Era corrida de milissegundos, mas a exceção subia do `salvar()` → saía do `_um()`
→ saía do `gather` → matava o `run()` inteiro.

**`io_atomico.py`** resolve com duas garantias: a troca é repetida enquanto o
leitor segura o arquivo (~3 s contra uma leitura de milissegundos) e, se ainda
assim falhar, **o save é pulado e a rodada continua** — o payload é sempre
completo, nunca um delta, então o próximo save regrava tudo.

Aplicado nos três coletores que tinham a mesma troca: `enriquecer_tudo.py`,
`minerar_web.py`, `minerar_area.py`. Testado com um leitor batendo em laço
fechado: 40 saves, 40 bem-sucedidos.

> Nada se perdeu naquela rodada: a fase Web grava **direto no banco**
> (`_ingerir`) antes de chamar o `salvar()`. O JSON é espelho para o mapa ao vivo.

---

## 22. CNPJ: estrutura aceita, o resto é confiança (06/08/2026)

Regra do usuário, depois de perder dado por similaridade de nome:

> "o critério de aceitação é simplesmente ter a estrutura ou quantidade de
> caracteres de um cnpj; ser da base nacional e estar no município em questão são
> apenas critérios de nível de confiança"

Coluna nova **`pois.cnpj_conf`**, com nota e critério:

| nota | significado |
|---|---|
| `4/4 dv+base_nacional+uf+municipio` | dígito verificador + existe na Receita + UF e município conferem |
| `4/4 receita_local+endereco+nome` | achado na base local pelo endereço, com nome batendo |
| `2/4 receita_local+endereco_unico` | endereço com **uma empresa só** — não havia o que decidir |
| `0/4 so_estrutura` | só tem forma de CNPJ (o que a IA inventou cai aqui) |

### A fase 2 deixou de descartar por nome fraco

Endereço identifica o PRÉDIO, não a loja. Com duas ou mais empresas ativas na
mesma porta, escolher pelo nome com score 0,4 é sorteio — esses seguem para a
web. **Com uma empresa só, não há o que decidir**: o CNPJ entra com a confiança
registrada. Medido em Canoas, dos 3.850 recusados por nome:

```
1 empresa ativa na porta   2.025   <- sem ambiguidade nenhuma
2 empresas                   839
3 ou mais                  1.088
```

Exemplos que estavam sendo jogados fora — `Crazy Som` x `CRAZY COMERCIO E
LOCACAO LTDA` (0,33), `Igreja Luterana Emanuel` x `CONGREGACAO EVANGELICA
LUTERANA EMANUEL` (0,71, perdia por **um centésimo**).

Resultado da mudança em Canoas: **6.345 CNPJs num passo, sem rede** (2.283 por
nome + 4.062 por endereço único).

`cnpj_local.py` ganhou linha de comando própria:

```bash
.venv/Scripts/python cnpj_local.py --cidade Canoas --aplicar
```

---

## 23. Cruzamento com planilha externa e cadastro do cliente (06-07/08/2026)

### Planilha de POIs de fora (Overture/OSM)

16.123 linhas de Canoas cruzadas com o banco. Casamento do mais forte ao mais
fraco: telefone único dos dois lados -> CEP+número+nome (>= 0,72) ->
coordenada+nome. **3.475 POIs enriquecidos** (só campo vazio; nada sobrescrito) e
**12.535 importados** como POIs novos, com `place_id` sintético `planilha:<id>`
para dedup exata na reingestão.

Duas armadilhas que só apareceram medindo:

- A coluna `instagram` da planilha é **93% Facebook** (15.051 de 16.123) e só 22
  são Instagram; e 973 perfis de Instagram estavam na coluna `site`. Gravar
  coluna-a-coluna encheria o campo `instagram` de páginas do Facebook — e o
  painel renderiza aquele campo como `@handle`, tirando o último trecho da URL.
  **O destino sai do domínio, não do nome da coluna.** Coluna nova `pois.facebook`.
- CEP+número a 0,55 de similaridade casava `Sapataria Santos` x `Audácia Jeans`
  e `Bourbon Hipermercado` x `Zaffari` (telefone do shopping, repetido em sete
  lojas). Corte subido para 0,72, e telefone repetido de qualquer lado passou a
  exigir nome concordante.

### Endereço no formato do Maps

`endereco_completo` da planilha vem como `Rua Humaitá, nº 1258, Canoas` — sem CEP
(coluna própria) e com "nº" entre a vírgula e o número. O `chave_endereco`, que é
quem acha o CNPJ na Receita, procura exatamente CEP e número-depois-da-vírgula:
os 12.535 importados entrariam **cegos para a fase 2 tendo CEP e número em mãos**.
Passaram a ser montados como `Rua Humaitá, 1258 - Bairro, Canoas - RS, 92025-340`.

### `cadastro_cliente` — a carteira de imóveis da empresa

Tabela nova, alimentada pela aba **Importar planilha** (com download de modelo,
prévia de amostra em modal e confirmação antes de gravar). Cada imóvel recebe uma
flag no cruzamento com os POIs:

| flag | significado |
|---|---|
| `ja_cadastrado` | comercial na base do cliente — **não visitar** |
| `reclassificar_alta/media/baixa` | não é comercial na base, mas há POI; o nível segue a confiança do CNPJ |
| `sem_poi` | imóvel da base sem POI correspondente |
| `novo_comercial` | POI que não está na base — acresce à carteira |

---

## 24. SearXNG no i9 como principal (07/08/2026)

`SEARXNG_URL` passou a aceitar **várias URLs separadas por vírgula, em ordem de
preferência**. A consulta desce para a seguinte quando a primeira falha ou limita:

```
SEARXNG_URL=http://100.115.117.49:8888,http://localhost:8888
```

O i9 é servidor e fica sempre ligado; o WSL local é reserva. Instalação lá: WSL
Ubuntu + `uv` + clone do repo + `settings_local.yml` com `formats: [html, json]`
e `bind_address: 0.0.0.0`, `run.sh` como lançador e tarefa agendada `ONLOGON`.

> Duas pegadinhas do WSL remoto: heredoc por `ssh -> wsl` chega mutilado (use
> `scp` para `/mnt/c/...` e copie de lá dentro do WSL), e o WSL só é alcançável
> de fora com `netsh portproxy` + regra de firewall apontando para o IP do WSL.

---

## 25. Painel: o que cada número mede (06-07/08/2026)

Três correções de placar. Todas nasceram do mesmo erro: **mostrar o número certo
debaixo da palavra errada.**

**A fase Street View não escreve no JSON** — grava a foto direto em
`streetview_imgs`. O watcher, que alimenta os cartões lendo o JSON, não tinha o
que contar e congelava no placar da fase Web: a barra anunciava "2.229 de 21.701"
juntando o resultado de uma fase com o total de outra. Agora a fase se anuncia
(`⟦fase⟧ streetview`), as categorias zeram na virada, os números saem do próprio
log da captura e os rótulos viram **Fachadas capturadas · Sem panorama**.

**"Sem match" só existe onde houve busca de POI.** É derivado
(`processados - sucessos`); no download de imagens a barra conta FOTOS e o
watcher não tem POI nenhum, então todo processado virava "fracasso" — daí os
4.250 sem match do nada. Passou a depender também do MODO, não só da fase.

**O resumo do log só fala das fases que rodaram.** "Ficaram completos: 0" numa
rodada só de fachadas era verdade aritmética lida como fracasso: aquele contador
mede o carente de Maps/Web, que a fase 4 não toca.

### Fase 4 só nos pobres

Opção `--sv-so-pobres` (caixa no painel, marcada por padrão): fachada só em POI
**sem foto, sem telefone ou sem avaliação**. Critério de propósito diferente do
"carente" das fases Maps/Web — aquele inclui "sem CNPJ", e depois que a Receita
local preencheu milhares isso não separa mais ninguém.

### Aba Dashboard

Cidades à esquerda, cards à direita, filtros por grupo. Esconde todo o resto do
painel (área de trabalho, execução, processo atual, busca, chips, log): é tela de
leitura, e os controles de operação ali sugeriam que o "Iniciar processo" rodava
o que estava sendo exibido.

**Faixas de qualidade** (exclusivas — cada POI numa só, então a soma fecha com o
total da cidade) alimentam o card de **retorno direto**, onde o usuário digita o
valor de cada faixa, as deduções, e vê o líquido.

**Custo em três naturezas que não se somam**: gasto variável (LLM por POI),
assinaturas (Webshare US$ 45 + IA, editável) e o cenário Google. Ratear a
mensalidade do proxy pelas horas usadas dava "US$ 1,68" e sugeria que rodar mais
sairia mais caro — é o contrário: a mensalidade é a mesma, então quanto mais
roda, menor o custo por POI.

> **Armadilha SQL que zerou duas faixas:** `p.cnpj <> ''` devolve **NULL** quando
> o campo é NULL, e a exclusão das faixas seguintes usa `NOT (...)`. `NOT NULL` é
> NULL e a linha some — a soma das faixas batia 8.447 num total de 21.700.
> `coalesce` em todo campo de texto.

## 26. Resgate do resíduo: o que o pipeline não resolveu (23/08/2026)

**Documento próprio: [`docs/RESGATE-POI.md`](docs/RESGATE-POI.md).**

A mineração em massa acerta a maioria e deixa um resíduo — `nao_encontrado`,
`encontrado_divergente`, sem CNPJ, sem telefone, sem horário. Esse resíduo é
tratado **no fim do processo geral**, um a um ou em lote de até 20, pelo agente
com ferramentas (`agente_local.py`, interface em `chat.html`).

Por que não escala em regra fixa: cada caso do resíduo precisa de **julgamento**.
O nome da placa não é o do registro ("Espetão Vancosty" x "Vancosty Comércio e
Distribuição"), o Maps guarda outro nome ("BussBier Chopp Para Festas" para
"Bussbier Cerveja Artesanal"), o endereço da Receita é do loteamento e o do Maps
é da via de acesso ("Quadra EE Dois, 01" x "Av. Dezessete de Abril").

Três decisões ficam com a IA, cada uma em **chamada isolada** — o mesmo padrão
do veredito visual do estágio 04:

1. **É o mesmo estabelecimento?** A régua de texto virou gatilho, não veredito.
2. **O CNPJ é deste ponto?** `consultar_receita` acha candidatos; `confirmar_cnpj`
   diz se o candidato é o ponto. Negativa é resultado, não falha.
3. **Mesma região basta.** Bairro igual ou CEP vizinho + ramo compatível
   confirma, mesmo com rua diferente.

O que fica com o CÓDIGO é comparar texto: ensinar a regra do bairro ao modelo
fez ele aprovar CNPJs de outros bairros dizendo "mesmo bairro". Ele não errou o
raciocínio; errou a leitura. Hoje a conferência é calculada e entregue como fato.

**O limiar de 0,90 do `nome_match` no pipeline não mudou.** Lá são milhares de
POIs sem revisão humana e uma chamada de IA por linha não paga. A segunda
opinião é do resgate, onde a conversa a custeia.

**Capacidade medida** (`prova_carga_chat.py N`): 5 em paralelo é o teto útil no
notebook — 413 s, ganho de 3,1× sobre a fila, 80-100% por campo. A 10 nada trava
e nenhum erro aparece no log, mas o Maps passa a não encontrar o
estabelecimento e o horário desaba de 80% para 10%. Quebra em silêncio.

## 27. Cadastur/MTur: a fonte que diz o que o Estado registrou (24/08/2026)

`cadastur.py` + skill `extracao-cadastur-mtur`. Documento de módulo em
[`docs/MODULOS.md`](docs/MODULOS.md).

**O que ele acrescenta que nenhuma outra fonte tem.** As demais dizem que existe
um comércio ali. O Cadastur diz *o que o Estado registrou que ali funciona* — e,
para meio de hospedagem, **quantos leitos**. Leito é consumo de água por
pessoa/dia; nenhuma outra fonte do sistema traz capacidade declarada. Medido em
Canoas: Intercity 162 UH / 206 leitos, Atrio 132/264, Canoas Parque 130/260.

### Quatro passos, e o quarto exige o terceiro

```
baixar  →  carregar o município  →  CRUZAR como qualquer base  →  gerar POI do que sobrou
```

O gerador **recusa** rodar sem o cruzamento. Se ele decidisse sozinho o que já
existe, seriam duas implementações da mesma pergunta — e a hora em que
divergissem seria a hora da duplicata.

Ele escreve por `realtime_ingest.ingerir_registro`, nunca por `INSERT` próprio: é
o ingestor que conhece a deduplicação por nome+coordenada, o merge não-destrutivo
e o resgate de fachada e foto já pagas.

### A coordenada, que o Cadastur não tem

A skill não geocodifica — endereço moderno é texto livre. A âncora entra nesta
ordem, e sem nenhuma delas a linha **não vira POI** e registra o porquê em
`sem_poi_motivo`:

1. **CNPJ** contra `cnpj_tratado` — documento igual, sem gradação.
2. **Cruzamento por endereço** com `cadastro_cliente`: para saneamento é a melhor
   coordenada que existe, a do imóvel que a companhia fatura.
3. **CNEFE** (acrescentada em 2209086) — as duas anteriores falham fora de
   município já trabalhado, que é justamente onde o Cadastur mais serve. Medido em
   Esteio, sem nenhuma cobertura de `cnpj_tratado`: **16 dos 17 pontos vieram só do
   CNEFE**, todos com precisão de porta. A origem fica em `fonte_dado`.
4. **Geocodificador** (seção 28), quando sobra só o texto.

Centroide de município mandaria alguém a campo no lugar errado — por isso não é
opção.

### `nv_geo_coord`: o piso é 2, e a razão é medida

O CNEFE declara a qualidade da própria coordenada. A tabela correta, do
`Dicionario_CNEFE_Censo_2022.xls` (a doc anterior descrevia 3 e 4 errado):

| | |
|---|---|
| 1 `ENDERECO_ORIGINAL` | colhida naquele endereço no Censo |
| 2 `ENDERECO_MODIFICADO` | apartamentos no mesmo número |
| 3 `ENDERECO_ESTIMADO` | não havia original, ou era inválida |
| 4 `FACE_QUADRA` | a face da quadra, não a porta |
| 5 `LOCALIDADE` | a localidade |
| 6 `SETOR_CENSITARIO` | centroide do setor |

O piso continua em 2, mas **por motivo medido**: nas três cidades carregadas os
níveis 1 e 2 cobrem de 97% a 99% dos endereços; 3 e 4 somados ficam abaixo de 1%,
o 5 não aparece e o 6 aparece duas vezes em 176 mil. Aceitar 3 e 4 resgataria
**zero** prestador — quem falha, falha por endereço sem número, rua abreviada ou
número indexado dentro de condomínio, não por nível. O piso não custa cobertura.

### Atualização incremental — o botão ↻

A skill já tinha metodologia: cache por `recurso_id` revalidado por SHA-256 e
`eventos_entidade.csv.gz` classificando cada entidade em ENTROU / ALTEROU /
PERMANECEU / SAIU. Faltava o nosso lado usar — a carga regravava as 388 mil linhas
a cada execução. Hoje o botão encadeia:

1. baixa só o trimestre que mudou;
2. grava só ENTROU e ALTEROU;
3. quem SAIU ganha `saiu_em` e **não é apagado** — sumir do arquivo do MTur
   significa ter perdido regularidade, e isso pode ser fechamento, troca de dono ou
   renovação atrasada. Apagar destruiria o sinal na hora em que ele aparece;
4. cruza, gera POI do que sobrou, **cruza de novo** (agora com os pontos novos — é
   essa segunda passada que os liga ao cadastro de imóveis do cliente) e enriquece
   só eles.

O `--poi-ids` do enriquecedor é o que torna o passo 4 viável: sem ele, alcançar 22
POIs novos significaria varrer os 31 mil do banco.

### Duas armadilhas que a medição pegou

1. **Nem todo conjunto é atualizado.** Doze estão em 2026T2, mas `parque-tematico`
   e `parques-aquaticos` pararam em 2024T4. Um `--desde 2026` fixo os perderia em
   silêncio — e parque aquático é o maior consumidor de água da lista. O download
   busca o mais recente **de cada um**.
2. **O endereço do Cadastur não tem separador nenhum.** Lido pelo parser do Maps,
   virava tudo "nome da rua", e a base entrava no cruzamento com zero chave de
   endereço. Com o município como âncora, 5 de 6 passaram a formar chave.

### LGPD: pessoa física é contada, não guardada

Guia de turismo é cadastro de pessoa física — CPF, nascimento, nome social, tipo
sanguíneo. **Nenhuma linha entra**; a tabela não tem coluna para CPF. Mas o TOTAL
entra em `cadastur_total_pf` e vira card informativo: contar não identifica
ninguém, e ignorar o conjunto inteiro jogava fora informação de mercado junto com o
dado pessoal.

A lista de conjuntos é **positiva** — as 14 de pessoa jurídica, nomeadas. Com lista
negativa, um cadastro de pessoa física criado amanhã entraria sozinho.

### Comandos

```bash
.venv/Scripts/python cadastur.py --listar
.venv/Scripts/python cadastur.py --uf RS --municipio Canoas --so-carregar
.venv/Scripts/python cadastur.py --uf RS --municipio Canoas --gerar
.venv/Scripts/python cadastur.py --uf RS --municipio Canoas --encadear   # o ciclo fechado
```

Medido: Cachoeirinha, 25 prestadores → 17 POIs novos, 8 sem coordenada declarados.
Rodar de novo gera zero.

> **De quebra:** o `*.sql` do `.gitignore` nasceu para dumps e levava junto as 26
> migrações. Nenhuma estava versionada — a história do schema vivia só no disco.
> Entraram no repositório em `f3024c0`.

## 28. A coordenada passou a ter procedência declarada (24/08/2026)

`geocodificar.py` · `conferir_coordenadas.py` · migração 0028 ·
`tests/test_precisao_coordenada.py`.

### O problema

Os POIs tinham coordenadas de origens muito diferentes e **nada dizia qual era
qual**. Conferido no código de ingestão de cada fonte:

- 13.216 tinham o pin do próprio Google — `porta`, ~15 m;
- 300 da extração estadual tinham `maps_lat` preenchido, mas com o **centroide do
  Overture**. Parece pin e não é — foi essa descoberta que motivou tudo;
- 177 do `ia_fachada` carregavam a coordenada do POI **vizinho**;
- 13.324 vieram da planilha do cliente e ninguém conferiu.

### O vocabulário

Cada POI declara `coord_precisao` e o raio que essa classe promete:

| classe | raio | quem ganha |
|---|---:|---|
| `porta` | 15 m | quem foi encontrado como **estabelecimento** (pin do Google) |
| `porta_aprox` | 40 m | casamento de texto que achou o prédio |
| `via` | 150 m | achou o logradouro |
| `bairro` / `municipio` | 800 m / 5 km | **recusados** — ver abaixo |
| `desconhecida` | — | veio da planilha e ninguém conferiu |

**`desconhecida` não significa ruim**, e a tela diz isso com todas as letras.
Confundir as duas coisas faria descartar milhares de pontos que podem ser ótimos.

**Casar texto nunca vale `porta`.** Mesmo quando o Photon responde `type: house`, o
resultado é `porta_aprox`: ele achou *uma* casa naquele logradouro, não
necessariamente o número pedido — e a diferença entre as duas coisas é exatamente o
que manda alguém tocar a campainha errada.

A precisão aparece na ficha de todo POI (pelo `campo_catalogo`, sem tocar em HTML),
no payload do mapa e numa fileira própria de filtro. É a pergunta de quem monta rota
de campo: não "o que se sabe do negócio", mas **"o quanto se pode confiar no lugar"**.

### A cascata, na ordem de CUSTO

```
1. PHOTON      OSM no i9, tolerante a endereço sujo. 0,1 s.
2. NOMINATIM   OSM no i9, mais estrito. Segunda chance.
3. MAPS        abre navegador com proxy, ~30 s. ÚNICA fonte que dá PORTA,
               porque procura o ESTABELECIMENTO em vez de interpretar texto.
```

O Maps é o melhor e vem por último, e isso não é contradição: numa varredura de doze
mil endereços a diferença entre ele e o OSM é a diferença entre minutos e dias. O
barato tenta primeiro; o caro entra no resíduo, onde vale cada segundo. **Só depois
dos três é que existe "não encontrado".** Nos cinco casos que o OSM não resolvia, o
Maps achou os cinco.

### `via_confere` — o geocodificador confere a via que devolveu

Ao conferir os 12 mil POIs de planilha nunca verificados, os primeiros divergentes
pareciam dado ruim. Conferidos caso a caso, **em 4 de 5 quem errava era o
geocodificador**:

```
pedido "Rua Hipolito Jose da Costa"  →  devolveu "Rua Daniel Cruz da Costa"
pedido "Avenida Esperanca"           →  devolveu "Avenida Getulio Vargas"
pedido "Avenida Guilherme Schell"    →  devolveu "Agencia de Correios"
```

O Photon é difuso: casa por semelhança e devolve **outra rua com cara de acerto**.
Sem conferência, o ponto saía rotulado `via` — que promete "a rua certa".

A regra levou três calibrações contra dado real:

| versão | reprovação | por quê |
|---|---:|---|
| exata | 29,7% | "Aristides Stumph" × "Stumpf" é a mesma rua; grafia varia demais |
| + grafia | 24,9% | número de porta vazava para dentro do nome ("Santana 402 1a"); "Sete de Setembro" × "7 de Setembro" |
| + número fora do núcleo, numeral↔extenso, cobertura **nos dois sentidos** | 10,2% | o lado pedido chega sujo, o devolvido vem limpo |

14/14 nos casos conhecidos, os bons e os ruins. Os 204 POIs do Cadastur cuja via não
se confirma foram removidos e o prestador voltou à fila com o motivo.

### Distância NÃO prova erro. Município prova.

A primeira versão da varredura moveria tudo que estivesse a mais de 2 km do endereço
geocodificado. **Teria sido destrutivo**: a coordenada da planilha vem do cadastro do
próprio cliente e costuma ser boa. Hoje ela só move o que cai **fora do município que
declara** — e na varredura inteira isso foi **um caso em 11.961**.

A conferência de município é **geométrica**, contra o polígono do IBGE, com 2 km de
folga para divisa. Conferir por nome não bastava: três pontos passaram e caíram a
171, 270 e 499 km — o último era Sarandi do **Paraná**.

### O campo `cidade` não é autoridade

Essa lição custou dado. Foram apagadas 8 fotos e 3 análises de IA de três POIs,
comparando a coordenada contra o polígono do município que o campo `cidade` declara.
**As fotos estavam certas; o rótulo é que errava:**

```
Centro Distribuicao CORSAN   cidade=Esteio        CEP 92420 = Canoas
Camping Porto Batista        cidade=Triunfo       CEP 92330 = Canoas
MBK pousada                  cidade=Luis Correia  CEP 64200 = Parnaiba
```

Nos três, o pin do Google e o CEP concordavam entre si e discordavam do rótulo. **O
CEP é terceira fonte independente** — vem do CNEFE — e vale mais que um campo de
texto que qualquer etapa do enriquecimento pode ter escrito.

A trava passou a só acusar quando a coordenada discorda do rótulo **E** do CEP. E foi
provada: um POI foi estragado de propósito, ela reprovou; desfeito, ela passou. Ver
[[cidade-nao-e-autoridade]].

### Dois piso e três defeitos que a medição pegou

1. **Sem piso, o geocodificador virava centroide de cidade** — 117 pontos assim; em
   Encantado, quatro estabelecimentos na mesma coordenada. Piso em `via` (150 m): o
   pior ponto que ainda leva alguém ao lugar. Abaixo disso o prestador fica na tabela
   com o motivo e nenhum ponto entra no mapa.
2. **245 POIs foram rotulados `bairro`/`municipio`** porque o geocodificador só
   resolveu até a cidade. Estar "dentro da incerteza" de 5 km não prova nada, e o
   rótulo dizia que o ponto **é** um centroide — o que ele não é. Desfeitos, e a
   ferramenta passou a recusar confirmação que não seja de nível de endereço.
3. **O vínculo era gravado só no fim de `gerar`.** Uma execução estadual interrompida
   deixou 4.442 POIs sem nenhum prestador apontando para eles. Agora grava em lote de 100.

E dois na leitura de endereço, que valem também para o CNEFE: o complemento roubava o
número ("… 2586 LOJA 3 SALA 1" lia 1), e a notação de faixa da Receita ("de 3501 a
5101 - lado impar") virava número.

### Resultado

**5.608 POIs** tiveram a precisão elevada de `desconhecida` para a classe provada.
`desconhecida` caiu de 13.324 para 7.715. Ver a distribuição atual na seção 10.

```bash
.venv/Scripts/python conferir_coordenadas.py --cidade Canoas --amostra 200
.venv/Scripts/python conferir_coordenadas.py --corrigir --workers 8
```

## 29. Trabalho pago capturado no lugar errado (24/08/2026)

`identificar_divergente.py` · `tests/test_trabalho_no_lugar_certo.py`.

**O que estava acontecendo.** Uma foto de fachada é tirada **na coordenada** do POI.
Coordenada errada, foto de outro prédio — e a análise de IA que a lê julga um
estabelecimento que não é aquele. O veredito sai com cara de veredito e não vale nada.

A varredura achou **13 POIs assim**. Três eram pousadas do Piauí com **seis fotos e
uma análise de IA cada**, capturadas a até 20 km do próprio endereço. Ninguém tinha
como notar: a tela mostra a foto ao lado do nome, e nada dizia que as duas coisas não
se encontram.

### Três destinos, porque são três problemas diferentes

| | o caso | o que foi feito |
|---|---|---|
| **MOVIDOS** (6) | o endereço geocodifica **dentro** do município e o ponto cai fora → a coordenada é que erra | ponto corrigido, 21 fotos e 3 análises removidas, POI de volta à fila de captura |
| **ROTULADOS** (2) | o ponto está na cidade que o próprio **endereço** diz; quem erra é o campo `cidade` | ponto e fotos **preservados** — trocar aqui seria jogar fora captura boa |
| **MARCADOS** (5) | o OSM não tem aquelas ruas, então não dá para provar onde o ponto deveria estar | as 20 fotos e 3 análises saíram; a coordenada ficou onde estava, marcada `revisar_manual` |

O terceiro caso é o mais sutil: não está provado onde o ponto deveria estar, mas
**está provado que ele está fora do município declarado** — logo a foto é de outro
município e não pode ser a fachada daquele negócio. Mover para lugar nenhum é pior
que marcar.

**41 objetos órfãos apagados do Storage.** Nada de lixo pago para trás.

### O geocodificador aprendeu a ler os dois formatos

Dez dos treze não geocodificavam — não por falta de dado, mas porque `_consultas` só
conhecia o formato do MTur (sem separador) e aqueles endereços vêm no do Maps
(vírgula e traço). O leitor errado devolvia o endereço inteiro como nome de rua.
Agora tenta os dois.

### A trava

`test_trabalho_no_lugar_certo.py` reprova qualquer foto ou análise de IA numa
coordenada fora do município declarado. Contenção no polígono, com **2 km de folga**
para divisa — o mesmo número que `geocodificar` e `test_coerencia_local` usam, de
propósito.

**Estado final dos 14 POIs tocados:** 11 em `porta` (15 m), 3 em `via`. Todos com
fachada recapturada na coordenada corrigida — **47 fotos refeitas**.

---

## 30. O i9 depois do reboot: o que quebra em silêncio (24/08/2026)

`verificar_servicos.py` · `scripts/i9/` · `tests/test_coerencia_local.py`.

### O que aconteceu

As duas máquinas foram reiniciadas. No i9 os containers subiram todos `healthy` —
Supabase completo, `cr-referencia`, SearXNG — e Nominatim, Photon, OSRM e SearXNG
voltaram sozinhos. **Mas os dois bancos ficaram inalcançáveis de fora, e nada
acusou:** o `docker ps` dizia saudável.

A causa: no Windows, as regras `netsh portproxy` para o WSL existem e apontam para o
IP certo, mas ficam presas em **127.0.0.1** depois do boot. Quem escuta é o
`wslrelay.exe`, que só faz loopback. **Reiniciar o `iphlpsvc` não resolve — a regra
precisa ser REINSERIDA.** Ver [[reinicio-quebra-portproxy]].

### A tarefa agendada que reaplica sozinha

Roda como **SYSTEM**, porque `netsh portproxy` exige elevação e o boot acontece sem
ninguém logado. E SYSTEM **não enxerga a distro**: `wsl -d Ubuntu` pendura, e foi
exatamente onde a primeira versão ficou presa.

Os candidatos a IP do WSL saem da **tabela de vizinhos** do adaptador, e cada um é
**testado** numa porta que só o WSL serve (5443). Há entradas obsoletas de boots
anteriores ali — no teste havia `192.168.235.246` ao lado do `192.168.226.17` vivo,
**ambas marcadas `Stale`**. Escolher pelo estado seria chute, e chutar aqui manda o
tráfego para lugar nenhum sem erro nenhum.

Ela também **espera o Tailscale aparecer**: regra que escuta num IP específico só
amarra se aquele endereço já existir, e no boot o Tailscale sobe depois da rede.

E **confere no fim.** Regra escrita não é regra amarrada — essa distinção custou meia
hora de investigação. Se alguma não amarrar, sai com código 2 e diz quais. Provado
rodando: achou o WSL vivo, reaplicou as 14 regras, todas amarraram, código 0.

### `verificar_servicos.py` verifica FUNÇÃO, não porta

Porta aberta com serviço devolvendo lixo é o pior caso — tudo parece bem e o dado sai
errado. Então: o Nominatim **geocodifica** um endereço conhecido, o OSRM **traça** uma
rota, o Ollama **gera** uma palavra, o banco **conta** linhas. Quando falha, diz qual é
o caminho.

```
-- alcance --  i9 8/8 portas · DGX 1/2
-- bancos --   produto 31.255 POIs · referência CNEFE 111.102.875, malha 3.560 municípios
-- geo --      nominatim · photon · osrm carro · osrm a pé · searxng
-- llm --      3 modelos · gerar qwen2.5vl:7b · 100% na GPU
               7/7 verificações passaram.
```

### O teste de coerência, calibrado em duas classes

Ele media distância do **centroide** do município, com teto de 50 km. Isso errava nos
dois sentidos: reprovava ponto legítimo em município grande (Santa Vitória do Palmar
tem 5.244 km²) e deixava passar deslocamento de 40 km. Agora é contenção no
**polígono**, separando o que são coisas diferentes:

- **DESLOCAMENTO** (>25 km) — o ponto está noutro lugar. **Derruba a suíte.**
- **RÓTULO DE CIDADE** (até 25 km) — a coordenada está certa e o campo `cidade`
  errado. Canoas, Esteio e Sapucaia fazem divisa e o Maps atribui à vizinha; o
  "Zoológico Sapucaia do Sul" está gravado como Canoas. São 18, quase todos
  anteriores. Fica **pinado no número**, para não crescer em silêncio.

E ele **pula** quando o banco de referência está fora do ar, em vez de acusar defeito
de dado que não existe.

---

## 31. O sistema inteiro passou a falar a língua do painel (24/08/2026)

`frontend/tokens.css` · `tests/test_linguagem_visual.py`.

O front tinha **duas famílias de cinza** convivendo — a do Google (`#5f6368`,
`#e8eaed`, `#dadce0`) e a do Tailwind (`#0f172a`, `#94a3b8`, `#cbd5e1`) —, **dois
azuis** (`#1a73e8` e `#2563eb`) e uma cauda de **92 tons únicos**, cada regra com o
seu. Ninguém fez isso de propósito: foi uma tela de cada vez.

**`tokens.css` é o vocabulário**, lido do `:root` do painel que o usuário aprovou —
cor, tipografia, uma escala de raio, sombra tingida de navy. Mais o que o painel não
tinha e a medição cobrou: variante `-texto` de cada acento (o laranja dá 2,45:1 sobre
branco, metade do mínimo), tom `-vivo` para superfície escura, e tinta de estado
**sólida**.

O que mudou nas telas: cabeçalho **navy** com tudo dentro invertido junto; acesso em
tela cheia dividido ao meio, com arte em CSS puro; perfil em duas colunas (o que a
pessoa edita × o que só o root muda); **aviso virou cartão no alto à direita** —
embaixo ao centro ele caía sobre o que a pessoa acabara de clicar; tabela e lista de
usuários com só linhas horizontais; e o chat, única tela com acento verde, entrou na
mesma família.

**Medido, não estimado.** Auditoria de contraste em **796 elementos** com todos os
modais abertos: de 15 reprovações para **zero**. O `#B96A12` que o próprio painel usa
no chip de alerta reprovava (4,09:1) — por isso o arquivo **mede** em vez de copiar.

**A trava:** `test_linguagem_visual.py` deriva o vocabulário do próprio `tokens.css`,
então matiz novo no CSS da moldura quebra o teste. Cor que codifica **dado** (ramo,
fonte, face de quadra) segue no `app.js`, com a regra de que toda cor de chip leia com
branco por cima.

---

## 32. As quatro tabelas que nasceram sem política (24/08/2026)

Migração [`0029`](migrations/0029_rls_nas_quatro_que_ficaram_de_fora.sql) ·
`tests/test_isolamento_tenant.py`.

Ao medir o estado do sistema, quatro tabelas apareceram sem RLS:
`atribuicao_divergente`, `fachada_triagem`, `foto_maps_triagem` e
`ifood_merchant`. O `docs/estado.json` afirmava `tabelas_sem_rls: []`, e afirmava
isso desde 13/08.

**São as mesmas quatro da migração 0024**, que já as tinha pego por outro motivo
(escreviam e não liam). O padrão, então, não é descuido pontual: **tabela nova
não herda a política**. Quem cria tabela precisa escrever quatro linhas — enable,
force, policy, trigger — e quatro vezes isso não aconteceu.

### Por que ninguém tinha notado

`comercialradar_worker` e `comercialradar_root` têm `rolbypassrls`. O pipeline
inteiro, portanto, não sentia nada. Quem é submetido às políticas é
`comercialradar_app`, o papel do servidor web — ou seja, **era exatamente por ali
que o painel de uma empresa alcançava linha de outra**, nas quatro tabelas.

### O `tenant_id` do `ifood_merchant`

As outras três já tinham a coluna preenchida em todas as linhas. O
`ifood_merchant` não tinha em **nenhuma** das 1.598: ligar a RLS naquele estado
tornaria as 1.598 invisíveis para o painel, que é pior que o problema que a RLS
resolve.

As linhas vêm de uma raspagem única de 19/08 (1h21 de execução) sobre a região
metropolitana inteira, **sem vínculo a POI** e com CNPJ em 16 das 1.598. A
atribuição escolhida foi **por município** — cada linha para a empresa que mais
tem POI naquela cidade: 1.002 Aegea-Corsan, 596 Columbia Tech Info. Fica
registrado que é **decisão, não derivação óbvia**: o critério diz onde cada
empresa trabalhou, não de quem o dado é.

Uma linha tinha a cidade grafada `porto-alergre` e teria ficado órfã. A migração
corrige a grafia antes de cruzar e **aborta** se sobrar qualquer linha sem dono —
uma órfã com RLS ligada é uma linha que ninguém mais vê, e ninguém seria avisado.
Depois disso a coluna virou `not null`.

### A trava pergunta pela REGRA, não pela lista

Conferir as quatro por nome não impediria a quinta.
`test_nenhuma_tabela_com_tenant_id_fica_sem_politica` varre o schema: se a tabela
tem `tenant_id`, ela precisa ter RLS, policy **e** o gatilho `preencher_tenant`.

O gatilho importa tanto quanto a policy — sem ele a linha nasce com `tenant_id`
nulo, e nulo não casa com policy nenhuma: some do painel de todo mundo, sem erro
nenhum.

**Duas exceções, ambas de projeto:** `auditoria` tem escritor próprio
(`registrar_auditoria` já resolve a empresa — da sessão, e quando não há, da
própria linha auditada), e o `root` de `usuarios` não pertence a empresa nenhuma.

### O que o teste achou de quebra

**66 registros de `auditoria` com `tenant_id` nulo** — nenhum papel os enxerga.
São anteriores ao ajuste e **não foram retro-atribuídos**: trilha de auditoria
editada depois do fato vale menos que trilha incompleta.

**Estado final:** 28 tabelas com `tenant_id`, todas com política. 231 testes
passando, 49/49 na prova de ponta a ponta.

---

## 33. A área que a tela mostrava não era a que o coletor lia (24/08/2026)

`area_utils.salvar_area` · `server.py` (`_tenant_para_gravar`) ·
`frontend/app.js` · `tests/test_area_trabalho.py`.

### O sintoma

O usuário selecionou Cachoeirinha, desenhou **4 quadras**, a tela respondeu
**"Área salva ✔"** e o painel confirmou *"Área definida (4 vértices)"*. A
mineração então começou assim:

```
Área 'area_atual': 25 vértices · caixa N-29.85920 S-29.97150 L-51.10600 O-51.24960
Área: 25 vértices — 264 tiles dentro dela
Estimativa: ~4 min          →  na prática, ~80 min
```

Vinte e cinco vértices, e a caixa é a de **Canoas** — nem Cachoeirinha, nem o
desenho. A linha no banco era de **15/08**, nove dias antes.

### As duas metades, e por que o erro ficou mudo

**No servidor.** Dentro de uma requisição, `realtime_ingest.conectar()` devolve
a conexão do **usuário** (`auth.conectar_como`), e ela só declara
`app.tenant_id` quando o usuário tem empresa:

```python
if u.tenant_id:
    cur.execute("select set_config('app.tenant_id', %s, true)", (u.tenant_id,))
```

O **root não tem empresa** — é o único usuário assim, e é de propósito: ele
atravessa todas. Sem a variável, a trigger `preencher_tenant` não tinha o que
carimbar e o `NOT NULL` de `area_trabalho.tenant_id` recusava a linha. A rota
estourava **500**.

Provado nos dois sentidos, com a mesma requisição:

| quem | resultado |
|---|---|
| `root` | 500 · `null value in column "tenant_id" violates not-null constraint` |
| `admin.corsan` | 200 · 4 vértices salvos |

**No navegador.** `salvarArea` chamava `fetch` e descartava a resposta; quem
chamava anunciava sucesso incondicionalmente:

```js
await salvarArea(latlngs);
toast("Área salva ✔", "ok");   // sempre
```

Sozinho, o 500 teria aparecido na tela. Sozinha, a mentira do front não teria o
que esconder. **Foram precisas as duas** — e o custo não é o erro, é a rodada:
80 minutos fotografando o município errado.

> Isto não era só da área. **Todo INSERT em tabela com dono, feito pelo root,
> falhava do mesmo jeito** — a área foi só onde apareceu primeiro.

### A correção

`salvar_area` aceita `tenant`, declarado por `set_config(..., true)` — local à
transação, igual ao que o `auth` faz, morrendo no commit.

Quem resolve o valor é `_tenant_para_gravar()`, e ele **só responde para o
root**: para quem tem empresa devolve `None`, porque a conexão já declarou a
variável e reescrevê-la a partir da rota seria abrir a porta para gravar na
empresa do vizinho.

Para o root vale a **mesma convenção que os jobs usam desde 13/08/2026**:
`CR_TENANT_ID` do ambiente do servidor. Ser a mesma variável é o ponto — é o que
faz a área em que ele grava e a empresa em que a mineração dele escreve serem a
mesma coisa. Sem nenhuma das duas, a rota responde **409 com o motivo**, nunca
500: o 500 vem sem corpo, então a tela não teria o que mostrar nem que quisesse.

No front, `salvarArea` devolve se o banco aceitou, o `toast` de sucesso fica
atrás desse retorno, e **quando falha a tela volta a desenhar o que o banco
tem** — deixar o polígono na tela depois de a gravação falhar é exatamente o que
produziu o defeito.

### A trava

`tests/test_area_trabalho.py`, seis testes: o root salva; a rota nunca responde
500; o que foi salvo é o que volta; e — a metade do navegador — `salvarArea`
olha `r.ok`, devolve `true`/`false`, e o `"Área salva ✔"` depende disso.

---

## 34. A Places API saiu da ferramenta (24/08/2026)

Decisão do dono do produto. Ela cobrava por chamada e trazia o mesmo tipo de
ponto que a **captura + OCR** traz de graça.

- O `<option value="places">` saiu do seletor; sobraram as duas fontes
  gratuitas: **Captura + OCR** e **Extração estadual**.
- "Passo da grade" e "Coleta profunda" eram opções **só dela** e saíram junto —
  a captura varre por *tile*, não por célula de grade.
- A rota **recusa** `motor=places` com **410**, em vez de ignorar em silêncio.
  Uma aba antiga aberta no navegador dispararia a captura achando que pediu a
  Places: o job rodaria, o resultado seria outro, e nada diria por quê.
- `minerar_area.py` continua no repositório como histórico. O painel não o
  alcança mais.

---

## 35. O painel foi para o i9 e VOLTOU no mesmo dia (24/08/2026)

> **Desfecho primeiro, para quem lê com pressa:** o painel roda no
> **notebook**. A mudança para o i9 foi feita, medida e revertida — o
> Google recusa a chave quando o referrer muda. O que ficou no i9 é o
> trabalho **sem navegador**. O relato abaixo é do porquê.

`scripts/i9/publicar.sh` · `scripts/i9/painel.sh` · `.gitattributes` ·
`server.py` (bloco `__main__`).

Endereço de trabalho: **http://localhost:8765** (o de sempre).

### A decisão, e por que ela é MENOR que a alternativa

O pedido era "os jobs rodam no i9". O caminho óbvio era extrair o catálogo de
jobs de dentro do `server.py`, escrever um serviço no i9 que recebe
`modo` + `opções`, e resolver como o painel acompanha um arquivo que passou a
morar noutra máquina. Três peças novas, uma delas com superfície de execução
remota, e a definição de cada job existindo em **dois lugares que podem
divergir**.

Antes de escrever qualquer uma delas, uma medição: **`server.py` importa limpo
no i9**, sem nenhuma dependência faltando. Se o painel roda lá, todo job já é
subprocesso local — na máquina certa. As três peças deixam de existir.

### O ganho é medido

`verificar_servicos.py` rodado dos dois lados, no mesmo minuto:

| | notebook | i9 |
|---|---:|---:|
| banco do produto | 1,5 s | **0,2 s** |
| photon | 0,09 s | 0,03 s |
| osrm | 0,04 s | 0,00 s |

O banco ficou **7,5× mais rápido** só por deixar de atravessar a rede — é o
mesmo argumento que o `worker_rede.py` já tinha escrito para as ferramentas do
chat. E o notebook deixa de ser dono da rodada: fechar a tampa não mata mais uma
captura de 80 minutos.

Capacidade, remedida: **16 CPUs, 94 GB de RAM, 682 GB livres** (a doc anterior
dizia 51 GB — errado por mais de 10×). O notebook impunha teto de quatro
Chromiums.

### O desenho, em duas peças

```
server.py   127.0.0.1:8765, DENTRO do WSL. Não alcançável de fora.
caddy       :8443 com o certificado da Tailscale → repassa pelo LOOPBACK.
```

O padrão do bind continua `127.0.0.1`, e isso **é escolha**: o painel não tem
TLS próprio, então escutar em qualquer interface por omissão seria servir login
e dado de cliente em claro. `CR_HOST`/`CR_PORTA` existem e **avisam alto** quando
o endereço não é loopback.

Separar as duas é o que faz o tráfego em claro nunca tocar uma interface de rede.

**O certificado é Let's Encrypt de verdade**, não interno: `tailscale cert` emite
para o nome MagicDNS, e o `curl` devolve `ssl_verify_result=0` — nenhum navegador
reclama. A Tailscale roda no **Windows** do i9, então os arquivos entram no WSL
por `/mnt/c/ferramentas`.

### O alcance, e a lição de 24/08 aplicada

A regra `netsh portproxy` da 8443 entrou **também** no `reamarrar-wsl.ps1`. Sem
isso o painel responderia dentro do WSL e ninguém o alcançaria depois do próximo
boot — exatamente o modo de falha da seção 30, com os containers todos `healthy`.

Ela escuta em `100.115.117.49`, **não** em `0.0.0.0`: só o tailnet, nunca a LAN.

### Publicar

```bash
scripts/i9/publicar.sh              # código + ambiente
scripts/i9/publicar.sh --env        # também o .env (confirma antes, chmod 600)
scripts/i9/publicar.sh --so-codigo  # só os .py mudaram
```

O código sai do **índice do git**, não do working tree — publicar working tree é
como se descobre, em produção, que a correção que faltava era um arquivo nunca
commitado. E não é `git clone`: o repositório é privado e o i9 **não ganha
credencial de GitHub** só para isso.

### Quatro pedras do caminho, todas silenciosas

1. **`/usr/bin/env: 'bash\r'`** — o CRLF do Windows viajando dentro do shebang.
   A mensagem culpa o `bash`, que não tem nada com o assunto, e o `\r` é
   invisível em editor e em `cat`. Resolvido com `.gitattributes` (`eol=lf` para
   tudo que um Linux executa).
2. **O `ssh` engole o stdin de quem o chama.** A confirmação do `.env` era lida
   por `read`, e o primeiro `ssh` do script comia a resposta: a pergunta
   aparecia, ninguém respondia, o script seguia como se tivesse sido negado e o
   arquivo não chegava. `ssh -n` em tudo que não recebe dados.
3. **`--with-deps` do Playwright aborta antes de baixar.** Ele tenta o `apt`
   primeiro, esbarra na senha do sudo e desiste — deixando
   `~/.cache/ms-playwright` **vazio** depois de uma publicação que pareceu
   correr bem. As duas coisas foram separadas: o download roda sempre; a falta
   das bibliotecas vira aviso com o comando exato.
4. **`instalar_bancada.py` apontava para um caminho que não existia mais.** O
   zip do modelo tinha ido de `assets/modelo_frontend/` para
   `assets/skills_locais/`. No notebook nada acusou, porque o `bancada.html` já
   existia de antes — **arquivo movido não quebra quem tem o resultado; quebra
   quem for gerar de novo, meses depois.**

### Operar

```bash
ssh orbisgrid@100.115.117.49 "wsl -d Ubuntu -- bash -lc \
  '/home/orbisgrid/comercialradar/scripts/i9/painel.sh subir|parar|estado|log'"
```

### O desfecho: o painel VOLTOU para o notebook, no mesmo dia

**O referrer.** As chaves do Google são restritas por referrer, e o mapa do
painel manda a **própria origem** do navegador. Servido do i9, o referrer virou
`https://desktop-s8l7nat.tail7e301b.ts.net:8443` — fora da lista autorizada. O
Google recusa, e o mapa simplesmente não desenha.

Eu tinha considerado esse risco e o descartei **pelo motivo errado**: raciocinei
sobre a *captura*, que serve o `_map.html` em `127.0.0.1:8766` e por isso mantém
o referrer de sempre. Isso é verdade para a captura e irrelevante para o painel,
que é outra página, com outra origem. Duas coisas usam a mesma chave por
caminhos diferentes; conferir uma não conferia a outra.

Some-se o que o próprio uso mostrou: para **desenvolvimento**, publicar a cada
alteração é atrito puro. Editar e recarregar vale mais que 7,5× no tempo de
consulta ao banco.

### O que ficou, e é o desenho certo

O painel e a captura moram no notebook. **O trabalho sem navegador atravessa para
o i9** — que é exatamente onde o OSRM, o Photon, o Nominatim, o SearXNG e o
Ollama já vivem, pelo mesmo motivo.

O primeiro caso é a **extração estadual**, e é o mais pesado do processo: DuckDB
sobre o Overture no S3 mais o PBF do OSM, para uma UF inteira — 383 mil POIs no
RS, horas de CPU e dezenas de GB. Num notebook ela disputaria tudo com os dez
Chromiums da captura.

```bash
bash scripts/i9/dataset_estadual.sh --remoto RS   # produz no i9 e traz
bash scripts/i9/dataset_estadual.sh --baixar RS   # só traz, se já existe lá
```

E `minerar_tudo.py` **não produz o dataset nesta máquina por padrão**. Se ele
não existe, para e diz o comando — em vez de começar sozinho uma tarefa de horas
que ninguém pediu. `--produzir-bases` força, para quando não há i9 à mão.

### O que sobrou de útil da tentativa

Nada do trabalho foi perdido, porque a infraestrutura é a mesma que o trabalho
pesado usa:

- **O código publicado no i9** (`scripts/i9/publicar.sh`), com venv de 105
  pacotes e Chromium que abre — é o que faz a extração estadual rodar lá.
- **`.gitattributes`** com `eol=lf`: o `\r` do Windows quebrava *qualquer* script
  publicado, não só o do painel.
- **O bit de execução no índice do git** — mesmo caso.
- **`tests/test_scripts_i9.py`**, que cobra CRLF, bit de exec e crase em heredoc.
- **O `.env` no i9**, necessário para qualquer trabalho que toque o banco de lá.

Desfeito: o painel e o Caddy foram parados, a regra `netsh` da 8443 removida, e
a linha correspondente saiu do `reamarrar-wsl.ps1`. O certificado da Tailscale
ficou em `C:\ferramentas` — inofensivo, e já pago.

> **A lição, e ela é sobre método, não sobre máquina:** eu tinha a informação de
> que as chaves eram restritas por referrer, levantei o risco, e o descartei sem
> **medir** — testei o raciocínio contra o caminho errado. Uma chamada ao mapa do
> painel a partir do i9 teria custado trinta segundos e evitado a mudança inteira.

---

## 36. A base estadual do Brasil, e o dia em que ela rodou duas vezes (25/08/2026)

A fase 1 do processo — **extração** — produz a base de POIs de uma UF inteira a
partir das fontes públicas (Overture + OSM + Foursquare) pela skill
`extracao-poi-estadual`, e depois importa **por município** para o banco
(`extracao_estadual.py`). O trabalho é do i9: são horas de CPU e dezenas de GB
por estado.

### O que aconteceu

`dataset_brasil.sh` foi disparado **duas vezes**, às 10:18 e às 10:20, e as duas
cópias rodaram juntas por quatro horas sobre os mesmos diretórios. Em PE chegou
a haver dois `poi_estadual.py` no mesmo `--base-dir`.

**Nenhum dos dois processos errou.** O cache da skill é endereçado por hash de
escopo, então os dois concordavam sobre o caminho de cada arquivo e escreviam um
por cima do outro em silêncio. O que apareceu foi consequência, sempre longe da
causa:

| Onde | Sintoma |
|---|---|
| BA, SP | `.parquet.tmp -> .parquet: No such file or directory` na etapa `dedup` |
| SP | morto pelo OOM após 125 min — dois `dedup` disputando 94 GB, swap em 14/16 |
| RS | `validate` REPROVADO por auditoria, com o funil acumulando três passadas |

### O funil que acusava o que não era

A reprovação do RS por "todo descarte tem linha em rejeitados" era **do método de
contagem, não do dado**:

```
155.423×3 + (76.255 + 124.083 + 124.083) + 15×3 + 1 = 790.736   ← funil, 3 passadas
155.423   +  124.083                     + 15       = 279.521   ← rejeitados, 1 passada
```

São os dois números do relatório, na vírgula. O funil é append-only e somou as
reexecuções que a rodada dupla provocou; a tabela de rejeitados é da última
passada. **Fica em aberto** se a `fusão suspeita a 11%` (limite 2%) tem a mesma
origem ou é defeito real de dedup — as UFs produzidas com a trava respondem isso.

### A causa de terem sido duas: o disparo morria calado

Nenhum trabalho longo sobrevivia ao fim da conexão SSH, e nenhum avisava disso.
O OpenSSH do Windows derruba a sessão inteira quando a conexão fecha, e o WSL
leva junto os processos daquela invocação — `setsid`, `nohup`, `disown` e o
`Start-Process` do PowerShell **não mudam nada**. Medido com um `sleep 300`:
some no instante em que o ssh volta, sem escrever uma linha.

As rodadas que sobreviviam sobreviviam **por acidente**: há uma sessão
`orbisgrid` desconectada desde 24/08 segurando 14 `wsl.exe` de pé, e o trabalho
pegava carona nela.

Então o primeiro disparo morria em silêncio, quem disparou não tinha como saber,
disparava de novo — e as duas acabavam vivas.

**A solução (`scripts/i9/lancar.sh`):** o WSL do i9 roda systemd como PID 1, e
`systemd-run --user` cria uma unidade transitória cujo pai é o gerenciador do
usuário, fora da árvore do ssh. Conferido de sessão nova: continua `active` com
o log crescendo depois que a conexão que o disparou já fechou.

```bash
bash scripts/i9/lancar.sh brasil ./scripts/i9/dataset_brasil.sh
bash scripts/i9/lancar.sh --ver brasil      # últimas linhas do log
bash scripts/i9/lancar.sh --estado brasil   # rodando ou não
bash scripts/i9/lancar.sh --parar brasil
```

### A conferência que mentia — e enganou por mais tempo que o defeito

```
wsl -- bash -lc 'echo brasil=$(pgrep -c -f dataset_brasil.sh)'   →  1
```

O padrão casa com a **própria linha de comando da conferência**. O `1` era ele
mesmo. Uma produção foi dada como viva quando estava morta havia meia hora, e o
log lido como "progresso" estava parado. Agora quem responde é o systemd
(`is-active`), que não tem como se auto-encontrar — e toda conversa com o i9 vai
por **arquivo** (`bash -s` pela entrada padrão), cuja linha de comando é só
`bash -s`: nada para as três camadas de aspas comerem, nada para uma busca por
processo casar por engano.

### A fusão suspeita a 11,47%: medida, e é perda real (25/08/2026)

Foi a única reprovação do `validate` que sobreviveu à rodada limpa — idêntica
antes e depois, 11.676 fusões suspeitas contra um limite de 2%. Não é artefato.

**73% delas são por telefone** (8.483 de 11.676). E a distância não separa nada:

| Distância | Compartilham token | Núcleos disjuntos | % disjunto |
|---|---:|---:|---:|
| 0–10 m | 697 | 1.879 | **73%** |
| 10–30 m | 966 | 1.685 | 64% |
| 30–60 m | 534 | 870 | 62% |
| 60–120 m | 427 | 628 | 60% |
| 120–200 m | 327 | 470 | 59% |

Mesmo colados a menos de 10 m, três em cada quatro pares fundidos por telefone
têm nomes **sem um único token em comum**:

```
idiomas wizard           +  c montenegro you        14 m
loterica schneider       +  bazar mega             147 m
geracao moto nova sport  +  agropecuaria gaucha    198 m
bah burger               +  figurati napolitana pizza  7 m
```

As legítimas, que precisam continuar fundindo, **sempre compartilham token**:
`farelos racoes stein` + `agrocomercial stein`.

### A IA mediu, e é pior que a heurística dizia

"Núcleos disjuntos" é heurística tão frágil quanto o telefone que ela critica.
Decidir se `loterica schneider` e `bazar mega` são o mesmo negócio é **julgamento
semântico sobre nome de comércio** — não há prova mecânica, e é exatamente onde a
IA é o instrumento certo (`julgar_fusao.py`, Spark).

400 pares sorteados com semente fixa, zero falhas:

| Veredito | | |
|---|---:|---:|
| **DIFERENTE** | 265 | **66,2%** |
| MESMO | 117 | 29,2% |
| INCERTO | 18 | 4,5% |

E o corte por evidência é o que aponta o parâmetro a mexer:

```
telefone   281 pares · DIFERENTE 74,4%
site        72 pares · DIFERENTE 61,1%
nome        47 pares · DIFERENTE 25,5%
```

**A regra de nome está boa; telefone e site é que decidem sozinhos demais.**
Extrapolando: 8.483×74,4% + 1.896×61,1% + 1.297×25,5% ≈ **7.800 estabelecimentos
perdidos só no RS** — mais que os 5.532 da heurística, que era conservadora.

**Ressalva sobre a medida:** o modelo se contradisse em casos gêmeos —
`4163 ad azul voo` × `4454 ad azul voo` saiu DIFERENTE e
`4134 ad azul voo` × `4996 ad azul voo` saiu MESMO, com o mesmo raciocínio. A
ordem de grandeza está estabelecida; a segunda casa decimal, não.

`julgar_fusao.py` **mede e não conserta**: não desfaz fusão, não reescreve
entrega, não mexe em parâmetro. O veredito de cada par fica em
`julgamento_fusao.csv`, com o motivo escrito.

**A causa** é `tel_max_locais=3` (`vendor/dedup_v3.py`): um telefone só vira
"hub" e perde valor de prova se aparecer em **mais de 3** posições. Dois ou três
comércios do mesmo dono dividindo o número — o caso mais comum do varejo
brasileiro — passa como prova soberana, com `raio_forte_m=200`, e vence o nome
divergente.

**Recomendação, ainda não aplicada:** telefone deveria corroborar, não decidir
sozinho — não autorizar fusão quando os núcleos discriminantes são disjuntos, em
qualquer distância. Preserva `Padaria X` × `Panificadora X`; mata
`bah burger` × `figurati pizza`. O custo é o oposto do atual: alguma duplicata em
vez de estabelecimento apagado — e duplicata é visível e corrigível.

É mudança no motor de dedup de uma **skill vendorada**, e vale para as 27 UFs:
decisão do dono do produto, não do implementador.

### As travas

`flock` em dois níveis (commit `c55a023`), porque são dois riscos diferentes:

| Arquivo | Garante |
|---|---|
| `dataset_brasil.sh` | uma produção do Brasil por máquina |
| `dataset_estadual.sh` | um pipeline por UF — venha do laço, de disparo manual ou do painel |

O descritor fica aberto por `exec N>`; o kernel solta sozinho se o processo
morrer, então queda não deixa cadeado órfão. `tests/test_scripts_i9.py` cobra as
duas coisas em código executável, mais a ausência de `nohup`/`setsid`/`pgrep` no
lançador.

### As bases contaminadas ficaram, como exemplo

`dados_externos/_contaminado_20260825/` guarda **RS, SP, PE e SC** produzidos
naquela janela, com um `LEIA.txt` explicando o que cada defeito foi. São
**exemplo do que duas execuções simultâneas produzem** — não lixo a limpar.

Continuam em `dados_externos/estadual/` seis UFs marcadas
`veredito=produzido_com_ressalva`: **PI, PR, RJ, MG, ES e BA**. Foram produzidas
na mesma janela e **todas reprovam no `validate`**. Ficam onde estão, com a
ressalva no próprio marcador — quem for usá-las lê o `relatorio_qualidade_*.json`
antes de confiar nos números. O **RS foi refeito limpo** por ser a UF em que a
fase 1 vai ser testada de ponta a ponta.

### Produzido ≠ aprovado

O marcador `_pronto.txt` registra o **veredito**, não só a conclusão:

```
extracao-poi-estadual · uf=RS · fontes=overture,osm,fsq
veredito=produzido_com_ressalva · codigo=1
entregavel=poi_padronizado_poi_rs.parquet
ressalva=ver relatorio_qualidade_*.json na saida
```

Sem essa distinção, uma base inteira ficava inutilizável por causa de um número
que quem usa deveria poder ver e julgar. O que continua **não** gerando marcador
é a execução que não chegou ao fim: pasta pela metade não é base.

### Fechar a fase 1 de uma UF

```bash
# 1 · produzir (horas, uma vez por UF, no i9)
bash scripts/i9/lancar.sh brasil ./scripts/i9/dataset_brasil.sh

# 2 · ver o que a extração tem
python extracao_estadual.py --saida dados_externos/estadual/RS/saida --listar

# 3 · importar o município (simula primeiro; --aplicar grava)
python extracao_estadual.py --saida dados_externos/estadual/RS/saida \
  --municipio 4304606 --empresa "Aegea - Corsan" --aplicar
```

O passo 3 roda **no i9**, porque o banco está lá — trazer dezenas de GB para o
notebook só para devolver o recorte de um município seria atravessar a rede duas
vezes à toa.

---

## 37. O endereço grudado: a IA lê, a skill prova (25/08/2026)

O logradouro é a **chave de junção das bases cruzadas**. `pois`,
`cadastro_cliente`, `ifood_merchant` e o CNEFE escrevem a mesma rua de jeitos
diferentes, e o cruzamento erra para os dois lados: perde par que existe e casa
par que não existe.

A skill `ajuste-logradouro` (v3.3.5, em `skills/`) resolve isso — mas declara,
no próprio contrato, que **não segmenta campo único grudado**. E a nossa `pois`
só tem um campo `endereco`, com formatos diferentes **dentro da mesma coluna**:

```
R. Mal. Rondon, 1199 - Niterói, Canoas - RS, 92120-210, Brasil
RUA A J RENNER Número: 813 Bairro: ESTANCIA VELHA Município: CANOAS UF: RS
Av. Getúlio Vargas, 2575, Niterói, Canoas, RS
Rua Epitácio Pessoa - Canoas, Canoas - RS, 92130-340
```

Regra de vírgula não resolve: o segundo formato nem separador tem — tem rótulo.

### A divisão é por natureza do problema, não por fase

| Tarefa | Tem prova disponível? | Quem faz |
|---|---|---|
| **Segmentar** formato heterogêneo | não — só interpretação | **IA** (`segmentar_endereco.py`) |
| **Canonizar** o logradouro | sim — mesmo número, 30 m, support de 2 imóveis distintos, CNEFE | **skill** |

Passar a canonização também para a IA foi considerado e **medido**. Não passa:

- ela já tentou inventar: **7 de 320** endereços com campo descartado por não
  estar no texto (`R.` virando `Rua`, CEP completado);
- já leu o formato errado: trocou bairro e cidade até a ordem entrar no prompt;
- **e não é determinística**: mesmas 320 entradas, `temperature 0`, duas
  execuções — 7 descartes numa, 1 na outra. O vLLM agrupa requisições e a
  aritmética muda com a composição do lote.

Um léxico municipal construído sobre saída que varia entre execuções não é
auditável, e a skill inteira existe para ser auditável.

### A IA roda só na Spark

Regra do dono do produto. `_conferir_endpoint()` **recusa o i9 pelo nome**, com
o motivo: o i9 é a máquina do trabalho pesado de dados (DuckDB sobre o Overture,
PBF do OSM, Postgres de produção), e modelo ali disputa a mesma RAM de uma
extração estadual. O i9 chama a Spark por HTTP; nunca carrega modelo.

### Nada é inventado

Todo campo devolvido precisa **existir no texto original**, conferido sobre a
forma sem acento e sem pontuação — a pergunta é se o modelo trocou **palavra**,
não se mexeu no ponto da abreviação. O que não ancora é descartado, e o motivo
viaja junto com o valor que ele tentou pôr:

| `metodo` | Significa |
|---|---|
| `ia` | todos os campos existem no texto |
| `parcial` | algum foi descartado — o `motivo` diz qual e o quê |
| `revisar` | nem o logradouro sobreviveu |
| `falhou` | a Spark não respondeu, ou respondeu o que não dá para ler |

Lote que falha não derruba a rodada **e não some**: cada endereço dele sai
marcado. Buraco silencioso na base é pior que erro declarado.

### O que a conferência pegou, medido em Canoas

Não é precaução teórica. Numa amostra real:

```
texto: Avenida Santos Ferreira, 2505 ...   modelo devolveu numero='2515'
texto: Avenida Santos Ferreira, 2455 ...   modelo devolveu numero='2457'
texto: 011, Av. Getúlio Vargas, 6531 ...   modelo devolveu complemento='sala 302'
```

**Número de imóvel trocado, duas vezes.** Número errado não parece erro em lugar
nenhum a jusante: vira chave de junção errada e **evidência falsa no support do
léxico**, que é a moeda da prova. E o `sala 302` é vazamento do próprio exemplo
do prompt — o modelo copiou o few-shot para dentro de um endereço real.

### E o que ela errava: o tipo de via

Endereços legíveis caíam em `revisar` porque o modelo devolvia
`Avenida Boqueirão` onde o texto diz `Av. Boqueirão`. A recusa estava certa —
ele não pode reescrever o texto — mas rejeitar o registro inteiro custa a fonte,
e pelo único desvio que não importa: o tipo de via é exatamente o que a skill
canoniza na fase seguinte.

A relaxação vale **só para o primeiro token**:

| Caso | Resultado |
|---|---|
| `Av.` → `Avenida` | passa, marcado como `parcial` |
| `Boqueirão` → `Boa Vista` | barrado — nome trocado |
| `R. Mal. Rondon` → `Rua Marechal Rondon` | barrado — o **nome** também expandiu |
| resto com menos de 4 caracteres | barrado — ancoraria em qualquer texto |

`--refazer-metodo revisar` relê só uma classe, para quando a regra melhora.

### O que veio da skill para o prompt

A **taxonomia de complemento**, importada de `complemento_organizador.ORDEM` em
vez de copiada. Se os dois falarem vocabulários diferentes, o complemento que
entregamos vira `aj_compl_identificador` — "valor sem rótulo", que a skill
mantém e sinaliza mas não usa. Cópia manual envelheceria em silêncio: a v3.3.5
acabou de acrescentar `ENTRADA`, `COMODO`, `COBERTURA`, `PORTARIA` e `PALAFITA`
do padrão CNEFE.

Este é o ponto exato onde faz sentido dar contexto da skill ao modelo: melhora a
**leitura**, sem mover o **julgamento**.

### Duas tabelas, as duas sem `tenant_id` — e a exceção está escrita

| Tabela | Chave | Conteúdo |
|---|---|---|
| `endereco_segmentado` | o **texto** do endereço | os campos lidos pela IA, com `metodo`, `motivo` e `modelo` |
| `logradouro_ajustado` | `(fonte, record_id)` | a forma canônica marcada, com `tier` e `run_id` |

A regra da migração 0029 é condicional: tabela **que tem** `tenant_id` precisa de
RLS, policy, gatilho e índice. Estas não têm, e é decisão. `R. Mal. Rondon` é o
mesmo endereço para qualquer cliente, e "RUA CORONEL MARCOS é a forma canônica
de R. Cel. Marcos em Canoas" é fato sobre via pública. Carimbar tenant faria
cada concessionária pagar de novo o mesmo aprendizado e daria **duas verdades à
mesma rua** — o problema que isto existe para acabar. O vínculo com o cliente
está no `record_id`, que aponta para tabelas que têm `tenant_id` e RLS.

### Medido em Canoas

```
segmentação   234 ms/endereço com 16 lotes simultâneos (845 ms em série)
              17.095 distintos de 20.934 POIs -> ~67 min, uma vez, em cache

cadeia        108.835 registros marcados em 17,3 s
              CONFIRMA=105.928  ALTA=1.672  REVISAR=1.235
              47.307 pares na trava de 30 m · scope=4304606
```

`CONFIRMA` é aplicável em massa; `REVISAR` é fila humana, porque houve **perda de
texto**. Sem essa graduação, quem consome faz uma coisa só com os dois.

### Um defeito nosso que a skill achou

`cod_unico_endereco` do CNEFE **não é único na nossa carga** — 52 repetidos em
6.000. Registro repetido faz o mesmo endereço votar duas vezes no support, e
support é a moeda da prova: duas linhas com o mesmo código não são dois imóveis.
Corrigido com `distinct on` de ordem determinística na exportação.

### Rodar

```bash
# 1 · a IA lê o que ainda não foi lido (uma vez por município, fica em cache)
bash scripts/i9/lancar.sh segmentar_canoas \
  "env SPARK_THREADS=16 .venv/bin/python segmentar_endereco.py --municipio 4304606 --aplicar"

# 2 · as quatro fontes, a skill e a volta para o banco
python ajuste_logradouro.py --municipio 4304606 --listar     # só conta
python ajuste_logradouro.py --municipio 4304606              # simula
python ajuste_logradouro.py --municipio 4304606 --aplicar
```

Sempre **por município**: a skill aborta se a base cruzar mais de uma zona UTM, e
o léxico é por `scope_id` municipal de propósito — sobrenome raro numa cidade não
é sobrenome errado na outra.

---

## 38. O que não é comparável não entra (27/08/2026)

Dois dias em que a pergunta deixou de ser "quantos POIs temos" e passou a ser
"quantos deles servem para alguma coisa". A resposta mudou a base inteira.

### 38.1 O mapa mostrava os pontos que a fusão tinha absorvido

A queixa era antiga: *"não é pra ter mais que 30 mil pontos de interesse numa
cidade como Canoas"*. As fusões já estavam aplicadas no banco — 14.320 POIs com
`status='fundido'` —, mas a consulta do mapa em `server.py` filtrava apenas
`match_valido IS NOT FALSE` e a presença de coordenada.

Medido: o mapa devolvia **49.636** pontos em Canoas quando a cidade
deduplicada tinha **28.533**. O absorvido continuava desenhado ao lado de quem
o absorveu.

> **O banco estava certo desde a véspera; o mapa é que nunca tinha sido
> avisado.** Toda deduplicação é invisível enquanto a consulta que desenha não
> souber dela.

A consulta passou a exigir `status <> 'fundido'` **e** ao menos um vínculo
`estado='vinculado'` — o segundo porque um ponto cujas fontes foram todas
desvinculadas não tem o que mostrar.

### 38.2 O quadro do que é comparável

Regra do dono do produto, formalizada em quadro e provada linha a linha com
inserção real no banco:

| # | nome | endereço | coordenada | veredito |
|---|---|---|---|---|
| 1–4 | ✅ | qualquer forma | — | aceita |
| 5 | ✅ | ✗ | ✅ | **recusa** — gere o endereço ANTES de inserir |
| 6 | ✗ | com número | — | aceita — a porta identifica sozinha |
| 7 | ✗ | só logradouro | ✗ | **recusa** |
| 8 | ✗ | ✗ | ✗ | **recusa** |
| 9 | ✅ | ✗ | ✗ | **recusa** — só o nome |
| 10 | ✗ | só logradouro | ✅ | aceita — a coordenada supre a porta |
| 11 | ✗ | ✗ | ✅ | **recusa** — só a coordenada |

A linha 11 não é opinião: `PESO["categoria"] = 1` e `MIN_PARA_IA = 4`. Um
registro com coordenada e categoria tem **teto de 1 ponto** — ele não funde com
ninguém, por construção das regras. Medido antes da limpeza: 2.755 vínculos
nesse estado, **2.754 da fonte estadual**, e **2.753 deles sustentavam POIs de
fonte única** — exatamente o que a regra obriga.

**Por que trigger e não CHECK:** o número canônico mora em
`logradouro_ajustado`, e um `CHECK` do Postgres não consulta outra tabela. O
trigger também olha o padrão no próprio `endereco`, porque no instante do
INSERT a normalização ainda não rodou para aquele POI — sem isso, todo POI sem
nome seria recusado por um número que só existiria minutos depois.

Duplicata exata é barrada por índice **único parcial** sobre `(nome, endereço)`,
ativo só para POI não fundido e vínculo não desvinculado: o absorvido tem, por
definição, o mesmo nome e endereço de quem o absorveu, e ele fica gravado para
a fusão poder ser desfeita.

### 38.3 O Photon foi testado e reprovado — e o 99% era a armadilha

A cascata proposta para gerar endereço pela coordenada era OSM primeiro, Maps
depois. O Photon do próprio i9 respondeu, rápido, e com `layer=house` achou
porta em **99%** dos 200 POIs de teste. Parecia resolvido.

Não era. A distância entre o ponto e a porta devolvida:

| | ≤ 20 m | 20–50 m | 50–100 m | **> 100 m** |
|---|---:|---:|---:|---:|
| Photon | 4 (2%) | 11 | 39 | **144 (73%)** |

Ele devolve a porta mais próxima que **conhece**, e o OSM quase não tem
numeração predial no RS: *"Eixo Sul Distribuidora"* recebia *"Rua Senador
Salgado Filho 250"*, a **834 metros**.

> **Cobertura que mente é pior que buraco.** Endereço errado não fica inerte na
> base: ele casa com o vizinho errado no cruzamento. Um número de sucesso que
> não vem acompanhado da medição de QUALIDADE não é resultado, é ilusão.

O CNEFE, medido do mesmo jeito, sobre os mesmos 200 pontos:

| | acha porta | ≤ 20 m | ≤ 50 m | por ponto |
|---|---:|---:|---:|---:|
| Photon (`layer=house`) | 99% | **2%** | 7,5% | 26 ms |
| **CNEFE (IBGE)** | 100% | **85%** | **96%** | **0,18 ms** |
| Maps (navegador) | alto | alto | alto | ~30.000 ms |

A cascata ficou **CNEFE → Maps**, sem OSM. E a pergunta "e numa cidade nova?"
não tinha problema a resolver: o CNEFE já está carregado para os **5.570
municípios do país** — 111.102.875 endereços.

`endereco_reverso.py`, medido pronto: **285 de 300 POIs (95%) resolvidos em
2 ms por ponto**; só 5% iriam ao Maps. O raio de **50 m** é onde a medição para
de ser confiável.

O município é resolvido por **código IBGE**, nunca por nome:
`area_utils.codigo_ibge_da_area` sai da mesma consulta espacial que já dá cidade
e UF. *"Santana"* existe em nove estados, e o CNEFE é indexado por código —
casar por nome traria as portas do município errado, que é pior que endereço
nenhum porque parece certo.

### 38.4 A duplicata longe nunca era proposta

A regra *"mesmo nome e mesmo logradouro é confiança máxima, mesmo a 50 metros
ou 100"* estava implementada em `evidencia.avaliar` com alcance de 1.000 m. Mas
`cruzar_fontes.candidatos` só propunha par pela grade de 111 m, varrendo 3×3 —
alcance real de ~330 m.

**A regra era letra morta justamente na faixa que existia para cobrir.** Medido
depois de uma rodada completa: 344 grupos de mesmo nome + mesmo logradouro
continuavam separados, e **334 estavam a mais de 100 m**.

Alargar a grade seria o conserto errado (1 km faria os 2,5 milhões de pares
virarem ~100 milhões). *"Mesmo nome na mesma rua"* é uma **chave**, não um
raio: um dicionário resolve em O(n). Custo do canal novo: **412 pares, 0,017%**
do total, e nenhum inútil.

E aí apareceu o contra-exemplo que faltava. Dos 266 pares que a regra fundiria:

| | quantos | o que é |
|---|---:|---|
| mesmo número de porta | **167** | *"Posto Ipiranga, Guilherme Schell 1046"* × o mesmo, a 7,7 km — a coordenada de uma fonte é que erra |
| número **diferente** | **48** | *"Saque e Pague"* nos números 1011 e 1623 da mesma avenida: caixas eletrônicos distintos |
| sem número num dos lados | 51 | ambíguo |

> **Quando o endereço concorda, a distância mente. Quando a coordenada
> concorda, o endereço mente.** O número da porta é o desempate.

Efeito medido: 266 → **218** fusões automáticas; *"Saque e Pague"* de 48 para
**0**.

### 38.5 Desempenho: dois gargalos, duas naturezas

**A gravação era latência.** Dois `UPDATE` por fusão contra o Postgres do i9
viravam 26.542 idas e voltas — ~100 minutos para Canoas. Medido sobre 400
fusões reais, com rollback:

```
um a um    180,6 s  ·      2 fusões/s  ·  13.271 levariam 99,8 min
EM LOTE      0,3 s  ·  1.596 fusões/s  ·  13.271 levam     0,1 min      602×
```

A *decisão* continua sequencial — a transitividade exige ordem: se A absorve B
e depois B absorveria C, C vai para A, não para B, que já é POI fundido. Só a
*escrita* virou lote, em blocos de 1.000 (acima disso o texto do comando passa
de megabytes e o parser do Postgres vira o novo gargalo).

**A comparação era trabalho repetido.** Perfilando 200 mil pares: 72,4 s, e o
gargalo não era decidir — era `unicodedata.category` (39,3 milhões de chamadas)
tirando acento. Com 35.321 POIs em 2,5 milhões de pares, cada POI aparece em
~140 pares e tinha o nome normalizado 140 vezes, sempre com o mesmo resultado.

```
sem memória : 35,2 s ·  5.678 pares/s
com memória :  5,2 s · 38.211 pares/s     6,7×
```

E o que torna seguro: **200.000 de 200.000 vereditos idênticos**. `tokens`
passou a devolver `frozenset` — conjunto mutável em cache seria corrompido pelo
primeiro chamador que o alterasse, e o estrago apareceria em outro par muito
depois, sem causa visível.

### 38.6 Erro que se disfarça de dado

Três defeitos da mesma família, no mesmo dia:

**"0 no Maps".** Uma run reportou 0 nas 46 categorias, em pleno bairro
comercial. `buscar_categoria` devolvia `[]` em dois pontos sem dizer nada — o
`goto` que falha e o feed que não aparece. A causa era o perfil de navegador
apodrecido (mesma URL, mesmo proxy: perfil velho → `goto` timeout; perfil novo
→ 20 links). Agora a função devolve `(achados, motivo)` e o worker descarta o
perfil e refaz a sessão.

**A run viva sem trabalhar.** `_abrir` devolvia `False` sem imprimir quando o
pool não dava proxy, e o laço devolvia o lote, dormia 5 s e tentava de novo
**sem teto**. Duas runs ficaram penduradas 14 e 10 minutos: processo vivo, log
parado, CPU no chão, navegador nascendo e morrendo. Agora ela diz o que houve
(com `flush=True`, senão a mensagem fica presa no buffer de 8 KB), desiste após
5 tentativas, e o resumo denuncia quantos POIs ficaram sem busca — antes eles
sumiam da conta e "3 processados" se lia como "3 de 3" quando eram 3 de 21.

**"Botão não encontrado" quando era tempo.** `WAIT_PROXIMO_MS` era 9.000 e o
painel do Maps leva **8,0 s** na primeira coordenada. Um segundo de folga
passava em rede boa e falhava no roteador do celular. A mensagem dizia "não
encontrado", que se lê como "esse botão não existe" — e mandou o diagnóstico
para idioma da página, perfil corrompido e muro de consentimento antes de
alguém medir o tempo. Teto para 25.000, e a mensagem passou a dizer que foi
tempo e quanto se esperou.

> Os três têm a mesma forma: **falha de infraestrutura com a mesma aparência de
> resultado legítimo.** Custam uma run inteira antes de alguém desconfiar.

### 38.7 `S` não é `SÃO`

Das 253 expansões de `S` → `SÃO` na base, **222 estavam erradas**:

```
QUADR S UM, S DOIS      letra de QUADRA + numeral (Guajuviras). Não existe
                        santo chamado "Um".
BECO S NOME             S = SEM. E o dano não é só o santo inventado: apaga-se
ESTRADA S DENOMINAÇÃO   o sinal de que a via NÃO TEM NOME.
RUA S SALVADOR DALI     o pintor, em Rubem Berta (POA), no CNEFE cru.
```

`DR` e `PE` têm uma leitura só; `S` é SÃO, SEM, SETOR e letra de quadra ao mesmo
tempo. Passou a expandir **contra uma lista de santos**. Errar para o lado de
não expandir é de propósito: se as duas bases guardam "S FULANO" elas continuam
casando entre si; expandir errado é que corrompe.

### 38.8 O código certo, na máquina errada

O teto de 25 s foi ajustado, commitado, e a run seguinte falhou igual. A
mensagem de erro entregou a causa sem querer: *"não pintou em 12s"* — que é
9.000 + 2.500. **O código novo chegou ao i9; o número não.**

`i9.py` tinha a lista `_PY` digitada à mão, com `search_pois_v2.py` e sem
`config.py` — que é lido pela busca, pelo navegador e pelo pool de proxies. Um
teste do projeto até **proibia** sincronizá-lo, alegando que guardava caminho da
máquina. Não guarda: tudo sai de `Path(__file__).resolve().parent` e credencial
vem do `.env`, esse sim nunca sincronizado. Verificado no próprio i9 depois de
sincronizado — `BASE_DIR` e perfis resolveram para os caminhos **de lá**.

> **O sintoma é o pior que existe: "o conserto não funcionou".** Perde-se tempo
> relendo a correção certa e procurando defeito onde não há.

`tests/test_sincronia_i9.py` passou a verificar o **fecho transitivo dos
imports** em vez de uma lista de nomes — e encontrou `spatial_clustering`,
`extract_full`, `realtime_ingest` e `auth` rodando velhos no i9 sem que ninguém
soubesse. O fecho segue só import de **nível de módulo**: import dentro de
função protegido por `try/except` é opcional por construção, e seguir os dois
faria o teste exigir meio repositório no i9.

### 38.9 A limpeza, e o que ficou intacto

| passo | apagados |
|---|---:|
| Fora de Canoas e Parnaíba | **29.355** POIs |
| Sem nome ou sem endereço | **6.205** POIs · 57 vínculos |
| Duplicata exata nome + endereço | **93** POIs · 52 vínculos (ficou a do Maps) |
| Sem vínculo algum | 5 |

`pois`: 83.235 → **47.577**. E as tabelas base **intactas**:
`cadastro_cliente` 102.065, `cnpj_tratado` 27.147, `cnefe_coletiva` 27.227 — as
FKs separam por construção (`CASCADE` no derivado do POI, `SET NULL` nas bases).

Antes disso, **74.572 campos** com `"nan"` foram limpos de dentro do JSON dos
vínculos (site 37.709, telefone 23.407, endereço 11.972, categoria 1.484). A
chave permanece com valor `null`.

**Lição de operação:** a primeira tentativa fez tudo numa transação só e estourou
10 minutos sem terminar; a segunda, em lotes de 2.000 com commit, perdeu a
conexão no lote 20.000 — e os 20.000 já estavam gravados. **Commit por lote é o
que transforma queda em retomada.**

### 38.10 O cadastro e a base estadual não têm duplicidade interna

Verificado, porque a suspeita era razoável e o número é grande:

**Cadastro da Corsan** — as 13.355 linhas não-residenciais de Canoas têm
`num_ligacao` **único**. O campo `cliente` vale `'corsan'` nas 102.065 linhas e
não identifica ninguém (foi o que fez o primeiro agrupamento sair errado). 864
linhas dividem endereço: **673 diferem no complemento** (`SALA 01`, `SALA 02`,
`PAVILHÃO 5` — unidades reais, cada uma com sua ligação) e 191 têm complemento
`0`, não preenchido. O caso extremo é *Engenheiro Irineu Carvalho, 98*, com
**88 ligações**: um condomínio cujo complemento ninguém registrou.

**Base estadual** — 27.927 vínculos com 27.927 `id_fonte` distintos. Mesmo nome
+ mesmo endereço: **48** excedentes (0,17%). Mesmo nome + mesma coordenada: 24.
Os nomes que repetem são redes reais espalhadas (Farmácia São João ×25, Posto
Ipiranga ×15, Shell ×15).

> O volume é real; não é inflado por repetição dentro de cada base.

### 38.11 Estado ao fim do dia

| | |
|---|---:|
| `pois` na tabela | 47.577 |
| Canoas — ativos | 28.535 |
| Canoas — fundidos | 14.851 |
| **Markers no mapa (Canoas)** | **28.533** |
| Suíte | 510 passando · 40 puladas |
| Triggers ativos | `poi_comparavel`, `vinculo_comparavel` |

---

## 39. O cadastro do cliente entra no processo (28/08/2026)

### O que estava errado

Perguntei quantos POIs tinham vínculo com o cadastro e o banco respondeu
**zero**. Concluí que faltava normalizar as bases fixas. Estava errado em dois
pontos, e o dono do produto corrigiu os dois.

**A normalização estava feita e salva**: 102.065 de 102.065 linhas do
`cadastro_cliente` em `logradouro_ajustado`. Eu tinha medido vínculo em
`vinculo_poi`, onde ele nunca esteve.

**O cruzamento também existia.** `cadastro_cliente.cruzar()` liga cada imóvel ao
POI e aplica a regra de negócio — `ja_cadastrado`, `reclassificar_alta/media/
baixa`, `novo_comercial`, `sem_poi`. Ele mora em `cadastro_cliente.poi_id`, não
em `vinculo_poi`.

O defeito real era outro: **a etapa não estava no `minerar_tudo`**. Tinha rodado
uma vez, à mão. A cada mineração o resultado ficava mais velho, e foi assim que
**255 ligações acabaram apontando para POI fundido** — o cruzamento parado
enquanto o passo 8 seguia unindo pontos.

### Por que o casamento direto quase não achava nada

Olhando os dois lados normalizados:

| fonte | logradouro normalizado |
|---|---|
| cadastro | `BRASIL` · `TIRADENTES` · `DAS ANDORINHAS` |
| POIs | `RUA DA BARCA` · `RUA TOBIAS BARRETO` |

**O cadastro não traz o tipo do logradouro na origem.** O dado bruto do cliente
é `INDIO SEPE`, `HENRIQUE DIAS` — sem `RUA`/`AVENIDA` —, e não há coluna de tipo
em nenhuma das 84. A normalização dos dois lados é fiel à fonte: ela não pode
inventar o que a fonte não tem, nem aproximar o que a fonte separou.

Casando com o tipo presente de um lado e ausente do outro, **297 POIs**. Com o
tipo removido dos dois, **17.712**.

### A hierarquia

Regra do dono do produto: *"o endereço normalizado é o máximo de confiança"*.

| força | critério | teto de distância |
|---:|---|---|
| 3 | logradouro normalizado + número | **nenhum** |
| 2 | CEP + número | 250 m |
| 1 | só geografia | **8 m** — a testada média de um lote |

A força 3 não tem raio de propósito. Se as duas fontes dizem a mesma via e a
mesma porta, quem erra é a coordenada — é a mesma razão pela qual a fusão une
*"mesmo nome, mesma rua e mesmo número"* a qualquer distância. Pôr um teto aqui
deixaria o dado fraco vetar o forte.

### O resultado

| | antes | agora |
|---|---:|---:|
| ligações com POI | 12.040 | **14.959** |
| └ por logradouro normalizado | — | 11.532 |
| └ por CEP + número | — | 910 |
| └ só por geografia | — | 2.517 |
| ligações apontando para POI fundido | 255 | **0** |
| POIs sem ligação nenhuma | — | 17.470 |

E as flags de negócio, que são o que o produto entrega:

| flag | ligações |
|---|---:|
| `sem_poi` | 87.106 |
| `ja_cadastrado` — comercial na base e com POI, não visitar | 6.243 |
| `reclassificar_baixa` | 6.123 |
| `reclassificar_media` | 1.526 |
| `reclassificar_alta` — POI com CNPJ confirmado, visitar primeiro | 1.067 |

### Onde a etapa entra

**Passo 9, depois do 8.** O cadastro cruza com POIs já fundidos e já com a
coordenada corrigida — o dado mais maduro que a rodada produz. Antes do 8, ele
casaria com duplicatas que o 8 uniria em seguida, e o vínculo apontaria para um
ponto que deixa de existir.

O total de etapas virou `TOTAL_ETAPAS`, constante. Quando a 9 entrou, todo o log
continuou dizendo "de 8" — inclusive o cabeçalho do próprio passo 9.

### O que isto habilita

A tela nova tem a **instalação como chave**, e agora os dois lados dela existem:

- **14.959 ligações com POI** — a fila principal
- **17.470 POIs sem ligação** — a lista secundária, para vinculação humana

### Uma regra de trabalho que ficou registrada no mesmo dia

Toda pergunta ao usuário vai na **caixa formal**, nunca solta no texto da
resposta. Ele pediu isso duas vezes — em 27/08 e de novo em 28/08, depois de eu
reincidir. Pergunta no meio de um relatório de números se perde: ele lê a
medição, a dúvida fica no rodapé, e a resposta vem incompleta ou não vem.

---

## O mapa da tela nova usa a máquina que já existia (28/08/2026)

### O defeito

Construí o mapa da tela nova do zero. Não devia. O `app.js` já tinha uma máquina
de mapa afinada por rodadas de uso, com defeitos caçados e documentados no
próprio código — e eu reintroduzi todos eles em uma tarde.

O dono do produto mediu e foi direto:

> "usa a mesma logica de desenho em mapa, divisas, markers que ja existia, voce
> destruiu o desempenho e a estetica com as mudanças que sem necessidade fez na
> dinamica de mapa, era só adicionar o que pedi nao mudar o que ja funcionava"

### A medição

No navegador, com os **32.429 POIs de Canoas** e o mesmo ícone nos dois casos:

| | desenhar | nós no DOM |
|---|---|---|
| `L.layerGroup` — o que eu fiz | **10.268 ms** | **32.429** |
| `L.markerClusterGroup` — o que já existia | **477 ms** | **518** |

**21× mais rápido, 63× menos DOM.** O cluster não é enfeite: é o que mantém no
DOM só o que cabe na tela. Sem ele, cada POI do município é um `<div>` vivo.

### O que mais eu tinha quebrado

- **Camada base recriada a cada troca.** Cada base era uma fábrica chamada no
  clique: escolher "Satélite" instanciava um `google.maps.Map` novo e largava o
  anterior. O `app.js` cria uma vez e guarda em `.layer`.
- **Recriar qualquer base quando a JS API do Google chega.** Defeito que a tela
  antiga já havia caçado: o "Mapa claro (sem Google)" virava um mapa do Google, e
  se a API não inicializasse ficava sem fundo nenhum. Só a base ativa **e do
  Google** pode ser recriada.
- **Sem panes.** A escada de z-index existe porque a malha é uma camada clicável
  que cobre o mapa inteiro e rouba o clique de tudo por baixo. Sem andar próprio,
  clicar num POI dentro do município selecionava o município.
- **Escolha de fundo não persistia.** Quem trabalha em satélite reescolhia a cada
  carregamento.
- **Popup montado para todos os pontos.** 32 mil textos construídos para que
  ninguém lesse. Agora é montado no clique.

### As duas instruções que pareciam se contradizer

Em 27/08 o pedido foi um mapa "sem delimitação de municípios, sem clusters". Em
28/08, manter a lógica que já existia — divisas incluídas. Não são a mesma
pergunta:

- **Divisas** viraram um **liga/desliga no menu do mapa, nascendo desligadas**.
  A máquina fica; o mapa abre limpo; quem precisa conferir onde um município
  acaba liga. A malha entrou com `interactive: false` — ela não precisa mais
  roubar clique, porque nesta tela a seleção de município é pelo painel.
- **Cluster** voltou. O que estava proibido era eu trocar o motor; o cluster é o
  motor. O que era para mudar — e mudou — é o **eixo da cor**: deixou de ser a
  categoria e passou a ser o cruzamento com o cadastro.

### O que de fato foi acrescentado

Só isto, que era o pedido original:

- **a cor** vem do cruzamento com o cadastro — amarelo já é comercial na base do
  cliente, verde em destaque é habitacional na base com comércio achado no local;
- **o distintivo acima do pino** conta quantas bases sustentam o ponto. Só
  aparece com mais de uma: "1" em 32 mil marcadores seria ruído.

### `mapa.css` — o desenho do marcador é um só

As regras do pino moravam no `style.css`, que só a tela antiga carrega; por isso
eu desenhei uma bolinha própria na tela nova. Saíram para `frontend/mapa.css`, e
**as duas páginas carregam o mesmo arquivo**. Duas cópias divergem, e a que
diverge é sempre a que ninguém está olhando. As decorações que são só da tela
antiga (veredito, estrela, losango da IA de fachada) continuam no `style.css`.

### A lição

Antes de escrever uma tela nova sobre um domínio que já tem tela, ler a antiga.
Cada comentário longo naquele arquivo é um defeito que alguém já pagou para
descobrir. Reescrever do zero é assinar embaixo de todos eles de novo.
