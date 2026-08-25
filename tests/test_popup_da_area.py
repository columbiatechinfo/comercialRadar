# -*- coding: utf-8 -*-
"""Clicar no polígono responde "o que tem aqui dentro?" e oferece apagar.

Pedido do dono do produto, 25/08/2026, e ele nasceu de uma confusão real: o
cabeçalho dizia "42 POIs" e o cartão da mineração dizia "165" — números da MESMA
área, nenhum dos dois errado. Os 42 são POIs do banco dentro do desenho; os 165
são ícones lidos por OCR num tile 14× maior que o desenho. Faltava um lugar onde
a pergunta tivesse resposta direta.
"""
import io
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(RAIZ, "frontend", "app.js")
CSS = os.path.join(RAIZ, "frontend", "style.css")


def _app():
    return io.open(APP, encoding="utf-8").read()


def test_o_javascript_compila():
    """Um erro de sintaxe aqui derruba a tela inteira, não só o popup."""
    r = subprocess.run(["node", "--check", APP], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:800]


def test_o_botao_de_apagar_e_pego_por_delegacao():
    """A regressão que custou três tentativas para achar.

    Ligar `b.onclick` logo depois de `openPopup()` parecia óbvio e falhava em
    SILÊNCIO: como o `bindPopup` recebe `options`, o Leaflet constrói uma Popup
    nova a cada clique, e a partir da segunda abertura o container ainda não
    existia quando `openPopup` retornava. O `querySelector` devolvia nulo, a
    guarda `if (!b) return` engolia, e o popup abria com os números certos e um
    "Apagar" que não apagava.

    Delegação resolve o clique quando ele acontece — aí o botão existe por
    definição. Se alguém voltar a guardar a referência, este teste acusa.
    """
    js = _app()
    assert 'addEventListener("click"' in js and ".area-pop-del" in js, \
        "o botão de apagar deixou de ser pego por delegação"
    assert 'querySelector(".area-pop-del")' not in js, \
        "voltou a guardar referência ao botão — falha em silêncio na 2ª abertura"


def test_o_conteudo_e_recalculado_a_cada_abertura():
    """`allPois` muda com a mineração em tempo real. Um HTML preso no `bindPopup`
    mostraria o número de quando o polígono foi desenhado — que é exatamente o
    engano que este popup existe para desfazer."""
    js = _app()
    assert "setPopupContent(htmlResumoArea())" in js, \
        "o popup voltou a ficar com conteúdo fixo do bind"


def test_apagar_a_area_nao_apaga_poi():
    """A área é FOCO de tela, não filtro de banco — regra de 04/08/2026. O texto
    da confirmação promete isso ao operador; o código precisa cumprir."""
    js = _app()
    i = js.index("Apagar a área desenhada?")
    trecho = js[i:i + 700]
    assert "Nenhum POI é apagado" in trecho, "a promessa saiu da confirmação"
    assert "setAreaLayer(null)" in trecho and "salvarArea([])" in trecho
    for proibido in ("/api/limpar-fora", "allPois.clear", "allPois.delete"):
        assert proibido not in trecho, f"o apagar da área encostou em {proibido}"


def test_a_quebra_e_por_fonte_e_nao_pelos_chips_de_origem():
    """Os chips agrupam por como o POI foi CONFIRMADO, e a base estadual inteira
    cai no balde "Outros" — foi isso que escondeu que os 42 POIs vinham do
    Overture/OSM/Foursquare. A tabela precisa quebrar por `fonte`."""
    js = _app()
    assert "FONTE_ROTULO" in js and "p.fonte" in js
    i = js.index("function resumoArea()")
    assert "origemDe" not in js[i:i + 900], \
        "o resumo voltou a agrupar pelos chips de origem"


def test_os_rotulos_cabem_em_uma_linha():
    """Medido no navegador: com o rótulo por extenso a célula quebrava em duas
    linhas e a coluna de números perdia o alinhamento — a única coisa que essa
    tabela precisa fazer bem. O detalhe foi para o `title`."""
    js = _app()
    i = js.index("const FONTE_ROTULO")
    bloco = js[i:js.index("};", i)]
    rotulos = re.findall(r'\["([^"]+)",', bloco)
    assert rotulos, "FONTE_ROTULO deixou de ser [rótulo, detalhe]"
    longos = [r for r in rotulos if len(r) > 18]
    assert not longos, f"rótulo longo demais para a célula: {longos}"
    assert 'title="${det}"' in js, "o detalhe deixou de ir para o title"


def test_a_area_em_hectares_sai_em_portugues():
    """`3.5 ha` num painel em português é defeito de acabamento, e apareceu."""
    js = _app()
    assert 'toLocaleString("pt-BR"' in js[js.index("area-pop-sub"):][:400], \
        "a área voltou a sair com ponto decimal"


def test_o_popup_tem_largura_minima():
    css = io.open(CSS, encoding="utf-8").read()
    i = css.index(".area-pop {")
    assert "min-width" in css[i:i + 200], \
        "sem largura mínima a tabela volta a quebrar em duas linhas"


def test_a_area_desenhada_fica_acima_da_malha():
    """O defeito: clicar no próprio desenho selecionava o MUNICÍPIO.

    A área morava no `overlayPane` padrão (z-index 400) e a malha do IBGE está
    em 410 — o contorno do município era desenhado por cima do polígono que o
    operador acabou de traçar, comia o clique e trocava o recorte do mapa
    inteiro. O tooltip pegajoso da malha também vinha por cima.

    Verificado no navegador com `elementFromPoint`: na borda do desenho o topo é
    `area-poly` na `paneArea`; fora dele a malha volta ao topo e continua
    recebendo clique, que é como se escolhe município.
    """
    js = _app()
    assert 'createPane("paneArea")' in js, "a área perdeu a pane própria"
    i = js.index('createPane("paneArea")')
    z_area = int(re.search(r"zIndex = (\d+)", js[i:i + 120]).group(1))
    j = js.index('createPane("paneMalha")')
    z_malha = int(re.search(r"zIndex = (\d+)", js[j:j + 120]).group(1))
    assert z_area > z_malha, f"a malha ({z_malha}) voltou a cobrir a área ({z_area})"
    assert 'pane: "paneArea"' in js[js.index("const AREA_STYLE"):][:260], \
        "o polígono desenhado deixou de usar a pane própria"


def test_marcador_e_via_continuam_ganhando_da_area():
    """Clicar num POI dentro da área tem de abrir o POI, não o resumo da área.
    A correção acima não pode ter passado a área na frente de todo mundo."""
    js = _app()
    z = {}
    for nome in ("paneArea", "paneQuadras", "paneMarcadores", "paneFaces"):
        i = js.index(f'createPane("{nome}")')
        z[nome] = int(re.search(r"zIndex = (\d+)", js[i:i + 120]).group(1))
    for acima in ("paneQuadras", "paneMarcadores", "paneFaces"):
        assert z[acima] > z["paneArea"], \
            f"{acima} ({z[acima]}) caiu abaixo da área ({z['paneArea']})"
