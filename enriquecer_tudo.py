"""
enriquecer_tudo.py — Enriquecimento em CASCATA (não opcional): cada POI pobre passa
por TODOS os métodos até ficar completo.

Fluxo, sobre os POIs válidos do banco que estão pobres (falta telefone OU endereço
OU categoria OU não tem foto):

  FASE 1 — Maps        : quem tem maps_url é aberto individualmente (painel completo:
                         fotos, reviews, telefone, horário). Grátis (proxy).
  FASE 2 — Web         : quem CONTINUA pobre → Yahoo + páginas + pré-filtro local +
                         LLM barato (só estrutura) + CNPJ/QSA na Receita Federal.
  FASE 3 — Street View : TODO POI localizado sem print ganha a foto da fachada.

Cada registro enriquecido é salvo num JSON incremental → o watcher do server ingere
(substitui por place_id/origem preservando o que já havia) e atualiza o mapa ao vivo.
O estado de "pobre" é mantido em memória entre as fases (não depende do watcher).

USO:
  .venv\\Scripts\\python enriquecer_tudo.py --out mineracao/enrich_tudo_db.json
     [--workers 6] [--no-proxy] [--limit N] [--area areas/area_atual.json]
     [--pular-maps] [--pular-cnpj-local] [--pular-web] [--pular-streetview]
"""

import json
import time
import shutil
import asyncio
import argparse
from pathlib import Path

from playwright.async_api import async_playwright

import config
import area_utils
import io_atomico
import realtime_ingest
from proxy_pool import ProxyPool
from human_browser import HumanSession
from search_pois_v2 import aguarda_painel_ou_lista, extract_panel, abrir_maps
from search_from_sheet import extrair_place_id
from extract_full import enriquecer_poi

import enriquecer_maps as EM
import minerar_web as MW
import capturar_evidencia as CE

BASE = Path(__file__).resolve().parent


# ──────────────────────────────────────────────────────────────────────────
# Carrega os POIs pobres do banco como registros (formato do ingester)
# ──────────────────────────────────────────────────────────────────────────
def _tem_foto(reg: dict) -> bool:
    """Foto já existente no banco (`_nfotos`) ou trazida agora (`fotos`).

    `carregar_carentes` sempre monta `fotos: []` — é o campo que a fase Maps
    PREENCHE. Quem responde pelo estado atual é o `_nfotos`, contado no banco.
    Confundir os dois faria todo POI parecer sem foto."""
    return bool(reg.get("_nfotos") or reg.get("fotos"))


def _falta_maps(reg: dict) -> bool:
    """Falta algo que o PAINEL DO MAPS sabe dar."""
    return (not reg.get("telefone") or not reg.get("endereco")
            or not reg.get("categoria") or not _tem_foto(reg))


def _falta_web(reg: dict) -> bool:
    """Falta algo que só a WEB dá: CNPJ (e o telefone que o Maps não achou).

    O Maps não devolve CNPJ — nenhum. Mandar por ele um POI que só precisa de
    CNPJ é abrir um navegador com proxy para buscar um dado que aquela fonte não
    tem: em Canoas seriam 9.071 POIs, horas de máquina, zero CNPJ. Cada fase
    pega o que ela sabe entregar."""
    return not reg.get("cnpj") or not reg.get("telefone")


def _carente(reg: dict) -> bool:
    """Falta algum dado crítico — é isto que 'enriquecer' quer dizer."""
    return _falta_maps(reg) or _falta_web(reg)


_SEM_INGEST = False  # via server (--sem-ingest): o watcher ingere, evita dois
                     # processos fazendo delete+recreate do mesmo place_id.

# Teto por POI na fase Web. O caminho completo (SERP + 5 páginas a 18 s + LLM a
# 45 s + BrasilAPI) cabe folgado aqui; o que passar disso está pendurado.
TIMEOUT_POI_S = 150.0
# Tamanho do lote: pequeno o bastante para um travamento custar pouco, grande o
# bastante para não serializar o pool de SERP.
LOTE_WEB = 60


