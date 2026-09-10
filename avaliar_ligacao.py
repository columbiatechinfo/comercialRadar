# -*- coding: utf-8 -*-
"""avaliar_ligacao.py — um veredito por HIDROMETRO, com todas as testemunhas.

POR QUE ESTE MODULO EXISTE, e por que o `avaliar_ia` nao bastava:

O `avaliar_ia` julga POI. Isso parecia certo ate 08/09/2026, quando o dono do
produto apontou o nivel errado: "o foco e a instalacao, nao os POIs; eles
enriquecem a instalacao a que se fundem, dando confiabilidade a ela".

A medicao deu razao a ele. 91.199 ligacoes tem POI vinculado, 77.564 delas
(85 por cento) tem MAIS DE UM, e 18.249 carregam vereditos que se
contradizem — 11.442 com aprovado e revisao ao mesmo tempo, 1.889 com aprovado
e REPROVADO sobre o mesmo medidor.

O caso que abriu a investigacao mostra por que: a Madeireira Maravilha aparece
seis vezes na base. A Receita traz o CNPJ, o Maps traz nota 4,0 com 162
avaliacoes, o IBGE e o estadual trazem o endereco. NENHUM POI tinha tudo, e
cada um foi julgado com o pedaco que trouxe. Um aprovou, o outro mandou para
revisao, e o mais rico — o do Maps — nem tinha sido julgado.

Aqui as seis testemunhas entram na mesma sala, e a resposta e uma.

O QUE ESTE MODULO NAO FAZ: nao apaga, nao funde e nao escolhe um POI
vencedor. `poi_veredito` continua valendo como o que a IA observou em cada
pedaco, e entra no dossie como evidencia — decisao do dono do produto no mesmo
dia. O que muda e onde mora a RESPOSTA.
"""
import argparse
import json
import threading
import time

import avaliar_ia as ia
import base_comum as bc
import descrever_imagens as di
import dossie_ligacao as dl
import setor
import imagens

MODELO_PADRAO = ia.MODELO_PADRAO
TIMEOUT = ia.TIMEOUT
VEREDITOS = ia.VEREDITOS

#: A escala e a MESMA do `avaliar_ia`, de proposito. O painel ja pinta por ela,
#: o operador ja a leu mil vezes, e o que mudou foi o sujeito do julgamento —
#: nao o vocabulario dele.
#: A ORDEM DAS PARTES E ESCOLHIDA PELO CACHE DE PREFIXO.
#:
#: O DOSSIE VEM NO FIM, e nao no meio. O vLLM guarda o calculo do prefixo
#: comum entre requisicoes, mas so ate o primeiro token que muda — dai em
#: diante recalcula tudo.
#:
#: A PRIMEIRA VERSAO DESTE MODULO punha `%(dossie)s` antes do bloco de regras,
#: que tem 8.685 caracteres identicos em toda chamada. Medido na fila real:
#: ZERO por cento de acerto de cache, contra os 42 por cento que o
#: `avaliar_ia` tira do mesmo modelo. Foi o mesmo erro que eu havia corrigido
#: nele em 07/09 e documentado; escrevi o modulo novo sem aplicar.
#:
#: Agora todo o texto invariavel vem primeiro — a explicacao, as regras de
#: pesar testemunhas, as regras de julgar e o esquema do JSON — e so entao o
#: dossie e a lista de imagens, que mudam a cada hidrometro.
#: O ANO DE HOJE, para a regra da foto vencida. Vem do relogio e nao de uma
#: constante escrita a mao: uma constante congelada faria a regra apodrecer em
#: silencio na virada do ano, tratando foto de 2026 como "do ano atual" em 2027.
def _ano_de_hoje():
    import datetime
    return datetime.date.today().year


#: AS REGRAS DE JULGAR DA LIGACAO, que NAO sao as do julgamento por POI.
#:
#: O DEFEITO QUE ISTO CORRIGE, medido em 08/09/2026 sobre as 3.966 primeiras
#: ligacoes julgadas: 3.277 delas — 82,6%% — sairam como "revisao_humana", e a
#: justificativa que mais se repetia era sempre a mesma forma, "as imagens
#: mostram uma residencia comum... a unica fonte (Receita) aponta". A IA nao
#: estava em duvida: ela estava obedecendo uma regra que mandava a foto decidir.
#:
#: E a regra estava certa NO LUGAR ONDE NASCEU. `avaliar_ia` julga UM POI com
#: quatro fotos e nada mais; ali a foto e toda a prova que existe, e "so aprova
#: o que estiver na fachada" e a unica salvaguarda possivel. Aqui o julgamento e
#: outro: chegam cinco bases independentes sobre o mesmo hidrometro, com CNPJ,
#: telefone, horario, nota e avaliacao datada. Herdar a regra da foto soberana
#: era deixar a testemunha mais fraca calar as outras cinco.
#:
#: Decisao do dono do produto em 08/09/2026, textual: "as imagens nao podem
#: determinar o veredito final, sao uma parte do veredito; se tiver fontes com
#: dados suficientes e a imagem nao for do ano atual nao pode reprovar ou mandar
#: pra avaliacao, ela tem que ter autonomia pra aprovar o que tem dados pra
#: isso".
#:
#: O QUE FICA DE PE: a classificacao (CNEFE, CNAE) e a pergunta
#: `sinal_no_imovel` continuam vindo de `avaliar_ia`, recortadas em tempo de
#: execucao. Sao as mesmas perguntas, e duplicar o texto delas aqui criaria as
#: duas versoes que aquele modulo existe para evitar.
def _regras_da_ligacao(secoes):
    ano = _ano_de_hoje()
    # O RECORTE COMECA EM "FOTO DE OFICIO": dali para a frente o texto de
    # `avaliar_ia` fala de `sinal_no_imovel` e de classificacao, que valem
    # igual nos dois julgamentos. O que vem ANTES fala da foto como prova
    # unica, e e justamente o que esta sendo substituido.
    herdado = ia._regras_de_julgar(secoes)
    corte = herdado.find("FOTO DE OFÍCIO NÃO PROVA ENDEREÇO")
    if corte <= 0:
        # FALHA QUE NAO AVISA E PIOR QUE ERRO. Este `find` procura um titulo
        # DENTRO do texto de outro modulo; no dia em que alguem reescrever
        # aquele titulo — como aconteceu em 10/09/2026, ao acentua-lo — o
        # corte volta -1, a cauda some e o prompt perde as regras de
        # `sinal_no_imovel` e de classificacao SEM UMA LINHA NO LOG.
        raise RuntimeError(
            "o corte do bloco herdado nao encontrou %r em avaliar_ia."
            "_regras_de_julgar — o titulo mudou de um lado so" % ("FOTO DE OFÍCIO NÃO PROVA ENDEREÇO",))
    cauda = herdado[corte:]
    return (_REGRAS_DA_LIGACAO % {"ano": ano, "ano_passado": ano - 1}) + (
        "\n\n" + cauda if cauda else "")


