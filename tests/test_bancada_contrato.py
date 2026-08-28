# -*- coding: utf-8 -*-
"""A bancada e a API precisam falar o mesmo nome de campo.

DOIS DEFEITOS, E O SEGUNDO SÓ EXISTIA POR CAUSA DO PRIMEIRO (27/08/2026)

**A tela lia `lon`; a API manda `lng`.** A página nasceu como modelo, com um
dataset de exemplo que usava `lon`, e nunca foi acertada. `a.lon` era sempre
`undefined`, e a linha

    const lat = a.lat.toFixed(6), lon = a.lon.toFixed(6)

estourava com *"Cannot read properties of undefined (reading 'toFixed')"*. A
guarda logo acima checava só `a.lat == null` — com latitude presente e
longitude ausente ela deixava passar.

**E aí aparecia o segundo.** A página fazia `D = JSON.parse(<script id=dataset>)`
ao carregar e só DEPOIS buscava o real. Enquanto a busca não voltava — e sempre
que ela falhava — o que ficava na tela era o exemplo: 40 ligações com nomes,
faturas e comentários de gente que não existe, com a marca do cliente em volta.

O usuário viu exatamente isso: o aviso vermelho de falha **e** a tela cheia de
dado plausível. O aviso ele leu; a tela ele acreditou.

> **Dado de exemplo se passando por dado real é pior que tela vazia.** A tela
> vazia faz perguntar; o exemplo faz decidir errado.

Eram 7.415 das 10.370 linhas do arquivo. Sobrou um esqueleto com a mesma forma
e nenhum conteúdo.
"""
import io
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402

PAGINA = os.path.join(RAIZ, "frontend", "bancada.html")


def _pagina():
    return io.open(PAGINA, encoding="utf-8").read()


def _dataset_embutido():
    s = _pagina()
    abre = '<script id="dataset" type="application/json">'
    i = s.index(abre)
    return json.loads(s[i + len(abre):s.index("</script>", i)])


def test_a_pagina_nao_traz_dado_de_ninguem():
    """O esqueleto tem a forma; conteúdo vem só da API."""
    d = _dataset_embutido()
    assert d["ligacoes"] == [], (
        f"a página voltou a embutir {len(d['ligacoes'])} ligações — elas aparecem "
        f"na tela enquanto a busca não volta, e ficam lá quando ela falha")
    assert d["fontes"] == [], "voltou a embutir catálogo de fontes"
    assert d["meta"].get("tenant") in ("", None), "voltou a embutir um tenant"

    s = _pagina()
    for marca in ("lote de demonstração", "exemplo-padaria", '"tenant": "demo"'):
        assert marca not in s, f"resquício do lote de exemplo: {marca!r}"


def test_o_vocabulario_fica_porque_e_rotulo_da_tela():
    """A única parte do exemplo que não é dado de ninguém: status, tier e
    motivos são os rótulos da própria bancada. Sem eles a página não monta nem
    o cabeçalho — e a API manda os dela por cima."""
    d = _dataset_embutido()
    assert d["vocabulario"], "o vocabulário sumiu junto com o exemplo"
    assert "status" in d["vocabulario"] and "tier" in d["vocabulario"]


def test_a_tela_le_lng_e_nao_lon():
    """O nome do campo é `lng` em todo o projeto — `pois.maps_lng`,
    `lng_origem`, `ANCORA_CAMPOS`. Só a página dizia `lon`, herdado do modelo."""
    s = _pagina()
    assert "a.lon" not in s, "a página voltou a ler `a.lon`, que a API nunca manda"
    assert "atual.ancora.lon" not in s
    assert "a.lng" in s, "a página deixou de ler a longitude"


def test_a_guarda_de_coordenada_olha_as_duas():
    """Checar só a latitude era o que deixava o `toFixed` chegar na longitude
    ausente. Meia guarda não protege."""
    s = _pagina()
    assert "a.lat == null || a.lng == null" in s, \
        "a guarda voltou a checar só a latitude"


