# De onde vem cada base

Este arquivo existe para responder uma pergunta só: **se precisar baixar de
novo, de onde vem e como se dispara.** Todas as fontes abaixo são públicas —
nenhuma exige contrato, e só uma exige credencial.

Tudo mora em `resources_root`, o schema compartilhado por todas as ferramentas
do A2L. A regra: `radar_comercial` guarda só o que o sistema **gera** (POI
minerado, imagem, análise de IA, cruzamento); toda **base** fica aqui.

O disparo é sempre o mesmo, e sempre destacado do terminal:

```bash
cd ~/Documentos/sistemas/radarComercial
./scripts/servidor/carregar_bases.sh            # todas
./scripts/servidor/carregar_bases.sh cnefe      # uma
./scripts/servidor/carregar_bases.sh --log      # acompanhar
./scripts/servidor/carregar_bases.sh --situacao # onde está
```

Quem garante que ele sobrevive à queda da rede é o `docker run -d`: o processo
passa a pertencer ao daemon do Docker, não à sessão de SSH.

---

## CNEFE — endereços do Censo 2022

| | |
|---|---|
| origem | IBGE, FTP aberto |
| endereço | `https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/` |
| formato | um `.zip` por UF, CSV com `;` dentro |
| credencial | nenhuma |
| carregador | `base_cnefe.py` |
| tabela | `resources_root.ibge_cnefe` |
| medido | **111.110.860 linhas · 27 UFs · 3 minutos** |

**A forma da tabela vem do CABEÇALHO DO CSV**, não de migração. São 34 colunas
que mudam entre censos, e `base_cnefe.py` monta o DDL lendo a primeira linha do
zip. Por isso `ibge_cnefe` não aparece em `migrations_a2l/` — e por isso uma
versão minha que a criava lá, com 2 colunas, fez as 27 UFs falharem no `COPY`
depois de cada uma já ter baixado.

Atualiza a cada censo. Não há motivo para rodar de novo antes disso.

---

## CNPJ — empresas e estabelecimentos da Receita Federal

| | |
|---|---|
| origem | Receita Federal, Dados Abertos |
| endereço | `https://arquivos.receitafederal.gov.br/public.php/webdav/` |
| autenticação | token do compartilhamento público, **já no código** (`base_cnpj.SHARE_TOKEN`) — não é segredo |
| formato | 37 `.zip` por mês, CSV com `;` |
| carregador | `base_cnpj.py` |
| tabelas | `rf_empresas`, `rf_estabelecimentos`, `rf_socios`, `rf_simples` e 6 dicionários |
| medido | **220.339.195 linhas · 37 arquivos · 45 minutos** |

O mês é escolhido pelo carregador (o mais recente disponível). **Atualiza
mensalmente** — é a base que mais se move, e a que justifica rodar a carga de
novo com alguma frequência.

---

## Malha municipal — as divisas que o painel desenha

| | |
|---|---|
| origem | IBGE, API de malhas |
| endereço | `https://servicodados.ibge.gov.br/api/v3/malhas/estados/{UF}?formato=application/vnd.geo+json&qualidade=intermediaria&intrarregiao=municipio` |
| nomes | `https://servicodados.ibge.gov.br/api/v1/localidades/estados/{UF}/municipios` |
| credencial | nenhuma |
| carregador | `scripts/servidor/carregar_malha.py` (lote) ou `/api/malha` (sob demanda) |
| tabela | `resources_root.ibge_malha` — `geom` em PostGIS |
| medido | **5.570 municípios · 27 UFs · ~2 minutos** |

**`qualidade=intermediaria`, e não `minima`.** Na mínima as divisas são
generalizadas e Itambé-PE, a 2 km da fronteira, cai na Paraíba. A carga foi
verificada com quatro coordenadas conhecidas, incluindo essa.

O IBGE responde **GZIP mesmo sem `Accept-Encoding`**. Sem descomprimir, o
`json.loads` estoura com `UnicodeDecodeError: byte 0x8b` — que é a assinatura do
gzip, e não um problema de acentuação como o nome do erro sugere.

Atualiza raramente (mudança de divisa municipal).

---

## Cadastur — prestadores de turismo

| | |
|---|---|
| origem | Ministério do Turismo, Dados Abertos (CKAN) |
| endereço | `https://dados.turismo.gov.br` |
| credencial | nenhuma |
| carregador | `cadastur.py` (usa a skill `extracao-cadastur-mtur`) |
| tabelas | `resources_root.cadastur_prestador`, `cadastur_total_pf` |
| medido | **804 linhas** — e isto está errado, ver abaixo |

**⚠️ ABERTO.** Dos 13 conjuntos, os 11 de 2026 baixam (arquivos de 6 a 19 MB,
XLSX válidos) e produzem **zero linhas**, sem aparecer no funil — que é
justamente onde a skill registra perdas. Só materializam os 8 recursos de 2024
dos dois conjuntos menores. Deveriam ser dezenas de milhares de prestadores.

O download funciona; o problema é a materialização, dentro da skill.

Atualiza trimestralmente.

---

## Overture Maps — a maior das três fontes de POI

| | |
|---|---|
| origem | Overture Maps Foundation (Amazon, Meta, Microsoft, TomTom) |
| endereço | bucket S3 `overturemaps-us-west-2`, lido pela CLI `overturemaps` |
| credencial | **nenhuma** — o bucket é público e não pede conta AWS |
| formato | GeoParquet particionado, consultado por *bounding box* com DuckDB |
| dependência | `overturemaps==1.0.1` no `requirements.txt` |
| medido (RS) | **56 tiles · 69 partes · 0 erros · 453.384 POIs brutos** |

É de onde vem a maior parte do volume: 453 mil dos 562 mil pontos do RS, ou
**81%**.

