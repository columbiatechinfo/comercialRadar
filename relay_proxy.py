# -*- coding: utf-8 -*-
"""Proxy por relay local — o único jeito que o Google Maps aceita.

O QUE FOI MEDIDO, e que torna isto necessário

Carregando `google.com/maps` no Chromium, do mesmo notebook, no mesmo minuto:

    sem proxy                        abriu   em  4,8 s
    credencial direta no Playwright  FALHOU  em 35,3 s
    relay local (este módulo)        abriu   em  1,7 s

O erro `Page.goto: Timeout 35000ms exceeded` que enchia o log não era o Google
bloqueando, nem o scraper, nem a extração: era o Chromium recebendo um proxy
COM usuário e senha. Autenticação de proxy pendura a navegação para o
`google.com` — e só para ele; `example.com` passava normalmente pelo mesmo IP.

COMO ISTO RESOLVE

Sobe um proxy na própria máquina que carrega a credencial, e aponta o navegador
para `127.0.0.1:<porta>`. Do ponto de vista do Chromium não existe proxy
autenticado: existe um proxy aberto em localhost. A saída continua sendo o IP
da Webshare.

Não é invenção nova — é o que `search_pois.py` sempre fez, e é por isso que
aquele pipeline fazia 40 lojas a 6 s cada enquanto a ferramenta do chat tomava
timeout. Dois caminhos para a mesma coisa, um funcionando e outro não.

A INTERFACE É A DO ProxyPool DE PROPÓSITO

`acquire_blocking`, `release` e `mark_cooldown` têm a mesma forma. Quem consumia
o pool antigo troca o objeto e nada mais — nenhum chamador precisa saber que o
proxy agora passa por um processo local.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import proxy_manager as pm

# Segundos para o relay ficar de pé antes de o navegador apontar para ele.
# Apontar antes derruba a primeira navegação com ERR_PROXY_CONNECTION_FAILED,
# que se parece com IP ruim e manda depurar no lugar errado.
ESPERA_SUBIR = 4.0


class PiscinaRelay:
    """Relays locais, um por vaga, cada um sobre um usuário do backbone.

    Usuários diferentes (`ezesjygi-1`, `-2`, …) saem por IPs diferentes, que é
    o que dá rotação. A vaga é do relay; o IP quem escolhe é a Webshare.
    """

    def __init__(self, vagas: int = 4):
        self.vagas = max(1, int(vagas))
        self._procs: dict[int, subprocess.Popen] = {}
        # TODO relay que nasce entra aqui e só sai quando se CONFIRMA morto.
        # `_procs` guarda quem está em uso; sozinho ele perdia o rastro dos que
        # saíam por cooldown, e foi assim que dez vagas viraram quarenta
        # processos vivos, com a RAM livre em 0,2 GB.
        self._nascidos: list = []
        # Teto de nascimentos. Sem ele, um IP que sempre falha faz o laço criar
        # processo para sempre, e o limite da máquina chega antes do fim.
        self._nasceram = 0
        self.max_nascimentos = self.vagas * 4
        self._portas: dict[int, int] = {}
        self._livres: list[int] = []
        self._gelo: dict[int, float] = {}     # vaga -> quando sai do cooldown
        self._trava = asyncio.Lock()
        self._subiu = False

    # ── ciclo de vida ────────────────────────────────────────────────────────

    def start(self) -> "PiscinaRelay":
        """Compatível com `ProxyPool().start()`: devolve a si mesmo."""
        return self

    @staticmethod
    def _matar(proc) -> None:
        """Pede, espera, e obriga. Pedir sem conferir é o mesmo que não pedir.

        No Windows `terminate()` sinaliza; não garante. Foi o que deixou trinta
        relays vivos e sem dono durante a prova de carga.
        """
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def _recolher(self) -> int:
        """Tira da lista os que já morreram. Devolve quantos seguem vivos."""
        self._nascidos = [p for p in self._nascidos if p.poll() is None]
        return len(self._nascidos)

    async def _garantir(self, vaga: int) -> bool:
        """Sobe o relay da vaga, se ainda não estiver de pé."""
        proc = self._procs.get(vaga)
        if proc is not None and proc.poll() is None:
            return True

        if not pm.PROXIES:
            return False

        # O teto conta NASCIMENTOS, não vivos: um laço que cria e mata sem
        # parar consome portas e CPU do mesmo jeito.
        if self._nasceram >= self.max_nascimentos:
            return False
        # e nunca mais relays vivos do que vagas — este é o numero que a
        # memoria da maquina aguenta
        if self._recolher() >= self.vagas:
            return False

        host, porta_remota, user, pwd = pm.PROXIES[vaga % len(pm.PROXIES)]
        porta = pm._porta_livre(pm.BASE_PORT + vaga)

        # UM ACEITADOR E UM TRABALHADOR (18/09/2026): sem os dois, o proxy.py abre um de cada POR NUCLEO — no i9 eram
        # 33 processos por navegador, 792 com a etapa 9 das duas cidades, para servir um navegador so.
        novo = subprocess.Popen(
            [sys.executable, "-m", "proxy", "--hostname", "127.0.0.1",
             "--port", str(porta), "--proxy-pool",
             f"http://{user}:{pwd}@{host}:{porta_remota}",
             "--num-acceptors", "1", "--num-workers", "1",
             "--log-level", "ERROR"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")

        self._nascidos.append(novo)      # rastreado ANTES de qualquer espera
        self._nasceram += 1

        await asyncio.sleep(ESPERA_SUBIR)
        if novo.poll() is not None:
            return False        # morreu ao subir: vaga não serve

        self._procs[vaga] = novo
        self._portas[vaga] = porta
        return True

    # ── interface igual à do ProxyPool ───────────────────────────────────────

    async def acquire_blocking(self, intervalo: float = 2.0,
                               tentativas: int = 30) -> dict | None:
        """Empresta uma vaga. Devolve o formato que a HumanSession espera.

        `username` e `password` vazios não são descuido: é justamente o ponto.
        Quem autentica é o relay; mandar credencial ao Chromium é o defeito que
        este módulo existe para não repetir.
        """
        for _ in range(max(1, tentativas)):
            async with self._trava:
                if not self._subiu:
                    self._livres = list(range(self.vagas))
                    self._subiu = True
                agora = time.time()
                vaga = next((v for v in self._livres
                             if self._gelo.get(v, 0) <= agora), None)
                if vaga is not None:
                    self._livres.remove(vaga)

            if vaga is None:
                await asyncio.sleep(intervalo)
                continue

            if await self._garantir(vaga):
                return {"server": f"http://127.0.0.1:{self._portas[vaga]}",
                        "username": "", "password": "", "_vaga": vaga}

            async with self._trava:      # relay não subiu: devolve e tenta outra
                self._livres.append(vaga)
            await asyncio.sleep(intervalo)
        return None

    async def release(self, proxy: dict) -> None:
        vaga = (proxy or {}).get("_vaga")
        if vaga is None:
            return
        async with self._trava:
            if vaga not in self._livres:
                self._livres.append(vaga)

    async def mark_cooldown(self, proxy: dict, segundos: int = 600) -> None:
        """Põe a vaga no gelo e MATA o relay, para o próximo uso pegar outro IP.

        Só devolver a vaga não adiantaria: o relay vivo mantém a mesma sessão
        de saída, e o IP que acabou de falhar voltaria igual.
        """
        vaga = (proxy or {}).get("_vaga")
        if vaga is None:
            return
        self._gelo[vaga] = time.time() + segundos
        proc = self._procs.pop(vaga, None)
        self._portas.pop(vaga, None)
        self._matar(proc)        # e `_nascidos` continua sabendo dele
        self._recolher()

    def encerrar(self) -> None:
        """Mata TODOS os relays que nasceram, não só os em uso.

        Percorrer `_procs` deixava vivos os que tinham saído por cooldown — que
        são justamente os mais numerosos quando os IPs estão ruins.
        """
        for proc in list(self._nascidos) + list(self._procs.values()):
            self._matar(proc)
        self._nascidos.clear()
        self._procs.clear()
        self._portas.clear()
        self._nasceram = 0

    @property
    def vivos(self) -> int:
        """Quantos relays respiram agora — para medir, e para testar."""
        return self._recolher()

    @property
    def total(self) -> int:
        return self.vagas
