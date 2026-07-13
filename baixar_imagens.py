"""
baixar_imagens.py — Processo PÓS (à parte): baixa TODAS as imagens para dentro do
banco e registra a DATA de captura de cada uma.

O que faz:
  FASE 1 — Fotos do Maps: para cada linha de images_urls ainda sem bytes, baixa a
           imagem (lh3.googleusercontent.com), extrai a DATA do EXIF (o Google
           preserva DateTimeOriginal) e grava dados(BYTEA)+data_imagem na própria
           images_urls.
  FASE 2 — Street View: para cada POI com print de fachada, lê o arquivo local,
           consulta a API de metadados do Street View (grátis) para obter a DATA do
           panorama + pano_id, e grava tudo na tabela streetview_imgs (bytes + data).

Tudo fica NO BANCO. Idempotente: só processa o que ainda não foi baixado.

USO:
  .venv\\Scripts\\python baixar_imagens.py [--workers 8] [--limit N]
     [--pular-fotos] [--pular-streetview]
"""

import io
import os
import time
import argparse
import threading
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config  # .env + UTF-8
import realtime_ingest
import psycopg2
from PIL import Image, ExifTags

BASE = Path(__file__).resolve().parent
SV_DIR = BASE / "streetview"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAPS_KEY = os.environ.get("MAPS_API_KEY", "")

_EXIF_DT = {v: k for k, v in ExifTags.TAGS.items() if v in ("DateTimeOriginal", "DateTime")}


def _data_exif(raw: bytes) -> str | None:
    """Data de captura do EXIF (YYYY-MM-DD) — o Google preserva nas fotos do Maps."""
    try:
        img = Image.open(io.BytesIO(raw))
        exif = img.getexif()
        if not exif:
            return None
        for tag in ("DateTimeOriginal", "DateTime"):
            tid = _EXIF_DT.get(tag)
            # DateTimeOriginal fica no IFD de Exif (0x8769); DateTime no IFD raiz
            val = None
            if tag == "DateTimeOriginal":
                sub = exif.get_ifd(0x8769)
                val = sub.get(tid) if sub else None
            else:
                val = exif.get(tid)
            if val:
                m = str(val).strip()[:10].replace(":", "-")  # "2023:01:02" → "2023-01-02"
                if len(m) == 10 and m[:4].isdigit():
                    return m
    except Exception:
        return None
    return None


# ──────────────────────────────────────────────────────────────────────────
# FASE 1 — Fotos do Maps
# ──────────────────────────────────────────────────────────────────────────
def _baixar_foto(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read()
        ct = r.headers.get("Content-Type", "image/jpeg")
    return raw, ct


def fase_fotos(workers: int, limit: int):
    conn = realtime_ingest.conectar()
    with conn.cursor() as cur:
        cur.execute("SELECT id, url FROM images_urls WHERE dados IS NULL AND url LIKE 'http%'")
        alvos = cur.fetchall()
    conn.close()
    if limit:
        alvos = alvos[:limit]
    total = len(alvos)
    print(f"📷 FASE 1 — Fotos do Maps: {total} a baixar", flush=True)
    if not total:
        return
    lock = threading.Lock()
    cont = {"ok": 0, "com_data": 0, "erro": 0, "bytes": 0}
    ini = time.time()
    _local = threading.local()

    def _conn():
        if not hasattr(_local, "c") or _local.c.closed:
            _local.c = realtime_ingest.conectar()
        return _local.c

    def _um(item):
        img_id, url = item
        try:
            raw, ct = _baixar_foto(url)
            data = _data_exif(raw)
            c = _conn()
            with c, c.cursor() as cur:
                cur.execute("UPDATE images_urls SET dados=%s, data_imagem=%s, bytes_tam=%s, content_type=%s WHERE id=%s",
                            (psycopg2.Binary(raw) if True else raw,
                             data, len(raw), ct[:40], img_id))
            with lock:
                cont["ok"] += 1
                cont["bytes"] += len(raw)
                if data:
                    cont["com_data"] += 1
        except Exception:
            with lock:
                cont["erro"] += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_um, a) for a in alvos]
        for k, _ in enumerate(as_completed(futs), 1):
            if k % 50 == 0 or k == total:
                print(f"📷 fotos {k}/{total} | baixadas {cont['ok']} | com data EXIF "
                      f"{cont['com_data']} | {cont['bytes']/1e6:.0f} MB | erros {cont['erro']} | "
                      f"{(time.time()-ini)/60:.1f}min", flush=True)


