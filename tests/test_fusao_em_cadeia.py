# -*- coding: utf-8 -*-
"""Numa cadeia de fusões, todo vínculo vai para o sobrevivente FINAL.

POR QUE ESTE ARQUIVO EXISTE

Medido em 27/08/2026, depois da mineração de Canoas: **6 vínculos ativos
apontando para POI já marcado `fundido`**. Na tela isso é ficha órfã — o ponto
não aparece no mapa (o `server.py` filtra fundidos) e o vínculo aponta para o
nada.

A causa foi o próprio lote que eu escrevi para acelerar a gravação. A versão
anterior gravava DENTRO do laço, uma fusão por vez, e a cadeia se resolvia pela
ordem de execução sem ninguém ter escrito código para isso:

    A absorve B   →  UPDATE move o vínculo de B para A
    C absorve A   →  UPDATE move o que estiver em A, o de B junto

O lote junta tudo num `UPDATE ... FROM (VALUES)`, e o Postgres casa cada linha
contra o estado ANTERIOR ao comando. A linha movida de B para A não é recasada
com `f.morre = A`; fica em A, que o comando seguinte marca como fundido.

O teste NÃO lê o fonte. Ele executa `aplicar()` contra um cursor falso que imita
a semântica do Postgres — inclusive a que causou o defeito: o casamento usa o
estado do início do comando. Um teste que aplicasse os VALUES em sequência
passaria com o código quebrado, que é justamente o engano a evitar.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cruzar_fontes as cf  # noqa: E402


class _CursorFalso:
    """Imita o `vinculo_poi` e o `pois` no que a fusão toca.

    `execute_values` casa contra uma CÓPIA do estado inicial — é essa semântica
    do Postgres que revela o defeito da cadeia."""

    def __init__(self, vinculos):
        self.vinculos = dict(vinculos)      # vinculo_id -> poi_id
        self.fundidos = set()

    def execute(self, sql, args=None):
        # A MARCA DE FUSÃO NÃO VEM MAIS POR AQUI, e ignorar isso deixou este
        # dublê cego. Ele reconhecia `status = 'fundido'` num `execute`; desde a
        # migração 0036 a fusão escreve `fundido_em`/`fundido_para` por um
        # `execute_values`, então `self.fundidos` ficava vazio e a asserção
        # sobre vínculos órfãos passava sem olhar nada. Ver `_execute_values`.
        pass

    def _values(self, blocos):
        antes = dict(self.vinculos)         # o estado do INÍCIO do comando
        for morre, vive, *_ in blocos:
            for vid, pid in antes.items():
                if pid == morre:
                    self.vinculos[vid] = vive


def _execute_values_falso(cur, sql, blocos, template=None, page_size=None):
    """A fusão escreve por DOIS `execute_values`: um move o vínculo, o outro
    marca o absorvido. Distinguir pelo SQL é o que mantém este teste vendo
    alguma coisa — a versão anterior só reconhecia `status = 'fundido'` num
    `execute`, e depois da migração 0036 passou a aprovar sem olhar nada."""
    if "update pois" in sql:
        cur.fundidos.update(m for m, *_ in blocos)
    else:
        cur._values(blocos)


def _poi(pid, place_id=None):
    return {"id": pid, "evid": 1, "place_id": place_id}


def _decisao(a, b, conf):
    return {"a": _poi(a), "b": _poi(b), "confianca": conf, "origem": "regra",
            "evidencia": {"motivos": ["teste"]}}


def _rodar(decisoes, vinculos, monkeypatch):
    import psycopg2.extras
    monkeypatch.setattr(psycopg2.extras, "execute_values", _execute_values_falso)
    cur = _CursorFalso(vinculos)

    class _Con:
        def cursor(self):
            return cur

    n = cf.aplicar(_Con(), decisoes)
    return cur, n


def test_o_vinculo_nao_para_em_poi_que_tambem_morreu(monkeypatch):
    """O defeito exato de 27/08/2026, com os papéis mínimos para reproduzi-lo.

    `_sobrevivente` desempata pelo menor id quando tudo mais empata, então o id
    menor vive. Duas decisões, e a segunda mata o sobrevivente da primeira:

        30 e 20 → vive 20, morre 30      (o vínculo de 30 vai para 20)
        20 e 10 → vive 10, morre 20      (20 morre; o vínculo de 30 fica nele)
    """
    cur, n = _rodar([_decisao(30, 20, 90), _decisao(20, 10, 80)],
                    {"v30": 30, "v20": 20, "v10": 10}, monkeypatch)

    orfaos = {v: p for v, p in cur.vinculos.items() if p in cur.fundidos}
    assert not orfaos, (
        f"vínculo parou em POI fundido: {orfaos} — na tela isso é ficha órfã")
    assert cur.vinculos == {"v30": 10, "v20": 10, "v10": 10}, \
        f"os três tinham de terminar no sobrevivente final: {cur.vinculos}"
    assert n == 2


def test_a_cadeia_longa_tambem_chega_ao_fim(monkeypatch):
    """Quatro POIs em fila. Um conserto que só olhasse um salto passaria no
    teste acima e falharia aqui."""
    cur, _ = _rodar([_decisao(40, 30, 90), _decisao(30, 20, 80),
                     _decisao(20, 10, 70)],
                    {f"v{p}": p for p in (40, 30, 20, 10)}, monkeypatch)
    assert set(cur.vinculos.values()) == {10}, \
        f"a cadeia parou no meio: {cur.vinculos}"


def test_o_par_ja_no_mesmo_POI_nao_vira_fusao(monkeypatch):
    """A lacuna `161 a aplicar → 157 gravados` do log é isto, e é correta: um
    par cujos dois lados já foram parar no mesmo POI não é fusão perdida."""
    cur, n = _rodar([_decisao(30, 10, 90), _decisao(20, 10, 80),
                     _decisao(30, 20, 70)],
                    {f"v{p}": p for p in (30, 20, 10)}, monkeypatch)
    assert n == 2, f"o par redundante virou fusão: {n}"
    assert set(cur.vinculos.values()) == {10}
