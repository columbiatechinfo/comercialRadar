# -*- coding: utf-8 -*-
"""O vazio do pandas não pode virar a palavra "nan" no banco.

`str(float('nan'))` devolve `'nan'`. A importação estadual testava `if v is None`
— que não pega NaN, NaT nem `pd.NA` — e gravou 105.148 campos com texto onde
devia haver nulo, TODOS em 25/08/2026, numa única tarde de importações:

    28.662  pois.email          18.837  pois.instagram
    28.394  pois.website        17.193  pois.telefone
     7.919  pois.endereco        1.452  pois.nome

O ESTRAGO NÃO FICOU NO BANCO, e é por isso que isto tem teste:

  o painel conta "com telefone" por `telefone is not null`. Em Cachoeirinha ele
  mostrava 11.918 POIs com telefone quando 6.559 tinham — 45% inventado. Em
  site, 74%. O operador montava rota de campo com esse número.

  o cruzamento contava "mesmo domínio: nan" como prova. Das 1.460 fusões que ele
  propunha, 1.334 (91%) eram esse nada casando com esse nada — e fusão errada
  entrega ao cliente a mesma loja duas vezes, ou junta duas lojas numa.

O `cadastur.py` já fazia certo desde sempre, com o comentário "Pandas devolve
NaN, e NaN vira a string 'nan'". O conhecimento existia no projeto; faltava
neste caminho.
"""
import io
import os
import re
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402

LIXO = ("nan", "none", "null", "nat", "<na>")


def _val():
    """Extrai o `val()` de dentro do `ingerir` e o devolve executável.

    Ele é uma função aninhada: importar o módulo não o alcança, e recortá-lo é
    o que permite exercitá-lo com os vazios de verdade do pandas em vez de
    conferir o código por `grep`.
    """
    src = io.open(os.path.join(RAIZ, "extracao_estadual.py"), encoding="utf-8").read()
    ini = src.index("    def val(r, col):")
    fim = src.index("    linhas = []", ini)
    corpo = "import pandas as pd\n" + re.sub(r"^    ", "", src[ini:fim], flags=re.M)
    ns = {}
    exec(corpo, ns)  # noqa: S102 — é o próprio código do projeto
    return ns["val"]


def test_os_vazios_do_pandas_viram_nulo():
    import pandas as pd
    val = _val()
    for vazio in (float("nan"), pd.NA, pd.NaT, None):
        assert val({"c": vazio}, "c") is None, f"{vazio!r} escapou como texto"


def test_o_texto_ja_escrito_assim_na_fonte_tambem():
    """O parquet do Overture traz "None" e "null" DIGITADOS em campo de contato.
    Deixá-los passar reconstrói o mesmo problema por outro caminho."""
    val = _val()
    for s in LIXO + ("NaN", "NULL", "N/A", "-", "  nan  "):
        assert val({"c": s}, "c") is None, f"{s!r} passou como se fosse dado"


def test_texto_de_verdade_sobrevive():
    """A guarda não pode virar um filtro que come dado bom."""
    val = _val()
    assert val({"c": "  Padaria X  "}, "c") == "Padaria X"
    assert val({"c": "Nanuque Turismo"}, "c") == "Nanuque Turismo"
    assert val({"c": "0"}, "c") == "0"
    assert val({"c": "(51) 3470-1234"}, "c") == "(51) 3470-1234"


def test_campo_multivalorado_nao_derruba_a_importacao():
    """`pd.isna` devolve ARRAY para lista, e array num `if` levanta ValueError.

    Sem a guarda de tipo, um campo multivalorado no parquet derrubaria a
    importação inteira do município — o mesmo formato de falha do `pd.NA` que
    já quebrou a extração estadual antes.
    """
    val = _val()
    assert val({"c": ["x", "y"]}, "c") is not None   # vira texto, não explode


def test_a_guarda_esta_no_caminho_da_importacao():
    """Ela precisa estar em `extracao_estadual`, não só neste teste."""
    s = io.open(os.path.join(RAIZ, "extracao_estadual.py"), encoding="utf-8").read()
    assert "pd.isna" in s, "a importação estadual voltou a confiar em `is None`"
    i = s.index("def ingerir(")
    assert "import pandas as pd" in s[i:i + 400], \
        "`pd` saiu do escopo de `ingerir` — o `val` quebraria com NameError"


