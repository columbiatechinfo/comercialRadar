---
name: leitura-fachada-cadastral
description: Lê imagens de fachada (campo, Street View, drone), TRIA se é imóvel e se há atividade econômica — e de quem ela é (alvo, vizinho, ambulante) — e RECONSTROI a estrutura de unidades de prédio, vila e condomínio (torres, pavimentos, prumadas, interfone, caixas de correio) sem fundir unidades físicas, UCs elétricas, economias de água e hidrômetros. Entrega sinais auditáveis e atributos cartografáveis para as teses do PGV Cadastral A2L — categoria x uso real, economias ocultas, esgoto sem cobrança e área divergente. Acione SEMPRE que houver foto, print, imagem, Street View ou drone de imóvel, fachada ou prédio e o pedido envolver anotar o que aparece na imagem, dizer se a foto é de imóvel, identificar comércio ou negócio dentro de casa, contar economias ou unidades de um edifício, verticalização, condomínio horizontal, classificar uso ou padrão construtivo, achar hidrômetro ou medidor, checar ocupação, ligação fantasma, conferir número da casa ou recadastramento por imagem. CORSAN/Aegea, COPEL, SABESP, Enel.
---

# Leitura de fachada cadastral — do pixel ao sinal, do sinal ao achado

Esta skill é um **sensor do motor cadastral**, não o motor inteiro.

Ela transforma imagem em evidência estruturada para responder quatro perguntas de negócio:

1. **Categoria x uso real** — o cadastro diz residencial, mas há comércio/serviço/misto no imóvel?
2. **Economias ocultas** — a base registra uma economia, mas o imóvel apresenta sinais de várias unidades independentes?
3. **Esgoto sem cobrança** — há sinais compatíveis com atendimento/conexão que mereçam cruzamento com rede e faturamento?
4. **Área/ocupação divergente** — a forma física do imóvel, quando imagem aérea/footprint estiver disponível, diverge do cadastro?

O endereço/identidade do imóvel é uma quinta camada transversal: **antes de dizer o que existe, prove em qual imóvel isso existe**.

## Antes de começar

Se o pedido não trouxe os parâmetros, resolva em uma pergunta curta (diretriz A2L de alinhamento). Três coisas
mudam o resultado e não se adivinham:

- **Fonte e data de cada imagem** — governam o teto de confiança de metade dos campos e toda a leitura temporal
  do comércio. Sem data em Street View/Mapillary, assuma 36 meses de idade e **declare a suposição**.
- **Vínculo** — há matrícula/`imovel_id` associado, e ele veio por ID da operação ou por endereço/GPS? A origem do
  vínculo decide como tratar divergência de número (gate `R-END-06`): vínculo por endereço com número divergente
  é ambiguidade; vínculo por ID direto é suspeita de cadastro desatualizado. Sem vínculo, a anotação é autônoma e
  nenhuma oportunidade passa de `sinal_imagem`.
- **Teses de interesse** — as quatro do PGV são o default. Se a campanha só quer uso real e economias ocultas, os
  demais blocos saem `nao_observavel` sem esforço de leitura, e a auditoria registra a decisão.

Com "vai direto" ou parâmetros completos, pule o alinhamento.

## Leia primeiro

Progressive disclosure — não tente carregar tudo de memória, os arquivos existem para serem consultados no ponto
de uso:

- `references/taxonomia.md` — vocabulário fechado de cada campo (§1-9), comércio temporal (§10) e oportunidades
  cadastrais (§11). Consulte **antes** de escrever qualquer valor; valor fora da lista quebra o cruzamento com a base.
- `references/derivacao.md` — regras nomeadas (`R-ECO-*`, `R-OCU-*`, `R-USO-*`, `R-AGU-*`, `R-ESG-*`, `R-PAD-*`,
  `R-END-*`, `R-UC-*`, `R-SIN-*`, `R-PGV-*`) e o cálculo de confiança. Todo campo inferido cita a sua regra.
- `assets/observabilidade.json` — teto de confiança por atributo × fonte, decaimento temporal dos campos voláteis,
  penalidades de enquadramento e qualidade.
- `assets/schema_fachada.json` e `assets/vocabulario.json` — o contrato de saída e as listas fechadas.

## Regra-mãe: sinal não é achado

A imagem pode produzir um sinal excelente, mas **uma fonte isolada não vira achado cadastral definitivo**.

