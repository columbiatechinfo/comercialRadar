# -*- coding: utf-8 -*-
"""Por que nem todo POI da area chega a ser avaliado pela IA.

Tres buracos reais, os tres silenciosos — o processo dizia "capturados 12/12" e
"avaliados 8", e nada no meio explicava a diferenca:

  1. `streetview_path` era gravado mesmo quando o byte NAO chegou ao Storage.
     O POI saia das duas listas ao mesmo tempo: da captura (que filtra por path
     vazio) e da avaliacao (que faz JOIN em `streetview_imgs`). Sumia do
     processo sem aparecer em contador nenhum.
  2. Na avaliacao, alvo sem bytes voltava com `return` mudo: nem contador, nem
     log, nem anotacao.
  3. O universo da area nunca era declarado — so o numero de alvos, que ja e o
     recorte de quem TEM imagem.
"""
import os
import sys
import uuid

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402


@pytest.fixture()
def con():
    try:
        c = bc.conectar()
    except Exception as e:
        pytest.skip(f"banco indisponivel: {type(e).__name__}")
    yield c
    try:
        c.rollback()
    finally:
        c.close()


def test_capturar_nao_marca_path_sem_imagem(monkeypatch):
    """A prova do defeito 1, sem tocar no banco: quando a gravacao devolve None
    — Storage fora, objeto recusado — a captura NAO pode devolver nome de
    arquivo, senao o chamador grava `streetview_path` e prende o POI."""
    import asyncio

    import streetview_capture as SV

    class PaginaFalsa:
        url = "https://www.google.com/maps/@-29.9,-51.0,3a,75y/data=!3m1"

        async def goto(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a, **k):
            return None

        async def screenshot(self, *a, **k):
            return b"\xff\xd8fake-jpeg"

    # Os metadados sao stub: o teste e sobre o que a captura FAZ com o
    # resultado da gravacao, nao sobre a rede.
    monkeypatch.setattr(SV, "metadados_pano",
                        lambda *a, **k: {"pano_id": "X", "lat": -29.9001,
                                         "lng": -51.0001, "data": "2024-01"})
    monkeypatch.setattr(SV, "_gravar_imagem", lambda *a, **k: None)
    res = asyncio.run(SV._capturar(PaginaFalsa(), {"id": 1, "lat": -29.9, "lng": -51.0}))
    assert res is None, "captura sem imagem gravada devolveu nome de arquivo"

    monkeypatch.setattr(SV, "_gravar_imagem", lambda *a, **k: 4242)
    res = asyncio.run(SV._capturar(PaginaFalsa(), {"id": 1, "lat": -29.9, "lng": -51.0}))
    assert res == "1.jpg", "captura com imagem gravada deveria devolver o arquivo"


def test_sem_panorama_exige_confirmacao_dos_metadados(monkeypatch):
    """O navegador nao e testemunha de ausencia.

    Ele desistir tem varias causas — e uma so delas e "nao existe foto aqui".
    Como 'NA' volta a fila mas custa uma rodada inteira, so o Google confirma.
    """
    import asyncio

    import streetview_capture as SV

    class PaginaFalsa:
        url = "https://www.google.com/maps/@-29.9,-51.0,17a"   # vista aerea

        async def goto(self, *a, **k):
            return None

        async def wait_for_timeout(self, *a, **k):
            return None

    alvo = {"id": 1, "lat": -29.9, "lng": -51.0}
    # Google confirma que nao ha panorama -> 'NA'
    monkeypatch.setattr(SV, "metadados_pano", lambda *a, **k: False)
    assert asyncio.run(SV._capturar(PaginaFalsa(), alvo)) == "NA"
    # duvida (sem chave, rede, cota) -> volta para a fila, nunca 'NA'
    monkeypatch.setattr(SV, "metadados_pano", lambda *a, **k: None)
    assert asyncio.run(SV._capturar(PaginaFalsa(), alvo)) is None


