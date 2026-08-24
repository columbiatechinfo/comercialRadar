# Guia de Setup — Skills de Design + LLM Local no DGX Spark

Guia prático em duas partes, para seguir na máquina:

- **Parte A** — instalar as duas skills de design de frontend (`taste-skill` e `ui-ux-pro-max`).
- **Parte B** — estruturar sua LLM local (Open WebUI + visão de imagem + contexto do Obsidian via RAG).

As partes são independentes: pode fazer A e B em qualquer ordem.

---

## PARTE A — Skills de design de frontend

Objetivo: dar "bom gosto" e referências de design ao seu agente de código (Claude Code, Cursor, etc.), reduzindo o visual genérico de IA.

Estratégia recomendada (do que combinamos):
- **`taste-skill`** = camada padrão de gosto, sempre ativa. Mais leve, só instruções.
- **`ui-ux-pro-max`** = banco de referências consultável, usado sob demanda. Inclui React Native (seu caso).

Ambas são MIT e não executam nada perigoso (`ui-ux-pro-max` roda só um `search.py` em Python stdlib, sem chamadas de rede).

### A.0 — Pré-requisitos

```bash
node --version     # precisa de Node.js (para o npx / npm)
python3 --version  # precisa de Python 3.x (para o search.py da ui-ux-pro-max)
```

Se faltar Node, instale a versão LTS mais recente de https://nodejs.org e reabra o terminal.

### A.1 — Instalar a `taste-skill` (Leonxlnx/taste-skill)

Versão v2 (padrão atual, recomendada):

```bash
npx skills add https://github.com/Leonxlnx/taste-skill --skill "design-taste-frontend"
```

Se você depender do comportamento exato da v1:

```bash
npx skills add https://github.com/Leonxlnx/taste-skill --skill "design-taste-frontend-v1"
```

Alternativa manual (sem CLI): baixe o `SKILL.md` da pasta da skill no repositório e copie para a pasta de skills do seu agente, por exemplo `.claude/skills/design-taste-frontend/SKILL.md`. Depois reinicie/recarregue o agente para ele descobrir a skill.

> Observação: a v2 está marcada como **experimental** (ainda iterando). O nome de instalação `design-taste-frontend` permanece estável; só o texto das regras pode mudar antes da v2.0.0 estável.

### A.2 — Instalar a `ui-ux-pro-max` (nextlevelbuilder/ui-ux-pro-max-skill)

O pacote npm é **`ui-ux-pro-max-cli`** e instala o comando **`uipro`** (não use os pacotes antigos `uipro-cli`, estão obsoletos).

Instale o CLI globalmente:

```bash
npm install -g ui-ux-pro-max-cli
```

Instale a skill — duas opções:

**Opção 1 — por projeto** (recomendado quando quer sob demanda):
```bash
cd /caminho/do/seu/projeto
uipro init --ai claude       # instala em .claude/skills/ do projeto
```

**Opção 2 — global** (fica disponível em todos os projetos):
```bash
uipro init --ai claude --global   # instala em ~/.claude/skills/
```

Comandos úteis do CLI:
```bash
uipro versions          # lista versões disponíveis
uipro update            # atualiza os arquivos da skill a partir do pacote instalado
uipro update --global   # o mesmo, para a instalação global
uipro uninstall         # remove a skill (auto-detecta a plataforma)
```

Alternativa dentro do Claude Code (plugin marketplace):
```text
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
```

### A.3 — Como usar as duas juntas (sem conflito)

