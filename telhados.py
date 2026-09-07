# -*- coding: utf-8 -*-
"""telhados.py — o último critério: o POI órfão que divide telhado com a ligação.

O QUE ESTE PASSO FAZ, NA ORDEM QUE O DONO DO PRODUTO DESCREVEU

    1. pega o tile que enquadra uma ligação que já tem ao menos um POI vinculado
    2. extrai os telhados do tile — o contraste entre telhado, rua e quintal é
       forte, e isso é segmentação por contraste, não IA
    3. lança a coordenada da ligação sobre a segmentação e vê em qual telhado ela cai
    4. traz todos os POIs do raio que caem NO MESMO telhado
    5. compara os dados desses POIs com os que já se ligaram àquela ligação
    6. marca os POIs SEM VÍNCULO como suspeitos, com `origem='telhado'`

É O ÚLTIMO PROCESSO, e a ordem não é detalhe: ele só alcança POI que ficou
completamente órfão depois de endereço, número e distância. Um ponto que já casou
por endereço não precisa de telhado, e um telhado nunca deve sobrepor uma
evidência melhor. Por isso a marcação é SUSPEITA, com o menor nível de confiança
do sistema, e por isso ela mora em `ligacao_poi` com `origem` dizendo de onde
veio — quem lê a lista vê que está olhando um palpite.

POR QUE NÃO É A CLASSE `Telhado` QUE JÁ EXISTIA

`cruzar_ligacao.Telhado` decide "mesmo telhado" comparando a COR média num
quadradinho ao redor de cada ponto. Duas casas geminadas com a mesma telha leem
como o mesmo telhado, e um telhado com clarabóia lê como dois. Cor não é
geometria.

E ela tinha um erro de escala que nunca apareceu porque o critério nunca disparou:
`LADO_GRAUS = 0.0009` supõe um tile de ~200 m de lado. O tile que a captura
produz é 3840×2160 px a zoom 19, que a 0,2588 m/px dá **994 × 559 m** — cinco
vezes maior, e retangular, não quadrado. Procurar o tile de um ponto com essa
constante erra o alvo na maioria das vezes.

Aqui a geometria é feita direito: o tamanho sai do zoom e da latitude, e a
pertinência ao telhado sai de uma SEGMENTAÇÃO de verdade.

    python telhados.py --registrar                 # cataloga os tiles do disco
    python telhados.py --base 1 --aplicar          # levanta as suspeitas
    python telhados.py --base 1 --area canoas.json --aplicar
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import math
import os
import re
import sys
import unicodedata

import base_comum as bc

# ── geometria ──────────────────────────────────────────────────────────────

# Metros por pixel no Web Mercator: a circunferência da Terra dividida pelos
# pixels do mundo naquele zoom, corrigida pela latitude. É a mesma conta que o
# Google usa para desenhar o tile, então é a que reproduz o enquadramento.
CIRCUNFERENCIA_M = 156543.03392


def mpp(lat: float, zoom: int) -> float:
    return CIRCUNFERENCIA_M * math.cos(math.radians(lat)) / (2 ** zoom)


class Tile:
    """Um tile de satélite com a sua caixa geográfica e o mapa de telhados.

    O nome do arquivo carrega o CENTRO — `tile_r000_c000_-29.91955_-51.18093.png`
    — e o `session.json` da captura carrega o zoom. Com os dois mais o tamanho em
    pixels, a caixa é determinada; não é preciso metadado extra nem confiar na
    bounding box pedida, que é MENOR que o que o viewport de fato fotografou.
    """

    def __init__(self, caminho, lat, lng, zoom, largura, altura,
                 tile_id=None, sessao=""):
        self.caminho = caminho
        self.lat, self.lng = lat, lng
        self.zoom, self.largura, self.altura = zoom, largura, altura
        self.tile_id, self.sessao = tile_id, sessao
        self.modo = None                       # 'estilizado' | 'foto'
        m = mpp(lat, zoom)
        # graus por pixel: latitude é constante; longitude encolhe com o cosseno,
        # e é por isso que o meio-lado em graus difere nos dois eixos.
        self.dlat = (m / 111320.0)
        self.dlng = (m / (111320.0 * math.cos(math.radians(lat))))
        self.meia_lat = self.dlat * altura / 2.0
        self.meia_lng = self.dlng * largura / 2.0
        self._seg = None

    def usar_caixa(self, lat_min, lat_max, lng_min, lng_max):
        """Substitui a caixa calculada pela MEDIDA, quando ela existe.

        O mapa publica em `window.__caixa` o que de fato desenhou. É melhor que
        a conta pelo zoom por um motivo prático: a conta supõe que a imagem é
        exatamente o viewport pedido, e um mapa que ajusta o enquadramento
        (retina, barra de escala, arredondamento de zoom) desmente isso em
        alguns pixels — que a 0,13 m/px são metros no chão.
        """
        self.meia_lat = (lat_max - lat_min) / 2.0
        self.meia_lng = (lng_max - lng_min) / 2.0
        self.lat = (lat_max + lat_min) / 2.0
        self.lng = (lng_max + lng_min) / 2.0
        self.dlat = (lat_max - lat_min) / float(self.altura)
        self.dlng = (lng_max - lng_min) / float(self.largura)

    @property
    def caixa(self):
        return (self.lat - self.meia_lat, self.lat + self.meia_lat,
                self.lng - self.meia_lng, self.lng + self.meia_lng)

    def cobre(self, lat, lng) -> bool:
        a, b, c, d = self.caixa
        return a <= lat <= b and c <= lng <= d

    def pixel(self, lat, lng):
        """(coluna, linha) do ponto dentro da imagem, ou None se cair fora."""
        x = int(round((lng - (self.lng - self.meia_lng)) / self.dlng))
        y = int(round(((self.lat + self.meia_lat) - lat) / self.dlat))
        if 0 <= x < self.largura and 0 <= y < self.altura:
            return x, y
        return None


# ── a extração dos telhados ────────────────────────────────────────────────
#
# DUAS ENTRADAS POSSÍVEIS, E ELAS NÃO SE PARECEM
#
# 1. O MAPA ESTILIZADO que a etapa 4 já captura. O estilo fixado por
#    `MAPS_MAP_ID` desenha as construções como POLÍGONOS CHAPADOS sobre fundo
#    branco, com as ruas em preto. Medido em 12 tiles de Canoas: 46,6% branco
#    (fundo), 17,0% magenta e 10,7% salmão (as duas classes de construção),
#    10,6% preto (rua). Não há sombra, não há árvore em cima do telhado, não há
#    perspectiva — a construção JÁ VEM segmentada pelo Google.
#
# 2. FOTO DE SATÉLITE, se um dia houver. Aí não há cor chapada e é preciso
#    segmentar de verdade, por contraste.
#
# O modo é DETECTADO, não configurado: um mapa estilizado tem quase metade dos
# pixels no mesmo branco; uma foto não tem. Configurar isso seria criar um
# jeito de errar — apontar o modo "estilizado" para uma foto produziria zero
# construções e nenhum erro.
#
# A PRIMEIRA VERSÃO DESTE ARQUIVO SEGMENTAVA O MAPA ESTILIZADO COMO SE FOSSE
# FOTO. Rodou, levantou 647 suspeitas, e todas eram lixo: os "telhados" que ela
# encontrava eram as CAIXAS DE TEXTO dos nomes de POI. Só apareceu ao desenhar
# o resultado por cima do tile — o número sozinho parecia ótimo.
REDUCAO = 2

# As duas cores de construção, em HSV do OpenCV (H 0..179).
# Magenta: H≈146, S≈128, V≈240.  Salmão: H≈0, S≈60, V≈240.
HSV_MAGENTA = ((138, 70, 150), (158, 255, 255))
HSV_SALMAO = ((0, 30, 190), (12, 110, 255))
HSV_SALMAO2 = ((168, 30, 190), (179, 110, 255))   # o vermelho dá a volta em H

# O QUE CONTA COMO "UM TELHADO", EM METROS QUADRADOS.
#
# O piso descarta caco de antisserrilhado e ícone: nada com menos de 15 m² é
# construção onde alguém tem ligação de água.
#
# O TETO É O QUE IMPORTA, E ELE FOI MEDIDO. As duas cores do estilo não são a
# mesma coisa: o SALMÃO desenha construção individual, e o MAGENTA desenha ÁREA
# DE USO COMERCIAL — que, numa rua de comércio, é contínua pelo quarteirão
# inteiro. Nos três tiles de prova, o magenta do centro de Canoas saiu como um
# único componente de 7.303 m²; num quarteirão menos denso, a mediana foi 50 m².
#
# Um componente de 7.000 m² não diz "mesmo telhado", diz "mesmo quarteirão" — e
# aceitá-lo transformaria a suspeita em "todos os POIs da quadra são candidatos",
# que é ruído com aparência de resultado. Acima do teto o componente existe, mas
# não sustenta suspeita nenhuma.
TELHADO_MIN_M2 = 15.0
TELHADO_MAX_M2 = 1500.0

# `felzenszwalb` agrupa pixels vizinhos enquanto a diferença interna for menor
# que a diferença para fora — que é exatamente "o telhado é uniforme e a borda
# dele contrasta". `scale` regula o tamanho típico do grupo; `min_size` joga
# fora cacos. Os dois estão em PIXELS DA IMAGEM REDUZIDA.
SEG_SCALE = 300
SEG_SIGMA = 0.8
SEG_MIN_PX = 120

# Quanto um segmento pode medir para ainda ser um telhado. Abaixo do piso é
# caco, caixa d'água ou carro; acima do teto é quadra inteira, mato ou avenida —
# o segmentador funde o que é contínuo, e asfalto é muito contínuo.
TELHADO_AREA_MIN_M2 = 25.0
TELHADO_AREA_MAX_M2 = 6000.0

# Um galpão é grande. É o sinal mais forte de cobertura comercial que existe na
# imagem, e é geométrico — não depende de calibrar cor.
COMERCIAL_AREA_M2 = 250.0

RAIO_TELHADO_M = 45.0


def _norm(t):
    t = unicodedata.normalize("NFD", str(t or "").upper())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9 ]", " ", t).strip()


# ONDE O MAPA DE TELHADOS FICA GUARDADO.
#
# NÃO É AO LADO DO TILE, e a primeira versão tentou: o volume `radar-capturas`
# pertence ao root e o contêiner roda como usuário comum, então a gravação morre
# com `Permission denied` no meio do passo. E mesmo com permissão estaria errado
# — a API monta `capturas` como somente-leitura de propósito, porque quem
# escreve ali é a captura, não quem lê.
PASTA_CACHE = os.environ.get("RADAR_TELHADO_CACHE", "/tmp/telhados")
_avisou_cache = [False]


def _caminho_cache(tile: Tile) -> str:
    # O nome do arquivo já é único (sessão + linha + coluna + coordenada), mas o
    # caminho inteiro entra achatado para dois tiles homônimos de sessões
    # diferentes não se sobrescreverem.
    chave = re.sub(r"[^A-Za-z0-9_.-]", "_", tile.caminho.lstrip("/"))
    return os.path.join(PASTA_CACHE, chave + ".seg.npz")


def _que_imagem_e(img):
    """Classifica o tile: 'estilizado', 'mapa_sem_construcao' ou 'foto'.

    SÃO TRÊS CASOS, E O DO MEIO É O QUE MORDEU. O sistema captura DUAS coisas
    diferentes que ambas são "um tile":

      · o mapa da varredura de placeId, cujo estilo desenha as construções
        como polígonos chapados — é o que serve aqui;
      · o mapa da captura de área, no estilo de ESTRADA, que não desenha
        construção nenhuma: é fundo bege com rótulo de POI por cima.

    A primeira versão só distinguia "mapa" de "foto", e o mapa de estrada caiu
    no ramo de foto. A segmentação por contraste então encontrou os retângulos
    de TEXTO dos nomes e os chamou de telhado: 647 suspeitas, todas lixo, e
    nenhum número denunciando isso.

    A distinção é por CONTAGEM DE CORES e não por brilho. Mapa vetorial usa
    algumas dezenas de cores chapadas; foto de satélite usa milhares. Depois,
    entre os mapas, o que decide é banal: TEM ou NÃO TEM a cor de construção.
    """
    import numpy as np

    amostra = img[::5, ::5].reshape(-1, 3)
    distintas = len(np.unique(amostra // 16, axis=0))
    if distintas > 400:
        return "foto"
    fr = _fracao_construcao(img)
    return "estilizado" if fr >= 0.02 else "mapa_sem_construcao"


def _fracao_construcao(img) -> float:
    """Quanto do tile está pintado com as cores de construção do estilo."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(img[::5, ::5], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, np.array(HSV_MAGENTA[0]), np.array(HSV_MAGENTA[1]))
    m |= cv2.inRange(hsv, np.array(HSV_SALMAO[0]), np.array(HSV_SALMAO[1]))
    m |= cv2.inRange(hsv, np.array(HSV_SALMAO2[0]), np.array(HSV_SALMAO2[1]))
    return float((m > 0).mean())


