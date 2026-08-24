# Catálogo de módulos — ComercialRadar

> Em `saas-multi-cliente` cada módulo liga e desliga **por cliente**. A flag mora
> em tabela, não em constante: constante no código significa deploy para ativar
> módulo; em tabela é um `UPDATE`. Atualizado em 12/08/2026.

```sql
create table tenant_features (
  tenant_id  uuid    not null references tenants(id),
  module_key text    not null,
  enabled    boolean not null default false,
  primary key (tenant_id, module_key)
);
```

**Estado: a tabela ainda não existe.** Hoje todo módulo está sempre ligado para
o operador único. A coluna `flag` abaixo é o alvo.

---

## Módulos

| `module_key` | O que faz | Código | Custo externo | Padrão |
|---|---|---|---|---|
| `captura_ocr` | Fotografa o Maps em tiles, detecta ícone de POI por OpenCV, lê o nome por OCR e busca cada um | `detect_crops.py` · `ocr_pois.py` · `minerar_captura.py` | Proxies | ligado |
| `mineracao_area` | Varre um polígono desenhado e descobre os POIs úteis da região | `minerar_area.py` | Places API | ligado |
| `planilha` | Importa `.xlsx`/`.csv` e casa cada linha com o lugar real no Maps | `search_from_sheet.py` | Maps | ligado |
| `enriquecimento_cascata` | POI pobre passa por Maps → web (SearXNG) → Street View até completar | `enriquecer_tudo.py` · `enriquecer_maps.py` | Street View | ligado |
| `cnpj_receita` | CNPJ, CNAE, situação cadastral e sócios pela base local da Receita | `cnpj_local.py` · `base_cnpj.py` | zero (base local) | ligado |
| `coletivas_cnefe` | Unidades coletivas do CNEFE como terceira fonte independente | `coletivas_radar.py` · `coletivas_importar.py` | zero (base local) | ligado |
| `leitura_fachada` | Fachada + contexto vão ao modelo de visão; a anotação passa pelo validador da skill | `avaliar_fachada.py` | **US$ 0,017/POI** (`gpt-4o`) ou zero (i9) | ligado |
| `cadastro_cliente` | Importa a carteira de imóveis da empresa e cruza com os POIs | `cadastro_cliente.py` | zero | ligado |
| `dashboard_retorno` | Cobertura, custo real × cenário Google e cálculo de retorno por faixa | `server.py` · `frontend/app.js` | zero | ligado |
| `extracao_estadual` | Extração POI em escala estadual (Overture + OSM + Foursquare), ingerida por município | `extracao_estadual.py` · skill `extracao-poi-estadual` | zero | ligado |
| `tratamento_cnpj` | Cruza CNPJ da Receita com o CNEFE e devolve aptidão, perfil comercial, evidência e rota | `tratamento_cnpj.py` · skill `tratamento-cnpj` | zero | ligado |
| `cadastur` | Cadastro obrigatório de prestador turístico (MTur). Carrega o município, cruza como qualquer base e gera POI do que sobrou | `cadastur.py` · skill `extracao-cadastur-mtur` | zero (portal público) | ligado |

---

## O que decide se um módulo é ligado por padrão

Módulo com **custo externo por item** nasce desligado quando o custo escala com a
base: `leitura_fachada` a US$ 0,017 por POI são US$ 356 nas 20.698 fachadas já
capturadas. Ligar para um cliente novo sem que ele saiba é gastar o dinheiro dele.

Módulo que só lê base local nasce ligado: o custo marginal é tempo de CPU.

---

## `cadastur` — o que ele acrescenta que nenhum outro tem

As demais fontes dizem *que existe um comércio ali*. O Cadastur diz *o que o
Estado registrou que ali funciona* — e, para meio de hospedagem, **quantos
leitos**. É a única fonte do sistema com capacidade declarada, e leito é consumo
de água por pessoa/dia. Medido em Canoas: Intercity 162 UH / 206 leitos, Atrio
132 / 264, Canoas Parque Hotel 130 / 260.

**Quatro passos, e o quarto exige o terceiro:**

1. **Baixar** — a skill puxa o snapshot trimestral do portal do MTur (API CKAN).
2. **Carregar** — só o município pedido entra em `cadastur_prestador`.
3. **Cruzar** — `cruzar_bases.py`, como qualquer outra base.
4. **Gerar** — o que não cruzou com nenhum POI vira POI novo.

O passo 4 **recusa** rodar sem o 3. Se ele decidisse sozinho o que já existe,
haveria duas implementações da mesma pergunta, e a hora em que divergissem seria
a hora em que o banco ganhasse duplicata.

**A coordenada não vem do Cadastur** — a skill não geocodifica, e o endereço
moderno é texto livre. Ela vem, nesta ordem: CNPJ contra `cnpj_tratado`
(documento igual, sem gradação), depois o cruzamento por endereço com
`cadastro_cliente` — que para uma companhia de saneamento é a melhor coordenada
que existe, a do imóvel que ela fatura. Quem não tem nenhuma das duas **não vira
POI** e registra o porquê em `sem_poi_motivo`. Ponto no centroide do município
mandaria alguém a campo no lugar errado.

