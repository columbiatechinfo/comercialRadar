# 🗺️ ComercialRadar

Captura automatizada de screenshots do Google Maps em resolução 4K (3840×2160),
cobrindo toda uma área geográfica (cidade, bairro, região) com todos os pontos
comerciais (POIs) visíveis.

---

## 📁 Estrutura do projeto

```
comercialRadar/
├── src/
│   ├── index.ts          # CLI interativo (modo principal)
│   ├── capture.ts        # Motor de captura Playwright
│   ├── capture-n8n.ts    # Versão headless para o n8n
│   ├── geo.ts            # Busca de limites e cálculo de grid
│   ├── retry-failed.ts   # Reprocessa tiles falhos
│   └── types.ts          # Tipos TypeScript
├── capturas/             # Imagens geradas ficam aqui
│   └── <nome-sessao>/
│       ├── session.json  # Progresso e metadados
│       └── tile_r000_c000_-5.09000_-42.80000.png
├── n8n-workflow.json     # Workflow para importar no n8n
├── setup.ps1             # Instalação automática Windows
├── package.json
└── tsconfig.json
```

---

## 🚀 Instalação (primeira vez)

### Pré-requisito
- **Node.js 18+** — https://nodejs.org

### Instalar tudo automaticamente
Abra o PowerShell como administrador na pasta do projeto e execute:

```powershell
PowerShell -ExecutionPolicy Bypass -File setup.ps1
```

O script vai:
1. Verificar Node.js
2. Instalar o **n8n** globalmente
3. Instalar dependências npm do projeto
4. Baixar o **Chromium** (Playwright)
5. Criar atalhos na Área de Trabalho

---

## 🖥️ Uso — Modo Script Direto (mais simples)

```cmd
cd C:\Users\ceo\Documents\Sistemas\comercialRadar
npx ts-node src/index.ts
```

Ou clique no atalho **"ComercialRadar - Captura"** criado na Área de Trabalho.

O script vai perguntar:
1. **Busca automática** (digita "Teresina, Piauí") ou **coordenadas manuais**
2. Nível de zoom (padrão: 17)
3. Delay entre tiles
4. Nome da sessão

---

## 🤖 Uso — Modo n8n (agendamento automático)

### Iniciar o n8n
```cmd
n8n start
```
Ou clique no atalho **"ComercialRadar - n8n"** na Área de Trabalho.

### Abrir interface
http://localhost:5678

### Importar o workflow
1. Menu lateral → **Workflows**
2. Botão **Import from file**
3. Selecione `n8n-workflow.json`
4. Edite o node **"Configurar Sessão"** e ajuste a query da cidade
5. Ative o workflow (toggle no topo)

O workflow executa automaticamente de Segunda a Sexta às 06h.

---

## 🔁 Reprocessar tiles falhos

Se a captura foi interrompida ou alguns tiles falharam:

```cmd
npx ts-node src/retry-failed.ts capturas\<nome-sessao>\session.json
```

---

## ⚙️ Configurações importantes

### Nível de Zoom
| Zoom | Visão                          | POIs visíveis |
|------|--------------------------------|---------------|
| 15   | Cidade inteira (mais área)     | Não           |
| 16   | Bairros                        | Poucos        |
| **17** | **Ruas (recomendado)**       | **✔ Sim**     |
| 18   | Quarteirões (mais detalhado)   | ✔ Sim         |
| 19   | Edifícios                      | ✔ Muitos      |

### Delay entre tiles
- `300ms` — Rápido, risco maior de bloqueio pelo Google
- `600ms` — Balanceado (padrão)
- `1000ms+` — Mais seguro para áreas grandes

---

## 📊 Exemplo: Teresina-PI completa

- Zoom 17, overlap 10%, delay 600ms
- Estimativa: ~800–1200 tiles
- Tempo: ~2–3 horas
- Espaço em disco: ~3–5 GB

---

## 🗂️ Arquivo session.json

Salvo dentro de cada pasta de sessão:

```json
{
  "sessionName": "teresina_2024_01",
  "startedAt": "2024-01-15T06:00:00.000Z",
  "totalTiles": 950,
  "completedTiles": 948,
  "failedTiles": [...],
  "status": "completed",
  "config": { ... }
}
```

---

## ❓ Problemas comuns

**"Chromium não encontrado"**
```cmd
npx playwright install chromium
```

**"Muitos tiles bloqueados pelo Google"**
Aumente o delay para 1500ms e reduza o zoom para 16.

**"Erro ao buscar cidade"**
A busca usa a API Nominatim (OpenStreetMap). Tente ser mais específico:
- ❌ `Teresina`
- ✅ `Teresina, Piauí, Brasil`
