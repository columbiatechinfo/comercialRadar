# -*- coding: utf-8 -*-
"""A base estadual não importa quem é de outro município.

O CICLO QUE ISTO ENCERRA

`conferir_municipio` roda no passo 7 e apaga quem tem CEP de fora; a extração
estadual roda no passo 2 e os trazia de volta. Toda mineração repetia o par:

    28/08/2026    86 gravados no passo 2  ·  87 apagados no passo 7
    27/08/2026    81 gravados             ·  81 apagados

O dado final ficava certo — o passo 7 cumpre a regra —, mas o número do passo 2
parecia ganho quando era descarte, e o passo 8 ficava sem nada novo para cruzar
porque o que tinha entrado já havia saído. A conferência passou a acontecer uma
etapa antes.

A REGRA É A MESMA DO PASSO 7, e vem do dono do produto: *"os que vêm da base com
CEP de outra cidade têm que ser deletados com certeza, a fonte do endereço é
confiável"*. Se o CEP é de Porto Alegre, o ponto é de Porto Alegre — mesmo com a
coordenada caindo dentro da divisa daqui.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import extracao_estadual as ee  # noqa: E402

CANOAS = "4304606"


def _linha(endereco):
    """Uma linha no formato do `execute_values` da etapa — o endereço é o 9º."""
    return ("Nome", "estadual", 0.0, 0.0, 0.0, 0.0, "estadual:x", "categoria",
            endereco, "", "", "", "", "Canoas", "RS", "estadual", True)


def _tem_referencia():
    try:
        import base_comum as bc
        bc.conectar_referencia().close()
        return True
    except Exception:
        return False


def test_cep_de_outro_municipio_nao_entra():
    if not _tem_referencia():
        pytest.skip("banco de referência indisponível")
    fica, fora = ee._sem_os_de_outro_municipio(
        [_linha("AV Y, 200 - 90010-000")], CANOAS)      # Porto Alegre
    assert fora == 1 and not fica, \
        "POI com CEP de outro município voltou a ser importado"


def test_cep_daqui_entra():
    if not _tem_referencia():
        pytest.skip("banco de referência indisponível")
    fica, fora = ee._sem_os_de_outro_municipio(
        [_linha("RUA X, 100 - 92010-000")], CANOAS)
    assert fora == 0 and len(fica) == 1


def test_cep_que_o_cnefe_nao_conhece_nao_e_prova():
    """São 2.616 assim em Canoas. Recusá-los apagaria ponto legítimo por causa
    de uma lacuna da base de referência, não por causa do dado."""
    if not _tem_referencia():
        pytest.skip("banco de referência indisponível")
    fica, fora = ee._sem_os_de_outro_municipio(
        [_linha("RUA Z, 300 - 99999-999")], CANOAS)
    assert fora == 0 and len(fica) == 1, \
        "CEP desconhecido do CNEFE passou a ser tratado como prova de exclusão"


def test_sem_cep_entra():
    """Sem CEP não há o que conferir; quem decide o município aí é outra coisa."""
    if not _tem_referencia():
        pytest.skip("banco de referência indisponível")
    fica, fora = ee._sem_os_de_outro_municipio([_linha("SEM CEP AQUI")], CANOAS)
    assert fora == 0 and len(fica) == 1


def test_a_referencia_fora_do_ar_nao_derruba_a_importacao():
    """O passo 7 ainda apaga. Fazer a importação inteira depender de um serviço
    de consulta trocaria um problema pequeno por um grande."""
    import conferir_municipio as cm
    original = cm.dono_dos_ceps
    cm.dono_dos_ceps = lambda ceps: (_ for _ in ()).throw(RuntimeError("caiu"))
    try:
        fica, fora = ee._sem_os_de_outro_municipio(
            [_linha("AV Y, 200 - 90010-000")], CANOAS)
    finally:
        cm.dono_dos_ceps = original
    assert len(fica) == 1 and fora == 0, \
        "a importação passou a morrer quando o banco de referência cai"


def test_a_conferencia_acontece_antes_da_insercao():
    """Depois do INSERT seria o passo 7 de novo — e o ciclo continuaria."""
    import io
    s = io.open(os.path.join(RAIZ, "extracao_estadual.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in s.splitlines()
                       if not l.lstrip().startswith("#"))
    assert (codigo.index("_sem_os_de_outro_municipio(linhas")
            < codigo.index("execute_values(")), \
        "a conferência de município passou para depois da inserção"
