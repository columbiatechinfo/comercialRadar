# -*- coding: utf-8 -*-
"""Sobe os bytes de imagem do Postgres local para o Storage da pilha no i9.

CUMPRIDO E APOSENTADO — 13/08/2026. Este script leu a coluna `dados` do Postgres
do notebook, e esse banco não existe mais: foram 71.917 imagens conferidas uma a
uma contra o Storage antes do `DROP DATABASE`. Rodá-lo hoje só produz erro de
conexão. Fica no repositório como registro de COMO a migração foi feita, não como
ferramenta — quem gravar imagem nova usa `imagens.gravar_streetview` /
`imagens.gravar_foto`, que já escrevem direto no Storage.

O desenho original, para quem precisar repetir a manobra em outra ferramenta:

São 72 mil objetos e 6,3 GB. Duas coisas mandam no desenho:

1. **Retomável.** Linha que já tem `storage_path` no i9 é pulada. Uma queda no
   meio custa o que faltava, não o que já passou — a migração das tabelas já
   provou o valor disso hoje.
2. **Concorrente, mas com teto.** O gargalo é a rede (2,5 MB/s medidos), não o
   servidor. Concorrência esconde a latência por objeto; passar disso só enche
   fila e atrapalha o resto que roda no i9.

Não apaga nada da origem. A coluna `dados` no notebook continua intacta até a
fase 3, que é a única irreversível.
"""
import os, sys, io, time, threading, queue, urllib.request, urllib.error

sys.path.insert(0, r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
os.chdir(r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
import config  # noqa: F401  — carrega o .env
import psycopg2

GATEWAY = "http://100.115.117.49:8000"
BUCKET  = "comercialradar"
WORKERS = 6
LOTE    = 400

def _env(k, p=""):
    return (os.environ.get(k) or p).strip()

def conectar_local():
    return psycopg2.connect(host="localhost", port=_env("POSTGRES_PORT", "5432"),
                            user=_env("POSTGRES_USER", "postgres"),
                            password=_env("POSTGRES_PASSWORD"),
                            dbname=_env("POSTGRES_DB", "comercialradar"))

def conectar_i9():
    return psycopg2.connect(host=_env("I9_POSTGRES_HOST"), port=_env("I9_POSTGRES_PORT", "5444"),
                            user=_env("I9_POSTGRES_USER", "comercialradar_worker"),
                            password=_env("I9_POSTGRES_PASSWORD"),
                            dbname=_env("I9_POSTGRES_DB", "postgres"),
                            options="-c search_path=comercialradar,public", connect_timeout=20)

SERVICE = _env("SUPABASE_SERVICE_ROLE_KEY")
if not SERVICE:
    sys.exit("falta SUPABASE_SERVICE_ROLE_KEY no .env")

def enviar(caminho: str, dados: bytes, tipo: str) -> bool:
    """PUT com upsert: reenviar o mesmo objeto não duplica nem falha."""
    req = urllib.request.Request(
        f"{GATEWAY}/storage/v1/object/{BUCKET}/{caminho}", data=dados, method="POST",
        headers={"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}",
                 "Content-Type": tipo or "image/jpeg", "x-upsert": "true"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        # 409 = objeto já existe; com x-upsert não deveria acontecer, mas se
        # acontecer é sucesso do ponto de vista de "o byte está lá".
        if e.code == 409:
            return True
        print(f"    ! {caminho}: HTTP {e.code} {e.read()[:90]!r}", flush=True)
        return False
    except Exception as e:
        print(f"    ! {caminho}: {type(e).__name__}", flush=True)
        return False

def migrar(tabela: str, prefixo: str, coluna_tipo: str | None):
    loc, i9 = conectar_local(), conectar_i9()
    with i9.cursor() as c:
        c.execute(f"select count(*) from {tabela} where storage_path is not null")
        feitos = c.fetchone()[0]
        c.execute(f"select count(*) from {tabela}")
        total = c.fetchone()[0]
    print(f"\n=== {tabela}: {feitos:,} de {total:,} já no Storage ===", flush=True)
    if feitos >= total:
        loc.close(); i9.close(); return

    with i9.cursor() as c:
        c.execute(f"select id from {tabela} where storage_path is null order by id")
        pendentes = [r[0] for r in c.fetchall()]

    fila, resultados = queue.Queue(maxsize=WORKERS * 2), queue.Queue()
    def trabalhar():
        while True:
            item = fila.get()
            if item is None:
                fila.task_done(); return
            id_, dados, tipo = item
            caminho = f"{prefixo}/{id_ % 100:02d}/{id_}.jpg"
            resultados.put((id_, caminho) if enviar(caminho, dados, tipo) else (id_, None))
            fila.task_done()

    fios = [threading.Thread(target=trabalhar, daemon=True) for _ in range(WORKERS)]
    for f in fios: f.start()

    ini, ok, falhas, bytes_env = time.time(), 0, 0, 0
    for i in range(0, len(pendentes), LOTE):
        ids = pendentes[i:i + LOTE]
        sel = f"select id, dados{(', ' + coluna_tipo) if coluna_tipo else ''} from {tabela} where id = any(%s) and dados is not null"
        with loc.cursor() as c:
            c.execute(sel, (ids,))
            linhas = c.fetchall()
        for r in linhas:
            fila.put((r[0], bytes(r[1]), (r[2] if coluna_tipo else None) or "image/jpeg"))
            bytes_env += len(r[1])
        fila.join()

        feitos_lote = []
        while not resultados.empty():
            id_, caminho = resultados.get()
            if caminho: feitos_lote.append((caminho, id_)); ok += 1
            else: falhas += 1
        if feitos_lote:
            with i9.cursor() as w:
                w.executemany(f"update {tabela} set storage_path=%s where id=%s", feitos_lote)
            i9.commit()

        dt = time.time() - ini
        mbs = bytes_env / 1048576 / dt if dt else 0
        resta = (len(pendentes) - ok - falhas) / (ok / dt) / 60 if ok and dt else 0
        print(f"  {ok:,}/{len(pendentes):,}  falhas={falhas}  {mbs:.1f} MB/s  faltam ~{resta:.0f} min",
              end="\r", flush=True)

    for _ in fios: fila.put(None)
    print(f"\n  {tabela}: {ok:,} enviados, {falhas} falhas", flush=True)
    loc.close(); i9.close()

migrar("streetview_imgs", "fachada", None)
migrar("images_urls", "foto", "content_type")
print("\n=== fim do envio ===", flush=True)
