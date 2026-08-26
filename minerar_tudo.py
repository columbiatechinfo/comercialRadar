# -*- coding: utf-8 -*-
"""minerar_tudo.py — a mineração de uma área, com as DUAS fontes gratuitas.

POR QUE ELAS DEIXARAM DE SER ALTERNATIVAS (24/08/2026, decisão do dono do produto)

Até aqui o painel pedia para ESCOLHER um motor, como se as três fossem formas
diferentes de fazer a mesma coisa. Não são — e a Places API, que era a terceira,
saiu por cobrar por chamada o que as outras trazem de graça.

As duas que ficaram enxergam coisas distintas, e é por isso que agora rodam
juntas:

    BASES PÚBLICAS   o que Overture, OpenStreetMap e Foursquare já sabem da UF.
                     Minutos, alcance grande, sem foto e sem "está aberto hoje".
    CAPTURA + OCR    o que o Google Maps mostra AGORA naquela rua: fotografa em
                     tiles, detecta os ícones, lê os nomes e busca cada um.
                     Horas, e é a única que traz painel e foto.

Uma sozinha deixa buraco nos dois sentidos: a base pública não sabe do comércio
que abriu mês passado, e a captura não vê o que não tem marcador no mapa.

A ORDEM É BASES PÚBLICAS PRIMEIRO, e ela foi escolhida

O ponto já existe quando a captura começa, então a deduplicação por
nome + coordenada trabalha a favor em vez de contra, e a tela mostra resultado
em minutos em vez de depois das horas de captura. Se a captura viesse primeiro,
a rodada inteira ficaria muda até ela terminar.

O DATASET DA UF

A skill `extracao-poi-estadual` trabalha por UF INTEIRA, não por município: são
383 mil POIs no RS. O resultado fica em `dados_externos/estadual/<UF>` e é
REAPROVEITADO por toda mineração seguinte naquela UF — só a primeira paga o
custo de produzi-lo.

`extracao_estadual.py` então importa dali **só o município da área**. Despejar a
UF inteira na base de um cliente que trabalha uma cidade não é cobertura, é
entulho.

USO
    python minerar_tudo.py --area area_atual --sessao canoas_centro \\
        --zoom 19 --workers 10 --capture-workers 10 --empresa "Aegea - Corsan"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import config  # noqa: F401  (.env + UTF-8)
import i9
import i9_windows
import area_utils

BASE = Path(__file__).resolve().parent
PYTHON = sys.executable
SKILL = BASE / "skills" / "extracao-poi-estadual"
DATASETS = BASE / "dados_externos" / "estadual"
# Onde o watcher do servidor procura o resultado da captura. Com a etapa
# rodando no i9 o arquivo nasce la e o `i9.Espelho` o traz para ca.
CAPTURAS = BASE / "capturas"

# Escrito pelo próprio orquestrador quando a skill termina inteira. A skill tem
# `status` próprio e é retomável, mas ela responde "em que etapa estou", não "o
# dataset está utilizável". São perguntas diferentes: uma execução interrompida
# na etapa 6 de 8 tem pasta, tem arquivos e não serve para importar.
MARCADOR = "_pronto.txt"


def _log(msg: str) -> None:
    """Uma linha por vez, sem buffer: é isto que aparece no painel ao vivo."""
    print(msg, flush=True)


def _rodar(cmd: list, cwd: Path | None = None) -> int:
    """Roda repassando stdout LINHA A LINHA.

    `capture_output` juntaria tudo e só devolveria no fim — a captura leva horas
    e o painel ficaria mudo o tempo todo, que é exatamente o que faz alguém
    achar que travou e matar a rodada.
    """
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1")
    p = subprocess.Popen(cmd, cwd=str(cwd or BASE), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    for linha in p.stdout:
        print(linha.rstrip(), flush=True)
    return p.wait()


def _fontes_disponiveis() -> tuple[str, list]:
    """O que a máquina de fato consegue puxar hoje, e o que falta para o resto.

    Declarar `overture,osm,fsq` e deixar duas falharem produziria um dataset
    OSM-only com nome de "bases públicas" — cobertura menor, sem ninguém saber.
    Aqui a lista é montada pelo que existe, e o que ficou de fora é DITO.
    """
    fontes, faltando = ["osm"], []

    from shutil import which
    if which("overturemaps"):
        fontes.insert(0, "overture")
    else:
        faltando.append(("overture", "CLI `overturemaps` fora do PATH — "
                                     "`pip install overturemaps` resolve"))

    if (os.environ.get("HF_TOKEN") or "").strip():
        fontes.append("fsq")
    else:
        faltando.append(("fsq", "HF_TOKEN ausente no .env — o Foursquare vem do "
                                "Hugging Face e exige token"))
    return ",".join(fontes), faltando


def garantir_dataset(uf: str, produzir_aqui: bool = False) -> Path:
    """Devolve a pasta do dataset da UF. NÃO a produz nesta máquina por padrão.

    POR QUE NÃO PRODUZ AQUI

    Produzir a UF é o trabalho pesado do processo: DuckDB sobre o Overture no S3
    mais o PBF do OpenStreetMap, para um estado inteiro — 383 mil POIs no RS.
    Num notebook isso disputa CPU e disco com os dez Chromiums da captura, e
    leva horas antes de a captura sequer começar.

    Esse é o tipo de trabalho que mora no i9, ao lado do OSRM e do Photon. A
    máquina tem 16 CPUs, 94 GB de RAM e 682 GB livres, e o código já está
    publicado lá (`scripts/i9/publicar.sh`).

    Então aqui a regra é: se o dataset existe, usa; se não existe, **para e diz
    o comando que o produz** — em vez de começar sozinho uma tarefa de horas que
    ninguém pediu. `--produzir-bases` força a produção local, para quando não há
    i9 à mão.
    """
    destino = DATASETS / uf.upper()
    # A MESMA verdade do diagnóstico, e não uma segunda conferência.
    #
    # Antes esta função olhava só o disco local. No notebook isso dava "não há
    # dataset de RS" com o RS pronto no i9 — e a saída sugeria PRODUZIR de novo
    # o que já existia: horas de CPU para refazer 10 GB.
    pronto, onde = dataset_pronto(uf)
    if pronto:
        _log(f"📚 Bases públicas: dataset de {uf.upper()} já existe ({onde})")
        _log(f"   {destino}")
        return destino

    if not produzir_aqui:
        raise SystemExit(
            f"\n❌ Não há dataset de {uf.upper()} em {destino}.\n\n"
            f"   Produzir é trabalho de HORAS (a UF inteira: Overture + OSM +\n"
            f"   Foursquare) e não roda aqui de propósito — é tarefa do i9.\n\n"
            f"   No i9, uma vez por UF:\n\n"
            f"     ssh orbisgrid@100.115.117.49 \"wsl -d Ubuntu -- bash -lc \\\n"
            f"       'cd /home/orbisgrid/comercialradar && \\\n"
            f"        ./scripts/i9/dataset_estadual.sh {uf.upper()}'\"\n\n"
            f"   Depois traga a pasta com:\n\n"
            f"     bash scripts/i9/dataset_estadual.sh --baixar {uf.upper()}\n\n"
            f"   Para minerar SÓ com a captura enquanto isso: --pular-bases.\n"
            f"   Para produzir aqui mesmo, sabendo do custo: --produzir-bases.")

    destino.mkdir(parents=True, exist_ok=True)
    fontes, faltando = _fontes_disponiveis()
    _log("─" * 62)
    _log(f"▶ 1/7 bases públicas — produzindo o dataset de {uf.upper()}")
    _log("─" * 62)
    _log(f"🌐 Fontes: {fontes}")
    for nome, motivo in faltando:
        _log(f"⚠️  SEM {nome}: {motivo}")
    _log("⏳ A UF inteira. É demorado, e acontece UMA VEZ por UF — as próximas")
    _log("   minerações nesta UF reaproveitam esta pasta.")

    inicio = time.time()
    rc = _rodar([PYTHON, "poi_estadual.py", "run",
                 "--uf", uf.upper(),
                 "--fontes", fontes,
                 "--formatos", "csv,geoparquet",
                 "--base-dir", str(destino)], cwd=SKILL)
    if rc != 0:
        raise SystemExit(
            f"❌ A extração estadual de {uf.upper()} falhou (código {rc}).\n"
            f"   A pasta {destino} guarda o progresso: rodar de novo CONTINUA de\n"
            f"   onde parou, não recomeça. Para inspecionar:\n"
            f"   cd {SKILL} && python poi_estadual.py status --uf {uf.upper()} "
            f"--base-dir {destino}")

    (destino / MARCADOR).write_text(
        f"extracao-poi-estadual · uf={uf.upper()} · fontes={fontes}\n"
        f"minutos={int((time.time() - inicio) / 60)}\n",
        encoding="utf-8")
    _log(f"✅ Dataset de {uf.upper()} pronto em {int((time.time()-inicio)/60)} min")
    return destino


# ONDE O DADO MORA, e por que perguntar no lugar errado dava resposta errada.
#
# O banco ja aponta para o i9 pelo `.env` (`I9_POSTGRES_HOST`). Os datasets
# estaduais, nao: sao ARQUIVOS, e vivem no disco de la — 10 GB so o RS. Rodando
# no notebook, `dados_externos/estadual/RS` nao existe, e a conferencia dizia
# "sem dataset de RS" com o RS pronto no i9 ha uma hora.
#
# Erro bobo e caro: leva a produzir de novo o que ja existe, ou a concluir que a
# etapa foi pulada por falta de dado quando o dado esta la.
#
# A regra passa a ser uma so: quem pergunta pelo dataset pergunta ONDE ELE MORA.
I9_SSH = os.environ.get("I9_SSH", "orbisgrid@100.115.117.49")
I9_DIR = os.environ.get("I9_DIR", "/home/orbisgrid/comercialradar")


def no_i9() -> bool:
    """Estamos rodando NA maquina onde o dado mora?"""
    return str(BASE).replace("\\", "/").startswith(I9_DIR)


def dataset_pronto(uf: str) -> tuple:
    """`(pronto, onde)` — confere no disco local e, se nao achar, NO i9.

    Devolve tambem ONDE a resposta foi obtida, porque "nao existe aqui" e "nao
    existe em lugar nenhum" sao conclusoes diferentes e so uma delas justifica
    produzir a UF de novo.
    """
    uf = (uf or "").upper()
    if not uf:
        return False, "sem UF"
    if (DATASETS / uf / MARCADOR).exists():
        return True, "disco local"
    if no_i9():
        return False, "disco do i9 (rodando nele)"

    # Uma pergunta so, curta, e que NAO derruba nada se o i9 estiver fora: sem
    # resposta, seguimos com o que sabemos do disco local.
    try:
        r = subprocess.run(
            ["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", I9_SSH,
             f"wsl -d Ubuntu -- bash -lc 'test -f {I9_DIR}/dados_externos/"
             f"estadual/{uf}/{MARCADOR} && echo PRONTO'"],
            capture_output=True, text=True, timeout=25)
        if "PRONTO" in (r.stdout or ""):
            return True, "i9"
    except Exception as erro:  # noqa: BLE001
        return False, f"nao consegui perguntar ao i9 ({type(erro).__name__})"
    return False, "nem aqui nem no i9"


CADASTUR = Path(os.environ.get("CADASTUR_SAIDA")
                or (BASE / "dados_externos" / "cadastur"))


def cadastur_baixado() -> bool:
    """O snapshot nacional do MTur ja esta em disco?

    A base e uma so para o pais inteiro e nao muda de dia para dia — por isso
    ela se baixa UMA VEZ e vale para toda mineracao seguinte, como o dataset
    estadual. Aqui so se PERGUNTA; quem baixa e um comando explicito.
    """
    try:
        return any(CADASTUR.glob("*.parquet")) or any(CADASTUR.rglob("*.parquet"))
    except OSError:
        return False


def _diagnostico(uf: str, cod: str, cidade: str, empresa: str,
                 pular: dict) -> dict:
    """O QUE VAI RODAR NESTA AREA, dito ANTES de gastar a primeira hora.

    A mineracao leva horas e cada etapa depende de um dado que pode nao existir
    para aquela UF. Descobrir no meio — ou pior, no fim — que o Cadastur foi
    pulado porque o municipio nao esta na malha e trabalho perdido duas vezes:
    a rodada e a confianca de quem olhou o resultado achando que era completo.

    Aqui nao se conserta nada: so se mede e se diz. Quem decide se roda assim ou
    prepara o que falta e a pessoa.
    """
    import base_comum as bc

    tem_dataset, onde_dataset = dataset_pronto(uf)

    tem_cnefe = False
    if cod:
        try:
            ref = bc.conectar_referencia()
            try:
                with ref.cursor() as cur:
                    cur.execute("select exists (select 1 from ibge_cnefe "
                                "where cod_municipio = %s limit 1)", (str(cod),))
                    tem_cnefe = bool(cur.fetchone()[0])
            finally:
                ref.close()
        except Exception as erro:  # noqa: BLE001
            _log(f"  (nao consegui conferir o CNEFE: {type(erro).__name__})")

    etapas = [
        (1, "bases publicas (Overture/OSM/FSQ)", tem_dataset and not pular["bases"],
         (f"dataset em: {onde_dataset}" if tem_dataset
          else f"sem dataset de {uf} — procurei em: {onde_dataset}")),
        (2, "importar o municipio", tem_dataset and bool(cod) and not pular["bases"],
         "" if cod else "municipio fora da malha IBGE carregada"),
        (3, "Cadastur/MTur",
         bool(cidade and uf) and cadastur_baixado() and not pular["cadastur"],
         "" if not (cidade and uf) else
         ("" if cadastur_baixado() else
          "snapshot do MTur ainda nao baixado (uma vez so, ver o log)")),
        (4, "captura + OCR do Maps", True, ""),
        (5, "descoberta por categoria no Maps", not pular["descoberta"], ""),
        (6, "iFood", tem_cnefe and not pular["ifood"],
         "" if tem_cnefe else "sem CNEFE do municipio: nao ha endereco-semente"),
        (7, "enderecos (IA + skill)", bool(cod),
         "" if cod else "precisa do codigo IBGE do municipio"),
        (8, "cruzamento entre as fontes", bool(empresa),
         "" if empresa else "informe --empresa: o vinculo carimba a dona do dado"),
    ]

    _log("")
    _log("=" * 62)
    _log(f" O QUE RODA EM {cidade or '?'}/{uf or '?'}"
         + (f" ({cod})" if cod else ""))
    # NESTA maquina. O dataset da UF mora onde a mineracao roda, e no notebook
    # ele nao esta — dizer so "nao ha dataset de RS" faria parecer que a UF nao
    # foi produzida, quando ela pode estar pronta no i9.
    _log(f" banco: i9 · datasets: {'i9' if not no_i9() else 'esta maquina'}")
    _log("=" * 62)
    for n, nome, vai, motivo in etapas:
        marca = "SIM " if vai else "NAO "
        _log(f"  {n}  {marca} {nome}" + (f"  — {motivo}" if motivo else ""))
    fora = [n for n, _, vai, _ in etapas if not vai]
    if fora:
        _log("")
        _log(f"  {len(fora)} etapa(s) de fora. A rodada CONTINUA com as demais —")
        _log("  cada uma pode ser repetida sozinha quando o que falta existir.")
    _log("=" * 62)
    return {n: vai for n, _, vai, _ in etapas}


def _importar_municipio(uf: str, cod: str, empresa: str,
                        produzir_aqui: bool = False) -> int:
    """O recorte do município, do dataset da UF para o banco.

    A UF INTEIRA NÃO ENTRA. São 945.716 POIs no RS: despejar isso na base de um
    cliente que trabalha uma cidade não é cobertura, é entulho — a tela fica
    lenta, a fila de aprovação enche de ponto que ninguém pediu e o custo de
    enriquecer sobe para todos eles.

    DATASET AUSENTE NÃO DERRUBA A RODADA, e isso mudou em 25/08/2026.
    `garantir_dataset` levanta `SystemExit` quando a UF não foi produzida — o
    que fazia sentido quando a mineração era só bases públicas + captura. Agora
    são sete etapas: abortar aqui levaria junto a captura, o iFood, os endereços
    e o cruzamento, que funcionam sem dataset nenhum. Hoje ele DIZ o que falta e
    devolve código; quem decide o que fazer com isso é o orquestrador.
    """
    pronto, onde = dataset_pronto(uf)

    # O DATASET SO EXISTE NO i9? ENTAO A IMPORTACAO RODA LA.
    #
    # Defeito que eu mesmo criei hoje: fiz a CONFERENCIA perguntar ao i9 e deixei
    # o CAMINHO local. `dataset_pronto` dizia "pronto (i9)", `garantir_dataset`
    # devolvia `dados_externos/estadual/RS` — que no notebook nao existe — e o
    # `extracao_estadual` morria com "nao achei poi_padronizado_*". Calado,
    # porque a etapa e tolerante: a base estadual simplesmente nao entrava.
    #
    # Rodar la e o desenho certo por dois motivos, e nao so por conveniencia: o
    # arquivo tem 10 GB e o BANCO tambem esta no i9. Trazer o dataset para o
    # notebook so para reenviar o recorte de um municipio de volta seria
    # atravessar a rede duas vezes a toa.
    if pronto and onde == "i9" and not no_i9():
        return _importar_no_i9(uf, cod, empresa)

    try:
        destino = garantir_dataset(uf, produzir_aqui)
    except SystemExit as aviso:
        _log(str(aviso))
        return 2

    return _rodar([PYTHON, "extracao_estadual.py",
                   "--saida", str(destino / "saida"),
                   "--municipio", str(cod),
                   "--empresa", empresa or "",
                   "--aplicar"])


def _importar_no_i9(uf: str, cod: str, empresa: str) -> int:
    """A importacao do municipio, executada na maquina onde o dado esta.

    O COMANDO VAI POR ARQUIVO, pela entrada padrao. Entre este notebook e o bash
    do i9 ha tres camadas — ssh, PowerShell e `wsl -- bash` — e cada uma
    reinterpreta aspas. `--empresa "Aegea - Corsan"` tem espaco E hifen: montado
    na linha de comando, chega do outro lado partido em tres argumentos. Com
    `bash -s` lendo da entrada, a linha de comando remota e so "bash -s" e nao ha
    o que as camadas comam.
    """
    import shlex

    # `chr(10)` no lugar de uma barra-n: este arquivo ja foi corrompido duas
    # vezes hoje por escape comido na edicao, e o sintoma e sempre um erro de
    # sintaxe longe da causa. Onde da para nao ter barra, nao tem.
    py = I9_DIR + "/.venv/bin/python"
    roteiro = chr(10).join([
        "cd " + shlex.quote(I9_DIR) + " || exit 1",
        "export PYTHONUTF8=1 PYTHONIOENCODING=utf-8",
        " ".join([shlex.quote(py), "extracao_estadual.py",
                  "--saida",
                  shlex.quote("dados_externos/estadual/" + uf.upper() + "/saida"),
                  "--municipio", shlex.quote(str(cod)),
                  "--empresa", shlex.quote(empresa or ""),
                  "--aplicar"]),
    ])
    # O CODIGO VAI JUNTO, TODA VEZ.
    #
    # O i9 rodava uma COPIA propria de `extracao_estadual.py`, sem sincronia
    # nenhuma com o repositorio, e isso trouxe um defeito de volta depois de
    # corrigido. Em 25/08 arrumei o vazio do pandas virando a palavra "nan" e
    # limpei 105.146 campos; em 26/08 a importacao de Bento Goncalves gravou
    # 1.408 POIs de nome "nan" outra vez, porque a maquina que importa nao tinha
    # a correcao. O erro nao parou no banco: quatro clubes com piscina distintos,
    # a ate 119 m um do outro, foram FUNDIDOS num so por "nomes iguais (100%)".
    #
    # Conferir a versao e avisar nao bastaria: o aviso chega quando o dado ja
    # entrou. Mandar o arquivo antes de cada execucao elimina a classe inteira,
    # e custa os 40 KB que ja viajam por esta mesma conexao.
    #
    # `config.py` e `base_comum.py` NAO vao: o primeiro e legitimamente
    # diferente (credencial e caminho daquela maquina) e o segundo ja esta
    # igual. Sobrescreve-los quebraria o i9.
    import base64

    fonte = (BASE / "extracao_estadual.py").read_bytes()
    envio = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", I9_SSH,
         "wsl -d Ubuntu -- bash -s"],
        input=("cd " + shlex.quote(I9_DIR) + " || exit 1" + chr(10)
               + "base64 -d > extracao_estadual.py" + chr(10)).encode("utf-8")
              + base64.b64encode(fonte) + bytes((10,)),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if envio.returncode != 0:
        _log("  ⚠️  nao consegui atualizar o extracao_estadual.py do i9 — "
             "ele pode estar rodando codigo antigo:")
        _log("      " + envio.stdout.decode("utf-8", "replace")[:200])

    _log(f"  o dataset esta no i9 — importando {cod} la, junto do banco")
    # BYTES, e nao `text=True`. No Windows o wrapper de texto traduz cada
    # quebra de linha para CRLF, e o bash do outro lado recebe o ultimo
    # argumento com um carriage return colado: `--aplicar` virou
    # `--aplicar<CR>` e o argparse respondeu 'unrecognized arguments:
    # --aplicar' — com a flag listada no proprio usage, que e o tipo de erro
    # que faz perder meia hora procurando no lugar errado.
    #
    # E a mesma cicatriz que o projeto ja tinha: o CR do Windows quebrando
    # script publicado no Linux (commit 7130d50).
    p = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", I9_SSH,
         "wsl -d Ubuntu -- bash -s"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    saida, _ = p.communicate(roteiro.encode("utf-8") + bytes((10,)))
    for linha in saida.decode("utf-8", "replace").splitlines():
        print("    " + linha.rstrip(), flush=True)
    return p.returncode


def _etapa(n: int, titulo: str) -> None:
    _log("")
    _log("─" * 62)
    _log(f"▶ {n}/8 {titulo}")
    _log("─" * 62)


def _tolerante(cmd: list, nome: str) -> int:
    """Roda uma etapa que NÃO pode derrubar a rodada.

    A mineração é um processo longo e caro — horas de captura. Uma etapa que
    falha (o Cadastur fora do ar, o iFood recusando, a Spark reiniciando) não
    pode custar o que já foi feito nem o que ainda vem: cada uma é independente
    e retomável sozinha.

    O que ela NÃO faz é silenciar: a falha aparece com nome e código, e a linha
    seguinte diz que o resto continua.
    """
    rc = _rodar(cmd)
    if rc != 0:
        _log(f"⚠️  {nome} falhou (código {rc}). As demais etapas continuam;")
        _log(f"   esta pode ser repetida sozinha depois.")
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--sessao", required=True)
    p.add_argument("--zoom", type=int, default=19)
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--capture-workers", dest="capture_workers", type=int, default=10)
    p.add_argument("--no-proxy", dest="no_proxy", action="store_true")
    p.add_argument("--empresa", default="", help="nome da empresa dona do dado")
    p.add_argument("--pular-bases", dest="pular_bases", action="store_true",
                   help="só a captura. Existe para depurar a captura, não para "
                        "uso normal: as duas fontes são o processo.")
    p.add_argument("--pular-cadastur", dest="pular_cadastur", action="store_true",
                   help="pula o Cadastur/MTur desta rodada")
    p.add_argument("--pular-ifood", dest="pular_ifood", action="store_true",
                   help="pula a descoberta do iFood desta rodada")
    p.add_argument("--pular-descoberta", dest="pular_descoberta",
                   action="store_true",
                   help="não varre as categorias do Maps nesta rodada")
    p.add_argument("--produzir-bases", dest="produzir_bases", action="store_true",
                   help="produz o dataset da UF NESTA máquina. São horas e ela "
                        "disputa CPU com a captura — o lugar disso é o i9.")
    a = p.parse_args(argv)

    poly = area_utils.carregar_area(a.area)
    if not poly:
        _log(f"❌ Área '{a.area}' não existe no banco. Desenhe a área no mapa antes.")
        return 2
    cidade, uf = area_utils.municipio_da_area(poly)
    _log(f"🗺  Área '{a.area}': {len(poly)} vértices · {cidade or '?'}/{uf or '?'}")

    cod_previa = _cod_municipio(cidade, uf) if uf and cidade else ""
    _diagnostico(uf, cod_previa, cidade, a.empresa,
                 {"bases": a.pular_bases, "cadastur": a.pular_cadastur,
                  "ifood": a.pular_ifood, "descoberta": a.pular_descoberta})

    # O CÓDIGO DO MUNICÍPIO NÃO DEPENDE DAS BASES PÚBLICAS.
    #
    # Ele nascia dentro do `else` lá embaixo, e com isso `--pular-bases` (ou uma
    # área cuja UF não se identifica) deixava `cod` sem valor — a etapa 6
    # estourava `UnboundLocalError: local variable 'cod'`, DEPOIS de a captura
    # já ter rodado. Horas de trabalho perdidas por uma variável que já estava
    # calculada vinte linhas acima, em `cod_previa`, para o diagnóstico.
    cod = cod_previa

    # ── 1 e 2 · bases públicas ────────────────────────────────────────────
    if a.pular_bases:
        _etapa(1, "bases públicas — PULADAS por --pular-bases (depuração)")
    elif not uf:
        _log("⚠️  Não identifiquei a UF da área — as bases públicas trabalham por")
        _log("   UF e por município, então esta etapa fica de fora desta rodada.")
    else:
        cod = _cod_municipio(cidade, uf)
        if not cod:
            _log(f"⚠️  Não achei o código IBGE de {cidade}/{uf} na malha — a")
            _log("   importação por município fica de fora desta rodada.")
        else:
            _etapa(2, f"bases públicas — importando {cidade}/{uf} ({cod})")
            rc = _importar_municipio(uf, cod, a.empresa,
                                     produzir_aqui=a.produzir_bases)
            if rc != 0:
                # NÃO derruba a rodada: o dataset está no disco e a importação
                # pode ser repetida sozinha depois. Perder as horas de captura
                # por causa disto seria trocar um problema pequeno por um caro.
                _log(f"⚠️  A importação do município falhou (código {rc}). O dataset")
                _log("   ficou no disco; dá para repetir só esta etapa depois.")

    # ── 3 · Cadastur/MTur ─────────────────────────────────────────────────
    _etapa(3, "Cadastur/MTur — o que o Estado registrou")
    if not (cidade and uf):
        _log("  pulado — sem município/UF da área")
    elif a.pular_cadastur:
        _log("  pulado por --pular-cadastur")
    elif not cadastur_baixado():
        # A BASE NAO SE BAIXA AQUI. Mesma regra da base estadual: produz uma
        # vez, consulta sempre, atualiza quando alguem MANDA.
        #
        # A primeira versao desta etapa chamava o Cadastur sem `--so-carregar`,
        # e ele baixava os 26 recursos NACIONAIS a cada mineracao de area —
        # minerar tres bairros da mesma cidade no mesmo dia baixaria a base
        # federal tres vezes. O recorte por municipio acontece DEPOIS do
        # download, entao o custo nao diminui com a area.
        _log(f"  ⏭  o snapshot do Cadastur nao esta em {CADASTUR}.")
        _log("     Ele e baixado UMA VEZ e reaproveitado por toda mineracao:")
        _log("")
        _log("       python cadastur.py --uf %s --gerar" % uf)
        _log("")
        _log("     Depois disso, so quando voce mandar atualizar.")
    else:
        # `--municipio` recebe NOME, nao codigo IBGE — e nao existe `--empresa`
        # nele: o tenant vem da sessao. A primeira versao passava o codigo e uma
        # flag inventada, e a rodada de Cachoeirinha respondeu
        # `unrecognized arguments: --empresa`.
        #
        # `--so-carregar` NAO baixa: le o snapshot que ja esta em disco.
        # `--gerar` e o que faz a fonte virar POI — sem ele a etapa "roda" sem
        # acrescentar ponto nenhum, que e o pior tipo de sucesso.
        _tolerante([PYTHON, "cadastur.py", "--uf", uf, "--municipio", cidade,
                    "--so-carregar", "--gerar"], "Cadastur")

    # ── 4 · captura + OCR ─────────────────────────────────────────────────
    #
    # RODA NO i9, e por dois motivos independentes.
    #
    # O primeiro é de carga: a captura sobe dez navegadores reais e a busca abre
    # uma sessão com proxy por lote. É o que come CPU, memória e banda do
    # notebook enquanto o operador tenta usar o sistema.
    #
    # O segundo decide a questão: **a API da Webshare não responde do
    # notebook**. Medido em 26/08/2026 — `urlopen error timed out` de lá, 1,1 s
    # do i9. Sem ela o pool cai num cache em disco e trabalha às cegas sobre a
    # lista de ontem, que foi o que aconteceu na run de Bento Gonçalves.
    _etapa(4, "captura + OCR do Maps — a única que traz painel e foto")
    i9.sincronizar(_log)

    cmd = ["minerar_captura.py", "--area", a.area, "--sessao", a.sessao,
           "--zoom", str(a.zoom), "--workers", str(a.workers),
           "--capture-workers", str(a.capture_workers)]
    if a.no_proxy:
        cmd.append("--no-proxy")

    # O MAPA AO VIVO PRECISA DO ARQUIVO AQUI.
    #
    # Quem grava os POIs durante a mineração é o watcher do servidor, e ele
    # observa `crops/<sessao>_db.json` LOCAL. Com a captura rodando lá o arquivo
    # nasce lá; sem alguém trazê-lo o operador veria a tela parada até o fim —
    # justamente a hora em que ele quer ver o marcador caindo.
    relativo = f"capturas/{a.sessao}/crops/{a.sessao}_db.json"
    espelho = i9.Espelho(relativo, CAPTURAS / a.sessao / "crops" / f"{a.sessao}_db.json")
    espelho.start()
    try:
        rc_captura = i9.rodar(cmd, _log)
    finally:
        espelho.encerrar()
        _log(f"  (o resultado veio do i9 {espelho.trouxe}x durante a captura)")

    if rc_captura != 0:
        _log(f"⚠️  A captura terminou com código {rc_captura}. As etapas de")
        _log("   endereço e cruzamento seguem sobre o que já entrou.")

    # ── 5 · descoberta por categoria no Maps ──────────────────────────────
    #
    # A SUBSTITUTA DA DESCOBERTA DO iFOOD, e ela nasceu de uma perda.
    #
    # Até 25/08 quem enumerava comércio por ramo era o iFood. Em 26/08 essa
    # metade morreu por fora: Turnstile interativo que recusa até clique humano
    # quando o navegador é dirigido por automação, API de listagem 404/403, app
    # blindado contra emulador. Só o detalhe (`/extra`) sobreviveu.
    #
    # O Google Maps faz a mesma pergunta por outro caminho, com o navegador que
    # a captura já usa: cada busca de categoria devolve a lista daquele ramo com
    # o nome ESCRITO pelo Google e a coordenada real. Sem OCR no meio — e é aí
    # que ela se separa da etapa 4.
    #
    # POR QUE DEPOIS DA CAPTURA, E NÃO ANTES. As duas descobrem, e a dedup é por
    # nome + coordenada. Rodando depois, a captura já pôs no banco o que ela viu,
    # e aqui só entra o que É DIFERENTE — em vez de as duas competirem para
    # gravar o mesmo ponto e uma delas perder por milímetros de coordenada.
    #
    # MEDIDO em Bento Gonçalves: 6 estabelecimentos na área, 3 deles novos
    # (Famiglia Volpini, In Piazza di Bento, Dolce Gusto) que nem a captura nem
    # as bases públicas tinham.
    #
    # PROXY É O PADRÃO, a pedido do dono do produto: varrer dezenas de
    # categorias seguidas pelo IP do operador faria o Google pedir CAPTCHA no
    # navegador PESSOAL dele.
    _etapa(5, "descoberta por categoria no Maps — o que os botões de ramo mostram")
    if a.pular_descoberta:
        _log("  pulado por --pular-descoberta")
    else:
        _tolerante([PYTHON, "descobrir_maps.py", "--area", a.area,
                    "--empresa", a.empresa, "--aplicar"],
                   "descoberta por categoria")

    # ── 6 · iFood ─────────────────────────────────────────────────────────
    #
    # Sob demanda, como a captura: o iFood não tem base pública por UF, e a
    # metade cara (enumerar os ids) precisa de navegador na praça daquela área.
    #
    # RODA NO WINDOWS DO i9 — e hoje está BLOQUEADA POR FORA.
    #
    # Duas coisas separadas, e eu já as confundi uma vez neste arquivo.
    #
    # ONDE RODA. O i9 é uma máquina com dois ambientes: o WSL Linux, onde moram
    # banco, datasets e a captura, e o Windows, que tem desktop. O iFood abre
    # navegador, e navegador visível não sobe no WSL — medido em 26/08/2026,
    # cinco variantes (sem `WAYLAND_DISPLAY`, `--ozone-platform=x11`,
    # `XDG_RUNTIME_DIR` do WSLg e as combinações), todas estourando o launch em
    # ~46 s. No Windows do i9 sobe em 1,1 s, e o banco responde de lá. Então a
    # etapa vai para o Windows do i9, não para o notebook: o notebook é onde o
    # operador trabalha.
    #
    # POR QUE ESTÁ FALHANDO. Não é a máquina, e escrever que era foi erro meu.
    # Em 25/08 esta etapa gravou 1.598 lojas; em 26/08 devolve zero — no
    # notebook E no i9, com proxy E sem proxy. O que mudou foi o iFood: o
    # Cloudflare passou de desafio automático para **Turnstile interativo**
    # ("Confirme que é humano"), e navegador automatizado não clica.
    #
    # O QUE JÁ NÃO DEPENDE DISTO: a metade que traz CNPJ. O endpoint
    # `marketplace.ifood.com.br/v1/merchants/{id}/extra` responde 200 sem
    # navegador nenhum (medido: 1.598 respostas, zero falhas, CNPJ em 99,7%).
    # Preso está só enumerar os ids de uma área — e para isso o iFood não expõe
    # rota pública: `/v1/page/home` existe e devolve 403, todo o resto 404, e o
    # sitemap não lista loja.
    #
    # A etapa é tolerante de propósito: ela falha, diz por quê, e a mineração
    # segue. Perder o iFood custa CNPJ, não custa a rodada.
    _etapa(6, "iFood — a descoberta que traz CNPJ em 99,7% das lojas")
    if a.pular_ifood:
        _log("  pulado por --pular-ifood")
    else:
        rc = i9_windows.rodar_no_windows(["extrair_ifood.py", "--area", a.area],
                                         log=_log)
        if rc != 0:
            _log(f"⚠️  iFood falhou (código {rc}).")
            _log("   Se a saída acima disser 'nenhuma loja', é o Turnstile do")
            _log("   Cloudflare — bloqueio externo, não defeito daqui. As demais")
            _log("   etapas continuam; esta pode ser repetida sozinha depois.")

    # ── 7 · endereços ─────────────────────────────────────────────────────
    #
    # A NORMALIZAÇÃO VEM DEPOIS DE TUDO QUE GRAVA POI, e não antes.
    #
    # Ela lê a coluna `endereco` de quem já está no banco. Rodá-la no meio faria
    # a captura e o iFood entrarem depois e ficarem de fora — e o cruzamento do
    # passo 7, que depende do logradouro canônico, cruzaria menos sem que
    # ninguém entendesse por quê.
    _etapa(7, "endereços — a IA lê o que está grudado, a skill prova a forma")
    if cod:
        # A ÁREA VAI JUNTO, SEMPRE — e é ela que decide o tamanho do trabalho.
        #
        # Sem o recorte, desenhar 1,5 ha mandava a IA da Spark ler os 7.223
        # endereços distintos de Cachoeirinha (360 lotes) e a skill normalizar
        # as 69.150 linhas do CNEFE do município. Com ele: 7 endereços e 262
        # linhas.
        #
        # NÃO EXISTE "sem área" para desligar isto, e não precisa existir:
        # escolher o município no painel GRAVA a divisa dele como área de
        # trabalho (`/api/area/municipio` escreve no mesmo `area_atual` que o
        # desenho manual usa). Então o polígono já diz a verdade nos dois casos
        # — quando é o município, a caixa cobre o município e o recorte não tira
        # nada. Ramificar aqui por "é área ou é município?" seria inventar uma
        # distinção que o dado não faz.
        _tolerante([PYTHON, "segmentar_endereco.py", "--municipio", cod,
                    "--area", a.area, "--aplicar"], "segmentação de endereço")
        _tolerante([PYTHON, "ajuste_logradouro.py", "--municipio", cod,
                    "--area", a.area, "--aplicar"], "ajuste de logradouro")
    else:
        _log("  pulado — sem código IBGE do município")

    # ── 8 · cruzamento ────────────────────────────────────────────────────
    _etapa(8, "cruzamento — quem é o mesmo ponto vira UM, com várias abas")
    if a.empresa:
        _tolerante([PYTHON, "povoar_vinculo.py", "--proprios",
                    "--empresa", a.empresa, "--aplicar"], "vínculo próprio")
        if cidade:
            # `cruzar_fontes`, e nao mais o `--juntar` do `povoar_vinculo`.
            #
            # Aquele agrupava por nome IDENTICO mais coordenada arredondada a
            # 5 casas, e com isso nao usava NADA do que a etapa 6 produziu — a
            # normalizacao virava dado que ninguem consumia. Este aplica as
            # regras declaradas (endereco > site > telefone, raio de 20 m,
            # telefone nunca sozinho), grava a confianca de 1 a 10 e manda o
            # meio-termo para a IA da Spark decidir.
            _tolerante([PYTHON, "cruzar_fontes.py",
                        "--cidade", cidade, "--empresa", a.empresa,
                        "--area", a.area,
                        "--aplicar"], "cruzamento entre as fontes")
    else:
        _log("  pulado — o cruzamento carimba a empresa dona, e ela vem no")
        _log("  comando (--empresa), nunca do .env")

    _log("─" * 62)
    _log("✅ Mineração completa. Filtre por 🔗 Multiorigem no mapa para revisar")
    _log("   os pontos que passaram a ser sustentados por mais de uma base.")
    return rc_captura


def _cod_municipio(cidade: str, uf: str) -> str:
    """Código IBGE pelo nome, sem acento — a mesma regra do `server.py`.

    Comparação em PYTHON e não por função do banco: a `unaccent` é extensão, e
    depender dela trocaria uma comparação de 500 nomes por uma dependência de
    instalação.
    """
    import unicodedata
    import base_comum

    def n(s):
        return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                       if unicodedata.category(c) != "Mn").strip()

    alvo = n(cidade)
    # PEDE A MALHA ANTES DE DESISTIR. Ela entra sob demanda (é assim que as 20
    # UFs do banco chegaram lá: foram visitadas no mapa), e sem o código quatro
    # das sete etapas caem juntas.
    area_utils.garantir_malha(uf, log=_log)
    ref = base_comum.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select cod_municipio, nome from ibge_malha where uf = %s", (uf,))
            for cod, nome in cur.fetchall():
                if n(nome) == alvo:
                    return str(cod)
    finally:
        ref.close()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