def _ingerir(reg: dict, poligono):
    """Grava o registro DIRETO no Postgres (não depende do watcher do server).
    Assim o enriquecer_tudo funciona rodado no terminal, sem o web ligado.
    Com --sem-ingest (rodando sob o server) NÃO ingere: quem grava é o watcher."""
    if _SEM_INGEST:
        return
    try:
        realtime_ingest.ingerir_registro(reg, poligono)
    except Exception as e:
        print(f"  ⚠️ ingest: {str(e)[:90]}", flush=True)


def carregar_carentes(poligono, limit: int, alvo: list | None = None) -> list:
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.place_id, p.maps_url, p.fonte, p.sessao, p.status,
                       p.nome, p.categoria, p.endereco, p.telefone, p.website,
                       p.avaliacao, p.total_avaliacoes, p.status_horario,
                       p.nome_original, p.endereco_original,
                       COALESCE(p.maps_lat, p.lat_origem), COALESCE(p.maps_lng, p.lng_origem),
                       p.lat_origem, p.lng_origem, p.cnpj, p.instagram,
                       -- cidade/uf do POI: sem elas a fase Web caía no padrão
                       -- fixo "Parnaíba" e a conferência do CNPJ na Receita
                       -- rejeitava TODO CNPJ de qualquer outra cidade
                       p.cidade, p.uf,
                       (SELECT COUNT(*) FROM images_urls i WHERE i.poi_id = p.id) AS nfotos
                FROM pois p
                WHERE p.match_valido IS NOT FALSE
                  AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL
                  -- DADO CRÍTICO FALTANDO: telefone, endereço, categoria, CNPJ
                  -- ou imagem. O `status IN (...)` de antes limitava a busca de
                  -- fotos aos POIs "rasos", e deixava de fora justamente os que
                  -- a captura traz como `ok` — 2.738 sem foto em Canoas.
                  AND (%(alvo)s::bigint[] IS NOT NULL
                       -- Sem alvo, a pergunta continua sendo "quem está pobre".
                       OR p.telefone IS NULL OR p.endereco IS NULL
                       OR p.categoria IS NULL OR p.cnpj IS NULL
                       OR NOT EXISTS (SELECT 1 FROM images_urls i WHERE i.poi_id = p.id))
                  -- COM alvo, são exatamente estes — e nenhum outro. Encadear
                  -- depois de gerar 22 POIs não pode significar varrer os 27
                  -- mil do banco para alcançar aqueles 22.
                  AND (%(alvo)s::bigint[] IS NULL OR p.id = ANY(%(alvo)s::bigint[]))
                ORDER BY p.id""", {"alvo": (alvo or None)})
            cols = ["db_id", "place_id", "maps_url", "fonte", "sessao", "status",
                    "nome", "categoria", "endereco", "telefone", "website",
                    "avaliacao", "total_avaliacoes", "status_horario",
                    "nome_original", "endereco_original", "lat", "lng",
                    "lat_origem", "lng_origem", "cnpj", "instagram",
                    "cidade", "uf", "nfotos"]
            regs = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                regs.append({
                    # identidade / dedup
                    "place_id": d["place_id"], "fonte": d["fonte"], "sessao": d["sessao"],
                    "status": d["status"], "match_valido": True,
                    "nome": d["nome"], "nome_planilha": d["nome_original"],
                    "endereco_planilha": d["endereco_original"],
                    "maps_url": d["maps_url"],
                    "lat_origem": d["lat_origem"], "lng_origem": d["lng_origem"],
                    "maps_lat": d["lat"], "maps_lng": d["lng"],
                    # dados atuais (para saber o que falta)
                    "categoria": d["categoria"], "endereco": d["endereco"],
                    "telefone": d["telefone"], "website": d["website"],
                    "avaliacao": d["avaliacao"], "total_avaliacoes": d["total_avaliacoes"],
                    "status_horario": d["status_horario"], "cnpj": d["cnpj"],
                    "instagram": d["instagram"],
                    "cidade": d["cidade"], "uf": d["uf"],
                    "fotos": [], "comentarios": [], "horarios": {},
                    "_nfotos": d["nfotos"], "_db_id": d["db_id"],
                })
            # A ÁREA É O FOCO — também aqui. O `poligono` chegava e não era
            # usado: a cascata varria o banco INTEIRO. Medido em 04/08/2026 com
            # a área em Canoas: 2.170 POIs carentes, dos quais 6 dentro dela e
            # 2.083 em Parnaíba — horas de browser e proxy gastas fora do foco,
            # por um botão que diz "enriquecer a área".
            # Quem foi gravado fora do polígono espera a sua vez: será enriquecido
            # no dia em que a área de trabalho for a cidade dele.
            if poligono:
                antes = len(regs)
                regs = [r for r in regs
                        if area_utils.ponto_no_poligono(
                            r.get("maps_lat") if r.get("maps_lat") is not None
                            else r.get("lat_origem"),
                            r.get("maps_lng") if r.get("maps_lng") is not None
                            else r.get("lng_origem"), poligono)]
                print(f"   área de trabalho: {len(regs)} de {antes} POIs carentes "
                      f"estão dentro dela", flush=True)
            return regs[:limit] if limit > 0 else regs
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────
# FASE 1 — Maps
# ──────────────────────────────────────────────────────────────────────────
async def fase_maps(regs, pool, usar_proxy, poligono, salvar, prog):
    # só quem precisa do que o Maps dá — quem só espera CNPJ vai direto à Web
    alvos = [r for r in regs if r.get("maps_url") and _falta_maps(r)]
    total = len(alvos)
    prog["fase"] = "Maps"
    print(f"🔗 FASE 1/3 — Maps: {total} POIs com maps_url", flush=True)
    if not total:
        return
    lotes = [alvos[i:i + 12] for i in range(0, len(alvos), 12)]
    queue = asyncio.Queue()
    for i, b in enumerate(lotes):
        queue.put_nowait((i, b))
    counter = {"n": 0, "ok": 0}
    lock = asyncio.Lock()

    async def _worker(wid):
        while True:
            try:
                bidx, lote = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            proxy = await pool.acquire_blocking() if usar_proxy else None
            profile = config.BROWSER_PROFILES_DIR / f"et_maps_w{wid}_b{bidx}"
            sess = None
            try:
                tz = next((a.get("lng_origem") for a in lote if a.get("lng_origem")), None)
                sess = await HumanSession.create(_PW, proxy, profile, layer="maps",
                                                 tz_hint_lng=tz, headless=True)
                if not await abrir_maps(sess):
                    queue.put_nowait((bidx, lote))
                    if proxy:
                        await pool.mark_cooldown(proxy, 600)
                    continue
                for reg in lote:
                    if await sess.is_captcha():
                        break
                    poi = await EM._extrair(sess, reg)
                    async with lock:
                        counter["n"] += 1
                        if poi and poi.get("nome"):
                            _aplica_maps(reg, poi)
                            reg["_tocado"] = True   # Maps trouxe painel real
                            if area_utils.gate_registro(reg, poligono):
                                counter["ok"] += 1
                                _ingerir(reg, poligono)   # grava DIRETO no banco
                            salvar()
                        prog["feitos"] += 1
                        print(f"🔗 [Maps W{wid}] POIs {counter['n']}/{total} | "
                              f"enriquecidos {counter['ok']} | {reg['nome'][:30]}", flush=True)
                    await sess.humanized_wait()
            except Exception as e:
                print(f"  Maps W{wid} erro: {str(e)[:80]}", flush=True)
            finally:
                if sess:
                    try:
                        await sess.close()
                    except Exception:
                        pass
                shutil.rmtree(profile, ignore_errors=True)
                if proxy:
                    await pool.release(proxy)

    n = min(6, config.MAX_WORKERS, len(lotes))
    await asyncio.gather(*[_worker(i) for i in range(n)])


def _aplica_maps(reg: dict, poi: dict):
    for k_reg, k_poi in [("nome", "nome"), ("categoria", "categoria"), ("endereco", "endereco"),
                         ("telefone", "telefone"), ("website", "website"),
                         ("avaliacao", "avaliacao"), ("status_horario", "status_horario")]:
        v = poi.get(k_poi)
        if v not in (None, "", []):
            reg[k_reg] = v
    if poi.get("total_avaliacoes"):
        reg["total_avaliacoes"] = poi["total_avaliacoes"]
    if poi.get("endereco"):
        reg["endereco_fonte"] = "maps"
    if poi.get("maps_lat") is not None:
        reg["maps_lat"], reg["maps_lng"] = poi["maps_lat"], poi["maps_lng"]
    reg["maps_url"] = poi.get("maps_url") or reg.get("maps_url")
    pid = extrair_place_id(poi.get("maps_url", ""))
    if pid:
        reg["place_id"] = pid
    reg["fotos"] = poi.get("fotos", []) or reg.get("fotos", [])
    reg["comentarios"] = poi.get("comentarios", []) or reg.get("comentarios", [])
    reg["horarios"] = poi.get("horarios", {}) or reg.get("horarios", {})
    reg["fonte_dado"] = "maps"


# ──────────────────────────────────────────────────────────────────────────
# FASE 2 — Web (Yahoo + Receita) sobre quem continua pobre
# ──────────────────────────────────────────────────────────────────────────
def fase_cnpj_local(regs, poligono, salvar, prog) -> dict:
    """CNPJ pela base da Receita que já está no banco — ANTES de ir à web.

    Buscar CNPJ no Yahoo sendo que as tabelas `rf_*` têm o Brasil inteiro é pagar
    caro e frágil pelo que está a uma consulta de distância: no `RDK Logs` o SERP
    devolveu zero resultados e o CNPJ estava aqui. Roda em segundos, não custa
    nada, e o que não casar segue para a web — que passa a ser o resíduo, não a
    porta de entrada."""
    import cnpj_local as CL
    prog["fase"] = "CNPJ local"
    alvos = [r for r in regs if not r.get("cnpj")]
    print(f"🏢 FASE 2/4 — CNPJ na base da Receita (local): {len(alvos)} POIs sem CNPJ",
          flush=True)
    if not alvos:
        return {"casados": 0}
    res = CL.casar(alvos)
    for r in alvos:
        if r.get("cnpj"):
            r["_tocado"] = True
            _ingerir(r, poligono)
            prog["feitos"] += 1
    salvar()
    print(f"   ✅ {res['casados']} casados "
          f"({res['casados'] - res.get('endereco_unico', 0)} por nome + "
          f"{res.get('endereco_unico', 0)} por endereço único) · "
          f"{res['fracos']} recusados (várias empresas na mesma porta) · "
          f"{res['sem_endereco']} sem CEP/número no endereço", flush=True)
    return res


async def fase_web(regs, uf, cidade_arg, usar_proxy, workers, poligono, salvar,
                   prog, visivel: bool = False):
    import aiohttp
    ainda = [r for r in regs if _falta_web(r)]
    total = len(ainda)
    prog["fase"] = "Web"
    print(f"🌐 FASE 2/3 — Web: {total} POIs sem CNPJ ou sem telefone", flush=True)
    if not total:
        return
    # visivel=True abre UM navegador por worker na tela: dá para acompanhar a
    # digitação e o resultado, e comportamento de uso real atrapalha detector
    serp = await MW.SerpPool(min(workers, 6), usar_proxy=usar_proxy,
                             visivel=visivel).start()
    sem = asyncio.Semaphore(workers)
    counter = {"n": 0, "ok": 0}
    lock = asyncio.Lock()
    conn = aiohttp.TCPConnector(limit=workers * 4, ssl=False)
    try:
        async with aiohttp.ClientSession(connector=conn) as session:
            async def _um(reg):
                cidade = MW._cidade_do(reg, cidade_arg)
                uf_reg = MW._uf_do(reg, uf)     # UF do POI, não a do primeiro
                antes = (reg.get("telefone"), reg.get("endereco"), reg.get("cnpj"))
                try:
                    # WATCHDOG: qualquer await pendurado — SERP, fetch de página,
                    # LLM, BrasilAPI — vira erro deste POI em vez de silêncio do
                    # job inteiro. Sem ele, um `await` sem teto congela o
                    # semáforo e nada mais anda.
                    await asyncio.wait_for(
                        MW._processar_poi(session, serp, reg, cidade, uf_reg, sem),
                        timeout=TIMEOUT_POI_S)
                except asyncio.TimeoutError:
                    async with lock:
                        counter["timeout"] = counter.get("timeout", 0) + 1
                    print(f"   ⏱️  {(reg.get('nome') or '')[:30]} passou de "
                          f"{TIMEOUT_POI_S:.0f}s — pulado", flush=True)
                except Exception:
                    pass
                if poligono:
                    area_utils.gate_registro(reg, poligono)
                mudou = (reg.get("telefone"), reg.get("endereco"), reg.get("cnpj")) != antes
                async with lock:
                    counter["n"] += 1
                    if mudou and reg.get("match_valido"):
                        reg["_tocado"] = True   # a web acrescentou algo real
                        counter["ok"] += 1
                        _ingerir(reg, poligono)   # grava DIRETO no banco
                        salvar()
                    prog["feitos"] += 1
                    print(f"🌐 [Web] POIs {counter['n']}/{total} | enriquecidos {counter['ok']} | "
                          f"tokens {MW._USO['in']+MW._USO['out']} (US${MW._custo():.3f}) | "
                          f"{(reg.get('nome') or '')[:28]}", flush=True)

            # EM LOTES, não num `gather` de milhares. O gather único criava as
            # 9.110 corrotinas de uma vez: sem ponto de parada, sem retomada, e
            # um punhado de awaits presos parava tudo. Em lote, um problema
            # custa no máximo um lote — e o `salvar()` no fim de cada um deixa o
            # trabalho no disco.
            for i in range(0, total, LOTE_WEB):
                bloco = ainda[i:i + LOTE_WEB]
                await asyncio.gather(*[_um(r) for r in bloco])
                salvar()
                print(f"   ── lote {i // LOTE_WEB + 1}/"
                      f"{(total + LOTE_WEB - 1) // LOTE_WEB} concluído "
                      f"({counter['n']}/{total}) ──", flush=True)
            if counter.get("timeout"):
                print(f"   ⏱️  {counter['timeout']} POIs estouraram o teto de "
                      f"{TIMEOUT_POI_S:.0f}s", flush=True)
    finally:
        await serp.close()


# ──────────────────────────────────────────────────────────────────────────
# FASE 3 — Street View para todos os localizados sem print
# ──────────────────────────────────────────────────────────────────────────
# Pobre para efeito de FACHADA: sem foto, sem telefone ou sem avaliação.
# Não é o mesmo "carente" das fases Maps/Web — aquele inclui "sem CNPJ", e depois
# que a Receita local preencheu milhares de CNPJs isso não separa mais ninguém.
# A pergunta aqui é outra: de quem eu ainda não sei quase nada? De quem já tem
# foto, telefone e nota, a fachada acrescenta pouco e custa o mesmo.
_SQL_POBRE = """(NOT EXISTS (SELECT 1 FROM images_urls i WHERE i.poi_id = p.id)
                 OR p.telefone IS NULL OR p.telefone = ''
                 OR p.total_avaliacoes IS NULL OR p.total_avaliacoes = 0)"""

# JÁ COMERCIAL NO CADASTRO NÃO PRECISA DE FACHADA — regra do usuário,
# 14/08/2026. O cliente já cobra esse imóvel como comercial: não há
# reclassificação a propor, e capturar para confirmar o que a base já afirma
# gasta hora de captura que a área precisa em outro lugar. Continua sendo
# OPÇÃO, para quem quiser o dossiê da carteira inteira.
_SQL_JA_COMERCIAL = """EXISTS (SELECT 1 FROM cadastro_cliente c
                                WHERE c.poi_id = p.id AND c.e_comercial)"""


def _sem_streetview_na_area(poligono, so_pobres: bool = False,
                            incluir_ja_comerciais: bool = False,
                            alvo: list | None = None) -> list:
    """IDs dos POIs DA ÁREA que ainda não têm foto de fachada.

    Por padrão pega TODO POI da área: a fachada falta a POI completo também. Dos
    28 POIs da área de Canoas, 22 tinham telefone, endereço e categoria — e
    **zero** tinham Street View.

    Com `so_pobres`, restringe a quem está mal documentado (sem foto, sem
    telefone ou sem avaliação). Passou a fazer diferença quando a área saltou de
    9 mil para 21,7 mil POIs: a 1.380 fachadas/hora, varrer tudo é ~15 h, e boa
    parte disso é fotografar de novo a porta de quem já tem foto, telefone e
    nota."""
    conn = realtime_ingest.conectar()
    try:
        # `soltar_presos` SAIU em 07/09/2026, junto com a tabela que ela
        # destravava. Ela devolvia à fila quem tinha `streetview_path` gravado
        # e nenhuma imagem em `streetview_imgs`; `streetview_imgs` não existe
        # mais, e o filtro abaixo agora pergunta direto a `poi_evidencia`, que
        # é onde a foto está — sem intermediário para ficar preso.
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT p.id, COALESCE(p.maps_lat, p.lat_origem),
                       COALESCE(p.maps_lng, p.lng_origem)
                  FROM pois p
                 WHERE p.match_valido IS NOT FALSE
                   AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL
                   -- QUEM NAO TEM FOTO EM `poi_evidencia`, e nao mais quem
                   -- tem `streetview_path` vazio. A coluna antiga apontava
                   -- para arquivo em disco e a captura de hoje nao a preenche:
                   -- perguntar por ela devolveria a base inteira, sempre.
                   --
                   -- 'NA' volta à fila a cada rodada da ÁREA: cobertura nova
                   -- aparece, e a marca já se provou falha demais para ser
                   -- definitiva. Quem não tem panorama mesmo custa uma consulta
                   -- de metadados, que é grátis. Por isso o `motivo_falha` nao
                   -- entra aqui: so os bytes contam como feito.
                   AND NOT EXISTS (SELECT 1 FROM radar_comercial.poi_evidencia e
                                    WHERE e.poi_id = p.id AND e.tipo = 'sv_frente'
                                      AND (e.dados IS NOT NULL
                                           OR e.storage_path IS NOT NULL))
                   {"AND p.id = ANY(%(alvo)s::bigint[])" if alvo else ""}
                   {f'AND {_SQL_POBRE}' if so_pobres else ''}
                   {'' if incluir_ja_comerciais else f'AND NOT {_SQL_JA_COMERCIAL}'}""",
                        # O dicionário vai SEMPRE, e não só quando há alvo: sem
                        # ele o psycopg2 não interpola, e o `%(alvo)s` chegaria
                        # cru ao Postgres. Passar sempre também protege
                        # qualquer `%` que apareça no SQL montado acima.
                        {"alvo": (alvo or None)})
            linhas = cur.fetchall()
    finally:
        conn.close()
    if not poligono:
        return [i for i, _, _ in linhas]
    return [i for i, la, lo in linhas
            if area_utils.ponto_no_poligono(la, lo, poligono)]


