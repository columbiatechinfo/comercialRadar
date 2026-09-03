"""
server.py — Backend web do ComercialRadar (FastAPI + WebSocket)

Serve o frontend (frontend/) e expõe a API local que o mapa consome:

  GET  /                      → frontend (mapa)
  GET  /api/pois              → POIs válidos do banco (leve, p/ os markers)
  GET  /api/pois/{id}         → POI completo (fotos, reviews, horários) p/ o modal
  GET  /api/stats             → contadores gerais do banco
  GET  /api/template          → planilha modelo .xlsx (campos + exemplos)
  POST /api/upload            → recebe a planilha do usuário (multipart)
  GET/POST /api/area          → polígono da área válida (persistido em areas/)
  POST /api/limpar-fora       → remove do banco POIs fora do polígono (dry_run p/ prévia)
  POST /api/jobs              → inicia job (planilha | mineracao) como subprocess
  GET  /api/jobs/atual        → status do job corrente
  POST /api/jobs/parar        → encerra o job corrente
  WS   /ws                    → eventos em tempo real: poi, progresso, job, log

O job roda como subprocess (.venv\\Scripts\\python <script>) e salva JSON
incremental; um watcher lê o JSON, ingere cada registro novo no Postgres
(realtime_ingest) e transmite o POI pro mapa via WebSocket.

Subir:  .venv\\Scripts\\python server.py   →  http://localhost:8765
"""

import io
import json
import math
import os
import re
import secrets
import sys
import time
import asyncio
import threading
import subprocess
import urllib.error      # explícito: `urllib.request` só o expõe por efeito colateral
import urllib.request
from pathlib import Path
from datetime import datetime
from collections import Counter

from fastapi import (FastAPI, UploadFile, File, Request, WebSocket,
                     WebSocketDisconnect, Body, Depends, HTTPException)
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config  # .env + UTF-8
import area_utils
import psycopg2.extras        # `execute_values` na atribuição em lote
import realtime_ingest
import base_comum
import auth as _auth          # o portao e a identidade do usuario da requisicao
from chat_api import registrar_chat   # rotas do chat com historico
# O cadastro de bases do cliente: e ele que declara qual coluna e o que,
# e sem essa declaracao escolher area e trabalhar no escuro.
from base_api import registrar_bases
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
FRONT = BASE / "frontend"
UPLOADS = BASE / "uploads"
AREAS = BASE / "areas"
MINERACAO = BASE / "mineracao"
CAPTURAS = BASE / "capturas"     # saída do processo principal (captura + OCR)
MALHAS = BASE / "malhas"
# A área de trabalho mora na tabela `area_trabalho` (area_utils), não em
# arquivo: ela é compartilhada entre o servidor e os coletores, que rodam
# como subprocessos separados.
PYTHON = str(BASE / ".venv" / "Scripts" / "python.exe")

# ONDE O JOB REALMENTE RODA.
#
# `PYTHON` acima aponta para `.venv/Scripts/python.exe` — um caminho de Windows,
# da máquina de desenvolvimento. Dentro do contêiner da API esse executável não
# existe, e nem adiantaria: a imagem da API é `python:3.10-slim`, sem navegador
# do Playwright, sem `scrapling` e sem `proxy`. Um job disparado pela tela
# morria na etapa 4, que é a captura do Maps.
#
# Quem tem tudo isso é a imagem do minerador, 15,5 GB. Então a API não executa:
# ela DESPACHA. `RADAR_JOB_DOCKER` diz em que imagem, `RADAR_JOB_REPO` diz onde
# o repositório vive NO HOST — o caminho é do host porque quem monta o volume é
# o daemon do Docker, não este processo.
#
# Sem as duas variáveis nada muda: em desenvolvimento o job continua rodando
# local, com o `PYTHON` de sempre.
_JOB_DOCKER = os.environ.get("RADAR_JOB_DOCKER", "").strip()
_JOB_REPO = os.environ.get("RADAR_JOB_REPO", "").strip()

for d in (UPLOADS, AREAS, MINERACAO, CAPTURAS, MALHAS):
    d.mkdir(exist_ok=True)


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    manager.loop = asyncio.get_running_loop()   # captura o event loop p/ broadcast WS
    yield


app = FastAPI(title="ComercialRadar", lifespan=_lifespan)
# O chat vive em modulo proprio: o servidor ja e grande, e assim da para
# mexer nas rotas de conversa sem tocar no que atende o mapa.
registrar_chat(app)
registrar_bases(app)

# ──────────────────────────────────────────────────────────────────────────
# WebSocket — broadcast de eventos pro frontend
# ──────────────────────────────────────────────────────────────────────────
class WSManager:
    def __init__(self):
        self.conns: list[WebSocket] = []
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.conns.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.conns:
            self.conns.remove(ws)

    async def _send_all(self, texto: str):
        mortas = []
        for ws in list(self.conns):
            try:
                await ws.send_text(texto)
            except Exception:
                mortas.append(ws)
        for ws in mortas:
            self.disconnect(ws)

    def broadcast(self, evento: dict):
        """Thread-safe: pode ser chamado das threads do job/watcher."""
        if not self.conns or not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._send_all(json.dumps(evento, ensure_ascii=False, default=str)), self.loop)
        except Exception:
            pass


manager = WSManager()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    """Progresso das rodadas, ao vivo.

    O middleware HTTP não alcança WebSocket — é outro protocolo, e o Starlette
    não passa o handshake por ele. Por isso a checagem é explícita aqui: sem
    isto, a rota de progresso ficaria como a única porta aberta depois de todo o
    portão, e ela transmite nome de estabelecimento e andamento de job.
    O token vem por query porque o navegador não deixa mandar cabeçalho no
    handshake de WebSocket.
    """
    try:
        _auth.usuario_atual(f"Bearer {ws.query_params.get('token', '')}")
    except HTTPException:
        await ws.close(code=1008)      # 1008 = policy violation
        return
    await manager.connect(ws)
    try:
        # manda o estado atual do job na conexão (reconexão não perde contexto)
        await ws.send_text(json.dumps({"tipo": "job", "dados": job_status()}, ensure_ascii=False))
        while True:
            await ws.receive_text()  # mantém a conexão viva (pings do cliente)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# ──────────────────────────────────────────────────────────────────────────
# Job manager — 1 job por vez, subprocess + watcher do JSON incremental
# ──────────────────────────────────────────────────────────────────────────
JOB: dict = {"status": "ocioso"}  # status: ocioso | rodando | finalizado | parado | erro
_JOB_LOCK = threading.Lock()


def job_status() -> dict:
    d = {k: v for k, v in JOB.items()
         if k not in ("proc", "cat", "feitos", "sv", "av")}
    # quem abre a página no meio do job recebe os rótulos da fase corrente —
    # senão os cartões só se acertariam no próximo tick do WebSocket
    d["rotulos"] = _ROTULOS_CARD.get(JOB.get("fase", ""), {})
    d["fase_rotulo"] = _ROTULO_FASE.get(JOB.get("fase", ""), "")
    return d


LOGS = BASE / "logs"


def _novo_job(modo: str, out_json: Path, extra: dict) -> dict:
    # LOG EM ARQUIVO, um por rodada.
    #
    # Até 14/08/2026 o stdout do subprocesso ia só para o WebSocket: aparecia no
    # painel e morria com a aba. Quando uma rodada saía com 47 falhas em 57 POIs,
    # não havia o que reler — o número estava na tela, o motivo tinha rolado para
    # fora, e a investigação começava por reprodução em vez de leitura.
    LOGS.mkdir(exist_ok=True)
    arq = LOGS / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{modo}.log"
    JOB.clear()
    JOB.update({
        "status": "rodando", "modo": modo, "inicio": datetime.now().isoformat(timespec="seconds"),
        "log_arquivo": str(arq),
        "out_json": str(out_json), "total": 0, "feitos": 0,
        "cat": {"validos": 0, "recuperados": 0, "descobertos": 0, "fora_area": 0, "ingeridos": 0},
        "contadores": {"processados": 0, "validos": 0, "recuperados": 0, "descobertos": 0,
                       "fora_area": 0, "sem_match": 0, "erros": 0, "ingeridos": 0},
        **extra,
    })
    return JOB


def _ler_json_tolerante(path: Path):
    """O coletor reescreve o arquivo inteiro a cada save — pode pegar no meio da escrita."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


_RE_TOTAL_SHEET = re.compile(r"POIs\s*:\s*\d+\s*\|\s*Pendentes:\s*(\d+)")
_RE_TOTAL_MINA = re.compile(r"—\s*(\d+)\s*células")
# Progresso "feitos/total" — a fonte AUTORITATIVA da barra (o watcher só conta categorias):
#   gemini-direto: "... POIs 48/2028 | recuperados 4 ..."
#   planilha:      "... ✅ 123/2028 nao_encontrado ..."
#   mineração:     "... célula 12/340 | ..."
_RES_PROG = (
    re.compile(r"POIs\s+(\d+)/(\d+)"),
    re.compile(r"[✅❌]\s+(\d+)/(\d+)"),
    re.compile(r"c[eé]lula\s+(\d+)/(\d+)"),
    re.compile(r"fotos\s+(\d+)/(\d+)"),
    re.compile(r"street view\s+(\d+)/(\d+)"),
)


_RE_FASE = re.compile(r"⟦fase⟧\s*(\w+)")
# Fases que processam POI. Nas outras a unidade da barra é TILE ou RECORTE, e
# derivar "sem match" de um tile é inventar fracasso: com 90 de 264 tiles
# capturados o painel anunciava "90 SEM MATCH" antes de buscar o primeiro POI.
_FASES_POI = {"busca", "enriquecimento", ""}
# "Sem match" só existe onde houve BUSCA de POI: procurar um nome no Maps e não
# achar. É derivado (processados − sucessos), e em qualquer outro modo essa
# subtração mede outra coisa: no download de imagens dava "4.250 SEM MATCH"
# porque a barra conta FOTOS baixadas e o watcher não tem POI nenhum para
# contar — todo processado virava "fracasso". Modo fora desta lista mostra 0.
_MODOS_COM_MATCH = {"planilha", "mineracao", "minerar_web", "enriquecer_maps"}
_ROTULO_FASE = {"captura": "fotografando o mapa", "deteccao": "detectando ícones",
                "ocr": "lendo os nomes", "busca": "buscando cada nome no Maps",
                "streetview": "fotografando a fachada de cada ponto",
                "fachada": "lendo a fachada de cada ponto"}
# "📸 POIs 12/500 | aptos 9 | inaptos 1 | fora de escopo 2 | oportunidades 14"
# A leitura em quatro fases fala outro vocabulário: a IA só devolve `aprovar`,
# `reprovar` ou `revisar`, e o quarto balde é o POI sem evidência aproveitável.
# O padrão antigo (`aptos | inaptos | fora de escopo | oportunidades`) fica: as
# rodadas 1.5.0 gravadas em log ainda casam com ele.
_RE_AV = re.compile(r"aprovar\s+(\d+)\s*\|\s*revisar\s+(\d+)\s*\|\s*"
                    r"reprovar\s+(\d+)\s*\|\s*sem evid[êe]ncia\s+(\d+)")
_RE_AV_ANTIGO = re.compile(r"aptos\s+(\d+)\s*\|\s*inaptos\s+(\d+)\s*\|\s*"
                           r"fora de escopo\s+(\d+)\s*\|\s*oportunidades\s+(\d+)")
# "📸 POIs 31/21701 | capturados 30 | sem pano 1 | Agelú Arte e Cia"
_RE_SV = re.compile(r"capturados\s+(\d+)\s*\|\s*sem pano\s+(\d+)")
# Na fase Street View os cartões medem outra coisa: não há "encontrado" nem
# "sem match", há fachada capturada e ponto sem panorama. Sem trocar o rótulo, o
# painel mostraria o número certo embaixo da palavra errada.
_ROTULOS_CARD = {"streetview": {"validos": "Fachadas capturadas",
                                "semmatch": "Sem panorama",
                                "ingeridos": "Gravadas no banco"},
                 # Os quatro baldes da leitura em quatro fases, na ORDEM em que
                 # `_RE_AV` os captura. `revisar` no lugar que era das
                 # oportunidades porque é o que precisa de gente — é a fila que
                 # alguém tem de trabalhar, e o painel deve mostrá-la crescendo.
                 "fachada": {"validos": "A IA aprovou",
                             "recuperados": "Pediu revisão humana",
                             "descobertos": "A IA reprovou",
                             "semmatch": "Sem evidência para ler",
                             "ingeridos": "Gravadas no banco"},
                 # A leitura anterior media outra coisa; rótulo antigo para
                 # número antigo, senão um log de 1.5.0 reaberto mostraria
                 # "oportunidades" embaixo de "pediu revisão humana".
                 "fachada_1_5": {"validos": "Leituras aptas",
                                 "recuperados": "Oportunidades",
                                 "descobertos": "Imagem inapta",
                                 "semmatch": "Não é imóvel",
                                 "ingeridos": "Gravadas no banco"}}


def _emitir_progresso():
    """Combina o progresso do log (feitos/total) com as categorias do watcher.
    'processados' vem do log (nunca passa do total); 'sem_match' é derivado, para
    fechar exatamente com a barra em qualquer modo."""
    c = JOB.get("cat", {})
    feitos = JOB.get("feitos", 0)
    total = JOB.get("total", 0)
    fase = JOB.get("fase", "")

    if fase == "fachada":
        # Também grava direto no banco (`fachada_anotacao`), sem passar pelo
        # watcher — os números saem do log, como no Street View.
        #
        # A ORDEM dos quatro depende do vocabulário que o processo fala, e as
        # duas ordens NÃO coincidem: a leitura 1.5.0 emite
        # `aptos | inaptos | fora de escopo | oportunidades`, a 2.0.0 emite
        # `aprovar | revisar | reprovar | sem evidência`. Desempacotar as duas
        # na mesma ordem punha o número de reprovados embaixo do rótulo de
        # revisão — errado, e crível o bastante para ninguém desconfiar.
        a, b, c_, d = JOB.get("av", (0, 0, 0, 0))
        if JOB.get("av_vocab") == "1.5.0":
            cont = {"validos": a, "recuperados": d, "descobertos": b,
                    "sem_match": c_, "ingeridos": a}
            rotulos = _ROTULOS_CARD["fachada_1_5"]
        else:
            cont = {"validos": a, "recuperados": b, "descobertos": c_,
                    "sem_match": d, "ingeridos": a + b + c_ + d}
            rotulos = _ROTULOS_CARD["fachada"]
        cont.update({"processados": min(feitos, total) if total else feitos,
                     "fora_area": 0, "erros": 0})
        JOB["contadores"] = cont
        manager.broadcast({"tipo": "progresso", "dados": {
            "contadores": cont, "total": total, "fase": fase,
            "fase_rotulo": _ROTULO_FASE.get(fase, ""), "rotulos": rotulos}})
        return

    if fase == "streetview":
        # A fase não escreve no JSON — grava a foto direto em `streetview_imgs`.
        # Então o watcher não tem o que contar, e os cartões ficavam parados no
        # placar da fase Web enquanto milhares de fachadas entravam no banco.
        # Aqui os números saem do próprio log da captura.
        sv = JOB.get("sv", (0, 0))
        cont = {"processados": min(feitos, total) if total else feitos,
                "validos": sv[0], "recuperados": 0, "descobertos": 0,
                "fora_area": 0, "sem_match": sv[1], "erros": 0, "ingeridos": sv[0]}
        JOB["contadores"] = cont
        manager.broadcast({"tipo": "progresso", "dados": {
            "contadores": cont, "total": total, "fase": fase,
            "fase_rotulo": _ROTULO_FASE.get(fase, ""),
            "rotulos": _ROTULOS_CARD.get(fase, {})}})
        return

    val, rec = c.get("validos", 0), c.get("recuperados", 0)
    desc, fora = c.get("descobertos", 0), c.get("fora_area", 0)
    sucessos = val + rec + desc + fora
    processados = max(feitos, sucessos)
    if total:
        processados = min(processados, total)
    # só há "sem match" onde se procurou POI: o MODO tem de ser de busca E a
    # fase tem de ser a de busca (em captura/detecção/OCR a barra conta tile e
    # recorte, e o que ainda não foi buscado não fracassou)
    sem = (max(0, processados - sucessos)
           if JOB.get("modo") in _MODOS_COM_MATCH and fase in _FASES_POI else 0)
    cont = {"processados": processados, "validos": val, "recuperados": rec,
            "descobertos": desc, "fora_area": fora, "sem_match": sem,
            "erros": 0, "ingeridos": c.get("ingeridos", 0)}
    JOB["contadores"] = cont
    manager.broadcast({"tipo": "progresso", "dados": {
        "contadores": cont, "total": total,
        "fase": fase, "fase_rotulo": _ROTULO_FASE.get(fase, ""),
        "rotulos": _ROTULOS_CARD.get(fase, {})}})


def _thread_logs(proc: subprocess.Popen):
    """Lê o stdout do subprocess: retransmite como log e extrai o progresso real."""
    arq = JOB.get("log_arquivo")
    fh = None
    if arq:
        try:
            fh = open(arq, "a", encoding="utf-8", buffering=1)   # linha a linha
        except OSError:
            fh = None
    for linha in iter(proc.stdout.readline, ""):
        linha = linha.replace("\r", "").rstrip()
        if not linha:
            continue
        if fh:
            # `buffering=1` grava a cada linha: o arquivo serve para acompanhar
            # a rodada VIVA, não só para autópsia depois que ela morre.
            try:
                fh.write(linha + "\n")
            except OSError:
                fh = None
        mf = _RE_FASE.search(linha)
        if mf:
            # fase nova zera o andamento: a unidade mudou (tile → recorte → POI)
            JOB["fase"] = mf.group(1)
            JOB["feitos"] = 0
            JOB["sv"] = (0, 0)
            JOB["av"] = (0, 0, 0, 0)
            # zerar as CATEGORIAS também: elas vêm do JSON da fase anterior e,
            # como `processados = max(feitos, sucessos)`, o placar velho segurava
            # a barra da fase nova num número que não era dela
            JOB["cat"] = {}
            _emitir_progresso()
            continue
        msv = _RE_SV.search(linha)
        if msv:
            JOB["sv"] = (int(msv.group(1)), int(msv.group(2)))
        mav = _RE_AV.search(linha)
        if mav:
            JOB["av"] = tuple(int(mav.group(i)) for i in (1, 2, 3, 4))
            JOB["av_vocab"] = "2.0.0"
        else:
            mav = _RE_AV_ANTIGO.search(linha)
            if mav:
                JOB["av"] = tuple(int(mav.group(i)) for i in (1, 2, 3, 4))
                JOB["av_vocab"] = "1.5.0"
        m = _RE_TOTAL_SHEET.search(linha) or _RE_TOTAL_MINA.search(linha)
        if m:
            JOB["total"] = int(m.group(1))
        for rgx in _RES_PROG:
            mp = rgx.search(linha)
            if mp:
                feitos, total = int(mp.group(1)), int(mp.group(2))
                # Job de várias FASES (captura → OCR → busca) muda o total ao
                # passar de uma para a outra. `max` é o certo DENTRO da fase
                # (o log de 10 workers chega fora de ordem), mas segurar o
                # número da fase anterior deixaria a barra travada no fim.
                if total != JOB.get("total"):
                    JOB["feitos"] = feitos
                else:
                    JOB["feitos"] = max(JOB.get("feitos", 0), feitos)
                JOB["total"] = total
                _emitir_progresso()
                break
        manager.broadcast({"tipo": "log", "linha": linha[:300]})
    if fh:
        try:
            fh.close()
        except OSError:
            pass
    try:
        proc.stdout.close()
    except Exception:
        pass


def _key(r: dict) -> str:
    if r.get("place_id"):
        return f"pid:{r['place_id']}"
    if r.get("_row") is not None:
        return f"row:{r['_row']}"
    return f"anon:{r.get('nome')}|{r.get('maps_lat')}|{r.get('maps_lng')}"


def _sig(r: dict) -> tuple:
    return (r.get("status"), bool(r.get("match_valido")),
            len(r.get("fotos") or []), len(r.get("comentarios") or []))


def _riqueza(r: dict) -> int:
    return len(r.get("fotos") or []) + len(r.get("comentarios") or [])


def _dedup(dados: list) -> dict:
    """1 registro por chave (o mais rico). O JSON da planilha tem place_id repetido
    — várias linhas apontam pro mesmo lugar; o banco colapsa por place_id. Sem esta
    dedup, registros irmãos oscilam a cada passada e re-ingerem/recontam em loop."""
    melhores: dict = {}
    for r in dados:
        if not isinstance(r, dict):
            continue
        k = _key(r)
        if k not in melhores or _riqueza(r) > _riqueza(melhores[k]):
            melhores[k] = r
    return melhores


def _baseline_do_arquivo(out_json: Path) -> dict:
    """Estado (key→assinatura) do JSON ANTES do job. Registros inalterados em
    relação a este baseline NÃO pertencem ao job atual — não contam nem re-ingerem."""
    dados = _ler_json_tolerante(out_json)
    if not isinstance(dados, list):
        return {}
    return {k: _sig(r) for k, r in _dedup(dados).items()}


def _classifica(status: str) -> str:
    if status in ("ok",):
        return "validos"
    # `recuperado_web` faltava aqui: todo POI que a fase Web enriquecia caía no
    # `return "sem_match"` do fim. O painel anunciava "78 SEM MATCH · 0
    # ENCONTRADOS" enquanto o banco recebia os 78 normalmente — o processo certo
    # e o placar errado, que é o pior tipo de erro para quem acompanha.
    if status in ("recuperado_proximo", "recuperado_ia", "recuperado_gemini",
                  "recuperado_web"):
        return "recuperados"
    if status in ("descoberto", "minerado"):
        return "descobertos"
    # Registro ANTIGO, de quando estar fora da área zerava o `match_valido` e o
    # POI era descartado. Hoje `status` guarda COMO o POI foi encontrado e o
    # "fora" vem do veredito da ingestão (`inserido_fora`), não daqui — mas o
    # JSON de uma coleta velha ainda pode trazer isto.
    if status == "fora_da_area":
        return "fora_area"
    if status == "erro":
        return "erros"
    return "sem_match"  # nao_encontrado, encontrado_divergente, fora_da_uf


def _poi_leve(r: dict, poi_id) -> dict:
    la, lo = area_utils.coord_do_registro(r)
    # CIDADE tem de vir junto. Todo filtro do painel é por município, e o POI que
    # chega ao vivo sem ela some no instante em que o usuário seleciona um —
    # aparecia no mapa durante o job e desaparecia depois. A regra de extração é
    # a MESMA da ingestão, senão o POI vivo e o POI recarregado do banco cairiam
    # em cidades diferentes.
    cidade = realtime_ingest._s(r.get("cidade")) or realtime_ingest._cidade_uf(
        r.get("endereco"), r.get("endereco_planilha"))[0]
    return {
        "id": poi_id, "nome": r.get("nome") or r.get("nome_planilha"),
        "categoria": r.get("categoria"), "endereco": r.get("endereco"),
        "lat": la, "lng": lo, "fonte": r.get("fonte"), "fonte_dado": r.get("fonte_dado"),
        "status": r.get("status"), "cidade": cidade,
        # Os chips de ATRIBUTO (📞 com telefone, 📷 com foto, 🏢 com CNPJ,
        # 📸 street view) leem estas flags. Sem elas o POI que chega ao vivo cai
        # em "sem telefone": numa mineração de Canoas o painel anunciou
        # "Com telefone 19 · Sem telefone 9.091" enquanto o banco tinha 7.130
        # com telefone. O `/api/pois` já as devolve; faltava a via do WebSocket.
        "tem_tel": bool(r.get("telefone")),
        "tem_cnpj": bool(r.get("cnpj")),
        "tem_foto": bool(r.get("fotos")),
        "tem_sv": bool(r.get("streetview_path")
                       and r.get("streetview_path") != "NA"),
        # o ingestor faz delete+recreate por place_id: o POI reingerido ganha id
        # NOVO, e sem esta chave o mapa fica com o marcador velho ao lado do novo
        "place_id": r.get("place_id"),
        "avaliacao": r.get("avaliacao"), "total_avaliacoes": r.get("total_avaliacoes"),
    }


def _thread_watcher(proc: subprocess.Popen, out_json: Path, poligono, baseline: dict):
    """
    Acompanha o JSON incremental do coletor. Compara cada registro com o BASELINE
    (estado antes do job): só registros novos ou que MUDARAM contam como trabalho
    deste job — são ingeridos no Postgres (dedup por assinatura, sem re-inserir os
    já existentes) e transmitidos ao mapa. As categorias são o delta do job; a
    barra de progresso vem do log (_emitir_progresso). 1 passada final no fim.
    """
    assinaturas: dict = {}     # key → última assinatura já ingerida (evita re-ingestão)
    ingeridos_keys: set = set()  # keys distintas já gravadas (conta 1x, não infla)
    # Quem o GATE DE ÁREA recusou. Não dá para tirar isso do `status` do
    # registro: o POI foi encontrado no Maps e o status é "ok" — ele só não é
    # daqui. Sem contar o veredito da ingestão, o painel mostrava "fora da área
    # 0" enquanto 19 de 31 POIs eram descartados por isso, e a única leitura
    # possível era "o gravador está quebrado".
    fora_keys: set = set()
    conn = None

    def _passada():
        nonlocal conn
        dados = _ler_json_tolerante(out_json)
        if not isinstance(dados, list):
            return
        cat = {"validos": 0, "recuperados": 0, "descobertos": 0, "fora_area": 0}
        for k, r in _dedup(dados).items():  # 1 registro (o mais rico) por place_id/linha
            sig = _sig(r)
            if baseline.get(k) == sig:
                continue  # inalterado desde antes do job → não é trabalho deste job
            cls = _classifica(r.get("status") or "")
            if cls in cat:
                cat[cls] += 1

            if assinaturas.get(k) == sig:
                continue  # já processado nesta execução do watcher
            assinaturas[k] = sig
            if r.get("match_valido"):
                try:
                    if conn is None or conn.closed:
                        conn = realtime_ingest.conectar()
                    resultado, poi_id = realtime_ingest.ingerir_registro(r, poligono, conn)
                except Exception as e:
                    resultado, poi_id = ("erro_db", None)
                    manager.broadcast({"tipo": "log", "linha": f"⚠️ ingest: {str(e)[:120]}"})
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                # Os dois são GRAVADOS. O `_fora` é só onde ele caiu em relação
                # ao polígono desenhado — e não vai para o mapa, porque o mapa
                # mostra o que está em foco (a área, ou o município escolhido).
                # Ele fica no banco esperando o dia em que aquela cidade for o
                # foco; é justamente por isso que não se joga fora.
                if resultado in ("inserido", "inserido_fora"):
                    ingeridos_keys.add(k)
                    if resultado == "inserido":
                        manager.broadcast({"tipo": "poi", "poi": _poi_leve(r, poi_id)})
                    else:
                        fora_keys.add(k)

        cat["fora_area"] = max(cat["fora_area"], len(fora_keys))
        JOB["cat"] = {**cat, "ingeridos": len(ingeridos_keys)}
        _emitir_progresso()

    while proc.poll() is None:
        _passada()
        time.sleep(2)
    time.sleep(1)
    _passada()  # passada final (recuperação pós-run grava no fim)

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    rc = proc.returncode
    if JOB.get("status") == "rodando":
        JOB["status"] = "finalizado" if rc == 0 else "erro"
    JOB["fim"] = datetime.now().isoformat(timespec="seconds")
    manager.broadcast({"tipo": "job", "dados": job_status()})


def _comando_no_minerador(cmd: list, env: dict) -> tuple:
    """Reescreve o comando para rodar dentro da imagem do minerador.

    Devolve `(cmd, nome_do_conteiner)`. Sem `RADAR_JOB_DOCKER` devolve o
    comando intacto e nome vazio — é o caminho de desenvolvimento.

    `docker run` e não `docker exec`: contêiner novo por job, com nome próprio,
    é o que deixa `/api/jobs/parar` funcionar. Terminar um `docker exec` mata só
    o cliente, e o processo segue vivo lá dentro — o operador veria "parado" na
    tela com a mineração ainda queimando IP.

    `--network host` porque o pipeline fala com o pooler, o Photon, o OSRM e o
    Nominatim por `127.0.0.1` do host, como o compose do minerador já faz.
    """
    if not _JOB_DOCKER or not cmd or cmd[0] != PYTHON:
        return cmd, ""
    if not _JOB_REPO:
        raise RuntimeError(
            "RADAR_JOB_DOCKER está definido mas RADAR_JOB_REPO não. Sem o "
            "caminho do repositório NO HOST não há o que montar em /app.")
    nome = "radar-job-%d" % int(time.time() * 1000)
    passar = []
    for k in ("CR_TENANT_ID", "PYTHONUTF8", "PYTHONIOENCODING",
              "PYTHONUNBUFFERED", "A2L_DB_HOST"):
        if env.get(k):
            passar += ["-e", "%s=%s" % (k, env[k])]
    novo = (["docker", "run", "--rm", "--name", nome, "--network", "host",
             "-v", "%s:/app" % _JOB_REPO, "-w", "/app", "-e", "HOME=/tmp"]
            + passar + [_JOB_DOCKER, "python"] + list(cmd[1:]))
    return novo, nome


def _iniciar_subprocess(cmd: list, out_json: Path, poligono):
    import os
    # Baseline ANTES do Popen: fotografa o estado do arquivo para o watcher contar
    # só o delta deste job (evita recontar/re-ingerir o que já estava lá).
    baseline = _baseline_do_arquivo(out_json)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    # A EMPRESA DO USUÁRIO vai para o subprocesso.
    #
    # O coletor grava com o worker (BYPASSRLS) e carimba `id_empresa` a partir de
    # `CR_TENANT_ID`. Sem sobrescrever aqui, todo job disparado pelo painel
    # gravaria na empresa fixa do `.env` — a Corsan rodaria uma mineração e o
    # resultado nasceria na Columbia Tech Info, sem erro nenhum, invisível para
    # quem pediu.
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is not None and u.id_empresa:
        env["CR_TENANT_ID"] = u.id_empresa
    cmd, nome_cont = _comando_no_minerador(cmd, env)
    JOB["container"] = nome_cont
    proc = subprocess.Popen(
        cmd, cwd=str(BASE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    JOB["proc"] = proc
    threading.Thread(target=_thread_logs, args=(proc,), daemon=True).start()
    threading.Thread(target=_thread_watcher, args=(proc, out_json, poligono, baseline), daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────
# API — área (polígono)
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/area")
def get_area():
    poly = area_utils.carregar_area()
    return {"polygon": poly or []}


def _tenant_para_gravar():
    """A empresa em que ESTA requisição grava — do token, nunca do corpo.

    Devolve `(id_empresa | None, erro | None)`.

    Para quem tem empresa, devolve `None`: a conexão de `auth.conectar_como` já
    declarou `request.jwt.claim.sub`, e reescrever a variável a partir daqui seria abrir
    a porta para um usuário gravar na empresa do vizinho. Só o ROOT precisa de
    resposta, porque ele é o único sem empresa — e sem ela nenhuma trigger tem
    o que carimbar.

    Para o root vale a MESMA convenção que os jobs já usam desde 13/08/2026:
    `CR_TENANT_ID` do ambiente do servidor. É de propósito que seja a mesma —
    era isso que faltava para gravar e enxergar concordarem. Se o root define
    uma área, ela nasce na empresa em que as mineracões dele já gravam.
    """
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is not None and u.id_empresa:
        return None, None
    tid = (os.environ.get("CR_TENANT_ID") or "").strip()
    if tid:
        return tid, None
    return None, ("Você entrou como root, que não pertence a nenhuma empresa, e "
                  "o servidor subiu sem CR_TENANT_ID. Não há empresa para gravar. "
                  "Suba o servidor com CR_TENANT_ID=<id da empresa> ou entre com "
                  "um usuário da empresa.")


@app.post("/api/area")
async def post_area(body: dict):
    poly = body.get("polygon") or []
    tenant, erro = _tenant_para_gravar()
    # Apagar não precisa de empresa: o DELETE é filtrado pela RLS (ou pelo
    # BYPASSRLS do root), e exigir empresa aqui impediria de limpar a área.
    if erro and poly:
        return JSONResponse({"erro": erro}, status_code=409)
    n = area_utils.salvar_area(poly, body.get("nome") or area_utils.AREA_PADRAO,
                               tenant=tenant)
    return {"ok": True, "vertices": n, "polygon": poly if n else []}


@app.post("/api/limpar-fora")
def limpar_fora(body: dict):
    poly = area_utils.carregar_area()
    if not poly:
        return JSONResponse({"erro": "Nenhuma área definida. Desenhe o polígono primeiro."}, status_code=400)
    dry = bool(body.get("dry_run", True))
    res = realtime_ingest.limpar_fora_da_area(poly, dry_run=dry)
    if not dry and res.get("removidos"):
        manager.broadcast({"tipo": "reload"})
        manager.broadcast({"tipo": "log",
                           "linha": f"🧹 Limpeza: {res['removidos']} POIs fora da área removidos do banco."})
    return res


# ──────────────────────────────────────────────────────────────────────────
# API — malha territorial (IBGE) p/ dar ênfase às divisas no mapa
# ──────────────────────────────────────────────────────────────────────────
_UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
        "SP", "SE", "TO"}


def _uf_majoritaria() -> str:
    """UF mais comum nos endereços do banco (a run é UF-scoped)."""
    try:
        conn = realtime_ingest.conectar()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT endereco FROM pois WHERE endereco IS NOT NULL LIMIT 4000")
            cont = Counter()
            for (end,) in cur.fetchall():
                achados = [g for g in re.findall(r"\b([A-Z]{2})\b", end) if g in _UFS]
                if achados:
                    cont[achados[-1]] += 1
        conn.close()
        return cont.most_common(1)[0][0] if cont else "PI"
    except Exception:
        return "PI"


def _http_json(url: str, timeout: int = 90):
    """GET JSON tolerante a resposta gzip (o IBGE comprime mesmo sem pedirmos)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "ComercialRadar/1.0"})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return json.loads(raw)