#: O PROMPT DEFINE OS STATUS, e este bloco NAO os redefine.
#:
#: Ate 09/09/2026 ele abria com "ESCOLHA UM VEREDITO" e listava os
#: quatro, incluindo `revisao_humana`. O prompt novo tem dois status e
#: proibe a duvida — e o bloco continuava oferecendo a terceira porta,
#: tres vezes, no meio do mesmo texto. Instrucao que se contradiz nao e
#: instrucao: o modelo escolhe a metade que quiser.
#:
#: O que sobra aqui e o que o prompt NAO diz: como pesar a foto contra
#: as fontes, e o que conta como fonte suficiente.
_REGRAS_DA_LIGACAO = """A IMAGEM É UMA TESTEMUNHA, E NÃO O JUIZ — e esta é a regra que manda sobre
todas as outras deste bloco.

Você recebe de três a seis provas de naturezas diferentes: bases de registro,
plataformas com data, avaliações de clientes assinadas, e fotos. A foto de rua
é UMA delas. Ela responde bem uma pergunta — "o que estava pendurado na
fachada no dia em que o carro passou" — e responde mal todas as outras.

O QUE A FACHADA NÃO SABE:
- não sabe o que funciona nos fundos, no sobrado ou dentro de casa;
- não sabe o que abriu depois que a foto foi tirada;
- não sabe de negócio que opera sem placa, que é o mais comum do bairro:
  costureira, doceira, manicure, marmita, oficina, aluguel de temporada.

ENTÃO: fachada muda NÃO É CONTRAPROVA. Ela não contradiz fonte nenhuma — ela
simplesmente não viu.

E FOTO QUE NÃO EXISTE É MENOS AINDA. Quando o dossiê diz que nenhuma imagem o
acompanha, isso NÃO é motivo para devolver a dúvida: é só a captura que ainda
não passou por este endereço. "Não há foto para confirmar" é a frase que você
está proibido de usar como justificativa — ela descreve o estado do nosso
trabalho, e não o estado do imóvel. Decida pelas fontes, que é o que você
recebeu. Uma casa comum na foto é o estado esperado da maioria dos comércios
que este projeto procura, e não um sinal contra eles.

A FOTO TEM DATA, E A DATA ESTÁ NO DOSSIÊ. Use-a assim, e esta é a regra que
mais muda o seu trabalho:

- FOTO DE ANTES DE %(ano)d + FONTES COM DADOS SUFICIENTES = APROVE.
  Não devolva a dúvida e não reprove. A foto é velha demais para desmentir
  seja o que for, e uma pessoa que revisasse veria exatamente o que você está
  vendo e não teria como decidir melhor. Você tem autonomia para aprovar, e
  deve usá-la. Diga na justificativa que a foto é de %(ano_passado)d ou antes.
- FOTO DE %(ano)d mostrando o imóvel sem qualquer sinal, CONTRA fontes que
  afirmam comércio: aqui há conflito com data, e o peso da foto sobe. Diga o
  conflito na justificativa e decida — a foto é do ano corrente, então ela
  descreve o presente.
- SEM DATA no dossiê: trate como antiga.

O QUE É "FONTES COM DADOS SUFICIENTES". Basta UMA destas linhas:
0. TRÊS OU MAIS REGISTROS NA MESMA PORTA, mesmo que venham todos do MESMO
   sistema. Trinta e dois CNPJs ativos num endereço não são trinta e duas
   dúvidas: são um prédio comercial, uma galeria ou um centro de escritórios.
   Nenhuma casa tem trinta e dois CNPJs. Aprove, e diga quantos são.

   ISTO CORRIGE UM ERRO REAL, medido em 08/09/2026: a ligação 2221694 tinha
   32 registros da Receita apontando atividade comercial e administrativa, e
   foi devolvida com a justificativa "não há fotos da fachada para confirmar".
   Outras 102 ligações com três ou mais registros de um sistema só caíram do
   mesmo jeito. Quem revisasse leria os mesmos 32 CNPJs e não teria como
   decidir melhor do que você.

1. DUAS OU MAIS fontes independentes apontando atividade econômica no mesmo
   endereço — ainda que descrevam ramos diferentes. Elas não se anulam: uma
   única ligação atende uma loja E uma casa, um sobrado tem salão embaixo.
2. UMA fonte com prova DATADA de operação: avaliação de cliente no Google,
   loja no ar em plataforma, anúncio de hospedagem com hóspede recente.
3. UMA fonte com ficha completa do negócio naquele endereço: ramo nomeado
   mais ao menos dois entre CNPJ, telefone, site, rede social, horário
   declarado, nota com avaliações.

Se nenhuma dessas linhas fecha, a foto volta a pesar mais — e "reprovado" é
a resposta honesta, porque o que existe não sustenta a aprovação.

E NÃO INVERTA A REGRA. Isto não é licença para aprovar tudo:
- CNPJ sozinho, sem mais nada, NÃO é ficha completa. Endereço de contador e
  MEI registrado em casa que nunca operou existem às centenas.
- ECONOMIA COMERCIAL OU INDUSTRIAL já declarada na própria ligação significa
  que o cliente JÁ cobra parte dela como comércio: não há o que reclassificar,
  e o veredito é "reprovado" salvo se houver outra atividade além daquela.
- Foto de %(ano)d mostrando TERRENO VAGO, IMÓVEL DEMOLIDO ou OBRA reprova
  mesmo contra fonte, porque aí a foto viu o que a fonte não podia saber: não
  há imóvel. Esta é a única reprovação que a foto ganha sozinha.

DIGA SEMPRE, na justificativa, QUANTAS FONTES sustentam o veredito e QUAL A
DATA da foto que você usou. Quem for à porta precisa saber o que esperar.
"""


