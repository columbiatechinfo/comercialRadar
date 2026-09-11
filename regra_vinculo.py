# -*- coding: utf-8 -*-
"""Quando um POI pertence a uma ligação de água. A regra, num lugar só.

A REGRA, ditada pelo dono do produto em 08/09/2026. A rua tem de bater
SEMPRE. Além dela, uma destas quatro:

  1. o número da porta bate;
  2. a distância é menor que 10 m;
  3. é o mesmo telhado;
  4. o nome se parece com o de outro POI que JÁ entrou nesta ligação.

E UM VETO, decidido no mesmo dia com os números na mesa: quando a fonte
PUBLICOU um número de porta e ele diverge do número da ligação, os critérios
2 e 3 não valem. O critério 4 sobrevive ao veto.

────────────────────────────────────────────────────────────────────────────
POR QUE ESTE MÓDULO EXISTE

A regra anterior aceitava, dentro de 20 m, qualquer POI — a condição era
`mesmo_end or mesmo_num or perto`, um OU, e `perto` sozinho já admitia. Abaixo
de 20 m o endereço nunca era testado.

Medido em Canoas, 350.494 vínculos gravados: só 21,8% batiam rua E número;
61,2% batiam apenas a rua — que a quadra inteira bate, os dois lados dela —, e
14,8% não batiam nada. **256.548 vínculos (73%) juntavam um POI que publica um
número de porta diferente do da ligação.**

A consequência caía na IA. O dossiê escolhe as fotos de rua do POI mais
próximo do hidrômetro e as fotos publicadas do POI com mais avaliações, e
nenhum dos dois testes pergunta "é esta a porta". Em 81,6% das ligações
julgadas as fotos eram de um imóvel que não era o julgado.

O caso que o dono auditou: ligação 2173075, Braulino Pansera 350. Quatro
fontes independentes — IBGE e três da Receita — todas publicando "Braulino
Pansera, 365", todas a 18,1 m, todas grudadas no 350.

────────────────────────────────────────────────────────────────────────────
POR QUE O VETO NÃO VALE PARA O CRITÉRIO 4

Os critérios 2 e 3 são geometria — distância e telhado —, e geometria é
exatamente o que está falhando: o ponto do POI é um centroide (telhado, centro
do lote, eixo da via) e o da ligação é o hidrômetro, no meio-fio. Contra um
número que a fonte escreveu, eles perdem.

O critério 4 é de outra natureza: ele não fala de ONDE, fala de QUEM. A mesma
loja existe várias vezes na base — a Madeireira Maravilha aparece seis —, cada
cópia com a coordenada e o cadastro da sua fonte. Se uma delas identificou a
porta, as outras são o mesmo estabelecimento e entram junto, ainda que tragam
um número desatualizado. Por isso o critério 4 depende de uma ÂNCORA: um grupo
de nomes parecidos, sem ninguém ancorado, não diz nada sobre esta ligação.

SÓ O NÚMERO PUBLICADO VETA. `logradouro_resolvido.forca = 'prova'` marca o
endereço que a fonte escreveu; 'indicio' é o endereço deduzido da coordenada
por geocodificação reversa. Vetar a coordenada com um endereço tirado dela
seria circular — é a mesma guarda que o cruzamento já usa em `end_prova`.

────────────────────────────────────────────────────────────────────────────
POR QUE A SEMELHANÇA DE NOME É MEDIDA POR RARIDADE

Duas versões deste teste falharam antes desta, e do mesmo jeito: uma lista de
palavras a ignorar. Pus "VEICULOS" na lista depois que "TRINITA VEICULOS"
casou com "NOTRE DAME VEICULOS"; na amostra seguinte apareceram PIZZARIA,
RESIDENCIAL, ESTACIONAMENTO e NASCIMENTO — todos longos o bastante para passar
sozinhos numa regra de comprimento, e "Edifício Residencial Belvedere" casou
com "Condomínio Residencial Don Rodrigo", que são dois prédios diferentes.

A lista nunca fica pronta porque o problema não é o tamanho da palavra: é a
RARIDADE dela. Medido nos 117.393 nomes de POI de Canoas:

    PIZZARIA ....... 219 nomes ... peso  6,28
    NASCIMENTO ..... 311 nomes ... peso  5,93
    RESIDENCIAL .... 834 nomes ... peso  4,95
    METROCASA ........  4 nomes ... peso 10,06
    TONELADA .........  2 nomes ... peso 10,57

Somando o peso dos tokens em comum, os pares conhecidos se separaram sem
sobreposição: todo par errado ficou em 6,28 ou menos, todo par certo em 10,06
ou mais. O corte em 9,0 fica no vão entre os dois, e não na borda de nenhum.
"""
import math
import re
import unicodedata

