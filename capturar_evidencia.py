# -*- coding: utf-8 -*-
"""capturar_evidencia.py — as quatro visadas de rua que a IA vai julgar.

O CONJUNTO, E POR QUE ELE MUDOU

    sv_frente   o panorama ENCARANDO a coordenada
    sv_lado_a   a mesma câmera, 90° à direita
    sv_fundo    a mesma câmera, 180°
    sv_lado_b   a mesma câmera, 270°

QUATRO VISADAS, E NÃO UMA — decisão do dono do produto em 04/09/2026, depois de
ver o resultado de três imagens. O objetivo deixou de ser "descreva a fachada
sob a mira" e passou a ser ACHAR O ESTABELECIMENTO, apareça ele em qual visada
aparecer. Um comércio de bairro fica com frequência na esquina, no fundo do
lote ou na lateral, e a visada única fechava a pergunta antes de olhar.

O SATÉLITE SAIU. Ele mostrava telhado e mais nada: nem letreiro, nem vitrine,
nem porta. Trazia o custo de uma imagem por POI e não decidia nenhum veredito.

O MARCADOR NÃO PRECISA DE PROJEÇÃO, e essa é a parte que quase virou trabalho
inútil. O `heading` que se pede ao Maps é o ângulo CÂMERA→POI — então o alvo
fica no centro horizontal da imagem por construção. Desenhar o marcador no
centro é exato; calcular onde ele cairia seria refazer a conta que já foi feita
para escolher o heading.

OS METADADOS VÊM ANTES DO NAVEGADOR, e isso é herança de um defeito caro. Pedir
o panorama pela coordenada do POI falha em 25 de 30 casos — a coordenada está
sobre a loja, e o Maps quer o panorama praticamente em cima do ponto. O
endpoint de metadados diz de graça se existe foto, QUAL é (`pano_id`) e ONDE a
câmera está; com isso o mesmo POI abre em 10 de 10.

    python capturar_evidencia.py --area area_atual --limite 20
    python capturar_evidencia.py --area area_atual --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import io as _io
import math
import os
import time

import area_utils
import base_comum as bc
import streetview_capture as sv

LARG, ALT = 1280, 900

# O SATÉLITE TEM JANELA PRÓPRIA, e menor de propósito.
#
# A 1280×900 no zoom 21 a vista cobre ~120 m — o quarteirão inteiro, com o
# imóvel virando um detalhe e o marcador virando um ponto. O que a IA precisa
# ver é o TELHADO do ponto e os vizinhos imediatos. Uma janela de 720×720 no
# mesmo zoom cobre ~52 m: a construção enquadrada, com contexto suficiente para
# dizer se ela é maior ou menor que as ao lado.
#
# Fechar mais o zoom não resolveria: acima de 21 o Google reamostra o mesmo
# tile, e a imagem fica maior sem ficar mais nítida.
ZOOM_SAT = 21
LARG_SAT, ALT_SAT = 720, 720
CHAVE = os.environ.get("MAPS_JS_KEY", "").strip()

ARGS_NAV = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"]

# AS QUATRO VISADAS, e o giro de cada uma a partir do rumo câmera→coordenada.
# A ordem importa: a frente vem primeiro porque é onde o alvo está por
# construção, e as outras três dão a volta no sentido horário.
VISADAS = (
    ("sv_frente", 0),
    ("sv_lado_a", 90),
    ("sv_fundo", 180),
    ("sv_lado_b", 270),
)

# O campo de visão das três visadas de entorno. 100° é o mais aberto que o Maps
# entrega sem distorcer as bordas a ponto de o letreiro deixar de ser legível.
FOV_ENTORNO = 100

# A vista de satélite com o marcador. `mapTypeId:'satellite'` e não 'hybrid':
# rótulo de rua por cima do telhado atrapalha justamente o que se quer ver.
SAT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>*{margin:0;padding:0}html,body,#map{width:%(l)dpx;height:%(a)dpx}</style>
</head><body><div id="map"></div><script>
function initMap(){
  const p = {lat:%(lat)s, lng:%(lng)s};
  const map = new google.maps.Map(document.getElementById('map'), {
    center:p, zoom:%(zoom)d, mapTypeId:'satellite', tilt:0,
    disableDefaultUI:true, clickableIcons:false});
  new google.maps.Marker({position:p, map:map,
    icon:{path:google.maps.SymbolPath.CIRCLE, scale:16,
          fillColor:'#22c55e', fillOpacity:0.18,
          strokeColor:'#22c55e', strokeWeight:4}});
  new google.maps.Marker({position:p, map:map, clickable:false,
    icon:{path:google.maps.SymbolPath.CIRCLE, scale:3.5,
          fillColor:'#052e16', fillOpacity:1,
          strokeColor:'#eaffea', strokeWeight:2}});
  google.maps.event.addListenerOnce(map,'idle',()=>{window.__pronto=true;});
}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=%(chave)s&callback=initMap&loading=async" async defer></script>
</body></html>"""


