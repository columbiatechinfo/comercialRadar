"""Executa as suítes em estilo script como testes de verdade no pytest.

`test_unit.py` e `test_rcc.py` têm os asserts no nível do módulo. Rodam por
`python tests/test_unit.py`, e um CI baseado em `pytest` nunca os enxergou —
a suíte "de 49 testes" era invisível para automação.

Aqui cada uma vira um teste: roda como subprocesso, e o stdout completo entra
na mensagem de falha. Não é a solução final (converter para funções é), mas
fecha o buraco de CI sem reescrever 50 asserts no mesmo lote das correções P0.
"""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize('script', ['test_unit.py', 'test_rcc.py'])
def test_suite_legada(script):
    caminho = os.path.join(HERE, script)
    if not os.path.exists(caminho):
        pytest.skip(f'{script} ausente')
    r = subprocess.run([sys.executable, caminho], capture_output=True, text=True,
                       cwd=os.path.dirname(HERE))
    assert r.returncode == 0, (
        f'{script} falhou (exit {r.returncode}):\n'
        f'{r.stdout[-3000:]}\n{r.stderr[-2000:]}')
