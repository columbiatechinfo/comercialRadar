# Taxonomia — vocabulário fechado dos campos de fachada

Todo campo de texto usa **exclusivamente** um dos valores abaixo. Vocabulário fechado é o que torna a anotação
cruzável com a base cadastral: texto livre vira coluna inútil no `GROUP BY`. Quando nada da lista serve, use
`outro:<descrição curta em minúsculas, sem acento>` — isso preserva o caso raro sem contaminar o domínio, e o
validador conta esses casos para você saber quando a lista precisa crescer.

Sumário: [Endereçamento](#1-enderecamento) · [Edificação](#2-edificacao) · [Uso](#3-uso) · [Ocupação](#4-ocupacao) ·
[Água](#5-agua) · [Energia](#6-energia) · [Esgoto](#7-esgoto) · [Entorno](#8-entorno) · [Alertas](#9-alertas)

---

## 1. Enderecamento

| Campo | Valores canônicos |
|---|---|
| `numero_fachada` | transcrição **literal** do número fixado/pintado na fachada, incluindo letra e zeros à esquerda (`0125`, `125-A`, `S/N`). Não normalize, não complete, não corrija. |
| `numero_porta` | número literal visível na porta do imóvel/unidade. |
| `numero_portao` | número literal visível no portão/grade da testada. |
| `numero_caixa_correio` | número/endereço literal visível na caixa de correio. Não transcrever nome de destinatário. |
| `numero_placa_comercial` | número/endereço literal impresso em placa/letreiro comercial quando a placa claramente identifica o endereço. |
| `numero_endereco_consolidado` | melhor leitura consolidada do número do imóvel; só preencher quando as ocorrências compatíveis permitem consolidar sem adivinhar. |
| `origem_numero_consolidado` | `fachada`, `porta`, `portao`, `caixa_correio`, `placa_endereco`, `placa_comercial`, `interfone`, `outro`, `indeterminado`. |
| `complementos_unidades_visiveis` | transcrição estrutural de complementos como `125-A;125-B`, `casa 1;casa 2`, `fundos`, `loja 1;loja 2`. |
| `divergencia_numero_base` | booleano observado/inferido após comparação com `vinculo.numero_base`; divergência bloqueia vínculo por endereço até resolução. |
| `numeros_adicionais` | lista separada por `;` quando há mais de uma numeração na mesma testada (`125;125A;127`) |
| `complemento_visivel` | `casa`, `fundos`, `apto`, `loja`, `sala`, `bloco`, `quadra_lote`, `sem_complemento` |
| `placa_logradouro` | transcrição literal do nome da via na placa |
| `cep_visivel` | 8 dígitos literais ou `null` |
| `paridade` | `par`, `impar`, `indeterminada` |
| `lado_via` | `direito`, `esquerdo`, `indeterminado` — sentido crescente da numeração; só preencha se a visada permitir inferir |

**Regra dura da numeração:** o número visual é uma das evidências mais baratas e valiosas da anotação — ele ancora o registro na régua de numeração do logradouro. Procure em fachada, porta, portão, caixa de correio e placa de endereço/comercial. Transcreva dígito por dígito e registre a origem física. Se um dígito está oculto, o campo é `null`/não observável; não complete pelo contexto. O mesmo número em dois objetos da mesma foto aumenta confiança, mas continua sendo uma única fonte independente.

**Caixa de correio:** número ou complemento escrito na caixa pode corroborar o endereço e, quando existem várias caixas individualizadas, também indicar múltiplas unidades. Não transcreva nome de pessoa/família. Não confunda serial de medidor, telefone, CNPJ, preço ou número de poste com número da casa.

---

## 2. Edificacao

**`tipo_edificacao`**
`casa_terrea`, `casa_sobreposta`, `sobrado`, `geminada`, `edificio_residencial`, `edificio_misto`,
`edificio_comercial`, `galpao`, `barracao`, `loja_terrea`, `quiosque_banca`, `posto_combustivel`,
`condominio_horizontal`, `templo`, `escola`, `unidade_saude`, `predio_publico`, `estrutura_rural`,
`terreno_sem_edificacao`, `edificacao_em_construcao`, `edificacao_em_demolicao`, `ruina`

**`alinhamento_testada`** — `no_alinhamento` (parede na divisa da calçada), `recuado_com_jardim`,
`recuado_com_garagem`, `recuado_amplo`, `indeterminado`

**`testada_estimada_m`** — faixa, nunca valor pontual: `ate_5`, `5_a_10`, `10_a_20`, `20_a_50`, `acima_50`.
Ancore em objeto de dimensão conhecida na cena (porta ≈ 0,80 m, portão de carro ≈ 2,50 m, poste ≈ 8-11 m,
altura de piso a piso ≈ 3,0 m). Diga a âncora usada na evidência.

**`recuo_frontal`** — `nulo`, `ate_3m`, `3_a_10m`, `acima_10m`, `indeterminado`

**`padrao_construtivo`** — `precario`, `popular`, `normal`, `superior`, `alto`, `indeterminado`

Régua de decisão (a fachada informa padrão, não valor venal — não pretenda mais precisão do que a imagem tem):

- `precario` — vedação improvisada (madeira de reaproveitamento, lona, taipa sem revestimento), cobertura em
  fibrocimento aparente sem forro, esquadria sem vidro ou vão sem esquadria, sem revestimento externo.
- `popular` — alvenaria simples, reboco pintado ou bloco aparente, telha cerâmica/fibrocimento, esquadria
  metálica simples, sem elemento decorativo, testada pequena.
- `normal` — alvenaria revestida e pintada em bom estado, esquadria de alumínio ou madeira, garagem coberta,
  muro/gradil regular, algum acabamento (soleira, pastilha pontual).
- `superior` — revestimento nobre parcial (porcelanato, pedra, madeira, ACM), esquadria ampla em alumínio
  anodizado/vidro, projeto arquitetônico legível, paisagismo, portão automatizado.
- `alto` — fachada em vidro/pedra com projeto autoral, grandes vãos, guarita, área verde tratada, muro alto
  com acabamento, indicadores múltiplos de alto custo unitário.

**`estrutura_vedacao`** — `alvenaria`, `concreto_armado`, `pre_moldado`, `metalica`, `madeira`, `mista`,
`taipa_adobe`, `improvisada`, `indeterminada`

**`acabamento_fachada`** — `sem_reboco`, `reboco_sem_pintura`, `pintura_simples`, `textura`, `ceramica_pastilha`,
`porcelanato_pedra`, `acm_vidro`, `madeira`, `indeterminado`

**`cobertura`** — `telha_ceramica`, `telha_fibrocimento`, `telha_metalica`, `laje_impermeabilizada`,
`telha_ceramica_com_platibanda`, `improvisada`, `sem_cobertura`, `nao_visivel`

**`esquadrias`** — `madeira`, `ferro`, `aluminio`, `pvc`, `vidro_temperado`, `vao_sem_esquadria`, `mista`,
`nao_visivel`

**`estado_conservacao`** — `novo`, `bom`, `regular`, `ruim`, `em_ruina`, `indeterminado`

**`fechamento_frontal`** — `sem_fechamento`, `muro_baixo`, `muro_alto`, `gradil`, `muro_com_gradil`,
`cerca_viva`, `cerca_arame`, `tapume`, `indeterminado`

**`area_permeavel`** — `inexistente`, `pequena`, `media`, `ampla`, `nao_visivel`

---

## 3. Uso

**`uso_predominante`** — `residencial`, `comercial`, `industrial`, `servicos`, `publico`, `religioso`,
`educacional`, `saude`, `rural_agropecuario`, `misto_res_com`, `terreno`, `indeterminado`

`misto_res_com` é a classe mais lucrativa de detectar e a mais esquecida: loja no térreo com residência
no pavimento superior é o caso clássico de imóvel tarifado como residencial que deveria ter economia
comercial associada. Sinais: letreiro no térreo + varal/cortina/ar-condicionado no superior, ou porta
comercial larga ao lado de porta social estreita.

**`atividade_letreiro`** — transcrição **literal** do letreiro/toldo/fachada comercial (nome fantasia,
razão social, ramo). Preserve grafia. Telefone comercial pode ser transcrito; ver `lgpd`.

**`segmento_inferido`** — `alimentacao`, `mercado_conveniencia`, `vestuario`, `farmacia_saude`,
`oficina_autopecas`, `construcao_material`, `beleza_estetica`, `servicos_profissionais`, `educacao`,
`hospedagem`, `financeiro`, `industria_transformacao`, `logistica_deposito`, `agropecuario`, `outro`,
`indeterminado`

**`cnae_sugerido`** — divisão CNAE de 2 dígitos apenas (`47`, `56`, `45`). Não sugira subclasse de 7 dígitos
a partir de uma foto: a precisão seria falsa.

**`porte_aparente`** — `micro`, `pequeno`, `medio`, `grande`, `indeterminado`

---

## 4. Ocupacao

**`situacao_ocupacao`** — `ocupado`, `provavelmente_ocupado`, `fechado_no_momento`, `provavelmente_vago`,
`vago_evidente`, `abandonado`, `em_obra`, `demolido`, `terreno_sem_edificacao`, `indeterminado`

`fechado_no_momento` ≠ `vago`. Comércio de porta de aço baixada em foto de domingo é `fechado_no_momento`.
Confundir os dois gera vistoria desperdiçada e é o erro mais caro deste bloco.

**`indicios_ocupacao`** (lista `;`) — `cortina_persiana`, `ar_condicionado`, `antena_parabolica`,
`veiculo_na_garagem`, `roupa_no_varal`, `lixeira_em_uso`, `planta_cuidada`, `grama_aparada`, `luz_acesa`,
`portao_aberto`, `movimentacao_pessoas`, `animal_domestico`, `brinquedo_mobiliario`, `caixa_dagua_recente`,
`medidor_com_lacre_integro`

**`indicios_vacancia`** (lista `;`) — `mato_alto`, `tapume`, `janela_quebrada`, `porta_lacrada`,
`pichacao_extensa`, `entulho`, `sem_esquadrias`, `caixa_correio_transbordando`, `medidor_removido`,
`telhado_desabado`, `placa_venda_aluguel`, `acumulo_folhas_poeira`, `hidrometro_removido`

**`risco_impedimento_leitura`** — `nenhum`, `portao_trancado`, `cao_solto`, `medicao_interna`,
`obstrucao_veiculo`, `obstrucao_vegetacao`, `medicao_em_altura`, `acesso_por_terceiro`, `indeterminado`

Este campo é o de maior retorno operacional direto: alimenta o planejamento de ocorrência de leitura antes de
a ocorrência acontecer. Um `cao_solto` anotado hoje é um código 6 evitado no próximo ciclo.

---

## 5. Agua

**`hidrometro_presente`** — `sim_visivel`, `abrigo_visivel_medidor_nao`, `ausente_confirmado`,
`nao_observavel`, `bateria_coletiva`

**`posicao_medicao`** — `caixa_embutida_no_muro`, `caixa_no_passeio`, `cavalete_aparente_externo`,
`cavalete_interno_ao_lote`, `bateria_agrupada_na_testada`, `abrigo_em_pilar`, `interna_edificacao`,
`nao_localizada`

**`tipo_abrigo`** — `caixa_polimero_com_tampa`, `caixa_metalica`, `caixa_alvenaria`, `nicho_no_muro`,
`sem_abrigo`, `improvisado`, `nao_visivel`

**`estado_abrigo`** — `integro`, `tampa_ausente`, `tampa_quebrada`, `obstruido`, `soterrado`, `nao_visivel`

**`acessibilidade_medicao`** — `livre_via_publica`, `livre_com_abertura_de_tampa`, `atras_de_portao`,
`obstruido_veiculo`, `obstruido_vegetacao`, `interno_requer_morador`, `inacessivel`, `indeterminada`

**`cavalete_material`** — `pvc`, `ferro_galvanizado`, `cobre`, `misto`, `nao_visivel`

**`suspeita_irregularidade_agua`** — `nenhuma_aparente`, `lacre_rompido`, `ligacao_aparente_sem_medidor`,
`derivacao_antes_do_medidor`, `medidor_invertido_aparente`, `abrigo_violado`, `indeterminada`

Suspeita é hipótese de vistoria, nunca conclusão. A anotação registra **o que se vê** ("tubulação de PVC entra
no muro sem passar pelo abrigo"), o `alerta` classifica como `SUSPEITA_IRREGULARIDADE` com severidade `atencao`,
e a decisão sobre fraude é do processo de fiscalização com prova em campo. Nunca escreva "fraude", "gato" ou
"furto" no campo de valor — essas palavras têm consequência jurídica e a foto não as sustenta.

**`leitura_mostrador`** — dígitos do totalizador **somente** quando a imagem é de campo, o mostrador está
enquadrado e todos os dígitos estão nítidos. Qualquer dúvida em um dígito → `null`. Um m³ errado vira conta
errada, reclamação e glosa.

---

## 6. Energia

**`medidores_energia_qtd`** — contagem de medidores individualizados visíveis. É evidência **fortíssima de unidades consumidoras elétricas independentes**, mas não prova direta do número de economias de água. Dois ou mais medidores no mesmo imóvel-alvo geram oportunidade `MULTIPLAS_UCS_MESMO_ENDERECO`; se o cadastro de água tiver menos economias, gerar `DIVERGENCIA_UC_ECONOMIAS`. Conte apenas medidores completos (com mostrador), nunca caixas vazias.

**`tipo_padrao_entrada`** — `caixa_individual_no_muro`, `caixa_em_poste_particular`, `bateria_coletiva`,
`cabine_primaria`, `entrada_subterranea`, `nao_visivel`

**`ramal_entrada`** — `aereo`, `subterraneo`, `nao_visivel`

**`medidor_removido`** — `true` quando há caixa/base com marca de medidor retirado. Indício forte de imóvel
desligado — cruza com `provavelmente_vago` e sustenta hipótese de ligação de água inativa de fato.

---

## 7. Esgoto

O que a fachada permite dizer sobre esgoto é **indireto**. A honestidade aqui vale mais que a completude:
`conectividade_esgoto_inferida` nunca substitui consulta ao cadastro de rede.

**`caixa_inspecao_aparente`** — `sim_na_calcada`, `sim_no_recuo_interno`, `sim_posicao_indefinida`,
`ausente_confirmada`, `nao_observavel`

Como reconhecer: tampa circular ou quadrada (Ø ~30-60 cm) em concreto, ferro fundido ou PVC, rente ao piso
da calçada ou do recuo, geralmente entre a testada e o meio-fio, alinhada com o eixo do banheiro/cozinha.
Não confunda com caixa de passagem elétrica (menor, retangular, com marca da concessionária de energia),
tampa de registro de água (menor, muitas vezes com logo da companhia de saneamento) ou boca de lobo (na sarjeta,
com grelha).

**`indicio_fossa`** — `respiro_aparente`, `tampa_em_area_permeavel_sem_rede`, `sem_indicio`, `nao_observavel`

**`lancamento_sarjeta_aparente`** — `sim_tubulacao_visivel`, `sim_mancha_umidade_permanente`, `nao_aparente`,
`nao_observavel`. Tubo de PVC atravessando a calçada e despejando na sarjeta é evidência forte de esgoto não
conectado à rede — item de alto valor para diagnóstico de cobertura de esgoto e para faturamento.

**`conectividade_esgoto_inferida`** — `provavelmente_conectado`, `provavelmente_nao_conectado`,
`indeterminado`. Sempre `juizo: inferido`, confiança teto 0,6, e a evidência cita quais sinais sustentam.

---

## 8. Entorno

**`pavimentacao_via`** — `asfalto`, `paralelepipedo`, `bloquete`, `concreto`, `terra_cascalho`, `nao_visivel`

**`calcada`** — `pavimentada_regular`, `pavimentada_irregular`, `parcial`, `inexistente`, `nao_visivel`

**`classe_urbana_aparente`** — `urbano_consolidado`, `urbano_periferico`, `expansao_urbana`,
`assentamento_precario`, `industrial_logistico`, `rural`, `indeterminado`

**`declividade_terreno`** — `plano`, `aclive`, `declive`, `acentuado`, `indeterminado`. Relevante para
posição do hidrômetro (em declive acentuado a medição costuma migrar para o alinhamento superior) e para
esforço de percurso do leiturista.

---

## 9. Alertas

| Código | Quando emitir | Severidade típica |
|---|---|---|
| `IMAGEM_INAPTA` | `apta_para_leitura=false` | crítico |
| `ENQUADRAMENTO_PARCIAL` | enquadramento ≠ `fachada_completa` e algum bloco ficou `nao_observavel` por isso | info |
| `DIVERGENCIA_ECONOMIAS` | `economias.divergencia=true` | atenção |
| `NUMERO_DIVERGE_CADASTRO` | `numero_fachada` legível ≠ `vinculo.numero_base` | atenção |
| `SUSPEITA_IMOVEL_INEXISTENTE` | `terreno_sem_edificacao`, `demolido` ou `ruina` com matrícula ativa vinculada | crítico |
| `SUSPEITA_VACANCIA` | `provavelmente_vago`/`vago_evidente`/`abandonado` | atenção |
| `IMPEDIMENTO_LEITURA` | `risco_impedimento_leitura` ≠ `nenhum` | atenção |
| `MEDICAO_NAO_LOCALIZADA` | `hidrometro_presente` = `ausente_confirmado` ou `posicao_medicao` = `nao_localizada` | atenção |
| `USO_DIVERGE_CADASTRO` | uso lido ≠ uso do cadastro (exige `vinculo`) | atenção |
| `SUSPEITA_IRREGULARIDADE` | qualquer suspeita ≠ `nenhuma_aparente` | atenção |
| `IMAGEM_DEFASADA` | `idade_meses > 24` e há campo volátil preenchido | info |
| `DADO_PESSOAL_PRESENTE` | pessoa ou placa de veículo identificável na imagem | info |
| `COLETIVA_DETECTADA` | `economias.estimativa > 1` | info |
| `REVISAO_HUMANA` | `auditoria.confianca_media < 0,5` ou qualquer campo crítico com confiança < 0,4 | atenção |

---

# 10. Comércio temporal — o que é, o que funciona e quando

Este bloco é deliberadamente mais detalhado que uma simples classificação `comercial`. O objetivo é produzir uma evidência que continue interpretável meses ou anos depois.

**`nome_estabelecimento_visivel`** — transcrição literal do nome fantasia/identificação comercial visível. Pessoa física não é transcrita.

**`atividade_letreiro`** — transcrição literal da atividade quando o texto disser o que o local faz: “pizzaria”, “farmácia”, “auto peças”, “salão”, “depósito de bebidas”. Não substitua pelo segmento inferido.

**`descricao_atividade_funcional`** — descrição curta do que aparentemente funciona no endereço, sempre `inferido` e sustentado por `R-USO-05`. Ex.: `mercado de bairro com venda de alimentos e bebidas`, `oficina automotiva com serviços mecânicos`, `salão de beleza/estética`. Evite detalhe que a fachada não sustenta.

**`situacao_estabelecimento_na_data_imagem`** — `em_atividade_aparente`, `fechado_no_momento`, `desativado_aparente`, `em_implantacao`, `em_reforma`, `indeterminado`. O nome do campo é proposital: vale na **data da imagem**.

**`sinais_atividade_comercial`** — lista multivalorada: `letreiro_ativo`, `fachada_identificada`, `vitrine_montada`, `porta_aberta`, `clientes_visiveis`, `produtos_expostos`, `mesas_cadeiras`, `veiculo_operacional`, `horario_funcionamento_visivel`, `iluminacao_interna`, `placa_aberto`, `placa_fechado`, `porta_aco_baixada`, `letreiro_removido`, `fachada_descaracterizada`, `imovel_em_reforma`, `sem_sinal_conclusivo`.

**`validade_temporal_uso`** — `atual_ate_6m`, `recente_7a18m`, `historica_19a36m`, `defasada_acima_36m`, `data_desconhecida`.

Regra interpretativa: uma foto antiga pode identificar perfeitamente **qual comércio existia**, mas não provar que ele **ainda existe**.

# 11. Oportunidades cadastrais

`oportunidades[]` não é uma lista de alterações automáticas. É uma fila de sinais/achados que merecem cruzamento, vistoria ou recadastramento. Todo item tem `codigo`, `classe`, `prioridade`, `confianca`, `evidencia`, `acao_sugerida`, `data_referencia`, `nivel_evidencia`, `fontes_independentes` e `automatizavel=false`.

`nivel_evidencia`:

- `sinal_imagem` — uma fonte visual sustenta a hipótese;
- `achado_convergente` — pelo menos duas fontes independentes apontam o mesmo fato;
- `contradicao` — fontes discordam e o vínculo/decisão deve ser bloqueado para revisão.

Duas pistas dentro da mesma fotografia continuam sendo uma única fonte independente.

Códigos principais:

- `MULTIPLAS_UCS_MESMO_ENDERECO` — dois ou mais medidores elétricos completos no imóvel-alvo.
- `DIVERGENCIA_UC_ECONOMIAS` — múltiplas UCs visíveis e cadastro de água com menos economias.
- `MULTIPLAS_UNIDADES_FISICAS` — acessos/numerações/interfones indicam subdivisão física.
- `USO_COMERCIAL_NAO_CADASTRADO` — imagem sustenta uso comercial e base vinculada indica residencial/outro incompatível.
- `USO_MISTO_POTENCIAL` — comércio + residência coexistem na mesma edificação/testada.
- `ATIVIDADE_COMERCIAL_MUDOU` — comparação temporal mostra troca relevante de atividade, quando houver imagem histórica/base anterior.
- `COMERCIO_APARENTA_DESATIVADO` — havia identificação comercial, mas sinais atuais da imagem indicam desativação; não confundir com fechamento de horário.
- `RECADASTRAMENTO_NUMERACAO` — legado: número/complemento diverge do vínculo cadastral.
- `DIVERGENCIA_NUMERO_ENDERECO` — número/endereço visual contradiz a base; não unir registros até resolver identidade.
- `ECONOMIAS_OCULTAS_POTENCIAL` — sinais de múltiplas unidades confrontados com menor quantidade de economias cadastradas.
- `ESGOTO_SEM_COBRANCA_POTENCIAL` — sinais de atendimento/conexão confrontados com ausência de cobrança; proximidade de rede sozinha não basta.
- `AREA_DIVERGENTE_POTENCIAL` — footprint/área por fonte geométrica adequada diverge da área cadastral.

**Múltiplas UCs não equivalem automaticamente a múltiplas economias de água.** A oportunidade serve justamente para investigar essa diferença sem fabricar conclusão tarifária.

---

# 12. Triagem — conteúdo da imagem e atividade econômica

Bloco obrigatório a partir de `schema_versao 1.4.0`. É o primeiro filtro de custo da campanha: responde, antes
de qualquer leitura fina, **o que a imagem retrata** e **se há atividade econômica — e de quem ela é**.

## `conteudo_imagem`

| Valor | Quando usar | `e_imovel` |
|---|---|---|
| `fachada_imovel` | testada visível e enquadrada, do alinhamento ao telhado ou próximo disso | true |
| `imovel_parcial` | só um pedaço do imóvel (detalhe de muro, meia fachada, canto) | true |
| `terreno_sem_edificacao` | lote vazio identificável como imóvel (com testada, muro, meio-fio) | true |
| `edificacao_em_obra` | estrutura em construção, com ou sem tapume | true |
| `interior_imovel` | ambiente interno, quintal fechado, área de serviço | false |
| `detalhe_medicao` | close de hidrômetro, cavalete, abrigo ou medidor, sem contexto de fachada | false |
| `logradouro_sem_imovel` | rua, calçada, esquina, sem imóvel identificável em foco | false |
| `rede_infraestrutura` | poste, transformador, PV, rede aparente, hidrante | false |
| `documento_ou_tela` | print de sistema, papel, ordem de serviço, foto de monitor | false |
| `mapa_ou_croqui` | recorte de mapa, croqui de rota, imagem de satélite plana | false |
| `veiculo_ou_pessoa` | carro, equipe, selfie, animal — cena sem imóvel | false |
| `area_rural_vegetacao` | campo, mata, lavoura, sem edificação identificável | false |
| `ininteligivel` | escura, borrada, dedo na lente, totalmente obstruída (exige `apta_para_leitura=false`) | false |

`detalhe_medicao` merece atenção: **não é apta para cadastro, mas é valiosa** — marque
`apto_para_cadastro=false` com `uso_parcial_permitido=medicao`. É a foto que resolve leitura de mostrador,
estado do abrigo e acessibilidade, e descartá-la por não ser fachada joga fora informação boa.

## `atividade_economica_aparente`

- `nenhuma_aparente` — testada sem letreiro, toldo, vitrine, mercadoria, placa ou movimento comercial. Exige
  `sinais_atividade_economica = nenhum`.
- `sinal_fraco` — um único indício ambíguo (placa pequena sem ramo legível, portão largo tipo depósito, veículo
  de serviço estacionado que pode ser do morador).
- `sinal_forte` — dois ou mais indícios convergentes, ou um inequívoco (letreiro com ramo legível, vitrine com
  mercadoria, fachada padronizada de rede).
- `indeterminado` — a fonte não permite decidir (aérea vertical, contraluz, distância).

## `sinais_atividade_economica` (lista `;`)

`letreiro_fachada`, `toldo_comercial`, `vitrine`, `porta_de_aco_comercial`, `mercadoria_exposta`,
`veiculo_servico_identificado`, `placa_profissional_liberal`, `banner_faixa`, `menu_cardapio`,
`estacionamento_clientes`, `galpao_carga_doca`, `equipamento_produtivo`, `fachada_padronizada_franquia`,
`horario_funcionamento`, `movimento_clientes`, `caixa_dagua_ou_reservatorio_produtivo`,
`antena_ou_camera_comercial`, `nenhum`

## `atividade_no_alvo` — o campo que evita o achado falso

`no_imovel_alvo`, `em_vizinho`, `ambulante_via_publica`, `indeterminado`.

Em rua de comércio denso a foto do alvo quase sempre contém o letreiro do vizinho. Marcar `em_vizinho` custa um
segundo e impede uma revisão tarifária indevida — que custa reclamação, ressarcimento e credibilidade da fila.
Quando o alvo tem atividade própria **e** o vizinho também, o valor é `no_imovel_alvo` (o que importa é o alvo);
descreva o vizinho na evidência.

## `natureza_atividade`

`comercio`, `servico`, `industria`, `agropecuaria`, `institucional`, `religiosa`, `educacional`, `saude`,
`hospedagem`, `logistica_deposito`, `misto`, `indeterminada`

## `formalidade_aparente`

- `estabelecimento_formal_aparente` — fachada dedicada ao negócio: vitrine, letreiro fixo, porta comercial.
- `atividade_domiciliar` — **negócio dentro de residência**: placa de salão/manicure/costura no muro, oficina no
  quintal, mercadinho na garagem, escritório de profissional liberal com placa de acrílico. A edificação segue
  residencial; a atividade não. É a fila `ATIVIDADE_DOMICILIAR_POTENCIAL`, para cruzar com CNPJ no endereço e
  categoria cadastral — nunca reclassificação automática.
- `ambulante_informal` — banca, carrinho, trailer, tenda na via pública. Quase sempre `ambulante_via_publica`
  no `atividade_no_alvo`, e portanto não altera o imóvel.
- `indeterminada`

## Alertas do bloco

| Código | Quando | Severidade |
|---|---|---|
| `IMAGEM_FORA_DE_ESCOPO` | `e_imovel=false` | atenção |
| `ATIVIDADE_ECONOMICA_APARENTE` | `sinal_fraco` ou `sinal_forte` | info |
| `ATIVIDADE_DOMICILIAR_APARENTE` | `formalidade_aparente=atividade_domiciliar` | atenção |
| `ATIVIDADE_DE_VIZINHO` | `atividade_no_alvo=em_vizinho` | atenção |
