# -*- coding: utf-8 -*-
"""
telhados_area.py — Telhados/pegadas de edificação de uma ÁREA (bbox), grátis.

Lê as pegadas de edifício da Overture Maps (conflação de Google Open Buildings +
Microsoft + OSM + Esri) direto do GeoParquet público, filtrando SÓ a sua bbox via
DuckDB — não baixa o planeta. Para cada edifício: área (m², geodésica) e tipo
provável (prédio vs casa térrea, por nº de andares/altura). Grava em `edificacoes`.

Sem QGIS: DuckDB (spatial) faz a leitura e o cálculo de área; nada de GDAL.

USO (use --bbox= com o SINAL DE IGUAL por causa dos números negativos):
  .venv\\Scripts\\python telhados_area.py --bbox=-2.913,-41.777,-2.903,-41.767 --rotulo=centro
      bbox = minlat,minlng,maxlat,maxlng

  # tipo prédio/casa pela IMAGEM AÉREA (qwen2.5vl) — valide numa amostra antes de escalar:
  .venv\\Scripts\\python telhados_area.py --bbox=... --rotulo=centro --visao --amostra=20

Tipo por atributo (altura/andares) é raro no BR → normalmente "indeterminado"; o modo
--visao lê o telhado de satélite e classifica prédio vs casa térrea. Imagem: Esri World
Imagery (grátis, z17 nadir). Requer o servidor Ollama acessível (Tailscale).
"""
import io
import ssl
import math
import json
import base64
import os
import sys
import argparse
import urllib.request

import realtime_ingest

RELEASE = "2026-06-17.0"     # release Overture (atualiza ~mensal; passe --release p/ trocar)
S3 = "s3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*"

# imagem aérea grátis (Esri World Imagery, tiles XYZ) — z17 é o máximo com cobertura no BR
ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://100.115.117.49:11434").rstrip("/") + "/api/generate"
MODELO_VISAO = "qwen2.5vl:7b"
_SSL = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"

PROMPT_AEREO = """Imagem de satélite (vista DE CIMA) de uma quadra urbana. Olhe a edificação \
no CENTRO da imagem. Classifique se é um PRÉDIO (edifício de apartamentos, vários andares — \
telhado grande, muitas vezes cinza/laje, com sombra longa projetada) ou uma CASA TÉRREA \
(1 pavimento — telhado pequeno de cerâmica, sombra curta). Se for claramente comércio/galpão \
térreo grande, use "casa_terrea" (é térreo). Responda SOMENTE JSON: \
{"tipo": "predio" | "casa_terrea" | "incerto", "andares_estimados": 1, "justificativa": "1 frase"}"""

DDL = """
CREATE TABLE IF NOT EXISTS edificacoes (
  id text, fonte text DEFAULT 'overture', area_ref text,
  area_m2 double precision, num_andares int, altura double precision,
  classe text, subtype text, tipo text, tipo_visao text,
  centro_lat double precision, centro_lng double precision, geom_wkt text,
  criado_em timestamptz DEFAULT now()
);
ALTER TABLE edificacoes ADD COLUMN IF NOT EXISTS tipo_visao text;
ALTER TABLE edificacoes ADD COLUMN IF NOT EXISTS centro_lat double precision;
ALTER TABLE edificacoes ADD COLUMN IF NOT EXISTS centro_lng double precision;
CREATE INDEX IF NOT EXISTS ix_edif_ref ON edificacoes (area_ref);
"""


def _num(lat, lng, z):
    n = 2 ** z
    return ((lng + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def _tile(z, x, y):
    from PIL import Image
    req = urllib.request.Request(ESRI.format(z=z, x=x, y=y), headers={"User-Agent": UA})
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=60, context=_SSL).read())).convert("RGB")


def _crop_aereo(lat, lng, z=17) -> bytes:
    """Mosaico 3x3 de tiles Esri centrado na edificação → JPEG (bytes)."""
    fx, fy = _num(lat, lng, z)
    cx, cy = int(fx), int(fy)
    from PIL import Image
    mos = Image.new("RGB", (768, 768))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            mos.paste(_tile(z, cx + dx, cy + dy), ((dx + 1) * 256, (dy + 1) * 256))
    px = int((fx - cx) * 256) + 256
    py = int((fy - cy) * 256) + 256
    crop = mos.crop((px - 170, py - 170, px + 170, py + 170))
    b = io.BytesIO(); crop.save(b, "JPEG", quality=85)
    return b.getvalue()


def _tipo_por_visao(lat, lng) -> str:
    """Baixa o crop aéreo e pergunta ao qwen2.5vl: prédio ou casa térrea?"""
    try:
        b64 = base64.b64encode(_crop_aereo(lat, lng)).decode()
        payload = json.dumps({"model": MODELO_VISAO, "prompt": PROMPT_AEREO, "images": [b64],
                              "stream": False, "format": "json",
                              "options": {"num_ctx": 4096, "temperature": 0, "num_predict": 120}}).encode()
        req = urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
        return (json.loads(resp.get("response", "{}")).get("tipo") or "incerto").strip()
    except Exception:
        return "erro"


