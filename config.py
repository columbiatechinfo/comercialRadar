"""
config.py — Configuração central do ComercialRadar v2

Carrega o .env, expõe constantes do pipeline otimizado (banda + IPs) e as
listas de fingerprint (user-agents, viewports, timezones BR).

Importar sempre a partir daqui — nada de hardcode espalhado pelos módulos.
"""

import os
import sys
import json
import io
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# UTF-8 no Windows/PowerShell (emojis nos logs não quebram em cp1252)
# ──────────────────────────────────────────────────────────────────────────
def forcar_utf8():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                wrapper = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
                setattr(sys, stream_name, wrapper)
            except Exception:
                pass


forcar_utf8()

# ──────────────────────────────────────────────────────────────────────────
# Caminhos
# ──────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
USER_AGENTS_PATH = BASE_DIR / "user_agents.json"
PROXY_CACHE_PATH = BASE_DIR / ".proxy_cache.json"
PROXY_TXT_FALLBACK = BASE_DIR / "Webshare_100_proxies.txt"
BROWSER_PROFILES_DIR = BASE_DIR / ".browser_profiles"

# ──────────────────────────────────────────────────────────────────────────
# Loader do .env (python-dotenv; degrada para parser manual)
# ──────────────────────────────────────────────────────────────────────────
def _carregar_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except Exception:
        if ENV_PATH.exists():
            for linha in ENV_PATH.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, v = linha.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_carregar_env()

WEBSHARE_API_KEY = os.environ.get("WEBSHARE_API_KEY", "").strip()

# ──────────────────────────────────────────────────────────────────────────
# Webshare API v2
# ──────────────────────────────────────────────────────────────────────────
WEBSHARE_API_BASE = "https://proxy.webshare.io/api/v2/"
WEBSHARE_LIST_ENDPOINT = "proxy/list/?mode=direct&page=1&page_size=100"
WEBSHARE_REFRESH_ENDPOINT = "proxy/refresh/"
PROXY_CACHE_TTL_SEC = 3600          # cacheia a lista por 1h (respeita rate limit)

# ──────────────────────────────────────────────────────────────────────────
# Workers e lotes
# ──────────────────────────────────────────────────────────────────────────
MAX_WORKERS = 10                    # calibrado para o hardware-alvo; não subir
BATCH_MIN = 8                       # POIs por lote (mínimo)
BATCH_MAX = 15                      # POIs por lote (máximo)

# ──────────────────────────────────────────────────────────────────────────
# Clustering espacial (regionalização)
# ──────────────────────────────────────────────────────────────────────────
DBSCAN_EPS_M = 300                  # raio de vizinhança (metros)
DBSCAN_MIN_SAMPLES = 2

# ──────────────────────────────────────────────────────────────────────────
# Cadência humanizada
# ──────────────────────────────────────────────────────────────────────────
DELAY_MIN_S = 2.0                   # delay entre buscas (máx 5s — humanização leve)
DELAY_MAX_S = 5.0
LONG_PAUSE_CHANCE = 0.03            # 3% de chance de pausa curta
LONG_PAUSE_MIN_S = 12.0
LONG_PAUSE_MAX_S = 25.0
EXPLORE_MIN_S = 1.0                 # "exploração" do painel antes de extrair
EXPLORE_MAX_S = 2.5

# ──────────────────────────────────────────────────────────────────────────
# Esperas por resultado (esperar o Google trazer, em vez de desistir rápido)
# ──────────────────────────────────────────────────────────────────────────
WAIT_PAINEL_MS = 12000             # quanto esperar o painel do POI aparecer
WAIT_LISTA_MS = 9000               # quanto esperar a lista de resultados
# TETO PARA O BOTÃO "Próximo" APARECER. Foi 9.000 até 26/08/2026, e nove
# segundos era um segundo de folga sobre a realidade — por isso a etapa
# quebrava sem motivo aparente quando a rede piorava.
#
# MEDIDO na noite do conserto, coordenadas reais da área de Canoas, via proxy:
#
#     primeira coordenada (sessão fria) .... 8,0 s
#     as quatro seguintes .................. 0,0 s  (o botão persiste)
#
# Oito contra nove. Numa rede boa passava; no roteador do celular, a mesma run
# deu 3 sucessos e 13 "botão Próximo não encontrado" — o erro dizia que não
# existia, quando ele só ainda não tinha pintado.
#
# SUBIR ISTO É QUASE DE GRAÇA: `wait_for` devolve no instante em que o elemento
# fica visível, então o teto só é pago quando ele REALMENTE não vem. E como o
# botão persiste entre POIs do mesmo lote, na prática só a primeira coordenada
# de cada sessão chega perto do limite.
WAIT_PROXIMO_MS = 25000            # quanto esperar o botão "Próximo"
WAIT_APOS_BUSCA_MS = 1200          # respiro curto após Enter (o resto é espera ativa)

# ──────────────────────────────────────────────────────────────────────────
# Matching (idêntico ao v1 para compatibilidade de schema)
# ──────────────────────────────────────────────────────────────────────────
MIN_OCR_LEN = 5
MAX_DIST_M = 100

# Tetos de coleta por POI (teto baixo = rápido/econômico)
MAX_FOTOS = 10
MAX_REVIEWS = 15
SIMILARIDADE_FORTE = 0.90           # match por nome até 2km
SIM_CARD_MIN = 0.60                 # similaridade mínima ao escolher card da lista

# ──────────────────────────────────────────────────────────────────────────
# Cooldown de IPs
# ──────────────────────────────────────────────────────────────────────────
CAPTCHA_COOLDOWN_SEC = 2 * 3600     # 2h ao detectar CAPTCHA/429

# ──────────────────────────────────────────────────────────────────────────
# Fingerprint
# ──────────────────────────────────────────────────────────────────────────
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
]

# Timezones BR coerentes com locale pt-BR. A seleção por geo do POI é feita em
# human_browser.tz_para_lng() quando a longitude está disponível.
TIMEZONES_BR = [
    "America/Sao_Paulo",
    "America/Bahia",
    "America/Fortaleza",
    "America/Recife",
    "America/Belem",
    "America/Cuiaba",
    "America/Campo_Grande",
    "America/Manaus",
]

LOCALE = "pt-BR"


def carregar_user_agents() -> list:
    try:
        return json.loads(USER_AGENTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ]


USER_AGENTS = carregar_user_agents()


# ──────────────────────────────────────────────────────────────────────────
# Bloqueio de recursos (route blocking) por camada
# ──────────────────────────────────────────────────────────────────────────
# Tipos sempre bloqueados (qualquer camada)
BLOCK_RESOURCE_TYPES_ALWAYS = {"stylesheet", "font", "media"}

# Padrões de URL de telemetria/ads sempre bloqueados
BLOCK_URL_SUBSTRINGS = (
    "googleads",
    "doubleclick",
    "google-analytics",
    "googletagmanager",
    "gstatic.com/recaptcha",
    "/gen_204",
    "play.google.com/log",
    "googlesyndication",
)

# Avatars de reviewers (não precisamos) — bloqueados na coleta rica
AVATAR_URL_SUBSTRINGS = (
    "googleusercontent.com/a-/",
    "googleusercontent.com/a/",
)
