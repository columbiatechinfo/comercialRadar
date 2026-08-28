# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- **Desfazer uma fusão passou a ser possível** (`migrations/0036`,
  `--desfundir`). Até aqui não havia como uma regra nova alcançar o que já
  tinha sido fundido: `--recruzar` reavalia os pares, mas só entre POIs
  **ativos**, e quem foi absorvido está fora da consulta. O
  `ParkShoppingCanoas` seguia dentro da `Pista de Patinação (Iceland)` mesmo
  depois de escrita a regra que o teria impedido.

  **A causa era de modelagem.** Absorver fazia `update pois set status =
  'fundido'` — e `status` não é campo de fusão: ele guarda a **origem** do
  ponto (`estadual`, `descoberto`, `ok`, `recuperado_web`, `cadastur`), aparece
  na ficha e é contado no painel. Sobrescrevê-lo apagava esse dado para sempre.
  A fusão ganhou colunas próprias:

  | coluna | o que guarda |
  |---|---|
  | `fundido_em` | quando foi absorvido. NULO = é um ponto — passa a ser **este** o teste de "ativo" |
  | `fundido_para` | em qual POI entrou, gravado enquanto a cadeia ainda existe |

  **O `status` de 15.260 dos 15.399 foi restaurado.** Ele não estava gravado em
  lugar nenhum, mas era dedutível: POIs da mesma origem (`fonte`, `fonte_dado`,
  `sessao`) receberam o mesmo valor, e 99,1% caem em cohorte de valor único. Os
  139 restantes ficam com a lacuna visível — inventar um valor plausível seria
  pior.

  **O destino de 13.263 fusões antigas foi recuperado** (`backfill_fundido_para.py`)
  rastreando o vínculo que carrega o nome do absorvido. Os 1.806 cujo nome
  aponta para mais de um POI ativo ficam sem destino, e **por isso não são
  desfeitos**: sem saber o sobrevivente não há como devolver a evidência, e o
  ponto voltaria oco — invisível no mapa, que exige vínculo ativo.

  **Três defeitos meus foram medidos e consertados antes disto valer:**

  | sintoma | causa |
  |---|---|
  | `duplicate key (IGREJA NOSSA SENHORA DO ROSÁRIO…)` | o índice único também testava `status`; e cópia literal não deve voltar |
  | `duplicate key (LABORATÓRIO DE ANATOMIA, ULBRA…)` | dois absorvidos iguais entre si voltavam juntos |
  | **10.690 sobreviventes ocos** | devolver *todos* os vínculos de nome igual tirava também o do sobrevivente |

  O último é o que mais enganava: 13.252 fusões desfeitas produziam 11.314
  pontos sem evidência, e a maioria era de quem **ficou**, não de quem voltou.
  Hoje volta um vínculo por ressuscitado, e nunca o último do sobrevivente —
  0 sobreviventes ocos, 46 ressuscitados aguardando o `povoar_vinculo`.

  Desfazer e refazer estão na **mesma transação**: se o cruzamento falhar, o
  mapa não fica com 13 mil duplicatas à mostra.

