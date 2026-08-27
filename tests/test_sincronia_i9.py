# -*- coding: utf-8 -*-
"""O que roda no i9 tem de ser o que está escrito aqui — inteiro.

A busca, a captura e o OCR rodam no i9. O `i9.py` manda para lá os arquivos que
divergem, comparando sha256. O que ele NÃO conhece, ele não manda — e o
relatório fica sinceramente errado: "os 15 arquivos já estão iguais".

ESTE DEFEITO JÁ ACONTECEU DUAS VEZES, com a mesma forma e sintomas diferentes:

    A lista tinha `src/capture-cli.ts` (a porta de entrada) e não
    `src/capture.ts` (quem abre o navegador). O `--disable-http2` foi corrigido,
    sincronizado, e a captura falhou igual. Consertado varrendo `src/`.

    27/08/2026: a lista tinha `search_pois_v2.py` e não `config.py`. O teto de
    espera do botão "Próximo" subiu de 9 s para 25 s com medição; a run seguinte
    continuou falhando. A mensagem de erro entregou o motivo sem querer — dizia
    "não pintou em 12s", que é 9.000 + 2.500. O código novo chegou; o NÚMERO
    não.

O SINTOMA É O PIOR QUE EXISTE: o conserto parece não ter funcionado. Perde-se o
tempo relendo a correção certa, procurando defeito onde não há, quando o
problema é que ela não chegou na máquina que trabalha.

Por isso o teste abaixo não confere uma lista de nomes — confere o FECHO: se um
arquivo sincronizado importa outro módulo do projeto, esse outro também vai.
Assim a próxima dependência nova é pega sozinha, sem depender de alguém lembrar.
"""
import ast
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import i9  # noqa: E402


def _importa_locais(caminho):
    """Módulos do próprio projeto que este arquivo importa (raiz do repo)."""
    try:
        arvore = ast.parse(io.open(caminho, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return set()
    nomes = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for a in no.names:
                nomes.add(a.name.split(".")[0])
        elif isinstance(no, ast.ImportFrom):
            if no.module and no.level == 0:
                nomes.add(no.module.split(".")[0])
    # só o que é arquivo .py na raiz do repositório
    return {n for n in nomes if os.path.isfile(os.path.join(RAIZ, n + ".py"))}


def test_config_vai_junto():
    """O caso concreto de 27/08/2026, cravado para não voltar.

    `config.py` guarda todo teto, limite e caminho, e é lido pela busca, pelo
    navegador e pelo pool de proxies. Sem ele no i9, mexer em constante aqui
    não muda nada onde o trabalho acontece."""
    assert "config.py" in i9.ARQUIVOS, (
        "config.py saiu da sincronização — ajustar teto no notebook deixaria "
        "de surtir efeito no i9, e o sintoma seria 'o conserto não funcionou'")


def test_o_fecho_das_dependencias_esta_completo():
    """A REGRA GERAL, e o motivo deste arquivo existir.

    Não adianta listar os módulos de entrada: quem roda lá importa outros, e um
    módulo importado que fica para trás é um defeito corrigido que volta.
    """
    faltando = {}
    for arq in i9.ARQUIVOS:
        if not arq.endswith(".py"):
            continue
        caminho = os.path.join(RAIZ, arq)
        if not os.path.isfile(caminho):
            continue
        for dep in _importa_locais(caminho):
            if dep + ".py" not in i9.ARQUIVOS:
                faltando.setdefault(dep + ".py", []).append(arq)

    assert not faltando, (
        "estes módulos são importados por arquivos que rodam no i9 mas NÃO são "
        "sincronizados — o i9 rodaria a versão velha deles:\n" +
        "\n".join(f"   {d}  ← importado por {', '.join(sorted(quem))}"
                  for d, quem in sorted(faltando.items())))


def test_a_sincronizacao_avisa_quando_nao_consegue_falar():
    """Se o SSH cai, o pior resultado é seguir em silêncio: a etapa rodaria com
    código velho e ninguém saberia. Ela tem de dizer."""
    s = io.open(os.path.join(RAIZ, "i9.py"), encoding="utf-8").read()
    i = s.index("def sincronizar(")
    corpo = s[i:i + 1500]
    assert "código antigo" in corpo, \
        "a sincronização deixou de avisar que pode estar rodando código velho"


def test_os_ts_continuam_sendo_varridos():
    """A lição da primeira vez (capture.ts) não pode ser desfeita: `src/` é
    varrido, não digitado."""
    s = io.open(os.path.join(RAIZ, "i9.py"), encoding="utf-8").read()
    assert "rglob" in s, "os .ts voltaram a depender de lista digitada à mão"
