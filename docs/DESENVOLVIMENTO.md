# Desenvolvimento e publicação

> Desde 14/09/2026. Antes disso, o diretório do repositório no i9 era ao mesmo tempo
> desenvolvimento e produção: a API era construída dele e os laços de Canoas liam os
> arquivos dele ao vivo — editar era publicar. Foi assim que uma mudança de nome no
> julgamento fez a checagem reprovar 6.883 aprovadas em produção.

## As duas cópias

| | Produção | Desenvolvimento |
|---|---|---|
| Código | `~/producao/radarComercial` — git worktree parado numa **tag** | `~/Documentos/sistemas/radarComercial` — branch `desenvolvimento` |
| API | `radar-comercial-api` · `127.0.0.1:7740` | `radar-comercial-api-dev` · `127.0.0.1:7741` |
| Endereço | `https://a2lsolucoes.com/seek/` e Tailscale `:8445` | Tailscale `https://a2l-server-main-i914hx-pc-1.tail7e301b.ts.net:8446/` |
| Banco | `a2l` | **o mesmo `a2l`**, leitura e escrita (decisão do dono do produto) |
| Laços de Canoas | montam os arquivos da cópia de produção | — |
| Runs de extração | despachadas pela API de produção | **recusadas** (`RADAR_AMBIENTE=desenvolvimento`) |
| `.env` | cópia, refeita a cada publicação | o arquivo real (fora do git) |
| Fora do git (`dados_externos/` 10 GB, proxies, `estado/`, `cache_ibge/`) | cópia, sincronizada a cada publicação | os originais |

**Mudou o `.env`, a lista de proxies ou uma base em `dados_externos/`?** A produção só vê
na próxima publicação (pode ser a mesma tag de novo: `bash scripts/publicar.sh <tag no ar>`).

**O banco é um só.** Tudo o que a tela de desenvolvimento grava — decisão oficial, usuário,
área — é gravação de produção. A separação é do código: mexer na API ou na tela aqui não muda o
que está no ar até publicar.

## O dia a dia

```bash
cd ~/Documentos/sistemas/radarComercial && git switch desenvolvimento
```

- **Tela** (`frontend/*`): o código entra montado no contêiner — salvou, F5 no endereço `:8446`.
  A SEEK é gerada por `frontend/seek.html`; se editar as peças, gere de novo antes.
- **API** (`*.py`): depois de salvar, `docker restart radar-comercial-api-dev`.
- **Dependência nova** (`requirements.txt`): reconstruir a imagem de desenvolvimento:

```bash
cd ~/Documentos/sistemas/radarComercial/deploy && docker compose -f compose.radar-comercial-api-dev.yml --env-file ../.env up -d --build
```

- **Migração**: o banco é o de produção. Aplicar **só depois de revisar** e de conferir quem
  lê a tabela na versão que está no ar — a coluna nova tem de ser compatível com o código antigo,
  que continua rodando até publicar.

## Publicar

```bash
cd ~/Documentos/sistemas/radarComercial && git switch master && git merge --ff-only desenvolvimento && git push origin master
```

```bash
git tag -a producao-AAAA-MM-DD -m "o que muda" && git push origin producao-AAAA-MM-DD
```

```bash
bash scripts/publicar.sh producao-AAAA-MM-DD
```

O script guarda a imagem no ar como `:anterior`, leva a cópia de produção para a tag, sincroniza
o que o git não leva, constrói a imagem nova e **ensaia** (`import server` nela, com o mesmo
compose só-leitura) antes de trocar o contêiner — se o ensaio falha, a API no ar não é tocada.
Depois confere `/api/saude`, `https://a2lsolucoes.com/seek/api/saude` e o prefixo da tela.
Histórico em `~/producao/PUBLICACOES.log`. O build leva ~5 min (a imagem embute os 10 GB de
`dados_externos/`); a troca, segundos.

> A primeira publicação (14/09/2026, 15h40) derrubou a API por ~3 min: `server.py` cria
> `areas/`, `mineracao/` e `malhas/` no import, pasta vazia não entra no git e a imagem é
> só-leitura. Voltou pelo `--voltar`; o script passou a criar as pastas e a ensaiar.

Voltar:

```bash
bash scripts/publicar.sh --voltar
```

## O que não se faz

- **Editar arquivo dentro de `~/producao/radarComercial`.** Ele só anda de tag em tag.
- **Rodar laço de produção (julgamento, busca, captura) a partir do desenvolvimento.** O banco é o
  mesmo: seria o código em teste gravando vereditos reais.
- **Mudar nome gravado no banco** (processo, motor, tipo) sem procurar quem lê o valor antigo —
  foi a causa do incidente de 14/09.