def _guardar_cache(cache, rotulos, props):
    import numpy as np

    try:
        os.makedirs(PASTA_CACHE, exist_ok=True)
        np.savez_compressed(cache, rotulos=rotulos.astype("int32"),
                            props=np.array(props, dtype=object))
    except OSError as e:
        if not _avisou_cache[0]:
            _log("   ⚠️  não consigo gravar o cache em %s (%s)."
                 % (PASTA_CACHE, e.__class__.__name__))
            _log("      A etapa continua — só fica mais lenta a cada execução.")
            _avisou_cache[0] = True


def _construcoes_por_cor(img, tile):
    """As construções do mapa estilizado, uma por componente conexo de cor.

    NÃO HÁ SEGMENTAÇÃO AQUI, e é o ponto: o Google já entregou a construção
    desenhada como polígono chapado. Casar a cor e rotular os componentes conexos
    devolve a planta baixa do quarteirão, com borda exata e sem palpite.

    AS DUAS CORES NÃO VALEM O MESMO. O magenta é a classe que o estilo usa para
    o que tem atividade — é o que se acende ao longo das ruas de comércio — e o
    salmão é a construção genérica. Por isso `comercial` sai da COR, e não de
    "claro e sem cor" como no modo foto: aqui a informação é categórica, não uma
    inferência sobre pixels.

    Dois prédios COLADOS na mesma cor viram um componente só. É o mesmo limite
    que uma foto teria (telhado contínuo é telhado contínuo), e o efeito no
    resultado é conhecido: a suspeita fica mais larga, nunca mais estreita.
    """
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mag = cv2.inRange(hsv, np.array(HSV_MAGENTA[0]), np.array(HSV_MAGENTA[1]))
    sal = cv2.inRange(hsv, np.array(HSV_SALMAO[0]), np.array(HSV_SALMAO[1]))
    sal |= cv2.inRange(hsv, np.array(HSV_SALMAO2[0]), np.array(HSV_SALMAO2[1]))

    m2_por_px = mpp(tile.lat, tile.zoom) ** 2
    props = {0: {"area_m2": 0.0, "telhado": False, "comercial": False,
                 "rgb": (255, 255, 255)}}
    rotulos = np.zeros(mag.shape, np.int32)
    proximo = 1

    # AS DUAS CORES SÃO ROTULADAS SEPARADAMENTE, e a primeira versão não fazia
    # isso: juntava tudo numa máscara só antes de contar componentes. O efeito
    # apareceu ao desenhar — um prédio magenta encostado num salmão virava UM
    # componente, e o quarteirão inteiro saiu como um telhado de 11.246 m².
    # Cor diferente é construção diferente; o desenho do mapa já disse isso.
    for mascara, e_comercial in ((mag, True), (sal, False)):
        # KERNEL 3×3, E NÃO 5×5. As construções vizinhas são separadas por uma
        # linha branca de 2 a 3 pixels; um fechamento de 5×5 ATRAVESSA essa
        # linha e cola o quarteirão. O 3×3 só cura o antisserrilhado da borda.
        nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        m = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, nucleo)
        m = _tapar_buracos(m)

        n, rot, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        for s in range(1, n):
            area = int(stats[s, cv2.CC_STAT_AREA]) * m2_por_px
            if area < TELHADO_MIN_M2:
                continue          # caco de antisserrilhado, ícone, borda
            rotulos[rot == s] = proximo
            props[proximo] = {
                "area_m2": area,
                "rgb": (240, 120, 224) if e_comercial else (240, 184, 184),
                # `telhado` é o que sustenta "os dois pontos estão no MESMO
                # telhado". Um componente acima do teto continua existindo e
                # continua marcando `comercial` — ele só não serve de prova de
                # que dois pontos são o mesmo prédio.
                "telhado": bool(area <= TELHADO_MAX_M2),
                "comercial": bool(e_comercial),
            }
            proximo += 1
    return rotulos, props


