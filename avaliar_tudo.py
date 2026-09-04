# -*- coding: utf-8 -*-
"""avaliar_tudo.py — capturar a evidência e julgar, numa corrida só.

TRÊS PASSOS, E ELES SÃO SEQUENCIAIS DE VERDADE: o julgamento lê o que a captura
gravou, então rodá-los em paralelo faria a IA avaliar metade da evidência e
dizer "não visível" sobre a outra metade — um erro que sai como veredito
plausível, que é o pior tipo.

    1  evidência de rua      satélite + fachada + lado oposto, por POI
    2  página do anúncio     só Airbnb; o iFood é protegido por desafio e sua
                             prova de atividade vem do `/extra`, em texto
    3  veredito              percepção cega e julgamento isolado, na Spark

O PASSO 2 NÃO DERRUBA A CORRIDA. Ele depende de proxy e de sessão furtiva, que
é a parte mais frágil do conjunto; e numa área sem hospedagem anunciada ele não
tem o que fazer. Falhar ali não pode impedir o veredito dos outros 140 POIs.

Uso:
    python avaliar_tudo.py --area area_atual --aplicar
    python avaliar_tudo.py --area area_atual --limite 20 --aplicar
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

import area_utils

PY = sys.executable or "python"
TOTAL = 3


def _log(m):
    print(m, flush=True)


def _passo(n, titulo):
    _log("")
    _log("─" * 62)
    _log("▶ %d/%d %s" % (n, TOTAL, titulo))


def _rodar(cmd, rotulo, tolerante=False) -> int:
    t0 = time.time()
    r = subprocess.run([PY, "-u"] + cmd)
    dt = time.time() - t0
    if r.returncode:
        msg = "  %s terminou com código %d (%.1f min)" % (rotulo, r.returncode,
                                                          dt / 60)
        if tolerante:
            _log("  ⚠️ " + msg + " — seguindo mesmo assim")
            return 0
        _log("  ❌" + msg)
    return r.returncode


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=4)
    p.add_argument("--sem-pagina", action="store_true",
                   help="pula o passo do Airbnb")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    comum = ["--area", a.area, "--trabalhadores", str(a.trabalhadores)]
    if a.limite:
        comum += ["--limite", str(a.limite)]
    if a.aplicar:
        comum.append("--aplicar")

    t0 = time.time()
    _log("▶ avaliação por IA — evidência e veredito")

    _passo(1, "evidência de rua — satélite, fachada e lado oposto")
    rc = _rodar(["capturar_evidencia.py"] + comum, "captura de evidência")
    if rc:
        return rc

    if a.sem_pagina:
        _passo(2, "página do anúncio — PULADA (--sem-pagina)")
    else:
        _passo(2, "página do anúncio — as hospedagens da área")
        # TRABALHADORES PRÓPRIOS: aqui cada um segura uma sessão furtiva com IP
        # próprio, e não uma aba. Quatro sessões do Camoufox ao mesmo tempo
        # pesam muito mais que quatro abas do Chromium.
        _rodar(["capturar_pagina.py", "--area", a.area, "--trabalhadores", "2"]
               + (["--limite", str(a.limite)] if a.limite else [])
               + (["--aplicar"] if a.aplicar else []),
               "captura de página", tolerante=True)

    _passo(3, "veredito — percepção cega, julgamento isolado")
    rc = _rodar(["avaliar_ia.py"] + comum, "veredito da IA")

    _log("")
    _log("─" * 62)
    _log("✅ Avaliação concluída em %.1f min." % ((time.time() - t0) / 60))
    _log("   Filtre por veredito no mapa para revisar o que ficou em "
         "'revisão humana'.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
