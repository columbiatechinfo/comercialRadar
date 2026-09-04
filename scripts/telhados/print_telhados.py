# -*- coding: utf-8 -*-
"""O print da fase dos telhados: as suspeitas REAIS desenhadas sobre o tile.

Nao e ilustracao — le a `ligacao_poi` onde `origem='telhado'` e desenha o que
esta gravado no banco.

O TEXTO E DESENHADO COM PIL, e nao com o `putText` do OpenCV: as fontes Hershey
do OpenCV nao tem acento, e "Edificio" virava "Edif??cio" na primeira versao —
num print cujo proposito e ser lido por uma pessoa.

Alem do tile inteiro, sai um RECORTE ampliado do aglomerado de suspeitas, que e
onde a coisa acontece e onde os rotulos se atropelavam.
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, "/app")
import base_comum as bc                                        # noqa: E402
import telhados as T                                           # noqa: E402

SAIDA = "/app/saida_telhados"

FONTES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def fonte(tam):
    for f in FONTES:
        if os.path.exists(f):
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default()


def texto(img_pil, xy, txt, tam=15, cor=(20, 20, 20), fundo=(255, 255, 255)):
    d = ImageDraw.Draw(img_pil)
    f = fonte(tam)
    cx = d.textbbox(xy, txt, font=f)
    d.rectangle((cx[0] - 4, cx[1] - 3, cx[2] + 4, cx[3] + 3), fill=fundo,
                outline=(90, 90, 90))
    d.text(xy, txt, font=f, fill=cor)


con = bc.conectar()
cur = con.cursor()
tiles = {t.tile_id: t for t in T.carregar_tiles(con)}

cur.execute("""
    select lp.tile_id, lp.ligacao, lp.poi_id, p.nome, lp.metros,
           lp.telhado_comercial, lp.confianca, lp.suspeita_motivo,
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           st_y(l.geom::geometry), st_x(l.geom::geometry)
      from radar_comercial.ligacao_poi lp
      join radar_comercial.pois p on p.id = lp.poi_id
      join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
     where lp.origem = 'telhado'
     order by lp.tile_id, lp.ligacao, lp.metros
""")
suspeitas = cur.fetchall()
print("suspeitas no banco:", len(suspeitas))

por_tile = {}
for s in suspeitas:
    por_tile.setdefault(s[0], []).append(s)

for tid, itens in sorted(por_tile.items()):
    tile = tiles.get(tid)
    if tile is None:
        continue
    img = cv2.imread(tile.caminho)
    a, b, c, d = tile.caixa
    rot, props = T.segmentar(tile)

    # contorno das construcoes que contam como telhado
    mask = np.isin(rot, [s for s, p in props.items()
                         if s and p["telhado"]]).astype(np.uint8) * 255
    cont, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, cont, -1, (50, 50, 50), 2)

    cur.execute("""
        select st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
               exists(select 1 from radar_comercial.ligacao_poi z
                       where z.poi_id = p.id and z.origem = 'criterio')
          from radar_comercial.pois p
         where p.pt_geo is not null
           and st_y(p.pt_geo::geometry) between %s and %s
           and st_x(p.pt_geo::geometry) between %s and %s
    """, (a, b, c, d))
    for la, lo, tem in cur.fetchall():
        px = tile.pixel(la, lo)
        if px:
            cv2.drawMarker(img, px, (150, 150, 150) if tem else (190, 195, 120),
                           cv2.MARKER_TRIANGLE_UP, 11, 2)

    cur.execute("""
        select st_y(geom::geometry), st_x(geom::geometry)
          from resources_root.cadastro_corsan
         where geom is not null
           and st_y(geom::geometry) between %s and %s
           and st_x(geom::geometry) between %s and %s
    """, (a, b, c, d))
    for la, lo in cur.fetchall():
        px = tile.pixel(la, lo)
        if not px:
            continue
        s, p = T.segmento_de(tile, la, lo)
        sobre = bool(p and p["telhado"])
        cv2.circle(img, px, 9, (255, 220, 0) if sobre else (60, 60, 235), -1)
        cv2.circle(img, px, 9, (20, 20, 20), 2)

    pts = []
    for (_, lig, pid, nome, m, com, conf, mot, pla, plo, lla, llo) in itens:
        pp, pl = tile.pixel(pla, plo), tile.pixel(lla, llo)
        if not pp or not pl:
            continue
        cv2.line(img, pl, pp, (255, 255, 255), 4)
        cv2.line(img, pl, pp, (40, 170, 40), 2)
        cv2.drawMarker(img, pp, (40, 200, 40), cv2.MARKER_TRIANGLE_UP, 22, 4)
        pts.append((pp, pl, nome, m, conf))

    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    # os rotulos SEM SE ATROPELAR: empilhados a partir do ponto mais alto do
    # aglomerado, cada um com uma linha fina ate o seu POI.
    dr = ImageDraw.Draw(pil)
    if pts:
        x0 = max(8, min(p[0][0] for p in pts) - 330)
        y0 = min(p[0][1] for p in pts) - 30
        for i, (pp, pl, nome, m, conf) in enumerate(pts):
            ax, ay = x0, max(8, y0 + 26 * i)
            dr.line((ax + 320, ay + 9, pp[0], pp[1]), fill=(90, 170, 90), width=1)
            texto(pil, (ax, ay), "%-28s %4.0f m   confiança %.1f"
                  % (nome[:28], m, conf), 14)

    leg = [((0, 220, 255), "ligação SOBRE construção"),
           ((235, 60, 60), "ligação fora de construção"),
           ((40, 200, 40), "POI órfão SUSPEITO — a linha vai até a ligação"),
           ((150, 150, 150), "POI que já tem vínculo por critério"),
           ((120, 195, 190), "POI órfão sem telhado em comum")]
    dr.rectangle((10, 10, 600, 44 + 26 * len(leg)), fill=(255, 255, 255),
                 outline=(40, 40, 40), width=2)
    dr.text((22, 20), "ETAPA 10 · suspeita por telhado — %d neste tile" % len(itens),
            font=fonte(19), fill=(20, 20, 20))
    for i, (cor, txt) in enumerate(leg):
        y = 52 + 26 * i
        dr.ellipse((24, y, 38, y + 14), fill=cor, outline=(30, 30, 30))
        dr.text((48, y - 1), txt, font=fonte(14), fill=(30, 30, 30))

    img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    cv2.imwrite(os.path.join(SAIDA, "PRINT_telhados_tile%d.png" % tid), img)

    # o recorte ampliado do aglomerado
    if pts:
        xs = [p[0][0] for p in pts] + [p[1][0] for p in pts]
        ys = [p[0][1] for p in pts] + [p[1][1] for p in pts]
        mx, my = 150, 120
        x1, x2 = max(0, min(xs) - mx), min(img.shape[1], max(xs) + mx)
        y1, y2 = max(0, min(ys) - my), min(img.shape[0], max(ys) + my)
        crop = img[y1:y2, x1:x2]
        if crop.size:
            esc = min(2.2, 1100.0 / max(1, crop.shape[1]))
            crop = cv2.resize(crop, None, fx=esc, fy=esc,
                              interpolation=cv2.INTER_CUBIC)
            cv2.imwrite(os.path.join(SAIDA, "ZOOM_telhados_tile%d.png" % tid), crop)
    print("  tile %d: %d suspeita(s)" % (tid, len(itens)))

con.close()
