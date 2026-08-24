"""Marca a foto: o alvo, a fachada e os elementos detectados.

Serve a três propósitos que costumam ser confundidos:

1. DIZER AO MODELO QUAL IMÓVEL É O ALVO. Hoje isso é uma frase — "o alvo está
   no CENTRO" — competindo com o resto do prompt. Um marcador desenhado é
   evidência visual, e ataca o erro que mais apareceu nos testes: classificar a
   loja do vizinho. É o caso 6 da especificação, "fachada ambígua ou
   compartilhada".

2. TORNAR A LEITURA AUDITÁVEL. Afirmar que há duas portas independentes exige
   APONTAR as duas — a evidência e a afirmação passam a ser a mesma coisa.
   (A leitura de MEDIÇÃO foi removida: em vinte fachadas o modelo não detectou
   um único medidor, e insistir só produzia `false` afirmando ausência que
   ninguém verificou. Medição virou dado de campo.)

3. DAR AO SUPERVISOR o que ele precisa para decidir em segundos, no dossiê.

As caixas vêm do próprio Qwen3-VL, que faz grounding 2D nativo e devolve
`bbox_2d` em escala 0–1000 normalizada. Não há detector separado: o mesmo
modelo que classifica aponta onde viu.
"""
from __future__ import annotations

import io
import math

# Cores por elemento, seguindo a legenda das imagens de instrução. Fixas de
# propósito: cor sorteada por execução faz a mesma fachada parecer diferente
# entre duas rodadas, e o supervisor perde a referência visual.
PALETA = {
    "porta_cartas": (255, 149, 20),
    "ramal": (168, 80, 220),
    "interfone": (232, 72, 168),
    "porta": (240, 200, 40),
    "janela": (40, 200, 200),
    "placa_comercial": (232, 48, 48),
    "numero_predial": (255, 80, 80),
    "marcador_google": (255, 0, 128),
    "fachada_alvo": (0, 122, 255),
    "fachada_vizinha": (150, 150, 150),
}
COR_ALVO = (235, 30, 30)
_PADRAO = (200, 200, 200)


def _bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Rumo em graus de 1 para 2. Mesma fórmula usada na captura."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def posicao_do_alvo(cam_lat, cam_lng, poi_lat, poi_lng, heading, fov) -> float:
    """Onde o POI cai na horizontal, como fração de 0 a 1.

    A captura aponta a câmera PARA o POI, então na prática isto devolve 0,5 — e
    é o resultado certo, não um cálculo vazio: se algum dia a foto for capturada
    com heading diferente do rumo (pano reaproveitado, captura por id de
    panorama), o marcador acompanha em vez de mentir no centro.
    """
    if None in (cam_lat, cam_lng, poi_lat, poi_lng, heading, fov):
        return 0.5
    delta = (_bearing(cam_lat, cam_lng, poi_lat, poi_lng) - heading + 540) % 360 - 180
    # Projeção linear dentro do campo de visão. Aproximação boa perto do centro,
    # que é onde o alvo está; nas bordas a lente distorce e o erro cresce — daí
    # o corte: fora do quadro, devolve a borda em vez de um número inventado.
    return min(max(0.5 + delta / fov, 0.0), 1.0)


COR_VIZINHO = (120, 120, 130)


