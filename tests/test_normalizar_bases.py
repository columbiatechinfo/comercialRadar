# -*- coding: utf-8 -*-
"""A base FIXA se normaliza por município, quando muda. O POI, só na área.

Regra do dono do produto, 26/08/2026:

    "a normalização roda sempre que as bases grandes forem atualizadas, mas em
     se tratando de POIs roda apenas na área selecionada, que aí sim, com as
     bases comparativas já normalizadas, surte efeito e agrupa mais"

E o número prova. A skill aprende `tokenA ≡ tokenB` por prova — mesmo número,
30 m, support de dois imóveis distintos. Com um pedaço, ela quase não tem o que
provar; com o município, tem:

    Bento Gonçalves, por área:       84 marcações,     4 ALTA
    Canoas, município inteiro:  308.881 marcações, 5.997 ALTA

O léxico é persistente por município, então toda área minerada depois herda o
aprendizado de graça.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402


def _fonte(nome):
    return io.open(os.path.join(RAIZ, nome), encoding="utf-8").read()


def test_a_base_fixa_roda_sem_area():
    """É o ÚNICO lugar do sistema onde rodar o município inteiro é certo."""
    s = _fonte("normalizar_bases.py")
    i = s.index("cmd = [PYTHON")
    cmd = s[i:i + 200]
    assert "--municipio" in cmd and "--area" not in cmd, \
        "a normalização de base fixa voltou a ser recortada por área"


def test_cobertura_conta_e_nao_so_a_data():
    """A etapa 7 normaliza por ÁREA e grava linhas de CNEFE com o `scope_id` do
    município. Olhando só a data, Bento Gonçalves parecia "em dia" com 67 de
    64.356 linhas (0%) e foi pulado, enquanto Canoas estava em 99%.

    A pergunta certa não é "quando rodou" e sim "rodou INTEIRO"."""
    s = _fonte("normalizar_bases.py")
    i = s.index("def precisa(")
    corpo = s[i:i + 2600]
    assert "_linhas_cnefe" in corpo, "a cobertura deixou de ser critério"
    assert "0.8" in corpo, "o piso de cobertura sumiu"


def test_so_municipio_com_uso_real():
    """320 municípios têm POI, mas 17 concentram 98% dos 82.977. Preparar os
    outros 303 seria horas de CNEFE para 2% do dado — e eles não ficam de fora
    para sempre: quando uma área ali for minerada, a etapa 7 os atende."""
    s = _fonte("normalizar_bases.py")
    assert "min_pois" in s and "having count(*) >= %s" in s


def test_zona_utm_unica_por_execucao():
    """A skill ABORTA quando a base cruza zonas UTM, e está certa: ela mede 30 m
    em UTM, e metro de zonas diferentes não se compara.

    Dois casos apareceram em 26/08, e o conserto é diferente em cada um:

      UM POI de outro estado ("Serra", em -20,-40, a 1.400 km de Canoas) derrubou
      a normalização de 320.250 registros. Ele não pertence: fica fora da caixa.

      SANTA MARIA atravessa a divisa 21S/22S de verdade — 905 endereços a oeste
      contra 148.578 a leste. Esses são legítimos, então normaliza-se a zona
      DOMINANTE e diz-se quantos ficaram de fora.
    """
    s = _fonte("ajuste_logradouro.py")
    assert "def zona_dominante(" in s, "o tratamento de divisa de zona sumiu"
    assert "def faixa_do_municipio(" in s, "o filtro de fora-do-município sumiu"
    assert "ficam de fora" in s, "o que ficou de fora deixou de ser dito"


def test_o_que_fica_de_fora_nao_e_apagado():
    """Coordenada errada é dado a corrigir, não a esconder. Quem fica de fora
    da exportação continua no banco."""
    s = _fonte("ajuste_logradouro.py")
    assert "delete" not in s.lower(), "a exportação passou a apagar registro"


def test_a_mineracao_normaliza_a_base_antes_da_area():
    """SEM ISTO A REGRA VIRAVA UM COMANDO QUE ALGUÉM PRECISAVA LEMBRAR.

    O `normalizar_bases` existia solto, e eu o rodei na mão nos 16 municípios.
    Numa cidade nova o processo faria o que já fez em Bento Gonçalves:

        ajuste_logradouro --area  →  normaliza um pedaço do CNEFE
        léxico do município vazio →  84 marcações, 4 ALTA
        cruzamento                →  sem a chave de junção que ele espera

    Com a base feita antes, o mesmo município deu 63.148 marcações; Gravataí,
    partindo do zero, deu 134.880 com 2.583 ALTA.
    """
    s = _fonte("minerar_tudo.py")
    i = s.index("normalizar_bases.py")
    j = s.index("ajuste_logradouro.py", 0)
    assert i < j, "a base fixa passou a ser normalizada DEPOIS da área"


def test_a_base_fixa_nao_derruba_a_rodada():
    """Cidade sem CNEFE, ou skill abortando, não pode custar a mineração —
    perde-se qualidade de agrupamento, não a rodada."""
    s = _fonte("minerar_tudo.py")
    i = s.index("normalizar_bases.py")
    assert "_tolerante" in s[max(0, i - 200):i], \
        "a normalização da base virou etapa que derruba a rodada"


def test_a_segunda_execucao_nao_refaz():
    """`normalizar_bases` compara carga da base com cobertura normalizada. Sem
    isso, cada mineração no mesmo município repetiria o trabalho inteiro."""
    s = _fonte("normalizar_bases.py")
    assert "nada a fazer" in s, "o curto-circuito de 'já está pronto' sumiu"


def test_so_o_poi_e_recortado_pela_area():
    """A REGRA, e ela é o coração do desenho:

        "normaliza a base toda da cidade; cada nova extração de partes da
         cidade já tem com quem comparar"

    A primeira versão recortava as QUATRO fontes juntas pela área — e isso
    destruía o sentido da comparação: de que serve normalizar o POI da área
    contra um pedaço do CNEFE do mesmo tamanho?

    Medido em Bento Gonçalves com área desenhada: 91 POIs contra 63.033 linhas
    de CNEFE. Antes, o CNEFE vinha recortado junto e a comparação não tinha com
    quem acontecer.
    """
    s = _fonte("ajuste_logradouro.py")
    i = s.index("c_pois, par_pois =")
    corpo = s[i:i + 500]
    assert 'c_cad = c_ifd = ""' in corpo, \
        "cadastro e iFood voltaram a ser recortados pela área"
    j = s.index("c_cne, par_cne")
    assert 'c_cne, par_cne = "", {}' in s[j:j + 80], \
        "o CNEFE voltou a ser recortado — ele é a AUTORIDADE e vai inteiro"
    # E o POI evoluiu de "recortado por área" para "ainda não normalizado":
    # é mais completo, porque o POI a 50 m fora do desenho não fica para trás.
    assert "SO_NOVOS" in s, "o modo incremental do POI sumiu"


def test_as_quatro_fontes_continuam_sendo_exportadas():
    """Recortar menos não pode virar exportar menos: as quatro entram na skill,
    e é do cruzamento entre elas que sai a forma canônica."""
    s = _fonte("ajuste_logradouro.py")
    for fonte in ("pois", "cadastro", "ifood", "cnefe"):
        assert f'contagem["{fonte}"]' in s, f"a fonte {fonte} saiu da exportação"
