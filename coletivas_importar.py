"""
coletivas_importar.py — leva o RCC para o banco e o casa com os POIs.

O RCC sai no grão da UNIDADE: 183.733 linhas para Canoas, uma por apartamento,
casa ou loja. Para cruzar com POI e com a leitura de fachada o grão útil é o da
COLETIVA — o prédio, não o apartamento. Este módulo agrega e casa.

POR QUE ISSO IMPORTA: a skill de fachada é explícita — uma fonte isolada nunca
vira `achado_convergente`. Hoje a leitura tem imagem + cadastro do cliente, que
são duas. O CNEFE é a **terceira**, e de origem completamente independente: um
recenseador do IBGE anotou aquela porta em 2022, sem saber da existência do
cliente nem da nossa foto.

Onde as três concordam que há mais unidades do que economias cobradas, a
oportunidade deixa de ser suspeita e passa a ser achado com procedência — que é
o que se defende numa mesa de revisão tarifária.

USO:
  .venv\\Scripts\\python coletivas_importar.py --rcc coletivas/rcc/RCC_4304606_CANOAS.csv
  .venv\\Scripts\\python coletivas_importar.py --casar --cidade Canoas
"""

import argparse
import csv
import io
import re
import unicodedata
from pathlib import Path

import config          # noqa: F401
import base_comum as bc

csv.field_size_limit(10_000_000)

# Distância máxima entre o POI e a coletiva para serem o mesmo imóvel. O CNEFE
# tem acurácia declarada de 5 m no nível 1; 35 m dá folga para o POI do Maps,
# que aponta a porta comercial, e ainda não alcança o vizinho de esquina.
CASAMENTO_MAX_M = 35.0


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", s.upper())).strip()


