# Resgate de POI — o que fazer com o que o pipeline não resolveu

> O pipeline principal minera em massa e acerta a maioria. O que sobra não é
> lixo: é a fila de resgate. Este documento descreve o processo que a atende, no
> fim do processo geral. Escrito em 23/08/2026.

---

## Onde este processo entra

```
mineração em massa  ──▶  POIs resolvidos  ──▶  banco
        │
        └──▶  RESÍDUO: nao_encontrado, encontrado_divergente, sem CNPJ,
              sem telefone, sem horário
                        │
                        └──▶  ESTE PROCESSO, um a um ou em lote de até 20
```

O resíduo existe por motivos que a massa não consegue tratar: o nome da placa
não é o do registro, o Maps guarda outro nome, a loja é MEI sem nome fantasia,
o endereço da Receita é do loteamento e o do Maps é da via de acesso. Cada um
desses casos precisa de **julgamento**, e julgamento não escala em regra fixa —
foi exatamente o que esta sessão descobriu tentando.

---

## A escada, na ordem em que roda

O agente (`agente_local.py`) escolhe as ferramentas, mas a ordem é ensinada no
prompt e há razão medida para cada posição.

| # | Degrau | O que traz | Por que aqui |
|---|---|---|---|
| 1 | `recordar` | o que já se sabe | evita refazer busca decidida antes |
| 2 | `consultar_banco` | POI já ingerido? | instantâneo, e pode encerrar a busca |
| 3 | **`consultar_maps`** | **endereço com número, telefone, horário da semana, nota, categoria, fotos, coordenada, avaliações com data** | **fonte principal** |
| 4 | `consultar_receita` | CNPJ e razão social | cruzamento oficial |
| 5 | `confirmar_cnpj` | veredito sim/não/incerto | candidato ≠ confirmação |
| 6 | `buscar_dados_empresariais` | CNPJ na web, já julgado | resgate quando a Receita não acha pelo nome |
| 7 | `consultar_instagram` | última publicação, seguidores | diz se o ponto está vivo |
| 8 | `buscar_web`, `buscar_lugar` | o que faltou | complemento, não substituto |

### O Maps é o terceiro degrau, e não o último

Ele já esteve em sétimo, com a descrição mandando usá-lo *"depois de
`buscar_web` e `buscar_lugar` não terem resolvido"*. O modelo obedecia — e
entregava endereço e telefone creditados a um guia local, **os mesmos** que o
painel tinha, mas sem horário, sem as seis fotos e sem coordenada.

A ordem antiga era economia de tempo, e deixou de valer: com sessão quente e
piscina, o Maps saiu de 129 s para 19–31 s por consulta.

### Guia local e diretório não substituem o painel

Endereço desatualizado, telefone raramente, e **nunca** horário, foto, nota ou
coordenada. Trocar a fonte boa pela rápida economiza segundo e gasta qualidade,
num sistema onde a decisão de mandar alguém a campo depende desse dado.

---

## As três decisões que a IA toma, e por que não são código

O projeto tem a regra de que erro de IA se corrige por IA, não por código sem
discernimento. Estes são os três pontos onde ela decide:

### 1 · É o mesmo estabelecimento?

`nome_match` compara **letras**. Deu 0,548 para "Bussbier Cerveja Artesanal e
Bebidas" contra "BussBier Chopp Para Festas" — mesmo endereço, mesmo telefone,
mesmo ramo. Qualquer pessoa vê que é a mesma loja.

Então a régua deixou de ser veredito e virou **gatilho**: quando desconfia,
`julgar_identidade.julgar` faz **uma chamada isolada** — sem histórico, sem
outra tarefa junto — mostrando nome, endereço, telefone e categoria.

Verificado que ele **recusa** quando deve, que é o que importa num juiz:

| caso | veredito |
|---|---|
| Atacadão × Atacadão-Teresina Bela Vista | sim |
| Padaria × Localiza Rent a Car | **nao** |
| ESF João XXIII × UBS PSF João XXIII | **nao** ← o falso positivo histórico |

**O limiar de 0,90 do pipeline não mudou, e não deve mudar.** Lá são milhares
de POIs sem revisão humana e uma chamada de IA por linha não paga. A segunda
opinião é do resgate, onde a conversa a custeia.

### 2 · O CNPJ é deste ponto?

`consultar_receita` acha **candidatos**; `confirmar_cnpj` diz se o candidato é o
ponto. A negativa é resultado: saber que aquele CNPJ não é dali poupa a visita.

O grupo Vancosty tem seis CNPJs em Canoas e região. Só um está em Guajuviras — o
bairro que o Maps aponta:

```
...0005-94  GUAJUVIRAS          sim
...0001-60  Águas Claras        nao
...0002-41  Pq. Espírito Santo  nao
...0003-22  São Vicente         nao
...0004-03  Morada do Vale      nao
...0006-75  Jardim Betânia      nao
```

