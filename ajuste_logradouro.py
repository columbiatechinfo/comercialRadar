# -*- coding: utf-8 -*-
"""ajuste_logradouro.py — a skill `ajuste-logradouro` virando dado no banco.

O QUE ESTE ARQUIVO RESOLVE

O logradouro é a CHAVE DE JUNÇÃO das bases cruzadas, e cada base escreve a mesma
rua de um jeito. `R. Cel. Marcos`, `RUA CORONEL MARCOS`, `AV BEIRA MAR`. O
cruzamento erra para os dois lados: perde par que existe e casa par que não
existe.

A skill marca a forma canônica com PROVA — só aprende `tokenA ≡ tokenB` a partir
de par confirmado por mesmo número exato dentro de 30 m, com support de dois
imóveis fisicamente distintos e o CNEFE decidindo a direção. Ela entrega ARQUIVO;
a premissa deste projeto é que dado mora no banco. Este arquivo é a ponte, e faz
três coisas que a skill não faz.

**Junta as quatro fontes que temos do mesmo município.** `pois` (o achado),
`cadastro_cliente` (a carteira da concessionária), `ifood_merchant` (a descoberta
do delivery) e o **CNEFE** — este último declarado `autoridade_nivel: 100`,
porque sem autoridade a skill só aprende por dominância de frequência e recusa
decidir entre duas grafias igualmente comuns.

**Resolve o campo grudado antes de entregar.** A skill não segmenta campo único —
e a `pois` só tem `endereco`. Quem separa é o `segmentar_endereco`, pela IA da
Spark, e o resultado vive em `endereco_segmentado`, indexado pelo TEXTO. Aqui só
se faz o `join`: endereço que ainda não foi lido é DITO, não silenciado.

**Roda por MUNICÍPIO, nunca por UF.** Não é preferência: a skill ABORTA se a base
cruzar mais de uma zona UTM, e o léxico é por `scope_id` municipal de propósito —
sobrenome raro numa cidade não é sobrenome errado na outra.

ONDE ISTO RODA

No i9, como a extração estadual: é onde o banco está, onde o CNEFE está, e onde a
skill passa no próprio gate (o `selftest` dela ataca lock POSIX e symlink, que o
Windows recusa sem privilégio).

A IA, quando é chamada, é a da SPARK. O i9 nunca carrega modelo.

USO
    python ajuste_logradouro.py --municipio 4304606 --listar
    python ajuste_logradouro.py --municipio 4304606
    python ajuste_logradouro.py --municipio 4304606 --aplicar
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import config  # noqa: F401  (.env + UTF-8)
import base_comum as bc

BASE = Path(__file__).resolve().parent
SKILL = BASE / "skills" / "ajuste-logradouro"
TRABALHO = BASE / "dados_externos" / "ajuste_logradouro"

# As colunas canônicas que a skill espera de toda fonte.
CANONICAS = ("record_id", "logradouro", "numero", "complemento", "lat", "lon", "scope_id")


def _log(m: str) -> None:
    print(m, flush=True)


def municipio(cod: str) -> tuple:
    """`(nome, uf)` pelo código IBGE, do banco de referência."""
    ref = bc.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select nome, uf from ibge_malha where cod_municipio = %s",
                        (str(cod).strip(),))
            r = cur.fetchone()
    finally:
        ref.close()
    if not r:
        raise SystemExit(f"município {cod} não está na malha IBGE do banco de referência")
    return r[0], r[1]


# ─── de onde sai cada fonte ──────────────────────────────────────────────────
#
# `translate` faz o papel do `unaccent` sem depender da extensão: ela não está
# garantida no banco de referência, e trocar uma comparação de nome por uma
# dependência de instalação é mau negócio. Mesma regra que `minerar_tudo` usa.
_ACENTOS = "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ"
_LISOS = "aaaaeeiooouucAAAAEEIOOOUUC"
_CIDADE = ("upper(translate(coalesce({col}, ''), %(ac)s, %(li)s)) = "
           "upper(translate(%(cidade)s, %(ac)s, %(li)s))").format

SQL_POIS = f"""
select p.id::text, e.logradouro, coalesce(e.numero, ''),
       coalesce(e.complemento, ''), p.maps_lat, p.maps_lng
  from pois p
  join endereco_segmentado e on e.endereco = p.endereco
 where {_CIDADE(col='p.cidade')}
   and coalesce(e.logradouro, '') <> ''
