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
  `areas/area_atual.json`. **Nenhum job inicia sem área.** POI fora do polígono → status
  `fora_da_area`, `match_valido=False`, não entra no banco.
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
- **Divisas territoriais**: `GET /api/malha` baixa a malha municipal da UF no IBGE (detecta a
  UF majoritária do banco; cache em `malhas/<UF>.geojson`; resposta do IBGE vem gzip — o
  endpoint descomprime). Overlay com tooltip do nome do município; toggle "🗺️ Divisas".
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

```
WEBSHARE_API_KEY=<chave da API Webshare (proxies)>
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
```
⚠️ Chaves foram coladas no chat durante o desenvolvimento — **rotacionar** por segurança.

**Dependências Python** (no .venv): `playwright, playwright-stealth, opencv-python, numpy, easyocr,
scikit-learn, openpyxl, aiohttp, python-dotenv, psycopg2-binary, openai`. `playwright install chromium`.
**Node:** `prisma@6`, `@prisma/client@6`, `ts-node`, `typescript` (Prisma 7 quebra — usar 6).

**UTF-8 no Windows:** `config.forcar_utf8()` reconfigura stdout/stderr (emojis quebram em cp1252).
Ao rodar Python via Bash tool, prefixar `PYTHONUTF8=1`.

---

## 3. Banco de dados (PostgreSQL, gerenciado por Prisma)

- DB `comercialradar` em `localhost:5432`. Schema em `prisma/schema.prisma`.
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

## 10. Estado atual do dataset (Parnaíba / Aegea PI)

Planilha: `C:\Users\ceo\Downloads\Contratos\Aegea PI\parnaiba_calebe_buscar.xlsx` (6.741 POIs, UF=PI).
Resultado no banco (`comercialradar`) **após os saneamentos de 02/07/2026** (lição 6 + corte
pelo polígono municipal do IBGE — todo POI do banco está DENTRO do município de Parnaíba):
- **3.978 POIs** — 2.396 ok + 394 recuperado_ia + 404 recuperado_gemini + 199
  recuperado_proximo + 585 descobertos.
- Fila de reprocesso no JSON (`--retry-failed`): ~2.028 linhas (inclui 69 que estavam fora do
  município e foram marcadas `erro`). Planilha + JSON de estado em `uploads/`.
- Resíduo não localizável segue no JSON com match_valido=false (não ingere).

---

## 11. Arquivos-chave (mapa mental)
```
server.py (FastAPI+WS)  frontend/ (mapa web)  area_utils.py  realtime_ingest.py  minerar_area.py
config.py  proxy_pool.py  spatial_clustering.py  human_browser.py  extract_full.py
search_pois_v2.py  recover_pois_v2.py  search_from_sheet.py  db_export.py
ai_decisor.py (OpenAI)  gemini_localizador.py (Gemini)  gerar_mapa_html.py
src/ingest.ts (Prisma)  prisma/schema.prisma  user_agents.json  camada1_serp_TODO.py (stub)
areas/area_atual.json (polígono)  uploads/ (planilhas)  mineracao/ (saídas do minerador)
.env (segredos)  mapa_pois.html (saída legado)  PIPELINE.md (doc pipeline)  DOCUMENTACAO.md (este)
descrever_imagens.py (análise IA)  docs/processo.html (mapa visual do fluxo)
base_comum.py  base_cnpj.py  base_cnefe.py (bases externas p/ enriquecer)
telhados_cv.py (identificador de telhados: Overture+CV+IA)  telhados_area.py  segmentar_telhados.py
```

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

## 14. Identificador de telhados por área — `telhados_cv.py`

Conta **cada edificação** de uma área (bbox ou polígono), mede **área (m²)** e **ALTURA
(m/pavimentos)** e classifica em **4 categorias simples** (decisão do cliente — sem
"alto padrão"): `casa_terrea` | `predio` | `galpao` (empresa) | `terreno_vazio`.
Grava na tabela **`telhados`** e gera **galeria HTML** em `exemplos/_telhados_<rotulo>.html`.

### Duas fontes de DETECÇÃO (achar cada edificação)
- **`overture` (padrão)** — footprints já extraídos por IA (**Google Open Buildings +
  Microsoft**, via **Overture Maps**), lidos do GeoParquet público com **DuckDB** filtrando
  só a bbox (reaproveita `telhados_area.puxar_overture`). Pega TODA edificação, inclusive
  **laje cinza** que a cor não vê, e traz a **área geodésica** de cada uma. É "IA pronta":
  nada roda na máquina/GPU local, **custo zero**.
