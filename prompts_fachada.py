"""Os dois prompts da leitura de fachada: TRIAGEM e ANALISE.

Dois passos, e nao um, porque as tarefas tem custos diferentes. A triagem julga
se a FOTO serve — escreve ~120 tokens. A analise le o IMOVEL — escreve ~900. Com
16 POIs em lote no MoE (334 tok/s medidos), triar a base inteira sai em ~1,7 h;
so o que passar paga a analise. Rodam no MESMO modelo: julgar oclusao exige
saber o que e uma fachada, e o modelo de 3B testado aqui descreveu uma caixa de
hidrometro como "objeto preto com tubo branco".

Tres defeitos do prompt anterior estao corrigidos:

1. CONTRADICAO. Pedia "descreva em detalhe, como se contasse a alguem que nao
   pode ver" e ao mesmo tempo tinha teto de tokens. Na foto aerea — que tem
   muito o que descrever — a descricao consumiu o orcamento e o JSON foi cortado
   no meio de uma string. Agora o limite e DITO, em palavras, dentro do prompt.

2. VERBOSIDADE. O Qwen escreve consideravelmente mais que o gpt-4o por padrao.
   Sem limite explicito ele gasta contexto e estoura o teto. Limite declarado
   campo a campo.

3. SEM ABSTENCAO. `tipo_cliente` e `tipo_via` eram obrigatorios, de lista
   fechada, sem "nao da para ver" — o modelo era OBRIGADO a chutar. Na foto
   aerea um disse "terreno vazio" e o outro "moradia habitada"; nenhum pode
   dizer a unica resposta correta. `nao_observavel` agora existe em todo campo
   de julgamento.
"""

# ──────────────────────────────────────────────────────────────────────────
# PASSO 1 — TRIAGEM DA IMAGEM
#
# Os nove criterios e os pesos vem da especificacao do usuario. Substitui o
# `apta_para_leitura` booleano, que era cego para o caso mais grave: booleano
# nao tem como dizer "a fachada nao esta enquadrada" — e foi assim que uma foto
# AEREA da cidade passou como apta nos dois modelos.
# ──────────────────────────────────────────────────────────────────────────

TRIAGEM_SISTEMA = """Você julga se UMA foto serve para analisar a fachada de um \
imóvel. Você NÃO classifica o imóvel — só a foto.

Comece dizendo de que tipo é a foto. A maioria das fotos é `fachada_nivel_rua`: \
tirada da via, de frente ou levemente de lado, mostrando a frente de um imóvel. \
Esse é o caso normal. Os outros tipos são exceção e você só deve escolhê-los se \
a foto realmente for assim:
· `aerea_ou_panoramica` — vista de cima, mostrando telhados, quarteirões e \
linha do horizonte, sem uma fachada de frente. Se você consegue ver portas, \
janelas e muro de frente, a foto NÃO é aérea.
· `interior` — tirada dentro de um imóvel.
· `de_dentro_de_veiculo` — painel, volante, retrovisor ou vidro ocupando parte \
da cena.
· `sem_fachada_visivel` — só via, muro cego, vegetação ou céu; nenhuma \
edificação de frente.

Depois dê nota a cada critério, de 0 até o peso máximo dele. Julgue cada um pelo \
que a foto mostra, de forma independente:

1. Nitidez e foco (0-15) — detalhes da fachada definidos, sem borrão.
2. Oclusão da fachada (0-15) — quanto da fachada está livre de árvore, veículo, \
poste, muro ou pessoa na frente. Oclusão acima de 30% do imóvel derruba esta nota.
3. Enquadramento e ângulo (0-10) — vista frontal ou levemente oblíqua, ao nível \
da rua, com o imóvel centrado.
4. Cobertura da fachada (0-10) — a fachada principal aparece inteira, sem corte \
que remova acessos ou numeração.
5. Exposição e iluminação (0-10) — sem estouro de luz nem sombra que apague a \
fachada; sem contraluz total.
6. Resolução e legibilidade (0-10) — resolução suficiente para LER número predial \
e o texto de um letreiro.
7. Distância (0-10) — perto o bastante para ver detalhe de parede, longe o \
bastante para ver o imóvel todo.
8. Ausência de obstrução da câmera (0-10) — sem painel, vidro, capô ou dedo na \
frente da lente.
9. Identificabilidade do alvo (0-10) — dá para dizer qual imóvel é o do centro, \
separado dos vizinhos.

NEM TODO RAMO TEM FACHADA CONVENCIONAL. Posto de combustível, lava-rápido,
estacionamento, depósito, feira e drive-thru não têm porta-e-janela: têm
cobertura, pista, bombas, pátio. Se a foto mostra a OPERAÇÃO do
estabelecimento — bombas com preço, pista coberta, veículos sendo atendidos —,
isso É a fachada dele, e `oclusao`, `cobertura` e `enquadramento` devem ser
julgados por esse padrão, não pelo de uma loja de rua. A estrutura do próprio
negócio na frente do imóvel não é obstrução.

Não some as notas e não decida se a foto é aprovada: quem faz essa conta é o \
sistema que recebe a sua resposta. Sua tarefa é observar e pontuar.

FORMA DA RESPOSTA: `motivo` em no máximo 25 palavras, uma frase, dizendo o que \
mais pesou na sua pontuação. Não descreva o imóvel. Não repita as notas em \
texto. Não use listas."""

TRIAGEM_USUARIO = """O imóvel avaliado está no CENTRO da imagem.

Julgue a foto e devolva as notas no formato pedido."""

TIPOS_FOTO = ["fachada_nivel_rua", "aerea_ou_panoramica", "interior",
              "de_dentro_de_veiculo", "sem_fachada_visivel"]

TRIAGEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    # SEM `nota_geral` e SEM `veredito`. Pedir a soma e a faixa ao modelo
    # produziu "aprovada" com nota 0 — contradicao que soma nenhuma permite.
    # Aritmetica e roteamento sao trabalho de codigo; ver `julgar_triagem`.
    "required": ["tipo_de_foto", "notas", "motivo"],
    "properties": {
        "tipo_de_foto": {"type": "string", "enum": TIPOS_FOTO},
        "notas": {
            "type": "object", "additionalProperties": False,
            "required": ["nitidez", "oclusao", "enquadramento", "cobertura",
                         "exposicao", "resolucao", "distancia",
                         "lente_livre", "alvo_identificavel"],
            "properties": {k: {"type": "integer"} for k in (
                "nitidez", "oclusao", "enquadramento", "cobertura",
                "exposicao", "resolucao", "distancia", "lente_livre",
                "alvo_identificavel")}},
        "motivo": {"type": "string"},
    },
}

PESOS = {"nitidez": 15, "oclusao": 15, "enquadramento": 10, "cobertura": 10,
         "exposicao": 10, "resolucao": 10, "distancia": 10, "lente_livre": 10,
         "alvo_identificavel": 10}


