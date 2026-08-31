# Migração para o padrão A2L — Radar Comercial

**Decidido em 30/08/2026.** Este documento é o plano; ele existe porque a
mudança toca 143 arquivos, troca o banco, remove a identidade própria e separa
API de frontend. Plano revisável agora custa menos que conserto depois.

Referências obrigatórias: [23 · Arquitetura de referência](../../avaliacoes_recomendacoes_analises/docs/23-arquitetura-de-referencia.md)
e [24 · Adequação de sistemas existentes](../../avaliacoes_recomendacoes_analises/docs/24-adequacao-de-sistemas-existentes.md).

---

## O que foi decidido

| assunto | decisão |
|---|---|
| nome | **Radar Comercial** (logos e textos) |
| pasta | `Documentos/sistemas/radarComercial` no i9 |
| schema da ferramenta | `radar_comercial` |
| dados de referência | **tudo** para `resources_root` — CNEFE, `ibge_malha`, CNPJ |
| níveis | `admin` → `administrator`; `editor` fica disponível e sem uso |
| portas | API **7720** · frontend **7810** · página da captura **7910** |
| dados minerados | **migram todos**, com `tenant_id` reescrito para a empresa nova |
| arquitetura | **API e frontend separados**, um par por sistema |
| acesso | túnel SSH com a chave do pendrive |
| IA de visão | vLLM na Spark, com token |
| imagens | Supabase Storage na `:7120`, bucket `radar_comercial` |
| proxies | recurso da API 7700, com token |

**A empresa e o administrador novos:**

```
empresa   d4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2   Corsan - Aegea RS
admin     a63f69ea-0df0-4e12-8e5a-3e2989192b38   calebe.damasceno@a2lsolucoes.com   nível 4
```

---

## Os conflitos medidos no sistema atual

Não são suposições: cada um foi contado no código em 30/08/2026.

### 1 · Banco

| hoje | padrão |
|---|---|
| `100.115.117.49:5444`, banco `postgres` | `192.168.3.10:7110`, banco `a2l` |
| papel `comercialradar_worker` | `app_user` |
| schema `comercialradar` | `radar_comercial` |
| **segundo banco `referencia` na 5443** — CNEFE (111M linhas), `ibge_malha`, CNPJ; **41 chamadas** a `conectar_referencia()` | `resources_root`, mesmo banco |

Resíduo no `.env`: `POSTGRES_HOST=localhost:5432 / DB=comercialradar`, que já não é
usado por nada.

**Levantei uma ressalva sobre page cache e ela não procede** — registrado porque
custou uma volta. O `base_comum.py` justifica a separação em dois bancos dizendo
que o CNEFE disputaria o page cache com o Auth. Medido antes de aceitar:

- **o acesso é por município, não pela tabela toda.** Todas as consultas são
  `WHERE cod_municipio = %s` — `ajuste_logradouro`, `corrigir_coordenada`,
  `cadastur`, `coletivas_radar`. Lêem um município por vez;
- **o Postgres já se protege disso.** Varredura sequencial de tabela maior que
  1/4 do `shared_buffers` usa um *buffer ring* de 256 KB, criado justamente para
  não despejar o cache. O cenário temido é o que esse mecanismo impede.

`resources_root` no mesmo banco, leitura para todos, e o schema da ferramenta
apenas referenciando.

### 2 · Identidade — o conflito mais profundo

- **Tabelas próprias:** `tenants` e `usuarios` (migração 0001), com `tenant_id`
  em três tabelas de negócio e RLS em cima delas.
- **12 rotas de identidade servidas por mim:** `GET/POST/PATCH/DELETE`
  `/api/usuarios` e `/api/empresas`, mais `/api/eu`. Todas passam para a API 7710.
- **Os níveis não batem:** uso `root, admin, supervisor, user`. O padrão tem
  cinco, e `admin` ≠ `administrator`.
- **A coluna é outra:** minha `tenant_id` × `id_empresa` do padrão.
- **Gateway errado:** falo com o Supabase na `:8000`; passa a ser `:7120`.

### 3 · Recursos — todos levam 401 hoje

| chamada atual | destino |
|---|---|
| SearXNG `100.115.117.49:8888` | `searxng.stack` + token da 7700 |
| Nominatim `100.115.117.49:8080` | `nominatim.stack` + token |
| Ollama `100.115.117.49:11434` | `vllm.stack` + token (IA passa a ser na Spark) |
| Qwen2-VL `100.115.117.49:8081` | `vllm.stack` + token — **é IA de visão, não OpenCV** |
| upload `100.115.117.49:8000` | já é **Supabase Storage**: muda para `:7120`, bucket `radar_comercial` |

