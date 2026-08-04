"""
area_utils.py — Polígono de área válida (limite geográfico do trabalho)

O frontend desenha um polígono no mapa; ele é salvo como JSON e passado aos
coletores/ingestores. Todo POI cuja coordenada cai FORA do polígono é marcado
com status 'fora_da_area' e match_valido=False (não entra no banco).

Formato do arquivo de área:
  {"nome": "parnaiba", "polygon": [[lat, lng], [lat, lng], ...]}
  (também aceita uma lista pura [[lat, lng], ...])

Sem dependências externas (ray casting puro).
"""

import json
from pathlib import Path

AREA_PADRAO = "area_atual"


def _esquema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS area_trabalho (
                     nome      text PRIMARY KEY,
                     polygon   jsonb NOT NULL,     -- [[lat, lng], ...]
                     salvo_em  timestamp DEFAULT now())""")


def salvar_area(poligono, nome: str = AREA_PADRAO) -> int:
    """Grava a área NO BANCO. Lista vazia apaga."""
    import base_comum as bc
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            _esquema(cur)
            if not poligono or len(poligono) < 3:
                cur.execute("DELETE FROM area_trabalho WHERE nome=%s", (nome,))
                con.commit()
                return 0
            pol = [[float(a), float(b)] for a, b in poligono]
            cur.execute("""INSERT INTO area_trabalho (nome, polygon, salvo_em)
                           VALUES (%s, %s, now())
                           ON CONFLICT (nome) DO UPDATE
                             SET polygon = EXCLUDED.polygon, salvo_em = now()""",
                        (nome, json.dumps(pol)))
        con.commit()
        return len(pol)
    finally:
        con.close()


def carregar_area(ref=AREA_PADRAO) -> list | None:
    """Vértices [[lat, lng], ...] da área — DO BANCO.

    A área é dado, e dado mora no banco (tabela `area_trabalho`), não num .json
    dentro da pasta do sistema: ela é compartilhada entre o servidor e todos os
    coletores, que rodam como subprocessos separados.

    `ref` é o NOME da área. Ainda aceita um caminho de arquivo, para não quebrar
    quem chame com `areas/area_atual.json` — nesse caso lê o arquivo e MIGRA para
    o banco, para a próxima leitura já vir de lá."""
    if ref is None:
        ref = AREA_PADRAO
    p = Path(str(ref))
    if p.suffix.lower() == ".json":
        nome = p.stem or AREA_PADRAO
        if p.exists():                       # legado: migra e segue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                poly = data.get("polygon") if isinstance(data, dict) else data
                if poly and len(poly) >= 3:
                    salvar_area(poly, nome)
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        ref = nome

    try:
        import base_comum as bc
        con = bc.conectar()
    except Exception:
        return None
    try:
        with con.cursor() as cur:
            _esquema(cur)
            con.commit()
            cur.execute("SELECT polygon FROM area_trabalho WHERE nome=%s", (str(ref),))
            r = cur.fetchone()
        if not r or not r[0] or len(r[0]) < 3:
            return None
        return [[float(a), float(b)] for a, b in r[0]]
    except Exception:
        return None
    finally:
        con.close()


def ponto_no_poligono(lat, lng, poligono) -> bool:
    """Ray casting: True se (lat, lng) está dentro do polígono [[lat, lng], ...]."""
    if lat is None or lng is None or not poligono:
        return False
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i][0], poligono[i][1]
        yj, xj = poligono[j][0], poligono[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            dentro = not dentro
        j = i
    return dentro


def bbox(poligono):
    lats = [p[0] for p in poligono]
    lngs = [p[1] for p in poligono]
    return min(lats), max(lats), min(lngs), max(lngs)


def coord_do_registro(reg):
    """Coordenada efetiva de um registro do pipeline (maps_* > *_origem)."""
    la = reg.get("maps_lat")
    lo = reg.get("maps_lng")
    if la is None or lo is None:
        la = reg.get("lat_origem")
        lo = reg.get("lng_origem")
    try:
        return (float(la), float(lo)) if la is not None and lo is not None else (None, None)
    except (TypeError, ValueError):
        return (None, None)


def gate_registro(reg: dict, poligono) -> bool:
    """
    Aplica o portão de área a um registro do pipeline.
    Retorna True se o registro segue válido; False se foi marcado fora_da_area.
    Registros sem coordenada nenhuma passam (não há como julgar).
    """
    if not poligono:
        return True
    la, lo = coord_do_registro(reg)
    if la is None:
        return True
    if ponto_no_poligono(la, lo, poligono):
        return True
    if reg.get("match_valido"):
        reg["status_antes_area"] = reg.get("status")
        reg["status"] = "fora_da_area"
        reg["match_valido"] = False
    return False
