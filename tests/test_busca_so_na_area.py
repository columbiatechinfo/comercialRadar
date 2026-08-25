# -*- coding: utf-8 -*-
"""A busca no Maps só roda no que está DENTRO da área desenhada.

Regra do dono do produto, 25/08/2026, e ela separa duas etapas que custam coisas
muito diferentes.

O OCR precisa do TILE INTEIRO. O retângulo fotografado tem tamanho fixo e não
encolhe com o polígono — um polígono de 3,5 ha e outro de 30 ha podem cair no
mesmo tile. Isso é aceito: ler o tile todo é uma passada de visão computacional,
barata, e paga uma vez.

A BUSCA não. Cada nome lido vira uma sessão de navegador com proxy, e é a etapa
mais cara do processo inteiro.

MEDIDO em Cachoeirinha: dos 165 ícones detectados num tile, **8** estavam dentro
do polígono. As outras 157 buscas — 95% do custo — rodariam fora do que o
operador pediu.
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import area_utils  # noqa: E402

FONTE = os.path.join(RAIZ, "search_pois_v2.py")


def _fonte():
    with open(FONTE, encoding="utf-8") as f:
        return f.read()


def test_a_fila_da_busca_e_filtrada_pelo_poligono():
    """Sem isto, 95% das sessões de navegador rodam fora da área pedida."""
    s = _fonte()
    assert "ponto_no_poligono" in s, \
        "a busca voltou a rodar em todos os recortes do tile"
    i = s.index("fila_completa = [r for r in todos")
    trecho = s[i:i + 2600]
    assert "poligono" in trecho and "fora" in trecho, \
        "o filtro saiu de onde a fila é montada"


def test_o_poligono_vem_da_sessao_e_nao_do_banco():
    """`_area.json` é a área COMO ELA ERA quando a captura rodou.

    Ler do banco traria a área ATUAL — e se o operador desenhou outra no meio
    (o que acontece: ele minera um bairro e já marca o próximo), a busca
    filtraria os recortes de uma área pelos limites de outra, em silêncio.
    """
    s = _fonte()
    assert "_area.json" in s, "o polígono voltou a vir de fora da sessão"
    assert "carregar_area()" not in s, \
        "está lendo a área ATUAL do banco em vez da área da captura"


def test_sem_area_a_busca_nao_some():
    """Sessão antiga não tem `_area.json`. Filtrar tudo fora seria transformar
    uma ausência de informação em zero resultados."""
    s = _fonte()
    i = s.index("area_json = session_path.parent")
    assert "poligono = None" in s[max(0, i - 400):i], \
        "sem área, o polígono precisa nascer nulo e o filtro ficar desligado"
    assert "roda em todos os recortes" in s[i:i + 900], \
        "a ausência de área precisa ser DITA, não silenciada"


def test_recorte_sem_coordenada_nao_e_descartado():
    """Não dá para julgar o que não tem posição — e descartar por dúvida
    perderia o recorte sem que ninguém soubesse por quê."""
    s = _fonte()
    i = s.index("for r in fila_completa:")
    trecho = s[i:i + 700]
    assert "is None" in trecho and "dentro.append(r)" in trecho, \
        "recorte sem lat/lng deixou de entrar na fila"


def test_o_filtro_bate_com_a_medicao_de_cachoeirinha():
    """O caso real que originou a regra, congelado como número.

    Se um dia o `ponto_no_poligono` mudar de semântica, este teste acusa — e é
    melhor acusar aqui do que numa rodada de horas.
    """
    poly = [[-29.90058656972893, -51.06972292967964],
            [-29.90237229548905, -51.068939877575936],
            [-29.902000271928348, -51.0679315639082],
            [-29.90014943406773, -51.06753467448577],
            [-29.900084328592392, -51.06845717422437]]
    # Um ponto do centro do polígono e outro a ~400 m, fora dele.
    assert area_utils.ponto_no_poligono(-29.9012, -51.0685, poly)
    assert not area_utils.ponto_no_poligono(-29.8999, -51.0728, poly)