def test_prompt_trata_alvo_dentro_de_predio_maior():
    """A foto e da via, nao da porta. Quando o ponto esta dentro de um
    atacadista ou de uma galeria, o modelo precisa poder NOMEAR isso — senao
    so lhe restam dois erros: negar o comercio que existe, ou colar no alvo o
    letreiro do estabelecimento de fora."""
    import avaliar_fachada as AF

    c = AF.SCHEMA_IA["properties"]["classificacao"]["properties"]
    assert "enquadramento_alvo" in c and "estabelecimento_maior" in c
    for v in ("fachada_propria", "unidade_dentro_de_loja_maior",
              "unidade_em_predio_ou_galeria", "unidade_em_condominio",
              "predio_visto_de_longe"):
        assert v in c["enquadramento_alvo"]["enum"], v

    p = AF._prompt_sistema()
    assert "atacadista" in p.lower(), "o caso do acougue dentro do atacadista sumiu"
    assert "NÃO é ausência de comércio" in p, "falta a regra do inverso"
    # e o juizo tambem tem de saber ler o enquadramento
    assert "unidade_dentro_de_loja_maior" in AF._PROMPT_JUIZO
    assert "recomenda_visita" in AF._PROMPT_JUIZO


def test_prompt_declara_a_distancia_da_camera():
    """De 125 m nao se le placa. Sem o numero no prompt, o modelo cobra o que a
    foto nao pode dar e conclui 'nenhum sinal de comercio'."""
    import avaliar_fachada as AF

    perto = AF._prompt_usuario({"nome": "X", "dist_camera_m": 12}, None)
    longe = AF._prompt_usuario({"nome": "X", "dist_camera_m": 125}, None)
    assert "12 m" in perto
    assert "NÃO SE COBRA" not in perto, "de perto a exigencia continua valendo"
    assert "125 m" in longe and "NÃO SE COBRA" in longe
    # sem a distancia registrada, nada e afirmado sobre ela
    mudo = AF._prompt_usuario({"nome": "X"}, None)
    assert "da via" not in mudo


def test_zoom_so_fecha_quando_a_distancia_exige():
    """A regra do usuario: se o POI esta no meio da quadra, va a via de frente e
    MIRE nele. Zoom e o que faz a mira valer — mas fechar cedo demais
    transforma qualquer muro entre a camera e o alvo na foto inteira, o que foi
    reprovado em imagem no Svariato (65 m com 30 graus deu close de muro)."""
    import streetview_capture as SV

    assert SV._fov_por_distancia(5) == 80, "de perto a lente e aberta"
    assert SV._fov_por_distancia(40) == 80
    assert SV._fov_por_distancia(65) == 60, "a 65 m ainda nao fecha"
    assert SV._fov_por_distancia(125) == 40
    assert SV._fov_por_distancia(280) == 25
    # monotonica: mais longe nunca abre mais
    ds = [5, 40, 41, 90, 91, 150, 151, 300]
    fovs = [SV._fov_por_distancia(d) for d in ds]
    assert fovs == sorted(fovs, reverse=True), fovs
    # e o raio permite chegar la
    assert SV.RAIO_PANO_M >= 300


def test_carregar_alvos_nao_repete_poi(con):
    """Um POI, uma leitura. Os LEFT JOIN de cadastro e CNEFE multiplicavam a
    linha, e a avaliacao pagava a mesma fachada duas vezes."""
    import area_utils
    import avaliar_fachada as AF

    poly = area_utils.carregar_area()
    if not poly:
        pytest.skip("nenhuma area de trabalho definida")
    ids = [a["poi_id"] for a in AF.carregar_alvos(poly, 0, False, con)]
    assert len(ids) == len(set(ids)), (
        f"{len(ids) - len(set(ids))} alvos repetidos — isso e leitura paga duas vezes")