def marcar_vizinhos(dados: bytes, pontos: list, cam_lat, cam_lng,
                    heading: float, fov: float, fy: float = 0.60,
                    escrever_numero: bool = False) -> bytes:
    """Projeta endereços do CNEFE na foto — os vizinhos, em cinza, com o número.

    Existe porque a mira sozinha nao resolve o erro mais caro: ela diz ONDE esta
    o alvo, nao ONDE ELE TERMINA. Com construcoes contiguas o modelo estica a
    caixa do alvo por cima da casa do lado, e nao ha na foto nada que marque a
    divisa.

    A base de POIs nao serve para isso — ela tem ESTABELECIMENTOS, e numa rua
    residencial o POI mais proximo pode estar a dezenas de metros. O CNEFE tem
    ponto por ENDERECO, inclusive residencia: sao 111 milhoes de linhas com
    quadra, face e numero.

    `pontos` são tuplas (lat, lng, numero). Cada uma vira um traço cinza na
    coluna onde aquele endereço cai, com o número por cima — o limite do lote
    passa a sair por exclusão em vez de adivinhação.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return dados
    im = Image.open(io.BytesIO(dados)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    try:
        fonte = ImageFont.truetype("arial.ttf", max(11, w // 85))
    except Exception:
        fonte = ImageFont.load_default()
    y = int(fy * h)
    vistos = set()
    for lat, lng, numero in pontos or []:
        fx = posicao_do_alvo(cam_lat, cam_lng, lat, lng, heading, fov)
        # Fora do quadro: `posicao_do_alvo` devolve a borda, e marcar na borda
        # inventaria um vizinho que a foto nao mostra.
        if fx <= 0.02 or fx >= 0.98:
            continue
        x = int(fx * w)
        if any(abs(x - v) < w // 60 for v in vistos):
            continue                      # mesmo endereco repetido: um traco basta
        vistos.add(x)
        d.line([x, y - h // 22, x, y + h // 22], fill=COR_VIZINHO,
               width=max(2, w // 640))
        # NÚMERO DESENHADO VIRA NÚMERO LIDO — por isso o padrão é não escrever.
        # Na primeira versão eu rotulava cada traço com o número do endereço, e
        # o modelo devolveu QUATRO `numero_predial` exatamente sobre as minhas
        # etiquetas: ele leu a anotação como se estivesse pintada no muro. O
        # dado falso sairia com aparência de dado observado. Os números vão no
        # TEXTO do prompt, onde não podem ser confundidos com a fachada.
        if not escrever_numero:
            continue
        rot = str(numero or "?")
        cx = d.textbbox((0, 0), rot, font=fonte)
        lw = cx[2] - cx[0]
        d.rectangle([x - lw // 2 - 3, y - h // 22 - (cx[3] - cx[1]) - 6,
                     x + lw // 2 + 3, y - h // 22 - 2], fill=COR_VIZINHO)
        d.text((x - lw // 2, y - h // 22 - (cx[3] - cx[1]) - 4), rot,
               fill=(255, 255, 255), font=fonte)
    saida = io.BytesIO()
    im.save(saida, format="JPEG", quality=92)
    return saida.getvalue()


def marcar_alvo(dados: bytes, fx: float = 0.5, fy: float = 0.60) -> bytes:
    """Mira vermelha sobre o imóvel avaliado.

    `fy` em 0,60 e não no meio: o alvo é a FACHADA, que fica na metade de baixo
    da foto. Marcar no centro geométrico cai no céu ou no telhado do fundo.

    A mira é vazada — duas linhas e um círculo aberto — para não cobrir o que o
    modelo precisa ler. Marcador cheio sobre a porta apagaria justamente o
    número e o medidor.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return dados
    im = Image.open(io.BytesIO(dados)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    x, y = int(fx * w), int(fy * h)
    r = max(14, w // 55)
    esp = max(3, w // 400)
    # GUIA VERTICAL de cima a baixo. A mira sozinha nao bastou: num POI cuja
    # coordenada caia sobre uma OBRA, o modelo pulou para a casa acabada ao lado
    # — ela "parece mais" uma fachada. A linha inteira amarra a coluna da imagem
    # ao alvo e nao deixa a escolha ambigua. Tracejada e fina para nao apagar o
    # que precisa ser lido.
    for yy in range(0, h, max(12, h // 40)):
        d.line([x, yy, x, min(yy + max(6, h // 80), h)], fill=COR_ALVO,
               width=max(1, esp - 1))
    d.ellipse([x - r, y - r, x + r, y + r], outline=COR_ALVO, width=esp)
    for x0, y0, x1, y1 in ((x - r * 2, y, x - r - 3, y), (x + r + 3, y, x + r * 2, y),
                           (x, y - r * 2, x, y - r - 3), (x, y + r + 3, x, y + r * 2)):
        d.line([x0, y0, x1, y1], fill=COR_ALVO, width=esp)
    d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=COR_ALVO)
    saida = io.BytesIO()
    im.save(saida, format="JPEG", quality=92)
    return saida.getvalue()


def desenhar_caixas(dados: bytes, deteccoes: list, rotular: bool = True) -> bytes:
    """Desenha as `bbox_2d` devolvidas pelo modelo.

    `deteccoes` é a lista do grounding: `[{"label": ..., "bbox_2d": [x0,y0,x1,y1]}]`
    com as coordenadas em 0–1000, independentes da resolução — é o formato
    nativo do Qwen3-VL, e por isso não há reescala a acertar aqui.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return dados
    im = Image.open(io.BytesIO(dados)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    try:
        fonte = ImageFont.truetype("arial.ttf", max(12, w // 70))
    except Exception:
        fonte = ImageFont.load_default()
    for det in deteccoes or []:
        cx = det.get("bbox_2d") or det.get("bbox")
        if not cx or len(cx) != 4:
            continue
        rot = str(det.get("label") or det.get("rotulo") or "")
        cor = PALETA.get(rot, _PADRAO)
        x0, y0, x1, y1 = (cx[0] / 1000 * w, cx[1] / 1000 * h,
                          cx[2] / 1000 * w, cx[3] / 1000 * h)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        esp = max(2, w // 500)
        d.rectangle([x0, y0, x1, y1], outline=cor, width=esp)
        if not rotular or not rot:
            continue
        cxa = d.textbbox((0, 0), rot, font=fonte)
        lw, lh = cxa[2] - cxa[0], cxa[3] - cxa[1]
        # Etiqueta ACIMA da caixa quando cabe; dentro, quando a caixa encosta no
        # topo. Etiqueta sobre a caixa esconderia o objeto que ela nomeia.
        ty = y0 - lh - 5 if y0 - lh - 5 > 0 else y0 + 2
        d.rectangle([x0, ty, x0 + lw + 6, ty + lh + 4], fill=cor)
        d.text((x0 + 3, ty + 2), rot, fill=(255, 255, 255), font=fonte)
    saida = io.BytesIO()
    im.save(saida, format="JPEG", quality=92)
    return saida.getvalue()


# ──────────────────────────────────────────────────────────────────────────
# Prompt de grounding — pede ONDE, não O QUE.
#
# Separado da classificação de propósito: misturar "aponte as caixas" com
# "classifique o imóvel" numa chamada só foi o erro que a skill de percepção
# cega já tinha documentado aqui. Cada chamada, uma tarefa.
# ──────────────────────────────────────────────────────────────────────────

GROUNDING_SISTEMA = """Você localiza objetos numa foto de fachada e devolve as \
coordenadas de cada um.

Aponte apenas o que você VÊ. Não marque objeto que você supõe existir por ser \
comum numa fachada. Lista vazia é resposta válida e correta quando não há nada \
do tipo procurado.

O que procurar:
· `porta_cartas` — caixa de correspondência, geralmente pequena e com fenda.
· `ramal` — entrada aparente de energia ou água: eletroduto, cabo descendo pela \
parede, cavalete.
· `interfone` — botoeira ou campainha junto ao acesso.
· `porta` — cada acesso de pedestre ou veículo, contado separadamente.
· `janela` — abertura envidraçada DO IMÓVEL ALVO. Marque no máximo as do \
pavimento térreo e do primeiro pavimento, e só quando forem janelas distintas. \
NÃO desenhe uma caixa por vidro de fachada envidraçada contínua, nem por painel \
de revestimento, nem por vão de galpão: pano de vidro corrido é UMA janela. \
Janela do vizinho não se marca.
· `placa_comercial` — letreiro ou placa com nome de estabelecimento.
· `numero_predial` — a sequência de DÍGITOS que identifica o imóvel, pintada ou \
afixada na parede, muro, portão ou soleira. LETREIRO COM NOME NÃO É NÚMERO \
PREDIAL, mesmo que contenha algarismos: se o que você vê é o nome do \
estabelecimento, isso é `placa_comercial` e nada mais. Não havendo dígitos \
legíveis, não marque nada.
· `marcador_google` — pequeno distintivo circular branco que o Google desenha DENTRO da cena, na posição onde ele registra um estabelecimento. Não é interface da tela nem placa do imóvel: é um símbolo sobreposto ao panorama. Marque cada um que aparecer — a posição dele é a posição que o Google atribui ao ponto.
· `fachada_alvo` — a frente do imóvel que está SOB A MIRA VERMELHA.
· `fachada_vizinha` — as frentes dos imóveis ao lado, uma caixa cada.

A MIRA VERMELHA DEFINE O ALVO. Ela foi posicionada por coordenada de campo, \
não por aparência: o imóvel sob ela é o avaliado, ponto final.
· Se a mira cai sobre uma OBRA INACABADA, um muro sem construção ou um terreno, \
é ISSO o alvo. Marque a caixa ali mesmo.
· NUNCA escolha a construção ao lado por ela parecer mais uma fachada — por \
estar acabada, ter portão, ter número na parede ou estar melhor enquadrada. \
Imóvel bonito ao lado da mira é `fachada_vizinha`.
· A caixa da `fachada_alvo` NÃO deve invadir o imóvel vizinho. Onde muda o \
padrão construtivo, muda o lote: pare a caixa ali.
· Porta, janela e número que pertençam ao vizinho não devem ser \
marcados — só os do imóvel sob a mira.

Cada objeto é uma entrada separada: quatro janelas são quatro entradas, não uma \
caixa cobrindo todas. Coordenadas em `bbox_2d` como [x0, y0, x1, y1] na escala \
de 0 a 1000."""

GROUNDING_USUARIO = """O imóvel avaliado está no CENTRO da imagem, marcado com \
a mira vermelha quando ela estiver presente.

Localize os objetos e devolva as coordenadas."""

ROTULOS = ["porta_cartas", "ramal", "interfone", "porta", "janela",
           "placa_comercial", "numero_predial", "marcador_google",
           "fachada_alvo", "fachada_vizinha"]

GROUNDING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["deteccoes"],
    "properties": {
        "deteccoes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["label", "bbox_2d"],
            "properties": {
                "label": {"type": "string", "enum": ROTULOS},
                "bbox_2d": {"type": "array", "items": {"type": "integer"},
                            "minItems": 4, "maxItems": 4},
            }}},
    },
}


# ---------------------------------------------------------------------------
# A PANORÂMICA — quatro quadros viram UMA cena
# ---------------------------------------------------------------------------
LETRAS = "ABCDEFGH"


def galeria(quadros: dict, passo: int = 60, vao: int = 30) -> tuple | None:
    """As visadas numa imagem só, mas SEPARADAS de forma inequívoca.

    A tentativa anterior colava tudo numa faixa contínua, e o resultado foi
    pior que as fotos soltas: o modelo passou a ler a emenda como se fosse
    cena, reprovou pontos com TELENTREGA e Farmácias São João à vista, e o
    grounding desenhou caixas atravessando costuras, sobre céu e
    estacionamento. Colar não é o mesmo que compor — a faixa dizia ao modelo
    "isto é um lugar só", e ele acreditou.

    Aqui é o contrário: vão largo entre os quadros, moldura em cada um e uma
    LETRA na calha acima. O modelo recebe uma imagem, como pedido, mas nada
    nela sugere continuidade. É uma prancha de contatos, e prancha de contatos
    se lê como coleção.

    A letra fica NA CALHA, nunca sobre a fachada, e é letra e não número:
    número desenhado sobre imagem já foi lido como `numero_predial` antes.

    Devolve `(bytes, ordem)` — a ordem das letras é o que o prompt precisa para
    dizer qual quadro encara o endereço.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    graus = list(range(0, 360, passo))
    need = [f"g{g}" for g in graus]
    if not all(quadros.get(k) for k in need):
        return None
    ims = [Image.open(io.BytesIO(quadros[k])).convert("RGB") for k in need]
    h = min(i.height for i in ims)
    w = min(i.width for i in ims)
    ims = [(i if (i.height, i.width) == (h, w) else i.resize((w, h), Image.LANCZOS))
           for i in ims]

    cols = 3 if len(ims) % 3 == 0 else 2
    linhas = (len(ims) + cols - 1) // cols
    rot = max(26, h // 22)                       # altura da calha da letra
    largura = cols * w + (cols + 1) * vao
    altura = linhas * (h + rot) + (linhas + 1) * vao
    # fundo escuro: separa dos prints, que são de rua clara, sem parecer papel
    folha = Image.new("RGB", (largura, altura), (24, 26, 30))
    d = ImageDraw.Draw(folha)
    try:
        fonte = ImageFont.truetype("arialbd.ttf", int(rot * 0.8))
    except Exception:
        fonte = ImageFont.load_default()

    ordem = []
    for n, im in enumerate(ims):
        r, c = divmod(n, cols)
        x = vao + c * (w + vao)
        y = vao + r * (h + rot + vao)
        d.text((x + 4, y), f"{LETRAS[n]}", font=fonte, fill=(235, 238, 245))
        folha.paste(im, (x, y + rot))
        d.rectangle([x - 3, y + rot - 3, x + w + 2, y + rot + h + 2],
                    outline=(235, 238, 245), width=3)
        ordem.append((LETRAS[n], graus[n]))
    saida = io.BytesIO()
    folha.save(saida, format="JPEG", quality=84)
    return saida.getvalue(), ordem


def descrever_galeria(ordem: list) -> str:
    """A frase que explica a prancha ao modelo. Sem ela, seis fotos numa folha
    voltam a parecer uma cena só."""
    partes = []
    for letra, g in ordem:
        if g == 0:
            partes.append(f"{letra} = encarando o endereço avaliado")
        else:
            partes.append(f"{letra} = {g}° à direita de A")
    return ("A imagem é uma PRANCHA COM " + str(len(ordem)) + " FOTOS SEPARADAS do "
            "mesmo ponto da rua, cada uma com moldura branca e uma letra na "
            "margem. NÃO é uma cena contínua: entre uma foto e outra a câmera "
            "girou, então o mesmo prédio pode reaparecer noutra foto e um "
            "comércio contado duas vezes é erro. As fotos: " + " · ".join(partes) + ".")


def impressao(dados: bytes, lado: int = 9) -> str | None:
    """dHash: assinatura perceptual de 64 bits, em hexadecimal.

    Compara GRADIENTE entre pixels vizinhos numa miniatura cinza, não os bytes.
    Isso é o que importa aqui: a mesma foto reenviada pelo Google vem com outra
    compressão e outro tamanho, então hash de bytes não a reconhece — e ela
    chegava duas vezes ao modelo, ocupando duas vagas de contexto com a mesma
    prova. Duas fotos idênticas da Caixa, de 2022-07-05, foram o caso que
    apareceu na galeria.

    Sem dependência nova: 9x8 pixels e uma comparação por linha bastam.
    """
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(dados)).convert("L").resize(
            (lado, lado - 1), Image.LANCZOS)
    except Exception:
        return None
    px = list(im.getdata())
    bits = 0
    n = 0
    for y in range(lado - 1):
        for x in range(lado - 1):
            bits = (bits << 1) | (1 if px[y * lado + x] > px[y * lado + x + 1] else 0)
            n += 1
    return f"{bits:0{(n + 3) // 4}x}"


def _dist_hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def sem_repetidas(itens: list, chave="b", limite: int = 6) -> list:
    """Devolve a lista sem as imagens repetidas, preservando a PRIMEIRA.

    `limite` é a distância de Hamming abaixo da qual duas assinaturas contam
    como a mesma imagem. 6 em 64 bits tolera recompressão e recorte leve sem
    fundir fotos que apenas se parecem — duas fachadas da mesma rua ficam a 15
    ou mais de distância.

    A primeira é que fica porque a ordem já traz significado: as fotos vêm por
    id, e a mais antiga é a que tem data registrada com mais frequência.
    """
    vistas, saida = [], []
    for i in itens:
        b = i.get(chave) if isinstance(i, dict) else i
        h = impressao(b) if b else None
        if h and any(_dist_hamming(h, v) <= limite for v in vistas):
            continue
        if h:
            vistas.append(h)
        if isinstance(i, dict):
            i["impressao"] = h
        saida.append(i)
    return saida
