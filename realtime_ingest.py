"""
realtime_ingest.py — Ingestão incremental de POIs no PostgreSQL (psycopg2)

Espelha as regras do src/ingest.ts (Prisma), mas registro a registro, para o
servidor web gravar no banco EM TEMPO REAL conforme o coletor produz resultados:
  - pula sem nome / match_valido=False
  - guard geográfico Brasil (lat∈[-34,6], lng∈[-74,-34])
  - gate de polígono (área desenhada no frontend), quando fornecido
  - idempotente por place_id (delete + recreate, derivadas em cascade manual)

Também expõe utilidades usadas pelo server.py (limpeza fora da área, consultas).
"""

import os
import json
import threading

import psycopg2
import psycopg2.extras

import config  # carrega o .env
import area_utils

_LOCK = threading.Lock()


def conectar():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        dbname=os.environ.get("POSTGRES_DB", "comercialradar"),
    )


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int("".join(ch for ch in str(v) if ch.isdigit() or ch == "-") or "0")
    except ValueError:
        return None


def _s(v):
    if v is None or v == "":
        return None
    return str(v)


def _horarios(h):
    if not h:
        return []
    if isinstance(h, list):
        return [(str(x.get("dia", "")), _s(x.get("horario")))
                for x in h if x and (x.get("dia") or x.get("horario"))]
    if isinstance(h, dict):
        return [(str(dia), _s(hor)) for dia, hor in h.items()]
    return []


