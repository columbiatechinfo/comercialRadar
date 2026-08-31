# -*- coding: utf-8 -*-
"""O cadastro do cliente cruza com os POIs pelo endereço normalizado.

A CHAVE É O NOME DA VIA, SEM O TIPO — e isso não é preferência, é o que as duas
fontes têm em comum.

O cadastro não traz o tipo do logradouro na origem: o dado bruto é `INDIO SEPE`,
`HENRIQUE DIAS`, `DAS ANDORINHAS`, e não há coluna de tipo em nenhuma das 84.
Os POIs vêm com ele: `RUA DA BARCA`, `RUA TOBIAS BARRETO`. A normalização dos
dois lados é fiel à fonte, então ela não aproxima o que a fonte separou.

MEDIDO em Canoas, 28/08/2026, casando logradouro + número:

    com o tipo como veio ......    297 POIs
    com o tipo removido ....... 17.712 POIs

A HIERARQUIA é a que o dono do produto declarou — *"o endereço normalizado é o
máximo de confiança"*:

    3  logradouro normalizado + número      sem teto de distância
    2  CEP + número                         até 250 m
    1  só geografia                         8 m — a testada de um lote

Efeito da mudança, medido na mesma cidade: 12.040 → **14.959** ligações com POI,
sendo 11.532 pelo logradouro normalizado — e o raio só-geográfico apertado de
12 para 8 m, a testada média de um lote.
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cadastro_cliente as cc  # noqa: E402


# ── a chave ───────────────────────────────────────────────────────────────

def test_o_tipo_do_logradouro_sai_dos_dois_lados():
    """`RUA DA BARCA` (POI) e `DA BARCA` (cadastro) são a mesma via."""
    for com_tipo, sem_tipo in (
            ("RUA DA BARCA", "DA BARCA"),
            ("AVENIDA GUILHERME SCHELL", "GUILHERME SCHELL"),
            ("R. PAES LEME", "PAES LEME"),
            ("TRAVESSA SAO JOSE", "SAO JOSE"),
            ("PRACA DA MATRIZ", "DA MATRIZ"),
            ("ESTRADA DO CONDE", "DO CONDE"),
    ):
        assert cc.via_sem_tipo(com_tipo) == cc.via_sem_tipo(sem_tipo), \
            f"{com_tipo!r} deixou de casar com {sem_tipo!r}"


def test_a_via_sem_tipo_nao_come_o_nome():
    """Tirar o tipo não pode virar tirar palavra. `RUA RUA DAS FLORES` é
    patológico, mas `AVENIDA BRASIL` não pode virar vazio."""
    assert cc.via_sem_tipo("AVENIDA BRASIL") == "BRASIL"
    assert cc.via_sem_tipo("INDIO SEPE") == "INDIO SEPE"
    assert cc.via_sem_tipo("BRASIL") == "BRASIL"


def test_acento_e_pontuacao_nao_separam():
    """`Av. Getúlio Vargas` e `GETULIO VARGAS` são a mesma avenida."""
    assert cc.via_sem_tipo("Av. Getúlio Vargas") == cc.via_sem_tipo("GETULIO VARGAS")
    assert cc.via_sem_tipo("Rua Protásio Alves") == cc.via_sem_tipo("PROTASIO ALVES")


# ── a hierarquia ──────────────────────────────────────────────────────────

def _codigo():
    import io
    s = io.open(os.path.join(RAIZ, "cadastro_cliente.py"), encoding="utf-8").read()
    return "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))


def test_o_logradouro_normalizado_e_a_forca_maxima():
    """Decisão do dono do produto: *"o endereço normalizado é o máximo de
    confiança"*. Se ele deixar de ser a força mais alta, o CEP volta a mandar."""
    c = _codigo()
    i = c.index("prop.append((3,")
    j = c.index("prop.append((2,")
    k = c.index("prop.append((1,")
    assert i < j < k, "a ordem das forças mudou — o normalizado saiu do topo"


def test_a_forca_maxima_nao_tem_teto_de_distancia():
    """Se as duas fontes dizem a mesma via e a mesma porta, quem erra é a
    coordenada — a mesma razão pela qual a fusão une "mesmo nome, mesma rua e
    mesmo número" a qualquer distância. Um raio aqui deixaria o dado fraco vetar
    o forte."""
    c = _codigo()
    i = c.index("por_via.get((via, numc)")
    bloco = c[i:i + 260]
    assert "prop.append((3," in bloco, "o bloco da força 3 mudou de forma"
    assert " <= " not in bloco.split("prop.append((3,")[0], \
        "voltou a existir teto de distância no casamento por logradouro"