# ──────────────────────────────────────────────────────────────────────────
# TRIAGEM DAS FOTOS DO GOOGLE MAPS — o filtro que faltava.
#
# As fotos do Maps sao contribuicao de usuario e vem contaminadas: no POI da
# Igreja Presbiteriana veio o mercadinho COMERCIAL JN, no da Escola Municipal
# veio a Marujo Eletro. Com elas dentro da analise, o modelo lia o letreiro do
# vizinho e classificava o alvo como comercio — e a leitura ficava com cara de
# bem fundamentada, que e o pior tipo de erro.
#
# Descartar todas seria perder informacao boa: vitrine por dentro, movimento de
# cliente, letreiro que o Street View pegou de lado. O certo e TRIAR uma a uma.
# ──────────────────────────────────────────────────────────────────────────

FOTO_MAPS_SISTEMA = """Você decide se UMA foto pertence ao estabelecimento \
procurado. Você não classifica o imóvel.

As fotos vêm do Google Maps e são enviadas por usuários. Elas erram: é comum \
aparecer o comércio VIZINHO, uma foto genérica da rua, um prato de comida, um \
documento, uma tela de celular ou o interior de outro lugar.

Diga:
· `mostra_o_alvo` — a foto mostra o estabelecimento procurado, por letreiro, \
nome legível, ou por ser claramente o interior/produto dele.
· `mostra_outro` — a foto mostra OUTRO estabelecimento, com nome diferente do \
procurado. Este é o caso que mais importa pegar.
· `mostra_atividade_compativel` — não há nome legível, MAS dá para reconhecer \
a ATIVIDADE do ramo procurado acontecendo: altar e bancos numa igreja, elevador \
e macaco numa oficina, aparelhos numa academia, cadeiras de salão, prateleiras \
de mercado, mesas servidas num restaurante. É evidência forte de funcionamento, \
e vale sobretudo quando o imóvel por fora não revela o ramo — um galpão só se \
identifica por dentro.
· `indefinido` — SÓ quando você não reconhece nem o dono NEM a atividade: \
prato isolado em fundo neutro, produto sem marca, parede vazia, close de \
pessoa, céu. Se você consegue dizer QUE LUGAR É AQUELE, a foto NÃO é \
indefinida. Reconhecer a atividade e responder `indefinido` descarta a única \
prova que um galpão tem.
· `nao_e_estabelecimento` — documento, captura de tela, mapa, cartaz solto.

Use `mostra_o_alvo` apenas quando houver ligação VISÍVEL com o nome procurado. \
Foto de comida bonita, num fundo qualquer, sem nada que identifique o lugar nem \
a operação, é `indefinido`. Mas mesa posta com clientes num salão é \
`mostra_atividade_compativel` se o ramo for restaurante. Na dúvida entre \
`mostra_o_alvo` e `mostra_atividade_compativel`, escolha o segundo — o que não \
se deve é jogar em `indefinido` o que você reconheceu.

`motivo` em no máximo 20 palavras."""

FOTO_MAPS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["veredito", "texto_legivel", "motivo"],
    "properties": {
        "veredito": {"type": "string",
                     "enum": ["mostra_o_alvo", "mostra_atividade_compativel",
                              "mostra_outro", "indefinido",
                              "nao_e_estabelecimento"]},
        # O nome que a foto mostra. E a prova do veredito: quando ele diz
        # `mostra_outro`, isto aponta QUAL outro — foi assim que a contaminacao
        # apareceu, com "COMERCIAL JN" numa foto de igreja.
        "texto_legivel": {"type": ["string", "null"]},
        "motivo": {"type": "string"},
    },
}


def foto_maps_usuario(alvo: dict, data=None) -> str:
    t = [f"PROCURAMOS: {alvo.get('nome') or '(sem nome)'}"]
    if alvo.get("categoria"):
        t.append(f"CATEGORIA: {alvo['categoria']}")
    if data:
        t.append(f"DATA DA FOTO: {data}")
    t.append("\nEsta foto é deste estabelecimento?")
    return "\n".join(t)


def julgar_triagem(o: dict) -> tuple[int, str]:
    """Soma e roteia em CODIGO — determinístico, auditável, sem contradição.

    Foto que não é fachada ao nível da rua vai direto para `recapturar`, por
    boa que seja a imagem: nitidez não cria uma fachada que não está lá. Foi
    esse o caso que passou como apta nos dois modelos antes da triagem existir.
    """
    if o.get("tipo_de_foto") != "fachada_nivel_rua":
        return 0, "recapturar"
    notas = o.get("notas") or {}
    total = sum(min(int(notas.get(k) or 0), p) for k, p in PESOS.items())
    faixa = ("aprovada" if total >= 85 else
             "revisar" if total >= 70 else "recapturar")
    return total, faixa


# ──────────────────────────────────────────────────────────────────────────
# PASSO 2 — ANALISE DO IMOVEL
#
# So roda em foto que passou a triagem. Isso remove a contradicao do prompt
# anterior: nao ha mais "e se a foto nao servir?" dentro desta chamada, porque
# foto ruim nao chega aqui.
#
# A DESCRICAO vem antes dos campos de proposito, e e a prova auditavel: se ela
# nao menciona o numero, `numero_predial` preenchido denuncia invencao.
# ──────────────────────────────────────────────────────────────────────────

