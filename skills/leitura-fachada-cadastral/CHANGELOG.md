# Changelog

## 1.5.0 — 2026-08-09

Dois blocos novos, na direção do salto de "ler uma fachada" para "entender o território": **cadastro vertical**
(reconstruir a estrutura de unidades de prédios, vilas e condomínios) e **atributos cartografáveis** (o insumo
do mapa, que esta skill produz e o módulo `pgv-mapeamento-cadastral` consome). Aditivo; a 1.4.0 segue válida.

- novo bloco `estrutura_imovel`, obrigatório em `schema_versao 1.5.0`: tipologia com 14 valores
  (`casa_isolada` … `condominio_vertical_multitorre`, `vila_corredor`, `galeria_comercial`,
  `lote_multiplas_edificacoes`), torres, pavimentos residenciais/comerciais, prumadas, unidades por pavimento,
  identificadores de interfone/caixa de correio, nome do edifício, blocos, composição de uso por pavimento,
  acessos residenciais/comerciais, entradas de garagem e três scores estruturais;
- **separação dura das quatro medidas** (`R-EST-01`): unidades físicas ≠ UCs elétricas ≠ economias de água ≠
  hidrômetros. O produto é a divergência entre elas, nunca a fusão;
- contagem no prumo (`R-EST-02`) com conferência aritmética `torres × pavimentos × unidades/pavimento`
  e folga de 25%; interfone/caixa de correio como melhor evidência vertical (`R-EST-03`);
- `R-EST-04`: no vertical, porta de testada conta acesso ao edifício, não unidade — evita subcontagem;
- `R-EST-05` vila e condomínio horizontal com o mesmo peso do prédio; `R-EST-06` multitorre;
  `R-EST-07` verticalização como mudança estrutural; `R-EST-08` composição de uso por pavimento;
- novo bloco `mapeamento` com separação explícita entre campos de **origem-imagem** e de **origem-territorial**;
  `C-27` impede a skill de fachada de preencher `face_id`, `quadra_id`, `logradouro_id` e `cluster`;
- `status_endereco` com 8 estados; número por interpolação da face como **hipótese** com teto 0,60 (`R-MAP-03`);
- `valor_esperado_anual = probabilidade × impacto` conferido pelo validador (`C-26`), e
  `gap_uc_economias = ucs - economias_base` conferido aritmeticamente;
- `vigencia_evidencia` derivada e conferida contra `idade_meses` (`R-MAP-05`);
- gates `C-21` (contagem sem método), `C-22` (vertical sem dimensionamento), `C-23` (divergência sem
  oportunidade), `C-24` (status de endereço), `C-25` (mudança de uso), `C-26`, `C-27`;
- novos códigos: `PREDIO_MULTIPLAS_UNIDADES_CADASTRO_UNITARIO`, `VERTICALIZACAO_NAO_CADASTRADA`,
  `CONDOMINIO_HORIZONTAL_CADASTRO_UNITARIO`, `GALERIA_COMERCIAL_SUBDIVIDIDA`, e os alertas
  `ESTRUTURA_VERTICAL_DETECTADA`, `DIVERGENCIA_UNIDADES_ECONOMIAS`, `MUDANCA_ESTRUTURAL_FORTE`;
- consolidador com 57 colunas novas (`estrutura_*`, `mapeamento_*`) e dicionário de dados atualizado;
- fixtures de edifício misto com cadastro unitário (9 unidades × 1 economia) e de erro estrutural; selftest verde.

## 1.4.0 — 2026-08-09

Passe de **triagem** no topo do protocolo: antes de ler o imóvel, dizer se a imagem é de imóvel e se há
atividade econômica — e de quem ela é. Mudança aditiva; nada da 1.3.0 foi removido.

- novo bloco `triagem` no schema, obrigatório a partir de `schema_versao 1.4.0` e apenas avisado em anotações
  legadas 1.2.0 — migração sem quebrar base já anotada;
- `conteudo_imagem` com 14 valores (fachada, parcial, terreno, obra, interior, detalhe de medição, logradouro,
  rede, documento/tela, mapa, veículo/pessoa, rural, ininteligível, outro) + `e_imovel` e `apto_para_cadastro`;
- `detalhe_medicao` e `interior_imovel` deixam de ser descarte cego: `apto_para_cadastro=false` com
  `uso_parcial_permitido` declarando o que ainda se aproveita;
- `atividade_economica_aparente` (nenhuma/fraco/forte/indeterminado) com `sinais_atividade_economica`
  em vocabulário fechado de 18 sinais físicos;
- **`atividade_no_alvo`** (alvo / vizinho / ambulante / indeterminado) — gate anti-contaminação: o letreiro do
  vizinho deixa de virar categoria divergente do alvo;
- **`formalidade_aparente=atividade_domiciliar`** e oportunidade `ATIVIDADE_DOMICILIAR_POTENCIAL` — negócio
  dentro de residência vira fila de revisão de categoria, nunca reclassificação automática;
- gates novos no validador: `C-16` (imagem sem imóvel não produz atributo nem oportunidade), `C-17` (sinal forte
  no alvo tem que virar uso ou oportunidade), `C-18` (atividade exige sinal listado e dono identificado),
  `C-19` (vizinho/ambulante não sustenta uso econômico do alvo), `C-20` (ininteligível exige imagem inapta);
- tetos de fonte para os campos de triagem, com atividade econômica marcada como volátil (comércio abre e fecha):
  campo 0,92 · Street View 0,80 · drone 0,35, com decaimento temporal;