#: Tokens sem conteúdo: forma jurídica e conectivo. A lista para AQUI de
#: propósito — ramo ("PIZZARIA", "FERRAGEM") não entra mais, porque a raridade
#: já o desqualifica e mantê-lo na lista tirava dele o pouco peso que tem.
#: Foi o que zerou "SM MINIMERCADO" × "MINIMERCADO SUPER MAGO" na primeira
#: versão: eu havia proibido a única palavra que os dois tinham em comum.
VAZIOS = {
    "LTDA", "ME", "EPP", "EIRELI", "SA", "S", "A", "CIA", "MEI", "O", "AS",
    "DE", "DA", "DO", "DAS", "DOS", "E", "EM", "COM", "POR", "NO", "NA",
    # TITULO NAO IDENTIFICA NINGUEM — e, pior, ele vira SIGLA.
    #
    # "Dra Raquel Machry" x "Consultorio Odontologico Dr. Marcelo Lima" somou
    # 12,82 SEM UM TOKEN EM COMUM, e por isso quase virou fusao em 10/09/2026.
    # O caminho: `_expandir_sigla` leu "DR" do segundo nome como as iniciais de
    # "DRA RAQUEL" no primeiro — D, R, consecutivas — e somou o peso das duas
    # palavras, inclusive o do sobrenome, que e' raro e vale muito.
    #
    # A sigla so podia soletrar isso porque "DRA" e' um token comum a metade
    # dos consultorios da cidade e ainda assim contava como palavra. Retirado
    # daqui, o par volta a somar zero, que e' o certo: dois dentistas
    # diferentes na mesma rua.
    "DR", "DRA", "DRS", "DRAS", "SR", "SRA", "SRS", "SRAS",
    "PROF", "PROFA", "ME", "MED",
}

#: A distância que dispensa o número, no critério 2.
PERTO_M = 10.0

#: O TETO DE DISTANCIA, QUE VALE ATE PARA O ENDERECO EXATO. Decisao do dono
#: do produto em 10/09/2026: "no maximo 50 metros mesmo sendo o mesmo
#: endereco e numero".
#:
#: A regra estrita nao tinha teto nenhum, e isso escapou na primeira medicao
#: porque eu contei quantos batiam rua e numero, nunca a que distancia. Medido
#: depois, nos 101.637 vinculos exatos vivos: 84.561 ate 50 m, 8.446 entre 50
#: e 200 m, 5.799 ate 1 km, 2.146 ate 5 km e **685 acima de 5 km** — o pior
#: par a 6.436 km, rua e numero identicos em outro estado.
#:
#: Sao duas causas, e nenhuma das duas e vinculo: o mesmo nome de rua se
#: repete em bairros diferentes (Canoas tem varias "Rua Sao Jose"), e ha POI
#: com coordenada errada que casa por texto. Em ambos os casos o par so
#: existe porque a comparacao e de STRING, e string nao sabe onde fica.
TETO_M = 200.0

#: O TETO SUBIU DE 50 PARA 200 M EM 10/09/2026, e o bairro entrou junto.
#:
#: O QUE MOSTROU QUE 50 ERA APERTADO: os 102 POIs do iFood que publicam o
#: endereco EXATO de uma ligacao de Canoas — rua e numero iguais, letra por
#: letra — e ficavam de fora. 48 deles entre 51 e 100 m, 23 entre 100 e 200.
#: O caso classico e o Canoas Shopping: onze lojas da praca de alimentacao na
#: Guilherme Schell 6750, todas a 82-100 m do hidrometro, porque o pino do
#: iFood marca a ENTRADA do shopping e nao a loja.
#:
#: Nao e so do iFood: a mesma coisa acontece com o pino do Maps num predio
#: grande, com o centroide do lote na Receita e com o ponto do CNEFE na
#: fachada errada de um terreno de esquina. Por isso a regra vale para TODAS
#: as fontes — decisao do dono do produto no mesmo dia.
#:
#: O BAIRRO E O QUE PERMITE AFROUXAR SEM ABRIR A PORTA. A 200 m cabe a quadra
#: inteira e as vizinhas, e "Rua Sao Jose, 150" existe em varios bairros da
#: mesma cidade. Com rua + numero + cidade + BAIRRO batendo, o par so
#: sobrevive se as quatro coisas concordarem — e o bairro esta preenchido em
#: 100% das ligacoes de Canoas.
#:
#: QUANDO NAO HA BAIRRO dos dois lados, o teto continua valendo sozinho: e o
#: caso da fonte que nao publica bairro, e exigir o que ela nao tem seria
#: exclui-la inteira.