_UF_POR_COD = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
               "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
               "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
               "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
_UFS_GEO: list = []          # [(sigla, shapely geom)] — malha das UFs, carregada uma vez


def _uf_do_ponto(lat: float, lng: float) -> str:
    """Em que UF cai este ponto? Usa a malha de estados do IBGE (cache em disco).

    É o que faz as divisas seguirem o mapa: sem isto a malha vinha sempre da UF
    majoritária do banco de POIs (PI), então quem trabalhava em PE não via divisa
    nenhuma."""
    global _UFS_GEO
    if not _UFS_GEO:
        cache = MALHAS / "_ufs.geojson"
        try:
            if cache.exists():
                gj = json.loads(cache.read_text(encoding="utf-8"))
            else:
                # "intermediaria" e não "minima": a mínima generaliza as divisas e
                # joga cidades de fronteira no estado errado (Itambé-PE virava PB)
                gj = _http_json("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
                                "?formato=application/vnd.geo+json&qualidade=intermediaria"
                                "&intrarregiao=UF")
                cache.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
            from shapely.geometry import shape
            _UFS_GEO = [(_UF_POR_COD[c], shape(f["geometry"]))
                        for f in gj.get("features", [])
                        for c in [str(f.get("properties", {}).get("codarea", ""))[:2]]
                        if c in _UF_POR_COD]
        except Exception:
            return ""
    try:
        from shapely.geometry import Point
        p = Point(lng, lat)
        for sig, geo in _UFS_GEO:
            if geo.contains(p):
                return sig
        # fora de terra (mar, fronteira): pega a UF mais próxima
        return min(_UFS_GEO, key=lambda g: g[1].distance(p))[0]
    except Exception:
        return ""


@app.get("/api/mapa/config")
def mapa_config():
    """Chave e Map ID da Maps JavaScript API, lidos do .env.

    Aqui a chave VAI para o navegador — não tem como ser diferente: quem carrega
    a Maps JavaScript API é a página. O que protege esse tipo de chave não é
    escondê-la, e sim restringi-la no console do Google por **referrer HTTP** e
    por API. Guardá-la no .env evita que ela viva no repositório, que é o ganho
    real aqui.

    Cobrança: Dynamic Maps é por CARREGAMENTO de mapa, não por tile."""
    return {"key": os.environ.get("GOOGLE_TILES_KEY", "").strip(),
            "mapId": os.environ.get("GOOGLE_MAP_ID", "").strip()}


@app.get("/api/ufs")
def ufs_carregadas():
    """UFs que já têm malha municipal no banco, com a contagem."""
    # `ibge_malha` e base publica: banco de REFERENCIA (ADR 0003).
    conn = base_comum.conectar_referencia()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT uf, count(*) FROM ibge_malha
                            WHERE uf IS NOT NULL AND length(uf)=2
                         GROUP BY uf ORDER BY uf""")
            return JSONResponse([{"uf": u, "n": n} for u, n in cur.fetchall()])
    finally:
        conn.close()


@app.get("/api/municipios")
def municipios_da_uf(uf: str):
    """Municípios de uma UF, para o seletor — sem geometria, que é pesada."""
    # `ibge_malha` e base publica: banco de REFERENCIA (ADR 0003).
    conn = base_comum.conectar_referencia()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT cod_municipio, nome FROM ibge_malha
                            WHERE uf = %s AND nome IS NOT NULL ORDER BY nome""",
                        ((uf or "").strip().upper(),))
            return JSONResponse([{"cod": c, "nome": n} for c, n in cur.fetchall()])
    finally:
        conn.close()


@app.post("/api/area/municipio")
def area_do_municipio(cod: str):
    """Usa o polígono do município COMO ÁREA DE TRABALHO.

    Evita desenhar a divisa ponto a ponto no mapa. Grava no mesmo
    `areas/area_atual.json` que o desenho manual usa, então todo o resto do
    sistema (mineração, enriquecimento, quadras) segue igual — muda só de onde
    veio o polígono.

    O anel externo basta: a área de trabalho é um filtro de contenção, e ilha ou
    buraco na divisa não muda quem está dentro para efeito de varredura."""
    # `ibge_malha` e base publica: banco de REFERENCIA (ADR 0003).
    conn = base_comum.conectar_referencia()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT nome, uf, ST_AsGeoJSON(ST_Envelope(geom)),
                                  ST_AsGeoJSON(geom)
                             FROM ibge_malha WHERE cod_municipio = %s""",
                        ((cod or "").strip(),))
            r = cur.fetchone()
    finally:
        conn.close()
    if not r:
        return JSONResponse({"erro": f"município {cod} não está na malha"},
                            status_code=404)
    nome, uf, _, gj = r
    g = json.loads(gj)
    coords = g["coordinates"]
    if g["type"] == "MultiPolygon":       # fica com a maior ilha
        coords = max(coords, key=lambda p: len(p[0]))
    anel = coords[0]
    poly = [[lat, lng] for lng, lat in anel]
    tenant, erro = _tenant_para_gravar()
    if erro:
        return JSONResponse({"erro": erro}, status_code=409)
    area_utils.salvar_area(poly, tenant=tenant)
    return JSONResponse({"ok": True, "municipio": nome, "uf": uf,
                         "vertices": len(poly), "polygon": poly})


@app.get("/api/malha")
def malha(uf: str = "", lat: float | None = None, lng: float | None = None):
    """GeoJSON dos municípios da UF, montado da tabela `ibge_malha`.

    Antes vinha de `malhas/<UF>.geojson`. Agora sai do banco: o dado é o mesmo,
    mas deixa de existir uma cópia solta na pasta do sistema. Quando a UF ainda
    não foi carregada, busca no IBGE e GRAVA NO BANCO — nunca mais em arquivo.

    Sem `uf`, resolve pela coordenada (o front manda o centro do mapa); sem
    coordenada, cai na UF majoritária do banco."""
    uf = (uf or "").strip().upper()
    if uf not in _UFS and lat is not None and lng is not None:
        uf = _uf_do_ponto(lat, lng)
    if uf not in _UFS:
        uf = _uf_majoritaria()

    def _do_banco():
        # `ibge_malha` e base publica: banco de REFERENCIA (ADR 0003).
        conn = base_comum.conectar_referencia()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT cod_municipio, nome, ST_AsGeoJSON(geom)
                                 FROM ibge_malha WHERE uf = %s""", (uf,))
                fs = [{"type": "Feature",
                       "properties": {"codarea": cod, "nome": nome or cod},
                       "geometry": json.loads(g)} for cod, nome, g in cur.fetchall()]
            return fs
        finally:
            conn.close()

    try:
        fs = _do_banco()
        if not fs:
            url_malha = (f"https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
                         f"?formato=application/vnd.geo+json&qualidade=intermediaria"
                         f"&intrarregiao=municipio")
            gj = _http_json(url_malha)
            url_nomes = (f"https://servicodados.ibge.gov.br/api/v1/localidades/"
                         f"estados/{uf}/municipios")
            nomes = {str(m["id"]): m["nome"] for m in _http_json(url_nomes, timeout=60)}
            # `ibge_malha` e base publica: banco de REFERENCIA (ADR 0003).
            conn = base_comum.conectar_referencia()
            try:
                with conn.cursor() as cur:
                    for f in gj.get("features", []):
                        cod = str(f.get("properties", {}).get("codarea", ""))
                        if not cod:
                            continue
                        cur.execute(
                            """INSERT INTO ibge_malha (cod_municipio, nome, uf, geom)
                               VALUES (%s,%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))
                               ON CONFLICT (cod_municipio) DO UPDATE
                                 SET nome = COALESCE(EXCLUDED.nome, ibge_malha.nome),
                                     uf = EXCLUDED.uf, geom = EXCLUDED.geom""",
                            (cod, nomes.get(cod, cod), uf, json.dumps(f["geometry"])))
                conn.commit()
            finally:
                conn.close()
            fs = _do_banco()
        return JSONResponse({"type": "FeatureCollection", "uf": uf, "features": fs})
    except Exception as e:
        return JSONResponse({"erro": f"malha indisponível: {str(e)[:120]}"},
                            status_code=502)


# ──────────────────────────────────────────────────────────────────────────
# API — POIs / stats
# ──────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────








@app.get("/api/pois")
def listar_pois():
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.nome, p.categoria, p.endereco, p.telefone, p.avaliacao,
                       p.total_avaliacoes, p.fonte, p.fonte_dado, p.status,
                       COALESCE(p.maps_lat, p.lat_origem), COALESCE(p.maps_lng, p.lng_origem),
                       (p.cnpj IS NOT NULL) AS tem_cnpj, p.situacao_cadastral, p.endereco_fonte,
                       (p.telefone IS NOT NULL) AS tem_tel,
                       (p.streetview_path IS NOT NULL AND p.streetview_path <> 'NA') AS tem_sv,
                       EXISTS (SELECT 1 FROM images_urls i WHERE i.poi_id = p.id) AS tem_foto,
                       -- MULTIORIGEM: o ponto e sustentado por MAIS DE UMA base.
                       --
                       -- E o que o operador precisa avaliar primeiro, porque e
                       -- onde a fusao pode ter errado: medido no RS, 66,2% das
                       -- fusoes suspeitas uniram estabelecimentos distintos.
                       -- Ponto de fonte unica nao tem o que revisar — o registro
                       -- E o ponto.
                       (SELECT count(*) > 1 FROM vinculo_poi v
                         WHERE v.poi_id = p.id AND v.estado = 'vinculado') AS multiorigem,

                       -- QUANTAS FONTES, e não só "mais de uma". O mapa novo
                       -- desenha o número em cima do ponto: dois pontos
                       -- multiorigem não valem o mesmo se um tem duas fontes e
                       -- o outro tem cinco, e o booleano apagava essa
                       -- diferença justamente onde ela decide a confiança.
                       (SELECT count(*) FROM vinculo_poi v
                         WHERE v.poi_id = p.id AND v.estado = 'vinculado') AS n_fontes,

                       -- A FLAG DO CADASTRO, que é o que pinta o ponto.
                       --
                       -- `ja_cadastrado`  o cliente já o tem como comercial —
                       --                  não há o que reclassificar
                       -- `reclassificar_*` está como habitacional na base e o
                       --                  POI diz comércio: é o achado que o
                       --                  produto existe para encontrar
                       --
                       -- Vem por LEFT JOIN porque a maioria dos pontos não tem
                       -- ligação nenhuma (17.470 em Canoas), e exigir a junção
                       -- os tiraria do mapa.
                       c.cruz_flag, c.num_ligacao,
                       a.veredito, a.motivo, a.recomendar_visita, a.tipo_construcao,
                       COALESCE(p.revisar_manual, false) AS revisar_manual, p.cidade,
                       p.place_id
                FROM pois p
                LEFT JOIN analise_ia a ON a.poi_id = p.id
                LEFT JOIN cadastro_cliente c ON c.poi_id = p.id
                WHERE p.match_valido IS NOT FALSE
                  AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL
                  -- O PONTO FUNDIDO NAO EXISTE MAIS COMO PONTO.
                  --
                  -- Ele virou aba de outro, e o registro so fica no banco para
                  -- a fusao poder ser desfeita. Sem esta linha o mapa mostrava
                  -- as duas coisas: o ponto sobrevivente E o absorvido, lado a
                  -- lado, e a deduplicacao inteira nao aparecia para quem olha.
                  --
                  -- MEDIDO em Canoas, 27/08/2026: o mapa devolvia 49.636 pontos
                  -- quando a cidade deduplicada tem 34.487. Os 14.320 fundidos
                  -- continuavam desenhados, e a queixa "nao e pra ter mais que
                  -- 30 mil pontos" era sobre isto — o banco ja estava certo, o
                  -- mapa e que nao tinha sido avisado.
                  AND p.fundido_em IS NULL
                  -- E o ponto sem NENHUMA ficha ativa nao tem o que mostrar: as
                  -- fontes que o sustentavam foram desvinculadas. Ele fica no
                  -- banco, auditavel, e some do mapa.
                  AND EXISTS (SELECT 1 FROM vinculo_poi v
                               WHERE v.poi_id = p.id AND v.estado = 'vinculado')""")
            cols = ["id", "nome", "categoria", "endereco", "telefone", "avaliacao",
                    "total_avaliacoes", "fonte", "fonte_dado", "status", "lat", "lng",
                    "tem_cnpj", "situacao_cadastral", "endereco_fonte", "tem_tel", "tem_sv", "tem_foto",
                    "multiorigem", "n_fontes", "cruz_flag", "num_ligacao",
                    "veredito", "motivo", "recomendar_visita", "tipo_construcao", "revisar_manual",
                    "cidade", "place_id"]
            return {"pois": [dict(zip(cols, row)) for row in cur.fetchall()]}
    finally:
        conn.close()


@app.get("/api/pois/{poi_id}")
def detalhe_poi(poi_id: int):
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, fonte, nome, categoria, endereco, telefone, website, avaliacao,
                       total_avaliacoes, plus_code, status_horario, lat_origem, lng_origem,
                       maps_lat, maps_lng, maps_url, place_id, status, distancia_m,
                       similaridade, nome_original, endereco_original, preco_medio,
                       fonte_dado, sessao, criado_em,
                       cnpj, razao_social, nome_fantasia, natureza_juridica, cnae,
                       situacao_cadastral, socios, instagram, email, resumo_avaliacoes,
                       streetview_path, fontes_web, endereco_fonte
                FROM pois WHERE id = %s""", (poi_id,))
            row = cur.fetchone()
            if not row:
                return JSONResponse({"erro": "POI não encontrado"}, status_code=404)
            cols = ["id", "fonte", "nome", "categoria", "endereco", "telefone", "website",
                    "avaliacao", "total_avaliacoes", "plus_code", "status_horario",
                    "lat_origem", "lng_origem", "maps_lat", "maps_lng", "maps_url",
                    "place_id", "status", "distancia_m", "similaridade", "nome_original",
                    "endereco_original", "preco_medio", "fonte_dado", "sessao", "criado_em",
                    "cnpj", "razao_social", "nome_fantasia", "natureza_juridica", "cnae",
                    "situacao_cadastral", "socios", "instagram", "email", "resumo_avaliacoes",
                    "streetview_path", "fontes_web", "endereco_fonte"]
            poi = dict(zip(cols, row))

            cur.execute("SELECT url FROM images_urls WHERE poi_id=%s ORDER BY ordem NULLS LAST, id LIMIT 12", (poi_id,))
            poi["fotos"] = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT autor, nota, texto, data FROM comentarios WHERE poi_id=%s ORDER BY id LIMIT 15", (poi_id,))
            poi["comentarios"] = [{"autor": a, "nota": n, "texto": t, "data": d} for a, n, t, d in cur.fetchall()]
            cur.execute("SELECT dia, horario FROM horario_funcionamento WHERE poi_id=%s ORDER BY id", (poi_id,))
            poi["horarios"] = [{"dia": d, "horario": h} for d, h in cur.fetchall()]

            # Análise visual por IA (descrever_imagens.py) — veredito + evidências
            cur.execute("""SELECT veredito, motivo, veredito_motivo, equivalencia, confere,
                                  porte, pessoas_estimadas, atividade_real, tipo_construcao,
                                  outro_estabelecimento, recomendar_visita, recomendacao_motivo,
                                  n_imagens, resposta_json
                           FROM analise_ia WHERE poi_id=%s""", (poi_id,))
            a = cur.fetchone()
            if a:
                acols = ["veredito", "motivo", "veredito_motivo", "equivalencia", "confere",
                         "porte", "pessoas_estimadas", "atividade_real", "tipo_construcao",
                         "outro_estabelecimento", "recomendar_visita", "recomendacao_motivo",
                         "n_imagens", "resposta_json"]
                ia = dict(zip(acols, a))
                perc = (ia.pop("resposta_json") or {})
                perc = perc.get("_percepcao", {}) if isinstance(perc, dict) else {}
                ia["ramo_visto"] = perc.get("ramo_visto")
                ia["nome_visto"] = perc.get("nome_visto")
                # todos os ângulos do street view (fachada + giro 360 + panoramas)
                cur.execute("""SELECT angulo FROM streetview_imgs
                               WHERE poi_id=%s AND angulo IS NOT NULL ORDER BY id""", (poi_id,))
                ia["angulos_sv"] = [r[0] for r in cur.fetchall()]
                poi["ia"] = ia

            # Leitura de fachada cadastral (avaliar_fachada.py). É OUTRA coisa que
            # a análise visual acima: aquela julga se o POI serve para visita,
            # esta lê o imóvel para o cadastro de saneamento. Convivem no modal.
            cur.execute("""SELECT status, e_imovel, apta, uso_observado, tipologia,
                                  unidades_fisicas, ucs_energia, hidrometros,
                                  economias_base, numero_lido, numero_confere,
                                  atividade_no_alvo, confianca, oportunidades,
                                  anotacao, modelo, criado_em
                             FROM fachada_anotacao WHERE poi_id=%s""", (poi_id,))
            f = cur.fetchone()
            if f:
                fc = ["status", "e_imovel", "apta", "uso_observado", "tipologia",
                      "unidades_fisicas", "ucs_energia", "hidrometros",
                      "economias_base", "numero_lido", "numero_confere",
                      "atividade_no_alvo", "confianca", "oportunidades",
                      "anotacao", "modelo", "criado_em"]
                fa = dict(zip(fc, f))
                anot = fa.pop("anotacao") or {}
                atr = (anot.get("atributos") or {})
                uso = atr.get("uso") or {}
                fa["letreiro"] = (uso.get("atividade_letreiro") or {}).get("valor")
                fa["estabelecimento"] = (uso.get("nome_estabelecimento_visivel") or {}).get("valor")
                fa["descricao"] = (uso.get("descricao_atividade_funcional") or {}).get("valor")
                fa["situacao"] = (uso.get("situacao_estabelecimento_na_data_imagem") or {}).get("valor")
                fa["data_imagem"] = (anot.get("imagem") or {}).get("data_captura")
                fa["alertas"] = anot.get("alertas") or []
                poi["fachada"] = fa

            # AS MESMAS ABAS DA BANCADA, tambem aqui.
            #
            # Este e o popup que abre ao CLICAR NUM PONTO no mapa — a tela que
            # o usuario realmente usa para olhar um estabelecimento. Ela montava
            # endereco, telefone, site, Instagram e preco a mao, um `if` por
            # campo, e por isso nao via nada que a extracao aprendeu depois.
            #
            # Agora le o mesmo catalogo que a fila do supervisor le. Campo novo
            # aparece nas duas telas de uma vez, e nenhuma delas precisa saber
            # que campo e esse.
            try:
                import ficha_abas
                poi["abas"] = ficha_abas.montar(conn, poi, e_root=False)
            except Exception:
                poi["abas"] = []      # a ficha nao pode cair por causa das abas
            return poi
    finally:
        conn.close()


