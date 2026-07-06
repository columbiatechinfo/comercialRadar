# ComercialRadar — Pipeline de Extração de POIs

## Visão Geral

```
index.ts  →  detect_crops.py  →  ocr_pois.py  →  search_pois.py  →  recover_pois.py
```

Cada etapa gera arquivos que alimentam a próxima.
Todos os arquivos de saída ficam dentro da pasta da sessão em `capturas/`.

---

## Etapa 0 — `index.ts` (TypeScript / Node)
**O que faz:** Interface de linha de comando interativa que define a área de
captura e dispara o Playwright para fotografar o Google Maps em tiles.
Permite buscar a área pelo nome (via OpenStreetMap) ou informar coordenadas
manuais. Gera todos os tiles PNG da sessão e o `session.json`.

**Comando:**
```
npx ts-node src/index.ts
```

**Perguntas interativas:**
```
[1] Buscar pelo nome (cidade/bairro)   ex: "Zona Norte Teresina PI"
[2] Informar coordenadas manuais       ex: -5.0823, -42.8012

Zoom do mapa        19 = rua c/ POIs | 18 = quadra | 17 = bairro
Delay entre tiles   300ms = rápido   | 1000ms = seguro
Sobreposição        10% recomendado
Nome da sessão      ex: teresinabairro1
```

**Saída:**
```
capturas/
└── teresinabairro1/
    ├── session.json       configuração + metadados da sessão
    └── tile_*.png         screenshots do Maps (um por posição)
```

---

## Etapa 1 — `detect_crops.py` (Python)
**O que faz:** Varre todos os tiles PNG, detecta ícones coloridos do Google
Maps via OpenCV (HoughCircles + HSV) e gera um recorte 360×70px centralizado
em cada ícone. Converte a posição do pixel para coordenada geográfica (lat/lng).

**Comando:**
```
py detect_crops.py capturas/teresina1/session.json
```

**Opções:**
```
--max-tiles 10    limita tiles processados (testes rápidos)
```

**Saída:**
```
capturas/teresinabairro1/
├── crops.json             lista de todos os ícones detectados com lat/lng
└── crops/
    ├── crop_*.jpg         recortes 360×70px de cada ícone
    └── resumo.html        visualização dos recortes
```

---

## Etapa 2 — `ocr_pois.py` (Python)
**O que faz:** Aplica EasyOCR em cada recorte para extrair o nome do
estabelecimento. Usa sistema de eleição por âncora (fragmento mais próximo
do ícone com maior confiança) para determinar o lado correto do label e
filtrar texto de markers vizinhos.

**Comando:**
```
py ocr_pois.py capturas/teresina1/session.json
```

**Opções:**
```
--max-crops 50     limita crops processados (testes rápidos)
--max-dist-x 120   janela horizontal em px a partir do ícone (default: 120)
--min-conf 0.40    confiança mínima do EasyOCR (default: 0.40)
```

**Saída:**
```
capturas/teresinabairro1/crops/
├── ocr_resultado.json     crops + campo ocr_texto para cada ícone
└── ocr_resumo.html        visualização com texto extraído
```

---

## Etapa 3 — `search_pois.py` (Python)
**O que faz:** Para cada POI com OCR válido (≥ 5 chars), abre o Google Maps
via Playwright em até 10 abas paralelas, pesquisa pelo nome e extrai todos
os dados do painel lateral. Calcula a distância entre a coordenada original
do ícone e a retornada pelo Maps — match válido se ≤ 100m.

**Dados extraídos:** nome oficial, categoria, avaliação média, total de
avaliações, endereço, telefone, website, horários da semana, plus code.

**Comando:**
```
py search_pois.py capturas/teresina1/session.json --workers 5
```

**Opções:**
```
--workers 10      abas paralelas no Playwright (default: 10)
--max-dist 100    distância máxima em metros para match válido (default: 100)
```

**Status possíveis:**
```
ok               match válido — distância ≤ max-dist
distancia_alta   encontrou mas coordenada diverge — vai para recover
nao_encontrado   Maps não retornou resultado — vai para recover
ocr_curto        OCR < 5 chars — vai para recover
erro: ...        falha técnica durante a busca
```

**Saída:**
```
capturas/teresinabairro1/crops/
├── search_resultado.json
└── search_resumo.html
```

---

## Etapa 4 — `recover_pois.py` (Python)
**O que faz:** Pega todos os itens que falharam no search e tenta recuperação
alternativa: abre o Maps diretamente na coordenada do ícone (zoom 19) e
clica em 9 pontos ao redor (centro + cruz + diagonais a ±30px) tentando
abrir o painel de algum POI próximo. Mescla os recuperados com os válidos
do search gerando o dataset final completo.

**Comando:**
```
py recover_pois.py capturas/teresinabairro1/session.json
```

**Opções:**
```
--workers 10      abas paralelas no Playwright (default: 10)
--max-dist 100    distância máxima em metros para match válido (default: 100)
```

**Saída:**
```
capturas/teresinabairro1/crops/
├── recover_resultado.json   ← dataset final completo (válidos + recuperados)
└── recover_resumo.html
```

---

## Estrutura completa após pipeline concluído

```
capturas/
└── teresinabairro1/
    ├── session.json
    ├── tile_*.png
    ├── crops.json
    └── crops/
        ├── resumo.html
        ├── crop_*.jpg
        ├── ocr_resultado.json
        ├── ocr_resumo.html
        ├── search_resultado.json
        ├── search_resumo.html
        ├── recover_resultado.json    ← DATASET FINAL
        └── recover_resumo.html
```

---

## Dependências

**Node (etapa 0):**
```
npm install
```

