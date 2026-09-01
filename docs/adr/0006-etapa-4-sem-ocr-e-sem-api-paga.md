# ADR 0006 — A etapa 4 sem OCR e sem API paga

**Data:** 01/09/2026
**Estado:** aceito, ainda não implementado

## O problema

A etapa 4 recortava tiles do mapa, rodava OCR para ler o nome dos POIs, e depois
**procurava esse nome** no Maps para obter o resto. Daí os três baldes do
relatório — `Match válido`, `Distância alta`, `Não encontrado`: buscar por nome
lido em pixel erra, e erra de formas que não dá para auditar depois.

A alternativa óbvia era a Places API, que é exata e é **cobrada por POI**.

## A decisão

Nenhuma das duas. O caminho tem três passos, todos medidos em 01/09/2026 na
quadra `430460605000002P/1`, Canoas Centro:

### 1. Colher o `placeId` de graça

O mapa JS entrega o `placeId` no **próprio evento de clique**, antes de qualquer
requisição:

```js
map.addListener('click', e => {
  if (e.placeId) {
    ids.push({placeId: e.placeId, lat: e.latLng.lat(), lng: e.latLng.lng()});
    e.stop();          // impede o cartão de abrir — e é o cartão que custa
  }
});
```

Sem `e.stop()`, abrir o cartão dispara `places.googleapis.com/.../Places/GetPlace`,
que é Place Details cobrado. Medido: **64 chamadas cobradas** num tile.
Com `e.stop()`: **zero**, e os mesmos 36 `placeId`.

### 2. Abrir o Maps de consumidor pelo id, via proxy

`https://www.google.com/maps/place/?q=place_id:<ID>` é navegação, não API. E vai
**direto no estabelecimento certo** — não há nome para casar, logo não há match
errado.

Medido num tile inteiro, 6 navegadores em paralelo, um proxy cada:

| | |
|---|---|
| POIs no tile | 36 |
| extraídos com nome | 35 (97%) |
| com endereço / telefone | 35 / 30 |
| CAPTCHA | 0 |
| chamadas cobradas | 0 |
| tempo | 58 s no tile (1,6 s por POI) |

O único erro foi falha ao subir o navegador — ver a decisão sobre o binário,
abaixo.

### 3. Extrair tudo, por âncora semântica

Nome, categoria, nota, endereço, telefone **e mais**: histograma por estrela,
resumo do Gemini, avaliações com resposta do proprietário, horários de pico hora
a hora, horário dos sete dias, fotos, acessibilidade, formas de pagamento,
"Localizado em", e o diretório de galeria.

Nunca por classe CSS — as do Maps são ofuscadas e trocam a cada release. Sempre
por `aria-label`, `data-*` e `role`, que existem por acessibilidade e por isso
são estáveis:

```
histograma       aria-label="5 estrelas, 116 avaliações"
horário de pico  aria-label="Movimento às 12:00: 100%."
avaliações       [data-review-id]
resumo IA        [data-about-this-summary-url]
ordenação        aria-label="Classificar avaliações"
galeria          aria-label="Fotos de X"   (plural — o singular "Foto de
                 Fulano" é o avatar de um avaliador, e abre a pessoa)
```

Existe ainda `window.APP_INITIALIZATION_STATE` (108 KB, com nome, telefone e
CEP), que não depende de DOM nenhum — reserva caso as âncoras caiam.

Medido na Flora Casa São Sebastião, 126 avaliações:

```
histograma     {5:116, 4:4, 3:2, 2:2, 1:2}   soma 126, fecha exato
horário pico   09h 62% · 10h 74% · 11h 81% · 12h 74% · 13h 59%
assuntos       preço 11 · axé 3 · incensos 3 · agilidade 3 · brinde 2
resumo IA      "As pessoas dizem que esta loja oferece uma ampla variedade
                de produtos de qualidade..."
avaliações     50 por visita, ordenadas por mais recentes, com autor, nota,
               quando, Local Guide, contagens do autor, texto e resposta do dono
fotos          17 URLs do lugar
sobre          serviços, comodidades, Wi-Fi, pagamento, acessibilidade
```

