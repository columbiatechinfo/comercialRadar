# -*- coding: utf-8 -*-
"""Tabela de quadras do Brasil, montada pela topologia das vias do OSM.

A quadra é a face fechada pelo cruzamento das vias. Não vem de polígono pronto —
o IBGE não publica malha de quadras (conferido no acervo em 2026-07-22), e o
Overture traz edificação, não quarteirão. Aqui ela nasce da rede viária e já
carrega o que interessa depois: as vias que a delimitam, quantos cruzamentos tem
na borda, e se o traçado é retangular.

Esse último campo é o que dita o trabalho seguinte: quadra retangular sai pronta
das vias e só precisa dos telhados dentro; quadra torta exige refino pela imagem
de satélite, que é caro. Em Itambé-PE, 28% das 609 faces saem retangulares.

O fluxo é SOB DEMANDA, não em lote: quando chega um município, olha se já está na
base; se está, devolve; se não, monta só ele. Nada de varrer o Brasil antes — o
Brasil inteiro levaria dias e não é preciso. Cada município entra quando é pedido
e fica em cache para sempre. É `garantir_municipio(cod)`.

Como um município é montado (só na primeira vez que é pedido):
    1. baixa o extrato .osm.pbf da REGIÃO no Geofabrik (cache; ~450 MB, uma vez);
    2. materializa as vias da região numa tabela DuckDB (uma vez; ~1 min);
    3. recorta as vias do município pela caixa da malha municipal do IBGE (0,1 s);
    4. une as linhas — o que noda a rede em todo cruzamento — e polygoniza;
    5. classifica cada face (retangular?, vias, cruzamentos) e grava.
Depois da região preparada, cada município novo custa ~3 s.

uso:
    quadras_br.py mun 2607653                  garante um município (código IBGE)
    quadras_br.py uf PE                        lote de uma UF (opcional)
    quadras_br.py regioes                      lista as regiões e tamanhos
env:
    DB=caminho do banco (padrão dados/quadras_br.duckdb)
    AREA_MIN=250   AREA_MAX=250000   metros quadrados por face
"""
import sys, os, json, math, time, urllib.request, hashlib
from pathlib import Path
from collections import Counter

import numpy as np
import duckdb
from shapely import wkb as _wkb, wkt as _wkt
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, polygonize
from pyproj import Transformer

RAIZ = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("CACHE_PBF", RAIZ / "dados" / "pbf"))
DB = Path(os.environ.get("DB", RAIZ / "dados" / "quadras_br.duckdb"))
AREA_MIN = float(os.environ.get("AREA_MIN", 250))
AREA_MAX = float(os.environ.get("AREA_MAX", 250_000))

GEOFABRIK = "https://download.geofabrik.de/south-america/brazil/{regiao}-latest.osm.pbf"
REGIOES = {                      # tamanho aproximado do extrato, para dar noção
    "norte":        ("AC AM AP PA RO RR TO", 150),
    "nordeste":     ("AL BA CE MA PB PE PI RN SE", 415),
    "centro-oeste": ("DF GO MS MT", 191),
    "sudeste":      ("ES MG RJ SP", 812),
    "sul":          ("PR RS SC", 400),
}
UF_REGIAO = {uf: r for r, (ufs, _) in REGIOES.items() for uf in ufs.split()}

# Vias que fecham quarteirão. Ficam de fora trilha, calçada, ciclovia e escada:
# elas atravessam a quadra sem serem rua e a partiriam ao meio.
VIAS_OK = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
           "residential", "living_street", "service", "road", "track",
           "motorway_link", "trunk_link", "primary_link", "secondary_link",
           "tertiary_link")


# ── infraestrutura ────────────────────────────────────────────────────────────
def con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect(str(DB))
    c.execute("INSTALL spatial; LOAD spatial;")
    c.execute("""
        CREATE TABLE IF NOT EXISTS quadras (
            id            BIGINT PRIMARY KEY,
            cod_municipio VARCHAR NOT NULL,
            uf            VARCHAR NOT NULL,
            municipio     VARCHAR,
            geom_wkt      VARCHAR NOT NULL,   -- EPSG:4326
            area_m2       DOUBLE  NOT NULL,
            perimetro_m   DOUBLE,
            n_vertices    INTEGER,
            n_vias        INTEGER,
            n_cruzamentos INTEGER,
            retangular    BOOLEAN NOT NULL,
            preenchimento DOUBLE,             -- área / retângulo mínimo
            cantos_90     INTEGER,
            vias          VARCHAR,            -- JSON [{nome,tipo,m}]
            lat_centro    DOUBLE,
            lng_centro    DOUBLE,
            fonte         VARCHAR DEFAULT 'osm',
            criado_em     TIMESTAMP DEFAULT current_timestamp
        )""")
    for ix, col in (("ix_quadras_mun", "cod_municipio"), ("ix_quadras_uf", "uf"),
                    ("ix_quadras_ret", "retangular"), ("ix_quadras_area", "area_m2")):
        c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON quadras({col})")
    # caixa envolvente indexada: filtro espacial barato sem depender de RTree
    for ix, col in (("ix_quadras_lat", "lat_centro"), ("ix_quadras_lng", "lng_centro")):
        c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON quadras({col})")
    c.execute("""CREATE TABLE IF NOT EXISTS municipios_feitos (
                    cod_municipio VARCHAR PRIMARY KEY, uf VARCHAR, municipio VARCHAR,
                    n_quadras INTEGER, n_retangulares INTEGER,
                    segundos DOUBLE, feito_em TIMESTAMP DEFAULT current_timestamp)""")
    return c


