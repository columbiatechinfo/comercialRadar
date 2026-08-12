# ADR 0003 — Dois bancos: o do produto e o das bases de referência

- **Data:** 12/08/2026
- **Estado:** proposta — aguarda decisão antes da migração
- **Decisores:** Columbia Tech

---

## Contexto

A pilha Supabase subiu no i9 e o banco de 66 GB precisa sair do notebook. Ao
medir o que são esses 66 GB, a composição decide a arquitetura:

| O que | Tamanho | Gerado aqui? |
|---|---|---|
| Receita Federal (`rf_*`) | 35 GB | não — base pública da RFB |
| IBGE (CNEFE + malha) | 23 GB | não — base pública do IBGE |
| Imagens em `bytea` | 6,3 GB | **sim** |
| OSM (`osm_via`, `osm_quadra`) | 1,9 GB | não — base pública |
| **Produto** (POIs, cadastro do cliente, anotações, coletivas) | **237 MB** | **sim** |
| radarTelhados (quadras, telhados) | 102 MB | sim |

**O dado insubstituível são 339 MB.** Os outros 60 GB são referência pública que
este sistema lê e nunca gera.

Dois fatos independentes empurram para a mesma conclusão:

1. **A regra da casa é explícita:** *analítico pesado nunca roda no mesmo Postgres
   que atende usuário*. Uma varredura na `ibge_cnefe` (111 milhões de linhas)
   compete por page cache com o Auth, o PostgREST e o Realtime da mesma instância.
2. **O PostGIS não bate.** A origem é 3.6.1; a imagem do Supabase só oferece
   3.3.7, e restaurar para uma versão **anterior** do PostGIS não é caminho
   suportado. As tabelas que mais sofrem — `ibge_cnefe`, `osm_via` — são
   exatamente as de referência.

---

## Decisão proposta

**Dois Postgres na mesma máquina**, no mesmo Docker e no mesmo disco — continua
sendo "tudo unificado no i9", que era o requisito.

**Banco do produto** — a instância do Supabase (`supabase/postgres:17.6`,
PostGIS 3.3.7):
`pois`, `cadastro_cliente`, `fachada_anotacao`, `cnefe_coletiva`,
`area_trabalho`, `comentarios`, `horario_funcionamento`. São as tabelas que o
usuário consulta, que precisam de **RLS ligado ao Auth** e que o PostgREST expõe.
Geometria simples — ponto e polígono com `ST_DWithin`/`ST_Contains` — que o
PostGIS 3.3 atende sem esforço.

**Banco de referência** — container próprio `postgis/postgis:17-3.5` ou superior:
`ibge_cnefe`, `rf_*`, `osm_via`, `ibge_malha`. Lido pelo pipeline Python, nunca
pelo navegador. PostGIS igual ou mais novo que a origem resolve a migração das
tabelas geométricas pesadas.

**Imagens** (`streetview_imgs`, `images_urls`, 6,3 GB) saem do banco para o
**Storage** assim que houver R2 ou MinIO. Enquanto não houver, acompanham o banco
do produto.

---

## Consequências

**O código passa a ter duas conexões.** `base_comum.conectar()` ganha um segundo
alvo, e cada módulo diz de qual banco ele lê. É a mudança de maior alcance desta
decisão, e a que justifica registrá-la em ADR.

**Nada de `JOIN` entre os dois.** Cruzamento POI × CNEFE deixa de ser um `JOIN` e
passa a ser: consulta ao banco de referência, resultado recortado, cruzamento no
Python — que é como o `coletivas_importar.py` já funciona, por casamento de
coordenada.

**Backup fica viável.** O banco do produto cai para menos de 7 GB — ou menos de
1 GB quando as imagens saírem. O de referência não precisa de backup: rebaixa-se
da fonte. Hoje, "fazer backup" significa 66 GB e ninguém faz.

**O RLS não é afetado.** As tabelas de referência não recebem `tenant_id` nem
política de isolamento — são dado público, lido por todos os clientes e gerado
por nenhum. Aplicar RLS de tenant em 111 milhões de linhas para proteger o que o
IBGE publica na internet seria pagar avaliação por linha à toa.

---

## Alternativas descartadas

**Tudo na instância do Supabase.** Mais simples e permite `JOIN` direto. Descartada
por dois motivos duros: viola a regra da casa sobre analítico, e esbarra no
PostGIS 3.3.7 justamente nas tabelas geométricas grandes.

**Tudo num Postgres separado, Supabase só para Auth.** Deixaria o produto fora do
PostgREST e do RLS ligado ao JWT — que é a razão de ter escolhido o perfil
`saas-multi-cliente`. Seria subir a pilha e não usá-la.

**Referência em Parquet lido por DuckDB.** Tecnicamente o melhor destino para dado
de referência em lote, e o padrão da casa para ingestão analítica. Descartada
**por ora**: exige reescrever as consultas do pipeline, e o objetivo desta etapa é
tirar o banco do notebook, não redesenhar o acesso a dado. Fica como evolução
natural do banco de referência.