Três armadilhas de parsing, todas encontradas medindo:

- **`m[êe]s` não pega "meses"** — o plural de "mês" é irregular, e alternativa
  de regex é ordenada: `/m[êe]s|meses/` casa "mes" primeiro e parte a palavra
  ao meio (`7 mes` + `es atrás`). A forma longa tem de vir antes.
- **A mesma avaliação aparece na visão geral e na aba** — sem dedup por
  `data-review-id`, 10 avaliações viram 52.
- **O `innerText` do cartão traz o rodapé** ("Gostei", "Compartilhar",
  "… Mais") e o selo "NOVA". Nada disso é texto do avaliador.

## O zoom continua 20

Testado a pedido: zoom menor cobre mais área por tela e poderia render o mesmo
com mais passadas. **Não rende.** Mesmo retângulo de referência (116 m × 166 m),
mesmas 8 passadas:

| zoom | POIs achados | área por tela |
|---|---|---|
| 20 | **49** | 1× |
| 19 | 26 (53%) | 4× |
| 18 | 11 (22%) | 16× |

A perda em zoom menor é por colisão de rótulo e **não é recuperável** por
passada: a curva do z19 estagnou em 26 já na quinta passada. O teste do z18 foi
interrompido por decisão do usuário assim que o z19 falhou.

## A sobreposição de varredura é adaptativa

Um tile sozinho vê **59%** do que existe no próprio retângulo — o resto some por
colisão de rótulo. Medido isolando o efeito: dos 53 POIs conhecidos dentro do
retângulo de um tile, ele mostrou 31.

A descoberta acumulada, com 17 varreduras deslocadas sobre o mesmo retângulo:

```
 1 varredura →  29      6 →  41 (84%)
11 varreduras →  47      15 →  49      17 →  49  (parou)
```

Decisão: **passo de meio tile, refinando só onde a varredura ainda acha POI
novo.** A sobreposição não é custo extra — varrendo com passo de meio tile, cada
ponto já cai dentro de 4 tiles vizinhos por construção.

## O OCR sai; os tiles ficam

Com o `placeId` entregando nome e categoria escritos e exatos, não sobra nome
para reconhecer em pixel. O OCR sai do processo.

Os tiles continuam sendo recortados, por outro motivo: identificar telhado
comercial no `mapa_pois_telhados` (mapId `33696f50cbe8e2d298796ada`), onde os
POIs são verde vivo uniforme e os telhados comerciais, rosa forte.

## O binário do navegador muda

O container roda `chrome-headless-shell`. Ele tem dois defeitos, com uma causa
só:

- **estoura com SIGSEGV** ao subir, de forma intermitente — inaceitável com 20
  navegadores de pé o dia inteiro;
- **se anuncia**: `navigator.webdriver=true`, `plugins=0`, sem `window.chrome`, e
  `userAgentData.brands` traz `HeadlessChrome` ainda que o user agent esteja
  falsificado.

Chromium completo (`/ms-playwright/chromium-1223/chrome-linux64/chrome`) sob
Xvfb, com `--disable-blink-features=AutomationControlled`:

| | headless-shell | Chromium + Xvfb |
|---|---|---|
| `navigator.webdriver` | `true` | `false` |
| marcas | `HeadlessChrome, ...` | `Not/A)Brand, Chromium` |
| `plugins` | 0 | 5 |
| `window.chrome` | ausente | presente |
| SIGSEGV | intermitente | não ocorreu |

Isso **inverte a ressalva sobre humanização**. Enquanto o navegador entregava a
bandeira, intervalo aleatório e arrasto de mapa eram teatro para uma plateia que
já tinha lido o roteiro. Sem a bandeira, passam a valer.

> `xvfb-run` trava neste container: sobe o Xvfb e fica preso no laço de espera
> sem nunca executar o comando. O Xvfb tem de subir à mão, com `DISPLAY` por
> variável de ambiente.

## Os tiles: em segundo plano, e só o do Google

Os tiles deixam de bloquear o processo — são recortados em segundo plano
enquanto o resto segue.

### A sobreposição do OSRM foi construída, medida e descartada

