import os
import json
import asyncio
import aiohttp
import argparse
import difflib
import math
from pathlib import Path
from datetime import datetime

# ==============================================================================
# CONFIGURAÇÕES E CHAVES
# ==============================================================================
MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "") 
SEARCH_RADIUS = 200      # Raio de busca da API (metros)
COVERAGE_THRESHOLD = 150 # Se houver busca a menos de 150m, usa o cache acumulado
MATCH_THRESHOLD = 0.5    # Similaridade mínima entre OCR e nome no Maps

# ==============================================================================
# FUNÇÕES DE APOIO (MATEMÁTICA E FORMATAÇÃO)
# ==============================================================================

def haversine(lat1, lon1, lat2, lon2):
    """Calcula a distância em metros entre duas coordenadas."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def simple_match(name1, name2):
    """Calcula similaridade entre strings (0.0 a 1.0)."""
    if not name1 or not name2: return 0
    return difflib.SequenceMatcher(None, name1.lower(), name2.lower()).ratio()

def format_poi_data(p_data, details=None):
    """
    Padroniza os dados. 
    Protegido contra KeyError: 'geometry' para aceitar dados brutos ou já formatados.
    """
    details = details or {}
    
    # Extração de Coordenadas
    if 'geometry' in p_data:
        lat = p_data['geometry']['location']['lat']
        lng = p_data['geometry']['location']['lng']
    else:
        lat = p_data.get('maps_lat')
        lng = p_data.get('maps_lng')

    # Extração de Categorias
    if 'types' in p_data:
        categoria = ", ".join(p_data.get('types', []))
    else:
        categoria = p_data.get('categoria', '')

    # Extração do Último Comentário
    last_review = ""
    if details.get('reviews'):
        last_review = details['reviews'][0].get('text', '')

    return {
        'nome': p_data.get('name', p_data.get('nome', '')),
        'categoria': categoria,
        'avaliacao': p_data.get('rating', p_data.get('avaliacao', '')),
        'total_avaliacoes': p_data.get('user_ratings_total', p_data.get('total_avaliacoes', 0)),
        'endereco': p_data.get('vicinity', details.get('formatted_address', p_data.get('endereco', ''))),
        'telefone': details.get('international_phone_number', p_data.get('telefone', '')),
        'website': details.get('website', p_data.get('website', '')),
        'ultimo_comentario': last_review or p_data.get('ultimo_comentario', ''),
        'maps_lat': lat,
        'maps_lng': lng,
        'place_id': p_data.get('place_id')
    }

# ==============================================================================
# COMUNICAÇÃO COM API GOOGLE PLACES
# ==============================================================================

async def fetch_nearby_places(session, lat, lng):
    """Busca até 60 lugares (3 páginas) ao redor da coordenada."""
    places = []
    next_page_token = None
    
    for _ in range(3):
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?location={lat},{lng}&radius={SEARCH_RADIUS}&key={MAPS_API_KEY}"
        if next_page_token:
            url += f"&pagetoken={next_page_token}"
            await asyncio.sleep(2.1) # Delay obrigatório do Google para tokens de página
            
        async with session.get(url) as resp:
            data = await resp.json()
            if data.get('status') not in ['OK', 'ZERO_RESULTS']:
                break
            places.extend(data.get('results', []))
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break
    return places

async def get_place_details(session, place_id):
    """Busca detalhes (comentários) de um lugar específico."""
    fields = "name,rating,formatted_address,reviews,website,international_phone_number,types"
    url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields={fields}&language=pt-BR&key={MAPS_API_KEY}"
    async with session.get(url) as resp:
        data = await resp.json()
        return data.get('result', {})

# ==============================================================================
# PROCESSO PRINCIPAL (COM SALVAMENTO INCREMENTAL)
# ==============================================================================

async def run_recover(session_path: Path):
    search_json = session_path.parent / 'crops' / 'search_resultado.json'
    if not search_json.exists():
        print(f"Erro: {search_json} não encontrado.")
        return

    data_orig = json.loads(search_json.read_text(encoding='utf-8'))
    validos = [r for r in data_orig if r.get('match_valido')]
    falhas = [r for r in data_orig if not r.get('match_valido')]
    
    out_path = session_path.parent / 'crops' / 'recover_resultado.json'
    
    searched_centers = []     # Memória de buscas já feitas
    master_places_cache = {}  # Memória de POIs encontrados
    novos_descobertos = []    # POIs novos (Discovery)
    ids_registrados = {r.get('poi', {}).get('place_id') for r in data_orig if r.get('poi')}

    print(f"\n🚀 ComercialRadar — Recover Ativado (Modo Auto-Save)")
    print(f"Total de falhas para processar: {len(falhas)}")

    async with aiohttp.ClientSession() as session:
        for idx, item in enumerate(falhas):
            try:
                lat, lng = item['lat'], item['lng']
                nome_ocr = item.get('ocr_texto', '')
                
                # 1. Verifica Cobertura Espacial (Economia de API)
                ponto_coberto = False
                for c_lat, c_lng in searched_centers:
                    if haversine(lat, lng, c_lat, c_lng) <= COVERAGE_THRESHOLD:
                        ponto_coberto = True
                        break
                
                # 2. Busca API ou Usa Cache
                if not ponto_coberto:
                    nearby = await fetch_nearby_places(session, lat, lng)
                    searched_centers.append((lat, lng))
                    
                    for p in nearby:
                        pid = p['place_id']
                        if pid not in master_places_cache:
                            formatted = format_poi_data(p)
                            master_places_cache[pid] = formatted
                            
                            # Discovery: Se o lugar é novo, registra
                            if pid not in ids_registrados:
                                ids_registrados.add(pid)
                                novos_descobertos.append({
                                    'idx': f"AUTO_{pid[:6]}",
                                    'lat': formatted['maps_lat'],
                                    'lng': formatted['maps_lng'],
                                    'ocr_texto': 'DESCOBERTO_VIA_API',
                                    'match_valido': True,
                                    'status': 'novo_descoberto',
                                    'poi': formatted
                                })
                
                # 3. Match de Nome
                best_match = None
                highest_score = 0
                for pid, poi_data in master_places_cache.items():
                    score = simple_match(nome_ocr, poi_data['nome'])
                    if score > highest_score:
                        highest_score = score
                        best_match = poi_data
                
                # 4. Enriquecimento de Dados
                if best_match and highest_score >= MATCH_THRESHOLD:
                    if not best_match.get('ultimo_comentario'):
                        details = await get_place_details(session, best_match['place_id'])
                        best_match.update(format_poi_data(best_match, details))
                    
                    item['poi'] = best_match
                    item['match_valido'] = True
                    item['status'] = 'recuperado_cache' if ponto_coberto else 'recuperado_api'
                    item['score_match'] = round(highest_score, 2)
                else:
                    item['status'] = 'nao_encontrado_no_raio'

                # 5. SALVAMENTO INCREMENTAL (A cada item processado)
                progresso = validos + falhas + novos_descobertos
                # Ordena pelo índice para manter coerência
                progresso.sort(key=lambda x: str(x.get('idx', '9999')))
                
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump(progresso, f, ensure_ascii=False, indent=2)

                simbolo = "✅" if item.get('match_valido') else "❌"
                origem = "🧊 CACHE" if ponto_coberto else "📡 API"
                print(f"[{idx+1}/{len(falhas)}] {simbolo} {origem} | {nome_ocr[:20]}")

            except Exception as e:
                print(f"[{idx+1}/{len(falhas)}] ⚠️ ERRO NO ITEM: {str(e)}")
                continue # Pula erro e vai para o próximo

    print(f"\n🏁 Processamento Finalizado!")
    print(f"Arquivo final salvo em: {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('session', help='Caminho para o session.json')
    args = parser.parse_args()
    asyncio.run(run_recover(Path(args.session)))