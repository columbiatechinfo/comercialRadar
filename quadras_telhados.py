# -*- coding: utf-8 -*-
"""PASSO 7 — telhados da quadra e a ÂNCORA de campo de cada face.

Duas coisas:

1. **Identificar os telhados, rápido.** Sem imagem e sem ML: os polígonos vêm do
   **Overture** (parquet público na S3, consultado direto pelo DuckDB com filtro
   de caixa). Testado em Itambé: 386 telhados na área de uma sessão em 65 s,
   mediana de 110 m². O OSM foi descartado por cobertura — 0 prédios em Itambé e
   92 no centro do Recife, contra 11.605 do Overture só na caixa de Itambé.

2. **Ancorar a numeração no chão.** O Overture não traz endereço nesta versão
   (as colunas são `class`, `names`, `sources`, `facade_material`, `has_parts`),
   então o número vem do Maps: UM telhado por face, lançado no panorama, e o
   título devolve "645 R. do Alecrim". Esse par (número, posição) é o zero da
   régua — sem ele o passo 6 só sabe proporção, não sabe onde a numeração começa.

O telhado NÃO substitui o ponto: ele é referência. O endereço continua sendo o do
CNEFE, com a coordenada original preservada.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from shapely import wkt as _wkt
from shapely.geometry import Point, Polygon

_dist_m = None  # ligado ao QA depois do import

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import base_comum as bc  # noqa: E402
import quadras_analise as QA  # noqa: E402
import quadras_db as QD  # noqa: E402

_dist_m = QA._dist_m

OVERTURE = ("s3://overturemaps-us-west-2/release/{ver}/theme=buildings/"
            "type=building/*")
# a versão é descoberta uma vez e fica em cache — o bucket lista os releases
_VER = None
AREA_MIN_TELHADO_M2 = 12.0     # abaixo disso é anexo, muro fechado, ruído
# distância máxima entre a posição do ponto na testada e a porta de um telhado
# para os dois serem a mesma casa. Uma frente de lote típica tem 8 m; 12 m dá
# folga para um vizinho, mas não deixa casar com a casa do fim da rua.
CASAMENTO_MAX_M = 12.0


def versao_overture() -> str:
    """Release mais recente publicado. Fixar versão no código envelhece sozinho."""
    global _VER
    if _VER:
        return _VER
    import re
    import urllib.request
    try:
        u = ("https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
             "?list-type=2&delimiter=/&prefix=release/")
        x = urllib.request.urlopen(u, timeout=60).read().decode()
        rel = re.findall(r"<Prefix>release/([^<]+)/</Prefix>", x)
        _VER = sorted(rel)[-1] if rel else "2026-07-22.0"
    except Exception:
        _VER = "2026-07-22.0"
    return _VER


def buscar_overture(minx, miny, maxx, maxy) -> list[dict]:
    """Telhados na caixa. Uma consulta só; o filtro por bbox é o que evita
    varrer o mundo."""
    import duckdb
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    c.execute("SET s3_region='us-west-2';")
    url = OVERTURE.format(ver=versao_overture())
    rows = c.execute(f"""
        SELECT id, class, height, num_floors, ST_AsText(geometry) AS wkt
          FROM read_parquet('{url}', hive_partitioning=1)
         WHERE bbox.xmin BETWEEN {minx} AND {maxx}
           AND bbox.ymin BETWEEN {miny} AND {maxy}""").fetchall()
    out = []
    for oid, cls, h, pav, wkt in rows:
        try:
            g = _wkt.loads(wkt)
        except Exception:
            continue
        if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda x: x.area)
        cen = g.centroid
        my, mx = QA._metros(cen.y)
        area = g.area * my * mx
        if area < AREA_MIN_TELHADO_M2:
            continue
        out.append({"overture_id": oid, "classe": cls, "altura": h,
                    "pavimentos": pav, "geom": g, "area_m2": area,
                    "lat": cen.y, "lng": cen.x})
    return out


def passo7_telhados(sid: str, com_maps: bool = True, usar_proxy: bool = True,
                    con=None) -> dict:
    """Telhados por quadra/face + uma âncora de endereço por face."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        QD.garantir_esquema(con)
        _esquema(con)
        s = QD.sessao(sid, con)
        if not s:
            raise ValueError(f"sessão {sid} não existe")
        with con.cursor() as cur:
            cur.execute("DELETE FROM quadra_telhado WHERE sessao_id=%s", (sid,))
        con.commit()

        area = _wkt.loads(s["area_wkt"])
        t0 = time.time()
        tel = buscar_overture(*area.bounds)
        print(f"[7/7] {len(tel)} telhados do Overture na área "
              f"({time.time() - t0:.0f} s, versão {versao_overture()})", flush=True)

        quadras = {q["id"]: _wkt.loads(q["geom_osm"]) for q in QD.quadras(sid, con)}
        faces = QD.faces(sid, con)
        linhas = {}
        for f in faces:
            linhas.setdefault(f["quadra_id"], []).append(
                (f["face_idx"], _wkt.loads(f["anel_real_wkt"] or f["anel_wkt"])))

        n_dentro = 0
        por_face: dict = {}
        with con.cursor() as cur:
            for t in tel:
                p = Point(t["lng"], t["lat"])
                qid = next((k for k, g in quadras.items() if g.contains(p)), None)
                if qid is None:
                    continue                      # telhado de outro quarteirão
                fs = linhas.get(qid) or []
                fi, d = None, 1e18
                for idx, ln in fs:
                    dd = ln.distance(p) * 111320.0
                    if dd < d:
                        fi, d = idx, dd
                cur.execute("""INSERT INTO quadra_telhado
                    (sessao_id, quadra_id, face_idx, overture_id, classe, altura_m,
                     pavimentos, area_m2, lat, lng, dist_face_m, geom_wkt)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (sid, qid, fi, t["overture_id"], t["classe"], t["altura"],
                     t["pavimentos"], round(t["area_m2"], 1), t["lat"], t["lng"],
                     round(d, 1) if d < 1e9 else None, t["geom"].wkt))
                t["id"] = cur.fetchone()[0]
                t["quadra_id"], t["face_idx"], t["dist"] = qid, fi, d
                por_face.setdefault((qid, fi), []).append(t)
                n_dentro += 1
        con.commit()
        print(f"      {n_dentro} dentro das quadras da sessão, "
              f"em {len(por_face)} faces", flush=True)

        ancoras = _ancorar(sid, por_face, com_maps, usar_proxy, con)
        res = {"telhados": n_dentro, "faces_com_telhado": len(por_face), **ancoras}
        QD.marcar_passo(sid, 7, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def _ancorar(sid, por_face, com_maps, usar_proxy, con) -> dict:
    """UM telhado por face vira consulta no Maps: o título devolve o número.

    A consulta NÃO é no centroide do telhado. O centroide fica dentro do
    quarteirão, e o Maps engata no panorama mais próximo — que pode ser de
    qualquer rua ao redor: medido, 4 das 5 primeiras âncoras voltaram com uma via
    diferente da face (a face dizia "Rua Pascoal Carrazzone" e o Maps respondia
    "Av. São Paulo"). O ponto certo é a projeção do telhado no EIXO DA VIA daquela
    face, que é onde o carro do Street View passou.

    O telhado escolhido é o mais próximo do meio da face: no meio a cobertura de
    panorama é melhor e o endereço é menos ambíguo que numa esquina."""
    if not com_maps or not por_face:
        print("      âncora de campo: desligada", flush=True)
        return {"ancoras": 0}
    faces = {(f["quadra_id"], f["face_idx"]): f for f in QD.faces(sid, con)}
    alvos = []
    for k, ts in por_face.items():
        f = faces.get(k)
        if not f:
            continue
        eixo = _wkt.loads(f["anel_wkt"])            # a via, não a testada
        meio = eixo.interpolate(0.5, normalized=True)
        t = min(ts, key=lambda x: (x["lat"] - meio.y) ** 2 + (x["lng"] - meio.x) ** 2)
        # projeta o telhado no eixo: é ali que o panorama existe
        pe = eixo.interpolate(eixo.project(Point(t["lng"], t["lat"])))
        alvos.append({"lat": pe.y, "lng": pe.x, "telhado": t["id"],
                      "quadra_id": k[0], "face_idx": k[1], "numero": None,
                      "esperado": f.get("nome_canonico")})
    import asyncio
    import quadras_canonico as QC
    t0 = time.time()
    n = asyncio.run(QC.enderecos(alvos, usar_proxy=usar_proxy))
    print(f"      âncora de campo: {n}/{len(alvos)} faces com número lido no Maps "
          f"({time.time() - t0:.0f} s)", flush=True)
    # a âncora só vale se a via lida CONFIRMAR a face: número certo na rua errada
    # move a régua inteira para o lugar errado
    bons = 0
    with con.cursor() as cur:
        for a in alvos:
            if a.get("numero") is None and not a.get("via"):
                continue
            bate = (a.get("esperado") and a.get("via")
                    and QA.norm_via(a["via"]) == QA.norm_via(a["esperado"]))
            cur.execute("""UPDATE quadra_telhado SET ancora_numero=%s, ancora_via=%s,
                                  ancora_em=now() WHERE id=%s""",
                        (a.get("numero") if bate else None,
                         a.get("via") or None, a["telhado"]))
            bons += int(bool(bate and a.get("numero")))
    con.commit()
    print(f"      {bons} âncoras CONFIRMAM a via da face (as demais leram outra "
          f"rua e o número foi descartado)", flush=True)
    return {"ancoras": n, "ancoras_validas": bons}


def passo8_casar(sid: str, con=None) -> dict:
    """PASSO 8 — casa ponto com TELHADO e interpola o resto entre eles.

    Até aqui a régua ancorava nas duas PONTAS da face: sabia proporção, não sabia
    onde cada porta fica. O telhado sabe — ele é a construção. Então:

    1. cada telhado da face é projetado na testada: ali está a porta dele;
    2. os pontos, na ordem da numeração, são casados um a um com os telhados na
       ordem em que aparecem na face — casamento guloso pela menor distância,
       um telhado por ponto;
    3. quem casou vai para a porta do seu telhado e vira ÂNCORA;
    4. quem não casou é distribuído ENTRE as âncoras pela mesma regra
       proporcional ao número — o vão de 12→26 continua valendo sete vezes o de
       10→12, só que agora entre duas portas reais em vez de entre as esquinas.

    O teto de 10 m continua valendo contra a coordenada ORIGINAL: casar com um
    telhado a 80 m não é casar, é inventar."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        _esquema(con)
        faces = {(f["quadra_id"], f["face_idx"]): f for f in QD.faces(sid, con)}
        tel_por_face: dict = {}
        for t in telhados(sid, con):
            if t["face_idx"] is not None:
                tel_por_face.setdefault((t["quadra_id"], t["face_idx"]), []).append(t)
        pts_por_face: dict = {}
        for p in QD.pontos(sid, con):
            if p["canonico"] and p["face_idx"] is not None and p["numero"]:
                pts_por_face.setdefault((p["quadra_id"], p["face_idx"]), []).append(p)

        n_casados = n_interp = 0
        with con.cursor() as cur:
            for k, ps in pts_por_face.items():
                f = faces.get(k)
                ts = tel_por_face.get(k) or []
                if not f or not ts or not f.get("anel_real_wkt"):
                    continue
                trilho = _wkt.loads(f["anel_real_wkt"])
                comp = trilho.length or 1
                # a porta de cada telhado: a projeção dele na testada
                # ordena SÓ pela posição: dois telhados podem se projetar no mesmo
                # ponto da testada (prédio dividido em duas feições no Overture), e
                # sem `key` o empate faz o Python comparar os dicts e estourar
                portas = sorted(
                    ((trilho.project(Point(t["lng"], t["lat"])) / comp, t) for t in ts),
                    key=lambda x: x[0])
                ps = sorted(ps, key=lambda p: p["numero"])
                # o sentido da numeração já foi decidido no passo 6; usa a posição
                # alinhada de cada ponto como palpite inicial
                alvo = {p["id"]: (trilho.project(
                    Point(p["lng_alinhado"] or p["lng"], p["lat_alinhado"] or p["lat"]))
                    / comp) for p in ps}
                casado, usada = {}, set()
                pares = sorted(
                    ((abs(alvo[p["id"]] - tp) * comp * 111320.0, p, i, t)
                     for p in ps for i, (tp, t) in enumerate(portas)),
                    key=lambda x: x[0])
                for d, p, i, t in pares:
                    if p["id"] in casado or i in usada or d > CASAMENTO_MAX_M:
                        continue
                    pe = trilho.interpolate(portas[i][0], normalized=True)
                    if _dist_m(p["lat"], p["lng"], pe.y, pe.x) > QA.DESLOC_MAX_M:
                        continue           # o teto vale contra a coordenada crua
                    casado[p["id"]] = (portas[i][0], t)
                    usada.add(i)
                if not casado:
                    continue
                # âncoras = (número, t) dos que casaram; o resto interpola
                anc = sorted((p["numero"], casado[p["id"]][0]) for p in ps
                             if p["id"] in casado)
                for p in ps:
                    if p["id"] in casado:
                        tt, t = casado[p["id"]]
                        modo, por = "telhado", (
                            f"casado com o telhado #{t['id']} ({t['area_m2']:.0f} m²) "
                            f"— posição na porta dele")
                        n_casados += 1
                    else:
                        tt = _entre(p["numero"], anc)
                        if tt is None:
                            continue
                        modo, por = "interpolado", (
                            f"entre as portas vizinhas, proporcional ao número "
                            f"(âncoras: {len(anc)} telhados casados nesta face)")
                        n_interp += 1
                    pe = trilho.interpolate(max(0.0, min(1.0, tt)), normalized=True)
                    d = _dist_m(p["lat"], p["lng"], pe.y, pe.x)
                    if d > QA.DESLOC_MAX_M:
                        continue           # mantém o que o passo 6 já tinha posto
                    cur.execute("""UPDATE quadra_ponto SET lat_alinhado=%s,
                                          lng_alinhado=%s, alinhado_modo=%s,
                                          alinhado_por=%s, desloc_m=%s WHERE id=%s""",
                                (pe.y, pe.x, modo, por, round(d, 1), p["id"]))
        con.commit()
        # por último, e depois de todo mundo ter mexido: quem mora em via que não
        # fecha quadra e tem telhado por perto volta para a coordenada original.
        # Aqui os telhados existem por definição, então é aqui que a regra pega
        # numa corrida normal de 1 a 8.
        # o casamento acima mexe em ponto de rua que não fecha quadra (ele tem
        # face_idx da quadra vizinha): redistribui na própria rua de novo, senão
        # o passo 8 desfaz o que o 6 arrumou
        QA.alinhar_vias_abertas(sid, con)
        pv = QA.preservar_no_lugar(sid, con)
        QA.nao_atravessar_via(sid, con)
        res = {"casados_com_telhado": n_casados, "interpolados": n_interp,
               "preservados": pv.get("preservados", 0)}
        print(f"[8/8] {n_casados} pontos casados com telhado · "
              f"{n_interp} interpolados entre as portas", flush=True)
        QD.marcar_passo(sid, 8, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def _entre(n: int, anc: list) -> float | None:
    """Posição do número `n` entre as âncoras, proporcional ao número.

    Dentro do intervalo interpola; fora dele extrapola pelo passo das âncoras
    extremas — o mesmo espaçamento proporcional do passo 6, mas ancorado em
    portas reais."""
    if not anc:
        return None
    if len(anc) == 1:
        return anc[0][1]
    for i in range(len(anc) - 1):
        (n0, t0), (n1, t1) = anc[i], anc[i + 1]
        if n0 <= n <= n1:
            return t0 if n1 == n0 else t0 + (t1 - t0) * (n - n0) / (n1 - n0)
    (n0, t0), (n1, t1) = anc[0], anc[-1]
    passo = (t1 - t0) / (n1 - n0) if n1 != n0 else 0.0
    return (t0 + (n - n0) * passo) if n < n0 else (t1 + (n - n1) * passo)


def _esquema(con):
    with con.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS quadra_telhado (
            id           bigserial PRIMARY KEY,
            sessao_id    text NOT NULL REFERENCES analise_sessao(id) ON DELETE CASCADE,
            quadra_id    bigint REFERENCES quadra(id) ON DELETE CASCADE,
            face_idx     integer,
            overture_id  text,
            classe       text,
            altura_m     real,
            pavimentos   integer,
            area_m2      real,
            lat          double precision,
            lng          double precision,
            dist_face_m  real,
            geom_wkt     text,
            ancora_numero integer,
            ancora_via    text,
            ancora_em     timestamptz
        );
        CREATE INDEX IF NOT EXISTS ix_qtel_sessao ON quadra_telhado(sessao_id);""")
    con.commit()


def telhados(sid: str, con=None) -> list[dict]:
    fechar = con is None
    con = con or bc.conectar()
    try:
        _esquema(con)
        with con.cursor() as cur:
            cur.execute("""SELECT id, quadra_id, face_idx, overture_id, classe,
                                  altura_m, pavimentos, area_m2, lat, lng,
                                  dist_face_m, geom_wkt, ancora_numero, ancora_via
                             FROM quadra_telhado WHERE sessao_id=%s ORDER BY id""",
                        (sid,))
            rows = cur.fetchall()
        k = ("id", "quadra_id", "face_idx", "overture_id", "classe", "altura_m",
             "pavimentos", "area_m2", "lat", "lng", "dist_face_m", "geom_wkt",
             "ancora_numero", "ancora_via")
        return [dict(zip(k, r)) for r in rows]
    finally:
        if fechar:
            con.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sessao", required=True)
    p.add_argument("--sem-maps", action="store_true")
    p.add_argument("--sem-proxy", action="store_true")
    a = p.parse_args()
    passo7_telhados(a.sessao, com_maps=not a.sem_maps, usar_proxy=not a.sem_proxy)
