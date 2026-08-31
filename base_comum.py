# -*- coding: utf-8 -*-
"""
base_comum.py — motor compartilhado dos módulos de bases externas (CNPJ, CNEFE, ANEEL).

Puramente ETL: baixa arquivo → descompacta → **COPY direto** pro Postgres do
comercialRadar (sem parsear linha a linha em Python — é o único jeito de aguentar
dezenas de GB). Idempotente: cada arquivo carregado é registrado em `fonte_arquivos`
e pulado numa re-execução.

Não mistura com os POIs — grava só nas tabelas próprias das bases (rf_*, ibge_*, aneel_*).
Ver [[ferramentas-separadas]] e DOCUMENTACAO.md.
"""
import io
import os
import ssl
import time
import base64
import zipfile
import tempfile
import urllib.request
from pathlib import Path

import realtime_ingest

TMP = Path(tempfile.gettempdir()) / "bases_externas"
TMP.mkdir(parents=True, exist_ok=True)
_SSL = ssl.create_default_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def conectar():
    """Banco do PRODUTO: POIs, cadastro do cliente, anotações, coletivas."""
    return realtime_ingest.conectar()


def conectar_referencia():
    """Banco de REFERÊNCIA: CNEFE, Receita Federal, OSM — 60 GB de base pública.

    Instância separada de propósito, e não capricho de organização: uma varredura
    na `ibge_cnefe` (111 milhões de linhas) disputaria o page cache com o Auth, o
    PostgREST e o Realtime da instância que atende usuário. É a regra da casa —
    analítico pesado não divide Postgres com quem serve requisição. Ver o
    ADR 0003.

    Consequência que o chamador precisa saber: **não existe JOIN entre os dois
    bancos.** Cruzar POI com CNEFE é consulta aqui, recorte, e junção no Python —
    que é como o `coletivas_importar.py` já faz, por casamento de coordenada.

    Enquanto `REF_POSTGRES_HOST` não estiver no `.env`, devolve a conexão do
    banco do produto. Isso mantém quem ainda não migrou funcionando sem tocar em
    nada — a migração de cada módulo é independente.
    """
    import psycopg2
    host = (os.environ.get("REF_POSTGRES_HOST") or "").strip()
    if not host:
        return conectar()
    return psycopg2.connect(
        host=host,
        port=os.environ.get("REF_POSTGRES_PORT", "5443"),
        user=os.environ.get("REF_POSTGRES_USER", "postgres"),
        password=os.environ.get("REF_POSTGRES_PASSWORD", ""),
        dbname=os.environ.get("REF_POSTGRES_DB", "referencia"),
        connect_timeout=int(os.environ.get("PG_CONNECT_TIMEOUT", "20")),
    )


# ── Controle de arquivos já carregados (idempotência) ───────────────────────────
def garantir_controle(conn):
    with conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fonte_arquivos (
                fonte        text NOT NULL,
                referencia   text NOT NULL,
                tabela       text,
                linhas       bigint,
                bytes        bigint,
                status       text DEFAULT 'ok',
                carregado_em timestamptz DEFAULT now(),
                PRIMARY KEY (fonte, referencia)
            )""")


def ja_carregado(conn, fonte: str, referencia: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM fonte_arquivos WHERE fonte=%s AND referencia=%s AND status='ok'",
                    (fonte, referencia))
        return cur.fetchone() is not None


def marcar(conn, fonte, referencia, tabela, linhas, bytes_):
    with conn, conn.cursor() as cur:
        # ON CONSTRAINT, e não a lista de colunas: esta função grava no banco do
        # produto, cuja chave é (id_empresa, fonte, referencia) desde a 0010, e no
        # de referência, que não tem empresa e mantém (fonte, referencia). O nome
        # da constraint é o mesmo nos dois, a lista de colunas não.
        cur.execute("""INSERT INTO fonte_arquivos (fonte, referencia, tabela, linhas, bytes)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT ON CONSTRAINT fonte_arquivos_pkey DO UPDATE
                         SET tabela=EXCLUDED.tabela, linhas=EXCLUDED.linhas,
                             bytes=EXCLUDED.bytes, status='ok', carregado_em=now()""",
                    (fonte, referencia, tabela, linhas, bytes_))


# ── Download com retomada (Range) e retry ───────────────────────────────────────
def baixar(url: str, destino: Path, auth: str = None, tentativas: int = 4) -> Path:
    """Baixa url→destino com retomada (HTTP Range) se o arquivo parcial existir."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    for t in range(tentativas):
        pos = destino.stat().st_size if destino.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        if auth:
            req.add_header("Authorization", "Basic " + auth)
        if pos:
            req.add_header("Range", f"bytes={pos}-")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_SSL) as r:
                modo = "ab" if (pos and r.status == 206) else "wb"
                if modo == "wb":
                    pos = 0
                with open(destino, modo) as f:
                    while True:
                        chunk = r.read(1 << 20)          # 1 MB
                        if not chunk:
                            break
                        f.write(chunk)
            return destino
        except Exception as e:
            if t == tentativas - 1:
                raise
            time.sleep(5 * (t + 1))                     # backoff