def test_soltar_presos_devolve_a_fila(con):
    """POI com path e sem imagem volta para a fila; 'NA' fica onde esta."""
    import streetview_capture as SV

    marca = uuid.uuid4().hex[:8]
    with con.cursor() as cur:
        cur.execute("""select p.id from pois p
                        where not exists (select 1 from streetview_imgs s
                                           where s.poi_id = p.id and s.angulo='facade')
                          and coalesce(p.streetview_path,'') = ''
                        limit 2""")
        ids = [r[0] for r in cur.fetchall()]
        if len(ids) < 2:
            pytest.skip("sem POIs livres para o teste")
        preso, na = ids
        cur.execute("update pois set streetview_path=%s where id=%s", (f"{marca}.jpg", preso))
        cur.execute("update pois set streetview_path='NA' where id=%s", (na,))
    con.commit()
    try:
        SV.soltar_presos(con)
        with con.cursor() as cur:
            cur.execute("select coalesce(streetview_path,'') from pois where id=%s", (preso,))
            assert cur.fetchone()[0] == "", "o preso nao voltou para a fila"
            cur.execute("select streetview_path from pois where id=%s", (na,))
            assert cur.fetchone()[0] == "NA", "'NA' nao pode ser reaberto: nao ha panorama ali"
    finally:
        with con.cursor() as cur:
            cur.execute("update pois set streetview_path='' where id = any(%s)", (ids,))
        con.commit()


def test_classificacao_no_contrato():
    """As sete perguntas pedidas em 14/08/2026 tem de estar no SCHEMA — nao so
    no texto do prompt. Pergunta que nao esta no schema o modelo simplesmente
    nao responde, e ninguem percebe: o JSON volta valido sem o campo."""
    import avaliar_fachada as AF

    c = AF.SCHEMA_IA["properties"]["classificacao"]["properties"]
    assert len(AF.TIPOS_CLIENTE) == 10, AF.TIPOS_CLIENTE
    for esperado in ("comercial_empresarial_industrial", "terreno_vazio",
                     "construcao_em_curso", "predio_multiandar_abandonado",
                     "moradia_luxo_habitada", "moradia_simples_abandonada"):
        assert esperado in c["tipo_cliente"]["enum"], esperado
    assert "habitacoes_distintas" in c and "metodo_habitacoes" in c
    assert "rua_rural" in c["tipo_via"]["enum"]
    assert "avenida_pavimentada" in c["tipo_via"]["enum"]
    assert "fila_de_clientes" in c["pessoas_na_imagem"]["enum"]
    assert "numero_na_parede" in c

    v = AF._SCHEMA_VEREDITO["properties"]
    assert set(v["veredito"]["enum"]) == {"aprova_comercial", "reprova",
                                          "recomenda_visita"}
    assert v["nota_comercial"]["type"] == "integer"


def test_prompt_descreve_os_tres_medidores():
    import avaliar_fachada as AF

    p = AF._prompt_sistema()
    for termo in ("hidrômetro", "cavalete", "padrão de entrada", "bateria",
                  "caixa de inspeção", "fossa"):
        assert termo.lower() in p.lower(), f"o prompt nao descreve: {termo}"
    # e continua sem entregar o numero do cadastro para o modelo repetir
    u = AF._prompt_usuario({"nome": "X", "categoria": "Y", "endereco": "R. Z, 175"},
                           {"numero": "175"})
    assert "175" not in u, "o numero cadastrado voltou para o prompt"
    assert "R. Z" not in u, "o endereco voltou para o prompt"


