# -*- coding: utf-8 -*-
"""Multiloja é MARCA no ponto, e não regra de fusão.

A HISTÓRIA DESTE ARQUIVO, PORQUE ELA É A LIÇÃO

Em 27/08/2026 nasceu aqui uma regra que DESCARTAVA o par quando os dois POIs
estavam no mesmo lugar, o lugar reunia mais de dois nomes distintos e os nomes
deles diferiam. A observação por trás continua verdadeira: no 4545 da Avenida
Farroupilha há 181 estabelecimentos, e endereço, domínio e coordenada são
idênticos para os 181 — nenhum identifica ninguém.

O CORTE ERRAVA 32% DAS VEZES. Medido sobre as recusas reais em Canoas:

    Master Sonho Colchões     + Master Sonho Colchões | Canoas    0 m
    Preciosa Boutique Atacado + Preciosa Boutique Atacado         0 m
    Crazy Som - Locação       + Crazy Som                         7 m

São o mesmo negócio. `semelhanca_nome` é Jaccard sobre tokens, e um nome que é
o outro MAIS UM SUFIXO cai para 0,75 — abaixo do limiar de 0,8. Um número fixo
não distingue "sufixo de filial" de "outra loja".

A IA DISTINGUE, e foi verificada nos mesmos pares antes da decisão. De 120:

    Unimed Porto Alegre + Coloprocto ............ DIFERENTE   certo
    Agah + Agência Treehauss .................... DIFERENTE   certo
    NGA Móveis Hospitalares + NGA Metalúrgica ... DIFERENTE   certo
    Master Sonho Colchões + ... | Canoas ........ MESMO       certo
    Crazy Som - Locação + Crazy Som ............. MESMO       certo

Decisão do dono do produto, 28/08/2026: é melhor que mais pares CHEGUEM à IA e
ela resolva — *"mesmo o shopping tendo vários no mesmo endereço, cada um
viraria um ponto individual porque seus nomes mostram que claramente são pontos
diferentes"*. O nome é a evidência, e ler nome é o que a IA faz melhor que um
limiar.

A marca sobreviveu à regra: ela vira `pois.multiloja` (migração 0037), porque
saber que um ponto está num prédio de várias lojas vale na tela e na revisão.
Ela só não decide mais nada sozinha.
"""
import io
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


# ── a marca NÃO decide ────────────────────────────────────────────────────

def test_a_multiloja_nao_descarta_o_par():
    """O recuo. O par do shopping tem de CHEGAR à IA, não morrer aqui.

    Os dois POIs do incidente: o shopping e a pista de patinação dentro dele,
    a 12 m, com o mesmo domínio. A regra antiga os descartava; hoje a evidência
    é apurada e a decisão é de quem sabe ler nome."""
    a = _poi(176532, "ParkShoppingCanoas", "AVENIDA FARROUPILHA", "4545",
             site="parkshoppingcanoas.com.br")
    b = _poi(216772, "Pista de Patinação (Iceland)", "PARKSHOPPINGCANOAS",
             lat=-29.914949 + 12 / 111320.0,
             site="https://www.parkshoppingcanoas.com.br/")
    a["multiloja"] = b["multiloja"] = True
    r = ev.avaliar(a, b)
    assert r["decisao"] != "descartar", \
        f"o corte da multiloja voltou: {r['porque']}"


def test_a_marca_nao_muda_veredito_nenhum():
    """Mais forte que o teste acima: ligar a marca não pode alterar NADA na
    decisão. Se alterar, ela voltou a ser regra por algum caminho."""
    casos = [
        # o mesmo negócio com sufixo — o falso positivo que derrubou a regra
        (_poi(1, "Master Sonho Colchões", "AVENIDA FARROUPILHA", "4545"),
         _poi(2, "Master Sonho Colchões | Canoas", "AVENIDA FARROUPILHA", "4545")),
        # dois negócios distintos na mesma porta
        (_poi(3, "Unimed Porto Alegre", "AVENIDA INCONFIDENCIA", "650"),
         _poi(4, "Coloprocto", "AVENIDA INCONFIDENCIA", "650",
              lat=-29.914949 + 14 / 111320.0)),
        # nomes iguais, mesma porta
        (_poi(5, "Cobasi", "AVENIDA FARROUPILHA", "4545"),
         _poi(6, "Cobasi", "AVENIDA FARROUPILHA", "4545")),
    ]
    for a, b in casos:
        a["multiloja"] = b["multiloja"] = False
        sem = ev.avaliar(a, b)
        a["multiloja"] = b["multiloja"] = True
        com = ev.avaliar(a, b)
        assert sem["decisao"] == com["decisao"], (
            f"a marca mudou o veredito de {a['nome']!r} + {b['nome']!r}: "
            f"{sem['decisao']} → {com['decisao']}")


def test_o_limiar_de_nome_deixaria_passar_o_mesmo_negocio():
    """A medida que condenou a regra, guardada como número.

    "Master Sonho Colchões" e "Master Sonho Colchões | Canoas" são o mesmo
    negócio, e a semelhança entre eles fica ABAIXO do limiar de 0,8 que a regra
    usava para dizer "nomes diferentes". Enquanto isto for verdade, nenhum
    corte pode se apoiar só nesse limiar."""
    s = ev.semelhanca_nome("Master Sonho Colchões", "Master Sonho Colchões | Canoas")
    assert s < 0.8, \
        f"a semelhança mudou ({s:.2f}) — reavaliar se o corte volta a ser viável"


# ── a marca continua existindo, como dado ─────────────────────────────────