# ── COPY: streaming de um CSV (file-like binário) direto pro Postgres ───────────
def copy_csv(conn, tabela: str, fobj, delimiter=";", header=False,
             encoding="LATIN1", quote='"', colunas=None) -> int:
    """COPY tabela FROM STDIN lendo o CSV cru de fobj (sem parsear em Python).
    Retorna o nº de linhas inseridas. fobj deve entregar bytes."""
    cols = f" ({', '.join(colunas)})" if colunas else ""
    sql = (f"COPY {tabela}{cols} FROM STDIN WITH (FORMAT csv, DELIMITER '{delimiter}', "
           f"QUOTE '{quote}', ENCODING '{encoding}'"
           + (", HEADER true" if header else "") + ")")
    try:
        with conn.cursor() as cur:
            cur.copy_expert(sql, _FiltraNul(fobj))   # tira NUL 0x00 (a RFB às vezes traz)
            n = cur.rowcount                          # nº de linhas copiadas (sem scan)
        conn.commit()
        return n if (n is not None and n >= 0) else 0
    except Exception:
        conn.rollback()      # COPY é atômico: em erro, desfaz e libera a transação
        raise


class _FiltraNul:
    """Remove bytes NUL (0x00) do stream — o COPY do Postgres os rejeita e a base da
    RFB às vezes traz NUL solto no meio do CSV. Lê em blocos (copy_expert usa read(n))."""
    def __init__(self, f):
        self.f = f

    def read(self, n=-1):
        b = self.f.read(n)
        return b.replace(b"\x00", b"") if (b and b"\x00" in b) else b


def copy_de_zip(conn, tabela: str, zip_path: Path, **kw) -> int:
    """Abre o(s) CSV(s) de dentro de um .zip e COPY cada um pra tabela."""
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for nome in z.namelist():
            if nome.endswith("/"):
                continue
            with z.open(nome) as f:
                total += copy_csv(conn, tabela, f, **kw)
    return total


def limpar_tmp(*paths):
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass


def b64_token(token: str) -> str:
    return base64.b64encode(f"{token}:".encode()).decode()

def assumir_empresa(cur, nome: str) -> tuple:
    """Declara na SESSAO de quem este processo grava. Devolve `(id, nome)`.

    O QUE VIAJA AQUI E O UUID DO USUARIO DE SERVICO, e nao o da empresa.

    Ate 30/08/2026 era `set_config('request.jwt.claim.sub', <uuid da empresa>)`, e as
    policies liam aquela variavel direto. As politicas do `core` nao leem: elas
    chamam `core.empresa_atual()`, que faz
    `select id_empresa from core.tb_users where id = (select auth.uid())` — ou
    seja, partem de um USUARIO e descobrem a empresa. Passar o uuid da empresa
    ali daria `auth.uid()` apontando para um usuario que nao existe, e toda
    politica negaria: o processo rodaria inteiro e gravaria zero linha.

    Por isso cada empresa tem um usuario de servico (`pipeline@...`, nivel 1). E
    ele quem o lote assume. De quebra, `core.tb_auditoria` passa a registrar
    QUEM fez cada mudanca, em vez de nao registrar nada para o pipeline.

    `false` no terceiro argumento, e nao `true`: a variavel vale pela SESSAO
    inteira, nao por transacao. O pipeline abre uma conexao e roda milhares de
    transacoes nela; local a transacao morreria no primeiro commit e a
    segunda gravacao ja nasceria sem dono.
    """
    cur.execute("select id, name from core.tb_empresas "
                "where lower(name) = lower(%s) and ativa", (nome.strip(),))
    emp = cur.fetchone()
    if not emp:
        cur.execute("select name from core.tb_empresas where ativa order by name")
        raise SystemExit(f"empresa {nome!r} nao existe. Ativas: "
                         + ", ".join(x[0] for x in cur.fetchall()))

    cur.execute("select id from core.tb_users "
                "where id_empresa = %s and ativo and email like 'pipeline@%%' "
                "order by criado_em limit 1", (emp[0],))
    servico = cur.fetchone()
    if not servico:
        raise SystemExit(
            f"a empresa {emp[1]!r} nao tem usuario de servico.\n\n"
            f"  O lote nao tem login, entao ele assume um usuario de servico da\n"
            f"  empresa para que `core.empresa_atual()` saiba de quem e o que\n"
            f"  ele grava. Sem isso toda politica nega e a rodada grava zero\n"
            f"  linha — sem erro, que e o pior jeito de falhar.\n\n"
            f"  Criar um usuario `pipeline@...` de nivel 1 na empresa {emp[0]}.")

    cur.execute("select set_config('request.jwt.claim.sub', %s, false)",
                (str(servico[0]),))
    return emp[0], emp[1]
