import json
import asyncio
import aiohttp
import math
import os
from datetime import datetime
from pathlib import Path

# ==============================================================================
# CONFIGURAÇÕES
# ==============================================================================
MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
SIDE_METERS = 150 
SEARCH_RADIUS = 110 

EXCLUDE_TYPES = {
    "locality", "political", "sublocality", "neighborhood", "route", 
    "administrative_area_level_1", "administrative_area_level_2", "country"
}

async def get_place_details(session, place_id):
    """Busca detalhes profundos (Horários, Reviews, etc)."""
    fields = "name,rating,user_ratings_total,formatted_address,formatted_phone_number,opening_hours,price_level,reviews,types,geometry,website"
    url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields={fields}&language=pt-BR&key={MAPS_API_KEY}"
    async with session.get(url) as resp:
        data = await resp.json()
        return data.get('result', {})

async def fetch_60_pois(session, lat, lng):
    """Busca estabelecimentos na vizinhança."""
    all_results = []
    next_page_token = None
    
    for _ in range(3):
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?location={lat},{lng}&radius={SEARCH_RADIUS}&key={MAPS_API_KEY}"
        if next_page_token:
            url += f"&pagetoken={next_page_token}"
            await asyncio.sleep(2.1)
            
        async with session.get(url) as resp:
            data = await resp.json()
            if data.get('status') not in ['OK', 'ZERO_RESULTS']:
                break
            
            for item in data.get('results', []):
                types = set(item.get('types', []))
                if not types.intersection(EXCLUDE_TYPES):
                    all_results.append(item)
            
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break
    return all_results

def get_grid_points(lats, lngs, step_m):
    lat_min, lat_max = min(lats), max(lats)
    lng_min, lng_max = min(lngs), max(lngs)
    lat_step = step_m / 111320
    avg_lat = (lat_min + lat_max) / 2
    lng_step = step_m / (111320 * math.cos(math.radians(avg_lat)))
    
    points = []
    curr_lat = lat_min
    while curr_lat <= lat_max:
        curr_lng = lng_min
        while curr_lng <= lng_max:
            points.append((curr_lat, curr_lng))
            curr_lng += lng_step
        curr_lat += lat_step
    return points

async def run_grid_search():
    # 1. Inputs Iniciais
    projeto_nome = input("Digite o nome da área (ex: Centro_SM): ").strip().replace(" ", "_")
    
    print("\n⚠️  ALERTA DE CUSTO:")
    opcao_detalhes = input("Deseja aplicar PLACE DETAILS (Horários, Reviews, Telefone)? (S/N): ").strip().lower()
    aplicar_details = (opcao_detalhes == 's')

    print("\nDigite as 4 coordenadas (Lat, Lng):")
    coords = []
    for i in range(4):
        raw = input(f"Ponto {i+1}: ")
        lat, lng = map(float, raw.split(','))
        coords.append((lat, lng))
    
    lats, lngs = [c[0] for c in coords], [c[1] for c in coords]
    
    # 2. Pasta e Arquivo
    timestamp = datetime.now().strftime('%Y%m%d_%H%M')
    modo = "COMPLETO" if aplicar_details else "BASICO"
    folder_name = f"{timestamp}_{projeto_nome}_{modo}"
    base_path = Path("buscas_grid") / folder_name
    base_path.mkdir(parents=True, exist_ok=True)
    
    output_file = base_path / "resultado_pois.json"
    grid = get_grid_points(lats, lngs, SIDE_METERS)
    
    all_found_ids = set()
    master_list = []

    # 3. Execução
    async with aiohttp.ClientSession() as session:
        for i, (lat, lng) in enumerate(grid):
            print(f"[{i+1}/{len(grid)}] Célula {lat:.5f} | Encontrados: {len(master_list)}", end="\r")
            
            basic_pois = await fetch_60_pois(session, lat, lng)
            
            for p in basic_pois:
                pid = p['place_id']
                if pid not in all_found_ids:
                    all_found_ids.add(pid)
                    
                    if aplicar_details:
                        # ROTA CARA: Busca tudo no Google
                        d = await get_place_details(session, pid)
                        poi_data = {
                            'nome': d.get('name'),
                            'lat': d.get('geometry', {}).get('location', {}).get('lat'),
                            'lng': d.get('geometry', {}).get('location', {}).get('lng'),
                            'endereco': d.get('formatted_address'),
                            'telefone': d.get('formatted_phone_number'),
                            'horarios': d.get('opening_hours', {}).get('weekday_text', []),
                            'rating': d.get('rating'),
                            'reviews': d.get('reviews', [])[:2],
                            'place_id': pid,
                            'fonte': 'Google Place Details'
                        }
                    else:
                        # ROTA ECONÔMICA: Usa o que já veio no Nearby Search
                        poi_data = {
                            'nome': p.get('name'),
                            'lat': p['geometry']['location']['lat'],
                            'lng': p['geometry']['location']['lng'],
                            'endereco_resumido': p.get('vicinity'),
                            'categorias': p.get('types'),
                            'rating_basico': p.get('rating'),
                            'place_id': pid,
                            'fonte': 'Google Nearby (Básico)'
                        }
                    
                    master_list.append(poi_data)
            
            # Auto-save
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(master_list, f, ensure_ascii=False, indent=2)

    print(f"\n\n✅ Finalizado! {len(master_list)} POIs salvos no modo {modo}.")

if __name__ == "__main__":
    if not os.path.exists("buscas_grid"): os.makedirs("buscas_grid")
    asyncio.run(run_grid_search())