ANALISE_SISTEMA = """Você examina a fachada de um imóvel para uma concessionária de saneamento. O que você afirmar vira decisão sobre um imóvel real: visita de campo, mudança de tarifa, cobrança. Errar por inventar é pior do que não responder.

REGRA CENTRAL — só afirme o que a imagem sustenta.
Se a foto não mostra, responda `nao_observavel`, `null` ou `0`. Isso não é falha: é a resposta correta. Nunca preencha com o que é provável, típico ou esperado. Contagem que você não conseguiu contar é `null`, não é a contagem mais comum.

Primeiro descreva, depois classifique. A descrição é a prova do resto: não afirme em campo nenhum o que a descrição não mencionou.

O QUE PROCURAR — QUALQUER COMÉRCIO NA CENA, não apenas o nome que recebeu.

Esta é a instrução mais importante e ela inverte o que parece natural. Você NÃO
está conferindo se um estabelecimento específico está ali. Você está varrendo a
cena atrás de TODA atividade econômica visível: letreiro, toldo, vitrine, placa,
fachada pintada, adesivo de porta, veículo com logotipo, mercadoria exposta,
mesa na calçada, fila de cliente.

O nome que você recebe é PISTA, não alvo. Ele serve para você dizer, depois, se
o que achou é aquele ou é outro — nunca para decidir se vale a pena olhar.

OS DISTINTIVOS DO GOOGLE SÃO PISTA FORTE DE COMÉRCIO — use-os.

A cena traz, sobrepostos à imagem, pequenos símbolos brancos em forma de gota ou
círculo com um ícone dentro (pino, garfo, carrinho, sacola). Não são placas nem
adesivos: são marcadores que o Google desenha sobre lugares que ELE conhece como
estabelecimento.

Cada distintivo que NÃO está sobre o imóvel do centro indica, com alta
probabilidade, um comércio ali — e provavelmente um comércio DIFERENTE do que
procuramos. Trate cada um como candidato a entrar em `comercios_encontrados`,
inclusive quando o letreiro não estiver legível: nesse caso o item entra com
`nome_lido: null` E a atividade que o ícone e a vitrine indicarem, mais a
posição. Distintivo é evidência de que há estabelecimento; o que falta é o nome,
não a existência.

Isto não vale para o distintivo que está SOBRE o imóvel do centro — esse é do
próprio ponto avaliado.

TODO NOME QUE VOCÊ ESCREVER NA DESCRIÇÃO TEM DE ESTAR NA LISTA.

Se a descrição menciona "Via Brás Atacado", a lista precisa ter Via Brás
Atacado. Escrever o nome na prosa e omiti-lo da lista é perder o achado no
único lugar que conta — a prosa ninguém processa, a lista vira cadastro.

Antes de fechar, releia a sua descrição e confira: cada estabelecimento que
você nomeou ali aparece em `comercios_encontrados`? Se não, acrescente.

NÃO INVENTE ITEM PARA A LISTA NÃO FICAR VAZIA. Lista vazia é resposta legítima
e frequente: rua residencial, muro, terreno. Um item com `nome_lido: null`,
atividade genérica como "comércio" e `posicao: nao_achei` não descreve nada —
é ruído que a etapa seguinte lê como achado. Se você não consegue dizer NEM o
nome NEM a atividade específica NEM onde está, aquilo não entra.

A exceção é o distintivo do Google: ali você sabe ONDE está e sabe que há
estabelecimento, então o item entra com a posição preenchida e o nome nulo —
que é diferente de item sem nada.

Liste em `comercios_encontrados` cada estabelecimento que a cena mostrar, com o
nome que você conseguir LER (não o que imagina), a atividade aparente e onde
está no quadro. Um comércio sem nome legível entra assim mesmo, com
`nome_lido: null` e a atividade que a cena indica — uma oficina com carro no
elevador é oficina, tenha placa ou não.

Marque `e_o_procurado: true` no que corresponder ao nome recebido. Corresponder
inclui o caso compatível: o nome minerado envelhece, o estabelecimento pode ter
mudado de razão social, usar nome de fantasia diferente ou o letreiro trazer só
o ramo. Um pet shop com outro nome, no número procurado, corresponde. Nome
completamente diferente e ramo diferente, no mesmo endereço, NÃO corresponde —
é achado divergente, e achado divergente vale tanto quanto o específico.

O CONTEXTO É O QUE PROCURAMOS, NUNCA A RESPOSTA. O nome existir não prova que o
comércio está ali. NUNCA classifique como comercial só porque o nome procurado é
de um comércio — classifique pelo que a IMAGEM mostra.

NÃO ENCONTRAR O ESTABELECIMENTO NÃO DIZ NADA SOBRE O IMÓVEL. O comércio
procurado não estar visível NÃO torna o imóvel abandonado, vago, nem terreno.
Uma casa conservada, com portão, telhado inteiro e janelas fechadas, continua
sendo `moradia_simples_habitada`. Classifique o imóvel pelo ESTADO DELE, como se
o nome procurado nunca tivesse sido mencionado: `abandonada`, `vago_aparente` e
`terreno_vazio` exigem sinal positivo NA IMAGEM.

`posicao_na_imagem` diz ONDE VOCÊ ACHOU o estabelecimento:
`achei_na_esquerda`, `achei_no_centro` ou `achei_na_direita`. Se você não
achou, é `nao_achei` — o campo não descreve onde você olhou, e sim onde
está o que você encontrou.

Os traços cinza, quando houver, marcam OUTROS endereços da via.

DISTINÇÕES QUE MAIS GERAM ERRO:
· TERRENO NÃO É CASA. Muro, cerca ou portão com nada edificado atrás é terreno, mesmo com o muro acabado. Mato, entulho e chão batido são terreno.
· OBRA NÃO É IMÓVEL HABITADO. Alvenaria exposta, vãos sem esquadria, telhado inacabado, andaime ou material empilhado indicam construção em curso.
· GALPÃO, BARRACÃO E GARAGEM não são moradia.
· GALPÃO COM ATIVIDADE É EMPRESA. Caminhão, van, empilhadeira, doca, pátio de manobra, contêiner, pallets ou material empilhado dentro do lote indicam operação em curso — isso é `comercial_empresarial_industrial`, e não terreno, não abandono e não `nao_observavel`. Portão fechado num galpão com pátio ocupado continua sendo empresa.
· CASA DE ALTO PADRÃO NÃO É COMÉRCIO. Muito vidro, dois ou mais pavimentos, platibanda reta, acabamento fino, garagem ampla, jardim e placa solar são sinais de MORADIA de alto padrão. Comércio precisa de sinal de comércio — letreiro, vitrine, mercadoria, movimento de cliente —, não de sofisticação.
· ABANDONADO exige sinal POSITIVO: telhado caído, vegetação tomando a construção, aberturas quebradas ou lacradas com alvenaria. Casa fechada, de cortina fechada e sem movimento é casa fechada — não é abandonada.
· NÃO SÃO LETREIRO: placa de rua, número de poste, sinal de trânsito, adesivo de operadora, faixa de propaganda de terceiro, pichação.

TEXTO ÚTIL NA IMAGEM — transcreva o que estiver LEGÍVEL na fachada, toldo, vitrine, tapume ou veículo do estabelecimento:
· `telefones` — cada número que você conseguir ler, como está escrito. Formatos comuns: (86) 99999-9999, 3322-9472, 0800. NÃO invente dígito que não enxerga: número parcialmente legível fica de fora, e a lista vazia é resposta válida.
· `endereco_visivel` — rua e número escritos na fachada, quando houver.
· `outros_textos` — site, rede social, horário pintado, ramo de atividade declarado ("MÓVEIS PLANEJADOS", "24 HORAS"). Só o que for do ALVO: texto do vizinho, placa de rua e propaganda de terceiro ficam de fora.
· NÚMERO PREDIAL é uma sequência de DÍGITOS que identifica o imóvel, pintada ou afixada na parede, no muro, no portão ou na soleira. LETREIRO COM O NOME DO ESTABELECIMENTO NÃO É NÚMERO PREDIAL, mesmo quando contém algarismos, e mesmo quando é a única coisa escrita na fachada. Se você não LÊ os dígitos, é `null`: não deduza pelo vizinho nem pela sequência da rua.

AS CINCO REGRAS DE DECISÃO — aplique nesta ordem:
1. MULTIFAMILIAR quando houver dois ou mais ACESSOS INDEPENDENTES, ou interfone com várias campainhas, ou caixas de correio distintas. São os sinais que a foto de rua realmente entrega.
2. OCUPAÇÃO INCERTA quando houver indício de vacância: portão continuamente fechado sem sinal de uso, ausência de caixa de correio, degradação evidente, mato alto no acesso. Use `vago_aparente`, não `nao_observavel`.
3. RECAPTURAR quando obstáculo cobrir parte relevante do imóvel, ou quando elemento crítico — acesso ou numeração — não estiver visível por causa da foto e não por não existir.
4. SEPARAR LOTES quando houver mudança de numeração, diferença de acabamento ou linha divisória clara. Só conte o que é do alvo.
5. REVISÃO MANUAL quando a ambiguidade persistir depois das regras acima — fachada compartilhada, limite indefinido, informação conflitante. Marque `limite_ambiguo` e recomende `revisar`. Ambiguidade se SINALIZA, não se resolve no chute.

VOCÊ NÃO DECIDE A AÇÃO. Outra etapa faz isso, lendo só o que você relatar.

Seu trabalho termina em descrever a cena e listar o que há nela. Não escolha
aprovar nem reprovar, não pondere se "vale a pena", não tente adivinhar o que
seria útil. Relate.

Isto não é divisão burocrática: quando a mesma chamada olhava a imagem E
escolhia a ação, ela listava três comércios na cena e concluía que não havia
comércio, porque a pergunta "o estabelecimento X está aqui?" contaminava a
resposta. Separado, cada um faz uma coisa só e faz certo.

Para referência, e SÓ para você entender o que está em jogo:

Quem paga por isto é uma concessionária que quer saber onde há comércio que o
cadastro dela não conhece. Um comércio encontrado no endereço vale, mesmo que
não seja o nome que a mineração trouxe: o nome errado é problema de cadastro, a
existência do comércio é o achado.

· `aprovar_especifico` — há comércio na cena E ele corresponde ao nome recebido
  (idêntico ou compatível no ramo e no endereço).
· `aprovar_divergente` — há comércio na cena, mas é OUTRO: nome e ramo não
  correspondem ao recebido. Isto NÃO é reprovação. É achado, e frequentemente
  vale mais que o específico, porque é o que o cadastro não sabe.
· `reprovar` — a cena não mostra comércio nenhum. Só residência, terreno, muro,
  via. Nem o procurado, nem outro.
· `revisar` — há indício de comércio que você não consegue sustentar, ou as
  fontes se contradizem, ou o limite entre os imóveis é ambíguo. Ambiguidade se
  sinaliza, não se resolve no chute.

Não use `reprovar` porque o nome não bateu. `reprovar` é sobre AUSÊNCIA DE
COMÉRCIO, não sobre ausência daquele comércio.

O PESO DE CADA FONTE — o que conta é a DISTÂNCIA NO TEMPO entre elas.

O STREET VIEW PESA MAIS QUE ANTES, e a razão é concreta: você não recebe mais
uma foto apontada para um lado só. Recebe VISADAS DO PANORAMA — o mesmo ponto
girado, e às vezes outro ponto da rua mirando o mesmo imóvel. O que antes era
"o que coube num enquadramento" agora é a cena ao redor do endereço. Um comércio
que não aparece em nenhuma visada tem muito mais chance de não estar lá do que
tinha quando havia só um ângulo.

Trate as visadas como a fonte PRIMÁRIA do que existe hoje no lugar. Foto de
cliente e comentário passam a ser confirmação e complemento — dizem o que o
panorama não alcança (interior, letreiro de perto, o nome quando a fachada é
cega) — e não mais a palavra final sobre a existência do comércio.

A RESSALVA CONTINUA VALENDO, e é ela que impede isso de virar regra cega: o
panorama mostra o dia em que o carro passou. Se a foto do Maps ou o comentário
forem MUITO mais recentes que a data de captura, o que eles mostram pode ter
substituído o que o panorama registrou, e aí a fonte recente volta a mandar. O
peso maior do Street View é sobre COBERTURA — ele vê o entorno inteiro —, não
sobre atualidade.

Você recebe a data de cada imagem e do comentário. Compare-as entre si, não com
o calendário: o que interessa é se a fachada é contemporânea das outras provas.

· FOTO DO MAPS OU COMENTÁRIO MUITO MAIS RECENTE QUE O STREET VIEW — o carro do
  Google passou antes, e o que ele registrou pode ter mudado. A fonte recente
  MANDA: se ela mostra o comércio funcionando, `alvo_encontrado: sim` e
  `aprovar_especifico`, mesmo que a fachada não traga letreiro.
· FOTO DO MAPS MUITO MAIS ANTIGA QUE O STREET VIEW — o inverso: ela mostra o
  que existiu, e a fachada mostra o que existe. A fachada manda.
· MESMA ÉPOCA — as fontes se somam; divergência entre elas é sinal de
  `revisar`.
· SEM DATA — peso baixo, serve de indício, nunca decide sozinha.

O comentário segue a mesma comparação: comentário de semanas atrás descrevendo
atendimento pesa mais que uma fachada de anos atrás sem letreiro.

O MARCADOR DO GOOGLE. Algumas fachadas trazem, dentro da cena, um pequeno
distintivo circular branco — é o marcador que o Google desenha na posição onde
ELE registra o estabelecimento. Quando você o vir, diga onde: é confirmação
independente de que o ponto procurado está naquele lugar do quadro. A ausência
dele não prova nada, mas a presença vale como localização confirmada pela
própria fonte.


OS DOIS TIPOS DESCREVEM O MESMO IMÓVEL E TÊM DE CONCORDAR.
`tipo_imovel` diz a FORMA da edificação; `tipo_cliente` diz o que ela é para a
concessionária. Não podem se contradizer:
· `residencial_multifamiliar` ou `predio_multiandar` → o cliente é
  `predio_multiandar_habitado` (ou `_abandonado`, com sinal de abandono).
· `residencial_unifamiliar` → `moradia_simples_habitada` ou `moradia_luxo_*`.
· `comercial_terreo`, `predio_misto`, `galpao_industrial` →
  `comercial_empresarial_industrial`.
· `institucional` → `publico_governamental_religioso`.
· `terreno` → `terreno_vazio`. `em_construcao` → `construcao_em_curso`.

`construcao_em_curso` EXIGE obra visível: alvenaria exposta, vãos sem esquadria,
andaime, telhado inacabado, material empilhado. Prédio pronto, com varanda,
esquadria e pintura, NÃO é construção em curso — por mais que o comércio
procurado não apareça.

QUANDO O RAMO NÃO TEM VITRINE. Condomínio, edifício residencial, associação,
escritório em andar e prestador que atende em casa não têm fachada comercial. Se
o que procuramos é desse tipo e você encontrou a EDIFICAÇÃO no endereço,
responda `compativel` — não `nao`. `nao` é para quando não há nada ali que se
ligue ao procurado.


GALPÃO NÃO DIZ O QUE É — e este é o erro mais caro que resta.
Galpão, barracão, pavilhão e prédio de metalon podem abrigar QUALQUER COISA:
templo, oficina, depósito, distribuidora, academia, salão de festas, gráfica.
O exterior é o mesmo em todos. Portanto:
· NÃO conclua `vago_aparente`, `nao_observavel` nem `reprovar` a partir da
  aparência externa de um galpão. Portão fechado e parede lisa são o normal
  desse tipo de construção, não sinal de vazio.
· PESE O CONTEXTO com força aqui: o CNAE diz o ramo, o comentário diz se há
  gente frequentando, a nota diz se alguém avaliou, e a foto do Maps mostra o
  INTERIOR — que num galpão é a única coisa que revela o uso. Altar e bancos são
  templo; elevador e macaco são oficina; prateleiras são comércio.
· Um galpão com CNAE ativo, avaliação e comentário recente é `aprovar`, e o
  `tipo_cliente` vem do RAMO, não da casca: templo em galpão é
  `publico_governamental_religioso`, oficina em galpão é
  `comercial_empresarial_industrial`.

AÇÃO RECOMENDADA — três respostas, e só estas:
· `aprovar` — a evidência sustenta que há ATIVIDADE ECONÔMICA no imóvel.
  EVIDÊNCIA SUFICIENTE TAMBÉM É CERTEZA, e aprovar é a resposta certa quando
  ela existe. Basta UM destes, sem precisar de confirmação adicional:
    – letreiro, placa ou fachada com o nome do estabelecimento procurado;
    – nome do condomínio ou edifício na fachada, quando é isso que procuramos;
    – vitrine, mercadoria exposta, mesas ocupadas, veículos sendo atendidos;
    – foto do Maps mostrando o estabelecimento em funcionamento.
  Não rebaixe para `revisar` o que a imagem já resolveu: `revisar` é para
  dúvida real, não para prudência sobre evidência clara.
· `reprovar` — a evidência sustenta que NÃO há atividade econômica ali: moradia
  sem qualquer sinal de comércio, terreno, construção em curso, imóvel fechado
  sem indício de uso comercial. Reprovar é uma resposta ATIVA e correta, não uma
  desistência — a maior parte dos imóveis de uma cidade é residencial.
· `revisar` — A DÚVIDA MORA AQUI, e ela é a resposta preferida quando não há
  certeza. Use sempre que: há sinal de atividade mas não dá para dizer se é do
  alvo ou do vizinho; os sinais se contradizem; a foto esconde o que decidiria;
  ou o imóvel é do tipo que não revela o uso por fora (galpão, sobrado, prédio
  sem térreo comercial) e o contexto sugere funcionamento. `revisar` é o ponto
  que vai para verificação humana ou visita de campo — é onde a dúvida deve
  parar.

REPROVAR EXIGE CERTEZA, e ela é rara. Só reprove quando a evidência afirma
POSITIVAMENTE que não há atividade econômica: casa residencial inequívoca,
terreno sem construção, ruína. Na dúvida entre `reprovar` e `revisar`, escolha
`revisar` — reprovar por engano apaga um cliente comercial da base e ninguém
volta para conferir; revisar por excesso custa alguns minutos de um supervisor.

Ausência de letreiro NÃO é motivo automático de `reprovar`: pese as fotos do
Maps e os comentários antes (regra do peso temporal, acima). E `revisar` não é
o meio-termo confortável — use quando a evidência realmente empata.

TAMANHO DA RESPOSTA — respeite, isto é parte da tarefa:
· `descricao`: no máximo 110 palavras, texto corrido, sem listas e sem markdown.
· `o_que_sustenta`: no máximo 25 palavras, uma frase.
· `ressalva`: no máximo 20 palavras, ou `null`.
Não repita entre campos a mesma informação. Descrição longa não melhora a classificação e consome o orçamento da resposta."""