#: O PROMPT. Duas coisas nele foram consertadas em 10/09/2026, e as duas por
#: apontamento do dono do produto.
#:
#: A ACENTUACAO E SEMANTICA, nao estetica. O texto vinha quase todo sem acento,
#: e em portugues isso troca palavra por palavra: "E O IMOVEL QUE JA PAGA
#: COMERCIO E REPROVADO" tem duas letras E com papeis opostos — a primeira e
#: conjuncao, a segunda deveria ser "e" com acento, o verbo. Como ele escreveu:
#: "casa 1 E ligacao 20 e completamente diferente em sentido de casa 1 E
#: ligacao 20". Um modelo de linguagem le exatamente essa ambiguidade, e
#: "nao e motivo", "e prova DATADA", "e reprovado" apareciam assim no texto que
#: decidia 12 mil vereditos.
#:
#: O SETOR SAI DO TEXTO E VIRA PARAMETRO. O prompt falava de hidrometro, de
#: agua e de "o imovel que ele abastece" — vocabulario de saneamento. O mesmo
#: produto serve energia eletrica e gas, onde nao ha hidrometro nenhum. As
#: palavras agora vem de `setor.palavras()`; ver o modulo e a migracao 0095.
PROMPT = """Você decide se os registros encontrados pertencem MESMO a esta
%(ligacao)s, e se o que há neles prova comércio no imóvel.

O contexto: %(concessionaria)s cobra este %(medidor)s como RESIDENCIAL. Se
houver comércio no imóvel que ele %(verbo)s, a tarifa está errada — e é isso
que se procura.

AS QUATRO FOTOS DE RUA SÃO DO MESMO PONTO, girando a câmera nas quatro
direções. Nenhum imóvel vem assinalado nelas: apontar o alvo antes de você
olhar seria dar a resposta junto com a pergunta. Olhe o que está lá e diga o
que vê.

%(julgar)s

O QUE VOCÊ RECEBE, e o peso de cada coisa:

- AS FONTES são bases independentes — Receita, IBGE, base estadual, Google
  Maps, iFood, Airbnb. Cada uma registrou o lugar por conta própria, em épocas
  diferentes. Quando convergem valem mais do que qualquer uma sozinha.
- AS DATAS estão no dossiê e nos rótulos das imagens. Use-as: foto de dois
  anos atrás descreve o que havia HÁ DOIS ANOS. Foto do Google publicada por
  visitante NÃO tem data no nosso cadastro, e sem data ela não sustenta
  afirmação sobre o presente — diga o que ela mostra, não quando.
- A LOJA NO AR é prova DATADA de operação. iFood aceitando pedido e anúncio de
  hospedagem com avaliação recente dizem que o negócio funcionava quando foi
  visto, o que a fachada nunca diz.

SEPARE QUEM NÃO É DESTE ENDEREÇO. O vínculo nasce de rua e número batendo, mas
uma base pode ter escrito o número errado. Olhe o número da porta, o logradouro
e o ramo de cada registro e diga quais claramente NÃO pertencem aqui.

E "CLARAMENTE" MESMO. Nome diferente sozinho não é motivo: uma loja e o CNPJ
dela costumam ter nomes distintos, e um sobrado tem a casa e o salão.

TROCA DE NOME NÃO É OUTRO ENDEREÇO. Dois registros do MESMO RAMO no MESMO
endereço com nomes diferentes quase sempre são o mesmo ponto em épocas
diferentes: no Brasil estabelecimento troca de nome o tempo todo, e o dono
seguinte herda a porta e o %(medidor)s. Cada fonte olhou numa época e anotou o
nome que estava na fachada NAQUELE dia.

DOIS STATUS, E SÓ DOIS:

- "aprovado": os dados sustentam que há atividade comercial neste imóvel.
- "reprovado": não sustentam.

NÃO EXISTE "revisão humana" NESTE JULGAMENTO. Você recebe o que existe sobre o
endereço; se isso não basta para aprovar, é reprovado. Devolver a dúvida para
uma pessoa que veria exatamente o mesmo material não acrescenta nada — e foi o
que fez 82,6%% dos casos pararem numa fila que ninguém tinha como resolver.

E O IMÓVEL QUE JÁ PAGA COMÉRCIO É REPROVADO. Economia comercial ou industrial
declarada na própria ligação significa que o cliente já cobra parte dela como
comércio: não há o que reclassificar.

OLHE A RUA, E NÃO SÓ O ALVO. Quatro coisas nas fotos valem para muito além
deste julgamento, e por isso são perguntadas à parte:

1. O NÚMERO PREGADO NA CASA. Leia todos os números de porta que conseguir nas
   quatro visadas — no muro, no portão, na placa, na faixa da calçada. A rua
   você já sabe qual é: está no dossiê. Um número lido aqui diz onde aquele
   número FICA nesta via, e há milhares de endereços neste cadastro cuja
   coordenada está errada e que um número lido reposiciona. Não invente:
   quando não tiver certeza do algarismo, marque a certeza como "media"; se
   não der para ler, não liste.

2. SE VOCÊ LER O NÚMERO DA CASA JULGADA, JULGUE AQUELA CASA. O dossiê diz qual
   é o número. Se ele aparece pregado num imóvel da foto, é ESSE o imóvel que
   está sendo cobrado — descreva o que há NELE, e não no vizinho de fachada
   mais chamativa. É a única vez em que a foto identifica o alvo sozinha.

3. OS MEDIDORES. Conte os %(medidores)s e os medidores de energia visíveis na
   fachada ou no muro. Vários medidores num imóvel só significam várias
   unidades no mesmo endereço — sobrado com salão, vila de fundos, quitinetes.
   Conte o que vê; zero é uma resposta.

4. AS FACHADAS COMERCIAIS DA CENA, INCLUSIVE AS QUE NÃO SÃO O ALVO. Letreiro,
   toldo com nome, vitrine, placa de serviço: liste o que estiver legível,
   marcando se é ou não o estabelecimento buscado. As que não são continuam
   valendo — são comércio que existe naquela quadra e que nenhuma base
   registrou.

   SÓ COMÉRCIO. Placa de rua, nome de praça, sinalização de trânsito e prédio
   público não são fachada comercial — não os liste. A primeira medição desta
   pergunta devolveu "R. Quintão · logradouro" e "Parque Deputado Possebon ·
   parque público" entre seis achados, e nenhum dos dois é um negócio.

   E O NÚMERO DE CADA UMA, quando estiver junto do letreiro. Nome mais número
   é o que permite procurar, no cadastro da concessionária, qual ligação
   atende aquele comércio — e é assim que um letreiro lido numa foto vira um
   alvo com endereço em vez de uma anotação solta. Sem o número, o letreiro
   ainda vale como sinal de que há comércio na quadra, mas não acha dono.

E a tampa de esgoto na calçada, quando houver: ela diz que a via tem coleta.

Responda SOMENTE um JSON:
{"status": "aprovado|reprovado",
 "pois_coerentes": [<números dos POIs que pertencem a esta ligação>],
 "pois_de_outro_endereco": [{"poi": <número>, "porque": "<até 15 palavras>"}],
 "estabelecimento": "<o nome do negócio que sustenta a aprovação, ou null>",
 "presenca_na_foto": "exata|comercial|nenhuma",
 "fotos_do_google_validam": true|false,
 "numero_na_fachada": "<o número que você LEU no imóvel julgado, ou null>",
 "numeros_vistos": [{"numero": "<lido>", "onde": "<qual visada>",
                     "certeza": "alta|media"}],
 "medidores": {"agua": <quantos>, "energia": <quantos>},
 "tampa_de_esgoto": true|false,
 "fachadas_vistas": [{"texto": "<o que está escrito>",
                      "ramo": "<o que aparenta ser>",
                      "numero": "<o número de porta que aparece COM este
letreiro, ou null>", "e_o_alvo": true|false}],
 "especie_cnefe": <1-8|null>, "secao_cnae": "<letra|null>",
 "sinal_no_imovel": "instalacao_fixa|so_oficio|nenhum",
 "justificativa": "<UM PARÁGRAFO, até 70 palavras, dizendo QUAIS fontes
sustentam o status e o que nas imagens confirma ou contradiz. Cite a data do
que usou. Escreva o que foi visto, não o que se supõe.>"}

O QUE SIGNIFICA "presenca_na_foto", e ela alimenta a pontuação do vínculo:
- "exata": as fotos mostram o estabelecimento nomeado — letreiro com o nome,
  ou ramo inequivocamente o mesmo.
- "comercial": as fotos mostram atividade comercial no imóvel, mas não dá para
  dizer que é AQUELE negócio.
- "nenhuma": as fotos não mostram sinal comercial, ou não há foto.

"fotos_do_google_validam" é verdadeiro quando as fotos publicadas mostram o
negócio funcionando — balcão, mercadoria, sala de atendimento, produto sendo
servido. Falso quando não há foto, ou quando o que há não diz nada sobre o
lugar.

────────────────────────────────────────────────────────────────────────

%(dossie)s

AS IMAGENS QUE VOCÊ RECEBEU, nesta ordem:

%(lista)s"""


