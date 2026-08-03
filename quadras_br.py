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
    2. carrega as vias da região na tabela `osm_via` do Postgres (uma vez);
    3. recorta as vias do município pela caixa da malha municipal do IBGE (0,1 s);
    4. une as linhas — o que noda a rede em todo cruzamento — e polygoniza;
    5. classifica cada face (retangular?, vias, cruzamentos) e grava.
Depois da região preparada, cada município novo custa ~3 s.

uso:
    quadras_br.py mun 2607653                  garante um município (código IBGE)
    quadras_br.py uf PE                        lote de uma UF (opcional)
    quadras_br.py regioes                      lista as regiões e tamanhos
env:
    AREA_MIN=250   AREA_MAX=250000   metros quadrados por face
"""
import sys, os, json, math, time, urllib.request, hashlib
from pathlib import Path
from collections import Counter

import numpy as np
import base_comum as bc
from shapely import wkb as _wkb, wkt as _wkt
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import unary_union, polygonize
from pyproj import Transformer

RAIZ = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("CACHE_PBF", RAIZ / "dados" / "pbf"))
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
class _PG:
    """Adaptador fino do psycopg2 para a API que este módulo já usava.

    O resto do arquivo foi escrito contra o DuckDB, onde
    `c.execute(sql, [p]).fetchall()` funciona direto na conexão. O psycopg2 usa
    cursor e o marcador `%s` em vez de `?`. Traduzir aqui, num lugar só, evita
    reescrever dezenas de chamadas — e o `?` só aparece como marcador neste
    módulo, nunca dentro de literal."""

    def __init__(self, conexao):
        self._con = conexao
        self._cur = conexao.cursor()

    def execute(self, sql, params=None):
        self._cur.execute(sql.replace("?", "%s"), params or None)
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(sql.replace("?", "%s"), seq)
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    def commit(self):
        self._con.commit()

    def close(self):
        try:
            self._con.commit()
        finally:
            self._cur.close()
            self._con.close()


def con():
    """Conexão com o Postgres+PostGIS, garantindo o esquema.

    As vias saíram de um DuckDB de 3 GB dentro da pasta do sistema para cá: dado
    é do banco, não do diretório de desenvolvimento. E o índice GIST tornou a
    consulta por caixa 48× mais rápida (0,06 s contra 2,91 s medidos na caixa de
    Itambé, com resultado idêntico via a via)."""
    c = _PG(bc.conectar())
    c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    c.execute("""
        CREATE TABLE IF NOT EXISTS osm_via (
            id     bigserial PRIMARY KEY,
            regiao text NOT NULL,             -- extrato do Geofabrik de origem
            nome   text,
            tipo   text,                      -- highway=
            geom   geometry(Geometry, 4326) NOT NULL
        )""")
    c.execute("""
        CREATE TABLE IF NOT EXISTS osm_quadra (
            id            bigint PRIMARY KEY,
            cod_municipio text, uf text, municipio text,
            area_m2       double precision, perimetro_m double precision,
            n_vertices    integer, n_vias integer, n_cruzamentos integer,
            retangular    boolean, preenchimento double precision,
            cantos_90     integer,
            vias          text,               -- JSON [{nome,tipo,m}]
            lat_centro    double precision, lng_centro double precision,
            fonte         text, criado_em timestamp DEFAULT now(),
            geom          geometry(Geometry, 4326) NOT NULL
        )""")
    c.execute("""CREATE TABLE IF NOT EXISTS osm_municipio_feito (
                    cod_municipio text PRIMARY KEY, uf text, municipio text,
                    n_quadras integer, n_retangulares integer,
                    segundos double precision, feito_em timestamp DEFAULT now())""")
    c.execute("""CREATE TABLE IF NOT EXISTS ibge_malha (
                    cod_municipio text PRIMARY KEY, nome text, uf text,
                    geom geometry(Geometry, 4326) NOT NULL)""")
    for ix, tab, col in (("ix_osm_via_geom", "osm_via", "USING GIST (geom)"),
                         ("ix_osm_via_regiao", "osm_via", "(regiao)"),
                         ("ix_osm_quadra_geom", "osm_quadra", "USING GIST (geom)"),
                         ("ix_osm_quadra_mun", "osm_quadra", "(cod_municipio)"),
                         ("ix_ibge_malha_geom", "ibge_malha", "USING GIST (geom)"),
                         ("ix_ibge_malha_uf", "ibge_malha", "(uf)")):
        c.execute(f"CREATE INDEX IF NOT EXISTS {ix} ON {tab} {col}")
    c.commit()
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


def malha_municipios(uf, c=None):
    """Polígonos municipais do IBGE — do BANCO, baixando da API se faltar a UF.

    Antes isto ficava em `dados/pbf/malha_UF.json` e em `malhas/UF.geojson`, duas
    cópias do mesmo dado dentro da pasta do sistema. Agora mora em `ibge_malha`,
    com índice GIST: quem precisa do município de um ponto pergunta ao banco em
    vez de abrir 13 arquivos e testar polígono a polígono em Python."""
    fechar = c is None
    c = c or con()
    try:
        rs = c.execute("SELECT cod_municipio, ST_AsText(geom) FROM ibge_malha "
                       "WHERE uf = ?", [uf.upper()]).fetchall()
        if not rs:
            print(f"  malha de {uf} não está no banco — baixando do IBGE...", flush=True)
            gj = json.loads(_json_ibge(
                f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
                f"?formato=application/vnd.geo+json&intrarregiao=municipio"))
            for f in gj.get("features", []):
                cod = str((f.get("properties") or {}).get("codarea") or "").strip()
                if not cod:
                    continue
                c.execute("""INSERT INTO ibge_malha (cod_municipio, nome, uf, geom)
                             VALUES (?,?,?,ST_SetSRID(ST_GeomFromGeoJSON(?),4326))
                             ON CONFLICT (cod_municipio) DO UPDATE
                               SET uf = EXCLUDED.uf, geom = EXCLUDED.geom""",
                          [cod, (f.get("properties") or {}).get("nome"), uf.upper(),
                           json.dumps(f["geometry"])])
            c.commit()
            rs = c.execute("SELECT cod_municipio, ST_AsText(geom) FROM ibge_malha "
                           "WHERE uf = ?", [uf.upper()]).fetchall()
        return {cod: _wkt.loads(w) for cod, w in rs}
    finally:
        if fechar:
            c.close()


def municipio_do_ponto(lat, lng, c=None):
    """Que município contém este ponto? Uma consulta indexada, no banco.

    Substitui a varredura de `malhas/*.geojson` em Python — que abria cada
    arquivo, montava cada polígono e testava um a um."""
    fechar = c is None
    c = c or con()
    try:
        r = c.execute("""SELECT cod_municipio, nome, uf FROM ibge_malha
                          WHERE ST_Contains(geom, ST_SetSRID(ST_Point(?, ?), 4326))
                          LIMIT 1""", [lng, lat]).fetchone()
        return (r[1] or "", r[2] or "", r[0]) if r else ("", "", "")
    finally:
        if fechar:
            c.close()


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


def regiao_de(pbf):
    """Nome do extrato do Geofabrik: 'nordeste-latest.osm.pbf' → 'nordeste'."""
    return Path(pbf).stem.replace("-latest.osm", "")


# nome antigo, de quando cada região era uma TABELA
tabela_vias = regiao_de


def preparar_regiao(c, pbf):
    """Carrega as vias do extrato UMA vez, se a região ainda não estiver no banco.

    É o passo que torna o Brasil viável: sem ele, cada município reabriria o
    extrato inteiro pelo GDAL (98 s por município — o país levaria 6 dias).

    A leitura do PBF continua sendo do DuckDB, que tem o driver espacial do GDAL;
    ele entra aqui como LEITOR de arquivo, em memória, e o resultado vai para o
    Postgres. Depois disso o .pbf não é mais necessário — e é re-baixável do
    Geofabrik quando uma região nova for pedida."""
    reg = regiao_de(pbf)
    n = c.execute("SELECT count(*) FROM osm_via WHERE regiao=?", [reg]).fetchone()[0]
    if n > 0:
        print(f"  vias já no banco: {reg} ({n:,} linhas)", flush=True)
        return reg
    print(f"  extraindo vias de {Path(pbf).name} (uma vez só)...", flush=True)
    t0 = time.time()
    import csv
    import io

    import duckdb                                   # só para ler o PBF pelo GDAL
    d = duckdb.connect()
    d.execute("INSTALL spatial; LOAD spatial;")
    tipos = ", ".join(f"'{x}'" for x in VIAS_OK)
    rs = d.execute(f"""
        SELECT ST_AsWKB(geom) AS g, name, highway
        FROM st_read('{str(pbf).replace(chr(92), '/')}', layer='lines',
                     open_options=['INTERLEAVED_READING=YES'])
        WHERE highway IN ({tipos}) AND geom IS NOT NULL
    """).fetchall()
    d.close()
    # COPY e não INSERT: são milhões de linhas, e a diferença é de minutos para
    # horas. A geometria viaja como WKB hexadecimal dentro de um CSV (onde a
    # barra invertida não é escape) e só vira `geometry` no INSERT final.
    c.execute("""CREATE TEMP TABLE _stage_via
                 (regiao text, nome text, tipo text, wkb bytea)""")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for g, nm, hw in rs:
        w.writerow((reg, nm, hw, "\\x" + bytes(g).hex()))
    buf.seek(0)
    c._cur.copy_expert("COPY _stage_via (regiao, nome, tipo, wkb) "
                       "FROM STDIN WITH (FORMAT csv)", buf)
    c.execute("""INSERT INTO osm_via (regiao, nome, tipo, geom)
                 SELECT regiao, nome, tipo, ST_SetSRID(ST_GeomFromWKB(wkb), 4326)
                   FROM _stage_via""")
    c.execute("DROP TABLE _stage_via")
    c.execute("ANALYZE osm_via")
    c.commit()
    print(f"  {len(rs):,} vias em {time.time()-t0:.0f}s -> osm_via (regiao={reg})",
          flush=True)
    return reg


def _prox_id(c):
    r = c.execute("SELECT coalesce(max(id), 0) FROM osm_quadra").fetchone()[0]
    return int(r) + 1


def processar_municipio(c, pbf, cod, poli_mun, uf, nome):
    ja = c.execute("SELECT 1 FROM osm_municipio_feito WHERE cod_municipio = ?",
                   [cod]).fetchone()
    if ja: return 0, 0, True
    t0 = time.time()
    minlng, minlat, maxlng, maxlat = poli_mun.bounds
    # `&&` compara as caixas envolventes usando o índice GIST — é o mesmo recorte
    # que antes se fazia com quatro colunas xmin/xmax/ymin/ymax, só que indexado
    # de verdade: 0,06 s contra 2,91 s na caixa de Itambé, resultado idêntico.
    linhas_ll, meta = [], []
    rs = c.execute("""SELECT ST_AsBinary(geom), nome, tipo FROM osm_via
                       WHERE regiao = ?
                         AND tipo = ANY(?)
                         AND geom && ST_MakeEnvelope(?, ?, ?, ?, 4326)""",
                   [regiao_de(pbf), list(VIAS_OK),
                    minlng, minlat, maxlng, maxlat]).fetchall()
    for g, nm, hw in rs:
        try: ls = _wkb.loads(bytes(g))
        except Exception: continue
        if ls.is_empty or ls.geom_type != "LineString" or len(ls.coords) < 2: continue
        linhas_ll.append(ls); meta.append((nm or "(sem nome)", hw))
    if len(linhas_ll) < 4:
        c.execute("INSERT INTO osm_municipio_feito (cod_municipio,uf,municipio,"
                  "n_quadras,n_retangulares,segundos) VALUES (?,?,?,?,?,?)",
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
        c.execute("INSERT INTO osm_municipio_feito (cod_municipio,uf,municipio,"
                  "n_quadras,n_retangulares,segundos) VALUES (?,?,?,?,?,?)",
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
        c.executemany("INSERT INTO osm_quadra (id,cod_municipio,uf,municipio,geom,area_m2,"
                      "perimetro_m,n_vertices,n_vias,n_cruzamentos,retangular,preenchimento,"
                      "cantos_90,vias,lat_centro,lng_centro,fonte) VALUES "
                      "(?,?,?,?,ST_SetSRID(ST_GeomFromText(?),4326),?,?,?,?,?,?,?,?,?,?,?,?)",
                      linhas_out)
    dt = time.time() - t0
    c.execute("INSERT INTO osm_municipio_feito (cod_municipio,uf,municipio,"
                  "n_quadras,n_retangulares,segundos) VALUES (?,?,?,?,?,?)",
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
        feito = c.execute("SELECT n_quadras FROM osm_municipio_feito "
                          "WHERE cod_municipio=?", [cod]).fetchone()
        if feito is None:
            uf, nome = uf_de(cod)
            pbf = baixar_pbf(UF_REGIAO[uf]); preparar_regiao(c, pbf)
            processar_municipio(c, pbf, cod, malha_municipios(uf)[cod], uf, nome)
        return c.execute("SELECT id, ST_AsText(geom), area_m2, retangular, "
                         "preenchimento, n_cruzamentos, vias, lat_centro, lng_centro "
                         "FROM osm_quadra WHERE cod_municipio=? ORDER BY area_m2 DESC",
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
