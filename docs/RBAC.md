# RBAC — ComercialRadar

> **Estado: implementado.** Os quatro níveis foram definidos pelo usuário em
> 13/08/2026 e substituem a proposta de cinco papéis de 12/08.
>
> Conferido no banco em **24/08/2026**: 48 usuários nos quatro níveis, 42
> empresas, 45 rotas autenticadas e nenhuma sem autenticação. **28 tabelas com
> `tenant_id`, todas com RLS, policy e gatilho** — as quatro últimas entraram
> pela migração [`0029`](../migrations/0029_rls_nas_quatro_que_ficaram_de_fora.sql).
> `tests/test_isolamento_tenant.py` prova o isolamento com o papel real da API
> (`comercialradar_app`, sem `BYPASSRLS`) e cobra a regra para toda tabela nova.
> Ver a seção 32 da [`DOCUMENTACAO.md`](../DOCUMENTACAO.md).

---

## A regra que vem antes da matriz

Todo usuário pertence a **uma empresa cliente**, e enxerga apenas dado dessa
empresa. Vale para POI, área de trabalho, cadastro importado, anotação de
fachada, oportunidade, dossiê e imagem — sem exceção de "só leitura" e sem
exceção para relatório agregado.

O isolamento é aplicado **no banco, por RLS**, não na camada de aplicação. Filtro
em `WHERE` do backend protege contra engano; RLS protege contra bug, contra rota
esquecida e contra o próximo desenvolvedor.

**Só o `root` atravessa empresas.**

---

## Os quatro níveis

| Nível | Escopo | Existe para |
|---|---|---|
| `root` | Todas as empresas, todas as ferramentas | Ver tudo, em qualquer schema e no banco de referência. Criar empresa e usuário. Aprovar importação de base. Único que enxerga a **procedência do dado** |
| `admin` | A própria empresa | Executar qualquer processo — mineração, enriquecimento, importação — e distribuir POIs para os supervisores. Pode exercer qualquer função abaixo |
| `supervisor` | A própria empresa, e só o que lhe foi enviado | Aprovar, reprovar ou devolver os pontos que o admin lhe atribuiu |
| `user` | A própria empresa | Ver. Não altera nada |

### O que `root` é, e o que não é

Decisão do usuário em 13/08/2026: `root` é papel de banco com **`BYPASSRLS` e
`USAGE` em todos os schemas**, mais acesso ao banco de referência.

Ele vê tudo — que é o pedido. O que ele **não** tem é poder de destruir a
instância: não apaga schema alheio, não cria papel, não desliga RLS. A diferença
importa no dia em que houver injeção de SQL ou bug numa rota: o estrago para em
leitura total, em vez de servidor perdido, e as outras ferramentas do mesmo banco
não vão junto.

Superusuário de verdade continua existindo — pelo terminal, para a pessoa, não
para a aplicação web.

---

## Matriz

`—` = sem acesso · `L` = leitura · `E` = escrita · `A` = ação de decisão

| Recurso | `root` | `admin` | `supervisor` | `user` |
|---|---|---|---|---|
| Empresas clientes | E | L (a própria) | — | — |
| Usuários da empresa | E | E | — | — |
| Usuários de outras empresas | E | — | — | — |
| **Procedência do dado** (IBGE, Google, BDGD, OSM) | L | — | — | — |
| Área de trabalho | E | E | L | L |
| Rodada de mineração | A | A | — | — |
| Rodada de enriquecimento | A | A | — | — |
| POI e seus dados | E | E | L | L |
| Imagens (foto, Street View) | L | L | L | L |
| Cadastro da empresa | E | E | L | L |
| Base enviada pelo cliente | E | E | — | — |
| **Aprovar importação de base** | A | — | — | — |
| Fila de aprovação — distribuir | E | E | — | — |
| Aprovar / reprovar / devolver POI | A | A | A | — |
| Dossiê | L | L | L | L |
| Gerar dossiê | A | A | A | — |
| Log de auditoria | L | L (da empresa) | — | — |

### Células que merecem explicação

**Procedência é a única linha em que `admin` não vê.** Requisito comercial: para
o cliente, a fonte somos nós. A procedência **continua gravada no banco**,
íntegra — o que muda é a exibição. Auditoria, reprocessamento e contestação
dependem dela existir.

**Ver é da empresa; opinar é do que lhe coube.** `supervisor` e `user` enxergam
todos os POIs da empresa. A atribuição não limita a VISTA — ela define a
**obrigação**: o que aquele supervisor precisa avaliar.

A diferença é prática. Quem decide sobre um ponto precisa olhar em volta: se o
vizinho já foi aprovado, se a rua inteira é comercial, se aquele CNPJ aparece em
outro endereço. Cegar o supervisor ao redor transforma cada decisão num palpite
sobre um ponto isolado.

O que a atribuição restringe é o **ato**: só decide o item que lhe foi enviado —
`/api/fila/{id}/decidir` responde 404 para item de outro. E `user` não decide
nada, de ninguém.

**`admin` também aprova.** O usuário foi explícito: admin pode ocupar qualquer
função abaixo. Ele não fica bloqueado esperando supervisor.

**`user` não gera dossiê.** Ler o dossiê é visualização; gerá-lo é produzir
documento comprobatório com o nome da empresa — isso é ato, não leitura.

---

## A fila de aprovação

Quem chega na fila: POI com **alta convergência** e recomendação da IA para
mudança de tipo cadastral — de cliente simples para atividade comercial.

1. O **admin** filtra ou seleciona uma área, marca vários POIs na lista geral e
   **envia aos supervisores que escolher**.
2. O **supervisor** decide, item a item:

| Ação | Exige | Produz |
|---|---|---|
| Aprovar | — | o **dossiê** do local |
| Reprovar | motivo **escrito** + motivo **genérico** de lista | registro do descarte, com causa |
| Devolver | observação do que impede, ou a edição necessária | volta ao fluxo, sem virar aprovação nem descarte |

O motivo genérico é o que vira estatística; o escrito é o que explica o caso que
nenhuma lista prevê. Um sem o outro perde metade da informação.

---

## Login e sessão

- **Cadastro público fechado.** Só `root` cria usuário. Já aplicado na `/infra`.
- Autenticação pelo **GoTrue** da pilha Supabase, que já está de pé.
- O `tenant_id` e o nível vêm **sempre do token verificado**, nunca do corpo da
  requisição. Vindo do corpo, o cliente escolhe qual empresa quer ler.
- **Tempo de sessão** definido no GoTrue (expiração do JWT + refresh). Sessão sem
  prazo é credencial permanente em máquina compartilhada.

---

## Auditoria

A matriz diz quem **pode**; a auditoria registra quem **fez**.

Tabela append-only, com trigger nas ações destrutivas e nas de decisão: aprovar,
reprovar, devolver, importar base, apagar POI, criar usuário. Em sistema
multi-cliente, o dia em que perguntarem "quem aprovou isso?" precisa ter
resposta — e o dossiê aprovado é documento que sai da empresa.