@app.get("/api/sv/{poi_id}/{angulo}")
def sv_img(poi_id: int, angulo: str):
    """Serve a imagem do Street View (fachada/giro 360/panorama).

    O byte vem do Storage desde 12/08/2026, com queda para a coluna `dados`
    enquanto ela existir. O navegador continua pedindo a mesma URL — quem mudou
    foi de onde o servidor busca, e é por isso que o front não precisou saber."""
    import imagens
    conn = realtime_ingest.conectar()
    try:
        b = imagens.streetview_do_poi(poi_id, conn, angulo, limite=1)
        if not b:
            return JSONResponse({"erro": "sem imagem"}, status_code=404)
        return Response(content=b[0], media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})
    finally:
        conn.close()


def _dentro_do_anel(anel, lat, lng) -> bool:
    """Ponto dentro do polígono, por contagem de cruzamentos (ray casting).

    É o MESMO algoritmo que a ficha do polígono usa no navegador. Duas
    implementações do mesmo teste divergem, e o dia em que divergirem o cartão
    lateral e a ficha vão discordar sobre a mesma área.
    """
    dentro = False
    n = len(anel)
    j = n - 1
    for i in range(n):
        ai, aj = anel[i], anel[j]
        if (ai[0] > lat) != (aj[0] > lat) and \
           lng < (aj[1] - ai[1]) * (lat - ai[0]) / (aj[0] - ai[0]) + ai[1]:
            dentro = not dentro
        j = i
    return dentro


def _anel_da_area(cur) -> list | None:
    """Os vértices da área desenhada, pelo cursor da requisição.

    `area_utils.carregar_area()` abre conexão própria, e medido aqui isso
    custava 188 ms — mais que todo o resto do recorte somado. A requisição já
    tem uma conexão com a identidade certa; abrir outra para ler seis vértices
    é o gasto mais caro do cartão.
    """
    try:
        cur.execute("SELECT polygon FROM area_trabalho WHERE nome = %s",
                    (area_utils.AREA_PADRAO,))
        r = cur.fetchone()
    except Exception:
        return None
    if not r or not r[0] or len(r[0]) < 3:
        return None
    return [[float(a), float(b)] for a, b in r[0]]


def _materializar_escopo(cur, temp, sql_pontos, anel, params=()) -> int:
    """Roda o SQL da caixa envolvente, aplica o teste exato e grava os ids.

    `sql_pontos` devolve `(id, lat, lng)` já recortado pelo retângulo. O teste
    de raio roda aqui, sobre o que sobrou. Devolve quantos ficaram dentro.
    """
    cur.execute(sql_pontos, params)
    dentro = [(i,) for i, la, ln in cur.fetchall()
              if la is not None and ln is not None and _dentro_do_anel(anel, la, ln)]
    cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {temp} (id bigint PRIMARY KEY) "
                "ON COMMIT DROP")
    cur.execute(f"TRUNCATE {temp}")
    if dentro:
        psycopg2.extras.execute_values(
            cur, f"INSERT INTO {temp} (id) VALUES %s", dentro)
    return len(dentro)


def _caixa(anel):
    lats = [p[0] for p in anel]
    lngs = [p[1] for p in anel]
    return (min(lats), max(lats), min(lngs), max(lngs))


def _escopo_da_area(cur) -> tuple[str, str] | None:
    """Materializa numa TEMP TABLE os POIs dentro da área desenhada.

    POR QUE EM DOIS PASSOS, e não num `ST_Contains`
    ------------------------------------------------
    O PostGIS deste banco vive no schema `extensions`, e o papel
    `app_user` não tem USAGE nele — nem o tipo `geometry` resolve
    pela conexão do produto. `ST_Contains` só funciona contra o banco de
    REFERÊNCIA (5443). Liberar o schema exigiria superusuário, e a decisão de
    28/08/2026 foi não depender disso.

    Então: o SQL corta pela CAIXA ENVOLVENTE (índice `pois_coord_por_tenant`,
    migração 0039) e o teste exato do polígono roda aqui, sobre o que sobrou.
    Para uma área de bairro o retângulo já elimina quase tudo, e o custo que
    resta é proporcional ao que o operador desenhou, não ao tamanho da base.

    A temp table existe porque as 8 consultas do cartão precisam do MESMO
    recorte: refazer o teste em cada uma seria oito vezes o mesmo trabalho.

    Devolve `(predicado_em_pois, predicado_em_p)` ou `None` se não há área.
    """
    anel = _anel_da_area(cur)
    if not anel:
        return None
    _materializar_escopo(
        cur, "_escopo_area",
        """SELECT id, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
             FROM pois
            WHERE fundido_em IS NULL
              AND COALESCE(maps_lat, lat_origem) BETWEEN %s AND %s
              AND COALESCE(maps_lng, lng_origem) BETWEEN %s AND %s""",
        anel, _caixa(anel))
    return ("id IN (SELECT id FROM _escopo_area)",
            "p.id IN (SELECT id FROM _escopo_area)")


def _escopo_do_cadastro(cur) -> str | None:
    """O mesmo recorte, do lado das LIGAÇÕES do cliente.

    Elas têm coordenada própria (`lat`/`lng`, 100% preenchidas nas 102.065
    linhas), então o recorte não precisa passar pelo POI — o que também é o
    certo: uma ligação sem POI continua contando na área onde ela está, e é
    justamente ela que forma a fila de vinculação humana.

    Índice `ix_cad_geo_por_tenant`, migração 0040.
    """
    anel = _anel_da_area(cur)
    if not anel:
        return None
    _materializar_escopo(
        cur, "_escopo_cadastro",
        """SELECT id, lat, lng FROM cadastro_cliente
            WHERE lat BETWEEN %s AND %s AND lng BETWEEN %s AND %s""",
        anel, _caixa(anel))
    return "id IN (SELECT id FROM _escopo_cadastro)"


