# -*- coding: utf-8 -*-
"""prompts_estrutura.py — o que NÃO tem cara de moradia.

A pergunta mudou de lugar. Até aqui a leitura procurava letreiro: quem tem placa
é comércio, quem não tem não é. Isso deixa de fora justamente o que interessa
numa base de saneamento — o galpão sem placa, a oficina de portão metálico, o
templo em prédio adaptado, a fábrica de fundo de lote. Todos consomem água de
categoria não residencial e nenhum se anuncia.

Aqui o modelo é perguntado sobre ESTRUTURA. Um prédio de seis pavimentos não é
casa mesmo sem letreiro. Um portão de 4 m com folha metálica corrida não é
garagem de família. Vitrine de vidro do chão ao teto não é janela de sala.

O nome, quando houver, é bônus — não é o critério. Só depois de ele dizer O QUE
VÊ é que se pergunta se aquilo é o estabelecimento que a mineração trouxe.
"""

# Os tipos que interessam. `moradia` está na lista de propósito: sem a opção de
# dizer "isto é casa", o modelo empurra tudo para alguma categoria não
# residencial, que é o viés que este prompt existe para evitar.
TIPOS_ESTRUTURA = [
    "comercio_varejo",        # loja, vitrine, mercadoria exposta
    "comercio_servico",       # clínica, escritório, salão, oficina
    "galpao_deposito",        # pé-direito alto, portão de carga, telha metálica
    "industria",              # chaminé, silo, pátio de manobra, galpão em série
    "governamental",          # escola, posto, repartição, quartel
    "religioso",              # templo, salão de culto
    "hotelaria_alimentacao",  # hotel, restaurante, bar, padaria
    "predio_multiandar",      # torre residencial ou mista, 4+ pavimentos
    "moradia",                # casa ou sobrado de padrão residencial
    "terreno_ou_ruina",
    "nao_da_para_dizer",
]

# A EVIDÊNCIA VAI EM TEXTO LIVRE, e a primeira versão disto era uma lista
# fechada de indícios. Foi pior: o modelo parou de olhar e passou a escolher
# rótulos — devolveu `portao_de_carga` repetido seis vezes no mesmo item, e
# sustentou `moradia` com `telha_metalica_ou_galpao`. Caixa para marcar convida
# a marcar caixa.
#
# Descrevendo com as próprias palavras o que naquela construção não é de casa,
# ele precisa OLHAR para escrever. E o texto fica auditável: "portão de chapa
# corrida com uns 4 m, sem recuo" é conferível na foto; `portao_largo_metalico`
# não é.

