"""
minerar_area.py — Mineração de POIs SEM planilha base, limitada a um polígono.

Gera uma grade de células sobre o polígono desenhado no frontend e varre cada
célula com o Google Places Nearby Search (até 60 resultados/célula), deduplica
por place_id e grava incrementalmente um JSON no formato do ingester
(mesmo formato do search_from_sheet), com fonte='descoberto' e status='minerado'.

Com --details, enriquece cada POI com Place Details (telefone, site, horários,
reviews) — ROTA PAGA, usar com consciência.

USO:
  .venv\\Scripts\\python minerar_area.py --area areas/area_atual.json --sessao minha_area
     [--details] [--step 150] [--radius 110] [--out caminho.json]

Chave: MAPS_API_KEY no .env (fallback para a chave do search_area_pois.py).
"""

import os
import json
import math
import time
import asyncio
import argparse
from pathlib import Path

import aiohttp

import config  # carrega o .env e força UTF-8
import area_utils
import io_atomico

MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "").strip()

EXCLUDE_TYPES = {
    "locality", "political", "sublocality", "neighborhood", "route",
    "administrative_area_level_1", "administrative_area_level_2", "country",
    "point_of_interest", "establishment", "plus_code", "premise",
}

# Tradução dos types mais comuns do Places para categoria legível
TYPE_PT = {
    "restaurant": "Restaurante", "food": "Alimentação", "cafe": "Café",
    "bakery": "Padaria", "bar": "Bar", "meal_takeaway": "Lanchonete",
    "meal_delivery": "Delivery", "supermarket": "Supermercado",
    "grocery_or_supermarket": "Mercado", "convenience_store": "Conveniência",
    "clothing_store": "Loja de roupas", "shoe_store": "Loja de calçados",
    "store": "Loja", "shopping_mall": "Shopping", "furniture_store": "Móveis",
    "home_goods_store": "Utilidades", "electronics_store": "Eletrônicos",
    "hardware_store": "Material de construção", "pharmacy": "Farmácia",
    "drugstore": "Farmácia", "hospital": "Hospital", "doctor": "Clínica",
    "dentist": "Dentista", "veterinary_care": "Veterinário",
    "beauty_salon": "Salão de beleza", "hair_care": "Cabeleireiro",
    "gym": "Academia", "school": "Escola", "university": "Faculdade",
    "bank": "Banco", "atm": "Caixa eletrônico", "insurance_agency": "Seguros",
    "real_estate_agency": "Imobiliária", "lawyer": "Advocacia",
    "car_repair": "Oficina mecânica", "car_dealer": "Concessionária",
    "car_wash": "Lava-jato", "gas_station": "Posto de combustível",
    "lodging": "Hotel/Pousada", "church": "Igreja", "gift_shop": "Presentes",
    "pet_store": "Pet shop", "florist": "Floricultura", "night_club": "Casa noturna",
    "travel_agency": "Agência de viagens", "laundry": "Lavanderia",
    "locksmith": "Chaveiro", "electrician": "Eletricista", "painter": "Pintor",
    "plumber": "Encanador", "storage": "Depósito", "accounting": "Contabilidade",
    "funeral_home": "Funerária", "book_store": "Livraria", "jewelry_store": "Joalheria",
    "liquor_store": "Bebidas", "bicycle_store": "Bicicletaria",
}


def categoria_de_types(types: list) -> str:
    for t in types or []:
        if t in TYPE_PT:
            return TYPE_PT[t]
    uteis = [t for t in (types or []) if t not in EXCLUDE_TYPES]
    return uteis[0].replace("_", " ") if uteis else ""


def grade_no_poligono(poligono, step_m: float) -> list:
    """Pontos de grade (centro das células) dentro do polígono."""
    lat_min, lat_max, lng_min, lng_max = area_utils.bbox(poligono)
    lat_step = step_m / 111320.0
    lat_media = (lat_min + lat_max) / 2
    lng_step = step_m / (111320.0 * math.cos(math.radians(lat_media)))

    pontos = []
    lat = lat_min
    while lat <= lat_max:
        lng = lng_min
        while lng <= lng_max:
            if area_utils.ponto_no_poligono(lat, lng, poligono):
                pontos.append((lat, lng))
            lng += lng_step
        lat += lat_step
    return pontos


async def _nearby_60(session, lat, lng, radius):
    """Nearby Search com paginação (até 60 resultados por célula)."""
    resultados, token = [], None
    for _ in range(3):
        url = (f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
               f"?location={lat},{lng}&radius={radius}&language=pt-BR&key={MAPS_API_KEY}")
        if token:
            url += f"&pagetoken={token}"
            await asyncio.sleep(2.1)  # o token demora ~2s para ativar
        async with session.get(url) as resp:
            data = await resp.json()
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            break
        for item in data.get("results", []):
            if not set(item.get("types", [])) & {"locality", "political", "route",
                                                 "neighborhood", "sublocality"}:
                resultados.append(item)
        token = data.get("next_page_token")
        if not token:
            break
    return resultados


async def _details(session, place_id):
    fields = ("name,rating,user_ratings_total,formatted_address,"
              "formatted_phone_number,opening_hours,price_level,reviews,"
              "types,geometry,website,url")
    url = (f"https://maps.googleapis.com/maps/api/place/details/json"
           f"?place_id={place_id}&fields={fields}&language=pt-BR&key={MAPS_API_KEY}")
    async with session.get(url) as resp:
        data = await resp.json()
    return data.get("result", {}) or {}


