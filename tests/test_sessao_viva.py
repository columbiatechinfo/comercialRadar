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


def test_o_perfil_so_e_apagado_como_CURA():
    """O perfil é a memória da sessão. Apagá-lo A CADA LOTE era jogar fora a
    prova de que aquele navegador já esteve ali antes — e isso continua
    proibido.

    O QUE MUDOU EM 28/08/2026, e por quê. Este teste dizia "rmtree" nunca, e com
    isso proibia também a única saída para o caso em que o PERFIL é o problema.
    A run das 14:44 queimou 5 IPs e trouxe 0 de 14 POIs com
    `Page.goto: Timeout 35000ms` — e nos mesmos IPs, minutos depois, 10 de 10
    sessões abriram o Maps em 4 s. Não era o IP: era o diretório de perfil, que
    uma run cancelada tinha deixado preso.

    A regra que ficou: o perfil ATRAVESSA a run inteira, e só é jogado fora
    quando o Maps NÃO ABRE — no máximo três vezes. Uma coisa é não apagar por
    higiene; outra é não conseguir se curar.
    """
    c = _worker()
    assert 'f"w{wid}"' in c, "o perfil voltou a ser por LOTE em vez de por worker"

    # apagar SÓ na cura: o único `rmtree` do worker está sob o teto de curas
    assert c.count("rmtree") == 1,         "o perfil voltou a ser apagado em mais de um lugar"
    assert "MAX_CURAS_PERFIL" in c and "curas_perfil <" in c,         "a troca de perfil perdeu o teto, e vira apagar por higiene de novo"
    i = c.index("rmtree")
    assert "Maps não abriu" in c[max(0, i - 700):i],         "o perfil passou a ser apagado fora da falha de abertura"


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
