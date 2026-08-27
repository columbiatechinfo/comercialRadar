# -*- coding: utf-8 -*-
"""POI cujo CEP é de outra cidade não é desta cidade.

A REGRA, do dono do produto em 27/08/2026:

> "os que vêm da base com CEP de outra cidade têm que ser deletados com certeza,
>  a fonte do endereço é confiável, assim como os do Google que estiverem fora"

E ela é uma afirmação sobre a natureza do dado. O CEP não é texto livre: os
Correios o atribuem a um trecho de logradouro de um município. Quando a base de
origem grava um CEP, ela está declarando o município — e essa declaração vale
mais que o campo `cidade`, que é preenchido pelo processo e já errou antes.

A COORDENADA NÃO SALVA O REGISTRO, e o número explica por quê: dos 348 POIs de
Canoas com CEP estrangeiro na primeira contagem, TODOS os 348 tinham coordenada
dentro da divisa municipal. Se a coordenada mandasse, nenhum seria pego — a
coordenada veio de busca por nome no Google, que casa com homônimo, enquanto o
CEP veio da ficha original.

A DECISÃO FOI TOMADA COM O CONTRA-EXEMPLO À VISTA. Dos 229 apagados em Canoas,
163 tinham no texto o nome "Canoas" ou um bairro real dela (Rio Branco,
Harmonia, Marechal Rondon) — um deles é "Av. Santos Ferreira, 1464 — Mal.
Rondon" com CEP de São Paulo. O dono do produto viu esses casos e manteve a
regra: o CEP manda.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import conferir_municipio as cm  # noqa: E402


def test_o_cep_e_lido_com_e_sem_hifen():
    """As fontes escrevem dos dois jeitos, e um CEP não lido é um POI não
    conferido."""
    assert cm.ceps_do_texto("Rua X, 1 - Canoas - RS, 92010-000") == ["92010000"]
    assert cm.ceps_do_texto("Rua X, 1, 92010000") == ["92010000"]
    assert cm.ceps_do_texto("CEP: 92.425-638 e 90010-220") == ["92425638", "90010220"]
    assert cm.ceps_do_texto("Rua X, 1 - Canoas") == []
    # e não confunde número de porta longo com CEP
    assert cm.ceps_do_texto("Rua X, 135293167184216") == []


def test_cep_desconhecido_nao_e_prova():
    """Ausência de registro é ausência de prova. Um CEP que o CNEFE não conhece
    NÃO manda o POI para o lixo — foram 2.574 em Canoas, e apagá-los por não
    estarem numa base seria punir o POI pelo buraco da referência."""
    s = io.open(os.path.join(RAIZ, "conferir_municipio.py"), encoding="utf-8").read()
    assert "cep_desconhecido" in s, "o CEP desconhecido deixou de ser contado à parte"
    i = s.index("if not conhecidos:")
    corpo = s[i:i + 260]
    assert "desconhecido += 1" in corpo and "continue" in corpo, \
        "o CEP fora do CNEFE passou a ser tratado como prova de outra cidade"


def test_um_cep_daqui_basta():
    """Um endereço pode citar dois CEPs — o do ponto e o de uma referência
    ("ao lado do prédio X, CEP tal"). Bastar UM ser daqui evita apagar o POI
    por causa do CEP do vizinho."""
    s = io.open(os.path.join(RAIZ, "conferir_municipio.py"), encoding="utf-8").read()
    assert "any(d[2] == cod_ibge for _c, d in conhecidos)" in s, \
        "voltou a exigir que TODOS os CEPs sejam daqui"


def test_o_cep_e_resolvido_contra_o_cnefe_e_nao_por_faixa():
    """A primeira versão usava a faixa mínima–máxima do município e marcava
    como estrangeiro o 92001970 da Base Aérea de Canoas — que é daqui e fica
    abaixo do piso. Faixa é aproximação; o CNEFE tem o CEP de cada endereço com
    o município ao lado."""
    s = io.open(os.path.join(RAIZ, "conferir_municipio.py"), encoding="utf-8").read()
    assert "def dono_dos_ceps(" in s, "a resolução por CNEFE sumiu"
    assert "cod_municipio" in s and "ibge_malha" in s
    for faixa in ("cmin", "cmax", "between"):
        assert faixa not in s, f"voltou a decidir por faixa ({faixa})"


def test_a_resolucao_e_em_lote():
    """Resolver um a um contra 111 milhões de linhas estourou 10 minutos na
    primeira tentativa. Em lote é uma varredura só."""
    s = io.open(os.path.join(RAIZ, "conferir_municipio.py"), encoding="utf-8").read()
    i = s.index("def dono_dos_ceps(")
    corpo = s[i:i + 1400]
    assert "= any(%s)" in corpo, "a resolução voltou a ser um CEP por consulta"


def test_a_etapa_apaga_por_ultimo_na_mineracao():
    """A ORDEM PROTEGE. `corrigir_coordenada` ainda pode consertar a coordenada
    de um ponto cujo endereço é daqui; só depois se pergunta se o ponto pertence
    à cidade — e aí a resposta é definitiva, porque apaga."""
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    i_corrige = s.index("corrigir_coordenada.py")
    i_confere = s.index("conferir_municipio.py")
    assert i_corrige < i_confere, (
        "a conferência de município passou a rodar ANTES da correção de "
        "coordenada — pontos consertáveis seriam apagados")


def test_a_etapa_nao_derruba_a_rodada():
    """Cidade sem CNEFE, ou banco de referência fora do ar, não pode custar a
    mineração: perde-se a limpeza, não a rodada."""
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    i = s.index("conferir_municipio.py")
    assert "_tolerante" in s[max(0, i - 300):i], \
        "a conferência de município virou etapa que derruba a rodada"


def test_nao_sobrou_ninguem_de_outro_municipio():
    """Contra o banco: depois de aplicada, a etapa não tem o que apagar. Sem
    isso ela repetiria trabalho a cada mineração — e, pior, um resto crescente
    passaria despercebido."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        plano = cm.avaliar(con, "4304606", "Canoas")
    finally:
        con.close()
    assert plano["fora"] == [], (
        f"{len(plano['fora'])} POIs de outro município voltaram a aparecer")
    assert plano["daqui"] > 0, "nenhum POI foi reconhecido como daqui — regra quebrada"
