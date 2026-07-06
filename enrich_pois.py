"""
enrich_pois.py — Enriquecimento máximo de POIs via Google Places Details API

Para cada POI válido do recover_resultado.json de uma sessão escolhida,
busca TODOS os campos disponíveis em uma única chamada por POI.

Custo por POI: ~$0.025 (Basic + Contact + Atmosphere em 1 chamada)
Sem chamadas Find Place: place_id extraído da maps_url quando ausente.

Uso: python enrich_pois.py
"""

import json
import asyncio
import aiohttp
import re
import os
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote

# ==============================================================================
# CONFIGURAÇÃO
# ==============================================================================

MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")
CONCURRENT_REQUESTS = 5

# Todos os campos disponíveis na API, agrupados por SKU de custo:
# Basic ($0.017): address_components, adr_address, business_status, formatted_address,
#                 geometry, icon*, name, permanently_closed, photos, place_id, plus_code,
#                 types, url, utc_offset, vicinity, wheelchair_accessible_entrance
# Contact (+$0.003): formatted_phone_number, international_phone_number,
#                    opening_hours, secondary_opening_hours, website
# Atmosphere (+$0.005): editorial_summary, price_level, rating, reviews,
#                       user_ratings_total, reservable, serves_*, delivery,
#                       dine_in, takeout, curbside_pickup
FIELDS = ",".join([
    # Basic
    "place_id", "name", "types", "business_status", "permanently_closed",
    "formatted_address", "address_components", "geometry", "plus_code",
    "url", "utc_offset", "vicinity", "photos",
    "wheelchair_accessible_entrance",
    # Contact
    "formatted_phone_number", "international_phone_number",
    "opening_hours", "secondary_opening_hours", "website",
    # Atmosphere
    "rating", "user_ratings_total", "price_level",
    "reviews", "editorial_summary",
    "reservable", "delivery", "dine_in", "takeout", "curbside_pickup",
    "serves_breakfast", "serves_brunch", "serves_lunch",
    "serves_dinner", "serves_beer", "serves_wine",
])

# Custo por chamada Details (Basic + Contact + Atmosphere)
CUSTO_DETAILS = 0.025

# ==============================================================================
# SELEÇÃO DE SESSÃO
# ==============================================================================

def listar_sessoes(capturas_dir: Path) -> list[dict]:
    """Encontra todas as sessões com recover_resultado.json."""
    sessoes = []
    for session_json in sorted(capturas_dir.glob("*/session.json")):
        recover = session_json.parent / "crops" / "recover_resultado.json"
        enrich = session_json.parent / "crops" / "enrich_resultado.json"
        if not recover.exists():
            continue

        data = json.loads(recover.read_text(encoding="utf-8"))
        validos = [r for r in data if r.get("match_valido") and r.get("poi")]
        ja_enriquecidos = 0
        if enrich.exists():
            data_enrich = json.loads(enrich.read_text(encoding="utf-8"))
            ja_enriquecidos = sum(1 for r in data_enrich if r.get("poi", {}).get("enriquecido_em"))

        mtime = datetime.fromtimestamp(recover.stat().st_mtime).strftime("%Y-%m-%d")
        sessoes.append({
            "nome": session_json.parent.name,
            "recover_path": recover,
            "enrich_path": enrich,
            "total_pois": len(data),
            "validos": len(validos),
            "ja_enriquecidos": ja_enriquecidos,
            "data": mtime,
        })
    return sessoes


def selecionar_sessao() -> dict:
    """Menu interativo para escolher a sessão."""
    capturas_dir = Path("capturas")
    if not capturas_dir.exists():
        print("Erro: pasta 'capturas/' não encontrada.")
        exit(1)

    sessoes = listar_sessoes(capturas_dir)
    if not sessoes:
        print("Nenhuma sessão com recover_resultado.json encontrada.")
        exit(1)

    print("\n" + "=" * 60)
    print("  ComercialRadar — Enriquecimento de POIs")
    print("=" * 60)
    print(f"\n{'#':<4} {'Sessão':<35} {'POIs válidos':>12} {'Já enrich.':>10} {'Data':>12}")
    print("-" * 75)
    for i, s in enumerate(sessoes, 1):
        status = f"{s['ja_enriquecidos']}/{s['validos']}" if s["ja_enriquecidos"] else "-"
        print(f"[{i}]  {s['nome']:<35} {s['validos']:>12} {status:>10} {s['data']:>12}")

    print()
    while True:
        try:
            escolha = int(input("Qual sessão deseja enriquecer? ")) - 1
            if 0 <= escolha < len(sessoes):
                return sessoes[escolha]
        except (ValueError, KeyboardInterrupt):
            print("\nCancelado.")
            exit(0)
        print(f"  Digite um número entre 1 e {len(sessoes)}.")


# ==============================================================================
# EXTRAÇÃO DE PLACE_ID
# ==============================================================================

