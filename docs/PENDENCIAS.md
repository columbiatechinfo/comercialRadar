# Pendências do comercialRadar

Última revisão: 10/09/2026, madrugada.

Este arquivo é a lista do que ficou em aberto, com o motivo e o que destrava
cada item. Ele existe porque conversa rola para cima e some; pendência que só
vive no chat vira pendência esquecida.

---

## Bloqueadas por terceiros

### 1. Rota de conteúdo no `api-bases`
**O que falta:** uma forma de LER os bytes de um arquivo registrado na pasta da
empresa no Drive.

**Por que importa:** o radar já lista a pasta (`GET /api/drive/bases` traz
status, `hash_sha256` e `link_arquivo`), mas não consegue puxar o arquivo para
o `COPY`. Sem isso, a base vem do Drive para o radar por download manual.

**Verificado:** li o bundle do contêiner `api-bases` em execução
(`dist/index.js`, 34.260 bytes, de 31/08 15:17). Ele expõe cinco rotas —
`/saude`, `/tipos`, `/tipos/:id`, `/bases`, `/bases/:id` — e nenhuma de
conteúdo. Há um comentário no código: *"resumo, nunca o conteudo dos
arquivos"*. Também não estão lá `/tipos/:id/modelo` nem
`/tipos/:id/sincronizar`, que a documentação descreve — o que roda está atrás
do doc.

**Pedido:** mensagem entregue à sessão "Infra a2l" em 09/09/2026, com a
sugestão de `GET /bases/:id/conteudo` em stream (arquivos de centenas de MB) ou
uma cópia em volume compartilhado. Sem resposta até agora.

**Enquanto isso:** o upload pelo painel cobre o caso, e o `hash_sha256` da
listagem permite conferir que é o mesmo arquivo registrado.

### 2. Leitura de rede social
**O que falta:** a data do último post dos 15.627 perfis de Instagram.

**Por que importa:** é a parcela de 15 pontos do score, e para negócio de
bairro sem fachada é muitas vezes a única prova de que está aberto.

**Medido em 09/09/2026:** o Instagram redireciona o perfil para
`/accounts/login/` em **5 de 5 IPs testados**, e o pool da Webshare tem apenas
**2 blocos /24 distintos** entre seus 500 IPs — os dois murados. A API interna
deslogada devolve **429 em 0,0 s**. Do navegador comum do dono do produto, na
conexão dele, o perfil abre normalmente com um modal dispensável: a barreira é
reputação de IP, não política.

**O que está pronto:** tabela `rede_social` (por perfil, não por POI, com data,
erro e tentativas), a fila que normaliza as arrobas, o leitor compartilhado em
`ferramenta_instagram.ler_perfil` e o laço de lote com proxy e reciclagem de
IP. Falta só o IP.

**Destrava com:** proxy residencial rotativo de verdade (Bright Data, Oxylabs,
Smartproxy) ou uma API de terceiro (ScrapeCreators, Apify, SociaVault).

**Descoberto de passagem:** a data do post **não está no HTML do perfil** —
zero `taken_at_timestamp`, nenhuma tag `<time>`. Quando houver IP, será preciso
abrir a primeira publicação de cada perfil, o que dobra o custo por perfil.

---

## Do produto, não começadas

### 3. Fotos do Google sem data
**55.649 fotos publicadas, nenhuma com `data_imagem`.** A parcela de 10 pontos
não pode ser sobre recência; hoje ela pontua "a IA olhou e disse que mostra o
negócio funcionando". Coletar a data exigiria voltar às fichas do Maps.

### 4. As seis colunas de qualificação que não subiram
A planilha `CANOAS_qualificacao_v6.xlsx` traz, além da decisão:
`ALVO_FISCALIZACAO`, `QTD_UNIDADES_ENDERECO`, `AGRUPAMENTO_LIGACAO`,
`LIGACAO_ATIVA_MESMO_ENDERECO`, `STATUS_ECONOMIA_FATURADA` e
`ECONOMIA_UNICA_ENDERECO`. Estão na tabela crua `base_bruta.canoas_v6`.

Duas parecem úteis para o julgamento: **quantas unidades há no endereço** e **se
há outra ligação ativa no mesmo lugar** — as duas respondem "é prédio ou casa",
que hoje o modelo deduz das economias.

### 5. Os 35.335 POIs órfãos
Perderam o vínculo com a regra estrita e não acharam outra ligação. O caminho
já existe (`casar_por_endereco.py`), mas eles ficaram fora porque a regra nova
entrou depois. Rodar a alocação de novo sobre eles.

