# O processo de oportunidade — do cadastro do cliente ao veredito

Definido pelo usuário em 13/08/2026. Este é **o produto**: tudo o que vem antes
(mineração, enriquecimento, fachada) existe para alimentar estes quatro passos.

---

## O que é ganho e o que não é

A distinção que organiza tudo:

| Situação | Vale? |
|---|---|
| Estabelecimento comercial que **já está** como comercial no cadastro do cliente | **Não é ganho.** Serve como complemento de informação — telefone, foto, CNAE |
| Local com atividade comercial que no cadastro está como **cliente simples** | **É o ganho.** É reclassificação de tarifa, e é o que o cliente paga para descobrir |
| Achado nosso sem correspondência no cadastro | Ligação clandestina ou imóvel não cadastrado — trilha separada |

O sistema precisa **separar fisicamente** os três grupos, não apenas contá-los.

---

## Os quatro passos

### 1 · Separar o comercial da base do cliente

Entrada: a carteira de imóveis da empresa de saneamento (`cadastro_cliente`).

Filtrar **apenas o que já está classificado como comercial**. É o conjunto de
comparação — não é resultado.

### 2 · Cruzar o achado com o comercial do cliente

Cruzar o que o processo principal encontrou contra esse recorte comercial.

O que casar **sai da lista de oportunidade** e entra como complemento: o cliente
já sabe que ali é comércio; o que agregamos é dado (telefone, foto, CNAE,
sócios), não a descoberta.

### 3 · Sobre o que sobrou, achar a divergência de tipo

Removidos os já-comerciais, cruzar o restante contra a base do cliente
**inteira**.

O que casar aqui é o alvo: **está no cadastro como cliente simples, e no local há
atividade comercial**. Essa é a pré-lista.

#### Quem já é comercial não passa pelo funil — 14/08/2026

Regra do usuário: POI que **já está como comercial no cadastro do cliente** não
entra na captura de Street View nem na avaliação por IA. Não há reclassificação
a propor, e capturar fachada para confirmar o que a base já cobra gasta hora de
captura que a área precisa em outro lugar.

Há **opção** para incluí-los, nos dois passos (`--incluir-ja-comerciais`), para
quem quiser o dossiê da carteira inteira.

Na tela de distribuição eles aparecem **marcados em amarelo, com o selo `⚠ JÁ
COMERCIAL NO CADASTRO`**, e continuam selecionáveis: a decisão é do admin. Ao
enviar, se houver algum na seleção, o sistema diz quantos são e pede
confirmação — a linha amarela some da vista quando o filtro muda, e o aviso
precisa estar onde o clique acontece.

### 4 · Validar antes de afirmar

A pré-lista não vira recomendação sem prova. Para cada item, reunir:

- Street View da fachada
- fotos do estabelecimento
- dados web e do Google
- dados empresariais — CNPJ, CNAE, **sócios**, endereço

Tudo isso vai para a IA, que **descreve o local** e entrega os elementos de
decisão: mudar o cadastro ou não.

> A IA descreve e fundamenta. Quem decide é a pessoa. Vale aqui a mesma regra da
> `leitura-fachada-cadastral`: divergência só é afirmada com o que se pode ler na
> imagem.

### 5 · A fila de decisão

O que sair do passo 4 com **alta convergência** e recomendação da IA para
mudança de tipo cadastral — de cliente simples para atividade comercial — entra
numa **lista de workflow**. Três saídas, e nenhuma delas é um clique solto:

| Ação | O que exige | O que produz |
|---|---|---|
| **Aprovar** | — | o **dossiê do local**: as informações comprobatórias reunidas num documento |
| **Reprovar** | motivo **escrito** + motivo **genérico** escolhido de uma lista | registro do descarte, com causa |
| **Devolver** | observações do que impede agora, ou as edições necessárias | volta ao fluxo sem virar aprovação nem descarte |

**Por que reprovar exige as duas coisas.** O motivo genérico é o que se conta e
vira estatística — quantas reprovações por "fachada residencial", por "endereço
divergente", por "comércio encerrado". O motivo escrito é o que explica o caso
particular, que nenhuma lista prevê. Só o genérico apaga a nuance; só o escrito
não agrega e não se mede.

