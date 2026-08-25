# -*- coding: utf-8 -*-
"""monitor_banda.py — quanto do plano de proxy já foi consumido, agora.

POR QUE ISTO EXISTE

O aviso da Webshare chega quando o estrago já está encaminhado: "you will
likely exceed your bandwidth limit". Ele não diz quanto falta, em que ritmo, nem
o que está gastando — e sem isso a única reação possível é comprar mais banda.

Este módulo lê a API e responde as três perguntas que decidem:

    quanto já foi           `/stats/aggregate/` → bandwidth_total
    em que ritmo            usado ÷ dias corridos do ciclo
    quanto custa uma rodada bytes por requisição × requisições previstas

O QUE **NÃO** GASTA BANDA DO PLANO, e isso muda a conta

Medido em 25/08/2026: `baixar_imagens.py`, `streetview_capture.py`,
`descrever_imagens.py` e `leitura_fachada.py` **não usam proxy**. Imagem do
Maps, Street View e análise visual saem pela conexão direta. O plano é
consumido por quem abre NAVEGADOR com proxy: a captura, a cascata do Maps, o
Instagram, a mineração web e o iFood.

Confundir os dois leva a dimensionar o plano pelo volume de imagem — que é o
maior em bytes e o único que não passa por ali.

USO
    python monitor_banda.py             # o quadro de agora
    python monitor_banda.py --json      # para o painel
    python monitor_banda.py --vigiar 60 # a cada 60 s, até Ctrl+C
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import config  # noqa: F401  (.env + UTF-8)

API = "https://proxy.webshare.io/api/v2"
GB = 1024 ** 3

# Campos que NUNCA saem daqui. A `/proxy/config/` devolve a senha do proxy em
# texto puro, e um monitor que a imprime no log transforma observabilidade em
# vazamento — aconteceu numa consulta manual em 25/08/2026.
SEGREDO = ("password", "username", "token", "key", "secret")


def _get(caminho: str) -> dict:
    chave = (os.environ.get("WEBSHARE_API_KEY") or "").strip()
    if not chave:
        raise SystemExit("Sem WEBSHARE_API_KEY no .env — este monitor depende dela.")
    req = urllib.request.Request(API + caminho,
                                 headers={"Authorization": "Token " + chave})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _limpar(d: dict) -> dict:
    return {k: v for k, v in d.items()
            if not any(t in k.lower() for t in SEGREDO)}


def coletar() -> dict:
    """O quadro completo, já com as contas feitas."""
    ag = _get("/stats/aggregate/")
    sub = _limpar(_get("/subscription/"))
    planos = _get("/subscription/plan/").get("results") or [{}]
    plano = _limpar(planos[0])

    usado = int(ag.get("bandwidth_total") or 0)
    projetado = int(ag.get("bandwidth_projected") or 0)
    reqs = int(ag.get("requests_total") or 0)

    ini = datetime.fromisoformat(sub["start_date"].replace("Z", "+00:00"))
    fim = datetime.fromisoformat(sub["end_date"].replace("Z", "+00:00"))
    agora = datetime.now(timezone.utc)
    dias_totais = max((fim - ini).days, 1)
    dias_corridos = max((agora - ini).total_seconds() / 86400, 0.01)
    dias_restantes = max((fim - agora).total_seconds() / 86400, 0)

    return {
        "usado_gb": round(usado / GB, 1),
        "projetado_gb": round(projetado / GB, 1),
        "por_dia_gb": round(usado / GB / dias_corridos, 2),
        "dias_restantes": round(dias_restantes, 1),
        "dias_totais": dias_totais,
        "ciclo_fim": fim.strftime("%d/%m/%Y"),
        "requisicoes": reqs,
        "falhas": int(ag.get("requests_failed") or 0),
        # O NÚMERO QUE DÁ ALAVANCA. 1,3 MB por requisição não é chamada de API,
        # é navegador puxando página com imagem. É onde a banda some, e é o
        # único parâmetro que se pode mudar sem comprar nada.
        "kb_por_requisicao": round(usado / max(reqs, 1) / 1024),
        "ips_usados": ag.get("number_of_proxies_used"),
        "ips_no_plano": plano.get("proxy_count"),
        "concorrencia_media": round(float(ag.get("average_concurrency") or 0), 1),
        "preco_mensal": plano.get("monthly_price"),
        "paises": ag.get("countries_used") or {},
        "throttled": bool(sub.get("throttled")),
    }


def _barra(pct: float, largura: int = 28) -> str:
    n = max(0, min(largura, int(pct / 100 * largura)))
    return "█" * n + "·" * (largura - n)


def imprimir(d: dict) -> None:
    print("\n⟦ banda do plano de proxy ⟧\n")
    # SEM limite conhecido: a API não expõe o teto do plano, então a projeção é
    # comparada com o próprio uso, e não com um número inventado. Dizer "82% do
    # limite" sem saber o limite seria pior que não dizer nada.
    print(f"  usado        {d['usado_gb']:>7.1f} GB   em {d['dias_totais'] - d['dias_restantes']:.1f} dias")
    print(f"  projetado    {d['projetado_gb']:>7.1f} GB   até {d['ciclo_fim']}")
    print(f"  ritmo        {d['por_dia_gb']:>7.2f} GB/dia · faltam {d['dias_restantes']:.1f} dias")
    if d["projetado_gb"] and d["usado_gb"]:
        pct = 100 * d["usado_gb"] / d["projetado_gb"]
        print(f"  do ciclo     {_barra(pct)} {pct:.0f}%")
    print()
    print(f"  requisições  {d['requisicoes']:,} · falhas {d['falhas']} "
          f"({100 * d['falhas'] / max(d['requisicoes'], 1):.2f}%)")
    print(f"  por requisição  {d['kb_por_requisicao']:,} KB "
          f"{'← navegador com imagem; é aqui que a banda vai' if d['kb_por_requisicao'] > 400 else ''}")
    print(f"  IPs          {d['ips_usados']}/{d['ips_no_plano']} usados · "
          f"concorrência média {d['concorrencia_media']}")
    if d["throttled"]:
        print("\n  ⚠️  A CONTA ESTÁ SENDO ESTRANGULADA (throttled). O plano já passou do teto.")
    top = sorted(d["paises"].items(), key=lambda kv: -kv[1])[:4]
    if top:
        print(f"  países       {', '.join('%s %s' % (k, v) for k, v in top)}")
    print()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="saída para o painel")
    p.add_argument("--vigiar", type=int, metavar="SEG",
                   help="repete a cada SEG segundos até Ctrl+C")
    a = p.parse_args(argv)

    if a.vigiar:
        anterior = None
        try:
            while True:
                d = coletar()
                if anterior is not None:
                    delta = d["usado_gb"] - anterior
                    # O DELTA é o que serve durante uma rodada: o acumulado quase
                    # não se move em 60 s e engana quem está vigiando.
                    print(f"  {datetime.now():%H:%M:%S}  {d['usado_gb']:7.1f} GB  "
                          f"({delta:+.2f} GB desde a leitura anterior)", flush=True)
                else:
                    imprimir(d)
                anterior = d["usado_gb"]
                time.sleep(a.vigiar)
        except KeyboardInterrupt:
            print("\n  encerrado.")
        return 0

    d = coletar()
    if a.json:
        print(json.dumps(d, ensure_ascii=False))
    else:
        imprimir(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