### 6. Coordenadas movidas por outro módulo
`corrigir_coordenada.py` moveu 3.056 POIs (`coord_fonte = 'cnefe_endereco'`),
com a anterior guardada. O dono do produto pediu que coordenadas fiquem nos
locais originais; desarmei o movimento no `casar_por_endereco`, mas **não
desfiz** esses 3.056 porque vêm de módulo anterior e pode haver razão. Decisão
pendente.

---

## Riscos conhecidos, não resolvidos

### 7. A política de escrita da `cadastro_corsan` ainda é lenta
`p_corsan_escreve` chama `core.eh_suporte()` e `core.hierarquia_atual()` **sem
subselect** — uma chamada de função por linha. A migração 0081 corrigiu só a
política de leitura, onde custava 14,5 s por consulta.

A tabela nova criada por `materializar_base.py` já nasce com a versão
corrigida, mas **a tabela em produção não**. Enquanto ninguém escrever em massa
nela, não dói; um `update` de 2,5 milhões de linhas doeria muito.

### 8. `streetview_capture.py` ainda é chamado pelo painel
`server.py` tem um modo `streetview` que executa `streetview_capture.py` — um
arquivo **que não existe mais**. O botão correspondente falharia. Não toquei
porque a captura hoje roda por outro caminho.

### 9. O modelo não reinicia sozinho depois de queda de energia
Medido em 10/09/2026: o contêiner `vllm` na Spark tem `restart: unless-stopped`
e mesmo assim ficou parado. Pior: `docker start` o subiu **sem publicar a
porta** — o serviço respondia ao próprio healthcheck em `127.0.0.1` e ficava
inalcançável de fora, o que parece "modelo no ar" para quem olha o `docker ps`.
Só `docker compose up -d` restabeleceu o encaminhamento.

**Sugestão:** um healthcheck que teste a porta PUBLICADA, e não a interna.

---

## Fila em andamento

**Julgamento v2** — 12.863 ligações, ~50/min, começou 10/09 03:20.
Só ligações `apta_cruzamento` (SIM ou SIM_COM_ANALISE_HUMANA), com vínculo por
endereço exato. Ao terminar, rodar `calcular_score.py --aplicar`.

---

## Corrigido em 10/09/2026, de manhã

### O vínculo escapava por três `join` diferentes

Pergunta do dono do produto: *"todos os vínculos atuais têm o mesmo endereço
com número?"*. A resposta era **não — 92,1%**, e eu tinha escrito 100% no
relatório. Medindo o porquê, apareceram **três fugas com a mesma forma**: a
consulta que aplica a regra só examina o que o `join` deixa passar, e **o que
ela não vê continua vivo por omissão**, porque "vivo" é só `descartado_em is
null`.

| a fuga | como escapava | quantos |
|---|---|---|
| POI fundido | `join pois … p.fundido_em is null` | 8 vínculos |
| outra cidade | `--cidade Canoas` na chamada | 586 vínculos de Gravataí |
| POI apagado | `join pois` sem chave estrangeira | 11 vínculos |

Os oito do POI fundido eram todos a **Madeireira Maravilha** (POI 78458),
apontando para ligações da Índio Sepé enquanto o POI publica "Rua das
Costureiras, 191". Nenhum dos três grupos chegou à IA — o dossiê e a fila
filtram do mesmo jeito —, então o estrago era de **contagem**, não de veredito.
Mas número que ninguém consegue explicar é número que não serve.

O terceiro virou migração: `0092` apaga os pendurados e põe
`on delete cascade`, porque **seis lugares apagam POI** e nenhum limpa o
vínculo.

### O `do nothing` engolia o resgate, em silêncio

O casamento por endereço achou 2.279 pares para os órfãos de Canoas e o banco
gravou **269**. O `on conflict (id_base, ligacao, poi_id) do nothing` descartava
os outros 2.010: o par já existia, inserido pelo cruzamento geométrico e
descartado pela revisão.

**365 deles tinham sido descartados por "número diferente" quando os números
publicados batem.** A causa é uma coluna velha: `mesmo_numero` foi gravado por
uma versão antiga de `_num` que concatenava todos os grupos de dígitos — "350
sala 2" virava "3502" e nunca casava com a porta 350. A função foi corrigida em
08/09; **a coluna não**, e `revisar_vinculo` lê a coluna.