**Python (etapas 1–4):**
```
pip install opencv-python numpy easyocr playwright --break-system-packages
playwright install chromium
```

**Python (v2 otimizada — etapas 3-4):**
```
pip install scikit-learn playwright-stealth python-dotenv aiohttp
```

---

# Versão v2 otimizada (Camada 2 — coleta rica)

`search_pois_v2.py` e `recover_pois_v2.py` substituem as etapas 3 e 4 com
otimização agressiva de banda (~70% menos) e prolongamento da vida útil dos
IPs. Os arquivos v1 (`search_pois.py`, `recover_pois.py`) seguem intactos como
backup. **O schema de saída é idêntico** — o resto do pipeline (incl.
`enrich_pois.py`) continua funcionando sem alteração.

## O que muda em relação ao v1

| Aspecto              | v1                         | v2                                        |
|----------------------|----------------------------|-------------------------------------------|
| Paralelismo          | 10 abas, browser único     | 10 workers, browser persistente por lote  |
| Trabalho por unidade | POI solto                  | Lote de 8-15 POIs geograficamente vizinhos |
| Proxies              | ponte local (instável)     | proxy nativo Playwright, 1 IP estático/lote |
| Banda                | ~6-10 MB/POI               | < 4 MB/POI (alvo); ~2-3 MB típico         |
| Bloqueio de recursos | nenhum                     | CSS/font/media/telemetria/avatars         |
| Humanização          | delay fixo curto           | cadência 5-15s + pausas longas + stealth  |
| Regionalização       | nenhuma                    | DBSCAN (eps≈300m) agrupa por bairro        |
| recover              | Google Places API (paga)   | scraping puro (sem custo de API)           |

## Como configurar o `.env`

Copie `.env.example` para `.env` e preencha:
```
WEBSHARE_API_KEY=sua_chave_aqui
```
A chave fica no Webshare Dashboard → API → Keys. O `.env` está no `.gitignore`
e **nunca** deve ser commitado. Sem a chave, o pool cai para o cache local e
depois para `Webshare_100_proxies.txt` (fallback gracioso).

## Comandos

```
py search_pois_v2.py capturas/<sessao>/session.json --workers 10
py recover_pois_v2.py capturas/<sessao>/session.json --workers 10
```

Opções do search v2:
```
--workers 10     workers paralelos (máx. 10, calibrado p/ o hardware)
--max-dist 100   distância máxima (m) para match válido
--limit 30       processa só os N primeiros POIs (teste de aceitação)
```

## Arquitetura (módulos novos)

```
config.py              constantes + loader .env + listas de fingerprint
proxy_pool.py          carrega 100 IPs via API Webshare, cooldown, 1 IP/lote
spatial_clustering.py  DBSCAN espacial → lotes coesos de 8-15 POIs
human_browser.py       contexto Playwright humanizado + stealth + route block
user_agents.json       20+ user-agents reais (fingerprint diverso)
camada1_serp_TODO.py   stub da Camada 1 (validação SERP barata) — não implementada
```

Fluxo de cada worker: pega lote da fila → adquire 1 IP estático → abre browser
persistente com fingerprint coeso → processa os 8-15 POIs sequencialmente
(cadência humanizada, mesmo cache de JS do Maps) → fecha tudo, descarta cookies,
libera o IP → pega o próximo lote com IP/fingerprint novos.

## Quando usar v1 vs v2

- **v2**: padrão para qualquer coleta de média/grande escala. Economiza banda,
  distribui carga entre 100 IPs, comportamento humanizado.
- **v1**: fallback rápido sem proxies para volumes pequenos, ou se a API
  Webshare estiver indisponível e você quiser conexão direta.

## Métricas emitidas ao final

```
📦 POIs processados   total de POIs visitados
📡 Banda total        soma de response sizes interceptados (MB)
📉 Banda por POI      MB/POI médio (alvo < 4)
🔥 IPs queimados      IPs que entraram em cooldown (CAPTCHA/429)
🚫 Lotes com CAPTCHA  nº de lotes abortados + taxa
⏱  Tempo total        duração da coleta
```

## Estratégia de fotos

A galeria **não é baixada via proxy**. O scraper extrai apenas as URLs
(`lh3.googleusercontent.com/...`) e salva no JSON. O download das imagens é
uma etapa separada, feita direto do IP do servidor (a CDN do Google é leve e
não exige proxy) — economiza banda cara do plano residencial.

## Troubleshooting

| Sintoma                              | Causa / solução                                             |
|--------------------------------------|-------------------------------------------------------------|
| `WEBSHARE_API_KEY ausente no .env`   | Crie o `.env` a partir do `.env.example` e cole a chave.    |
| `Nenhuma fonte de proxies`           | Sem chave, sem cache e sem `Webshare_100_proxies.txt`. Forneça ao menos um. |
| `🚫 CAPTCHA no lote`                  | IP entrou em cooldown 2h automaticamente; o lote é re-enfileirado com outro IP. Se frequente, baixe `--workers` ou aumente os delays em `config.py`. |
| Banda > 4 MB/POI                     | Verifique se o route blocking está ativo (layer="maps"); avatars/telemetria devem ser abortados. |
| Lentidão                             | IPs estáticos são US-based; latência maior até o Google. Normal. Os delays humanizados também somam tempo (proposital). |
| Incoerência geo (IP US + locale BR)  | Esperado com o plano atual (IPs US). `locale=pt-BR` é mantido p/ resultados em português. Para coerência total seria preciso plano de IPs BR. |

> **Nota:** `recover_pois_v2.py` usa scraping puro (sem Places API). Se você
> precisa do enriquecimento via API paga, use `enrich_pois.py` (etapa separada).