#: QUANTAS CONEXOES, independentemente de quantos trabalhadores.
#:
#: O DEFEITO QUE ISTO CORRIGE, medido em 08/09/2026 na primeira corrida deste
#: modulo: ele abria UMA CONEXAO POR TRABALHADOR e foi disparado com 80. O
#: pooler da porta 7100 aceita 20 sessoes NO TOTAL — entre todas as maquinas, a
#: API, o painel e as capturas —, e 64 threads morreram com
#: `(EMAXCONNSESSION) max clients reached in session mode`.
#:
#: Pior: o comentario que eu tinha escrito ali dizia "o teto de trabalhadores e
#: quem protege o pooler", e isso e falso. O teto de trabalhadores protege o
#: MODELO; o pooler tem 20 e nao sabe quantos trabalhadores existem. O
#: `avaliar_ia` ja tinha aprendido isso — `CONEXOES = 4` e a mesma nota no
#: cabecalho — e eu nao segui.
#:
#: SEIS BASTAM porque o dossie usa o banco em rajadas curtas e a chamada ao
#: modelo, que e o grosso do tempo, nao usa banco NENHUM. Ver `Poco.pegar`.
#: O VOCABULARIO DO SETOR, preenchido por `rodar`. Comeca no generico para que
#: quem chame `uma()` direto — um teste, um script — receba um prompt correto
#: em vez de um `KeyError` ou, pior, o nome de um medidor que nao existe
#: naquele cliente.
_palavras = setor.VOCABULARIO[setor.PADRAO]

CONEXOES = 6


class Poco:
    """Emprestimo de conexao. Quem nao pega, espera — nao abre outra.

    A CONEXAO E DEVOLVIDA ANTES DA CHAMADA AO MODELO, e e isso que faz seis
    atenderem oitenta. Montar o dossie sao dezenas de consultas de
    milissegundos; julgar sao dez segundos de espera pela Spark. Segurar a
    conexao durante a espera seria deixar 74 trabalhadores parados na fila do
    banco enquanto o banco esta ocioso.
    """

    def __init__(self, n):
        import queue
        self.fila = queue.Queue()
        for _ in range(max(1, n)):
            self.fila.put(bc.conectar())

    def pegar(self):
        import contextlib

        @contextlib.contextmanager
        def _emprestar():
            con = self.fila.get()
            try:
                yield con
            except Exception:
                # CONEXAO QUE VIU ERRO VOLTA LIMPA. Sem o rollback, a proxima
                # a peg&-la herda a transacao abortada e morre com "current
                # transaction is aborted" — defeito que ja custou uma rodada
                # inteira no `minerar_placeid`.
                try:
                    con.rollback()
                except Exception:                              # noqa: BLE001
                    pass
                raise
            finally:
                self.fila.put(con)
        return _emprestar()

    def fechar(self):
        while not self.fila.empty():
            try:
                self.fila.get_nowait().close()
            except Exception:                                  # noqa: BLE001
                pass