def _bairro_bate(c):
    """Bairro da ligacao e do POI concordam — ou um dos dois nao existe?"""
    a, b = c.get("bairro_lig"), c.get("bairro_poi")
    if not a or not b:
        return True
    return set(normalizar(a)) & set(normalizar(b)) != set()

#: Quanto os tokens em comum precisam somar para dois nomes serem o mesmo
#: negócio. Ver a calibração no cabeçalho: o pior par certo deu 10,06 e o
#: melhor par errado, 6,28.
PESO_MINIMO = 9.0

#: `{token: peso}`, montado por `carregar_pesos`. Fica vazio até alguém
#: carregar — e `parecidos` recusa a trabalhar sem ele, em vez de cair num
#: teste mais fraco em silêncio. Critério 4 é o único que sobrevive ao veto do
#: número; se ele degradar sem avisar, o vazamento volta por ali.
_PESO = {}
_PESO_DESCONHECIDO = 0.0


#: A ULTIMA PALAVRA DO NOME, e uma lista fechada. Nao e substring.
#:
#: O CNEFE do IBGE percorre o endereco inteiro e registra a unidade VAZIA do
#: mesmo jeito que registra a loja. E' dado bom — e' o recenseador dizendo que
#: passou ali e nao havia ninguem —, so que entrava no vinculo como se fosse
#: estabelecimento. Medido em 10/09/2026: 724 vinculos vivos, 629 do IBGE.
#:
#: A PRIMEIRA VERSAO DISTO FOI UMA REGEX CHUTADA, com `FECHAD\w*` e `VAGA`
#: soltos, e reprovou "Comercio de Fechaduras", "Chaveiro e Fechaduras Silva"
#: e "Mercado Vaga Livre" — comercio de verdade. Foi preciso ler os nomes que
#: existem na base para descobrir a forma deles, que e' regular:
#:
#:     VAGO · LOJA VAGA · ESTABELECIMENTO VAGO · VAZIO · DESOCUPADO
#:     DEPOSITO VAGO · SALA VAGA · FECHADO · SALA VAZIA · LOJA DESOCUPADA
#:     SALA COMERCIAL VAGA · TERRENO VAGO · PREDIO DESOCUPADO · PECA VAZIA
#:     ESTABELECINENTO VAGO   <- o erro de digitacao do proprio IBGE
#:
#: A palavra da vacancia e sempre a ULTIMA, e o nome e curto. O que vem antes
#: varia demais para listar — inclusive com erro de digitacao —, entao o teste
#: nao olha para isso. E o que salva o comercio: "CONDOMINIO FECHADO SUN
#: VILLAGE" termina em VILLAGE, "VAGAO MODA MASCULINA" em MASCULINA,
#: "SECRETARIA MUNICIPAL DE OBRAS" em OBRAS.
#:
#: OBRAS E CONSTRUCAO FICARAM DE FORA de proposito: na base sao quase sempre
#: empresa ("A C CONSTRUCAO LTDA", "EMPREITEIRA DE OBRAS RIEFF"), e nao imovel
#: em obra.
_FIM_VAZIO = {
    "VAGO", "VAGA", "VAGOS", "VAGAS",
    "VAZIO", "VAZIA", "VAZIOS", "VAZIAS",
    "DESOCUPADO", "DESOCUPADA", "DESOCUPADOS", "DESOCUPADAS",
    "FECHADO", "FECHADA", "FECHADOS", "FECHADAS",
}