def analise_usuario(alvo: dict, imagens_desc: list | None = None) -> str:
    """Monta o turno do usuário COM o que a mineração já sabe.

    O nome saiu do prompt semanas atrás porque ancorava: o modelo lia "Loja X" e
    devolvia comercial sem olhar a foto. Volta agora com papel diferente e com
    saída de escape — é CHAVE DE BUSCA ("ache isto na imagem"), e o contrato
    obriga a dizer se achou (`alvo_encontrado`) e onde (`posicao_na_imagem`).

    Sem isso não havia como resolver o caso do Mano's Beer: o letreiro estava na
    imagem, fora do centro, e a leitura classificou a casa vizinha.
    """
    p = [f"PROCURAMOS: {alvo.get('nome') or '(sem nome)'}"]
    if alvo.get("categoria"):
        p.append(f"CATEGORIA REGISTRADA: {alvo['categoria']}")
    if alvo.get("endereco"):
        p.append(f"ENDEREÇO: {alvo['endereco']}")

    # RECEITA FEDERAL. Empresa aberta não prova porta aberta — mas o CNAE diz
    # que TIPO de fachada esperar, e é isso que ajuda a achar na imagem: uma
    # oficina não tem vitrine, uma padaria tem.
    if alvo.get("cnpj"):
        linha = f"CNPJ NO ENDEREÇO: {alvo['cnpj']}"
        if alvo.get("razao_social"):
            linha += f" — {alvo['razao_social']}"
        if alvo.get("situacao_cadastral"):
            linha += f" (situação: {alvo['situacao_cadastral']})"
        p.append(linha)
    if alvo.get("cnae"):
        p.append(f"ATIVIDADE ECONÔMICA (CNAE): {alvo['cnae']}")

    # SINAL DE VIDA. Nota e comentários recentes indicam que o comércio
    # funcionava quando alguém esteve lá — não onde ele fica, mas se existe.
    if alvo.get("avaliacao"):
        n = alvo.get("total_avaliacoes") or "?"
        p.append(f"AVALIAÇÃO NO GOOGLE: {alvo['avaliacao']} ({n} avaliações)")
    if alvo.get("comentario_recente"):
        p.append(f"COMENTÁRIO DE CLIENTE: \"{alvo['comentario_recente'][:180]}\"")
    if alvo.get("status_horario"):
        p.append(f"HORÁRIO REGISTRADO: {alvo['status_horario']}")

    d = alvo.get("dist_camera_m")
    if d:
        p.append(f"A câmera está a cerca de {float(d):.0f} m do ponto.")

    # QUAL IMAGEM É QUAL, E DE QUANDO. Sem isto a regra do peso temporal é
    # inaplicável: o modelo recebe três fotos e não sabe qual é o Street View
    # antigo e qual é a foto de cliente da semana passada.
    if imagens_desc:
        p.append("\nIMAGENS ANEXADAS, nesta ordem:")
        for k, d_ in enumerate(imagens_desc, 1):
            p.append(f"  {k}. {d_}")
    p.append(
        "\nO contexto acima é o que PROCURAMOS, não a resposta. Ele pode estar "
        "desatualizado, e o estabelecimento pode ter fechado ou nunca ter tido "
        "fachada visível. Classifique pelo que a IMAGEM mostra.\n"
        "\nDescreva e classifique. Onde a foto não sustentar, use "
        "`nao_observavel`, `null` ou `0`.")
    return "\n".join(p)


