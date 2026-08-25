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
import area_utils

BASE = Path(__file__).resolve().parent
PYTHON = sys.executable
SKILL = BASE / "skills" / "extracao-poi-estadual"
DATASETS = BASE / "dados_externos" / "estadual"

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
    if (destino / MARCADOR).exists():
        _log(f"📚 Bases públicas: dataset de {uf.upper()} já existe — reaproveitando")
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
    _log(f"▶ 1/3 bases públicas — produzindo o dataset de {uf.upper()}")
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

    # ── 1 e 2 · bases públicas ────────────────────────────────────────────
    if a.pular_bases:
        _log("⏭  Bases públicas PULADAS por --pular-bases (modo de depuração)")
    elif not uf:
        _log("⚠️  Não identifiquei a UF da área — as bases públicas trabalham por")
        _log("   UF e por município, então esta etapa fica de fora desta rodada.")
    else:
        cod = _cod_municipio(cidade, uf)
        if not cod:
            _log(f"⚠️  Não achei o código IBGE de {cidade}/{uf} na malha — a")
            _log("   importação por município fica de fora desta rodada.")
        else:
            _log("─" * 62)
            _log(f"▶ 2/3 bases públicas — importando {cidade}/{uf} ({cod})")
            _log("─" * 62)
            rc = _importar_municipio(uf, cod, a.empresa,
                                     produzir_aqui=a.produzir_bases)
            if rc != 0:
                # NÃO derruba a rodada: o dataset está no disco e a importação
                # pode ser repetida sozinha depois. Perder as horas de captura
                # por causa disto seria trocar um problema pequeno por um caro.
                _log(f"⚠️  A importação do município falhou (código {rc}). O dataset")
                _log("   ficou no disco; dá para repetir só esta etapa depois.")

    # ── 3 · captura + OCR ─────────────────────────────────────────────────
    _log("─" * 62)
    _log("▶ 3/3 captura + OCR")
    _log("─" * 62)
    cmd = [PYTHON, "minerar_captura.py", "--area", a.area, "--sessao", a.sessao,
           "--zoom", str(a.zoom), "--workers", str(a.workers),
           "--capture-workers", str(a.capture_workers)]
    if a.no_proxy:
        cmd.append("--no-proxy")
    return _rodar(cmd)


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