**Nenhuma manda token.**

### 4 · Portas — nenhuma na faixa

`8765` (servidor), `8766` (página do mapa na captura), `8000`, `8080`, `8081`,
`8888`, `11434`.

### 5 · Rede

Tudo aponta para `100.115.117.49`. O novo é `192.168.3.10` na LAN e
`100.66.173.63` por túnel.

### 6 · Nome

**620 ocorrências em 143 arquivos.**

### 7 · Proxies — passam a ser recurso do catálogo

O pool Webshare (500 IPs, 250 BR + 250 CO) é chamado direto na API da Webshare,
com a chave no `.env`. **Decidido: vira recurso da API 7700**, com token — assim
dois sistemas minerando não queimam os mesmos IPs sem saber um do outro.

### 8 · Dois nomes que eu errei ao auditar

- `detect_pois_opencv.py` **não usa OpenCV**: chama `/v1/chat/completions` com
  `qwen2-vl`. É inferência, e vai para o vLLM da Spark.
- `subir_imagens.py` **já usa Supabase Storage** — o `:8000` era o gateway, não
  um serviço próprio. Muda porta e nome do bucket, nada mais.

---

## Ordem de execução

A ordem não é arbitrária: cada passo depende do anterior, e os dois primeiros
não precisam do ambiente novo no ar.

### Fase 1 — sem tocar no ambiente novo

1. **Camada de configuração única.** Hoje endereço e porta aparecem espalhados
   em `agente_local.py`, `avaliar_fachada.py`, `detect_pois_opencv.py`,
   `descrever_imagens.py` e nos testes. Antes de trocar os valores, centralizar —
   senão a troca é 40 edições e uma delas escapa.
2. **Renomear para Radar Comercial.** 620 ocorrências, com cuidado para separar
   o que é *nome de produto* (muda) do que é *identificador técnico* (schema,
   papel, variável de ambiente — muda em outro passo, com o banco).

### Fase 2 — com o túnel aberto

3. **Conferir o que já existe** no `a2l`: se o schema `radar_comercial` está
   criado, se há linha em `core.tb_tools`, e o que `resources_root` já contém.
   Pode ser que CNEFE e `ibge_malha` já tenham sido carregados por outro sistema.
4. **Migrar o schema da ferramenta.** As tabelas de POI, vínculo, análise da IA e
   cadastro do cliente, com `tenant_id` reescrito para `id_empresa` da Corsan-Aegea.
5. **Migrar os dados de referência** para `resources_root`, se ainda não
   estiverem lá.
6. **Remover a identidade própria.** Apagar `tenants` e `usuarios`, tirar as 12
   rotas, apontar login para `:7120` e consultas de usuário para `:7710`.
7. **Token nos recursos.** Cadastrar o cliente na API 7700 e passar
   `Authorization: Bearer` em toda chamada a mapa e IA.
8. **Separar API e frontend** em dois serviços, nas portas 7720 e 7810.

### Fase 3 — verificação

9. Rodar a suíte inteira contra o ambiente novo.
10. Uma mineração de ponta a ponta numa área pequena, para provar que captura,
    OCR, busca, endereços, cruzamento e cadastro seguem funcionando.

---

## O que NÃO pode se perder na migração

Trabalho recente que custou caro e precisa sobreviver:

- **`--de-etapa`** — retomar rodada sem refazer a captura;
- **cura de sessão morta** — navegador morto deixou de virar "o lugar não
  existe"; 2% → 76% de acerto;
- **cura de perfil** — o perfil é suspeito antes do IP;
- **monitor de proxies** — `proxy_ip` e `proxy_evento`, com RLS e gatilho de
  tenant;
- **filtro de país no pool** — só BR para cidade brasileira;
- **recorte por área** no `/api/stats` e no `/api/cadastro/resumo`;
- **contrato do WebSocket** — os campos que a tela lê.

Cada um tem teste. A suíte é a rede de proteção da migração.

---

## O que ficou em aberto

- **A busca de Santa Maria está pela metade** (7.188 de 10.851) no ambiente
  antigo, que está fora do ar. Decidir se termina lá antes de migrar ou se
  recomeça no ambiente novo.