**Mesma região basta.** Bairro igual (ou CEP vizinho) + ramo compatível
confirma, mesmo com rua e número diferentes: o cadastro do governo registra por
loteamento ("Quadra EE Dois, 01") onde o Maps mostra a via de acesso ("Av.
Dezessete de Abril"). Exigir a rua idêntica reprovava o CNPJ certo.

### 3 · O que o código faz, e a IA não

**Comparar duas cadeias de texto.** Ensinar a regra do bairro ao modelo fez ele
aprovar CNPJs de Pq. Espírito Santo e São Vicente dizendo *"mesmo bairro"* —
comparando com Guajuviras. Não errou o raciocínio: errou a **leitura**.

Hoje a conferência é calculada e entregue como fato:

```
JÁ CONFERIDO PARA VOCÊ (não recalcule, use)
  MESMA REGIÃO: o bairro 'GUAJUVIRAS' aparece no endereço do Maps | CEP VIZINHO
```

O julgamento continua dele — pesar bairro contra ramo, fantasia e situação é
discernimento. Comparar string não é.

---

## O que a resposta traz

Cada campo com a **ferramenta** que o produziu. Fonte inventada já apareceu duas
vezes — "Fonte: Econodata" e "Fonte: Diário Cidade" — sempre quando um degrau
falhava calado e o número vinha de trecho de página.

- endereço com número, telefone, horário de cada dia
- **avaliações com nota, autor e data** — a data é o que as faz valerem: no PKC
  Fusion, 1,0 há três semanas contra 5,0 há três anos desenha um negócio em
  queda que a média 4,1 esconderia
- **rede social com a data da última publicação** — perfil parado há 566 dias e
  ponto provavelmente fechado. O handle sai do `href` do painel, não é
  adivinhado do nome
- até 6 fotos da fachada
- CNPJ **confirmado ou negado**, com o motivo

### Empresa baixada aparece

`situacao_cadastral` já filtrou só ativas, e devolvia zero para os dois "Los
Chiapas" de Canoas — ambos baixados — empurrando o modelo a inventar o CNPJ.
Num radar comercial, **CNPJ baixado no endereço de uma loja aberta é achado**:
troca de titularidade, MEI que virou LTDA, mesmo dono por outro CNPJ.

---

## Capacidade medida

`prova_carga_chat.py N` roda N buscas em paralelo, uma conversa para cada, e
mede taxa por campo e tempo.

| | 5 paralelos | 10 paralelos |
|---|---|---|
| tempo | 413 s | 488 s |
| ganho sobre a fila | 3,1× | 5,6× |
| endereço | 80% | 50% |
| horário | 80% | **10%** |
| avaliações | 100% | 70% |
| CNPJ | 80% | 60% |

**Cinco é o teto útil no notebook.** A 10 nada trava e nenhum erro aparece no
log — o Maps simplesmente passa a não encontrar o estabelecimento, a escada cai
para a busca web, e o horário desaba. Quebra em silêncio, que é pior que quebrar
com erro.

No i9 (16 CPUs, 51 GB livres) o teto é outro e ainda não foi medido.

### Conversa longa contamina

Numa de 131 mensagens o modelo copiou o formato das próprias respostas antigas —
**com as omissões delas** — e ignorou campos que as ferramentas passaram a
devolver. Por isso a prova de carga usa **uma conversa por busca**, e o chat tem
botão de apagar.

---

## Como rodar

```bash
python agente_local.py "ache endereco, telefone, horario, avaliacoes com data, redes sociais com data do ultimo post e o CNPJ confirmado do <NOME> em <CIDADE> <UF>"
```

Em lote, até 20 por pedido, com `buscar_varios`. Pela interface, `chat.html`.

`MAPS_SESSOES` define a piscina de sessões do Maps **e** o portão do lote. No
notebook, 3 a 5. Sem a variável o padrão é 1, e o lote roda o Maps em fila.

---

## Armadilhas conhecidas

| Sintoma | Causa | Onde |
|---|---|---|
| `Page.goto: Timeout 35000ms` no Maps | credencial de proxy entregue ao Chromium | `relay_proxy.py` — o relay carrega a credencial fora do navegador |
| relays se multiplicando, RAM caindo | `terminate()` no Windows pede, não obriga | `PiscinaRelay._matar` insiste com `kill()` |
| cruzamento por endereço dá zero | `"Av."` com ponto não casava com `"AV"` | `agente_local.consultar_receita` |
| Receita não acha pelo nome | a placa diz "Espetão Vancosty", o registro diz "Vancosty Comércio e Distribuição" | busca alarga para a palavra distintiva, e avisa que alargou |
| resposta vazia e instantânea | turnos de assistente vazios no histórico ensinam a responder vazio | `chat_api._historico` os descarta |

**Nunca preencher vazio com frase própria.** Pôr `(chamei a ferramenta X)` no
lugar fez o modelo copiar isso *como resposta ao usuário*, literalmente.

---

## Cobertura

`tests/test_busca_ferramentas.py` — determinístico, sem navegador e sem modelo.
Cada teste cita o caso real que o originou.