**Guia de turismo não vira linha** — só total. É cadastro de pessoa física, com
CPF, data de nascimento, nome social e tipo sanguíneo. Nada disso entra; a
contagem entra (ver abaixo). A lista de conjuntos é **positiva** — as 14 de
pessoa jurídica, nomeadas — porque o portal já criou conjunto novo duas vezes:
com lista negativa, um cadastro de pessoa física que nascesse amanhã entraria
como linha sem ninguém ver.

**Nem todo conjunto é atualizado.** Em 24/08/2026, doze estavam em 2026T2 e dois
— `parque-tematico` e `empreendimento-de-entretenimento-e-lazer-e-parques-aquaticos`
— pararam em 2024T4. O download busca o mais recente **de cada um**, e não um ano
fixo: parque aquático é o maior consumidor de água da lista, e um `--desde 2026`
o deixaria de fora sem uma linha de aviso.

### Atualizar a fonte

A skill **já traz a metodologia** — cache por `recurso_id` revalidado por
SHA-256, e `eventos_entidade.csv.gz` classificando cada entidade em
`ENTROU` / `ALTEROU` / `PERMANECEU` / `SAIU`. O que faltava era o nosso lado
usar: a carga regravava as 388 mil linhas a cada execução.

Agora, uma ação faz tudo — pelo botão **↻ Atualizar fonte** no passo Bases
públicas, ou:

```bash
python cadastur.py --uf RS --municipio Canoas --gerar --encadear
```

1. baixa só o trimestre que mudou;
2. grava só `ENTROU` e `ALTEROU`; `PERMANECEU` é pulado;
3. quem **saiu** do snapshot ganha `saiu_em` — e **não é apagado**: sumir do
   arquivo significa ter perdido regularidade, e pode ser fechamento, troca de
   dono ou renovação atrasada;
4. cruza, gera POI do que sobrou, **cruza de novo** (agora com os pontos novos)
   e enriquece só eles — Maps → web → CNPJ → Street View.

### As três âncoras de coordenada

Em ordem de qualidade da chave, e a origem fica gravada em `fonte_dado`:

| # | Âncora | Chave | Precisão |
|---|---|---|---|
| 1 | `cnpj_tratado` | CNPJ | do CNEFE, via Receita |
| 2 | cruzamento com `cadastro_cliente` | endereço | do imóvel que o cliente fatura |
| 3 | `ibge_cnefe` | endereço | `porta` (nv 1) ou `porta_face` (nv 2) |

A terceira é a que destrava o caso geral: em Esteio — município sem nenhuma
cobertura de `cnpj_tratado` — ela sozinha gerou 16 dos 17 pontos.

`nv_geo_coord` 3 (estimada na localidade) e 4+ (centróide de setor, erro de km)
são **recusados**. A skill `tratamento-cnpj` aceita até 3 porque lá o número
alimenta um score; aqui ele vira ponto para alguém visitar, e visita não tolera
o ruído que um score tolera.

Via **ambígua é recusada, não resolvida**: em Cachoeirinha existem uma AVENIDA e
uma RUA "Flores da Cunha". Escolher a primeira produziria base limpa e ponto
errado.

### Pessoa física: contada, não guardada

Guia de turismo é cadastro de pessoa física — CPF, data de nascimento, nome
social, tipo sanguíneo. A linha **não entra**; o total entra, em
`cadastur_total_pf`, e vira um card informativo no painel. Contar não identifica
ninguém; guardar identificaria, e não há finalidade num produto que procura
economia que consome água. A tabela de totais não tem coluna que possa receber
identificação — é a linha de defesa que sobrevive a qualquer refatoração.

### O chat também fecha o ciclo

`guardar_ponto` é o degrau que faltava: todas as outras ferramentas do chat
leem, e o que o agente descobria morria na conversa. Agora ele grava pelo mesmo
escritor único de POI, captura a fachada e cruza com a base.

Ele **exige** lat/lng e recusa sem — e é uma ferramenta explícita, não um efeito
de toda consulta: o chat também serve para explorar, e consulta que vira
cadastro enche a base de tentativa e a fila de aprovação de trabalho que ninguém
pediu.

```bash
python cadastur.py --listar
python cadastur.py --uf RS --municipio Canoas --gerar --encadear
python cadastur.py --uf RS --municipio Canoas --so-carregar --gerar   # sem rebaixar
```

---

## `extracao_estadual` — a integrar

Chegou em 12/08/2026 como skill pronta (`extracao-poi-estadual`), com saída já
produzida para o RS: POI bruto de OSM e Overture, padronizado, deduplicado com
vínculos, rejeitados e relatório de qualidade — todos em Parquet e CSV.

O que precisa ser decidido antes de virar módulo:

- **Onde o dado entra.** A saída é arquivo; a premissa do projeto é que dado mora
  no banco. A ingestão precisa de tabela e de chave que case com a `pois`.
- **Como convive com a `captura_ocr`.** As duas descobrem POI por caminhos
  diferentes; sem regra de precedência, o mesmo estabelecimento entra duas vezes.
- **Recorte por cliente.** Extração estadual atravessa municípios de vários
  clientes. Sem `tenant_id`, não há como recortar o que cada um vê.

Enquanto essas três não tiverem resposta, o módulo fica desligado e a skill fica
versionada como referência.