@app.get("/api/proxies")
def proxies_monitor(horas: int = 24):
    """O plano de proxies e o consumo dele — inventário, estado e histórico.

    DE ONDE VEM CADA COISA, porque não é tudo do mesmo lugar:

        plano     `proxy_ip`, escrita pelo pool a cada carga. É o que se PAGA.
        estado    DERIVADO dos eventos, não lido do pool: ele vive dentro do
                  processo de mineração no i9, e o servidor não o alcança.
        consumo   `proxy_evento`, agregada na janela pedida.

    "EM CASTIGO AGORA" É DERIVADO, e a conta é o evento mais recente de castigo
    de cada IP somado à duração dele: `em + segundos` ainda no futuro. Guardar um
    booleano "está de castigo" seria estado a expirar sozinho, e ninguém estaria
    lá para apagá-lo quando o cooldown vencesse.

    "EM USO" é o IP com um `pegou` sem `devolveu` depois, dentro da janela curta
    — um worker que morreu sem devolver deixaria o IP marcado para sempre, então
    a janela é o que impede o número de mentir para cima.

    A JANELA É OBRIGATÓRIA em toda consulta. `proxy_evento` cresce por run, e
    varrer a tabela inteira para pintar um cartão seria trocar leitura barata por
    trabalho pesado a cada abertura do modal.
    """
    horas = max(1, min(int(horas or 24), 24 * 90))
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT count(*), count(*) FILTER (WHERE ativo),
                                  coalesce(max(visto_em), now())
                             FROM proxy_ip""")
            total, ativos, visto = cur.fetchone()

            cur.execute("""SELECT coalesce(pais, '?'), count(*)
                             FROM proxy_ip WHERE ativo GROUP BY 1 ORDER BY 2 DESC""")
            por_pais = {p: n for p, n in cur.fetchall()}

            # O ÚLTIMO EVENTO DE CADA IP decide o estado. `DISTINCT ON` com o
            # índice `(proxy_id, em DESC)` faz isso sem varrer o histórico.
            cur.execute("""
                WITH ultimo AS (
                    -- O `id` DESEMPATA, e isto é conserto de defeito. Os
                    -- eventos vão ao banco EM LOTE, num INSERT só: o `now()`
                    -- do default é o mesmo para todos, e "o último evento
                    -- deste IP" ficava ambíguo. Medido: um IP que tinha tomado
                    -- castigo e outro que fora devolvido apareceram os dois
                    -- como "em uso", porque o `pegou` do mesmo lote empatou e
                    -- venceu. O `bigserial` guarda a ordem real de inserção.
                    SELECT DISTINCT ON (proxy_id) proxy_id, tipo, em, segundos
                      FROM proxy_evento
                     WHERE em > now() - interval '48 hours'
                     ORDER BY proxy_id, em DESC, id DESC
                )
                SELECT proxy_id, tipo, em, segundos,
                       (tipo = 'castigo' AND em + (segundos || ' seconds')::interval > now())
                         AS castigo_ativo,
                       (tipo = 'pegou' AND em > now() - interval '15 minutes')
                         AS em_uso
                  FROM ultimo""")
            estados = {r[0]: {"tipo": r[1], "em": r[2], "castigo": r[4], "uso": r[5]}
                       for r in cur.fetchall()}

            cur.execute("""SELECT proxy_id,
                                  count(*) FILTER (WHERE tipo = 'pegou'),
                                  count(*) FILTER (WHERE tipo = 'castigo'),
                                  coalesce(sum(bytes), 0)
                             FROM proxy_evento
                            WHERE em > now() - make_interval(hours => %s)
                            GROUP BY 1""", (horas,))
            uso = {r[0]: {"pegou": r[1], "castigo": r[2], "bytes": int(r[3] or 0)}
                   for r in cur.fetchall()}

            cur.execute("""SELECT tipo, count(*), coalesce(sum(bytes), 0)
                             FROM proxy_evento
                            WHERE em > now() - make_interval(hours => %s)
                            GROUP BY 1""", (horas,))
            consumo = {t: {"n": n, "bytes": int(b or 0)} for t, n, b in cur.fetchall()}

            cur.execute("""SELECT coalesce(motivo, 'sem motivo'), count(*)
                             FROM proxy_evento
                            WHERE tipo = 'castigo'
                              AND em > now() - make_interval(hours => %s)
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", (horas,))
            motivos = [{"motivo": m, "n": n} for m, n in cur.fetchall()]

            cur.execute("""SELECT coalesce(etapa, 'sem etapa'), count(*)
                             FROM proxy_evento
                            WHERE tipo = 'castigo'
                              AND em > now() - make_interval(hours => %s)
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", (horas,))
            etapas = [{"etapa": e, "n": n} for e, n in cur.fetchall()]

            cur.execute("""SELECT id, endereco, porta, coalesce(pais, ''),
                                  coalesce(cidade, ''), ativo
                             FROM proxy_ip ORDER BY pais, endereco""")
            itens = []
            for pid, end, porta, pais, cidade, ativo in cur.fetchall():
                e = estados.get(pid) or {}
                u = uso.get(pid) or {}
                if not ativo:
                    estado = "fora_do_plano"
                elif e.get("castigo"):
                    estado = "castigo"
                elif e.get("uso"):
                    estado = "em_uso"
                elif config.PROXY_PAIS and pais.upper() != config.PROXY_PAIS:
                    estado = "reservado"
                else:
                    estado = "livre"
                itens.append({
                    "id": pid, "endereco": end, "porta": porta,
                    "pais": pais, "cidade": cidade, "estado": estado,
                    "usos": u.get("pegou", 0), "castigos": u.get("castigo", 0),
                    "bytes": u.get("bytes", 0),
                    "ultimo": e["em"].isoformat() if e.get("em") else None,
                })

            contagem = {}
            for i in itens:
                contagem[i["estado"]] = contagem.get(i["estado"], 0) + 1

            return {
                "janela_horas": horas,
                "pais_ativo": config.PROXY_PAIS,
                "plano": {"total": total, "ativos": ativos,
                          "por_pais": por_pais,
                          "visto_em": visto.isoformat() if visto else None},
                "por_estado": contagem,
                "consumo": consumo,
                "motivos_de_castigo": motivos,
                "castigo_por_etapa": etapas,
                "itens": itens,
            }
    finally:
        conn.close()


@app.get("/api/stats")
def stats(cidade: str = "", area: int = 0):
    """Estatísticas do banco.

    Com `?cidade=` filtra por município (casa com `pois.cidade`,
    case-insensitive) — o mapa seleciona um município por clique na divisa.

    Com `?area=1` filtra pela ÁREA DESENHADA à mão, que é o que o operador vê no
    mapa. Sem isto o cartão mostrava a base inteira ao lado de um mapa recortado:
    o número não descrevia nada do que estava na tela. Os dois se combinam; se a
    área não existir mais no banco, o pedido cai de volta para o município.
    """
    cidade = (cidade or "").strip()
    wp = "lower(cidade) = lower(%s)" if cidade else "TRUE"      # filtro em pois
    wj = "lower(p.cidade) = lower(%s)" if cidade else "TRUE"    # filtro em join com p
    pc = [cidade] if cidade else []
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            if area:
                rec = _escopo_da_area(cur)
                if rec:
                    wp = f"({wp}) AND {rec[0]}"
                    wj = f"({wj}) AND {rec[1]}"
            cur.execute(f"SELECT status, COUNT(*) FROM pois WHERE {wp} GROUP BY status", pc)
            por_status = {s or "?": n for s, n in cur.fetchall()}
            cur.execute(f"SELECT fonte, COUNT(*) FROM pois WHERE {wp} GROUP BY fonte", pc)
            por_fonte = {s or "?": n for s, n in cur.fetchall()}
            cur.execute(f"""SELECT COUNT(*), COUNT(telefone), COUNT(cnpj),
                           COUNT(CASE WHEN streetview_path IS NOT NULL AND streetview_path<>'NA' THEN 1 END)
                           FROM pois WHERE match_valido IS NOT FALSE AND {wp}""", pc)
            validos, com_tel, com_cnpj, com_sv = cur.fetchone()
            cur.execute(f"SELECT COUNT(*) FROM images_urls i JOIN pois p ON p.id=i.poi_id WHERE {wj}", pc)
            fotos = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM comentarios c JOIN pois p ON p.id=c.poi_id WHERE {wj}", pc)
            comentarios = cur.fetchone()[0]
            cur.execute(f"""SELECT a.veredito, COUNT(*) FROM analise_ia a
                            JOIN pois p ON p.id=a.poi_id WHERE {wj} GROUP BY a.veredito""", pc)
            por_veredito = {v or "?": n for v, n in cur.fetchall()}
            cur.execute(f"""SELECT COUNT(*) FROM analise_ia a JOIN pois p ON p.id=a.poi_id
                            WHERE a.recomendar_visita AND {wj}""", pc)
            recomendar = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM analise_ia a JOIN pois p ON p.id=a.poi_id WHERE {wj}", pc)
            analisados = cur.fetchone()[0]
            return {"validos": validos, "por_status": por_status, "por_fonte": por_fonte,
                    "cidade": cidade or None, "fotos": fotos, "comentarios": comentarios,
                    "com_telefone": com_tel, "com_cnpj": com_cnpj, "com_streetview": com_sv,
                    "analisados": analisados, "aprovados": por_veredito.get("aprovado", 0),
                    "reprovados": por_veredito.get("reprovado", 0), "recomendar_visita": recomendar}
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# API — planilha modelo + upload
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/template")
def template_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "POIs"
    headers = ["nome", "endereco_completo", "lat", "lon", "uf"]
    exemplos = [
        ["Mercadinho São José", "Av. Presidente Vargas, 1520 - Centro, Parnaíba - PI, 64200-100", -2.90613, -41.77542, "PI"],
        ["Farmácia Pague Menos", "R. Riachuelo, 300 - Centro, Parnaíba - PI", -2.90811, -41.77310, "PI"],
        ["Restaurante Sabor da Terra", "", -2.91240, -41.76888, "PI"],
    ]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cel = ws.cell(row=1, column=c)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="1A73E8")
        cel.alignment = Alignment(horizontal="center")
    for ex in exemplos:
        ws.append(ex)
    larguras = [34, 58, 12, 12, 6]
    for i, w in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws2 = wb.create_sheet("LEIA-ME")
    notas = [
        "PLANILHA MODELO — ComercialRadar",
        "",
        "Colunas (a detecção de cabeçalho é flexível, mas use estes nomes):",
        "  nome               → OBRIGATÓRIO. Nome do estabelecimento.",
        "  endereco_completo  → recomendado. Endereço com cidade/UF melhora muito o match.",
        "  lat / lon          → recomendado. Coordenada aproximada (ponto decimal).",
        "  uf                 → sigla do estado (ex.: PI). Uma UF por planilha.",
        "",
        "As linhas de exemplo desta planilha podem ser apagadas.",
        "Os pontos só serão aceitos se caírem DENTRO do polígono desenhado no mapa.",
    ]
    for n in notas:
        ws2.append([n])
    ws2.column_dimensions["A"].width = 95

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modelo_comercialradar.xlsx"'})


# ──────────────────────────────────────────────────────────────────────────
# Dashboard — o retrato de cada cidade e o que ela custou
# ──────────────────────────────────────────────────────────────────────────
# Preços praticados neste projeto, em US$. Só entra aqui o que TEM preço: a
# captura+OCR, o Street View e a Receita local rodam na máquina e não custam
# chamada — o que custa neles é tempo, e tempo aparece como horas, não como
# dinheiro inventado.
PRECO = {
    "places_nearby": 0.032,   # Places API Nearby Search, por chamada (motor pago)
    "llm_in": 0.15 / 1e6,     # gpt-4o-mini entrada, US$/token
    "llm_out": 0.60 / 1e6,    # gpt-4o-mini saída
    "proxy_mes": 45.0,        # Webshare, 100 IPs residenciais estáticos
}
# Tabela do Google Maps Platform, por chamada, para o CENÁRIO ALTERNATIVO: quanto
# sairia fazer pela API paga o mesmo que a captura+OCR e o Street View fazem de
# graça aqui. São preços de tabela publicados; o Google muda de tempos em tempos
# e há camada gratuita mensal — por isso o número entra como COMPARATIVO, nunca
# somado ao que foi realmente gasto.
PRECO_GOOGLE = {
    "nearby": 0.032,          # Nearby Search — descoberta dos POIs
    "details": 0.017,         # Place Details — telefone, endereço, horário
    "foto": 0.007,            # Place Photos
    "streetview": 0.007,      # Street View Static
}
POIS_POR_NEARBY = 20          # o Nearby devolve no máximo 20 por chamada
USD_BRL = 5.45
# Ritmos MEDIDOS nesta base, para converter volume em horas de máquina.
RITMO_H = {"streetview": 1380, "web": 540, "captura": 900}

# ASSINATURAS: custo que corre no mês inteiro, rode-se muito ou nada. Não entra
# rateado por hora — o proxy custa os mesmos US$ 45 se a máquina ficar parada, e
# apresentar "US$ 1,68 de proxy" dava a impressão de que rodar mais sairia mais
# caro. O valor da IA é editável no painel: é assinatura em dólar e muda de mês
# para mês conforme o plano.
ASSINATURAS = {
    "webshare": {"usd": 45.0, "rotulo": "Webshare — 100 IPs residenciais estáticos"},
    "ia": {"usd": 200.0, "rotulo": "Assinatura de IA (Claude)", "editavel": True},
}

# FAIXAS DE QUALIDADE, mutuamente exclusivas e na ordem em que são testadas: um
# POI cai na PRIMEIRA que aceitar. É o que dá sentido a "valor por POI" — um
# registro com telefone e CNPJ conferido não vale o mesmo que um ponto no mapa.
#
# `coalesce` em TODO campo de texto não é preciosismo: `cnpj <> ''` devolve NULL
# quando o cnpj é NULL, e a exclusão das faixas seguintes usa `NOT (...)` —
# `NOT NULL` é NULL, e a linha some. Sem isso as faixas C e D deram zero e a
# soma das faixas batia 8.447 num total de 21.700.
_TEL = "coalesce(p.telefone,'') <> ''"
_CNPJ = "coalesce(p.cnpj,'') <> ''"
_END = "p.endereco IS NOT NULL"
_SV = "coalesce(p.streetview_path,'') NOT IN ('', 'NA')"
FAIXAS = [
    ("a", "Completo com CNPJ conferido",
     f"{_TEL} AND {_CNPJ} AND coalesce(p.cnpj_conf,'') LIKE '4/4%%' AND {_END}"),
    ("b", "Telefone + CNPJ (confiança menor)", f"{_TEL} AND {_CNPJ} AND {_END}"),
    ("c", "Telefone + endereço, sem CNPJ", f"{_TEL} AND {_END}"),
    ("d", "Localizado com fachada, sem telefone", f"{_END} AND {_SV}"),
    ("e", "Só o ponto no mapa", "TRUE"),
]


# Os critérios saem do banco em snake_case somado ("dv+base_nacional+uf+municipio")
# porque lá eles são feitos para filtro. Na tela, viram frase.
_PT_CRIT = {
    "dv": "dígito verificador",
    "base_nacional": "existe na Receita",
    "uf": "UF confere",
    "municipio": "município confere",
    "receita_local": "achado na Receita local",
    "endereco": "pelo endereço",
    "endereco_unico": "endereço com uma empresa só",
    "nome": "nome confere",
    "so_estrutura": "só tem forma de CNPJ",
}


def _CRITERIO_PT(bruto: str) -> str:
    if not bruto:
        return "origem não registrada"
    return " · ".join(_PT_CRIT.get(p, p) for p in bruto.split("+"))


def _ORD_NOTA(nota: str) -> int:
    try:
        return int(nota.split("/")[0])
    except (ValueError, AttributeError):
        return -1


@app.get("/api/dashboard")
def dashboard(cidade: str = ""):
    """Retrato de uma cidade (ou de todas) + custo estimado do que foi feito."""
    cidade = (cidade or "").strip()
    # `match_valido IS NOT FALSE` — o MESMO corte do mapa. Sem isto o dashboard
    # dizia 22.231 e o mapa 22.221, e a diferença eram POIs com enriquecimento
    # incoerente que ninguém deveria estar contando. Dois números para a mesma
    # pergunta, na mesma tela, é pior que um número errado: quem lê não sabe em
    # qual acreditar.
    base = "p.match_valido IS NOT FALSE"
    w = f"{base} AND lower(p.cidade) = lower(%s)" if cidade else base
    pc = [cidade] if cidade else []
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT cidade, count(*) FROM pois
                            WHERE cidade IS NOT NULL AND cidade <> ''
                            GROUP BY 1 ORDER BY 2 DESC""")
            cidades = [{"cidade": c, "pois": n} for c, n in cur.fetchall()]

            cur.execute(f"""
                SELECT count(*),
                  count(*) FILTER (WHERE p.telefone <> ''),
                  count(*) FILTER (WHERE p.cnpj <> ''),
                  count(*) FILTER (WHERE p.endereco IS NOT NULL),
                  count(*) FILTER (WHERE p.email <> ''),
                  count(*) FILTER (WHERE p.website <> ''),
                  count(*) FILTER (WHERE p.instagram <> ''),
                  count(*) FILTER (WHERE p.facebook <> ''),
                  count(*) FILTER (WHERE p.streetview_path IS NOT NULL
                                     AND p.streetview_path <> 'NA'),
                  count(*) FILTER (WHERE p.streetview_path = 'NA'),
                  count(*) FILTER (WHERE p.total_avaliacoes > 0),
                  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM images_urls i
                                                  WHERE i.poi_id = p.id)),
                  count(*) FILTER (WHERE p.place_id LIKE 'planilha:%%')
                  FROM pois p WHERE {w}""", pc)
            r = cur.fetchone()
            campos = ["total", "telefone", "cnpj", "endereco", "email", "website",
                      "instagram", "facebook", "streetview", "sem_panorama",
                      "avaliacoes", "fotos", "importados"]
            cob = dict(zip(campos, r))

            # O rótulo cru do banco é "4/4 receita_local+endereco+nome" — bom para
            # filtrar em SQL, ilegível numa tabela. Aqui ele é PARTIDO em nota
            # (4/4) e critério legível, e a nota vira a chave de ordenação: antes
            # a tabela vinha por quantidade e alternava 2/4, 4/4, 4/4, sem rótulo,
            # 2/4 — uma escada sem degrau, impossível de ler de cima para baixo.
            cur.execute(f"""SELECT coalesce(split_part(p.cnpj_conf,' (',1),''),
                                   count(*) FROM pois p
                             WHERE {w} AND p.cnpj <> '' GROUP BY 1 ORDER BY 2 DESC""", pc)
            conf = []
            for bruto, n in cur.fetchall():
                nota, _, criterio = (bruto or "").partition(" ")
                if "/" not in nota:
                    nota, criterio = "?", "origem não registrada"
                conf.append({"nota": nota, "criterio": _CRITERIO_PT(criterio), "n": n,
                             "bruto": bruto})
            conf.sort(key=lambda c: (-_ORD_NOTA(c["nota"]), -c["n"]))

            # faixas de qualidade — cada POI numa faixa só, testadas em ordem
            faixas, ja = [], []
            for chave, rot, cond in FAIXAS:
                antes = " AND NOT (" + " OR ".join(ja) + ")" if ja else ""
                cur.execute(f"SELECT count(*) FROM pois p "
                            f"WHERE {w} AND ({cond}){antes}", pc)
                faixas.append({"chave": chave, "rotulo": rot, "n": cur.fetchone()[0]})
                ja.append(f"({cond})")

            cur.execute(f"""SELECT p.fonte, coalesce(p.fonte_dado,'-'), count(*)
                              FROM pois p WHERE {w} GROUP BY 1,2 ORDER BY 3 DESC""", pc)
            origem = [{"fonte": a, "dado": b, "n": c} for a, b, c in cur.fetchall()]

            cur.execute(f"""SELECT p.status, count(*) FROM pois p WHERE {w}
                             GROUP BY 1 ORDER BY 2 DESC""", pc)
            status = [{"status": a, "n": b} for a, b in cur.fetchall()]

            # IMAGEM é o dado mais caro de produzir aqui — cada fachada custou uma
            # sessão de navegador, e cada foto uma abertura de ficha no Maps. O
            # cartão antigo mostrava três linhas; estas contas respondem o que se
            # pergunta na prática: quantos POIs têm fachada, quantos têm foto,
            # quanto ocupa e quanto DEIXOU de ser capturado por não haver panorama.
            cur.execute(f"""SELECT count(*), coalesce(sum(s.bytes_tam),0),
                                   count(DISTINCT s.poi_id)
                              FROM streetview_imgs s JOIN pois p ON p.id = s.poi_id
                             WHERE {w}""", pc)
            sv_n, sv_bytes, sv_pois = cur.fetchone()

            cur.execute(f"""SELECT count(*), coalesce(sum(i.bytes_tam),0),
                                   count(DISTINCT i.poi_id),
                                   count(*) FILTER (WHERE i.storage_path IS NOT NULL)
                              FROM images_urls i JOIN pois p ON p.id = i.poi_id
                             WHERE {w}""", pc)
            fo_n, fo_bytes, fo_pois, fo_baixadas = cur.fetchone()

            # cadastro do cliente, se já houver
            cadastro = None
            # SEM o schema no nome: quem resolve é o `search_path`, que aponta
            # para `comercialradar`. Fixar 'public.' aqui fazia a checagem
            # devolver NULL e o painel dizer "nenhuma base de cadastro
            # importada" com 102.065 imóveis gravados — sobra do tempo em que
            # tudo morava em `public`.
            cur.execute("SELECT to_regclass('cadastro_cliente')")
            if cur.fetchone()[0]:
                wc = "lower(cidade) = lower(%s)" if cidade else "TRUE"
                cur.execute(f"""SELECT coalesce(cruz_flag,'(não cruzado)'), count(*)
                                  FROM cadastro_cliente WHERE {wc}
                                 GROUP BY 1 ORDER BY 2 DESC""", pc)
                flags = [{"flag": a, "n": b} for a, b in cur.fetchall()]
                if flags:
                    import cadastro_cliente as CC
                    cadastro = {"flags": flags, "descricoes": CC.FLAGS,
                                "total": sum(f["n"] for f in flags)}
            # ── CONVERGÊNCIA: meu mapeamento × cadastro do cliente ──
            # As duas bases medem o mesmo território por caminhos diferentes, e o
            # que interessa não é o total de cada uma: é onde elas se encontram,
            # onde uma viu o que a outra não viu, e o que cada lado acrescentou.
            cur.execute(f"""
                SELECT count(*) FILTER (WHERE c.poi_id IS NOT NULL),
                       count(*) FILTER (WHERE c.poi_id IS NULL),
                       count(*) FILTER (WHERE c.poi_id IS NOT NULL AND c.e_comercial),
                       count(*) FILTER (WHERE c.poi_id IS NOT NULL AND NOT c.e_comercial),
                       -- O DENOMINADOR QUE FAZ SENTIDO: o cadastro comercial.
                       -- Comparar 22 mil POIs comerciais com 102 mil imoveis, a
                       -- maioria residencias, produz uma "cobertura" que nao
                       -- significa cobertura de coisa alguma.
                       --
                       -- Sem simbolo de porcentagem aqui de proposito: o psycopg2
                       -- le esse caractere como marcador de parametro, inclusive
                       -- dentro de comentario SQL, e a consulta morre com
                       -- IndexError longe da causa.
                       count(*) FILTER (WHERE c.e_comercial),
                       count(*) FILTER (WHERE c.e_comercial AND c.poi_id IS NULL)
                  FROM cadastro_cliente c
                 WHERE {'lower(c.cidade) = lower(%s)' if cidade else 'TRUE'}""", pc)
            (cad_casado, cad_so, cad_com, cad_nao_com,
             cad_com_total, cad_com_sem_poi) = cur.fetchone()

            cur.execute(f"""
                SELECT count(*),
                       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM cadastro_cliente c
                                                       WHERE c.poi_id = p.id)),
                       count(*) FILTER (WHERE p.place_id LIKE 'planilha:%%'),
                       count(*) FILTER (WHERE p.cnpj <> '')
                  FROM pois p WHERE {w}""", pc)
            poi_tot, poi_casado, poi_import, poi_cnpj = cur.fetchone()

            # o que cada lado ACRESCENTOU ao outro
            cur.execute(f"""
                SELECT count(*) FILTER (WHERE p.telefone <> ''),
                       count(*) FILTER (WHERE p.cnpj <> ''),
                       count(*) FILTER (WHERE p.streetview_path NOT IN ('', 'NA'))
                  FROM pois p JOIN cadastro_cliente c ON c.poi_id = p.id
                 WHERE {w}""", pc)
            deu_tel, deu_cnpj, deu_sv = cur.fetchone()

            convergencia = {
                "poi_total": poi_tot, "poi_casado": poi_casado,
                "poi_so_meu": poi_tot - poi_casado, "poi_importado": poi_import,
                "cad_total": cad_casado + cad_so, "cad_casado": cad_casado,
                "cad_so_deles": cad_so, "cad_comercial_casado": cad_com,
                "cad_nao_comercial_casado": cad_nao_com,
                # o recorte comercial, que é contra o que a comparação vale
                "cad_comercial_total": cad_com_total,
                "cad_comercial_sem_poi": cad_com_sem_poi,
                "eu_dei_telefone": deu_tel, "eu_dei_cnpj": deu_cnpj,
                "eu_dei_fachada": deu_sv,
            }

            # ── leitura de fachada: atributos novos ──
            cur.execute(f"""
                SELECT count(*),
                       count(*) FILTER (WHERE f.status = 'aprovado'),
                       count(*) FILTER (WHERE f.status = 'fora_escopo'),
                       count(*) FILTER (WHERE f.status = 'inapto'),
                       count(*) FILTER (WHERE f.medicao_coletiva),
                       count(*) FILTER (WHERE f.medicao_estado IN
                            ('tampa_ausente','tampa_quebrada','obstruido','soterrado')),
                       count(*) FILTER (WHERE f.medicao_acesso IN
                            ('inacessivel','obstruido_vegetacao','obstruido_veiculo',
                             'interno_requer_morador')),
                       count(*) FILTER (WHERE f.numero_confere IS FALSE),
                       count(*) FILTER (WHERE f.unidades_fisicas > f.economias_base)
                  FROM fachada_anotacao f JOIN pois p ON p.id = f.poi_id
                 WHERE {w}""", pc)
            r = cur.fetchone()
            fach = dict(zip(("lidas", "aprovadas", "fora_escopo", "inaptas",
                             "medicao_coletiva", "tampa_problema", "acesso_obstruido",
                             "numero_diverge", "unidades_acima"), r))
            for col, chave in (("estado_conservacao", "conservacao"),
                               ("tipo_edificacao", "tipos"),
                               ("uso_observado", "usos"),
                               ("medicao_abrigo", "abrigos")):
                cur.execute(f"""SELECT f.{col}, count(*) FROM fachada_anotacao f
                                  JOIN pois p ON p.id = f.poi_id
                                 WHERE {w} AND f.{col} IS NOT NULL
                                 GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", pc)
                fach[chave] = [{"v": a, "n": b} for a, b in cur.fetchall()]
            cur.execute(f"""SELECT coalesce(sum(f.pavimentos),0), count(f.pavimentos)
                              FROM fachada_anotacao f JOIN pois p ON p.id = f.poi_id
                             WHERE {w}""", pc)
            sp, np_ = cur.fetchone()
            fach["pavimentos_medio"] = round(sp / np_, 1) if np_ else None

        # ── custo ──
        # Só a fase Web tem preço por POI: ela abre navegador com proxy e chama
        # o LLM. `recuperado_web` é a marca de quem passou por ela.
        web = next((s["n"] for s in status if s["status"] == "recuperado_web"), 0)
        places = next((o["n"] for o in origem
                       if o["fonte"] == "pipeline" and o["dado"] == "maps"), 0)
        # ~2,4 k tokens de entrada e ~180 de saída por POI, medido nas rodadas
        llm = web * (2400 * PRECO["llm_in"] + 180 * PRECO["llm_out"])
        horas = {
            "street view": (cob["streetview"] + cob["sem_panorama"]) / RITMO_H["streetview"],
            "fase web": web / RITMO_H["web"],
            "captura + OCR": (cob["total"] - cob["importados"]) / RITMO_H["captura"],
        }
        h_total = sum(horas.values())
        # CENÁRIO ALTERNATIVO: o mesmo trabalho pela API paga do Google. Preço de
        # tabela, para dar escala ao que a captura+OCR economiza. Nunca somado ao
        # gasto real — é o custo que NÃO foi pago.
        proprios = cob["total"] - cob["importados"]
        google = {
            "nearby": math.ceil(proprios / POIS_POR_NEARBY) * PRECO_GOOGLE["nearby"],
            "details": proprios * PRECO_GOOGLE["details"],
            "foto": cob["fotos"] * PRECO_GOOGLE["foto"],
            "streetview": (cob["streetview"] + cob["sem_panorama"]) * PRECO_GOOGLE["streetview"],
        }
        custo = {
            # VARIÁVEL: só existe porque este volume rodou
            "llm_usd": round(llm, 4),
            "places_usd": 0.0,          # motor pago não foi usado nesta base
            "places_chamadas": places,
            # FIXO: corre no mês inteiro, rodando ou parado
            "assinaturas": [
                {"chave": k, "rotulo": v["rotulo"], "usd": v["usd"],
                 "editavel": v.get("editavel", False)}
                for k, v in ASSINATURAS.items()
            ],
            "google": {k: round(v, 2) for k, v in google.items()},
            "google_total_usd": round(sum(google.values()), 2),
            "horas": {k: round(v, 1) for k, v in horas.items()},
            "horas_total": round(h_total, 1),
            "usd_brl": USD_BRL,
            "pois": cob["total"],
        }
        custo["variavel_usd"] = round(custo["llm_usd"] + custo["places_usd"], 2)
        return {"cidade": cidade, "cidades": cidades, "cobertura": cob,
                "cnpj_confianca": conf, "faixas": faixas, "origem": origem,
                "status": status, "convergencia": convergencia, "fachada": fach,
                "streetview": {
                    "imagens": sv_n, "bytes": int(sv_bytes or 0), "pois": sv_pois,
                    "fotos": fo_n, "fotos_bytes": int(fo_bytes or 0),
                    "fotos_pois": fo_pois, "fotos_baixadas": fo_baixadas,
                },
                "cadastro": cadastro, "custo": custo}
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# Cadastro do cliente — modelo, prévia, confirmação e cruzamento
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/modelos")
def modelos_disponiveis():
    """Todo modelo de planilha que o sistema aceita, num lugar só.

    Antes o modelo existia só para a aba de importação de POIs, e o de cadastro
    não existia — quem fosse montar a planilha teria de adivinhar os nomes das
    colunas a partir do erro de importação."""
    import cadastro_cliente as CC
    return {"modelos": [
        {"id": "pois", "nome": "POIs para mineração",
         "descricao": "Lista de estabelecimentos a procurar no Maps.",
         "url": "/api/template", "formato": "xlsx",
         "colunas": ["nome", "endereco_completo", "lat", "lon", "uf"]},
        {"id": "cadastro", "nome": "Cadastro de clientes (base da empresa)",
         "descricao": f"Carteira de imóveis/ligações. {len(CC.MAPA)} colunas; "
                      f"só `num_ligacao` é obrigatória (é a chave).",
         "url": "/api/modelos/cadastro", "formato": "csv",
         "colunas": CC.COL_DB},
    ]}


@app.get("/api/modelos/cadastro")
def modelo_cadastro():
    import cadastro_cliente as CC
    txt = CC.modelo_csv()
    return Response(txt, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="modelo_cadastro_cliente.csv"'})


@app.post("/api/cadastro/previa")
async def cadastro_previa(file: UploadFile = File(...)):
    """Lê o arquivo, NÃO grava, e devolve o que veio para conferência no modal.

    Confirmar depois de ver é o ponto: são 102 mil linhas por arquivo, e um
    cabeçalho fora do padrão gravaria a base inteira com colunas trocadas."""
    import cadastro_cliente as CC
    nome = Path(file.filename or "cadastro.csv").name
    if not nome.lower().endswith(".csv"):
        return JSONResponse({"erro": "Envie um .csv"}, status_code=400)
    destino = UPLOADS / nome
    destino.write_bytes(await file.read())
    try:
        linhas, ref, avisos = CC.ler_arquivo(str(destino))
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=400)
    if not linhas:
        return JSONResponse({"erro": "nenhuma linha válida no arquivo"},
                            status_code=400)
    cidades = {}
    com_coord = comerciais = 0
    for d in linhas:
        cidades[d.get("cidade") or "?"] = cidades.get(d.get("cidade") or "?", 0) + 1
        com_coord += 1 if d.get("lat") is not None else 0
        comerciais += 1 if d.get("e_comercial") else 0
    amostra = [{k: linhas[i].get(k) for k in
                ("num_ligacao", "cidade", "categoria", "endereco",
                 "situacao_ligacao", "e_comercial", "lat", "lng")}
               for i in range(min(12, len(linhas)))]
    return {"arquivo": nome, "linhas": len(linhas), "referencia": ref,
            "avisos": avisos, "com_coordenada": com_coord,
            "comerciais": comerciais, "amostra": amostra,
            "cidades": sorted(cidades.items(), key=lambda x: -x[1])[:8],
            "colunas_tabela": CC.COL_DB}


@app.post("/api/cadastro/confirmar")
def cadastro_confirmar(body: dict = Body(...)):
    """Grava de fato e já cruza com os POIs da cidade."""
    import cadastro_cliente as CC
    arquivo = UPLOADS / Path(str(body.get("arquivo") or "")).name
    if not arquivo.exists():
        return JSONResponse({"erro": "arquivo não encontrado"}, status_code=400)
    cliente = (body.get("cliente") or "corsan").strip().lower()
    linhas, ref, _av = CC.ler_arquivo(str(arquivo))
    res = CC.importar(linhas, cliente, ref)
    cruz = None
    if body.get("cruzar", True):
        cidade = (body.get("cidade") or
                  (linhas[0].get("cidade") if linhas else "") or "").strip()
        if cidade:
            cruz = CC.cruzar(cidade)
    return {"importacao": res, "cruzamento": cruz, "flags": CC.FLAGS}


@app.post("/api/cadastro/cruzar")
def cadastro_cruzar(body: dict = Body(...)):
    import cadastro_cliente as CC
    cidade = (body.get("cidade") or "").strip()
    if not cidade:
        return JSONResponse({"erro": "informe a cidade"}, status_code=400)
    return {"cruzamento": CC.cruzar(cidade), "flags": CC.FLAGS}


@app.get("/api/cadastro/resumo")
def cadastro_resumo(cidade: str = "", area: int = 0):
    """O estado do cruzamento cadastro ↔ POI — SEM recruzar nada.

    O `POST /api/cadastro/cruzar` executa a etapa 9; este só LÊ o que ela
    gravou. A distinção não é cosmética: a tela principal precisa do número a
    cada carga, e disparar um cruzamento de 102 mil linhas para pintar um cartão
    seria trocar leitura por trabalho pesado a cada F5.

    OS DOIS LADOS DA CONTA, e é isso que a tela nova mostra:

        `com_poi`   ligações do cliente que casaram com um ponto
        `sem_poi`   ligações que nenhum ponto explica
        `poi_sem_ligacao`  o inverso — pontos que o cadastro não conhece, e que
                    viram a fila de vinculação humana

    `por_flag` é a regra de negócio: `ja_cadastrado` não se visita,
    `reclassificar_alta` é o CNPJ conferido e vai primeiro na fila.
    """
    cidade = (cidade or "").strip()
    wc = "cidade ILIKE %s" if cidade else "TRUE"
    pc = [cidade] if cidade else []
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            # `?area=1` RECORTA PELA ÁREA DESENHADA, e este cartão precisa
            # disso tanto quanto o de cima: "já comerciais no cadastro" ao lado
            # de "12 pontos novos" só faz sentido se os dois falarem do mesmo
            # pedaço do mapa. Sem isto, o 14.959 do município inteiro aparecia
            # encostado num número de bairro.
            #
            # A ligação tem coordenada PRÓPRIA (100% preenchida), então o
            # recorte não passa pelo POI — e é o certo: ligação sem POI continua
            # contando na área onde ela está, e é justamente ela que forma a
            # fila de vinculação humana.
            wca = wc
            if area:
                rec = _escopo_do_cadastro(cur)
                if rec:
                    wca = f"({wc}) AND {rec}"
            cur.execute(f"""SELECT count(*),
                                   count(*) FILTER (WHERE poi_id IS NOT NULL),
                                   count(*) FILTER (WHERE e_comercial),
                                   count(*) FILTER (WHERE e_comercial
                                                      AND poi_id IS NOT NULL)
                              FROM cadastro_cliente WHERE {wca}""", pc)
            total, com_poi, comerciais, comerciais_com_poi = cur.fetchone()

            cur.execute(f"""SELECT coalesce(cruz_flag, 'sem_flag'), count(*)
                              FROM cadastro_cliente WHERE {wca}
                             GROUP BY 1""", pc)
            por_flag = {f: n for f, n in cur.fetchall()}

            # O POI sem ligação é do lado dos PONTOS, então o filtro de cidade
            # vai em `pois` — e o fundido fica de fora, porque ele não é um
            # ponto: foi absorvido por outro.
            wp = "p.cidade ILIKE %s" if cidade else "TRUE"
            if area:
                recp = _escopo_da_area(cur)
                if recp:
                    wp = f"({wp}) AND {recp[1]}"
            cur.execute(f"""SELECT count(*) FROM pois p
                             WHERE p.fundido_em IS NULL AND {wp}
                               AND NOT EXISTS (SELECT 1 FROM cadastro_cliente c
                                                WHERE c.poi_id = p.id)""", pc)
            poi_sem_ligacao = cur.fetchone()[0]

            # `comerciais` é quantas ligações o CADASTRO já classifica como
            # comercial; `com_poi` é quantas casaram com um ponto nosso, de
            # QUALQUER classificação — inclusive as de reclassificar. O cartão
            # mostrava `com_poi` sob o rótulo "já comerciais no cadastro", e
            # eram coisas diferentes: 14.959 contra 11.749 na base inteira.
            return {"cidade": cidade or None, "ligacoes": total,
                    "comerciais": comerciais,
                    "comerciais_com_poi": comerciais_com_poi,
                    "com_poi": com_poi, "sem_poi": total - com_poi,
                    "poi_sem_ligacao": poi_sem_ligacao, "por_flag": por_flag}
    finally:
        conn.close()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    nome = Path(file.filename or "planilha.xlsx").name
    if not nome.lower().endswith((".xlsx", ".csv")):
        return JSONResponse({"erro": "Envie um arquivo .xlsx ou .csv"}, status_code=400)
    destino = UPLOADS / nome
    destino.write_bytes(await file.read())

    linhas = 0
    try:
        if nome.lower().endswith(".csv"):
            linhas = max(0, len(destino.read_text(encoding="utf-8-sig").splitlines()) - 1)
        else:
            from openpyxl import load_workbook
            wb = load_workbook(destino, read_only=True)
            linhas = max(0, (wb.active.max_row or 1) - 1)
            wb.close()
    except Exception:
        pass
    return {"ok": True, "arquivo": nome, "linhas": linhas}


# ──────────────────────────────────────────────────────────────────────────
# API — jobs
# ──────────────────────────────────────────────────────────────────────────
# Cada card da aba é um RECORTE da leitura, e o mesmo recorte serve para contar e
# para listar — assim o número do card e a lista que ele abre nunca divergem.
_RECORTES_FACHADA = {
    # ── A DECISÃO DA IA ────────────────────────────────────────────────────
    # Os três primeiros são o processo de quatro fases: `acao_recomendada` só
    # admite `aprovar`, `reprovar` ou `revisar`. `revisar` é o balde que
    # alimenta a fila do supervisor — sem card, a leitura ficava gravada e
    # ninguém via que havia trabalho humano esperando.
    # APROVAR TEM DUAS FORMAS desde a leitura 3.0.0, e a divergente é a que o
    # produto vende: comércio ativo num endereço que o cadastro do cliente
    # conhece por outro nome — ou não conhece. `aprovar` sozinho é da leitura
    # 2.0.0, que não fazia a distinção.
    "aprovadas":   ("Leituras aptas", "f.status = 'aprovado'"),
    "especifico":  ("Comércio confirmado — o esperado",
                    "f.acao_recomendada = 'aprovar_especifico'"),
    "divergente":  ("Comércio DIVERGENTE — outro nome no endereço",
                    "f.acao_recomendada = 'aprovar_divergente'"),
    "revisar":     ("A IA pediu revisão humana", "f.acao_recomendada = 'revisar'"),
    "reprovadas":  ("Sem comércio na cena", "f.acao_recomendada = 'reprovar'"),
    # O ponto pode estar na coordenada errada: a IA leu a cena e não achou o
    # estabelecimento nela. É o recorte que mais rápido paga recaptura.
    "alvo_ausente": ("Alvo não aparece na imagem", "f.alvo_encontrado = 'nao'"),
    # O nome REAL no imóvel, que é o achado que diverge do cadastro.
    "letreiro":    ("Letreiro legível na fachada",
                    "COALESCE(f.texto_do_letreiro, '') <> ''"),
    # Galpão pode ser qualquer coisa — depósito, igreja, transportadora — e é
    # onde os dados auxiliares mais mudam o veredito.
    "galpao":      ("Galpão", "f.tipo_imovel = 'galpao_industrial'"),
    "multiplas":   ("Múltiplas unidades no lote", "f.multiplas_unidades IS TRUE"),
    # Julgado SEM a fachada: a foto do Street View foi descartada e o veredito
    # saiu só das fotos do Maps. Não é erro — é o caso do muro cego de 2018 com
    # foto de cliente de 2025 —, mas é o que um auditor quer conferir primeiro.
    "so_foto":     ("Julgado sem a fachada",
                    "f.imagens_usadas IS NOT NULL AND f.imagens_usadas->>'fachada' IS NULL"),
    # ── O QUE O PRODUTO VENDE ──────────────────────────────────────────────
    # OPORTUNIDADE, na definição do negócio: é comercial segundo a IA e NÃO
    # consta como comercial na base do cliente. As duas metades importam —
    # comércio que o cliente já cadastrou como comércio não é achado, é cadastro
    # em dia; e imóvel que a IA reprovou não é oportunidade, é ponto sem sinal.
    #
    # `revisar` entra junto com `aprovar` porque a ação da IA é SUGESTÃO, não
    # decisão: o que está esperando olho humano segue sendo oportunidade em
    # potencial, e escondê-la até alguém decidir seria esconder justamente a
    # fila que precisa ser trabalhada.
    #
    # A leitura 1.5.0 continua contando pela coluna `oportunidades`, que era
    # como ela expressava a mesma ideia.
    # Na leitura 3.0.0 a oportunidade deixa de depender de `tipo_cliente`: o que
    # define é a IA ter encontrado comércio. E o DIVERGENTE entra sempre, sem
    # exigir tipo — um endereço com outro comércio ativo é, por definição,
    # comércio que o cadastro não descreve.
    "oportunidade": ("Oportunidade — comércio fora do cadastro",
                     "(f.acao_recomendada IN ('aprovar_especifico',"
                     " 'aprovar_divergente', 'revisar', 'aprovar')"
                     " AND (f.acao_recomendada = 'aprovar_divergente'"
                     "      OR f.tipo_cliente = 'comercial_empresarial_industrial')"
                     " AND NOT EXISTS (SELECT 1 FROM cadastro_cliente c"
                     "                  WHERE c.poi_id = f.poi_id AND c.e_comercial))"
                     " OR jsonb_array_length(f.oportunidades) > 0"),
    "convergente": ("Achado convergente",
                    "f.oportunidades @> '[{\"nivel_evidencia\":\"achado_convergente\"}]'"),
    "uso_diverge": ("Uso divergente",
                    "f.uso_observado IN ('comercial','servicos','industrial','misto_res_com')"),
    "unidades":    ("Unidades acima das economias",
                    "f.unidades_fisicas > f.economias_base"),
    "coletiva":    ("Medição coletiva", "f.medicao_coletiva"),
    "medicao":     ("Medição com problema",
                    "f.medicao_estado IN ('tampa_ausente','tampa_quebrada','obstruido','soterrado')"
                    " OR f.medicao_acesso IN ('inacessivel','obstruido_vegetacao',"
                    "'obstruido_veiculo','interno_requer_morador')"),
    "numero":      ("Número diverge", "f.numero_confere IS FALSE"),
    "conservacao": ("Conservação ruim",
                    "f.estado_conservacao IN ('ruim','em_ruina')"),
    "inapto":      ("Imagem inapta", "f.status = 'inapto'"),
    "fora_escopo": ("Não é imóvel", "f.status = 'fora_escopo'"),
}


# A LEITURA CORRENTE DE CADA POI, e só ela.
#
# `fachada_anotacao` é append-only: reler um ponto cria linha nova e preserva a
# anterior, porque o histórico de como a IA mudou de ideia é dado. O preço é que
# uma consulta ingênua conta o mesmo imóvel uma vez por releitura — o card diria
# 3 onde há 1, e a lista mostraria o mesmo endereço três vezes com vereditos
# diferentes, sem nada indicando qual vale.
_ULTIMA_LEITURA = """(SELECT DISTINCT ON (poi_id) * FROM fachada_anotacao
                       ORDER BY poi_id, criado_em DESC, id DESC) f"""


@app.get("/api/fachada/resumo")
def fachada_resumo():
    """Contagem de cada recorte, na área de trabalho em foco."""
    poligono = area_utils.carregar_area()
    cidade = area_utils.municipio_da_area(poligono)[0]
    w = "lower(p.cidade) = lower(%s)" if cidade else "TRUE"
    pc = [cidade] if cidade else []
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('fachada_anotacao')")
            if not cur.fetchone()[0]:
                return {"cards": [], "total": 0}
            sel = ", ".join(f"count(*) FILTER (WHERE {c})"
                            for _r, c in _RECORTES_FACHADA.values())
            cur.execute(f"""SELECT count(*), {sel} FROM {_ULTIMA_LEITURA}
                              JOIN pois p ON p.id = f.poi_id WHERE {w}""", pc)
            r = cur.fetchone()
        cards = [{"chave": k, "rotulo": v[0], "n": n}
                 for (k, v), n in zip(_RECORTES_FACHADA.items(), r[1:])]
        return {"total": r[0], "cards": cards, "cidade": cidade}
    finally:
        conn.close()


@app.get("/api/fachada/lista")
def fachada_lista(recorte: str = "aprovadas", limite: int = 400):
    """POIs de um recorte, com o mínimo para a lista do modal — o resto vem do
    detalhe quando o usuário clicar, para a lista abrir rápido com 400 itens."""
    if recorte not in _RECORTES_FACHADA:
        return JSONResponse({"erro": "recorte desconhecido"}, status_code=400)
    poligono = area_utils.carregar_area()
    cidade = area_utils.municipio_da_area(poligono)[0]
    w = "lower(p.cidade) = lower(%s)" if cidade else "TRUE"
    pc = ([cidade] if cidade else []) + [limite]
    cond = _RECORTES_FACHADA[recorte][1]
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT p.id, p.nome, p.endereco,
                       COALESCE(p.maps_lat, p.lat_origem), COALESCE(p.maps_lng, p.lng_origem),
                       f.status, f.uso_observado, f.tipologia, f.confianca,
                       jsonb_array_length(f.oportunidades),
                       f.numero_lido, f.numero_confere, f.estado_conservacao,
                       f.acao_recomendada, f.alvo_encontrado, f.tipo_cliente,
                       f.tipo_imovel, f.texto_do_letreiro, f.ressalva,
                       f.veredito_justificativa
                  FROM {_ULTIMA_LEITURA} JOIN pois p ON p.id = f.poi_id
                 WHERE {w} AND ({cond})
                 -- COALESCE porque a leitura 2.0.0 não grava `oportunidades`:
                 -- sem ele, todo item novo empataria em NULL e a ordenação
                 -- ficaria por acaso.
                 ORDER BY COALESCE(jsonb_array_length(f.oportunidades), 0) DESC,
                          f.confianca DESC NULLS LAST
                 LIMIT %s""", pc)
            cols = ["id", "nome", "endereco", "lat", "lng", "status", "uso",
                    "tipologia", "confianca", "n_oport", "numero_lido",
                    "numero_confere", "conservacao", "acao", "alvo_encontrado",
                    "tipo_cliente", "tipo_imovel", "letreiro", "ressalva",
                    "justificativa"]
            itens = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"recorte": recorte, "rotulo": _RECORTES_FACHADA[recorte][0],
                "itens": itens}
    finally:
        conn.close()


