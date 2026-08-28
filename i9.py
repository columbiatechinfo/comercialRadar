# -*- coding: utf-8 -*-
"""i9.py — rodar etapa pesada na máquina certa, com o código certo.

POR QUE A ETAPA PESADA VAI PARA O i9

O notebook é onde o operador trabalha. A captura sobe dez navegadores reais e a
busca no Maps abre uma sessão com proxy por lote — é o que come CPU, memória e
banda enquanto ele tenta usar o sistema.

E há uma razão que não é de conforto: **a API da Webshare não responde do
notebook**. Medido em 26/08/2026 — `urlopen error timed out` de lá, 1,1 s do i9.
Sem ela o pool cai num cache em disco e trabalha às cegas sobre a lista de
ontem.

O CÓDIGO VAI JUNTO, E ISSO NÃO É ZELO — É CICATRIZ

O i9 rodava cópias próprias dos arquivos, sem sincronia nenhuma. Em 25/08 eu
corrigi o vazio do pandas virando a palavra "nan" e limpei 105.146 campos; em
26/08 a importação de Bento Gonçalves gravou 1.408 POIs de nome "nan" outra vez,
porque a máquina que importa não tinha a correção. E o erro não parou no banco:
cinco clubes com piscina distintos foram fundidos num só.

No dia em que escrevi este arquivo, SEIS dos treze arquivos da captura estavam
divergentes no i9 — entre eles o `human_browser.py` sem a flag `--disable-http2`
(sem a qual nenhum acesso ao Google através de proxy completa), o
`search_pois_v2.py` sem o filtro da área desenhada e o `pontos_de_busca.py` sem
a correção que fez o iFood voltar a achar endereços.

Conferir e avisar não bastaria: o aviso chega quando o dado já entrou.

O QUE **NÃO** É ENVIADO

`config.py` e `.env` são legitimamente diferentes — credencial e caminho daquela
máquina. Sobrescrevê-los quebraria o i9. `base_comum.py` vai porque hoje é
idêntico e é o caminho do banco; se um dia precisar divergir, ele sai desta
lista e a razão fica escrita aqui.
"""
from __future__ import annotations

import base64
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent

SSH = os.environ.get("I9_SSH", "orbisgrid@100.115.117.49")
DIR = os.environ.get("I9_DIR", "/home/orbisgrid/comercialradar")

# O Node do i9 mora no espaço do usuário (`~/.local/node`) e NÃO está no PATH de
# shell não interativo. Sem esta linha o `npx` que responde é o do WINDOWS,
# herdado pelo PATH do WSL — ele roda, e roda errado, sobre caminhos Linux.
PREFIXO = 'export PATH="$HOME/.local/node/bin:$PATH" PYTHONUTF8=1 PYTHONIOENCODING=utf-8'

# Tudo que a captura, a busca e o iFood tocam. Um arquivo esquecido aqui é um
# defeito corrigido que volta — foi assim que o "nan" ressuscitou.
#
# E FOI ASSIM DE NOVO EM 27/08/2026, com `config.py`.
#
# O teto de espera do botão "Próximo" subiu de 9 s para 25 s aqui, com medição.
# A run seguinte, no i9, continuou falhando — e a mensagem de erro entregou o
# motivo sem querer: dizia "não pintou em 12s", que é 9.000 + 2.500. O código
# novo tinha chegado; o NÚMERO não. `search_pois_v2.py` estava nesta lista,
# `config.py` não estava.
#
# `config.py` é lido por três dos módulos daqui (a busca, o navegador e o pool
# de proxies) e guarda todo teto, limite e caminho. Sem ele, ajustar constante
# no notebook não muda nada onde o trabalho acontece — e o sintoma é o pior
# possível: o conserto "não funcionou", quando na verdade não chegou.
# O teste `test_o_fecho_das_dependencias_esta_completo` guarda esta lista: ele
# lê os `import` de cada arquivo daqui e exige que todo módulo do projeto que
# eles usam também esteja presente. Foi ele quem encontrou os três últimos —
# `spatial_clustering` (monta os lotes de POIs), `extract_full` (fotos e
# avaliações) e `realtime_ingest` — que rodavam velhos no i9 sem que ninguém
# soubesse. Não confie na memória para manter isto em dia; o teste não esquece.
_PY = [
    "config.py",  # PRIMEIRO de propósito: é o que os outros leem
    "minerar_captura.py", "detect_crops.py", "ocr_pois.py", "search_pois_v2.py",
    "human_browser.py", "proxy_pool.py", "extrair_ifood.py", "enriquecer_ifood.py",
    "pontos_de_busca.py", "area_utils.py", "base_comum.py", "db_export.py",
    "extracao_estadual.py", "evidencia.py",
    "spatial_clustering.py", "extract_full.py", "realtime_ingest.py",
    "auth.py",  # o `realtime_ingest` importa; o fecho é transitivo
    # A ingestao roda no i9 e e ela quem precisa gerar o endereco pela
    # coordenada antes de gravar o POI — sem este arquivo la, o trigger
    # `poi_comparavel` recusaria o ponto que o i9 tentasse inserir.
    #
    # `geocodificar.py` NAO entra, e e escolha: ele arrasta 21 modulos (a pilha
    # de agentes inteira) para atender 5% dos casos. Por isso o import dele em
    # `endereco_reverso` mora DENTRO da funcao e e protegido — no i9 a cascata
    # resolve pelo CNEFE, que cobre 95%, e o residuo fica para quem tem a pilha.
    "endereco_reverso.py",

    # ── AS ETAPAS 5 A 8 PASSARAM A RODAR NO i9 (27/08/2026) ─────────────────
    #
    # Regra do dono do produto: "todos esses passos devem rodar no i9". Só a
    # captura ia para lá; a descoberta por categoria e o iFood abriam navegador
    # com proxy NO NOTEBOOK do operador — seis Chromium disputando a CPU do
    # painel que ele estava olhando —, e a normalização e o cruzamento comiam
    # memória na mesma máquina.
    #
    # E havia um efeito que só aparecia no incidente: com os navegadores aqui,
    # "derrubar os navegadores da mineração" derrubava o Chrome PESSOAL junto.
    #
    # ATENÇÃO AO QUE ESTA LISTA JÁ DEIXOU PASSAR: `segmentar_endereco`,
    # `ajuste_logradouro` e `povoar_vinculo` EXISTIAM no i9 sem estar aqui —
    # cópias antigas que ninguém atualizava. Arquivo que roda lá e não está
    # nesta lista é defeito corrigido que volta, e o sintoma é o pior possível:
    # "o conserto não funcionou".
    "descobrir_maps.py", "enriquecer_por_ifood.py",
    "normalizar_bases.py", "segmentar_endereco.py", "ajuste_logradouro.py",
    "corrigir_coordenada.py", "conferir_municipio.py",
    "povoar_vinculo.py", "cruzar_fontes.py", "julgar_par_banco.py",
]