#: Quantas palavras o nome pode ter e ainda ser descricao de vacancia. "SALA
#: COMERCIAL VAGA" tem tres; "SALA 2 SEM USO", quatro. Um nome longo que
#: termina numa dessas palavras e frase, nao rotulo.
_MAX_PALAVRAS_VAZIO = 4


def declara_vazio(nome):
    """O proprio nome do POI diz que a unidade esta vazia?

    SO O NOME, e nao a categoria: a categoria de um "VAGO" do CNEFE costuma vir
    preenchida com o uso ANTERIOR do imovel, e usa-la aqui reintroduziria o
    comercio que ja nao existe.
    """
    if not nome:
        return False
    s = unicodedata.normalize("NFKD", str(nome))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    ps = re.sub(r"[^A-Z0-9 ]+", " ", s).split()
    if not ps or len(ps) > _MAX_PALAVRAS_VAZIO:
        return False
    if ps[-1] in _FIM_VAZIO:
        return True
    # "SALA 2 SEM USO" — a unica forma em que a vacancia sao duas palavras.
    return len(ps) >= 2 and ps[-2:] == ["SEM", "USO"]


def normalizar(s):
    """MAIÚSCULA, sem acento, sem pontuação, sem número e sem token vazio."""
    if not s:
        return []
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return [t for t in s.split()
            if not t.isdigit() and len(t) >= 2 and t not in VAZIOS]


def carregar_pesos(con, cidade=""):
    """Conta em quantos nomes de POI cada token aparece, e vira peso.

    UMA VEZ POR EXECUÇÃO. São 117 mil nomes em Canoas e a conta leva segundos;
    refazê-la por par seria o gargalo do cruzamento inteiro.
    """
    global _PESO, _PESO_DESCONHECIDO
    cur = con.cursor()
    if cidade:
        cur.execute("""select coalesce(nome,'') from radar_comercial.pois
                        where fundido_em is null
                          and upper(coalesce(cidade,'')) = upper(%s)""",
                    (cidade,))
    else:
        cur.execute("""select coalesce(nome,'') from radar_comercial.pois
                        where fundido_em is null""")
    quantos = {}
    n = 0
    for (nome,) in cur:
        ts = set(normalizar(nome))
        if not ts:
            continue
        n += 1
        for t in ts:
            quantos[t] = quantos.get(t, 0) + 1
    if not n:
        raise RuntimeError("nenhum nome de POI para pesar")
    _PESO = {t: math.log(n / (1.0 + c)) for t, c in quantos.items()}
    # TOKEN QUE NUNCA VIMOS É RARO, e não neutro: um nome que nem aparece na
    # base desta cidade identifica mais, não menos.
    _PESO_DESCONHECIDO = math.log(n)
    return len(_PESO)


def _expandir_sigla(sigla, tokens):
    """Os tokens que essa sigla soletra, se ela soletrar algum.

    "SM" É SUPER MAGO, e sigla não é token — comparar conjuntos nunca casaria
    "SM MINIMERCADO" com "MINIMERCADO SUPER MAGO", que são a mesma loja
    escrita por duas fontes. Correção do dono do produto em 08/09/2026.

    A busca é por sequência CONSECUTIVA: as iniciais de "Super Mago" nessa
    ordem. Aceitar iniciais espalhadas pelo nome faria "SM" casar com "Silva
    Materiais Marcenaria" por qualquer par de palavras que começasse com S e
    com M, e a sigla deixaria de identificar.
    """
    n = len(sigla)
    if not (2 <= n <= 4):
        return None
    for i in range(len(tokens) - n + 1):
        if "".join(t[0] for t in tokens[i:i + n]) == sigla:
            return tokens[i:i + n]
    return None