1. Deixe a **`taste-skill` como padrão contínuo** (global). Ela vira a camada de gosto sempre ativa.
2. Use a **`ui-ux-pro-max` sob demanda**, invocando quando precisar de referência concreta — paleta, estilo por stack, tipo de gráfico. Exemplo de chamada do buscador dela:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/.claude/skills/ui-ux-pro-max/scripts/search.py" "beauty spa wellness" --domain styles
   # se "python" não existir: tente python3, depois py -3
   ```
3. **Evite deixar as duas como "global sempre ativa" ao mesmo tempo** — economiza contexto e evita orientações que se sobrepõem.

### A.4 — Verificar a instalação

- Reinicie/recarregue o agente (Claude Code, Cursor, etc.).
- Confirme que os diretórios existem:
  ```bash
  ls -la ~/.claude/skills/         # para instalações globais
  ls -la ./.claude/skills/         # para instalações por projeto
  ```
- Peça algo de UI ao agente e veja se ele aplica as regras de design.

---

## PARTE B — LLM local (Open WebUI + visão + Obsidian via RAG)

Modelo mental — três camadas sobre o modelo que já roda:

```
serving (API OpenAI-compatible)  →  interface de chat (Open WebUI)  →  contexto (RAG do vault)
```

Contexto de hardware: o DGX Spark é **ARM64 (Grace) + Blackwell**, então use imagens de container **arm64** (o Open WebUI publica multi-arch; roda normalmente). Os 128 GB de memória unificada acomodam o modelo 32B + embedding + reranker juntos, sem aperto.

### B.1 — Camada 1: validar o serving (só conferir)

Seu modelo 32B de visão já está servindo. Confirme dois pontos:

1. **Endpoint compatível com a API da OpenAI** ativo, ex.: `http://localhost:8000/v1` (vLLM/SGLang/TGI/Ollama expõem isso).
   ```bash
   curl http://localhost:8000/v1/models
   ```
2. **Visão no formato OpenAI** — o servidor precisa aceitar imagem via `image_url` (base64) no payload de chat. Se subiu o Qwen3-VL-32B com o template multimodal correto, já está OK. É esse formato que o Open WebUI usa para enviar as imagens anexadas no chat.

Anote o **host:porta** e a **base URL** (`.../v1`) — você vai usar já a seguir.

### B.2 — Camada 2: subir o Open WebUI (chat + imagem)

```bash
docker run -d -p 3000:8080 \
  -v open-webui:/app/backend/data \
  -v /caminho/do/seu/vault:/data/vault:ro \
  -e OPENAI_API_BASE_URL=http://SEU_HOST:8000/v1 \
  -e OPENAI_API_KEY=qualquer-coisa \
  --name open-webui --restart always \
  ghcr.io/open-webui/open-webui:main
```

Notas:
- A linha `-v /caminho/do/seu/vault:/data/vault:ro` monta seu vault do Obsidian **somente leitura** dentro do container (usaremos na Parte B.4).
- `OPENAI_API_KEY` pode ser qualquer texto se seu servidor não exige chave.

Acesse `http://localhost:3000`, crie a **conta admin** (a primeira conta criada é o administrador). Seu modelo já aparece no seletor. Anexar imagem no chat passa a funcionar automaticamente, porque o modelo é de visão.

### B.3 — Camada 3: modelos de embedding e reranker (via Ollama)

O RAG precisa de um **modelo de embedding** separado do modelo de chat. Como você escreve em **português**, use modelos **multilíngues** (não o `all-MiniLM` inglês padrão).

Instale o Ollama (build arm64 + CUDA para o Spark) e baixe os modelos:

```bash
# embedding multilíngue forte (ótimo em PT)
ollama pull bge-m3

# reranker multilíngue (o "segredo" da qualidade do RAG)
ollama pull bge-reranker-v2-m3
```

Alternativa de embedding igualmente boa e recente: **`Qwen3-Embedding`** (0.6B / 4B / 8B, conforme o quanto quer investir de memória).

### B.4 — Configurar o RAG e conectar o vault do Obsidian

No Open WebUI, faça os ajustes em **Admin → Settings → Documents**:

1. **Embedding**: mude o *engine* para **Ollama** e informe o nome exato do modelo: `bge-m3`.
2. **Reranking**: ative e informe `bge-reranker-v2-m3`.
3. **Hybrid Search**: ative (`ENABLE_RAG_HYBRID_SEARCH`) — combina BM25 (palavra-chave) + vetor. Importante para termos técnicos (nomes de função, códigos, siglas do OrbisGrid).
4. **Chunking**: use o splitter por header de markdown (`MarkdownHeaderTextSplitter`), que respeita os títulos `#`/`##` das suas notas. Para começar, `chunk size` ~1000–1500 tokens e `overlap` ~100 costumam ir bem em notas de Obsidian.
5. **Top K**: comece com 5. Suba para 8–10 só se as respostas ficarem incompletas, sempre respeitando a janela de contexto do modelo.

