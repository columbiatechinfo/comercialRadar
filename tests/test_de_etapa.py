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
    captura e — pior — deixa processo órfão segurando porta no i9.

    A VERIFICAÇÃO OLHA A FUNÇÃO QUE CONTÉM a chamada, e não a linha. A primeira
    versão exigia o gate na própria linha e reprovou o `i9.rodar` de dentro do
    `_tolerante_i9`, que já é guardado no topo da função — falso positivo meu.
    """
    import ast
    fonte = _codigo()
    arv = ast.parse(fonte)

    # as três portas de entrada guardam no topo
    for fn in ("_rodar", "_tolerante", "_tolerante_i9"):
        i = fonte.index("def %s(" % fn)
        assert "_etapa_pulada()" in fonte[i:i + 400], \
            "%s roda mesmo com a etapa pulada" % fn

    # e toda chamada a `i9.rodar` está sob alguma dessas guardas
    def guardada(no):
        """A função que contém esta linha menciona o gate?"""
        for f in ast.walk(arv):
            if not isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if f.lineno <= no.lineno <= (f.end_lineno or f.lineno):
                trecho = "\n".join(fonte.splitlines()[f.lineno - 1:f.end_lineno])
                if "_etapa_pulada()" in trecho:
                    return True
        return False

    achou = 0
    for no in ast.walk(arv):
        if not isinstance(no, ast.Call):
            continue
        f = no.func
        if not (isinstance(f, ast.Attribute) and f.attr == "rodar"
                and isinstance(f.value, ast.Name) and f.value.id == "i9"):
            continue
        achou += 1
        assert guardada(no), (
            "há um `i9.rodar(` na linha %d fora de qualquer guarda: a etapa "
            "pulada dispara trabalho no i9 e deixa processo órfão — foi assim "
            "que a captura de Rio Grande travou a porta 8766 de Santa Maria"
            % no.lineno)
    assert achou >= 2, "as chamadas a `i9.rodar` sumiram; o teste ficou cego"


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
