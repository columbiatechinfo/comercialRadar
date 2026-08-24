# -*- coding: utf-8 -*-
"""Apoio comum da suíte.

`USUARIOS-INICIAIS.csv` É LIDO POR SEIS ARQUIVOS DE TESTE, e em 24/08/2026 os
seis quebraram de uma vez:

    KeyError: 'email'    × 39 testes

O arquivo tinha sido salvo com **ponto e vírgula** no lugar da vírgula — é o que
o Excel em português faz, sozinho, quando alguém o abre e salva. Com
`DictReader` no separador padrão, a linha inteira vira UMA coluna chamada
`nome;email;nivel;empresa;senha`, e `l["email"]` estoura.

O erro não diz "separador errado". Diz que falta a coluna `email` num arquivo
onde ela está visivelmente presente — e a suíte inteira cai num ponto que não
tem nada a ver com a causa.

`ler_credenciais` detecta o separador em vez de fixá-lo. Não é tolerância a
sujeira: é reconhecer que este arquivo é editado por PESSOA, em Excel, e que o
formato dele vai oscilar de novo.
"""
import csv
import os

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_USUARIOS = os.path.join(RAIZ, "USUARIOS-INICIAIS.csv")


def ler_credenciais(caminho: str = CSV_USUARIOS) -> dict:
    """`{email: linha}` do CSV de usuários, com o separador detectado.

    Devolve `{}` quando o arquivo não existe — quem chama decide se pula.
    """
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        cabecalho = f.readline()
        # Conta em vez de confiar no `Sniffer`: o cabeçalho tem cinco campos e
        # nenhum deles contém vírgula ou ponto e vírgula, então contar decide
        # sem ambiguidade. O `Sniffer` erra em arquivo de uma coluna só.
        sep = ";" if cabecalho.count(";") > cabecalho.count(",") else ","
        f.seek(0)
        linhas = list(csv.DictReader(f, delimiter=sep))
    if linhas and "email" not in linhas[0]:
        raise AssertionError(
            f"{caminho} não tem coluna 'email' nem com separador '{sep}'. "
            f"Colunas lidas: {list(linhas[0])}")
    return {l["email"]: l for l in linhas if l.get("email")}


@pytest.fixture(scope="session")
def credenciais():
    """As credenciais dos usuários iniciais, ou `skip` se não houver arquivo."""
    cred = ler_credenciais()
    if not cred:
        pytest.skip("USUARIOS-INICIAIS.csv ausente ou vazio")
    return cred