- **Mais de dois nomes no mesmo lugar é um prédio, não uma dúvida de nome**
  (`TETO_MULTILOJA`). Regra do dono do produto: *"mais de 2 itens de nome
  diferente no mesmo lugar já não é apenas ambiguidade de nome do mesmo
  estabelecimento (…) mais de 2 significa um shopping ou multilojas, nesse caso
  cada um é um estabelecimento mesmo"*.

  DOIS nomes ainda pode ser o mesmo negócio escrito de duas formas —
  `Restaurante Tempero e Arte` e `Tempero & Arte` no mesmo número. TRÊS ou mais
  não: é galeria, shopping, centro clínico, campus.

  **O que ela conserta.** `ParkShoppingCanoas` tinha sido fundido com a
  `Pista de Patinação (Iceland)` de dentro dele, e a IA decidiu por evidência
  que é toda verdadeira: `mesmo domínio: parkshoppingcanoas.com.br · a 12 m`. O
  domínio é do shopping, e todas as lojas o exibem. No 4545 da Avenida
  Farroupilha há **181 nomes distintos** — endereço, domínio e coordenada são
  idênticos para os 181, e nenhum deles identifica ninguém.

  **A distribuição em Canoas** mostra que o limiar cai no lugar certo:

  | nomes distintos na porta | portas |
  |---|---|
  | 1 | 12.945 |
  | 2 | 2.244 |
  | 3 ou mais | **1.207** |

  **"Mesmo lugar" não é "mesma string de endereço"**, e a primeira versão desta
  regra errou nisso. O shopping está em `AVENIDA FARROUPILHA 4545`; a pista
  dentro dele tem logradouro `PARKSHOPPINGCANOAS` e **nenhum número** — a loja
  de dentro usa o nome do prédio como rua. Exigir a mesma porta deixaria de fora
  justamente o caso que motivou a regra. Vale, então, dentro de `RAIO_M`, o
  mesmo raio que o site e o telefone já exigem para valer; e a marca contagia
  quem está a essa distância de uma porta-multiloja.

  **O efeito medido** em Canoas: as perguntas à IA com nomes diferentes caem de
  **83.683 para 6.109 — 93% a menos**. São exatamente as perguntas em que a IA
  vinha respondendo "MESMO" para o shopping. Nenhuma fusão automática mudou,
  porque no estado atual não há nenhuma pendente: todas já aconteceram.

  Sai por `descartar` e não por `perguntar` porque *"cada um é um
  estabelecimento mesmo"* é uma afirmação, não uma dúvida — e foi a IA que
  fundiu o shopping com a pista olhando essa mesma evidência.

- **O que não é comparável não entra** (`trigger poi_comparavel`,
  `trigger vinculo_comparavel`). Um registro só é aceito se houver COMO
  compará-lo com outro — a regra não é "tem nome" nem "tem endereço", é ter,
  junto, o bastante para identificar. As 11 linhas do quadro foram provadas com
  inserção real:

  | nome | endereço | coordenada | veredito |
  |---|---|---|---|
  | ✅ | qualquer forma | — | aceita |
  | ✅ | ✗ | ✅ | **recusa** — gere o endereço antes de inserir |
  | ✗ | com número | — | aceita (a porta identifica sozinha) |
  | ✗ | só logradouro | ✅ | aceita (a coordenada supre a porta) |
  | ✗ | só logradouro | ✗ | **recusa** |
  | ✅ ou ✗ | ✗ | — | **recusa** (só o nome, ou só a coordenada) |

  É *trigger* e não *CHECK* porque o número canônico mora em
  `logradouro_ajustado`, e um CHECK do Postgres não consulta outra tabela. Ele
  também olha o padrão no próprio `endereco`: no instante do INSERT a
  normalização ainda não rodou para aquele POI, e sem isso todo POI sem nome
  seria recusado por um número que só existiria minutos depois.

- **`endereco_reverso.py` — a coordenada vira endereço com número.** Cascata
  **CNEFE → Maps**, sem OSM. O Photon do i9 foi testado e reprovado por
  medição, sobre 200 POIs de Canoas:

  | | acha porta | ≤ 20 m | ≤ 50 m | por ponto |
  |---|---:|---:|---:|---:|
  | Photon (`layer=house`) | 99% | **2%** | 7,5% | 26 ms |
  | **CNEFE (IBGE)** | 100% | **85%** | **96%** | **0,18 ms** |

  Aquele 99% é o tipo de número que engana: o Photon devolve a porta mais
  próxima que *conhece*, e o OSM quase não tem numeração predial no RS —
  "Eixo Sul Distribuidora" recebia um endereço a **834 metros**. Gravar isso
  não seria buraco, seria corrupção: endereço errado casa com o vizinho errado
  no cruzamento. O CNEFE já está carregado para os **5.570 municípios do país**
  (111.102.875 endereços). O raio de 50 m é onde a medição para de ser
  confiável; além dele a porta mais próxima começa a ser a do outro quarteirão.

- **`pois.endereco_gerado_por`** — marca de onde veio o endereço quando ele
  **não** veio da fonte (`cnefe` ou `maps`). Endereço observado e endereço
  inferido não são a mesma coisa, e sem a marca ninguém separa os dois depois.

- **`area_utils.codigo_ibge_da_area`** — o código IBGE sai da mesma consulta
  espacial que já resolve cidade e UF. Existe porque o CNEFE é indexado por
  **código**: "Santana" existe em nove estados, e casar por nome traria as
  portas do município errado — pior que endereço nenhum, porque parece certo.