**Devolver não é reprovar.** É o estado que reconhece que o ponto é útil mas a
evidência ainda não fecha. Sem ele, o revisor é empurrado para uma decisão
binária e acaba reprovando o que deveria voltar melhor instruído — e o achado se
perde sem que ninguém tenha decidido descartá-lo.

#### Como o lote é montado — 13/08/2026

O admin abre a área de trabalho inteira, **não uma amostra**. A tela lista todos
os pontos do polígono com o que serve para separar trabalho — situação no
cadastro, categoria, edificação, conservação, que evidência existe — filtra,
marca os filtrados de uma vez e envia aos supervisores que escolher.

A marcação **sobrevive à troca de filtro**: dá para juntar "reclassificar com
CNPJ confirmado" e depois "os que têm Street View" no mesmo lote, sem perder o
que já estava marcado.

> A versão anterior mandava os N primeiros de `/api/pois` — rota que não recorta
> área nenhuma. O texto dizia "os POIs da área atual" e entregava os N primeiros
> da base inteira, por ordem de id. Distribuição aleatória com aparência de
> critério é pior do que distribuição declaradamente manual: ninguém desconfia.

#### O que o supervisor preenche

Clicar no item abre a ficha quase em tela cheia: à esquerda a evidência —
Street View, fotos, o que encontramos, **o que o cadastro do cliente diz**, a
leitura de fachada e a conclusão da IA; à direita os campos que sustentam a
mudança:

| Campo | Por quê |
|---|---|
| **Uso observado** — comercial, misto, residencial, indefinido | é o veredito que muda a tarifa |
| **Atividade no local** | "comercial" não reclassifica nada; "oficina mecânica" sim |
| Nome, CNPJ, telefone | vêm preenchidos; o supervisor **corrige**, não redigita |
| Economias comerciais no imóvel | a unidade de cobrança do saneamento |
| Endereço confere / corrigido | divergência de endereço é a causa nº 1 de contestação |
| Precisa de visita | separa o que fecha na mesa do que exige campo |
| Observação técnica | o que nenhum campo prevê |

**Aprovar sem uso e atividade é recusado pelo banco**, não só pela tela
(`aprova_exige_revisao`, migração 0009). Aprovar é afirmar ao cliente que ali há
comércio; sem dizer comércio de quê, o dossiê afirma sem sustentar. A trava
nasceu `not valid`: as aprovações anteriores ao campo continuam válidas, porque
invalidar decisão tomada é reescrever histórico.

O **dossiê** é o entregável que sai daqui: é o que a empresa de saneamento leva
para justificar a mudança de tarifa. Ele nasce da aprovação, não do achado.

---

## Onde as skills novas entram

**Integradas em 13/08/2026.** As duas rodam sobre o nosso banco e gravam nele:

| Skill | Adaptador | O que grava |
|---|---|---|
| `extracao-poi-estadual` | `extracao_estadual.py` | POIs em `pois`, `fonte='estadual'`, por município |
| `tratamento-cnpj` | `tratamento_cnpj.py` | `cnpj_tratado`, com os quatro eixos separados |

As duas leem e escrevem ARQUIVO; as entradas moram no banco de referência e o
destino é o banco do produto. Os adaptadores fazem a volta completa — exportam o
recorte, rodam a skill, reingerem — e por isso a skill continua atualizável sem
reescrever nada aqui.

**Sempre por município.** O RS tem 383 mil POIs no padronizado e 168 mil
estabelecimentos ativos só em Canoas. Rodar por UF troca minutos por horas para
responder à mesma pergunta, e despejar o estado inteiro na base de um cliente
que trabalha uma cidade não é cobertura, é entulho.

**A empresa vem do comando, não do `.env`.** `CR_TENANT_ID` é valor fixo e serve
a um pipeline de cliente único. A mesma extração do RS alimenta a Corsan hoje e
outra concessionária amanhã — sem `--empresa`, 17 mil POIs de Canoas iriam para
a empresa errada sem erro nenhum, porque a trigger carimba o que a sessão mandar.

### O que a `tratamento-cnpj` respondeu, medido em Canoas

Primeira execução, 3.000 CNPJs ativos contra 176.899 endereços do CNEFE, 22
segundos:

```
rota de tratamento              perfil comercial
VERIFICAR_CAMPO         1.365   COMERCIO_ATENDIMENTO       1.184
RECLASSIFICACAO_1_1       794   NAO_COMERCIAL                772
SEM_ROTA                  640   RESIDENCIAL_NAO_COMERCIAL    467
INDIVIDUALIZACAO_MULTI    201   PRESENCIAL_SEM_PUBLICO       436
```

**423 são comércio de atendimento com reclassificação 1:1** — endereço com
estabelecimento único identificado pelo IBGE. É o passo 3 do processo acima,
respondido por dado público, antes de qualquer captura paga.

Uma lição da integração: a skill avisou *"CNEFE sem colunas opcionais
(degradação declarada)"*, e entre as que faltavam estava
`COD_INDICADOR_ESTAB_ENDERECO` — de onde a rota deriva, e que nós tínhamos. Ela
declarou o que perdeu em vez de silenciar; ignorar esse aviso teria produzido
uma coluna `rota` cheia e errada.

### `extracao-poi-estadual` v3.6.1

POIs em escala estadual de Overture + OSM (.pbf via DuckDB) + Foursquare, com
dedup por evidência e nenhuma perda silenciosa — todo descarte e toda fusão
gravados linha a linha. A descrição cita CORSAN/Aegea, COPEL, SABESP, SANEAGO e
AGESPISA: é o mesmo mercado deste sistema.

**Onde entra:** alimenta o passo 2 com uma base de comparação que não custa
captura. Hoje o achado vem só da mineração, que é cara por área. A extração
estadual dá cobertura ampla e barata; a mineração fica para onde se quer
profundidade.

**A decidir antes:** a saída é arquivo (CSV/GeoParquet) e a premissa do projeto é
que dado mora no banco. Precisa de rota de ingestão para o schema
`comercialradar`, com a procedência preservada.

### `tratamento-cnpj` v2.2

Cruza CNPJ da Receita com CNEFE/IBGE: geocodifica, valida endereço, mede a
**incerteza posicional em metros**, junta evidência de existência física e
classifica potencial comercial. Bloqueia match fuzzy ambíguo em vez de adivinhar.

**Onde entra:** no passo 4, como fonte dos dados empresariais — inclusive sócios.
E no passo 3, porque "potencial comercial por CNAE" é exatamente o sinal que
separa cliente simples com atividade de cliente simples de verdade.

**A decidir antes:** hoje `cnpj_local.py` faz um casamento próprio contra
`rf_estabelecimentos`. Ou a skill substitui esse caminho, ou os dois convivem e
alguém precisa dizer qual manda quando divergem.

### `radar-coletivo` — sai daqui

O usuário foi explícito em 13/08/2026: coletivas são **outra ferramenta**, e a
skill não deveria estar em `comercialRadar/skills/`. Mover quando houver pasta
própria; enquanto isso, não expandir o uso.

---

## Recepção de bases de clientes

Hoje a carteira entra por importação manual. O usuário pediu um caminho próprio:

1. **Envio.** Sistema para o cliente enviar a base cadastral.
2. **Agentes de recepção.** Monitoram a chegada, leem os campos e **adaptam
   automaticamente** ao esquema do banco.
3. **Veredito por arquivo**, três saídas possíveis:
   - faltam campos mínimos → devolve **erro dizendo o que falta**;
   - completo → **recomenda** importar, na tabela certa;
   - dúvida → sobe para decisão humana.
4. **Aprovação.** O usuário root vê uma **lista de recomendações**, cada uma com
   a descrição da IA sobre os fatos e **alguns exemplos das linhas enviadas**, e
   aprova ou não.

> O root aprova olhando exemplo real, não só o parecer. Mapeamento de campo
> errado contamina a base inteira em silêncio, e a amostra é o que deixa o erro
> visível antes da importação.

---

## Fonte do dado, no frontend

Requisito do usuário em 13/08/2026:

**A fonte aparente para o cliente somos nós.** IBGE, BDGD, Overture, OSM,
Receita, web — a procedência fica **embaçada/oculta** na interface.

- Apenas **usuário root** pode desembaçar e ver a origem real.
- A procedência **continua gravada no banco**, íntegra. O que muda é a exibição,
  nunca o registro: auditoria, reprocessamento e contestação dependem dela.

Isso é regra de **apresentação com controle de acesso** — entra na `/modelo-acesso`
junto com o papel `root`, não como filtro no SQL.