def test_o_consumidor_tambem_se_defende():
    """Duas guardas, de propósito.

    A da importação impede novo dado sujo; a do `evidencia` protege do que já
    está gravado e do que vier por qualquer outro caminho. Uma só não bastava:
    a base tinha 105 mil campos sujos ANTES de a guarda existir.
    """
    import evidencia as ev
    assert ev.dominio("nan") == ""
    assert ev.so_digitos("nan") == ""


@pytest.mark.parametrize("tabela,coluna", [
    ("pois", "nome"), ("pois", "telefone"), ("pois", "website"),
    ("pois", "email"), ("pois", "instagram"), ("pois", "endereco"),
    ("pois", "categoria"), ("vinculo_poi", "nome"),
])
def test_o_banco_esta_limpo(tabela, coluna):
    """A limpeza de 25/08/2026 corrigiu 105.146 campos. Este teste é o que
    avisa se um caminho novo recomeçar a sujar."""
    import base_comum as bc
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute(f'select count(*) from comercialradar."{tabela}" '
                        f'where lower(trim("{coluna}")) = any(%s)', (list(LIXO),))
            n = cur.fetchone()[0]
    finally:
        con.close()
    assert n == 0, f"{tabela}.{coluna} voltou a ter {n:,} campos com texto de vazio"


# ─── a terceira guarda: o NOME ───────────────────────────────────────────────

def test_nome_vazio_nao_produz_semelhanca():
    """CUSTOU CINCO PISCINAS, 26/08/2026.

    Em Bento Gonçalves, quatro fusões foram aplicadas entre POIs a 96–119 m com
    o motivo "nomes iguais (100%)". Os nomes eram, os dois, a palavra "nan".
    Eram cinco clubes com piscina distintos, virados um só — e a IA confirmou,
    porque com "nan" e "nan" na mesa ela não tinha como discordar.

    Domínio, telefone e logradouro já estavam protegidos. O nome, que é o campo
    mais óbvio, não estava.
    """
    import evidencia as ev
    assert ev.semelhanca_nome("nan", "nan") == 0.0
    assert ev.semelhanca_nome("", "") == 0.0
    assert ev.semelhanca_nome("None", "none") == 0.0
    assert ev.semelhanca_nome("Padaria Silva", "Silva Padaria") == 1.0


def test_dois_pois_sem_nome_no_mesmo_bairro_nao_fundem():
    """O caso exato, reconstruído: dois clubes com piscina sem nome, a ~110 m."""
    import evidencia as ev
    a = {"nome": "nan", "categoria": "Piscina/Clube", "lat": -29.150, "lng": -51.510}
    b = {"nome": "nan", "categoria": "Piscina/Clube", "lat": -29.151, "lng": -51.510}
    assert ev.avaliar(a, b)["decisao"] == "descartar"


# O TESTE `test_o_i9_recebe_o_codigo_a_cada_importacao` SAIU DAQUI.
#
# Ele guardava a causa ESTRUTURAL de um defeito que voltou depois de corrigido: o
# i9 rodava uma copia propria de `extracao_estadual.py`, sem sincronia com o
# repositorio. A correcao do vazio do pandas existia aqui e nao la, e em
# 26/08/2026 a importacao de Bento Goncalves regravou 1.408 POIs de nome "nan" —
# e quatro clubes com piscina distintos, a ate 119 m um do outro, foram FUNDIDOS
# por "nomes iguais (100%)".
#
# A cura era mandar o arquivo em base64 ANTES de cada execucao, e o teste cobrava
# exatamente isso. Em 30/08/2026 o sistema passou a rodar no servidor: ha UMA
# copia do codigo, a mesma que se edita. A causa estrutural deixou de existir, e
# com ela a cura.
#
# O QUE CONTINUA VALENDO, e por isso fica escrito aqui: conferir a versao e
# AVISAR nao teria bastado — o aviso chega quando o dado ja entrou. Se algum dia
# voltar a haver duas copias de um importador, a unica defesa que funciona e a
# copia viajar junto, nao o alerta.

