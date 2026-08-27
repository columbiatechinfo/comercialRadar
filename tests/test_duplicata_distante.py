# -*- coding: utf-8 -*-
"""Duplicata longe existe, e o número da porta é quem decide se é duplicata.

DOIS DEFEITOS ACHADOS EM 27/08/2026, um dentro do outro.

O PRIMEIRO: a regra não alcançava o par.

A regra do dono do produto — "mesmo nome e mesmo logradouro é confiança máxima,
mesmo a 50 metros ou 100" — foi implementada em `evidencia.avaliar` com alcance
de 1.000 m. Mas `cruzar_fontes.candidatos` só propunha par pela grade de células
de 111 m, varrendo 3x3 — alcance real de ~330 m. A regra virou letra morta
justamente na faixa que existia para cobrir.

MEDIDO em Canoas, depois de uma rodada completa: 344 grupos de mesmo nome +
mesmo logradouro continuavam separados, e 334 estavam a MAIS de 100 m. Só 8
dentro de 20 m — o buraco era quase todo fora do alcance da grade.

O conserto não é alargar a grade (1 km faria os 2,7 milhões de pares virarem
~100 milhões). "Mesmo nome na mesma rua" é uma CHAVE, não um raio: um
dicionário resolve em O(n). Custo medido do segundo caminho: 412 pares a mais,
0,017% do total.

O SEGUNDO: sem o número, a regra fundia rede legítima.

Dos 266 pares que ela fundiria, 48 eram "Saque e Pague" nos números 1011 e 1623
da mesma avenida — caixas eletrônicos distintos da mesma rede. Fundir apagaria
ponto real do mapa.

Mas 167 tinham o MESMO número: "Posto Ipiranga, Guilherme Schell 1046" contra o
mesmo endereço, a 7,7 km. Aí a distância é a coordenada de uma fonte errando —
nunca a identidade. Quando as duas dizem a mesma porta, o endereço já provou o
que a coordenada nega.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import evidencia as ev  # noqa: E402
import cruzar_fontes as cf  # noqa: E402


def _poi(pid, nome, lat, lng, rua, num=""):
    return {"id": pid, "nome": nome, "lat": lat, "lng": lng,
            "logr_marcado": rua, "logr_original": rua, "numero_canonico": num,
            "tier": "CONFIRMA", "telefone": "", "website": "", "categoria": "",
            "evid": 0}


# --------------------------------------------------------------------------
# 1. o par tem de ser PROPOSTO
# --------------------------------------------------------------------------
def test_mesmo_nome_e_rua_vira_par_mesmo_longe():
    """O defeito original: a 500 m, a grade nunca propunha o par, e a regra dos
    1.000 m nunca era consultada."""
    a = _poi(1, "Barbearia do Totto", -29.9200, -51.1800, "RUA MATEO BEI", "100")
    b = _poi(2, "Barbearia do Totto", -29.9245, -51.1800, "RUA MATEO BEI", "100")
    # ~500 m: fora das 3x3 células de 111 m
    assert ev.distancia_m(a["lat"], a["lng"], b["lat"], b["lng"]) > 400
    pares = cf.candidatos([a, b])
    assert len(pares) == 1, "o par distante de mesmo nome+rua não foi proposto"


def test_a_grade_continua_pegando_quem_nao_tem_nome_em_comum():
    """O segundo caminho ACRESCENTA, não substitui: duplicata com nomes
    diferentes ("Farmácia" x "Drogaria São João" na mesma porta) só aparece pela
    vizinhança."""
    a = _poi(1, "Farmacia Sao Joao", -29.9200, -51.1800, "RUA A", "10")
    b = _poi(2, "Drogaria Sao Joao", -29.92005, -51.18005, "RUA B", "10")
    assert len(cf.candidatos([a, b])) == 1, "a vizinhança deixou de propor par"


def test_o_grupo_gigante_nao_explode_e_e_dito():
    """Nome genérico em avenida longa: 40 POIs dariam 780 pares sozinhos. Acima
    do teto o grupo fica para a vizinhança — e isso é DITO, nunca calado."""
    assert cf.TETO_GRUPO_NOME <= 30, "o teto do grupo por nome ficou alto demais"
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    i = s.index("def candidatos(")
    corpo = s[i:i + 4200]
    assert "ficaram para a vizinhança" in corpo, \
        "o grupo cortado pelo teto voltou a ser omitido"

    # e o corte funciona de fato
    muitos = [_poi(i, "Farmacia", -29.92 - i * 0.01, -51.18, "AVENIDA LONGA", str(i))
              for i in range(cf.TETO_GRUPO_NOME + 5)]
    # espalhados o bastante para a grade não os unir
    assert cf.candidatos(muitos) == [], \
        "o grupo acima do teto voltou a gerar par por nome"


# --------------------------------------------------------------------------
# 2. o número da porta decide
# --------------------------------------------------------------------------
def test_mesmo_numero_funde_a_qualquer_distancia():
    """"Posto Ipiranga, Guilherme Schell 1046" x o mesmo, a 7,7 km. Duas fontes
    dizendo a mesma porta é mais forte que a coordenada de uma delas."""
    a = _poi(1, "Posto Ipiranga", -29.9200, -51.1800, "AVENIDA GUILHERME SCHELL", "1046")
    b = _poi(2, "Posto Ipiranga", -29.8500, -51.1800, "AVENIDA GUILHERME SCHELL", "1046")
    r = ev.avaliar(a, b)
    assert r["decisao"] == "fundir", f"deixou de fundir mesmo endereço: {r}"
    assert r["dist_m"] > 5000, "o caso deixou de ser o de coordenada podre"


def test_numero_diferente_nao_funde_sozinho():
    """OS 48 "Saque e Pague". Mesma rede, portas 1011 e 1623 da mesma avenida:
    são caixas distintos. Fundir apagaria ponto real — a decisão vai para a IA,
    não para o atalho."""
    a = _poi(1, "Saque e Pague", -29.9200, -51.1800, "AVENIDA GUILHERME SCHELL", "1011")
    b = _poi(2, "Saque e Pague", -29.9250, -51.1800, "AVENIDA GUILHERME SCHELL", "1623")
    r = ev.avaliar(a, b)
    assert r["decisao"] != "fundir", \
        f"voltou a fundir rede em portas diferentes: {r['porque']}"


def test_sem_numero_o_teto_de_1km_volta_a_valer():
    """Faltando a porta, só resta a proximidade para sustentar — e aí o teto
    original protege contra a filial do outro bairro."""
    perto_a = _poi(1, "Mercado Uniao", -29.9200, -51.1800, "RUA MATEO BEI", "")
    perto_b = _poi(2, "Mercado Uniao", -29.9240, -51.1800, "RUA MATEO BEI", "")
    assert ev.avaliar(perto_a, perto_b)["decisao"] == "fundir"

    longe_b = _poi(3, "Mercado Uniao", -29.9800, -51.1800, "RUA MATEO BEI", "")
    r = ev.avaliar(perto_a, longe_b)
    assert r["dist_m"] > 1000
    assert r["decisao"] != "fundir", "sem número, passou a fundir além de 1 km"