#: O CATALOGO DE CATEGORIAS, QUE EXCLUI MAIS DO QUE PARECE.
#:
#: `categoria_catalogo` tem 2.259 linhas e 1.642 marcadas `avaliar`. O que ele
#: deixa de fora e, na maior parte, CNAE de quem trabalha de casa ou na rua:
#: 7319002 publicidade, 4930201 transporte de carga, 9700500 servicos
#: domesticos, 4399103 alvenaria, 4923002 taxi. A exclusao e pensada — um CNPJ
#: desses num endereco residencial quase sempre e alguem registrado em casa.
#:
#: SO QUE ELA E INVISIVEL. Medido em Canoas em 10/09/2026: das 9.925 ligacoes
#: aguardando julgamento, 461 entravam na fila e ~8.900 sumiam por aqui, sem
#: uma linha de log. Quem olhasse o painel veria "541 na fila" e concluiria que
#: nao havia mais o que julgar.
#:
#: `--sem-catalogo` desliga o corte e deixa a IA decidir caso a caso. Decisao
#: do dono do produto em 10/09/2026, ao pedir "TODOS os candidatos".
SQL_CATALOGO = """and exists (select 1 from radar_comercial.categoria_catalogo cc
                where cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
                  and cc.avaliar)"""

#: A LIGACAO CUJO VEREDITO ENVELHECEU. Ela ja foi julgada, mas depois disso
#: algum POI dela ganhou foto nova — captura, recaptura sem mira, foto do
#: Google. O veredito antigo olhou um material que nao existe mais.
#:
#: Isto e o que permite capturar e julgar AO MESMO TEMPO: a captura vai
#: gravando `poi_evidencia.capturado_em`, e cada passada do julgamento pega o
#: que ficou pronto desde a anterior, sem esperar a captura inteira terminar.
SQL_DESATUALIZADO = """and (
       not exists (select 1 from radar_comercial.ligacao_veredito v
                    where v.ligacao = lp.ligacao)
    or exists (select 1 from radar_comercial.ligacao_veredito v
                join radar_comercial.poi_evidencia e on e.poi_id = lp.poi_id
               where v.ligacao = lp.ligacao
                 and e.capturado_em > v.avaliado_em))"""


def _log(m):
    print(m, flush=True)


SQL_FILA = """
select distinct lp.ligacao
  from radar_comercial.ligacao_poi lp
  join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
  join radar_comercial.pois p on p.id = lp.poi_id
 where upper(l.categoria) = 'RESIDENCIAL'
   and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
   and p.fundido_em is null
   -- SO A LIGACAO MARCADA COMO APTA, e esta e a regra que separa o que
   -- custa do que nao custa.
   --
   -- Decisao do dono do produto em 09/09/2026: toda a base cruza com os POIs,
   -- mas so as marcadas passam pelo enriquecimento caro — Maps, Street View,
   -- rede social e IA. As demais ficam vinculadas pelo endereco estrito e
   -- param ai.
   --
   -- `apta_cruzamento` e coluna GERADA de `qualificacao`, entao ela cobre os
   -- dois status que aprovam: SIM e SIM_COM_ANALISE_HUMANA. O segundo se
   -- comporta como o primeiro aqui; a diferenca dele existe na TELA, para o
   -- cliente filtrar o que leva a campo.
   --
   -- NULO NAO ENRIQUECE. Base sem a coluna declarada nao entra na fila cara:
   -- erro de preenchimento custa uma ligacao de fora, e um "sim" suposto custa
   -- extracao paga.
   and l.apta_cruzamento
   -- O VINCULO DESCARTADO NAO CONTA COMO VINCULO.
   --
   -- Medido em 08/09/2026, logo depois de `revisar_vinculo` marcar 261.425
   -- descartes: 13 das 20 primeiras ligacoes da fila voltaram `sem_poi` —
   -- `dossie_ligacao` respeita `descartado_em` e nao achava nenhuma fonte,
   -- enquanto esta consulta ainda as enfileirava. Cada uma dessas custava uma
   -- volta ao banco para descobrir que nao havia o que julgar.
   and lp.descartado_em is null
   %(catalogo)s
   %(filtro)s
 order by 1
"""

SEM_VEREDITO = """
   and not exists (select 1 from radar_comercial.ligacao_veredito v
                    where v.ligacao = lp.ligacao)
"""


def fila(con, limite, refazer, ligacoes=None, sem_catalogo=False, desatualizados=False):
    if ligacoes:
        return [str(x) for x in ligacoes]
    cur = con.cursor()
    if desatualizados:
        filtro = SQL_DESATUALIZADO
    elif refazer:
        filtro = ""
    else:
        filtro = SEM_VEREDITO
    cur.execute(SQL_FILA % {"filtro": filtro,
                            "catalogo": "" if sem_catalogo else SQL_CATALOGO})
    saida = [r[0] for r in cur.fetchall()]
    return saida[:limite] if limite else saida


def gravar(con, ligacao, v, resposta, percepcao, resumo, modelo, n_img, dt):
    cur = con.cursor()
    cur.execute("""
        insert into radar_comercial.ligacao_veredito
            (id_empresa, ligacao, veredito, justificativa, confianca,
             pois, fontes, imagens, percepcao, modelo, segundos)
        values ((select core.empresa_atual()), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (id_empresa, ligacao) do update set
            veredito = excluded.veredito,
            justificativa = excluded.justificativa,
            confianca = excluded.confianca,
            pois = excluded.pois, fontes = excluded.fontes,
            imagens = excluded.imagens, percepcao = excluded.percepcao,
            modelo = excluded.modelo, segundos = excluded.segundos,
            avaliado_em = now()
    """, (ligacao, v, resposta.get("justificativa"),
          resposta.get("confianca", ia.CONF_VEREDITO.get(v, 0.5)),
          resumo.get("pois", 0), resumo.get("fontes", 0), n_img,
          json.dumps(percepcao, ensure_ascii=False), modelo, round(dt, 2)))
    con.commit()


def _inteiro(v, teto=99):
    """Um inteiro pequeno, ou None. O modelo devolve "2", 2, "dois" e null."""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= teto else None


def _texto(v, n=120):
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] or None


