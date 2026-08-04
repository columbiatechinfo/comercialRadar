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
import os
import re
import sys
import time
import asyncio
import threading
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime
from collections import Counter

from fastapi import FastAPI, UploadFile, File, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config  # .env + UTF-8
import area_utils
import realtime_ingest

BASE = Path(__file__).resolve().parent
FRONT = BASE / "frontend"
UPLOADS = BASE / "uploads"
AREAS = BASE / "areas"
MINERACAO = BASE / "mineracao"
MALHAS = BASE / "malhas"
# A área de trabalho mora na tabela `area_trabalho` (area_utils), não em
# arquivo: ela é compartilhada entre o servidor e os coletores, que rodam
# como subprocessos separados.
PYTHON = str(BASE / ".venv" / "Scripts" / "python.exe")

for d in (UPLOADS, AREAS, MINERACAO, MALHAS):
    d.mkdir(exist_ok=True)


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    manager.loop = asyncio.get_running_loop()   # captura o event loop p/ broadcast WS
    yield


app = FastAPI(title="ComercialRadar", lifespan=_lifespan)

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
    return {k: v for k, v in JOB.items() if k not in ("proc", "cat", "feitos")}


def _novo_job(modo: str, out_json: Path, extra: dict) -> dict:
    JOB.clear()
    JOB.update({
        "status": "rodando", "modo": modo, "inicio": datetime.now().isoformat(timespec="seconds"),
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


def _emitir_progresso():
    """Combina o progresso do log (feitos/total) com as categorias do watcher.
    'processados' vem do log (nunca passa do total); 'sem_match' é derivado, para
    fechar exatamente com a barra em qualquer modo."""
    c = JOB.get("cat", {})
    feitos = JOB.get("feitos", 0)
    total = JOB.get("total", 0)
    val, rec = c.get("validos", 0), c.get("recuperados", 0)
    desc, fora = c.get("descobertos", 0), c.get("fora_area", 0)
    sucessos = val + rec + desc + fora
    processados = max(feitos, sucessos)
    if total:
        processados = min(processados, total)
    sem = max(0, processados - sucessos)
    cont = {"processados": processados, "validos": val, "recuperados": rec,
            "descobertos": desc, "fora_area": fora, "sem_match": sem,
            "erros": 0, "ingeridos": c.get("ingeridos", 0)}
    JOB["contadores"] = cont
    manager.broadcast({"tipo": "progresso", "dados": {"contadores": cont, "total": total}})


def _thread_logs(proc: subprocess.Popen):
    """Lê o stdout do subprocess: retransmite como log e extrai o progresso real."""
    for linha in iter(proc.stdout.readline, ""):
        linha = linha.replace("\r", "").rstrip()
        if not linha:
            continue
        m = _RE_TOTAL_SHEET.search(linha) or _RE_TOTAL_MINA.search(linha)
        if m:
            JOB["total"] = int(m.group(1))
        for rgx in _RES_PROG:
            mp = rgx.search(linha)
            if mp:
                feitos, total = int(mp.group(1)), int(mp.group(2))
                JOB["feitos"] = max(JOB.get("feitos", 0), feitos)
                JOB["total"] = total
                _emitir_progresso()
                break
        manager.broadcast({"tipo": "log", "linha": linha[:300]})
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
    if status in ("recuperado_proximo", "recuperado_ia", "recuperado_gemini"):
        return "recuperados"
    if status in ("descoberto", "minerado"):
        return "descobertos"
    if status == "fora_da_area":
        return "fora_area"
    if status == "erro":
        return "erros"
    return "sem_match"  # nao_encontrado, encontrado_divergente, fora_da_uf


def _poi_leve(r: dict, poi_id) -> dict:
    la, lo = area_utils.coord_do_registro(r)
    return {
        "id": poi_id, "nome": r.get("nome") or r.get("nome_planilha"),
        "categoria": r.get("categoria"), "endereco": r.get("endereco"),
        "lat": la, "lng": lo, "fonte": r.get("fonte"), "fonte_dado": r.get("fonte_dado"),
        "status": r.get("status"),
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
                if resultado == "inserido":
                    ingeridos_keys.add(k)
                    manager.broadcast({"tipo": "poi", "poi": _poi_leve(r, poi_id)})

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


def _iniciar_subprocess(cmd: list, out_json: Path, poligono):
    import os
    # Baseline ANTES do Popen: fotografa o estado do arquivo para o watcher contar
    # só o delta deste job (evita recontar/re-ingerir o que já estava lá).
    baseline = _baseline_do_arquivo(out_json)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
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


@app.post("/api/area")
async def post_area(body: dict):
    poly = body.get("polygon") or []
    n = area_utils.salvar_area(poly, body.get("nome") or area_utils.AREA_PADRAO)
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
    conn = realtime_ingest.conectar()
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
    conn = realtime_ingest.conectar()
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
    conn = realtime_ingest.conectar()
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
    area_utils.salvar_area(poly)
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
        conn = realtime_ingest.conectar()
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
            conn = realtime_ingest.conectar()
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
# API — quadras (área → vias → quadras → pontos → faces)
#
# Cinco passos, cada um disparável sozinho e retomável pela SESSÃO. O servidor
# só orquestra: quem faz é `quadras_analise`, o mesmo módulo do terminal — para
# que rodar pelo mapa e rodar pelo shell não possam divergir.
# ──────────────────────────────────────────────────────────────────────────
def _qa():
    """Import tardio: puxa shapely e o DuckDB do OSM, que não precisam subir
    junto com o servidor."""
    import quadras_analise
    return quadras_analise


@app.get("/api/quadras/sessoes")
def quadras_sessoes(limite: int = 50):
    import quadras_db
    try:
        return quadras_db.sessoes(limite=limite)
    except Exception as e:
        return JSONResponse({"erro": str(e)[:200]}, status_code=500)


@app.get("/api/quadras/lista")
def quadras_lista(limite: int = 300, busca: str = ""):
    """Quadras já tratadas — a lista do painel. Clicar numa leva o mapa até ela."""
    import quadras_db
    try:
        return quadras_db.lista_quadras(limite=limite, busca=busca)
    except Exception as e:
        return JSONResponse({"erro": str(e)[:200]}, status_code=500)


@app.post("/api/quadras/area")
async def quadras_area(req: Request):
    """Passo 1 — a área desenhada vira sessão."""
    op = await req.json()
    try:
        return _qa().passo1_area(op["wkt"])
    except Exception as e:
        return JSONResponse({"erro": str(e)[:300]}, status_code=400)


@app.post("/api/quadras/rodar")
async def quadras_rodar(req: Request):
    """Passos 2–5 da sessão, em segundo plano (a leitura do nome canônico no
    Maps leva minutos). O progresso é lido por /api/quadras/{sid}."""
    op = await req.json()
    sid = op.get("sessao")
    de = int(op.get("de") or 2)
    ate = int(op.get("ate") or 5)
    if not sid:
        return JSONResponse({"erro": "informe a sessão"}, status_code=400)
    kw = {"usar_proxy": not op.get("sem_proxy"),
          "com_maps": bool(op.get("com_maps"))}
    threading.Thread(target=_qa().rodar, args=(sid, de, ate), kwargs=kw,
                     daemon=True).start()
    return {"ok": True, "sessao": sid, "de": de, "ate": ate}


@app.post("/api/quadras/retomar")
async def quadras_retomar(req: Request):
    """Continua do passo seguinte ao último concluído."""
    import quadras_db
    op = await req.json()
    sid = op.get("sessao")
    s = quadras_db.sessao(sid) if sid else None
    if not s:
        return JSONResponse({"erro": "sessão não encontrada"}, status_code=404)
    de = min(int(s["passo"]) + 1, 5)
    threading.Thread(target=_qa().rodar, args=(sid, de, 5),
                     kwargs={"usar_proxy": not op.get("sem_proxy")},
                     daemon=True).start()
    return {"ok": True, "sessao": sid, "de": de}


# nomes que são ROTAS, não sessões. Sem esta lista, uma instância antiga do
# servidor (sem /api/quadras/lista) deixa o {sid} capturar a palavra "lista" e
# responder "sessão não encontrada" — erro que não diz o que está errado.
_QUADRAS_RESERVADOS = {"lista", "sessoes", "area", "rodar", "retomar"}


@app.get("/api/quadras/{sid}")
def quadras_geojson(sid: str):
    """GeoJSON da sessão: vias, quadra (OSM e real), faces e pontos.

    O ponto sai na coordenada ORIGINAL do CNEFE — este processo classifica, não
    corrige a base."""
    import quadras_db
    if sid in _QUADRAS_RESERVADOS:
        return JSONResponse({"erro": f"'{sid}' é uma rota, não uma sessão — "
                                     f"este servidor está desatualizado, reinicie-o",
                             "servidor_antigo": True}, status_code=409)
    try:
        s = quadras_db.sessao(sid)
        if not s:
            return JSONResponse({"erro": "sessão não encontrada"}, status_code=404)
        con = realtime_ingest.conectar()
    except Exception as e:
        return JSONResponse({"erro": str(e)[:200]}, status_code=500)
    try:
        feats = []
        feats.append({"type": "Feature", "properties": {"camada": "area"},
                      "geometry": _wkt_geo(s["area_wkt"])})
        for v in quadras_db.vias(sid, con):
            feats.append({"type": "Feature",
                          "geometry": _wkt_geo(v["geom_wkt"]),
                          "properties": {"camada": "via", "id": v["id"],
                                         "nome_osm": v["nome_osm"], "tipo": v["tipo"],
                                         "nome_canonico": v["nome_canonico"],
                                         "comprimento_m": v["comprimento_m"]}})
        # a quadra de via aberta é degenerada (um corredor por lado da rua): o
        # mapa precisa saber, senão ela é desenhada como se fosse quarteirão
        aberta_q = set()
        for q in quadras_db.quadras(sid, con):
            ab = bool(q.get("via_aberta_id"))
            if ab:
                aberta_q.add(q["id"])
            feats.append({"type": "Feature", "geometry": _wkt_geo(q["geom_osm"]),
                          "properties": {"camada": "quadra_osm", "id": q["id"],
                                         "area_m2": q["area_osm_m2"], "vias": q["vias"],
                                         "via_aberta": ab, "lado": q.get("lado")}})
            if q["geom_real"]:
                feats.append({"type": "Feature", "geometry": _wkt_geo(q["geom_real"]),
                              "properties": {"camada": "quadra_real", "id": q["id"],
                                             "area_m2": q["area_real_m2"],
                                             "via_aberta": ab,
                                             "recuo_medio_m": q["recuo_medio_m"]}})
        for f in quadras_db.faces(sid, con):
            feats.append({"type": "Feature", "geometry": _wkt_geo(f["anel_wkt"]),
                          "properties": {"camada": "face", **f, "anel_wkt": None,
                                         "via_aberta": f["quadra_id"] in aberta_q,
                                         "anel_real_wkt": None}})
            # a borda REAL da face é o trilho do alinhamento (passo 6)
            if f.get("anel_real_wkt"):
                feats.append({"type": "Feature",
                              "geometry": _wkt_geo(f["anel_real_wkt"]),
                              "properties": {"camada": "face_real",
                                             "quadra_id": f["quadra_id"],
                                             "face_idx": f["face_idx"],
                                             "via_aberta": f["quadra_id"] in aberta_q,
                                             "nome_canonico": f["nome_canonico"]}})
        try:
            import quadras_telhados
            for t in quadras_telhados.telhados(sid, con):
                if not t.get("geom_wkt"):
                    continue
                feats.append({"type": "Feature",
                              "geometry": _wkt_geo(t["geom_wkt"]),
                              "properties": {"camada": "telhado", **t,
                                             "geom_wkt": None}})
        except Exception as e:
            print(f"[quadras] telhados indisponíveis: {str(e)[:90]}", flush=True)
        for p in quadras_db.pontos(sid, con):
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [p["lng"], p["lat"]]},
                          "properties": {"camada": "ponto", **p}})
        return {"type": "FeatureCollection", "features": feats,
                "sessao": {k: v for k, v in s.items()
                           if k not in ("area_wkt",)} | {
                    "criado_em": s["criado_em"].isoformat(timespec="seconds")
                    if s["criado_em"] else None,
                    "atualizado_em": s["atualizado_em"].isoformat(timespec="seconds")
                    if s["atualizado_em"] else None}}
    except Exception as e:
        return JSONResponse({"erro": str(e)[:300]}, status_code=500)
    finally:
        try: con.close()
        except Exception: pass