def baixar_pbf(regiao):
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{regiao}-latest.osm.pbf"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"  pbf em cache: {dest.name} ({dest.stat().st_size/1e6:.0f} MB)", flush=True)
        return dest
    url = GEOFABRIK.format(regiao=regiao)
    print(f"  baixando {url} ...", flush=True)
    tmp = dest.with_suffix(".parcial")
    t0 = time.time(); ult = [0]
    def prog(blocos, tam, total):
        b = blocos * tam
        if total > 0 and b - ult[0] > 30_000_000:
            ult[0] = b
            print(f"    {b/1e6:6.0f} / {total/1e6:.0f} MB  ({time.time()-t0:.0f}s)", flush=True)
    urllib.request.urlretrieve(url, tmp, reporthook=prog)
    tmp.rename(dest)
    print(f"  ok: {dest.stat().st_size/1e6:.0f} MB em {time.time()-t0:.0f}s", flush=True)
    return dest


def _json_ibge(url, timeout=300):
    """A API do IBGE devolve gzip mesmo quando não se pede — decomprime na mão."""
    req = urllib.request.Request(url, headers={"Accept-Encoding": "identity",
                                               "User-Agent": "comercialRadar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if b[:2] == b"\x1f\x8b":
        import gzip; b = gzip.decompress(b)
    return b


def malha_municipios(uf):
    """Polígonos municipais do IBGE, direto da API de malhas."""
    ch = CACHE / f"malha_{uf}.json"
    if not ch.exists() or ch.stat().st_size < 1000:
        CACHE.mkdir(parents=True, exist_ok=True)
        ch.write_bytes(_json_ibge(
            f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
            f"?formato=application/vnd.geo+json&intrarregiao=municipio"))
    d = json.loads(ch.read_bytes())
    from shapely.geometry import shape
    out = {}
    for f in d["features"]:
        cod = str(f["properties"].get("codarea"))
        out[cod] = shape(f["geometry"])
    return out


def nomes_municipios(uf):
    ch = CACHE / f"nomes_{uf}.json"
    if not ch.exists() or ch.stat().st_size < 100:
        CACHE.mkdir(parents=True, exist_ok=True)
        ch.write_bytes(_json_ibge(
            f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios",
            timeout=120))
    return {str(m["id"]): m["nome"] for m in json.loads(ch.read_bytes())}


# ── geometria ─────────────────────────────────────────────────────────────────
def retangular(pol, tol_ang=12.0, tol_lado=0.12):
    """Compara a face com o menor retângulo que a envolve e conta cantos de 90°."""
    mrr = pol.minimum_rotated_rectangle
    preench = pol.area / mrr.area if mrr.area else 0.0
    cs = list(pol.exterior.coords)[:-1]
    n = len(cs); rectos = 0
    for i in range(n):
        a = np.array(cs[i - 1]); b = np.array(cs[i]); c = np.array(cs[(i + 1) % n])
        u, v = a - b, c - b
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu < 1e-9 or nv < 1e-9: continue
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(u, v) / (nu * nv))))))
        if abs(ang - 90) <= tol_ang: rectos += 1
    return (preench >= 1 - tol_lado and rectos >= 4), preench, rectos


def tabela_vias(pbf):
    return "vias_" + Path(pbf).stem.replace("-latest.osm", "").replace("-", "_")


