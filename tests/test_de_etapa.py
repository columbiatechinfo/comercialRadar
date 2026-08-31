# -*- coding: utf-8 -*-
"""RETOMAR UMA RODADA QUE CAIU NO MEIO, sem refazer a captura.

POR QUE ELE EXISTE. Em 29/08/2026 a rodada de Rio Grande caiu no passo 7 porque
o i9 REINICIOU no meio — `up 29 min`, boot às 10:00, última linha do log às
09:56. Captura, OCR e busca já estavam no banco: **2 h de trabalho**. Não havia
como retomar do 7 sem refazer tudo.

O GATE FICA NO ATO, NÃO NO CABEÇALHO, e a primeira versão errou justamente
nisso: eu pus `if not _pular_etapa(n):` na frente de cada `_etapa(n, ...)`.
Aquilo protege a LINHA DO CABEÇALHO — as chamadas seguintes continuam no mesmo
recuo e rodam igual. Guardar o corpo exigiria reindentar sete blocos, o que é
convite a erro.

E O GATE POR FUNÇÃO SÓ COBRE QUEM PASSA POR ELA. Na segunda versão protegi
`_rodar`, `_tolerante` e `_tolerante_i9` — e o passo 4 escapou, porque ele chama
`i9.rodar` DIRETO. O cabeçalho dizia "PULADA" e a captura rodava assim mesmo.

O CUSTO DAQUELE ESCAPE foi real e apareceu uma hora depois: a captura órfã ficou
no i9 segurando a porta 8766, e a rodada seguinte (Santa Maria) morreu com
"A porta 8766 já está em uso" — 0 de 3.234 tiles.
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

ALVO = os.path.join(RAIZ, "minerar_tudo.py")


def _codigo():
    """Sem comentário: asserção que lê comentário concorda com a prosa."""
    import tokenize
    linhas = io.open(ALVO, encoding="utf-8").read().splitlines(keepends=True)
    with tokenize.open(ALVO) as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type != tokenize.COMMENT:
                continue
            i, a, b = tok.start[0] - 1, tok.start[1], tok.end[1]
            linhas[i] = linhas[i][:a] + " " * (b - a) + linhas[i][b:]
    return "".join(linhas)


def test_o_gate_existe_e_tem_flag():
    py = _codigo()
    assert '"--de-etapa"' in py, "a flag de retomada sumiu"
    assert "def _etapa_pulada(" in py, "o gate sumiu"
    assert "_ETAPA_ATUAL = n" in py, \
        "`_etapa` deixou de registrar em que etapa estamos, e o gate cega"


def test_todo_ato_caro_passa_pelo_gate():
    """NENHUM ato caro pode ficar de fora: uma etapa que escapa gasta horas de
    captura.

    O QUE ESTE TESTE COBRAVA ANTES, e por que mudou. Ele exigia que toda chamada
    a `i9.rodar` estivesse sob a guarda — porque o passo 4 chamava aquela função
    DIRETO e atravessou o `--de-etapa` na primeira versão: o cabeçalho dizia
    "PULADA" e a captura rodava assim mesmo. O escape custou caro uma hora
    depois, quando a captura órfã segurou a porta 8766 e matou a rodada seguinte
    com 0 de 3.234 tiles.

    Em 30/08/2026 a camada de SSH saiu inteira — o sistema roda no servidor, e
    `i9.rodar` não existe mais. O teste passou a cobrar as duas coisas que
    sobrevivem à mudança: que as portas de entrada guardem, e que **nenhuma
    outra porta apareça** sem guarda.
    """
    py = _codigo()

    # as portas de entrada guardam — na propria linha, ou delegando a uma que
    # guarda. `_tolerante_i9` e hoje um delegador de uma linha: exigir o gate
    # DENTRO dele seria exigir a guarda duas vezes no mesmo caminho.
    for fn in ("_rodar", "_tolerante", "_tolerante_i9"):
        i = py.index("def %s(" % fn)
        corpo = py[i:i + 1800]
        corpo = corpo[:corpo.index("\ndef ", 10)] if "\ndef " in corpo[10:] else corpo
        propria = "_etapa_pulada()" in corpo
        delega = any(("return %s(" % g) in corpo
                     for g in ("_rodar", "_tolerante") if g != fn)
        assert propria or delega, \
            "%s roda mesmo com a etapa pulada: nao guarda nem delega" % fn

    # e a camada de SSH não voltou: ela era a porta que escapava
    assert "i9.rodar" not in py and "import i9" not in py, (
        "a camada de SSH voltou. Ela era uma porta de entrada FORA das três "
        "guardadas, e foi por ela que a captura atravessou o `--de-etapa`")

    # nenhum subprocess solto: todo disparo passa por uma das três
    import re as _re
    soltos = []
    for m in _re.finditer(r"subprocess\.(run|Popen|call|check_output)\(", py):
        ini = py.rfind("\n", 0, m.start()) + 1
        # `_rodar` e `_tolerante` PODEM chamar subprocess: são elas as guardas
        trecho = py[max(0, m.start() - 2000):m.start()]
        dono = trecho.rfind("\ndef ")
        nome = py[max(0, m.start() - 2000) + dono:][5:40].split("(")[0] if dono >= 0 else "?"
        if nome.strip() not in ("_rodar", "_tolerante", "_tolerante_i9"):
            soltos.append((nome.strip(), py[ini:py.find("\n", m.start())].strip()[:60]))
    assert not soltos, (
        "há disparo de processo fora das funções guardadas — a etapa pulada "
        "ainda executa: %s" % soltos)


def test_o_gate_bloqueia_e_solta_no_lugar_certo():
    """Não basta existir: tem de barrar antes e soltar depois."""
    import minerar_tudo as m
    de, atual = m._DE_ETAPA, m._ETAPA_ATUAL
    try:
        m._DE_ETAPA = 7
        for n in (1, 3, 4, 5, 6):
            m._ETAPA_ATUAL = n
            assert m._etapa_pulada(), "etapa %d devia ser pulada" % n
            assert m._rodar(["cmd", "/c", "exit", "9"]) == 0, \
                "a etapa %d pulada ainda executou o comando" % n
        for n in (7, 8, 9):
            m._ETAPA_ATUAL = n
            assert not m._etapa_pulada(), "etapa %d NÃO devia ser pulada" % n
    finally:
        m._DE_ETAPA, m._ETAPA_ATUAL = de, atual
