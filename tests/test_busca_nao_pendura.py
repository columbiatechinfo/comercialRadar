# -*- coding: utf-8 -*-
"""A busca no Maps não pode ficar viva sem trabalhar.

O QUE ACONTECEU EM 26/08/2026, duas vezes na mesma noite

Duas runs seguidas ficaram penduradas na etapa de busca. Sintoma idêntico nas
duas: processo vivo, log parado no cabeçalho da etapa, CPU no chão, navegador
nascendo e morrendo. Uma ficou 14 minutos assim, a outra 10, e as duas foram
mortas na mão — nenhuma escreveu uma linha dizendo o que estava acontecendo.

A CAUSA eram duas coisas que, juntas, tornavam o problema invisível:

    `_abrir()` devolvia False sem imprimir quando `acquire_blocking` não
    conseguia proxy (ele espera 60 tentativas de 2 s = 2 min e devolve None).

    O laço, ao receber False, devolvia o lote para a fila, dormia 5 s e tentava
    OUTRA VEZ — sem teto. Girava para sempre.

Cada uma sozinha seria tolerável. Juntas, produziam uma run que parecia viva e
nunca terminava, e não havia como distinguir "trabalhando devagar" de "girando
em falso" sem abrir o gerenciador de processos.

O CONSERTO tem três partes, e as três estão testadas aqui:

    diz por que falhou, com `flush=True` (mensagem no buffer chega tarde
    demais para servir de diagnóstico)
    desiste depois de 5 tentativas — ~10 min de pool sem dar IP não é congestão
    passageira
    o resumo final denuncia quantos POIs ficaram sem busca, senão "3
    processados" se lê como "3 de 3" quando eram 3 de 21
"""
import io
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import proxy_pool  # noqa: E402


def _fonte():
    return io.open(os.path.join(RAIZ, "search_pois_v2.py"), encoding="utf-8").read()


def test_o_pool_sabe_dizer_quantos_estao_livres_e_de_castigo():
    """Quando `acquire_blocking` devolve None, quem chama tem duas leituras
    possíveis e a conduta muda em cada uma: IPs EM USO passam sozinhos; IPs de
    CASTIGO significam que o Google barrou, e insistir queima o resto do pool.

    Sem estes dois números a mensagem de erro não ajuda ninguém a decidir."""
    p = proxy_pool.ProxyPool()
    p._proxies = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    p._cooldown = {"a": time.time() + 600}
    p._in_use = {"b"}
    livres, castigo = p.resumo()
    assert (livres, castigo) == (1, 1), f"resumo errado: {livres}, {castigo}"

    # e o castigo vencido não conta mais
    p._cooldown = {"a": time.time() - 1}
    livres, castigo = p.resumo()
    assert castigo == 0 and livres == 2


def test_a_falta_de_proxy_e_dita_em_voz_alta():
    """Era o caminho mudo. Sem isto, o log fica no cabeçalho da etapa e a run
    parece travada sem causa."""
    s = _fonte()
    i = s.index("async def _abrir(")
    corpo = s[i:i + 2600]
    assert "SEM PROXY" in corpo, "a falta de proxy voltou a ser silenciosa"
    assert "pool.resumo()" in corpo, "a mensagem deixou de dizer o estado do pool"


def test_toda_saida_do_caminho_de_falha_e_descarregada():
    """`print` sem `flush` num processo que escreve pouco fica preso no buffer
    de 8 KB do stdout — pode levar minutos para aparecer. Mensagem de
    diagnóstico que chega depois do problema não é diagnóstico."""
    s = _fonte()
    i = s.index("async def _abrir(")
    corpo = s[i:i + 2600]
    for marca in ("SEM PROXY", "Maps não abriu", "navegador não subiu"):
        j = corpo.index(marca)
        trecho = corpo[j:j + 320]
        assert "flush=True" in trecho, f"o aviso {marca!r} não é descarregado"


def test_o_worker_desiste_em_vez_de_girar_para_sempre():
    """O CORAÇÃO DESTE ARQUIVO.

    Desistir é melhor que pendurar: com teto, a run termina, diz quantos POIs
    ficaram sem busca, e as etapas seguintes (normalização, cruzamento) ainda
    rodam sobre o que já foi achado. E como rodar de novo ACRESCENTA, o que
    ficou para trás é retomado na próxima rodada, sem nada perdido."""
    s = _fonte()
    assert "MAX_FALHAS_ABRIR" in s, "o teto de tentativas sumiu"
    i = s.index("MAX_FALHAS_ABRIR = ")
    teto = int(s[i:i + 30].split("=")[1].split()[0])
    assert 2 <= teto <= 10, f"teto fora do razoável: {teto}"

    j = s.index("falhas_seguidas += 1")
    corpo = s[j:j + 700]
    assert "return" in corpo, "o worker voltou a insistir para sempre"
    assert "queue.put_nowait" in corpo, \
        "o lote deixou de voltar para a fila — POIs seriam perdidos"

    # e o contador zera quando a sessão abre, senão falhas espalhadas ao longo
    # de uma run longa somariam e derrubariam um worker saudável
    assert "falhas_seguidas = 0" in s, "o contador não zera no sucesso"


def test_o_resumo_denuncia_quem_ficou_sem_busca():
    """POI que nunca foi buscado não entra em nenhum status e some da conta.
    Sem esta linha, 3 de 21 se lê como 3 de 3."""
    s = _fonte()
    assert '"a_buscar"' in s, "o total a buscar deixou de ser registrado"
    assert "FICARAM SEM BUSCA" in s, "o resumo voltou a esconder o que faltou"
    i = s.index("FICARAM SEM BUSCA")
    assert "não cobrem a área" in s[i:i + 300], \
        "o resumo deixou de avisar que os números não cobrem a área"


def test_o_lote_devolvido_nao_vira_poi_perdido():
    """Quem desiste devolve o lote ANTES de sair. Se saísse sem devolver, os
    POIs daquele lote sumiriam da fila e da próxima rodada — e o sistema todo
    se apoia em "rodar de novo acrescenta"."""
    s = _fonte()
    i = s.index("falhas_seguidas += 1")
    j = s.index("return", i)
    entre = s[i:j]
    assert "queue.put_nowait" in entre, \
        "o worker passou a sair sem devolver o lote"