def esquema(con):
    with con.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cnefe_coletiva (
              id              serial PRIMARY KEY,
              coletiva_id     text NOT NULL,
              cod_municipio   text NOT NULL,
              logradouro      text,
              numero          integer,
              localidade      text,
              forma           text,
              uso             text,
              veredito        text,
              qtd_observada   integer,     -- unidades que o recenseador viu
              qtd_inferida    integer,     -- lacunas que o pipeline deduziu
              qtd_blocos      integer,
              unidades        integer,     -- linhas do RCC nesta coletiva
              economias_cnefe integer,     -- soma de UND_ECONOMIAS
              com_atividade   integer,
              atividades      text,        -- nomes vistos pelo recenseador
              lat             double precision,
              lng             double precision,
              acuracia_m      real,
              recomendacao    text,
              poi_id          integer REFERENCES pois(id) ON DELETE SET NULL,
              poi_dist_m      real,
              importado_em    timestamp DEFAULT now())""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_col_id
                         ON cnefe_coletiva (cod_municipio, coletiva_id)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS ix_col_poi
                         ON cnefe_coletiva (poi_id)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS ix_col_geo
                         ON cnefe_coletiva (lat, lng)""")
    con.commit()


def _int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def importar(caminho: Path, con) -> dict:
    """Agrega o RCC por COLETIVA_ID e grava.

    Só entram as coletivas de verdade: `COL_FORMA = INDEFINIDA` é endereço
    isolado ou agrupamento sem forma reconhecida, e carregar 86 mil deles
    encheria a tabela de linhas que nunca virão a ser evidência de nada."""
    grupos = {}
    f = io.open(caminho, encoding="utf-8-sig", newline="")
    for r in csv.DictReader(f, delimiter=";"):
        cid = (r.get("COLETIVA_ID") or "").strip()
        forma = (r.get("COL_FORMA") or "").strip()
        if not cid or forma in ("", "INDEFINIDA"):
            continue
        g = grupos.get(cid)
        if g is None:
            g = grupos[cid] = {
                "coletiva_id": cid,
                "cod_municipio": (r.get("END_MUNICIPIO_COD")
                                  or caminho.stem.split("_")[1]),
                "logradouro": (r.get("END_LOGRADOURO") or "").strip(),
                "numero": _int(r.get("END_NUMERO")),
                "localidade": (r.get("END_LOCALIDADE") or "").strip(),
                "forma": forma, "uso": (r.get("COL_USO") or "").strip(),
                "veredito": (r.get("COL_VEREDITO") or "").strip(),
                "qtd_observada": _int(r.get("COL_QTD_OBSERVADA")) or 0,
                "qtd_inferida": _int(r.get("COL_QTD_INFERIDA")) or 0,
                "qtd_blocos": _int(r.get("COL_QTD_BLOCOS")) or 0,
                "unidades": 0, "economias_cnefe": 0, "com_atividade": 0,
                "atividades": [], "lat": None, "lng": None,
                "acuracia_m": _float(r.get("GEO_ACURACIA_M")),
                "recomendacao": (r.get("ACT_RECOMENDACAO") or "").strip(),
            }
        g["unidades"] += 1
        g["economias_cnefe"] += _int(r.get("UND_ECONOMIAS")) or 0
        if (r.get("ATV_PRESENTE") or "").strip().upper() == "SIM":
            g["com_atividade"] += 1
            nome = (r.get("ATV_NOME") or "").strip()
            if nome and nome not in g["atividades"] and len(g["atividades"]) < 8:
                g["atividades"].append(nome)
        if g["lat"] is None:
            la, lo = _float(r.get("GEO_LAT")), _float(r.get("GEO_LON"))
            if la is not None and lo is not None:
                g["lat"], g["lng"] = la, lo

    cod = next(iter(grupos.values()))["cod_municipio"] if grupos else None
    from psycopg2.extras import execute_values
    with con.cursor() as cur:
        if cod:
            cur.execute("DELETE FROM cnefe_coletiva WHERE cod_municipio = %s", (cod,))
        execute_values(cur, """
            INSERT INTO cnefe_coletiva (coletiva_id, cod_municipio, logradouro,
              numero, localidade, forma, uso, veredito, qtd_observada,
              qtd_inferida, qtd_blocos, unidades, economias_cnefe, com_atividade,
              atividades, lat, lng, acuracia_m, recomendacao)
            VALUES %s""",
            [(g["coletiva_id"], g["cod_municipio"], g["logradouro"], g["numero"],
              g["localidade"], g["forma"], g["uso"], g["veredito"],
              g["qtd_observada"], g["qtd_inferida"], g["qtd_blocos"],
              g["unidades"], g["economias_cnefe"], g["com_atividade"],
              " · ".join(g["atividades"]) or None, g["lat"], g["lng"],
              g["acuracia_m"], g["recomendacao"]) for g in grupos.values()],
            page_size=1000)
    con.commit()
    return {"coletivas": len(grupos), "municipio": cod}


def casar_com_pois(cidade: str, con) -> dict:
    """Liga cada coletiva ao POI mais próximo dentro do raio.

    Por COORDENADA, não por texto de endereço: o CNEFE escreve o logradouro do
    jeito do recenseador e o Maps do jeito do Google, e casar string aqui
    repetiria o trabalho que a régua de numeração já mostrou ser traiçoeiro. A
    coordenada do CNEFE vem com acurácia declarada — é o que a torna confiável."""
    with con.cursor() as cur:
        cur.execute("""
            WITH c AS (
              SELECT id, lat, lng FROM cnefe_coletiva
               WHERE lat IS NOT NULL
                 AND cod_municipio IN (
                   SELECT DISTINCT cod_municipio FROM cnefe_coletiva)),
            p AS (
              SELECT id, COALESCE(maps_lat, lat_origem) la,
                     COALESCE(maps_lng, lng_origem) lo
                FROM pois
               WHERE cidade ILIKE %s AND COALESCE(maps_lat, lat_origem) IS NOT NULL),
            m AS (
              SELECT c.id AS cid, p.id AS pid,
                     111320 * sqrt(power(c.lat - p.la, 2)
                       + power((c.lng - p.lo) * cos(radians(c.lat)), 2)) AS d,
                     row_number() OVER (PARTITION BY c.id ORDER BY
                       power(c.lat - p.la, 2) + power((c.lng - p.lo) * cos(radians(c.lat)), 2))
                       AS r
                FROM c JOIN p
                  ON p.la BETWEEN c.lat - 0.0004 AND c.lat + 0.0004
                 AND p.lo BETWEEN c.lng - 0.0004 AND c.lng + 0.0004)
            UPDATE cnefe_coletiva x SET poi_id = m.pid, poi_dist_m = m.d
              FROM m WHERE m.r = 1 AND m.d <= %s AND x.id = m.cid""",
            (cidade, CASAMENTO_MAX_M))
        n = cur.rowcount
    con.commit()
    with con.cursor() as cur:
        cur.execute("SELECT count(*) FROM cnefe_coletiva WHERE poi_id IS NOT NULL")
        total = cur.fetchone()[0]
    return {"casadas_agora": n, "com_poi": total}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rcc", help="CSV do RCC a importar")
    p.add_argument("--casar", action="store_true")
    p.add_argument("--cidade", default="Canoas")
    a = p.parse_args()

    con = bc.conectar()
    try:
        esquema(con)
        if a.rcc:
            r = importar(Path(a.rcc), con)
            print(f"✅ {r['coletivas']:,} coletivas importadas "
                  f"(município {r['municipio']})".replace(",", "."))
        if a.casar or a.rcc:
            r = casar_com_pois(a.cidade, con)
            print(f"🔗 {r['casadas_agora']:,} casadas com POI agora · "
                  f"{r['com_poi']:,} com POI no total".replace(",", "."))
    finally:
        con.close()


if __name__ == "__main__":
    main()