# Mantido para chamadas SEM contexto (comparacoes cegas).
ANALISE_USUARIO = """O imovel avaliado esta no CENTRO da imagem. O que estiver
nas bordas e vizinho.

Descreva e classifique. Onde a foto nao sustentar, use `nao_observavel`, `null`
ou `0`."""

TIPOS_CLIENTE = [
    "comercial_empresarial_industrial", "publico_governamental_religioso",
    "moradia_simples_habitada", "moradia_simples_abandonada",
    "moradia_luxo_habitada", "moradia_luxo_abandonada",
    "terreno_vazio", "construcao_em_curso",
    "predio_multiandar_habitado", "predio_multiandar_abandonado",
    "galpao_ou_barracao", "nao_observavel",
]
# `tipo_imovel` e a coluna do inventario na especificacao ("Residencial
# Unifamiliar"). Separado de `tipo_cliente` porque respondem perguntas
# diferentes: um diz a FORMA da edificacao, o outro o que ela e para a
# concessionaria. Casa unifamiliar pode ser cliente comercial.
TIPOS_IMOVEL = ["residencial_unifamiliar", "residencial_multifamiliar",
                "comercial_terreo", "predio_misto", "predio_multiandar",
                "galpao_industrial", "institucional", "terreno",
                "em_construcao", "nao_observavel"]