- **`cor` (fallback)** — nosso **CV clássico (OpenCV)** na imagem Google: máscara de cor da
  telha (**cerâmica** laranja / **laje** clara) → **watershed** que separa casas
  **geminadas** usando o tamanho de lote como espaçamento de sementes → filtro de
  **saturação** (`_pureza`) contra falso-positivo em solo batido. Use onde o Overture é
  grosseiro (cidades pequenas — foi o caso de Parnaíba, origem do módulo).

Imagem: **Google z21** (~0,075 m/px — detalhe real; z22 é upscale). `--zoom` configurável.

### ALTURA + TIPO — `analisar_e_tipar(roofs, arr, mpp)` (o coração)
Overture não dá altura no BR (0/165 na amostra; alturas da Microsoft: 0% no tile de
Teresina — sondado e descartado). A altura sai da FÍSICA da própria imagem z21:
1. **Sombra projetada no chão** — direção do sol detectada sozinha; footprint deslocado na
   direção da sombra mede o comprimento → metros. Pixel sobre outro footprint é OCLUSO
   (não conta contra); pé da sombra tampado → "não mede" (None).
2. **Fachada visível** (z21 é levemente oblíquo) — banda escura na borda do lado da sombra;
   **autocalibrada px→m** pelos prédios que mediram sombra. Resolve o denso.
3. **Resgate pelo grupo** — bloco sem medição herda a mediana dos **≥2 blocos altos
   (≥140 m²) a ≤80 m** (condomínio); semeado só por medidos, não percola.
4. **Tipo**: cerâmica → casa; alto (≥3,5 m) → prédio se **planta recortada** (fill<0.96–0.98,
   caixa de escada) + **grade de ≥12 JANELAS** na fachada OU grupo de blocos; **frisos
   direcionais ≥0.25** (telhado metálico) = veto → galpão; >900 m² → galpão; 2ª passada:
   "galpão" alto cercado por ≥2 prédios com telhado da MESMA COR = fragmento → prédio.
5. `terreno_vazio`: footprint sem estrutura (chão batido/mato, sem sombra/fachada).

Colunas novas na tabela: **`altura_m`, `andares`, `janelas`** (`origem_tipo=overture+altura_v2`).

- **`--ia`** (opcional): recorte + 1 exemplo de referência por tipo no **seu Ollama**
  (qwen2.5vl). Obs.: zero/few-shot puro COLAPSA no 7b (crava um rótulo só) — usar apenas
  como refino com exemplos curados. Ver [[percepcao-cega-julgamento-isolado]].

### Funções principais
- `detectar_overture(bbox, ox, oy, arr, mpp)` — footprints → polígono/área/centro; material
  pela cor DENTRO do footprint (`_material_footprint`, erode contra desalinhamento).
- `detectar(arr, mpp)` — CV: `_mascaras` (HSV) → `_instancias_ws` (watershed) na cerâmica.
- `analisar_e_tipar(roofs, arr, mpp)` — altura+tipo (item acima).
- `recorte` (crop 360px com contorno), `galeria`, `curar_exemplo`, `run`.

### Comandos
```
# área por polígono (4+ vértices), Overture (recomendado):
python telhados_cv.py --poligono="-5.1279,-42.7981;-5.1259,-42.7940;-5.1320,-42.7914;-5.1346,-42.7954" --rotulo=area1
python telhados_cv.py --bbox=-5.13157,-42.79608,-5.12843,-42.79292 --rotulo=amostra
python telhados_cv.py --bbox=... --fonte=cor --zoom=20   # CV fallback / zoom menor
python telhados_cv.py --bbox=... --ia                    # refina tipo c/ exemplos
python telhados_cv.py --exemplo casa_terrea=quadra1:7    # curar exemplo de referência
```

### Validação (Teresina-PI, amostra com condomínio de apês revisada card a card)
132 edificações: **92 casas térreas (100% ok) · 31 prédios (28/28 blocos verificados na
calibração) · 9 galpões · 0 vazios**. Limite conhecido: ainda há confusão residual
prédio×galpão em construções altas atípicas — se um dia precisar de andares exatos, o
dado real é o **Google Open Buildings 2.5D via Earth Engine** (login único de 5 min).

> Antecessores (mantidos): `telhados_area.py` (Overture cru, `--visao`) e
> `segmentar_telhados.py` (FastSAM — descartado, ruído; ainda exporta o motor de tiles).
> O `telhados_cv.py` é o caminho recomendado.
