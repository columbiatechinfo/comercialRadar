# -*- coding: utf-8 -*-
"""Os CINCO passos da análise de quadras. Cada um roda sozinho e é retomável.

    1. area    — a área desenhada vira uma sessão
    2. vias    — vias do OSM da área + NOME CANÔNICO por via + quadras montadas
    3. borda   — a borda real da quadra (a do OSM corre pelo eixo da rua)
    4. pontos  — endereços a até 20 m das vias da quadra + os de dentro dela
    5. faces   — via canônica e paridade de cada face; marca o que destoa

Princípio que atravessa tudo: NENHUM PONTO É MOVIDO. A coordenada gravada é a do
CNEFE, sempre. O processo classifica; não corrige a base.

Cada passo lê o que precisa do banco e grava o que produziu antes de devolver —
é isso que torna a retomada possível sem estado em memória.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from shapely import wkt as _wkt
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize, unary_union

import base_comum as bc
import quadras_db as QD

BASE = Path(__file__).resolve().parent

# ── passo 3: sem telhado para provar onde o lote começa, a borda real sai da
# largura da via. A quadra do OSM corre pelo EIXO, então cada face recua METADE
# da caixa viária. Valores típicos de cidade brasileira, por tipo do OSM.
LARGURA_VIA_M = {
    "motorway": 20.0, "trunk": 16.0, "primary": 14.0, "secondary": 12.0,
    "tertiary": 10.0, "residential": 8.0, "unclassified": 8.0,
    "living_street": 6.0, "service": 5.0, "road": 8.0, "pedestrian": 5.0,
}
LARGURA_PADRAO_M = 8.0

# o polygonize precisa das ruas do ENTORNO para fechar a quadra da borda: quem
# desenha em cima de um quarteirão só pega as 2 vias que cruzam o desenho
MARGEM_VIAS_M = 250.0
FAIXA_PONTOS_M = 20.0        # passo 4: distância máxima ao eixo da via
RAIO_FACE_M = 25.0           # passo 5: até onde um ponto pertence a uma face
LIMIAR_PROFUNDIDADE_M = 2.0  # passo 5: separação de recuo que decide a paridade
# faixa em volta do eixo onde a coordenada NÃO diz o lado (o CNEFE põe
# endereço em cima da linha): dentro dela quem decide é a paridade
TOLERANCIA_LADO_MIN_M = 3.0
# alcance da busca da tolerância por identidade (logradouro + CEP + paridade)
RAIO_TOLERANCIA_M = 1000.0
# ...mas o que vale é a distância à FACE: medido em 6 sessões, o ponto aprovado
# pela geometria fica a no máximo 24,1 m da linha da face (mediana 4,3 m),
# enquanto o trazido só pela identidade fica a 363 m de mediana. Sem este corte
# a regra puxava os outros quarteirões da mesma rua — mesmo CEP, mesma paridade,
# e nada a ver com esta quadra. 30 m preserva 100% dos legítimos.
DIST_MAX_FACE_M = 30.0
MIN_VOTOS_NOME = 2           # passo 5: abaixo disto a face não elege nome nenhum
# resgate por coordenada nível 1: abaixo deste número de aprovados a mediana não
# significa nada, e a régua passa a ser o mais distante deles
MIN_APROVADOS_MEDIANA = 4
QUEBRA_FACE = 38.0           # ângulo que separa uma face da seguinte
AREA_MIN_M2, AREA_MAX_M2 = 200.0, 400_000.0
# Largura mínima de um quarteirão. Avenida desenhada como duas vias paralelas no
# OSM fecha uma FATIA entre elas, que o polygonize entrega como se fosse quadra.
# Medido numa área de Itambé: as fatias tinham 6,7 · 7,2 · 7,8 · 8,1 · 11,7 m de
# largura, e a quadra real mais estreita tinha 25,8 m. 15 m fica no vão.
LARGURA_MIN_QUADRA_M = 15.0
# Margem da ESQUINA no passo 6. Encurtar o trilho só pela meia caixa da via
# transversal deixa a ponta EM CIMA do trilho dela: numa esquina de 90°, andar
# `recuo` metros ao longo da face leva exatamente à linha da transversal
# (medido: 0,03 m de distância). Esta margem afasta a primeira e a última porta
# do cruzamento — e corresponde ao recuo lateral que o lote de canto tem.
MARGEM_ESQUINA_M = 4.0
# Teto do deslocamento no passo 6. Mover um endereço mais que isto só para ele
# caber na régua é trocar um dado ruim por um palpite: a régua vale onde a
# coordenada já está quase certa. Quem passa do teto FICA onde está e é marcado
# — o mapa mostra que ali a numeração e a coordenada discordam de verdade.
DESLOC_MAX_M = 10.0
# Fração do comprimento que a via precisa correr AO LONGO da borda de uma quadra
# para ser considerada formadora dela. Ver `_fecham_quadra`: a distribuição é
# bimodal, então o valor exato no meio pouco importa.
FRACAO_BORDA_MIN = 0.50
# Passo 6: raio para achar telhado perto de um ponto de via que NÃO fecha quadra.
# Quem mora em beco, rua projetada ou acesso de engenho não tem testada de
# quarteirão para onde ir — projetar na face da quadra vizinha arrastava esses
# pontos 50 a 164 m. Havendo telhado por perto, a coordenada do CNEFE já está
# num lugar construído e é melhor que qualquer projeção: fica onde está.
RAIO_TELHADO_VIA_ABERTA_M = 53.0


def _metros(lat: float):
    """(metros por grau de latitude, metros por grau de longitude) no lugar."""
    return 111_320.0, 111_320.0 * math.cos(math.radians(lat))


def _dist_m(la1, ln1, la2, ln2):
    my, mx = _metros((la1 + la2) / 2)
    return math.hypot((ln1 - ln2) * mx, (la1 - la2) * my)


def _proj(geoms, lat0, lng0):
    """lat/lng → metros locais. Trabalhar em graus mistura unidades e já zerou
    um passo inteiro no processo anterior."""
    my, mx = _metros(lat0)
    return [type(g)([((x - lng0) * mx, (y - lat0) * my) for x, y in g.coords])
            for g in geoms]


# ════════════════════════════════════════════════════════════════════════════
# PASSO 1 — a área
# ════════════════════════════════════════════════════════════════════════════
def passo1_area(area_wkt: str, con=None) -> dict:
    """Registra a área desenhada e descobre de que município ela é."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        area = _wkt.loads(area_wkt)
        if area.is_empty or not area.is_valid:
            raise ValueError("área inválida")
        c = area.centroid
        mun, uf, cod = _municipio_do_ponto(c.y, c.x, con)
        sid = QD.nova_sessao(area.wkt, mun, uf, cod, con)
        my, mx = _metros(c.y)
        km2 = area.area * my * mx / 1e6
        QD.marcar_passo(sid, 1, {"area_km2": round(km2, 3), "municipio": mun,
                                 "uf": uf, "cod_municipio": cod}, con=con)
        print(f"[1/5] sessão {sid} · {mun or '?'}/{uf or '?'} · {km2:.2f} km²", flush=True)
        return {"sessao": sid, "municipio": mun, "uf": uf, "cod_municipio": cod,
                "area_km2": round(km2, 3)}
    finally:
        if fechar:
            con.close()


