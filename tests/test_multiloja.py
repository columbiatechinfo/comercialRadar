# -*- coding: utf-8 -*-
"""Mais de dois nomes no mesmo lugar é um prédio, não uma dúvida de nome.

REGRA DO DONO DO PRODUTO, 27/08/2026

    "mais de 2 itens de nome diferente no mesmo lugar já não é apenas
    ambiguidade de nome do mesmo estabelecimento igual o restaurante por
    exemplo, mais de 2 significa um shopping ou multilojas, nesse caso, cada um
    é um estabelecimento mesmo"

DOIS nomes ainda pode ser o mesmo negócio escrito de duas formas — foi o caso
do "Restaurante Tempero e Arte" e do "Tempero & Arte" no mesmo número. TRÊS ou
mais não: é galeria, shopping, centro clínico, campus.

O QUE A REGRA CONSERTA, medido em Canoas:

    ParkShoppingCanoas + Pista de Patinação (Iceland) — o shopping fundido com
    a pista dentro dele. A IA decidiu por `mesmo domínio:
    parkshoppingcanoas.com.br · a 12 m`, e as duas evidências são VERDADEIRAS:
    o domínio é do shopping e todas as lojas o exibem.

    No 4545 da Avenida Farroupilha há 181 nomes distintos. Endereço, domínio e
    coordenada são iguais para todos os 181 — nenhum deles identifica ninguém.

POR QUE O TESTE CONSTRÓI O CENÁRIO EM VEZ DE MEDIR O BANCO

A fusão errada já aconteceu, e depois dela `corrigir_coordenada` moveu um dos
pontos: hoje eles estão a ~98 m, não a 12. O par não se reproduz consultando o
banco. Os números aqui são os do incidente — 12 m, o domínio real, os dois
logradouros como estão gravados —, e é por isso que o cenário é escrito.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cruzar_fontes as cf  # noqa: E402
import evidencia as ev  # noqa: E402


def _poi(pid, nome, logr, num="", lat=-29.914949, lng=-51.165644, site="",
         cat=""):
    return {"id": pid, "nome": nome, "logr_marcado": logr, "tier": "CONFIRMA",
            "numero_canonico": num, "lat": lat, "lng": lng, "site": site,
            "telefone": "", "categoria": cat, "endereco": "", "evid": 0}


# ── o incidente ───────────────────────────────────────────────────────────

def _o_par_do_shopping():
    """Os dois POIs como estão no banco, e a 12 m como estavam na fusão."""
    shopping = _poi(176532, "ParkShoppingCanoas", "AVENIDA FARROUPILHA", "4545",
                    site="parkshoppingcanoas.com.br")
    pista = _poi(216772, "Pista de Patinação (Iceland)", "PARKSHOPPINGCANOAS",
                 lat=-29.914949 + 12 / 111320.0,
                 site="https://www.parkshoppingcanoas.com.br/")
    return shopping, pista


def test_sem_a_regra_o_shopping_funde_com_a_loja_de_dentro():
    """A linha de base. Sem isto, o teste seguinte passaria por não haver nada
    a impedir, e o arquivo viraria decoração."""
    a, b = _o_par_do_shopping()
    a["multiloja"] = b["multiloja"] = False
    r = ev.avaliar(a, b)
    assert r["decisao"] != "descartar", \
        "o par deixou de ser fundível por outro motivo — refazer o cenário"
    assert "mesmo domínio" in " · ".join(r["motivos"]), \
        "o domínio compartilhado sumiu da evidência"


def test_com_a_regra_o_shopping_nao_funde_com_a_loja_de_dentro():
    """O conserto. Nomes diferentes, no mesmo lugar de mais de dois nomes."""
    a, b = _o_par_do_shopping()
    a["multiloja"] = b["multiloja"] = True
    r = ev.avaliar(a, b)
    assert r["decisao"] == "descartar", \
        f"o shopping voltou a fundir com a loja de dentro: {r['porque']}"
    assert "multiloja" in r["porque"]


def test_a_porta_nao_precisa_casar_para_o_lugar_ser_o_mesmo():
    """A versão que EXIGIA a mesma porta não teria pego o incidente.

    O shopping está em "AVENIDA FARROUPILHA 4545"; a pista dentro dele tem
    logradouro "PARKSHOPPINGCANOAS" e NENHUM número. Casar string de endereço
    deixaria de fora justamente quem a regra existe para separar."""
    a, b = _o_par_do_shopping()
    assert ev.logradouro_de(a) != ev.logradouro_de(b), \
        "o cenário deixou de refletir o incidente: as portas casam"
    a["multiloja"] = b["multiloja"] = True
    assert ev.avaliar(a, b)["decisao"] == "descartar"


# ── o que a regra NÃO pode quebrar ────────────────────────────────────────

def test_a_mesma_loja_com_o_mesmo_nome_continua_fundindo():
    """Duas fontes gravando a MESMA loja do shopping são a mesma loja. A regra
    fala de nome DIFERENTE — se ela comesse isto, o shopping ficaria cheio de
    duplicatas."""
    a = _poi(1, "Cobasi", "AVENIDA FARROUPILHA", "4545")
    b = _poi(2, "Cobasi", "AVENIDA FARROUPILHA", "4545",
             lat=-29.914949 + 5 / 111320.0)
    a["multiloja"] = b["multiloja"] = True
    r = ev.avaliar(a, b)
    assert r["decisao"] == "fundir", \
        f"a mesma loja parou de fundir dentro do shopping: {r['porque']}"


def test_dois_nomes_na_mesma_porta_nao_e_multiloja():
    """O caso que o dono do produto separou explicitamente: DOIS ainda é
    ambiguidade de nome. "Restaurante Tempero e Arte" e "Tempero & Arte" no
    mesmo número são o mesmo restaurante."""
    pois = [_poi(1, "Restaurante Tempero e Arte", "RUA TIRADENTES", "310"),
            _poi(2, "Tempero & Arte", "RUA TIRADENTES", "310")]
    cf._marcar_multiloja(pois)
    assert not any(p["multiloja"] for p in pois), \
        "dois nomes na mesma porta viraram multiloja"


def test_tres_nomes_na_mesma_porta_e_multiloja():
    """O limiar, do outro lado. `TETO_MULTILOJA` é 2, então 3 já é prédio."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    cf._marcar_multiloja(pois)
    assert all(p["multiloja"] for p in pois), \
        f"3 nomes na mesma porta não viraram multiloja (teto={ev.TETO_MULTILOJA})"


