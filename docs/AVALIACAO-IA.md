# A avaliação por IA — da quadra ao veredito

*04/09/2026 · primeiro bloco fechado ponta a ponta em Canoas*

## A pergunta

Uma só, e ela é comercial: **este imóvel, que a Corsan fatura como
RESIDENCIAL, tem atividade econômica?** Se tem, a ligação está com a tarifa
errada. Tudo o que este documento descreve existe para responder isso com
prova, e não com palpite.

## O caminho

| # | Passo | Entra | Sai | Tabela | Custo |
|---|---|---|---|---|---|
| 1 | Desenhar a área | polígono | recorte de trabalho | `area_trabalho` | grátis |
| 2 | Marcar categorias | catálogo | o que vale foto | `categoria_catalogo` | grátis |
| 3 | Minerar | a área | POIs | `pois` + tabelas por fonte | paga |
| 4 | Cruzar com o cliente | POIs + cadastro | vínculos | `ligacao_poi` | grátis |
| 5 | Refazer os links | tabelas por fonte | URLs por POI | `poi_link` | grátis |
| 6 | Evidência de rua | POIs da fila | 3 imagens/POI | `poi_evidencia` | paga |
| 7 | Página do anúncio | links do Airbnb | 1 imagem/anúncio | `poi_evidencia` | grátis |
| 8 | Ver às cegas | as imagens | descrição fiel | `poi_veredito.percepcao` | grátis |
| 9 | Julgar em separado | descrição + cadastro | veredito | `poi_veredito` | grátis |

Os passos 6 a 9 rodam num comando só: `avaliar_tudo.py`, ou o botão
**Avaliar com IA** no painel.

## As decisões que custaram medição

### Uma chamada por imagem, e não as três juntas

A percepção começou como uma chamada com as três imagens. No POI 99207 a loja
"Black Style" fica na **calçada oposta**, aparece só na terceira imagem, e o
modelo a descreveu como estabelecimento *da fachada*. O julgamento tem regra
explícita contra isso — "comércio do lado oposto não aprova o imóvel" — e a
regra é inútil quando a contaminação acontece **antes** dela: o julgamento só
vê texto, e o texto já mentia.

Duas tentativas de conter por instrução falharam. Instrução não segura o que a
estrutura permite. Hoje quem descreve a fachada **nunca viu** o outro lado.

Custo: duas chamadas a mais por POI, ~4 s → ~9 s de percepção.

### A mira virava letreiro

`letreiros: ["FACHADA AVALIADA"]` — o modelo transcrevia a legenda que *nós*
desenhamos sobre a foto. Quem desenha na imagem tem de dizer ao leitor o que
desenhou; a legenda entrou na lista do que se ignora, ao lado da marca d'água
do Google.

### A aba é do trabalhador, não do POI

Contexto novo por POI significava cache vazio: cada ponto rebaixava o
JavaScript inteiro do Maps antes do primeiro pixel, e com três trabalhadores
disputando banda isso estourava os 25 s de espera. A mensagem gravada era "o
Maps não entrou em modo panorama", que **soa como** "não há foto aqui". Havia.

10 falhas em 18 viraram 2 em 18; 25,8 s → 12,2 s por POI.

### Falha transitória volta para a fila

A fila excluía todo POI com linha de `sv_frente` — e a captura grava linha
também quando falha, para registrar o motivo. Um tile lento carimbava o POI
como feito e ele nunca mais era tentado. Hoje só ficam fora as duas falhas
**definitivas**: o Google confirmando que não há panorama, e a chave ausente.

### O iFood não tem print de página

A página da loja responde 200 e cobre o cardápio com o desafio "Pressione e
segure" do PerimeterX. Não se constrói solucionador de teste anti-robô.

Não é perda: a prova de atividade já existe de graça no endpoint `/extra` —
`disponivel`, nota, avaliações, CNPJ e `visto_em`. É **mais forte** que um
print do cardápio, porque diz *quando*. Vai à IA como texto.

### O Airbnb tem, e em leiaute de celular

Ele não publica endereço exato, então a prova não é a fachada: é a página
inteira — mapa, avaliações datadas, calendário. A mesma página em 1280 px de
largura passa de 8.000 de altura e o servidor de visão reduz até o texto virar
borrão; em 430 px ela empilha numa coluna legível dentro do orçamento.

A tarja de cookies **sai do DOM**, e não é clicada: aceitar ou recusar é dar
consentimento em nome do usuário. A busca é pelo texto ("Política de
Cookies"), não pelo `data-testid` — nome interno de componente é o que mais
muda num site grande, e quando muda falha calado.

## Os quatro vereditos

| Veredito | O que significa |
|---|---|
| `aprovado_exato` | o negócio do cadastro está ali, identificado |
| `aprovado_comercial` | não é aquele, mas o imóvel tem comércio visível |
| `revisao_humana` | há indício, não há prova |
| `reprovado` | nenhuma atividade econômica relevante |

Os dois primeiros aprovam. A diferença entre eles não é confiança, é **alvo**:
no `exato` a equipe sabe por quem perguntar; no `comercial` ela vai ao endereço
e descobre.

Na dúvida entre aprovar e reprovar, `revisao_humana`. Reprovar apaga o caso da
lista; revisar custa um olhar.

## As duas classificações

São perguntas independentes, e uma não deriva da outra:

- **espécie do CNEFE** (1 a 8) — o que é a *edificação*. Conversa com o
  recadastramento do IBGE.
- **seção da CNAE** (A a U) — qual é a *atividade*. Conversa com a Receita e
  com a tarifa.

Um galpão pode ser espécie 6 e seção C; uma casa com salão na frente pode ser
espécie 1 e seção G.

## Os medidores

A percepção conta as caixas de medidor de energia e água na fachada. Vários
medidores num imóvel de ligação residencial é indício forte de mais de uma
unidade consumidora — que é exatamente o que a companhia procura. `NULL`
significa "não foi possível contar", nunca zero.

## Como rodar

```bash
python avaliar_tudo.py --area area_atual --aplicar          # tudo
python avaliar_ia.py --poi 99207 --aplicar --refazer        # reconferir um caso
python capturar_pagina.py --poi 85335 --aplicar             # só a página
```

No painel: **Categorias para a IA** (marcar o que vale foto) e depois
**Avaliar com IA**. O contador ao lado do botão mostra quantos POIs a corrida
alcança *antes* de gastar.
