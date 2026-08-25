# -*- coding: utf-8 -*-
"""A release do Overture precisa ser RESOLVIDA, senão a entrega é recusada.

    ERRO na etapa 'dedup': observacao sob snapshot indeterminado nas fontes
    overture. Resolva a versao da fonte (--source-mode latest) antes de gerar
    a entrega.

Em 25/08/2026 o RS falhou assim DEPOIS de 38 minutos, e o PI depois de 3 — os
dois já com tudo processado:

    TERRITORY: 1.189.101 -> 1.065.018 pontos em 497 municipios
    NORMALIZE: 1.065.018 -> 1.065.018

Um milhão de pontos barrados no último passo por falta de um nome de versão.

A CAUSA, e ela é silenciosa por construção: o `glob` do DuckDB sobre
`s3://overturemaps-us-west-2/release/*/*` devolve ZERO LINHAS sem levantar
exceção. O DuckDB assina a requisição; o bucket do Overture é público e responde
à listagem anônima. Sem erro para registrar e sem release para declarar, o
snapshot ficava `indeterminate` — e a recusa da `dedup` estava certa: entregar
dado sob identidade de fonte desconhecida é o que essa trava existe para impedir.

A correção lê a listagem HTTP do MESMO bucket. Não é outra fonte — é o mesmo
prefixo por outra porta, então a identidade declarada continua sendo a real.

Skill VENDORADA: uma atualização dela sobrescreve o arquivo. Este teste avisa em
segundos; a produção avisaria depois de 38 minutos, uma UF por vez.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "skills", "extracao-poi-estadual"))


@pytest.fixture(scope="module")
def resolvido():
    """Resolve a release consultando a fonte de verdade.

    Depende de rede: sem ela, `skip`. Um teste de resolução de fonte que passa
    offline estaria provando o contrário do que se quer.
    """
    P = pytest.importorskip("poi_estadual.procedencia")
    # `OVERTURE_RELEASE` no ambiente curto-circuitaria a resolução e o teste
    # passaria sem exercitar o caminho que quebrou.
    antigo = os.environ.pop("OVERTURE_RELEASE", None)
    try:
        s = P.resolver_overture(None, None, consultar=True)
    except Exception as e:                                     # noqa: BLE001
        pytest.skip(f"sem rede para consultar o Overture: {type(e).__name__} {e}")
    finally:
        if antigo is not None:
            os.environ["OVERTURE_RELEASE"] = antigo
    return s


def test_a_release_e_determinada(resolvido):
    """`determinado` é o que a `dedup` exige para deixar a entrega passar."""
    assert resolvido.get("determinado") is True, (
        f"snapshot indeterminado — a dedup vai recusar a entrega: {resolvido}")


def test_o_status_e_verificado_e_nao_presumido(resolvido):
    """`verified_latest` significa que a versão veio da FONTE.

    `pinned` também passaria na dedup, mas quer dizer "alguém digitou" — e um
    palpite errado declara uma identidade falsa para o dado, que é pior que não
    declarar nenhuma.
    """
    assert resolvido.get("resolution_status") == "verified_latest", resolvido


def test_a_release_parece_uma_release(resolvido):
    """Formato `AAAA-MM-DD.N`. Sem isto, uma string vazia passaria como versão."""
    import re
    sid = resolvido.get("snapshot_id")
    assert sid and sid != "indeterminado", resolvido
    # A release em si fica em `source_version` ou equivalente; o teste aceita
    # qualquer campo que a carregue, porque o nome variou entre versões da skill.
    txt = " ".join(str(v) for v in resolvido.values())
    assert re.search(r"\d{4}-\d{2}-\d{2}\.\d", txt), \
        f"nenhum campo com cara de release: {resolvido}"


def test_o_patch_local_esta_declarado():
    """Skill vendorada: a próxima atualização apaga a correção em silêncio."""
    p = os.path.join(RAIZ, "skills", "extracao-poi-estadual",
                     "poi_estadual", "procedencia.py")
    texto = open(p, encoding="utf-8").read()
    assert "PATCH LOCAL" in texto, "o aviso de patch local sumiu de procedencia.py"
    assert "ZERO LINHAS" in texto, \
        "a explicação do glob vazio deixou de estar onde a correção mora"