- `nivel_evidencia = sinal_imagem`: a imagem sustenta o fato, mas ainda falta convergência externa.
- `nivel_evidencia = achado_convergente`: pelo menos duas fontes independentes apontam o mesmo fato.
- `nivel_evidencia = contradicao`: fontes independentes discordam; o caso deve ser bloqueado/revisado.

Dois objetos na mesma foto **não são duas fontes**. Ex.: três medidores + três portas na mesma fachada aumentam a força do sinal visual, mas continuam sendo uma única fonte independente: `imagem_fachada`.

Cadastro da companhia, BDGD, CNEFE, CNPJ, rede de esgoto, histórico operacional e vistoria são fontes independentes quando realmente possuem origem própria.

---

# O que precisa sair de cada imagem

## 1. Identidade e endereço

Procure e transcreva, sem completar dígito borrado:

- número fixado na fachada;
- número na porta;
- número no portão;
- número na caixa de correio;
- placa de endereço;
- número/endereço impresso em placa ou letreiro comercial;
- placa de logradouro;
- complementos como `A`, `B`, `fundos`, `casa 1`, `casa 2`, `loja`, `sala`, `apto`;
- múltiplas numerações na mesma testada.

A **caixa de correio tem dupla utilidade**:

1. pode revelar o número/endereço do imóvel;
2. quando existem várias caixas individualizadas, especialmente com números/complementos distintos, é indício de múltiplas unidades.

Não transcreva nome de morador, destinatário de correspondência ou qualquer identificação pessoal. Leia apenas o que descreve o **imóvel/unidade**.

### Não confundir com número de endereço

Nunca trate automaticamente como número da casa:

- serial de medidor de energia;
- matrícula/serial de hidrômetro;
- telefone em letreiro;
- CNPJ;
- preço;
- horário de funcionamento;
- número de placa veicular;
- número de poste/transformador.

Registre a **origem física** do número lido em `enderecamento.origem_numero_consolidado`.

## 2. Comércio — o que é, o que funciona e quando era verdadeiro

Para qualquer fachada com sinal comercial, responda separadamente:

- uso do imóvel: residencial, comercial, serviços, industrial ou misto;
- nome visível do estabelecimento;
- atividade literal do letreiro;
- segmento inferido;
- descrição funcional: **o que aparentemente funciona naquele local**;
- situação do estabelecimento **na data da imagem**;
- sinais que sustentam a atividade;
- data/ano da foto e validade temporal.

Exemplo correto:

> A imagem de agosto/2024 mostra “Auto Center Avenida”, com pneus, alinhamento e troca de óleo anunciados, porta aberta e produtos expostos. Na data da captura, o endereço aparentava operar como estabelecimento de serviços automotivos. Isso não prova que continua ativo hoje.

`fechado_no_momento` não é `desativado_aparente`. Porta de aço baixada, sozinha, só diz que estava fechada naquele instante.

Quando o cadastro informa residencial e a imagem mostra comércio/misto, a combinação **imagem + cadastro da companhia** pode formar um `achado_convergente` de divergência de uso, desde que o vínculo do imóvel esteja resolvido.

## 2.5 Estrutura do imóvel — para prédio, reconstrua o empreendimento

Em casa, a fachada é o imóvel. Em prédio, vila e condomínio, a fachada é a **capa** de uma estrutura de unidades
que precisa ser reconstruída. Preencha `estrutura_imovel` sempre (obrigatório em `schema_versao 1.5.0`):

- **tipologia** entre 14 valores — de `casa_isolada` a `condominio_vertical_multitorre`, passando por
  `vila_corredor`, `condominio_horizontal`, `sobrado_subdividido`, `galeria_comercial` e
  `lote_multiplas_edificacoes`. A tipologia governa qual evidência vale;
- **dimensionamento**: torres, pavimentos, pavimentos residenciais e comerciais, prumadas aparentes,
  unidades por pavimento;
- **identificadores** lidos em interfone, caixa de correio ou placa (`101;102;201;202`) — o número da unidade
  entra, o nome do morador nunca;
- **as quatro medidas separadas**: `unidades_fisicas_estimadas`, `ucs_energia_visiveis`, `hidrometros_visiveis`
  e as economias cadastradas do vínculo.