@app.get("/api/avaliar/estimativa")
def avaliar_estimativa(modelo: str = "gpt-4o-mini", refazer: bool = False):
    """Quanto há para ler e quanto custa — ANTES de gastar.

    A conta só faz sentido com o recorte na frente: são 16 mil fachadas na área
    de Canoas, e a diferença entre os dois modelos é de uma ordem de grandeza."""
    import avaliar_fachada as AF
    import leitura_fachada as LF
    poligono = area_utils.carregar_area()
    con = realtime_ingest.conectar()
    try:
        AF.esquema(con)
        # A elegibilidade é a do MOTOR QUE VAI RODAR, não a do antecessor. Com
        # `AF.carregar_alvos` o card prometia ler 20.696 e o job lia 20.692,
        # porque só o motor novo sabe quem já tem anotação da versão corrente.
        alvos = LF.elegiveis(con, poligono, 0, refazer, None, False)
        # Quantos da área NÃO entram, e por quê. O card mostrava só o número de
        # alvos; numa área de 254 POIs com 40 fachadas capturadas, "40 fachadas
        # a ler" lia-se como "a IA vai pular 214".
        pano = AF.panorama_da_area(poligono, con)
        com_vinculo = sum(1 for a in alvos if a["vinculo"])
        with con.cursor() as cur:
            cur.execute("""SELECT count(*), count(*) FILTER (WHERE status='aprovado')
                             FROM fachada_anotacao""")
            feitos, aprovados = cur.fetchone()
        # Com a conexão aberta: a estimativa prefere o histórico real das
        # últimas leituras à constante do código.
        est = LF.estimar(len(alvos), con)
    finally:
        con.close()
    return {**est, "com_vinculo": com_vinculo, "ja_avaliados": feitos,
            "ja_aprovados": aprovados, "area": pano,
            "cidade": area_utils.municipio_da_area(poligono)[0]}


def _cod_municipio_da_area(poly) -> str | None:
    """Código IBGE do município da área desenhada.

    As duas skills novas trabalham POR MUNICÍPIO, e o município não é digitado:
    sai de onde o usuário já definiu o recorte — o polígono no mapa. Pedir o
    código de novo seria uma chance a mais de errar, e errar aqui é rodar a
    cidade errada inteira."""
    cidade, uf = area_utils.municipio_da_area(poly)
    if not (cidade and uf):
        return None
    import unicodedata

    def n(s):
        return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                       if unicodedata.category(c) != "Mn").strip()

    # Comparação sem acento feita em PYTHON, e não por função do banco: a
    # `unaccent` do Postgres é extensão, e depender dela aqui trocaria uma
    # comparação de 500 nomes por uma dependência de instalação.
    ref = base_comum.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select cod_municipio, nome from ibge_malha where uf = %s", (uf,))
            alvo = n(cidade)
            for cod, nome in cur.fetchall():
                if n(nome) == alvo:
                    return str(cod)
    finally:
        ref.close()
    return None


def _empresa_do_pedido() -> str:
    """Nome da empresa de quem disparou o job. É o que os adaptadores exigem —
    e é do TOKEN, nunca do corpo do pedido."""
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is None or not u.id_empresa:
        return ""
    con = base_comum.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("select name from core.tb_empresas where id = %s::uuid", (u.id_empresa,))
            r = cur.fetchone()
            return r[0] if r else ""
    finally:
        con.close()


@app.post("/api/jobs")
def iniciar_job(body: dict):
    with _JOB_LOCK:
        if JOB.get("status") == "rodando" and JOB.get("proc") and JOB["proc"].poll() is None:
            return JSONResponse({"erro": "Já existe um job rodando. Pare-o antes de iniciar outro."},
                                status_code=409)

        modo = body.get("modo")
        op = body.get("opcoes") or {}

        # O polígono é obrigatório para quem trabalha SOBRE o mapa — planilha,
        # mineração, avaliação. As bases públicas não: elas vêm por município,
        # que é a unidade em que o governo publica. Exigir um retângulo
        # desenhado para baixar o Cadastur de Canoas seria pedir um dado que a
        # tarefa não usa, e o operador ficaria travado sem entender por quê.
        # O MOTOR REMOVIDO E RECUSADO ANTES DA AREA.
        #
        # A recusa vivia dentro do `elif modo == "mineracao"`, DEPOIS da
        # exigencia de poligono. Uma aba antiga aberta no navegador, que ainda
        # manda `motor=places`, ouvia "desenhe o poligono", desenhava, tentava
        # de novo — e so entao descobria que o motor nao existe mais. Duas
        # voltas para uma resposta que o servidor ja tinha na primeira.
        #
        # Pedido que a ferramenta nao atende mais nao depende do estado da
        # area: e invalido em qualquer estado.
        if str(op.get("motor") or "").strip() == "places":
            return JSONResponse(
                {"erro": "O motor 'places' (Google Places API, pago) foi removido "
                         "da ferramenta. A mineração usa a captura + OCR, que não "
                         "custa por chamada. Recarregue a página."},
                status_code=410)

        poly = area_utils.carregar_area()
        # `base_estadual` entra na mesma exceção do Cadastur, e pelo mesmo
        # motivo: ela trabalha por UF, que é a unidade em que as bases públicas
        # são publicadas. Exigir um retângulo desenhado para produzir a base do
        # Rio Grande do Sul seria pedir um dado que a tarefa não usa.
        if not poly and modo not in ("cadastur", "base_estadual", "base_cadastur"):
            return JSONResponse({"erro": "Desenhe o polígono da área antes de iniciar."}, status_code=400)

        if modo == "planilha":
            arquivo = UPLOADS / Path(str(body.get("arquivo") or "")).name
            if not arquivo.exists():
                return JSONResponse({"erro": "Planilha não encontrada. Importe o arquivo primeiro."},
                                    status_code=400)
            out_json = arquivo.parent / f"{arquivo.stem}_db.json"
            cmd = [PYTHON, "search_from_sheet.py", str(arquivo),
                   "--workers", str(int(op.get("workers", 10))),
                   "--area", area_utils.AREA_PADRAO]
            if op.get("recuperar", True):
                cmd.append("--recuperar")
            if op.get("retry_failed"):
                cmd.append("--retry-failed")
            if op.get("gemini_direto"):
                cmd.append("--gemini-direto")
            if op.get("no_proxy"):
                cmd.append("--no-proxy")
            if op.get("cidade"):
                cmd += ["--cidade", str(op["cidade"])]
            _novo_job("planilha", out_json, {"arquivo": arquivo.name})

        elif modo == "mineracao":
            sessao = re.sub(r"[^\w-]", "_", str(op.get("sessao") or "mineracao"))
            sessao = f"{sessao}_{datetime.now().strftime('%Y%m%d_%H%M')}"
            # A PLACES API SAIU DA FERRAMENTA (24/08/2026, decisão do dono do
            # produto). Ela cobrava por chamada e produzia o mesmo tipo de dado
            # que a captura + OCR produz de graça. `minerar_area.py` continua no
            # repositório como histórico, mas o painel não o alcança mais e a
            # rota RECUSA o modo — em vez de ignorar em silêncio um pedido que
            # ainda venha de uma aba antiga aberta no navegador.
            # A recusa do motor `places` subiu para antes da exigencia de
            # area (ver o comentario la em cima). Aqui nao ha mais o que
            # checar: duas recusas do mesmo pedido, em lugares diferentes,
            # divergem na primeira vez que alguem edita uma delas.
            # AS DUAS FONTES, NUM JOB SÓ (24/08/2026). `minerar_tudo.py` roda as
            # bases públicas e depois a captura + OCR. Elas deixaram de ser
            # alternativas porque enxergam coisas diferentes: a base pública não
            # sabe do comércio que abriu mês passado, e a captura não vê o que
            # não tem marcador no mapa.
            #
            # O watcher segue no arquivo da CAPTURA, e não é descuido: a etapa
            # das bases públicas ingere direto (`--aplicar`), como o Cadastur
            # faz. O watcher existe para o marcador cair no mapa ao vivo, e é a
            # captura que produz registro a registro.
            #
            # E é o arquivo NORMALIZADO, não o `search_resultado.json`: aquele
            # guarda o POI aninhado em `poi:{...}` e o ingester procura
            # `nome`/`maps_lat` no topo — leria tudo e gravaria nada. Quem achata
            # é o `db_export`, chamado de 10 em 10 s pelo minerar_captura.
            out_json = CAPTURAS / sessao / "crops" / f"{sessao}_db.json"
            cmd = [PYTHON, "minerar_tudo.py", "--area", area_utils.AREA_PADRAO,
                   "--sessao", sessao,
                   "--zoom", str(int(op.get("zoom", 19))),
                   "--workers", str(int(op.get("workers", 10))),
                   "--capture-workers", str(int(op.get("capture_workers", 10))),
                   "--empresa", _empresa_do_pedido()]
            if op.get("no_proxy"):
                cmd.append("--no-proxy")
            # A CAIXA EXISTE NO PAINEL, e a razão mudou de ideia no mesmo dia.
            #
            # Ela não estava lá de propósito: as duas fontes SÃO o processo, e
            # uma opção na tela viraria "desligar a metade barata" no dia em que
            # a rodada estivesse demorando.
            #
            # Só que a etapa das bases públicas EXIGE o dataset da UF, e
            # produzi-lo é trabalho de horas no i9. Sem saída, quem ainda não o
            # tem fica sem poder minerar — que é pior que o risco de alguém
            # desligar a metade barata. A caixa nasce desmarcada e o rótulo diz
            # o que se perde.
            if op.get("pular_bases"):
                cmd.append("--pular-bases")
            _novo_job("mineracao", out_json, {"sessao": sessao, "motor": "duas-fontes"})

        elif modo == "avaliar":
            # A leitura de fachada grava DIRETO em `fachada_anotacao` — não passa
            # registro para o watcher. O JSON aqui é só um destino inerte para o
            # watcher não ficar procurando arquivo que ninguém escreve.
            out_json = MINERACAO / "_avaliar_noop.json"
            # MOTOR NOVO: `leitura_fachada.py`, em quatro fases isoladas, contra
            # o vLLM da Spark. O antecessor fazia uma chamada por POI pedindo
            # tudo de uma vez e cobrava por token da OpenAI; por isso `teto_usd`
            # não é mais repassado — o modelo é local e o teto que importa agora
            # é o de TEMPO, que o painel estima antes de disparar.
            cmd = [PYTHON, "leitura_fachada.py", "--area", area_utils.AREA_PADRAO,
                   "--modelo", str(op.get("modelo") or "qwen3vl-moe")]
            if op.get("limit"):
                cmd += ["--limit", str(int(op["limit"]))]
            if op.get("refazer"):
                cmd.append("--refazer")
            # Quem ja e comercial no cadastro fica de fora por padrao: nao ha
            # reclassificacao a propor. A caixa existe para quem quiser o
            # dossie da carteira inteira.
            if op.get("incluir_ja_comerciais"):
                cmd.append("--incluir-ja-comerciais")
            _novo_job("avaliar", out_json, {})

        elif modo == "enriquecer_tudo":
            # CASCATA: cada POI pobre passa por Maps → Web → Street View até completar.
            # --sem-ingest: o próprio watcher (abaixo) grava no banco e transmite ao mapa;
            # sem isso, o subprocess ingeriria em paralelo (race no delete+recreate).
            out_json = MINERACAO / f"enrich_tudo_{datetime.now().strftime('%Y%m%d_%H%M')}_db.json"
            cmd = [PYTHON, "enriquecer_tudo.py", "--out", str(out_json),
                   "--workers", str(int(op.get("workers", 6))),
                   "--area", area_utils.AREA_PADRAO, "--sem-ingest"]
            if op.get("no_proxy"):
                cmd.append("--no-proxy")
            if op.get("pular_maps"):
                cmd.append("--pular-maps")
            # a fase local (Receita no banco) tem chave própria: ela leva
            # segundos e é grátis, a web leva horas e é paga — amarrar as duas
            # no mesmo interruptor obriga a pagar a cara para ter a barata
            if op.get("pular_cnpj_local"):
                cmd.append("--pular-cnpj-local")
            if op.get("pular_web"):
                cmd.append("--pular-web")
            if op.get("visivel"):
                cmd.append("--visivel")
            if op.get("sv_so_pobres"):
                cmd.append("--sv-so-pobres")
            if op.get("incluir_ja_comerciais"):
                cmd.append("--incluir-ja-comerciais")
            if op.get("pular_streetview"):
                cmd.append("--pular-streetview")
            _novo_job("enriquecer_tudo", out_json, {})

        elif modo == "cnpj_receita":
            # A skill `tratamento-cnpj` sobre a área de trabalho: cruza a Receita
            # com o CNEFE e devolve perfil comercial, evidência de existência
            # física e rota de tratamento. Grátis e sem chamada externa — é tudo
            # base pública que já mora no banco de referência.
            cod = _cod_municipio_da_area(poly)
            if not cod:
                return JSONResponse(
                    {"erro": "Não identifiquei o município da área. Desenhe dentro de um município."},
                    status_code=400)
            out_json = MINERACAO / f"cnpj_{cod}_{datetime.now().strftime('%Y%m%d_%H%M')}_db.json"
            cmd = [PYTHON, "tratamento_cnpj.py", "--municipio", cod,
                   "--empresa", _empresa_do_pedido(), "--aplicar"]
            if op.get("limite"):
                cmd += ["--limite", str(int(op["limite"]))]
            _novo_job("cnpj_receita", out_json, {"municipio": cod})

        elif modo == "extracao_estadual":
            # AQUISIÇÃO, não enriquecimento: traz POIs que as bases públicas já
            # conhecem (Overture + OSM + Foursquare), sem custo por ponto. Fica
            # ao lado da Captura + OCR pelo mesmo motivo — as duas ACHAM ponto;
            # o enriquecimento é o que se faz depois de ter o ponto.
            cod = _cod_municipio_da_area(poly)
            if not cod:
                return JSONResponse({"erro": "Não identifiquei o município da área."},
                                    status_code=400)
            saida = str(op.get("saida") or "").strip()
            if not saida or not Path(saida).exists():
                return JSONResponse(
                    {"erro": "Informe a pasta da extração estadual já produzida."},
                    status_code=400)
            out_json = MINERACAO / f"estadual_{cod}_{datetime.now().strftime('%Y%m%d_%H%M')}_db.json"
            cmd = [PYTHON, "extracao_estadual.py", "--saida", saida,
                   "--municipio", cod, "--empresa", _empresa_do_pedido(), "--aplicar"]
            if op.get("limite"):
                cmd += ["--limite", str(int(op["limite"]))]
            _novo_job("extracao_estadual", out_json, {"municipio": cod})

        elif modo == "base_cadastur":
            # BAIXAR (ou ATUALIZAR) o snapshot nacional do MTur.
            #
            # Mesma regra da base estadual, e ela nasceu de um desperdicio
            # medido: o passo 3 da mineracao chamava o Cadastur SEM
            # `--so-carregar`, e ele baixava os 26 recursos FEDERAIS a cada area
            # minerada. O recorte por municipio acontece depois do download,
            # entao o custo nao diminuia com a area — tres bairros da mesma
            # cidade no mesmo dia baixariam a base do pais tres vezes.
            #
            # Agora a mineracao so CONSULTA o que esta em disco, e o download
            # mora aqui: um clique, deliberado.
            #
            # Roda no NOTEBOOK, e nao no i9 como a base estadual: sao centenas
            # de MB, nao dezenas de GB, e o `cadastur.py` grava direto no banco
            # (que ja aponta para o i9 pelo .env).
            uf = (str(op.get("uf") or "").strip().upper()
                  or (area_utils.municipio_da_area(poly)[1] if poly else ""))
            if len(uf) != 2:
                return JSONResponse(
                    {"erro": "Informe a UF (duas letras) ou desenhe uma área dentro dela."},
                    status_code=400)
            out_json = MINERACAO / "_base_cadastur_noop.json"
            cmd = [PYTHON, "cadastur.py", "--uf", uf, "--gerar"]
            if op.get("atualizar"):
                # `--refresh` ignora o cache da skill. Deliberado, nunca padrão:
                # sem ele um snapshot já baixado é reaproveitado, que é o ponto.
                cmd.append("--refresh")
            _novo_job("base_cadastur", out_json, {"uf": uf})

        elif modo == "base_estadual":
            # PRODUZIR (ou ATUALIZAR) a base das bases públicas — no i9.
            #
            # Não é mineração: é a matéria-prima dela. A skill trabalha por UF
            # INTEIRA (383 mil POIs no RS), com DuckDB sobre o Overture no S3
            # mais o PBF do OpenStreetMap. São horas, e por isso roda no i9 —
            # ao lado do OSRM e do Photon, que já vivem lá pelo mesmo motivo.
            #
            # E fica lá. O banco também está no i9: trazer dezenas de GB para o
            # notebook só para reenviar o recorte de um município de volta seria
            # atravessar a rede duas vezes à toa.
            #
            # A UF vem do POLÍGONO quando há um, e do corpo quando o operador
            # quer produzir um estado onde ainda não desenhou nada — que é o
            # caso normal ao montar a base do Brasil.
            uf = (str(op.get("uf") or "").strip().upper()
                  or (area_utils.municipio_da_area(poly)[1] if poly else ""))
            if len(uf) != 2:
                return JSONResponse(
                    {"erro": "Informe a UF (duas letras) ou desenhe uma área dentro dela."},
                    status_code=400)
            out_json = MINERACAO / "_base_estadual_noop.json"   # watcher ocioso
            cmd = ["bash", str(BASE / "scripts" / "i9" / "dataset_estadual.sh"), "--remoto"]
            # ATUALIZAR é deliberado, nunca padrão: ele repina a identidade da
            # fonte (`--source-mode latest`) e troca o mundo debaixo do
            # resultado. Sem a flag, uma base já pronta é reaproveitada.
            if op.get("atualizar"):
                cmd.append("--atualizar")
            cmd.append(uf)
            _novo_job("base_estadual", out_json, {"uf": uf,
                                                  "atualizar": bool(op.get("atualizar"))})

        elif modo == "minerar_web":
            # resíduo → Yahoo + pré-filtro + LLM barato + Receita Federal
            arquivo = Path(str(body.get("arquivo") or "")).name
            json_alvo = None
            if arquivo:
                cand = UPLOADS / f"{Path(arquivo).stem}_db.json"
                if cand.exists():
                    json_alvo = cand
            if json_alvo is None:  # fallback: JSON de coleta mais recente em uploads/
                jsons = sorted(UPLOADS.glob("*_db.json"), key=lambda p: p.stat().st_mtime)
                json_alvo = jsons[-1] if jsons else None
            if json_alvo is None:
                return JSONResponse({"erro": "Nenhum JSON de coleta em uploads/. "
                                             "Rode a planilha primeiro."}, status_code=400)
            cmd = [PYTHON, "minerar_web.py", "--json", str(json_alvo),
                   "--workers", str(int(op.get("workers", 5))),
                   "--area", area_utils.AREA_PADRAO]
            if op.get("cidade"):
                cmd += ["--cidade", str(op["cidade"])]
            out_json = json_alvo
            _novo_job("minerar_web", out_json, {"arquivo": json_alvo.name})

        elif modo == "enriquecer_maps":
            # descobertos/recuperados_ia rasos → abre cada um no Maps (painel completo)
            out_json = MINERACAO / f"enrich_maps_{datetime.now().strftime('%Y%m%d_%H%M')}_db.json"
            cmd = [PYTHON, "enriquecer_maps.py", "--out", str(out_json),
                   "--workers", str(int(op.get("workers", 6))),
                   "--area", area_utils.AREA_PADRAO, "--sem-ingest"]
            if op.get("no_proxy"):
                cmd.append("--no-proxy")
            _novo_job("enriquecer_maps", out_json, {})

        elif modo == "streetview":
            # prints do Street View — atualiza o banco direto (sem JSON/watcher)
            out_json = MINERACAO / "_streetview_noop.json"  # watcher fica ocioso
            cmd = [PYTHON, "streetview_capture.py",
                   "--workers", str(int(op.get("workers", 3)))]
            if op.get("refazer"):
                cmd.append("--refazer")
            _novo_job("streetview", out_json, {})

        elif modo == "baixar_imagens":
            # download das imagens (bytes+datas) para o banco — grava direto
            out_json = MINERACAO / "_baixar_noop.json"  # watcher fica ocioso
            cmd = [PYTHON, "baixar_imagens.py",
                   "--workers", str(int(op.get("workers", 8)))]
            if op.get("pular_fotos"):
                cmd.append("--pular-fotos")
            if op.get("pular_streetview"):
                cmd.append("--pular-streetview")
            _novo_job("baixar_imagens", out_json, {})


        elif modo == "cadastur":
            # Cadastur/MTur: cadastro obrigatório de prestador de serviço
            # turístico. É a única fonte do sistema que traz CAPACIDADE
            # declarada — UH e leitos —, e leito é consumo de água por
            # pessoa/dia.
            #
            # Três etapas num comando: baixa o snapshot, carrega o recorte do
            # município e gera POI do que não cruzou com nada. O `--gerar`
            # exige o cruzamento rodado, e o próprio script recusa se não
            # estiver — não é este lugar que decide isso.
            municipio = str(op.get("municipio") or "").strip()
            uf = str(op.get("uf") or "").strip().upper()
            if not municipio or len(uf) != 2:
                return JSONResponse(
                    {"erro": "Informe o município e a UF — o Cadastur é "
                             "publicado por município, não por área desenhada."},
                    status_code=400)
            out_json = MINERACAO / "_cadastur_noop.json"   # watcher fica ocioso
            cmd = [PYTHON, "cadastur.py", "--uf", uf, "--municipio", municipio]
            if op.get("datasets"):
                cmd += ["--datasets", str(op["datasets"])]
            if op.get("so_carregar"):
                cmd.append("--so-carregar")
            if op.get("gerar", True):
                cmd.append("--gerar")
            if op.get("encadear"):
                # Cruza de novo (agora com os POIs novos no banco) e enriquece
                # SÓ eles — Maps, web, CNPJ e Street View. É a esteira dos
                # demais; nada é pulado por o ponto ter vindo de base pública.
                cmd.append("--encadear")
            if op.get("pular_streetview"):
                cmd.append("--pular-streetview")
            if op.get("sem_pessoa_fisica"):
                cmd.append("--sem-pessoa-fisica")
            _novo_job("cadastur", out_json,
                      {"municipio": municipio, "uf": uf})

        else:
            return JSONResponse({"erro": f"Modo inválido: {modo}"}, status_code=400)

        try:
            _iniciar_subprocess(cmd, out_json, poly)
        except Exception as e:
            JOB["status"] = "erro"
            return JSONResponse({"erro": f"Falha ao iniciar: {e}"}, status_code=500)

        manager.broadcast({"tipo": "job", "dados": job_status()})
        return job_status()


