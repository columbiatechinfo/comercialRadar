"""
proxy_pool.py — Pool de IPs estáticos residenciais (Webshare API v2)

- Baixa os 100 IPs estáticos no startup via /api/v2/proxy/list/?mode=direct.
- Cacheia a lista em .proxy_cache.json por 1h (respeita rate limit da API).
- Distribui 1 IP por lote (acquire/release), preferindo o menos usado.
- Marca IP em cooldown (2h) ao detectar CAPTCHA/429 — "queima" controlada.
- Reset de cooldown sob demanda (reset_cooldowns) e auto-expiração por tempo.

Sem credenciais hardcoded: a chave vem de config.WEBSHARE_API_KEY (.env).
Fallback gracioso: cache local → Webshare_100_proxies.txt → erro claro.

Proxy nativo do Playwright: use to_playwright(proxy) no new_context/persistent.
"""

import json
import time
import urllib.request
import urllib.error
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

import config


class ProxyPool:
    def __init__(self):
        self._proxies: List[Dict] = []          # lista de dicts normalizados
        self._last_used: Dict[str, float] = {}  # proxy_id -> timestamp
        self._in_use: set = set()               # proxy_ids ocupados agora
        self._cooldown: Dict[str, float] = {}   # proxy_id -> expira_em (ts)
        self._burned: set = set()               # proxy_ids que já entraram em cooldown
        self._lock = asyncio.Lock()

    # ──────────────────────────────────────────────────────────────────
    # Carregamento
    # ──────────────────────────────────────────────────────────────────
    def start(self):
        """Carrega o pool (API → cache → txt). Síncrono; chamado no startup."""
        proxies = self._carregar_da_api()
        origem = "API Webshare"

        if not proxies:
            proxies = self._carregar_do_cache(ignorar_ttl=True)
            origem = "cache local (.proxy_cache.json)"

        if not proxies:
            proxies = self._carregar_do_txt()
            origem = "Webshare_100_proxies.txt"

        if not proxies:
            raise RuntimeError(
                "Nenhuma fonte de proxies disponível. "
                "Configure WEBSHARE_API_KEY no .env ou forneça Webshare_100_proxies.txt."
            )

        self._proxies = proxies
        for p in proxies:
            self._last_used[p["id"]] = 0.0

        print(f"   🌐 {len(proxies)} IPs estáticos carregados ({origem})")
        return self

    def _carregar_da_api(self) -> List[Dict]:
        if not config.WEBSHARE_API_KEY:
            print("   ⚠️  WEBSHARE_API_KEY ausente no .env — pulando API")
            return []

        # Cache fresco (< TTL) evita bater na API repetidamente
        cache = self._carregar_do_cache(ignorar_ttl=False)
        if cache:
            print(f"   🧊 Usando cache de proxies (< {config.PROXY_CACHE_TTL_SEC // 60}min)")
            return cache

        url = config.WEBSHARE_API_BASE + config.WEBSHARE_LIST_ENDPOINT
        proxies: List[Dict] = []
        try:
            while url:
                req = urllib.request.Request(
                    url, headers={"Authorization": f"Token {config.WEBSHARE_API_KEY}"}
                )
                data = json.loads(urllib.request.urlopen(req, timeout=20).read())
                for r in data.get("results", []):
                    if not r.get("valid", True):
                        continue
                    addr = r["proxy_address"]
                    port = r["port"]
                    proxies.append({
                        "id": r.get("id", f"{addr}:{port}"),
                        "address": addr,
                        "port": port,
                        "username": r.get("username", ""),
                        "password": r.get("password", ""),
                        "server": f"http://{addr}:{port}",
                        "country": r.get("country_code", ""),
                        "city": r.get("city_name", ""),
                    })
                url = data.get("next")
        except urllib.error.HTTPError as e:
            print(f"   ⚠️  API Webshare HTTP {e.code}: {e.reason}")
            return []
        except Exception as e:
            print(f"   ⚠️  API Webshare falhou: {e}")
            return []

        if proxies:
            self._salvar_cache(proxies)
        return proxies

    def _carregar_do_cache(self, ignorar_ttl: bool) -> List[Dict]:
        try:
            if not config.PROXY_CACHE_PATH.exists():
                return []
            blob = json.loads(config.PROXY_CACHE_PATH.read_text(encoding="utf-8"))
            ts = blob.get("timestamp", 0)
            if not ignorar_ttl and (time.time() - ts) > config.PROXY_CACHE_TTL_SEC:
                return []
            return blob.get("proxies", [])
        except Exception:
            return []

    def _salvar_cache(self, proxies: List[Dict]):
        try:
            config.PROXY_CACHE_PATH.write_text(
                json.dumps({"timestamp": time.time(), "proxies": proxies},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _carregar_do_txt(self) -> List[Dict]:
        """Fallback: Webshare_100_proxies.txt (formato host:port:user:pwd)."""
        txt = config.PROXY_TXT_FALLBACK
        if not txt.exists():
            return []
        proxies = []
        for i, linha in enumerate(txt.read_text(encoding="utf-8").splitlines()):
            linha = linha.strip()
            if not linha:
                continue
            partes = linha.split(":")
            if len(partes) != 4:
                continue
            host, port, user, pwd = partes
            proxies.append({
                "id": f"txt-{i}-{user}",
                "address": host,
                "port": int(port),
                "username": user,
                "password": pwd,
                "server": f"http://{host}:{port}",
                "country": "",
                "city": "",
            })
        return proxies

    # ──────────────────────────────────────────────────────────────────
    # Aquisição / liberação
    # ──────────────────────────────────────────────────────────────────
    def _disponivel(self, p: Dict, agora: float) -> bool:
        pid = p["id"]
        if pid in self._in_use:
            return False
        exp = self._cooldown.get(pid)
        if exp and exp > agora:
            return False
        return True

    async def acquire(self) -> Optional[Dict]:
        """Retorna um proxy livre (o menos usado recentemente) e o marca em uso."""
        async with self._lock:
            agora = time.time()
            candidatos = [p for p in self._proxies if self._disponivel(p, agora)]
            if not candidatos:
                return None
            escolhido = min(candidatos, key=lambda p: self._last_used.get(p["id"], 0.0))
            self._in_use.add(escolhido["id"])
            self._last_used[escolhido["id"]] = agora
            return escolhido

    def resumo(self) -> tuple:
        """(livres, de castigo) agora. Para quem precisa DIZER por que não pegou.

        Quando `acquire_blocking` devolve None, quem chama tem duas leituras
        possíveis, e a conduta muda em cada uma: se os IPs estão todos EM USO,
        é só esperar; se estão de CASTIGO, o Google barrou e insistir queima o
        resto do pool. Sem estes dois números a mensagem de erro não ajuda
        ninguém a decidir.

        Deliberadamente SEM lock: é leitura para log, chamada num caminho de
        falha, e travar aqui seria pior que um número um instante velho.
        """
        agora = time.time()
        castigo = sum(1 for p in self._proxies
                      if (self._cooldown.get(p["id"]) or 0) > agora)
        livres = sum(1 for p in self._proxies if self._disponivel(p, agora))
        return livres, castigo

    async def acquire_blocking(self, intervalo: float = 2.0, tentativas: int = 60) -> Optional[Dict]:
        """Tenta adquirir, aguardando se todos estiverem ocupados/cooldown."""
        for _ in range(tentativas):
            p = await self.acquire()
            if p:
                return p
            await asyncio.sleep(intervalo)
        return None

    async def release(self, proxy: Dict):
        async with self._lock:
            self._in_use.discard(proxy["id"])
            self._last_used[proxy["id"]] = time.time()

    async def mark_cooldown(self, proxy: Dict, segundos: int = None):
        segundos = segundos if segundos is not None else config.CAPTCHA_COOLDOWN_SEC
        async with self._lock:
            pid = proxy["id"]
            self._cooldown[pid] = time.time() + segundos
            self._in_use.discard(pid)
            self._burned.add(pid)
            print(f"   🔥 IP {proxy['address']}:{proxy['port']} em cooldown "
                  f"({segundos // 60}min)")

    async def reset_cooldowns(self):
        """Reset diário do estado de cooldown."""
        async with self._lock:
            self._cooldown.clear()

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def to_playwright(proxy: Dict) -> Dict:
        """Formato aceito por new_context(proxy=...) / launch_persistent_context."""
        # Credencial só entra se existir. Proxy de RELAY vem sem usuário — e
        # mandar usuário vazio ao Chromium ainda o joga no caminho de proxy
        # autenticado, que é o que pendura o google.com por 35 s.
        cfg = {"server": proxy["server"]}
        if proxy.get("username"):
            cfg["username"] = proxy["username"]
            cfg["password"] = proxy.get("password", "")
        return cfg

    @property
    def total(self) -> int:
        return len(self._proxies)

    @property
    def burned_count(self) -> int:
        return len(self._burned)

    def disponiveis_agora(self) -> int:
        agora = time.time()
        return sum(1 for p in self._proxies if self._disponivel(p, agora))


if __name__ == "__main__":
    pool = ProxyPool().start()
    print(f"Total: {pool.total} | Disponíveis: {pool.disponiveis_agora()}")

    async def _t():
        p = await pool.acquire()
        print("acquire:", p["address"], p["port"], p["country"], p["city"])
        print("playwright:", ProxyPool.to_playwright(p))
        await pool.release(p)

    asyncio.run(_t())
