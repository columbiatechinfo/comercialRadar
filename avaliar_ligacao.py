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
    corte = herdado.find("FOTO DE OFICIO NAO PROVA ENDERECO")
    cauda = herdado[corte:] if corte > 0 else ""
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
_REGRAS_DA_LIGACAO = """A IMAGEM E UMA TESTEMUNHA, E NAO O JUIZ — e esta e a regra que manda sobre
todas as outras deste bloco.

Voce recebe de tres a seis provas de naturezas diferentes: bases de registro,
plataformas com data, avaliacoes de clientes assinadas, e fotos. A foto de rua
e UMA delas. Ela responde bem uma pergunta — "o que estava pendurado na
fachada no dia em que o carro passou" — e responde mal todas as outras.

O QUE A FACHADA NAO SABE:
- nao sabe o que funciona nos fundos, no sobrado ou dentro de casa;
- nao sabe o que abriu depois que a foto foi tirada;
- nao sabe de negocio que opera sem placa, que e o mais comum do bairro:
  costureira, doceira, manicure, marmita, oficina, aluguel de temporada.

ENTAO: fachada muda NAO E CONTRAPROVA. Ela nao contradiz fonte nenhuma — ela
simplesmente nao viu.

E FOTO QUE NAO EXISTE E MENOS AINDA. Quando o dossie diz que nenhuma imagem o
acompanha, isso NAO e motivo para revisao humana: e so a captura que ainda nao
passou por este endereco. "Nao ha foto para confirmar" e a frase que voce esta
proibido de usar como justificativa — ela descreve o estado do nosso trabalho,
e nao o estado do imovel. Decida pelas fontes, que e o que voce recebeu. Uma casa comum na foto e o estado esperado da maioria
dos comercios que este projeto procura, e nao um sinal contra eles.

A FOTO TEM DATA, E A DATA ESTA NO DOSSIE. Use-a assim, e esta e a regra que
mais muda o seu trabalho:

- FOTO DE ANTES DE %(ano)d + FONTES COM DADOS SUFICIENTES = APROVE.
  Nao mande para revisao humana e nao reprove. A foto e velha demais para
  desmentir seja o que for, e a pessoa que abrisse essa revisao veria
  exatamente o que voce esta vendo e nao teria como decidir melhor. Voce tem
  autonomia para aprovar, e deve usa-la. Diga na justificativa que a foto e
  de %(ano_passado)d ou antes.
- FOTO DE %(ano)d mostrando o imovel sem qualquer sinal, CONTRA fontes que
  afirmam comercio: aqui ha conflito com data, e o peso da foto sobe. Diga o
  conflito na justificativa e decida — a foto e do ano corrente, entao ela
  descreve o presente.
- SEM DATA no dossie: trate como antiga.

O QUE E "FONTES COM DADOS SUFICIENTES". Basta UMA destas linhas:
0. TRES OU MAIS REGISTROS NA MESMA PORTA, mesmo que venham todos do MESMO
   sistema. Trinta e dois CNPJs ativos num endereco nao sao trinta e duas
   duvidas: sao um predio comercial, uma galeria ou um centro de escritorios.
   Nenhuma casa tem trinta e dois CNPJs. Aprove, e diga quantos sao.

   ISTO CORRIGE UM ERRO REAL, medido em 08/09/2026: a ligacao 2221694 tinha
   32 registros da Receita apontando atividade comercial e administrativa, e
   foi para revisao humana com a justificativa "nao ha fotos da fachada para
   confirmar". Outras 102 ligacoes com tres ou mais registros de um sistema so
   cairam do mesmo jeito. A pessoa que abrisse essa revisao leria os mesmos 32
   CNPJs e nao teria como decidir melhor do que voce.

1. DUAS OU MAIS fontes independentes apontando atividade economica no mesmo
   endereco — ainda que descrevam ramos diferentes. Elas nao se anulam: um
   hidrometro abastece uma loja E uma casa, um sobrado tem salao embaixo.
2. UMA fonte com prova DATADA de operacao: avaliacao de cliente no Google,
   loja no ar em plataforma, anuncio de hospedagem com hospede recente.
3. UMA fonte com ficha completa do negocio naquele endereco: ramo nomeado
   mais ao menos dois entre CNPJ, telefone, site, rede social, horario
   declarado, nota com avaliacoes.

Se nenhuma dessas linhas fecha, a foto volta a pesar mais — e "reprovado" e
a resposta honesta, porque o que existe nao sustenta a aprovacao.

E NAO INVERTA A REGRA. Isto nao e licenca para aprovar tudo:
- CNPJ sozinho, sem mais nada, NAO e ficha completa. Endereco de contador e
  MEI registrado em casa que nunca operou existem as centenas.
- ECONOMIA COMERCIAL OU INDUSTRIAL ja declarada na propria ligacao significa
  que o cliente JA cobra parte dela como comercio: nao ha o que reclassificar,
  e o veredito e "reprovado" salvo se houver outra atividade alem daquela.
- Foto de %(ano)d mostrando TERRENO VAGO, IMOVEL DEMOLIDO ou OBRA reprova
  mesmo contra fonte, porque ai a foto viu o que a fonte nao podia saber: nao
  ha imovel. Esta e a unica reprovacao que a foto ganha sozinha.

DIGA SEMPRE, na justificativa, QUANTAS FONTES sustentam o veredito e QUAL A
DATA da foto que voce usou. Quem for a porta precisa saber o que esperar.
"""