def ingerir_registro(r: dict, poligono=None, conn=None) -> tuple:
    """
    Grava UM registro do pipeline no banco. Retorna (resultado, poi_id):
      ('inserido', id) · ('pulado', None) · ('fora_da_area', None)
    """
    if not r.get("nome") or r.get("match_valido") is False or r.get("match_valido") is None:
        return ("pulado", None)

    la = _f(r.get("maps_lat"))
    lo = _f(r.get("maps_lng"))
    if la is None or lo is None:
        la, lo = _f(r.get("lat_origem")), _f(r.get("lng_origem"))
    # Guard Brasil (mesma regra do ingest.ts)
    if la is not None and lo is not None and (la < -34 or la > 6 or lo < -74 or lo > -34):
        return ("pulado", None)
    # Gate de área (polígono do frontend)
    if poligono and la is not None and not area_utils.ponto_no_poligono(la, lo, poligono):
        return ("fora_da_area", None)

    fechar = False
    if conn is None:
        conn = conectar()
        fechar = True
    try:
        with _LOCK, conn, conn.cursor() as cur:
            ids = []
            if r.get("place_id"):
                cur.execute("SELECT id FROM pois WHERE place_id = %s", (r["place_id"],))
                ids = [row[0] for row in cur.fetchall()]
            elif r.get("nome_planilha"):
                # Sem place_id (recuperados do Gemini) → deduplica pela ORIGEM
                # (fonte+sessao+nome da planilha), senão cada rerun re-insere o mesmo POI.
                cur.execute(
                    """SELECT id FROM pois WHERE place_id IS NULL
                       AND fonte = %s AND sessao IS NOT DISTINCT FROM %s AND nome_original = %s""",
                    (r.get("fonte") or "desconhecido", _s(r.get("sessao")), str(r["nome_planilha"])))
                ids = [row[0] for row in cur.fetchall()]
            if ids:
                # MERGE não-destrutivo: um dado NOVO vazio nunca apaga um dado BOM
                # já salvo. Vale p/ enriquecimento (CNPJ/streetview/web) E p/ os campos
                # operacionais — ex.: reabrir o Maps p/ pegar o endereço correto não pode
                # zerar o telefone que a web havia achado. Coluna do banco = chave do reg,
                # exceto endereço/telefone-da-planilha (esses vêm de nome_planilha etc).
                _MERGE = ("cnpj", "razao_social", "nome_fantasia", "natureza_juridica",
                          "cnae", "situacao_cadastral", "socios", "instagram", "email",
                          "resumo_avaliacoes", "streetview_path", "fontes_web",
                          "telefone", "website", "categoria", "status_horario",
                          "avaliacao", "total_avaliacoes", "preco_medio", "plus_code",
                          "endereco", "endereco_fonte")
                cur.execute(f"SELECT {', '.join(_MERGE)} FROM pois WHERE id = %s", (ids[0],))
                antigo = cur.fetchone()
                if antigo:
                    for campo, valor in zip(_MERGE, antigo):
                        if valor not in (None, "", 0) and r.get(campo) in (None, "", 0):
                            r[campo] = valor
                # PRESERVA fotos/comentários/horários já coletados se o registro novo
                # não os traz (uma etapa de enriquecimento que só melhora texto NÃO pode
                # apagar as fotos que o Maps já tinha capturado).
                keep = ids[0]
                if not (r.get("fotos") or []):
                    cur.execute("SELECT url, ordem FROM images_urls WHERE poi_id = %s ORDER BY ordem NULLS LAST, id", (keep,))
                    fs = cur.fetchall()
                    if fs:
                        r["fotos"] = [u for u, _ in fs]
                        r["_preserva_fotos"] = True
                if not (r.get("comentarios") or []):
                    cur.execute("SELECT autor, nota, texto, data FROM comentarios WHERE poi_id = %s ORDER BY id", (keep,))
                    cs = cur.fetchall()
                    if cs:
                        r["comentarios"] = [{"autor": a, "nota": n, "texto": t, "data": d} for a, n, t, d in cs]
                if not r.get("horarios"):
                    cur.execute("SELECT dia, horario FROM horario_funcionamento WHERE poi_id = %s ORDER BY id", (keep,))
                    hs = cur.fetchall()
                    if hs:
                        r["horarios"] = [{"dia": d, "horario": h} for d, h in hs]
                cur.execute("DELETE FROM images_urls WHERE poi_id = ANY(%s)", (ids,))
                cur.execute("DELETE FROM comentarios WHERE poi_id = ANY(%s)", (ids,))
                cur.execute("DELETE FROM horario_funcionamento WHERE poi_id = ANY(%s)", (ids,))
                cur.execute("DELETE FROM pois WHERE id = ANY(%s)", (ids,))

            cur.execute(
                """INSERT INTO pois (fonte, sessao, nome, categoria, endereco, telefone, website,
                       avaliacao, total_avaliacoes, plus_code, status_horario,
                       lat_origem, lng_origem, maps_lat, maps_lng, maps_url, place_id,
                       status, distancia_m, similaridade, match_valido, ocr_texto,
                       nome_original, endereco_original, preco_medio, fonte_dado, ia_resposta,
                       cnpj, razao_social, nome_fantasia, natureza_juridica, cnae,
                       situacao_cadastral, socios, instagram, email, resumo_avaliacoes,
                       streetview_path, fontes_web, endereco_fonte)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (
                    r.get("fonte") or "desconhecido", _s(r.get("sessao")), str(r["nome"]),
                    _s(r.get("categoria")), _s(r.get("endereco")), _s(r.get("telefone")),
                    _s(r.get("website")), _s(r.get("avaliacao")), _i(r.get("total_avaliacoes")),
                    _s(r.get("plus_code")), _s(r.get("status_horario")),
                    _f(r.get("lat_origem") if r.get("lat_origem") is not None else r.get("lat")),
                    _f(r.get("lng_origem") if r.get("lng_origem") is not None else r.get("lng")),
                    _f(r.get("maps_lat")), _f(r.get("maps_lng")), _s(r.get("maps_url")),
                    _s(r.get("place_id")), _s(r.get("status")), _f(r.get("distancia_m")),
                    _f(r.get("similaridade")), r.get("match_valido"), _s(r.get("ocr_texto")),
                    _s(r.get("nome_planilha")), _s(r.get("endereco_planilha")),
                    _s(r.get("preco_medio")), _s(r.get("fonte_dado")), _s(r.get("ia_resposta")),
                    _s(r.get("cnpj")), _s(r.get("razao_social")), _s(r.get("nome_fantasia")),
                    _s(r.get("natureza_juridica")), _s(r.get("cnae")),
                    _s(r.get("situacao_cadastral")), _s(r.get("socios")), _s(r.get("instagram")),
                    _s(r.get("email")), _s(r.get("resumo_avaliacoes")),
                    _s(r.get("streetview_path")), _s(r.get("fontes_web")),
                    _s(r.get("endereco_fonte")),
                ),
            )
            poi_id = cur.fetchone()[0]

            fotos = [(poi_id, str(u), k) for k, u in enumerate(r.get("fotos") or []) if u]
            if fotos:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO images_urls (poi_id, url, ordem) VALUES %s", fotos)

            coments = [(poi_id, _s(c.get("autor")), _f(c.get("nota")), _s(c.get("texto")), _s(c.get("data")))
                       for c in (r.get("comentarios") or []) if isinstance(c, dict)]
            if coments:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO comentarios (poi_id, autor, nota, texto, data) VALUES %s", coments)

            hors = [(poi_id, dia, hor) for dia, hor in _horarios(r.get("horarios"))]
            if hors:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO horario_funcionamento (poi_id, dia, horario) VALUES %s", hors)

        return ("inserido", poi_id)
    finally:
        if fechar:
            conn.close()


def limpar_fora_da_area(poligono, dry_run: bool = True) -> dict:
    """
    Remove do banco os POIs cuja coordenada efetiva cai FORA do polígono.
    dry_run=True só conta (não deleta). POIs sem coordenada são preservados.
    """
    conn = conectar()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                           FROM pois""")
            fora, dentro, sem_coord = [], 0, 0
            for pid, la, lo in cur.fetchall():
                if la is None or lo is None:
                    sem_coord += 1
                elif area_utils.ponto_no_poligono(la, lo, poligono):
                    dentro += 1
                else:
                    fora.append(pid)

            if not dry_run and fora:
                cur.execute("DELETE FROM images_urls WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM comentarios WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM horario_funcionamento WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM pois WHERE id = ANY(%s)", (fora,))

        return {"fora": len(fora), "dentro": dentro, "sem_coord": sem_coord,
                "removidos": 0 if dry_run else len(fora), "dry_run": dry_run}
    finally:
        conn.close()