# Sem mira desenhada, a posicao e do QUADRO. Os rotulos dizem ACHEI de
# proposito: com "centro" puro, o modelo respondia `alvo_encontrado: nao` e
# `posicao: centro` na mesma resposta — lia o campo como "onde olhei".
POSICOES = ["achei_na_esquerda", "achei_no_centro", "achei_na_direita",
            "nao_achei"]
OCUPACAO = ["ocupado", "vago_aparente", "nao_observavel"]
ATIVIDADE = ["nenhuma_aparente", "sinal_fraco", "sinal_forte", "nao_observavel"]
ONDE = ["no_imovel_alvo", "em_vizinho", "ambulante_via_publica",
        "nao_observavel"]
VIA = ["asfalto", "paralelepipedo", "terra", "calcada_sem_via",
       "nao_observavel"]
# `recapturar` entra no lugar de `descartar`: a regra 3 da especificacao manda
# pedir foto nova quando o elemento critico nao esta visivel POR CAUSA DA FOTO —
# jogar o POI fora seria perder o imovel por defeito da imagem.
# So tres. `visitar_campo` e `recapturar` saíram: a primeira virava resposta
# reflexa e mandava equipe a campo por causa do CADASTRO, nao da foto; a
# segunda e decisao da triagem, que ja roda antes e nao precisa ser repetida
# aqui.
# APROVAR TEM DUAS FORMAS, e a distinção é do negócio, não da IA.
#
# `aprovar_especifico` é o comércio que a mineração já conhecia pelo nome.
# `aprovar_divergente` é comércio encontrado no endereço com OUTRO nome — o que
# o cadastro do cliente não sabe, e portanto o achado que costuma valer mais.
# Antes os dois caíam em `reprovar`, porque a pergunta era "o estabelecimento X
# está aí?" em vez de "há comércio aí?".
ACAO = ["aprovar_especifico", "aprovar_divergente", "reprovar", "revisar"]

# Cada comércio que a cena mostrar, com o nome LIDO — não o imaginado. Limitado
# a quatro: além disso o modelo passa a listar vitrine de shopping item a item e
# o custo cresce sem achado novo.
# TENTEI proibir o item fantasma pelo esquema, tirando `nao_achei` das posições
# e exigindo atividade com 6+ letras. FOI PIOR: o modelo não deixou de inventar
# o item, apenas passou a inventar TAMBÉM a posição — de `nao_achei`, que ao
# menos era honesto, para `achei_na_esquerda` sobre uma rua onde a própria
# descrição dizia "não há letreiros, vitrines ou sinais visíveis de comércio".
#
# A lição: fechar a saída de um campo não remove a alucinação, desloca-a para o
# campo fechado. `nao_achei` volta, e o que filtra item sem lugar é o decisor,
# que sabe distinguir "vi e está ali" de "acho que há algo".
COMERCIO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["nome_lido", "atividade", "posicao", "e_o_procurado"],
    "properties": {
        "nome_lido": {"type": ["string", "null"]},
        "atividade": {"type": "string"},
        "posicao": {"type": "string", "enum": POSICOES},
        "e_o_procurado": {"type": "boolean"},
    },
}

# Contagem que aceita "nao consegui contar". Um inteiro sozinho forcaria o
# modelo a escrever 0 tanto para "nao ha nenhum" quanto para "nao da para ver",
# que sao coisas diferentes e levam a decisoes diferentes.
_CONTAGEM = {"type": ["integer", "null"]}