def _fontes() -> list:
    """A lista de arquivos a sincronizar, com `src/` VARRIDO e não digitado.

    ESTA FUNÇÃO EXISTE POR CAUSA DE UM ERRO MEU, no dia em que escrevi o módulo.
    A lista era digitada à mão e tinha `src/capture-cli.ts` — a porta de entrada
    da linha de comando — mas não `src/capture.ts`, que é quem abre o navegador.

    Corrigi o `--disable-http2` no `capture.ts`, mandei sincronizar, o módulo
    respondeu "os 15 arquivos já estão iguais" e a captura falhou de novo com o
    mesmo erro. O relatório estava certo sobre os arquivos que conhecia; o
    defeito era não conhecer o que importava.

    Varrer a pasta não depende de eu lembrar. Um `.ts` novo entra sozinho.
    """
    arquivos = list(_PY)
    src = BASE / "src"
    if src.is_dir():
        arquivos += sorted(
            str(p.relative_to(BASE)).replace(os.sep, "/")
            for p in src.rglob("*.ts")
        )
    return arquivos


ARQUIVOS = _fontes()


def _ssh(entrada: bytes, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SSH,
         "wsl -d Ubuntu -- bash -s"],
        input=entrada, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout)


def _script(linhas) -> bytes:
    """BYTES e `chr(10)`, nunca `text=True`.

    No Windows o wrapper de texto traduz cada quebra para CRLF, e o bash do
    outro lado recebe o último argumento com um carriage return colado:
    `--aplicar` vira `--aplicar<CR>` e o argparse responde "unrecognized
    arguments: --aplicar", com a flag listada no próprio usage. É o tipo de erro
    que faz perder meia hora procurando no lugar errado, e o projeto já tem essa
    cicatriz (commit 7130d50).
    """
    return chr(10).join(linhas).encode("utf-8") + bytes((10,))


