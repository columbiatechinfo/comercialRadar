# -*- coding: utf-8 -*-
"""Geometria e navegacao do Street View. Sem banco, sem fila, sem POI.

DE ONDE ISTO VEIO, e por que existe separado: ate 07/09/2026 estas funcoes
moravam dentro de `streetview_capture.py`, junto com um fluxo de captura que
gravava numa tabela — `streetview_imgs` — que foi aposentada. O fluxo acabou;
elas nao. Achar o panorama mais proximo de uma coordenada, calcular o rumo da
camera para ele, escolher o campo de visao pela distancia e abrir um pano pelo
id sao contas do Street View, nao daquele fluxo, e a captura nova
(`capturar_evidencia.py`) as usa exatamente iguais.

Apagar o arquivo inteiro deixou quatro modulos importando o que nao existia
mais, e um deles era a propria captura de fachada: ela morria no import, em
producao. Este modulo e o conserto — e a fronteira que faltava desde o
comeco, porque agora nao ha como aposentar um fluxo e levar a geometria junto
sem querer.

NADA AQUI ESCREVE. Se uma funcao precisar de conexao, ela nao e daqui.
"""
import re
import os
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE = Path(__file__).resolve().parent


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


_RE_CAM = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),3a")


def _bearing(lat1, lng1, lat2, lng2) -> float:
    """Ângulo (0-360°, N=0) da câmera (1) em direção ao estabelecimento (2)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


_META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"


RAIO_PANO_M = 300


def _fov_por_distancia(d: float) -> int:
    """Abertura da lente conforme a distância até o alvo.

    Sem isto, mirar de longe não adianta: a 125 m, com os 80° padrão, o
    estabelecimento vira um detalhe de dez pixels no meio de um quarteirão, e a
    IA descreve o quarteirão. Fechar o `fov` é o equivalente a dar zoom — o
    alvo volta a ocupar a foto.

    A curva é FROUXA de propósito. A primeira versão fechava mais — 45° a 60 m,
    30° a 120 m — e a prova em imagem reprovou: no Svariato, a 65 m, os 30°
    deram um close de muro, porque entre a câmera e o alvo havia um muro a
    poucos metros. Fechar o ângulo faz de qualquer obstáculo a foto inteira.
    Aberto, o obstáculo divide o quadro com o alvo e a leitura ainda acontece.

    Só fecha de verdade quando a distância deixa de ter jeito: a 125 m, com 25°,
    o Açougue Carne Fresca saiu legível — deu para ler o letreiro na parede do
    atacadista. Com os 80° padrão teria saído um quarteirão.
    """
    if d <= 40:
        return 80
    if d <= 90:
        return 60
    if d <= 150:
        return 40
    return 25


def metadados_pano(lat, lng, raio: int = RAIO_PANO_M) -> dict | bool | None:
    """O panorama mais próximo da coordenada.

    Devolve `{"pano_id", "lat", "lng", "data"}` quando existe, `False` quando o
    Google confirma que não há panorama, e `None` quando não deu para saber
    (sem chave, erro de rede, cota). None é diferente de False de propósito:
    quem não sabe não carimba 'NA'.
    """
    # A CHAVE DE SERVIDOR, E A DO JS COMO QUEDA.
    #
    # `MAPS_SERVER_KEY` nunca existiu neste `.env` — so ha `MAPS_JS_KEY` —, e
    # por isso esta funcao devolvia None para TUDO desde sempre. O efeito era
    # silencioso e caro: `metadados_pano` respondendo None significa "nao deu
    # para saber", a captura desiste, e `streetview_imgs` ficou com ZERO linhas
    # sem ninguem notar que a causa era uma variavel de ambiente.
    #
    # A chave do JS foi testada contra este endpoint em 04/09/2026 e e aceita:
    # a resposta veio `ZERO_RESULTS` (nao ha panorama naquele ponto), e nao
    # `REQUEST_DENIED`. Uma chave restrita por referenciador daria REQUEST_DENIED
    # — e o codigo trata isso como None, que e o comportamento certo.
    chave = ((os.environ.get("MAPS_SERVER_KEY") or "").strip()
             or (os.environ.get("MAPS_JS_KEY") or "").strip())
    if not (chave and lat is not None and lng is not None):
        return None
    # `source=outdoor` — a correção que fez a captura parar de fotografar o
    # INTERIOR DAS LOJAS.
    #
    # Sem este parâmetro, o endpoint devolve o panorama mais próximo de
    # QUALQUER coleção, e no centro comercial o mais próximo é frequentemente o
    # 360° que a própria loja publicou de dentro. Medido em 18/08/2026: 297 de
    # 820 visadas saíram como `interior`, e um terço dos POIs da área ficou sem
    # evidência alguma — enquanto a busca manual pelo endereço mostrava o Street
    # View externo existindo normalmente.
    #
    # Diagnostiquei isso antes como "vitrine em close confundida com interior".
    # Estava errado: eram interiores de verdade, e o problema era de escolha de
    # panorama, não de leitura.
    q = urllib.parse.urlencode({"location": f"{lat},{lng}", "radius": raio,
                                "source": "outdoor", "key": chave})
    try:
        with urllib.request.urlopen(f"{_META_URL}?{q}", timeout=20) as r:
            d = json.load(r)
    except Exception:
        return None
    st = d.get("status")
    if st == "ZERO_RESULTS":
        return False
    if st != "OK":
        return None      # OVER_QUERY_LIMIT, REQUEST_DENIED, INVALID_REQUEST
    loc = d.get("location") or {}
    if not (d.get("pano_id") and loc.get("lat") is not None):
        return None
    return {"pano_id": d["pano_id"], "lat": loc["lat"], "lng": loc["lng"],
            "data": d.get("date")}


def tem_panorama(lat, lng, raio: int = RAIO_PANO_M) -> bool | None:
    m = metadados_pano(lat, lng, raio)
    return None if m is None else bool(m)


CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb.20220419-08-p0.pt+FX+410",
     "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAESHAgBEhJnd3NfMjAyMjA0MTktMF9SQzEaAnB0IAEaBgiAo_KTBg",
     "domain": ".google.com", "path": "/"},
]


ESPERA_PANO_S = 25


_RE_PANO_ABERTO = re.compile(r"/@[^/]*,\d+(?:\.\d+)?y,\d+(?:\.\d+)?h,")


async def _abrir(page, url: str, segundos: float = ESPERA_PANO_S) -> tuple | None:
    """Abre a URL e espera a aba entrar em panorama.

    Devolve `(cam_lat, cam_lng)` quando o Maps escreve a posição da câmera na
    URL, e `(None, None)` quando ele entra em panorama sem escrevê-la — que
    também é sucesso, e era lido como falha (ver `_RE_PANO_ABERTO`). Devolve
    `None` só quando a aba não chegou a panorama nenhum.

    Quem chama trata o retorno como verdadeiro/falso; a coordenada da câmera já
    veio dos metadados, e não é daqui que ela é usada.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    fim = time.time() + segundos
    while time.time() < fim:
        m = _RE_CAM.search(page.url)
        if m:
            return float(m.group(1)), float(m.group(2))
        if _RE_PANO_ABERTO.search(page.url):
            return (None, None)
        await page.wait_for_timeout(400)
    return None