Sem a cláusula `do update`, esse POI ficava órfão para sempre: o casamento por
endereço o encontrava toda vez, e toda vez o `do nothing` o jogava fora sem
dizer nada.

### `casar_por_endereco` nunca levou o conserto do acento

`upper(cidade) = upper(%s)` nos dois lados — o mesmo defeito que
`cruzar_ligacao` corrigiu em 03/09/2026. A Corsan grava "GRAVATAI" e a malha do
IBGE devolve "Gravataí". Só passou despercebido porque **"CANOAS" não tem
acento**, e Canoas foi a única cidade em que este módulo rodou.

`pois.cidade` tem as duas grafias: 18.274 órfãos em "GRAVATAÍ" e 393 em
"GRAVATAI". Qualquer chamada acertava um dos dois e perdia o outro — e o log
diria "0 órfãos", que parece fila vazia e não erro.

### Alocação dos órfãos, 10/09/2026

Decisão do dono do produto: o teto de 50 m **vale também aqui**, mesmo o
casamento sendo exato por logradouro + número + cidade (a regra 1).

| cidade | pares achados | entraram | ficaram na fila |
|---|---|---|---|
| Canoas | 16.868 | 1.940 | 14.347 |
| Gravataí | 9.125 | 7.152 | 1.973 |

A diferença entre as duas é a **coordenada**, não o endereço: em Canoas a
mediana do par é 228 m e em Gravataí, 17 m. Os 16.320 que ficaram na fila
publicam o endereço da ligação e têm o ponto no lugar errado — enquanto a
coordenada não for corrigida, a foto do dossiê sairia do imóvel errado.

Vínculos vivos depois: **94.646**, e a revisão completa acusa **0 a descartar**.

### O `SIM_COM_ANALISE_HUMANA` que eu escrevi era impossível

A primeira versão rebaixava a ligação sustentada só por vínculo de nome. Deu
**zero**, e o motivo é **estrutural, e não um bug**: o critério de nome exige
uma **âncora**, e a âncora é um vínculo de endereço exato da *mesma* ligação. O
vínculo por nome é testemunha a mais, nunca a única — a condição não podia
acontecer.

Regra corrigida pelo dono do produto: rebaixa *"quando for de fonte
inconclusiva, tipo airbnb, e não tiver no mesmo telhado da instalação"*. As
duas juntas. São dois jeitos independentes de provar comércio **naquela porta**
— alguém esteve lá (Maps, iFood) ou os dois pontos caem sobre a mesma
construção. Cadastro sem telhado não tem nenhum dos dois.

| flag | ligações | score médio |
|---|---|---|
| SIM | 3.527 | 25,2 |
| SIM_COM_ANALISE_HUMANA | 4.894 | 11,7 |
| NÃO | 3.399 | 10,5 |

### `max_tokens` apertava de novo, agora em prédio com 70 CNPJs

17 das 1.745 rejulgadas falharam com `Unterminated string` por volta do
caractere 1.000. Todas eram prédio comercial com **69 a 77 POIs** no mesmo
endereço: a resposta listando os POIs não cabia em 1.100. Subido para 1.600, as
17 recuperadas. É a segunda vez que o teto aperta pelo mesmo motivo — a conta
não é do tamanho do texto médio, é do **maior caso da fila**.

### A regra de endereço exato não tinha teto de distância

Achado medindo a pergunta acima. `mesma_rua and mesmo_numero` compara **texto**,
e texto não sabe onde fica: 685 vínculos vivos estavam a **mais de 5 km**, o
pior a **6.436 km**. Duas causas, nenhuma delas vínculo — o mesmo nome de rua
repetido em bairro diferente, e POI com coordenada errada.

Decisão do dono do produto: **teto de 50 m, mesmo com rua e número batendo**.

### Semelhança de nome juntava a filial

Dos 8.385 vínculos aceitos por nome, só **282 (3,4%)** estavam no mesmo
telhado, com distância média de 23,1 m — ou seja, o critério estava juntando o
**mesmo negócio em portas diferentes**, que é justamente o que não serve numa
ligação de água: cada porta tem o seu hidrômetro.

Decisão: nome e Airbnb passam a exigir **mesma rua + idf ≥ 9,0 + mesmo
telhado**. Restaram **274**.

