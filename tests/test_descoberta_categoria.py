# -*- coding: utf-8 -*-
"""Descoberta por categoria no Maps — a substituta da descoberta do iFood.

A descoberta pelo iFood morreu em 26/08/2026: Turnstile interativo, API 404/403,
app blindado contra emulador, proxies estrangeiros. Toda rota FECHOU.

O Google Maps tem os botões de categoria, e cada busca devolve a lista daquele
ramo com nome (escrito pelo Google, sem OCR) e coordenada real do `!3d!4d`.
Medido em Canoas: 40 restaurantes por busca; em Bento Gonçalves, 3 POIs novos
que nenhuma outra fonte tinha.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import descobrir_maps as dm  # noqa: E402


def test_a_coordenada_vem_do_href_e_nao_do_centro():
    """O `!3d!4d` é o ponto REAL do lugar. Pegar o centro do mapa poria todos
    os resultados na mesma coordenada."""
    href = "/maps/place/Coco+Bambu/data=!3d-29.91622!4d-51.16444!16s"
    m = dm.UUID_COORD.search(href)
    assert m and float(m.group(1)) == -29.91622 and float(m.group(2)) == -51.16444


def test_as_categorias_cobrem_alem_de_comida():
    """O iFood deixou de ser só comida, e a base de saneamento quer todo
    comércio. A lista precisa ir além de restaurante."""
    cats = " ".join(dm.CATEGORIAS)
    for esperado in ("farmácias", "mercados", "pet shop", "óticas", "academias",
                     "material de construção", "hotéis"):
        assert esperado in cats, f"categoria ausente: {esperado}"


def test_so_o_diferente_vira_poi():
    """`_novos` é o que impede a descoberta de duplicar o que outra fonte já
    trouxe — a regra "rodar de novo ACRESCENTA, não repete"."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "def _novos(" in s
    i = s.index("def _novos(")
    corpo = s[i:i + 900]
    assert "abs(maps_lat" in corpo and "translate" in corpo, \
        "a dedup por nome+coordenada saiu do filtro de novos"


def test_o_recorte_pela_area_e_pelo_poligono():
    """A busca do Maps traz o entorno (15z); só entra o que está DENTRO do
    desenho, não o que está na caixa."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "ponto_no_poligono" in s, "voltou a aceitar tudo que o Maps devolveu"


def test_nao_depende_do_ifood():
    """Esta etapa existe justamente porque o iFood fechou. Ela não pode importar
    nada do caminho morto."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "extrair_ifood" not in s and "marketplace.ifood" not in s


def test_a_cidade_vem_da_area_e_nao_do_codigo():
    """CRAVAR A CIDADE CUSTOU CINCO POIs ERRADOS, 26/08/2026.

    A primeira versão gravava "Canoas" para todo mundo. A descoberta rodou em
    Bento Gonçalves e os cinco POIs novos entraram como sendo de Canoas — um
    deles chamado, literalmente, "Loja Todeschini BENTO GONÇALVES".

    Quem acusou foi o `test_coerencia_local` do próprio projeto, no mesmo dia:
    5 POIs a 81 km da cidade que declaram. Cidade errada não fica quieta — ela
    estraga o recorte de toda etapa seguinte, porque metade das consultas do
    sistema casa por nome de cidade.
    """
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    # O que importa é o que se GRAVA, não o que se menciona: "Canoas" aparece
    # no comentário que explica este próprio erro.
    i = s.index("dados = [(")
    insercao = s[i:i + 400]
    assert '"Canoas"' not in insercao, "a cidade voltou a ser cravada no insert"
    assert "cidade, uf" in insercao, "a cidade deixou de vir do polígono"
    assert "municipio_da_area" in s


# ============================================================================
# 27/08/2026 — "0 NO MAPS" NAO PODIA SIGNIFICAR DUAS COISAS
#
# Uma run inteira reportou `0 no Maps` nas 46 categorias, em pleno bairro
# comercial de Canoas. Lido de fora, isso e "a area nao tem restaurante, nem
# farmacia, nem padaria". Nao era: NENHUMA busca tinha acontecido.
#
# A causa foi o perfil de navegador reaproveitado entre runs. Ele apodreceu e
# toda navegacao daquele worker passou a dar timeout. Medido no conserto, mesma
# URL, mesmo proxy, mesmo minuto:
#
#     perfil cr_descobre_0 (velho)  ->  goto TIMEOUT, feed 0, links 0
#     perfil novo em branco         ->  feed 1, 20 links
#
# Headless ou headful, com ou sem tz_hint_lng: so o perfil mudava o resultado.
# Depois do conserto, mesma area: 20 / 20 / 35 / 20 onde antes eram 0 / 0 / 0.
#
# O DEFEITO nao era o perfil -- perfil apodrece, acontece. Era `buscar_categoria`
# devolver [] calada em dois pontos, fazendo falha de infraestrutura ter a mesma
# aparencia de resultado legitimo. Erro disfarçado de dado custa uma run inteira
# antes de alguem desconfiar.
# ============================================================================
import asyncio

# --------------------------------------------------------------------------
# Página falsa: exercita a função de verdade, sem navegador e sem rede.
# --------------------------------------------------------------------------
class _Loc:
    def __init__(self, n):
        self._n = n

    async def count(self):
        return self._n


class _Pagina:
    """Mínimo que `buscar_categoria` toca. `elementos` diz quantos de cada
    seletor existem; `goto_erro` faz a navegação explodir; `titulo` é o
    título da página (vazio = aba morta)."""

    def __init__(self, elementos=None, goto_erro=None, titulo="restaurantes"):
        self.elementos = elementos or {}
        self.goto_erro = goto_erro
        self._titulo = titulo
        self.url = ""

    async def goto(self, url, **kw):
        if self.goto_erro:
            raise self.goto_erro
        self.url = url

    async def title(self):
        return self._titulo

    def locator(self, sel):
        return _Loc(self.elementos.get(sel, 0))

    async def evaluate(self, *a, **k):
        return 0


def _correr(page, termo="restaurantes"):
    # os sleeps da função são reais; encurta-os para o teste não custar 5 s
    original = asyncio.sleep

    async def rapido(_s, *a, **k):
        return await original(0)

    asyncio.sleep = rapido
    try:
        return asyncio.get_event_loop().run_until_complete(
            dm.buscar_categoria(page, termo, -29.88, -51.16))
    finally:
        asyncio.sleep = original


def test_navegacao_que_falha_diz_que_falhou():
    """Era o caso da run perdida: timeout virava "0 no Maps"."""
    achados, motivo = _correr(_Pagina(goto_erro=TimeoutError("estourou")))
    assert achados == []
    assert motivo, "navegação falhou e a função não disse nada"
    assert "navegação falhou" in motivo and "TimeoutError" in motivo


def test_aba_morta_e_reconhecida():
    """Página sem título e sem feed é sessão morta, não bairro sem comércio."""
    achados, motivo = _correr(_Pagina(elementos={}, titulo=""))
    assert achados == []
    assert "branco" in motivo or "morta" in motivo


def test_captcha_e_consentimento_tem_nome_proprio():
    """Se o Google barrar, o log tem de dizer QUAL barreira — a conduta é
    diferente: consentimento se dispensa, CAPTCHA obriga a trocar de IP."""
    p = _Pagina(elementos={'iframe[src*="recaptcha"]': 1}, titulo="Google Maps")
    _, motivo = _correr(p)
    assert motivo == "CAPTCHA"

    p = _Pagina(elementos={'form[action*="consent"]': 1}, titulo="Antes de continuar")
    _, motivo = _correr(p)
    assert motivo == "muro de consentimento"


def test_area_realmente_vazia_nao_vira_erro():
    """O outro lado da moeda: página SÃ, feed presente, zero card. Aí "0" é
    resposta legítima e não pode virar alarme — senão o alarme perde o valor."""
    p = _Pagina(elementos={'div[role="feed"]': 1,
                           'div[role="feed"] a[href*="/maps/place/"]': 0},
                titulo="restaurantes - Google Maps")
    achados, motivo = _correr(p)
    assert achados == []
    assert motivo == "", f"área vazia foi reportada como falha: {motivo!r}"


def test_o_worker_descarta_o_perfil_podre():
    """A AUTOCURA. Perfil apodrece e vai continuar apodrecendo; o que não pode
    é uma sessão ruim custar as 46 categorias.

    Na primeira falha de NAVEGAÇÃO o worker joga o perfil fora, refaz a sessão
    e tenta a mesma categoria de novo. Uma vez só: se falhar outra vez o
    problema não é o perfil, e insistir só gastaria proxy.

    Teste estrutural — o caminho de cura exige navegador e rede de verdade, e
    o que se garante aqui é que ele não seja removido por descuido.
    """
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    i = s.index("async def _worker(")
    corpo = s[i:i + 3000]
    assert "shutil.rmtree(perfil" in corpo, "parou de descartar o perfil podre"
    assert "curou" in corpo, "a trava de tentar-uma-vez-só sumiu"
    assert "_abrir()" in corpo, "a sessão deixou de ser refeita"


def test_o_resumo_diz_o_que_nao_foi_buscado():
    """Silêncio no fim foi o que fez a run parecer "varreu 46 categorias"
    quando não varreu nenhuma. O total só significa alguma coisa acompanhado
    do que ficou de fora."""
    s = io.open(os.path.join(RAIZ, "descobrir_maps.py"), encoding="utf-8").read()
    assert "falhas: list = []" in s, "a lista de categorias não buscadas sumiu"
    assert "NÃO foram" in s and "não cobre a área toda" in s, \
        "o resumo voltou a omitir o que falhou"