@app.get("/api/jobs/atual")
def job_atual():
    return job_status()


@app.post("/api/jobs/parar")
def parar_job():
    proc = JOB.get("proc")
    if proc and proc.poll() is None:
        JOB["status"] = "parado"
        # O CONTEINER PRIMEIRO, DEPOIS O CLIENTE.
        #
        # Quando o job roda no minerador, `proc` e o cliente do `docker run`.
        # Termina-lo derruba a conexao e nao o processo la dentro: a tela diria
        # "parado" com a mineracao ainda rodando e queimando IP.
        nome = JOB.get("container")
        if nome:
            try:
                subprocess.run(["docker", "rm", "-f", nome], timeout=30,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass
        manager.broadcast({"tipo": "job", "dados": job_status()})
        return {"ok": True, "status": "parado"}
    return {"ok": True, "status": JOB.get("status", "ocioso")}


# ──────────────────────────────────────────────────────────────────────────
# Frontend estático
# ──────────────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    """A TELA PRINCIPAL É A NOVA desde 28/08/2026 — decisão do dono do produto.

    A anterior continua servida em `/antigo`, inteira e funcionando: ela cobre
    coisas que a nova ainda não faz (importar planilha, cadastro do cliente,
    bancada, fila de aprovação), e tirá-la do ar seria trocar uma tela por meia.

    O `?v=` versiona css/js pelo mtime: a URL muda a cada edição, então o
    navegador busca a versão nova mesmo tendo cópia velha em cache — sem
    depender de hard reload. Sem isso, a troca da tela principal chegaria para
    metade da equipe com o JavaScript antigo.
    """
    return _pagina("painel.html", ("painel.js",))


@app.get("/antigo")
def index_antigo():
    """A tela anterior, que segue completa enquanto a nova não a cobre."""
    return _pagina("index.html", ("style.css", "app.js"))


def _pagina(nome: str, estaticos: tuple) -> Response:
    html = (FRONT / nome).read_text(encoding="utf-8")
    for arq in estaticos:
        alvo = FRONT / arq
        if not alvo.exists():
            continue
        v = int(alvo.stat().st_mtime)
        html = html.replace(f"/static/{arq}", f"/static/{arq}?v={v}")
    return Response(html, media_type="text/html",
                    headers={"Cache-Control": "no-cache"})


class _FrontSemCache(StaticFiles):
    """O front é editado com o servidor no ar; sem isto o navegador segura o
    css/js antigos por horas (foi o que sumiu com o seletor de mapa). O
    "no-cache" não desliga o cache: manda revalidar, e o 304 é barato."""

    def file_response(self, *a, **kw):
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


# ── O portão ─────────────────────────────────────────────────────────────────
#
# Fecha `/api/*` por padrão. O que é público entra na lista abaixo, e o padrão é
# NEGAR — o inverso, abrir por padrão e proteger o que alguém lembrar, foi o que
# deixou 29 rotas servindo dado sem token depois que a tela de login já existia.
# A tela bloqueava a vista; a rota respondia a quem chamasse direto.
#
# Além de autenticar, o portão publica o usuário em `USUARIO_DA_REQUISICAO`. É
# isso que faz `realtime_ingest.conectar()` — chamado por 10 rotas antigas —
# devolver a conexão com RLS em vez da conexão do worker. Nenhuma daquelas rotas
# precisou ser tocada.
@app.get("/api/saude")
def saude():
    """Está de pé E serve para alguma coisa?

    CONFERE O BANCO, e não só o processo. Um healthcheck que responde 200 porque
    o uvicorn está vivo não distingue "de pé" de "de pé e inútil": com o Postgres
    fora do ar, toda rota devolve 503 e o Docker continua marcando o contêiner
    como saudável — então nada reinicia e nada avisa.

    NÃO DIZ O QUE ESTÁ ERRADO. É rota pública, e quem pergunta antes de entrar
    não precisa saber se o que caiu foi o banco, o pooler ou a credencial. O
    motivo vai para o log, que só quem tem o servidor lê.

    `select 1` e nada mais: ela roda a cada 30 segundos, e uma consulta que
    encoste em tabela de negócio somaria trabalho ao banco o dia inteiro para
    responder uma pergunta que `select 1` já responde.
    """
    try:
        con = realtime_ingest.conectar()
        try:
            with con.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        finally:
            con.close()
        return {"ok": True}
    except Exception as erro:                                   # noqa: BLE001
        print(f"[saude] o banco nao respondeu: {type(erro).__name__}: {erro}",
              flush=True)
        return JSONResponse({"ok": False}, status_code=503)


PUBLICAS = {
    "/", "/api/login", "/api/renovar", "/api/saude",
    "/favicon.ico", "/docs", "/openapi.json", "/redoc",
}

# Ação só para quem executa processo. Leitura não entra aqui: `user` lê.
SO_ADMIN = (
    "/api/jobs", "/api/upload", "/api/limpar-fora", "/api/cadastro/",
    "/api/area/municipio",
)

# ROTAS QUE O NAVEGADOR PEDE POR <img src>, E POR ISSO ACEITAM O TOKEN NA QUERY.
#
# `<img>` não manda cabeçalho `Authorization` — não há como. Desde que o portão
# entrou, toda fachada no modal do mapa e toda foto de perfil vinham 401 e o
# navegador desenhava o ícone de imagem quebrada. Ninguém viu porque `onerror`
# removia a figura em silêncio, e a foto de perfil cai para as iniciais.
#
# A saída é a mesma já usada pelo WebSocket, que tem a mesma limitação: token na
# query. Fica restrito a ESTAS rotas, e só quando não há cabeçalho — token em
# URL aparece em log de servidor e em histórico, e não é para virar o caminho
# padrão de autenticação do resto da API.
TOKEN_NA_QUERY = ("/api/sv/", "/api/eu/foto", "/api/dossie/", "/api/modelos/")


@app.middleware("http")
async def portao(request: Request, call_next):
    caminho = request.url.path
    if not caminho.startswith("/api/") or caminho in PUBLICAS:
        return await call_next(request)

    cabecalho = request.headers.get("authorization", "")
    if not cabecalho and any(caminho.startswith(p) for p in TOKEN_NA_QUERY):
        tok = request.query_params.get("token", "")
        if tok:
            cabecalho = f"Bearer {tok}"
    try:
        u = _auth.usuario_atual(cabecalho)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)

    if request.method != "GET" and any(caminho.startswith(p) for p in SO_ADMIN):
        if not u.pode("admin"):
            return JSONResponse({"detail": "exige nível admin"}, status_code=403)

    ficha = _auth.USUARIO_DA_REQUISICAO.set(u)
    try:
        return await call_next(request)
    finally:
        # `reset` e não `set(None)`: sem ele o contexto do worker do uvicorn
        # guarda o último usuário e a requisição seguinte, se algo falhar antes
        # do portão, herdaria o crachá alheio.
        _auth.USUARIO_DA_REQUISICAO.reset(ficha)


# ── Empresas clientes (CRUD) ─────────────────────────────────────────────────
#
# A conexão vem de `auth.conectar_como(u)`: o papel muda conforme o nível e a
# transação declara `request.jwt.claim.sub`. Quem filtra é a policy, não o `WHERE`.


class EmpresaEntrada(BaseModel):
    nome: str = Field(min_length=2, max_length=120)
    documento: str | None = Field(default=None, max_length=20)
    ativo: bool = True


@app.get("/api/empresas")
def empresas_listar(u: _auth.Usuario = Depends(_auth.exige("admin"))):
    """Root vê todas; o admin vê a própria — e isso é a policy, não um IF.

    Exige `admin` porque abaixo disso ninguém precisa: `supervisor` e `user` já
    recebem o nome da própria empresa no `/api/eu`. Deixar a rota aberta a
    qualquer sessão não vazava nada — a policy limitava à empresa do usuário —
    mas dava a quem não administra uma porta para um recurso de administração,
    e a vistoria de hoje flagrou justamente isso.
    """
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select id, nome, documento, ativo, criado_em
                             from core.tb_empresas order by nome""")
            return {"empresas": [
                {"id": str(i), "nome": n, "documento": d, "ativo": a,
                 "criado_em": c.isoformat() if c else None}
                for i, n, d, a, c in cur.fetchall()]}
    finally:
        con.close()


@app.post("/api/empresas", status_code=201)
def empresas_criar(e: EmpresaEntrada, u: _auth.Usuario = Depends(_auth.exige("root"))):
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""insert into core.tb_empresas (nome, documento, ativo)
                           values (%s,%s,%s) returning id""",
                        (e.nome.strip(), e.documento, e.ativo))
            novo = cur.fetchone()[0]
        con.commit()
        return {"id": str(novo), "nome": e.nome.strip()}
    finally:
        con.close()


@app.patch("/api/empresas/{empresa_id}")
def empresas_editar(empresa_id: str, e: EmpresaEntrada,
                    u: _auth.Usuario = Depends(_auth.exige("root"))):
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""update core.tb_empresas set nome=%s, documento=%s, ativo=%s
                            where id=%s returning id""",
                        (e.nome.strip(), e.documento, e.ativo, empresa_id))
            if not cur.fetchone():
                raise HTTPException(404, "empresa não encontrada")
        con.commit()
        return {"id": empresa_id, "nome": e.nome.strip()}
    finally:
        con.close()


