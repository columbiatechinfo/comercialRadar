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