Agora crie a base de conhecimento a partir do vault:

1. Vá em **Workspace → Knowledge → + Create Knowledge Base**. Dê um nome (ex.: `vault-obsidian`).
2. Use **Sync Directory** apontando para o vault montado no container: `/data/vault`.
3. Aguarde a indexação (roda uma vez; reindexa nas próximas sincronizações).

Uso no chat:
- Digite **`#`** seguido do nome da base para dar o contexto do vault à pergunta, ex.:
  ```text
  #vault-obsidian qual foi minha decisão sobre a arquitetura multi-cloud do OrbisGrid?
  ```
- Diferença importante: **Knowledge** usa recuperação (RAG) sob demanda — certo para vault grande. **Notes / Full Context** injeta o conteúdo inteiro em toda mensagem — use só para notas curtas que devem estar SEMPRE presentes.

### B.5 — (Opcional) Chat dentro do próprio Obsidian

Para perguntas rápidas sem sair das notas, instale no Obsidian um plugin de chat (ex.: **Copilot for Obsidian** ou **Smart Connections**) e aponte-o para o **mesmo endpoint OpenAI-compatible** (`http://SEU_HOST:8000/v1`). O Open WebUI continua sendo o hub principal (visão + RAG); o plugin é só o atalho in-note. A parte de imagem fica mais fraca nesse plugin.

### B.6 — (Opcional) Auto-reindexar o vault quando as notas mudam

Vault vivo precisa reindexar, senão o RAG responde com a versão antiga. Um watcher simples resolve. Pré-requisito: `inotify-tools`.

```bash
sudo apt-get install -y inotify-tools
```

Script (`watch_vault.sh`) — ajuste `VAULT`, `OWUI`, `KB_ID` e `API_KEY`:

```bash
#!/usr/bin/env bash
VAULT="/caminho/do/seu/vault"
OWUI="http://localhost:3000"
KB_ID="COLE_AQUI_O_ID_DA_KNOWLEDGE_BASE"   # veja na URL da base no Open WebUI
API_KEY="COLE_AQUI_SUA_API_KEY"            # Settings → Account → API Keys

echo "Observando $VAULT ..."
while inotifywait -r -e modify,create,delete,move "$VAULT" >/dev/null 2>&1; do
  echo "[$(date)] Mudança detectada — reindexando..."
  curl -s -X POST "$OWUI/api/v1/knowledge/$KB_ID/reindex" \
    -H "Authorization: Bearer $API_KEY" >/dev/null
  # debounce simples: espera 10s para agrupar edições em rajada
  sleep 10
done
```

```bash
chmod +x watch_vault.sh
./watch_vault.sh
```

> O caminho exato do endpoint de reindex pode variar conforme a versão do Open WebUI. Se `.../reindex` não existir na sua versão, confirme o endpoint correto em **Settings → Documents** (botão de re-sync) ou na doc da API do seu build, e troque a linha do `curl`. Para começar, dá para simplesmente clicar em "Sync/Re-index" na interface quando editar bastante.

### B.7 — Checklist de "pega-ratão"

- **Imagens ≠ vault.** A imagem anexada vai direto ao modelo de visão; o RAG é sobre o texto do vault. Convivem no mesmo chat, mas são pistas separadas.
- **Reindexar.** Editou muitas notas? Rode o Sync/Re-index.
- **arm64.** Use imagens de container arm64 no Spark.
- **Embedding multilíngue.** Não deixe no padrão inglês — `bge-m3` ou `Qwen3-Embedding`.
- **Tool-calling.** Se ligar o modo "agentic RAG" (`ENABLE_KB_EXEC=True`) e o modelo travar, desligue e volte ao RAG clássico.

---

## Resultado final

- **Parte A**: agente de código com bom gosto de design (`taste-skill` padrão + `ui-ux-pro-max` sob demanda).
- **Parte B**: um "ChatGPT privado" no Spark, com histórico, upload de imagem (visão) e acesso às suas notas do Obsidian via RAG — tudo local.
