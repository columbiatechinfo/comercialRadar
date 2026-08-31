# O servidor A2L, conferido em 30/08/2026

Este arquivo é o retrato do ambiente **medido**, não do ambiente presumido. Ele
existe porque quase toda decisão de migração que eu tomei antes de olhar estava
errada de alguma forma — e as correções estão marcadas abaixo.

Acesso: `ssh i9` (alias no `~/.ssh/config`), usuário `a2l`, chave que vive só no
pendrive. `HOME=/home/a2l`.

---

## 1 · O que eu presumi errado

| presumi | é |
|---|---|
| o padrão de implantação é `systemd` | é **Docker Compose**, um `compose.<servico>.yml` por serviço em `~/stack/`, com Caddy na frente |
| operação por unidade/timer | **cron** do usuário `a2l` |
| este é o primeiro sistema a migrar | já existem 7 schemas de ferramenta; o `core` está inteiro |
| eu definiria o layout | o layout já está posto; eu sigo |
| backup ainda por fazer | já roda: `~/stack/backup/backup.sh` às 03:17, `pg_dump -d a2l` + `pg_dumpall --roles-only` |
| `auth.uid()` é mais forte que `app.tenant_id` | é o **mesmo mecanismo** — `current_setting('request.jwt.claim.sub')`, um GUC de sessão |

---

## 2 · Serviços e portas

Blocos do doc 17: faixa 7000–7999, 100 por categoria, incremento de 10.

| serviço | porta | como sobe |
|---|---|---|
| `supabase-pooler` sessão | 7100 | contêiner (5432 interno) |
| `supabase-pooler` transação | 7110 | contêiner (6543 interno) |
| `supabase-db` | — | **não publica porta**; só pela rede do Docker |
| Supabase gateway (Kong) | 7120 | contêiner |
| `api-recursos` | 7700 | `compose.api-recursos.yml` |
| `api-identidade` | 7710 | `compose.api-identidade.yml` |
| **Radar Comercial — API** | **7720** | a escrever |
| `frontend` genérico | 7800 | `compose.frontend.yml`, nginx:1.27-alpine |
| **Radar Comercial — frontend** | **7810** | a escrever |
| **Radar Comercial — captura** | **7910** | a escrever |
| vLLM (Spark) | 192.168.3.20:7400 | só alcançável por dentro do servidor |

Nenhum serviço publica em `0.0.0.0`. O molde é sempre:

```yaml
ports:
  - "127.0.0.1:PORTA:PORTA"
  - "${BIND_LAN:-127.0.0.1}:PORTA:PORTA"
```

Mais `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `mem_limit`,
`healthcheck` e log rotacionado em 20 MB × 5.

**A exceção que este sistema precisa** — decidida em 30/08/2026: a API fica no
molde, e a mineração vai para um **segundo contêiner** com a imagem do
Playwright, sem `read_only`, com volume para `capturas/` e `crops/` e
`shm_size` grande. Dez Chromium não cabem num contêiner somente-leitura. A
consequência é que a API deixa de dar `Popen` e passa a **enfileirar** o job;
o minerador consome.

---

## 3 · O banco

Um banco (`a2l`), um schema por sistema.

```
schemas: auth  core  extensions  graphql  realtime  storage  vault
         resources_root                       ← base pública, leitura para todos
         radar_comercial  radar_coletivas  radar_telhados
         orbis_grid  gerador_coords_rotas  a2l_gcp