def test_juizo_e_chamada_separada_e_recebe_o_cadastro():
    """A leitura nao ve o cadastro; o juizo ve. E o padrao que tornou o veredito
    confiavel aqui: quem descreve nao decide, quem decide nao olha o pixel."""
    import avaliar_fachada as AF

    alvo = {"poi_id": 1, "nome": "Padaria X", "categoria": "Padaria",
            "cnpj": "12345678000199", "cnae": "4721-1/02",
            "vinculo": {"matricula": "A1", "categoria": "RESIDENCIAL", "economias": 1}}
    obs = {"classificacao": {"tipo_cliente": "comercial_empresarial_industrial",
                             "habitacoes_distintas": 1, "metodo_habitacoes": [],
                             "tipo_via": "rua_pavimentada",
                             "pessoas_na_imagem": "fila_de_clientes",
                             "numero_na_parede": "175"},
           "triagem": {}, "uso": {}, "imagem": {}}
    txt = AF._resumo_para_juizo(alvo, obs)
    assert "RESIDENCIAL" in txt and "12345678000199" in txt
    assert "fila_de_clientes" in txt
    assert "0 a 10" in AF._PROMPT_JUIZO
    for saida in ("aprova_comercial", "recomenda_visita", "reprova"):
        assert saida in AF._PROMPT_JUIZO


def test_dossie_mostra_o_veredito_e_a_escala():
    """O veredito e a nota sao a resposta que o cliente compra. Nota sem a regua
    ao lado e numero solto: 7 parece alto ate alguem supor que a escala ia a 100."""
    import dossie

    d = {"poi_id": 1, "nome": "Padaria X", "endereco": "R. A, 10",
         "empresa": "Aegea - Corsan",
         "fachada": {"tipo_cliente": "comercial_empresarial_industrial",
                     "habitacoes": 1, "tipo_via": "rua_pavimentada",
                     "pessoas": "fila_de_clientes", "numero_parede": "175",
                     "veredito": "aprova_comercial", "nota": 9,
                     "justificativa": "letreiro e vitrine com CNPJ ativo",
                     "fatores": ["letreiro (imagem)", "CNPJ ativo (Receita)"]}}
    h = dossie.montar_html(d)
    assert "Veredito comercial da IA" in h
    assert "9<em>/10</em>" in h
    assert "Classificação do imóvel" in h
    # rotulo humanizado: chave de enum em documento comprobatorio parece erro
    assert "Comercial empresarial industrial" in h
    assert "comercial_empresarial_industrial" not in h
    assert h.count("<li>") == 2

    # sem veredito, o bloco simplesmente nao aparece — nada de secao vazia
    d2 = dict(d, fachada={"tipo_cliente": "moradia_simples_habitada"})
    h2 = dossie.montar_html(d2)
    assert "Veredito comercial da IA" not in h2
    assert "Moradia simples habitada" in h2


def test_ja_comercial_fica_de_fora_por_padrao():
    """A regra do usuario vira SQL, e o SQL tem de estar nos dois caminhos: na
    captura de fachada e na avaliacao."""
    import avaliar_fachada as AF
    import enriquecer_tudo as ET

    assert "e_comercial" in AF.SQL_JA_COMERCIAL
    assert "e_comercial" in ET._SQL_JA_COMERCIAL
    import inspect
    assert "incluir_ja_comerciais" in inspect.signature(AF.carregar_alvos).parameters
    assert "incluir_ja_comerciais" in inspect.signature(
        ET._sem_streetview_na_area).parameters


def test_panorama_fecha_a_conta(con):
    """Os quatro numeros tem de somar o universo — e sao eles que explicam por
    que a avaliacao alcanca menos POIs do que a area tem."""
    import area_utils
    import avaliar_fachada as AF

    poly = area_utils.carregar_area()
    if not poly:
        pytest.skip("nenhuma area de trabalho definida")
    p = AF.panorama_da_area(poly, con)
    assert p["com_fachada"] + p["sem_panorama"] + p["nunca_capturados"] == p["validos"], p
    assert p["a_avaliar"] <= p["com_fachada"]
    assert p["ja_avaliados"] <= p["validos"]

    # e o que a avaliacao carrega de fato tem de bater com `a_avaliar`
    alvos = AF.carregar_alvos(poly, 0, False, con)
    assert len(alvos) == p["a_avaliar"], (
        f"carregar_alvos={len(alvos)} != panorama.a_avaliar={p['a_avaliar']}")