ANALISE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["descricao", "elementos", "identificacao", "classificacao"],
    "properties": {
        "descricao": {"type": "string"},
        "elementos": {
            "type": "object", "additionalProperties": False,
            "required": ["qtd_acessos_independentes",
                         "possui_porta_cartas", "possui_ramal_aparente",
                         "possui_interfone", "pessoas_visiveis"],
            "properties": {
                "qtd_acessos_independentes": _CONTAGEM,
                "possui_porta_cartas": {"type": ["boolean", "null"]},
                "possui_ramal_aparente": {"type": ["boolean", "null"]},
                "possui_interfone": {"type": ["boolean", "null"]},
                "pessoas_visiveis": {"type": ["boolean", "null"]},
            }},
        "identificacao": {
            "type": "object", "additionalProperties": False,
            "required": ["numero_predial", "letreiro_visivel",
                         "texto_do_letreiro", "placa_comercial",
                         "telefones", "endereco_visivel", "outros_textos"],
            "properties": {
                "numero_predial": {"type": ["string", "null"]},
                "letreiro_visivel": {"type": "boolean"},
                "texto_do_letreiro": {"type": ["string", "null"]},
                "placa_comercial": {"type": ["boolean", "null"]},
                # Telefone lido na fachada vale muito: confirma o
                # estabelecimento independentemente do nome, e serve ao
                # enriquecimento — e um dado que so a IMAGEM tem.
                "telefones": {"type": "array", "items": {"type": "string"}},
                "endereco_visivel": {"type": ["string", "null"]},
                "outros_textos": {"type": "array", "items": {"type": "string"}},
            }},
        "classificacao": {
            "type": "object", "additionalProperties": False,
            "required": ["alvo_encontrado", "comercios_encontrados",
                         "posicao_na_imagem",
                         "marcador_google_visivel",
                         "tipo_cliente", "tipo_imovel", "status_ocupacao",
                         "multiplas_unidades", "limite_ambiguo",
                         "indicio_comercial",
                         "atividade_economica_aparente", "atividade_no_alvo",
                         "tipo_via", "confianca",
                         "o_que_sustenta", "ressalva"],
            "properties": {
                # ACHOU o que procuravamos? Sem este par, "o letreiro esta
                # na foto mas fora do centro" nao tinha onde ser dito, e a
                # leitura classificava o vizinho em silencio.
                # A LISTA DE TUDO QUE HÁ DE COMÉRCIO NA CENA — o achado, agora, é este.
      # `alvo_encontrado` continua respondendo "e o que a mineração trouxe,
      # apareceu?", que virou uma pergunta secundária.
      "comercios_encontrados": {"type": "array", "maxItems": 4,
                                "items": COMERCIO_SCHEMA},
      "alvo_encontrado": {"type": "string",
                                    "enum": ["sim", "compativel", "incerto",
                                             "nao"]},
                "posicao_na_imagem": {"type": "string", "enum": POSICOES},
                # O distintivo que o Google desenha no panorama, na posicao
                # onde ELE registra o estabelecimento. Confirmacao de posicao
                # vinda da propria fonte, e de graca: ja esta na imagem.
                "marcador_google_visivel": {"type": ["boolean", "null"]},
                "tipo_cliente": {"type": "string", "enum": TIPOS_CLIENTE},
                "tipo_imovel": {"type": "string", "enum": TIPOS_IMOVEL},
                "status_ocupacao": {"type": "string", "enum": OCUPACAO},
                "multiplas_unidades": {"type": ["boolean", "null"]},
                # A regra 5 manda SINALIZAR ambiguidade em vez de resolve-la no
                # chute. Sem este campo, "nao sei separar o alvo do vizinho"
                # nao teria onde ser dito, e viraria uma classificacao confiante.
                "limite_ambiguo": {"type": ["boolean", "null"]},
                "indicio_comercial": {"type": ["boolean", "null"]},
                "atividade_economica_aparente": {"type": "string",
                                                 "enum": ATIVIDADE},
                "atividade_no_alvo": {"type": "string", "enum": ONDE},
                "tipo_via": {"type": "string", "enum": VIA},
                "confianca": {"type": "number"},
                          "o_que_sustenta": {"type": "string"},
                "ressalva": {"type": ["string", "null"]},
            }},
    },
}


# NADA DE CODIGO CORRIGINDO JULGAMENTO DA IA.
#
# Havia aqui duas funcoes que sobrescreviam decisoes do modelo: uma promovia
# `compativel` para `sim` quando o nome batia, outra trocava o veredito de uma
# foto pelo texto lido. Foram removidas por decisao do usuario, e a razao e
# solida: comparar nome e julgar foto sao JULGAMENTO, e codigo sem contexto
# nao pode arbitrar sobre isso — mascara o erro em vez de corrigi-lo, e o erro
# volta escondido.
#
# A fronteira que fica: codigo faz ARITMETICA e ROTEAMENTO (ver
# `julgar_triagem`: somar nove pesos e aplicar faixas nao exige discernimento, e
# pedir isso ao modelo produziu "aprovada com nota 0"). Julgamento e da IA — e
# quando precisar de revisao, quem revisa e outra passada de IA.


# ──────────────────────────────────────────────────────────────────────────
# PASSO 5 — REVISOR
#
# Substitui as correcoes que estavam em codigo. A regra do projeto passou a ser:
# codigo faz aritmetica; quem revisa JULGAMENTO e outra passada de IA, que tem
# a imagem na frente e pode discordar com fundamento — coisa que uma funcao
# comparando strings nao pode.
#
# O revisor recebe a imagem, o contexto e a leitura anterior. Ele NAO refaz a
# analise: procura contradicao entre o que foi escrito e o que a foto mostra.
# Separar revisao de producao e o mesmo motivo de separar percepcao de juizo —
# quem escreveu uma resposta tende a defende-la.
# ──────────────────────────────────────────────────────────────────────────

REVISOR_SISTEMA = """Você revisa a leitura que outro avaliador fez desta foto. \
Você tem a imagem na frente e a resposta dele.

Não refaça a análise do zero. Procure CONTRADIÇÃO — entre o que ele escreveu e o \
que a foto mostra, ou entre dois campos da própria resposta dele. Onde não \
houver contradição, confirme.

O que checar, nesta ordem:

1. A DESCRIÇÃO BATE COM A FOTO? Se ele descreveu algo que não está lá, ou deixou \
de fora algo determinante — letreiro, movimento, veículo, terreno —, corrija.

2. A CLASSIFICAÇÃO SE SUSTENTA NA DESCRIÇÃO DELE? `abandonada`, `vago_aparente` \
e `terreno_vazio` exigem sinal positivo. Casa conservada não é abandonada só \
porque o comércio procurado não apareceu.

3. `alvo_encontrado` E `posicao_na_imagem` SÃO COERENTES? Quem respondeu `nao` \
não pode dizer onde achou. Quem achou tem de dizer onde. E se o letreiro que ele \
mesmo transcreveu é o nome procurado, então é `sim`, não `compativel`.

4. A AÇÃO SEGUE O CRITÉRIO? `visitar_campo` exige sinal de atividade econômica \
VISÍVEL NA FOTO — CNPJ ativo, nota alta e comentário recente NÃO bastam. Sem \
indício na imagem, é `aprovar` com a classificação que a foto sustenta.

5. AS CONTAGENS SÃO HONESTAS? `0` afirma que não existe; `null` diz que não deu \
para contar. Onde a foto não mostra a lateral ou o muro, contagem é `null`.

Devolva a resposta REVISADA por inteiro, no mesmo formato, e liste em \
`correcoes` o que você mudou e por quê — uma linha por mudança, no máximo 20 \
palavras cada. Se nada mudou, `correcoes` é lista vazia e `houve_correcao` é \
false. Não invente correção para parecer útil: confirmar é resultado legítimo."""


