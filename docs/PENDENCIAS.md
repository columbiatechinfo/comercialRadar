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

### 5. Os 22.821 POIs órfãos
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