def test_o_poi_fundido_fica_fora_dos_candidatos():
    """Fundido não é um ponto: foi absorvido, e o que estava nele passou para o
    sobrevivente. MEDIDO antes deste filtro: 255 ligações do cadastro apontando
    para POI fundido, porque o cruzamento rodava a cada tanto e a fusão seguia
    unindo pontos entre uma passada e outra."""
    c = _codigo()
    i = c.index("FROM pois p")
    assert "p.fundido_em IS NULL" in c[i:i + 700], \
        "o cruzamento voltou a considerar POI fundido como candidato"


# ── é etapa do processo ───────────────────────────────────────────────────

def test_o_cruzamento_do_cadastro_e_etapa_da_mineracao():
    """O DEFEITO QUE ISTO CONSERTA. `cadastro_cliente.cruzar()` existia e já
    tinha rodado uma vez, à mão — 12.040 ligações com POI, uma foto do banco que
    envelhecia a cada mineração. Fora do `minerar_tudo`, o cruzamento não
    acompanha nada do que a rodada produz."""
    import io
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    assert '_tolerante_i9(["cadastro_cliente.py", "--cruzar"' in s, \
        "o cruzamento do cadastro saiu da mineração"


def test_o_cadastro_cruza_depois_da_fusao():
    """Antes do passo 8 ele casaria com duplicatas que o 8 vai unir logo em
    seguida, e o vínculo apontaria para um ponto que deixa de existir."""
    import io
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    assert s.index('"cruzar_fontes.py"') < s.index('"cadastro_cliente.py", "--cruzar"'), \
        "o cadastro passou a cruzar antes da fusão"


# O TESTE `test_o_arquivo_e_sincronizado_para_o_i9` SAIU DAQUI.
#
# Ele conferia se a etapa estava na lista de arquivos sincronizados para o
# i9, e guardava um defeito real: etapa fora da lista rodava a versao VELHA
# la, e tres estiveram nessa situacao sem ninguem saber.
#
# Em 30/08/2026 a sincronia por SSH acabou — o sistema passou a RODAR no
# servidor. Sem duas copias nao ha lista, e o defeito que ele guardava
# deixou de ser possivel. Removido, e nao adaptado: teste que nao pode
# falhar nao protege nada.

def test_o_total_de_etapas_nao_e_chumbado_no_texto():
    """Quando a etapa 9 entrou, todo o log continuou dizendo "de 8" — inclusive
    o cabeçalho do próprio passo 9."""
    c = _codigo() and None
    import io
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert "TOTAL_ETAPAS" in codigo, "o total de etapas voltou a ser texto fixo"
    assert "{n}/8" not in codigo, "o cabeçalho voltou a dizer 8 etapas"


# ── contra o banco ────────────────────────────────────────────────────────

def test_nenhuma_ligacao_aponta_para_poi_fundido():
    """O invariante do lado do cadastro, irmão do que já vale para o vínculo."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        cur.execute("""select count(*) from cadastro_cliente c
                         join pois p on p.id = c.poi_id
                        where p.fundido_em is not null""")
        n = cur.fetchone()[0]
    finally:
        con.close()
    assert n == 0, f"{n} ligações apontando para POI que foi absorvido"


def test_o_raio_so_geografico_e_a_testada_de_um_lote():
    """8 m, e o número tem significado — regra do dono do produto, 28/08/2026.

    Dois pontos a menos que uma frente de lote de distância estão no MESMO lote;
    a partir dela, já é o vizinho. Os 12 m anteriores não vinham de medida
    nenhuma: eram um "bem menos que 35" escolhido a olho.

    EFEITO MEDIDO em Canoas ao apertar de 12 para 8 m: os casamentos só por
    geografia caem de 3.886 para 2.517, e as ligações com POI de 16.328 para
    14.959. O que sai é justamente o mais fraco — sem endereço em comum e a mais
    de uma testada de distância.
    """
    assert cc.RAIO_SO_GEO_M == 8.0, (
        f"o raio só-geográfico virou {cc.RAIO_SO_GEO_M} — ele é a testada média "
        f"de um lote, não um número solto")