async def _achar_pano(page, lat, lng, heading=None) -> tuple | None:
    """Caminho antigo: pede o panorama pela COORDENADA DO POI.

    Mantido como reserva. Ele falha muito e por um motivo que não é erro nosso:
    `map_action=pano&viewpoint=` exige panorama praticamente EM CIMA do ponto, e
    a coordenada de um POI fica sobre a loja, não sobre a rua. Medido em
    14/08/2026 sobre 30 POIs que os metadados garantiam ter foto: **abriu 5**.
    Nos 25 restantes o Maps caiu na vista aérea, e a captura lia esse silêncio
    como "não existe panorama aqui" e carimbava `NA` para sempre. O pano estava
    a 5–20 m — distância de calçada.
    """
    q = (f"https://www.google.com/maps/@?api=1&map_action=pano"
         f"&viewpoint={lat},{lng}&hl=pt-BR")
    if heading is not None:
        q += f"&heading={heading:.0f}&pitch=5&fov=80"
    return await _abrir(page, q)


async def _abrir_por_id(page, pano_id: str, heading=None, fov: int = 80) -> tuple | None:
    """Abre o panorama PELO ID, mirando o alvo. Não depende de proximidade.

    Nos mesmos 10 POIs em que a coordenada falhava, abriu 10.
    """
    q = ("https://www.google.com/maps/@?api=1&map_action=pano"
         f"&pano={urllib.parse.quote(pano_id)}&hl=pt-BR")
    if heading is not None:
        q += f"&heading={heading:.0f}&pitch=5&fov={fov}"
    return await _abrir(page, q)


def _dist_m(la1, lo1, la2, lo2) -> float:
    dy = (la2 - la1) * 111320
    dx = (lo2 - lo1) * 111320 * math.cos(math.radians(la1))
    return math.hypot(dx, dy)
