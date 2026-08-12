"""Configuração do pytest — torna a suíte descobrível por um CI padrão.

`pytest -q` retornava `no tests ran` (exit 5): `test_unit.py` e `test_rcc.py`
são scripts com `assert` no nível do módulo, um estilo que roda por invocação
manual e é invisível para coleta. Testes que só rodam quando alguém lembra de
rodá-los não protegem ninguém.

A conversão de ~50 asserts para funções seria uma reescrita grande e arriscada
no mesmo lote das correções P0. A ponte: os dois arquivos deixam de ser
coletados como módulos (executá-los no import quebra a coleta ao primeiro
gate) e passam a ser executados como SUBPROCESSO por `test_legado.py`, onde
falham como teste normal e o stdout vira mensagem de erro.

Converter os legados em funções fica como dívida declarada, não silenciosa.
"""

collect_ignore = ['test_unit.py', 'test_rcc.py', 'gen_sintetico.py',
                  'cnefe_fixture.py']

# ── ISOLAMENTO DE REDE ────────────────────────────────────────────────────
# A suíte tocava o FTP do IBGE. Isso a torna (a) lenta, (b) dependente de um
# servidor de terceiro para dizer se o CÓDIGO está certo e (c) capaz de passar
# por um motivo errado — cache preenchido por um download real esconde o
# defeito de quem não sabe adquirir. Duas camadas, porque parte da suíte roda o
# emissor em SUBPROCESSO:
#   1. `RADAR_OFFLINE` no ambiente — herdado por todo filho.
#   2. bloqueio de `socket.socket.connect` no processo do pytest.
import os as _os                                                    # noqa: E402
import socket as _socket                                            # noqa: E402

import pytest                                                       # noqa: E402

_os.environ['RADAR_OFFLINE'] = '1'

# Nada de tupla em `startswith`: uma entrada '' faria QUALQUER host casar e o
# bloqueio viraria enfeite — a classe de defeito que esta suíte existe para
# pegar.
_LOCAIS = ('127.', '::1', 'localhost', '0.0.0.0')
_CONNECT_ORIGINAL = _socket.socket.connect


class RedeProibidaNoTeste(RuntimeError):
    """Um teste tentou sair para a rede. Isso é o defeito, não o ambiente."""


def _local(host) -> bool:
    h = str(host or '')
    return bool(h) and any(h.startswith(p) for p in _LOCAIS)


def pytest_configure(config):
    def _connect(self, endereco, *a, **kw):
        host = endereco[0] if isinstance(endereco, (tuple, list)) else endereco
        if self.family in (_socket.AF_INET, _socket.AF_INET6) \
                and not _local(host):
            raise RedeProibidaNoTeste(
                f'conexao para {host} bloqueada: a suite tem de ser '
                'deterministica e offline. Use `cnefe_fixture.cache_set_offline`.')
        return _CONNECT_ORIGINAL(self, endereco, *a, **kw)

    _socket.socket.connect = _connect


def pytest_unconfigure(config):
    _socket.socket.connect = _CONNECT_ORIGINAL


@pytest.fixture(autouse=True)
def _offline_em_todo_teste(monkeypatch):
    """Reafirma a variável a cada teste: um teste que a apague não contamina
    os seguintes."""
    monkeypatch.setenv('RADAR_OFFLINE', '1')