def _log(m):
    print(m, flush=True)


# QUANTO CORTAR DA BORDA DO PRINT DO STREET VIEW.
#
# O Maps de consumidor desenha por cima do panorama: o cartão do local no canto
# superior esquerdo, os botões de compartilhar e fechar no direito, o minimapa
# no inferior esquerdo, a bússola e o zoom na borda direita. Nada disso é a
# cena, e tudo isso a IA lê como se fosse.
#
# O corte é por PROPORÇÃO e não por seletor de CSS: o Maps troca as classes a
# cada implantação, e um seletor que some faz a interface voltar sem avisar. As
# margens abaixo foram medidas nos prints de 04/09/2026 e cobrem os quatro
# cantos com folga.
#
# O ALVO CONTINUA CENTRADO depois do corte — o `heading` mira nele, e cortar
# margens iguais não move o centro.
# Medido duas vezes: com topo=0.14 sobrava uma tarja preta do cartão do
# Maps no canto superior esquerdo. 0.20 a elimina, e o que se perde é céu.
CORTE = {"esq": 0.15, "dir": 0.12, "topo": 0.20, "baixo": 0.17}


def _cortar_interface(png: bytes) -> bytes:
    """Tira as bordas onde o Maps desenha a própria interface."""
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return png
        h, w = arr.shape[:2]
        x1, x2 = int(w * CORTE["esq"]), int(w * (1 - CORTE["dir"]))
        y1, y2 = int(h * CORTE["topo"]), int(h * (1 - CORTE["baixo"]))
        rec = arr[y1:y2, x1:x2]
        if rec.size == 0:
            return png
        ok, buf = cv2.imencode(".png", rec)
        return buf.tobytes() if ok else png
    except Exception:                                          # noqa: BLE001
        return png