def test_a_api_manda_o_que_a_tela_espera():
    """O contrato, verificado contra o payload de verdade — não contra o
    exemplo. É o teste que teria pego o defeito no dia em que ele nasceu."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        # `fundido_em is null` desde a migração 0036. Pelo critério antigo esta
        # amostra podia cair num POI ABSORVIDO — `status` voltou a guardar a
        # origem, e a maioria dos fundidos tem hoje `estadual` ou `descoberto`
        # ali. Montar a bancada de um ponto que não existe mais no mapa testaria
        # o contrário do que interessa.
        cur.execute("""select id from pois
                        where fundido_em is null
                          and coalesce(maps_lat, lat_origem) is not null
                        order by id limit 1""")
        r = cur.fetchone()
        if not r:
            import pytest
            pytest.skip("sem POI para montar")
        import bancada_dataset as BD
        d = BD.montar(con, [{"poi_id": r[0], "status": "pendente",
                             "prioridade": "normal"}], e_root=True, base="teste")
    finally:
        con.close()

    lig = d["ligacoes"][0]
    assert not lig.get("erro"), f"a montagem falhou: {lig.get('erro')}"
    a = lig["ancora"]
    assert "lng" in a, "a âncora deixou de trazer lng"
    assert "lon" not in a, "a API passou a mandar `lon` — decida um nome só"

    # e o que a página faz com isso não pode estourar
    if a.get("lat") is not None:
        assert a.get("lng") is not None, (
            "âncora com latitude e sem longitude — é exatamente o estado que "
            "fazia o toFixed estourar")


def test_falha_de_carga_nao_deixa_dado_antigo_na_tela():
    """Quando a busca falha, a página limpa em vez de manter o que estava. Sem
    isto, o esqueleto vazio resolve a primeira carga mas não a segunda: quem
    troca de ponto e recebe erro continuaria vendo o ponto anterior."""
    s = _pagina()
    i = s.index("async function carregar(")
    corpo = s[i:i + 1800]
    assert "limpar(" in corpo, "a falha de carga deixou de limpar a tela"
    assert re.search(r"catch\s*\(e\)\s*\{\s*\n?\s*limpar\(", corpo), \
        "o catch da carga não limpa mais a tela"


def test_fila_vazia_nao_quebra_o_boot():
    """DEFEITO QUE EU MESMO CRIEI AO TIRAR O EXEMPLO, 27/08/2026.

    `boot()` terminava com `abrir(D.ligacoes[0].num_ligacao)`. Com as 40
    ligações de exemplo embutidas, `ligacoes[0]` sempre existia e o vazio nunca
    foi exercitado. Tirado o exemplo, a PRIMEIRA carga passou a estourar ali —
    antes mesmo de a busca responder.

    Fila vazia é estado normal: monta-se o cabeçalho e espera-se o `carregar()`.
    """
    s = _pagina()
    i = s.index("abrir(D.ligacoes[0].num_ligacao)")
    trecho = s[max(0, i - 200):i + 60]
    assert "D.ligacoes && D.ligacoes.length" in trecho, \
        "o boot voltou a abrir a primeira ligação sem checar se existe alguma"


def test_as_correcoes_vivem_no_INSTALADOR_e_nao_no_html():
    """A LIÇÃO QUE QUASE SE PERDEU, 27/08/2026.

    `frontend/bancada.html` é GERADO por `instalar_bancada.py` a partir do zip
    do modelo, e está no `.gitignore`. Eu consertei o HTML direto, provei na
    tela, e só na hora do commit o git recusou o arquivo — o conserto teria
    sumido na próxima instalação, sem deixar rastro.

    Por isso as trocas moram no instalador, versionadas, e são reaplicadas toda
    vez que o modelo é reinstalado.
    """
    s = io.open(os.path.join(RAIZ, "instalar_bancada.py"), encoding="utf-8").read()
    assert "_TROCAS" in s, "as correções saíram do instalador"
    assert "a.lon" in s and "a.lng" in s, "a troca de lon por lng sumiu do instalador"
    assert "_esvaziar_dataset" in s, "o esvaziamento do exemplo saiu do instalador"


def test_o_instalador_denuncia_troca_que_nao_casou():
    """Texto muda quando o zip do modelo muda. Uma correção que deixou de casar
    tem de aparecer NA INSTALAÇÃO — não semanas depois, na tela do operador,
    como o `toFixed` apareceu."""
    s = io.open(os.path.join(RAIZ, "instalar_bancada.py"), encoding="utf-8").read()
    assert "nao_casaram" in s, "o instalador voltou a silenciar troca que falhou"
    assert "NAO CASOU" in s, "a troca que falha deixou de ser impressa"
    assert "todas as correções do modelo casaram" in s, \
        "a checagem final deixou de cobrir as correções"
