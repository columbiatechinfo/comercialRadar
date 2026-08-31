# -*- coding: utf-8 -*-
"""O CARTÃO PASSA A CONTAR SÓ A ÁREA DESENHADA.

Antes, com área à mão, o cartão mostrava a base inteira ao lado de um mapa
recortado: o número não descrevia nada do que estava na tela. `/api/stats`
ganhou `?area=1`.

POR QUE NÃO É `ST_Contains`. Seria o natural, e não dá: o PostGIS deste banco
vive no schema `extensions`, e o papel `app_user` não tem USAGE
nele — nem o tipo `geometry` resolve pela conexão do produto. Liberar exigiria
superusuário, e a decisão de 28/08/2026 foi não depender disso.

O desenho que ficou: o SQL corta pela CAIXA ENVOLVENTE (índice
`pois_coord_por_tenant`, migração 0039) e o teste exato do polígono roda em
Python sobre o que sobrou.

MEDIDO:

    índice 0039           20 ms -> 9 ms numa caixa de bairro (36.620 ativos)
    ler a área            188 ms pela `carregar_area()` (conexão própria)
                          -> pelo cursor da requisição, o escopo caiu 77 -> 20 ms
    /api/stats            51.447 válidos sem recorte, 15 com área=1;
                          276 ms -> 193 ms (cada agregado roda sobre 15 linhas)
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

SERVER = os.path.join(RAIZ, "server.py")
JS = os.path.join(RAIZ, "frontend", "painel.js")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _sem_comentario(txt, marca="#"):
    """Tira comentário de linha. Para SQL, `marca="--"`.

    O TESTE OLHA CÓDIGO, NÃO PROSA. Asserções deste arquivo já reprovaram código
    correto porque o comentário logo acima explicava justamente o que elas
    proibiam — o `ST_Contains` que NÃO se usa, a `carregar_area()` que NÃO se
    chama. É o vício que mais me pegou neste repositório.
    """
    fora = []
    for l in txt.splitlines():
        i = l.find(marca)
        fora.append(l if i < 0 else l[:i])
    return "\n".join(fora)


def _codigo_py(caminho):
    """O Python do arquivo SEM comentário e SEM docstring.

    `_sem_comentario` não bastava: ela tira `#`, e a proibição casava com a
    DOCSTRING, que é onde eu explico por que não faço aquilo. Aqui o `ast`
    remove as duas coisas — sobra só o que executa.
    """
    import ast
    fonte = _ler(caminho)
    arv = ast.parse(fonte)
    fora = set()
    for no in ast.walk(arv):
        if not isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            continue
        corpo = getattr(no, "body", None)
        if corpo and isinstance(corpo[0], ast.Expr) and \
           isinstance(corpo[0].value, ast.Constant) and \
           isinstance(corpo[0].value.value, str):
            d = corpo[0]
            fora.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    linhas = [("" if i + 1 in fora else l)
              for i, l in enumerate(fonte.splitlines())]
    return _sem_comentario("\n".join(linhas))


def _funcao(py, nome):
    """O corpo de uma função, do `def` até o próximo `def`/`@` na coluna zero.

    Janela por CONTAGEM DE BYTES é falso negativo esperando o dia: `i + 1400`
    reprovou código correto assim que a função ganhou uma docstring maior.
    """
    import re
    i = py.index("def %s(" % nome)
    m = re.search(r"^(def |@|class )", py[i + 10:], re.M)
    return py[i:i + 10 + m.start()] if m else py[i:]


def _quadrado():
    return [[0.0, 0.0], [0.0, 10.0], [10.0, 10.0], [10.0, 0.0]]


def test_o_teste_de_ponto_no_poligono_acerta():
    import server
    q = _quadrado()
    assert server._dentro_do_anel(q, 5.0, 5.0), "o centro do quadrado ficou de fora"
    assert not server._dentro_do_anel(q, 15.0, 5.0), "ponto ao norte entrou"
    assert not server._dentro_do_anel(q, 5.0, 15.0), "ponto a leste entrou"
    assert not server._dentro_do_anel(q, -1.0, -1.0), "ponto a sudoeste entrou"

    # CÔNCAVO: um quadrado 10x10 com um entalhe ao sul, entre lng 4 e 6, subindo
    # até lat 8. É o caso que separa um teste de raio de verdade de um "está na
    # caixa envolvente" — o entalhe fica DENTRO do retângulo e FORA da área.
    u = [[0, 0], [0, 4], [8, 4], [8, 6], [0, 6], [0, 10], [10, 10], [10, 0]]
    assert server._dentro_do_anel(u, 1.0, 2.0), "o braço oeste do U ficou de fora"
    assert server._dentro_do_anel(u, 1.0, 8.0), "o braço leste do U ficou de fora"
    assert server._dentro_do_anel(u, 9.0, 5.0), "o topo do U ficou de fora"
    assert not server._dentro_do_anel(u, 1.0, 5.0), \
        "o entalhe entrou: isto é uma caixa envolvente, não um teste de polígono"


def test_o_mesmo_algoritmo_nos_dois_lados():
    """Duas implementações do mesmo teste divergem, e no dia em que divergirem o
    cartão lateral e a ficha do polígono vão discordar sobre a mesma área."""
    js = _ler(JS)
    py = _ler(SERVER)
    assert "function dentroDoAnel(" in js, "o teste de raio sumiu do navegador"
    assert "def _dentro_do_anel(" in py, "o teste de raio sumiu do servidor"
    # a mesma condição de cruzamento, escrita nas duas linguagens
    assert "(ai[0] > lat) !== (aj[0] > lat)" in js
    assert "(ai[0] > lat) != (aj[0] > lat)" in py
    assert "(aj[1] - ai[1]) * (lat - ai[0])) / (aj[0] - ai[0]) + ai[1]" in js
    assert "(aj[1] - ai[1]) * (lat - ai[0]) / (aj[0] - ai[0]) + ai[1]" in py


def test_o_recorte_corta_pela_caixa_antes_do_teste_exato():
    """Sem a caixa, o teste exato rodaria sobre a tabela inteira em Python —
    trocar um seq scan por um laço no servidor de aplicação é piorar.

    A caixa vive no `_caixa()`, o SQL de cada tabela no chamador, e quem junta
    os dois é o `_materializar_escopo()`. Dois recortes usam a mesma máquina:
    POIs e ligações do cadastro.
    """
    py = _codigo_py(SERVER)
    assert "def _caixa(anel):" in py, "a caixa envolvente sumiu"
    assert "min(lats), max(lats), min(lngs), max(lngs)" in py, \
        "a caixa deixou de vir dos extremos do anel"
    assert "def _materializar_escopo(" in py, \
        "a montagem do recorte sumiu, e cada tabela vai reimplementá-la"

    for fn, tabela, coord in (
        ("_escopo_da_area", "FROM pois", "COALESCE(maps_lat, lat_origem)"),
        ("_escopo_do_cadastro", "FROM cadastro_cliente", "lat BETWEEN %s AND %s"),
    ):
        corpo = _funcao(py, fn)
        assert tabela in corpo, "%s deixou de recortar a tabela dele" % fn
        assert coord in corpo, \
            "%s parou de usar a coordenada indexada, e o índice não casa" % fn
        assert "BETWEEN %s AND %s" in corpo, "%s perdeu a caixa envolvente" % fn

    # POI fundido não é ponto: foi absorvido por outro
    assert "fundido_em IS NULL" in _funcao(py, "_escopo_da_area"), \
        "POI fundido voltou a entrar na conta"

    # a ligação NÃO passa pelo POI: ela tem coordenada própria, e a que não tem
    # POI é justamente a fila de vinculação humana
    assert "poi_id" not in _funcao(py, "_escopo_do_cadastro"), \
        "o recorte das ligações voltou a passar pelo POI, e perde a fila"


def test_a_area_e_lida_pelo_cursor_da_requisicao():
    """`area_utils.carregar_area()` abre conexão própria: medido, 188 ms — mais
    que todo o resto do recorte somado. A requisição já tem conexão com a
    identidade certa, e um lugar só lê a área.
    """
    py = _codigo_py(SERVER)
    corpo = _funcao(py, "_anel_da_area")
    assert "SELECT polygon FROM area_trabalho" in corpo, \
        "o recorte voltou a ler a área por fora do cursor da requisição"
    assert "carregar_area(" not in corpo, \
        "voltou a abrir uma segunda conexão só para ler seis vértices"

    for fn in ("_escopo_da_area", "_escopo_do_cadastro"):
        assert "_anel_da_area(cur)" in _funcao(py, fn), \
            "%s parou de usar a leitura compartilhada da área" % fn


def test_as_oito_consultas_compartilham_o_mesmo_recorte():
    """Refazer o teste em cada uma seria oito vezes o mesmo trabalho."""
    py = _ler(SERVER)
    assert "_escopo_area" in py, "a materialização do recorte sumiu"
    assert "ON COMMIT DROP" in py, \
        "a temp table do recorte deixou de morrer com a transação"
    i = py.index("def stats(")
    corpo = py[i:i + 1400]
    assert "area: int = 0" in corpo, "o /api/stats não aceita mais a área"
    assert 'wp = f"({wp}) AND {rec[0]}"' in corpo and 'wj = f"({wj}) AND {rec[1]}"' in corpo, \
        "a área deixou de se combinar com o município — um dos dois recortes se perde"


def test_a_migracao_do_indice_existe_e_nao_usa_postgis():
    sql = _sem_comentario(
        _ler(os.path.join(RAIZ, "migrations_a2l", "0001_radar_comercial.sql")), "--")
    assert "pois_coord_por_empresa" in sql, \
        "o índice da coordenada sumiu, ou mudou de nome sem avisar o código"
    # O trecho do PRÓPRIO índice, e não o arquivo inteiro: com 72 índices no
    # mesmo arquivo, procurar `id_empresa` antes do primeiro `WHERE` acharia o
    # de outra tabela e passaria por acidente.
    i = sql.index("pois_coord_por_empresa")
    trecho = sql[i:sql.index(";", i)]
    assert trecho.index("id_empresa") < trecho.index("COALESCE"), \
        "id_empresa deixou de ser a primeira coluna do índice"
    assert "COALESCE(maps_lat, lat_origem)" in trecho, \
        "o índice deixou de indexar a coordenada efetiva, e o planejador não o usa"
    assert "ST_" not in sql, (
        "a migração voltou a depender de PostGIS, que o papel do produto não "
        "pode usar — a criação falha com 'permission denied for schema extensions'")


def test_o_navegador_manda_a_area_como_sinalizador():
    """Mandar centenas de vértices numa query string seria repetir o que o
    servidor já tem gravado."""
    js = _ler(JS)
    assert '"?area=1"' in js and 'estado.temArea ?' in js, \
        "a tela parou de avisar o servidor que há área desenhada"
    assert "polygon" not in js[js.index("async function carregarStats"):
                               js.index("async function carregarStats") + 900], \
        "o polígono voltou para a URL"
    # e o recorte do lado do navegador acompanha o do servidor
    i = js.index("function poisDoEscopo(")
    assert "dentroDoAnel(anel, p.lat, p.lng)" in js[i:i + 900], \
        "o cartão voltou a contar fora da área desenhada, e discorda das barras"
