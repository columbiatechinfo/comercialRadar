# RBAC — ComercialRadar

> **Estado: proposta.** Nada disto existe no código hoje — não há login, não há
> `tenant_id` e não há papel. Este documento é o alvo que a `/modelo-acesso`
> implementa e o `estado.json` cobra. Atualizado em 12/08/2026.

---

## A regra que vem antes da matriz

Todo usuário pertence a **uma empresa cliente**, e enxerga apenas dado dessa
empresa. Isso vale para POI, área de trabalho, cadastro importado, anotação de
fachada, oportunidade e imagem — sem exceção de "só leitura" e sem exceção para
relatório agregado.

O isolamento é aplicado **no banco, por RLS**, e não na camada de aplicação. Filtro
em `WHERE` do backend protege contra engano; RLS protege contra bug, contra rota
esquecida e contra o próximo desenvolvedor.

---

## Papéis

| Papel | Escopo | Existe para |
|---|---|---|
| `plataforma_admin` | Todas as empresas | Criar empresa cliente, criar usuário, ligar e desligar módulo por cliente. É o único papel que atravessa tenants — e por isso o único que precisa de trilha de auditoria própria |
| `gestor` | A própria empresa | Definir áreas de trabalho, importar o cadastro da empresa, ver dashboard, custo e retorno |
| `auditor` | A própria empresa | Aprovar, reprovar e mandar reanalisar achado. É quem transforma leitura em decisão |
| `operador` | A própria empresa | Disparar rodadas de extração e enriquecimento, acompanhar progresso |
| `leitor` | A própria empresa | Ver mapa, ficha e dashboard. Não dispara nada e não decide nada |

---

## Matriz

`—` = sem acesso · `L` = leitura · `E` = escrita · `A` = ação de decisão

| Recurso | `plataforma_admin` | `gestor` | `auditor` | `operador` | `leitor` |
|---|---|---|---|---|---|
| Empresas clientes | E | — | — | — | — |
| Usuários da empresa | E | E | — | — | — |
| Módulos por empresa | E | L | — | — | — |
| Área de trabalho | L | E | L | L | L |
| Cadastro do cliente (importar) | L | E | L | — | L |
| POIs e enriquecimento | L | L | L | E | L |
| Rodada de extração / IA | L | L | — | E | — |
| Anotação de fachada | L | L | L | L | L |
| Veredito do achado | L | L | **A** | — | — |
| Dashboard, custo e retorno | L | L | L | L | L |
| Chaves de API e segredos | E | — | — | — | — |

O `auditor` não dispara rodada e o `operador` não decide veredito. Separar as duas
coisas é o que impede que quem gerou o achado seja quem o aprova.

---

## Como o isolamento é escrito

Sob carga, política de RLS é avaliada **por linha**. As duas regras que decidem se
ela é viável ou se derruba o banco:

**Chamada de função dentro de subselect.** `(select auth.uid())` é avaliado uma
vez; `auth.uid()` solto é avaliado por linha da varredura.

```sql
-- viável
create policy poi_do_tenant on pois for select
  using (tenant_id = (select auth.jwt() ->> 'tenant_id')::uuid);
```

**`tenant_id` como primeira coluna de todo índice.** Sem isso, a RLS obriga a
varrer a tabela inteira para depois descartar. Numa tabela como `ibge_cnefe`, com
111 milhões de linhas, é a diferença entre milissegundos e minutos.

```sql
create index on pois (tenant_id, id);
create index on fachada_anotacao (tenant_id, poi_id);
```

---

## As tabelas de referência não são de ninguém

`ibge_cnefe`, `ibge_malha`, `osm_via`, `rf_*` são **dado público de referência**,
lidos por todos os clientes e gerados por nenhum. Não recebem `tenant_id` e não
recebem RLS de isolamento — recebem permissão de leitura.

Aplicar RLS de tenant nelas custaria a avaliação por linha em 111 milhões de
registros para proteger um dado que o IBGE publica na internet.
