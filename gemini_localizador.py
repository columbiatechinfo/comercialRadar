"""
gemini_localizador.py — 3ª camada: localização via Gemini + Google Search grounding

Quando o Maps não acha o POI (nem direto, nem nos vizinhos), consulta o Gemini
com grounding (busca no Google, grátis dentro da cota) e traz dados estruturados:
endereço, coordenada, telefone, categoria, horário, avaliação, site.

BATCHING: envia vários POIs por chamada (default 6) → 1 requisição de grounding
cobre N POIs, economizando muito (grounding tem cota diária generosa; só tokens custam).

Chave: GEMINI_API_KEY no .env. Modelo: GEMINI_MODEL (default gemini-2.5-flash).
"""

import os
import re
import json
import time
import urllib.request
import urllib.error

import config  # carrega o .env

# gemini-2.5-pro é MUITO superior ao flash na extração via grounding: o flash devolve
# endereço/horário/avaliação null onde o pro traz endereço completo com rua/número/CEP,
# horário por dia e nota (comprovado com Churrascaria Thiluin). Vale o custo maior.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# POIs por chamada de grounding. Batch grande DILUI a busca (o modelo pesquisa raso
# cada item) → dados esparsos. 4 equilibra foco vs nº de chamadas; a cota grátis de
# grounding (1500/dia) cobre bem os volumes típicos de um município.
TAM_LOTE = 4


def _call(prompt: str, timeout: int = 90, tentativas: int = 5) -> str:
    if not _KEY:
        raise RuntimeError("GEMINI_API_KEY ausente no .env")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }).encode()
    ultima = ""
    for i in range(tentativas):
        try:
            req = urllib.request.Request(
                _URL, data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": _KEY},
            )
            r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            return r["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            ultima = f"HTTP {e.code}"
            if e.code == 429 or e.code >= 500:   # rate limit ou sobrecarga temporária
                time.sleep(4 * (i + 1))
                continue
            break
        except Exception as e:
            ultima = str(e)[:80]
            time.sleep(2 * (i + 1))
    return ""


def _parse_array(txt: str) -> list:
    if not txt:
        return []
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


_CAMPOS = ('[{"i":int, "encontrado":bool, "nome_oficial":str, "endereco":str, '
           '"lat":number, "lng":number, "telefone":str, "categoria":str, '
           '"horario":str, "avaliacao":number, "total_avaliacoes":number, '
           '"preco_medio":str, "website":str}]')


def _prompt(bloco):
    lista = "\n".join(
        f"{j+1}. {it.get('nome','')} — {it.get('municipio','') or 'Brasil'}, {it.get('uf','')}"
        for j, it in enumerate(bloco)
    )
    return (
        "Você é um pesquisador de dados de estabelecimentos. Para CADA item da lista, "
        "faça uma busca DEDICADA no Google (não uma busca só para a lista toda) e extraia "
        "TODOS os dados públicos: Google Maps, Instagram, iFood, sites.\n\n"
        f"LISTA:\n{lista}\n\n"
        "Para cada item, busque ativamente:\n"
        "- ENDEREÇO COMPLETO: rua, número, bairro, cidade-UF e CEP (não só a cidade).\n"
        "- telefone/WhatsApp, horário de funcionamento detalhado, avaliação e nº de avaliações,\n"
        "  faixa de preço, categoria específica, site.\n"
        "- coordenadas (lat/lng) quando o Google Maps mostrar.\n"
        "NÃO deixe um campo null se a informação existir em QUALQUER fonte pública. "
        "Em conflito, prefira o Google Maps.\n\n"
        "Retorne SOMENTE um JSON array (um objeto por item, na MESMA ORDEM, "
        '"i" = número do item):\n'
        f"{_CAMPOS}\n"
        "encontrado=true só com confiança real de ser o estabelecimento certo na cidade indicada."
    )


def localizar_bloco(bloco: list) -> list:
    """Uma chamada de grounding para um bloco. Retorna lista alinhada ao bloco."""
    arr = _parse_array(_call(_prompt(bloco)))
    by_i = {o.get("i"): o for o in arr if isinstance(o, dict)}
    out = []
    for j in range(len(bloco)):
        obj = by_i.get(j + 1)
        if obj is None and j < len(arr) and isinstance(arr[j], dict):
            obj = arr[j]
        out.append(obj)
    return out


def localizar_lote(itens: list, tam_lote: int = TAM_LOTE) -> list:
    """Sequencial (compat). Para volume, use os blocos em paralelo no chamador."""
    resultados = [None] * len(itens)
    for ini in range(0, len(itens), tam_lote):
        bloco = itens[ini:ini + tam_lote]
        for j, obj in enumerate(localizar_bloco(bloco)):
            resultados[ini + j] = obj
    return resultados


if __name__ == "__main__":
    itens = [{"nome": n, "municipio": "Parnaíba", "uf": "PI"}
             for n in ["Multicine", "Hotel Portinho", "Dk Modas",
                       "Dentista Laelia Carvalhedo", "Shalom"]]
    for it, r in zip(itens, localizar_lote(itens)):
        if r and r.get("encontrado"):
            print(f"  ✅ {it['nome']:26} -> {r.get('nome_oficial')} | {r.get('endereco','')[:45]}")
        else:
            print(f"  ❌ {it['nome']:26} -> não encontrado")