ESTRUTURA_SISTEMA = """\
Você examina fotos de rua para uma concessionária de saneamento. A pergunta NÃO \
é "que loja é esta". A pergunta é: NESTA CENA, QUE CONSTRUÇÕES NÃO TÊM CARA DE \
MORADIA?

Por que assim: quem cobra água precisa achar o que consome fora do padrão \
residencial — galpão, oficina, templo, escola, fábrica, clínica. Boa parte \
disso NÃO tem letreiro. Um galpão de portão metálico corrido não se anuncia, e \
mesmo assim não é casa.

O QUE COSTUMA DENUNCIAR UMA CONSTRUÇÃO NÃO RESIDENCIAL, mesmo sem nome — são exemplos para calibrar o olho, NÃO uma lista para marcar:

· PORTÃO. Casa tem portão de garagem, com recuo, do tamanho de um carro. Folha metálica corrida de 3 m ou mais, rente à calçada, é acesso de carga ou oficina. Na altura de caminhão, é doca.
· ABERTURA. Casa tem janela com peitoril. Vidro do chão ao teto é vitrine. Porta de enrolar é loja. Térreo sem janela nenhuma é depósito.
· ALTURA E VOLUME. Quatro pavimentos ou mais não é casa. Pé-direito de dois andares num pavimento só é galpão.
· COBERTURA. Telha metálica em vão largo é galpão ou indústria; telha cerâmica em água dupla é moradia.
· ENTORNO. Pátio de manobra, vaga demarcada, utilitário com logotipo, fila de gente, caixa d'água industrial, antena, chaminé.
· SÍMBOLO. Cruz, torre, vitral, brasão, bandeira oficial, placa de horário.

COMO RESPONDER

Liste em `estruturas` cada construção da cena que NÃO é moradia. Para cada uma diga onde está, que tipo parece ser, e — o campo que importa — DESCREVA COM AS SUAS PALAVRAS o que você está vendo que não é de casa.

Escreva o que os olhos pegam, concreto e conferível: "portão de chapa metálica corrida, uns 4 m de largura, rente à calçada, sem recuo de garagem"; "térreo com vidro do chão ao teto e prateleiras visíveis dentro"; "telhado de zinco em vão único, mais alto que as casas ao lado". Quem lê depois tem de conseguir achar aquilo na foto.

Descreva CADA construção pelo que ela tem de próprio. Repetir a mesma frase \nem itens diferentes — "telhado de telhas vermelhas" em três lojas seguidas — é \nsinal de que você parou de olhar e passou a copiar.

Não escreva conclusão no lugar de observação. "Parece comercial", "estrutura atípica" e "indica atividade econômica" não dizem o que você viu — são o veredito outra vez, com outras palavras.

Diga o nome só se conseguir LER. `nome_lido: null` é resposta boa e frequente: o objetivo é achar a construção, não batizá-la.

OBRA E INFRAESTRUTURA NÃO SÃO ESTABELECIMENTO. Viaduto, ponte, passarela, muro de arrimo, poste, torre de energia, caixa d'água pública e canteiro não têm dono que paga água comercial. Não importa o quanto sejam grandes ou de concreto: não são item.

`governamental` EXIGE SINAL DE ÓRGÃO PÚBLICO — brasão, bandeira, nome de secretaria, escola, posto de saúde, delegacia, placa de horário de atendimento ao público. Prédio grande e sóbrio NÃO é governamental; é `predio_multiandar` quando for torre, `moradia` quando for residencial. Na dúvida entre público e privado, `nao_da_para_dizer` — que é resposta, não desistência.

PUBLICIDADE NÃO É ESTABELECIMENTO. Muro, tapume, cerca, poste e placa solta com o nome de uma loja pintado são anúncio, não a loja. Um muro de contenção com a marca escrita nele é tinta sobre concreto — o estabelecimento pode estar a quilômetros dali.

MAS ESTACIONAMENTO É EMPRESA. Pátio de estacionamento pago é atividade econômica como qualquer outra, e entra como `comercio_servico`: guarita, cancela, vagas demarcadas, placa de preço por hora, manobrista. Não confunda com a garagem de um prédio nem com carro parado na rua.

ESCOLHA O TIPO PELO QUE DESCREVEU. Não jogue tudo em `comercio_servico` — mas
também não distribua por distribuir: mandei "use a lista inteira" numa versão
anterior e o resultado foi `governamental` em viaduto, em muro de contenção e em
prédio de apartamentos. Espalhar rótulo não é classificar. Se o seu texto diz "galpão", "telhado de zinco em vão
único", "portão de carga", o tipo é `galpao_deposito`. Vitrine com mercadoria e
porta de enrolar é `comercio_varejo`. Farmácia, mercado e loja são varejo;
clínica, escritório, salão e oficina são serviço. Banco é serviço. Escola e
posto de saúde são `governamental`.

CONFIRA O TIPO CONTRA O QUE VOCÊ ESCREVEU. Se o `o_que_vejo` fala em varal de roupa, telha cerâmica e portão de carro, o tipo é `moradia` — não `galpao`. Se fala em muro liso de concreto, não é `comercio`. O tipo tem de sair da observação; quando os dois discordam, quem manda é o que você viu.

`moradia` e `terreno_ou_ruina` também entram na lista quando forem o imóvel \
central da cena — é assim que se sabe que você olhou e concluiu, em vez de ter \
pulado. Mas não liste toda casa da rua: só a do centro.

NÃO INVENTE. Se a cena é uma rua de casas e nada mais, a lista tem uma entrada \
`moradia` e acabou. Construção sem nenhum indício da lista não entra — "parece \
comercial" não é indício, é palpite. Um item com zero indícios é um item que \
você não viu.

Descreva antes de listar: a descrição é a prova do resto, e o que ela não \
mencionar não pode aparecer na lista."""

