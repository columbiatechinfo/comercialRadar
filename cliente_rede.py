# -*- coding: utf-8 -*-
"""Manda o degrau pesado para o i9 — e sabe voltar sozinho se ele não atender.

O QUE ESTE MÓDULO RESOLVE

`worker_rede.py` roda as ferramentas no i9. Falta o outro lado: fazer o agente
usá-lo sem que nada no agente mude de nome ou de assinatura.

É o que acontece aqui. `envolver()` troca as funções dentro de
`agente_local.FERRAMENTAS` por versões que fazem a mesma chamada por HTTP. O
modelo continua pedindo `consultar_maps(nome, cidade)`; o que mudou foi a
máquina onde o Chromium abre.

A QUEDA PARA LOCAL, e por que ela existe

Se o i9 estiver desligado, em manutenção ou fora da Tailscale, a ferramenta
roda no notebook como sempre rodou. Sem isso, uma máquina fora do ar deixaria o
chat inteiro sem busca — trocar um teto de desempenho por um ponto único de
falha seria piorar.

A queda é registrada no resultado (`executado_em`), porque diferença de
desempenho sem explicação vira suspeita de bug. Se a resposta demorar o dobro,
o campo diz que foi local, e o motivo aparece.

O TEMPO DE ESPERA

Generoso — o Maps leva dezenas de segundos por natureza, e cair no meio para
refazer no notebook custaria as duas coisas. Já a checagem de saúde é curta:
descobrir que o i9 está fora tem de ser rápido, senão a queda demora mais que o
trabalho.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

URL = os.environ.get("WORKER_REDE_URL", "").strip().rstrip("/")
TOKEN = os.environ.get("WORKER_REDE_TOKEN", "").strip()

ESPERA_TRABALHO = 300    # o Maps é lento por natureza; cortar no meio é pior
ESPERA_SAUDE = 4         # descobrir que está fora tem de ser barato

# Estado da última checagem, para não perguntar "está de pé?" a cada chamada
_SAUDE = {"em": 0.0, "ok": False, "detalhe": None}
INTERVALO_SAUDE = 60


def disponivel(forcar: bool = False) -> bool:
    """O worker está de pé? A resposta vale um minuto."""
    if not URL or not TOKEN:
        return False
    agora = time.time()
    if not forcar and agora - _SAUDE["em"] < INTERVALO_SAUDE:
        return _SAUDE["ok"]
    try:
        with urllib.request.urlopen(f"{URL}/saude", timeout=ESPERA_SAUDE) as r:
            _SAUDE.update({"em": agora, "ok": True,
                           "detalhe": json.loads(r.read())})
    except Exception as e:
        _SAUDE.update({"em": agora, "ok": False,
                       "detalhe": f"{type(e).__name__}: {str(e)[:120]}"})
    return _SAUDE["ok"]


def chamar(nome: str, **args) -> dict | None:
    """Executa a ferramenta no i9. Devolve None se não deu — aí cai para local."""
    corpo = json.dumps({"nome": nome, "args": args}).encode()
    req = urllib.request.Request(
        f"{URL}/ferramenta", data=corpo, method="POST",
        headers={"Content-Type": "application/json", "X-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=ESPERA_TRABALHO) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # 401 e 404 são configuração errada, não instabilidade: não adianta
        # tentar de novo, e cair para local calado esconderia o problema
        if e.code in (401, 404):
            return {"erro": f"worker recusou '{nome}': HTTP {e.code}. "
                            f"Confira WORKER_REDE_TOKEN e a versão do worker."}
        return None
    except Exception:
        return None      # i9 fora do ar: o chamador roda local


def envolver(ferramentas: dict, quais: set | None = None) -> dict:
    """Troca as funções por versões que rodam no i9, com queda para local.

    Nada muda de nome nem de assinatura: o modelo continua chamando
    `consultar_maps(nome, cidade)`. Só a máquina onde o Chromium abre é outra.
    """
    if not URL or not TOKEN:
        return ferramentas

    # Só o que é pesado vale a viagem. `consultar_banco` e `consultar_receita`
    # falam com o Postgres do i9 de qualquer jeito; mandá-las por HTTP
    # acrescentaria um salto para poupar nenhum.
    quais = quais or {"consultar_maps", "consultar_instagram"}

    def remoto(nome, local):
        def chamado(**kw):
            if disponivel():
                r = chamar(nome, **kw)
                if r is not None:
                    return r
            r = local(**kw)
            if isinstance(r, dict):
                r["executado_em"] = "notebook (i9 indisponível)"
                r["motivo_da_queda"] = _SAUDE.get("detalhe") \
                    if not _SAUDE["ok"] else "worker não respondeu a tempo"
            return r
        chamado.__name__ = nome
        chamado.__doc__ = local.__doc__
        return chamado

    return {n: (remoto(n, f) if n in quais else f)
            for n, f in ferramentas.items()}


def onde() -> dict:
    """Diagnóstico curto: onde os degraus pesados vão rodar agora."""
    if not URL:
        return {"destino": "notebook", "porque": "WORKER_REDE_URL não definida"}
    if not TOKEN:
        return {"destino": "notebook", "porque": "WORKER_REDE_TOKEN não definido"}
    ok = disponivel(forcar=True)
    return {"destino": "i9" if ok else "notebook", "url": URL,
            "worker": _SAUDE["detalhe"]}


if __name__ == "__main__":
    print(json.dumps(onde(), ensure_ascii=False, indent=2))