- **Segundo caminho de candidatos na fusão** (`cruzar_fontes.candidatos`):
  "mesmo nome na mesma rua" é uma **chave**, não um raio. A grade de 111 m
  alcançava ~330 m, e a regra dos 1.000 m era letra morta justamente na faixa
  que existia para cobrir — 334 de 344 grupos duplicados estavam a mais de
  100 m. Custo do canal novo: **412 pares, 0,017%** do total.

### Corrigido

- **O POI sem nome derrubava a extração estadual.** A base do estado traz ponto
  com categoria e endereço, sem nome — `Posto de Combustível, AVENIDA GETULIO
  VARGAS, 7500`. Pela matriz acima ele é *comparável* (sem nome, mas com número
  da porta); quem recusava era a coluna `pois.nome`, `NOT NULL`, com
  `NotNullViolation` que matava o passo 2 inteiro. Agora grava `""` e o trigger
  `poi_comparavel` volta a ser o único juiz de quem entra. Provado rodando no
  i9: saída 0, **4.191 POIs gravados**, 925 pulados por não terem endereço nem
  porta do CNEFE a 50 m.

- **A auto-cura do worker trocava o perfil e ficava com o mesmo IP.** Uma
  categoria de 46 (`tabacarias`) terminou `NÃO BUSCADO`. Eram dois defeitos
  somados: a cura descartava o perfil do Chromium mas reaproveitava o proxy — se
  o IP é o problema, a segunda tentativa falha idêntica — e a marca `curou`
  nunca voltava a `False`, o que dava **uma cura só por worker na run inteira**.
  Agora são até 3 curas por worker, cada uma manda o IP para o castigo
  (`mark_cooldown`, 600 s) e pega outro do pool.

- **A fusão nunca via a duplicata longe, e o número da porta decide.** Dos 266
  pares que a regra fundiria, 167 tinham o **mesmo número** ("Posto Ipiranga,
  Guilherme Schell 1046" contra o mesmo endereço a 7,7 km — a coordenada de uma
  fonte é que erra) e 48 tinham número **diferente** ("Saque e Pague" nos
  números 1011 e 1623 da mesma avenida: caixas distintos). Agora número igual
  funde a qualquer distância; número diferente cai nos pontos e na IA.
  Efeito: 266 → **218** fusões automáticas, "Saque e Pague" de 48 para **0**.

- **O mapa mostrava os pontos FUNDIDOS.** `server.py` não filtrava
  `status='fundido'` nem exigia vínculo ativo. Medido em Canoas: o mapa
  devolvia **49.636** pontos quando a cidade deduplicada tem **28.533**. Os
  absorvidos continuavam desenhados ao lado de quem os absorveu, e a
  deduplicação inteira não aparecia para quem olha. O banco já estava certo; o
  mapa é que nunca tinha sido avisado.

- **A gravação das fusões vira lote** — 26.542 idas e voltas ao Postgres do i9
  viravam ~100 minutos. Medido sobre 400 fusões reais: **2 → 1.596 fusões/s,
  602×**. A *decisão* continua sequencial, porque a transitividade exige ordem;
  só a *escrita* virou lote, em blocos de 1.000.

- **A comparação gastava o tempo normalizando o mesmo nome.** Perfilando 200
  mil pares: 72,4 s, e o gargalo era `unicodedata.category` (39,3 milhões de
  chamadas) — cada POI aparece em ~140 pares e tinha o nome normalizado 140
  vezes. As cinco funções de texto ganharam memória: **35,2 s → 5,2 s, 6,7×**,
  com **200.000 de 200.000 vereditos idênticos**.

- **A busca podia ficar viva sem trabalhar.** `_abrir` devolvia `False` sem
  imprimir quando o pool não dava proxy, e o laço tentava de novo **sem teto**.
  Duas runs ficaram penduradas 14 e 10 minutos, com log parado e CPU no chão.
  Agora ela diz o que houve (com `flush`, senão a mensagem fica no buffer),
  desiste após 5 tentativas e o resumo final denuncia quantos POIs ficaram sem
  busca — antes eles sumiam da conta e "3 processados" se lia como "3 de 3".

