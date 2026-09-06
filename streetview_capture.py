"""
streetview_capture.py — Print do Street View para cada POI localizado (GRÁTIS).

Para cada POI válido do banco com coordenada e sem streetview_path, abre o
panorama do Street View na coordenada via Playwright headless (o Google Earth
Pro é app desktop — automatizá-lo por mouse/teclado para milhares de POIs é
inviável; o panorama do Maps é o mesmo acervo, em lote e headless), tira um
screenshot e grava os BYTES em streetview_imgs (angulo='facade'). Atualiza
pois.streetview_path (o modal exibe via /api/sv/<poi_id>/facade).

Sem pano na coordenada → marca 'NA' (não tenta de novo).

USO:
  .venv\\Scripts\\python streetview_capture.py [--workers 3] [--limit N] [--refazer]
"""

import re
import os
import json
import math
import time
import asyncio
import argparse
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

import config
import realtime_ingest

BASE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Depois que o pano carrega, a URL vira /@CAM_LAT,CAM_LNG,3a,... (posição da câmera).
_RE_CAM = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),3a")


def _bearing(lat1, lng1, lat2, lng2) -> float:
    """Ângulo (0-360°, N=0) da câmera (1) em direção ao estabelecimento (2)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

# ── "Não tem panorama" precisa de prova, não de desistência ────────────────
#
# O navegador desistir em 12 s NÃO é a mesma coisa que não existir foto ali:
# tile lento, proxy bloqueado e aba pendurada dão exatamente o mesmo silêncio.
# E `NA` é PERMANENTE — o POI sai da fila da captura e, sem imagem, some também
# da avaliação por IA. Medido em 14/08/2026 numa amostra de 40 marcados: 7
# tinham panorama, alguns de 2025. Isso é 18% de exclusão definitiva por engano.
#
# O endpoint de metadados responde a pergunta certa, sem ambiguidade e sem
# custo: a Google não cobra requisição de metadados do Street View.
_META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

# RAIO DA BUSCA DO PANORAMA.
#
# Com 50 m, 265 POIs de Canoas voltavam como "sem panorama" tendo foto entre 50
# e 100 m: coordenada caída dentro da quadra, não falta de cobertura. O usuário
# conferiu à mão a Av. Farroupilha 4803 — avenida coberta de ponta a ponta — e
# estava certo: a marca era do raio, não do Google.
#
# 300 m porque a regra é a do usuário, 14/08/2026: "o objetivo é se pôr de
# frente para o local do POI e tirar o print; se ele está no meio da quadra, se
# coloque na via de frente para ele". Ponto no meio da quadra não é motivo para
# desistir — é motivo para ir até a via mais próxima e MIRAR. Distância vira
# problema de enquadramento, e enquadramento se resolve com `fov`, abaixo.
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


def revisar_na(conn=None) -> int:
    """Reabre os 'NA' que na verdade TÊM panorama.

    Varre todos os marcados como sem panorama e pergunta aos metadados. Quem
    tiver foto volta para a fila da captura. Roda de novo sem problema: quem
    realmente não tem panorama continua 'NA'.
    """
    fechar = conn is None
    conn = conn or realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""select id, coalesce(maps_lat, lat_origem),
                                  coalesce(maps_lng, lng_origem)
                             from pois
                            where coalesce(streetview_path,'') = 'NA'
                              and coalesce(maps_lat, lat_origem) is not null""")
            alvos = cur.fetchall()
        if not alvos:
            return 0
        print(f"🔎 conferindo {len(alvos):,} marcados como sem panorama…", flush=True)
        # Em paralelo porque é consulta de metadados, não captura: são chamadas
        # curtas e sem cota. Sequencial, 700 POIs levariam minutos para
        # responder uma pergunta que leva segundos.
        import concurrent.futures as _cf
        reabrir, indef, feitos = [], 0, 0
        with _cf.ThreadPoolExecutor(max_workers=12) as ex:
            futuros = {ex.submit(tem_panorama, la, lo): pid for pid, la, lo in alvos}
            for fut in _cf.as_completed(futuros):
                pid = futuros[fut]
                r = fut.result()
                if r is True:
                    reabrir.append(pid)
                elif r is None:
                    indef += 1
                feitos += 1
                if feitos % 200 == 0:
                    print(f"   {feitos:,}/{len(alvos):,} · {len(reabrir)} com panorama",
                          flush=True)
        if reabrir:
            with conn, conn.cursor() as cur:
                cur.execute("update pois set streetview_path = '' where id = any(%s)",
                            (reabrir,))
        print(f"   ♻️ {len(reabrir):,} tinham panorama e voltaram para a fila"
              + (f" · {indef} indefinidos (sem chave ou erro de rede)" if indef else ""),
              flush=True)
        return len(reabrir)
    finally:
        if fechar:
            conn.close()


CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb.20220419-08-p0.pt+FX+410",
     "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAESHAgBEhJnd3NfMjAyMjA0MTktMF9SQzEaAnB0IAEaBgiAo_KTBg",
     "domain": ".google.com", "path": "/"},
]


def carregar_alvos(limit: int, refazer: bool, ids: list = None) -> list:
    conn = realtime_ingest.conectar()
    try:
        # Antes de decidir quem falta, devolver à fila quem ficou preso entre os
        # dois mundos. Sem isto o `filtro` abaixo pula exatamente esses POIs.
        soltar_presos(conn)
        with conn.cursor() as cur:
            if ids:
                cur.execute("""
                    SELECT id, nome, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                    FROM pois
                    WHERE id = ANY(%s) AND COALESCE(maps_lat, lat_origem) IS NOT NULL
                    ORDER BY id""", (ids,))
            else:
                # 'NA' ENTRA DE NOVO A CADA RODADA — decisão do usuário em
                # 14/08/2026. O Street View publica cobertura nova o tempo todo,
                # e a marca de "sem panorama" foi provada falha demais para ser
                # definitiva: 159 dos 720 marcados tinham foto. Custa uma
                # consulta de metadados por POI, que é grátis, e o que
                # realmente não tem panorama continua não tendo.
                filtro = ("" if refazer else
                          "AND (streetview_path IS NULL OR streetview_path IN ('', 'NA'))")
                cur.execute(f"""
                    SELECT id, nome, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                    FROM pois
                    WHERE match_valido IS NOT FALSE
                      AND COALESCE(maps_lat, lat_origem) IS NOT NULL
                      {filtro}
                    ORDER BY id""")
            alvos = [{"id": i, "nome": n, "lat": la, "lng": lo} for i, n, la, lo in cur.fetchall()]
            return alvos[:limit] if limit > 0 else alvos
    finally:
        conn.close()


