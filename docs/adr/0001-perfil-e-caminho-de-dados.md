# ADR 0001 — Perfil `saas-multi-cliente` e caminho de dados `psycopg`

- **Data:** 12/08/2026
- **Estado:** aceita
- **Decisores:** Columbia Tech

---

## Contexto

O ComercialRadar nasceu como ferramenta interna de operador único: roda em
`127.0.0.1:8765`, sem login, sem `tenant_id`, com um Postgres local de 38 tabelas
e 60 GB. Lendo só o código, a classificação automática seria `script-interno` —
não há autenticação nem banco de aplicação com usuários.

Essa leitura está certa sobre o **presente** e errada sobre o **produto**. O
sistema atende levantamentos para concessionárias — Aegea PI e Canoas/RS já estão
na base — e a intenção declarada é ter empresas clientes com usuários vinculados,
cada uma vendo apenas o próprio escopo, com mais de um nível de permissão.

---

## Decisão

**Perfil: `saas-multi-cliente`.**

Consequência imediata: `tenant_id`, RLS completa, matriz RBAC, módulos por flag em
tabela e E2E passam a ser **requisitos**, e aparecem no checklist como
`pendente` — não como `na`.

**Caminho de dados: `psycopg`.**

O sinal automático apontaria `prisma`, porque `prisma/schema.prisma` existe. Não é
o caminho real: o schema cobre 6 das 38 tabelas e sua última migration é de
30/06/2026. Detalhado no [ADR 0002](0002-prisma-legado.md).

---

## Consequências

**O checklist nasce vermelho, e é para nascer.** Marcar `na` em RBAC e RLS porque
"hoje é local" produziria um painel verde sobre um sistema que não isola cliente
nenhum. A distância entre o perfil e o código é a informação mais útil que este
documento carrega.

**A ordem de implantação tem uma dependência dura.** `tenant_id` precisa entrar
**antes** de a base crescer mais: retrofitar coluna de tenant em `pois` (26 mil),
`cadastro_cliente` (102 mil) e nas anotações é barato hoje e caro depois — e cada
índice existente precisa ser recriado com `tenant_id` na frente.

**Worker de confiança, não alcançável pelo browser.** Com `psycopg`, o pipeline
Python roda com papel próprio e `SET LOCAL` explícito, ou com `BYPASSRLS`
consciente. O que o browser alcança é a API, nunca o banco direto.

**Se a decisão fosse `script-interno`**, o ganho seria não escrever RBAC nem RLS
— e o custo seria descobrir na primeira venda que dois clientes compartilham
tabela sem isolamento, com a base já grande.

---

## Alternativas descartadas

**`script-interno`** — descrevia fielmente o código de hoje e produziria a menor
burocracia. Descartada porque congelaria a arquitetura no uso de um operador só, e
a introdução de tenant depois custa mais que agora.

**`app-single-tenant`** — atenderia se o painel fosse apenas exposto a mais gente
da Columbia Tech. Descartada porque a segregação necessária é por **empresa
cliente**, não por usuário.

**Migrar a camada de dados para `supabase-js`** — descartada de saída. O pipeline
é Python com PostGIS e `execute_values` sobre tabelas de dezenas de GB; reescrever
camada de dados que funciona é risco sem retorno.
