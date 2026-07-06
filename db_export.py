"""
db_export.py — Normaliza o resultado do pipeline principal e ingere no Postgres

Lê o search_resultado.json ou recover_resultado.json de uma sessão, converte cada
item para o formato normalizado do ingester Prisma e grava no banco
(pois + images_urls + comentarios + horario_funcionamento).

USO:
  py db_export.py capturas/<sessao>/session.json [--source recover|search] [--no-ingest]

Por padrão usa recover_resultado.json se existir (dataset final), senão search.
"""

import re
import json
import argparse
import subprocess
from pathlib import Path
from urllib.parse import unquote

import config


def extrair_place_id(maps_url: str):
    if not maps_url:
        return None
    m = re.search(r"!16s([^!?&]+)", maps_url)
    if m:
        return unquote(m.group(1)).lstrip("/")
    m = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", maps_url)
    return m.group(1) if m else None


def normalizar_item(r: dict, fonte: str, sessao: str) -> dict | None:
    poi = r.get("poi", {}) or {}
    nome = poi.get("nome", "")
    if not nome:
        return None
    return {
        "fonte": fonte,
        "sessao": sessao,
        "nome": nome,
        "categoria": poi.get("categoria", ""),
        "endereco": poi.get("endereco", ""),
        "telefone": poi.get("telefone", ""),
        "website": poi.get("website", ""),
        "avaliacao": poi.get("avaliacao", ""),
        "total_avaliacoes": poi.get("total_avaliacoes", 0),
        "plus_code": poi.get("plus_code", ""),
        "status_horario": poi.get("status_horario", ""),
        "lat_origem": r.get("lat"),
        "lng_origem": r.get("lng"),
        "maps_lat": poi.get("maps_lat"),
        "maps_lng": poi.get("maps_lng"),
        "maps_url": poi.get("maps_url", ""),
        "place_id": poi.get("place_id") or extrair_place_id(poi.get("maps_url", "")),
        "status": r.get("status"),
        "distancia_m": r.get("distancia_m"),
        "similaridade": r.get("similaridade"),
        "match_valido": r.get("match_valido"),
        "ocr_texto": r.get("ocr_texto", ""),
        "fotos": poi.get("fotos", []) or [],
        # comentarios: lista completa (extração nova) ou fallback p/ única review antiga
        "comentarios": poi.get("comentarios") or (
            [{"autor": "", "nota": None, "texto": poi.get("ultima_avaliacao", ""), "data": ""}]
            if poi.get("ultima_avaliacao") else []
        ),
        "horarios": poi.get("horarios", {}) or {},
    }


def exportar(session_path: Path, source: str = "auto") -> Path | None:
    crops = session_path.parent / "crops"
    recover = crops / "recover_resultado.json"
    search = crops / "search_resultado.json"

    if source == "recover":
        alvo = recover
    elif source == "search":
        alvo = search
    else:  # auto: prefere recover (dataset final)
        alvo = recover if recover.exists() else search

    if not alvo.exists():
        print(f"Erro: {alvo} não encontrado.")
        return None

    sessao = session_path.parent.name
    data = json.loads(alvo.read_text(encoding="utf-8"))
    registros = [n for n in (normalizar_item(r, "pipeline", sessao) for r in data) if n]

    out = crops / f"{sessao}_db.json"
    out.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")

    tot_f = sum(len(x["fotos"]) for x in registros)
    tot_c = sum(len(x["comentarios"]) for x in registros)
    print(f"📦 Normalizados {len(registros)} POIs (de {alvo.name}) | {tot_f} fotos | {tot_c} comentários")
    print(f"   → {out}")
    return out


def ingerir(json_path: Path):
    print("\n▶ Ingerindo no PostgreSQL via Prisma...")
    subprocess.run(
        ["npx", "ts-node", "src/ingest.ts", str(json_path)],
        shell=True, cwd=str(config.BASE_DIR),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", help="Caminho para session.json")
    parser.add_argument("--source", choices=["auto", "recover", "search"], default="auto")
    parser.add_argument("--no-ingest", action="store_true", help="Só gera o JSON normalizado")
    args = parser.parse_args()

    out = exportar(Path(args.session), args.source)
    if out and not args.no_ingest:
        ingerir(out)


if __name__ == "__main__":
    main()
