# -*- coding: utf-8 -*-
"""A coordenada vira endereço com número — ou o ponto não entra.

REGRA DO DONO DO PRODUTO, 27/08/2026

POI com nome e coordenada mas sem endereço é válido para comparação, "mas o
endereço deve ser gerado a partir da coordenada". E o critério é duro: só segue
com o ponto quem obtiver endereço COM NÚMERO; o resto é apagado. O endereço
gerado fica marcado no POI e passa pela normalização como qualquer outro.

O PHOTON FOI TESTADO E REPROVADO, e o número é a razão

A cascata proposta era OSM primeiro, Maps depois. Medido sobre 200 POIs reais
de Canoas, contra o Photon do próprio i9:

    Photon (layer=house)  99% acham porta ... e 73% delas a MAIS DE 100 m.
                          Só 2% dentro de 20 m. Ele devolve a porta mais
                          próxima que CONHECE, e o OSM quase não tem numeração
                          predial no RS — "Eixo Sul Distribuidora" recebia
                          "Rua Senador Salgado Filho 250", a 834 metros.

    CNEFE (IBGE)          100% acham, 85% dentro de 20 m, 96% dentro de 50 m,
                          a 0,18 ms por ponto.

É o tipo de número que engana: 99% parece sucesso e seria corrupção da base,
porque endereço errado não fica inerte — ele casa com o vizinho errado no
cruzamento. Por isso a cascata virou CNEFE, depois Maps, sem OSM.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import endereco_reverso as er  # noqa: E402


def test_o_raio_nao_deixa_pegar_a_porta_do_vizinho():
    """50 m é onde a medição para de ser confiável: dentro dele, 96% dos pontos
    têm porta do CNEFE; além, a mais próxima começa a ser a do outro
    quarteirão. Dobrar o raio dobraria a cobertura com endereço de outro
    estabelecimento — cobertura que mente é pior que buraco."""
    assert er.RAIO_MAX_M <= 60, (
        f"raio de {er.RAIO_MAX_M} m — acima disso a porta devolvida passa a ser "
        f"de outro imóvel, e endereço errado casa com o vizinho errado")
    assert er.RAIO_MAX_M >= 20, "raio apertado demais joga fora resposta boa"


def test_a_cascata_nao_usa_o_osm():
    """Photon e Nominatim ficaram DE FORA por medição, não por esquecimento.
    Se voltarem, voltam com o defeito: 73% das portas a mais de 100 m."""
    s = io.open(os.path.join(RAIZ, "endereco_reverso.py"), encoding="utf-8").read()
    i = s.index("def endereco_de(")
    corpo = s[i:i + 1200]
    for morto in ("photon", "nominatim"):
        assert morto not in corpo.lower(), \
            f"{morto} voltou para a cascata — ver a medição no cabeçalho do módulo"
    assert "por_cnefe" in corpo, "o CNEFE saiu da cascata"
    assert "por_maps" in corpo, "o Maps saiu do resíduo"


def test_a_grade_do_municipio_e_carregada_uma_vez_so():
    """177 mil linhas e 0,4 s por município. Recarregar a cada ponto faria a
    etapa inteira custar horas — a medição de 2 ms por ponto depende disto."""
    s = io.open(os.path.join(RAIZ, "endereco_reverso.py"), encoding="utf-8").read()
    assert "_GRADES" in s and "if cod_ibge in _GRADES" in s, \
        "o cache por município sumiu — cada ponto voltaria a recarregar o CNEFE"


def test_sem_numero_nao_serve():
    """O Maps só é aceito quando devolve algo com dígito. Endereço sem número
    não cumpre a regra, e deixá-lo passar seria aceitar pela porta dos fundos
    o que o trigger recusa na porta da frente."""
    s = io.open(os.path.join(RAIZ, "endereco_reverso.py"), encoding="utf-8").read()
    i = s.index("def por_maps(")
    corpo = s[i:s.index("def endereco_de(")]
    assert "isdigit" in corpo, "o Maps voltou a aceitar endereço sem número"


def test_none_significa_o_ponto_nao_entra():
    """A função devolve None quando não conseguiu, e quem chama TEM de apagar o
    ponto. Está no docstring porque é a diferença entre cumprir a regra e
    reabrir o buraco que o trigger fecha no banco."""
    assert er.endereco_de.__doc__ and "NÃO ENTRA" in er.endereco_de.__doc__


def test_o_endereco_gerado_e_marcado():
    """Endereço que veio da coordenada não é igual a endereço que a fonte
    declarou: um foi observado, o outro foi inferido. Sem a marca, ninguém
    consegue mais separar os dois depois."""
    import base_comum as bc
    con = bc.conectar()
    try:
        cur = con.cursor()
        cur.execute("""select data_type from information_schema.columns
                        where table_name='pois' and column_name='endereco_gerado_por'""")
        r = cur.fetchone()
        assert r, "a coluna endereco_gerado_por sumiu de pois"
    finally:
        con.close()


def test_o_cnefe_responde_de_verdade():
    """Teste contra o banco, sobre uma AMOSTRA — e não sobre um ponto escolhido.

    A primeira versão cravava uma coordenada e falhou: a porta mais próxima
    dela estava a 55 m, cinco metros fora do raio. O módulo estava certo; o
    teste é que media o lugar errado. E há pontos que legitimamente não têm
    porta nenhuma — o primeiro POI de Canoas é "Ponte férrea sobre o Rio".

    Medir a TAXA sobre uma amostra pega o que importa (o CNEFE do município
    sumiu? a grade parou de casar?) sem quebrar por causa de um ponto atípico.
    Medido em 27/08/2026: 285 de 300 POIs de Canoas, 95%.
    """
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        cur.execute("""select coalesce(maps_lat,lat_origem), coalesce(maps_lng,lng_origem)
          from pois where cidade='Canoas' and coalesce(maps_lat,lat_origem) is not null
          order by id limit 100""")
        pontos = cur.fetchall()
    finally:
        con.close()
    if not pontos:
        import pytest
        pytest.skip("sem POIs de Canoas para amostrar")

    achados = [er.por_cnefe(la, lo, "4304606") for la, lo in pontos]
    ok = [a for a in achados if a]
    taxa = len(ok) / len(pontos)
    assert taxa >= 0.80, (
        f"o CNEFE resolveu só {taxa:.0%} da amostra — era 95% em 27/08/2026. "
        f"O município saiu do banco de referência, ou a grade parou de casar")
    for a in ok:
        assert a["numero"], "veio sem número"
        assert a["distancia_m"] <= er.RAIO_MAX_M
        assert a["fonte"] == "cnefe"
