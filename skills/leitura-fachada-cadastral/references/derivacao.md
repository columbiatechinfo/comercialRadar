# Regras de derivação — como sair do observado para o inferido sem inventar

Todo campo com `juizo: inferido` carrega o identificador da regra usada no atributo `regra`. Isso é o que permite,
meses depois, auditar por que o sistema disse que aquele imóvel tinha 4 economias — e corrigir a regra, não o caso.
Regra sem identificador é opinião; com identificador, é processo.

Sumário: [Economias](#economias) · [Ocupação](#ocupacao) · [Uso](#uso) · [Medição de água](#medicao-de-agua) ·
[Esgoto](#esgoto) · [Padrão construtivo](#padrao-construtivo) · [Confiança](#calculo-de-confianca)

---

## Economias

O número de economias é o campo de maior impacto financeiro da anotação inteira — ele define quantas vezes a
tarifa mínima incide. Errar para menos é receita perdida; errar para mais é reclamação, ressarcimento e risco
regulatório. Por isso a derivação é por **hierarquia de evidência com teste de concordância**, não por chute único.

### Hierarquia de força

| Ordem | Evidência | Regra | Força | Por que |
|---|---|---|---|---|
| 2 | `energia.medidores_energia_qtd` | `R-ECO-01` | forte-indireta | Múltiplos medidores individualizados são evidência forte de unidades consumidoras independentes, mas não provam por si sós a quantidade tarifária de economias de água. Use como evidência de independência e oportunidade cadastral; para água, exija convergência com hidrômetros/acessos/cadastro. |
| 1 | `agua.hidrometros_visiveis_qtd` (bateria) | `R-ECO-02` | forte-direta | Bateria de hidrômetros já reflete a decisão de individualização da própria companhia. |
| 3 | `acessos.portas_acesso_independentes` | `R-ECO-03` | média | Porta independente na testada é a definição prática de economia autônoma; mas porta de garagem, porta de serviço e porta de depósito inflam a contagem. |
| 4 | `acessos.campainhas_interfones` | `R-ECO-04` | média | Botoeira com N botões é forte indício de N unidades; campainha antiga com botão único não nega nada. |
| 5 | `acessos.caixas_correio` | `R-ECO-05` | média | Bom em prédio, ruído em casa (caixa dupla por hábito). |
| 6 | `acessos.numeracoes_distintas` | `R-ECO-06` | fraca | Numeração `125` e `125-A` indica desdobro; mas número antigo não removido produz falso positivo. |
| 7 | `pavimentos × unidades por pavimento` | `R-ECO-07` | fraca | Só para edifício vertical em que se contam sacadas/janelas alinhadas. Estimativa, nunca contagem. |
| 8 | Nenhuma das anteriores | `R-ECO-08` | — | `estimativa = 1`, `metodo = unitario_default`, confiança ≤ 0,55. Casa térrea comum cai aqui, e está correto que caia. |

### Procedimento

1. Conte cada evidência disponível de forma independente, sem olhar para as outras. Ancorar a segunda contagem
   na primeira destrói justamente a informação que o teste de concordância precisa.
2. Registre todas em `economias.evidencias_contadas`.
3. Tome como `estimativa` o valor da evidência de maior força **compatível com economias de água**. `energia.medidores_energia_qtd` sozinho não autoriza `estimativa > 1`: ele prova UCs elétricas, gera `MULTIPLAS_UCS_MESMO_ENDERECO` e pede convergência com hidrômetros, acessos, numerações ou cadastro antes de alterar a estimativa de economias.
4. **Teste de concordância** (`R-ECO-09`): se duas evidências de força ≥ média discordam em mais de 1 unidade,
   marque `divergencia = true`, rebaixe a confiança para no máximo 0,55, emita `DIVERGENCIA_ECONOMIAS` e preencha
   `faixa` com `[min, max]` das evidências. Divergência não é falha da anotação — é a anotação fazendo seu trabalho:
   ela está dizendo que este imóvel precisa de vistoria, e essa informação vale mais que um número inventado.
5. Nunca reconcilie divergência escolhendo a média. `2` e `6` não viram `4`; viram `faixa [2,6]` e uma vistoria.

`R-ECO-13` — **UC elétrica não é economia de água.** Se a única evidência para múltiplas unidades for `energia.medidores_energia_qtd`, `economias.estimativa` não pode ser elevado automaticamente acima de 1. Registre a oportunidade cadastral e mantenha a decisão tarifária para cruzamento/vistoria.

### Casos que sempre valem atenção

- **Vila / corredor lateral** (`R-ECO-10`): portão único na testada com corredor que dá acesso a várias casas nos
  fundos. A testada mostra 1 porta e a realidade tem 5 economias. Sinais: corredor lateral estreito, bateria de
  medidores desproporcional à testada, várias caixas de correio, vários hidrômetros agrupados. Quando a bateria
  de medição discorda da testada, **a bateria vence** — ela conta o que existe, a testada só o que se vê.
- **Prédio com térreo comercial** (`R-ECO-11`): economias residenciais e comerciais são contadas e reportadas
  separadamente em `usos_secundarios`, porque a tarifa difere por categoria.
- **Condomínio horizontal com portaria** (`R-ECO-12`): a fachada é o muro do condomínio, não do imóvel. A anotação
  descreve o condomínio, `economias.estimativa` fica `null` e o alerta `COLETIVA_DETECTADA` marca que o grão do
  registro não é o grão do imóvel. Anotar "1 economia" aqui seria simplesmente falso.

---

## Ocupacao

`R-OCU-01` — **Contagem de sinais.** Some os indícios de ocupação e de vacância listados na taxonomia:

| Condição | `situacao_ocupacao` | Confiança sugerida |
|---|---|---|
| ≥ 2 indícios de ocupação e 0 de vacância | `ocupado` | até o teto da fonte |
| 1 indício de ocupação e 0 de vacância | `provavelmente_ocupado` | ≤ 0,75 |
| ≥ 3 indícios de vacância e 0 de ocupação | `vago_evidente` | até o teto |
| ≥ 2 de vacância, 0 de ocupação | `provavelmente_vago` | ≤ 0,70 |
| sinais dos dois lados | `indeterminado` + `REVISAO_HUMANA` | ≤ 0,45 |
| nenhum sinal em nenhum lado | `indeterminado` | ≤ 0,40 |

`R-OCU-02` — **Comércio fechado não é vago.** Porta de aço baixada, sem mato, sem pichação, com letreiro íntegro
e medidor no lugar → `fechado_no_momento`. Só migre para vago se houver indício estrutural de abandono
(letreiro removido, medidor retirado, entulho, vidro quebrado).

`R-OCU-03` — **Vacância não sustenta inexistência.** `provavelmente_vago` alimenta priorização de vistoria; nunca
proponha inativação de matrícula a partir dela. `SUSPEITA_IMOVEL_INEXISTENTE` exige `terreno_sem_edificacao`,
`demolido` ou `ruina` — ausência de edificação, não ausência de morador.

`R-OCU-04` — **Defasagem manda em Street View.** Em imagem com `idade_meses > 24`, o teto de confiança dos campos
voláteis já decai pela fórmula em `assets/observabilidade.json`. Uma foto de 2019 dizendo "em obra" descreve 2019,
não hoje — e o campo `IMAGEM_DEFASADA` existe para impedir que essa informação circule como se fosse atual.

---

## Uso

`R-USO-01` — **Letreiro manda.** Letreiro comercial legível no térreo → `uso_predominante` ao menos parcialmente
comercial. Transcreva literal antes de classificar; classificar sem transcrever perde a evidência.

`R-USO-02` — **Misto por estratificação vertical.** Térreo com vitrine/porta comercial larga/letreiro + pavimento
superior com cortina, varal, ar-condicionado tipo split residencial ou sacada com uso doméstico →
`misto_res_com`. Este é o achado de maior retorno tarifário do bloco de uso.

`R-USO-03` — **Porta larga sem letreiro não é comércio.** Portão de garagem largo é o falso positivo mais comum.
Exija ao menos dois sinais comerciais (vitrine, toldo, letreiro, grade tipo loja, balcão visível, estacionamento
de clientes) antes de classificar como comercial sem letreiro.

`R-USO-04` — **CNAE só até divisão.** Duas casas de CNAE, e apenas quando o segmento é inequívoco pelo letreiro.
Subclasse a partir de foto é precisão inventada.

---

## Medicao de agua

`R-AGU-01` — **Abrigo sem medidor visível ≠ ausência.** Caixa fechada → `abrigo_visivel_medidor_nao`. Só use
`ausente_confirmado` quando o abrigo está aberto e vazio, ou quando há ramal aparente entrando no imóvel sem
qualquer ponto de medição na testada.

`R-AGU-02` — **Ligação aparente sem medição.** Tubulação entrando no lote sem passar por abrigo/cavalete →
`suspeita_irregularidade_agua = ligacao_aparente_sem_medidor`, alerta `SUSPEITA_IRREGULARIDADE` (atenção).
Descreva o traçado do tubo na evidência. A palavra "fraude" não entra na anotação: a foto sustenta a hipótese,
a vistoria sustenta a conclusão, e a diferença entre as duas é responsabilidade jurídica.

`R-AGU-03` — **Impedimento previsto.** `acessibilidade_medicao` ∈ {`atras_de_portao`, `interno_requer_morador`,
`inacessivel`} ou placa de cão → `risco_impedimento_leitura` correspondente + alerta `IMPEDIMENTO_LEITURA`.
Esta é a saída que cruza diretamente com histórico de ocorrência 6/10 e com a skill `fora-de-rota-pdca`.

`R-AGU-04` — **Leitura do mostrador exige três condições simultâneas**: fonte `campo`, mostrador enquadrado e
todos os dígitos nítidos. Falhou uma → `null`. Não interpole dígito borrado a partir de leitura anterior:
isso fabrica consumo.

---

## Esgoto

`R-ESG-01` — **Conectado (provável).** Caixa de inspeção na calçada/recuo + PV de rede na via em frente +
ausência de lançamento em sarjeta → `provavelmente_conectado`, confiança ≤ 0,60, `juizo: inferido`.

`R-ESG-02` — **Não conectado (provável).** Tubo atravessando calçada com despejo na sarjeta, ou mancha de
umidade permanente na sarjeta em tempo seco, ou respiro de fossa com ausência de rede aparente →
`provavelmente_nao_conectado`, confiança ≤ 0,60.

`R-ESG-03` — **Não confunda tampas.** Antes de marcar caixa de inspeção, descarte: caixa de passagem elétrica
(retangular menor, logo da distribuidora), registro de água (menor, logo da companhia de saneamento), boca de
lobo (na sarjeta, com grelha), caixa de telecom (marca de operadora). Na dúvida entre duas →
`sim_posicao_indefinida` com confiança rebaixada, ou `nao_observavel`.

`R-ESG-04` — **Teto duro.** `conectividade_esgoto_inferida` nunca passa de 0,60, em nenhuma fonte. A rede é
subterrânea; a fachada dá indício, não prova. Quem decide conectividade é o cadastro de rede cruzado com a
viabilidade técnica.

---

## Padrao construtivo

`R-PAD-01` — **Três sinais mínimos.** Classifique o padrão somando sinais independentes de acabamento,
esquadria e cobertura. Com menos de três sinais legíveis → `indeterminado`. Padrão construtivo derivado de um
único detalhe (só a pintura, por exemplo) é instável entre anotadores e polui a série histórica.

`R-PAD-02` — **Conservação é ortogonal a padrão.** Casa de padrão `superior` pode estar `ruim` de conservação e
casa `popular` pode estar `nova`. Não deixe um contaminar o outro — são duas perguntas diferentes e cada uma
alimenta uma decisão diferente.

`R-PAD-03` — **Não infira renda, classe social ou perfil do morador.** A anotação descreve a construção. Qualquer
juízo sobre quem mora ali está fora do escopo, não é observável, e cria risco discriminatório em decisão
operacional (corte, vistoria, cobrança). Se a pergunta de negócio parecer exigir isso, ela precisa ser
reformulada em termos de atributos físicos observáveis.

---

## Calculo de confianca

Confiança final de cada campo:

```
teto_efetivo = teto_fonte[atributo][fonte]
             × decaimento_temporal        (só campos voláteis)
             × penalidade_enquadramento
             × penalidade_nitidez
             × penalidade_iluminacao

confianca    = min(base_do_juizo, teto_efetivo)
```

As penalidades entram no **teto**, não na base — a qualidade da imagem limita o que a fonte pode sustentar,
independentemente de quão evidente o objeto pareceu. É exatamente assim que `scripts/validar_anotacao.py`
recalcula, então a conta que você faz ao anotar é a mesma que o gate refaz.

`base_do_juizo`: comece em 0,95 para `observado` com o objeto nítido e inequívoco; 0,80 para `observado` com
objeto pequeno ou parcialmente oculto; 0,70 para `inferido` com regra de força forte; 0,55 para `inferido` com
regra de força média; 0,40 para `inferido` com regra fraca.

`teto_efetivo` sai de `assets/observabilidade.json`, já com o decaimento temporal aplicado nos campos voláteis.
O validador recalcula esse teto e rejeita qualquer campo acima dele — não porque a estimativa esteja errada,
mas porque uma confiança que a fonte não pode sustentar contamina todo consumidor a jusante que filtre por
confiança. Se o teto parecer baixo demais para um caso real, o caminho é discutir o teto na matriz, não
burlá-lo no registro.

---

## Comércio e temporalidade — regras adicionais

`R-USO-05` — **Descrição funcional.** `descricao_atividade_funcional` só pode ser inferida quando letreiro, fachada temática ou sinais operacionais permitem explicar em uma frase o que funciona no local. A evidência deve citar o sinal concreto; não inferir atividade apenas pelo formato do imóvel.

`R-USO-06` — **Estado vale na data da foto.** `situacao_estabelecimento_na_data_imagem` descreve apenas o instante documentado. Street View/Mapillary sem `data_captura` não pode produzir `em_atividade_aparente` ou `desativado_aparente` com confiança operacional; use `indeterminado`/revisão.

`R-USO-07` — **Fechado ≠ desativado.** Porta de aço baixada isoladamente → `fechado_no_momento`. `desativado_aparente` exige pelo menos dois sinais compatíveis, como letreiro removido, fachada descaracterizada, vitrine vazia/degradada, reforma incompatível ou medidor removido.

`R-USO-08` — **Uso misto.** Comércio no térreo + sinais residenciais independentes em outro pavimento/testada → `misto_res_com` + oportunidade `USO_MISTO_POTENCIAL` quando relevante.

`R-USO-09` — **Divergência com base.** Se `vinculo.uso_base` for residencial e o uso visual comercial/misto tiver confiança suficiente, gerar `USO_COMERCIAL_NAO_CADASTRADO`. A data da imagem deve acompanhar a oportunidade.

## Unidades consumidoras — regras adicionais

`R-UC-01` — **Duas ou mais UCs elétricas.** `energia.medidores_energia_qtd >= 2` dentro da ROI do imóvel-alvo gera `MULTIPLAS_UCS_MESMO_ENDERECO`. Isso é oportunidade de cruzamento, não prova de múltiplas economias de água.

`R-UC-02` — **Divergência objetiva.** Se `vinculo.economias_agua_base` existe e é menor que a contagem de UCs elétricas visíveis, gerar `DIVERGENCIA_UC_ECONOMIAS`, com ação sugerida de recadastramento/vistoria.

`R-UC-03` — **Convergência aumenta prioridade.** Múltiplas UCs + múltiplos acessos/numerações/hidrômetros elevam a confiança de subdivisão física.

`R-UC-04` — **Não usar medidor do vizinho.** Só contar medidor associado ao `alvo_fachada`. Imagem com várias testadas e ROI incerta rebaixa confiança e pode exigir `VINCULO_IMOVEL_AMBIGUO`.

`R-UC-05` — **Bateria coletiva.** Caixa coletiva com N medidores completos pode sustentar N UCs, desde que os medidores pertençam ao mesmo endereço/alvo. Caixas vazias não contam.

`R-UC-06` — **Economia de água é outra pergunta.** O número de economias de água deve ser inferido pela hierarquia própria e cruzado com a informação elétrica; divergência é dado útil, não algo a ser “corrigido” por média.

## Vínculo e temporalidade

`R-VIN-01` — confiança de vínculo < 0,80 ou `ambiguidade=true` impede alteração cadastral automática e exige revisão.

`R-TEM-01` — toda oportunidade baseada em uso, ocupação ou atividade comercial recebe `data_referencia = imagem.data_captura`. Sem data, a oportunidade pode existir como histórica/indeterminada, mas não deve ser apresentada como situação atual.

---

## Endereçamento visual e identidade — regras v1.2

`R-END-01` — **Leia o número onde ele realmente aparece.** Número do imóvel pode estar na fachada, porta, portão, caixa de correio, placa de endereço ou placa comercial. Registre o texto literal e a origem física. A origem não é detalhe: ela permite revisar falso positivo depois.

`R-END-02` — **Número de equipamento não é número da casa.** Serial de medidor, matrícula de hidrômetro, telefone, CNPJ, preço, horário, número de poste e placa veicular não podem alimentar `numero_endereco_consolidado` sem evidência explícita de que representam endereço.

`R-END-03` — **Concordância intraimagem aumenta confiança, não independência de fonte.** O mesmo `350` na fachada e na caixa de correio torna a leitura visual mais forte, mas continua sendo uma única fonte independente (`imagem_fachada`).

`R-END-04` — **Caixa de correio é evidência dupla.** Um número em caixa de correio pode corroborar o endereço. Múltiplas caixas individualizadas, sobretudo com complementos diferentes (`125-A`, `125-B`, `casa 1`, `casa 2`), também sustentam hipótese de múltiplas unidades.

`R-END-05` — **Não leia o morador.** Nome de destinatário, família ou pessoa em caixa de correio/interfone fica fora da saída. Só unidade, número, complemento ou informação estrutural do imóvel pode ser transcrita.

`R-END-06` — **Divergência de número bloqueia o casamento.** Se `numero_endereco_consolidado` divergir de `vinculo.numero_base`, marque `vinculo.numero_confere=false`, gere `DIVERGENCIA_NUMERO_ENDERECO` e não aumente confiança por proximidade espacial. Quando o método de vínculo depende de endereço/GPS+numero, a divergência exige `ambiguidade=true` e confiança de vínculo abaixo de 0,80 até resolução. A exceção é `id_direto`, que preserva a identidade técnica da matrícula mas trata a numeração como contradição cadastral a confirmar.

`R-END-07` — **Sequência predial é inferência fraca.** Se vizinhos mostram 145 e 149, o alvo pode ser 147 pela sequência/paridade, mas o valor deve ficar `inferido`, com confiança baixa e nunca ser usado sozinho para vincular matrícula ou gerar atualização cadastral.

`R-END-08` — **Complementos importam.** `125`, `125-A`, `125-B`, `fundos`, `casa 1/2`, `loja 1/2`, `sala 1/2` são sinais de subdivisão física/funcional. Quando convergem com caixas de correio, portas, interfones ou medidores, elevam a prioridade de `ECONOMIAS_OCULTAS_POTENCIAL`.

## Sinal, achado e contradição — regras v1.2

`R-CONV-01` — **Fonte única gera sinal.** Uma imagem isolada pode gerar oportunidade, mas `nivel_evidencia` deve ser `sinal_imagem` quando nenhum dado independente foi confrontado.

`R-CONV-02` — **Achado exige duas fontes independentes.** `nivel_evidencia=achado_convergente` exige pelo menos duas origens independentes em `fontes_independentes`. Duas pistas na mesma fotografia contam como uma única fonte.

`R-CONV-03` — **Comparação com cadastro é convergência quando a identidade está resolvida.** Imagem mostrando uso comercial + cadastro da companhia dizendo residencial pode ser achado convergente de divergência de uso. Imagem mostrando 3 UCs + cadastro de água com 1 economia pode ser achado convergente de divergência cadastral. A conclusão continua sendo “fila de revisão”, não alteração automática.

`R-CONV-04` — **Discordância vira contradição, não média.** Fontes que discordam devem produzir `nivel_evidencia=contradicao`; não escolha a fonte conveniente nem faça média de números incompatíveis.

## Quatro teses de negócio — regras v1.2

`R-PGV-01` — **Categoria diferente do uso real.** Uso comercial/serviços/misto visualmente sustentado, confrontado com categoria/uso residencial da base, gera fila de revisão de categoria com data da imagem.

`R-PGV-02` — **Economias ocultas.** Múltiplas unidades físicas/funcionais visualmente sustentadas, confrontadas com quantidade menor de economias na base, geram `ECONOMIAS_OCULTAS_POTENCIAL`. A quantidade proposta de economias de água só sobe quando a hierarquia de evidência específica de água permite.

`R-PGV-03` — **Esgoto sem cobrança é hipótese multifuente.** Proximidade de rede, sozinha, nunca prova conexão. A oportunidade `ESGOTO_SEM_COBRANCA_POTENCIAL` exige cruzamento entre faturamento/cadastro e evidência de rede/ligação; quando a conexão não é comprovável tecnicamente, a vistoria é obrigatória.

`R-PGV-04` — **Área divergente exige fonte geométrica adequada.** Foto frontal não produz área construída confiável em m². `AREA_DIVERGENTE_POTENCIAL` deve usar footprint/imagem aérea/drone + área cadastral, registrando método e incerteza. Pavimentos visíveis podem ajudar a estimar área construída, mas não substituem footprint.

## Conciliação multi-imagem — regras v1.3

Uma campanha real produz mais de uma imagem por matrícula, e cada fonte enxerga bem uma coisa diferente.
Conciliar não é escolher a melhor foto; é escolher a melhor fonte **por campo**. Executado por
`scripts/conciliar_multifoto.py`.

`R-CON-01` — **Vitória por teto, não por confiança declarada.** O vencedor de cada campo é o de maior
`min(confianca, teto_efetivo)`. Isso faz o hidrômetro vir da foto de campo, a cobertura e o footprint virem do
drone e a testada vir da panorâmica, sem que ninguém precise decidir manualmente. Empate resolve por data mais
recente e, persistindo, por nome de arquivo — a ordenação é total para que a saída seja reproduzível.

`R-CON-02` — **Duas fotos não são duas fontes.** Duas imagens de nível de rua continuam sendo a fonte
`imagem_fachada`; fachada + aérea são duas classes. A conciliação **marca elegibilidade** de convergência e para
aí: promover `sinal_imagem` a `achado_convergente` é decisão do motor cadastral com fonte externa na mesa
(`R-CONV-02`), não do conciliador. A flag `--elevar-convergencia` existe para o caso em que essa decisão já foi
tomada a montante, e o relatório sempre registra que foi usada.

`R-CON-03` — **Campo volátil em datas diferentes é série temporal, não erro.** Comércio que fechou, obra que
terminou, medidor que foi retirado: o registro sai como `temporal`, mantendo o valor mais bem sustentado e
preservando os candidatos no relatório. Tratar isso como contradição enche a fila de revisão com o
funcionamento normal do mundo.

`R-CON-04` — **Campo estável em desacordo é contradição, e vai para revisão.** Drone vendo dois pavimentos e
foto frontal vendo um não é ruído: costuma ser edícula ou segundo pavimento nos fundos — exatamente o achado que
sustenta `AREA_DIVERGENTE_POTENCIAL` e, às vezes, `ECONOMIAS_OCULTAS_POTENCIAL`. Nunca resolva escolhendo em
silêncio: registre as duas leituras com fonte e score.

`R-CON-05` — **Só conciliam imagens com o mesmo vínculo.** A chave é `vinculo.matricula` (ou `imovel_id`).
Anotação sem chave não entra no grupo e aparece no funil como `sem_chave` — juntar por proximidade espacial é
justamente o que `R-END-06` proíbe. Imagem inapta é descartada do grupo, também com contagem explícita.

## Triagem de imagem e atividade econômica — regras v1.4

`R-TRI-01` — **Triar antes de ler.** O bloco `triagem` é preenchido no Passe 0, antes de qualquer atributo. Ler
primeiro e triar depois inverte o custo: o esforço já foi gasto quando se descobre que a foto era de um print de
sistema. Em campanha de dezenas de milhares de imagens, essa inversão é o item mais caro do orçamento.

`R-TRI-02` — **`e_imovel=false` encerra a anotação (`C-16`).** Sem atributos, sem oportunidades, com
`IMAGEM_FORA_DE_ESCOPO` e motivo. O produto dessa imagem é uma ordem de recoleta, não um registro cadastral.
Exceção parcial: `detalhe_medicao` e `interior_imovel` continuam úteis — marque `apto_para_cadastro=false` e
declare `uso_parcial_permitido`, para que o consumidor a jusante saiba o que dá para aproveitar.

`R-TRI-03` — **Atividade exige sinal e dono (`C-18`).** Declarar atividade sem listar o sinal físico que a
sustenta é opinião; declarar sem dizer de quem ela é (alvo, vizinho, ambulante) é meio caminho para o achado
falso. Os dois campos são obrigatórios sempre que `atividade_economica_aparente` for `sinal_fraco` ou
`sinal_forte`.

`R-TRI-04` — **Gate anti-contaminação (`C-19`).** Atividade atribuída a `em_vizinho` ou `ambulante_via_publica`
não sustenta uso econômico no imóvel-alvo. Para classificar o alvo como comercial é preciso sinal **próprio**
dele — e aí o valor correto de `atividade_no_alvo` passa a ser `no_imovel_alvo`. Esta regra é o par visual do
`alvo_fachada`: uma delimita o espaço, a outra delimita a atribuição.

`R-TRI-05` — **Detectar e não registrar é pior que não detectar (`C-17`).** Sinal forte no alvo tem que aparecer
em `uso.uso_predominante` (uso econômico ou `misto_res_com`) ou virar oportunidade de categoria. Um sinal forte
que morre no bloco de triagem é trabalho jogado fora — e um dado que contradiz a própria anotação.

`R-TRI-06` — **Atividade domiciliar é fila, não reclassificação.** `atividade_domiciliar` gera
`ATIVIDADE_DOMICILIAR_POTENCIAL` com `nivel_evidencia=sinal_imagem`. A promoção a achado exige fonte
independente — CNPJ no endereço, cadastro da companhia, histórico de consumo compatível com uso não residencial.
Reclassificar categoria tarifária a partir de uma placa de acrílico no muro é exatamente o tipo de decisão
automática que gera passivo regulatório.

`R-TRI-07` — **Teto de fonte vale para a triagem.** Conteúdo da imagem é fácil de ver em qualquer fonte (teto
alto); atividade econômica em imagem aérea é quase cega (teto 0,35 e caindo com o decaimento temporal, porque
comércio abre e fecha). A `confianca_triagem` respeita o menor teto entre os campos que ela sustenta.

## Cadastro vertical — regras v1.5

Para casa, a fachada basta. Para prédio, vila e condomínio é preciso **reconstruir a estrutura de unidades** —
e a pergunta deixa de ser "quantas economias vejo?" para "qual é a estrutura física provável deste
empreendimento, e como ela se compara com o que o cadastro registra?".

`R-EST-01` — **Quatro medidas, nunca fundidas.** `unidades_fisicas` ≠ `ucs_energia` ≠ `economias_agua` ≠
`hidrometros`. Cada uma responde a uma pergunta diferente e é produzida por um processo diferente. O achado
cadastral **é a divergência entre elas**; fundir duas produz um número que parece resposta e não é. Nove UCs
elétricas nunca viram "nove economias de água" — viram uma fila de vistoria com evidência forte.

`R-EST-02` — **Contagem no prumo.** A fachada vertical repete-se: prumadas de janela, sacada, ar-condicionado,
shaft. `unidades_fisicas ≈ torres × pavimentos_residenciais × unidades_por_pavimento + unidades_comerciais`.
O validador confere essa aritmética com folga de 25% e cobra explicação na evidência quando não fecha —
pilotis, cobertura duplex e recuo de último pavimento são as causas legítimas mais comuns.

`R-EST-03` — **Interfone e caixa de correio são a melhor evidência em vertical.** Uma botoeira lendo
`101 102 201 202 301 302 401 402` não diz apenas "oito unidades": revela **4 pavimentos × 2 unidades**, ou seja,
a estrutura. Vale mais que a contagem de janelas. Se ao lado houver nove medidores, a leitura correta é
"oito apartamentos + uma UC de área comum ou da loja", não "nove apartamentos". Transcreva os identificadores
(`101;102;...`) e **nunca** o nome do morador ao lado deles.

`R-EST-04` — **No vertical, porta de testada não mede unidade.** Um edifício de 40 apartamentos tem uma porta
social e uma de garagem. `portas_independentes` continua valendo em casa, vila e galeria — no prédio ela conta
acessos ao edifício, e usá-la como evidência de economia subconta o imóvel inteiro.

`R-EST-05` — **Condomínio horizontal e vila valem tanto quanto o prédio.** Um número de rua, uma ligação
cadastrada, e atrás do portão cinco casas com cinco medidores, cinco caixas de correio e cinco portas. É o
mesmo achado do edifício com outra geometria, e costuma passar despercebido porque a testada parece uma casa.
Sinais: corredor lateral, bateria de medição desproporcional à testada, portão único com várias campainhas.

`R-EST-06` — **Multitorre não se conta por pavimento total.** Três torres de 12 pavimentos não são 36 unidades:
são `3 × 12 × unidades_por_pavimento`. Sem `unidades_por_pavimento` legível, o resultado é faixa, não número.

`R-EST-07` — **Verticalização é mudança estrutural, não erro de cadastro.** Cadastro dizendo "casa, 1 economia"
e território mostrando edifício de seis pavimentos → `mudanca_estrutural = casa_para_edificio` e
`VERTICALIZACAO_NAO_CADASTRADA` em prioridade alta. O cadastro não estava errado quando foi feito; o território
mudou depois. Essa distinção importa na conversa com a concessionária.

`R-EST-08` — **Composição de uso por pavimento.** Térreo comercial com superiores residenciais não é apenas
"misto": registre `composicao_uso_terreo` e `composicao_uso_superiores`. Mesmo com as economias corretas, a
**categoria** pode estar errada — e essa é uma tese de receita independente da contagem de unidades.

---

## Atributos cartografáveis — regras v1.5

`R-MAP-01` — **Esta skill não desenha mapa; produz o insumo do mapa.** O bloco `mapeamento` separa dois tipos de
campo: os de **origem-imagem** (número observado, uso observado, unidades estimadas, idade da evidência), que a
anotação preenche, e os de **origem-territorial** (`face_id`, `quadra_id`, `logradouro_id`,
`cluster_oportunidade`), que só existem depois de agregar milhares de imóveis e são atribuídos pelo módulo
`pgv-mapeamento-cadastral`. Preencher os territoriais aqui é erro (`C-27`): a face de quadra de um imóvel não é
observável na foto dele.

`R-MAP-02` — **Status de endereço é um estado, não um booleano.** `confere`, `diverge`, `sem_numero`,
`multiplos_numeros`, `complemento_encontrado`, `ambiguo`, `hipotese_sequencia`, `sem_cadastro`. Cada um leva a
uma ação diferente: `diverge` vai para conferência cadastral, `sem_numero` para coleta em campo,
`multiplos_numeros` para desmembramento.

`R-MAP-03` — **Número por interpolação da face é hipótese, com teto 0,60.** Uma fachada sem número entre o 330 e
o 350 provavelmente é 340 — mas "provavelmente" é a palavra inteira. Grave `numero_estimado_contextual` com
`fonte_numero_estimado = sequencia_face` e `status_endereco = hipotese_sequencia`. O valor cresce na
convergência: caixa de correio dizendo 340 **e** sequência da face dizendo 340 **e** cadastro dizendo 340 é outra
coisa — e é o módulo territorial que faz essa soma, não a leitura da foto.

`R-MAP-04` — **Valor esperado é probabilidade × impacto.** `valor_esperado_anual = probabilidade_confirmacao ×
impacto_anual_estimado`, conferido pelo validador. Mapear quantidade de suspeitas ordena a fila errada: um
bairro com 400 suspeitas de R$ 80 mil/ano vale menos que outro com 120 suspeitas de R$ 900 mil/ano.

`R-MAP-05` — **Vigência da evidência, não "foto velha".** `0_12m`, `13_24m`, `25_36m`, `37_60m`, `acima_60m`,
derivada de `idade_meses` e conferida contra ela. Um comércio observado em 2019 não pode aparecer no mapa com o
mesmo peso de um comprovado neste ano — e a camada de vigência é o que mostra onde a evidência territorial
precisa ser renovada.