def peso_do_par(a, b, detalhe=False):
    """Quanto os tokens em comum de dois nomes somam.

    Conta o que os dois nomes dizem em comum, seja escrito por extenso nos
    dois, seja abreviado num deles.

    Com `detalhe=True` devolve `(total, comuns, la, lb)` — `parecidos` precisa
    do conjunto para o teste do topo, e recalcula-lo do lado de fora criaria a
    segunda implementacao da expansao de sigla.
    """
    if not _PESO:
        raise RuntimeError(
            "carregar_pesos() não foi chamado — sem a tabela de raridade o "
            "critério de nome fica cego e casa 'Pizzaria' com 'Pizzaria'")
    la, lb = normalizar(a), normalizar(b)
    A, B = set(la), set(lb)
    comuns = A & B
    total = sum(_PESO.get(t, _PESO_DESCONHECIDO) for t in comuns)
    # AS SIGLAS DE UM LADO CONTRA AS PALAVRAS DO OUTRO, nos dois sentidos.
    # O peso é o das PALAVRAS soletradas, e não o da sigla: quem identifica é
    # "MAGO", e "SM" é só o modo de escrevê-lo curto.
    #
    # SO QUANDO JA HA ALGO EM COMUM: sigla CONFIRMA identidade, nao estabelece.
    #
    # Uma sigla de duas letras acha iniciais consecutivas em quase qualquer
    # nome — num nome de quatro palavras ha tres posicoes candidatas —, e o
    # peso somado e o das palavras soletradas, que costuma ser alto. Medido em
    # 10/09/2026, auditando fusoes:
    #
    #   "RB CONSULTORIA EM INVESTIMENTOS" x "CONDOMINIO RESIDENCIAL RESERVA DO
    #   BOSQUE"  = 18,12, sem um token em comum: RB leu "RESERVA BOSQUE".
    #   "CR Growth Marketing" x "Corretor Ramon Souza" = 16,52: CR leu
    #   "CORRETOR RAMON".
    #
    # Com a exigencia de um token literal em comum, os dois vao a zero e o caso
    # que motivou a sigla continua de pe: "SM MINIMERCADO" x "MINIMERCADO
    # SUPER MAGO" divide MINIMERCADO, e a sigla so acrescenta o peso de MAGO.
    if not comuns:
        return (total, comuns, la, lb) if detalhe else total
    for curtos, longos in ((la, lb), (lb, la)):
        for s in curtos:
            if s in comuns:
                continue
            achou = _expandir_sigla(s, longos)
            if not achou:
                continue
            for t in achou:
                if t not in comuns:
                    comuns.add(t)
                    total += _PESO.get(t, _PESO_DESCONHECIDO)
    if detalhe:
        return total, comuns, la, lb
    return total


def _topo_em_comum(comuns, la, lb):
    """A palavra que mais identifica um dos dois nomes esta no outro?

    POR QUE A SOMA NAO BASTA. O corte de 9,0 separa bem quando o que os dois
    nomes dividem e' UMA palavra rara. Ele nao separa quando sao DUAS palavras
    de ramo, cada uma comum, que somadas passam de 9,0. Medido em Canoas:

        ADVOGADOS 5,86 + ASSOCIADOS 6,75 = 12,61
        COMERCIO  4,37 + EXTERIOR   9,60 = 13,97

    Com isso, "Vellinho - Advogados Associados" quase fundiu com "Infinity
    Advogados & Associados", e "Claudia Neto Comercio Exterior" com "DPLog
    Solucoes em Comercio Exterior" — quatro empresas, duas fusoes erradas.

    E NAO ADIANTA EXIGIR UM TOKEN RARO: "EXTERIOR" vale 9,60 porque comercio
    exterior e' raro em Canoas. O idf mede raridade, e nao sabe distinguir
    "raro porque e' nome" de "raro porque e' especialidade".

    O que separa e' outra pergunta: cada nome tem uma palavra que o identifica
    — a de maior peso —, e e' ela que precisa aparecer no outro. "Posto APOLO"
    tem APOLO (10,99) em comum; "VELLINHO Advogados Associados" tem VELLINHO
    (o topo dele) fora. Basta que o topo de UM dos dois esteja: "SM
    MINIMERCADO" x "MINIMERCADO SUPER MAGO" casa porque MINIMERCADO e o topo
    do primeiro, ainda que MAGO seja o do segundo.
    """
    if not comuns:
        return False
    for lado in (la, lb):
        if not lado:
            continue
        topo = max(lado, key=lambda x: _PESO.get(x, _PESO_DESCONHECIDO))
        if topo in comuns:
            return True
    return False


def parecidos(a, b):
    """Os dois nomes falam do mesmo negócio?"""
    if not a or not b:
        return False
    total, comuns, la, lb = peso_do_par(a, b, detalhe=True)
    return total >= PESO_MINIMO and _topo_em_comum(comuns, la, lb)


