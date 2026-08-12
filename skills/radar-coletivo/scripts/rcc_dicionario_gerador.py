#!/usr/bin/env python3
"""Gera o DICIONÁRIO canônico do RCC e a base de exemplo — fonte única.

O dicionário não é documentação escrita à mão: é a MESMA estrutura que gera a
ordem das colunas, os tipos e os domínios da base. Documento e base não podem
divergir porque saem do mesmo lugar.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _dominios_da_skill():
    """Domínios que a NORMALIZAÇÃO produz vêm do LAYER_MAP, não de lista
    digitada aqui. Foram três reprovações em dado real (BAIXA na qualidade,
    ANDAR/DIR na posição, ARMAZEM/BANCA/BARRACA no tipo de unidade) até ficar
    claro que domínio adivinhado é dívida com juros: a fixture sintética nunca
    mostra o vocabulário inteiro."""
    try:
        import radar_utils as xu
    except Exception:
        return {}
    cam = {}
    for k, v in xu.LAYER_MAP.items():
        c, t = (v if isinstance(v, (list, tuple)) else (v, k))
        cam.setdefault(c, set()).add(t)
    und = sorted(cam.get('UNIDADE', set()) | cam.get('MORADIA', set())) + ['OUTRO']
    pos = sorted(cam.get('POSICAO', set()) | cam.get('PAVIMENTO', set()))
    return {'UND_TIPO': ' | '.join(und), 'UND_POSICAO': ' | '.join(pos)}


DOM_SKILL = _dominios_da_skill()

# ── BLOCOS (o prefixo numérico dá ordem determinística e legível) ───────────
BLOCOS = [
    ('00', 'IDENT', 'Identificação — chaves e proveniência'),
    ('10', 'END', 'Endereço canônico e original'),
    ('20', 'GEO', 'Geometria'),
    ('25', 'SET', 'Contexto do setor censitário — ocupação de domicílios'),
    ('30', 'COL', 'Classificação da coletividade'),
    ('40', 'UND', 'Atributos da unidade'),
    ('45', 'ATV', 'Atividade econômica — o que funciona no local'),
    ('50', 'EVD', 'Evidência — o porquê da linha'),
    ('60', 'CNF', 'Confiabilidade'),
    ('70', 'CAD', 'Confronto com o cadastro'),
    ('80', 'ACT', 'Ação recomendada'),
    ('90', 'AUD', 'Auditoria e linhagem'),
]

# campo, tipo, obrigatorio, dominio, estabilidade, descricao
CAMPOS = {
'IDENT': [
 ('IMOVEL_ID','texto','condicional','IMV-<9 dig>','PERSISTENTE',
  'Imóvel físico. Único id que sobrevive a mudança de nome de rua e casa fontes sem chave comum. Vem do store append-only da resolução de entidade. Vazio enquanto a resolução não rodar.'),
 ('COLETIVA_ID','texto','condicional','COL-<mun 7>-<seq 6>','ESTAVEL_POR_SAFRA',
  'Grupo coletivo. Surrogate legível emitido por store; ordenável e citável em campo. VAZIO quando o registro não tem identidade de endereço (número ausente): a unidade existe, o agrupamento não.'),
 ('COLETIVA_CHAVE_HASH','texto','condicional','inteiro 19 díg (texto)','DETERMINISTICO',
  'Hash BLAKE2b(8)>>1 da CHAVE CANÔNICA publicada ao lado. Reproduzível por terceiros sem acesso ao pipeline; é a prova de que COLETIVA_ID aponta para o endereço que diz.'),
 ('COLETIVA_CHAVE_CANONICA','texto','condicional','<mun 7>|<TIPO TITULO NOME>|N<numero>|<localidade>','DETERMINISTICO',
  'A PRÉ-IMAGEM do hash, publicada. Sem ela o id não seria reproduzível por terceiros, porque a canônica usa o nome HARMONIZADO e o numeral expandido. Desde a CANON_VERSAO 3 leva TIPO + TÍTULO + NOME: sem o título, RUA BARÃO DO GRAVATAÍ e RUA BARONESA DO GRAVATAÍ — duas ruas de Porto Alegre — recebiam o MESMO identificador. Identificador cuja pré-imagem não viaja com o dado é opaco, não determinístico.'),
 ('BLOCO_ID','texto','condicional','<COLETIVA_ID>-B<valor>','ESTAVEL_POR_SAFRA',
  'Bloco/torre dentro da coletiva. Vazio quando a coletiva não tem subdivisão por bloco.'),
 ('UNIDADE_ID','texto','sim','<BLOCO_ID|COLETIVA_ID>-<TIPO 3>-<valor 4>','ESTAVEL_POR_SAFRA',
  'Unidade/economia. GRÃO DA TABELA: uma linha, um UNIDADE_ID por sistema de origem. Composto e legível: COL-4300604-000123-B-APT-0203.'),
 ('ORIGEM_SISTEMA','texto','sim','IBGE | CADASTRO','n/a',
  'Sistema que produziu a linha. IBGE é a fonte dos dados TRATADOS; CADASTRO entra só como confronto. Duas linhas com o mesmo UNIDADE_ID e ORIGEM_SISTEMA distinto são a MESMA unidade vista pelos dois lados.'),
 ('ORIGEM_REGISTRO_ID','texto','sim','livre (cru da fonte)','n/a',
  'Id do registro no sistema de origem, preservado sem renomear. É o caminho de volta ao artefato que gerou a linha.'),
 ('ORIGEM_GRUPO_ID','texto','condicional','livre (cru da fonte)','n/a',
  'Id do agrupamento no sistema de origem (RADAR_ID_BLOCO, COD_COLETIVA, CLUSTER_ID). Preservado cru — nunca decisório, sempre rastreável.'),
 ('ORIGEM_LIGACAO','texto','condicional','matrícula da concessionária','n/a',
  'Matrícula quando existir. Chave de junção histórica entre as skills; aqui é atributo, não identidade.'),
 ('EXEC_ID','texto','sim','RUN-<AAAAMMDD>-<6 hex>','POR_EXECUCAO',
  'Execução que emitiu a linha. Permite diff entre rodadas sem depender de data de arquivo.'),
],
'END': [
 ('END_MUNICIPIO','texto','sim','código IBGE 7 díg','n/a','Município. Prefixo de toda chave — sem ele endereços homônimos de cidades distintas colidem.'),
 ('END_LOGRADOURO','texto','sim','livre normalizado','n/a','Logradouro canônico: tipo + título + nome, ASCII maiúsculo, espaço colapsado, abreviação e numeral expandidos.'),
 ('END_LOGRADOURO_ORIG','texto','sim','livre cru','n/a','Logradouro como veio da fonte. Par de auditoria — permite conferir a normalização sem abrir o pipeline.'),
 ('END_LOGR_EQUIV_SUGERIDA','texto','nao','livre','n/a','Outro logradouro que provavelmente é ESTE, escrito de outro jeito. MARCAÇÃO, nunca aplicação: a fonética acerta a grafia (AYRTON SENNA≡AIRTON SENA) e erra o ordinal (fundiria CEFER I com CEFER II), então ela gera o candidato e a geometria confirma. Vazio = nenhum candidato.'),
 ('END_LOGR_EQUIV_GRAU','texto','nao','SUSTENTADA | PLAUSIVEL | ESPECULATIVA','n/a','Grau da equivalência, na mesma régua R9 das hipóteses de unidade. SUSTENTADA exige as DUAS provas independentes: mesma coordenada (≤50 m) E número em comum. Só grafia nunca sustenta.'),
 ('END_LOGR_EQUIV_EVIDENCIA','texto','nao','livre','n/a','O que sustenta o grau, em texto: distância entre centróides e quantidade de números em comum. Sem isto o grau é uma opinião com nome técnico.'),
 ('END_NUMERO','inteiro','sim','> 0','n/a','Número predial. Zero não existe para cruzamento (R1); linha sem número usa chave de quadra/lote.'),
 ('END_NUMERO_ORIG','texto','sim','livre cru','n/a','Número como veio: S/N, KM 12, 0, com letra.'),
 ('END_QUADRA','texto','condicional','livre','n/a','Quadra, quando o endereçamento for por quadra/lote.'),
 ('END_LOTE','texto','condicional','livre','n/a','Lote, idem.'),
 ('END_COMPLEMENTO','texto','condicional','rótulos canônicos separados por ·','n/a','Complemento normalizado nas 7 camadas, em ordem canônica.'),
 ('END_COMPLEMENTO_ORIG','texto','condicional','livre cru','n/a','Complemento cru. É onde mais se erra no parsing — o par é obrigatório.'),
 ('END_LOCALIDADE','texto','sim','livre normalizado','n/a','Bairro/localidade, ASCII maiúsculo, espaço colapsado.'),
 ('END_CEP','texto','condicional','8 díg (texto)','n/a','CEP. Descritivo apenas: nunca entra em chave nem penaliza confiabilidade quando a coordenada confirma (R7).'),
],
'GEO': [
 ('GEO_LAT_ORIG','decimal(9,6)','condicional','-34..6','n/a','Latitude COMO VEIO da fonte, intocada. Nunca é sobrescrita: o tratamento propõe ao lado, jamais por cima.'),
 ('GEO_LON_ORIG','decimal(9,6)','condicional','-74..-32','n/a','Longitude como veio da fonte.'),
 ('GEO_LAT','decimal(9,6)','condicional','-34..6','n/a','Latitude EM USO. Igual à original quando não houve ajuste. Vazia em unidade inferida — coordenada nunca é fabricada.'),
 ('GEO_LON','decimal(9,6)','condicional','-74..-32','n/a','Longitude em uso.'),
 ('GEO_AJUSTE_FONTE','texto','condicional','SEM_AJUSTE | MINERACAO_HISTORICO | VIZINHO_CONFIRMADO | REGUA_LOGRADOURO | CENTROIDE_BLOCO | CAMPO','n/a','O que produziu a coordenada em uso. SEM_AJUSTE significa que a original foi mantida — e é o valor esperado na maioria das linhas.'),
 ('GEO_DESLOCAMENTO_M','decimal(8,1)','condicional','>= 0','n/a','Distância em metros entre a coordenada original e a em uso (haversine). Zero quando não houve ajuste. É a medida auditável do quanto o tratamento moveu o ponto — número grande com evidência fraca é alvo de revisão.'),
 ('GEO_ACURACIA_M','decimal(8,1)','condicional','>= 0','n/a','Incerteza posicional declarada da coordenada em uso, em metros. Sem isto o ajuste vira opinião: mover 30 m com acurácia de 50 m não é correção.'),
 ('GEO_NIVEL','inteiro','condicional','1..6','n/a','Nível de precisão declarado pela fonte para a coordenada ORIGINAL.'),
 ('GEO_QUALIDADE','texto','sim','VALIDADA | ESTIMADA | BAIXA | HERDADA_CENTROIDE | AUSENTE','n/a','Qualidade da coordenada EM USO. BAIXA = a coordenada existe mas o nível de precisão é ruim (não confundir com ESTIMADA, que é derivada). HERDADA_CENTROIDE marca posição emprestada do grupo — nunca confundir com medição.'),
 ('GEO_RAIO_GRUPO_M','decimal(8,1)','condicional','>= 0','n/a','Raio máximo do grupo ao centróide, em metros. Grupo espalhado denuncia unificação indevida.'),
],
'SET': [
 ('SET_COD','texto','sim','15 dígitos','ESTAVEL_POR_SAFRA','Setor censitário do endereço, 15 dígitos. O CNEFE traz 16 com sufixo; a junção trunca — nunca o inverso, porque o sufixo não é constante entre UFs.'),
 ('SET_DOM_PARTICULARES','inteiro','condicional','>= 0','n/a','Domicílios particulares do setor (DPPO+DPPV+DPPUO+DPIO). Fecha exatamente com a contagem de COD_ESPECIE=1 do CNEFE no mesmo setor — é essa identidade que prova que o desocupado está no arquivo de endereços, apenas não identificado.'),
 ('SET_DOM_OCUPADOS','inteiro','condicional','>= 0','n/a','Domicílios particulares ocupados no setor.'),
 ('SET_DOM_USO_OCASIONAL','inteiro','condicional','>= 0','n/a','Domicílios particulares permanentes de uso ocasional.'),
 ('SET_DOM_VAGOS','inteiro','condicional','>= 0','n/a','Domicílios particulares permanentes vagos.'),
 ('SET_DOM_SEM_OCUPACAO_HABITUAL','inteiro','condicional','>= 0','n/a','Vagos MAIS uso ocasional (V0008+V0009), somados porque nenhum dos dois tem morador habitual. NAO se chama DESOCUPADOS: vago e uso ocasional sao categorias distintas do IBGE, e o rotulo antigo fazia ler 30%% de uso ocasional como 30%% de imovel vago. As parcelas continuam publicadas separadas em SET_DOM_VAGOS e SET_DOM_USO_OCASIONAL.'),
 ('SET_TX_SEM_OCUPACAO_HABITUAL','decimal(6,4)','condicional','0..1','n/a','Domicilios sem ocupacao habitual sobre particulares. ATRIBUTO DO SETOR, jamais da unidade: multiplicá-la pelas unidades da coletiva para estimar quantas estão vagas produz número que parece dado e é palpite. Gate G19 é executável.'),
 ('SET_CLASSE_SEM_OCUPACAO_HABITUAL','texto','condicional','BAIXA | MEDIA | ALTA | MUITO_ALTA | INSUFICIENTE','n/a','Faixa da taxa: BAIXA <10%, MEDIA <25%, ALTA <40%, MUITO_ALTA >=40%. INSUFICIENTE abaixo de 20 domicílios no setor, onde a taxa é ruído amostral e não classifica.'),
],

'COL': [
 ('COL_FORMA','texto','sim','VERTICAL | VERTICAL_MULTIBLOCO | HORIZONTAL | HORIZONTAL_MULTIBLOCO | LOTEAMENTO | COMERCIAL | SIMPLES | INDEFINIDA','n/a','Eixo estrutural reconciliado. Domínio único para todas as fontes.'),
 ('COL_FORMA_ORIG','texto','sim','livre cru','n/a','Rótulo de tipologia como a fonte emitiu, antes do DE-PARA.'),
 ('COL_USO','texto','sim','RESIDENCIAL | COMERCIAL | MISTO | INDEFINIDO','n/a','Uso predominante do grupo.'),
 ('COL_CLASSE','texto','sim','GRANDE | PEQUENA | SIMPLES','n/a','Porte. Só porte: PAR_FALTANTE virou UND_NATUREZA, GENERICA virou COL_FORMA=INDEFINIDA.'),
 ('COL_CRITERIO_UNIFICACAO','texto','sim','NUMERO_QUADRA_LOTE | NUMERO | QUADRA_LOTE | PARCIAL','n/a','O que uniu os membros do grupo. PARCIAL é valor real e frequente — grupo sem âncora forte.'),
 ('COL_VEREDITO','texto','sim','CONCLUSIVO | FORTE | MODERADO | FRAGIL | INSUFICIENTE','n/a','Força da conclusão sobre a coletividade.'),
 ('COL_QTD_OBSERVADA','inteiro','sim','>= 1','n/a','Unidades OBSERVADAS no grupo. Nunca soma inferidas.'),
 ('COL_QTD_INFERIDA','inteiro','sim','>= 0','n/a','Unidades INFERIDAS no grupo, em coluna separada por doutrina.'),
 ('COL_QTD_BLOCOS','inteiro','sim','>= 0','n/a','Blocos/torres distintos.'),
 ('COL_QTD_UNID_ATIVIDADE','inteiro','sim','>= 0','n/a','Unidades do grupo com atividade econômica observada. Zero em coletiva puramente residencial.'),
 ('COL_FLAG_USO_MISTO','inteiro','sim','0 | 1','n/a','Residencial e não-residencial no mesmo grupo. Muda a abordagem de campo e a tarifa.'),
 ('COL_POLO_CLASSE','texto','condicional','ZONA_CENTRAL | POLO_REGIONAL | POLO_LOCAL | FORA_DE_POLO','n/a','Aglomeração comercial (densidade de endereços) a que o grupo pertence. Contexto de vizinhança: loja em zona central não é loja isolada.'),
],
'UND': [
 ('UND_NATUREZA','texto','sim','OBSERVADO | INFERIDO','n/a','Fato x hipótese. Nenhuma agregação mistura os dois; toda leitura abre em OBSERVADO.'),
 ('UND_BLOCO','texto','condicional','livre','n/a','Bloco/torre da unidade.'),
 ('UND_TIPO','texto','sim','APARTAMENTO | CASA | SALA | LOJA | BOX | LOTE | SOBRADO | QUITINETE | OUTRO','n/a','Tipo da unidade, domínio fechado.'),
 ('UND_VALOR','texto','condicional','livre','n/a','Identificador da unidade dentro do grupo (101, 3, A).'),
 ('UND_POSICAO','texto','condicional','FRENTE | FUNDOS | LADO | ESQ | DIR | MEIO | LATERAL | ANEXO | DEPENDENCIA | PORAO | TERREO | ANDAR | PAVIMENTO | SOBRELOJA | SUBSOLO | COBERTURA | LAJE','n/a','Posição ou pavimento, quando é ela que distingue a economia. Domínio espelha as camadas POSICAO e PAVIMENTO da normalização de complemento — valor novo lá exige entrada aqui, por isso o gate.'),
 ('UND_ECONOMIAS','inteiro','sim','>= 1','n/a','Economias representadas pela linha. Somável — é a grandeza que agrega.'),
],
'ATV': [
 ('ATV_PRESENTE','texto','sim','SIM | NAO | INDETERMINADO','n/a','Há atividade econômica observada NESTA unidade. INDETERMINADO é resposta legítima: a fonte não classificou, e isso não é o mesmo que ausência.'),
 ('ATV_NOME','texto','condicional','livre normalizado','n/a','O que funciona no local — nome do estabelecimento como o recenseador anotou, normalizado. É a resposta direta a "o que tem ali".'),
 ('ATV_NOME_ORIG','texto','condicional','livre cru','n/a','Nome exatamente como veio no arquivo. Par de auditoria: texto de recenseamento vem abreviado e inconsistente.'),
 ('ATV_SETOR','texto','condicional','ALIMENTACAO | COMERCIO_VAREJO | COMERCIO_SERVICO | SERVICO_PROF | SERVICO_AUTO | BELEZA_ESTETICA | SAUDE | EDUCACAO | HOSPEDAGEM | LAZER | ASSOCIACAO_ONG | INDUSTRIAL | MANUFATURA | RELIGIOSO | AGROPECUARIO | DOMICILIO_COLETIVO | CONSTRUCAO | RESIDENCIAL | VAGO | DIVERSOS | NAO_CLASSIFICADO','n/a','Setor da atividade. Vem da espécie oficial; só a espécie 6 (outras finalidades) passa pelo léxico sobre o nome anotado. RESIDENCIAL é valor legítimo: domicílio com nome de estabelecimento anotado é negócio em casa, e o recenseador viu.'),
 ('ATV_DETALHE','texto','condicional','livre','n/a','Trecho do nome anotado que sustentou a classificação de setor. Sem isto o léxico é caixa-preta.'),
 ('ATV_ESPECIE_COD','inteiro','condicional','1..8','n/a','Espécie do endereço no layout oficial, preservada crua. É o que decide o macro-uso antes de qualquer léxico.'),
 ('ATV_QTD_ESTABELECIMENTOS','inteiro','condicional','>= 0','n/a','Estabelecimentos CONTADOS no endereço (espécies 3,4,5,6,8). Contagem estrutural, não declaração.'),
 ('ATV_DECLARADA_FAIXA','texto','condicional','UNICO | MULTIPLO_ATE_10 | MULTIPLO_11_MAIS | MULTIPLO_QTD_DESCONHECIDA | SEM_DECLARACAO','n/a','Multiplicidade DECLARADA pelo recenseador, decodificada do indicador oficial. Domínio em CÓDIGO, não em rótulo de exibição: rótulo muda com a redação, código é contrato. É percepção de quem esteve no local.'),
 ('ATV_VALIDACAO_DECLARADA','texto','condicional','CONFIRMADO | DUVIDA_SUB_ENUMERACAO | DUVIDA_SUPER_CONTAGEM | SEM_DECLARACAO','n/a','Confronto entre a faixa declarada e a contagem estrutural. DUVIDA_SUB_ENUMERACAO = o recenseador viu mais do que a base registrou: alvo de verificação, nunca descarte. A estrutura decide, a declaração valida.'),
 ('ATV_EVIDENCIA_CAMPO','texto','condicional','DSC_ESTABELECIMENTO | COD_ESPECIE | COD_INDICADOR_ESTAB_ENDERECO | MULTIPLOS_CAMPOS | NENHUM','n/a','QUAL campo do layout oficial sustenta a afirmação de atividade. Nomear o campo é o que impede a coluna de virar opinião: um auditor abre o arquivo bruto e confere.'),
 ('ATV_GRAO_EVIDENCIA','texto','condicional','UNIDADE | ENDERECO','n/a','ATÉ ONDE a evidência resolve. UNIDADE exige que o registro traga complemento estruturado nos campos NOM_COMP/VAL_COMP; sem eles a atividade é do ENDERECO e atribuí-la a uma unidade seria inferência disfarçada de fato.'),
 ('ATV_INCERTEZA_M','decimal(8,1)','condicional','> 0','n/a','Incerteza posicional da evidência, em metros, derivada do nível de precisão da coordenada oficial. Nunca zero.'),
 ('ATV_ROTA_TRATAMENTO','texto','condicional','RECLASSIFICACAO_1_1 | INDIVIDUALIZACAO_MULTI | VERIFICAR_CAMPO | SEM_ROTA','n/a','Rota decidida pelo indicador oficial de multiplicidade, nunca por contagem bruta: torre com uma loja no térreo tem indicador UNICO e é reclassificação direta, não individualização.'),
 ('ATV_FLAG_FACHADA_ATIVA','inteiro','condicional','0 | 1','n/a','Estabelecimento em pavimento térreo com frente para a via, pelo complemento normalizado. Marca o imóvel que opera de portas abertas.'),
 ('ATV_FLAG_GAP_TARIFARIO','inteiro','condicional','0 | 1','n/a','Atividade econômica observada em ligação com perfil RESIDENCIAL no cadastro. Exige confronto com o cadastro da concessionária — sem ele fica 0, nunca 1.'),
],
'EVD': [
 ('EVD_REGRA','texto','sim','identificador de regra','n/a','Regra que produziu a linha (gap_grade, T1.complemento_estruturado). Sem isso não há auditoria, só resultado.'),
 ('EVD_DESCRICAO','texto','sim','livre','n/a','A evidência em texto legível, suficiente para um terceiro julgar sem rodar nada.'),
 ('EVD_PRESENTES','texto','condicional','livre','n/a','Conjunto observado que sustentou a inferência.'),
 ('EVD_GRAU','texto','condicional','SUSTENTADA | PLAUSIVEL | ESPECULATIVA','n/a','R9 — grau da hipótese por UM critério: por quantos lados a observação a cerca. SUSTENTADA nos dois lados, PLAUSIVEL de um lado e perto, ESPECULATIVA extrapolação livre. Obrigatório em toda linha INFERIDO (G16). Nenhuma hipótese é descartada: descartar deixa um quadrante vazio na matriz de confusão, e sem falso negativo o limiar nunca calibra.'),
 ('EVD_CLASSE','texto','condicional','A_INTERIOR_FAIXA | A_INTERIOR_SEQUENCIA | A_INTERIOR_QUADRA_LOTE | B_ANDAR_AUSENTE | C_EXTRAPOLADA | D_POSICIONAL_SATURADO | E_POSICIONAL_ENUMERADO | F_POSICIONAL_IMPLICITO','n/a','Mecanismo exato que produziu a hipótese. B_ANDAR_AUSENTE era ponto cego: a grade era o produto cartesiano dos andares OBSERVADOS, então andar sem nenhum rastro ficava invisível — e prédio não pula andar.'),
 ('EVD_DISTANCIA','inteiro','condicional','>= 1','n/a','Passos além do intervalo observado do próprio andar. Preenchido se e somente se EVD_CLASSE=C_EXTRAPOLADA (G18). É o campo que permite mover o limiar depois com um UPDATE, sem re-rodar a safra.'),
],
'CNF': [
 ('CNF_ELEGIBILIDADE','texto','sim','ELEGIVEL | ELEGIVEL_COM_OBSERVACAO | ELEGIVEL_COM_QUARENTENA | RETIDO_SEM_NUMERO | RETIDO_GEO | RETIDO_ANOMALIA | RETIDO_CONFIANCA','n/a','POR QUE este registro entrou ou não no universo de candidatos. A exclusão por FAIXA DE CONFIANÇA usa pesos heurísticos (40/30/30) ainda NÃO calibrados contra campo — pode ficar, invisível não pode. RETIDO_CONFIANCA é o rótulo a monitorar: se ele estiver removendo oportunidade real, o número aparece aqui antes de aparecer no prejuízo.'),
 ('CNF_COORD','inteiro','sim','0..40','n/a','Eixo coordenada. Zero obrigatório em INFERIDO.'),
 ('CNF_ENDERECO','inteiro','sim','0..30','n/a','Eixo endereço textual.'),
 ('CNF_CONTEXTO','inteiro','sim','0..30','n/a','Eixo contexto.'),
 ('CNF_LOCALIZACAO','inteiro','sim','0..100','n/a','Soma dos três eixos.'),
 ('CNF_FAIXA','texto','sim','MUITO_ALTA | ALTA | MEDIA | BAIXA','n/a','Faixa de localização. É trava, não decoração.'),
 ('CNF_TIPOLOGIA','texto','condicional','ALTA | MEDIA | BAIXA','n/a','Confiança da tipologia — dimensão distinta da localização.'),
 ('CNF_INFERENCIA','texto','condicional','ALTA | MEDIA | BAIXA','n/a','Confiança da hipótese. Só preenchida em INFERIDO.'),
],
'CAD': [
 ('CAD_STATUS','texto','sim','CASADO | SEM_CADASTRO | CADASTRO_SEM_FONTE | AMBIGUO','n/a','Resultado do confronto no grão da unidade.'),
 ('CAD_SITUACAO','texto','condicional','ATIVA | CORTADA | SUPRIMIDA | INATIVA','n/a','Situação da ligação no cadastro.'),
 ('CAD_QTD_LIGACOES','inteiro','condicional','>= 0','n/a','Ligações no endereço.'),
 ('CAD_ECONOMIAS','inteiro','condicional','>= 0','n/a','Economias que o cadastro declara no endereço.'),
 ('CAD_PERFIL_TARIFARIO','texto','condicional','RESIDENCIAL | COMERCIAL | INDUSTRIAL | PUBLICO | MISTO','n/a','Perfil tarifário cadastrado. Confrontado com ATV_PRESENTE, produz o gap tarifário.'),
 ('CAD_DELTA_ECONOMIAS','inteiro','condicional','livre','n/a','Observadas menos cadastradas. É o número que vira dinheiro — calculado só sobre OBSERVADO.'),
],
'ACT': [
 ('ACT_RECOMENDACAO','texto','sim','INDIVIDUALIZAR | CRUZAR_COM_CADASTRO_COMERCIAL | VISTORIA_CONFIRMATORIA | RECLASSIFICAR | NENHUMA_ACAO','n/a','Ação sugerida. Sugestão apoiada em evidência, nunca decisão automática.'),
 ('ACT_REQUER_CONFIRMACAO','texto','sim','SIM | NAO','n/a','INFERIDO implica SIM, verificado no gate.'),
 ('ACT_PRIORIDADE','inteiro','condicional','1..5','n/a','1 = maior. Deriva de delta, confiabilidade e veredito.'),
 ('ACT_DESTINO_CAMPO','texto','sim','ENVIAR | AMOSTRAR | RETER','n/a','O que fazer com a linha. Deriva de EVD_GRAU: SUSTENTADA vai a campo, PLAUSIVEL entra em amostra dirigida, ESPECULATIVA fica na base e não sai para rota. Erro visível ao analista custa minutos; erro visível ao técnico custa a adoção do produto. AMOSTRAR e RETER nunca entram no export de campo (G17), e nenhum grau entra em contagem.'),
],
'AUD': [
 ('AUD_FONTE','texto','sim','<sistema>/<versão>','n/a','Sistema e versão que emitiu a linha.'),
 ('AUD_SAFRA','texto','sim','livre','n/a','Safra do dado de origem.'),
 ('AUD_EXECUTADO_EM','timestamp','sim','ISO-8601 UTC','n/a','Momento da execução.'),
 ('AUD_HASH_ENTRADA','texto','sim','SHA-256 (16 díg)','n/a','Hash do arquivo de entrada. Fecha a linhagem: da linha ao byte.'),
],
}


# ── PROVENIÊNCIA: todo campo nomeia sua origem. Sem exceção, sem campo órfão.
#    'IBGE:<campo>'      -> campo do layout oficial, cru
#    'DERIVADO:<regra>'  -> calculado a partir de campos IBGE nomeados
#    'CADASTRO:<campo>'  -> vem do cadastro da concessionária (bloco de confronto)
#    'RCC:<regra>'       -> gerado pelo próprio relatório (id, execução)
ORIGEM = {
 'IMOVEL_ID':'RCC:store de resolucao de entidade sobre enderecos IBGE',
 'COLETIVA_ID':'RCC:surrogate da chave canonica',
 'COLETIVA_CHAVE_HASH':'DERIVADO:blake2b(COLETIVA_CHAVE_CANONICA)',
 'COLETIVA_CHAVE_CANONICA':'DERIVADO:COD_MUNICIPIO+NOM_TIPO_SEGLOGR+NOM_SEGLOGR_HARM+NUM_ENDERECO+DSC_LOCALIDADE canonizados',
 'BLOCO_ID':'DERIVADO:COLETIVA_ID + bloco de NOM_COMP_ELEM/VAL_COMP_ELEM',
 'UNIDADE_ID':'DERIVADO:BLOCO_ID + unidade de NOM_COMP_ELEM/VAL_COMP_ELEM',
 'ORIGEM_SISTEMA':'RCC:constante por fonte de entrada',
 'ORIGEM_REGISTRO_ID':'IBGE:COD_UNICO_ENDERECO',
 'ORIGEM_GRUPO_ID':'DERIVADO:id de agrupamento do motor de origem',
 'ORIGEM_LIGACAO':'CADASTRO:matricula',
 'EXEC_ID':'RCC:identificador da execucao',
 'END_MUNICIPIO':'IBGE:COD_MUNICIPIO',
 'END_LOGRADOURO':'DERIVADO:NOM_TIPO_SEGLOGR + NOM_TITULO_SEGLOGR + NOM_SEGLOGR normalizados',
 'END_LOGRADOURO_ORIG':'IBGE:NOM_TIPO_SEGLOGR + NOM_TITULO_SEGLOGR + NOM_SEGLOGR',
 'END_LOGR_EQUIV_SUGERIDA':'DERIVADO:chave fonetica/romano + confirmacao por geometria e numeracao',
 'END_LOGR_EQUIV_GRAU':'DERIVADO:regua R9 sobre a evidencia independente',
 'END_LOGR_EQUIV_EVIDENCIA':'DERIVADO:distancia entre centroides e numeros em comum',
 'END_NUMERO':'DERIVADO:NUM_ENDERECO coagido a inteiro',
 'END_NUMERO_ORIG':'IBGE:NUM_ENDERECO',
 'END_QUADRA':'DERIVADO:NOM_COMP_ELEM/VAL_COMP_ELEM camada QUADRA',
 'END_LOTE':'DERIVADO:NOM_COMP_ELEM/VAL_COMP_ELEM camada LOTE',
 'END_COMPLEMENTO':'DERIVADO:NOM_COMP_ELEM1..5 + VAL_COMP_ELEM1..5 em 7 camadas',
 'END_COMPLEMENTO_ORIG':'IBGE:NOM_COMP_ELEM1..5 + VAL_COMP_ELEM1..5',
 'END_LOCALIDADE':'IBGE:DSC_LOCALIDADE',
 'END_CEP':'IBGE:CEP',
 'GEO_LAT_ORIG':'IBGE:LATITUDE',
 'GEO_LON_ORIG':'IBGE:LONGITUDE',
 'GEO_LAT':'DERIVADO:LATITUDE apos ajuste declarado em GEO_AJUSTE_FONTE',
 'GEO_LON':'DERIVADO:LONGITUDE apos ajuste declarado em GEO_AJUSTE_FONTE',
 'GEO_AJUSTE_FONTE':'RCC:etapa que produziu a coordenada em uso',
 'GEO_DESLOCAMENTO_M':'DERIVADO:haversine(GEO_*_ORIG, GEO_*)',
 'GEO_ACURACIA_M':'DERIVADO:piso por NV_GEO_COORD',
 'GEO_NIVEL':'IBGE:NV_GEO_COORD',
 'GEO_QUALIDADE':'DERIVADO:NV_GEO_COORD + sanidade geoespacial',
 'GEO_RAIO_GRUPO_M':'DERIVADO:haversine max ao centroide do grupo',
 'SET_COD':'DERIVADO:COD_SETOR truncado a 15 digitos',
 'SET_DOM_PARTICULARES':'IBGE:AGREGADOS_SETOR_2022:V0003',
 'SET_DOM_OCUPADOS':'IBGE:AGREGADOS_SETOR_2022:V0007',
 'SET_DOM_USO_OCASIONAL':'IBGE:AGREGADOS_SETOR_2022:V0008',
 'SET_DOM_VAGOS':'IBGE:AGREGADOS_SETOR_2022:V0009',
 'SET_DOM_SEM_OCUPACAO_HABITUAL':'DERIVADO:V0008 + V0009',
 'SET_TX_SEM_OCUPACAO_HABITUAL':'DERIVADO:(V0008 + V0009) / V0003',
 'SET_CLASSE_SEM_OCUPACAO_HABITUAL':'DERIVADO:faixa de SET_TX_SEM_OCUPACAO_HABITUAL com piso amostral',
 'COL_FORMA':'DERIVADO:complemento normalizado + COD_ESPECIE',
 'COL_FORMA_ORIG':'DERIVADO:rotulo de tipologia do motor de origem',
 'COL_USO':'DERIVADO:COD_ESPECIE residencial x nao-residencial no grupo',
 'COL_CLASSE':'DERIVADO:porte do grupo (unidades e blocos)',
 'COL_CRITERIO_UNIFICACAO':'DERIVADO:NUM_ENDERECO + quadra/lote do complemento',
 'COL_VEREDITO':'DERIVADO:forca da evidencia estrutural e espacial',
 'COL_QTD_OBSERVADA':'DERIVADO:contagem de unidades OBSERVADO no grupo',
 'COL_QTD_INFERIDA':'DERIVADO:contagem de unidades INFERIDO no grupo',
 'COL_QTD_BLOCOS':'DERIVADO:blocos distintos no complemento',
 'COL_QTD_UNID_ATIVIDADE':'DERIVADO:contagem de ATV_PRESENTE=SIM no grupo',
 'COL_FLAG_USO_MISTO':'DERIVADO:COD_ESPECIE {1,2} e {3,4,5,6,8} na mesma chave',
 'COL_POLO_CLASSE':'DERIVADO:DBSCAN por densidade de enderecos IBGE',
 'UND_NATUREZA':'DERIVADO:registro presente no arquivo x lacuna inferida',
 'UND_BLOCO':'DERIVADO:camada BLOCO do complemento',
 'UND_TIPO':'DERIVADO:camada UNIDADE/MORADIA do complemento',
 'UND_VALOR':'DERIVADO:valor da camada UNIDADE/MORADIA',
 'UND_POSICAO':'DERIVADO:camada POSICAO do complemento',
 'UND_ECONOMIAS':'DERIVADO:1 por unidade observada',
 'ATV_PRESENTE':'DERIVADO:COD_ESPECIE + DSC_ESTABELECIMENTO',
 'ATV_NOME':'DERIVADO:DSC_ESTABELECIMENTO normalizado',
 'ATV_NOME_ORIG':'IBGE:DSC_ESTABELECIMENTO',
 'ATV_SETOR':'DERIVADO:COD_ESPECIE; especie 6 pelo lexico sobre DSC_ESTABELECIMENTO',
 'ATV_DETALHE':'DERIVADO:trecho de DSC_ESTABELECIMENTO que casou no lexico',
 'ATV_ESPECIE_COD':'IBGE:COD_ESPECIE',
 'ATV_QTD_ESTABELECIMENTOS':'DERIVADO:contagem de COD_ESPECIE em {3,4,5,6,8} por chave',
 'ATV_DECLARADA_FAIXA':'IBGE:COD_INDICADOR_ESTAB_ENDERECO (decodificado)',
 'ATV_VALIDACAO_DECLARADA':'DERIVADO:COD_INDICADOR_ESTAB_ENDERECO x ATV_QTD_ESTABELECIMENTOS',
 'ATV_EVIDENCIA_CAMPO':'RCC:nome do campo IBGE que sustentou a afirmacao',
 'ATV_GRAO_EVIDENCIA':'DERIVADO:presenca de complemento estruturado no registro',
 'ATV_INCERTEZA_M':'DERIVADO:piso por NV_GEO_COORD',
 'ATV_ROTA_TRATAMENTO':'DERIVADO:COD_INDICADOR_ESTAB_ENDERECO',
 'ATV_FLAG_FACHADA_ATIVA':'DERIVADO:camada PAVIMENTO terreo + COD_ESPECIE nao-residencial',
 'ATV_FLAG_GAP_TARIFARIO':'DERIVADO:ATV_PRESENTE x CAD_PERFIL_TARIFARIO',
 'EVD_REGRA':'RCC:identificador da regra que produziu a linha',
 'EVD_DESCRICAO':'RCC:evidencia em texto legivel',
 'EVD_PRESENTES':'DERIVADO:conjunto observado no grupo',
 'EVD_GRAU':'DERIVADO:quantos lados da observacao cercam a hipotese',
 'EVD_CLASSE':'DERIVADO:mecanismo da regra de gap que produziu a linha',
 'EVD_DISTANCIA':'DERIVADO:passos alem do intervalo observado do andar',
 'CNF_ELEGIBILIDADE':'DERIVADO:NUMERO + SANIDADE_GEO + N_SINAIS_PRIMARIOS + RADAR_CONF_FAIXA',
 'CNF_COORD':'DERIVADO:NV_GEO_COORD',
 'CNF_ENDERECO':'DERIVADO:completude de logradouro, numero e complemento',
 'CNF_CONTEXTO':'DERIVADO:sanidade geo + ausencia de anomalia de coleta',
 'CNF_LOCALIZACAO':'DERIVADO:soma dos tres eixos',
 'CNF_FAIXA':'DERIVADO:faixa de CNF_LOCALIZACAO',
 'CNF_TIPOLOGIA':'DERIVADO:forca do padrao de complemento',
 'CNF_INFERENCIA':'DERIVADO:tamanho e regularidade da lacuna',
 'CAD_STATUS':'CADASTRO:resultado do confronto',
 'CAD_SITUACAO':'CADASTRO:situacao da ligacao',
 'CAD_PERFIL_TARIFARIO':'CADASTRO:perfil tarifario',
 'CAD_QTD_LIGACOES':'CADASTRO:ligacoes no endereco',
 'CAD_ECONOMIAS':'CADASTRO:economias declaradas',
 'CAD_DELTA_ECONOMIAS':'DERIVADO:COL_QTD_OBSERVADA - CAD_ECONOMIAS',
 'ACT_RECOMENDACAO':'DERIVADO:delta, confiabilidade e veredito',
 'ACT_REQUER_CONFIRMACAO':'DERIVADO:UND_NATUREZA',
 'ACT_PRIORIDADE':'DERIVADO:delta, confiabilidade e veredito',
 'ACT_DESTINO_CAMPO':'DERIVADO:EVD_GRAU',
 'AUD_FONTE':'RCC:sistema e versao que emitiu a linha',
 'AUD_SAFRA':'RCC:safra declarada do arquivo de entrada',
 'AUD_EXECUTADO_EM':'RCC:timestamp da execucao',
 'AUD_HASH_ENTRADA':'RCC:sha256 do arquivo de entrada',
}


def gerar_dicionario():
    """Gera o dicionário. Campo sem origem declarada ABORTA — é o gate que
    impede coluna inventada de entrar no padrão."""
    linhas = []
    for num, bloco, desc_bloco in BLOCOS:
        for i, (campo, tipo, obrig, dom, estab, desc) in enumerate(CAMPOS[bloco], 1):
            if campo not in ORIGEM:
                raise SystemExit(
                    f'PROVENIENCIA AUSENTE: {campo} nao declara de onde vem. '
                    'Todo campo tratado nomeia seu campo de origem no layout '
                    'oficial, ou a regra que o deriva de campos nomeados.')
            dom = DOM_SKILL.get(campo, dom)
            linhas.append({
                'ORDEM': f'{num}{i:02d}', 'CAMPO': campo, 'BLOCO': bloco,
                'BLOCO_DESCRICAO': desc_bloco, 'TIPO': tipo,
                'OBRIGATORIO': obrig, 'DOMINIO': dom,
                'ORIGEM_DADO': ORIGEM[campo],
                'ESTABILIDADE': estab, 'DESCRICAO': desc})
    return linhas


def ordem_colunas():
    return [l['CAMPO'] for l in gerar_dicionario()]


if __name__ == '__main__':
    dic = gerar_dicionario()
    import os
    destino = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'RCC_dicionario.csv')
    with open(destino, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(dic[0].keys()), delimiter=';')
        w.writeheader(); w.writerows(dic)
    print(f'{destino} — {len(dic)} campos em {len(BLOCOS)} blocos')
    for num, bloco, _ in BLOCOS:
        print(f'  {num} {bloco:<6} {len(CAMPOS[bloco]):>2} campos')