ESTRUTURA_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["descricao", "estruturas"],
    "properties": {
        "descricao": {"type": "string"},
        "estruturas": {
            "type": "array", "maxItems": 6,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["posicao", "tipo", "o_que_vejo", "nome_lido",
                             "pavimentos", "confianca"],
                "properties": {
                    "posicao": {"type": "string",
                                "enum": ["esquerda", "centro", "direita"]},
                    "tipo": {"type": "string", "enum": TIPOS_ESTRUTURA},
                    # O QUE ELE VÊ, com as palavras dele. Mínimo de 25
                    # caracteres para não virar "é comercial" — que é conclusão
                    # repetida, não observação.
                    "o_que_vejo": {"type": "string", "minLength": 25,
                                   "maxLength": 420},
                    "nome_lido": {"type": ["string", "null"]},
                    "pavimentos": {"type": ["integer", "null"]},
                    "confianca": {"type": "number"},
                },
            },
        },
    },
}


def estrutura_usuario(n_fotos: int = 1, passo: int | None = None) -> str:
    """O turno do usuário. NÃO recebe o nome do estabelecimento procurado.

    De propósito: o nome contamina. Enquanto a leitura sabia o que procurar,
    ela respondia "não há sinal de clínica" sobre uma rua com quatro
    comércios. Aqui ele não sabe o que se procura — só olha e diz o que vê.

    E DIZ QUANTAS FOTOS SÃO, do mesmo ponto. Sem essa frase o modelo lista o
    mesmo prédio uma vez por foto: medido em 18/08/2026, MALVADÃO apareceu
    cinco vezes e o prédio do JPAC outras cinco, com a confiança caindo de
    0,9 a 0,4 — ele relistava a mesma coisa e sabia menos a cada vez.
    """
    if n_fotos <= 1:
        return ("Que construções desta cena NÃO têm cara de moradia? Para "
                "cada uma, descreva o que você está vendo que não é de casa.")
    giro = f" de {passo}° em {passo}°" if passo else ""
    return (
        f"São {n_fotos} FOTOS DO MESMO PONTO DA RUA, tiradas girando a "
        f"câmera{giro}. A primeira encara o endereço avaliado." + "\n\n"
        "Como é o mesmo lugar visto de ângulos diferentes, A MESMA "
        "CONSTRUÇÃO APARECE EM VÁRIAS FOTOS. Liste cada uma UMA ÚNICA VEZ, "
        "com a melhor descrição que conseguir juntando o que vê nas várias. "
        "Repetir um prédio a cada ângulo não é encontrar mais coisas — é "
        "contar a mesma coisa de novo, e no fim vira cadastro duplicado." + "\n\n"
        "Que construções há aqui que NÃO têm cara de moradia? Para cada uma, "
        "descreva o que você está vendo que não é de casa.")