A regra-mãe deste bloco (`R-EST-01`): **nunca funda as quatro medidas**. Nove UCs elétricas não são nove
economias de água. A divergência entre elas é o produto — `9 unidades físicas × 1 economia cadastrada` é o
achado, e ele vai para vistoria, não para alteração automática.

Contagem no prumo (`R-EST-02`): `torres × pavimentos_residenciais × unidades_por_pavimento + comerciais`. O
validador confere a aritmética com folga de 25% e pede explicação quando não fecha — pilotis e cobertura duplex
são as causas legítimas mais comuns. Toda contagem acima de 1 exige `metodo_estimativa` declarado (`C-21`);
tipologia vertical exige dimensionamento ou identificadores (`C-22`); unidades acima das economias cadastradas
exigem oportunidade correspondente (`C-23`).

## 3. Economias ocultas e unidades independentes

Conte de forma independente:

- medidores de energia completos;
- hidrômetros/baterias;
- portas de acesso independentes;
- campainhas/interfones;
- caixas de correio;
- números/complementos distintos;
- unidades aparentes por pavimento;
- estabelecimentos distintos na mesma testada.

### Múltiplas UCs elétricas

Dois ou mais medidores elétricos individualizados no mesmo imóvel geram `MULTIPLAS_UCS_MESMO_ENDERECO`.

Mas nunca faça:

`3 UCs elétricas = 3 economias de água`.

UC elétrica é evidência forte de independência de unidades; economia de água é outra decisão cadastral.

Se a base registra uma economia e a fachada mostra múltiplas UCs, a combinação **imagem + cadastro da companhia** gera `DIVERGENCIA_UC_ECONOMIAS` como achado de fila, não como alteração automática.

### Numeração como sinal de subdivisão

Os padrões abaixo fortalecem a hipótese de economias ocultas:

- `125`, `125-A`, `125-B`;
- `casa 1`, `casa 2`, `fundos`;
- três caixas de correio individualizadas com três identificadores de unidade;
- três botões de interfone + três portas + três numerações;
- loja no térreo + acesso residencial separado.

Uma caixa de correio única com número `125` é principalmente **evidência de endereço**. Várias caixas identificadas viram também **evidência de unidades**.

## 4. Água e esgoto

A imagem pode encontrar:

- hidrômetro/abrigo e sua acessibilidade;
- múltiplos hidrômetros;
- caixa de inspeção aparente;
- PV/rede aparente na via;
- sinais de fossa;
- lançamento aparente em sarjeta.

Mas proximidade da rede **não prova conexão**. Para `ESGOTO_SEM_COBRANCA_POTENCIAL`, a imagem deve ser cruzada com rede/cadastro/faturamento e a saída continua sujeita à confirmação de campo quando a conexão não puder ser comprovada tecnicamente.

## 5. Padrão, lote e área

Fachada serve bem para porte, pavimentos, acabamento, conservação, anexos visíveis e uso físico.

Área construída precisa de fonte adequada. Quando houver imagem aérea/drone/footprint, registre:

- `area_footprint_estimada_m2`;
- `area_construida_estimativa_m2`;
- pavimentos;
- anexos e ocupação do lote.

Foto frontal isolada não deve inventar área em m².

---

# Protocolo em 6 passes

## Passe 0 — Triagem, tempo, qualidade e vínculo

### Triagem: isto é um imóvel? há atividade econômica? de quem?

Duas perguntas baratas, feitas antes de qualquer leitura fina, que evitam os dois erros mais caros da campanha:
**anotar uma foto que não é de imóvel** (custo puro, lixo na base) e **importar o comércio do vizinho para o
cadastro do alvo** (achado falso, com consequência tarifária e reclamação do cliente).

Preencha o bloco `triagem` sempre — ele é obrigatório a partir de `schema_versao 1.4.0`:

| Campo | O que responde |
|---|---|
| `conteudo_imagem` | fachada, imóvel parcial, terreno, obra, interior, **detalhe de medição**, logradouro sem imóvel, rede/poste, documento ou tela, mapa/croqui, veículo/pessoa, área rural, ininteligível |
| `e_imovel` | a imagem retrata um imóvel identificável (edificado ou lote)? |
| `apto_para_cadastro` | a imagem sustenta anotação cadastral de fachada? |
| `uso_parcial_permitido` | se não é apta: para que ainda serve (`medicao`, `interior`, `entorno`, `nenhum`) |
| `atividade_economica_aparente` | `nenhuma_aparente`, `sinal_fraco`, `sinal_forte`, `indeterminado` |
| `sinais_atividade_economica` | letreiro, toldo, vitrine, porta de aço, mercadoria exposta, veículo de serviço, placa de profissional liberal, cardápio, doca, equipamento produtivo, fachada de franquia, horário de funcionamento… |
| `atividade_no_alvo` | **`no_imovel_alvo`, `em_vizinho`, `ambulante_via_publica`, `indeterminado`** |
| `natureza_atividade` | comércio, serviço, indústria, agropecuária, institucional, religiosa, educacional, saúde, hospedagem, logística, misto |
| `formalidade_aparente` | `estabelecimento_formal_aparente`, **`atividade_domiciliar`**, `ambulante_informal` |

**`e_imovel = false` encerra a anotação.** Não preencha atributos, não gere oportunidade: emita
`IMAGEM_FORA_DE_ESCOPO` com o motivo e devolva a imagem para recoleta. Uma foto de tela de sistema anotada como
se fosse fachada é pior que uma foto faltando — ela ocupa a matrícula com dado inventado e ninguém volta lá.

**`atividade_no_alvo` é o gate anti-contaminação e talvez o campo mais importante do bloco.** Em rua de comércio
denso, a foto do imóvel-alvo quase sempre contém o letreiro do vizinho. Marcar `em_vizinho` custa um segundo e
impede um achado falso de "categoria divergente" que iria direto para uma fila de revisão tarifária. O validador
recusa uso econômico no alvo quando a atividade foi atribuída a vizinho ou a ambulante (`C-19`).

**`atividade_domiciliar` é o achado de maior retorno silencioso**: salão na sala, oficina no fundo do quintal,
mercadinho na garagem, escritório com placa de profissional liberal. A edificação continua residencial e o
cadastro também — mas há atividade econômica ali. Não é conclusão de categoria; é a fila
`ATIVIDADE_DOMICILIAR_POTENCIAL` para cruzar com CNPJ no endereço e categoria cadastral.

Sinal de atividade sem `sinais_atividade_economica` listado é opinião, não leitura (`C-18`). Sinal forte no
alvo tem que aparecer no `uso_predominante` ou virar oportunidade de categoria (`C-17`) — detectar e não
registrar é pior que não detectar.

### Tempo, qualidade e vínculo

Registre:

- fonte;
- `data_captura` e `ano_captura`;
- origem da data;
- coordenada/azimute quando disponíveis;
- matrícula/`imovel_id` quando houver;
- ROI do imóvel-alvo quando a imagem contém vizinhos.

Diferencie:

- **confiança de leitura**: quão bem o objeto foi visto;
- **confiança de vínculo**: quão certo é que a imagem pertence à matrícula correta.

Uma leitura perfeita da fachada errada é um erro cadastral perfeito.

### Gate de inaptidão

Antes de olhar o imóvel, olhe a imagem. Declare `apta_para_leitura = false`, emita `IMAGEM_INAPTA` e **pare**
quando a imagem não sustenta nenhum campo com confiança útil: fachada quase toda obstruída, borrada a ponto de
não distinguir esquadria, noturna sem iluminação, ou simplesmente não é uma fachada. Entregar trinta campos
`null` acompanhados de prosa explicativa custa mais e informa menos do que um registro inapto e honesto — e o
registro inapto é o que dispara nova coleta.

Enquadramento e qualidade viram **penalidade multiplicativa** no teto de confiança de todo campo (tabelas em
`assets/observabilidade.json`); não é preciso descontar na mão, mas é preciso preencher os campos de qualidade
com honestidade, porque é deles que o desconto sai.

## Passe 1 — Varredura 3×3

Leia `SE SC SD / CE CC CD / IE IC ID` e inventarie objetos antes de classificar.

Procure especialmente números, caixas de correio, portas, interfones, letreiros, medidores, hidrômetros, caixas de esgoto e elementos que identifiquem a testada.

## Passe 2 — Transcrição literal e endereçamento

Transcreva número/endereço e textos comerciais antes de interpretar.

Para o número do imóvel:

1. leia todas as ocorrências possíveis;
2. associe cada ocorrência ao objeto onde aparece;
3. descarte números de equipamento/telefone/placa;
4. consolide apenas quando as ocorrências forem compatíveis;
5. compare com `vinculo.numero_base`;
6. se divergir, **não force o casamento do imóvel**.