- **"0 no Maps" não podia significar duas coisas.** Uma run inteira reportou 0
  nas 46 categorias em pleno bairro comercial. A causa era o perfil de
  navegador apodrecido (mesma URL, mesmo proxy: perfil velho → `goto` timeout;
  perfil novo → 20 links). O defeito real era `buscar_categoria` devolver `[]`
  calada, fazendo falha de infraestrutura ter a mesma aparência de resultado
  legítimo. O worker agora descarta o perfil e refaz a sessão.

- **O painel do Maps leva 8 s, e o teto era 9.** `WAIT_PROXIMO_MS` 9.000 →
  25.000. Um segundo de folga passava em rede boa e falhava no roteador do
  celular. A mensagem mentia — dizia "não encontrado", que se lê como "esse
  botão não existe" — e mandou o diagnóstico para idioma da página, perfil
  corrompido e muro de consentimento antes de alguém medir o tempo.

- **`S` só vira `SÃO` contra lista** — **222 de 253** expansões estavam erradas.
  `QUADR S UM` (letra de quadra), `BECO S NOME` e `ESTRADA S DENOMINAÇÃO`
  (`S` = SEM, e o dano não é só o santo inventado: apaga-se o sinal de que a
  via não tem nome), `RUA S SALVADOR DALI` (o pintor).

- **`config.py` entrou na sincronização do i9.** Um teste o proibia alegando
  que guardava caminho da máquina; não guarda — tudo sai de
  `Path(__file__).resolve().parent` e credencial vem do `.env`, esse sim nunca
  sincronizado. A proibição custou caro: o teto de 25 s ficou no notebook e a
  run seguinte falhou igual, com a mensagem entregando a causa sem querer
  ("não pintou em 12s" = 9.000 + 2.500). `tests/test_sincronia_i9.py` passou a
  verificar o **fecho transitivo dos imports** — e encontrou
  `spatial_clustering`, `extract_full`, `realtime_ingest` e `auth` rodando
  velhos no i9 sem que ninguém soubesse.

### Removido

- **Dado impossível de comparar, e município fora de escopo.** Da tabela de
  POIs (nunca das bases): **29.355** de outros municípios, **6.205** sem nome
  ou sem endereço, **93** duplicatas exatas (ficou a do Maps) e 5 sem vínculo.
  `pois` foi de 83.235 → **47.577**. `cadastro_cliente` (102.065),
  `cnpj_tratado` (27.147) e `cnefe_coletiva` (27.227) ficaram **intactas** — as
  FKs separam por construção: `CASCADE` no que é derivado do POI, `SET NULL`
  nas bases.

- **`"nan"` de dentro do JSON dos vínculos** — 74.572 campos em 39.436
  registros (site 37.709, telefone 23.407, endereço 11.972, categoria 1.484).
  A chave permanece com valor `null`: quem lê sabe que o campo está vazio, em
  vez de ler "nan" como se fosse um site chamado nan.

### Adicionado (rodadas anteriores)
- **Endereço grudado lido pela IA da Spark** (`segmentar_endereco.py`): a
  `pois` guarda um campo `endereco` só, com formatos diferentes dentro da mesma
  coluna, e a skill `ajuste-logradouro` declara que não segmenta campo único.
  A IA lê; todo campo devolvido precisa **existir no texto original** ou é
  descartado com o motivo. Uma chamada por valor distinto, gravação a cada 25
  lotes e retomada pelo que já está em `endereco_segmentado`.
- **`ajuste_logradouro.py`** — a skill `ajuste-logradouro` v3.3.5 virando dado
  no banco. Quatro fontes no mesmo scope municipal (`cnefe` como
  `autoridade_nivel 100`, `cadastro_cliente`, `pois`, `ifood_merchant`), com a
  forma canônica marcada e o **tier** dizendo quanto confiar. Medido em Canoas:
  108.835 registros em 17,3 s, `CONFIRMA=105.928 · ALTA=1.672 · REVISAR=1.235`.
- **`scripts/i9/lancar.sh`** — trabalho longo no i9 vira unidade do systemd do
  Ubuntu. `setsid`/`nohup` e o `Start-Process` do PowerShell **não sobrevivem**
  ao fim da conexão SSH, e falham em silêncio: foi por isso que uma produção
  "disparada" morreu calada e alguém disparou de novo.
- **Trava de instância única na produção estadual** (`flock` em dois níveis):
  uma produção do Brasil por máquina, um pipeline por UF.
