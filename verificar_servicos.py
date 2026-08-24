# -*- coding: utf-8 -*-
"""verificar_servicos.py — o sistema está inteiro? Rode depois de todo reinício.

POR QUE ISTO EXISTE

Em 24/08/2026 as duas máquinas foram reiniciadas. Os containers subiram todos
saudáveis, o Nominatim e o OSRM voltaram sozinhos — e **os dois bancos ficaram
inalcançáveis**. Nada acusou: o `docker ps` mostrava `healthy`, e só uma tentativa
de conexão de fora revelava o problema.

Descobrir isso levou meia hora de investigação manual. Da segunda vez leva o
tempo de rodar este arquivo.

VERIFICAÇÃO FUNCIONAL, NÃO DE PORTA

Porta aberta com serviço devolvendo lixo é o pior caso possível: tudo parece
bem e o dado sai errado. Então cada serviço responde a uma **pergunta real** —
o Nominatim geocodifica um endereço conhecido, o OSRM traça uma rota, o Ollama
gera uma palavra, o banco conta linhas.

O QUE ELE NÃO FAZ

Não conserta. Diz o que está quebrado e qual é o caminho, porque metade dos
problemas de infra tem mais de uma saída e a escolha é de quem opera.

Uso:
    python verificar_servicos.py
    python verificar_servicos.py --rapido    # pula o LLM, que é o mais lento
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request

import config  # noqa: F401  (carrega o .env)

I9 = "100.115.117.49"
DGX = "100.85.164.54"
UA = {"User-Agent": "ComercialRadar/verificacao"}

OK, RUIM, AVISO = "OK ", ">> ", " ~ "


def _http(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=UA)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if b[:2] == b"\x1f\x8b":                 # o IBGE responde gzip sem avisar
        import gzip
        b = gzip.decompress(b)
    return json.loads(b.decode("utf-8")), time.time() - t0


def _porta(host: str, porta: int, segundos: float = 4) -> bool:
    s = socket.socket()
    s.settimeout(segundos)
    try:
        s.connect((host, porta))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _linha(estado: str, nome: str, detalhe: str = "") -> None:
    print(f"  {estado} {nome:<26}{detalhe}", flush=True)


# ── Bancos ───────────────────────────────────────────────────────────────────

def banco_produto() -> bool:
    import base_comum as bc
    try:
        t0 = time.time()
        con = bc.conectar()
        with con.cursor() as k:
            k.execute("select count(*) from comercialradar.pois")
            n = k.fetchone()[0]
        con.close()
        _linha(OK, "banco do produto", f"{n:,} POIs · {time.time()-t0:.1f}s")
        return True
    except Exception as e:
        _linha(RUIM, "banco do produto", f"{type(e).__name__}")
        # A CAUSA MAIS COMUM, e a que custou meia hora da primeira vez.
        if not _porta(I9, 5444):
            print("       A porta 5444 não responde de fora, mas o container "
                  "costuma estar de pé.\n"
                  "       O encaminhamento do Windows para o WSL perde a amarra "
                  "no reinício:\n"
                  "       as regras existem mas ficam presas em 127.0.0.1. "
                  "Reaplicá-las resolve —\n"
                  "       `netsh interface portproxy delete/add v4tov4 "
                  "listenaddress=100.115.117.49 ...`\n"
                  "       (reiniciar o iphlpsvc NÃO basta: a regra precisa ser "
                  "reinserida).")
        return False


def banco_referencia() -> bool:
    import base_comum as bc
    try:
        t0 = time.time()
        ref = bc.conectar_referencia()
        with ref.cursor() as k:
            k.execute("select count(*) from ibge_cnefe")
            n = k.fetchone()[0]
            k.execute("select count(*) from ibge_malha")
            m = k.fetchone()[0]
        ref.close()
        _linha(OK, "banco de referência",
               f"CNEFE {n:,} · malha {m:,} municípios · {time.time()-t0:.1f}s")
        return True
    except Exception as e:
        _linha(RUIM, "banco de referência", f"{type(e).__name__}")
        return False


# ── Geo ──────────────────────────────────────────────────────────────────────

ENDERECO_PROVA = "Avenida Getulio Vargas 5075, Canoas, RS, Brasil"


def nominatim() -> bool:
    base = (os.environ.get("NOMINATIM_URL") or "").rstrip("/")
    if not base:
        _linha(AVISO, "nominatim", "sem NOMINATIM_URL no .env")
        return True
    try:
        d, s = _http(base + "/search?" + urllib.parse.urlencode(
            {"q": ENDERECO_PROVA, "format": "jsonv2", "limit": 1}))
        if not d:
            _linha(RUIM, "nominatim", "respondeu VAZIO para endereço conhecido")
            return False
        _linha(OK, "nominatim", f"{d[0]['lat']},{d[0]['lon']} · {s:.2f}s")
        return True
    except Exception as e:
        _linha(RUIM, "nominatim", f"{type(e).__name__}")
        return False


def photon() -> bool:
    base = (os.environ.get("PHOTON_URL") or "").rstrip("/")
    if not base:
        _linha(AVISO, "photon", "sem PHOTON_URL no .env")
        return True
    try:
        d, s = _http(base + "/api?" + urllib.parse.urlencode(
            {"q": ENDERECO_PROVA, "limit": 1}))
        f = (d.get("features") or [None])[0]
        if not f:
            _linha(RUIM, "photon", "respondeu VAZIO para endereço conhecido")
            return False
        lo, la = f["geometry"]["coordinates"]
        _linha(OK, "photon", f"{la},{lo} · {s:.2f}s")
        return True
    except Exception as e:
        _linha(RUIM, "photon", f"{type(e).__name__}")
        return False


def osrm() -> bool:
    bom = True
    for var, perfil, rot in (("OSRM_URL", "driving", "osrm carro"),
                             ("OSRM_URL_A_PE", "foot", "osrm a pé")):
        base = (os.environ.get(var) or "").rstrip("/")
        if not base:
            _linha(AVISO, rot, f"sem {var} no .env")
            continue
        try:
            d, s = _http(f"{base}/route/v1/{perfil}/"
                         "-51.18,-29.91;-51.15,-29.93?overview=false")
            r = (d.get("routes") or [{}])[0]
            if d.get("code") != "Ok" or not r.get("distance"):
                _linha(RUIM, rot, f"code={d.get('code')} sem rota")
                bom = False
                continue
            _linha(OK, rot, f"{r['distance']/1000:.1f} km · base "
                            f"{d.get('data_version', '?')} · {s:.2f}s")
        except Exception as e:
            _linha(RUIM, rot, f"{type(e).__name__}")
            bom = False
    return bom


def searxng() -> bool:
    """A primeira URL que responder vale — a lista tem o i9 e o local."""
    for base in (os.environ.get("SEARXNG_URL") or "").split(","):
        base = base.strip().rstrip("/")
        if not base:
            continue
        try:
            d, s = _http(base + "/search?" + urllib.parse.urlencode(
                {"q": "hotel canoas rs", "format": "json"}), timeout=25)
            n = len(d.get("results") or [])
            if n:
                _linha(OK, "searxng", f"{n} resultados · {base} · {s:.2f}s")
                return True
        except Exception:
            continue
    _linha(RUIM, "searxng", "nenhuma das URLs respondeu")
    return False


# ── LLM ──────────────────────────────────────────────────────────────────────

def llm(rapido: bool = False) -> bool:
    base = (os.environ.get("OLLAMA_URL") or "").rstrip("/")
    if not base:
        _linha(AVISO, "llm local", "sem OLLAMA_URL no .env")
        return True
    try:
        d, s = _http(base + "/api/tags", timeout=20)
    except Exception as e:
        _linha(RUIM, "llm local", f"{base} · {type(e).__name__}")
        print("       Confira se o OLLAMA_URL aponta para a máquina certa: em "
              "24/08/2026 ele\n"
              "       apontava para a DGX, que não tem Ollama instalado.")
        return False

    modelos = [m.get("name") for m in (d.get("models") or [])]
    if not modelos:
        _linha(RUIM, "llm local", "nenhum modelo instalado")
        return False
    _linha(OK, "llm local", f"{len(modelos)} modelos · {', '.join(modelos[:3])}")
    if rapido:
        return True

    # GERAÇÃO DE VERDADE. `/api/tags` responde mesmo com a GPU fora do ar, e um
    # modelo que carrega mas não gera é indistinguível de um saudável até a
    # primeira rodada de produção.
    # O MODELO DE PRODUÇÃO primeiro. `qwen2.5vl:3b` também casa com o prefixo e
    # é bem mais rápido — testar o rápido e concluir que está tudo bem é
    # exatamente o erro que uma verificação não pode cometer.
    from_avaliar = ("qwen2.5vl:7b", "qwen2.5vl:3b")
    alvo = next((m for m in from_avaliar if m in modelos), modelos[0])
    corpo = json.dumps({
        "model": alvo,
        "messages": [{"role": "user",
                      "content": "Responda apenas com a palavra: FUNCIONANDO"}],
        "stream": False,
        # SEM ISTO o modelo "thinking" devolve `response` vazio, e o placar de
        # qualquer teste vira ficção.
        "think": False,
        "options": {"num_predict": 12, "temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(base + "/api/chat", data=corpo,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            resp = json.loads(r.read().decode("utf-8"))
        texto = (resp.get("message") or {}).get("content", "").strip()
        seg = time.time() - t0
        if not texto:
            _linha(RUIM, f"  gerar {alvo}", "resposta VAZIA")
            return False
        _linha(OK, f"  gerar {alvo}", f"{seg:.1f}s · {texto[:24]!r}")
    except Exception as e:
        _linha(RUIM, f"  gerar {alvo}", f"{type(e).__name__}")
        return False

    # ONDE ELE RODA. Modelo maior que a VRAM cai para a CPU e fica várias vezes
    # mais lento — sem quebrar nada, o que é justamente o que torna difícil de
    # notar.
    try:
        d, _ = _http(base + "/api/ps", timeout=20)
        for m in d.get("models") or []:
            tot, vram = m.get("size", 0), m.get("size_vram", 0)
            pct = 100 * vram / tot if tot else 0
            estado = OK if pct >= 95 else AVISO
            _linha(estado, "  na GPU", f"{m.get('name')} · {pct:.0f}%"
                   + ("" if pct >= 95 else "  << o resto vai para a CPU"))
    except Exception:
        pass
    return True


# ── Alcance bruto ────────────────────────────────────────────────────────────

def alcance() -> None:
    print("\n── alcance das máquinas ──")
    for rot, host, portas in (
            ("i9", I9, [("pg produto", 5444), ("pg referência", 5443),
                        ("pooler", 5442), ("nominatim", 8080), ("photon", 2322),
                        ("osrm", 5000), ("searxng", 8888), ("ollama", 11434)]),
            ("DGX", DGX, [("llm", 11434), ("ssh", 22)])):
        vivos = [n for n, p in portas if _porta(host, p, 3)]
        mortos = [n for n, p in portas if n not in vivos]
        estado = OK if not mortos else (RUIM if len(mortos) > 2 else AVISO)
        _linha(estado, f"{rot} ({host})",
               f"{len(vivos)}/{len(portas)} portas"
               + (f" · fechadas: {', '.join(mortos)}" if mortos else ""))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rapido", action="store_true",
                   help="pula a geração do LLM, que é a verificação mais lenta")
    args = p.parse_args()

    print("⟦verificação de serviços⟧\n")
    alcance()
    print("\n── bancos ──")
    r = [banco_produto(), banco_referencia()]
    print("\n── geo ──")
    r += [nominatim(), photon(), osrm(), searxng()]
    print("\n── llm ──")
    r += [llm(args.rapido)]

    ruins = r.count(False)
    print()
    if ruins:
        print(f"  {ruins} de {len(r)} verificações FALHARAM — veja as linhas "
              f"marcadas com '>>' acima.")
    else:
        print(f"  {len(r)}/{len(r)} verificações passaram. Sistema íntegro.")
    return 1 if ruins else 0


if __name__ == "__main__":
    sys.exit(main())
