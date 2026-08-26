# -*- coding: utf-8 -*-
"""A base FIXA se normaliza por município, quando muda. O POI, só na área.

Regra do dono do produto, 26/08/2026:

    "a normalização roda sempre que as bases grandes forem atualizadas, mas em
     se tratando de POIs roda apenas na área selecionada, que aí sim, com as
     bases comparativas já normalizadas, surte efeito e agrupa mais"

E o número prova. A skill aprende `tokenA ≡ tokenB` por prova — mesmo número,
30 m, support de dois imóveis distintos. Com um pedaço, ela quase não tem o que
provar; com o município, tem:

    Bento Gonçalves, por área:       84 marcações,     4 ALTA
    Canoas, município inteiro:  308.881 marcações, 5.997 ALTA

O léxico é persistente por município, então toda área minerada depois herda o
aprendizado de graça.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402


def _fonte(nome):
    return io.open(os.path.join(RAIZ, nome), encoding="utf-8").read()


def test_a_base_fixa_roda_sem_area():
    """É o ÚNICO lugar do sistema onde rodar o município inteiro é certo."""
    s = _fonte("normalizar_bases.py")
    i = s.index("cmd = [PYTHON")
    cmd = s[i:i + 200]
    assert "--municipio" in cmd and "--area" not in cmd, \
        "a normalização de base fixa voltou a ser recortada por área"


def test_cobertura_conta_e_nao_so_a_data():
    """A etapa 7 normaliza por ÁREA e grava linhas de CNEFE com o `scope_id` do
    município. Olhando só a data, Bento Gonçalves parecia "em dia" com 67 de
    64.356 linhas (0%) e foi pulado, enquanto Canoas estava em 99%.

    A pergunta certa não é "quando rodou" e sim "rodou INTEIRO"."""
    s = _fonte("normalizar_bases.py")
    i = s.index("def precisa(")
    corpo = s[i:i + 2600]
    assert "_linhas_cnefe" in corpo, "a cobertura deixou de ser critério"
    assert "0.8" in corpo, "o piso de cobertura sumiu"


def test_so_municipio_com_uso_real():
    """320 municípios têm POI, mas 17 concentram 98% dos 82.977. Preparar os
    outros 303 seria horas de CNEFE para 2% do dado — e eles não ficam de fora
    para sempre: quando uma área ali for minerada, a etapa 7 os atende."""
    s = _fonte("normalizar_bases.py")
    assert "min_pois" in s and "having count(*) >= %s" in s


def test_zona_utm_unica_por_execucao():
    """A skill ABORTA quando a base cruza zonas UTM, e está certa: ela mede 30 m
    em UTM, e metro de zonas diferentes não se compara.

    Dois casos apareceram em 26/08, e o conserto é diferente em cada um:

      UM POI de outro estado ("Serra", em -20,-40, a 1.400 km de Canoas) derrubou
      a normalização de 320.250 registros. Ele não pertence: fica fora da caixa.

      SANTA MARIA atravessa a divisa 21S/22S de verdade — 905 endereços a oeste
      contra 148.578 a leste. Esses são legítimos, então normaliza-se a zona
      DOMINANTE e diz-se quantos ficaram de fora.
    """
    s = _fonte("ajuste_logradouro.py")
    assert "def zona_dominante(" in s, "o tratamento de divisa de zona sumiu"
    assert "def faixa_do_municipio(" in s, "o filtro de fora-do-município sumiu"
    assert "ficam de fora" in s, "o que ficou de fora deixou de ser dito"


def test_o_que_fica_de_fora_nao_e_apagado():
    """Coordenada errada é dado a corrigir, não a esconder. Quem fica de fora
    da exportação continua no banco."""
    s = _fonte("ajuste_logradouro.py")
    assert "delete" not in s.lower(), "a exportação passou a apagar registro"
