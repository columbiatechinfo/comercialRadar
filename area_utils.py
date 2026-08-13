"""
area_utils.py — Polígono de área válida (limite geográfico do trabalho)

O frontend desenha um polígono no mapa; ele é o FOCO do trabalho, não um filtro
de gravação. Todo POI encontrado é gravado — o que cai fora do polígono entra
identificado por `cidade`/`uf` e simplesmente não aparece na tela enquanto
aquela área está em foco. Achar custa tempo de busca; descartar o que já foi
achado é jogar esse tempo fora, e no dia em que a cidade vizinha for minerada
ela já começa com parte do trabalho pronto.

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


_MUN_CACHE: dict = {}


def municipio_da_area(poligono=None, ref=AREA_PADRAO) -> tuple:
    """(cidade, uf) DA ÁREA DE TRABALHO — a fonte única.

    Cidade e UF não são adivinhadas por módulo. Elas saem de onde o usuário as
    definiu: o polígono desenhado no mapa, ou o município escolhido no painel
    (que vira polígono pela malha do IBGE). Um ponto dentro dele — o centroide —
    resolve o município pela `ibge_malha`, com índice espacial.

    Antes cada consumidor deduzia por conta própria e o `minerar_web` tinha
    `"Parnaíba"` FIXO no código, herança de quando havia um cliente só: numa
    rodada de Canoas ele montava a busca `"Fulano" parnaiba RS` e, pior, a
    conferência do CNPJ na Receita comparava o município com "parnaiba" e
    REJEITAVA todo CNPJ encontrado. O dado chegava e era jogado fora."""
    poly = poligono if poligono is not None else carregar_area(ref)
    if not poly or len(poly) < 3:
        return ("", "")
    chave = (round(poly[0][0], 5), round(poly[0][1], 5), len(poly))
    if chave in _MUN_CACHE:
        return _MUN_CACHE[chave]
    lat = sum(p[0] for p in poly) / len(poly)
    lng = sum(p[1] for p in poly) / len(poly)
    # A consulta é feita AQUI, e não mais em `quadras_br`. O módulo de quadras
    # mudou-se para o radarTelhados em 11/08/2026, junto com todo o território;
    # o comercialRadar continua precisando resolver município por ponto porque
    # cidade e UF são a fonte única de toda a ferramenta. A tabela `ibge_malha`
    # fica nas duas — são 8,6 MB de divisa oficial, e duplicar dado de
    # referência público é mais barato que acoplar duas ferramentas.
    # A `ibge_malha` é base pública e mora no banco de REFERÊNCIA desde
    # 12/08/2026. Consultá-la pela conexão do produto devolvia ("", "") em
    # silêncio — e cidade vazia não estoura erro: ela envenena a busca web e faz
    # a conferência de CNPJ recusar tudo, que foi exatamente o bug do "Parnaíba"
    # fixo descrito acima. Falha silenciosa em fonte única é a pior de todas.
    try:
        import base_comum as bc
        con = bc.conectar_referencia()
        try:
            with con.cursor() as cur:
                cur.execute("""SELECT nome, uf FROM ibge_malha
                                WHERE ST_Contains(geom,
                                        ST_SetSRID(ST_Point(%s, %s), 4326))
                                LIMIT 1""", (lng, lat))
                r = cur.fetchone()
            out = ((r[0] or ""), (r[1] or "").upper()) if r else ("", "")
        finally:
            con.close()
    except Exception:
        out = ("", "")
    _MUN_CACHE[chave] = out
    return out


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
    Marca se o registro caiu DENTRO da área de trabalho.
    Retorna True se está dentro; False se está fora.
    Registros sem coordenada nenhuma passam (não há como julgar).

    **Estar fora não invalida mais o registro** (regra do usuário, 04/08/2026).
    Antes isto zerava `match_valido`, e o ingestor descartava o POI logo na
    primeira checagem — um comércio já encontrado e já pago em tempo de busca era
    jogado fora por estar 40 m além da linha desenhada. Agora ele é gravado, com
    `cidade`/`uf` tiradas do endereço, e o polígono só decide o que aparece na
    TELA: o mapa mostra a área em foco ou o município selecionado, e o de fora
    espera ali até que aquela cidade seja o foco.

    O `status` original é preservado — ele diz como o POI foi encontrado, que é
    outra pergunta. Quem quiser o banco restrito à área usa "Limpar banco fora
    da área", que é uma decisão explícita.
    """
    if not poligono:
        return True
    la, lo = coord_do_registro(reg)
    if la is None:
        return True
    if ponto_no_poligono(la, lo, poligono):
        reg["fora_da_area"] = False
        return True
    reg["fora_da_area"] = True
    return False