async def fase_streetview(workers, prog, poligono, so_pobres: bool = False,
                          incluir_ja_comerciais: bool = False, alvo: list | None = None):
    prog["fase"] = "Street View"
    ids = _sem_streetview_na_area(poligono, so_pobres, incluir_ja_comerciais, alvo)
    # MARCADOR DE FASE: sem ele o painel continuava somando a fase Web. A barra
    # anunciava "2.229 de 21.701" — 2.229 era o que a Web tinha gravado, 21.701
    # é o total de fachadas: dois números de fases diferentes na mesma frase, e
    # nenhum deles o andamento do Street View, que naquele instante era 31.
    print("⟦fase⟧ streetview", flush=True)
    alvo = "pobres (sem foto/telefone/avaliação)" if so_pobres else "da área"
    corte = ("" if incluir_ja_comerciais
             else " · fora os que já são comerciais no cadastro")
    print(f"📸 FASE 3/3 — Street View: {len(ids)} POIs {alvo} sem fachada{corte}",
          flush=True)
    if not ids:
        return
    # grava as quatro visadas em `poi_evidencia`, com mira e recorte
    await CE.rodar(None, 0, True, workers, pois=ids)


# ──────────────────────────────────────────────────────────────────────────
# Orquestração
# ──────────────────────────────────────────────────────────────────────────
_PW = None