# ── a marcação ────────────────────────────────────────────────────────────

def test_a_marca_contagia_quem_esta_ao_lado_sem_numero():
    """A loja de dentro costuma não ter número — é o caso da pista de
    patinação. Marcar só quem casa a porta a deixaria de fora."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    dentro = _poi(9, "Pista de Patinação", "PARKSHOPPINGCANOAS",
                  lat=-29.914949 + 12 / 111320.0)
    pois.append(dentro)
    cf._marcar_multiloja(pois)
    assert dentro["multiloja"], \
        "quem está a 12 m de uma porta-multiloja não herdou a marca"


def test_o_contagio_tem_alcance_e_nao_pega_a_cidade():
    """`RAIO_M` e não "o bairro". Um ponto a 200 m do shopping é outro lugar —
    marcar tudo faria a regra recusar fusão legítima pela cidade inteira."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    longe = _poi(9, "Padaria da Esquina", "RUA OUTRA",
                 lat=-29.914949 + 200 / 111320.0)
    pois.append(longe)
    cf._marcar_multiloja(pois)
    assert not longe["multiloja"], "a marca vazou para 200 m de distância"


def test_a_contagem_usa_a_mesma_chave_que_a_fusao():
    """Contar por `endereco` cru separaria "Av. Farroupilha, 4545" de "AVENIDA
    FARROUPILHA, 4545 - LUC 3003", e o shopping deixaria de parecer shopping.
    A contagem usa `logradouro_de`, a mesma função que decide `mesma_rua`."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    i = s.index("def _marcar_multiloja(")
    corpo = s[i:s.index("\ndef ", i + 10)]
    assert "ev.logradouro_de(p)" in corpo, \
        "a contagem passou a usar outra chave de endereço que a fusão"
    assert "ev.norm_nome(" in corpo, \
        "os nomes deixaram de ser normalizados antes de contar distintos"


def test_so_conta_porta_com_numero():
    """Mesma rua sem número não é o mesmo lugar: uma avenida inteira teria
    centenas de nomes sem ser galeria nenhuma."""
    pois = [_poi(i, n, "AVENIDA BRASIL")
            for i, n in enumerate(("Um", "Dois", "Três", "Quatro"), 1)]
    for k, p in enumerate(pois):
        p["lat"] = -29.9 + k * 700 / 111320.0
    cf._marcar_multiloja(pois)
    assert not any(p["multiloja"] for p in pois), \
        "uma avenida sem números virou multiloja"
