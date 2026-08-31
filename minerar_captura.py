"""
minerar_captura.py — a mineração de área pelo processo PRINCIPAL (captura + OCR).

É o caminho que o painel usa na aba "Mineração de área". Ele não consulta a
Places API paga: fotografa o Google Maps em tiles 4K com um estilo vetorial
limpo (só os markers de POI, sem nome de rua), detecta os ícones, lê os nomes
por OCR e busca cada um no Maps para virar POI com coordenada e painel.

    .venv\\Scripts\\python minerar_captura.py --sessao NOME [--area area_atual]
                                             [--zoom 19] [--workers 10]

Quatro estágios, cada um retomável por si (todos pulam o que já fizeram):

    1  captura   src/capture-cli.ts   → capturas/<sessao>/*.png
    2  recortes  detect_crops.py      → capturas/<sessao>/crops/*.png
    3  OCR       ocr_pois.py          → crops/ocr_resultado.json
    4  busca     search_pois_v2.py    → crops/search_resultado.json

O arquivo do estágio 4 é o que o watcher do servidor ingere no Postgres: ele já
sai no formato do ingester (`nome`, `maps_lat`, `match_valido`, `place_id`…).
Por isso este script NÃO grava no banco — quem grava é o watcher, e dois
processos no mesmo delete+recreate se atropelam.

O progresso é traduzido para as linhas que o `_thread_logs` do servidor lê
("— N células", "célula X/Y", "POIs X/Y"), para a barra do painel andar.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import config  # .env + UTF-8
import area_utils

BASE = Path(__file__).resolve().parent
CAPTURAS = BASE / "capturas"
PYTHON = str(BASE / ".venv" / "Scripts" / "python.exe")
if not Path(PYTHON).exists():
    PYTHON = sys.executable

# "📸 [    12/340] 3.5% | ✔12 ✗0" → a barra de captura
_RE_TILE = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")
# "🌐 [W2] lote 3/9 ... [128/340 38%]" → a barra da busca
_RE_BUSCA = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s+\d+%\s*\]")
# de quanto em quanto tempo o resultado é achatado para o watcher enxergar
NORMALIZA_S = 10.0


def _ts_node() -> list:
    """Como chamar o `ts-node` — o DO PROJETO, se ele estiver instalado.

    A versão anterior devolvia sempre `npx`, e o comando ficava
    `npx ts-node src/capture-cli.ts`. Funciona na máquina de quem desenvolve,
    onde `npm install` já rodou e o npx acha o binário local. No contêiner
    falhou de um jeito que não parece o que é:

        TypeError: Cannot read properties of undefined (reading 'fileExists')
          at readConfig (/tmp/.npm/_npx/1bf7c3c15bf47d04/node_modules/ts-node/...)

    Repare no caminho: `/tmp/.npm/_npx/...`. O npx NÃO usou o ts-node de
    `/app/node_modules/.bin` — que está lá, instalado por `npm ci` — e foi
    BAIXAR outro da internet. O ts-node avulso não enxerga o `typescript` do
    projeto, e estoura num campo interno, sem dizer que o problema é esse.

    Chamar o binário local pelo caminho tira o npx da decisão: ou o arquivo
    existe e é ele, ou não existe e caímos no npx como antes. Nenhuma rede, e
    nenhuma versão surpresa.
    """
    local = BASE / "node_modules" / ".bin" / ("ts-node.cmd" if os.name == "nt" else "ts-node")
    if local.exists():
        return [str(local)]

    # Sem `node_modules` — máquina de desenvolvimento antes do `npm install`.
    # No Windows o executável é `npx.cmd`; `npx` sozinho não é executável fora
    # do shell e o subprocess morre com FileNotFoundError.
    if os.name == "nt":
        for c in ("npx.cmd", "npx.exe"):
            for d in os.environ.get("PATH", "").split(os.pathsep):
                cand = Path(d) / c
                if cand.exists():
                    return [str(cand), "ts-node"]
    return ["npx", "ts-node"]


def _env(**extra) -> dict:
    """Ambiente dos filhos com UTF-8 forçado.

    Ligado a um PIPE (e não a um console), o Python do Windows escolhe cp1252 e
    o primeiro emoji do log derruba o processo — `detect_crops.py` morria no
    próprio cabeçalho, com UnicodeEncodeError, antes de detectar nada. Quem
    importa `config` chama `forcar_utf8()` e escapa; nem todo script importa.
    Resolver pelo ambiente cobre qualquer filho, inclusive os futuros."""
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **extra)


def _linhas(stream):
    """Quebra o stream em linhas por \\n **E por \\r**.

    A captura escreve o progresso com `process.stdout.write('\\r📸 [...]')` —
    sem quebra de linha, para reescrever a mesma linha no terminal. Lendo com
    `for linha in stream`, nada chega até aparecer um `\\n`: os 264 tiles vinham
    num único bloco no fim, e a barra do painel ficava congelada no meio do
    caminho (o usuário viu 90/264 com a captura já concluída). Lendo caractere a
    caractere o progresso sai na hora; o volume é de alguns KB por estágio."""
    buf = []
    while True:
        c = stream.read(1)
        if not c:
            break
        if c in "\r\n":
            linha = "".join(buf).rstrip()
            buf = []
            if linha:
                yield linha
        else:
            buf.append(c)
    resto = "".join(buf).rstrip()
    if resto:
        yield resto


def _fase(nome: str):
    """Marca a fase para o painel. Sem isto, o servidor trata TILE como se fosse
    POI e deriva `sem_match = processados − sucessos`: durante a captura o
    painel anunciava '90 SEM MATCH' quando nenhum POI tinha sido buscado."""
    print(f"⟦fase⟧ {nome}", flush=True)


def _rodar(cmd, titulo: str, traduz=None) -> int:
    """Roda o estágio repassando o stdout dele linha a linha.

    O painel lê o NOSSO stdout, então tudo que o filho imprime tem de passar por
    aqui — e o `traduz` acrescenta a linha de progresso no formato que o
    servidor reconhece, sem precisar mexer no filho."""
    print(f"\n{'─' * 62}\n▶ {titulo}\n{'─' * 62}", flush=True)
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(BASE), env=_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            encoding="utf-8", errors="replace")
    ultimo = None
    for linha in _linhas(proc.stdout):
        print(linha, flush=True)
        if traduz:
            t = traduz(linha)
            if t and t != ultimo:
                print(t, flush=True)
                ultimo = t
    cod = proc.wait()
    print(f"◀ {titulo}: {'ok' if cod == 0 else f'FALHOU (código {cod})'} "
          f"em {time.time() - t0:.0f} s", flush=True)
    return cod


def _normalizar(sessao_json: Path, silencioso: bool = False) -> Path | None:
    """Achata o `search_resultado.json` para o formato do ingester.

    O `search_pois_v2` grava o POI ANINHADO (`{"poi": {...}, "status": ...}`),
    e tanto o `realtime_ingest` quanto o `src/ingest.ts` leem os campos no TOPO
    (`nome`, `maps_lat`, `place_id`). Sem esta passada o watcher lê o arquivo,
    não acha `nome` em registro nenhum e pula TODOS em silêncio — o job terminaria
    "com sucesso" e o banco continuaria vazio. Quem sabe achatar é o
    `db_export.normalizar_item`, que já existia para o caminho de linha de comando."""
    import contextlib
    import io as _io
    import db_export
    try:
        if silencioso:
            with contextlib.redirect_stdout(_io.StringIO()):
                return db_export.exportar(sessao_json, source="search")
        return db_export.exportar(sessao_json, source="search")
    except Exception as e:
        print(f"⚠️  normalização falhou: {e}", flush=True)
        return None


def _normalizar_em_loop(sessao_json: Path, parar: threading.Event) -> None:
    """Renormaliza a cada `NORMALIZA_S` enquanto a busca roda.

    É o que faz o marcador cair no mapa DURANTE o job, e não só no fim: o
    watcher do servidor relê o arquivo achatado a cada passada. Normalizar é
    barato (lê um JSON e escreve outro), então o custo é irrelevante perto dos
    segundos que cada POI leva no Maps."""
    while not parar.wait(NORMALIZA_S):
        _normalizar(sessao_json, silencioso=True)


def _prog_tile(linha: str):
    m = _RE_TILE.search(linha)
    return f"célula {m.group(1)}/{m.group(2)}" if m else None


def _prog_busca(linha: str):
    m = _RE_BUSCA.search(linha)
    return f"POIs {m.group(1)}/{m.group(2)}" if m else None


def main():
    ap = argparse.ArgumentParser(description="Mineração de área por captura + OCR")
    ap.add_argument("--sessao", required=True, help="nome da sessão (pasta em capturas/)")
    ap.add_argument("--area", default=area_utils.AREA_PADRAO,
                    help="nome da área de trabalho no banco")
    ap.add_argument("--zoom", type=int, default=19,
                    help="19 = rua com POIs (padrão), 18 = quadra")
    ap.add_argument("--overlap", type=int, default=10, help="sobreposição entre tiles, %%")
    ap.add_argument("--workers", type=int, default=10, help="workers da BUSCA (estágio 4)")
    ap.add_argument("--capture-workers", type=int, default=10,
                    help="navegadores paralelos na captura (estágio 1)")
    ap.add_argument("--no-proxy", action="store_true", help="busca sem proxy")
    ap.add_argument("--de", type=int, default=1, help="estágio inicial (1 a 4)")
    ap.add_argument("--ate", type=int, default=4, help="estágio final (1 a 4)")
    a = ap.parse_args()

    poly = area_utils.carregar_area(a.area)
    if not poly or len(poly) < 3:
        print(f"❌ Área '{a.area}' não existe ou tem menos de 3 vértices. "
              f"Desenhe a área no mapa antes de minerar.", flush=True)
        return 2

    sdir = CAPTURAS / a.sessao
    sdir.mkdir(parents=True, exist_ok=True)
    sessao_json = sdir / "session.json"
    poly_json = sdir / "_area.json"
    poly_json.write_text(json.dumps(poly), encoding="utf-8")

    s, n, o, l = area_utils.bbox(poly)
    print(f"🗺  Área '{a.area}': {len(poly)} vértices · caixa "
          f"N{n:.5f} S{s:.5f} L{l:.5f} O{o:.5f}", flush=True)
    print(f"📁 Sessão: {sdir}", flush=True)

    if a.de <= 1 <= a.ate:
        env = _env(CAPTURE_WORKERS=str(a.capture_workers))
        cmd = _ts_node() + ["src/capture-cli.ts",
               "--sessao", a.sessao, "--saida", str(sdir),
               "--poly", str(poly_json), "--zoom", str(a.zoom),
               "--overlap", str(a.overlap)]
        print(f"\n{'─' * 62}\n▶ 1/4 captura dos tiles\n{'─' * 62}", flush=True)
        _fase("captura")
        proc = subprocess.Popen(cmd, cwd=str(BASE), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                encoding="utf-8", errors="replace")
        ultimo = None
        for linha in _linhas(proc.stdout):
            print(linha, flush=True)
            t = _prog_tile(linha)
            if t and t != ultimo:
                print(t, flush=True)
                ultimo = t
        if proc.wait() != 0:
            print("❌ captura falhou — os estágios seguintes não têm o que ler.",
                  flush=True)
            return 1

    if not sessao_json.exists():
        print(f"❌ {sessao_json} não existe. A captura não chegou a rodar.", flush=True)
        return 1

    if a.de <= 2 <= a.ate:
        _fase("deteccao")
        # o detector imprime "[   7/264] tile… → 102 ícones": mesma forma da
        # captura, então a mesma tradução serve e a barra continua andando
        if _rodar([PYTHON, "detect_crops.py", str(sessao_json)],
                  "2/4 detecção dos ícones de POI", traduz=_prog_tile) != 0:
            return 1

    if a.de <= 3 <= a.ate:
        _fase("ocr")
        if _rodar([PYTHON, "ocr_pois.py", str(sessao_json)],
                  "3/4 OCR dos nomes", traduz=_prog_tile) != 0:
            return 1

    if a.de <= 4 <= a.ate:
        _fase("busca")
        # --full: além do painel, abre a GALERIA DE FOTOS e a aba de avaliações.
        # Sem ela a mineração trazia só o painel, e o POI nascia sem foto — em
        # Canoas foram 2.738 sem imagem nenhuma, que depois precisaram da fase
        # Maps do enriquecimento para serem reabertos um a um. Pagar a extração
        # completa aqui, com a sessão do Maps já aberta, é muito mais barato.
        cmd = [PYTHON, "search_pois_v2.py", str(sessao_json), "--full",
               "--workers", str(a.workers)]
        if a.no_proxy:
            cmd.append("--no-proxy")
        # sem --ingest de propósito: quem grava no banco é o watcher do servidor,
        # e dois processos no mesmo delete+recreate se atropelam
        parar = threading.Event()
        t = threading.Thread(target=_normalizar_em_loop, args=(sessao_json, parar),
                             daemon=True)
        t.start()
        cod = _rodar(cmd, "4/4 busca de cada nome no Maps", traduz=_prog_busca)
        parar.set()
        t.join(timeout=15)
        if cod != 0:
            return 1

    saida = _normalizar(sessao_json)          # passada final, com tudo pronto
    print(f"\n✅ Mineração concluída. Resultado em {saida}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