def _wkt_geo(w: str) -> dict:
    from shapely import wkt as _w
    from shapely.geometry import mapping
    return mapping(_w.loads(w))

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
                       a.veredito, a.motivo, a.recomendar_visita, a.tipo_construcao,
                       COALESCE(p.revisar_manual, false) AS revisar_manual, p.cidade
                FROM pois p
                LEFT JOIN analise_ia a ON a.poi_id = p.id
                WHERE p.match_valido IS NOT FALSE
                  AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL""")
            cols = ["id", "nome", "categoria", "endereco", "telefone", "avaliacao",
                    "total_avaliacoes", "fonte", "fonte_dado", "status", "lat", "lng",
                    "tem_cnpj", "situacao_cadastral", "endereco_fonte", "tem_tel", "tem_sv", "tem_foto",
                    "veredito", "motivo", "recomendar_visita", "tipo_construcao", "revisar_manual", "cidade"]
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
            return poi
    finally:
        conn.close()


@app.get("/api/sv/{poi_id}/{angulo}")
def sv_img(poi_id: int, angulo: str):
    """Serve a imagem do Street View (fachada/giro 360/panorama) direto do banco."""
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT dados FROM streetview_imgs
                           WHERE poi_id=%s AND angulo=%s AND dados IS NOT NULL
                           ORDER BY id DESC LIMIT 1""", (poi_id, angulo))
            r = cur.fetchone()
            if not r or not r[0]:
                return JSONResponse({"erro": "sem imagem"}, status_code=404)
            return Response(content=bytes(r[0]), media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})
    finally:
        conn.close()


