# ADR 0002 — Prisma é legado, não o caminho de dados

- **Data:** 12/08/2026
- **Estado:** aceita
- **Decisores:** Columbia Tech

---

## Contexto

O repositório tem `prisma/schema.prisma`, `package.json` com `prisma@6` e
`@prisma/client@6` em `devDependencies`, e uma migration `20260630173701_init`.
O README exibe o selo "PostgreSQL · Prisma 6" e a documentação diz "banco
gerenciado por Prisma".

Nada disso descreve o sistema atual:

- o schema declara **6 modelos** — `Poi`, `ImageUrl`, `StreetviewImg`,
  `AnaliseIa`, `Comentario`, `HorarioFuncionamento`;
- o banco tem **38 tabelas**. `ibge_cnefe`, `ibge_malha`, `osm_via`, `rf_*`,
  `cadastro_cliente`, `cnefe_coletiva`, `fachada_anotacao`, `area_trabalho` e as
  tabelas de quadra foram criadas por Python, com `CREATE TABLE IF NOT EXISTS`;
- a última migration é de **30/06/2026** e não acompanha o banco desde então;
- nenhum código Python importa Prisma. Todo acesso é `psycopg2` em `base_comum.py`.

Há ainda a regra do `Sistemas/CLAUDE.md`: **Prisma está descartado** como padrão
da casa, porque não propaga claims do JWT e exigiria contorno com `SET LOCAL`.

---

## Decisão

O caminho de dados é **`psycopg`**. O Prisma fica como **legado inerte**: não é
usado para migrar, gerar cliente ou validar schema, e nenhuma tabela nova passa
por ele.

Não será removido agora. A migration inicial é o registro histórico de como as
seis primeiras tabelas nasceram, e apagá-la não devolve nada.

---

## Consequências

**A DDL vive no Python que cria cada tabela.** É o que já acontece na prática —
`esquema(con)` em cada módulo, idempotente. A contrapartida honesta: não existe
histórico de migração versionado, e uma mudança de coluna feita à mão no banco não
deixa rastro no repositório.

**O README mente hoje.** O selo "Prisma 6" e a frase "gerenciado por Prisma"
precisam sair, senão quem chegar ao projeto procura o schema para entender o banco
e encontra 16% dele.

**Se um dia houver migrations de verdade**, a escolha natural é uma ferramenta que
fale SQL direto — não reativar o Prisma, que teria de aprender 32 tabelas de uma
vez para virar fonte da verdade.

---

## Alternativas descartadas

**Trazer as 38 tabelas para o schema.prisma** — daria histórico versionado, ao
custo de manter em dia um schema que ninguém usa para acessar o banco, e de
introduzir o problema de RLS que a regra da casa já identificou.

**Apagar `prisma/` e o `package.json`** — o `package.json` ainda declara o
Playwright e o `sharp` usados pela captura em TypeScript. Apagar junto quebraria a
captura; apagar só o Prisma é limpeza que pode esperar por um PR próprio.
