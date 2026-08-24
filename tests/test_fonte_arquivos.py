# -*- coding: utf-8 -*-
"""A contabilidade de procedencia nao pode derrubar a ingestao.

Dois defeitos reais, os dois descobertos DEPOIS do trabalho pesado ja feito:

  1. Reprocessar o mesmo municipio estourava por chave duplicada em
     `fonte_arquivos` — e como tudo roda numa transacao so, levava junto os
     24 mil CNPJs recem-calculados. 48 s de skill jogados fora por uma linha
     de registro.

  2. A chave era (fonte, referencia), sem empresa. Com RLS, a segunda empresa
     a processar o mesmo municipio nao ENXERGA a linha da primeira, entao
     `ja_carregado()` diz "pode carregar" e o INSERT bate numa duplicata
     invisivel. Dado publico e o mesmo para todo mundo; o dono do resultado e
     que muda.
"""
import os
import sys
import uuid

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402

FONTE = "teste-regressao"
SQL = """insert into fonte_arquivos (fonte, referencia, tabela, linhas, status)
         values (%s, %s, 'cnpj_tratado', %s, 'ok')
         on conflict on constraint fonte_arquivos_pkey do update
           set linhas = excluded.linhas, status = 'ok', carregado_em = now()"""


@pytest.fixture()
def con():
    try:
        c = bc.conectar()
    except Exception as e:
        pytest.skip(f"banco indisponivel: {type(e).__name__}")
    yield c
    c.rollback()
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("delete from fonte_arquivos where fonte = %s", (FONTE,))
    c.close()


def test_reprocessar_o_mesmo_municipio_nao_estoura(con):
    ref = f"municipio {uuid.uuid4().hex[:8]}"
    with con.cursor() as cur:
        cur.execute(SQL, (FONTE, ref, 400))
        cur.execute(SQL, (FONTE, ref, 24147))       # a segunda rodada
        cur.execute("""select linhas from fonte_arquivos
                        where fonte = %s and referencia = %s""", (FONTE, ref))
        assert cur.fetchone()[0] == 24147, "a segunda rodada nao atualizou a contagem"
    con.commit()


def test_duas_empresas_no_mesmo_municipio(con):
    """A prova da chave: a mesma referencia sob duas empresas convive."""
    ref = f"municipio {uuid.uuid4().hex[:8]}"
    with con.cursor() as cur:
        cur.execute("select id from tenants where ativo order by criado_em limit 2")
        empresas = [r[0] for r in cur.fetchall()]
        if len(empresas) < 2:
            pytest.skip("menos de duas empresas cadastradas")
        for tid in empresas:
            # A trigger carimba a empresa da sessao; aqui declaramos qual e.
            cur.execute("select set_config('app.tenant_id', %s, false)", (str(tid),))
            cur.execute(SQL, (FONTE, ref, 1000))
        cur.execute("""select count(*) from fonte_arquivos
                        where fonte = %s and referencia = %s""", (FONTE, ref))
        assert cur.fetchone()[0] == 2, (
            "a segunda empresa nao conseguiu registrar o mesmo municipio")
    con.commit()
