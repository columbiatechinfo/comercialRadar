# -*- coding: utf-8 -*-
"""Achar a loja do iFood a partir de um POI — e não achar a errada.

A descoberta pelo iFood morreu (Turnstile interativo, API 404/403, app blindado).
Sobrou o `/extra`, que dá CNPJ sem navegador — mas para chamá-lo é preciso o id,
e o id vem do link. Esta é a corrente que acha o link certo.

CADA TESTE AQUI É UM DEFEITO QUE CUSTOU UMA MEDIÇÃO ERRADA em 26/08/2026.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import enriquecer_por_ifood as e  # noqa: E402


def test_subdominio_de_rede_e_loja_valida():
    """`madero.ifood.com.br` é loja como qualquer outra. Exigir `www` descartava
    as redes grandes — o Madero apareceu na busca e foi jogado fora."""
    assert e.id_do_link(
        "https://madero.ifood.com.br/delivery/canoas-rs/koi-sushi/"
        "958b81f6-cc30-4287-9931-aaaabbbbcccc")
    assert e.id_do_link("https://www.ifood.com.br/restaurantes") == ""


def test_o_redirecionador_do_buscador_e_desembrulhado():
    """"Sonho Meu Doceria" trouxe 9 resultados, TODOS como `bing.com/ck/a?u=…`.
    Sem desembrulhar, uma busca que ACHOU virava `sem_link`."""
    real = e._desembrulhar(
        "https://r.search.yahoo.com/x/RU=https%3A%2F%2Fwww.ifood.com.br"
        "%2Fdelivery%2Fx%2Fy%2Fabc/RK=2")
    assert real.startswith("https://www.ifood.com.br/delivery/")


def test_todos_os_candidatos_sao_coletados():
    """Pegar o PRIMEIRO link era o maior defeito: "Sushi Arte" devolvia sete
    lojas e o código ficava com `daniisushi-harmonia`."""
    links = [("https://www.ifood.com.br/delivery/canoas-rs/a/11111111-1111-4111-8111-111111111111", ""),
             ("https://www.ifood.com.br/delivery/canoas-rs/b/22222222-2222-4222-8222-222222222222", "")]
    assert len(e.candidatos_de(links)) == 2


def test_a_trava_afrouxa_quando_o_nome_bate():
    """"Santo Açaí Oficial" casou com uma loja de nome IDÊNTICO e foi recusada
    por estar a 2.147 m. A distância vinha da coordenada do POI (a base
    estadual erra por quilômetros), não de ser outra loja.

    Nome idêntico é evidência muito mais forte que proximidade; quando ele bate,
    a distância vira guarda-corpo contra a homônima de outra cidade."""
    poi = {"nome": "Santo Açaí Oficial"}
    assert e._raio_para(poi, {"name": "Santo Açaí Oficial"}) == e.RAIO_NOME_EXATO_M
    assert e._raio_para(poi, {"name": "La Casa de Açaí"}) == e.RAIO_CONFERE_M


def test_a_resposta_da_ia_e_lida_nos_tres_formatos():
    """Um caso devolve objeto solto; vários devolvem array OU um objeto por
    linha. O parser exigia array e transformava ACERTO em recusa — a IA
    respondeu `{"escolha": 1}` e o placar marcou "recusou", 6 de 6."""
    s = io.open(os.path.join(RAIZ, "enriquecer_por_ifood.py"), encoding="utf-8").read()
    i = s.index("def _chamar_ia(")
    corpo = s[i:i + 2600]
    assert "finditer" in corpo, "voltou a exigir um formato só"
    assert "Extra data" in corpo, "o motivo do formato múltiplo saiu do código"


def test_erro_dentro_da_thread_e_dito():
    """Engolir a exceção em silêncio fez uma falha minha virar "a IA recusou
    todos". Erro escondido em thread é o mais caro de achar."""
    s = io.open(os.path.join(RAIZ, "enriquecer_por_ifood.py"), encoding="utf-8").read()
    i = s.index("def julgar_lojas(")
    corpo = s[i:i + 3000]
    assert "lote nao julgado" in corpo, "o erro da thread voltou a ser silencioso"


def test_a_ia_pode_recusar_e_isso_e_resposta():
    """`escolha: 0` é legítimo e obrigatório: gravar o CNPJ da loja errada é
    pior que não gravar — vira dado falso com aparência de verificado."""
    s = io.open(os.path.join(RAIZ, "enriquecer_por_ifood.py"), encoding="utf-8").read()
    assert "RESPONDA 0 QUANDO" in s
    assert "ia_recusou" in s


def test_a_trava_vale_acima_da_ia():
    """A IA lê nome, não mede metro. Se ela escolher e a coordenada desmentir,
    a distância ainda derruba o par."""
    s = io.open(os.path.join(RAIZ, "enriquecer_por_ifood.py"), encoding="utf-8").read()
    i = s.index("escolhas = await asyncio.to_thread")
    assert "_raio_para" in s[i:i + 900], "a trava deixou de valer depois da IA"