@app.get("/api/stats")
def stats(cidade: str = ""):
    """Estatísticas do banco. Com ?cidade= filtra por município (casa com pois.cidade,
    case-insensitive) — o mapa seleciona um município por clique no polígono."""
    cidade = (cidade or "").strip()
    wp = "lower(cidade) = lower(%s)" if cidade else "TRUE"      # filtro em pois
    wj = "lower(p.cidade) = lower(%s)" if cidade else "TRUE"    # filtro em join com p
    pc = [cidade] if cidade else []
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
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
@app.post("/api/jobs")
def iniciar_job(body: dict):
    with _JOB_LOCK:
        if JOB.get("status") == "rodando" and JOB.get("proc") and JOB["proc"].poll() is None:
            return JSONResponse({"erro": "Já existe um job rodando. Pare-o antes de iniciar outro."},
                                status_code=409)

        poly = area_utils.carregar_area()
        if not poly:
            return JSONResponse({"erro": "Desenhe o polígono da área antes de iniciar."}, status_code=400)

        modo = body.get("modo")
        op = body.get("opcoes") or {}

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
            out_json = MINERACAO / f"{sessao}_db.json"
            cmd = [PYTHON, "minerar_area.py", "--area", area_utils.AREA_PADRAO,
                   "--sessao", sessao, "--out", str(out_json),
                   "--step", str(float(op.get("step", 150))),
                   "--radius", str(float(op.get("radius", 110)))]
            if op.get("details"):
                cmd.append("--details")
            _novo_job("mineracao", out_json, {"sessao": sessao})

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
            if op.get("pular_web"):
                cmd.append("--pular-web")
            if op.get("pular_streetview"):
                cmd.append("--pular-streetview")
            _novo_job("enriquecer_tudo", out_json, {})

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

        elif modo == "quadras":
            # Os 5 passos na área desenhada. Roda o MESMO CLI do terminal, como
            # os demais processos: o log do subprocess é o progresso na tela.
            # Não toca em POI nenhum — grava só nas tabelas de quadras.
            out_json = MINERACAO / "_quadras_noop.json"   # watcher fica ocioso
            cmd = [PYTHON, "quadras.py", "tudo", "--area", area_utils.AREA_PADRAO]
            if op.get("com_maps"):
                cmd.append("--com-maps")
            if op.get("sem_proxy"):
                cmd.append("--sem-proxy")
            _novo_job("quadras", out_json, {})

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
    """index.html com css/js versionados pelo mtime.

    O `?v=` troca a URL a cada edição do arquivo, então o navegador busca a versão
    nova mesmo tendo uma cópia velha em cache — sem depender de hard reload."""
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    for arq in ("style.css", "app.js"):
        v = int((FRONT / arq).stat().st_mtime)
        html = html.replace(f"/static/{arq}", f"/static/{arq}?v={v}")
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


class _FrontSemCache(StaticFiles):
    """O front é editado com o servidor no ar; sem isto o navegador segura o
    css/js antigos por horas (foi o que sumiu com o seletor de mapa). O
    "no-cache" não desliga o cache: manda revalidar, e o 304 é barato."""

    def file_response(self, *a, **kw):
        r = super().file_response(*a, **kw)
        r.headers["Cache-Control"] = "no-cache"
        return r


app.mount("/static", _FrontSemCache(directory=str(FRONT)), name="static")

# A pasta streetview/ deixou de existir: a fachada é servida por
# /api/sv/{poi_id}/facade, direto de `streetview_imgs`. Eram 2,6 GB de arquivo
# duplicando o que já estava no banco.


if __name__ == "__main__":
    import uvicorn
    print("\n  ✅ ComercialRadar no ar  →  http://127.0.0.1:8765")
    print("     deixe esta janela aberta · Ctrl+C para parar\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
