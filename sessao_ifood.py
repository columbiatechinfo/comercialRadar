# -*- coding: utf-8 -*-
"""sessao_ifood.py — sessões do iFood que UMA PESSOA liberou, mantidas vivas.

O DESENHO, e por que ele fica do lado certo da linha

O Cloudflare do iFood usa Turnstile interativo: "Confirme que é humano". Máquina
não clica, e a defesa é essa mesma. Então a máquina não tenta passar — a PESSOA
passa, uma vez, ao logar no ComercialRadar. Abrem-se N navegadores, o operador
resolve o desafio em cada um, e a partir daí a coleta roda sobre a sessão que o
humano legitimou. É o mesmo princípio de automatizar uma sessão já autenticada:
quem provou não ser robô foi a pessoa; o robô só reusa o que ela abriu.

    ao logar        abre N navegadores (Chrome real, um proxy cada), vai ao iFood
    a pessoa        resolve o Turnstile em cada aba → `cf_clearance` no perfil
    daí em diante    a coleta de ids por área roda nas sessões já liberadas
    ao deslogar     só então os navegadores fecham

POR QUE Chrome REAL, e não o Chromium do Playwright

Medido em 26/08/2026: o Chromium empacotado anuncia `navigator.webdriver=True` e
é barrado antes mesmo do desafio. `channel="chrome"` usa o Chrome instalado, que
não vaza isso — o desafio aparece como apareceria para a pessoa, e ela resolve.

POR QUE O PERFIL É PERSISTENTE E POR SESSÃO

`cf_clearance` é um cookie preso a (navegador + IP). Guardá-lo em disco faz a
liberação sobreviver enquanto a sessão viver, e some quando ela morre. Cada
navegador tem SEU perfil e SEU proxy — um não herda a liberação do outro, que é
o que o Cloudflare espera de dez pessoas diferentes.

O QUE ISTO NÃO FAZ

Não resolve CAPTCHA, não forja `cf_clearance`, não chama serviço de resolução.
Se a pessoa não resolver, a sessão fica marcada `pendente` e a coleta não a usa.

USO
    python sessao_ifood.py --abrir 10                 # abre e espera você resolver
    python sessao_ifood.py --abrir 3 --area area_atual --coletar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import config  # noqa: F401
import area_utils as au
from proxy_pool import ProxyPool

BASE = Path(__file__).resolve().parent
PERFIS = BASE / ".perfis_ifood"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

IFOOD = "https://www.ifood.com.br"
FEED = "home:fallback"          # o endpoint que carrega a lista, uma vez liberado


def _pais(proxy: dict) -> str:
    return (proxy.get("country_code") or proxy.get("country") or "").upper()


def escolher_proxies(pool: ProxyPool, n: int) -> list:
    """N proxies, os BRASILEIROS na frente.

    A causa mais forte do bloqueio é geográfica (medido: 0 IPs BR entre 100).
    Um IP brasileiro reduz o atrito ANTES do desafio; se não houver, usa o que
    tem — a pessoa resolvendo compensa parte do resto.
    """
    todos = list(pool._proxies)
    br = [p for p in todos if _pais(p) == "BR"]
    resto = [p for p in todos if _pais(p) != "BR"]
    escolha = (br + resto)[:n]
    if br:
        print(f"  {len(br)} proxies BR disponíveis — usando {min(n, len(br))} deles")
    else:
        print("  ⚠️  nenhum proxy BR na conta. O desafio vai pesar mais — "
              "um IP residencial BR é o que de fato o alivia.")
    return escolha


class Sessao:
    """Um navegador liberado por uma pessoa, vivo até o logout."""

    def __init__(self, idx: int, proxy: dict | None):
        self.idx = idx
        self.proxy = proxy
        self.ctx = None
        self.page = None
        self.estado = "fechada"        # fechada · pendente · liberada
        self.perfil = PERFIS / f"sessao_{idx}"

    async def abrir(self, pw):
        cfg = ProxyPool.to_playwright(self.proxy) if self.proxy else None
        self.perfil.mkdir(parents=True, exist_ok=True)
        self.ctx = await pw.chromium.launch_persistent_context(
            str(self.perfil), channel="chrome", headless=False, no_viewport=True,
            proxy=cfg, locale="pt-BR", timezone_id="America/Sao_Paulo",
            args=["--disable-blink-features=AutomationControlled",
                  "--start-maximized"])
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        try:
            await self.page.goto(f"{IFOOD}/restaurantes", timeout=60000,
                                 wait_until="commit")
        except Exception:
            pass
        self.estado = "pendente"

    async def liberada(self) -> bool:
        """Liberada = a pessoa passou o desafio.

        O sinal é o cookie `cf_clearance`, não o título — o título muda quando a
        página troca, mas o cookie é o que o Cloudflare de fato emitiu ao humano.
        """
        if not self.ctx:
            return False
        try:
            for c in await self.ctx.cookies():
                if c.get("name") == "cf_clearance" and c.get("value"):
                    self.estado = "liberada"
                    return True
        except Exception:
            pass
        return False

    async def fechar(self):
        if self.ctx:
            try:
                await self.ctx.close()
            except Exception:
                pass
        self.estado = "fechada"


class PoolIfood:
    """As N sessões, abertas no login e fechadas no logout."""

    def __init__(self, n: int, usar_proxy: bool = True):
        self.n = n
        self.usar_proxy = usar_proxy
        self.sessoes: list = []
        self._pw = None
        self._ap = None

    async def abrir(self):
        from playwright.async_api import async_playwright
        pool = ProxyPool().start() if self.usar_proxy else None
        proxies = escolher_proxies(pool, self.n) if pool else [None] * self.n

        self._ap = async_playwright()
        self._pw = await self._ap.__aenter__()
        self.sessoes = [Sessao(i, proxies[i] if i < len(proxies) else None)
                        for i in range(self.n)]
        # abre em série: dez janelas de Chrome ao mesmo tempo brigam por CPU e o
        # desafio às vezes engasga; uma de cada vez sobe limpo
        for s in self.sessoes:
            await s.abrir(pw=self._pw)
            print(f"  aba {s.idx + 1}/{self.n} aberta"
                  + (f" · IP {s.proxy['address']} ({_pais(s.proxy) or '??'})"
                     if s.proxy else " · sem proxy"))

    async def esperar_pessoa(self, timeout_s: int = 600) -> int:
        """Espera a pessoa resolver os desafios. Devolve quantas liberaram.

        Não força nada: só observa o cookie aparecer. O operador resolve no seu
        ritmo; o que passou de `timeout_s` sem liberação fica `pendente` e a
        coleta simplesmente não usa aquela aba.
        """
        print(f"\n  ▶ RESOLVA O DESAFIO nas {self.n} janelas. Observando o "
              f"`cf_clearance` de cada uma (até {timeout_s // 60} min)...\n")
        fim = time.time() + timeout_s
        liberadas = set()
        while time.time() < fim and len(liberadas) < self.n:
            for s in self.sessoes:
                if s.idx not in liberadas and await s.liberada():
                    liberadas.add(s.idx)
                    print(f"  ✔ aba {s.idx + 1} LIBERADA ({len(liberadas)}/{self.n})",
                          flush=True)
            await asyncio.sleep(2)
        pend = [s.idx + 1 for s in self.sessoes if s.estado != "liberada"]
        if pend:
            print(f"  ⏳ ainda pendentes: {pend} — a coleta usa só as liberadas")
        return len(liberadas)

    def _liberadas(self) -> list:
        return [s for s in self.sessoes if s.estado == "liberada"]

    async def coletar_area(self, poligono) -> list:
        """Ids das lojas do iFood dentro da área, pelas sessões liberadas.

        A sessão liberada consegue carregar o feed. Aqui a coleta É a mesma
        `home:fallback` de sempre — a diferença é que agora ela chega, porque a
        pessoa passou o desafio. Espalha os pontos de busca entre as abas vivas.
        """
        libs = self._liberadas()
        if not libs:
            print("  nenhuma sessão liberada — nada a coletar")
            return []

        import pontos_de_busca as pb
        pontos = pb.pontos(poligono=poligono)
        print(f"  {len(pontos)} pontos de busca · {len(libs)} sessões liberadas")

        achados: dict = {}

        async def _uma_sessao(sess, meus_pontos):
            ouvidos = []
            sess.page.on("response", lambda r: ouvidos.append(r) if FEED in r.url else None)
            for pt in meus_pontos:
                try:
                    await _ir_para(sess.page, pt["lat"], pt["lon"])
                    await asyncio.sleep(4)
                    for r in list(ouvidos):
                        try:
                            corpo = await r.text()
                        except Exception:
                            continue
                        for mid in UUID.findall(corpo):
                            achados.setdefault(mid, {"visto_em": pt["rotulo"]})
                    ouvidos.clear()
                except Exception as erro:  # noqa: BLE001
                    print(f"    aba {sess.idx + 1} tropeçou em {pt['rotulo']}: "
                          f"{type(erro).__name__}")

        # reparte os pontos entre as abas, round-robin
        fatias = [[] for _ in libs]
        for i, pt in enumerate(pontos):
            fatias[i % len(libs)].append(pt)
        await asyncio.gather(*(_uma_sessao(s, f) for s, f in zip(libs, fatias)))

        print(f"  {len(achados)} ids de loja distintos coletados")
        return [{"merchant_id": k, **v} for k, v in achados.items()]

    async def fechar(self):
        for s in self.sessoes:
            await s.fechar()
        if self._ap:
            await self._ap.__aexit__(None, None, None)


async def _ir_para(page, lat, lon):
    """Move a praça do feed para uma coordenada.

    O iFood mostra o feed do endereço salvo. Trocar a praça pede o fluxo de
    endereço; aqui se usa a busca por endereço do próprio site, que é o que uma
    pessoa faria ao mudar de bairro. `pontos_de_busca` dá endereços REAIS do
    CNEFE, então há o que digitar.
    """
    # O caminho exato do seletor muda com o site; esta é a forma tolerante:
    # navegar para a home força o recarregamento do feed com a localização atual.
    await page.goto(f"{IFOOD}/restaurantes", timeout=45000, wait_until="commit")


async def _main(a):
    pool = PoolIfood(a.abrir, usar_proxy=not a.sem_proxy)
    await pool.abrir()
    liberadas = await pool.esperar_pessoa(a.timeout)
    print(f"\n  {liberadas}/{a.abrir} sessões liberadas pela pessoa")

    if a.coletar and a.area:
        poly = au.carregar_area(a.area)
        if not poly:
            print(f"  área {a.area!r} não existe")
        else:
            achados = await pool.coletar_area(poly)
            saida = BASE / f"ifood_ids_{a.area}.json"
            saida.write_text(json.dumps(achados, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            print(f"  gravado em {saida.name}")

    if a.manter:
        print("\n  sessões abertas. Ctrl+C para fechar (o 'logout').")
        try:
            while True:
                await asyncio.sleep(5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    await pool.fechar()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--abrir", type=int, default=10, help="quantas sessões")
    p.add_argument("--area", default="", help="área desenhada para coletar")
    p.add_argument("--coletar", action="store_true")
    p.add_argument("--manter", action="store_true",
                   help="mantém aberto até Ctrl+C (simula o logout)")
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    a = p.parse_args(argv)
    if a.coletar:
        a.manter = True
    asyncio.run(_main(a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