def _tapar_buracos(mascara):
    """Fecha os buracos INTERNOS da máscara, sem engordar a borda.

    O rótulo do POI é escrito em branco por CIMA da construção, e abre um vazio
    no meio dela. Um ponto que caísse ali leria "não é telhado" — pelo nome do
    próprio estabelecimento. Engordar a máscara resolveria isso e criaria o
    problema pior de colar construções vizinhas; tapar buraco não move borda
    nenhuma.

    O truque é o de sempre: inunda-se o FUNDO a partir da moldura; o que sobrar
    de preto sem ser alcançado está cercado, e portanto é buraco.
    """
    import cv2
    import numpy as np

    h, w = mascara.shape
    fora = mascara.copy()
    mold = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(fora, mold, (0, 0), 255)
    buracos = cv2.bitwise_not(fora)
    return cv2.bitwise_or(mascara, buracos)


def segmentar(tile: Tile):
    """Devolve (rotulos, propriedades) do tile.

    Segmentar 3840×2160 custa segundos; um tile serve a dezenas de ligações e a
    etapa é reentrante, então o resultado é guardado em `.npz`. Apagar o cache
    força a recontagem — é o que fazer depois de mexer nos parâmetros.

    CACHE QUE NÃO GRAVA NÃO DERRUBA O PASSO. Ele é otimização: sem ele a etapa
    fica lenta, não errada. Falhar aqui custaria a rodada inteira por causa de
    uma permissão de disco.
    """
    if tile._seg is not None:
        return tile._seg

    import numpy as np

    cache = _caminho_cache(tile)
    if os.path.exists(cache):
        try:
            d = np.load(cache, allow_pickle=True)
            tile._seg = (d["rotulos"], d["props"].item())
            return tile._seg
        except Exception:                                     # noqa: BLE001
            pass                    # cache corrompido: refaz em vez de quebrar

    import cv2

    img = cv2.imread(tile.caminho, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("não consegui ler o tile: " + tile.caminho)

    tile.modo = _que_imagem_e(img)

    if tile.modo == "estilizado":
        rotulos, props = _construcoes_por_cor(img, tile)
        _guardar_cache(cache, rotulos, props)
        tile._seg = (rotulos, props)
        return tile._seg

    if tile.modo == "mapa_sem_construcao":
        # UM TILE SEM CONSTRUÇÃO DESENHADA NÃO RESPONDE NADA, e dizer isso é o
        # conserto. Segmentar este mapa por contraste devolveria os retângulos
        # de texto dos rótulos como se fossem telhados — foi o que aconteceu na
        # primeira versão. Zero telhado é a resposta correta, e ela some da
        # conta sem inventar evidência.
        import numpy as np

        rotulos = np.zeros((tile.altura, tile.largura), np.int32)
        props = {0: {"area_m2": 0.0, "telhado": False, "comercial": False,
                     "rgb": (255, 255, 255)}}
        _guardar_cache(cache, rotulos, props)
        tile._seg = (rotulos, props)
        return tile._seg

    from skimage.segmentation import felzenszwalb

    peq = cv2.resize(img, (tile.largura // REDUCAO, tile.altura // REDUCAO),
                     interpolation=cv2.INTER_AREA)
    # Bilateral preserva a BORDA e alisa o miolo — telha tem textura, e sem isso
    # cada fiada de telha vira um segmento.
    peq = cv2.bilateralFilter(peq, 7, 45, 45)
    rgb = cv2.cvtColor(peq, cv2.COLOR_BGR2RGB)

    rotulos = felzenszwalb(rgb, scale=SEG_SCALE, sigma=SEG_SIGMA,
                           min_size=SEG_MIN_PX)

    m2_por_px = (mpp(tile.lat, tile.zoom) * REDUCAO) ** 2
    n = int(rotulos.max()) + 1
    achatado = rotulos.ravel()
    px = rgb.reshape(-1, 3).astype(np.float64)
    conta = np.bincount(achatado, minlength=n).astype(np.float64)
    soma = np.stack([np.bincount(achatado, weights=px[:, i], minlength=n)
                     for i in range(3)], axis=1)
    media = soma / np.maximum(conta, 1)[:, None]

    props = {}
    for s in range(n):
        area = float(conta[s] * m2_por_px)
        r, g, b = (float(x) for x in media[s])
        brilho = (r + g + b) / 3.0
        # Vegetação: verde acima dos outros dois. Sombra: escuro demais para
        # dizer qualquer coisa. Nenhum dos dois é telhado.
        verde = g > r + 8 and g > b + 8
        sombra = brilho < 42
        telhado = (TELHADO_AREA_MIN_M2 <= area <= TELHADO_AREA_MAX_M2
                   and not verde and not sombra)
        # Comercial: grande, ou claro e sem cor (fibrocimento e metálica, que
        # cobrem galpão e loja de rua). Telha cerâmica é vermelha e puxa
        # residencial. O LIMIAR DE COR AINDA NÃO FOI CALIBRADO contra fachada
        # conferida; a área, sim, é geométrica e não depende de calibração.
        claro = brilho >= 120
        sem_cor = (max(r, g, b) - min(r, g, b)) <= 26
        props[s] = {
            "area_m2": area, "rgb": (r, g, b), "telhado": bool(telhado),
            "comercial": bool(telhado and (area >= COMERCIAL_AREA_M2
                                           or (claro and sem_cor))),
        }

    _guardar_cache(cache, rotulos, props)
    tile._seg = (rotulos, props)
    return tile._seg


def segmento_de(tile: Tile, lat, lng):
    """Em qual telhado cai o ponto. (id, propriedades) ou (None, None).

    A ESCALA SAI DA FORMA DO ARRAY, e não de uma constante. Os dois modos
    produzem rótulos em resoluções diferentes — o estilizado em tamanho cheio, a
    foto reduzida por `REDUCAO`. Usar a constante nos dois faria o modo
    estilizado ler o pixel errado, deslocado pela metade do tile, e o erro seria
    invisível: devolveria um telhado, só que o do vizinho.
    """
    p = tile.pixel(lat, lng)
    if not p:
        return None, None
    rotulos, props = segmentar(tile)
    escala = max(1, int(round(tile.largura / float(rotulos.shape[1]))))
    x, y = p[0] // escala, p[1] // escala
    if not (0 <= y < rotulos.shape[0] and 0 <= x < rotulos.shape[1]):
        return None, None
    s = int(rotulos[y, x])
    return s, props.get(s)


# ── catálogo dos tiles no banco ────────────────────────────────────────────

# `tile_<qualquer coisa>_<lat>_<lng>.<png|webp>` — a convenção que a captura de
# tile e a varredura de placeId passaram a usar em 03/09/2026.
RE_TILE = re.compile(r"tile_.*?_(-?\d+\.\d+)_(-?\d+\.\d+)\.(png|webp)$")


def _log(m):
    print(m, flush=True)


def achar_tiles_no_disco(pasta="capturas"):
    """Os tiles georreferenciados de todas as sessões, de qualquer captura.

    DUAS PROCEDÊNCIAS, e as duas servem: o tile grande da captura de área
    (`tile_r000_c000_...png`, 3840×2160 no estilo de estrada) e os tiles da
    varredura de placeId (`tile_r_000_...webp`, 1280×900 no estilo que desenha
    as construções). O segundo é o que interessa para telhado; o primeiro entra
    porque nada custa e porque um dia pode haver satélite ali.

    A CAIXA VEM DO ÍNDICE QUANDO EXISTE. `_tiles.json` guarda o que o próprio
    mapa reportou ter desenhado (`window.__caixa`); calcular a caixa pelo zoom
    dá quase o mesmo, e o "quase" já custou caro uma vez.
    """
    from PIL import Image

    achados = []
    for raiz, _, arquivos in os.walk(pasta):
        indice = {}
        ij = os.path.join(raiz, "_tiles.json")
        if os.path.exists(ij):
            try:
                indice = json.load(open(ij, encoding="utf-8"))
            except Exception:                                 # noqa: BLE001
                indice = {}
        zoom_sessao = None
        for cand in (os.path.join(raiz, "session.json"),
                     os.path.join(os.path.dirname(raiz), "session.json")):
            if os.path.exists(cand):
                try:
                    zoom_sessao = int(json.load(open(cand))["config"]["zoomLevel"])
                    break
                except Exception:                             # noqa: BLE001
                    pass

        for a in sorted(arquivos):
            m = RE_TILE.search(a)
            if not m:
                continue
            caminho = os.path.join(raiz, a)
            base = os.path.splitext(a)[0]
            meta = indice.get(base, {})
            try:
                with Image.open(caminho) as im:
                    larg, alt = im.size
            except Exception:                                 # noqa: BLE001
                continue
            zoom = int(meta.get("zoom") or zoom_sessao or 19)
            t = Tile(caminho, float(m.group(1)), float(m.group(2)),
                     zoom, larg, alt,
                     sessao=os.path.basename(os.path.dirname(caminho)))
            if all(k in meta for k in ("lat_min", "lat_max", "lng_min", "lng_max")):
                t.usar_caixa(meta["lat_min"], meta["lat_max"],
                             meta["lng_min"], meta["lng_max"])
            achados.append(t)
    return achados


def registrar(pasta="capturas", aplicar=False) -> dict:
    """LISTA o que existe no disco. Nao cataloga mais nada no banco.

    # O TILE E RASCUNHO, E NAO ACERVO.
#
# Decisao do dono do produto em 07/09/2026: o tile e recapturado toda vez que a
# area roda, entao guarda-lo nao poupa nada — so ocupa disco e Storage. Sao
# 50.880 arquivos e 1,1 GB em `capturas/`, mais 51.504 linhas de catalogo.
#
# O QUE ELE PRECISA RESPONDER e "que tile cobre este ponto?", e para isso basta
# o disco: o nome do arquivo carrega o centro
# (`tile_r_008_-29.91725_-51.19778.webp`) e o `_tiles.json` ao lado guarda a
# caixa que o proprio mapa reportou ter desenhado. `achar_tiles_no_disco` ja
# lia tudo isso — a tabela era uma copia do que o diretorio ja sabia.
    #
    # O `--aplicar` continua aceito e nao faz nada: a etapa 10 do pipeline
    # ainda o passa, e mudar os dois de uma vez daria uma janela em que um
    # deles esta velho.
    """
    tiles = achar_tiles_no_disco(pasta)
    _log("   %d tile(s) georreferenciado(s) em %s/" % (len(tiles), pasta))
    for t in tiles:
        _log("      %-46s z%d %dx%d  %.0f x %.0f m"
             % (os.path.basename(t.caminho), t.zoom, t.largura, t.altura,
                t.largura * mpp(t.lat, t.zoom), t.altura * mpp(t.lat, t.zoom)))
    return {"tiles": len(tiles), "gravados": 0}


def limpar_tiles(pasta="capturas") -> dict:
    """Apaga os tiles do disco. Roda no fim da rodada.

    APAGA SO O QUE E TILE, pelo mesmo `RE_TILE` que os encontra — a pasta
    `capturas/` guarda tambem print de pagina e recorte, que nao sao rascunho
    e nao podem ir junto. E apaga o `_tiles.json` da sessao, que sem os tiles
    nao descreve mais nada.
    """
    n = bytes_ = 0
    for raiz, _, arquivos in os.walk(pasta):
        tinha = False
        for a in arquivos:
            if not RE_TILE.search(a):
                continue
            p = os.path.join(raiz, a)
            try:
                bytes_ += os.path.getsize(p)
                os.remove(p)
                n += 1
                tinha = True
            except OSError:
                pass
        if tinha:
            ij = os.path.join(raiz, "_tiles.json")
            if os.path.exists(ij):
                try:
                    os.remove(ij)
                except OSError:
                    pass
    _log("   %d tile(s) apagado(s), %.2f GB liberados"
         % (n, bytes_ / 1073741824.0))
    return {"apagados": n, "bytes": bytes_}


def carregar_tiles(con=None, pasta="capturas"):
    """Os tiles que existem no disco AGORA.

    # O TILE E RASCUNHO, E NAO ACERVO.
#
# Decisao do dono do produto em 07/09/2026: o tile e recapturado toda vez que a
# area roda, entao guarda-lo nao poupa nada — so ocupa disco e Storage. Sao
# 50.880 arquivos e 1,1 GB em `capturas/`, mais 51.504 linhas de catalogo.
#
# O QUE ELE PRECISA RESPONDER e "que tile cobre este ponto?", e para isso basta
# o disco: o nome do arquivo carrega o centro
# (`tile_r_008_-29.91725_-51.19778.webp`) e o `_tiles.json` ao lado guarda a
# caixa que o proprio mapa reportou ter desenhado. `achar_tiles_no_disco` ja
# lia tudo isso — a tabela era uma copia do que o diretorio ja sabia.
    #
    # `con` continua na assinatura e e ignorado: quem chama ja tem a conexao na
    # mao e trocar a chamada junto so aumentaria a superficie deste commit.
    """
    return achar_tiles_no_disco(pasta)


# ── a suspeita ─────────────────────────────────────────────────────────────

SQL_LIGACOES_COM_VINCULO = """
    select lp.ligacao,
           st_y(l.geom::geometry), st_x(l.geom::geometry),
           array_agg(lp.poi_id)
      from radar_comercial.ligacao_poi lp
      join {tabela} l on l.num_ligacao::text = lp.ligacao
     where lp.id_base = %s and lp.origem = 'criterio'
       and l.geom is not null
     group by 1,2,3
"""


def _semelhanca(a: dict, b: dict) -> tuple:
    """Quanto o POI órfão parece com um irmão já vinculado. (0..1, motivo)."""
    motivos = []
    nota = 0.0
    na, nb = _norm(a.get("nome")), _norm(b.get("nome"))
    if na and nb:
        r = difflib.SequenceMatcher(None, na, nb).ratio()
        if r >= 0.62:
            nota += 0.4 * r
            motivos.append("nome %.0f%%" % (100 * r))
    ca, cb = _norm(a.get("categoria")), _norm(b.get("categoria"))
    if ca and cb and ca == cb:
        nota += 0.3
        motivos.append("mesma categoria")
    va, vb = _norm(a.get("via")), _norm(b.get("via"))
    if va and vb and va == vb:
        nota += 0.3
        motivos.append("mesma via")
    return min(1.0, nota), ", ".join(motivos)


def suspeitar(base_id: int, area: str = "", aplicar: bool = False,
              raio: float = RAIO_TELHADO_M) -> dict:
    import numpy as np
    from scipy.spatial import cKDTree

    import area_utils

    poligono = area_utils.carregar_area(area) if area else None
    con = bc.conectar()
    cur = con.cursor()

    tiles = carregar_tiles(con)
    _log("   %d tile(s) no catálogo e no disco" % len(tiles))
    if not tiles:
        _log("   Sem tile não há telhado — a captura da área precisa ter")
        _log("   rodado ANTES, na mesma máquina: o tile vive em disco e é")
        _log("   apagado no fim da rodada. Este passo não inventa imagem.")
        con.close()
        return {"erro": "sem tile"}

    cur.execute("select tabela_dados, mapa_colunas from radar_comercial.base_cliente"
                " where id = %s", (base_id,))
    linha = cur.fetchone()
    if not linha:
        con.close()
        return {"erro": "base %s não existe" % base_id}
    tabela, mapa = linha
    mapa = mapa if isinstance(mapa, dict) else json.loads(mapa or "{}")
    col_via = mapa.get("endereco") or "endereco"

    cur.execute(SQL_LIGACOES_COM_VINCULO.format(tabela=tabela), (base_id,))
    ligacoes = cur.fetchall()
    _log("   %d ligação(ões) com vínculo por critério" % len(ligacoes))

    # OS POIS ÓRFÃOS: os que não têm vínculo NENHUM. São o público deste passo,
    # e é o que o torna barato — quem já casou por endereço não entra na conta.
    cur.execute("""
        select p.id, st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
               coalesce(p.nome,''), coalesce(p.categoria,''),
               coalesce(p.endereco,''), coalesce(p.fonte,'')
          from radar_comercial.pois p
         where p.pt_geo is not null
           and not exists (select 1 from radar_comercial.ligacao_poi z
                            where z.poi_id = p.id)
    """)
    orfaos = []
    for pid, la, lo, nome, cat, end, fonte in cur.fetchall():
        if poligono and not area_utils.ponto_no_poligono(la, lo, poligono):
            continue
        orfaos.append({"id": pid, "lat": la, "lng": lo, "nome": nome,
                       "categoria": cat, "via": end, "fonte": fonte})
    _log("   %d POI(s) órfão(s) na área" % len(orfaos))
    if not orfaos or not ligacoes:
        con.close()
        return {"orfaos": len(orfaos), "ligacoes": len(ligacoes), "suspeitas": 0}

    # Os dados dos POIs já vinculados, para a comparação de semelhança.
    cur.execute("""
        select p.id, coalesce(p.nome,''), coalesce(p.categoria,''),
               coalesce(p.endereco,'')
          from radar_comercial.pois p
         where exists (select 1 from radar_comercial.ligacao_poi z
                        where z.poi_id = p.id and z.id_base = %s)
    """, (base_id,))
    irmaos = {r[0]: {"nome": r[1], "categoria": r[2], "via": r[3]}
              for r in cur.fetchall()}

    # Índice espacial dos órfãos, em metros locais. Mesmo truque do cruzamento:
    # projeção equirretangular local, exata o bastante para 45 m.
    lat0 = sum(o["lat"] for o in orfaos) / len(orfaos)
    kx = 111320.0 * math.cos(math.radians(lat0))
    xy = np.array([[o["lng"] * kx, o["lat"] * 111320.0] for o in orfaos])
    arv = cKDTree(xy)

    registros = []
    placar = {"ligacoes_com_tile": 0, "ligacoes_sem_tile": 0,
              "fora_de_telhado": 0, "sem_orfao_no_telhado": 0}
    vistos = set()

    # O MENOR TILE PRIMEIRO. Vários tiles podem cobrir o mesmo ponto, e eles não
    # valem o mesmo: o tile de 166 m a zoom 20 distingue construções vizinhas; o
    # de 994 m a zoom 19 mistura o quarteirão. Pegar "o primeiro que cobre"
    # entregava o grande, que além de grosseiro é o estilo de estrada, sem
    # construção desenhada.
    tiles.sort(key=lambda t: t.meia_lat * t.meia_lng)

    for ligacao, llat, llon, pois_ligados in ligacoes:
        candidatos = [t for t in tiles if t.cobre(llat, llon)]
        if not candidatos:
            placar["ligacoes_sem_tile"] += 1
            continue
        placar["ligacoes_com_tile"] += 1

        # Entre os que cobrem, vale o primeiro que de fato responde: um tile sem
        # construção desenhada não é motivo para desistir do ponto se existe
        # outro melhor cobrindo o mesmo lugar.
        tile, seg, prop = None, None, None
        for cand in candidatos:
            s, p = segmento_de(cand, llat, llon)
            if s is not None and p and p["telhado"]:
                tile, seg, prop = cand, s, p
                break
        if tile is None:
            placar["fora_de_telhado"] += 1
            continue

        perto = arv.query_ball_point((llon * kx, llat * 111320.0), raio)
        achou = 0
        for i in perto:
            o = orfaos[i]
            if (ligacao, o["id"]) in vistos:
                continue
            s2, _ = segmento_de(tile, o["lat"], o["lng"])
            if s2 != seg:
                continue                     # no raio, mas em outro telhado
            melhor, motivo = 0.0, ""
            for pid in pois_ligados:
                irmao = irmaos.get(pid)
                if not irmao:
                    continue
                n, m = _semelhanca(o, irmao)
                if n > melhor:
                    melhor, motivo = n, m
            metros = math.hypot((o["lng"] - llon) * kx,
                                (o["lat"] - llat) * 111320.0)
            perto_20 = metros <= 20.0
            # A CONTAGEM É HONESTA: só marca os critérios que de fato valem.
            # Telhado e distância são os únicos que este passo mede; endereço e
            # número são falsos porque, se fossem verdade, o POI não seria órfão.
            criterios = 1 + int(prop["comercial"]) + int(perto_20)
            registros.append((
                base_id, str(ligacao), o["id"],
                False, False, perto_20, True, bool(prop["comercial"]),
                metros, criterios, round(criterios / 5.0, 3),
                # SEM `tile_id`. A coluna apontava para `tile_captura`, que
                # deixou de existir em 07/09/2026 — o tile virou rascunho da
                # rodada. A procedencia nao se perde: `origem='telhado'` diz o
                # criterio e `suspeita_motivo` diz a medida que o sustentou
                # ("telhado de 697 m2; sem semelhanca de dados"). Conferido nas
                # 98 linhas que tinham tile_id: as 98 ja traziam o motivo.
                o["fonte"] or None, "telhado",
                ("telhado de %.0f m2%s%s"
                 % (prop["area_m2"],
                    "; comercial" if prop["comercial"] else "",
                    ("; " + motivo) if motivo else "; sem semelhança de dados"))))
            vistos.add((ligacao, o["id"]))
            achou += 1
        if not achou:
            placar["sem_orfao_no_telhado"] += 1

    _log("")
    _log("   ligações com tile ............ %d" % placar["ligacoes_com_tile"])
    _log("   ligações sem tile ............ %d" % placar["ligacoes_sem_tile"])
    _log("   caíram fora de telhado ....... %d" % placar["fora_de_telhado"])
    _log("   telhado sem órfão dentro ..... %d" % placar["sem_orfao_no_telhado"])
    _log("   SUSPEITAS levantadas ......... %d" % len(registros))

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"orfaos": len(orfaos), "suspeitas": len(registros), **placar}

    # LISTA VAZIA NAO CHEGA AO BANCO, e por isso `rowcount` mente.
    #
    # `execute_values` com `registros` vazio nao executa comando nenhum, e
    # `cur.rowcount` continua descrevendo o comando ANTERIOR — que aqui era o
    # SELECT dos POIs orfaos. Medido em 04/09/2026: "SUSPEITAS levantadas 0"
    # seguido de "42.423 suspeita(s) gravada(s)". O dado estava certo e o
    # relatorio mentia, que e a combinacao que faz alguem confiar no numero
    # errado — ninguem confere 42 mil linhas por causa de um numero grande.
    if not registros:
        _log("   nada a gravar: nenhum POI órfão dividiu telhado com ligação")
        con.close()
        return {"orfaos": len(orfaos), "suspeitas": 0, "gravadas": 0, **placar}

    from psycopg2.extras import execute_values
    execute_values(cur, """
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fonte_poi, origem, suspeita_motivo)
        values %s
        on conflict (id_base, ligacao, poi_id) do nothing
    """, registros, page_size=1000)
    gravadas = cur.rowcount
    con.commit()
    _log("   %d suspeita(s) gravada(s) (as que já existiam por critério" % gravadas)
    _log("      ficam como estão — telhado nunca sobrepõe evidência melhor)")
    con.close()
    return {"orfaos": len(orfaos), "suspeitas": len(registros),
            "gravadas": gravadas, **placar}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registrar", action="store_true",
                   help="lista os tiles que existem no disco")
    p.add_argument("--limpar", action="store_true",
                   help="apaga os tiles do disco (fim da rodada)")
    p.add_argument("--pasta", default="capturas")
    p.add_argument("--base", type=int, default=0)
    p.add_argument("--area", default="")
    p.add_argument("--raio", type=float, default=RAIO_TELHADO_M)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    if a.limpar:
        _log("▶ limpando os tiles do disco")
        limpar_tiles(a.pasta)
        return 0

    if a.registrar:
        _log("▶ os tiles que existem no disco")
        r = registrar(a.pasta, a.aplicar)
        return 1 if r.get("erro") else 0

    if not a.base:
        p.error("informe --base <id> (ou --registrar)")
    _log("▶ telhados — o último critério, sobre os POIs órfãos")
    r = suspeitar(a.base, a.area, a.aplicar, a.raio)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
