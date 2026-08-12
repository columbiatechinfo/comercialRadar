# ADR 0004 — Um schema por ferramenta, no mesmo banco

- **Data:** 12/08/2026
- **Estado:** aceita e aplicada
- **Decisores:** Columbia Tech

---

## Contexto

A pilha Supabase do i9 vai atender **todos** os sistemas da casa, não só o
comercialRadar. A pergunta que precisava de resposta antes do segundo sistema
chegar: banco por ferramenta, ou schema por ferramenta?

O comercialRadar entrou com as 9 tabelas no `public` do banco `postgres`. Isso
produziu, no mesmo dia, um problema concreto: o Supabase concede `SELECT` a
`anon` no `public` por padrão, e a chave `anon` é **pública por natureza** — vai
embutida no navegador. Medido em 12/08/2026: `GET /rest/v1/pois` com a chave anon
devolvia as linhas da base de POIs e do cadastro do cliente.

---

## Decisão

**Um schema por ferramenta, com o nome dela, no mesmo banco.** O `public` fica
vazio.

```
banco postgres
  auth · storage · realtime     do Supabase, instância inteira
  comercialradar                 as 9 tabelas do produto
  radartelhados                  quando chegar
  saletopsellers                 quando chegar
  public                         VAZIO
```

Cada ferramenta recebe três papéis com o nome dela — `_migrator`, `_app`,
`_readonly` — com `USAGE` **apenas** no schema próprio e `search_path` apontando
para ele.

**Duplicar dado entre ferramentas é aceitável**, e é decisão explícita do dono do
produto: individualidade e segurança valem mais que a economia de não repetir uma
tabela.

---

## Consequências

**Cruzar dado entre ferramentas continua possível.** É a razão de não separar por
banco: `select … from comercialradar.pois join radartelhados.telhado …` funciona
com um `GRANT`, sem federação e sem cópia. Bancos separados exigiriam
`postgres_fdw` para o mesmo efeito, com o dado atravessando a rede.

**Acesso cruzado é explícito, nunca padrão.** Nenhum papel enxerga o schema de
outra ferramenta. Provado: `comercialradar_app` recebe
`permission denied for schema radartelhados` ao tentar. Quando o cruzamento for
necessário, o `GRANT` entra e é registrado em ADR — o compartilhamento fica
possível **e visível**.

**A identidade é compartilhada; a autorização é individual.** O `auth.users` do
GoTrue é único por instância e não se duplica por ferramenta sem subir uma pilha
inteira por sistema. O que fica no schema de cada uma é o perfil e o papel: quem
é essa pessoa *dentro daquele sistema*. Na prática é o comportamento desejado —
entra uma vez, e cada ferramenta decide sozinha o que pode.

**RLS ligada em todas as 9 tabelas, sem policy.** Isso significa que
`comercialradar_app` hoje lê **zero linhas** — RLS sem policy nega tudo para quem
não é dono. É o estado seguro. O pipeline segue funcionando porque conecta como
`postgres`, que é dono e passa por cima. As policies são trabalho da
`/modelo-acesso`, e o `GRANT` para `anon`/`authenticated` vem **junto** com elas.

**Um banco continua sendo um domínio de falha só.** Consulta descontrolada de um
sistema afeta os outros. Mitigado em parte por ter tirado o analítico pesado dali
([ADR 0003](0003-onde-mora-cada-banco.md)); o que resta é limitar conexão por
papel quando houver mais de um sistema em produção.

---

## Alternativas descartadas

**Banco por ferramenta.** Isolamento mais forte e domínio de falha separado.
Descartada porque mataria o cruzamento entre sistemas — que é requisito — e
duplicaria o cadastro de usuários, já que `auth.users` é por instância.

**Continuar no `public` com RLS resolvendo tudo.** Menos trabalho agora.
Descartada pelo que aconteceu: o `public` já vem com `GRANT` para `anon`, e
depender de lembrar de revogar em toda tabela nova é depender de disciplina onde
dá para depender de estrutura.

**Um schema `plataforma` com tenants e usuários comuns.** Era a minha proposta:
evitaria duplicar empresa e perfil em cada ferramenta. Descartada pelo dono do
produto em favor da individualidade — cada sistema com o seu, mesmo repetindo.