**Consequência que o dono precisa saber: o Airbnb foi a zero.** Os 42 vínculos
da fonte caíram, porque título de anúncio ("Apartamento aconchegante perto
do centro") nunca soma 9,0 de idf contra nome de comércio. A fonte continua
coletada e os POIs continuam na base; o que sumiu foi o vínculo dela com
ligação. Reverter é mudar uma linha em `regra_vinculo.aceitar`.

### O motivo do aceite não era gravado em lugar nenhum

`descartado_motivo` explicava a queda; nada explicava a permanência. Para
responder a pergunta do dono eu tive de **deduzir** a classe de cada vínculo a
partir de `mesmo_endereco`, `mesmo_numero` e `fonte` — e dedução reconstrói o
critério de hoje sobre um vínculo gravado ontem. Migração `0091`:
`ligacao_poi.aceito_por`.

### Placar com uma linha por distância

O motivo do teto carrega a distância — "endereço exato, mas a 954 m" — porque
na linha do banco ela é a explicação inteira. No `Counter` isso virou **mais de
mil chaves com contagem 1**, escondendo os quatro totais que interessavam.
Agrupado por família em `_familia()`.

### Guarda que casou com o próprio texto — a quinta vez

`if "
import re" not in t` casou com `import regra_vinculo as rv`, e o
`import re` nunca entrou. O arquivo só não quebrou porque `re` já vinha por
outro caminho no teste.

## Corrigido na madrugada de 10/09/2026

### A conexão de preparo ficava aberta a corrida inteira
`avaliar_ligacao.rodar()` abria uma conexão para montar a fila e ler as seções
da CNAE, e **nunca a fechava**. Ela ficava `idle in transaction` pelas horas do
julgamento, segurando `AccessShareLock` em toda tabela que tocou.

**Como apareceu:** um `alter policy` em `cadastro_corsan` entrou na fila atrás
dela e — como a fila de lock do Postgres é FIFO — **todos os 80 trabalhadores
pararam atrás do alter**. A fila congelou em 494 e só voltou quando o processo
inteiro foi derrubado.

**O segundo estrago era silencioso:** transação aberta há horas impede o
autovacuum de limpar as tabelas que ela leu. Isso não aparece em log nenhum;
aparece como lentidão semanas depois.

**Lição de operação:** não rodar DDL em `cadastro_corsan` com a fila andando.

### A política de escrita voltou a ser rápida
`p_corsan_escreve` chamava `core.eh_suporte()` e `core.hierarquia_atual()` sem
subselect — uma chamada de função por linha. A migração 0081 corrigiu só a
leitura. Agora as duas estão com subselect (migração 0090), conferido lendo a
expressão gravada.

### O prompt mandava procurar uma mira que não existe
O texto afirmava que "a primeira foto traz uma mira no centro marcando o alvo",
e `dossie_ligacao.py` **nunca desenhou nenhuma** — a mira é desenhada por
`anotar.marcar_alvo`, que só o `dossie.py` do julgamento por POI chama. O
modelo foi instruído por semanas a procurar um marcador inexistente.

Corrigido junto com o pedido do dono do produto de que as quatro visadas venham
sem marcador: agora o prompt diz que nenhum imóvel vem assinalado, e explica
por quê.

### `aprovado_exato` tinha virado "a IA soube um nome"
A primeira versão do mapeamento binário promovia a `aprovado_exato` sempre que
o campo `estabelecimento` viesse preenchido — e o CNPJ da Receita SEMPRE
publica uma razão social. Medido: 16 de 20 saíram `aprovado_exato`.

Agora quem decide é `presenca_na_foto = "exata"`, que é o que a palavra sempre
significou: a fachada mostra aquele negócio. Depois da correção: 5,9%.

### Três guardas que mentiam
- O guarda de `rowcount` do `revisar_vinculo` acusava "pedi 263.415 e o banco
  marcou 52.415" — `execute_values` com `page_size` menor que o lote só reporta
  a última página. O banco tinha gravado tudo. Agora mede pelo banco.
- O guarda de coordenada do `materializar_base` anunciou "nenhuma linha ganhou
  coordenada" sobre uma carga perfeita: contava **depois** de ligar a RLS, e a
  conexão do `migrator` não carrega claim. Agora conta antes.
- O verificador de política dizia "função nua" com a política já corrigida:
  procurava `(SELECT` e o Postgres renderiza `( SELECT`.

Alarme que dispara à toa é pior que alarme nenhum — ensina a ignorar o próximo.

### O volume de upload perdia a permissão a cada rebuild
`chown` no volume não gruda: quando o volume está vazio, o Docker o semeia com
o conteúdo **e o dono** da pasta da imagem a cada start. Consertado no
`Dockerfile.api`, não por fora.