PROMPT = """Você decide se os registros encontrados pertencem MESMO a esta
ligacao de agua, e se o que ha neles prova comercio no imovel.

O contexto: a companhia cobra este hidrometro como RESIDENCIAL. Se houver
comercio no imovel que ele abastece, a tarifa esta errada — e e isso que se
procura.

AS QUATRO FOTOS DE RUA SAO DO MESMO PONTO, girando a camera nas quatro
direcoes. Nenhum imovel vem assinalado nelas: apontar o alvo antes de voce
olhar seria dar a resposta junto com a pergunta. Olhe o que esta la e diga o
que ve.

%(julgar)s

O QUE VOCE RECEBE, e o peso de cada coisa:

- AS FONTES sao bases independentes — Receita, IBGE, base estadual, Google
  Maps, iFood, Airbnb. Cada uma registrou o lugar por conta propria, em epocas
  diferentes. Quando convergem valem mais do que qualquer uma sozinha.
- AS DATAS estao no dossie e nos rotulos das imagens. Use-as: foto de dois
  anos atras descreve o que havia HA DOIS ANOS. Foto do Google publicada por
  visitante NAO tem data no nosso cadastro, e sem data ela nao sustenta
  afirmacao sobre o presente — diga o que ela mostra, nao quando.
- A LOJA NO AR e prova DATADA de operacao. iFood aceitando pedido e anuncio de
  hospedagem com avaliacao recente dizem que o negocio funcionava quando foi
  visto, o que a fachada nunca diz.

SEPARE QUEM NAO E DESTE ENDERECO. O vinculo nasce de rua e numero batendo, mas
uma base pode ter escrito o numero errado. Olhe o numero da porta, o logradouro
e o ramo de cada registro e diga quais claramente NAO pertencem aqui.

E "CLARAMENTE" MESMO. Nome diferente sozinho nao e motivo: uma loja e o CNPJ
dela costumam ter nomes distintos, e um sobrado tem a casa e o salao.

TROCA DE NOME NAO E OUTRO ENDERECO. Dois registros do MESMO RAMO no MESMO
endereco com nomes diferentes quase sempre sao o mesmo ponto em epocas
diferentes: no Brasil estabelecimento troca de nome o tempo todo, e o dono
seguinte herda a porta e o hidrometro. Cada fonte olhou numa epoca e anotou o
nome que estava na fachada NAQUELE dia.

DOIS STATUS, E SO DOIS:

- "aprovado": os dados sustentam que ha atividade comercial neste imovel.
- "reprovado": nao sustentam.

NAO EXISTE "revisao humana" NESTE JULGAMENTO. Voce recebe o que existe sobre o
endereco; se isso nao basta para aprovar, e reprovado. Devolver a duvida para
uma pessoa que veria exatamente o mesmo material nao acrescenta nada — e foi o
que fez 82,6%% dos casos pararem numa fila que ninguem tinha como resolver.

E O IMOVEL QUE JA PAGA COMERCIO E REPROVADO. Economia comercial ou industrial
declarada na propria ligacao significa que o cliente ja cobra parte dela como
comercio: nao ha o que reclassificar.

Responda SOMENTE um JSON:
{"status": "aprovado|reprovado",
 "pois_coerentes": [<numeros dos POIs que pertencem a esta ligacao>],
 "pois_de_outro_endereco": [{"poi": <numero>, "porque": "<ate 15 palavras>"}],
 "estabelecimento": "<o nome do negocio que sustenta a aprovacao, ou null>",
 "presenca_na_foto": "exata|comercial|nenhuma",
 "fotos_do_google_validam": true|false,
 "especie_cnefe": <1-8|null>, "secao_cnae": "<letra|null>",
 "sinal_no_imovel": "instalacao_fixa|so_oficio|nenhum",
 "justificativa": "<UM PARAGRAFO, ate 70 palavras, dizendo QUAIS fontes
sustentam o status e o que nas imagens confirma ou contradiz. Cite a data do
que usou. Escreva o que foi visto, nao o que se supoe.>"}

O QUE SIGNIFICA "presenca_na_foto", e ela alimenta a pontuacao do vinculo:
- "exata": as fotos mostram o estabelecimento nomeado — letreiro com o nome,
  ou ramo inequivocamente o mesmo.
- "comercial": as fotos mostram atividade comercial no imovel, mas nao da para
  dizer que e AQUELE negocio.
- "nenhuma": as fotos nao mostram sinal comercial, ou nao ha foto.

"fotos_do_google_validam" e verdadeiro quando as fotos publicadas mostram o
negocio funcionando — balcao, mercadoria, sala de atendimento, produto sendo
servido. Falso quando nao ha foto, ou quando o que ha nao diz nada sobre o
lugar.

────────────────────────────────────────────────────────────────────────

%(dossie)s

AS IMAGENS QUE VOCE RECEBEU, nesta ordem:

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
   and exists (select 1 from radar_comercial.categoria_catalogo cc
                where cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
                  and cc.avaliar)
   %(filtro)s
 order by 1
"""

SEM_VEREDITO = """
   and not exists (select 1 from radar_comercial.ligacao_veredito v
                    where v.ligacao = lp.ligacao)
"""


def fila(con, limite, refazer, ligacoes=None):
    if ligacoes:
        return [str(x) for x in ligacoes]
    cur = con.cursor()
    cur.execute(SQL_FILA % {"filtro": "" if refazer else SEM_VEREDITO})
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
    prompt = PROMPT % {"dossie": texto, "lista": lista,
                       "julgar": _regras_da_ligacao(secoes)}
    try:
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
                                  max_tokens=1600, timeout=TIMEOUT)
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


def rodar(limite, aplicar, trabalhadores, modelo, ligacoes, refazer):
    con = bc.conectar()
    alvos = fila(con, limite, refazer, ligacoes)
    _log("▶ veredito por LIGACAO — o dossiê de todas as fontes numa chamada")
    _log("   %d ligação(ões) na fila" % len(alvos))
    if not alvos:
        return {"alvos": 0}
    secoes = ia._secoes_texto(con)
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
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    r = rodar(a.limite, a.aplicar, a.trabalhadores, a.modelo, a.ligacao,
              a.refazer)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