A CLI recusou a *release* `2026-08-19.0` e caiu no caminho padrão — o script
registra que "releases latest concorda com esta versão", então o dado é o mesmo.
É o tipo de coisa que pode mudar em silêncio numa execução futura.

**Sem a CLI a base sai menor e não falha.** `dataset_estadual.sh` faz
`command -v overturemaps`; não achando, avisa e continua só com OSM, gravando o
marcador de PRODUZIDO. Seriam 27 UFs de base degradada marcadas como prontas —
o pacote não estava declarado no `requirements.txt` até 31/08/2026.

Atualiza mensalmente.

---

## OpenStreetMap — a fonte com melhor endereço

| | |
|---|---|
| origem | Geofabrik, espelho oficial dos extratos do OSM |
| endereço | dump `.pbf` da macrorregião |
| credencial | **nenhuma** |
| formato | Protocol Buffer, lido com `ST_ReadOSM` do DuckDB |
| medido (RS) | **105.315 nós · 169.626 vias · 2.263 relações · 51 s · 108.994 POIs** |

Traz 19% do volume do RS, mas é a fonte com **endereço mais completo** — o
Overture tem cobertura maior e endereço mais falho. É por isso que as duas
juntas valem mais que qualquer uma sozinha, e é o que a fusão resolve.

**51 segundos.** É a fonte mais barata das três.

Atualiza diariamente na origem; o dump é baixado por macrorregião e cacheado.

---

## Foursquare — a terceira fonte, e a única com credencial

| | |
|---|---|
| origem | Foursquare OS Places, publicado no Hugging Face |
| endereço | `https://huggingface.co/datasets/foursquare/fsq-os-places` |
| credencial | **token do Hugging Face** — a única de todas as bases |
| onde está | `.env` do servidor, como `huggingface` (minúsculas) |
| medido | ainda não — o RS rodou sem ele |

A skill lê `HF_TOKEN`; o `.env` guarda como `huggingface`, e variável de
ambiente é sensível a maiúscula. `dataset_estadual.sh` faz a ponte entre os dois
nomes — duplicar o valor no `.env` sob os dois seria segredo rotacionado em um
lugar só.

Sem o token as fontes caem para `overture,osm`, **o script avisa e continua**, e
a base sai sem o Foursquare com o token presente no arquivo o tempo todo. Foi o
que aconteceu na primeira execução do RS.

**E corrigir o token não bastava para refazer o RS.** O marcador `_pronto.txt`
já registrava `fontes=overture,osm`, e o laço das 27 UFs só perguntava se o
arquivo existia: a rodada seguinte respondeu "já pronta" e pulou. A UF ficaria
sem Foursquare para sempre, e as outras 26 fariam o mesmo assim que qualquer
fonte fosse acrescentada.

Agora o laço lê o `fontes=` do marcador e refaz a UF quando a máquina consegue
produzir **mais** do que produziu — só a mais, nunca a menos: se o token sumir
do `.env`, a UF completa que está em disco é preservada em vez de ser
substituída por uma pior. O workspace não é apagado; o que já veio do Overture e
do OSM é reaproveitado e o custo real é a fonte nova mais a refusão.

---

## A fusão — o que o sistema faz com as três

Isto não é fonte: é o trabalho do `poi_estadual.py`, e é onde o tempo vai.

Ele decide **quais pontos das três fontes são o mesmo lugar**: raio de 30 m,
similaridade mínima de 85% (92% entre fontes diferentes), Jaccard 0,60. Roda em
blocos de 2 km com halo de 250 m — o halo existe para um estabelecimento na
borda de dois blocos não ser partido em dois.

| | |
|---|---|
| disparo | `scripts/servidor/dataset_brasil.sh` (27 UFs) ou `dataset_estadual.sh <UF>` |
| destino | `dados_externos/estadual/<UF>/` **em disco**, não em tabela |
| entra no banco | pela etapa 2 da mineração, **um município por vez** |

### O que o RS mediu, com duas fontes

```
21 minutos · 5,7 GB · entregável de 101 MB
794.350 → 638.662 → 562.378 → 543.585 POIs
21.190 fusões · 231.972 rejeitados · 1 cluster cruzando divisa
VALIDATE: 10 de 14 verificações OK → REPROVADO (fusão 3,8%, limite 2%)
```

**21 minutos por UF, não horas.** Eu havia estimado "dias" para as 27 a partir
de um comentário no código; medido, são **~9 a 10 horas**. A estimativa anterior
vinha de leitura, não de medição.

Onde o tempo foi, no RS: o OSM levou 51 s e o download do Overture uns 8 min. O
resto — cerca de **12 dos 21 minutos** — foi a fusão. É o `dedup`, com CPU em
100% de um núcleo e pico de 13 GB de RAM.

**Só um núcleo.** A máquina tem 123 GB e muito mais CPU do que o processo usa, e
a fusão paraleliza bem por município. Se as 27 UFs incomodarem, é aqui que está
o ganho — não no download.

`produzido_com_ressalva` **não** quer dizer inutilizável: o entregável existe e
a ressalva viaja no `_pronto.txt`, junto do `relatorio_qualidade_*.json`. Base
reprovada por um número que quem usa deveria poder julgar não é descartada.

A UF inteira **não** entra no banco: são 543 mil POIs no RS, e despejar isso na
base de um cliente que trabalha uma cidade é entulho.

## O que NÃO vem da internet

**O cadastro do cliente** — 102.065 ligações da Corsan. Não é minerável e não
tem fonte pública: precisa ser pedido à concessionária. O carregador existe
(`cadastro_cliente.py --importar`, com o `MAPA` de 71 colunas que traduz o
layout da planilha), e a decisão de onde ele passa a morar é de 31/08/2026:
`resources_root`, junto das demais bases.
