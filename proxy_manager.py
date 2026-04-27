"""
proxy_manager.py — ComercialRadar

Gerenciador de proxies locais por worker.

O que faz:
- Sobe vários proxies locais no início.
- Cada worker recebe uma porta local funcional.
- A cada ROTATE_ITEMS itens, troca o worker para outro proxy/porta.
- Prefere proxies que ficaram sem uso por mais tempo.
- Evita reaproveitar proxy recém usado quando houver opção.
"""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_PORT = 18200
ROTATE_ITEMS = 10
MAX_PROXIES = 30
PROXY_REUSE_COOLDOWN_SEC = 120

WEBSHARE_API_URL = (
    "https://proxy.webshare.io/api/v2/proxy/list/download/"
    "vpaocfaptrnkwvsosapbyooavlfnqdhehzjopvxe/-/any/username/backbone/-/"
    "?plan_id=13228096"
)


def _carregar_proxies() -> list:
    try:
        import urllib.request

        req = urllib.request.Request(
            WEBSHARE_API_URL,
            headers={"User-Agent": "ComercialRadar/1.0"},
        )
        data = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        proxies = []

        for linha in data.splitlines():
            linha = linha.strip()
            if not linha:
                continue

            partes = linha.split(":")
            if len(partes) == 4:
                proxies.append((partes[0], int(partes[1]), partes[2], partes[3]))

        if proxies:
            print(f"   🌐 {len(proxies)} proxies carregados da API Webshare")
            txt = Path(__file__).parent / "Webshare_100_proxies.txt"
            txt.write_text(
                "\n".join("{}:{}:{}:{}".format(*px) for px in proxies),
                encoding="utf-8",
            )
            return proxies

    except Exception as e:
        print(f"   ⚠️  API Webshare falhou ({e}) — usando cache local")

    txt = Path(__file__).parent / "Webshare_100_proxies.txt"
    if txt.exists():
        proxies = []
        for linha in txt.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha:
                continue

            partes = linha.split(":")
            if len(partes) == 4:
                proxies.append((partes[0], int(partes[1]), partes[2], partes[3]))

        if proxies:
            print(f"   🌐 {len(proxies)} proxies carregados do cache local")
            return proxies

    return [("p.webshare.io", 80, f"ezesjygi-{i}", "m8ymuaquh5r1") for i in range(1, 101)]


PROXIES = _carregar_proxies()


def _porta_livre(preferida: int) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferida))
        return preferida
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _porta_aberta(porta: int, timeout: float = 0.7) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", porta), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _subir_proxy(porta_local: int, proxy_idx: int) -> subprocess.Popen:
    host, port, user, pwd = PROXIES[proxy_idx % len(PROXIES)]

    cmd = [
        sys.executable,
        "-m",
        "proxy",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(porta_local),
        "--proxy-pool",
        f"http://{user}:{pwd}@{host}:{port}",
        "--log-level",
        "ERROR",
    ]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


async def _aguardar_proxy(slot: int, porta: int, proc: subprocess.Popen, tentativas: int = 30) -> tuple[int, bool]:
    for _ in range(tentativas):
        await asyncio.sleep(0.35)

        if proc.poll() is not None:
            out, err = "", ""
            try:
                out, err = proc.communicate(timeout=1)
            except Exception:
                pass

            print(f"\n   ❌ proxy slot {slot} morreu na porta {porta}")
            if out.strip():
                print(f"   STDOUT: {out.strip()[:500]}")
            if err.strip():
                print(f"   STDERR: {err.strip()[:500]}")
            return slot, False

        if _porta_aberta(porta):
            return slot, True

    print(f"\n   ❌ proxy slot {slot} não abriu porta {porta}")
    return slot, False


