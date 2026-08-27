# -*- coding: utf-8 -*-
"""O painel do Maps é lento, e o teto tem de caber a lentidão.

O QUE ACONTECEU EM 26/08/2026

A busca começou a devolver "botão Próximo não encontrado" em quase todo POI.
A mesma área, no começo da noite, tinha dado 7 de 7 — o que mudou pelo meio
foi a rede: o notebook passou para o roteador do celular.

MEDIDO na noite do conserto, coordenadas reais da área de Canoas, via proxy:

    primeira coordenada (sessão fria) .... 8,0 s
    as quatro seguintes .................. 0,0 s   (o botão persiste)

O teto era 9.000 ms. Oito contra nove: um segundo de folga. Em rede boa
passava; no celular, a mesma run deu 4 sucessos e 13 falhas.

E O ERRO MENTIA. Ele dizia "não encontrado", que se lê como "esse botão não
existe nesta página" — e mandou o diagnóstico para idioma da página, perfil
corrompido e muro de consentimento antes de alguém medir o tempo. "Não existe"
e "ainda não veio" pedem conserto diferente: trocar seletor num caso, dar
tempo no outro.

SUBIR O TETO É QUASE DE GRAÇA, e é isso que torna esta escolha fácil:
`wait_for` devolve no instante em que o elemento fica visível, então o teto só
é pago quando o botão REALMENTE não vem. Como ele ainda por cima persiste
entre POIs do mesmo lote, na prática só a primeira coordenada de cada sessão
chega perto do limite.
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: E402


def test_o_teto_cabe_o_painel_lento():
    """8,0 s medidos exigem folga de verdade, não de um segundo. O piso de 20 s
    dá mais que o dobro do pior caso observado."""
    assert config.WAIT_PROXIMO_MS >= 20000, (
        f"teto de {config.WAIT_PROXIMO_MS} ms — o painel levou 8.000 ms na "
        f"medição de 26/08/2026, e a folga precisa aguentar rede pior")
    # e não pode virar espera eterna: quem trava a etapa é pior que quem falha
    assert config.WAIT_PROXIMO_MS <= 60000, "o teto virou espera sem fim"


def test_o_erro_diz_que_foi_TEMPO_e_nao_ausencia():
    """A mensagem antiga custou meia hora de diagnóstico no lugar errado."""
    s = io.open(os.path.join(RAIZ, "search_pois_v2.py"), encoding="utf-8").read()
    i = s.index("async def nivel2(")
    corpo = s[i:i + 2500]
    assert "não pintou" in corpo, "o erro voltou a dizer 'não encontrado'"
    assert "não é ausência do botão" in corpo, \
        "a mensagem deixou de separar 'não existe' de 'não deu tempo'"
    # e diz QUANTO esperou — sem o número não dá para saber se o teto é o problema
    assert "WAIT_PROXIMO_MS" in corpo, "o erro não informa mais quanto esperou"


def test_a_espera_e_por_evento_e_nao_sleep_fixo():
    """`wait_for(state="visible")` é o que torna o teto alto barato: ele volta
    assim que o botão aparece. Trocar por `wait_for_timeout` faria TODO POI
    pagar o teto inteiro, e aí 25 s por POI seria proibitivo."""
    s = io.open(os.path.join(RAIZ, "search_pois_v2.py"), encoding="utf-8").read()
    i = s.index("async def nivel2(")
    corpo = s[i:i + 1400]
    assert 'wait_for(state="visible"' in corpo, \
        "a espera do Próximo virou sleep fixo — o teto passaria a ser pago sempre"
    assert "WAIT_PROXIMO_MS" in corpo


def test_o_numero_esta_documentado_com_a_medicao():
    """Teto sem a medição ao lado é número mágico: o próximo a mexer não sabe
    se pode baixar, e baixar reabre exatamente este defeito."""
    s = io.open(os.path.join(RAIZ, "config.py"), encoding="utf-8").read()
    i = s.index("WAIT_PROXIMO_MS")
    # o comentário vem ANTES da atribuição
    antes = s[max(0, i - 1200):i]
    assert re.search(r"8[,.]0 s", antes), \
        "a medição de 8,0 s saiu de perto do número"
    assert "wait_for" in antes or "de graça" in antes, \
        "sumiu a explicação de por que o teto alto não custa caro"