```

**Nenhum schema de ferramenta tem tabela ainda.** O `radar_comercial` existe e
está vazio; o Radar Comercial é o primeiro a povoar.

### Papéis

`migrator` (dono das tabelas, só migrações) · `app_user` (runtime, sem posse e
sem `BYPASSRLS`) · `readonly` · `authenticated`.

Existem também `app_dev` (o par de desenvolvimento, que não conecta no banco de
produção) e `resources_loader`, dono do `resources_root`.

**Nenhum papel disponível à aplicação tem `BYPASSRLS`** — só os da plataforma
(`postgres`, `service_role`, `supabase_admin`, `supabase_etl_admin`,
`supabase_read_only_user`), e nenhum deles é para uso do produto. O root atravessa empresas *dentro da
política*, por `core.eh_suporte()`. Isso resolve sozinho a pergunta de qual
papel o `root` do painel usaria: nenhum novo.

### Conexão

O Supavisor exige o tenant embutido no nome do usuário — `app_user.a2l`. Sem
isso responde `ENOIDENTIFIER`, que é erro longe da causa.

| quem | porta | por quê |
|---|---|---|
| API (7720) | 7110 · transação | muitas conexões curtas, uma transação por requisição — inclusive as temporárias `ON COMMIT DROP` do recorte por área |
| pipeline / ETL | 7100 · sessão | `COPY` longo, temp table entre transações e lock de sessão morrem em modo transação |

> **Divergência assumida do doc 23**, que diz *"Toda aplicação conecta pela 7110
> (modo transação), nunca direto na 7100"*. A regra vale para aplicação; o
> pipeline é lote. A decisão é de 30/08/2026 e está aqui para ser auditável, não
> escondida.

Variáveis no `.env` do servidor (valores escritos com `read -rs`, nunca por
chat), **as três conferidas conectando em 31/08/2026**:

| variável | papel | porta |
|---|---|---|
| `A2L_DB_URL` | `app_user.a2l` | 7110 |
| `A2L_MIGRATOR_URL` | `migrator.a2l` | 7110 |
| `A2L_PIPELINE_DB_URL` | `app_user.a2l` | 7100 |

**`inet_server_port()` devolve 7100 nas três**, e isso confundiu a conferência
por um tempo: não é a porta do host. O Postgres escuta na 7100 **dentro** do
contêiner (`POSTGRES_PORT=7100` no ambiente do pooler) — dois espaços de nome
diferentes com o mesmo número.

A separação de modos foi provada pela **configuração** (`POOLER_POOL_MODE`,
`mode_type=transaction` no tenant, e o mapeamento 5432/6543 padrão do
Supavisor), **não pelo comportamento**: com um cliente só e pool ocioso, o
Supavisor devolve o mesmo backend e um `SET` de sessão sobrevive nas duas
portas. A diferença aparece sob concorrência.

---

## 4 · Identidade

`core.tb_users` tem `id_empresa`, `id_nivel_user`, `email`, `name`, `ativo` —
não existe `tenant_id`. Níveis por `hierarquia`, não por id:

| id | código | hierarquia |
|---|---|---|
| 9 | root | 100 |
| 4 | administrator | 80 |
| 3 | supervisor | 60 |
| 2 | editor | 40 |
| 1 | user | 20 |

`core.tb_tools` já tem `radarComercial` com **id 1**.

### As três funções de contexto

Todas `STABLE SECURITY DEFINER`, com `search_path` fixo:

```sql
core.empresa_atual()  → select id_empresa from core.tb_users where id = (select auth.uid())
core.nivel_atual()    → select id_nivel_user  from core.tb_users where id = (select auth.uid())
core.eh_suporte()     → nível = 9
```

A forma da política, copiada do `core`:

```sql
core.eh_suporte() or id_empresa = core.empresa_atual()
```

com `hierarquia >= N` quando o nível importa. `FORCE ROW LEVEL SECURITY` em toda
tabela de negócio, e `id_empresa` como **primeira coluna de todo índice**.

Permissões no `radar_comercial`, conferidas em 31/08/2026 e **já corretas**:
`migrator` é dono e tem `CREATE`; `app_user` e `readonly` têm só `USAGE`.
`authenticated` **não tem nem `USAGE`** — se o frontend for ler por PostgREST
com `Accept-Profile: radar_comercial`, isso precisa ser concedido.

### O pipeline não tem quem seja

Ele é lote: não há login, e `auth.uid()` volta nulo — o que faria toda política
negar. Decidido em 30/08/2026: **cada empresa ganha um usuário de serviço em
`core.tb_users`** (`pipeline@<empresa>`, nível 1), e o pipeline declara o uuid
dele em `request.jwt.claim.sub`. As políticas do `core` funcionam sem alteração,
e `core.tb_auditoria` passa a registrar quem gravou cada POI.

---

## 5 · O que o schema `radar_comercial` precisa ter

Levantado do código, não das migrações — a decisão foi começar do zero. O
inventário está em [`inventario_schema.json`](inventario_schema.json):
**33 tabelas, 421 colunas**.

O risco dessa decisão era perder o que só existisse numa migração antiga. Foi
medido e **não existe**: das 22 tabelas que as 42 migrações criam, todas as 22
são usadas pelo código. A interseção fechou em zero.

As maiores: `pois` (53 colunas), `fachada_anotacao` (64), `cadastro_cliente`
(19), `cnefe_coletiva` (20), `ifood_merchant` (18).

Vão para `resources_root`, não para cá: `ibge_cnefe`, `ibge_malha`,
`cnefe_coletiva`, `rf_empresas`, `rf_estabelecimentos`, `rf_socios`, `rf_cnaes`,
`rf_municipios`.

Somem, substituídas pelo `core`: `tenants` → `core.tb_empresas`, `usuarios` →
`core.tb_users`, `auditoria` → `core.tb_auditoria`.

**O inventário não traz tipo de dado.** Tipo sai da leitura de quem grava cada
coluna, uma a uma — e é o trabalho que falta.

---

## 6 · O que não pode ser reconstruído

- **Os dados.** O banco antigo morreu com a máquina. POIs, imagens e
  anotações não têm backup alcançável.
- **O cadastro da Corsan** (102.065 ligações) precisa ser pedido de novo ao
  cliente — ele não é minerável.
- **`resources_root`** se refaz de fonte pública: CNEFE (FTP do IBGE por UF),
  malha do IBGE, CNPJ da Receita.