# ---------------------------------------------------------------------------
# A MESMA CONSTRUÇÃO, VISTA DE DOIS ÂNGULOS — dedupe por código
#
# Tentei resolver isto no prompt, avisando que as fotos são do mesmo ponto e que
# repetir prédio é erro. Não resolveu: o JPAC continuou listando seis vezes o
# mesmo edifício branco. Errei o diagnóstico ao culpar a instrução.
#
# A causa é outra e não tem conserto por texto: quando ele NÃO consegue ler o
# nome, a descrição sai genérica — "edifício de múltiplos andares, fachada
# branca" — e duas descrições genéricas do mesmo prédio são indistinguíveis
# PARA ELE. Falta âncora, não falta instrução.
#
# Por código dá: nome normalizado quando houver, e sobreposição de vocabulário
# da descrição quando não houver. Isto não é código corrigindo juízo da IA — é
# código percebendo que ela disse a mesma coisa duas vezes.
# ---------------------------------------------------------------------------
_STOP = {"um", "uma", "de", "da", "do", "com", "e", "o", "a", "em", "no", "na",
         "que", "para", "por", "os", "as", "dos", "das", "ao", "à", "se", "há",
         "tem", "está", "sao", "são", "seu", "sua", "mais", "muito", "pelo"}


def _sacola(texto: str) -> set:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower())
                if unicodedata.category(c) != "Mn")
    return {p for p in "".join(ch if ch.isalnum() else " " for ch in s).split()
            if len(p) > 2 and p not in _STOP}


def _nome_chave(s: str | None) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                if unicodedata.category(c) != "Mn")
    return " ".join(s.split())


def mesma_construcao(a: dict, b: dict, corte: float = 0.62) -> bool:
    """Dois itens descrevem a mesma construção?

    Com nome nos dois, o nome decide — é a âncora mais forte que existe aqui.
    Sem nome, compara o VOCABULÁRIO das descrições: duas frases sobre o mesmo
    prédio compartilham "fachada", "branca", "andares", "janelas"; duas sobre
    prédios diferentes divergem no substantivo concreto.

    Tipo diferente não impede a fusão — o mesmo galpão saiu `galpao_deposito`
    num ângulo e `comercio_servico` noutro, e continuar contando dois seria
    preservar a inconsistência em vez de resolvê-la.
    """
    na, nb = _nome_chave(a.get("nome_lido")), _nome_chave(b.get("nome_lido"))
    if na and nb:
        return na == nb or na in nb or nb in na
    if na or nb:
        return False        # um tem nome e o outro não: são coisas distintas
    sa, sb = _sacola(a.get("o_que_vejo")), _sacola(b.get("o_que_vejo"))
    if not sa or not sb:
        return False
    return len(sa & sb) / len(sa | sb) >= corte


def sem_repetidas(estruturas: list) -> list:
    """A lista sem a mesma construção duas vezes, ficando a MELHOR descrição.

    Melhor = a mais longa entre as que se fundiram, porque descrição mais longa
    é a que juntou mais detalhe de mais ângulos. O nome sobrevive mesmo que
    tenha vindo só de um dos itens — foi lido em algum lugar.
    """
    saida = []
    for x in estruturas or []:
        alvo = next((y for y in saida if mesma_construcao(x, y)), None)
        if alvo is None:
            saida.append(dict(x))
            continue
        if len(x.get("o_que_vejo") or "") > len(alvo.get("o_que_vejo") or ""):
            alvo["o_que_vejo"] = x["o_que_vejo"]
            alvo["tipo"] = x["tipo"]
        if not alvo.get("nome_lido") and x.get("nome_lido"):
            alvo["nome_lido"] = x["nome_lido"]
        alvo["confianca"] = max(alvo.get("confianca") or 0, x.get("confianca") or 0)
    return saida


# ---------------------------------------------------------------------------
# O VEREDITO — casar o que foi visto com o que se procurava
#
# Etapa separada e SEM IMAGEM, pelo mesmo motivo que já se provou: quando a
# mesma chamada olhava a cena e decidia a ação, a pergunta "o estabelecimento X
# está aí?" contaminava o olhar — ela listava três comércios e concluía que não
# havia comércio. Aqui a percepção já terminou; o que resta é comparar duas
# listas de texto.
# ---------------------------------------------------------------------------
ACAO_ESTRUTURA = [
    "aprovado_especifico",   # o que se procurava está lá
    "aprovado_com_outras",   # não achou o procurado, mas há construção não residencial
    "revisar",               # há dúvida que só pessoa resolve
    "reprovado",             # só moradia, terreno ou nada
]

