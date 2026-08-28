# -*- coding: utf-8 -*-
"""O CARTÃO PASSA A CONTAR SÓ A ÁREA DESENHADA.

Antes, com área à mão, o cartão mostrava a base inteira ao lado de um mapa
recortado: o número não descrevia nada do que estava na tela. `/api/stats`
ganhou `?area=1`.

POR QUE NÃO É `ST_Contains`. Seria o natural, e não dá: o PostGIS deste banco
vive no schema `extensions`, e o papel `comercialradar_worker` não tem USAGE
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
    """O TESTE OLHA CÓDIGO, NÃO PROSA.

    Duas asserções deste arquivo falharam contra um código correto porque o
    comentário logo acima explicava justamente o que elas proibiam — o
    `ST_Contains` que NÃO se usa, a `carregar_area()` que NÃO se chama. É o
    mesmo vício que já me pegou cinco vezes neste repositório: o teste passa a
    conferir a minha prosa.
    """
    fora = []
    for l in txt.splitlines():
        i = l.find(marca)
        fora.append(l if i < 0 else l[:i])
    return "\n".join(fora)


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
    trocar um seq scan por um laço no servidor de aplicação é piorar."""
    py = _ler(SERVER)
    i = py.index("def _escopo_da_area(")
    corpo = py[i:i + 3000]
    assert "BETWEEN %s AND %s" in corpo, "a caixa envolvente sumiu do SQL"
    assert "min(lats), max(lats), min(lngs), max(lngs)" in corpo, \
        "a caixa deixou de vir dos extremos do anel"
    assert "COALESCE(maps_lat, lat_origem)" in corpo, \
        "o recorte parou de usar a coordenada efetiva, e o índice 0039 não casa"
    assert "fundido_em IS NULL" in corpo, "POI fundido voltou a entrar na conta"


def test_a_area_e_lida_pelo_cursor_da_requisicao():
    """`area_utils.carregar_area()` abre conexão própria: medido, 188 ms — mais
    que todo o resto do recorte somado. A requisição já tem conexão com a
    identidade certa."""
    py = _ler(SERVER)
    i = py.index("def _escopo_da_area(")
    corpo = _sem_comentario(py[i:i + 3000])
    assert "SELECT polygon FROM area_trabalho" in corpo, \
        "o recorte voltou a ler a área por fora do cursor da requisição"
    assert "area_utils.carregar_area()" not in corpo, \
        "voltou a abrir uma segunda conexão só para ler seis vértices"


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
    d = os.path.join(RAIZ, "migrations")
    achou = [f for f in os.listdir(d) if f.startswith("0039_")]
    assert achou, "a migração 0039 do índice de coordenada sumiu"
    sql = _sem_comentario(_ler(os.path.join(d, achou[0])), "--")
    assert "pois_coord_por_tenant" in sql, "o índice mudou de nome sem avisar o código"
    assert "tenant_id" in sql[:sql.index("WHERE")], \
        "tenant_id deixou de ser a primeira coluna do índice"
    assert "COALESCE(maps_lat, lat_origem)" in sql, \
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
