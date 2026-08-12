# Infraestrutura — ComercialRadar

> Perfil detectado: **`wsl-dev`** (host Windows com WSL). Achados informam, nada
> bloqueia. Subida em 12/08/2026.

O dado é real, mesmo o perfil sendo de desenvolvimento. Onde isso muda o
julgamento, está dito.

---

## Onde tudo mora

| | |
|---|---|
| Máquina | **i9** — `100.115.117.49` no tailnet, Windows com WSL Ubuntu 24.04 |
| Recursos | 16 núcleos · 94 GB de RAM no WSL (128 no host) · **781 GB livres** |
| Diretório | `/home/orbisgrid/comercialradar-infra/pilha` |
| Volume do banco | `.../pilha/volumes/db/data` — **dentro do sistema de arquivos do WSL** |

O volume **nunca** vai para `/mnt/c`. A travessia entre o FS do WSL e o do
Windows destrói o I/O — é a diferença entre um banco usável e um que trava.

A escolha do i9 não foi preferência: o notebook tem **um disco só**, com 66 GB
livres, e o banco tem 66 GB. Dump mais restauração pediriam ~132 GB.

---

## O que subiu

Onze serviços, todos `healthy`. Versões fixas — nada de `:latest`, porque
atualização é ato deliberado, com backup antes.

| Serviço | Imagem |
|---|---|
| db | `supabase/postgres:17.6.1.136` |
| api-gw | `envoyproxy/envoy:v1.39.0` |
| rest | `postgrest/postgrest:v14.12` |
| auth | `supabase/gotrue:v2.189.0` |
| realtime | `supabase/realtime:v2.102.3` |
| storage | `supabase/storage-api:v1.60.4` |
| supavisor | `supabase/supavisor:2.9.5` |
| studio | `supabase/studio:2026.08.03` |
| meta · imgproxy · functions | `postgres-meta:v0.96.6` · `imgproxy:v3.30.1` · `edge-runtime:v1.74.0` |

```bash
cd /home/orbisgrid/comercialradar-infra/pilha
sh run.sh start | stop | status | logs [servico] | secrets
```

---

## Portas — e por que estas

O i9 já rodava uma pilha geo inteira. As portas foram escolhidas contra ela:

```
ocupadas antes:  5000-5003 OSRM/OpenTopoData · 5433 e 8080 Nominatim
                 8888 SearXNG · 11434 Ollama · 2322 e 9201 Photon
                 5432 (Windows) PostgreSQL 14 nativo, alheio a esta pilha
```

| Do tailnet | → WSL | O quê |
|---|---|---|
| `100.115.117.49:8000` | `:8000` | gateway — REST, Auth, Storage, Studio |
| `100.115.117.49:5442` | `:5432` | Postgres pelo Supavisor |
| `100.115.117.49:6543` | `:6543` | pooler em modo transação |

O encaminhamento escuta **só na interface do Tailscale**, e não em `0.0.0.0`
como as regras que já existiam ali: quem precisa alcançar a pilha está no
tailnet. A porta externa **5442** existe porque a 5432 do Windows já é do
PostgreSQL 14 nativo.

> **Fragilidade conhecida:** `netsh portproxy` aponta para o IP da distro
> (`192.168.226.17`), que **muda quando o WSL reinicia**. As regras antigas do i9
> têm o mesmo problema. O sintoma é "a pilha sumiu da rede" sem nada nos logs.
> Solução definitiva: `networkingMode=mirrored` no `.wslconfig` — exige
> `wsl --shutdown`, o que derruba Ollama, OSRM e Nominatim junto, então é
> mudança para uma janela combinada.

---

## Papéis de banco

Criados à mão: o Supabase não os cria, e são eles que tornam o RLS confiável.
Se a aplicação for **dona** da tabela ou tiver `BYPASSRLS`, toda política vira
decoração — e a `/seguranca` audita isso como crítico.

| Papel | Para | `superuser` | `bypassrls` |
|---|---|---|---|
| `cr_migrator` | migrations e CI | não | não |
| `cr_app` | runtime da aplicação | não | não |
| `cr_readonly` | relatório e BI | não | não |

Senhas no `.env` da pilha, com permissão `600`.

---

## Segredos

Gerados pelo `utils/generate-keys.sh` do próprio projeto, que conhece o formato
da versão instalada. Os valores de exemplo do repositório oficial são **públicos**
e varridos por scanner — subir com eles é deixar a porta aberta.

> Na primeira execução o `setup.sh` **imprimiu os segredos na saída**, e eles
> foram parar na transcrição da sessão. Como nada dependia deles ainda, todos os
> sete foram **regerados** antes de qualquer serviço nascer. Custo zero por ter
> sido pego na hora; caro se tivesse sido percebido depois.

---

## PostGIS — a diferença que decide a migração

```
origem (notebook)   PostgreSQL 16.11 · PostGIS 3.6.1
destino (pilha)     PostgreSQL 17.6  · PostGIS 3.3.7  (única na imagem)
```

Postgres 16 → 17 é o sentido que funciona. **PostGIS 3.6 → 3.3 é o contrário**, e
restauração para versão anterior do PostGIS não é caminho suportado. As tabelas
com geometria pesada — `ibge_cnefe`, `osm_via` — são justamente as que mais
sofrem com isso.

Ver [ADR 0003](adr/0003-onde-mora-cada-banco.md).

---

## Storage

`STORAGE_BACKEND` em arquivo local, dentro do volume da pilha. Em `wsl-dev` isso
basta. Em produção vai para o **R2** — egress zero, e o disco fica só para o
Postgres, porque disco cheio derruba o banco junto e imagem acumula rápido.

Hoje há **6,3 GB de imagem em `bytea` dentro do banco** (`streetview_imgs` e
`images_urls`). Esse é o candidato natural a sair para o Storage: backup,
restauração e replicação carregam esses bytes junto em toda operação.

---

## Backup — pendente, e é o item mais caro de adiar

Em `wsl-dev` a skill não obriga. **Não confundir com não precisar**: são 66 GB de
dado real, sendo ~340 MB insubstituíveis (POIs, cadastro do cliente, anotações de
fachada) que não se rebaixa de fonte pública nenhuma.

O que falta: `pgBackRest` com full semanal, incremental diário e WAL contínuo; e
**restauração testada em máquina limpa, com conferência de contagem de linhas**.
Backup nunca restaurado é hipótese, não backup.