# ──────────────────────────────────────────────────────────────────────────
# FASE 2 — Street View (bytes + data do panorama)
# ──────────────────────────────────────────────────────────────────────────
def _sv_metadata(lat, lng):
    url = ("https://maps.googleapis.com/maps/api/streetview/metadata?"
           + urllib.parse.urlencode({"location": f"{lat},{lng}", "key": MAPS_KEY}))
    try:
        import json
        md = json.loads(urllib.request.urlopen(url, timeout=15).read())
        if md.get("status") == "OK":
            return md.get("date"), (md.get("pano_id") or "")[:40]
    except Exception:
        pass
    return None, None


def fase_streetview(workers: int, limit: int):
    conn = realtime_ingest.conectar()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.streetview_path, COALESCE(p.maps_lat,p.lat_origem), COALESCE(p.maps_lng,p.lng_origem)
            FROM pois p
            WHERE p.streetview_path IS NOT NULL AND p.streetview_path <> 'NA'
              AND NOT EXISTS (SELECT 1 FROM streetview_imgs s WHERE s.poi_id = p.id)""")
        alvos = cur.fetchall()
    conn.close()
    if limit:
        alvos = alvos[:limit]
    total = len(alvos)
    print(f"\n📸 FASE 2 — Street View: {total} a arquivar (bytes + data do panorama)", flush=True)
    if not total:
        return
    lock = threading.Lock()
    cont = {"ok": 0, "com_data": 0, "erro": 0}
    ini = time.time()
    _local = threading.local()

    def _conn():
        if not hasattr(_local, "c") or _local.c.closed:
            _local.c = realtime_ingest.conectar()
        return _local.c

    def _um(item):
        poi_id, arq, lat, lng = item
        try:
            fp = SV_DIR / arq
            raw = fp.read_bytes() if fp.exists() else None
            data, pano = _sv_metadata(lat, lng) if lat is not None else (None, None)
            c = _conn()
            with c, c.cursor() as cur:
                cur.execute("""INSERT INTO streetview_imgs (poi_id, dados, data_captura, pano_id, bytes_tam, lat, lng, angulo)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,'facade')""",
                            (poi_id,
                             psycopg2.Binary(raw) if (raw and True) else raw,
                             data, pano, len(raw) if raw else None, lat, lng))
            with lock:
                cont["ok"] += 1
                if data:
                    cont["com_data"] += 1
        except Exception:
            with lock:
                cont["erro"] += 1

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_um, a) for a in alvos]
        for k, _ in enumerate(as_completed(futs), 1):
            if k % 50 == 0 or k == total:
                print(f"📸 street view {k}/{total} | arquivados {cont['ok']} | com data "
                      f"{cont['com_data']} | erros {cont['erro']} | {(time.time()-ini)/60:.1f}min", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--pular-fotos", action="store_true")
    p.add_argument("--pular-streetview", action="store_true")
    a = p.parse_args()
    ini = time.time()
    print("🗂️  Download de imagens para o banco (bytes + datas)\n", flush=True)
    if not a.pular_fotos:
        fase_fotos(a.workers, a.limit)
    if not a.pular_streetview:
        fase_streetview(min(a.workers, 6), a.limit)  # API metadata: mais conservador

    conn = realtime_ingest.conectar()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(dados), COUNT(data_imagem) FROM images_urls")
        nf, nfb, nfd = cur.fetchone()
        cur.execute("SELECT COUNT(*), COUNT(data_captura) FROM streetview_imgs")
        nsv, nsvd = cur.fetchone()
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size('images_urls') + pg_total_relation_size('streetview_imgs'))")
        tam = cur.fetchone()[0]
    conn.close()
    print(f"\n{'═'*54}")
    print(f"🗂️  Download concluído")
    print(f"{'═'*54}")
    print(f"   Fotos no banco    : {nfb}/{nf}  (com data EXIF: {nfd})")
    print(f"   Street views      : {nsv}  (com data do panorama: {nsvd})")
    print(f"   Tamanho em disco  : {tam}")
    print(f"   Tempo             : {(time.time()-ini)/60:.1f} min")
    print(f"{'═'*54}", flush=True)


if __name__ == "__main__":
    main()