def preparar_regiao(c, pbf):
    """Extrai as vias do PBF UMA vez e guarda com a caixa envolvente pré-calculada.

    É o passo que torna o Brasil viável: sem ele, cada município reabre o extrato
    inteiro pelo GDAL. O bbox vai em colunas simples porque o filtro por caixa
    resolve 99% do recorte e não exige índice espacial."""
    t = tabela_vias(pbf)
    ok = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?",
                   [t]).fetchone()[0]
    if ok:
        n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        if n > 0:
            print(f"  vias em cache: {t} ({n:,} linhas)", flush=True); return t
        c.execute(f"DROP TABLE {t}")
    print(f"  extraindo vias de {Path(pbf).name} (uma vez só)...", flush=True)
    t0 = time.time()
    tipos = ", ".join(f"'{x}'" for x in VIAS_OK)
    c.execute(f"""
        CREATE TABLE {t} AS
        SELECT ST_AsWKB(geom) AS g, name, highway,
               ST_XMin(geom) AS xmin, ST_XMax(geom) AS xmax,
               ST_YMin(geom) AS ymin, ST_YMax(geom) AS ymax
        FROM st_read('{str(pbf).replace(chr(92), '/')}', layer='lines',
                     open_options=['INTERLEAVED_READING=YES'])
        WHERE highway IN ({tipos}) AND geom IS NOT NULL
    """)
    for col in ("xmin", "xmax", "ymin", "ymax"):
        c.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_{col} ON {t}({col})")
    n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"  {n:,} vias em {time.time()-t0:.0f}s -> {t}", flush=True)
    return t


def _prox_id(c):
    r = c.execute("SELECT coalesce(max(id), 0) FROM quadras").fetchone()[0]
    return int(r) + 1


def processar_municipio(c, pbf, cod, poli_mun, uf, nome):
    ja = c.execute("SELECT 1 FROM municipios_feitos WHERE cod_municipio = ?", [cod]).fetchone()
    if ja: return 0, 0, True
    t0 = time.time()
    minlng, minlat, maxlng, maxlat = poli_mun.bounds
    tipos = ", ".join(f"'{t}'" for t in VIAS_OK)
    # Lê da tabela já materializada da região. Ler o PBF por município custava
    # 98 s cada — a varredura dos 436 MB acontecia inteira toda vez, e o Brasil
    # levaria 6 dias. Materializando a região uma vez, cai para segundos.
    q = f"""
        SELECT g, name, highway FROM {tabela_vias(pbf)}
        WHERE highway IN ({tipos})
          AND xmin <= {maxlng} AND xmax >= {minlng}
          AND ymin <= {maxlat} AND ymax >= {minlat}
    """
    linhas_ll, meta = [], []
    for g, nm, hw in c.execute(q).fetchall():
        try: ls = _wkb.loads(bytes(g))
        except Exception: continue
        if ls.is_empty or ls.geom_type != "LineString" or len(ls.coords) < 2: continue
        linhas_ll.append(ls); meta.append((nm or "(sem nome)", hw))
    if len(linhas_ll) < 4:
        c.execute("INSERT INTO municipios_feitos VALUES (?,?,?,?,?,?,current_timestamp)",
                  [cod, uf, nome, 0, 0, time.time() - t0])
        return 0, 0, False
    lat0 = (minlat + maxlat) / 2; lng0 = (minlng + maxlng) / 2
    crs = (f"+proj=tmerc +lat_0={lat0} +lon_0={lng0} +k=1 +x_0=0 +y_0=0 "
           f"+datum=WGS84 +units=m")
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    linhas = [LineString([fwd.transform(x, y) for x, y in l.coords]) for l in linhas_ll]
    rede = unary_union(linhas)                       # noda em todo cruzamento
    faces = [p for p in polygonize(rede) if AREA_MIN < p.area < AREA_MAX]
    if not faces:
        c.execute("INSERT INTO municipios_feitos VALUES (?,?,?,?,?,?,current_timestamp)",
                  [cod, uf, nome, 0, 0, time.time() - t0])
        return 0, 0, False
    seg = list(rede.geoms) if hasattr(rede, "geoms") else [rede]
    cnt = Counter()
    for s in seg:
        for p in (s.coords[0], s.coords[-1]): cnt[(round(p[0], 1), round(p[1], 1))] += 1
    cruz = [Point(k) for k, v in cnt.items() if v >= 3]
    mun_m = Polygon([fwd.transform(x, y) for x, y in poli_mun.exterior.coords]) \
        if poli_mun.geom_type == "Polygon" else None
    # STRtree: sem ele, achar as vias que tocam cada face testava toda linha contra
    # toda face — 24x mais lento no perfil, e era 95% do tempo do município.
    from shapely.strtree import STRtree
    tree_v = STRtree(linhas)
    tree_c = STRtree(cruz) if cruz else None
    pid = _prox_id(c); linhas_out = []; n_ret = 0
    for face in faces:
        rp = face.representative_point()
        if mun_m is not None and not mun_m.contains(rp): continue
        ret, pre, rec = retangular(face)
        b = face.exterior.buffer(1.5); vias_d = {}
        for j in tree_v.query(b):
            it = linhas[j].intersection(b)
            comp = getattr(it, "length", 0.0)
            if comp < 8: continue
            nm, hw = meta[j]; k = (nm, hw); vias_d[k] = vias_d.get(k, 0.0) + comp
        vias = [{"nome": n, "tipo": t, "m": round(m)}
                for (n, t), m in sorted(vias_d.items(), key=lambda x: -x[1])]
        ncr = 0
        if tree_c is not None:
            ex = face.exterior
            ncr = sum(1 for j in tree_c.query(ex.buffer(3)) if ex.distance(cruz[j]) < 3)
        face_ll = Polygon([inv.transform(x, y) for x, y in face.exterior.coords])
        cll = face_ll.centroid
        linhas_out.append([pid, cod, uf, nome, face_ll.wkt, face.area,
                           face.exterior.length, len(face.exterior.coords) - 1,
                           len(vias), ncr, ret, pre, rec,
                           json.dumps(vias, ensure_ascii=False), cll.y, cll.x, "osm"])
        pid += 1; n_ret += int(ret)
    if linhas_out:
        c.executemany("INSERT INTO quadras (id,cod_municipio,uf,municipio,geom_wkt,area_m2,"
                      "perimetro_m,n_vertices,n_vias,n_cruzamentos,retangular,preenchimento,"
                      "cantos_90,vias,lat_centro,lng_centro,fonte) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", linhas_out)
    dt = time.time() - t0
    c.execute("INSERT INTO municipios_feitos VALUES (?,?,?,?,?,?,current_timestamp)",
              [cod, uf, nome, len(linhas_out), n_ret, dt])
    return len(linhas_out), n_ret, False