@app.delete("/api/empresas/{empresa_id}")
def empresas_desativar(empresa_id: str, u: _auth.Usuario = Depends(_auth.exige("root"))):
    """DESATIVA, não apaga.

    Apagar empresa com dado embaixo é impossível — a FK recusa — e forçar em
    cascata destruiria o acervo dela. Desativar tira o acesso e preserva o
    histórico, que é o que auditoria e contestação exigem."""
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("update core.tb_empresas set ativo=false where id=%s returning nome",
                        (empresa_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "empresa não encontrada")
        con.commit()
        return {"id": empresa_id, "nome": r[0], "ativo": False}
    finally:
        con.close()


# ── Dossiê ───────────────────────────────────────────────────────────────────

def _dossie_html(poi_id: int, u: "_auth.Usuario") -> str:
    import dossie
    con = _auth.conectar_como(u)
    try:
        dados = dossie.coletar(poi_id, con)
    finally:
        con.close()
    if not dados:
        # 404 e não 403: para quem não alcança o POI pela RLS, ele não existe.
        # Responder 403 confirmaria que o registro existe em OUTRA empresa.
        raise HTTPException(404, "POI não encontrado")
    return dossie.montar_html(dados)


@app.get("/api/dossie/{poi_id}")
def dossie_html(poi_id: int, u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Versão de tela. Qualquer nível lê o dossiê do que já alcança."""
    return Response(content=_dossie_html(poi_id, u), media_type="text/html; charset=utf-8")


@app.get("/api/dossie/{poi_id}/pdf")
async def dossie_pdf(poi_id: int, u: _auth.Usuario = Depends(_auth.exige("supervisor"))):
    """O documento físico.

    Caminho com `/pdf` e não com sufixo `.pdf`: o FastAPI casa as rotas na
    ordem em que foram declaradas, e `/api/dossie/{poi_id}` engolia
    `159029.pdf` tentando lê-lo como inteiro — 422, nunca chegando aqui.

    Renderizado pelo Chromium do Playwright, que já está instalado para a
    captura — não vale trazer uma segunda engine de PDF para o projeto.

    A página é carregada por `set_content`, não por URL: assim o Chromium não
    precisa de sessão nem alcança a API, e o PDF sai idêntico ao que o usuário
    autenticado veria. Carregar por URL exigiria repassar o token para dentro do
    navegador — credencial atravessando mais uma fronteira sem necessidade.
    """
    from playwright.async_api import async_playwright
    html_doc = _dossie_html(poi_id, u)
    try:
        async with async_playwright() as pw:
            navegador = await pw.chromium.launch(headless=True)
            pagina = await navegador.new_page()
            # As imagens são data: URI, então não há rede a esperar — mas o
            # `networkidle` protege contra uma fonte externa futura.
            await pagina.set_content(html_doc, wait_until="networkidle")
            pdf = await pagina.pdf(format="A4", print_background=True,
                                   margin={"top": "16mm", "bottom": "16mm",
                                           "left": "14mm", "right": "14mm"})
            await navegador.close()
    except Exception as e:
        raise HTTPException(503, f"não consegui gerar o PDF: {type(e).__name__}")

    nome = f"dossie-poi-{poi_id}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ── Fila de aprovação ────────────────────────────────────────────────────────
#
# O admin distribui; o supervisor decide. As duas metades de uma coisa só: a
# atribuição É a fila, e é ela que também define o que o supervisor enxerga —
# a policy de `pois` faz um EXISTS contra esta tabela.

class AtribuirEntrada(BaseModel):
    poi_ids: list[int] = Field(min_length=1, max_length=2000)
    supervisor_ids: list[str] = Field(min_length=1, max_length=50)


class DecisaoEntrada(BaseModel):
    status: str                          # aprovado | reprovado | devolvido | campo
    motivo_generico: str | None = None
    motivo_escrito: str | None = None
    observacao: str | None = None
    # Visita de campo nao e um rotulo, e uma PAUTA: o que ir verificar no local.
    # Mandar alguem a campo sem dizer o que conferir e mandar de novo depois.
    pauta: list[str] | None = None
    # O FATO que sustenta cada item da pauta. Sem isso a pauta vira lista de
    # desejos, e quem vai a campo nao sabe o que motivou cada linha.
    pauta_porque: dict | None = None
    # O que o supervisor preencheu. Chaves livres, mas `uso` e `atividade` são
    # exigidos para aprovar — pelo CHECK do banco, não só por este arquivo.
    revisao: dict | None = None


# Os campos que a tela de revisão preenche. Ficam aqui, e não espalhados no
# JavaScript, porque é esta lista que decide o que entra no `jsonb`: chave que
# não está aqui é descartada, então uma tela adulterada não injeta campo
# arbitrário no documento que vira prova para o cliente.
CAMPOS_REVISAO = ("uso", "atividade", "nome_confirmado", "cnpj", "telefone",
                  "economias_comerciais", "endereco_confere", "endereco_corrigido",
                  "visita_necessaria", "observacao_tecnica")
USOS_REVISAO = ("comercial", "misto", "residencial", "indefinido")


@app.get("/api/fila/candidatos")
def fila_candidatos(u: _auth.Usuario = Depends(_auth.exige("admin"))):
    """Tudo que existe DENTRO da área de trabalho, com o que serve de filtro.

    A distribuição antiga pegava os N primeiros POIs de `/api/pois` — que não
    recorta área nenhuma. O admin lia "serão enviados os POIs da área atual" e
    recebia os N primeiros da base inteira, ordenados por id: distribuição
    aleatória com aparência de critério.

    Aqui a área é o recorte de verdade, e vêm juntos os atributos pelos quais
    faz sentido separar trabalho: o que o cruzamento com o cadastro disse, o que
    a leitura de fachada viu, e que evidência existe para decidir. As facetas
    saem do próprio conjunto — lista fixa de categorias mentiria sobre o que há
    nesta área.
    """
    poligono = area_utils.carregar_area()
    if not poligono:
        raise HTTPException(422, "nenhuma área de trabalho definida")
    lat0, lat1, lng0, lng1 = area_utils.bbox(poligono)

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # A caixa vai no SQL (usa índice); o polígono é testado em Python
            # sobre o que a caixa devolveu. PostGIS mora em `extensions`, fora do
            # `search_path` desta conexão, e trazer a extensão para o caminho só
            # por causa de um ST_Contains custa mais do que o laço.
            cur.execute("""
                select p.id, p.nome, p.categoria, p.endereco, p.cidade,
                       coalesce(p.maps_lat, p.lat_origem),
                       coalesce(p.maps_lng, p.lng_origem),
                       nullif(btrim(coalesce(p.cnpj, '')), '') is not null,
                       p.cnpj_conf,
                       exists (select 1 from images_urls i where i.poi_id = p.id),
                       (p.streetview_path is not null
                        and p.streetview_path not in ('', 'NA')),
                       f.tipo_edificacao, f.estado_conservacao, f.uso_observado,
                       c.cruz_flag, c.e_comercial, c.num_ligacao,
                       exists (select 1 from atribuicao a where a.poi_id = p.id),
                       -- A PRECISAO vai junto de cada ponto. Ela decide a cor
                       -- do marcador e alimenta o filtro; buscar depois, ponto
                       -- a ponto, seria uma consulta por marcador na tela.
                       coalesce(p.coord_precisao, 'desconhecida'),
                       p.coord_fonte, p.coord_incerteza_m
                  from pois p
                  left join fachada_anotacao f on f.poi_id = p.id
                  left join cadastro_cliente c on c.poi_id = p.id
                 where p.match_valido is not false
                   and coalesce(p.maps_lat, p.lat_origem) between %s and %s
                   and coalesce(p.maps_lng, p.lng_origem) between %s and %s
                 order by p.id""", (lat0, lat1, lng0, lng1))
            linhas = cur.fetchall()
    finally:
        con.close()

    cols = ("id nome categoria endereco cidade lat lng tem_cnpj cnpj_conf tem_foto "
            "tem_sv edificacao conservacao uso_fachada cruz_flag e_comercial "
            "num_ligacao na_fila coord_precisao coord_fonte coord_incerteza_m").split()
    itens = []
    for r in linhas:
        d = dict(zip(cols, r))
        if not area_utils.ponto_no_poligono(d["lat"], d["lng"], poligono):
            continue
        # Sem linha de cadastro casada, o POI não é "não comercial": é ACHADO
        # NOVO, que no processo é outra trilha (acrescer, não reclassificar).
        # Deixar o campo nulo obrigaria a tela a inventar o rótulo.
        d["cruz_flag"] = d["cruz_flag"] or "fora_do_cadastro"
        # BANDEIRA PRÓPRIA, e não deduzida do `cruz_flag` na tela: quem já é
        # comercial no cadastro não é ganho — mandar para a fila gasta o tempo
        # do supervisor confirmando o que o cliente já cobra. O admin ainda pode
        # mandar, mas tem de ver que está mandando.
        d["ja_comercial"] = bool(d.pop("e_comercial", None))
        itens.append(d)

    def faceta(chave):
        cont = {}
        for i in itens:
            v = i.get(chave) or "—"
            cont[v] = cont.get(v, 0) + 1
        return [{"v": k, "n": n} for k, n in
                sorted(cont.items(), key=lambda kv: -kv[1])]

    # O filtro roda no navegador — é instantâneo e faz "marcar os filtrados"
    # significar exatamente o que está na tela. O teto existe para a área que
    # cobre a cidade inteira: 22 mil linhas por requisição é o tipo de payload
    # que só aparece na demonstração para o cliente. Truncar em silêncio seria
    # pior; por isso o `truncado` volta e a tela avisa.
    TETO = 8000
    resp = {"total": len(itens), "truncado": len(itens) > TETO,
            "na_fila": sum(1 for i in itens if i["na_fila"]),
            "ja_comerciais": sum(1 for i in itens if i["ja_comercial"]),
            "facetas": {"categoria": faceta("categoria"),
                        "cruz_flag": faceta("cruz_flag"),
                        "edificacao": faceta("edificacao"),
                        "conservacao": faceta("conservacao")}}
    resp["itens"] = itens[:TETO]
    return resp


@app.post("/api/fila/atribuir", status_code=201)
def fila_atribuir(e: AtribuirEntrada, u: _auth.Usuario = Depends(_auth.exige("admin"))):
    """Manda vários POIs para vários supervisores de uma vez.

    `on conflict do nothing` porque reenviar o mesmo lote é o caso NORMAL: o
    admin filtra uma área, manda, filtra de novo com o filtro um pouco diferente
    e manda outra vez. Sem isso, o segundo envio estouraria por chave duplicada
    e ele perderia o lote inteiro por causa de dois POIs repetidos.
    """
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # Só supervisores DESTA empresa — a policy de `usuarios` já barra o
            # resto, e o `nivel` evita mandar ponto para quem não decide nada.
            cur.execute("""select us.id from core.tb_users us
                             join core.tb_niveis_user nu
                                  on nu.id = us.id_nivel_user
                            where us.id = any(%s::uuid[]) and us.ativo
                              and nu.codigo in ('supervisor','administrator')""",
                        ([str(s) for s in e.supervisor_ids],))
            validos = [r[0] for r in cur.fetchall()]
            if not validos:
                raise HTTPException(422, "nenhum supervisor válido nesta empresa")

            # E só POIs que ESTE usuário alcança: se ele mandar id de outra
            # empresa, a policy não devolve a linha e o id não entra no lote.
            cur.execute("select id from pois where id = any(%s)", (e.poi_ids,))
            pois_ok = [r[0] for r in cur.fetchall()]
            if not pois_ok:
                raise HTTPException(422, "nenhum POI válido no lote")

            pares = [(p, s, u.id) for s in validos for p in pois_ok]
            psycopg2.extras.execute_values(
                cur,
                """insert into atribuicao (poi_id, supervisor_id, atribuido_por)
                   values %s on conflict (poi_id, supervisor_id) do nothing""",
                pares)
            criadas = cur.rowcount
        con.commit()
        return {"supervisores": len(validos), "pois": len(pois_ok),
                "atribuicoes_novas": criadas,
                "ja_existiam": len(pares) - criadas}
    finally:
        con.close()


@app.get("/api/fila")
def fila_listar(status: str = "pendente", limite: int = 200,
                u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Supervisor vê a SUA fila; admin vê a da empresa. Quem separa é a policy."""
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # A NOTA DA IA VEM JUNTO. O supervisor abre a fila com dezenas de
            # itens iguais e precisa de uma ordem de ataque; sem ela, decide na
            # ordem em que o admin distribuiu, que não é ordem de nada.
            cur.execute("""select a.id, a.poi_id, p.nome, p.categoria, p.endereco,
                                  a.status, a.atribuido_em, us.nome,
                                  f.veredito_comercial, f.nota_comercial
                             from atribuicao a
                             join pois p     on p.id  = a.poi_id
                             left join core.tb_users us on us.id = a.supervisor_id
                             left join fachada_anotacao f on f.poi_id = a.poi_id
                            where (%s = 'todos' or a.status::text = %s)
                            order by a.atribuido_em desc
                            limit %s""", (status, status, min(limite, 1000)))
            return {"itens": [
                {"id": i, "poi_id": pid, "nome": n, "categoria": c, "endereco": en,
                 "status": st, "atribuido_em": at.isoformat() if at else None,
                 "supervisor": sup, "veredito_ia": vc, "nota_ia": nc}
                for i, pid, n, c, en, st, at, sup, vc, nc in cur.fetchall()]}
    finally:
        con.close()


# ──────────────────────────────────────────────────────────────────────────
# API — fila do COMÉRCIO DIVERGENTE
#
# Fila separada porque a decisão é de outra natureza. Na fila normal o
# supervisor CONFIRMA um imóvel que o cadastro do cliente já conhece; aqui ele
# decide se um estabelecimento que ninguém conhecia entra na base. As perguntas
# são outras, os motivos de recusa são outros, e misturar as duas numa tela só
# faria as perguntas de uma aparecerem no trabalho da outra.
# ──────────────────────────────────────────────────────────────────────────
MOTIVOS_DIVERGENTE = {
    "nao_e_comercio": "Não é comércio — a IA leu errado",
    "letreiro_do_vizinho": "O letreiro é do imóvel vizinho",
    "ja_cadastrado": "Já existe no cadastro com outro nome",
    "encerrado": "Comércio encerrado",
    "endereco_incerto": "Não dá para dizer a que endereço pertence",
    "outro": "Outro",
}


class DecisaoDivergente(BaseModel):
    status: str                                  # aceito | recusado | devolvido
    motivo_generico: str | None = None
    motivo_escrito: str | None = None
    observacao: str | None = None


@app.get("/api/divergentes")
def divergentes_listar(status: str = "pendente", limite: int = 200,
                       u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """O que a IA achou de comércio que o cadastro não descreve.

    Traz LADO A LADO o nome do cadastro e o nome lido na parede: a decisão é
    exatamente comparar os dois, e obrigar o supervisor a abrir a ficha só para
    ver o que ele já poderia ter visto na lista é trabalho jogado fora.
    """
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select d.id, d.poi_id, p.nome, p.endereco, p.categoria,
                                  d.nome_lido, d.atividade, d.status,
                                  d.atribuido_em, us.nome, f.confianca
                             from atribuicao_divergente d
                             join pois p on p.id = d.poi_id
                             left join core.tb_users us on us.id = d.supervisor_id
                             left join fachada_anotacao f on f.id = d.anotacao_id
                            where (%s = 'todos' or d.status = %s)
                            order by d.atribuido_em desc
                            limit %s""", (status, status, min(limite, 1000)))
            return {"itens": [
                {"id": i, "poi_id": pid, "nome_cadastro": n, "endereco": e,
                 "categoria": c, "nome_lido": nl, "atividade": at, "status": st,
                 "atribuido_em": qd.isoformat() if qd else None,
                 "supervisor": sup, "confianca": conf}
                for i, pid, n, e, c, nl, at, st, qd, sup, conf in cur.fetchall()],
                "motivos": MOTIVOS_DIVERGENTE}
    finally:
        con.close()


@app.get("/api/divergentes/{item_id}/ficha")
def divergente_ficha(item_id: int, u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """A MESMA evidência da fila normal — imagens, leitura, cadastro.

    Reaproveita `dossie.coletar` pelo mesmo motivo de lá: duas coletas
    divergiriam, e aí a tela mostraria uma coisa e o PDF provaria outra.
    """
    import dossie
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select d.id, d.poi_id, d.nome_lido, d.atividade,
                                  d.status, d.motivo_generico, d.motivo_escrito,
                                  d.observacao, d.atribuido_em
                             from atribuicao_divergente d where d.id = %s""",
                        (item_id,))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "item não encontrado na sua fila")
            item = dict(zip("id poi_id nome_lido atividade status motivo_generico "
                            "motivo_escrito observacao atribuido_em".split(), r))
            item["atribuido_em"] = (item["atribuido_em"].isoformat()
                                    if item["atribuido_em"] else None)
        dados = dossie.coletar(item["poi_id"], con)
    finally:
        con.close()
    return {"item": item, "poi": dados, "motivos": MOTIVOS_DIVERGENTE}


@app.post("/api/divergentes/{item_id}/decidir")
def divergente_decidir(item_id: int, d: DecisaoDivergente,
                       u: _auth.Usuario = Depends(_auth.exige("supervisor"))):
    """Aceitar, recusar ou devolver o achado divergente.

    A exigência de motivo na recusa é CHECK no banco, como na fila normal. A
    validação aqui existe só para devolver 422 com texto claro — regra de
    negócio que mora só na aplicação some no primeiro cliente de API que
    ninguém previu.
    """
    if d.status not in ("aceito", "recusado", "devolvido"):
        raise HTTPException(422, "status deve ser aceito, recusado ou devolvido")
    if d.status == "recusado":
        if not d.motivo_generico or d.motivo_generico not in MOTIVOS_DIVERGENTE:
            raise HTTPException(422, "recusar exige um motivo da lista")
        if len((d.motivo_escrito or "").strip()) < 10:
            raise HTTPException(422, "recusar exige o motivo escrito (10+ caracteres)")
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""update atribuicao_divergente
                              set status = %s, motivo_generico = %s,
                                  motivo_escrito = %s, observacao = %s,
                                  supervisor_id = %s, decidido_em = now()
                            where id = %s returning poi_id""",
                        (d.status, d.motivo_generico, d.motivo_escrito,
                         d.observacao, u.id, item_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "item não encontrado na sua fila")
        con.commit()
        return {"ok": True, "poi_id": r[0], "status": d.status}
    finally:
        con.close()


@app.get("/api/fila/{item_id}/ficha")
def fila_ficha(item_id: int, u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Tudo o que se sabe do ponto, para decidir sem sair da tela.

    O supervisor decidia olhando nome e endereço — duas linhas — e o dossiê era
    um PDF que abria em outra aba. Decidir sobre reclassificação de tarifa com
    isso é chutar; a evidência (fachada, fotos, o que a IA leu, o que o cadastro
    diz) precisa estar diante de quem assina.

    Reaproveita `dossie.coletar`: é a MESMA coleta que vira o documento
    comprobatório. Duas coletas diferentes acabariam divergindo, e aí a tela
    mostraria uma coisa e o PDF provaria outra.
    """
    import dossie
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select a.id, a.poi_id, a.status, a.revisao,
                                  a.motivo_generico, a.motivo_escrito, a.observacao,
                                  a.atribuido_em, us.nome
                             from atribuicao a
                             left join core.tb_users us on us.id = a.supervisor_id
                            where a.id = %s""", (item_id,))
            r = cur.fetchone()
            if not r:
                # 404 e não 403: para quem não alcança o item, ele não existe.
                raise HTTPException(404, "item não encontrado na sua fila")
            item = {"id": r[0], "poi_id": r[1], "status": r[2], "revisao": r[3] or {},
                    "motivo_generico": r[4], "motivo_escrito": r[5], "observacao": r[6],
                    "atribuido_em": r[7].isoformat() if r[7] else None,
                    "supervisor": r[8]}

            # O que o CADASTRO DO CLIENTE diz daquele imóvel. É a outra metade da
            # comparação: sem ela o supervisor não sabe do que está divergindo.
            cur.execute("""select num_ligacao, categoria, e_comercial, cruz_flag,
                                  cruz_dist_m, numero, endereco,
                                  coalesce(economias_res,0) + coalesce(economias_com,0)
                                    + coalesce(economias_ind,0) + coalesce(economias_pub,0)
                                    + coalesce(economias_out,0),
                                  coalesce(economias_com,0)
                             from cadastro_cliente where poi_id = %s limit 1""",
                        (item["poi_id"],))
            c = cur.fetchone()
            if c:
                item["cadastro"] = dict(zip(
                    "num_ligacao categoria e_comercial cruz_flag cruz_dist_m numero "
                    "endereco economias economias_com".split(), c))

        dados = dossie.coletar(item["poi_id"], con)
        # AS ABAS SAEM DO CATALOGO, nao de campo fixo no JavaScript. Campo novo
        # que a extracao aprender entra em `campo_catalogo` e aparece sozinho.
        #
        # A procedencia (IBGE, Google, Receita) e filtrada AQUI, no servidor:
        # o RBAC so a libera para o `root`, e filtrar no navegador seria fingir
        # — o dado teria chegado e qualquer console o leria.
        import ficha_abas
        abas = ficha_abas.montar(con, dados, e_root=(u.nivel == "root"))
    finally:
        con.close()
    return {"item": item, "poi": dados, "abas": abas,
            "pauta_possivel": list(ficha_abas.PAUTA_VALIDA),
            "usos": list(USOS_REVISAO)}


@app.post("/api/fila/{item_id}/decidir")
def fila_decidir(item_id: int, d: DecisaoEntrada,
                 u: _auth.Usuario = Depends(_auth.exige("supervisor"))):
    """Aprovar, reprovar ou devolver.

    As exigências de motivo NÃO são validadas aqui e sim no banco, por CHECK. A
    validação daqui existe só para devolver 422 com texto claro; se ela sumir, o
    banco continua recusando. Regra de negócio que mora só na aplicação some no
    primeiro cliente de API que ninguém previu.
    """
    if d.status not in ("aprovado", "reprovado", "devolvido", "campo"):
        raise HTTPException(
            422, "status deve ser aprovado, reprovado, devolvido ou campo")
    # A exigencia REAL e o CHECK `campo_exige_pauta` no banco; esta validacao
    # existe para o erro chegar legivel, e nao como 500 de constraint.
    if d.status == "campo":
        if not (d.pauta or []):
            raise HTTPException(
                422, "visita de campo exige pauta: diga o que verificar no local")
        import ficha_abas
        fora = [x for x in d.pauta if x not in ficha_abas.PAUTA_VALIDA]
        if fora:
            raise HTTPException(422, f"pauta desconhecida: {', '.join(fora)}")
    if d.status == "reprovado" and not (d.motivo_generico and (d.motivo_escrito or "").strip()):
        raise HTTPException(422, "reprovar exige motivo genérico E motivo escrito")
    if d.status == "devolvido" and not (d.observacao or "").strip():
        raise HTTPException(422, "devolver exige dizer o que impede a aprovação")

    # Só as chaves conhecidas entram, e o `uso` só se for um dos declarados. O
    # `jsonb` aceitaria qualquer coisa, e o que se grava aqui vira texto do
    # documento que a concessionária usa para mudar tarifa.
    rev = {k: v for k, v in (d.revisao or {}).items()
           if k in CAMPOS_REVISAO and v not in (None, "")}
    if rev.get("uso") and rev["uso"] not in USOS_REVISAO:
        raise HTTPException(422, f"uso deve ser um de {', '.join(USOS_REVISAO)}")
    if d.status == "aprovado" and not (
            rev.get("uso") and len(str(rev.get("atividade", "")).strip()) >= 3):
        raise HTTPException(422, "aprovar exige o uso observado e a atividade do local")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # PRIORIDADE ALTA com tres ou mais itens na pauta: caso com muita
            # coisa a verificar trava a fila se esperar a vez normal.
            _pauta = d.pauta or None
            _prio = "alta" if len(d.pauta or []) >= 3 else "normal"
            cur.execute("""update atribuicao
                              set status=%s::decisao_fila,
                                  motivo_generico=%s::motivo_reprova,
                                  motivo_escrito=%s, observacao=%s, decidido_em=now(),
                                  pauta=%s::radar_comercial.pauta_campo[],
                                  pauta_porque=%s::jsonb,
                                  prioridade=%s::radar_comercial.prioridade_campo,
                                  -- concatena para não perder o que já havia
                                  -- sido preenchido numa devolução anterior
                                  revisao = coalesce(revisao, '{}'::jsonb) || %s::jsonb
                            where id=%s
                        returning poi_id, status""",
                        (d.status, d.motivo_generico, d.motivo_escrito, d.observacao,
                         _pauta,
                         json.dumps(d.pauta_porque or {}, ensure_ascii=False),
                         _prio,
                         json.dumps(rev, ensure_ascii=False), item_id))
            r = cur.fetchone()
            if not r:
                # 404 e não 403: para quem não é dono do item, ele não existe.
                raise HTTPException(404, "item não encontrado na sua fila")
        con.commit()
        return {"id": item_id, "poi_id": r[0], "status": r[1]}
    finally:
        con.close()


# ── Usuários ─────────────────────────────────────────────────────────────────
#
# As travas de nível estão aqui, e não só no frontend, porque botão escondido
# não é permissão: quem chamar a rota direto passa. Três regras:
#
#  1. Ninguém cria acima do próprio nível — senão `admin` vira `root` em dois
#     passos, criando um root e entrando com ele.
#  2. `root` só é criado por `root`. É o papel que atravessa todas as empresas.
#  3. `admin` só mexe na PRÓPRIA empresa. O `id_empresa` vem do crachá dele,
#     nunca do corpo do pedido — vindo do corpo, ele escolheria a empresa alheia.

class UsuarioEntrada(BaseModel):
    email: str = Field(max_length=200)
    nome: str = Field(min_length=2, max_length=120)
    nivel: str = Field(default="user")
    cargo: str | None = Field(default=None, max_length=80)
    telefone: str | None = Field(default=None, max_length=32)
    id_empresa: str | None = None       # só o root usa; admin herda o próprio


class UsuarioEdicao(BaseModel):
    nome: str | None = Field(default=None, max_length=120)
    nivel: str | None = None
    cargo: str | None = Field(default=None, max_length=80)
    telefone: str | None = Field(default=None, max_length=32)
    ativo: bool | None = None


def _validar_nivel(quem: _auth.Usuario, alvo: str):
    if alvo not in _auth.NIVEIS:
        raise HTTPException(422, f"nível inválido: {alvo}")
    if alvo == "root" and quem.nivel != "root":
        raise HTTPException(403, "apenas root cria outro root")
    if _auth.NIVEIS.index(alvo) > _auth.NIVEIS.index(quem.nivel):
        raise HTTPException(403, "não é possível criar usuário acima do seu nível")


@app.get("/api/usuarios")
def usuarios_listar(u: _auth.Usuario = Depends(_auth.exige("admin"))):
    """Root vê todos; admin vê os da própria empresa — pela policy, não por IF."""
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select us.id, us.name, us.email,
                                  coalesce(nu.codigo,'user'), null::text,
                                  us.phone, us.ativo, t.name, us.criado_em
                             from core.tb_users us
                             left join core.tb_niveis_user nu
                                    on nu.id = us.id_nivel_user
                             left join core.tb_empresas t on t.id = us.id_empresa
                            order by t.name nulls first, nu.hierarquia, us.name""")
            return {"usuarios": [
                {"id": str(i), "nome": n, "email": e, "nivel": nv, "cargo": c,
                 "telefone": tel, "ativo": a, "empresa": emp,
                 "criado_em": cr.isoformat() if cr else None}
                for i, n, e, nv, c, tel, a, emp, cr in cur.fetchall()]}
    finally:
        con.close()


