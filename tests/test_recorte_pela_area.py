# -*- coding: utf-8 -*-
"""O processo caro roda no que está DENTRO do desenho, não no município.

Regra do dono do produto, 25/08/2026. A mesma doença apareceu em TRÊS lugares
diferentes, e em todos ela tinha a mesma forma: montar o trabalho sobre o
município inteiro e só depois cruzar com o polígono.

    busca no Maps        88 ícones no tile, 80 fora do desenho, buscados assim
                         mesmo até 25/08 — cada um uma sessão de navegador
    IA de endereço       7.223 endereços distintos de Cachoeirinha enviados à
                         Spark (360 lotes) para uma área com 6 POIs
    pontos do iFood      grade de 2,5 km sobre o município, 15 pontos escolhidos,
                         ZERO dentro do desenho — e havia 262 endereços do CNEFE
                         disponíveis lá dentro. A etapa 5 falhava inteira.

O município continua sendo o escopo quando o município É a área: escolher o
município no painel grava a divisa dele em `area_atual`, então o polígono diz a
verdade nos dois casos e não existe ramificação a fazer.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import area_utils as au  # noqa: E402


def _fonte(nome):
    return io.open(os.path.join(RAIZ, nome), encoding="utf-8").read()


# ─── o corte compartilhado ───────────────────────────────────────────────────

def test_o_corte_vira_caixa_e_nao_poligono():
    """A caixa vai ao banco, o polígono fica em Python.

    Mandar `ponto_no_poligono` ao SQL seria ray casting por linha e varredura
    sequencial; a caixa é comparação de quatro floats numa coluna indexada.
    """
    frag, par = au.recorte_sql([[1, 2], [3, 4], [1, 4]], "p.lat", "p.lng")
    assert "between" in frag and "p.lat" in frag and "p.lng" in frag
    # A caixa vem FOLGADA (`MARGEM_TRABALHO_M`), então cada limite passa do
    # vértice — a margem existe para o cruzamento achar o duplicado da borda.
    assert par["area_s"] < 1 and par["area_n"] > 3
    assert par["area_o"] < 2 and par["area_l"] > 4
    # sem margem, o corte volta a ser a caixa crua
    _, cru = au.recorte_sql([[1, 2], [3, 4], [1, 4]], "p.lat", "p.lng", margem_m=0)
    assert cru == {"area_s": 1, "area_n": 3, "area_o": 2, "area_l": 4}


def test_sem_poligono_o_corte_some():
    """Sem área, a consulta tem de continuar valendo para o município — e não
    virar um `where` que não casa com nada."""
    assert au.recorte_sql(None, "a", "b") == ("", {})
    assert au.recorte_sql([], "a", "b") == ("", {})


def test_o_prefixo_permite_varias_fontes_na_mesma_consulta():
    """`ajuste_logradouro` corta quatro fontes com colunas diferentes. Sem
    prefixo os parâmetros colidiriam e a última venceria em silêncio."""
    _, a = au.recorte_sql([[1, 2], [3, 4], [1, 4]], "p.lat", "p.lng")
    _, b = au.recorte_sql([[5, 6], [7, 8], [5, 8]], "c.lat", "c.lng", "cad")
    assert not (set(a) & set(b)), "os parâmetros das duas fontes colidem"


# ─── a IA só lê o que está dentro ────────────────────────────────────────────

def test_a_ia_de_endereco_recebe_a_area():
    js = _fonte("segmentar_endereco.py")
    assert '"--area"' in js, "`segmentar_endereco` perdeu o --area"
    assert "recorte_sql" in js, "a leitura voltou a varrer o município inteiro"
    # O POLÍGONO EXATO SAIU DAQUI DE PROPÓSITO, em 26/08/2026.
    #
    # Esta etapa alimenta a normalização, que alimenta o cruzamento — e o
    # cruzamento compara com margem, para achar o duplicado da borda. Cortar
    # aqui no polígono exato deixava esses vizinhos SEM endereço lido: medido,
    # 80 POIs aqui contra 307 lá, e só 22% do que o cruzamento viu tinha
    # logradouro canônico.
    assert "MARGEM_TRABALHO_M" in js, "a etapa deixou de usar a margem comum"


def test_a_leitura_traz_coordenada():
    """A consulta original selecionava só `p.endereco`. Sem coordenada não há
    como saber se o POI está no desenho — foi o que obrigou a mudá-la."""
    s = _fonte("segmentar_endereco.py")
    i = s.index("select p.endereco")
    assert "maps_lat" in s[i:i + 400], "a leitura voltou a não trazer coordenada"


def test_o_minerar_passa_a_area_para_as_duas_etapas_6():
    s = _fonte("minerar_tudo.py")
    i = s.index("segmentar_endereco.py")
    j = s.index("ajuste_logradouro.py", i)
    assert '"--area", a.area' in s[i:j + 200], \
        "a etapa 6 voltou a rodar sem saber da área"


# ─── a normalização também ───────────────────────────────────────────────────

def test_a_normalizacao_corta_as_quatro_fontes():
    s = _fonte("ajuste_logradouro.py")
    for col in ("p.maps_lat", "c.lat", "m.lat", "latitude::numeric"):
        assert col in s, f"a fonte de {col} deixou de ser recortada"


def test_o_cnefe_foi_partido_para_o_corte_caber():
    """A consulta do CNEFE termina em `order by ... limit`; colar o fragmento no
    fim seria SQL inválido. Ela é montada em duas metades de propósito."""
    s = _fonte("ajuste_logradouro.py")
    assert "SQL_CNEFE_INICIO" in s and "SQL_CNEFE_FIM" in s
    i = s.index("SQL_CNEFE_INICIO + c_cne + SQL_CNEFE_FIM")
    assert i > 0, "o corte saiu do meio da consulta do CNEFE"


def test_a_normalizacao_nao_chama_ia():
    """O cabeçalho dizia "A IA, quando é chamada, é a da Spark", e isso fazia
    parecer que este arquivo chama IA. Ele não chama: a skill é determinística e
    só aprende de par provado. Quem chama a Spark é o `segmentar_endereco`."""
    s = _fonte("ajuste_logradouro.py")
    for sinal in ("openai", "/v1/chat", "SPARK_URL"):
        assert sinal not in s, f"`ajuste_logradouro` passou a chamar IA ({sinal})"
    assert "NÃO CHAMA IA" in s, "o cabeçalho voltou a sugerir que ele chama IA"


# ─── os pontos do iFood ──────────────────────────────────────────────────────

def test_o_ifood_recorta_antes_de_agrupar():
    """A causa do "nenhum endereço do CNEFE em área desenhada".

    A grade se formava sobre o município (células de 2,5 km), escolhia um
    endereço por célula — o mais próximo do CENTRO dela — e só então cruzava com
    o polígono. Numa área de 1,5 ha nenhum centro de célula cai dentro, e os 262
    endereços disponíveis eram descartados junto.
    """
    s = _fonte("pontos_de_busca.py")
    assert "CNEFE_NA_CAIXA" in s, "o caminho da área sumiu"
    assert "_agrupar_em_celulas" in s, "a grade voltou a se formar antes do corte"
    i = s.index("if poligono:", s.index("cods = [c for c"))
    trecho = s[i:i + 900]
    assert trecho.index("ponto_no_poligono") < trecho.index("_agrupar_em_celulas"), \
        "voltou a agrupar antes de recortar — é o bug que dava zero pontos"


def test_o_caminho_do_municipio_continua_no_banco():
    """Cachoeirinha tem 69.715 endereços; Porto Alegre tem muito mais. Trazer
    tudo para o Python para agrupar seria trocar um bug por um gargalo."""
    s = _fonte("pontos_de_busca.py")
    i = s.index("else:", s.index("CNEFE_NA_CAIXA,", s.index("def pontos(")))
    assert "cur.execute(GRADE" in s[i:i + 400], \
        "o caminho do município deixou de usar a grade do banco"


def test_a_celula_escolhe_o_endereco_mais_central():
    """Mesma regra do `distinct on` do banco, para que os dois caminhos
    devolvam o mesmo tipo de ponto."""
    linhas = [
        ("RUA", "A", "1", "", "", -29.9000, -51.0800, "4303103"),
        ("RUA", "B", "2", "", "", -29.9001, -51.0801, "4303103"),
        ("RUA", "C", "3", "", "", -29.8000, -51.0800, "4303103"),
    ]
    import pontos_de_busca as pb
    saida = pb._agrupar_em_celulas(linhas, 2.5 * pb.GRAU_POR_KM)
    assert len(saida) == 2, "as células saíram erradas"
    densidades = sorted(r[2] for r in saida)
    assert densidades == [1, 2], f"a densidade da célula está errada: {densidades}"


# ─── o cruzamento também ─────────────────────────────────────────────────────

def test_o_cruzamento_recorta_pela_area():
    """A quarta etapa com a mesma doença, achada em 26/08/2026.

    MEDIDO na micro área de Cachoeirinha:

        sem recorte   11.897 POIs · 831.211 pares · 742 chamadas de IA
        com recorte       22 POIs ·      52 pares ·   0 chamadas

    Para uma área com 6 POIs.
    """
    s = _fonte("cruzar_fontes.py")
    assert '"--area"' in s, "`cruzar_fontes` perdeu o --area"
    assert "SQL_AREA" in s and "_um_lado_dentro" in s


def test_o_cruzamento_folga_a_area_em_150_metros():
    """A fusão precisa enxergar o vizinho de FORA da linha.

    O duplicado do POI que está na borda pode estar do outro lado dela; cortar
    exato o tornaria invisível, e o ponto entraria na entrega DUAS VEZES por
    causa do recorte — o oposto do que o cruzamento existe para fazer.

    150 m é a própria rede de candidatos (célula de ~110 m mais as vizinhas):
    a margem não inventa alcance, só não amputa o que o algoritmo já usa.

    A MARGEM É UMA SÓ, e vive no `area_utils`. Ela nasceu dentro do
    `cruzar_fontes` e por isso ficou só lá — o que produziu três recortes
    diferentes no processo (medido em 26/08: 80, 85 e 307 POIs nas etapas de
    segmentar, normalizar e cruzar). O cruzamento comparava 222 POIs que
    ninguém tinha normalizado.
    """
    assert abs(au.MARGEM_TRABALHO_M - 150) < 1, "a folga mudou de tamanho"
    quadrado = [[-29.9, -51.1], [-29.9, -51.09], [-29.89, -51.09], [-29.89, -51.1]]
    s, n, o, l = au.bbox_com_margem(quadrado)
    assert s < -29.9 and n > -29.89 and o < -51.1 and l > -51.09
    # a longitude encolhe com o cosseno da latitude: usar 111 km nos dois eixos
    # daria uma margem ~13% mais estreita em longitude, no RS
    assert (l - -51.09) > (n - -29.89)


def test_basta_um_lado_dentro_da_area():
    """Exigir os DOIS perderia exatamente o caso que a margem existe para pegar.
    Nenhum dos dois dentro é vizinhança de fora do pedido."""
    import cruzar_fontes as cf
    poly = [[-29.9, -51.1], [-29.9, -51.09], [-29.89, -51.09], [-29.89, -51.1]]
    dentro = {"lat": -29.895, "lng": -51.095}
    fora = {"lat": -29.88, "lng": -51.08}
    assert cf._um_lado_dentro({"a": dentro, "b": fora}, poly)
    assert cf._um_lado_dentro({"a": fora, "b": dentro}, poly)
    assert not cf._um_lado_dentro({"a": fora, "b": fora}, poly)


def test_sem_area_o_cruzamento_pega_o_municipio():
    """Escolher o município no painel grava a divisa como área, então na prática
    o polígono existe sempre — mas a função tem de continuar valendo sem ele."""
    import cruzar_fontes as cf
    import inspect
    assert inspect.signature(cf.carregar).parameters["poligono"].default is None