def soltar_presos(conn=None) -> int:
    """Liberta o POI que está marcado como capturado e não tem imagem.

    É o rastro deixado pelo defeito corrigido em 14/08/2026: `streetview_path`
    gravado mesmo quando o byte não chegou ao Storage. O POI nesse estado sai
    das DUAS listas ao mesmo tempo — não entra na captura, porque tem `path`, e
    não entra na avaliação por IA, porque não tem linha em `streetview_imgs`.
    Ele desaparece do processo sem aparecer em contador nenhum.

    `'NA'` fica de fora de propósito: ali a ausência de imagem é o próprio
    resultado — não existe panorama naquela coordenada, e reabrir a captura
    seria repetir a viagem toda rodada para receber a mesma resposta.
    """
    fechar = conn is None
    conn = conn or realtime_ingest.conectar()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE pois p SET streetview_path = ''
                 WHERE COALESCE(p.streetview_path, '') NOT IN ('', 'NA')
                   AND NOT EXISTS (SELECT 1 FROM streetview_imgs s
                                    WHERE s.poi_id = p.id AND s.angulo = 'facade')""")
            n = cur.rowcount
        if n:
            print(f"   ♻️ {n} POIs estavam marcados como capturados sem imagem "
                  f"nenhuma — voltaram para a fila da captura", flush=True)
        return n
    finally:
        if fechar:
            conn.close()


def _gravar_path(poi_id: int, path: str, conn):
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE pois SET streetview_path = %s WHERE id = %s", (path, poi_id))


def _gravar_imagem(poi_id: int, dados: bytes, lat, lng, **extra):
    """A fachada vai para `streetview_imgs`, não para um .jpg na pasta.

    `lat`/`lng` são os da CÂMERA, não os do POI. A coluna descreve onde a foto
    foi tirada, e é dela que sai a distância até o alvo — número que o prompt
    precisa para o modelo calibrar o que está vendo. Guardar ali a coordenada
    do POI seria repetir o que `pois` já tem e perder a única informação que só
    a captura conhece.

    Os BYTES vão para o Storage desde 12/08/2026; a tabela guarda o caminho.
    Substitui a linha 'facade' anterior deste POI: recapturar é justamente para
    trocar a foto, e acumular versões só incharia a tabela e o bucket."""
    import imagens
    conn = realtime_ingest.conectar()
    try:
        return imagens.gravar_streetview(poi_id, dados, lat, lng, conn, **extra)
    finally:
        conn.close()


# Quanto esperar o Maps entrar em modo panorama.
#
# Eram 12 s, medidos numa aba só: os acertos vinham em 3,6–4,1 s, e 12 parecia
# folga de três vezes. Com 4 workers na mesma instância a folga evapora — a
# mesma página que abria em 4 s passa de 12 e a captura conta falha, com o POI
# voltando para a fila para gastar tudo de novo na próxima rodada.
#
# 25 s custa esperar mais no caso ruim e não custa nada no bom: quem abre em 4 s
# continua abrindo em 4 s. Só entra em jogo quando a alternativa era desistir.
ESPERA_PANO_S = 25


#: A marca de que a aba ESTÁ num panorama, independentemente de coordenada.
#:
#: `_RE_CAM` exige `@<lat>,<lng>,3a` — coordenada decimal da câmera E o `3a`. É
#: uma URL que o Maps produz na maioria das vezes, e não sempre: medido em
#: 04/09/2026 no POI 84666, o panorama abriu e a URL final ficou
#:
#:     /@0,0,0a,80y,190h,95t/data=!3m5!1e1!3m3!1sbS_DNbKiLK7KHubelxvd1w!2e0!...
#:
#: Zero, zero, zero-a. O panorama estava aberto — os dados dele estão ali, o id
#: pedido está ali —, o Maps só não resolveu a posição da câmera de volta para a
#: URL. A captura lia isso como fracasso e gravava "o Maps não entrou em modo
#: panorama", que é uma frase confiantemente errada: soa como "não há foto
#: aqui", e havia.
#:
#: O que separa panorama de vista aérea não é a coordenada: é o trio de ângulos
#: `<zoom>y,<rumo>h,<inclinação>t`, que só existe em panorama. Uma URL de mapa
#: traz `,17z/` e nunca um `y,` seguido de `h,`.
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


async def _capturar(page, alvo: dict) -> str | None:
    """Captura o pano ENCARANDO a fachada (heading câmera→estabelecimento).
    Retorna nome do arquivo salvo, 'NA' se sem pano."""
    lat, lng = alvo["lat"], alvo["lng"]
    try:
        # 1) PERGUNTA AOS METADADOS PRIMEIRO — quem é o panorama e onde ele está.
        #
        # Esta ordem inverteu-se em 14/08/2026 e é a correção que fez a captura
        # funcionar. Antes o navegador ia primeiro, pedindo o pano pela
        # coordenada do POI; ele falhava em 25 de 30 e a falha virava `NA`
        # permanente. Os metadados dizem, de graça e sem ambiguidade, se existe
        # panorama num raio de 50 m, QUAL é ele (`pano_id`) e ONDE a câmera
        # está — que é justamente o que faltava para mirar a fachada.
        m = await asyncio.to_thread(metadados_pano, lat, lng)
        if m is False:
            return "NA"          # o Google confirma: não há foto ali
        if m is None:
            # POR QUE FALHOU — o contador precisa dizer, senão "falhas subiu"
            # não aponta para lugar nenhum e a investigação começa do zero.
            alvo["_motivo"] = "metadados"
            return None
        # 2) DE FRENTE PARA O LOCAL. Com a câmera conhecida, o ângulo sai de uma
        #    conta — e o zoom sai da distância. É a regra do usuário: se o ponto
        #    está no meio da quadra, vá até a via mais próxima e MIRE nele.
        d = _dist_m(m["lat"], m["lng"], lat, lng)
        heading = _bearing(m["lat"], m["lng"], lat, lng)
        fov = _fov_por_distancia(d)
        if not await _abrir_por_id(page, m["pano_id"], heading, fov):
            # SEGUNDA TENTATIVA no mesmo caminho, e não no antigo.
            #
            # Os metadados já garantiram que o panorama existe, então não abrir
            # é transitório — tile lento, aba disputando banda com os outros
            # workers. Cair para `_achar_pano` aqui era trocar o método que
            # funciona pelo que falha em 25 de 30 por desenho, e ainda gastar
            # mais uma espera inteira para chegar ao mesmo lugar.
            await page.wait_for_timeout(1500)
            if not await _abrir_por_id(page, m["pano_id"], heading, fov):
                alvo["_motivo"] = "nao_abriu"
                return None      # não abriu: fila de novo, nunca 'NA'
        alvo["_dist_pano_m"] = round(d, 1)
        alvo["_fov"] = fov
        await page.wait_for_timeout(2500)  # tiles do panorama carregarem
        # Sem `path=`, o Playwright DEVOLVE os bytes — e é assim que a imagem vai
        # direto para `streetview_imgs`. Antes ela era salva em streetview/, e uma
        # segunda passada (`baixar_imagens.py --streetview`) a copiava para o
        # banco: o arquivo era só um intermediário que ninguém apagava, e a pasta
        # tinha chegado a 2,6 GB duplicando o que já estava gravado.
        png = await page.screenshot(type="jpeg", quality=72,
                                    clip={"x": 0, "y": 64, "width": 1280, "height": 656})
        # O RETORNO DE `_gravar_imagem` PRECISA SER OLHADO.
        #
        # Ele devolve None quando o byte não chegou ao Storage — e nesse caso
        # `gravar_streetview` já apagou a linha, justamente para não registrar
        # imagem que não existe. Ignorar esse None era o pior defeito desta
        # etapa: o POI voltava como "capturado", ganhava `streetview_path`, e
        # com isso saía PARA SEMPRE de duas listas ao mesmo tempo — a de quem
        # falta capturar (que filtra por `streetview_path` vazio) e a da
        # avaliação por IA (que faz JOIN em `streetview_imgs`, onde não há
        # linha). O log dizia "capturados 12/12" e a IA lia 8, sem que nada
        # explicasse os 4.
        # A CÂMERA vai NOMEADA (0012). `lat/lng` continua recebendo a mesma
        # coordenada por compatibilidade com quem já lê essa coluna, mas quem
        # precisa projetar na imagem usa `cam_*`/`heading`/`fov` — que só esta
        # função conhece e que antes se perdiam ao sair daqui.
        extra_cam = {"cam_lat": m["lat"], "cam_lng": m["lng"],
                     "heading": heading, "fov": fov}
        if _gravar_imagem(alvo["id"], png, m["lat"], m["lng"], **extra_cam,
                          pano_id=m["pano_id"], data_captura=m.get("data")) is None:
            alvo["_motivo"] = "storage"
            return None
        await _giro(page, alvo, m, heading, fov)
        return f"{alvo['id']}.jpg"
    except Exception as e:
        alvo["_motivo"] = f"erro:{type(e).__name__}"
        return None


# Quantas visadas ALÉM da fachada. O panorama é 360° e sempre foi: até aqui
# olhávamos um rumo só e jogávamos fora o resto.
# O GIRO COBRE OS QUATRO RUMOS, inclusive o 0°.
#
# O 0° repete a direção da fachada, e isso é de propósito: a foto `facade` tem
# fov variável (zoom pela distância) e por isso NÃO pode compor a faixa. O `g0`
# é a mesma direção em fov 90, para a costura fechar. Fica o close para ler
# letreiro de perto e a panorâmica para enxergar a cena — duas coisas
# diferentes, cada uma com o enquadramento que serve.
# OS DOIS FLANCOS, E NAO A VOLTA INTEIRA.
#
# Era `(0, 60, 120, 180, 240, 300)`: seis giros mais a fachada, SETE imagens por
# POI. Medido em 06/09/2026, 72 POIs: 479 imagens, 6,7 por ponto. Para os 27.756
# POIs de Canoas isso projetava 21 GB e mais de 30 h de navegador.
#
# DUAS COISAS ESTAVAM ERRADAS NESSE DESENHO:
#
# `g0` aponta para o MESMO rumo da fachada — era duplicata pura, um sexto do
# custo gasto em fotografar duas vezes a mesma coisa.
#
# E a volta completa era paga ADIANTADO para todo mundo, quando ela existe para
# um caso especifico: a leitura ve um muro e conclui "sem comercio". Esse caso
# ja tem tratamento proprio — `descrever_imagens` busca panoramas extras
# ANTES DE REPROVAR, e so para quem seria reprovado. Capturar 360 graus de todo
# POI e pagar o resgate de todos para socorrer alguns.
#
# Ficam a fachada e os dois flancos: cobre "do outro lado da via" e "na
# esquina", que sao os casos comuns. "Atras da camera" continua alcancavel pelo
# resgate, que e onde ele sempre esteve.
GIRO_GRAUS = (90, 270)

# FOV FIXO NO GIRO, e é requisito de costura, não preferência.
#
# O `fov` da fachada varia com a distância (80, 60, 40…) para dar zoom no
# imóvel. Isso é certo para a foto isolada e ERRADO para o giro: para os quadros
# emendarem num 360° sem vão nem sobreposição, o passo tem de ser igual ao fov.
#
# Passou de 4x90° para 6x60° porque MAIS QUADRO não melhora resolução — o modelo
# reduz a faixa para o orçamento de tokens dele, e faixa mais larga deixa cada
# loja MENOR na imagem que ele lê. O que melhora é a distorção: fov 90 estica as
# bordas de cada quadro, fov 60 estica muito menos, e é na borda que costuma
# estar o letreiro do vizinho.
FOV_GIRO = 60


async def _giro(page, alvo, m, heading, fov) -> None:
    """O GIRO DO PANORAMA — o entorno, não só a fachada.

    O comércio procurado pode estar do outro lado da via, na esquina, ou atrás
    da câmera. Encarando um rumo só, a leitura via um muro e concluía, com
    razão para o que enxergava e errado para o lugar, que não havia comércio.

    Isto já existia em `descrever_imagens.py` como protocolo de RESGATE, e só
    rodava para POI já reprovado: 2.368 pontos tinham o giro, 24.572 não. Aqui
    ele passa a ser padrão da captura, que é o momento em que o panorama já está
    aberto e o custo é só reabrir mirando outro rumo.

    Falha em um rumo não derruba nada: a fachada já está gravada, e uma visada a
    menos é uma imagem a menos, não um ponto perdido.
    """
    for g in GIRO_GRAUS:
        try:
            if not await _abrir_por_id(page, m["pano_id"], (heading + g) % 360, FOV_GIRO):
                continue
            await page.wait_for_timeout(2000)
            png = await page.screenshot(type="jpeg", quality=72,
                                        clip={"x": 0, "y": 64,
                                              "width": 1280, "height": 656})
            _gravar_imagem(alvo["id"], png, m["lat"], m["lng"],
                           cam_lat=m["lat"], cam_lng=m["lng"],
                           heading=(heading + g) % 360, fov=FOV_GIRO,
                           pano_id=m["pano_id"], data_captura=m.get("data"),
                           angulo=f"g{g}")
        except Exception:
            continue


async def worker(wid, fila: asyncio.Queue, ctx, counter, total, lock):
    page = await ctx.new_page()
    await page.set_viewport_size({"width": 1280, "height": 800})
    conn = realtime_ingest.conectar()
    try:
        while True:
            try:
                alvo = fila.get_nowait()
            except asyncio.QueueEmpty:
                return
            res = await _capturar(page, alvo)
            async with lock:
                counter["n"] += 1
                if res and res != "NA":
                    counter["ok"] += 1
                    _gravar_path(alvo["id"], res, conn)
                elif res == "NA":
                    counter["na"] += 1
                    _gravar_path(alvo["id"], "NA", conn)
                else:
                    # TERCEIRO DESFECHO, que antes não tinha nome nem contador:
                    # havia pano, mas a captura ou a gravação falhou. NÃO grava
                    # `streetview_path` — sem isso o POI volta na próxima
                    # rodada, que é o comportamento certo para falha
                    # transitória (Storage fora, timeout de tile).
                    counter["falhou"] += 1
                    mot = alvo.get("_motivo", "desconhecido")
                    counter.setdefault("motivos", {})
                    counter["motivos"][mot] = counter["motivos"].get(mot, 0) + 1
                print(f"📸 POIs {counter['n']}/{total} | capturados {counter['ok']} | "
                      f"sem pano {counter['na']} | falhas {counter['falhou']} | "
                      f"{alvo['nome'][:34]}", flush=True)
            await page.wait_for_timeout(500)
    finally:
        conn.close()
        try:
            await page.close()
        except Exception:
            pass


async def run(workers: int, limit: int, refazer: bool, ids: list = None):
    alvos = carregar_alvos(limit, refazer, ids)
    total = len(alvos)
    print(f"📸 Street View: {total} POIs para capturar | workers: {workers}", flush=True)
    if not total:
        print("Nada a capturar — todos os POIs válidos já têm print (ou 'NA').")
        return

    fila = asyncio.Queue()
    for a in alvos:
        fila.put_nowait(a)
    counter = {"n": 0, "ok": 0, "na": 0, "falhou": 0}
    lock = asyncio.Lock()
    ini = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="pt-BR", user_agent=UA)
        await ctx.add_cookies(CONSENT_COOKIES)
        await asyncio.gather(*[worker(i, fila, ctx, counter, total, lock)
                               for i in range(workers)])
        await browser.close()

    print(f"\n{'═'*52}")
    print(f"📸 Street View | Resumo")
    print(f"{'═'*52}")
    print(f"   Capturados : {counter['ok']}/{total}")
    print(f"   Sem pano   : {counter['na']}")
    if counter["falhou"]:
        print(f"   Falharam   : {counter['falhou']} — continuam na fila")
        # POR QUÊ, discriminado. "Falhas subiram" sem isto manda investigar do
        # zero: metadados, navegador e Storage têm causas e consertos distintos.
        rot = {"nao_abriu": "o Maps não entrou em panorama a tempo",
               "metadados": "os metadados não responderam (chave, cota ou rede)",
               "storage": "o byte não chegou ao Storage"}
        for mot, n in sorted(counter.get("motivos", {}).items(),
                             key=lambda kv: -kv[1]):
            print(f"      {n:>5}  {rot.get(mot, mot)}")
    print(f"   Tempo      : {(time.time()-ini)/60:.1f} min")
    print("   💾 gravado em streetview_imgs (banco)")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--refazer", action="store_true", help="Recaptura mesmo quem já tem print")
    p.add_argument("--ids", default="", help="POIs específicos, ex: 1644,31861")
    p.add_argument("--ids-arquivo", dest="ids_arquivo", default="",
                   help="arquivo com um id de POI por linha")
    a = p.parse_args()
    # A LISTA POR ARQUIVO, porque a consulta padrão pega o ESTADO INTEIRO e
    # `--ids` não cabe na linha de comando.
    #
    # O recorte que interessa em Canoas são os 26.209 POIs que cruzaram com
    # uma ligação da concessionária — a lista que a IA vai julgar. Capturar
    # fachada dos 118 mil da cidade seria 25 h de navegador para imagens que
    # ninguém abre. Qual é o recorte é decisão de quem roda, e por isso entra
    # por fora em vez de virar mais um filtro aqui dentro.
    ids = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else []
    if a.ids_arquivo:
        with open(a.ids_arquivo, encoding="utf-8") as f:
            ids += [int(l.strip()) for l in f if l.strip()]
        print("   %d POI(s) lidos de %s" % (len(ids), a.ids_arquivo), flush=True)
    asyncio.run(run(a.workers, a.limit, a.refazer, ids))


if __name__ == "__main__":
    main()