# ── o marcador desenhado no centro ─────────────────────────────────────────
def _marcar_centro(png: bytes, rotulo: str) -> bytes:
    """Desenha a mira no centro da imagem — onde o alvo está por construção.

    A mira é ABERTA (um losango de cantos, não um círculo cheio): marcador
    opaco no centro esconderia justamente a fachada que a IA precisa ler.
    """
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return png
        h, w = arr.shape[:2]
        cx, cy = w // 2, int(h * 0.52)
        r = int(min(w, h) * 0.13)
        for cor, esp in (((0, 0, 0), 7), ((0, 255, 90), 3)):
            for a0, a1 in ((225, 315), (45, 135), (135, 225), (315, 405)):
                cv2.ellipse(arr, (cx, cy), (r, r), 0, a0, a0 + 40, cor, esp)
            cv2.line(arr, (cx, cy - 14), (cx, cy + 14), cor, esp - 1)
            cv2.line(arr, (cx - 14, cy), (cx + 14, cy), cor, esp - 1)
        if rotulo:
            (tw, th), _ = cv2.getTextSize(rotulo, cv2.FONT_HERSHEY_SIMPLEX, .6, 2)
            x, y = cx - tw // 2, cy + r + 30
            cv2.rectangle(arr, (x - 8, y - th - 8), (x + tw + 8, y + 8),
                          (255, 255, 255), -1)
            cv2.rectangle(arr, (x - 8, y - th - 8), (x + tw + 8, y + 8),
                          (20, 20, 20), 2)
            cv2.putText(arr, rotulo, (x, y), cv2.FONT_HERSHEY_SIMPLEX, .6,
                        (20, 20, 20), 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", arr)
        return buf.tobytes() if ok else png
    except Exception:                                          # noqa: BLE001
        return png                 # marcador é enfeite; imagem sem ele serve


# ── quem entra ─────────────────────────────────────────────────────────────
SQL_ALVO = """
    select distinct p.id, st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.nome,''), coalesce(p.fonte,''), coalesce(p.categoria,'')
      from radar_comercial.pois p
      join radar_comercial.ligacao_poi lp on lp.poi_id = p.id
      join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
      join radar_comercial.categoria_catalogo cc
            on cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
     where p.pt_geo is not null
       and cc.avaliar
       and upper(l.categoria) = 'RESIDENCIAL'
       and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
       -- QUEM JA TEM FOTO NAO VOLTA. Quem FALHOU volta, e essa distincao
       -- custou 17 POIs de 143 na primeira corrida do bloco de Canoas.
       --
       -- A condicao era `not exists (... tipo = 'sv_frente')`, sem olhar se
       -- havia byte na linha. So que a captura grava linha TAMBEM quando
       -- falha, para registrar o motivo — entao um tile lento carimbava o POI
       -- como feito, e ele nunca mais era tentado. Falha transitoria virando
       -- permanente, em silencio.
       --
       -- Duas falhas sao DEFINITIVAS e continuam fora: o Google confirmando
       -- que nao ha panorama no ponto, e a chave ausente. Repetir essas duas e
       -- gastar navegador para receber a mesma resposta.
       and not exists (select 1 from radar_comercial.poi_evidencia e
                        where e.poi_id = p.id and e.tipo = 'sv_frente'
                          and (e.dados is not null
                               or e.motivo_falha like 'o Google confirma%'
                               or e.motivo_falha like 'MAPS_JS_KEY%'))
     order by p.id
"""


SQL_POR_ID = """
    select distinct p.id, st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.nome,''), coalesce(p.fonte,''), coalesce(p.categoria,'')
      from radar_comercial.pois p
     where p.pt_geo is not null and p.id = any(%s)
     order by p.id
"""


def alvos(con, poligono, limite, pois=None):
    """A fila. Com `--poi` a lista é EXATAMENTE a pedida, sem filtro nenhum.

    Recapturar uma amostra escolhida a dedo é o caso de todo teste de método:
    passar pela fila normal excluiria justamente quem já tem evidência, que é
    quem se quer refazer.
    """
    cur = con.cursor()
    if pois:
        cur.execute(SQL_POR_ID, (list(pois),))
    else:
        cur.execute(SQL_ALVO)
    fora = 0
    saida = []
    for pid, la, lo, nome, fonte, cat in cur.fetchall():
        if poligono and not area_utils.ponto_no_poligono(la, lo, poligono):
            fora += 1
            continue
        saida.append({"id": pid, "lat": la, "lng": lo, "nome": nome,
                      "fonte": fonte, "categoria": cat})
        if limite and len(saida) >= limite:
            break
    return saida, fora


def gravar(con, poi_id, tipo, dados, **extra):
    campos = ["poi_id", "tipo", "dados", "bytes_tam"]
    vals = [poi_id, tipo, psycopg2_bin(dados), len(dados) if dados else None]
    for k, v in extra.items():
        if v is not None:
            campos.append(k)
            vals.append(v)
    marc = ", ".join(["%s"] * len(campos))
    sets = ", ".join("%s = excluded.%s" % (c, c) for c in campos
                     if c not in ("poi_id", "tipo"))
    with con.cursor() as k:
        k.execute(
            "insert into radar_comercial.poi_evidencia (%s) values (%s) "
            "on conflict (id_empresa, poi_id, tipo) do update set %s, "
            "capturado_em = now()" % (", ".join(campos), marc, sets), vals)
    con.commit()


def psycopg2_bin(dados):
    import psycopg2
    return psycopg2.Binary(dados) if dados else None


# ── a captura ──────────────────────────────────────────────────────────────
async def um_poi(page, con, alvo, placar) -> None:
    """As quatro visadas de UM POI, numa aba que já vem aberta.

    A ABA É DO TRABALHADOR, E NÃO DO POI — e essa troca é a correção de
    04/09/2026. Abrir um contexto novo por POI significava cache vazio: cada
    ponto rebaixava o JavaScript inteiro do Maps antes de mostrar o primeiro
    pixel, e com três trabalhadores disputando a banda isso estourava os 25 s
    que `_abrir` espera pela marca `,3a,` na URL. O sintoma era enganoso — a
    mensagem gravada era "o Maps não entrou em modo panorama", que soa como
    "não existe foto aqui". Existia: os mesmos quatro POIs que falharam abriram
    em 8 s quando rodados sozinhos numa aba reaproveitada.
    """
    lat, lng = alvo["lat"], alvo["lng"]
    try:
        # As quatro visadas saem do MESMO panorama, girando a câmera.
        m = await asyncio.to_thread(sv.metadados_pano, lat, lng)
        if m is False:
            gravar(con, alvo["id"], "sv_frente", None,
                   motivo_falha="o Google confirma que não há panorama aqui")
            placar["sem_pano"] += 1
        elif m is None:
            gravar(con, alvo["id"], "sv_frente", None,
                   motivo_falha="metadados não responderam")
            placar["sem_metadados"] += 1
        else:
            d = sv._dist_m(m["lat"], m["lng"], lat, lng)
            frente = sv._bearing(m["lat"], m["lng"], lat, lng)
            fov = sv._fov_por_distancia(d)
            for tipo, giro in VISADAS:
                heading = (frente + giro) % 360
                # A FRENTE FECHA NO ALVO, AS OUTRAS TRÊS ABREM.
                #
                # `_fov_por_distancia` escolhe o zoom para enquadrar a
                # coordenada — o que serve à frente e atrapalha o resto: nas
                # laterais e no fundo não há alvo para enquadrar, há entorno
                # para varrer. Fechar o campo ali cortaria justamente a esquina
                # onde o comércio de bairro costuma estar, que é o que estas
                # três visadas existem para achar.
                fov_aqui = fov if giro == 0 else FOV_ENTORNO
                try:
                    # DUAS TENTATIVAS, e a segunda não é teimosia: os metadados
                    # JÁ garantiram que o panorama existe e disseram o id dele.
                    # Não abrir é transitório — tile lento, aba disputando banda
                    # com os outros trabalhadores. Medido em 04/09/2026: 3 de 6
                    # POIs falharam na primeira e o panorama existia nos três.
                    ok = await sv._abrir_por_id(page, m["pano_id"], heading, fov_aqui)
                    if not ok:
                        await page.wait_for_timeout(1500)
                        ok = await sv._abrir_por_id(page, m["pano_id"], heading, fov_aqui)
                    if not ok:
                        gravar(con, alvo["id"], tipo, None,
                               motivo_falha="o Maps não entrou em modo panorama "
                                            "em duas tentativas")
                        placar["falha_pano"] += 1
                        continue
                    await page.wait_for_timeout(1200)
                    bruto = await page.screenshot()
                    # SÓ A FRENTE LEVA MIRA. No fundo o alvo está atrás da
                    # câmera — pôr mira ali diria à IA que a fachada é aquela.
                    # CORTA E DEPOIS MARCA. A mira precisa ficar no centro
                    # do que a IA vai ver, e não no centro do que foi jogado
                    # fora com as bordas.
                    limpo = _cortar_interface(bruto)
                    # SÓ A FRENTE LEVA MIRA, e ela vai SEM LEGENDA. A tarja
                    # "FACHADA AVALIADA" tapava justamente a parte de baixo da
                    # fachada — porta, vitrine e medidor —, que é onde está a
                    # prova. A mira aberta já diz onde é o alvo; o texto só
                    # cobria a imagem, e o modelo ainda o lia como letreiro.
                    img = _marcar_centro(limpo, "") if tipo == "sv_frente" else limpo
                    gravar(con, alvo["id"], tipo, img,
                           pano_id=m["pano_id"], cam_lat=m["lat"], cam_lng=m["lng"],
                           heading=heading, pitch=5.0, fov=float(fov_aqui),
                           distancia_m=d, largura_px=LARG, altura_px=ALT)
                    placar[tipo] += 1
                except Exception as e:                         # noqa: BLE001
                    gravar(con, alvo["id"], tipo, None,
                           motivo_falha=type(e).__name__)
                    placar["falha_pano"] += 1

        # O SATÉLITE SAIU DAQUI em 04/09/2026 — ver o cabeçalho. O código do
        # `SAT_HTML` continua no arquivo porque a vista de cima ainda serve à
        # etapa de telhados; o que deixou de existir é a captura por POI.
    finally:
        # A ABA CONTINUA VIVA para o próximo POI. O que precisa voltar ao
        # estado inicial é o TAMANHO DA JANELA: o satélite a encolheu para
        # 720×720, e o Street View do próximo ponto quer os 1280×900.
        try:
            await page.set_viewport_size({"width": LARG, "height": ALT})
        except Exception:                                      # noqa: BLE001
            pass


async def rodar(area, limite, aplicar, trabalhadores, pois=None):
    poligono = None if pois else (area_utils.carregar_area(area) if area else None)
    con = bc.conectar()
    lista, fora = alvos(con, poligono, limite, pois)
    _log("   %d POI(s) na fila da evidência" % len(lista))
    if fora:
        _log("   %d fora do desenho" % fora)
    if not lista:
        _log("   nada a capturar. Marque categorias em 'Categorias para a IA'")
        _log("   e confira que há vínculo com ligação residencial ativa.")
        con.close()
        return {"alvos": 0}
    for a in lista[:5]:
        _log("      %8d %-34s %s" % (a["id"], a["nome"][:34], a["fonte"]))
    if not aplicar:
        _log("   (ensaio: nada capturado. Use --aplicar)")
        con.close()
        return {"alvos": len(lista), "capturados": 0}

    from playwright.async_api import async_playwright
    placar = {k: 0 for k in [v[0] for v in VISADAS]
              + ["sem_pano", "sem_metadados", "falha_pano"]}
    t0 = time.time()
    async with async_playwright() as pw:
        nav = await pw.chromium.launch(headless=False, args=ARGS_NAV)
        try:
            fila = asyncio.Queue()
            for a in lista:
                fila.put_nowait(a)

            async def obreiro(n):
                c = bc.conectar()
                ctx = await nav.new_context(
                    viewport={"width": LARG, "height": ALT})
                page = await ctx.new_page()
                try:
                    while True:
                        try:
                            a = fila.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        try:
                            await um_poi(page, c, a, placar)
                        except Exception as e:                 # noqa: BLE001
                            _log("      %d FALHOU: %s" % (a["id"], str(e)[:70]))
                            # A ABA PODE TER MORRIDO JUNTO. Sem trocá-la, o
                            # trabalhador arrasta o mesmo erro por toda a fila
                            # restante e o placar culpa POIs que estão sãos.
                            if page.is_closed():
                                ctx = await nav.new_context(
                                    viewport={"width": LARG, "height": ALT})
                                page = await ctx.new_page()
                        feitos = placar["sv_frente"] + placar["sem_pano"]
                        if feitos and feitos % 5 == 0:
                            _log("      %d/%d · %.1f s/POI"
                                 % (feitos, len(lista),
                                    (time.time() - t0) / max(feitos, 1)))
                finally:
                    c.close()
                    try:
                        await ctx.close()
                    except Exception:                          # noqa: BLE001
                        pass

            await asyncio.gather(*[obreiro(i) for i in range(trabalhadores)])
        finally:
            await nav.close()

    dt = time.time() - t0
    _log("")
    for k in sorted(placar):
        if placar[k]:
            _log("   %-16s %5d" % (k, placar[k]))
    _log("   %d POI(s) em %.1f min · %.1f s por POI"
         % (len(lista), dt / 60, dt / max(len(lista), 1)))
    con.close()
    return {"alvos": len(lista), **placar, "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=3)
    p.add_argument("--poi", action="append", type=int,
                   help="repetível; recaptura estes POIs, ignorando a fila")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ evidência para a IA — 3 imagens por POI")
    r = asyncio.run(rodar(a.area, a.limite, a.aplicar, a.trabalhadores,
                            a.poi))
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
