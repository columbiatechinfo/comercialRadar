# -*- coding: utf-8 -*-
"""
imagens.py — de onde vêm os bytes de uma imagem.

Desde 12/08/2026 os bytes moram no **Storage** da pilha, não no Postgres. Byte
de imagem dentro do banco faz todo backup, toda restauração e toda replicação
carregarem 6,3 GB a mais, para sempre — e imagem é justamente o dado que não
precisa de transação.

O banco guarda o caminho (`storage_path`); os bytes vêm por HTTP.

**Queda para o banco, de propósito.** Enquanto a coluna `dados` existir, uma
linha sem `storage_path` ainda é servida a partir dela. É isso que permite a
troca ser feita em fases: os 9 arquivos que leem imagem migram um a um, e
nenhum deles quebra no dia em que o outro ainda não migrou. Quando a coluna for
apagada (fase 3), a queda simplesmente deixa de encontrar linha e o caminho
único passa a ser o Storage.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

BUCKET = os.environ.get("SUPABASE_BUCKET", "comercialradar")


def _gateway() -> str:
    return (os.environ.get("SUPABASE_URL")
            or f"http://{os.environ.get('I9_POSTGRES_HOST', '100.115.117.49')}:8000").rstrip("/")


def _chave() -> str:
    # `service_role` ignora a RLS do Storage. É a chave de PROCESSO DE SERVIDOR:
    # o pipeline e a API a usam, o navegador nunca.
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def baixar(caminho: str, timeout: int = 60) -> bytes | None:
    """Bytes de um objeto do Storage. None se não existir ou a chave faltar."""
    chave = _chave()
    if not caminho or not chave:
        return None
    req = urllib.request.Request(
        f"{_gateway()}/storage/v1/object/{BUCKET}/{caminho}",
        headers={"apikey": chave, "Authorization": f"Bearer {chave}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def _de_linha(caminho: str | None, dados) -> bytes | None:
    if caminho:
        b = baixar(caminho)
        if b:
            return b
    return bytes(dados) if dados else None


def _tem_coluna(con, tabela: str, coluna: str) -> bool:
    """A coluna `dados` some na fase 3; consultá-la depois disso seria erro."""
    with con.cursor() as cur:
        cur.execute("""select 1 from information_schema.columns
                        where table_name=%s and column_name=%s limit 1""", (tabela, coluna))
        return cur.fetchone() is not None


def _buscar(con, tabela: str, onde: str, args: tuple, limite: int | None = None) -> list:
    """Lista de bytes das linhas que casarem, na ordem da consulta."""
    campos = "storage_path" + (", dados" if _tem_coluna(con, tabela, "dados") else "")
    sql = f"select {campos} from {tabela} where {onde}"
    if limite:
        sql += f" limit {int(limite)}"
    with con.cursor() as cur:
        cur.execute(sql, args)
        linhas = cur.fetchall()
    out = []
    for r in linhas:
        b = _de_linha(r[0], r[1] if len(r) > 1 else None)
        if b:
            out.append(b)
    return out


def streetview_por_id(sv_id: int, con) -> bytes | None:
    r = _buscar(con, "streetview_imgs", "id = %s", (sv_id,))
    return r[0] if r else None


def streetview_do_poi(poi_id: int, con, angulo: str = "facade", limite: int = 1) -> list:
    return _buscar(con, "streetview_imgs",
                   "poi_id = %s and angulo = %s order by id", (poi_id, angulo), limite)


def fotos_do_poi(poi_id: int, con, limite: int = 4) -> list:
    return _buscar(con, "images_urls", "poi_id = %s order by id", (poi_id,), limite)