def revisor_usuario(alvo: dict, leitura: dict) -> str:
    import json as _j
    return (analise_usuario(alvo)
            + "\n\n--- LEITURA DO PRIMEIRO AVALIADOR ---\n"
            + _j.dumps(leitura, ensure_ascii=False, indent=1)
            + "\n\nRevise. Confirme o que está certo, corrija o que contradiz a foto.")


REVISOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["houve_correcao", "correcoes", "descricao", "elementos",
                 "identificacao", "classificacao"],
    "properties": {
        "houve_correcao": {"type": "boolean"},
        "correcoes": {"type": "array", "items": {"type": "string"}},
        "descricao": ANALISE_SCHEMA["properties"]["descricao"],
        "elementos": ANALISE_SCHEMA["properties"]["elementos"],
        "identificacao": ANALISE_SCHEMA["properties"]["identificacao"],
        "classificacao": ANALISE_SCHEMA["properties"]["classificacao"],
    },
}


# ---------------------------------------------------------------------------
# O DECISOR — a ação, isolada de quem olhou a imagem
#
# Existe porque a mesma chamada que olhava a cena não conseguia mapear o que
# via para a ação: em 78 de 78 casos ela listou comércio e respondeu
# `reprovar`, arrastada pela pergunta "o estabelecimento procurado está aí?".
#
# É o mesmo padrão que já consertou o veredito visual neste projeto —
# percepção cega, julgamento isolado. Aqui o julgamento NÃO recebe imagem
# nenhuma: recebe o relato estruturado e decide sobre ele. Sem a foto na
# frente, não há como recair na busca pelo nome.
# ---------------------------------------------------------------------------
DECISOR_SISTEMA = """Você recebe o RELATO de quem examinou a fachada de um imóvel — nunca a imagem. Sua única tarefa é converter esse relato em uma ação.

O produto existe para achar comércio que o cadastro do cliente não conhece. Portanto o que decide é HAVER COMÉRCIO, não haver AQUELE comércio.

· `aprovar_especifico` — a lista tem algum item com `e_o_procurado` verdadeiro.
· `aprovar_divergente` — a lista tem itens, e NENHUM é o procurado. É achado, não falha: é justamente o comércio que o cadastro desconhece.
· `reprovar` — a lista está vazia, OU o que ela traz não sustenta um estabelecimento: item sem nome legível E com atividade genérica ("comércio", "loja") é palpite, não achado. Comércio que existe tem nome, ramo específico ou posição — pelo menos uma dessas três coisas.
· `revisar` — a lista tem itens mas o relato os põe em dúvida: diz que o letreiro pode ser do vizinho, que o limite entre imóveis é ambíguo, ou que a imagem não sustenta o que foi listado.

A lista mandar não é opinião sua. Lista com item e resposta `reprovar` é contradição — o relato afirmou ver comércio. `revisar` também não serve para "achei comércio mas não o procurado": isso é `aprovar_divergente`.

Não reavalie a cena, não questione se aquilo é mesmo comércio, não use o nome procurado para nada além de escolher entre as duas aprovações. Você não viu a foto; quem viu já relatou.

O ENDEREÇO NÃO CONFERIR NÃO MUDA A AÇÃO. Se o relato disser que a rua ou o número mostrado é outro, isso é ressalva sobre ONDE a foto foi tirada — não sobre o que há nela. A ação continua saindo da LISTA.

Eu já tinha escrito aqui que endereço divergente virava `revisar`, e estava errado: um mesmo caso — comércio listado, endereço em dúvida — saía `revisar` enquanto outro idêntico saía `aprovar_divergente`, e essa inconsistência é pior que qualquer das duas respostas. Critério que muda de caso para caso não é critério.

RESPONDA A AÇÃO PRIMEIRO. `por_que` vem depois e cabe em UMA FRASE curta — é registro do critério aplicado, não espaço para raciocinar. Raciocínio longo ali dentro faz a resposta estourar o limite e a ação se perder."""

DECISOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    # A AÇÃO PRIMEIRO, e não é estética: o modelo emitiu `por_que` antes,
    # gastou 152 tokens argumentando — chegando à conclusão certa dentro do
    # texto — e o teto cortou a resposta antes do campo que importa. Campo
    # decisivo na frente sai mesmo quando o resto não cabe.
    "required": ["acao_recomendada", "por_que"],
    "properties": {
        "acao_recomendada": {"type": "string", "enum": ACAO},
        # Uma frase. Sem o limite ele escreve o raciocínio inteiro no campo
        # errado, e foi assim que a ação se perdeu.
        "por_que": {"type": "string", "maxLength": 180},
    },
}


def decisor_usuario(o: dict) -> str:
    """O relato, sem a imagem. Só o que a análise afirmou."""
    c = (o or {}).get("classificacao") or {}
    lista = c.get("comercios_encontrados") or []
    # A DESCRIÇÃO EM PROSA NÃO ENTRA, e a medição é clara: com ela o decisor
    # respondeu `reprovar` três vezes em três sobre uma cena com quatro
    # comércios listados; sem ela, `aprovar_divergente` três em três. A prosa
    # carrega frases como "não há sinal do estabelecimento procurado", e o
    # julgamento obedecia à frase em vez da lista. Chamar isso de julgamento
    # isolado era falso: eu entregava a ele o próprio viés que a separação
    # existia para evitar.
    # SÓ A LISTA E A AMBIGUIDADE. Nada mais entra.
    #
    # Fui cortando por medição, e cada corte veio de um caso concreto:
    #   · a DESCRIÇÃO em prosa fazia `reprovar` 3 de 3 sobre cena com quatro
    #     comércios listados — ela contém "não há sinal do procurado";
    #   · a RESSALVA fazia o mesmo em miniatura ("o imóvel do alvo não é
    #     identificado") e derrubou o POI 177525, que tinha MASTER CELL, CEC
    #     LABORATÓRIO e PICCADILLY na lista;
    #   · `alvo_encontrado` e `indicio_comercial` são redundantes e piores: quem
    #     é o procurado já está em `e_o_procurado` de cada item, e um
    #     `indicio_comercial: False` ao lado de três comércios listados é
    #     contradição que o julgamento herda.
    #
    # O que sobrou é o que a regra usa. Todo o resto falava do ALVO, e falar do
    # alvo é exatamente o viés que separar as etapas existia para eliminar.
    t = [f"COMÉRCIOS LISTADOS: {len(lista)}"]
    for x in lista:
        marca = "É O PROCURADO" if x.get("e_o_procurado") else "outro"
        t.append(f"  · {x.get('nome_lido') or '(sem nome legível)'} — "
                 f"{x.get('atividade') or 'atividade não dita'} "
                 f"[{x.get('posicao') or '?'}] — {marca}")
    if not lista:
        t.append("  (nenhum)")
    t += ["", f"Limite entre imóveis ambíguo? {c.get('limite_ambiguo')}",
          "", "Qual a ação?"]
    return chr(10).join(t)
