# -*- coding: utf-8 -*-
"""Onde cada serviço mora, e o token que ele exige. Um lugar só.

POR QUE ESTE MÓDULO EXISTE
==========================

Até 30/08/2026 o endereço de cada serviço era um padrão embutido no arquivo que
o consumia:

    agente_local.py          SEARXNG_URL   = ...100.115.117.49:8888
    agente_local.py          NOMINATIM_URL = ...100.115.117.49:8080
    avaliar_fachada.py       OLLAMA_URL    = ...100.115.117.49:11434
    detect_pois_opencv.py    QWEN_URL      = ...100.115.117.49:8081
    leitura_fachada.py       VLLM_URL      = ...100.85.164.54:8000
    identificar_divergente   VLLM_URL      = ...100.85.164.54:8000
    subir_imagens.py         GATEWAY       = ...100.115.117.49:8000

Sete arquivos, cinco endereços, dois deles duplicados — e mais os testes. Trocar
o ambiente assim é trocar quarenta linhas e deixar uma para trás; a que fica
para trás só aparece quando alguém rodar aquele caminho, semanas depois.

Com a mudança para o padrão A2L (doc 23 e 24) TODOS mudam de uma vez: outra
rede, outra faixa de portas, e — o que é novo — **token obrigatório**.

O TOKEN, E POR QUE ELE VIVE AQUI
================================

Nominatim, Photon, OSRM, SearXNG e vLLM passaram a exigir `Authorization:
Bearer` emitido pela API de recursos na 7700. Sem ele, 401. O token dura 15
minutos, então não dá para pegar um no início do processo e guardar: uma
mineração roda horas.

`cabecalho()` resolve isso — pede um token novo quando o atual está por vencer,
e devolve o cabeçalho pronto. Quem chama não precisa saber que existe token.

A RENOVAÇÃO É ANTECIPADA, com 60 s de folga. Esperar o 401 para reagir significa
que a primeira chamada de cada 15 minutos falha — e algumas delas (o lote de
busca, a leitura de fachada) não são repetidas por ninguém.

O QUE NÃO EXIGE TOKEN
=====================

Supabase e Studio têm autenticação própria; exigir dois tokens quebraria todo
cliente sem ganho. O `gateway()` devolve endereço sem cabeçalho de recurso.

USE OS NOMES `.stack`, NÃO OS IPs
=================================

`nominatim.stack` e companhia não têm DNS: entram no `/etc/hosts` da máquina que
consome. É por esses nomes que o Caddy balanceia entre o i9 e o notebook —
chamando `192.168.3.10:7200` direto, o transbordo se perde.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

import config  # noqa: F401  — carrega o .env


def _env(chave: str, padrao: str = "") -> str:
    return (os.environ.get(chave) or padrao).strip().rstrip("/")


# ── Endereços ───────────────────────────────────────────────────────────────
#
# O padrão de cada um é o do ambiente A2L. A variável de ambiente continua
# mandando: é ela que permite apontar para um túnel, para o notebook de
# transbordo, ou para um serviço de teste — sem tocar em código.

LAN = _env("A2L_LAN", "192.168.3.10")

#: Supabase — REST, Auth e Storage. Autenticação própria, sem token de recurso.
SUPABASE = _env("SUPABASE_URL", f"http://{LAN}:7120")

#: API de identidade: usuários, empresas, níveis e permissões.
IDENTIDADE = _env("A2L_IDENTIDADE_URL", f"http://{LAN}:7710")

#: API de recursos: emite o token dos serviços abaixo.
RECURSOS = _env("A2L_RECURSOS_URL", f"http://{LAN}:7700")

#: API de bases: a pasta da empresa no Google Drive.
#:
#: O RADAR NÃO FALA COM O GOOGLE, e é o contrato: a credencial OAuth pertence
#: só ao `api-bases`. Daqui saem apenas chamadas HTTP com o token DO USUÁRIO —
#: o mesmo GoTrue de `SUPABASE`, o que dispensa credencial nova e faz a RLS de
#: lá valer sem que este serviço precise saber de qual empresa alguém é.
BASES = _env("A2L_BASES_URL", f"http://{LAN}:7720")

#: Serviços que EXIGEM token. O nome `.stack` é proposital — ver o topo.
NOMINATIM = _env("NOMINATIM_URL", "https://nominatim.stack")
PHOTON = _env("PHOTON_URL", "https://photon.stack")
OSRM_CARRO = _env("OSRM_CARRO_URL", "https://osrm-carro.stack")
OSRM_PE = _env("OSRM_PE_URL", "https://osrm-pe.stack")
SEARXNG = _env("SEARXNG_URL", "https://searxng.stack")
VLLM = _env("VLLM_URL", "https://vllm.stack")

#: O bucket do Storage acompanha o nome novo da ferramenta.
BUCKET = _env("STORAGE_BUCKET", "radar_comercial")

#: Portas que ESTE sistema publica (doc 17: blocos de 100, incremento de 10).
PORTA_API = int(_env("PORTA_API", "7720"))          # bloco 7700 — APIs próprias
PORTA_FRONT = int(_env("PORTA_FRONT", "7810"))      # bloco 7800 — frontends
PORTA_CAPTURA = int(_env("PORTA_CAPTURA", "7910"))  # bloco 7900 — interna


# ── Token dos recursos ──────────────────────────────────────────────────────

_FOLGA_S = 60          # renova com 60 s de sobra: ver o topo
_lock = threading.Lock()
_token = {"valor": "", "expira": 0.0}


class SemCredencialDeRecurso(RuntimeError):
    """Falta `A2L_RECURSOS_CLIENTE` ou `A2L_RECURSOS_SEGREDO` no .env.

    Dita como exceção própria, e não como `None` devolvido em silêncio: sem
    credencial toda chamada a mapa e IA vira 401 lá adiante, longe da causa.
    """


def _pedir_token() -> tuple[str, float]:
    cliente = (os.environ.get("A2L_RECURSOS_CLIENTE") or "").strip()
    segredo = (os.environ.get("A2L_RECURSOS_SEGREDO") or "").strip()
    if not cliente or not segredo:
        raise SemCredencialDeRecurso(
            "A2L_RECURSOS_CLIENTE e A2L_RECURSOS_SEGREDO não estão no .env. "
            "Sem eles, Nominatim, SearXNG e vLLM devolvem 401. O segredo é "
            "mostrado UMA vez ao gerar o cliente (`gerar_cliente.mjs`) e vive "
            "no cofre.")

    corpo = json.dumps({"cliente": cliente, "segredo": segredo}).encode("utf-8")
    req = urllib.request.Request(
        f"{RECURSOS}/token", data=corpo, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    valor = d.get("token") or d.get("access_token") or ""
    if not valor:
        raise RuntimeError(f"a API de recursos respondeu sem token: {str(d)[:120]}")
    # `expires_in` em segundos; sem ele, assume os 15 min documentados.
    dur = float(d.get("expires_in") or d.get("expira_em") or 900)
    return valor, time.time() + dur


def token() -> str:
    """O token válido agora, renovando se estiver por vencer."""
    with _lock:
        if _token["valor"] and time.time() < _token["expira"] - _FOLGA_S:
            return _token["valor"]
        valor, expira = _pedir_token()
        _token["valor"], _token["expira"] = valor, expira
        return valor


def cabecalho(extra: dict | None = None) -> dict:
    """Cabeçalho pronto para chamar recurso protegido.

    Quem chama não precisa saber que existe token, nem quando ele vence.
    """
    h = {"Authorization": f"Bearer {token()}"}
    if extra:
        h.update(extra)
    return h


def esquecer_token() -> None:
    """Descarta o token guardado — para teste, e para reagir a um 401 tardio."""
    with _lock:
        _token["valor"], _token["expira"] = "", 0.0

# ──────────────────────────────────────────────────────────────────────────
# A IA roda num lugar so
# ──────────────────────────────────────────────────────────────────────────
#
# Regra do dono do produto, 25/08/2026: a única máquina que carrega modelo é a
# Spark. Modelo em qualquer outra disputa RAM com o Postgres de produção e com a
# extração estadual, e cria uma segunda verdade sobre qual modelo respondeu o
# quê — que ninguém consegue auditar depois.
#
# ESTA GUARDA JÁ FICOU CEGA UMA VEZ, e é por isso que ela mudou de forma. Ela
# era uma lista NEGRA de um IP só (`if "100.115.117.49" in url`). Quando aquela
# máquina foi desligada e o código passou a resolver endereços pelo módulo, o IP
# deixou de casar: a guarda continuou lá, verde na leitura, recusando nada.
#
# Lista BRANCA não envelhece assim. Máquina nova só entra aqui de propósito.

#: Onde é legítimo haver modelo. `vllm.stack` é o nome que o Caddy resolve;
#: 192.168.3.20 é a Spark no cabo, alcançável só por dentro do servidor.
HOSTS_DE_IA = ("vllm.stack", "192.168.3.20")


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return (urlsplit(url).hostname or "").lower()


def conferir_endpoint_de_ia(url: str, variavel: str = "VLLM_URL") -> None:
    """Levanta `SystemExit` se `url` não for o vLLM sancionado.

    A recusa DIZ qual host foi barrado. Falhar com "endpoint inválido" deixaria
    quem leu sem saber que existe uma regra — e a próxima pessoa apontaria para
    a mesma máquina de novo.
    """
    if not url or not url.startswith("http"):
        raise SystemExit(f"{variavel} inválida: {url!r}")

    alvo = _host(url)
    if alvo in HOSTS_DE_IA or alvo == _host(VLLM):
        return

    raise SystemExit(
        f"\n❌ RECUSADO: {url}\n\n"
        f"   O host `{alvo}` não é máquina de IA. A única que carrega modelo é\n"
        f"   a Spark, servida pelo vLLM em {' ou '.join(HOSTS_DE_IA)}.\n\n"
        f"   Modelo em qualquer outra máquina disputa a RAM do Postgres de\n"
        f"   produção e da extração estadual, e cria uma segunda verdade sobre\n"
        f"   qual modelo respondeu o quê.\n\n"
        f"   Aponte {variavel} para o vLLM.")