- alertas `IMAGEM_FORA_DE_ESCOPO`, `ATIVIDADE_ECONOMICA_APARENTE`, `ATIVIDADE_DOMICILIAR_APARENTE`,
  `ATIVIDADE_DE_VIZINHO`;
- consolidador ganha 12 colunas `triagem_*` logo após os metadados, e o dicionário de dados documenta o bloco;
- aviso de passes deixa de cobrar o protocolo completo de imagem barrada na triagem;
- `references/taxonomia.md` §12 e `references/derivacao.md` `R-TRI-01..07`;
- 14 fixtures (4 novas: imagem que não é imóvel, atividade domiciliar, contaminação por vizinho e anotação 1.4.0
  sem triagem); selftest verde.

## 1.3.0 — 2026-08-09

Fusão da linha 1.2.0 (doutrina PGV: sinal/achado/contradição, identidade, comércio temporal, UC ≠ economia)
com o que a linha 1.0.0 trazia de operacional e havia ficado de fora do SKILL.md. Assets, schema, validador,
consolidador e painel da 1.2.0 permanecem intactos — a mudança é aditiva.

- `description` volta a carregar gatilhos explícitos de acionamento (foto, print, Street View, drone, vistoria
  virtual, recadastramento por imagem, hidrômetro, ligação fantasma, impedimento de leitura) fundidos ao
  vocabulário PGV; sem eles a skill deixava de disparar em pedidos que são exatamente o seu caso de uso;
- seção **Antes de começar**: alinhamento curto de fonte/data, origem do vínculo e teses de interesse;
- seção **Leia primeiro**: ponteiros de progressive disclosure para `references/` e `assets/`;
- **gate de inaptidão** explícito no Passe 0 (`apta_para_leitura=false` → `IMAGEM_INAPTA` e para);
- seção **LGPD** completa (dado do imóvel vs dado da pessoa, blur, caixa de correio: número entra, nome não);
- seção **Lote de imagens e conciliação**, com funil de campanha (recebidas = anotadas + inaptas + ilegíveis);
- **novo `scripts/conciliar_multifoto.py`**: N imagens do mesmo imóvel → 1 registro, campo a campo pelo maior
  teto efetivo de fonte, reusando `teto_efetivo` do validador. Separa `contradicao` (campo estável divergente)
  de `divergencia_temporal` (campo volátil em datas diferentes) e marca elegibilidade de convergência
  fachada+aérea **sem promover** `sinal_imagem` por conta própria (flag `--elevar-convergencia`, default off);
- regras `R-CON-01..05` documentadas em `references/derivacao.md`;
- tabela de **cruzamento com o restante da linha A2L** (ligacao-fantasma, fora-de-rota-pdca,
  construcao-vias-publicas, tratamento-coletivas-cadastro) e regra de priorização da fila por impacto;
- documentação do layout do consolidado (`_conf`/`_juizo`/`_regra`) e do filtro SQL a jusante;
- checklist de autoteste de 12 itens antes da entrega;
- selftest passa a ter 5 etapas, com `tests/fixtures_conciliacao/` (mesma matrícula em campo + Street View
  antigo + drone) cobrindo temporalidade, contradição, complementaridade e não-promoção.

## 1.2.0 — 2026-08-09

- alinhamento explícito às quatro teses do PGV Cadastral: categoria x uso real, economias ocultas, esgoto sem cobrança e área/ocupação divergente;
- nova regra-mãe `sinal_imagem` x `achado_convergente` x `contradicao`: uma fonte isolada não vira achado;
- leitura de número/endereço em fachada, porta, portão, caixa de correio e placa comercial, com origem física auditável;
- caixa de correio passa a servir tanto como evidência de endereço quanto como indício de múltiplas unidades quando individualizada;
- gate `R-END-06`: número visual divergente bloqueia vínculo por endereço e não pode ser compensado por proximidade espacial;
- proteção contra falso OCR de endereço: serial de medidor, telefone, CNPJ, preço, horário, poste e placa veicular não contam como número da casa;
- `ECONOMIAS_OCULTAS_POTENCIAL`, `DIVERGENCIA_NUMERO_ENDERECO`, `ESGOTO_SEM_COBRANCA_POTENCIAL` e `AREA_DIVERGENTE_POTENCIAL`;
- fontes independentes registradas em cada oportunidade; duas pistas na mesma foto continuam sendo uma fonte;
- suporte a `area_footprint_estimada_m2` e `area_construida_estimativa_m2` para fonte aérea/drone; fachada frontal isolada não pode inventar área;
- consolidador passa a contar sinais, achados convergentes e contradições;
- novos testes de número em caixa de correio, economias ocultas, achado indevido com fonte única e divergência de número/vínculo.

## 1.1.0 — 2026-08-09

- validação real do `schema_fachada.json` com JSON Schema Draft 2020-12;
- correção dos tipos `campo_inteiro`, `campo_numero` e `campo_booleano`;
- comércio temporal: nome, atividade, descrição funcional, situação na data da imagem e validade temporal;
- múltiplas UCs no mesmo endereço como oportunidade cadastral de primeira classe;
- separação explícita entre UC elétrica e economia de água;
- vínculo ao imóvel e ROI de fachada disponíveis para reduzir contaminação por vizinhos;
- `oportunidades[]` com ação sugerida e `automatizavel=false`;
- auditoria declarada conferida contra métricas recalculadas;
- consolidador e painel ampliados para comércio e oportunidades;
- novos fixtures para schema vazio, comércio sem data e múltiplas UCs.
