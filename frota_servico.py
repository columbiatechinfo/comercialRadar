# -*- coding: utf-8 -*-
"""frota_servico.py — a frota permanente de uma máquina (dono do produto, 17/09/2026).

A frota deixa de nascer e morrer com o processo da etapa (regra 2: o navegador vive até degradar). Este serviço fica
no ar num contêiner por máquina, com `init`, e mantém uma `Frota` por site. As etapas não abrem navegador: gravam
tarefas em `navegacao.tarefa` (`frota_cliente.enviar`), este serviço pega, executa num navegador quente e grava o
resultado; a etapa lê pelo lote (`frota_cliente.acompanhar`).

    etapa (minerar_tudo)         navegacao.tarefa             este serviço (i9, notebook)
    enviar(lote, pontos)   →     fila                  →      pegar_tarefas (skip locked, por site e ambiente)
                                                             Frota(site): navegador quente executa a função registrada
    acompanhar(lote)       ←     ok · erro + resultado  ←     grava resultado, batimento a cada 30 s

O TIPO É UM NOME DO REGISTRO, nunca um módulo livre: a fila não executa código arbitrário. Para um passo novo entrar
na frota, a função de tarefa (`def f(p, **kwargs) -> JSON`) é acrescentada em `REGISTRO`.

UMA CONEXÃO POR SITE: o laço de despacho usa a mesma conexão da frota daquele site (com trava). O pooler tem 20.

USO
    python3 frota_servico.py --sites "ifood:3:BR,CO;airbnb:3"      # site:vagas[:países]
    FROTA_SITES="ifood:3" python3 frota_servico.py
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import queue
import signal
import threading
import time

import frota_navegacao as fn

AMBIENTE = "desenvolvimento" if os.environ.get("RADAR_AMBIENTE", "").strip() == "desenvolvimento" else "producao"
BATIMENTO_S = 30
POR_VAGA = 2                     # tarefas entregues à frota por vaga: fila curta, o resto espera no banco

# O REGISTRO: tipo → (módulo, função de tarefa). A função recebe a `Pagina` da frota e os argumentos da tarefa.
REGISTRO = {
    "teste.ip": ("frota_servico", "tarefa_teste_ip"),
    "aquecer": ("frota_servico", "tarefa_aquecer"),
    "ifood.ponto": ("extrair_ifood", "um_ponto_frota"),
    "airbnb.caixa": ("extrair_airbnb", "caixa_frota"),
    "airbnb.ficha": ("detalhar_airbnb", "ficha_frota"),
}
# O QUE CADA SITE PEDE DA FROTA, medido em 17/09/2026. No iFood o cookie guardado carrega o estado de um IP que já
# posicionou uma praça — e com ele a coordenada não aplica mais (dois pontos morreram assim na primeira rodada pela
# fila); e o IP castigado em outro site serve, porque quem é recusado recebe a lista inteira.
CONFIG_DO_SITE = {
    "ifood": {"usar_cookie": False, "castigo": "indiferente"},
}

# A PÁGINA QUE AQUECE cada site ao subir o serviço: a verificação da primeira página é paga antes do primeiro job
AQUECER = {
    "ifood": "https://www.ifood.com.br/inicio",
    "airbnb": "https://www.airbnb.com.br/",
}


def _log(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def tarefa_teste_ip(p, **_):
    p.ir("https://api.ipify.org/?format=json", timeout=30000)
    return {"ip": json.loads(p.page.inner_text("body"))["ip"]}


def tarefa_aquecer(p, url, **_):
    p.ir(url, timeout=120000, http_bloqueio=False)
    p.page.wait_for_timeout(8000)
    return {"url": p.page.url}


def _embrulho(p, _funcao, **kw):
    """O resultado leva quem executou: a sessão, o IP e o dono da vaga (o painel da frota cruza por eles)."""
    valor = _funcao(p, **kw)
    return {"valor": valor, "sessao_id": p.sessao_id, "proxy_id": p.proxy_id, "dono": p._s.dono}


class Posto:
    """Um site nesta máquina: a frota, o laço que pega tarefas e o que grava os resultados."""

    def __init__(self, site, vagas, paises):
        self.site, self.vagas = site, vagas
        self.frota = fn.Frota(site, navegadores=vagas, paises=paises, tentativas=3, processo="frota_servico",
                              log=_log, **CONFIG_DO_SITE.get(site, {}))
        self.em_voo = {}
        self.prontas = queue.Queue()
        self.parar = threading.Event()
        self.contagem = {"pegas": 0, "ok": 0, "erro": 0}
        self.laco = threading.Thread(target=self._laco, name="posto-%s" % site, daemon=True)
        if site in AQUECER:
            for _ in range(vagas):
                self.frota.enviar(_embrulho, _funcao=tarefa_aquecer, url=AQUECER[site])
        self.laco.start()

    def _funcao(self, tipo):
        mod, nome = REGISTRO[tipo]
        return getattr(importlib.import_module(mod), nome)

    def _fim(self, tid, estado, resultado=None, erro=None, dono=None):
        self.frota.banco.executar(
            """update navegacao.tarefa set estado = %s, resultado = %s::jsonb, erro = %s, dono = coalesce(%s, dono),
                      terminado_em = now(), visto_em = now()
                where id = %s and estado = 'rodando'""",
            (estado, json.dumps(resultado, ensure_ascii=False, default=str) if resultado is not None else None,
             (erro or "")[:500] or None, dono, tid))
        self.contagem[estado] = self.contagem.get(estado, 0) + 1

    def _gravar_prontas(self):
        n = 0
        while True:
            try:
                tid, fut = self.prontas.get_nowait()
            except queue.Empty:
                return n
            n += 1
            self.em_voo.pop(tid, None)
            e = fut.exception()
            if e is not None:
                self._fim(tid, "erro", erro="%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else ""))
            else:
                r = fut.result()
                self._fim(tid, "ok", resultado=r["valor"], dono=r["dono"])

    def _laco(self):
        b = self.frota.banco
        batimento = 0.0
        while not self.parar.is_set():
            try:
                gravou = self._gravar_prontas()
                pegou = 0
                livres = self.vagas * POR_VAGA - len(self.em_voo)
                if livres > 0:
                    linhas = b.executar("select id, tipo, argumentos from navegacao.pegar_tarefas(%s, %s, %s, %s)",
                                        (AMBIENTE, self.site, fn.DONO_MAQUINA, livres), buscar="todos") or []
                    for tid, tipo, args in linhas:
                        pegou += 1
                        self.contagem["pegas"] += 1
                        if tipo not in REGISTRO:
                            self._fim(tid, "erro", erro="tipo fora do registro do serviço: %s" % tipo)
                            continue
                        try:
                            funcao = self._funcao(tipo)
                        except Exception as e:                    # noqa: BLE001
                            self._fim(tid, "erro", erro="não importou %s: %s" % (tipo, e))
                            continue
                        fut = self.frota.enviar(_embrulho, _funcao=funcao, **(args or {}))
                        self.em_voo[tid] = fut
                        fut.add_done_callback(lambda f, tid=tid: self.prontas.put((tid, f)))
                if self.em_voo and time.time() - batimento > BATIMENTO_S:
                    b.executar("""update navegacao.tarefa set visto_em = now()
                                   where id = any(%s) and estado = 'rodando'""", (list(self.em_voo),))
                    batimento = time.time()
                if not pegou and not gravou:
                    self.parar.wait(2)
            except Exception as e:                                # noqa: BLE001
                _log("posto %s: o laço falhou (%s: %s) — tenta de novo em 15 s" % (self.site, type(e).__name__, e))
                self.parar.wait(15)

    def encerrar(self, espera_s=90):
        """Para de pegar, espera o que está em voo, devolve à fila o que sobrar e fecha os navegadores."""
        self.parar.set()
        self.laco.join(timeout=10)
        fim = time.time() + espera_s
        while self.em_voo and time.time() < fim:
            self._gravar_prontas()
            time.sleep(1)
        self._gravar_prontas()
        if self.em_voo:
            self.frota.banco.executar("""update navegacao.tarefa set estado = 'fila', maquina = null, dono = null
                                          where id = any(%s) and estado = 'rodando'""", (list(self.em_voo),))
            _log("posto %s: %d tarefa(s) em voo devolvidas à fila" % (self.site, len(self.em_voo)))
        self.frota.fechar(esperar=False)


def ler_sites(texto):
    """'ifood:3:BR,CO;airbnb:3' → [(site, vagas, países)]."""
    saida = []
    for parte in (texto or "").split(";"):
        c = [x.strip() for x in parte.split(":") if x.strip()]
        if not c:
            continue
        paises = tuple(x.strip().upper() for x in c[2].split(",")) if len(c) > 2 else ("BR", "CO")
        saida.append((c[0], int(c[1]) if len(c) > 1 else 2, paises))
    return saida


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sites", default=os.environ.get("FROTA_SITES", ""), help="site:vagas[:países];...")
    a = ap.parse_args()
    sites = ler_sites(a.sites)
    if not sites:
        ap.error("informe --sites ou FROTA_SITES, ex.: ifood:3;airbnb:3")
    _log("frota permanente · %s · ambiente %s · %s" % (fn.DONO_MAQUINA, AMBIENTE,
                                                      ", ".join("%s %d vagas %s" % (s, v, "/".join(p))
                                                                for s, v, p in sites)))
    postos = [Posto(s, v, p) for s, v, p in sites]
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    ultimo = 0.0
    while not parar.wait(5):
        if time.time() - ultimo > 60:
            for po in postos:
                _log("posto %s · em voo %d · %s · frota %s" % (po.site, len(po.em_voo), json.dumps(po.contagem),
                                                              json.dumps(po.frota.resumo())))
            ultimo = time.time()
    _log("encerrando: devolvendo o que está em voo e fechando os navegadores")
    for po in postos:
        po.encerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
