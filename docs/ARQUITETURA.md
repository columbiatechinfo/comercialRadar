# Arquitetura — ComercialRadar

> Diagrama versionado no repositório e revisado no PR. Diagrama que vive fora do
> código morre em três semanas. Atualizado em 12/08/2026.

---

## Em dez linhas

Um backend **FastAPI** (`server.py`, porta 8765) dispara subprocessos Python que
capturam, mineram e enriquecem POIs, e transmite o progresso ao frontend por
**WebSocket**. O frontend é **Leaflet + OSM** com Google como base opcional.
Todo o processamento pesado fala com o **PostgreSQL + PostGIS** por `psycopg`,
sem ORM e sem camada REST no meio. As bases de referência — CNEFE do IBGE,
Receita Federal, OSM — moram no mesmo banco e são lidas, nunca geradas, por este
sistema. O **radarTelhados** compartilha esse banco e é dono das tabelas de
quadra e telhado.

---

## Entidades

O banco tem 38 tabelas. O diagrama traz as do domínio; as de referência
(`rf_*` com 34 GB, `ibge_cnefe` com 23 GB) aparecem como fonte, não como entidade
do produto.

```mermaid
classDiagram
    class Tenant {
        +uuid id
        +text nome
        +text cnpj
        ~ainda não existe~
    }
    class Usuario {
        +uuid id
        +uuid tenant_id
        +text papel
        ~ainda não existe~
    }
    class Poi {
        +int id
        +text nome
        +text endereco
        +float maps_lat
        +float maps_lng
        +text categoria
        +text cnpj
        +text origem
    }
    class CadastroCliente {
        +int id
        +text matricula
        +text numero
        +text categoria
        +int economias_agua
        102.065 linhas
    }
    class FachadaAnotacao {
        +int poi_id
        +text status
        +text uso_observado
        +text numero_lido
        +jsonb anotacao
        +jsonb oportunidades
    }
    class CnefeColetiva {
        +text coletiva_id
        +int qtd_observada
        +int qtd_inferida
        27.227 linhas
    }
    class StreetviewImg {
        +int poi_id
        +bytea dados
        +date data_captura
    }
    class ImageUrl
    class Comentario
    class HorarioFuncionamento
    class AreaTrabalho {
        +text nome
        +geometry poligono
    }

    Tenant "1" --> "*" Usuario
    Tenant "1" --> "*" AreaTrabalho : ainda não vinculado
    Tenant "1" --> "*" CadastroCliente : ainda não vinculado
    AreaTrabalho "1" --> "*" Poi : recorte
    Poi "1" --> "*" ImageUrl
    Poi "1" --> "*" StreetviewImg
    Poi "1" --> "*" Comentario
    Poi "1" --> "*" HorarioFuncionamento
    Poi "1" --> "0..1" FachadaAnotacao
    Poi "0..1" --> "0..1" CadastroCliente : vínculo por coordenada
    Poi "0..1" --> "0..1" CnefeColetiva : casamento a 35 m
```

`Tenant` e `Usuario` estão no diagrama marcados como inexistentes **de
propósito**: o perfil decidido é `saas-multi-cliente` e essas duas entidades são
o buraco entre o que o produto promete e o que o código tem hoje. Escondê-las
faria o diagrama mentir. Ver [CHECKLIST.md](CHECKLIST.md).

---

## Fluxo crítico

Descobrir um imóvel cadastrado como residencial onde funciona comércio — e levar
esse achado até uma decisão humana, com a evidência anexa.

```mermaid
sequenceDiagram
    autonumber
    actor Op as Operador
    participant API as server.py (FastAPI)
    participant Ext as Extração<br/>(captura · mineração · estadual)
    participant DB as PostgreSQL + PostGIS
    participant Enr as Enriquecimento em cascata
    participant RF as Receita Federal (local)
    participant IA as Modelo de visão
    actor Aud as Auditor

    Op->>API: define a área (select, clique ou polígono)
    API->>Ext: dispara a rodada
    Ext->>DB: grava POIs com coordenada, município e UF

    Note over DB: passo 2 — o que o cliente JÁ tem como comercial
    API->>DB: cruza POIs × cadastro_cliente por coordenada
    DB-->>API: já-comercial (não é ganho: vira complemento)

    Note over DB: passo 3 — a pré-lista
    API->>DB: casou com imóvel do cliente, mas a categoria diverge
    DB-->>API: pré-lista de divergência de tipo

    Note over Enr: passo 4 — validar antes de gastar modelo
    API->>Enr: POIs pobres da pré-lista
    Enr->>Enr: Maps → web (SearXNG) → Street View
    Enr->>RF: CNPJ, CNAE, situação, sócios
    RF-->>DB: dados empresariais
    Enr->>DB: fachada capturada + fotos

    Note over IA: passo 5 — a descrição que sustenta a decisão
    API->>IA: fachada limpa + contexto (sem o número do cadastro)
    IA-->>API: observação estruturada
    API->>API: valida na skill leitura-fachada-cadastral
    alt anotação reprovada no validador
        API->>DB: grava como reprovada, não gera achado
    else aprovada
        API->>DB: anotação + oportunidades com fontes independentes
    end

    Note over Aud: passo 6 — a decisão é humana
    Aud->>API: aprova · reprova · manda reanalisar
    API->>DB: veredito, autor, data e as imagens usadas
```

O passo 5 não recebe o número do cadastro. Quando recebia, o modelo devolvia o
número que lhe foi dado e o código depois "conferia" que batia — uma checagem
circular que tornava a divergência de numeração impossível de detectar.

---

## Onde quebra sob carga

A premissa é uso em massa. Os pontos abaixo estão medidos ou identificados, e
nenhum deles é "depois a gente otimiza".

| Ponto | O que acontece | Alternativa |
|---|---|---|
| **Analítico no mesmo Postgres** | `ibge_cnefe` (23 GB) e `rf_estabelecimentos` (17 GB) são varridos por consulta analítica no mesmo servidor que atende o painel. Uma varredura estadual derruba o tempo de resposta de todo mundo | Separar o analítico — réplica de leitura, ou DuckDB sobre Parquet para o que é lote |
| **Sem pooler** | Cada subprocesso Python abre conexão própria. Dezenas de workers em paralelo esgotam `max_connections` antes de esgotar CPU | Supavisor ou PgBouncer em modo transação |
| **Sem `tenant_id`** | Não existe coluna de cliente em nenhuma tabela. Ao introduzir, `tenant_id` tem de ser a **primeira coluna de todo índice**, senão a RLS varre a tabela inteira por linha | `/modelo-acesso` |
| **Imagens em `bytea`** | `streetview_imgs` tem 4,1 GB e `images_urls` 2,2 GB dentro do banco. Backup, restauração e replicação carregam os bytes junto | Object storage (R2) com o banco guardando só a referência |
| **WebSocket direto do backend** | O progresso é transmitido pelo processo que também executa. Com muitos clientes conectados, a transmissão compete com o trabalho | Broadcast publicado por processo separado |
| **`fachada_anotacao` com uma linha por POI** | A chave não inclui o modelo: reprocessar com outro modelo sobrescreve a leitura anterior e o histórico se perde | Chave `(poi_id, modelo)` ou tabela de versões |

---

## Caminho de dados

`psycopg`, direto no Postgres, com PostGIS e `execute_values`. Sem PostgREST e
sem ORM no caminho quente.

Existe um `prisma/schema.prisma` que cobre **6 das 38 tabelas** e cuja última
migration é de 30/06/2026 — legado da primeira versão em TypeScript, não o
caminho de dados atual. Ver [ADR 0002](adr/0002-prisma-legado.md).
