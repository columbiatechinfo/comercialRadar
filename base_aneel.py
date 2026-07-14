# -*- coding: utf-8 -*-
"""
base_aneel.py — Instalações de energia elétrica (ANEEL, BDGD via portal CKAN).

Unidades consumidoras georreferenciadas da Base de Dados Geográfica da Distribuidora:
UCBT (baixa tensão — o grosso das instalações), UCMT (média), UCAT (alta). Traz
município (IBGE), classe/subclasse (residencial/comercial/industrial/rural), carga,
tensão, distribuidora. Cada camada vira uma tabela aneel_<camada> (colunas do header).

USO:
  .venv\\Scripts\\python base_aneel.py                  # baixa UCAT/UCMT/UCBT
  .venv\\Scripts\\python base_aneel.py --so UCAT        # só uma camada
  .venv\\Scripts\\python base_aneel.py --recriar
"""
import re
import ssl
import json
import zipfile
import argparse
import unicodedata
import urllib.request

import base_comum as bc

CKAN = ("https://dadosabertos.aneel.gov.br/api/3/action/package_show"
        "?id=base-de-dados-geografica-da-distribuidora-bdgd")
_SSL = ssl.create_default_context()


def _norm(c: str) -> str:
    c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
    c = re.sub(r"[^a-zA-Z0-9]+", "_", c).strip("_").lower()
    return c or "col"


def recursos_uc() -> list:
    """Consulta a API CKAN e devolve os recursos de unidades consumidoras (UC*)."""
    req = urllib.request.Request(CKAN, headers={"User-Agent": bc.UA})
    with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
        d = json.loads(r.read())
    out = []
    for res in d.get("result", {}).get("resources", []):
        nome = (res.get("name") or "").upper()
        fmt = (res.get("format") or "").upper()
        if nome.startswith("UC") and fmt in ("CSV", "ZIP") and res.get("url"):
            out.append({"nome": res["name"], "url": res["url"], "fmt": fmt})
    return out


def _sniff(primeira_linha_bytes):
    """Detecta encoding e delimitador da 1ª linha."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            txt = primeira_linha_bytes.decode(enc)
            break
        except Exception:
            txt = primeira_linha_bytes.decode("latin-1", "ignore"); enc = "latin-1"
    delim = ";" if txt.count(";") >= txt.count(",") else ","
    pg_enc = "UTF8" if enc.startswith("utf") else "LATIN1"
    return pg_enc, delim, txt.rstrip("\r\n")


def _abrir_csv_do(destino, fmt):
    """Retorna (zipfile|None, nome_csv, abre()) — abstrai csv solto vs dentro de zip."""
    if fmt == "ZIP":
        z = zipfile.ZipFile(destino)
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        return z, csvs
    return None, [destino.name]


def _colunas(head, delim):
    cols = [_norm(c) for c in head.split(delim)]
    vist, saida = {}, []
    for c in cols:
        vist[c] = vist.get(c, 0) + 1
        saida.append(c if vist[c] == 1 else f"{c}_{vist[c]}")
    return saida


def _processar(conn, ref, tabela, destino, fmt):
    """Detecta header, cria a tabela e faz COPY. Comum ao download e ao --arquivo."""
    tam = destino.stat().st_size
    if fmt == "ZIP":
        with zipfile.ZipFile(destino) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                print("      sem CSV no zip"); return
            with z.open(csvs[0]) as f:
                pg_enc, delim, head = _sniff(f.readline())
    else:
        with open(destino, "rb") as f:
            pg_enc, delim, head = _sniff(f.readline())
    cols = _colunas(head, delim)
    with conn, conn.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {tabela} (%s)"
                    % ", ".join(f"{c} text" for c in cols))
    print(f"      COPY → {tabela} ({tam/1e6:.0f} MB, {len(cols)} cols, {pg_enc}/{delim!r})…", flush=True)
    n = 0
    if fmt == "ZIP":
        with zipfile.ZipFile(destino) as z:
            for c in [x for x in z.namelist() if x.lower().endswith(".csv")]:
                with z.open(c) as f:
                    n += bc.copy_csv(conn, tabela, f, delimiter=delim, header=True,
                                     encoding=pg_enc, colunas=cols)
    else:
        with open(destino, "rb") as f:
            n += bc.copy_csv(conn, tabela, f, delimiter=delim, header=True,
                             encoding=pg_enc, colunas=cols)
    bc.marcar(conn, "aneel", ref, tabela, n, tam)
    print(f"      ✓ {n:,} instalações".replace(",", "."), flush=True)


def carregar_local(caminho, recriar):
    """Carga a partir de um arquivo BAIXADO MANUALMENTE (ANEEL bloqueia bot com
    302-loop de WAF). Baixe o CSV/ZIP da camada no navegador e aponte aqui."""
    from pathlib import Path
    p = Path(caminho)
    conn = bc.conectar(); bc.garantir_controle(conn)
    ref = p.name
    tabela = "aneel_" + _norm(p.stem)
    fmt = "ZIP" if p.suffix.lower() == ".zip" else "CSV"
    if recriar:
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {tabela}")
            cur.execute("DELETE FROM fonte_arquivos WHERE fonte='aneel' AND referencia=%s", (ref,))
    print(f"⚡ ANEEL (arquivo local) | {ref}", flush=True)
    _processar(conn, ref, tabela, p, fmt)
    conn.close(); print("✅ ANEEL concluído.", flush=True)


def run(so, recriar, indices):
    conn = bc.conectar()
    bc.garantir_controle(conn)
    recs = recursos_uc()
    if so:
        recs = [r for r in recs if any(r["nome"].upper().startswith(s.upper()) for s in so)]
    print(f"⚡ ANEEL BDGD | {len(recs)} camada(s) de unidades consumidoras", flush=True)

    for i, r in enumerate(recs, 1):
        ref = r["nome"]
        tabela = "aneel_" + _norm(r["nome"].rsplit(".", 1)[0])
        if recriar:
            with conn, conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {tabela}")
                cur.execute("DELETE FROM fonte_arquivos WHERE fonte='aneel' AND referencia=%s", (ref,))
        if bc.ja_carregado(conn, "aneel", ref):
            print(f"  {i}/{len(recs)} {ref:16} — já carregado, pula"); continue
        destino = bc.TMP / r["nome"]
        try:
            print(f"  {i}/{len(recs)} {ref:16} baixando…", flush=True)
            bc.baixar(r["url"], destino)
            _processar(conn, ref, tabela, destino, r["fmt"])
        except Exception as e:
            msg = str(e)[:140]
            if "redirect" in msg.lower() or "302" in msg:
                msg += "  → ANEEL bloqueia bot; baixe no navegador e use --arquivo <caminho>"
            print(f"      ✗ erro: {msg}", flush=True)
        finally:
            bc.limpar_tmp(destino)
    conn.close()
    print("✅ ANEEL concluído.", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--so", default="", help="camadas, ex: UCAT,UCMT")
    p.add_argument("--arquivo", default="", help="carrega um CSV/ZIP baixado manualmente (ANEEL bloqueia bot)")
    p.add_argument("--recriar", action="store_true")
    p.add_argument("--indices", action="store_true")
    a = p.parse_args()
    if a.arquivo:
        carregar_local(a.arquivo, a.recriar)
    else:
        so = [s.strip() for s in a.so.split(",") if s.strip()]
        run(so, a.recriar, a.indices)