Sequência dos vizinhos (`145`, alvo, `149`) pode ser usada como inferência fraca de `147`, nunca como número observado.

## Passe 3 — Uso e atividade comercial temporal

Determine o que é e o que funciona ali, sempre preso à data da foto.

## Passe 4 — Independência de unidades

Conte UCs, hidrômetros, portas, caixas de correio, interfones e numerações separadamente. Depois teste concordância.

## Passe 5 — Água, esgoto, área e oportunidades

Extraia os demais atributos e classifique cada oportunidade como `sinal_imagem`, `achado_convergente` ou `contradicao`.

## Passe 6 — Reconstrução e gates

Antes da entrega, confirme:

- a imagem é mesmo de imóvel, e a triagem registrou isso?
- a atividade econômica lida pertence ao alvo, ou é do vizinho/ambulante?
- o número/endereço visual corresponde ao imóvel-alvo?
- algum objeto pertence ao vizinho?
- comércio está datado?
- múltiplas UCs foram tratadas como oportunidade, não como economias automáticas?
- existe um único imóvel contado duas vezes?
- `achado_convergente` realmente tem duas fontes independentes?
- divergência de número bloqueou o vínculo quando deveria?

---

# Gates de identidade

## Número visual confere

Se o número consolidado coincide com o cadastro, ele aumenta a confiança do vínculo, principalmente quando também concorda com via, face, GPS e sequência predial.

## Número visual diverge

Se o número visual diverge de `numero_base`:

- `vinculo.numero_confere = false`;
- emitir alerta `NUMERO_DIVERGE_CADASTRO`/`DIVERGENCIA_NUMERO_ENDERECO`;
- gerar oportunidade `DIVERGENCIA_NUMERO_ENDERECO`;
- se o vínculo foi feito por endereço/GPS+numero, rebaixar confiança e marcar ambiguidade;
- **não unir registros só porque estão próximos espacialmente**.

Se a matrícula veio por ID direto da própria operação, a divergência pode revelar cadastro de número desatualizado, mas ainda deve ser tratada como contradição até confirmação.

---

# Gates de negócio

- **Categoria ≠ uso real:** imagem comercial/mista + cadastro residencial -> fila de revisão de categoria.
- **Economias ocultas:** sinais de múltiplas unidades + base com poucas economias -> fila de confirmação cadastral.
- **Esgoto sem cobrança:** sinais + rede/cadastro/faturamento convergentes -> fila própria; proximidade de rede sozinha não basta.
- **Área divergente:** footprint/área estimada em fonte aérea + área cadastral divergente -> fila; fachada frontal sozinha não basta.

Toda fila deve carregar evidência, confiança, data de referência, fontes independentes e ação sugerida.

---

# Regras duras

- Dúvida -> `null` + motivo.
- `observado` exige pixel verificável.
- `inferido` exige regra nomeada.
- Campo volátil descreve **a data da imagem**.
- Comércio sem data confiável não é estado atual.
- Número borrado não é completado por conveniência.
- Número em caixa de correio pode ser endereço; nome de destinatário não é transcrito.
- Múltiplas caixas de correio sem identificação contam como indício fraco de unidades; com identificadores distintos, o sinal fica mais forte.
- Múltiplas UCs não viram automaticamente múltiplas economias de água.
- Fonte única não vira `achado_convergente`.
- Número divergente não une registros.
- Proximidade da rede de esgoto não prova conexão.
- Suspeita não vira acusação de fraude.
- Imagem que não é de imóvel não vira anotação: `e_imovel=false` encerra, com motivo e recoleta.
- Atividade econômica declarada exige sinal listado e dono identificado (alvo, vizinho ou ambulante).
- Comércio de vizinho e ambulante na calçada não tornam o imóvel-alvo comercial.
- Atividade dentro de residência é fila de revisão de categoria, nunca reclassificação automática.
- Não inferir renda, classe social ou perfil do morador.

---

# Atributos cartografáveis

Preencha `mapeamento` com o que a imagem sustenta — número observado e base, `status_endereco`, uso base e
observado, `mudanca_uso` e `transicao_uso`, economias base e estimadas, UCs, `gap_uc_economias`, impacto,
probabilidade e `valor_esperado_anual`, idade e `vigencia_evidencia`.

