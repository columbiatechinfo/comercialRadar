<div align="center">

# 🗺️ ComercialRadar

**Plataforma de mapeamento e enriquecimento de pontos comerciais (POIs)**
_Descobre, valida e enriquece estabelecimentos com dados do Google Maps, mineração web e Receita Federal — tudo num mapa interativo em tempo real._

<br>

![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-WebSocket-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase%20self--hosted-4169E1?logo=postgresql&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-stealth-2EAD33?logo=playwright&logoColor=white)
![Leaflet](https://img.shields.io/badge/Leaflet-OpenStreetMap-199900?logo=leaflet&logoColor=white)

</div>

---

## 📌 O que é

O ComercialRadar recebe uma **planilha de estabelecimentos** (ou minera uma **área desenhada no mapa**) e produz uma base rica de POIs geolocalizados, com **fotos, avaliações, horários, telefone, site, Instagram, CNPJ e quadro de sócios** — exibidos num frontend de mapa no padrão Google Maps/Waze, alimentado em **tempo real via WebSocket**.

Foi construído para levantamentos comerciais de campo (ex.: base de clientes potenciais de um município inteiro) onde a planilha de origem é **incompleta ou com endereços errados**, e a meta é obter o dado **correto e verificável** da forma mais barata possível.

---

## ✨ Principais recursos

| | Recurso |
|---|---|
| 🗺️ | **Mapa interativo** (Leaflet + OSM) com markers estilizados por categoria, clusters, modal rico e **divisas municipais do IBGE** |
| ✏️ | **Área de trabalho por polígono** — desenhe os limites; pontos fora são rejeitados e o banco pode ser limpo pela área |
| 📄 | **Fluxo por planilha** — importa `.xlsx`/`.csv`, casa cada linha com o lugar real no Maps |
| ⛏️ | **Mineração de área** — varre uma região do mapa e descobre todos os POIs úteis |
| 💎 | **Enriquecimento em cascata** — cada POI pobre passa por **Maps → Web → Street View** até completar |
| 🏢 | **Dados empresariais** — CNPJ, razão social, CNAE, situação cadastral e **sócios**, validados na Receita Federal |
| 📸 | **Street View** — captura a fachada de cada ponto e guarda a **data do panorama** |
| 🔎 | **Busca flutuante** com autocomplete ao vivo, e **filtros por origem e atributo** (com CNPJ, sem telefone, etc.) |
| 📥 | **Imagens no banco** — baixa os bytes de todas as fotos + a **data EXIF** de cada uma |
| 📊 | **Dashboard** — cobertura, CNPJ por confiança, custo real × cenário Google e **cálculo de retorno** por faixa de qualidade |
| 👥 | **Cadastro do cliente** — importa a carteira de imóveis da empresa e cruza com os POIs, marcando o que **não visitar** e o que acresce à base |
| ⚡ | **Tempo real** — markers e cards atualizam via WebSocket conforme o backend processa |

---

## 🏗️ Arquitetura

```
                    ┌──────────────────── FRONTEND (mapa) ────────────────────┐
                    │  Leaflet + OSM · busca · filtros · modal · WebSocket     │
                    └───────────────┬─────────────────────────▲───────────────┘
                                    │ REST /api + /ws          │ eventos (poi/progresso/log)
                    ┌───────────────▼─────────────────────────┴───────────────┐
                    │              server.py  (FastAPI + jobs)                 │
                    │  dispara subprocessos · watcher ingere · transmite       │
                    └───────────────┬──────────────────────────────────────────┘
        ┌───────────────────────────┼───────────────────────────────────────────┐
        ▼                           ▼                           ▼                 ▼
 search_from_sheet.py        minerar_area.py           enriquecer_tudo.py   baixar_imagens.py
 (planilha → Maps)           (polígono → Maps)         (cascata pós)        (imagens+datas→banco)
        │                           │                           │
        └──────────────┬────────────┴───────────────────────────┘
                       ▼
        PostgreSQL no i9 (Supabase self-hosted)  ·  pois + images_urls + comentarios
              + horario_funcionamento + streetview_imgs
```

### Os fluxos

**A · Coleta por planilha** (`search_from_sheet.py`)
Casa cada linha da planilha com o lugar real no Maps (fill robusto, proxy estático, fingerprint), com **portões de distância** (evita homônimos distantes) e **filtro de UF**. Cadeia de recuperação em 4 camadas para quem não casa direto: vizinhos "próximo daqui" → decisor OpenAI → localizador Gemini → descoberta de leads.

**B · Mineração de área** (`minerar_area.py`)
Varre a bounding box de um polígono em grade e coleta todos os estabelecimentos, respeitando o gate da área.

**C · Enriquecimento em cascata** (`enriquecer_tudo.py`)
Para cada POI com dado pobre (falta telefone/endereço/categoria):
1. **🔗 Maps** — reabre o painel oficial (fotos, reviews, telefone, horário, endereço estruturado)
2. **🌐 Web** — busca no Yahoo → Instagram/iFood/sites → IA barata só para estruturar → **CNPJ + sócios na Receita Federal**
3. **📸 Street View** — foto da fachada + data do panorama

**D · Imagens para o banco** (`baixar_imagens.py`)
Passo final: baixa os bytes de todas as fotos (com a **data EXIF**) e dos Street Views (com a **data do panorama** via API de metadados) para dentro do PostgreSQL.

---

## 🧰 Stack

- **Backend:** Python 3.10 · FastAPI + WebSocket · Playwright (+ stealth) · aiohttp · psycopg2 · Pillow
- **IA:** OpenAI `gpt-4o-mini` (decisor/estruturador) · Google `gemini-2.5-pro`/`flash` (localizador)
- **Dados abertos:** BrasilAPI / minhareceita.org (Receita Federal) · malhas IBGE · Google Street View metadata
- **Banco:** PostgreSQL do **Supabase self-hosted no i9**, schema `comercialradar`,
  alcançado por Tailscale. Acesso por `psycopg2`; migrações em `*.sql` versionados.
  Bytes de imagem ficam no **Storage**, não no banco.
  ⚠️ O `prisma/schema.prisma` continua no repositório como **legado** — ele não
  manda mais no schema (ADR [0002](docs/adr/0002-prisma-legado.md))
- **Frontend:** HTML/CSS/JS puro · Leaflet + OpenStreetMap + markercluster
- **Proxies:** Webshare (100 IPs estáticos, cooldown automático)

---

## 🚀 Setup

### Pré-requisitos
- Python 3.10, Node.js (para `ts-node`, usado pela captura), acesso ao Postgres do i9
  (ou um Postgres local, se `I9_POSTGRES_HOST` ficar vazio)
- Contas: Webshare (proxies), OpenAI, Google AI Studio (Gemini)

### Instalação
```bash
# 1. Ambiente Python
python -m venv .venv
.venv\Scripts\pip install playwright playwright-stealth opencv-python numpy easyocr \
    scikit-learn openpyxl aiohttp python-dotenv psycopg2-binary openai Pillow fastapi uvicorn
.venv\Scripts\playwright install chromium

# 2. Node (captura de tiles via src/capture.ts)
npm install

# 3. Configurar segredos
copy .env.example .env        # preencha as chaves
```

### `.env`
```ini
WEBSHARE_API_KEY=...

# Banco do PRODUTO — no i9. Esvaziar I9_POSTGRES_HOST volta ao Postgres local.
I9_POSTGRES_HOST=...
I9_POSTGRES_PORT=...
I9_POSTGRES_DB=...
SUPABASE_SERVICE_ROLE_KEY=...  # Storage: bytes de imagem fora do banco

# Banco de REFERÊNCIA — CNEFE, malha IBGE, CNPJ. Container à parte, sem JOIN.
REF_POSTGRES_HOST=...
REF_POSTGRES_PORT=...
REF_POSTGRES_DB=...

# Postgres local — legado/fallback
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=...          # se tiver '@', no DATABASE_URL vira %40
POSTGRES_DB=comercialradar
OPENAI_API_KEY=...             # opcional (fallback p/ Gemini flash)
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-pro

GOOGLE_TILES_KEY=...           # fundo do mapa (o server faz proxy; não vai ao navegador)
GOOGLE_MAP_ID=...
MAPS_JS_KEY=...                # captura de tiles — SEM ela a captura para na hora
MAPS_API_KEY=...               # Places (motor pago, opcional) e data do Street View

# metabuscador próprio; várias instâncias em ordem de preferência
SEARXNG_URL=http://100.115.117.49:8888,http://localhost:8888
```

---

## 🖥️ Uso

### Interface web (recomendado)

O painel roda **no i9** desde 24/08/2026 — é lá que moram o banco, o Storage e
os serviços geo, e o pipeline deixou de atravessar a rede a cada consulta
(o banco respondia em 1,5 s do notebook; responde em 0,2 s de lá).

**Abra https://desktop-s8l7nat.tail7e301b.ts.net:8443** — precisa estar no
tailnet. Certificado da Tailscale, então o navegador não reclama.

Publicar código novo e operar:
```bash
scripts/i9/publicar.sh                      # do notebook
ssh orbisgrid@100.115.117.49 "wsl -d Ubuntu -- bash -lc '/home/orbisgrid/comercialradar/scripts/i9/painel.sh estado'"
```

Para rodar localmente (depuração), ainda funciona:
```bash
.venv\Scripts\python server.py
```
Abre em **http://localhost:8765**. ⚠️ Não deixe os dois no ar contra o mesmo
banco: cada um tem o seu trava-job de um por vez, e juntos disparam dois. No cabeçalho, escolha o modo:
- **📄 Importar planilha** — baixe o modelo, importe seu `.xlsx`, **desenhe a área** (passo 1) e inicie.
  Aqui também entra o **cadastro do cliente** (a carteira de imóveis), com prévia
  em modal antes de gravar e cruzamento com os POIs no fim.
- **⛏️ Mineração de área** — desenhe o polígono e minere. Motor padrão: captura+OCR (grátis).
- **💎 Enriquecimento** — cascata Maps → CNPJ local → Web → Street View. A fase 4
  vem marcada como **só nos pobres** (sem foto, telefone ou avaliação).
- **🧱 Quadras + endereços** — o processo de 5 passos sobre a malha de vias.
- **📊 Dashboard** — leitura, não operação: cobertura, CNPJ por confiança, custo e retorno.

A **área de trabalho é a fonte única de cidade/UF** — ela vem do polígono
desenhado, do clique num município ou do select de UF+município, e vale para
todas as abas.

Os markers e cards atualizam em tempo real conforme o processamento avança.

### Linha de comando (processos longos)
> Runs longos sobrevivem melhor no terminal do que em background. Sempre prefixe `PYTHONUTF8=1` no Git Bash.
```bash
# coleta por planilha (Maps + recuperação + ingest + mapa)
.venv\Scripts\python search_from_sheet.py "planilha.xlsx" --workers 10 --recuperar --ingest

# enriquecimento em cascata (idempotente; retomável)
.venv\Scripts\python enriquecer_tudo.py --area areas\area_atual.json --workers 4

# só a fase web (SearXNG + Receita)
.venv\Scripts\python enriquecer_tudo.py --area area_atual --pular-maps --pular-streetview

# CNPJ pela base da Receita já no banco — segundos, sem rede, e resolve a maioria
.venv\Scripts\python cnpj_local.py --cidade Canoas --aplicar

# só a fachada, e só de quem está mal documentado
.venv\Scripts\python enriquecer_tudo.py --area area_atual \
  --pular-maps --pular-cnpj-local --pular-web --sv-so-pobres

# baixar todas as imagens (bytes+datas) para o banco — rodar por ÚLTIMO
.venv\Scripts\python baixar_imagens.py --workers 8
```

> A área é passada pelo **nome** (`--area area_atual`), não por caminho de
> arquivo: ela mora na tabela `area_trabalho`, para o servidor e todos os
> coletores enxergarem a mesma coisa.

---

## 🗄️ Banco de dados

Tabela principal **`pois`** (1 linha por ponto) + derivadas 1:N:

| Tabela | Conteúdo |
|---|---|
| `pois` | dados escalares: nome, endereço (+ `endereco_fonte`), telefone, categoria, avaliação, **cnpj, razao_social, cnae, socios, situacao_cadastral**, instagram, streetview_path… |
| `images_urls` | fotos do Maps (url + **data EXIF**); os bytes ficam no Storage |
| `comentarios` | avaliações escritas |
| `horario_funcionamento` | horários por dia |
| `streetview_imgs` | fachada + **data do panorama** + pano_id + `angulo` (giro 360°) |
| `analise_ia` | veredito visual por POI (1:1) |
| `cadastro_cliente` | carteira de imóveis do cliente · `cruzamento` liga aos POIs |
| `cnpj_tratado` · `cadastur_prestador` · `cnefe_coletiva` | as bases externas já tratadas |
| `usuarios` · `tenants` · `auditoria` | acesso, empresa e rastro |

**Onde ele mora:** schema `comercialradar` no Supabase self-hosted do **i9**,
alcançado por Tailscale. Um segundo container (`cr-referencia`) guarda CNEFE,
malha do IBGE e CNPJ — **nunca há JOIN entre os dois**: é base pública,
rebaixável da fonte, e por isso fica fora do backup.

**Multi-tenant:** 27 tabelas têm `tenant_id`, com RLS e índice com `tenant_id`
na primeira coluna. Quatro tabelas novas ainda estão sem política — ver
[`docs/CHECKLIST.md`](docs/CHECKLIST.md).

> ⚠️ **Nunca rode `prisma migrate dev`.** O Prisma é legado aqui (ADR
> [0002](docs/adr/0002-prisma-legado.md)): nenhum código importa `PrismaClient`.
> Mudança de schema é uma migração `*.sql` versionada em `prisma/migrations/`.

**Backup:** dump diário do banco do produto (31 MB) + espelho do Storage com
`link-dest`, 14 dias de retenção, cron das 03:10, **restauração validada** em
máquina limpa.

---

## 🧩 Estrutura de arquivos

```
server.py                FastAPI + WebSocket + orquestração de jobs + APIs do painel
frontend/                mapa e painel (index.html, app.js, style.css, tokens.css)
realtime_ingest.py       ESCRITOR ÚNICO de POI (dedup, merge não-destrutivo, área)
auth.py                  usuários, níveis e o tenant da requisição

— de onde vem o POI —
search_from_sheet.py     planilha → Maps (+ 4 camadas de recuperação)
minerar_captura.py       captura → recortes → OCR → busca (o motor do painel)
minerar_area.py          polígono → Places Nearby (pago)
cadastur.py              Cadastur/MTur: baixar → carregar → cruzar → gerar POI
extracao_estadual.py     POI em escala estadual (Overture + OSM + Foursquare)
coletivas_radar.py       unidades coletivas do CNEFE

— como ele engorda —
enriquecer_tudo.py       cascata Maps → web → Street View (aceita --poi-ids)
cnpj_local.py            CNPJ pela Receita já no banco   ·   tratamento_cnpj.py
cadastro_cliente.py      carteira de imóveis   ·   cruzar_bases.py  o cruzamento
streetview_capture.py    fachada + giro 360°   ·   baixar_imagens.py  bytes → Storage

— onde ele fica no lugar certo —
geocodificar.py          Photon → Nominatim → painel do Maps; precisão declarada
conferir_coordenadas.py  varredura   ·   identificar_divergente.py  trabalho errado

— como ele é julgado —
descrever_imagens.py     veredito visual   ·   avaliar_fachada.py  leitura de fachada

— o chat com ferramentas (resgate do resíduo) —
agente_local.py · chat_api.py · ferramenta_*.py · dossie.py

— infraestrutura —
verificar_servicos.py    7 verificações de FUNÇÃO, não de porta
scripts/i9/              banco, Storage e backup   ·   human_browser.py  ·  proxy_pool.py

— provas e documentos —
tests/ (229 testes) · prova_ponta_a_ponta.py · prova_carga_chat.py
DOCUMENTACAO.md          documentação técnica completa (fonte única de verdade)
docs/                    PRD · ARQUITETURA · MODULOS · RBAC · INFRA · CHECKLIST · ADRs
```

---

## 🛠️ A jornada — problemas enfrentados e como resolvemos

Este projeto passou por várias iterações de depuração. Os principais aprendizados:

<details>
<summary><b>1. Dispersão geográfica de POIs</b></summary>

Nomes iguais a lugares famosos ("Tóquio", "K2") faziam o Maps/Gemini retornar o lugar distante.
**Solução:** portões de distância (match 20 km, candidatos 20 km, Gemini 40 km) + guard de coordenada no ingestor + a run é UF-scoped. Saneamento por polígono municipal do IBGE.
</details>

<details>
<summary><b>2. Candidatos legados sem portão inflando recuperados/descobertos</b></summary>

O JSON acumulava candidatos coletados antes do portão existir (dist_m de milhares de km), e a IA/descoberta os aceitava.
**Solução:** filtro `_cands_confiaveis` (≤20 km) antes da IA e da descoberta + revalidação.
</details>

<details>
<summary><b>3. Contadores do painel inflados / barra 100% falsa</b></summary>

O watcher recontava o arquivo inteiro e re-ingeria os já gravados.
**Solução:** baseline (delta do job) + dedup por place_id + progresso vindo do log.
</details>

<details>
<summary><b>4. Gemini flash fraco e caro no resíduo</b></summary>

`gemini-2.5-flash` devolvia endereço/telefone null; o pro custava ~US$37/cidade.
**Solução:** mineração web própria (Yahoo — Google dá CAPTCHA, Bing serve resultado-isca) + pré-filtro por regex + IA só para estruturar + Receita Federal. **~US$0,001/POI.**
</details>

<details>
<summary><b>5. Ingestor apagando fotos e dupla ingestão</b></summary>

Reingerir um POI com `fotos=[]` apagava as fotos; rodar standalone não gravava (dependia do watcher).
**Solução:** **merge não-destrutivo** (dado novo vazio nunca apaga o antigo) + ingestão direta com flag `--sem-ingest` para o modo web.
</details>

<details>
<summary><b>6. Endereço da web sobrescrevendo o do Maps</b></summary>

A fase Web (que não abre o Maps) gravava endereço errado por cima do endereço estruturado do Maps.
**Solução:** prioridade Maps > Web + coluna `endereco_fonte` + passada de conferência que reabre o `maps_url`.
</details>

<details>
<summary><b>7. Extração frágil por seletores absolutos</b></summary>

Seletores CSS (`data-item-id`) renderizam tarde e quebram.
**Solução:** busca por **padrão no texto** ("Ctrl+F") — telefone/site por regex, resiliente a timing e layout.
</details>

---

## 📄 Documentação

Detalhes técnicos completos (seletores do Maps aprendidos, flags, armadilhas, estado do dataset) em **[`DOCUMENTACAO.md`](DOCUMENTACAO.md)** — a fonte única de verdade do projeto.

---

<div align="center">
<sub>Columbia Tech · ComercialRadar</sub>
</div>