- **Cadastur/MTur como passo do processo e gerador de POI** (`cadastur.py` + skill
  `extracao-cadastur-mtur`): baixar → carregar → cruzar → gerar, com o gerador
  recusando rodar sem o cruzamento. Única fonte do sistema com **capacidade
  declarada** (leitos). Atualização incremental pelo botão ↻, em que quem SAIU do
  arquivo do MTur ganha `saiu_em` em vez de ser apagado.
- **Procedência declarada da coordenada** em todo POI: `porta` (15 m),
  `porta_aprox` (40 m), `via` (150 m), `desconhecida`. Aparece na ficha, no payload
  do mapa e numa fileira própria de filtro.
- **Geocodificador em cascata** (`geocodificar.py`): Photon → Nominatim → painel do
  Maps, na ordem de custo. Só depois dos três é que existe "não encontrado".
- **`via_confere`**: o geocodificador confere a via que devolveu. Três calibrações
  contra dado real levaram a reprovação de 29,7% para 10,2%.
- **CNEFE como terceira âncora de coordenada** do Cadastur — 16 dos 17 pontos de
  Esteio vieram só dela, todos com precisão de porta.
- `conferir_coordenadas.py` — varredura da base; move só o que cai **fora** do
  município que declara.
- `identificar_divergente.py` — acha foto e análise de IA capturadas na coordenada
  errada, com três destinos distintos conforme o que está provado.
- `verificar_servicos.py` — 7 verificações de **função**, não de porta: o Nominatim
  geocodifica, o OSRM traça rota, o Ollama gera palavra, o banco conta linhas.
- Tarefa agendada no i9 que **reaplica o encaminhamento WSL depois do boot**, roda
  como SYSTEM, acha o IP do WSL sem `wsl.exe` e confere se cada regra amarrou.
- `frontend/tokens.css` — o vocabulário visual, medido em vez de copiado.
- `guardar_ponto` no chat: o que o agente descobre passa a ser gravado pelo
  escritor único de POI, fechando o ciclo.
- As **26 migrações SQL** entraram no repositório — o `.gitignore` as levava junto
  com os dumps, e a história do schema vivia só no disco.
- Leitura de fachada por IA (`avaliar_fachada.py`) com o validador da skill
  `leitura-fachada-cadastral` como porteiro — anotação reprovada não entra na base.
- Unidades coletivas do CNEFE como terceira fonte independente
  (`coletivas_radar.py`, `coletivas_importar.py`), pelo método do Radar Coletivo.
- Aba **Avaliar candidatos** no painel, com menu flyout, cards próprios e ficha em
  tela cheia com as imagens que a IA usou.
- Aba **Dashboard** com cobertura, custo real × cenário Google e cálculo de
  retorno direto por faixa de qualidade.
- CNPJ pela base local da Receita Federal (`cnpj_local.py`), com a estrutura do
  número como critério de aceite e a base nacional como nível de confiança.
- Escrita atômica de JSON (`io_atomico.py`) que não derruba a rodada em
  `WinError 5`.
- Documentação de projeto: PRD, arquitetura em mermaid, RBAC, catálogo de módulos,
  checklist, ADRs e painel de acompanhamento.

### Alterado
- **O banco saiu do `localhost` para o i9** (Supabase self-hosted), os bytes de
  imagem saíram do banco para o **Storage**, e o schema virou `comercialradar`.
  O banco do produto caiu de 60 GB para 0,51 GB.
- **Distância não prova erro; município prova.** A varredura de coordenadas só move
  o ponto que cai fora do município que declara — foi 1 caso em 11.961. A primeira
  versão, que moveria tudo a mais de 2 km, teria sido destrutiva.
- **O campo `cidade` deixou de ser autoridade.** A trava só acusa quando a
  coordenada discorda do rótulo **e** do CEP — que vem do CNEFE e é terceira fonte
  independente. Antes disso, 8 fotos e 3 análises corretas foram apagadas por
  confiar no rótulo.
- **A conferência de município virou geométrica**, contra o polígono do IBGE com
  2 km de folga. Por nome, três pontos passaram e caíram a 171, 270 e 499 km.
- **O teste de coerência mede contenção no polígono**, não distância do centroide,
  e separa deslocamento (>25 km, derruba a suíte) de rótulo de cidade errado
  (até 25 km, pinado no número).