async def run(out_json: Path, workers: int, usar_proxy: bool, limit: int, area_path: str,
              pular_maps: bool, pular_web: bool, pular_sv: bool,
              pular_cnpj_local: bool = False, visivel: bool = False,
              sv_so_pobres: bool = False, incluir_ja_comerciais: bool = False,
              alvo: list | None = None):
    global _PW
    poligono = area_utils.carregar_area(area_path) if area_path else None
    regs = carregar_carentes(poligono, limit, alvo)
    total = len(regs)
    # CIDADE E UF VÊM DA ÁREA DE TRABALHO — a mesma que o usuário definiu no
    # painel (select de UF+município, clique no mapa ou polígono desenhado).
    # É a fonte única da ferramenta; deduzir por POI, ou pior, cair num padrão
    # fixo, foi o que fez a fase Web buscar "parnaiba RS" numa rodada de Canoas
    # e a conferência do CNPJ rejeitar todo CNPJ por município divergente.
    cidade, uf = area_utils.municipio_da_area(poligono)
    print(f"💎 Enriquecimento em cascata: {total} POIs pobres | "
          f"Área: {cidade or '?'}/{uf or '?'} | "
          f"Proxy: {'sim' if usar_proxy else 'NÃO'} | Workers: {workers}", flush=True)
    print(f"POIs : {total} | Pendentes: {total}", flush=True)  # p/ o server captar o total
    # ANTES do desvio abaixo: o ramo "sem carente" também usa `prog`, e com ele
    # declarado só adiante o processo morria de UnboundLocalError exatamente no
    # caso em que a fachada era o único trabalho que restava.
    prog = {"fase": "-", "feitos": 0}
    if not total:
        # Sem carente NÃO quer dizer sem trabalho: a fachada falta a POI
        # completo, e sair aqui pularia a fase 3 justamente no caso em que ela
        # é a única coisa que resta a fazer.
        print("✅ Nenhum POI pobre para Maps/Web.", flush=True)
        if not pular_sv:
            await fase_streetview(min(workers, 4), prog, poligono, sv_so_pobres,
                                  incluir_ja_comerciais, alvo)
        return

    lock_io = asyncio.Lock()

    def salvar():
        # SÓ os que foram GENUINAMENTE enriquecidos AGORA (flag _tocado). Sem isso,
        # re-ingeriríamos registros que só "tinham telefone de antes" — e a re-ingestão
        # apagaria as fotos deles (a fase Maps que falhou volta com fotos=[]).
        payload = [r for r in regs if r.get("_tocado")]
        io_atomico.escrever_json(out_json, payload)

    config.BROWSER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    ini = time.time()
    async with async_playwright() as pw:
        _PW = pw
        pool = ProxyPool().start() if usar_proxy else None
        if not pular_maps:
            await fase_maps(regs, pool, usar_proxy, poligono, salvar, prog)
        # local antes da web: o que a Receita já responde não precisa de SERP
        if not pular_cnpj_local:
            fase_cnpj_local(regs, poligono, salvar, prog)
        if not pular_web:
            await fase_web(regs, uf, cidade, usar_proxy, workers, poligono, salvar,
                           prog, visivel=visivel)
        salvar()
    shutil.rmtree(config.BROWSER_PROFILES_DIR, ignore_errors=True)

    if not pular_sv:
        await fase_streetview(min(workers, 4), prog, poligono, sv_so_pobres,
                              incluir_ja_comerciais, alvo)

    # O RESUMO SÓ FALA DAS FASES QUE RODARAM. "Completos/incompletos" mede o
    # carente de Maps/Web; numa rodada só de Street View esses campos não podiam
    # mudar, e o resumo anunciava "Ficaram completos: 0" — verdade aritmética
    # lida como fracasso. Fase pulada não entra em linha nenhuma.
    rodou_dados = not (pular_maps and pular_cnpj_local and pular_web)
    print(f"\n{'═'*52}")
    print(f"💎 Enriquecimento em cascata | Resumo")
    print(f"{'═'*52}")
    if rodou_dados:
        completos = sum(1 for r in regs if not _carente(r))
        tocados = sum(1 for r in regs if r.get("_tocado"))
        print(f"   POIs pobres tratados : {total}")
        print(f"   Enriquecidos (algo novo) : {tocados}")
        print(f"   Ficaram completos    : {completos}")
        print(f"   Ainda incompletos    : {total - completos}")
    else:
        print(f"   Fases Maps/CNPJ/Web  : puladas (rodada só de fachadas)")
    if not pular_web:
        print(f"   Custo web (LLM)      : US$ {MW._custo():.4f}")
    if not pular_sv:
        print(f"   Fachadas             : ver linhas '📸' acima "
              f"(gravadas em streetview_imgs)")
    print(f"   Tempo                : {(time.time()-ini)/60:.1f} min")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="mineracao/enrich_tudo_db.json")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--area", default="")
    p.add_argument("--pular-maps", action="store_true")
    p.add_argument("--pular-cnpj-local", action="store_true")
    p.add_argument("--visivel", action="store_true",
                   help="abre os navegadores na TELA (1 por worker)")
    p.add_argument("--pular-web", action="store_true")
    p.add_argument("--pular-streetview", action="store_true")
    p.add_argument("--incluir-ja-comerciais", action="store_true",
                   help="tambem captura fachada de quem ja e comercial no cadastro")
    p.add_argument("--sv-so-pobres", action="store_true",
                   help="Street View só em POI mal documentado (sem foto, sem "
                        "telefone ou sem avaliação).")
    p.add_argument("--sem-ingest", action="store_true",
                   help="Não ingere direto (rodando sob o server web, o watcher grava).")
    p.add_argument("--poi-ids", default="",
                   help="ids separados por vírgula. Enriquece EXATAMENTE estes, "
                        "na mesma ordem de fases — é o que permite encadear "
                        "depois de gerar POI de uma base pública, sem varrer o "
                        "banco inteiro para alcançar os poucos que nasceram.")
    a = p.parse_args()
    global _SEM_INGEST
    _SEM_INGEST = a.sem_ingest
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    alvo = [int(x) for x in a.poi_ids.split(",") if x.strip().isdigit()] or None
    if a.poi_ids and not alvo:
        # Pedir alvo e receber lista vazia NÃO pode virar "enriquece tudo":
        # seria a diferença entre 22 POIs e 27 mil, decidida por um erro de
        # digitação.
        raise SystemExit(f"--poi-ids={a.poi_ids!r} não tem nenhum id válido")
    asyncio.run(run(out, a.workers, not a.no_proxy, a.limit, a.area,
                    a.pular_maps, a.pular_web, a.pular_streetview,
                    a.pular_cnpj_local, a.visivel, a.sv_so_pobres,
                    a.incluir_ja_comerciais, alvo))


if __name__ == "__main__":
    main()