def gravar_visual(con, ligacao, resposta, resumo):
    """O que a IA leu na rua vira linha, e não parágrafo.

    POR QUE ISTO EXISTE, e por que num lugar separado do veredito. O número
    pregado no muro, os medidores contados na fachada e o letreiro do vizinho
    não dizem respeito só a ESTA ligação: eles descrevem a VIA. A foto foi
    tirada de um ponto com coordenada conhecida, olhando uma rua que já
    sabemos qual é — então "952" lido ali é a informação de onde o 952 daquela
    via fica, e serve a qualquer outro ponto que precise se localizar nela.

    Ver a migração 0096 e `enriquecer_cruzado.py`, que é quem consome.

    A CÂMERA VEM DO POI DAS VISADAS. `resumo["fonte_das_visadas"]` é o POI de
    quem o dossiê tirou as fotos de rua; a posição do panorama está em
    `poi_evidencia`. Sem ela o número lido continua valendo como confirmação
    do próprio endereço, mas não localiza mais ninguém — por isso a coordenada
    é gravada quando existe e o resto entra do mesmo jeito quando não existe.
    """
    poi = resumo.get("fonte_das_visadas")
    cur = con.cursor()

    cam_lat = cam_lng = None
    if poi:
        cur.execute("""select cam_lat, cam_lng
                         from radar_comercial.poi_evidencia
                        where poi_id = %s and tipo like 'sv_%%'
                          and cam_lat is not null
                        limit 1""", (poi,))
        r = cur.fetchone()
        if r:
            cam_lat, cam_lng = float(r[0]), float(r[1])

    # A VIA VEM DO CADASTRO, E NUNCA DA IA. Ela lê o número; qual é a rua já
    # se sabe. Deixar o modelo nomear o logradouro seria abrir a porta para
    # ele "corrigir" a rua a partir de uma placa mal lida, e o número passaria
    # a apontar para a via errada — o oposto do que esta tabela serve.
    cur.execute("""select nom_logradouro, cidade
                     from resources_root.cadastro_corsan
                    where num_ligacao::text = %s limit 1""", (ligacao,))
    r = cur.fetchone()
    logradouro, cidade = (r[0], r[1]) if r else (None, None)

    med = resposta.get("medidores") or {}
    if not isinstance(med, dict):
        med = {}
    cur.execute("""
        insert into radar_comercial.leitura_visual
            (id_empresa, ligacao, poi_id, numero_na_fachada,
             medidores_agua, medidores_energia, tampa_esgoto)
        values ((select core.empresa_atual()), %s, %s, %s, %s, %s, %s)
        on conflict (id_empresa, ligacao) do update set
            poi_id = excluded.poi_id,
            numero_na_fachada = excluded.numero_na_fachada,
            medidores_agua = excluded.medidores_agua,
            medidores_energia = excluded.medidores_energia,
            tampa_esgoto = excluded.tampa_esgoto,
            lido_em = now()
    """, (ligacao, poi, _texto(resposta.get("numero_na_fachada"), 12),
          _inteiro(med.get("agua")), _inteiro(med.get("energia")),
          bool(resposta.get("tampa_de_esgoto"))
          if resposta.get("tampa_de_esgoto") is not None else None))

    # SEM LOGRADOURO NÃO HÁ NÚMERO ÚTIL. Um número sem a via a que pertence
    # não localiza nada e ainda ocuparia o índice.
    nums = resposta.get("numeros_vistos") or []
    if logradouro and isinstance(nums, list):
        vistos = set()
        for it in nums[:20]:
            if not isinstance(it, dict):
                continue
            num = _texto(it.get("numero"), 12)
            onde = _texto(it.get("onde"), 30) or "?"
            if not num or not num.strip("0"):
                continue
            if (num, onde) in vistos:
                continue
            vistos.add((num, onde))
            cert = str(it.get("certeza") or "media").lower()
            cur.execute("""
                insert into radar_comercial.numero_lido
                    (id_empresa, ligacao, poi_id, logradouro, cidade, numero,
                     onde, certeza, cam_lat, cam_lng)
                values ((select core.empresa_atual()), %s,%s,%s,%s,%s,%s,%s,
                        %s,%s)
                on conflict (id_empresa, ligacao, numero, onde) do update set
                    certeza = excluded.certeza,
                    cam_lat = excluded.cam_lat, cam_lng = excluded.cam_lng,
                    lido_em = now()
            """, (ligacao, poi, logradouro, cidade, num, onde,
                  cert if cert in ("alta", "media") else "media",
                  cam_lat, cam_lng))

    # AS FACHADAS SÃO REESCRITAS a cada julgamento: elas descrevem a cena
    # daquela leitura, e uma releitura com fotos novas substitui a anterior em
    # vez de somar a ela.
    cur.execute("""delete from radar_comercial.fachada_vista
                    where ligacao = %s
                      and id_empresa = (select core.empresa_atual())""",
                (ligacao,))
    fach = resposta.get("fachadas_vistas") or []
    if isinstance(fach, list):
        for it in fach[:20]:
            if not isinstance(it, dict):
                continue
            txt, ramo = _texto(it.get("texto")), _texto(it.get("ramo"), 60)
            if not txt and not ramo:
                continue
            cur.execute("""
                insert into radar_comercial.fachada_vista
                    (id_empresa, ligacao, poi_id, texto, ramo, numero,
                     e_o_alvo, onde, cam_lat, cam_lng)
                values ((select core.empresa_atual()),
                        %s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (ligacao, poi, txt, ramo, _texto(it.get("numero"), 12),
                  bool(it.get("e_o_alvo")), _texto(it.get("onde"), 30),
                  cam_lat, cam_lng))
    con.commit()


def marcar_intrusos(con, ligacao, resposta, ids_validos, modelo):
    """Grava quem a IA disse nao pertencer a este hidrometro. Devolve quantos.

    DUAS GUARDAS, e as duas ja evitaram estrago em outros pontos do sistema:

    1. SO POI QUE ESTAVA NO DOSSIE. O modelo pode inventar um numero, ou
       repetir um id que leu noutro lugar do texto. Marcar um vinculo que nao
       fazia parte deste julgamento seria agir sobre o que ninguem examinou.

    2. NUNCA TODOS. Se a resposta manda descartar o dossie inteiro, alguma
       coisa saiu errada — nao ha julgamento possivel sobre uma ligacao sem
       testemunha, e o resultado seria uma ligacao muda e sem vinculo nenhum.
       Nesse caso nao se marca nada e o caso fica para gente.
    """
    pedidos = (resposta or {}).get("pois_de_outro_endereco") or []
    if not isinstance(pedidos, list):
        return 0
    alvos = []
    for p in pedidos:
        try:
            pid = int(p.get("poi") if isinstance(p, dict) else p)
        except (TypeError, ValueError):
            continue
        if pid in ids_validos:
            alvos.append((pid, str((p or {}).get("porque", ""))[:200]
                          if isinstance(p, dict) else ""))
    if not alvos or len(alvos) >= len(ids_validos):
        return 0
    with con.cursor() as k:
        for pid, porque in alvos:
            k.execute("""
                update radar_comercial.ligacao_poi
                   set descartado_em = now(),
                       descartado_motivo = %s,
                       descartado_por = %s
                 where ligacao = %s and poi_id = %s
                   and descartado_em is null""",
                      (porque or "a IA leu o dossie e concluiu que e outro "
                                 "endereco", modelo, ligacao, pid))
    con.commit()
    return len(alvos)


def _para_veredito(resposta):
    """O `aprovado|reprovado` do modelo vira o veredito que o sistema guarda.

    POR QUE TRADUZIR EM VEZ DE TROCAR O VOCABULARIO. O dono do produto pediu
    que a ANALISE fosse binaria — "simplificando e deixando mais fiel" —, e ela
    e: o modelo responde duas coisas e nao precisa mais escolher entre quatro
    caixas parecidas. Mas `aprovado_exato` e `aprovado_comercial` nao sao duas
    caixas parecidas: sao "sei QUAL negocio e" e "sei que ha comercio". O
    painel pinta por elas, a planilha ordena por elas e a regra de "uma
    aprovacao basta" le a primeira como mais forte.

    A distincao volta de graca: ela e o campo `estabelecimento`, que o modelo
    ja preenche quando sabe o nome. Perguntar duas vezes a mesma coisa seria o
    que a simplificacao veio tirar.

    E `revisao_humana` NAO SAI DAQUI. O prompt novo proibiu a duvida: se o que
    existe nao basta para aprovar, e reprovado. Devolver para uma pessoa que
    veria o mesmo material foi o que fez 82,6% dos casos pararem numa fila que
    ninguem tinha como resolver.
    """
    r = resposta or {}
    s = str(r.get("status") or r.get("veredito") or "").strip().lower()
    if s.startswith("aprov"):
        # A FOTO E QUEM DIZ SE E EXATO, e nao o nome que o modelo escreveu.
        #
        # A primeira versao promovia a `aprovado_exato` sempre que o campo
        # `estabelecimento` viesse preenchido. Medido nas 20 primeiras do
        # prompt novo: 16 de 20 sairam `aprovado_exato`, porque o CNPJ da
        # Receita SEMPRE publica uma razao social — o modelo nunca fica sem
        # nome para escrever.
        #
        # So que `aprovado_exato` nunca quis dizer "sabemos o nome": quer dizer
        # que a FACHADA mostra aquele negocio. E o `aprovado_comercial` quer
        # dizer "ha comercio ali, nao sei qual". Confundir os dois esvazia a
        # coluna que o painel pinta e a planilha ordena.
        #
        # A pergunta certa ja esta sendo respondida em `presenca_na_foto`.
        pres = str(r.get("presenca_na_foto") or "").strip().lower()
        return "aprovado_exato" if pres == "exata" else "aprovado_comercial"
    if s.startswith("reprov"):
        return "reprovado"
    return None


def uma(poco, ligacao, modelo, secoes, placar, trava, aplicar):
    t0 = time.time()
    # O BANCO SO ENQUANTO SE MONTA O DOSSIE. Depois a conexao volta ao poco e
    # a espera pela Spark acontece sem segurar nada.
    with poco.pegar() as con:
        texto, imgs, tipos, resumo = dl.montar(con, ligacao, ia, imagens)
    if texto is None:
        with trava:
            placar["sem_poi"] += 1
        return
    lista = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(tipos))
    # AS PALAVRAS DO SETOR entram aqui, e nao no texto: o mesmo prompt serve
    # agua, energia e gas. Ver `setor.py` e a migracao 0095.
    prompt = PROMPT % dict(_palavras, dossie=texto, lista=lista,
                           julgar=_regras_da_ligacao(secoes))
    try:
        # 2400 AGORA, E O MOTIVO E O MESMO DAS DUAS VEZES ANTERIORES: o
        # esquema cresceu. O v3 pede quatro listas novas — numeros lidos,
        # medidores, fachadas da cena — e um predio de galeria pode devolver
        # dez fachadas. O teto foi 700, virou 1100 quando o v2 truncou 36 de
        # 2.473, virou 1600 quando um predio com 70 CNPJs truncou 17 de 1.745,
        # e a conta continua sendo a do MAIOR caso da fila, nao a do medio.
        #
        # 1100 E NAO 700, e o motivo veio medido em 10/09/2026: 36 das 2.473
        # primeiras ligacoes falharam com "Unterminated string" — JSON cortado
        # no meio, sempre por volta da linha 74.
        #
        # O teto de 700 servia ao esquema antigo. O v2 pede mais: duas listas
        # de POIs (uma ligacao tinha dez), `presenca_na_foto`,
        # `fotos_do_google_validam`, especie, secao, sinal e o paragrafo. Numa
        # ligacao com muitos registros a resposta estoura, e o corte nao chega
        # como erro do modelo: chega como texto que nao fecha, e o parse morre
        # longe da causa.
        #
        # Falha aqui nao grava veredito, entao a ligacao volta para a fila
        # sozinha na proxima rodada — o estrago foi tempo, nao dado perdido.
        resposta = di._chat_local(modelo, prompt, [ia._b64(b) for b in imgs],
                                  max_tokens=2400, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha"] += 1
            _log("   %-10s FALHOU: %s" % (ligacao, str(e)[:70]))
        return
    v = _para_veredito(resposta)
    if v is None:
        # FORA DA ESCALA CONTINUA SENDO REPROVADO, e nao duvida. O prompt pede
        # duas palavras; o que vier fora disso e resposta malformada, e
        # resposta malformada nao e evidencia de comercio.
        with trava:
            placar["fora_da_escala"] += 1
        resumo["_veredito_cru"] = resposta
        v = "reprovado"
    percepcao = {"dossie": texto, "resposta": resposta, **resumo}
    dt = time.time() - t0
    fora = 0
    if aplicar:
        with poco.pegar() as con:
            gravar(con, ligacao, v, resposta or {}, percepcao, resumo, modelo,
                   len(imgs), dt)
            # O QUE SE VIU NA RUA, GRAVADO SEPARADO. Falha aqui NAO derruba o
            # veredito: o julgamento e o produto, e a leitura visual e o
            # subproduto que alimenta os outros pontos. Perder um numero lido
            # custa um ponto a menos na fila de alocacao; perder o veredito
            # custa a ligacao inteira.
            try:
                gravar_visual(con, ligacao, resposta or {}, resumo)
            except Exception as e:                             # noqa: BLE001
                _log("   leitura visual de %s falhou: %s: %s"
                     % (ligacao, type(e).__name__, str(e)[:90]))
                try:
                    con.rollback()
                except Exception:                              # noqa: BLE001
                    pass
            fora = marcar_intrusos(con, ligacao, resposta,
                                   set(resumo.get("ids") or []), modelo)
        if fora:
            with trava:
                placar["poi_de_outro_endereco"] += fora
    with trava:
        placar[v] += 1
        _log("   %-10s %-19s %d POIs/%d fontes · %s"
             % (ligacao, v, resumo.get("pois", 0), resumo.get("fontes", 0),
                (resposta or {}).get("justificativa", "")[:66]))


def rodar(limite, aplicar, trabalhadores, modelo, ligacoes, refazer,
          sem_catalogo=False, desatualizados=False):
    con = bc.conectar()
    alvos = fila(con, limite, refazer, ligacoes,
                 sem_catalogo=sem_catalogo,
                 desatualizados=desatualizados)
    _log("▶ veredito por LIGACAO — o dossiê de todas as fontes numa chamada")
    _log("   %d ligação(ões) na fila" % len(alvos))
    if not alvos:
        return {"alvos": 0}
    secoes = ia._secoes_texto(con)
    # UMA CONSULTA POR EXECUCAO, como as secoes: o setor nao muda no meio de
    # uma corrida, e le-lo por ligacao seria 12 mil consultas iguais.
    global _palavras
    _palavras = setor.palavras(con)
    _log("setor: %s" % setor.qual(con))
    # A CONEXAO DE PREPARO FECHA AQUI, e isto nao e higiene: e correcao.
    #
    # IDLE IN TRANSACTION SEGURA LOCK. Ela era aberta, usada para montar a fila
    # e ler as secoes da CNAE, e nunca fechada — ficava `idle in transaction`
    # pelas HORAS da corrida, segurando AccessShareLock em toda tabela que
    # tocou.
    #
    # Medido em 10/09/2026: um `alter policy` em `cadastro_corsan` entrou na
    # fila atras dela e, como a fila de lock do Postgres e FIFO, TODOS os
    # trabalhadores pararam atras do alter. A fila de julgamento congelou em
    # 494 e so voltou quando o processo inteiro foi derrubado.
    #
    # O segundo estrago e silencioso: transacao aberta ha horas impede o
    # autovacuum de limpar as tabelas que ela leu, e isso nao aparece em log
    # nenhum — aparece como lentidao semanas depois.
    #
    # O trabalho de verdade nao usa esta conexao: cada tarefa pega a sua do
    # `Poco` e a devolve antes de chamar o modelo.
    con.close()

    placar = {k: 0 for k in VEREDITOS}
    placar.update({"sem_poi": 0, "falha": 0, "fora_da_escala": 0,
                   "poi_de_outro_endereco": 0})
    trava = threading.Lock()
    t0 = time.time()

    # UMA CONEXAO POR TRABALHADOR aqui, e nao emprestimo: o dossie faz muitas
    # consultas curtas em sequencia e a disputa por uma conexao unica seria o
    # gargalo. O teto de trabalhadores e quem protege o pooler.
    poco = Poco(CONEXOES)

    def worker(fatia):
        for lig in fatia:
            try:
                uma(poco, lig, modelo, secoes, placar, trava, aplicar)
            except Exception as e:                             # noqa: BLE001
                # A FALHA DE UMA LIGACAO NAO DERRUBA O TRABALHADOR. Na primeira
                # corrida a excecao subia ate o `threading` e matava a thread
                # inteira: 64 delas morreram e a fila parou de andar sem que o
                # placar acusasse — o log dizia "Exception in thread" e mais
                # nada.
                with trava:
                    placar["falha"] += 1
                    _log("   %-10s ERRO %s: %s"
                         % (lig, type(e).__name__, str(e)[:70]))

    n = max(1, trabalhadores)
    fatias = [alvos[i::n] for i in range(n)]
    threads = [threading.Thread(target=worker, args=(f,)) for f in fatias]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    poco.fechar()

    dt = time.time() - t0
    _log("")
    for k in placar:
        if placar[k]:
            _log("   %-20s %5d" % (k, placar[k]))
    _log("   %d ligação(ões) em %.1f min · %.1f s cada"
         % (len(alvos), dt / 60, dt / max(len(alvos), 1)))
    return {"alvos": len(alvos), **placar}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=8)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--ligacao", action="append",
                   help="repetível; avalia estas ligações ignorando a fila")
    p.add_argument("--refazer", action="store_true")
    p.add_argument("--sem-catalogo", dest="sem_catalogo", action="store_true",
                   help="ignora `categoria_catalogo.avaliar` e julga todo "
                        "candidato — a IA decide no lugar do catalogo")
    p.add_argument("--desatualizados", action="store_true",
                   help="so as ligacoes sem veredito OU cujo veredito e mais "
                        "velho que a foto mais nova dos POIs dela")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    r = rodar(a.limite, a.aplicar, a.trabalhadores, a.modelo, a.ligacao,
              a.refazer, sem_catalogo=a.sem_catalogo,
              desatualizados=a.desatualizados)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
