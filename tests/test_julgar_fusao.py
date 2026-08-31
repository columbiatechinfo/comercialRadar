# -*- coding: utf-8 -*-
"""O julgamento das fusões suspeitas — e as travas que o tornam uma MEDIÇÃO.

Este módulo produz um número que vai embasar decisão sobre o motor de dedup de
todas as 27 UFs. Um número medido errado é pior que número nenhum: número nenhum
deixa a pergunta aberta, número errado a fecha na resposta errada.

As travas aqui são todas sobre isso — não sobre o modelo acertar mais, e sim
sobre a contagem não mentir.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import julgar_fusao as J  # noqa: E402

PAR = {"id_a": "a1", "id_b": "b1", "motivo": "telefone", "dist_m": "9.25",
       "nucleo_a": "fashion hair visuale", "nucleo_b": "barbershop helio s"}
MAPA = {"a1": ("Fashion Hair Visuale", "Beauty Salon", "R. X, 10", "5133334444"),
        "b1": ("BarberShop Helio S", "Barbershop", "R. X, 12", "5133334444")}


def _dublar(monkeypatch, resposta):
    monkeypatch.setattr(J, "_chamar", lambda pares, mapa: resposta)


def test_veredito_fora_do_vocabulario_vira_incerto(monkeypatch):
    """O modelo responde "PROVAVELMENTE DIFERENTE" de vez em quando.

    Aceitar isso como voto criaria uma quarta categoria na contagem — ou, pior,
    faria a taxa de DIFERENTE variar conforme o humor da redação. `INCERTO` é
    onde o que não é resposta tem de cair.
    """
    _dublar(monkeypatch, [{"veredito": "PROVAVELMENTE DIFERENTE", "motivo": "acho"}])
    r = J.julgar([PAR], MAPA, threads=1, verboso=False)[0]
    assert r["veredito"] == "INCERTO"


def test_incerto_e_resposta_legitima(monkeypatch):
    """Forçar binário faz o modelo chutar, e chute contado como medição é pior
    que medição faltando. O prompt oferece `INCERTO` de propósito."""
    assert "INCERTO" in J.VEREDITOS
    assert "não chute" in J.PROMPT
    _dublar(monkeypatch, [{"veredito": "INCERTO", "motivo": "não dá para saber"}])
    r = J.julgar([PAR], MAPA, threads=1, verboso=False)[0]
    assert r["veredito"] == "INCERTO"


def test_lote_que_falha_nao_some_da_contagem(monkeypatch):
    """Par que some faz o denominador encolher e a taxa subir sozinha.

    Aconteceu na primeira execução: 15% dos lotes voltavam com JSON truncado. Se
    eles tivessem sumido em silêncio, a taxa de DIFERENTE teria sido publicada
    sobre 85% da amostra como se fosse sobre 100%.
    """
    def _explode(pares, mapa):
        raise ValueError("JSON truncado")

    monkeypatch.setattr(J, "_chamar", _explode)
    r = J.julgar([PAR, dict(PAR, id_a="a2")], MAPA, threads=1, verboso=False)
    assert len(r) == 2, "par sumiu da contagem"
    assert all(x["veredito"] == "FALHOU" for x in r)
    assert "JSON truncado" in r[0]["motivo"]


def test_o_modelo_recebe_o_nome_completo_e_nao_o_nucleo():
    """O núcleo discriminante é o nome MENOS os tokens de contexto.

    Julgar por ele seria julgar por menos do que existe: o contexto removido —
    o nome do shopping, o bairro — é às vezes justamente o que diz que os dois
    estão na mesma galeria, ou que não estão.
    """
    txt = J._descrever(PAR, MAPA)
    assert "Fashion Hair Visuale" in txt, "usou o núcleo em vez do nome da fonte"
    assert "Beauty Salon" in txt and "Barbershop" in txt, "a categoria não foi junto"
    assert "9.25" in txt and "telefone" in txt, "distância e evidência precisam ir"


def test_sem_o_bruto_cai_para_o_nucleo_em_vez_de_sumir():
    """Descartar o par por falta do bruto encolheria a amostra em silêncio —
    e enviesaria, porque quem falta não falta ao acaso."""
    txt = J._descrever(PAR, {})
    assert "fashion hair visuale" in txt and "barbershop helio s" in txt


def test_o_prompt_diz_que_telefone_igual_nao_e_prova():
    """É a instrução que carrega a medição inteira.

    Sem ela o modelo herda o senso comum de que telefone igual = mesmo lugar, e
    devolveria MESMO para os pares que estamos justamente tentando contar. No
    varejo brasileiro o mesmo número atende dois negócios do mesmo dono com
    frequência — foi o que a amostra do RS mostrou.
    """
    assert "Telefone igual NÃO é prova" in J.PROMPT


def test_a_ia_do_julgamento_tambem_e_so_da_spark():
    """Mesma regra do resto da cadeia: só a Spark carrega modelo.

    O julgamento importa a guarda do `segmentar_endereco`, que por sua vez a
    pega do `endpoints`. Este teste existe para que a importação não se perca
    numa refatoração — o julgamento é caro e roda sozinho.
    """
    with pytest.raises(SystemExit) as e:
        J._conferir_endpoint("http://100.115.117.49:8081/v1")
    assert "100.115.117.49" in str(e.value), \
        "a recusa precisa dizer QUAL host foi barrado"