def numero_limpo(s):
    """Só os dígitos, sem zero à esquerda, só o primeiro grupo.

    "08" e "8" são a mesma porta, e "00159" é "159" — sem isto a primeira
    medição inventou 42 conflitos que não existiam. E há lixo real no campo:
    um POI publicava "136092310200" para a porta 1360. O corte acima de seis
    dígitos é defesa contra isso: número de porta não tem sete dígitos.
    """
    if not s:
        return None
    m = re.search(r"\d+", str(s))
    if not m:
        return None
    d = m.group(0).lstrip("0") or "0"
    return d if len(d) <= 6 else None


def contradiz_numero(num_poi_publicado, num_ligacao, e_prova):
    """A fonte escreveu um número, e não é o desta ligação?"""
    if not e_prova:
        return False
    a, b = numero_limpo(num_poi_publicado), numero_limpo(num_ligacao)
    return bool(a and b and a != b)


#: As fontes que NAO publicam o endereco exato, e por isso escapam da regra.
#:
#: O Airbnb nao diz onde fica o imovel — e do desenho da plataforma, nao falha
#: de coleta: o anuncio mostra um circulo aproximado ate a reserva ser feita.
#: Exigir numero de porta de quem nunca o publica seria excluir a fonte
#: inteira, e hospedagem por temporada e justamente consumo comercial numa
#: ligacao residencial.
SEM_ENDERECO_EXATO = ("airbnb",)


def _dentro_do_teto(c):
    """O par existe no mesmo lugar do mundo?

    DISTANCIA DESCONHECIDA PASSA. `metros` vem do cruzamento e so falta quando
    um dos dois lados nao tem coordenada; recusar por ausencia de medida
    puniria o vinculo pelo que nao foi medido. Sao poucos, e o score os separa
    depois — la a distancia vale ponto, e sem numero ela vale zero.
    """
    m = c.get("metros")
    if m is not None and m > TETO_M:
        return False
    # ACIMA DE 50 M O BAIRRO PASSA A SER EXIGIDO. Ate 50 m o par esta na
    # mesma quadra e o bairro nao acrescenta; entre 50 e 200 ele e o que
    # separa a "Rua Sao Jose, 150" de um bairro da do outro.
    if m is not None and m > 50.0:
        return _bairro_bate(c)
    return True