Deixe **nulos** `face_id`, `quadra_id`, `logradouro_id` e `cluster_oportunidade`: são de origem territorial e
pertencem ao módulo `pgv-mapeamento-cadastral`, que agrega milhares de imóveis em face → quadra → logradouro →
bairro → campanha. A face de quadra de um imóvel não é observável na foto dele — preenchê-la aqui é erro
(`C-27`). Esta skill é o sensor; o mapa é do outro módulo.

Duas contas são conferidas pelo validador: `gap_uc_economias = ucs_energia - economias_base` e
`valor_esperado_anual = probabilidade_confirmacao × impacto_anual_estimado` (`C-26`). Mapear quantidade de
suspeitas ordena a fila errada; mapear valor esperado ordena a fila certa.

---

# LGPD

Fachada é espaço público, mas a foto quase sempre carrega dado pessoal junto. A regra prática separa **dado do
imóvel** (que é o produto) de **dado da pessoa** (que é passivo):

- **Não transcreva** nome de morador ou destinatário de correspondência, telefone particular, placa de veículo
  nem texto manuscrito de caráter pessoal. Nome fantasia e telefone comercial impressos em letreiro são dados de
  estabelecimento e podem ser transcritos.
- Pessoa identificável ou placa legível → `lgpd.pessoas_visiveis` / `lgpd.placas_veiculo_visiveis = true`, alerta
  `DADO_PESSOAL_PRESENTE` e `acao = blur_recomendado`. O rosto é registrado como presente, nunca descrito.
- `lgpd.dado_pessoal_transcrito` sai sempre `false`; o validador reprova a anotação se vier `true`.

A caixa de correio é o ponto de atrito mais comum: o **número** da unidade é evidência de endereço e entra; o
**nome** ao lado dele não entra em campo nenhum, nem como evidência.

---

# Gate de entrega

```bash
python3 scripts/validar_anotacao.py saida/anotacoes \
  --schema assets/schema_fachada.json \
  --observabilidade assets/observabilidade.json \
  --vocabulario assets/vocabulario.json \
  --relatorio saida/validacao.json
```

Depois:

```bash
python3 scripts/consolidar.py saida/anotacoes --out saida/fachadas --formato csv
```

E, quando houver revisão:

```bash
python3 scripts/painel_fachadas.py saida/fachadas.csv --imagens fotos --out saida/painel.html
```

Validação com `status: "reprovado"` significa que a anotação **não sai** — corrija os campos apontados e revalide.
Aceitar uma anotação reprovada por pressa é exatamente como um dado ruim entra na base e sobrevive por anos.

## O que sai no consolidado

Uma linha por imagem, com quatro colunas irmãs por atributo — `agua_hidrometro_presente`, `_conf`, `_juizo`,
`_regra`. Esse layout existe para o filtro que todo consumidor a jusante precisa fazer sem abrir JSON:

```sql
WHERE agua_hidrometro_presente_conf >= 0.70
  AND agua_hidrometro_presente_juizo <> 'inferido'
```

O cabeçalho vem do schema, não das chaves encontradas: o CSV de um lote sem nenhum imóvel com piscina tem as
mesmas colunas do lote que tem. Isso é o que permite empilhar campanhas de meses diferentes sem retrabalho.

---

# Lote de imagens e conciliação por imóvel

Em campanha, mantenha o protocolo por imagem — o passe que se corta primeiro para ganhar velocidade (o de
reconstrução) é justamente o que segura a taxa de erro.

- **Uma anotação por imagem, sem contaminação entre vizinhas.** Ver a casa anterior enviesa a próxima, e o viés
  se propaga por toda a rua. Quando a imagem contém vizinhos, delimite o `alvo_fachada` antes de ler.
- **Várias imagens do mesmo imóvel** → anote cada uma isoladamente e concilie depois:

```bash
python3 scripts/conciliar_multifoto.py saida/anotacoes --chave matricula \
    --out saida/conciliado --relatorio saida/conciliacao.json
```

  A conciliação escolhe, campo a campo, a imagem cujo **teto efetivo de fonte** é maior — foto de campo vence
  Street View em medição; drone vence campo em pavimentos, cobertura e footprint. Duas fotos de fachada continuam
  sendo **uma** fonte independente (`imagem_fachada`); fachada + aérea são duas. O script marca a elegibilidade,
  mas não promove `sinal_imagem` a `achado_convergente` sozinho — promoção é decisão do motor cadastral.