def _classificar(num_floors, height):
    """prédio (>=2 andares ou >=6 m), casa térrea (1 andar / <6 m) ou indeterminado."""
    if num_floors is not None:
        return "predio" if num_floors >= 2 else "casa_terrea"
    if height is not None:
        return "predio" if height >= 6 else "casa_terrea"
    return "indeterminado"


def puxar_overture(minlat, minlng, maxlat, maxlng, release):
    try:
        import duckdb
    except ImportError:
        sys.exit("Falta o DuckDB. Rode:  .venv\\Scripts\\python -m pip install duckdb")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    caminho = S3.format(rel=release)
    sql = f"""
        SELECT id, height, num_floors, class, subtype,
               ST_Area_Spheroid(geometry) AS area_m2,
               ST_Y(ST_Centroid(geometry)) AS clat,
               ST_X(ST_Centroid(geometry)) AS clng,
               ST_AsText(geometry) AS wkt
        FROM read_parquet('{caminho}', filename=true, hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {minlng} AND {maxlng}
          AND bbox.ymin BETWEEN {minlat} AND {maxlat}
    """
    return con.execute(sql).fetchall()   # (id,height,num_floors,class,subtype,area_m2,clat,clng,wkt)


def run(bbox, rotulo, release, visao=False, amostra=0):
    from collections import Counter
    minlat, minlng, maxlat, maxlng = bbox
    ref = rotulo or f"{minlat},{minlng},{maxlat},{maxlng}"
    print(f"🏠 Telhados da área [{ref}] | Overture {release}", flush=True)
    print("  consultando GeoParquet público (só a bbox)…", flush=True)
    linhas = puxar_overture(minlat, minlng, maxlat, maxlng, release)
    print(f"  {len(linhas):,} edificações na área".replace(",", "."), flush=True)
    if not linhas:
        print("  (nada encontrado — confira a ordem da bbox: minlat,minlng,maxlat,maxlng)")
        return

    # 👁️ modo visão: classifica prédio/casa pela IMAGEM AÉREA (o tipo por atributo é raro no BR)
    tipos_visao = {}
    if visao:
        alvos = linhas[:amostra] if amostra else linhas
        print(f"  👁️ visão aérea (qwen2.5vl) em {len(alvos)} edificações…", flush=True)
        for i, (bid, _h, _nf, _cl, _st, _a, clat, clng, _wkt) in enumerate(alvos, 1):
            tipos_visao[bid] = _tipo_por_visao(clat, clng)
            if i % 25 == 0:
                print(f"    {i}/{len(alvos)}", flush=True)

    import psycopg2.extras
    conn = realtime_ingest.conectar()
    registros, resumo, area_total = [], Counter(), 0.0
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("DELETE FROM edificacoes WHERE area_ref=%s", (ref,))   # idempotente por área
        for (bid, height, nfloors, classe, subtype, area_m2, clat, clng, wkt) in linhas:
            tipo_attr = _classificar(nfloors, height)
            tv = tipos_visao.get(bid)
            resumo[tv or tipo_attr] += 1
            area_total += area_m2 or 0
            registros.append((bid, ref, area_m2, nfloors, height, classe, subtype,
                              tipo_attr, tv, clat, clng, wkt))
        psycopg2.extras.execute_values(cur, """
            INSERT INTO edificacoes
              (id, area_ref, area_m2, num_andares, altura, classe, subtype,
               tipo, tipo_visao, centro_lat, centro_lng, geom_wkt)
            VALUES %s""", registros, page_size=1000)

    areas = sorted(r[2] for r in registros if r[2])   # r[2] = area_m2
    med = areas[len(areas) // 2] if areas else 0
    fonte_tipo = "visão aérea" if visao else "atributo (altura/andares)"
    print(f"\n  === RESUMO === (tipo por {fonte_tipo})")
    print(f"  Total de edificações : {len(linhas):,}".replace(",", "."))
    for k in ("predio", "casa_terrea", "misto", "incerto", "indeterminado", "erro"):
        if resumo.get(k):
            print(f"    {k:14}: {resumo[k]:,}".replace(",", "."))
    print(f"  Área: total {area_total/10000:,.1f} ha · mediana por edif. {med:,.0f} m²".replace(",", "."))
    print(f"  Gravado em 'edificacoes' (area_ref='{ref}').", flush=True)
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", required=True, help="minlat,minlng,maxlat,maxlng")
    p.add_argument("--rotulo", default="", help="nome da área (chave em edificacoes.area_ref)")
    p.add_argument("--release", default=RELEASE, help="release da Overture")
    p.add_argument("--visao", action="store_true", help="classifica prédio/casa pela imagem aérea (qwen2.5vl)")
    p.add_argument("--amostra", type=int, default=0, help="limita a visão às N primeiras (validar antes de escalar)")
    a = p.parse_args()
    partes = [float(x) for x in a.bbox.split(",")]
    if len(partes) != 4:
        sys.exit("bbox precisa de 4 números: minlat,minlng,maxlat,maxlng")
    minlat, minlng, maxlat, maxlng = partes
    run((min(minlat, maxlat), min(minlng, maxlng), max(minlat, maxlat), max(minlng, maxlng)),
        a.rotulo, a.release, a.visao, a.amostra)