def _municipio_do_ponto(lat, lng, con):
    """De que município é este ponto? Pela MALHA do IBGE, agora no banco.

    Antes isso saía do CNEFE com um GROUP BY por caixa envolvente. Sem filtro de
    município a consulta não tinha índice utilizável e varria a tabela de 111 M
    de linhas: **31,4 s dos 32 s do processo inteiro**. Passou então a varrer os
    GeoJSON de `malhas/` em Python — abrindo 13 arquivos e testando polígono a
    polígono. Hoje é `ST_Contains` sobre `ibge_malha`, com índice GIST: um
    índice espacial de verdade, e nenhum dado solto na pasta.

    O CNEFE fica como último recurso, e o `rollback` no except não é zelo: uma
    consulta que falha deixa a transação ABORTADA no psycopg2, e o comando
    seguinte morre com 'comandos ignorados até o fim do bloco'."""
    try:
        import quadras_br as QB
        nome, uf, cod = QB.municipio_do_ponto(lat, lng)
        if cod:
            return nome, uf, cod
    except Exception as e:
        print(f"  [município] malha do banco falhou: {str(e)[:80]}", flush=True)
    # sem malha em disco: cai no CNEFE, restringindo a caixa ao mínimo
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT cod_municipio FROM ibge_cnefe
                            WHERE latitude::double precision  BETWEEN %s AND %s
                              AND longitude::double precision BETWEEN %s AND %s
                            LIMIT 1""",
                        (lat - 0.002, lat + 0.002, lng - 0.002, lng + 0.002))
            r = cur.fetchone()
        if r:
            cod = str(r[0])
            nome, uf = _nome_municipio(cod)
            return nome, uf, cod
    except Exception as e:
        con.rollback()
        print(f"  [município] não identificado: {str(e)[:80]}", flush=True)
    return "", "", ""


def _nome_municipio(cod: str):
    """Nome e UF a partir da base de quadras do OSM (DuckDB), que já os guarda."""
    try:
        import quadras_br as QB
        c = QB.con()
        r = c.execute("SELECT municipio, uf FROM municipios_feitos WHERE cod_municipio=?",
                      [cod]).fetchone()
        if r:
            return r[0], r[1]
        r = c.execute("SELECT municipio, uf FROM quadras WHERE cod_municipio=? LIMIT 1",
                      [cod]).fetchone()
        return (r[0], r[1]) if r else ("", "")
    except Exception:
        return "", ""


# ════════════════════════════════════════════════════════════════════════════
# PASSO 2 — vias do OSM, nome canônico por via, e as quadras
# ════════════════════════════════════════════════════════════════════════════
def passo2_vias(sid: str, usar_proxy=True, com_maps=False, con=None) -> dict:
    """Vias do OSM que cruzam a área → nome canônico de cada uma → quadras.

    As quadras nascem do `polygonize` da rede viária unida: unir as linhas noda a
    rede em todo cruzamento, e cada face fechada é um quarteirão. Por isso a
    borda corre pelo EIXO da rua — o passo 3 conserta isso."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        s = QD.sessao(sid, con)
        if not s:
            raise ValueError(f"sessão {sid} não existe")
        QD.limpar_passo(sid, 2, con)
        area = _wkt.loads(s["area_wkt"])
        linhas, meta = _vias_do_osm(area, s["cod_municipio"])
        if len(linhas) < 4:
            raise ValueError(f"só {len(linhas)} vias na área — sem rede para fechar quadra")
        print(f"[2/5] {len(linhas)} vias do OSM (área + {MARGEM_VIAS_M:.0f} m de "
              f"margem, para fechar as quadras da borda)", flush=True)

        # ── quadras ANTES do nome canônico ──
        # A margem existe só para a rede fechar. Nomear as 56 vias que ela traz
        # quando a área tem 1 quadra é pagar 14× a mais por nada: primeiro se
        # sabe quais quadras ficam, depois se nomeia só quem faz borda com elas.
        faces_ll = _quadras_da_rede(linhas, area)
        print(f"[2/5] {len(faces_ll)} quadras montadas", flush=True)
        if not faces_ll:
            raise ValueError("nenhuma quadra fechada dentro da área desenhada")

        usadas = _vias_das_quadras(faces_ll, linhas)
        # o contexto é a CIDADE, não o desenho — ver `quadras_do_municipio`
        fecham = _fecham_quadra(quadras_do_municipio(s["cod_municipio"]), linhas)
        print(f"      {sum(1 for x in fecham if not x)} vias NÃO fecham quadra "
              f"(beco, rua projetada, acesso) — seus pontos não são alinhados "
              f"quando há telhado por perto", flush=True)
        alvos = []
        for i, ls in enumerate(linhas):
            p = ls.interpolate(0.5, normalized=True)
            alvos.append({"lat": p.y, "lng": p.x, "usar": i in usadas,
                          "fecha": fecham[i]})
        pedir = [a for a in alvos if a["usar"]]
        # O Maps está DESLIGADO por padrão: ele custava mais tempo que todo o
        # resto do processo somado (~60 s contra 1,4 s). O nome da face sai da
        # maioria dos endereços dela, no passo 5. A coluna continua aqui para
        # quando a leitura for religada com `--com-maps`.
        if not com_maps:
            print(f"      nome canônico do Maps: desligado — o nome vem da maioria "
                  f"dos endereços de cada face (passo 5)", flush=True)
        else:
            import asyncio
            import quadras_canonico as QC
            t0 = time.time()
            n = asyncio.run(QC.nomear(pedir, usar_proxy=usar_proxy))
            print(f"      nome canônico: {n}/{len(pedir)} vias das quadras lidas no "
                  f"Maps em {time.time() - t0:.0f} s "
                  f"({len(linhas) - len(pedir)} do entorno não foram consultadas)",
                  flush=True)

        with con.cursor() as cur:
            for ls, (nome, tipo), a in zip(linhas, meta, alvos):
                cur.execute("""INSERT INTO via_osm
                    (sessao_id, nome_osm, tipo, geom_wkt, comprimento_m,
                     lat_consulta, lng_consulta, nome_canonico, canonico_erro,
                     fecha_quadra)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (sid, nome, tipo, ls.wkt, _comprimento_m(ls), a["lat"], a["lng"],
                     a.get("nome_canonico"), a.get("erro"), a["fecha"]))
            cur.execute("UPDATE via_osm SET canonico_em=now() "
                        "WHERE sessao_id=%s AND nome_canonico IS NOT NULL", (sid,))
        con.commit()
        with con.cursor() as cur:
            for pol in faces_ll:
                cen = pol.centroid
                my, mx = _metros(cen.y)
                cur.execute("""INSERT INTO quadra
                    (sessao_id, geom_osm, area_osm_m2, lat_centro, lng_centro, vias)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (sid, pol.wkt, pol.area * my * mx, cen.y, cen.x,
                     json.dumps(_vias_da_quadra(pol, linhas, meta), ensure_ascii=False)))
        con.commit()
        res = {"vias": len(linhas), "quadras": len(faces_ll),
               "canonico": sum(1 for a in alvos if a.get("nome_canonico"))}
        QD.marcar_passo(sid, 2, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def _vias_do_osm(area: Polygon, cod_municipio: str):
    """Vias do OSM em volta da área, lidas da base do `quadras_br`.

    A busca vai ALÉM da área desenhada (`MARGEM_VIAS_M`). Uma quadra é fechada
    pelas vias que a cercam, e quem desenha em cima de UM quarteirão pega só as
    duas ruas que cruzam o desenho — a rede não fecha e o passo morria com "só 2
    vias". Com a margem, as ruas do entorno entram, o `polygonize` fecha, e
    depois só ficam as quadras cujo interior cai na área pedida."""
    import quadras_br as QB
    from shapely import wkb as _wkb
    c = QB.con()
    if cod_municipio:
        QB.garantir_municipio(str(cod_municipio), c)
    g = MARGEM_VIAS_M / 111320.0
    minlng, minlat, maxlng, maxlat = area.buffer(g).bounds
    viz = area.buffer(g)
    # Não é preciso mais escolher a REGIÃO: as vias das duas regiões vivem na
    # mesma tabela e o índice GIST recorta pela caixa. Antes, escolher "a última
    # tabela vias_*" trazia o Sudeste para uma área de Pernambuco e devolvia zero.
    # Ainda assim se confere se há alguma via ali, para o erro continuar claro.
    rows = c.execute("""SELECT ST_AsBinary(geom), nome, tipo FROM osm_via
                         WHERE tipo = ANY(?)
                           AND geom && ST_MakeEnvelope(?, ?, ?, ?, 4326)""",
                     [list(QB.VIAS_OK), minlng, minlat, maxlng, maxlat]).fetchall()
    if not rows:
        raise ValueError("nenhuma via do OSM nesta área — a região dela ainda não "
                         f"foi carregada; rode: quadras_br.py mun "
                         f"{cod_municipio or '<cod>'}")
    linhas, meta = [], []
    for g, nm, hw in rows:
        try:
            ls = _wkb.loads(bytes(g))
        except Exception:
            continue
        if ls.is_empty or ls.geom_type != "LineString" or len(ls.coords) < 2:
            continue
        if not ls.intersects(viz):
            continue
        linhas.append(ls)
        meta.append((nm or "", hw or ""))
    return linhas, meta


def _comprimento_m(ls: LineString) -> float:
    return sum(_dist_m(ls.coords[i][1], ls.coords[i][0],
                       ls.coords[i + 1][1], ls.coords[i + 1][0])
               for i in range(len(ls.coords) - 1))


def _quadras_da_rede(linhas, area):
    """Une as vias (o que noda a rede em cada cruzamento) e polygoniza."""
    lat0, lng0 = area.centroid.y, area.centroid.x
    my, mx = _metros(lat0)
    em_m = [LineString([((x - lng0) * mx, (y - lat0) * my) for x, y in l.coords])
            for l in linhas]
    rede = unary_union(em_m)
    area_m = Polygon([((x - lng0) * mx, (y - lat0) * my)
                      for x, y in area.exterior.coords])
    out, fatias = [], 0
    for p in polygonize(rede):
        if not (AREA_MIN_M2 < p.area < AREA_MAX_M2):
            continue
        if not area_m.contains(p.representative_point()):
            continue
        if _largura_minima(p) < LARGURA_MIN_QUADRA_M:
            fatias += 1          # fatia entre duas vias paralelas, não é quarteirão
            continue
        out.append(Polygon([(lng0 + x / mx, lat0 + y / my) for x, y in p.exterior.coords]))
    if fatias:
        print(f"      {fatias} fatia(s) entre vias paralelas descartadas "
              f"(menos de {LARGURA_MIN_QUADRA_M:.0f} m de largura)", flush=True)
    return out


def _largura_minima(pol) -> float:
    """Lado menor do retângulo mínimo — a largura real do polígono, em metros
    (o polígono já vem em metros locais)."""
    try:
        cs = list(pol.minimum_rotated_rectangle.exterior.coords)
    except Exception:
        return 1e9
    if len(cs) < 4:
        return 1e9
    lados = [math.dist(cs[i], cs[i + 1]) for i in range(3)]
    return min(lados)


def quadras_do_municipio(cod_municipio, con=None):
    """Todas as quadras do município na base do OSM — o MUNDO REAL de referência.

    Existe porque duas perguntas do processo não são sobre o desenho, e sim sobre
    a cidade: "esta via forma quarteirão?" e "esta rua tem outro lado?". Responder
    olhando só as quadras que sobraram na sessão dá resultado catastrófico quando
    o usuário desenha em cima de UMA quadra: sem outra quadra para comparar, todas
    as vias viram "não fecha quadra" (82 de 82, medido) e todas as faces viram
    "rua de um lado só" — aí nenhum endereço é reprovado e o quarteirão vizinho
    entra inteiro. Itambé tem 609 quadras na base; a sessão tinha 1."""
    if not cod_municipio:
        return []
    import quadras_br as QB
    c = QB.con() if con is None else con
    try:
        rs = c.execute("SELECT ST_AsText(geom) FROM osm_quadra WHERE cod_municipio = ?",
                       [str(cod_municipio)]).fetchall()
        return [_wkt.loads(w) for (w,) in rs]
    finally:
        if con is None:
            c.close()


def _fecham_quadra(contexto, linhas, tol_m: float = 12.0) -> list:
    """Quais vias correm AO LONGO da borda de alguma quadra — uma por linha.

    `contexto` são as quadras do MUNICÍPIO (ver `quadras_do_municipio`), não as da
    sessão: a pergunta é sobre a cidade, não sobre o recorte desenhado.

    Não confundir com `_vias_das_quadras`, que só pergunta se a via ENCOSTA numa
    borda: uma viela que cruza a rua perpendicularmente atravessa o buffer e
    ganha ~24 m de sobreposição, o suficiente para o critério de lá (8 m). Aqui
    o que vale é a FRAÇÃO do comprimento que acompanha a borda.

    Medido em Itambé (1.452 vias), a distribuição é bimodal — 506 vias abaixo de
    10% e 737 acima de 90%, quase nada no meio. Por isso o corte em 50% é
    estável: entre 30% e 70% o total só varia de 597 para 688 vias."""
    if not contexto:
        return [True] * len(linhas)      # sem referência, não se acusa ninguém
    bordas = unary_union([p.exterior for p in contexto]).buffer(tol_m / 111320.0)
    out = []
    for ls in linhas:
        tot = ls.length
        out.append(bool(tot) and ls.intersection(bordas).length / tot >= FRACAO_BORDA_MIN)
    return out


def _vias_das_quadras(faces_ll, linhas, tol_m: float = 12.0) -> set:
    """Índices das vias que fazem borda com alguma quadra mantida.

    São as únicas que precisam de nome canônico: as outras vieram só da margem
    que fecha a rede."""
    g = tol_m / 111320.0
    bordas = [p.exterior.buffer(g) for p in faces_ll]
    return {i for i, ls in enumerate(linhas)
            if any(ls.intersects(b) and ls.intersection(b).length * 111320.0 >= 8
                   for b in bordas)}


def _vias_da_quadra(pol, linhas, meta):
    """Quais vias formam esta quadra, por quanto de borda cada uma acompanha."""
    b = pol.exterior.buffer(2.0 / 111320.0)
    d = {}
    for ls, (nome, tipo) in zip(linhas, meta):
        it = ls.intersection(b)
        comp = getattr(it, "length", 0.0) * 111320.0
        if comp < 8:
            continue
        k = (nome, tipo)
        d[k] = d.get(k, 0.0) + comp
    return [{"nome": n, "tipo": t, "m": round(m)}
            for (n, t), m in sorted(d.items(), key=lambda x: -x[1])]


# ════════════════════════════════════════════════════════════════════════════
# PASSO 3 — a borda real
# ════════════════════════════════════════════════════════════════════════════
def passo3_borda(sid: str, con=None) -> dict:
    """Encolhe cada quadra até a borda real, FACE A FACE.

    A quadra do OSM corre pelo eixo da rua, então meia caixa viária fica dentro
    do polígono. Cada face recua metade da largura da SUA via — recuo único
    trava no lado mais estreito e deixa os outros sobrando via.

    Sem telhado detectado (o processo anterior recuava até ~1 m da construção
    mais próxima), a largura vem do tipo da via no OSM. É ESTIMATIVA, e o recuo
    aplicado fica gravado em `quadra_face.recuo_m` para poder ser auditado."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        QD.limpar_passo(sid, 3, con)
        qs = QD.quadras(sid, con)
        vias = QD.vias(sid, con)
        linhas = [(v, _wkt.loads(v["geom_wkt"])) for v in vias]
        n_ok = 0
        recuos = []
        with con.cursor() as cur:
            for q in qs:
                pol = _wkt.loads(q["geom_osm"])
                faces = _faces_da_quadra(pol)
                real, por_face = _encolher(pol, faces, linhas)
                cen = pol.centroid
                my, mx = _metros(cen.y)
                med = (sum(por_face.values()) / len(por_face)) if por_face else 0.0
                recuos.append(med)
                cur.execute("""UPDATE quadra SET geom_real=%s, recuo_medio_m=%s,
                                      area_real_m2=%s WHERE id=%s""",
                            (real.wkt if real else None, round(med, 2),
                             (real.area * my * mx) if real else None, q["id"]))
                for f in faces:
                    ls = LineString(f["anel"])
                    cur.execute("""INSERT INTO quadra_face
                        (quadra_id, face_idx, anel_wkt, comprimento_m, recuo_m)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (quadra_id, face_idx) DO UPDATE SET
                            anel_wkt=EXCLUDED.anel_wkt,
                            comprimento_m=EXCLUDED.comprimento_m,
                            recuo_m=EXCLUDED.recuo_m""",
                        (q["id"], f["idx"], ls.wkt, round(f["comprimento_m"], 1),
                         round(por_face.get(f["idx"], 0.0), 2)))
                n_ok += 1 if real else 0
        con.commit()
        res = {"quadras": len(qs), "com_borda_real": n_ok,
               "recuo_medio_m": round(sum(recuos) / len(recuos), 2) if recuos else 0}
        print(f"[3/5] borda real em {n_ok}/{len(qs)} quadras · "
              f"recuo médio {res['recuo_medio_m']} m", flush=True)
        QD.marcar_passo(sid, 3, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def _faces_da_quadra(pol: Polygon) -> list[dict]:
    """Quebra o contorno nas faces: agrupa arestas seguidas enquanto o rumo não
    virar mais que `QUEBRA_FACE` — a virada é a esquina."""
    cs = list(pol.exterior.coords)[:-1]
    n = len(cs)
    if n < 3:
        return []
    rumos = []
    for i in range(n):
        a, b = cs[i], cs[(i + 1) % n]
        my, mx = _metros(a[1])
        rumos.append(math.degrees(math.atan2((b[1] - a[1]) * my, (b[0] - a[0]) * mx)) % 360)
    quebras = [i for i in range(n)
               if min(abs(rumos[i] - rumos[i - 1]), 360 - abs(rumos[i] - rumos[i - 1])) >= QUEBRA_FACE]
    if not quebras:
        quebras = [0]
    faces = []
    for k, ini in enumerate(quebras):
        fim = quebras[(k + 1) % len(quebras)]
        idxs, i = [], ini
        while True:
            idxs.append(i)
            i = (i + 1) % n
            if i == fim:
                break
            if len(idxs) > n:
                break
        pts = [cs[j] for j in idxs] + [cs[fim]]
        ls = LineString(pts)
        faces.append({"idx": len(faces), "anel": pts, "comprimento_m": _comprimento_m(ls)})
    return faces


def _largura(tipo: str) -> float:
    return LARGURA_VIA_M.get((tipo or "").strip(), LARGURA_PADRAO_M)


def _encolher(pol: Polygon, faces: list[dict], linhas):
    """Subtrai de cada face a sua meia-caixa viária. Devolve (polígono, recuos)."""
    if not faces:
        return pol, {}
    lat0, lng0 = pol.centroid.y, pol.centroid.x
    my, mx = _metros(lat0)
    para_m = lambda pts: [((x - lng0) * mx, (y - lat0) * my) for x, y in pts]
    pol_m = Polygon(para_m(pol.exterior.coords))
    faixas, recuos = [], {}
    for f in faces:
        ls = LineString(f["anel"])
        # a via cujo eixo acompanha esta face define a largura
        melhor, melhor_d = None, 1e18
        meio = ls.interpolate(0.5, normalized=True)
        for v, g in linhas:
            d = g.distance(meio)
            if d < melhor_d:
                melhor, melhor_d = v, d
        rec = _largura(melhor["tipo"] if melhor else "") / 2.0
        recuos[f["idx"]] = rec
        faixas.append(LineString(para_m(f["anel"])).buffer(rec, cap_style=2))
    try:
        real_m = pol_m.difference(unary_union(faixas))
    except Exception:
        return pol, recuos
    if real_m.is_empty:
        return None, recuos
    if isinstance(real_m, MultiPolygon):
        real_m = max(real_m.geoms, key=lambda g: g.area)
    if real_m.area < 0.15 * pol_m.area:      # encolheu demais: a via não cabe
        return None, recuos
    return Polygon([(lng0 + x / mx, lat0 + y / my)
                    for x, y in real_m.exterior.coords]), recuos


# ════════════════════════════════════════════════════════════════════════════
# PASSO 4 — os pontos
# ════════════════════════════════════════════════════════════════════════════
def passo4_pontos(sid: str, con=None) -> dict:
    """Endereços do CNEFE a até 20 m das vias que formam a quadra (dos DOIS
    lados) mais todos os que caem dentro da geometria dela.

    O ponto entra na coordenada ORIGINAL. Um mesmo endereço pode servir a duas
    quadras vizinhas — a faixa de 20 m pega os dois lados da rua de propósito,
    e é o passo 5 que decide de qual lado ele é."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        s = QD.sessao(sid, con)
        QD.limpar_passo(sid, 4, con)
        qs = QD.quadras(sid, con)
        vias = [(v, _wkt.loads(v["geom_wkt"])) for v in QD.vias(sid, con)]
        # UMA consulta ao CNEFE para a área toda. Era uma por quadra: com 54
        # quadras, 54 varreduras da mesma região numa tabela de 111 M de linhas.
        area = _wkt.loads(s["area_wkt"])
        g = (FAIXA_PONTOS_M + MARGEM_VIAS_M) / 111320.0
        minlng, minlat, maxlng, maxlat = area.buffer(g).bounds
        with con.cursor() as cur:
            cur.execute("""
                SELECT cod_unico_endereco, latitude, longitude, cod_especie,
                       nom_tipo_seglogr, nom_seglogr, num_endereco, cep,
                       dsc_estabelecimento, nv_geo_coord
                  FROM ibge_cnefe
                 WHERE cod_municipio=%s
                   AND latitude::double precision  BETWEEN %s AND %s
                   AND longitude::double precision BETWEEN %s AND %s
            """, (str(s["cod_municipio"]), minlat, maxlat, minlng, maxlng))
            brutos = cur.fetchall()
        cand = []
        for (cod, la, ln, esp, tipo, via, num, cep, estab, nvg) in brutos:
            try:
                la, ln = float(la), float(ln)
            except (TypeError, ValueError):
                continue
            try:
                n = int(str(num).strip())
            except (TypeError, ValueError):
                n = None
            cand.append((cod, la, ln, Point(ln, la), esp,
                         f"{tipo or ''} {via or ''}".strip(), n,
                         (cep or "").strip() or None, (estab or "").strip() or None,
                         (nvg or "").strip() or None))
        print(f"      {len(cand)} endereços do CNEFE na área (1 consulta)", flush=True)

        total = 0
        with con.cursor() as cur:
            for q in qs:
                pol_osm = _wkt.loads(q["geom_osm"])
                pol_real = _wkt.loads(q["geom_real"]) if q["geom_real"] else pol_osm
                # as vias DESTA quadra: as que acompanham a borda dela
                perto = [gv for _, gv in vias
                         if gv.distance(pol_osm.exterior) * 111320.0 < 15.0]
                # caixa da quadra + faixa: descarta cedo quem está longe, sem
                # pagar distância ponto a linha para os milhares de endereços
                cx = pol_osm.buffer((FAIXA_PONTOS_M + 5) / 111320.0)
                linhas_novas = []
                for (cod, la, ln, p, esp, logr, n, cep, estab, nvg) in cand:
                    if not cx.contains(p):
                        continue
                    dentro = pol_real.contains(p) or pol_osm.contains(p)
                    dv = min((gv.distance(p) * 111320.0 for gv in perto), default=1e9)
                    if not dentro and dv > FAIXA_PONTOS_M:
                        continue
                    linhas_novas.append(
                        (sid, q["id"], cod, logr, n, cep, esp, estab, nvg, la, ln,
                         "dentro_quadra" if dentro else "faixa_via",
                         round(dv, 1) if dv < 1e8 else None))
                if linhas_novas:
                    cur.executemany("""INSERT INTO quadra_ponto
                        (sessao_id, quadra_id, cod_endereco, logradouro, numero, cep,
                         especie, estabelecimento, nv_geo, lat, lng, origem, dist_via_m)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", linhas_novas)
                    total += len(linhas_novas)
        con.commit()
        res = {"pontos": total, "quadras": len(qs), "faixa_m": FAIXA_PONTOS_M}
        print(f"[4/5] {total} pontos coletados em {len(qs)} quadras "
              f"(faixa de {FAIXA_PONTOS_M:.0f} m + interior)", flush=True)
        QD.marcar_passo(sid, 4, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


# ════════════════════════════════════════════════════════════════════════════
# PASSO 5 — via canônica e paridade por face
# ════════════════════════════════════════════════════════════════════════════
def _classificar_faces(sid: str, con) -> tuple:
    """Decide via, paridade e classificação de cada face. Roda DUAS vezes:

    a primeira com todos os pontos, para o expurgo de duplicados poder usar a
    paridade; a segunda depois dele, porque remover linhas MUDA a composição
    da face. Sem a segunda, a q116 guardava um veredito de antes do expurgo —
    "só 21 endereços, insuficiente" — quando com os 19 que restaram o recuo
    já separava (gap 2,1 m) e a face era `par`."""
    import statistics as st
    vias = [(v, _wkt.loads(v["geom_wkt"])) for v in QD.vias(sid, con)]
    faces = QD.faces(sid, con)
    pts = QD.pontos(sid, con)
    por_quadra: dict = {}
    for p in pts:
        por_quadra.setdefault(p["quadra_id"], []).append(p)

    pol_por_quadra = {q["id"]: _wkt.loads(q["geom_osm"]) for q in QD.quadras(sid, con)}
    # "há quadra do outro lado?" é pergunta sobre a CIDADE, não sobre o desenho.
    # Com as quadras da sessão, quem desenha em cima de UM quarteirão não tem
    # nenhuma outra para comparar: toda face vira "rua de um lado só", ninguém é
    # reprovado e o quarteirão vizinho entra inteiro. Aqui entra o município.
    from shapely import STRtree
    _ctx = quadras_do_municipio((QD.sessao(sid, con) or {}).get("cod_municipio"))
    if not _ctx:                     # município ainda não montado: usa a sessão
        _ctx = list(pol_por_quadra.values())
    _ids = list(range(len(_ctx)))
    _idx = (STRtree(_ctx), [-1] * len(_ctx)) if _ctx else None
    n_canon = n_nao = 0
    with con.cursor() as cur:
        for qid, fs in _agrupar_faces(faces).items():
            meus = por_quadra.get(qid, [])
            linhas = {f["face_idx"]: _wkt.loads(f["anel_wkt"]) for f in fs}
            quadra_pol = pol_por_quadra.get(qid)
            # cada ponto vai para a face mais próxima (até RAIO_FACE_M)
            do_ponto: dict = {}
            for p in meus:
                pt = Point(p["lng"], p["lat"])
                melhor, md = None, 1e18
                for fi, ln in linhas.items():
                    d = ln.distance(pt) * 111320.0
                    if d < md:
                        melhor, md = fi, d
                do_ponto[p["id"]] = (melhor if md <= RAIO_FACE_M else None, md)
            for f in fs:
                fi = f["face_idx"]
                ln = linhas[fi]
                # via canônica: a via do OSM cujo eixo acompanha esta face
                meio = ln.interpolate(0.5, normalized=True)
                via, dv = None, 1e18
                for v, g in vias:
                    d = g.distance(meio) * 111320.0
                    if d < dv:
                        via, dv = v, d
                prof = {0: [], 1: []}
                da_face = []
                for p in meus:
                    if do_ponto[p["id"]][0] != fi:
                        continue
                    da_face.append(p)
                    if p["numero"]:
                        prof[p["numero"] % 2].append(
                            _recuo_com_sinal(Point(p["lng"], p["lat"]), ln, quadra_pol))
                largura = 2.0 * _tolerancia(f.get("recuo_m"))
                par, por, centro = _paridade(prof, largura)
                nome, fonte = _nome_da_face(da_face, via)
                cur.execute("""UPDATE quadra_face SET via_id=%s, nome_canonico=%s,
                                      nome_osm=%s, nome_fonte=%s, dist_via_m=%s,
                                      paridade=%s, paridade_por=%s,
                                      recuo_par_m=%s, recuo_impar_m=%s
                                WHERE quadra_id=%s AND face_idx=%s""",
                            (via["id"] if via else None, nome,
                             via.get("nome_osm") if via else None, fonte,
                             round(dv, 1) if dv < 1e8 else None, par, por,
                             round(st.median(prof[0]), 1) if prof[0] else None,
                             round(st.median(prof[1]), 1) if prof[1] else None,
                             qid, fi))
                # rua de um lado só: a paridade não tem o que separar aqui
                so_um_lado = _sem_outro_lado(ln, qid, pol_por_quadra,
                                             f.get("recuo_m"), _idx)
                # classifica os pontos desta face
                for p in meus:
                    if do_ponto[p["id"]][0] != fi:
                        continue
                    rec = _recuo_com_sinal(Point(p["lng"], p["lat"]), ln, quadra_pol)
                    ok, motivo = _canonico(p, par, rec, centro, largura, nome,
                                           so_um_lado)
                    n_canon += int(ok is True)
                    n_nao += int(ok is False)
                    # aprovado só porque a rua não tem outro lado sai em tom
                    # escuro, como os demais resgates: é desta face, mas não pela
                    # regra principal. O próprio motivo diz quando foi esse o caso.
                    rg = ("via_sem_outro_lado"
                          if ok is True and so_um_lado and motivo
                          and "não tem outro lado" in motivo else None)
                    cur.execute("""UPDATE quadra_ponto SET face_idx=%s, canonico=%s,
                                          motivo=%s, resgate=coalesce(%s, resgate)
                                    WHERE id=%s""",
                                (fi, ok, motivo, rg, p["id"]))
    con.commit()
    return n_canon, n_nao, faces


def passo5_faces(sid: str, con=None) -> dict:
    """Cada face herda a via canônica mais próxima; a paridade sai do RECUO.

    Contar par × ímpar não separa os dois lados da rua: a borda da quadra corre
    pelo eixo, e quando a coordenada do IBGE fica curta o endereço de LÁ cai
    dentro do nosso polígono. Medido na q6703: 20 pares × 16 ímpares parecia
    "misto", mas os pares estavam a 6,8 m do eixo (sobre as casas desta quadra) e
    os ímpares a 1,7 m (em cima do eixo). O recuo separa; a contagem não."""
    import statistics as st
    fechar = con is None
    con = con or bc.conectar()
    try:
        QD.limpar_passo(sid, 5, con)
        n_canon, n_nao, faces = _classificar_faces(sid, con)
        con.commit()
        # ANTES das regras de resgate: um endereço que está em duas quadras tem
        # de ser resolvido primeiro, senão cada uma o "recupera" por conta própria
        dup = _resolver_duplicados(sid, con)
        if dup["duplicados_removidos"]:
            # o expurgo mudou a composição das faces — o veredito anterior é velho
            n_canon, n_nao, faces = _classificar_faces(sid, con)
        ext = _tolerancia_por_endereco(sid, con)
        n_canon += ext["recuperados"] + ext["trazidos"]
        n_nao -= ext["recuperados"]
        # por último: o que a coordenada do IBGE defende, mesmo com a numeração
        # contra — só depois que as outras regras já disseram quem é aprovado,
        # porque a régua deste resgate são justamente eles
        nv1 = _resgate_nv1(sid, con)
        # e por fim a evidência mais simples: estar dentro do quarteirão
        dq = _dentro_e_da_quadra(sid, con)
        nv1.update(dq)
        # e de novo no fim: os resgates podem ter reintroduzido o mesmo endereço
        dup2 = _resolver_duplicados(sid, con)
        dup["duplicados_removidos"] += dup2["duplicados_removidos"]
        # recontagem a partir do banco — depois de apagar linhas, somar deltas mente
        pts = QD.pontos(sid, con)
        n_canon = sum(1 for p in pts if p["canonico"] is True)
        n_nao = sum(1 for p in pts if p["canonico"] is False)
        res = {"canonicos": n_canon, "nao_canonicos": n_nao,
               "faces": len(faces), **ext, **nv1, **dup,
               "faces_com_via": sum(1 for f in QD.faces(sid, con) if f["nome_canonico"])}
        print(f"[5/5] {n_canon} pontos canônicos · {n_nao} destoam · "
              f"{res['faces_com_via']}/{len(faces)} faces com via canônica", flush=True)
        QD.marcar_passo(sid, 5, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def norm_via(s: str) -> str:
    """Compara logradouro ignorando acento, caixa, pontuação e abreviação."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    for a, b in (("avenida", "av"), ("rua", "r"), ("travessa", "tv"),
                 ("praca", "pc"), ("rodovia", "rod"), ("estrada", "estr"),
                 ("alameda", "al"), ("loteamento", "lot"), ("conjunto", "cj")):
        t = re.sub(rf"\b{a}\b", b, t)
    return re.sub(r"\s+", " ", t).strip()


def _resolver_duplicados(sid: str, con) -> dict:
    """Cada endereço pertence a UMA quadra. Resolve quem ficou em duas.

    O passo 4 percorre quadra a quadra, e a faixa de 20 m das duas quadras de uma
    mesma rua alcança os mesmos endereços — o mesmo `cod_endereco` entrava duas
    vezes. Cada linha era classificada por conta própria, então um endereço podia
    sair APROVADO nas duas (medido: 46 duplicados, 13 aprovados em dobro), e o
    mapa desenhava o ponto duas vezes com cores diferentes.

    Regra do usuário: se o ponto está no espaço de outra quadra e a numeração é da
    outra, ele é da outra — não se recupera como parte desta. A escolha, nesta
    ordem: estar DENTRO da quadra, a paridade da face BATER, e por fim a menor
    distância à face. As linhas perdedoras são apagadas."""
    quadras = {q["id"]: _wkt.loads(q["geom_real"] or q["geom_osm"])
               for q in QD.quadras(sid, con)}
    faces = {(f["quadra_id"], f["face_idx"]): f for f in QD.faces(sid, con)}
    linhas = {(f["quadra_id"], f["face_idx"]): _wkt.loads(f["anel_wkt"])
              for f in QD.faces(sid, con)}
    por_end: dict = {}
    for p in QD.pontos(sid, con):
        if p["cod_endereco"]:
            por_end.setdefault(p["cod_endereco"], []).append(p)

    def _nota(p):
        k = (p["quadra_id"], p["face_idx"])
        pt = Point(p["lng"], p["lat"])
        pol = quadras.get(p["quadra_id"])
        dentro = bool(pol and pol.contains(pt))
        f = faces.get(k)
        par_ok = bool(f and f["paridade"] and p["numero"] is not None
                      and (p["numero"] % 2 == 0) == (f["paridade"] == "par"))
        d = linhas[k].distance(pt) * 111320.0 if k in linhas else 1e9
        return (dentro, par_ok, -d)

    apagados = 0
    with con.cursor() as cur:
        for cod, ps in por_end.items():
            if len(ps) < 2:
                continue
            vencedor = max(ps, key=_nota)
            for p in ps:
                if p["id"] == vencedor["id"]:
                    continue
                cur.execute("DELETE FROM quadra_ponto WHERE id=%s", (p["id"],))
                apagados += 1
    con.commit()
    if apagados:
        print(f"      {apagados} linhas duplicadas removidas — cada endereço ficou "
              f"na quadra a que pertence", flush=True)
    return {"duplicados_removidos": apagados}


def _tolerancia_por_endereco(sid: str, con) -> dict:
    """Tolerância por IDENTIDADE do endereço, além da geometria.

    Pedido do usuário: um endereço com o MESMO logradouro, o MESMO CEP e a MESMA
    paridade da face é daquela face — mesmo que a coordenada o tenha jogado para
    o outro lado da via, ou a até 1 km dali.

    Duas frentes:
      1. RECUPERA quem já foi coletado e a geometria reprovou;
      2. TRAZ do CNEFE quem nem chegou a ser coletado (a faixa de 20 m do passo 4
         não alcança 1 km).

    O ponto entra na coordenada ORIGINAL, como todos os outros — o mapa mostra
    onde o cadastro realmente o colocou, ligado à face pela identidade e não pela
    posição. As três condições juntas são a trava: só o nome de rua já trouxe
    homônimo de outro bairro em experiências anteriores; com CEP e paridade, não.
    """
    from collections import Counter
    faces = QD.faces(sid, con)
    pontos = QD.pontos(sid, con)
    por_face: dict = {}
    for p in pontos:
        if p["face_idx"] is not None and p["quadra_id"] is not None:
            por_face.setdefault((p["quadra_id"], p["face_idx"]), []).append(p)
    s = QD.sessao(sid, con)
    ja = {(norm_via(p["logradouro"] or ""), p["numero"]) for p in pontos if p["numero"]}
    recuperados = trazidos = 0
    perfis = []

    with con.cursor() as cur:
        for f in faces:
            par = f["paridade"]
            nome = f["nome_canonico"]
            if not par or not nome:
                continue
            meus = por_face.get((f["quadra_id"], f["face_idx"]), [])
            # a identidade da face: o CEP majoritário entre quem ela já aceitou
            ceps = Counter(p["cep"] for p in meus if p["canonico"] and p["cep"])
            if not ceps:
                ceps = Counter(p["cep"] for p in meus if p["cep"])
            if not ceps:
                continue
            cep, _ = ceps.most_common(1)[0]
            alvo = norm_via(nome)
            perfis.append((f, alvo, cep, par))

            # 1) recupera o que a geometria reprovou mas a identidade confirma —
            #    desde que esteja JUNTO da face. Endereço da mesma rua e do mesmo
            #    CEP a 300 m é outro quarteirão, não um ponto desta face deslocado.
            ln_f = _wkt.loads(f["anel_wkt"])
            for p in meus:
                if p["canonico"] is not False or not p["numero"]:
                    continue
                if p["cep"] != cep or norm_via(p["logradouro"] or "") != alvo:
                    continue
                if (p["numero"] % 2 == 0) != (par == "par"):
                    continue
                d = ln_f.distance(Point(p["lng"], p["lat"])) * 111320.0
                if d > DIST_MAX_FACE_M:
                    continue
                cur.execute("""UPDATE quadra_ponto SET canonico=true, motivo=%s
                                WHERE id=%s""",
                            (f"mesmo logradouro, CEP {cep} e numeração {par} da face, "
                             f"a {d:.0f} m dela — a coordenada é que está deslocada",
                             p["id"]))
                recuperados += 1

        # 2) traz do CNEFE o que nem foi coletado — a busca vai longe, mas só
        #    entra quem estiver a até DIST_MAX_FACE_M da face
        for f, alvo, cep, par in perfis:
            ln = _wkt.loads(f["anel_wkt"])
            meio = ln.interpolate(0.5, normalized=True)
            g = RAIO_TOLERANCIA_M / 111320.0
            cur.execute("""
                SELECT cod_unico_endereco, latitude, longitude, cod_especie,
                       nom_tipo_seglogr, nom_seglogr, num_endereco, cep,
                       dsc_estabelecimento, nv_geo_coord
                  FROM ibge_cnefe
                 WHERE cod_municipio=%s AND cep=%s
                   AND latitude::double precision  BETWEEN %s AND %s
                   AND longitude::double precision BETWEEN %s AND %s
            """, (str(s["cod_municipio"]), cep,
                  meio.y - g, meio.y + g, meio.x - g, meio.x + g))
            for (cod, la, ln_, esp, tipo, via, num, cep_, estab, nvg) in cur.fetchall():
                try:
                    la, ln_ = float(la), float(ln_)
                    n = int(str(num).strip())
                except (TypeError, ValueError):
                    continue
                logr = f"{tipo or ''} {via or ''}".strip()
                if norm_via(logr) != alvo:
                    continue
                if (n % 2 == 0) != (par == "par"):
                    continue
                if (alvo, n) in ja:
                    continue
                # a distância que decide é até a FACE, não até o meio dela
                d = ln.distance(Point(ln_, la)) * 111320.0
                if d > DIST_MAX_FACE_M:
                    continue
                ja.add((alvo, n))
                cur.execute("""INSERT INTO quadra_ponto
                    (sessao_id, quadra_id, face_idx, cod_endereco, logradouro, numero,
                     cep, especie, estabelecimento, nv_geo, lat, lng, origem,
                     dist_via_m, canonico, motivo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s)""",
                    (sid, f["quadra_id"], f["face_idx"], cod, logr, n, cep_, esp,
                     (estab or "").strip() or None, (nvg or "").strip() or None,
                     la, ln_, "tolerancia_endereco", round(d, 1),
                     f"mesmo logradouro, CEP {cep} e numeração {par}, a {d:.0f} m "
                     f"da face — trazido pela identidade do endereço"))
                trazidos += 1
    con.commit()
    if recuperados or trazidos:
        print(f"      tolerância por endereço (logradouro + CEP + paridade): "
              f"{recuperados} recuperados do outro lado da via · "
              f"{trazidos} trazidos de até {RAIO_TOLERANCIA_M:.0f} m", flush=True)
    return {"recuperados": recuperados, "trazidos": trazidos}


def _dentro_e_da_quadra(sid: str, con) -> dict:
    """Quem está DENTRO da quadra real é da quadra — com confiança reduzida.

    Regra do usuário. O ponto no miolo do quarteirão não está em nenhum dos dois
    lados de nenhuma rua: a paridade não fala dele, e a distância às faces também
    não (ele fica além do raio que amarra ponto a face). Ficava em cinza, "sem
    julgamento", quando a evidência mais forte que existe já estava dada — ele
    está dentro do polígono.

    Entra pela face mais próxima, marcado como resgate: sai no tom ESCURO, que é
    como o mapa diz "é daqui, mas não pela regra principal"."""
    quadras = {q["id"]: _wkt.loads(q["geom_real"] or q["geom_osm"])
               for q in QD.quadras(sid, con)}
    linhas: dict = {}
    for f in QD.faces(sid, con):
        linhas.setdefault(f["quadra_id"], []).append((f["face_idx"],
                                                      _wkt.loads(f["anel_wkt"])))
    n = 0
    with con.cursor() as cur:
        for p in QD.pontos(sid, con):
            if p["canonico"] is True or p["quadra_id"] not in quadras:
                continue
            pt = Point(p["lng"], p["lat"])
            if not quadras[p["quadra_id"]].contains(pt):
                continue
            fi = p["face_idx"]
            if fi is None:
                fs = linhas.get(p["quadra_id"]) or []
                if fs:
                    fi = min(fs, key=lambda t: t[1].distance(pt))[0]
            cur.execute("""UPDATE quadra_ponto SET canonico=true, face_idx=%s,
                                  resgate=%s, motivo=%s WHERE id=%s""",
                        (fi, "dentro_da_quadra",
                         "está dentro do quarteirão — é dele, mas não pela regra "
                         "da face" + (f" (antes: {p['motivo']})" if p["motivo"] else ""),
                         p["id"]))
            n += 1
    con.commit()
    if n:
        print(f"      {n} pontos aprovados por estarem dentro do quarteirão "
              f"(confiança reduzida)", flush=True)
    return {"dentro_da_quadra": n}


def _resgate_nv1(sid: str, con) -> dict:
    """Resgata o reprovado que a COORDENADA defende: nível 1 do IBGE e tão perto
    da quadra real quanto os aprovados daquela face.

    A paridade reprova por numeração, e numeração o cadastro erra. Quando o
    endereço tem a coordenada mais confiável que o IBGE emite (nv_geo_coord = 1,
    capturada no próprio endereço) E está tão junto do quarteirão quanto a metade
    mais próxima dos aprovados, a geometria está dizendo que ele é daqui — mesmo
    que o número diga o contrário.

    A régua é a MEDIANA da distância dos aprovados à quadra real: passar dela
    significa estar entre os 50% mais próximos. Com poucos aprovados a mediana
    não tem sentido, e aí basta não estar mais longe que o pior deles.

    O resgatado fica marcado (`resgate`) e sai num tom mais escuro no mapa — é
    aprovação por outra evidência, e isso tem que ficar visível."""
    import statistics as st
    quadras = {q["id"]: q for q in QD.quadras(sid, con)}
    pontos = QD.pontos(sid, con)
    por_face: dict = {}
    for p in pontos:
        if p["face_idx"] is not None and p["quadra_id"] is not None:
            por_face.setdefault((p["quadra_id"], p["face_idx"]), []).append(p)

    def _dist(p, pol):
        return pol.distance(Point(p["lng"], p["lat"])) * 111320.0

    resgatados = 0
    with con.cursor() as cur:
        for (qid, fi), ms in por_face.items():
            q = quadras.get(qid)
            if not q:
                continue
            pol = _wkt.loads(q["geom_real"] or q["geom_osm"])
            # A régua é de PROXIMIDADE, então só entram nela os aprovados que
            # estavam mesmo por perto. Quem veio pela tolerância por endereço foi
            # aprovado por IDENTIDADE, de até 1 km — incluí-los levou a mediana de
            # uma face a 279 m, e aí a regra deixaria passar qualquer coisa.
            aprov = [p for p in ms if p["canonico"] is True
                     and p["origem"] != "tolerancia_endereco"]
            if not aprov:
                continue                      # sem referência não há como comparar
            ds = sorted(_dist(p, pol) for p in aprov)
            if len(ds) >= MIN_APROVADOS_MEDIANA:
                limite, criterio = st.median(ds), "mediana"
            else:
                limite, criterio = ds[-1], "o mais distante"
            for p in ms:
                if p["canonico"] is not False or (p["nv_geo"] or "").strip() != "1":
                    continue
                d = _dist(p, pol)
                if d > limite:
                    continue
                cur.execute("""UPDATE quadra_ponto SET canonico=true, resgate=%s,
                                      motivo=%s WHERE id=%s""",
                            ("nv1_proximidade",
                             f"coordenada nível 1 do IBGE e a {d:.1f} m da quadra real "
                             f"— tão perto quanto os aprovados desta face "
                             f"({criterio}: {limite:.1f} m). Reprovado antes por: "
                             f"{p['motivo'] or 'paridade'}", p["id"]))
                resgatados += 1
    con.commit()
    if resgatados:
        print(f"      resgate por coordenada nível 1: {resgatados} pontos "
              f"aprovados pela proximidade", flush=True)
    return {"resgatados_nv1": resgatados}


def _agrupar_faces(faces):
    d = {}
    for f in faces:
        d.setdefault(f["quadra_id"], []).append(f)
    return d


def _nome_da_face(pontos: list[dict], via: dict | None):
    """O nome da face é o do LOGRADOURO MAJORITÁRIO dos endereços que caem nela.

    Quem sabe o nome da rua são os itens que moram nela — não o traçado do OSM,
    que abrevia e erra, nem uma consulta de rede. Exige maioria de verdade: com
    empate (1 a 1) não se elege nada, senão o desempate vira ordem de dicionário.

    A leitura no Maps continua existindo (`via_osm.nome_canonico`) e passa na
    frente quando houver — mas está desligada por padrão, porque custava mais
    tempo que todo o resto do processo somado."""
    from collections import Counter
    if via and via.get("nome_canonico"):
        return via["nome_canonico"], "Maps (nome canônico da via)"
    c = Counter((p.get("logradouro") or "").strip()
                for p in pontos if (p.get("logradouro") or "").strip())
    if c:
        (nome, n), *resto = c.most_common()
        folga = n - (resto[0][1] if resto else 0)
        if n >= MIN_VOTOS_NOME and folga >= 1:
            return nome, f"maioria dos endereços ({n} de {sum(c.values())})"
    if via and via.get("nome_osm"):
        return via["nome_osm"], "traçado do OSM (sem maioria nos endereços)"
    return None, "indefinido"


def _recuo_com_sinal(p: Point, ln: LineString, quadra) -> float:
    """Recuo do ponto ao eixo da face, POSITIVO para dentro do quarteirão.

    Distância absoluta não distingue os lados: um ponto 8 m para dentro e outro
    8 m para fora medem igual, e a face acabava elegendo a paridade do lado
    ERRADO — os endereços da quadra vizinha entravam e os desta ficavam
    vermelhos. O sinal responde a pergunta que interessa: de que lado da via
    este endereço está?"""
    d = ln.distance(p) * 111320.0
    dentro = quadra is not None and quadra.contains(p)
    return d if dentro else -d


def _tolerancia(recuo_face) -> float:
    """Meia caixa viária da face (o recuo que o passo 3 aplicou nela)."""
    return max(float(recuo_face or 0.0), TOLERANCIA_LADO_MIN_M)


def _paridade(prof: dict, largura: float):
    """Par ou ímpar desta face: ganha o grupo que estiver MAIS PARA DENTRO.

    Comparação RELATIVA, nunca corte no zero. O CNEFE desloca blocos inteiros de
    endereços: na Rua Antonieta Cabral ele os põe em cima do eixo (ímpares de
    −0,4 a +5,9 m); na Travessa Antonieta Cabral joga tudo 5 m para fora (pares a
    −4,8 m, ímpares a −15,3 m). Nos dois casos a diferença ENTRE os grupos é
    limpa — é o zero que não serve de referência.

    Devolve também o CENTRO do lado vencedor: é a linha das portas desta face, e
    é contra ela que os pontos são classificados, não contra o eixo.

    Sem lado só quando o vencedor está além de UMA CAIXA VIÁRIA inteira para
    fora — aí ele cruzou a rua toda e é do quarteirão de lá."""
    import statistics as st
    n_par, n_imp = len(prof[0]), len(prof[1])
    if not (n_par or n_imp):
        return None, "sem número nesta face", None
    m_par = st.median(prof[0]) if prof[0] else None
    m_imp = st.median(prof[1]) if prof[1] else None
    if n_par >= 2 and n_imp >= 2 and abs(m_par - m_imp) >= LIMIAR_PROFUNDIDADE_M:
        v, fundo, raso = (("par", m_par, m_imp) if m_par > m_imp
                          else ("impar", m_imp, m_par))
        if fundo < -largura:
            return None, (f"os dois lados caem além da caixa viária "
                          f"(par {m_par:+.1f} m, ímpar {m_imp:+.1f} m) — "
                          f"esta face é do quarteirão vizinho"), None
        return v, (f"{v} está {fundo:+.1f} m do eixo e o outro {raso:+.1f} m — "
                   f"o mais para dentro é o lado desta quadra"), fundo
    # um só lado tem números: ele decide, se não cruzou a rua inteira
    tot = n_par + n_imp
    if tot >= 2 and max(n_par, n_imp) / tot >= 0.60:
        v = "par" if n_par > n_imp else "impar"
        med = m_par if v == "par" else m_imp
        if med is not None and med < -largura:
            return None, (f"só há numeração {v}, e a {med:+.1f} m — além da caixa "
                          f"viária, é do outro quarteirão"), None
        return (v, f"{max(n_par, n_imp)} de {tot} endereços da face são {v} "
                   f"({med:+.1f} m)", med)
    return None, f"só {tot} endereço(s) com número — insuficiente para dizer o lado", None


def _sem_outro_lado(ln: LineString, qid, pol_por_quadra: dict, recuo_m: float,
                    indice=None) -> bool:
    """Esta face é de uma rua que só tem UM lado edificável?

    A paridade existe para separar os dois lados de uma rua. Onde não há outro
    lado — a rua margeia o fim do bairro, um rio, a zona rural — ela não tem o
    que separar: par e ímpar caem todos na única face que existe, e reprovar
    metade deles por "numeração destoa" é aplicar uma régua que não vale ali.

    O teste: anda perpendicular à face, para fora da própria quadra, um pouco
    além da caixa da rua. Se não cai dentro de NENHUMA outra quadra DO MUNICÍPIO,
    não há outro lado. Em Itambé, 563 das 2.260 faces (25%).

    O contexto é o município e não a sessão: quem desenha em cima de um único
    quarteirão não tem outra quadra para comparar, e TODA face passaria por "um
    lado só" — nada seria reprovado e o quarteirão vizinho entraria inteiro."""
    if ln.length <= 0:
        return False
    mid = ln.interpolate(0.5, normalized=True)
    a = ln.interpolate(max(0.0, 0.45), normalized=True)
    b = ln.interpolate(min(1.0, 0.55), normalized=True)
    dx, dy = b.x - a.x, b.y - a.y
    n = math.hypot(dx, dy)
    if not n:
        return False
    px, py = -dy / n, dx / n
    passo = (float(recuo_m or LARGURA_PADRAO_M / 2.0) + 16.0) / 111320.0
    propria = pol_por_quadra.get(qid)
    # o próprio quarteirão também está no contexto do município, com geometria
    # ligeiramente diferente da da sessão: sem excluí-lo, ele responderia como
    # "o outro lado de si mesmo"
    centro = propria.representative_point() if propria is not None else None
    for s in (1, -1):
        t = Point(mid.x + px * passo * s, mid.y + py * passo * s)
        if propria is not None and propria.contains(t):
            continue                       # esse lado é o miolo da própria quadra
        if indice is not None:
            arv, ids = indice
            for i in arv.query(t):
                g = arv.geometries[i]
                if ids[i] == qid or not g.contains(t):
                    continue
                if centro is not None and g.contains(centro):
                    continue               # é a própria quadra, vista pela base
                return False
        else:
            for oid, pol in pol_por_quadra.items():
                if oid != qid and pol.contains(t):
                    return False
    return True


def _canonico(p, paridade, recuo: float, centro: float | None, largura: float,
              nome_face: str | None = None, sem_outro_lado: bool = False):
    """None = não dá para julgar; True = é desta face; False = não é.

    A referência é o CENTRO do lado da face (a linha das portas dela), não o
    eixo da via: o CNEFE desloca blocos inteiros, e comparar com o zero reprovava
    faces cujos endereços estão todos 5 m para fora. Fica do outro lado quem
    está mais de MEIA CAIXA VIÁRIA aquém desse centro — atravessou a rua.

    Não há limite para o lado de dentro: endereço no fundo do lote é desta face.

    **Sem número, quem julga é o LOGRADOURO.** A paridade só existe para separar
    os dois lados de uma rua; se o endereço não tem número ela não tem o que
    dizer — mas o nome da via tem. Endereço sem número cujo logradouro é o da
    face e que está do lado dela é daquela face. Eram 24% dos pontos da sessão
    (107 de 446) parados em cinza por falta de um dado que nunca vai chegar: na
    Praça Getúlio Vargas, 27 dos 41. O mesmo vale quando a FACE é que não tem
    paridade definida."""
    ref = centro if centro is not None else 0.0
    limite = ref - max(largura / 2.0, TOLERANCIA_LADO_MIN_M)
    # Numa rua de um lado só NÃO EXISTE "o outro lado": não há face para onde
    # mandar quem está aquém do centro, e reprová-lo o deixa órfão. Vale para a
    # geometria tanto quanto para a paridade — as duas regras só sabem escolher
    # ENTRE dois lados. O alcance segue limitado pela coleta (faixa de 20 m) e
    # pela distância à face (RAIO_FACE_M).
    if recuo < limite and not sem_outro_lado:
        return False, (f"está do outro lado da via ({recuo:+.0f} m, contra "
                       f"{ref:+.0f} m das portas desta face)")
    # deixou passar por não haver outro lado: o motivo tem de dizer isso
    so_lado = (" · esta rua não tem outro lado, então o ponto é desta face"
               if recuo < limite else "")
    mesmo_logr = bool(nome_face and p.get("logradouro")
                      and norm_via(p["logradouro"]) == norm_via(nome_face))
    if not p.get("numero"):
        if mesmo_logr:
            return True, ("sem número, mas o logradouro é o desta face e o ponto "
                          "está do lado dela" + so_lado)
        return None, "sem número e logradouro diferente do da face"
    if not paridade:
        if mesmo_logr:
            return True, ("face sem paridade definida, mas o logradouro é o dela "
                          "e o ponto está do lado dela" + so_lado)
        return None, "face sem paridade definida"
    ok = (p["numero"] % 2 == 0) == (paridade == "par")
    if ok:
        return True, (so_lado.lstrip(" ·").strip() or None)
    if sem_outro_lado:
        # não existe outro lado para onde este número possa ir: a face é a única
        # que a rua tem, e a paridade não tem o que separar
        return True, ("numeração destoa, mas esta rua não tem outro lado — "
                      "a face é a única que existe")
    return False, f"numeração destoa da face ({paridade})"


# ════════════════════════════════════════════════════════════════════════════
# PASSO 6 — alinhar os pontos na face real
# ════════════════════════════════════════════════════════════════════════════
def alinhar_vias_abertas(sid: str, con=None) -> dict:
    """Distribui na PRÓPRIA rua os endereços de via que não fecha quadra.

    Beco, rua projetada e acesso de engenho não delimitam quarteirão, então não
    há testada de quadra para eles. Mas a rua existe, tem eixo e tem dois lados —
    e é nela que esses endereços moram. Antes eles ou eram projetados na face da
    quadra VIZINHA (50 a 164 m de arrasto) ou ficavam na coordenada crua, soltos:
    1.089 pontos espalhados sem organização nenhuma.

    Aqui a via vira o trilho: recuada de meia caixa para o lado em que o ponto
    está (dois trilhos por via, um por lado), com a MESMA régua do passo 6 —
    sentido pelo Theil–Sen dos próprios números, espaçamento proporcional ao
    NÚMERO e teto de `DESLOC_MAX_M`. Quem passa do teto ou não tem número vai
    para o pé da perpendicular NESSE trilho, não no da quadra vizinha.

    O lado sai do produto vetorial entre a direção da via e o vetor até o ponto:
    positivo à esquerda do traçado, negativo à direita. Não importa qual é qual —
    importa que os dois grupos não se misturem, senão a régua ordenaria juntos os
    números dos dois lados da rua."""
    from shapely import STRtree
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, geom_wkt, tipo, fecha_quadra FROM via_osm
                            WHERE sessao_id=%s AND fecha_quadra IS NOT NULL""", (sid,))
            vias = cur.fetchall()
        if not vias:
            return {"alinhados_via_aberta": 0}
        pts = [p for p in QD.pontos(sid, con) if p["canonico"]]
        if not pts:
            return {"alinhados_via_aberta": 0}

        lat0, lng0 = pts[0]["lat"], pts[0]["lng"]
        my, mx = _metros(lat0)

        def para_m(g):
            return LineString([((x - lng0) * mx, (y - lat0) * my) for x, y in g.coords])

        geos = [para_m(_wkt.loads(g)) for _, g, _, _ in vias]
        arv = STRtree(geos)
        aberta = {i: (vias[i][0], vias[i][2]) for i in range(len(vias)) if not vias[i][3]}

        # cada ponto para a via mais próxima; só nos interessam as abertas
        grupos: dict = {}
        for p in pts:
            q = Point((p["lng"] - lng0) * mx, (p["lat"] - lat0) * my)
            i = arv.nearest(q)
            if i not in aberta:
                continue                       # a via dele fecha quadra: passo 6
            ln = geos[i]
            s = ln.project(q)
            a = ln.interpolate(max(0.0, s - 0.5))
            b = ln.interpolate(min(ln.length, s + 0.5))
            # sinal do produto vetorial: de que lado do traçado o ponto está
            cruz = ((b.x - a.x) * (q.y - a.y) - (b.y - a.y) * (q.x - a.x))
            grupos.setdefault((i, cruz >= 0), []).append(p)

        n_reg = n_perp = n_longe = 0
        with con.cursor() as cur:
            for (i, esq), ps in grupos.items():
                ln = geos[i]
                meia = LARGURA_VIA_M.get(aberta[i][1], LARGURA_PADRAO_M) / 2.0
                try:
                    trilho = ln.offset_curve(meia if esq else -meia)
                except Exception:
                    trilho = ln
                if trilho.is_empty or trilho.length <= 0:
                    trilho = ln
                if trilho.geom_type != "LineString":       # offset pode partir
                    trilho = max(trilho.geoms, key=lambda g: g.length)

                def grava(p, alvo, modo, por):
                    # de volta a graus
                    la, lg = alvo.y / my + lat0, alvo.x / mx + lng0
                    d = _dist_m(p["lat"], p["lng"], la, lg)
                    # Teto também na perpendicular. Ela é o "movimento mínimo",
                    # mas mínimo de 105 m é teletransporte: o ponto simplesmente
                    # não é dessa rua. `FAIXA_PONTOS_M` é a própria faixa com que
                    # a coleta o trouxe — além dela não há vínculo com a via.
                    if d > FAIXA_PONTOS_M:
                        cur.execute("""UPDATE quadra_ponto
                                          SET lat_alinhado=lat, lng_alinhado=lng,
                                              desloc_m=0, ordem_face=NULL,
                                              alinhado_modo='preservado',
                                              alinhado_por=%s WHERE id=%s""",
                                    (f"a rua mais próxima não fecha quadra e está a "
                                     f"{d:.0f} m — longe demais para ser dele; "
                                     f"mantido na coordenada original", p["id"]))
                        return None
                    cur.execute("""UPDATE quadra_ponto SET lat_alinhado=%s,
                                          lng_alinhado=%s, alinhado_modo=%s,
                                          alinhado_por=%s, desloc_m=%s WHERE id=%s""",
                                (la, lg, modo, por, round(d, 1), p["id"]))
                    return d

                ms = [p for p in ps if p["numero"]]
                if len(ms) < 2:
                    for p in ps:
                        q = Point((p["lng"] - lng0) * mx, (p["lat"] - lat0) * my)
                        if grava(p, trilho.interpolate(trilho.project(q)),
                                 "via_aberta_perp",
                                 "perpendicular à própria rua (que não fecha quadra) "
                                 "— sem duas âncoras numeradas para haver régua") is None:
                            n_longe += 1
                        else:
                            n_perp += 1
                    continue

                comp = trilho.length or 1
                proj = {p["id"]: trilho.project(
                    Point((p["lng"] - lng0) * mx, (p["lat"] - lat0) * my)) / comp
                    for p in ms}
                cresce = _sentido(ms, proj)
                nums = [p["numero"] for p in ms]
                n0, n1 = min(nums), max(nums)
                span = (n1 - n0) or 1
                for p in ps:
                    q = Point((p["lng"] - lng0) * mx, (p["lat"] - lat0) * my)
                    if not p["numero"]:
                        if grava(p, trilho.interpolate(trilho.project(q)),
                                 "via_aberta_perp",
                                 "perpendicular à própria rua (que não fecha quadra) "
                                 "— sem número, fora da régua") is None:
                            n_longe += 1
                        else:
                            n_perp += 1
                        continue
                    frac = (p["numero"] - n0) / span
                    if cresce < 0:
                        frac = 1.0 - frac
                    alvo = trilho.interpolate(frac, normalized=True)
                    la, lg = alvo.y / my + lat0, alvo.x / mx + lng0
                    if _dist_m(p["lat"], p["lng"], la, lg) > DESLOC_MAX_M:
                        if grava(p, trilho.interpolate(trilho.project(q)),
                                 "via_aberta_perp",
                                 f"perpendicular à própria rua (que não fecha "
                                 f"quadra) — a régua o levaria além do teto de "
                                 f"{DESLOC_MAX_M:.0f} m") is None:
                            n_longe += 1
                        else:
                            n_perp += 1
                        continue
                    if grava(p, alvo, "via_aberta",
                             f"nº {n0}–{n1} distribuídos na PRÓPRIA rua, que não fecha "
                             f"quadra ({'crescente' if cresce > 0 else 'decrescente'} "
                             f"no traçado), espaçamento proporcional ao número") is None:
                        n_longe += 1
                    else:
                        n_reg += 1
        con.commit()
        print(f"      {n_reg + n_perp} pontos alinhados na PRÓPRIA rua que não fecha "
              f"quadra ({n_reg} pela régua, {n_perp} na perpendicular)"
              + (f" · {n_longe} longe demais da rua, mantidos onde estavam"
                 if n_longe else ""), flush=True)
        return {"alinhados_via_aberta": n_reg, "perp_via_aberta": n_perp,
                "longe_da_via_aberta": n_longe}
    finally:
        if fechar:
            con.close()


def preservar_no_lugar(sid: str, con=None) -> dict:
    """Quem já está num lugar construído e não pertence àquela testada FICA.

    Havendo TELHADO a menos de `RAIO_TELHADO_VIA_ABERTA_M` do ponto, a coordenada
    do CNEFE aponta para uma construção real — vale mais que qualquer projeção. O
    telhado é a condição de entrada; a partir dele, duas situações mandam manter:

    1. **A via mais próxima não fecha quadra.** Beco, rua projetada e acesso de
       engenho não delimitam quarteirão, e quem mora neles não tem testada para
       onde ir. O passo 6 os projetava na face da quadra VIZINHA — em Itambé, de
       50 a 164 m, cruzando quarteirão inteiro.

    2. **O nome do endereço não confirma a face.** Se o ponto diz "RUA TIMBAÚBA"
       e a face para onde ele iria tem outro nome — ou não tem nome nenhum,
       porque nenhum endereço do CNEFE caiu nela —, não há o que sustente a
       mudança. O nome do próprio endereço é a evidência mais forte de onde ele
       fica, e nenhuma projeção deve passar por cima dela. Medido em Itambé: com
       nome batendo, o deslocamento médio é 5,8 m; com nome diferente, 9,2 m; com
       face sem nome, 18,0 m — a discordância de nome PREVÊ o disparate.

    Sem telhado por perto não existe evidência nenhuma, e o ponto segue com o
    tratamento normal: melhor uma projeção do que uma coordenada solta no nada.

    Depende dos telhados (passo 7), então roda no fim do 6 (se já houver) e de
    novo no fim do 8. É idempotente: rodar duas vezes dá o mesmo resultado."""
    from shapely import STRtree
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT geom_wkt, fecha_quadra FROM via_osm
                            WHERE sessao_id=%s AND fecha_quadra IS NOT NULL""", (sid,))
            vias = cur.fetchall()
            cur.execute("SELECT lat, lng FROM quadra_telhado WHERE sessao_id=%s", (sid,))
            tel = cur.fetchall()
            cur.execute("""SELECT p.id, p.lat, p.lng, p.logradouro,
                                  coalesce(f.nome_canonico, f.nome_osm)
                             FROM quadra_ponto p
                             LEFT JOIN quadra_face f
                               ON f.quadra_id=p.quadra_id AND f.face_idx=p.face_idx
                            WHERE p.sessao_id=%s AND p.lat_alinhado IS NOT NULL""",
                        (sid,))
            pts = cur.fetchall()
        if not tel or not pts:
            falta = "sem telhados — rode o passo 7" if not tel else "sem pontos alinhados"
            print(f"      preservação: não se aplica ({falta})", flush=True)
            return {"preservados": 0, "motivo": falta}

        # tudo em metros num plano local: comparar distância em grau mente, porque
        # um grau de longitude vale menos que um de latitude fora do equador
        lat0, lng0 = pts[0][1], pts[0][2]
        my, mx = _metros(lat0)

        def em_m(w):
            return LineString([((x - lng0) * mx, (y - lat0) * my)
                               for x, y in _wkt.loads(w).coords])

        g_ab = [em_m(v) for v, f in vias if not f]
        g_fe = [em_m(v) for v, f in vias if f]
        t_ab = STRtree(g_ab) if g_ab else None
        t_fe = STRtree(g_fe) if g_fe else None
        t_tel = STRtree([Point((ln - lng0) * mx, (la - lat0) * my) for la, ln in tel])

        n = n_via = n_nome = 0
        with con.cursor() as cur:
            for pid, la, ln, logr, nome_face in pts:
                p = Point((ln - lng0) * mx, (la - lat0) * my)
                if not len(t_tel.query(p.buffer(RAIO_TELHADO_VIA_ABERTA_M))):
                    continue                      # sem telhado perto, sem evidência
                via_aberta = False
                if t_ab is not None:
                    d_ab = t_ab.geometries[t_ab.nearest(p)].distance(p)
                    d_fe = (t_fe.geometries[t_fe.nearest(p)].distance(p)
                            if t_fe is not None else float("inf"))
                    via_aberta = d_ab < d_fe
                if via_aberta:
                    # NÃO fica mais na coordenada crua: `alinhar_vias_abertas`
                    # o distribui na PRÓPRIA rua, que é onde ele mora. Deixá-lo
                    # solto era o espalhamento que aparecia no mapa; arrastá-lo
                    # para a face vizinha era pior ainda. Esta regra sai de cena.
                    continue
                nome_confirma = bool(nome_face and logr
                                     and norm_via(logr) == norm_via(nome_face))
                if nome_confirma:
                    continue                      # é a face dele mesmo: régua vale
                por = ("a face não tem nome identificado" if not nome_face else
                       f"o endereço diz '{logr}' e a face é '{nome_face}'")
                cur.execute("""UPDATE quadra_ponto
                                  SET lat_alinhado=lat, lng_alinhado=lng, desloc_m=0,
                                      ordem_face=NULL, alinhado_modo='preservado',
                                      alinhado_por=%s
                                WHERE id=%s""",
                            (f"{por}, com telhado a menos de "
                             f"{RAIO_TELHADO_VIA_ABERTA_M:.0f} m — mantido na "
                             f"coordenada original do CNEFE", pid))
                n += 1
                n_nome += 1
        con.commit()
        print(f"      {n} pontos MANTIDOS na coordenada original "
              f"(o nome do endereço não confirma a face)", flush=True)
        return {"preservados": n}
    finally:
        if fechar:
            con.close()