@app.post("/api/usuarios", status_code=201)
def usuarios_criar(novo: UsuarioEntrada, u: _auth.Usuario = Depends(_auth.exige("admin"))):
    _validar_nivel(u, novo.nivel)

    # A empresa do novo usuário: root escolhe, admin herda a sua. Aceitar
    # `id_empresa` do corpo para o admin seria deixá-lo cadastrar gente dentro
    # do cliente vizinho.
    if u.nivel == "root":
        destino = novo.id_empresa
        if novo.nivel != "root" and not destino:
            raise HTTPException(422, "informe a empresa para usuário que não é root")
        if novo.nivel == "root":
            destino = None
    else:
        destino = u.id_empresa

    email = novo.email.strip().lower()
    senha = secrets.token_urlsafe(12)
    import auth as _a
    req = urllib.request.Request(
        f"{_a._GW}/auth/v1/admin/users", method="POST",
        data=json.dumps({"email": email, "password": senha,
                         "email_confirm": True}).encode(),
        headers={"apikey": _a._ANON, "Authorization": f"Bearer {_a._ANON}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            uid = json.loads(r.read())["id"]
    except urllib.error.HTTPError as e:
        detalhe = e.read()[:200].decode("utf-8", "ignore")
        if "already" in detalhe.lower() or e.code == 422:
            raise HTTPException(409, "já existe usuário com esse e-mail")
        raise HTTPException(502, "não consegui criar a credencial")
    except Exception:
        raise HTTPException(503, "serviço de autenticação indisponível")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # CRIAR USUARIO E DA API DE IDENTIDADE (7710), NAO DAQUI.
            #
            # `core.tb_users` pertence a `supabase_admin`, e criar alguem
            # exige tambem criar a conta no GoTrue — que SQL nao faz. A rota
            # fica de pe para nao sumir do catalogo, e recusa dizendo onde ir.
            raise HTTPException(
                501, "criar usuário é da API de identidade (7710): "
                     "POST /usuarios. Esta rota ficou do desenho antigo, "
                     "quando a identidade morava no banco do radar.")
            cur.execute("""insert into core.tb_users
                             (id, id_empresa, id_nivel_user, name, email, phone)
                           values (%s,%s,%s,%s,%s,%s)""",
                        (uid, destino, novo.nivel, novo.nome.strip(), email,
                         novo.cargo, novo.telefone))
        con.commit()
    finally:
        con.close()
    # A senha volta UMA vez, para quem criou repassar. Não fica guardada em
    # lugar nenhum nosso — recuperação é pelo fluxo do Auth, não pelo nosso banco.
    return {"id": uid, "email": email, "nivel": novo.nivel, "senha_inicial": senha}


@app.patch("/api/usuarios/{usuario_id}")
def usuarios_editar(usuario_id: str, alt: UsuarioEdicao,
                    u: _auth.Usuario = Depends(_auth.exige("admin"))):
    if alt.nivel:
        _validar_nivel(u, alt.nivel)
    if usuario_id == u.id and alt.ativo is False:
        raise HTTPException(422, "você não pode desativar a si mesmo")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            # A policy já impede alcançar usuário de outra empresa; este SELECT
            # existe para responder 404 em vez de "0 linhas afetadas".
            cur.execute("""select coalesce(nu.codigo, 'user')
                             from core.tb_users us
                             left join core.tb_niveis_user nu
                                    on nu.id = us.id_nivel_user
                            where us.id = %s""", (usuario_id,))
            atual = cur.fetchone()
            if not atual:
                raise HTTPException(404, "usuário não encontrado")
            if atual[0] == "root" and u.nivel != "root":
                raise HTTPException(403, "apenas root altera um root")
            # `cargo` NAO EXISTE em `core.tb_users`, e o parametro e engolido de
            # proposito: recusar a alteracao inteira por causa de um campo
            # que a identidade nova nao tem seria pior que perde-lo.
            cur.execute("""update core.tb_users
                              set name = coalesce(%s, name),
                                  id_nivel_user = coalesce(
                                      (select id from core.tb_niveis_user
                                        where codigo = %s), id_nivel_user),
                                  phone = coalesce(%s, phone),
                                  ativo = coalesce(%s, ativo),
                                  atualizado_em = now()
                            where id = %s
                        returning name,
                                  (select codigo from core.tb_niveis_user
                                    where id = id_nivel_user), ativo""",
                        (alt.nome, alt.nivel, alt.telefone, alt.ativo,
                         usuario_id))
            r = cur.fetchone()
        con.commit()
        return {"id": usuario_id, "nome": r[0], "nivel": r[1], "ativo": r[2]}
    finally:
        con.close()


@app.delete("/api/usuarios/{usuario_id}")
def usuarios_desativar(usuario_id: str, u: _auth.Usuario = Depends(_auth.exige("admin"))):
    """Desativa; não apaga. Histórico de quem aprovou o quê tem de sobreviver
    à saída da pessoa — é disso que auditoria é feita."""
    if usuario_id == u.id:
        raise HTTPException(422, "você não pode desativar a si mesmo")
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select coalesce(nu.codigo, 'user')
                             from core.tb_users us
                             left join core.tb_niveis_user nu
                                    on nu.id = us.id_nivel_user
                            where us.id = %s""", (usuario_id,))
            atual = cur.fetchone()
            if not atual:
                raise HTTPException(404, "usuário não encontrado")
            if atual[0] == "root" and u.nivel != "root":
                raise HTTPException(403, "apenas root desativa um root")
            cur.execute("update core.tb_users set ativo=false, atualizado_em=now() where id=%s",
                        (usuario_id,))
        con.commit()
        return {"id": usuario_id, "ativo": False}
    finally:
        con.close()


@app.get("/api/eu")
def quem_sou_eu(u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """O crachá que o frontend usa para decidir o que mostrar.

    `fontes_visiveis` é o requisito comercial: para o cliente, a fonte do dado
    somos nós. A procedência continua GRAVADA — o que muda é a exibição."""
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""select us.name, us.email, us.phone, null::text,
                                  us.img_perfil, t.name, null::text
                             from core.tb_users us
                             left join core.tb_empresas t on t.id = us.id_empresa
                            where us.id = %s""", (u.id,))
            r = cur.fetchone() or (None,) * 7
    finally:
        con.close()
    return {"id": u.id, "nivel": u.nivel, "id_empresa": u.id_empresa,
            "nome": r[0], "email": r[1], "telefone": r[2], "cargo": r[3],
            "tem_foto": bool(r[4]), "empresa": r[5],
            # A marca do TENANT, no terceiro espaco do cabecalho (ADR 0005).
            # Nulo mostra so o nome em texto: ausencia de logo nao pode virar
            # espaco quebrado.
            "empresa_logo": r[6],
            "fontes_visiveis": u.nivel == "root"}


class LoginEntrada(BaseModel):
    email: str = Field(max_length=200)
    senha: str = Field(max_length=200)


@app.post("/api/login")
def login(e: LoginEntrada):
    """Troca e-mail e senha por um token.

    O navegador NÃO fala com o GoTrue direto, e isso não é preciosismo: a chave
    `anon` e o endereço do serviço de autenticação ficariam no código da página.
    Passando por aqui, o frontend conhece apenas `/api/login` — coerente com a
    regra de que, para o cliente, a origem do que ele vê somos nós.

    A mensagem de erro é a mesma para e-mail inexistente e senha errada, de
    propósito: distinguir os dois transforma a tela de login em um verificador
    de quem tem conta no sistema.
    """
    import auth as _a
    corpo = json.dumps({"email": e.email.strip().lower(), "password": e.senha}).encode()
    req = urllib.request.Request(
        f"{_a._GW}/auth/v1/token?grant_type=password", data=corpo, method="POST",
        headers={"apikey": _a._ANON, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError:
        raise HTTPException(401, "e-mail ou senha inválidos")
    except Exception:
        raise HTTPException(503, "serviço de autenticação indisponível")
    return {"access_token": tok.get("access_token"),
            "refresh_token": tok.get("refresh_token"),
            "expira_em": tok.get("expires_in")}


class RenovarEntrada(BaseModel):
    refresh_token: str


@app.post("/api/renovar")
def renovar(e: RenovarEntrada):
    """Troca o refresh_token por um access_token novo.

    O token de acesso do GoTrue dura uma hora. Sem esta rota o painel guardava
    só ele e descartava o refresh que o login já devolvia — de modo que, ao
    completar a hora, QUALQUER chamada tomava 401 e a tela caía no login.

    Isso não é incômodo de usabilidade: acontecia no meio de uma carga de 16 mil
    fachadas, e quem estava acompanhando perdia a tela do processo que continuava
    rodando no servidor, sem barra, sem log e sem saber se ainda estava vivo.

    Rota PÚBLICA porque a credencial é o próprio refresh_token — exigir o
    access_token aqui seria exigir justamente o que expirou.
    """
    import auth as _a
    corpo = json.dumps({"refresh_token": e.refresh_token}).encode()
    req = urllib.request.Request(
        f"{_a._GW}/auth/v1/token?grant_type=refresh_token", data=corpo, method="POST",
        headers={"apikey": _a._ANON, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError:
        raise HTTPException(401, "sessão não pôde ser renovada")
    except Exception:
        raise HTTPException(503, "serviço de autenticação indisponível")
    return {"access_token": tok.get("access_token"),
            "refresh_token": tok.get("refresh_token"),
            "expira_em": tok.get("expires_in")}


class PerfilEntrada(BaseModel):
    nome: str | None = Field(default=None, max_length=120)
    telefone: str | None = Field(default=None, max_length=32)
    cargo: str | None = Field(default=None, max_length=80)


@app.patch("/api/eu")
def editar_perfil(p: PerfilEntrada, u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Cada um edita o PRÓPRIO cadastro.

    Repare no `where us.id = %s` com o id vindo do TOKEN: não há parâmetro de
    rota dizendo qual usuário editar. Se houvesse, alguém trocaria o id e
    editaria o perfil do vizinho — e nível, empresa e e-mail não estão aqui de
    propósito: quem muda nível é o root, e e-mail é credencial, muda no Auth.
    """
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""update core.tb_users
                              set name  = coalesce(%s, name),
                                  phone = coalesce(%s, phone),
                                  atualizado_em = now()
                            where id = %s
                        returning name, phone, null::text""",
                        (p.nome, p.telefone, p.cargo, u.id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, "usuário não encontrado")
        con.commit()
        return {"nome": r[0], "telefone": r[1], "cargo": r[2]}
    finally:
        con.close()


@app.post("/api/eu/foto")
async def enviar_foto(arquivo: UploadFile = File(...),
                      u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Foto de perfil: bytes no Storage, caminho no banco.

    Limite de 4 MB porque foto de perfil é exibida em 64 px — aceitar o JPEG de
    12 MB que a câmera do celular produz gastaria banda e Storage para nada."""
    if (arquivo.content_type or "") not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(415, "envie JPEG, PNG ou WebP")
    dados = await arquivo.read()
    if len(dados) > 4 * 1024 * 1024:
        raise HTTPException(413, "imagem acima de 4 MB")

    import imagens
    caminho = f"perfil/{u.id}.jpg"
    if not imagens.enviar(caminho, dados, arquivo.content_type):
        raise HTTPException(502, "falha ao gravar no Storage")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("update core.tb_users set img_perfil=%s, atualizado_em=now() where id=%s",
                        (caminho, u.id))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "bytes": len(dados)}


@app.get("/api/eu/foto")
def ler_foto(u: _auth.Usuario = Depends(_auth.usuario_atual)):
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("select img_perfil from core.tb_users where id=%s", (u.id,))
            r = cur.fetchone()
    finally:
        con.close()
    if not r or not r[0]:
        raise HTTPException(404, "sem foto")
    import imagens
    b = imagens.baixar(r[0])
    if not b:
        raise HTTPException(404, "foto não encontrada no Storage")
    return Response(content=b, media_type="image/jpeg",
                    headers={"Cache-Control": "no-cache"})


# ── Logo da empresa cliente ──────────────────────────────────────────────────
#
# Mesma mecanica da foto de perfil — bytes no Storage, caminho no banco — mas o
# dono e o TENANT, nao o usuario: todos da mesma empresa veem a mesma marca.


@app.post("/api/empresa/logo")
async def enviar_logo(arquivo: UploadFile = File(...),
                      u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """Quem troca a marca da empresa e `admin` ou `root`.

    Supervisor e user nao trocam: a marca identifica a empresa inteira no
    cabecalho de todo mundo, e nao e preferencia individual.
    """
    if not u.pode("admin"):
        raise HTTPException(403, "so admin ou root trocam a marca da empresa")
    if (arquivo.content_type or "") not in ("image/jpeg", "image/png",
                                            "image/webp", "image/svg+xml"):
        raise HTTPException(415, "envie JPEG, PNG, WebP ou SVG")
    dados = await arquivo.read()
    if len(dados) > 2 * 1024 * 1024:
        # 2 MB e generoso para uma marca exibida em 22 px de altura. O limite
        # existe para o Storage nao virar deposito de PSD exportado errado.
        raise HTTPException(413, "imagem acima de 2 MB")

    import imagens
    caminho = f"marca/{u.id_empresa}"
    if not imagens.enviar(caminho, dados, arquivo.content_type):
        raise HTTPException(502, "falha ao gravar no Storage")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("update core.tb_empresas set logo_path=%s where id=%s",
                        (caminho, u.id_empresa))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "bytes": len(dados)}


@app.get("/api/empresa/logo")
def ler_logo(u: _auth.Usuario = Depends(_auth.usuario_atual)):
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("select logo_path from core.tb_empresas where id=%s",
                        (u.id_empresa,))
            r = cur.fetchone()
    finally:
        con.close()
    if not r or not r[0]:
        raise HTTPException(404, "empresa sem marca")
    import imagens
    b = imagens.baixar(r[0])
    if not b:
        raise HTTPException(404, "marca nao encontrada no Storage")
    # `image/png` cobre PNG e serve JPEG/WebP no navegador; SVG precisa do tipo
    # certo ou o navegador baixa em vez de desenhar.
    tipo = "image/svg+xml" if b[:5] in (b"<?xml", b"<svg ") else "image/png"
    return Response(content=b, media_type=tipo,
                    headers={"Cache-Control": "no-cache"})


# ── A bancada de validacao ───────────────────────────────────────────────────
#
# A tela vem de `assets/modelo_frontend/tela_radar_comercial.zip`: 149 KB de
# CSS, 319 KB de JS e 327 KB de HTML, ja especificada, com lateral de fila,
# regua de probabilidade, tabela de cruzamento, mapa por camadas e barra de
# decisao. Reimplementar aquilo a mao sairia pior.
#
# Ela foi desenhada para CARREGAR UM DATASET. Entao o trabalho e produzir o
# nosso naquele contrato — `bancada_dataset.montar` — e servir os dois.


@app.get("/api/bancada/dataset")
def bancada_dataset(limite: int = 40, poi: int | None = None,
                    u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """A fila do usuario no formato que a bancada consome.

    `root` e `admin` veem a fila inteira do tenant; supervisor ve so o que lhe
    foi atribuido — a mesma regra da rota `/api/fila`, aqui de novo porque quem
    le esta funcao precisa saber, e nao descobrir num `join` distante.
    """
    import bancada_dataset as BD

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            so_meus = "" if u.pode("admin") else "and a.supervisor_id = %s"
            args = (limite,) if u.pode("admin") else (u.id, limite)
            cur.execute(f"""select a.id, a.poi_id, a.status::text, a.observacao,
                                   a.motivo_escrito, a.decidido_em,
                                   a.prioridade::text, a.pauta::text[], us.nome
                              from atribuicao a
                              left join core.tb_users us on us.id = a.supervisor_id
                             where true {so_meus}
                             order by (a.status = 'pendente') desc,
                                      a.prioridade desc, a.id
                             limit %s""", args)
            colunas = ["id", "poi_id", "status", "observacao", "motivo_escrito",
                       "decidido_em", "prioridade", "pauta", "supervisor"]
            itens = [dict(zip(colunas, r)) for r in cur.fetchall()]
            for it in itens:
                if it["decidido_em"]:
                    it["decidido_em"] = it["decidido_em"].isoformat()

            # O PONTO PEDIDO ENTRA MESMO SEM ESTAR NA FILA.
            #
            # Clicar num ponto do mapa abre a bancada nele — e a maioria dos
            # 27 mil POIs nunca foi atribuida a ninguem. Exigir atribuicao
            # antes de olhar transformaria a bancada em tela de supervisor, e
            # ela e a tela de quem examina um ponto.
            if poi is not None and not any(i["poi_id"] == poi for i in itens):
                cur.execute("""select id from pois where id = %s""", (poi,))
                if cur.fetchone():
                    itens.insert(0, {"poi_id": poi, "status": "pendente",
                                     "prioridade": "normal"})

        return BD.montar(con, itens, e_root=(u.nivel == "root"),
                         base=f"fila de {u.nome or u.nivel}")
    finally:
        con.close()


@app.get("/api/cadastur/resumo")
def cadastur_resumo(municipio: str = "", uf: str = ""):
    """O que o Cadastur trouxe para este município.

    Alimenta o card do painel. Devolve TRÊS coisas que respondem perguntas
    diferentes:

      · o que virou ponto no mapa, e de onde saiu a coordenada de cada um;
      · o que NÃO virou, com o motivo — sem isso, "28 carregados, 16 pontos"
        parece perda, e é decisão;
      · quantos prestadores PESSOA FÍSICA existem ali. Este é um NÚMERO e só:
        guia de turismo não vira POI e não tem linha guardada, mas saber que a
        cidade tem 10 deles é informação de mercado legítima.
    """
    con = realtime_ingest.conectar()
    try:
        with con.cursor() as cur:
            filtro = ("where (%(m)s = '' or lower(municipio) = lower(%(m)s)) "
                      "and (%(u)s = '' or upper(uf) = upper(%(u)s))")
            par = {"m": municipio or "", "u": uf or ""}

            cur.execute(f"""select count(*),
                                   count(*) filter (where poi_id is not null),
                                   count(*) filter (where sem_poi_motivo is not null
                                                      and poi_id is null),
                                   count(*) filter (where saiu_em is not null),
                                   sum(leitos), sum(uh),
                                   max(ref_periodo)
                              from resources_root.cadastur_prestador {filtro}""",
                        par)
            (total, com_poi, sem_poi, sairam, leitos, uh, periodo) = cur.fetchone()

            cur.execute(f"""select sem_poi_motivo, count(*)
                              from resources_root.cadastur_prestador {filtro}
                             and sem_poi_motivo is not null and poi_id is null
                             group by 1 order by 2 desc""", par)
            motivos = {m: n for m, n in cur.fetchall()}

            cur.execute(f"""select atividade_turistica, count(*)
                              from resources_root.cadastur_prestador {filtro}
                             and atividade_turistica is not null
                             group by 1 order by 2 desc limit 12""", par)
            atividades = [{"nome": a, "n": n} for a, n in cur.fetchall()]

            cur.execute(f"""select atividade, uf, municipio, ref_periodo,
                                   quantidade
                              from radar_comercial.cadastur_total_pf {filtro}
                             order by ref_periodo desc, quantidade desc""", par)
            pf = [{"atividade": a, "uf": u, "municipio": m,
                   "periodo": (per.isoformat() if per else None), "n": q}
                  for a, u, m, per, q in cur.fetchall()]
    finally:
        con.close()

    return {
        "total": total or 0,
        "com_poi": com_poi or 0,
        "sem_poi": sem_poi or 0,
        "sairam": sairam or 0,
        "leitos": int(leitos) if leitos else 0,
        "uh": int(uh) if uh else 0,
        "periodo": periodo.isoformat() if periodo else None,
        "motivos": motivos,
        "atividades": atividades,
        "pessoa_fisica": pf,
    }


@app.get("/painel")
def painel_novo():
    """A tela principal do desenho de handoff, em rota PRÓPRIA.

    Ela não substitui a `/` ainda, e isso é deliberado: as duas convivem
    enquanto o dono do produto compara, e o que já funciona não para de
    funcionar por causa de uma tela nova. Trocar a principal é decisão dele.
    """
    alvo = FRONT / "painel.html"
    if not alvo.exists():
        raise HTTPException(404, "painel.html não encontrada em frontend/")
    return FileResponse(str(alvo), media_type="text/html")


@app.get("/bancada")
def bancada_pagina():
    """A tela. O HTML e servido como esta no modelo; os dados vem da rota acima."""
    alvo = FRONT / "bancada.html"
    if not alvo.exists():
        raise HTTPException(404, "bancada.html nao instalada — rode "
                                 "`python instalar_bancada.py`")
    return FileResponse(str(alvo), media_type="text/html")


app.mount("/static", _FrontSemCache(directory=str(FRONT)), name="static")

# A pasta streetview/ deixou de existir: a fachada é servida por
# /api/sv/{poi_id}/facade, direto de `streetview_imgs`. Eram 2,6 GB de arquivo
# duplicando o que já estava no banco.


# ══════════════════ as fontes de um POI, e como desfazer a fusão ═════════════
#
# A fusão é feita por máquina com evidência incompleta e vai errar — medido no
# RS, 66,2% das fusões suspeitas uniram estabelecimentos distintos. Estas rotas
# são a saída: ver de que fontes o ponto é feito, e tirar a que não pertence.


class DesvincularEntrada(BaseModel):
    fonte: str
    id_fonte: str


@app.get("/api/poi/{poi_id}/fontes")
def poi_fontes(poi_id: int, u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """As abas da ficha: uma por fonte, com o que cada uma afirma.

    Vem também o que já foi desvinculado, porque a ficha precisa poder mostrar
    "isto já foi separado daqui" — sem isso, quem abrir amanhã não entende por
    que o ponto tem uma fonte a menos que o relatório da extração diz.
    """
    import vinculo
    con = _auth.conectar_como(u)
    try:
        abas = vinculo.fontes_do_poi(con, poi_id, incluir_desvinculados=True)
    finally:
        con.close()
    for a in abas:
        for k in ("desvinculado_em",):
            if a.get(k):
                a[k] = a[k].isoformat()
    return {"poi_id": poi_id,
            "fontes": [a for a in abas if a["estado"] == "vinculado"],
            "desvinculadas": [a for a in abas if a["estado"] == "desvinculado"]}


@app.post("/api/poi/{poi_id}/desvincular")
def poi_desvincular(poi_id: int, e: DesvincularEntrada,
                    u: _auth.Usuario = Depends(_auth.exige("supervisor"))):
    """Tira uma fonte do POI. O que sai vira POI novo; o que fica continua junto.

    Exige `supervisor` porque é decisão sobre a IDENTIDADE de um ponto — a
    mesma alçada de quem aprova ou reprova um achado. Um operador que só
    consulta não deveria poder partir um POI em dois.
    """
    import vinculo
    con = _auth.conectar_como(u)
    try:
        r = vinculo.desvincular(con, poi_id, e.fonte, e.id_fonte,
                                por=(u.email or u.nome or "?"))
        con.commit()
        # A DESVINCULACAO JA ESTA COMMITADA antes de a captura comecar.
        #
        # A ordem importa: a separacao e a decisao da pessoa e esta correta;
        # a captura e o que se acrescenta. Se ela estivesse dentro da
        # transacao, um erro de rede desfaria uma decisao humana — e o
        # operador clicaria de novo achando que nao tinha funcionado.
        with con.cursor() as cur:
            cur.execute('select nome, maps_lat, maps_lng, cidade from pois where id = %s', (r["poi_novo"],))
            novo_poi = cur.fetchone()
        if novo_poi:
            r["captura"] = vinculo.capturar_novo(
                r["poi_novo"], novo_poi[0], novo_poi[1], novo_poi[2],
                novo_poi[3] or "")
    except vinculo.NaoPodeDesvincular as erro:
        con.rollback()
        # 409 e não 400: o pedido está bem formado, o ESTADO é que não permite.
        raise HTTPException(409, str(erro))
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return r


# ══════════════════ a fila de logradouro que precisa de gente ════════════════


class RevisaoLogradouro(BaseModel):
    logradouro_corrigido: str = ""
    status: str = "corrigido"
    nota: str = ""


@app.get("/api/logradouro/pendencias")
def logradouro_pendencias(scope_id: str = "", limite: int = 200,
                          u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """O que a normalização não resolveu e passou para uma pessoa.

    `REVISAR` significa que houve PERDA DE TEXTO — um segmento entre parênteses,
    um bairro depois do hífen, uma poda. `HUMANO`, que sobrou pouco ou nada de
    via. Os dois são fila de gente por desenho, não por falha.
    """
    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""
                select fonte, record_id, scope_id, logradouro_original,
                       logradouro_marcado, tier, risco, numero_canonico,
                       complemento_organizado, revisao_status,
                       logradouro_corrigido, revisado_por, revisado_em
                  from logradouro_ajustado
                 where tier in ('REVISAR', 'HUMANO')
                   and (%s = '' or scope_id = %s)
                 order by (revisao_status = 'pendente') desc,
                          case tier when 'HUMANO' then 0 else 1 end,
                          record_id
                 limit %s""", (scope_id, scope_id, min(int(limite), 1000)))
            cols = [d[0] for d in cur.description]
            itens = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("""
                select revisao_status, count(*) from logradouro_ajustado
                 where tier in ('REVISAR', 'HUMANO')
                   and (%s = '' or scope_id = %s)
                 group by 1""", (scope_id, scope_id))
            resumo = dict(cur.fetchall())
    finally:
        con.close()
    for i in itens:
        if i.get("revisado_em"):
            i["revisado_em"] = i["revisado_em"].isoformat()
    return {"itens": itens, "resumo": resumo}


@app.post("/api/logradouro/{fonte}/{record_id}/revisar")
def logradouro_revisar(fonte: str, record_id: str, e: RevisaoLogradouro,
                       u: _auth.Usuario = Depends(_auth.usuario_atual)):
    """A pessoa decide: corrige, confirma o que a máquina marcou, ou descarta.

    `revisado_por` não é enfeite. Sem ele, a correção humana fica
    indistinguível da automática três meses depois — e a pergunta "quem decidiu
    que esta rua se chama assim" fica sem resposta justamente nos casos em que
    ela importa, que são os que a máquina não soube resolver.
    """
    if e.status not in ("corrigido", "confirmado", "descartado"):
        raise HTTPException(422, "status deve ser corrigido, confirmado ou descartado")
    if e.status == "corrigido" and not e.logradouro_corrigido.strip():
        # Marcar "corrigido" sem escrever a correção deixaria a fila limpa e o
        # dado igual — o pior desfecho possível para uma revisão.
        raise HTTPException(422, "para marcar como corrigido, escreva o logradouro")

    con = _auth.conectar_como(u)
    try:
        with con.cursor() as cur:
            cur.execute("""
                update logradouro_ajustado
                   set revisao_status = %s,
                       logradouro_corrigido = nullif(%s, ''),
                       revisao_nota = nullif(%s, ''),
                       revisado_por = %s, revisado_em = now()
                 where fonte = %s and record_id = %s
                 returning tier, logradouro_original, logradouro_corrigido""",
                        (e.status, e.logradouro_corrigido.strip(), e.nota.strip(),
                         (u.email or u.nome or "?"), fonte, record_id))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, f"{fonte}:{record_id} não está na fila")
        con.commit()
    finally:
        con.close()
    return {"ok": True, "tier": r[0], "original": r[1], "corrigido": r[2]}


if __name__ == "__main__":
    import uvicorn
    # `127.0.0.1` continua sendo o PADRÃO, e isso é escolha e não descuido: o
    # painel não tem TLS próprio, então escutar em qualquer interface por
    # omissão seria servir login e dado de cliente em claro para a rede.
    #
    # No i9 o endereço segue 127.0.0.1 — quem escuta na rede é o Caddy, com o
    # certificado da Tailscale, repassando para cá pelo loopback. As variáveis
    # existem para o dia em que outro arranjo for necessário, e para o
    # `scripts/i9/painel.sh` declarar o que está fazendo em vez de depender do
    # que estiver escrito aqui.
    host = os.environ.get("CR_HOST", "127.0.0.1").strip() or "127.0.0.1"
    porta = int(os.environ.get("CR_PORTA", "8765"))
    if host not in ("127.0.0.1", "localhost"):
        print(f"\n  ⚠️  Escutando em {host} — SEM TLS. Só faz sentido atrás de um")
        print("     proxy que termine HTTPS; direto na rede, expõe a sessão.\n")
    print(f"\n  ✅ ComercialRadar no ar  →  http://{host}:{porta}")
    print("     deixe esta janela aberta · Ctrl+C para parar\n", flush=True)
    uvicorn.run(app, host=host, port=porta, log_level="info")
