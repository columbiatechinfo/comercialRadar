# -*- coding: utf-8 -*-
"""O PLANO DE PROXIES E O CONSUMO DELE — inventário, estado e histórico.

POR QUE ESTAS TABELAS EXISTEM. O rodízio de IPs vivia inteiro dentro do processo
de mineração, no i9: quem estava em uso, quem tomou castigo e quanto cada um
trabalhou morria com o processo. O servidor não tinha como responder "o que eu
pago e em que estado está", e o operador descobria IP queimado lendo log.

Em 28/08/2026 o plano passou de 100 para 500 IPs (250 BR + 250 CO) e a pergunta
"quanto gastei" deixou de caber num log que rola para cima.

MEDIDO ao construir: 500 em `proxy_ip` (250 BR ativos, 250 CO reservados), e o
endpoint devolvendo 90 KB com os 500 itens.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

POOL = os.path.join(RAIZ, "proxy_pool.py")
SERVER = os.path.join(RAIZ, "server.py")
HTML = os.path.join(RAIZ, "frontend", "painel.html")
JS = os.path.join(RAIZ, "frontend", "painel.js")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _codigo(caminho, marca="#"):
    return "\n".join(l.split(marca)[0] for l in _ler(caminho).splitlines())


def test_o_pais_e_o_primeiro_recorte():
    """Buscar endereço brasileiro por IP colombiano faz duas coisas ruins de uma
    vez: o Maps LOCALIZA o resultado pelo IP — outro conjunto, outra ordem,
    outro idioma — e um endereço de Canoas pedido de Bogotá é o padrão que um
    detector procura."""
    py = _codigo(POOL)
    assert "def _do_pais(" in py, "o pool voltou a sortear IP de qualquer país"
    assert "_do_pais(p)" in py.split("def _disponivel(")[1][:300], \
        "o filtro de país saiu do caminho de disponibilidade"

    # o padrão vem do config, e NÃO de cada chamador: são doze lugares que
    # constroem o pool, e mudar os doze é garantir esquecer um
    cfg = _codigo(os.path.join(RAIZ, "config.py"))
    assert "PROXY_PAIS" in cfg, "o país padrão sumiu do config"
    assert 'getattr(config, "PROXY_PAIS"' in py, \
        "o pool parou de herdar o país padrão, e os doze chamadores voltam a sortear tudo"


def test_o_resumo_do_rodizio_conta_so_o_pais_ativo():
    """Somar os IPs reservados de outro país faria o log dizer "0 de castigo"
    com o rodízio inteiro parado — e a conduta de quem lê essa linha mudaria
    para pior."""
    py = _codigo(POOL)
    corpo = py.split("def resumo(")[1][:900]
    assert "_do_pais(p)" in corpo, \
        "o resumo voltou a contar IP reservado como se fosse utilizável"


def test_o_que_vai_para_a_tela_nao_leva_credencial():
    """Usuário e senha do proxy são a chave do plano inteiro."""
    py = _codigo(POOL)
    corpo = py.split("def resumo_completo(")[1][:2000]
    for segredo in ("password", "username", '"server"'):
        assert segredo not in corpo, \
            "o resumo do monitor voltou a expor %s" % segredo

    srv = _codigo(SERVER)
    corpo = srv[srv.index("def proxies_monitor("):srv.index("def proxies_monitor(") + 6000]
    for segredo in ("password", "username"):
        assert segredo not in corpo, "o endpoint do monitor expõe %s" % segredo


def test_monitorar_nunca_derruba_minerar():
    """Se o banco estiver fora, o rodízio continua funcionando e o que se perde
    é a linha do gráfico, não a run."""
    py = _codigo(POOL)
    for fn in ("registrar_inventario", "flush_eventos", "_conexao"):
        corpo = py.split("def %s(" % fn)[1][:1800]
        assert "except Exception" in corpo, \
            "%s pode estourar e derrubar a mineração" % fn
    # e é EM LOTE: um INSERT por `acquire` põe uma ida ao banco dentro do
    # caminho quente de dez workers
    assert "_LOTE_EVENTOS" in py and "execute_values" in py, \
        "o registro voltou a escrever evento por evento"


def test_o_estado_e_derivado_e_nao_guardado():
    """Guardar um booleano "está de castigo" seria estado a expirar sozinho, e
    ninguém estaria lá para apagá-lo quando o cooldown vencesse."""
    srv = _codigo(SERVER)
    corpo = srv[srv.index("def proxies_monitor("):srv.index("def proxies_monitor(") + 6000]
    assert "castigo_ativo" in corpo and "seconds')::interval > now()" in corpo, \
        "o castigo deixou de ser derivado da duração do evento"
    # O `id` DESEMPATA: os eventos vão em LOTE, num INSERT só, e o `now()` do
    # default é o mesmo para todos. Sem isto, um IP que tomou castigo e outro
    # que foi devolvido apareceram AMBOS como "em uso" — medido.
    assert "ORDER BY proxy_id, em DESC, id DESC" in corpo, (
        "o desempate por `id` saiu: eventos do mesmo lote empatam no tempo e "
        "o estado do IP passa a ser sorteado")
    # e toda consulta é limitada por janela: `proxy_evento` cresce por run
    assert corpo.count("make_interval(hours =>") >= 3, \
        "alguma consulta do monitor voltou a varrer o histórico inteiro"


def test_o_modal_existe_e_e_quase_tela_cheia():
    """São 500 linhas para conferir; numa caixa estreita a tabela vira rolagem
    dentro de rolagem.

    VERIFICADO NO NAVEGADOR: 1232x672 numa viewport de 1280x720, cabeçalho da
    tabela `sticky`, a tabela rolando sozinha e a página sem rolagem lateral.
    """
    html = _ler(HTML)
    assert 'id="m-proxies"' in html, "o modal de proxies sumiu"
    assert 'id="btn-proxies"' in html, "o botão da lateral sumiu"
    i = html.index('id="m-proxies"')
    caixa = html[i:i + 700]
    assert "h-full w-full" in caixa, "o modal deixou de ser quase tela cheia"
    for alvo in ("px-cartoes", "px-barra", "px-estados", "px-motivos",
                 "px-etapas", "px-linhas", "px-conta", "px-janela",
                 "px-busca", "px-filtro"):
        assert 'id="%s"' % alvo in html, "o alvo %s sumiu do modal" % alvo
    assert "sticky top-0" in html[i:i + 6000], \
        "o cabeçalho da tabela deixou de grudar, e some com 500 linhas"


def test_a_tela_diz_a_taxa_e_nao_so_o_numero():
    """"12 castigos" não diz nada sem saber de quantas pegadas: 12 em 20 é um
    problema, 12 em 4.000 é rotina."""
    js = "\n".join(l for l in _ler(JS).splitlines() if not l.lstrip().startswith("//"))
    assert "das ${nf.format(pegou)} pegadas" in js, \
        "o cartão de queimados voltou a mostrar número absoluto sem a taxa"
    # e o corte da tabela é DECLARADO, não silencioso
    assert "filtre para ver os demais" in js, \
        "a tabela voltou a cortar em silêncio, e a tela mente sobre o tamanho do plano"