def extrair_place_id(poi: dict) -> str | None:
    """
    Retorna o place_id do POI.
    Primeiro tenta o campo direto, depois extrai da maps_url.
    """
    pid = poi.get("place_id")
    if pid:
        return pid

    url = poi.get("maps_url", "")
    if not url:
        return None

    # Padrão !16s%2Fg%2F... ou !16s%2FChI... na URL do Maps
    m = re.search(r"!16s([^!?&]+)", url)
    if m:
        return unquote(m.group(1)).lstrip("/")

    return None


# ==============================================================================
# CHAMADA À API
# ==============================================================================

async def fetch_details(http: aiohttp.ClientSession, place_id: str) -> dict:
    """Chama Places Details com todos os campos em uma única requisição."""
    url = (
        f"https://maps.googleapis.com/maps/api/place/details/json"
        f"?place_id={place_id}"
        f"&fields={FIELDS}"
        f"&language=pt-BR"
        f"&key={MAPS_API_KEY}"
    )
    async with http.get(url) as resp:
        data = await resp.json()
    status = data.get("status")
    if status == "OK":
        return data.get("result", {})
    return {"_api_status": status}


# ==============================================================================
# MERGE DE DADOS
# ==============================================================================

def merge_details(poi_atual: dict, details: dict) -> dict:
    """Funde os novos dados da API no POI existente, sem sobrescrever campos já bons."""
    if not details or details.get("_api_status"):
        return poi_atual

    poi = dict(poi_atual)

    # Identidade e localização
    poi["place_id"] = details.get("place_id", poi.get("place_id"))
    poi["maps_url_canonica"] = details.get("url", "")
    poi["plus_code"] = details.get("plus_code", {}).get("global_code", poi.get("plus_code", ""))

    # Status operacional
    poi["business_status"] = details.get("business_status", "")
    poi["permanently_closed"] = details.get("permanently_closed", False)

    # Endereço
    poi["formatted_address"] = details.get("formatted_address", poi.get("endereco", ""))
    poi["address_components"] = _parse_address_components(details.get("address_components", []))

    # Contato
    poi["telefone"] = (
        details.get("international_phone_number")
        or details.get("formatted_phone_number")
        or poi.get("telefone", "")
    )
    poi["website"] = details.get("website", poi.get("website", ""))

    # Horários
    if details.get("opening_hours"):
        poi["horarios_texto"] = details["opening_hours"].get("weekday_text", [])
        poi["aberto_agora"] = details["opening_hours"].get("open_now")
        poi["periodos"] = details["opening_hours"].get("periods", [])
    if details.get("secondary_opening_hours"):
        poi["horarios_secundarios"] = details["secondary_opening_hours"]

    # Avaliação e preço
    poi["avaliacao"] = details.get("rating", poi.get("avaliacao", ""))
    poi["total_avaliacoes"] = details.get("user_ratings_total", poi.get("total_avaliacoes", 0))
    poi["price_level"] = details.get("price_level")  # 0–4

    # Descrição editorial
    poi["editorial_summary"] = details.get("editorial_summary", {}).get("overview", "")

    # Avaliações completas (até 5)
    reviews_raw = details.get("reviews", [])
    poi["reviews"] = [
        {
            "autor": r.get("author_name", ""),
            "nota": r.get("rating"),
            "texto": r.get("text", ""),
            "data": r.get("relative_time_description", ""),
            "timestamp": r.get("time"),
        }
        for r in reviews_raw
    ]
    poi["ultima_avaliacao"] = poi["reviews"][0]["texto"] if poi["reviews"] else poi.get("ultima_avaliacao", "")

    # Fotos (mantém existentes se a API não retornar)
    fotos_api = details.get("photos", [])
    if fotos_api:
        poi["fotos"] = [
            f"https://maps.googleapis.com/maps/api/place/photo"
            f"?maxwidth=800&photo_reference={p['photo_reference']}&key={MAPS_API_KEY}"
            for p in fotos_api[:10]
        ]

    # Serviços e atributos
    attrs = [
        "delivery", "dine_in", "takeout", "curbside_pickup", "reservable",
        "wheelchair_accessible_entrance",
        "serves_breakfast", "serves_brunch", "serves_lunch",
        "serves_dinner", "serves_beer", "serves_wine",
    ]
    for attr in attrs:
        val = details.get(attr)
        if val is not None:
            poi[attr] = val

    # Categorias
    if details.get("types"):
        poi["categoria"] = ", ".join(details["types"])

    poi["enriquecido_em"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return poi


def _parse_address_components(components: list) -> dict:
    """Extrai campos de endereço estruturado da lista de componentes."""
    mapa = {
        "street_number": "numero",
        "route": "logradouro",
        "sublocality_level_1": "bairro",
        "administrative_area_level_2": "cidade",
        "administrative_area_level_1": "estado",
        "country": "pais",
        "postal_code": "cep",
    }
    resultado = {}
    for comp in components:
        for tipo in comp.get("types", []):
            if tipo in mapa:
                resultado[mapa[tipo]] = comp.get("long_name", "")
    return resultado


# ==============================================================================
# PROCESSO PRINCIPAL
# ==============================================================================

async def run_enrich(sessao: dict):
    recover_path: Path = sessao["recover_path"]
    enrich_path: Path = sessao["enrich_path"]

    data = json.loads(recover_path.read_text(encoding="utf-8"))
    validos = [r for r in data if r.get("match_valido") and r.get("poi")]
    invalidos = [r for r in data if not (r.get("match_valido") and r.get("poi"))]

    # Carrega progresso anterior se existir
    ja_feitos: set[str] = set()
    resultado_existente: list = []
    if enrich_path.exists():
        resultado_existente = json.loads(enrich_path.read_text(encoding="utf-8"))
        ja_feitos = {
            r["poi"].get("place_id") or extrair_place_id(r.get("poi", {}))
            for r in resultado_existente
            if r.get("poi", {}).get("enriquecido_em")
        }
        ja_feitos.discard(None)

    pendentes = [
        r for r in validos
        if (extrair_place_id(r["poi"]) or "") not in ja_feitos
    ]
    ja_prontos = [r for r in validos if r not in pendentes]

    custo_estimado = len(pendentes) * CUSTO_DETAILS

    print(f"\n{'=' * 60}")
    print(f"  Sessão: {sessao['nome']}")
    print(f"  POIs válidos: {sessao['validos']}")
    print(f"  Já enriquecidos (retomada): {len(ja_feitos)}")
    print(f"  A processar agora: {len(pendentes)}")
    print(f"  Custo estimado: US$ {custo_estimado:.2f}")
    print(f"{'=' * 60}\n")

    if not pendentes:
        print("Todos os POIs já foram enriquecidos.")
        return

    confirmacao = input("Confirma execução? [s/N] ").strip().lower()
    if confirmacao != "s":
        print("Cancelado.")
        return

    # Estado compartilhado entre workers
    resultado = list(resultado_existente) if resultado_existente else list(ja_prontos)
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(CONCURRENT_REQUESTS)
    chamadas_api = 0
    erros = 0

    async def processar_item(http: aiohttp.ClientSession, item: dict, idx: int, total: int):
        nonlocal chamadas_api, erros
        async with sem:
            poi = item.get("poi", {})
            place_id = extrair_place_id(poi)
            nome = poi.get("nome", "?")[:30]

            if not place_id:
                print(f"[{idx}/{total}] ⚠  Sem place_id  | {nome}")
                async with lock:
                    resultado.append(item)
                    _salvar(enrich_path, resultado)
                erros += 1
                return

            try:
                details = await fetch_details(http, place_id)
                chamadas_api += 1

                if details.get("_api_status"):
                    print(f"[{idx}/{total}] ✗  API:{details['_api_status']:<12} | {nome}")
                    erros += 1
                    item_enriquecido = dict(item)
                else:
                    item_enriquecido = dict(item)
                    item_enriquecido["poi"] = merge_details(poi, details)
                    status_op = details.get("business_status", "")[:12]
                    print(f"[{idx}/{total}] ✓  {status_op:<12} | {nome}")

                async with lock:
                    resultado.append(item_enriquecido)
                    _salvar(enrich_path, resultado)

            except Exception as e:
                print(f"[{idx}/{total}] ⚠  ERRO: {e} | {nome}")
                erros += 1
                async with lock:
                    resultado.append(item)
                    _salvar(enrich_path, resultado)

    print(f"Iniciando enriquecimento ({CONCURRENT_REQUESTS} requisições paralelas)...\n")
    inicio = datetime.now()

    connector = aiohttp.TCPConnector(limit=CONCURRENT_REQUESTS + 2)
    async with aiohttp.ClientSession(connector=connector) as http:
        tasks = [
            processar_item(http, item, i + 1, len(pendentes))
            for i, item in enumerate(pendentes)
        ]
        await asyncio.gather(*tasks)

    # Adiciona inválidos ao resultado final
    ids_no_resultado = {r.get("idx") for r in resultado}
    for r in invalidos:
        if r.get("idx") not in ids_no_resultado:
            resultado.append(r)

    resultado.sort(key=lambda x: str(x.get("idx", "9999")))
    _salvar(enrich_path, resultado)

    duracao = (datetime.now() - inicio).seconds
    custo_real = chamadas_api * CUSTO_DETAILS

    print(f"\n{'=' * 60}")
    print(f"  Concluído em {duracao}s")
    print(f"  Chamadas à API: {chamadas_api}")
    print(f"  Erros/sem place_id: {erros}")
    print(f"  Custo real estimado: US$ {custo_real:.2f}")
    print(f"  Arquivo: {enrich_path}")
    print(f"{'=' * 60}\n")


def _salvar(path: Path, dados: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ==============================================================================
# ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", help="Nome da sessão (pula o menu interativo)")
    parser.add_argument("--yes", action="store_true", help="Confirma execução sem perguntar")
    args = parser.parse_args()

    if args.session:
        capturas_dir = Path("capturas")
        sessoes = listar_sessoes(capturas_dir)
        matches = [s for s in sessoes if s["nome"] == args.session]
        if not matches:
            print(f"Sessão '{args.session}' não encontrada.")
            exit(1)
        sessao = matches[0]
        if args.yes:
            # Patch input() para retornar "s" automaticamente
            import builtins
            builtins.input = lambda _: "s"
    else:
        sessao = selecionar_sessao()

    asyncio.run(run_enrich(sessao))