class ProxyManager:
    def __init__(self, n_workers: int = 10):
        self.n_workers = max(1, int(n_workers))

        # Sobe mais proxies do que workers para sempre existir proxy "descansado" na rotação.
        desejado = max(self.n_workers * 3, self.n_workers + 5)
        self._n_slots = min(desejado, MAX_PROXIES, len(PROXIES))

        self._procs = []
        self._portas = []
        self._ativos = set()

        self._slots = {}
        self._contadores = {}
        self._last_used = {}
        self._bloqueados = set()
        self._lock = asyncio.Lock()

    async def start(self):
        print(f"   ⏳ Subindo {self._n_slots} proxies locais para {self.n_workers} worker(s)...")

        self._procs.clear()
        self._portas.clear()
        self._ativos.clear()
        self._last_used.clear()

        for slot in range(self._n_slots):
            porta = _porta_livre(BASE_PORT + slot)
            self._portas.append(porta)

            host, remote_port, user, _ = PROXIES[slot % len(PROXIES)]
            print(f"      slot {slot:02d}: 127.0.0.1:{porta} → {user}@{host}:{remote_port}")

            proc = _subir_proxy(porta, slot)
            self._procs.append(proc)
            self._last_used[slot] = 0.0

        checks = await asyncio.gather(*[
            _aguardar_proxy(slot, self._portas[slot], self._procs[slot])
            for slot in range(self._n_slots)
        ])

        for slot, ok in checks:
            if ok:
                self._ativos.add(slot)
            else:
                try:
                    self._procs[slot].terminate()
                except Exception:
                    pass

        ativos = sorted(self._ativos)
        print(f"   🔌 {len(ativos)}/{self._n_slots} proxies prontos | rotação a cada {ROTATE_ITEMS} itens")

        if not ativos:
            print("   ❌ Nenhum proxy local ficou pronto.")
            return

        now = time.time()

        for wid in range(self.n_workers):
            slot = ativos[wid % len(ativos)]
            self._slots[wid] = slot
            self._contadores[wid] = 0
            self._last_used[slot] = now

            host, _, user, _ = PROXIES[slot % len(PROXIES)]
            print(f"      W{wid} usando slot {slot:02d} porta {self._portas[slot]} → {user}@{host}")

    def has_proxy(self) -> bool:
        return bool(self._ativos)

    def porta(self, worker_id: int) -> int | None:
        if not self._ativos:
            return None

        slot = self._slots.get(worker_id)
        if slot not in self._ativos:
            ativos = sorted(self._ativos)
            slot = ativos[worker_id % len(ativos)]
            self._slots[worker_id] = slot

        return self._portas[slot]

    def _slot_para_worker(self, worker_id: int) -> int | None:
        if not self._ativos:
            return None

        now = time.time()
        em_uso_por_outros = {
            slot for wid, slot in self._slots.items()
            if wid != worker_id
        }

        candidatos = [
            slot for slot in self._ativos
            if slot not in self._bloqueados
            and slot not in em_uso_por_outros
            and slot != self._slots.get(worker_id)
        ]

        if not candidatos:
            candidatos = [
                slot for slot in self._ativos
                if slot not in self._bloqueados
                and slot != self._slots.get(worker_id)
            ]

        if not candidatos:
            candidatos = [
                slot for slot in self._ativos
                if slot not in self._bloqueados
            ]

        if not candidatos:
            return self._slots.get(worker_id)

        frios = [
            slot for slot in candidatos
            if now - self._last_used.get(slot, 0.0) >= PROXY_REUSE_COOLDOWN_SEC
        ]

        pool = frios if frios else candidatos

        # Escolhe o menos usado recentemente.
        return min(pool, key=lambda s: self._last_used.get(s, 0.0))

    async def registrar_item(self, worker_id: int, bloqueado: bool = False) -> bool:
        if not self._ativos:
            return False

        async with self._lock:
            atual = self._slots.get(worker_id)

            if bloqueado and atual is not None:
                self._bloqueados.add(atual)
                motivo = "bloqueio"
            else:
                self._contadores[worker_id] = self._contadores.get(worker_id, 0) + 1
                if self._contadores[worker_id] < ROTATE_ITEMS:
                    return False
                motivo = f"{ROTATE_ITEMS} itens"

            novo = self._slot_para_worker(worker_id)
            if novo is None or novo == atual:
                self._contadores[worker_id] = 0
                return False

            self._slots[worker_id] = novo
            self._contadores[worker_id] = 0
            self._last_used[novo] = time.time()

            host, _, user, _ = PROXIES[novo % len(PROXIES)]
            porta = self._portas[novo]
            ts = time.strftime("%H:%M:%S")

            descanso = time.time() - self._last_used.get(novo, 0.0)
            print(f"\n  [{ts}] W{worker_id} 🔄 proxy por {motivo} → slot {novo:02d} porta {porta} | {user}@{host}")
            return True

    async def stop(self):
        for proc in self._procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        print("   🔌 Proxies encerrados")