"""

SQL_CADASTRO = f"""
select c.id::text, c.logradouro, coalesce(c.numero, ''),
       coalesce(c.complemento, ''), c.lat, c.lng
  from cadastro_cliente c
 where {_CIDADE(col='c.cidade')}
   and coalesce(c.logradouro, '') <> ''
"""

SQL_IFOOD = f"""
select m.merchant_id, m.rua, coalesce(m.numero, ''), '', m.lat, m.lng
  from ifood_merchant m
 where {_CIDADE(col='m.cidade')}
   and coalesce(m.rua, '') <> ''
"""

SQL_POIS_SEM_LEITURA = f"""
select count(*) from pois p
 where p.endereco is not null and p.endereco <> ''
   and {_CIDADE(col='p.cidade')}
   and not exists (select 1 from endereco_segmentado e where e.endereco = p.endereco)
"""

# O CNEFE monta o logradouro de três partes, e a ordem importa: tipo, título,
# nome. `AVENIDA` + `PRESIDENTE` + `VARGAS`.
#
# `distinct on` porque o `cod_unico_endereco` NÃO é único na nossa carga: 52
# repetidos em 6.000 na primeira execução (0,9%), e a skill acusou com razão —
# `record_id` duplicado faz o mesmo endereço votar duas vezes no support, e
# support é a moeda da prova. Duas linhas com o mesmo código não são dois
# imóveis; contá-las como dois é o começo de um léxico aprendido de eco.
#
# A escolha é determinística (`order by` completo) para que duas execuções sobre
# a mesma base tirem a MESMA linha — a skill depende de idempotência.
SQL_CNEFE = """
select distinct on (cod_unico_endereco)
       cod_unico_endereco,
       trim(concat_ws(' ', nullif(nom_tipo_seglogr,''), nullif(nom_titulo_seglogr,''),
                      nullif(nom_seglogr,''))),
       coalesce(num_endereco::text, ''),
       trim(concat_ws(' ', nullif(nom_comp_elem1,''), nullif(val_comp_elem1,''),
                      nullif(nom_comp_elem2,''), nullif(val_comp_elem2,''))),
       latitude, longitude
  from ibge_cnefe
 where cod_municipio = %s
   and coalesce(nom_seglogr, '') <> ''
 order by cod_unico_endereco, latitude nulls last, longitude nulls last
 limit %s
