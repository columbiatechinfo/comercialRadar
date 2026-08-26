# -*- coding: utf-8 -*-
"""Chromium + proxy + Google = trava total, e o culpado é o HTTP/2.

MEDIDO EM 26/08/2026, depois de a run de Bento Gonçalves perder 15 IPs.

O sintoma mente. `page.goto("https://www.google.com/maps")` devolve
`ERR_TIMED_OUT`, o pool marca o IP como queimado, põe em cooldown de 10 minutos
e passa ao próximo — que trava igual. A leitura fácil é "os proxies morreram", e
foi a que eu quase entreguei.

O QUE OS NÚMEROS DISSERAM

    os 100 IPs do cache eram os MESMOS 100 da conta, todos marcados válidos
    o mesmo IP, no mesmo instante, num GET direto: 200 em 0,9 s
    example.com e bing.com abriam PELO NAVEGADOR pelo mesmo proxy
    só a combinação Chromium + proxy + Google falhava

Nem `wait_until="commit"` escapava — o navegador não recebia o primeiro byte.
Então não é página pesada nem sub-recurso travado: é a negociação do protocolo.
`--disable-quic` não resolve. `--disable-http2` resolve.

    sem a flag   0/8 IPs abriram o Maps · média 17,8 s até estourar
    com a flag   8/8 IPs abriram o Maps · média  2,8 s

É a mesma família da cicatriz de 24/07/2026, quando IPs da Webshare
"penduravam no google.com só pelo navegador".

POR QUE ISTO É UM TESTE E NÃO SÓ UM COMENTÁRIO

A flag parece supérflua para quem lê a lista de `args` — é o tipo de coisa que
alguém remove numa limpeza. O custo de removê-la não aparece em teste de
unidade: aparece horas depois, numa run de campo, como "os proxies estão ruins
hoje". E aí se troca de fornecedor de proxy em vez de devolver uma flag.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402

FONTE = os.path.join(RAIZ, "human_browser.py")


def test_a_flag_esta_no_launcher_do_maps():
    s = io.open(FONTE, encoding="utf-8").read()
    assert "--disable-http2" in s, (
        "sem esta flag o Chromium trava em TODO acesso ao Google via proxy, e o "
        "sintoma aparece como IP queimado — 0/8 IPs abriram o Maps sem ela")


def test_a_flag_vai_junto_dos_outros_args_do_launch():
    """Não basta a string existir no arquivo: ela precisa chegar ao Chromium."""
    s = io.open(FONTE, encoding="utf-8").read()
    i = s.index("launch_persistent_context")
    j = s.index("await _aplicar_stealth", i)
    assert "--disable-http2" in s[i:j], \
        "a flag saiu da lista de `args` que o launch recebe"


def test_o_porque_esta_escrito_junto():
    """Flag sem motivo escrito é flag que alguém remove numa limpeza — e o custo
    só reaparece em campo, horas depois, disfarçado de proxy ruim."""
    s = io.open(FONTE, encoding="utf-8").read()
    i = s.index("--disable-http2")
    contexto = s[max(0, i - 1600):i]
    assert "HTTP/2" in contexto and "proxy" in contexto.lower(), \
        "o motivo da flag sumiu; sem ele ela vira lixo aparente"


def test_quic_sozinho_nao_era_a_resposta():
    """Registrado porque foi testado e falhou: `--disable-quic` não resolve.

    Sem isto escrito, a próxima pessoa a investigar gasta o mesmo tempo
    chegando à mesma resposta errada.
    """
    s = io.open(FONTE, encoding="utf-8").read()
    assert "disable-quic" in s, "a tentativa que NÃO funcionou deixou de ser registrada"