def test_tres_nomes_na_mesma_porta_e_multiloja():
    """`TETO_MULTILOJA` é 2, então 3 já é prédio."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    cf._marcar_multiloja(pois)
    assert all(p["multiloja"] for p in pois), \
        f"3 nomes na mesma porta não viraram multiloja (teto={ev.TETO_MULTILOJA})"


def test_dois_nomes_na_mesma_porta_nao_e_multiloja():
    """DOIS ainda é ambiguidade de nome: "Restaurante Tempero e Arte" e
    "Tempero & Arte" no mesmo número são o mesmo restaurante."""
    pois = [_poi(1, "Restaurante Tempero e Arte", "RUA TIRADENTES", "310"),
            _poi(2, "Tempero & Arte", "RUA TIRADENTES", "310")]
    cf._marcar_multiloja(pois)
    assert not any(p["multiloja"] for p in pois)


def test_a_marca_contagia_quem_esta_ao_lado_sem_numero():
    """A loja de dentro do shopping costuma usar o NOME DO PRÉDIO como
    logradouro e não ter número — é o caso da pista de patinação, cujo
    logradouro é `PARKSHOPPINGCANOAS`. Marcar só quem casa a porta a deixaria
    de fora."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    dentro = _poi(9, "Pista de Patinação", "PARKSHOPPINGCANOAS",
                  lat=-29.914949 + 12 / 111320.0)
    pois.append(dentro)
    cf._marcar_multiloja(pois)
    assert dentro["multiloja"], \
        "quem está a 12 m de uma porta-multiloja não herdou a marca"


def test_o_contagio_tem_alcance_e_nao_pega_a_cidade():
    """`RAIO_M`, e não "o bairro"."""
    pois = [_poi(i, n, "AVENIDA FARROUPILHA", "4545")
            for i, n in enumerate(("Spoleto", "Cobasi", "POA Parrilla"), 1)]
    longe = _poi(9, "Padaria da Esquina", "RUA OUTRA",
                 lat=-29.914949 + 200 / 111320.0)
    pois.append(longe)
    cf._marcar_multiloja(pois)
    assert not longe["multiloja"], "a marca vazou para 200 m de distância"


def test_so_conta_porta_com_numero():
    """Mesma rua sem número não é o mesmo lugar: uma avenida inteira teria
    centenas de nomes sem ser galeria nenhuma."""
    pois = [_poi(i, n, "AVENIDA BRASIL")
            for i, n in enumerate(("Um", "Dois", "Três", "Quatro"), 1)]
    for k, p in enumerate(pois):
        p["lat"] = -29.9 + k * 700 / 111320.0
    cf._marcar_multiloja(pois)
    assert not any(p["multiloja"] for p in pois)


# ── a marca é gravada, e não envelhece ────────────────────────────────────

def test_a_marca_vai_para_o_banco():
    """Serve à tela e à revisão humana — é o que sobrou da regra."""
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in s.splitlines()
                       if not l.lstrip().startswith("#"))
    assert "set multiloja =" in codigo, \
        "o cruzamento deixou de gravar a marca de multiloja"


def test_a_gravacao_apaga_a_marca_de_quem_deixou_de_ser():
    """Gravar só os `true` deixaria a marca envelhecer: um endereço que perdeu
    estabelecimentos continuaria marcado para sempre. O UPDATE escreve os dois
    lados sobre todos os POIs comparados."""
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in s.splitlines()
                       if not l.lstrip().startswith("#"))
    i = codigo.index("set multiloja =")
    assert "(id = any(" in codigo[i:i + 120], \
        "a marca voltou a ser gravada só para quem é multiloja, e envelhece"


def test_a_coluna_existe_no_banco():
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        cur.execute("""select data_type, is_nullable from information_schema.columns
                        where table_name = 'pois' and column_name = 'multiloja'""")
        r = cur.fetchone()
    finally:
        con.close()
    assert r, "a coluna `multiloja` não existe — aplicar a migração 0037"
    assert r[0] == "boolean" and r[1] == "NO", f"tipo inesperado: {r}"


def test_a_marca_e_gravada_mesmo_sem_par_para_comparar():
    """O DEFEITO QUE ESTE TESTE GUARDA, medido em 28/08/2026.

    A gravação da marca morava dentro do bloco de carimbo do `cruzado_em`, que
    fica DEPOIS do `if not pares: return`. Numa rodada sem par nenhum a marca
    simplesmente não era atualizada — e essa é a rodada COMUM: a mineração das
    08:19 daquele dia carregou 32.361 POIs, comparou zero pares (todos já
    carimbados) e saiu sem tocar nela.

    O dado envelhecia exatamente nas passadas baratas, que são a maioria."""
    codigo = _so_codigo("cruzar_fontes.py")
    i = codigo.index("if not pares:")
    saida = codigo[i:i + 400]
    assert "_gravar_multiloja" in saida, \
        "a saída antecipada voltou a pular a gravação da marca"

    # e continua acontecendo no caminho normal
    assert codigo.count("_gravar_multiloja(cur, pois)") >= 2, \
        "a marca deixou de ser gravada em um dos dois caminhos"


def _so_codigo(nome):
    """O arquivo sem COMENTÁRIOS e sem DOCSTRINGS.

    A primeira versão do teste acima tirava só os comentários, e casou com a
    docstring do próprio `_gravar_multiloja` — que cita `if not pares: return`
    ao explicar por que a função existe. É a terceira vez num dia que um teste
    meu examina prosa achando que examina código.
    """
    import ast
    s = io.open(os.path.join(RAIZ, nome), encoding="utf-8").read()
    for no in ast.walk(ast.parse(s)):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                           ast.Module)) and ast.get_docstring(no):
            s = s.replace(no.body[0].value.value, "")
    return "\n".join(l for l in s.splitlines()
                     if not l.lstrip().startswith("#"))