"""


def _gravar_csv(caminho: Path, linhas, scope: str) -> int:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CANONICAS)
        for rid, logr, num, compl, lat, lon in linhas:
            w.writerow([rid, logr or "", num or "", compl or "",
                        "" if lat is None else lat, "" if lon is None else lon, scope])
            n += 1
    return n


def exportar(cod: str, limite_cnefe: int, dirtrab: Path) -> dict:
    """Cada fonte vira um CSV com as colunas canônicas. Devolve o que saiu."""
    nome, uf = municipio(cod)
    entrada = dirtrab / "entrada"
    contagem = {}

    p = {"ac": _ACENTOS, "li": _LISOS, "cidade": nome}
    con = bc.conectar()
    cur = con.cursor()

    # POIS — depende do que a IA já leu. O que falta é DITO, nunca silenciado:
    # uma fonte que entra menor sem aviso vira "a base tem pouco POI".
    cur.execute(SQL_POIS_SEM_LEITURA, p)
    falta = cur.fetchone()[0]
    if falta:
        _log(f"  ⚠️  {falta:,} POIs com endereço ainda NÃO segmentado — ficam de fora.")
        _log(f"      Resolva antes com:")
        _log(f"        python segmentar_endereco.py --municipio {cod} --aplicar")

    cur.execute(SQL_POIS, p)
    contagem["pois"] = _gravar_csv(entrada / "pois.csv", cur.fetchall(), cod)

    cur.execute(SQL_CADASTRO, p)
    contagem["cadastro"] = _gravar_csv(entrada / "cadastro.csv", cur.fetchall(), cod)

    cur.execute(SQL_IFOOD, p)
    contagem["ifood"] = _gravar_csv(entrada / "ifood.csv", cur.fetchall(), cod)
    con.close()

    ref = bc.conectar_referencia()
    with ref.cursor() as c2:
        c2.execute(SQL_CNEFE, (cod, limite_cnefe or 2000000))
        contagem["cnefe"] = _gravar_csv(entrada / "cnefe.csv", c2.fetchall(), cod)
    ref.close()

    for k, v in contagem.items():
        _log(f"  {k:<10} {v:>9,} registros")
    return contagem


def escrever_config(cod: str, dirtrab: Path, contagem: dict) -> Path:
    """A config da skill, com o CNEFE declarado como autoridade.

    SEM AUTORIDADE A SKILL NÃO DECIDE DIREÇÃO. Ela detecta que `MARECHAL` e
    `MAL` são equivalentes, mas escolher qual é a forma certa é outra pergunta —
    e misturar as duas foi o que produziu `SILVA→SILVAA` na história dela. Sem
    fonte oficial declarada, uma equivalência entre formas igualmente frequentes
    fica `INDEFINIDO` de propósito.
    """
    fontes = []
    for sid, arq in (("cnefe", "cnefe.csv"), ("cadastro", "cadastro.csv"),
                     ("pois", "pois.csv"), ("ifood", "ifood.csv")):
        if not contagem.get(sid):
            continue
        f = {"source_id": sid, "arquivo": f"entrada/{arq}",
             "colunas": {c: c for c in CANONICAS}}
        if sid == "cnefe":
            f["autoridade_nivel"] = 100
        fontes.append(f)

    cfg = {
        "projeto": f"AJUSTE_LOGRADOURO_{cod}",
        # O scope é o município, e a skill exige `scope_id` por linha quando ele
        # tem 7 dígitos: é a trava que impede gravar conhecimento de uma cidade
        # dentro do léxico de outra.
        "scope_id": str(cod),
        "hardening": True,
        "aprendizado": True,
        # Estado mutável, com caminho exclusivo. O léxico é PERSISTENTE entre
        # rodadas — apontar sempre o mesmo é o que faz support, decay e
        # quarentena evoluírem em vez de recomeçarem.
        "lexico_path": f"lexico_{cod}.json",
        "vocab_path": str(SKILL / "vocabulario_aprendido.json"),
        "parametros": {
            "raio_aprendizado_m": 30,
            "min_support_equiv": 2,
            "modo_numero": "estrito",
            # PRODUÇÃO: ALERTA de schema vira parada. É o que barra fonte sem
            # autoridade e coordenada em Web Mercator antes de virar léxico.
            "abortar_em_alerta": False,
            "xlsx": False,          # headless: ninguém abre planilha aqui
            "parquet": True,
            "marcacao_por_fonte": True,
            "retencao_runs": 0,     # nunca apaga: run é evidência
        },
        "fontes": fontes,
    }
    import yaml
    caminho = dirtrab / "config.yaml"
    caminho.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
    return caminho


def rodar_skill(cfg: Path, dirtrab: Path) -> Path:
    """Roda a skill e devolve o diretório do run publicado."""
    saida = dirtrab / "saida"
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONUNBUFFERED="1")
    t = time.time()
    p = subprocess.Popen(
        [sys.executable, str(SKILL / "ajustar_logradouro.py"), str(cfg),
         "--out", str(saida)],
        cwd=str(dirtrab), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1)
    for linha in p.stdout:
        _log("    " + linha.rstrip())
    if p.wait() != 0:
        raise SystemExit(f"❌ a skill falhou (código {p.returncode})")
    _log(f"  skill em {time.time() - t:.0f}s")

    # `latest.json` é o ponteiro atômico. Ler arquivo solto da raiz é errado por
    # desenho — eles não existem mais lá desde a v3.2.2 da skill.
    ponteiro = json.loads((saida / "latest.json").read_text(encoding="utf-8"))
    return Path(ponteiro["path"]) if os.path.isabs(ponteiro["path"]) \
        else saida / ponteiro["path"]


def ingerir(rundir: Path, cod: str, aplicar: bool) -> None:
    """As marcações voltam para o banco, por (fonte, record_id)."""
    import psycopg2.extras

    arq = rundir / "ajuste_logradouro.csv"
    if not arq.exists():
        raise SystemExit(f"não achei {arq}")

    linhas, tiers = [], {}
    with open(arq, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            tier = r.get("aj_logr_tier") or ""
            tiers[tier] = tiers.get(tier, 0) + 1
            linhas.append((
                r.get("source_id") or r.get("fonte") or "?",
                r.get("record_id") or "",
                str(cod),
                r.get("logradouro") or "",
                r.get("aj_logr_marcado") or "",
                tier,
                r.get("aj_logr_origem") or "",
                r.get("aj_logr_risco") or "",
                r.get("aj_num_canonico") or "",
                r.get("aj_compl_organizado") or "",
                rundir.name,
            ))

    _log(f"  {len(linhas):,} marcações · " +
         " · ".join(f"{k or '(vazio)'}={v:,}" for k, v in sorted(tiers.items())))
    if not aplicar:
        _log("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        return

    con = bc.conectar()
    cur = con.cursor()
    psycopg2.extras.execute_values(cur, """
        insert into logradouro_ajustado
          (fonte, record_id, scope_id, logradouro_original, logradouro_marcado,
           tier, origem, risco, numero_canonico, complemento_organizado, run_id)
        values %s
        on conflict (fonte, record_id) do update set
          scope_id = excluded.scope_id,
          logradouro_original = excluded.logradouro_original,
          logradouro_marcado = excluded.logradouro_marcado,
          tier = excluded.tier, origem = excluded.origem, risco = excluded.risco,
          numero_canonico = excluded.numero_canonico,
          complemento_organizado = excluded.complemento_organizado,
          run_id = excluded.run_id, ajustado_em = now()
        """, linhas, page_size=1000)
    cur.execute("""insert into fonte_arquivos (fonte, referencia, tabela, linhas, status)
                   values ('ajuste-logradouro', %s, 'logradouro_ajustado', %s, 'ok')
                   on conflict on constraint fonte_arquivos_pkey do update
                     set linhas = excluded.linhas, status = 'ok', carregado_em = now()""",
                (f"{rundir.name} · municipio {cod}", len(linhas)))
    con.commit()
    con.close()
    _log(f"\n  GRAVADO: {len(linhas):,} marcações em logradouro_ajustado")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--municipio", required=True, help="código IBGE de 7 dígitos")
    p.add_argument("--limite-cnefe", dest="limite_cnefe", type=int, default=0)
    p.add_argument("--listar", action="store_true", help="só exporta e conta")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    nome, uf = municipio(a.municipio)
    dirtrab = TRABALHO / str(a.municipio)
    _log(f"▶ {nome}/{uf} ({a.municipio})")
    _log(f"  trabalho em {dirtrab}")

    _log("\n1/3 exportando as fontes")
    contagem = exportar(a.municipio, a.limite_cnefe, dirtrab)
    if not sum(contagem.values()):
        _log("  nenhuma linha — nada a ajustar")
        return 1
    if a.listar:
        return 0

    _log("\n2/3 rodando a skill")
    cfg = escrever_config(a.municipio, dirtrab, contagem)
    rundir = rodar_skill(cfg, dirtrab)
    _log(f"  run: {rundir}")

    _log("\n3/3 marcações de volta para o banco")
    ingerir(rundir, a.municipio, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
