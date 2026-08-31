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
import endpoints

I9 = endpoints.LAN
DGX = endpoints.LAN          # a Spark so responde por dentro do servidor
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
            k.execute("select count(*) from radar_comercial.pois")
            n = k.fetchone()[0]
        con.close()
        _linha(OK, "banco do produto", f"{n:,} POIs · {time.time()-t0:.1f}s")
        return True
    except Exception as e:
        _linha(RUIM, "banco do produto", f"{type(e).__name__}")
        # A CAUSA MAIS COMUM.
        #
        # ATÉ 30/08/2026 ESTE CONSELHO FALAVA DE `netsh interface portproxy`: o
        # encaminhamento do Windows para o WSL do i9 perdia a amarra a cada
        # reinício, as regras ficavam presas em 127.0.0.1, e reinseri-las era a
        # cura (reiniciar o `iphlpsvc` não bastava). Aquela máquina não existe
        # mais — o conselho mandava mexer em coisa que não há, que é pior do que
        # não dar conselho nenhum.
        if not _porta(endpoints.LAN, 7110):
            print("       O pooler não responde na 7110. Ele é um container:\n"
                  "       `docker ps | grep supabase-pooler` e\n"
                  "       `docker logs --tail 40 supabase-pooler`.\n"
                  "       Se estiver de pé e ainda assim recusar, confira o nome\n"
                  "       do usuário: o Supavisor exige o tenant embutido\n"
                  "       (`app_user.a2l`) e, sem ele, responde ENOIDENTIFIER.")
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
    """A IA do sistema — no vLLM da Spark, desde 24/08/2026.

    ONDE ELA MORA, E POR QUE MUDOU

    Até aqui esta verificação conferia o Ollama do i9. Mas a IA do produto passou
    a viver na Spark (`Qwen3-VL-30B-A3B-Instruct-FP8`, servido como
    `qwen3vl-moe` pelo container `nvcr.io/nvidia/vllm`), que é a máquina com a
    GPU dedicada. Conferir o i9 daria **7/7 apontando para o lugar errado** — o
    pior resultado possível para uma verificação, porque ela existe justamente
    para impedir isso.

    O vLLM fala o protocolo da OpenAI, o mesmo que `avaliar_fachada`,
    `descrever_imagens` e `leitura_fachada` usam. Então esta função exercita
    exatamente o caminho da produção, e não um atalho.
    """
    base = (os.environ.get("VLLM_URL")
            or os.environ.get("LOCAL_LLM_URL")
            or endpoints.VLLM).rstrip("/")
    try:
        d, s = _http(base + "/models", timeout=20)
    except Exception as e:
        _linha(RUIM, "llm (vLLM)", f"{base} · {type(e).__name__}")
        print("       O container está parado? Na Spark:\n"
              "         docker start vllm-tools\n"
              "       O modelo leva ~150 s para carregar antes de responder.")
        return False

    modelos = [m.get("id") for m in (d.get("data") or [])]
    if not modelos:
        _linha(RUIM, "llm (vLLM)", "servidor no ar e nenhum modelo servido")
        return False
    _linha(OK, "llm (vLLM)", f"{len(modelos)} modelo(s) · {', '.join(modelos[:3])}")
    if rapido:
        return True

    # GERAÇÃO DE VERDADE, e COM IMAGEM. `/models` responde mesmo com a GPU fora
    # do ar, e um modelo que carrega mas não gera é indistinguível de um saudável
    # até a primeira rodada de produção.
    #
    # A imagem não é capricho: todo uso real deste modelo aqui é VISÃO. Um teste
    # só de texto passaria com o caminho multimodal quebrado — que é o caminho
    # que o sistema de fato usa.
    alvo = next((m for m in modelos if "vl" in (m or "").lower()), modelos[0])
    import base64
    # PNG 1x1 vermelho, embutido: verificação não deve depender de arquivo em
    # disco nem de rede para montar o próprio caso de teste.
    px = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg==")
    corpo = json.dumps({
        "model": alvo, "temperature": 0, "max_tokens": 12,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Responda apenas com a palavra: FUNCIONANDO"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(px).decode()}},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=corpo,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer local"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            resp = json.loads(r.read().decode("utf-8"))
        msg = ((resp.get("choices") or [{}])[0].get("message") or {})
        texto = (msg.get("content") or "").strip()
        seg = time.time() - t0
        if not texto:
            _linha(RUIM, f"  gerar {alvo}", "resposta VAZIA")
            return False
        _linha(OK, f"  gerar {alvo}", f"{seg:.1f}s · {texto[:24]!r}")
        uso = resp.get("usage") or {}
        if uso:
            _linha(OK, "  visão", f"aceitou imagem · {uso.get('prompt_tokens')} tokens de entrada")
    except Exception as e:
        _linha(RUIM, f"  gerar {alvo}", f"{type(e).__name__}: {str(e)[:60]}")
        return False
    return True


# ── Alcance bruto ────────────────────────────────────────────────────────────

def alcance() -> None:
    print("\n── alcance das máquinas ──")
    for rot, host, portas in (
            ("i9", I9, [("pg produto", 5444), ("pg referência", 5443),
                        ("pooler", 5442), ("nominatim", 8080), ("photon", 2322),
                        ("osrm", 5000), ("searxng", 8888)]),
            # A porta 8000 e o vLLM — a IA do produto mora aqui desde 24/08/2026.
            # A 11434 (Ollama) saiu: nao ha Ollama na Spark, e conferi-la
            # produzia um "fechada" permanente que ensinava a ignorar o aviso.
            ("Spark", DGX, [("vllm", 8000), ("ssh", 22)])):
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
