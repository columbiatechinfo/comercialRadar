# -*- coding: utf-8 -*-
"""Quando o endereço desmente a coordenada, quem ganha e por quê.

O CASO, 27/08/2026

Um POI diz "Rua 25 de Março, 55 — Rio Branco, Canoas" e está desenhado a 9,9 km
dali, no meio do Rio Jacuí. Os dois não podem estar certos.

E a pergunta não é de opinião: o CNEFE sabe onde fica a Rua 25 de Março, 55, e
sabe em que bairro ela está. Quando o **bairro ou o CEP que o próprio POI
declara** batem com o registro do CNEFE, acabou a dúvida — não é o endereço que
está errado, é a coordenada.

MEDIDO em Canoas, sobre os 12.478 POIs cujo endereço normalizado o CNEFE conhece:

    até 100 m da porta ..... 10.628 (85%)   a coordenada concorda
    100 m a 500 m ............ 1.005
    500 m a 2 km ............... 494
    mais de 2 km ............... 351

    dos 1.850 deslocados, 1.296 (70%) têm bairro OU CEP confirmando

DE ONDE VEM O ERRO: todos os piores vieram de `maps_painel` — a busca por nome
no Google casou com um homônimo em outro bairro. O `place_id` não protege: 99%
dos deslocados têm um. Ele identifica o lugar que o Maps devolveu, não o certo.

POR QUE SÓ OS CORROBORADOS (decisão do dono do produto): mover um ponto apoiado
só na distância seria trocar um erro conhecido por um erro invisível. Se o
endereço estiver errado, o ponto vai para o lugar errado com aparência de certo
— e ninguém mais desconfia.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import corrigir_coordenada as cc  # noqa: E402


def test_so_move_com_prova_independente():
    """`_corrobora` é o que separa "sei que a coordenada erra" de "acho"."""
    # o bairro do CNEFE aparece no endereço que o POI declara
    assert cc._corrobora("Rua 25 de Março, 55 - Rio Branco, Canoas",
                         "RIO BRANCO", "92200540") == "bairro"
    # o CEP do CNEFE aparece nos dígitos do endereço
    assert cc._corrobora("Rua Florença, 88 - Canoas, 92.425-638",
                         "OZANAN", "92425638") == "cep"
    # os dois
    assert cc._corrobora("Rua X, 1 - Centro, 92010000", "CENTRO", "92010000") \
        == "bairro+cep"
    # e o caso que NÃO move: nada no texto confirma
    assert cc._corrobora("Rua X, 1 - Canoas", "MATHIAS VELHO", "92330400") == ""


def test_o_acento_nao_impede_a_corroboracao():
    """"Rio Branco" no POI e "RIO BRANCO" no CNEFE são o mesmo bairro; e o
    CNEFE grava sem acento enquanto a fonte grava com. Comparar cru perderia a
    prova justamente onde ela existe."""
    assert cc._corrobora("Rua A, 1 - Estância Velha, Canoas",
                         "ESTANCIA VELHA", "") == "bairro"


def test_o_limiar_nao_mexe_em_diferenca_de_quarteirao():
    """Abaixo de 100 m a diferença ainda pode ser a porta vizinha ou um terreno
    grande. Mexer ali trocaria ruído por ruído."""
    assert cc.LONGE_M >= 100, (
        f"limiar de {cc.LONGE_M} m — abaixo disso a diferença é do tamanho de um "
        f"lote, e mover não melhora nada")
    assert cc.LONGE_M <= 300, "limiar alto demais deixa passar erro de quadra inteira"


def test_a_coordenada_antiga_e_guardada_antes():
    """Correção automática que não se desfaz é aposta, não conserto. E o
    `coalesce` importa: rodar duas vezes não pode fazer a segunda gravar como
    "anterior" a coordenada que a primeira já corrigiu."""
    s = io.open(os.path.join(RAIZ, "corrigir_coordenada.py"), encoding="utf-8").read()
    i = s.index("def aplicar(")
    corpo = s[i:i + 3000]
    assert "coord_anterior_lat = coalesce(p.coord_anterior_lat" in corpo, \
        "a coordenada anterior deixou de ser preservada, ou passou a ser sobrescrita"


def test_o_relato_nao_pode_mentir_sobre_quantos():
    """O `execute_values` parte a lista em lotes de 100 por padrão, e
    `cur.rowcount` fica valendo só o ÚLTIMO. Na primeira execução o log
    anunciou "96 POIs reposicionados" quando 1.296 tinham sido gravados —
    1296 mod 100. O dado estava certo e o relato, errado, que é o tipo de erro
    que faz alguém rodar de novo achando que faltou."""
    s = io.open(os.path.join(RAIZ, "corrigir_coordenada.py"), encoding="utf-8").read()
    assert "page_size=max(1, len(dados))" in s, \
        "voltou a contar só o último lote — o log passaria a mentir o total"


def test_quem_nao_tem_prova_e_marcado_e_nao_movido():
    """Os 554 sem corroboração ficam onde estão, visíveis para revisão. Sumir
    com eles ou movê-los às cegas são os dois jeitos de perder o problema de
    vista."""
    s = io.open(os.path.join(RAIZ, "corrigir_coordenada.py"), encoding="utf-8").read()
    i = s.index("if plano[\"revisar\"]")
    corpo = s[i:i + 700]
    assert "revisar_manual = true" in corpo, "os sem prova deixaram de ser marcados"
    assert "delete" not in corpo.lower(), "os sem prova passaram a ser apagados"


def test_roda_de_novo_sem_refazer():
    """Contra o banco: depois de aplicado, um segundo passe não tem o que mover.
    Sem isso a etapa entraria em laço em toda mineração."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        cur.execute("select count(*) from pois where coord_fonte = 'cnefe_endereco'")
        ja = cur.fetchone()[0]
        if not ja:
            import pytest
            pytest.skip("a correção ainda não rodou nesta base")
        plano = cc.avaliar(con, "4304606", "Canoas")
    finally:
        con.close()
    assert plano["mover"] == [], (
        f"{len(plano['mover'])} POIs voltaram a aparecer como deslocados depois "
        f"da correção — a etapa repetiria o trabalho a cada mineração")