- **Valores diferentes entre imagens de datas diferentes não são erro**, são série temporal: comércio que fechou,
  obra que terminou, medidor que foi retirado. O relatório separa `divergencia_temporal` de `contradicao`.
- **Feche o funil ao final**: imagens recebidas = anotadas + inaptas (por motivo) + ilegíveis. Sem isso não se
  sabe qual foi a cobertura real da campanha, e cobertura desconhecida vira meta não cumprida na medição.

---

# Cruzamento com o restante da linha A2L

Esta skill é o sensor; quem decide são os motores a jusante. Cada saída tem um destino:

| Saída | Consumidor |
|---|---|
| `SUSPEITA_IMOVEL_INEXISTENTE`, `SUSPEITA_VACANCIA` | `ligacao-fantasma` — evidência visual no scoring de matrícula |
| `IMPEDIMENTO_LEITURA`, `MEDICAO_NAO_LOCALIZADA`, `acessibilidade_medicao` | `fora-de-rota-pdca` — ocorrência 6/10 prevista antes do ciclo |
| `DIVERGENCIA_NUMERO_ENDERECO`, `numero_endereco_consolidado` | `construcao-vias-publicas`, `ferramenta-logradouro-padrao` — régua de numeração e `imovel_id` |
| `ECONOMIAS_OCULTAS_POTENCIAL`, `MULTIPLAS_UCS_MESMO_ENDERECO` | `tratamento-coletivas-cadastro`, `tratamento-cadastro-comercial` |
| `CATEGORIA_DIVERGENTE`, uso `misto_res_com`, comércio datado | revisão de categoria tarifária e recadastramento comercial |
| `ESGOTO_SEM_COBRANCA_POTENCIAL` | cruzamento com cadastro de rede e faturamento |
| `AREA_DIVERGENTE_POTENCIAL` | revisão de área construída / PGV |
| `risco_impedimento_leitura`, tempo de acesso | dimensionamento de rota e produtividade de campo |

## Priorização da fila

Ordene por **impacto financeiro esperado**, não por confiança. Uma divergência de economias em imóvel comercial
de grande porte vale mais que dez confirmações de casa térrea. Confiança baixa define **quem revisa**; impacto
define **quem vai primeiro**. O painel já ordena por risco de decisão errada — a fila de campo é outro
ordenamento, e os dois convivem.

---

# Autoteste antes de entregar

1. `triagem` preenchida: conteúdo da imagem, `e_imovel`, aptidão, atividade econômica e a quem ela pertence.
2. Todo campo preenchido tem `juizo`, `confianca` e evidência verificável por outra pessoa na mesma imagem.
3. Nenhuma confiança acima do teto da fonte, já com decaimento temporal e penalidades de qualidade.
4. Todo campo `inferido` cita a regra nomeada.
5. `varredura[]` preenchida e coerente com os campos `observado`.
6. Vocabulário fechado respeitado (`outro:*` contado, não escondido).
7. Alvos duros transcritos literalmente ou `null` com motivo — nada normalizado, nada completado.
8. Número consolidado tem origem física declarada e foi confrontado com `vinculo.numero_base`.
9. Comércio datado; campo volátil descreve a data da imagem, não hoje.
10. Economias com todas as evidências contadas, teste de concordância aplicado e UC elétrica não convertida em
   economia de água.
11. Nenhuma oportunidade em `achado_convergente` com fonte única.
12. LGPD: nenhum dado pessoal transcrito; alerta emitido quando aplicável.
13. `scripts/validar_anotacao.py` retornando `aprovado`.

# Critério de sucesso

Uma boa saída não diz simplesmente “há comércio” ou “há três medidores”. Ela diz algo como:

> A imagem de agosto/2024 mostra número 350 no portão e na caixa de correio, compatível com o número cadastrado. No mesmo imóvel aparece um Auto Center em atividade aparente naquela data, com pneus, alinhamento e troca de óleo anunciados. Há três medidores elétricos individualizados e três acessos. A base de água registra uma economia. O uso e a quantidade de unidades apresentam sinais de divergência cadastral; a combinação com a base gera fila de revisão, não alteração automática.

Essa é a diferença entre **ler uma foto** e **produzir evidência cadastral útil**.