def aceitar(candidatos):
    """`{poi_id: motivo}` — quais candidatos pertencem a ESTA ligação.

    Cada candidato é um dict com:
        poi, fonte, mesma_rua, mesmo_numero, mesmo_telhado, metros, nome,
        contradiz

    ENDERECO EXATO E A UNICA PORTA — regra do dono do produto em 09/09/2026:
    "o cruzamento sera feito de forma EXATA por endereco + numero + cidade".

    O QUE MUDOU, E POR QUE. Ate ontem havia quatro criterios de entrada:
    numero, distancia abaixo de 10 m, mesmo telhado e nome de ancora. Medido
    em Canoas: os tres ultimos aceitavam 35.641 vinculos, e 92% deles eram de
    POI que PUBLICA outro numero de porta. Eles nao eram criterios auxiliares
    — eram a porta por onde o vizinho entrava.

    OS OUTROS NIVEIS NAO SUMIRAM: viraram SCORE. Distancia, telhado, Street
    View, avaliacoes e fotos deixam de decidir quem entra e passam a medir
    quanta confianca o vinculo merece. A diferenca importa: antes eles criavam
    vinculo que nao existia; agora qualificam vinculo que o endereco ja provou.

    A CIDADE JA VEM GARANTIDA pelo cruzamento, que filtra os dois lados por
    ela antes de formar par. Nao ha como um POI de Gravatai chegar aqui como
    candidato de uma ligacao de Canoas.
    """
    fica = {}
    for c in candidatos:
        # UNIDADE VAZIA NAO E ESTABELECIMENTO, e o teste vem antes de tudo:
        # nao adianta o endereco ser exato se o que esta no endereco e uma
        # sala sem uso. Ver `declara_vazio`.
        if declara_vazio(c.get("nome")):
            continue
        fonte = (c.get("fonte") or "").strip().lower()
        if fonte in SEM_ENDERECO_EXATO:
            # O AIRBNB ENTRA POR RUA + TELHADO, e nao pelo nome.
            #
            # A primeira versao desta regra exigiu dele o mesmo que do nome de
            # ancora — rua, telhado E idf >= 9,0 — e a fonte inteira foi a
            # ZERO: titulo de anuncio ("Apartamento aconchegante perto do
            # centro") nunca soma 9,0 contra nome de comercio, porque nao e
            # nome de negocio nenhum. Exigir idf do Airbnb e exigir uma coisa
            # que a fonte nao tem.
            #
            # O que sobra e geometria, e aqui ela basta: rua + mesmo telhado
            # sao duas condicoes independentes, e o telhado e o teste mais
            # forte que existe sem numero de porta — os dois pontos caem sobre
            # a MESMA construcao. Decisao do dono do produto em 10/09/2026.
            if (c.get("mesma_rua") and c.get("mesmo_telhado")
                    and _dentro_do_teto(c)):
                fica[c["poi"]] = "airbnb_rua_telhado"
            continue
        # TODO O RESTO: rua E numero, sem excecao e sem consolo — E DENTRO
        # DO TETO. Rua e numero sao texto; o teto e o unico teste que pergunta
        # se o par existe no mesmo lugar do mundo.
        if c.get("mesma_rua") and c.get("mesmo_numero") and _dentro_do_teto(c):
            fica[c["poi"]] = "endereco_exato"

    # O NOME DE ANCORA CONTINUA, e so ele — porque nao fala de onde, fala de
    # quem. A mesma loja existe varias vezes na base, cada copia com o cadastro
    # da sua fonte; se uma identificou a porta, as outras sao o mesmo
    # estabelecimento. Sem isto, a Madeireira Maravilha entraria uma vez e as
    # outras cinco testemunhas dela seriam descartadas.
    ancoras = [c for c in candidatos if fica.get(c["poi"]) == "endereco_exato"]
    if not ancoras:
        return fica
    for c in candidatos:
        if c["poi"] in fica or not c.get("mesma_rua"):
            continue
        # O TELHADO PASSOU A SER OBRIGATORIO AQUI. Decisao do dono do produto
        # em 10/09/2026: "nos casos de nome e airbnb tem que estar pelo menos
        # na mesma rua e >= 9,0 de idf e como mesmo telhado".
        #
        # O que isso corrige: dos 8.385 vinculos de nome vivos, so 282 (3,4%)
        # estavam no mesmo telhado, e a distancia media era 23,1 m — ou seja,
        # a semelhanca de nome estava juntando o mesmo negocio em ENDERECOS
        # diferentes (uma rede com duas lojas na mesma rua, uma filial), que e
        # justamente o que nao se quer numa ligacao de agua: cada porta tem o
        # seu hidrometro.
        if not c.get("mesmo_telhado"):
            continue
        if not _dentro_do_teto(c):
            continue
        if any(parecidos(c.get("nome"), a.get("nome")) for a in ancoras):
            fonte = (c.get("fonte") or "").strip().lower()
            fica[c["poi"]] = ("airbnb_telhado_nome"
                              if fonte in SEM_ENDERECO_EXATO
                              else "nome_de_ancora")
    return fica


def motivo_da_recusa(c):
    """Por que este candidato não entrou. Vai gravado em `descartado_motivo`."""
    if declara_vazio(c.get("nome")):
        return "o proprio nome diz que a unidade esta vazia"
    if not c.get("mesma_rua"):
        return "rua diferente da ligacao"
    fonte = (c.get("fonte") or "").strip().lower()
    if fonte in SEM_ENDERECO_EXATO:
        if not c.get("mesmo_telhado"):
            return "airbnb sem o telhado da ligacao"
        return "airbnb no telhado, mas fora do teto de distancia"
    if c.get("mesmo_numero") and not _dentro_do_teto(c):
        # O MOTIVO CARREGA O NUMERO porque este e o descarte que mais parece
        # erro: rua e numero batem, e mesmo assim cai.
        return ("endereco exato, mas a %d m (teto de %d m)"
                % (round(c.get("metros") or 0), round(TETO_M)))
    if not c.get("mesmo_numero"):
        if not c.get("mesmo_telhado"):
            return "numero diferente e nem o mesmo telhado"
        return "numero diferente; telhado bate, mas o nome nao acha ancora"
    return "mesma rua e numero, mas recusado"
