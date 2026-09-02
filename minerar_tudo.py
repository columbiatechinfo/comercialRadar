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
# Onde o watcher do servidor procura o resultado da captura. Quem escreve e
# quem le sao o mesmo disco desde 30/08/2026 — antes a etapa rodava em outra
# maquina e uma thread trazia o arquivo de tempos em tempos.
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
    if _etapa_pulada():
        return 0
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
    """Devolve a pasta do dataset da UF. NÃO a produz sozinha por padrão.

    POR QUE NÃO PRODUZ SOZINHA

    Produzir a UF é o trabalho pesado do processo: DuckDB sobre o Overture no S3
    mais o PBF do OpenStreetMap, para um estado inteiro — 383 mil POIs no RS.
    São horas de CPU e disco, e enquanto isso a captura não começa.

    A regra é: se o dataset existe, usa; se não existe, **para e diz o comando
    que o produz** — em vez de começar sozinha uma tarefa de horas que ninguém
    pediu. `--produzir-bases` autoriza a produção nesta rodada.

    ATÉ 30/08/2026 O TEXTO MANDAVA PRODUZIR NO i9, por SSH. Fazia sentido quando
    quem editava era um notebook e o dataset de 10 GB morava ao lado do banco,
    na outra máquina. No padrão A2L o sistema mora no servidor — que é
    justamente a máquina com CPU para isso — e a produção acontece aqui.
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
            f"   Foursquare) e não começa sozinho de propósito — quem paga as\n"
            f"   horas decide quando.\n\n"
            f"   Uma vez por UF, nesta máquina:\n\n"
            f"     python minerar_tudo.py --uf {uf.upper()} --produzir-bases\n\n"
            f"   Para minerar SÓ com a captura enquanto isso: --pular-bases.\n")

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


# ONDE O DATASET MORA — uma maquina so, desde 30/08/2026.
#
# Ate esta data havia duas: o notebook editava e o i9 guardava o dataset da UF
# (10 GB) junto do banco. `dataset_pronto` PERGUNTAVA AO i9 POR SSH se a UF
# estava produzida, e a resposta mudava o caminho da importacao. Isso ja custou
# um defeito calado: a conferencia foi para o i9 e o caminho ficou local, entao
# `garantir_dataset` devolvia uma pasta que no notebook nao existia e o
# `extracao_estadual` morria sem dizer por que — a base estadual simplesmente
# nao entrava.
#
# No padrao A2L o sistema MORA no servidor: dataset, banco e codigo na mesma
# maquina. A pergunta remota deixou de existir, e com ela a classe de defeito.


def dataset_pronto(uf: str) -> tuple:
    """`(pronto, onde)` — o dataset da UF esta no disco?

    Devolve tambem ONDE, porque "nao existe" e a unica resposta que justifica
    produzir a UF de novo, e quem le o log precisa ver o caminho conferido.
    """
    uf = (uf or "").upper()
    if not uf:
        return False, "sem UF"
    if (DATASETS / uf / MARCADOR).exists():
        return True, "disco local"
    return False, f"nao existe em {DATASETS / uf}"


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
        (9, "cadastro do cliente — qual ligacao e cada ponto", bool(cidade),
         "" if cidade else "sem cidade nao ha cadastro a cruzar"),
    ]

    _log("")
    _log("=" * 62)
    _log(f" O QUE RODA EM {cidade or '?'}/{uf or '?'}"
         + (f" ({cod})" if cod else ""))
    _log(f" datasets: {DATASETS}")
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


# A FUNCAO `_importar_no_i9` SAIU DAQUI.
#
# Ela mandava o recorte do municipio para rodar no i9 por SSH, e antes de
# cada execucao reenviava o `extracao_estadual.py` em base64 — porque a
# copia de la nao tinha sincronia nenhuma com o repositorio e isso trouxe
# de volta um defeito ja corrigido: em 26/08/2026 a importacao de Bento
# Goncalves gravou 1.408 POIs de nome "nan", e quatro clubes distintos a
# ate 119 m um do outro foram FUNDIDOS por "nomes iguais (100%)".
#
# Com codigo, dataset e banco na mesma maquina nao ha copia a sincronizar,
# nem base64 a mandar, nem CR do Windows para o bash do outro lado engolir.


TOTAL_ETAPAS = 9


# DE QUAL ETAPA COMEÇAR — e o gate fica no ATO, não no cabeçalho.
#
# Nasceu de duas rodadas perdidas no meio. Em 29/08/2026 a de Rio Grande caiu no
# passo 7 porque o i9 REINICIOU (`up 29 min`, boot às 10:00, última linha do log
# às 09:56). Captura, OCR e busca já estavam no banco — 2 h de trabalho — e não
# havia como retomar do 7 sem refazer tudo.
#
# A PRIMEIRA VERSÃO DESTE GATE ESTAVA ERRADA, e vale registrar: eu pus um
# `if not _pular_etapa(n):` na frente de cada `_etapa(n, ...)`. Aquilo protege a
# LINHA DO CABEÇALHO, não o corpo — as chamadas seguintes continuam no mesmo
# recuo e rodariam igual. Guardar o corpo exigiria reindentar sete blocos, o que
# é convite a erro.
#
# Guardando o ATO em vez do bloco, três funções cobrem tudo: nada roda sem
# passar por `_rodar`, `_tolerante` ou `_tolerante_i9`.
#
# As etapas são idempotentes por desenho — leem o banco e regravam. Pular a
# captura não é atalho: é reconhecer que ela já rodou.
_DE_ETAPA = 1
_ETAPA_ATUAL = 1


def _etapa_pulada() -> bool:
    return _ETAPA_ATUAL < _DE_ETAPA


def _etapa(n: int, titulo: str) -> None:
    global _ETAPA_ATUAL
    _ETAPA_ATUAL = n
    if _etapa_pulada():
        _log("")
        _log(f"▶ {n}/{TOTAL_ETAPAS} {titulo} — PULADA (--de-etapa {_DE_ETAPA})")
        return
    return _etapa_cabecalho(n, titulo)


def _etapa_cabecalho(n: int, titulo: str) -> None:
    _log("")
    _log("─" * 62)
    # O TOTAL SAI DA CONSTANTE, e nao chumbado no texto. Quando a etapa 9
    # (cadastro do cliente) entrou, em 28/08/2026, todo o log continuou
    # dizendo "de 8" — o cabecalho da rodada, o passo 9 inclusive.
    _log(f"▶ {n}/{TOTAL_ETAPAS} {titulo}")
    _log("─" * 62)


def _tolerante(cmd: list, nome: str) -> int:
    if _etapa_pulada():
        return 0
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


def _tolerante_i9(argumentos: list, nome: str) -> int:
    """O NOME FICOU, A VIAGEM SAIU. Roda local, como todo o resto.

    Até 30/08/2026 esta função mandava a etapa para OUTRA máquina por SSH: o
    notebook do operador editava o código, o i9 executava. Existia por um motivo
    bom — seis Chromium com proxy no notebook disputam a CPU da tela que mostra
    a própria mineração — e cobrou caro por isso três vezes em um único dia:

        · a captura escapou do `--de-etapa`, ficou órfã no i9 e segurou a porta
          8766; a rodada seguinte morreu com "porta já está em uso", 0 de 3.234
          tiles;
        · matar o processo local NÃO matava o remoto — matar o pai não mata o
          filho do outro lado do SSH;
        · o i9 reiniciou no meio de uma rodada e o `ssh` local ficou pendurado
          sem timeout, esperando resposta de um processo que já não existia.

    No padrão A2L (doc 23) o sistema MORA no servidor: quem edita e quem executa
    são a mesma máquina, e a classe inteira de defeito desaparece.

    O nome sobrevive para não reescrever dezesseis chamadas num commit que já
    muda banco, identidade e portas. Ele será aposentado quando a poeira baixar.
    """
    return _tolerante([PYTHON] + argumentos, nome)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    # `required` deixa de valer com `--so-diagnostico`: pedir o nome da sessao
    # para NAO rodar sessao nenhuma e obrigar a inventar um valor descartavel.
    p.add_argument("--sessao", default="")
    p.add_argument("--zoom", type=int, default=19)
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--capture-workers", dest="capture_workers", type=int, default=10)
    p.add_argument("--no-proxy", dest="no_proxy", action="store_true")
    p.add_argument("--empresa", default="", help="nome da empresa dona do dado")
    p.add_argument("--de-etapa", dest="de_etapa", type=int, default=1,
                   metavar="N",
                   help="começa da etapa N (1-9): retoma uma rodada que caiu "
                        "no meio sem refazer a captura")
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
    p.add_argument("--so-diagnostico", dest="so_diagnostico", action="store_true",
                   help="mostra quais etapas rodariam, e SAI sem rodar nenhuma")
    p.add_argument("--produzir-bases", dest="produzir_bases", action="store_true",
                   help="produz o dataset da UF. São horas de CPU e disco, e "
                        "nesse tempo a captura não anda.")
    a = p.parse_args(argv)
    global _DE_ETAPA
    _DE_ETAPA = max(1, min(int(a.de_etapa or 1), TOTAL_ETAPAS))

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

    if a.so_diagnostico:
        _log("")
        _log("  (--so-diagnostico: nada foi executado)")
        return 0

    if not a.sessao:
        _log("❌ --sessao é obrigatório para rodar. Para só ver o quadro acima, "
             "use --so-diagnostico.")
        return 2

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
    # A ETAPA MAIS PESADA: dez navegadores reais mais uma sessão com proxy por
    # lote. Ela come CPU, memória e banda, e é por isso que o sistema mora no
    # servidor e não na máquina de quem opera.
    #
    # UM DETALHE QUE JÁ CUSTOU UMA RODADA: **a API da Webshare não responde de
    # qualquer lugar**. Medido em 26/08/2026 — `urlopen error timed out` do
    # notebook, 1,1 s do servidor. Sem ela o pool cai num cache em disco e
    # trabalha às cegas sobre a lista de ontem, que foi o que aconteceu na run
    # de Bento Gonçalves. Se a mineração voltar a rodar de outro lugar, este é
    # o primeiro item a conferir.
    _etapa(4, "captura + OCR do Maps — a única que traz painel e foto")

    cmd = ["minerar_captura.py", "--area", a.area, "--sessao", a.sessao,
           "--zoom", str(a.zoom), "--workers", str(a.workers),
           "--capture-workers", str(a.capture_workers)]
    if a.no_proxy:
        cmd.append("--no-proxy")

    # O MAPA AO VIVO PRECISA DO ARQUIVO AQUI.
    #
    # O ESPELHO SAIU, E ELE NÃO TINHA MAIS O QUE ESPELHAR.
    #
    # Quem grava os POIs durante a mineração é o watcher do servidor, e ele
    # observa `crops/<sessao>_db.json`. Enquanto a captura rodava em OUTRA
    # máquina, o arquivo nascia lá e uma thread o trazia de tempos em tempos —
    # sem isso o operador via a tela parada até o fim, justamente a hora em que
    # ele quer ver o marcador caindo.
    #
    # Rodando no servidor, quem escreve e quem lê são o mesmo disco. Copiar um
    # arquivo para o lugar onde ele já está seria trabalho e uma janela a mais
    # para inconsistência.
    #
    # NADA A FAZER AQUI, PORTANTO — este bloco é só o registro de por quê.
    # O GATE TAMBÉM AQUI. Esta etapa não passa por `_rodar` com guarda própria
    # no cabeçalho: ela dispara direto, e por isso atravessou o `--de-etapa` na
    # primeira versão — o cabeçalho dizia "PULADA" e a captura rodava assim
    # mesmo. Gate por FUNÇÃO só cobre quem passa por ela.
    rc_captura = 0 if _etapa_pulada() else _rodar([PYTHON] + cmd)

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
        # PESADA COMO A CAPTURA: abre um navegador com proxy POR WORKER, seis
        # Chromium ao mesmo tempo. Enquanto isso rodou na máquina do operador,
        # o sintoma enganava — quem sentia a máquina pesar culpava a captura.
        #
        # E derrubar "os navegadores da mineração" derrubava o Chrome PESSOAL
        # junto: não há como distinguir pelo nome do processo. No servidor, sem
        # ninguém navegando, os dois deixam de se confundir.
        _tolerante_i9(["descobrir_maps.py", "--area", a.area,
                       "--empresa", a.empresa, "--aplicar"],
                      "descoberta por categoria")

    # ── 6 · iFood ─────────────────────────────────────────────────────────
    #
    # Sob demanda, como a captura: o iFood não tem base pública por UF, e a
    # metade cara (enumerar os ids) precisa de navegador na praça daquela área.
    #
    # BLOQUEADA POR FORA — e isso NÃO é problema de máquina.
    #
    # PRECISA DE DESKTOP. Esta etapa abre navegador visível, e navegador visível
    # não sobe em ambiente sem sessão gráfica — medido em 26/08/2026 no WSL,
    # cinco variantes (sem `WAYLAND_DISPLAY`, `--ozone-platform=x11`,
    # `XDG_RUNTIME_DIR` do WSLg e as combinações), todas estourando o launch em
    # ~46 s, contra 1,1 s onde havia desktop. No servidor Ubuntu isso continua
    # valendo: sem X ou Wayland, esta metade da etapa não roda. **Item aberto da
    # migração** — a alternativa é Xvfb, ainda não medida aqui.
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
    _etapa(6, "iFood — descobrir as lojas da área, e o CNPJ de cada uma")
    if a.pular_ifood:
        _log("  pulado por --pular-ifood")
    else:
        # A ETAPA VOLTOU A TER DUAS METADES, em 02/09/2026.
        #
        # Em 26/08 a descoberta saiu daqui, e o motivo registrado foi: "o
        # Cloudflare passou de desafio automático para Turnstile interativo, e
        # navegador automatizado não clica". Estava certo sobre o CHROMIUM e
        # errado sobre o problema — o Camoufox resolve o Turnstile em 4 s.
        #
        # O endereço digitado, que era o outro ponto frágil, saiu junto: a praça
        # do feed muda por COORDENADA, pelo "Usar minha localização" do modal de
        # endereço. Não há mais autocomplete, nem dependência de o CNEFE ter
        # número da casa.
        #
        # Medido em 02/09/2026, Canoas inteira:
        #
        #     descoberta   1.537 lojas · 10,6 min · parou por saturação
        #     detalhe      1.537 de 1.537 · 4,1 min · CNPJ em 99,5%
        #
        # A ORDEM IMPORTA. Descobrir traz loja que NENHUM POI da base conhecia;
        # o `enriquecer_por_ifood` alcança só o que já existe. Rodar o segundo
        # primeiro não é errado, é menos: ele não teria o que a descoberta acha.
        rc = _tolerante_i9(["extrair_ifood.py", "--area", a.area, "--oculto"],
                           "iFood — descobrir as lojas da área")
        if rc == 0:
            # O detalhe é HTTP puro e sai de graça: `/v1/merchants/{id}/extra`,
            # sem token e sem navegador. Só depois dele é que sobra trabalho
            # para o caminho caro.
            _tolerante_i9(["detalhar_ifood.py"],
                          "iFood — CNPJ e endereço pelo endpoint público")
        else:
            _log("   sem descoberta nesta rodada; o detalhe fica para a próxima")

        # E O CAMINHO INVERSO CONTINUA, para o POI que o iFood não listou:
        # parte do que já está na base, acha o link por busca e tira o id da URL.
        # As duas direções se completam — uma cobre a loja que a base não tem, a
        # outra o POI que o feed não trouxe.
        rc = _tolerante_i9(["enriquecer_por_ifood.py", "--area", a.area,
                         "--empresa", a.empresa, "--aplicar"],
                        "iFood — CNPJ pelo link da loja")
        if rc != 0:
            _log("   A etapa é tolerante: perder o iFood custa CNPJ, não a")
            _log("   rodada. Pode ser repetida sozinha depois.")

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
        # ESTA ETAPA É A EXCEÇÃO: ela roda a CIDADE, não a área.
        #
        # Decisão do dono do produto, 26/08/2026, e ele foi explícito de que
        # vale SÓ para a normalização de logradouro — "todos os passos da fase 1
        # de extração rodam apenas na área selecionada ou cidade selecionada".
        #
        # O motivo é que aqui o recorte destrói o próprio trabalho. A skill
        # aprende `tokenA ≡ tokenB` por PROVA: mesmo número, 30 m, support de
        # dois imóveis distintos. Com um pedaço ela quase não tem o que provar.
        #
        #     Bento Gonçalves, por área:       84 marcações,     4 ALTA
        #     Canoas, município inteiro:  308.881 marcações, 5.997 ALTA
        #
        # E o custo não se repete: `--so-novos` faz as duas rodarem sobre o que
        # AINDA NÃO foi feito. Numa cidade virgem passa tudo uma vez; nas
        # rodadas seguintes, só o que a mineração acabou de descobrir. Assim
        # ninguém fica para trás — o POI que caiu 50 m fora do desenho seria
        # invisível para sempre num recorte por área.
        # ANTES DE TUDO: A BASE FIXA DO MUNICÍPIO, se ela ainda não foi feita.
        #
        # Regra do dono do produto, 26/08/2026: base grande normaliza por
        # MUNICÍPIO quando muda; POI normaliza só na área. Sem esta linha, a
        # regra existia num comando avulso que alguém precisava lembrar de
        # rodar — e numa cidade nova o efeito seria exatamente o que já
        # aconteceu em Bento Gonçalves:
        #
        #     ajuste_logradouro --area  →  normaliza um pedaço do CNEFE
        #     léxico do município vazio →  84 marcações, 4 ALTA
        #     cruzamento                →  sem a chave de junção que ele espera
        #
        # Com a base feita antes, o mesmo município deu 63.148 marcações — e
        # Canoas, 308.881 com 5.997 ALTA. A skill aprende `tokenA ≡ tokenB` por
        # prova (mesmo número, 30 m, dois imóveis distintos): com um pedaço ela
        # quase não tem o que provar.
        #
        # `normalizar_bases` decide sozinho se há trabalho: compara a carga da
        # base com a cobertura já normalizada e, sem motivo, não faz nada. Então
        # isto custa segundos nas rodadas seguintes do mesmo município.
        #
        # É TOLERANTE: numa cidade sem CNEFE, ou se a skill abortar, a rodada
        # continua — perde-se qualidade de agrupamento, não a mineração.
        _tolerante_i9(["normalizar_bases.py", "--municipio", cod],
                   "normalização da base fixa do município")

        _tolerante_i9(["segmentar_endereco.py", "--municipio", cod,
                    "--so-novos", "--aplicar"], "segmentação de endereço")
        # `--so-novos`, e NÃO `--area`. Regra do dono do produto: normaliza
        # todos os POIs ainda não normalizados da cidade foco, mesmo minerando
        # um pedaço — assim nenhum fica para trás. Na prática quase todos já
        # estão feitos, então só os que a mineração acabou de descobrir passam.
        _tolerante_i9(["ajuste_logradouro.py", "--municipio", cod,
                    "--so-novos", "--aplicar"], "ajuste de logradouro")

        # E SÓ AGORA A COORDENADA PODE SER CONFERIDA CONTRA O ENDEREÇO.
        #
        # A ordem não é gosto: a correção casa o logradouro NORMALIZADO contra
        # o CNEFE. Rodando antes do ajuste acima, "Av. Gen. Flores da Cunha" não
        # encontraria "AVENIDA GENERAL FLORES DA CUNHA" e o POI passaria batido.
        #
        # MEDIDO em Canoas, 27/08/2026: 1.850 POIs a mais de 100 m da porta que
        # o próprio endereço deles declara, sendo 351 a mais de 2 km. Todos os
        # piores vieram de `maps_painel` — a busca por nome casou com um
        # homônimo em outro bairro, e o `place_id` não protege disso.
        _tolerante_i9(["corrigir_coordenada.py", "--cidade", cidade,
                    "--municipio", cod, "--aplicar"],
                   "coordenada conferida contra o endereço")

        # E POR ÚLTIMO O MUNICÍPIO, porque ele APAGA.
        #
        # A ordem protege: o passo acima ainda pode consertar a coordenada de um
        # ponto cujo endereço é daqui. Só depois se pergunta se o ponto pertence
        # à cidade — e aí a resposta é definitiva.
        #
        # Regra do dono do produto, 27/08/2026: "os que vêm da base com CEP de
        # outra cidade têm que ser deletados com certeza, a fonte do endereço é
        # confiável". O CEP não é texto livre: os Correios o atribuem a um
        # trecho de logradouro de um município, e essa declaração vale mais que
        # o campo `cidade`, que é preenchido pelo processo e já errou antes.
        #
        # MEDIDO em Canoas: 229 POIs com CEP de outro município — e todos os 348
        # da primeira contagem tinham coordenada DENTRO da divisa. Se a
        # coordenada mandasse, nenhum seria pego.
        _tolerante_i9(["conferir_municipio.py", "--cidade", cidade,
                    "--municipio", cod, "--aplicar"],
                   "POIs de outro município")
    else:
        _log("  pulado — sem código IBGE do município")

    # ── 8 · cruzamento ────────────────────────────────────────────────────
    _etapa(8, "cruzamento — quem é o mesmo ponto vira UM, com várias abas")
    if a.empresa:
        _tolerante_i9(["povoar_vinculo.py", "--proprios",
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
            # SEM `--area`: a comparação é com a CIDADE INTEIRA.
            #
            # Regra do dono do produto, 26/08/2026: ao minerar uma área nova, o
            # que ela descobriu tem de ser confrontado com toda a base daquela
            # cidade — não só com os vizinhos do desenho.
            #
            # Recortando pela área, um POI novo era comparado com ~118 vizinhos
            # em vez dos 49.559 da cidade. E o custo disso apareceu: Canoas
            # tinha 13.271 duplicatas esperando, com 11.983 delas de MESMO NOME
            # e MESMO LOGRADOURO — nunca fundidas porque a fusão nunca as viu
            # juntas.
            #
            # O que sustenta essa mudança é a fusão ser incremental por
            # natureza: o POI já fundido sai da consulta (`status='fundido'`),
            # então a cada rodada só o que ainda não foi resolvido é comparado.
            _tolerante_i9(["cruzar_fontes.py",
                        "--cidade", cidade, "--empresa", a.empresa,
                        "--aplicar"], "cruzamento entre as fontes")
    else:
        _log("  pulado — o cruzamento carimba a empresa dona, e ela vem no")
        _log("  comando (--empresa), nunca do .env")

    # ── 9 · o cadastro do cliente ─────────────────────────────────────────
    _etapa(9, "cadastro do cliente — qual ligação é cada ponto")
    if cidade:
        # DEPOIS DO 8, E ISSO É DECISÃO DO DONO DO PRODUTO, 28/08/2026.
        #
        # O cadastro cruza com POIs que já foram fundidos e já tiveram a
        # coordenada corrigida — o dado mais maduro que a rodada produz. Antes
        # do 8, ele casaria com duplicatas que o 8 vai unir logo em seguida, e
        # o vínculo apontaria para um ponto que deixa de existir.
        #
        # A ETAPA NÃO EXISTIA NO PROCESSO, e é o defeito que isto conserta. O
        # `cadastro_cliente.cruzar()` existe desde antes e já havia rodado uma
        # vez, à mão: 12.040 ligações com POI, de uma foto do banco que
        # envelhecia a cada mineração. Foi assim que 255 delas acabaram
        # apontando para POI fundido — o cruzamento ficou parado enquanto o
        # passo 8 seguia unindo pontos.
        #
        # O QUE MUDA COM A NORMALIZAÇÃO NO TOPO, medido em Canoas:
        #
        #     antes .... 12.040 ligações com POI  (CEP+número e geografia)
        #     agora .... 16.328                   (logradouro normalizado: 11.532)
        #
        # E o outro lado do número é a segunda lista da tela: 16.101 POIs sem
        # ligação nenhuma, para vinculação humana.
        _tolerante_i9(["cadastro_cliente.py", "--cruzar", "--cidade", cidade],
                      "cadastro do cliente")
    else:
        _log("  pulado — sem cidade não há cadastro a cruzar")

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