def uf_de(cod):
    d = json.loads(_json_ibge(
        f"https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{cod}", 60))
    return d["microrregiao"]["mesorregiao"]["UF"]["sigla"], d["nome"]


def garantir_municipio(cod, c=None):
    """Sob demanda: se o município já está na base, devolve as quadras dele; se
    não, processa só ele (baixando a região na primeira vez) e devolve.

    É a porta de entrada do resto do sistema — o pipeline de telhados chama isto
    e recebe as quadras prontas, sem se importar se já existiam ou acabaram de ser
    montadas. Nada de varrer o Brasil antes: cada município entra quando é pedido."""
    cod = str(cod)
    fechar = c is None
    if c is None: c = con()
    try:
        feito = c.execute("SELECT n_quadras FROM municipios_feitos WHERE cod_municipio=?",
                          [cod]).fetchone()
        if feito is None:
            uf, nome = uf_de(cod)
            pbf = baixar_pbf(UF_REGIAO[uf]); preparar_regiao(c, pbf)
            processar_municipio(c, pbf, cod, malha_municipios(uf)[cod], uf, nome)
        return c.execute("SELECT id, geom_wkt, area_m2, retangular, preenchimento, "
                         "n_cruzamentos, vias, lat_centro, lng_centro "
                         "FROM quadras WHERE cod_municipio=? ORDER BY area_m2 DESC",
                         [cod]).fetchall()
    finally:
        if fechar: c.close()


def processar_uf(uf, limite=None):
    uf = uf.upper(); reg = UF_REGIAO[uf]
    pbf = baixar_pbf(reg)
    c = con(); preparar_regiao(c, pbf)
    malha = malha_municipios(uf); nomes = nomes_municipios(uf)
    cods = sorted(malha)[:limite] if limite else sorted(malha)
    print(f"{uf}: {len(cods)} municipios (extrato {reg})", flush=True)
    tq = tr = 0
    for i, cod in enumerate(cods, 1):
        nome = nomes.get(cod, "?")
        n, r, pulou = processar_municipio(c, pbf, cod, malha[cod], uf, nome)
        tq += n; tr += r
        marca = "cache" if pulou else f"{n:5d} quadras, {r:4d} retangulares"
        print(f"  [{i:4d}/{len(cods)}] {cod} {nome[:28]:28s} {marca}", flush=True)
    print(f"\n{uf}: {tq:,} quadras ({tr:,} retangulares, {100*tr/max(1,tq):.0f}%)", flush=True)
    c.close()


def main():
    if len(sys.argv) < 2: print(__doc__); return
    cmd = sys.argv[1]
    if cmd == "regioes":
        for r, (ufs, mb) in REGIOES.items(): print(f"  {r:13s} {mb:4d} MB   {ufs}")
    elif cmd == "uf":
        processar_uf(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif cmd == "mun":
        cod = sys.argv[2]
        qs = garantir_municipio(cod)
        ret = sum(1 for q in qs if q[3])
        print(f"{cod}: {len(qs)} quadras, {ret} retangulares "
              f"({100*ret/max(1,len(qs)):.0f}%)", flush=True)
    elif cmd == "uf":                          # lote opcional, não é o fluxo normal
        processar_uf(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
    else: print(__doc__)


if __name__ == "__main__":
    main()
