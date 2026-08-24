# -*- coding: utf-8 -*-
"""O processamento de rede sai do notebook e passa a rodar no i9.

POR QUE MUDAR DE MÁQUINA

Os degraus pesados da escada — Maps, Instagram — abrem Chromium com proxy e
fingerprint. Cada um come ~300 MB. No notebook isso impunha um teto de quatro
navegadores, e o lote em paralelo batia nele: três lojas levavam ~390 s porque
o Maps ia em fila.

O i9 tem 16 CPUs e 51 GB livres no WSL. O mesmo trabalho cabe dezesseis vezes.
E ele já é onde vivem o Postgres, o SearXNG, o Nominatim e o Photon — hoje o
notebook atravessa a rede para falar com todos eles a cada consulta. Rodando
aqui, essas conversas viram localhost.

Some-se o que não é desempenho: o i9 fica ligado. Fechar o notebook deixa de
matar um lote em andamento.

O QUE ESTE SERVIÇO É

Uma porta HTTP para as ferramentas que já existem. Ele não reimplementa nada —
importa `ferramenta_maps`, `ferramenta_instagram` e `agente_local` e chama as
mesmas funções. Trocar a máquina onde o navegador abre não deveria mudar o que
o navegador faz, e não muda.

O DESENHO, e por que uma porta só

Uma rota genérica `/ferramenta`, e não `/maps`, `/instagram`, `/web`. Ferramenta
nova passa a existir aqui sem rota nova e sem cliente novo — é a mesma razão de
`FERRAMENTAS` ser um dicionário no agente.

SEGURANÇA

Fica na Tailscale, que já não é rede aberta, mas exige token mesmo assim: uma
rota que abre navegador e consulta banco não deve depender só de topologia.
Sem `WORKER_REDE_TOKEN` no ambiente, o serviço RECUSA subir — segredo com valor
padrão é segredo que ninguém troca.

Subir:
    MAPS_SESSOES=16 WORKER_REDE_TOKEN=... \
      uvicorn worker_rede:app --host 0.0.0.0 --port 8410
"""
from __future__ import annotations

import asyncio
import os
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

TOKEN = os.environ.get("WORKER_REDE_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "WORKER_REDE_TOKEN ausente. Este serviço abre navegador e consulta o "
        "banco; não sobe sem segredo. Defina no ambiente do i9.")

# Quantas chamadas pesadas ao mesmo tempo. Acompanha o tamanho da piscina de
# sessões do Maps: mais requisições que sessões só produz fila mais longa.
SIMULTANEAS = int(os.environ.get("MAPS_SESSOES", "8"))

app = FastAPI(title="comercialRadar — worker de rede")

_PORTAO = asyncio.Semaphore(SIMULTANEAS)
_NASCIDO = time.time()
_CONTA = {"pedidos": 0, "erros": 0}

# As ferramentas expostas. Só leitura e só rede: nada aqui escreve no banco.
# A lista é explícita de propósito — expor `FERRAMENTAS` inteiro publicaria
# qualquer função que alguém registrasse depois, sem ninguém decidir.
PESADAS = {"consultar_maps", "consultar_instagram"}
LEVES = {"buscar_web", "abrir_pagina", "buscar_lugar", "consultar_receita",
         "consultar_banco"}


def _tabela():
    """Resolve os nomes para as funções reais, uma vez."""
    import agente_local as A
    return {n: A.FERRAMENTAS[n] for n in (PESADAS | LEVES)
            if n in A.FERRAMENTAS}


class Pedido(BaseModel):
    nome: str
    args: dict = {}


@app.get("/saude")
def saude():
    import ferramenta_maps as M
    return {"ok": True, "de_pe_ha_segundos": round(time.time() - _NASCIDO),
            "sessoes_maps": M.TAMANHO_PISCINA, "simultaneas": SIMULTANEAS,
            "ferramentas": sorted(_tabela()), **_CONTA}


@app.post("/ferramenta")
async def ferramenta(p: Pedido, x_token: str = Header(default="")):
    if x_token != TOKEN:
        raise HTTPException(401, "token inválido")

    tabela = _tabela()
    fn = tabela.get(p.nome)
    if fn is None:
        raise HTTPException(404, f"ferramenta '{p.nome}' não é exposta aqui")

    _CONTA["pedidos"] += 1
    t0 = time.time()
    laco = asyncio.get_event_loop()

    async def executar():
        # as ferramentas são síncronas; vão para a piscina de threads para não
        # travarem o laço do servidor enquanto esperam rede
        return await laco.run_in_executor(None, lambda: fn(**p.args))

    try:
        if p.nome in PESADAS:
            async with _PORTAO:
                r = await executar()
        else:
            r = await executar()
    except Exception as e:
        _CONTA["erros"] += 1
        return {"erro": f"{type(e).__name__}: {str(e)[:300]}",
                "segundos": round(time.time() - t0, 1)}

    if isinstance(r, dict):
        r["segundos"] = round(time.time() - t0, 1)
        r["executado_em"] = "i9"
    return r


@app.on_event("shutdown")
async def desligar():
    """Fecha os navegadores. Sem isso, restart deixa Chromium órfão comendo RAM."""
    try:
        import ferramenta_maps as M
        M.encerrar_maps()
    except Exception:
        pass
