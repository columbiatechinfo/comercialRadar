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
import os
import asyncio
from pathlib import Path
from typing import Optional, List, Dict

import config


class ProxyPool:
    """O rodízio de IPs estáticos, com o país como primeiro recorte.

    O PAÍS NÃO É DETALHE. Desde 28/08/2026 o plano tem 250 IPs no Brasil e 250
    na Colômbia, e buscar endereço brasileiro por IP colombiano faz duas coisas
    ruins ao mesmo tempo: o Maps LOCALIZA o resultado pelo IP — devolve outro
    conjunto, com outra ordem e outro idioma — e um endereço de Canoas pedido
    de Bogotá é justamente o padrão que um detector procura.

    Por isso o pool nasce restrito a um país e os demais ficam RESERVADOS, não
    apagados: eles continuam no `resumo_completo()` para o monitor mostrar o
    plano inteiro, e passam a valer no dia em que houver cliente lá.
    """

    def __init__(self, pais: str | None = None):
        # `None` = sem restrição (o comportamento anterior, para quem só quer
        # inspecionar o plano). Quem MINERA passa o país da cidade.
        if pais is None:
            pais = getattr(config, "PROXY_PAIS", None) or ""
        self.pais = (pais or "").strip().upper() or None
        self._proxies: List[Dict] = []          # lista de dicts normalizados
        self._last_used: Dict[str, float] = {}  # proxy_id -> timestamp
        self._in_use: set = set()               # proxy_ids ocupados agora
        self._cooldown: Dict[str, float] = {}   # proxy_id -> expira_em (ts)
        self._burned: set = set()               # proxy_ids que já entraram em cooldown
        self._lock = asyncio.Lock()
        # Registro de consumo (migração 0041). `etapa` é preenchida por quem
        # minera, para o gráfico saber se o IP queimou na busca ou na captura.
        self._con = None
        self._buffer: List[tuple] = []
        self._tenant = (os.environ.get("CR_TENANT_ID") or "").strip() or None
        self.etapa: str | None = None

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
        self.registrar_inventario()
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
    def _do_pais(self, p: Dict) -> bool:
        """O IP entra no rodízio? Sem restrição, todos. Com restrição, os de lá
        — E OS DE PAÍS DESCONHECIDO.

        A última parte não é frouxidão, é defesa. Excluir quem não declara país
        faria o pool esvaziar EM SILÊNCIO no dia em que a Webshare parasse de
        mandar o campo: 500 IPs carregados, "0 livres", e a mineração parando
        sem uma linha que explique. Reservar exige prova de que o IP é de OUTRO
        lugar; a falta de informação não é essa prova.
        """
        if not self.pais:
            return True
        pais = (p.get("country") or "").upper()
        if not pais:
            return True
        return pais == self.pais

    def _disponivel(self, p: Dict, agora: float) -> bool:
        if not self._do_pais(p):
            return False
        pid = p["id"]
        if pid in self._in_use:
            return False
        exp = self._cooldown.get(pid)
        if exp and exp > agora:
            return False
        return True

    def em_castigo(self, proxy: Dict) -> bool:
        """Este IP esta de castigo agora?

        `_disponivel` ja sabia disso, e nao servia para quem escolhe o proxy
        por conta propria — `minerar_placeid` percorre `_proxies` por indice,
        porque precisa de um IP FIXO por navegador durante toda a rodada e nao
        de um emprestimo por requisicao. Faltava so poder perguntar.

        Sincrona de proposito: e leitura de um dicionario em memoria, e quem
        pergunta esta dentro de um laco apertado escolhendo entre centenas.
        """
        exp = self._cooldown.get((proxy or {}).get("id"))
        return bool(exp and exp > time.time())

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
            self._evento(escolhido, "pegou")
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
        # O CASTIGO CONTA SÓ O QUE ESTE POOL USA. Somar os IPs reservados de
        # outro país faria o log dizer "0 de castigo" com o rodízio inteiro
        # parado — e a conduta de quem lê essa linha mudaria para pior.
        meus = [p for p in self._proxies if self._do_pais(p)]
        castigo = sum(1 for p in meus
                      if (self._cooldown.get(p["id"]) or 0) > agora)
        livres = sum(1 for p in meus if self._disponivel(p, agora))
        return livres, castigo

    def resumo_completo(self) -> Dict:
        """O plano INTEIRO, para o monitor — não só a fatia que este pool usa.

        O `resumo()` responde a pergunta da mineração ("posso pegar um IP
        agora?") e por isso conta só o país ativo. Quem olha o monitor faz outra
        pergunta: "o que eu estou pagando, e em que estado está". Um IP
        reservado de outro país não é um IP quebrado, e some do `resumo()` — se
        o monitor usasse aquele número, metade do plano viraria invisível.

        Cada IP sai com o estado que decide a conduta de quem lê:

            livre      pronto para uso
            em_uso     um worker está com ele agora
            castigo    o Google barrou; volta quando o cooldown vencer
            reservado  é de outro país; não entra no rodízio de hoje
        """
        agora = time.time()
        itens = []
        for p in self._proxies:
            pid = p["id"]
            exp = self._cooldown.get(pid) or 0
            if not self._do_pais(p):
                estado = "reservado"
            elif pid in self._in_use:
                estado = "em_uso"
            elif exp > agora:
                estado = "castigo"
            else:
                estado = "livre"
            itens.append({
                "id": str(pid),
                # NUNCA a credencial: este resumo vai para a tela, e usuário e
                # senha do proxy são a chave do plano inteiro.
                "endereco": p.get("address", ""),
                "porta": p.get("port", ""),
                "pais": (p.get("country") or "").upper(),
                "cidade": p.get("city") or "",
                "estado": estado,
                "cooldown_ate": int(exp) if exp > agora else None,
                "queimado_na_run": pid in self._burned,
                "usado_em": int(self._last_used.get(pid, 0)) or None,
            })

        contagem = {}
        for i in itens:
            contagem[i["estado"]] = contagem.get(i["estado"], 0) + 1
        por_pais = {}
        for i in itens:
            por_pais[i["pais"] or "?"] = por_pais.get(i["pais"] or "?", 0) + 1

        return {
            "pais_ativo": self.pais,
            "total": len(itens),
            "por_estado": contagem,
            "por_pais": por_pais,
            "queimados_na_run": len(self._burned),
            "itens": itens,
        }

    async def acquire_blocking(self, intervalo: float = 2.0, tentativas: int = 60) -> Optional[Dict]:
        """Tenta adquirir, aguardando se todos estiverem ocupados/cooldown."""
        for _ in range(tentativas):
            p = await self.acquire()
            if p:
                return p
            await asyncio.sleep(intervalo)
        return None

    async def release(self, proxy: Dict, bytes_: int = None):
        async with self._lock:
            self._in_use.discard(proxy["id"])
            self._last_used[proxy["id"]] = time.time()
            self._evento(proxy, "devolveu", bytes_=bytes_)

    async def mark_cooldown(self, proxy: Dict, segundos: int = None,
                            motivo: str = None):
        segundos = segundos if segundos is not None else config.CAPTCHA_COOLDOWN_SEC
        async with self._lock:
            pid = proxy["id"]
            self._cooldown[pid] = time.time() + segundos
            self._in_use.discard(pid)
            self._burned.add(pid)
            self._evento(proxy, "castigo", motivo=motivo, segundos=segundos)
            print(f"   🔥 IP {proxy['address']}:{proxy['port']} em cooldown "
                  f"({segundos // 60}min)")

    async def reset_cooldowns(self):
        """Reset diário do estado de cooldown."""
        async with self._lock:
            self._cooldown.clear()

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────
    # Registro de consumo (migração 0041)
    #
    # MONITORAMENTO NUNCA DERRUBA MINERAÇÃO. Toda escrita daqui é
    # best-effort: se o banco estiver fora, o rodízio continua funcionando e o
    # que se perde é a linha do gráfico, não a run. Por isso cada método tem
    # `except Exception: pass` e nenhum deles é aguardado por quem minera.
    #
    # E É EM LOTE, não por evento. Um INSERT por `acquire` põe uma ida ao banco
    # dentro do caminho quente de dez workers; com o lote, são dezenas de
    # eventos numa viagem só. O buffer descarrega por tamanho ou no
    # `flush_eventos()`, que quem minera chama ao terminar.
    # ──────────────────────────────────────────────────────────────────

    _LOTE_EVENTOS = 50

    def _conexao(self):
        """Conexão preguiçosa, e só se alguém já tiver pedido registro."""
        if self._con is not None:
            return self._con
        try:
            import realtime_ingest
            self._con = realtime_ingest.conectar()
            self._con.autocommit = True
        except Exception:
            self._con = False          # False = já tentou e não deu
        return self._con or None

    def registrar_inventario(self):
        """Grava o PLANO como a Webshare o descreve. Chamado depois do `start`.

        O IP que sai do plano vira `ativo = false` em vez de sumir: o histórico
        de eventos continua apontando para ele, e apagá-lo transformaria consumo
        passado em linha órfã.
        """
        con = self._conexao()
        if not con or not self._proxies:
            return
        try:
            import psycopg2.extras
            linhas = [(str(p["id"]), p.get("address", ""), int(p.get("port") or 0),
                       (p.get("country") or "").upper(), p.get("city") or "")
                      for p in self._proxies]
            with con.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO proxy_ip (id, endereco, porta, pais, cidade,
                                          ativo, visto_em)
                    VALUES %s
                    ON CONFLICT (id) DO UPDATE
                       SET endereco = EXCLUDED.endereco,
                           porta    = EXCLUDED.porta,
                           pais     = EXCLUDED.pais,
                           cidade   = EXCLUDED.cidade,
                           ativo    = true,
                           visto_em = now()""",
                    linhas,
                    template="(%s, %s, %s, %s, %s, true, now())")
                cur.execute(
                    "UPDATE proxy_ip SET ativo = false "
                    " WHERE ativo AND NOT (id = ANY(%s))",
                    ([str(p["id"]) for p in self._proxies],))
        except Exception:
            pass

    def _evento(self, proxy: Dict, tipo: str, motivo: str = None,
                segundos: int = None, bytes_: int = None):
        self._buffer.append((self._tenant, str(proxy["id"]), tipo, motivo,
                             self.etapa, segundos, bytes_))
        if len(self._buffer) >= self._LOTE_EVENTOS:
            self.flush_eventos()

    def flush_eventos(self):
        """Descarrega o buffer. Chamável a qualquer momento, inclusive vazio."""
        if not self._buffer:
            return
        lote, self._buffer = self._buffer, []
        con = self._conexao()
        if not con:
            return                      # perdeu o registro, não a run
        try:
            import psycopg2.extras
            with con.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO proxy_evento
                           (id_empresa, proxy_id, tipo, motivo, etapa,
                            segundos, bytes)
                    VALUES %s""", lote,
                    template="(%s::uuid, %s, %s, %s, %s, %s::int, %s::bigint)")
        except Exception:
            pass

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
