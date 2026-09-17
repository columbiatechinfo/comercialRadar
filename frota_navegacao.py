# -*- coding: utf-8 -*-
"""frota_navegacao.py — a frota única de navegação (dono do produto, 17/09/2026).

Todo passo que abre site de coleta passa a pedir navegador a ESTA frota, e não mais a abrir o seu. As 10 regras foram
decididas uma a uma (memória `frota-de-navegacao-regras`); as que moram aqui:

    Camoufox em tudo              um navegador por vaga, com o IP e o cookie daquele site
    vive até degradar             sem prazo fixo: fecha por bloqueio, CAPTCHA não passado, vazio ou erro em sequência,
                                  memória; e só então abre outro, com outro IP
    castigo por site              15 min → 1 h → 6 h → 24 h, no banco (`navegacao.castigar`), visto por todas as máquinas
    fila, nunca o IP da casa      sem IP livre a vaga ESPERA; não existe caminho sem proxy aqui dentro
    Brasil antes da Colômbia      a Colômbia só entra quando todo IP brasileiro está ocupado ou de castigo
    cookie no banco               `navegacao.cookie` por site × IP, reaproveitado por qualquer máquina que pegar o IP
    tudo registrado               uma linha por tarefa em `navegacao.evento` (resultado, ms, bytes); resumo diário

O QUE A FROTA NÃO FAZ: não resolve CAPTCHA. Quem chama detecta o desafio que não passou e levanta `Captcha`; para a
frota isso é degradação — fecha, castiga o IP naquele site e a tarefa volta para a fila em outro navegador.

POR QUE UMA THREAD POR VAGA: a API síncrona do Playwright não atravessa threads. Cada vaga é dona do seu navegador e
só ela o toca; quem chama só entrega tarefas (`enviar`) e recebe um `Future`, de código síncrono ou assíncrono
(`asyncio.wrap_future`).

UMA CONEXÃO POR FROTA: o pooler tem 20 sessões para tudo. Eventos, contadores e renovação de reserva saem em lote por
uma thread de faxina, a cada poucos segundos.

USO
    from frota_navegacao import Frota, Bloqueio, Captcha

    def loja(p, url):
        p.ir(url)
        if "Confirme que é humano" in p.page.inner_text("body"):
            raise Captcha("turnstile não passou")
        return p.page.content()

    with Frota("ifood", navegadores=6, aquecer="https://www.ifood.com.br/") as frota:
        for f in [frota.enviar(loja, u) for u in urls]:
            html = f.result()

    python3 frota_navegacao.py --teste --navegadores 3 --tarefas 30 --bloquear-a-cada 7
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import queue
import socket
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

MAQUINA = (os.environ.get("RADAR_MAQUINA") or socket.gethostname())[:40]
# O DONO DIZ A MÁQUINA E O CONTÊINER (painel da frota, 17/09/2026): o pid é do espaço do contêiner, e dois contêineres
# no mesmo i9 com o controlador no pid 7 se confundiam. "i9/98661de5f6de" liga cada sessão ao navegador exato.
_HOST = socket.gethostname()[:20]
DONO_MAQUINA = MAQUINA if _HOST == MAQUINA else "%s/%s" % (MAQUINA, _HOST)
RESERVA_S = 1800            # prazo da reserva do IP; renovada enquanto o navegador vive
RENOVAR_S = 300
COOKIE_S = 600              # de quanto em quanto tempo o cookie da sessão viva vai para o banco
FAXINA_S = 5
ESPERA_FILA = (15, 120)     # sem IP livre: espera 15 s, dobrando até 2 min
LIMITE_FALHAS = int(os.environ.get("NAV_LIMITE_FALHAS", "3"))
MEMORIA_MB = int(os.environ.get("NAV_MEMORIA_MB", "2500"))
IP_DA_CASA = {x.strip() for x in os.environ.get("NAV_IP_DA_CASA", "191.177.146.237").split(",") if x.strip()}


class Bloqueio(Exception):
    """O site recusou este IP (403/429, página de bloqueio). Castiga o IP no site e fecha o navegador."""


class Captcha(Bloqueio):
    """Desafio que o navegador não passou. Mesmo tratamento do bloqueio."""


class ErroDaPagina(Exception):
    """O site não entregou o que a tarefa precisava (elemento que não veio, passo que não aplicou). Fecha o navegador
    na sequência, como qualquer falha, mas NÃO castiga o IP: não é o IP que está ruim."""


class Degradou(Exception):
    """Quem chama pede o fechamento do navegador (estado estranho), sem castigar o IP."""


# ── banco ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class _Banco:
    def __init__(self):
        self._trava = threading.Lock()
        self.con = None
        self._abrir()

    def _abrir(self):
        import realtime_ingest
        self.con = realtime_ingest.conectar()
        self.con.autocommit = True
        with self.con.cursor() as cur:
            cur.execute("set client_min_messages = warning")

    def executar(self, sql, args=(), buscar=None, lote=None, modelo=None):
        """buscar: None | 'um' | 'todos'. lote: linhas para `execute_values`."""
        import psycopg2
        import psycopg2.extras
        with self._trava:
            for tentativa in (1, 2):
                try:
                    with self.con.cursor() as cur:
                        if lote is not None:
                            psycopg2.extras.execute_values(cur, sql, lote, template=modelo, page_size=500)
                        else:
                            cur.execute(sql, args)
                        if buscar == "um":
                            return cur.fetchone()
                        if buscar == "todos":
                            return cur.fetchall()
                        return None
                except (psycopg2.OperationalError, psycopg2.InterfaceError):
                    if tentativa == 2:
                        raise
                    time.sleep(3)
                    self._abrir()

    def fechar(self):
        try:
            self.con.close()
        except Exception:                                      # noqa: BLE001
            pass


def _inventario(banco, log):
    """Os IPs do plano (API da Webshare → cache → txt, o mesmo carregador do `ProxyPool`) para `navegacao.proxy`.

    Devolve {id: dict com a credencial}. A credencial fica só na memória: o banco guarda endereço, porta e país."""
    from proxy_pool import ProxyPool
    pool = ProxyPool(pais="")
    lista = pool._carregar_da_api() or pool._carregar_do_cache(ignorar_ttl=True) or pool._carregar_do_txt()
    if not lista:
        raise RuntimeError("sem inventário de proxy (API da Webshare, cache e txt vazios) — a frota não sobe sem IP")
    linhas = [(str(p["id"]), p.get("address", ""), int(p.get("port") or 0), (p.get("country") or "").upper(),
               p.get("city") or "") for p in lista]
    banco.executar("""insert into navegacao.proxy (id, endereco, porta, pais, cidade) values %s
                      on conflict (id) do update set endereco = excluded.endereco, porta = excluded.porta,
                             pais = excluded.pais, cidade = excluded.cidade, ativo = true, visto_em = now()""",
                   lote=linhas)
    # quem saiu do plano fica inativo, não some: o histórico de eventos segue apontando para ele
    banco.executar("update navegacao.proxy set ativo = false where ativo and not (id = any(%s))",
                   ([ln[0] for ln in linhas],))
    log("frota: %d IPs no inventário (%s)" % (len(lista), ", ".join(
        "%s %d" % (k, sum(1 for ln in linhas if ln[3] == k)) for k in sorted({ln[3] for ln in linhas}))))
    return {str(p["id"]): p for p in lista}


def _proxy_playwright(p):
    cfg = {"server": p.get("server") or "http://%s:%s" % (p["address"], p["port"])}
    if p.get("username"):                    # sem usuário não se manda credencial vazia (memória proxies-chromium-google)
        cfg["username"] = p["username"]
        cfg["password"] = p.get("password", "")
    return cfg


# ── a página que a tarefa recebe ──────────────────────────────────────────────────────────────────────────────────
class Pagina:
    """O que a função da tarefa recebe: a `page` viva do Playwright e dois atalhos."""

    def __init__(self, sessao):
        self._s = sessao
        self.page = sessao.page
        self.contexto = sessao.ctx
        self.proxy_id = sessao.proxy_id
        self.sessao_id = sessao.id
        self.tarefas_anteriores = sessao.paginas     # 0 = primeira tarefa deste navegador (a verificação vem nela)
        self.resultado = "ok"
        self.detalhe = None
        self.fechar_pedido = None            # (motivo, castigar): fecha o navegador DEPOIS de entregar o resultado

    def ir(self, url, espera="domcontentloaded", timeout=45000, http_bloqueio=True):
        """goto que castiga o IP em 403/429 (levanta `Bloqueio`) e guarda a URL no evento.

        `http_bloqueio=False` em site atrás de Cloudflare: a página de verificação chega com 403 ENQUANTO o navegador
        ainda tenta passar, e decidir no status seria castigar o IP antes da tentativa (regra 2). Aí quem decide é a
        tarefa, olhando a página depois de esperar."""
        self._s.ultima_url = url
        resp = self.page.goto(url, wait_until=espera, timeout=timeout)
        st = resp.status if resp is not None else None
        if http_bloqueio and st in (403, 429):
            raise Bloqueio("http %d" % st)
        return resp

    def texto(self):
        """O texto visível da página, tolerando a navegação no meio da leitura (a verificação que passa recarrega)."""
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception as e:                                 # noqa: BLE001
            if "context was destroyed" in str(e) or "navigation" in str(e).lower():
                return ""
            raise

    def degradar(self, motivo, castigar=False):
        """A tarefa deu certo mas o navegador não serve mais (ex.: a verificação ficou na tela). O resultado é
        entregue, e só então a vaga fecha o navegador — com castigo NESTE site quando `castigar`."""
        self.fechar_pedido = (str(motivo)[:150], bool(castigar))
        if castigar:
            self.resultado = "captcha"

    def marcar(self, resultado, detalhe=None):
        """'vazio' quando a página veio sem o que devia: conta na sequência de falhas que degrada o navegador."""
        self.resultado = resultado
        self.detalhe = detalhe


# ── um navegador vivo ─────────────────────────────────────────────────────────────────────────────────────────────
class _Sessao:
    def __init__(self, frota, vaga, proxy_id):
        self.frota = frota
        self.vaga = vaga
        self.proxy_id = proxy_id
        self.dono = "%s:%d:%s:%d" % (DONO_MAQUINA, os.getpid(), frota.site, vaga)
        self.id = None
        self.cm = self.nav = self.ctx = self.page = None
        self.pids = set()
        self.paginas = 0
        self.falhas_seguidas = 0
        self.bytes = 0
        self.ultima_url = None
        self.aberta = time.time()
        self.cookie_em = time.time()
        self.reserva_perdida = False
        self.sujo = True
        self.fase = "lancar"
        self.ultimo_resultado = None       # o painel da frota lê da sessão, sem buscar em `evento` (0122)
        self.ultimo_em = None

    def _conta_bytes(self, resp):
        try:
            n = resp.headers.get("content-length")
            if n:
                self.bytes += int(n)
        except Exception:                                      # noqa: BLE001
            pass

    def abrir(self):
        from camoufox.sync_api import Camoufox
        f = self.frota
        with f._lancar:                        # um lançamento por vez: o pico de CPU e a conta dos processos novos
            antes = _filhos()
            opcoes = dict(headless=True, geoip=True, locale="pt-BR")
            opcoes.update(f.opcoes)
            self.cm = Camoufox(proxy=_proxy_playwright(f.proxies[self.proxy_id]), **opcoes)
            self.nav = self.cm.__enter__()
            self.pids = _filhos() - antes
        linha = f.banco.executar("""select estado from navegacao.cookie
                                     where site = %s and proxy_id = %s and valido""",
                                 (f.site, self.proxy_id), buscar="um") if f.usar_cookie else None
        self.ctx = self.nav.new_context(storage_state=linha[0] if linha else None, **f.contexto)
        self.page = self.ctx.new_page()
        self.page.on("response", self._conta_bytes)
        # O DONO NO MESMO FORMATO DA RESERVA (0122): o painel da frota cruza processo, sessão e IP por ele
        self.id = f.banco.executar("""insert into navegacao.sessao (maquina, processo, site, proxy_id, estado, dono)
                                       values (%s, %s, %s, %s, 'aquecendo', %s) returning id""",
                                   (MAQUINA, f.processo, f.site, self.proxy_id, self.dono), buscar="um")[0]
        self.fase = "aquecer"
        if f.aquecer:
            resp = self.page.goto(f.aquecer, wait_until="domcontentloaded", timeout=60000)
            if resp is not None and resp.status in (403, 429):
                raise Bloqueio("aquecer: http %d" % resp.status)
        f.banco.executar("update navegacao.sessao set estado = 'ativa', ultima_atividade = now() where id = %s",
                         (self.id,))
        f.log("frota %s · vaga %d: navegador aberto no IP %s%s" % (
            f.site, self.vaga, f.proxies[self.proxy_id].get("address"), " (com cookie)" if linha else ""))

    def memoria_mb(self):
        try:
            import psutil
            total = 0
            for pid in list(self.pids):
                try:
                    pr = psutil.Process(pid)
                    total += pr.memory_info().rss + sum(c.memory_info().rss for c in pr.children(recursive=True))
                except psutil.NoSuchProcess:
                    self.pids.discard(pid)
            return total / 1048576
        except Exception:                                      # noqa: BLE001
            return 0

    def salvar_cookie(self):
        if not self.frota.usar_cookie:
            return
        try:
            estado = self.ctx.storage_state()
        except Exception:                                      # noqa: BLE001
            return
        self.frota.banco.executar(
            """insert into navegacao.cookie (site, proxy_id, estado) values (%s, %s, %s::jsonb)
               on conflict (site, proxy_id) do update set estado = excluded.estado, valido = true, usado_em = now()""",
            (self.frota.site, self.proxy_id, json.dumps(estado)))
        self.cookie_em = time.time()

    def fechar(self, motivo, castigar=False, cookie_valido=True):
        f = self.frota
        if cookie_valido and self.paginas and self.ctx is not None:
            self.salvar_cookie()
        try:
            if self.cm is not None:
                self.cm.__exit__(None, None, None)
        except Exception:                                      # noqa: BLE001
            pass
        self.cm = self.nav = self.ctx = self.page = None
        b = f.banco
        if castigar:                           # o castigo antes de soltar: nenhuma outra vaga pega o IP no intervalo
            ate = b.executar("""select to_char(navegacao.castigar(%s, %s, %s) at time zone 'America/Sao_Paulo',
                                                'HH24:MI')""", (f.site, self.proxy_id, motivo[:200]), buscar="um")[0]
            f.contagem["castigos"] += 1
            f.log("frota %s · vaga %d: IP %s de castigo até %s (%s)" % (
                f.site, self.vaga, f.proxies[self.proxy_id].get("address"), ate, motivo))
        if not cookie_valido:
            b.executar("update navegacao.cookie set valido = false where site = %s and proxy_id = %s",
                       (f.site, self.proxy_id))
        b.executar("select navegacao.soltar_proxy(%s, %s, %s)", (f.site, self.proxy_id, self.dono))
        if self.id is not None:
            b.executar("""update navegacao.sessao set estado = 'fechada', fechada_em = now(), motivo_fechamento = %s,
                                 paginas = %s, falhas_seguidas = %s, ultima_atividade = now(),
                                 ultimo_resultado = coalesce(%s, ultimo_resultado),
                                 ultimo_em = coalesce(to_timestamp(%s), ultimo_em) where id = %s""",
                       (motivo[:200], self.paginas, self.falhas_seguidas, self.ultimo_resultado, self.ultimo_em,
                        self.id))


def _filhos():
    try:
        import psutil
        return {c.pid for c in psutil.Process().children(recursive=False)}
    except Exception:                                          # noqa: BLE001
        return set()


# ── a frota ───────────────────────────────────────────────────────────────────────────────────────────────────────
class Frota:
    def __init__(self, site, navegadores=4, paises=("BR", "CO"), aquecer=None, opcoes=None, contexto=None,
                 tentativas=3, limite_falhas=LIMITE_FALHAS, memoria_mb=MEMORIA_MB, processo=None, log=print,
                 apenas_ips=None, castigo="evitar", usar_cookie=True):
        self.site = site
        self.paises = list(paises)
        # SÓ PARA TESTE (dono do produto, 17/09/2026: "testa os IPs punidos"): usa apenas estes ids de proxy, na ordem,
        # ignorando castigo. A reserva continua valendo — duas vagas nunca pegam o mesmo IP.
        self.apenas_ips = list(apenas_ips or [])
        # 'evitar' (padrão), 'preferir' ou 'indiferente' (0124). No iFood o IP castigado RENDE MAIS: a sessão recusada
        # cai no feed de reserva, que traz a lista inteira, enquanto a aceita traz 20 lojas e cobra verificação para ver
        # o resto. Quem escolhe é o passo, não a frota.
        self.castigo = castigo
        # COOKIE GUARDADO NÃO SERVE A TODO SITE (medido no iFood em 17/09/2026): lá o cookie carrega o estado de um IP
        # que já posicionou uma praça, e com ele a coordenada não aplica mais. Quem sabe disso é o passo.
        self.usar_cookie = usar_cookie
        self.aquecer = aquecer
        self.opcoes = dict(opcoes or {})
        self.contexto = dict(contexto or {})
        self.tentativas = tentativas
        self.limite_falhas = limite_falhas
        self.memoria_limite = memoria_mb
        self.processo = processo or os.path.basename(sys.argv[0] or "python")[:60]
        self.log = log
        self.banco = _Banco()
        self.proxies = _inventario(self.banco, log)
        self._manutencao_diaria()
        self.fila = queue.Queue()
        self._parar = threading.Event()
        self._lancar = threading.Lock()
        self._eventos = []
        self._ok = {}
        self._trava = threading.Lock()
        self.vivas = {}
        self.contagem = {"ok": 0, "falhas": 0, "castigos": 0, "aberturas": 0, "espera_ip_s": 0}
        self.vagas = [threading.Thread(target=self._vaga, args=(i,), name="%s-%d" % (site, i), daemon=True)
                      for i in range(navegadores)]
        self._faxineira = threading.Thread(target=self._faxina, name="%s-faxina" % site, daemon=True)
        self._faxineira.start()
        for t in self.vagas:
            t.start()

    # quem chama
    def enviar(self, funcao, *args, **kw) -> cf.Future:
        fut = cf.Future()
        self.fila.put((funcao, args, kw, fut, 0))
        return fut

    def esperar(self):
        self.fila.join()

    def fechar(self, esperar=True):
        if esperar:
            self.esperar()
        self._parar.set()
        for t in self.vagas:
            t.join(timeout=90)
        self._faxineira.join(timeout=30)
        self._descarregar()
        self.banco.fechar()

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_):
        self.fechar(esperar=tipo is None)

    def resumo(self):
        return dict(self.contagem, vivas=len(self.vivas), na_fila=self.fila.qsize())

    # a vaga
    def _pegar_ip(self, vaga):
        espera, avisado = ESPERA_FILA[0], 0.0
        dono = "%s:%d:%s:%d" % (DONO_MAQUINA, os.getpid(), self.site, vaga)
        while not self._parar.is_set():
            if self.apenas_ips:
                pid = None
                for cand in self.apenas_ips:
                    r = self.banco.executar(
                        """insert into navegacao.reserva as r (site, proxy_id, dono, ate)
                           values (%s, %s, %s, now() + make_interval(secs => %s))
                           on conflict (site, proxy_id) do update set dono = excluded.dono, ate = excluded.ate, em = now()
                              where r.ate <= now()
                           returning r.proxy_id""", (self.site, cand, dono, RESERVA_S), buscar="um")
                    if r:
                        pid = r[0]
                        break
            else:
                pid = self.banco.executar("select navegacao.pegar_proxy(%s, %s, %s, %s, %s)",
                                          (self.site, dono, RESERVA_S, self.paises, self.castigo), buscar="um")[0]
            if pid and pid in self.proxies:
                return pid
            if pid:                            # ativo no banco e ausente nesta carga: devolve e recarrega o plano
                self.banco.executar("select navegacao.soltar_proxy(%s, %s, %s)", (self.site, pid, dono))
                self.proxies = _inventario(self.banco, self.log)
                continue
            if time.time() - avisado > 60:
                self.log("frota %s · vaga %d: sem IP livre (todos ocupados ou de castigo) — na fila, sem IP da casa"
                         % (self.site, vaga))
                avisado = time.time()
            self._evento(None, None, "fila", espera * 1000, 0, "sem IP livre")
            self.contagem["espera_ip_s"] += espera
            self._parar.wait(espera)
            espera = min(espera * 2, ESPERA_FILA[1])
        return None

    def _abrir(self, vaga):
        locais = 0
        while not self._parar.is_set():
            pid = self._pegar_ip(vaga)
            if pid is None:
                return None
            s = _Sessao(self, vaga, pid)
            try:
                s.abrir()
                self.contagem["aberturas"] += 1
                return s
            except Exception as e:                             # noqa: BLE001
                # NÃO AQUECEU neste IP, ou o proxy recusou: castiga NESTE site e tenta outro. Falha do próprio Camoufox
                # (binário, memória da máquina) não é culpa do IP — castigar aí queimaria o plano inteiro em minutos.
                do_ip = s.fase == "aquecer" or _erro_de_proxy(e)
                self._evento(s.id, pid, _classe_do_erro(e), None, s.bytes, "abrir: %s" % _curto(e))
                s.fechar("abrir: %s" % _curto(e), castigar=do_ip, cookie_valido=not do_ip)
                if not do_ip:
                    locais += 1
                    self.log("frota %s · vaga %d: o Camoufox não abriu (%s)%s" % (
                        self.site, vaga, _curto(e), " — espera 60 s" if locais >= 3 else ""))
                    if locais >= 3:
                        self._parar.wait(60)
                        locais = 0
        return None

    def _vaga(self, vaga):
        s = None
        while True:
            try:
                item = self.fila.get(timeout=1)
            except queue.Empty:
                if self._parar.is_set():
                    break
                if s is not None and time.time() - s.cookie_em > COOKIE_S:
                    s.salvar_cookie()
                continue
            try:
                s = self._uma(vaga, s, item)
            except Exception as e:                             # noqa: BLE001
                # defeito da própria frota: a vaga não pode morrer calada e deixar quem espera pendurado
                self.log("frota %s · vaga %d: defeito da frota: %s" % (self.site, vaga, _curto(e)))
                traceback.print_exc()
                fut = item[3]
                if not fut.done():
                    fut.set_exception(e)
                if s is not None:
                    self.vivas.pop(s.id, None)
                    try:
                        s.fechar("defeito da frota: %s" % _curto(e))
                    except Exception:                          # noqa: BLE001
                        pass
                s = None
            finally:
                self.fila.task_done()
        if s is not None:
            self.vivas.pop(s.id, None)
            s.fechar("fim da frota")

    def _uma(self, vaga, s, item):
        funcao, args, kw, fut, tentativa = item
        if tentativa == 0 and not fut.set_running_or_notify_cancel():
            return s                          # a repetição já está RUNNING desde a primeira vez
        if self._parar.is_set():
            fut.set_exception(RuntimeError("frota parada"))
            return s
        if s is None:
            s = self._abrir(vaga)
            if s is None:
                fut.set_exception(RuntimeError("frota parada antes de haver IP livre"))
                return None
            self.vivas[s.id] = s
        p = Pagina(s)
        t0, b0 = time.time(), s.bytes
        fechar = castigar = None
        try:
            r = funcao(p, *args, **kw)
        except Exception as e:                                 # noqa: BLE001
            classe = _classe_do_erro(e)
            s.ultimo_resultado, s.ultimo_em = classe, time.time()
            s.falhas_seguidas += 1
            s.sujo = True
            self.contagem["falhas"] += 1
            self._evento(s.id, s.proxy_id, classe, int((time.time() - t0) * 1000), s.bytes - b0,
                         "%s · %s" % (_curto(e), _url(s.ultima_url)))
            if isinstance(e, Bloqueio):
                fechar, castigar = classe + ": " + _curto(e), True
            elif isinstance(e, Degradou):
                fechar, castigar = "degradou: " + _curto(e), False
            elif _erro_de_proxy(e):
                fechar, castigar = "proxy: " + _curto(e), True
            elif _navegador_morreu(e):
                fechar, castigar = "navegador caiu: " + _curto(e), False
            elif s.falhas_seguidas >= self.limite_falhas:
                # ERRO DE PÁGINA NÃO É CULPA DO IP (17/09/2026): o clique num elemento que não ficou clicável é defeito
                # da tarefa ou mudança do site. Fecha o navegador (regra 2), mas sem castigo — no primeiro teste do
                # iFood três IPs bons ficaram 15 min de castigo por um clique no cabeçalho.
                fechar, castigar = "%d falhas seguidas (%s)" % (s.falhas_seguidas, classe), classe != "erro_pagina"
            if tentativa + 1 < self.tentativas:
                self.fila.put((funcao, args, kw, fut, tentativa + 1))
            else:
                fut.set_exception(e)
        else:
            s.paginas += 1
            s.sujo = True
            s.ultimo_resultado, s.ultimo_em = p.resultado, time.time()
            self._evento(s.id, s.proxy_id, p.resultado, int((time.time() - t0) * 1000), s.bytes - b0,
                         p.detalhe or _url(s.ultima_url))
            if p.resultado in ("ok", "captcha_resolvido"):
                s.falhas_seguidas = 0
                self.contagem["ok"] += 1
                with self._trava:
                    self._ok[s.proxy_id] = self._ok.get(s.proxy_id, 0) + 1
            else:
                s.falhas_seguidas += 1
                self.contagem["falhas"] += 1
                if s.falhas_seguidas >= self.limite_falhas:
                    fechar, castigar = "%d seguidas sem conteúdo (%s)" % (s.falhas_seguidas, p.resultado), True
            fut.set_result(r)
            if fechar is None and p.fechar_pedido:
                fechar, castigar = p.fechar_pedido[0], p.fechar_pedido[1]
            if fechar is None and s.paginas % 20 == 0 and s.memoria_mb() > self.memoria_limite:
                fechar, castigar = "memória acima de %d MB" % self.memoria_limite, False
        if fechar is None and s.reserva_perdida:
            fechar, castigar = "reserva do IP perdida", False
        if fechar is not None:
            self.vivas.pop(s.id, None)
            s.fechar(fechar, castigar=castigar, cookie_valido=not castigar)
            return None
        return s

    # a faxina: eventos, contadores, reservas e a manutenção do dia
    def _evento(self, sessao_id, proxy_id, resultado, ms, nbytes, detalhe):
        with self._trava:
            self._eventos.append((sessao_id, self.site, proxy_id, resultado, ms, nbytes or None,
                                  (detalhe or "")[:300] or None))

    def _descarregar(self):
        with self._trava:
            eventos, self._eventos = self._eventos, []
            oks, self._ok = self._ok, {}
        try:
            if eventos:
                self.banco.executar("""insert into navegacao.evento (sessao_id, site, proxy_id, resultado, ms, bytes,
                                                                      detalhe) values %s""", lote=eventos)
            if oks:
                self.banco.executar("""insert into navegacao.proxy_site as s (site, proxy_id, sucessos, ultimo_ok)
                                       select v.site, v.id, v.n, now() from (values %s) v(site, id, n)
                                       on conflict (site, proxy_id) do update
                                          set sucessos = s.sucessos + excluded.sucessos, ultimo_ok = now()""",
                                    lote=[(self.site, k, n) for k, n in oks.items()], modelo="(%s, %s, %s::bigint)")
            vivas = [s for s in list(self.vivas.values()) if s.sujo and s.id is not None]
            if vivas:
                self.banco.executar("""update navegacao.sessao x set paginas = v.p, falhas_seguidas = v.f,
                                              ultima_atividade = now(), ultimo_resultado = v.r,
                                              ultimo_em = to_timestamp(v.e)
                                         from (values %s) v(id, p, f, r, e) where x.id = v.id""",
                                    lote=[(s.id, s.paginas, s.falhas_seguidas, s.ultimo_resultado, s.ultimo_em)
                                          for s in vivas],
                                    modelo="(%s::bigint, %s::int, %s::int, %s::text, %s::float8)")
                for s in vivas:
                    s.sujo = False
        except Exception as e:                                 # noqa: BLE001
            # evento perdido não para a coleta, mas não some calado (a 0041 perdia assim)
            self.log("frota %s: faxina falhou (%d eventos voltam para a fila): %s" % (self.site, len(eventos), _curto(e)))
            with self._trava:
                self._eventos[:0] = eventos
                for k, n in oks.items():
                    self._ok[k] = self._ok.get(k, 0) + n

    def _renovar(self):
        vivas = [s for s in list(self.vivas.values()) if s.id is not None]
        if vivas:                              # batimento: sessão viva sem tarefa não pode parecer órfã
            self.banco.executar("update navegacao.sessao set ultima_atividade = now() where id = any(%s)",
                                ([s.id for s in vivas],))
        for s in vivas:
            r = self.banco.executar("select navegacao.renovar_reserva(%s, %s, %s, %s)",
                                    (self.site, s.proxy_id, s.dono, RESERVA_S), buscar="um")
            if not (r and r[0]):
                s.reserva_perdida = True

    def _manutencao_diaria(self):
        """Partições dos próximos dias e resumo dos dias fechados, uma máquina por vez (trava consultiva)."""
        try:
            # SESSÃO ÓRFÃ: processo morto (contêiner parado, máquina caiu) não fecha o que abriu. O batimento renova
            # `ultima_atividade` a cada 5 min; uma hora sem ele é navegador que não existe mais.
            self.banco.executar("""update navegacao.sessao set estado = 'fechada', fechada_em = now(),
                                          motivo_fechamento = 'órfã: processo sem batimento há 1 h'
                                    where estado <> 'fechada' and ultima_atividade < now() - interval '1 hour'""")
            if self.banco.executar("select pg_try_advisory_lock(hashtext('navegacao.consolidar'))", buscar="um")[0]:
                try:
                    self.banco.executar("select navegacao.consolidar(30)")
                finally:
                    self.banco.executar("select pg_advisory_unlock(hashtext('navegacao.consolidar'))")
        except Exception as e:                                 # noqa: BLE001
            self.log("frota %s: manutenção diária falhou: %s" % (self.site, _curto(e)))

    def _faxina(self):
        renovado = dia = time.time()
        while not self._parar.wait(FAXINA_S):
            self._descarregar()
            if time.time() - renovado > RENOVAR_S:
                try:
                    self._renovar()
                except Exception as e:                         # noqa: BLE001
                    self.log("frota %s: renovar reservas falhou: %s" % (self.site, _curto(e)))
                renovado = time.time()
            if time.time() - dia > 6 * 3600:
                self._manutencao_diaria()
                dia = time.time()


def _curto(e):
    return ("%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else ""))[:160]


def _url(u):
    if not u:
        return None
    x = urlsplit(u)
    return (x.netloc + x.path)[:200]


def _classe_do_erro(e):
    if isinstance(e, ErroDaPagina):
        return "erro_pagina"
    if isinstance(e, Captcha):
        return "captcha"
    if isinstance(e, Bloqueio):
        return "bloqueio"
    nome = type(e).__name__.lower() + " " + str(e)[:200].lower()
    if str(e).startswith(("Locator.", "ElementHandle.", "Page.evaluate", "Frame.", "Mouse."))             or "strict mode violation" in nome:
        return "erro_pagina"
    if "timeout" in nome:
        return "timeout"
    return "erro"


def _erro_de_proxy(e):
    t = (type(e).__name__ + " " + str(e)[:300]).lower()
    return "proxy" in t or "invalidip" in t


def _navegador_morreu(e):
    t = str(e).lower()
    return any(x in t for x in ("target closed", "browser has been closed", "target page, context or browser has been closed",
                                "connection closed"))


# ── a sonda ───────────────────────────────────────────────────────────────────────────────────────────────────────
_BLOQUEADAS = set()


def _sonda(p, n, bloquear_a_cada):
    p.ir("https://api.ipify.org/?format=json", timeout=30000)
    ip = json.loads(p.page.inner_text("body"))["ip"]
    if ip in IP_DA_CASA:
        raise RuntimeError("saiu pelo IP da casa (%s) — a frota não pode fazer isso" % ip)
    if bloquear_a_cada and n % bloquear_a_cada == 0 and n not in _BLOQUEADAS:
        _BLOQUEADAS.add(n)
        raise Bloqueio("bloqueio simulado pela sonda (tarefa %d)" % n)
    return ip


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--teste", action="store_true", help="sonda: IP de saída por navegador, castigo e rodízio")
    ap.add_argument("--site", default="teste_frota")
    ap.add_argument("--navegadores", type=int, default=3)
    ap.add_argument("--tarefas", type=int, default=30)
    ap.add_argument("--bloquear-a-cada", type=int, default=0, help="simula bloqueio na tarefa múltipla de N")
    a = ap.parse_args()
    if not a.teste:
        ap.print_help()
        return 0
    t0 = time.time()
    with Frota(a.site, navegadores=a.navegadores, processo="frota_navegacao --teste") as frota:
        futuros = [(i, frota.enviar(_sonda, i, a.bloquear_a_cada)) for i in range(1, a.tarefas + 1)]
        ips = {}
        for i, f in futuros:
            try:
                ip = f.result()
                ips[ip] = ips.get(ip, 0) + 1
            except Exception as e:                             # noqa: BLE001
                print("tarefa %d falhou: %s" % (i, _curto(e)))
        print("resumo:", json.dumps(frota.resumo()), "· IPs de saída:", json.dumps(ips), "· %.0f s" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
