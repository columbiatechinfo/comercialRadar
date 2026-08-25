# -*- coding: utf-8 -*-
"""O `pd.NA` que derrubava a extração estadual inteira.

    FSQ dist: 3 shards densos de 100 | 550.514 pontos no bbox | 25s
    ERRO na etapa 'fetch': boolean value of NA is ambiguous

RS e PI falharam no mesmo ponto, em 25/08/2026, DEPOIS de o download ter dado
certo. A causa era um `or` sobre um valor que o pandas se recusa a avaliar como
booleano:

    lbl.map(lambda a: (...) if isinstance(a, (list, np.ndarray)) else (a or None))
                                                                      ^^^^^^^^^^

`pd.NA` não é `None`, não é lista e não é string — cai no `else`, e `a or None`
força `bool(pd.NA)`, que levanta. A etapa `fetch` inteira morre por causa de uma
célula vazia numa coluna opcional.

O defeito só apareceu quando o Foursquare começou a funcionar: até então o `fsq`
morria no 403 do Hugging Face, muito antes desta linha. Portão aberto, defeito
seguinte à vista — e ele custou duas UFs antes de alguém ler o log.

Este arquivo existe porque a skill é VENDORADA: uma atualização dela sobrescreve
o arquivo corrigido. A suíte reprova em segundos; a produção morreria depois de
horas de download.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "skills", "extracao-poi-estadual"))

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")


@pytest.fixture(scope="module")
def primeira_categoria():
    fsq = pytest.importorskip("poi_estadual.foursquare")
    fn = getattr(fsq, "_primeira_categoria", None)
    if fn is None:
        # Ela vive dentro de `_build` em algumas versões; então o teste procura
        # pelo comportamento no módulo e, não achando, avisa em vez de passar.
        pytest.fail("`_primeira_categoria` sumiu de foursquare.py — a skill foi "
                    "atualizada por cima do patch local? Ver o cabeçalho do arquivo.")
    return fn


@pytest.mark.parametrize("entrada, esperado", [
    # O CASO QUE QUEBRAVA. Os três valores ausentes que o pandas usa.
    (pd.NA, None),
    (float("nan"), None),
    (pd.NaT, None),
    # O caminho normal: lista de rótulos, fica o primeiro, sem espaços.
    (["Restaurant", "Pizza"], "Restaurant"),
    ([" Bakery "], "Bakery"),
    (np.array(["Bar", "Pub"]), "Bar"),
    # Lista vazia não é erro, é ausência.
    ([], None),
    (np.array([]), None),
    # Escalares que chegam de vez em quando.
    (None, None),
    ("Hotel", "Hotel"),
    ("", None),
])
def test_categoria_aceita_tudo_que_a_coluna_traz(primeira_categoria, entrada, esperado):
    assert primeira_categoria(entrada) == esperado


def test_a_coluna_inteira_com_NA_no_meio_nao_derruba(primeira_categoria):
    """O caso REAL: uma Series com listas e buracos, como vem do parquet.

    Testar valor a valor não bastaria — o estouro acontecia dentro do `.map()`
    sobre a coluna, e é essa a forma que a produção usa.
    """
    col = pd.Series([["Restaurant"], pd.NA, None, ["Bar", "Pub"], [], float("nan")],
                    dtype=object)
    saida = col.map(primeira_categoria)
    assert list(saida) == ["Restaurant", None, None, "Bar", None, None]


def test_o_patch_local_esta_declarado_no_arquivo():
    """Skill vendorada: quem a atualizar precisa VER que havia um patch aqui.

    Sem o aviso no cabeçalho, a próxima atualização apaga a correção em
    silêncio e a produção volta a morrer depois de horas de download.
    """
    p = os.path.join(RAIZ, "skills", "extracao-poi-estadual",
                     "poi_estadual", "foursquare.py")
    texto = open(p, encoding="utf-8").read()
    assert "PATCH LOCAL" in texto, \
        "o aviso de patch local sumiu de foursquare.py"
    assert "boolean value of NA is ambiguous" in texto, \
        "o sintoma original deixou de estar escrito onde a correção mora"