- **`nv_geo_coord`**: a semântica dos níveis 3 e 4 estava descrita errada; o piso
  continua em 2, agora por motivo medido — aceitar 3 e 4 resgataria zero prestador.
- **O front inteiro passou a ler `tokens.css`**: de duas famílias de cinza, dois
  azuis e 92 tons únicos para um vocabulário só. Auditoria de contraste em 796
  elementos: de 15 reprovações para zero.
- POI reencontrado é **atualizado**, não recriado; POI sem `place_id` deixou de
  duplicar a cada reingestão.
- **Remoção da interface do Google das imagens** antes de qualquer modelo vê-las.
  Pedir que ignorasse não funcionava: o modelo lia o endereço da caixa preta como
  número do imóvel.
- **O endereço do cadastro saiu do prompt.** Com ele, o `gpt-4o` devolvia o número
  informado — 3 de 5 leituras mudaram ao removê-lo — e a conferência do código
  ficava circular.
- **Divergência de numeração exige segunda vista**: o número é relido num recorte
  ampliado da mesma foto. Autodeclaração de legibilidade não serve e repetir a
  chamada também não.
- Aptidão da imagem virou chamada separada, com perguntas de sim ou não.
- Street View passou a ser opcional, restrito a POIs pobres.

### Corrigido
- **A área de trabalho desenhada não era gravada, e a tela dizia que sim.** O
  `root` não pertence a empresa nenhuma, `auth.conectar_como` não declarava
  `app.tenant_id`, a trigger não tinha o que carimbar e o `NOT NULL` recusava a
  linha — `POST /api/area` respondia 500. O front chamava `fetch` sem olhar a
  resposta e anunciava "Área salva ✔". Resultado: a mineração varreu 264 tiles
  do município errado, a partir de uma área de nove dias antes.
- 13 POIs tinham foto de fachada e análise de IA capturadas na coordenada errada —
  três pousadas do Piauí com seis fotos cada, a até 20 km do próprio endereço.
  Tratados em três destinos; 41 objetos órfãos removidos do Storage.
- 245 POIs rotulados `bairro`/`municipio` porque o geocodificador só resolveu até a
  cidade — o rótulo dizia que o ponto **é** um centroide, o que ele não é. Desfeitos.
- 4.442 POIs ficaram sem prestador apontando para eles quando uma execução estadual
  foi interrompida: o vínculo só era gravado no fim. Agora grava em lote de 100.
- O complemento roubava o número do endereço ("… 2586 LOJA 3 SALA 1" lia 1) e a
  notação de faixa da Receita ("de 3501 a 5101 - lado impar") virava número.

### Removido
- **Google Places API.** Cobrava por chamada e trazia o mesmo tipo de ponto que
  a captura + OCR traz de graça. Saíram com ela o "passo da grade" e a "coleta
  profunda", que eram opções só dela. A rota recusa `motor=places` com 410 em
  vez de ignorar em silêncio — aba antiga no navegador dispararia outro job sem
  dizer por quê. `minerar_area.py` fica no repositório como histórico.
- Processo de quadras, régua de numeração e biblioteca de recortes de construção —
  foram para o **radarTelhados** em 11/08/2026.

### Segurança
- A chave do Google exposta no commit `1c7f081` foi **revogada e confirmada morta**
  (`REQUEST_DENIED`/expired). Três chaves novas, separadas por função; a de servidor
  deixou de ser a mesma do navegador. gitleaks no pre-commit e no CI.
- Backup diário com **restauração validada** em máquina limpa.
- `cache_ibge/` (135 MB) fora do repositório.
- **RLS completa:** as 4 tabelas criadas depois do passe de 12/08 ganharam
  política, gatilho e índice (migração `0029`), e o teste passou a cobrar a regra
  geral — tabela nova sem política reprova a suíte.
- **Pendente:** 100 credenciais de proxy Webshare no histórico do git desde o
  commit inicial. O arquivo saiu do rastreamento em 24/08; **rotacionar na
  Webshare é a única correção real** — reescrever commit não apaga o que já foi
  publicado.
- **Pendente:** 66 registros de `auditoria` com `tenant_id` nulo, invisíveis para
  todos. Não retro-atribuídos: trilha editada depois do fato vale menos que
  trilha incompleta.