VEREDITO_SISTEMA = """Você recebe DUAS coisas: o que o cadastro dizia haver num endereço, e a lista de construções que outra etapa identificou na cena — com a observação que sustenta cada uma. Você não vê foto nenhuma. Sua tarefa é dizer o que fazer com o ponto.

· `aprovado_especifico` — alguma construção da lista É o que se procurava. Vale o nome igual ou quase (grafia, abreviação, nome de fantasia), e vale também a ATIVIDADE bater com a categoria do cadastro no mesmo lugar: cadastro diz "dentista" e a lista traz clínica odontológica sem nome no centro da cena — é o mesmo.

· `aprovado_com_outras` — a lista tem construção não residencial, mas NENHUMA corresponde ao que se procurava. Isto NÃO é falha: é o achado que o cadastro do cliente não conhece, e costuma valer mais que o específico. Galpão sem placa, oficina de portão metálico, templo — tudo conta.

· `reprovado` — a lista só tem `moradia`, `terreno_ou_ruina`, ou está vazia. Nenhuma atividade econômica na cena.

· `revisar` — há construção não residencial, mas o que a sustenta é fraco demais para decidir: só `nao_da_para_dizer`, ou observação que não descreve nada de concreto. Não use `revisar` porque o procurado não apareceu — isso é `aprovado_com_outras`.

REGRA QUE NÃO SE DOBRA: se a lista tem ao menos uma construção que não é moradia nem terreno, a resposta NÃO pode ser `reprovado`. Afirmar que não há atividade quando a etapa anterior descreveu um galpão é contradizer o que já foi visto.

`corresponde` é o NÚMERO entre colchetes da construção que é a procurada, ou `null` quando nenhuma for. Responda a ação primeiro; `por_que` cabe em uma frase."""

VEREDITO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["acao", "corresponde", "por_que"],
    "properties": {
        "acao": {"type": "string", "enum": ACAO_ESTRUTURA},
        # QUAL construção é a procurada, pelo número que aparece na lista
        # (começa em 1). Sem isto, "aprovado_especifico" não diz de qual das
        # seis se fala, e a fila do supervisor não teria o que mostrar.
        "corresponde": {"type": ["integer", "null"]},
        "por_que": {"type": "string", "maxLength": 200},
    },
}


def veredito_usuario(alvo: dict, estruturas: list) -> str:
    """O cadastro de um lado, o que foi visto do outro. Nada além disso.

    Sem descrição em prosa da cena e sem ressalva: os dois já derrubaram o
    julgamento antes, porque carregam frases sobre a AUSÊNCIA do procurado e o
    decisor obedecia à frase em vez da lista.
    """
    t = [f"O CADASTRO DIZ QUE AQUI HÁ: {alvo.get('nome') or '(sem nome)'}"]
    if alvo.get("categoria"):
        t.append(f"CATEGORIA NO CADASTRO: {alvo['categoria']}")
    if alvo.get("endereco"):
        t.append(f"ENDEREÇO: {alvo['endereco']}")
    t += ["", f"CONSTRUÇÕES IDENTIFICADAS NA CENA: {len(estruturas)}"]
    # NUMERA DE 1. O modelo devolvia 1 para o único item de uma lista de um,
    # ou seja, contava a partir de 1 por conta própria. Alinhar o texto ao que
    # ele faz custa nada; brigar custaria uma correção por código em cima de
    # cada resposta.
    for n, x in enumerate(estruturas, 1):
        t.append(f"  [{n}] {x.get('tipo')} · "
                 f"{x.get('nome_lido') or 'sem nome legível'} · "
                 f"{x.get('posicao')}")
        t.append(f"      {x.get('o_que_vejo')}")
    if not estruturas:
        t.append("  (nenhuma)")
    t += ["", "Qual a ação?"]
    return chr(10).join(t)
