# -*- coding: utf-8 -*-
"""Nenhum arquivo usa um nome que não existe naquele ponto.

POR QUE ESTE ARQUIVO EXISTE

Em 27/08/2026 um conserto meu na auto-cura do `descobrir_maps` apagou, junto com
a linha que devia sair, o `sess = await _abrir()` que abria a sessão antes do
laço. Como `sess` passou a ser atribuído SÓ dentro do laço, o Python o tratou
como local, e a primeira leitura estourou:

    UnboundLocalError: cannot access local variable 'sess'

O passo 5 morreu na run inteira. E o pior: os dois testes que eu tinha escrito
para aquele conserto PASSARAM — porque procuravam TEXTO no fonte (`MAX_CURAS`,
`mark_cooldown`, `acquire_blocking`). Todos os três estavam lá. Teste que lê
texto não vê o que o interpretador vê.

O `py_compile` também não pega: a sintaxe está correta. O que pega é a análise
de escopo, e ela custa milissegundos:

    descobrir_maps.py:276:56: undefined name 'sess'

Este arquivo roda essa análise sobre todo o repositório. Ele NÃO julga estilo —
`import` sem uso e f-string sem `{}` ficam de fora de propósito, porque não
quebram nada em execução e transformariam o teste em ruído que se aprende a
ignorar. Só entram as duas classes que derrubam o processo quando a linha roda.
"""
import io
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# As duas mensagens do pyflakes que significam "esta linha estoura ao rodar".
FATAIS = ("undefined name", "referenced before assignment")


def _arquivos():
    for pasta in (RAIZ, os.path.join(RAIZ, "tests")):
        for nome in sorted(os.listdir(pasta)):
            if nome.endswith(".py"):
                yield os.path.join(pasta, nome)


def test_nenhum_nome_usado_antes_de_existir():
    """A classe de defeito que passou por cima de dois testes de texto."""
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("pyflakes não instalado")

    p = subprocess.run([sys.executable, "-m", "pyflakes", *_arquivos()],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=RAIZ)
    ruins = [l for l in (p.stdout or "").splitlines()
             if any(f in l for f in FATAIS)]
    assert not ruins, ("nome usado antes de existir — estoura ao rodar:\n  "
                       + "\n  ".join(ruins))


def test_a_analise_pega_o_defeito_que_motivou_o_arquivo():
    """Guarda contra teste que passa por não estar olhando nada.

    Reproduz o `sess` exatamente como ficou: atribuído dentro do laço, lido
    antes. Se um dia a análise deixar de acusar isto, o arquivo inteiro vira
    decoração e é melhor descobrir aqui."""
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("pyflakes não instalado")

    import tempfile
    molde = (
        "async def _worker():\n"
        "    async def _abrir():\n"
        "        return 1\n"
        "    while True:\n"
        "        usar(sess)\n"
        "        sess = await _abrir()\n"
    )
    d = tempfile.mkdtemp()
    alvo = os.path.join(d, "reproducao.py")
    io.open(alvo, "w", encoding="utf-8").write(molde)
    p = subprocess.run([sys.executable, "-m", "pyflakes", alvo],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert any(f in (p.stdout or "") for f in FATAIS), \
        f"a análise deixou de ver o defeito do `sess`: {p.stdout!r}"
