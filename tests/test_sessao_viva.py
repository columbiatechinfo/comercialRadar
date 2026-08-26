# -*- coding: utf-8 -*-
"""A sessão do Maps vive enquanto houver lote — não morre a cada um.

DECISÃO DO DONO DO PRODUTO, 26/08/2026:

    "o problema é ficar abrindo novas conexões. Se eu abro uma única vez por
     run isso provavelmente não acontece."

Ele estava certo, e a medição do dia inteiro aponta para isso. A versão antiga
abria navegador, pegava IP, buscava 8 nomes, fechava e APAGAVA o perfil. Cada
lote era uma chegada A FRIO — sem cookie, sem histórico, sem nada. É o padrão
que um detector reconhece primeiro, porque pessoa nenhuma navega assim.

MEDIDO em Bento Gonçalves: 15 IPs queimados para buscar 25 nomes. E o que de
fato derrubava a busca era o HTTP/2 (ver `test_http2_mata_o_maps`), não o IP —
a reciclagem tratava o sintoma errado e pagava caro por isso.

O perfil agora é POR WORKER e sobrevive à execução: é o que faz a liberação do
desafio valer para a rodada seguinte.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402

FONTE = os.path.join(RAIZ, "search_pois_v2.py")


def _worker():
    s = io.open(FONTE, encoding="utf-8").read()
    i = s.index("async def worker(wid, queue")
    j = s.index("# Orquestração", i)
    return s[i:j]


def test_a_sessao_nasce_fora_do_laco_de_lotes():
    """Se `HumanSession.create` voltar para dentro do laço, voltamos a abrir um
    navegador por lote — que é exatamente o que queimou 15 IPs."""
    c = _worker()
    i_laco = c.index("while True:")
    i_cria = c.index("HumanSession.create")
    # a criação está dentro de `_abrir`, que é definida ANTES do laço
    i_abrir = c.index("async def _abrir")
    assert i_abrir < i_laco, "a sessão voltou a ser criada dentro do laço de lotes"
    assert i_cria < i_laco, "`HumanSession.create` está no caminho de cada lote"


def test_o_perfil_nao_e_apagado():
    """O perfil é a memória da sessão. Apagá-lo a cada lote era jogar fora a
    prova de que aquele navegador já esteve ali antes."""
    c = _worker()
    assert "rmtree" not in c, "o perfil do worker voltou a ser apagado"
    assert 'f"w{wid}"' in c, "o perfil voltou a ser por LOTE em vez de por worker"


def test_a_sessao_so_cai_por_captcha_ou_falha_de_abertura():
    """Trocar de sessão sem motivo é recriar o problema com outro nome."""
    c = _worker()
    assert "captcha_no_lote" in c and "_derrubar(cooldown=900)" in c, \
        "o CAPTCHA deixou de derrubar a sessão"
    assert "Maps não abriu" in c and "_derrubar(cooldown=600)" in c, \
        "a falha de abertura deixou de derrubar a sessão"


def test_o_ip_e_devolvido_e_nao_perdido():
    """Sessão longa segura um IP por muito tempo; ela precisa devolvê-lo ao
    terminar, senão o pool seca sozinho."""
    c = _worker()
    assert "finally:" in c and "await _derrubar()" in c, \
        "a sessão pode terminar sem devolver o IP ao pool"


def test_o_comentario_da_secao_diz_a_verdade():
    """O cabeçalho dizia "recicla browser+IP a cada lote". Comentário que
    descreve o código antigo é pior que comentário nenhum."""
    s = io.open(FONTE, encoding="utf-8").read()
    assert "recicla browser+IP a cada lote" not in s
