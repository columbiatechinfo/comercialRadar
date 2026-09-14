# -*- coding: utf-8 -*-
"""seek_eventos.py — os avisos em tempo real da SEEK, entre processos (14/09/2026).

Trava, liberacao e decisao de uma ligacao precisam chegar a TODA tela aberta da
mesma empresa. A tela recebe pelo `/ws` do servidor; o problema e que a API nao
e um processo so — producao (7740) e desenvolvimento (7741) sao dois, sobre o
mesmo banco, e amanha serao N replicas. O aviso publicado por um processo nao
alcanca as conexoes do outro.

O TRANSPORTE ENTRE PROCESSOS E O BROADCAST DO SUPABASE REALTIME, e nao
LISTEN/NOTIFY. Os dois funcionam; a escolha e pela escala:

- LISTEN precisa de uma conexao de SESSAO parada por processo. A API so alcanca o
  banco pelo Supavisor, e o modo sessao (7100) tem 20 lugares para a pilha inteira
  — medido em 10/09/2026, o quarto processo de captura morreu com
  EMAXCONNSESSION. Cada replica da API tomaria um lugar para sempre.
- NOTIFY poe o tempo real no Postgres que atende o usuario: o commit de toda
  transacao que notifica passa por uma trava global da fila de notificacoes, e um
  ouvinte lento impede a fila (8 GB) de ser truncada — cheia, o commit da
  DECISAO falha. E o contrario da regra da casa (CLAUDE.md): tempo real e
  Broadcast publicado pelo backend.
- O Broadcast nao encosta no banco: a API publica por HTTP (`/api/broadcast`, em
  lote) e assina o canal pelo WebSocket do Realtime, que ja roda na pilha e foi
  feito para esse leque. Canal PRIVADO com a chave de servico: a chave anon e
  recusada no canal (medido nesta data).

O QUE NAO DEPENDE DISTO: a trava vale pelo BANCO (`seek_trava`) e o 409 do
`decidir` tambem. Com o Realtime fora, cada processo continua avisando as
proprias conexoes, e a tela se ressincroniza sozinha (`GET /api/seek/travas` ao
reconectar, ao voltar para a aba e a cada 2 min). Evento aqui e aviso, nao
autoridade.

ONDE QUEBRA SOB CARGA:
- no Realtime self-hosted de um no so: ~milhares de mensagens/s por canal. Acima
  disso, um canal por empresa (`radar-seek:<empresa>`) e Realtime em cluster;
- no leque local: cada evento percorre so as conexoes daquela empresa (indice por
  empresa no `WSManager`), mas uma empresa com 10 mil telas abertas e decisoes a
  50/s sao 500 mil envios/s num processo Python — ai o evento precisa ser
  agregado por janela (ex.: 250 ms) antes de sair.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid

#: Quem publicou: o processo ignora o proprio evento quando ele volta do Realtime
#: (a entrega local ja foi feita, sem esperar a volta).
ORIGEM = uuid.uuid4().hex[:12]
#: O canal. Os testes usam outro (`SEEK_EVENTOS_TOPICO`) para nao pintar trava
#: falsa na tela de ninguem.
TOPICO = (os.environ.get("SEEK_EVENTOS_TOPICO") or "radar-seek").strip()

_entregar = None              # callable(evento) — o WSManager do servidor
_ouvintes: list = []          # callables(evento) — ex.: o cache da fila
_fila: "queue.Queue[dict]" = queue.Queue(maxsize=20000)
_iniciado = threading.Event()
_parar = threading.Event()
ESTADO = {"origem": ORIGEM, "topico": TOPICO, "assinado": False, "publicados": 0,
          "recebidos": 0, "descartados": 0, "erros_publicar": 0, "erros_assinar": 0,
          "ultimo_erro": None}


def _log(msg):
    print("[seek_eventos] " + msg, flush=True)


def _gw():
    import endpoints
    return str(endpoints.SUPABASE).rstrip("/")


def _chave():
    # A CHAVE DE SERVICO, E SO AQUI: canal privado precisa dela para publicar e
    # assinar sem politica em `realtime.messages` — e politica para `anon` ou
    # `authenticated` abriria o canal a quem tem a chave publica.
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def configurar(entregar):
    """O servidor diz como entregar um evento as conexoes DESTE processo."""
    global _entregar
    _entregar = entregar


def ouvir(fn):
    """Recebe todo evento (local ou vindo de outro processo). Nao pode demorar."""
    if fn not in _ouvintes:
        _ouvintes.append(fn)


def _distribuir(evento):
    if _entregar is not None:
        try:
            _entregar(evento)
        except Exception as e:                                  # noqa: BLE001
            _log("entrega local falhou: %s" % e)
    for fn in list(_ouvintes):
        try:
            fn(evento)
        except Exception as e:                                  # noqa: BLE001
            _log("ouvinte falhou: %s" % e)


def publicar(evento: dict):
    """Entrega ja as conexoes deste processo e manda para os outros.

    Nunca levanta e nunca espera a rede: a publicacao remota sai de uma fila, em
    lote, numa thread. Fila cheia (Realtime fora ha muito tempo) descarta."""
    ev = dict(evento)
    ev.setdefault("tipo", "seek")
    ev["origem"] = ORIGEM
    _distribuir(ev)
    if not _chave():
        return
    try:
        _fila.put_nowait(ev)
    except queue.Full:
        ESTADO["descartados"] += 1


def _publicador():
    url = _gw() + "/realtime/v1/api/broadcast"
    falhas = 0
    while not _parar.is_set():
        try:
            primeiro = _fila.get(timeout=1)
        except queue.Empty:
            continue
        lote = [primeiro]
        # JUNTA O QUE CHEGOU NOS PROXIMOS 50 ms: um POST por lote, e nao um por
        # evento — uma decisao em lote de varias empresas vira uma chamada so.
        limite = time.time() + 0.05
        while len(lote) < 200 and time.time() < limite:
            try:
                lote.append(_fila.get(timeout=max(0.0, limite - time.time())))
            except queue.Empty:
                break
        corpo = json.dumps({"messages": [{"topic": TOPICO, "event": e.get("ev") or "seek",
                                          "payload": e, "private": True} for e in lote]},
                           ensure_ascii=False, default=str).encode("utf-8")
        chave = _chave()
        req = urllib.request.Request(url, data=corpo, method="POST", headers={
            "apikey": chave, "Authorization": "Bearer " + chave, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
            ESTADO["publicados"] += len(lote)
            falhas = 0
        except Exception as e:                                  # noqa: BLE001
            ESTADO["erros_publicar"] += 1
            ESTADO["ultimo_erro"] = "publicar: %s" % str(e)[:160]
            falhas += 1
            if falhas in (1, 10) or falhas % 100 == 0:
                _log("publicar no Realtime falhou (%d seguidas): %s" % (falhas, str(e)[:160]))


def _assinante():
    import websocket   # websocket-client, ja na imagem

    url = _gw().replace("http", "ws", 1) + "/realtime/v1/websocket?vsn=1.0.0"
    espera, ref = 1.0, 0
    while not _parar.is_set():
        ws = None
        try:
            chave = _chave()
            # A CHAVE VAI NO CABECALHO, e nao na URL: URL aparece em log de proxy.
            ws = websocket.create_connection(url, timeout=10, header=["apikey: " + chave])
            ref += 1
            ws.send(json.dumps({"topic": "realtime:" + TOPICO, "event": "phx_join", "ref": str(ref),
                                "payload": {"config": {"broadcast": {"self": False}, "private": True},
                                            "access_token": chave}}))
            ws.settimeout(10)
            while True:
                m = json.loads(ws.recv())
                if m.get("event") == "phx_reply" and m.get("topic") == "realtime:" + TOPICO:
                    if (m.get("payload") or {}).get("status") != "ok":
                        raise RuntimeError("entrada no canal recusada: %s" % str(m.get("payload"))[:200])
                    break
            ESTADO["assinado"] = True
            _log("assinado no canal %s (origem %s)" % (TOPICO, ORIGEM))
            if espera > 1:
                # VOLTOU DEPOIS DE UMA QUEDA: o que passou no buraco se perdeu, e as
                # telas deste processo pedem o estado de novo.
                _distribuir({"tipo": "seek", "ev": "ressincronizar", "origem": ORIGEM})
            espera = 1.0
            ws.settimeout(5)
            batida = time.time()
            while not _parar.is_set():
                if time.time() - batida > 25:
                    ref += 1
                    ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": str(ref)}))
                    batida = time.time()
                try:
                    bruto = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not bruto:
                    raise ConnectionError("o Realtime fechou a conexao")
                m = json.loads(bruto)
                ev = m.get("event")
                if ev == "broadcast":
                    carga = (m.get("payload") or {}).get("payload")
                    if isinstance(carga, dict) and carga.get("origem") != ORIGEM:
                        ESTADO["recebidos"] += 1
                        _distribuir(carga)
                elif ev in ("phx_error", "phx_close"):
                    raise ConnectionError("canal %s: %s" % (ev, str(m.get("payload"))[:160]))
        except Exception as e:                                  # noqa: BLE001
            ESTADO["assinado"] = False
            ESTADO["erros_assinar"] += 1
            ESTADO["ultimo_erro"] = "assinar: %s" % str(e)[:160]
            if espera <= 1 or espera >= 30:
                _log("assinatura do Realtime caiu, nova tentativa em %.0f s: %s" % (espera, str(e)[:160]))
            _parar.wait(espera)
            espera = min(30.0, espera * 2)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:                               # noqa: BLE001
                    pass


def iniciar():
    """Sobe o publicador e o assinante uma vez por processo (no lifespan)."""
    if _iniciado.is_set():
        return
    _iniciado.set()
    if not _chave():
        _log("SUPABASE_SERVICE_ROLE_KEY ausente: avisos so entre as conexoes deste processo")
        return
    threading.Thread(target=_publicador, name="seek-eventos-publica", daemon=True).start()
    threading.Thread(target=_assinante, name="seek-eventos-assina", daemon=True).start()


def parar():
    _parar.set()
