# -*- coding: utf-8 -*-
"""
telhados_area.py — Telhados/pegadas de edificação de uma ÁREA (bbox), grátis.

Lê as pegadas de edifício da Overture Maps (conflação de Google Open Buildings +
Microsoft + OSM + Esri) direto do GeoParquet público, filtrando SÓ a sua bbox via
DuckDB — não baixa o planeta. Para cada edifício: área (m², geodésica) e tipo
provável (prédio vs casa térrea, por nº de andares/altura). Grava em `edificacoes`.

Sem QGIS: DuckDB (spatial) faz a leitura e o cálculo de área; nada de GDAL.

USO:
  .venv\\Scripts\\python telhados_area.py --bbox -2.913,-41.777,-2.903,-41.767
      (bbox = minlat,minlng,maxlat,maxlng)  [--rotulo centro_parnaiba] [--release 2026-06-17.0]
"""
import sys
import argparse

import realtime_ingest

RELEASE = "2026-06-17.0"     # release Overture (atualiza ~mensal; passe --release p/ trocar)
S3 = "s3://overturemaps-us-west-2/release/{rel}/theme=buildings/type=building/*"

DDL = """
CREATE TABLE IF NOT EXISTS edificacoes (
  id text, fonte text DEFAULT 'overture', area_ref text,
  area_m2 double precision, num_andares int, altura double precision,
  classe text, subtype text, tipo text, geom_wkt text,
  criado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_edif_ref ON edificacoes (area_ref);
"""


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
               ST_AsText(geometry) AS wkt
        FROM read_parquet('{caminho}', filename=true, hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {minlng} AND {maxlng}
          AND bbox.ymin BETWEEN {minlat} AND {maxlat}
    """
    return con.execute(sql).fetchall()   # (id, height, num_floors, class, subtype, area_m2, wkt)


def run(bbox, rotulo, release):
    minlat, minlng, maxlat, maxlng = bbox
    ref = rotulo or f"{minlat},{minlng},{maxlat},{maxlng}"
    print(f"🏠 Telhados da área [{ref}] | Overture {release}", flush=True)
    print("  consultando GeoParquet público (só a bbox)…", flush=True)
    linhas = puxar_overture(minlat, minlng, maxlat, maxlng, release)
    print(f"  {len(linhas):,} edificações na área".replace(",", "."), flush=True)
    if not linhas:
        print("  (nada encontrado — confira a ordem da bbox: minlat,minlng,maxlat,maxlng)")
        return

    import psycopg2.extras
    conn = realtime_ingest.conectar()
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("DELETE FROM edificacoes WHERE area_ref=%s", (ref,))   # idempotente por área
        registros, resumo = [], {"predio": 0, "casa_terrea": 0, "indeterminado": 0}
        area_total = 0.0
        for (bid, height, nfloors, classe, subtype, area_m2, wkt) in linhas:
            tipo = _classificar(nfloors, height)
            resumo[tipo] += 1
            area_total += area_m2 or 0
            registros.append((bid, ref, area_m2, nfloors, height, classe, subtype, tipo, wkt))
        psycopg2.extras.execute_values(cur, """
            INSERT INTO edificacoes
              (id, area_ref, area_m2, num_andares, altura, classe, subtype, tipo, geom_wkt)
            VALUES %s""", registros, page_size=1000)

    areas = sorted(r[2] for r in registros if r[2])   # r[2] = area_m2
    med = areas[len(areas) // 2] if areas else 0
    print(f"\n  === RESUMO ===")
    print(f"  Total de edificações : {len(linhas):,}".replace(",", "."))
    print(f"    🏢 prédios          : {resumo['predio']:,}".replace(",", "."))
    print(f"    🏠 casas térreas    : {resumo['casa_terrea']:,}".replace(",", "."))
    print(f"    ❓ indeterminado    : {resumo['indeterminado']:,}".replace(",", "."))
    print(f"  Área: total {area_total/10000:,.1f} ha · mediana por edif. {med:,.0f} m²".replace(",", "."))
    print(f"  Gravado em 'edificacoes' (area_ref='{ref}').", flush=True)
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", required=True, help="minlat,minlng,maxlat,maxlng")
    p.add_argument("--rotulo", default="", help="nome da área (chave em edificacoes.area_ref)")
    p.add_argument("--release", default=RELEASE, help="release da Overture")
    a = p.parse_args()
    partes = [float(x) for x in a.bbox.split(",")]
    if len(partes) != 4:
        sys.exit("bbox precisa de 4 números: minlat,minlng,maxlat,maxlng")
    minlat, minlng, maxlat, maxlng = partes
    run((min(minlat, maxlat), min(minlng, maxlng), max(minlat, maxlat), max(minlng, maxlng)),
        a.rotulo, a.release)