def sincronizar(log=print) -> int:
    """Manda os arquivos que divergem. Devolve quantos foram."""
    import hashlib

    # Primeiro PERGUNTA o que já está igual: mandar 15 arquivos a cada etapa
    # custa segundos de sessão SSH, e o normal é nada ter mudado.
    pedido = [f"cd {shlex.quote(DIR)} || exit 1"]
    for f in ARQUIVOS:
        pedido.append(f'if [ -f {shlex.quote(f)} ]; then '
                      f'echo "{f} $(sha256sum {shlex.quote(f)} | cut -c1-16)"; '
                      f'else echo "{f} AUSENTE"; fi')
    try:
        r = _ssh(_script(pedido), timeout=90)
    except (subprocess.TimeoutExpired, OSError) as erro:
        log(f"  ⚠️  não consegui falar com o i9 ({type(erro).__name__}) — "
            "ele pode estar rodando código antigo")
        return -1

    remoto = {}
    for linha in r.stdout.decode("utf-8", "replace").splitlines():
        p = linha.split()
        if len(p) == 2:
            remoto[p[0]] = p[1]

    enviar = []
    for f in ARQUIVOS:
        local = BASE / f
        if not local.exists():
            continue
        h = hashlib.sha256(local.read_bytes()).hexdigest()[:16]
        if remoto.get(f) != h:
            enviar.append(f)

    if not enviar:
        log(f"  i9: os {len(ARQUIVOS)} arquivos já estão iguais")
        return 0

    for f in enviar:
        dado = (BASE / f).read_bytes()
        destino = shlex.quote(f)
        pasta = os.path.dirname(f)
        linhas = [f"cd {shlex.quote(DIR)} || exit 1"]
        if pasta:
            linhas.append(f"mkdir -p {shlex.quote(pasta)}")
        linhas.append(f"base64 -d > {destino}")
        # A QUEBRA DE LINHA ENTRE O ROTEIRO E O DADO É OBRIGATÓRIA.
        #
        # `_script` já termina em `\n`; sem ela o base64 cola no redirecionamento
        # e o bash lê `base64 -d > arquivoAAAABBBB...` — o nome do arquivo vira o
        # nome mais os dados. O erro que volta é o aviso do ssh, que não tem
        # relação nenhuma com a causa e manda quem investiga para o lado errado.
        try:
            rr = _ssh(_script(linhas) + base64.b64encode(dado) + bytes((10,)),
                      timeout=120)
            if rr.returncode != 0:
                # O ssh escreve avisos (chave pós-quântica) no mesmo canal. Eles
                # não são o erro; mostrá-los como se fossem esconde o erro.
                ruido = ("post-quantum", "store now", "WARNING:", "Warning:")
                msg = " ".join(
                    l for l in rr.stdout.decode("utf-8", "replace").splitlines()
                    if l.strip() and not any(x in l for x in ruido))
                log(f"  ⚠️  falhou ao enviar {f}: {msg[:160] or 'sem detalhe'}")
        except (subprocess.TimeoutExpired, OSError) as erro:
            log(f"  ⚠️  falhou ao enviar {f}: {type(erro).__name__}")

    log(f"  i9: {len(enviar)} arquivo(s) atualizados — {', '.join(enviar)}")
    return len(enviar)


def rodar(argumentos: list, log=print) -> int:
    """Roda um comando Python do projeto NO i9, transmitindo a saída ao vivo."""
    py = DIR + "/.venv/bin/python"
    roteiro = _script([
        f"cd {shlex.quote(DIR)} || exit 1",
        PREFIXO,
        " ".join([shlex.quote(py)] + [shlex.quote(a) for a in argumentos]),
    ])
    p = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SSH,
         "wsl -d Ubuntu -- bash -s"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    p.stdin.write(roteiro)
    p.stdin.close()
    for linha in iter(p.stdout.readline, b""):
        log("    " + linha.decode("utf-8", "replace").rstrip())
    return p.wait()


def trazer(remoto: str, local: Path) -> bool:
    """Copia UM arquivo do i9 para cá. Devolve se veio algo."""
    roteiro = _script([
        f"cd {shlex.quote(DIR)} || exit 1",
        f"[ -f {shlex.quote(remoto)} ] && base64 {shlex.quote(remoto)} || true",
    ])
    try:
        r = _ssh(roteiro, timeout=180)
    except (subprocess.TimeoutExpired, OSError):
        return False
    dado = r.stdout.strip()
    if not dado:
        return False
    try:
        bruto = base64.b64decode(dado)
    except Exception:  # noqa: BLE001 — saída suja é "não veio", não erro fatal
        return False
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(bruto)
    return True


class Espelho(threading.Thread):
    """Traz um arquivo do i9 de tempos em tempos, enquanto a etapa roda lá.

    O MAPA AO VIVO DEPENDE DISTO. Quem grava os POIs no banco durante a
    mineração é o watcher do servidor, e ele observa um ARQUIVO local
    (`crops/<sessao>_db.json`). Com a captura rodando no i9 esse arquivo nasce
    lá, e sem alguém trazê-lo o operador veria a tela parada até o fim — que é
    justamente a hora em que ele quer ver o marcador caindo.

    Não é streaming: é uma cópia a cada `intervalo`. O atraso é de segundos e o
    watcher, que já era idempotente, não sabe a diferença.
    """

    def __init__(self, remoto: str, local: Path, intervalo: float = 12.0):
        super().__init__(daemon=True)
        self.remoto, self.local, self.intervalo = remoto, local, intervalo
        self.parar = threading.Event()
        self.trouxe = 0

    def run(self):
        while not self.parar.is_set():
            if trazer(self.remoto, self.local):
                self.trouxe += 1
            self.parar.wait(self.intervalo)

    def encerrar(self):
        """Para o laço e faz UMA última cópia — a que contém o resultado final."""
        self.parar.set()
        self.join(timeout=self.intervalo + 5)
        if trazer(self.remoto, self.local):
            self.trouxe += 1