Chegou a ser montada e funcionava. O que a derrubou foi uma constatação simples:
**o OSRM não tem telhado.** Sendo roteador, o tile dele expõe só o grafo de
rotas — `speeds` (velocidade, peso, duração, e o nome da rua), `turns` e
`osmnodes`, tudo `LineString`. Nenhum polígono, nenhuma edificação. Os telhados
rosa da imagem sempre vieram do próprio Google, do estilo `mapa_pois_telhados`.

Fica registrado o que se aprendeu, caso a ideia volte:

- O endpoint existe e responde nas duas instâncias já de pé (`osrm-carro` em
  7300, `osrm-pe` em 7310): `/tile/v1/{perfil}/tile(x,y,z).mvt`.

  | zoom | 12 | 14 | 16 | 18 | 19 | 20+ |
  |---|---|---|---|---|---|---|
  | resposta | 1,4 MB | 184 KB | 29 KB | 2 KB | **vazio** | **400** |

  O teto útil é z18. Não seria obstáculo: MVT é vetor, um tile z18 cobre 16
  tiles z20 e a precisão em z18 é de ~3 cm por unidade.

- O alinhamento não precisa de conta de projeção: basta desenhar o GeoJSON
  **dentro do próprio mapa do Google**, na camada `Data` — mesma tela, mesma
  projeção, mesmo centro.

- Duas armadilhas, se alguém refizer: esconder o basemap por CSS **apaga junto
  a camada `Data`** (precisa de um segundo mapa sem `mapId`, porque `styles` é
  ignorado quando há `mapId`); e mapa com `display:none` **nunca dispara
  `idle`**, então a espera estoura.

- Telhado de fonte independente do Google existe e não custa download:
  `brazil-latest.osm.pbf` já está em disco no i9, e dele sai o polígono
  `building`. Não foi feito — decisão de 01/09/2026 é ficar só com o Google.

Sob Xvfb não há GPU, então o mapa vetorial cai para raster
(`Attempted to load a Vector Map ... Falling back to Raster`). Não impede nada:
o `mapId` continua valendo e o estilo do `mapa_pois_telhados` é respeitado.

### Onde as imagens ficam

No **Storage do Supabase self-hosted**, não em pasta do sistema, em **WebP**
qualidade 90. Uma imagem por posição, **137 KB** medidos no tile de Canoas.
Com passo de meio tile (~21 mil posições) Canoas fica em **~2,9 GB** — bem
dentro do teto de 30 GB por cidade média.

## Os navegadores: quentes, e nunca fechados

- **20 no i9 principal** (100.66.173.63) e **15 no Predator**
  (`a2l-predator-phn16-72-1`, 100.74.126.57).
- **Nunca fechados.** Renovados uma vez por dia, os 35 de uma vez, por comando
  explícito — nunca automaticamente ao fim de cada run. Navegador quente
  trocando só de proxy é mais confiável que navegador novo a cada POI.
- **Intervalo aleatório** entre POIs, não o mais rápido possível: dá tempo de o
  dado chegar e não desenha um padrão.
- **Arrasto de mapa**, onde for útil.
- **Curtida em POI fica de fora**: exige conta Google logada. Sem login a
  navegação é anônima e não há identidade para o Google acumular histórico
  contra — logar provavelmente facilitaria o bloqueio, não o contrário.

> Pendência: o i9 principal ainda não tem chave SSH no Predator
> (`Permission denied (publickey)`). Precisa ser autorizada antes de instalar
> o pool lá.

## Consequências

- Some o custo de Places da etapa 4 — era o maior item de custo variável.
- Somem os baldes `Distância alta` e `Não encontrado`: navegando por id, não há
  match para errar.
- O `placeId` é o identificador canônico do Google para o estabelecimento e sai
  de graça — serve de chave natural para o `POIS_UNICOS`.
- A etapa 5 (resgate por busca de nome) fica sem função e sai do processo.
- Passa a depender de navegação com proxy, que é frágil por natureza: o pool de
  500 IPs da Webshare já trata rotação e cooldown, e no teste ficou em 250
  disponíveis com 0 queimados.
