# -*- coding: utf-8 -*-
"""O NAVEGADOR MORREU, OU O LUGAR NÃO EXISTE? São coisas muito diferentes.

O DEFEITO, medido em Santa Maria em 29/08/2026. De 1.046 `nao_encontrado`,
**1.033 (98,8%)** traziam a mesma mensagem:

    Target page, context or browser has been closed

Não era ausência no Maps: era o navegador do worker morto, e cada busca seguinte
falhando em milissegundos. POI legítimo — "Sala do Empreendedor Santa Maria",
"Tabelionato de Notas", "Desentupidora Flores e Trindade" — virou "não existe no
Maps", foi gravado como resolvido e passou a ser PULADO em qualquer retomada.

A PROVA DE QUE EXISTIAM: buscados um a um pelo MESMO caminho, MESMO IP e MESMO
perfil, **6 de 6 apareceram** — 4 deles com ficha direta.

O QUE O DIAGNÓSTICO POR WORKER MOSTROU, e é o que aponta o conserto:

    W7   18 ok /  1 falha    <- recebeu a cura de perfil
    W2   13 ok /  5 falhas   <- recebeu a cura de perfil
    outros oito     0 ok / 116-144 falhas cada

Os DOIS únicos workers cuja sessão foi reconstruída são os DOIS únicos que
produziram. A cura já existia e funcionava — ela só não disparava quando o Maps
ABRIA e o navegador morria depois.

Descartados por medição, e cada um custou uma volta: degradação de sessão (perfil
novo não mudou nada — e o diretório é apagado ao fim de toda run), IP queimado
(500 IPs, 2 castigos), periferia residencial (só 4% das falhas), seletor quebrado
(existe e está visível em todos os estágios) e recursos (65 GB livres, sem OOM).
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

ALVO = os.path.join(RAIZ, "search_pois_v2.py")


def _codigo():
    """Sem comentário — pelo `tokenize`, apagando só o vão do comentário."""
    import tokenize
    linhas = io.open(ALVO, encoding="utf-8").read().splitlines(keepends=True)
    with tokenize.open(ALVO) as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type != tokenize.COMMENT:
                continue
            i, a, b = tok.start[0] - 1, tok.start[1], tok.end[1]
            linhas[i] = linhas[i][:a] + " " * (b - a) + linhas[i][b:]
    return "".join(linhas)


def test_morte_de_sessao_tem_status_proprio():
    """Gravar "o navegador caiu" com o nome "o lugar não existe" é mentir no
    dado, e a mentira sobrevive à retomada."""
    py = _codigo()
    assert "def _e_morte_de_sessao(" in py, "o reconhecedor de sessão morta sumiu"
    assert "target page, context or browser has been closed" in py.lower(), \
        "a mensagem real do Playwright saiu da lista"
    assert '"erro_sessao"' in py, "o status próprio da falha de infraestrutura sumiu"

    # e ele é aplicado no ponto que produziu 98,8% das falhas
    i = py.index("motivo_coord = await abrir_coordenada_para_n2")
    assert "_e_morte_de_sessao(motivo_coord)" in py[i:i + 700], \
        "a preparação da coordenada voltou a chamar sessão morta de `nao_encontrado`"


def test_a_retomada_devolve_o_erro_de_sessao_a_fila():
    """`erro_sessao` não é resultado: é infraestrutura que falhou. Guardá-lo
    como processado transforma uma queda de navegador em veredito permanente."""
    py = _codigo()
    i = py.index("results_existentes = json.loads")
    corpo = py[i:i + 900]
    assert 'r.get("status") != "erro_sessao"' in corpo, \
        "a retomada voltou a considerar sessão morta como item já resolvido"
    assert "processados = {_chave(r) for r in results_existentes}" in corpo, \
        "a lista de processados deixou de sair da lista já filtrada"


def test_o_worker_se_cura_quando_perde_o_navegador():
    """Sem isto o worker segue moendo o lote inteiro contra uma página fechada
    — foi o que oito dos dez fizeram durante a run toda."""
    py = _codigo()
    # A JANELA ACOMPANHA O BLOCO, e não um número de bytes: `i + 1200` cortou
    # o `queue.put_nowait` assim que o comentário do conserto entrou, e o teste
    # reprovou código correto. Medida fixa sobre arquivo que cresce é falso
    # negativo esperando o dia.
    i = py.index("res = await search_one(")
    corpo = py[i:py.index("async with lock:", i)]
    assert 'res.get("status") == "erro_sessao"' in corpo, \
        "o worker voltou a ignorar a morte do próprio navegador"
    assert "_derrubar()" in corpo, "a sessão morta deixou de ser derrubada"
    assert "queue.put_nowait" in corpo, \
        "o resto do lote deixou de voltar para a fila"
    # o IP NÃO leva castigo: não foi ele que falhou
    j = corpo.index('res.get("status") == "erro_sessao"')
    assert "cooldown" not in corpo[j:j + 500], \
        "o IP voltou a ser punido por uma morte que não foi culpa dele"


def test_os_dez_nao_sobem_no_mesmo_instante():
    """Dez contextos persistentes nascendo juntos é o que melhor explica a morte
    de oito: não faltou memória, disco nem /dev/shm, e os dois sobreviventes
    foram os que subiram depois."""
    py = _codigo()
    i = py.index("async def worker(")
    corpo = py[i:i + 2500]
    assert "asyncio.sleep(wid *" in corpo, \
        "o arranque escalonado sumiu: os dez voltam a subir no mesmo instante"