# nome antigo, de quando a regra só olhava a via
preservar_vias_abertas = preservar_no_lugar


def passo6_alinhar(sid: str, con=None) -> dict:
    """Distribui os pontos de cada face ao longo da BORDA REAL dela.

    Três decisões, nesta ordem:

    1. **Onde a face começa e termina.** A borda real é a linha da face recuada
       de meia caixa viária para dentro (o mesmo recuo que o passo 3 aplicou) —
       é ali que os lotes começam. Ela é o trilho.

    2. **Para que lado a numeração cresce.** Sai dos próprios pontos: projeta
       cada um no trilho e compara a ordem das projeções com a ordem dos números
       (soma dos sinais das inclinações par a par). Positivo, a numeração cresce
       no sentido do traçado; negativo, contra. Não se supõe sentido nenhum.

    3. **O espaçamento é proporcional ao NÚMERO, não à contagem.** Em 10, 12, 26,
       28, 30 o vão de 12→26 vale sete vezes o de 10→12: entre eles faltam
       endereços que o cadastro não tem, e espaçar por igual fingiria que a rua é
       contínua. A posição é `t = (n - n_min) / (n_max - n_min)`, ancorada nas
       duas pontas da face.

    A coordenada ORIGINAL nunca é tocada: o alinhado vai em colunas próprias, e
    o mapa mostra os dois ligados, para a correção ser sempre auditável."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        QD.limpar_passo(sid, 6, con)
        quadras = {q["id"]: q for q in QD.quadras(sid, con)}
        pts = QD.pontos(sid, con)
        por_face: dict = {}          # aprovados COM número: entram na régua
        todos_da_face: dict = {}     # todos os aprovados: vão ao menos à testada
        for p in pts:
            if p["canonico"] and p["face_idx"] is not None:
                k = (p["quadra_id"], p["face_idx"])
                todos_da_face.setdefault(k, []).append(p)
                if p["numero"]:
                    por_face.setdefault(k, []).append(p)

        # as faces de cada quadra, para saber quem é vizinha de quem no anel
        todas = QD.faces(sid, con)
        por_quadra_f: dict = {}
        for x in todas:
            por_quadra_f.setdefault(x["quadra_id"], []).append(x)
        n_alin = n_faces = n_recusa = n_perp_sem_regua = 0
        with con.cursor() as cur:
            for f in todas:
                faces_da_quadra = por_quadra_f[f["quadra_id"]]
                k = (f["quadra_id"], f["face_idx"])
                ms = por_face.get(k) or []
                q = quadras.get(f["quadra_id"])
                if not q:
                    continue
                trilho = _face_real(_wkt.loads(f["anel_wkt"]), f["recuo_m"],
                                    _wkt.loads(q["geom_osm"]).centroid)
                # o canto é da rua transversal, não desta testada: encurta cada
                # ponta pela meia caixa da face VIZINHA, senão o primeiro e o
                # último endereço caem na esquina, em cima da face seguinte
                trilho = _encurtar(trilho, *_recuos_vizinhos(f, faces_da_quadra))
                cur.execute("""UPDATE quadra_face SET anel_real_wkt=%s
                                WHERE quadra_id=%s AND face_idx=%s""",
                            (trilho.wkt, f["quadra_id"], f["face_idx"]))
                # TODO aprovado desta face vai para a testada, mesmo os que a
                # régua não alcança: sem número (76 na sessão de Itambé) ou numa
                # face com menos de duas âncoras. Eles não entram na ordem da
                # numeração, mas ficar na coordenada crua os deixava fora da face.
                for p in todos_da_face.get(k, []):
                    if p["numero"] and len(ms) >= 2:
                        continue                     # a régua cuida deste
                    pt = Point(p["lng"], p["lat"])
                    pe = trilho.interpolate(trilho.project(pt))
                    dp = _dist_m(p["lat"], p["lng"], pe.y, pe.x)
                    cur.execute("""UPDATE quadra_ponto SET lat_alinhado=%s,
                                          lng_alinhado=%s, ordem_face=NULL,
                                          alinhado_modo='perpendicular',
                                          alinhado_por=%s, desloc_m=%s WHERE id=%s""",
                                (pe.y, pe.x,
                                 f"perpendicular à face ({dp:.0f} m) — "
                                 + ("sem número, fora da régua da numeração"
                                    if not p["numero"] else
                                    "a face não tem duas âncoras numeradas"),
                                 round(dp, 1), p["id"]))
                    n_perp_sem_regua += 1
                if len(ms) < 2:
                    continue                         # sem duas âncoras não há reta
                comp = trilho.length
                proj = {p["id"]: trilho.project(Point(p["lng"], p["lat"])) / (comp or 1)
                        for p in ms}
                cresce = _sentido(ms, proj)
                nums = [p["numero"] for p in ms]
                n0, n1 = min(nums), max(nums)
                span = (n1 - n0) or 1
                ordenados = sorted(ms, key=lambda p: p["numero"] if cresce > 0
                                   else -p["numero"])
                for i, p in enumerate(ordenados):
                    frac = (p["numero"] - n0) / span
                    if cresce < 0:
                        frac = 1.0 - frac
                    alvo = trilho.interpolate(frac, normalized=True)
                    d = _dist_m(p["lat"], p["lng"], alvo.y, alvo.x)
                    if d > DESLOC_MAX_M:
                        # A régua o mandaria longe demais. Ele não fica na
                        # coordenada crua: vai para o PÉ DA PERPENDICULAR na
                        # testada — o movimento mínimo, que o põe na frente do
                        # lote sem reordená-lo pela numeração. Continua marcado,
                        # porque ali as duas fontes discordam.
                        pt = Point(p["lng"], p["lat"])
                        pe = trilho.interpolate(trilho.project(pt))
                        dp = _dist_m(p["lat"], p["lng"], pe.y, pe.x)
                        cur.execute("""UPDATE quadra_ponto SET lat_alinhado=%s,
                                              lng_alinhado=%s, ordem_face=NULL,
                                              alinhado_modo='perpendicular',
                                              alinhado_por=%s, desloc_m=%s
                                        WHERE id=%s""",
                                    (pe.y, pe.x,
                                     f"perpendicular à face ({dp:.0f} m) — a régua "
                                     f"o levaria {d:.0f} m, acima do teto de "
                                     f"{DESLOC_MAX_M:.0f} m: aqui a numeração e a "
                                     f"coordenada discordam",
                                     round(dp, 1), p["id"]))
                        n_recusa += 1
                        continue
                    cur.execute("""UPDATE quadra_ponto SET lat_alinhado=%s,
                                          lng_alinhado=%s, ordem_face=%s,
                                          alinhado_modo='regua',
                                          alinhado_por=%s, desloc_m=%s WHERE id=%s""",
                                (alvo.y, alvo.x, i,
                                 f"nº {n0}–{n1} distribuídos na borda real "
                                 f"({'crescente' if cresce > 0 else 'decrescente'} "
                                 f"no traçado), espaçamento proporcional ao número",
                                 round(d, 1), p["id"]))
                    n_alin += 1
                n_faces += 1
        con.commit()
        if n_recusa:
            print(f"      {n_recusa} pontos na PERPENDICULAR — a régua os levaria "
                  f"a mais de {DESLOC_MAX_M:.0f} m", flush=True)
        if n_perp_sem_regua:
            print(f"      {n_perp_sem_regua} pontos na PERPENDICULAR — sem número "
                  f"ou face sem duas âncoras", flush=True)
        # quem mora em rua que não fecha quadra é distribuído NA PRÓPRIA RUA, e
        # não na testada da quadra vizinha. Roda DEPOIS da régua das faces,
        # porque sobrescreve o que ela tiver feito com esses pontos.
        va = alinhar_vias_abertas(sid, con)
        gr = _agrupar_mesmo_endereco(sid, con)
        # depende dos telhados: só age se o passo 7 já rodou nesta sessão. Numa
        # corrida 1→8 quem aplica é o passo 8, no fim.
        pv = preservar_no_lugar(sid, con)
        res = {"alinhados": n_alin, "faces_alinhadas": n_faces,
               "perpendiculares": n_recusa + n_perp_sem_regua,
               "preservados": pv.get("preservados", 0), **va, **gr}
        print(f"[6/6] {n_alin} pontos alinhados na borda real de {n_faces} faces",
              flush=True)
        QD.marcar_passo(sid, 6, res, con=con)
        return res
    finally:
        if fechar:
            con.close()


def _recuos_vizinhos(f: dict, faces: list[dict]) -> tuple:
    """Meia caixa viária das faces ANTERIOR e SEGUINTE no anel da quadra.

    O anel é cíclico e `face_idx` segue a ordem do contorno, então a vizinha de
    quem começa é a última."""
    ordem = sorted(faces, key=lambda x: x["face_idx"])
    n = len(ordem)
    i = next((k for k, x in enumerate(ordem) if x["face_idx"] == f["face_idx"]), 0)
    def r(x):
        return ((float(x.get("recuo_m") or 0.0) or LARGURA_PADRAO_M / 2.0)
                + MARGEM_ESQUINA_M)
    return (r(ordem[(i - 1) % n]), r(ordem[(i + 1) % n])) if n > 1 else (0.0, 0.0)


def _encurtar(ln: LineString, ini_m: float, fim_m: float) -> LineString:
    """Corta `ini_m` do começo e `fim_m` do fim da linha, em metros.

    Sem isso o primeiro e o último endereço de cada face pousam na ESQUINA, que
    é a caixa da rua transversal — e visualmente caem em cima da face vizinha.
    Se o corte comeria a linha inteira (face curta de canto), reduz para 20% de
    cada ponta e mantém a face."""
    if ln.length <= 0:
        return ln
    my, mx = _metros(ln.coords[0][1])
    esc = math.hypot(mx, my) / math.sqrt(2)          # graus → metros, aproximado
    comp_m = _comprimento_m(ln)
    if comp_m <= 0:
        return ln
    a, b = ini_m, fim_m
    if a + b >= 0.8 * comp_m:                        # sobraria quase nada
        a = b = 0.2 * comp_m
    t0, t1 = a / comp_m, 1.0 - b / comp_m
    if t1 <= t0:
        return ln
    p0 = ln.interpolate(t0, normalized=True)
    p1 = ln.interpolate(t1, normalized=True)
    meio = [c for c in ln.coords
            if t0 < ln.project(Point(c), normalized=True) < t1]
    return LineString([(p0.x, p0.y)] + meio + [(p1.x, p1.y)])


def _agrupar_mesmo_endereco(sid: str, con) -> dict:
    """Mesmo logradouro + mesmo número = UMA porta. Todos vão para o mesmo lugar.

    O CNEFE registra vários domicílios no mesmo endereço (o prédio, a vila, os
    fundos) e espalha as coordenadas em volta. Cada um sozinho parece um endereço
    diferente; juntos são uma porta só.

    O destino é o MEDOIDE — o membro cuja posição minimiza a soma das distâncias
    aos outros, ou seja, o mais perto da maioria deles. Medoide e não centroide
    porque o centroide inventa um ponto onde talvez não haja nada; o medoide é uma
    coordenada que o cadastro de fato registrou.

    No mapa o grupo vira um marcador só, maior conforme o tamanho."""
    from collections import defaultdict
    grupos = defaultdict(list)
    for p in QD.pontos(sid, con):
        if p["canonico"] and p["numero"] and p["logradouro"]:
            grupos[f"{norm_via(p['logradouro'])}|{p['numero']}"].append(p)
    n_grupos = n_pts = 0
    with con.cursor() as cur:
        for gid, ms in grupos.items():
            if len(ms) < 2:
                continue
            medoide = min(ms, key=lambda a: sum(
                _dist_m(a["lat"], a["lng"], b["lat"], b["lng"]) for b in ms))
            for p in ms:
                cur.execute("""UPDATE quadra_ponto SET grupo_id=%s, grupo_n=%s,
                                      grupo_lat=%s, grupo_lng=%s WHERE id=%s""",
                            (gid, len(ms), medoide["lat"], medoide["lng"], p["id"]))
            n_grupos += 1
            n_pts += len(ms)
    con.commit()
    if n_grupos:
        print(f"      {n_pts} endereços repetidos juntados em {n_grupos} portas "
              f"(mesmo logradouro e número)", flush=True)
    return {"grupos": n_grupos, "pontos_agrupados": n_pts}


def _face_real(ln: LineString, recuo, centro) -> LineString:
    """A linha da face recuada de meia caixa viária para DENTRO do quarteirão —
    onde os lotes de fato começam. É o trilho do alinhamento."""
    r = float(recuo or 0.0) or LARGURA_PADRAO_M / 2.0
    cs = list(ln.coords)
    lat0 = cs[0][1]
    my, mx = _metros(lat0)
    ax, ay = cs[0]
    bx, by = cs[-1]
    dx, dy = (bx - ax) * mx, (by - ay) * my
    n = math.hypot(dx, dy) or 1e-9
    nx, ny = -dy / n, dx / n
    if (centro.x - ax) * mx * nx + (centro.y - ay) * my * ny < 0:
        nx, ny = -nx, -ny
    return LineString([(x + nx * r / mx, y + ny * r / my) for x, y in cs])


def _sentido(ms: list[dict], proj: dict) -> int:
    """+1 se a numeração cresce no sentido do traçado da face, -1 se contra.

    Soma dos sinais das inclinações par a par (Theil–Sen em sinal): aguenta o
    ponto com coordenada torta sem que ele inverta a face inteira."""
    s = 0
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            dn = b["numero"] - a["numero"]
            dt = proj[b["id"]] - proj[a["id"]]
            if dn and dt:
                s += 1 if (dn > 0) == (dt > 0) else -1
    return 1 if s >= 0 else -1


# ════════════════════════════════════════════════════════════════════════════
PASSOS = {1: "area", 2: "vias", 3: "borda", 4: "pontos", 5: "faces",
          6: "alinhar", 7: "telhados", 8: "casar"}


def rodar(sid: str, de: int, ate: int = 6, **kw) -> dict:
    """Roda os passos `de`..`ate` da sessão, parando no primeiro erro e
    registrando-o — é o que permite `retomar` depois de um estouro."""
    con = bc.conectar()
    out = {}
    try:
        for n in range(max(de, 2), ate + 1):
            try:
                if n == 2:
                    out[n] = passo2_vias(sid, con=con, **{k: v for k, v in kw.items()
                                                          if k in ("usar_proxy", "com_maps")})
                elif n == 3:
                    out[n] = passo3_borda(sid, con=con)
                elif n == 4:
                    out[n] = passo4_pontos(sid, con=con)
                elif n == 5:
                    out[n] = passo5_faces(sid, con=con)
                elif n == 6:
                    out[n] = passo6_alinhar(sid, con=con)
            except Exception as e:
                QD.marcar_passo(sid, n - 1, {}, erro=f"passo {n}: {str(e)[:200]}", con=con)
                print(f"  ✗ passo {n} falhou: {str(e)[:160]}", flush=True)
                print(f"    retome com:  quadras.py retomar --sessao {sid}", flush=True)
                out["erro"] = {"passo": n, "msg": str(e)[:200]}
                break
        return out
    finally:
        con.close()