def _monta_registro(p, sessao, poligono, det=None):
    """Nearby result (+details opcional) → registro no formato do ingester."""
    loc = p.get("geometry", {}).get("location", {})
    lat, lng = loc.get("lat"), loc.get("lng")
    dentro = area_utils.ponto_no_poligono(lat, lng, poligono)

    reg = {
        "fonte": "descoberto",
        "fonte_dado": "maps",
        "sessao": sessao,
        "nome": p.get("name", ""),
        "categoria": categoria_de_types(p.get("types")),
        "endereco": p.get("vicinity", "") or "",
        "avaliacao": str(p.get("rating")) if p.get("rating") is not None else None,
        "total_avaliacoes": p.get("user_ratings_total"),
        "lat_origem": lat, "lng_origem": lng,
        "maps_lat": lat, "maps_lng": lng,
        "place_id": p.get("place_id"),
        "status": "minerado",
        # fora da área NÃO invalida: o POI é gravado com cidade/UF e só fica
        # fora do foco da tela (ver `area_utils.gate_registro`)
        "fora_da_area": not dentro,
        "match_valido": bool(p.get("name")),
        "fotos": [], "comentarios": [], "horarios": {},
    }

    if det:
        horarios = {}
        for linha in det.get("opening_hours", {}).get("weekday_text", []) or []:
            dia, _, hor = str(linha).partition(":")
            if dia:
                horarios[dia.strip()] = hor.strip()
        comentarios = [{
            "autor": r.get("author_name"),
            "nota": r.get("rating"),
            "texto": r.get("text"),
            "data": r.get("relative_time_description"),
        } for r in (det.get("reviews") or [])[:config.MAX_REVIEWS]]
        preco = det.get("price_level")
        reg.update({
            "endereco": det.get("formatted_address") or reg["endereco"],
            "telefone": det.get("formatted_phone_number", "") or "",
            "website": det.get("website", "") or "",
            "maps_url": det.get("url", "") or "",
            "horarios": horarios,
            "comentarios": comentarios,
            "preco_medio": ("$" * int(preco)) if isinstance(preco, int) and preco > 0 else None,
        })
    return reg


async def minerar(poligono, sessao, out_json: Path, step_m: float, radius: float, details: bool):
    grade = grade_no_poligono(poligono, step_m)
    print(f"⛏️  Mineração de área — {len(grade)} células (passo {step_m:.0f}m, raio {radius:.0f}m)"
          f" | Details: {'SIM (pago)' if details else 'não'}", flush=True)
    if not grade:
        print("⚠️  Polígono muito pequeno para a grade — nada a minerar.")
        return

    vistos = set()
    registros = []
    if out_json.exists():
        try:
            registros = json.loads(out_json.read_text(encoding="utf-8"))
            vistos = {r.get("place_id") for r in registros if r.get("place_id")}
            print(f"♻️  Retomando: {len(registros)} POIs já minerados.", flush=True)
        except Exception:
            registros = []

    sem = asyncio.Semaphore(6)
    lock = asyncio.Lock()
    feitas = {"n": 0}
    inicio = time.time()

    def _salvar():
        io_atomico.escrever_json(out_json, registros)

    async def _celula(session, i, lat, lng):
        async with sem:
            try:
                brutos = await _nearby_60(session, lat, lng, radius)
            except Exception:
                brutos = []
            novos = []
            async with lock:
                candidatos = [p for p in brutos if p.get("place_id") and p["place_id"] not in vistos]
                for p in candidatos:
                    vistos.add(p["place_id"])
            for p in candidatos:
                det = None
                if details:
                    try:
                        det = await _details(session, p["place_id"])
                    except Exception:
                        det = None
                novos.append(_monta_registro(p, sessao, poligono, det))
            async with lock:
                registros.extend(novos)
                feitas["n"] += 1
                if feitas["n"] % 5 == 0 or feitas["n"] == len(grade):
                    _salvar()
                uteis = sum(1 for r in registros if r.get("match_valido"))
                print(f"⛏️  célula {feitas['n']}/{len(grade)} | únicos {len(registros)} | "
                      f"válidos na área {uteis} | {(time.time()-inicio)/60:.1f}min", flush=True)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*[_celula(session, i, la, lo) for i, (la, lo) in enumerate(grade)])

    _salvar()
    uteis = sum(1 for r in registros if r.get("match_valido"))
    print(f"\n{'═'*52}")
    print(f"⛏️  Mineração | Resumo")
    print(f"{'═'*52}")
    print(f"   POIs únicos coletados : {len(registros)}")
    print(f"   Válidos (na área)     : {uteis}")
    print(f"   Fora da área          : {len(registros) - uteis}")
    print(f"   Tempo                 : {(time.time()-inicio)/60:.1f} min")
    print(f"   💾 {out_json}")
    print(f"{'═'*52}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", required=True, help="JSON do polígono da área")
    parser.add_argument("--sessao", default="mineracao", help="Nome da sessão (identifica o run no banco)")
    parser.add_argument("--out", default="", help="JSON de saída (default: mineracao/<sessao>_db.json)")
    parser.add_argument("--step", type=float, default=150, help="Passo da grade em metros")
    parser.add_argument("--radius", type=float, default=110, help="Raio de busca por célula em metros")
    parser.add_argument("--details", action="store_true", help="Place Details (telefone/horários/reviews) — PAGO")
    args = parser.parse_args()

    poligono = area_utils.carregar_area(args.area)
    if not poligono:
        print(f"❌ Área inválida: {args.area}")
        return

    out = Path(args.out) if args.out else Path("mineracao") / f"{args.sessao}_db.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(minerar(poligono, args.sessao, out, args.step, args.radius, args.details))


if __name__ == "__main__":
    main()
